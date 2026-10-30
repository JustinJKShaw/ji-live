"""Anchor-following monophonic drone for the Stage 6 MIDI rig.

The drone plays the current anchor pitch class at the engine's fundamental
octave (``anchor_ratio * fundamental``).  It does **not** use the analysis
voicing octave.  If the anchor is missing, the first current ratio is used as a
safe fallback and a warning is logged.

The note is started once and then held.  Every set update compares the new
anchor frequency with the held one:

* unchanged frequency -> send nothing, so the note never retriggers;
* same MIDI note, different bend -> send only a pitch bend;
* different MIDI note -> note-off the old note, then pitch-bend + note-on the
  new one.

This replaces the old most-consonant/re-select-every-N-mutations bass policy.
Stage 6 is MIDI-only: the sound comes from the Ableton-hosted MPE instrument.
"""

from __future__ import annotations

import math
import sys
import threading

try:  # Support both ``python -m control.drone`` and direct execution.
    from .mpe_output import MPEOutput, MPEPitch, frequency_to_pitch
except ImportError:  # pragma: no cover - exercised only by direct execution
    from mpe_output import MPEOutput, MPEPitch, frequency_to_pitch

_DEFAULT_VELOCITY = 90


class AnchorDrone:
    """Hold one MPE note at the current anchor pitch class.

    Args:
        output: An :class:`~control.mpe_output.MPEOutput` (or compatible fake).
        velocity: Note-on velocity (default 90).
    """

    def __init__(self, output: MPEOutput, velocity: int = _DEFAULT_VELOCITY) -> None:
        if isinstance(velocity, bool) or not isinstance(velocity, int):
            raise TypeError("velocity must be an integer")
        if not 1 <= velocity <= 127:
            raise ValueError("velocity must be between 1 and 127")
        self.output = output
        self.velocity = velocity

        self._lock = threading.Lock()
        self._target_frequency: float | None = None
        self._pitch: MPEPitch | None = None
        self._sounding = False
        self._fallback_warned = False

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    @property
    def is_sounding(self) -> bool:
        with self._lock:
            return self._sounding

    @property
    def current_frequency(self) -> float | None:
        with self._lock:
            return self._target_frequency

    @property
    def note(self) -> int | None:
        with self._lock:
            return None if self._pitch is None else self._pitch.note

    @property
    def bend(self) -> int | None:
        with self._lock:
            return None if self._pitch is None else self._pitch.bend

    # ------------------------------------------------------------------
    # Set updates
    # ------------------------------------------------------------------

    def on_set(
        self,
        ratios: list[float] | tuple[float, ...],
        anchor: float | None,
        fundamental: float,
    ) -> MPEPitch | None:
        """Follow one snapshot of the mutation engine.

        The snapshot is taken by the bridge; the drone never reads the engine.
        Returns the pitch that is now sounding, or ``None`` if the snapshot was
        empty.
        """

        with self._lock:
            ratio, frequency = self._select_target(
                ratios, anchor, fundamental
            )
            if ratio is None or frequency is None:
                return None

            if self._target_frequency is not None and math.isclose(
                self._target_frequency,
                frequency,
                rel_tol=1e-9,
                abs_tol=1e-9,
            ):
                # Same anchor frequency: the note must stay constant.
                return self._pitch

            pitch = frequency_to_pitch(
                frequency, self.output.bend_range_semitones
            )

            if self._sounding and self._pitch is not None:
                if pitch.note == self._pitch.note:
                    # Same MIDI note, new bending: no retrigger.
                    self.output.pitch_bend(pitch.bend)
                    action = "retune"
                else:
                    self.output.note_off(self._pitch.note)
                    self.output.pitch_bend(pitch.bend)
                    self.output.note_on(pitch.note, self.velocity)
                    action = "retrigger"
            else:
                self.output.pitch_bend(pitch.bend)
                self.output.note_on(pitch.note, self.velocity)
                action = "start"

            self._target_frequency = frequency
            self._pitch = pitch
            self._sounding = True

        print(
            f"[DRONE] anchor={_fmt_ratio(ratio)} freq={frequency:.2f} "
            f"note={pitch.note} bend={pitch.bend} ({action})"
        )
        return pitch

    # Alias used by callers that think in set-update terms.
    update = on_set

    def start(
        self,
        ratios: list[float] | tuple[float, ...],
        anchor: float | None,
        fundamental: float,
    ) -> MPEPitch | None:
        """Alias for :meth:`on_set`; starts the note if it is not sounding."""

        return self.on_set(ratios, anchor, fundamental)

    # ------------------------------------------------------------------
    # Stop / cleanup
    # ------------------------------------------------------------------

    def stop(self) -> None:
        """Note-off the held note and send All Notes Off.  Idempotent."""

        with self._lock:
            pitch = self._pitch
            was_sounding = self._sounding
            self._sounding = False
            self._pitch = None
            self._target_frequency = None

        if was_sounding and pitch is not None:
            self.output.note_off(pitch.note)
        self.output.all_notes_off()
        if was_sounding:
            print("[DRONE] stopped")

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _select_target(
        self,
        ratios: list[float] | tuple[float, ...],
        anchor: float | None,
        fundamental: float,
    ) -> tuple[float | None, float | None]:
        try:
            ratio_list = [_positive_float(ratio, "ratio") for ratio in ratios]
        except (TypeError, ValueError):
            raise TypeError("ratios must be an iterable of real numbers") from None
        if not ratio_list:
            _warn("empty ratio set; drone has no note to play")
            return None, None

        fundamental = _positive_float(fundamental, "fundamental")

        selected: float | None = None
        if anchor is not None:
            try:
                anchor_value = float(anchor)
            except (TypeError, ValueError):
                anchor_value = None
            if anchor_value is not None:
                for ratio in ratio_list:
                    if math.isclose(
                        ratio, anchor_value, rel_tol=0.0, abs_tol=1e-12
                    ):
                        selected = ratio
                        break

        if selected is None:
            selected = ratio_list[0]
            if not self._fallback_warned:
                if anchor is None:
                    _warn(
                        "anchor_ratio is None; using current_ratios[0]="
                        f"{selected:.6f} as the drone fallback"
                    )
                else:
                    _warn(
                        f"anchor_ratio={anchor!r} is not in the current set; "
                        f"using current_ratios[0]={selected:.6f}"
                    )
                self._fallback_warned = True
        else:
            self._fallback_warned = False

        return selected, selected * fundamental


def _positive_float(value: float, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise TypeError(f"{label} must be a real number") from None
    if not math.isfinite(number) or number <= 0.0:
        raise ValueError(f"{label} must be finite and strictly positive")
    return number


def _fmt_ratio(value: float | None) -> str:
    return "none" if value is None else f"{value:.6f}"


def _warn(message: str) -> None:
    print(f"[DRONE] warning: {message}", file=sys.stderr)

"""Anchor-following monophonic drone: Stage 6 ``hold`` plus Stage 8.3d modes.

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

Stage 8.3d adds two pad-like modes on top of that ``hold`` behavior without
touching it:

``hold`` (default)
    :class:`AnchorDrone` exactly as in Stage 6.  No thread, never
    transport-gated, same message stream and log-line shape.
``pulse``
    a repeating pattern of ``pulse_notes`` notes on the anchor pitch class,
    each at a seeded octave offset within ``pulse_octave_span``; monophonic,
    transport-gated like the drums.
``bend``
    hold the anchor for ``hold_beats``, then slide the pitch by
    ``slide_semitones`` (down or up) over ``slide_seconds`` as *one continuous
    pitch bend* -- the note is never retriggered to slide -- then note-off and
    rest until the derived cycle boundary.

The ``bend`` cycle is derived, never configured:
``cycle_beats = ceil(hold_beats + slide_seconds * bpm / 60)`` quarter beats, so
the gesture stays on the grid and always leaves a rest before the next note.
The 48-semitone MPE bend range is fixed, so a +/-12 st slide uses a quarter of
the range.

All three modes stay monophonic on one member channel: a true multi-note pad
would need MPE voice allocation across member channels, which is out of scope.
"""

from __future__ import annotations

import json
import math
import random
import sys
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable

try:  # Support both ``python -m control.drone`` and direct execution.
    from . import rhythm
    from .mpe_output import (
        BEND_MAX_CENTERED,
        BEND_MIN_CENTERED,
        MPEOutput,
        MPEPitch,
        frequency_to_pitch,
    )
except ImportError:  # pragma: no cover - exercised only by direct execution
    import rhythm  # type: ignore[no-redef]
    from mpe_output import (  # type: ignore[no-redef]
        BEND_MAX_CENTERED,
        BEND_MIN_CENTERED,
        MPEOutput,
        MPEPitch,
        frequency_to_pitch,
    )

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


# ---------------------------------------------------------------------------
# Stage 8.3d: modes, settings, and the mode schedulers
# ---------------------------------------------------------------------------

DEFAULT_CONFIG_PATH = (
    Path(__file__).resolve().parent.parent / "config" / "drone_config.json"
)

DRONE_MODES: tuple[str, ...] = ("hold", "pulse", "bend", "sequence")
DEFAULT_MODE = "hold"
DEFAULT_VELOCITY_MODE = 90

PULSE_NOTES_MIN = 2
PULSE_NOTES_MAX = 8
DEFAULT_PULSE_NOTES = 4
PULSE_GRIDS: tuple[str, ...] = ("1/4", "1/8", "1/16")
PULSE_GRID_QUARTERS: dict[str, float] = {"1/4": 1.0, "1/8": 0.5, "1/16": 0.25}
DEFAULT_PULSE_GRID = "1/4"
PULSE_GATE_MIN = 0.05
PULSE_GATE_MAX = 2.0
DEFAULT_PULSE_GATE = 0.5
PULSE_SPAN_MIN = 0
PULSE_SPAN_MAX = 2
DEFAULT_PULSE_SPAN = 1

#: ``sequence``: a repeating figure that walks up the current set from the
#: anchor and back down to it (a pad-like harmonic movement through the classes).
SEQ_NOTES_MIN = 2
SEQ_NOTES_MAX = 8
DEFAULT_SEQ_NOTES = 5
#: Note length in **bars**: the figure holds each class for this long, so the
#: harmonic movement unfolds over bars rather than on a fast grid.
SEQ_BARS: tuple[str, ...] = ("1/4", "1/2", "1", "2", "4")
SEQ_BARS_VALUE: dict[str, float] = {
    "1/4": 0.25,
    "1/2": 0.5,
    "1": 1.0,
    "2": 2.0,
    "4": 4.0,
}
DEFAULT_SEQ_BARS = "1"
#: Gate as a fraction of the note (1.0 = legato, a tiny gap before the next
#: note-on; 0.5 = half the note sounds, then a rest).
SEQ_GATE_MIN = 0.1
SEQ_GATE_MAX = 1.0
DEFAULT_SEQ_GATE = 1.0
SEQ_OCTAVE_LIMIT = 2

SLIDE_DIRECTIONS: tuple[str, ...] = ("down", "up")
DEFAULT_SLIDE_DIRECTION = "down"
SLIDE_SEMITONES_MIN = 1
SLIDE_SEMITONES_MAX = 12
DEFAULT_SLIDE_SEMITONES = 2
SLIDE_SECONDS_MIN = 0.05
SLIDE_SECONDS_MAX = 4.0
DEFAULT_SLIDE_SECONDS = 0.4
HOLD_BEATS_MIN = 0.25
HOLD_BEATS_MAX = 16.0
DEFAULT_HOLD_BEATS = 2.5

DRONE_SEED_MAX = 2**31 - 1
DEFAULT_DRONE_SEED = 1

#: Bend updates per second during a slide (30-60 is plenty for a portamento).
SLIDE_UPDATES_PER_SECOND = 50.0
#: Slice length for a wait that must notice a set update or a transport stop.
_WAIT_SLICE_SECONDS = 0.05


def _finite(value: object, label: str) -> float:
    try:
        numeric = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise TypeError(f"{label} must be a real number") from None
    if not math.isfinite(numeric):
        raise ValueError(f"{label} must be finite")
    return numeric


def _as_int(value: object, label: str) -> int:
    if isinstance(value, bool):
        raise TypeError(f"{label} must be an integer")
    return int(round(_finite(value, label)))


#: Short names accepted by :meth:`DroneSettings.with_change` (GUI/OSC use).
_SETTING_ALIASES: dict[str, str] = {
    "span": "pulse_octave_span",
    "pulse_span": "pulse_octave_span",
    "dir": "slide_direction",
    "semis": "slide_semitones",
    "slide": "slide_seconds",
    "hold": "hold_beats",
}


def validate_mode(value: object) -> str:
    """Return one of ``hold``, ``pulse``, ``bend``."""

    text = str(value).strip().lower()
    if text not in DRONE_MODES:
        raise ValueError("mode must be one of " + ", ".join(DRONE_MODES))
    return text


def validate_velocity(value: object, label: str = "velocity") -> int:
    """Return a MIDI velocity clamped to ``1..127``."""

    return max(1, min(127, _as_int(value, label)))


def validate_pulse_notes(value: object) -> int:
    """Return the pulse pattern length, 2..8."""

    number = _as_int(value, "pulse_notes")
    if not PULSE_NOTES_MIN <= number <= PULSE_NOTES_MAX:
        raise ValueError(
            f"pulse_notes must be {PULSE_NOTES_MIN}..{PULSE_NOTES_MAX}"
        )
    return number


def validate_pulse_grid(value: object) -> str:
    """Return one of ``1/4``, ``1/8``, ``1/16``."""

    text = str(value).strip().upper().replace(" ", "")
    if text not in PULSE_GRID_QUARTERS:
        raise ValueError("pulse_grid must be one of " + ", ".join(PULSE_GRIDS))
    return text


def validate_pulse_gate(value: object) -> float:
    """Return the pulse gate in beats, clamped to ``0.05..2.0``."""

    return max(
        PULSE_GATE_MIN, min(PULSE_GATE_MAX, _finite(value, "pulse_gate"))
    )


def validate_pulse_span(value: object) -> int:
    """Return the pulse octave span, 0..2."""

    number = _as_int(value, "pulse_octave_span")
    if not PULSE_SPAN_MIN <= number <= PULSE_SPAN_MAX:
        raise ValueError(
            f"pulse_octave_span must be {PULSE_SPAN_MIN}..{PULSE_SPAN_MAX}"
        )
    return number


def validate_seq_notes(value: object) -> int:
    """Return the sequence length, 2..8."""

    number = _as_int(value, "seq_notes")
    if not SEQ_NOTES_MIN <= number <= SEQ_NOTES_MAX:
        raise ValueError(
            f"seq_notes must be {SEQ_NOTES_MIN}..{SEQ_NOTES_MAX}"
        )
    return number


def validate_seq_bars(value: object) -> str:
    """Return one of the note lengths in bars (``1/4``, ``1/2``, ``1``, ``2``, ``4``)."""

    text = str(value).strip().replace(" ", "").replace("bar", "").replace("s", "")
    if text in SEQ_BARS_VALUE:
        return text
    try:
        numeric = float(text)
    except (TypeError, ValueError):
        raise ValueError(
            "seq_bars must be one of " + ", ".join(SEQ_BARS)
        ) from None
    for name, bars in SEQ_BARS_VALUE.items():
        if math.isclose(numeric, bars, rel_tol=0.0, abs_tol=1e-9):
            return name
    raise ValueError("seq_bars must be one of " + ", ".join(SEQ_BARS))


def seq_bars_value(value: object) -> float:
    """Bars per note for a validated ``seq_bars`` label."""

    return SEQ_BARS_VALUE[validate_seq_bars(value)]


def validate_seq_gate(value: object) -> float:
    """Return the gate as a fraction of the note, clamped to ``0.1..1.0``."""

    return max(
        SEQ_GATE_MIN, min(SEQ_GATE_MAX, _finite(value, "seq_gate"))
    )


def sequence_offsets(notes: object) -> tuple[int, ...]:
    """Signed set-step contour for one sequence cycle.

    ``0, +1, ..., +peak, ..., +1, 0`` truncated to ``notes`` entries, so the
    figure leaves the anchor and comes back to it (an odd length ends on the
    anchor; an even length returns to it at the loop point).  Each step is one
    class up or down the *current sorted set*, wrapping across octaves.
    """

    count = validate_seq_notes(notes)
    peak = count // 2
    up = list(range(peak + 1))
    down = list(range(peak - 1, -1, -1))
    return tuple((up + down)[:count])


def validate_slide_direction(value: object) -> str:
    """Return ``down`` or ``up``."""

    text = str(value).strip().lower()
    if text not in SLIDE_DIRECTIONS:
        raise ValueError(
            "slide_direction must be one of " + ", ".join(SLIDE_DIRECTIONS)
        )
    return text


def validate_slide_semitones(value: object) -> int:
    """Return the slide size in semitones, 1..12."""

    number = _as_int(value, "slide_semitones")
    if not SLIDE_SEMITONES_MIN <= number <= SLIDE_SEMITONES_MAX:
        raise ValueError(
            f"slide_semitones must be {SLIDE_SEMITONES_MIN}.."
            f"{SLIDE_SEMITONES_MAX}"
        )
    return number


def validate_slide_seconds(value: object) -> float:
    """Return the slide duration, clamped to ``0.05..4.0`` seconds."""

    return max(
        SLIDE_SECONDS_MIN,
        min(SLIDE_SECONDS_MAX, _finite(value, "slide_seconds")),
    )


def validate_hold_beats(value: object) -> float:
    """Return the hold length, clamped to ``0.25..16`` quarter beats."""

    return max(
        HOLD_BEATS_MIN, min(HOLD_BEATS_MAX, _finite(value, "hold_beats"))
    )


def validate_seed(value: object) -> int:
    """Return an integer seed clamped to ``0..2**31-1``."""

    return max(0, min(DRONE_SEED_MAX, _as_int(value, "seed")))


def cycle_beats(hold: object, slide_seconds: object, bpm: object) -> float:
    """Derived ``bend`` cycle length in quarter beats.

    ``hold_beats + slide_seconds * bpm / 60`` rounded **up** to the next whole
    quarter beat, so the gesture stays on the grid and always leaves a rest
    before the next note.  At 120 BPM the shipped values give
    ``2.5 + 0.8 = 3.3 -> 4`` beats.
    """

    hold_value = validate_hold_beats(hold)
    seconds = validate_slide_seconds(slide_seconds)
    tempo = rhythm.clamp_bpm(bpm)
    total = hold_value + seconds * tempo / 60.0
    return float(max(1.0, math.ceil(total - 1e-9)))


@dataclass(frozen=True, slots=True)
class DroneSettings:
    """Validated, immutable Stage 8.3d drone-mode controls.

    ``hold`` is the shipped default: every field except ``mode`` is inert in
    that mode, so an old rig keeps the Stage 6 drone exactly.
    """

    mode: str = DEFAULT_MODE
    velocity: int = DEFAULT_VELOCITY_MODE
    pulse_notes: int = DEFAULT_PULSE_NOTES
    pulse_grid: str = DEFAULT_PULSE_GRID
    pulse_gate: float = DEFAULT_PULSE_GATE
    pulse_octave_span: int = DEFAULT_PULSE_SPAN
    slide_direction: str = DEFAULT_SLIDE_DIRECTION
    slide_semitones: int = DEFAULT_SLIDE_SEMITONES
    slide_seconds: float = DEFAULT_SLIDE_SECONDS
    hold_beats: float = DEFAULT_HOLD_BEATS
    seed: int = DEFAULT_DRONE_SEED
    seq_notes: int = DEFAULT_SEQ_NOTES
    seq_bars: str = DEFAULT_SEQ_BARS
    seq_gate: float = DEFAULT_SEQ_GATE

    @classmethod
    def create(cls, **values: Any) -> "DroneSettings":
        """Build settings with every value validated/normalised."""

        defaults = cls()
        return cls(
            mode=validate_mode(values.get("mode", defaults.mode)),
            velocity=validate_velocity(values.get("velocity", defaults.velocity)),
            pulse_notes=validate_pulse_notes(
                values.get("pulse_notes", defaults.pulse_notes)
            ),
            pulse_grid=validate_pulse_grid(
                values.get("pulse_grid", defaults.pulse_grid)
            ),
            pulse_gate=validate_pulse_gate(
                values.get("pulse_gate", defaults.pulse_gate)
            ),
            pulse_octave_span=validate_pulse_span(
                values.get("pulse_octave_span", defaults.pulse_octave_span)
            ),
            slide_direction=validate_slide_direction(
                values.get("slide_direction", defaults.slide_direction)
            ),
            slide_semitones=validate_slide_semitones(
                values.get("slide_semitones", defaults.slide_semitones)
            ),
            slide_seconds=validate_slide_seconds(
                values.get("slide_seconds", defaults.slide_seconds)
            ),
            hold_beats=validate_hold_beats(
                values.get("hold_beats", defaults.hold_beats)
            ),
            seed=validate_seed(values.get("seed", defaults.seed)),
            seq_notes=validate_seq_notes(
                values.get("seq_notes", defaults.seq_notes)
            ),
            seq_bars=validate_seq_bars(
                values.get("seq_bars", defaults.seq_bars)
            ),
            seq_gate=validate_seq_gate(
                values.get("seq_gate", defaults.seq_gate)
            ),
        )

    def with_change(self, name: object, value: object) -> "DroneSettings":
        """Return settings with one validated change applied."""

        key = str(name).strip().lower()
        key = _SETTING_ALIASES.get(key, key)
        if key == "mode":
            return replace(self, mode=validate_mode(value))
        if key == "velocity":
            return replace(self, velocity=validate_velocity(value))
        if key == "pulse_notes":
            return replace(self, pulse_notes=validate_pulse_notes(value))
        if key == "pulse_grid":
            return replace(self, pulse_grid=validate_pulse_grid(value))
        if key == "pulse_gate":
            return replace(self, pulse_gate=validate_pulse_gate(value))
        if key in ("pulse_span", "pulse_octave_span"):
            return replace(self, pulse_octave_span=validate_pulse_span(value))
        if key == "slide_direction":
            return replace(self, slide_direction=validate_slide_direction(value))
        if key == "slide_semitones":
            return replace(self, slide_semitones=validate_slide_semitones(value))
        if key == "slide_seconds":
            return replace(self, slide_seconds=validate_slide_seconds(value))
        if key == "hold_beats":
            return replace(self, hold_beats=validate_hold_beats(value))
        if key == "seed":
            return replace(self, seed=validate_seed(value))
        if key == "seq_notes":
            return replace(self, seq_notes=validate_seq_notes(value))
        if key in ("seq_bars", "seq_length"):
            return replace(self, seq_bars=validate_seq_bars(value))
        if key == "seq_gate":
            return replace(self, seq_gate=validate_seq_gate(value))
        raise ValueError(
            f"unknown drone setting {name!r}; expected mode, velocity, "
            "pulse_notes, pulse_grid, pulse_gate, pulse_octave_span, "
            "slide_direction, slide_semitones, slide_seconds, hold_beats, "
            "seq_notes, seq_bars, seq_gate, seed"
        )

    def cycle_beats(self, bpm: object = rhythm.BPM_DEFAULT) -> float:
        """Derived ``bend`` cycle length in quarter beats (read-only)."""

        return cycle_beats(self.hold_beats, self.slide_seconds, bpm)

    def state_args(
        self,
        port_ok: bool,
        playing: bool = False,
        bpm: object = rhythm.BPM_DEFAULT,
    ) -> list[Any]:
        """``/ji/drone/state`` payload (fourteen values, documented order)."""

        return [
            int(bool(port_ok)),
            self.mode,
            int(self.velocity),
            int(self.pulse_notes),
            self.pulse_grid,
            float(self.pulse_gate),
            int(self.pulse_octave_span),
            self.slide_direction,
            int(self.slide_semitones),
            float(self.slide_seconds),
            float(self.hold_beats),
            float(self.cycle_beats(bpm)),
            int(self.seed),
            int(bool(playing)),
            int(self.seq_notes),
            self.seq_bars,
            float(self.seq_gate),
        ]

    def describe_mode(self, bpm: object = rhythm.BPM_DEFAULT) -> str:
        """One ``[DRONE] mode=...`` line (no per-note traffic)."""

        if self.mode == "pulse":
            return (
                f"mode=pulse notes={self.pulse_notes} "
                f"grid={self.pulse_grid} span={self.pulse_octave_span} "
                f"seed={self.seed}"
            )
        if self.mode == "sequence":
            return (
                f"mode=sequence notes={self.seq_notes} "
                f"bars={self.seq_bars} gate={self.seq_gate:.2f}"
            )
        if self.mode == "bend":
            return (
                f"mode=bend {self.slide_direction} "
                f"{self.slide_semitones:.2f} st in "
                f"{self.slide_seconds:.3f} s every "
                f"{self.cycle_beats(bpm):.3f} beats"
            )
        return "mode=hold"


@dataclass(frozen=True, slots=True)
class DroneConfig:
    """Validated ``config/drone_config.json``.

    The MIDI bus itself stays in ``config/mpe_config.json`` (``drone_port``),
    the Stage 6 contract; this file only owns the mode and its parameters.
    """

    port: str = "IAC Driver Bus 1"
    settings: DroneSettings = DroneSettings()

    def __post_init__(self) -> None:
        if not isinstance(self.port, str) or not self.port.strip():
            raise ValueError("port must be a non-empty string")
        if not isinstance(self.settings, DroneSettings):
            raise TypeError("settings must be a DroneSettings")

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "DroneConfig":
        if not isinstance(data, Mapping):
            raise TypeError("drone config must be a mapping")
        values = {
            key: data[key]
            for key in (
                "mode",
                "velocity",
                "pulse_notes",
                "pulse_grid",
                "pulse_gate",
                "pulse_octave_span",
                "slide_direction",
                "slide_semitones",
                "slide_seconds",
                "hold_beats",
                "seed",
                "seq_notes",
                "seq_bars",
                "seq_gate",
            )
            if key in data
        }
        return cls(
            port=str(data.get("port", "IAC Driver Bus 1")),
            settings=DroneSettings.create(**values),
        )

    @classmethod
    def from_json(cls, path: str | Path) -> "DroneConfig":
        return load_config(path)


def load_config(path: str | Path = DEFAULT_CONFIG_PATH) -> DroneConfig:
    """Load and validate the drone config (same path fallback as the others)."""

    candidate = Path(path)
    if not candidate.is_absolute() and not candidate.exists():
        project_candidate = (
            Path(__file__).resolve().parent.parent / candidate
        )
        if project_candidate.exists():
            candidate = project_candidate

    try:
        text = candidate.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise FileNotFoundError(
            f"drone config not found: {candidate}"
        ) from None

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in {candidate}: {exc}") from exc
    return DroneConfig.from_mapping(data)


def select_target(
    ratios: Sequence[float],
    anchor: float | None,
    fundamental: float,
) -> tuple[float, float] | None:
    """``(ratio, frequency)`` for the current anchor, or ``None`` if empty.

    Same rule as :class:`AnchorDrone`: the exact anchor class when it is in the
    set, else the first (lowest) class.  Used by ``pulse``/``bend``; ``hold``
    keeps its own copy so its message stream cannot change.
    """

    pool = [
        float(ratio)
        for ratio in ratios
        if math.isfinite(float(ratio)) and float(ratio) > 0.0
    ]
    if not pool:
        return None
    fund = _positive_float(fundamental, "fundamental")
    selected = None
    if anchor is not None:
        try:
            value = float(anchor)
        except (TypeError, ValueError):
            value = None
        if value is not None:
            for ratio in pool:
                if math.isclose(ratio, value, rel_tol=0.0, abs_tol=1e-12):
                    selected = ratio
                    break
    if selected is None:
        selected = pool[0]
    return selected, selected * fund


class _ModeScheduler:
    """Daemon-thread base for the transport-gated ``pulse``/``bend`` modes."""

    name = "mode"

    def __init__(
        self, rig: "DroneRig", *, now: Callable[[], float] = time.monotonic
    ) -> None:
        self.rig = rig
        self._now = now
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._held: int | None = None

    @property
    def is_running(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            if not getattr(self.rig.output, "is_open", False):
                raise RuntimeError("drone output is not open")
            self._stop.clear()
            thread = threading.Thread(
                target=self._run, name=f"ji-drone-{self.name}", daemon=True
            )
            self._thread = thread
        thread.start()

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        with self._lock:
            if self._thread is thread:
                self._thread = None
        self.silence()

    def silence(self) -> None:
        """note-off the held note and send CC 123.  Never raises."""

        with self._lock:
            held = self._held
            self._held = None
        self.note_off(held)
        try:
            self.rig.output.all_notes_off()
        except Exception:
            pass

    # -- message helpers --------------------------------------------------

    def note_on(self, note: int, velocity: int) -> None:
        with self._lock:
            self._held = int(note)
        self.rig.output.note_on(int(note), int(velocity))

    def note_off(self, note: int | None) -> None:
        if note is None:
            return
        try:
            self.rig.output.note_off(int(note))
        except Exception:
            pass

    def _run(self) -> None:  # pragma: no cover - exercised with a fake output
        raise NotImplementedError


class PulseScheduler(_ModeScheduler):
    """Repeat ``pulse_notes`` notes on the anchor pitch class, transport-gated."""

    name = "pulse"

    def _run(self) -> None:  # pragma: no cover - exercised with a fake output
        deadline = self._now()
        step = 0
        try:
            while not self._stop.is_set():
                settings = self.rig.drone_settings
                step_quarters = PULSE_GRID_QUARTERS[settings.pulse_grid]
                step_seconds = (60.0 / self.rig.bpm) * step_quarters
                now = self._now()
                if deadline > now:
                    if self._stop.wait(deadline - now):
                        break
                elif self._stop.is_set():
                    break

                target = self.rig.anchor_target()
                if target is not None:
                    offset = self.rig.pulse_octave(step)
                    frequency = target[1] * (2.0 ** offset)
                    pitch = frequency_to_pitch(
                        frequency, self.rig.bend_range
                    )
                    with self._lock:
                        held = self._held
                        self._held = None
                    self.note_off(held)
                    try:
                        self.rig.output.pitch_bend(pitch.bend)
                        self.note_on(pitch.note, settings.velocity)
                    except Exception as exc:
                        print(
                            f"[DRONE] pulse output error: {exc}",
                            file=sys.stderr,
                        )
                        break
                    gate_seconds = (
                        min(settings.pulse_gate, step_quarters)
                        * (60.0 / self.rig.bpm)
                    )
                    if self._stop.wait(max(0.001, gate_seconds)):
                        break
                    with self._lock:
                        held = self._held
                        self._held = None
                    self.note_off(held)

                step += 1
                deadline += step_seconds
                now = self._now()
                if deadline < now:
                    deadline = now
                wait = deadline - now
                if wait > 0.0 and self._stop.wait(wait):
                    break
        except Exception as exc:
            print(f"[DRONE] pulse scheduler error: {exc}", file=sys.stderr)
        finally:
            with self._lock:
                held = self._held
                self._held = None
            self.note_off(held)


class BendScheduler(_ModeScheduler):
    """Hold, slide the pitch with a continuous bend, rest, repeat.

    The cycle length is derived (:meth:`DroneSettings.cycle_beats`).  A set
    mutation during the hold retunes the base pitch exactly as ``hold`` does
    (bend-only when the MIDI note is unchanged, retrigger otherwise).  A
    mutation during the slide is **deferred**: the new base is picked up at the
    next cycle, because re-bending mid-gesture would jump the pitch.
    """

    name = "bend"

    def _run(self) -> None:  # pragma: no cover - exercised with a fake output
        cycle_start = self._now()
        try:
            while not self._stop.is_set():
                settings = self.rig.drone_settings
                beat_seconds = 60.0 / self.rig.bpm
                span_beats = settings.cycle_beats(self.rig.bpm)
                target = self.rig.anchor_target()
                if target is None:
                    cycle_start = self._now()
                    if self._stop.wait(_WAIT_SLICE_SECONDS):
                        break
                    continue

                version = self.rig.version
                pitch = frequency_to_pitch(target[1], self.rig.bend_range)
                with self._lock:
                    held = self._held
                    self._held = None
                self.note_off(held)
                try:
                    self.rig.output.pitch_bend(pitch.bend)
                    self.note_on(pitch.note, settings.velocity)
                except Exception as exc:
                    print(f"[DRONE] bend output error: {exc}", file=sys.stderr)
                    break
                self.rig.log_slide(settings)

                hold_deadline = self._now() + settings.hold_beats * beat_seconds
                stopped = False
                while True:
                    now = self._now()
                    if now >= hold_deadline:
                        break
                    if self._stop.wait(
                        min(_WAIT_SLICE_SECONDS, hold_deadline - now)
                    ):
                        stopped = True
                        break
                    if self.rig.version == version:
                        continue
                    version = self.rig.version
                    fresh_target = self.rig.anchor_target()
                    if fresh_target is None:
                        continue
                    fresh = frequency_to_pitch(
                        fresh_target[1], self.rig.bend_range
                    )
                    if fresh.note == pitch.note:
                        self.rig.output.pitch_bend(fresh.bend)
                    else:
                        self.note_off(pitch.note)
                        self.rig.output.pitch_bend(fresh.bend)
                        self.note_on(fresh.note, settings.velocity)
                    pitch = fresh
                if stopped:
                    break

                base_bend = pitch.bend
                semitones = float(settings.slide_semitones) * (
                    -1.0 if settings.slide_direction == "down" else 1.0
                )
                delta = semitones / self.rig.bend_range * 8192.0
                final_bend = int(
                    round(
                        max(
                            BEND_MIN_CENTERED,
                            min(BEND_MAX_CENTERED, base_bend + delta),
                        )
                    )
                )
                updates = max(
                    2,
                    int(round(settings.slide_seconds * SLIDE_UPDATES_PER_SECOND)),
                )
                slide_start = self._now()
                for index in range(1, updates + 1):
                    at = slide_start + settings.slide_seconds * index / updates
                    now = self._now()
                    if at > now and self._stop.wait(at - now):
                        stopped = True
                        break
                    bend = int(
                        round(base_bend + (final_bend - base_bend) * index / updates)
                    )
                    bend = max(BEND_MIN_CENTERED, min(BEND_MAX_CENTERED, bend))
                    self.rig.output.pitch_bend(bend)
                if stopped:
                    break

                with self._lock:
                    held = self._held
                    self._held = None
                self.note_off(held)

                cycle_end = cycle_start + span_beats * beat_seconds
                now = self._now()
                if cycle_end > now:
                    if self._stop.wait(cycle_end - now):
                        break
                    cycle_start = cycle_end
                else:
                    cycle_start = now
        except Exception as exc:
            print(f"[DRONE] bend scheduler error: {exc}", file=sys.stderr)
        finally:
            with self._lock:
                held = self._held
                self._held = None
            self.note_off(held)


class SequenceScheduler(_ModeScheduler):
    """Walk the current set from the anchor and back, transport-gated.

    One cycle is :func:`sequence_offsets` ``(seq_notes)``: 0, +1, +2, ... peak
    ... +1, 0 in *set steps*, so the figure leaves the anchor, climbs through the
    classes (octave-wrapping past the top of the set) and returns.  Every note is
    resolved against the current snapshot, so a mutation retunes the figure
    without changing its shape.
    """

    name = "sequence"

    def _run(self) -> None:  # pragma: no cover - exercised with a fake output
        deadline = self._now()
        index = 0
        try:
            while not self._stop.is_set():
                settings = self.rig.drone_settings
                step_seconds = self.rig.sequence_note_seconds(settings)
                # A tiny gap keeps the note-off just before the next note-on.
                gap = min(0.01, step_seconds * 0.02)
                gate_seconds = max(
                    0.001,
                    min(step_seconds - gap, settings.seq_gate * step_seconds),
                )
                now = self._now()
                if deadline > now:
                    if self._stop.wait(deadline - now):
                        break
                elif self._stop.is_set():
                    break

                offsets = sequence_offsets(settings.seq_notes)
                frequency = self.rig.sequence_frequency(
                    offsets[index % len(offsets)]
                )
                if frequency is not None:
                    pitch = frequency_to_pitch(frequency, self.rig.bend_range)
                    with self._lock:
                        held = self._held
                        self._held = None
                    self.note_off(held)
                    try:
                        self.rig.output.pitch_bend(pitch.bend)
                        self.note_on(pitch.note, settings.velocity)
                    except Exception as exc:
                        print(
                            f"[DRONE] sequence output error: {exc}",
                            file=sys.stderr,
                        )
                        break
                    if self._stop.wait(gate_seconds):
                        break
                    with self._lock:
                        held = self._held
                        self._held = None
                    self.note_off(held)

                index += 1
                deadline += step_seconds
                now = self._now()
                if deadline < now:
                    deadline = now
                wait = deadline - now
                if wait > 0.0 and self._stop.wait(wait):
                    break
        except Exception as exc:
            print(f"[DRONE] sequence scheduler error: {exc}", file=sys.stderr)
        finally:
            with self._lock:
                held = self._held
                self._held = None
            self.note_off(held)


class DroneRig:
    """Own the drone output, the Stage 6 ``hold`` drone, and the mode schedulers.

    ``hold`` is the default and behaves exactly like Stage 6: no thread, never
    transport-gated, the same ``[DRONE] anchor=...`` line shape.  ``pulse`` and
    ``bend`` start on a transport start and stop with note-off + CC 123.
    """

    def __init__(
        self,
        output: MPEOutput,
        config: DroneConfig | Mapping[str, Any] | str | Path | None = None,
        *,
        velocity: object | None = None,
        bpm: object = rhythm.BPM_DEFAULT,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        from_file = config is not None
        if config is None:
            try:
                cfg = load_config(DEFAULT_CONFIG_PATH)
                from_file = True
            except (OSError, TypeError, ValueError):
                cfg = DroneConfig()
        elif isinstance(config, DroneConfig):
            cfg = config
        elif isinstance(config, (str, Path)):
            cfg = load_config(config)
        elif isinstance(config, Mapping):
            cfg = DroneConfig.from_mapping(config)
        else:
            raise TypeError(
                "config must be a DroneConfig, mapping, path, or None"
            )

        settings = cfg.settings
        if velocity is not None and not from_file:
            # An old rig without drone_config.json keeps mpe_config's velocity.
            settings = replace(
                settings, velocity=validate_velocity(velocity, "drone velocity")
            )
        self.config = replace(cfg, settings=settings)
        self.output = output
        self.bend_range = float(getattr(output, "bend_range_semitones", 48.0))
        self._lock = threading.RLock()
        self._settings = self.config.settings
        self._bpm = rhythm.clamp_bpm(bpm)
        self._meter_num = rhythm.METER_NUM_DEFAULT
        self._meter_den = rhythm.METER_DEN_DEFAULT
        self._snapshot: tuple[list[float], float | None, float] | None = None
        self._version = 0
        self._transport_running = False
        self._fallback_warned = False
        self._pulse_key: tuple[int, int, int] | None = None
        self._pulse_pattern: tuple[int, ...] = (0,)
        self.anchor = AnchorDrone(output, velocity=self._settings.velocity)
        self.pulse_scheduler = PulseScheduler(self, now=now)
        self.bend_scheduler = BendScheduler(self, now=now)
        self.sequence_scheduler = SequenceScheduler(self, now=now)

    # -- state ----------------------------------------------------------

    @property
    def settings(self) -> DroneSettings:
        with self._lock:
            return self._settings

    drone_settings = settings

    @property
    def mode(self) -> str:
        with self._lock:
            return self._settings.mode

    @property
    def bpm(self) -> float:
        with self._lock:
            return self._bpm

    @property
    def meter(self) -> str:
        with self._lock:
            return f"{self._meter_num}/{self._meter_den}"

    @property
    def bar_quarters(self) -> float:
        """Quarter notes in one bar at the current meter."""

        with self._lock:
            return rhythm.bar_quarters(self._meter_num, self._meter_den)

    def set_meter(self, meter_num: object, meter_den: object) -> str:
        """Record the meter (the ``sequence`` note length is in bars)."""

        numerator = rhythm.clamp_meter_numerator(meter_num)
        denominator = rhythm.validate_meter_denominator(meter_den)
        with self._lock:
            self._meter_num = numerator
            self._meter_den = denominator
            return f"{numerator}/{denominator}"

    def sequence_note_seconds(self, settings: "DroneSettings" | None = None) -> float:
        """One ``sequence`` note: ``seq_bars`` bars at the current tempo."""

        if settings is None:
            settings = self.drone_settings
        beat_seconds = 60.0 / self.bpm
        return (
            SEQ_BARS_VALUE[validate_seq_bars(settings.seq_bars)]
            * self.bar_quarters
            * beat_seconds
        )

    @property
    def version(self) -> int:
        with self._lock:
            return self._version

    @property
    def port(self) -> str:
        return self.config.port

    @property
    def velocity(self) -> int:
        with self._lock:
            return self._settings.velocity

    @property
    def cycle_beats(self) -> float:
        with self._lock:
            return self._settings.cycle_beats(self._bpm)

    @property
    def snapshot_version(self) -> int:
        with self._lock:
            return self._version

    def _scheduler(self) -> _ModeScheduler | None:
        with self._lock:
            mode = self._settings.mode
        if mode == "pulse":
            return self.pulse_scheduler
        if mode == "bend":
            return self.bend_scheduler
        if mode == "sequence":
            return self.sequence_scheduler
        return None

    @property
    def is_playing(self) -> bool:
        scheduler = self._scheduler()
        return bool(scheduler is not None and scheduler.is_running)

    playing = is_playing

    def state_args(self, port_ok: bool) -> list[Any]:
        """``/ji/drone/state`` payload (fourteen values, documented order)."""

        with self._lock:
            settings = self._settings
            bpm = self._bpm
        return settings.state_args(port_ok, self.is_playing, bpm)

    # -- lifecycle ------------------------------------------------------

    def start(self) -> None:
        """Start the mode scheduler when the transport is already running."""

        if self._transport_running and self.mode != "hold":
            self._start_mode()

    def stop(self) -> None:
        """Stop every mode and silence the bus."""

        self._stop_modes()
        self.anchor.stop()

    def set_transport(self, running: object) -> None:
        """``hold`` ignores the transport; ``pulse``/``bend`` are gated."""

        self._transport_running = bool(running)
        if self.mode == "hold":
            return
        if self._transport_running:
            self._start_mode()
        else:
            self._stop_modes()

    def set_bpm(self, bpm: object) -> float:
        value = rhythm.clamp_bpm(bpm)
        with self._lock:
            self._bpm = value
            return value

    # -- set updates ----------------------------------------------------

    def on_set(
        self,
        ratios: Sequence[float],
        anchor: float | None,
        fundamental: float,
    ) -> MPEPitch | None:
        """Store the snapshot; ``hold`` forwards to :class:`AnchorDrone` exactly."""

        with self._lock:
            self._snapshot = (list(ratios), anchor, float(fundamental))
            self._version += 1
            mode = self._settings.mode
        if mode == "hold":
            return self.anchor.on_set(ratios, anchor, fundamental)
        # pulse/bend read the snapshot at their next note/cycle; a mutation
        # during a slide is deferred to the next cycle (documented).
        return None

    update = on_set

    def anchor_target(self) -> tuple[float, float] | None:
        """``(ratio, frequency)`` for the current anchor, or ``None``."""

        with self._lock:
            snapshot = self._snapshot
        if snapshot is None:
            return None
        ratios, anchor, fundamental = snapshot
        result = select_target(ratios, anchor, fundamental)
        if result is None:
            _warn("empty ratio set; the drone has no note to play")
            return None
        if not self._fallback_warned:
            ratio = result[0]
            if anchor is None or not math.isclose(
                ratio, float(anchor), rel_tol=0.0, abs_tol=1e-12
            ):
                _warn(
                    "anchor is not in the current set; using "
                    f"ratio={ratio:.6f}"
                )
                self._fallback_warned = True
        return result

    def pulse_octave(self, step: int) -> int:
        """Octave offset for one pulse step (seeded, stable by seed)."""

        with self._lock:
            settings = self._settings
        span = int(settings.pulse_octave_span)
        if span <= 0:
            return 0
        key = (int(settings.seed), int(settings.pulse_notes), span)
        with self._lock:
            if key != self._pulse_key:
                rng = random.Random(
                    f"ji-drone-pulse|{key[0]}|{key[1]}|{key[2]}"
                )
                self._pulse_pattern = tuple(
                    rng.randint(-span, span) for _ in range(key[1])
                )
                self._pulse_key = key
            pattern = self._pulse_pattern
        return int(pattern[int(step) % len(pattern)])

    def sequence_frequency(self, offset: object) -> float | None:
        """Frequency for one signed set-step ``offset`` from the anchor.

        ``+1`` is the next class up in the current sorted set (wrapping into the
        next octave at the top), ``-1`` the next class down.  Returns ``None``
        when there is no set snapshot yet.
        """

        try:
            steps = int(round(float(offset)))
        except (TypeError, ValueError):
            raise TypeError("offset must be a real number") from None
        with self._lock:
            snapshot = self._snapshot
        if snapshot is None:
            return None
        target = self.anchor_target()
        if target is None:
            return None
        ratios, _anchor, fundamental = snapshot
        pool = sorted(
            float(ratio)
            for ratio in ratios
            if math.isfinite(float(ratio)) and float(ratio) > 0.0
        )
        if not pool:
            return None
        anchor_ratio = target[0]
        index = 0
        for position, ratio in enumerate(pool):
            if math.isclose(
                ratio, anchor_ratio, rel_tol=0.0, abs_tol=1e-12
            ):
                index = position
                break
        size = len(pool)
        degree = index + steps
        octave = 0
        while degree >= size:
            degree -= size
            octave += 1
        while degree < 0:
            degree += size
            octave -= 1
        octave = max(-SEQ_OCTAVE_LIMIT, min(SEQ_OCTAVE_LIMIT, octave))
        return float(fundamental) * pool[degree] * (2.0 ** octave)

    def log_slide(self, settings: DroneSettings) -> None:
        """One ``[DRONE] slide ...`` line per cycle (never per bend update)."""

        print(
            f"[DRONE] slide {settings.slide_direction} "
            f"{settings.slide_semitones:.2f} st over "
            f"{settings.slide_seconds:.3f} s"
        )

    # -- settings -------------------------------------------------------

    def set_setting(self, name: object, value: object) -> DroneSettings:
        key = str(name).strip().lower()
        if key == "mode":
            return self.set_mode(value)
        with self._lock:
            before = self._settings
            after = before.with_change(key, value)
            if after == before:
                return before
            self._settings = after
        if key == "velocity":
            self.anchor.velocity = after.velocity
        if key == "seed":
            with self._lock:
                self._pulse_key = None
        print(f"[DRONE] {key}={_describe_setting(after, key)}")
        return after

    def set_mode(self, mode: object) -> DroneSettings:
        """Switch mode without adding a second enable path.

        ``hold`` starts immediately; the gated modes start with the transport
        (or right away when it is already running) and say so in the log.
        """

        name = validate_mode(mode)
        with self._lock:
            if name == self._settings.mode:
                return self._settings
            previous = self._settings.mode
            self._settings = replace(self._settings, mode=name)
            snapshot = self._snapshot
            transport = self._transport_running
        self._stop_modes()
        if previous != "hold":
            # The mode thread already silenced; make sure nothing is left.
            self.anchor.stop()
        if name == "hold":
            if snapshot is not None:
                self.anchor.on_set(*snapshot)
            print(f"[DRONE] {self._settings.describe_mode(self.bpm)}")
            return self._settings
        if previous == "hold":
            self.anchor.stop()
        note = "started" if transport else "starts with the transport (press Play)"
        print(f"[DRONE] {self._settings.describe_mode(self.bpm)} ({note})")
        if transport:
            self._start_mode()
        return self._settings

    # -- internals ------------------------------------------------------

    def _start_mode(self) -> None:
        scheduler = self._scheduler()
        if scheduler is None:
            return
        was_running = scheduler.is_running
        try:
            scheduler.start()
        except Exception as exc:
            print(
                f"[DRONE] {self.mode} scheduler failed: {exc}", file=sys.stderr
            )
            return
        if not was_running and scheduler.is_running:
            print(f"[DRONE] {self.mode} started")

    def _stop_modes(self) -> None:
        for scheduler in (
            self.pulse_scheduler,
            self.bend_scheduler,
            self.sequence_scheduler,
        ):
            if scheduler.is_running:
                scheduler.stop()


def _describe_setting(settings: DroneSettings, key: str) -> str:
    if key in ("pulse_gate", "slide_seconds", "hold_beats"):
        return f"{getattr(settings, key):.3f}"
    if key == "pulse_octave_span":
        return str(settings.pulse_octave_span)
    return str(getattr(settings, key))


__all__ = [
    "AnchorDrone",
    "BendScheduler",
    "DEFAULT_CONFIG_PATH",
    "DEFAULT_DRONE_SEED",
    "DEFAULT_HOLD_BEATS",
    "DEFAULT_MODE",
    "DEFAULT_PULSE_GATE",
    "DEFAULT_PULSE_GRID",
    "DEFAULT_PULSE_NOTES",
    "DEFAULT_PULSE_SPAN",
    "DEFAULT_SLIDE_DIRECTION",
    "DEFAULT_SEQ_GATE",
    "DEFAULT_SEQ_BARS",
    "DEFAULT_SEQ_NOTES",
    "DEFAULT_SLIDE_SECONDS",
    "DEFAULT_SLIDE_SEMITONES",
    "DRONE_MODES",
    "DRONE_SEED_MAX",
    "DroneConfig",
    "DroneRig",
    "DroneSettings",
    "HOLD_BEATS_MAX",
    "HOLD_BEATS_MIN",
    "PULSE_GATE_MAX",
    "PULSE_GATE_MIN",
    "PULSE_GRIDS",
    "PULSE_GRID_QUARTERS",
    "PULSE_NOTES_MAX",
    "PULSE_NOTES_MIN",
    "PULSE_SPAN_MAX",
    "PULSE_SPAN_MIN",
    "PulseScheduler",
    "SEQ_GATE_MAX",
    "SEQ_GATE_MIN",
    "SEQ_BARS",
    "SEQ_BARS_VALUE",
    "SEQ_NOTES_MAX",
    "SEQ_NOTES_MIN",
    "SequenceScheduler",
    "SLIDE_DIRECTIONS",
    "SLIDE_SECONDS_MAX",
    "SLIDE_SECONDS_MIN",
    "SLIDE_SEMITONES_MAX",
    "SLIDE_SEMITONES_MIN",
    "SLIDE_UPDATES_PER_SECOND",
    "cycle_beats",
    "load_config",
    "select_target",
    "sequence_offsets",
    "validate_hold_beats",
    "validate_mode",
    "validate_pulse_gate",
    "validate_pulse_grid",
    "validate_pulse_notes",
    "validate_pulse_span",
    "validate_seed",
    "validate_seq_gate",
    "validate_seq_bars",
    "seq_bars_value",
    "validate_seq_notes",
    "validate_slide_direction",
    "validate_slide_seconds",
    "validate_slide_semitones",
    "validate_velocity",
]

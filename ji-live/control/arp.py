"""Two-group monophonic set arpeggiator for the Stage 6 MIDI rig.

Each regeneration picks a *shared* ratio from the current frequency set and
builds two three-note groups::

    group A = [shared, a1, a2]
    group B = [shared, b1, b2]

The shared note is the exact same :class:`ArpNote` object (same ratio, same
octave offset, same frequency) in both groups.  For set sizes >= 5 the four
non-shared slots are four distinct classes, so A and B share exactly one ratio.
Below 5 classes the non-shared pool is reused; the arp still runs, but
exact-one-shared/four-distinct is only guaranteed for set sizes >= 5.

Default playback order is::

    A1, A2, A3, B1, B2, B3, A1, A2, A3, ...

with a fixed 0.25 s step and 0.20 s gate.  These are placeholders; rhythm is
deferred.  A set update regenerates the pattern and applies it at the next step
boundary without restarting the clock or cutting a held note.
"""

from __future__ import annotations

import math
import random
import threading
import time
from dataclasses import dataclass
from fractions import Fraction

try:  # Support both ``python -m control.arp`` and direct execution.
    from .mpe_output import MPEOutput, MPEPitch
except ImportError:  # pragma: no cover - exercised only by direct execution
    from mpe_output import MPEOutput, MPEPitch

DEFAULT_STEP_SECONDS = 0.25
DEFAULT_GATE_SECONDS = 0.20
DEFAULT_OCTAVE_OFFSETS = (0, 1, 2)
DEFAULT_SHARED_MODE = "anchor"
DEFAULT_SEED = 1
DEFAULT_VELOCITY = 90
_GROUP_SIZE = 3
_PATTERN_STEPS = 6


@dataclass(frozen=True, slots=True)
class ArpNote:
    """One arp event: a ratio, its octave offset, and the resulting frequency."""

    ratio: float
    octave: int
    frequency: float


@dataclass(frozen=True, slots=True)
class ArpPattern:
    """Two three-note groups plus the shared note they have in common."""

    group_a: tuple[ArpNote, ArpNote, ArpNote]
    group_b: tuple[ArpNote, ArpNote, ArpNote]
    shared: ArpNote

    @property
    def steps(self) -> tuple[ArpNote, ...]:
        """The six-step A/B sequence."""

        return self.group_a + self.group_b

    @property
    def shared_ratio(self) -> float:
        return self.shared.ratio

    @property
    def shared_frequency(self) -> float:
        return self.shared.frequency


def build_groups(
    ratios: list[float] | tuple[float, ...],
    anchor: float | None,
    fundamental: float,
    rng: random.Random | None = None,
    shared_mode: str = DEFAULT_SHARED_MODE,
    octave_offsets: tuple[int, int, int] | list[int] = DEFAULT_OCTAVE_OFFSETS,
) -> ArpPattern:
    """Build one two-group arp pattern from a snapshot of the frequency set.

    Args:
        ratios: Current octave-reduced classes (unique, 1..12 values).
        anchor: Current anchor ratio, or ``None``.
        fundamental: Engine fundamental in Hz.
        rng: Dedicated ``random.Random``; a fresh one is created if omitted.
        shared_mode: ``"anchor"`` (use the anchor if it is in the set, else a
            random class) or ``"random"``.
        octave_offsets: Positional offsets ``(shared, non_shared_1,
            non_shared_2)``.  The shared offset is identical in both groups.

    Returns:
        A frozen :class:`ArpPattern`.
    """

    ratio_list = _clean_ratios(ratios)
    fund = _positive_float(fundamental, "fundamental")
    if rng is None:
        rng = random.Random(DEFAULT_SEED)
    if shared_mode not in ("anchor", "random"):
        raise ValueError("shared_mode must be 'anchor' or 'random'")
    offsets = _clean_offsets(octave_offsets)

    shared = _choose_shared(ratio_list, anchor, rng, shared_mode)
    non_shared = [ratio for ratio in ratio_list if ratio != shared]

    # Four distinct non-shared classes are possible only when the set has at
    # least five classes.  For smaller sets, cycle through what is available.
    if len(non_shared) >= 4:
        picks = rng.sample(non_shared, 4)
    else:
        pool = list(non_shared) or [shared]
        rng.shuffle(pool)
        picks = [pool[index % len(pool)] for index in range(4)]

    shared_note = _make_note(shared, offsets[0], fund)
    group_a = (
        shared_note,
        _make_note(picks[0], offsets[1], fund),
        _make_note(picks[1], offsets[2], fund),
    )
    group_b = (
        shared_note,
        _make_note(picks[2], offsets[1], fund),
        _make_note(picks[3], offsets[2], fund),
    )
    return ArpPattern(group_a=group_a, group_b=group_b, shared=shared_note)


# Common alternative name for callers/tests that think "generate".
generate_groups = build_groups


class SetArp:
    """Continuous monophonic arp driven by set-update snapshots.

    Args:
        output: An :class:`~control.mpe_output.MPEOutput` (or compatible fake).
        step_seconds: Fixed step duration (default 0.25 s).
        gate_seconds: Note-on duration within each step (default 0.20 s).
        octave_offsets: Positional octave offsets (default ``(0, 1, 2)``).
        shared_mode: ``"anchor"`` or ``"random"``.
        seed: Dedicated RNG seed; the same seed reproduces the pattern.
        velocity: Note-on velocity (default 90).
    """

    def __init__(
        self,
        output: MPEOutput,
        step_seconds: float = DEFAULT_STEP_SECONDS,
        gate_seconds: float = DEFAULT_GATE_SECONDS,
        octave_offsets: tuple[int, int, int] | list[int] = DEFAULT_OCTAVE_OFFSETS,
        shared_mode: str = DEFAULT_SHARED_MODE,
        seed: int = DEFAULT_SEED,
        velocity: int = DEFAULT_VELOCITY,
    ) -> None:
        self.output = output
        self.step_seconds = _positive_float(step_seconds, "step_seconds")
        self.gate_seconds = _positive_float(gate_seconds, "gate_seconds")
        if self.gate_seconds > self.step_seconds:
            raise ValueError("gate_seconds must be <= step_seconds")
        self.octave_offsets = _clean_offsets(octave_offsets)
        if shared_mode not in ("anchor", "random"):
            raise ValueError("shared_mode must be 'anchor' or 'random'")
        self.shared_mode = shared_mode
        if isinstance(seed, bool) or not isinstance(seed, int):
            raise TypeError("seed must be an integer")
        self.seed = seed
        if isinstance(velocity, bool) or not isinstance(velocity, int):
            raise TypeError("velocity must be an integer")
        if not 1 <= velocity <= 127:
            raise ValueError("velocity must be between 1 and 127")
        self.velocity = velocity

        self._rng = random.Random(seed)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._pattern: ArpPattern | None = None
        self._step_index = 0
        self._last_signature: tuple | None = None
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    @property
    def pattern(self) -> ArpPattern | None:
        with self._lock:
            return self._pattern

    @property
    def groups(self) -> tuple[tuple[ArpNote, ...], tuple[ArpNote, ...]] | None:
        with self._lock:
            pattern = self._pattern
        if pattern is None:
            return None
        return pattern.group_a, pattern.group_b

    @property
    def shared_ratio(self) -> float | None:
        with self._lock:
            pattern = self._pattern
        return None if pattern is None else pattern.shared_ratio

    @property
    def is_running(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    # ------------------------------------------------------------------
    # Set updates
    # ------------------------------------------------------------------

    def on_set(
        self,
        ratios: list[float] | tuple[float, ...],
        anchor: float | None,
        fundamental: float,
    ) -> ArpPattern:
        """Regenerate the pattern from a snapshot; safe to call before start.

        The new pattern is stored immediately but only takes effect at the next
        step boundary.  No MIDI is sent here, so a held note is not cut.
        """

        with self._lock:
            pattern = build_groups(
                ratios,
                anchor,
                fundamental,
                rng=self._rng,
                shared_mode=self.shared_mode,
                octave_offsets=self.octave_offsets,
            )
            signature = _pattern_signature(pattern)
            changed = signature != self._last_signature
            self._pattern = pattern
            self._step_index = 0
            self._last_signature = signature

        if changed:
            _print_pattern(pattern)
        return pattern

    # Aliases used by callers that think in set-update / regeneration terms.
    update = on_set
    regenerate = on_set

    # ------------------------------------------------------------------
    # Thread lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the arp thread.  Idempotent while already running."""

        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            thread = threading.Thread(
                target=self._run,
                name="ji-set-arp",
                daemon=True,
            )
            self._thread = thread
            thread.start()

    def stop(self) -> None:
        """Stop the thread, note-off any held note, and send All Notes Off."""

        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        with self._lock:
            if self._thread is thread:
                self._thread = None
        self.output.all_notes_off()

    # ------------------------------------------------------------------
    # Thread body
    # ------------------------------------------------------------------

    def _run(self) -> None:  # pragma: no cover - exercised through fake ports
        step = self.step_seconds
        gate = self.gate_seconds
        deadline = time.monotonic()
        held_note: int | None = None

        try:
            while not self._stop.is_set():
                with self._lock:
                    pattern = self._pattern
                    if pattern is None:
                        note = None
                        step_number = 0
                    else:
                        index = self._step_index % _PATTERN_STEPS
                        self._step_index = (index + 1) % _PATTERN_STEPS
                        note = pattern.steps[index]
                        step_number = index + 1

                if note is None:
                    deadline += step
                    wait = deadline - time.monotonic()
                    if wait > 0.0 and self._stop.wait(wait):
                        break
                    continue

                # A previous gate longer than the step would still be held.
                if held_note is not None:
                    self.output.note_off(held_note)
                    held_note = None

                pitch: MPEPitch = self.output.play(note.frequency, self.velocity)
                held_note = pitch.note
                print(
                    f"[ARP] step={step_number} freq={note.frequency:.2f} "
                    f"note={pitch.note} bend={pitch.bend}"
                )

                if self._stop.wait(gate):
                    break
                self.output.note_off(held_note)
                held_note = None

                deadline += step
                now = time.monotonic()
                if deadline < now:
                    # Fell behind (slow output/JIT); resume from now, do not
                    # burst through missed steps.
                    deadline = now
                wait = deadline - now
                if wait > 0.0 and self._stop.wait(wait):
                    break
        finally:
            if held_note is not None:
                self.output.note_off(held_note)


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _choose_shared(
    ratios: list[float],
    anchor: float | None,
    rng: random.Random,
    shared_mode: str,
) -> float:
    if shared_mode == "anchor" and anchor is not None:
        try:
            anchor_value = float(anchor)
        except (TypeError, ValueError):
            anchor_value = None
        if anchor_value is not None:
            for ratio in ratios:
                if math.isclose(
                    ratio, anchor_value, rel_tol=0.0, abs_tol=1e-12
                ):
                    return ratio
    return rng.choice(ratios)


def _make_note(ratio: float, octave: int, fundamental: float) -> ArpNote:
    return ArpNote(
        ratio=ratio,
        octave=octave,
        frequency=float(ratio * fundamental * (2.0 ** octave)),
    )


def _clean_ratios(
    ratios: list[float] | tuple[float, ...],
) -> list[float]:
    if isinstance(ratios, (str, bytes)):
        raise TypeError("ratios must be an iterable of real numbers")
    cleaned: list[float] = []
    for raw in ratios:
        value = _positive_float(raw, "ratio")
        if value not in cleaned:
            cleaned.append(value)
    if not cleaned:
        raise ValueError("ratios must not be empty")
    return cleaned


def _clean_offsets(
    offsets: tuple[int, ...] | list[int],
) -> tuple[int, int, int]:
    if isinstance(offsets, (str, bytes)):
        raise TypeError("octave_offsets must contain three integers")
    values = list(offsets)
    if len(values) != 3:
        raise ValueError("octave_offsets must contain exactly three integers")
    cleaned: list[int] = []
    for value in values:
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError("octave_offsets must contain integers")
        if not -8 <= value <= 8:
            raise ValueError("octave offsets must be between -8 and 8")
        cleaned.append(value)
    return cleaned[0], cleaned[1], cleaned[2]


def _pattern_signature(pattern: ArpPattern) -> tuple:
    def note_key(note: ArpNote) -> tuple[float, int, float]:
        return (note.ratio, note.octave, round(note.frequency, 6))

    return (
        tuple(note_key(note) for note in pattern.group_a),
        tuple(note_key(note) for note in pattern.group_b),
    )


def _print_pattern(pattern: ArpPattern) -> None:
    group_a = ", ".join(_ratio_label(note.ratio) for note in pattern.group_a)
    group_b = ", ".join(_ratio_label(note.ratio) for note in pattern.group_b)
    print(
        f"[ARP] shared={_ratio_label(pattern.shared_ratio)} "
        f"({pattern.shared_frequency:.2f} Hz)  "
        f"A=[{group_a}]  B=[{group_b}]"
    )


def _ratio_label(ratio: float) -> str:
    fraction = Fraction(ratio).limit_denominator(64)
    if abs(float(fraction) - ratio) <= max(1e-6, abs(ratio) * 1e-6):
        return f"{fraction.numerator}/{fraction.denominator}"
    return f"{ratio:.6f}"


def _positive_float(value: float, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise TypeError(f"{label} must be a real number") from None
    if not math.isfinite(number) or number <= 0.0:
        raise ValueError(f"{label} must be finite and strictly positive")
    return number

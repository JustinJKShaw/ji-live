"""Stage 8.1 rhythm foundation: tempo, meter, transport, and a monotonic clock.

This module is deliberately pure: no ``mido``, no audio, no OSC sockets.  It
owns the canonical rhythm state used by the Python bridge and the shared math
used by the drum scheduler and the UI layer:

* quarter-note BPM (20-300, default 120);
* meter numerator 1-16 and denominator 1/2/4/8/16 (default 4/4);
* transport running 0/1;
* bar/beat/16th-step conversions;
* beat quantization helpers for beat-locked loops (the actual scheduling is in
  sclang);
* :class:`BeatClock`, a monotonic beat clock that survives tempo changes
  without restarting.

Timing rules (design doc §6.14):

* BPM is quarter-note based, so the quarter-note beat is ``60 / bpm`` seconds.
* The bar is ``numerator * 4 / denominator`` quarter notes long.
* A 16th-note step is ``60 / bpm / 4`` seconds, divided by the drum rate
  multiplier (0.5 = half time/slower, 2 = double time/faster).
* Beat-locked loop takes are ``beats * 60 / bpm`` seconds at record time.
  Tempo changes do **not** time-stretch an existing buffer in 8.1.

The clock uses ``time.monotonic`` by default and accepts an injectable ``now``
function so tests never sleep.
"""

from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass, replace
from typing import Callable

# ---------------------------------------------------------------------------
# Ranges and defaults (single source of truth for Python and the UI)
# ---------------------------------------------------------------------------

BPM_MIN = 20.0
BPM_MAX = 300.0
BPM_DEFAULT = 120.0

METER_NUM_MIN = 1
METER_NUM_MAX = 16
METER_NUM_DEFAULT = 4
METER_DENOMINATORS: tuple[int, ...] = (1, 2, 4, 8, 16)
METER_DEN_DEFAULT = 4

STEPS_PER_QUARTER = 4  # the drum pattern grid is 16th notes

DRUM_RATES: tuple[float, ...] = (0.5, 1.0, 2.0)
DRUM_RATE_DEFAULT = 1.0

LOOP_MODES: tuple[str, ...] = ("free", "beat")
LOOP_MODE_DEFAULT = "free"
LOOP_BEATS_MIN = 1.0
LOOP_BEATS_MAX = 64.0
LOOP_BEATS_DEFAULT = 4.0
LOOP_SECONDS_MIN = 1.0
LOOP_SECONDS_MAX = 30.0
LOOP_SECONDS_DEFAULT = 8.0

LOOP_INDEX_MIN = 1
LOOP_INDEX_MAX = 4


# ---------------------------------------------------------------------------
# Validation / clamping helpers
# ---------------------------------------------------------------------------


def _finite(value: object, label: str) -> float:
    try:
        numeric = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise TypeError(f"{label} must be a real number") from None
    if not math.isfinite(numeric):
        raise ValueError(f"{label} must be finite")
    return numeric


def clamp_bpm(value: object) -> float:
    """Return ``value`` as a quarter-note BPM clamped to 20-300."""

    return max(BPM_MIN, min(BPM_MAX, _finite(value, "bpm")))


def clamp_meter_numerator(value: object) -> int:
    """Return ``value`` rounded and clamped to 1-16."""

    return max(METER_NUM_MIN, min(METER_NUM_MAX, int(round(_finite(value, "numerator")))))


def validate_meter_denominator(value: object) -> int:
    """Return a supported denominator or raise ``ValueError``.

    Unlike the numerator, the denominator is *rejected* rather than snapped:
    silently turning 3 into 4 would change the bar length behind the user's
    back.
    """

    denominator = int(round(_finite(value, "denominator")))
    if denominator not in METER_DENOMINATORS:
        raise ValueError(
            "denominator must be one of "
            + ", ".join(str(item) for item in METER_DENOMINATORS)
        )
    return denominator


def validate_loop_mode(value: object) -> str:
    """Return ``"free"`` or ``"beat"``; anything else raises ``ValueError``."""

    mode = str(value).strip().lower()
    if mode not in LOOP_MODES:
        raise ValueError("loop mode must be 'free' or 'beat'")
    return mode


def validate_loop_index(value: object) -> int:
    """Return a 1-based loop index in 1-4 or raise ``ValueError``."""

    index = int(round(_finite(value, "loop index")))
    if not LOOP_INDEX_MIN <= index <= LOOP_INDEX_MAX:
        raise ValueError(
            f"loop index must be {LOOP_INDEX_MIN}..{LOOP_INDEX_MAX}, got {value!r}"
        )
    return index


def clamp_loop_beats(value: object) -> float:
    """Return a beat-mode loop length clamped to 1-64 beats."""

    return max(LOOP_BEATS_MIN, min(LOOP_BEATS_MAX, _finite(value, "loop beats")))


def clamp_loop_seconds(value: object) -> float:
    """Return a free-mode loop length clamped to 1-30 seconds."""

    return max(LOOP_SECONDS_MIN, min(LOOP_SECONDS_MAX, _finite(value, "loop seconds")))


def validate_drum_rate(value: object) -> float:
    """Return 0.5, 1.0, or 2.0; any other multiplier is rejected."""

    numeric = _finite(value, "drum rate")
    for allowed in DRUM_RATES:
        if math.isclose(numeric, allowed, rel_tol=0.0, abs_tol=1e-9):
            return allowed
    raise ValueError("drum rate must be 0.5, 1.0, or 2.0")


# ---------------------------------------------------------------------------
# Pure tempo / meter / step math
# ---------------------------------------------------------------------------


def beat_seconds(bpm: object = BPM_DEFAULT) -> float:
    """Quarter-note duration in seconds (``60 / bpm``)."""

    return 60.0 / clamp_bpm(bpm)


def bar_quarters(num: object = METER_NUM_DEFAULT, den: object = METER_DEN_DEFAULT) -> float:
    """Bar length in quarter notes: ``numerator * 4 / denominator``."""

    numerator = clamp_meter_numerator(num)
    denominator = validate_meter_denominator(den)
    return numerator * 4.0 / denominator


def bar_seconds(
    bpm: object = BPM_DEFAULT,
    num: object = METER_NUM_DEFAULT,
    den: object = METER_DEN_DEFAULT,
) -> float:
    """Bar duration in seconds at ``bpm``."""

    return bar_quarters(num, den) * beat_seconds(bpm)


def step_seconds(bpm: object = BPM_DEFAULT, rate: object = DRUM_RATE_DEFAULT) -> float:
    """Duration of one 16th-note pattern step, divided by the rate multiplier.

    ``rate=0.5`` halves the rate (each step lasts twice as long; half time),
    ``rate=2.0`` doubles it (double time).  Tempo and meter state are unchanged.
    """

    multiplier = validate_drum_rate(rate)
    return 60.0 / clamp_bpm(bpm) / STEPS_PER_QUARTER / multiplier


def beats_to_seconds(beats: object, bpm: object = BPM_DEFAULT) -> float:
    """Convert quarter-note beats to seconds at ``bpm``."""

    return _finite(beats, "beats") * beat_seconds(bpm)


def seconds_to_beats(seconds: object, bpm: object = BPM_DEFAULT) -> float:
    """Convert seconds to quarter-note beats at ``bpm``."""

    return _finite(seconds, "seconds") / beat_seconds(bpm)


def loop_take_seconds(beats: object, bpm: object = BPM_DEFAULT) -> float:
    """Beat-mode loop take length at record time (``beats * 60 / bpm``)."""

    count = clamp_loop_beats(beats)
    return beats_to_seconds(count, bpm)


def steps_per_bar(
    num: object = METER_NUM_DEFAULT, den: object = METER_DEN_DEFAULT
) -> int:
    """Number of 16th-note steps in one bar.

    Every supported denominator divides 16, so this is exact integer math:
    4/4 -> 16, 3/4 -> 12, 6/8 -> 12, 5/4 -> 20, 7/8 -> 14, 1/16 -> 1.
    """

    numerator = clamp_meter_numerator(num)
    denominator = validate_meter_denominator(den)
    return numerator * (16 // denominator)


def quarter_indices_in_bar(
    num: object = METER_NUM_DEFAULT, den: object = METER_DEN_DEFAULT
) -> tuple[int, ...]:
    """Quarter-note beat indices whose onset lies inside the bar.

    For 6/8 the bar is three quarter notes long, so this returns ``(0, 1, 2)``.
    For 7/8 it returns ``(0, 1, 2, 3)`` because beat 3 starts at 3.0 < 3.5.
    """

    length = bar_quarters(num, den)
    return tuple(range(int(math.ceil(length - 1e-9))))


# ---------------------------------------------------------------------------
# Quantization (used by the sclang loop surface; mirrored here for tests)
# ---------------------------------------------------------------------------


def quantize_beats(beats: object, quantum: object = 1.0) -> float:
    """Round a beat position **up** to the next multiple of ``quantum``."""

    position = _finite(beats, "beats")
    grid = _finite(quantum, "quantum")
    if grid <= 0.0:
        raise ValueError("quantum must be strictly positive")
    return math.ceil(position / grid - 1e-9) * grid


def next_beat_time(
    now: object,
    origin: object,
    beat_period: object,
    quantum: object = 1.0,
) -> float:
    """Absolute time of the next beat boundary after ``now``.

    ``origin`` is the transport-start time and ``beat_period`` is one quarter
    note in seconds.  A time exactly on a boundary returns the *next* boundary,
    never the current instant, so a beat-quantized loop cannot start
    immediately by accident.
    """

    current = _finite(now, "now")
    start = _finite(origin, "origin")
    period = _finite(beat_period, "beat_period")
    grid = _finite(quantum, "quantum")
    if period <= 0.0:
        raise ValueError("beat_period must be strictly positive")
    if grid <= 0.0:
        raise ValueError("quantum must be strictly positive")

    if current < start:
        return start
    position = (current - start) / period
    beats = math.floor(position / grid + 1e-9) * grid + grid
    return start + beats * period


# ---------------------------------------------------------------------------
# State and clock
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RhythmState:
    """Immutable snapshot of the canonical bridge-side rhythm state."""

    bpm: float = BPM_DEFAULT
    meter_num: int = METER_NUM_DEFAULT
    meter_den: int = METER_DEN_DEFAULT
    running: bool = False

    @classmethod
    def create(
        cls,
        bpm: object = BPM_DEFAULT,
        meter_num: object = METER_NUM_DEFAULT,
        meter_den: object = METER_DEN_DEFAULT,
        running: object = False,
    ) -> "RhythmState":
        return cls(
            bpm=clamp_bpm(bpm),
            meter_num=clamp_meter_numerator(meter_num),
            meter_den=validate_meter_denominator(meter_den),
            running=bool(running),
        )

    @property
    def meter(self) -> str:
        return f"{self.meter_num}/{self.meter_den}"

    @property
    def bar_quarters(self) -> float:
        return bar_quarters(self.meter_num, self.meter_den)

    @property
    def beat_seconds(self) -> float:
        return beat_seconds(self.bpm)

    @property
    def bar_seconds(self) -> float:
        return bar_seconds(self.bpm, self.meter_num, self.meter_den)

    @property
    def steps_per_bar(self) -> int:
        return steps_per_bar(self.meter_num, self.meter_den)

    def step_seconds(self, rate: object = DRUM_RATE_DEFAULT) -> float:
        return step_seconds(self.bpm, rate)

    def as_tempo_args(self) -> list[float | int]:
        """Payload for ``/ji/rhythm/tempo`` (bridge -> sclang)."""

        return [float(self.bpm), int(self.meter_num), int(self.meter_den), int(self.running)]

    def as_state_args(
        self, drums_enabled: bool, drum_rate: float
    ) -> list[float | int]:
        """Payload for ``/ji/rhythm/state`` (bridge -> UI)."""

        return [
            float(self.bpm),
            int(self.meter_num),
            int(self.meter_den),
            int(self.running),
            int(bool(drums_enabled)),
            float(drum_rate),
        ]

    def with_tempo(self, bpm: object) -> "RhythmState":
        return replace(self, bpm=clamp_bpm(bpm))

    def with_meter(self, num: object, den: object) -> "RhythmState":
        return replace(
            self,
            meter_num=clamp_meter_numerator(num),
            meter_den=validate_meter_denominator(den),
        )

    def with_running(self, running: object) -> "RhythmState":
        return replace(self, running=bool(running))


class BeatClock:
    """A monotonic beat clock that can change tempo without restarting.

    The clock stores an anchor ``(time, beat)`` pair.  ``beat_at`` extrapolates
    from the anchor.  Changing the tempo first re-anchors at the current beat,
    so the phase is continuous; starting the transport resets the beat to 0.

    All methods are thread-safe and accept an explicit ``at`` timestamp for
    tests.
    """

    def __init__(
        self,
        bpm: object = BPM_DEFAULT,
        *,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        if not callable(now):
            raise TypeError("now must be callable")
        self._now = now
        self._lock = threading.RLock()
        self._bpm = clamp_bpm(bpm)
        self._beat_seconds = 60.0 / self._bpm
        self._anchor_time = float(now())
        self._anchor_beat = 0.0
        self._running = False

    @property
    def bpm(self) -> float:
        with self._lock:
            return self._bpm

    @property
    def beat_seconds(self) -> float:
        with self._lock:
            return self._beat_seconds

    @property
    def running(self) -> bool:
        with self._lock:
            return self._running

    def _time(self, at: float | None) -> float:
        return float(self._now() if at is None else at)

    def start(self, at: float | None = None) -> None:
        """Start (or restart) at beat 0.  Idempotent while running."""

        with self._lock:
            if self._running:
                return
            self._anchor_time = self._time(at)
            self._anchor_beat = 0.0
            self._running = True

    def stop(self, at: float | None = None) -> None:
        """Freeze the beat position and stop.  Idempotent while stopped."""

        with self._lock:
            if not self._running:
                return
            now = self._time(at)
            self._anchor_beat = self._beat_at_locked(now)
            self._anchor_time = now
            self._running = False

    def set_bpm(self, bpm: object, at: float | None = None) -> float:
        """Change tempo, keeping the current beat phase continuous."""

        value = clamp_bpm(bpm)
        with self._lock:
            if value == self._bpm:
                return value
            if self._running:
                now = self._time(at)
                self._anchor_beat = self._beat_at_locked(now)
                self._anchor_time = now
            self._bpm = value
            self._beat_seconds = 60.0 / value
            return value

    def beat_at(self, at: float | None = None) -> float:
        with self._lock:
            return self._beat_at_locked(self._time(at))

    def _beat_at_locked(self, at: float) -> float:
        if not self._running:
            return self._anchor_beat
        return self._anchor_beat + (at - self._anchor_time) / self._beat_seconds

    def time_of_beat(self, beat: object) -> float:
        """Absolute time at which the (possibly fractional) ``beat`` occurs."""

        value = _finite(beat, "beat")
        with self._lock:
            return self._anchor_time + (value - self._anchor_beat) * self._beat_seconds

    def next_beat_time(self, quantum: object = 1.0, at: float | None = None) -> float:
        """Time of the next beat boundary after ``at`` (or now)."""

        grid = _finite(quantum, "quantum")
        if grid <= 0.0:
            raise ValueError("quantum must be strictly positive")
        now = self._time(at)
        with self._lock:
            position = self._beat_at_locked(now)
        beats = math.floor(position / grid + 1e-9) * grid + grid
        return self.time_of_beat(beats)


class RhythmEngine:
    """Thread-safe rhythm state + clock owned by the Python bridge.

    Mutating methods return the new :class:`RhythmState` snapshot so the bridge
    can broadcast exactly what changed.  No OSC, audio, or logging happens here.
    """

    def __init__(
        self,
        bpm: object = BPM_DEFAULT,
        meter_num: object = METER_NUM_DEFAULT,
        meter_den: object = METER_DEN_DEFAULT,
        running: object = False,
        *,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        state = RhythmState.create(bpm, meter_num, meter_den, running)
        self._lock = threading.RLock()
        self._state = state
        self._clock = BeatClock(state.bpm, now=now)
        if state.running:
            self._clock.start()

    @property
    def state(self) -> RhythmState:
        with self._lock:
            return self._state

    @property
    def bpm(self) -> float:
        with self._lock:
            return self._state.bpm

    @property
    def meter(self) -> str:
        with self._lock:
            return self._state.meter

    @property
    def running(self) -> bool:
        with self._lock:
            return self._state.running

    @property
    def clock(self) -> BeatClock:
        return self._clock

    def set_bpm(self, bpm: object) -> RhythmState:
        value = clamp_bpm(bpm)
        with self._lock:
            if value != self._state.bpm:
                self._clock.set_bpm(value)
                self._state = self._state.with_tempo(value)
            return self._state

    def set_meter(self, meter_num: object, meter_den: object) -> RhythmState:
        numerator = clamp_meter_numerator(meter_num)
        denominator = validate_meter_denominator(meter_den)
        with self._lock:
            if (numerator, denominator) != (
                self._state.meter_num,
                self._state.meter_den,
            ):
                self._state = self._state.with_meter(numerator, denominator)
            return self._state

    def set_running(self, running: object) -> RhythmState:
        value = bool(running)
        with self._lock:
            if value == self._state.running:
                return self._state
            if value:
                self._clock.start()
            else:
                self._clock.stop()
            self._state = self._state.with_running(value)
            return self._state

    def beat_at(self, at: float | None = None) -> float:
        return self._clock.beat_at(at)

    def next_beat_time(self, quantum: object = 1.0, at: float | None = None) -> float:
        return self._clock.next_beat_time(quantum, at)


__all__ = [
    "BPM_DEFAULT",
    "BPM_MAX",
    "BPM_MIN",
    "BeatClock",
    "DRUM_RATE_DEFAULT",
    "DRUM_RATES",
    "LOOP_BEATS_DEFAULT",
    "LOOP_BEATS_MAX",
    "LOOP_BEATS_MIN",
    "LOOP_INDEX_MAX",
    "LOOP_INDEX_MIN",
    "LOOP_MODE_DEFAULT",
    "LOOP_MODES",
    "LOOP_SECONDS_DEFAULT",
    "LOOP_SECONDS_MAX",
    "LOOP_SECONDS_MIN",
    "METER_DENOMINATORS",
    "METER_DEN_DEFAULT",
    "METER_NUM_DEFAULT",
    "METER_NUM_MAX",
    "METER_NUM_MIN",
    "RhythmEngine",
    "RhythmState",
    "STEPS_PER_QUARTER",
    "bar_quarters",
    "bar_seconds",
    "beat_seconds",
    "beats_to_seconds",
    "clamp_bpm",
    "clamp_loop_beats",
    "clamp_loop_seconds",
    "clamp_meter_numerator",
    "loop_take_seconds",
    "next_beat_time",
    "quantize_beats",
    "quarter_indices_in_bar",
    "seconds_to_beats",
    "step_seconds",
    "steps_per_bar",
    "validate_drum_rate",
    "validate_loop_index",
    "validate_loop_mode",
    "validate_meter_denominator",
]

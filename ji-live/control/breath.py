"""Stage 8.7 breathing rhythm engine: settings, curves and a free clock.

This module is deliberately pure: no sockets, no audio, no threads beyond the
clock's own lock, and an injectable ``now`` function so tests never sleep.

The breath is a *volume* ``V(phi)`` in ``[0, 1]`` over one cycle.  It never
touches :class:`control.rhythm.BeatClock` and never changes the tempo or the
bar grid: it is a scheduling offset (and a velocity/expression delta) applied
inside each voice's scheduler, exactly like the existing swing offset.

Timing follows the *flow* (the derivative of the volume): the music pushes
while the diaphragm fills and relaxes while it empties.  ``V(1) == V(0)`` for
every shape, so the mean slope over one cycle is zero and there is no
cumulative drift.

Design doc section 6.14; stage 8.7.
"""

from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass, replace
from typing import Callable

try:  # Support both ``python -m control.breath`` and direct execution.
    from . import rhythm
except ImportError:  # pragma: no cover - exercised only by direct execution
    import rhythm  # type: ignore[no-redef]

# ---------------------------------------------------------------------------
# Ranges and defaults (single source of truth for Python and the UI)
# ---------------------------------------------------------------------------

BREATH_SHAPES: tuple[str, ...] = ("sine", "triangle", "inhale", "exhale")
BREATH_DEFAULT_SHAPE = "inhale"

BREATH_DEPTH_MAX = 1.0
BREATH_PERIOD_BARS_MIN = 1
BREATH_PERIOD_BARS_MAX = 32
BREATH_DEFAULT_PERIOD_BARS = 4
BREATH_DEFAULT_DEPTH = 0.35
BREATH_DEFAULT_INTENSITY = 0.5
BREATH_DEFAULT_PHASE = 0.0
BREATH_DEFAULT_ARP_LOCAL = 0.0
#: Stage 8.7.1: False keeps the arp on its own half-cycle phase (the default);
#: True makes it use the master phase so it breathes in sync with the groove.
BREATH_DEFAULT_ARP_SYNC = False

BREATH_MAX_SHIFT_STEPS = 0.5  # of a 16th note, at depth 1.0
BREATH_VELOCITY_SWING = 14  # +/- MIDI velocity at intensity 1.0

#: Voices the bridge clock knows about.  The arp is the only ``local`` voice.
BREATH_VOICES: tuple[str, ...] = ("drums", "bass", "arp", "lead", "drone")

#: Offset clamp as a fraction of the voice's own step (see the schedulers).
BREATH_OFFSET_LIMIT = 0.45


# ---------------------------------------------------------------------------
# Validation / clamping helpers
# ---------------------------------------------------------------------------


def _finite(value: object, label: str) -> float:
    if isinstance(value, bool):
        raise TypeError(f"{label} must be a real number")
    try:
        numeric = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise TypeError(f"{label} must be a real number") from None
    if not math.isfinite(numeric):
        raise ValueError(f"{label} must be finite")
    return numeric


def validate_bool(value: object, label: str = "value") -> bool:
    """Return a strict boolean; 0/1 is accepted for OSC convenience."""

    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    raise TypeError(f"{label} must be a boolean or 0/1")


def clamp_unit(value: object, label: str = "value") -> float:
    """Clamp a finite real into ``0.0 .. 1.0``."""

    return max(0.0, min(1.0, _finite(value, label)))


def validate_shape(value: object) -> str:
    """Return one of :data:`BREATH_SHAPES` or raise ``ValueError``."""

    shape = str(value).strip().lower()
    if shape not in BREATH_SHAPES:
        raise ValueError(
            "shape must be one of " + ", ".join(BREATH_SHAPES)
        )
    return shape


def validate_period_bars(value: object) -> int:
    """Return an integer period in 1..32 bars (rounded, then clamped)."""

    if isinstance(value, bool):
        raise TypeError("period_bars must be a real number")
    try:
        numeric = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise TypeError("period_bars must be a real number") from None
    if not math.isfinite(numeric):
        raise ValueError("period_bars must be finite")
    return max(
        BREATH_PERIOD_BARS_MIN,
        min(BREATH_PERIOD_BARS_MAX, int(round(numeric))),
    )


# ---------------------------------------------------------------------------
# The curve (this exact maths; design doc section 6.14)
# ---------------------------------------------------------------------------


def _warp(shape: str, phase: float) -> float:
    p = phase % 1.0
    if shape == "sine":
        return p
    if shape == "triangle":
        return 1.0 - abs(2.0 * p - 1.0)
    if shape == "inhale":
        return p ** 0.6  # fast fill, slow release
    return p ** 1.6  # slow fill, fast release ("exhale")


def breath_volume(shape: str, phase: float) -> float:
    """Raised cosine over the warped phase: ``V`` in ``0..1``."""

    w = _warp(validate_shape(shape), _finite(phase, "phase"))
    return 0.5 - 0.5 * math.cos(2.0 * math.pi * w)


def breath_slope(shape: str, phase: float, _h: float = 1e-4) -> float:
    """Central-difference slope of :func:`breath_volume` (per cycle unit)."""

    value = _finite(phase, "phase")
    return (
        breath_volume(shape, value + _h)
        - breath_volume(shape, value - _h)
    ) / (2.0 * _h)


def _slope_norm(shape: str) -> float:
    return max(
        abs(breath_slope(shape, index / 512.0)) for index in range(512)
    )


#: Peak absolute slope per shape, so depth 1 shifts by exactly MAX_SHIFT_STEPS.
_SLOPE_NORM: dict[str, float] = {shape: _slope_norm(shape) for shape in BREATH_SHAPES}


def breath_offset_steps(shape: str, phase: float, depth: float) -> float:
    """Timing offset in 16th-note steps for a shape at ``phase``.

    ``|offset| <= depth * BREATH_MAX_SHIFT_STEPS`` and the mean over a cycle is
    zero (no drift).  Depth 0 returns exactly ``0.0``.
    """

    amount = clamp_unit(depth, "depth")
    if amount <= 0.0:
        return 0.0
    normalised = _SLOPE_NORM[validate_shape(shape)]
    if normalised <= 0.0:  # pragma: no cover - every shape has a slope
        return 0.0
    return (
        amount
        * BREATH_MAX_SHIFT_STEPS
        * breath_slope(shape, phase)
        / normalised
    )


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BreathSettings:
    """Immutable, validated breath settings (the live feel control).

    The breath is intentionally **not** persisted in a performance state: it
    is a live performance gesture, not part of the saved rig.
    """

    enabled: bool = False
    depth: float = BREATH_DEFAULT_DEPTH
    period_bars: int = BREATH_DEFAULT_PERIOD_BARS
    shape: str = BREATH_DEFAULT_SHAPE
    intensity: float = BREATH_DEFAULT_INTENSITY
    phase: float = BREATH_DEFAULT_PHASE
    arp_local: float = BREATH_DEFAULT_ARP_LOCAL
    arp_sync: bool = BREATH_DEFAULT_ARP_SYNC

    @classmethod
    def create(cls, **values: object) -> "BreathSettings":
        """Build settings with every value validated/clamped."""

        defaults = cls()
        return cls(
            enabled=validate_bool(
                values.get("enabled", defaults.enabled), "enabled"
            ),
            depth=clamp_unit(values.get("depth", defaults.depth), "depth"),
            period_bars=validate_period_bars(
                values.get("period_bars", defaults.period_bars)
            ),
            shape=validate_shape(values.get("shape", defaults.shape)),
            intensity=clamp_unit(
                values.get("intensity", defaults.intensity), "intensity"
            ),
            phase=clamp_unit(values.get("phase", defaults.phase), "phase"),
            arp_local=clamp_unit(
                values.get("arp_local", defaults.arp_local), "arp_local"
            ),
            arp_sync=validate_bool(
                values.get("arp_sync", defaults.arp_sync), "arp_sync"
            ),
        )

    def with_change(self, name: str, value: object) -> "BreathSettings":
        """Return settings with one validated change applied."""

        key = str(name).strip().lower()
        if key == "enabled":
            return replace(self, enabled=validate_bool(value, "enabled"))
        if key == "depth":
            return replace(self, depth=clamp_unit(value, "depth"))
        if key == "period_bars":
            return replace(
                self, period_bars=validate_period_bars(value)
            )
        if key == "period":
            return replace(
                self, period_bars=validate_period_bars(value)
            )
        if key == "shape":
            return replace(self, shape=validate_shape(value))
        if key == "intensity":
            return replace(self, intensity=clamp_unit(value, "intensity"))
        if key == "phase":
            return replace(self, phase=clamp_unit(value, "phase"))
        if key in ("arp_local", "arplocal"):
            return replace(self, arp_local=clamp_unit(value, "arp_local"))
        if key in ("arp_sync", "arpsync"):
            return replace(
                self, arp_sync=validate_bool(value, "arp_sync")
            )
        raise ValueError(
            f"unknown breath setting {name!r}; expected enabled, depth, "
            "period_bars, shape, intensity, phase, arp_local, arp_sync"
        )

    def state_args(self) -> list[float | int | str]:
        """The 8-value ``/ji/breath/state`` payload (8.7.1 appends arp_sync)."""

        return [
            int(self.enabled),
            float(self.depth),
            int(self.period_bars),
            str(self.shape),
            float(self.intensity),
            float(self.phase),
            float(self.arp_local),
            int(self.arp_sync),
        ]


# ---------------------------------------------------------------------------
# Clock
# ---------------------------------------------------------------------------


class BreathClock:
    """One monotonic breath phase shared (by reference) by every scheduler.

    The clock stores an anchor ``(phase, time)`` pair and a period in seconds.
    ``phase_at`` extrapolates from the anchor.  A tempo change re-anchors with
    the current phase, so the breath never jumps; a transport start calls
    :meth:`reset` so every take begins at the bottom of the inhale.

    All methods are thread-safe and accept an explicit ``at`` timestamp for
    tests.
    """

    def __init__(
        self,
        settings: BreathSettings | None = None,
        *,
        bpm: object = rhythm.BPM_DEFAULT,
        meter_num: object = rhythm.METER_NUM_DEFAULT,
        meter_den: object = rhythm.METER_DEN_DEFAULT,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        if settings is None:
            settings = BreathSettings()
        if not isinstance(settings, BreathSettings):
            raise TypeError("settings must be a BreathSettings or None")
        if not callable(now):
            raise TypeError("now must be callable")
        self._now = now
        self._lock = threading.RLock()
        self._settings = settings
        self._bpm = rhythm.clamp_bpm(bpm)
        self._meter_num = rhythm.clamp_meter_numerator(meter_num)
        self._meter_den = rhythm.validate_meter_denominator(meter_den)
        self._period = self._period_for(
            self._bpm, self._meter_num, self._meter_den,
            settings.period_bars,
        )
        self._anchor_phase = float(settings.phase)
        self._epoch = float(now())

    # -- queries --------------------------------------------------------

    @property
    def settings(self) -> BreathSettings:
        with self._lock:
            return self._settings

    @property
    def bpm(self) -> float:
        with self._lock:
            return self._bpm

    @property
    def meter(self) -> str:
        with self._lock:
            return f"{self._meter_num}/{self._meter_den}"

    @property
    def period_seconds(self) -> float:
        with self._lock:
            return self._period

    # -- internals ------------------------------------------------------

    @staticmethod
    def _period_for(
        bpm: float, meter_num: int, meter_den: int, period_bars: int
    ) -> float:
        bar = rhythm.bar_seconds(bpm, meter_num, meter_den)
        return max(1e-6, float(period_bars) * float(bar))

    def _time(self, at: float | None) -> float:
        return float(self._now() if at is None else at)

    # -- mutations ------------------------------------------------------

    def set_settings(self, settings: BreathSettings) -> BreathSettings:
        """Install new settings without jumping, unless the phase changed.

        A changed ``phase`` is a deliberate rotation: the breath moves to the
        new phase immediately.  Any other change (depth, period, shape,
        intensity, arp_local, enabled) preserves the running phase.
        """

        if not isinstance(settings, BreathSettings):
            raise TypeError("settings must be a BreathSettings")
        with self._lock:
            now = self._time(None)
            if settings.phase != self._settings.phase:
                self._anchor_phase = float(settings.phase)
            else:
                self._anchor_phase = self.phase_at(now)
            self._epoch = now
            self._settings = settings
            self._period = self._period_for(
                self._bpm, self._meter_num, self._meter_den,
                settings.period_bars,
            )
            return settings

    def set_tempo(
        self, bpm: object, meter_num: object, meter_den: object
    ) -> None:
        """Change tempo/meter, keeping the breath phase continuous."""

        value = rhythm.clamp_bpm(bpm)
        numerator = rhythm.clamp_meter_numerator(meter_num)
        denominator = rhythm.validate_meter_denominator(meter_den)
        with self._lock:
            now = self._time(None)
            self._anchor_phase = self.phase_at(now)
            self._epoch = now
            self._bpm = value
            self._meter_num = numerator
            self._meter_den = denominator
            self._period = self._period_for(
                value, numerator, denominator, self._settings.period_bars
            )

    def reset(self, at: float | None = None) -> None:
        """Restart the cycle at ``settings.phase`` (a transport start)."""

        with self._lock:
            self._anchor_phase = float(self._settings.phase)
            self._epoch = self._time(at)

    # -- the voice API --------------------------------------------------

    def phase_at(self, at: float | None = None) -> float:
        with self._lock:
            at = self._time(at)
            if self._period <= 0.0:  # pragma: no cover - period is clamped
                return float(self._settings.phase)
            return (
                self._anchor_phase + (at - self._epoch) / self._period
            ) % 1.0

    def _voice_depth_shift(self, voice: str) -> tuple[float, float]:
        name = str(voice).strip().lower()
        if name not in BREATH_VOICES:
            raise ValueError(
                "voice must be one of " + ", ".join(BREATH_VOICES)
            )
        settings = self._settings
        if name == "arp":
            # Stage 8.7.1: ``arp_sync`` uses the master phase so the arp
            # breathes with the groove.  The default keeps its own half-cycle
            # phase (breathing against the groove); ``arp_local`` is an extra
            # depth in both modes.
            shift = 0.0 if settings.arp_sync else 0.5
            return (
                min(1.0, settings.depth + settings.arp_local),
                shift,
            )
        return settings.depth, 0.0

    def _voice_phase(self, voice: str, at: float | None) -> tuple[float, float, BreathSettings]:
        with self._lock:
            settings = self._settings
            depth, shift = self._voice_depth_shift(voice)
            phase = (self.phase_at(at) + shift) % 1.0
            return depth, phase, settings

    def offset_seconds(self, voice: str, at: float | None = None) -> float:
        """Timing offset in seconds for ``voice`` (0 when disabled/depth 0)."""

        depth, phase, settings = self._voice_phase(voice, at)
        if not settings.enabled or depth <= 0.0:
            return 0.0
        steps = breath_offset_steps(settings.shape, phase, depth)
        if steps == 0.0:
            return 0.0
        return steps * (60.0 / self._bpm / 4.0)

    def level(self, voice: str, at: float | None = None) -> float:
        """The volume ``0..1`` for ``voice``; a flat 0.5 when disabled.

        Depth is the *timing* amount and intensity is the velocity swell, so
        the level does not depend on depth: breath on with depth 0 is a pure
        velocity/expression breath.
        """

        _depth, phase, settings = self._voice_phase(voice, at)
        if not settings.enabled:
            return 0.5
        return breath_volume(settings.shape, phase)

    def velocity_delta(self, voice: str, at: float | None = None) -> int:
        """Velocity/expression delta in MIDI units (0 at level 0.5 or off)."""

        _, _, settings = self._voice_phase(voice, at)
        if not settings.enabled:
            return 0
        level = self.level(voice, at)
        return int(round(
            settings.intensity * BREATH_VELOCITY_SWING * (2.0 * level - 1.0)
        ))


__all__ = [
    "BREATH_DEFAULT_ARP_LOCAL",
    "BREATH_DEFAULT_ARP_SYNC",
    "BREATH_DEFAULT_DEPTH",
    "BREATH_DEFAULT_INTENSITY",
    "BREATH_DEFAULT_PERIOD_BARS",
    "BREATH_DEFAULT_PHASE",
    "BREATH_DEFAULT_SHAPE",
    "BREATH_DEPTH_MAX",
    "BREATH_MAX_SHIFT_STEPS",
    "BREATH_OFFSET_LIMIT",
    "BREATH_PERIOD_BARS_MAX",
    "BREATH_PERIOD_BARS_MIN",
    "BREATH_SHAPES",
    "BREATH_VELOCITY_SWING",
    "BREATH_VOICES",
    "BreathClock",
    "BreathSettings",
    "breath_offset_steps",
    "breath_slope",
    "breath_volume",
    "clamp_unit",
    "validate_bool",
    "validate_period_bars",
    "validate_shape",
]

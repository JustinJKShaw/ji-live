"""Stage 8.3a monophonic JI bass voice.

The bass is plain single-channel MIDI (not an MPE zone): one output port with
a fixed main channel and an optional sub channel one or two octaves below.  It
is transport-synced by :class:`BassScheduler`, which uses monotonic deadlines
and short gates like the Stage 8.1 drum scheduler.

Layers (mirroring ``control/drums.py``):

* :class:`BassConfig` -- validated ``config/bass_config.json``.
* :class:`BassSettings` -- the mutable-by-replace pattern/pitch controls.
* :class:`BassPattern` -- the deterministic trigger grid for one pattern loop.
* :class:`BassPitchPicker` -- seeded ratio/octave selection from a set snapshot.
* :class:`BassOutput` -- one lazy ``mido`` port, main and sub channels.
* :class:`BassScheduler` -- daemon thread, drift-corrected timing, gate timers.
* :class:`BassRig` -- output + engine + scheduler behind one small API.

No ``mido`` import happens at import time or in any constructor.  A fake output
object can be injected for the pure tests.  The module is pure of OSC, PyQt6,
and audio; it imports only the standard library and the pure
:mod:`control.rhythm` / :mod:`control.mpe_output` helpers.

Timing and pitch rules are documented in ``docs/README_stage8_3.md`` and
design doc §6.16.
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

try:  # Support both ``python -m control.bass`` and direct execution.
    from . import rhythm
    from .mpe_output import MPEMessage, frequency_to_pitch, midi_note_frequency
except ImportError:  # pragma: no cover - exercised only by direct execution
    import rhythm  # type: ignore[no-redef]
    from mpe_output import (  # type: ignore[no-redef]
        MPEMessage,
        frequency_to_pitch,
        midi_note_frequency,
    )

# ---------------------------------------------------------------------------
# Constants / defaults
# ---------------------------------------------------------------------------

DEFAULT_CONFIG_PATH = (
    Path(__file__).resolve().parent.parent / "config" / "bass_config.json"
)

DEFAULT_PORT = "IAC Driver Bus 4"
DEFAULT_CHANNEL = 2
DEFAULT_SUB_CHANNEL = 3
DEFAULT_BEND_RANGE_SEMITONES = 2.0
DEFAULT_VELOCITY = 90
DEFAULT_SUB_VELOCITY = 80

GRIDS: tuple[str, ...] = ("1/4", "1/8", "1/16")
GRID_STEPS: dict[str, int] = {"1/4": 1, "1/8": 2, "1/16": 4}
DEFAULT_GRID = "1/4"

PRESETS: tuple[str, ...] = ("pulse", "techno", "chaos")
PRESET_CUSTOM = "custom"
DEFAULT_PRESET = "pulse"

PITCH_MODES: tuple[str, ...] = ("root", "anchor", "walk", "random", "cycle")
DEFAULT_PITCH_MODE = "root"

LENGTH_MIN = 1
LENGTH_MAX = 32
GATE_BEATS_MIN = 0.05
GATE_BEATS_MAX = 8.0
OCTAVE_RANGE_MIN = 0
OCTAVE_RANGE_MAX = 3
BASE_OCTAVE_MIN = -3
BASE_OCTAVE_MAX = 0
SUB_OCTAVES: tuple[int, ...] = (1, 2)
SEED_MAX = 2**31 - 1
DEFAULT_SEED = 1

ACCENT_MAX_BOOST = 30
_FUNDAMENTAL_DEFAULT = 220.0

# Preset values (documented in docs/README_stage8_3.md §4.1).
# Enable/seed/sub/length are intentionally not part of a preset.
PRESET_VALUES: dict[str, dict[str, Any]] = {
    "pulse": {
        "grid": "1/4",
        "density": 0.0,
        "syncopation": 0.0,
        "swing": 0.0,
        "gate_beats": 3.5,
        "accent": 0.0,
        "root_downbeat": True,
        "pitch_mode": "root",
        "movement": 0.0,
        "octave_range": 0,
        "octave_jump": 0.0,
        "base_octave": -2,
    },
    "techno": {
        "grid": "1/16",
        "density": 0.55,
        "syncopation": 0.15,
        "swing": 0.10,
        "gate_beats": 0.12,
        "accent": 0.25,
        "root_downbeat": True,
        "pitch_mode": "walk",
        "movement": 0.45,
        "octave_range": 1,
        "octave_jump": 0.05,
        "base_octave": -2,
    },
    "chaos": {
        "grid": "1/16",
        "density": 0.80,
        "syncopation": 0.35,
        "swing": 0.15,
        "gate_beats": 0.08,
        "accent": 0.35,
        "root_downbeat": True,
        "pitch_mode": "random",
        "movement": 0.90,
        "octave_range": 2,
        "octave_jump": 0.35,
        "base_octave": -2,
    },
}

_MASK64 = (1 << 64) - 1


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def _finite(value: object, label: str) -> float:
    try:
        numeric = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise TypeError(f"{label} must be a real number") from None
    if not math.isfinite(numeric):
        raise ValueError(f"{label} must be finite")
    return numeric


def clamp_unit(value: object, label: str = "value") -> float:
    """Return a finite float clamped to ``0.0..1.0``."""

    return max(0.0, min(1.0, _finite(value, label)))


def validate_bool(value: object, label: str = "value") -> bool:
    """Accept a bool or 0/1 (OSC-style); otherwise raise ``TypeError``."""

    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, float) and value in (0.0, 1.0):
        return bool(value)
    raise TypeError(f"{label} must be a boolean or 0/1")


def validate_grid(value: object) -> str:
    """Return one of ``1/4``, ``1/8``, ``1/16`` or raise ``ValueError``."""

    text = str(value).strip()
    if text not in GRID_STEPS:
        raise ValueError(f"grid must be one of {', '.join(GRIDS)}")
    return text


def grid_steps(value: object) -> int:
    """Steps per quarter note for a grid name (1, 2, or 4)."""

    return GRID_STEPS[validate_grid(value)]


def validate_preset(value: object) -> str:
    """Return a selectable preset name or raise ``ValueError``."""

    name = str(value).strip().lower()
    if name not in PRESETS:
        raise ValueError("preset must be one of " + ", ".join(PRESETS))
    return name


def validate_pitch_mode(value: object) -> str:
    """Return one of the documented pitch modes."""

    mode = str(value).strip().lower()
    if mode not in PITCH_MODES:
        raise ValueError("pitch_mode must be one of " + ", ".join(PITCH_MODES))
    return mode


def validate_length(value: object) -> int | None:
    """Return ``None`` (auto bar length) or an integer 1..32."""

    if value is None:
        return None
    if isinstance(value, str) and value.strip().lower() in ("", "auto"):
        return None
    number = int(round(_finite(value, "length")))
    if not LENGTH_MIN <= number <= LENGTH_MAX:
        raise ValueError(f"length must be {LENGTH_MIN}..{LENGTH_MAX} steps")
    return number


def validate_gate_beats(value: object) -> float:
    """Return gate length in beats, clamped to 0.05..8.0."""

    return max(GATE_BEATS_MIN, min(GATE_BEATS_MAX, _finite(value, "gate_beats")))


def validate_octave_range(value: object) -> int:
    """Return the octave range as an integer 0..3."""

    number = int(round(_finite(value, "octave_range")))
    if not OCTAVE_RANGE_MIN <= number <= OCTAVE_RANGE_MAX:
        raise ValueError(
            f"octave_range must be {OCTAVE_RANGE_MIN}..{OCTAVE_RANGE_MAX}"
        )
    return number


def validate_base_octave(value: object) -> int:
    """Return the base octave as an integer -3..0."""

    number = int(round(_finite(value, "base_octave")))
    if not BASE_OCTAVE_MIN <= number <= BASE_OCTAVE_MAX:
        raise ValueError(
            f"base_octave must be {BASE_OCTAVE_MIN}..{BASE_OCTAVE_MAX}"
        )
    return number


def validate_octave_jump(value: object) -> float:
    """Return the octave-jump probability clamped to 0..1."""

    return clamp_unit(value, "octave_jump")


def validate_seed(value: object) -> int:
    """Return an integer seed clamped to 0..2**31-1."""

    number = int(round(_finite(value, "seed")))
    return max(0, min(SEED_MAX, number))


def validate_sub_octaves(value: object) -> int:
    """Return 1 or 2 sub octaves."""

    number = int(round(_finite(value, "sub_octaves")))
    if number not in SUB_OCTAVES:
        raise ValueError("sub_octaves must be 1 or 2")
    return number


def validate_channel(value: object, label: str = "channel") -> int:
    """Return a 1-based MIDI channel 1..16."""

    number = int(round(_finite(value, label)))
    if not 1 <= number <= 16:
        raise ValueError(f"{label} must be a 1-based MIDI channel in 1..16")
    return number


def validate_velocity(value: object, label: str = "velocity") -> int:
    """Return a MIDI velocity 1..127."""

    number = int(round(_finite(value, label)))
    if not 1 <= number <= 127:
        raise ValueError(f"{label} must be between 1 and 127")
    return number


def _nonempty_str(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TypeError(f"{label} must be a non-empty string")
    return value


# ---------------------------------------------------------------------------
# Settings / config
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BassSettings:
    """Validated bass pattern/pitch settings.

    Presets replace the rhythm/pitch/articulation fields and reset the pattern
    length to one bar; ``enabled``, ``seed``, and ``sub_*`` are user/output
    choices and survive a preset change.  Any other manual edit marks the preset
    ``custom``.
    """

    enabled: bool = False
    preset: str = DEFAULT_PRESET
    grid: str = DEFAULT_GRID
    length_steps: int | None = None
    density: float = 0.0
    syncopation: float = 0.0
    swing: float = 0.0
    gate_beats: float = 3.5
    accent: float = 0.0
    root_downbeat: bool = True
    pitch_mode: str = DEFAULT_PITCH_MODE
    movement: float = 0.0
    octave_range: int = 0
    octave_jump: float = 0.0
    base_octave: int = -2
    seed: int = DEFAULT_SEED
    sub_enabled: bool = False
    sub_octaves: int = 1

    @classmethod
    def create(cls, **values: Any) -> "BassSettings":
        """Build settings with every value validated/normalised."""

        defaults = cls()
        enabled = validate_bool(values.get("enabled", defaults.enabled), "enabled")
        preset = values.get("preset", defaults.preset)
        preset = (
            DEFAULT_PRESET
            if str(preset).strip().lower() == PRESET_CUSTOM
            else validate_preset(preset)
        )
        grid = validate_grid(values.get("grid", defaults.grid))
        length_value = values.get(
            "length_steps", values.get("length", defaults.length_steps)
        )
        length = validate_length(length_value)
        density = clamp_unit(values.get("density", defaults.density), "density")
        syncopation = clamp_unit(
            values.get("syncopation", defaults.syncopation), "syncopation"
        )
        swing = clamp_unit(values.get("swing", defaults.swing), "swing")
        gate = validate_gate_beats(values.get("gate_beats", defaults.gate_beats))
        accent = clamp_unit(values.get("accent", defaults.accent), "accent")
        root_downbeat = validate_bool(
            values.get("root_downbeat", defaults.root_downbeat), "root_downbeat"
        )
        pitch_mode = validate_pitch_mode(
            values.get("pitch_mode", defaults.pitch_mode)
        )
        movement = clamp_unit(
            values.get("movement", defaults.movement), "movement"
        )
        octave_range = validate_octave_range(
            values.get("octave_range", defaults.octave_range)
        )
        octave_jump = validate_octave_jump(
            values.get("octave_jump", defaults.octave_jump)
        )
        base_octave = validate_base_octave(
            values.get("base_octave", defaults.base_octave)
        )
        seed = validate_seed(values.get("seed", defaults.seed))
        sub_enabled = validate_bool(
            values.get("sub_enabled", defaults.sub_enabled), "sub_enabled"
        )
        sub_octaves = validate_sub_octaves(
            values.get("sub_octaves", defaults.sub_octaves)
        )
        return cls(
            enabled=enabled,
            preset=preset,
            grid=grid,
            length_steps=length,
            density=density,
            syncopation=syncopation,
            swing=swing,
            gate_beats=gate,
            accent=accent,
            root_downbeat=root_downbeat,
            pitch_mode=pitch_mode,
            movement=movement,
            octave_range=octave_range,
            octave_jump=octave_jump,
            base_octave=base_octave,
            seed=seed,
            sub_enabled=sub_enabled,
            sub_octaves=sub_octaves,
        )

    @property
    def steps_per_beat(self) -> int:
        return grid_steps(self.grid)

    @property
    def step_beats(self) -> float:
        return 1.0 / self.steps_per_beat

    def with_preset(self, preset: object) -> "BassSettings":
        """Apply a preset, preserving enable/seed/sub choices.

        The pattern length returns to the automatic one-bar value; manual
        rhythm/pitch/articulation edits are replaced (the documented behavior).
        """

        name = validate_preset(preset)
        values = dict(PRESET_VALUES[name])
        values["length_steps"] = None
        return replace(self, preset=name, **values)

    def with_change(self, name: str, value: object) -> "BassSettings":
        """Return settings with one validated manual edit applied."""

        key = str(name).strip().lower()
        if key == "preset":
            return self.with_preset(value)
        if key == "length":
            key = "length_steps"
        if key in ("enabled", "sub_enabled"):
            return replace(self, **{key: validate_bool(value, key)})
        if key == "sub_octaves":
            return replace(self, sub_octaves=validate_sub_octaves(value))
        validators = {
            "grid": validate_grid,
            "length_steps": validate_length,
            "density": lambda item: clamp_unit(item, "density"),
            "syncopation": lambda item: clamp_unit(item, "syncopation"),
            "swing": lambda item: clamp_unit(item, "swing"),
            "gate_beats": validate_gate_beats,
            "accent": lambda item: clamp_unit(item, "accent"),
            "root_downbeat": lambda item: validate_bool(item, "root_downbeat"),
            "pitch_mode": validate_pitch_mode,
            "movement": lambda item: clamp_unit(item, "movement"),
            "octave_range": validate_octave_range,
            "octave_jump": validate_octave_jump,
            "base_octave": validate_base_octave,
            "seed": validate_seed,
        }
        if key not in validators:
            raise ValueError(
                "unknown bass setting "
                f"{name!r}; expected preset, grid, length_steps, density, "
                "syncopation, swing, gate_beats, accent, root_downbeat, "
                "pitch_mode, movement, octave_range, octave_jump, "
                "base_octave, seed, sub_enabled, sub_octaves, enabled"
            )
        return replace(self, preset=PRESET_CUSTOM, **{key: validators[key](value)})


@dataclass(frozen=True, slots=True)
class BassConfig:
    """Validated ``config/bass_config.json``: output plus default settings."""

    port: str = DEFAULT_PORT
    channel: int = DEFAULT_CHANNEL
    sub_channel: int = DEFAULT_SUB_CHANNEL
    bend_range_semitones: float = DEFAULT_BEND_RANGE_SEMITONES
    velocity: int = DEFAULT_VELOCITY
    sub_velocity: int = DEFAULT_SUB_VELOCITY
    settings: BassSettings = BassSettings()

    def __post_init__(self) -> None:
        if not isinstance(self.port, str) or not self.port.strip():
            raise ValueError("port must be a non-empty string")
        if not isinstance(self.settings, BassSettings):
            raise TypeError("settings must be a BassSettings")
        if not 1 <= self.channel <= 16:
            raise ValueError("channel must be 1..16")
        if not 1 <= self.sub_channel <= 16:
            raise ValueError("sub_channel must be 1..16")
        if self.channel == self.sub_channel:
            raise ValueError("sub_channel must differ from channel")
        if not math.isfinite(self.bend_range_semitones) or self.bend_range_semitones <= 0:
            raise ValueError("bend_range_semitones must be finite and positive")
        if not 1 <= self.velocity <= 127:
            raise ValueError("velocity must be 1..127")
        if not 1 <= self.sub_velocity <= 127:
            raise ValueError("sub_velocity must be 1..127")

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "BassConfig":
        if not isinstance(data, Mapping):
            raise TypeError("bass config must be a mapping")

        preset = validate_preset(data.get("preset", DEFAULT_PRESET))
        # A preset supplies the base values; explicit keys override them, so a
        # short ``{"preset": "techno"}`` config behaves as expected while the
        # shipped full config stays authoritative.
        values: dict[str, Any] = dict(PRESET_VALUES[preset])
        values["preset"] = preset
        for key in (
            "enabled",
            "grid",
            "length_steps",
            "length",
            "density",
            "syncopation",
            "swing",
            "gate_beats",
            "accent",
            "root_downbeat",
            "pitch_mode",
            "movement",
            "octave_range",
            "octave_jump",
            "base_octave",
            "seed",
            "sub_enabled",
            "sub_octaves",
        ):
            if key in data:
                values[key] = data[key]
        if "length" in data and "length_steps" not in data:
            values["length_steps"] = data["length"]
        settings = BassSettings.create(**values)

        return cls(
            port=str(data.get("port", DEFAULT_PORT)),
            channel=validate_channel(data.get("channel", DEFAULT_CHANNEL)),
            sub_channel=validate_channel(
                data.get("sub_channel", DEFAULT_SUB_CHANNEL), "sub_channel"
            ),
            bend_range_semitones=_finite(
                data.get("bend_range_semitones", DEFAULT_BEND_RANGE_SEMITONES),
                "bend_range_semitones",
            ),
            velocity=validate_velocity(data.get("velocity", DEFAULT_VELOCITY)),
            sub_velocity=validate_velocity(
                data.get("sub_velocity", DEFAULT_SUB_VELOCITY), "sub_velocity"
            ),
            settings=settings,
        )

    @classmethod
    def from_json(cls, path: str | Path) -> "BassConfig":
        return load_config(path)


def load_config(path: str | Path = DEFAULT_CONFIG_PATH) -> BassConfig:
    """Load and validate the bass config (same path fallback as the others)."""

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
        raise FileNotFoundError(f"bass config not found: {candidate}") from None

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in {candidate}: {exc}") from exc
    return BassConfig.from_mapping(data)


# ---------------------------------------------------------------------------
# Pattern generation
# ---------------------------------------------------------------------------


def resolve_length(
    settings: BassSettings,
    meter_num: object = 4,
    meter_den: object = 4,
) -> int:
    """Pattern length in grid steps.

    A custom ``length_steps`` survives meter changes; the default is one bar:
    ``bar_quarters * steps_per_beat``, capped at :data:`LENGTH_MAX`.
    """

    if settings.length_steps is not None:
        return max(LENGTH_MIN, min(LENGTH_MAX, int(settings.length_steps)))
    numerator = rhythm.clamp_meter_numerator(meter_num)
    denominator = rhythm.validate_meter_denominator(meter_den)
    bar = rhythm.bar_quarters(numerator, denominator)
    return max(
        LENGTH_MIN,
        min(LENGTH_MAX, int(round(bar * settings.steps_per_beat))),
    )


def swing_offset(
    step: object,
    swing: object,
    step_seconds: object,
    steps_per_beat: object = 4,
) -> float:
    """Seconds to delay one grid step for swing.

    Odd 8th/16th grid steps are delayed by ``swing * 0.5 * step_seconds``.
    A 1/4 grid is straight (there is no odd 8th/16th to swing).  Swing 0 is
    always straight; swing 1 is half a grid step late.  There is no triplet
    subdivision.
    """

    index = int(round(float(step)))
    amount = clamp_unit(swing, "swing")
    length = _finite(step_seconds, "step_seconds")
    if length <= 0.0:
        raise ValueError("step_seconds must be strictly positive")
    subdivision = int(round(_finite(steps_per_beat, "steps_per_beat")))
    if subdivision < 2 or index % 2 == 0:
        return 0.0
    return amount * 0.5 * length


@dataclass(frozen=True, slots=True)
class BassPattern:
    """One deterministic trigger loop (``triggers[step]`` is a bool)."""

    triggers: tuple[bool, ...]
    length_steps: int
    steps_per_beat: int
    bar_index: int
    meter_num: int
    meter_den: int

    def triggers_at(self, step: int) -> bool:
        if not self.triggers:
            return False
        return bool(self.triggers[int(step) % len(self.triggers)])

    def steps(self) -> tuple[int, ...]:
        return tuple(
            step for step, on in enumerate(self.triggers) if on
        )


def _mix64(value: int) -> int:
    """SplitMix64 finaliser (stable across processes; matches the groove)."""

    value = (value + 0x9E3779B97F4A7C15) & _MASK64
    value = ((value ^ (value >> 30)) * 0xBF58476D1CE4E5B9) & _MASK64
    value = ((value ^ (value >> 27)) * 0x94D049BB133111EB) & _MASK64
    return (value ^ (value >> 31)) & _MASK64


def _salt_value(salt: str) -> int:
    value = 0xCBF29CE484222325
    for byte in salt.encode("utf-8"):
        value = ((value ^ byte) * 0x100000001B3) & _MASK64
    return value


def _bar_rng(seed: int, bar_index: int, salt: str) -> random.Random:
    key = _mix64(validate_seed(seed))
    key = _mix64(key ^ (int(bar_index) & _MASK64))
    key = _mix64(key ^ _salt_value(salt))
    return random.Random(key)


def build_pattern(
    settings: BassSettings,
    meter_num: object = 4,
    meter_den: object = 4,
    bar_index: int = 0,
) -> BassPattern:
    """Build one deterministic trigger loop.

    Rules:

    * Step 0 always triggers.  It is the bar downbeat and, when
      ``root_downbeat`` is on, also the root pitch anchor.
    * Every other step triggers with probability ``density``.
    * ``syncopation`` adds an anticipation at ``step - 1`` for a seeded subset
      of triggered steps ``>= 2`` when that cell is empty.  It never moves or
      removes step 0, and step 1 has no anticipation (its predecessor is the
      downbeat).  The original hit stays.
    * The same preset/modifiers/seed/bar produce the same loop; no ambient
      ``random`` state is used.
    """

    if not isinstance(settings, BassSettings):
        raise TypeError("settings must be a BassSettings")
    numerator = rhythm.clamp_meter_numerator(meter_num)
    denominator = rhythm.validate_meter_denominator(meter_den)
    length = resolve_length(settings, numerator, denominator)
    spb = settings.steps_per_beat
    bar = max(0, int(bar_index))

    rng = _bar_rng(settings.seed, bar, "bass-triggers")
    base = [False] * length
    base[0] = True
    for step in range(1, length):
        if rng.random() < settings.density:
            base[step] = True

    triggers = list(base)
    if settings.syncopation > 0.0:
        sync_rng = _bar_rng(settings.seed, bar, "bass-sync")
        for step in range(2, length):
            if not base[step] or triggers[step - 1]:
                continue
            if sync_rng.random() < settings.syncopation:
                triggers[step - 1] = True

    return BassPattern(
        triggers=tuple(triggers),
        length_steps=length,
        steps_per_beat=spb,
        bar_index=bar,
        meter_num=numerator,
        meter_den=denominator,
    )


# ---------------------------------------------------------------------------
# Pitch selection
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BassNote:
    """One selected bass pitch for a trigger."""

    ratio: float
    octave_offset: int
    frequency: float
    step: int
    bar_index: int
    is_downbeat: bool
    mode: str

    @property
    def octave_total(self) -> int:
        return self.octave_offset

    def sub_frequency(self, sub_octaves: int) -> float:
        return self.frequency / float(2 ** int(sub_octaves))


class BassPitchPicker:
    """Seeded, deterministic ratio/octave selection from one set snapshot.

    The walk/cycle state resets at every pattern loop (bar), so the same set +
    settings + seed + bar + step reproduces the same note.  A set update changes
    the pool for the next trigger; it never retriggers or stops a held note.
    """

    def __init__(
        self,
        settings: BassSettings | None = None,
        ratios: Sequence[float] = (1.0,),
        anchor: float | None = None,
        fundamental: float = _FUNDAMENTAL_DEFAULT,
    ) -> None:
        self._settings = BassSettings.create() if settings is None else settings
        self._ratios: tuple[float, ...] = ()
        self._anchor: float | None = None
        self._fundamental = float(fundamental)
        self._bar_index = -1
        self._pitch_rng = random.Random(0)
        self._octave_rng = random.Random(0)
        self._walk_index = 0
        self._cycle_index = 0
        self._last_ratio: float | None = None
        self._octave_offset = 0
        self._anchor_fallback_logged = False
        self.set_pool(ratios, anchor, fundamental)
        self.set_settings(self._settings)

    # -- configuration --------------------------------------------------

    def set_settings(self, settings: BassSettings) -> None:
        if not isinstance(settings, BassSettings):
            raise TypeError("settings must be a BassSettings")
        seed_changed = settings.seed != self._settings.seed
        self._settings = settings
        if seed_changed:
            self._bar_index = -1
            self._anchor_fallback_logged = False

    def set_pool(
        self,
        ratios: Sequence[float],
        anchor: float | None = None,
        fundamental: float | None = None,
    ) -> None:
        values = sorted(
            float(ratio)
            for ratio in ratios
            if math.isfinite(float(ratio)) and float(ratio) > 0.0
        )
        seen: set[float] = set()
        cleaned: list[float] = []
        for value in values:
            key = round(value, 12)
            if key in seen:
                continue
            seen.add(key)
            cleaned.append(value)
        self._ratios = tuple(cleaned) or (1.0,)
        self._anchor = None if anchor is None else float(anchor)
        if fundamental is not None:
            self._fundamental = float(fundamental)
        self._anchor_fallback_logged = False
        # Re-anchor the walk at the old ratio when it still exists, else at the
        # nearest class in the new pool.
        if self._last_ratio is not None:
            self._walk_index = self._nearest_index(self._last_ratio)

    # -- queries --------------------------------------------------------

    @property
    def ratios(self) -> tuple[float, ...]:
        return self._ratios

    @property
    def anchor(self) -> float | None:
        return self._anchor

    @property
    def fundamental(self) -> float:
        return self._fundamental

    # -- bar / note -----------------------------------------------------

    def reset_bar(self, bar_index: int) -> None:
        bar = max(0, int(bar_index))
        if bar == self._bar_index:
            return
        self._bar_index = bar
        self._pitch_rng = _bar_rng(self._settings.seed, bar, "bass-pitch")
        self._octave_rng = _bar_rng(self._settings.seed, bar, "bass-octave")
        self._walk_index = 0
        self._cycle_index = 0
        self._last_ratio = None
        self._octave_offset = 0

    def next_note(self, step: int, bar_index: int, is_downbeat: bool) -> BassNote:
        """Pick the note for one trigger (the caller has checked timing)."""

        self.reset_bar(bar_index)
        settings = self._settings
        ratios = self._ratios
        mode = settings.pitch_mode

        if is_downbeat and settings.root_downbeat:
            ratio = 1.0
            self._walk_index = self._nearest_index(1.0)
            self._cycle_index = 1 if len(ratios) > 1 else 0
            self._last_ratio = 1.0
            self._octave_offset = 0
            return BassNote(
                ratio=1.0,
                octave_offset=0,
                frequency=self._fundamental
                * (2.0 ** settings.base_octave),
                step=int(step),
                bar_index=int(bar_index),
                is_downbeat=True,
                mode=mode,
            )

        if mode == "root":
            ratio = 1.0
        elif mode == "anchor":
            ratio = self._pick_anchor()
        elif mode == "walk":
            ratio = self._pick_walk()
        elif mode == "random":
            ratio = self._pick_random()
        elif mode == "cycle":
            ratio = self._pick_cycle()
        else:  # pragma: no cover - validation prevents this
            ratio = 1.0

        octave_offset = self._pick_octave_offset(mode, is_downbeat)
        self._last_ratio = ratio
        frequency = (
            self._fundamental
            * ratio
            * (2.0 ** (settings.base_octave + octave_offset))
        )
        return BassNote(
            ratio=ratio,
            octave_offset=octave_offset,
            frequency=frequency,
            step=int(step),
            bar_index=int(bar_index),
            is_downbeat=bool(is_downbeat),
            mode=mode,
        )

    # -- pickers --------------------------------------------------------

    def _nearest_index(self, ratio: float) -> int:
        if not self._ratios:
            return 0
        return min(
            range(len(self._ratios)),
            key=lambda index: abs(math.log2(self._ratios[index] / ratio)),
        )

    def _pick_anchor(self) -> float:
        anchor = self._anchor
        if anchor is not None:
            for ratio in self._ratios:
                if math.isclose(anchor, ratio, rel_tol=1e-9, abs_tol=1e-9):
                    return ratio
        if not self._anchor_fallback_logged:
            print(
                "[BASS] anchor missing from the current set; "
                "falling back to root"
            )
            self._anchor_fallback_logged = True
        return 1.0

    def _pick_walk(self) -> float:
        if len(self._ratios) == 1:
            return self._ratios[0]
        movement = self._settings.movement
        if self._last_ratio is not None and movement <= 0.0:
            return self._ratios[self._walk_index]
        if self._pitch_rng.random() < movement:
            max_step = 1 if movement < 0.6 else 2
            delta = self._pitch_rng.choice((-1, 1)) * self._pitch_rng.randint(
                1, max_step
            )
            self._walk_index = (self._walk_index + delta) % len(self._ratios)
        return self._ratios[self._walk_index]

    def _pick_random(self) -> float:
        movement = self._settings.movement
        if self._last_ratio is not None:
            matching = [
                ratio
                for ratio in self._ratios
                if math.isclose(ratio, self._last_ratio, rel_tol=1e-9)
            ]
            if matching and self._pitch_rng.random() >= movement:
                return matching[0]
        if len(self._ratios) == 1:
            return self._ratios[0]
        candidates = self._ratios
        current = self._last_ratio
        if current is not None and movement >= 1.0:
            filtered = tuple(
                ratio
                for ratio in self._ratios
                if not math.isclose(ratio, current, rel_tol=1e-9)
            )
            if filtered:
                candidates = filtered
        return candidates[self._pitch_rng.randrange(len(candidates))]

    def _pick_cycle(self) -> float:
        ratio = self._ratios[self._cycle_index % len(self._ratios)]
        self._cycle_index = (self._cycle_index + 1) % len(self._ratios)
        return ratio

    def _pick_octave_offset(self, mode: str, is_downbeat: bool) -> int:
        settings = self._settings
        if mode == "root" or (is_downbeat and settings.root_downbeat):
            return 0
        span = settings.octave_range
        if span <= 0:
            return 0
        if self._octave_rng.random() < settings.octave_jump:
            self._octave_offset = self._octave_rng.randint(-span, span)
        return max(-span, min(span, self._octave_offset))


# ---------------------------------------------------------------------------
# MIDI output
# ---------------------------------------------------------------------------


class BassOutput:
    """One plain MIDI output port that can address a main and a sub channel."""

    def __init__(
        self,
        port_name: str = DEFAULT_PORT,
        channel: int = DEFAULT_CHANNEL,
        sub_channel: int = DEFAULT_SUB_CHANNEL,
        *,
        velocity: int = DEFAULT_VELOCITY,
        sub_velocity: int = DEFAULT_SUB_VELOCITY,
        output: Any | None = None,
        label: str = "bass",
    ) -> None:
        self.port_name = _nonempty_str(port_name, "port_name")
        self.channel = validate_channel(channel)
        self.sub_channel = validate_channel(sub_channel, "sub_channel")
        if self.channel == self.sub_channel:
            raise ValueError("sub_channel must differ from channel")
        self.velocity = validate_velocity(velocity)
        self.sub_velocity = validate_velocity(sub_velocity, "sub_velocity")
        self.label = label

        self._output = output
        self._opened = output is not None
        self._mido: Any | None = None

    # -- lifecycle ------------------------------------------------------

    def open(self) -> None:
        """Open the real mido port, or no-op with an injected fake output."""

        if self._opened:
            return
        try:
            import mido  # intentionally lazy
        except ImportError as exc:  # pragma: no cover - hardware path
            raise RuntimeError(
                "mido is required for bass MIDI output. Run with: "
                "uv run --with mido --with python-rtmidi python ..."
            ) from exc
        try:
            names = list(mido.get_output_names())
        except Exception as exc:  # pragma: no cover - hardware path
            raise RuntimeError(
                "could not enumerate MIDI outputs; is python-rtmidi installed? "
                f"({exc})"
            ) from exc
        if self.port_name not in names:
            available = ", ".join(repr(name) for name in names) or "(none)"
            raise RuntimeError(
                f"{self.label} MIDI output {self.port_name!r} not found. "
                f"Available MIDI outputs: {available}. "
                "Check Audio MIDI Setup -> IAC Driver and config/bass_config.json."
            )
        try:
            port = mido.open_output(self.port_name)
        except Exception as exc:  # pragma: no cover - hardware path
            raise RuntimeError(
                f"could not open {self.label} MIDI output "
                f"{self.port_name!r}: {exc}"
            ) from exc
        self._mido = mido
        self._output = _MidoBassAdapter(mido, port)
        self._opened = True

    def close(self) -> None:
        output = self._output
        self._output = None
        self._opened = False
        if output is not None:
            close = getattr(output, "close", None)
            if callable(close):
                close()

    @property
    def is_open(self) -> bool:
        return self._opened

    # -- messages (0-based channel in the message object) ---------------

    def pitch_bend(self, channel: int, bend: int) -> None:
        self._send(
            MPEMessage(
                type="pitchwheel",
                channel=self._index(channel),
                pitch=int(bend),
            )
        )

    def note_on(self, channel: int, note: int, velocity: int) -> None:
        self._send(
            MPEMessage(
                type="note_on",
                channel=self._index(channel),
                note=int(note),
                velocity=validate_velocity(velocity),
            )
        )

    def note_off(self, channel: int, note: int) -> None:
        self._send(
            MPEMessage(
                type="note_off",
                channel=self._index(channel),
                note=int(note),
                velocity=0,
            )
        )

    def control_change(self, channel: int, control: int, value: int) -> None:
        self._send(
            MPEMessage(
                type="control_change",
                channel=self._index(channel),
                control=int(control),
                value=int(value),
            )
        )

    def all_notes_off(self, channel: int) -> None:
        self.control_change(channel, 123, 0)

    def _index(self, channel: int) -> int:
        return validate_channel(channel) - 1

    def _send(self, message: MPEMessage) -> None:
        if not self._opened or self._output is None:
            raise RuntimeError(
                f"{self.label} MIDI output {self.port_name!r} is not open"
            )
        self._output.send(message)


class _MidoBassAdapter:
    """Translate :class:`MPEMessage` into ``mido.Message`` for a real port."""

    def __init__(self, mido_module: Any, port: Any) -> None:
        self._mido = mido_module
        self._port = port

    def send(self, message: MPEMessage) -> None:
        if message.type == "pitchwheel":
            outgoing = self._mido.Message(
                "pitchwheel", channel=message.channel, pitch=message.pitch
            )
        elif message.type in ("note_on", "note_off"):
            outgoing = self._mido.Message(
                message.type,
                channel=message.channel,
                note=message.note,
                velocity=message.velocity,
            )
        elif message.type == "control_change":
            outgoing = self._mido.Message(
                "control_change",
                channel=message.channel,
                control=message.control,
                value=message.value,
            )
        else:  # pragma: no cover
            raise ValueError(f"unsupported bass MIDI message {message.type!r}")
        self._port.send(outgoing)

    def close(self) -> None:
        self._port.close()


# ---------------------------------------------------------------------------
# Engine (settings + pattern + pitch state)
# ---------------------------------------------------------------------------


class BassEngine:
    """Thread-safe bass state: settings, meter, set snapshot, and picker."""

    def __init__(
        self,
        config: BassConfig,
        *,
        meter_num: object = 4,
        meter_den: object = 4,
    ) -> None:
        if not isinstance(config, BassConfig):
            raise TypeError("config must be a BassConfig")
        self.config = config
        self._lock = threading.RLock()
        self._settings = config.settings
        self._meter_num = rhythm.clamp_meter_numerator(meter_num)
        self._meter_den = rhythm.validate_meter_denominator(meter_den)
        self._ratios: tuple[float, ...] = (1.0,)
        self._anchor: float | None = None
        self._fundamental = _FUNDAMENTAL_DEFAULT
        self._version = 0
        self._pattern_cache: tuple[tuple[int, int], BassPattern] | None = None
        self._picker = BassPitchPicker(
            self._settings, self._ratios, self._anchor, self._fundamental
        )

    # -- queries --------------------------------------------------------

    @property
    def version(self) -> int:
        with self._lock:
            return self._version

    @property
    def settings(self) -> BassSettings:
        with self._lock:
            return self._settings

    @property
    def enabled(self) -> bool:
        with self._lock:
            return self._settings.enabled

    @property
    def meter(self) -> str:
        with self._lock:
            return f"{self._meter_num}/{self._meter_den}"

    @property
    def meter_num(self) -> int:
        with self._lock:
            return self._meter_num

    @property
    def meter_den(self) -> int:
        with self._lock:
            return self._meter_den

    @property
    def length_steps(self) -> int:
        with self._lock:
            return resolve_length(
                self._settings, self._meter_num, self._meter_den
            )

    @property
    def ratios(self) -> tuple[float, ...]:
        with self._lock:
            return self._ratios

    @property
    def anchor(self) -> float | None:
        with self._lock:
            return self._anchor

    @property
    def fundamental(self) -> float:
        with self._lock:
            return self._fundamental

    def pattern(self, bar_index: int = 0) -> BassPattern:
        with self._lock:
            return self._pattern_locked(bar_index)

    def _pattern_locked(self, bar_index: int) -> BassPattern:
        key = (int(bar_index), self._version)
        if self._pattern_cache is not None and self._pattern_cache[0] == key:
            return self._pattern_cache[1]
        pattern = build_pattern(
            self._settings, self._meter_num, self._meter_den, bar_index
        )
        self._pattern_cache = (key, pattern)
        return pattern

    def timing(self) -> tuple[BassSettings, int]:
        """``(settings, length_steps)`` for the scheduler's next step."""

        with self._lock:
            return self._settings, resolve_length(
                self._settings, self._meter_num, self._meter_den
            )

    def note_at(self, step: int, bar_index: int) -> BassNote | None:
        """Return the note for a triggering step, or ``None``."""

        with self._lock:
            pattern = self._pattern_locked(bar_index)
            if not pattern.triggers_at(step):
                return None
            downbeat = (int(step) % pattern.length_steps) == 0
            return self._picker.next_note(step, bar_index, downbeat)

    def velocity_for(self, is_downbeat: bool) -> int:
        with self._lock:
            settings = self._settings
        base = self.config.velocity
        boost = (
            int(round(settings.accent * ACCENT_MAX_BOOST))
            if is_downbeat
            else 0
        )
        return max(1, min(127, base + boost))

    def sub_velocity_for(self, is_downbeat: bool) -> int:
        with self._lock:
            settings = self._settings
        boost = (
            int(round(settings.accent * ACCENT_MAX_BOOST))
            if is_downbeat
            else 0
        )
        return max(1, min(127, self.config.sub_velocity + boost))

    # -- mutations (immediate at the engine level) ----------------------

    def set_setting(self, name: str, value: object) -> BassSettings:
        with self._lock:
            settings = self._settings.with_change(name, value)
            self._settings = settings
            self._version += 1
            self._pattern_cache = None
            self._picker.set_settings(settings)
            return settings

    def set_settings(self, settings: BassSettings) -> BassSettings:
        with self._lock:
            self._settings = settings
            self._version += 1
            self._pattern_cache = None
            self._picker.set_settings(settings)
            return settings

    def set_enabled(self, enabled: object) -> bool:
        with self._lock:
            self._settings = replace(
                self._settings, enabled=validate_bool(enabled, "enabled")
            )
            self._version += 1
            return self._settings.enabled

    def set_meter(self, meter_num: object, meter_den: object) -> int:
        """Change the meter; returns the (possibly new) pattern length."""

        numerator = rhythm.clamp_meter_numerator(meter_num)
        denominator = rhythm.validate_meter_denominator(meter_den)
        with self._lock:
            if (numerator, denominator) != (self._meter_num, self._meter_den):
                self._meter_num = numerator
                self._meter_den = denominator
                self._version += 1
                self._pattern_cache = None
            return resolve_length(
                self._settings, self._meter_num, self._meter_den
            )

    def on_set(
        self,
        ratios: Sequence[float],
        anchor: float | None,
        fundamental: float,
    ) -> None:
        """Store a new engine snapshot; the next trigger uses the new pool."""

        fund = _finite(fundamental, "fundamental")
        if fund <= 0.0:
            raise ValueError("fundamental must be strictly positive")
        pool = tuple(
            sorted(
                float(ratio)
                for ratio in ratios
                if math.isfinite(float(ratio)) and float(ratio) > 0.0
            )
        )
        with self._lock:
            self._ratios = pool or (1.0,)
            self._anchor = None if anchor is None else float(anchor)
            self._fundamental = fund
            self._picker.set_pool(self._ratios, self._anchor, self._fundamental)
            self._version += 1
            self._pattern_cache = None

    def state_args(self, port_ok: bool) -> list[Any]:
        """``/ji/bass/state`` payload (order documented in the prompt)."""

        with self._lock:
            settings = self._settings
            length = resolve_length(
                settings, self._meter_num, self._meter_den
            )
            return [
                int(settings.enabled),
                int(bool(port_ok)),
                settings.preset,
                settings.grid,
                int(length),
                float(settings.density),
                float(settings.syncopation),
                float(settings.swing),
                float(settings.gate_beats),
                float(settings.accent),
                settings.pitch_mode,
                float(settings.movement),
                int(settings.octave_range),
                float(settings.octave_jump),
                int(settings.seed),
                int(settings.base_octave),
                int(settings.sub_enabled),
                int(settings.sub_octaves),
                int(self.config.channel),
                int(self.config.sub_channel),
            ]

    def description(self) -> str:
        with self._lock:
            settings = self._settings
            return (
                f"preset={settings.preset} grid={settings.grid} "
                f"density={settings.density:.2f} "
                f"sync={settings.syncopation:.2f} "
                f"swing={settings.swing:.2f} "
                f"gate={settings.gate_beats:.3f} beats "
                f"pitch={settings.pitch_mode} "
                f"movement={settings.movement:.2f} "
                f"octaves={settings.octave_range} seed={settings.seed}"
            )


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------


class BassScheduler:
    """Daemon-thread transport-synced bass sequencer.

    The thread owns drift-corrected monotonic deadlines and sends pitch bend
    before each note-on.  A gate timer sends the note-off; a retrigger cancels
    the pending timer and note-offs the held note first, so a late gate can
    never cut the next note.
    """

    def __init__(
        self,
        output: BassOutput,
        engine: BassEngine,
        *,
        bpm: float = rhythm.BPM_DEFAULT,
        now: Callable[[], float] = time.monotonic,
        on_note: Callable[[BassNote, Any, int], None] | None = None,
    ) -> None:
        if not callable(now):
            raise TypeError("now must be callable")
        self.output = output
        self.engine = engine
        self._now = now
        self._bpm = rhythm.clamp_bpm(bpm)
        self._on_note = on_note
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._step_index = 0
        self._gate_timer: threading.Timer | None = None
        self._gate_token = 0
        self._held_main: int | None = None
        self._held_sub: int | None = None
        self._logged_version = -1
        self._sub_warned = False
        self._swing_logged = False

    @property
    def bpm(self) -> float:
        with self._lock:
            return self._bpm

    @property
    def step_index(self) -> int:
        with self._lock:
            return self._step_index

    @property
    def is_running(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    def set_bpm(self, bpm: object) -> float:
        value = rhythm.clamp_bpm(bpm)
        with self._lock:
            self._bpm = value
            return value

    def release_sub(self) -> None:
        """Note-off the held sub note (used when the sub is disabled)."""

        with self._lock:
            sub = self._held_sub
            self._held_sub = None
        if sub is not None:
            try:
                self.output.note_off(self.output.sub_channel, sub)
            except Exception:
                pass

    def start(self) -> None:
        """Start at step 0.  Idempotent while running."""

        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            if not self.output.is_open:
                raise RuntimeError("bass output is not open")
            self._stop.clear()
            self._step_index = 0
            self._logged_version = -1
            self._sub_warned = False
            self._swing_logged = False
            thread = threading.Thread(
                target=self._run, name="ji-bass", daemon=True
            )
            self._thread = thread
        thread.start()

    def stop(self) -> None:
        """Stop the thread, cancel a pending gate, and silence both channels."""

        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        with self._lock:
            if self._thread is thread:
                self._thread = None
            self._cancel_gate_locked()
            held = (self._held_main, self._held_sub)
            self._held_main = None
            self._held_sub = None
        self._send_offs(held)
        for channel in (
            self.output.channel,
            self.output.sub_channel,
        ):
            try:
                self.output.all_notes_off(channel)
            except Exception:
                pass

    # -- thread body ----------------------------------------------------

    def _run(self) -> None:  # pragma: no cover - exercised with a fake output
        deadline = self._now()
        step = 0
        bar = 0
        try:
            while not self._stop.is_set():
                settings, length = self.engine.timing()
                bpm = self.bpm
                step_seconds = (60.0 / bpm) / settings.steps_per_beat
                # Swing delays odd 8th/16th grid steps by up to half a step.
                offset = swing_offset(
                    step,
                    settings.swing,
                    step_seconds,
                    settings.steps_per_beat,
                )
                target = deadline + offset
                now = self._now()
                if target > now:
                    if self._stop.wait(target - now):
                        break
                elif self._stop.is_set():
                    break

                note = self.engine.note_at(step, bar)
                if note is not None:
                    self._play(note, settings, step_seconds)

                with self._lock:
                    self._step_index = step
                step += 1
                if step >= max(1, length):
                    step = 0
                    bar += 1
                deadline += step_seconds
                now = self._now()
                if deadline < now:
                    # Fell behind: resume from now; never burst through steps.
                    deadline = now
                wait = deadline - now
                if wait > 0.0 and self._stop.wait(wait):
                    break
        except Exception as exc:  # a broken output must not crash quietly
            print(f"[BASS] scheduler error: {exc}", file=sys.stderr)
        finally:
            with self._lock:
                self._cancel_gate_locked()
                held = (self._held_main, self._held_sub)
                self._held_main = None
                self._held_sub = None
            self._send_offs(held)

    def _play(
        self,
        note: BassNote,
        settings: BassSettings,
        step_seconds: float,
    ) -> None:
        config = self.engine.config
        main_pitch = frequency_to_pitch(
            note.frequency, config.bend_range_semitones
        )
        velocity = self.engine.velocity_for(note.is_downbeat)
        gate_seconds = max(
            0.001, settings.gate_beats * (60.0 / self.bpm)
        )

        with self._lock:
            self._cancel_gate_locked()
            held = (self._held_main, self._held_sub)
            self._held_main = None
            self._held_sub = None
        self._send_offs(held)

        self.output.pitch_bend(self.output.channel, main_pitch.bend)
        self.output.note_on(self.output.channel, main_pitch.note, velocity)
        with self._lock:
            self._held_main = main_pitch.note

        sub_pitch = None
        if settings.sub_enabled:
            sub_frequency = note.sub_frequency(settings.sub_octaves)
            if sub_frequency < midi_note_frequency(0):
                if not self._sub_warned:
                    print(
                        f"[BASS] sub hz={sub_frequency:.3f} is below MIDI "
                        "note 0; skipping the sub note"
                    )
                    self._sub_warned = True
            else:
                sub_pitch = frequency_to_pitch(
                    sub_frequency, config.bend_range_semitones
                )
                sub_velocity = self.engine.sub_velocity_for(note.is_downbeat)
                self.output.pitch_bend(self.output.sub_channel, sub_pitch.bend)
                self.output.note_on(
                    self.output.sub_channel, sub_pitch.note, sub_velocity
                )
                with self._lock:
                    self._held_sub = sub_pitch.note

        self._schedule_gate(gate_seconds)

        version = self.engine.version
        if version != self._logged_version:
            self._logged_version = version
            print(
                f"[BASS] note ratio={note.ratio:.3f} "
                f"hz={note.frequency:.3f} midi={main_pitch.note} "
                f"bend={main_pitch.bend}"
            )
        callback = self._on_note
        if callback is not None:
            try:
                callback(note, main_pitch, velocity)
            except Exception as exc:  # status is best-effort
                print(f"[BASS] note callback failed: {exc!r}", file=sys.stderr)

    def _schedule_gate(self, seconds: float) -> None:
        with self._lock:
            self._cancel_gate_locked()
            self._gate_token += 1
            token = self._gate_token
            timer = threading.Timer(
                max(0.001, float(seconds)), self._gate_fire, args=(token,)
            )
            timer.daemon = True
            self._gate_timer = timer
        timer.start()

    def _cancel_gate_locked(self) -> None:
        timer = self._gate_timer
        self._gate_timer = None
        self._gate_token += 1
        if timer is not None:
            timer.cancel()

    def _gate_fire(self, token: int) -> None:
        with self._lock:
            if token != self._gate_token or self._stop.is_set():
                return
            self._gate_timer = None
            self._gate_token += 1
            held = (self._held_main, self._held_sub)
            self._held_main = None
            self._held_sub = None
        self._send_offs(held)

    def _send_offs(self, held: tuple[int | None, int | None]) -> None:
        main, sub = held
        if main is not None:
            try:
                self.output.note_off(self.output.channel, main)
            except Exception:
                pass
        if sub is not None:
            try:
                self.output.note_off(self.output.sub_channel, sub)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Rig
# ---------------------------------------------------------------------------


class BassRig:
    """Own the bass output, engine, and scheduler behind one small API."""

    def __init__(
        self,
        config: BassConfig | Mapping[str, Any] | str | Path | None = None,
        *,
        config_path: str | Path | None = None,
        output: Any | None = None,
        meter_num: object = 4,
        meter_den: object = 4,
        bpm: object = rhythm.BPM_DEFAULT,
    ) -> None:
        if config is None:
            config = config_path if config_path is not None else DEFAULT_CONFIG_PATH
        if isinstance(config, BassConfig):
            cfg = config
        elif isinstance(config, (str, Path)):
            cfg = load_config(config)
        elif isinstance(config, Mapping):
            cfg = BassConfig.from_mapping(config)
        else:
            raise TypeError("config must be a BassConfig, mapping, path, or None")

        self.config = cfg
        self.engine = BassEngine(
            cfg, meter_num=meter_num, meter_den=meter_den
        )
        self.output = BassOutput(
            cfg.port,
            cfg.channel,
            cfg.sub_channel,
            velocity=cfg.velocity,
            sub_velocity=cfg.sub_velocity,
            output=output,
            label="bass",
        )
        self.scheduler = BassScheduler(
            self.output, self.engine, bpm=bpm, on_note=self._on_note
        )
        self._port_ok = output is not None
        self._started = False
        self._transport_running = False
        self._note_callback: Callable[[float, int, int], None] | None = None
        self._last_note_sent: tuple[float, int, int] | None = None

    # -- lifecycle ------------------------------------------------------

    def start(self) -> bool:
        """Open the port; a missing Bus 4 disables only the bass."""

        if self._started:
            return self._port_ok
        self._started = True
        if not self.output.is_open:
            try:
                self.output.open()
            except Exception as exc:
                self._port_ok = False
                self.engine.set_enabled(False)
                print(
                    f"[MIDI] bass disabled: {exc}; drone/arp/drums continue",
                    file=sys.stderr,
                )
                return False
        self._port_ok = True
        print(
            f"[BASS] port={self.config.port} ch={self.config.channel} "
            f"sub_ch={self.config.sub_channel} "
            f"bend={self.config.bend_range_semitones:g}"
        )
        print(f"[BASS] {self.engine.description()}")
        if self._transport_running and self.engine.enabled:
            self.scheduler.start()
        return True

    def stop(self) -> None:
        try:
            if self.output.is_open:
                self.scheduler.stop()
        finally:
            self.output.close()
        self._started = False
        self._port_ok = False

    # -- state ----------------------------------------------------------

    @property
    def port_ok(self) -> bool:
        return self._port_ok

    @property
    def enabled(self) -> bool:
        return self.engine.enabled

    @property
    def port(self) -> str:
        return self.config.port

    def state_args(self) -> list[Any]:
        return self.engine.state_args(self._port_ok)

    def set_note_callback(
        self, callback: Callable[[float, int, int], None] | None
    ) -> None:
        self._note_callback = callback

    # -- transport / tempo / meter --------------------------------------

    def set_transport(self, running: object) -> None:
        self._transport_running = bool(running)
        if self._transport_running:
            if self._port_ok and self.engine.enabled:
                self.scheduler.start()
        else:
            if self.scheduler.is_running:
                self.scheduler.stop()

    def set_bpm(self, bpm: object) -> float:
        return self.scheduler.set_bpm(bpm)

    def set_meter(self, meter_num: object, meter_den: object) -> int:
        before = self.engine.length_steps
        length = self.engine.set_meter(meter_num, meter_den)
        if length != before:
            print(
                f"[BASS] pattern reloaded for meter={self.engine.meter} "
                f"steps={length}"
            )
        return length

    # -- settings -------------------------------------------------------

    def set_setting(self, name: str, value: object) -> BassSettings:
        key = str(name).strip().lower()
        if key == "enabled":
            enabled = self.engine.set_enabled(value)
            if self._port_ok:
                if enabled and self._transport_running:
                    self.scheduler.start()
                elif not enabled and self.scheduler.is_running:
                    self.scheduler.stop()
            return self.engine.settings
        settings = self.engine.set_setting(key, value)
        if key == "sub_enabled" and not settings.sub_enabled:
            self.scheduler.release_sub()
        if key == "preset":
            print(f"[BASS] {self.engine.description()}")
        return settings

    def set_preset(self, preset: object) -> BassSettings:
        return self.set_setting("preset", preset)

    def set_enabled(self, enabled: object) -> bool:
        """Enable/disable the bass; starts/stops the scheduler as needed."""

        self.set_setting("enabled", enabled)
        return self.engine.enabled

    def on_set(
        self,
        ratios: Sequence[float],
        anchor: float | None,
        fundamental: float,
        *,
        transient: bool = False,
    ) -> None:
        """Store one snapshot; the next bass step picks from the new pool.

        ``transient=True`` is one step of a Stage 8.4.1 class glide: the pool
        follows the moving class and only the per-set log line is suppressed.
        """

        self.engine.on_set(ratios, anchor, fundamental)
        if transient:
            return
        print(
            f"[BASS] set updated root={1.0:.3f} "
            f"hz={self.engine.fundamental:.3f} pool={len(self.engine.ratios)}"
        )

    # -- note callback ---------------------------------------------------

    def _on_note(self, note: BassNote, pitch: Any, velocity: int) -> None:
        callback = self._note_callback
        if callback is None:
            return
        key = (round(note.frequency, 6), int(pitch.note), int(velocity))
        if key == self._last_note_sent:
            return
        self._last_note_sent = key
        callback(note.frequency, int(pitch.note), int(velocity))


__all__ = [
    "ACCENT_MAX_BOOST",
    "BASE_OCTAVE_MAX",
    "BASE_OCTAVE_MIN",
    "BassConfig",
    "BassEngine",
    "BassNote",
    "BassOutput",
    "BassPattern",
    "BassPitchPicker",
    "BassRig",
    "BassScheduler",
    "BassSettings",
    "DEFAULT_CHANNEL",
    "DEFAULT_CONFIG_PATH",
    "DEFAULT_GRID",
    "DEFAULT_PORT",
    "DEFAULT_PRESET",
    "DEFAULT_SEED",
    "DEFAULT_SUB_CHANNEL",
    "GATE_BEATS_MAX",
    "GATE_BEATS_MIN",
    "GRIDS",
    "GRID_STEPS",
    "LENGTH_MAX",
    "LENGTH_MIN",
    "OCTAVE_RANGE_MAX",
    "OCTAVE_RANGE_MIN",
    "PITCH_MODES",
    "PRESETS",
    "PRESET_CUSTOM",
    "PRESET_VALUES",
    "SEED_MAX",
    "SUB_OCTAVES",
    "build_pattern",
    "clamp_unit",
    "grid_steps",
    "load_config",
    "resolve_length",
    "swing_offset",
    "validate_base_octave",
    "validate_bool",
    "validate_channel",
    "validate_gate_beats",
    "validate_grid",
    "validate_length",
    "validate_octave_jump",
    "validate_octave_range",
    "validate_pitch_mode",
    "validate_preset",
    "validate_seed",
    "validate_sub_octaves",
    "validate_velocity",
]

"""Stage 8.2 pure groove model: presets, grid, modifiers, seeded generation.

This module is deliberately pure.  It imports only :mod:`control.rhythm` (also
pure) and the standard library: no ``mido``, no PyQt6, no sockets, no audio.

The model has two layers:

* **Grid** -- 16 lanes (one bit mask per lane) over the current meter's
  16th-note bar.  Bit ``s`` of a lane mask means "step ``s`` is active".
  Editable columns are capped at :data:`MAX_GRID_STEPS` (32) for Stage 8.2;
  meters longer than 32 steps repeat the 32-step grid across the rest of the
  bar (documented in ``docs/README_stage8_2.md``).
* **Modifiers** -- preset, density, syncopation, swing, probability, seed.
  Density/syncopation generate additional *ghost* hits; probability gates only
  those ghosts, never the core preset/grid hits.  Swing only changes timing.

Generation is deterministic.  A bar's ghosts are drawn from RNG streams keyed by
``(seed, bar_index, modifier name)``, so the same preset + modifiers + seed +
bar index always gives the same pattern.  No ambient :mod:`random` state is used.

Meter behavior
--------------
Selecting a preset, changing the meter, or resetting the grid reloads the grid
from the preset.  Manual per-cell edits are therefore discarded by a preset or
meter change -- the GUI documents this.  The ``"four"`` preset reproduces the
Stage 8.1 four-on-the-floor pattern exactly for every supported meter; the other
presets use a 16-step template that is truncated to the bar and tiled for bars
longer than 16 steps.

Ghost rules (documented, testable)
----------------------------------
* Eligible ghost lanes are the percussion lanes (names starting with ``perc``,
  i.e. default lane indices 4-15).  Kick/snare core lanes are never ghosted.
* **Density** selects ``round(density * steps_per_bar / 2)`` empty eligible
  cells (0 for density 0, 8 for a full 16-step bar at density 1).  Selection is
  a seeded sample, so raising density adds hits instead of replacing them.
* **Syncopation** adds an anticipation 16th on the same lane one step before
  every core hit that is *not* on a quarter-note downbeat (and not step 0),
  with probability ``syncopation`` per hit.  The core hit itself always remains.
* **Probability** keeps each generated ghost with probability ``probability``.
  At 0 every ghost is muted; at 1 every ghost plays.  Core grid hits are never
  gated.
"""

from __future__ import annotations

import math
import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace

try:  # Support both ``python -m control.groove`` and direct execution.
    from . import rhythm
except ImportError:  # pragma: no cover - exercised only by direct execution
    import rhythm  # type: ignore[no-redef]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

LANE_COUNT = 16
MAX_GRID_STEPS = 32
GRID_MASK = (1 << MAX_GRID_STEPS) - 1
GRID_LO_MASK = 0xFFFF
GRID_HI_MASK = 0xFFFF

PRESETS: tuple[str, ...] = ("four", "break", "half", "techno")
DEFAULT_PRESET = "four"
DEFAULT_SEED = 1

DENSITY_DEFAULT = 0.0
SYNCOPATION_DEFAULT = 0.0
SWING_DEFAULT = 0.0
PROBABILITY_DEFAULT = 0.0

MODIFIER_NAMES: tuple[str, ...] = (
    "density",
    "syncopation",
    "swing",
    "probability",
)

# Default lane names, matching config/rhythm_config.json.  The list order is the
# lane-mask bit order (bit 0 = kick1 / note 36).
DEFAULT_LANE_NAMES: tuple[str, ...] = (
    "kick1",
    "kick2",
    "snare1",
    "snare2",
    "perc1",
    "perc2",
    "perc3",
    "perc4",
    "perc5",
    "perc6",
    "perc7",
    "perc8",
    "perc9",
    "perc10",
    "perc11",
    "perc12",
)

# Named roles used by the preset templates.  A role that is missing from a
# custom lane map is skipped (the preset still works for the remaining roles).
ROLE_KICK = "kick1"
ROLE_SNARE = "snare1"
ROLE_PERC2 = "perc2"
ROLE_PERC3 = "perc3"
ROLE_PERC5 = "perc5"

# 16-step templates for the non-"four" presets.  They are truncated to bars
# shorter than 16 steps and tiled for bars longer than 16 steps.
_TEMPLATES: dict[str, dict[str, tuple[int, ...]]] = {
    "break": {
        ROLE_KICK: (0, 6, 10),
        ROLE_SNARE: (4, 12),
        ROLE_PERC2: (4, 12),
        ROLE_PERC3: (0, 2, 4, 6, 8, 10, 12, 14),
    },
    "half": {
        ROLE_KICK: (0, 8),
        ROLE_SNARE: (8,),
        ROLE_PERC3: (0, 4, 8, 12),
    },
    "techno": {
        ROLE_KICK: (0, 4, 8, 12),
        ROLE_SNARE: (),
        ROLE_PERC2: (4, 12),
        ROLE_PERC3: (0, 2, 4, 6, 8, 10, 12, 14),
        ROLE_PERC5: (2, 6, 10, 14),
    },
}

_SEED_MAX = 2**31 - 1
_MASK64 = (1 << 64) - 1


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def clamp_unit(value: object, label: str = "value") -> float:
    """Return ``value`` as a finite float clamped to ``0.0..1.0``."""

    try:
        numeric = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise TypeError(f"{label} must be a real number") from None
    if not math.isfinite(numeric):
        raise ValueError(f"{label} must be finite")
    return max(0.0, min(1.0, numeric))


def validate_preset(value: object) -> str:
    """Return a lower-case preset name or raise ``ValueError``."""

    name = str(value).strip().lower()
    if name not in PRESETS:
        raise ValueError(
            "preset must be one of " + ", ".join(PRESETS) + f", got {value!r}"
        )
    return name


def validate_seed(value: object) -> int:
    """Return an integer seed clamped to ``0..2**31-1``."""

    try:
        numeric = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise TypeError("seed must be a real number") from None
    if not math.isfinite(numeric):
        raise ValueError("seed must be finite")
    return max(0, min(_SEED_MAX, int(round(numeric))))


def validate_lane(value: object) -> int:
    """Return a lane index ``0..15`` or raise ``ValueError``."""

    try:
        numeric = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise TypeError("lane must be a real number") from None
    if not math.isfinite(numeric):
        raise ValueError("lane must be finite")
    lane = int(round(numeric))
    if not 0 <= lane < LANE_COUNT:
        raise ValueError(f"lane must be 0..{LANE_COUNT - 1}, got {value!r}")
    return lane


def validate_step(value: object) -> int:
    """Return a step index ``0..31`` or raise ``ValueError``."""

    try:
        numeric = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise TypeError("step must be a real number") from None
    if not math.isfinite(numeric):
        raise ValueError("step must be finite")
    step = int(round(numeric))
    if not 0 <= step < MAX_GRID_STEPS:
        raise ValueError(f"step must be 0..{MAX_GRID_STEPS - 1}, got {value!r}")
    return step


def validate_lane_names(value: object) -> tuple[str, ...]:
    """Return exactly 16 non-empty lane names or raise ``ValueError``."""

    if isinstance(value, (str, bytes)):
        raise TypeError("lane names must be a sequence of strings")
    try:
        names = tuple(str(item) for item in value)  # type: ignore[union-attr]
    except TypeError:
        raise TypeError("lane names must be a sequence of strings") from None
    if len(names) != LANE_COUNT:
        raise ValueError(f"lane names must contain exactly {LANE_COUNT} items")
    if any(not name for name in names):
        raise ValueError("lane names must be non-empty")
    return names


# ---------------------------------------------------------------------------
# Spec and grid
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GrooveSpec:
    """Validated groove modifiers."""

    preset: str = DEFAULT_PRESET
    density: float = DENSITY_DEFAULT
    syncopation: float = SYNCOPATION_DEFAULT
    swing: float = SWING_DEFAULT
    probability: float = PROBABILITY_DEFAULT
    seed: int = DEFAULT_SEED

    @classmethod
    def create(
        cls,
        preset: object = DEFAULT_PRESET,
        density: object = DENSITY_DEFAULT,
        syncopation: object = SYNCOPATION_DEFAULT,
        swing: object = SWING_DEFAULT,
        probability: object = PROBABILITY_DEFAULT,
        seed: object = DEFAULT_SEED,
    ) -> "GrooveSpec":
        return cls(
            preset=validate_preset(preset),
            density=clamp_unit(density, "density"),
            syncopation=clamp_unit(syncopation, "syncopation"),
            swing=clamp_unit(swing, "swing"),
            probability=clamp_unit(probability, "probability"),
            seed=validate_seed(seed),
        )

    def with_modifier(self, name: str, value: object) -> "GrooveSpec":
        """Return a new spec with one modifier clamped/validated."""

        key = str(name).strip().lower()
        if key == "preset":
            return replace(self, preset=validate_preset(value))
        if key not in MODIFIER_NAMES:
            raise ValueError(
                "modifier must be one of preset, "
                + ", ".join(MODIFIER_NAMES)
            )
        return replace(self, **{key: clamp_unit(value, key)})

    def with_seed(self, seed: object) -> "GrooveSpec":
        return replace(self, seed=validate_seed(seed))


@dataclass(frozen=True, slots=True)
class GrooveGrid:
    """16 lane bit masks; bit ``s`` means step ``s`` is active."""

    masks: tuple[int, ...]

    def __post_init__(self) -> None:
        if len(self.masks) != LANE_COUNT:
            raise ValueError(f"grid must contain {LANE_COUNT} lane masks")
        for index, mask in enumerate(self.masks):
            if isinstance(mask, bool) or not isinstance(mask, int):
                raise TypeError(f"lane {index} mask must be an integer")
            if not 0 <= mask <= GRID_MASK:
                raise ValueError(
                    f"lane {index} mask must be a 0..0x{GRID_MASK:x} integer"
                )

    @classmethod
    def empty(cls) -> "GrooveGrid":
        return cls(tuple(0 for _ in range(LANE_COUNT)))

    @classmethod
    def from_pairs(cls, pairs: Sequence[object]) -> "GrooveGrid":
        """Build from 16 ``(mask_lo, mask_hi)`` pairs (the OSC layout)."""

        if len(pairs) != LANE_COUNT:
            raise ValueError(f"grid needs {LANE_COUNT} (lo, hi) pairs")
        masks: list[int] = []
        for index, pair in enumerate(pairs):
            if isinstance(pair, (str, bytes)):
                raise TypeError(f"lane {index} pair must be two integers")
            try:
                low, high = pair  # type: ignore[misc]
            except (TypeError, ValueError):
                raise ValueError(f"lane {index} pair must be (lo, hi)") from None
            lo = _pair_int(low, "mask_lo")
            hi = _pair_int(high, "mask_hi")
            masks.append((lo & GRID_LO_MASK) | ((hi & GRID_HI_MASK) << 16))
        return cls(tuple(masks))

    @classmethod
    def from_bits(cls, bits: Mapping[int, Sequence[int]]) -> "GrooveGrid":
        """Build from ``{lane_index: [step, ...]}`` (test/config helper)."""

        masks = [0] * LANE_COUNT
        for lane, steps in bits.items():
            index = validate_lane(lane)
            for step in steps:
                masks[index] |= 1 << validate_step(step)
        return cls(tuple(masks))

    def lane_mask(self, lane: object) -> int:
        return self.masks[validate_lane(lane)]

    def is_on(self, lane: object, step: object) -> bool:
        return bool(self.lane_mask(lane) & (1 << validate_step(step)))

    def with_cell(self, lane: object, step: object, on: object = True) -> "GrooveGrid":
        index = validate_lane(lane)
        column = validate_step(step)
        masks = list(self.masks)
        if bool(on):
            masks[index] |= 1 << column
        else:
            masks[index] &= ~(1 << column) & GRID_MASK
        return GrooveGrid(tuple(masks))

    def toggled(self, lane: object, step: object) -> "GrooveGrid":
        return self.with_cell(lane, step, not self.is_on(lane, step))

    def pairs(self) -> tuple[tuple[int, int], ...]:
        """The 32-int OSC payload as 16 ``(lo, hi)`` pairs."""

        return tuple(
            (mask & GRID_LO_MASK, (mask >> 16) & GRID_HI_MASK)
            for mask in self.masks
        )

    def payload(self) -> list[int]:
        """The flat 32-int ``/ji/drums/grid`` payload."""

        out: list[int] = []
        for low, high in self.pairs():
            out.extend((low, high))
        return out


def _pair_int(value: object, label: str) -> int:
    try:
        numeric = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise TypeError(f"{label} must be a real number") from None
    if not math.isfinite(numeric):
        raise ValueError(f"{label} must be finite")
    return int(round(numeric))


# ---------------------------------------------------------------------------
# Preset generation
# ---------------------------------------------------------------------------


def lane_index_map(lane_names: object = DEFAULT_LANE_NAMES) -> dict[str, int]:
    """Map lane name -> index, validating the 16-lane list."""

    names = validate_lane_names(lane_names)
    return {name: index for index, name in enumerate(names)}


def preset_lane_steps(
    preset: object,
    meter_num: object = 4,
    meter_den: object = 4,
    lane_names: object = DEFAULT_LANE_NAMES,
) -> dict[int, tuple[int, ...]]:
    """Return ``{lane_index: (step, ...)}`` for one preset and meter.

    ``"four"`` uses the exact Stage 8.1 rule (kick every quarter, snare on beats
    2/4 where the bar contains them, closed hat on offbeat 8ths).  The other
    presets truncate their 16-step template to shorter bars and tile it for
    longer bars.  Only steps inside the first :data:`MAX_GRID_STEPS` columns are
    stored; generation repeats those columns across longer bars.
    """

    name = validate_preset(preset)
    numerator = rhythm.clamp_meter_numerator(meter_num)
    denominator = rhythm.validate_meter_denominator(meter_den)
    indices = lane_index_map(lane_names)
    total_steps = rhythm.steps_per_bar(numerator, denominator)
    stored_steps = min(total_steps, MAX_GRID_STEPS)
    steps_by_lane: dict[int, set[int]] = {index: set() for index in range(LANE_COUNT)}

    def add(role: str, step: int) -> None:
        index = indices.get(role)
        if index is None:
            return
        if 0 <= step < stored_steps:
            steps_by_lane[index].add(step)

    if name == "four":
        quarters = rhythm.bar_quarters(numerator, denominator)
        for step in range(stored_steps):
            if step % rhythm.STEPS_PER_QUARTER == 0:
                add(ROLE_KICK, step)
                beat = step // rhythm.STEPS_PER_QUARTER
                if beat in (1, 3) and beat < quarters - 1e-9:
                    add(ROLE_SNARE, step)
            if step % rhythm.STEPS_PER_QUARTER == 2:
                add(ROLE_PERC3, step)
    else:
        template = _TEMPLATES[name]
        repeats = max(1, (stored_steps + 15) // 16)
        for role, positions in template.items():
            for repeat in range(repeats):
                offset = repeat * 16
                for position in positions:
                    add(role, position + offset)

    return {
        index: tuple(sorted(steps))
        for index, steps in steps_by_lane.items()
        if steps
    }


def preset_grid(
    preset: object = DEFAULT_PRESET,
    meter_num: object = 4,
    meter_den: object = 4,
    lane_names: object = DEFAULT_LANE_NAMES,
) -> GrooveGrid:
    """Build the base grid for a preset and meter."""

    masks = [0] * LANE_COUNT
    for lane, steps in preset_lane_steps(
        preset, meter_num, meter_den, lane_names
    ).items():
        for step in steps:
            masks[lane] |= 1 << step
    return GrooveGrid(tuple(masks))


# ---------------------------------------------------------------------------
# Seeded generation
# ---------------------------------------------------------------------------


def _mix64(value: int) -> int:
    """SplitMix64 finaliser (pure integer math, stable across processes)."""

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


def _percussion_lanes(lane_names: object) -> tuple[int, ...]:
    names = validate_lane_names(lane_names)
    lanes = tuple(
        index for index, name in enumerate(names) if name.startswith("perc")
    )
    if lanes:
        return lanes
    # Conservative fallback for a custom map without "perc" names.
    return tuple(index for index in range(LANE_COUNT) if index not in (0, 2))


@dataclass(frozen=True, slots=True)
class GrooveBar:
    """One generated bar of the 16th-note grid."""

    steps: tuple[tuple[int, ...], ...]
    meter_num: int
    meter_den: int
    bar_index: int
    spec: GrooveSpec

    @property
    def bar_steps(self) -> int:
        return len(self.steps)

    def lanes_at(self, step_index: int) -> tuple[int, ...]:
        if not self.steps:
            return ()
        return self.steps[int(step_index) % len(self.steps)]

    def lane_has(self, lane: int, step_index: int) -> bool:
        return lane in self.lanes_at(step_index)


def build_bar(
    spec: GrooveSpec,
    grid: GrooveGrid,
    meter_num: object = 4,
    meter_den: object = 4,
    bar_index: int = 0,
    lane_names: object = DEFAULT_LANE_NAMES,
) -> GrooveBar:
    """Generate one deterministic bar from a spec + grid.

    ``bar_index`` is the only time-varying input, so a given bar is reproducible
    from ``(preset, modifiers, seed, bar_index)``.  Core grid hits are always
    present; density/syncopation ghosts are added, then probability gates only
    the ghosts.
    """

    if not isinstance(spec, GrooveSpec):
        raise TypeError("spec must be a GrooveSpec")
    if not isinstance(grid, GrooveGrid):
        raise TypeError("grid must be a GrooveGrid")
    numerator = rhythm.clamp_meter_numerator(meter_num)
    denominator = rhythm.validate_meter_denominator(meter_den)
    names = validate_lane_names(lane_names)
    total_steps = rhythm.steps_per_bar(numerator, denominator)
    bar = int(bar_index)
    if bar < 0:
        raise ValueError("bar_index must be non-negative")

    # Core layer: repeat the 32 editable columns across longer bars.
    core: list[set[int]] = []
    for step in range(total_steps):
        column = step % MAX_GRID_STEPS
        lanes = {
            lane for lane in range(LANE_COUNT) if grid.masks[lane] & (1 << column)
        }
        core.append(lanes)

    ghosts: set[tuple[int, int]] = set()

    # Density: a seeded sample of empty percussion cells.
    ghost_lanes = _percussion_lanes(names)
    if spec.density > 0.0:
        candidates = [
            (lane, step)
            for step in range(total_steps)
            for lane in ghost_lanes
            if lane not in core[step]
        ]
        target = int(round(spec.density * total_steps / 2.0))
        target = max(0, min(len(candidates), target))
        if target:
            rng = _bar_rng(spec.seed, bar, "density")
            ghosts.update(rng.sample(candidates, target))

    # Syncopation: anticipation 16ths before non-downbeat core hits.
    if spec.syncopation > 0.0:
        rng = _bar_rng(spec.seed, bar, "syncopation")
        for step in range(1, total_steps):
            if step % rhythm.STEPS_PER_QUARTER == 0:
                continue
            for lane in sorted(core[step]):
                if lane in core[step - 1]:
                    continue
                if rng.random() < spec.syncopation:
                    ghosts.add((lane, step - 1))

    # Probability: gate only generated ghosts, in a deterministic order.
    if ghosts and spec.probability > 0.0:
        rng = _bar_rng(spec.seed, bar, "probability")
        kept = {
            ghost
            for ghost in sorted(ghosts)
            if rng.random() < spec.probability
        }
    else:
        kept = set()

    lanes_by_step: list[set[int]] = [set(lanes) for lanes in core]
    for lane, step in kept:
        lanes_by_step[step].add(lane)

    return GrooveBar(
        steps=tuple(tuple(sorted(lanes)) for lanes in lanes_by_step),
        meter_num=numerator,
        meter_den=denominator,
        bar_index=bar,
        spec=spec,
    )


def swing_offset(
    step: object,
    swing: object = SWING_DEFAULT,
    step_seconds: object = 0.125,
) -> float:
    """Seconds to delay ``step`` for the given swing amount.

    Even steps are always straight.  Odd 16th steps are delayed by
    ``swing * 0.5 * step_seconds``, so swing 0 is straight and swing 1 delays
    odd steps by half a step (the documented maximum; there is no triplet
    subdivision).
    """

    try:
        index = int(round(float(step)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise TypeError("step must be a real number") from None
    amount = clamp_unit(swing, "swing")
    try:
        length = float(step_seconds)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise TypeError("step_seconds must be a real number") from None
    if not math.isfinite(length) or length <= 0.0:
        raise ValueError("step_seconds must be finite and strictly positive")
    if index % 2 == 0:
        return 0.0
    return amount * 0.5 * length


# ---------------------------------------------------------------------------
# Stateful groove used by the drum engine
# ---------------------------------------------------------------------------


class Groove:
    """Mutable preset/grid/modifier state for one drum engine.

    This class has no lock of its own; :class:`control.drums.DrumEngine` owns
    the lock and calls these methods under it.  Pure tests may use it directly.
    """

    def __init__(
        self,
        spec: GrooveSpec | None = None,
        *,
        meter_num: object = 4,
        meter_den: object = 4,
        lane_names: object = DEFAULT_LANE_NAMES,
    ) -> None:
        self._lane_names = validate_lane_names(lane_names)
        self._meter_num = rhythm.clamp_meter_numerator(meter_num)
        self._meter_den = rhythm.validate_meter_denominator(meter_den)
        self._spec = GrooveSpec.create() if spec is None else _require_spec(spec)
        self._grid = preset_grid(
            self._spec.preset,
            self._meter_num,
            self._meter_den,
            self._lane_names,
        )

    # -- queries --------------------------------------------------------

    @property
    def spec(self) -> GrooveSpec:
        return self._spec

    @property
    def grid(self) -> GrooveGrid:
        return self._grid

    @property
    def lane_names(self) -> tuple[str, ...]:
        return self._lane_names

    @property
    def meter_num(self) -> int:
        return self._meter_num

    @property
    def meter_den(self) -> int:
        return self._meter_den

    @property
    def meter(self) -> str:
        return f"{self._meter_num}/{self._meter_den}"

    @property
    def steps_per_bar(self) -> int:
        return rhythm.steps_per_bar(self._meter_num, self._meter_den)

    @property
    def grid_steps(self) -> int:
        """Number of editable step columns for the current meter (max 32)."""

        return min(self.steps_per_bar, MAX_GRID_STEPS)

    def bar(self, bar_index: int = 0) -> GrooveBar:
        return build_bar(
            self._spec,
            self._grid,
            self._meter_num,
            self._meter_den,
            bar_index,
            self._lane_names,
        )

    def lanes_at(self, step: int, bar_index: int = 0) -> tuple[int, ...]:
        return self.bar(bar_index).lanes_at(step)

    def as_status_args(self) -> list[float | int | str]:
        """Payload for ``/ji/drums/groove``."""

        spec = self._spec
        return [
            spec.preset,
            float(spec.density),
            float(spec.syncopation),
            float(spec.swing),
            float(spec.probability),
            int(spec.seed),
            int(self.grid_steps),
        ]

    # -- mutations ------------------------------------------------------

    def set_preset(self, preset: object) -> GrooveSpec:
        """Select a preset and replace the grid with its base pattern."""

        name = validate_preset(preset)
        self._spec = replace(self._spec, preset=name)
        self._grid = preset_grid(
            name, self._meter_num, self._meter_den, self._lane_names
        )
        return self._spec

    def set_modifier(self, name: str, value: object) -> GrooveSpec:
        key = str(name)
        if key == "preset":
            # Route through set_preset so the grid is reloaded too.
            return self.set_preset(value)
        self._spec = self._spec.with_modifier(key, value)
        return self._spec

    def set_density(self, value: object) -> GrooveSpec:
        return self.set_modifier("density", value)

    def set_syncopation(self, value: object) -> GrooveSpec:
        return self.set_modifier("syncopation", value)

    def set_swing(self, value: object) -> GrooveSpec:
        return self.set_modifier("swing", value)

    def set_probability(self, value: object) -> GrooveSpec:
        return self.set_modifier("probability", value)

    def set_seed(self, seed: object) -> GrooveSpec:
        self._spec = self._spec.with_seed(seed)
        return self._spec

    def set_cell(self, lane: object, step: object, on: object = True) -> bool:
        """Set one grid cell; returns the new cell state.

        Lane ``0..15`` and step ``0..31`` are validated.  A step outside the
        current bar never plays until the meter grows; a preset or meter change
        discards it.
        """

        self._grid = self._grid.with_cell(lane, step, on)
        return bool(self._grid.is_on(lane, step))

    def toggle_cell(self, lane: object, step: object) -> bool:
        self._grid = self._grid.toggled(lane, step)
        return bool(self._grid.is_on(lane, step))

    def reset_grid(self) -> GrooveGrid:
        """Reload the current preset's base pattern, discarding manual edits."""

        self._grid = preset_grid(
            self._spec.preset,
            self._meter_num,
            self._meter_den,
            self._lane_names,
        )
        return self._grid

    def set_meter(self, meter_num: object, meter_den: object) -> GrooveGrid:
        """Change the meter and reload the preset grid for the new bar."""

        numerator = rhythm.clamp_meter_numerator(meter_num)
        denominator = rhythm.validate_meter_denominator(meter_den)
        if (numerator, denominator) != (self._meter_num, self._meter_den):
            self._meter_num = numerator
            self._meter_den = denominator
            self._grid = preset_grid(
                self._spec.preset, numerator, denominator, self._lane_names
            )
        return self._grid

    def swing_offset(self, step: object, step_seconds: object) -> float:
        return swing_offset(step, self._spec.swing, step_seconds)


def _require_spec(value: object) -> GrooveSpec:
    if not isinstance(value, GrooveSpec):
        raise TypeError("spec must be a GrooveSpec")
    # Re-validate through create() so even a directly constructed spec is safe.
    return GrooveSpec.create(
        value.preset,
        value.density,
        value.syncopation,
        value.swing,
        value.probability,
        value.seed,
    )


__all__ = [
    "DEFAULT_LANE_NAMES",
    "DEFAULT_PRESET",
    "DEFAULT_SEED",
    "DENSITY_DEFAULT",
    "GRID_HI_MASK",
    "GRID_LO_MASK",
    "GRID_MASK",
    "Groove",
    "GrooveBar",
    "GrooveGrid",
    "GrooveSpec",
    "LANE_COUNT",
    "MAX_GRID_STEPS",
    "MODIFIER_NAMES",
    "PRESETS",
    "PROBABILITY_DEFAULT",
    "SWING_DEFAULT",
    "SYNCOPATION_DEFAULT",
    "build_bar",
    "clamp_unit",
    "lane_index_map",
    "preset_grid",
    "preset_lane_steps",
    "swing_offset",
    "validate_lane",
    "validate_lane_names",
    "validate_preset",
    "validate_seed",
    "validate_step",
]

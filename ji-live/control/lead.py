"""Stage 8.3d monophonic JI lead/melody voice.

The lead is a *melodic* voice with its own IAC bus (``IAC Driver Bus 5``) so it
is a ready FX insert point in Ableton.  It reuses the Stage 6 MPE contract
(:class:`control.mpe_output.MPEOutput`: member channel 2, 48-semitone bend
range) and is deliberately monophonic -- one member channel, one note at a
time.  Pad-like width comes from the instrument + FX chain, not from MPE voice
allocation (that is out of scope for 8.3d).

Layers (mirroring ``control/bass.py``):

* :class:`LeadConfig`   -- validated ``config/lead_config.json``.
* :class:`LeadSettings` -- immutable, replace-by-value performance controls.
* :class:`LeadNote`     -- one stored motif note, in **set-relative** terms.
* :class:`LeadMotif`    -- the generated phrase.
* :class:`LeadEngine`   -- settings + motif + set snapshot + projection.
* :class:`LeadScheduler`-- daemon thread, drift-corrected deadlines, gate timers.
* :class:`LeadRig`      -- output + engine + scheduler behind one small API.

Consistency contract (the heart of the stage)
---------------------------------------------
Every motif note stores ``degree`` (its position in the *sorted current class
list* at birth) plus ``ratio_hint`` (the ratio it was born on).  Projection
against the current set is exactly:

1. if ``ratio_hint`` is still in the current set, keep that class (the exact
   pitch is preserved);
2. otherwise use ``ratios[degree % len(ratios)]`` (wrap when the set shrank);
3. the octave offset is unchanged.

Therefore a mutation that leaves the motif's ratios alone changes nothing, and
a mutation that removes one used ratio changes only that note while the contour
around it survives.  ``on_set`` never regenerates the motif; it only stores the
snapshot and re-projects.

Regeneration (a new phrase from the seed) happens only for the parameters that
define the generated *shape*: ``seed``, ``grid``, ``phrase_bars``, ``density``
and ``octave_range`` (plus a meter change, which changes the phrase length), a
``variation``, and ``vary()``.  Everything else -- velocity, gate, octave,
swing, groove link, variation rate, enabled -- is playback-only and applies
instantly to the notes that are already stored.

No ``mido`` import happens at import time or in any constructor.  A fake output
object can be injected for the pure tests.

See ``docs/README_stage8_3d.md`` and design doc §6.18.
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

try:  # Support both ``python -m control.lead`` and direct execution.
    from . import rhythm
    from .bass import swing_offset
    from .mpe_output import frequency_to_pitch
except ImportError:  # pragma: no cover - exercised only by direct execution
    import rhythm  # type: ignore[no-redef]
    from bass import swing_offset  # type: ignore[no-redef]
    from mpe_output import frequency_to_pitch  # type: ignore[no-redef]

# ---------------------------------------------------------------------------
# Constants / defaults
# ---------------------------------------------------------------------------

DEFAULT_CONFIG_PATH = (
    Path(__file__).resolve().parent.parent / "config" / "lead_config.json"
)

DEFAULT_PORT = "IAC Driver Bus 5"
DEFAULT_MEMBER_CHANNEL = 2
DEFAULT_BEND_RANGE_SEMITONES = 48.0

GRIDS: tuple[str, ...] = ("1/4", "1/8", "1/16", "1/8T")
#: Length of one grid step in quarter notes (a beat is one quarter note).
GRID_QUARTERS: dict[str, float] = {
    "1/4": 1.0,
    "1/8": 0.5,
    "1/16": 0.25,
    "1/8T": 1.0 / 3.0,
}
DEFAULT_GRID = "1/8"

PHRASE_BARS_MIN = 1
PHRASE_BARS_MAX = 8
DEFAULT_PHRASE_BARS = 2

DENSITY_MIN = 0.1
DENSITY_MAX = 1.0
DEFAULT_DENSITY = 0.75

GATE_BEATS_MIN = 0.05
GATE_BEATS_MAX = 4.0
DEFAULT_GATE_BEATS = 0.5

VELOCITY_MIN = 1
VELOCITY_MAX = 127
DEFAULT_VELOCITY = 90

OCTAVE_RANGE_MIN = 0
OCTAVE_RANGE_MAX = 2
DEFAULT_OCTAVE_RANGE = 1

BASE_OCTAVE_MIN = -2
BASE_OCTAVE_MAX = 2
DEFAULT_BASE_OCTAVE = 0

VARIATIONS: tuple[str, ...] = (
    "identity",
    "transpose",
    "invert",
    "retrograde",
    "thin",
    "ornament",
    "rhythm",
    "random",
)
DEFAULT_VARIATION = "identity"

VARIATION_BARS_MIN = 0
VARIATION_BARS_MAX = 16
DEFAULT_VARIATION_BARS = 0

DEFAULT_SEED = 1
SEED_MAX = 2**31 - 1
DEFAULT_ENABLED = False
DEFAULT_SWING = 0.0
DEFAULT_GROOVE_LINK = True

#: Bar downbeats are accented by this many velocity units (clamped to 127).
ACCENT_BOOST = 15
#: No more than this many consecutive empty grid steps are allowed.
MAX_EMPTY_RUN = 3
#: Stepwise bias: seven of these eight moves are +/-1 (about 70% +-1 dst).
_MOVE_DELTAS: tuple[int, ...] = (-2, -1, -1, -1, 1, 1, 1, 2)
#: Chance of an extra octave move when ``octave_range > 0``.
_OCTAVE_JUMP_CHANCE = 0.15
#: Below this many MIDI notes per second the scheduler is behind, not early.
_MIN_GATE_SECONDS = 0.001

_FUNDAMENTAL_DEFAULT = 220.0
#: Placeholder classes used to shape the motif before the first real set
#: snapshot arrives (the first ``on_set`` re-births the motif from the real set).
_DEFAULT_RATIOS: tuple[float, ...] = (
    1.0,
    9.0 / 8.0,
    5.0 / 4.0,
    4.0 / 3.0,
    3.0 / 2.0,
    5.0 / 3.0,
    15.0 / 8.0,
)

_MASK64 = (1 << 64) - 1

_SETTING_ALIASES: dict[str, str] = {
    "enabled": "enabled",
    "grid": "grid",
    "phrase_bars": "phrase_bars",
    "bars": "phrase_bars",
    "phrase": "phrase_bars",
    "density": "density",
    "gate_beats": "gate_beats",
    "gate": "gate_beats",
    "velocity": "velocity",
    "octave_range": "octave_range",
    "octaves": "octave_range",
    "base_octave": "base_octave",
    "base": "base_octave",
    "variation": "variation",
    "variation_bars": "variation_bars",
    "var_every": "variation_bars",
    "swing": "swing",
    "groove_link": "groove_link",
    "link": "groove_link",
    "seed": "seed",
}

#: Setting names that regenerate the motif from the seed.
_GENERATION_KEYS = frozenset(
    {"seed", "grid", "phrase_bars", "density", "octave_range"}
)


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


def _as_int(value: object, label: str) -> int:
    if isinstance(value, bool):
        raise TypeError(f"{label} must be an integer")
    numeric = _finite(value, label)
    return int(round(numeric))


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
    """Return one of ``1/4``, ``1/8``, ``1/16``, ``1/8T``."""

    text = str(value).strip().upper().replace(" ", "")
    if text not in GRID_QUARTERS:
        raise ValueError("grid must be one of " + ", ".join(GRIDS))
    return text


def grid_quarters(value: object) -> float:
    """Quarter notes per grid step (1, 0.5, 0.25 or 1/3)."""

    return GRID_QUARTERS[validate_grid(value)]


def steps_per_beat(value: object) -> int:
    """Whole grid steps per quarter note (1, 2, 4 or 3 for 1/8T)."""

    return max(1, int(round(1.0 / grid_quarters(value))))


def steps_per_bar(
    meter_num: object = 4, meter_den: object = 4, grid: object = DEFAULT_GRID
) -> int:
    """Grid steps in one bar for the meter and grid."""

    numerator = rhythm.clamp_meter_numerator(meter_num)
    denominator = rhythm.validate_meter_denominator(meter_den)
    bar = rhythm.bar_quarters(numerator, denominator)
    return max(1, int(round(bar / grid_quarters(grid))))


def validate_phrase_bars(value: object) -> int:
    """Return the phrase length in bars, 1..8."""

    number = _as_int(value, "phrase_bars")
    if not PHRASE_BARS_MIN <= number <= PHRASE_BARS_MAX:
        raise ValueError(
            f"phrase_bars must be {PHRASE_BARS_MIN}..{PHRASE_BARS_MAX}"
        )
    return number


def validate_density(value: object) -> float:
    """Return the note density clamped to ``0.1..1.0``."""

    return max(DENSITY_MIN, min(DENSITY_MAX, _finite(value, "density")))


def validate_gate_beats(value: object) -> float:
    """Return the gate length in quarter beats, clamped to ``0.05..4.0``."""

    return max(
        GATE_BEATS_MIN, min(GATE_BEATS_MAX, _finite(value, "gate_beats"))
    )


def validate_velocity(value: object, label: str = "velocity") -> int:
    """Return a MIDI velocity clamped to ``1..127``."""

    return max(VELOCITY_MIN, min(VELOCITY_MAX, _as_int(value, label)))


def validate_octave_range(value: object) -> int:
    """Return the octave travel range, 0..2."""

    number = _as_int(value, "octave_range")
    if not OCTAVE_RANGE_MIN <= number <= OCTAVE_RANGE_MAX:
        raise ValueError(
            f"octave_range must be {OCTAVE_RANGE_MIN}..{OCTAVE_RANGE_MAX}"
        )
    return number


def validate_base_octave(value: object) -> int:
    """Return the base octave offset, -2..+2."""

    number = _as_int(value, "base_octave")
    if not BASE_OCTAVE_MIN <= number <= BASE_OCTAVE_MAX:
        raise ValueError(
            f"base_octave must be {BASE_OCTAVE_MIN}..{BASE_OCTAVE_MAX}"
        )
    return number


def validate_variation(value: object) -> str:
    """Return one of the eight documented variation names."""

    text = str(value).strip().lower()
    if text not in VARIATIONS:
        raise ValueError("variation must be one of " + ", ".join(VARIATIONS))
    return text


def validate_variation_bars(value: object) -> int:
    """Return the automatic variation period in bars, clamped to ``0..16``."""

    return max(
        VARIATION_BARS_MIN,
        min(VARIATION_BARS_MAX, _as_int(value, "variation_bars")),
    )


def validate_seed(value: object) -> int:
    """Return an integer seed clamped to ``0..2**31-1``."""

    return max(0, min(SEED_MAX, _as_int(value, "seed")))


def validate_channel(value: object, label: str = "member_channel") -> int:
    """Return an MPE member channel, 2..16 (1 is the master channel)."""

    number = _as_int(value, label)
    if not 2 <= number <= 16:
        raise ValueError(f"{label} must be 2..16 (1 is the MPE master channel)")
    return number


def normalize_setting_name(name: object) -> str:
    """Map a short or long setting name to its canonical field name."""

    key = str(name).strip().lower()
    canonical = _SETTING_ALIASES.get(key)
    if canonical is None:
        raise ValueError(
            f"unknown lead setting {name!r}; expected enabled, grid, "
            "phrase_bars, density, gate_beats, velocity, octave_range, "
            "base_octave, variation, variation_bars, swing, groove_link, seed"
        )
    return canonical


def _clean_ratios(ratios: Sequence[float]) -> tuple[float, ...]:
    """Sorted, de-duplicated, positive ratios; defaults to ``(1.0,)``."""

    if isinstance(ratios, (str, bytes)):
        raise TypeError("ratios must be an iterable of real numbers")
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
    return tuple(cleaned) or (1.0,)


def _match_ratio(ratios: Sequence[float], hint: float) -> float | None:
    """Return the current class close to ``hint``, or ``None``."""

    for ratio in ratios:
        if math.isclose(ratio, hint, rel_tol=1e-12, abs_tol=1e-12):
            return float(ratio)
    return None


def _degree_of(anchor: float | None, ratios: Sequence[float]) -> int:
    """Position of the anchor in the sorted set, else 0."""

    if anchor is not None:
        try:
            value = float(anchor)
        except (TypeError, ValueError):
            value = None
        if value is not None:
            index = _match_ratio_index(ratios, value)
            if index is not None:
                return index
    return 0


def _match_ratio_index(ratios: Sequence[float], value: float) -> int | None:
    for index, ratio in enumerate(ratios):
        if math.isclose(ratio, value, rel_tol=1e-12, abs_tol=1e-12):
            return index
    return None


# ---------------------------------------------------------------------------
# Settings / config
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LeadSettings:
    """Validated, immutable lead performance controls."""

    enabled: bool = DEFAULT_ENABLED
    grid: str = DEFAULT_GRID
    phrase_bars: int = DEFAULT_PHRASE_BARS
    density: float = DEFAULT_DENSITY
    gate_beats: float = DEFAULT_GATE_BEATS
    velocity: int = DEFAULT_VELOCITY
    octave_range: int = DEFAULT_OCTAVE_RANGE
    base_octave: int = DEFAULT_BASE_OCTAVE
    variation: str = DEFAULT_VARIATION
    variation_bars: int = DEFAULT_VARIATION_BARS
    swing: float = DEFAULT_SWING
    groove_link: bool = DEFAULT_GROOVE_LINK
    seed: int = DEFAULT_SEED

    @classmethod
    def create(cls, **values: Any) -> "LeadSettings":
        """Build settings with every value validated/normalised."""

        defaults = cls()
        return cls(
            enabled=validate_bool(values.get("enabled", defaults.enabled), "enabled"),
            grid=validate_grid(values.get("grid", defaults.grid)),
            phrase_bars=validate_phrase_bars(
                values.get("phrase_bars", defaults.phrase_bars)
            ),
            density=validate_density(values.get("density", defaults.density)),
            gate_beats=validate_gate_beats(
                values.get("gate_beats", defaults.gate_beats)
            ),
            velocity=validate_velocity(values.get("velocity", defaults.velocity)),
            octave_range=validate_octave_range(
                values.get("octave_range", defaults.octave_range)
            ),
            base_octave=validate_base_octave(
                values.get("base_octave", defaults.base_octave)
            ),
            variation=validate_variation(
                values.get("variation", defaults.variation)
            ),
            variation_bars=validate_variation_bars(
                values.get("variation_bars", defaults.variation_bars)
            ),
            swing=clamp_unit(values.get("swing", defaults.swing), "swing"),
            groove_link=validate_bool(
                values.get("groove_link", defaults.groove_link), "groove_link"
            ),
            seed=validate_seed(values.get("seed", defaults.seed)),
        )

    def state_args(
        self,
        port_ok: bool,
        playing: bool,
        variation_index: int = 0,
    ) -> list[Any]:
        """``/ji/lead/state`` payload (sixteen values, documented order)."""

        return [
            int(self.enabled),
            int(bool(port_ok)),
            self.grid,
            int(self.phrase_bars),
            float(self.density),
            float(self.gate_beats),
            int(self.velocity),
            int(self.octave_range),
            int(self.base_octave),
            self.variation,
            int(variation_index),
            int(self.variation_bars),
            float(self.swing),
            int(self.groove_link),
            int(self.seed),
            int(bool(playing)),
        ]

    def with_change(self, name: object, value: object) -> "LeadSettings":
        """Return settings with one validated change applied."""

        key = normalize_setting_name(name)
        if key == "enabled":
            return replace(self, enabled=validate_bool(value, "enabled"))
        if key == "grid":
            return replace(self, grid=validate_grid(value))
        if key == "phrase_bars":
            return replace(self, phrase_bars=validate_phrase_bars(value))
        if key == "density":
            return replace(self, density=validate_density(value))
        if key == "gate_beats":
            return replace(self, gate_beats=validate_gate_beats(value))
        if key == "velocity":
            return replace(self, velocity=validate_velocity(value))
        if key == "octave_range":
            return replace(self, octave_range=validate_octave_range(value))
        if key == "base_octave":
            return replace(self, base_octave=validate_base_octave(value))
        if key == "variation":
            return replace(self, variation=validate_variation(value))
        if key == "variation_bars":
            return replace(self, variation_bars=validate_variation_bars(value))
        if key == "swing":
            return replace(self, swing=clamp_unit(value, "swing"))
        if key == "groove_link":
            return replace(self, groove_link=validate_bool(value, "groove_link"))
        return replace(self, seed=validate_seed(value))


@dataclass(frozen=True, slots=True)
class LeadConfig:
    """Validated ``config/lead_config.json``: output plus default settings."""

    port: str = DEFAULT_PORT
    member_channel: int = DEFAULT_MEMBER_CHANNEL
    bend_range_semitones: float = DEFAULT_BEND_RANGE_SEMITONES
    settings: LeadSettings = LeadSettings()

    def __post_init__(self) -> None:
        if not isinstance(self.port, str) or not self.port.strip():
            raise ValueError("port must be a non-empty string")
        if not isinstance(self.settings, LeadSettings):
            raise TypeError("settings must be a LeadSettings")
        validate_channel(self.member_channel)
        bend_range = float(self.bend_range_semitones)
        if not math.isfinite(bend_range) or bend_range <= 0.0:
            raise ValueError("bend_range_semitones must be finite and positive")

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "LeadConfig":
        if not isinstance(data, Mapping):
            raise TypeError("lead config must be a mapping")
        values = {
            key: data[key]
            for key in (
                "enabled",
                "grid",
                "phrase_bars",
                "density",
                "gate_beats",
                "velocity",
                "octave_range",
                "base_octave",
                "variation",
                "variation_bars",
                "swing",
                "groove_link",
                "seed",
            )
            if key in data
        }
        return cls(
            port=str(data.get("port", DEFAULT_PORT)),
            member_channel=validate_channel(
                data.get("member_channel", DEFAULT_MEMBER_CHANNEL)
            ),
            bend_range_semitones=_finite(
                data.get("bend_range_semitones", DEFAULT_BEND_RANGE_SEMITONES),
                "bend_range_semitones",
            ),
            settings=LeadSettings.create(**values),
        )

    @classmethod
    def from_json(cls, path: str | Path) -> "LeadConfig":
        return load_config(path)


def load_config(path: str | Path = DEFAULT_CONFIG_PATH) -> LeadConfig:
    """Load and validate the lead config (same path fallback as the others)."""

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
        raise FileNotFoundError(f"lead config not found: {candidate}") from None

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in {candidate}: {exc}") from exc
    return LeadConfig.from_mapping(data)


# ---------------------------------------------------------------------------
# Motif / notes
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LeadNote:
    """One motif note stored in set-relative terms.

    Attributes:
        step: grid step inside the phrase (0-based).
        length: grid steps held (clamped to the next note start).
        degree: position in the sorted class list at birth.
        octave: octave offset around the lead base octave.
        ratio_hint: the ratio the note was born on.
        velocity: note-on velocity at birth (downbeats are accented).
    """

    step: int
    length: int
    degree: int
    octave: int
    ratio_hint: float
    velocity: int

    @property
    def end(self) -> int:
        return self.step + max(1, self.length)


@dataclass(frozen=True, slots=True)
class LeadMotif:
    """A generated phrase: notes plus the parameters that shaped it."""

    notes: tuple[LeadNote, ...]
    phrase_bars: int
    grid: str
    steps_per_bar: int
    phrase_steps: int
    density: float
    octave_range: int
    gate_beats: float
    velocity_base: int
    seed: int
    variation: str = DEFAULT_VARIATION
    variation_index: int = 0
    meter_num: int = 4
    meter_den: int = 4

    @property
    def steps(self) -> tuple[int, ...]:
        return tuple(note.step for note in self.notes)

    @property
    def degrees(self) -> tuple[int, ...]:
        return tuple(note.degree for note in self.notes)

    @property
    def note_count(self) -> int:
        return len(self.notes)

    def note_at(self, step: int) -> LeadNote | None:
        """Return the note that starts on ``step`` (no wrap)."""

        for note in self.notes:
            if note.step == step:
                return note
        return None


@dataclass(frozen=True, slots=True)
class LeadProjected:
    """A motif note resolved against the current set (ready for MIDI)."""

    step: int
    length: int
    degree: int
    octave: int
    ratio: float
    frequency: float
    velocity: int
    is_downbeat: bool


# ---------------------------------------------------------------------------
# Deterministic helpers (SplitMix64, stable across processes)
# ---------------------------------------------------------------------------


def _mix64(value: int) -> int:
    value = (value + 0x9E3779B97F4A7C15) & _MASK64
    value = ((value ^ (value >> 30)) * 0xBF58476D1CE4E5B9) & _MASK64
    value = ((value ^ (value >> 27)) * 0x94D049BB133111EB) & _MASK64
    return (value ^ (value >> 31)) & _MASK64


def _salt_value(salt: str) -> int:
    value = 0xCBF29CE484222325
    for byte in salt.encode("utf-8"):
        value = ((value ^ byte) * 0x100000001B3) & _MASK64
    return value


def _rng(seed: int, index: int, salt: str) -> random.Random:
    key = _mix64(validate_seed(seed))
    key = _mix64(key ^ (int(index) & _MASK64))
    key = _mix64(key ^ _salt_value(salt))
    return random.Random(key)


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------


def place_steps(
    rng: random.Random,
    phrase_steps: int,
    steps_per_bar_value: int,
    density: float,
) -> list[bool]:
    """Return the trigger grid for one phrase.

    Rules (documented in ``docs/README_stage8_3d.md``):

    * step 0 of every bar is always on;
    * every other step is on with probability ``density``;
    * no more than :data:`MAX_EMPTY_RUN` consecutive steps stay empty.

    The random draws are consumed once per non-downbeat step, in order, so a
    lower density is a subset of a higher one for the same seed.
    """

    total = max(1, int(phrase_steps))
    bar = max(1, int(steps_per_bar_value))
    amount = max(0.0, min(1.0, float(density)))
    on = [False] * total
    for start in range(0, total, bar):
        on[start] = True
    for step in range(total):
        if on[step]:
            continue
        if rng.random() < amount:
            on[step] = True
    run = 0
    for step in range(total):
        if on[step]:
            run = 0
            continue
        run += 1
        if run > MAX_EMPTY_RUN:
            on[step] = True
            run = 0
    return on


def generate_motif(
    *,
    seed: int = DEFAULT_SEED,
    phrase_bars: int = DEFAULT_PHRASE_BARS,
    grid: str = DEFAULT_GRID,
    density: float = DEFAULT_DENSITY,
    meter_num: object = 4,
    meter_den: object = 4,
    ratios: Sequence[float] = _DEFAULT_RATIOS,
    anchor: float | None = 1.0,
    octave_range: int = DEFAULT_OCTAVE_RANGE,
    velocity: int = DEFAULT_VELOCITY,
    gate_beats: float = DEFAULT_GATE_BEATS,
    variation: str = DEFAULT_VARIATION,
    variation_index: int = 0,
) -> LeadMotif:
    """Build one deterministic motif from the seed and the shape parameters.

    The degree walk starts on the anchor's degree (when the anchor is in the
    set) and then moves by -2..+2 degrees per note with a stepwise bias, clamped
    to ``0..len(ratios)-1``.  When the walk would leave the set -- or when
    ``octave_range > 0`` and a seeded coin passes -- the *octave* moves instead
    of the degree, so the line never jumps out of the scale.
    """

    bars = validate_phrase_bars(phrase_bars)
    grid_name = validate_grid(grid)
    amount = validate_density(density)
    span = validate_octave_range(octave_range)
    base_velocity = validate_velocity(velocity)
    gate = validate_gate_beats(gate_beats)
    name = validate_variation(variation)
    index = max(0, int(variation_index))
    seed_value = validate_seed(seed)
    pool = _clean_ratios(ratios)
    size = len(pool)
    numerator = rhythm.clamp_meter_numerator(meter_num)
    denominator = rhythm.validate_meter_denominator(meter_den)
    bar_steps = steps_per_bar(numerator, denominator, grid_name)
    total = max(1, bars * bar_steps)

    placement_rng = _rng(seed_value, index, "lead-place")
    on = place_steps(placement_rng, total, bar_steps, amount)
    walk_rng = _rng(seed_value, index, "lead-degrees")

    degree = max(0, min(size - 1, _degree_of(anchor, pool)))
    octave = 0
    raw: list[tuple[int, int, int, int]] = []
    for step, active in enumerate(on):
        if not active:
            continue
        if raw:  # every note after the first walks
            delta = walk_rng.choice(_MOVE_DELTAS)
            target = degree + delta
            if 0 <= target < size:
                degree = target
            elif span > 0:
                shift = 1 if target >= size else -1
                moved = max(-span, min(span, octave + shift))
                if moved != octave:
                    octave = moved
                else:
                    degree = max(0, min(size - 1, target))
            else:
                degree = max(0, min(size - 1, target))
            if span > 0 and walk_rng.random() < _OCTAVE_JUMP_CHANCE:
                octave = max(
                    -span, min(span, octave + walk_rng.choice((-1, 1)))
                )
        note_velocity = base_velocity
        if step % bar_steps == 0:
            note_velocity = min(VELOCITY_MAX, base_velocity + ACCENT_BOOST)
        raw.append((step, degree, octave, note_velocity))

    gate_steps = max(1, int(round(gate / grid_quarters(grid_name))))
    notes: list[LeadNote] = []
    for position, (step, note_degree, note_octave, note_velocity) in enumerate(raw):
        next_step = raw[position + 1][0] if position + 1 < len(raw) else total
        length = max(1, min(gate_steps, next_step - step, total - step))
        notes.append(
            LeadNote(
                step=step,
                length=length,
                degree=note_degree,
                octave=note_octave,
                ratio_hint=pool[note_degree % size],
                velocity=note_velocity,
            )
        )

    return LeadMotif(
        notes=tuple(notes),
        phrase_bars=bars,
        grid=grid_name,
        steps_per_bar=bar_steps,
        phrase_steps=total,
        density=amount,
        octave_range=span,
        gate_beats=gate,
        velocity_base=base_velocity,
        seed=seed_value,
        variation=name,
        variation_index=index,
        meter_num=numerator,
        meter_den=denominator,
    )


# ---------------------------------------------------------------------------
# Variations
# ---------------------------------------------------------------------------


def _normalize(notes: Sequence[LeadNote], phrase_steps: int) -> tuple[LeadNote, ...]:
    """Sort by step, clamp lengths so notes never overlap, drop out-of-range."""

    kept = sorted(
        (note for note in notes if 0 <= note.step < phrase_steps),
        key=lambda note: note.step,
    )
    result: list[LeadNote] = []
    for position, note in enumerate(kept):
        next_step = (
            kept[position + 1].step if position + 1 < len(kept) else phrase_steps
        )
        span = max(1, min(note.length, next_step - note.step))
        result.append(replace(note, length=span))
    return tuple(result)


def _reshaped(
    motif: LeadMotif,
    notes: Sequence[LeadNote],
    name: str,
    index: int,
) -> LeadMotif:
    return replace(
        motif,
        notes=_normalize(notes, motif.phrase_steps),
        variation=name,
        variation_index=index,
    )


def apply_variation(
    name: object,
    motif: LeadMotif,
    *,
    seed: int | None = None,
    index: int | None = None,
    ratios: Sequence[float] | None = None,
) -> LeadMotif:
    """Return ``motif`` with one deterministic variation applied.

    ``identity`` keeps the notes; ``transpose``/``invert``/``ornament`` refresh
    the affected notes' ``ratio_hint`` from ``ratios`` so a pitch change is
    audible immediately (the projection prefers the hint).  Every transformation
    is seeded by ``(seed, variation_index)``, so the same settings always produce
    the same line.
    """

    shape = validate_variation(name)
    if not isinstance(motif, LeadMotif):
        raise TypeError("motif must be a LeadMotif")
    seed_value = validate_seed(motif.seed if seed is None else seed)
    step_index = max(0, int(motif.variation_index if index is None else index))
    pool = _clean_ratios(
        _DEFAULT_RATIOS if ratios is None else ratios
    )
    size = len(pool)
    rng = _rng(seed_value, step_index, f"lead-variation-{shape}")

    if shape == "identity":
        return replace(motif, variation=shape, variation_index=step_index)

    if shape == "random":
        return generate_motif(
            # Keep the derived seed inside the documented 0..2**31-1 range so
            # two different parents never clamp onto the same seed.
            seed=(_mix64(seed_value) ^ _mix64(step_index + 0x51ED270B))
            % (SEED_MAX + 1),
            phrase_bars=motif.phrase_bars,
            grid=motif.grid,
            density=motif.density,
            meter_num=motif.meter_num,
            meter_den=motif.meter_den,
            ratios=pool,
            anchor=(
                None
                if not motif.notes
                else _ratio_of_degree(pool, motif.notes[0].degree)
            ),
            octave_range=motif.octave_range,
            velocity=motif.velocity_base,
            gate_beats=motif.gate_beats,
            variation=shape,
            variation_index=step_index,
        )

    if shape == "transpose":
        delta = rng.choice((1, 2))
        notes = [
            _with_degree(note, (note.degree + delta) % size, pool)
            for note in motif.notes
        ]
        return _reshaped(motif, notes, shape, step_index)

    if shape == "invert":
        pivot = motif.notes[0].degree if motif.notes else 0
        notes = [
            _with_degree(note, (2 * pivot - note.degree) % size, pool)
            for note in motif.notes
        ]
        return _reshaped(motif, notes, shape, step_index)

    if shape == "retrograde":
        reversed_pitches = list(reversed(motif.notes))
        notes = [
            replace(
                note,
                degree=source.degree,
                octave=source.octave,
                ratio_hint=source.ratio_hint,
                velocity=source.velocity,
            )
            for note, source in zip(motif.notes, reversed_pitches)
        ]
        return _reshaped(motif, notes, shape, step_index)

    if shape == "thin":
        step_every = rng.choice((2, 3))
        notes = [
            note
            for position, note in enumerate(motif.notes)
            if position % step_every != step_every - 1
        ]
        return _reshaped(motif, notes, shape, step_index)

    if shape == "ornament":
        candidates: list[int] = []
        for position in range(len(motif.notes) - 1):
            current = motif.notes[position]
            following = motif.notes[position + 1]
            gap = following.step - current.end
            if gap >= 1 and abs(following.degree - current.degree) >= 2:
                candidates.append(position)
        if not candidates:
            for position in range(len(motif.notes) - 1):
                current = motif.notes[position]
                following = motif.notes[position + 1]
                if following.step - current.end >= 1:
                    candidates.append(position)
        if not candidates:
            return replace(motif, variation=shape, variation_index=step_index)
        position = candidates[rng.randrange(len(candidates))]
        current = motif.notes[position]
        following = motif.notes[position + 1]
        passing = (current.degree + following.degree) // 2
        insert_at = current.end
        notes = list(motif.notes)
        notes.insert(
            position + 1,
            LeadNote(
                step=insert_at,
                length=max(1, min(1, following.step - insert_at)),
                degree=passing % size,
                octave=current.octave,
                ratio_hint=_ratio_of_degree(pool, passing),
                velocity=motif.velocity_base,
            ),
        )
        return _reshaped(motif, notes, shape, step_index)

    # ``rhythm``: keep the pitch sequence, redraw which grid steps are on.
    placement = place_steps(
        rng, motif.phrase_steps, motif.steps_per_bar, motif.density
    )
    on_steps = [step for step, active in enumerate(placement) if active]
    gate_steps = max(1, int(round(motif.gate_beats / grid_quarters(motif.grid))))
    sources = list(motif.notes)
    notes = []
    for position, step in enumerate(on_steps):
        source = sources[position % len(sources)]
        next_step = on_steps[position + 1] if position + 1 < len(on_steps) else motif.phrase_steps
        notes.append(
            LeadNote(
                step=step,
                length=max(1, min(gate_steps, next_step - step)),
                degree=source.degree,
                octave=source.octave,
                ratio_hint=source.ratio_hint,
                velocity=(
                    min(VELOCITY_MAX, motif.velocity_base + ACCENT_BOOST)
                    if step % motif.steps_per_bar == 0
                    else motif.velocity_base
                ),
            )
        )
    return _reshaped(motif, notes, shape, step_index)


def _ratio_of_degree(pool: Sequence[float], degree: int) -> float:
    if not pool:
        return 1.0
    return float(pool[max(0, int(degree)) % len(pool)])


def _with_degree(note: LeadNote, degree: int, pool: Sequence[float]) -> LeadNote:
    return replace(
        note, degree=degree, ratio_hint=_ratio_of_degree(pool, degree)
    )


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class LeadEngine:
    """Thread-safe lead state: settings, motif, set snapshot, projection."""

    def __init__(
        self,
        config: LeadConfig,
        *,
        meter_num: object = 4,
        meter_den: object = 4,
        bpm: object = rhythm.BPM_DEFAULT,
    ) -> None:
        if not isinstance(config, LeadConfig):
            raise TypeError("config must be a LeadConfig")
        self.config = config
        self._lock = threading.RLock()
        self._settings = config.settings
        self._meter_num = rhythm.clamp_meter_numerator(meter_num)
        self._meter_den = rhythm.validate_meter_denominator(meter_den)
        self._bpm = rhythm.clamp_bpm(bpm)
        self._ratios: tuple[float, ...] = _clean_ratios(_DEFAULT_RATIOS)
        self._anchor: float | None = None
        self._fundamental = _FUNDAMENTAL_DEFAULT
        self._born = False
        self._variation_index = 0
        self._groove_swing = 0.0
        self._version = 0
        self._motif = self._generate_locked()
        self._projection: tuple[LeadProjected, ...] | None = None
        self._projection_version = -1

    # -- queries --------------------------------------------------------

    @property
    def version(self) -> int:
        with self._lock:
            return self._version

    @property
    def settings(self) -> LeadSettings:
        with self._lock:
            return self._settings

    @property
    def enabled(self) -> bool:
        with self._lock:
            return self._settings.enabled

    @property
    def bpm(self) -> float:
        with self._lock:
            return self._bpm

    @property
    def meter(self) -> str:
        with self._lock:
            return f"{self._meter_num}/{self._meter_den}"

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

    @property
    def motif(self) -> LeadMotif:
        with self._lock:
            return self._motif

    @property
    def groove_swing(self) -> float:
        with self._lock:
            return self._groove_swing

    @property
    def born(self) -> bool:
        """True once a real set snapshot defined the motif's degrees."""

        with self._lock:
            return self._born

    def effective_swing(self) -> float:
        """The swing the scheduler uses.

        With ``groove_link`` on this is the drum groove's live swing; otherwise
        it is the lead's own ``swing``.  There is one swing definition in the
        rig -- it is never forked.
        """

        with self._lock:
            if self._settings.groove_link:
                return float(self._groove_swing)
            return float(self._settings.swing)

    def description(self) -> str:
        with self._lock:
            settings = self._settings
            return (
                f"grid={settings.grid} bars={settings.phrase_bars} "
                f"density={settings.density:.2f} "
                f"gate={settings.gate_beats:.3f} beats "
                f"velocity={settings.velocity} "
                f"octaves={settings.octave_range} seed={settings.seed}"
            )

    def describe_phrase(self) -> str:
        """``seed=1 variation=identity #0 steps=16 notes=12``."""

        with self._lock:
            motif = self._motif
            return (
                f"seed={motif.seed} variation={motif.variation} "
                f"#{motif.variation_index} steps={motif.phrase_steps} "
                f"notes={motif.note_count}"
            )

    # -- set snapshot ---------------------------------------------------

    def on_set(
        self,
        ratios: Sequence[float],
        anchor: float | None,
        fundamental: float,
    ) -> LeadMotif:
        """Store a set snapshot and re-project; never regenerate after birth."""

        fund = _finite(fundamental, "fundamental")
        if fund <= 0.0:
            raise ValueError("fundamental must be strictly positive")
        pool = _clean_ratios(ratios)
        with self._lock:
            self._ratios = pool
            self._anchor = None if anchor is None else float(anchor)
            self._fundamental = fund
            if not self._born:
                # The first real snapshot births the motif: it defines the
                # degrees.  Every later snapshot only re-projects.
                self._born = True
                self._motif = self._generate_locked()
            self._version += 1
            self._projection = None
            return self._motif

    update = on_set

    def set_groove_swing(self, swing: object) -> float:
        """Record the drum groove's current swing (push, never polled)."""

        value = clamp_unit(swing, "groove_swing")
        with self._lock:
            if value == self._groove_swing:
                return self._groove_swing
            self._groove_swing = value
            return value

    # -- projection ------------------------------------------------------

    def projection(self) -> tuple[LeadProjected, ...]:
        """Motif notes resolved against the current set (cached per version)."""

        with self._lock:
            if (
                self._projection is not None
                and self._projection_version == self._version
            ):
                return self._projection
            self._projection = self._project_locked()
            self._projection_version = self._version
            return self._projection

    def _project_locked(self) -> tuple[LeadProjected, ...]:
        settings = self._settings
        ratios = self._ratios
        fundamental = self._fundamental
        base = 2.0 ** settings.base_octave
        motif = self._motif
        projected: list[LeadProjected] = []
        for note in motif.notes:
            ratio = _match_ratio(ratios, note.ratio_hint)
            if ratio is None:
                ratio = float(ratios[note.degree % len(ratios)])
            velocity = settings.velocity + (
                int(note.velocity) - int(motif.velocity_base)
            )
            projected.append(
                LeadProjected(
                    step=note.step,
                    length=note.length,
                    degree=note.degree,
                    octave=note.octave,
                    ratio=ratio,
                    frequency=fundamental
                    * ratio
                    * (2.0 ** note.octave)
                    * base,
                    velocity=max(VELOCITY_MIN, min(VELOCITY_MAX, velocity)),
                    is_downbeat=(note.step % max(1, motif.steps_per_bar)) == 0,
                )
            )
        return tuple(projected)

    def note_at(self, step: int) -> LeadProjected | None:
        """The projected note starting at ``step``, or ``None``."""

        for note in self.projection():
            if note.step == int(step):
                return note
        return None

    def timing(self) -> tuple[LeadSettings, LeadMotif]:
        with self._lock:
            return self._settings, self._motif

    @property
    def steps_per_bar(self) -> int:
        with self._lock:
            return steps_per_bar(
                self._meter_num, self._meter_den, self._settings.grid
            )

    @property
    def phrase_steps(self) -> int:
        with self._lock:
            return self._motif.phrase_steps

    # -- mutations (immediate at the engine level) ----------------------

    def set_setting(self, name: object, value: object) -> LeadSettings:
        """Apply one validated setting.

        Shape parameters regenerate the motif; ``variation`` applies that
        variation immediately; everything else is playback-only.
        """

        key = normalize_setting_name(name)
        with self._lock:
            before = self._settings
            after = before.with_change(key, value)
            if after == before:
                return before
            self._settings = after
            if key in _GENERATION_KEYS:
                if key == "seed":
                    self._variation_index = 0
                self._motif = self._generate_locked()
            elif key == "variation":
                self._motif = self._apply_variation_locked(after.variation)
            self._version += 1
            self._projection = None
            return after

    def set_settings(self, settings: LeadSettings) -> LeadSettings:
        if not isinstance(settings, LeadSettings):
            raise TypeError("settings must be a LeadSettings")
        with self._lock:
            before = self._settings
            if settings == before:
                return before
            regenerate = any(
                getattr(settings, key) != getattr(before, key)
                for key in _GENERATION_KEYS
            )
            self._settings = settings
            if settings.seed != before.seed:
                self._variation_index = 0
            if regenerate:
                self._motif = self._generate_locked()
            self._version += 1
            self._projection = None
            return settings

    def set_enabled(self, enabled: object) -> bool:
        with self._lock:
            self._settings = replace(
                self._settings, enabled=validate_bool(enabled, "enabled")
            )
            self._version += 1
            return self._settings.enabled

    def set_bpm(self, bpm: object) -> float:
        value = rhythm.clamp_bpm(bpm)
        with self._lock:
            self._bpm = value
            return value

    def set_meter(self, meter_num: object, meter_den: object) -> int:
        """Change the meter; returns the (possibly new) phrase length in steps."""

        numerator = rhythm.clamp_meter_numerator(meter_num)
        denominator = rhythm.validate_meter_denominator(meter_den)
        with self._lock:
            if (numerator, denominator) != (self._meter_num, self._meter_den):
                self._meter_num = numerator
                self._meter_den = denominator
                self._motif = self._generate_locked()
                self._version += 1
                self._projection = None
            return self._motif.phrase_steps

    # -- variations ------------------------------------------------------

    def vary(self) -> LeadMotif:
        """Advance ``variation_index`` by one and apply that variation."""

        with self._lock:
            return self._vary_locked()

    def maybe_auto_vary(self, bar_index: int) -> LeadMotif | None:
        """Apply the next variation when ``variation_bars`` says it is due."""

        with self._lock:
            period = int(self._settings.variation_bars)
            if period <= 0:
                return None
            bar = int(bar_index)
            if bar <= 0 or bar % period != 0:
                return None
            return self._vary_locked()

    def _vary_locked(self) -> LeadMotif:
        self._variation_index += 1
        self._motif = self._apply_variation_locked(self._settings.variation)
        self._version += 1
        self._projection = None
        return self._motif

    def _apply_variation_locked(self, name: str) -> LeadMotif:
        return apply_variation(
            name,
            self._motif,
            seed=self._settings.seed,
            index=self._variation_index,
            ratios=self._ratios,
        )

    def _generate_locked(self) -> LeadMotif:
        settings = self._settings
        motif = generate_motif(
            seed=settings.seed,
            phrase_bars=settings.phrase_bars,
            grid=settings.grid,
            density=settings.density,
            meter_num=self._meter_num,
            meter_den=self._meter_den,
            ratios=self._ratios,
            anchor=self._anchor,
            octave_range=settings.octave_range,
            velocity=settings.velocity,
            gate_beats=settings.gate_beats,
            variation=settings.variation,
            variation_index=self._variation_index,
        )
        if settings.variation != "identity" and self._born:
            # A regenerated phrase keeps the selected variation, so the state
            # label and the notes never disagree after a shape change.
            motif = apply_variation(
                settings.variation,
                motif,
                seed=settings.seed,
                index=self._variation_index,
                ratios=self._ratios,
            )
        return motif

    # -- status ----------------------------------------------------------

    def state_args(self, port_ok: bool, playing: bool) -> list[Any]:
        """``/ji/lead/state`` payload (sixteen values, documented order)."""

        with self._lock:
            return self._settings.state_args(
                port_ok, playing, self._motif.variation_index
            )


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------


class LeadScheduler:
    """Daemon-thread, transport-gated, drift-corrected lead sequencer.

    The thread owns monotonic deadlines and sends pitch bend before each
    note-on.  A gate timer sends the note-off; a retrigger cancels the pending
    timer and note-offs the held note first, so a late gate can never cut the
    next note.  Step 0 is the phrase origin: starting the scheduler is what
    makes the phrase restart on a transport start.
    """

    def __init__(
        self,
        output: Any,
        engine: LeadEngine,
        *,
        bpm: object = rhythm.BPM_DEFAULT,
        now: Callable[[], float] = time.monotonic,
        on_note: Callable[[LeadProjected, int], None] | None = None,
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
        self._total_steps = 0
        self._gate_timer: threading.Timer | None = None
        self._gate_token = 0
        self._held: int | None = None

    # -- state ----------------------------------------------------------

    @property
    def bpm(self) -> float:
        with self._lock:
            return self._bpm

    @property
    def step_index(self) -> int:
        with self._lock:
            return self._step_index

    @property
    def total_steps(self) -> int:
        with self._lock:
            return self._total_steps

    @property
    def is_running(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    def set_bpm(self, bpm: object) -> float:
        value = rhythm.clamp_bpm(bpm)
        with self._lock:
            self._bpm = value
            return value

    # -- lifecycle -------------------------------------------------------

    def start(self) -> None:
        """Start at the phrase origin (step 0).  Idempotent while running."""

        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            if not getattr(self.output, "is_open", False):
                raise RuntimeError("lead output is not open")
            self._stop.clear()
            self._step_index = 0
            self._total_steps = 0
            thread = threading.Thread(
                target=self._run, name="ji-lead", daemon=True
            )
            self._thread = thread
        thread.start()

    def stop(self) -> None:
        """Stop the thread, cancel a pending gate, note-off and send CC 123."""

        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        with self._lock:
            if self._thread is thread:
                self._thread = None
            self._cancel_gate_locked()
            held = self._held
            self._held = None
        self._send_off(held)
        try:
            self.output.all_notes_off()
        except Exception:
            pass

    # -- thread body -----------------------------------------------------

    def _run(self) -> None:  # pragma: no cover - exercised with a fake output
        deadline = self._now()
        total = 0
        last_bar = -1
        try:
            while not self._stop.is_set():
                settings, motif = self.engine.timing()
                bpm = self.bpm
                beat_seconds = 60.0 / bpm
                step_quarters = grid_quarters(settings.grid)
                step_seconds = beat_seconds * step_quarters
                phrase_steps = max(1, motif.phrase_steps)
                step = total % phrase_steps
                offset = swing_offset(
                    step,
                    self.engine.effective_swing(),
                    step_seconds,
                    steps_per_beat(settings.grid),
                )
                target = deadline + offset
                now = self._now()
                if target > now:
                    if self._stop.wait(target - now):
                        break
                elif self._stop.is_set():
                    break

                bar = total // max(1, motif.steps_per_bar)
                if bar != last_bar:
                    last_bar = bar
                    if bar > 0:
                        varied = self.engine.maybe_auto_vary(bar)
                        if varied is not None:
                            print(
                                f"[LEAD] variation={varied.variation} "
                                f"#{varied.variation_index} "
                                f"(auto at bar {bar}, "
                                f"phrase {varied.phrase_bars} bars)"
                            )

                note = self.engine.note_at(step)
                if note is not None:
                    self._play(note, settings, beat_seconds, step_quarters)

                with self._lock:
                    self._step_index = step
                    self._total_steps = total
                total += 1
                deadline += step_seconds
                now = self._now()
                if deadline < now:
                    # Fell behind: resume from now; never burst through steps.
                    deadline = now
                wait = deadline - now
                if wait > 0.0 and self._stop.wait(wait):
                    break
        except Exception as exc:  # a broken output must not crash quietly
            print(f"[LEAD] scheduler error: {exc}", file=sys.stderr)
        finally:
            with self._lock:
                self._cancel_gate_locked()
                held = self._held
                self._held = None
            self._send_off(held)

    def _play(
        self,
        note: LeadProjected,
        settings: LeadSettings,
        beat_seconds: float,
        step_quarters: float,
    ) -> None:
        config = self.engine.config
        pitch = frequency_to_pitch(note.frequency, config.bend_range_semitones)

        with self._lock:
            self._cancel_gate_locked()
            held = self._held
            self._held = None
        self._send_off(held)

        self.output.pitch_bend(pitch.bend)
        self.output.note_on(pitch.note, int(note.velocity))
        with self._lock:
            self._held = int(pitch.note)

        gate_beats = min(
            float(settings.gate_beats), note.length * step_quarters
        )
        self._schedule_gate(max(_MIN_GATE_SECONDS, gate_beats * beat_seconds))

        callback = self._on_note
        if callback is not None:
            try:
                callback(note, int(pitch.note))
            except Exception as exc:  # status is best-effort
                print(f"[LEAD] note callback failed: {exc!r}", file=sys.stderr)

    # -- gates -----------------------------------------------------------

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
            held = self._held
            self._held = None
        self._send_off(held)

    def _send_off(self, note: int | None) -> None:
        if note is None:
            return
        try:
            self.output.note_off(note)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Rig
# ---------------------------------------------------------------------------


class LeadRig:
    """Own the lead output, engine, and scheduler behind one small API."""

    def __init__(
        self,
        config: LeadConfig | Mapping[str, Any] | str | Path | None = None,
        *,
        config_path: str | Path | None = None,
        output: Any | None = None,
        meter_num: object = 4,
        meter_den: object = 4,
        bpm: object = rhythm.BPM_DEFAULT,
    ) -> None:
        try:
            from .mpe_output import MPEOutput
        except ImportError:  # pragma: no cover - direct execution
            from mpe_output import MPEOutput  # type: ignore[no-redef]

        if config is None:
            config = config_path if config_path is not None else DEFAULT_CONFIG_PATH
        if isinstance(config, LeadConfig):
            cfg = config
        elif isinstance(config, (str, Path)):
            cfg = load_config(config)
        elif isinstance(config, Mapping):
            cfg = LeadConfig.from_mapping(config)
        else:
            raise TypeError("config must be a LeadConfig, mapping, path, or None")

        self.config = cfg
        self.engine = LeadEngine(
            cfg, meter_num=meter_num, meter_den=meter_den, bpm=bpm
        )
        self.output = MPEOutput(
            cfg.port,
            member_channel=cfg.member_channel,
            bend_range_semitones=cfg.bend_range_semitones,
            velocity=cfg.settings.velocity,
            output=output,
            label="lead",
        )
        self.scheduler = LeadScheduler(self.output, self.engine, bpm=bpm)
        self._port_ok = output is not None
        self._started = False
        self._transport_running = False
        self._last_note_sent: tuple[float, int] | None = None

    # -- lifecycle ------------------------------------------------------

    def start(self) -> bool:
        """Open Bus 5; a missing/unopenable port disables only the lead."""

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
                    f"[LEAD] disabled: {self.config.port} not available; "
                    "drone/arp/drums/bass continue",
                    file=sys.stderr,
                )
                print(f"[LEAD] reason: {exc}", file=sys.stderr)
                return False
        self._port_ok = True
        print(
            f"[LEAD] port={self.config.port} "
            f"channel={self.config.member_channel} "
            f"bend={self.config.bend_range_semitones:g} "
            f"{self.engine.description()}"
        )
        print(f"[LEAD] phrase {self.engine.describe_phrase()}")
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

    @property
    def playing(self) -> bool:
        return bool(
            self._port_ok
            and self._transport_running
            and self.engine.enabled
            and self.scheduler.is_running
        )

    def state_args(self) -> list[Any]:
        return self.engine.state_args(self._port_ok, self.playing)

    # -- transport / tempo / meter --------------------------------------

    def set_transport(self, running: object) -> None:
        """Transport-gated exactly like the drums/bass (the arp is not)."""

        self._transport_running = bool(running)
        if self._transport_running:
            if self._port_ok and self.engine.enabled:
                self.scheduler.start()
        else:
            if self.scheduler.is_running:
                self.scheduler.stop()

    def set_bpm(self, bpm: object) -> float:
        value = self.scheduler.set_bpm(bpm)
        self.engine.set_bpm(value)
        return value

    def set_meter(self, meter_num: object, meter_den: object) -> int:
        before = self.engine.phrase_steps
        steps = self.engine.set_meter(meter_num, meter_den)
        if steps != before:
            print(
                f"[LEAD] phrase regenerated for meter={self.engine.meter} "
                f"steps={steps}"
            )
            print(f"[LEAD] phrase {self.engine.describe_phrase()}")
        return steps

    def set_groove_swing(self, swing: object) -> float:
        return self.engine.set_groove_swing(swing)

    # -- settings -------------------------------------------------------

    def set_setting(self, name: object, value: object) -> LeadSettings:
        key = normalize_setting_name(name)
        if key == "enabled":
            enabled = self.engine.set_enabled(value)
            if self._port_ok:
                if enabled and self._transport_running:
                    self.scheduler.start()
                elif not enabled and self.scheduler.is_running:
                    self.scheduler.stop()
            print(f"[LEAD] {'enabled' if enabled else 'disabled'}")
            return self.engine.settings
        before = self.engine.settings
        settings = self.engine.set_setting(key, value)
        if key == "variation":
            motif = self.engine.motif
            print(
                f"[LEAD] variation={motif.variation} #{motif.variation_index} "
                f"(phrase {motif.phrase_bars} bars)"
            )
        elif key in _GENERATION_KEYS and settings != before:
            print(f"[LEAD] phrase {self.engine.describe_phrase()}")
        return settings

    def set_enabled(self, enabled: object) -> bool:
        self.set_setting("enabled", enabled)
        return self.engine.enabled

    def set_variation(self, name: object) -> LeadSettings:
        return self.set_setting("variation", name)

    def vary(self) -> LeadMotif:
        """Advance one variation (manual trigger)."""

        motif = self.engine.vary()
        print(
            f"[LEAD] variation={motif.variation} #{motif.variation_index} "
            f"(phrase {motif.phrase_bars} bars)"
        )
        return motif

    def on_set(
        self,
        ratios: Sequence[float],
        anchor: float | None,
        fundamental: float,
    ) -> LeadMotif:
        return self.engine.on_set(ratios, anchor, fundamental)

    def set_note_callback(
        self, callback: Callable[[float, int, int], None] | None
    ) -> None:
        """Install a status callback ``(hz, midi_note, velocity)``."""

        def _relay(note: LeadProjected, midi_note: int) -> None:
            if callback is None:
                return
            key = (round(note.frequency, 6), int(midi_note))
            if key == self._last_note_sent:
                return
            self._last_note_sent = key
            callback(note.frequency, int(midi_note), int(note.velocity))

        self.scheduler._on_note = _relay if callback is not None else None


__all__ = [
    "ACCENT_BOOST",
    "BASE_OCTAVE_MAX",
    "BASE_OCTAVE_MIN",
    "DEFAULT_BEND_RANGE_SEMITONES",
    "DEFAULT_CONFIG_PATH",
    "DEFAULT_DENSITY",
    "DEFAULT_ENABLED",
    "DEFAULT_GATE_BEATS",
    "DEFAULT_GRID",
    "DEFAULT_GROOVE_LINK",
    "DEFAULT_MEMBER_CHANNEL",
    "DEFAULT_OCTAVE_RANGE",
    "DEFAULT_PHRASE_BARS",
    "DEFAULT_PORT",
    "DEFAULT_SEED",
    "DEFAULT_VARIATION",
    "DEFAULT_VARIATION_BARS",
    "DEFAULT_VELOCITY",
    "DENSITY_MAX",
    "DENSITY_MIN",
    "GATE_BEATS_MAX",
    "GATE_BEATS_MIN",
    "GRIDS",
    "GRID_QUARTERS",
    "LeadConfig",
    "LeadEngine",
    "LeadMotif",
    "LeadNote",
    "LeadProjected",
    "LeadRig",
    "LeadScheduler",
    "LeadSettings",
    "MAX_EMPTY_RUN",
    "OCTAVE_RANGE_MAX",
    "OCTAVE_RANGE_MIN",
    "PHRASE_BARS_MAX",
    "PHRASE_BARS_MIN",
    "SEED_MAX",
    "VARIATIONS",
    "VARIATION_BARS_MAX",
    "VARIATION_BARS_MIN",
    "apply_variation",
    "clamp_unit",
    "generate_motif",
    "grid_quarters",
    "load_config",
    "normalize_setting_name",
    "place_steps",
    "steps_per_bar",
    "steps_per_beat",
    "validate_base_octave",
    "validate_bool",
    "validate_channel",
    "validate_density",
    "validate_gate_beats",
    "validate_grid",
    "validate_octave_range",
    "validate_phrase_bars",
    "validate_seed",
    "validate_variation",
    "validate_variation_bars",
    "validate_velocity",
]

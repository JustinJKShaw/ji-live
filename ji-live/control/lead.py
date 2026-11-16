"""Stage 8.3d monophonic JI lead/melody voice.

Stage 9.2 adds the performance layer on top of the 9.1 played take: input
override (a played key takes the lead, the loop's sounding note is released and
the loop's attack resumes on key-up), and per-note pitch-bend/mod-wheel curves
(``LeadNote.bend_points`` / ``mod_points``) that the scheduler replays.  Both
are Python-only: no new bus, SynthDef, sclang change or file-format version.


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
    from .mpe_output import bend_for_frequency, frequency_to_pitch
except ImportError:  # pragma: no cover - exercised only by direct execution
    import rhythm  # type: ignore[no-redef]
    from bass import swing_offset  # type: ignore[no-redef]
    from mpe_output import bend_for_frequency, frequency_to_pitch  # type: ignore[no-redef]

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

GATE_MODES: tuple[str, ...] = ("beat", "note")
DEFAULT_GATE_MODE = "beat"

#: Who drives Bus 5 (Stage 9.1).  ``generated`` runs the seeded line, ``played``
#: plays the recorded take, and ``live``/``record`` stop the sequencer and let
#: the input rig monitor/capture.
SOURCES: tuple[str, ...] = ("generated", "played", "live", "record")
DEFAULT_SOURCE = "generated"

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

#: Stage 9.2 expression curves: at most this many points per curve, and the
#: widest bend a point may ask for (48 semitones, the MPE range).
POINTS_MAX = 256
BEND_CENTS_LIMIT = 48.0 * 100.0
#: A duplicate curve offset counts as the same point within this tolerance.
_POINT_EPSILON = 1e-9
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
    "gate_mode": "gate_mode",
    "gate mode": "gate_mode",
    "note_len": "gate_mode",
    "note len": "gate_mode",
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


def validate_gate_mode(value: object) -> str:
    """Return ``"beat"`` (today's cap) or ``"note"`` (the note length wins)."""

    text = str(value).strip().lower()
    if text not in GATE_MODES:
        raise ValueError("gate_mode must be one of " + ", ".join(GATE_MODES))
    return text


def validate_source(value: object) -> str:
    """Return one of ``generated``, ``played``, ``live``, ``record``."""

    text = str(value).strip().lower()
    if text not in SOURCES:
        raise ValueError("source must be one of " + ", ".join(SOURCES))
    return text


def validate_seed(value: object) -> int:
    """Return an integer seed clamped to ``0..2**31-1``."""

    return max(0, min(SEED_MAX, _as_int(value, "seed")))


def validate_channel(value: object, label: str = "member_channel") -> int:
    """Return an MPE member channel, 2..16 (1 is the master channel)."""

    number = _as_int(value, label)
    if not 2 <= number <= 16:
        raise ValueError(f"{label} must be 2..16 (1 is the MPE master channel)")
    return number


# ---------------------------------------------------------------------------
# Stage 9.2 expression points (shared by engine, melody and state)
# ---------------------------------------------------------------------------


def _point_pairs(points: object, label: str) -> list[tuple[float, object]]:
    """Validate the *shape* of a curve: finite non-negative sorted offsets."""

    if points is None:
        return []
    if isinstance(points, (str, bytes)) or not isinstance(points, Sequence):
        raise TypeError(f"{label} must be a sequence of (offset, value) pairs")
    items: list[tuple[float, object]] = []
    for position, point in enumerate(points):
        if (
            isinstance(point, (str, bytes))
            or not isinstance(point, Sequence)
            or len(point) != 2
        ):
            raise ValueError(
                f"{label}[{position}] must be a (offset, value) pair"
            )
        offset = _finite(point[0], f"{label}[{position}] offset")
        if offset < 0.0:
            raise ValueError(f"{label}[{position}] offset must be >= 0")
        if items and offset < items[-1][0] - _POINT_EPSILON:
            raise ValueError(f"{label} offsets must be sorted ascending")
        items.append((offset, point[1]))
    return items


def _coalesce(
    items: Sequence[tuple[float, object]]
) -> list[tuple[float, object]]:
    """Duplicate offsets last-wins, then drop identical consecutive values."""

    deduped: list[tuple[float, object]] = []
    for offset, value in items:
        if deduped and abs(deduped[-1][0] - offset) <= _POINT_EPSILON:
            deduped[-1] = (offset, value)
        else:
            deduped.append((offset, value))
    result: list[tuple[float, object]] = []
    for offset, value in deduped:
        if result and result[-1][1] == value:
            continue
        result.append((offset, value))
    return result[:POINTS_MAX]


def normalize_bend_points(
    points: object, label: str = "bend_points"
) -> tuple[tuple[float, float], ...]:
    """Validate/normalise a bend curve ``(offset_beats, cents)``.

    Offsets are finite and ``>= 0`` in ascending order (duplicates last-wins);
    cents are finite and clamped to ``+/-4800``; identical consecutive values are
    coalesced and at most :data:`POINTS_MAX` points are kept.
    """

    items: list[tuple[float, object]] = []
    for position, (offset, value) in enumerate(_point_pairs(points, label)):
        cents = _finite(value, f"{label}[{position}] cents")
        cents = max(-BEND_CENTS_LIMIT, min(BEND_CENTS_LIMIT, cents))
        items.append((offset, cents))
    return tuple(_coalesce(items))  # type: ignore[return-value]


def normalize_mod_points(
    points: object, label: str = "mod_points"
) -> tuple[tuple[float, int], ...]:
    """Validate/normalise a mod curve ``(offset_beats, 0..127)``."""

    items: list[tuple[float, object]] = []
    for position, (offset, value) in enumerate(_point_pairs(points, label)):
        amount = _as_int(value, f"{label}[{position}] mod")
        if not 0 <= amount <= 127:
            raise ValueError(f"{label}[{position}] mod must be 0..127")
        items.append((offset, amount))
    return tuple(_coalesce(items))  # type: ignore[return-value]


def normalize_setting_name(name: object) -> str:
    """Map a short or long setting name to its canonical field name."""

    key = str(name).strip().lower()
    canonical = _SETTING_ALIASES.get(key)
    if canonical is None:
        raise ValueError(
            f"unknown lead setting {name!r}; expected enabled, grid, "
            "phrase_bars, density, gate_beats, velocity, octave_range, "
            "base_octave, variation, variation_bars, swing, groove_link, seed, "
            "gate_mode"
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
    gate_mode: str = DEFAULT_GATE_MODE

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
            gate_mode=validate_gate_mode(
                values.get("gate_mode", defaults.gate_mode)
            ),
        )

    def state_args(
        self,
        port_ok: bool,
        playing: bool,
        variation_index: int = 0,
    ) -> list[Any]:
        """``/ji/lead/state`` payload (seventeen values, documented order)."""

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
            self.gate_mode,
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
        if key == "gate_mode":
            return replace(self, gate_mode=validate_gate_mode(value))
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
                "gate_mode",
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
        bend_points: Stage 9.2 ``(offset_beats, cents)`` bend curve, measured
            from the *raw* note-on (not quantised, not swung).
        mod_points: Stage 9.2 ``(offset_beats, 0..127)`` mod-wheel curve.
    """

    step: int
    length: int
    degree: int
    octave: int
    ratio_hint: float
    velocity: int
    bend_points: tuple[tuple[float, float], ...] = ()
    mod_points: tuple[tuple[float, int], ...] = ()

    @property
    def end(self) -> int:
        return self.step + max(1, self.length)


@dataclass(frozen=True, slots=True)
class LeadMotif:
    """A phrase: notes plus the parameters that shaped it.

    Stage 9.1 appends four defaulted fields so a *played* take can live in the
    same structure as the generated line without changing any existing
    construction site:

    * ``source`` -- ``"generated"`` or ``"played"``;
    * ``base_notes`` -- the take exactly as recorded (the Revert target);
    * ``history`` -- ``(variation name, index used)`` pairs in application
      order, so replaying a saved variation index reproduces the RNG exactly;
    * ``melody_name`` -- provenance for the status line (``""`` when unnamed).
    """

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
    source: str = "generated"
    base_notes: tuple[LeadNote, ...] = ()
    history: tuple[tuple[str, int], ...] = ()
    melody_name: str = ""

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
    bend_points: tuple[tuple[float, float], ...] = ()
    mod_points: tuple[tuple[float, int], ...] = ()


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


def _resize_notes(
    notes: Sequence[LeadNote], old_total: int, new_total: int
) -> tuple[LeadNote, ...]:
    """Crop a take to a shorter phrase, or tile it to fill a longer one."""

    total = max(1, int(new_total))
    if total <= int(old_total) or int(old_total) <= 0:
        return tuple(note for note in notes if note.step < total)
    result: list[LeadNote] = []
    start = 0
    while start < total:
        for note in notes:
            step = note.step + start
            if step < total:
                result.append(replace(note, step=step))
        start += int(old_total)
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
        """``seed=1 variation=identity #0 steps=16 notes=12 source=played "x"``."""

        with self._lock:
            motif = self._motif
            line = (
                f"seed={motif.seed} variation={motif.variation} "
                f"#{motif.variation_index} steps={motif.phrase_steps} "
                f"notes={motif.note_count} source={motif.source}"
            )
            if motif.melody_name:
                line += f' "{motif.melody_name}"'
            return line

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
                    bend_points=tuple(note.bend_points),
                    mod_points=tuple(note.mod_points),
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
                if self._motif.source == "played":
                    self._apply_played_shape_locked(key, before, after)
                else:
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
            changed = [
                key
                for key in _GENERATION_KEYS
                if getattr(settings, key) != getattr(before, key)
            ]
            self._settings = settings
            if settings.seed != before.seed:
                self._variation_index = 0
            if changed:
                if self._motif.source == "played":
                    # Never regenerate a played line implicitly: apply the
                    # documented shape policy one key at a time.  The 8.5 recall
                    # loads the motif explicitly right after this call.
                    for key in changed:
                        self._apply_played_shape_locked(key, before, settings)
                else:
                    self._motif = self._generate_locked()
            self._version += 1
            self._projection = None
            return settings

    # -- Stage 9.1: played motifs ---------------------------------------

    def load_motif(
        self,
        notes: Sequence[LeadNote],
        *,
        grid: object = DEFAULT_GRID,
        phrase_bars: object = DEFAULT_PHRASE_BARS,
        meter_num: object = 4,
        meter_den: object = 4,
        name: str = "",
        base_notes: Sequence[LeadNote] | None = None,
        history: Sequence[tuple[str, int]] = (),
        variation: object = DEFAULT_VARIATION,
        variation_index: object = 0,
    ) -> LeadMotif:
        """Install a played phrase (never calls :func:`generate_motif`).

        The settings' ``grid``/``phrase_bars`` are updated atomically in the
        same lock and ``gate_mode`` becomes ``note`` so a held take is not cut
        by the default 0.5-beat gate.  ``base_notes`` defaults to ``notes``.
        """

        grid_name = validate_grid(grid)
        bars = validate_phrase_bars(phrase_bars)
        numerator = rhythm.clamp_meter_numerator(meter_num)
        denominator = rhythm.validate_meter_denominator(meter_den)
        bar_steps = steps_per_bar(numerator, denominator, grid_name)
        total = max(1, bars * bar_steps)

        cleaned = self._validate_notes(notes, total)
        base = (
            tuple(cleaned)
            if base_notes is None
            else self._validate_notes(base_notes, total)
        )
        entries = self._validate_history(history)
        shape = validate_variation(variation)
        index = max(0, _as_int(variation_index, "variation_index"))

        with self._lock:
            self._settings = replace(
                self._settings,
                grid=grid_name,
                phrase_bars=bars,
                gate_mode="note",
            )
            self._variation_index = index
            self._meter_num = numerator
            self._meter_den = denominator
            self._motif = self._build_played_motif_locked(
                base,
                line_notes=cleaned,
                grid=grid_name,
                phrase_bars=bars,
                meter_num=numerator,
                meter_den=denominator,
                history=entries,
                variation=shape,
                variation_index=index,
                melody_name=str(name or ""),
                replay=bool(entries),
            )
            self._born = True
            self._version += 1
            self._projection = None
            return self._motif

    def clear_melody(self) -> LeadMotif:
        """Return to a fresh generated motif (``gate_mode`` untouched)."""

        with self._lock:
            self._variation_index = 0
            self._motif = self._generate_locked()
            self._version += 1
            self._projection = None
            return self._motif

    def set_melody_name(self, name: object) -> LeadMotif:
        """Attach provenance to the current take (after a melody save)."""

        with self._lock:
            self._motif = replace(self._motif, melody_name=str(name or ""))
            self._version += 1
            return self._motif

    def revert(self) -> LeadMotif:
        """Restore the recorded base, clear the history, keep the settings."""

        with self._lock:
            motif = self._motif
            if motif.source != "played":
                return motif
            base = motif.base_notes or motif.notes
            self._variation_index = 0
            self._motif = self._build_played_motif_locked(
                base,
                grid=motif.grid,
                phrase_bars=motif.phrase_bars,
                meter_num=motif.meter_num,
                meter_den=motif.meter_den,
                history=(),
                variation="identity",
                variation_index=0,
                melody_name=motif.melody_name,
                replay=False,
            )
            self._version += 1
            self._projection = None
            print(
                f"[LEAD] reverted to the recorded base "
                f"({len(self._motif.notes)} notes)"
            )
            return self._motif

    @staticmethod
    def _validate_notes(
        notes: Sequence[LeadNote], phrase_steps: int
    ) -> tuple[LeadNote, ...]:
        """Validate and normalise note values (shared by melody/state parsing)."""

        if isinstance(notes, (str, bytes)):
            raise TypeError("notes must be a sequence of LeadNote")
        cleaned: list[LeadNote] = []
        for position, note in enumerate(notes):
            if not isinstance(note, LeadNote):
                raise TypeError(f"note {position} must be a LeadNote")
            step = _as_int(note.step, f"note {position} step")
            length = _as_int(note.length, f"note {position} length")
            degree = _as_int(note.degree, f"note {position} degree")
            octave = _as_int(note.octave, f"note {position} octave")
            velocity = _as_int(note.velocity, f"note {position} velocity")
            hint = _finite(note.ratio_hint, f"note {position} ratio_hint")
            bend_points = normalize_bend_points(
                note.bend_points, f"note {position} bend_points"
            )
            mod_points = normalize_mod_points(
                note.mod_points, f"note {position} mod_points"
            )
            if step < 0:
                raise ValueError(f"note {position} step must be >= 0")
            if length < 1:
                raise ValueError(f"note {position} length must be >= 1")
            if degree < 0:
                raise ValueError(f"note {position} degree must be >= 0")
            if not -8 <= octave <= 8:
                raise ValueError(f"note {position} octave must be -8..8")
            if hint <= 0.0:
                raise ValueError(f"note {position} ratio_hint must be > 0")
            if not VELOCITY_MIN <= velocity <= VELOCITY_MAX:
                raise ValueError(
                    f"note {position} velocity must be "
                    f"{VELOCITY_MIN}..{VELOCITY_MAX}"
                )
            cleaned.append(
                LeadNote(
                    step=step,
                    length=length,
                    degree=degree,
                    octave=octave,
                    ratio_hint=hint,
                    velocity=velocity,
                    bend_points=bend_points,
                    mod_points=mod_points,
                )
            )
        return _normalize(cleaned, max(1, int(phrase_steps)))

    @staticmethod
    def _validate_history(
        history: Sequence[tuple[str, int]],
    ) -> tuple[tuple[str, int], ...]:
        if history is None:
            return ()
        if isinstance(history, (str, bytes)):
            raise TypeError("history must be a sequence of (name, index) pairs")
        entries: list[tuple[str, int]] = []
        for position, entry in enumerate(history):
            if isinstance(entry, (str, bytes)) or len(entry) != 2:
                raise ValueError(
                    f"history {position} must be a [name, index] pair"
                )
            name = validate_variation(entry[0])
            index = _as_int(entry[1], f"history {position} index")
            if index < 0:
                raise ValueError(f"history {position} index must be >= 0")
            entries.append((name, index))
        return tuple(entries)

    def _build_played_motif_locked(
        self,
        base_notes: Sequence[LeadNote],
        *,
        line_notes: Sequence[LeadNote] | None = None,
        grid: str,
        phrase_bars: int,
        meter_num: int,
        meter_den: int,
        history: Sequence[tuple[str, int]] = (),
        variation: str = DEFAULT_VARIATION,
        variation_index: int = 0,
        melody_name: str = "",
        source: str = "played",
        replay: bool = True,
    ) -> LeadMotif:
        bar_steps = steps_per_bar(meter_num, meter_den, grid)
        total = max(1, phrase_bars * bar_steps)
        base = _normalize(base_notes, total)
        line = base if line_notes is None else _normalize(line_notes, total)
        motif = LeadMotif(
            notes=line,
            phrase_bars=phrase_bars,
            grid=grid,
            steps_per_bar=bar_steps,
            phrase_steps=total,
            density=self._settings.density,
            octave_range=self._settings.octave_range,
            gate_beats=self._settings.gate_beats,
            velocity_base=self._settings.velocity,
            seed=self._settings.seed,
            variation=variation,
            variation_index=variation_index,
            meter_num=meter_num,
            meter_den=meter_den,
            source=source,
            base_notes=base,
            history=(),
            melody_name=melody_name,
        )
        entries = tuple(history)
        if replay and entries:
            motif = replace(motif, notes=base)
            for name, used_index in entries:
                motif = self._apply_variation_to(motif, name, used_index)
        return replace(
            motif,
            variation=variation,
            variation_index=variation_index,
            history=entries if replay else (),
        )

    def _apply_variation_to(
        self, motif: LeadMotif, name: str, index: int
    ) -> LeadMotif:
        result = apply_variation(
            name,
            motif,
            seed=self._settings.seed,
            index=index,
            ratios=self._ratios,
        )
        return replace(
            result,
            source=motif.source,
            base_notes=motif.base_notes,
            melody_name=motif.melody_name,
            history=motif.history,
        )

    def _apply_played_shape_locked(
        self, key: str, before: LeadSettings, after: LeadSettings
    ) -> None:
        """The §7.3 policy table for a played motif (never regenerates)."""

        motif = self._motif
        grid = after.grid
        bars = after.phrase_bars
        history = motif.history
        variation = motif.variation
        index = self._variation_index
        name = motif.melody_name
        base_total = motif.phrase_steps

        if key == "density":
            self._motif = replace(motif, density=after.density, seed=after.seed)
            print("[LEAD] density has no effect on a played line")
            return
        if key == "seed":
            self._variation_index = 0
            self._motif = replace(
                motif, seed=after.seed, history=(), variation_index=0
            )
            return
        if key == "octave_range":
            if after.octave_range > 0:
                base = tuple(
                    replace(
                        note,
                        octave=max(
                            -after.octave_range,
                            min(after.octave_range, note.octave),
                        ),
                    )
                    for note in motif.base_notes
                )
                self._motif = self._build_played_motif_locked(
                    base,
                    grid=grid,
                    phrase_bars=bars,
                    meter_num=motif.meter_num,
                    meter_den=motif.meter_den,
                    history=history,
                    variation=variation,
                    variation_index=index,
                    melody_name=name,
                )
                return
            self._motif = replace(motif, octave_range=after.octave_range)
            return

        if key == "grid":
            old_quarters = grid_quarters(before.grid)
            new_quarters = grid_quarters(after.grid)
            scale = old_quarters / new_quarters
            base = tuple(
                replace(
                    note,
                    step=int(round(note.step * scale)),
                    length=max(1, int(round(note.length * scale))),
                )
                for note in motif.base_notes
            )
        elif key == "phrase_bars":
            new_total = max(
                1,
                bars * steps_per_bar(motif.meter_num, motif.meter_den, grid),
            )
            base = _resize_notes(motif.base_notes, base_total, new_total)
        else:  # pragma: no cover - defensive: unknown generation key
            base = motif.base_notes
        self._motif = self._build_played_motif_locked(
            base,
            grid=grid,
            phrase_bars=bars,
            meter_num=motif.meter_num,
            meter_den=motif.meter_den,
            history=history,
            variation=variation,
            variation_index=index,
            melody_name=name,
        )

    def set_variation_index(self, index: object, *, force: bool = False) -> LeadMotif:
        """Restore a saved variation index and rebuild the motif (Stage 8.5).

        The motif is a pure function of the current settings, the set, and this
        index, so restoring settings + index reproduces the recalled colour.
        ``force=True`` regenerates even when the index did not change (the
        state load applies the index once more after the new set reaches the
        lead, whose ``on_set`` deliberately only re-projects).  This is
        additive: ``vary()`` remains the only *performance* gesture.
        """

        value = max(0, _as_int(index, "variation_index"))
        with self._lock:
            if value == self._variation_index and not force:
                return self._motif
            self._variation_index = value
            if self._motif.source == "played":
                self._motif = self._replay_played_locked(value)
            else:
                self._motif = self._generate_locked()
            self._version += 1
            self._projection = None
            return self._motif

    def _replay_played_locked(self, target: int) -> LeadMotif:
        """Rebuild a played line from its base, replaying the history exactly.

        ``history`` stores ``(name, index_used)`` so the RNG of every applied
        variation is reproduced whatever triggered it.  A target past the
        stored history keeps applying the selected variation, one index at a
        time, until the counter reaches it (compounding is intended).
        """

        motif = self._motif
        # Every entry whose applied index is <= the target belongs to the
        # restored state (a combo change applies at the current index without
        # advancing it, so filtering by the stored index -- not by slice length
        # -- is what makes the replay exact whatever triggered it).
        entries = (
            ()
            if target <= 0
            else tuple(
                entry for entry in motif.history if int(entry[1]) <= target
            )
        )
        rebuilt = self._build_played_motif_locked(
            motif.base_notes or motif.notes,
            grid=motif.grid,
            phrase_bars=motif.phrase_bars,
            meter_num=motif.meter_num,
            meter_den=motif.meter_den,
            history=entries,
            variation=motif.variation,
            variation_index=target,
            melody_name=motif.melody_name,
        )
        applied = list(entries)
        used = max((int(entry[1]) for entry in entries), default=0)
        while used < target:
            used += 1
            name = self._settings.variation
            rebuilt = self._apply_variation_to(rebuilt, name, used)
            applied.append((name, used))
        return replace(
            rebuilt,
            variation=(rebuilt.variation if applied else "identity"),
            variation_index=target,
            source="played",
            base_notes=tuple(rebuilt.base_notes),
            melody_name=motif.melody_name,
            history=tuple(applied),
        )

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
                if self._motif.source == "played":
                    motif = self._motif
                    new_total = max(
                        1,
                        int(motif.phrase_bars)
                        * steps_per_bar(numerator, denominator, motif.grid),
                    )
                    base = _resize_notes(
                        motif.base_notes, motif.phrase_steps, new_total
                    )
                    self._motif = self._build_played_motif_locked(
                        base,
                        grid=motif.grid,
                        phrase_bars=motif.phrase_bars,
                        meter_num=numerator,
                        meter_den=denominator,
                        history=motif.history,
                        variation=motif.variation,
                        variation_index=self._variation_index,
                        melody_name=motif.melody_name,
                    )
                else:
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
        before = self._motif
        result = apply_variation(
            name,
            before,
            seed=self._settings.seed,
            index=self._variation_index,
            ratios=self._ratios,
        )
        if before.source == "played":
            # ``random`` returns a freshly generated motif; re-attach the take
            # so Revert and the status line survive every variation.
            entry = (validate_variation(name), int(self._variation_index))
            result = replace(
                result,
                source="played",
                base_notes=before.base_notes or before.notes,
                melody_name=before.melody_name,
                history=before.history + (entry,),
            )
        return result

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


class _ExpressionPlayer:
    """One bounded, cancellable playback of one note's bend/mod curves.

    A single daemon thread (never one timer per point): it waits on an event up
    to each point's real-time offset and sends the message under the scheduler
    lock, so it can never interleave a bend with the next note-on.  ``cancel()``
    is idempotent and safe from any thread.
    """

    def __init__(
        self,
        output: Any,
        lock: threading.RLock,
        note: LeadProjected,
        note_number: int,
        bend_range_semitones: float,
        beat_seconds: float,
        *,
        initial_bend: int | None = None,
        initial_mod: int | None = None,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self._output = output
        self._lock = lock
        self._note = note
        self._note_number = int(note_number)
        self._bend_range = float(bend_range_semitones)
        self._beat_seconds = float(beat_seconds)
        self._initial_bend = initial_bend
        self._initial_mod = initial_mod
        self._now = now
        self._event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        thread = threading.Thread(
            target=self._run, name="ji-lead-expression", daemon=True
        )
        self._thread = thread
        thread.start()

    def cancel(self) -> None:
        self._event.set()

    def _events(self) -> list[tuple[float, int, float]]:
        """Merged curve sorted by offset; bend (0) before mod (1) on a tie."""

        events: list[tuple[float, int, float]] = [
            (float(offset), 0, float(cents))
            for offset, cents in self._note.bend_points
        ]
        events.extend(
            (float(offset), 1, float(value))
            for offset, value in self._note.mod_points
        )
        events.sort(key=lambda item: (item[0], item[1]))
        return events

    def _run(self) -> None:
        started = self._now()
        last_bend = self._initial_bend
        last_mod = self._initial_mod
        for offset, kind, value in self._events():
            delay = started + max(0.0, offset) * self._beat_seconds - self._now()
            if delay > 0.0 and self._event.wait(delay):
                return
            if self._event.is_set():
                return
            with self._lock:
                if self._event.is_set():
                    return
                try:
                    if kind == 0:
                        target = self._note.frequency * (2.0 ** (value / 1200.0))
                        bend = bend_for_frequency(
                            self._note_number, target, self._bend_range
                        )
                        if bend == last_bend:
                            continue
                        last_bend = bend
                        self._output.pitch_bend(bend)
                    else:
                        mod = int(round(value))
                        if mod == last_mod:
                            continue
                        last_mod = mod
                        self._output.control_change(1, mod)
                except Exception:  # a broken output must not leak a thread
                    return


class LeadScheduler:
    """Daemon-thread, transport-gated, drift-corrected lead sequencer.

    The thread owns monotonic deadlines and sends pitch bend before each
    note-on.  A gate timer sends the note-off; a retrigger cancels the pending
    timer and note-offs the held note first, so a late gate can never cut the
    next note.  Step 0 is the phrase origin: starting the scheduler is what
    makes the phrase restart on a transport start.

    Stage 9.2 adds :meth:`override` / :meth:`release_override`: while the input
    owns the lead the thread still advances but plays nothing, and releasing the
    key re-attacks the loop's last note when its gate is still open.
    """

    def __init__(
        self,
        output: Any,
        engine: LeadEngine,
        *,
        bpm: object = rhythm.BPM_DEFAULT,
        now: Callable[[], float] = time.monotonic,
        on_note: Callable[[LeadProjected, int], None] | None = None,
        breath: object | None = None,
    ) -> None:
        if not callable(now):
            raise TypeError("now must be callable")
        self.output = output
        self.engine = engine
        self._now = now
        # Stage 8.7: a shared BreathClock, or None for exactly the old timing.
        self._breath = breath
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
        #: Stage 9.1: optional first deadline (a bar line when re-entering).
        self._first_deadline: float | None = None
        #: Stage 9.2: input override + the last played step's resume bookkeeping.
        self._overridden = False
        self._played_total = 0
        self._last_note: LeadProjected | None = None
        self._last_settings: LeadSettings | None = None
        self._last_beat_seconds = 0.0
        self._last_step_quarters = 1.0
        self._step_started_at: float | None = None
        self._step_gate_seconds = 0.0
        self._resumed_for: int | None = None
        self._expression: _ExpressionPlayer | None = None

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

    @property
    def overridden(self) -> bool:
        """True while the input owns Bus 5 and the sequencer plays nothing."""

        with self._lock:
            return self._overridden

    def set_bpm(self, bpm: object) -> float:
        value = rhythm.clamp_bpm(bpm)
        with self._lock:
            self._bpm = value
            return value

    # -- Stage 8.7 breath -----------------------------------------------

    def set_breath(self, breath: object | None) -> None:
        """Attach/detach the shared breath clock (``None`` = exact old timing)."""

        self._breath = breath

    def _breath_offset(self, step_seconds: float) -> float:
        """Clamped lead breath offset in seconds (0.0 when no breath)."""

        breath = self._breath
        if breath is None:
            return 0.0
        try:
            value = float(breath.offset_seconds("lead", self._now()))
        except Exception:  # a broken clock must never stop the lead
            return 0.0
        limit = 0.45 * float(step_seconds)
        return max(-limit, min(limit, value))

    def _velocity_with_breath(self, base: int) -> int:
        breath = self._breath
        if breath is None:
            return int(base)
        try:
            delta = int(breath.velocity_delta("lead"))
        except Exception:
            return int(base)
        return max(1, min(127, int(base) + delta))

    # -- lifecycle -------------------------------------------------------

    def start(self, at_time: float | None = None) -> None:
        """Start at the phrase origin (step 0).  Idempotent while running.

        ``at_time`` (Stage 9.1) makes the first deadline an absolute monotonic
        timestamp instead of "now", so leaving ``live`` can resume exactly on
        the next bar line.  ``None`` is the original behaviour.
        """

        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            if not getattr(self.output, "is_open", False):
                raise RuntimeError("lead output is not open")
            self._stop.clear()
            self._step_index = 0
            self._total_steps = 0
            self._first_deadline = None if at_time is None else float(at_time)
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
            expression = self._cancel_expression_locked()
            held = self._held
            self._held = None
            self._overridden = False
            self._last_note = None
            self._step_started_at = None
            self._step_gate_seconds = 0.0
            self._resumed_for = None
        if expression is not None:
            expression.cancel()
        self._send_off(held)
        try:
            self.output.all_notes_off()
        except Exception:
            pass

    # -- Stage 9.2: input override --------------------------------------

    def override(self) -> None:
        """Hand Bus 5 to the input: release the loop note, play nothing.

        Called by the input rig *before* it sounds its own note.  The clock and
        the step index keep advancing, so the loop stays in time; the thread
        simply skips every ``_play`` while overridden.
        """

        with self._lock:
            if self._overridden:
                return
            self._overridden = True
            self._cancel_gate_locked()
            expression = self._cancel_expression_locked()
            held = self._held
            self._held = None
            step = self._step_index
        if expression is not None:
            expression.cancel()
        self._send_off(held)
        print(f"[LEAD-IN] override on at step {step}")

    def release_override(self) -> None:
        """Give Bus 5 back to the loop, re-attacking its note when still due.

        The input rig sends its note-off before calling this.  If the last
        played step's nominal gate is still open, the note attacks again from
        now (the owner's "release triggers the attack"); otherwise the next
        scheduled step enters normally.
        """

        with self._lock:
            if not self._overridden:
                return
            self._overridden = False
            expression = self._cancel_expression_locked()
            note = self._last_note
            settings = self._last_settings
            started = self._step_started_at
            gate_seconds = self._step_gate_seconds
            token = self._played_total
            running = self.is_running and not self._stop.is_set()
            step = self._step_index
            eligible = bool(
                running
                and note is not None
                and settings is not None
                and started is not None
                and (self._now() - started) < gate_seconds
                and self._resumed_for != token
            )
            if eligible:
                # ``_play`` is reentrant on this RLock: the resume attack and
                # its fresh gate happen with the lock still held, so the input
                # can never slip a bend between them.
                try:
                    self._play(
                        note,
                        settings,
                        self._last_beat_seconds,
                        self._last_step_quarters,
                        resumed=True,
                    )
                except Exception as exc:  # a broken output never kills the rig
                    eligible = False
                    print(f"[LEAD-IN] resume attack failed: {exc!r}")
            next_step = (step + 1) % max(
                1, self.engine.motif.phrase_steps
            )
        if expression is not None:
            expression.cancel()
        if not running:
            print("[LEAD-IN] override cleared (scheduler stopped)")
        elif eligible:
            print(f"[LEAD-IN] override off; loop note attacked at step {step}")
        else:
            print(f"[LEAD-IN] override off; next loop note at step {next_step}")

    def cancel_expression(self) -> None:
        """Cancel the active expression curve (source change / lead off)."""

        with self._lock:
            expression = self._cancel_expression_locked()
        if expression is not None:
            expression.cancel()

    def clear_override(self) -> None:
        """Drop the override without resuming (source change / lead off / stop).

        Unlike :meth:`release_override` this never re-attacks the old line's
        note: the caller is changing what Bus 5 plays.
        """

        with self._lock:
            if not self._overridden:
                return
            self._overridden = False
            self._resumed_for = None
            expression = self._cancel_expression_locked()
        print("[LEAD-IN] override cleared")

    # -- thread body -----------------------------------------------------

    def _run(self) -> None:  # pragma: no cover - exercised with a fake output
        with self._lock:
            first = self._first_deadline
            self._first_deadline = None
        deadline = self._now() if first is None else first
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
                # Swing and Stage 8.7 breath are added to the SLEEP TARGET only;
                # ``deadline`` below still advances by exactly one step.
                offset = swing_offset(
                    step,
                    self.engine.effective_swing(),
                    step_seconds,
                    steps_per_beat(settings.grid),
                )
                offset += self._breath_offset(step_seconds)
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
                    # ``_play`` itself refuses while overridden, so the check
                    # cannot race with the input thread's ``override()``.
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
        except Exception as exc:  # a broken output must not crash quietly
            print(f"[LEAD] scheduler error: {exc}", file=sys.stderr)
        finally:
            with self._lock:
                self._cancel_gate_locked()
                expression = self._cancel_expression_locked()
                held = self._held
                self._held = None
            if expression is not None:
                expression.cancel()
            self._send_off(held)

    def _play(
        self,
        note: LeadProjected,
        settings: LeadSettings,
        beat_seconds: float,
        step_quarters: float,
        *,
        resumed: bool = False,
    ) -> None:
        config = self.engine.config
        pitch = frequency_to_pitch(note.frequency, config.bend_range_semitones)

        # The whole attack runs under the lock: ``override()`` and this method
        # can never interleave a loop note-on with the input's takeover.
        with self._lock:
            if self._overridden:
                return  # the input owns Bus 5: no note, gate, bend or CC
            self._cancel_gate_locked()
            expression = self._cancel_expression_locked()
            held = self._held
            self._held = None
            self._played_total += 1
            token = self._played_total
            self._last_note = note
            self._last_settings = settings
            self._last_beat_seconds = float(beat_seconds)
            self._last_step_quarters = float(step_quarters)

            if settings.gate_mode == "note":
                gate_beats = note.length * step_quarters
            else:
                gate_beats = min(
                    float(settings.gate_beats), note.length * step_quarters
                )
            gate_seconds = max(_MIN_GATE_SECONDS, gate_beats * beat_seconds)

            # CC 1 is channel state: the generated line sends none (the 9.1
            # stream is unchanged), a note with mod points starts on its first
            # value, and a resume attack clears the input's wheel state.
            first_mod: int | None = None
            if note.mod_points:
                first_mod = 0
                for offset, value in note.mod_points:
                    if abs(offset) <= _POINT_EPSILON:
                        first_mod = int(value)
                        break
            send_mod = bool(note.mod_points) or resumed
            self._send_off(held)
            self.output.pitch_bend(pitch.bend)
            if send_mod:
                self.output.control_change(
                    1, 0 if first_mod is None else first_mod
                )
            self.output.note_on(
                pitch.note, self._velocity_with_breath(int(note.velocity))
            )
            self._held = int(pitch.note)
            self._step_started_at = self._now()
            self._step_gate_seconds = gate_seconds
            if resumed:
                self._resumed_for = token

            player = None
            if note.bend_points or note.mod_points:
                player = _ExpressionPlayer(
                    self.output,
                    self._lock,
                    note,
                    int(pitch.note),
                    config.bend_range_semitones,
                    beat_seconds,
                    initial_bend=int(pitch.bend),
                    initial_mod=(first_mod if send_mod else None),
                    now=self._now,
                )
                self._expression = player
            self._schedule_gate(gate_seconds)
            if player is not None:
                player.start()

        if expression is not None:
            expression.cancel()

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

    def _cancel_expression_locked(self) -> _ExpressionPlayer | None:
        """Stop the curve *before* the lock is released (no send can slip in)."""

        player = self._expression
        self._expression = None
        if player is not None:
            player.cancel()
        return player

    def _gate_fire(self, token: int) -> None:
        with self._lock:
            if token != self._gate_token or self._stop.is_set():
                return
            self._gate_timer = None
            self._gate_token += 1
            held = self._held
            self._held = None
            expression = self._cancel_expression_locked()
        if expression is not None:
            expression.cancel()
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
        #: Stage 9.1: who drives Bus 5 (generated | played | live | record).
        self._source = DEFAULT_SOURCE
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
        self._sync_scheduler()
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
            and self._source in ("generated", "played")
            and self.scheduler.is_running
        )

    @property
    def source(self) -> str:
        return self._source

    @property
    def melody_name(self) -> str:
        return self.engine.motif.melody_name

    def state_args(self) -> list[Any]:
        return self.engine.state_args(self._port_ok, self.playing)

    # -- Stage 9.1: the one source switch --------------------------------

    def set_source(
        self, name: object, *, at_time: float | None = None
    ) -> str:
        """Switch who drives Bus 5 and start/stop the scheduler accordingly.

        ``generated``/``played`` run the sequencer (gated by the transport);
        ``record`` stops it immediately -- never mute it, so no stale gates or
        interleaved bends can survive.  ``live`` is a **deprecated alias** for
        the 9.1 model: the input always overrides now, so it maps to ``played``
        when a take exists and to ``generated`` otherwise.
        """

        source = validate_source(name)
        if source == "live":
            print("[LEAD] source=live is deprecated; input always overrides")
            source = "played" if self.engine.motif.source == "played" else "generated"
        if source == self._source:
            if source in ("generated", "played"):
                self._sync_scheduler(at_time=at_time)
            return self._source
        self._source = source
        # An expression curve belongs to the line that is leaving, and an
        # override belongs to the input note that the caller is releasing.
        self.scheduler.clear_override()
        self.scheduler.cancel_expression()
        if source == "record":
            if self.scheduler.is_running:
                step = self.scheduler.step_index
                self.scheduler.stop()
                print(
                    f"[LEAD-IN] record on (sequencer paused at step {step})"
                )
            else:
                print("[LEAD-IN] record on (sequencer paused)")
        else:
            self._sync_scheduler(at_time=at_time)
        print(f"[LEAD] source={source}")
        return self._source

    def _sync_scheduler(self, at_time: float | None = None) -> None:
        """One shared start/stop rule for transport, enable, source and recall."""

        should_run = bool(
            self._port_ok
            and self._transport_running
            and self.engine.enabled
            and self._source in ("generated", "played")
        )
        if should_run:
            if not self.scheduler.is_running:
                self.scheduler.start(at_time=at_time)
        elif self.scheduler.is_running:
            self.scheduler.stop()

    # -- Stage 9.1: played phrases --------------------------------------

    def load_motif(self, notes: Sequence[LeadNote], **kwargs: Any) -> LeadMotif:
        return self.engine.load_motif(notes, **kwargs)

    def clear_melody(self) -> LeadMotif:
        return self.engine.clear_melody()

    def revert(self) -> LeadMotif:
        return self.engine.revert()

    # -- transport / tempo / meter --------------------------------------

    def set_transport(self, running: object) -> None:
        """Transport-gated exactly like the drums/bass (the arp is not)."""

        self._transport_running = bool(running)
        self._sync_scheduler()

    def set_bpm(self, bpm: object) -> float:
        value = self.scheduler.set_bpm(bpm)
        self.engine.set_bpm(value)
        return value

    def set_breath(self, breath: object | None) -> None:
        """Forward the shared Stage 8.7 breath clock to the scheduler."""

        self.scheduler.set_breath(breath)

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
            self._sync_scheduler()
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

    def set_settings(self, settings: LeadSettings) -> LeadSettings:
        """Replace every lead setting at once (Stage 8.5 state recall).

        Mirrors :meth:`set_setting`'s enable side effect: the scheduler starts or
        stops when the recalled ``enabled`` flag differs from the live one.
        """

        if not isinstance(settings, LeadSettings):
            raise TypeError("settings must be a LeadSettings")
        before = self.engine.settings
        applied = self.engine.set_settings(settings)
        self._sync_scheduler()
        if applied.enabled != before.enabled:
            print(f"[LEAD] {'enabled' if applied.enabled else 'disabled'}")
        return applied

    def set_variation_index(
        self, index: object, *, force: bool = False
    ) -> LeadMotif:
        """Restore a saved variation index (Stage 8.5 state recall)."""

        return self.engine.set_variation_index(index, force=force)

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

    # -- Stage 9.2: input override --------------------------------------

    def override_from_input(self) -> None:
        """The input takes Bus 5 (called before its own note-on)."""

        self.scheduler.override()

    def release_input_override(self) -> None:
        """The input's last note ended: give the loop back (resume attack)."""

        self.scheduler.release_override()

    def clear_input_override(self) -> None:
        """The input's note is being released by an exit path (no resume)."""

        self.scheduler.clear_override()

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
    "BEND_CENTS_LIMIT",
    "DEFAULT_BEND_RANGE_SEMITONES",
    "DEFAULT_CONFIG_PATH",
    "DEFAULT_DENSITY",
    "DEFAULT_ENABLED",
    "DEFAULT_GATE_BEATS",
    "DEFAULT_GATE_MODE",
    "DEFAULT_GRID",
    "DEFAULT_GROOVE_LINK",
    "DEFAULT_MEMBER_CHANNEL",
    "DEFAULT_OCTAVE_RANGE",
    "DEFAULT_PHRASE_BARS",
    "DEFAULT_PORT",
    "DEFAULT_SEED",
    "DEFAULT_SOURCE",
    "DEFAULT_VARIATION",
    "DEFAULT_VARIATION_BARS",
    "DEFAULT_VELOCITY",
    "DENSITY_MAX",
    "DENSITY_MIN",
    "GATE_BEATS_MAX",
    "GATE_BEATS_MIN",
    "GATE_MODES",
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
    "POINTS_MAX",
    "SEED_MAX",
    "SOURCES",
    "VARIATIONS",
    "VARIATION_BARS_MAX",
    "VARIATION_BARS_MIN",
    "apply_variation",
    "clamp_unit",
    "generate_motif",
    "grid_quarters",
    "load_config",
    "normalize_bend_points",
    "normalize_mod_points",
    "normalize_setting_name",
    "place_steps",
    "steps_per_bar",
    "steps_per_beat",
    "validate_base_octave",
    "validate_bool",
    "validate_channel",
    "validate_density",
    "validate_gate_beats",
    "validate_gate_mode",
    "validate_grid",
    "validate_octave_range",
    "validate_phrase_bars",
    "validate_seed",
    "validate_source",
    "validate_variation",
    "validate_variation_bars",
    "validate_velocity",
]

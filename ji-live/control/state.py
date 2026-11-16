"""Stage 8.5 performance states: named, versioned JSON snapshots of the rig.

A **performance state** is everything the bridge owns that makes the rig sound
the way it does, saved under a name and recallable later without restarting
anything.  It is deliberately *not* a session dump:

in scope
    frequency set (ratios, anchor, analysis octaves, fundamental), mutation
    controls, the four home slots + active home + journey bookkeeping, tempo and
    meter, drums (enable/rate/lane mask), the groove (preset/modifiers/seed and
    the 32-int grid payload), and every ``BassSettings``/``ArpSettings``/
    ``LeadSettings``/``DroneSettings`` field + the lead variation index.
excluded (by the owner)
    loops (no loop recall), the sclang-owned autotune parameters, the live
    transport state, the 64-deep Back history, and pending quantizer actions.

This module is **pure**: standard library plus the other pure ``control``
models.  It never imports the bridge, OSC, PyQt6, mido, or threads.  It owns
name rules, the on-disk format, per-section validation (through the existing
``*Settings.create`` / ``SetSnapshot.create`` / ``HomeConfig.from_mapping`` /
``GrooveSpec`` / ``GrooveGrid`` validators, never a second validation layer),
and an atomic, path-safe state library.

File format (version 1)
-----------------------
::

    {
      "format": "ji-performance-state",
      "version": 1,
      "saved_at": "2026-11-02T02:00:00-05:00",
      "name": "A-drone",
      "freqset": {"ratios": [...], "anchor": 1.0,
                  "octaves": [[1.0, 0], ...], "fundamental": 220.0},
      "mutation": {"randomness": 0.55, ...},
      "home": {"slots": [null, {...}, ...], "active_slot": -1, ...},
      "rhythm": {"bpm": 120.0, "meter_num": 4, "meter_den": 4},
      "drums": {"enabled": false, "rate": 1.0, "lane_mask": 69},
      "groove": {"preset": "four", ..., "grid": [32 ints]},
      "bass": {... every BassSettings field ...},
      "arp": {... every ArpSettings field ...},
      "lead": {... every LeadSettings field ..., "variation_index": 0},
      "drone": {... every DroneSettings field ...}
    }

Missing optional sections/keys fall back to the shipped values; unknown keys
are ignored; a **newer** ``version`` is refused.  The whole document is parsed
and validated before the caller applies anything, so a corrupt file can never
leave half the rig changed.

Name rules
----------
After trimming: 1..40 characters, the first character alphanumeric, the rest
``[A-Za-z0-9 ._-]``; no ``/``, ``\\``, ``..``, control characters, or leading
dot.  The filename is ``<name>.json``; two names that differ only in case are
refused so a case-insensitive filesystem (macOS APFS) cannot silently merge
them.

Storage
-------
``ji-live/states/<name>.json`` by default.  Saves are atomic: the text is
written to ``<name>.json.tmp`` in the same directory, flushed and fsync'd, then
``os.replace``d onto the final name.  A failed save leaves the previous file
intact.  State files are user data: nothing here ever writes into ``config/``.
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field, fields, replace
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

try:  # Support both ``python -m control.state`` and a package import.
    from . import arp as arp_module
    from . import bass as bass_module
    from . import drone as drone_module
    from . import home as home_module
    from . import lead as lead_module
    from .drums import DEFAULT_LANE_MASK
    from .groove import GrooveGrid, GrooveSpec, preset_grid
    from .mutation import MAX_SET_SIZE, SetSnapshot
    from .rhythm import (
        RhythmState,
        validate_drum_rate,
        BPM_DEFAULT,
        METER_NUM_DEFAULT,
        METER_DEN_DEFAULT,
    )
except ImportError:  # pragma: no cover - exercised only by direct execution
    import arp as arp_module  # type: ignore[no-redef]
    import bass as bass_module  # type: ignore[no-redef]
    import drone as drone_module  # type: ignore[no-redef]
    import home as home_module  # type: ignore[no-redef]
    import lead as lead_module  # type: ignore[no-redef]
    from drums import DEFAULT_LANE_MASK  # type: ignore[no-redef]
    from groove import GrooveGrid, GrooveSpec, preset_grid  # type: ignore[no-redef]
    from mutation import MAX_SET_SIZE, SetSnapshot  # type: ignore[no-redef]
    from rhythm import (  # type: ignore[no-redef]
        RhythmState,
        validate_drum_rate,
        BPM_DEFAULT,
        METER_NUM_DEFAULT,
        METER_DEN_DEFAULT,
    )

STATE_FORMAT = "ji-performance-state"
STATE_VERSION = 1
STATE_EXTENSION = ".json"
STATE_TEMP_EXTENSION = ".json.tmp"

NAME_MIN = 1
NAME_MAX = 40
NAME_ALLOWED_EXTRA = " ._-"

#: The shipped state directory (``ji-live/states/``), resolved from this file.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_STATE_DIR = PROJECT_ROOT / "states"

#: Values a missing ``mutation`` section falls back to (the bridge defaults).
MUTATION_DEFAULTS = {
    "randomness": 0.55,
    "dissonance_bias": 0.0,
    "stepwise_bias": 0.25,
    "temperature": 0.0,
    "anchor_mobility": 0.25,
    "change_count": 1,
    "glide_time": 0.15,
    "max_changes": 3,
}

#: Engine octave range (the bridge never overrides it).
OCTAVE_MIN = -1
OCTAVE_MAX = 2


# ---------------------------------------------------------------------------
# Errors / names
# ---------------------------------------------------------------------------


class StateError(ValueError):
    """One exception type for name, format, validation, and I/O problems.

    ``kind`` lets the bridge phrase the one ``[STATE]`` line without parsing the
    message: ``name``, ``format``, ``validation``, ``missing``, or ``io``.
    """

    def __init__(self, message: str, *, kind: str = "validation") -> None:
        super().__init__(str(message))
        self.kind = str(kind)


def _is_control(character: str) -> bool:
    return ord(character) < 32 or ord(character) == 127


def normalize_state_name(value: Any) -> str:
    """Trim surrounding whitespace; never changes the case."""

    if not isinstance(value, str):
        raise StateError("a state name must be text", kind="name")
    return value.strip()


def validate_state_name(value: Any) -> str:
    """Return the trimmed, validated name or raise :class:`StateError`."""

    name = normalize_state_name(value)
    if not name:
        raise StateError("a state name cannot be empty", kind="name")
    if len(name) > NAME_MAX:
        raise StateError(
            f"a state name is at most {NAME_MAX} characters", kind="name"
        )
    if not name[0].isalnum() or not name[0].isascii():
        raise StateError(
            "a state name must start with A-Z, a-z, or 0-9", kind="name"
        )
    for character in name:
        if _is_control(character):
            raise StateError(
                "a state name cannot contain control characters", kind="name"
            )
        if character.isascii() and (character.isalnum() or character in NAME_ALLOWED_EXTRA):
            continue
        raise StateError(
            "a state name may only use A-Z, a-z, 0-9, space, '.', '_', '-'",
            kind="name",
        )
    if ".." in name:
        raise StateError("a state name cannot contain '..'", kind="name")
    if name.startswith("."):
        raise StateError("a state name cannot start with '.'", kind="name")
    if "/" in name or "\\" in name:
        raise StateError("a state name cannot contain a path separator", kind="name")
    return name


@dataclass(frozen=True, slots=True)
class StateName:
    """A validated state name (immutable, comparable by value)."""

    value: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", validate_state_name(self.value))

    @classmethod
    def create(cls, value: Any) -> "StateName":
        return cls(validate_state_name(value))

    @property
    def stem(self) -> str:
        return self.value

    @property
    def filename(self) -> str:
        return f"{self.value}{STATE_EXTENSION}"

    @property
    def key(self) -> str:
        return self.value.casefold()

    def __str__(self) -> str:
        return self.value


def resolve_state_dir(path: str | Path | None = None) -> Path:
    """Resolve ``--state-dir``; relative paths use the project root."""

    if path is None:
        return DEFAULT_STATE_DIR
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = PROJECT_ROOT / candidate
    return candidate


# ---------------------------------------------------------------------------
# Small typed helpers (format validation only; ranges use the model validators)
# ---------------------------------------------------------------------------


def _as_mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise StateError(f"{label} must be a JSON object")
    return value


def _as_text(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise StateError(f"{label} must be text")
    return value


def _as_bool(value: Any, label: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        numeric = float(value)
        if numeric in (0.0, 1.0):
            return bool(int(numeric))
    raise StateError(f"{label} must be true or false")


def _as_int(value: Any, label: str) -> int:
    if isinstance(value, bool):
        raise StateError(f"{label} must be an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        return int(value)
    raise StateError(f"{label} must be an integer")


def _as_int_in(value: Any, label: str, low: int, high: int) -> int:
    number = _as_int(value, label)
    if not low <= number <= high:
        raise StateError(f"{label} must be between {low} and {high}")
    return number


def _as_float(value: Any, label: str) -> float:
    if isinstance(value, bool):
        raise StateError(f"{label} must be a real number")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise StateError(f"{label} must be a real number") from None
    if not math.isfinite(number):
        raise StateError(f"{label} must be finite")
    return number


def _as_float_in(value: Any, label: str, low: float, high: float) -> float:
    number = _as_float(value, label)
    if not low <= number <= high:
        raise StateError(f"{label} must be between {low:g} and {high:g}")
    return number


def _key(mapping: Mapping[str, Any], name: str, default: Any) -> Any:
    return mapping[name] if name in mapping else default


def _known(section: Mapping[str, Any], model: Any) -> dict[str, Any]:
    """Return only the model's dataclass fields present in ``section``."""

    return {
        item.name: section[item.name]
        for item in fields(model)
        if item.name in section
    }


def _migrate(document: Mapping[str, Any], version: int) -> Mapping[str, Any]:
    """Migration seam for older documents.

    Version 1 is the only version today, so there is nothing to rewrite; a
    future version 2 loader can upgrade a version-1 mapping here before the
    common validation runs.
    """

    return document


def _section(data: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = data.get(key)
    if value is None:
        return {}
    return _as_mapping(value, key)


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------


def _snapshot_mapping(snapshot: SetSnapshot) -> dict[str, Any]:
    return {
        "ratios": [float(ratio) for ratio in snapshot.ratios],
        "anchor": None if snapshot.anchor is None else float(snapshot.anchor),
        "octaves": [[float(ratio), int(octave)] for ratio, octave in snapshot.octaves],
    }


def _canonical_snapshot(snapshot: SetSnapshot) -> SetSnapshot:
    """Fill a missing analysis octave with 0, exactly like the engine does."""

    octaves = dict(snapshot.octaves)
    for ratio in snapshot.ratios:
        octaves.setdefault(ratio, 0)
    return SetSnapshot(
        ratios=snapshot.ratios,
        anchor=snapshot.anchor,
        octaves=tuple(sorted(octaves.items())),
    )


def _snapshot_from(value: Any, label: str) -> SetSnapshot:
    """Build a canonical snapshot from a ``{ratios, anchor, octaves}`` mapping."""

    if isinstance(value, SetSnapshot):
        try:
            return _canonical_snapshot(
                SetSnapshot.create(value.ratios, value.anchor, value.octaves)
            )
        except (TypeError, ValueError) as exc:
            raise StateError(f"{label}: {exc}") from None
    data = _as_mapping(value, label)
    ratios = data.get("ratios")
    if not isinstance(ratios, (list, tuple)) or not ratios:
        raise StateError(f"{label}.ratios must be a non-empty list")
    if len(ratios) > MAX_SET_SIZE:
        raise StateError(
            f"{label}.ratios has more than {MAX_SET_SIZE} classes"
        )
    anchor = data.get("anchor")
    if anchor is not None:
        anchor = _as_float(anchor, f"{label}.anchor")
    octaves_raw = data.get("octaves", ())
    if octaves_raw is None:
        octaves_raw = ()
    if not isinstance(octaves_raw, (list, tuple, Mapping)):
        raise StateError(f"{label}.octaves must be a list of [ratio, octave]")
    octaves: list[tuple[float, int]] = []
    items = (
        octaves_raw.items()
        if isinstance(octaves_raw, Mapping)
        else octaves_raw
    )
    for index, entry in enumerate(items):
        if isinstance(entry, (str, bytes)) or not isinstance(entry, (list, tuple)):
            raise StateError(
                f"{label}.octaves[{index}] must be [ratio, octave]"
            )
        if len(entry) != 2:
            raise StateError(f"{label}.octaves[{index}] must be [ratio, octave]")
        ratio = _as_float(entry[0], f"{label}.octaves[{index}] ratio")
        octave = _as_int(entry[1], f"{label}.octaves[{index}] octave")
        if not OCTAVE_MIN <= octave <= OCTAVE_MAX:
            raise StateError(
                f"{label}.octaves[{index}] octave must be between "
                f"{OCTAVE_MIN} and {OCTAVE_MAX}"
            )
        octaves.append((ratio, octave))
    try:
        return _canonical_snapshot(SetSnapshot.create(ratios, anchor, octaves))
    except (TypeError, ValueError) as exc:
        raise StateError(f"{label}: {exc}") from None


@dataclass(frozen=True, slots=True)
class MutationSettings:
    """The mutation controls a state carries (the bridge's stored values)."""

    randomness: float = MUTATION_DEFAULTS["randomness"]
    dissonance_bias: float = MUTATION_DEFAULTS["dissonance_bias"]
    stepwise_bias: float = MUTATION_DEFAULTS["stepwise_bias"]
    temperature: float = MUTATION_DEFAULTS["temperature"]
    anchor_mobility: float = MUTATION_DEFAULTS["anchor_mobility"]
    change_count: int = MUTATION_DEFAULTS["change_count"]
    glide_time: float = MUTATION_DEFAULTS["glide_time"]
    max_changes: int = MUTATION_DEFAULTS["max_changes"]

    @classmethod
    def create(
        cls,
        randomness: Any = MUTATION_DEFAULTS["randomness"],
        dissonance_bias: Any = MUTATION_DEFAULTS["dissonance_bias"],
        stepwise_bias: Any = MUTATION_DEFAULTS["stepwise_bias"],
        temperature: Any = MUTATION_DEFAULTS["temperature"],
        anchor_mobility: Any = MUTATION_DEFAULTS["anchor_mobility"],
        change_count: Any = MUTATION_DEFAULTS["change_count"],
        glide_time: Any = MUTATION_DEFAULTS["glide_time"],
        max_changes: Any = MUTATION_DEFAULTS["max_changes"],
    ) -> "MutationSettings":
        maximum = _as_int_in(max_changes, "mutation.max_changes", 1, MAX_SET_SIZE)
        return cls(
            randomness=_as_float_in(randomness, "mutation.randomness", 0.0, 1.0),
            dissonance_bias=_as_float_in(
                dissonance_bias, "mutation.dissonance_bias", -1.0, 1.0
            ),
            stepwise_bias=_as_float_in(
                stepwise_bias, "mutation.stepwise_bias", 0.0, 1.0
            ),
            temperature=_as_float_in(
                temperature, "mutation.temperature", 0.0, 1.0
            ),
            anchor_mobility=_as_float_in(
                anchor_mobility, "mutation.anchor_mobility", 0.0, 1.0
            ),
            change_count=_as_int_in(
                change_count, "mutation.change_count", 1, maximum
            ),
            glide_time=_as_float_in(
                glide_time, "mutation.glide_time", 0.0, 10.0
            ),
            max_changes=maximum,
        )

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any] | None) -> "MutationSettings":
        section = {} if data is None else _as_mapping(data, "mutation")
        values = dict(MUTATION_DEFAULTS)
        values.update(_known(section, cls))
        try:
            return cls.create(**values)
        except StateError:
            raise
        except (TypeError, ValueError) as exc:
            raise StateError(f"mutation: {exc}") from None

    def as_dict(self) -> dict[str, Any]:
        return {
            "randomness": float(self.randomness),
            "dissonance_bias": float(self.dissonance_bias),
            "stepwise_bias": float(self.stepwise_bias),
            "temperature": float(self.temperature),
            "anchor_mobility": float(self.anchor_mobility),
            "change_count": int(self.change_count),
            "glide_time": float(self.glide_time),
            "max_changes": int(self.max_changes),
        }


@dataclass(frozen=True, slots=True)
class HomeSection:
    """The four captured home slots plus the journey/return configuration."""

    slots: tuple[SetSnapshot | None, ...] = (None, None, None, None)
    active_slot: int = -1
    auto: bool = home_module.AUTO_DEFAULT
    every: int = home_module.EVERY_DEFAULT
    seed: int = home_module.SEED_DEFAULT
    max_drift: int = home_module.DRIFT_DEFAULT
    return_glide: float = home_module.RETURN_GLIDE_DEFAULT
    journey_seed: int = home_module.SEED_DEFAULT
    journey_mutations: int = 0

    @classmethod
    def create(
        cls,
        slots: Iterable[Any] = (),
        active_slot: Any = -1,
        auto: Any = home_module.AUTO_DEFAULT,
        every: Any = home_module.EVERY_DEFAULT,
        seed: Any = home_module.SEED_DEFAULT,
        max_drift: Any = home_module.DRIFT_DEFAULT,
        return_glide: Any = home_module.RETURN_GLIDE_DEFAULT,
        journey_seed: Any = home_module.SEED_DEFAULT,
        journey_mutations: Any = 0,
    ) -> "HomeSection":
        resolved: list[SetSnapshot | None] = []
        for index, entry in enumerate(slots):
            if index >= home_module.DEFAULT_SLOTS:
                raise StateError(
                    f"home.slots has more than {home_module.DEFAULT_SLOTS} entries"
                )
            resolved.append(
                None if entry is None else _snapshot_from(entry, f"home.slots[{index}]")
            )
        while len(resolved) < home_module.DEFAULT_SLOTS:
            resolved.append(None)

        slot = _as_int_in(
            active_slot, "home.active_slot", -1, home_module.DEFAULT_SLOTS - 1
        )
        if slot >= 0 and resolved[slot] is None:
            raise StateError(
                f"home.active_slot {slot} is an empty slot"
            )
        try:
            config = home_module.HomeConfig.from_mapping(
                {
                    "auto": auto,
                    "every": every,
                    "seed": seed,
                    "max_drift": max_drift,
                    "return_glide": return_glide,
                }
            )
        except (TypeError, ValueError) as exc:
            raise StateError(f"home: {exc}") from None
        return cls(
            slots=tuple(resolved),
            active_slot=slot,
            auto=bool(config.auto),
            every=int(config.every),
            seed=int(config.seed),
            max_drift=int(config.max_drift),
            return_glide=float(config.return_glide),
            journey_seed=_as_int_in(
                journey_seed,
                "home.journey_seed",
                home_module.SEED_MIN,
                home_module.SEED_MAX,
            ),
            journey_mutations=_as_int_in(
                journey_mutations, "home.journey_mutations", 0, 2**31 - 1
            ),
        )

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any] | None) -> "HomeSection":
        section = {} if data is None else _as_mapping(data, "home")
        slots = section.get("slots", ())
        if slots is None:
            slots = ()
        if isinstance(slots, (str, bytes)) or not isinstance(
            slots, (list, tuple)
        ):
            raise StateError("home.slots must be a list of four entries")
        values = {"slots": slots}
        values.update(_known(section, cls))
        try:
            return cls.create(**values)
        except StateError:
            raise
        except (TypeError, ValueError) as exc:
            raise StateError(f"home: {exc}") from None

    def as_dict(self) -> dict[str, Any]:
        return {
            "slots": [
                None if slot is None else _snapshot_mapping(slot)
                for slot in self.slots
            ],
            "active_slot": int(self.active_slot),
            "auto": bool(self.auto),
            "every": int(self.every),
            "seed": int(self.seed),
            "max_drift": int(self.max_drift),
            "return_glide": float(self.return_glide),
            "journey_seed": int(self.journey_seed),
            "journey_mutations": int(self.journey_mutations),
        }


@dataclass(frozen=True, slots=True)
class DrumsSection:
    """Drum output enable/rate/lane mask (the same 16-bit mask as /ji/drums/state)."""

    enabled: bool = False
    rate: float = 1.0
    lane_mask: int = DEFAULT_LANE_MASK

    @classmethod
    def create(
        cls,
        enabled: Any = False,
        rate: Any = 1.0,
        lane_mask: Any = DEFAULT_LANE_MASK,
    ) -> "DrumsSection":
        try:
            resolved_rate = validate_drum_rate(rate)
        except (TypeError, ValueError) as exc:
            raise StateError(f"drums.rate: {exc}") from None
        return cls(
            enabled=_as_bool(enabled, "drums.enabled"),
            rate=float(resolved_rate),
            lane_mask=_as_int_in(lane_mask, "drums.lane_mask", 0, 0xFFFF),
        )

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any] | None) -> "DrumsSection":
        section = {} if data is None else _as_mapping(data, "drums")
        return cls.create(**_known(section, cls))

    def as_dict(self) -> dict[str, Any]:
        return {
            "enabled": bool(self.enabled),
            "rate": float(self.rate),
            "lane_mask": int(self.lane_mask),
        }


@dataclass(frozen=True, slots=True)
class GrooveSection:
    """The groove modifiers plus the 32-int ``/ji/drums/grid`` payload."""

    spec: GrooveSpec = field(default_factory=GrooveSpec)
    grid: GrooveGrid = field(default_factory=lambda: preset_grid("four", 4, 4))

    @classmethod
    def create(cls, spec: Any = None, grid: Any = None) -> "GrooveSection":
        try:
            resolved = GrooveSpec.create() if spec is None else (
                spec if isinstance(spec, GrooveSpec) else GrooveSpec.create(
                    **_known(_as_mapping(spec, "groove"), GrooveSpec)
                )
            )
        except (TypeError, ValueError) as exc:
            raise StateError(f"groove: {exc}") from None
        if grid is None:
            resolved_grid = preset_grid(resolved.preset, 4, 4)
        elif isinstance(grid, GrooveGrid):
            resolved_grid = grid
        elif isinstance(grid, (list, tuple)):
            if len(grid) != 32:
                raise StateError(
                    "groove.grid must be the 32-int /ji/drums/grid payload"
                )
            try:
                resolved_grid = GrooveGrid.from_pairs(
                    [(grid[index], grid[index + 1]) for index in range(0, 32, 2)]
                )
            except (TypeError, ValueError) as exc:
                raise StateError(f"groove.grid: {exc}") from None
        else:
            raise StateError("groove.grid must be a list of 32 integers")
        return cls(spec=resolved, grid=resolved_grid)

    @classmethod
    def from_mapping(
        cls, data: Mapping[str, Any] | None, *, meter_num: int = 4, meter_den: int = 4
    ) -> "GrooveSection":
        section = {} if data is None else _as_mapping(data, "groove")
        spec_values = _known(section, GrooveSpec)
        try:
            spec = GrooveSpec.create(**spec_values)
        except (TypeError, ValueError) as exc:
            raise StateError(f"groove: {exc}") from None
        if "grid" in section and section["grid"] is not None:
            return cls.create(spec, section["grid"])
        # A hand-edited/older file without a grid falls back to the preset grid
        # for the state's meter, so the result is coherent rather than empty.
        try:
            grid = preset_grid(spec.preset, meter_num, meter_den)
        except (TypeError, ValueError) as exc:
            raise StateError(f"groove.grid: {exc}") from None
        return cls(spec=spec, grid=grid)

    def as_dict(self) -> dict[str, Any]:
        spec = self.spec
        return {
            "preset": spec.preset,
            "density": float(spec.density),
            "syncopation": float(spec.syncopation),
            "swing": float(spec.swing),
            "probability": float(spec.probability),
            "seed": int(spec.seed),
            "grid": list(self.grid.payload()),
        }


@dataclass(frozen=True, slots=True)
class LeadPlaySection:
    """Stage 9.1 inline played line: notes, Revert target, history, provenance.

    The four keys live beside the ``LeadSettings`` fields in the ``lead``
    section.  They are optional; an old state without them loads unchanged as a
    generated line.
    """

    notes: tuple[lead_module.LeadNote, ...] = ()
    base_notes: tuple[lead_module.LeadNote, ...] = ()
    history: tuple[tuple[str, int], ...] = ()
    melody: str = ""
    source: str = "generated"

    @classmethod
    def create(
        cls,
        notes: Iterable[Any] = (),
        base_notes: Iterable[Any] | None = None,
        history: Iterable[Any] = (),
        melody: Any = "",
        source: Any = "generated",
    ) -> "LeadPlaySection":
        try:
            resolved_notes = lead_module.LeadEngine._validate_notes(
                list(notes), _STATE_NOTE_STEPS
            )
            resolved_base = (
                resolved_notes
                if base_notes is None
                else lead_module.LeadEngine._validate_notes(
                    list(base_notes), _STATE_NOTE_STEPS
                )
            )
            resolved_history = lead_module.LeadEngine._validate_history(
                list(history)
            )
        except (TypeError, ValueError) as exc:
            raise StateError(f"lead.notes: {exc}") from None
        name = _as_text(melody, "lead.melody")
        kind = str(source).strip().lower()
        if kind not in ("generated", "played"):
            raise StateError("lead.source must be 'generated' or 'played'")
        if not resolved_notes:
            kind = "generated"
        return cls(
            notes=resolved_notes,
            base_notes=resolved_base or resolved_notes,
            history=resolved_history,
            melody=name,
            source=kind,
        )

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any] | None) -> "LeadPlaySection":
        section = {} if data is None else _as_mapping(data, "lead")
        notes_raw = section.get("notes", ())
        base_raw = section.get("base_notes")
        history_raw = section.get("history", ())
        if notes_raw is None:
            notes_raw = ()
        if history_raw is None:
            history_raw = ()
        if isinstance(notes_raw, (str, bytes)) or not isinstance(
            notes_raw, (list, tuple)
        ):
            raise StateError("lead.notes must be a list")
        if base_raw is not None and (
            isinstance(base_raw, (str, bytes))
            or not isinstance(base_raw, (list, tuple))
        ):
            raise StateError("lead.base_notes must be a list")
        if isinstance(history_raw, (str, bytes)) or not isinstance(
            history_raw, (list, tuple)
        ):
            raise StateError("lead.history must be a list of [name, index] pairs")
        notes = tuple(
            _lead_note_from(item, f"lead.notes[{index}]")
            for index, item in enumerate(notes_raw)
        )
        base = (
            None
            if base_raw is None
            else tuple(
                _lead_note_from(item, f"lead.base_notes[{index}]")
                for index, item in enumerate(base_raw)
            )
        )
        default_source = "played" if notes else "generated"
        return cls.create(
            notes=notes,
            base_notes=base,
            history=tuple(history_raw),
            melody=section.get("melody", ""),
            source=section.get("source", default_source),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "notes": [_lead_note_mapping(note) for note in self.notes],
            "base_notes": [_lead_note_mapping(note) for note in self.base_notes],
            "history": [[name, int(index)] for name, index in self.history],
            "melody": self.melody,
            "source": self.source,
        }


#: Permissive normalisation bound; :class:`PerformanceState` enforces the real
#: phrase length once the meter/grid/bars are known.
_STATE_NOTE_STEPS = 1_000_000


def _lead_note_mapping(note: lead_module.LeadNote) -> dict[str, Any]:
    """One inline note; the Stage 9.2 curves are always written (may be [])."""

    return {
        "step": int(note.step),
        "length": int(note.length),
        "degree": int(note.degree),
        "octave": int(note.octave),
        "ratio_hint": float(note.ratio_hint),
        "velocity": int(note.velocity),
        "bend_points": [
            [float(offset), float(cents)]
            for offset, cents in note.bend_points
        ],
        "mod_points": [
            [float(offset), int(value)] for offset, value in note.mod_points
        ],
    }


def _lead_note_from(value: Any, label: str) -> lead_module.LeadNote:
    data = _as_mapping(value, label)
    if "bend_points" in data and data["bend_points"] is not None:
        bend_points: Any = data["bend_points"]
    else:
        bend_points = ()
    if "mod_points" in data and data["mod_points"] is not None:
        mod_points: Any = data["mod_points"]
    else:
        mod_points = ()
    try:
        note = lead_module.LeadNote(
            step=_as_int(data.get("step"), f"{label}.step"),
            length=_as_int(data.get("length"), f"{label}.length"),
            degree=_as_int(data.get("degree"), f"{label}.degree"),
            octave=_as_int(data.get("octave"), f"{label}.octave"),
            ratio_hint=_as_float(data.get("ratio_hint"), f"{label}.ratio_hint"),
            velocity=_as_int(data.get("velocity"), f"{label}.velocity"),
            bend_points=bend_points,
            mod_points=mod_points,
        )
    except StateError:
        raise
    return note


def _settings_from(section: Mapping[str, Any], model: Any, label: str) -> Any:
    """Validate a section through the model's own ``create(**values)``."""

    values = _known(section, model)
    try:
        return model.create(**values)
    except (TypeError, ValueError) as exc:
        raise StateError(f"{label}: {exc}") from None


def _settings_dict(settings: Any) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for item in fields(settings):
        value = getattr(settings, item.name)
        if isinstance(value, tuple):
            value = list(value)
        result[item.name] = value
    return result


def _bass_from(section: Mapping[str, Any]) -> bass_module.BassSettings:
    values = _known(section, bass_module.BassSettings)
    try:
        settings = bass_module.BassSettings.create(**values)
    except (TypeError, ValueError) as exc:
        raise StateError(f"bass: {exc}") from None
    # ``create`` deliberately maps "custom" to the base preset (that is how a
    # fresh config is built).  A saved state must keep the custom marker so the
    # GUI still reads ``custom`` after a recall.
    if values.get("preset") == bass_module.PRESET_CUSTOM:
        settings = replace(settings, preset=bass_module.PRESET_CUSTOM)
    return settings


# ---------------------------------------------------------------------------
# The state itself
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PerformanceState:
    """An immutable, fully validated performance state."""

    name: str
    saved_at: str = ""
    freqset: SetSnapshot = SetSnapshot(ratios=(1.0,), octaves=((1.0, 0),))
    fundamental: float = 220.0
    mutation: MutationSettings = MutationSettings()
    home: HomeSection = HomeSection()
    rhythm: RhythmState = RhythmState()
    drums: DrumsSection = DrumsSection()
    groove: GrooveSection = GrooveSection()
    bass: bass_module.BassSettings = bass_module.BassSettings()
    arp: arp_module.ArpSettings = arp_module.ArpSettings()
    lead: lead_module.LeadSettings = lead_module.LeadSettings()
    lead_variation_index: int = 0
    lead_play: LeadPlaySection = LeadPlaySection()
    drone: drone_module.DroneSettings = drone_module.DroneSettings()

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", validate_state_name(self.name))
        if not isinstance(self.saved_at, str):
            raise StateError("saved_at must be text")
        if not isinstance(self.freqset, SetSnapshot):
            raise StateError("freqset must be a SetSnapshot")
        if not self.freqset.ratios:
            raise StateError("freqset.ratios cannot be empty")
        if len(self.freqset.ratios) > MAX_SET_SIZE:
            raise StateError(
                f"freqset.ratios has more than {MAX_SET_SIZE} classes"
            )
        if not isinstance(self.mutation, MutationSettings):
            raise StateError("mutation must be a MutationSettings")
        if not isinstance(self.home, HomeSection):
            raise StateError("home must be a HomeSection")
        if not isinstance(self.rhythm, RhythmState) or self.rhythm.running:
            raise StateError("rhythm must be a stopped RhythmState")
        if not isinstance(self.drums, DrumsSection):
            raise StateError("drums must be a DrumsSection")
        if not isinstance(self.groove, GrooveSection):
            raise StateError("groove must be a GrooveSection")
        for label, value, model in (
            ("bass", self.bass, bass_module.BassSettings),
            ("arp", self.arp, arp_module.ArpSettings),
            ("lead", self.lead, lead_module.LeadSettings),
            ("drone", self.drone, drone_module.DroneSettings),
        ):
            if not isinstance(value, model):
                raise StateError(f"{label} must be a {model.__name__}")
        _as_int_in(
            self.lead_variation_index, "lead.variation_index", 0, 1_000_000
        )
        if not isinstance(self.lead_play, LeadPlaySection):
            raise StateError("lead_play must be a LeadPlaySection")
        total = (
            lead_module.steps_per_bar(
                self.rhythm.meter_num, self.rhythm.meter_den, self.lead.grid
            )
            * int(self.lead.phrase_bars)
        )
        for label, notes in (
            ("lead.notes", self.lead_play.notes),
            ("lead.base_notes", self.lead_play.base_notes),
        ):
            for index, note in enumerate(notes):
                if not 0 <= note.step < total:
                    raise StateError(
                        f"{label}[{index}].step {note.step} is outside "
                        f"0..{total - 1}"
                    )
        _as_float(self.fundamental, "freqset.fundamental")
        if self.fundamental <= 0.0:
            raise StateError("freqset.fundamental must be strictly positive")

    # -- construction ---------------------------------------------------

    @classmethod
    def create(
        cls,
        name: Any,
        snapshot: SetSnapshot | None = None,
        fundamental: Any = 220.0,
        *,
        saved_at: str = "",
        mutation: MutationSettings | Mapping[str, Any] | None = None,
        home: HomeSection | Mapping[str, Any] | None = None,
        rhythm: RhythmState | Mapping[str, Any] | None = None,
        drums: DrumsSection | Mapping[str, Any] | None = None,
        groove: GrooveSection | GrooveSpec | None = None,
        grid: GrooveGrid | Sequence[int] | None = None,
        bass: bass_module.BassSettings | None = None,
        arp: arp_module.ArpSettings | None = None,
        lead: lead_module.LeadSettings | None = None,
        lead_variation_index: Any = 0,
        lead_play: LeadPlaySection | Mapping[str, Any] | None = None,
        drone: drone_module.DroneSettings | None = None,
    ) -> "PerformanceState":
        """Build a state from live models (the bridge's capture path)."""

        snap = (
            SetSnapshot.create((1.0,), 1.0, ((1.0, 0),))
            if snapshot is None
            else snapshot
        )
        if not isinstance(snap, SetSnapshot):
            raise StateError("snapshot must be a SetSnapshot")
        snap = _canonical_snapshot(snap)
        if isinstance(mutation, Mapping) or mutation is None:
            mutation_value = MutationSettings.from_mapping(mutation)
        else:
            mutation_value = mutation
        if isinstance(home, Mapping) or home is None:
            home_value = HomeSection.from_mapping(home)
        else:
            home_value = home
        if rhythm is None:
            rhythm_value = RhythmState.create()
        elif isinstance(rhythm, Mapping):
            rhythm_value = RhythmState.create(
                bpm=rhythm.get("bpm", BPM_DEFAULT),
                meter_num=rhythm.get("meter_num", METER_NUM_DEFAULT),
                meter_den=rhythm.get("meter_den", METER_DEN_DEFAULT),
            )
        else:
            rhythm_value = RhythmState.create(
                bpm=rhythm.bpm,
                meter_num=rhythm.meter_num,
                meter_den=rhythm.meter_den,
            )
        if isinstance(drums, Mapping) or drums is None:
            drums_value = DrumsSection.from_mapping(drums)
        else:
            drums_value = drums
        if isinstance(groove, GrooveSection) or (
            groove is None and grid is None
        ):
            groove_value = groove if groove is not None else GrooveSection.create()
        else:
            groove_value = GrooveSection.create(groove, grid)
        return cls(
            name=validate_state_name(name),
            saved_at=str(saved_at),
            freqset=snap,
            fundamental=_as_float(fundamental, "freqset.fundamental"),
            mutation=mutation_value,
            home=home_value,
            rhythm=rhythm_value,
            drums=drums_value,
            groove=groove_value,
            bass=bass_module.BassSettings() if bass is None else bass,
            arp=arp_module.ArpSettings() if arp is None else arp,
            lead=lead_module.LeadSettings() if lead is None else lead,
            lead_variation_index=_as_int_in(
                lead_variation_index, "lead.variation_index", 0, 1_000_000
            ),
            lead_play=(
                LeadPlaySection.create()
                if lead_play is None
                else (
                    lead_play
                    if isinstance(lead_play, LeadPlaySection)
                    else LeadPlaySection.from_mapping(lead_play)
                )
            ),
            drone=drone_module.DroneSettings() if drone is None else drone,
        )

    def with_saved_at(self, saved_at: str) -> "PerformanceState":
        return replace(self, saved_at=str(saved_at))

    # -- mapping --------------------------------------------------------

    @classmethod
    def from_mapping(
        cls, data: Mapping[str, Any], *, name: str | None = None
    ) -> "PerformanceState":
        """Parse and fully validate one state document.

        ``name`` overrides the document's ``name`` key (the library passes the
        filename stem, so a renamed file still loads under its new name).  Any
        problem raises :class:`StateError`; nothing is applied.
        """

        document = _as_mapping(data, "state")
        if document.get("format") != STATE_FORMAT:
            raise StateError(
                f'not a {STATE_FORMAT} document (format={document.get("format")!r})',
                kind="format",
            )
        if "freqset" not in document:
            raise StateError("freqset is required (a state needs a frequency set)")
        version = _as_int(document.get("version"), "version")
        if version > STATE_VERSION:
            raise StateError(
                f"unsupported version {version}; this build reads "
                f"version {STATE_VERSION}",
                kind="format",
            )
        if version < 1:
            raise StateError(f"invalid version {version}", kind="format")
        if version < STATE_VERSION:
            document = _migrate(document, version)

        raw_name = name if name is not None else document.get("name")
        state_name = validate_state_name(raw_name)

        freqset_section = _section(document, "freqset")
        ratios = freqset_section.get("ratios")
        snapshot = _snapshot_from(
            {
                "ratios": ratios,
                "anchor": freqset_section.get("anchor"),
                "octaves": freqset_section.get("octaves", ()),
            },
            "freqset",
        )
        fundamental = _as_float(
            freqset_section.get("fundamental", 220.0), "freqset.fundamental"
        )
        if fundamental <= 0.0:
            raise StateError("freqset.fundamental must be strictly positive")

        mutation = MutationSettings.from_mapping(_section(document, "mutation"))
        home = HomeSection.from_mapping(_section(document, "home"))

        rhythm_section = _section(document, "rhythm")
        try:
            rhythm = RhythmState.create(
                bpm=_key(rhythm_section, "bpm", BPM_DEFAULT),
                meter_num=_key(rhythm_section, "meter_num", METER_NUM_DEFAULT),
                meter_den=_key(rhythm_section, "meter_den", METER_DEN_DEFAULT),
            )
        except (TypeError, ValueError) as exc:
            raise StateError(f"rhythm: {exc}") from None

        drums = DrumsSection.from_mapping(_section(document, "drums"))
        groove = GrooveSection.from_mapping(
            _section(document, "groove"),
            meter_num=rhythm.meter_num,
            meter_den=rhythm.meter_den,
        )
        bass = _bass_from(_section(document, "bass"))
        arp = _settings_from(_section(document, "arp"), arp_module.ArpSettings, "arp")
        lead_section = _section(document, "lead")
        lead = _settings_from(lead_section, lead_module.LeadSettings, "lead")
        lead_variation = _as_int_in(
            lead_section.get("variation_index", 0),
            "lead.variation_index",
            0,
            1_000_000,
        )
        lead_play = LeadPlaySection.from_mapping(lead_section)
        drone = _settings_from(
            _section(document, "drone"), drone_module.DroneSettings, "drone"
        )

        return cls(
            name=state_name,
            saved_at=_as_text(document.get("saved_at", ""), "saved_at"),
            freqset=snapshot,
            fundamental=fundamental,
            mutation=mutation,
            home=home,
            rhythm=rhythm,
            drums=drums,
            groove=groove,
            bass=bass,
            arp=arp,
            lead=lead,
            lead_variation_index=lead_variation,
            lead_play=lead_play,
            drone=drone,
        )

    def to_mapping(self) -> dict[str, Any]:
        """Serialise back to the version-1 document (no loop/autotune keys)."""

        return {
            "format": STATE_FORMAT,
            "version": STATE_VERSION,
            "saved_at": self.saved_at,
            "name": self.name,
            "freqset": {
                **_snapshot_mapping(self.freqset),
                "fundamental": float(self.fundamental),
            },
            "mutation": self.mutation.as_dict(),
            "home": self.home.as_dict(),
            "rhythm": {
                "bpm": float(self.rhythm.bpm),
                "meter_num": int(self.rhythm.meter_num),
                "meter_den": int(self.rhythm.meter_den),
            },
            "drums": self.drums.as_dict(),
            "groove": self.groove.as_dict(),
            "bass": _settings_dict(self.bass),
            "arp": _settings_dict(self.arp),
            "lead": {
                **_settings_dict(self.lead),
                "variation_index": int(self.lead_variation_index),
                **self.lead_play.as_dict(),
            },
            "drone": _settings_dict(self.drone),
        }

    def to_json(self) -> str:
        return json.dumps(self.to_mapping(), indent=2, ensure_ascii=False) + "\n"

    # -- reporting ------------------------------------------------------

    def field_count(self) -> int:
        """Number of scalar values saved (the ``(N fields)`` log number)."""

        def leaves(value: Any) -> int:
            if isinstance(value, Mapping):
                return sum(leaves(item) for item in value.values())
            if isinstance(value, (list, tuple)):
                return sum(leaves(item) for item in value)
            return 1

        mapping = self.to_mapping()
        return sum(
            leaves(value)
            for key, value in mapping.items()
            if key not in ("format", "version", "saved_at", "name")
        )


# ---------------------------------------------------------------------------
# The library
# ---------------------------------------------------------------------------


class StateLibrary:
    """A directory of ``<name>.json`` states: list / save / load / delete.

    The library is silent: it raises :class:`StateError` and the bridge prints
    the one ``[STATE]`` line, so the same library can be used by tests and by
    ``--check`` without duplicating log formatting.
    """

    def __init__(self, directory: str | Path | None = None) -> None:
        self.directory = resolve_state_dir(directory)

    # -- paths ----------------------------------------------------------

    def path_for(self, name: Any) -> Path:
        return self.directory / StateName.create(name).filename

    def _existing_path(self, name: Any) -> Path | None:
        """Exact path if present, else a case-insensitive match (hand-typed)."""

        exact = self.path_for(name)
        if exact.exists():
            return exact
        key = StateName.create(name).key
        for path in self._candidates():
            if path.stem.casefold() == key:
                return path
        return None

    def _candidates(self) -> list[Path]:
        try:
            entries = list(self.directory.iterdir())
        except OSError:
            return []
        found = [
            entry
            for entry in entries
            if entry.is_file() and entry.name.endswith(STATE_EXTENSION)
            and not entry.name.endswith(STATE_TEMP_EXTENSION)
        ]
        return sorted(found, key=lambda entry: entry.name)

    # -- directory ------------------------------------------------------

    def ensure(self) -> Path:
        """Create the directory on demand; raise a clear error when it cannot."""

        directory = self.directory
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise StateError(
                f"state directory {directory} cannot be created: {exc}", kind="io"
            ) from None
        if not directory.is_dir():
            raise StateError(
                f"state directory {directory} is not a directory", kind="io"
            )
        if not os.access(directory, os.W_OK):
            raise StateError(
                f"state directory {directory} is not writable", kind="io"
            )
        return directory

    def writable(self) -> bool:
        try:
            self.ensure()
        except StateError:
            return False
        return True

    # -- queries --------------------------------------------------------

    def names(self) -> tuple[str, ...]:
        """Valid state names, sorted case-insensitively then case-sensitively."""

        names: list[str] = []
        for path in self._candidates():
            try:
                names.append(StateName.create(path.stem).value)
            except StateError:
                continue  # a stray file the player did not create through us
        return tuple(sorted(names, key=lambda value: (value.casefold(), value)))

    def count(self) -> int:
        return len(self.names())

    def exists(self, name: Any) -> bool:
        try:
            return self._existing_path(name) is not None
        except StateError:
            return False

    # -- writes ---------------------------------------------------------

    def save(self, state: PerformanceState) -> Path:
        """Atomically write ``state``; raise :class:`StateError` on failure."""

        if not isinstance(state, PerformanceState):
            raise StateError("save needs a PerformanceState")
        name = StateName.create(state.name).value
        directory = self.ensure()

        target = directory / f"{name}{STATE_EXTENSION}"
        for path in self._candidates():
            if path.stem.casefold() == name.casefold() and path.stem != name:
                raise StateError(
                    f'a state named "{path.stem}" already exists '
                    f'(names differ only in case)',
                    kind="validation",
                )

        temp = directory / f"{name}{STATE_TEMP_EXTENSION}"
        try:
            with open(temp, "w", encoding="utf-8") as handle:
                handle.write(state.to_json())
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, target)
        except OSError as exc:
            try:
                temp.unlink()
            except OSError:
                pass
            raise StateError(f"save failed: {exc}", kind="io") from None
        return target

    def delete(self, name: Any) -> bool:
        """Delete one state; ``False`` when it did not exist (a logged no-op)."""

        path = self._existing_path(name)
        if path is None:
            return False
        try:
            path.unlink()
        except OSError as exc:
            raise StateError(f"delete failed: {exc}", kind="io") from None
        return True

    # -- reads ----------------------------------------------------------

    def load(self, name: Any) -> PerformanceState:
        """Read, parse, and validate one state; raise :class:`StateError`."""

        path = self._existing_path(name)
        if path is None:
            raise StateError(f'no state named "{name}"', kind="missing")
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise StateError(f"cannot read {path.name}: {exc}", kind="io") from None
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise StateError(
                f"{path.name}: invalid JSON at line {exc.lineno}: {exc.msg}"
            ) from None
        return PerformanceState.from_mapping(data, name=path.stem)


__all__ = [
    "DEFAULT_STATE_DIR",
    "DrumsSection",
    "GrooveSection",
    "HomeSection",
    "LeadPlaySection",
    "MAX_SET_SIZE",
    "MutationSettings",
    "NAME_MAX",
    "NAME_MIN",
    "PerformanceState",
    "STATE_EXTENSION",
    "STATE_FORMAT",
    "STATE_TEMP_EXTENSION",
    "STATE_VERSION",
    "StateError",
    "StateLibrary",
    "StateName",
    "normalize_state_name",
    "resolve_state_dir",
    "validate_state_name",
]

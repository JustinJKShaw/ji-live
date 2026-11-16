"""Stage 9.1 named lead-melody library (``melodies/<name>.json``).

A melody file is the played take plus its provenance: the grid/phrase shape,
the tempo/meter it was captured at, the quantise strength, the swing, the
set ratios it was played against (provenance only -- a melody is set-relative
and loads against the **current** set), the take (``base_notes``, the Revert
target), the line at save time (``notes``) and the variation history.

The module is pure (standard library plus :mod:`control.lead`).  Name rules are
the Stage 8.5 ones (:func:`control.state.validate_state_name`) so the two
libraries cannot drift.  Saves are atomic (``.tmp`` + flush + fsync +
``os.replace``) and nothing here ever writes into ``config/``.

Format (version 1)::

    {
      "format": "ji-lead-melody", "version": 1, "name": "intro line",
      "saved_at": "2026-11-14T14:02:11", "grid": "1/8", "phrase_bars": 2,
      "meter_num": 4, "meter_den": 4, "bpm": 120.0, "quantize": 1.0,
      "swing": 0.0,
      "set": {"fundamental": 220.0, "anchor": 1.0, "ratios": [...]},
      "variation": {"name": "identity", "index": 1,
                    "history": [["invert", 1]]},
      "base_notes": [{"step": 0, ...}],
      "notes": [{"step": 0, ...}]
    }
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

try:  # Support both ``python -m control.melody`` and a package import.
    from . import lead as lead_module
    from . import rhythm as rhythm_module
    from .lead import LeadNote
    from .state import PROJECT_ROOT, StateError, validate_state_name
except ImportError:  # pragma: no cover - exercised only by direct execution
    import lead as lead_module  # type: ignore[no-redef]
    import rhythm as rhythm_module  # type: ignore[no-redef]
    from lead import LeadNote  # type: ignore[no-redef]
    from state import PROJECT_ROOT, StateError, validate_state_name  # type: ignore[no-redef]

MELODY_FORMAT = "ji-lead-melody"
MELODY_VERSION = 1
MELODY_EXTENSION = ".json"
MELODY_TEMP_EXTENSION = ".json.tmp"
DEFAULT_MELODY_DIR = PROJECT_ROOT / "melodies"


class MelodyError(ValueError):
    """One exception type for name, format, validation and I/O problems.

    ``kind`` is ``name``, ``format``, ``validation``, ``missing`` or ``io``.
    """

    def __init__(self, message: str, *, kind: str = "validation") -> None:
        super().__init__(str(message))
        self.kind = str(kind)


def resolve_melody_dir(path: str | Path | None = None) -> Path:
    """Resolve ``--melody-dir``; relative paths use the project root."""

    if path is None:
        return DEFAULT_MELODY_DIR
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = PROJECT_ROOT / candidate
    return candidate


def validate_melody_name(value: Any) -> str:
    """Trim and validate a melody name with the Stage 8.5 rules."""

    try:
        return validate_state_name(value)
    except StateError as exc:
        raise MelodyError(str(exc), kind=exc.kind) from None


def _as_mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise MelodyError(f"{label} must be a JSON object")
    return value


def _as_list(value: Any, label: str) -> list[Any]:
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)):
        raise MelodyError(f"{label} must be a list")
    return list(value)


def _as_int(value: Any, label: str) -> int:
    if isinstance(value, bool):
        raise MelodyError(f"{label} must be an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        return int(value)
    raise MelodyError(f"{label} must be an integer")


def _as_float(value: Any, label: str) -> float:
    if isinstance(value, bool):
        raise MelodyError(f"{label} must be a real number")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise MelodyError(f"{label} must be a real number") from None
    if not math.isfinite(number):
        raise MelodyError(f"{label} must be finite")
    return number


def _note_mapping(note: LeadNote) -> dict[str, Any]:
    """One note; the Stage 9.2 curves are always written (may be [])."""

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


def _note_from_mapping(value: Any, label: str) -> LeadNote:
    data = _as_mapping(value, label)
    bend_raw = data.get("bend_points")
    mod_raw = data.get("mod_points")
    try:
        note = LeadNote(
            step=_as_int(data.get("step"), f"{label}.step"),
            length=_as_int(data.get("length"), f"{label}.length"),
            degree=_as_int(data.get("degree"), f"{label}.degree"),
            octave=_as_int(data.get("octave"), f"{label}.octave"),
            ratio_hint=_as_float(data.get("ratio_hint"), f"{label}.ratio_hint"),
            velocity=_as_int(data.get("velocity"), f"{label}.velocity"),
            bend_points=() if bend_raw is None else bend_raw,
            mod_points=() if mod_raw is None else mod_raw,
        )
    except MelodyError:
        raise
    except (TypeError, ValueError) as exc:
        raise MelodyError(f"{label}: {exc}") from None
    return note


def _history_from(value: Any, label: str) -> tuple[tuple[str, int], ...]:
    if value is None:
        return ()
    entries = _as_list(value, label)
    result: list[tuple[str, int]] = []
    for position, entry in enumerate(entries):
        pair = _as_list(entry, f"{label}[{position}]")
        if len(pair) != 2:
            raise MelodyError(f"{label}[{position}] must be [name, index]")
        try:
            name = lead_module.validate_variation(pair[0])
        except (TypeError, ValueError) as exc:
            raise MelodyError(f"{label}[{position}]: {exc}") from None
        index = _as_int(pair[1], f"{label}[{position}] index")
        if index < 0:
            raise MelodyError(f"{label}[{position}] index must be >= 0")
        result.append((name, index))
    return tuple(result)


def _clean_ratios(value: Any) -> tuple[float, ...]:
    entries = _as_list(value, "set.ratios")
    if not 1 <= len(entries) <= 12:
        raise MelodyError("set.ratios must have 1..12 classes")
    ratios = tuple(_as_float(entry, "set.ratios") for entry in entries)
    if any(ratio <= 0.0 for ratio in ratios):
        raise MelodyError("set.ratios must be strictly positive")
    if any(right < left for left, right in zip(ratios, ratios[1:])):
        raise MelodyError("set.ratios must be ascending")
    return tuple(lead_module._clean_ratios(ratios))


@dataclass(frozen=True, slots=True)
class Melody:
    """One validated melody file."""

    name: str
    grid: str
    phrase_bars: int
    meter_num: int
    meter_den: int
    bpm: float
    quantize: float
    swing: float
    notes: tuple[LeadNote, ...]
    base_notes: tuple[LeadNote, ...]
    history: tuple[tuple[str, int], ...]
    variation: str
    variation_index: int
    ratios: tuple[float, ...]
    fundamental: float
    saved_at: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", validate_melody_name(self.name))
        object.__setattr__(self, "grid", lead_module.validate_grid(self.grid))
        object.__setattr__(
            self, "phrase_bars", lead_module.validate_phrase_bars(self.phrase_bars)
        )
        object.__setattr__(
            self, "meter_num", rhythm_module.clamp_meter_numerator(self.meter_num)
        )
        object.__setattr__(
            self,
            "meter_den",
            rhythm_module.validate_meter_denominator(self.meter_den),
        )
        object.__setattr__(self, "bpm", rhythm_module.clamp_bpm(self.bpm))
        object.__setattr__(
            self, "quantize", max(0.0, min(1.0, float(self.quantize)))
        )
        object.__setattr__(
            self, "swing", max(0.0, min(1.0, float(self.swing)))
        )
        object.__setattr__(
            self, "variation", lead_module.validate_variation(self.variation)
        )
        object.__setattr__(
            self, "variation_index", max(0, int(self.variation_index))
        )
        fundamental = float(self.fundamental)
        if not math.isfinite(fundamental) or fundamental <= 0.0:
            raise MelodyError("set.fundamental must be finite and positive")
        object.__setattr__(self, "fundamental", fundamental)
        ratios = tuple(float(ratio) for ratio in self.ratios)
        if not 1 <= len(ratios) <= 12 or any(ratio <= 0.0 for ratio in ratios):
            raise MelodyError("set.ratios must have 1..12 positive classes")
        object.__setattr__(self, "ratios", ratios)
        object.__setattr__(self, "saved_at", str(self.saved_at))

        total = lead_module.steps_per_bar(
            self.meter_num, self.meter_den, self.grid
        ) * int(self.phrase_bars)
        for label, notes in (("notes", self.notes), ("base_notes", self.base_notes)):
            for index, note in enumerate(notes):
                if not isinstance(note, LeadNote):
                    raise MelodyError(f"{label}[{index}] must be a LeadNote")
                if not 0 <= int(note.step) < total:
                    raise MelodyError(
                        f"{label}[{index}].step must be 0..{total - 1}"
                    )
        try:
            validated = lead_module.LeadEngine._validate_notes(self.notes, total)
            base = lead_module.LeadEngine._validate_notes(self.base_notes, total)
        except (TypeError, ValueError) as exc:
            raise MelodyError(f"notes: {exc}") from None
        object.__setattr__(self, "notes", validated)
        object.__setattr__(self, "base_notes", base or validated)
        object.__setattr__(
            self,
            "history",
            lead_module.LeadEngine._validate_history(self.history),
        )

    # -- mapping --------------------------------------------------------

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any], *, name: str | None = None) -> "Melody":
        document = _as_mapping(data, "melody")
        if document.get("format") != MELODY_FORMAT:
            raise MelodyError(
                f"not a {MELODY_FORMAT} document "
                f"(format={document.get('format')!r})",
                kind="format",
            )
        version = _as_int(document.get("version"), "version")
        if version > MELODY_VERSION:
            raise MelodyError(
                f"unsupported melody version {version}; this build reads "
                f"version {MELODY_VERSION}",
                kind="format",
            )
        if version < 1:
            raise MelodyError(f"invalid version {version}", kind="format")
        raw_name = name if name is not None else document.get("name")
        melody_name = validate_melody_name(raw_name)
        melody_set = _as_mapping(document.get("set", {}), "set")
        ratios = _clean_ratios(melody_set.get("ratios", (1.0,)))
        fundamental = _as_float(melody_set.get("fundamental", 220.0), "set.fundamental")
        variation = _as_mapping(document.get("variation", {}), "variation")
        notes_raw = _as_list(document.get("notes"), "notes")
        base_raw = document.get("base_notes")
        try:
            notes = tuple(
                _note_from_mapping(item, f"notes[{position}]")
                for position, item in enumerate(notes_raw)
            )
            base = (
                notes
                if base_raw is None
                else tuple(
                    _note_from_mapping(item, f"base_notes[{position}]")
                    for position, item in enumerate(_as_list(base_raw, "base_notes"))
                )
            )
        except MelodyError:
            raise
        try:
            return cls(
                name=melody_name,
                grid=document.get("grid", "1/8"),
                phrase_bars=_as_int(document.get("phrase_bars", 2), "phrase_bars"),
                meter_num=_as_int(document.get("meter_num", 4), "meter_num"),
                meter_den=_as_int(document.get("meter_den", 4), "meter_den"),
                bpm=_as_float(document.get("bpm", 120.0), "bpm"),
                quantize=_as_float(document.get("quantize", 1.0), "quantize"),
                swing=_as_float(document.get("swing", 0.0), "swing"),
                notes=notes,
                base_notes=base,
                history=_history_from(
                    variation.get("history", ()), "variation.history"
                ),
                variation=variation.get("name", "identity"),
                variation_index=_as_int(
                    variation.get("index", 0), "variation.index"
                ),
                ratios=ratios,
                fundamental=fundamental,
                saved_at=str(document.get("saved_at", "")),
            )
        except MelodyError:
            raise
        except (TypeError, ValueError) as exc:
            raise MelodyError(str(exc)) from None

    def to_mapping(self) -> dict[str, Any]:
        return {
            "format": MELODY_FORMAT,
            "version": MELODY_VERSION,
            "name": self.name,
            "saved_at": self.saved_at,
            "grid": self.grid,
            "phrase_bars": int(self.phrase_bars),
            "meter_num": int(self.meter_num),
            "meter_den": int(self.meter_den),
            "bpm": float(self.bpm),
            "quantize": float(self.quantize),
            "swing": float(self.swing),
            "set": {
                "fundamental": float(self.fundamental),
                "anchor": 1.0,
                "ratios": [float(ratio) for ratio in self.ratios],
            },
            "variation": {
                "name": self.variation,
                "index": int(self.variation_index),
                "history": [[name, int(index)] for name, index in self.history],
            },
            "base_notes": [_note_mapping(note) for note in self.base_notes],
            "notes": [_note_mapping(note) for note in self.notes],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_mapping(), indent=2, ensure_ascii=False) + "\n"


class MelodyLibrary:
    """A directory of ``<name>.json`` melodies: list / save / load / delete."""

    def __init__(self, directory: str | Path | None = None) -> None:
        self.directory = resolve_melody_dir(directory)

    # -- paths ----------------------------------------------------------

    def path_for(self, name: Any) -> Path:
        return self.directory / f"{validate_melody_name(name)}{MELODY_EXTENSION}"

    def _existing_path(self, name: Any) -> Path | None:
        exact = self.path_for(name)
        if exact.exists():
            return exact
        key = validate_melody_name(name).casefold()
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
            if entry.is_file()
            and entry.name.endswith(MELODY_EXTENSION)
            and not entry.name.endswith(MELODY_TEMP_EXTENSION)
        ]
        return sorted(found, key=lambda entry: entry.name)

    # -- directory ------------------------------------------------------

    def ensure(self) -> Path:
        directory = self.directory
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise MelodyError(
                f"melody directory {directory} cannot be created: {exc}", kind="io"
            ) from None
        if not directory.is_dir():
            raise MelodyError(
                f"melody directory {directory} is not a directory", kind="io"
            )
        if not os.access(directory, os.W_OK):
            raise MelodyError(
                f"melody directory {directory} is not writable", kind="io"
            )
        return directory

    def writable(self) -> bool:
        try:
            self.ensure()
        except MelodyError:
            return False
        return True

    # -- queries --------------------------------------------------------

    def names(self) -> tuple[str, ...]:
        names: list[str] = []
        for path in self._candidates():
            try:
                names.append(validate_melody_name(path.stem))
            except MelodyError:
                continue
        return tuple(sorted(names, key=lambda value: (value.casefold(), value)))

    def count(self) -> int:
        return len(self.names())

    def exists(self, name: Any) -> bool:
        try:
            return self._existing_path(name) is not None
        except MelodyError:
            return False

    # -- writes ---------------------------------------------------------

    def save(self, melody: Melody) -> Path:
        if not isinstance(melody, Melody):
            raise MelodyError("save needs a Melody")
        name = validate_melody_name(melody.name)
        directory = self.ensure()
        target = directory / f"{name}{MELODY_EXTENSION}"
        for path in self._candidates():
            if path.stem.casefold() == name.casefold() and path.stem != name:
                raise MelodyError(
                    f'a melody named "{path.stem}" already exists '
                    f"(names differ only in case)",
                    kind="validation",
                )
        temp = directory / f"{name}{MELODY_TEMP_EXTENSION}"
        try:
            with open(temp, "w", encoding="utf-8") as handle:
                handle.write(melody.to_json())
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, target)
        except OSError as exc:
            try:
                temp.unlink()
            except OSError:
                pass
            raise MelodyError(f"save failed: {exc}", kind="io") from None
        return target

    def delete(self, name: Any) -> bool:
        path = self._existing_path(name)
        if path is None:
            return False
        try:
            path.unlink()
        except OSError as exc:
            raise MelodyError(f"delete failed: {exc}", kind="io") from None
        return True

    # -- reads ----------------------------------------------------------

    def load(self, name: Any) -> Melody:
        path = self._existing_path(name)
        if path is None:
            raise MelodyError(f'no melody named "{name}"', kind="missing")
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise MelodyError(f"cannot read {path.name}: {exc}", kind="io") from None
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise MelodyError(
                f"{path.name}: invalid JSON at line {exc.lineno}: {exc.msg}"
            ) from None
        return Melody.from_mapping(data, name=path.stem)


__all__ = [
    "DEFAULT_MELODY_DIR",
    "MELODY_EXTENSION",
    "MELODY_FORMAT",
    "MELODY_TEMP_EXTENSION",
    "MELODY_VERSION",
    "Melody",
    "MelodyError",
    "MelodyLibrary",
    "resolve_melody_dir",
    "validate_melody_name",
]

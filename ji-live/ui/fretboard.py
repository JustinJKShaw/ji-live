"""Stage 8.8 12-TET fretboard improvisation aid -- pure note maths.

No PyQt6, no sockets, no audio and no ``osc_control`` import: standard library
only.  The module answers three questions for the GUI:

* what is the *nearest 12-TET note* to every frequency in the live JI set, and
  how many cents away is it?
* how do those notes deduplicate into pitch classes (a scale), in the order
  they appear above the fundamental?
* where is each pitch class on a guitar neck with an arbitrary tuning?

The aid is a **pitch-class** aid: a note is marked at every octave on the
fretboard.  It never suggests a register or a voicing.

Conventions (see ``docs/README_stage8_8.md``):

* MIDI 69 = A4 = 440 Hz; C4 = MIDI 60.
* Note names are sharps only: ``C C# D D# E F F# G G# A A# B``.
* ``cents`` is signed: negative means the JI frequency is **flat** of the
  12-TET note, positive means sharp.
* Rounding is half-up (``floor(x + 0.5)``), never Python's banker's ``round``.
"""

from __future__ import annotations

import math
import operator
from collections.abc import Sequence as _SequenceABC
from dataclasses import dataclass
from typing import Sequence

NOTE_NAMES: tuple[str, ...] = (
    "C",
    "C#",
    "D",
    "D#",
    "E",
    "F",
    "F#",
    "G",
    "G#",
    "A",
    "A#",
    "B",
)

A4_MIDI = 69
A4_HZ = 440.0

MIN_STRINGS = 6
MAX_STRINGS = 8
DEFAULT_STRINGS: tuple[int, ...] = (40, 45, 50, 55, 59, 64)  # E2 A2 D3 G3 B3 E4

MIN_FRETS = 0
MAX_FRETS = 24
DEFAULT_FRETS = 24

#: Exact MIDI numbers, low string -> high string (the combo uses this order).
TUNING_PRESETS: tuple[tuple[str, tuple[int, ...]], ...] = (
    ("Standard E", (40, 45, 50, 55, 59, 64)),
    ("Drop D", (38, 45, 50, 55, 59, 64)),
    ("Drop C", (36, 43, 48, 53, 57, 62)),
    ("DADGAD", (38, 45, 50, 55, 57, 62)),
    ("Open G", (38, 43, 50, 55, 59, 62)),
    ("Open D", (38, 45, 50, 54, 57, 62)),
    ("Half-step down", (39, 44, 49, 54, 58, 63)),
    ("7-string B", (35, 40, 45, 50, 55, 59, 64)),
    ("8-string F#", (30, 35, 40, 45, 50, 55, 59, 64)),
)
TUNING_PRESET_MAP: dict[str, tuple[int, ...]] = dict(TUNING_PRESETS)

#: The label the tuning combo shows for a tuning that is not a preset.
CUSTOM_PRESET = "Custom"


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def _as_float(value: object, label: str, *, positive: bool = False) -> float:
    """Coerce a real number; reject bools, strings and non-numbers."""

    if isinstance(value, bool) or isinstance(value, (str, bytes)):
        raise TypeError(f"{label} must be a number")
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise TypeError(f"{label} must be a number") from exc
    if not math.isfinite(number):
        raise ValueError(f"{label} must be finite")
    if positive and number <= 0.0:
        raise ValueError(f"{label} must be > 0")
    return number


def _as_int(value: object, label: str) -> int:
    """Coerce a true integer; reject bools, strings and floats."""

    if isinstance(value, bool) or isinstance(value, (str, bytes)):
        raise TypeError(f"{label} must be an integer")
    try:
        return int(operator.index(value))  # type: ignore[arg-type]
    except TypeError as exc:
        raise TypeError(f"{label} must be an integer") from exc


def _pitch_class(value: object, label: str) -> int:
    number = _as_int(value, label)
    if not 0 <= number <= 11:
        raise ValueError(f"{label} must be 0..11")
    return number


def _midi_note(value: object, label: str) -> int:
    number = _as_int(value, label)
    if not 0 <= number <= 127:
        raise ValueError(f"{label} must be 0..127")
    return number


def _validate_strings(strings: object) -> tuple[int, ...]:
    if isinstance(strings, (str, bytes, bool)) or not isinstance(
        strings, _SequenceABC
    ):
        raise TypeError("strings must be a sequence of MIDI notes")
    values = tuple(strings)
    if not MIN_STRINGS <= len(values) <= MAX_STRINGS:
        raise ValueError(
            f"a tuning needs {MIN_STRINGS}..{MAX_STRINGS} strings"
        )
    return tuple(_midi_note(value, "string") for value in values)


# ---------------------------------------------------------------------------
# Note maths
# ---------------------------------------------------------------------------


def hz_to_midi_float(hz: object) -> float:
    """Frequency -> fractional MIDI note (A4 = 69 = 440 Hz)."""

    value = _as_float(hz, "frequency", positive=True)
    return A4_MIDI + 12.0 * math.log2(value / A4_HZ)


def midi_to_hz(midi: object) -> float:
    """Fractional MIDI note -> frequency in Hz."""

    value = _as_float(midi, "MIDI note")
    return A4_HZ * 2.0 ** ((value - A4_MIDI) / 12.0)


def nearest_12tet(midi_float: object) -> tuple[int, float]:
    """Nearest 12-TET MIDI note and signed cents (half-up rounding)."""

    value = _as_float(midi_float, "MIDI note")
    nearest = int(math.floor(value + 0.5))
    cents = (value - nearest) * 100.0
    return nearest, cents


def midi_note_name(midi: object) -> str:
    """``60 -> "C4"``; any integer is accepted (fret 24 can pass MIDI 127)."""

    value = _as_int(midi, "MIDI note")
    return f"{NOTE_NAMES[value % 12]}{value // 12 - 1}"


def note_name(pitch_class: object) -> str:
    """Validated pitch class (0..11) -> note name."""

    return NOTE_NAMES[_pitch_class(pitch_class, "pitch class")]


def ratio_pitch_class(fundamental: object, ratio: object) -> int:
    """Pitch class reached by ``fundamental * ratio``."""

    base = _as_float(fundamental, "fundamental", positive=True)
    factor = _as_float(ratio, "ratio", positive=True)
    hz = base * factor
    if not math.isfinite(hz):
        raise ValueError("fundamental * ratio must be finite")
    return nearest_12tet(hz_to_midi_float(hz))[0] % 12


# ---------------------------------------------------------------------------
# Scale degrees
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ScaleSource:
    """One set ratio that reaches a 12-TET pitch class."""

    ratio: float
    cents: float


@dataclass(frozen=True, slots=True)
class ScaleDegree:
    """One 12-TET pitch class of the aid's scale."""

    pitch_class: int
    sources: tuple[ScaleSource, ...]

    @property
    def name(self) -> str:
        return NOTE_NAMES[self.pitch_class]

    @property
    def best_source(self) -> ScaleSource:
        return self.sources[0]

    @property
    def cents(self) -> float:
        return self.best_source.cents


def root_pitch_class(fundamental: object) -> int:
    """The 12-TET pitch class of the fundamental (the scale root)."""

    base = _as_float(fundamental, "fundamental", positive=True)
    return nearest_12tet(hz_to_midi_float(base))[0] % 12


def scale_degrees(
    fundamental: object, ratios: object
) -> tuple[ScaleDegree, ...]:
    """Deduplicated 12-TET degrees of a JI set, in scale order from the root.

    The fundamental is validated even for an empty set.  Each degree lists
    every distinct ratio reaching its pitch class, sorted by ``abs(cents)``
    then ratio; the first source is the best (closest) one.
    """

    base = _as_float(fundamental, "fundamental", positive=True)
    if isinstance(ratios, (str, bytes)):
        raise TypeError("ratios must be a sequence of numbers")
    try:
        items = list(ratios)  # type: ignore[arg-type]
    except TypeError as exc:
        raise TypeError("ratios must be a sequence of numbers") from exc
    if not items:
        return ()

    root = root_pitch_class(base)
    grouped: dict[int, list[ScaleSource]] = {}
    for raw in items:
        ratio = _as_float(raw, "ratio", positive=True)
        hz = base * ratio
        if not math.isfinite(hz):
            raise ValueError("fundamental * ratio must be finite")
        nearest, cents = nearest_12tet(hz_to_midi_float(hz))
        pitch_class = nearest % 12
        sources = grouped.setdefault(pitch_class, [])
        # Two ratios within 1e-12 are the same set member; keep the first.
        if any(
            abs(source.ratio - ratio) <= 1e-12 for source in sources
        ):
            continue
        sources.append(ScaleSource(ratio=ratio, cents=cents))

    degrees = []
    for pitch_class, sources in grouped.items():
        sources.sort(key=lambda source: (abs(source.cents), source.ratio))
        degrees.append(
            ScaleDegree(pitch_class=pitch_class, sources=tuple(sources))
        )
    degrees.sort(key=lambda degree: (degree.pitch_class - root) % 12)
    return tuple(degrees)


def scale_summary(degrees: Sequence[ScaleDegree]) -> str:
    """``"A B C# D E F# G#"`` -- the legend's one-line scale."""

    if isinstance(degrees, (str, bytes)):
        raise TypeError("degrees must be a sequence of ScaleDegree")
    names = []
    for degree in degrees:
        if not isinstance(degree, ScaleDegree):
            raise TypeError("degrees must contain ScaleDegree values")
        names.append(degree.name)
    return " ".join(names)


# ---------------------------------------------------------------------------
# Guitar tuning
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GuitarTuning:
    """One guitar tuning: MIDI numbers, low string -> high string."""

    strings: tuple[int, ...] = DEFAULT_STRINGS

    def __post_init__(self) -> None:
        object.__setattr__(self, "strings", _validate_strings(self.strings))

    @classmethod
    def create(cls, strings: object = DEFAULT_STRINGS) -> "GuitarTuning":
        return cls(strings=_validate_strings(strings))

    @property
    def string_count(self) -> int:
        return len(self.strings)

    @property
    def labels(self) -> tuple[str, ...]:
        return tuple(midi_note_name(midi) for midi in self.strings)

    @property
    def preset(self) -> str:
        for label, strings in TUNING_PRESETS:
            if self.strings == strings:
                return label
        return CUSTOM_PRESET

    def with_string(self, index: object, midi: object) -> "GuitarTuning":
        position = _as_int(index, "string index")
        if not 0 <= position < self.string_count:
            raise ValueError("string index is out of range")
        values = list(self.strings)
        values[position] = _midi_note(midi, "string")
        return GuitarTuning.create(tuple(values))

    def with_count(self, count: object) -> "GuitarTuning":
        """6..8 strings; growing prepends a low fourth, shrinking drops lows."""

        target = _as_int(count, "string count")
        if not MIN_STRINGS <= target <= MAX_STRINGS:
            raise ValueError(
                f"string count must be {MIN_STRINGS}..{MAX_STRINGS}"
            )
        current = self.string_count
        if target == current:
            return self
        values = list(self.strings)
        if target > current:
            for _ in range(target - current):
                values.insert(0, max(0, values[0] - 5))
        else:
            del values[: current - target]
        return GuitarTuning.create(tuple(values))


DEFAULT_TUNING = GuitarTuning.create(DEFAULT_STRINGS)


# ---------------------------------------------------------------------------
# Fretboard cells
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FretboardCell:
    """One string/fret position on the board."""

    string: int  # index into tuning.strings; 0 = lowest
    fret: int  # 0 = open string
    midi: int  # open + fret (may exceed 127 high up)
    pitch_class: int  # midi % 12
    degree: ScaleDegree | None  # None when not in the 12-TET set
    is_root: bool
    is_anchor: bool


def build_fretboard(
    tuning: GuitarTuning,
    degrees: Sequence[ScaleDegree],
    *,
    frets: object = DEFAULT_FRETS,
    root_pc: object | None = None,
    anchor_pc: object | None = None,
) -> tuple[FretboardCell, ...]:
    """All string/fret cells, string-major (string 0 = lowest)."""

    if not isinstance(tuning, GuitarTuning):
        raise TypeError("tuning must be a GuitarTuning")
    last_fret = _as_int(frets, "frets")
    if not MIN_FRETS <= last_fret <= MAX_FRETS:
        raise ValueError(f"frets must be {MIN_FRETS}..{MAX_FRETS}")
    root = None if root_pc is None else _pitch_class(root_pc, "root pitch class")
    anchor = (
        None
        if anchor_pc is None
        else _pitch_class(anchor_pc, "anchor pitch class")
    )

    if isinstance(degrees, (str, bytes)):
        raise TypeError("degrees must be a sequence of ScaleDegree")
    by_class: dict[int, ScaleDegree] = {}
    for degree in degrees:
        if not isinstance(degree, ScaleDegree):
            raise TypeError("degrees must contain ScaleDegree values")
        by_class.setdefault(degree.pitch_class, degree)

    cells = []
    for string_index, open_midi in enumerate(tuning.strings):
        for fret in range(last_fret + 1):
            midi = open_midi + fret
            pitch_class = midi % 12
            degree = by_class.get(pitch_class)
            marked = degree is not None
            cells.append(
                FretboardCell(
                    string=string_index,
                    fret=fret,
                    midi=midi,
                    pitch_class=pitch_class,
                    degree=degree,
                    is_root=bool(marked and root is not None and pitch_class == root),
                    is_anchor=bool(
                        marked and anchor is not None and pitch_class == anchor
                    ),
                )
            )
    return tuple(cells)


__all__ = [
    "A4_HZ",
    "A4_MIDI",
    "CUSTOM_PRESET",
    "DEFAULT_FRETS",
    "DEFAULT_STRINGS",
    "DEFAULT_TUNING",
    "FretboardCell",
    "GuitarTuning",
    "MAX_FRETS",
    "MAX_STRINGS",
    "MIN_FRETS",
    "MIN_STRINGS",
    "NOTE_NAMES",
    "ScaleDegree",
    "ScaleSource",
    "TUNING_PRESETS",
    "TUNING_PRESET_MAP",
    "build_fretboard",
    "hz_to_midi_float",
    "midi_note_name",
    "midi_to_hz",
    "nearest_12tet",
    "note_name",
    "ratio_pitch_class",
    "root_pitch_class",
    "scale_degrees",
    "scale_summary",
]

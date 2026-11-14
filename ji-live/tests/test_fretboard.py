#!/usr/bin/env python3
"""Stage 8.8 fretboard improvisation aid -- pure harness (stdlib only).

Run from ``ji-live/``::

    ~/.ji-tools/uv run python tests/test_fretboard.py

No PyQt6, no python-osc, no sockets.  ``ui/fretboard.py`` is stdlib-only, so
this harness also proves the pure module never needs the GUI stack.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Allow both ``python tests/test_fretboard.py`` and ``python -m ...``.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from ui import fretboard  # noqa: E402
from ui.fretboard import (  # noqa: E402
    DEFAULT_FRETS,
    DEFAULT_STRINGS,
    DEFAULT_TUNING,
    GuitarTuning,
    build_fretboard,
    hz_to_midi_float,
    midi_note_name,
    midi_to_hz,
    nearest_12tet,
    note_name,
    ratio_pitch_class,
    root_pitch_class,
    scale_degrees,
    scale_summary,
)

A_MAJOR_RATIOS = [1.0, 9.0 / 8.0, 5.0 / 4.0, 4.0 / 3.0, 3.0 / 2.0, 5.0 / 3.0, 15.0 / 8.0]
A_MAJOR_NAMES = ["A", "B", "C#", "D", "E", "F#", "G#"]
A_MAJOR_CENTS = [0.0, 3.91, -13.69, -1.96, 1.96, -15.64, -11.73]


def _raises(kind: type[BaseException], function, *args, **kwargs) -> None:
    try:
        function(*args, **kwargs)
    except kind:
        return
    except BaseException as exc:  # noqa: BLE001 - a wrong type is a failure too
        raise AssertionError(
            f"{function.__name__}{args!r} raised {type(exc).__name__}, "
            f"expected {kind.__name__}"
        ) from exc
    raise AssertionError(
        f"{function.__name__}{args!r} did not raise {kind.__name__}"
    )


def _cell(cells, string: int, fret: int):
    for cell in cells:
        if cell.string == string and cell.fret == fret:
            return cell
    raise AssertionError(f"no cell at string {string} fret {fret}")


def test_note_maths() -> None:
    print("\n=== 1. note maths ===")
    assert hz_to_midi_float(440) == 69.0
    assert midi_to_hz(69) == 440.0
    assert abs(midi_to_hz(60) - 261.6255653005986) < 1e-9
    assert abs(hz_to_midi_float(261.6255653005986) - 60.0) < 1e-9
    assert midi_note_name(60) == "C4"
    assert midi_note_name(40) == "E2"
    assert midi_note_name(45) == "A2"
    assert midi_note_name(50) == "D3"
    assert midi_note_name(55) == "G3"
    assert midi_note_name(59) == "B3"
    assert midi_note_name(64) == "E4"
    assert midi_note_name(0) == "C-1"
    assert midi_note_name(127) == "G9"
    assert midi_note_name(128) == "G#9", "a fret above 127 still has a name"
    assert note_name(0) == "C" and note_name(11) == "B"
    assert ratio_pitch_class(220, 3.0 / 2.0) == 4  # E
    assert ratio_pitch_class(220, 1.0) == 9  # A
    _raises(ValueError, hz_to_midi_float, 0)
    _raises(ValueError, hz_to_midi_float, -1)
    _raises(ValueError, hz_to_midi_float, float("nan"))
    _raises(ValueError, hz_to_midi_float, float("inf"))
    _raises(ValueError, midi_to_hz, float("nan"))
    _raises(ValueError, ratio_pitch_class, 220, 0)
    _raises(ValueError, ratio_pitch_class, -220, 1)
    _raises(ValueError, note_name, 12)
    _raises(TypeError, note_name, "A")
    _raises(TypeError, midi_note_name, 60.0)
    _raises(TypeError, midi_note_name, True)
    print("  Hz <-> MIDI, C4=60 naming, ratio pitch classes, invalid inputs")


def test_half_up_rounding() -> None:
    print("\n=== 2. half-up rounding (never banker's) ===")
    assert nearest_12tet(57.5) == (58, -50.0)
    assert nearest_12tet(58.5) == (59, -50.0)
    assert nearest_12tet(69.0) == (69, 0.0)
    nearest, cents = nearest_12tet(hz_to_midi_float(261.625565))
    assert nearest == 60 and abs(cents) < 0.01
    rounded, rounded_cents = nearest_12tet(60.49)
    assert rounded == 60 and abs(rounded_cents - 49.0) < 1e-9
    assert nearest_12tet(-0.5) == (0, -50.0)
    print("  57.5 -> 58, 58.5 -> 59, A4/C4 exact, signed cents")


def test_a_major_set() -> None:
    print("\n=== 3. worked A-major set (A = 220 Hz) ===")
    degrees = scale_degrees(220.0, A_MAJOR_RATIOS)
    assert [degree.name for degree in degrees] == A_MAJOR_NAMES
    assert [degree.pitch_class for degree in degrees] == [9, 11, 1, 2, 4, 6, 8]
    for degree, expected in zip(degrees, A_MAJOR_CENTS):
        assert abs(degree.cents - expected) < 0.01, (
            degree.name,
            degree.cents,
            expected,
        )
    assert root_pitch_class(220.0) == 9
    assert scale_summary(degrees) == "A B C# D E F# G#"
    # Every degree's sources are sorted closest-first.
    for degree in degrees:
        keys = [(abs(s.cents), s.ratio) for s in degree.sources]
        assert keys == sorted(keys), degree
        assert degree.best_source is degree.sources[0]
    # The exact worked table.
    table = [(1.0, 57.0, 57), (9 / 8, 59.039, 59), (5 / 4, 60.863, 61),
             (4 / 3, 61.980, 62), (3 / 2, 64.020, 64), (5 / 3, 65.844, 66),
             (15 / 8, 67.883, 68)]
    for ratio, midi_float, nearest in table:
        assert abs(hz_to_midi_float(220.0 * ratio) - midi_float) < 0.001
        assert nearest_12tet(hz_to_midi_float(220.0 * ratio))[0] == nearest
    print("  A B C# D E F# G#, cents, root 9, table, source order")


def test_duplicates() -> None:
    print("\n=== 4. duplicates and octave-unison ratios ===")
    degrees = scale_degrees(220.0, [9.0 / 8.0, 10.0 / 9.0])
    assert len(degrees) == 1, degrees
    degree = degrees[0]
    assert degree.name == "B" and degree.pitch_class == 11
    assert len(degree.sources) == 2
    assert degree.best_source.ratio == 9.0 / 8.0
    assert degree.sources[1].ratio == 10.0 / 9.0
    assert abs(degree.sources[0].cents) < abs(degree.sources[1].cents)

    # A 2.0 ratio rounds to the next octave and groups with the root.
    octave = scale_degrees(220.0, [1.0, 2.0])
    assert len(octave) == 1 and octave[0].pitch_class == 9
    assert len(octave[0].sources) == 2
    assert octave[0].best_source.ratio == 1.0
    # A ratio repeated within 1e-12 is one source, not two.
    repeated = scale_degrees(220.0, [1.0, 1.0 + 1e-13, 1.0])
    assert len(repeated) == 1 and len(repeated[0].sources) == 1
    print("  9/8 + 10/9 -> one B (best 9/8); 2/1 dedupes with 1/1")


def test_432_hz() -> None:
    print("\n=== 5. a different fundamental keeps the note names ===")
    degrees = scale_degrees(432.0, A_MAJOR_RATIOS)
    assert [degree.name for degree in degrees] == A_MAJOR_NAMES
    assert root_pitch_class(432.0) == 9
    # Cents move with the reference (432 vs 440 is -31.77 cents at A).
    a_cents = degrees[0].cents
    assert abs(a_cents - (-31.767)) < 0.01, a_cents
    print("  432 Hz: same A B C# D E F# G#, cents shifted")


def test_empty_and_invalid() -> None:
    print("\n=== 6. empty and invalid sets ===")
    assert scale_degrees(220.0, []) == ()
    assert scale_degrees(220.0, ()) == ()
    _raises(ValueError, scale_degrees, 0, [1.0])
    _raises(ValueError, scale_degrees, 220, [0.0])
    _raises(ValueError, scale_degrees, 220, [-1.5])
    _raises(ValueError, scale_degrees, 220, [float("nan")])
    _raises(ValueError, scale_degrees, 220, [1.0, float("inf")])
    _raises(TypeError, scale_degrees, 220, "123")
    _raises(TypeError, scale_degrees, 220, None)
    _raises(ValueError, scale_degrees, 220, [1.0, 1e308])
    _raises(TypeError, scale_summary, ["A"])
    _raises(TypeError, scale_summary, "A B")
    assert scale_summary(()) == ""
    print("  empty -> (), non-positive/NaN/inf/strings rejected")


def test_tuning() -> None:
    print("\n=== 7. guitar tuning validation, labels and presets ===")
    assert DEFAULT_TUNING.strings == DEFAULT_STRINGS
    assert DEFAULT_TUNING.string_count == 6
    assert DEFAULT_TUNING.labels == ("E2", "A2", "D3", "G3", "B3", "E4")
    assert DEFAULT_TUNING.preset == "Standard E"
    assert GuitarTuning.create(list(DEFAULT_STRINGS)) == DEFAULT_TUNING
    assert GuitarTuning().strings == DEFAULT_STRINGS

    drop_d = GuitarTuning.create((38, 45, 50, 55, 59, 64))
    assert drop_d.preset == "Drop D"
    assert drop_d.labels == ("D2", "A2", "D3", "G3", "B3", "E4")
    assert fretboard.TUNING_PRESET_MAP["DADGAD"] == (38, 45, 50, 55, 57, 62)
    assert [label for label, _ in fretboard.TUNING_PRESETS][:3] == [
        "Standard E",
        "Drop D",
        "Drop C",
    ]
    seven = GuitarTuning.create(fretboard.TUNING_PRESET_MAP["7-string B"])
    assert seven.preset == "7-string B" and seven.string_count == 7
    eight = GuitarTuning.create(fretboard.TUNING_PRESET_MAP["8-string F#"])
    assert eight.string_count == 8 and eight.strings[0] == 30

    for bad in (
        (40, 45, 50, 55, 59),
        tuple(range(40, 49)),
        (40, 45, 50, 55, 59, 128),
        (40, 45, 50, 55, 59, -1),
        (40, 45, 50, 55, 59, True),
        (40, 45, 50, 55, 59, 64.0),
        "404550555964",
        (),
        None,
    ):
        _raises((TypeError, ValueError), GuitarTuning.create, bad)

    custom = GuitarTuning.create((40, 45, 50, 55, 59, 65))
    assert custom.preset == "Custom"
    assert custom.with_string(5, 64).with_string(0, 38) == drop_d
    assert custom.with_string(5, 63).strings[5] == 63
    _raises(ValueError, custom.with_string, -1, 40)
    _raises(ValueError, custom.with_string, 6, 40)
    _raises(ValueError, custom.with_string, 0, 128)
    _raises(TypeError, custom.with_string, 0, 40.0)
    print("  6..8 strings, 0..127 notes, labels, nine presets, Custom")


def test_with_count() -> None:
    print("\n=== 8. with_count grows low / shrinks lows ===")
    six = DEFAULT_TUNING
    seven = six.with_count(7)
    assert seven.strings == (35, 40, 45, 50, 55, 59, 64)
    assert seven.labels[0] == "B1"
    assert seven.preset == "7-string B"
    eight = seven.with_count(8)
    assert eight.strings == (30, 35, 40, 45, 50, 55, 59, 64)
    assert eight.labels[0] == "F#1"
    assert eight.preset == "8-string F#"
    assert eight.with_count(6) == six
    assert eight.with_count(7) == seven
    assert six.with_count(6) is six
    assert six.with_count(8).with_count(6) == six
    _raises(ValueError, six.with_count, 5)
    _raises(ValueError, six.with_count, 9)
    _raises(TypeError, six.with_count, 7.0)
    # The clamp: a low string never goes below MIDI 0.
    low = GuitarTuning.create((1, 5, 10, 15, 20, 25))
    assert low.with_count(7).strings[0] == 0
    assert low.with_count(8).strings[:2] == (0, 0)
    print("  6->7 adds B1, 7->8 adds F#1, 8->6 drops them, clamp at 0")


def test_fretboard_cells() -> None:
    print("\n=== 9. fretboard cells ===")
    degrees = scale_degrees(220.0, A_MAJOR_RATIOS)
    cells = build_fretboard(DEFAULT_TUNING, degrees, frets=24, root_pc=9)
    assert len(cells) == 6 * 25 == 150
    assert all(cell.string in range(6) for cell in cells)
    assert cells[0].string == 0 and cells[0].fret == 0
    assert cells[-1].string == 5 and cells[-1].fret == 24
    assert cells[4].midi == 44 and cells[4].pitch_class == 8  # G2

    open_g = _cell(cells, 3, 0)
    assert open_g.midi == 55 and open_g.pitch_class == 7
    assert open_g.degree is None and not open_g.is_root
    g_sharp = _cell(cells, 3, 1)
    assert g_sharp.midi == 56 and g_sharp.pitch_class == 8
    assert g_sharp.degree is not None and g_sharp.degree.name == "G#"
    open_a = _cell(cells, 1, 0)
    assert open_a.degree is not None and open_a.degree.name == "A"
    assert open_a.is_root and not open_a.is_anchor
    assert not _cell(cells, 2, 0).is_root  # open D is in the set, not the root

    marked_classes = {degree.pitch_class for degree in degrees}
    expected_marked = sum(
        1
        for open_midi in DEFAULT_STRINGS
        for fret in range(25)
        if (open_midi + fret) % 12 in marked_classes
    )
    assert sum(1 for cell in cells if cell.degree is not None) == expected_marked

    anchored = build_fretboard(
        DEFAULT_TUNING, degrees, frets=24, root_pc=9, anchor_pc=4
    )
    e_cells = [cell for cell in anchored if cell.degree and cell.degree.name == "E"]
    assert e_cells and all(cell.is_anchor for cell in e_cells)
    assert all(not cell.is_anchor for cell in anchored if not cell.degree)

    empty_board = build_fretboard(DEFAULT_TUNING, ())
    assert len(empty_board) == 150
    assert all(cell.degree is None for cell in empty_board)

    zero = build_fretboard(DEFAULT_TUNING, degrees, frets=0)
    assert len(zero) == 6
    twelve = build_fretboard(DEFAULT_TUNING, degrees, frets=12)
    assert len(twelve) == 6 * 13

    _raises(ValueError, build_fretboard, DEFAULT_TUNING, degrees, frets=25)
    _raises(ValueError, build_fretboard, DEFAULT_TUNING, degrees, frets=-1)
    _raises(TypeError, build_fretboard, DEFAULT_TUNING, degrees, frets=1.0)
    _raises(ValueError, build_fretboard, DEFAULT_TUNING, degrees, root_pc=12)
    _raises(ValueError, build_fretboard, DEFAULT_TUNING, degrees, anchor_pc=-1)
    _raises(TypeError, build_fretboard, DEFAULT_STRINGS, degrees)
    _raises(TypeError, build_fretboard, DEFAULT_TUNING, ["A"])
    assert DEFAULT_FRETS == 24
    print("  150 cells, open G unmarked, G# fret 1, open A root, anchor flag")


def test_scale_summary() -> None:
    print("\n=== 10. scale summary ===")
    degrees = scale_degrees(220.0, A_MAJOR_RATIOS)
    assert scale_summary(degrees) == "A B C# D E F# G#"
    assert scale_summary(degrees[:1]) == "A"
    assert scale_summary(()) == ""
    _raises(TypeError, scale_summary, "A B")
    print("  join in scale order; empty -> empty string")


def main() -> int:
    print("JI Stage 8.8 -- 12-TET fretboard pure harness")
    test_note_maths()
    test_half_up_rounding()
    test_a_major_set()
    test_duplicates()
    test_432_hz()
    test_empty_and_invalid()
    test_tuning()
    test_with_count()
    test_fretboard_cells()
    test_scale_summary()
    print("\nALL FRETBOARD TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

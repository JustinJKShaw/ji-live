# Stage 8.8 Agent Prompt — 12-TET fretboard improvisation aid

You are implementing **Stage 8.8** of the JI live-performance system. Stage 8.7
(breathing rhythm engine + bass hit row) and its follow-up 8.7.1 (`Arp sync`)
are built and awaiting the owner's listening check. This stage is deliberately
small in risk and large in usefulness: a **read-only GUI improvisation aid**
that shows the nearest 12-TET notes to the live JI set and draws them on a
fully tunable guitar fretboard.

The owner is the authority on whether the aid is useful. The agent environment
may have no PyQt6 (the Qt wheel needs `libEGL.so.1` in the container) and no
audio; nothing in this stage touches audio, MIDI, sclang or the bridge, so the
verification is the pure Python harness plus — if Qt is available — the
offscreen Qt smoke test and the owner's eyes.

---

## 0. What the owner said (verbatim, 2026-11-05)

> "ok the next feature i want implemented is an improvisation aid that displays
> the closest 12tet notes to the frequencies in the set (i have noticed guitar
> only sounds good if played close to these notes), and displays all of these
> 12tet notes on a guitar fretboard (a full fretboard scale diagram), with an
> ability to alter the guitars tuning (so i can use it in drop d for example,
> this should be fully flexible towards whatever tuning)."

Decoded into requirements:

1. **Nearest 12-TET set**: for every frequency in the current JI set, find the
   closest 12-TET note; show the resulting note names and how far each JI
   frequency is from that note (cents).
2. **Full fretboard diagram**: draw a complete guitar fretboard (6 strings,
   24 frets by default) and mark every position whose note is one of those
   nearest 12-TET notes.
3. **Fully flexible tuning**: every open string is editable (MIDI note 0–127),
   with presets including **Drop D**, plus 7- and 8-string defaults. Tuning is
   a **local UI setting**; it does not touch audio, MIDI, OSC or a performance
   state.
4. **Live**: the diagram follows `/ji/freqset/changed` (mutations, state loads,
   hello), with no restart and no new bridge message.

---

## 1. Read these files first

```text
docs/README_stage8_7.md                 the baseline: breath, set delivery, 8.7.1
docs/START_HERE.md                      controls + troubleshooting (update it)
docs/GUI_PARAMETERS.md                  every control, one line each (update it)
docs/design_doc_10.28.10.50             §6.14 (set/breath), §6.21, §7.1,
                                        §8.9, §9, §13, §14
ui/osc_control.py                       FrequencySet, ji_ratio_label, StatusStore
ui/ji_ui.py                             MainWindow, _on_status_message,
                                        group builders, Knob, window layout
tests/test_ui.py                        pure harness + three Qt smoke blocks
tests/test_startup.py                   README/START_HERE link checks
README.md                               status, layout, tests, docs/prompt links
docs/prompts/PROMPT_stage8_7_breath.md  the prompt format to match
```

Run Python from `ji-live/`. The environment has a uv-managed Python at
`~/.ji-tools/uv` even when `uv` is not on `PATH`:

```bash
~/.ji-tools/uv run --with python-osc python tests/test_fretboard.py
```

Qt may be unavailable (`libEGL.so.1`); report that honestly rather than
claiming a visual pass.

---

## 2. What must not change

- **No bridge, OSC, sclang or audio changes.** The bridge already broadcasts the
  live set on `/ji/freqset/changed`; the aid only consumes it. Do not add an
  address, a status message, a `control/` change, a state key or a SynthDef.
- **The performance state is untouched.** The guitar tuning is a UI aid, not
  part of the saved rig. It resets to Standard E when the GUI restarts. Say so
  in the docs.
- **No new config file** and no changes to `config/`.
- **One window.** No tabs, no separate top-level window, no dialogs. The
  Fretboard is one more group in `MainWindow`.
- **The compact 8 pt GUI and the scroll-area net stay.** Status remains in the
  layout. The default window grows for the board, but the scroll area is still
  the small-screen fallback.
- **Read-only aid.** Tuning edits and name toggles must never send OSC. The
  controller is not called from the Fretboard group.
- **Existing harnesses pass unmodified** except `tests/test_ui.py` (Qt group
  and height assertions) and the new `tests/test_fretboard.py`. In particular
  `test_mutation.py` is untouched.
- `/ji/freqset/changed` stays `[fundamental, anchor, ratio_1 … ratio_N]`; the
  aid maps the ratios itself.

---

## 3. Scope

### 3.1 In scope

1. **`ui/fretboard.py`** (new, pure, no PyQt6): note maths, 12-TET derivation,
   scale-degree dedupe, guitar tuning type and presets, fretboard-cell builder.
2. **`ui/ji_ui.py`**: one **Fretboard** group with a custom-painted
   `FretboardWidget`, string/fret/tuning controls, a set summary and a degree
   legend; live sync from `FREQSET_CHANGED`.
3. **`tests/test_fretboard.py`** (new, pure) and the Qt additions in
   `tests/test_ui.py`.
4. Docs and the prompt record.

### 3.2 Out of scope (say it in the stage README)

Audio analysis, pitch detection, MIDI output from the aid, sending notes to
sclang/MIDI, scale-degree solfège names, flat-spelling preference, capo,
left-handed mirror, fanned frets, custom fret counts above 24, more than 8
strings, chord diagrams, position/voicing suggestions, tuning persistence in a
performance state or `config/`, printing/export, or a second tuning to compare.

---

## 4. The 12-TET derivation (this exact maths)

### 4.1 Pitch classes, not registers

The JI set arrives as octave-reduced ratios in `[1, 2)` above a fundamental.
The aid is a **pitch-class** aid: a note is marked at every octave on the
fretboard; it does not suggest a register or a voicing. The legend shows the
set's one-octave frequencies.

### 4.2 Hz → MIDI (floating point)

```python
A4_MIDI = 69
A4_HZ = 440.0

def hz_to_midi_float(hz: float) -> float:
    return A4_MIDI + 12.0 * math.log2(hz / A4_HZ)

def midi_to_hz(midi: float) -> float:
    return A4_HZ * 2.0 ** ((midi - A4_MIDI) / 12.0)
```

### 4.3 Nearest 12-TET note and cents

Round **half-up**, never Python's banker's `round`:

```python
def nearest_12tet(midi_float: float) -> tuple[int, float]:
    nearest = int(math.floor(midi_float + 0.5))
    cents = (midi_float - nearest) * 100.0
    return nearest, cents
```

- `nearest` is the closest MIDI integer; `cents` is signed: **negative means
  the JI frequency is flat** relative to the 12-TET note, positive sharp.
- `nearest_12tet(57.5) == (58, -50.0)`; `nearest_12tet(58.5) == (59, -50.0)`.
- `A4 = 440 Hz` → `(69, 0.0)`; `C4 = 261.625565` → `(60, ~0.0)`.

`midi_note_name(midi)` uses **C4 = MIDI 60** and sharps only:

```python
NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")

def midi_note_name(midi: int) -> str:
    return f"{NOTE_NAMES[midi % 12]}{midi // 12 - 1}"
```

Examples: 40 → `E2`, 45 → `A2`, 50 → `D3`, 55 → `G3`, 59 → `B3`, 64 → `E4`,
60 → `C4`, 0 → `C-1`, 127 → `G9`.

### 4.4 The set → scale degrees

For each ratio `r` in `FrequencySet.ratios`:

```python
hz = fundamental * r
midi_float = hz_to_midi_float(hz)
nearest, cents = nearest_12tet(midi_float)
pitch_class = nearest % 12
```

Group by `pitch_class`; a pitch class can be reached by more than one ratio
(e.g. `9/8` and `10/9` both reach B). Deduplicate:

- one `ScaleDegree` per 12-TET pitch class;
- `sources` lists every distinct ratio (dedupe ratios within `1e-12`) that maps
  there, each with its own cents, **sorted by `abs(cents)` ascending**, then by
  ratio;
- `best_source` = `sources[0]`; `degree.cents` = `best_source.cents`;
- `degree.name` = `NOTE_NAMES[pitch_class]`;
- sort the degrees in **scale order from the root**:
  `(pitch_class - root_pc) % 12` ascending.

`root_pc = nearest_12tet(hz_to_midi_float(fundamental))[0] % 12`.
`anchor_pc = ratio_pitch_class(fundamental, freqset.anchor)` when the anchor is
not `None`; otherwise `None`.

The result has at most 12 degrees and may have fewer (duplicates + notes that
round together). A ratio near `2.0` rounds to the next octave and therefore
groups with the root pitch class — dedupe handles it.

### 4.5 Worked example (put this in the stage README)

Fundamental `A = 220.00 Hz`, set
`[1/1, 9/8, 5/4, 4/3, 3/2, 5/3, 15/8]`:

| Ratio | Hz | MIDI float | Nearest | 12-TET | Cents |
|---|---|---|---|---|---|
| 1/1 | 220.00 | 57.000 | 57 | A | +0 |
| 9/8 | 247.50 | 59.039 | 59 | B | +4 |
| 5/4 | 275.00 | 60.863 | 61 | C# | -14 |
| 4/3 | 293.33 | 61.980 | 62 | D | -2 |
| 3/2 | 330.00 | 64.020 | 64 | E | +2 |
| 5/3 | 366.67 | 65.844 | 66 | F# | -16 |
| 15/8 | 412.50 | 67.883 | 68 | G# | -12 |

The 12-TET set is **A B C# D E F# G#** (A major). Standard tuning open strings:
`E2 A2 D3 G3 B3 E4` → E, A, D, G, B, E; every one is in the set **except the
open G** (G natural; G# is at fret 1). That is the regression example for the
Qt test.

---

## 5. `ui/fretboard.py` (new, pure)

No PyQt6, no sockets, no `osc_control` import; standard library only. It may
be imported by `ui/ji_ui.py` and by `tests/test_fretboard.py`.

### 5.1 Constants

```python
NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
A4_MIDI = 69
A4_HZ = 440.0

MIN_STRINGS = 6
MAX_STRINGS = 8
DEFAULT_STRINGS = (40, 45, 50, 55, 59, 64)   # E2 A2 D3 G3 B3 E4

MIN_FRETS = 0
MAX_FRETS = 24
DEFAULT_FRETS = 24
```

### 5.2 Note maths

```python
def hz_to_midi_float(hz: object) -> float
def midi_to_hz(midi: object) -> float
def nearest_12tet(midi_float: object) -> tuple[int, float]
def midi_note_name(midi: object) -> str
def note_name(pitch_class: object) -> str          # validated 0..11
def ratio_pitch_class(fundamental: object, ratio: object) -> int
```

`hz_to_midi_float` / `ratio_pitch_class` reject non-finite or non-positive
values with `ValueError`; `midi_note_name` accepts any integer (a fret above
MIDI 127 still has a note name).

### 5.3 Data classes

```python
@dataclass(frozen=True, slots=True)
class ScaleSource:
    ratio: float
    cents: float

@dataclass(frozen=True, slots=True)
class ScaleDegree:
    pitch_class: int
    sources: tuple[ScaleSource, ...]

    @property
    def name(self) -> str: ...            # NOTE_NAMES[pitch_class]
    @property
    def best_source(self) -> ScaleSource: # sources[0]
    @property
    def cents(self) -> float: ...         # best_source.cents
```

```python
def scale_degrees(fundamental: object, ratios: object) -> tuple[ScaleDegree, ...]
def root_pitch_class(fundamental: object) -> int
def scale_summary(degrees: Sequence[ScaleDegree]) -> str   # "A B C# D E F# G#"
```

`scale_degrees` validates the fundamental and every ratio; an empty ratio
sequence returns `()` (the UI shows “waiting for the set”).

### 5.4 Guitar tuning

```python
@dataclass(frozen=True, slots=True)
class GuitarTuning:
    strings: tuple[int, ...] = DEFAULT_STRINGS

    @classmethod
    def create(cls, strings: object = DEFAULT_STRINGS) -> "GuitarTuning": ...
    @property
    def string_count(self) -> int: ...
    @property
    def labels(self) -> tuple[str, ...]: ...   # ("E2", "A2", ...)
    @property
    def preset(self) -> str: ...               # matching preset or "Custom"
    def with_string(self, index: object, midi: object) -> "GuitarTuning": ...
    def with_count(self, count: object) -> "GuitarTuning": ...
```

- `strings` is **low string → high string**; each is an int 0..127. Reject
  strings/bytes, bools, non-integers, lengths outside 6..8, and notes outside
  0..127 with `TypeError`/`ValueError`.
- `with_string(index, midi)` validates both and returns a new value.
- `with_count(count)` accepts 6..8:
  - increasing keeps the current highest string and **prepends** a string 5
    semitones below the current lowest (a low fourth), clamped at MIDI 0;
  - decreasing **drops the lowest** strings;
  - returns `self` when the count is unchanged.
- `preset` compares the tuple exactly with `TUNING_PRESETS`; otherwise
  `"Custom"`.

### 5.5 Presets (exact MIDI numbers, low → high)

```python
TUNING_PRESETS: tuple[tuple[str, tuple[int, ...]], ...] = (
    ("Standard E",    (40, 45, 50, 55, 59, 64)),
    ("Drop D",        (38, 45, 50, 55, 59, 64)),
    ("Drop C",        (36, 43, 48, 53, 57, 62)),
    ("DADGAD",        (38, 45, 50, 55, 57, 62)),
    ("Open G",        (38, 43, 50, 55, 59, 62)),
    ("Open D",        (38, 45, 50, 54, 57, 62)),
    ("Half-step down",(39, 44, 49, 54, 58, 63)),
    ("7-string B",    (35, 40, 45, 50, 55, 59, 64)),
    ("8-string F#",   (30, 35, 40, 45, 50, 55, 59, 64)),
)
TUNING_PRESET_MAP: dict[str, tuple[int, ...]] = dict(TUNING_PRESETS)
DEFAULT_TUNING = GuitarTuning.create(DEFAULT_STRINGS)
```

Labels: `D2 A2 D3 G3 B3 E4` for Drop D, and so on. Keep the table in this exact
order; the combo uses it.

### 5.6 Fretboard cells

```python
@dataclass(frozen=True, slots=True)
class FretboardCell:
    string: int                 # index into tuning.strings; 0 = lowest
    fret: int                   # 0 = open string
    midi: int                   # open + fret (may exceed 127 high up)
    pitch_class: int            # midi % 12
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
) -> tuple[FretboardCell, ...]
```

- Validate `frets` 0..24.
- Map `pitch_class → degree`; iterate `for string in range(len(strings))` then
  `for fret in range(frets + 1)`, string-major order. Total
  `strings × (frets + 1)` cells.
- `is_root` only for a marked cell whose pitch class is `root_pc`;
  `is_anchor` only when `anchor_pc` is supplied and matches.
- The widget draws string index 0 at the **bottom** (low E at the bottom,
  standard fretboard-diagram orientation).

### 5.7 Pure-module tests to add (`tests/test_fretboard.py`)

Cover at least:

- `hz_to_midi_float(440) == 69.0`; `midi_to_hz(69) == 440.0`;
  `midi_note_name(60) == "C4"`, `40/45/50/55/59/64` → `E2/A2/D3/G3/B3/E4`,
  `127 → G9`.
- Half-up rounding: `nearest_12tet(57.5) == (58, -50.0)`,
  `nearest_12tet(58.5) == (59, -50.0)`.
- The worked A-major set: names `A B C# D E F# G#`; cents within `0.01` of
  `0, +3.91, -13.69, -1.96, +1.96, -15.64, -11.73`; root pc 9.
- `ratio_pitch_class(220, 3/2) == 4`.
- Duplicates: `scale_degrees(220, [9/8, 10/9])` is one B degree with two
  sources, best source `9/8`.
- A `2.0` ratio dedupes with `1.0` at the root; a 432 Hz fundamental still
  produces the same note names (cents shift, nearest notes do not).
- Empty ratios → `()`; non-positive/NaN values raise.
- Tuning validation, labels, presets, `with_string`, `with_count` (6→7→8 adds
  B1/F#1; 8→6 drops them; 6→5 raises), custom preset = `"Custom"`.
- `build_fretboard(DEFAULT_TUNING, degrees, frets=24, root_pc=9)`:
  150 cells (6 strings × 25 frets); open G (string 3, fret 0) is unmarked; G# at string 3 fret 1 is
  marked; open A (string 1) is the root; total count matches.
- `scale_summary(...) == "A B C# D E F# G#"`.

---

## 6. GUI (`ui/ji_ui.py`)

### 6.1 New group

Add `Fretboard` as a full-width group **between Lead and Status**:

```python
outer.addWidget(self._build_fretboard_group())
```

Add “Fretboard” to the `MainWindow` docstring’s group list and to the Qt test’s
expected group titles. The group is visible by default.

### 6.2 Controls (one row above the diagram)

```text
Strings [6]  Frets [24]  Tuning [Standard E v]  [E2][A2][D3][G3][B3][E4]  Names [x]
```

| Widget | Behaviour |
|---|---|
| `fretboard_strings_spin` | QSpinBox 6..8, default 6. Calls `tuning.with_count`; no OSC |
| `fretboard_frets_spin` | QSpinBox 0..24, default 24. Redraws |
| `fretboard_tuning_combo` | `QComboBox` with the preset labels + `Custom`; selecting a preset replaces the whole tuning |
| `fretboard_string_combos` | Eight `QComboBox`es, one per possible string, populated with all 128 MIDI notes as `"E2"`-style labels with `midi` as the item data. Show only `string_count`; low→high order. Editing one calls `with_string` and moves the preset combo to `Custom` |
| `fretboard_names_checkbox` | on/off, default on: note names inside the markers |
| `fretboard_set_label` | read-only: `12-TET set (A = 220.00 Hz): A B C# D E F# G#` |
| `fretboard_legend_label` | read-only, word-wrapped: `A 1/1 +0¢ · B 9/8 +4¢ · C# 5/4 -14¢ · …` (use the existing `osc_control.ji_ratio_label` for the fraction, or its decimal fallback) |
| `fretboard_widget` | the custom `FretboardWidget` |

Populate the tuning combos with a `_fretboard_sync` guard (or
`blockSignals`) so programmatic syncs never emit changes. Keep all eight combos
alive; hide the unused ones. The preset combo is display-only when `Custom`.

### 6.3 `FretboardWidget` (in `ui/ji_ui.py`)

A `QWidget` painted with `QPainter` (no images, no external assets):

- `set_board(tuning, degrees, *, root_pc, anchor_pc, frets, show_names)` stores
  a `build_fretboard(...)` result and calls `update()`.
- Expose read-only `cells`, `degrees`, `frets`, `show_names` properties for the
  Qt test.
- `paintEvent`:
  - antialiasing; board background and ink from `self.palette()`;
  - margins `left=64` for string labels, `right=12`, `top=20`, `bottom=8`;
  - open column ~28 px; fret columns equal width; nut is a thick line;
  - string rows low at the bottom, high at the top; string labels `E2`… left;
  - fret numbers 1..N above (skip if the column is too narrow);
  - inlays at 3,5,7,9,12,15,17,19,21,24 (double at 12 and 24);
  - one marker per marked cell: filled circle; **root** in the accent colour,
    **anchor** gets an extra ring, other scale notes in the secondary colour;
    a light outline for contrast; the note name inside when `show_names` and
    the radius allows (skip small text rather than clip);
  - empty degree list: draw the bare board and labels only;
  - `setMouseTracking(True)`; `mouseMoveEvent` shows a `QToolTip` for the marked
    cell under the cursor: `E2 fret 5 · A · 1/1 · +0¢ · 220.00 Hz` (and the
    degree's other sources if any).
- `minimumSizeHint()`/`sizeHint()`: width ≥ 900, height
  `28 + 18 * string_count`; `QSizePolicy` expanding horizontally and fixed
  vertically. Repaint is O(cells) (≤ 200), no timers.
- High-DPI: use logical coordinates and `QFontMetrics`; no pixmap caching.

Colours: use palette colours for the board/ink and two fixed accents that stay
readable in light and dark (e.g. root `#d9822b`, scale `#3d7ea6`, anchor ring
`#8e44ad`). Keep the rest of the window’s 8 pt compact style.

### 6.4 Live sync (no new OSC)

- In `_on_status_message`, add a branch for `osc_control.FREQSET_CHANGED` that
  calls `self._sync_fretboard()` (there is no such branch today).
- `__init__` calls `_sync_fretboard()` and `_sync_fretboard_controls()` once
  after the groups are built.
- `_sync_fretboard()`:
  - if `self.store.freqset is None`: label `Fretboard: waiting for the set`,
    empty legend, empty degrees, bare board;
  - else compute `fretboard.scale_degrees(...)`, `root_pitch_class`,
    `ratio_pitch_class` for `freqset.anchor` when not `None`, update both labels
    and `self.fretboard_widget.set_board(...)`.
- Tuning handlers update `self._fretboard_tuning`, sync the controls, and call
  `self.fretboard_widget.set_board(...)`; they never call
  `self.controller.*`.
- A malformed set is already rejected by `store.apply`; the aid keeps the last
  good diagram and shows the normal “ignored malformed” message.

### 6.5 Window

The group adds roughly 200 px. Set `self.resize(1280, 1172)` (measure and adjust
if the actual offscreen height differs), update the three
`window.height() <= 990` assertions in `tests/test_ui.py` to `<= 1205`, and
update their comments. Status must remain in the layout; the scroll area stays
the net.

### 6.6 Imports

Add a package-direct `fretboard` import independent of the big `osc_control`
block:

```python
if __package__:
    from . import fretboard
else:  # direct ``python ui/ji_ui.py``
    import fretboard
```

Add the needed Qt names (`QPainter`, `QPen`, `QBrush`, `QColor`, `QFontMetrics`,
`QRectF`, `QSize`, `QSizePolicy`, `QPalette`, `QToolTip`) to the existing
`PyQt6.QtWidgets`/`QtGui`/`QtCore` imports. Do not import PyQt6 from
`ui/fretboard.py`.

---

## 7. Docs (required)

- **NEW `docs/README_stage8_8.md`**: the feature, the exact maths, the worked
  A-major table, the tuning presets, the GUI, the tests, risks, the human
  checklist, out-of-scope. Include an ASCII sketch of the fretboard and state
  that the tuning is session-only and read-only (no OSC).
- `docs/START_HERE.md`: a “Fretboard (improvisation aid)” section — no command,
  no flag; the controls, the tuning presets, and troubleshooting rows:
  *the board is empty* (waiting for the set / bridge off), *a note is marked
  that sounds wrong* (it is the nearest 12-TET, read the cents), *the open G is
  missing in A major* (G natural vs G#), *my tuning resets* (session-only by
  design).
- `docs/GUI_PARAMETERS.md`: the Fretboard rows (Strings/Frets/Tuning/string
  combos/Names/set label/legend), one line each.
- `docs/design_doc_10.28.10.50`:
  - new **§6.22 Fretboard improvisation aid (Stage 8.8)** with the maths, the
    pitch-class rule, the presets and the read-only/state decision;
  - §7.1: state explicitly that this stage adds **no** OSC address (the aid
    consumes the existing `/ji/freqset/changed`);
  - §8.9: add the **8.8** row for this feature. The table currently reserves
    8.8 for the optional MIDI-triggered mutation/VST work: **renumber that
    optional row to 8.9** and update any references;
  - §9: a `8.8` status row `🟡 Built <date> — awaiting human acceptance`;
  - §13: out-of-scope additions (capo, left-handed, chord diagrams, tuning
    persistence, audio analysis);
  - §14: a `8.8.0` changelog row.
- Root `README.md`: the status paragraph, the layout tree (`ui/fretboard.py`,
  `tests/test_fretboard.py`, `docs/README_stage8_8.md`), the test list
  (`tests/test_fretboard.py`), the docs links and the prompt link.
- Keep `docs/prompts/PROMPT_stage8_8_fretboard.md` (this file) as the stage
  record.

---

## 8. Tests

### 8.1 New pure harness — `tests/test_fretboard.py`

Plain `assert` style, no PyQt6/python-osc needed (the module is stdlib-only):

```bash
~/.ji-tools/uv run python tests/test_fretboard.py
```

Sections: note maths, half-up rounding, A-major degrees/cents, duplicates,
root/anchor pcs, 432 Hz fundamental, empty/invalid sets, tuning validation and
presets, `with_count` up/down, fretboard cell count/positions/root flag,
`scale_summary`. Print section lines and `ALL FRETBOARD TESTS PASSED`.

### 8.2 `tests/test_ui.py` (Qt only; pure tests are unchanged)

Add to the three Qt smoke blocks / the first one as appropriate:

- `"Fretboard"` in the expected `QGroupBox` titles.
- Defaults: tuning `Standard E`, strings 6, frets 24, combos
  `E2 A2 D3 G3 B3 E4`, Names checked.
- Feed `/ji/freqset/changed [220, -1, 1, 9/8, 5/4, 4/3, 3/2, 5/3, 15/8]`:
  set label contains `A B C# D E F# G#`; legend contains `5/4` and `-14`-ish;
  widget degrees names match; open G cell unmarked; open A is root; G# fret 1
  marked.
- Drop D preset: combo data `[38, 45, 50, 55, 59, 64]`; no controller calls.
- Custom string edit: preset becomes `Custom`; tuning string changes.
- Strings 7: seven combos visible, lowest string `B1` (35) with Standard E
  extended; widget cell count changes.
- Frets 12: widget frets 12 and cell count `strings × 13`.
- Names off: widget `show_names is False`; no OSC.
- Height bound ≤ 1205; no `FakeController.calls` from any fretboard control.

Because the container may lack `libEGL.so.1`, `--qt` may skip; report that. The
pure harness must pass either way. Do not weaken the Qt test to hide a real
failure.

### 8.3 Regression

Run from `ji-live/` (all except Qt are required):

```bash
~/.ji-tools/uv run python tests/test_fretboard.py
~/.ji-tools/uv run --with python-osc python tests/test_rhythm.py
~/.ji-tools/uv run --with python-osc python tests/test_breath.py
~/.ji-tools/uv run --with python-osc python tests/test_bass.py
~/.ji-tools/uv run --with python-osc python tests/test_state.py
~/.ji-tools/uv run --with python-osc python tests/test_arp.py
~/.ji-tools/uv run --with python-osc python tests/test_lead.py
~/.ji-tools/uv run --with python-osc python tests/test_drone.py
~/.ji-tools/uv run --with python-osc python tests/test_groove.py
~/.ji-tools/uv run --with python-osc python tests/test_quantize.py
~/.ji-tools/uv run --with python-osc python tests/test_home.py
~/.ji-tools/uv run --with python-osc python tests/test_glide.py
~/.ji-tools/uv run --with python-osc python tests/test_startup.py
~/.ji-tools/uv run --with python-osc python tests/test_mutation.py
~/.ji-tools/uv run --with python-osc python tests/test_mutation_controls.py
~/.ji-tools/uv run --with python-osc python tests/test_ui.py --pure
~/.ji-tools/uv run python tests/test_mpe.py --pure
# if PyQt6 can run:
QT_QPA_PLATFORM=offscreen ~/.ji-tools/uv run --with python-osc --with PyQt6 \
    python tests/test_ui.py --qt
```

---

## 9. Human acceptance checklist (the owner is the authority)

1. With the bridge connected, the Fretboard group shows the current set’s
   nearest 12-TET names and the legend’s cents; it updates on every mutation
   without a restart.
2. The fretboard shows a full 24-fret, 6-string neck; every occurrence of every
   12-TET note in the set is marked; root and anchor are distinguishable.
3. With `A = 220 Hz` and the shipped 7-class set, the names read
   `A B C# D E F# G#`; the open G string is **not** marked, G# at fret 1 is;
   the open A is the root.
4. Drop D: the low string label changes to `D2`, fret 0 is marked, the rest of
   the board redraws; every preset behaves as its label says.
5. Custom tuning: edit any string to any MIDI note; labels/markers update; the
   tuning combo reads `Custom`; 7/8 strings show the extra low string.
6. Frets 24 → 12 changes the columns; names stay readable.
7. `Names` off hides the text but keeps the markers.
8. Editing tuning sends **no** OSC (watch the bridge log; it stays quiet).
9. The window is usable at the default size; Status is still reachable (scroll
   on a small screen); no painting glitches at resize.
10. Record the result. **8.8 is not complete until the owner confirms it is
    useful.**

---

## 10. Risks

- **The 12-TET set is a pitch-class aid, not a register map.** Do not mark only
  one octave, and do not suggest a voicing.
- **Half-up rounding and dedupe**: banker's rounding at a tie, or leaving two
  degrees on one pitch class, gives an inconsistent legend and board. Test
  `57.5`/`58.5` and the `9/8` vs `10/9` duplicate.
- **Cents sign**: negative = JI flat. State it in the legend/tooltip once.
- **MIDI octave naming**: C4 = 60; 40 is E2, not E3. The combo labels and string
  labels must agree.
- **Ratios near 2.0** round to the next octave; group them with the root.
- **Tuning reads/writes**: never send OSC from these controls; guard combo
  population with a sync flag so programmatic changes do not fire handlers.
- **Painting**: 24 frets × 8 strings is small, but text clipping and high-DPI
  scaling are the visual failure modes; skip labels rather than clip, and test
  resize if Qt is available.
- **Window height**: the group is ~200 px; update all three Qt bounds and the
  comments together, and keep Status in the layout.
- **No Qt in the agent container**: do not claim a visual pass; run the pure
  harness and say the Qt test was skipped.
- **State**: tuning is session-only. Do not add a state key; do not claim it is
  saved.

---

## 11. Deliverables

```text
ji-live/
├── ui/fretboard.py                    NEW: note maths, tuning, scale, cells
├── ui/ji_ui.py                        MOD: Fretboard group + painted widget
├── tests/test_fretboard.py            NEW
├── tests/test_ui.py                   MOD: Qt group/tuning/height checks
├── docs/README_stage8_8.md            NEW
├── docs/START_HERE.md                 MOD: controls + troubleshooting
├── docs/GUI_PARAMETERS.md             MOD: Fretboard rows
├── docs/design_doc_10.28.10.50        MOD: §6.22/§7.1/§8.9/§9/§13/§14
└── README.md                          MOD: status, layout, tests, links
```

No changes to `control/`, `config/`, `synthdefs/`, `tests/sc/`, any OSC address,
or any performance-state key.

---

## 12. Self-review requirement (final response)

Provide:

- Files added/changed.
- For each requirement, the code that satisfies it or a concrete concern.
- “If I had to bet on which line is most likely to fail on the owner’s machine,
  which is it, and why? Give a one-line verification command.”
- Assumptions, max 5 bullets.
- Explicit statement that audio, MIDI, sclang and the live GUI were not tested
  by the agent (unless Qt actually ran), and that 8.8 awaits human acceptance.
- The exact one-sclang + one-Python start sequence, copied from
  `docs/START_HERE.md` after your updates.

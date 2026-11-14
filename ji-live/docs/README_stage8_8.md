# Stage 8.8 — 12-TET fretboard improvisation aid

**Status:** built 2026-11-05, **awaiting the owner's acceptance**. The aid is a
read-only GUI panel: it shows the nearest 12-TET note to every ratio in the live
JI set (with signed cents), and draws every occurrence of those notes on a full
guitar fretboard whose tuning is fully editable. It adds **no** OSC address, no
audio/MIDI/sclang path, no performance-state key and no config file. Audio, MIDI,
sclang and the live GUI were **not** available in the agent environment (the Qt
wheel needs system libraries this container lacks), so nothing here is an
acceptance claim.

Baseline: 8.7.1 (breathing rhythm + bass hit row, `Arp sync`) is built and
awaiting the owner's listening check. 8.8 is additive: it only *consumes* the
existing `/ji/freqset/changed` message and paints.

---

## 1. What the owner asked for (2026-11-05)

> "ok the next feature i want implemented is an improvisation aid that displays
> the closest 12tet notes to the frequencies in the set (i have noticed guitar
> only sounds good if played close to these notes), and displays all of these
> 12tet notes on a guitar fretboard (a full fretboard scale diagram), with an
> ability to alter the guitars tuning (so i can use it in drop d for example,
> this should be fully flexible towards whatever tuning)."

Decoded:

1. **Nearest 12-TET set** — for every JI ratio, the closest 12-TET note and the
   signed cents between the JI frequency and that note.
2. **Full fretboard diagram** — 6 strings and 24 frets by default; every
   position whose note is one of those 12-TET notes is marked. The aid is a
   **pitch-class** map: a note is marked in every octave, not just one register.
3. **Fully flexible tuning** — every open string is any MIDI note 0–127, with
   presets including Drop D, and 7- and 8-string defaults.
4. **Live** — follows `/ji/freqset/changed` (mutation, state load, hello) with
   no restart.

---

## 2. The rule that must never break

**The aid is read-only.** No control in the Fretboard group calls the
controller, sends OSC, touches the bridge, sclang or the audio/MIDI rig. The
tuning is a **local, session-only UI setting**: it is not saved in a performance
state, not written to `config/`, and resets to Standard E when the GUI restarts.
The performance state is untouched.

The only input is the existing broadcast message:

```text
/ji/freqset/changed  [fundamental, anchor, ratio_1 … ratio_N]
```

The aid maps the ratios itself; the message shape is unchanged.

---

## 3. The exact maths (`ui/fretboard.py`, pure, no PyQt6)

### 3.1 Hz ↔ MIDI

```python
A4_MIDI = 69
A4_HZ = 440.0

def hz_to_midi_float(hz):   return A4_MIDI + 12.0 * math.log2(hz / A4_HZ)
def midi_to_hz(midi):       return A4_HZ * 2.0 ** ((midi - A4_MIDI) / 12.0)
```

### 3.2 Nearest note and cents — half-up rounding

Python's `round` is banker's (ties to even), so the aid uses `floor(x + 0.5)`:

```python
def nearest_12tet(midi_float):
    nearest = int(math.floor(midi_float + 0.5))
    cents = (midi_float - nearest) * 100.0
    return nearest, cents
```

* `nearest_12tet(57.5) == (58, -50.0)` and `nearest_12tet(58.5) == (59, -50.0)`.
* `cents` is **signed**: negative = the JI frequency is **flat** of the 12-TET
  note; positive = sharp.
* Note names are **sharps only** and use **C4 = MIDI 60**:
  `NOTE_NAMES = ("C", "C#", "D", …, "B")`, `midi_note_name(40) == "E2"`.

### 3.3 Set → pitch-class degrees

For each ratio `r`: `hz = fundamental * r`, `nearest, cents = nearest_12tet(...)`,
`pitch_class = nearest % 12`. Group by pitch class and deduplicate:

* one `ScaleDegree` per 12-TET pitch class;
* `sources` lists every distinct ratio (deduped within `1e-12`) reaching it,
  each with its own cents, **sorted by `abs(cents)` then ratio**;
* `best_source = sources[0]`, `degree.cents = best_source.cents`;
* degrees are sorted in **scale order from the root**:
  `(pitch_class - root_pc) % 12`, where
  `root_pc = nearest_12tet(hz_to_midi_float(fundamental))[0] % 12`;
* `anchor_pc = ratio_pitch_class(fundamental, anchor)` when the anchor is not
  `None`.

A ratio near `2.0` rounds into the next octave and therefore groups with the
root; the dedupe handles it. Two different ratios can reach one pitch class
(e.g. `9/8` and `10/9` both reach B); the best (closest) one is the legend's
headline and the others are shown in parentheses.

### 3.4 Worked example — A = 220.00 Hz, A-major set

Set `[1/1, 9/8, 5/4, 4/3, 3/2, 5/3, 15/8]`:

| Ratio | Hz | MIDI float | Nearest | 12-TET | Cents |
|---|---|---|---|---|---|
| 1/1 | 220.00 | 57.000 | 57 | A | +0 |
| 9/8 | 247.50 | 59.039 | 59 | B | +4 |
| 5/4 | 275.00 | 60.863 | 61 | C# | -14 |
| 4/3 | 293.33 | 61.980 | 62 | D | -2 |
| 3/2 | 330.00 | 64.020 | 64 | E | +2 |
| 5/3 | 366.67 | 65.844 | 66 | F# | -16 |
| 15/8 | 412.50 | 67.883 | 68 | G# | -12 |

The 12-TET set is **A B C# D E F# G#** (A major), root pitch class 9.

With Standard tuning `E2 A2 D3 G3 B3 E4`:

* open `E2`, `A2`, `D3`, `B3`, `E4` are in the set (the open A is the root);
* the **open G is not** — G natural is not in A major; **G# at fret 1** is.
  That is the regression example used by the Qt test.

### 3.5 432 Hz and other fundamentals

The note names do not change with the reference frequency; only the cents do.
`A = 432 Hz` still yields `A B C# D E F# G#`, with A about −31.77¢ from the
440-based 12-TET grid.

---

## 4. Guitar tuning

`GuitarTuning` is a frozen value object; `strings` is **low → high**, each an int
0..127.

| Preset | MIDI, low → high | Labels |
|---|---|---|
| Standard E | 40, 45, 50, 55, 59, 64 | E2 A2 D3 G3 B3 E4 |
| Drop D | 38, 45, 50, 55, 59, 64 | D2 A2 D3 G3 B3 E4 |
| Drop C | 36, 43, 48, 53, 57, 62 | C2 G2 C3 F3 A3 D4 |
| DADGAD | 38, 45, 50, 55, 57, 62 | D2 A2 D3 G3 A3 D4 |
| Open G | 38, 43, 50, 55, 59, 62 | D2 G2 D3 G3 B3 D4 |
| Open D | 38, 45, 50, 54, 57, 62 | D2 A2 D3 F#3 A3 D4 |
| Half-step down | 39, 44, 49, 54, 58, 63 | D#2 G#2 C#3 F#3 A#3 D#4 |
| 7-string B | 35, 40, 45, 50, 55, 59, 64 | B1 E2 A2 D3 G3 B3 E4 |
| 8-string F# | 30, 35, 40, 45, 50, 55, 59, 64 | F#1 B1 E2 A2 D3 G3 B3 E4 |

* `with_string(index, midi)` validates and returns a new tuning.
* `with_count(n)` accepts 6..8. Growing keeps the highest string and
  **prepends a low fourth** (5 semitones below the current lowest, clamped at
  MIDI 0), so Standard E → 7-string adds B1 (35) and → 8-string adds F#1 (30).
  Shrinking drops the lowest strings. `6 → 5` and `8 → 9` are rejected.
* `preset` compares the tuple exactly with the table, otherwise `"Custom"`.

The tuning is **session-only**: restarting the GUI returns to Standard E. That
is deliberate (the aid is not part of the rig).

---

## 5. The fretboard drawing

`build_fretboard(tuning, degrees, *, frets=24, root_pc=None, anchor_pc=None)`
returns `strings × (frets + 1)` cells in string-major order (string 0 = lowest):

```python
FretboardCell(string, fret, midi, pitch_class, degree, is_root, is_anchor)
```

`degree is None` means the position is not in the 12-TET set. `is_root` and
`is_anchor` are true only on a marked cell whose pitch class matches the
optional root/anchor pitch classes.

`FretboardWidget` paints the neck with `QPainter` (no images, no assets):

* string 0 (low E) at the **bottom**, high string at the top;
* an open-string column (~28 px), equal-width fret columns and a thick nut;
* string labels at the left (`E2`, …), fret numbers above (skipped when the
  column is too narrow);
* inlay dots at 3, 5, 7, 9, 12, 15, 17, 19, 21 and 24 (double at 12 and 24);
* one filled circle per marked cell: **root** `#d9822b`, other scale notes
  `#3d7ea6`, an extra `#8e44ad` ring for the **anchor**; the note name inside
  when `Names` is on and the marker is large enough (text is skipped, never
  clipped);
* tooltips (e.g. `E2 fret 5 · A · 1/1 · +0¢ · 220.00 Hz`, plus other sources);
* no timer: a repaint is O(cells) (≤ 200); high-DPI uses logical coordinates.

ASCII sketch (`A = 220`, A major, Standard E, 6 strings; `o` = marked,
`O` = root, `.` = unmarked, `|` = nut; 24 frets shortened for the page):

```text
       open   1   2   3   4   5   6   7   8   9  10  11  12
 E4   o   .   o   .   o   O   .   o   .   o   o   .   o
 B3   o   .   o   o   .   o   .   o   .   o   O   .   o
 G3   .   o   O   .   o   .   o   o   .   o   .   o   .
 D3   o   .   o   .   o   .   o   O   .   o   .   o   o
 A2   O   .   o   .   o   o   .   o   .   o   .   o   O
 E2   o   .   o   .   o   O   .   o   .   o   o   .   o
       ----|----|----|----|----|----|----|----|----|----|----|----
       nut   1    2    3    4    5    6    7    8    9   10   11
```

(The sketch is illustrative; the widget draws every fret up to the `Frets`
value and every marked octave, not one voicing.)

---

## 6. GUI (one more group in the single window)

The **Fretboard** group sits full-width **between Lead and Status** and is
visible by default. One control row above the diagram:

```text
Strings [6]  Frets [24]  Tuning [Standard E v]  [E2][A2][D3][G3][B3][E4]  Names [x]
```

| Widget | Behaviour |
|---|---|
| `Strings` spin 6..8 | `with_count`; adds a low fourth or drops the lowest strings |
| `Frets` spin 0..24 | redraws the columns (0 = open strings only) |
| `Tuning` combo | the nine presets + `Custom`; a preset replaces the whole neck. `Custom` is display-only |
| eight string combos | all 128 MIDI notes as `E2`-style labels with the number as item data; only `string_count` are shown, low → high. Editing one switches the preset label to `Custom` |
| `Names` checkbox | note names inside the markers (markers stay when off) |
| set label | `12-TET set (A = 220.00 Hz): A B C# D E F# G#`, or `Fretboard: waiting for the set` |
| legend label | word-wrapped `A 1/1 +0¢ · B 9/8 +4¢ · C# 5/4 -14¢ · …`; duplicate sources in parentheses |
| board | the painted `FretboardWidget` |

Every combo/spin is populated signal-blocked. No handler calls
`self.controller.*`; the Qt test asserts `FakeController.calls` stays empty.

The default window grows from 1280×966 to **1280×1172** (the Qt bound is
≤ 1205). The scroll area is still the small-screen fallback and Status remains
in the layout.

---

## 7. Live sync

* `__init__` builds the group, syncs the tuning controls and calls
  `_sync_fretboard()` once.
* `_on_status_message` gains one branch: `FREQSET_CHANGED` →
  `_sync_fretboard()`.
* `_sync_fretboard()` recomputes `scale_degrees`, `root_pitch_class` and the
  anchor pitch class, updates the set/legend labels and redraws the board.
  With no set yet it shows `Fretboard: waiting for the set`, an empty legend and
  a bare neck.
* Tuning handlers update `self._fretboard_tuning`, re-sync the widgets and call
  `FretboardWidget.set_board(...)`; they never send OSC.
* A malformed set is still rejected by `StatusStore.apply`; the aid keeps the
  last good diagram and the normal `Ignored malformed …` message.

---

## 8. Files

```text
ji-live/
├── ui/fretboard.py                 NEW: note maths, scale degrees, tuning, cells
├── ui/ji_ui.py                     MOD: Fretboard group + FretboardWidget
├── tests/test_fretboard.py         NEW: pure harness
├── tests/test_ui.py                MOD: Qt group/tuning/height checks
├── docs/README_stage8_8.md         NEW (this file)
├── docs/START_HERE.md              MOD: controls + troubleshooting
├── docs/GUI_PARAMETERS.md          MOD: Fretboard rows
├── docs/design_doc_10.28.10.50     MOD: §6.22 / §7.1 / §8.9 / §9 / §13 / §14
└── README.md                       MOD: status, layout, tests, links
```

No changes to `control/`, `config/`, `synthdefs/`, `tests/sc/`, any OSC address,
or any performance-state key.

---

## 9. Risks

* **Pitch-class, not register.** Mark every octave; do not suggest a voicing.
* **Half-up rounding and dedupe.** Banker's rounding at a tie, or two degrees on
  one pitch class, gives an inconsistent legend and board. Tests pin `57.5`,
  `58.5` and the `9/8` vs `10/9` duplicate.
* **Cents sign.** Negative = JI flat. The legend and tooltips use the signed
  value.
* **MIDI octave naming.** C4 = 60; 40 is E2, not E3. String labels and combo
  labels use the same helper.
* **Ratios near 2.0** round into the next octave; they group with the root.
* **No OSC from tuning.** The handlers are deliberately controller-free and the
  combos are signal-blocked while syncing.
* **Painting.** Text clipping and high-DPI scaling are the visual failure modes;
  labels are skipped rather than clipped. A 7/8-string board is taller
  (`28 + 18 × strings`).
* **Window height.** The group is ~200 px; all three Qt bounds are 1205 and
  Status stays in the layout.
* **No Qt in the agent container.** The Qt smoke test skips (`libEGL.so.1` and
  other system libs are missing). The pure harness passes; no visual pass is
  claimed.
* **State.** Tuning is session-only; do not add a state key or claim it is
  saved.

---

## 10. Tests

Pure (stdlib only, run from `ji-live/`):

```bash
~/.ji-tools/uv run python tests/test_fretboard.py
```

Covers note maths and naming, half-up rounding, the worked A-major degrees and
cents, duplicates (`9/8`/`10/9`, `1/1`/`2/1`, repeated ratios), root/anchor pitch
classes, a 432 Hz fundamental, empty/invalid sets, tuning validation and
presets, `with_string`, `with_count` up/down and the MIDI-0 clamp, the 150-cell
board and its positions/root/anchor flags, and `scale_summary`.

Qt (optional, needs PyQt6 and its system libraries):

```bash
QT_QPA_PLATFORM=offscreen uv run --with python-osc --with PyQt6 \
  python tests/test_ui.py --qt
```

The Qt test adds `Fretboard` to the expected group titles, the Standard E
defaults, the fed A-major set, open-G/G#/root checks, Drop D, a Custom edit,
7 strings, 12 frets, `Names` off, the 1205 height bound, and an empty
`FakeController.calls`.

Regression: every harness in the stage prompt still passes; `test_mutation.py`
is untouched.

---

## 11. Human acceptance checklist (the owner is the authority)

1. With the bridge connected, the Fretboard group shows the current set's
   nearest 12-TET names and the legend's cents; it updates on every mutation
   without a restart.
2. The board shows a full 24-fret, 6-string neck; every occurrence of every
   12-TET note in the set is marked; root and anchor are distinguishable.
3. With `A = 220 Hz` and the shipped 7-class set, the names read
   `A B C# D E F# G#`; the open G string is **not** marked, G# at fret 1 is; the
   open A is the root.
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

## 12. Out of scope (deliberate)

Audio analysis, pitch detection, MIDI output from the aid, sending notes to
sclang/MIDI, scale-degree solfège names, a flat-spelling preference, capo,
left-handed mirror, fanned frets, custom fret counts above 24, more than 8
strings, chord diagrams, position/voicing suggestions, tuning persistence in a
performance state or `config/`, printing/export, or a second tuning to compare.

---

## 13. Changelog

| Ver | Date | Change |
|---|---|---|
| 8.8.0 | 2026-11-05 | 12-TET fretboard improvisation aid: nearest-note/cents maths, deduplicated scale degrees, nine guitar presets with fully editable 6–8-string tuning, a painted full fretboard, and live `/ji/freqset/changed` sync. Read-only, session-only tuning, no OSC/state/audio change. Built, awaiting the owner's acceptance. |

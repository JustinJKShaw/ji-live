# Stage 9.1 — Lead MIDI input, recording, quantisation and saved melodies

Status: **built 2026-11-14 — awaiting the owner's human acceptance.**
Stage 9.1 makes the lead playable: a MIDI controller or the QWERTY keyboard
plays notes that sound in the **current JI pitch-class set**, the played line is
recorded and quantised to the beat/groove, saved as a named melody, and then
kept transforming through the existing lead Variation system.

This increment is a **lead/performance** addition, not a granular/glitch FX
one (Stage 9's original theme); it is listed as such in the design doc's §8.9
and §8.10 tables.

The owner's decisions (2026-11-14) are settled and implemented without extra
options:

| # | Decision | Implementation |
|---|---|---|
| 1 | Nearest frequency class to the MIDI input | `LeadInputRig.map_note`; no other mapping mode |
| 2 | Live monitoring with the transport stopped | `source` = `live` keeps monitoring; recording still needs Play |
| 3 | Sequencer paused while input is incoming | `LeadRig.set_source` stops (never mutes) the scheduler in `live`/`record` |
| 4 | Destructive variations + Revert | `LeadMotif.base_notes` / `history`; `LeadEngine.revert()` |
| 5 | Self-contained performance states | inline `lead.notes`/`base_notes`/`history`/`melody`/`source` |
| 6 | No `.mid` files; QWERTY in scope and togglable | `KEY_SEMITONES`; GUI `Keys` toggle |

There is **no sclang change** in 9.1: no `MIDIIn`, no `MIDIClient.init`, no new
SynthDef, no new audio bus and no new address to port 57120. The input is
Python-only and uses the existing Bus 5 output.

---

## 1. Data model

### 1.1 The mapped key (pure)

```python
@dataclass(frozen=True, slots=True)
class MappedNote:
    midi_in: int
    frequency: float      # what sounds (Hz)
    ratio: float          # the set's own float
    degree: int           # index in the sorted current ratios
    octave: int           # relative to the fundamental, BEFORE base_octave
    cents: float          # signed: played key vs the class (+ = key sharp)
```

### 1.2 The played take

A take is a tuple of the existing `LeadNote(step, length, degree, octave,
ratio_hint, velocity)`. `LeadMotif` gained four defaulted fields (appended, so
every existing constructor is unchanged):

```python
source: str = "generated"                    # "generated" | "played"
base_notes: tuple[LeadNote, ...] = ()        # the take as recorded (Revert)
history: tuple[tuple[str, int], ...] = ()    # (variation, index used)
melody_name: str = ""                        # provenance for the status line
```

`history` stores the **index used** for every applied variation because
`apply_variation` is seeded by `(seed, index)`: `Vary` applies at `index + 1`
while the `Variation` combo applies at the current index. A replay that stored
only the name would shift the RNG and change the line. JSON uses
`[["invert", 1], ["rhythm", 2]]`.

### 1.3 Melody file (`melodies/<name>.json`, format `ji-lead-melody`, v1)

```json
{
  "format": "ji-lead-melody", "version": 1,
  "name": "intro line", "saved_at": "2026-11-14T14:02:11",
  "grid": "1/8", "phrase_bars": 2,
  "meter_num": 4, "meter_den": 4, "bpm": 120.0,
  "quantize": 1.0, "swing": 0.0,
  "set": {"fundamental": 220.0, "anchor": 1.0,
          "ratios": [1.0, 1.125, 1.25, 1.3333333333333333, 1.5,
                     1.6666666666666667, 1.875]},
  "variation": {"name": "identity", "index": 1,
                "history": [["invert", 1]]},
  "base_notes": [{"step": 0, "length": 1, "degree": 2, "octave": 0,
                  "ratio_hint": 1.25, "velocity": 104}],
  "notes": [{"step": 0, "length": 1, "degree": 2, "octave": 0,
             "ratio_hint": 1.25, "velocity": 104}]
}
```

* `base_notes` is the take (the Revert target); `notes` is the line at save
  time; `variation.history` is a list of `[name, index_used]` pairs.
* `set` is provenance only: a melody is set-relative and loads against the
  **current** set. `ratio_hint` stores the set's own float; a rounded value
  such as `1.3333` would break `_match_ratio` and silently fall back to the
  degree.
* Name rules are the Stage 8.5 ones (`control.state.validate_state_name`), so
  `states/` and `melodies/` cannot drift. Saves are atomic (`.tmp` + flush +
  fsync + `os.replace`), a case-only duplicate is refused, and nothing is ever
  written into `config/`.
* `version` 1 only; a newer version is refused with one clear error.

### 1.4 Performance states — inline notes

The `lead` section of a performance state keeps every `LeadSettings` field and
adds the take inline, so a state needs no melody file (owner decision 5):

```json
"lead": { "...every LeadSettings field...", "variation_index": 3,
          "notes": [...], "base_notes": [...],
          "history": [["invert", 1], ["rhythm", 2]],
          "melody": "intro line", "source": "played" }
```

Old states without these keys load unchanged as a generated line. The whole
document is still parsed and validated before anything is applied; one bad
inline note aborts the load. `version` stays **1**.

---

## 2. Mapping maths (C1)

```text
f_in  = 440 * 2 ** ((midi_note - 69) / 12)          # mpe_output.midi_note_frequency
oct   = floor(log2(f_in / fundamental))
f_oct = f_in / 2 ** oct                             # folded into [F, 2F)
d(r)  = 1200 * log2(f_oct / (fundamental * r))      # signed cents
ratio = the ratio minimising |d(r)|
f_out = fundamental * ratio * 2 ** oct
degree = sorted_ratios.index(ratio)
note.octave = oct - base_octave                     # so projection reproduces f_out
```

* `ratios` are sorted ascending, positive and de-duplicated (the existing
  `lead._clean_ratios`); an empty/invalid sequence normalises to `(1.0,)`.
* `midi_note` must be an int 0–127; `fundamental > 0` and finite.
* A key exactly on a class returns **that float** (cents 0), so playback through
  `LeadEngine._project_locked` is bit-exact.
* Ties between two classes are impossible for real just ratios; if they occur
  the lower ratio wins (the comparison is a strict `<`).
* `cycle`/`free`/`anchor` mapping modes were considered and deliberately
  deferred; only `nearest` ships.

Worked values (F = 220, just major `1, 9/8, 5/4, 4/3, 3/2, 5/3, 15/8`):

| MIDI | note | `oct` | class | cents | degree/octave |
|---|---|---|---|---|---|
| 61 | C#4 277.18 | 0 | 5/4 = 275.00 | +13.7 | 2 / 0 |
| 64 | E4 329.63 | 0 | 3/2 = 330.00 | −1.9 | 4 / 0 |
| 69 | A4 440.00 | 1 | 1/1 = 220.00 | 0.0 | 0 / 1 |
| 66 | F#4 369.99 | 0 | 5/3 = 366.67 | +15.6 | 5 / 0 |

**Proof used by the tests** (`tests/test_lead_input.py`
`test_mapping_worked_values`):

* the four worked rows above;
* a synthetic 12-class equal-tempered set maps every class to itself with
  `abs(cents) < 1e-9`;
* a folded pitch exactly at `2F` returns `ratio = 1`, `cents = 0`, octave 2;
* for `base_octave` −2/0/+2 the mapped note is loaded into `LeadEngine`, and
  `LeadEngine._project_locked` reproduces `MappedNote.frequency` exactly
  (`rel_tol=1e-12`). That is the `degree`/`octave` round trip.

---

## 3. Live play (C1, C5)

`LeadEngine`/`LeadRig` gained one `source`:

| `source` | Sequencer | Input monitor | Meaning |
|---|---|---|---|
| `generated` | runs (today) | silent | accepted default |
| `played` | runs the played motif | silent | hear the take, then `Vary` it |
| `live` | stopped | on | play freely |
| `record` | stopped | on | capture |

* `LeadRig.set_source(name, at_time=None)` is the one place that decides who
  drives Bus 5. One shared `_sync_scheduler()` is used by `start`,
  `set_transport`, `set_enabled`, `set_source` and every state recall, so
  `set_transport(True)`/`set_enabled(True)` can no longer start the sequencer
  while the input is monitoring.
* Entering `live`/`record` **stops** the scheduler (note-off + CC 123), never
  mutes it; no interleaved bends or stale gates.
* Leaving `live`/`record` for `generated`/`played` while the transport runs
  starts on the next bar line: the bridge passes
  `rhythm.clock.time_of_beat(next_bar_beat)` to
  `LeadScheduler.start(at_time=...)`.
* `Live` off returns to `played` when a played line is loaded, else
  `generated` (derived, never a stale "previous source").
* `LeadRig.playing` remains false in `live`/`record`.
* The input rig never opens or closes Bus 5: it uses `lead_rig.output`.
* `LeadRig.set_note_callback` and the input rig share the existing
  `/ji/lead/note` status (deduped by the rig) so `Last:` shows what was played.

### 3.1 Note ownership

* One member channel, monophonic. `note_on` with a held note sends `note_off`
  first, then pitch bend, then `note_on` (bend before note-on, like
  `LeadScheduler._play`).
* `note_off` only releases the held key; a mismatched or already-released key
  is ignored.
* Sustain (CC 64) defers note-offs while down; a note-on while down retriggers
  normally. A release with the pedal down is sent when the pedal comes up.
* Velocity is `clamp(round(velocity * velocity_scale), 1, 127)`.
* Every exit path sends note-off + `all_notes_off()`: `stop()`, `Live` off,
  source change, `Lead` off, port lost, bridge stop. A **transport stop is not
  an exit path** (owner decision 2): live keeps monitoring with the transport
  stopped; only an in-flight recording finishes.

### 3.2 QWERTY

The key map lives in `control/lead_input.py` (pure and tested), not in the
widget:

```python
KEY_SEMITONES = {"a": 0, "w": 1, "s": 2, "e": 3, "d": 4, "f": 5,
                 "t": 6, "g": 7, "y": 8, "h": 9, "u": 10, "j": 11}
```

Base note `60 + 12 * octave_shift`; `z`/`x` shift the octave −/+ (clamped so
every key stays in 0–127); fixed velocity 100. The GUI sends
`/ji/lead/input/note_on` / `note_off` to the bridge, ignores auto-repeat,
ignores presses while a `QLineEdit`/`QComboBox`/spin box has focus and while a
modifier is held, and releases everything on toggle off, focus loss, window
close or a source change. `Keys` is GUI-local and does not imply `Live`: keys
sound only in `live`/`record`.

---

## 4. Recording and quantisation (C2, C3)

### 4.1 State machine

```text
idle ──Rec──> count_in ──target beat──> recording ──window end / Stop──> idle
                  └──Stop/cancel────────────┘
```

* Record needs a running transport (`[LEAD-IN] record needs the transport
  (press Play)`), because the beat clock is frozen when stopped.
* `count_in` vocabulary: `off | 1 beat | 1 bar | 2 bars`. A bar is
  `ceil(bar_quarters)` whole quarter beats, the same ceil rule as the loop
  count-in: 4/4 → 4, 3/4 → 3, 6/8 → 3, 5/8 → 3, 7/8 → 4.
* `now = rhythm.clock.beat_at()`;
  `start_beat = ceil((now + count_beats) / bar_quarters) * bar_quarters`
  (always a bar line); `end_beat = start_beat + bars * bar_quarters`.
  No note is captured or sounded before `start_beat`.
* A second Rec during the count-in skips it and opens the window at the next
  bar line (`[LEAD-IN] count-in skipped (second Rec)`), mirroring the loop
  convention.
* Stop during the count-in cancels silently; Stop during recording keeps the
  take (open notes close at `now`, are quantised and loaded).
* A transport stop during recording finishes the take, keeps it, logs
  `[LEAD-IN] recording aborted (transport stop); take kept`, and does not start
  the scheduler: the source becomes `live` if the Live toggle was on, else
  `played`.
* On a normal finish the source becomes `played` (hear the take).
* The supervisor is a daemon thread polling every ~5 ms, recomputing
  `clock.time_of_beat(...)` each iteration (a tempo change cannot break the
  window), with a generation token so a cancelled take cannot start a stale
  recorder. Maximum window: 8 bars.

### 4.2 Recorder rules

* A re-struck key closes the previous note at the new onset.
* Monophonic reduction is fixed to **last**: a chord keeps the last note and
  increments `dropped`, logged once at finish.
* A note held past the window closes at the window end; notes outside
  `[start_beat, end_beat)` are dropped; zero/negative length becomes one step;
  `finish()` returns the notes and resets the capture (an empty take is `()`).

### 4.3 Quantiser (exact contract)

```python
s = (position_beats - start_beat) / grid_quarters      # in grid steps
k = round(s)
swung = k + swing_offset(k, swing, step_seconds, steps_per_beat) / step_seconds
s_q = s + strength * (swung - s)                       # 0..1
return int(round(s_q))
```

* `strength` 0–1 (GUI 0–100 %): 1 = exact grid, 0 = as played (the stored step
  is still the rounded integer).
* The take is quantised against the **swung** grid but the stored `LeadNote`
  step is **unswung**: playback re-applies `bass.swing_offset`, so the take
  lands where it was played and still follows later groove changes. Swing is
  never baked into `LeadNote`.
* `swing` is `engine.effective_swing()` (groove link or the lead's own swing)
  at record time. `Breath` is playback-only and never part of quantisation.
* Length = `max(1, off_step - on_step)`, clamped to the next onset and the
  phrase end by `lead._normalize`.

Acceptance values (120 BPM, 4/4, grid 1/8 = 0.5 beats/step, record from beat
4): onsets at 4.02 / 4.52 / 5.08 / 6.06 beats → steps 0, 1, 2, 4; offsets
giving lengths 1, 1, 2, 1. `tests/test_lead_input.py::test_quantizer` asserts
this, plus strength 0/0.5/1, a swung odd step that still stores the unswung
step, the `1/8T` grid, and the 3/4, 5/8 and 7/8 count-in ceil.

### 4.4 Finish → load → play

1. Quantise to `LeadNote`s; the dropped chord count is logged once.
2. `LeadEngine.load_motif(...)` with `grid` and `phrase_bars = bars`,
   `gate_mode = "note"`.
3. `source = "played"`; if the transport still runs, restart the scheduler at
   the bar line at/after `now` (the take ends on a bar line, so the phrase
   loops seamlessly); if stopped, stay stopped.
4. One log line:
   `[LEAD-IN] recorded 13 notes / 2 bars (grid 1/8, quant 100 %, 2 chord notes reduced)`.
5. Send `/ji/lead/state` + `/ji/lead/input/state` once (no per-note spam).

---

## 5. The played phrase inside `LeadEngine` (C5)

`LeadEngine.load_motif(notes, *, grid, phrase_bars, meter_num, meter_den,
name="", base_notes=None, history=(), variation="identity",
variation_index=0)` validates each note (integer `step >= 0`, `length >= 1`,
`degree >= 0`, `octave` −8..8, `ratio_hint > 0`, `velocity` 1..127), normalises,
sets `phrase_steps`, updates `grid`/`phrase_bars`/`gate_mode = "note"` in the
same lock, stores the take and **never** calls `generate_motif`.
`clear_melody()` returns to a fresh seeded generated line (`gate_mode`
untouched); `revert()` restores `base_notes`, clears history, sets index 0 and
keeps the settings.

### 5.1 Source-aware regeneration

`_GENERATION_KEYS` still regenerates a generated line. A **played** line is
never implicitly regenerated; the policy table is:

| Setting | Played-motif behaviour |
|---|---|
| `grid` | rescale `base_notes` (`new_step = round(old_step * old_gq / new_gq)`), replay history, `_normalize`, rebuild `phrase_steps` |
| `phrase_bars` | crop to the new length or tile the notes to fill a longer phrase |
| `density` | field only; one `[LEAD] density has no effect on a played line` line |
| `seed` | field only; `_variation_index = 0` and history cleared; notes unchanged |
| `octave_range` | field only; clamp recorded octaves into ±range only when `range > 0` |
| meter change | rescale/crop to the new bar length, like grid + phrase_bars |
| `variation` | apply via `_apply_variation_locked` (as today) |
| everything else | playback-only, unchanged |

`set_settings()` (state recall) applies the same policy and never regenerates a
played motif; the recall loads the motif explicitly afterwards.

### 5.2 Variations, history and Revert

* `_vary_locked` and `set_setting("variation", …)` apply
  `apply_variation(name, motif, seed=seed, index=_variation_index,
  ratios=ratios)`, append `(name, index)` to `history` and **re-attach**
  `source`/`base_notes`/`melody_name` from the pre-variation motif. Re-attach
  matters most for `random`, which returns a freshly generated motif and would
  otherwise lose the Revert target.
* `apply_variation()` stays pure and does not touch `history`.
* `revert()` logs `[LEAD] reverted to the recorded base (13 notes)`.
* `set_variation_index(index, force=False)`:
  * generated: today's behaviour (regenerate from the seed);
  * played: rebuild from `base_notes` and replay the stored history. Every
    entry whose stored index is `<= index` belongs to the restored state (a
    combo change applies at the current index without advancing it, so
    filtering by stored index -- not by slice length -- is what makes the
    replay exact whatever triggered it). A target past the stored history
    keeps applying the selected variation with increasing indices.
* Compounding is intended (owner: aleatoric composition); only `Revert` undoes.

`describe_phrase()` now appends `source=…` and the melody name, e.g.
`seed=1 variation=identity #0 steps=16 notes=12 source=played "intro line"`.

### 5.3 `gate_mode`

```python
gate_mode: str = "beat"      # "beat" = today; "note" = the note length wins
```

The scheduler caps every gate at `min(gate_beats, length × step)` in `beat`
mode. A recorded 2-beat note would be cut by the default 0.5-beat gate, so
recording and melody loading set `gate_mode = "note"`, where the scheduler uses
`note.length * step_quarters * beat_seconds` with no `gate_beats` cap. The GUI
`Note len` checkbox (checked = note mode) lets the player go back.

`/ji/lead/state` appends `gate_mode` as value **17** (after `playing`); the
first 16 values and their order are unchanged. `parse_lead_state` accepts 16 or
17 values so a stale GUI cannot wedge.

---

## 6. OSC surface

All additive.

### 6.1 UI → bridge

| Address | Args |
|---|---|
| `/ji/lead/set_gate_mode` | string `beat\|note` (the `Note len` checkbox) |
| `/ji/lead/input/set_enabled` | int 0/1 (Live) |
| `/ji/lead/input/set_quantize` | float 0..1 |
| `/ji/lead/input/set_count_in` | string `off\|1 beat\|1 bar\|2 bars` |
| `/ji/lead/input/set_bars` | int 1..8 |
| `/ji/lead/input/note_on` | int midi, int velocity (QWERTY) |
| `/ji/lead/input/note_off` | int midi (QWERTY) |
| `/ji/lead/input/record` | none |
| `/ji/lead/input/stop` | none |
| `/ji/lead/input/revert` | none |
| `/ji/lead/input/clear` | none (back to generated) |
| `/ji/lead/input/refresh` | none (close/re-open the port) |
| `/ji/lead/melody/save` | string name |
| `/ji/lead/melody/load` | string name |
| `/ji/lead/melody/delete` | string name |
| `/ji/lead/melody/request` | none |

### 6.2 Bridge → UI

```text
/ji/lead/input/state  [live, port_ok, port_name, quantize, count_in, bars,
                       recording, count_beats, note_count, source,
                       melody_name, last_in_note, last_out_note, last_cents]
                      # 14 values, exact order
/ji/lead/melody/list  [count, name_1 .. name_N]
/ji/lead/melody/state [count, last_saved, last_loaded, message]
```

Rules: one small state message per change, never per note; `input/state` is
sent at start, on `/ji/ui/hello`, after every applied change, after a record
state change, after a source change and after a melody action.
`revert`, `clear` and a melody `load` are queued on `PHASE_LEAD` while the
transport runs and immediate when stopped (they replace the sounding line).
`set_enabled` (Live), `record`, `stop` and `refresh` are immediate start/stop
gestures; `note_on/off` are never queued; save/delete/list/request are
immediate file operations.

`LeadRig.set_source` also keeps the scheduler rule in one place, so a state
recall, a Live toggle, `Lead on` and a transport start cannot disagree.

---

## 7. Config and flags

`config/lead_input_config.json` (new, read-only):

```json
{
  "port": "Launchkey",
  "match": "contains",
  "channel": 0,
  "quantize": 1.0,
  "count_in": "1 bar",
  "bars": 2,
  "velocity_scale": 1.0,
  "octave_shift": 0
}
```

`port` empty + `--lead-in` lists the available inputs and disables the input
(one line), never fails the rig. `match` `contains|exact`; `channel` 0 (omni)
or 1–16; `quantize` 0–1; `count_in` one of the four strings; `bars` 1–8;
`velocity_scale` 0.5–2.0; `octave_shift` −2..2. Unknown keys are ignored; a bad
value disables the input only.

Launcher/bridge flags:

```text
--lead-in                  enable the MIDI input (off by default)
--lead-in-port <name>      controller name/substring (overrides the config)
--lead-in-config <path>    alternative config
--melody-dir <path>        melody library folder (default melodies/)
```

`--lead-in` without `--lead` prints one line and is ignored; `--lead` without
`--lead-in` is exactly the old rig. `--check` prints the mido input names beside
the output names, verifies the configured input when `--lead-in` is set, and
validates `--melody-dir` (creates it, reports the count) like `--state-dir`.
`--dry-run` lists the new flags and the chosen source.

---

## 8. GUI

One compact **Lead input** group under **Lead** (two rows + status; the Lead
group does not grow except for the `Note len` checkbox):

```text
Lead input
Port: Launchkey 49 [Refresh]  [ ] Keys  [ ] Live  [Rec] [Stop] [Revert] [Clear]
Quant [----100%]  Count-in [1 bar v]  Bars [2]  Melody [intro line v] Name [______] [Save] [Load] [Del]
Input: Launchkey 49 - idle - 13 notes | Melody: intro line (13 notes) - played
```

* `Port:` is a read-only label fed by `input/state.port_name`; `Refresh` asks
  the bridge to close/re-open the port. The GUI never enumerates MIDI devices
  and no ports address is added.
* `Keys` is GUI-local; `Live` mirrors the source (`live`/`record`); `Rec`,
  `Stop`, `Revert` and `Clear` follow the enablement rules; `Quant` coalesces at
  ~30 Hz; the melody combo is filled from `/ji/lead/melody/list` and the name
  box is pre-filled from the selection.
* `Note len` sits next to the Lead `Gate` knob. The window default is
  `1280x1258` and `tests/test_ui.py` bounds the height at 1290; the scroll area
  remains the small-screen net.

---

## 9. Diagnostics

One prefix, `[LEAD-IN]`, never per note:

```text
[LEAD-IN] input=Launchkey 49 channel=0 match=contains quantize=1.00 count-in=1 bar bars=2
[LEAD-IN] notes sound as the nearest class of the current set (map=nearest)
[LEAD-IN] live on (sequencer paused at step 7)
[LEAD-IN] live off; phrase resumes at bar 5
[LEAD-IN] record requested (count-in 1 bar, 2 bars) at beat 8
[LEAD-IN] recording started at beat 12 (window 12..20)
[LEAD-IN] recorded 13 notes / 2 bars (grid 1/8, quant 100 %, 2 chord notes reduced)
[LEAD-IN] recording aborted (transport stop); take kept
[LEAD-IN] record needs the transport (press Play)
[LEAD-IN] count-in skipped (second Rec)
[LEAD-IN] melody saved "intro line" (13 notes, melodies/)
[LEAD-IN] melody loaded "intro line" (13 notes, grid 1/8, 2 bars)
[LEAD-IN] melody deleted "intro line"
[LEAD-IN] reverted to the recorded base (13 notes)
[LEAD-IN] cleared the melody; generated line restored
[LEAD-IN] disabled: no input port configured; available: ...
[LEAD-IN] disabled: no input matched "Foo"; available: ...
[LEAD-IN] disabled: 2 inputs matched "MIDI"; use a more specific name: ...
[LEAD-IN] input lost: ...; the rest of the rig continues
```

The existing `[LEAD]` lines stay; `describe_phrase()` gains `source=…` and the
melody name. No line per note.

---

## 10. Tests

Pure (no mido/Qt/sockets), run from `ji-live/`:

```bash
~/.ji-tools/uv run --with python-osc python tests/test_lead_input.py
```

Covers: the mapping worked table and 0-cent self-mapping; the degree/octave
round trip through `_project_locked`; the quantiser (straight/swung/triplet
grids, strength, 3/4, 5/8, 7/8, bar lines); the recorder (pairs, retrigger,
chord reduction, held/zero/outside, empty); the key map; the lazy port
(contains/exact, 0/>1 matches, velocity 0 = off, sustain, channel filter); the
input rig (bend-before-on, retrigger off, CC 123, silent in `generated`,
mismatched off, velocity scale); the record state machine (count-in skip,
cancel, partial keep, transport-stop abort, load, no auto-play); the melody
library (round trip, names, case duplicate, versions, atomic temp cleanup,
sorting, delete); the played-motif policy (rescale, crop/tile, density/seed
no-op, octaves, mutation contour, vary/history, `random` provenance, revert,
history replay, clear, 17-value state, generated stream unchanged); `gate_mode`
note vs beat; inline state notes (round trip, old states, bad note aborts,
recall then Revert); and the no-eager-mido rule.

Full non-Qt regression list: see the prompt / `README.md`.

---

## 11. Risks and decisions

* **Double-trigger.** CoreMIDI lets several apps open one source. If Ableton
  listens to the controller, every note plays twice (snapped + raw). Turn off
  Ableton's Track input for that controller (Live → Preferences → MIDI Ports);
  see `START_HERE.md` troubleshooting.
* **Monophonic ownership.** One member channel. The input rig and the scheduler
  never run together: the `source` switch stops one before the other plays. No
  "mute" leaves the sequencer running.
* **Bar-line resume.** Re-entering `generated`/`played` passes
  `time_of_beat(next_bar)` to `start(at_time=…)`, so the phrase never jumps
  mid-bar.
* **Swing double-application.** Quantise against the swung grid, store the
  unswung step. Storing the swung position would apply swing twice.
* **Breath.** Playback-only; never in recorded timing.
* **`ratio_hint` floats.** Use the set's own float; JSON round-trips `float`
  exactly. A rounded hint breaks `_match_ratio` and silently falls back to the
  degree.
* **Regeneration trap.** `_GENERATION_KEYS` regenerates; a played motif goes
  through the §5.1 policy table or a Grid change would destroy the take.
* **`random` loses provenance.** Re-attach `source`/`base_notes` after
  `apply_variation` or Revert breaks.
* **State compatibility.** `version` 1, optional keys only, the first 16
  `/ji/lead/state` values unchanged, `parse_lead_state` accepts 16 or 17.
* **mido import.** Lazy only; `tests/test_lead.py` and `test_lead_input.py`
  assert it.
* **Callback thread.** No blocking, disk I/O or OSC burst inside the mido
  callback; the record supervisor is a separate daemon thread.
* **Stuck notes.** Every exit path sends note-off + CC 123 (tested with a fake
  output).
* **Window height / docs assertions.** `test_ui.py` bounds the height and
  `test_startup.py` checks every flag against `START_HERE.md`.
* **No hardware in the agent environment.** There is no MIDI hardware, IAC,
  Ableton or audio here; nothing in this stage was listened to.

---

## 12. Human acceptance checklist (the owner is the authority)

1. **Routing**: the controller is visible to Python (`mido.get_input_names()`);
   Ableton's Track input for that controller is off, so no note is doubled.
   Nothing new is needed in Audio MIDI Setup.
2. **Play in**: with `Live` on, every key sounds on Bus 5 at the nearest class
   in the register played. A key on a class is exact; a key between two classes
   is pulled to the nearer one. `Last:` shows the played note.
3. **Live gating**: `Live` off → the keyboard does not sound over the generated
   line. `Live` on → the sequencer stops; `Live` off → it resumes on the next
   bar line (no mid-bar jump).
4. **QWERTY**: `Keys` on → `a w s e d f t g y h u j` play the same mapped line;
   `z`/`x` shift the octave; typing in the melody name box never plays notes;
   toggling off releases everything.
5. **Record**: Play, press `Rec` with `1 bar` count-in; the count is silent, the
   take starts on a bar line and stops on its own after `Bars`. The take plays
   back quantised, on the grid, with swing if the groove swings. `Stop` early
   keeps the partial take; a second `Rec` during the count skips it.
6. **Groove quantisation**: with `Groove link` on and a swung groove, a take
   played "straight" lands on the swung grid; changing the groove afterwards
   moves the phrase with it (the stored step is unswung).
7. **Variations**: `Vary` transforms the recorded line (transpose, invert,
   retrograde, thin, ornament, rhythm, random); variations compound; `Revert`
   restores the recorded line exactly.
8. **Mutation**: pressing `Mutate` keeps the contour — notes whose class
   survives keep their pitch; only removed classes move. The melody stays
   recognisable.
9. **Save/load**: type a name, `Save`; restart the bridge, `Refresh`/`Load`
   reproduces the line (grid, bars and notes). `Del` removes it. Nothing is
   written into `config/`.
10. **States**: save a state with a played line; recall it in a fresh bridge;
    the line and its variation state come back; `Revert` works immediately. An
    old state without notes loads as a generated line.
11. **No regressions**: generated lead, `Vary`/`Var every`, drone modes, arp,
    bass, drums, groove, breath, loops, home, states, autotune and the existing
    GUI behave exactly as before. Toggling `Live` never leaves a stuck note.
12. **Window**: the Lead input group fits at the default size; Status is
    reachable; the scroll area handles a small screen.
13. Record the result. **9.1 is not complete until the owner confirms the
    controller feels right, the quantised take is musical, and the variations
    keep working on a played line.**

---

## 13. What was not verified here

MIDI hardware, IAC, Ableton, audio output and sclang were **not** tested by the
agent (none are present in this environment). The evidence is the pure Python
harness plus the honest reporting above. Stage 9.1 awaits the owner's human
acceptance; no listening pass is claimed.

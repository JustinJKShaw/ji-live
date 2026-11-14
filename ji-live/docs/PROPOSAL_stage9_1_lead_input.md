# PROPOSAL — Stage 9.1: play the lead from a MIDI controller, record it, quantize it, keep varying it

**Status:** proposal only (design record). The implementation handoff is
[`docs/prompts/PROMPT_stage9_1_lead_input.md`](prompts/PROMPT_stage9_1_lead_input.md);
where the two differ, the prompt wins. The owner's decisions of 2026-11-14 are
recorded in §10.

**Owner brief (verbatim intent):** for the **lead** I want to be able to play
MIDI with a MIDI controller (or the keyboard) and have that output pitches in the
pitch class [the current JI set]; I want to record that and have it quantized to
the beat/groove; MIDI melodies can be input and are saved; then they can be
continually modified using the existing lead Variation system.

---

## 1. The feature in five capabilities

| # | Capability | One-line definition |
|---|---|---|
| C1 | **Play in** | A note played on a controller (or the computer keyboard) sounds on Bus 5 as the **nearest class of the current JI set**, in the register played |
| C2 | **Record** | Capture the played notes as `(onset, length, degree, octave, ratio_hint, velocity)`, aligned to the bridge beat clock |
| C3 | **Quantize** | Snap onsets/lengths to the lead `Grid`, with a quantize strength, optionally against the groove's swing (and optionally against drum hits) |
| C4 | **Save** | A recorded melody is a named file in a melody library (`melodies/<name>.json`), listable/saveable/loadable/deletable, never written into `config/` |
| C5 | **Keep varying** | A loaded/recorded melody becomes the lead's **motif**, so `Vary`, `Var every` and all eight variations keep transforming it exactly as they transform the generated line |

C5 is the cheap part: `apply_variation()` is already a pure function of a
`LeadMotif`, so a recorded phrase inherits the whole variation system **provided
the phrase enters the engine as a motif and the engine stops regenerating it**
(§4).

---

## 2. What exists today (facts that constrain the design)

| Fact | Where |
|---|---|
| There is **no MIDI input anywhere**. Every `mido` call is `get_output_names()` / `open_output()`. No `open_input`, no `get_input_names`, no `MIDIIn`/`MIDIdef` in any `.scd` | `control/mpe_output.py:245,261`, `control/bass.py:1131,1145`, `control/drums.py:690,704` |
| The lead is **generated only**. `LeadMotif` is produced by `generate_motif()` / `apply_variation()`; there is no note setter on `LeadEngine` or `LeadRig` | `control/lead.py:772,916,1090` |
| Notes are stored **set-relative**: `LeadNote(step, length, degree, octave, ratio_hint, velocity)`; projection keeps `ratio_hint` if it survives, else `ratios[degree % len]`, octave unchanged — so the line survives mutations | `control/lead.py:621-698`, `1268-1297` |
| Shape keys `{seed, grid, phrase_bars, density, octave_range}` **regenerate** the motif; everything else is playback-only. `set_variation_index(force=True)` also regenerates | `control/lead.py:188-191`, `1325-1388` |
| `on_set()` never regenerates — it only stores the snapshot and re-projects | `control/lead.py:1216-1241` |
| Eight variations are pure and seeded by `(seed, index, salt)`: identity, transpose, invert, retrograde, thin, ornament, rhythm, random | `control/lead.py:117-126`, `916-1070` |
| The lead scheduler free-runs from the transport start: step 0 is the phrase origin, swing + breath are added to the **sleep target only**, no lookahead | `control/lead.py:1499-1780` |
| `LeadScheduler._play()` always sends **bend then note-on**, then a `threading.Timer` gate; the note-off retrigger cancels a stale gate by token | `control/lead.py:1700-1772` |
| Beat positions come from `BeatClock.beat_at(t)` / `time_of_beat()` / `next_beat_time(q)`; the clock re-anchors on tempo change and freezes when stopped | `control/rhythm.py:371-478` |
| Swing is one definition, `bass.swing_offset(step, swing, step_seconds, steps_per_beat)` — odd 8th/16th steps late by `swing*0.5*step`; a 1/4 grid is straight; there is no triplet *swing* (1/8T is a grid, not a feel) | `control/bass.py:676-698` |
| Every control change is beat-quantized: `PHASE_LEAD = 7`, immediate when the transport is stopped | `control/quantize.py:64-87` |
| Frequency → MIDI is `frequency_to_pitch(f, R)`: nearest 12-TET note + 14-bit bend, `R = 48` → 0.59 cents resolution | `control/mpe_output.py:102-152` |
| State files are JSON in `states/`, atomic `.tmp`+fsync+`os.replace`, names 1–40 chars, never in `config/`; new settings fields round-trip with no `state.py` edit (defaults) | `control/state.py:1051-1207` |
| The GUI never talks to Ableton/sclang directly; the bridge owns state | `docs/README_stage7.md:38-41` |
| Docs and `--help` are part of acceptance: `tests/test_startup.py` asserts every launcher flag appears in `docs/START_HERE.md` | `tests/test_startup.py:340-397` |
| The window is `1280×1198`; `tests/test_ui.py` bounds the height | `ui/ji_ui.py:1045`, `tests/test_ui.py:3256` |

**Consequence:** C1–C4 are greenfield (input port, capture, quantize, file
format). C5 is a small, surgical change to `control/lead.py`. The hard part is
not the feature; it is not regressing the generated lead.

---

## 3. Architecture: where the input lives

### 3.1 Recommended: bridge-owned input, one note entry point

```text
   MIDI controller ──mido input──┐
                                 ├──> LeadInputRig.note_on/off() ──> mapper ──> MPEOutput (Bus 5)
   GUI QWERTY keys ──OSC─────────┘         │
                                           └──> LeadRecorder (beat clock) ──> LeadEngine.load_motif()
                                                                                    │
                                                                          LeadScheduler + apply_variation()
```

* **Same process as the lead rig** → the input sees `LeadEngine.ratios`,
  `fundamental`, `BeatClock`, and the same `MPEOutput`; no new bus, no new
  process, no new sclang file.
* **One entry point** `LeadInputRig.note_on(midi, velocity)` means the physical
  controller and the computer keyboard share the mapper, the recorder and the
  note-ownership rules.
* The pure pieces (`map_note`, `LeadRecorder`, `MelodyLibrary`) have no mido
  import, so `tests/test_lead.py`-style suites run without hardware.

### 3.2 Rejected alternatives

| Option | Why not |
|---|---|
| **sclang `MIDIIn` + `MIDIdef`** | The rig's contract is "Python owns MIDI, sclang owns audio" (`docs/README_mpe.md:17-19`). sclang has no MPE out to Ableton, so the note would have to come back to Python anyway; it duplicates the pitch maths and the "no sclang MIDI" rule |
| **GUI-owned `mido.open_input` → OSC to bridge** | Adds a hop and a hardware owner to the GUI process; closing the GUI would drop the controller; the bridge already owns MIDI and the clock. Only worth it for QWERTY, which is not a hardware port |
| **Play audio into the live autotune instead** | That path already exists (guitar/voice) but cannot be replayed as notes, cannot be varied, and is 43 ms (Tartini) + 100 ms (`PitchShift`) late — wrong tool |
| **Ableton-side recording** | The whole point is that the set, the variations and the quantize all live in the bridge; an Ableton clip would leave the JI mapping behind |

### 3.3 Latency budget

Controller → mido callback → mapper (~10 µs) → `MPEOutput` → IAC → Ableton:
**a few milliseconds plus the Ableton buffer**, i.e. an order of magnitude
tighter than the autotune input path. No lookahead buffer is needed or wanted;
the rig's idiom is a monotonic deadline per event (`lead.py:1635-1690`).

### 3.4 The computer keyboard (in scope, togglable)

A `QKeyEvent` handler on the main window maps `a w s e d f t g y h u j` to a
chromatic octave (plus `z`/`x` for octave shift), emitting
`/ji/lead/input/note_on|off` to the bridge. Same mapper, same recorder, same
`LeadInputRig`; only the transport differs. A `Keys` toggle turns it on/off so
it never competes with the controller or with a text field.

Details that matter: filter `event.isAutoRepeat()`; send a note-off for every
down key when the window loses focus or the toggle goes off; ignore keys while a
`QLineEdit` (melody name) has focus; and keep the key→semitone map in
`lead_input.py` (pure, testable), not in the widget.

### 3.5 MIDI routing: what you need on your end

**Nothing to install, and nothing to create in Audio MIDI Setup for the
controller.** The path stays inside Python + the existing IAC output:

* `python-rtmidi` is a CoreMIDI client: any USB-class-compliant keyboard appears
  in `mido.get_input_names()` when plugged in. No driver unless the maker ships
  a vendor-specific one.
* Bluetooth MIDI (if that is what you use): pair once in Audio MIDI Setup →
  **Window → Show MIDI Studio → Bluetooth Configuration**; it then appears as a
  normal source.
* SuperCollider is **not** involved: no `MIDIIn`, no `MIDIClient.init`, no
  change to `ji_start.scd`. The input never touches sclang.
* No MIDI clock/sync, no virtual-MIDI software, no second interface. The bridge
  `BeatClock` is the timing master; the controller only sends notes.

**The one thing to change: stop Ableton from also listening to the controller.**
CoreMIDI lets several apps open the same source at once (there is no exclusive
lock), so if Live has that port enabled as a Track input you would hear every
note twice — once snapped on the Lead track, once raw (12-TET) wherever that
port is routed.

* Live → Preferences → **Link/Tempo/MIDI** → MIDI Ports: switch **Track** off
  (and **Remote** too, unless you map knobs) for the controller's input port.
  Leave the IAC buses exactly as they are.
* If you want to keep the controller enabled for Remote/knob mapping, instead
  make sure **no track's MIDI From is that port or "All Ins"**. The rig's five
  tracks are already set to specific IAC buses, so this is true unless a new
  track is added.
* Close or mute any other app that listens to the controller (standalone synth,
  MIDI monitor).

**Verify before the feature lands:**

```bash
cd ji-live
uv run --with mido --with python-rtmidi python -c "import mido; print(mido.get_input_names())"
```

Your controller should be listed. The new `--check` will print the same list and
verify the configured name. Names often carry a suffix (`Launchkey 49 MIDI 1`),
which is why the config matches a substring.

**Caveats:** one input port at a time in v1 (two controllers are not merged); run
the launcher in a normal desktop session, because CoreMIDI hot-plug
notifications can be missed over SSH; a controller with only DIN out needs a
USB–MIDI interface, which then appears as the port.

---

## 4. The phrase model: how a played melody becomes a `LeadMotif`

### 4.1 Minimal, additive change to `LeadMotif`

```python
@dataclass(frozen=True, slots=True)
class LeadMotif:
    ...                                        # unchanged fields
    source: str = "generated"                  # "generated" | "played"
    base_notes: tuple[LeadNote, ...] = ()      # the recorded line, before any variation
    history: tuple[tuple[str, int], ...] = ()  # (variation name, index used), in order
    melody_name: str = ""                      # provenance for the status line
```

`base_notes` is what **Revert** restores; `history` is what makes
`set_variation_index()` reproducible for a played phrase (today it regenerates
from the seed, which would destroy a played line). The index travels with the
name because `apply_variation` is seeded by `(seed, index)` and `Vary` applies at
`index + 1` while the combo applies at the current index.

### 4.2 The regeneration policy becomes source-aware

`_GENERATION_KEYS` stays the same for generated motifs (their tests pin it). For
a **played** motif:

| Control changes | Played-motif behaviour |
|---|---|
| `grid` | **re-quantize** the base notes from their stored step positions (`new_step = round(old_step × old_grid_quarters / new_grid_quarters)`), then `_normalize()`; the phrase is re-gridded, not regenerated |
| `phrase_bars` | crop to the new length, or tile the notes to fill a longer phrase; `_normalize()` clamps lengths |
| `density` | no effect on the notes (the player already chose them); the field updates, one log line says so. Density still shapes the *next* generated phrase |
| `seed` | no effect on the notes; it reseeds future variations (`_variation_index = 0` as today) |
| `octave_range` | no effect beyond clamping the recorded octaves into `±range` when > 0 |
| meter change | re-quantize/crop to the new bar length, like `phrase_bars` |
| `variation`, `vary()`, `variation_bars` | **unchanged behaviour** — this is the whole point |
| `velocity`, `gate_beats`, `base_octave`, `swing`, `groove_link`, `enabled` | playback-only, unchanged |

`on_set()` is untouched: a played melody keeps its `ratio_hint`/`degree`, so a
**Mutate** moves only the notes whose class disappeared, exactly like the
generated line. That is the consistency contract C5 needs, already tested in
`tests/test_lead.py:407`.

### 4.3 "Keep varying" semantics

* `Vary` applies `settings.variation` to the **current** motif (compounding, as
  today) and appends `(name, index)` to `history`.
* `Revert` (`/ji/lead/input/revert`) restores `base_notes`, clears `history`,
  sets `variation_index = 0`, keeps the settings. This is the safety net the
  generated line does not need because `seed` can rebuild it.
* `random` on a played motif still calls `generate_motif()` — a fresh seeded
  line. Document it as "random replaces the recorded line; Revert brings it
  back", or (better) give the combo a confirmation in the status line.
* `set_variation_index(index)` for a played motif replays the stored entries
  from `base_notes`, each with its stored index, filtering by the applied
  variation index (`used_index <= index` rather than a raw slice) because a
  combo change applies at the current index without advancing it; if `index >
  len(history)` it keeps applying the selected variation until it reaches
  `index`. State recall (§8.3) then reproduces the exact line. (The final
  implementation filters by applied index; see `docs/README_stage9_1.md` §5.2.)

### 4.4 Gate lengths

`LeadScheduler._play()` caps every gate at `min(gate_beats, note.length × step)`.
A recorded 2-beat note would be cut by the default 0.5-beat gate. Two clean
options:

1. **Recommended:** add `gate_mode: "beat" | "note"` to `LeadSettings`
   (default `"beat"` = today). Recording sets it to `"note"`, so the recorded
   lengths win. It round-trips through `state.py` automatically.
2. Cheaper, no schema change: when a melody loads, set the `Gate` knob to the
   longest note (≤ 4 beats). Simple but silently moves a control.

---

## 5. Pitch mapping (C1): the exact maths

### 5.1 Nearest class in the played register (default `nearest`)

```python
f_in  = 440.0 * 2.0 ** ((midi_note - 69) / 12.0)      # mpe_output.midi_note_frequency
oct   = floor(log2(f_in / F))                         # F = fundamental, 220 Hz default
f_oct = f_in / 2.0 ** oct                             # folded into [F, 2F)
d(r)  = 1200.0 * log2(f_oct / (F * r))                # signed cents to class r
ratio = argmin_r |d(r)|                               # the set's own float, never a copy
f_out = F * ratio * 2.0 ** oct                        # what actually sounds
degree = sorted_ratios.index(ratio)
note.octave = oct - base_octave                       # so projection reproduces f_out
```

This is the same octave-aware rule sclang already uses for the autotune
(`test_live_rig.scd:234-252`), re-implemented in Python because no
12-TET→class mapper exists today. Properties:

* a key that lands exactly on a class is **bit-exact** (same float, no bend
  drift);
* the register is preserved (a bass note stays low; `base_octave` is applied
  later by the projection);
* the worst-case error is **half the largest gap in the set** — about 102 cents
  for the 7-class just major scale. Large gaps are inherent to a just set; a
  "minimum distance to the next class" display tells the player when a key is
  ambiguous.

### 5.2 Mapping modes: `nearest` ships, the enum is reserved

v1 ships **`nearest` only** (owner decision, 2026-11-14: "nearest frequency
class to the MIDI input"). The mapper still takes a `map_mode` argument so the
other rules can be added later without touching the recorder or the engine:

| Mode | Rule | Status |
|---|---|---|
| `nearest` | minimise cents as above | **shipped** |
| `cycle` | `degree = (midi_note − root_note) % len(set)` — every key is the next class | deferred |
| `free` | pass the played note through (12-TET) | deferred (A/B reference) |
| `anchor` | every key plays the anchor class | deferred (performance effect) |

### 5.3 What gets recorded

`LeadNote(step, length, degree, octave, ratio_hint, velocity)` with
`ratio_hint = the current set's float`, so later mutations are handled by the
existing projection. Velocity is the played velocity (optionally scaled), not the
lead's base velocity; `motif.velocity_base` is set to the lead `velocity` so the
projection's rebase keeps the played dynamics.

**Decision recorded here (and in §10):** played notes **follow** later set
mutations (class identity) rather than being pinned to the capture-time
frequencies. That is the lead's contract and the reason C5 works.

---

## 6. Recording and quantizing (C2/C3)

### 6.1 Capture

The recorder is pure and time-agnostic: the rig feeds it
`beat = clock.beat_at(time.monotonic())`.

```python
class LeadRecorder:
    def note_on(self, midi: int, velocity: int, beat: float) -> None: ...
    def note_off(self, midi: int, beat: float) -> None: ...
    def finish(self, *, phrase_steps: int, grid: str, strength: float,
               swing: float, reduce: str = "last") -> tuple[LeadNote, ...]: ...
```

Rules:

* a note-on with velocity 0 is a note-off (mido sends both forms);
* a re-struck key closes the previous note at the new onset;
* **monophonic reduction**, fixed to `last` in v1 (`top`/`first` are a later
  option): when a chord arrives, the last note is kept and the dropped count is
  logged once (`[LEAD-IN] 2 chord notes reduced`);
* a note held past the window closes at the window end;
* notes on the count-in are ignored (not captured, not sounded);
* transport stop mid-record: close open notes, finish with what exists, do not
  auto-play, log once.

### 6.2 Quantize

```python
s       = (beat - phrase_start_beat) / grid_quarters       # in grid steps
k       = round(s)                                         # nearest grid step
swung_k = k + swing_offset(k, swing, step_seconds, steps_per_beat) / step_seconds
s_q     = s + strength * (swung_k - s)                     # 0..1
step    = int(round(s_q))
```

* **Strength** 0–100 %: 100 % is exact grid, 0 % keeps the played timing (but the
  stored `step` is still an integer — `LeadNote` has no sub-step field; true
  microtiming would need a new `offset: float = 0.0` field, see phase 4).
* **Groove**: quantize against the **swung** grid (`bass.swing_offset`, the one
  definition) and store the **unswung** integer step. Playback re-applies the
  same swing, so the phrase lands where it was played **and** follows later
  groove/swing changes. This is the key subtlety: never bake the swing into the
  stored step.
* **Breath is not part of quantization.** Breath is a playback feel offset
  (`lead.py:1572-1583`); recording uses the canonical grid. Documented.
* **Length**: quantize the note-off with the same function;
  `length = max(1, off_step - on_step)`, clamped to the next onset and to the
  phrase end by `_normalize()`.
* **Optional `snap to drums`** (phase 4): collect active steps from the current
  `GrooveGrid` (union of lanes, or one chosen lane) and move `k` to the nearest
  active step within ±1 grid step; fall back to the plain grid when none is
  near. This is the only honest meaning of "quantize to the groove" beyond
  swing, and the grid is already available as `groove.grid.masks`.

### 6.3 Count-in and record window

* `Count-in`: `off | 1 beat | 1 bar | 2 bars`, reusing the loop count-in
  vocabulary and its ceil-to-whole-quarter-beats bar rule
  (`docs/README_stage8_6.md:441-451`, `test_live_rig.scd:426-437`).
* The record window starts on the **next bar line** (`phrase_start_beat =
  ceil(beat / bar_quarters) * bar_quarters`), so the phrase origin is a downbeat
  — matching the lead's "step 0 of the phrase is the transport origin" rule
  (`docs/README_stage8_3d.md:200-206`).
* `Bars`: `1 | 2 | 4 | 8` (the lead phrase max is 8). Auto-stop exactly at
  `phrase_start_beat + bars * bar_quarters`, computed on the beat clock — not a
  wall-clock timer.
* While **recording**, the lead sequencer is stopped (§7), so the click is the
  drums/bass/arp and the player hears only their own line.
* After `finish()`: quantize, build the motif, `LeadEngine.load_motif()`, set
  `phrase_bars`/`grid` on the settings, log
  `[LEAD-IN] recorded 13 notes / 2 bars (grid 1/8, quant 100 %)`, switch source
  to `played`, and start the sequencer at the next bar if the transport runs.

---

## 7. Live play and the sequencer on one monophonic port

Bus 5 is **one member channel, one note at a time**
(`docs/README_stage8_3d.md:70-75`). A live note and a sequenced note cannot share
it without a note-ownership arbiter.

**Recommended: a source switch** on the lead, not an overlay.

| `source` | Sequencer | Input monitor | Use |
|---|---|---|---|
| `generated` | runs (today) | off | the accepted default |
| `live` | stopped | on | play freely; nothing recorded |
| `played` | runs the recorded motif | off | hear the line, then `Vary` it |
| `record` | stopped | on | capture with the drums as the click |

Implementation: `LeadRig.set_source(name)` starts/stops `LeadScheduler` with the
same rule as `set_transport`/`set_enabled`, so **the sequencer is stopped, not
muted** — no interleaved pitch bends, no stale gate timers, no note-off stealing.
Leaving `live` starts it on the **next bar line** (a one-shot wait, then
`start()`), so the phrase re-enters at step 0 on a downbeat instead of
mid-bar. This is simpler and safer than reference-counting notes across two
senders, and it matches how the rig already starts/stops the scheduler on
transport and enable changes (`lead.py:1897-1907`, `1934-1944`).

An **overlay** mode (sequencer keeps playing, live notes steal and then hand
back) is possible later: it needs a single note arbiter with priority, a
"sequencer resumes at the next step" rule, and a shared gate token. It is
deliberately out of scope for v1 because it multiplies the stuck-note surface.

**Stop paths** (each must send note-off + CC 123 exactly once): `Live` off,
source change, transport stop, `Lead on` off, input port lost, bridge stop, GUI
close. The existing `LeadScheduler.stop()` already does note-off + CC 123; the
input rig must have the equivalent.

---

## 8. Persistence (C4)

### 8.1 Melody library (`control/melody.py`)

Mirror `control/state.py` because it is the accepted house pattern:

```python
DEFAULT_MELODY_DIR = PROJECT_ROOT / "melodies"
MELODY_FORMAT = "ji-lead-melody"
MELODY_VERSION = 1

class MelodyLibrary:          # names / save / load / delete / ensure / path_for
    ...
```

* names: the same rules as states (1–40 chars, first alphanumeric, then
  `A-Z a-z 0-9 space . _ -`, no `/ \ ..`, no leading dot, case-only duplicates
  refused);
* atomic write `<name>.json.tmp` → flush → fsync → `os.replace`;
* `--melody-dir` overrides (relative resolves from `ji-live/`);
* never writes into `config/`; a read-only folder is one clear error line.

### 8.2 File format

```json
{
  "format": "ji-lead-melody",
  "version": 1,
  "name": "intro line",
  "saved_at": "2026-11-14T14:02:11",
  "source": "played",
  "grid": "1/8",
  "phrase_bars": 2,
  "meter_num": 4,
  "meter_den": 4,
  "bpm": 120.0,
  "quantize": 1.0,
  "swing": 0.0,
  "captured_from_beat": 8.0,
  "set": { "fundamental": 220.0, "anchor": 1.0, "ratios": [1.0, 1.125, 1.25, 1.3333, 1.5, 1.6667, 1.875] },
  "variation": { "name": "identity", "index": 1, "history": [["invert", 1]] },
  "base_notes": [
    { "step": 0, "length": 1, "degree": 2, "octave": 0, "ratio_hint": 1.25, "velocity": 104 }
  ],
  "notes": [
    { "step": 0, "length": 1, "degree": 2, "octave": 0, "ratio_hint": 1.25, "velocity": 104 }
  ]
}
```

`base_notes` = the line as recorded (`Revert`); `notes` = the line as it was when
saved (so a reload is exact even if the variation code changes). The file is
small (16 notes ≈ 1.5 kB). `set` is provenance, not a requirement: the melody is
set-relative and loads against the **current** set.

### 8.3 Performance states (self-contained — owner decision)

A state carries the lead's **notes inline**. The point of a saved state is the
minimum structure to start improvising from, and the lead line is part of that
structure. The melody library (§8.1) remains the way to keep and reload a melody
by name; a state does not depend on it.

```json
"lead": { "…": "every LeadSettings field", "variation_index": 3,
          "notes": [ … ],
          "base_notes": [ … ],
          "history": [["invert", 1], ["rhythm", 2]],
          "melody": "intro line", "source": "played" }
```

* `notes` / `base_notes` use the same six-field shape as the melody file.
* On load: validate every note through the existing `LeadNote` rules, restore
  the settings, then `load_motif()` with source, history and name. **Revert works
  immediately after a recall** because `base_notes` travelled with the state.
* Optional keys: a pre-9.1 state has none and loads as before
  (`source="generated"`), which is the 8.5 versioning policy
  (`docs/README_stage8_5.md:313-315`).
* Cost: 16 notes ≈ 1.5 kB per state; a state file stays a few kB.

### 8.4 MIDI files

**Out of scope (owner decision, 2026-11-14).** No `.mid` import or export. A
`.mid` cannot carry JI pitches without a per-note bend track, so it would be a
lossy second format for no gain; the JSON melody/state is the format.

---

## 9. Surface: OSC, config, flags, GUI

### 9.1 New OSC addresses (all additive)

```text
/ji/lead/input/set_enabled      int 0/1          (live monitor)
/ji/lead/input/set_quantize     float 0..1
/ji/lead/input/set_count_in     string off|1 beat|1 bar|2 bars
/ji/lead/input/set_bars         int 1..8
/ji/lead/input/note_on          int midi, int velocity        (QWERTY / external)
/ji/lead/input/note_off         int midi
/ji/lead/input/record           none   (start; respects the count-in)
/ji/lead/input/stop             none   (finish now, quantize, keep the take)
/ji/lead/input/revert           none   (back to the recorded base)
/ji/lead/melody/save            string name
/ji/lead/melody/load            string name
/ji/lead/melody/delete          string name
/ji/lead/melody/request         none

bridge -> UI:
/ji/lead/input/state    [live, port_ok, port_name, quantize, count_in,
                         bars, recording, count_beats, note_count, source,
                         melody_name, last_in_note, last_out_note, last_cents]
/ji/lead/melody/list    [count, name_1 .. name_N]
/ji/lead/melody/state   [count, last_saved, last_loaded, message]
```

The QWERTY `Keys` toggle is GUI-local: it starts/stops sending `note_on|off`
(and sends note-offs for held keys when switched off), so it needs no bridge
state and cannot desync.

Rules copied from the rig: validate in `osc_server` **and** in the engine; one
small state message per change, never per note. The final dispatch is:
`revert`, `clear` and a melody **load** are queued on `PHASE_LEAD` while the
transport runs and immediate when stopped (they replace the sounding line);
`set_enabled` (Live), `record`, `stop` and `refresh` are immediate start/stop
gestures; `note_on/off` are never queued; save/delete/list/request are immediate
file operations.

`/ji/lead/state` keeps its first 16 values in order and appends `gate_mode` as
value 17. `parse_lead_state` accepts 16 or 17 values so a stale GUI cannot
wedge.

An additional `/ji/lead/input/refresh` (none) closes/re-opens the port from the
GUI `Refresh` button.

### 9.2 Config

`config/lead_input_config.json` (new, read-only input like every other config):

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

`port` + `match: contains|exact` is the friendly part: controller names are long
and platform-specific; a substring is enough and a missing port disables only
the input with one `[LEAD-IN]` line.

### 9.3 Launcher flags

```text
--lead-in                 enable the MIDI input (off by default, like --lead)
--lead-in-port <name>     controller name/substring (overrides the config)
--lead-in-config <path>   alternative config
--melody-dir <path>       melody library folder (default melodies/)
```

`--lead` stays required (no Bus 5 → no input); `--lead-in` without `--lead`
prints one line and is ignored. `--check` prints
`mido.get_input_names()` beside the output names and verifies the configured
input name, exactly like the existing output check (`run/ji_station.py:549-566`).

### 9.4 GUI

A new **Lead input** group under **Lead** (two compact rows + status), so the
Lead group does not grow:

```text
Lead input
Port: Launchkey 49 [Refresh] | ☐ Keys | ☐ Live | Rec | Stop | Revert | Clear
Quant [────100%] | Count-in [1 bar ▾] | Bars [2] | Melody [intro line ▾] Name [____] Save Load Del
Input: Launchkey 49 - idle - 13 notes | Melody: intro line (13 notes) - played
```

* `Keys` is the QWERTY toggle; `Port:` is a **read-only label** fed by
  `input/state.port_name` (the bridge is the authority on ports; the GUI never
  enumerates devices). A missing controller is fixed in
  `config/lead_input_config.json`/`--lead-in-port` and then `Refresh`.
* Knobs coalesce at ~30 Hz (existing `Knob`), buttons send immediately.
* Incoming `/ji/lead/input/state` syncs with signals blocked (no feedback OSC),
  same as the Lead group (`ui/ji_ui.py:3705-3734`).
* The group is disabled when `port_ok = 0`, with the reason in the status line;
  `Keys` stays usable even with no controller.
* Window grows one group (~60 px) → `1280×1258`; update `resize()` and the
  `tests/test_ui.py` height bound. The scroll area stays the small-screen net.

---

## 10. Resolved decisions (2026-11-14)

| # | Question | Decision | Where it lands |
|---|---|---|---|
| 1 | Map mode | **`nearest` only** | §5.1; `cycle`/`free`/`anchor` remain reserved, no UI |
| 2 | Live play with the transport stopped | **yes** (monitor only; recording needs Play) | §7 |
| 3 | Sequencer during live play | **paused while input is incoming** | §7 — the `Live` toggle stops the scheduler; it resumes on the next bar line |
| 4 | Variations on a played line | **destructive + `Revert`** — aleatoric composition is part of the project, and revert covers the rest | §4.3 |
| 5 | State integration | **self-contained: notes inline** ("minimum structure to start from") | §8.3 |
| 6 | MIDI files / QWERTY | **no `.mid` import or export; QWERTY in scope and togglable** | §3.4, §8.4 |
| 7 | Polyphony | v1 monophonic, last-note priority, chords reduced | §6.1 |
| 8 | Pitch identity through mutation | **follow the set** via `degree`/`ratio_hint` | §4.2 |
| 9 | "the keyboard" | a MIDI keyboard **and** the QWERTY keyboard | §3.4 |

One reading of #3 worth stating: the `Live` toggle is the manual control, and a
small optional *auto-duck* (the first incoming note pauses the sequencer; after
~1 bar of silence it resumes at the next bar line) can be added later without new
state — it is just the same `set_source` call from the input callback.

---

## 11. Phased implementation plan

Each phase is independently shippable and testable; nothing in a phase changes
the accepted behaviour of the generated lead.

### Phase 1 — MIDI in, map, live monitor, QWERTY (~1.5 days, ~450 LOC + tests)

| File | Change |
|---|---|
| `control/lead_input.py` | new: `LeadInputSettings`, `MappedNote`, `map_note()`, the QWERTY key map, `MidiInputPort` (lazy mido, fake for tests), `LeadInputRig` (note ownership, sustain, stop) |
| `control/lead.py` | `LeadRig.set_source()` / `source` property; stop the scheduler in `live`, restart on the next bar line |
| `control/ji_bridge.py` | construct the rig with `--lead-in`, wire `/ji/lead/input/*`, `/ji/lead/input/state`, log lines |
| `control/osc_server.py` | handlers + validators (pure), including `note_on`/`note_off` |
| `ui/osc_control.py` | constants, builders, parser, `LeadInputStatus` in the store |
| `ui/ji_ui.py` | the Lead input row: Input combo, Keys toggle, Live; key-event handler |
| `run/ji_station.py`, `config/lead_input_config.json` | flags + config |
| tests | `tests/test_lead_input.py` (pure mapping: identity, cents, octaves, edge notes; key map; fake port), `test_ui.py --pure`, `test_startup.py` |

**Acceptance:** keys sound on Bus 5 at the nearest class, in the played register;
QWERTY and the controller both reach the same mapper; `Live` pauses the sequencer
and resumes it on a bar line; a missing port disables only the input; the
generated lead is bit-identical when the input is off.

### Phase 2 — record, quantize, phrase (~2 days, ~500 LOC + tests)

| File | Change |
|---|---|
| `control/lead_input.py` | `LeadRecorder` (pure: onsets/offsets in beats, reduction, finish) + the quantizer |
| `control/lead.py` | `LeadMotif.source/base_notes/history`; `LeadEngine.load_motif()`, `revert()`; source-aware regeneration policy; `gate_mode` |
| `control/ji_bridge.py` | record state machine (count-in, window, auto-stop on the beat), `PHASE_LEAD` queueing, auto-play after finish |
| `ui/*` | Rec/Stop/Revert, Quant, Count-in, Bars, status |
| tests | quantize cases (straight/swung, strength 0/50/100, triplets, 3/4 and 7/8 bars), reduction, held-note window edge, engine policy, mutations on a played phrase |

**Acceptance:** a 2-bar take is quantized, played back on the grid with swing,
survives `Mutate` with its contour, and all eight variations transform it
(`random` replaced by `Revert`).

### Phase 3 — melody library + self-contained state (~1 day, ~350 LOC + tests)

`control/melody.py` (`Melody`, `MelodyLibrary`), `/ji/lead/melody/*`, Save/Load/
Delete/Refresh in the GUI, `--melody-dir`, and the inline `lead.notes` /
`base_notes` / `history` in performance states (§8.3).

**Acceptance:** save → restart the bridge → Refresh → Load reproduces the line;
save a state with a played line, load it in a fresh bridge and `Revert` restores
the recorded base; a pre-9.1 state loads unchanged; names obey the state rules;
nothing is written into `config/`.

### Phase 4 — polish (~1–1.5 days)

`snap to drums` quantization; auto-duck (pause on the first incoming note, resume
after a bar of silence); sub-step `offset` for captured feel; sustain and
pitch-bend expression; a "nearest-class cents" readout in the status line;
`top`/`first` chord reduction.

---

## 12. Edge cases and failure modes (design for these from day one)

| Case | Behaviour |
|---|---|
| Note-on with velocity 0 | treated as note-off |
| Same key retriggered before off | close the old note at the new onset |
| Chord | reduce (`last` default) + one log line |
| Note held across the record end | closed at the window end |
| Transport stops mid-record | finish + quantize, no auto-play, one line |
| Count-in active, player plays | ignored (and not sounded) until the window opens |
| Input port unplugged | `[LEAD-IN] input lost: …`; the rest of the rig runs; Refresh re-opens |
| Controller also routed to Ableton | doubled notes; documented: disable that input in Live's MIDI prefs |
| Bus 5 missing | input disabled only (same rule as the lead) |
| Melody file missing on state load | generated line continues, one `[STATE]` line |
| Read-only melody folder | one `io` error line; nothing else changes |
| Stale `mido`/GUI | input is bridge-side; a GUI restart never drops notes |
| 1/16T? | not offered; the four lead grids are the vocabulary (`GRIDS`) |

---

## 13. Regression traps (the "do not break" list)

1. **Generated lead must stay byte-identical** when `--lead-in` is off and no
   melody is loaded: same seed → same motif, same messages. Pin it with a test
   that compares the scheduler stream before/after the feature.
2. **`/ji/lead/state` field order is frozen** (16 values). Append only.
3. **`PHASE_LEAD` order is frozen**; a melody load is one action on that phase.
4. **No mido import at module import time** (`tests/test_lead.py:919`,
   `tests/test_startup.py`). Lazy import in `MidiInputPort.open()`, fake port in
   tests.
5. **`on_set()` must not regenerate** — a played phrase depends on it.
6. **Swing stays one definition** (`bass.swing_offset`), never forked; store
   unswung steps.
7. **Breath stays a playback offset**; never in the recorded timing.
8. **Docs and `--help`**: `docs/START_HERE.md` must document every new flag or
   `tests/test_startup.py` fails; update `docs/GUI_PARAMETERS.md`, the design doc
   and add `docs/README_stage9_1.md` per the maintenance rule.
9. **Window height** test bound (`tests/test_ui.py`), `ui/ji_ui.py:1045`.
10. **Stop paths**: every exit sends note-off + CC 123; test with a fake output
    and assert no note is left on.

---

## 14. Worked example (so the maths is checkable)

Bridge at 120 BPM, 4/4, lead `Grid 1/8`, `Gate` irrelevant, set = just major
`1, 9/8, 5/4, 4/3, 3/2, 5/3, 15/8`, `F = 220 Hz`, `base_octave 0`, swing 0.
Record starts on the next bar: beat **4.0** (2.00 s).

Played (seconds after the bar line):

| Played key | MIDI | Hz | Octave fold | Nearest class | Cents | Recorded `degree/octave` | Sounds |
|---|---|---|---|---|---|---|---|
| C#4 | 61 | 277.18 | 0 | 5/4 = 275.00 | +13.7 | 2 / 0 | 275.00 |
| E4 | 64 | 329.63 | 0 | 3/2 = 330.00 | −1.9 | 4 / 0 | 330.00 |
| A4 | 69 | 440.00 | +1 | 1/1 = 220.00 ×2 | 0.0 | 0 / 1 | 440.00 |
| F#4 | 66 | 369.99 | 0 | 5/3 = 366.67 | +15.6 | 5 / 0 | 366.67 |

Timing (grid step = 0.5 quarter beats = 0.25 s; `s = (beat − 4.0)/0.5`):

| Played at | Rel. beats | `s` | Step | Off at | Off step | Length |
|---|---|---|---|---|---|---|
| 2.01 s | 0.02 | 0.04 | 0 | 2.24 s | 0.96 → 1 | 1 |
| 2.26 s | 0.52 | 1.04 | 1 | 2.50 s | 2.00 → 2 | 1 |
| 2.54 s | 1.08 | 2.16 | 2 | 3.02 s | 4.08 → 4 | 2 |
| 3.03 s | 2.06 | 4.12 | 4 | 3.24 s | 4.96 → 5 | 1 |

Recorded line: `(0,1,2,0), (1,1,4,0), (2,2,0,1), (4,1,5,0)`. Playback through
`_project_locked` gives 275.00, 330.00, 440.00, 366.67 Hz. Press **Mutate**: a
note keeps its pitch while 5/4, 3/2, 1/1 or 5/3 survives; only a removed class
falls back to its degree. Press **Vary**: `invert` mirrors degrees around 2,
`retrograde` keeps the rhythm and reverses the pitches, `rhythm` redraws the
onsets.

---

## 15. Effort summary

| Phase | Files touched | New/changed LOC | Tests |
|---|---|---|---|
| 1 — input + map + live + QWERTY | 7 | ~450 | pure mapping, key map, fake port, UI pure |
| 2 — record + quantize + phrase | 6 | ~500 | quantize matrix, engine policy |
| 3 — library + self-contained state | 5 | ~350 | library round-trip, state recall/Revert |
| 4 — polish | 5 | ~200 | groove snap, auto-duck |

Total ≈ **1,500 LOC and ~5–6 working days** with the project's usual test and doc
overhead. There is no sclang, SynthDef, audio-bus or `config/` write in any
phase.

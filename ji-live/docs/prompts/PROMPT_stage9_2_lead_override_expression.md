# Stage 9.2 Agent Prompt — Lead override, audible count-in, and pitch-bend / mod-wheel expression

You are implementing **Stage 9.2** of the JI live-performance system. Stages
0–8.5 are owner-accepted. Stages 8.6–9.1 are built; **9.1 is owner-confirmed
functional (2026-11-14)** and its documented behaviour is the baseline. Treat
every doc as authoritative for what the code does today and do not regress it.

This stage makes the lead feel like one continuous instrument instead of a
mode switch, and adds the player's hand movement to it:

1. **The count-in is audible** — the input keeps sounding through a beat
   count-in.
2. **Input overrides the loop, live** — no `Live` toggle: a played key takes
   the lead immediately, the sequencer's sounding note is released, and the
   loop's attack resumes on release.
3. **Pitch bend and mod wheel are captured and output** on the lead, on top of
   the nearest-class note chosen at key-down.
4. **That movement is recorded with the take** and played back as part of the
   played phrase (this is feasible; it is in scope for this stage).

The owner is the authority on the feel. The agent environment has **no MIDI
hardware, no IAC, no Ableton and no audio**; the evidence is the pure Python
harness plus honest reporting. **Do not claim a human listening pass.**

**Numbering note:** 9.2 is the chronological increment after 9.1. It is a
lead/performance increment, not a granular/glitch FX one; list it as such in
the design doc's §8.9/§8.10 and §9 tables rather than forcing it into the FX
section.

---

## 0. What the owner said (verbatim, 2026-11-14)

> "in a beat count-in to recording i want to still be able to play even if it is
> recorded. also i want the following: i dont want to have to toggle between
> live and playing, if i hit a midi note, it should override whatever is playing
> in the lead (not permanently, but the playing loop should not be played and the
> midi in should be, and then immediately return to the loop after the note is no
> longer being played. if a note is being played in the loop, the release should
> trigger the attack of the note). also consider if the following is feasible:
> capturing moving pitch bend and mod wheel and outputting that (the nearest
> pitch should be given by the note from the input, and then pitch bend would be
> added to that). ideally this movement can also somehow be recorded in the loop
> and played back. if that seems realistic to implement then also i want this in
> the plan."

The owner also approved 9.1 as functional after running the real controller
(2026-11-14). The QWERTY `key_midi` import fix from that pass is part of the
baseline; keep it.

This prompt is the design record for 9.2. If the owner later changes a
decision, record it as a dated follow-up in the stage README rather than
rewriting history.

---

## 1. Read these files first

Read the code and docs, not just the summaries.

```text
docs/README_stage9_1.md              the accepted baseline this stage extends
docs/README_stage8_3d.md              the lead voice + variations contract
docs/README_stage8_2.md               groove/swing + the next-quarter-beat queue
docs/README_stage8.md                 rhythm/transport, BeatClock
docs/README_stage8_5.md               performance-state format + versioning
docs/README_stage8_7.md               breath: playback-only, never baked in
docs/START_HERE.md                    authoritative start/stop/flag sheet (update)
docs/GUI_PARAMETERS.md                every control, one line each (update)
docs/README_mpe.md                    MPE bus/channel/bend conventions
docs/README_live_rig.md               audio routing (no new bus)
docs/design_doc_10.28.10.50           §5, §6.11, §6.18, §6.24, §7.1, §7.5,
                                      §8.9, §9, §13, §14
control/lead_input.py                 mapper, port, recorder, quantiser, rig
control/lead.py                       LeadNote/Motif/Engine/Scheduler/Rig
control/mpe_output.py                 frequency_to_pitch, MPEOutput, control_change
control/midi_rig.py                   output-only MIDI rig (do not add input)
control/rhythm.py                     RhythmState/BeatClock/next_beat_time
control/bass.py                       swing_offset (the one swing definition)
control/state.py                      PerformanceState + inline lead notes
control/melody.py                     melody file + library
control/ji_bridge.py                  input handlers, source switch, state recall
control/osc_server.py                 handlers + validators (pure)
ui/osc_control.py                     pure builders/parsers/store
ui/ji_ui.py                           one window, Lead + Lead input groups
run/ji_station.py                     one launcher (flags, --check, --dry-run)
config/lead_config.json               lead output defaults
config/lead_input_config.json         input defaults
tests/test_lead_input.py              Stage 9.1 harness and fake idioms
tests/test_lead.py                    lead suite + fake output/clock idioms
tests/test_state.py                   state round-trip/validation idioms
tests/test_ui.py                      pure + optional Qt harness
tests/test_startup.py                 launcher/start-file/docs checks
docs/prompts/PROMPT_stage9_1_lead_input.md   prompt-format precedent
```

Run Python from `ji-live/`. The uv-managed Python lives at `~/.ji-tools/uv`
even when `uv` is not on `PATH`:

```bash
~/.ji-tools/uv run --with python-osc python tests/test_lead_input.py
```

`mido`, `python-rtmidi`, PyQt6, sclang/scsynth, IAC and Ableton are **not**
required for the pure harness. Never import `mido` at module import time.

---

## 2. Current state you must preserve

- **9.1 accepted behaviour.** Nearest-class mapping, base-octave-correct
  `degree`/`octave`, `LeadRecorder` with last-note chord reduction, bar-aligned
  count-in/window, swing-aware quantisation storing **unswung** steps,
  `melodies/<name>.json`, inline `lead.notes` states, played motif +
  `history` + `Revert`, `gate_mode`, and the Lead input GUI group.
- **`/ji/lead/state` is 17 values.** The first 16 are frozen; 17 is
  `gate_mode`. Do not reorder.
- **`/ji/lead/input/state` is 14 values.** This stage appends two values (bend
  and mod) as 15/16 and relaxes `parse_lead_input_state` to accept 14 or 16.
  Never reorder the first 14.
- **Generated-line output is byte-identical** when the input is silent and no
  played take is loaded: same seed → same motif → same MIDI stream. Notes with
  no expression points add no CC 1 and no extra bend; only an override resume
  adds the mod reset that the input made necessary.
- **`on_set()` only re-projects.** No regeneration in this stage.
- **One source switch / one scheduler rule.** `LeadRig.set_source` and
  `_sync_scheduler` remain the only place that starts/stops the sequencer.
- **The one swing definition**: `bass.swing_offset(step, swing, step_seconds,
  steps_per_beat)`. Never fork it.
- **Breath is playback-only**: its offset is added to the scheduler's sleep
  target, never to the grid or to stored notes.
- **State format**: `states/<name>.json`, `format`/`version: 1`, atomic
  `.tmp`+fsync+`os.replace`, names 1–40 chars, never writes `config/`. New
  keys must be optional with defaults; absent keys fall back to old behaviour.
- **Melody format**: `ji-lead-melody`, `version: 1`, optional new note keys,
  atomic, names via `control.state.validate_state_name`.
- **No sclang change at all.** No `MIDIIn`, no `MIDIClient.init`, no new
  SynthDef, no new bus, no new OSC address to port 57120.
- **No `mido` import at module import time.**
- **The QWERTY baseline**: `ui/ji_ui.py` imports `key_midi` from
  `control.lead_input` after resolving the project root; do not remove that
  import or the pure regression check for it.

### Do not change

- `/ji/freqset/update` and its payload; `synthdefs/*`; `startup.scd` audio
  behaviour; `tests/sc/test_live_rig.scd` DSP; the four-channel audio map;
  loops (still sclang-owned, session-only, excluded from states).
- `control/mutation.py`, `control/home.py`, `control/glide.py`,
  `control/groove.py` semantics, `control/breath.py` semantics,
  `control/rhythm.py` semantics.
- The `PHASE_*` ordering.
- One window, no tab/dialog; the scroll area stays the small-screen net.
- Existing harnesses pass unmodified except the intended additions in
  `tests/test_lead_input.py`, `tests/test_lead.py`, `tests/test_state.py`,
  `tests/test_ui.py` and `tests/test_startup.py`.

---

## 3. What this stage adds

```text
MIDI key ─> LeadInputRig ─> LeadScheduler.override() ─> sequencer note released
                │                                       sequencer output muted
                └─> MPE note (nearest class + bend) ──> Bus 5
key up   ─> LeadInputRig ─> LeadScheduler.release_override()
                              └─> retrigger the loop's current note (attack)
                                  or resume at the next step

pitch wheel / CC 1 ─> nearest-class note + bend / mod ─> Bus 5
                         └─> recorded as (offset_beats, value) points on the take
                              └─> LeadScheduler replays the points with the phrase
```

Four capabilities:

- **C1 override** — the input always takes precedence over the sequencer while
  a key is held; the loop resumes immediately on release.
- **C2 audible count-in** — the input sounds through the count-in; the take
  still starts on its bar line.
- **C3 expression** — pitch bend and mod wheel modify the lead in real time.
- **C4 recorded expression** — the movement is stored with each note and played
  back by the lead scheduler, including from melody files and states.

**Feasibility verdict (C3/C4):** yes. The MPE output already exposes 14-bit
pitch bend and `control_change` on the member channel, so this is Python-only
and needs no sclang/audio change. The cost is a bounded expression player and
two optional note fields; no new bus or file format version.

**Out of scope:** `.mid` import/export; polyphonic/MPE multi-channel input;
mapping modes other than `nearest`; aftertouch, channel pressure, sustain-pedal
*level* (CC 64 stays a switch); recording count-in notes as a pickup (see
§4.2); sub-step timing of note **onsets** (expression offsets are sub-step, but
onsets stay quantised); auto-duck of other voices; any sclang/audio change.

---

## 4. Part A — input overrides the loop (C1)

### 4.1 Remove the Live/playing split (owner decision)

The owner does not want to toggle between live and playing. Implement this
model:

| `source` | Sequencer | Input |
|---|---|---|
| `generated` | runs | **overrides** |
| `played` | runs the played motif | **overrides** |
| `record` | stopped (unchanged) | sounds and records |

- **Remove the `Live` checkbox from the GUI.** The input monitor needs no
  mode; it is active whenever the input port is open and the lead is enabled.
- Keep `/ji/lead/input/set_enabled` accepted for compatibility (a stale GUI),
  but it now means only **input monitor on/off** (default on), not a source:
  it must never start/stop the sequencer. Log one line when it changes and
  send `input/state`.
- In `/ji/lead/input/state`, the first value keeps the name `live` and now
  means **input monitor active** (`port_ok` and the compatibility flag on). It
  is no longer a source and is normally 1; the GUI has no control for it.
- `_sync_scheduler` must run the sequencer for `generated`/`played` and stop it
  for `record` (plus the existing transport/enabled gates). `live` never
  reaches `_sync_scheduler`; it is mapped at the alias boundary.
- Keep `source = "live"` accepted by `validate_source`/old states as a
  **deprecated alias**: map it to `played` when a played motif exists, else
  `generated`, log
  `[LEAD] source=live is deprecated; input always overrides`, and never gate
  the input on it.
- `LeadRig.playing` must be true only for `generated`/`played` with a running
  scheduler (the recording rule is unchanged).
- `LeadInputRig` note entry is gated by `port_ok`, `lead.enabled` and the input
  monitor flag — **not** by `source`. `record` is not special for sounding
  (the sequencer is already stopped).
- The GUI/status line keeps showing the source on the melody line
  (`played`/`generated`/`record`); there is no `live` source to show.

### 4.2 The count-in is audible (C2)

- Remove the `count_in` silence guard in `LeadInputRig.note_on`: keys sound
  during the count-in exactly as during recording.
- The **take still starts on its bar line**. The recorder captures only in the
  `recording` state, so notes that begin and end during the count-in are not
  stored. To keep a phrase that begins during the count-in, the note must still
  be held at the bar line; at `_begin_recording`, seed the recorder with every
  currently held key at `start_beat` so it is stored from step 0.
- Track the held velocity so the seeded note keeps the played velocity.
- **Remove the 9.1 `_release_held()` call at record start.** It was there to
  silence the pre-roll; with an audible count-in a held note must survive into
  the take. `LeadRig.set_source("record")` independently stops the sequencer,
  so no input note-off is needed. The note is released only by the normal exit
  paths (Stop, Live-equivalent disable, source change on a state recall, lead
  off, port loss).
- **Decision recorded here:** the count-in is a monitoring pre-roll, not a
  pickup bar. Storing count-in-only notes would need negative steps or an
  extra bar; do not invent that. If the owner wants a pickup take, it is a
  separate decision.
- A transport stop during the count-in still cancels it silently (9.1 rule).
- The count-in remains silent on **Bus 5's sequencer** because `record` stops
  the scheduler; the audible sound is the input.

### 4.3 Scheduler override API (the heart of C1)

Add to `LeadScheduler`:

```python
def override(self) -> None: ...
def release_override(self) -> None: ...
@property
def overridden(self) -> bool: ...
```

Exact semantics:

- `override()` is called by `LeadInputRig` **before** it sends the input
  note-on. Under the scheduler lock:
  - set `_overridden = True`;
  - cancel any pending gate timer;
  - `note_off` the sequencer's held note (`_held`);
  - cancel any pending expression playback (Part D);
  - never stop the scheduler thread, never advance/reset its step.
- While `_overridden` is true, `_run` still advances the clock and the step
  index but **does not call `_play`**. There must be no sequencer note-on, gate
  timer, bend, mod or expression message while overridden.
- `release_override()` under the same lock:
  - clear `_overridden`;
  - if the scheduler is not running or `_stop` is set: do nothing else;
  - if the last played step's note is still inside its nominal gate window
    (`now - _step_started_at < _step_gate_seconds`), immediately attack it
    again via `_play` (fresh bend + mod reset + note-on + a fresh gate from
    now). This is the owner's "the release should trigger the attack of the
    note";
  - if that window has passed, or the last step is a rest, stay silent until
    the next scheduled step;
  - do not let the scheduler thread double-trigger the same step: use a
    last-played token (`_played_total` / a generation counter) checked by both
    paths.
- Track `_step_started_at` and `_step_gate_seconds` in `_play` for the
  eligibility test. They are cleared on stop/override.
- `stop()` clears `_overridden` after note-off + CC 123.
- A transport stop while overriding: the scheduler stops (notes off); the
  input may keep sounding (transport-stop monitoring is allowed); override is
  cleared. A later Play starts the phrase at step 0 as today.
- A source change, `Lead on` off, bridge stop, port loss, or `record` start
  must release the override (`LeadRig`/bridge call `release_override()` and
  the input releases its note). No stuck override.
- `LeadInputRig` keeps the input monophonic: on the **first** note-on when no
  key/pedal was held, call `lead.scheduler.override()` (or a small
  `LeadRig.override_from_input()` wrapper); on the **last** key-up when the
  sustain pedal is up, call `release_override()`. A retrigger while already
  overridden must not call `override()` again (it would re-send note-offs, but
  more importantly it must not clear the resume bookkeeping).
- Sustain (CC 64) keeps the override alive until pedal-up, matching the input
  note. A new key while sustained retriggers the input note and stays
  overridden.

### 4.4 Ordering and ownership rules

- No interleaved bends: Bus 5 is one member channel. `override()` runs
  under the scheduler lock and finishes the sequencer's note before the input
  sends its bend. `release_override()` finishes the input's note (the input
  rig sends its note-off first) before it attacks the loop note.
- The input rig sends its note-off, then calls `release_override()`.
- If the loop's last step has no note at release, the release is still
  instantaneous; the next loop step attacks normally.
- A held input note across a source/transport change is released by the
  existing exit paths (note-off + CC 123).

---

## 5. Part B — pitch bend and mod wheel (C3)

### 5.1 Input translation

Extend `MidiInputPort` (still lazy, no mido at import time):

```text
pitchwheel                      -> ("bend", value, 0)      value -8192..8191
control_change 1 (mod wheel)    -> ("mod",  value, 0)      value 0..127
control_change 64 (sustain)     -> ("sustain", 64, value)  unchanged
note_on / note_off              -> unchanged
```

- Apply the existing channel filter to pitchwheel and CC 1.
- Ignore pitch bend/CC on other channels, aftertouch, pressure, clock.
- `LeadInputRig._on_message` dispatches `bend`/`mod` to new methods
  `bend(value)` / `mod(value)`.

### 5.2 Bend maths (exact)

The class is chosen **once at note-on**; bend moves the pitch from there.

```text
range   = settings.bend_range_semitones          # controller range, default 2
cents   = (pitchwheel / 8192) * range * 100      # signed
f_bent  = mapped.frequency * 2 ** (cents / 1200)
```

To output `f_bent` without changing the MIDI note that is sounding, compute the
14-bit bend **relative to the note number sent at note-on**:

```text
N       = pitched.note                            # kept for the whole input note
semis   = 12 * log2(f_bent / midi_note_frequency(N))
bend14  = round(semis / lead_config.bend_range_semitones * 8192)
bend14  = clamp(bend14, -8192, 8191)
```

This matters: do **not** call `frequency_to_pitch(f_bent)` and send just its
`.bend` while the receiver still holds `N` — a large bend could cross a
semitone and produce the wrong pitch. `frequency_to_pitch` is only used once at
note-on to choose `N` and the base bend. (The helper may be factored into a
small pure function `bend_for_frequency(note_number, target_hz,
mpe_range)`; test it.)

- A key exactly on a class with pitchwheel 0 gives the 9.1 output (bit-exact
  when the class float is used).
- On a bend event with a held note: send `pitch_bend(...)` only; no note-on,
  no gate change, no recorder note event.
- With no held note: store the wheel value for the next note-on; send nothing.
- On note-on: store the mapped base, choose `N`, send the **base+bend** bend,
  then the current mod CC, then note-on. (Mod before note-on so the instrument
  starts with it; bend before note-on as today.)
- On release: keep the physical wheel values in the rig; the next note uses
  them. The sequencer's own note-on always sends its own bend and mod reset.

### 5.3 Mod wheel output

- Forward CC 1 on the lead member channel: `output.control_change(1, value)`.
- Forward every CC 1 change (throttled to ~5 ms, duplicates coalesced); CC 1
  is channel state and shapes whatever is sounding, input or loop. Send the
  current value once before each input note-on so the note starts with it.
- **No `mod_cc` option** in this stage: CC 1 is the literal mod wheel. If an
  instrument expects the MPE timbre CC 74, the owner remaps it in Ableton; do
  not silently send both.
- Throttle: coalesce duplicate values; at most one message per ~5 ms per input
  event stream. Never log per event.

### 5.4 Config and status

- Add `bend_range_semitones` to `config/lead_input_config.json` (default
  `2.0`, valid `0.0–48.0`; 0 disables bend). Unknown keys still ignored; a bad
  value disables only the input.
- Extend `LeadInputRig.state_args()` with two values **after** the existing 14:

```text
15 last_bend_cents  float  current controller bend in cents (signed; 0 = centre)
16 last_mod         int    current mod wheel 0..127
```

- `/ji/lead/input/state` is now 16 values. `parse_lead_input_state` accepts
  **14 or 16**; a 14-value payload gives bend 0/mod 0.
- GUI status line:
  `Input: Launchkey 49 - idle - 13 notes - bend +12 ¢ - mod 64`
  (omit bend/mod when both are zero to keep it compact). No per-event GUI
  updates beyond the existing coalesced state sends.

---

## 6. Part C — record the movement with the take (C4)

### 6.1 Note data model (optional fields, appended)

```python
@dataclass(frozen=True, slots=True)
class LeadNote:
    step: int
    length: int
    degree: int
    octave: int
    ratio_hint: float
    velocity: int
    bend_points: tuple[tuple[float, float], ...] = ()
    mod_points: tuple[tuple[float, int], ...] = ()
```

- A point is `(offset_beats, cents)` or `(offset_beats, 0..127)`, where
  `offset_beats` is measured from the **raw note-on** in quarter-note beats.
  It is not quantised and not swung; the note's stored `step` is the quantised
  onset, and playback adds the offsets to that onset.
- Validation (in the shared note validator used by engine, melody and state):
  `offset` finite, `>= 0`, sorted ascending; cents finite, clamp to
  `±(48 * 100)`; mod 0..127; duplicate offsets last-wins; coalesce identical
  consecutive values; cap at **256 points per curve** (drop later points).
- Existing positional `LeadNote(step, length, degree, octave, ratio_hint,
  velocity)` construction stays valid; empty tuples are the default.
- JSON note mappings add:
  `"bend_points": [[0.0, -12.0], [0.3125, 0.0]]` and
  `"mod_points": [[0.0, 64]]`. They are always written (empty lists allowed)
  and default to `()` when absent, so old files and old states load unchanged.
  Format versions stay **1**.

### 6.2 Recorder capture

Extend `LeadRecorder` without breaking the 9.1 signature:

```python
def note_on(self, midi, velocity, beat, *, bend_cents=0.0, mod=0) -> None
def expression(self, beat, *, bend_cents=None, mod=None) -> None
```

- `note_on` stores the onset and seeds offset-0 points when `bend_cents != 0`
  or `mod != 0`.
- `expression` attaches a point to the currently captured note:
  `offset = beat - record["on"]`, clamped to `[0, record_duration]`;
  ignore if no note or if the note has already ended; coalesce equal
  consecutive values; cap 256 points.
- A retrigger starts a new expression capture; a chord reduction keeps only
  the last note and its points.
- The window/quantisation rules are unchanged: points after the note's off
  are dropped; a note held past the window ends at the window end.
- `finish()` returns `LeadNote`s carrying the points; the recorder resets as
  today (including expression state).
- The input rig calls `recorder.note_on(..., bend_cents=self._last_bend_cents,
  mod=self._mod)` on a captured note-on and `recorder.expression(...)` on each
  bend/mod change while `recording`.
- Count-in notes are not captured; a held note seeded at `_begin_recording`
  starts with its current bend/mod as offset 0.
- **Sub-step onset recording stays out of scope**; only expression offsets are
  sub-step.

### 6.3 Playback of expression

`LeadScheduler._play` starts the note's curve after the note-on:

- Send the note's base bend before note-on as today.
- Send CC 1 **only when it is needed**: when the note has `mod_points`, or when
  this is an override-resume attack (to clear the input's mod state). An
  ordinary generated note with no points sends no CC 1, so the generated MIDI
  stream is unchanged (the 9.1 acceptance rule).
- After note-on, apply each bend point by sending a pitch bend calculated
  **relative to the note number sent at note-on** (same helper as §5.2, target
  `note.frequency * 2 ** (cents / 1200)`). Apply each mod point with CC 1.
- Real time offset = `offset_beats * beat_seconds` at the moment the note is
  played. The curve follows the note's actual (swung/breath-adjusted) start
  because offsets are relative.
- Use **at most one** active expression player (a small daemon thread with a
  condition, a re-armed `threading.Timer` chain, or an equivalent); never one
  timer per point per note unbounded. Requirements:
  - no blocking of the scheduler thread or the mido reader thread;
  - cancel on the gate `note_off`, the next `_play`, `override()`,
    `release_override()` (before the resume attack), `stop()`, a source
    change and a lead disable;
  - a fresh attack (e.g. override resume) restarts the curve at offset 0;
  - points with the same value as the last sent one are skipped.
- The generated line has no points, so it emits nothing extra; only the
  existing bend-before-note-on remains.
- Breath may move the note start; expression is relative, so it follows.
- Swing is already in the note-on deadline; expression is relative, so it
  follows.
- Expression does not run while the scheduler is stopped/recording/overridden.

### 6.4 Melody and state round-trip

- `control/melody.py` and `control/state.py` note serialisation/parsing add the
  two keys; old files without them produce empty tuples.
- A melody save stores the take's points; load → `load_motif` → playback
  carries them.
- A performance state stores them inline in `lead.notes`/`base_notes`; recall
  and `Revert` restore them.
- The played motif's `Revert` target includes the points (they are part of the
  take); a variation transforms the notes as today and must not silently drop
  the points from the base. Decide and document how the variations carry
  points: **preserve the source note's points when a variation moves or
  repeats a note, and drop them when the variation generates a fresh note
  (`random`, an ornament insertion, a rhythm-step note with no source)**.
  Document this in the stage README.

---

## 7. Controls, OSC, config and GUI

### 7.1 OSC changes (all additive except the compatibility note)

| Address | Args | Direction |
|---|---|---|
| `/ji/lead/input/set_enabled` | int 0/1 | UI → bridge — **compatibility only**; toggles input monitoring, never the source |
| `/ji/lead/input/state` | **14 or 16** values (append `last_bend_cents`, `last_mod`) | Bridge → UI |

No other new address is required. Keep `/ji/lead/input/refresh`, the melody
addresses and the Lead set addresses unchanged. `revert`/`clear`/melody `load`
stay beat-queued; Live/record/stop/refresh and note/expression events stay
immediate.

### 7.2 GUI

- **Remove the `Live` checkbox and its handler.** Keep `Keys`, `Port`,
  `Refresh`, `Rec`, `Stop`, `Revert`, `Clear`, `Quant`, `Count-in`, `Bars`,
  the melody row and `Note len`.
- The `Keys` toggle remains GUI-local; keys sound because the input is always
  monitoring. It does not imply a source.
- `Rec` is enabled only while the transport runs and the port is open
  (unchanged).
- The status line shows the input state including bend/mod and the source:
  `Input: Launchkey 49 - idle - 13 notes - bend +12¢ - mod 64`
  and `Melody: intro line (13 notes) - played`.
- `Lead`/`Note len`/melody controls unchanged.
- The window may lose one checkbox; keep `1280x1258` and the Qt height bound
  at 1290, or shrink only if measured. The scroll area remains the net.
- No new tab/dialog/window. QWERTY must not fire while a text box/combo/spin
  has focus (9.1 rule).

### 7.3 Config

`config/lead_input_config.json` gains:

```json
{
  "port": "Launchkey", "match": "contains", "channel": 0,
  "quantize": 1.0, "count_in": "1 bar", "bars": 2,
  "velocity_scale": 1.0, "octave_shift": 0,
  "bend_range_semitones": 2.0
}
```

Validate 0.0–48.0. A bad value disables only the input. Document it in
`START_HERE.md`, `GUI_PARAMETERS.md` and the stage README. `--check` prints it
beside the other input defaults.

### 7.4 Launcher

No new flags. `--check` keeps verifying the input config and now prints
`bend=… st`. `--dry-run` is unchanged. `tests/test_startup.py` keeps passing.

---

## 8. Diagnostics

One prefix, `[LEAD-IN]`; never per event:

```text
[LEAD-IN] input=Launchkey 49 channel=0 match=contains quantize=1.00 count-in=1 bar bars=2
[LEAD-IN] notes sound as the nearest class of the current set (map=nearest)
[LEAD-IN] expression bend_range=2.00 st mod=CC1
[LEAD-IN] override on at step 7
[LEAD-IN] override off; loop note attacked at step 7
[LEAD-IN] override off; next loop note at step 8
[LEAD-IN] override cleared (scheduler stopped)
[LEAD-IN] recording started at beat 12 (window 12..20)
[LEAD-IN] recorded 13 notes / 2 bars (grid 1/8, quant 100 %, 3 bend curves, 2 mod curves)
```

The existing `[LEAD]` lines stay. No line per bend/CC event.

---

## 9. Tests

### 9.1 New `tests/test_lead_expression.py` (pure; no mido, no Qt, no sockets)

Follow the existing harness style (`def test_*`, `main()`, prints
`ALL ... PASSED`, exits nonzero on failure, adds the project root to
`sys.path`).

- **Bend maths**: `bend_for_frequency` exact at 0, ±half, full range; a class
  key with 0 wheel is bit-exact; clamping; controller range 0/2/48; a bend
  that crosses a semitone keeps the original note number and produces the
  expected 14-bit value.
- **Port**: pitchwheel and CC 1 translation with the channel filter; velocity-0
  note-off and sustain unchanged; pitchwheel on another channel ignored.
- **Input rig**: bend before note-on; mod before note-on; bend while held sends
  only pitch bend (no note-on); no note → state only; release keeps the wheel
  values for the next note; order is bend, mod, note-on.
- **Recorder**: `bend_points`/`mod_points` capture, offset 0 seeding,
  coalescing, cap 256, clamping to the note end, retrigger, chord reduction,
  empty take.
- **Note validation/serialisation**: round-trip through `LeadNote`, melody
  JSON and state JSON; old notes without the keys load with `()`; bad offsets
  (negative/unsorted/NaN), cents beyond range and mod 128 are rejected.
- **Scheduler expression**: with a fake output and a short note, the expected
  bend/mod messages appear in order; gate note-off cancels the rest; next note
  cancels the old curve; `stop()` cancels; no expression for a note with no
  points.
- **Override**: a fake output + real short scheduler; `override()` sends the
  loop note-off and suppresses the next steps; `release_override()` retriggers
  the current note when its gate is open, and does not when it has elapsed;
  no duplicate note-on for the same step; override is cleared on stop.
- **Count-in**: an input note during the count-in sounds but is not recorded;
  a key held at the bar line is seeded at step 0 with its velocity; count-in
  cancellation still silent.
- **No mido import**: after importing the modules, `"mido"` is not in
  `sys.modules`.

### 9.2 Additions to existing suites

- `tests/test_lead_input.py`: the count-in behaviour, override call order,
  state 16 values, 14/16 parsing, config bend range, GUI-source strings.
- `tests/test_lead.py`: scheduler `override`/`release_override` invariants,
  expression playback, generated stream unchanged with no points, gate/held
  cleanup.
- `tests/test_state.py`: bend/mod points inline round-trip, old state without
  them, bad point aborts the whole load.
- `tests/test_ui.py --pure`: 14/16 input parser, no `Live` widget constant in
  the window source, bend/mod status text, builders.
- `tests/test_ui.py --qt`: the Lead input group has no `Live` checkbox; its
  defaults still line up; an incoming 16-value state syncs silently; the
  status line shows bend/mod; the height bound; no extra window.
- `tests/test_startup.py`: no new flags required, but `--check` mentions the
  bend range and the docs still list every flag.

### 9.3 Regression (run from `ji-live/`; all except Qt are required)

```bash
~/.ji-tools/uv run --with python-osc python tests/test_mutation.py
~/.ji-tools/uv run --with python-osc python tests/test_mutation_controls.py
~/.ji-tools/uv run --with python-osc python tests/test_rhythm.py
~/.ji-tools/uv run --with python-osc python tests/test_breath.py
~/.ji-tools/uv run --with python-osc python tests/test_groove.py
~/.ji-tools/uv run --with python-osc python tests/test_quantize.py
~/.ji-tools/uv run --with python-osc python tests/test_bass.py
~/.ji-tools/uv run --with python-osc python tests/test_arp.py
~/.ji-tools/uv run --with python-osc python tests/test_lead.py
~/.ji-tools/uv run --with python-osc python tests/test_lead_input.py
~/.ji-tools/uv run --with python-osc python tests/test_lead_expression.py
~/.ji-tools/uv run --with python-osc python tests/test_drone.py
~/.ji-tools/uv run --with python-osc python tests/test_home.py
~/.ji-tools/uv run --with python-osc python tests/test_glide.py
~/.ji-tools/uv run --with python-osc python tests/test_state.py
~/.ji-tools/uv run --with python-osc python tests/test_startup.py
~/.ji-tools/uv run --with python-osc python tests/test_ui.py --pure
~/.ji-tools/uv run python tests/test_mpe.py --pure
~/.ji-tools/uv run python tests/test_fretboard.py
# if PyQt6 can run:
QT_QPA_PLATFORM=offscreen ~/.ji-tools/uv run --with python-osc --with PyQt6 \
    python tests/test_ui.py --qt
```

sclang tests are **not** required (no sclang change); if you run them, say so.

---

## 10. Human acceptance checklist (the owner is the authority)

1. **Count-in**: arm `Rec` with a one-bar count-in; play freely during the
   count-in and hear every note. The take still starts on the bar line; a note
   held across the line is in the take from step 0.
2. **No Live toggle**: the GUI has no `Live`; playing a key at any time takes
   the lead. The sequencer is not stopped.
3. **Override**: hold a MIDI key over a running generated/played line. The
   loop's sounding note stops immediately and only the input sounds. Release:
   the loop's current note attacks immediately if it was still sounding;
   otherwise the next step enters normally. No double attack, no stuck note.
4. **Sustain**: holding the pedal keeps the override until pedal-up; a new key
   retriggers.
5. **Bend**: move the wheel while a key is held; the note glides smoothly from
   the nearest class, no re-attack, no class snap. Centred returns to the class.
6. **Mod**: move the mod wheel; the lead instrument responds as its CC 1
   mapping dictates (map it in Ableton if needed).
7. **Recorded movement**: record a phrase with a bend and a mod move; the take
   plays back the same movement, on the quantised grid. Save the melody,
   restart the bridge, load it: the movement is still there. Save/recall a
   state and `Revert`: the movement is restored.
8. **Variations**: `Vary`/`Vary every` still work on a take with expression;
   `Revert` restores the take and its curves.
9. **No regressions**: generated lead when the input is silent is unchanged;
   `Vary`, groove link, breath, states, melodies, QWERTY, drone/bass/arp/drums,
   loops and the window all behave as before.
10. **Window**: the Lead input group fits, Status is reachable, the scroll
    area handles a small screen.
11. Record the result. **9.2 is not complete until the owner confirms the
    override feels seamless, the bend/mod is musical, and the recorded movement
    plays back.**

---

## 11. Risks and decisions to document in the stage README

- **Race/double attack.** Override and resume run on different threads (input
  callback, scheduler). One scheduler lock and a last-played token must make
  "release → attack" exactly once. Test with the real scheduler and a fake
  output.
- **Bend is relative to the note number.** Never compute a fresh nearest note
  while a note is sounding; keep `N` from note-on and use the bend-relative
  formula. Otherwise a wheel move can jump an octave.
- **Wheel range mismatch.** The controller's actual bend range must match
  `config/lead_input_config.json`; document how to check it in the controller.
- **MIDI event rate.** Pitch bend can arrive at ~100/s; coalesce and throttle
  (~5 ms) and never log per event. The callback stays free of disk I/O and
  blocking.
- **Timer load.** One expression player per sounding note, cancelled on every
  exit path; cap 256 points; no per-point thread explosion.
- **JSON size / version.** Optional fields, empty lists written, `version`
  stays 1; a state/melody with curves must still load on the previous build
  (unknown keys are ignored there by design).
- **Count-in decision.** The count-in is monitoring-only; held notes cross the
  bar line. If the owner wanted a pickup bar, that is a separate change.
- **Record still stops the sequencer.** The owner's "no Live toggle" is about
  normal playing; recording remains a clean take with the sequencer stopped.
- **Variation provenance.** A variation that moves a source note keeps its
  curves; a freshly generated note starts empty. Document per variation.
- **Qt.** The agent environment cannot run PyQt6; write and review the Qt test
  even if it cannot be executed here.
- **No hardware.** MIDI hardware, IAC, Ableton, audio and sclang are not
  available; never claim a listening pass.

---

## 12. Deliverables

```text
ji-live/
├── control/
│   ├── lead_input.py          MOD: audible count-in, bend/mod translation,
│   │                               override calls, expression capture,
│   │                               bend config, 16-value status
│   ├── lead.py                MOD: LeadNote bend/mod points, shared note
│   │                               validation, scheduler override/release,
│   │                               expression playback, stop/exit cleanup
│   ├── state.py               MOD: note point serialisation (optional keys)
│   ├── melody.py              MOD: note point serialisation (optional keys)
│   ├── ji_bridge.py           MOD: remove Live source semantics, compat
│   │                               set_enabled, 16-value status, logs
│   └── osc_server.py          MOD: set_enabled compatibility, no new address
├── config/
│   └── lead_input_config.json MOD: bend_range_semitones
├── run/ji_station.py          MOD: --check prints the bend range (no new flags)
├── ui/
│   ├── osc_control.py         MOD: 14/16 parser, status fields, no Live
│   └── ji_ui.py               MOD: remove the Live checkbox, bend/mod status
├── tests/
│   ├── test_lead_expression.py NEW: bend maths, port, recorder, serialisation,
│   │                               expression playback, override, count-in
│   ├── test_lead_input.py      MOD: count-in, override, 16 values, 14/16
│   ├── test_lead.py            MOD: override/expression invariants
│   ├── test_state.py           MOD: point round-trip
│   ├── test_ui.py              MOD: pure + Qt (no Live, bend/mod, 14/16)
│   └── test_startup.py         MOD: bend range in --check/docs
└── docs/
    ├── README_stage9_2.md      NEW: design, maths, data model, risks, checklist
    ├── START_HERE.md           MOD: no Live, count-in, bend/mod, troubleshooting
    ├── GUI_PARAMETERS.md       MOD: Live removed, bend/mod status, count-in
    ├── README_stage9_1.md      MOD: link to 9.2
    ├── README_mpe.md           MOD: one line on input expression on Bus 5
    ├── README_live_rig.md      MOD: no new bus (unchanged statement)
    └── design_doc_10.28.10.50  MOD: §6.25, §7.1, §7.5, §8.9, §9, §13, §14
README.md                       MOD: status, layout, tests, links
```

No changes to `synthdefs/`, `startup.scd`, `ui/control_surface.scd`,
`control/mutation.py`, `control/home.py`, `control/glide.py`,
`control/rhythm.py`, `control/breath.py` or `control/groove.py`. If you believe
one is unavoidable, stop and explain why in the stage README instead of adding
it quietly.

---

## 13. Self-review requirement (final response)

Provide:

- Files added/changed.
- For each requirement above, the code that satisfies it or a concrete concern.
- **The override sequence**: the exact call order from key-down to resume,
  including the lock boundary and the retrigger eligibility test.
- **The bend maths**: the class/`N`/cents/14-bit formula and the test that a
  bend crossing a semitone keeps the note number correct.
- **The expression data model**: one worked note with a bend and mod curve
  through `LeadNote` → melody JSON → state JSON → scheduler playback.
- **Count-in behaviour**: what is audible, what is stored, and the held-note
  seed.
- “If I had to bet on which line is most likely to fail on the owner's machine,
  which is it, and why? Give a one-line verification command.”
- Assumptions, max 5 bullets.
- Explicit statement that MIDI hardware, IAC, Ableton, audio and sclang were not
  tested by the agent; 9.2 awaits human acceptance.
- The exact one-sclang + one-Python start sequence, copied from
  `docs/START_HERE.md` after your updates.

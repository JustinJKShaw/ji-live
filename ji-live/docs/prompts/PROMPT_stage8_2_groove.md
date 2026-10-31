# Stage 8.2 Agent Prompt — Groove Generation + Beat-Quantized State Changes

You are implementing **Stage 8.2** of the JI live-performance system. Stages
0–7.1 and 8.1 are implemented. Stage 8.1 (rhythm foundation + drum engine v1)
is in use and sounds good; the owner has moved on to 8.2. Preserve every
accepted Stage 5.5 audio, Stage 6 MIDI, Stage 7/7.1 GUI, and Stage 8.1 rhythm
path.

Stage 8.2 has **two** deliverables:

1. **Groove generation for the drum engine**: pattern presets, density,
   syncopation, swing, probability, and a per-step lane grid.
2. **Beat-quantized state changes**: while the global transport is running,
   major performance-state changes (mutation/octave, on/off switches, drum
   lane/rate changes, groove edits) take effect on the **next quarter-note
   beat**, not immediately.

Do not claim human audio/MIDI acceptance. The human is the authority.

---

## 1. Read these files first

Read the current code and docs, not just summaries:

```text
docs/design_doc_10.28.10.50        §6.14, §7.1, §8.9, §9, §13, §14
docs/README_stage8.md              current Stage 8.1 behavior, limitations
docs/prompts/PROMPT_stage8_1_rhythm.md
                                   previous stage's contract and tone
tests/sc/test_live_rig.scd         sclang globals/loop functions; do not
                                   change audio DSP
ui/control_surface.scd             current tempo/loop OSC surface
startup.scd                        server/bus setup; do not change audio
control/rhythm.py                  RhythmState/RhythmEngine/BeatClock/math
control/drums.py                   DrumConfig/DrumEngine/DrumPattern/
                                   DrumOutput/DrumScheduler/DrumRig
control/midi_rig.py                optional drum output; drone/arp
control/ji_bridge.py               canonical rhythm state, /ji/rhythm/*
control/osc_server.py              OSC callbacks
control/mutation.py                engine API only; do not modify
ui/osc_control.py                  pure UI message/state layer
ui/ji_ui.py                        compact PyQt6 window (scroll area)
config/rhythm_config.json          drum port/channel/lanes/defaults
tests/test_rhythm.py               8.1 pure tests
tests/test_ui.py                   pure + headless Qt tests
tests/test_mpe.py                  fake MIDI output tests
README.md                          run commands / status
```

Run Python tests from `ji-live/`.

---

## 2. Current state you must preserve

- Stage 5.5 audio: MiniFuse 2 input / BlackHole 16ch output, 2 in / 16 out,
  48 kHz, blockSize 64. SC 0/1 = live autotuned inputs, 2/3 = loops.
- Stage 6: drone on IAC Bus 1, arp on IAC Bus 2, member channel 2,
  48-semitone bend range. The arp's 0.25 s/0.20 s timing is still fixed; do
  not tempo-sync it in 8.2.
- Stage 7/7.1: compact one-window GUI, Status, and independent Drone/Arp
  switches in the top-row MIDI group.
- Stage 8.1:
  - Python bridge owns quarter-note BPM (20–300), meter (1–16 over
    1/2/4/8/16), and transport.
  - `RhythmEngine`/`BeatClock` are the monotonic beat source.
  - Drums: optional IAC Bus 3, channel 10, 16 lanes (36–51), default
    four-on-the-floor, lane mask, half/normal/double rate, short gate
    note-on/note-off, CC 123 on stop.
  - Per-loop `free`/`beat` modes; beat-mode Record/Play quantized in sclang;
    free mode immediate.
  - GUI is deliberately compact: 8 pt font, 32 px knobs, one-row autotune
    channels, compact loops/rhythm, top-right MIDI group, and a `QScrollArea`
    safety net. Keep it compact.
- `/ji/freqset/update`, `synthdefs/*`, `startup.scd` audio behavior,
  `tests/sc/test_live_rig.scd` audio DSP, `tests/sc/test_dual_loop.scd`,
  `tests/sc/test_ableton_routing.scd`, `control/mutation.py`, and Stage 5.5
  routing/loop-length semantics must not change.
- Existing tests must keep passing:
  `tests/test_mutation.py`, `tests/test_mutation_controls.py`,
  `tests/test_rhythm.py`, `tests/test_mpe.py --pure`,
  `tests/test_ui.py --pure`, and the optional Qt test.
- Important compatibility rule: while the transport is **stopped**, every
  quantized action applies immediately. This keeps Stage 7/8.1 behavior and
  tests working when no transport is running.
- Direct API calls such as `MutationOSCServer.trigger()` and `DrumEngine.set_lane()`
  remain immediate low-level operations. Quantization belongs to the bridge/UI
  request layer, not the pure engine.

---

## 3. Scope

### In scope

- New pure groove model/generator (presets, density, syncopation, swing,
  probability, per-step grid, seeded/deterministic output, validation).
- Additive drum-engine integration: grid source, groove settings, swing
  timing, deterministic ghost generation.
- Beat-quantized state changes in the Python bridge:
  - mutation trigger / octave trigger;
  - Drone on/off, Arp on/off;
  - drums on/off, drum rate, per-lane enables;
  - groove preset/modifier/grid edits.
- A pending-state queue driven by `RhythmEngine`'s next quarter-note beat.
- GUI: compact Groove controls and a collapsible 16-lane step grid; pending
  indicator; status feedback.
- OSC additions, status messages, tests, and docs.
- Update `docs/README_stage8_2.md`, the design doc, and `README.md`.

### Out of scope

- Bass/lead/extra melodic voices (8.3).
- Sequencing/pattern mutation of frequency sets or MIDI patterns (8.4).
- Save/load/recall and seed reproduction of the whole state (8.5). Groove
  generation itself must be seed-reproducible, but a full session snapshot is
  not required.
- More than four loops, overdub, per-loop routing, loop-player autotune,
  loop-file persistence (8.6).
- Time-stretching existing loop buffers.
- Changing the Stage 6 arp timing or making it tempo-synced.
- Polyphonic MPE, granular FX, visual process, GUI aesthetics/tabs.
- Ableton API automation.
- Time-quantizing tempo, meter, or transport themselves (see §5.3).

---

## 4. Architecture

```text
UI (PyQt6)
 ├─ bridge 9000 → mutation/groove/drum/MIDI requests, transport, tempo, meter
 ├─ sclang 57120 → autotune/loops, loop set_mode/set_beats, status query
 └─ listener 9001 ← bridge: quantize/state, rhythm/state, drums/state,
                     drums/groove, drums/grid, midi/state, freqset/changed
                   ← sclang: loop/config, loop/state, cpu, autotune/state
```

The Python bridge remains the canonical rhythm and drum owner. sclang keeps
its `TempoClock` for loop quantization. The two clocks are separate; document
drift.

### 4.1 Global beat definition

- "Next beat" means the next **quarter-note beat** of the bridge's
  `RhythmEngine`/`BeatClock`, not the next 16th-note drum step and not the
  next bar.
- Use `RhythmEngine.next_beat_time(1.0)` (or the equivalent `BeatClock`
  method). A request that arrives exactly on a boundary waits for the
  following beat; it never applies at the current instant.
- The drum rate (0.5/1/2) does not change the quantization grid. A half-time
  drum change still lands on the next quarter-note beat.
- At 120 BPM the maximum wait is 0.5 s; at 20 BPM it is 3.0 s. Document that
  this latency is intentional.

### 4.2 What is quantized vs. immediate

**Quantized while transport is running** (applied at the next quarter beat):

| Action | Examples | Coalescing |
|---|---|---|
| Mutation trigger | Mutate button, `/ji/mutation/trigger` | one pending trigger per beat; repeated presses before the beat coalesce and log the count |
| Octave trigger | Octave button, `/ji/mutation/octave` | one pending octave per beat |
| Drone on/off | MIDI group checkbox | last-wins |
| Arp on/off | MIDI group checkbox | last-wins |
| Drums on/off | Rhythm drums checkbox | last-wins |
| Drum rate | Half/Normal/Double | last-wins |
| Drum lane | 16 lane toggles | last-wins per MIDI note |
| Groove preset | preset combo | last-wins |
| Density / syncopation / swing / probability / seed | Groove knobs/spin | last-wins per parameter |
| Step-grid cell | grid toggle | last-wins per `(lane, step)` |
| Grid reset/preset load | selecting a preset reloads the grid | last-wins with preset |

**Always immediate** (not quantized):

- tempo (`/ji/rhythm/set_tempo`), meter (`/ji/rhythm/set_meter`), transport
  (`/ji/rhythm/transport`) — these define the grid; document why;
- continuous mutation parameters (randomness, dissonance, stepwise,
  temperature, anchor mobility, change_count, glide);
- live-autotune parameters;
- loop mode/beats/free length and loop Stop/Stop Rec/Clear/Stop All;
- free-loop Record/Play (Stage 7 semantics);
- beat-mode loop Record/Play are already quantized by sclang to its next
  beat — do not double-queue them in Python;
- UI-only actions (opening/closing the step grid, scrolling, etc.).

When transport is **stopped**, quantized actions apply immediately. This is
mandatory backward compatibility.

### 4.3 Quantizer mechanics

Create a pure, testable queue (suggested `control/quantize.py`,
`BeatQuantizer`/`StateQueue`; a different file split is fine if documented):

- Thread-safe `schedule(key, action, coalesce=True)`, `cancel(key)`,
  `pending_count`, `seconds_to_next_beat`, `flush()`.
- `tick(now)` / `run()`: actions run at the next beat; no busy-wait.
- The queue must wake/recompute when the bridge tempo changes or the
  transport stops/starts. Do not drop a pending action on a tempo change.
- On transport stop: **flush pending actions immediately** in deterministic
  order, then let the normal transport-stop path stop drums/beat loops.
  Log the flush. This avoids carrying a stale queue into the next start.
- On transport start: no stale pending actions should exist.
- Deterministic application order at a beat:
  1. mutation then octave;
  2. groove preset/modifiers/grid;
  3. drum enable/rate/lanes;
  4. drone/arp enables.
  Document the order. Actions with the same key coalesce; different keys all
  run.
- Callbacks run outside the bridge's parameter locks. The mutation callback
  may call `MutationOSCServer.trigger()`/`octave()`; the server already takes
  its own locks and invokes `on_event` after releasing them.
- Do not start the quantizer thread in `JIBridge.__init__`; start it lazily
  (or in `JIBridge.start()`), and stop it in `JIBridge.stop()`, so the many
  pure tests that construct a bridge without starting it create no threads.
- Support an injectable clock for tests (`now=...`) so the queue can be
  tested without sleeping.

### 4.4 Mutation request hook

`MutationOSCServer.trigger()` must stay immediate for direct callers/tests.
Add an optional request callback (e.g. `on_mutation_request`,
`on_octave_request`) used by the OSC handlers; when present, the handler asks
the bridge to queue instead of applying immediately. The bridge's interactive
console must also go through the queue. `/ji/mutation/trigger` and
`/ji/mutation/octave` addresses and direct `trigger()`/`octave()` semantics
stay unchanged.

The bridge must send `/ji/quantize/state` when:
- an action is queued;
- actions are applied/flushed/cancelled;
- on `/ji/ui/hello` and bridge start;
- after a transport/tempo change that changes the next-beat countdown.

### 4.5 Groove model (pure)

Create a pure groove module (suggested `control/groove.py`). It must not
import mido, PyQt6, sockets, or audio.

Required data/behavior:

- **Grid**: 16 lanes × step columns for the current meter's 16th-note bar
  (`steps_per_bar = numerator * 16 / denominator`). Cap editable columns at
  32 for 8.2; meters longer than 32 steps keep generated/truncated patterns
  and document the cap. A lane row is a bit mask; bit `s` = step `s`.
- **Presets**: at least four named presets, including the 8.1 default
  `"four"` (four-on-the-floor). Choose and document the exact patterns. A
  suggested set:
  - `four`: kick1 0,4,8,12; snare1 4,12; perc3 2,6,10,14;
  - `break`: kick1 0,6,10; snare1 4,12; perc3 every 2; perc2 4,12;
  - `half`: kick1 0,8; snare1 8; perc3 every 4;
  - `techno`: kick1 every 4; perc3 every 2; perc2 4,12; perc5 2,6,10,14.
  For non-4/4 meters, truncate/extend against `steps_per_bar` in a documented
  way, as 8.1 does. Do not invent polyrhythm logic.
- Selecting a preset replaces the grid with that preset's base pattern
  (document that manual grid edits are discarded on preset change). The
  default at startup is `"four"`, matching 8.1.
- **Density** 0..1: add deterministic ghost hits to empty eligible cells.
  Density 0 = preset/grid only. Suggested eligible lanes are perc lanes, or
  any lane except kick1/snare1; document exactly.
- **Syncopation** 0..1: deterministically displace a subset of non-downbeat
  hits by one 16th step (or add offbeat 16th ghosts). Do not displace step 0;
  keep a documented, testable rule. Syncopation 0 = no displacement.
- **Probability** 0..1: gates **generated ghost hits** (density/syncopation),
  not the core preset/grid hits. The core groove must never drop out because
  probability is low. Use a seeded RNG; document.
- **Swing** 0..1: timing only, not pattern. At 0 all steps are straight; at
  1 odd 16th steps are delayed by at most 50% of one step. Suggested:
  `swing_offset(step) = 0 if step even else swing * 0.5 * step_seconds`.
  The drum scheduler must use it without bursting or drifting.
- **Seed/reproducibility**: a documented seed (default from
  `config/rhythm_config.json`, e.g. 1). Same preset + modifiers + seed + bar
  index must produce the same ghost hits/displacements. Do not use ambient
  `random` state. Reset/reseed per bar deterministically so the pattern is
  reproducible.
- **Validation**: preset names, 0..1 modifiers, lane index 0..15, step index
  0..31, seed integer. Reject/clamp safely; never apply an unsafe value.
- **Meter changes**: rebuild the preset/grid for the new bar length; document
  whether custom edits survive (prefer reloading the preset on meter change
  and logging it).

### 4.6 Drum engine/scheduler integration

- `DrumEngine`/`DrumPattern` (or a new groove engine used by them) must be
  able to return the notes for a step from the generated pattern, including
  swing offset for the scheduler.
- Keep `DrumEngine.set_lane`, `set_rate`, `set_meter`, `set_enabled` immediate
  at the engine level; quantized behavior is the bridge request layer.
- `DrumScheduler` must support a swing offset per step. It must remain
  drift-corrected, responsive to stop, and must not burst through missed
  steps. Note-on/note-off gate semantics and CC 123 on stop stay as 8.1.
- Changing rate/swing/grid/preset at a beat may change the next step's
  timing; do not restart the whole transport or the audio engine.
- Log the applied groove once per change (not per step).

### 4.7 OSC map (proposed)

**UI → bridge**

```text
/ji/drums/set_preset          string preset
/ji/drums/set_density         float 0..1
/ji/drums/set_syncopation     float 0..1
/ji/drums/set_swing           float 0..1
/ji/drums/set_probability     float 0..1
/ji/drums/set_step            int lane_index 0..15, int step 0..31, int on 0/1
/ji/drums/set_seed            int seed
/ji/drums/reset_grid          none (reload the current preset)
```

These are quantized when the transport is running. Keep the existing
`/ji/drums/set_enabled`, `/ji/drums/set_lane`, `/ji/drums/set_rate`,
`/ji/midi/set_enabled`, and mutation addresses; their handlers now request
quantized application.

**Bridge → UI (additive)**

```text
/ji/drums/groove    string preset, float density, float syncopation,
                    float swing, float probability, int seed,
                    int grid_steps
/ji/drums/grid      32 ints: for lane 0..15, [mask_lo, mask_hi]
                    (bit s of mask_lo = step s; bit s of mask_hi = step 16+s)
/ji/quantize/state  int pending_count, float seconds_to_next_beat
                    (seconds = -1.0 when the transport is stopped and the
                    queue is empty/offline)
```

Keep `/ji/drums/state`, `/ji/rhythm/state`, `/ji/loop/config`, and the
existing status messages unchanged. Send the new messages at bridge start, on
`/ji/ui/hello`, and after every queue/apply/flush. `/ji/status/query` goes to
sclang, not the bridge; do not create a second bridge-side query path for 8.2.
The UI's existing `/ji/ui/hello` is the bridge-side snapshot trigger.

### 4.8 GUI

Keep the one-window, no-tabs, compact design. Do not regress the current
8 pt/32 px/scroll-area layout.

Add:

- A **Groove** group (below Rhythm or inside it):
  - Preset combo (`four`, `break`, `half`, `techno`, or the chosen names).
  - Compact knobs/spins for Density, Syncopation, Swing, Probability.
  - "Show grid" checkbox (grid hidden by default so the compact window stays
    compact).
  - Optional "Reset grid" button.
- A **step grid** when shown:
  - 16 lane rows (labels from `DRUM_LANE_DEFS`), one small toggle per 16th
    step column for the current bar (up to 32).
  - Row/column labels legible at 8 pt; cells small (≈14 px). Grid may scroll
    horizontally/vertically inside the window if needed.
  - Clicking a cell sends `/ji/drums/set_step` (quantized); the UI may show the
    click optimistically, but must resync from `/ji/drums/grid` after the
    beat. Avoid feedback OSC while syncing.
  - Building 256–512 widgets must not freeze the window; lazy-build on first
    show is acceptable.
- A **pending/quantize status** line, e.g.
  `Next beat: 0.32 s | 2 pending`, and clear feedback when a mutation or
  toggle is queued. The UI must show `quantized off`/immediate when transport
  is stopped.
- Keep all existing controls and status. The Drone/Arp switches stay in the
  top MIDI group.
- The GUI must remain usable if the bridge/sclang/drums are absent; show
  waiting/disabled states, not crash.
- Keep the ≈30 Hz coalescing for continuous knobs; buttons/spins send
  immediately (the bridge queues them).
- Block widget signals when syncing incoming state; no feedback OSC.

### 4.9 Startup/status

- On bridge start: send current groove/grid/quantize state and the existing
  rhythm/drum/MIDI state.
- On `/ji/ui/hello`: send all of it.
- `/ji/status/query` remains a UI → sclang message for loop config; do not
  route it to the bridge. The bridge's snapshot trigger is `/ji/ui/hello`.
- Send `/ji/quantize/state` after queueing/applying/flushing and on transport
  changes. Do not flood: one message per change, not per pending action.
  If only the countdown is changing, update at no more than ~10 Hz or let the
  UI count down locally from the last `seconds_to_next_beat`; never send at
  animation-frame rate.

---

## 5. Required behavior details

### 5.1 Mutation

- Pressing Mutate while the transport is running must not change the set until
  the next quarter beat. Before the beat, `/ji/freqset/changed` and the audio
  must still show the old set.
- At the beat, apply one mutation and send exactly one `/ji/freqset/update`
  (the existing contract). The drone/arp receive the new snapshot at the beat.
- Repeated Mutate presses before the beat coalesce into one pending trigger;
  log `coalesced N requests`.
- Octave follows the same rule.
- With transport stopped, mutation/octave are immediate.
- Do not quantize the mutation parameter knobs; they remain immediate.

### 5.2 Enables/rate/lanes

- Drone/Arp/drums on/off, drum rate, and lane toggles are last-wins while
  running and apply at the next beat.
- A lane toggle affects the pattern from the next drum step **at/after** the
  beat; do not leave a stuck note. The scheduler's existing gate note-offs
  still run.
- Turning drums off at the beat stops the scheduler and sends note-offs/CC 123.
- Turning drums on at the beat starts the scheduler; define whether it starts
  at step 0 or the next step and document. Prefer step 0 on the beat for a
  clean bar-like start.
- Transport stop flushes pending enables before stopping, so the last command
  is not lost. Then the transport-stop path stops drums/beat loops.

### 5.3 Tempo/meter/transport

- Tempo/meter/transport remain immediate. They define the grid.
- If tempo changes while actions are pending, recompute the next beat at the
  new tempo; do not apply immediately and do not drop the actions.
- If transport stops while actions are pending, flush them immediately and
  log.
- The quantization uses the Python beat clock; beat-mode loops still use
  sclang's clock. Document the possible few-ms drift and that the existing
  Resync action/stop-start re-anchors.

### 5.4 Groove application

- Preset/modifier/grid/seed changes apply at the next beat and are last-wins.
- The pattern heard in the bar after the beat must match the new state.
- Grid edits are per-cell and must survive until preset change/meter rebuild;
  document.
- Swing affects timing immediately from the next step at/after the beat, not
  the already-played steps.
- Probability/density/syncopation must be deterministic for a given seed/bar.
- Core preset/grid hits must always play; probability gates only ghosts.
- The 8.1 default (`four`, density 0, sync 0, swing 0, probability 0) must be
  identical to the current four-on-the-floor pattern.

---

## 6. Deliverables

```text
ji-live/
├── control/
│   ├── groove.py              NEW: presets, grid, modifiers, seeded generation
│   ├── quantize.py            NEW: pure beat-quantized state queue
│   ├── drums.py               MODIFIED additively: groove integration, swing
│   ├── midi_rig.py            MODIFIED additively if needed for drum setters
│   ├── ji_bridge.py           MODIFIED additively: quantizer, groove state
│   └── osc_server.py          MODIFIED additively: groove/quantize hooks
├── ui/
│   ├── osc_control.py         MODIFIED additively: builders/parsers/state
│   └── ji_ui.py               MODIFIED additively: Groove group, step grid,
│                              pending status
├── config/
│   └── rhythm_config.json     MODIFIED additively (or new groove_config.json)
├── tests/
│   ├── test_groove.py         NEW: groove/preset/grid/modifier tests
│   ├── test_quantize.py       NEW: queue/coalescing/flush tests
│   ├── test_rhythm.py         MODIFIED additively if needed
│   └── test_ui.py             MODIFIED additively
├── docs/
│   ├── README_stage8_2.md     NEW
│   └── design_doc_10.28.10.50 UPDATED: new §6.15, §7.1, §8.9, §9, §14
└── README.md                  UPDATED: Stage 8.2 status/next link
```

If a different file split is clearer, keep the same conceptual layers and
document it. Prefer additive changes.

---

## 7. Tests

### 7.1 New pure tests

Run from `ji-live/`:

```bash
uv run --with python-osc python tests/test_quantize.py
uv run --with python-osc python tests/test_groove.py
```

Cover at least:

- Quantizer:
  - transport stopped → `schedule()` applies immediately;
  - transport running → not before the next quarter beat, exactly at it;
  - coalescing (last-wins per key; mutation one per beat with a log count);
  - deterministic order when several keys are due;
  - tempo change reschedules without dropping;
  - transport stop flushes in order;
  - no threads created in `JIBridge.__init__`; stop is clean;
  - `seconds_to_next_beat`/pending count updates.
- Groove:
  - each preset is deterministic and documented; `"four"` matches 8.1;
  - non-4/4 truncation/extend;
  - density/syncopation/probability bounds and 0-value identity;
  - swing offset 0 = straight, 1 = 50% late on odd steps; deterministic;
  - grid validation, bit/mask encoding, up to 32 steps;
  - same seed/preset/modifiers/bar → same ghosts; different seed → different
    ghosts (where the modifier is nonzero);
  - probability never drops core preset/grid hits;
  - invalid preset/lane/step/modifier rejected or clamped safely.
- Drum scheduler:
  - swing changes step timing without bursts or stuck notes;
  - note-on/note-off/CC 123 still deterministic with a fake output.

### 7.2 Existing tests

```bash
uv run --with python-osc python tests/test_mutation.py
uv run --with python-osc python tests/test_mutation_controls.py
uv run --with python-osc python tests/test_rhythm.py
python tests/test_mpe.py --pure
uv run --with python-osc python tests/test_ui.py --pure
```

All must pass. In particular:
- `MutationOSCServer.trigger()` remains immediate when called directly;
- `DrumEngine.set_lane()` remains immediate;
- with transport stopped, bridge requests apply immediately;
- `JIBridge(...)` still constructs without mido/MIDI enabled;
- `--midi` without `--drums` still does not need Bus 3.

### 7.3 Optional headless Qt test

```bash
QT_QPA_PLATFORM=offscreen uv run --with python-osc --with PyQt6 \
  python tests/test_ui.py --qt
```

Add checks for:
- Groove group/preset/modifier controls; step grid show/hide; cell click sends
  `set_step`; status sync with signals blocked;
- pending status line;
- mutation/toggle requests show pending while transport is running;
- with transport stopped, actions apply/send immediately and pending is
  clear;
- window stays compact and usable at the default size; grid hidden by
  default.
Skip cleanly if PyQt6 is unavailable.

### 7.4 Human sclang/audio checks

Audio, MIDI, Ableton, IAC, and live timing are human-only. Provide a short
human checklist in `docs/README_stage8_2.md`. Optional sclang test file is
allowed but not required.

---

## 8. Human acceptance checklist

1. With transport **stopped**, Mutate/Octave and all toggles still act
   immediately (Stage 7/8.1 behavior).
2. With transport **playing**, Mutate applies exactly on the next beat; the
   old set is audible/visible until then; the pending indicator shows it.
3. Drone/Arp/drums on/off, drum rate, and lane toggles apply on the next beat;
   no double-trigger, no stuck notes.
4. Groove preset, density, syncopation, swing, and probability changes apply
   on the next beat and are audible; 0 settings reproduce the 8.1 groove.
5. The step grid edits individual cells while playing; edits land on the next
   beat; the grid status matches what is heard; hidden by default.
6. Swing is audible at high values and disappears at 0; timing does not drift
   or burst.
7. The same seed/preset/modifiers reproduce the same groove; document the
   seed.
8. Free loops are unchanged and immediate; beat-mode loops still quantize to
   sclang's next beat.
9. Mutation, autotune, loop, drone/arp, and existing GUI behavior do not
   regress; the window remains compact/usable and scrolls instead of clipping.
10. No audio dropouts, no MIDI stuck notes; CPU remains within budget.
11. Record the result. **Stage 8.2 is not complete until the human confirms
    the groove and quantized-state behavior and no audio/MIDI regressions.**

---

## 9. Diagnostics

Add useful lines with existing prefixes:

```text
[QUANT] queued mutation (1 pending) due beat 42 (in 0.250 s)
[QUANT] coalesced 2 mutation requests before beat 42
[QUANT] applied 3 actions at beat 42 (order: mutation, groove, drums)
[QUANT] flushed 2 pending actions on transport stop
[QUANT] tempo change: next beat recalculated (in 0.180 s)
[GROOVE] preset=break density=0.30 sync=0.20 swing=0.15 probability=0.70 seed=1
[GROOVE] grid reloaded for meter=7/8 steps=14
[DRUMS] swing=0.15 step=5 offset=0.009 s
[DRUMS] applied groove at beat 42
```

Do not spam per-step logs during performance; log queue/apply/config changes
and the first scheduled step only.

---

## 10. Risks / decisions to document

- **Latency:** quantized changes wait up to one beat (3 s at 20 BPM). This is
  intentional; stopping the transport makes changes immediate.
- **Clock drift:** Python's beat quantizer and sclang's `TempoClock` are
  separate. Quantized mutations/drums land on Python's beat; beat-mode loops
  land on sclang's. Document the few-ms drift and the existing Resync /
  stop-start path. Do not silently hide it.
- **Coalescing:** repeated mutation presses before the beat may be collapsed;
  log the count. Different keys all run in the documented order.
- **Transport stop flush:** pending actions apply immediately on stop; the
  last command is not lost. Document.
- **Grid cap:** editable grid columns are capped at 32; meters longer than
  that keep generated patterns and log the cap. Document.
- **Preset vs. grid:** selecting a preset replaces manual grid edits. Document.
- **Probability:** only generated ghosts are gated; core hits always play.
- **Swing:** max 50% of a 16th step late; no triplet/swing subdivision.
- **Existing arp timing:** do not tempo-sync the Stage 6 arp in 8.2.
- **GUI density:** keep the compact 8 pt layout and scroll area; do not push
  Status or Drone/Arp off-screen.

---

## 11. Self-review requirement (final response)

Provide:

- Files added/changed.
- For each requirement above, the code that satisfies it or a concrete
  concern.
- "If I had to bet on which line is most likely to fail on the human's
  machine, which is it, and why? Give a one-line verification command."
- Assumptions, max 5 bullets.
- Explicit statement that audio, MIDI, Ableton, IAC, sclang timing, and the
  live GUI were not tested by the agent; Stage 8.2 awaits human acceptance.

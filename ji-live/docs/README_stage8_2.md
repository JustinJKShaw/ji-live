# JI Station — Stage 8.2 Groove Generation + Beat-Quantized State

**Status:** **human-tested and accepted 2026-10-31.** The owner ran the live
audio/MIDI/GUI rig, including beat-mode loop record/play, and confirmed it
works. Stage 8.2 is complete. The agent's own verification remains limited to
the pure Python harnesses plus the UI/joint code review recorded below.

Stage 8.2 has two deliverables:

1. **Groove generation** for the drum engine: presets, density, syncopation,
   swing, probability, a seeded/reproducible generator, and a per-step lane
   grid.
2. **Beat-quantized state changes**: while the transport is running, major
   performance-state changes (mutation/octave, MIDI/drum enables, drum rate and
   lanes, groove edits) apply on the next **quarter-note beat**, not
   immediately.

It is additive. It does not change `/ji/freqset/update`, the Stage 5.5 audio
map, the Stage 6 drone/arp timing, the Stage 7/7.1 GUI contracts, or the Stage
8.1 rhythm/drum MIDI path. `control/mutation.py`, `synthdefs/*`, `startup.scd`,
`tests/sc/test_live_rig.scd`, `tests/sc/test_dual_loop.scd`, and
`tests/sc/test_ableton_routing.scd` are untouched. `ui/control_surface.scd`
gets one Stage 8.1 bug fix: beat-mode Record/Play scheduled actions now receive
the loop index, so synced loop record/play actually runs (see §5.8).

---

## Acceptance record

**2026-10-31 — owner-confirmed.** The owner ran the live rig and reported
"all works". This covers the groove presets/modifiers, the step grid, the
pending/quantized-state behavior, and the Stage 8.1 paths exercised through
8.2 (transport, drums, free/beat loops). The Stage 8.1 beat-mode loop bug was
found during this check and fixed in 8.2.2 (`action.value(index)`); the owner
re-tested beat-mode Record/Play successfully. Human acceptance is the
authority; no further agent audio/MIDI claims are made.

**Next:** Stage 8.3a is the monophonic JI bass voice; the agent prompt is
[PROMPT_stage8_3_bass.md](prompts/PROMPT_stage8_3_bass.md).

---

## 1. What Stage 8.2 adds

1. **Groove model** (`control/groove.py`): four presets (`four`, `break`,
   `half`, `techno`), density, syncopation, swing, probability, a 16-lane
   step grid, and deterministic seeded ghost generation.
2. **Groove engine integration** (`control/drums.py`): the scheduler plays the
   generated pattern, applies swing per step without drift/bursts, and keeps
   note-on/note-off/CC 123 semantics.
3. **Beat quantizer** (`control/quantize.py`): a thread-safe next-quarter-beat
   queue with last-wins coalescing, a documented phase order, tempo-change
   rescheduling, and a transport-stop flush.
4. **Bridge integration** (`control/ji_bridge.py`, `control/osc_server.py`):
   queued mutation/octave, drone/arp, drum enable/rate/lane, and groove edits;
   new status messages `/ji/drums/groove`, `/ji/drums/grid`,
   `/ji/quantize/state`.
5. **GUI** (`ui/ji_ui.py`, `ui/osc_control.py`): a compact **Groove** group, a
   collapsible 16-lane step grid (hidden by default), and a pending/quantize
   status line.
6. **Tests/docs**: `tests/test_groove.py`, `tests/test_quantize.py`, additive
   `tests/test_rhythm.py` / `tests/test_ui.py`, this document.

Still out of scope: bass/lead/extra voices (8.3), pattern/sequencing mutation
(8.4), save/recall and whole-state seed reproduction (8.5), more loops/overdub/
routing (8.6), time-stretching existing loop buffers, tempo-syncing the Stage 6
arp, polyphonic MPE, granular FX, GUI aesthetics/tabs, and Ableton API
automation.

### Files

| File | Status | Purpose |
|---|---|---|
| `control/groove.py` | new | pure presets, grid, modifiers, seeded generation, swing math |
| `control/quantize.py` | new | pure thread-safe next-quarter-beat state queue |
| `control/drums.py` | modified additively | groove source, swing scheduler, grid setters |
| `control/midi_rig.py` | modified additively | drum groove/step/seed delegation |
| `control/ji_bridge.py` | modified additively | quantizer, groove state, `/ji/quantize/state`, `/ji/drums/groove`, `/ji/drums/grid` |
| `control/osc_server.py` | modified additively | groove handlers + mutation/octave request hooks |
| `ui/osc_control.py` | modified additively | builders, parsers, `GrooveStatus`, `QuantizeStatus`, store fields |
| `ui/ji_ui.py` | modified additively | Groove group, lazy step grid, pending line |
| `ui/control_surface.scd` | bug fix | beat-mode scheduled actions receive the loop index |
| `ui/control_surface_8.2.2_patch.scd` | new (optional) | standalone live patch for `~ji_schedule_loop_action` |
| `config/rhythm_config.json` | modified additively | nested `groove` defaults |
| `tests/test_groove.py` | new | preset/grid/modifier/seeded generation tests |
| `tests/test_quantize.py` | new | queue/coalescing/flush/order/thread tests |
| `tests/test_rhythm.py` | modified additively | groove config + swing scheduler check |
| `tests/test_ui.py` | modified additively | groove builders/parsers/bridge/Qt coverage |
| `docs/README_stage8_2.md` | new | this document |
| `README.md`, `docs/design_doc_10.28.10.50` | updated | status, OSC map, changelog |

---

## 2. Setup

The Stage 5.5 audio, Stage 6 MIDI, Stage 7 GUI, and Stage 8.1 drum setup are
unchanged. Stage 8.2 needs no new device, bus, port, or sclang file.

### 2.1 Config

`config/rhythm_config.json` now carries the Stage 8.2 defaults inside `groove`:

```json
"meter": [4, 4],
"groove": {
  "preset": "four",
  "density": 0.0,
  "syncopation": 0.0,
  "swing": 0.0,
  "probability": 0.0,
  "seed": 1
},
```

Flat top-level keys (`"preset"`, `"seed"`, …) are also accepted; a nested
`"groove"` value wins if both are present. `"four"` with all modifiers at 0 is
the exact Stage 8.1 four-on-the-floor pattern, so an old config is unchanged.

### 2.2 Start order

Run everything from `ji-live/`. The order is unchanged from Stage 8.1:

1. sclang: evaluate `startup.scd`.
2. sclang: evaluate `tests/sc/test_live_rig.scd`.
3. bash terminal 1 (leave running):

   ```bash
   uv run --with python-osc --with mido --with python-rtmidi \
     python control/ji_bridge.py --interactive --midi --drums --glide-time 0.15
   ```

4. sclang: evaluate `ui/control_surface.scd` **after** the live rig.
5. bash terminal 2 (leave running):

   ```bash
   uv run --with python-osc --with PyQt6 python ui/ji_ui.py
   ```

6. In the GUI: Rhythm → Play, then Rhythm → Groove. The step grid is hidden by
   default; tick **Show grid** to edit cells.

Stage 8.2 adds no new sclang file, but re-evaluate `ui/control_surface.scd`
after the live rig to pick up the §5.8 beat-mode Record/Play fix (it resets
per-loop modes to Free, so set Beat again).

---

## 3. Controls

### 3.1 Groove group (UI → bridge, port 9000)

| Control | OSC | Range/default |
|---|---|---|
| Preset | `/ji/drums/set_preset` | `four`, `break`, `half`, `techno`; default `four` |
| Density | `/ji/drums/set_density` | 0..1, default 0 |
| Syncopation | `/ji/drums/set_syncopation` | 0..1, default 0 |
| Swing | `/ji/drums/set_swing` | 0..1, default 0 |
| Probability | `/ji/drums/set_probability` | 0..1, default 0 |
| Seed | `/ji/drums/set_seed` | integer 0..2^31−1, default 1 |
| Step cell | `/ji/drums/set_step` | `int lane 0..15, int step 0..31, int on 0/1` |
| Reset grid | `/ji/drums/reset_grid` | no args; reloads the current preset |

Groove knobs are coalesced at ~30 Hz like the other continuous knobs; the
preset combo, seed spin, Show grid checkbox, Reset grid button, and grid cells
send immediately (the bridge queues them). While the transport runs these
requests are **queued for the next quarter beat**; while it is stopped they
apply immediately.

**Important interaction:** probability gates generated ghosts. At the shipped
default `probability = 0` you hear only the preset/grid (Stage 8.1 behavior).
To hear density/syncopation ghosts, raise **Probability** above 0 as well.

### 3.2 Step grid

- 16 lane rows, labeled from `DRUM_LANE_DEFS`.
- One small checkable cell per 16th step for the current bar, up to 32 columns.
- Hidden by default to keep the window compact; built lazily on first show.
- Clicking a cell sends `/ji/drums/set_step` (quantized while running). The UI
  shows the click optimistically and resyncs from `/ji/drums/grid` after the
  beat; incoming sync blocks widget signals, so it never echoes OSC.
- The bridge sends the grid as 32 ints: for each lane 0..15, `[mask_lo,
  mask_hi]`; bit `s` of `mask_lo` is step `s`, bit `s` of `mask_hi` is step
  `16 + s`.

### 3.3 Status

| Message | Args | Source |
|---|---|---|
| `/ji/drums/groove` | `string preset, float density, float syncopation, float swing, float probability, int seed, int grid_steps` | bridge |
| `/ji/drums/grid` | 32 ints: `[lo, hi]` per lane 0..15 | bridge |
| `/ji/quantize/state` | `int pending_count, float seconds_to_next_beat` | bridge |

`seconds_to_next_beat` is `-1.0` when the transport is stopped and the queue is
empty/offline. The bridge sends these at start, on `/ji/ui/hello`, after every
queue/apply/flush/cancel, and after a transport/tempo change that changes the
countdown. It does not flood: one message per change, never per animation
frame. The UI counts down locally from the last value.

The pending line reads e.g. `Next beat: 0.32 s | 2 pending` while running, and
`Next beat: - | quantized off (immediate)` while stopped.

---

## 4. Groove model

### 4.1 Presets (16-step templates, 4/4)

| Preset | kick1 | snare1 | perc2 | perc3 | perc5 |
|---|---|---|---|---|---|
| `four` | 0,4,8,12 | 4,12 | — | 2,6,10,14 | — |
| `break` | 0,6,10 | 4,12 | 4,12 | every 2 (0,2,…,14) | — |
| `half` | 0,8 | 8 | — | every 4 (0,4,8,12) | — |
| `techno` | every 4 | — | 4,12 | every 2 | 2,6,10,14 |

`four` is generated with the Stage 8.1 rule, not from the template table, so it
matches 8.1 for every meter (including 12/8, where the snare stays on quarter
beats 2 and 4). The other presets truncate their template to bars shorter than
16 steps and tile it for longer bars. Non-4/4 meters therefore keep a
deterministic pattern; no polyrhythm logic is attempted.

Selecting a preset **replaces the grid**. Manual cell edits are discarded on a
preset change, a meter change, or **Reset grid** — this is documented and by
design.

### 4.2 Density

`round(density * steps_per_bar / 2)` empty cells on percussion lanes are chosen
by a seeded sample. At density 0: nothing. At density 1 on a 16-step bar: 8
ghosts. Kick1/snare1 are never chosen; the core groove is untouched.

### 4.3 Syncopation

For every core grid hit that is **not** on a quarter-note downbeat (and not step
0), a same-lane anticipation 16th at `step − 1` is added with probability
`syncopation`. The core hit always remains, so syncopation never removes a
preset/grid hit. At syncopation 1 on `four`, the offbeat hats at steps
2,6,10,14 gain anticipations at 1,5,9,13.

### 4.4 Probability

Each generated ghost (density or syncopation) is kept with probability
`probability`. Probability 0 mutes every ghost; 1 keeps every ghost. Core
preset/grid hits are **never** gated. This is why the shipped default
(`probability = 0`) reproduces 8.1 exactly.

### 4.5 Swing

Timing only, never pattern. Even 16th steps are straight. Odd 16th steps are
delayed by `swing * 0.5 * step_seconds`; swing 0 = straight, swing 1 = half a
step late. There is no triplet subdivision. The drum scheduler advances its
nominal deadline by exactly one step, so swing does not drift and never fires a
step early; if the thread falls behind it resumes from `now` instead of bursting
through missed steps. At the extreme `swing = 1` an odd step lands on the next
even step's grid position; that coincidence is the documented maximum, not a
triplet feel.

### 4.6 Seed and reproducibility

Ghost selection uses a SplitMix64-derived integer key built from
`(seed, bar_index, modifier name)`. Ambient `random` state is never used. Same
preset + modifiers + seed + bar index → exactly the same ghosts. Different
seeds differ where a modifier is nonzero. The seed is logged and shown in the
GUI; there is no full session snapshot in 8.2 (that is 8.5).

### 4.7 Grid cap and meter changes

- Editable columns are capped at **32**.
- Meters with more than 32 16th steps (e.g. 16/2 = 128) generate the whole bar
  by repeating the 32-step grid; the GUI only edits the first 32 columns.
- A meter change rebuilds the grid from the current preset and logs
  `[GROOVE] grid reloaded for meter=… steps=…`. Manual edits are discarded;
  modifier values and the seed survive.

---

## 5. Beat-quantized state changes

### 5.1 What is quantized

While the transport is **running**, these apply at the next **quarter-note
beat** of the Python `RhythmEngine`/`BeatClock`:

| Action | Keys/coalescing |
|---|---|
| Mutation trigger | one pending trigger per beat; repeated requests coalesce and log the count |
| Octave trigger | one pending octave per beat |
| Drone on/off + Arp on/off | last-wins |
| Drums on/off | last-wins |
| Drum rate | last-wins |
| Drum lane | last-wins per MIDI note |
| Groove preset / density / syncopation / swing / probability / seed | last-wins per key |
| Step-grid cell | last-wins per `(lane, step)` |
| Reset grid | last-wins; runs with the groove phase |

Tempo, meter, and transport are **always immediate** — they define the grid.
Continuous mutation knobs, autotune, loop mode/length/Stop/Stop Rec/Clear/Stop
All, free-loop Record/Play, UI-only actions, and beat-mode loop Record/Play are
also immediate; beat-mode loops are already quantized by sclang and are **not**
double-queued in Python. The Stage 6 arp keeps its fixed 0.25 s/0.20 s timing.

### 5.2 Stopped transport

When the transport is **stopped**, every quantized action applies immediately.
This is mandatory Stage 7/8.1 compatibility: the GUI and direct API behave
exactly as before when nothing is playing.

### 5.3 Latency

The wait is up to one quarter beat: 0.5 s at 120 BPM, 3.0 s at 20 BPM. This is
intentional. Stopping the transport makes changes immediate.

The drum rate (half/normal/double) does **not** change the quantization grid; a
half-time drum change still lands on the next quarter-note beat.

### 5.4 Order and coalescing

At a beat, actions run in this deterministic order:

1. mutation
2. octave
3. groove (preset, reset, modifiers, seed, cells)
4. drum enable/rate/lanes
5. drone/arp enables

Within a phase, request order is kept, so a later request generally wins over
an earlier one affecting the same state. Same-key requests coalesce last-wins
and increment the logged request count; different keys all run.

Mutation requests are one per beat: pressing Mutate repeatedly before the beat
collapses into one trigger and logs `coalesced N mutation requests`. The
mutation is applied at the beat, so exactly one `/ji/freqset/update` is sent and
the old set remains visible/audible until then.

### 5.5 Tempo changes and transport stop

- If the tempo changes with actions pending, their next beat is recomputed at
  the **new** tempo. Nothing is applied early and nothing is dropped.
- On transport stop, pending actions are **flushed immediately** in
  deterministic order and logged, then the normal transport-stop path stops the
  drums/beat loops. The last command is not lost, and a stale queue cannot
  carry into the next start.
- On transport start the queue is empty by construction.

### 5.6 Clocks and drift

Python's beat quantizer and sclang's `TempoClock` are separate. Quantized
mutations/drums/groove land on Python's quarter beat; beat-mode loops land on
sclang's. Over a long session they can drift by a few milliseconds. The existing
**Resync** action re-broadcasts tempo/meter/transport, and a transport stop→start
re-anchors both clocks. This is documented, not hidden.

### 5.7 Drums on/off at a beat

Turning drums **on** at the beat starts the scheduler at step 0 for a clean,
bar-like start. Turning drums **off** at the beat stops the scheduler and sends
note-offs for held hits plus CC 123. Lane toggles affect the next step at/after
the beat and never leave a stuck note; the existing gate note-offs still run.

### 5.8 Stage 8.1 beat-mode loop fix

Before this fix, `~ji_schedule_loop_action` in `ui/control_surface.scd` ran the
scheduled action as `action.value` with **no argument**. The beat-mode actions
`~ji_record_loop_action` and `~ji_play_loop_action` are declared with
`|index|`, so they fired with `index = nil`; the record/play call then did
nothing. Free loops bypassed the scheduler and worked, which made the failure
look like "synced Rec does nothing".

A standalone live patch is included at
`ui/control_surface_8.2.2_patch.scd`; evaluate it after the control surface and
it redefines only `~ji_schedule_loop_action` (no mode/length/buffer reset). The
fix itself is one line: `action.value(index)`. Re-evaluate `ui/control_surface.scd`
in sclang after evaluating the live rig. The install line now includes a version
marker:

```text
[UI] control surface 8.2.2 installed -> 127.0.0.1:9001; autotune + loop + rhythm + status OSC ready
```

If it does not say **8.2.2**, sclang evaluated a stale editor buffer or an old
file. Close/revert the editor buffer, reopen `ui/control_surface.scd` from disk,
and evaluate it again. The `[RHYTHM] loop N record fired on beat ...` log should
then be followed by the normal `[LOOP N] recording ...` lines. Beat-mode Play is
fixed by the same change.

---

## 6. Diagnostics

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

The queue logs one line per queue/apply/flush/config change, not per pending
action. The drum scheduler logs swing once per start (the first delayed step)
and never logs per-step hits during performance.

---

## 7. Failure modes

| Symptom | Likely cause | Fix |
|---|---|---|
| Changes feel late | Transport is running; changes wait for the next quarter beat (up to 3 s at 20 BPM) | Expected. Stop the transport for immediate changes |
| Density/syncopation knobs do nothing | Probability is 0, which mutes generated ghosts | Raise Probability above 0 |
| Preset change wiped my grid edits | Selecting a preset reloads its base grid | Expected; re-edit or use Reset grid deliberately |
| Grid edits disappeared after a meter change | Meter change reloads the preset grid | Expected; documented |
| Grid columns stop at 32 in a long meter | 8.2 editable cap | Expected; generation repeats the 32 columns |
| Mutate pressed twice only moves once | Same-key coalescing before the beat | Expected; the log shows `coalesced 2` |
| Pending line says `quantized off` | Transport stopped | Press Play |
| Beat-mode loop play does nothing | Transport stopped | Press Play (transport) |
| Drums off at the beat leaves silence | `Drums on` is off, or all lanes are off | Check the Rhythm group |
| Swing sounds odd at 1.0 | 1.0 is half a step late, not triplet swing | Use 0.4–0.7 for a shuffle |
| Python groove and loop feel drift | Separate clocks | Resync, or stop→start the transport |
| UI shows waiting labels | Bridge/sclang not running or wrong port | Start the missing process; check CLI ports |

---

## 8. Tests

Run from `ji-live/`.

### 8.1 New pure tests

```bash
uv run --with python-osc python tests/test_quantize.py
uv run --with python-osc python tests/test_groove.py
```

- `test_quantize.py`: stopped→immediate; exact next-quarter-beat application;
  boundary waits; coalescing/log count; deterministic phase order; tempo-change
  reschedule; transport-stop flush; `JIBridge.__init__` creates no thread;
  clean start/stop; `on_change`/snapshot countdown; a short real-thread wake
  check.
- `test_groove.py`: `four` matches 8.1 for 4/4/3/4/6/8/5/4/7/8/2/4/12/8; the
  other presets match their documented templates; truncate/tile; density/
  syncopation/probability bounds and zero identity; core hits are never
  dropped; swing 0/1; grid bit/mask/32-int encoding and validation; 32-column
  cap; seeded reproducibility and ambient-random independence; stateful
  preset/grid/meter transitions; drum-engine integration.

### 8.2 Existing harnesses (must still pass)

```bash
uv run --with python-osc python tests/test_mutation.py
uv run --with python-osc python tests/test_mutation_controls.py
uv run --with python-osc python tests/test_rhythm.py
python tests/test_mpe.py --pure
uv run --with python-osc python tests/test_ui.py --pure
```

In particular:
- `MutationOSCServer.trigger()` stays immediate when called directly;
- `DrumEngine.set_lane()`/`set_rate()`/`set_enabled()`/`set_meter()` stay
  immediate at the engine level;
- with the transport stopped, bridge requests apply immediately;
- `JIBridge(...)` still constructs without mido/MIDI enabled and without
  starting threads;
- `--midi` without `--drums` still does not require Bus 3.

### 8.3 Optional headless Qt test

```bash
QT_QPA_PLATFORM=offscreen uv run --with python-osc --with PyQt6 \
  python tests/test_ui.py --qt
```

Adds checks for the Groove group/preset/modifier controls, hidden-by-default
and lazy step grid, cell-click `set_step`, signal-blocked grid/groove sync, the
pending status line, running-vs-stopped mutation feedback, and clean close. It
skips cleanly if PyQt6/Qt is unavailable (the agent container has no
`libEGL.so.1`, so it skipped there).

### 8.4 Human sclang/audio checks

Audio, MIDI, Ableton, IAC, and live timing are human-only. No new sclang file is required, but re-evaluate `ui/control_surface.scd` to
pick up the §5.8 beat-mode fix. Use the checklist below.

---

## 9. Human acceptance checklist

1. With transport **stopped**, Mutate/Octave and all toggles still act
   immediately (Stage 7/8.1 behavior).
2. With transport **playing**, Mutate applies exactly on the next beat; the old
   set is visible/audible until then; the pending line shows it.
3. Drone/Arp/drums on/off, drum rate, and lane toggles apply on the next beat;
   no double-trigger and no stuck notes.
4. Groove preset, density, syncopation, swing, and probability changes apply on
   the next beat and are audible; 0 settings reproduce the 8.1 groove.
5. The step grid edits individual cells while playing; edits land on the next
   beat; the grid matches what is heard; it is hidden by default.
6. Swing is audible at high values and disappears at 0; timing does not drift
   or burst.
7. The same seed/preset/modifiers reproduce the same groove; the seed is
   documented and visible.
8. Free loops are unchanged and immediate; beat-mode Record/Play now actually
   runs at sclang's next beat (see §5.8) and still quantizes to that beat.
9. Mutation, autotune, loop, drone/arp, and existing GUI behavior do not
   regress; the window remains compact/usable and scrolls instead of clipping.
10. No audio dropouts, no stuck MIDI notes; CPU remains within budget.
11. Record the result. **Done: the owner confirmed the groove and quantized-
    state behavior and no audio/MIDI regressions on 2026-10-31.**

---

## 10. What the agent verified (and did not)

Verified in the agent environment (Linux container, Python via `uv`):

- `tests/test_quantize.py` — passed.
- `tests/test_groove.py` — passed.
- `tests/test_rhythm.py` — passed (including the new groove config/swing
  scheduler check; fake MIDI output; no mido).
- `tests/test_ui.py --pure` — passed.
- `tests/test_mutation.py`, `tests/test_mutation_controls.py`,
  `tests/test_mpe.py --pure` — passed.
- The Qt test **skipped**: the container lacks `libEGL.so.1`. The live window
  was therefore not executed by the agent.

Not verified by the agent: audio, MIDI, IAC Bus 3, Ableton, the drum rack,
swing and quantization on a live clock, GUI behavior on the human's machine,
and the human listening test. Those were subsequently confirmed by the owner
on 2026-10-31. **Stage 8.2 is accepted.**

---

## 11. Limitations / decisions

- **Latency:** quantized changes wait up to one quarter beat (3 s at 20 BPM).
  Intentional; stopping the transport makes changes immediate.
- **Clock drift:** Python and sclang clocks are separate; Resync or stop→start
  re-anchors them. Documented, not hidden.
- **Coalescing:** repeated same-key requests before the beat collapse; the log
  shows the count. Different keys all run in the documented order.
- **Transport-stop flush:** pending actions apply immediately on stop; the last
  command is not lost.
- **Grid cap:** editable columns are capped at 32; longer meters repeat the
  32-step grid and log the cap.
- **Preset vs. grid:** selecting a preset (or changing meter/resetting) replaces
  manual grid edits.
- **Probability:** only generated ghosts are gated; core hits always play. At
  probability 0, density/syncopation are inaudible by design.
- **Swing:** max 50% of a 16th step; no triplet/swing subdivision.
- **Drums on at a beat** starts at step 0.
- **No full state save/recall** in 8.2 (8.5); the groove seed reproduces
  generated ghosts, not the whole session.
- The Stage 6 arp timing is unchanged and not tempo-synced.
- The GUI keeps the compact 8 pt / 32 px layout and the scroll-area safety net;
  the step grid is hidden by default.

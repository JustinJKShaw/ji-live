# Stage 8.5 Agent Prompt — Performance state save/recall

You are implementing **Stage 8.5** of the JI live-performance system. Stages
0–7.1, 8.1, 8.2, 8.3a and 8.3d are human-confirmed; 8.3b/8.3c are in live use;
8.4 (home sets + smooth return) is built and awaiting its human checklist.
Treat every documented behavior as the accepted baseline and do not regress it.

Stage 8.5 lets the player **save the whole performance configuration under a
name and recall it later, without restarting anything**:

> "i want various states to be saved and recalled (no need for loop recall)."
>
> — owner, 2026-11-01

**Scope change from the old design-doc plan:** the planned 8.5 mentioned loop
settings. The owner has explicitly excluded loop recall. **Loops are out of
scope** (§3.3), as are the sclang-owned autotune parameters and the live
transport state. Everything else that makes the rig sound the way it does is in.

**Concurrency:** this prompt and the 8.4.1 glide patch both edit
`control/ji_bridge.py`, `ui/ji_ui.py`, `tests/test_ui.py` and the docs. Run
8.4.1 first, then this stage, in the same working tree; do not run both agents
concurrently against one tree. If 8.4.1 is present, a state load must cancel an
active glide (§3.2, §6.2).

Do not claim human audio acceptance. The owner is the authority.

---

## 1. Read these files first

Read the code and docs, not just the summaries:

```text
docs/design_doc_10.28.10.50        §2.7, §6.2, §6.5, §6.14–§6.19, §7.1, §8.9,
                                   §9, §13, §14
docs/START_HERE.md                 authoritative start/stop/flag sheet
docs/GUI_PARAMETERS.md             every GUI control and its range
docs/README_stage8_4.md            home sets, return, state message
docs/README_stage8_3d.md           lead + drone settings (accepted)
docs/README_stage8_3c.md           arp sync/evolve (does not change here)
docs/README_stage8_3.md            bass settings
docs/README_stage8_2.md            groove model
docs/prompts/PROMPT_stage8_4_home_return.md
                                   most recent stage prompt: structure and tone
control/mutation.py                SetSnapshot, snapshot/apply_snapshot/reseed
control/home.py                    HomeConfig/HomeState/HomeManager
control/quantize.py                BeatQuantizer phases and hooks
control/ji_bridge.py               the owner of almost every value a state holds
control/osc_server.py              OSC handlers/callbacks
control/rhythm.py                  BPM/meter/transport
control/drums.py                   drum config, lanes, groove ownership
control/groove.py                  GrooveSpec/Grid and its status/grid payloads
control/bass.py                    BassSettings.create(**values)
control/arp.py                     ArpSettings.create(**values)
control/lead.py                    LeadSettings.create(**values), variation index
control/drone.py                   DroneSettings.create(**values)
control/midi_rig.py                how a settings change reaches each bus
ui/osc_control.py                  pure builders/parsers/store
ui/ji_ui.py                        compact PyQt6 window
run/ji_station.py                  one Python launcher (flags + --check)
config/*.json                      shipped defaults (not user states)
tests/test_home.py                 the most recent pure harness: structure to copy
tests/test_mutation.py             must not change
README.md                          current run commands/status
```

Run Python tests from `ji-live/`.

---

## 2. What you must preserve

- Stage 5.5 audio (2 in / 16 out, 48 kHz, blockSize 64), Stage 6 MPE (Bus 1
  drone, Bus 2 arp, member channel 2, 48 st), 8.1 rhythm/drums (Bus 3), 8.3a
  bass (Bus 4), 8.3d lead (Bus 5) and drone modes, and 8.4 home sets/return are
  in use or accepted. Do not change their contracts.
- **`MutationEngine.propose_mutation()` / `mutate_many()` behavior must not
  change.** `tests/test_mutation.py` must pass **unmodified**; if that is
  genuinely impossible, stop and document why instead of editing it.
- The quantization rule is absolute: while the transport is stopped every
  quantized action applies immediately; while it runs, actions wait for the next
  quarter beat. Direct engine setters and `MutationOSCServer.trigger()` remain
  immediate; quantization belongs to the bridge/UI request layer.
- One `/ji/freqset/update` per applied change, never per note. A state recall is
  **one** set change and **one** update.
- Keep the compact 8 pt / 32 px / scroll-area GUI; do not clip Status. The
  window is currently 1280×768 with the Home row; add at most one compact row.
- Keep the one-sclang + one-Python start path and update `docs/START_HERE.md`.
- No sclang change, no SynthDef change, no audio DSP, no new MIDI voice.
- **No loop recall** and **no autotune recall** (§3.3). Do not add loop or
  autotune fields to the state file.
- State files are user data, not shipped config: never write into `config/`.

Existing tests that must keep passing:

```bash
uv run --with python-osc python tests/test_mutation.py
uv run --with python-osc python tests/test_mutation_controls.py
uv run --with python-osc python tests/test_rhythm.py
uv run --with python-osc python tests/test_groove.py
uv run --with python-osc python tests/test_quantize.py
uv run --with python-osc python tests/test_bass.py
uv run --with python-osc python tests/test_arp.py
uv run --with python-osc python tests/test_lead.py
uv run --with python-osc python tests/test_drone.py
uv run --with python-osc python tests/test_home.py
uv run --with python-osc python tests/test_startup.py
python tests/test_mpe.py --pure
uv run --with python-osc python tests/test_ui.py --pure
```

---

## 3. What a "performance state" is

### 3.1 In scope (everything the bridge owns)

A state is a **named, versioned, human-readable JSON snapshot** of:

```text
freqset    ratios (octave-reduced, sorted, unique), anchor, analysis octaves,
           fundamental
mutation   randomness, dissonance_bias, stepwise_bias, temperature,
           anchor_mobility, change_count, glide_time (and max_changes, so a
           recalled file cannot exceed the engine's cap)
home       4 slots (snapshot or null), active_slot, auto, every, seed,
           max_drift, return_glide, journey_seed, journey_mutations
rhythm     bpm, meter_num, meter_den            (NOT transport running)
drums      enabled, rate, lane_mask
groove     preset, density, syncopation, swing, probability, seed, and the
           32-column grid payload (per-lane mask_lo/mask_hi)
bass       every BassSettings field
arp        every ArpSettings field
lead       every LeadSettings field + variation_index
drone      every DroneSettings field
```

Notes:

- `freqset` uses `control.mutation.SetSnapshot` semantics; restoring it is
  `engine.apply_snapshot(...)` and must also force the saved anchor.
- `home` restores the slots and the active home. The 64-deep Back history is
  **not** saved (it is a session trace, not a colour); document that.
- `drums.lane_mask` is the same 16-bit mask `/ji/drums/state` reports. Groove
  grid: reuse `Groove.grid.payload()` / the `/ji/drums/grid` layout (32 ints:
  for lane 0..15, `[mask_lo, mask_hi]`) rather than inventing a second format.
- `lead.variation_index` is saved because it selects which transformation is
  currently applied; restoring the index and the settings must reproduce the
  motif (it is generated from `seed`, `variation`, `grid`, `phrase_bars`,
  `density`, `octave_range`, and the set).
- `bass`/`arp`/`lead`/`drone` are exactly the `*Settings.create(**values)`
  field sets. Build them through the existing validators — never hand-write a
  second validation layer.

### 3.2 Not in scope (live actions)

- **Transport** (`running`): a state is a colour, not a play command. Loading a
  state never starts or stops the transport; the current transport state stays.
- **Loop buffers / recording / playback**: excluded by the owner.
- **Autotune** parameters: sclang-owned; unchanged in this stage. Do not send
  `/ji/autotune/set` from a recall.
- **Pending quantizer actions**, the return in flight, and the Back history.
  A load **cancels a pending return** first (one log line, same rule as Back).
  If the **8.4.1 stepwise-glide patch** is present, a load also **cancels an
  active glide** first (the glide thread would otherwise overwrite the recalled
  set with its own final step); call its `_cancel_glide("state load")` before
  applying anything.

### 3.3 Explicit exclusions (owner)

> "no need for loop recall"

Do not save or restore loop modes, loop lengths, loop buffers, loop transport,
or the default loop length. Do not add them "for later"; a later stage can add a
new version.

---

## 4. File format and storage

### 4.1 Format (version 1)

```json
{
  "format": "ji-performance-state",
  "version": 1,
  "saved_at": "2026-11-02T02:00:00-05:00",
  "name": "A-drone",
  "freqset": {
    "ratios": [1.0, 1.125, 1.25],
    "anchor": 1.0,
    "octaves": [[1.0, 0], [1.125, 0], [1.25, 0]],
    "fundamental": 220.0
  },
  "mutation": {
    "randomness": 0.55, "dissonance_bias": 0.0, "stepwise_bias": 0.25,
    "temperature": 0.0, "anchor_mobility": 0.25, "change_count": 1,
    "glide_time": 0.15, "max_changes": 3
  },
  "home": {
    "slots": [null, null, null, null],
    "active_slot": -1, "auto": false, "every": 8, "seed": 1,
    "max_drift": 0, "return_glide": 0.06,
    "journey_seed": 1, "journey_mutations": 0
  },
  "rhythm": {"bpm": 120.0, "meter_num": 4, "meter_den": 4},
  "drums": {"enabled": false, "rate": 1.0, "lane_mask": 69},
  "groove": {
    "preset": "four", "density": 0.0, "syncopation": 0.0,
    "swing": 0.0, "probability": 0.0, "seed": 1,
    "grid": [0, 0, 0, 0]
  },
  "bass": {"enabled": false, "preset": "pulse", "grid": "1/4"},
  "arp": {"step_seconds": 0.25, "gate_seconds": 0.2, "velocity": 90},
  "lead": {"enabled": false, "grid": "1/8", "variation_index": 0},
  "drone": {"mode": "hold", "velocity": 90}
}
```

The example above is abbreviated: `grid` holds the full 32 ints from
`/ji/drums/grid`, and every `bass`/`arp`/`lead`/`drone` section carries **all**
of the fields of its `*Settings` class (§3.1).

Rules:

- `format` and `version` are required and checked. A newer/unknown version is
  refused with one clear line; an older supported version is migrated (there is
  only version 1 now, so the loader must simply be structured for it).
- Missing optional sections/keys default to the shipped values (so a hand-edited
  or older file still loads). Unknown keys are ignored.
- The whole document is parsed and validated **before** anything is applied. Any
  invalid value aborts the load with one `[STATE]` line and changes nothing.
- `saved_at` is informational; do not use it for ordering. The list is sorted by
  name (case-insensitive, then case-sensitive for ties).

### 4.2 Storage

```text
ji-live/states/<name>.json        default directory, created on demand
```

- `--state-dir <path>` on the launcher and the bridge overrides it; relative
  paths resolve from the project root exactly like the other config paths.
- Name rules: 1..40 characters after trimming, first character alphanumeric,
  then `[A-Za-z0-9 ._-]`; no `/`, `\`, `..`, control characters or a leading
  dot. Reject with one line. The filename is `<name>.json`.
- **Atomic save:** write `<name>.json.tmp` in the same directory, `flush`,
  `os.replace` to the final name. A failed save must leave the previous file
  intact and log one line.
- A missing directory is created on the first save (and by `--check`); a
  read-only/missing directory is reported, never fatal to the rig.

---

## 5. `control/state.py` (new pure module)

Standard library only (no OSC, PyQt6, mido, or threads). Suggested shape:

```text
StateError            one exception type for name/format/validation problems
StateName             validate/normalise a name
PerformanceState      immutable parsed state; from_mapping / to_mapping
StateLibrary          directory listing/save/load/delete (path-safe, atomic)
STATE_FORMAT / STATE_VERSION / DEFAULT_STATE_DIR
```

`PerformanceState` must be buildable from a mapping and serialisable back to one,
and must round-trip **every** in-scope field. Keep the mapping close to the
example above; do not flatten it into one namespace.

Do not import `control.ji_bridge` from `control/state.py` (the bridge imports the
state module, not the other way round). Validate the settings sections through
`BassSettings.create`, `ArpSettings.create`, `LeadSettings.create`,
`DroneSettings.create`, `HomeConfig.from_mapping`, `SetSnapshot.create`,
`GrooveSpec`/`GrooveGrid` validation, and `RhythmState.create` where practical.

---

## 6. Bridge, OSC, quantize

### 6.1 Quantize phase

Add `PHASE_STATE = 10` **last** (`home, mutation, octave, groove, drums, bass,
arp, lead, drone, midi, state`), so a recall at a beat wins over everything
requested for that beat. Update the module docstring, `PHASE_NAMES`,
`PHASE_ORDER` and `__all__`. Add one assertion to `tests/test_quantize.py` that
`PHASE_STATE` runs after `PHASE_MIDI` at the same beat.

### 6.2 Bridge

```text
state_dir        path/mapping for the state directory (optional)
states           StateLibrary instance (always constructed; an empty dir costs
                 nothing)
request_state_save(name)
request_state_load(name)
request_state_delete(name)
request_state_list()
```

- Each request is beat-quantized on `PHASE_STATE`; while stopped it applies
  immediately. Save/delete/list are file operations and may apply immediately
  even while running (they do not change sound) — **decide and document**; the
  suggested rule is: save/delete/list immediate, load quantized.
- A load is **one atomic action**:
  1. cancel a pending return (one line); if the 8.4.1 glide patch is present,
     cancel an active glide too (its thread must not overwrite the load);
  2. apply the frequency set (`apply_snapshot`) and force the saved anchor;
  3. apply mutation controls (add public `MutationOSCServer` setters if needed —
     e.g. `set_randomness`/`set_dissonance_bias`/`set_stepwise_bias` — and keep
     the existing OSC handler behavior);
  4. restore home slots/active/config and journey seed/counter, then
     `engine.reseed(journey_seed)` so the recalled colour starts a deterministic
     stream (document that a mid-journey RNG stream is not resumed exactly);
  5. restore rhythm bpm/meter and broadcast;
  6. restore drums enable/rate/lanes/groove;
  7. restore bass/arp/lead/drone settings and enable flags through the existing
     `set_*_setting` paths;
  8. send exactly one `send_set(glide)` using the restored `mutation.glide_time`
     (a load is a jump, not a smooth return), one `/ji/home/state`, one
     `/ji/state/state`, and the usual voice status messages.
- A missing bus on recall must degrade one voice only (the existing rules):
  log one line, apply what can be applied, never take the rig down.
- A load must be idempotent: loading the same file twice changes nothing the
  second time (and still logs one line).
- Record the recalled snapshot in the Back history (a load is an applied set
  change); a capture/state save is not.

### 6.3 OSC (UI → bridge)

```text
/ji/state/save      string name
/ji/state/load      string name
/ji/state/delete    string name
/ji/state/request   none      (ask for a fresh list + status)
```

Handlers validate names/arity, log one line on error, and never raise out of the
OSC thread. Reply messages (Bridge → UI):

```text
/ji/state/list   [int count, string name_1 ... string name_N]   (sorted)
/ji/state/state  [int count, string last_saved, string last_loaded, string message]
```

`message` is a short human line (`"saved"`, `"loaded"`, `"no such state"`,
`"invalid state: ..."`), never a traceback. Send both at start, on
`/ji/ui/hello`, and after every save/load/delete/request.

---

## 7. GUI

Add one compact **State** group (one control row + the existing Status message
line is enough; do not clip Status):

- `Name` editable combo (the saved names, sorted), `Save`, `Load`, `Delete`,
  `Refresh`.
- Status line: `State: 3 saved | saved A-drone | loaded A-drone` (or
  `State: none saved`, or `State: error ...`).
- Buttons send immediately; the bridge owns quantization. Incoming
  `/ji/state/list`/`/ji/state/state` sync the combo/status with signals blocked
  (no feedback OSC). Disabled states: `Save` with an empty name, `Load`/`Delete`
  when the name is not in the list.
- Keep the window compact (one extra row is allowed; the scroll area remains the
  safety net). If the window must grow, document the new default size.

---

## 8. Launcher and `--check`

- `--state-dir <path>` on `run/ji_station.py`, forwarded to the bridge.
- `--check` prints `[CHECK] state dir ok: path=... states=N` and reports a
  directory that cannot be created/written as a problem. It must not create a
  file unless the directory is missing (creating the empty directory is fine).
- `docs/START_HERE.md` gains the flag row, a short "Save / recall a state"
  procedure, and troubleshooting rows (invalid name, corrupt file, read-only
  dir, a recalled voice with its bus missing).

---

## 9. Tests

### 9.1 New pure tests

```bash
uv run --with python-osc python tests/test_state.py
```

Cover at least:

- name validation/normalisation (length, characters, path traversal, leading
  dot, empty, duplicate case);
- `PerformanceState` round-trip of **every** in-scope field, including a 4-slot
  home section with one empty slot and one non-default anchor/voicing;
- missing sections/keys default; unknown keys ignored; wrong `format`/`version`
  rejected; one bad field aborts the whole load and leaves the bridge untouched;
- `StateLibrary`: save/list/load/delete, atomic write (no `.tmp` left), save over
  an existing name replaces it, delete a missing name is a logged no-op,
  read-only/missing directory errors are non-fatal;
- bridge stopped → load is immediate, exactly one `/ji/freqset/update`, one
  `/ji/home/state`, one `/ji/state/state`;
- bridge running → load waits for the next beat, exactly one update, exactly one
  beat; a load cancels a pending return (one line);
- restored values: ratios/anchor/octaves, mutation controls, home slots/active,
  rhythm, drums/groove grid, bass/arp/lead/drone settings, lead variation index;
- a bridge restart with the same state dir can load a state saved by the first
  bridge (construct bridge B after bridge A);
- a missing drum/bass/arp/lead/drone bus degrades one voice with one line;
- no loop/autotune keys are written or read (assert the JSON has no `loop` or
  `autotune` key);
- no `mido` import during pure construction;
- `/ji/state/*` handlers (arity, bad names, soft callbacks) and the two reply
  message shapes.

### 9.2 Existing tests

All listed in §2 must pass. `tests/test_mutation.py` unmodified. Update
`tests/test_quantize.py` (state after midi), `tests/test_ui.py` (builders,
parsers, store, controller, Qt State group), `tests/test_startup.py`
(`--state-dir`, `--check` output, START_HERE flags).

### 9.3 Optional headless Qt test

```bash
QT_QPA_PLATFORM=offscreen uv run --with python-osc --with PyQt6 \
  python tests/test_ui.py --qt
```

Add State-group checks: defaults, signal-blocked sync, no feedback OSC,
disabled states, error line, and that the window still fits. Skip cleanly if
PyQt6/Qt is unavailable.

---

## 10. Human acceptance checklist

1. Save a state `A` with a distinctive set, tempo, groove and voice settings.
2. Change the set, controls, tempo/meter, groove, and bass/arp/lead/drone
   settings (and capture a different home).
3. Load `A` while the transport is stopped: everything returns at once, the
   Status set matches, and the rig sounds like `A` again.
4. Load `A` while the transport runs: it lands on the next beat with one set
   update, no burst, no stuck note (drone retunes, lead re-projects, arp/bass
   follow).
5. Save `B` with a different colour; switch `A`/`B` repeatedly.
6. Quit the bridge, restart it, load `A` from the GUI: the state is still there.
7. Delete a state; list shows it gone; loading it logs one clear error.
8. An invalid/hand-corrupted file is refused with one line and changes nothing.
9. Loop audio, loop settings and autotune parameters are **not** touched by a
   save or a load.
10. Drone/arp/bass/lead/drums, groove, groove-quantize, home sets/return and the
    accepted 8.3d/8.4 controls do not regress; the window stays compact.
11. Record the result. **8.5 is not complete until the human confirms save/recall
    across a restart with no audio/MIDI regressions.**

---

## 11. Diagnostics

One line per event, never per note:

```text
[STATE] saved A-drone (12 fields, states/)
[STATE] loaded A-drone
[STATE] load A-drone: distance 0, 1 set update
[STATE] deleted A-drone
[STATE] gap: no state named "nope" (3 saved)
[STATE] invalid state "x": anchor is not in the class set
[STATE] save failed: states/ is not writable
[STATE] return cancelled by state load
```

Use the existing `[QUANT]`, `[BRIDGE]`, `[MUTATION]` and `[HOME]` lines unchanged.

---

## 12. Risks and decisions to document

- **Partial recall.** Validate the whole file first; a bad field must never leave
  half the rig changed.
- **Format drift.** `format`/`version` exist from day one; a future stage adds
  fields without breaking old files. Never reorder semantics silently.
- **Transport is not recalled.** A state is a colour, not a play command. Say it.
- **Loops and autotune are excluded** by the owner. Say it in the README and the
  GUI tooltip; do not half-build them.
- **Home history is not saved.** Slots and the active home are; the 64-deep Back
  trace is session state.
- **Journey RNG.** Reseeding on recall makes the next stream deterministic from
  the saved seed, but a mid-journey stream is not resumed exactly. Say it.
- **Missing buses.** A recalled voice whose bus is missing degrades alone.
- **File safety.** Names are path-safe; writes are atomic; a failed save leaves
  the old file intact.
- **GUI density.** One more row; keep Status visible.
- **Docs drift.** `START_HERE.md`, `--help`, the stage README and the design doc
  are part of the acceptance.

---

## 13. Deliverables

```text
ji-live/
├── control/
│   ├── state.py               NEW: name rules, PerformanceState, StateLibrary
│   ├── quantize.py            MOD: PHASE_STATE (last)
│   ├── osc_server.py          MOD: /ji/state/* handlers (+ public control setters
│   │                          if needed)
│   └── ji_bridge.py           MOD: StateLibrary, requests, atomic load, state msgs
├── ui/
│   ├── osc_control.py         MOD: builders/parsers/StateStatus/store/controller
│   └── ji_ui.py               MOD: compact State group
├── run/ji_station.py          MOD: --state-dir, --check
├── tests/
│   ├── test_state.py          NEW
│   ├── test_quantize.py       MOD (state after midi)
│   ├── test_ui.py             MOD
│   └── test_startup.py        MOD
├── docs/
│   ├── README_stage8_5.md     NEW: format, procedure, controls, human checklist
│   ├── START_HERE.md          MOD: --state-dir, Save/Load procedure, troubleshooting
│   ├── README_stage8_4.md     MOD: link forward to 8.5
│   └── design_doc_10.28.10.50 MOD: §6.20 (state), §7.1, §8.9, §9, §13, §14
└── README.md                  MOD: status, links, layout
```

Runtime data lives in `ji-live/states/` (created on demand) and is **not** part
of the repository's shipped config.

---

## 14. Self-review requirement (final response)

Provide:

- Files added/changed.
- For each requirement above, the code that satisfies it or a concrete concern.
- "If I had to bet on which line is most likely to fail on the human's machine,
  which is it, and why? Give a one-line verification command."
- Assumptions, max 5 bullets.
- Explicit statement that audio, MIDI, Ableton, IAC, sclang timing and the live
  GUI were not tested by the agent; 8.5 awaits human acceptance.
- The exact one-sclang + one-Python start sequence, copied from
  `docs/START_HERE.md` after your updates, so the human can paste it.

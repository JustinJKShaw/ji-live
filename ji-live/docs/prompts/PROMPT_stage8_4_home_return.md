# Stage 8.4 Agent Prompt — Home Sets, Deterministic Journeys, and Smooth Return

You are implementing **Stage 8.4** of the JI live-performance system. Stages
0–7.1, 8.1, 8.2 and 8.3a are human-confirmed; 8.3b/8.3c are in live use; and the
owner approved **8.3d** (lead/melody + drone modes) on **2026-11-01**. Treat
every documented behavior below as the accepted baseline and do not regress it.

Stage 8.4 makes **movement through frequency sets repeatable and musical**:

> "i want to be able to return to certain states after moving around through
> frequency sets … a toggleable feature where given a set number of mutations and
> a seed, the frequency set will mutate deterministically and return smoothly
> (i.e. to return should not take more than the minimum number of allowed
> changes per mutation) to a home set, after a home set is selected … the goal of
> this is to allow for something akin to set chord progressions. additionally, a
> 'back' button that returns to the immediately prior previous state may be
> useful."
>
> — owner, 2026-11-01

Do not claim human audio acceptance. The owner is the authority.

The design doc's planned 8.4 was "pattern/sequencing mutation: repeating
frequency-set and MIDI patterns, seed/recall of generated patterns". This stage
is that idea, scoped to the *frequency-set* layer first. MIDI pattern recall and
full session save/recall stay in 8.5.

---

## 1. Read these files first

Read the code and docs, not just the summaries:

```text
docs/design_doc_10.28.10.50        §2.7, §6.2, §6.5, §6.11–§6.18, §7.1, §8.9,
                                   §9, §13, §14
docs/START_HERE.md                 authoritative start/stop/flag sheet
docs/README_stage8_3d.md           accepted lead + drone baseline (approved)
docs/README_stage8_3c.md           arp sync/evolve (does not change here)
docs/README_stage8_3.md            bass voice pattern to copy
docs/README_stage8_2.md            groove + quantize model
docs/prompts/PROMPT_stage8_3d_lead_drone.md
                                   most recent stage prompt: structure and tone
control/mutation.py                THE file this stage builds on: MutationEvent,
                                   MutationBatch, submit/applly, anchor rules,
                                   engine RNG, set_ratios/set_anchor
control/quantize.py                BeatQuantizer phases and hooks
control/ji_bridge.py               engine ownership, send_set, status, requests
control/osc_server.py              OSC handlers/callbacks
control/midi_rig.py                drone/arp/bass/lead/drums fan-out
control/rhythm.py                  BPM/meter/transport, next_beat_time, beat_at
control/lead.py                    set-relative motif (must keep its identity)
control/drone.py                   hold/pulse/bend/sequence (must keep working)
ui/osc_control.py                  pure builders/parsers/store
ui/ji_ui.py                        compact PyQt6 window
run/ji_station.py                  one Python launcher (flags + --check)
config/*.json                      existing config patterns
tests/test_mutation.py             mutation determinism/scoring (must not change)
tests/test_mutation_controls.py    control surface
tests/test_quantize.py             queue phases
tests/test_ui.py                   pure + headless Qt
tests/test_startup.py              launcher/start-file/docs checks
README.md                          current run commands/status
```

Run Python tests from `ji-live/`.

---

## 2. What you must preserve

- Stage 5.5 audio (2 in / 16 out, 48 kHz, blockSize 64), Stage 6 MPE (Bus 1
  drone, Bus 2 arp, member channel 2, 48 st), Stage 8.1 rhythm/drums (Bus 3),
  8.3a bass (Bus 4), 8.3d lead (Bus 5) and drone modes (`hold`/`pulse`/`bend`/
  `sequence`) are in use and accepted. Do not change their contracts.
- **`MutationEngine.propose_mutation()` / `mutate_many()` behavior must not
  change.** Existing seeds produce existing sequences; `tests/test_mutation.py`
  and `tests/test_mutation_controls.py` must pass unchanged. If you need new
  engine capabilities, add them (see §4.1) rather than editing existing paths.
- The quantization rule is absolute: while the transport is stopped every
  quantized action applies immediately; while it runs, actions wait for the next
  quarter beat. Direct engine setters and `MutationOSCServer.trigger()` remain
  immediate; quantization belongs to the bridge/UI request layer.
- One `/ji/freqset/update` per applied change, never per note. At most one set
  update per quarter beat from this stage.
- Keep the compact 8 pt / 32 px / scroll-area GUI; do not clip Status.
- Keep the one-sclang + one-Python start path and update `docs/START_HERE.md`.
- No sclang change, no SynthDef change, no audio DSP, no new MIDI voice.

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
uv run --with python-osc python tests/test_startup.py
python tests/test_mpe.py --pure
uv run --with python-osc python tests/test_ui.py --pure
```

---

## 3. The musical model (read this before designing)

The engine state that matters here is a **set snapshot**:

```text
SetSnapshot:
  ratios   tuple[float, ...]   octave-reduced classes, sorted, unique
  anchor   float | None        the protected drone class (always in ratios)
  octaves  tuple[(ratio,int)]  analysis voicing; only needed to restore exactly
```

Two derived quantities drive the whole stage:

```text
remove   = classes in the current set but not in the home set
add      = classes in the home set but not in the current set
distance = max(len(remove), len(add))            # edits needed to land home
batches  = ceil(distance / changes_per_batch)    # beats needed to return
```

`changes_per_batch` is **`MutationEngine.max_changes`** (the hard per-mutation
cap, default 3), *not* the `change_count` UI setting. This is the owner's
"minimum number of allowed changes per mutation": the return must take exactly
`batches` quarter beats, and each beat's batch must carry at most
`max_changes` replacements. A return that takes one extra beat is a bug.

The vocabulary the owner will use, so keep the code and docs consistent:

| Term | Meaning |
|---|---|
| **home set** | a captured snapshot you can return to |
| **slot** | one of four home slots (`A`–`D`) |
| **distance** | classes that still differ from the active home |
| **return** | the beat-spaced sequence of batches that lands exactly on home |
| **journey** | mutations made since the last landing on a home |
| **drift** | optional cap on how far a journey may stray from home |
| **back** | restore the immediately previous snapshot |

Musical intent: a 7-class JI set is a small harmonic world. A *home* is a
stable chord/colour; a journey is the drifting mutation the rig is already good
at; a return is a short, audible, voice-led way back (2–4 beats, not a jump).
Four slots plus a cycle gives **set chord progressions**: A → B → C → A, each
transition played as a smooth return.

---

## 4. Part A — Engine additions (additive only)

### 4.1 Two small, additive engine methods

`MutationEngine.apply(MutationEvent)` already supports arbitrary targeted
add/remove/replace; you do **not** need to change the mutation algorithm. Add
only:

```python
def reseed(self, seed: int | None) -> None:
    """Replace the engine RNG (used when a journey starts)."""

def apply_snapshot(self, snapshot) -> None:
    """Set ratios, anchor and voicing octaves exactly (used by Back)."""

def snapshot(self) -> SetSnapshot:
    """Public, immutable copy of (ratios, anchor, octaves)."""
```

`SetSnapshot` may live in `control/mutation.py` or `control/home.py` — if it
lives in `home.py`, `apply_snapshot` must accept any object with those three
fields (duck-typed) or a plain tuple. **Do not** reuse or change the private
`_snapshot()/_restore()` pair used by `mutate_many`'s rollback.

`apply_snapshot` must validate: ratios are positive, octave-reduced, unique,
≤ `max_size`; the anchor (if not `None`) is in the set; octaves are within
`octave_min..octave_max`. On invalid input raise `ValueError` and leave the
state untouched.

### 4.2 Why the algorithm is untouched

The owner guessed this "will likely require modifications to the mutation
algorithm … somehow future states will have to be considered". It does not:
`apply()` is already a targeted edit, so a planner can walk to any set. The
"future state" thinking becomes **policy** at the bridge layer (the optional
drift cap in §6.3), which must default **off** so the accepted mutation feel is
byte-identical. Document this reasoning in `docs/README_stage8_4.md`.

---

## 5. Part B — `control/home.py`: snapshots, distance, planning

Create a pure module (standard library only; no OSC, PyQt6, mido, or threads in
the planner):

```text
HomeConfig    validated config/home_config.json (defaults + slots + seed)
HomeState     immutable status snapshot
HomePlanner   pure distance/pairing/batch planning
HomeManager   slots, active home, journey bookkeeping, history, planner cache
```

### 5.1 Distance and batch planning

```python
def plan_return(current: SetSnapshot, home: SetSnapshot,
                max_changes: int, seed: int) -> list[list[tuple[float|None, float|None]]]:
    """Batches of (remove_ratio, add_ratio); applying them in order lands home."""
```

Rules the tests will pin:

1. `len(plan) == ceil(distance / max_changes)` exactly — never one batch more.
2. Every batch has at most `max_changes` pairs, and at least one edit.
3. After applying all batches, the ratio set equals the home set exactly
   (compare folded, sorted values) and the anchor equals the home anchor.
4. If the home anchor is still in the current set it is **never removed** by
   any batch. If it must be added, it is added in the **last** batch and the
   engine's anchor is set to it after landing.
5. Plan determinism: the same `(current, home, max_changes, seed)` yields the
   same plan. The seed only breaks ties.
6. Sizes may differ (home can be larger/smaller than current): a batch may
   remove without adding or add without removing. Never exceed `max_size` or
   `min_size` mid-plan — compute the pairing so the intermediate sizes stay
   within `[min_size, max_size]`.
7. Pairing is **voice-led**, not arbitrary: remove the class that is most
   distant from home first, and add the home class nearest to the class it
   replaces (log-ratio distance). Document the exact rule.

A worked example belongs in the stage README: e.g. current
`{1, 9/8, 4/3, 3/2, 5/3}` home `{1, 9/8, 5/4, 3/2, 5/3}`, `max_changes = 1`
→ one batch removing `4/3`, adding `5/4`, `1` beat.

### 5.2 History (for Back)

`HomeManager` owns a bounded snapshot history (`deque(maxlen=64)` by default,
config `history_depth` 8..256):

- Record **every applied set change** that goes through the bridge: a mutation,
  a batch, one return step, a Back, a jump, a home capture is *not* a set
  change (it stores a slot, not history).
- `back()` restores the previous snapshot, records the move, returns the
  restored snapshot. Repeated Back walks further back. Back at the oldest entry
  is a logged no-op.
- Any new forward change clears a redo stack; `redo()` is optional (nice to
  have, not required).
- Back during a pending return cancels the return first (log one line).

---

## 6. Part C — Journeys, automatic return, drift

### 6.1 Slots and the active home

Four slots `A`–`D` (`slots`: 4, fixed; `slot_names = ("A","B","C","D")`).

```text
capture(slot)  store the current SetSnapshot in that slot; make it active
go(slot)       smooth return to that slot (plans from the *current* set)
clear(slot)    forget it
```

`active_slot` is the last captured or last `go`-target. `distance` in the state
is always `current vs active home` (0 when there is none).

### 6.2 Journey and automatic return

- `auto` (default **off**), `every` (1..32, default 8), `seed` (0..2³¹−1).
- `start_journey()` reseeds the engine (`reseed(seed)`), sets
  `journey_mutations = 0`, and requires an active home.
- Each applied mutation (a normal mutation, whatever `change_count` is)
  increments `journey_mutations`.
- When `auto` is on and `journey_mutations >= every`, queue a return to the
  active home and reset the counter when it lands.
- Manual `Mutate` always works; if a return is in flight, a manual mutation
  **cancels** it (log `[HOME] return cancelled by mutation`) and the counter
  keeps going from the new position.
- `return` (button/OSC) works with `auto` off: plan from the current set and
  play the return now.
- Determinism: same home + same seed + same number of mutations, with no manual
  intervention, reproduces the same journey (and therefore the same return
  beats). Any manual mutation is part of the sequence. Say this explicitly in
  the README; do not claim "same seed always reproduces" when a human is
  mutating.

### 6.3 Drift cap (the "future states" feature)

`max_drift` (0..`max_size`, default **0 = unlimited**, GUI label `Drift`):

- With `max_drift > 0` and an active home, a proposed mutation whose result
  would put `distance(current, home) > max_drift` is **rejected and retried**
  (up to 8 re-proposals) before being accepted as a last resort.
- The retry loop must not consume the whole beat; if all 8 fail, accept the
  last proposal and log one line (`[HOME] drift cap exceeded (8 tries)`).
- This is a bridge-layer policy; `propose_mutation` itself is untouched.

---

## 7. Part D — Beat-spaced return

The return is **not** a jump and **not** a burst of updates:

- Transport **running**: exactly one batch per quarter beat. Batch *k* is
  applied at the next quarter beat; batch *k+1* is scheduled from inside batch
  *k*'s action (self-rescheduling on `PHASE_HOME`). One
  `send_set(glide)` per batch, with the configured `return_glide` seconds
  (0.02..0.30, default 0.06). After the last batch the bridge logs
  `[HOME] returned to A in 3 beats (3 batches)` and sends one
  `/ji/home/state`.
- Transport **stopped**: the compatibility rule wins — the whole return applies
  immediately as **one** set change and **one** `/ji/freqset/update`
  (do not send one update per batch while stopped).
- Every batch must go through the normal path so sclang, MIDI (drone/arp/bass/
  lead), the UI status and the history all see it. Reuse
  `JIBridge.send_set(glide_time)`; do not duplicate the fan-out.
- A pending return is cancelled by: a manual mutation, a manual octave move,
  `go()` to a different slot, `back()`, or a transport stop that flushes the
  queue. Log the cancel once.

---

## 8. Part E — Bridge, OSC, quantize, state

### 8.1 Quantize phase

Add `PHASE_HOME = 0` and shift the existing phases up by one (`mutation = 1`,
`octave = 2`, … `midi = 9`). The constants are symbolic everywhere
(`ui/osc_control.py`, the bridge and `tests/test_quantize.py` import them by
name), so a mechanical renumber keeps the suite passing; update `PHASE_NAMES`,
`PHASE_ORDER` and the module docstring in the same commit. The applied order at
a beat becomes:

```text
home, mutation, octave, groove, drums, bass, arp, lead, drone, midi
```

Rationale: a scheduled return lands first, so a mutation requested for the same
beat composes on top of the returned set. Add one assertion to
`tests/test_quantize.py` that a `PHASE_HOME` action runs before a
`PHASE_MUTATION` action scheduled for the same beat (the existing test only
pins the relative order of the older phases).

### 8.2 Bridge

`JIBridge` gains (all optional and off/none by default so the accepted rig is
unchanged without configuration):

```text
home_config      path/mapping for config/home_config.json
home             HomeManager instance (always constructed; empty slots cost nothing)
request_home_capture(slot)
request_home_go(slot)
request_home_return()
request_back()
request_home_jump()          # immediate exact set swap (not a smooth return)
```

Each request is beat-quantized on `PHASE_HOME`; while stopped it applies
immediately. All of them must send one `/ji/home/state` afterwards, and every
applied set change must also send the usual `/ji/freqset/changed` via
`send_set`.

### 8.3 OSC (UI → bridge)

```text
/ji/home/capture        int slot 0..3
/ji/home/clear          int slot 0..3
/ji/home/go             int slot 0..3
/ji/home/return         none
/ji/home/jump           none
/ji/home/back           none
/ji/home/set_auto       int 0/1
/ji/home/set_every      int 1..32
/ji/home/set_seed       int 0..2^31-1
/ji/home/set_drift      int 0..12   (0 = unlimited)
/ji/home/set_glide      float 0.02..0.30
```

Handlers validate (clamp/raise like the existing `/ji/drone/*` handlers), log
one line on error, and never raise out of the OSC thread.

### 8.4 State (Bridge → UI)

`/ji/home/state` — 13 values, in order:

```text
0 int  has_home        (1 if any slot is captured)
1 int  active_slot     (-1 = none, 0..3 = A..D)
2 int  home_classes
3 int  distance        (current vs active home, 0 when none)
4 int  pending_batches (return steps still queued, 0 if none)
5 int  history_depth   (snapshots currently in history)
6 int  auto            (0/1)
7 int  every
8 int  seed
9 int  drift
10 int returning       (0/1)
11 int  journey_mutations
12 int  slot_mask      (bit 0 = A … bit 3 = D captured)
```

Send at start, on `/ji/ui/hello`, after every applied home action, after every
set change that moves `distance` (one small message, never per note), and after
a transport change.

---

## 9. Part F — GUI

Add a compact **Home** group (one or two rows), placed under the Mutation group
or beside it — your call, but Status must stay visible without scrolling at the
default window size:

- `Slot` combo `A B C D`, `Set home` (capture into the slot), `Go` (smooth
  return to it), `Clear`.
- `Return` button, `Back` button, `Jump` button (optional; label it `Jump` and
  tooltip "instant set swap, not a smooth return").
- `Auto` checkbox + `Every` spin (mutations) + `Drift` spin + `Seed` spin.
- Status line: `Home: A · 7 cl | dist 2 | auto 8 | drift 3 | back 12` and, while
  returning, `Home: returning to A · 2/3 beats`. Empty state:
  `Home: none (Set home to capture)`.
- Knobs/spins coalesce (~30 Hz) for continuous controls; buttons/combos send
  immediately; incoming state syncs with signals blocked (no feedback OSC);
  the pending line reflects queued changes while running.
- Disabled states: no `Go`/`Return`/`Back` when there is nothing to do; `Auto`
  needs a captured slot.

---

## 10. Optional stretch (only after the required parts pass)

- **Slot cycle** (`cycle` 0/1): after landing on a slot, the next automatic
  return targets the next captured slot (A→B→C→A). This is the literal "set
  chord progression" mode: capture two or four colours and let the rig walk
  between them on the bar grid.
- **Landing on the bar grid**: an option to start the return on the next *bar*
  instead of the next beat, so progressions change on bar lines.
- **Redo** (`/ji/home/redo`) paired with Back.

Keep these additive, documented, and tested; if time runs out, leave them out
rather than half-built.

---

## 11. Tests

### 11.1 New pure tests

```bash
uv run --with python-osc python tests/test_home.py
```

Cover at least:

- `HomeConfig`/`HomeManager` validation and clamps (slots, every, seed, drift,
  glide, history depth); an empty manager has no home and distance 0.
- capture/clear/go semantics; active slot tracking; `has_home`; `slot_mask`.
- **Distance + plan**: for many random (current, home) pairs including different
  sizes: exact `ceil(d/max_changes)` batches, each ≤ `max_changes`, landing
  exactly on home, anchor preserved/added-last, sizes within
  `[min_size, max_size]`, determinism per seed.
- The worked example from §5.1.
- **Smooth return scheduling** (fake/real bridge, fake outputs): running →
  one batch per quarter beat with exactly `batches` beats and one
  `send_set` per beat; stopped → one immediate set and exactly one
  `/ji/freqset/update`.
- **Journeys**: `every` mutations trigger a return on the next beat; manual
  mutation cancels a pending return; the journey counter resets on landing;
  the same home+seed+count reproduces the same sets (and `reseed` is what makes
  it reproducible).
- **Drift cap**: with `max_drift=2` a long journey never exceeds distance 2
  (except the documented 8-try fallback), and `max_drift=0` is unlimited.
- **Back**: restores the previous snapshot exactly (ratio set + anchor), walks
  back several steps, no-op at the oldest, cancels a pending return.
- **Anchor rules**: home anchor never removed when present; if it must be
  re-added it is added in the last batch and `engine.anchor_ratio` equals the
  home anchor after landing.
- **Engine additions**: `reseed` changes the sequence; `apply_snapshot`
  round-trips; invalid snapshots raise and leave state untouched;
  `propose_mutation` output is unchanged by the new methods (compare a
  pre-recorded sequence for a fixed seed, or the existing
  `tests/test_mutation.py` untouched).
- Bridge quantization: stopped → immediate; running → pending then applied;
  coalescing; transport-stop flush; `/ji/home/state` shape and handlers.
- No `mido` import during pure construction.

### 11.2 Existing tests

All listed in §2 must pass. In particular `tests/test_mutation.py` must pass
**unmodified**; if it is genuinely impossible, stop and document why instead of
editing its expectations.

### 11.3 Optional headless Qt test

```bash
QT_QPA_PLATFORM=offscreen uv run --with python-osc --with PyQt6 \
  python tests/test_ui.py --qt
```

Add Home-group checks: defaults, signal-blocked sync, no feedback OSC, disabled
states, the queued/immediate message text, and that the window still fits.
Skip cleanly if PyQt6/Qt is unavailable.

### 11.4 Launcher / docs

`tests/test_startup.py` must cover `--home-config` and any new `--check` output;
`docs/START_HERE.md` must document the new flags, the Home group and the return
procedure.

---

## 12. Human acceptance checklist

1. Capture a home on slot `A`; the line shows `dist 0`.
2. Press **Mutate** repeatedly; the status line shows the distance from home
   growing, and the rig sounds as before.
3. Press **Return** while the transport runs: the set walks home in
   `ceil(distance/max_changes)` beats (audible as a short, smooth progression,
   not a jump), and the Status set matches the captured home exactly.
4. With `Auto` on and `Every = 8`, eight mutations trigger the return
   automatically; the journey counter resets.
5. The same home + same seed + the same number of mutations reproduces the same
   journey from a restart.
6. **Back** restores the immediately previous set (check the Status ratios), and
   pressing it repeatedly walks further back.
7. Capture a second slot `B` with a different colour; `Go B` returns smoothly;
   with the optional cycle on, the rig alternates A→B→A on the bar grid — the
   "chord progression" behavior.
8. The return never takes an extra beat, never leaves the set off-home, and
   never produces a stuck note (drone retunes, lead re-projects, arp/bass follow).
9. Drone/arp/bass/lead/drums, groove, groove-quantize, arp sync/evolve and the
   accepted 8.3d controls do not regress; the window stays compact.
10. Record the result. **8.4 is not complete until the human confirms the return
    is smooth, minimal and repeatable.**

---

## 13. Diagnostics

One line per event, never per note:

```text
[HOME] slot=A captured classes=7 anchor=1.000000
[HOME] return A distance=3 batches=3 changes=1
[HOME] returned to A in 3 beats (3 batches)
[HOME] journey seed=5 every=8 drift=3 mutations=1/8
[HOME] return cancelled by mutation
[HOME] back -> classes=7 anchor=1.500000 (history 11)
[HOME] gap: slot=B is empty
```

Use the existing `[QUANT]`, `[BRIDGE]` and `[MUTATION]` lines unchanged.

---

## 14. Risks and decisions to document

- **Determinism vs a human.** Same seed reproduces a journey only until the
  player mutates by hand. Say this plainly; do not promise more.
- **Minimal-change definition.** `ceil(distance / max_changes)` counts classes,
  not perceived harmonic distance. A 1-class return can still be a large
  interval; the voice-led pairing mitigates it. Document the rule and the
  pairing.
- **Intermediate sizes.** A return through a set that is briefly 5 or 9 classes
  may sound thin/thick; the planner must respect `min_size`/`max_size` and the
  drone/lead must survive it. Test the edges.
- **Set-update rate.** One per beat maximum; do not let the return flash the
  sclang/MIDI fan-out.
- **History memory.** Bounded (`maxlen`), snapshots are small; 64 is plenty.
- **Phase numbering.** Inserting `home` must not reorder the accepted phases'
  relative order; pin it in `tests/test_quantize.py`.
- **GUI density.** One more group; use the scroll area, never clip Status.
- **Docs drift.** `START_HERE.md`, `--help`, the stage README and the design doc
  are part of the acceptance.

---

## 15. Deliverables

```text
ji-live/
├── control/
│   ├── home.py                 NEW: SetSnapshot, distance/planning, slots,
│   │                           journeys, history, drift cap
│   ├── mutation.py             MOD: additive snapshot()/apply_snapshot()/reseed()
│   ├── quantize.py             MOD: PHASE_HOME + the documented order
│   ├── ji_bridge.py            MOD: HomeManager ownership, requests, state,
│   │                           send_set reuse, journey hooks
│   └── osc_server.py           MOD: /ji/home/* handlers
├── config/
│   └── home_config.json        NEW: slots, auto, every, seed, drift, glide,
│                               history depth
├── ui/
│   ├── osc_control.py          MOD: builders/parsers/HomeStatus/store
│   └── ji_ui.py                MOD: compact Home group
├── run/ji_station.py           MOD: --home-config, --check
├── tests/
│   ├── test_home.py            NEW
│   ├── test_ui.py              MOD
│   └── test_startup.py         MOD (new flags)
├── docs/
│   ├── README_stage8_4.md      NEW: model, worked example, controls, human
│   │                           checklist
│   ├── START_HERE.md           MOD: flags, Home group, return/back procedure
│   ├── README_stage8_3d.md     MOD: link forward to 8.4
│   └── design_doc_10.28.10.50  MOD: §6.5 (mutation), new §6.19, §7.1, §8.9,
│                               §9, §14
└── README.md                   MOD: status, links, layout
```

Default `config/home_config.json` (shipped values; `auto` off so the accepted
rig is unchanged until the player turns it on):

```json
{
  "slots": 4,
  "auto": false,
  "every": 8,
  "seed": 1,
  "max_drift": 0,
  "return_glide": 0.06,
  "history_depth": 64
}
```

---

## 16. Self-review requirement (final response)

Provide:

- Files added/changed.
- For each requirement above, the code that satisfies it or a concrete concern.
- "If I had to bet on which line is most likely to fail on the human's machine,
  which is it, and why? Give a one-line verification command."
- Assumptions, max 5 bullets.
- Explicit statement that audio, MIDI, Ableton, IAC, sclang timing and the live
  GUI were not tested by the agent; 8.4 awaits human acceptance.
- The exact one-sclang + one-Python start sequence, copied from
  `docs/START_HERE.md` after your updates, so the human can paste it.

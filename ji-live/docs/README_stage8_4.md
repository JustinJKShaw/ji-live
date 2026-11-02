# Stage 8.4 — Home sets, deterministic journeys, and smooth return

Stage 8.4 makes movement through frequency sets **repeatable and musical**. It
adds four home slots, a seeded journey, an optional drift cap, **Back**, and a
beat-spaced **return** that lands exactly on a captured set in
`ceil(distance / max_changes)` quarter beats. No sclang file, SynthDef, audio DSP
or MIDI voice changed.

> Owner, 2026-11-01: *"i want to be able to return to certain states after moving
> around through frequency sets … a toggleable feature where given a set number
> of mutations and a seed, the frequency set will mutate deterministically and
> return smoothly (i.e. to return should not take more than the minimum number of
> allowed changes per mutation) to a home set, after a home set is selected …
> the goal of this is to allow for something akin to set chord progressions.
> additionally, a 'back' button that returns to the immediately prior previous
> state may be useful."*

Status: implemented and covered by pure tests. **Audio, MIDI, Ableton, IAC,
sclang timing and the live GUI were not tested by the agent; 8.4 awaits the human
acceptance checklist in §9.** The design doc's planned 8.4 was
"pattern/sequencing mutation"; this stage scopes that idea to the frequency-set
layer first. MIDI pattern recall and whole-session save/recall stay in 8.5.

**Stage 8.4.1 (stepwise/replaced class glide)** sits on top of 8.4 and changes
only what the Glide knob broadcasts: a replacement class now melts from its old
ratio to its new one for every voice (§6.1). The 8.4 home/return contracts,
engine state and one-update rule are otherwise unchanged; the one-update rule
gains exactly one exception (the bounded glide stream).

**Owner-confirmed 2026-11-02.** The owner approved the 8.4.1 behavior on the
live rig: the replaced class melts on the autotuned audio and the MIDI voices,
pure add/remove still snaps, Glide 0 is effectively instant, and a second
mutation / Return / Go / Back / Jump cancels a melt cleanly. 8.4's own home/
return checklist (§9) remains separately human-pending.

---

## 1. The model in one page

The engine state that matters is a **set snapshot** (`control.mutation.
SetSnapshot`):

```text
SetSnapshot:
  ratios   tuple[float, ...]   octave-reduced classes, sorted, unique
  anchor   float | None        the protected drone class (always in ratios)
  octaves  tuple[(ratio,int)]  analysis voicing (needed to restore exactly)
```

A **home set** is a snapshot captured into one of four **slots** (`A`–`D`). A
**journey** is the mutation drift since the last landing on a home. A **return**
is the beat-spaced sequence of batches that lands exactly on the active home.
**Distance** is how many classes still differ; **drift** optionally caps how far
a journey may stray; **Back** restores the immediately previous snapshot.

Two derived quantities drive the whole stage:

```text
remove   = classes in the current set but not in the home set
add      = classes in the home set but not in the current set
distance = max(len(remove), len(add))            # edits needed to land home
batches  = ceil(distance / changes_per_batch)    # beats needed to return
```

`changes_per_batch` is **`MutationEngine.max_changes`** (the hard per-mutation
cap, default 3), *not* the `change_count` UI setting. That is the owner's
"minimum number of allowed changes per mutation": a return takes exactly
`ceil(distance / max_changes)` quarter beats, and a return that takes one extra
beat is a bug.

| Term | Meaning |
|---|---|
| **home set** | a captured snapshot you can return to |
| **slot** | one of four home slots (`A`–`D`) |
| **distance** | classes that still differ from the active home |
| **return** | the beat-spaced sequence of batches that lands exactly on home |
| **journey** | mutations made since the last landing on a home |
| **drift** | optional cap on how far a journey may stray from home |
| **back** | restore the immediately previous snapshot |

Four slots plus the optional cycle give **set chord progressions**: `A → B → C →
A`, each transition played as a smooth return rather than a jump.

---

## 2. Why the mutation algorithm is untouched

The owner guessed this "will likely require modifications to the mutation
algorithm … somehow future states will have to be considered". It does not.

`MutationEngine.apply(MutationEvent)` is already a **targeted edit**: it can add,
remove or replace one class exactly. A planner can therefore walk from any set to
any other set with ordinary edits. The "future state" thinking becomes **policy**
at the bridge layer — the optional drift cap (§5) — which defaults **off**, so
the accepted mutation feel is byte-identical. `propose_mutation()` and
`mutate_many()` were not changed; `tests/test_mutation.py` passes unmodified and
`tests/test_home.py` pins a recorded seed-5 sequence.

Only three additive engine methods exist:

```python
def reseed(self, seed: int | None) -> None: ...        # journey start
def apply_snapshot(self, snapshot) -> None: ...        # Back / jump / landing
def snapshot(self) -> SetSnapshot: ...                 # public immutable copy
```

`apply_snapshot` validates (positive ratios, folded/deduplicated, ≤ `max_size`,
anchor in the set, octaves in `octave_min..octave_max`) and raises `ValueError`
without touching the state on bad input. It does **not** reuse the private
`_snapshot()/_restore()` rollback pair used by `mutate_many`.

---

## 3. Planning a return (`control/home.py`)

`plan_return(current, home, max_changes, seed, *, min_size=3, max_size=12)`
returns batches of `(remove_ratio, add_ratio)`; applying them in order lands
exactly on the home set. The rules the tests pin:

1. `len(plan) == ceil(distance / max_changes)` — never one batch more.
2. Every batch has at most `max_changes` pairs and at least one edit.
3. After applying all batches the ratio set equals the home set and the anchor
   equals the home anchor.
4. If the home anchor is still in the current set it is never removed by any
   batch. If it must be added, it is added in the **last** batch and the
   engine's anchor is set to it after landing.
5. The same `(current, home, max_changes, seed)` always yields the same plan.
   The seed only breaks ties.
6. Sizes may differ: a batch may remove without adding or add without removing.
   Intermediate sizes stay within `[min_size, max_size]`.
7. Pairing is **voice-led**, not arbitrary.

**Voice-led pairing.** Removals are ordered by their distance from the home set
(most distant first; ties broken by the seed). Each removal then takes the
nearest remaining home class (log-ratio distance in cents) as its replacement.
Leftover removals or additions are pure edits. To respect the size bounds, pure
additions run first when the home set is larger, pure removals run first when it
is smaller, and replacements (which do not change the size) sit between them. The
home anchor's edit is always moved to the last batch.

### 3.1 Worked example

Current `{1, 9/8, 4/3, 3/2, 5/3}`, home `{1, 9/8, 5/4, 3/2, 5/3}`,
`max_changes = 1`:

```text
remove = {4/3}      add = {5/4}      distance = 1      batches = 1
plan = [[(4/3, 5/4)]]                                   1 beat
```

The `4/3` is the only class not in the home set; the `5/4` is the only home class
missing; they are the nearest pair, so one replacement lands the set in one beat.

A three-class example with `max_changes = 1` (the human checklist case):

```text
current {1, 9/8, 5/4, 4/3, 3/2, 5/3, 15/8}
home    {1, 9/8, 5/4, 3/2, 5/3, 7/4, 11/8}
distance = 3  ->  3 batches, one replacement each, 3 quarter beats
```

---

## 4. Slots, capture, go, clear

```text
capture(slot)  store the current SetSnapshot in that slot; make it active
go(slot)       smooth return to that slot (plans from the *current* set)
clear(slot)    forget it
```

`active_slot` is the last captured or last `go`-target; `distance` in the state
is always current vs the active home (0 when there is none). Capturing a slot
**starts a journey** from the configured seed, so the checklist's "same home +
same seed + same number of mutations reproduces the same journey" works from a
restart. Clearing the active slot leaves no active home.

A **capture is not a set change**: it stores a slot, it does not enter the Back
history.

---

## 5. Journeys, automatic return, drift

* `auto` (default **off**), `every` (1..32, default 8), `seed` (0..2³¹−1).
* `start_journey()` reseeds the engine, sets `journey_mutations = 0`, and
  requires an active home. It is called on capture, on a seed change, and is
  available to future stages.
* Each applied mutation increments `journey_mutations`.
* When `auto` is on and `journey_mutations >= every`, a return is **queued for
  the next quarter beat** and the counter resets when the return lands.
* Manual `Mutate` always works; if a return is in flight, a manual mutation
  **cancels** it (`[HOME] return cancelled by mutation`) and the counter keeps
  going from the new position.
* `Return` (button/OSC) works with `auto` off: plan from the current set and play
  the return now.
* **Determinism is conditional.** The same home + same seed + the same number of
  mutations reproduces the same journey **only while no human intervention
  happens**. Any manual mutation is part of the sequence. This is stated plainly
  and not promised beyond that: a human mutating by hand changes the RNG stream.

### 5.1 Drift cap

`max_drift` (0..`max_size`, default **0 = unlimited**, GUI label `Drift`):

* With `max_drift > 0` and an active home, a proposed mutation whose result would
  put `distance(current, home) > max_drift` is **rejected and retried** (up to 8
  re-proposals) before being accepted as a last resort.
* The retry rolls the *state* back between proposals but lets the engine RNG
  advance, so every retry differs. The loop is bounded (8 tries) and cannot
  consume the beat.
* If all 8 fail, the last proposal is accepted and one line is logged:
  `[HOME] drift cap exceeded (8 tries)`.
* This is a bridge-layer policy; `propose_mutation` is untouched and the default
  0 keeps the accepted mutation feel identical.

---

## 6. The beat-spaced return

The return is **not** a jump and **not** a burst of updates.

* **Transport running:** exactly one batch per quarter beat. The request itself
  sits on `PHASE_HOME` at the next beat and applies batch 0; each later batch is
  scheduled from inside the previous batch's action (`PHASE_HOME`), so the whole
  return takes exactly `batches` beats. One `send_set(return_glide)` per batch
  (glide 0.02..0.30 s, default 0.06). After the last batch the bridge logs
  `[HOME] returned to A in 3 beats (3 batches)` and sends one
  `/ji/home/state`.
* **Transport stopped:** the compatibility rule wins — the whole return applies
  immediately as **one** set change and **one** `/ji/freqset/update`
  (`[HOME] returned to A immediately (3 batches, transport stopped)`).
* Every batch goes through the normal path (`JIBridge.send_set`), so sclang, the
  MIDI rig (drone/arp/bass/lead), the UI status and the history all see it. The
  fan-out is never duplicated.
* A pending return is cancelled by: a manual mutation, a manual octave move,
  `go()` to a different slot, `back()`, or a transport stop that flushes the
  queue. The cancel is logged once with its cause. Since Stage 8.4.1 a Return or
  Go also cancels an active class glide first (see §6.1).

---

## 6.1 Stepwise class glide (Stage 8.4.1)

Stage 8.4.1 fixes what the Mutation group's **Glide** knob means. It is **not** a
global autotune retune time:

> "the purpose is specifically for only for the stepwise mutations (one frequency
> moves to another) … the stepwise one should move over time (the actual
> frequency class of that note shifts over the course of the glide time. so one
> frequency class kind of melts into the next. therefore it should affect midi
> too, since the frequency sets are a global property." — owner, 2026-11-01;
> follow-up: "replaced events are included."

**Definition.** A mutation event with **both** `removed_ratio` and `new_ratio`
set (`reason == "stepwise"` or `"replaced"`) is a *gliding replacement*. Pure
`added`/`removed` events are not.

**The melt.** Given the before/after snapshots, the `(old, new)` pairs, the knob
time `T` and a constant 30 Hz rate (clamped 5–60):

```text
steps = max(1, floor(T * 30 + 0.5))          # round half up: 0.15 s -> 5
r(t)  = old * (new / old) ** (t / T)         # cents-linear
```

`steps + 1` snapshots are broadcast at exactly `t_k = k*T/steps`; `set(0)` is the
before set with pure edits applied and the replacement still at its old ratio,
and `set(T)` is the engine's `after` set exactly. Intermediate values are folded
and rounded through `fold_ratio`, so they match the engine's 12-decimal classes.
A replaced anchor travels with its pair; a pure-removed anchor falls back to the
engine's final anchor (or the nearest class). A moving class may momentarily
collide with another class; the intermediate set is deduplicated (it briefly has
one fewer class) and the final step restores the exact set.

**Every voice hears it**, because the intermediate snapshots are a global
property and go through the normal fan-out (`JIBridge.send_snapshot`):

| Voice | During the melt | After the final step |
|---|---|---|
| Live autotune / loop players (sclang) | every step via `/ji/freqset/update`; the 20 Hz lookup follows, `retuneTime` smooths each step | final set; `retuneTime` = the Glide knob |
| Drone (Bus 1) | every step via `on_set`; a held anchor retunes/bends along `r(t)` | final set |
| Bass (Bus 4) | every step stores the moving pool; the next step picks from it | final set |
| Lead (Bus 5) | every step re-projects; the next note uses the moving class | final set |
| Arp (Bus 2) | every step is stored, but the pattern is **not rebuilt and the cursor is not reset**; a pattern note whose ratio is absent plays the nearest current ratio (cents), same octave | one rebuild (today's `on_set`) |
| UI | no per-step message | one `/ji/freqset/changed` mirror |

**One update per change, with one exception.** The Stage 8.4 rule ("one
`/ji/freqset/update` per applied change / quarter beat") still holds **outside**
a glide. A glide is one logical change that intentionally emits a bounded 30 Hz
stream (5–6 updates for the default 0.15 s, 61 for a 2 s knob); the final step is
the one `[BRIDGE] ->` log line for the change. Do not treat those intermediate
updates as separate changes: the engine state, history, home distance, journey
counter and UI are final from the moment the mutation lands.

**Pure add/remove snaps.** A pure addition or removal has no `(old, new)` pair,
so the bridge sends exactly one update with the 0.005 s sclang minimum. This is a
deliberate change from the old "every mutation retunes over the Glide knob"
behavior; the Live Autotune `Retune` knob remains the persistent retune setting
for the rest of the rig. Note that every set update (including each glide step)
rewrites each active synth's `retuneTime`, so the `Retune` knob's displayed value
can disagree with the synth until it is touched; that already happened before
8.4.1 and is not changed here. The final step writes the Glide knob so the
resting value is consistent with the control.

**Cancellation.** A new mutation, Return/Go, Back or Jump cancels an active melt:
it delivers the previous final set once (a 0.005 s snap), logs
`[GLIDE] cancelled by …`, and then applies the new change. A transport stop does
**not** cancel a glide — it is a set morph, not a note. `bridge.stop()` stops the
scheduler with no snap. The Stage 8.5 state load must call
`JIBridge.cancel_glide("load")` first (the only cross-feature hook).

**Before `start()`.** A bridge that has not been started (construction-only code,
tests) and a direct `MutationOSCServer.trigger()` with no captured `before`
keep the accepted one-update path: no plan is built, no scheduler thread is
created, and the event's own `glide_time` is used. `JIBridge.__init__` creates no
glide thread (the worker starts lazily on the first plan).

**Diagnostics.** One line per glide, never per step (the sclang receiver still
prints its own one line per update, as accepted in Stage 3):

```text
[GLIDE] replaced 1.500000 -> 1.400000 over 0.150 s (5 steps)
[GLIDE] replaced 1.333333 -> 1.406250, 1.500000 -> 1.454545 over 0.300 s (9 steps)
[GLIDE] done (0.150 s)
[GLIDE] cancelled by mutation
[GLIDE] cancelled by return
[GLIDE] cancelled by back
[GLIDE] cancelled by jump
```

**Cost.** 30 Hz is enough for the autotune's lag and the drone's bend; a 2 s
glide is 61 updates and prints 61 sclang receiver lines. No sclang, SynthDef,
audio DSP, MIDI voice or new config key is added: the existing
`/ji/freqset/update` receiver already applies intermediate sets. `control/
glide.py` is the pure planner/scheduler (`GLIDE_RATE_HZ`, `GlidePlan`,
`build_glide_plan`, `GlideScheduler`); `tests/test_glide.py` covers it.

---

## 7. Back, history, redo

`HomeManager` owns a bounded snapshot history (`deque(maxlen=history_depth)`,
default 64, config 8..256).

* Every applied set change that goes through the bridge is recorded: a mutation,
  a batch, one return step, a Back (the move is the history itself), a jump.
  A home capture is not a set change.
* The bridge records the initial set at construction, so Back works from the
  first mutation even before a home is captured.
* `back()` restores the previous snapshot, walks further back on repeated
  presses, and is a logged no-op at the oldest entry.
* Any new forward change clears the redo stack. `HomeManager.redo()` exists and
  is tested; **it has no OSC/GUI surface in 8.4** (a redo button is a 8.5
  candidate rather than a half-built control).
* Back during a pending return cancels the return first.

The snapshot restores ratios, anchor and voicing octaves exactly
(`[HOME] back -> classes=7 anchor=1.500000 (history 11)`).

---

## 8. Surface: OSC, state, GUI

### 8.1 OSC (UI → bridge)

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

The six set requests (capture/clear/go/return/jump/back) are beat-quantized on
`PHASE_HOME`; while the transport is stopped they apply immediately. The
settings (auto/every/seed/drift/glide) are config, not set changes, and apply
immediately. Handlers validate and clamp (`set_every` 99 → 32, `set_glide` 0.5 →
0.30), log one line on error, and never raise out of the OSC thread.

### 8.2 Phase order

`PHASE_HOME = 0` and the existing phases shifted up by one:

```text
home, mutation, octave, groove, drums, bass, arp, lead, drone, midi
```

A return batch lands first, so a mutation requested for the same beat composes
on top of the returned set. `tests/test_quantize.py` pins this.

### 8.3 `/ji/home/state` (Bridge → UI)

13 values, in order:

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
11 int journey_mutations
12 int slot_mask       (bit 0 = A … bit 3 = D captured)
```

Sent at start, on `/ji/ui/hello`, after every applied home action, after every
set change that moves `distance`, and after a transport change — one small
message per event, never per note.

### 8.4 GUI

A one-row **Home** group under the Mutation/MIDI row:

* `Slot` combo `A B C D`, `Set home`, `Go`, `Clear`, `Return`, `Back`, `Jump`
  (tooltip: "instant set swap, not a smooth return"), `Auto`, `Every`, `Drift`,
  `Seed`, and the status line.
* Status line: `Home: A · 7 cl | dist 2 | auto 8 | drift 3 | back 12`;
  while returning `Home: returning to A · 2/3 beats`; empty state
  `Home: none (Set home to capture)`; captured slots but no active home
  `Home: no active home (Go or Set home)`.
* Buttons/combos send immediately and the Status message says "queued for the
  next beat" while the transport runs or "applied immediately" when stopped.
  Spins coalesce at ~30 Hz. Incoming state syncs with signals blocked (no
  feedback OSC).
* Disabled: `Go`/`Clear` for an empty selected slot, `Return`/`Jump` at distance
  0, `Back` with fewer than two history snapshots, `Go`/`Return`/`Back` while a
  return is in flight (`Jump` stays available for an instant swap), and `Auto`
  until a slot is captured.
* The `2/3 beats` progress comes from the payload's `pending_batches`; the GUI
  remembers the total it saw when the return started (the first state after
  batch 0 carries `pending = total − 1`).
* The window grew one row to 1280×768 so the extra Home row does not push Status
  below the fold; the scroll area remains the small-screen safety net.

---

## 9. Human acceptance checklist

1. Capture a home on slot `A`; the line shows `dist 0`.
2. Press **Mutate** repeatedly; the status line shows the distance from home
   growing, and the rig sounds as before.
3. Press **Return** while the transport runs: the set walks home in
   `ceil(distance/max_changes)` beats (audible as a short, smooth progression,
   not a jump), and the Status set matches the captured home exactly.
4. With `Auto` on and `Every = 8`, eight mutations trigger the return
   automatically; the journey counter resets.
5. The same home + same seed + the same number of mutations reproduces the same
   journey from a restart (no manual mutation in between).
6. **Back** restores the immediately previous set (check the Status ratios), and
   pressing it repeatedly walks further back.
7. Capture a second slot `B` with a different colour; `Go B` returns smoothly.
   The optional slot cycle is **not** in 8.4 (see §10).
8. The return never takes an extra beat, never leaves the set off-home, and never
   produces a stuck note (drone retunes, lead re-projects, arp/bass follow).
9. Drone/arp/bass/lead/drums, groove, groove-quantize, arp sync/evolve and the
   accepted 8.3d controls do not regress; the window stays compact.
10. Record the result. **8.4 is not complete until the human confirms the return
    is smooth, minimal and repeatable.**

---

## 10. Decisions and risks

* **Determinism vs a human.** The same seed reproduces a journey only until the
  player mutates by hand; a manual mutation is part of the sequence. No more is
  promised.
* **Minimal-change definition.** `ceil(distance / max_changes)` counts classes,
  not perceived harmonic distance. A one-class return can still be a large
  interval; the voice-led pairing (nearest replacement) mitigates it. This is the
  documented rule.
* **Intermediate sizes.** A return through a set that is briefly smaller or
  larger may sound thin/thick. The planner respects `min_size`/`max_size` and
  adds/removes before replacing so it never crosses them; the drone/lead survive
  any size ≥ 3.
* **Set-update rate.** One update per beat maximum while running; the stopped
  return deliberately collapses to one update. **Stage 8.4.1 exception:** one
  gliding replacement is one logical change that emits a bounded 30 Hz stream
  (5–6 updates at the default 0.15 s, 61 at 2 s); outside a glide the rule
  stands. The Glide knob no longer retunes pure add/remove updates (they snap at
  0.005 s) — a deliberate behavior change, documented in `GUI_PARAMETERS.md`
  and `START_HERE.md`.
* **History memory.** Bounded (`maxlen`), snapshots are small; 64 is plenty.
* **Phase numbering.** Inserting `home` first did not reorder the older phases;
  `tests/test_quantize.py` pins home-before-mutation and the full order.
* **GUI density.** One more group, one row; the scroll area absorbs small
  screens and Status stays visible at the default size.
* **Docs drift.** `START_HERE.md`, `--help`, this README and the design doc are
  part of the acceptance.
* **Not implemented (deliberately).** Slot cycle (`A→B→C→A` on the bar grid),
  bar-grid landing, and a redo button are the stage's optional stretch. They are
  left out rather than half-built; `HomeManager.redo()` is complete and tested
  but has no UI.

---

## 11. Diagnostics

One line per event, never per note:

```text
[HOME] slots=4 auto=0 every=8 seed=1 drift=0 glide=0.060 history=64
[HOME] slot=A captured classes=7 anchor=1.000000
[HOME] journey seed=5 every=8 drift=3 mutations=1/8
[HOME] return A distance=3 batches=3 changes=1
[HOME] returned to A in 3 beats (3 batches)
[HOME] returned to A immediately (3 batches, transport stopped)
[HOME] return cancelled by mutation
[HOME] return cancelled by transport stop
[HOME] back -> classes=7 anchor=1.500000 (history 11)
[HOME] jumped to B classes=5 anchor=1.250000
[HOME] gap: slot=B is empty
[HOME] drift cap exceeded (8 tries)
[GLIDE] replaced 1.500000 -> 1.400000 over 0.150 s (5 steps)
[GLIDE] done (0.150 s)
[GLIDE] cancelled by mutation
```

`[QUANT]`, `[BRIDGE]` and `[MUTATION]` lines are unchanged. A glide prints one
`[GLIDE]` line (plus `done`/`cancelled`), never one per step; the sclang
receiver still prints its accepted line per set update.

---

## 12. Start / test commands

```bash
cd ji-live
uv run --with python-osc --with mido --with python-rtmidi --with PyQt6 \
  python run/ji_station.py
```

```bash
uv run --with python-osc python tests/test_home.py
uv run --with python-osc python tests/test_glide.py
uv run --with python-osc python tests/test_quantize.py
uv run --with python-osc python tests/test_ui.py --pure
uv run --with python-osc python tests/test_startup.py
QT_QPA_PLATFORM=offscreen uv run --with python-osc --with PyQt6 \
  python tests/test_ui.py --qt
```

The Home group needs no flag and no new bus: it edits the existing frequency set
and reuses `/ji/freqset/update`, so the default launcher command still starts
exactly the accepted 8.3d rig. `config/home_config.json` ships `auto: false`, and
`run/ji_station.py --home-config <path>` points the bridge at another file.

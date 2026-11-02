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
  queue. The cancel is logged once with its cause.

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
  return deliberately collapses to one update.
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
```

`[QUANT]`, `[BRIDGE]` and `[MUTATION]` lines are unchanged.

---

## 12. Start / test commands

```bash
cd ji-live
uv run --with python-osc --with mido --with python-rtmidi --with PyQt6 \
  python run/ji_station.py
```

```bash
uv run --with python-osc python tests/test_home.py
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

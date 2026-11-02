# Stage 8.4.1 Agent Prompt — Stepwise/replaced frequency-class glide

You are implementing **Stage 8.4.1**, a focused patch on top of the built-but-not-
yet-human-accepted Stage 8.4. Stages 0–7.1, 8.1, 8.2, 8.3a and 8.3d are
human-confirmed; 8.3b/8.3c are in live use; 8.4 (home sets + smooth return) is
built and awaiting its human checklist. Treat every documented behavior as the
accepted baseline and do not regress it.

The owner has clarified what the Mutation group's **Glide** knob is for. It is
**not** a global autotune retune time:

> "the purpose is specifically for only for the stepwise mutations (one frequency
> moves to another) if kind of mutation happens instead of the frequency classes
> immediately updating, the stepwise one should move over time (the actual
> frequency class of that note shifts over the course of the glide time. so one
> frequency class kind of melts into the next. therefore it should affect midi
> too, since the frequency sets are a global property."
>
> — owner, 2026-11-01
>
> Follow-up: "replaced events are included."

So: when a mutation replaces one class with another, the **class itself must
travel** from the old ratio to the new ratio over the Glide time, and every voice
that reads the frequency set must hear that travel. Pure additions and removals
stay instantaneous.

**Concurrency:** this patch and the Stage 8.5 state-save/recall prompt both edit
`control/ji_bridge.py`, `ui/ji_ui.py`, `tests/test_ui.py` and the docs. Run this
patch first; do not run both agents concurrently against one working tree. 8.5's
state load must cancel an active glide, which is the one cross-feature hook.

Do not claim human audio acceptance. The owner is the authority.

---

## 1. Read these files first

Read the code and docs, not just the summaries:

```text
docs/design_doc_10.28.10.50        §2.7, §6.2, §6.5, §6.11–§6.19, §7.1, §8.9,
                                   §9, §14
docs/START_HERE.md                 authoritative start/stop/flag sheet
docs/GUI_PARAMETERS.md             the Glide row and the current explanation
docs/README_stage8_4.md            home sets, return, and the one-update rule
docs/README_stage8_3d.md           lead/drone set-update contracts
docs/README_stage8_3c.md           arp sync/evolve and pattern rebuild
control/mutation.py                MutationEvent/MutationBatch, SetSnapshot
control/home.py                    SetSnapshot use, return planning
control/ji_bridge.py               send_set, _on_event, _mutate, return/back/jump
control/osc_server.py              trigger/apply_trigger/announce, glide storage
control/quantize.py                the beat queue (mutation lands on a beat)
control/midi_rig.py                on_set fan-out to drone/arp/bass/lead
control/arp.py                     on_set rebuilds and resets the pattern
control/bass.py                    on_set stores the pitch pool
control/lead.py                    on_set re-projects the motif
control/drone.py                   on_set and the hold/pulse/sequence/bend modes
ui/osc_control.py                  the Glide ParamSpec (0–2 s, default 0.15)
ui/ji_ui.py                        the Mutation group and knob tooltips
tests/test_home.py                 how the bridge is driven with a fake clock
tests/test_arp.py                  arp pattern/order tests
tests/test_mutation.py             must not change
README.md                          current run commands/status
```

Run Python tests from `ji-live/`.

---

## 2. What you must preserve

- Stage 5.5 audio, Stage 6 MPE (Bus 1 drone, Bus 2 arp), 8.1 rhythm/drums,
  8.3a bass, 8.3d lead + drone modes, and 8.4 home sets/return are accepted or
  in use. Do not change their contracts beyond the additive `transient` path
  described here.
- **`MutationEngine.propose_mutation()` / `mutate_many()` behavior must not
  change.** `tests/test_mutation.py` must pass **unmodified**.
- The quantization rule is absolute: while the transport is stopped every
  quantized action applies immediately; while it runs, actions wait for the next
  quarter beat. A glide starts when the mutation applies (on the beat) and then
  runs in real time; it is not itself beat-quantized.
- **One `/ji/freqset/update` per applied change** stays true outside a glide. A
  glide is **one logical change** that intentionally emits a bounded stream
  (30 Hz) for its duration. Update the 8.4 README's "at most one set update per
  quarter beat" wording to carve out this single exception.
- Keep the compact GUI (1280×768, 8 pt, scroll area); do not clip Status. No new
  knob or group is needed.
- No sclang change is expected: the existing `/ji/freqset/update` receiver and
  the 20 Hz lookup already apply intermediate sets. If you change a `.scd` file,
  justify it and update `docs/START_HERE.md`.
- No SynthDef, audio DSP, or new MIDI voice.
- A glide must never leak a thread, a stuck note, or a half-applied set.

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

Tests that count "exactly one update per change" may need to settle or cancel an
active glide first; do not weaken their intent, and do not edit
`tests/test_mutation.py`.

---

## 3. What a glide is (the definition the tests pin)

A **gliding replacement** is one `MutationEvent` with **both** `removed_ratio`
and `new_ratio` set. That includes the engine's `reason == "stepwise"` and
`reason == "replaced"` (the owner confirmed both). A pure `added` or `removed`
event is **not** a glide.

Given:

```text
before   = SetSnapshot before the mutation
after    = SetSnapshot after the mutation (the engine's authoritative state)
pairs    = [(removed_i, new_i), ...]   from the gliding replacements
T        = the Glide knob value in seconds (0..2, default 0.15)
rate     = 30.0 steps/second (constant in control/glide.py; clamp 5..60)
steps    = max(1, round(T * rate))     # segments; steps + 1 snapshots
```

the broadcast set at time `t` (0 ≤ t ≤ T) is:

```text
set(t) = before
         - every pure removal            (applied at t = 0, immediately)
         + every pure addition           (applied at t = 0, immediately)
         - every replaced old class
         + every moving class  r_i(t)
```

with **cents-linear** (log-ratio) interpolation per pair:

```text
r_i(t) = old_i * (new_i / old_i) ** (t / T)
```

so `r_i(0) == old_i`, `r_i(T) == new_i`, and the class travels monotonically in
pitch. Round through `fold_ratio` so the values match the engine's 12-decimal
classes.

**Anchor rule.** If the old anchor is one of the replaced classes, the
intermediate anchor is that pair's `r_i(t)`. If the anchor is a pure removal, use
`after.anchor` when it is present in `set(t)`; otherwise the nearest class to the
old anchor. Every intermediate snapshot must satisfy `SetSnapshot` invariants
(positive, unique, anchor in the set). Intermediate `octaves` may be empty
(the broadcast only uses ratios/anchor/fundamental).

**Endpoints.** `set(0)` is the before set with the pure edits applied and the
replacement still at its old ratio; `set(T) == after` exactly.

**Multiple replacements** in one `MutationBatch` all travel together over the
same `T`; a batch may also contain pure adds/removes.

**T = 0** (or no gliding replacement) means no glide: exactly one update with
the final set. Pure additions/removals use a **minimal 0.005 s update glide**
(not the Glide knob): the owner defined the knob as the replacement melt only,
so an add/remove must snap immediately. This is a deliberate change to the old
"every mutation retunes over the Glide knob" behavior; document it and say that
the Live Autotune `Retune` knob is still the persistent retune setting for the
rest of the rig.

---

## 4. What every voice must hear

The intermediate snapshots are a **global property**, so they go to the same
fan-out as a normal set update:

| Voice | During the glide | After the final step |
|---|---|---|
| Live autotune (sclang) | every step via `/ji/freqset/update`; the lookup follows, `Lag.kr` smooths the 30 Hz steps | final set; `retuneTime` = the Glide knob for a glide, `0.005` for a pure add/remove |
| Loop players (sclang) | every step, same receiver | final set |
| Drone (Bus 1) | every step via `on_set`; a held anchor retunes/bends along `r(t)` | final set |
| Bass (Bus 4) | every step stores the moving pool; the next step picks from it | final set |
| Lead (Bus 5) | every step re-projects; the next note uses the moving class | final set |
| Arp (Bus 2) | every step is stored, but the pattern is **not rebuilt and the cursor is not reset**; at trigger time a pattern note whose ratio is absent from the current set is played at the **nearest current ratio** (cents), same octave offset | the final step rebuilds the pattern once (the existing `on_set` behavior) |
| UI | **no** per-step `/ji/freqset/changed`; one final mirror | final set |

If a voice's bus is missing, its existing degradation rule applies unchanged.

**Why the arp needs the special path:** `ArpEngine.on_set` currently rebuilds the
pattern and resets the step index on every call. Calling it 30 times a second
would thrash the pattern and spam the log. Add an additive
`on_set(..., rebuild: bool = True)` (or an equivalent transient method) and a
trigger-time `resolve_note(note)` that maps an absent ratio to the nearest
current ratio. The final, non-transient call rebuilds once.

**Live-autotune side note (document, do not fix here):** every set update
overwrites each synth's `retuneTime`; the Live Autotune `Retune` knob and the
displayed state can disagree with the synth until it is touched. This already
happens today; the glide makes it visible. The final step uses the Glide knob as
the update's glide so the resting value stays consistent with the knob.

---

## 5. Architecture

### 5.1 New pure module `control/glide.py`

Standard library only (no OSC, PyQt6, mido, or threads in the planner):

```text
GLIDE_RATE_HZ = 30.0            # clamp 5..60
GlidePlan                       # before, after, pairs, pure adds/removes, T, rate
  steps -> int
  duration -> float
  snapshot_at(t) -> SetSnapshot # cents-linear, anchor rule, fold/round
  final_snapshot -> SetSnapshot # == after
build_glide_plan(before, after, events, duration, rate=...) -> GlidePlan | None
GlideScheduler                  # testable step clock + lazy worker thread
  start(plan) -> bool
  cancel(*, snap: bool = True) -> bool
  tick(now=None) -> int         # apply due steps; used by tests
  stop()
  active / progress / plan
```

- `build_glide_plan` returns `None` when there is no gliding replacement, when
  `duration <= 0`, or when the plan's endpoint would not equal `after` (a
  defensive consistency check). It raises `ValueError` for malformed input.
- The scheduler sends step 0 synchronously from `start()`, then one step per
  `1/rate` on its worker thread, and the final step at exactly `T`. The callback
  is `on_step(snapshot, final: bool)`.
- The worker thread starts **lazily** on the first `start(plan)` (never in
  `JIBridge.__init__`, matching `BeatQuantizer`), is a daemon, and is stopped by
  `bridge.stop()`. `now`/`sleep` are injectable so tests use `tick()` with a fake
  clock and never sleep.
- `cancel(snap=True)` stops the stream and, when `snap` is true, delivers the
  final snapshot once through the normal (non-transient) path. `cancel` is
  idempotent and returns whether a glide was active.
- `start()` while active cancels the previous plan first (the bridge also does
  this explicitly; both are safe).

### 5.2 Bridge

```text
glide          GlideScheduler (constructed in __init__, thread lazy)
send_snapshot(snapshot, glide_time, *, ui, transient, log) -> bool
_cancel_glide(reason) -> bool
_on_glide_step(snapshot, final) -> None
```

- `send_snapshot` is the snapshot-aware sibling of `send_set`:
  - sclang: one `/ji/freqset/update [fundamental, glide_time, *snapshot.ratios]`;
  - MIDI: `midi.on_set(ratios, anchor, fundamental, transient=transient)`;
  - UI: only when `ui=True` (the final step), using the engine's final state;
  - log: only when `log=True` (the final step), one `[BRIDGE] -> ...` line.
  Refactor `send_set` to call `send_snapshot(self.engine.snapshot(), ...)` with
  `ui=True, transient=False, log=True` so its behavior is byte-identical.
- `_mutate` captures `before = self.engine.snapshot()` immediately before
  `server.trigger()` / the drift loop and stores it for `_on_event` (for the
  drift path, the accepted attempt's before is that snapshot). `_on_event`
  consumes and clears it.
- `_on_event`:
  1. build the plan from `before`, `engine.snapshot()`, the event(s), and
     `server.glide_time`;
  2. if `plan is None`: send one update with a minimal `0.005` s glide (an
     add/remove snaps; the Glide knob is not used);
  3. else: cancel any previous glide, print one
     `[GLIDE] replaced A -> B over 0.150 s (5 steps)` line (one per pair or a
     compact summary), and start the scheduler. Step 0 is the t=0 set; the
     final step is the normal `after` set.
  `_after_mutation()` (history, journey counter, `/ji/home/state`, auto return)
  runs once, immediately, exactly as today — the engine's state is already
  final; the glide only changes what is broadcast.
- If `_on_event` is called without a captured `before` (a direct
  `MutationOSCServer.trigger()` outside the bridge), skip the glide and send one
  update. This keeps the server API safe.
- **Cancel rules.** `_cancel_glide(reason)` snaps to the final set and logs one
  line. Call it first in: `_mutate` (a new mutation), `_begin_return` (Return/Go),
  `_jump_home`, `_back_home`, and (for the future 8.5 load) the state load. A
  transport stop does **not** cancel a glide (it is a set morph, not a note).
- The glide must not outlive `bridge.stop()`; stop it with the quantizer.

### 5.3 MIDI transient path

- `MIDIRig.on_set(..., transient: bool = False)` and `_apply(..., transient)`.
  Transient calls update the stored snapshot and fan out; they must not rebuild
  the arp pattern, reset its cursor, or print the pattern.
- `ArpEngine.on_set(..., rebuild: bool = True)`; `rebuild=False` stores the
  snapshot and leaves the pattern/cursor untouched. Add the trigger-time
  `resolve_note` mapping described in §4. `rebuild=True` is exactly today.
- Bass/lead/drone need no signature change; their existing `on_set` is cheap and
  already follows the snapshot.

### 5.4 Rate, duration, limits

- `T` is the Glide knob (0–2 s). `rate` is 30 Hz. `steps + 1` snapshots,
  including t=0 and t=T. A 0.15 s glide is 5–6 updates; a 2 s glide is 61.
- The stream is bounded and must never run faster than 60 Hz or longer than the
  knob's range.
- The sclang receiver prints one line per update; that is accepted (5 lines for
  the default glide). Do not add a per-step Python log line.

---

## 6. GUI, OSC, state

- No new knob, group, address, or config key is required. The Glide knob keeps
  its 0–2 s range and default 0.15 s.
- Add a tooltip to the Glide knob: "Stepwise/replaced class melt time: the
  replaced class travels to its new ratio over this time, for every voice."
- The Status message line may say `stepwise glide 0.150 s` when the knob moves;
  the final `/ji/freqset/changed` shows the final set. Do not add a per-step
  status message.
- `/ji/ui/state` and `/ji/home/state` are unchanged.
- Update `docs/GUI_PARAMETERS.md`'s Glide row and its "Glide in detail" note to
  the new behavior (the current text describes the old per-mutation retune).

---

## 7. Tests

### 7.1 New pure tests

```bash
uv run --with python-osc python tests/test_glide.py
```

Cover at least:

- `build_glide_plan` returns `None` for pure add/remove, zero duration, and
  inconsistent endpoints; raises on malformed input;
- one pair: `snapshot_at(0)` = before (with pure edits), `snapshot_at(T)` = after,
  midpoint = geometric mean, monotonic cents, 12-decimal canonical values;
- multiple pairs in one batch, plus pure adds/removes applied at t=0;
- anchor: replaced anchor travels, pure-removed anchor falls back, anchor always
  in the intermediate set;
- step count and timing: `steps + 1` snapshots, exact `t_k = k*T/steps`, final
  flag exactly once;
- scheduler with a fake clock: no thread in construction, lazy start, `tick`
  applies due steps only, cancel with and without snap, idempotent cancel/start,
  stop, no leaked thread;
- no `mido` import.

### 7.2 Bridge tests (extend `tests/test_glide.py` or `tests/test_home.py`)

Use the existing `FakeClient`/fake bridge pattern from `tests/test_home.py`.

- A crafted `MutationEvent(reason="stepwise", removed_ratio=a, new_ratio=b)`
  applied to the engine and announced through the bridge produces `steps + 1`
  `/ji/freqset/update` messages, the last one equal to the final set; the MIDI
  rig receives transient snapshots and one final non-transient one; the UI
  receives only the final `/ji/freqset/changed`.
- `reason="replaced"` also glides; a pure add/remove produces exactly one update.
- A new mutation during a glide cancels it (one final snap, then the new change);
  a Home Return/Go, Back, and Jump each cancel it; a transport stop does not.
- The engine state, history depth, home distance and journey counter are final
  from the moment the mutation lands (the glide does not change them).
- With the transport stopped the mutation + glide start immediately; with it
  running the mutation lands on the beat and the glide starts then.
- A glide on a bridge whose buses are missing does not raise.

### 7.3 Arp transient tests (extend `tests/test_arp.py`)

- `on_set(transient/rebuild=False)` stores the snapshot without rebuilding,
  resetting the cursor, or printing the pattern.
- During a transient snapshot, a pattern note whose ratio is absent is resolved
  to the nearest current ratio; a note whose ratio survives is unchanged.
- The final `on_set(rebuild=True)` rebuilds once and resets the cursor (today's
  behavior).

### 7.4 Existing tests

All §2 tests pass. Update only what the new behavior genuinely changes:
`tests/test_home.py` (settle/cancel an active glide before counting return
updates), `tests/test_ui.py` (the Glide tooltip if asserted), and
`tests/test_startup.py` (docs wording if it checks the Glide line). Do not edit
`tests/test_mutation.py`.

### 7.5 Optional headless Qt test

The Qt test still skips cleanly without Qt; no new widgets are required.

---

## 8. Human acceptance checklist

1. Set **Glide** to 0.3 s. Press **Mutate** until a one-for-one replacement
   happens (a class disappears and another appears): the live input and any
   playing loop *melt* from the old pitch to the new one over ~0.3 s.
2. The drone follows if it is holding the moving class; the bass/lead use the
   moving class on their next notes; the arp's pattern keeps its shape and its
   notes follow the moving class until the final rebuild.
3. Pure add/remove mutations still snap immediately (no melt, no Glide-knob
   retune).
4. Glide = 0 is effectively instant.
5. A second mutation during a melt cancels the first cleanly; no stuck note, and
   the final set is the second mutation's.
6. **Return**, **Go**, **Back**, and **Jump** during a melt cancel it cleanly and
   do what they normally do.
7. No audio/MIDI regressions in drone/arp/bass/lead/drums, groove, home sets, or
   the GUI; the window stays compact.
8. Record the result. **8.4.1 is not complete until the human confirms the class
   melts on both the autotuned audio and the MIDI voices.**

---

## 9. Diagnostics

One line per glide, never per step:

```text
[GLIDE] replaced 1.500000 -> 1.454545 over 0.150 s (5 steps)
[GLIDE] replaced 1.333333 -> 1.406250, 1.500000 -> 1.454545 over 0.300 s (10 steps)
[GLIDE] done (0.150 s)
[GLIDE] cancelled by mutation
[GLIDE] cancelled by return
[GLIDE] cancelled by back
[GLIDE] cancelled by jump
```

`[QUANT]`, `[BRIDGE]`, `[MUTATION]` and `[HOME]` lines keep their existing
formats; the final step's `[BRIDGE] -> /ji/freqset/update` line is the one set
line for the change.

---

## 10. Risks and decisions to document

- **One update per change vs a stream.** The 8.4 "one update per quarter beat"
  rule is intentionally relaxed for a glide; a glide is one logical change with
  a bounded 30 Hz stream. Outside glides the rule stands.
- **The knob no longer retunes adds/removes.** The owner defined Glide as the
  replacement melt; pure add/remove updates use the 0.005 s minimum. This is a
  deliberate behavior change, not a regression; document it in
  `GUI_PARAMETERS.md`, `README_stage8_4.md` and `START_HERE.md` (the
  `--glide-time` row).
- **Engine state is final immediately.** The audible set is intermediate; the
  UI, history, home distance and subsequent mutations use the final set.
- **Overlapping changes.** Any new set change cancels the glide and snaps to the
  final set first. A transport stop does not.
- **Arp pattern identity.** Transient updates never rebuild; the trigger-time
  nearest-ratio projection keeps the pattern's shape while following the melt;
  the final update rebuilds once. This is new behavior and must be tested.
- **Anchor movement.** A replaced anchor travels; the drone follows with its
  existing retune/bend rules (bend-only when the MIDI note is unchanged).
- **Class collision.** A moving class can cross another class (or the anchor) at
  some `t`. The intermediate snapshot must stay unique; dedupe the collision
  (the set briefly has one fewer class) and document it, or nudge by a cent if
  that is audible. Add a collision case to the plan tests.
- **Step rate.** 30 Hz is enough for the autotune's Lag and the drone's bend; the
  final step restores the knob as the autotune retune time. A 2 s glide is 61
  updates; document the cost.
- **Log volume.** The sclang receiver logs one line per update; the Python side
  logs one line per glide. Do not add per-step Python logs.
- **No sclang change expected.** If you change one, justify it and update
  `START_HERE.md`.
- **Docs drift.** `GUI_PARAMETERS.md`, `README_stage8_4.md`, the design doc and
  `README.md` are part of the acceptance.

---

## 11. Deliverables

```text
ji-live/
├── control/
│   ├── glide.py               NEW: GlidePlan, build_glide_plan, GlideScheduler
│   ├── ji_bridge.py           MOD: glide scheduler, send_snapshot, before capture,
│   │                          cancel rules, logs
│   ├── midi_rig.py            MOD: transient flag through on_set/_apply
│   └── arp.py                 MOD: on_set(rebuild=...), trigger-time resolution
├── ui/
│   └── ji_ui.py               MOD: Glide knob tooltip (+ status message if useful)
├── tests/
│   ├── test_glide.py          NEW
│   ├── test_arp.py            MOD (transient behavior)
│   ├── test_home.py           MOD (settle/cancel glides around return tests)
│   └── test_ui.py             MOD (only if it asserts the Glide text)
├── docs/
│   ├── README_stage8_4.md     MOD: "Stepwise class glide (8.4.1)" section and the
│   │                          one-update exception
│   ├── GUI_PARAMETERS.md      MOD: Glide row + detail note
│   ├── START_HERE.md          MOD: Glide/`--glide-time` description
│   └── design_doc_10.28.10.50 MOD: §6.5 (mutation), §6.19 (home/return note),
│                              §9, §14 changelog
└── README.md                  MOD: status/links if needed
```

No new config key, OSC address, or launcher flag is required. The Glide knob's
range and default stay 0–2 s / 0.15 s.

---

## 12. Self-review requirement (final response)

Provide:

- Files added/changed.
- For each requirement above, the code that satisfies it or a concrete concern.
- "If I had to bet on which line is most likely to fail on the human's machine,
  which is it, and why? Give a one-line verification command."
- Assumptions, max 5 bullets.
- Explicit statement that audio, MIDI, Ableton, IAC, sclang timing and the live
  GUI were not tested by the agent; 8.4.1 awaits human acceptance.
- The exact one-sclang + one-Python start sequence, copied from
  `docs/START_HERE.md` after your updates, so the human can paste it.

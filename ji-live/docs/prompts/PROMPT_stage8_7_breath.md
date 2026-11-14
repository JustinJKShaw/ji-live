# Stage 8.7 Agent Prompt — Breathing rhythm engine + bass hit row

You are implementing **Stage 8.7** of the JI live-performance system. Stage 8.6.2
(loop editing: memory slots, overdub, reverse, varispeed, granular redesign,
morph/melt/filter) is **built and awaiting the owner's listening check**. This
stage adds the two things the owner asked for next: a **breathing rhythm engine**
(the whole grid inhales/exhales like a diaphragm, globally and locally) and an
**editable one-row hit grid for the bass**.

Read §1 before touching anything. The owner is the authority on feel; the agent
environment may have no sclang/scsynth and no audio, so every timing/DSP claim
is reviewed code plus the human self-test, not a measurement. **Do not claim
acceptance.**

---

## 0. What the owner said (verbatim, 2026-11-03)

> "ok great the next feature i want is an advanced rhythm engine where i can
> have an option to have more organic/physical feelings rhythms. things can
> breathe, and contract and pulse. the whole tempo pulsing in a way that feels
> like a diaphram expanding and contracting (this could be global but maybe also
> local for things like the arp or loop effects). also for the bass i want to be
> able to have an optional editable grid (this is one row, it doesn't need pitch
> just hits for the groove). the breathing should also have some kind of
> adjustable parameter."

Decoded into requirements:

1. **One master breath** that makes the *played* grid breathe — events arrive
   early/late and velocities swell in a slow, physical cycle — while the
   canonical tempo and bar grid stay exact.
2. **Local breath**: the arp can breathe against the groove (own depth + half a
   cycle offset), and the loop FX can breathe on their own.
3. **Adjustable parameters**: depth, period, shape, intensity, phase; plus the
   arp's local amount. Breathing off = exactly today's timing.
4. **Bass hit row**: one editable row of hits per bar (no pitch, no per-step
   velocity), optional, with the generated pattern still available.

---

## 1. Read these files first

```text
docs/README_stage8_6.md                 8.6/8.6.1/8.6.2 baseline (§14, §15)
docs/START_HERE.md                      runtime controls + troubleshooting
docs/GUI_PARAMETERS.md                  every control, one line each
docs/design_doc_10.28.10.50             §6.14 rhythm, §6.16 bass, §6.21 loop surface,
                                        §7.1 OSC map, §9 status, §14 changelog
control/rhythm.py                       BeatClock, RhythmEngine, bar/step maths
control/quantize.py                     PHASE_* queue (unchanged!)
control/drums.py                        DrumEngine, DrumScheduler._run (swing offset)
control/bass.py                         BassSettings, build_pattern, BassScheduler._run
control/arp.py                          ArpSettings, SetArp thread, _clamp_wait
control/lead.py                         LeadSettings, LeadScheduler
control/drone.py                        DroneRig pulse/sequence scheduling
control/midi_rig.py                     set_<voice>_bpm/meter/transport forwarding
control/ji_bridge.py                    clock ownership, _sync_drums_context, state sends
control/osc_server.py                   /ji/<voice>/set_* handler pattern
control/state.py                        performance-state v1 (bass section)
ui/osc_control.py                       tables, parsers, builders, store, controller
ui/ji_ui.py                             Rhythm group, Bass group, drum grid, loop rows
tests/test_breath.py                    NEW (you add it)
tests/test_bass.py, test_state.py, test_ui.py, test_rhythm.py
synthdefs/ji_loop_voice.scd             loop voice (add the loop-FX breath)
tests/sc/test_live_rig.scd              ~ji_loop_edit, ~ji_loop_edit_args, the current
                                        29-value /ji/loop/edit echo
tests/sc/test_loop_edit.scd             human-run loop self-test
```

Run Python tests from `ji-live/`.

---

## 2. What must not change

- **The canonical tempo and grid are exact.** `BeatClock.time_of_beat`,
  `next_beat_time`, `beat_at`, `RhythmEngine` and the `/ji/rhythm/*` contract are
  **untouched** by the breath. The BPM readout never pulses, a bar line is always
  where it was, and everything queued on `PHASE_*` (mutations, groove, state
  loads) still lands on the plain grid. Breath is a *scheduling offset inside
  each voice*, never a tempo change.
- **Breath off is byte-identical.** `enabled=False` (default) must produce
  exactly today's note times and velocities in every voice, and
  `grain_breath = 0` must leave the loop voice bit-identical.
- **No drift, no reordering.** The nominal deadline of every scheduler still
  advances by exactly one step (the swing pattern is the precedent); the breath
  offset is added to the *sleep target* only, clamped to ±45 % of that voice's
  step, and the shape's mean slope over a cycle is zero (§4.2).
- **Loops stay sclang-owned and grid-locked**: the loop tempo clock, the
  beat-grain scheduler, `~ji_tempo_clock` and every loop *timing* decision are
  untouched. Only the grain **FX** breathe, locally, inside the voice.
- Loops: no bridge changes, no new loop state in a performance state, memory
  slots remain session-only.
- **Performance-state v1 stays readable**: new bass keys are optional with
  defaults; old files load; the format `version` stays **1**.
- The compact 8 pt GUI with Status visible. This stage adds **one row** to the
  Rhythm group and a **hidden** bass hit row, so the default window grows to
  about 1280×966 (Qt bound ≤ 990; the scroll area stays the net).
- `MutationEngine` and every other voice's *sound*: untouched.
  `tests/test_mutation.py` passes unmodified.
- The one-sclang + one-Python start path (`ji_start.scd` + `run/ji_station.py`).

---

## 3. Scope

### 3.1 In scope

1. **`control/breath.py`** (new, pure): breath settings, four shape curves, the
   offset/level maths, and a monotonic `BreathClock` (phase, tempo re-anchor,
   reset on transport start).
2. **Global breathing** for drums, bass, arp + lead (timing **and** velocity)
   and the drone (velocity only), applied inside each scheduler.
3. **Local arp breath**: `arp_local` depth, half a cycle out of phase.
4. **Loop-FX breath**: two new loop-edit params (`grain_breath`,
   `grain_breath_rate`) modulating the grain density and size inside
   `\ji_loop_voice`; 0 = exact bypass.
5. **Bass hit row**: `hits_enabled` + one bar of hits in `BassSettings`, a
   pattern override in the engine, `/ji/bass/*hits*` OSC, a GUI row, and the
   state round-trip.
6. Status/OSC/GUI/docs/tests for all of it.

### 3.2 Out of scope (say it in the stage README)

Real tempo automation/curves (the BPM is still a constant), polyrhythmic or
per-voice independent breath periods, per-voice breath depth for drums/bass/lead
(the arp is the only local voice), breath on the mutation/quantize queue,
breath on sclang loop/record timing, MPE velocity curves, per-step velocity or
accent in the bass hit row, hit rows longer than one bar or several bass rows,
persisting breath settings in `config/`.

---

## 4. Breath engine — `control/breath.py`

Pure module, no sockets, no threads, injectable clock. Import it as
`from control import breath as breath_module` (bridge) / `import breath`
(direct-run fallbacks, same style as the other modules).

### 4.1 Settings

```python
BREATH_SHAPES = ("sine", "triangle", "inhale", "exhale")
BREATH_DEFAULT_SHAPE = "inhale"
BREATH_DEPTH_MAX = 1.0
BREATH_PERIOD_BARS_MIN = 1
BREATH_PERIOD_BARS_MAX = 32
BREATH_DEFAULT_PERIOD_BARS = 4
BREATH_DEFAULT_DEPTH = 0.35
BREATH_DEFAULT_INTENSITY = 0.5
BREATH_DEFAULT_PHASE = 0.0
BREATH_DEFAULT_ARP_LOCAL = 0.0
BREATH_MAX_SHIFT_STEPS = 0.5     # of a 16th note, at depth 1.0
BREATH_VELOCITY_SWING = 14       # +/- MIDI velocity at intensity 1.0
```

`BreathSettings` is a frozen, slots dataclass with `enabled`, `depth`,
`period_bars`, `shape`, `intensity`, `phase`, `arp_local`, the same
`create(**values)` / `with_change(name, value)` contract as `BassSettings`, and
`state_args()` returning the 7-value `/ji/breath/state` payload in the order of
§6. Every value is validated/clamped (`depth/intensity/phase/arp_local` 0..1,
`period_bars` 1..32 int, `shape` one of the four, `enabled` bool).

### 4.2 The curve (this exact maths)

The breath is a **volume** `V(φ) ∈ [0, 1]` over the cycle `φ ∈ [0,1)`, built as a
raised cosine over a warped phase `w(φ)`:

```python
def _warp(shape, phase):
    p = phase % 1.0
    if shape == "sine":     return p
    if shape == "triangle": return 1.0 - abs(2.0 * p - 1.0)
    if shape == "inhale":   return p ** 0.6      # fast fill, slow release
    return p ** 1.6                              # slow fill, fast release  ("exhale")

def breath_volume(shape, phase):
    w = _warp(shape, phase)
    return 0.5 - 0.5 * math.cos(2.0 * math.pi * w)
```

* **Level** (velocity/expression) = `V(φ)`.
* **Timing** follows the *flow*, i.e. the derivative: the music pushes while the
  diaphragm fills and relaxes while it empties.

```python
def breath_slope(shape, phase, _h=1e-4):
    return (breath_volume(shape, phase + _h)
            - breath_volume(shape, phase - _h)) / (2.0 * _h)

# normalise so the peak shift is exactly BREATH_MAX_SHIFT_STEPS at depth 1
_SLOPE_NORM = {s: max(abs(breath_slope(s, i / 512.0)) for i in range(512))
               for s in BREATH_SHAPES}

def breath_offset_steps(shape, phase, depth):
    if depth <= 0.0:
        return 0.0
    return depth * BREATH_MAX_SHIFT_STEPS * breath_slope(shape, phase) / _SLOPE_NORM[shape]
```

`V(1) == V(0)` for every shape, so the mean slope over a cycle is exactly 0:
**no cumulative drift, ever.** `|offset| <= depth * 0.5` of a 16th. Do not
integrate, do not add a random walk, do not touch `BeatClock`.

### 4.3 `BreathClock`

One instance owned by the bridge, shared (by reference) with every scheduler:

```python
class BreathClock:
    def __init__(self, settings=None, *, bpm=120.0, meter_num=4, meter_den=4,
                 now=time.monotonic) -> None
    @property
    def settings(self) -> BreathSettings
    def set_settings(self, settings) -> BreathSettings   # a phase change rotates on purpose
    def set_tempo(self, bpm, meter_num, meter_den) -> None  # re-anchor, no jump
    def reset(self, at=None) -> None        # phase := settings.phase, epoch := at
    def phase_at(self, at=None) -> float
    def offset_seconds(self, voice: str, at=None) -> float
    def level(self, voice: str, at=None) -> float       # 0..1
    def velocity_delta(self, voice: str, at=None) -> int
```

* `period_seconds = period_bars * rhythm.bar_seconds(bpm, meter_num, meter_den)`
  (`control.rhythm`). The phase is
  `(settings.phase + (now - epoch) / period) % 1.0`.
* `set_tempo` recomputes the period and moves the epoch to `now` with the phase
  preserved (`new_phase = phase_at(now)`), so a tempo change never jumps the
  breath.
* `reset` is called by the bridge on a transport **start** so every take begins
  at the bottom of the inhale. A transport stop does **not** freeze the clock
  (a free-running arp keeps breathing); it resets on the next start anyway.
* **Voices**: `"drums"`, `"bass"`, `"arp"`, `"lead"`, `"drone"`.
  - `drums`/`bass`/`lead`/`drone`: depth `settings.depth`, phase `φ`.
  - `arp`: depth `min(1, settings.depth + settings.arp_local)`, phase `φ + 0.5`
    — the arp breathes *against* the groove.
* `offset_seconds` = `breath_offset_steps(...) * (60 / bpm / 4)` (one 16th in
  seconds), 0 when disabled or depth 0.
* `level` = `breath_volume(...)` when enabled, else `0.5` (a flat centre).
* `velocity_delta` = `round(intensity * BREATH_VELOCITY_SWING * (2*level - 1))`,
  `0` when disabled. (At `level = 0.5` the delta is 0, so the default breath is
  a pure timing feel until intensity is raised.)

### 4.4 Scheduler integration (the swing precedent)

Every voice already computes an `offset` (swing) added to the sleep target
while the nominal deadline advances by exactly one step
(`DrumScheduler._run`, `BassScheduler._run`; the arp/lead do the same through
`deadline`/`_clamp_wait`). Add breath the same way:

```python
offset = self.engine.swing_offset(step, step_length)   # existing
offset += self._breath_offset(step_length)             # NEW (0.0 when no breath)
target = deadline + offset
...
deadline += step_length                                # nominal: unchanged
```

`_breath_offset` returns `0.0` when `self._breath is None` (the default, so every
existing test keeps its timing) and otherwise:

```python
value = self._breath.offset_seconds("drums", self._now())
limit = 0.45 * step_seconds
return max(-limit, min(limit, value))
```

Details per voice:

* **Drums** (`control/drums.py`, `DrumScheduler`): add `breath=None` to
  `__init__` + `set_breath(breath)`; offset as above (cap also by
  `step_length - swing_offset` like the gate limit if it helps). Velocity:
  `self.output.note_on(note, velocity=self._velocity_with_breath())` where the
  base is `self.output.velocity` (or `engine.velocity`) plus
  `breath.velocity_delta("drums")`, clamped 1..127. Never a delta when the
  breath is off.
* **Bass** (`control/bass.py`, `BassScheduler`): same offset pattern (after the
  existing `swing_offset`); velocity = `engine.velocity_for(downbeat)` +
  `velocity_delta("bass")` for both the main and the sub note (the sub keeps its
  own base).
* **Arp** (`control/arp.py`, `SetArp._run`): fold the offset into the first
  argument of `_clamp_wait(deadline + breath_offset, realign_at, evolve_at)` so a
  beat snap or an evolution still wins; the nominal `deadline += step` stays.
  Velocity = `settings.velocity + velocity_delta("arp")`.
* **Lead** (`control/lead.py`, `LeadScheduler._run`): the same
  `target = deadline + offset` pattern as drums/bass (`deadline += step_seconds`
  stays nominal). Velocity = `settings.velocity + velocity_delta("lead")`.
* **Drone** (`control/drone.py`, the pulse/sequence scheduler): **velocity only**
  — add `velocity_delta("drone")` where `settings.velocity` is passed to
  `note_on`. Its cycle timing stays quarter-note based.
* **No breath** in `quantize.py`, `rhythm.py`, the mutation engine, the state
  loader, or the `/ji/rhythm/*` bridge contract.

Wire it up:

* Each rig gets a one-line `set_breath(breath)` that forwards to its scheduler:
  `DrumRig`, `BassRig`, `SetArp` (the arp *is* its scheduler), `LeadRig` →
  `LeadScheduler`, `DroneRig`.
* `control/midi_rig.py`: `set_breath(self, breath)` forwarding to
  `self.drums`, `self.bass`, `self.arp`, `self.lead`, `self.drone_rig`
  (each guarded, like the existing `set_<voice>_bpm` helpers).
* `control/ji_bridge.py`: own `self.breath = breath_module.BreathClock(...)`;
  `self._midi_call("set_breath", self.breath)` inside `_sync_drums_context`
  (idempotent, also covers a rig created later); `set_tempo` on every
  tempo/meter change (`_on_rhythm_*`), `reset()` when the transport starts
  (`_on_rhythm_transport(True)`); send `/ji/breath/state` at start, on hello,
  and after every breath change. Log one line per change:
  `[BREATH] on depth=0.35 period=4 bars shape=inhale intensity=0.50 arp_local=0.00`.

**Do not** pass the breath into `BeatClock`, `RhythmEngine`, `Quantizer` or the
state file.

---

## 5. Loop-FX breath (sclang, local)

Two new **loop edit** params, per loop, independent of the bridge breath (the
loop FX breathe on their own LFO — document that):

| Param | Wire name | Range | Default | Meaning |
|---|---|---|---|---|
| Breath | `grain_breath` | 0..1 | 0.0 | modulation depth |
| B.Rate | `grain_breath_rate` | 0.02..1 Hz | 0.08 | modulation speed |

In `synthdefs/ji_loop_voice.scd`:

```supercollider
//  All vars at the top of the function, before the first statement.
var breathLfo = SinOsc.kr(grainBreathRate.clip(0.02, 1.0), 0);   // -1..1
var breathe = grainBreath.clip(0, 1) * breathLfo;                // -1..1
...
var freeTrig = Impulse.kr((grainRate * (1 + (0.6 * breathe))).clip(0.1, 80));
var burstDensity = (grainRate * (1 + burst) * (1 + (0.6 * breathe))).clip(0.1, 80);
var grainDur = (jitteredSize * (1 + (0.35 * breathe))).clip(0.005, 0.5);
```

Replace the existing `freeTrig` / `burstDensity` / `grainDur` expressions with
those (the vars stay declared at the top; only the assignments change).
`grainBreath = 0` must be bit-identical (factor 1). Add the args with defaults
`grainBreath = 0.0, grainBreathRate = 0.08`.

sclang side (`tests/sc/test_live_rig.scd`): add `grainBreath: 0.0,
grainBreathRate: 0.08` to `~ji_loop_edit`, clamp in `~ji_loop_set_edit`
(`grain_breath` 0..1, `grain_breath_rate` 0.02..1), add
`\grainBreath`/`\grainBreathRate` to `~ji_loop_edit_args`, and extend the
`/ji/loop/edit` payload from **29 to 31 values** (append `grain_breath,
grain_breath_rate` after `grain_filter_rate`). `ui/control_surface.scd`: bump
`~ji_ui_control_surface_version` to `"8.7.0"` and update its header comment.

**Watch out:** `var` declarations are only legal **at the top of a function**
(SC Getting Started 04). A late `var` makes the SynthDef fail to parse and
`ji_start.scd` reports `missing SynthDefs: [ji_loop_voice]`. Keep every var above
the first `Out.kr`.

Python mirrors: `LOOP_EDIT_PARAMS` 27 → **29**, `LoopEdit` 29 → **31** fields,
`parse_loop_edit` strict **31**, `/ji/loop/edit_set` accepts the two new names.

---

## 6. Bass hit row

The existing `BassSettings.grid` is the *subdivision* (1/4, 1/8, 1/16). The new
hit row must **not** reuse that name — call it **hits**.

### 6.1 Settings and engine (`control/bass.py`)

```python
BASS_HITS_MAX = 32        # one row, one bar of the current grid, capped

@dataclass(frozen=True, slots=True)
class BassSettings:
    ...
    hits_enabled: bool = False
    hits: tuple[bool, ...] = ()
```

* `create()` validates: `hits_enabled` bool; `hits` coerced from any sequence
  to `tuple(bool(...))`, length 0..`BASS_HITS_MAX`, extra items truncated.
* The row length for the current grid/meter is
  `bar_steps(settings, meter_num, meter_den) = round(bar_quarters * steps_per_beat)`
  clamped 1..32 (4/8/16 steps in 4/4 for 1/4, 1/8, 1/16; 3/6/12 in 3/4).
* `effective_hits()` pads/truncates the stored row to `bar_steps`; an
  **empty** stored row (`()`, i.e. never set) behaves as step 0 on, so a fresh
  loop still plays. A row the user cleared is all-False (deliberate silence).
* `BassEngine.pattern(bar_index)` returns a `BassPattern` whose triggers are
  **exactly the hit row** when `hits_enabled`, with `length_steps = bar_steps`
  (the row *is* one bar: while Hits is on, `Len` is not used — say that in the
  GUI tooltip and the docs), and the generated pattern otherwise. The pitch
  picker, accent and gate are untouched (`note_at`, `velocity_for`,
  `downbeat = step % length == 0`).
* New engine methods (each validates, bumps the version and clears the
  pattern cache):
  - `set_hits_enabled(on)` — when turning it **on** with a still-empty stored
    row, call `copy_hits()` first, so enabling starts from what the user is
    hearing.
  - `set_hit(index, on)` — index 0..31; pads the row to the bar length.
  - `clear_hits()` — all off, row length = bar (deliberate silence).
  - `copy_hits(bar_index=0)` — seed the row from the **generated** pattern of
    that bar: call `build_pattern()` directly, **never** the cached
    `pattern()` (which returns the hit row itself when enabled), then
    truncate/pad to the bar length. The GUI **Copy** button calls this.
  - `hits_payload()` → `[enabled, count, hit_0 ... hit_{count-1}]` using the
    **effective** row, so the GUI always draws exactly `bar_steps` buttons
    after a meter or subdivision change.
* `BassRig` forwards those methods (plus `hits_payload()`); `MIDIRig` too
  (guarded, like `set_bass_setting`). A playing scheduler picks the new row up
  on its next step because the engine version changed.
* The hit row is edited through those dedicated methods/addresses, **not**
  through `with_change` (which stays the beat-queued settings path). `create()`
  still accepts the two keys so a recalled state applies them wholesale.
* A preset change, a meter change or a `grid` (subdivision) change must **not**
  clear the hits; the row is re-shaped to the new bar (§6.1).

### 6.2 OSC (`control/osc_server.py`, `control/ji_bridge.py`)

| Address | Args | Direction |
|---|---|---|
| `/ji/bass/set_hits_enabled` | [0/1] | UI → bridge |
| `/ji/bass/set_hit` | [int index 0..31, 0/1] | UI → bridge |
| `/ji/bass/clear_hits` | — | UI → bridge |
| `/ji/bass/copy_hits` | — | UI → bridge |
| `/ji/bass/hits` | [int enabled, int count, int hit_0 … int hit_{count-1}] | bridge → UI |

Handlers validate arity/type, never raise, and **apply immediately** (a hit
edit is a performance gesture, like a drum-grid step) — do not queue on a
`PHASE_*`. After each change the bridge sends `/ji/bass/hits` and logs one line
(`[BASS] hits enabled row=1/16 steps=16`). `/ji/bass/state` stays at its
**current 20 values** (no change — existing parsers and tests must keep
working). Send `/ji/bass/hits` at bridge start, on `/ji/ui/hello`, after every
hit change and whenever the meter or `grid` changes the row length.

### 6.3 Breath OSC

| Address | Args | Direction |
|---|---|---|
| `/ji/breath/set_enabled` | [0/1] | UI → bridge |
| `/ji/breath/set_depth` | [0..1] | UI → bridge |
| `/ji/breath/set_period` | [int 1..32] (bars) | UI → bridge |
| `/ji/breath/set_shape` | ["sine"\|"triangle"\|"inhale"\|"exhale"] | UI → bridge |
| `/ji/breath/set_intensity` | [0..1] | UI → bridge |
| `/ji/breath/set_phase` | [0..1] | UI → bridge |
| `/ji/breath/set_arp_local` | [0..1] | UI → bridge |
| `/ji/breath/state` | [enabled, depth, period_bars, shape, intensity, phase, arp_local] | bridge → UI |

Immediate (not beat-queued). Validate strictly, clamp, log one line, send the
state back.

---

## 7. Python UI layer (`ui/osc_control.py`)

- Constants: `BREATH_SHAPES`, `BREATH_DEFAULT_*`, `BASS_HITS_MAX`,
  `BASS_HITS_DEFAULT_*`, and the new loop-edit defaults.
- `@dataclass BreathStatus` with the 7 fields + `status_line()`:
  `Breath: off` / `Breath: on · depth 35 % · 4 bars · inhale · int 50 % · arp 0 %`.
- `parse_breath_state(args)` strict 7 values, clamped; `parse_bass_hits(args)`
  strict `len == 2 + count` with `count` 0..32 (reject a mismatch), returning
  `(enabled, tuple[bool, ...])`.
- Builders: `build_breath_param(name, value)` (whitelist of the 7 names,
  clamped/validated), `build_bass_hits_enabled`, `build_bass_hit(index, on)`,
  `build_bass_clear_hits`, `build_bass_copy_hits`.
- `StatusStore`: `breath: BreathStatus | None`, `bass_hits: tuple[bool, ...] |
  None` + `bass_hits_enabled: bool`; `apply()` for `/ji/breath/state` and
  `/ji/bass/hits`.
- `OSCController`: `set_breath_param`, `bass_set_hits_enabled`,
  `bass_set_hit`, `bass_clear_hits`, `bass_copy_hits`.
- Loop edit mirrors for the two new params (like 8.6.2): `LOOP_EDIT_PARAMS`
  entries, `LoopEdit` fields, `parse_loop_edit` 31, `LoopEdit.status_line()`
  gains `breath N %` when non-zero (keep it short: the line is already long).
- Export every new name in `__all__`.

---

## 8. GUI (`ui/ji_ui.py`)

### 8.1 Breath row (inside the **Rhythm** group, one row, under the top row)

```text
Breath [x]  Depth [0..100 %]  Period [1..32 b]  Shape [sine/triangle/inhale/exhale]
Intensity [0..100 %]  Phase [0..100 %]  Arp local [0..100 %]
```

| Widget | Range / default | Tooltip gist |
|---|---|---|
| `breath_enable_checkbox` | off | makes the grid breathe (timing + velocity) |
| `breath_depth_spin` | 0–100 % (35) | how far events move early/late (max ±½ 16th) |
| `breath_period_spin` | 1–32 b (4) | one full inhale+exhale in bars |
| `breath_shape_combo` | 4 shapes (`inhale`) | where the breath accelerates: sine/triangle/inhale (fast fill)/exhale (fast release) |
| `breath_intensity_spin` | 0–100 % (50) | velocity/expression swell |
| `breath_phase_spin` | 0–100 % (0) | rotate the breath against the bar |
| `breath_arp_spin` | 0–100 % (0) | the arp's own depth, half a cycle out of phase |

Send the value on change (divide the percent spins by 100; period is an int);
sync from `/ji/breath/state` signal-blocked (never echo). Add a `breath_hint`
label only if room allows (e.g. `breathing · 4 bars`).

### 8.2 Bass hits (Bass group)

- On the **bass status row** (where `bass_label` is): a `Hits` checkbox, a
  `Copy` and a `Clear` button. Enabling `Hits` sends `set_hits_enabled 1` (the
  bridge seeds the row from the generated pattern when it is empty) and shows
  the row.
- Under it: a **hidden** one-row `QScrollArea` (max height ~34 px, like the
  drum grid) holding `bar_steps` checkable `QPushButton`s (`step 0..N-1`).
  Built lazily; rebuilt when `/ji/bass/hits` reports a new count (meter/grid
  change). Clicking a step sends `set_hit(index, on)`; the echo re-syncs the
  button signal-blocked.
- `Copy` sends `copy_hits`; `Clear` sends `clear_hits`. Disable the whole hits
  row only while the bass **port** is unavailable (same rule as the other bass
  controls); keep it editable while the bass is merely switched off, so the
  owner can prepare a row before enabling the voice.
- The hidden row must not change the default window height (only `Show`-style
  toggles do).

### 8.3 Loop Morph row

Add `Breath` (0–100 %) and `B.Rate` (0.02–1 Hz) next to `Filter`/`F.Rate`.
The row is dense: **drop the `morph_hint` label** (its information is in the
status line) and keep the whole row ≤ ~1200 px. Grain off disables the loop
breath like the other grain controls.

### 8.4 Window

`resize(1280, 966)` and update the Qt height bound from 950 to **990** (three
places in `tests/test_ui.py`), plus the comments that say 940.

---

## 9. Docs

- **NEW `docs/README_stage8_7.md`**: the feature, the breath curve diagram, the
  scheduler recipe, the bass row, OSC tables, risks, the human checklist,
  out-of-scope.
- `docs/START_HERE.md`: the Breath controls + the `Hits` row in the loop/bass
  section; troubleshooting rows: *breath does nothing* (off / depth 0 / period
  too long), *everything sounds late or early* (phase/depth), *the arp still
  breathes when the groove doesn't* (arp_local), *the bass plays the old
  pattern* (Hits off), *my hits vanished* (Clear/state recall/Copy).
- `docs/GUI_PARAMETERS.md`: the Breath row and the Hits row (range/default/what
  it does).
- `docs/design_doc_10.28.10.50`: §6.14 (breath sub-section with the maths),
  §6.16 (bass hits), §6.21 (loop surface: 31 values, 29 params), §7.1 (OSC rows
  above), §8.9 row `8.7`, §9 status row, §13 deferred, §14 changelog `8.7.0`.
- Root `README.md`: status paragraph, test list (`tests/test_breath.py`), the
  docs links and the prompt link.
- Keep `docs/prompts/PROMPT_stage8_7_breath.md` (this file) as the stage record.

---

## 10. Tests

### 10.1 Python (from `ji-live/`)

```bash
uv run --with python-osc python tests/test_breath.py
uv run --with python-osc python tests/test_rhythm.py
uv run --with python-osc python tests/test_bass.py
uv run --with python-osc python tests/test_state.py
uv run --with python-osc python tests/test_arp.py
uv run --with python-osc python tests/test_lead.py
uv run --with python-osc python tests/test_drone.py
uv run --with python-osc python tests/test_groove.py
uv run --with python-osc python tests/test_quantize.py
uv run --with python-osc python tests/test_home.py
uv run --with python-osc python tests/test_glide.py
uv run --with python-osc python tests/test_startup.py
uv run --with python-osc python tests/test_mutation.py          # unmodified
uv run --with python-osc python tests/test_mutation_controls.py
uv run --with python-osc python tests/test_ui.py --pure
python tests/test_mpe.py --pure
# Qt (needs PyQt6)
QT_QPA_PLATFORM=offscreen uv run --with python-osc --with PyQt6 \
  python tests/test_ui.py --qt
```

Existing harnesses must pass **unmodified** except `test_bass.py`,
`test_state.py` and `test_ui.py` (extended for the new fields).

New coverage:

* **`tests/test_breath.py`** (new, pure):
  - settings validation/clamps (`create`, `with_change`, bad shape/period);
  - `breath_volume` in 0..1, `V(0) == 0`, `V(1) ≈ 0`, every shape;
  - the mean slope over 2048 samples is ≈ 0 (±1e-3) for every shape → no drift;
  - `|offset_steps| <= depth * 0.5` and `offset == 0` at depth 0 / disabled;
  - `velocity_delta` is 0 at level 0.5, ≤ `BREATH_VELOCITY_SWING`, 0 when off;
  - `BreathClock` with an injected `now`: phase wraps, `set_tempo` keeps the
    phase continuous, `reset` restarts at `settings.phase`, the arp phase is
    half a cycle off and its depth includes `arp_local`, a stopped transport
    keeps the phase moving.
  - **drift proof**: simulate 1000 steps of a fake scheduler at 120 BPM with
    depth 1.0 and assert the nominal deadline advances exactly `1000 * step`
    while the breath-shifted targets stay within the clamp.
* **Scheduler integration** (one per voice, or at least drums): inject a fake
  breath object into the scheduler and assert that a positive offset delays the
  note, a negative one advances it, the nominal deadline sequence is unchanged,
  and `None`/breath-off produces exactly the old timing.
* **`test_bass.py`**: hits validation (length/type/clamp), `effective_hits`
  padding, `set_hit`/`clear_hits`/`copy_hits`, `pattern()` override +
  `triggers_at`, pitch path unchanged, `hits_payload()` arity, `Len` ignored
  while hits are on, a preset change keeps the hits, `bar_steps` for 4/4 and
  3/4 at all three subdivisions.
* **`test_state.py`**: a state with the new bass keys round-trips; a state
  **without** them loads with `hits_enabled=False, hits=()`; the version stays
  1; unknown keys are still ignored.
* **`test_ui.py`**:
  - breath: builders clamp/reject, strict 7-value parser, store application,
    controller addresses (bridge only), Qt row exists, sends for each control,
    incoming state syncs without echo, window bound;
  - hits: builders/parser (count mismatch rejected), store, Qt `Hits`
    checkbox/step buttons/Copy/Clear still hidden row by default, echo sync,
    no echo back;
  - loop breath: `LOOP_EDIT_PARAMS` 29, `LoopEdit` 31, strict 31-value parser,
    Qt `Breath`/`B.Rate` sends, status line.

### 10.2 sclang self-test — `tests/sc/test_loop_edit.scd` (human-run)

Extend with PASS/FAIL for `grain_breath`/`grain_breath_rate` defaults, clamps,
`~ji_loop_edit_args` carrying both args, and (in the voice block) that
`grainBreath = 0` still produces a voice. Add manual steps: *loop breath 0 % =
unchanged; 30–60 % makes the cloud swell and thin; B.Rate 0.03 is a long breath,
0.3 a pulse.*

---

## 11. Human acceptance checklist (the owner is the authority)

1. **No regression**: breath off and `grain_breath 0 %` — the rig sounds
   exactly as 8.6.2 (drums/bass/arp/lead/drone timing, loop FX, no extra
   latency).
2. **Global breath**: on, depth 35 %, 4 bars, inhale, intensity 50 % — the whole
   groove audibly inhales/exhales; the BPM display never moves and bar lines stay
   put. Depth 100 % is dramatic but never reorders hits; period 1 bar is a fast
   pulse, 16 bars a long swell.
3. **Shape**: sine vs triangle vs inhale vs exhale are clearly different
   characters.
4. **Intensity**: 0 % = timing only; 100 % = strong velocity/expression swell.
5. **Arp local**: with global depth 0 and arp local 60 %, the arp breathes by
   itself (half a cycle offset); the drums stay steady. Raise global depth and
   the arp breathes against them.
6. **Loop FX breath**: a granular loop's density/size swells with `Breath`;
   0 % is unchanged.
7. **Bass hits**: enabling `Hits` seeds the row from the current pattern; clicks
   add/remove hits and the bass plays exactly them (pitch/accent/gate from the
   current mode); Clear silences the hits; Copy re-seeds from the generated
   pattern; the row re-shapes when the Grid combo or the meter changes; state
   save/recall keeps the row.
8. **Quantized actions** (mutate, go, state load, groove edits) still land on
   the beat grid.
9. CPU/MIDI timing stay usable with all voices.
10. Record the result. **8.7 is not complete until the owner confirms the feel.**

---

## 12. Risks

- **Timing correctness is the whole stage.** The invariant is: nominal deadlines
  never change, the offset is added only to the sleep target and is clamped to
  ±45 % of the voice's step, and the shape has zero mean slope. Any of those
  broken = drift or reordered hits. Add the 1000-step drift test (§10.1).
- **Do not touch `BeatClock`.** Breath inside the clock would move the
  quantizer, the BPM readout and the state queue.
- **MIDI jitter**: the offset (default ±22 ms at 120 BPM) is larger than the
  thread jitter; do not add smoothing that would destroy the shape.
- **Arp boundaries**: the breath offset must be inside `_clamp_wait`, never
  after it, or a beat snap/evolution can land late.
- **Tempo change during a breath**: re-anchor the phase (no jump). A transport
  start resets to the bottom of the inhale.
- **Bass hits vs the `grid` setting**: `grid` is the subdivision; the row is
  `hits`. Never overload the names.
- **State**: v1 must still load old files; the breath is **not** saved (it is a
  live feel control — say so in the README; if the owner later wants it saved,
  it becomes a v2 question).
- **SynthDef `var` placement** (see §5): a late `var` silently loses the whole
  voice.
- **GUI width/height**: the Rhythm row and the Morph row are dense; drop the
  morph hint and keep the window ≤ 1280×980.
- **No audio in the agent environment**: the self-test and the checklist are
  the verification. Do not claim acceptance.

---

## 13. Deliverables

```text
ji-live/
├── control/breath.py                  NEW: settings, curves, BreathClock
├── control/drums.py                   MOD: set_breath, offset + velocity
├── control/bass.py                    MOD: hits settings/engine + set_breath
├── control/arp.py                     MOD: set_breath, _clamp_wait offset, velocity
├── control/lead.py                    MOD: set_breath, offset + velocity
├── control/drone.py                   MOD: set_breath (velocity only)
├── control/midi_rig.py                MOD: set_breath forwarding, bass hits forwarding
├── control/ji_bridge.py               MOD: breath clock, tempo/transport hooks,
│                                          /ji/breath/state, /ji/bass/hits
├── control/osc_server.py              MOD: 7 breath + 4 hits handlers
├── control/state.py                   MOD: bass hits in v1 (optional keys)
├── ui/osc_control.py                  MOD: tables/parsers/builders/store/controller
├── ui/ji_ui.py                        MOD: Breath row, Bass hits row, loop breath
├── synthdefs/ji_loop_voice.scd        MOD: grainBreath/grainBreathRate
├── tests/sc/test_live_rig.scd         MOD: 2 loop keys, 31-value payload
├── tests/sc/test_loop_edit.scd        MOD: loop breath checks + manual steps
├── tests/test_breath.py               NEW
├── tests/test_bass.py                 MOD: hits
├── tests/test_state.py                MOD: hits round-trip
├── tests/test_ui.py                   MOD: pure + Qt
├── docs/README_stage8_7.md            NEW
├── docs/START_HERE.md                 MOD: controls + troubleshooting
├── docs/GUI_PARAMETERS.md             MOD: Breath + Hits rows
├── docs/design_doc_10.28.10.50        MOD: §6.14/§6.16/§6.21/§7.1/§8.9/§9/§13/§14
└── README.md                          MOD: status, tests, docs links
```

No changes to the bridge's `/ji/freqset/update` contract, the audio map or the
loop ownership model.

---

## 14. Self-review requirement (final response)

Provide:

- Files added/changed.
- For each requirement above, the code that satisfies it or a concrete concern.
- "If I had to bet on which line is most likely to fail on the human's machine,
  which is it, and why? Give a one-line verification command."
- Assumptions, max 5 bullets.
- Explicit statement that audio, MIDI, sclang timing and the live GUI were not
  tested by the agent; 8.7 awaits human acceptance.
- The exact one-sclang + one-Python start sequence, copied from
  `docs/START_HERE.md` after your updates.

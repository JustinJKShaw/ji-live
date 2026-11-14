# Stage 8.7 — Breathing rhythm engine + bass hit row

**Status:** built 2026-11-03, **awaiting the owner's listening check**. **8.7.1
(2026-11-04) adds the owner-requested `Arp sync` option** (the arp can use the
master breath phase instead of its half-cycle offset). This document describes
the feature, the exact maths, the scheduler recipe, the OSC/GUI surface, the
risks and the human checklist. Audio, MIDI, sclang timing and the live GUI were
**not** available in the agent environment; nothing here is an acceptance claim.

Baseline: 8.6.2 (loop editing: memory slots, overdub, reverse, varispeed,
granular redesign, morph/melt/filter) is built and awaiting its own listening
check. 8.7 is additive and leaves every accepted sound path untouched when the
breath is off and the bass hit row is off.

---

## 1. What the owner asked for (2026-11-03)

> "ok great the next feature i want is an advanced rhythm engine where i can
> have an option to have more organic/physical feelings rhythms. things can
> breathe, and contract and pulse. the whole tempo pulsing in a way that feels
> like a diaphram expanding and contracting (this could be global but maybe also
> local for things like the arp or loop effects). also for the bass i want to be
> able to have an optional editable grid (this is one row, it doesn't need pitch
> just hits for the groove). the breathing should also have some kind of
> adjustable parameter."

Decoded:

1. **One master breath** makes the *played* grid breathe — events arrive
   early/late and velocities swell — while the canonical tempo and bar grid stay
   exact.
2. **Local breath:** the arp can breathe against the groove, and the loop FX can
   breathe on their own.
3. **Adjustable parameters:** depth, period, shape, intensity, phase, plus the
   arp's local amount. Breath off = exactly today's timing.
4. **Bass hit row:** one editable row of hits per bar (no pitch, no per-step
   velocity), optional, with the generated pattern still available.

---

## 2. The rule that must never break

**The canonical tempo and grid are exact.** `control/rhythm.py` (`BeatClock`,
`RhythmEngine`), the `/ji/rhythm/*` contract, the `PHASE_*` quantizer queue, the
mutation engine and every sclang loop/record timing decision are **untouched**.
The BPM readout never pulses; a bar line is always where it was; everything
queued on a `PHASE_*` still lands on the plain grid.

Breath is a **scheduling offset inside each voice**, exactly like the existing
swing offset:

```text
offset  = swing_offset(step) + breath_offset(step)   # seconds
target  = deadline + offset                          # the SLEEP TARGET only
...
deadline += step_seconds                             # nominal: always one step
```

The offset is clamped to **±45 % of that voice's step** and the curve has a
**zero mean slope over a cycle**, so there is no drift and no reordering. When
`enabled=False` (the default) every scheduler adds exactly `0.0` and every note
time/velocity is today's. When `grainBreath = 0` the loop voice is
bit-identical.

---

## 3. `control/breath.py` (new, pure)

No sockets, no threads, no audio, an injectable `now`. Imported as
`from control import breath as breath_module` (bridge) and `import breath`
direct-directory fallbacks, like the other modules.

### 3.1 Settings

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
BREATH_DEFAULT_ARP_SYNC = False     # True = arp uses the master phase
BREATH_MAX_SHIFT_STEPS = 0.5     # of a 16th note, at depth 1.0
BREATH_VELOCITY_SWING = 14       # +/- MIDI velocity at intensity 1.0
```

`BreathSettings` is a frozen slots dataclass with `enabled`, `depth`,
`period_bars`, `shape`, `intensity`, `phase`, `arp_local`, `arp_sync`;
`create(**values)`, `with_change(name, value)` and `state_args()` (the 8-value
`/ji/breath/state` payload; 8.7.1 appends `arp_sync`). Every value is
validated/clamped.

### 3.2 The curve

The breath is a volume `V(φ) ∈ [0, 1]` over one cycle, a raised cosine over a
warped phase:

```python
def _warp(shape, phase):
    p = phase % 1.0
    if shape == "sine":     return p
    if shape == "triangle": return 1.0 - abs(2.0 * p - 1.0)
    if shape == "inhale":   return p ** 0.6      # fast fill, slow release
    return p ** 1.6                              # slow fill, fast release

def breath_volume(shape, phase):
    w = _warp(shape, phase)
    return 0.5 - 0.5 * math.cos(2.0 * math.pi * w)
```

```text
 V
 1 |        .-'''''-.
   |      .'         '.
0.5|    .'             '.
   |  .'                 '.
 0 +-'---------------------'-   φ
   0        0.5            1
        inhale: fast fill, slow release (the default)
```

* **Level** (velocity/expression) = `V(φ)`.
* **Timing** follows the *flow* (the derivative `dV/dφ`): the music pushes while
  the diaphragm fills and relaxes while it empties.

```python
def breath_slope(shape, phase, _h=1e-4):
    return (breath_volume(shape, phase + _h)
            - breath_volume(shape, phase - _h)) / (2.0 * _h)

# peak shift exactly BREATH_MAX_SHIFT_STEPS at depth 1
_SLOPE_NORM = {s: max(abs(breath_slope(s, i / 512.0)) for i in range(512))
               for s in BREATH_SHAPES}

def breath_offset_steps(shape, phase, depth):
    if depth <= 0.0:
        return 0.0
    return depth * BREATH_MAX_SHIFT_STEPS * breath_slope(shape, phase) / _SLOPE_NORM[shape]
```

`V(1) == V(0)` for every shape, so the mean slope over a cycle is exactly zero:
**no cumulative drift, ever.** `|offset| <= depth * 0.5` of a 16th. Nothing here
integrates, random-walks or touches `BeatClock`.

### 3.3 `BreathClock`

One instance owned by the bridge, shared **by reference** with every scheduler:

```python
BreathClock(settings=None, *, bpm=120.0, meter_num=4, meter_den=4,
            now=time.monotonic)
  .settings                       # BreathSettings
  .set_settings(settings)         # a phase change rotates on purpose
  .set_tempo(bpm, meter_num, meter_den)  # re-anchor, no jump
  .reset(at=None)                 # phase := settings.phase, epoch := at
  .phase_at(at=None)              # (anchor + (at-epoch)/period) % 1
  .offset_seconds(voice, at=None)
  .level(voice, at=None)          # 0..1 (0.5 when disabled)
  .velocity_delta(voice, at=None) # int
```

* `period = period_bars * rhythm.bar_seconds(bpm, num, den)`.
* `set_tempo` preserves the phase (`new_phase = phase_at(now)`); a tempo change
  never jumps the breath.
* A transport **start** calls `reset()`, so every take begins at the bottom of
  the inhale. A stop does **not** freeze the clock (a free-running arp keeps
  breathing); the next start resets it anyway.
* Voices: `drums`, `bass`, `arp`, `lead`, `drone`. The first four use
  `depth`/`φ`; **the arp uses `min(1, depth + arp_local)`** with one of two
  phases: `arp_sync = False` (the default) uses `φ + 0.5` so it breathes
  against the groove; `arp_sync = True` (8.7.1) uses the master `φ` so the arp
  breathes **in sync** with everything else. `arp_local` still adds depth in
  both modes, so `arp_local 0` + sync is exactly the groove's breath.
* `offset_seconds = breath_offset_steps(...) * (60 / bpm / 4)` (one 16th in
  seconds), exactly 0 when disabled or depth 0.
* `level = breath_volume(...)` when enabled, else a flat `0.5` (depth is the
  timing amount, intensity the velocity swell — breath on with depth 0 is a pure
  velocity breath).
* `velocity_delta = round(intensity * 14 * (2*level - 1))`, `0` when disabled or
  at `level = 0.5`.

---

## 4. Scheduler recipe (the swing precedent)

Each voice already has a drift-corrected `deadline`. Add the breath exactly the
way swing is added:

```python
# --- drums / bass / lead: sleep before playing -----------------------
swing   = self.engine.swing_offset(step, step_length)      # existing
offset  = swing + self._breath_offset(step_length)         # NEW (0.0 off)
target  = deadline + offset
now     = self._now()
if target > now:
    if self._stop.wait(target - now):
        break
notes...
deadline += step_length                                     # nominal: unchanged

# --- arp: the offset goes INSIDE _clamp_wait -------------------------
wait = _clamp_wait(deadline + self._breath_offset(step),
                   realign_at, evolve_at) - now
# a beat snap or an evolution still wins
```

`_breath_offset` returns `0.0` when `self._breath is None`, otherwise:

```python
value = self._breath.offset_seconds("drums", self._now())
limit = 0.45 * step_seconds
return max(-limit, min(limit, value))
```

Velocity: the voice's base velocity plus `breath.velocity_delta(voice)`,
clamped 1..127. Never a delta when the breath is off. The sub bass note keeps
its own base plus the same delta. The arp adds the delta to
`settings.velocity`; the lead adds it to the projected note velocity (which
already includes the per-note accent).

Per voice:

| Voice | Timing | Velocity | Local? |
|---|---|---|---|
| Drums (`DrumScheduler`) | offset added to the sleep target; gate limit also accounts for it | `output.velocity + delta("drums")` | global depth |
| Bass (`BassScheduler`) | after swing, same sleep target | `velocity_for(downbeat) + delta("bass")`, and the sub base + delta | global depth |
| Arp (`SetArp`) | folded into `_clamp_wait(deadline + offset, …)` | `settings.velocity + delta("arp")` | `min(1, depth + arp_local)` at `φ + 0.5`, or at `φ` with **Arp sync** on |
| Lead (`LeadScheduler`) | sleep target, same pattern | `note.velocity + delta("lead")` | global depth |
| Drone (pulse/bend/sequence) | **none** — cycle timing stays quarter-note based | `settings.velocity + delta("drone")` | global depth |

Wiring: each rig gets a one-line `set_breath(breath)` forwarding to its
scheduler (`DrumRig`, `BassRig`, `SetArp`, `LeadRig`, `DroneRig`).
`MIDIRig.set_breath` forwards to every rig (guarded). The bridge owns
`self.breath = BreathClock(...)`, calls `set_breath` in `_sync_drums_context`
(idempotent, also covers a rig created later), `set_tempo` on every
tempo/meter change and `reset()` on a transport start. It sends
`/ji/breath/state` at start, on hello and after every change, and logs one line:

```text
[BREATH] on depth=0.35 period=4 bars shape=inhale intensity=0.50 arp_local=0.00 arp_sync=off
```

**No breath** in `quantize.py`, `rhythm.py`, the mutation engine, the state
loader or the `/ji/rhythm/*` contract.

---

## 5. Loop-FX breath (sclang, local, independent)

Two new **loop edit** parameters per loop. They are **not** driven by the bridge
clock: each loop voice has its own LFO, so a loop breathes even with the master
breath off.

| Param | Wire name | Range | Default | Meaning |
|---|---|---|---|---|
| Breath | `grain_breath` | 0..1 | 0.0 | modulation depth |
| B.Rate | `grain_breath_rate` | 0.02..1 Hz | 0.08 | modulation speed |

In `synthdefs/ji_loop_voice.scd`, all vars are declared at the top of the
function (a late `var` silently loses the whole SynthDef):

```supercollider
var breathLfo = SinOsc.kr(grainBreathRate.clip(0.02, 1.0), 0);   // -1..1
var breathe = grainBreath.clip(0, 1) * breathLfo;                // -1..1
...
var freeTrig = Impulse.kr((grainRate * (1 + (0.6 * breathe))).clip(0.1, 80));
var burstDensity = (grainRate * (1 + burst) * (1 + (0.6 * breathe))).clip(0.1, 80);
var grainDur = (jitteredSize * (1 + (0.35 * breathe))).clip(0.005, 0.5);
```

`grainBreath = 0` makes every factor exactly `1` (bit-identical). The loop tempo
clock, the beat-grain scheduler, `~ji_tempo_clock` and every loop *timing*
decision are untouched: only the grain FX breathe.

sclang: `~ji_loop_edit` gains `grainBreath`/`grainBreathRate`; `~ji_loop_set_edit`
clamps them (0..1, 0.02..1); `~ji_loop_edit_args` passes `\grainBreath` and
`\grainBreathRate`; `/ji/loop/edit` carries **31 values** (appended after
`grain_filter_rate`); `~ji_ui_control_surface_version = "8.7.0"`.

Python mirrors: `LOOP_EDIT_PARAMS` 27 → **29**, `LoopEdit` 29 → **31** fields,
`parse_loop_edit` strict **31**, `/ji/loop/edit_set` accepts the two new names.

---

## 6. Bass hit row

The existing `BassSettings.grid` is the *subdivision* (1/4, 1/8, 1/16). The new
row is **`hits`** — never overloaded.

### 6.1 Settings and engine

```python
BASS_HITS_MAX = 32

@dataclass(frozen=True, slots=True)
class BassSettings:
    ...
    hits_enabled: bool = False
    hits: tuple[bool, ...] = ()
```

* `create()` validates `hits_enabled` and coerces any sequence to
  `tuple(bool(...))`, truncating beyond 32. A string is rejected.
* `bar_steps(settings, meter_num, meter_den) = clamp(round(bar_quarters *
  steps_per_beat), 1, 32)`: 4/8/16 in 4/4 and 3/6/12 in 3/4 for 1/4/1/8/1/16.
* `effective_hits()` pads/truncates the stored row to `bar_steps`. An **empty**
  stored row (`()`, never set) behaves as step 0 on, so a fresh loop still
  plays; a row the user cleared is all `False` (deliberate silence).
* While `hits_enabled`, `BassEngine.pattern(bar_index)` returns exactly the hit
  row with `length_steps = bar_steps` (the row *is* one bar, so `Len` is not
  used — the GUI tooltip and this document say so), and the scheduler cycles by
  `bar_steps`. Pitch picker, accent, gate, `note_at`, `velocity_for` and the
  downbeat test are untouched.
* Dedicated methods (each validates, bumps `version`, clears the pattern cache):
  - `set_hits_enabled(on)` — turning it on with a still-empty row first copies
    the generated pattern, so enabling starts from what the user is hearing.
  - `set_hit(index, on)` — index 0..31; pads the row to the bar length.
  - `clear_hits()` — all off, row length = bar (deliberate silence).
  - `copy_hits(bar_index=0)` — seed from `build_pattern()` **directly**, never
    the cached `pattern()` (which returns the hit row itself when enabled).
  - `hits_payload()` → `[enabled, count, hit_0 … hit_{count-1}]` using the
    **effective** row, so the GUI always draws exactly `bar_steps` buttons after
    a meter/subdivision change.
* A preset, meter or `grid` change **keeps** the row and re-shapes it to the new
  bar. The row is **not** edited through `with_change` (that stays the
  beat-queued settings path); `create()` still accepts both keys so a recalled
  state applies them wholesale.

### 6.2 OSC

| Address | Args | Direction |
|---|---|---|
| `/ji/bass/set_hits_enabled` | [0/1] | UI → bridge |
| `/ji/bass/set_hit` | [int index 0..31, 0/1] | UI → bridge |
| `/ji/bass/clear_hits` | — | UI → bridge |
| `/ji/bass/copy_hits` | — | UI → bridge |
| `/ji/bass/hits` | [int enabled, int count, int hit_0 …] | bridge → UI |

Handlers validate arity/type, never raise and **apply immediately** (a hit edit
is a performance gesture, like a drum-grid step) — never queued on a `PHASE_*`.
`/ji/bass/state` stays at its **20 values** (unchanged). `/ji/bass/hits` is sent
at bridge start, on `/ji/ui/hello`, after every hit change, and whenever the
meter or `grid` changes the row length. Each change logs one line, e.g.
`[BASS] hits enabled row=1/16 steps=16`.

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
| `/ji/breath/set_arp_sync` | [0/1] | UI → bridge |
| `/ji/breath/state` | [enabled, depth, period_bars, shape, intensity, phase, arp_local, arp_sync] | bridge → UI |

Immediate, not beat-queued. Validate strictly, clamp, log one line, echo the
state.

---

## 7. GUI

### 7.1 Rhythm group: one Breath row

```text
Breath [x]  Depth [35 %]  Period [4 b]  Shape [inhale]  Intensity [50 %]  Phase [0 %]  Arp local [0 %]  Arp sync [ ]
```

Values are sent immediately on change (percent spins divide by 100; period is an
int). `/ji/breath/state` syncs the widgets signal-blocked (never echo). The small
hint reads `breath off`, `breathing · N bars`.

### 7.2 Bass group: the hit row

On the bass status row: `Hits` checkbox, `Copy`, `Clear`. Under it a **hidden**
one-row `QScrollArea` (max height ~34 px, like the drum grid) holding exactly
`bar_steps` checkable `QPushButton`s. The row is built lazily and rebuilt when
`/ji/bass/hits` reports a new count. Clicking a step sends `set_hit(index, on)`;
the echo re-syncs the button signal-blocked. `Copy`/`Clear` send their addresses.
The whole row is enabled only while the bass port is available (same rule as the
other bass controls), so the owner can prepare a row while the bass is merely
switched off. The hidden row does not change the default window height.

### 7.3 Loops: `Breath` and `B.Rate`

Added next to `Filter`/`F.Rate` in the Morph row. The old morph hint label was
dropped (its information is in the status line) so the dense row stays within
~1200 px. Grain off disables the loop-breath controls like the other grain
controls.

### 7.4 Window

Default `1280×966` (the Qt test bound is ≤ 990). The scroll area remains the
small-screen safety net.

---

## 8. Performance state

`hits_enabled` and `hits` are optional bass keys with defaults, so a state file
written by 8.6.2 or earlier still loads (`hits_enabled=False, hits=()`), and the
format `version` stays **1**. The hit row **is** saved, because it is part of the
groove. The **breath is not saved**: it is a live feel control (a v2 question if
the owner later wants it). No loop state, no autotune, no new keys elsewhere.

---

## 9. Files

```text
ji-live/
├── control/breath.py                  NEW: settings, curves, BreathClock
├── control/drums.py                   MOD: set_breath, offset + velocity
├── control/bass.py                    MOD: hits settings/engine + set_breath
├── control/arp.py                     MOD: set_breath, _clamp_wait offset, velocity
├── control/lead.py                    MOD: set_breath, offset + velocity
├── control/drone.py                   MOD: set_breath (velocity only)
├── control/midi_rig.py                MOD: set_breath + bass hits forwarding
├── control/ji_bridge.py               MOD: breath clock, tempo/transport hooks,
│                                          /ji/breath/state, /ji/bass/hits
├── control/osc_server.py              MOD: 7 breath + 4 hits handlers
├── control/state.py                   UNCHANGED: generic field serialisation
│                                          carries the optional hits keys in v1
├── ui/osc_control.py                  MOD: tables/parsers/builders/store/controller
├── ui/ji_ui.py                        MOD: Breath row, Bass hits row, loop breath
├── synthdefs/ji_loop_voice.scd        MOD: grainBreath/grainBreathRate
├── tests/sc/test_live_rig.scd         MOD: 2 loop keys, 31-value payload
├── tests/sc/test_loop_edit.scd        MOD: loop breath checks + manual steps
├── tests/test_breath.py               NEW
├── tests/test_bass.py                 MOD: hits
├── tests/test_state.py                MOD: hits round-trip
├── tests/test_ui.py                   MOD: pure + Qt
├── docs/README_stage8_7.md            NEW (this file)
├── docs/START_HERE.md                 MOD
├── docs/GUI_PARAMETERS.md             MOD
├── docs/design_doc_10.28.10.50        MOD
└── README.md                          MOD
```

No bridge contract, audio map or loop-ownership change.

---

## 10. Risks

- **Timing correctness is the whole stage.** Invariants: the nominal deadline
  never changes, the offset is added only to the sleep target and is clamped to
  ±45 % of the step, and the curve has zero mean slope. Any of those broken =
  drift or reordered hits. `tests/test_breath.py` runs a 1000-step drift proof.
- **`BeatClock` must not be touched.** Breath inside the clock would move the
  quantizer, the BPM readout and the state queue.
- **MIDI jitter:** the offset (±22 ms at 120 BPM at depth 1) is larger than the
  thread jitter; no smoothing is added (it would destroy the shape).
- **Arp boundaries:** the offset is inside `_clamp_wait`, never after it, or a
  beat snap/evolution could land late.
- **Tempo change during a breath:** the clock re-anchors the phase (no jump). A
  transport start resets to the bottom of the inhale.
- **Bass hits vs the `grid` setting:** `grid` is the subdivision, `hits` is the
  row. Never overload the names.
- **State:** v1 must still load old files; the breath is not saved.
- **SynthDef `var` placement:** a late `var` silently loses the whole voice
  (`ji_start.scd` reports `missing SynthDefs: [ji_loop_voice]`).
- **GUI width/height:** the Rhythm and Morph rows are dense; the morph hint was
  dropped, the window is 1280×966.
- **No audio in the agent environment:** the self-test and the checklist are the
  verification.

---

## 11. Tests

Python (run from `ji-live/`):

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

New coverage in `tests/test_breath.py`: settings, curve range/endpoints, mean
slope ≈ 0, offset bounds and depth-0/off, velocity delta, clock wrap/tempo
continuity/reset/arp half-cycle, the 1000-step drift proof, and a scheduler seam
check for drums/bass/arp/drone (positive delay, negative offset, `None`
unchanged). `tests/test_bass.py` adds the hit-row validation, bar lengths,
padding, set/clear/copy, pattern override, `Len` ignored, preset/meter behavior
and `hits_payload()` arity. `tests/test_state.py` adds the hits round-trip and an
old-file compatibility check (version stays 1). `tests/test_ui.py` adds the
breath/hits builders, strict parsers, store, controller addresses, Qt row
behavior and the loop-breath 29/31 mirrors.

sclang (human-run): `tests/sc/test_loop_edit.scd` gains the loop-breath defaults,
clamps, args and the manual steps:

- loop breath 0 % = unchanged;
- 30–60 % makes the cloud swell and thin;
- B.Rate 0.03 is a long breath, 0.3 a pulse.

**Agent verification:** every pure Python harness passes with the uv-managed
CPython 3.15.2 (`test_breath`, `test_rhythm`, `test_bass`, `test_state`,
`test_arp`, `test_lead`, `test_drone`, `test_groove`, `test_quantize`,
`test_home`, `test_glide`, `test_startup`, `test_mutation` (unmodified),
`test_mutation_controls`, `test_ui --pure`, `test_mpe --pure`). `test_ui --qt`
**skipped**: the Qt wheel needs `libEGL.so.1`, which this container does not
have (the same limitation as the 8.6 runs). sclang, scsynth, mido hardware,
IAC/Ableton and audio were not available, so the sclang edits and the live GUI
are reviewed code plus the human checklist below, not tested.

---

## 12. Human acceptance checklist (the owner is the authority)

1. **No regression:** breath off and `grain_breath 0 %` — the rig sounds exactly
   as 8.6.2 (drums/bass/arp/lead/drone timing, loop FX, no extra latency).
2. **Global breath:** on, depth 35 %, 4 bars, inhale, intensity 50 % — the whole
   groove audibly inhales/exhales; the BPM display never moves and bar lines stay
   put. Depth 100 % is dramatic but never reorders hits; period 1 bar is a fast
   pulse, 16 bars a long swell.
3. **Shape:** sine vs triangle vs inhale vs exhale are clearly different
   characters.
4. **Intensity:** 0 % = timing only; 100 % = strong velocity/expression swell.
5. **Arp local / Arp sync:** with global depth 0 and arp local 60 %, the arp
   breathes by itself (half a cycle offset); the drums stay steady. Raise global
   depth and the arp breathes against them. Tick **Arp sync** (keep `Arp local`
   at 0) and the arp inhales/exhales **with** the drums and bass; raise `Arp
   local` with sync on and it follows the same phase but deeper.
6. **Loop FX breath:** a granular loop's density/size swells with `Breath`; 0 %
   is unchanged.
7. **Bass hits:** enabling `Hits` seeds the row from the current pattern; clicks
   add/remove hits and the bass plays exactly them (pitch/accent/gate from the
   current mode); Clear silences the hits; Copy re-seeds from the generated
   pattern; the row re-shapes when the Grid combo or the meter changes; state
   save/recall keeps the row.
8. **Quantized actions** (mutate, go, state load, groove edits) still land on the
   beat grid.
9. CPU/MIDI timing stay usable with all voices.
10. Record the result. **8.7 is not complete until the owner confirms the feel.**

---

## 13. Out of scope (deliberate)

Real tempo automation/curves (the BPM is still a constant); polyrhythmic or
per-voice independent breath periods; per-voice breath depth for
drums/bass/lead (the arp is the only local voice); breath on the
mutation/quantize queue; breath on sclang loop/record timing; MPE velocity
curves; per-step velocity or accent in the bass hit row; hit rows longer than
one bar or several bass rows; persisting breath settings in `config/` or in a
performance state.

---

## 14. Changelog

| Ver | Date | Change |
|---|---|---|
| 8.7.0 | 2026-11-03 | Breathing rhythm engine + bass hit row, as described above. Built, awaiting the owner's listening check. |
| 8.7.1 | 2026-11-04 | **Arp sync option** (owner follow-up): `BreathSettings.arp_sync` (default `False` = the existing half-cycle local breath). When on, the arp uses the master phase `φ` instead of `φ + 0.5`; `arp_local` still adds depth. `/ji/breath/set_arp_sync` and an **Arp sync** checkbox; `/ji/breath/state` grows from 7 to 8 values (7-value payloads still parse). Defaults leave every existing breath performance unchanged. |

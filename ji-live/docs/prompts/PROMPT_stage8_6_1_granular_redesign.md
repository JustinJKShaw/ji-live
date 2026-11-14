# Stage 8.6.1 Agent Prompt — Granular redesign (motion, cloud, JI pitch, smear)

You are implementing **Stage 8.6.1** of the JI live-performance system. Stage 8.6
(loop editing: memory slots, overdub, reverse, varispeed, granular, count-in,
auto-play, per-loop gain) is **built and in the owner's hands**, but the owner
rejected the granular half as not musically useful. This stage redesigns it.

Read §1 before touching anything. The owner is the authority on sound; the agent
environment has **no sclang/scsynth and no audio**, so every DSP claim below is
reviewed code plus the human self-test, not a measurement.

---

## 0. What the owner said (verbatim, 2026-11-03)

> "when i turn the grain feature on there is no sound from the loop. it is coming
> out the usual cannel?"

(That one was a bug: `TGrains` is `(…, dur, pan, amp, interp)` and `centerPos` is
in seconds — already fixed. Do not regress it.)

> "ok its audible now but not very useful, instead of making fun glitchy sounds
> it just kinda makes annoying noises playing grains over and over, and not much
> parameter tweaking makes it anything interesting, also moving some parameters
> makes it jump back to 1/16 div for some reason. the goal is to get cool
> elvolving effects kinda like the Output Portal plugin, moving through the
> buffer. also the pitch is kinda useless since as far as i can tell it doesn't
> really snap to the frequency set we are using. come up with a plan to make the
> granular mode much more musically useful in the context of the larger project
> and better/more interesting sounding"

So: **evolving, moving, musically-in-tune granular textures**, not a stuck
slice. The plan is §4.

---

## 1. Read these files first

```text
docs/README_stage8_6.md                 what 8.6 built, the DSP graph, the rules
docs/START_HERE.md                      the "Loop editing" runtime section
docs/GUI_PARAMETERS.md                  the Loops / Loop edit / Grain tables
docs/design_doc_10.28.10.50             §6.6, §6.21, §9, §13, §14
synthdefs/ji_loop_voice.scd             the voice you are changing
synthdefs/ji_looper_player.scd          the accepted player (reference graph)
tests/sc/test_live_rig.scd              ~ji_loop_edit, ~ji_loop_edit_args, pitch
tests/sc/test_loop_edit.scd             the human self-test you must extend
ui/control_surface.scd                  /ji/loop/edit_set, the status payloads
ui/osc_control.py                       LOOP_EDIT_PARAMS, LoopEdit, parsers
ui/ji_ui.py                             the Loop edit / Grain rows
tests/test_ui.py                        pure + Qt coverage to extend
```

Run Python tests from `ji-live/`.

---

## 2. What must not change

- **The loop always runs through the accepted JI autotune**: Tartini → tracker
  buses → nearest class via `targetFreq` → clamp → `Lag` → `PitchShift` →
  `XFade2(mix = 1.0)`. Varispeed and granular only change what goes *into* that
  chain; never bypass it, never add a tune toggle or `tune_effective`.
- **Normal playback at the defaults stays byte-identical** to the accepted loop:
  `mode = normal`, `rate = 1/1`, `reverse = 0`, `gain = 0.70`, count-in off,
  auto-play off. The new grain parameters only matter while `mode = grain`.
- Loops stay **sclang-owned**: no bridge changes, no new bridge args, no new
  state-file keys, no loop data in a Stage 8.5 performance state.
- Four loops, mono buffers, the four-channel map, `~ji_loop_outbus`, the loop
  tracker buses, the 20 Hz lookup and the glide broadcast.
- Memory slots stay session-only (no files).
- The one-sclang + one-Python start path (`ji_start.scd` + `run/ji_station.py`).
- The compact 8 pt GUI with Status visible. This stage adds **one** compact row
  (the window grows to about 1280×910; document it and update the Qt bound).
- `MutationEngine` and every other voice: untouched. `tests/test_mutation.py`
  passes unmodified.

---

## 3. Scope

### 3.1 In scope

1. **Motion engine** — the grain read position travels through the buffer.
2. **Cloud defaults and per-grain jitter** — density/size/position that sound
   like a texture, not a stutter.
3. **JI pitch snapping** — `Pitch` snaps to the current frequency set, is
   re-snapped on every set change, and the effective ratio is reported.
4. **Smear** — a short diffusion after the autotune, default 0 (no change).
5. **Freeze** — hold the motion.
6. **Beat-mode burst** — a density envelope per division so a beat-synced grain
   is a small cloud, not one tick.
7. Fix the **Div combo jumping back to 1/16** bug (§8).
8. Status/OSC/GUI/docs/tests for all of it.

### 3.2 Out of scope (say it in the stage README)

Per-loop autotune knobs, granular over the *live* inputs, stereo loops, per-loop
routing, multi-grain spectral processing (FFT), a second granular voice per loop,
grain pattern sequencer, persistence, loop recall inside performance states.

---

## 4. DSP design — `synthdefs/ji_loop_voice.scd`

Keep the existing argument names and defaults; **add** the new ones. Keep the
normal path exactly as it is (`Phasor` + `BufRd` + `LeakDC`), the mode crossfade,
the tracker `Out.kr` writes and the autotune tail.

### 4.1 New arguments

| Arg | Default | Range (clamped in sclang) | Meaning |
|---|---|---|---|
| `grainMotion` | 1 | 0..4 | 0 off, 1 forward, 2 backward, 3 pingpong, 4 drift |
| `grainSpeed` | 0.03 | 0.001..0.5 | buffer lengths per second |
| `grainDepth` | 0.6 | 0..1 | scan depth around `grainPos` |
| `grainSizeJitter` | 0.2 | 0..1 | per-grain size variation |
| `grainPitchJitter` | 0.0 | 0..1 | per-grain pitch scatter |
| `grainPitchSnap` | 1 | 0/1 | informational only — snapping happens in sclang |
| `grainSmear` | 0.0 | 0..1 | post-autotune diffusion |
| `grainFreeze` | 0 | 0/1 | hold the motion |
| `grainBurst` | 0.6 | 0..1 | beat mode: extra density right after a trigger |
| `grainDecay` | 0.15 | 0.01..1 | beat mode: burst decay in seconds (sclang computes it) |

`grainRate` range widens to **0.5..80**; `grainSize` stays 5..500 ms;
`grainPos`/`grainScatter` stay 0..1.

### 4.2 Motion

Compute a 0..1 phase in the SynthDef (control rate), then place the base read
position:

```supercollider
var bufSeconds = frames / srate;
var stepRate = grainSpeed.clip(0.001, 0.5) / bufSeconds * ControlDur.ir;
var frozen = grainFreeze.clip(0, 1);
var motionPhase = Select.kr(grainMotion.clip(0, 4), [
    0.5,                                                    // off: centre
    Phasor.kr(0, stepRate * (1 - frozen), 0, 1),            // forward
    Phasor.kr(1, stepRate.neg * (1 - frozen), 0, 1),        // backward
    Phasor.kr(0, stepRate * 2 * (1 - frozen), 0, 2).fold(0, 1),  // pingpong
    LFNoise1.kr(grainSpeed.clip(0.001, 0.5) * (1 - frozen)).range(0, 1)  // drift
]);
var posFraction = (grainPos + (grainDepth * (motionPhase - 0.5))).wrap(0, 1);
var basePos = posFraction * bufSeconds;
var scattered = basePos
    + (grainScatter * bufSeconds * TRand.kr(-1, 1, trig));
```

Notes:

* `Phasor.kr` applies `sc_wrap` every sample (verified), so a negative rate and
  the backward mode wrap correctly; `fold(0,1)` turns a 0..2 ramp into a
  triangle for pingpong.
* `ControlDur.ir` is one control block in seconds (`blockSize / sampleRate`), so
  `stepRate` is the per-control-block phase increment. Do **not** use
  `SampleDur` (that would be 64× too slow with `Phasor.kr`).
* Freeze must not jump when it releases: `(1 - frozen)` scales the increment, so
  the phase stops where it is and continues from there.
* Off must be neutral: `motionPhase = 0.5` and `grainDepth` then has no effect.

### 4.3 Grain placement and jitter

Keep the existing safety clamp (the whole grain inside the buffer), now in
seconds:

```supercollider
var size = grainSize * (1 + (grainSizeJitter * TRand.kr(-0.5, 0.5, trig)));
size = size.clip(0.005, 0.5);
var halfSpan = size * grainRatio * 0.5;
var centre = scattered.clip(halfSpan, bufSeconds - halfSpan);
var shortBuffer = bufSeconds < (2 * halfSpan);
var centrePos = Select.kr(shortBuffer, [centre, bufSeconds * 0.5]);
```

`grainRatio` is `grainPitch` (the **effective, already-snapped** ratio) times a
per-grain jitter:

```supercollider
var pitchJitter = 1 + (grainPitchJitter * TRand.kr(-0.5, 0.5, trig));
var growRate = grainPitch.abs.clip(0.25, 4.0) * pitchJitter
    * Select.kr(TRand.kr(0, 1, trig) < grainReverse.clip(0, 1), [1, -1]);
```

**The `TGrains` call stays exactly as fixed in 8.6**:

```supercollider
TGrains.ar(1, trig, bufnum, growRate, centrePos, size,
    pan: 0.0, amp: 1.0, interp: 4)
```

(pan before amp, names used, `centerPos` in seconds.) Do not "simplify" it back
to positional args.

### 4.4 Triggers and the beat burst

```supercollider
var freeTrig = Impulse.kr(grainRate.clip(0.1, 80));
var beatTrig = Changed.kr(grainTrig);
var burstEnv = EnvGen.kr(Env.perc(0.001, 1.0), beatTrig,
    timeScale: grainDecay.clip(0.01, 1));
var burst = grainBurst * burstEnv;
var burstDensity = (grainRate * (1 + burst)).clip(0.1, 80);
var beatClock = Impulse.kr(burstDensity);
var trig = Select.kr(grainSync.clip(0, 1), [freeTrig, beatClock]);
```

**`Env.perc` times are client-side constants** — never put a Synth argument
inside an `Env` (that is a SynthDef compile error). The envelope is
`Env.perc(0.001, 1.0)` scaled by `timeScale: grainDecay`, which *is* a control
input. One `grainTrig` OSC set still produces exactly one burst; never send per
audio block. `grainDecay` is set by sclang to about half the current division in
seconds whenever the division or tempo changes.

### 4.5 Smear (post-autotune)

After the autotune output, before `* gain * env`:

```supercollider
var smearWet = AllpassC.ar(shifted, 0.05, 0.007, 0.04);
smearWet = AllpassC.ar(smearWet, 0.05, 0.011, 0.05);
out = XFade2.ar(out, smearWet, (grainSmear.clip(0, 1) * 2) - 1);
```

`grainSmear = 0` must give bit-identical output to the accepted chain (XFade2 at
pan -1). Document that it is post-autotune (the loop still goes *through* the
autotune; this is an effect *after* it) and that it is subtle at low values.

### 4.6 Grain mode retune floor

In grain mode the analysed signal is a jittery cloud; a hard autotune lag
warbles. Use

```supercollider
// near the top of the SynthDef, next to the autotune vars
var retune = Select.kr(mode.clip(0, 1) > 0.5, [retuneTime, retuneTime.max(0.15)]);
// ... later, replacing Lag.kr(clampedRatio, retuneTime):
smoothRatio = Lag.kr(clampedRatio, retune);
```

Normal mode is unchanged.

### 4.7 CPU

Four voices, each with Tartini + PitchShift + TGrains + two allpasses. Keep it
linear: one `TGrains` per loop (never per grain voice), no LocalBuf, no FFT. Note
in the docs that four simultaneous grain voices are still the ceiling.

---

## 5. sclang — `tests/sc/test_live_rig.scd`

### 5.1 New edit-state keys (keep the existing ones)

```supercollider
grainMotion: \forward, grainSpeed: 0.03, grainDepth: 0.6,
grainSizeJitter: 0.2, grainPitchJitter: 0.0, grainPitchSnap: 1,
grainSmear: 0.0, grainFreeze: 0, grainBurst: 0.6,
```

New defaults for the existing keys (they only matter in grain mode):
`grainRate: 20.0`, `grainSize: 0.12`, `grainPos: 0.5`, `grainScatter: 0.1`.
Normal mode is unaffected by all of this.

### 5.2 `~ji_loop_edit_args.(i)` adds

`\grainMotion` (0..4 index), `\grainSpeed`, `\grainDepth`, `\grainSizeJitter`,
`\grainPitchJitter`, `\grainSmear`, `\grainFreeze`, `\grainBurst`,
`\grainDecay`, and passes the **effective** pitch as `\grainPitch`.

### 5.3 JI snapping

Add one pure helper and one refresh hook:

```supercollider
// Nearest member of the current set (x octaves, 0.25..4) to the requested ratio.
~ji_loop_snap_ratio = { |ratio, ratios|
    var target = ratio.asFloat.clip(0.25, 4.0);
    var best = target, bestDist = 1e9;
    ratios.do { |r|
        7.do { |k|          // octaves -3..+3 around 1/1
            var candidate = r * (2 ** (k - 3));
            if (candidate >= 0.25 and: { candidate <= 4.0 }) {
                var dist = (candidate / target).log2.abs;
                if (dist < bestDist) { bestDist = dist; best = candidate };
            };
        };
    };
    best;
};

~ji_loop_effective_pitch = { |edit|
    if (edit[\grainPitchSnap] == 1) {
        ~ji_loop_snap_ratio.(edit[\grainPitch], ~ji_ji_set_ratios);
    } {
        edit[\grainPitch].asFloat.clip(0.25, 4.0);
    };
};
```

* `~ji_loop_set_edit` for `grain_pitch` / `grain_pitch_snap` recomputes the
  effective ratio and `player.set(\grainPitch, effective)`.
* The `/ji/freqset/update` receiver (`~ji_freqset_oscdef`) calls a new
  `~ji_loop_refresh_pitch.(ratios)` after `~ji_broadcast_glide`: it re-snaps and
  re-sets every playing grain loop (only when `mode == \grain` and
  `grainPitchSnap == 1`), one `[LOOP i] grain pitch …` line per changed loop.
* `~ji_loop_play_loop` re-snaps before creating the voice too.

### 5.4 Div/beat decay

`sclang` computes `grainDecay = 0.5 * ~ji_loop_division_subdivision.(division) *
60 / ~ji_rhythm_bpm` and passes it in the args; recompute on tempo/division
change (the existing `~ji_loop_grain_restart` path is the right hook).

### 5.5 `~ji_loop_set_edit` — new params

`grain_motion` (choice: `off`/`forward`/`backward`/`pingpong`/`drift`),
`grain_speed`, `grain_depth`, `grain_size_jitter`, `grain_pitch_jitter`,
`grain_pitch_snap` (0/1), `grain_smear`, `grain_freeze` (0/1), `grain_burst`.
Clamp, update the state, `player.set` the changed argument, send
`/ji/loop/edit`, log one line. Number of edit params: 23.

---

## 6. OSC surface

- `/ji/loop/edit_set` accepts the new param names (same address, longer list).
- `/ji/loop/edit` grows from 15 to **25 values**:

```text
index (1..4), reverse (0/1), rate (float), gain (float),
mode (0/1), sync (0/1), division ("1/16"|"1/8"|"1/4"|"1/2"|"1"),
grain_rate (float), grain_size_ms (float), grain_pos (0..1),
grain_scatter (0..1), grain_pitch (requested float),
grain_size_jitter (0..1), grain_pitch_jitter (0..1),
grain_pitch_snap (0/1), grain_smear (0..1), grain_freeze (0/1),
grain_motion ("off"|"forward"|"backward"|"pingpong"|"drift"),
grain_speed (float), grain_depth (0..1), grain_burst (0..1),
grain_reverse (0..1), count_in ("off"|"1 beat"|"1 bar"|"2 bars"),
auto_play (0/1), effective_pitch (float, the JI-snapped ratio)
```

`grain_pitch` stays the *requested* ratio; `effective_pitch` is what the voice
actually receives (`grain_pitch` when `Snap` is off).

- Keep `/ji/loop/mem` and `/ji/loop/count` unchanged.
- Every handler validates arity/type and never raises out of the OSCdef.

---

## 7. GUI — `ui/ji_ui.py`, `ui/osc_control.py`

- Keep the Loop edit row and the existing Grain row. **Add one** compact row
  under it ("Grain motion"):

```text
Motion [off/forward/backward/pingpong/drift]  Speed [0.001..0.5]  Depth [0..100 %]
Size Jit [0..100 %]  Pitch Jit [0..100 %]  Snap [x]  Smear [0..100 %]  Freeze [x]
```

- The Grain row keeps Grain / Sync / Div / Rate / Size / Pos / Scatter / Pitch /
  Rev% with the new defaults shown.
- `Pitch` stays a ratio spin (0.25–4.0) but the tooltip and status line explain
  the snap: `Loop 2 · ×3/2 · grain/beat · pos 50 % · motion forward · snap · gain 0.70`.
  A small `Snap` checkbox in the new row controls it; when snapped, the status
  line shows the effective ratio (`×3/2`) rather than the raw float.
- Same rules as 8.6: signal-blocked sync (never echo OSC), per-loop cached
  `LoopEdit`, Grain off disables every grain control, hint for beat+stopped.
- Window ~1280×910; update the Qt height bound (≤ 950) and the docs.

Python pure layer: extend `LOOP_EDIT_PARAMS` (name, label, range, default,
kind), `LoopEdit` fields, `parse_loop_edit` (strict 25), builders' choice lists
(`LOOP_GRAIN_MOTIONS`), and keep every value clamped.

---

## 8. Bug: the Div combo jumps back to 1/16

Reproduction: set `Grain = on`, `Sync = beat`, `Div = 1/4`, then move `Size` or
`Pos`. The Div combo returns to `1/16`.

Likely cause: the GUI re-syncs **every** widget from each `/ji/loop/edit` echo
(`_sync_loop_edit_row`), so whichever side holds a stale division wins. Find out
which side is stale:

1. Add a Qt test that sets `Div = 1/4`, then feeds an unrelated
   `/ji/loop/edit` echo with `division = "1/4"` and asserts the combo stays.
2. If that passes, the sclang state is stale: add a self-test line that calls
   `~ji_loop_set_edit.(1, \division, "1/4")` and prints
   `~ji_loop_edit[0][\division]` plus the next `/ji/loop/edit` payload, and
   check the `/ji/loop/edit_set` handler path (string vs symbol, rejected
   values, `msg[3]` type).
3. Fix the root cause. Do **not** paper over it by muting echoes; if the GUI
   needs a guard, keep it narrow (e.g. ignore an echo field that is identical
   to what the widget already shows, and never overwrite a widget whose value
   was sent within the last ~200 ms and not yet confirmed).
4. Add a regression test at whichever layer was wrong.

---

## 9. Docs

- `docs/README_stage8_6.md`: add a §14 "8.6.1 granular redesign" section (what
  changed, why, the new parameter table, the motion diagram, the JI snap rule,
  the smear placement, the Div fix, the new defaults) and update the diagrams,
  the OSC table, the risks and the checklist. Rename the file only if you also
  update every link (prefer keeping the name).
- `docs/START_HERE.md`: extend the loop-editing section with Motion/Speed/Depth,
  Size/Pitch jitter, Snap, Smear and Freeze; add troubleshooting rows:
  * grains still sound static → `Motion` must not be `off`, `Depth` > 0;
  * the cloud is silent → `Pos 0 %` on a silent head, move `Pos`/`Scatter`;
  * the pitch control seems to do nothing → `Snap` is on and the nearest set
    ratio is the same octave/ratio, look at the effective ratio in the status;
  * warbling in grain mode → expected with heavy jitter; lower `Pitch Jit`;
  * the Div combo jumps → stale control surface/GUI, re-evaluate.
- `docs/GUI_PARAMETERS.md`: every new control with range/default.
- `docs/design_doc_10.28.10.50`: §6.6 (granular paragraph), §6.21 (surface),
  §7.1 (OSC values: `/ji/loop/edit` is now 25), §8.6.1 row in §8.9, §9 status,
  §13 (deferred: FFT granulation, live-input grain, per-loop autotune), §14
  changelog row `8.6.1`.
- Root `README.md`: status paragraph and the docs links.

---

## 10. Tests

### 10.1 Python (must pass, from `ji-live/`)

```bash
uv run --with python-osc python tests/test_ui.py --pure
python tests/test_mpe.py --pure
```

plus every harness listed in §2 of the 8.6 prompt / root README (all pass
unmodified except `test_ui.py`).

Extend `tests/test_ui.py`:

- `LOOP_GRAIN_MOTIONS` and the new `LOOP_EDIT_PARAMS` entries (names, ranges,
  defaults, kinds).
- Builder clamps for every new param; unknown motion raises; `pitch_snap` and
  `freeze` are 0/1.
- `LoopEdit` defaults (motion `forward`, speed 0.03, depth 0.6, size jitter 0.2,
  pitch jitter 0, snap on, smear 0, freeze off, rate 20, size 120 ms, pos 0.5,
  scatter 0.1) and `status_line()` containing `motion forward` and the snapped
  ratio when `snap` is on.
- Strict 25-value parser: wrong arity rejected, every value clamped, motion
  string validated, effective pitch parsed as float.
- Store application of the new `/ji/loop/edit`.
- Controller sends the new params to **sclang** only.
- Qt: the Grain motion row exists; controls send for the selected loop; Grain
  off disables the new row too; incoming edit syncs without echo; **Div stays
  1/4 after an unrelated echo** (the §8 regression); window height bound.

### 10.2 sclang self-test — `tests/sc/test_loop_edit.scd` (human-run)

Extend with PASS/FAIL checks for:

- the new defaults and every clamp/reject (motion names, speed, depth, jitter,
  snap, smear, freeze, burst);
- `~ji_loop_snap_ratio` maths: `1.5 → 3/2`, `0.66 → 2/3`, `2.0 → 2/1`,
  `1.98 → 2/1`, `0.5 → 1/2`, and a set-specific case (e.g. with
  `[1, 9/8, 5/4, 4/3, 3/2, 5/3, 15/8]`, `1.19 → 9/8`, `1.26 → 5/4`);
- the effective pitch changes when `~ji_ji_set_ratios` changes and
  `grainPitchSnap = 1`;
- a `\ji_loop_voice` created in grain mode carries `mix = 1.0` and the new args;
- the count-in/memory checks from 8.6 still pass.
- Print the manual audio steps (below) and one final `[LOOP EDIT TEST] PASS/FAIL`.

---

## 11. Human acceptance checklist (the owner is the authority)

1. **No regression**: normal play (rate 1/1, forward) sounds exactly as before;
   the `Smear` control at 0 changes nothing.
2. **Motion**: Grain on, `Motion = forward`, `Speed ≈ 0.03`, `Depth 60 %` — the
   cloud slowly travels through the loop and evolves; switching to backward,
   pingpong and drift gives clearly different motion; `Freeze` holds it.
3. **Cloud**: with `Rate 20`, `Size 120 ms`, `Scatter 10 %` the texture is
   smooth/continuous, not a stutter; `Rate` 5 vs 60 is a clear density change;
   `Size Jit` thickens it.
4. **Pitch**: `Snap` on, `Pitch 1.5` → the loop moves up a fifth **in the
   current JI set** (the status line shows the effective ratio); mutate the set
   and the playing granular loop re-snaps without a click; `Snap` off restores
   the raw ratio; `Pitch Jit` makes the cloud shimmer.
5. **Smear**: raising `Smear` adds blur/space without destroying the JI
   tuning; 0 is dry.
6. **Beat**: `Sync = beat`, transport running; grains lock to `Div`; each
   division is a small cloud (burst) rather than one tick; changing `Div` or
   tempo keeps it locked; stopping the transport stops the loop (as in 8.6).
7. **Div bug**: change `Div`, then move `Size`/`Pos`/`Smear`; `Div` stays.
8. CPU stays usable with four loops (one grain loop at a time is the realistic
   case; note the numbers).
9. Memory slots, dub, reverse, varispeed, count-in, auto-play, per-loop gain and
   the 8.5 states still behave as documented in 8.6.
10. Record the result. **8.6.1 is not complete until the owner confirms the
    grain sound and no regressions.**

---

## 12. Risks

- **No audio in the agent environment.** The DSP is reviewed code; the
  self-test and the checklist are the verification. Do not claim acceptance.
- **Tartini on clouds.** A jittery cloud may track badly; the 0.15 s retune floor
  is the mitigation. If it still warbles, lower `Pitch Jit` (documented).
- **Phase/freeze maths.** `Phasor.kr` with `ControlDur.ir` is the correct unit;
  `fold(0,1)` for pingpong; freeze scales the increment (no jump). Verify in the
  self-test by printing the args, not by ear alone.
- **Smear placement.** Post-autotune only; default 0; XFade2 at -1 must be
  bit-identical.
- **JI snap.** Snapping the *ratio* (not the note) is the design: the material is
  already tuned by the autotune, so a set ratio is a consonant interval. Document
  that the effective ratio is what the voice gets.
- **CPU.** One `TGrains` + two allpasses per loop; four grain voices is the
  ceiling.
- **GUI density.** One more row; window ~1280×910; scroll area stays the net.
- **Docs drift.** This stage's docs are part of acceptance.

---

## 13. Deliverables

```text
ji-live/
├── synthdefs/ji_loop_voice.scd      MOD: motion, jitter, smear, burst, retune floor
├── tests/sc/test_live_rig.scd       MOD: new state keys, snap, refresh hook, args
├── tests/sc/test_loop_edit.scd      MOD: new checks + manual steps
├── ui/control_surface.scd           MOD: new params, 25-value /ji/loop/edit
├── ui/osc_control.py                MOD: tables/dataclass/parser/builders
├── ui/ji_ui.py                      MOD: Grain motion row, snap status, Div fix
├── tests/test_ui.py                 MOD (pure + Qt)
├── docs/README_stage8_6.md          MOD: 8.6.1 section
├── docs/START_HERE.md               MOD: controls + troubleshooting
├── docs/GUI_PARAMETERS.md           MOD: new controls
├── docs/design_doc_10.28.10.50      MOD: §6.6/§6.21/§7.1/§8.9/§9/§13/§14
└── README.md                        MOD: status + links
```

No changes to `control/` (the bridge) or `config/`. Loops remain sclang-owned.

---

## 14. Self-review requirement (final response)

Provide:

- Files added/changed.
- For each requirement above, the code that satisfies it or a concrete concern.
- "If I had to bet on which line is most likely to fail on the human's machine,
  which is it, and why? Give a one-line verification command."
- Assumptions, max 5 bullets.
- Explicit statement that audio, MIDI, sclang timing and the live GUI were not
  tested by the agent; 8.6.1 awaits human acceptance.
- The exact one-sclang + one-Python start sequence, copied from
  `docs/START_HERE.md` after your updates.

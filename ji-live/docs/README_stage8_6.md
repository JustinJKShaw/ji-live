# Stage 8.6 — Loop editing (memory slots, overdub, reverse, varispeed, granular)

Owner request (2026-11-02), verbatim:

> "loop saving and recall in memory slots (these are per session, they dont have
> to be actually saved beyond that). an overdub feature. then i want a bunch of
> loop manipulation features: i want the loops to be able to be reversed, also
> altered by playing it back at various ratios of its length (standard ones like
> 2, 1/2 but also more strange ones like 2/3, 3/5, whatever, and this should
> pitch around the loop). ideally also some strange glitchy features like
> granular playback (free and beat-synced) that can be tweaked with parameters.
> and any other features already listed in the docs that i missed and sound
> useful."

Follow-up the same day, on the pitch question and two record options:

> "its ok to have the repitched loop going into the autotune, it doesn't have to
> be repitched exactly, the goal is just to have some modification of the loop
> that introduces new material. a pitched up or down loop going into the
> autotune is perfectly fine. also some additional features i forgot to include-
> selectable options to have a countdown before the loop record (ex. click the
> button and then it starts recording a bar later), and an option to have the
> loop start playing immediately after recording."

**Status: 8.6 implemented 2026-11-02; the granular half was rejected on
musical grounds and redesigned in 8.6.1 (2026-11-03, §14); the owner then asked
for a dry→grain morph, a melt toggle and moving filters, added in 8.6.2
(2026-11-03, §15) — awaiting human acceptance.** The agent environment has no sclang, no scsynth and no audio;
every DSP claim below is reviewed code plus the human self-test
`tests/sc/test_loop_edit.scd`, not a measurement. The owner is the authority.

---

## 1. What Stage 8.6 adds

| Feature | Summary |
|---|---|
| **Memory slots A–D** | Per-session copies of a loop's audio **and** edit state. Save / Recall / Clear. No files, no persistence. |
| **Overdub** | `Dub` records over a loop for exactly one pass (`preLevel: 1.0`, `loop: 1.0`) instead of replacing it. `Rec` still replaces. |
| **Reverse** | Per-loop playback direction. The read head keeps its position and the direction flips. |
| **Varispeed** | Per-loop playback at one of 11 fixed length ratios (`2/1` … `1/2`). Tape-style: it also re-pitches the loop. |
| **Granular** | Per-loop grain cloud, free-running **or** beat-synced, with rate / size / position / scatter / pitch / per-grain reverse; 8.6.1 adds motion/speed/depth/freeze, per-grain size and pitch jitter, JI-snapped pitch, smear and a beat-mode burst; 8.6.2 adds the dry↔grain **Morph**, the **Melt** ramp and the moving **melt filter**. |
| **Per-loop gain** | 0–1, default 0.70 (the old hardcoded gain). |
| **Count-in** | A selectable delay before a Rec actually records: `off`, `1 beat`, `1 bar`, `2 bars` (a bar is the current meter's bar). |
| **Auto-play** | Start the loop as soon as a take finishes. |
| **Status/OSC** | `/ji/loop/dub`, `/ji/loop/edit_set`, `/ji/loop/mem_save|recall|clear` up; `/ji/loop/edit`, `/ji/loop/mem`, `/ji/loop/count` down; `dubbing` and `counting` loop states. |

Everything ships **at the accepted defaults**: rate `1/1`, normal mode, forward,
gain 0.70, count-in `off`, auto-play `off`. At those defaults the loop voice is
the accepted `\ji_looper_player` graph (same Tartini → nearest class → mid-ratio
clamp → `Lag` → `PitchShift` → `mix = 1.0` chain), so a recorded loop sounds and
retunes exactly as before.

## 2. The DSP graph

`synthdefs/ji_loop_voice.scd` (new) is one SynthDef with two playback paths:

```text
normal  : BufRd(Phasor, mono, loop) -> LeakDC ----------\
                                                          XFade2(mode, 0.02 s Lag)
grain   : TGrains(mono)             -> LeakDC ----------/        |
            ^ motion phase -> +- depth/scatter -> clamped centre  |
            ^ beat burst envelope / free impulse (grainTrig)      |
                                                                 v
   Tartini -> Out.kr(freqBus) / Out.kr(hasFreqBus)        (always written)
           -> targetFreq / measured -> clamp(+/- pitchRangeLimit)
           -> Lag(retuneTime, >= 0.15 s in grain mode)
           -> PitchShift(windowSize) -> XFade2(mix = 1.0)
           -> XFade2(dry, 2 x AllpassC, grainSmear) -> * gain * env(30 ms) -> outBus
```

* **Normal/varispeed/reverse.** `Phasor.ar(0, increment, 0, frames, 0)` where
  `increment = Lag.kr(rate * (reverse ? -1 : 1), 0.02)` is in *frames per output
  sample* (`rate 1.0` = natural speed). SuperCollider's `Phasor` applies
  `sc_wrap(level, start, end)` on every sample (`Phasor_next_kk` in
  `server/plugins/TriggerUGens.cpp`), so a negative increment wraps from `start`
  back to `end`: a reversed loop runs end → start and keeps running. The phase
  is **not** reset on a direction change; only the sign of the increment changes,
  through the 0.02 s `Lag`, so the head slows, stops and reverses without a
  click. `BufRd(..., loop: 1, interp: 4)` + `LeakDC` complete the path.
* **Granular.** `TGrains` (mono) triggered by `Impulse.kr(grainRate)` when
  `grainSync = 0`, or by `Changed.kr(grainTrig)` when `grainSync = 1`. sclang
  sends an **incrementing integer** `grainTrig` once per subdivision, so one OSC
  `set` produces exactly one grain; the voice is created with `grainTrig = 0`
  and the first scheduled grain sends `1` (a change fires).
  **`TGrains.ar` is `(numChannels, trigger, bufnum, rate, centerPos, dur,
  pan, amp, interp)` — `pan` before `amp` — and `centerPos` is in seconds**
  (the UGen multiplies it by the buffer sample rate). `pan`/`amp` are passed
  by name (`pan: 0, amp: 1`) so the order cannot silently zero the grain
  amplitude; the first build did swap them and the grain mode was completely
  silent. The centre is `grainPos * bufferSeconds + grainScatter * bufferSeconds
  * TRand.kr(-1, 1, trig)`, clamped so the **whole** grain stays inside the
  buffer (`halfSpan = grainSize * grainPitch * 0.5` seconds, centre clamped to
  `[halfSpan, bufferSeconds - halfSpan]`, short buffers play from the centre).
  The grain rate is `grainPitch` or its negative, chosen per grain with
  `TRand.kr(0, 1, trig) < grainReverse` (a per-grain sample-and-hold).
  `Pos = 0 %` puts the cloud at the very start of the loop: if the take begins
  with silence, the cloud is silent too — move `Pos` or add `Scatter`.
* **Mode crossfade.** `XFade2(normal, grain, Lag.kr(mode, 0.02) * 2 - 1)`, so a
  mode switch cannot click.
* **Tracker buses.** `Out.kr(freqBus, ...)` and `Out.kr(hasFreqBus, ...)` are
  written in **both** modes; the 20 Hz sclang lookup keeps working unchanged.
* **No new buses.** The four existing loop tracker pairs are reused.

`synthdefs/ji_looper_recorder.scd` gained two **additive** arguments,
`preLevel = 0.0` and `loop = 0.0`. The defaults reproduce the accepted recorder
exactly; `~ji_record_loop` still passes neither, so a normal `Rec` overwrites
from frame 0 and self-frees when the buffer is full. Only `~ji_dub_loop` passes
`preLevel: 1.0, loop: 1.0`.

`synthdefs/ji_buf_copy.scd` (new) copies `numFrames` mono frames `src → dst` on
the server: `BufRd(src, Line.ar(0, numFrames-1, numFrames/sampleRate,
doneAction: 2)) → BufWr(dst, same phase)`. The memory slots use it, so no
version-specific `Buffer.copyData` and no file I/O.

`synthdefs/ji_looper_player.scd` and the legacy `tests/sc/test_dual_loop.scd`
path are untouched.

## 3. "The autotune stays in the chain" (owner clarification)

`mix` is fixed at **1.0** in the loop voice in every mode. Normal, reverse,
varispeed and granular all feed the same Tartini → nearest-class → `PitchShift`
graph that the accepted loop uses. There is **no** tune toggle, no `mix`
recomputation and no `tune_effective` field anywhere.

Consequences — intended, not bugs:

* A varispeeded loop is re-analysed at its transposed pitch, so the current set
  may pull it partly back toward a class. The audible result is the
  *interaction* of the recorded material, the rate, the grain settings and the
  set. That is the "new material" the owner asked for, not an exact
  transposition.
* A granular cloud's pitch is tracked by Tartini and re-projected note-by-note,
  so it may sound tuned, detuned or both depending on material and set. In grain
  mode the retune `Lag` is floored at 0.15 s (§14), because a jittery cloud
  tracked instantly warbles.
* `rate = 1/1`, normal mode, forward: exactly the accepted loop.

The GUI and the loop status line never show a "tune bypass" state.

## 4. Controls

### 4.1 Loop boxes

| Control | Range (default) | What it does |
|---|---|---|
| **Rec** | button | Records a fresh take (replaces), beat-quantized in beat mode. Honors `Count-in`. |
| **Stop Rec** | button | Ends a take **or a dub** early; the buffer is kept. |
| **Play** | button | Plays through the autotune (beat mode: next beat). |
| **Stop** | button | Stops playback (and a pending count-in); buffer kept. |
| **Dub** | button | Overdub: records **over** the loop for one pass, keeping its contents. On an empty loop it records a fresh take and says so. |
| **Clear** | button | Frees the buffer (keeps the loop's edit state; never touches the memory slots). |
| State label | read-only | `empty` / `recording` / `recorded` / `playing` / `stopped` / `dubbing` / `counting N`. |

### 4.2 Loop edit row (the selected loop)

| Control | Range (default) | What it does |
|---|---|---|
| `Loop` | 1–4 (1) | Which loop the edit and grain rows show and change. |
| `Rev` | 0/1 (off) | Reverse playback. |
| `Rate` | `2/1`…`1/2` (`1/1`) | Varispeed ratio; it also re-pitches. Still through the autotune. |
| `Gain` | 0–1 (0.70) | Per-loop output gain. |
| `Count-in` | off / 1 beat / 1 bar / 2 bars (off) | Delay before a **Rec** records. Rec only, not a dub. |
| `Auto` | 0/1 (off) | Auto-play when a take ends. |
| `Mem` | A–D | Selected memory slot for Save/Recall/Clr. |
| **Save** / **Recall** / **Clr** | buttons | Copy the loop into a slot / restore a slot into the loop (stopped) / free a slot. |
| Status | read-only | `Loop 2 · ×3/2 · rev · count-in 1 bar · auto · gain 0.70` (since 9.0 the label lives on the Harm row; 8.6.2–8.8 had it on the Morph row) |

### 4.3 Grain row (the selected loop, Stage 8.6.1 defaults)

| Control | Range (default) | What it does |
|---|---|---|
| `Grain` | 0/1 (off) | Switches the loop between normal and granular playback (crossfaded). |
| `Sync` | free / beat (free) | free = own clock, runs with the transport stopped. beat = a burst per `Div`, transport-gated. |
| `Div` | `1/16`, `1/8`, `1/4`, `1/2`, `1` (1/16) | Beat-synced grain interval. |
| `Rate` | 0.5–80 grains/s (20) | Free-mode grain rate. With a 120 ms grain this is a continuous cloud. |
| `Size` | 5–500 ms (120) | Grain duration. |
| `Pos` | 0–100 % (50) | Centre of the read position in the buffer. |
| `Scatter` | 0–100 % (10) | Random position scatter. |
| `Pitch` | 0.25–4.0 (1.0) | **Requested** grain ratio; Snap turns it into a set ratio (see §4.5). |
| `Rev%` | 0–100 % (0) | Per-grain reverse probability. |

### 4.4 Grain motion row (the selected loop, Stage 8.6.1)

| Control | Range (default) | What it does |
|---|---|---|
| `Motion` | off / forward / backward / pingpong / drift (forward) | How the grain read position travels through the buffer. off keeps it at `Pos` (the 8.6 behaviour). |
| `Speed` | 0.001–0.5 loop-lengths/s (0.03) | Scan speed: 0.03 crosses an 8 s loop in about 33 s. |
| `Depth` | 0–100 % (60) | How far around `Pos` the scan goes; 0 % pins the position. |
| `Size Jit` | 0–100 % (20) | Per-grain size variation (thickens the cloud). |
| `Pitch Jit` | 0–100 % (0) | Per-grain pitch scatter (shimmer; heavy values warble). |
| `Snap` | on/off (on) | Snap the ratio to the current JI set (§4.5). |
| `Smear` | 0–100 % (0) | Post-autotune diffusion (two short allpasses). 0 % is the dry accepted chain. |
| `Freeze` | on/off (off) | Hold the motion where it is; unfreezing continues from the same position. |

### 4.4c Morph row (the selected loop, Stage 8.6.2)

| Control | Range (default) | What it does |
|---|---|---|
| `Morph` | 0–100 % (0) | Dry loop (0 %) ↔ grain cloud (100 %); in between the two are smeared together by an equal-power crossfade. Needs `Grain` on. |
| `Melt` | on/off (off) | Start at the original and progressively melt into the grains: Play (or ticking Melt) ramps Morph from 0 to the slider value over ~4 s; unticking melts back. |
| `Filter` | 0–100 % (0) | Moving melt filter (post-autotune): a resonant lowpass and a low cut on two slow LFOs fuse the dry and grain layers. 0 % is bypass; works with Grain off too. |
| `F.Rate` | 0.01–1 Hz (0.12) | Movement speed of the filter (about 8 s per sweep at 0.12). |

Morph and Melt are disabled while `Grain` is off; Filter / F.Rate stay usable.

The grain and motion controls are disabled while `Grain` is off. With
`Sync = beat` and the transport stopped the row shows
`beat grains need the transport (press Play)`; while frozen it shows
`motion frozen`.

### 4.4b Rules that matter

* **All loop edit changes are immediate** — reverse, rate, gain, mode, sync and
  every grain parameter. They are real-time performance gestures and are *not*
  beat-quantized. Record/Play keep their free-immediate / beat-next-beat rule.
* **Count-in** counts on the tempo clock in quarter beats, starting at the next
  beat (`target = now.ceil + beats`). `1 bar` = `meter_num * 4 / meter_den`
  quarter beats **rounded up** (4/4 → 4, 3/4 → 3, 6/8 → 3, 5/8 → 3, 7/8 → 4;
  the 9.0 repair). The take length is recomputed at
  fire time, so a beat-mode take still uses `beats * 60 / bpm` at the moment it
  starts. No recorder runs before the count-in target (no silent pre-roll).
* **Count-in cancellation.** A second Rec during `counting` cancels it and
  records immediately (`count-in skipped (second Rec)`). Stop Rec, Stop, Clear,
  Stop All and a new Dub cancel it and restore the previous state without
  auto-play. In **beat mode** a transport stop also cancels it; in **free mode**
  the count-in continues on the clock because free loops are not
  transport-gated.
* **Auto-play** is a post-take action, not a mode. It fires at the take's exact
  end (buffer full) or when `Stop Rec` ends it early, and only if a valid buffer
  exists. In beat mode it needs a running transport; otherwise it logs
  `auto-play skipped: transport stopped (beat mode)` and leaves the loop
  `recorded`. It is ignored when the take ends with the loop already playing (a
  dub that keeps playing does not restart). The recorder Synth is the identity
  token, so a replaced take can never fire a stale auto-play.

## 5. Memory slots (session only)

* Four slots `A`–`D` (0–3). Each holds a **copy** of the loop buffer plus the
  sound edit state (`reverse`, `rate`, `gain`, `mode`, `sync`, `division`,
  `grain_*`) and the buffer duration. It never references the live loop buffer.
* `count_in` / `auto_play` are **record preferences, not sound**: they are not
  stored in a slot and are left as they are on recall.
* **Save** refuses while the loop is `recording`, `dubbing` or `counting`, or
  when there is no buffer. It allocates a new buffer of the same frame count,
  starts `\ji_buf_copy`, and when the copy finishes frees the previous slot
  buffer, stores the copy and sends `/ji/loop/mem`. One line:
  `[LOOP 2] mem A saved (8.0 s)`.
* **Recall** requires a filled slot (an empty one logs `mem A is empty`). It
  stops the recorder/dub and the player (30 ms fade), waits, allocates a new
  loop buffer of the slot's frame count, copies slot → loop, frees the loop's
  previous buffer, installs the new one, restores the slot's edit state and
  sends `/ji/loop/edit`, `/ji/loop/state` (`stopped`) and `/ji/loop/config`. The
  recalled loop stays **stopped**.
* **Clear** frees the slot buffer and nils the slot (a logged no-op when
  already empty). Clearing a *loop* still frees only its buffer and keeps that
  loop's edit state; it never touches a memory slot.
* **Session only.** Re-evaluating `tests/sc/test_live_rig.scd` (or
  `ji_start.scd`) frees the slot buffers; nothing is written to disk and there
  is no `loops/` folder. `~ji_stop_all.()` stops audio, recorders, players,
  grain schedulers and count-ins but keeps the buffers and the memory slots.

Memory: four slots × up to 30 s mono ≈ 4 × 5.8 MB ≈ 23 MB at the maximum
loop length; the shipped 8 s loops use ≈ 6 MB.

## 6. OSC surface (loops stay sclang-owned)

| Address | Args | Direction | Meaning |
|---|---|---|---|
| `/ji/loop/dub` | int index 1..4 | UI → sclang | Overdub (allocate + record if empty) |
| `/ji/loop/edit_set` | [int index, string param, value] | UI → sclang | One edit; 27 params (see §15.2) |
| `/ji/loop/mem_save` | [int slot 0..3, int index 1..4] | UI → sclang | Copy loop → slot |
| `/ji/loop/mem_recall` | [int slot 0..3, int index 1..4] | UI → sclang | Copy slot → loop, leave it stopped |
| `/ji/loop/mem_clear` | int slot 0..3 | UI → sclang | Free one slot |
| `/ji/loop/edit` | 29 values (below) | sclang → UI | Full edit state |
| `/ji/loop/mem` | [int slot, int filled, int frames, float seconds] | sclang → UI | One slot |
| `/ji/loop/count` | [int index, int beats_left] | sclang → UI | Count-in tick (one per beat, 0 = done/cancelled) |

`/ji/loop/edit` order (29 values, Stage 8.6.2): `index (1..4), reverse
(0/1), rate (float), gain (float), mode (0 normal / 1 grain), sync (0 free / 1
beat), division ("1/16" | "1/8" | "1/4" | "1/2" | "1"), grain_rate (float),
grain_size_ms (float), grain_pos (0..1), grain_scatter (0..1), grain_pitch
(requested float), grain_size_jitter (0..1), grain_pitch_jitter (0..1),
grain_pitch_snap (0/1), grain_smear (0..1), grain_freeze (0/1), grain_motion
("off" | "forward" | "backward" | "pingpong" | "drift"), grain_speed (float),
grain_depth (0..1), grain_burst (0..1), grain_reverse (0..1), count_in ("off" |
"1 beat" | "1 bar" | "2 bars"), auto_play (0/1), effective_pitch (float, the
JI-snapped ratio the voice actually receives), grain_morph (0..1), grain_melt
(0/1), grain_filter (0..1), grain_filter_rate (0.01..2 Hz)`. `grain_pitch` stays the
*requested* ratio; `effective_pitch` is what the voice gets. There is no
`mix`/`tune` value.

Existing loop addresses are unchanged and keep working. `/ji/loop/stop_record`
stops an overdub as well as a record. Every handler validates arity/type, clamps,
logs one line and never raises out of the OSCdef.

All four edits and four slot states are sent from `ui/control_surface.scd` at
install, on `/ji/status/query`, and after every change. The periodic 2 Hz status
tick still sends only CPU + loop states; there is never a message per grain.

**A note on the internal division/count-in symbols.** sclang cannot lex a bare
symbol literal that starts with a digit: `\1_16` tokenises as `\1` followed by
the primitive name `_16`, so the file fails to parse (`expecting CLOSEPAREN`).
The runtime state therefore uses the equivalent **quoted** literals `'1_16'`,
`'1_8'`, `'1_4'`, `'1_2'`, `'1'` and `'1_beat'`, `'1_bar'`, `'2_bars'` (same
Symbol values, valid syntax; see SuperCollider issue #2673). The OSC payloads
are the human-readable strings `"1/16"` … `"1"` and `"off"`/`"1 beat"`/`"1 bar"`/
`"2 bars"`, so nothing about the wire format changes.

`ui/osc_control.py` mirrors the sclang ranges: `LOOP_RATES` (label + float),
`LOOP_DIVISIONS`, `LOOP_GRAIN_MOTIONS` (5 names), `LOOP_EDIT_PARAMS` (27
params: name, label, range, default, kind), `LOOP_COUNT_IN_OPTIONS`,
`LOOP_COUNT_IN_DEFAULT = "off"`, `LOOP_AUTO_PLAY_DEFAULT = False`,
`LOOP_MEM_SLOTS = 4`, plus `LoopEdit` (29 fields), `LoopMemory`,
`ji_ratio_label` / `loop_snap_pitch` and the strict `parse_loop_edit` (exactly
25), `parse_loop_mem` (exactly 4) and `parse_loop_count` (exactly 2) parsers.

The Python bridge is untouched: loops never go through it, and a Stage 8.5
performance state still contains **no** loop data.

## 7. Beat-synced granular clock

Grains in beat mode are scheduled on `~ji_tempo_clock`, never with a sleep loop.
The next target is the previous target plus the division (`d * 60 / bpm`
seconds), rescheduled from inside the callback while the loop is playing, in
grain mode, with `sync = beat`, and with `~ji_rhythm_running` true. Each
callback sends one incrementing `grainTrig` and logs nothing per grain.

* Transport stop cancels/parks a beat-synced grain loop even when the loop's own
  mode is `free`: it cancels the grain schedule, stops the voice, sets
  `stopped` and logs `[LOOP i] grain beat 1/16 stopped (transport)`.
* Free-mode grains keep running with the transport stopped.
* Since 8.6.1 one subdivision is a **burst**, not one grain: the voice's beat
  clock runs at `grainRate * (1 + grainBurst * burstEnv)` where `burstEnv` is
  `Env.perc(0.001, 1.0)` scaled by `grainDecay` (about half the division).
  `~ji_loop_grain_start` sets the decay on every start/restart, so a tempo or
  division change re-scales the burst.
* A tempo change cancels and reschedules at the next boundary.
* Transport start does **not** auto-start loops; the human presses Play and the
  grain schedule starts with it.

## 8. Diagnostics (one line per event, never per grain)

```text
[LOOP 2] dub start (buffer 8.0 s)
[LOOP 2] dub complete (1 pass)
[LOOP 2] dub stopped
[LOOP 2] edit reverse=1
[LOOP 2] edit rate=0.666667 (2/3)
[LOOP 2] edit count_in=1 bar auto_play=1
[LOOP 2] edit grain mode=on sync=beat div=1/16
[LOOP 2] count-in 1 bar (4 beats)
[LOOP 2] record start after count-in (8.0 s)
[LOOP 2] count-in skipped (second Rec)
[LOOP 2] count-in cancelled (transport stop)
[LOOP 2] auto-play (8.0 s)
[LOOP 2] auto-play skipped: transport stopped (beat mode)
[LOOP 2] grain beat 1/16 started (burst decay 0.016 s)
[LOOP 2] grain beat 1/16 stopped (transport)
[LOOP 2] edit grain pitch=1.490 -> 1.5000 (snap 1)
[LOOP 2] grain pitch 1.490 -> 1.5000 (snapped to the current set)
[LOOP 2] edit grain_motion=bounce rejected (use off/forward/backward/pingpong/drift)
[LOOP 2] edit grain_motion=bounce rejected (value not allowed)
[LOOP 2] mem A saved (8.0 s)
[LOOP 2] mem A recall -> loop 2 (stopped)
[LOOP] mem A cleared
[LOOP 2] mem A is empty
[UI] ignore /ji/loop/edit_set: index must be 1..4
```

`[LOOP]`, `[RHYTHM]`, `[UI]`, `[LOOKUP]` and `[BRIDGE]` keep their meaning.

## 9. Code map

| File | Change |
|---|---|
| `synthdefs/ji_loop_voice.scd` | NEW: normal / reverse / varispeed / granular voice into the accepted autotune; 8.6.1 adds motion, per-grain size/pitch jitter, the beat burst, the grain-mode retune floor and the post-autotune smear |
| `synthdefs/ji_buf_copy.scd` | NEW: server-side mono buffer copy |
| `synthdefs/ji_looper_recorder.scd` | MOD: additive `preLevel` / `loop` arguments |
| `tests/sc/test_live_rig.scd` | MOD: edit state, `~ji_loop_edit_args`, dub, auto-play, count-in bookkeeping, memory slots, `\ji_loop_voice` player; 8.6.1 adds the grain/motion state, `~ji_loop_snap_ratio`, `~ji_loop_effective_pitch`, `~ji_loop_refresh_pitch`, `~ji_loop_grain_decay` and the 25-value `/ji/loop/edit` |
| `tests/sc/test_loop_edit.scd` | NEW: human-run sclang self-test; 8.6.1 adds the motion/jitter/snap/smear/freeze checks and the Div regression |
| `ui/control_surface.scd` | MOD: five loop-edit handlers, `\ji/loop/edit|mem|count`, count-in, beat-grain clock, transport integration; 8.6.1 sets the burst decay on every grain start/restart (version 8.6.1) |
| `ui/osc_control.py` | MOD: addresses, tables, builders, `LoopEdit`/`LoopMemory`, parsers, store, controller methods; 8.6.1 widens the tables to 23 params and `LoopEdit` to 25 fields and adds `LOOP_GRAIN_MOTIONS`, `ji_ratio_label`/`loop_snap_pitch`; 8.6.2 adds morph/melt/filter (27 params, 29 fields) |
| `ui/ji_ui.py` | MOD: `Dub` per loop, Loop edit row, Grain row, countdown label, 1280×880; 8.6.1 adds the Grain motion row, the snap status and 1280×910; 8.6.2 adds the Morph row (Morph/Melt/Filter/F.Rate) and 1280×940 |
| `ji_start.scd` | MOD: `requiredDefs` includes `\ji_loop_voice` and `\ji_buf_copy` |
| `tests/test_ui.py` | MOD: pure + Qt coverage for the above |
| `docs/` | MOD: this file, `START_HERE.md`, `GUI_PARAMETERS.md`, the design doc, the root `README.md` |

## 10. Risks and decisions

* **Untestable DSP.** No sclang/scsynth/audio in the agent environment. The new
  SynthDefs are reviewed code only; `tests/sc/test_loop_edit.scd` and the
  checklist are the verification. Reverse relies on `Phasor`'s documented
  two-sided `sc_wrap`; the self-test and step 5 of the checklist confirm it.
* **Autotune stays in the chain.** A varispeeded or granular loop is still
  analysed and retuned toward the current set. The audible pitch is the
  interaction of material, rate/grain settings and set — intended "new
  material", not an exact repitch.
* **Count-in** counts on the tempo clock in quarter beats; a bar follows the
  current meter. Free mode counts with the transport stopped; beat mode cancels
  on a transport stop; a second Rec skips the count-in. No silent pre-roll.
* **Auto-play** starts at the take's exact end (not after the +0.15 s safety
  timer) so beat mode cannot flam. The identity token prevents a stale take from
  firing.
* **Clicks.** Rate/reverse/mode changes are smoothed (`Lag`, `XFade2`, the 30 ms
  gate); the human must confirm.
* **Scheduler cancellation.** A pending grain or count-in callback is cancelled
  by bumping a per-loop **generation token**, never by calling `.stop` on the
  object `TempoClock.schedAbs` returns (that object may be the clock itself, so
  stopping it could kill the time base). A stale callback is a no-op.
* **CPU.** Up to four grain voices with Tartini + `PitchShift`; the defaults
  (normal, 1/1) add only a `Phasor`+`BufRd` read against the accepted `PlayBuf`
  path, so the default cost is comparable. Four simultaneous grain voices are
  the intended ceiling.
* **Memory.** Four slots × up to 30 s mono ≈ 23 MB; freed on replace/clear/rig
  re-evaluation.
* **Buffer-copy correctness.** Frame counts, `doneAction`, and freeing the old
  buffer only after its player has faded and the copy completed.
* **Overdub clipping.** No limiter is added; dubbing can clip. Record quieter.
* **Session-only.** Slots vanish on sclang re-evaluation; nothing is written to
  disk; `loops/` does not exist.
* **GUI density.** Two rows + a Dub button (8.6), three rows in 8.6.1; Status
  stays visible; the default window grew to 1280×910 in 8.6.1 and 1280×940 in
  8.6.2 (the scroll area remains the safety net).
* **Docs drift.** `START_HERE.md`, `GUI_PARAMETERS.md`, this file and the design
  doc are part of acceptance.

## 11. Deferred (deliberately not built)

Loop persistence of any kind (files, `.aiff`, `loops/`), loop recall inside
Stage 8.5 performance states, more than four loops, stereo loop buffers,
per-loop output-pair routing, per-loop autotune parameter knobs (retune / mix /
window / threshold — the loop always keeps `mix = 1.0`), one-shot mode, loop
undo/redo, independent time-stretch, tempo re-stretch, a loop-file browser, loop
position/phase readouts, and a pattern sequencer.

## 12. Human acceptance checklist

1. Start the rig normally. Loops 1/2 record, play and retune to JI exactly as
   before (rate 1/1, normal, gain 0.70): **no audible regression**.
2. **Dub**: record loop 1, press `Dub`, play over it for one pass; the layer is
   mixed in and the loop keeps playing. `Stop Rec` ends a dub early. `Rec` still
   replaces.
3. **Count-in**: set `Count-in = 1 bar` on loop 1; with the transport running
   press Rec, watch `counting 3`, `counting 2` …, and confirm the take starts one
   bar later at the configured length. Try `1 beat` and `2 bars`. With the
   transport stopped, free mode still counts in; beat mode records only with a
   running transport and a transport stop cancels a count-in in beat mode. A
   second Rec during the count skips it and records immediately.
4. **Auto-play**: enable `Auto`; record and confirm the loop starts as soon as
   the take ends (an early `Stop Rec` also auto-plays the partial take). In beat
   mode with the transport stopped it logs `auto-play skipped` and leaves the
   loop recorded.
5. **Reverse**: toggle `Rev` while playing — backwards without a click; toggling
   back restores forward.
6. **Ratios**: each of `2/1, 3/2, 5/3, 5/4, 4/3, 1/1, 3/4, 4/5, 2/3, 3/5, 1/2`
   plays at that speed and pitch (2/1 = octave up, 2/3 = down a fifth), no clicks
   on change. The JI autotune may pull it partly back toward a class — expected.
   `1/1` must sound exactly like the accepted loop.
7. **Granular free**: `Grain`, `Sync = free`; the loop becomes a grain cloud;
   `Rate`/`Size`/`Pos`/`Scatter`/`Pitch`/`Rev%` each change it audibly and
   smoothly; it runs with the transport stopped.
8. **Granular beat**: `Sync = beat`, transport running, loop playing; onsets
   lock to `Div`; stopping the transport stops the loop; restarting and pressing
   Play resumes.
9. **Memory slots**: save loop 1 into `A`; re-record loop 1 with different
   material; recall `A` → the original audio and edit state return (stopped);
   recall `A` into loop 2; clear `A`; recall an empty slot logs one line.
   Re-evaluate the rig → slots are empty (session-only).
10. **Gain**: per-loop gain changes the balance; the default matches today.
11. **Status**: the loop line shows direction, ratio, count-in, auto-play, gain
    and grain mode; no message storms (never per grain).
12. Performance states (8.5) still save/load with **no loop keys**; loop audio
    and edits are untouched by a state load.
13. Drums/bass/lead/drone/groove/home/arp/state and the accepted 8.3d/8.4/8.5
    controls do not regress; CPU stays acceptable with four loops.
14. Record the result. **8.6 is not complete until the owner confirms the loop
    audio, the memory slots across a session, and no regressions.**

## 12b. Known follow-up — Stage 8.6.1 granular redesign

The first granular build was **audible but rejected by the owner on musical
grounds** (a static read position repeats one slice, one grain per trigger with
no spread/jitter, raw non-set pitch, no motion). Stage 8.6.1 redesigns it:
motion through the buffer (off/forward/backward/pingpong/drift, speed, depth,
freeze), a real cloud (density/size defaults, per-grain size and pitch jitter),
JI-snapped grain pitch that follows set mutations, a post-autotune smear, and a
beat-mode burst envelope. It also fixes the `Div` combo resetting to `1/16`.
Implemented in §14 below (2026-11-03 — awaiting human acceptance), and followed
by the owner-requested Morph/Melt/filter in §15 (8.6.2). The
implementation prompt is
[docs/prompts/PROMPT_stage8_6_1_granular_redesign.md](prompts/PROMPT_stage8_6_1_granular_redesign.md).
Everything in §1–§12 above stays the baseline except the grain parameter values,
the new grain/motion parameters and the `/ji/loop/edit` payload length (15 ->
25), which 8.6.1 changes.

## 13. Tests

Python (from `ji-live/`):

```bash
uv run --with python-osc python tests/test_ui.py --pure
python tests/test_mpe.py --pure
```

plus every harness listed in the root `README.md`. These are unchanged except
`test_ui.py`; **the 8.6.1 agent environment had no Python interpreter at all**,
so they were reviewed, not executed — the human should run them (they are the
same commands as 8.6).

The Stage 8.6 additions in `tests/test_ui.py` cover the rate/division/motion
tables, every builder and its clamps/rejections, `LoopEdit` defaults,
`count_in_beats` in 4/4 and 3/4 and the JI snap maths, the strict 25/4/2-value
parsers, store application (including `dubbing`/`counting`), the controller
sends (to sclang) and the Qt rows, Dub buttons, countdown label,
signal-blocked sync, grain/motion disabling, the stopped-transport and frozen
hints and the §8 Div regression (Div = 1/4 survives an unrelated echo).

sclang (human-run, after `startup.scd` + `tests/sc/test_live_rig.scd` +
`ui/control_surface.scd`):

```text
evaluate  tests/sc/test_loop_edit.scd
```

It prints `PASS`/`FAIL` per check, then the manual audio steps, then one
`[LOOP EDIT TEST] PASS`/`FAIL` line. The memory section replaces loop 4's audio
with slot A (loop 4 is the scratch loop for that check).

---

## 14. Stage 8.6.1 — granular redesign (2026-11-03)

Owner rejection (verbatim, 2026-11-03):

> "ok its audible now but not very useful, instead of making fun glitchy sounds
> it just kinda makes annoying noises playing grains over and over, and not much
> parameter tweaking makes it anything interesting, also moving some parameters
> makes it jump back to 1/16 div for some reason. the goal is to get cool
> elvolving effects kinda like the Output Portal plugin, moving through the
> buffer. also the pitch is kinda useless since as far as i can tell it doesn't
> really snap to the frequency set we are using."

**Status: implemented 2026-11-03 — awaiting human acceptance.** No sclang, no
scsynth and no audio in the agent environment: every DSP claim below is
reviewed code plus `tests/sc/test_loop_edit.scd` and the §14.12 checklist. The
owner is the authority on the sound.

### 14.1 What was wrong with the 8.6 grain half

| Symptom | Cause in 8.6 |
|---|---|
| "grains over and over", static | the read position was a constant `Pos` plus scatter; nothing moved through the buffer |
| one tick per beat division | beat mode fired exactly one `grainTrig` per subdivision, with no burst envelope |
| "annoying noises", no texture | defaults were 8 grains/s × 80 ms (a sparse stutter) with no size jitter |
| "pitch kinda useless" | `grainPitch` was the raw 0.25–4.0 float; it was never related to the JI set |
| "jump back to 1/16 div" | `~ji_loop_division_symbol` used `indexOf`, which compares by **identity** in SuperCollider; every division/count-in edit sent as an OSC String was silently rejected (§14.9) |

### 14.2 The new grain path

```text
grainTrig (beat) / Impulse (free)
        |
        v
  burst env (Env.perc x grainDecay) --> beat clock = grainRate * (1 + grainBurst * env)
        |
        v  trig
  motion phase (off / forward / backward / pingpong / drift)
        |  grainSpeed [buffer lengths/s] * ControlDur  (per control block)
        v
  posFraction = (grainPos + grainDepth * (phase - 0.5)).wrap(0, 1)
        |
        v
  basePos + scatter * TRand(-1, 1, trig)      per-grain size jitter (1 +- 0.5 * SizeJit)
        |                                     per-grain pitch jitter (1 +- 0.5 * PitchJit)
        v
  centre clamped to [halfSpan, bufferSeconds - halfSpan]  -> TGrains (mono, 1 per loop)
        |
        v
  XFade2(normal, grain, mode) -> Tartini -> tracker buses -> target/clamp
        -> Lag(retune >= 0.15 s in grain mode) -> PitchShift -> XFade2(mix = 1.0)
        -> smear: XFade2(dry, 2 x AllpassC, grainSmear) -> * gain * env -> outBus
```

One `TGrains` per loop (never per grain voice), no `LocalBuf`, no FFT: four
simultaneous grain voices remain the CPU ceiling.

### 14.3 New parameters (all clamped in sclang and mirrored in Python)

| Parameter | Wire name | Range | Default | Meaning |
|---|---|---|---|---|
| Motion | `grain_motion` | off / forward / backward / pingpong / drift | forward | how the read position travels |
| Speed | `grain_speed` | 0.001–0.5 buffer lengths/s | 0.03 | scan speed |
| Depth | `grain_depth` | 0–1 | 0.6 | scan depth around `Pos` |
| Size Jit | `grain_size_jitter` | 0–1 | 0.2 | per-grain size variation |
| Pitch Jit | `grain_pitch_jitter` | 0–1 | 0.0 | per-grain pitch scatter |
| Snap | `grain_pitch_snap` | 0/1 | 1 | JI-snap the grain ratio (done in sclang) |
| Smear | `grain_smear` | 0–1 | 0.0 | post-autotune diffusion |
| Freeze | `grain_freeze` | 0/1 | 0 | hold the motion |
| Burst | `grain_burst` | 0–1 | 0.6 | beat mode: extra density after a trigger |
| Burst decay | `grain_decay` | 0.01–1 s | computed | `0.5 * divisionBeats * 60 / bpm` |

New defaults for the 8.6 parameters (they only matter while `mode = grain`):
`grain_rate` **20**, `grain_size` **120 ms**, `grain_pos` **0.5**,
`grain_scatter` **0.1**; `grain_rate`'s range widened to **0.5–80**.
`/ji/loop/edit_set` now accepts **23** params (`reverse`, `rate`, `gain`,
`mode`, `sync`, `division`, `grain_rate`, `grain_size`, `grain_pos`,
`grain_scatter`, `grain_pitch`, `grain_size_jitter`, `grain_pitch_jitter`,
`grain_pitch_snap`, `grain_smear`, `grain_freeze`, `grain_motion`,
`grain_speed`, `grain_depth`, `grain_burst`, `grain_reverse`, `count_in`,
`auto_play`) and `/ji/loop/edit` carries **25** values (§6).

### 14.4 Motion

A control-rate 0..1 phase is mapped through `grainPos` and `grainDepth`:

* `off` — phase 0.5, so the position is exactly `Pos` and `Depth` has no
  effect (the 8.6 behaviour).
* `forward` / `backward` — `Phasor.kr` at `grainSpeed * ControlDur` (one
  increment per **control block**; `SampleDur` would be 64× too slow). Because
  `Speed` is in buffer lengths per second, one full pass takes `1 / Speed`
  seconds whatever the loop length (0.03 ≈ 33 s, 0.5 ≈ 2 s). `Phasor` applies
  `sc_wrap` every sample, so a negative rate wraps correctly; the backward mode
  starts at the end and runs to the start.
* `pingpong` — a 0..2 ramp (double increment) folded to a triangle.
* `drift` — `LFNoise1.kr(grainSpeed)` scaled 0..1 (a slow random walk).
* `Freeze` multiplies every increment by `(1 - grainFreeze)`: the phase stops
  where it is and continues from there on release, so there is no jump.

### 14.5 JI pitch snapping (the rule)

`Pitch` is a **requested** ratio. With `Snap` on, sclang maps it to the nearest
member of the *current* set × an octave (-3..+3), constrained to 0.25..4.0, by
log2 distance (`~ji_loop_snap_ratio`); the ratio — not the note — is snapped,
because the material is already tuned by the autotune, so a set ratio is a
consonant interval. The result is the **effective ratio**, and that is what the
voice receives as `grainPitch`:

* `/ji/loop/edit` carries both: `grain_pitch` (requested) and
  `effective_pitch` (what the voice gets); the status line shows the effective
  ratio as a fraction (`pitch ×3/2`).
* A `/ji/freqset/update` re-snaps every **playing grain loop** with Snap on
  (`~ji_loop_refresh_pitch`) and logs one `[LOOP i] grain pitch …` line only
  when the effective ratio changed. The voice is re-`set`, so there is no click.
* `Snap` off passes the raw ratio (clamped 0.25..4.0).
* The GUI mirrors the snap (nearest set ratio, log2 distance) for its
  optimistic status line; sclang's echo is authoritative.

### 14.6 Smear

Two `AllpassC` stages (0.007 s / 0.011 s, decay 0.04 / 0.05) **after** the
autotune, crossfaded with `XFade2(dry, wet, grainSmear * 2 - 1)`. At
`grainSmear = 0` the crossfade pan is -1, which is the accepted dry signal
bit-for-bit. It is subtle at low values; it never bypasses the autotune.

### 14.7 Beat-mode burst

The beat clock is `Impulse.kr(grainRate * (1 + grainBurst * burstEnv))`, where
`burstEnv = EnvGen.kr(Env.perc(0.001, 1.0), Changed.kr(grainTrig),
timeScale: grainDecay)`. One `grainTrig` set still produces exactly one burst —
never a message per audio block. `~ji_loop_grain_start` (and every restart)
sets `grainDecay = 0.5 * divisionBeats * 60 / bpm`, so a tempo or division
change re-scales the burst. At fast divisions that decay is short (1/16 at 120
BPM ≈ 16 ms), so a dense cloud there needs a higher `Rate`/`Burst` — the burst
is a small cloud at 1/4 and 1/2, and a single grain is the correct behaviour at
1/16 with a low Rate. With `grainBurst = 0` beat mode is exactly the 8.6
one-grain-per-division behaviour.

### 14.8 Normal mode is unchanged

At the defaults (`mode = normal`, `rate = 1/1`, `reverse = 0`, `gain = 0.70`,
count-in off, auto-play off) the voice is the accepted graph: the normal path
is untouched, the mode crossfade is a constant 0, the retune `Select` is a
constant `retuneTime`, and `XFade2(..., -1)` for the smear is the dry signal.
The new grain parameters are computed but never reach the output while normal
mode is selected.

### 14.9 The "Div jumps back to 1/16" bug (root cause and fix)

Reproduction: Grain on, Sync = beat, Div = 1/4, then move Size or Pos — the Div
combo returns to 1/16.

Root cause (sclang, not the GUI): SuperCollider's `Collection:indexOf` compares
with **identity** (`===`), while the GUI sends the division as a freshly
decoded OSC **String**. `~ji_loop_division_strings.indexOf("1/4")` was
therefore always `nil`, so `~ji_loop_set_edit` rejected every division (and
count-in) edit with one `rejected` line and kept `'1_16'`. The GUI's
optimistic local state showed 1/4; the next successful edit (Size, Pos, …)
returned a `/ji/loop/edit` echo carrying sclang's stale `1/16`, and
`_sync_loop_edit_row` — correctly trusting the authoritative echo — put the
combo back to 1/16. `startup.scd` already documents the same identity gotcha
for device names.

Fix: `~ji_loop_division_symbol` and `~ji_loop_count_in_symbol` use
`indexOfEqual` (value comparison), and the string `mode`/`sync` branches use
`indexOfEqual(...).notNil`. Symbols are interned, so the symbol-direction
lookups were always fine.

Regression coverage: the sclang self-test now asserts that String division and
count-in edits are accepted (they would have failed in 8.6), and the Qt test
sets Div = 1/4, moves Size, feeds the unrelated `/ji/loop/edit` echo and
asserts the combo stays on 1/4. The GUI still applies every echo (no echo is
muted) and only writes a widget when the incoming value differs.

### 14.10 GUI

One **Grain motion** row under the Grain row:
`Motion [off/forward/backward/pingpong/drift]  Speed [0.001..0.5]  Depth
[0..100 %]  Size Jit [0..100 %]  Pitch Jit [0..100 %]  Snap [x]  Smear
[0..100 %]  Freeze [x]`. The grain row keeps Grain / Sync / Div / Rate / Size /
Pos / Scatter / Pitch / Rev% with the new defaults. Grain off disables both
rows; the hint still warns when beat mode needs the transport and says
`motion frozen` while frozen. `grain_burst` (the beat-mode burst amount,
default 0.6) has no dedicated widget in this row yet: it is reachable through
`/ji/loop/edit_set` and sclang. The window is **1280×940** since 8.6.2 (Qt bound 950;
the scroll area stays the safety net). The status line reads
`Loop 2 · ×3/2 · grain/beat · pos 50 % · motion forward · pitch ×3/2 · snap · gain 0.70`.

### 14.11 Risks

* **No audio in the agent environment.** The SynthDef is reviewed code; the
  self-test and §14.12 are the verification. Do not claim acceptance.
* **`Phasor.kr` units.** The increment is per control block (`ControlDur`),
  not per sample. If grains barely move on the human's machine, check that
  first (`~ji_loop_edit_args.(1)` + the printed motion params).
* **Tartini on clouds.** A jittery cloud can track badly; the 0.15 s retune
  floor in grain mode is the mitigation. If it still warbles, lower `Pitch Jit`.
* **`LFNoise1.kr(0)` when frozen** holds its last value (the intended freeze);
  no NaN is expected, but the first audio check is "freeze holds, unfreeze
  continues".
* **Smear at 0 is bit-identical** only because `XFade2` at pan -1 returns its
  first input; the human check 1 confirms it.
* **CPU.** One `TGrains` + two allpasses per loop; four grain voices is the
  ceiling.
* **GUI density.** One more row per redesign (910 at 8.6.1, 940 at 8.6.2); the
  scroll area stays the net.
* **The snap maths.** Snapping is by log2 distance; the boundary between 9/8
  and 5/4 is 1.1858, so e.g. 1.19 → 5/4 (an early draft of the stage prompt
  listed 1.19 → 9/8; the shipped maths and the self-test use 5/4, with 1.18 →
  9/8 covering the 9/8 side).

### 14.12 Human acceptance checklist (8.6.1)

1. **No regression**: normal play (rate 1/1, forward) sounds exactly as before;
   `Smear 0 %` changes nothing.
2. **Motion**: Grain on, Motion = forward, Speed ≈ 0.03, Depth 60 % — the cloud
   slowly travels and evolves; backward / pingpong / drift are clearly
   different; `Freeze` holds it and unfreezing continues.
3. **Cloud**: Rate 20, Size 120 ms, Scatter 10 % is a smooth texture, not a
   stutter; Rate 5 vs 60 is a clear density change; Size Jit thickens it.
4. **Pitch**: Snap on, Pitch 1.5 → a fifth in the current set (status shows the
   effective ratio); mutate the set and a playing grain loop re-snaps without a
   click; Snap off restores the raw ratio; Pitch Jit shimmers.
5. **Smear**: raising it adds blur/space without destroying the JI tuning; 0 is
   dry.
6. **Beat**: Sync = beat with the transport running; grains lock to Div and each
   division is a small cloud (burst), not one tick; changing Div or tempo keeps
   it locked; stopping the transport stops the loop.
7. **Div bug**: change Div, then move Size/Pos/Smear; Div stays.
8. **CPU** stays usable with four loops.
9. Memory slots, dub, reverse, varispeed, count-in, auto-play, per-loop gain and
   the 8.5 states still behave as in §12.
10. Record the result. **8.6.1 is not complete until the owner confirms the
    grain sound and no regressions.**

### 14.13 Out of scope (deliberately not built)

Per-loop autotune knobs, granular playback of the **live** inputs, stereo loop
buffers, per-loop output routing, multi-grain spectral processing (FFT), a
second granular voice per loop, a grain pattern sequencer, any loop
persistence, and loop recall inside Stage 8.5 performance states. Four loops,
mono buffers, the four-channel map and sclang ownership all stay as they were.


---

## 15. Stage 8.6.2 — Morph, Melt and the moving filter (2026-11-03)

Owner request (verbatim, 2026-11-03):

> "ok the new paramaters are usful, is there maybe be some way kind of mix the
> original loop into the granular playback, so theres kind of a granular smearing
> of the original loop and a toggle to start with the the original and
> progressively glitch it with the grains. maybe add some moving filters too that
> melt the original and granular playback together for more texture? the basic
> goal is to have a slider that as it moves brings the loop from its normal
> playback to a smeared and glitched version of it using the grains?"

**Status: implemented 2026-11-03 — awaiting human acceptance.** No sclang, no
scsynth and no audio in the agent environment; every DSP claim below is reviewed
code plus `tests/sc/test_loop_edit.scd` and the §15.9 checklist.

### 15.1 The idea

Until 8.6.1 `mode` was a hard switch: normal **or** grains. 8.6.2 makes it a
continuous blend inside grain mode, so one slider walks the loop from the
original into a smeared, glitched texture:

```text
original loop (BufRd/Phasor, rate + reverse)  ─┐
                                               ├─ XFade2(equal power, blend) ─ sig
grain cloud (TGrains: motion, jitter, ...)   ──┘        blend = mode * grainMorph
                                                             |
                         Tartini -> tracker buses -> clamp -> Lag -> PitchShift
                                                             |
                     XFade2(sig, shifted, mix = 1.0)         |
                                                             v
             smear (2 x AllpassC) -> melt filter (2 moving filters) -> * gain * env
```

* `mode = normal` (Grain off) is unchanged: `blend = 0`.
* `mode = grain` + **Morph 0** is the **original loop** — the dry path, through
  the same autotune, with the exact accepted `retuneTime`. There is no
  dereference of the grain path, so it is bit-identical to normal playback.
* Morph 1 is the full 8.6.1 grain cloud. In between, the equal-power crossfade
  smears the two together (the dry body plus the granular tail).
* The autotune always analyses the blend, so both layers stay in the current
  JI set. The retune floor follows the blend (`max(retuneTime, 0.15 * blend)`)
  instead of the mode, so a mostly-dry morph does not glide like a cloud and a
  fully dry one keeps the accepted retune.

### 15.2 Parameters added

| Parameter | Wire name | Range | Default | Meaning |
|---|---|---|---|---|
| Morph | `grain_morph` | 0..1 | 0.0 | dry loop (0) ↔ grain cloud (1); needs Grain on |
| Melt | `grain_melt` | 0/1 | 0 | ramp the morph instead of jumping: 0 → Morph on, Morph → 0 off |
| Filter | `grain_filter` | 0..1 | 0.0 | moving melt filter amount (0 = bypass) |
| F.Rate | `grain_filter_rate` | 0.01..2 Hz | 0.12 | speed of the filter movement |

`/ji/loop/edit_set` now accepts **27** params and `/ji/loop/edit` carries
**29** values (§6). `~ji_loop_edit_args` passes `grainMorph` (the ramp start
value), `grainFilter` and `grainFilterRate` to the voice.

### 15.3 Melt (the "start dry, progressively glitch" toggle)

`grainMorph` is the **target**; what the voice has is
`~ji_loop_morph_current`. The ramp:

* `Melt` on → a 30 Hz AppClock Routine moves the morph from its current value
  to the Morph slider value over `~ji_loop_melt_seconds` (**4 s**, change it in
  sclang), sending one `player.set(\grainMorph, …)` per step. One
  `[LOOP i] melt 0.00 -> 0.60 over 4.0 s` line at the end, then one
  `/ji/loop/edit`.
* `Melt` off → the same ramp back to 0, so the loop melts back to the original.
* Pressing **Play** with Melt on always starts at **0** (the original), then
  ramps to the Morph value — per play, so re-triggering the loop re-melts it.
* Moving Morph while melting re-targets the ramp from the current value (no
  jump). Leaving grain mode, Stop, Stop All and a memory recall cancel the ramp
  (a per-loop generation token, never `.stop` on the clock object).
* The GUI shows the target (the slider) and a hint: `original loop (morph 0 %)`,
  `morph 40 % dry <-> grains`, `melting to the Morph amount`.

### 15.4 The moving melt filter

Post-autotune, after the smear, a **resonant lowpass** and a **low cut**, each
swept by its own slow LFO (different rate and phase: `filterRate` and
`filterRate * 0.63` with a π offset), over `LinExp` ranges so the movement is
even in log frequency:

```text
lowpass  : 700 Hz .. 6 kHz
low cut  :  90 Hz .. 420 Hz
out      = XFade2(out, RHPF(RLPF(out, lowpass, 0.35), lowcut, 0.8), grainFilter * 2 - 1)
```

The passband never closes, so the filter breathes instead of gating. It is
applied to the **whole** morph output (dry + grains), which is what fuses them,
and it also works with Grain off (on the plain loop). `grainFilter = 0` is an
exact bypass.

### 15.5 No regression at the defaults

`mode = normal`, Morph 0, Melt 0, Filter 0, F.Rate 0.12, `rate = 1/1`, forward,
gain 0.70: the morph crossfade is a constant 0, the retune is the accepted
`retuneTime`, the smear and the melt filter are exact bypasses. A recorded loop
sounds and retunes exactly as the accepted player.

### 15.6 GUI

One new compact row under the Grain motion row:

```text
Morph [0..100 %]   Melt [x]   Filter [0..100 %]   F.Rate [0.01..1 Hz]
```

Morph and Melt are disabled while Grain is off (they are the grain blend);
Filter and F.Rate stay enabled because the moving filter is a post-autotune
texture that also works on the normal loop. The status line gains
`morph 60 %`, `melt` and `filter 30 %` only when they are non-default, e.g.
`Loop 2 · ×3/2 · grain/beat · pos 50 % · motion forward · pitch ×3/2 · morph 60 % · filter 30 % · snap · gain 0.70`.
The window is **1280×940** (Qt bound 950; the scroll area stays the net).

### 15.7 Risks

* **No audio in the agent environment.** Reviewed code + the self-test.
* **Equal-power crossfade.** `XFade2` is equal-power, so a 50 % morph is not a
  -6 dB linear mix; that is intended for a dry/wet blend, but the human should
  check that the loudness feels right in the middle.
* **Melt timing.** The ramp runs on AppClock at 30 Hz; `~ji_loop_melt_seconds`
  is a sclang global (4 s default). If a melt is interrupted by a Stop, the
  value stays where it was (the loop starts at 0 on the next Play anyway).
* **Filter LFOs.** Two `SinOsc.kr` around 0.01–2 Hz; at the slow end the
  movement is a long breath. The lowpass/low cut ranges were chosen so the
  passband never closes; if the human finds it too dark, raise the Filter
  amount slowly (0 – 50 % is the useful range).
* **sclang `var` placement.** SuperCollider only accepts `var` declarations
  at the **top of a function block** ("These you need to declare at the
  beginning of the Function, just after the args" — SC Getting Started, 04
  Functions). A `var` after the first statement is a syntax error, the
  SynthDef never loads, and `ji_start.scd` reports
  `missing SynthDefs: [ji_loop_voice]`. 8.6.2 hit exactly this once (the melt
  filter's declarations) and it was fixed by hoisting them; keep every `var`
  in `ji_loop_voice.scd` above the first `Out.kr`.
* **CPU.** One extra `TGrains` crossfade branch (already there) and two
  biquads per loop; four grain voices stay the ceiling.
* **GUI density.** A fourth row; 1280×940. The scroll area remains the safety
  net on smaller screens.
* **Div/echoes.** Unchanged from 8.6.1: the GUI still applies every
  `/ji/loop/edit` echo and writes a widget only when the value differs.

### 15.8 Diagnostics

```text
[LOOP 2] melt 0.00 -> 0.60 over 4.0 s
[LOOP 2] edit grain_morph=0.35
[LOOP 2] edit grain_melt=1
[LOOP 2] edit grain_filter=0.3
[LOOP 2] edit grain_filter_rate=0.12
```

### 15.9 Human acceptance checklist (8.6.2)

1. **No regression**: Grain off, or Grain on with Morph 0 % — the loop sounds
   exactly like 8.6.1's normal playback, including the autotune.
2. **Morph**: Grain on, Morph 0 → 50 → 100 % while playing: the original stays
   intact underneath while the grain cloud fades in and smears it; 100 % is the
   8.6.1 cloud. Both layers stay in the JI set and in time.
3. **Melt**: Morph 60 %, tick Melt, press Play: the loop starts as the
   original and melts into the cloud over ~4 s. Untick: it melts back. Move
   Morph while melting: the ramp re-targets without a jump. A Stop during a
   melt leaves no stuck value.
4. **Filter**: raise Filter over a morphed loop — a slow sweep fuses the two
   layers; F.Rate 0.05 is a long breath, 0.5 is a wobble; 0 % is bypass. With
   Grain off the filter still works on the loop.
5. **CPU** stays usable with four loops (one morphed grain loop is the
   realistic case).
6. Record the result. **8.6.2 is not complete until the owner confirms the
   morph/melt sound and no regressions.**

### 15.10 Out of scope

Per-band (multiband) morphing, a VST-style spectral smear (FFT), a second grain
voice per loop, morph automation/LFO other than the Melt ramp, filter envelope
shapes beyond the two slow LFOs, and loop persistence.

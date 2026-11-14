# Stage 8.6 Agent Prompt — Loop editing (memory slots, overdub, reverse, varispeed, granular)

You are implementing **Stage 8.6** of the JI live-performance system. Every
earlier stage is **owner-approved**: 0–7.1, 8.1, 8.2, 8.3a, 8.3b, 8.3c, 8.3d,
8.4, 8.4.1 and 8.5 (approval recorded 2026-11-02). Treat every documented
behavior as the accepted baseline and do not regress it.

Stage 8.6 is the **first stage since Stage 4/5 that touches audio DSP**: it adds
new SynthDefs and sclang loop logic. The Python bridge stays out of the loop
path; loops remain **sclang-owned** (`ui/control_surface.scd`,
`tests/sc/test_live_rig.scd`, `synthdefs/`). The owner asked for:

> "loop saving and recall in memory slots (these are per session, they dont have
> to be actually saved beyond that). an overdub feature. then i want a bunch of
> loop manipulation features: i want the loops to be able to be reversed, also
> altered by playing it back at various ratios of its length (standard ones like
> 2, 1/2 but also more strange ones like 2/3, 3/5, whatever, and this should
> pitch around the loop). ideally also some strange glitchy features like
> granular playback (free and beat-synced) that can be tweaked with parameters.
> and any other features already listed in the docs that i missed and sound
> useful."
>
> — owner, 2026-11-02

Follow-up the same day, clarifying the pitch question and adding two record
options:

> "its ok to have the repitched loop going into the autotune, it doesn't have to
> be repitched exactly, the goal is just to have some modification of the loop
> that introduces new material. a pitched up or down loop going into the
> autotune is perfectly fine. also some additional features i forgot to include-
> selectable options to have a countdown before the loop record (ex. click the
> button and then it starts recording a bar later), and an option to have the
> loop start playing immediately after recording."
>
> — owner, 2026-11-02

**Important:** this stage's core is SuperCollider code and the agent environment
has **no sclang/scsynth and no audio**. You cannot run the DSP. Build
defensively, keep every default byte-identical to today's sound, and ship the
human self-test in §9.2 so the owner can verify the audio. Do not claim audio
acceptance. The owner is the authority.

Do not run another stage concurrently in this working tree.

---

## 1. Read these files first

Read the code and docs, not just the summaries:

```text
docs/design_doc_10.28.10.50   §6.6, §6.10, §6.14, §7.1, §7.3, §8.6.1, §8.9, §9,
                              §13, §14
docs/START_HERE.md            authoritative start/stop/flag sheet
docs/GUI_PARAMETERS.md        every GUI control and its range (Loops, Rhythm)
docs/README_stage8_2.md       free/beat loop modes and beat quantization
docs/README_stage8_5.md       performance states: loops are deliberately excluded
docs/README_live_rig.md       the four-channel live rig and loop audio map
synthdefs/ji_looper_recorder.scd
synthdefs/ji_looper_player.scd
synthdefs/ji_autotune_ch.scd  the accepted Tartini/PitchShift graph
startup.scd                   buses 0–18, SynthDef auto-load, boot
tests/sc/test_live_rig.scd    ~ji_record_loop / ~ji_play_loop / ~ji_stop_loop /
                              ~ji_stop_record / ~ji_stop_all, voice table,
                              tracker buses, lookup, glide broadcast
tests/sc/test_dual_loop.scd   the Stage 4 legacy path (must keep working)
tests/sc/test_rhythm.scd      the human-run sclang check style to copy
ui/control_surface.scd        loop mode/length state, beat scheduling,
                              /ji/loop/* handlers, status senders
ui/osc_control.py             loop builders/validators, parsers, Store,
                              OSCController (loops go to sclang, not the bridge)
ui/ji_ui.py                   the compact Loops group
ji_start.scd                  requiredDefs + the one-file start path
run/ji_station.py             --check
tests/test_ui.py              loop builders/parsers/store + Qt checks
tests/test_startup.py         ji_start.scd/doc checks
```

Run Python tests from `ji-live/`.

---

## 2. What you must preserve

- Stage 5.5 audio (2 in / 16 out, 48 kHz, blockSize 64), the loop audio map
  (loops 1/2 → SC 2/3; loops 3/4 sum into the same two channels), the MPE buses,
  the drums/bass/lead voices, home sets and performance states. Do not change
  their contracts.
- **`MutationEngine.propose_mutation()` / `mutate_many()` behavior must not
  change.** `tests/test_mutation.py` must pass **unmodified**.
- **`/ji/freqset/update` stays the only bridge → sclang set update**, one update
  per applied change. A loop's JI retune at the shipped defaults (rate 1/1,
  normal mode, tune on) must behave exactly like today: the loop keeps playing
  through the Tartini → nearest-class → PitchShift chain.
- The **Python bridge does not change** (no new bridge arguments, no state-file
  keys): loops are sclang-owned and stage 8.5 explicitly excludes loop recall.
  A saved performance state still contains **no** loop data.
- The existing loop behavior at defaults: Free/Beat mode, lengths, Rec, Stop
  Rec, Play, Stop, Clear, Stop All, beat quantization, `/ji/loop/state`,
  `/ji/loop/config`, `/ji/loop/set_default_length`. The four-loop count stays.
  The new record options **ship off**: count-in = off and auto-play = off, so a
  Rec still starts immediately and a finished take still waits for Play.
- The loop **always stays in the accepted JI autotune chain** (Tartini →
  nearest class → PitchShift, `mix = 1.0`). Varispeed and granular only change
  what goes *into* that chain; they never bypass it.
- The one-sclang + one-Python start path (`ji_start.scd` + `run/ji_station.py`).
  `ji_start.scd` must know about every new SynthDef and still refuse to install a
  half-working rig.
- Keep the compact 8 pt GUI. This stage adds **at most two compact rows** (plus
  a `Dub` button per loop box). Status must stay visible; the scroll area stays
  the safety net. If the window grows, document the new default size (expect
  around 1280×880) and update the Qt test's height bound.
- **No loop file persistence** (`Buffer.write`/`.aiff`/`loops/` are out), no
  more loops, no stereo loop buffers, no per-loop output-pair routing.
- Do not add loop/autotune keys to performance-state files.

Existing Python tests that must keep passing:

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
uv run --with python-osc python tests/test_glide.py
uv run --with python-osc python tests/test_state.py
uv run --with python-osc python tests/test_startup.py
python tests/test_mpe.py --pure
uv run --with python-osc python tests/test_ui.py --pure
```

---

## 3. Scope

### 3.1 In scope

1. **Loop memory slots** — 4 slots `A`–`D`, **per session only**.
   Save copies a loop's audio buffer **and** its edit state; Recall copies the
   slot back into a chosen loop; Clear frees the slot. No files, no persistence.
2. **Overdub** — record onto an existing loop while keeping its contents
   (`preLevel: 1.0`), one pass, auto-stop at the loop length; `Stop Rec` stops
   a dub too. `Rec` keeps its today's replace behavior.
3. **Reverse** — per-loop playback direction.
4. **Varispeed ratios** — per-loop playback at ratios of its natural speed,
   which also **repitches the loop** (tape-style). Fixed ratio list (§4.2). The
   repitched loop still runs through the JI autotune; the autotune may pull it
   partly back toward the current set. That interaction is **intended**: the
   goal is new material, not an exact transposition (§4.3).
5. **Granular playback** — per-loop switch to a granular voice: free-running or
   beat-synced, with grain rate, size, position, scatter, pitch and per-grain
   reverse probability. Transport-gated in beat mode (like beat-mode loops).
6. **Per-loop gain** — 0..1, default 0.70 (today's hardcoded gain).
7. **Count-in** — a selectable delay before a Rec actually records: `off`,
   `1 beat`, `1 bar` or `2 bars` (a bar is the current meter's bar), counted on
   the tempo clock. Default `off`.
8. **Auto-play** — a per-loop option that starts the loop playing as soon as a
   take (or an empty-loop dub) finishes. Default off.
9. **Status + OSC surface** for the above, and a **human sclang self-test**.
10. Docs: stage README, START_HERE, GUI_PARAMETERS, design doc, root README.

### 3.2 Out of scope (do not half-build)

- **Persistence of any kind** for loops (no `Buffer.write`, no `.aiff`, no
  `loops/` folder). The owner: "they dont have to be actually saved beyond
  that".
- **Loop recall inside Stage 8.5 performance states.** State files stay
  loop-free; a state load must not touch loop audio or loop edit params.
- **More than four loops.** The four-channel product map stays as is.
- **Stereo loop buffers / per-loop output pairs / per-loop routing.**
- **Per-loop autotune parameter knobs** (retune/mix/window/threshold). The loop
  always keeps the accepted `mix = 1.0` autotune path (§4.3); full per-loop
  autotune editing is left for a later stage.
- **One-shot mode, loop undo/redo, independent time-stretch, tempo re-stretch,
  loop-file browser, loop position/phase readouts, pattern sequencer.**
  Mention them as deferred in the stage README.
- Any change to mutation, the groove, the voices, the bridge, or the state
  format.

### 3.3 "Other loop features already listed in the docs"

The design doc's planned 8.6 listed *more loops, per-loop routing/gain/overdub,
loop-player autotune controls, loop-file persistence*. This prompt keeps
**overdub** and **per-loop gain**, adds the owner's new manipulation features,
and defers **more loops**, **routing**, **file persistence** (owner) and
**loop-player autotune knobs** (§3.2). Say all of that in the stage README.

---

## 4. DSP design (new SynthDefs)

Keep `synthdefs/ji_looper_player.scd` and `synthdefs/ji_looper_recorder.scd`
working as they are for the legacy `tests/sc/test_dual_loop.scd`. Add:

### 4.1 `synthdefs/ji_loop_voice.scd` — the new live-rig loop voice

One SynthDef that can play the buffer normally (forward/reverse, 0.5×–2×) or
granularly, feeding the same Tartini → tracker-bus → PitchShift autotune graph
as the accepted player.

Suggested argument set (keep these names; the sclang code sets them):

| Arg | Default | Meaning |
|---|---|---|
| `bufnum` | 0 | mono loop buffer |
| `outBus` | 2 | SC output channel (`~ji_loop_outbus`) |
| `freqBus`, `hasFreqBus` | 0 | loop tracker buses (always written) |
| `targetFreq` | 220 | written by the sclang lookup |
| `retuneTime` | 0.05 | `Lag` for the autotune ratio |
| `windowSize` | 0.1 | PitchShift window |
| `mix` | 1.0 | fixed at 1.0: the loop always runs through the JI retune (keep the arg for graph compatibility) |
| `pitchRangeLimit` | 12 | semitones, same clamp as today |
| `gain` | 0.7 | per-loop output gain |
| `tartiniThreshold` | 0.9 | tracker threshold |
| `gate` | 1 | 30 ms fade, `doneAction: 2` |
| `mode` | 0 | 0 = normal, 1 = grain |
| `reverse` | 0 | 0 = forward, 1 = reverse |
| `rate` | 1.0 | varispeed ratio (frames per output sample) |
| `grainSync` | 0 | 0 = free, 1 = beat-synced |
| `grainRate` | 8.0 | free grains per second |
| `grainSize` | 0.08 | grain duration in seconds |
| `grainPos` | 0.0 | read position, 0..1 of the buffer |
| `grainScatter` | 0.0 | position scatter, 0..1 of the buffer |
| `grainPitch` | 1.0 | grain playback ratio (0.25..4.0) |
| `grainReverse` | 0.0 | per-grain reverse probability 0..1 |
| `grainTrig` | 0 | beat-synced trigger counter (see below) |

Graph requirements:

* **Normal mode**: read the buffer with `BufRd` driven by a wrapping `Phasor`
  whose increment is `Lag.kr(rate * (reverse ? -1 : 1), 0.02)` (frames per
  output sample, `rate 1.0` = natural speed), `loop: 1`, `interp: 4`. `LeakDC`
  after the read. A negative increment must wrap from the start back to the end
  (verify in the self-test; if `Phasor` does not wrap backwards, use an explicit
  `Wrap`/`LocalIn` phase). Do not restart the read head on a reverse change: the
  phase is continuous and the direction flips.
* **Grain mode**: `TGrains` (mono) with a trigger:
  * free (`grainSync = 0`): `Impulse.kr(grainRate)`;
  * beat (`grainSync = 1`): `Changed.kr(grainTrig)` — sclang sends an
    **incrementing integer** `grainTrig` once per subdivision, so one OSC set
    produces exactly one grain. Never send per-audio-block messages. Create the
    voice with `grainTrig = 0`, keep a per-loop counter, and let the first
    scheduled grain send `1` (a change from 0 fires). Reset the counter whenever
    the grain clock restarts so the first grain after a restart always fires.
  * `centerPos` = `(grainPos * frames) + (grainScatter * frames *
    TRand.kr(-1, 1, trig))`. Clamp the *whole* grain inside the buffer: let
    `halfSpan = grainSize * grainPitch * sampleRate * 0.5`, clamp the centre to
    `[halfSpan, frames - halfSpan]`, and if the buffer is shorter than
    `2 * halfSpan` use the buffer centre (a short buffer must never read
    out of bounds);
  * `rate` = `grainPitch` or its negative, selected per grain with
    `TRand.kr(0, 1, trig) < grainReverse` (sample-and-hold per grain);
  * `dur` = `grainSize`, `amp = 1`, `interp = 4`.
* **Mode crossfade**: the two paths are selected with a short crossfade
  (`XFade2` driven by `Lag.kr(mode, 0.02)`), so switching mode cannot click.
* **Autotune**: run the *selected* signal through the same
  `Tartini` → tracker `Out.kr` → target/safe-frequency ratio → clamp →
  `Lag` → `PitchShift` → `XFade2(mix)` chain as `\ji_looper_player`, then
  `* gain * env`. The tracker buses must always be written, in both modes.
* **No new buses.** Use the existing loop tracker buses from
  `tests/sc/test_live_rig.scd`.

### 4.2 The ratio list

Use these exact ratios (label → float) for the GUI combo and the sclang
validator; they are the permitted `rate` values:

```text
"2/1" 2.0      "3/2" 1.5      "5/3" 1.6666667  "5/4" 1.25
"4/3" 1.3333333 "1/1" 1.0     "3/4" 0.75       "4/5" 0.8
"2/3" 0.6666667 "3/5" 0.6     "1/2" 0.5
```

Store the label in the UI and send the float in OSC (or send the label and let
sclang map it — pick one and document; sending the **float** is simpler and
matches the existing float payloads). `2/1` is +12 st, `3/2` +7.02 st,
`2/3` −7.02 st, `3/5` −8.84 st against the recorded material.

### 4.3 The autotune stays in the chain (owner clarification)

**Do not bypass the JI retune.** The owner's clarification is explicit: a
pitched-up or pitched-down loop going into the autotune is fine, and the goal is
"some modification of the loop that introduces new material", not an exact
repitch. So the loop voice always runs with `mix = 1.0` through
`Tartini → nearest class → PitchShift`, exactly like today, in normal, reverse,
varispeed **and** granular mode.

Consequences to document (not bugs):

- a varispeeded loop is re-analysed at the transposed pitch and the autotune may
  pull it partly toward the nearest class in the current set; the audible result
  is the interaction of the recorded material, the rate, the grain settings and
  the current set — that is the "new material";
- a granular cloud's pitch is tracked by Tartini and re-projected note-by-note,
  so it may sound tuned, detuned or both depending on the material and the set;
- `rate = 1/1` and normal mode still behave exactly like the accepted loop.

No tune toggle, no `mix` recomputation, no `tune_effective` field. Keep `mix`
at 1.0 in the voice args so the tracker/lookup path is identical to the accepted
player. The loop status line must not show a "tune bypass" state.

### 4.4 Recorder — overdub support

Extend `synthdefs/ji_looper_recorder.scd` **additively** with `preLevel = 0.0`
and `loop = 0.0` arguments (defaults preserve today's overwrite behavior;
`doneAction: 2` stays). `~ji_record_loop` keeps passing no `preLevel` (or
explicitly 0).

### 4.5 Buffer copy — memory slots

Add `synthdefs/ji_buf_copy.scd`: copy `numFrames` mono frames `src → dst` on the
server with `BufRd` → `BufWr` driven by a `Line` phase (`0 .. numFrames-1` over
`numFrames / sampleRate` seconds, `doneAction: 2`), so sclang can copy a loop
into a memory slot (and back) without file I/O. Do not depend on a version-
specific `Buffer.copyData`; the small SynthDef is the contract.

### 4.6 CPU and limits

Four loop voices already run Tartini + PitchShift. Granular adds `TGrains`; keep
the default path (normal, rate 1/1) as cheap as today, and note in the docs that
4 simultaneous grain voices are the intended ceiling. The status/CPU line must
stay usable; no new control buses.

---

## 5. sclang state and functions

### 5.1 Per-loop edit state

Add to `tests/sc/test_live_rig.scd` (near the loop bookkeeping):

```supercollider
~ji_loop_edit = Array.fill(4, {
    (
        reverse: 0, rate: 1.0, gain: 0.7,
        mode: \normal, sync: \free, division: \1_16,
        grainRate: 8.0, grainSize: 0.08, grainPos: 0.0,
        grainScatter: 0.0, grainPitch: 1.0, grainReverse: 0.0,
        countIn: \off, autoPlay: 0
    )
});
~ji_loop_grain_sched = Array.newClear(4);
~ji_loop_count_sched = Array.newClear(4);
~ji_loop_dub_was_playing = Array.fill(4, { false });
~ji_loop_mem = Array.newClear(4);   // each: (buf, frames, seconds, edit)
```

`tests/sc/test_live_rig.scd` is evaluated **before** `ui/control_surface.scd`,
so any function that touches `~ji_tempo_clock`, `~ji_rhythm_running` or
`~ji_ui_addr` must nil-guard them and treat "not installed yet" as "transport
stopped / no UI". Never send a status from the live-rig install block; the
status senders live in the control surface, where `~ji_ui_addr` exists. The OSC
`division` string maps to the symbols `\1_16`, `\1_8`, `\1_4`, `\1_2`, `\1`
(beats).

Ranges/defaults (validate and clamp in sclang, and mirror the same ranges in
`ui/osc_control.py`). The sclang event keys are `countIn`/`autoPlay`; the OSC
parameter names and the edit status use `count_in`/`auto_play`:

| Param | Range | Default | Notes |
|---|---|---|---|
| `reverse` | 0/1 | 0 | |
| `rate` | one of §4.2 | 1.0 | float; reject unknown values with one line |
| `gain` | 0..1 | 0.7 | linear |
| `mode` | `normal` / `grain` | `normal` | |
| `sync` | `free` / `beat` | `free` | beat needs the transport |
| `division` | `1/16`, `1/8`, `1/4`, `1/2`, `1` (beats) | `1/16` | grain interval |
| `grain_rate` | 0.5..40 grains/s | 8.0 | free mode |
| `grain_size` | 5..500 ms | 80 ms | store seconds internally |
| `grain_pos` | 0..1 | 0.0 | fraction of buffer |
| `grain_scatter` | 0..1 | 0.0 | fraction of buffer |
| `grain_pitch` | 0.25..4.0 | 1.0 | ratio |
| `grain_reverse` | 0..1 | 0.0 | per-grain probability |
| `count_in` | `off`, `1 beat`, `1 bar`, `2 bars` | `off` | delay before a Rec records; `1 bar` = `meter_num * 4 / meter_den` quarter beats (4/4 = 4, 3/4 = 3, 6/8 = 3) |
| `auto_play` | 0/1 | 0 | start playing when a take ends (see §5.2) |

`~ji_loop_edit_args.(index)` returns the full Synth arg list from the state +
buffer + buses + `mix = 1.0` (§4.3). Use it both for `~ji_play_loop` and
for the "edit while playing" `Synth.set` calls (only set the args that changed,
or all of them — one `set` per edit is fine).

### 5.2 Functions to add / change in `tests/sc/test_live_rig.scd`

- `~ji_play_loop.(i)` — create `\ji_loop_voice` instead of
  `\ji_looper_player`, with all edit args. Keep the existing first-play target
  logic, the `~ji_loop_states` transitions, the log line shape and the 30 ms
  stop fade.
- `~ji_stop_loop.(i)` — also stop that loop's grain scheduler; state unchanged.
- `~ji_stop_record.(i)` — handle a `\dubbing` state: stop the recorder, set
  `\playing` if the loop was playing before the dub, else `\recorded`; log one
  line. Then apply **auto-play**: if `~ji_loop_edit[i-1][\autoPlay] == 1` and a
  valid buffer exists, start the loop immediately with the direct
  `~ji_play_loop.(i)` (not the beat-scheduled wrapper). In beat mode this needs
  a running transport: if the transport is stopped, log
  `[LOOP i] auto-play skipped: transport stopped (beat mode)` and leave the
  loop `recorded`. `autoPlay` is ignored when the take was stopped with the
  loop already playing (a dub that keeps playing does not restart). Put the
  auto-play step in **one shared helper** (e.g. `~ji_after_record.(i)`) and call
  it from both the early `~ji_stop_record` path **and** the natural completion
  path, so a take that fills its buffer auto-plays just like an early-stopped
  one. Schedule the auto-play at **exactly the take duration** (`secs` after the
  record starts), not after the existing +0.15 s safety timer: in beat mode a
  20–150 ms late start would flam against the grid. Keep the +0.15 s timer for
  state/logging only, and make it a no-op when the loop is already `\playing`
  (auto-play ran). Cancel a pending auto-play when Stop Rec/Stop/Clear lands
  early; the early stop then runs the helper immediately. Use an identity token
  (e.g. the recorder Synth) so a re-record cannot fire the previous take's
  auto-play.
- `~ji_dub_loop.(i)` — new:
  * no valid buffer → behave like `~ji_record_loop.(i)` (allocate + record) and
    say so in one line;
  * otherwise stop any record/dub, cancel nothing else, remember whether the
    loop was playing (`~ji_loop_dub_was_playing[i]`), start
    `\ji_looper_recorder` on the **same buffer** with `preLevel: 1.0`,
    `recLevel: 1.0`, `loop: 1.0`, `run: 1.0`; keep the player running; state
    `\dubbing`; schedule an auto-stop after `buffer.duration + 0.15`. When the
    dub stops, return to `\playing` if it was playing before, otherwise
    `\recorded`.
  * one log line at start (`[LOOP i] dub start (buffer … s)`), one at stop.
- **Count-in** (in `ui/control_surface.scd`, in `~ji_record_loop_action`, so
  direct `~ji_record_loop` calls keep their immediate behavior):
  * if `~ji_loop_edit[i-1][\countIn] == \off`, record exactly as today;
  * otherwise cancel any pending record/count-in, compute
    `beats = countInBeats(i)` from the state table, target
    `now.ceil + beats` on `~ji_tempo_clock` (so the take starts on a beat
    boundary; "1 bar" is exactly one bar after the next beat), set
    `~ji_loop_states[i-1] = \counting`, send `/ji/loop/state`, log one
    `[LOOP i] count-in 1 bar (4 beats)` line, and `schedAbs` the target;
  * at the target: recompute the take seconds **at fire time**
    (`~ji_loop_take_seconds.(i)`), call `~ji_record_loop.(i, seconds)`, log
    `[LOOP i] record start after count-in (n.n s)`; while a count-in is pending,
    skip the existing 0.1 s `~ji_ui_send_loop_state_later` (it would overwrite
    `counting` with the previous state); send `counting` once and the real state
    when the record starts;
  * `\counting` is a new state; the buffer is untouched until the record starts.
    Cancelling a count-in must **not** run the auto-play step (there is no new
    take): restore the previous state and leave any existing buffer alone;
  * cancel rules: a second Rec during `\counting` cancels the count-in and
    records immediately (`[LOOP i] count-in skipped (second Rec)`); Stop Rec,
    Clear, Stop All and a new Dub cancel it and restore the previous state
    (`empty`/`recorded`/`stopped`); in **beat mode** a transport stop also
    cancels it (one line), while in **free mode** the count-in continues on the
    clock because free loops are not transport-gated;
  * the count-in applies to **Rec only**: `/ji/loop/dub` starts immediately (it
    is an overdub gesture). Document that.
- `~ji_loop_set_edit.(i, param, value)` — validate/clamp, update
  `~ji_loop_edit[i-1]`, if a player exists `player.set(...)`,
  restart/stop the grain scheduler as needed, send `/ji/loop/edit`, log one
  `[LOOP i] edit param=…` line. Unknown param / bad value → one line, no change.
- `~ji_loop_grain_start.(i)` / `~ji_loop_grain_stop.(i)` /
  `~ji_loop_grain_restart_all` — beat-synced grain triggering (below).
- `~ji_loop_mem_save.(slot, i)`, `~ji_loop_mem_recall.(slot, i)`,
  `~ji_loop_mem_clear.(slot)`, `~ji_loop_mem_send.(slot)`.
- `~ji_ui_send_loop_edit.(i)` and `~ji_ui_send_all_loop_edits`.

### 5.3 Beat-synced granular clock

Beat-synced grains must land on the **tempo clock**, not on an `AppClock` wait
loop. Mirror `~ji_schedule_loop_action`: compute the next subdivision from
`~ji_beat_offset` and `~ji_tempo_clock`, schedule with `schedAbs`, and
reschedule from inside the callback while:

```text
loop is playing
and ~ji_loop_edit[i-1][\mode] == \grain
and ~ji_loop_edit[i-1][\sync] == \beat
and ~ji_rhythm_running
```

Each callback sends one incrementing `\grainTrig` (`player.set(\grainTrig, n)`,
`n` monotonically increasing per loop) and one log line only on start/stop, never
per grain. Division `d` beats at BPM `b` means an interval of `d * 60 / b`
seconds; subdivisions smaller than one beat are fractions (`1/16` = 0.0625
beats). Compute the next target with the existing `~ji_beat_offset` origin
(`target = offset + ((position / d).ceil * d)`) and guard against a zero/negative
interval. On transport stop the existing `/ji/rhythm/tempo` handler already
stops beat-mode loops; extend it so a loop whose **grain sync is beat** is
stopped too, even when its loop mode is `free`: cancel its grain schedule, stop
the voice, state `\stopped`, one line. Free-mode grains keep running when the
transport stops. On transport start, do not auto-start loops; the player is
started by the human (`Play`, beat-quantized as today), and the grain schedule
starts with it. On tempo change, the next scheduled target is recomputed at the
new tempo (cancel and reschedule at the next boundary).

### 5.4 Memory slots (session only)

- Four slots `A`–`D` (0..3). Each holds a **copy** of the loop buffer plus the
  sound edit state (`reverse`, `rate`, `gain`, `mode`, `sync`, `division`,
  `grain_*`) and the buffer duration; never a reference to the live loop buffer.
  `count_in`/`auto_play` are record preferences, not sound: they are **not**
  stored in a slot and are left as they are on recall.
- `save(slot, loop)`:
  * refuse while the loop is `recording` or `dubbing` (one line);
  * require a valid buffer; allocate a new buffer of the same frame count and
    start `\ji_buf_copy`; when it finishes, free the previous slot buffer (if
    any), store `(buf, frames, seconds, edit.copy)` and send the slot status;
  * one `[LOOP i] mem A saved (n.n s)` line.
- `recall(slot, loop)`:
  * require a filled slot (empty → one `[LOOP i] mem A is empty` line);
  * stop record/dub and the loop's player (30 ms fade), wait long enough,
    allocate a new loop buffer of the slot's frame count, copy slot → loop;
    after the copy free the loop's previous buffer, install the new one with
    `~ji_set_loop_buffer`, restore the slot's edit event, send
    `/ji/loop/edit`, `/ji/loop/state` (`stopped`) and `/ji/loop/config`;
  * the recalled loop stays **stopped**; the human presses Play. One line.
- `clear(slot)`: free the slot buffer (if any), nil the slot, send the status,
  one line. `clear` of an empty slot is a logged no-op. Clearing a **loop**
  (the existing `/ji/loop/clear`) still frees only the buffer; it keeps that
  loop's edit state and never touches the memory slots.
- Freeing on cleanup: re-evaluating `test_live_rig.scd` frees any slot buffers
  and stops the grain schedulers (session state ends with the session).
  `~ji_stop_all.()` stops audio, recorders, players and grain schedulers but
  keeps buffers and memory slots, as it does today.
- Never write a file.

### 5.5 Status

- `/ji/loop/state` gains the state strings `dubbing` and `counting`.
- `/ji/loop/edit` (sclang → UI), **15 values** in this exact order:

```text
index (1..4), reverse (0/1), rate (float), gain (float),
mode (0 normal / 1 grain), sync (0 free / 1 beat),
division (string: "1/16" | "1/8" | "1/4" | "1/2" | "1"),
grain_rate (float), grain_size_ms (float), grain_pos (float 0..1),
grain_scatter (float 0..1), grain_pitch (float), grain_reverse (float 0..1),
count_in (string: "off" | "1 beat" | "1 bar" | "2 bars"), auto_play (0/1)
```

There is **no** `mix`/`tune` value: the loop always stays in the autotune.

- `/ji/loop/mem` (sclang → UI), **4 values**:
  `slot (0..3), filled (0/1), frames (int), seconds (float)`.
- `/ji/loop/count` (sclang → UI), **2 values**: `index (1..4), beats_left (int)`.
  Sent once when a count-in is scheduled and on each counted quarter beat
  (`beats_left` = 3, 2, 1 …), then `0` when the recorder starts or the count is
  cancelled. This is a tiny status message per **beat**, never per audio block;
  it drives the `counting N` display.
- Send all four edits and all four slot states from `ui/control_surface.scd`
  at install (where `~ji_ui_addr` exists), on `/ji/status/query`, and after
  every change. `~ji_stop_all` / re-evaluation must not spam statuses.

---

## 6. OSC surface (UI → sclang; loops stay sclang-owned)

| Address | Args | Meaning |
|---|---|---|
| `/ji/loop/dub` | int index 1..4 | start overdub on that loop (allocate+record if empty) |
| `/ji/loop/edit_set` | [int index, string param, value] | one edit; `param` one of `reverse`, `rate`, `gain`, `mode`, `sync`, `division`, `grain_rate`, `grain_size`, `grain_pos`, `grain_scatter`, `grain_pitch`, `grain_reverse`, `count_in`, `auto_play` |
| `/ji/loop/mem_save` | [int slot 0..3, int index 1..4] | copy loop `index` into slot |
| `/ji/loop/mem_recall` | [int slot 0..3, int index 1..4] | copy slot into loop `index`, leave it stopped |
| `/ji/loop/mem_clear` | int slot 0..3 | free one slot |

Existing addresses keep working: `/ji/loop/record|record_length|stop_record|
play|stop|stop_all|clear|set_mode|set_beats|set_length|set_default_length`.
`/ji/loop/stop_record` stops an overdub as well as a record (document it).

Replies: the existing `/ji/loop/state` and `/ji/loop/config`, plus the three
new ones in §5.5: `/ji/loop/edit`, `/ji/loop/mem` and `/ji/loop/count`.

Every handler validates arity/types, clamps, logs one line and **never raises
out of the OSCdef** (guard `msg.size`, use `asInteger`/`asFloat` only after an
`isNumber` check, and keep the existing `[UI] ignore …` style).

Beat quantization: record/play keep their existing free-immediate / beat-next-
beat behavior. Edit changes are **immediate** (real-time performance gestures),
including reverse and rate. Say that in the docs.

---

## 7. Python UI layer (`ui/osc_control.py`, `ui/ji_ui.py`)

### 7.1 Pure layer

- Add the addresses and builders:
  `build_loop_dub(index)`, `build_set_loop_edit(index, param, value)`,
  `build_loop_mem_save(slot, index)`, `build_loop_mem_recall(slot, index)`,
  `build_loop_mem_clear(slot)`. Validate indices/slots/params and clamp values
  like the existing loop builders (raise `ValueError` on unknown names).
- Add the shared spec tables used by the GUI and tests:
  `LOOP_RATES` (label + float, §4.2), `LOOP_DIVISIONS`, `LOOP_EDIT_PARAMS`
  (name, label, low, high, default, integer/bool), `LOOP_COUNT_IN_OPTIONS`
  (`("off", "1 beat", "1 bar", "2 bars")` with beats-per-bar handling),
  `LOOP_COUNT_IN_DEFAULT = "off"`, `LOOP_AUTO_PLAY_DEFAULT = False` and
  `LOOP_MEM_SLOTS = 4`.
- Add `LoopEdit` (15 fields, `is_grain`, `is_beat`, `count_in_beats(bar_beats)`,
  a short `status_line()`) and `LoopMemory` (slot, filled, frames, seconds)
  dataclasses, strict `parse_loop_edit(args)` (exactly 15),
  `parse_loop_mem(args)` (exactly 4) and `parse_loop_count(args)` (exactly 2:
  index 1..4, beats_left 0..64) parsers. Clamp ranges on parse like the other
  parsers; reject the wrong arity. There is **no** `tune_effective`.
- Extend `LOOP_STATES` with `"dubbing"` and `"counting"`.
- Extend `StatusStore` with `loop_edits: dict[int, LoopEdit]` (defaults),
  `loop_mem: dict[int, LoopMemory]` (empty) and `loop_counts: dict[int, int]`
  (all 0); wire all three parsers into `STATUS_PARSERS`, `STATUS_ADDRESSES` and
  `apply()`.
- Add `OSCController.loop_dub`, `set_loop_edit`, `loop_mem_save`,
  `loop_mem_recall`, `loop_mem_clear` — all send to **sclang** (`_send_sc`), like
  the existing loop methods.

### 7.2 Qt window

- Add a `Dub` button to each loop box (third button in the existing grid), with
  a tooltip that says it records **over** the loop (and records fresh if empty).
- Add **one "Loop edit" row** for the selected loop (reuse a new `Loop` combo or
  the existing selection idea): `Rev` checkbox, `Rate` combo (§4.2 labels),
  `Gain` knob/spin 0..1, `Count-in` combo (Off / 1 beat / 1 bar / 2 bars),
  `Auto` checkbox (auto-play), `Mem` combo A–D, `Save`, `Recall`, `Clr`, and a
  status label (`Loop 2 · ×3/2 · rev · count-in 1 bar · auto · gain 0.70`).
  If that row overflows at 1280 px, compact the widths first; do not add a third
  row.
- Add **one "Grain" row**: `Grain` checkbox (normal/grain), `Sync` free/beat,
  `Div` combo, `Rate` spin 0.5–40, `Size` spin 5–500 ms, `Pos` 0–100 %,
  `Scatter` 0–100 %, `Pitch` 0.25–4.0, `Rev%` 0–100 %. Disable the grain
  controls while `Grain` is off; show a hint when `Sync = beat` and the
  transport is stopped.
- While a loop's `/ji/loop/state` is `counting`, its state label shows the
  countdown (`counting 3`, `counting 2` …) from `/ji/loop/count`; the edit
  status line may also show `count-in 1 bar`.
- Incoming `/ji/loop/edit`, `/ji/loop/mem` and `/ji/loop/count` sync the
  controls **with signals blocked** (never echo OSC). The selected loop's edits
  are mirrored; keep a
  per-loop cached `LoopEdit` so switching the selector shows that loop.
- The selected loop selector should follow the incoming edit state of the loop it
  shows, not overwrite the user's selection.
- Window: two more rows + a `Dub` button; expect ~1280×880. Keep the scroll area
  and Status visible; document the new default size; update the Qt test's
  height bound (currently `<= 820`) accordingly.
- Tooltips must state: varispeed repitches the loop but it still runs through
  the JI autotune, so the current set may partly re-pitch it (intended — new
  material, not an exact transposition); granular in beat mode follows the
  transport; `Count-in` delays a Rec by the chosen amount on the beat clock
  (free mode counts while the transport is stopped; beat mode is cancelled by a
  transport stop; a second Rec skips it); `Auto` starts the loop when a take
  ends (beat mode needs a running transport); memory slots are session-only and
  lost when sclang re-evaluates the rig.

---

## 8. Startup, `--check`, docs

- `ji_start.scd`: add `\ji_loop_voice` and `\ji_buf_copy` to `requiredDefs` (the
  boot check must refuse a half-installed rig).
- `run/ji_station.py --check`: no new flag or config is required (this stage has
  no new config file). If anything about the loop edit defaults is worth
  printing, keep it to one line; do not touch the existing output shape.
- `docs/START_HERE.md`:
  * a "Loop editing (Stage 8.6)" runtime section: memory slots, dub, reverse,
    rate ratios (the loop still runs through the autotune), count-in, auto-play,
    granular free/beat, per-loop gain;
  * troubleshooting rows: a Dub while recording, a recall of an empty slot,
    beat-synced grains silent because the transport is stopped, a reversed loop
    sounding "backwards" (expected), a varispeeded loop that still sounds in tune
    (the autotune is doing its job — expected), count-in counting in free mode
    with the transport stopped (expected) but cancelled in beat mode, auto-play
    skipped because the transport is stopped, a `counting` label that never
    moves (stale control surface), memory slots empty after re-evaluating the
    rig, clicks on rate changes.
- `docs/GUI_PARAMETERS.md`: every new control with range/default.
- `docs/README_stage8_6.md`: new stage README — owner quote (both messages),
  what changed, the DSP graph, the "autotune stays in the chain" rule and its
  musical consequence, controls, OSC table, memory-slot semantics, count-in /
  auto-play rules, diagnostics, risks, the human checklist, and the deferred
  list from §3.2.
- `docs/design_doc_10.28.10.50`: update §6.6 (loops: overdub, reverse,
  varispeed, granular, memory slots, count-in, auto-play, no persistence, and
  the "autotune stays in the chain" rule), add §6.21 for the loop edit surface,
  add §7.1 OSC rows, update §8.9 (8.6 built), §9 (status), §13 (loop persistence
  still out; more loops/routing deferred), §14 (changelog).
- Root `README.md`: status paragraph, layout (`ji_loop_voice.scd`,
  `ji_buf_copy.scd`, `test_loop_edit.scd`), test list, docs links.
- Do **not** add loop data to `control/state.py`, the bridge, or `config/`.

---

## 9. Tests

### 9.1 Python (must pass)

```bash
uv run --with python-osc python tests/test_ui.py --pure
python tests/test_mpe.py --pure
```

Extend `tests/test_ui.py` with at least:

- the rate table (labels/floats, exact values) and the division table;
- `build_loop_dub` / `build_set_loop_edit` / mem builders: indices and slots
  clamp, unknown params raise, values clamp to the documented ranges;
- `LoopEdit` defaults (count-in `off`, auto-play off), `is_grain`/`is_beat`
  logic, `count_in_beats(bar_beats)` for each option (4/4 and 3/4), status text
  (no "tune bypass" wording);
- strict 15-value `/ji/loop/edit` parser (wrong arity rejected, values clamped,
  `count_in` accepts only the four labels, `auto_play` 0/1), strict 4-value
  `/ji/loop/mem` parser;
- count-in/auto-play builders and clamps: unknown `count_in` raises, `auto_play`
  is 0/1, the edit builder accepts both new params;
- `parse_loop_count` arity/range, and store application of `/ji/loop/edit`,
  `/ji/loop/mem`, `/ji/loop/count`, and both `dubbing` and `counting` in
  `/ji/loop/state`;
- controller methods send the exact addresses/args **to sclang**;
- Qt: the two new rows exist, Dub buttons send, the `Count-in` combo and
  `Auto` checkbox sync without feedback OSC, a `/ji/loop/count` message shows
  `counting N` on the loop's state label, edit/grain controls sync, `Grain`
  disables the grain row when off, a stopped transport hints at `Sync = beat`,
  and the window still fits (new bound).

All tests in §2 must pass unmodified except `tests/test_ui.py` (extended) and
`tests/test_startup.py` (if the docs/ji_start checks need the new names).

### 9.2 sclang self-test (mandatory, human-run)

Add `tests/sc/test_loop_edit.scd` in the style of `tests/sc/test_rhythm.scd`:

- verify the edit arrays exist, defaults are correct, every parameter clamps and
  rejects bad input (print PASS/FAIL per check);
- verify the memory slots start empty, a save fills one, a recall into another
  loop copies the frame count and edit state, and a clear empties it;
- verify a `\ji_loop_voice` can be created and freed, the tracker buses are
  written, and the voice is created with `mix = 1.0` in every mode (§4.3);
- verify the count-in maths (`off`/1 beat/1 bar/2 bars in 4/4 and 3/4), that a
  count-in can be scheduled and cancelled, and that `autoPlay` defaults to 0;
- print the manual audio steps: record, count-in, auto-play, dub, reverse, each
  rate ratio, free and beat granular, save/recall across loops;
- print one final `[LOOP EDIT TEST] PASS`/`FAIL` line and never throw.

The agent cannot run this file; the owner can. Keep it dependency-light (it
assumes `startup.scd`, `test_live_rig.scd`, `control_surface.scd` are loaded, as
`test_rhythm.scd` does).

---

## 10. Human acceptance checklist

1. Start the rig normally. Loops 1/2 record, play and retune to JI exactly as
   before (rate 1/1, normal, tune on): **no audible regression**.
2. **Dub**: record loop 1, press `Dub`, play over it for one pass; the new layer
   is mixed in and the loop keeps playing; `Stop Rec` ends a dub early. `Rec`
   still replaces.
3. **Count-in**: set `Count-in = 1 bar` on loop 1; with the transport running
   press Rec, watch the countdown (`counting 3`, `counting 2` …), and confirm
   the take actually starts one bar later and has the configured length. Try
   `1 beat` and `2 bars`. With the transport stopped, free mode still counts
   in; beat mode records only with a running transport and a transport stop
   cancels a count-in in beat mode. A second Rec during the count skips it and
   records immediately.
4. **Auto-play**: enable `Auto`; record a take and confirm it starts playing as
   soon as the take ends (early `Stop Rec` also auto-plays the partial take).
   In beat mode with the transport stopped, it logs `auto-play skipped` and
   leaves the loop recorded.
5. **Reverse**: toggle `Rev` while playing: the loop runs backwards without a
   click; toggling back restores forward.
6. **Ratios**: each of `2/1, 3/2, 5/3, 5/4, 4/3, 1/1, 3/4, 4/5, 2/3, 3/5, 1/2`
   plays at that speed and pitch (2/1 = octave up, 2/3 = down a fifth); no
   clicks on change. The loop still runs through the JI autotune, so the current
   set may pull it partly back toward a class — that interaction is **expected**
   and is the "new material"; `1/1` must sound exactly like the accepted loop.
7. **Granular free**: `Grain`, `Sync = free`; the loop becomes a grain cloud;
   `Rate`/`Size`/`Pos`/`Scatter`/`Pitch`/`Rev%` each change it audibly and
   smoothly; the loop runs with the transport stopped. The autotune stays on the
   grain stream (tuned/detuned results are both valid).
8. **Granular beat**: `Sync = beat`, transport running, loop playing; grain
   onsets lock to the chosen `Div`; stopping the transport stops the loop;
   restarting and pressing Play resumes.
9. **Memory slots**: save loop 1 into `A`; re-record loop 1 with different
   material; recall `A` → the original audio and edit state return (stopped);
   recall `A` into loop 2; clear `A`; recall an empty slot logs one line.
   Re-evaluate the rig → slots are empty (session-only).
10. **Gain**: per-loop gain changes the balance; defaults match today's loudness.
11. **Status**: the loop line shows direction, ratio, count-in, auto-play, gain
    and grain mode; no message storms (never per grain).
12. Performance states (Stage 8.5) still save/load with **no loop keys**; loop
    audio and edits are untouched by a state load.
13. Drums/bass/lead/drone/groove/home/arp/state and the accepted 8.3d/8.4/8.5
    controls do not regress; CPU stays acceptable with 4 loops.
14. Record the result. **8.6 is not complete until the owner confirms the loop
    audio, the memory slots across a session, and no regressions.**

---

## 11. Diagnostics

One line per event, **never per grain**:

```text
[LOOP 2] dub start (buffer 8.0 s)
[LOOP 2] dub complete (1 pass)
[LOOP 2] edit reverse=1
[LOOP 2] edit rate=0.666667 (2/3)
[LOOP 2] edit count_in=1 bar auto_play=1
[LOOP 2] count-in 1 bar (4 beats)
[LOOP 2] record start after count-in (8.0 s)
[LOOP 2] count-in skipped (second Rec)
[LOOP 2] count-in cancelled (transport stop)
[LOOP 2] auto-play (8.0 s)
[LOOP 2] auto-play skipped: transport stopped (beat mode)
[LOOP 2] edit grain mode=on sync=beat div=1/16
[LOOP 2] grain beat 1/16 stopped (transport)
[LOOP 2] mem A saved (8.0 s)
[LOOP 2] mem A recall -> loop 2 (stopped)
[LOOP] mem A cleared
[LOOP 2] mem A is empty
[UI] ignore /ji/loop/edit_set: unknown param ...
```

Keep the existing `[LOOP]`, `[RHYTHM]`, `[UI]`, `[LOOKUP]`, `[BRIDGE]` lines
unchanged in meaning.

---

## 12. Risks and decisions to document

- **Untestable DSP.** The agent cannot run sclang/scsynth; the new SynthDefs are
  reviewed code only. The sclang self-test and the human checklist are the
  verification.
- **Autotune stays in the chain (owner clarification).** A varispeeded or
  granular loop is still analysed and retuned toward the current set, so the
  audible pitch is the *interaction* of the material, the rate/grain settings
  and the set. This is intended ("new material"), not an exact repitch; say it in
  the stage README, the GUI tooltip and the checklist.
- **Count-in.** The delay is counted on the tempo clock in quarter beats; a bar
  follows the current meter. Free mode counts while the transport is stopped;
  beat mode cancels on a transport stop; a second Rec skips the count-in. Do not
  start a recorder before the count-in target (no silent pre-roll).
- **Auto-play.** It is a post-take action, not a mode: it fires when a take ends
  (buffer full or `Stop Rec`) and only if a valid buffer exists. Beat mode needs
  a running transport; an early-stop partial take is auto-played as-is. It must
  start **at the take's exact end** (not after the +0.15 s safety timer) or beat
  mode will flam; use an identity token so a pending auto-play from a replaced
  take can never fire.
- **Clicks.** Rate, reverse and mode changes are smoothed (`Lag`, `XFade2`, 30 ms
  gates); the human must confirm.
- **CPU.** Up to 4 grain voices with Tartini + PitchShift; defaults add nothing.
- **Memory.** Four slots × up to 30 s mono ≈ 23 MB; free on replace/clear/rig
  re-evaluation.
- **Buffer copy correctness.** Frame counts, doneAction, and freeing the old
  buffer only after its player has faded and the copy completed.
- **Overdub clipping.** No limiter is added; dubbing can clip — document it.
- **Beat-synced grain drift.** Schedule on the tempo clock, reschedule from
  inside the callback; never use a sleep loop.
- **Session-only.** Slots vanish on sclang re-evaluation; nothing is written to
  disk; `loops/` does not exist.
- **GUI density.** Two rows + Dub; Status visible; document the new size.
- **Docs drift.** START_HERE, GUI_PARAMETERS, the stage README and the design doc
  are part of acceptance.

---

## 13. Deliverables

```text
ji-live/
├── synthdefs/
│   ├── ji_loop_voice.scd        NEW: normal/reverse/varispeed/granular voice
│   ├── ji_buf_copy.scd          NEW: server-side mono buffer copy
│   └── ji_looper_recorder.scd   MOD: additive preLevel/loop args (overdub)
├── tests/sc/
│   ├── test_live_rig.scd        MOD: edit state, dub, mem slots, new player
│   └── test_loop_edit.scd       NEW: human-run sclang self-test
├── ui/
│   ├── control_surface.scd      MOD: /ji/loop/dub|edit_set|mem_* + status +
│   │                            beat grain clock + transport integration
│   ├── osc_control.py           MOD: builders/parsers/LoopEdit/LoopMemory/store
│   └── ji_ui.py                 MOD: Dub buttons + Loop edit row + Grain row
├── ji_start.scd                 MOD: requiredDefs += new SynthDefs
├── tests/
│   ├── test_ui.py               MOD (pure + Qt)
│   └── test_startup.py          MOD only if its ji_start/doc checks need it
├── docs/
│   ├── README_stage8_6.md       NEW: design, procedure, checklist, deferred
│   ├── START_HERE.md            MOD: section + troubleshooting
│   ├── GUI_PARAMETERS.md        MOD: new controls
│   └── design_doc_10.28.10.50   MOD: §6.6, §6.21, §7.1, §8.9, §9, §13, §14
└── README.md                    MOD: status, layout, tests, links
```

No changes to `control/` (the Python bridge) or `config/`. Loops remain
sclang-owned.

---

## 14. Self-review requirement (final response)

Provide:

- Files added/changed.
- For each requirement above, the code that satisfies it or a concrete concern.
- "If I had to bet on which line is most likely to fail on the human's machine,
  which is it, and why? Give a one-line verification command."
- Assumptions, max 5 bullets.
- Explicit statement that audio, MIDI, Ableton, IAC, sclang timing and the live
  GUI were not tested by the agent; 8.6 awaits human acceptance.
- The exact one-sclang + one-Python start sequence, copied from
  `docs/START_HERE.md` after your updates.

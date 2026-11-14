# Stage 9.0 — Loop FX bus + harmonic-series delay (and count-in repair)

**Status:** built 2026-11-09, **awaiting the owner's acceptance**. This stage adds
a **per-loop, post-`PitchShift` FX send bus + `\ji_harmonic_delay` SynthDef**
with two selectable flavours (**Cascade** and **Delay**), and repairs four
concrete **loop count-in** bugs. The owner's follow-up (2026-11-09) also asked
for a **fixed, tempo-locked delay time** — quarter notes, dotted, triplets and a
bar — so the `Time` control has a `TimeSync` companion.

Audio, MIDI and sclang/scsynth were **not available** in the agent environment.
The headless Qt suite was made runnable by staging the missing Linux
`libEGL`/font/X11 libraries locally; it passes. That is not the owner's macOS
audio/SC environment, so nothing here is an acceptance claim. The pure Python
harness, the headless Qt suite and the human checklist are the evidence.

Baseline: 8.8 (read-only 12-TET fretboard aid) is built and awaiting the owner's
acceptance. This stage starts the Stage 9 FX work; the reserved 8.9 row
(MIDI-triggered mutation mapping / SC-side VST hosting) is untouched.

---

## 1. What the owner asked for

> "ok looks good, the next feature i want implemented is a harmonic series delay
> fx option on the loops (some kind of effect bus feature might be useful) the
> goal is (post-pitch shift) a buffer segment plays with delay ascending up the
> harmonic series. (also i am not sure if the count in option for loop recording
> is actually working properly, so this should also be looked into) plan this
> feature and write a prompt for the agent who will implement it."

and the follow-up:

> "i would also like an ability to have fixed (ex quarter notes, dotted, etc)
> time delay"

Decoded:

1. **Post-pitch-shift** — the FX input is the loop's tuned voice, never the raw
   buffer and never a second autotune pass.
2. **Harmonic series** — integer rates 1×, 2×, 3×, 4× … (fundamental, octave,
   12th, double octave), with staggered delays.
3. **Two flavours** — **Cascade** (capture a segment, play it once with the
   harmonics entering in order) and **Delay** (a continuous harmonized buffer
   delay).
4. **Effect bus** — one private audio bus per loop and a separate SynthDef, so
   the accepted loop voice stays untouched and future loop FX can reuse it.
5. **Count-in** — investigate and fix the free-mode second-Rec bug and the
   state/generation bugs.
6. **Fixed time** — the FX time may be locked to musical note values
   (`1/4`, dotted `1/4d`, triplet `1/4t`, `1 bar`, …) instead of free seconds.

---

## 2. The rule that must never break

With `Harm = off` the loop voice is exactly the 8.8 voice: normal / reverse /
varispeed / granular → JI `PitchShift` (`mix = 1.0`) → smear → melt filter →
gain → `Out.ar(outBus, …)`. The only added send is to a **private** FX bus, and
no FX synth exists while the mode is off, so there is no audible wet and no extra
CPU. The JI autotune is never bypassed, lowered, moved before the FX, or run a
second time.

Loop FX settings are **session-only per-loop edit state**, like every other loop
edit: they are not in a performance state, not in `config/`, and not in a new
file. There is no new bridge message and no new state key. Loops remain excluded
from states.

---

## 3. Architecture

### 3.1 Four private audio buses

`startup.scd` allocates four mono **audio** buses beside the Stage 4 control
buses:

```supercollider
~ji_loop_fx1 = Bus.audio(s, 1);
~ji_loop_fx2 = Bus.audio(s, 1);
~ji_loop_fx3 = Bus.audio(s, 1);
~ji_loop_fx4 = Bus.audio(s, 1);
~ji_loop_fx_buses = [~ji_loop_fx1, ~ji_loop_fx2, ~ji_loop_fx3, ~ji_loop_fx4];
```

They are **not** hardware outputs and **not** control buses. The audio allocator
places them after the hardware I/O: a 2-in / 16-out boot returns **18–21**
(the earlier 16–19 estimate assumed no input buses were reserved). The code never
hard-codes any number — it passes `.index`. Re-evaluation re-uses existing buses
(never frees/reallocates them, so baked SynthDef indices stay valid) and
completes a partial block with a warning. `startup.scd` fails loudly if any FX bus
is missing or two share an index.

`tests/sc/test_live_rig.scd` re-uses the same buses, allocates any that are
missing (for an older startup), verifies all four are distinct, and prints:

```text
[LOOP] tracker buses: loop_freq=[11, 12, 13, 14] loop_hasfreq=[15, 16, 17, 18] loop_fx=[18, 19, 20, 21]
```

### 3.2 Four FX scratch buffers

```supercollider
~ji_loop_harm_max_seconds = 2.0;
~ji_loop_fx_buf = Array.fill(4, {
    Buffer.alloc(s, (2.0 * s.sampleRate).round.asInteger, 1)
});
```

They are FX scratch (not loop audio, not memory slots), live for the session,
and are freed/reallocated by re-evaluating `test_live_rig.scd` (the cleanup at
the top frees the old ones) or by the explicit closing lines.

### 3.3 The loop voice send

`synthdefs/ji_loop_voice.scd` gains one argument, `fxBus = 0`, and one extra
line after the accepted output:

```supercollider
Out.ar(outBus, out * gain * env);   // accepted dry output, unchanged
Out.ar(fxBus, out * gain * env);    // post-PitchShift FX send, private bus
```

The send is **post-PitchShift, post-smear, post-melt-filter, post-gain,
post-envelope**: the FX hears the tuned loop and stops with it.
`~ji_loop_edit_args` passes `\fxBus, ~ji_loop_fx_buses[i - 1].index` and never
passes the `harm_*` parameters to the loop voice.

### 3.4 Node order and lifecycle

The FX synth is created **after** the loop player
(`Synth.new(..., player, \addAfter)`), so `In.ar(fxBus)` sees the same block's
send. `~ji_loop_fx_start.(i)` creates it when `harmMode != \off` and a player
exists; `~ji_loop_fx_stop.(i)` sets `gate = 0` (the SynthDef's ASR uses
`doneAction: 2`, so it frees itself) and clears the reference;
`~ji_loop_fx_restart.(i)` stops and starts (mode/time changes);
`~ji_play_loop` starts it, `~ji_stop_loop` stops it, and `~ji_stop_all` reaches
it through `~ji_stop_loop`. A stopped/freed loop never leaves an FX synth
reading a silent bus forever. At most four FX synths exist, each mono, each
reading one bus.

Parameter changes (`mix`, `steps`, `falloff`, `spacing`, `auto`) `set` the live
synth. `harm_mode`, `harm_time` and `harm_time_sync` restart it (the
capture/buffer state is reset). With the mode off there is no synth and no CPU.

---

## 4. The DSP (`synthdefs/ji_harmonic_delay.scd`)

### 4.1 Common maths

* **Rates:** `r_k = k` for `k = 1 … steps` — exact integer harmonics, not 12-TET
  notes.
* **Amplitudes:** `a_k = 1 / k ^ (0.5 + 1.5 * falloff)`, multiplied by `mix`.
  At `falloff = 0` the roll-off is `1/√k`; at `1` it is `1/k²`.
* **Time:** `0.05…2.0 s` (the FX buffer), default `0.5`.
* **Steps:** `1…8`, default `4`.
* **Spacing:** `0…1 s`, default `0.08` (Cascade only).
* **Auto:** `0/1`, default `1` (Cascade only).
* **Mix:** wet return `0…1`, default `0.35`; the dry loop is always upstream.
* `mix` and the gate are smoothed; the **per-tap read rate is never lagged** (a
  `Lag` on `k` would glide the pitch and smear the integer ratios).
* **Build fix (2026-11-09):** the mode selector is an **audio-rate arithmetic
  blend** `(cascade * (1 - mode)) + (delayed * mode)`, not `Select.kr`.
  `Select.kr` made the wet return control rate, and `Out.ar` then failed with
  “Out input … is not audio rate”. The first owner boot exposed this; the
  SynthDef now builds.

### 4.2 Cascade (mode 0)

While idle, the post-shift input is written at 1 frame/sample. On a **Fire**
(a rising `trig` counter) or the **Auto** clock the write freezes, the write head
is snapshotted and up to eight players read the **same captured segment** at
`k` frames/sample:

* player `k` starts `(k - 1) * spacing` seconds after the freeze;
* it plays for `time / k` seconds (it consumes exactly `time` seconds of source);
* it has amplitude `a_k` and a short attack/release envelope.

The buffer stays frozen for
`max(time, (steps - 1) * spacing + time / steps)` seconds; writing resumes
immediately after. Auto fires first at `time` seconds (so the first segment is
full), then every `time + cascade duration + 0.1 s`, and is suppressed while a
**manual** cascade runs. A **Fire during a cascade restarts it from the same
frozen segment** (the write head is still frozen, so the capture does not move);
this is the documented choice. A Fire before `time` seconds of audio exists
plays a partial, silence-padded segment. `spacing = 0` is allowed and puts all
harmonics in at once (a harmonic chord).

### 4.3 Delay (mode 1)

The write never stops. Tap `k` starts `time` seconds behind the initial write
head and reads at `k` frames/sample, wrapping at the buffer end. The write head
moves at 1× while a tap moves at `k×`, so the delay window **drifts**: that is
the intended **varispeed harmonizer** character, not a fixed-time echo. All taps
run while the mode is on; `spacing`, `auto` and Fire are inert (the GUI disables
them and sclang logs one ignore line if a Fire arrives). `steps = 1` is a plain
delay; 2 adds the octave, 3 the 12th, and so on.

### 4.4 Fixed musical time (owner follow-up)

`harm_time_sync` is a closed vocabulary:

```text
free, 1/32, 1/32d, 1/32t, 1/16, 1/16d, 1/16t, 1/8, 1/8d, 1/8t,
1/4, 1/4d, 1/4t, 1/2, 1/2d, 1/2t, 1/1, 1/1d, 1/1t, 1 bar
```

`free` uses `harm_time` seconds. Every other value is a quarter-note count at
the bridge tempo: straight notes use their base value, `d` = dotted (×1.5),
`t` = triplet (×2/3), and `1 bar` is the current meter's bar (exact: a 5/8 bar
is 2.5 quarter beats). The effective time is

```text
seconds = beats * 60 / bpm          (bpm clamped to 20..300)
seconds = clamp(seconds, 0.05, 2.0)  (the FX buffer)
```

A BPM change restarts every active fixed-time FX and refreshes its
`/ji/loop/edit` echo; free-time FX are untouched. `harm_time` stays the free
value even while a division is selected, so returning to `free` restores it.
The GUI shows the derived seconds read-only while a division is selected.

> Note: the count-in bar rule is **ceil** (5/8 → 3) so the take never starts
> early; the fixed FX delay uses the **exact** meter bar (5/8 → 2.5 beats), then
> clamps to 2 s. The two rules are intentionally different and both documented.

### 4.5 Safety and CPU

`time` is clamped to the buffer duration, `steps` to 1…8, `spacing` to 0…1 s;
`BufRd` uses `loop = 1`, so every read wraps inside the buffer. The ceiling is
four FX synths, each one `BufWr` + up to eight `BufRd`/envelopes, only while the
mode is on — no FFT, no per-harmonic `PitchShift`, no second grain cloud.

---

## 5. Count-in repair (Part A)

The owner suspected the count-in. Four concrete issues were confirmed in the
8.8 tree and fixed; the accepted options/semantics are unchanged.

### A1. Free-mode second Rec did not skip the count-in (real bug)

`/ji/loop/record_length` (the free-mode Rec path) called
`~ji_record_loop_action` unconditionally, unlike `/ji/loop/record`'s
second-Rec branch. Pressing Rec twice during a free-mode count restarted the
count, and `previous` was stored as `\counting`, so a later Stop Rec restored
`\counting` (stuck).

**Fix:** the handler now checks the state **before** starting a new count. If
`\counting`, it logs `[LOOP i] count-in skipped (second Rec)`, cancels with
restore = true, remembers the new free length (beat mode keeps its beat length),
and calls `~ji_record_loop_now.()` — in **both** modes. The free path now
behaves like the beat path.

### A2. `~ji_count_in_start` captured a stale/corrupt previous state

It sampled `previous` before cancelling a pending count-in, so `previous` could
be `\counting`. **Fix:** resolve any pending count-in with restore = true
**before** sampling, and never store `\counting`. `~ji_cancel_count_in` also has
a belt-and-braces guard: if the stored previous state is `\counting` (or nil),
it restores `\recorded` when the buffer is valid, otherwise `\empty`, and logs
one line.

### A3. Intermediate count-in ticks were not generation-checked

Only the final callback checked the generation; old ticks could send the old
countdown into a new count. **Fix:** every tick closure captures `generation`
and checks it (and `\counting`) before calling `~ji_loop_count_send`. A
cancelled count is silent.

### A4. Bar rounding was inconsistent for odd 1/8 meters

`~ji_loop_count_in_quarter_beats` used `round`; the Python mirror used `round`.
For 5/8 (2.5) and 7/8 (3.5) that was ambiguous. **Fix:** both use **ceil to the
next whole quarter beat**: 4/4 → 4, 3/4 → 3, 6/8 → 3, 5/8 → 3, 7/8 → 4. The
common meters stay exact, and a take never starts early.

### A5. What did not change

`off` / `1 beat` / `1 bar` / `2 bars`; the tempo-clock quarter-beat count
starting at `now.ceil`; the `/ji/loop/count` tick shape; free mode counting with
the transport stopped; beat mode requiring the transport and cancelling on a
transport stop; `Stop Rec`/`Stop`/`Clear`/`Stop All`/`Dub` cancelling and
restoring; no recorder before the target; auto-play suppressed on a cancelled
count.

---

## 6. Controls, OSC and the Python mirrors

### 6.1 Loop-edit parameters

| Wire name | GUI label | Type / range | Default | Meaning |
|---|---|---|---|---|
| `harm_mode` | `Harm` | `off`, `cascade`, `delay` | `off` | FX off / one-shot cascade / continuous harmonized delay |
| `harm_mix` | `Mix` | 0–1 | 0.35 | Wet return level (dry loop upstream) |
| `harm_time` | `Time` | 0.05–2.0 s | 0.50 | Free segment/window time |
| `harm_time_sync` | (next to `Time`) | `free`, `1/32`…`1 bar` | `free` | Fixed tempo-locked time (dotted `d`, triplet `t`) |
| `harm_steps` | `Steps` | 1–8 (int) | 4 | Harmonics 1×…N× |
| `harm_spacing` | `Space` | 0.0–1.0 s | 0.08 | Cascade entry spacing (inert in Delay) |
| `harm_falloff` | `Fall` | 0–1 | 0.50 | Amplitude exponent `0.5 + 1.5 × falloff` |
| `harm_auto` | `Auto` | 0/1 | 1 | Cascade auto-fire (inert in Delay) |

`LOOP_EDIT_PARAMS` now has **37 editable params**; the strict `/ji/loop/edit`
payload has **39 values** because it also carries `index` and
`effective_pitch`. (The stage prompt said "31 → 38" and "38 params"; the owner's
fixed-time follow-up adds `harm_time_sync`, so the payload is **39** and the
editable count is **37** — `index`/`effective_pitch` are wire-only and never
editable.)

### 6.2 `/ji/loop/edit` — 39 values

After `grain_breath_rate` (the 31st value) the eight values are appended in this
exact order:

```text
harm_mode (string), harm_mix, harm_time, harm_time_sync (string),
harm_steps (int), harm_spacing, harm_falloff, harm_auto (0/1 int)
```

`parse_loop_edit` rejects any length other than 39, an unknown `harm_mode` or
`harm_time_sync`, and clamps the numbers. `LoopEdit.status_line()` appends e.g.
`harm cascade · 4× · 0.50 s · mix 35 %` when the mode is not off (and the
division, e.g. `1/4d`, when fixed). `LoopEditMemory`/the sclang memory slot copy
the edit dict, so the new keys travel with a saved loop memory slot; the
performance state still excludes loops.

### 6.3 `/ji/loop/fx_fire`

| Address | Args | Direction |
|---|---|---|
| `/ji/loop/fx_fire` | `[int index]` | UI → sclang (control surface) |

sclang validates `index` 1–4; if `harm_mode == cascade` and an FX synth exists,
it increments that loop's trigger counter, `set(\trig, counter)` and logs
`[LOOP i] harm fire (n)`; otherwise one ignore line. No reply. The Python
controller sends it to the **SC client** (like `loop_record`), never the bridge.

### 6.4 `/ji/loop/edit_set`

`~ji_loop_set_edit` accepts the eight `harm_*` names. `harm_mode` value-compares
the three wire strings (`indexOfEqual`, not `indexOf`), maps to
`\off`/`\cascade`/`\delay`, and rejects anything else; `harm_time_sync` is
value-compared against the closed list; numbers clamp; `harm_steps` is an
integer; `harm_auto` is 0/1. A change sends a fresh `/ji/loop/edit` and one
`[LOOP i] edit …` line. `harm_mode`/`harm_time`/`harm_time_sync` restart the live
FX; the other parameters `set` it; off frees it. The control-surface version is
`9.0.0`.

### 6.5 No bridge/state change

No new `/ji/bridge` message, no new state key, no `config/` file. The FX is a
loop edit over the existing UI→sclang OSC surface.

---

## 7. GUI

One row was added after the Morph row:

```text
Harm [off v]  Mix [35 %]  Time [0.50 s] [free v]  Steps [4]  Space [80 ms]  Fall [50 %]  Auto [x]  [Fire]
```

* `Harm` maps display labels `off`/`Cascade`/`Delay` to wire `off`/`cascade`/
  `delay`.
* `Time` is the free seconds spin (0.05–2.00, 2 decimals + ` s`); the adjacent
  combo is the fixed-time division. When a division is selected the spin is
  read-only and shows the tempo-derived seconds.
* `Mix`/`Fall` are percent spins (wire value/100). `Space` is milliseconds
  (wire seconds, matching the grain-size convention). `Steps` is a 1–8 spin.
  `Auto` is a checkbox; `Fire` is a button.
* `Mix`/`Time`/`TimeSync`/`Steps`/`Fall` are enabled in both active modes;
  `Space`/`Auto`/`Fire` only in Cascade; all FX controls except `Harm` are
  disabled when off. `Fire` is enabled only in Cascade **and** while the
  selected loop is playing.
* Every incoming `/ji/loop/edit` syncs the row signal-blocked (`_loop_edit_sync`)
  and never echoes; every edit sends `/ji/loop/edit_set` immediately (loop edits
  are not beat-queued). The row is rebuilt when the selected loop changes.
* **Robust mode:** before a Harm parameter edit (and before `Fire`) the GUI
  re-sends the visible `harm_mode`, so a lost/ignored mode message cannot leave
  the FX off while the row shows Cascade/Delay. sclang ignores a repeated mode
  (no FX restart) and, if an active mode has no live FX synth, a parameter edit
  recreates it.
* **Focus:** when `TimeSync` is fixed, the Time spin is **read-only and stays
  enabled** (step buttons hidden) rather than disabled; disabling it used to
  move keyboard focus to the next control (`Default len`), which looked like the
  edit had jumped to the loop length.
* The status line moved to the Harm row and shows the `harm …` summary.
* The default window is `1280×1198`; the Qt height bound is `1230`; the scroll
  area remains the small-screen fallback. No new group/tab/dialog/window.

The GUI **ran headless** in this container after staging the missing Linux
`libEGL`/font/X11 libraries; every Qt check passed. This verifies the widgets,
signals, sends, echo sync and height, not the owner's live GUI/audio.

---

## 8. Tests and evidence

Run from `ji-live/`:

```bash
~/.ji-tools/uv run --with python-osc python tests/test_ui.py --pure
~/.ji-tools/uv run --with python-osc python tests/test_startup.py
# when PyQt6 can run:
QT_QPA_PLATFORM=offscreen ~/.ji-tools/uv run --with python-osc --with PyQt6 python tests/test_ui.py --qt
```

* **Pure Python** covers: 37 editable params; exact harm defaults/ranges;
  `build_set_loop_edit` for every new name (bad modes/syncs rejected, numbers
  clamped, `harm_steps` int); the strict 39-value parser (rejects 31/38/40,
  bad modes/syncs, clamps); `LoopEdit` defaults and `status_line`; the fixed-time
  maths; `count_in_beats` ceil cases (4/4, 3/4, 6/8, 5/8, 7/8, off/1 beat);
  `build_loop_fx_fire`/`OSCController.loop_fx_fire` (SC client, exact message);
  and the store applying a 39-value edit.
* **Qt** (when available) covers the row defaults, mode sends, every control
  send, Fire enabled only in Cascade while playing, the SC fake (not the bridge
  fake), an incoming 39-value sync with no feedback, height ≤ 1230 and Status
  present. It ran headless here (staged Linux Qt libraries) and passed.
* **Human-run diagnostic:** `tests/sc/debug_stage9.scd` checks the direct mode
  edit, the exact `/ji/loop/edit` echo size/mode, the core recorder, and the FX
  node after the player. On the owner's 2026-11-09 run it printed
  `echo size=40 args=39 harmMode=delay` and
  `harm delay started (... in 21 -> out 3 ...)`, proving both layers.
* **sclang** (`tests/sc/test_loop_edit.scd`, human-run) checks the harm defaults,
  clamps, `\fxBus` args, FX buses/buffers and the ceil count-in values, and
  prints manual steps for free-mode second Rec, Stop Rec during a count, 5/8/7/8,
  Cascade/Delay/fixed time and post-PitchShift behaviour. `test_live_rig.scd`
  checks/re-uses the FX buses and buffers, requires `\ji_harmonic_delay`, and
  prints `loop_fx=…`.
* **Not tested by the agent:** audio, MIDI, sclang/scsynth and the live macOS
  GUI (the Qt widgets were tested headless on Linux).
  A1/A2 are covered by the manual second-Rec/Stop Rec steps, not by an automated
  sclang state test. The FX **start/stop node lifecycle and node order** are
  checked by the manual checklist (and the `[LOOP i] harm … started` /
  `s.plotTree` lines); `test_live_rig.scd` verifies the buses/buffers/SynthDef
  and the args, but does not instantiate an FX synth at rig load (that would
  make sound during setup).

Regression: the other pure harnesses (`test_fretboard.py`, `test_rhythm.py`,
`test_breath.py`, `test_bass.py`, `test_state.py`, `test_arp.py`, `test_lead.py`,
`test_drone.py`, `test_groove.py`, `test_quantize.py`, `test_home.py`,
`test_glide.py`, `test_mutation.py`, `test_mutation_controls.py`,
`test_mpe.py`) are unchanged and were run.

---

## 9. Human acceptance checklist (the owner is the authority)

1. **FX off:** with `Harm = off`, every loop sounds exactly as in 8.8 (normal,
   reverse, varispeed, grain, morph, breath); no wet tail, no clicks, no CPU
   hiss.
2. **Cascade:** play loop 1, choose `Cascade`, press `Fire`; hear the captured
   segment, then the octave, 12th, 16th … entering in order. `Steps` 1 = only the
   fundamental; 8 = the full stack. `Time`, `Space`, `Fall`, `Mix` change the
   gesture as labelled. `Auto` on fires periodically; off requires `Fire`.
3. **Delay:** choose `Delay`; the loop is continuously doubled/filled by the
   harmonic stack, no trigger. `Mix` 0 is dry, `Steps` 1 is a plain delay,
   higher steps add the ladder. `Space`/`Auto`/`Fire` are disabled.
4. **Fixed time:** choose `TimeSync` `1/4`, `1/4d`, `1/4t`, `1/8`, `1 bar`; the
   seconds spin shows the derived value read-only. Change BPM: a playing fixed
   FX retimes; free time does not. Values beyond 2 s clamp to the buffer.
5. **Post-pitch-shift:** mutate the JI set while the FX is on; the FX remains
   tuned to the loop's shifted pitch and the harmonic ratios are integer
   multiples of it. No second autotune pass.
6. **Per-loop:** loop 1 CASCADE and loop 2 DELAY run independently; stopping
   loop 1 leaves loop 2's FX alive; stopping a loop frees its FX (no stuck synth
   in `s.plotTree`).
7. **Count-in:** free mode, `1 bar`, transport stopped; press Rec; watch
   `counting` count down and the take start one bar later. Press Rec again
   during the count: it must skip and record immediately (both modes). `Stop Rec`
   during the count restores the previous state (empty or recorded), no stuck
   "counting". Repeat in 3/4, 6/8, 5/8, 7/8; a bar is rounded up to a whole
   quarter beat. Beat mode still needs Play and a transport stop cancels a
   count-in.
8. **No state change:** `Harm` settings are session-only; saving/loading a
   performance state does not save or restore them; a GUI/sclang restart returns
   to `off`.
9. **CPU/latency:** four loops with Cascade/Delay on stay usable; no dropouts;
   loop start/stop remains clean.
10. **Window:** the Harm row fits at the default size; Status is reachable; the
    scroll area handles a small screen; no painting glitches.
11. Record the result. **9.0 is not complete until the owner confirms the FX is
    musically useful and the count-in is trustworthy.**

---

## 10. Files

```text
ji-live/
├── synthdefs/ji_harmonic_delay.scd        NEW: Cascade + continuous Delay
├── synthdefs/ji_loop_voice.scd            MOD: fxBus post-PitchShift send
├── startup.scd                            MOD: 4 private FX audio buses
├── tests/sc/test_live_rig.scd             MOD: FX buses/buffers/args/lifecycle,
│                                              count-in ceil, 39-value payload
├── tests/sc/test_loop_edit.scd            MOD: Harm checks + manual steps
├── ui/control_surface.scd                 MOD: harm handlers/fire, count-in fixes,
│                                              version 9.0.0
├── ui/osc_control.py                      MOD: constants, LoopEdit fields,
│                                              parser/builders, controller
├── ui/ji_ui.py                            MOD: Harm row, Fire, status, height
├── tests/test_ui.py                       MOD: pure + Qt
├── docs/README_stage9_0.md                NEW: this file
├── docs/START_HERE.md                     MOD: controls + troubleshooting
├── docs/GUI_PARAMETERS.md                 MOD: Harm row + count-in rounding
├── docs/README_live_rig.md                MOD: FX bus map
├── docs/design_doc_10.28.10.50            MOD: §4.2/§5.2/§6.21/§6.23/§7.1/
│                                              §8.9/§9/§13/§14
└── README.md                              MOD: status, layout, tests, links
```

No changes to `control/`, `config/`, any bridge OSC address, or any
performance-state key.

---

## 11. Known risks / honest caveats

* **Node order** — an FX synth that reads zero is almost always a wrong bus
  index or a synth created before the player. Check `s.plotTree` and the
  `[LOOP i] harm … started (fx node …, in … -> out …)` line.
* **Frozen-buffer race** — the write freezes on the same control block as the
  capture; a Fire is latched at the play head. A very short `time` can still
  play a partial segment if Fire arrives before the buffer has `time` seconds.
* **Auto/Fire** — a manual Fire during a cascade restarts the same frozen
  segment by design; Auto is suppressed during a manual cascade and its period
  is longer than the cascade.
* **Fixed time vs buffer** — `1 bar` at slow tempos clamps to 2 s; this is
  documented rather than silently extending the buffer.
* **No listening pass** — the owner is the authority; this stage awaits
  acceptance.

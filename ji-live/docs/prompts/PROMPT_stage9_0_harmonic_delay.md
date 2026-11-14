# Stage 9.0 Agent Prompt — Loop FX bus + harmonic-series delay (and count-in fix)

You are implementing **Stage 9.0** of the JI live-performance system. Stage 8.8
(the read-only 12-TET fretboard aid) is built and awaiting the owner's
acceptance. This stage starts the **Stage 9 FX work** with two deliverables:

1. a **harmonic-series delay FX** on the loop voices, inserted **after** the
   loop's JI `PitchShift`, built as a **per-loop effect bus + separate
   `\ji_harmonic_delay` SynthDef**, with two selectable flavours:
   **Cascade** (a captured segment plays once, harmonics entering in order) and
   **Delay** (a continuous harmonized buffer delay); and
2. a **count-in repair**: the owner suspects the loop recording count-in does
   not work properly. This prompt contains a concrete audit; fix the bugs,
   prove them, and do not change the accepted count-in behaviour otherwise.

The owner is the authority on the sound and on whether the count-in now works.
The agent environment may have no audio, no sclang/scsynth and no PyQt6; the
verification is the pure Python harness plus honest reporting of what was not
testable.

---

## 0. What the owner said (verbatim, 2026-11-09)

> "ok looks good, the next feature i want implemented is a harmonic series delay
> fx option on the loops (some kind of effect bus feature might be useful) the
> goal is (post-pitch shift) a buffer segment plays with delay ascending up the
> harmonic series. (also i am not sure if the count in option for loop recording
> is actually working properly, so this should also be looked into) plan this
> feature and write a prompt for the agent who will implement it."

Decoded, with the owner's follow-up answers:

1. **Post-pitch-shift**: the FX input is the loop voice's post-autotune signal
   (after `PitchShift`, smear and melt filter), never the raw recorded buffer
   and never a second pass through the JI autotune.
2. **Harmonic series delay**: a buffer segment is replayed at integer multiples
   of its rate (1×, 2×, 3×, 4× … = fundamental, octave, 12th, double octave …)
   with staggered delays. These are exact integer harmonics, not 12-TET notes
   and not necessarily members of the current JI set.
3. **Two selectable flavours** (owner choice):
   - **Cascade** — capture a segment of the post-shift loop, then play it once
     as an ascending harmonic cascade: fundamental first, then octave, 12th,
     16th … each entering after a short delay. A `Fire` button and an optional
     auto-fire retrigger it. This is the "a buffer segment plays with delay
     ascending up the harmonic series" gesture.
   - **Delay** — a continuous harmonized buffer delay: the loop's delayed audio
     is continuously mixed with copies playing at 1×, 2×, 3× … No capture
     freeze and no trigger; it runs while the mode is on.
4. **Effect bus**: a **per-loop FX send bus** and a separate FX SynthDef, so the
   loop voice stays the accepted autotune voice and future loop FX can reuse
   the bus (the owner's "some kind of effect bus feature might be useful").
5. **Count-in**: investigate and fix. The owner is not sure it works; the audit
   below found a free-mode second-Rec bug and two state/generation bugs.

Stage numbering: this starts **Stage 9.0**. The optional **8.9** row
(MIDI-triggered mutation mapping / SC-side VST hosting) stays reserved and is
**not** touched.

---

## 1. Read these files first

```text
docs/README_stage8_8.md                  the current baseline + stage format
docs/README_stage8_7.md                  breath/bass-hit baseline, same style
docs/README_stage8_6.md                   loop editing, count-in, auto-play, bus notes
docs/START_HERE.md                       controls + troubleshooting (update it)
docs/GUI_PARAMETERS.md                   every control, one line each (update it)
docs/README_live_rig.md                  SC bus/audio map (update the FX buses)
docs/design_doc_10.28.10.50              §4.2, §6.21, §6.22, §7.1, §8.9, §9,
                                         §13, §14
synthdefs/ji_loop_voice.scd              the voice the FX send is added to
synthdefs/ji_buf_copy.scd                the project's BufRd/BufWr example
startup.scd                              control/audio bus allocation (5a-5b)
tests/sc/test_live_rig.scd               loop voice, edit args, count-in, play/stop
tests/sc/test_loop_edit.scd              edit/args/count-in human checks
ui/control_surface.scd                   count-in, loop edit handlers, lifecycle
ui/osc_control.py                        LOOP_EDIT_PARAMS, LoopEdit, parse/build
ui/ji_ui.py                              Loop edit / Grain / Morph rows, status
tests/test_ui.py                         pure + optional Qt harness
tests/test_startup.py                    launcher/doc checks
README.md                                status, layout, tests, links
docs/prompts/PROMPT_stage8_8_fretboard.md  the prompt-format precedent
```

Run Python from `ji-live/`. The environment has a uv-managed Python at
`~/.ji-tools/uv` even when `uv` is not on `PATH`:

```bash
~/.ji-tools/uv run --with python-osc python tests/test_ui.py --pure
```

sclang/scsynth and a real audio interface are **not** required for the pure
harness; do not claim a listening pass.

---

## 2. What must not change

- **The accepted dry loop path.** With `harm_mode = "off"` the loop voice must
  produce exactly the 8.8 output: normal/reverse/varispeed/granular → JI
  `PitchShift` (`mix = 1.0`) → smear → melt filter → gain → `Out.ar(outBus, …)`.
  FX off creates **no FX synth** and adds no audible wet. The one new
  `Out.ar(fxBus, …)` send is to a private bus and must not change the output.
- **The JI autotune.** The FX is **post-PitchShift**. Do not move the FX before
  `PitchShift`, do not bypass it, do not lower `mix`, and do not re-run the FX
  output through the autotune.
- **No bridge, mutation, rhythm, breath, quantizer, state or config change.**
  Loop FX settings are part of the existing per-loop edit state (session-only,
  like loop edits); they are **not** saved in a performance state, in
  `config/`, or in a new file. No new bridge message.
- **No new state keys.** The performance-state format and `version` stay as
  they are. Loops remain excluded from states.
- **One window, no new tab/dialog.** The FX controls live in the existing loop
  edit area; the scroll area remains the small-screen net.
- **The count-in's accepted semantics** (the choices, the meter bar, the
  free/beat gating, the cancel actions) stay; only the bugs and the bar
  rounding are fixed. Do not turn it into a different feature.
- **Existing harnesses pass unmodified** except the intended additions to
  `tests/test_ui.py`, `tests/test_loop_edit.scd`, `tests/sc/test_live_rig.scd`
  and `tests/test_startup.py` if doc/layout assertions need it. In particular
  `tests/test_mutation.py` is untouched.
- **No `control/` changes** are expected: the loop edit surface is UI→sclang.
  If you believe a bridge change is unavoidable, stop and explain why in the
  stage README instead of adding one quietly.

---

## 3. Part A — the count-in audit (do this first)

The owner suspects the count-in. Reading the current code found these concrete
problems. Confirm each in the live code before changing it; the line references
are from the 8.8 tree.

### A1. Free-mode "second Rec" does not skip the count-in (real bug)

The GUI's free-mode Rec sends `/ji/loop/record_length` (`ui/osc_control.py`
`build_loop_record`), handled at `ui/control_surface.scd`
`OSCdef(\ji_ui_loop_record_length, …)`. That handler calls
`~ji_record_loop_action.(index)` unconditionally, which starts the count-in
again. The `/ji/loop/record` handler (beat mode) has the intended branch:

```supercollider
if (~ji_loop_states[index - 1] == \counting) {
    // A second Rec skips the count-in and records now.
    ~ji_cancel_count_in.(index, true);
    ~ji_record_loop_now.(index);
}
```

The free-mode path lacks it. Consequences:

* pressing Rec twice during a free-mode count-in **restarts the count** instead
  of recording immediately, contradicting `docs/README_stage8_6.md` §4.4b and
  the GUI tooltip ("a second Rec skips the count-in");
* the re-entry reads `previous = ~ji_loop_states[i - 1]`, which is `\counting`,
  stores it in `~ji_loop_count_prev`, and a later Stop Rec restores `\counting`
  — the loop is then stuck in "counting" until another action clears it.

**Required fix.** In `record_length`, after parsing `seconds` and `mode`, check
the state **before** starting a new count-in. If `\counting`, log
`[LOOP i] count-in skipped (second Rec)`, cancel with restore = true, set the
free-mode custom length to the newly parsed `seconds` (beat mode keeps its beat
length), and call `~ji_record_loop_now.(i)`. Do this in both the free and beat
branches (the beat branch should behave exactly like `/ji/loop/record`). The
cancel must restore the last **non-counting** state.

### A2. `~ji_count_in_start` captures a stale/corrupt previous state

`~ji_count_in_start` currently does this order:

```supercollider
var previous = ~ji_loop_states[i - 1];   // may be \counting!
~ji_cancel_loop_schedule.(i);
~ji_cancel_count_in.(i, false);          // restore = false, state unchanged
...
~ji_loop_count_prev[i - 1] = previous;
```

If a count-in is already pending, `previous` is `\counting`, so a later cancel
restores `\counting` and the loop never leaves it. **Required:** cancel/resolve
any pending count-in with **restore = true** before sampling `previous`, or
refuse to start a second count-in when already `\counting`; never store
`\counting` as the previous state. Add a belt-and-braces guard in
`~ji_cancel_count_in`: if the stored previous state is `\counting`, restore to
`\recorded` when the buffer is valid, otherwise `\empty` (and log one line).

### A3. Intermediate count-in ticks are not generation-checked

`~ji_count_in_start` schedules one tick per counted quarter with
`~ji_tempo_clock.schedAbs(target - left, …)`. Only the **final** callback checks
`~ji_loop_count_gen[i - 1] == generation`; the intermediate ticks check only
`state == \counting`. After a cancel and a new count-in for the same loop, old
ticks can still fire and send the **old** countdown into the new count. Capture
`generation` in every tick closure and check it before calling
`~ji_loop_count_send`. A cancelled count must be silent.

### A4. Bar length rounding is inconsistent for odd 1/8 meters

`~ji_loop_count_in_quarter_beats` computes
`beatsPerBar = barBeats.max(1.0).round.asInteger`; the Python mirror
`LoopEdit.count_in_beats` uses `round`. For a 5/8 bar (2.5 quarter beats) and a
7/8 bar (3.5) this is ambiguous and can start the take a half-beat early or
late. **Required:** choose one documented rule and mirror it exactly. Use
**ceil to the next whole quarter beat** (`5/8 → 3`, `7/8 → 4`, `4/4 → 4`,
`3/4 → 3`, `6/8 → 3`) so the count never starts early; the common meters stay
exact. Update `~ji_loop_count_in_quarter_beats`, `LoopEdit.count_in_beats`
(use `math.ceil`), the docs, and both test harnesses.

### A5. Keep the accepted count-in behaviour

Do not change: `off`/`1 beat`/`1 bar`/`2 bars`; the tempo-clock quarter-beat
count starting at `now.ceil`; the `/ji/loop/count` tick shape; free mode
counting while the transport is stopped; beat mode requiring the transport and
cancelling on a transport stop; `Stop Rec`/`Stop`/`Clear`/`Stop All`/`Dub`
cancelling and restoring; no recorder before the target; auto-play suppressed
on a cancelled count. Only A1–A4 and their tests.

### A6. Add evidence

Extend `tests/sc/test_loop_edit.scd`'s count-in block with the 5/8 and 7/8
values and a manual step for the second-Rec skip in **free** mode (the existing
manual checklist only proves it implicitly). Add a state-only sclang check if
you can factor the skip decision into a small helper; otherwise say in the
stage README that A1/A2 are covered by the manual step, not by an automated
sclang test. In `tests/test_ui.py --pure`, update `count_in_beats` expectations
and add the ceil cases.

---

## 4. Part B — the loop FX bus architecture

### 4.1 Four private audio buses

Add one mono **private audio bus per loop** (not a hardware output bus, not a
control bus):

```supercollider
~ji_loop_fx1 = Bus.audio(s, 1);
~ji_loop_fx2 = Bus.audio(s, 1);
~ji_loop_fx3 = Bus.audio(s, 1);
~ji_loop_fx4 = Bus.audio(s, 1);
~ji_loop_fx_buses = [~ji_loop_fx1, ~ji_loop_fx2, ~ji_loop_fx3, ~ji_loop_fx4];
```

Allocate/re-use them in `startup.scd`'s `waitForBoot` block beside the Stage 4
control buses, with the same re-use rule (never free/reallocate an existing
bus; report the indices in the allocated-buses line). The **audio** bus
allocator starts after `numOutputBusChannels` (16), so they should be 16–19; do
not hard-code that in SynthDefs — pass `.index`. Verify every bus exists before
the rig installs; a missing FX bus is a startup error, not a silent -1.

`tests/sc/test_live_rig.scd` must check/re-use the same buses (allocate them if
the file is evaluated after a fresh startup that lacks them) and print
`loop_fx=…` beside the tracker buses. Update the bus map in
`docs/README_live_rig.md` and the design doc §4.2/§9.

### 4.2 Four FX buffers

Add one mono buffer per loop, `LOOP_HARM_MAX_SECONDS = 2.0` seconds at the
server sample rate, allocated in `tests/sc/test_live_rig.scd` beside the four
loop buffers:

```supercollider
~ji_loop_fx_buf = Array.fill(4, {
    Buffer.alloc(s, (2.0 * s.sampleRate).round.asInteger, 1)
});
```

They are FX scratch buffers, not loop audio and not memory slots. They stay
allocated for the session and are freed by the closing lines at the bottom of
`test_live_rig.scd` / `~ji_stop_all` like the loop buffers. Re-evaluating the
rig frees and reallocates them (or reuses them if you make that explicit).

### 4.3 The loop voice send

`synthdefs/ji_loop_voice.scd` gains **one** argument:

```supercollider
fxBus = 0
```

(the actual value is always a valid private bus; the FX synth exists only while
the FX is on). After the final post-autotune/post-filter `out` and its `gain` /
`env` are formed, add:

```supercollider
Out.ar(outBus, out * gain * env);   // accepted dry output, unchanged
Out.ar(fxBus, out * gain * env);    // post-PitchShift FX send, private bus
```

The send is **post-PitchShift, post-smear, post-melt-filter, post-gain,
post-envelope**: the FX hears the tuned loop and stops with it. At defaults
(`harm_mode = off`) no FX synth reads the bus, so the output is the accepted
voice. Do **not** apply the FX inside `ji_loop_voice`; the whole point is the
separate bus.

`~ji_loop_edit_args.(i)` in `tests/sc/test_live_rig.scd` passes
`\fxBus, ~ji_loop_fx_buses[i - 1].index`. It does **not** pass the `harm_*`
parameters to the loop voice; those go to the FX synth only.

### 4.4 The FX SynthDef and its lifecycle

New `synthdefs/ji_harmonic_delay.scd`:

```supercollider
SynthDef(\ji_harmonic_delay, { |inBus = 0, outBus = 2, bufnum = 0,
    gate = 1, mode = 0, mix = 0.35, time = 0.5, steps = 4,
    spacing = 0.08, falloff = 0.5, auto = 1, trig = 0|
    ...
}).add;
```

* It reads `In.ar(inBus, 1)` (the loop's post-autotune send).
* It writes **wet only** to `outBus` with `Out.ar(outBus, wet * mix * gateEnv)`;
  the dry loop is already on that bus in the loop voice. Do not duplicate the
  dry signal in the FX synth.
* `gate` uses a short ASR (`Env.asr(0.01, 1.0, 0.03)`, `doneAction: 2`) so the
  FX stops with the loop and frees itself.
* One instance per loop, created **after** the loop player with
  `Synth.after(player, \ji_harmonic_delay, args)` so it reads the same block's
  send. Store it in `~ji_loop_fx_synths[i - 1]`.
* `~ji_loop_fx_start.(i)` creates it when `harmMode != \off` and a player
  exists; `~ji_loop_fx_stop.(i)` sets `gate = 0` and clears the reference (the
  synth frees itself); `~ji_loop_fx_restart.(i)` stops and starts (used for a
  mode or time change).
* `~ji_play_loop` starts the FX after creating the player; `~ji_stop_loop`
  stops it; a loop's own `gate` release / player free must not leave an FX
  synth reading a silent bus forever.
* Parameter changes (`mix`, `steps`, `falloff`, `spacing`, `auto`) `set` the
  live FX synth. A `harm_mode` change or a `harm_time` change restarts it (the
  buffer/capture state is reset). `harm_steps` may `set` without restart if
  your implementation supports it; otherwise restart.
* No FX synth exists when the mode is `off`; no CPU is spent.
* Four FX synths maximum; each is mono and reads one bus.

---

## 5. Part C — the two DSP modes

The exact algorithm is yours, but the behaviour and the maths below are the
contract. Both modes use the same per-loop 2 s buffer, the same `steps`,
`time`, `falloff` and `mix`; they share the harmonic amplitude law.

### 5.1 Common maths

* **Harmonic rates:** `r_k = k` for `k = 1 … steps` (all integer multiples:
  fundamental, octave, 12th, 16th, 20th, …). `steps` is 1–8 (default 4).
* **Fundamental amplitude law:** `a_k = 1 / k ^ (0.5 + 1.5 * falloff)`, with
  `falloff` 0–1 (default 0.5). Multiply by `mix` (0–1, default 0.35).
  At `falloff = 0` the roll-off is gentle (1/√k); at 1 it is 1/k².
  Document this exact law.
* **Time:** `time` 0.05–2.0 s (default 0.5). In Cascade it is the captured
  segment length; in Delay it is the base delay/window.
* **Steps:** 1–8; `steps = 1` is the fundamental only.
* **Spacing:** `spacing` 0.0–1.0 s (default 0.08); Cascade only.
* **Auto:** 0/1, default 1; Cascade only.
* **Mix:** wet return gain 0–1; the dry loop is always present upstream.
* Use `Lag.kr` / short envelopes on `mix` and the mode switch so enabling,
  disabling or changing parameters never clicks. Do not smooth the `k` read
  rate itself (that would detune the harmonics).

### 5.2 Cascade mode

Intent: **one buffer segment plays with the harmonics entering in ascending
order**, then the FX returns to capture.

1. While idle and the loop is playing, continuously write the post-shift input
   into the 2 s buffer at a write phase that advances at 1 frame/sample.
   The buffer always contains the most recent `time` seconds (or the most
   recent audio since the FX started).
2. On a **Fire** (a rising change of the `trig` counter) or the **auto** clock,
   freeze the write, snapshot the write head, and start `steps` players:
   * player `k` reads the captured segment at rate `k` frames/sample;
   * it starts `(k - 1) * spacing` seconds after the freeze;
   * it plays for `time / k` seconds (the segment at k× speed);
   * it has amplitude `a_k` and a short attack/release envelope (skip the
     player when `k > steps`).
   The first player is the segment at its original rate; the second is the
   octave; the third is the 12th; and so on. This is the ascending gesture.
3. The buffer is **frozen** for the whole cascade: all players read the same
   captured segment, so the harmonic relationship is exact. Do not let the
   write head move while the cascade plays.
4. The cascade ends after
   `max(time, (steps - 1) * spacing + time / steps)` seconds. Resume writing
   (re-capture) immediately after; a new Fire then uses a fresh segment.
5. `Fire` during a cascade may restart the cascade from the same frozen
   segment (or be ignored) — pick one and document it. Do not corrupt the
   write phase. A Fire before `time` seconds of audio exists plays a partial
   segment; document that.
6. **Auto** (default on) fires every
   `time + cascade_duration + 0.1` seconds, first at `time` seconds after the
   FX starts (so the first segment is full). With Auto off, only `Fire` starts
   a cascade. `spacing = 0` means all harmonics enter together (a harmonic
   chord) and is allowed.

### 5.3 Delay mode

Intent: **continuous harmonized buffer delay**, no freeze and no Fire.

1. Write the post-shift input into the 2 s buffer continuously; the write phase
   never stops.
2. Each tap `k` reads the buffer at rate `k` frames/sample, looping over the
   buffer (wrap at the buffer end), starting `time` seconds behind the initial
   write head. Because the write head moves at 1× while a tap reads at k×, the
   delay window drifts; that is the intended **varispeed harmonizer**
   character, not a fixed-time echo. Document that in the stage README.
3. All taps run whenever the mode is on; the wet is continuous.
4. `spacing`, `auto` and `Fire` are inert in Delay mode (log one line if Fire
   is pressed; the GUI disables the inert controls).
5. `steps = 1` is a single delayed copy at 1× (a plain delay); 2 adds the
   octave, 3 the 12th, and so on.

### 5.4 Buffer safety and CPU

* Never read or write outside `bufnum`'s frame count. Clamp `time` to the
  buffer duration (2.0 s). `steps` max 8. `spacing` max 1.0 s.
* The buffer may be shorter than a full 2 s only if the server sample rate is
  odd; clamp.
* Four FX synths, each one `BufWr` + up to eight `BufRd` + envelopes, only when
  the mode is on. This is the CPU ceiling; do not add an FFT, a `PitchShift`
  per harmonic, or a second voice.
* The delay/cascade must be mono; the loop voice and its `outBus` are mono.

---

## 6. Controls, OSC and the Python mirrors

### 6.1 Loop-edit parameters (all session-only, per loop)

| Wire name | GUI label | Type / range | Default | Meaning |
|---|---|---|---|---|
| `harm_mode` | `Harm` | `off`, `cascade`, `delay` | `off` | FX off / one-shot cascade / continuous harmonized delay |
| `harm_mix` | `Mix` | 0–1 | 0.35 | Wet return level (the dry loop is upstream) |
| `harm_time` | `Time` | 0.05–2.0 s | 0.50 | Cascade segment length / Delay base window |
| `harm_steps` | `Steps` | 1–8 | 4 | Harmonics 1×…N× |
| `harm_spacing` | `Space` | 0.0–1.0 s | 0.08 | Cascade entry spacing (inert in Delay) |
| `harm_falloff` | `Fall` | 0–1 | 0.50 | Amplitude exponent `0.5 + 1.5 × falloff` |
| `harm_auto` | `Auto` | 0/1 | 1 | Cascade auto-fire (inert in Delay) |

Add constants to `ui/osc_control.py` with clear names and docs:
`LOOP_HARM_MODES`, `LOOP_HARM_MODE_DEFAULT`, `LOOP_HARM_MIX_DEFAULT`,
`LOOP_HARM_TIME_MIN/MAX/DEFAULT`, `LOOP_HARM_STEPS_MIN/MAX/DEFAULT`,
`LOOP_HARM_SPACING_MIN/MAX/DEFAULT`, `LOOP_HARM_FALLOFF_MIN/MAX/DEFAULT`,
`LOOP_HARM_AUTO_DEFAULT`, `LOOP_HARM_MAX_SECONDS`.

### 6.2 `/ji/loop/edit` payload — 31 → 38 values

Append exactly these seven values, in this order, after
`grain_breath_rate` (the current 31st value):

```text
harm_mode (string), harm_mix, harm_time, harm_steps,
harm_spacing, harm_falloff, harm_auto (0/1 int)
```

Update `~ji_ui_send_loop_edit` in `tests/sc/test_live_rig.scd` and the strict
`parse_loop_edit` in `ui/osc_control.py` (`len(args) != 38` is an error).
`harm_mode` must be one of the three wire strings; everything else is clamped.
The `LoopEdit` dataclass gains the seven fields with the documented defaults.
`LoopEdit.status_line()` appends e.g. `harm cascade 4× · 0.50 s · mix 35 %`
when the mode is not off (do not clutter the line when off). `LoopEditMemory`
copies the dict, so the new keys travel with a saved loop memory slot; the
performance state still excludes loops.

### 6.3 `/ji/loop/fx_fire` (UI → sclang only)

New address:

| Address | Args | Direction |
|---|---|---|
| `/ji/loop/fx_fire` | `[int index]` | UI → sclang (control surface) |

`sclang` handler (`ui/control_surface.scd`): validate `index` 1–4; if
`harm_mode == "cascade"` and an FX synth exists, increment that loop's trigger
counter, `set(\trig, counter)` on the FX synth, log one line
`[LOOP i] harm fire (n)`; otherwise log one ignore line and do nothing. No
reply message; `/ji/loop/edit` is the existing state echo and does not need to
change for a fire.

`ui/osc_control.py`: add `build_loop_fx_fire(index)` and
`OSCController.loop_fx_fire(index)` sending to the **SC client** (like
`loop_record`), not the bridge. The pure test asserts the exact
`("/ji/loop/fx_fire", [i])` message and that it does not use the bridge client.

### 6.4 `/ji/loop/edit_set` names

`~ji_loop_set_edit` accepts `harm_mode`, `harm_mix`, `harm_time`,
`harm_steps`, `harm_spacing`, `harm_falloff`, `harm_auto` with the same
validation as the existing params:
* `harm_mode`: value-compare the three strings (remember
  `indexOfEqual`, not `indexOf`), map to `\off`/`\cascade`/`\delay`, reject
  anything else with one line;
* numeric values clamp to the ranges above; `harm_steps` is an integer;
* `harm_auto` is 0/1;
* any change sends a fresh `/ji/loop/edit` and one `[LOOP i] edit …` line;
* `harm_mode`/`harm_time` changes restart the live FX synth; the other
  parameters `set` it. If the mode changes to off, stop/free the FX synth; to
  cascade/delay, start a fresh one (buffer/capture state reset).

`~ji_ui_control_surface_version` bumps to `"9.0.0"`; update any version checks
in `tests/sc/test_live_rig.scd`/`test_loop_edit.scd`.

### 6.5 No bridge/performance-state change

No new `/ji/bridge` message, no new state key, no `config/` file. The FX is a
loop edit, controlled over the existing UI→sclang OSC surface.

---

## 7. GUI

Add **one row** to the existing loop-edit area (the rows for the loop chosen in
the `Loop` combo), between the Morph row and the status line, or after Morph if
that reads better. Suggested row:

```text
Harm [off v]  Mix [35 %]  Time [0.50 s]  Steps [4]  Space [80 ms]  Fall [50 %]  Auto [x]  [Fire]
```

* `Harm` is a `QComboBox` over `off`/`Cascade`/`Delay` (display labels; wire
  values lower-case as above).
* `Mix`/`Fall` are percent knobs or spins (0–100 %, send value/100).
* `Time` is a seconds spin 0.05–2.00, 3 decimals or 2 decimals + ` s`.
* `Steps` is a 1–8 spin.
* `Space` is milliseconds (0–1000, displayed `/1000` seconds on the wire or
  send seconds directly — match the existing grain-size convention and say
  which).
* `Auto` is a checkbox.
* `Fire` is a `QPushButton`, enabled only when `Harm = Cascade` and the loop is
  playing; it calls `self.controller.loop_fx_fire(index)` and does **not** send
  a loop edit. `Space`/`Auto`/`Fire` are disabled in `Delay` and `off`; `Mix`,
  `Time`, `Steps`, `Fall` remain enabled in both active modes.
* Incoming `/ji/loop/edit` syncs every widget signal-blocked (`_loop_edit_sync`)
  and never echoes. Every edit sends `/ji/loop/edit_set` immediately (loop
  edits are immediate, like every other loop edit; they are not beat-queued).
* Rebuild the row when the selected loop changes, like the other loop rows.
* The loop status line shows the Harm summary when on.
* The default window grows by ~26 px: `self.resize(1280, 1198)`; update the
  three Qt height bounds/comments from 1205 to **1230** (measure and adjust if
  the offscreen height differs). Status stays in the layout; the scroll area
  remains the net.
* No new group, no tab, no dialog, no separate window.

---

## 8. Tests

### 8.1 Pure Python (`tests/test_ui.py --pure`)

- `LOOP_EDIT_PARAMS` now contains 38 params; the new defaults and ranges are
  exact.
- `build_set_loop_edit` accepts every new name, rejects bad modes, clamps
  numbers, and `harm_steps` is an integer.
- `parse_loop_edit` accepts exactly 38 values and rejects 31/37/39; bad
  `harm_mode` raises; values clamp/mirror.
- `LoopEdit` defaults, `status_line()` when off/on, and `LoopMemory`
  round-trip carry the new keys.
- `count_in_beats` uses ceil: 4/4 → 4, 3/4 → 3, 6/8 → 3, 5/8 (2.5) → 3,
  7/8 (3.5) → 4, off/1 beat unchanged.
- `build_loop_fx_fire` and `OSCController.loop_fx_fire` send
  `/ji/loop/fx_fire [index]` to the **SC** client, not the bridge.
- Store apply of a 38-value `/ji/loop/edit` updates every field.

### 8.2 Optional Qt (`tests/test_ui.py --qt`)

- The new row exists with defaults (`off`, 35 %, 0.50 s, 4, 80 ms, 50 %, Auto
  on).
- Selecting `Cascade`/`Delay` sends the right `harm_mode`; changing Mix/Time/
  Steps/Space/Fall sends the right values; `Auto` sends 0/1.
- `Fire` is disabled off/Delay and enabled in Cascade while playing (or sends
  and logs when enabled); a press sends `/ji/loop/fx_fire` to the SC fake, not
  the bridge fake.
- An incoming 38-value `/ji/loop/edit` syncs the row with no feedback.
- Height ≤ 1230; Status remains present; no extra top-level window.
- Qt may skip (no `libEGL.so.1`); say so.

### 8.3 sclang tests

- `tests/sc/test_live_rig.scd`:
  - edit defaults for the seven `harm_*` keys;
  - `~ji_loop_edit_args` includes `\fxBus` with the bus index and no harm args;
  - 38-value `/ji/loop/edit` shape;
  - FX buses exist, are private audio buses, and are re-used;
  - `~ji_loop_fx_start/stop` create/free the FX synth after the player;
  - count-in helper ceil checks for 5/8/7/8;
  - a state-only check for `~ji_cancel_count_in` restoring a valid state (if
    feasible).
- `tests/sc/test_loop_edit.scd`: add the `harm_*` defaults/clamps, payload,
  fire, and the manual count-in steps (free-mode second Rec, Stop Rec during
  count, 5/8/7/8).
- The stage README's human checklist covers what sclang cannot automate.

### 8.4 Regression

Run from `ji-live/` (all except Qt are required):

```bash
~/.ji-tools/uv run python tests/test_fretboard.py
~/.ji-tools/uv run --with python-osc python tests/test_rhythm.py
~/.ji-tools/uv run --with python-osc python tests/test_breath.py
~/.ji-tools/uv run --with python-osc python tests/test_bass.py
~/.ji-tools/uv run --with python-osc python tests/test_state.py
~/.ji-tools/uv run --with python-osc python tests/test_arp.py
~/.ji-tools/uv run --with python-osc python tests/test_lead.py
~/.ji-tools/uv run --with python-osc python tests/test_drone.py
~/.ji-tools/uv run --with python-osc python tests/test_groove.py
~/.ji-tools/uv run --with python-osc python tests/test_quantize.py
~/.ji-tools/uv run --with python-osc python tests/test_home.py
~/.ji-tools/uv run --with python-osc python tests/test_glide.py
~/.ji-tools/uv run --with python-osc python tests/test_startup.py
~/.ji-tools/uv run --with python-osc python tests/test_mutation.py
~/.ji-tools/uv run --with python-osc python tests/test_mutation_controls.py
~/.ji-tools/uv run --with python-osc python tests/test_ui.py --pure
~/.ji-tools/uv run python tests/test_mpe.py --pure
# if PyQt6 can run:
QT_QPA_PLATFORM=offscreen ~/.ji-tools/uv run --with python-osc --with PyQt6 \
    python tests/test_ui.py --qt
```

Do not weaken the Qt test to hide a real failure.

---

## 9. Human acceptance checklist (the owner is the authority)

1. **No regression / FX off:** with `Harm = off`, every loop sounds exactly as
   in 8.8 (normal, reverse, varispeed, grain, morph, breath); no extra CPU
   hiss, no wet tail, no clicks.
2. **Cascade:** play loop 1, choose `Cascade`, press `Fire`; hear the captured
   segment, then the octave, 12th, 16th … entering in order. `Steps` 1 = only
   the fundamental; 4/8 = the full stack. `Time`, `Space`, `Fall`, `Mix` change
   the gesture as labelled. `Auto` on fires the cascade periodically; off
   requires `Fire`.
3. **Delay:** choose `Delay`; the loop is continuously doubled/filled by the
   harmonic stack, no trigger needed. `Mix` 0 is dry, `Steps` 1 is a plain
   delay, higher steps add the harmonic ladder.
4. **Post-pitch-shift:** mutate the JI set while the FX is on; the FX remains
   tuned to the loop's shifted pitch (it is downstream of `PitchShift`), and
   the harmonic ratios are integer multiples of it. No second autotune pass.
5. **Per-loop:** loop 1 CASCADE and loop 2 DELAY run independently; stopping
   loop 1 leaves loop 2's FX alive; stopping a loop frees its FX (no tail, no
   stuck synth in `s.plotTree`).
6. **Count-in:** free mode, `1 bar`, transport stopped; press Rec; watch
   `counting` count down and the take start one bar later. Press Rec again
   during the count: it must skip and record immediately (both free and beat
   mode). `Stop Rec` during the count restores the previous state (empty or
   recorded), no stuck "counting". Repeat in 3/4, 6/8, 5/8, 7/8; a bar is
   rounded up to a whole quarter beat. Beat mode still needs Play and a
   transport stop cancels a count-in.
7. **No state change:** `Harm` settings are session-only; saving/loading a
   performance state does not save or restore them; a GUI/sclang restart
   returns to `off`.
8. **CPU/latency:** four loops with Cascade/Delay on stay usable; no audio
   dropouts; loop start/stop remains clean.
9. **Window:** the Harm row fits at the default size; Status is reachable; the
   scroll area handles a small screen; no painting glitches.
10. Record the result. **9.0 is not complete until the owner confirms the FX is
    musically useful and the count-in is trustworthy.**

---

## 10. Risks

- **Node order / bus reads.** `In.ar` must run after the loop voice's
  `Out.ar(fxBus)`. Use `Synth.after(player, …)` and verify with `s.plotTree`
  and a scope; a zero-read FX is almost always node order or a wrong bus index.
- **Private audio buses.** They are not the 16 hardware outputs and not the
  control buses. Allocate once, reuse on re-eval, pass `.index`, and fail
  loudly if missing. Do not hard-code 16–19 in SynthDefs.
- **Frozen-buffer race.** In Cascade, the write must stop before the players
  read; the write phase snapshot must be the same block as the freeze.
  Out-of-bounds reads are the other failure; clamp `time` to the buffer.
- **Trigger/auto double fire.** Auto must not fire during a cascade; a Fire
  during a cascade must not corrupt the write phase. Pick and document the
  restart/ignore rule.
- **Harmonic detune.** Do not smooth the per-tap read rate; a `Lag` on `k`
  would glide the pitch and smear the integer ratios. Only `mix`/mode need
  smoothing.
- **CPU.** Four FX synths × eight `BufRd` only while active; no per-harmonic
  `PitchShift`, no FFT, no second grain cloud.
- **Count-in state.** The cancel/restore path is the source of the stuck
  `counting` state; never store `\counting` as the previous state, and
  generation-check every tick.
- **Payload mirrors.** 31 → 38 values is a strict parser change; a mismatch
  makes the whole loop edit silently rejected. Update sclang, `osc_control.py`,
  `LoopEdit`, tests, docs together.
- **Window height.** One more row; measure if Qt runs, otherwise use 1198/1230
  as above.
- **No audio/sclang/Qt in the agent environment.** Do not claim a listening or
  visual pass. The pure harness and the human checklist are the evidence.

---

## 11. Deliverables

```text
ji-live/
├── synthdefs/ji_harmonic_delay.scd        NEW: cascade + continuous delay
├── synthdefs/ji_loop_voice.scd            MOD: fxBus send
├── startup.scd                            MOD: 4 private FX audio buses
├── tests/sc/test_live_rig.scd             MOD: FX buses/buffers/args/lifecycle,
│                                              count-in checks, 38-value payload
├── tests/sc/test_loop_edit.scd            MOD: Harm checks + count-in manual steps
├── ui/control_surface.scd                 MOD: harm params, fx_fire, lifecycle,
│                                              version 9.0.0
├── ui/osc_control.py                      MOD: constants, LoopEdit fields,
│                                              params, parser, builders, controller
├── ui/ji_ui.py                            MOD: Harm row, Fire, status, height
├── tests/test_ui.py                       MOD: pure + Qt
├── tests/test_startup.py                  MOD only if a doc/launch assertion needs it
├── docs/README_stage9_0.md                NEW: feature, both DSP modes, count-in
│                                              fixes, bus map, tests, checklist
├── docs/START_HERE.md                     MOD: controls + troubleshooting
├── docs/GUI_PARAMETERS.md                 MOD: Harm row + count-in rounding
├── docs/README_live_rig.md                MOD: bus map (4 private FX buses)
├── docs/design_doc_10.28.10.50            MOD: §4.2, §6.23, §7.1, §8.9, §9,
│                                              §13, §14
└── README.md                              MOD: status, layout, tests, links
```

No changes to `control/`, `config/`, `synthdefs/ji_loop_voice.scd` beyond the
`fxBus` send, any OSC address used by the bridge, or any performance-state key.
`docs/prompts/PROMPT_stage9_0_harmonic_delay.md` is this file and is kept as the
stage record.

---

## 12. Self-review requirement (final response)

Provide:

- Files added/changed.
- For each requirement, the code that satisfies it or a concrete concern.
- **The count-in bug findings:** restate A1/A2/A3/A4 and how each was fixed and
  evidenced; if an issue was not reproducible, say exactly what you found.
- “If I had to bet on which line is most likely to fail on the owner's machine,
  which is it, and why? Give a one-line verification command.”
- Assumptions, max 5 bullets.
- Explicit statement that audio, MIDI, sclang and the live GUI were not tested
  by the agent (unless sclang/Qt actually ran), and that 9.0 awaits human
  acceptance.
- The exact one-sclang + one-Python start sequence, copied from
  `docs/START_HERE.md` after your updates.

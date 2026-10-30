# JI Station — Stage 4 (dual-channel autotune + raw looper)

**Status:** Stage 4 is **implemented** in sclang/audio code. It is **awaiting
human audio acceptance**. The agent environment cannot run SuperCollider or
hear audio, so this file does not claim that the Stage 4 audio has passed.

> **Stage 5 update:** Stage 5 is now implemented. `startup.scd` boots
> **MiniFuse 2 input / BlackHole 16ch output** with 2 in / 16 out; SC writes
> BlackHole and the human monitors Ableton through the MiniFuse 2. Stage 4 audio
> remains on output pair 0/1 (Ableton track 1). See `README_stage5.md` and the
> design doc §8.6. Stage 4 audio is still awaiting human acceptance through that
> path.
>
> **Live-rig integration (Stage 5.5):** [test_live_rig.scd](test_live_rig.scd)
> puts the two autotuned inputs and two loops on separate SC outputs
> **0, 1, 2, 3** and connects them to the Python mutation bridge. That is the
> current four-channel product; see [README_live_rig.md](README_live_rig.md).
> The Stage 4 `test_dual_loop.scd` remains unchanged.

Stage 4 extends the accepted Stage 1/3 autotune path to two independent live
inputs and adds raw loop recording/playback. Every live input and every playing
loop retunes to its own nearest class on the next 20 Hz lookup tick after a
FrequencySet mutation, in its own octave, without replay or restart.

```text
input 0 → Tartini → nearest class → PitchShift → out 0
input 1 → Tartini → nearest class → PitchShift → out 1

input 0 (dry) → RecordBuf → loop buffer 1
input 1 (dry) → RecordBuf → loop buffer 2
loop buffer N → PlayBuf → Tartini → nearest class → PitchShift → out
```

---

## 1. Files

```text
ji-live/
├── startup.scd                              MODIFIED: Stage 5 2-in/16-out devices + Stage 4 buses 8–18
├── synthdefs/
│   ├── ji_autotune_ch.scd                   UNCHANGED: verified single-channel DSP
│   ├── ji_looper_recorder.scd               NEW: dry mono RecordBuf recorder
│   └── ji_looper_player.scd                 NEW: PlayBuf + Tartini + PitchShift
├── test_dual_loop.scd                       NEW: Stage 4 orchestration
├── README_stage4.md                         NEW: this file
└── design_doc_10.28.10.50                   UPDATED: Stage 4 spec/status/changelog
```

There is deliberately **no** `synthdefs/ji_autotune_dual.scd`. Stage 4 reuses
two instances of the already-verified `\ji_autotune_ch` graph, one per input
channel. A thin dual wrapper would only duplicate the same DSP and add a second
definition to maintain; the two instances already have independent tracker
buses and independent `targetFreq` values. No new autotune DSP is introduced.

Unchanged: `synthdefs/ji_autotune_ch.scd`, `test_bridge.scd`,
`test_autotune.scd`, `test_tartini.scd`, and all Stage 2/3 Python files.

---

## 2. Prerequisites

- macOS, SuperCollider 3.15, sc3-plugins/Tartini, **MiniFuse 2** input, and
  **BlackHole 16ch** output at 48 kHz (the Stage 5 device configuration).
- `startup.scd` evaluated: server booted with
  `inDevice_("MiniFuse 2")` / `outDevice_("BlackHole 16ch")`, 2 in / 16 out,
  buses 0–18 allocated, and every `synthdefs/*.scd` loaded (the recorder and
  player are new files; startup loads them automatically).
- Headphones. Inputs are live and the outputs contain pitch-shifted copies;
  speakers will feed back.
- No Stage 0/1/3 test left running. `test_dual_loop.scd` stops the known
  variables, but a Synth started under a different variable must be freed by
  hand. Do **not** run `test_bridge.scd` and `test_dual_loop.scd` at the same
  time: both install a `/ji/freqset/update` receiver and both start autotune
  audio.
- Python 3.11+ via `uv`; `python-osc` is fetched by `uv run`.
- A silent Stage 4 start is normal. Play into input 0 and input 1 to hear the
  live channels through Ableton track 1 (see `README_stage5.md`).

### Bus allocation (cumulative)

| Bus | Name | Stage | Purpose |
|---|---|---|---|
| 0 | `~ji_tartini_freq1` | 0 | debug tracker freq, ch 0 |
| 1 | `~ji_tartini_freq2` | 0 | debug tracker freq, ch 1 |
| 2 | `~ji_tartini_hasfreq1` | 0 | debug confidence, ch 0 |
| 3 | `~ji_tartini_hasfreq2` | 0 | debug confidence, ch 1 |
| 4 | `~ji_cpu` | 0 | system CPU |
| 5 | `~ji_autotune_freq1` | 1 | autotune tracker freq, ch 0 |
| 6 | `~ji_autotune_hasfreq1` | 1 | autotune confidence, ch 0 |
| 7 | `~ji_autotune_target1` | 1 | reserved target, ch 0 |
| 8 | `~ji_autotune_freq2` | 4 | autotune tracker freq, ch 1 |
| 9 | `~ji_autotune_hasfreq2` | 4 | autotune confidence, ch 1 |
| 10 | `~ji_autotune_target2` | 4 | reserved target, ch 1 |
| 11–14 | `~ji_loop_freq1..4` | 4 | loop Tartini frequencies |
| 15–18 | `~ji_loop_hasfreq1..4` | 4 | loop Tartini confidences |

Buses 0–7 are never reallocated. Re-evaluating `startup.scd` reuses the existing
Stage 4 block when all eleven buses are present. A partial block is completed
with a warning, and the sanity check reports any numbering that is not 0–18.

### Separate input and output devices (macOS)

SuperCollider **does** support separate input and output devices. `ServerOptions`
has `inDevice` and `outDevice`; use the individual setters to split them
(`device_("...")` sets both to one device):

```supercollider
ServerOptions.devices.postln;     // all devices
ServerOptions.inDevices.postln;   // exact input names
ServerOptions.outDevices.postln;  // exact output names

s.options.device_("One Device");                    // both directions
// or, for separate devices:
s.options.inDevice_("MiniFuse 2");
s.options.outDevice_("BlackHole 16ch");
```

When the two differ, scsynth is launched with `-H <in> <out>`. Set these
**before booting**; a running server needs `s.quit` / `s.reboot`, then
re-evaluating `startup.scd`.

The Stage 5 `startup.scd` uses this configuration (MiniFuse 2 input,
BlackHole 16ch output):

```supercollider
s.options.inDevice_("MiniFuse 2");
s.options.outDevice_("BlackHole 16ch");
s.options.numInputBusChannels  = 2;
s.options.numOutputBusChannels = 16;
s.options.blockSize            = 64;
s.options.sampleRate           = 0;   // 0 = use the devices' Audio MIDI Setup rate
```

**Do not force `s.options.sampleRate = 48000`** when BlackHole 16ch is a
separate output device: on macOS this can make scsynth exit with code 0 right
after "server ready". Use `0` (the default) and set both devices to 48 kHz in
Audio MIDI Setup; the post-boot `Audio I/O:` line reports the server's actual
rate. See `README_stage5.md` §4.

Then:

1. Set both devices to **48000 Hz** in Audio MIDI Setup.
2. Reboot the server and re-evaluate `startup.scd`. It should print
   `inDevice=MiniFuse 2 outDevice=BlackHole 16ch` and
   `Audio I/O: 2 in / 16 out, 48000 Hz, blockSize 64`.
3. Run `test_ableton_routing.scd`; in Ableton, input is **BlackHole 16ch** with
   inputs 1–16 enabled, and output is the MiniFuse 2 (or headphones).
4. Stage 4 audio remains on output **pair 0/1** (Ableton track 1); the rest of
   the 16-channel map is reserved for later stages. Stage 4 is not audible from
   SC alone: SC writes to BlackHole, and Ableton must monitor BlackHole and send
   its master output to the MiniFuse 2/headphones. See `README_stage5.md`.

Notes:

- Use `ServerOptions.inDevices` / `outDevices` for the exact names; a typo makes
  scsynth fall back or fail. `startup.scd` warns and lists the available names
  instead of silently substituting another physical device.
- Separate devices have independent clocks. Keep both at 48 kHz and test a
  several-minute run for drift/dropouts.
- Do **not** use Bluetooth headphones for this; latency and device support are
  poor. Use wired headphones on the MiniFuse 2.
- **Aggregate Device fallback:** if `-H`/separate devices fail on your macOS
  version, create one Aggregate Device containing the input and output devices,
  set `singleDeviceName` to the aggregate name in `startup.scd` (or select the
  aggregate in Ableton only), and use 2/16 bus channels. Verify the aggregate's
  channel order. The primary Stage 5 path is the separate
  `inDevice`/`outDevice` configuration above; see `README_stage5.md` §9.

---

## 3. Start order

### a. SuperCollider: boot the rig

Evaluate `startup.scd` (Cmd+Return). Confirm:

```text
Available input devices:  [ ... MiniFuse 2 ... ]
Available output devices: [ ... BlackHole 16ch ... ]
Audio devices: in=MiniFuse 2 out=BlackHole 16ch (separate)
[DEVICE] input found: MiniFuse 2
[DEVICE] output found: BlackHole 16ch
Stage 4 buses allocated (8-18).
JI system buses allocated: freq1=0 freq2=1 hasfreq1=2 hasfreq2=3 cpu=4 autotune_freq1=5 autotune_hasfreq1=6 autotune_target1=7 autotune_freq2=8 autotune_hasfreq2=9 autotune_target2=10 loop_freq=[ 11, 12, 13, 14 ] loop_hasfreq=[ 15, 16, 17, 18 ]
Loading 4 SynthDef file(s) ...
  - ji_autotune_ch.scd
  - ji_looper_player.scd
  - ji_looper_recorder.scd
  - ji_tartini_debug.scd
inDevice=MiniFuse 2 outDevice=BlackHole 16ch
Audio I/O: 2 in / 16 out, 48000 Hz, blockSize 64
Server device setting: [ MiniFuse 2, BlackHole 16ch ]
Startup complete (Stage 5: 2 in / 16 out, buses 0-18).
```

If the numbers are not 0–18, read the NOTE that follows. Everything can still
work, but the contract is cleaner after a fresh interpreter + server.

### b. SuperCollider: start Stage 4

Evaluate the whole of `test_dual_loop.scd` (Cmd+Return). It:

- checks the server, buses 8–18, and the three SynthDefs;
- stops and frees the known Stage 0/1/3/4 routines and synths, and frees old
  loop buffers;
- prints `NetAddr.langPort` (normally `57120`);
- installs the Stage 3 `/ji/freqset/update` receiver under a new OSCdef key;
- resets the seven-class set at A3 = 220 Hz;
- starts `\ji_autotune_ch` on input 0 → out 0 and input 1 → out 1;
- allocates `~ji_buf_loop1..4` (4 s mono placeholders) and reserves tracker
  buses 11–18;
- starts the 20 Hz multi-voice lookup and the 1 s status/CPU lines.

Confirm lines like:

```text
[AUTOTUNE] ch0 started (node 1000) inBus=0 outBus=0
[AUTOTUNE] ch1 started (node 1001) inBus=1 outBus=1
[BRIDGE] /ji/freqset/update receiver installed (key=\ji_freqset_update_dual), initial set size=7 fund=220.00
[LOOP] buffers allocated: 1=1 2=2 3=3 4=4 (4.0 s mono each)
[LOOP] tracker buses: loop_freq=[ 11, 12, 13, 14 ] loop_hasfreq=[ 15, 16, 17, 18 ]
[LOOKUP] multi-voice lookup running at 20 Hz for 6 voices
[STAGE4] Ready. Live: input 0 -> out 0, input 1 -> out 1. Loops: 1/3 -> out 0, 2/4 -> out 1.
```

### c. Terminal: start the Python bridge

From `ji-live/`:

```bash
uv run --with python-osc python control/ji_bridge.py --interactive --glide-time 0.15
```

No new Python protocol is needed. The bridge still sends exactly:

```text
/ji/freqset/update  fundamental  glide_time  ratio_1 ... ratio_N
```

If the bridge was started before sclang was listening, its initial set message
was lost; press `m` once to send the current set. The first mutation always
sends the complete set.

---

## 4. Operating loops from sclang

```supercollider
~ji_record_loop.(1, 4.0);   // record input 0 into loop 1 for 4 s
~ji_stop_record.(1);        // optional early stop; buffer is kept
~ji_play_loop.(1);          // play loop 1 through its own autotune chain

~ji_record_loop.(2, 4.0);   // record input 1 into loop 2
~ji_stop_record.(2);
~ji_play_loop.(2);

~ji_stop_loop.(1);          // 30 ms fade, buffer kept
~ji_stop_loop.(2);
~ji_stop_all.();            // stop audio, recorders, lookup and CPU routines
```

- Loop 1 and loop 3 record input 0; loop 2 and loop 4 record input 1.
- Loops 1/3 monitor on output 0; loops 2/4 monitor on output 1. They sum with
  the corresponding live channel.
- `~ji_record_loop.(index, seconds)` allocates a fresh mono buffer of exactly
  `seconds` and starts recording at frame 0. It overwrites; it never overdubs.
- Recording auto-stops when the allocated buffer fills (`RecordBuf` `loop: 0`,
  `doneAction: 2`). Calling `~ji_stop_record.()` earlier frees the recorder and
  leaves the partial buffer intact. The remainder of an early-stopped buffer is
  silence, so record the full `seconds` for a seamless loop.
- `~ji_play_loop.()` is refused while that loop is still recording; call
  `~ji_stop_record.()` first (or wait for the auto-stop).
- The four buffers are allocated at test start; recording reallocates the one
  being used. All four tracker-bus pairs are reserved even though the Stage 4
  test only exercises loops 1 and 2.

The loop player has a 10 ms fade-in and a 30 ms release, so start/stop does not
slam the output. It uses `PlayBuf` → `LeakDC` → `Tartini` → `PitchShift`, with
Tartini measuring the **unshifted** buffer. The loop is never transposed as a
whole.

---

## 5. How mutations retune live inputs and loops

The set receiver in `test_dual_loop.scd` copies the accepted Stage 3 behaviour:

1. validate `fundamental, glide, ratios...` (1–12 positive ratios, positive
   fundamental, non-negative glide);
2. update `~ji_ji_set_ratios`, `~ji_ji_fundamental`, and the 12-slot Hz mirror;
3. **broadcast** `retuneTime = max(glide, 0.005)` to every active live/loop
   synth immediately;
4. let the 20 Hz multi-voice lookup service every active voice.

The lookup is the verified Stage 1 arithmetic, including `2/1` as the
octave-wrap candidate:

```text
oct = floor(log2(freq / fundamental))
fold freq into [fundamental, 2*fundamental)
pick the nearest class (current set + 2/1)
multiply the result back by 2^oct
```

Each voice keeps its own `lastTarget`. A mutation may leave a voice's target
unchanged; that voice keeps playing. When its target changes, sclang sets
`targetFreq` on that voice only. No replay, note-on, loop restart, or server-side
nearest-class SynthDef is involved.

**Glide strategy chosen:** broadcast, not a shared consumable
`~ji_pending_glide`. A single global pending glide consumed by one voice would
starve the other live channel and the loops. Every active voice receives the
event glide at set-update time; its own `Lag.kr` then applies that time when its
target eventually changes. A loop created *after* a mutation starts with
`~ji_current_glide` (the most recent event glide).

A loop's "own octave" is the octave of the recorded audio. It is not forced into
the analysis voicing and `MutationEvent.new_octave` is not used.

---

## 6. Expected output

```text
[BRIDGE] sclang language OSC port = 57120
[BRIDGE] Hz mirror allocated: bufnum=0 slots=12
[AUTOTUNE] ch0 started (node 1000) inBus=0 outBus=0
[AUTOTUNE] ch1 started (node 1001) inBus=1 outBus=1
[BRIDGE] /ji/freqset/update receiver installed (key=\ji_freqset_update_dual), initial set size=7 fund=220.00
[LOOP] buffers allocated: 1=1 2=2 3=3 4=4 (4.0 s mono each)
[LOOP] tracker buses: loop_freq=[ 11, 12, 13, 14 ] loop_hasfreq=[ 15, 16, 17, 18 ]
[LOOKUP] multi-voice lookup running at 20 Hz for 6 voices
[STAGE4] Ready. ...
[BRIDGE] set update: size=7 fundamental=220.00 Hz glide=0.150s buffer=0 ratios=[ 1.0, 1.125, 1.25, 1.3333333333333, 1.5, 1.6666666666667, 1.875 ]
[AUTOTUNE ch=0] freq=220.31 target=220.00 hasFreq=0.91 shift=-0.02st setSize=7 fund=220.00
[AUTOTUNE ch=1] freq=329.63 target=330.00 hasFreq=0.88 shift=0.02st setSize=7 fund=220.00
[LOOP 1] state=recording bufnum=1
[LOOP 1] state=playing freq=246.94 target=247.50 hasFreq=0.87 shift=0.04st
[LOOP 2] state=playing freq=293.66 target=293.33 hasFreq=0.90 shift=-0.02st
CPU: 38.2%
```

Exact buffer numbers, frequencies, targets, and classes vary with the audio and
the stochastic mutation. The important diagnostics are `target=`, the
`set update` line, the loop `state=`, and `CPU:`.

Python output is unchanged from Stage 3 (`[MUTATION] ...`,
`[BRIDGE] -> /ji/freqset/update ...`).

---

## 7. Human acceptance test

1. Evaluate the Stage 5 `startup.scd` (MiniFuse 2 in / BlackHole 16ch out);
   confirm the device line, 2 in / 16 out, buses 0–18, and all four SynthDef
   files loaded.
2. Evaluate `test_dual_loop.scd`. Confirm two `[AUTOTUNE] ... started` lines,
   four loop buffer numbers, tracker buses 11–18, the multi-voice lookup line,
   and status/CPU lines.
3. Start Python:
   ```bash
   cd ji-live
   uv run --with python-osc python control/ji_bridge.py --interactive --glide-time 0.15
   ```
4. Play/sing into input 0 and input 1. Confirm each channel independently snaps
   to a member of the current set, in its own octave. Watch both
   `[AUTOTUNE ch=...]` lines.
5. Record loop 1 from input 0:
   ```supercollider
   ~ji_record_loop.(1, 4.0);
   // after the record time:
   ~ji_stop_record.(1);
   ~ji_play_loop.(1);
   ```
6. While loop 1 plays, press `m` in Python until the loop's current class is
   removed/replaced. Confirm:
   - `[LOOP 1] target=` changes;
   - the loop retunes without restarting playback;
   - the target stays in the recorded audio's octave;
   - the transition uses the event glide (`g 0.15`, or raise it).
7. Record loop 2 from input 1, play it, and mutate again. Confirm both live
   channels and both loops retune independently to the new set.
8. Confirm the set size stays 3–12 and no mutation removes the last class.
9. Confirm CPU is below 60% with 2 live + 2 loops. Record the measured value.
10. Confirm no clicks/dropouts/zipper noise during mutations and loop
    start/stop. If a loop seam clicks once per cycle, see §9.
11. Stop Python with `q`; then run the SuperCollider stop block in §8.

Acceptance is audible: two live inputs and two loops all retune to the changing
set without replay or restart.

---

## 8. Stopping

In Python: `q` (or Ctrl+C) stops the OSC listener.

In SuperCollider:

```supercollider
~ji_stop_all.();
~ji_dual_lookup_routine.stop;
~ji_dual_cpu_routine.stop;
~ji_freqset_oscdef.free;
~ji_buf_loop1.free;
~ji_buf_loop2.free;
~ji_buf_loop3.free;
~ji_buf_loop4.free;
~ji_buf_freqset.free;
```

`~ji_stop_all.()` stops and frees the live channels, loop players, recorders,
and the lookup/CPU routines. The following lines free the set receiver, loop
buffers, and the 12-slot mirror. `~ji_stop_all.()` deliberately leaves the
receiver and buffers allocated so it is safe to call before the explicit frees;
calling it twice is harmless. Re-evaluating `test_dual_loop.scd` also stops the
known old variables and frees old loop buffers.

---

## 9. CPU and failure modes

### CPU

Stage 4 runs four Tartini + four PitchShift chains when two live channels and
two loops are active, plus four `RecordBuf` voices only while recording. Stage 1
measured one chain at roughly a few percent; the expected Stage 4 load is well
under the 60% limit, but **the agent cannot measure it**. Read the printed
`CPU:` value on the human machine.

If it exceeds 60%:

1. Stop unused loops (`~ji_stop_loop.(n)`) and avoid recording four at once.
2. Confirm no old Stage 0/1/3 synth is still running.
3. Try a smaller `windowSize` only by recreating the player
   (`windowSize` is not a safe structural change after creation); it lowers
   grain cost but changes tone.
4. Do **not** add a server-side nearest-class lookup or a C++ UGen unless a
   measurement shows the control lookup is the bottleneck. The existing 20 Hz
   `Bus.get` lookup is inexpensive.

### Failure modes

| Symptom | Likely cause | Fix |
|---|---|---|
| `ERROR: test_dual_loop: the Stage 4 buses (8-18) are missing` | old `startup.scd`, or a partial allocation | Evaluate the Stage 4 `startup.scd`; for clean 8–18 numbering after a partial-allocation warning, restart the interpreter, reboot the server, evaluate again |
| `ERROR: ... a required SynthDef is missing` | startup was not evaluated, or it could not find `synthdefs/` | Evaluate `startup.scd` from disk; confirm the four listed files load |
| `FAILURE IN SERVER /n_free Node ... not found` after changing the device or rebooting | sclang still holds Synth objects from the previous server; the cleanup at the top of `test_dual_loop.scd` tries to free them | Harmless. The test creates fresh synths after the cleanup. Restart the interpreter for a clean slate. Make sure `startup.scd` was re-evaluated after the reboot so the SynthDefs are present |
| No sound after changing devices or input count | `ServerOptions` changes only apply after a server reboot; if output is BlackHole, SC alone is silent because BlackHole needs a monitor path | Reboot/re-evaluate `startup.scd`; check `inDevice`/`outDevice`; route BlackHole into Ableton and monitor Ableton on the MiniFuse 2 (`README_stage5.md`). The sine/`SoundIn` checks below are heard through Ableton track 1 |
| `[BRIDGE] set update` never prints | sclang on a different language port, or another app owns 57120 | Check `NetAddr.langPort`; restart Python with `--sc-port <port>` |
| Set updates arrive but no target changes | the held note's class survived, or the mutation only added a class elsewhere | Keep pressing `m`; watch Python's `removed=`/`new=` line |
| Audio is doubled/phasey | Stage 0/1/3 synth or a second autotune still running | Free it; `test_dual_loop.scd` stops only the known variables. Do not run `test_bridge.scd` concurrently |
| Two set receivers act on every update | a custom OSCdef from a previous test is still installed | Free it; re-evaluate `test_dual_loop.scd`, which frees `~ji_freqset_oscdef` |
| `hasFreq` stays 0.00 on a live channel | wrong input, low gain, or interface not selected | Raise interface gain, check `inDeviceName`, try `~ji_autotune_synth_ch0.set(\inputGain, 4)` |
| Loop records silence | wrong loop/input mapping, or `~ji_record_loop` was called for input 1 while playing into input 0 | Loop 1/3 = input 0; loop 2/4 = input 1. Check the `recording ... from input` line |
| `no buffer` on play after a successful recording | stale pre-fix `test_dual_loop.scd` functions, or the buffer was manually freed | Re-evaluate the whole `test_dual_loop.scd` (the current code uses `[i - 1]` for its 0-based loop arrays), then record again |
| Loop plays but never appears in status | `~ji_play_loop.()` was refused because recording was still active | Run `~ji_stop_record.(n)` first; watch for the `still recording` line |
| Loop plays but does not retune on mutation | lookup not running, loop voice inactive, or its class survived | Check `[LOOKUP]` and `[LOOP n] state=playing`; keep mutating; watch `[LOOP n] target=` |
| Loop retunes in the wrong octave | the recorded audio's octave changed, or the wrong buffer/loop index is in use | The lookup uses the measured loop pitch; re-record and re-check `[LOOP n] freq=` |
| Clicks at mutation time | glide too short for the interval | Raise `g` to 0.15–0.3; the minimum is clamped to 0.005 |
| Click once per loop cycle | the recorded phrase does not end on a zero crossing; Stage 4 has no loop-seam crossfade | Re-record a phrase that ends quietly, or accept the seam click; a crossfade is deferred |
| `RecordBuf` / `PlayBuf` argument error | wrong SC version or a malformed edit | SC 3.15 RecordBuf order is `in, bufnum, offset, recLevel, preLevel, run, loop, trigger, doneAction`; PlayBuf is `numChannels, bufnum, rate, trigger, startPos, loop, doneAction` |
| CPU above 60% | four loops + two live channels, or stale synths | See the CPU section above; stop unused loops and free old synths |

### Audio routing check (if there is no sound)

Run these after evaluating `startup.scd`, before blaming the autotune:

```supercollider
s.serverRunning.postln;
s.options.device.postln;          // string, or [in, out] when split
s.options.inDevice.postln;
s.options.outDevice.postln;
s.options.numInputBusChannels.postln;
s.options.numOutputBusChannels.postln;
ServerOptions.devices.postln;
ServerOptions.inDevices.postln;
ServerOptions.outDevices.postln;

// 1. Output only: should be a quiet 440 Hz tone through Ableton track 1
//    (SC writes BlackHole; it is not heard from SC alone).
x = { Out.ar(0, SinOsc.ar(440, 0, 0.1) ! 2) }.play;

// 2. Input passthrough, no autotune:
//    input 1 -> output 1, then input 2 -> output 2.
y = { Out.ar(0, SoundIn.ar(0)) }.play;
z = { Out.ar(1, SoundIn.ar(1)) }.play;

// Cleanup:
// x.free; y.free; z.free;
```

- With Stage 5, the sine is written to BlackHole and is audible only through
  Ableton track 1. If it is silent in Ableton, the problem is the **Ableton
  input/monitor path**, not Stage 4.
- If the sine works but `y`/`z` are silent, the problem is the **input device, input channel, or gain**.
- `SoundIn.ar(1)` requires `numInputBusChannels >= 2`. Stage 4 hardcodes hardware inputs 0 and 1; if your mic is on input 3/4, you must change `\inBus` / recorder `inputChannel` in `test_dual_loop.scd` or route the interface inputs to 1/2.
- If both tests work but `[AUTOTUNE ch=...] hasFreq` stays 0.00, the signal is reaching the server but not Tartini: check gain, play a steady pitch, and try raising `inputGain` on the live synth.

---

## 10. Limitations (Stage 4 scope)

- **Stage 5 provides the 16-channel infrastructure.** Stage 4's
  `test_dual_loop.scd` still writes only output pair 0/1 (Ableton track 1). The
  Stage 5.5 live rig gives loops 1/2 dedicated mono channels (SC 2/3); stereo
  and per-loop pair routing beyond that is deferred to Stage 8.
- **Mono loops.** Loop buffers and players are mono; stereo loop files, `.aiff`
  persistence, and stereo loop pairs are deferred.
- **No loop OSC control.** Loops are started/stopped from sclang only. Python
  loop transport is Stage 8.
- **No overdub, no loop-length editing, no crossfade at the loop seam.**
  Recording overwrites from frame 0.
- **No MIDI, MPE, UI, granular FX, VST, or bass/drone.**
- **The set receiver is sclang-side.** Python still sends only the Stage 3
  `/ji/freqset/update` payload; no new protocol and no direct Python → scsynth
  messages.
- **The loop player's first target is a remembered target.** On a loop's first
  play it uses the source live channel's last target; on later plays it uses the
  loop's own last target. Until the first Tartini lock (about 40–50 ms), the loop
  may briefly shift toward that remembered target. It then settles to the
  nearest current class.
- Stage 4 does not claim Stage 3.5 audible acceptance (batch width,
  temperature, anchor mobility); that remains pending separately.

---

## 11. Verification available in the agent environment

No SuperCollider or native Python is installed in the agent environment, so no
audio or sclang execution was possible. The unchanged Python control layer can
still be exercised on the human machine:

```bash
cd ji-live
uv run --with python-osc python test_mutation_controls.py
```

That confirms the Stage 3.5 controls still pass after the Stage 4 sclang-only
changes. The human run in §7 is the arbiter for the new audio work.

**Stage 4 is implemented — awaiting human audio acceptance.**

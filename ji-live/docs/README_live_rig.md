# JI Station — Live Rig (2 inputs + 2 loops → 4 outputs)

**Status:** implemented; **owner-confirmed 2026-10-30**, including the
adjustable loop length (8 s default, per-take override and
`~ji_set_loop_default`). **Stage 7/7.1 is implemented** (one window for mutation, live autotune,
loop transport/length, status, and Drone/Arp switches; see
`README_stage7.md` and design doc §8.8). This is the integration rig that
combines Stages 1–5.5 with the Python mutation bridge and the Stage 6 MIDI rig.

```text
MiniFuse 2 input 0 (mic)    → Tartini → nearest class → PitchShift → SC out 0 → Ableton track 1
MiniFuse 2 input 1 (guitar) → Tartini → nearest class → PitchShift → SC out 1 → Ableton track 2
input 0 (dry) → loop 1 → Tartini → nearest class → PitchShift → SC out 2 → Ableton track 3
input 1 (dry) → loop 2 → Tartini → nearest class → PitchShift → SC out 3 → Ableton track 4

Ableton master → MiniFuse 2 / headphones
Python MutationEngine → /ji/freqset/update → sclang lookup → all live + loop voices
```

Four **mono** outputs, not four stereo pairs. That is why the Ableton tracks are
mono tracks on BlackHole inputs 1, 2, 3, and 4.

---

## 1. Files

```text
ji-live/
├── startup.scd                       Stage 5: 2 in / 16 out, MiniFuse 2 / BlackHole 16ch
├── tests/sc/test_live_rig.scd        NEW: the four-channel live rig
├── tests/sc/test_dual_loop.scd       Stage 4: unchanged; pair 0/1 test
├── tests/sc/test_ableton_routing.scd Stage 5: 8-pair infrastructure test
├── config/live_rig_routing.json      NEW: SC channel ↔ Ableton track map
├── control/ji_bridge.py              Stage 3: unchanged Python bridge
├── synthdefs/*.scd                   unchanged
└── docs/README_live_rig.md           NEW: this file
```

`tests/sc/test_live_rig.scd` is a copy of the accepted Stage 4 orchestration with a new
output map and a distinct OSCdef key (`\ji_freqset_update_live_rig`). The
Stage 4 loop-index fix (`[i - 1]` for the 0-based arrays) is preserved. The
Stage 3 `/ji/freqset/update` contract and all Python files are unchanged.

The extra Stage 4 loop voices 3/4 remain allocated. If used, they sum into
SC 2 and SC 3. The four-channel product uses loops 1 and 2.

---

## 2. Prerequisites

- Stage 5 `startup.scd` evaluated: server running with
  **MiniFuse 2 input / BlackHole 16ch output**, 2 in / 16 out, 48 kHz,
  blockSize 64, buses 0–18, all SynthDefs loaded.
- Ableton Live open, input **BlackHole 16ch**, output **MiniFuse 2** (or its
  headphones), 48 kHz.
- Both devices at **48000 Hz** in Audio MIDI Setup. The startup uses
  `s.options.sampleRate = 0` (device rate) to avoid the macOS
  “server ready then exit code 0” issue; see `README_stage5.md` §4.
- Four **mono** Ableton tracks configured as in §3.
- Python 3.11+ via `uv`; `python-osc` is fetched by `uv run`.
- No Stage 0/1/3/4 test still running. `tests/sc/test_live_rig.scd` stops the known
  old routines and replaces `~ji_freqset_oscdef`.

---

## 3. Ableton setup: four mono tracks

1. **Preferences → Audio**
   - Audio Input Device: **BlackHole 16ch**
   - Audio Output Device: **MiniFuse 2** (or headphones on it)
   - Sample Rate: **48000 Hz**
   - Input Config: enable channels **1–16** (at minimum 1–4)
2. Create **four audio tracks**. Set each track's input to a **single mono
   channel**, not a stereo pair:

   | Track | Input | SC source | Content |
   |---|---|---|---|
   | 1 | BlackHole 16ch **1** | SC 0 | autotuned input 0 (mic) |
   | 2 | BlackHole 16ch **2** | SC 1 | autotuned input 1 (guitar) |
   | 3 | BlackHole 16ch **3** | SC 2 | loop 1 (autotuned playback) |
   | 4 | BlackHole 16ch **4** | SC 3 | loop 2 (autotuned playback) |

3. On each track: **Monitor = In**, **Audio To = Master**.
4. Master output → **MiniFuse 2** outputs 1/2 (or the headphone outputs).
5. Do **not** route the master or any track back to BlackHole 16ch. That causes
   feedback.

If Live only offers a stereo input selector for BlackHole, choose the mono
channel entry (`1`, `2`, `3`, `4`); if your version insists on a stereo pair,
use a Utility/mono device or select 1/2 and pan, but the intended setup is four
mono tracks.

The machine-readable map is [config/live_rig_routing.json](../config/live_rig_routing.json).
The separate 8-pair infrastructure test map is
[config/ableton_routing.json](../config/ableton_routing.json); do not confuse the
two.

---

## 4. Start order

### a. SuperCollider: boot

Evaluate [startup.scd](../startup.scd) and confirm the Stage 5 lines:

```text
[DEVICE] input found: MiniFuse 2
[DEVICE] output found: BlackHole 16ch
inDevice=MiniFuse 2 outDevice=BlackHole 16ch
Audio I/O: 2 in / 16 out, 48000 Hz, blockSize 64
```

If you see a sample-rate warning, set both devices to 48000 Hz in Audio MIDI
Setup, quit the server, and re-evaluate.

### b. SuperCollider: start the live rig

Evaluate the whole of [tests/sc/test_live_rig.scd](../tests/sc/test_live_rig.scd) (Cmd+Return).
Confirm lines like:

```text
[BRIDGE] sclang language OSC port = 57120
[BRIDGE] Hz mirror allocated: bufnum=0 slots=12
[AUTOTUNE] ch0 started (node 1000) inBus=0 outBus=0
[AUTOTUNE] ch1 started (node 1001) inBus=1 outBus=1
[BRIDGE] /ji/freqset/update receiver installed (key=\ji_freqset_update_live_rig), initial set size=7 fund=220.00
[LOOP] buffers allocated: 1=1 2=2 3=3 4=4 (8.00 s mono each)
[LOOP] default record length: 8.00 s. ~ji_set_loop_default.(12.0) changes it; ~ji_record_loop.(i, seconds) overrides one take.
[LOOP] tracker buses: loop_freq=[ 11, 12, 13, 14 ] loop_hasfreq=[ 15, 16, 17, 18 ]
[LOOKUP] multi-voice lookup running at 20 Hz for 6 voices
[RIG] Ready. SC 0 = autotuned input 0, SC 1 = autotuned input 1, SC 2 = loop 1, SC 3 = loop 2.
```

The live status prints once per second:

```text
[AUTOTUNE ch=0] freq=220.31 target=220.00 hasFreq=0.91 shift=-0.02st setSize=7 fund=220.00
[AUTOTUNE ch=1] freq=329.63 target=330.00 hasFreq=0.88 shift=0.02st setSize=7 fund=220.00
CPU: 12.3%
```

### c. Terminal: Python mutation bridge

From `ji-live/`:

```bash
uv run --with python-osc python control/ji_bridge.py --interactive --glide-time 0.15
```

Interactive commands:

```text
m / Enter        mutate the set
o                octave event
c <n>            changes per batch
t <0..1>         temperature
a <0..1>         anchor mobility
g <seconds>      glide time
s                show the current set
q                quit
```

If the bridge started before sclang was listening, press `m` once; the first
mutation sends the complete set. Every mutation sends exactly:

```text
/ji/freqset/update  fundamental  glide_time  ratio_1 ... ratio_N
```

No Python or protocol change was made for the live rig.

---

## 5. Play and loop

Play/sing into MiniFuse 2 inputs 1 and 2. Ableton tracks 1 and 2 should show
the autotuned signals and the `[AUTOTUNE ch=...]` lines should show
`hasFreq > 0.5`.

### Loop length is adjustable

The live rig defaults to **8.0 seconds** per loop. Recording always reallocates
the buffer to exactly the requested length.

```supercollider
~ji_loop_default_seconds;         // 8.0
~ji_record_loop.(1);              // loop 1, current default (8 s)
~ji_record_loop.(1, 16.0);        // loop 1, one 16 s take
~ji_set_loop_default.(12.0);      // future ~ji_record_loop.(i) calls use 12 s
~ji_record_loop.(2);              // loop 2, 12 s
```

- `~ji_record_loop.(index, seconds)` accepts any positive length.
- `~ji_set_loop_default.(seconds)` changes the default for later recordings.
- `~ji_record_loop.(index)` uses `~ji_loop_default_seconds`.
- The initial four buffers are 8 s placeholders; recording replaces the used
  one with a buffer of the requested length.
- Memory is `seconds * 48000 * 4 bytes` per mono loop: about 1.5 MB at 8 s,
  3.1 MB at 16 s, 5.8 MB at 30 s.
- Recording still overwrites from frame 0; it is not overdub. An early
  `~ji_stop_record.(1)` keeps the partly filled buffer, and the remainder is
  silence. Let the full requested time elapse for a seamless loop.

Record loop 1 from input 0:

```supercollider
~ji_record_loop.(1);         // 8 s default; records input 0 -> loop 1
// or: ~ji_record_loop.(1, 16.0);
~ji_stop_record.(1);         // optional early stop
~ji_play_loop.(1);           // loop 1 -> SC 2 -> Ableton track 3
```

Record loop 2 from input 1:

```supercollider
~ji_record_loop.(2);         // 8 s default; records input 1 -> loop 2
~ji_stop_record.(2);
~ji_play_loop.(2);           // loop 2 -> SC 3 -> Ableton track 4
```

Stop and clean up:

```supercollider
~ji_stop_loop.(1);
~ji_stop_loop.(2);
~ji_stop_all.();
```

Loop commands and the 0-based array behavior are the accepted Stage 4
functions. Loop 1 records input 0; loop 2 records input 1. Loops 1/2 are mono
and are summed only onto their own SC output channel (2 and 3). They do not
sum with the live channels.

The `[LOOP n] playing ...` line prints the buffer duration; use it to confirm
the requested length. The `[LOOP n] recording ...` line prints the length and
input channel.

---

## 6. Mutate the set and confirm retuning

With both live inputs and at least one loop playing, press `m` in the Python
terminal. Confirm:

- `[BRIDGE] set update: size=... fundamental=... ratios=...` in sclang;
- the live `[AUTOTUNE ch=...] target=` values change on the next 20 Hz lookup
  tick;
- a playing `[LOOP 1] target=` / `[LOOP 2] target=` changes without restarting
  playback;
- the transition uses the event glide (`g` changes it; minimum 0.005);
- nothing needs to be re-recorded or replayed.

`tests/sc/test_live_rig.scd` uses the accepted Stage 3 nearest-class arithmetic:
fold the measured frequency into the current octave, compare against the
current classes plus `2/1`, and put the voice's own octave back. Every voice
keeps its own `lastTarget`.

---

## 7. Expected routing

| SC output | Content | Ableton track | Ableton input |
|---|---|---|---|
| 0 | autotuned input 0 | 1 | BlackHole 1 (mono) |
| 1 | autotuned input 1 | 2 | BlackHole 2 (mono) |
| 2 | loop 1 autotuned | 3 | BlackHole 3 (mono) |
| 3 | loop 2 autotuned | 4 | BlackHole 4 (mono) |

The first four BlackHole channels are used. The remaining 12 Stage 5 output
channels are unused by this rig.

**Live rig vs Stage 5 infrastructure:** the Stage 5 8-pair map
([config/ableton_routing.json](../config/ableton_routing.json)) reserves 0–1 for a
live mix, 2–3 for Loop 1, etc. This live rig is the immediate product map:
0/1 are the two separate live channels and 2/3 are the two loops. Both are
valid; do not apply the 8-pair track layout while running this rig.

---

## 8. Stop

In Python: `q` (or Ctrl+C).

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

`~ji_stop_all.()` stops the live channels, loop players, recorders, and the
lookup/CPU routines. The explicit lines free the set receiver, loop buffers,
and the 12-slot mirror. Re-evaluating `tests/sc/test_live_rig.scd` also cleans up the
known old variables and old loop buffers.

---

## 9. Failure modes

| Symptom | Likely cause | Fix |
|---|---|---|
| `test_live_rig: the server is not running` | startup not evaluated, or server exited | Boot `startup.scd`; check the sample-rate warning; confirm 2 in / 16 out |
| `test_live_rig: a required SynthDef is missing` | startup was not evaluated from disk | Evaluate `startup.scd` and confirm all four files load |
| No live signal on tracks 1/2 | Ableton input is a stereo pair, tracks are not mono, or wrong BlackHole channels | Set track 1 to BlackHole input **1**, track 2 to input **2**, Monitor In |
| Loop 1 plays on track 1 or 2 instead of 3 | old `tests/sc/test_live_rig.scd` or `tests/sc/test_dual_loop.scd` still running | Re-evaluate the current `tests/sc/test_live_rig.scd`; `~ji_loop_outbus` must be `[2, 3, 2, 3]` |
| Loop 2 is silent | loop 2 was not recorded, or is still recording | Check `[LOOP 2] state=`; run `~ji_stop_record.(2)` then `~ji_play_loop.(2)` |
| `[BRIDGE] set update` never prints | Python not running, or wrong language port | Start the bridge; check `NetAddr.langPort` (normally 57120); use `--sc-port` if needed |
| Mutations arrive but targets never change | the held class survived, or lookup not running | Keep pressing `m`; check `[LOOKUP]` and `[BRIDGE] set update` |
| A loop does not retune | loop is not playing, or its class survived | Check `[LOOP n] state=playing` and `target=`; mutate again |
| Sound is doubled / phasey | old Stage 0/1/3/4 synth still running | Re-evaluate `tests/sc/test_live_rig.scd` (it stops known variables) or restart the interpreter |
| Feedback | Ableton master routed to BlackHole, or speakers feeding the mic | Master to MiniFuse only; use headphones |
| Server exits after “server ready” | forced `s.options.sampleRate` | Keep `s.options.sampleRate = 0`; set both devices to 48 kHz; see `README_stage5.md` §4 |
| No sound at all | BlackHole is virtual and Ableton is not monitoring it | Enable inputs 1–4, Monitor In on tracks 1–4, master to MiniFuse 2 |

---

## 10. Limitations

- Four **mono** channels; not four stereo pairs.
- Two live inputs only. The extra Stage 4 loop voices 3/4 are optional and sum
  into SC 2/3.
- Loop length is adjustable (`~ji_set_loop_default.` or per-take seconds), but
  recording is overwrite-only, mono, in-memory; no overdub, persistence, or
  crossfade. Memory scales with length.
- No per-loop stereo routing, MIDI/MPE mutation, granular FX (Stage 9), or UI
  (Stage 7, next).
- The Python mutation engine and `/ji/freqset/update` protocol are unchanged.
- The 48 kHz rate comes from the two devices; `s.options.sampleRate` is not
  forced.
- **Audio acceptance belongs to the human.** The agent environment has no
  SuperCollider, scsynth, Ableton, or Python audio path.

---

## 11. Acceptance checklist

1. Startup prints `inDevice=MiniFuse 2 outDevice=BlackHole 16ch`,
   `Audio I/O: 2 in / 16 out, 48000 Hz, blockSize 64`, and no device warning.
2. `tests/sc/test_live_rig.scd` prints two `[AUTOTUNE] ... started` lines, the
   `\ji_freqset_update_live_rig` receiver, four loop buffers, and
   `[RIG] Ready`.
3. Ableton tracks 1 and 2 receive the two live autotuned inputs; tracks 3 and
   4 receive loops 1 and 2.
4. Python starts; pressing `m` prints `[BRIDGE] set update` and retunes both
   live channels and any playing loop without replay.
5. Recording loop 1 from input 0 and loop 2 from input 1 works at the default
   8 s; `~ji_record_loop.(1, 16.0)` and `~ji_set_loop_default.(12.0)` change
   the length; playback appears on SC 2 and 3 / tracks 3 and 4.
6. CPU is below 60% with 2 live + 2 loops (read the `CPU:` line).
7. No clicks or dropouts during mutations or loop start/stop; if a loop seam
   clicks once per cycle, re-record a phrase ending quietly.
8. The owner reported all sound checks passed on 2026-10-30. The loop-length
   control is a post-acceptance enhancement; confirm it once in the live rig.

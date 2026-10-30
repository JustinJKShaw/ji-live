# JI Station — Stage 5 (BlackHole 16ch + Ableton audio routing)

**Status:** Stage 5 is **implemented**, and the project owner reported **all
sound checks passed on 2026-10-30** (8-pair routing and the four-channel live
rig). The agent environment cannot run SuperCollider, scsynth, Ableton, or hear
audio; the acceptance is the owner's report. The adjustable loop-length control
was added after that report as an enhancement.

> **Live-rig integration (Stage 5.5):** [test_live_rig.scd](test_live_rig.scd)
> now combines the two live autotunned inputs and two loops on **SC 0, 1, 2, 3**
> with the Python mutation bridge. That is the immediate four-channel product;
> see [README_live_rig.md](README_live_rig.md) and
> [config/live_rig_routing.json](config/live_rig_routing.json). This Stage 5
> document remains the 16-channel infrastructure/monitoring reference.

Stage 5 creates the monitoring path through which the deferred Stage 4 sound
check is finally heard:

```text
MiniFuse 2 (2 inputs)
  → scsynth: Tartini / PitchShift / loops
  → SC output pair 0/1 ... 14/15
  → BlackHole 16ch (virtual, 16 channels)
  → Ableton Live (input BlackHole 16ch, inputs 1–16 enabled)
  → Ableton master output
  → MiniFuse 2 / wired headphones
```

The primary configuration uses **separate input and output devices** —
`inDevice_("MiniFuse 2")` and `outDevice_("BlackHole 16ch")`. **No Aggregate
Device is required.** An Aggregate is documented only as a fallback.

---

## 1. Files

```text
ji-live/
├── startup.scd                     MODIFIED: separate devices, 2 in / 16 out
├── test_ableton_routing.scd        NEW: 1 kHz tone on each of 8 stereo pairs
├── config/ableton_routing.json     NEW: SC channel ↔ Ableton track map
├── README_stage5.md                NEW: this file
├── README_stage4.md                UPDATED: Stage 5 device/monitor path
└── design_doc_10.28.10.50          UPDATED: §3.6 / §4.2 / §6.10 / §8.6 / §9 / §14
```

Related Stage 5.5 live-rig files (see [README_live_rig.md](README_live_rig.md)):
`test_live_rig.scd`, `config/live_rig_routing.json`. Those use four mono
outputs (SC 0–3) rather than the 8-pair infrastructure map in this document.

Unchanged and not to be modified for Stage 5:

- `synthdefs/ji_autotune_ch.scd` (verified DSP)
- `test_bridge.scd`, `test_autotune.scd`, `test_tartini.scd` (accepted)
- `control/*.py` and the Stage 2/3 Python tests
- `test_dual_loop.scd` (Stage 4 loop-index behavior stays as fixed)
- the Stage 3 `/ji/freqset/update` contract

Stage 5 adds **no** OSC address, no Python change, no new SynthDef file, no
server-side nearest-class lookup, and no Ableton API automation.

---

## 2. Prerequisites

- macOS with SuperCollider 3.15 (sc3-plugins/Tartini working from Stages 0–1).
- **MiniFuse 2** connected and working as the 2-channel input device.
- **BlackHole 16ch** installed from <https://existential.audio/blackhole/>
  (the 16-channel build — not BlackHole 2ch or 64ch).
- **Ableton Live** installed. Live 11+ is preferred; separate audio
  input/output devices and 16 input channels are required.
- Both the MiniFuse 2 and BlackHole 16ch set to **48000 Hz** in Audio MIDI
  Setup.
- Wired headphones on the MiniFuse 2 (or its main outputs). Do **not** use
  Bluetooth headphones for this test.
- No Stage 0/1/3/4 audio still running while you measure the routing test, and
  no second app monitoring BlackHole.

---

## 3. No Aggregate Device (primary path)

scsynth can open **one device for input and a different device for output**:

```supercollider
s.options.inDevice_("MiniFuse 2");
s.options.outDevice_("BlackHole 16ch");
```

When the two names differ, scsynth is launched with `-H <in> <out>`. This is the
Stage 5 primary path. `device_("...")` sets **both** directions to one device and
is used only by the single-device fallback in §9.

Use the exact names from these lists — a typo makes scsynth fall back or fail:

```supercollider
ServerOptions.inDevices.postln;    // must contain MiniFuse 2
ServerOptions.outDevices.postln;   // must contain BlackHole 16ch
ServerOptions.devices.postln;      // all devices
```

If a name is not found, `startup.scd` prints a warning and the available list.
It does **not** silently substitute another physical device.

---

## 4. SuperCollider startup configuration

The top of `startup.scd` now contains:

```supercollider
var inDeviceName  = "MiniFuse 2";       // 2 inputs
var outDeviceName = "BlackHole 16ch";   // 16 outputs = 8 stereo pairs
var singleDeviceName = "REPLACE_ME";    // fallback only; see §9

s.options.numInputBusChannels  = 2;
s.options.numOutputBusChannels = 16;
s.options.blockSize            = 64;
// 0 is SC's default: use the devices' Audio MIDI Setup rate.
// Do NOT force 48000 here with BlackHole as a separate output device.
s.options.sampleRate = 0;

s.options.inDevice_(inDeviceName);
s.options.outDevice_(outDeviceName);
```

The running server uses the devices' Audio MIDI Setup rate, and `startup.scd`
checks `s.sampleRate` after boot. If it is not 48000 Hz, it prints a warning;
set both devices to 48 kHz and reboot. The required print is still
`Audio I/O: 2 in / 16 out, 48000 Hz, blockSize 64` when the devices are at
48 kHz.

**Known macOS issue:** forcing `s.options.sampleRate = 48000` with a separate
BlackHole output has caused scsynth to exit immediately with code 0 after
"server ready" (see [this scsynth.org thread](https://scsynth.org/t/booting-sc-reliably-with-different-in-and-out-devices/12878)).
That is why the file uses `s.options.sampleRate = 0` (device rate) and sets
48 kHz in Audio MIDI Setup. The explicit `0` also clears any old forced value
when `startup.scd` is re-evaluated. The device rate, not the server option, is
authoritative here.

**Reboot requirement.** These are `ServerOptions`; changing them while scsynth
is running has no effect. After editing or re-evaluating `startup.scd`:

```supercollider
s.quit;          // or s.reboot
// then evaluate startup.scd again from disk
```

The control-bus allocation is unchanged: **0–18**, exactly as in Stage 4.
Audio output buses are now 0–15. Stage 4 live/loop audio still uses only
**pair 0/1**.

### Expected startup output

```text
Available input devices:  [ ... MiniFuse 2 ... ]
Available output devices: [ ... BlackHole 16ch ... ]
Audio devices: in=MiniFuse 2 out=BlackHole 16ch (separate)
[DEVICE] input found: MiniFuse 2
[DEVICE] output found: BlackHole 16ch
Stage 4 buses allocated (8-18).
JI system buses allocated: freq1=0 freq2=1 hasfreq1=2 hasfreq2=3 cpu=4 autotune_freq1=5 autotune_hasfreq1=6 autotune_target1=7 autotune_freq2=8 autotune_hasfreq2=9 autotune_target2=10 loop_freq=[ 11, 12, 13, 14 ] loop_hasfreq=[ 15, 16, 17, 18 ]
Loading 4 SynthDef file(s) from .../synthdefs
  - ji_autotune_ch.scd
  - ji_looper_player.scd
  - ji_looper_recorder.scd
  - ji_tartini_debug.scd
inDevice=MiniFuse 2 outDevice=BlackHole 16ch
Audio I/O: 2 in / 16 out, 48000 Hz, blockSize 64
Server device setting: [ MiniFuse 2, BlackHole 16ch ]
Startup complete (Stage 5: 2 in / 16 out, buses 0-18).
Next: test_ableton_routing.scd (Stage 5), then test_dual_loop.scd (Stage 4).
```

The exact device-list formatting and bus order vary slightly. The required
diagnostics are:

- `inDevice=MiniFuse 2 outDevice=BlackHole 16ch`
- `Audio I/O: 2 in / 16 out, 48000 Hz, blockSize 64`
- bus indices 0–18, all distinct
- four SynthDef files loaded

If you see a warning that a device was not found, fix the name or the device
before continuing. If scsynth fails to boot with `inDevice`/`outDevice`, see the
Aggregate fallback in §9.

---

## 5. Audio MIDI Setup (macOS)

1. Open **Audio MIDI Setup** (`/Applications/Utilities`).
2. Select **MiniFuse 2** in the sidebar → set **Format** to **48000 Hz** and
   confirm 2 input / 2 output channels.
3. Select **BlackHole 16ch** → set **Format** to **48000 Hz** and confirm
   16 output channels.
4. Do **not** create an Aggregate Device for the primary path.
5. Optional: set the Mac's system output to the MiniFuse 2 for normal computer
   audio, but keep BlackHole **out** of the system output while testing — see
   the feedback note in §10.

Both devices must run at 48 kHz. A 44.1 kHz device will be resampled or will
produce pitch/speed errors, and independent clocks can drift.

---

## 6. Ableton Live setup

### 6.1 Audio preferences

Open **Live → Preferences → Audio**:

| Setting | Value |
|---|---|
| Audio Input Device | **BlackHole 16ch** |
| Audio Output Device | **MiniFuse 2** (or its headphones) |
| Sample Rate | **48000 Hz** |
| Buffer Size | 128 or 256 samples (64 is possible but less forgiving) |
| Input Config | enable **all 16** BlackHole input channels |
| Output Config | MiniFuse 2 outputs 1/2 (or the headphone outputs) |

In **Input Config**, click the **Configure** / channel-enable area and turn on
channels **1–16**. If only 1/2 is enabled, only track 1 can receive audio — the
most common Stage 5 failure.

If your Live version does not offer separate **Audio Input Device** and
**Audio Output Device** selectors, use the Aggregate fallback in §9 rather than
changing the SC path.

### 6.2 Eight stereo tracks

Create eight **audio tracks** and set each one's input to its BlackHole pair:

| Track | Name | Audio From | Input channels | Monitor |
|---|---|---|---|---|
| 1 | Input Autotuned | BlackHole 16ch | 1/2 | **In** |
| 2 | Loop 1 (reserved) | BlackHole 16ch | 3/4 | **In** |
| 3 | Loop 2 (reserved) | BlackHole 16ch | 5/6 | **In** |
| 4 | Loop 3 (reserved) | BlackHole 16ch | 7/8 | **In** |
| 5 | Loop 4 (reserved) | BlackHole 16ch | 9/10 | **In** |
| 6 | Granular (Stage 9) | BlackHole 16ch | 11/12 | **In** |
| 7 | Dry / diagnostic | BlackHole 16ch | 13/14 | **In** |
| 8 | Reserved | BlackHole 16ch | 15/16 | **In** |

- Set every track's **Audio To** to **Master**.
- Set **Monitor** to **In** so the input is heard without arming. If your Live
  version requires arming for monitoring, arm the track (do not record).
- Keep each track's fader at a safe level; the test tone is quiet
  (~ -22 dBFS per channel).
- Do **not** route any track or the master back to BlackHole 16ch. That creates
  a feedback loop.

### 6.3 Master output

Set the **Master** track's output to **MiniFuse 2** outputs 1/2 (or the
MiniFuse headphone outputs). This is what makes BlackHole audible. SC writes to
BlackHole; Ableton is the monitor mixer.

---

## 7. Run the routing test

With scsynth booted from the Stage 5 `startup.scd`, evaluate the whole of
`test_ableton_routing.scd` (Cmd+Return).

It checks:

- `s.serverRunning`;
- `s.options.numOutputBusChannels == 16`;
- stops its own previous routine and synth;
- plays a 1 kHz sine at 0.08 amplitude on each pair, about 1 second per pair,
  with a 50 ms release and an explicit free before the next pair.

### Expected output

```text
[ROUTING] 16-channel output test: 1 kHz sine, one stereo pair at a time
[ROUTING] outDevice=BlackHole 16ch numOutputBusChannels=16
[ROUTING] BlackHole is virtual: SC alone is silent. Ableton must monitor BlackHole
[ROUTING] and send its master to the MiniFuse 2 / headphones.
[ROUTING] 8 pairs, about 1.0 s each. ...
[ROUTING] running.
[ROUTING] pair 1 (Ableton track 1) -> SC ch 0/1
[ROUTING] pair 2 (Ableton track 2) -> SC ch 2/3
[ROUTING] pair 3 (Ableton track 3) -> SC ch 4/5
[ROUTING] pair 4 (Ableton track 4) -> SC ch 6/7
[ROUTING] pair 5 (Ableton track 5) -> SC ch 8/9
[ROUTING] pair 6 (Ableton track 6) -> SC ch 10/11
[ROUTING] pair 7 (Ableton track 7) -> SC ch 12/13
[ROUTING] pair 8 (Ableton track 8) -> SC ch 14/15
[ROUTING] done
```

In Ableton, the matching track's meter should move in order, one track at a
time, and the tone should be audible through the master output to the MiniFuse
2/headphones. A short gap between pairs is normal.

If track 1 shows extra material, Stage 4 audio is still running on pair 0/1.
Stop it first:

```supercollider
~ji_stop_all.();
```

### Stop path

The test runs for about nine seconds and then stops itself. To stop early:

```supercollider
~ji_ableton_routing_routine.stop;
~ji_ableton_routing_synth.free;
```

Re-evaluating `test_ableton_routing.scd` performs the same cleanup first, so it
is safe to run repeatedly. The test does not use Python, the mutation engine, or
the Stage 4 set receiver.

---

## 8. Why SC alone is silent (the monitor path)

BlackHole is a **virtual loopback device**. It has no speakers. scsynth writing
to BlackHole produces no audible sound until another application reads BlackHole
and sends it to a physical output.

The required path is:

```text
scsynth → BlackHole 16ch → Ableton input (1–16) → Ableton master → MiniFuse 2
```

If you run SC alone, or Ableton is not monitoring BlackHole, or Ableton's master
is muted / routed elsewhere, the rig is silent even though SC's meters and
SynthDefs are fine. This is expected, not a Stage 5 failure.

Do not also select BlackHole as the macOS system output or monitor it with a
second app while Ableton is monitoring it; that doubles the signal or feeds
back.

---

## 9. Shared devices, concurrency, and the Aggregate fallback

### 9.1 Both apps use both devices, in opposite directions

| Application | MiniFuse 2 | BlackHole 16ch |
|---|---|---|
| scsynth | **input** (2 ch) | **output** (16 ch) |
| Ableton Live | **output** (master/headphones) | **input** (16 ch) |

CoreAudio normally allows this; BlackHole is designed for multiple clients. Some
audio-interface drivers do not allow one app to open the input while another
opens the output of the same physical device. If either app cannot start audio
with this split, use the Aggregate fallback below.

### 9.2 Aggregate fallback (not the primary path)

Use an Aggregate only if separate devices fail on this macOS/SC/Live version.
Two variants:

**A. Aggregate for Ableton only** (least invasive; SC keeps separate devices):

1. Audio MIDI Setup → **+** → **Create Aggregate Device**.
2. Check **BlackHole 16ch** and **MiniFuse 2**.
3. Set the aggregate's clock source to **MiniFuse 2** and its sample rate to
   **48000 Hz**.
4. In Ableton, select the **Aggregate** as both Audio Input Device and Audio
   Output Device. Enable the aggregate inputs that correspond to BlackHole
   1–16, and route the master to the aggregate outputs that correspond to
   MiniFuse 2.
5. Leave `startup.scd` on the separate `inDevice_`/`outDevice_` path.

**B. Aggregate for scsynth** (only if `-H` / separate devices fail):

1. Create the aggregate as above.
2. In `startup.scd`, set:

   ```supercollider
   var inDeviceName  = "REPLACE_ME";
   var outDeviceName = "REPLACE_ME";
   var singleDeviceName = "JI Aggregate";   // exact aggregate name
   ```

   `startup.scd` then calls `s.options.device_("JI Aggregate")` for both
   directions and keeps 2 input / 16 output bus channels.
3. **Verify the aggregate channel order.** An aggregate may list MiniFuse 2
   outputs before BlackHole's 16 outputs, which shifts SC's bus 0/1 away from
   BlackHole channel 1/2. Check the aggregate's channel list in Audio MIDI
   Setup and adjust the Ableton track inputs and
   `config/ableton_routing.json` to the actual order. Do not assume BlackHole is
   aggregate channels 1–16.
4. Aggregate devices add latency and can drift if their clock sources disagree;
   use the MiniFuse 2 as clock master and re-run the 5-minute check.

The separate-device path in §4 is the primary, supported configuration.

---

## 10. 5-minute drift / dropout check

Separate devices have independent clocks. A short test can pass while a longer
run drifts, clicks, or drops out.

1. Confirm both devices show **48000 Hz** in Audio MIDI Setup and in Live.
2. Run `test_ableton_routing.scd` once to confirm all eight pairs.
3. Play a sustained tone on pair 0/1 for five minutes:

   ```supercollider
   x = { Out.ar(0, SinOsc.ar(1000, 0, 0.05) ! 2) }.play;
   // listen for 5 minutes, then:
   x.free;
   ```

4. Alternatively, leave `test_dual_loop.scd` running with live input for five
   minutes.
5. Listen for pitch drift, crackles, dropouts, or a growing latency offset.
6. Record: start/end time, buffer size, Live CPU meter, SC `CPU:` line, number
   of dropouts, and whether both devices stayed at 48 kHz.
7. If drift/dropouts appear only in separate-device mode, reproduce with the
   Aggregate fallback (§9.2) and note the result.

The agent cannot perform this run; it is part of human acceptance.

---

## 11. Channel map

The reserved map is in `config/ableton_routing.json`; the design doc reference
is §4.2.

| SC output bus | Ableton track | Ableton input channels | Content |
|---|---|---|---|
| 0–1 | 1 | 1/2 | live autotuned stereo mix (Stage 4 current audio) |
| 2–3 | 2 | 3/4 | Loop 1 (reserved — Stage 8) |
| 4–5 | 3 | 5/6 | Loop 2 (reserved — Stage 8) |
| 6–7 | 4 | 7/8 | Loop 3 (reserved — Stage 8) |
| 8–9 | 5 | 9/10 | Loop 4 (reserved — Stage 8) |
| 10–11 | 6 | 11/12 | Granular FX (Stage 9, reserved) |
| 12–13 | 7 | 13/14 | dry/diagnostic (reserved) |
| 14–15 | 8 | 15/16 | reserved |

SC bus indices are **0-based**; Ableton hardware input channels are **1-based**.
SC bus 0/1 is Ableton input 1/2.

This is the **8-pair infrastructure map**, not the live-rig product map. For the
four-channel live rig (SC 0/1 live inputs, SC 2/3 loops), see
[README_live_rig.md](README_live_rig.md) and
[config/live_rig_routing.json](config/live_rig_routing.json).

**Stage 5 scope boundary:** this stage verifies the 16-channel infrastructure.
Stage 4 live/loop audio stays on **pair 0/1** (Ableton track 1) for the first
sound check. Stage 4's mono loops currently sum into pair 0/1; they do **not**
appear on tracks 2–5. Per-loop dedicated pair routing is deferred to Stage 8,
when loop transport is implemented. Do not read the reserved rows as delivered
routing.

---

## 12. Stage 4 sound check through Ableton

After the routing test passes, complete the deferred Stage 4 audible acceptance.
The Stage 5.5 live rig ([README_live_rig.md](README_live_rig.md)) is the
preferred four-channel sound-check path; this section uses the unchanged
`test_dual_loop.scd` pair-0/1 test. Either confirms the Stage 4 loop/autotune
behavior, but the live rig gives each input and loop its own Ableton track.

1. In Ableton, confirm track 1 is **BlackHole 16ch 1/2**, **Monitor In**, output
   to Master, and the master goes to the MiniFuse 2/headphones.
2. Evaluate the whole of `test_dual_loop.scd` (it still uses output pair 0/1).
3. Confirm the expected Stage 4 lines: two `[AUTOTUNE] ... started`, four loop
   buffer numbers, tracker buses 11–18, `[LOOKUP]`, and `[STAGE4] Ready`.
4. Play/sing into **MiniFuse 2 input 1** and **input 2**. Confirm live autotune
   on Ableton track 1 and watch the `[AUTOTUNE ch=...]` lines.
5. Start Python only when needed:

   ```bash
   cd ji-live
   uv run --with python-osc python control/ji_bridge.py --interactive --glide-time 0.15
   ```

6. Mutate and confirm the live channels retune. Record a loop and play it back:
   the loop is audible on **track 1** as well (pair 0/1), not on tracks 2–5.
7. Stop the rig:

   ```supercollider
   ~ji_stop_all.();
   ~ji_dual_lookup_routine.stop;
   ~ji_dual_cpu_routine.stop;
   ~ji_freqset_oscdef.free;
   ~ji_buf_loop1.free; ~ji_buf_loop2.free;
   ~ji_buf_loop3.free; ~ji_buf_loop4.free;
   ~ji_buf_freqset.free;
   ```

The human must hear Stage 4's live autotune through Ableton track 1 before
Stage 4 or Stage 5 is called accepted.

---

## 13. Failure modes

| Symptom | Likely cause | Fix |
|---|---|---|
| `WARNING: output device not found: 'BlackHole 16ch'` | BlackHole not installed, wrong build (2ch/64ch), or a different name | Install BlackHole 16ch; use the exact name from `ServerOptions.outDevices` |
| `WARNING: input device not found: 'MiniFuse 2'` | Interface unplugged or named differently | Reconnect; check `ServerOptions.inDevices`; fix `inDeviceName` |
| scsynth prints `server ready` then `Server 'localhost' exited with exit code 0` | `s.options.sampleRate` (or `hardwareBufferSize`) forced with a separate BlackHole output | Set `s.options.sampleRate = 0` and leave `hardwareBufferSize` nil; set both devices to 48 kHz in Audio MIDI Setup; reboot. See §4. |
| scsynth fails to boot after setting `inDevice_`/`outDevice_` | Separate-device `-H` not supported by this macOS/SC build | Use the Aggregate fallback (§9.2); do not silently switch devices |
| Startup still prints `2 in / 2 out` | old `startup.scd`, or ServerOptions changed on a running server | Re-evaluate the Stage 5 file from disk, `s.quit`, then evaluate again |
| Tone is silent in Ableton but SC is running | BlackHole is virtual; Ableton is not monitoring it | Select BlackHole 16ch as Live's input, enable 1–16, Monitor In, master to MiniFuse 2 |
| Only track 1 meters; tracks 2–8 silent | Ableton Input Config has only channels 1/2 enabled, or SC booted with 2 outputs | Enable inputs 1–16; confirm `Audio I/O: 2 in / 16 out` and `numOutputBusChannels == 16` |
| Ableton meters move but nothing is audible | Master output is not the MiniFuse 2, master is muted, or headphones are on another output | Set master output to MiniFuse 2 1/2; check its physical volume/headphone jack |
| All eight tracks meter at once | Duplicate monitoring or a second app also reading BlackHole | Close other BlackHole clients; monitor each pair on one track only |
| Feedback / runaway level | Ableton master or a track is routed back to BlackHole 16ch, or macOS monitors BlackHole | Route master to MiniFuse 2 only; remove BlackHole from system output |
| Wrong pitch / speed, or slow drift | One device is at 44.1 kHz, or independent clocks are drifting | Set both to 48 kHz; restart Live/SC; run the 5-minute check; consider Aggregate fallback |
| Channels swapped (track 2 shows track 1) | SC 0-based bus vs Ableton 1-based input, or wrong pair selected | Recheck `config/ableton_routing.json`; track N = BlackHole 2N-1/2N |
| `FAILURE IN SERVER /n_free Node ... not found` when re-running the test | the previous tone synth was already freed | Harmless; re-evaluating the test cleans up and continues |
| Stage 4 loops do not appear on tracks 2–5 | expected Stage 5 scope | Per-loop routing is Stage 8; loops currently sum on pair 0/1 (track 1) |
| `test_ableton_routing` errors `expected 16` | startup was not re-evaluated after the Stage 5 change | Reboot and re-evaluate `startup.scd`; confirm `Audio I/O: 2 in / 16 out` |

---

## 14. Limitations (Stage 5 scope)

- **Infrastructure only.** Stage 5 validates the separate-device 16-channel path
  with test tones. It does not reroute Stage 4 loops.
- **Stage 4 audio stays on pair 0/1.** Live autotune and all currently playing
  loops sum on Ableton track 1. Tracks 2–8 are reserved.
- **Per-loop dedicated pairs are deferred to Stage 8**, together with loop
  transport/routing. The channel map documents the reservation; it is not a
  claim that those tracks carry audio today.
- **No granular FX** (Stage 9), **no MIDI mutation** (Stage 8), **no MPE /
  bass-drone** in Stage 5 (Stage 6 is now complete), **no UI** (Stage 7, next),
  **no VST hosting**, and **no Ableton API automation**.
- **No Python or OSC protocol change.** Python still owns the `MutationEngine`
  and sends only `/ji/freqset/update`; sclang still owns the nearest-class
  lookup.
- **No Aggregate Device in the primary path.** It is a documented fallback for
  separate-device or concurrency failures.
- **No long-session guarantee yet.** The 5-minute drift/dropout check is a human
  acceptance step, not an agent claim.

---

## 15. Verification available in the agent environment

No SuperCollider, scsynth, Ableton, `uv`, or Python is installed in the agent
environment, so no sclang evaluation or audio test was possible. This stage was
reviewed statically, and `config/ableton_routing.json` was parsed as valid JSON.
The unchanged Python layer can be checked on the human machine:

```bash
cd ji-live
uv run --with python-osc python test_mutation.py
uv run --with python-osc python test_mutation_controls.py
```

Both should still pass; Stage 5 does not touch Python. The human run below is
the arbiter for the routing and the deferred Stage 4 sound check.

---

## 16. Human acceptance checklist

1. `ServerOptions.inDevices` contains **MiniFuse 2** and
   `ServerOptions.outDevices` contains **BlackHole 16ch**.
2. Evaluate the Stage 5 `startup.scd`. Confirm:
   - `inDevice=MiniFuse 2 outDevice=BlackHole 16ch`
   - `Audio I/O: 2 in / 16 out, 48000 Hz, blockSize 64`
   - buses 0–18 allocated and distinct
   - all four SynthDefs loaded
3. In Ableton:
   - input **BlackHole 16ch**, inputs **1–16** enabled;
   - output **MiniFuse 2** (or headphones), 48 kHz;
   - eight stereo tracks, track N input = BlackHole channels **2N-1/2N**;
   - Monitor **In**; master to MiniFuse 2.
4. Evaluate `test_ableton_routing.scd`. Confirm the tone visits all eight tracks
   in order with the expected `[ROUTING]` lines and is audible through
   Ableton's master.
5. Run for **5 minutes**. Confirm no drift/dropouts.
6. Evaluate `test_dual_loop.scd`; play into the MiniFuse 2 input; confirm live
   autotune on **Ableton track 1** and complete the deferred Stage 4 sound
   check.
7. Record the result. The owner reported all sound checks passed on
   2026-10-30, including Stage 5 routing and the Stage 4 signal through
   Ableton.

**Stage 5 is implemented and owner-confirmed (2026-10-30).** The adjustable
loop-length control is a later live-rig enhancement; see
[README_live_rig.md](README_live_rig.md).

# JI Station — Stage 0 (Tartini pitch tracking + passthrough)

Stage 0 proves the audio environment is correct before any DSP complexity:
scsynth boots on a 2-channel interface, Tartini pitch-tracks both input
channels, the estimates are printed to the post window, and audio passes
through unchanged.

```
ji-live/
├── startup.scd
├── synthdefs/
│   └── ji_tartini_debug.scd
├── test_tartini.scd
└── README_stage0.md
```

---

## 1. Prerequisites

* macOS, Apple Silicon.
* SuperCollider **3.13 or newer**, Apple Silicon native build. The SC IDE
  prints its version in the post window at startup; the scsynth boot line
  (`scsynth 3.13.x ...`) confirms the server version.
* **sc3-plugins**, installed for the same SuperCollider version/architecture
  (Tartini lives there).
* A 2-channel audio interface (2 inputs + 2 outputs) that can run at
  **48000 Hz** with a 64-frame buffer.

Version / capability checks to run in the post window before anything else:

```supercollider
ServerOptions.devices.postln;          // exact device names for startup.scd
ServerOptions.inDevices.postln;        // devices that actually have inputs
Tartini.kr(SinOsc.ar(220)).postln;     // posts a Tartini UGen if sc3-plugins is loaded
```

If the third line reports `ERROR: Class not defined`, sc3-plugins' class
library is not installed for this SuperCollider — stop here and install it.

macOS also needs to allow microphone access for SuperCollider:
*System Settings → Privacy & Security → Microphone → enable SuperCollider.*
Without it the inputs are silent even though the interface shows signal.

---

## 2. Exact steps

**a.** Open the SuperCollider IDE.

**b.** Open `startup.scd` and set the audio device on the line marked
`REPLACE_ME` (section 1 of the file):

```supercollider
var deviceName = "REPLACE_ME";   // <-- REPLACE_ME
```

Replace it with the exact name from `ServerOptions.devices`, e.g.
`var deviceName = "Scarlett 2i2 4th Gen";` — spelling and capitalisation must
match. Leaving it as `REPLACE_ME` is allowed: the file then warns and uses the
system default device.

**c.** Evaluate `startup.scd` (Cmd+Return). It boots the server itself (no need
to press "Boot"), allocates the control buses, and loads the SynthDefs.
Wait for:

```
JI system buses allocated: freq1=0 freq2=1 hasfreq1=2 hasfreq2=3 cpu=4
```

If the numbers are not 0–4, read the warning that follows the line.

**d.** Evaluate `test_tartini.scd` (Cmd+Return, whole file — open it from disk,
do not paste it into an unsaved document). It starts `\ji_tartini_debug` and
begins printing.

**e.** Play a sustained **A3 (~220 Hz)** into input channel 1 and a sustained
**G3 (~196 Hz)** into input channel 2. A sine, or a clean sustained guitar note,
works best; avoid heavy distortion or reverb.

---

## 3. Expected output

After `startup.scd`:

```
JI system buses allocated: freq1=0 freq2=1 hasfreq1=2 hasfreq2=3 cpu=4
Loading SynthDef file: ji_tartini_debug.scd
Startup complete -- now evaluate test_tartini.scd.
```

After `test_tartini.scd`, with both notes playing:

```
Loading SynthDef: /Users/you/ji-live/synthdefs/ji_tartini_debug.scd
[test] \ji_tartini_debug started (node 1000)
[CH1] freq=220.31 Hz  hasFreq=0.91
[CH2] freq=196.00 Hz  hasFreq=0.88
[CH1] freq=220.14 Hz  hasFreq=0.90
[CH2] freq=195.87 Hz  hasFreq=0.89
CPU: 3.2%
```

Acceptance criteria:

| Check | Expected |
|---|---|
| CH1 frequency | 220 Hz ± 2 Hz |
| CH2 frequency | 196 Hz ± 2 Hz |
| `hasFreq` | > 0.7 on both channels |
| Passthrough audio | audible, clean, no dropouts |

Notes:

* The first few lines print `[CH1] no signal       hasFreq=0.00` — the polling
  starts immediately, the Synth node is created a few milliseconds later.
  This is normal.
* `hasFreq < 0.3` is printed as `no signal` instead of a frequency, because
  Tartini's frequency output is meaningless without confidence.
* The pitch readout lags the audio by roughly **43 ms**: the default Tartini
  window is `n = 2048` samples (2048 / 48000). This affects the printed
  numbers only — the audio passthrough itself is not delayed.
* `CPU:` is scsynth's average CPU (percent of one core). Two Tartini instances
  at blockSize 64 typically cost a few percent.
* To stop, evaluate the three commands at the bottom of `test_tartini.scd`.
  Cmd+. also stops everything (clocks cleared, nodes freed), but then you have
  to re-evaluate `test_tartini.scd` to start again.

---

## 4. Failure modes

| Symptom | Cause | Fix |
|---|---|---|
| `UGen 'Tartini' not installed` (server error when the Synth is created) | sc3-plugins is not installed for the server, or the `.scx` plugin binary is built for a different SC version / architecture | Install sc3-plugins for SC 3.13 (Apple Silicon), then re-evaluate `startup.scd` **and** `test_tartini.scd` (the SynthDef must be rebuilt and re-sent) |
| `ERROR: Class not defined` mentioning `Tartini` while loading the SynthDef | sc3-plugins' class library is not installed (client side) | Same as above: install sc3-plugins, restart the IDE |
| `no signal` on both channels | input device not configured (wrong device, or the interface's inputs are not the selected device) | Set `deviceName` in `startup.scd`; if input and output are different devices use `s.options.inDevice` / `s.options.outDevice` instead of `device`; check macOS microphone permission |
| `no signal` on one channel only | that input has no signal / interface is mono | Check the cable and the interface's input gain; this stage expects 2 input channels |
| Frequency wildly wrong (octave too high/low, jitter) | input too quiet or too noisy; Tartini locks onto a harmonic or noise | Increase the input gain on the interface, play a strong sustained tone, and try a different `tartiniThreshold` (default here: 0.9) |
| Server fails to boot | sample rate mismatch between interface and server (e.g. interface at 44100 Hz), or the device cannot do 48000 Hz with 64-frame buffers | Align the device's rate in *Audio MIDI Setup*, or change `sampleRate` in `startup.scd`; reboot the server |
| `*** WARNING: the ji buses are not in slots 0-4` | something else allocated control buses first, or `startup.scd` was re-evaluated without rebooting | `s.reboot;` then evaluate `startup.scd` again |
| `*** WARNING: project folder unknown` / `cannot locate the project folder` | the file was evaluated from an unsaved document (no path) | Open `startup.scd` / `test_tartini.scd` from disk and evaluate them there |
| Audio dropouts / `CPU` climbing above ~80% | blockSize too small or the hardware buffer is too small for the interface | Raise `blockSize` to 128 in `startup.scd`, then `s.reboot` |
| Server boots, pitch prints, no audible passthrough | wrong output device, muted interface, or monitoring off | Check `s.options.outDevice`, the interface's monitor level, and macOS output device |

---

## 5. What is NOT included in Stage 0

No autotune, no retuning/looper, no mutation, no granular FX, no VST hosting,
no MIDI, no UI — Stage 0 only boots the server, tracks pitch on 2 channels, and
passes audio through.

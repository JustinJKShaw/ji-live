# JI Station — Stage 1 (single-channel JI autotune)

Tartini tracks input channel 1; sclang picks the nearest member of a hardcoded
7-note JI set (A3 = 220 Hz) and writes that pitch into the Synth's
`targetFreq`; the Synth shifts the input with `PitchShift` so the sounding
pitch lands on the target. Rhythm is preserved (granular shifter); formants are
not (deliberate for this stage).

```
ji-live/
├── startup.scd                  (MODIFIED: buses 5-7, loads synthdefs/*.scd)
├── synthdefs/
│   ├── ji_tartini_debug.scd     (UNCHANGED from Stage 0)
│   └── ji_autotune_ch.scd       (NEW)
├── test_autotune.scd            (NEW)
└── README_stage1.md             (NEW)
```

---

## 1. Prerequisites

* **Stage 0 has passed** (Tartini works, server boots at 48 kHz / blockSize 64).
* **Re-evaluate `startup.scd`.** It changed: it now allocates control buses
  5–7 and loads every `synthdefs/*.scd` dynamically. Without this, the Stage 1
  buses and the `\ji_autotune_ch` SynthDef do not exist.
* **Stop the Stage 0 test** if it is still running, otherwise its dry
  passthrough is summed with the autotune output (doubled, phasey audio):

  ```supercollider
  ~ji_tartini_routine.stop;
  ~ji_cpu_routine.stop;
  ~ji_tartini_debug_synth.free;
  ```

* Use **headphones** — the mic/interface input is live and the output is a
  pitch-shifted copy of it; speakers will feed back.
* Same device setup as Stage 0: `deviceName` in `startup.scd` (line marked
  `REPLACE_ME`), 48 kHz, 2 in / 2 out.

---

## 2. Steps

**a.** Evaluate `startup.scd` (Cmd+Return). Confirm the post window shows all
eight buses:

```
JI system buses allocated: freq1=0 freq2=1 hasfreq1=2 hasfreq2=3 cpu=4 autotune_freq1=5 autotune_hasfreq1=6 autotune_target1=7
Loading 2 SynthDef file(s) from /Users/you/ji-live/synthdefs
  - ji_autotune_ch.scd
  - ji_tartini_debug.scd
Startup complete -- now evaluate test_tartini.scd or test_autotune.scd.
```

If the numbers are not 0–7, read the NOTE that follows; the system still works,
the buses are just no longer in the reserved slots.

**b.** Evaluate `test_autotune.scd` (whole file, Cmd+Return). It starts
`\ji_autotune_ch` on input 0 → output 0 and begins printing one
`[AUTOTUNE]` line per second.

**c.** Play a slow chromatic scale on the guitar (or sing), ~2 s per note,
starting at A3 (220 Hz), into input 0. Listen and watch the `target` column:
it should always be one of the seven set pitches, transposed to the octave you
are playing (A3 → 220.00, A4 → 440.00):

```
220.00   247.50   275.00   293.33   330.00   366.67   412.50
```

**d.** Try the retune speed (no restart needed):

```supercollider
~ji_autotune_synth.set(\retuneTime, 0.005);   // near-instant snap
~ji_autotune_synth.set(\retuneTime, 0.5);     // audible portamento
~ji_autotune_synth.set(\retuneTime, 0.05);    // default
```

**e.** Optional: change the fundamental (takes effect on the next lookup):

```supercollider
~ji_ji_fundamental = 261.6255653;   // C4 instead of A3
```

You can also test the lookup function without playing anything:

```supercollider
~ji_nearest_target.(246.9).postln;  // -> 247.5   (12-TET B -> JI B)
~ji_nearest_target.(261.6).postln;  // -> 275.0   (C is not in the set -> C#)
~ji_nearest_target.(438.0).postln;  // -> 440.0   (flat A4 stays an A)
```

---

## 3. Expected output

```
[test] \ji_autotune_ch started (node 1000)
[AUTOTUNE] freq=220.31  target=220.00  hasFreq=0.91  shift=-0.02st
[AUTOTUNE] freq=246.94  target=247.50  hasFreq=0.88  shift=0.04st
[AUTOTUNE] freq=261.60  target=275.00  hasFreq=0.90  shift=0.87st
CPU: 3.7%
```

* `freq` — Tartini's estimate of the **input**.
* `target` — the JI pitch the Synth is currently tuning to (one of the 7
  values above, in whatever octave you played).
* `shift` — `12 * log2(target / freq)`, in semitones. Small values (±0.1 st)
  mean you were already almost in tune; ±1 st means the note was pulled to a
  neighbouring scale degree.
* `shift` prints `0.00` while no confident pitch is detected (the last target
  is held).

Acceptance criteria:

| Check | Expected |
|---|---|
| Output pitch | snaps to one of the 7 JI pitches, ±5 cents |
| `retuneTime` 0.005 | near-instant snap at note changes |
| `retuneTime` 0.5 | clearly audible portamento between targets |
| Clicks at note boundaries | none |
| `CPU:` | < 20 % with one channel active |

Notes:

* The scale is A major in JI. **F, G and C are not in the set**, so chromatic
  notes outside it snap to the nearest degree (C→C#, F→F#, G→G#). That is what
  "nearest member of the 7-note set" means, not a bug.
* `PitchShift` delays the shifted signal by roughly `windowSize` (100 ms at the
  default 0.1 s). Rhythm of the input is preserved, but the shifted sound
  arrives late — lower `windowSize` (e.g. 0.05) if that bothers you, at the cost
  of a grainier tone.
* The target only updates while `hasFreq > 0.5`; during note releases the last
  target is held, so the shifter does not chase noise.
* `CPU:` is scsynth's average CPU (percent of one core). One Tartini + one
  PitchShift is typically a few percent.

---

## 4. Failure modes

| Symptom | Cause | Fix |
|---|---|---|
| `UGen 'Tartini' not installed` | sc3-plugins missing or built for another SC version | Reinstall sc3-plugins for SC 3.15 (Apple Silicon), then re-evaluate `startup.scd` and `test_autotune.scd` |
| No pitch output, `hasFreq` stays 0.00 / `no signal` | input too quiet or wrong input channel | Raise the interface gain, check that you play into input 1; or raise the Synth's `inputGain`: `~ji_autotune_synth.set(\inputGain, 4)` |
| Clicks / buzz at note boundaries | `retuneTime` too small for the rate of pitch change | Raise to `0.02` or more: `~ji_autotune_synth.set(\retuneTime, 0.05)` |
| Pitches "stick" to the wrong class | the fundamental does not match the instrument's tuning reference | Change `~ji_ji_fundamental` (step e) |
| `PitchShift` UGen error / `SynthDef not found` | SC version incompatibility, or `startup.scd` was not re-evaluated | Check `PitchShift.help` compiles and plays; then re-evaluate `startup.scd` (it loads `synthdefs/*.scd`) |
| `ERROR: test_autotune: the Stage 1 buses (5-7) are missing` | old `startup.scd` (Stage 0 version) still in use | Use the Stage 1 `startup.scd`, evaluate it, then `test_autotune.scd` |
| Output sounds doubled / phasey | the Stage 0 test Synth is still running | Stop it (see Prerequisites) |
| Bus numbers are not 0–7 | buses were allocated earlier in this interpreter session | Restart the interpreter (Language → Restart Interpreter), reboot the server, evaluate `startup.scd` again |
| `CPU:` above 20 % | several synths running, or a very small `windowSize` | Free unused synths; `~ji_autotune_synth.set(\windowSize, ...)` only at creation — recreate the Synth instead |

---

## 5. Stopping

```supercollider
~ji_lookup_routine.stop;
~ji_cpu_routine_stage1.stop;
~ji_autotune_synth.free;
```

(Cmd+. also stops everything: clocks are cleared and all nodes are freed. To
start again, re-evaluate `test_autotune.scd`.)

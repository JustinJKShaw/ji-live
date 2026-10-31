# Stage 5 Agent Prompt — BlackHole 16ch + Ableton Audio Routing


> **Layout note (2026-10-30):** the project now keeps Python harnesses in
> `tests/`, sclang test scripts in `tests/sc/`, and documentation in `docs/`.
> Paths below are the original handoff paths. Resolve `test_*.py` to
> `tests/...`, `test_*.scd` to `tests/sc/...`, and `README_*` /
> `design_doc_10.28.10.50` to `docs/...`. See `README.md` at the project root.


You are implementing **Stage 5** of the JI live-performance system. Stages 0–4 are
implemented. Stage 4's functional code is in place but its **audible** acceptance
is deferred: Stage 5 creates the BlackHole → Ableton monitoring path through
which that sound test will finally be heard.

Do not claim Stage 4 or Stage 5 audio passed. The human is the audio authority.

---

## 1. Read these files first

Read the current code, not just summaries:

```text
ji-live/design_doc_10.28.10.50
ji-live/README_stage4.md
ji-live/README_stage3.md
ji-live/startup.scd
ji-live/test_dual_loop.scd
ji-live/synthdefs/ji_autotune_ch.scd
ji-live/synthdefs/ji_looper_recorder.scd
ji-live/synthdefs/ji_looper_player.scd
ji-live/test_bridge.scd
```

The design document and README_stage4 are authoritative where they conflict with
older text. In particular, the old Aggregate Device plan is superseded by the
separate-device configuration in §3.6/§8.6.

---

## 2. Current state you must preserve

- `scsynth` owns audio: Tartini, PitchShift, loop playback, buses.
- Python owns the `MutationEngine` and sends the complete class set to sclang on
  `/ji/freqset/update`; sclang owns the nearest-class lookup. Do not change that.
- Stage 4 runs two live autotune voices and up to four mono loop voices. It uses
  control buses 0–18 and audio outputs **0/1** for the current live/loop mix.
- `synthdefs/ji_autotune_ch.scd` is verified. Do not change its DSP.
- `test_bridge.scd`, `test_autotune.scd`, and `test_tartini.scd` are accepted.
  Do not modify them.
- `test_dual_loop.scd`'s loop-index fix (`[i - 1]` for the 0-based loop arrays)
  must be preserved. Do not re-break the record/play/stop indexing.
- Stage 3.5 Python controls/defaults are intact. Do not modify `control/*.py`
  unless a verified bug forces it; if you do, re-run the Stage 2/3 tests.
- Current `startup.scd` uses one `deviceName` and 2 in / 2 out. Stage 5 changes
  this to separate devices and 16 outputs.

### Verified device capability

SuperCollider supports separate input/output devices. `ServerOptions` has
`inDevice` and `outDevice`; use `inDevice_` / `outDevice_` to split them
(`device_("...")` sets both to one device), and
`ServerOptions.inDevices` / `outDevices` list exact names. scsynth receives
`-H <in> <out>` when they differ.

Do **not** claim Aggregate is required. The primary path is:

```supercollider
s.options.inDevice_("MiniFuse 2");
s.options.outDevice_("BlackHole 16ch");
s.options.numInputBusChannels = 2;
s.options.numOutputBusChannels = 16;
s.options.sampleRate = 48000;
s.options.blockSize = 64;
```

Use `ServerOptions.inDevices` / `outDevices` at runtime to confirm the exact
names on the human's machine. `s.options.device` returns a string when both
devices match and an array `[in, out]` when they differ.

---

## 3. Stage 5 goal

Make this path work end to end:

```text
MiniFuse 2 (2 input channels)
  → scsynth Stage 4 audio on output pair 0/1
  → BlackHole 16ch (16 output channels)
  → Ableton Live, input BlackHole 16ch, inputs 1–16 enabled
  → Ableton master output → MiniFuse 2 / headphones
```

Requirements:

1. `startup.scd` uses **MiniFuse 2** as `inDevice` and **BlackHole 16ch** as
   `outDevice`, with 2 input / 16 output bus channels, 48 kHz, blockSize 64.
2. It prints the exact input/output device names and the I/O counts on boot.
3. `test_ableton_routing.scd` plays a 1 kHz sine sequentially on each of the
   eight stereo pairs (SC channels 0/1, 2/3, … 14/15) and prints which pair is
   playing.
4. Ableton, configured with BlackHole 16ch input and inputs 1–16 enabled, shows
   the tone on the matching track 1–8.
5. The human hears the tone through Ableton's master output to the MiniFuse 2
   (or headphones on it).
6. Stage 4 still runs on output pair 0/1; its live autotune is audible through
   Ableton track 1. This is the deferred Stage 4 sound check.
7. No Aggregate Device is used. If separate devices prove unstable on the
   human's machine, document the Aggregate fallback, but do not make it the
   primary path.
8. No Python or OSC protocol changes in this stage.

### Scope boundary: channel map vs. routing

Stage 5 verifies the **16-channel infrastructure**. Stage 4 live/loop audio
stays on pair 0/1 for the first sound check. Do not rework the loop players or
reroute loops in this stage. The reserved map is:

| SC output channels | Ableton track | Content |
|---|---|---|
| 0–1 | 1 | live autotuned stereo mix (current Stage 4 audio) |
| 2–3 | 2 | Loop 1 (reserved) |
| 4–5 | 3 | Loop 2 (reserved) |
| 6–7 | 4 | Loop 3 (reserved) |
| 8–9 | 5 | Loop 4 (reserved) |
| 10–11 | 6 | Granular FX (Stage 8, reserved) |
| 12–13 | 7 | dry/diagnostic (reserved) |
| 14–15 | 8 | reserved |

Per-loop dedicated pair routing is deferred to Stage 8, when loop transport is
implemented. Document this clearly; do not imply Stage 4 loops already appear on
tracks 2–5.

---

## 4. Deliverables

```text
ji-live/
├── startup.scd                     (MODIFIED: separate devices, 16 out)
├── test_ableton_routing.scd        (NEW)
├── README_stage5.md               (NEW)
├── config/ableton_routing.json     (NEW)
├── README_stage4.md                (MODIFIED only if needed to match the new path)
└── design_doc_10.28.10.50          (MODIFIED: status/changelog; §3.6/§6.10/§8.6)
```

Do not modify `synthdefs/ji_autotune_ch.scd`, `test_bridge.scd`,
`test_autotune.scd`, `test_tartini.scd`, `control/*.py`, or the Stage 2/3 Python
tests.

### 4.1 `startup.scd`

- Replace the single `deviceName` configuration with separate input/output
  names, or keep a single-device fallback if the names are still `REPLACE_ME`.
- Use `inDevice_` / `outDevice_`. `device_("...")` is only for a single shared device.
- Set `numInputBusChannels = 2`, `numOutputBusChannels = 16`, 48 kHz,
  blockSize 64.
- Keep the cumulative bus allocation 0–18 unchanged. Audio bus indices 0–15 are
  now valid; control buses remain 0–18.
- Print:
  ```text
  inDevice=MiniFuse 2 outDevice=BlackHole 16ch
  Audio I/O: 2 in / 16 out, 48000 Hz, blockSize 64
  ```
- Keep the existing Stage 4 bus sanity checks.
- If the device names are not found, print a clear warning and list
  `ServerOptions.inDevices` / `outDevices`; do not silently fall back to a
  different physical device.

### 4.2 `test_ableton_routing.scd`

- Standalone sclang test, evaluable as a whole.
- Check the server is running and `s.options.numOutputBusChannels == 16`.
- Stop/clean its own previous routine and test synth before starting.
- Use a simple SynthDef or direct `Out.ar`:
  ```supercollider
  // One stereo pair at a time, e.g. pair * 2.
  Out.ar(pair * 2, SinOsc.ar(1000, 0, 0.08) ! 2);
  ```
- Play each pair for about 1 second, then stop and free it before the next.
- Print, for example:
  ```text
  [ROUTING] pair 1 (Ableton track 1) -> SC ch 0/1
  [ROUTING] pair 2 (Ableton track 2) -> SC ch 2/3
  ...
  [ROUTING] done
  ```
- Include a stop path:
  ```supercollider
  ~ji_ableton_routing_routine.stop;
  ~ji_ableton_routing_synth.free;
  ```
- Do not depend on Python, the mutation engine, or Stage 4's set receiver.
- Keep it safe to run repeatedly.

### 4.3 `README_stage5.md`

Cover:

- prerequisites: BlackHole 16ch installed, MiniFuse 2, Ableton Live, 48 kHz;
- no Aggregate Device: SC uses `inDevice`/`outDevice`;
- exact `startup.scd` device settings and reboot requirement;
- Audio MIDI Setup: both devices 48 kHz;
- Ableton: input **BlackHole 16ch**, enable inputs 1–16, output **MiniFuse 2**
  (or headphones on it), 48 kHz;
- create 8 stereo tracks, each selecting its BlackHole pair; monitor In;
- run `test_ableton_routing.scd`; expected `[ROUTING]` lines and Ableton meters;
- monitor path: BlackHole is virtual, so SC alone is silent; Ableton must
  monitor BlackHole and send master to the MiniFuse 2;
- run `test_dual_loop.scd` and confirm the Stage 4 live autotune on track 1;
- note that both apps share devices in opposite directions (SC uses MiniFuse 2
  input and BlackHole output; Ableton uses BlackHole input and MiniFuse 2
  output). If CoreAudio/Ableton cannot open a device concurrently or your
  Ableton version cannot select separate input/output devices, document the
  Aggregate fallback;
- 5-minute drift/dropout check with separate devices;
- failure modes: wrong device name, no input in Ableton, no monitoring, wrong
  sample rate, BlackHole channels not enabled, `-H`/separate-device failure
  (Aggregate fallback), duplicate monitoring/feedback;
- limitations: loop pairs reserved, Stage 8 later; no MIDI/MPE/UI/granular.

### 4.4 `config/ableton_routing.json`

Record the channel map and device names, for example:

```json
{
  "sample_rate": 48000,
  "in_device": "MiniFuse 2",
  "out_device": "BlackHole 16ch",
  "sc_output_channels": 16,
  "pairs": [
    {"ableton_track": 1, "sc_channels": [0, 1], "content": "live autotuned stereo mix"},
    {"ableton_track": 2, "sc_channels": [2, 3], "content": "Loop 1 (reserved)"},
    ...
  ]
}
```

Use exact device names; document that §4.2 of the design doc is the reference.

### 4.5 Design doc

Update:
- §3.6 device configuration: separate `inDevice`/`outDevice`, no required
  Aggregate; MiniFuse 2 / BlackHole 16ch.
- §6.10 Ableton routing: same.
- §8.6 Stage 5 spec and acceptance: match this prompt.
- §9 status: Stage 4 audio acceptance deferred to Stage 5; Stage 5 in progress.
- Changelog.

---

## 5. Human acceptance test

1. Confirm `ServerOptions.inDevices` contains **MiniFuse 2** and
   `ServerOptions.outDevices` contains **BlackHole 16ch**.
2. Evaluate the updated `startup.scd`. Confirm the device line, 2 in / 16 out,
   buses 0–18, and all SynthDefs loaded.
3. In Ableton:
   - Audio input: **BlackHole 16ch**; enable inputs 1–16.
   - Audio output: **MiniFuse 2** (or headphones on it).
   - Create 8 stereo tracks; track N input = BlackHole channels 2N-1/2N;
     monitor In.
4. Evaluate `test_ableton_routing.scd`. Confirm the tone visits all 8 tracks in
   order and is audible through Ableton's master.
5. Run for 5 minutes; confirm no drift/dropouts.
6. Evaluate `test_dual_loop.scd`; play into the MiniFuse 2 input; confirm live
   autotune on Ableton track 1 and complete the deferred Stage 4 sound check.
7. Record the result. Stage 5 is not complete until the human hears the routing
   and the Stage 4 signal through Ableton.

---

## 6. Constraints

- Do not modify `synthdefs/ji_autotune_ch.scd`, `test_bridge.scd`,
  `test_autotune.scd`, or `test_tartini.scd`.
- Do not add a server-side nearest-class SynthDef or send Python directly to
  scsynth.
- Do not change the Stage 3 set contract or the Stage 4 loop-index behavior.
- Do not add loop OSC control, MIDI, MPE, UI, granular FX, VST, or Ableton API
  automation. This stage is device configuration + routing test + docs.
- Keep Stage 4's live/loop audio on pair 0/1; do not rework loop routing.
- Keep 2 input channels; 16 output channels are required now.
- Do not claim audio passed unless the human reports it.

---

## 7. Self-review and final response

After implementing, provide:

1. Files changed/added.
2. For each acceptance criterion, the code that satisfies it or a concrete
   concern.
3. “If I had to bet on which line is most likely to fail on the human's machine,
   which is it, and why? Give a one-line verification command.”
4. Assumptions (maximum 5 bullets).
5. Explicitly state: audio was not testable in the agent environment; Stage 5
   awaits human audio acceptance, and the Stage 4 sound check is part of that.

Useful verification commands:

```bash
cd ji-live
uv run --with python-osc python test_mutation.py
uv run --with python-osc python test_mutation_controls.py
```

These confirm the unchanged Python/control layer. The human run in §5 is the
arbiter for the new routing.

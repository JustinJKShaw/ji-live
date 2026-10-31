# Stage 8.1 Agent Prompt — Rhythm Foundation + Drum Engine v1

You are implementing **Stage 8.1** of the JI live-performance system: the first
increment of the long-running Stage 8 performance-expansion phase. Stages 0–7
and 7.1 are implemented. The owner has confirmed the GUI/audio/MIDI rig works
well; the only open Stage 7 item is the drone MIDI receive path, which the
Drone/Arp switches and drone retrigger are meant to diagnose.

Stage 8.1 is a **new-feature stage**, but it must not regress the accepted
audio, autotune, loop, drone, arp, or GUI paths. It is the foundation for later
Stage 8 subphases: groove generation, more MIDI voices, pattern/sequencing
mutation, save/recall with seeds, and loop options.

Do not claim human audio/MIDI acceptance. The human is the authority.

---

## 1. Read these files first

Read the current code and the updated design doc, not just summaries:

```text
docs/design_doc_10.28.10.50          §2.15–§2.16, §4.1, §6.11–§6.14,
                                     §7.1, §8.9, §9, §13, §14
docs/README_stage7.md                Stage 7/7.1 GUI, OSC, drone/arp switches
docs/README_mpe.md                   Stage 6 MIDI rig, IAC/Ableton setup, calibration
docs/README_live_rig.md              four-channel audio rig, loop functions, start order
docs/prompts/PROMPT_stage7_gui.md    previous handoff tone (historical paths)
tests/sc/test_live_rig.scd           current sclang globals/functions; do not change audio DSP
tests/sc/test_ableton_routing.scd    routing infrastructure (reference only)
startup.scd                          server/bus/SynthDef setup; do not change audio behavior
control/ji_bridge.py                 bridge lifecycle, send_set, UI status, --midi
control/osc_server.py                OSC surface and callback pattern
control/midi_rig.py                  drone/arp orchestrator and per-bus switches
control/mpe_output.py                lazy mido adapter and pitch/bend helpers
control/drone.py                     anchor drone
control/arp.py                       set arp and its fixed 0.25 s/0.20 s timing
control/mutation.py                  engine API only; do not modify
config/mpe_config.json               Stage 6 MIDI config (Bus 1 / Bus 2)
config/live_rig_routing.json         four mono audio channels
ui/ji_ui.py                          one-window PyQt6 GUI
ui/osc_control.py                    pure OSC message/state layer
ui/control_surface.scd               sclang autotune/loop/status surface
tests/test_ui.py                     pure + headless Qt test style
tests/test_mpe.py                    fake-output / pure-test style
tests/test_mutation_controls.py      bridge/server harness style
```

Current layout note: Python harnesses are in `tests/`, sclang tests in
`tests/sc/`, docs in `docs/`. Run Python tests from `ji-live/`.

---

## 2. Current state you must preserve

- Stage 5.5 audio: MiniFuse 2 input / BlackHole 16ch output, 2 in / 16 out,
  48 kHz, blockSize 64. SC outputs 0/1 = live autotuned inputs, 2/3 = loops.
- `tests/sc/test_live_rig.scd` owns the two live autotune synths, four loop
  voices, the 20 Hz lookup, `/ji/freqset/update` receiver, and loop functions:
  `~ji_record_loop`, `~ji_stop_record`, `~ji_play_loop`, `~ji_stop_loop`,
  `~ji_stop_all`, `~ji_set_loop_default`, `~ji_set_loop_buffer`.
- Stage 6 MIDI: drone on `IAC Driver Bus 1`, arp on `IAC Driver Bus 2`, both
  member channel 2, 48-semitone bend range. The arp timing is fixed
  0.25 s/0.20 s; changing it is **not** part of 8.1.
- Stage 7.1: the GUI's Status group has independent Drone on/Arp on switches
  (`/ji/midi/set_enabled`, `/ji/midi/state`); preserve them.
- The Python bridge owns the `MutationEngine` and sends exactly
  `/ji/freqset/update fundamental glide_time ratio_1 ... ratio_N`.
- `JIBridge(...)` must still construct without mido and MIDI disabled.
- Existing tests must keep passing:
  `tests/test_mutation.py`, `tests/test_mutation_controls.py`,
  `tests/test_mpe.py --pure`, `tests/test_ui.py --pure`.
- The four-channel audio routing and the Stage 6/7 MIDI/GUI paths are not
  allowed to regress.

**Do not modify:** `synthdefs/*`, `startup.scd` audio behavior,
`tests/sc/test_live_rig.scd` audio DSP, `tests/sc/test_dual_loop.scd`,
`tests/sc/test_ableton_routing.scd`, `control/mutation.py`, the
`/ji/freqset/update` payload/receiver, or the Stage 5.5 routing/loop-length
semantics. Additive changes to `tests/sc/test_live_rig.scd` are allowed only if
unavoidable and must preserve every default; prefer extending
`ui/control_surface.scd` instead.

---

## 3. Goal

Add a shared rhythm foundation and the first new MIDI voice:

1. **Transport, tempo, and time signature** controls in the GUI.
2. **Tempo-derived drum MIDI output** on an optional third bus, with a default
   four-on-the-floor groove.
3. **Per-lane drum toggles** and **half/normal/double-time**.
4. **Free or beat-locked loops**, with per-loop beat lengths so loops can
   phase against each other.
5. Status feedback for all of the above, and no audio/MIDI regressions.

Stage 8.1 is deliberately **not** groove generation, additional bass/lead
voices, sequencing, save/recall, or more loops. Those are 8.2+.

---

## 4. Scope

### In scope for Stage 8.1

- `control/rhythm.py` — pure rhythm state/math and a monotonic beat/step
  clock, with no mido and no audio. Tempo, meter, transport, bar/beat/step
  conversion, quantization helpers, validation.
- `control/drums.py` — drum note map, default pattern generation, lane state,
  half/normal/double rate, and a scheduler that sends drum note-ons through a
  MIDI output. Pure-testable with a fake output.
- Additive `control/midi_rig.py` support for a third drum output, and additive
  `control/ji_bridge.py` / `control/osc_server.py` for rhythm/drum state and
  commands.
- Additive `ui/osc_control.py` message builders/parsers/state, and
  `ui/ji_ui.py` Rhythm controls plus per-loop Free/Beat and beat-length
  controls.
- Additive `ui/control_surface.scd` handlers for tempo/meter/transport and
  per-loop mode/beats; beat-quantized loop record/play using a `TempoClock`.
- `config/rhythm_config.json` (new) for the drum port/channel/note map and
  default rhythm settings; or documented additive fields in
  `config/mpe_config.json` if you prefer one config file. Prefer a separate
  rhythm config so Stage 6 MIDI users are unaffected.
- `tests/test_rhythm.py` (new), additive `tests/test_ui.py` coverage, and an
  optional sclang rhythm test.
- `docs/README_stage8.md` and design-doc updates.

### Out of scope for Stage 8.1

- Bass/lead/extra melodic voices (8.3).
- Groove presets, density, syncopation, swing, probability, and a per-step
  drum grid (8.2).
- Sequencing/pattern mutation of frequency sets or MIDI patterns (8.4).
- Save/load/recall and seed reproduction (8.5).
- More than four loops, overdub, per-loop routing, loop-player autotune knobs,
  loop-file persistence (8.6).
- Time-stretching existing loop buffers on tempo change.
- Changing the Stage 6 arp's fixed timing (8.3/8.4).
- Polyphonic MPE, granular FX, visual process, or GUI aesthetics.
- Ableton API automation.

---

## 5. Architecture

```text
GUI (PyQt6, one window)
 ├─ bridge 9000  → rhythm set_tempo/set_meter/transport, drums set_*, existing mutation
 ├─ sclang 57120 → autotune/loops, loop set_mode/set_beats, status query
 └─ listener 9001 ← bridge: rhythm/state, drums/state, midi/state, existing status
                    ← sclang: loop/config, loop/state, cpu, autotune/state
```

Clock ownership and timing:

- The **Python bridge owns the canonical rhythm state** (bpm, meter,
  transport running). It is the source for the GUI status and the drum
  scheduler.
- The bridge broadcasts tempo/meter/running to sclang
  (`/ji/rhythm/tempo`). sclang sets its `TempoClock` and uses it to quantize
  loop record/play to the next beat.
- Drums are scheduled in Python from `time.monotonic()` with drift-corrected
  deadlines (the same pattern as `SetArp`). Do not busy-wait or block the Qt
  event loop.
- **Loop buffer lengths are seconds.** Beat mode computes seconds from the
  current bpm at record time. Tempo changes after recording do **not**
  time-stretch an existing buffer; document that the user should stop/re-record
  or use free mode. This is a deliberate 8.1 limitation.
- Pure Python code must not import mido; the drum output must import it lazily
  only when a real port opens. Fake outputs must be injectable.

### 5.1 Tempo, meter, transport

- `bpm` is **quarter-note BPM**, 20–300, default 120.
- Meter numerator 1–16; denominator 1, 2, 4, 8, or 16; default 4/4.
- Bar length in quarter notes = `numerator * 4 / denominator`.
- A 16th-note step is `60 / bpm / 4` seconds (relative to the quarter-note
  beat).
- `transport_running` is 0/1. Drums and beat-mode loops run only while the
  transport is running; free loops can still be started/stopped individually.
- Transport start should not restart the audio engine, the mutation set, or
  the existing drone/arp; it starts/aligns rhythm scheduling only.
- On transport stop, drum note-offs/All Notes Off must be sent; do not leave
  stuck drum notes.
- Changing tempo while running should update the drum scheduler and sclang
  `TempoClock` without a restart. Do not burst through missed steps; resync
  from the current time like `SetArp` does.

### 5.2 Loop modes and quantization

Each loop 1–4 has a mode and a length:

- **Free:** existing Stage 7 behavior. Length in seconds (1–30, default 8).
  Record/Play/Stop behave as they do today. Free mode must be byte-for-byte
  behavior-compatible with the current loop functions.
- **Beat:** length in beats (1–64, default 4). Record and Play are scheduled at
  the next beat boundary on the sclang `TempoClock`. The recorded take is
  `beats * 60 / bpm` seconds long at record time.
- Different beat lengths (e.g., 4, 3, 5, 7) must be allowed so loops phase.
- Stop and Clear are immediate in 8.1. Do not delay them to the next beat.
- If the transport is stopped, beat-mode Record/Play must be rejected with a
  clear log/UI message; free mode can still operate.
- Expose per-loop mode/beats/seconds status back to the UI.
- Do not reimplement loop audio logic in Python. Additive sclang scheduling
  around the existing `~ji_record_loop`/`~ji_play_loop` is the intended path.

### 5.3 Drum engine and note map

The drum output is a new slow-tempo-independent MIDI voice, not an MPE voice.
Use the existing lazy mido adapter style and a fake-output seam for tests.

- Default port: `IAC Driver Bus 3`; default channel: **10** (1-based MIDI
  channel 10, mido channel index 9).
- Drum output is **opt-in** with `--drums` (default off). Existing
  `--midi` without `--drums` must keep working even if Bus 3 does not exist.
- If `--drums` is requested and Bus 3 cannot be opened, print a clear error and
  keep the drone/arp/audio rig running with drums disabled. The GUI must show
  drums disabled/port not available; do not crash the bridge. (A missing bus
  is a setup issue, not an audio failure.)
- Default note map (configurable in `config/rhythm_config.json`):

| MIDI | Note | Lane | Default role |
|---|---|---|---|
| 36 | C1 | kick1 | four-on-the-floor kick |
| 37 | C#1 | kick2 | second kick |
| 38 | D1 | snare1 | backbeat snare |
| 39 | D#1 | snare2 | second snare |
| 40 | E1 | perc1 | rim/click |
| 41 | F1 | perc2 | clap |
| 42 | F#1 | perc3 | closed hat |
| 43 | G1 | perc4 | pedal hat |
| 44 | G#1 | perc5 | open hat |
| 45 | A1 | perc6 | low tom |
| 46 | A#1 | perc7 | mid tom |
| 47 | B1 | perc8 | high tom |
| 48 | C2 | perc9 | crash |
| 49 | C#2 | perc10 | ride |
| 50 | D2 | perc11 | shaker |
| 51 | D#2 | perc12 | cowbell |

- Note that MIDI C1 is 36 in the standard MIDI numbering used here; Ableton
  may display that as C1 or C3 depending on its octave convention. Document
  this so the human can map a Drum Rack correctly.
- The pattern grid is 16th notes. The default groove is four-on-the-floor:
  - kick1 on every beat (steps where `step % 4 == 0`);
  - snare1 on beats 2 and 4 when the current meter has them;
  - closed hat (perc3) on offbeat 8ths (every second 8th);
  - all other lanes off.
  Make the exact default deterministic and documented. For non-4/4 meters,
  truncate/extend against the bar length in a simple, documented way; do not
  invent complex polyrhythm logic in 8.1.
- Each of the 16 lanes has an enable toggle. Disabling a lane stops it on the
  next step; re-enabling starts it at the next step. Do not leave a stuck note
  (drums are note-on/note-off or note-on-only with a short gate; define and
  document which).
- Half/normal/double time: rate multiplier 0.5/1/2 applied to the step
  duration (half = slower, double = faster). Keep tempo/meter state unchanged.
- All individual drum hits are togglable at the lane level in 8.1. A per-step
  grid, probability, swing, and groove presets are 8.2.

### 5.4 GUI

Add a **Rhythm** group to the existing one-window GUI (no tabs, no menus):

- Play/Stop transport.
- BPM control 20–300 (dial or spin box; show the value).
- Time signature numerator/denominator controls.
- Drum enable checkbox, drum port/status label, and half/normal/double-time
  selector.
- 16 lane toggles labeled Kick 1, Kick 2, Snare 1, Snare 2, Perc 1–12 (or the
  configured labels). These are lane-level toggles, not a step grid.
- Loop section: each of loops 1–4 gets a Free/Beat selector and a length
  control (seconds in free mode, beats in beat mode). The existing global
  length can remain as the default free length, but per-loop lengths must be
  possible.
- Status: tempo, meter, transport, drum enabled/port/rate, lane states,
  per-loop mode/length, and current loop state.
- The GUI must remain usable if the bridge/sclang/drums are absent; show
  waiting/disabled states, do not crash.
- Continue the existing patterns: status callbacks via Qt signals, not widget
  updates from the OSC thread; block widget signals when syncing from incoming
  state; coalesce continuous knob sends (~30 Hz); buttons send immediately.

### 5.5 OSC map (proposed)

UI → bridge:

```text
/ji/rhythm/set_tempo          float bpm [20, 300]
/ji/rhythm/set_meter          int numerator [1,16], int denominator [1,2,4,8,16]
/ji/rhythm/transport          int running [0,1]
/ji/drums/set_enabled         int enabled [0,1]
/ji/drums/set_lane            int midi_note, int enabled [0,1]
/ji/drums/set_rate            float rate [0.5, 1.0, 2.0]
```

Bridge → UI:

```text
/ji/rhythm/state              float bpm, int num, int den, int running,
                              int drums_enabled, float drum_rate
/ji/drums/state               int enabled, int port_ok, float rate, int lane_mask
```

Bridge → sclang:

```text
/ji/rhythm/tempo              float bpm, int num, int den, int running
```

UI → sclang (loop config):

```text
/ji/loop/set_mode             int index 1..4, string "free"|"beat"
/ji/loop/set_beats            int index 1..4, float beats 1..64
```

sclang → UI:

```text
/ji/loop/config               int index, string mode, float beats, float seconds
```

You may adjust the exact payloads if the implementation requires it, but update
the design doc §7.1 and the README/table and keep them consistent. Clamp or
reject invalid values in both Python and sclang; never apply an unsafe value.

For lane state, a 16-bit `lane_mask` is suggested (bit 0 = MIDI 36, bit 15 =
MIDI 51). Document the bit order.

### 5.6 Status and startup

- On bridge/UI startup, send current rhythm/drum state and per-loop config.
- Send `/ji/rhythm/tempo` to sclang at bridge start and after every tempo/
  meter/transport change, so sclang can set its clock even if the UI was not
  running when the bridge started.
- Send `/ji/rhythm/state` and `/ji/drums/state` after every change and on
  `/ji/ui/hello`.
- Send `/ji/loop/config` for all four loops on `/ji/status/query`.
- Do not flood: one state message per change, no polling faster than needed.

---

## 6. Deliverables

```text
ji-live/
├── control/
│   ├── rhythm.py              NEW: rhythm state/clock/quantization/math
│   ├── drums.py               NEW: drum map/pattern/scheduler/lane state
│   ├── midi_rig.py            MODIFIED additively: third drum output or drum rig
│   ├── ji_bridge.py           MODIFIED additively: rhythm/drum state, --drums
│   └── osc_server.py          MODIFIED additively: rhythm/drum callbacks
├── ui/
│   ├── ji_ui.py               MODIFIED additively: Rhythm group, loop mode/beats
│   └── control_surface.scd    MODIFIED additively: tempo/clock, loop quantize/config
├── config/
│   └── rhythm_config.json     NEW: drum port/channel/note map/defaults
├── tests/
│   ├── test_rhythm.py         NEW: pure rhythm/drum tests
│   └── test_ui.py             MODIFIED additively: rhythm/drum messages and Qt controls
├── docs/
│   ├── README_stage8.md       NEW: setup, controls, OSC, failures, acceptance
│   └── design_doc_10.28.10.50 UPDATED: status, §6.14, §7.1, §8.9, §9, §14
└── README.md                  UPDATED: Stage 8.1 status/next link
```

If the implementation needs a different file split, keep the same conceptual
layers and document it. Prefer additive changes to existing modules.

---

## 7. Tests

### 7.1 Pure tests (no hardware, no mido, no PyQt6)

```bash
cd ji-live
uv run --with python-osc python tests/test_rhythm.py
```

Cover at minimum:

- Tempo/meter/transport clamping and validation.
- Bar length/beat duration/step duration math for 4/4, 3/4, 6/8, 5/4.
- Beat-mode take length from beats and bpm.
- Loop mode/index/beat-length validation.
- Drum note map (36–51) and labels.
- Default four-on-the-floor pattern on the 16th grid for 4/4.
- Lane mask/toggles, half/normal/double rate.
- Fake drum output receives the expected note numbers/order and no notes when
  disabled; transport stop sends All Notes Off/note-offs.
- Invalid messages are rejected/clamped without crashing.

### 7.2 Existing tests

```bash
cd ji-live
uv run --with python-osc python tests/test_mutation.py
uv run --with python-osc python tests/test_mutation_controls.py
python tests/test_mpe.py --pure
uv run --with python-osc python tests/test_ui.py --pure
```

All must pass. `JIBridge(...)` must still construct without mido and MIDI
disabled; `--midi` without `--drums` must not require Bus 3.

### 7.3 Optional headless Qt test

```bash
QT_QPA_PLATFORM=offscreen uv run --with python-osc --with PyQt6 \
  python tests/test_ui.py --qt
```

Add checks for the Rhythm group: BPM/meter changes emit the expected messages,
transport toggle, lane toggles, drum rate, loop mode/beat controls, status
sync, and waiting/disabled states when drums are absent. Skip cleanly if PyQt6
is unavailable.

### 7.4 Sclang/audio checks (human-run)

Add an optional `tests/sc/test_rhythm.scd` (or extend the existing control
surface test) for:

- Evaluating `startup.scd`, then `tests/sc/test_live_rig.scd`, then
  `ui/control_surface.scd`.
- Setting tempo/meter through the bridge/UI and confirming the loop record/play
  quantization.
- Free mode unchanged; beat mode records exactly the specified beats at the
  current tempo.
- Different loop beat lengths phase rather than hard-restarting each other.
- No stuck loops, no audio dropouts.

The agent cannot verify audio, MIDI hardware, Ableton, or IAC here; the human
does that.

---

## 8. Human acceptance checklist

1. GUI shows a Rhythm group with transport, BPM, meter, drum enable/rate, 16
   lane toggles, and drum status.
2. Start the bridge with `--midi --drums` and a configured `IAC Driver Bus 3`;
   the default four-on-the-floor groove is audible on the drum track at
   120 BPM, 4/4.
3. Tempo and time-signature changes take effect; half/normal/double time works.
4. Individual drum lanes toggle on/off without stuck notes.
5. Loop 1–4 can be `free` or `beat`; beat mode records the requested number of
   beats at the current tempo and starts playback on the beat.
6. Different loop beat lengths phase against each other.
7. Free loops behave exactly as in Stage 7.
8. Drum bus missing: the bridge keeps drone/arp/audio running and reports
   drums disabled; the GUI does not crash.
9. Existing mutation, autotune, loop, drone/arp, and GUI behavior is unchanged.
10. Closing the GUI still leaves the rig running.
11. Record the result. **Stage 8.1 is not complete until the human confirms
    the rhythm/drum controls and no audio/MIDI regressions.**

---

## 9. Expected diagnostics

Useful lines to add (prefixes consistent with the existing logs):

```text
[RHYTHM] bpm=120.0 meter=4/4 running=1
[RHYTHM] loop 1 mode=beat beats=4.0 seconds=2.000
[DRUMS] port=IAC Driver Bus 3 channel=10 rate=1.0
[DRUMS] pattern kick1=quarter snare1=2,4 perc3=offbeat8
[DRUMS] enabled=1 lanes=0x0005
[MIDI] drums disabled: IAC Driver Bus 3 not found; drone/arp continue
```

Do not spam per-step logs during performance; log pattern/config changes and
the first step only.

---

## 10. Risks / decisions to document

- **Clock drift:** Python drums and sclang loop quantization are separate
  clocks at the same tempo. Document the v1 behavior and provide a resync path
  if practical (for example, re-broadcasting tempo on transport start and
  exposing a "resync" action). Do not silently hide drift.
- **Tempo changes do not time-stretch buffers.** Beat-locked loops keep their
  recorded duration; stop/re-record or use free mode. This must be in the
  README and UI help/labels.
- **Time-signature semantics:** BPM is quarter-note based; denominator affects
  bar length. Document how 6/8/7/8 are interpreted so the human is not
  surprised.
- **MIDI octave convention:** C1 = 36 here; Ableton may label it C3. Document
  the Drum Rack mapping.
- **Bus 3 missing:** drums degrade gracefully; drone/arp/audio must keep
  working.
- **Drum note-off semantics:** choose note-on/note-off or note-on-only with a
  short gate; make it deterministic and test it. Drums must not leave stuck
  notes.
- **Existing arp timing:** do not change the Stage 6 arp's fixed 0.25/0.20
  timing in 8.1; tempo-syncing the arp is a later subphase.

---

## 11. Self-review requirement (final response)

Provide:

- Files added/changed.
- For each requirement above, the code that satisfies it or a concrete
  concern.
- "If I had to bet on which line is most likely to fail on the human's
  machine, which is it, and why? Give a one-line verification command."
  Candidates: the IAC Bus 3/drum port setup, Ableton note/octave mapping,
  clock drift between Python drums and sclang loops, or beat-mode loop
  quantization timing.
- Assumptions, max 5 bullets.
- Explicit statement that audio, MIDI, Ableton, IAC, and the live GUI were not
  tested by the agent; Stage 8.1 awaits human acceptance.

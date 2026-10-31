# Stage 8.3a Agent Prompt — Monophonic JI Bass Voice

You are implementing **Stage 8.3a** of the JI live-performance system. Stages
0–7.1, 8.1, and 8.2 are implemented. Stage 8.1 and Stage 8.2 were
**human-tested and accepted on 2026-10-31** and are in live use. Stage 8.3a is
the first increment of Stage 8.3: a monophonic JI bass voice. A lead voice,
polyphony, and the rest of 8.3 are later.

The owner's brief for this first increment:

- simple pulses of the fundamental on the downbeat (a kind of drone bass);
- a moving techno-style bassline that puts a groove on single notes;
- wilder moving basslines that travel around different octaves with fun grooves;
- monophonic, so ordinary single-channel pitch bend is enough;
- an optional separate sub channel on the same MIDI bus (different MIDI
  channel) may be useful.

Do not claim human audio/MIDI acceptance. The human is the authority.

---

## 1. Read these files first

Read the current code and docs, not just summaries:

```text
docs/design_doc_10.28.10.50        §6.11, §6.12, §6.13, §6.14, §6.15,
                                   §7.1, §8.9, §9, §13, §14
docs/README_stage8_2.md            accepted 8.2 behavior, groove/quantize model
docs/README_stage8.md              accepted 8.1 rhythm/drum behavior
docs/README_mpe.md                 Stage 6 MPE bus/channel conventions
docs/prompts/PROMPT_stage8_2_groove.md
                                   previous stage contract/tone
docs/prompts/PROMPT_stage8_1_rhythm.md
                                   rhythm foundation contract/tone
control/mpe_output.py              MPE pitch math, lazy mido, fake seam
control/drone.py                   Stage 6 anchor drone
control/arp.py                     Stage 6 set arp
control/midi_rig.py                MIDIRig, optional drum output, snapshots
control/rhythm.py                  RhythmEngine/BeatClock/math
control/groove.py                  seeded generation/grid conventions
control/quantize.py                BeatQuantizer phases/coalescing/flush
control/drums.py                   scheduler/lane/config conventions
control/ji_bridge.py               bridge ownership, snapshots, status
control/osc_server.py              OSC handlers/callbacks
control/mutation.py                engine API only; do not modify
ui/osc_control.py                  pure UI builders/parsers/state
ui/ji_ui.py                        compact PyQt6 window
config/mpe_config.json             Stage 6 MIDI config
config/rhythm_config.json          Stage 8.1/8.2 drum/groove config
tests/test_mpe.py                  fake MIDI output tests
tests/test_rhythm.py               rhythm/drum scheduler tests
tests/test_groove.py               seeded groove tests
tests/test_quantize.py             beat queue tests
tests/test_ui.py                   pure + headless Qt tests
README.md                          run commands/status
```

Run Python tests from `ji-live/`.

---

## 2. Current state you must preserve

- Stage 5.5 audio: MiniFuse 2 input / BlackHole 16ch output, 2 in / 16 out,
  48 kHz, blockSize 64. SC 0/1 = live autotuned inputs, 2/3 = loops.
- Stage 6: drone on IAC Bus 1, arp on IAC Bus 2, member channel 2,
  48-semitone bend range. The arp's 0.25 s/0.20 s timing is fixed; **do not
  tempo-sync it** in 8.3a.
- Stage 7/7.1: compact one-window GUI, Status, independent Drone/Arp switches
  in the top-row MIDI group.
- Stage 8.1/8.2 (owner-confirmed):
  - Python bridge owns BPM/meter/transport, `RhythmEngine`/`BeatClock`.
  - Drums on optional IAC Bus 3, channel 10, 16 lanes, four-on-the-floor,
    lane masks, half/normal/double rate, CC 123 on stop.
  - Groove presets/density/syncopation/swing/probability/seed, 16-lane step
    grid, and the beat-quantized state queue (`control/quantize.py`).
  - Per-loop free/beat modes; beat-mode Record/Play quantized in sclang.
  - `/ji/drums/groove`, `/ji/drums/grid`, `/ji/quantize/state` status.
- Do not change `/ji/freqset/update`, `synthdefs/*`, `startup.scd` audio
  behavior, `tests/sc/test_live_rig.scd`, `tests/sc/test_dual_loop.scd`,
  `tests/sc/test_ableton_routing.scd`, `control/mutation.py`, the Stage 5.5
  routing/loop-length semantics, or the Stage 6 drone/arp contracts.
- Existing tests must keep passing:
  `tests/test_mutation.py`, `tests/test_mutation_controls.py`,
  `tests/test_rhythm.py`, `tests/test_groove.py`, `tests/test_quantize.py`,
  `tests/test_mpe.py --pure`, `tests/test_ui.py --pure`, and the optional Qt
  test.
- Important compatibility rule: while the transport is **stopped**, every
  quantized action applies immediately. Direct API calls (engine setters,
  `MutationOSCServer.trigger()`, etc.) remain immediate; quantization belongs to
  the bridge/UI request layer.
- Keep the compact 8 pt / 32 px / scroll-area GUI. Do not push Status or the
  Drone/Arp switches off-screen.

---

## 3. Scope

### In scope (8.3a)

- A new **monophonic bass voice** with a transport-synced step sequencer.
- The three owner archetypes:
  1. **pulse/drone**: long pulses of the fundamental on the downbeat;
  2. **techno**: a moving single-note line with a groove;
  3. **chaos**: wide, seeded pitch/octave movement with a fun groove.
- Rhythm controls: grid, density, syncopation, swing, gate, accent, optional
  root-on-downbeat, pattern length.
- Pitch controls: source mode, movement, octave range, octave-jump
  probability, base octave, seed.
- Optional **sub** on a second channel of the same MIDI bus, typically one or
  two octaves below the main note.
- Presets for the three archetypes plus manual/custom edits.
- Bridge/OSC/status/GUI integration and beat-quantized parameter changes.
- Pure tests with a fake MIDI output, additive existing tests, docs.

### Out of scope

- Lead voice, extra melodic voices, polyphony, MPE zones, per-note channel
  allocation (8.3b+).
- Pattern/sequencing mutation of the frequency set or MIDI patterns (8.4).
- Save/load/recall and whole-session seed reproduction (8.5).
- More than four loops, overdub, per-loop routing, loop-player autotune,
  loop-file persistence (8.6).
- Time-stretching existing loop buffers.
- Changing Stage 6 arp timing or making it tempo-synced.
- Audio DSP, granular FX, visual process, GUI aesthetics/tabs, Ableton API
  automation, SC-side VST hosting.
- A second bass voice, polyphonic bass, chord stabs, or a full piano-roll UI.

---

## 4. Architecture

```text
UI (PyQt6)
 ├─ bridge 9000 → /ji/bass/* requests, existing mutation/rhythm/drum/groove
 ├─ sclang 57120 → autotune/loops, unchanged
 └─ listener 9001 ← bridge: bass/state, bass/note, quantize/state, existing
                   ← sclang: loop/config, loop/state, cpu, autotune/state

Bridge (Python)
 └─ MIDIRig
     ├─ drone (IAC Bus 1, ch 2)      unchanged
     ├─ arp   (IAC Bus 2, ch 2)      unchanged
     ├─ drums (IAC Bus 3, ch 10)     unchanged, optional
     └─ bass  (IAC Bus 4, ch 2 main / ch 3 sub)  NEW, optional
```

The bass is Python MIDI only. It adds no SC audio bus, SynthDef, or sclang
file. The bridge remains the canonical owner of transport/tempo/meter and the
current JI set snapshot.

### 4.1 MIDI output model

- New optional port, default `IAC Driver Bus 4`. **Do not reuse the drums bus
  by default**; bass and drums are independent voices. The config may point
  the bass at another existing bus if the owner prefers, but document it.
- One port object, two fixed 1-based channels: main `channel=2`, sub
  `channel=3` by default. The sub channel must differ from the main channel.
- Bass is **plain monophonic MIDI**, not an MPE zone: fixed channel, note-on/
  note-off, pitch bend, CC 123. Do not rotate channels per note.
- Pitch bend range default **2.0 semitones** (standard single-channel synth),
  configurable and required to match the receiving instrument. Use the
  existing `frequency_to_pitch()` math from `control/mpe_output.py` rather than
  inventing a second tuning formula. Send pitch bend **before** note-on.
- Open the IAC port lazily, only in `BassRig.start()`/`MIDIRig.start()`, and
  import `mido` only when opening a real port. A fake output must be injectable
  for tests.
- A missing Bus 4 disables the bass only:
  `[MIDI] bass disabled: <reason>; drone/arp/drums continue`. The rest of the
  rig keeps running.
- `--bass` must work with or without `--midi` and `--drums`:
  `MIDIRig(midi=False, drums=False, bass=True)` opens only the bass port.
- On stop/disable: note-off the held main and sub notes and send CC 123 on both
  channels. Never leave a stuck note.
- Do not open the same IAC port twice with two independent clients. Use one
  port wrapper that can send to both channels.
- Do not open the same IAC port twice with two independent clients. Use one
  port wrapper that can send to both channels.
- **Important refactor:** `MIDIRig._apply()` currently returns early when
  `midi_enabled` is false, and `start()`/`stop()` special-case the MPE buses.
  Make bass snapshot/start/stop forwarding independent of `midi_enabled`:
  `MIDIRig(midi=False, bass=True)` must open only the bass port, receive
  `on_set()` snapshots, and stop cleanly. Do not break the existing
  `midi=False, drums=True` mode. `JIBridge` must construct `MIDIRig` when
  `midi or drums or bass` is true.

Suggested `config/bass_config.json` shape (additive; exact keys may evolve):

```json
{
  "port": "IAC Driver Bus 4",
  "channel": 2,
  "sub_channel": 3,
  "sub_enabled": false,
  "sub_octaves": 1,
  "bend_range_semitones": 2.0,
  "velocity": 90,
  "sub_velocity": 80,
  "gate_beats": 3.5,
  "preset": "pulse",
  "grid": "1/4",
  "density": 0.0,
  "syncopation": 0.0,
  "swing": 0.0,
  "accent": 0.0,
  "root_downbeat": true,
  "pitch_mode": "root",
  "movement": 0.0,
  "octave_range": 0,
  "octave_jump": 0.0,
  "base_octave": -2,
  "seed": 1
}
```

The final defaults must be documented in `docs/README_stage8_3.md` and must
match the preset table in §5.1.

### 4.2 Suggested module shape

A clear split, analogous to drums, is:

```text
control/bass.py
  BassConfig        validated config (port/channels/bend/velocity/defaults)
  BassSettings      enabled, preset, grid, density, gate, pitch controls, sub
  BassPattern       deterministic trigger grid for one bar / step count
  BassPitchPicker   pure seeded ratio/octave selection from a set snapshot
  BassOutput        one lazy mido port, main/sub channel messages
  BassScheduler     daemon thread, drift-corrected deadlines, gate note-offs
  BassRig           output + settings + scheduler behind one small API

config/bass_config.json
control/midi_rig.py       optional BassRig, snapshots, drum-like setters
control/ji_bridge.py      state, OSC callbacks, quantized bass phase, status
control/osc_server.py     /ji/bass/* handlers
control/quantize.py       PHASE_BASS (if needed)
ui/osc_control.py         builders/parsers/BassStatus/store fields
ui/ji_ui.py               compact Bass group, status, pending integration
tests/test_bass.py        new pure tests
tests/test_ui.py          additive bass builders/parsers/Qt checks
```

A different split is fine if the layers stay documented.

---

## 5. Required user-facing behavior

### 5.1 Presets

Provide at least these named presets. The exact numbers may be tuned, but the
installed defaults must be documented and deterministic.

| Preset | Intent | Suggested starting point |
|---|---|---|
| `pulse` | Drone bass / downbeat pulses | grid `1/4`, only the bar downbeat triggers, `gate_beats` long (≈3.5 in 4/4), pitch `root`, movement 0, octave range 0 |
| `techno` | Moving single-note groove | grid `1/16`, density ≈0.55, syncopation ≈0.15, swing ≈0.10, short gate ≈0.12 beats, pitch `walk`, movement ≈0.45, octave range 1, octave jump ≈0.05 |
| `chaos` | Wilder octave-moving lines | grid `1/16`, density ≈0.80, syncopation ≈0.35, swing ≈0.15, short gate ≈0.08 beats, pitch `random` or `walk`, movement ≈0.90, octave range 2, octave jump ≈0.35 |

- Selecting a preset replaces all bass settings with that preset's values.
- Manual edits switch the preset label to `custom` (or an equivalent
  documented state); selecting a preset later overwrites them.
- `pulse` must sound like a sustained/pulsing fundamental on the downbeat, not
  a busy line.

### 5.2 Rhythm controls

| Control | Range/default | Behavior |
|---|---|---|
| Enable | 0/1, default 0 | Bass runs only while enabled and transport running |
| Grid | `1/4`, `1/8`, `1/16`; default `1/16` | Steps per beat = 1/2/4 |
| Length | 1–32 steps; default one bar = `bar_quarters * steps_per_beat` | Pattern repeats; cap at 32 (long meters keep the cap) |
| Density | 0..1, default preset | Probability that a non-downbeat step triggers |
| Syncopation | 0..1, default preset | Deterministic displacement/addition on offbeats; never moves step 0 |
| Swing | 0..1, default preset | Timing only; odd 16th/8th steps delayed up to 50% of a step |
| Gate | beats 0.05–8.0, default preset | Note length; longer gates hold across steps (monophonic) |
| Accent | 0..1, default preset | Downbeat/accent velocity boost; exact mapping documented |
| Root on downbeat | 0/1, default 1 | If on, every pattern loop's step 0 uses the fundamental/root |

- Density 0 with `root_on_downbeat=1` gives only the downbeat — the `pulse`
  preset.
- Syncopation 0 is identity. Define and test the exact rule (for example, a
  seeded subset of non-downbeat hits is moved one 16th earlier, or an
  anticipation ghost is added; core/downbeat hits stay). Do not displace
  step 0.
- Swing must not drift or burst; the scheduler advances its nominal deadline
  by exactly one step and never catches up by firing a burst.
- Gate may exceed one step. The next trigger note-offs the held note first;
  a gate timer note-offs at the deadline otherwise. Do not send a late
  note-off for a note that was already retriggered.

### 5.3 Pitch controls

| Control | Range/default | Behavior |
|---|---|---|
| Pitch mode | `root`, `anchor`, `walk`, `random`, `cycle` | Source of the next ratio |
| Movement | 0..1 | 0 = hold/repeat; 1 = maximum motion/leaps |
| Octave range | 0..3, default 0 | Allowed ± octave offsets around the base |
| Octave jump | 0..1 | Probability of changing octave offset per trigger |
| Base octave | -3..0, default -2 | Whole octaves relative to the fundamental |
| Seed | int 0..2^31−1, default 1 | Reproducible pitch/trigger choices |
| Sub on | 0/1, default 0 | Enable the sub channel |
| Sub octaves | 1..2, default 1 | Sub frequency = main / 2^sub_octaves |

Pitch rules:

- `root`: frequency = `fundamental * 2^base_octave`, i.e. ratio 1/1.
- `anchor`: anchor ratio if present and in the set; otherwise fall back to
  root and log once. Never invent a pitch.
- `walk`: seeded stepwise walk through the sorted current set ratios; movement
  controls the probability and size of the move; stay inside the set.
- `random`: seeded choice from the current set; movement controls whether it
  changes class or repeats.
- `cycle`: deterministic cycle through the sorted set in pitch order; movement
  may control octave changes only.
- Octave offset is selected from `-octave_range..+octave_range` around
  `base_octave`; `octave_jump` is the probability of changing it on a trigger.
- If `root_on_downbeat` is on, the pattern's step 0 always uses ratio 1/1 and
  octave 0 (relative to base), regardless of pitch mode. This is what makes
  the pulse/drone and gives moving lines a root anchor.
- Same current set + same controls + same seed + same bar/step must reproduce
  the same ratio and octave. Do not use ambient `random` state.
- A set update changes the available pool for the **next trigger**; do not
  retrigger a held note merely because the set changed. If the held ratio is
  removed, let the gate finish (or end at the next trigger) and pick from the
  new set.
- Frequencies must be playable; use `frequency_to_pitch()` and document any
  whole-octave transposition. Log the selected ratio/Hz on the first note after
  a change, not on every step.

### 5.4 Sub

- Optional, default off. Same port, separate channel (default 3).
- Sub frequency = main frequency / `2 ** sub_octaves` (one or two octaves
  below).
- Same trigger and gate policy as the main note; `sub_velocity` configurable.
- Send pitch bend before sub note-on; note-off/CC 123 on stop and disable.
- If the sub frequency is below MIDI note 0, skip the sub note for that trigger
  and log once (do not silently transpose it upward and call it a sub).
- The sub must not steal the main channel or leave a note on when the main
  stops.

### 5.5 Transport, tempo, meter, and scheduler

- The bass runs only while **enabled and transport running**.
- Transport start resets the bass to step 0 at the bar start. Enabling bass at a
  beat while running starts it at step 0 at that beat (like drums).
- Transport stop sends note-offs and CC 123 on main and sub channels.
- Tempo changes alter step duration without restarting the scheduler or
  dropping the current note incorrectly.
- Meter changes rebuild the bar length; the default pattern length becomes
  one bar (`bar_quarters * steps_per_beat`, capped at 32) unless the user has
  set a custom length. Document which behavior you choose.
- The scheduler is a daemon thread with drift-corrected `time.monotonic()`
  deadlines, no busy-wait, and a stop `Event`. It must not block the Qt thread.
- No burst through missed steps; no stuck notes at fast tempos or long gates.

### 5.6 Beat-quantized bass changes

Use the Stage 8.2 `BeatQuantizer` request layer.

- While the transport is running, bass enable/disable, preset, grid, density,
  syncopation, swing, gate, accent, pitch mode, movement, octave range/jump,
  seed, base octave, sub enable/octaves, and length apply at the **next
  quarter-note beat**.
- While the transport is stopped, they apply immediately.
- Add a `PHASE_BASS` (or document an equivalent) to the deterministic order.
  Suggested order: mutation, octave, groove, drums, **bass**, midi.
- Last-wins coalescing per key; different keys all run.
- Enabling at the beat starts the scheduler at step 0; disabling at the beat
  stops it and sends note-offs/CC 123.
- Tempo/meter/transport remain immediate.
- Do not double-queue beat-mode loop Record/Play; they stay in sclang.
- Send `/ji/quantize/state` once per queue/apply/flush via the existing bridge
  path; do not flood.

### 5.7 Set snapshots

- `MIDIRig.on_set()` already receives `(ratios, anchor, fundamental)` and stores
  the last snapshot before start. Forward the same immutable snapshot to the
  bass (`BassRig.on_set`).
- The bass thread never reads `MutationEngine`.
- On a set update, log a compact line:
  `[BASS] set updated ratio=... hz=... pool=...` once, not per step.
- If the anchor is None, `anchor` pitch mode falls back to root with one log.

---

## 6. OSC map (proposed)

**UI → bridge**

```text
/ji/bass/set_enabled          int 0/1
/ji/bass/set_preset           string pulse|techno|chaos
/ji/bass/set_grid             string "1/4"|"1/8"|"1/16"  (or int 1|2|4)
/ji/bass/set_length           int steps 1..32
/ji/bass/set_density          float 0..1
/ji/bass/set_syncopation      float 0..1
/ji/bass/set_swing            float 0..1
/ji/bass/set_gate             float beats 0.05..8.0
/ji/bass/set_accent           float 0..1
/ji/bass/set_root_downbeat    int 0/1
/ji/bass/set_pitch_mode       string root|anchor|walk|random|cycle
/ji/bass/set_movement         float 0..1
/ji/bass/set_octave_range     int 0..3
/ji/bass/set_octave_jump      float 0..1
/ji/bass/set_base_octave      int -3..0
/ji/bass/set_seed             int 0..2^31-1
/ji/bass/set_sub_enabled      int 0/1
/ji/bass/set_sub_octaves      int 1..2
```

**Bridge → UI (additive)**

```text
/ji/bass/state   int enabled, int port_ok, string preset, string grid,
                 int length_steps, float density, float syncopation,
                 float swing, float gate_beats, float accent,
                 string pitch_mode, float movement, int octave_range,
                 float octave_jump, int seed, int base_octave,
                 int sub_enabled, int sub_octaves, int main_channel,
                 int sub_channel
/ji/bass/note    float hz, int midi_note, int velocity
                 (optional; send on note changes, not every scheduler tick)
```

Send `/ji/bass/state` at bridge start, on `/ji/ui/hello`, after every applied
bass change, and after transport/status changes that affect it. Keep
`/ji/drums/state`, `/ji/drums/groove`, `/ji/drums/grid`, `/ji/rhythm/state`,
`/ji/quantize/state`, and `/ji/midi/*` unchanged. `/ji/status/query` remains a
UI → sclang message; do not route it to the bridge.

---

## 7. GUI

Keep the one-window, no-tabs, compact design. Do not regress the current
8 pt / 32 px / scroll-area layout.

Add a **Bass** group (below Groove or in a compact two-row block):

- Enable checkbox.
- Preset combo: `pulse`, `techno`, `chaos`; show `custom` when edited.
- Grid combo: `1/4`, `1/8`, `1/16`.
- Length spin (steps).
- Compact knobs/spins for Density, Syncopation, Swing, Gate, Accent,
  Movement, Octave range, Octave jump, Base octave, Seed.
- Pitch-mode combo.
- Sub on checkbox + sub-octaves spin.
- A status line, e.g.
  `Bass: on | techno | 1/16 | E1 41.20 Hz | sub off`.
- If Bus 4 is unavailable, show `Bass: disabled (port not available; see bridge
  log)`, disable the controls, and do not crash.
- If the bridge is absent, keep waiting labels and allow the window to run.
- Knobs are coalesced at ≈30 Hz; buttons/spins send immediately; incoming
  `/ji/bass/state` sync blocks widget signals (no feedback OSC).
- The Bass group must not push Status, Drone/Arp, or the transport off-screen.
  The scroll area remains the safety net.

Optional: a small `/ji/bass/note` status label showing the last note/Hz. Do not
build a piano roll or per-step bass grid in 8.3a; that belongs to 8.4.

---

## 8. Tests

### 8.1 New pure tests (`tests/test_bass.py`)

Run from `ji-live/`:

```bash
uv run --with python-osc python tests/test_bass.py
```

Cover at least:

- Config validation: port, channels 1..16, main != sub, bend range, velocity,
  gate, grid, seed, validation/clamping.
- Preset defaults and manual-edit -> `custom`.
- Grid/step duration for `1/4`, `1/8`, `1/16`; length 1..32; meter changes.
- Density 0 / root-on-downbeat identity; density 1; syncopation 0 identity;
  syncopation never moves step 0; deterministic same seed; different seed
  differs where movement is nonzero.
- Pitch modes:
  - root is exactly fundamental * base octave;
  - anchor uses the anchor ratio; fallback when None;
  - walk stays in the current set, movement 0 holds;
  - random is deterministic for a seed and stays in the set;
  - cycle follows sorted set order;
  - octave range/jump stays within the documented range;
  - set updates affect the next trigger, not a held note.
- Sub: correct octave transposition, separate channel, same gate, note-off,
  skip below MIDI 0, sub disabled.
- Fake MIDI output: pitch bend before note-on, monophonic note-off before the
  next note-on, main/sub channel numbers, CC 123 on stop, no stuck notes.
- Scheduler: swing offset, drift correction, stop responsiveness, long gate
  retrigger behavior (no stale note-off cutting the next note).
- Missing Bus 4 disables bass without raising and does not disturb drums/drone/
  arp.
- `MIDIRig(midi=False, drums=False, bass=True)` works; `--bass` without
  `--midi`/`--drums` does not require the other ports.
- No mido import during pure construction.

### 8.2 Existing tests

```bash
uv run --with python-osc python tests/test_mutation.py
uv run --with python-osc python tests/test_mutation_controls.py
uv run --with python-osc python tests/test_rhythm.py
uv run --with python-osc python tests/test_groove.py
uv run --with python-osc python tests/test_quantize.py
python tests/test_mpe.py --pure
uv run --with python-osc python tests/test_ui.py --pure
```

All must pass. In particular:
- `JIBridge(...)` still constructs without mido/MIDI/bass enabled;
- `--midi` without `--drums` does not need Bus 3;
- `--bass` without `--midi`/`--drums` does not need Bus 1/2/3;
- with the transport stopped, bass changes are immediate;
- with it running, they are queued and coalesced;
- direct engine setters remain immediate.

### 8.3 Optional headless Qt test

```bash
QT_QPA_PLATFORM=offscreen uv run --with python-osc --with PyQt6 \
  python tests/test_ui.py --qt
```

Add checks for:
- Bass group/preset/mode/grid/sub controls; default values;
- knob/button/spin sends and signal-blocked `/ji/bass/state` sync;
- no feedback OSC during sync;
- disabled/waiting state when the bass port is unavailable;
- pending status after a bass change while running;
- stopped transport -> immediate;
- window stays compact with the Bass group added.
Skip cleanly if PyQt6 is unavailable.

### 8.4 Human sclang/audio checks

Audio, MIDI, Ableton, IAC, and live timing are human-only. Provide a short
checklist in `docs/README_stage8_3.md`. No new sclang file should be required
for 8.3a; the bass is Python MIDI only.

---

## 9. Human acceptance checklist

1. Bus 4 and two Ableton tracks (Bass ch 2, Sub ch 3) are configured; the
   bridge starts with `--bass` (with or without `--midi`/`--drums`) and logs the
   bass port/channels.
2. `pulse` produces a stable fundamental pulse/drone on the downbeat, in tune,
   with no clicks or stuck notes.
3. `techno` produces a moving single-note line with a clear groove; density/
   syncopation/swing/gate changes are audible at the next beat.
4. `chaos` moves through the set and across octaves; the same seed/preset
   reproduces the same line.
5. Pitch modes root/anchor/walk/random/cycle behave as documented; changing
   the frequency set changes the next notes without retriggering the held note
   unnecessarily.
6. Sub plays one/two octaves below on its own channel, follows gates, and can
   be disabled without affecting the main bass.
7. Transport start/stop, tempo, and meter changes behave; stopping leaves no
   stuck bass/sub notes; enabling at a beat starts on step 0.
8. Bass enable/parameters are beat-quantized while running and immediate while
   stopped; the pending line reflects them.
9. Drums, groove/grid, mutation, autotune, loops, drone/arp, and the existing
   GUI do not regress; the window stays compact.
10. Missing Bus 4 disables bass only, with a clear bridge message; the rest of
    the rig keeps running.
11. Record the result. **8.3a is not complete until the human confirms the bass
    and no audio/MIDI regressions.**

---

## 10. Diagnostics

Use the existing prefixes and add useful lines without per-step spam:

```text
[BASS] port=IAC Driver Bus 4 ch=2 sub_ch=3 bend=2.0
[BASS] preset=techno grid=1/16 density=0.55 sync=0.15 swing=0.10 gate=0.120 beats pitch=walk movement=0.45 octaves=1 seed=1
[BASS] note ratio=1.000 hz=55.000 midi=33 bend=... (first note / change only)
[BASS] set updated root=1.000 hz=220.000 pool=7
[BASS] sub ratio=0.500 hz=27.500 midi=21
[QUANT] queued bass preset (1 pending) due beat 42 (in 0.250 s)
[QUANT] applied 2 actions at beat 42 (order: drums, bass)
[MIDI] bass disabled: bass MIDI output 'IAC Driver Bus 4' not found ...; drone/arp/drums continue
```

Do not log every scheduled bass note during performance. Log preset/config
changes, the first note after a change, set updates, and disable/stop events.

---

## 11. Deliverables

```text
ji-live/
├── control/
│   ├── bass.py                NEW: config, settings, pattern, pitch picker,
│   │                          output, scheduler, BassRig
│   ├── midi_rig.py            MODIFIED additively: optional bass, snapshots
│   ├── ji_bridge.py           MODIFIED additively: bass state/OSC callbacks,
│   │                          bass quantize phase, /ji/bass/state
│   ├── osc_server.py          MODIFIED additively: /ji/bass/* handlers
│   ├── quantize.py            MODIFIED additively: PHASE_BASS
│   └── rhythm.py              unchanged unless a shared helper is genuinely
│                              needed
├── config/
│   └── bass_config.json       NEW: port/channels/bend/velocity/defaults
├── ui/
│   ├── osc_control.py         MODIFIED additively: builders/parsers/BassStatus
│   └── ji_ui.py               MODIFIED additively: Bass group, status/sync
├── tests/
│   ├── test_bass.py           NEW: pure bass/config/pitch/sub/scheduler tests
│   ├── test_mpe.py            MODIFIED additively if needed for MIDIRig bass
│   └── test_ui.py             MODIFIED additively: bass UI/pending
├── docs/
│   ├── README_stage8_3.md     NEW: setup, controls, presets, acceptance
│   ├── README_stage8_2.md     maybe touch only to link 8.3
│   └── design_doc_10.28.10.50 UPDATED: §6.16, §7.1, §8.9, §9, §14
└── README.md                  UPDATED: Stage 8.3a status/next link
```

If a different file split is clearer, keep the same conceptual layers and
document it. Prefer additive changes.

---

## 12. Risks / decisions to document

- **Bus 4 vs reuse:** default is a new `IAC Driver Bus 4`; if the owner prefers
  one bus, the config can point at Bus 3 with bass channels 11/12, but the
  drums stay on channel 10. Document the chosen convention.
- **Plain MIDI vs MPE:** bass uses fixed channels and standard pitch bend, not
  an MPE zone. The instrument's pitch-bend range must match config (default 2
  semitones). Do not silently assume 48.
- **Sub channel:** a separate synth track is required. Sub may be skipped when
  below MIDI note 0; do not transpose it upward and call it a sub.
- **Quantization latency:** bass changes wait up to one quarter beat while
  running (3 s at 20 BPM). Intentional; stopping the transport makes them
  immediate.
- **Gate/retrigger:** monophonic; next note-offs the held note; long gates hold
  but must never produce a stale late note-off that cuts the next note.
- **Determinism:** same set/controls/seed/bar/step must reproduce the line; no
  ambient random state.
- **Meter/length:** document whether a meter change keeps a custom length or
  reloads the bar length, and how the pattern repeats for long bars.
- **Set changes:** a removed class affects the next trigger, not a held note;
  do not cut/retrigger the current note just because the set changed.
- **GUI density:** keep the 8 pt / 32 px compact layout; the Bass group must
  not push existing controls off-screen.

---

## 13. Self-review requirement (final response)

Provide:

- Files added/changed.
- For each requirement above, the code that satisfies it or a concrete
  concern.
- "If I had to bet on which line is most likely to fail on the human's
  machine, which is it, and why? Give a one-line verification command."
- Assumptions, max 5 bullets.
- Explicit statement that audio, MIDI, Ableton, IAC, sclang timing, and the
  live GUI were not tested by the agent; 8.3a awaits human acceptance.

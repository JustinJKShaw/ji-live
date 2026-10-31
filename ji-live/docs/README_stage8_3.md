# JI Station — Stage 8.3a Monophonic JI Bass Voice

**Status:** implemented; **awaiting human audio/MIDI acceptance.** The agent
verified the pure Python model and a fake MIDI output only. Audio, IAC Bus 4,
Ableton, sclang, and the live PyQt6 window were not testable in the agent
environment (the Qt harness skips without `libEGL`).

Stage 8.3a is the first increment of Stage 8.3: a **monophonic, transport-synced
JI bass**. A lead voice, polyphony, and the rest of 8.3 are later. The bass is
Python MIDI only — no new SC bus, SynthDef, or sclang file.

---

## 1. What it adds

1. **Bass engine** (`control/bass.py`): validated config/settings, a deterministic
   trigger pattern, seeded pitch/octave selection, a lazy MIDI output, a
   drift-corrected scheduler with gate timers, and `BassRig`.
2. **MIDI integration** (`control/midi_rig.py`): an optional fourth port that is
   independent of the Stage 6 MPE buses and the Stage 8.1 drums.
3. **Bridge/OSC** (`control/ji_bridge.py`, `control/osc_server.py`,
   `control/quantize.py`): `/ji/bass/*` requests, `/ji/bass/state` and
   `/ji/bass/note`, and `PHASE_BASS` in the next-quarter-beat queue.
4. **GUI** (`ui/osc_control.py`, `ui/ji_ui.py`): a compact **Bass** group with
   enable, preset, grid, pitch mode, length, sub, parameter knobs, seed, a status
   line, and the last-note readout.
5. **Config/tests/docs**: `config/bass_config.json`, `tests/test_bass.py`,
   additive `tests/test_ui.py`, this document.

---

## 2. Setup

### 2.1 MIDI

1. Audio MIDI Setup → IAC Driver: enable a bus named exactly
   `IAC Driver Bus 4` (or point `config/bass_config.json` at another existing
   bus; the drums stay on Bus 3).
2. Ableton: one MIDI track **Bass** listening to `IAC Driver Bus 4`, channel 2;
   one optional track **Sub** on the same bus, channel 3.
3. Set the receiving instrument's **pitch-bend range to 2 semitones**, matching
   `bend_range_semitones` in the config. This is plain single-channel MIDI, not
   an MPE zone: do not reuse the 48-semitone MPE setting.
4. The sub track is optional. A sub note below MIDI note 0 (8.18 Hz) is skipped
   rather than silently transposed up.

### 2.2 Start order

Unchanged from Stage 8.2, with `--bass` added to the bridge:

```bash
# with the existing MPE rig and drums
uv run --with python-osc --with mido --with python-rtmidi \
  python control/ji_bridge.py --interactive --midi --drums --bass --glide-time 0.15

# bass only (no Bus 1/2/3 required)
uv run --with python-osc --with mido --with python-rtmidi \
  python control/ji_bridge.py --interactive --bass

# optional alternative config / bus
uv run --with python-osc --with mido --with python-rtmidi \
  python control/ji_bridge.py --bass --bass-config config/bass_config.json
```

Then in sclang evaluate `startup.scd`, `tests/sc/test_live_rig.scd`, and
`ui/control_surface.scd` as before, and start the GUI:

```bash
uv run --with python-osc --with PyQt6 python ui/ji_ui.py
```

A missing Bus 4 prints
`[MIDI] bass disabled: ...; drone/arp/drums continue`, disables only the bass,
and leaves every other voice running.

### 2.3 Config

`config/bass_config.json` ships the `pulse` defaults:

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
  "enabled": false,
  "preset": "pulse",
  "grid": "1/4",
  "length": null,
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

A short config such as `{"preset": "techno"}` starts from that preset and
overrides only the keys it names.

---

## 3. Presets

Selecting a preset replaces the rhythm/pitch/articulation values and resets the
pattern length to one bar. `enabled`, `seed`, and the sub settings survive.
Any other manual edit changes the displayed preset to `custom`; selecting a
preset again overwrites the edits.

| Preset | Grid | Density | Sync | Swing | Gate (beats) | Accent | Pitch | Movement | Oct octaves | Oct jump |
|---|---|---|---|---|---|---|---|---|---|---|
| `pulse` | 1/4 | 0.00 | 0.00 | 0.00 | 3.5 | 0.00 | root | 0.00 | 0 | 0.00 |
| `techno` | 1/16 | 0.55 | 0.15 | 0.10 | 0.12 | 0.25 | walk | 0.45 | 1 | 0.05 |
| `chaos` | 1/16 | 0.80 | 0.35 | 0.15 | 0.08 | 0.35 | random | 0.90 | 2 | 0.35 |

`pulse` is a downbeat fundamental pulse: step 0 of every pattern loop, a long
3.5-beat gate in 4/4, base octave -2 (55 Hz at the default 220 Hz fundamental).
At 120 BPM that is a 2 s loop with a 1.75 s note and a short gap.

---

## 4. Rhythm controls

| Control | Range | Behavior |
|---|---|---|
| Enable | 0/1 | Runs only while enabled **and** the transport is running |
| Grid | `1/4`, `1/8`, `1/16` | 1, 2, 4 steps per quarter note |
| Length | 1–32 steps | Default one bar (`bar_quarters × steps_per_beat`), capped at 32 |
| Density | 0..1 | Probability that a **non-downbeat** grid step triggers |
| Syncopation | 0..1 | Seeded anticipation at `step−1` for triggered steps ≥ 2 |
| Swing | 0..1 | Timing only; odd 1/8 and 1/16 steps delayed up to half a step |
| Gate | 0.05–8.0 beats | Note length; may exceed one step |
| Accent | 0..1 | Step-0 velocity boost up to +30 (main and sub) |
| Root downbeat | 0/1 (config/OSC) | Step 0 uses ratio 1/1 and octave offset 0 |

Rules:

- **Step 0 always triggers.** It is the bar downbeat. With `root_downbeat` off
  it still triggers, but its pitch is chosen by the pitch mode instead of being
  forced to the root. Density 0 therefore gives the downbeat-only `pulse`.
- **Density** gates only steps 1..N−1. The core downbeat never drops out.
- **Syncopation** adds an anticipation one grid step before a seeded subset of
  triggered steps (2..N−1) when that cell is empty. The original hit stays. It
  never moves or removes step 0, and step 1 has no anticipation because its
  predecessor is the downbeat. On a 1/16 grid the anticipation is a 16th; on a
  1/8 grid it is an 8th (documented grid-relative behavior).
- **Swing** is `swing * 0.5 * step_seconds` on odd grid steps for `1/8` and
  `1/16`; `1/4` is straight. The nominal deadline always advances one step, so
  swing does not drift and never fires a burst.
- **Gate** is a timer. A retrigger cancels the old timer, note-offs the held
  note, then plays the new one. A stale late note-off can never cut the next
  note.
- **Meter changes** rebuild the automatic bar length (`bar_quarters ×
  steps_per_beat`, capped at 32). A custom length set from the GUI/OSC survives
  a meter change; the spin box keeps showing the custom value.

---

## 5. Pitch controls

| Control | Range | Behavior |
|---|---|---|
| Pitch mode | `root`, `anchor`, `walk`, `random`, `cycle` | Source of the next ratio |
| Movement | 0..1 | 0 = hold/repeat; 1 = maximum motion/leaps |
| Octave range | 0..3 | Allowed ± offsets around the base octave |
| Octave jump | 0..1 | Probability of changing the octave offset per trigger |
| Base octave | −3..0 | Whole octaves relative to the fundamental (default −2) |
| Seed | 0..2³¹−1 | Reproducible trigger and pitch choices |
| Sub on | 0/1 | Enable the sub channel |
| Sub octaves | 1..2 | Sub frequency = main / 2^sub_octaves |

Rules:

- **root**: frequency = `fundamental × 2^base_octave` (ratio 1/1, octave 0).
- **anchor**: the anchor ratio if it is present in the current set; otherwise
  ratio 1/1 and one `[BASS] anchor missing ...` log. A pitch is never invented.
- **walk**: seeded stepwise walk through the sorted set. Movement controls the
  probability of moving and the maximum step (1 below 0.6, up to 2 above).
  Movement 0 holds the last class.
- **random**: seeded choice from the set. With movement `m`, the previous class
  repeats with probability `1−m`; at `m=1` it avoids repeating when the set has
  more than one class.
- **cycle**: deterministic cycle through the sorted ratios in pitch order;
  movement affects octaves only.
- **Octave offset** is chosen from `−octave_range..+octave_range`, around
  `base_octave`, and changes with probability `octave_jump`. It resets to 0 at
  every pattern downbeat. `root` and a root-downbeat step always use offset 0.
- **Determinism:** the walk/cycle state and the RNG streams reset at every
  pattern loop. The same current set + settings + seed + bar/step reproduces the
  same ratio and octave. No ambient `random` state is used.
- **Set updates** change the pool for the next trigger. A held note is never
  retriggered or cut just because the set changed. If the held class is removed,
  the gate finishes and the next trigger picks from the new set.
- **Playable range:** `frequency_to_pitch()` from `control/mpe_output.py` is
  reused. Frequencies outside MIDI 0–127 are transposed by whole octaves (the
  `octave_shift` in the pitch result); the default bass register never needs
  this transposition.

---

## 6. Sub

- Same output port, separate channel (default MIDI channel 3).
- Sub frequency = main / `2 ** sub_octaves` (one or two octaves below).
- Same trigger/gate policy, velocity `sub_velocity` plus the accent boost.
- Pitch bend is sent before the sub note-on. Stop/disable note-offs both held
  notes and sends CC 123 on both channels.
- If the sub frequency is below MIDI note 0, the sub note is skipped for that
  trigger (logged once per scheduler run). It is never transposed up and called
  a sub.
- The sub can be enabled/disabled independently of the main bass.

---

## 7. Transport, tempo, meter, and quantization

- The bass runs only while **enabled and transport running**.
- Transport start resets the sequencer to step 0. Enabling the bass at a beat
  while the transport runs starts at step 0 at that beat.
- Transport stop sends note-offs and CC 123 on the main and sub channels.
- Tempo changes alter step duration without restarting the scheduler.
- Meter changes rebuild the automatic bar length; a custom length survives.
- While running, **enabling/preset/grid/length/density/syncopation/swing/gate/
  accent/pitch mode/movement/octave range/jump/seed/base octave/sub enable/sub
  octaves** are queued to the next quarter-note beat of the Python
  `RhythmEngine`. While stopped they apply immediately.
- Phase order is `mutation, octave, groove, drums, bass, midi`. Bass requests
  coalesce last-wins per key.
- Quantization latency is up to one quarter beat (0.5 s at 120 BPM, 3 s at
  20 BPM). This is intentional; stop the transport for immediate changes.
- Beat-mode loop Record/Play remain sclang-quantized and are not double-queued.

---

## 8. OSC / status

**UI → bridge** (all validated/clamped; quantized while running)

```text
/ji/bass/set_enabled       int 0/1
/ji/bass/set_preset        string pulse|techno|chaos
/ji/bass/set_grid          string "1/4"|"1/8"|"1/16" (or 1|2|4)
/ji/bass/set_length        int 1..32
/ji/bass/set_density       float 0..1
/ji/bass/set_syncopation   float 0..1
/ji/bass/set_swing         float 0..1
/ji/bass/set_gate          float beats 0.05..8.0
/ji/bass/set_accent        float 0..1
/ji/bass/set_root_downbeat int 0/1
/ji/bass/set_pitch_mode    string root|anchor|walk|random|cycle
/ji/bass/set_movement      float 0..1
/ji/bass/set_octave_range  int 0..3
/ji/bass/set_octave_jump   float 0..1
/ji/bass/set_base_octave   int -3..0
/ji/bass/set_seed          int 0..2^31-1
/ji/bass/set_sub_enabled   int 0/1
/ji/bass/set_sub_octaves   int 1..2
```

**Bridge → UI**

```text
/ji/bass/state  int enabled, int port_ok, string preset, string grid,
                int length_steps, float density, float syncopation,
                float swing, float gate_beats, float accent,
                string pitch_mode, float movement, int octave_range,
                float octave_jump, int seed, int base_octave,
                int sub_enabled, int sub_octaves, int main_channel,
                int sub_channel
/ji/bass/note   float hz, int midi_note, int velocity
```

`/ji/bass/state` is sent at bridge start, on `/ji/ui/hello`, after every
applied bass change, and after a transport/meter change. `/ji/bass/note` is
sent only when the triggered note changes. `/ji/quantize/state` is unchanged and
is the only pending-count message. `/ji/status/query` remains UI → sclang.

---

## 9. GUI

The one-window compact **Bass** group sits below Groove:

- Enable, preset (`pulse`/`techno`/`chaos`/`custom`), grid, pitch mode, length,
  sub on/off, sub octaves.
- Knobs: density, syncopation, swing, gate, accent, movement, octave jump.
- Spins: octave range, base octave, seed.
- Status: `Bass: on | techno | 1/16 | sub off` plus
  `Last: A1 55.00 Hz`.
- Knobs are coalesced at ≈30 Hz; buttons/spins send immediately. Incoming
  `/ji/bass/state` syncs with signals blocked; there is no feedback OSC.
- If Bus 4 is unavailable the group shows
  `Bass: disabled (port not available; see bridge log)` and disables its
  controls. If the bridge is absent it shows `Bass: waiting for bridge`. The
  window keeps running in both cases.
- No piano roll or per-step bass grid in 8.3a; that belongs to 8.4.

---

## 10. Tests (run from `ji-live/`)

```bash
uv run --with python-osc python tests/test_bass.py
uv run --with python-osc python tests/test_quantize.py
uv run --with python-osc python tests/test_groove.py
uv run --with python-osc python tests/test_rhythm.py
uv run --with python-osc python tests/test_mutation.py
uv run --with python-osc python tests/test_mutation_controls.py
python tests/test_mpe.py --pure
uv run --with python-osc python tests/test_ui.py --pure
QT_QPA_PLATFORM=offscreen uv run --with python-osc --with PyQt6 \
  python tests/test_ui.py --qt
```

`test_bass.py` covers: config/channel/bend/velocity validation; presets and
`custom`; grid/step/length/meter math; density/root/sync/seed determinism;
root/anchor/walk/random/cycle/octaves and set updates; sub transposition and
below-note-0 skip; fake-output bend-before-note, monophonic note-offs, CC 123,
prompt stop; missing Bus 4 isolation; `MIDIRig(midi=False, drums=False,
bass=True)`; bridge immediate-vs-queued bass changes and the transport-stop
flush. The Qt test adds the Bass group/widgets, signal-blocked sync, disabled
port state, and queued/immediate feedback.

Agent result: all pure harnesses pass. The Qt test **skipped** here because the
container lacks `libEGL.so.1`; the window was not executed by the agent.

---

## 11. Human acceptance checklist

1. Bus 4 and two Ableton tracks (Bass channel 2, Sub channel 3) are configured;
   the bridge starts with `--bass` (with or without `--midi`/`--drums`) and logs
   `[BASS] port=IAC Driver Bus 4 ch=2 sub_ch=3 bend=2`.
2. `pulse` produces a stable fundamental pulse on the downbeat, in tune, with
   no clicks or stuck notes.
3. `techno` produces a moving single-note line with a clear groove; density/
   syncopation/swing/gate changes are audible on the next beat.
4. `chaos` moves through the set and across octaves; the same seed/preset
   reproduces the same line.
5. root/anchor/walk/random/cycle behave as documented; a set change affects the
   next notes without unnecessarily retriggering the held note.
6. Sub plays one/two octaves below on its own channel, follows gates, and can be
   disabled without affecting the main bass.
7. Transport start/stop, tempo, and meter behave; stopping leaves no stuck
   bass/sub notes; enabling at a beat starts on step 0.
8. Bass changes are beat-quantized while running and immediate while stopped;
   the pending line reflects them.
9. Drums, groove/grid, mutation, autotune, loops, drone/arp, and the existing GUI
   do not regress; the window stays compact.
10. Missing Bus 4 disables bass only, with a clear bridge message; the rest of
    the rig keeps running.
11. Record the result. **8.3a is not complete until the human confirms the bass
    and no audio/MIDI regressions.**

---

## 12. Diagnostics

```text
[BASS] port=IAC Driver Bus 4 ch=2 sub_ch=3 bend=2.0
[BASS] preset=techno grid=1/16 density=0.55 sync=0.15 swing=0.10 gate=0.120 beats pitch=walk movement=0.45 octaves=1 seed=1
[BASS] note ratio=1.000 hz=55.000 midi=33 bend=0
[BASS] set updated root=1.000 hz=220.000 pool=7
[BASS] applied at beat 42
[MIDI] bass disabled: bass MIDI output 'IAC Driver Bus 4' not found ...; drone/arp/drums continue
```

Notes are logged only on the first trigger after a change, not every step.

---

## 13. Decisions / limitations

- **Bus 4 by default.** The bass does not reuse the drums bus. Pointing it at
  an existing bus is possible in the config, but do not point it at the drums
  bus while drums are enabled: that would open one IAC port with two clients.
- **Plain MIDI, not MPE.** Fixed main/sub channels, standard pitch bend,
  configurable range defaulting to **2 semitones**. It must match the receiving
  instrument; the Stage 6 MPE 48-semitone setting is unrelated.
- **Sub is a second synth track.** A sub note below MIDI note 0 is skipped, not
  transposed.
- **Quantization latency** is up to one quarter beat while running. Stopping the
  transport makes changes immediate.
- **Monophonic gate/retrigger:** the next trigger note-offs the held note; a
  timer note-offs at the gate deadline otherwise. A retrigger invalidates the
  old timer, so no stale late note-off can cut the new note.
- **Determinism:** same set + controls + seed + bar/step produces the same line.
- **Preset vs. manual edits:** manual edits show `custom`; selecting a preset
  overwrites them. Presets reset the pattern length to one bar.
- **Meter/length:** automatic length follows the bar; a custom length survives
  a meter change.
- **Set changes** affect the next trigger, not a held note.
- **No whole-session save/recall** (8.5); the bass seed reproduces its own line,
  not the whole rig.
- **No arp tempo-sync / no Stage 6 timing change.**
- **GUI density:** 8 pt / 32 px / scroll-area layout preserved; the Bass group is
  two compact rows plus a status line.

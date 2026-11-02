# Stage 8.3d Agent Prompt — Lead/Melody Engine + Drone Pad Options

You are implementing **Stage 8.3d** of the JI live-performance system. Stages
0–7.1, 8.1, 8.2, 8.3a, 8.3b and 8.3c are implemented. Stage 8.3a (bass) is
**human-confirmed**. Stage 8.3b (arp parameters + one-entry startup) and 8.3c
(beat-synced arp + self-evolution) are implemented and the owner reports they
look good ("everything seems great", 2026-11-01); treat their documented behavior
as the baseline and do not regress it.

Stage 8.3d is the last melodic-MIDI increment of Stage 8.3: a **lead/melody
engine** and a small set of **drone/pad options**. No audio DSP, no SynthDef, no
sclang change.

The owner's brief:

> the next agent will continue by adding a lead/melody engine (i want some way
> to generate melodies, that also lock with the grooves, create different
> variations on them, and also some kind of consistency in melody that is
> preserved through mutation, and also a simple output for fx. also i want some
> small set of options for the drone (maybe make it more of pad), ex it can
> provide repeating patterns of a certain number of notes, or say on a note for
> a while but have some kind of pitch drop or slide up towards the end for
> effect.

Do not claim human audio/MIDI acceptance. The owner is the authority.

---

## 1. Read these files first

Read the code and docs, not just the summaries:

```text
docs/design_doc_10.28.10.50        §6.11, §6.12, §6.13, §6.14, §6.15, §6.16,
                                   §6.17, §7.1, §8.9, §9, §13, §14
docs/START_HERE.md                 authoritative start/stop/flag sheet
docs/README_stage8_3c.md           accepted sync + self-evolution model
docs/README_stage8_3b.md           arp parameters, quantize, compact GUI
docs/README_stage8_3.md            bass voice (the closest engine to copy)
docs/README_stage8_2.md            groove + quantize model
docs/README_stage8.md              rhythm/drum foundation
docs/README_mpe.md                 MPE bus/channel/bend conventions
docs/prompts/PROMPT_stage8_3b_arp_startup.md
                                   most recent prompt: structure and tone
control/drone.py                   AnchorDrone (hold mode today)
control/arp.py                     8.3b/8.3c arp: settings, order, sync, evolve
control/bass.py                    BassRig/BassScheduler: the model to copy
control/groove.py                  GrooveSpec/GridBar, swing and density
control/rhythm.py                  RhythmEngine/BeatClock, bar math
control/quantize.py                BeatQuantizer phases (PHASE_ARP, PHASE_MIDI)
control/midi_rig.py                MIDIRig, MPEConfig, bus ownership
control/ji_bridge.py               bridge ownership, status, quantized requests
control/osc_server.py              OSC handlers/callbacks
ui/osc_control.py                  pure builders/parsers/store
ui/ji_ui.py                        compact PyQt6 window
config/mpe_config.json             drone/arp ports, timing, sync, evolve, seed
config/rhythm_config.json          drum/groove config
config/bass_config.json            the per-voice config pattern to copy
tests/test_arp.py                  8.3b/8.3c pure tests
tests/test_bass.py                 most complete voice tests
tests/test_mpe.py                  drone/arp/MPE fake-output tests
tests/test_ui.py                   pure + headless Qt tests
tests/test_startup.py              launcher/start-file/docs checks
run/ji_station.py                  one Python launcher (flags + --check)
ji_start.scd                       one sclang entry point
README.md                          current run commands/status
```

Run Python tests from `ji-live/`.

---

## 2. Current state you must preserve

- Stage 5.5 audio: MiniFuse 2 input / BlackHole 16ch output, 2 in / 16 out,
  48 kHz, blockSize 64. SC 0/1 = live autotuned inputs, 2/3 = loops.
- Stage 6: drone on `IAC Driver Bus 1`, arp on `IAC Driver Bus 2`, member
  channel 2, 48-semitone MPE bend range (same for every MPE voice).
- Stage 7/7.1: compact one-window GUI, Status, independent Drone/Arp switches
  in the top-row MIDI group.
- Stage 8.1/8.2: the Python bridge owns BPM/meter/transport; `RhythmEngine`/
  `BeatClock`; drums on optional Bus 3; groove presets/grid/modifiers;
  `control/quantize.py` next-quarter-beat queue; `/ji/quantize/state`.
- Stage 8.3a: bass on optional Bus 4 (plain MIDI, main channel 2, sub channel 3,
  2-semitone bend), `PHASE_BASS`, `/ji/bass/*`, compact Bass group.
- Stage 8.3b: mutable arp parameters, `PHASE_ARP`, `/ji/arp/*` +
  `/ji/arp/state` (17 fields), compact Arp group, `ji_start.scd`,
  `run/ji_station.py`, `docs/START_HERE.md`.
- Stage 8.3c: arp `Sync free|beat` with `1/4|1/8|1/16|1/8T` and a bridge-owned
  phase snap, plus `Evolve` (`evolve_beats`, `evolve_depth`) that regenerates the
  arp pattern from the set snapshot without a mutation. Both default off.
- Do **not** change `/ji/freqset/update`, `synthdefs/*`, `startup.scd` audio
  behavior, `tests/sc/test_live_rig.scd` audio DSP, `test_dual_loop.scd`,
  `test_ableton_routing.scd`, `control/mutation.py`, `control/groove.py`
  semantics, Stage 5.5 routing/loop semantics, or the Stage 6 MPE
  bus/channel/bend contracts.
- Existing tests must keep passing:
  `tests/test_mutation.py`, `tests/test_mutation_controls.py`,
  `tests/test_rhythm.py`, `tests/test_groove.py`, `tests/test_quantize.py`,
  `tests/test_bass.py`, `tests/test_arp.py`, `tests/test_startup.py`,
  `tests/test_mpe.py --pure`, `tests/test_ui.py --pure`, and the optional Qt
  test.
- Compatibility rule: while the transport is stopped, every quantized action
  applies immediately. Direct engine setters and `MutationOSCServer.trigger()`
  remain immediate; quantization belongs to the bridge/UI request layer.
- Keep the compact 8 pt / 32 px / scroll-area GUI. The Arp, Bass, Groove,
  Rhythm, Status and top MIDI groups must still fit or scroll without clipping.
- Keep the one-sclang / one-Python start path working, and update
  `docs/START_HERE.md` (it is the source of truth and is updated by every stage).

---

## 3. What this stage adds

Two new voices, each independently enabled and each degradable on its own:

```text
Lead   -> IAC Driver Bus 5 -> Ableton "Lead" track  (MPE, member ch 2, 48 st)
Drone  -> IAC Driver Bus 1 -> Ableton "Drone" track (MPE, member ch 2, 48 st)
           (same bus as today; the drone gains modes on top of the hold)
```

- **Lead**: generates a melodic phrase, locks to the transport/tempo and to the
  current groove, keeps its identity through frequency-set mutations, and lets
  the player step through deterministic variations. Its own bus makes it a
  ready FX insert point in Ableton.
- **Drone**: keeps today's sustained anchor as the default, and adds a small set
  of pad-like behaviors: a repeating N-note pattern, and a long note that slides
  down or up at the end of its cycle.

Out of scope for 8.3d: polyphonic MPE voice allocation (a true multi-note pad),
SC-side FX DSP (Stage 9), changing the 48-semitone bend range, changing the
frequency-set/mutation algorithm, and any new sclang file.

---

## 4. Part A — Lead/melody engine

### 4.1 Architecture

Create `control/lead.py` and `config/lead_config.json`, mirroring
`control/bass.py`:

```text
LeadConfig     validated config/lead_config.json (port, channel, bend, defaults)
LeadSettings   immutable, validated, replace-by-value settings
LeadMotif      the generated phrase (see 4.2/4.3)
LeadEngine     owns settings + the current motif + the set snapshot
LeadScheduler  daemon thread, drift-corrected deadlines, gate timers
LeadRig        output + engine + scheduler behind one small API
```

- Output: reuse `control/mpe_output.MPEOutput` (`IAC Driver Bus 5`, member
  channel 2, 48-semitone bend) so JI pitches are exact and the Ableton track
  gets the same MPE contract as the drone/arp. The lead is **monophonic** — one
  member channel, one note at a time.
- No `mido` import at module import time or in any constructor; a fake output
  must be injectable exactly like `BassRig`/`MIDIRig`.
- The lead is **independent of `--midi`**, exactly like `--bass`:
  `MIDIRig(midi=False, lead=True)` must construct, open only Bus 5, and play;
  `--lead` without `--midi` is a supported rig (a lead-only MIDI setup), and
  `--midi` without `--lead` must not open Bus 5. Give the lead its own
  `LeadConfig` so it never depends on `config/mpe_config.json`.
- The lead must **degrade gracefully**: if Bus 5 is missing or will not open,
  print one `[LEAD] disabled: ...; drone/arp/drums/bass continue` line and keep
  the rest of the rig running. Do **not** follow the drone/arp `MIDIRig.start`
  path, which raises on a missing port. `--no-lead` and `midi=False` must both
  work without opening Bus 5. Like `--bass`, `--lead` ships **off**: the
  launcher's default command must keep starting exactly the 8.3c rig until the
  owner adds the bus and the flag.
- The scheduler is transport-gated like the drums/bass: it plays only while the
  transport runs, starts at the phrase origin on a transport start, and stops
  with note-off + CC 123. (The arp is deliberately different — it free-runs —
  do not change the arp.)

### 4.2 The consistency contract (this is the heart of the stage)

The lead must keep its melodic identity when the frequency set mutates. Store
each motif note in **set-relative** terms, never as an absolute ratio:

```text
LeadNote:
  step      int    grid step inside the phrase (0-based)
  length    int    grid steps held
  degree    int    position in the *sorted current class list* at birth
  octave    int    octave offset around the lead base octave
  ratio_hint float the ratio the note was born on
  velocity  int    note-on velocity (accented notes are louder)
```

Projection rule (implement exactly, document it):

1. If `ratio_hint` is still in the current sorted set, the note keeps that class
   (exact pitch preserved).
2. Otherwise the note uses the class at `degree`, wrapping modulo the set size
   when the set shrank (`degree % len(set)`).
3. The note's octave offset is unchanged.

Consequences you must test:

- A mutation that does not touch the motif's ratios leaves every frequency
  identical — the melody is literally unchanged.
- A mutation that removes one used ratio changes only that note (to its
  fallback class) and leaves the up/down contour of everything around it intact.
- A `variation` change is the only thing that deliberately rewrites the motif.
- The motif is regenerated only when: the seed changes, a variation is applied,
  `phrase_bars`/`grid` change (new length/rhythm), or the user asks for a new
  motif. `on_set` alone never rewrites it.

`LeadEngine.on_set(ratios, anchor, fundamental)` stores the snapshot and
re-projects the existing motif; it never regenerates it.

### 4.3 Generating the motif

`LeadMotif` is derived deterministically from `seed`, `variation_index`,
`phrase_bars`, `grid` and `density`:

- Phrase length in grid steps = `phrase_bars * steps_per_bar` (bar from the
  meter, `rhythm.bar_quarters` math).
- Placement: every grid step is on with probability `density` (seeded), but
  **step 0 of every bar is always on**, and no more than 3 consecutive steps are
  empty (avoid a hole in the phrase).
- Degrees: start on the anchor's degree when the anchor is in the set, else
  degree 0. Then move by -2..+2 degrees per note with a stepwise bias (about 70%
  of moves are ±1), clamped to `0..len(set)-1`, with `octave` changing only when
  the walk would leave the set (or `octave_range > 0` and a seeded coin passes).
- Velocity: the bar downbeats get `+15` accent (clamped to 127), everything else
  uses the configured velocity.
- Notes are monophonic: the gate is clamped to the next note's start (or the
  phrase end), so a held note never overlaps the next one.
- A motif must survive a set that shrinks to 3 classes and grows to 12.

Document the generator in `docs/README_stage8_3d.md` with one worked example
(seed, grid, density → the step/degree list).

### 4.4 Variations

`variation` selects a deterministic transformation applied to the *current
motif* or, for `random`, a fresh generation. All are seeded by
`(seed, variation_index)` so the same settings always produce the same line.

| Variation | Transformation |
|---|---|
| `identity` | the motif as generated |
| `transpose` | shift every degree by +1 or +2 (seeded, wrapping) |
| `invert` | mirror degrees around the first note's degree |
| `retrograde` | reverse the note order within the phrase, keep the rhythm |
| `thin` | drop every 2nd or 3rd note (seeded) — sparser, same contour |
| `ornament` | insert a passing tone at one empty grid step between two leaps |
| `rhythm` | keep pitches/degrees, redraw which grid steps are on (seeded) |
| `random` | a fresh seeded motif with the same shape rules |

Two triggers:

- **manual** — the GUI `Vary` button and `/ji/lead/vary` advance
  `variation_index` by 1 and apply the resulting variation. It is a normal
  quantized request: the next quarter beat while the transport runs, immediate
  while it is stopped.
- **automatic** — `variation_bars` > 0 applies the next variation every N bars,
  aligned to the bar grid, while the transport runs. `0` disables it.

Log every applied variation once: `[LEAD] variation=invert #3 (phrase 2 bars)`.

### 4.5 Rhythm and groove lock

The lead must sit in the same pocket as the drums:

- Grid: `1/4`, `1/8`, `1/16`, `1/8T` (quarter notes per step 1, 0.5, 0.25,
  1/3).
- Phase: the phrase starts on a bar boundary of the transport origin (use the
  bridge's beat clock, exactly like the drums' step-0 reset; do not invent a
  second phase model).
- Swing: reuse the existing swing-offset rule
  (`control/bass.py::swing_offset` / `control/groove.py`), do not define a new
  one. `groove_link` on (default) means the lead uses the **drum groove's current
  swing** so it moves with the pattern; off means the lead's own `swing`.
- Tempo and meter come from the bridge; a tempo change must not restart the
  phrase or drop a note.

### 4.6 Parameters and defaults

`config/lead_config.json` is the source of defaults; the shipped values must be
musically usable immediately:

```json
{
  "port": "IAC Driver Bus 5",
  "member_channel": 2,
  "bend_range_semitones": 48,
  "enabled": false,
  "grid": "1/8",
  "phrase_bars": 2,
  "density": 0.75,
  "gate_beats": 0.5,
  "velocity": 90,
  "octave_range": 1,
  "base_octave": 0,
  "variation": "identity",
  "variation_bars": 0,
  "swing": 0.0,
  "groove_link": true,
  "seed": 1
}
```

`enabled` ships `false` (the owner ticks **Lead on**), matching the bass. The
launcher flag `--lead` also ships **off**, exactly like `--bass`: without it the
bridge never opens Bus 5 and the Lead group shows a disabled state.

| Control | Range | Notes |
|---|---|---|
| Grid | 1/4, 1/8, 1/16, 1/8T | phrase resolution |
| Phrase bars | 1..8 | motif length |
| Density | 0.1..1.0 | fraction of grid steps with a note |
| Gate | 0.05..4.0 beats | clamped to ≤ the next note |
| Velocity | 1..127 | base velocity; downbeats accented |
| Octave range | 0..2 | how far the line may travel |
| Base octave | -2..+2 | relative to the fundamental |
| Variation | the eight names above | |
| Variation bars | 0..16 | 0 = manual only |
| Swing | 0.0..1.0 | used only when `groove_link` is off |
| Groove link | bool | follow the drum groove's swing |
| Seed | 0..2^31−1 | reproducible motif + variations |
| Enabled | bool | lead on/off; the port stays open |

`/ji/lead/state` (16 values, documented order):

```text
0 int enabled      1 int port_ok        2 string grid
3 int phrase_bars  4 float density      5 float gate_beats
6 int velocity     7 int octave_range   8 int base_octave
9 string variation 10 int variation_index  11 int variation_bars
12 float swing     13 int groove_link   14 int seed
15 int playing
```

### 4.7 The "simple output for FX"

The lead has its own bus on purpose: `IAC Driver Bus 5` → an Ableton **Lead**
track with any instrument and any FX chain (delay, reverb, saturation, a
granular insert, etc.). Requirements:

- MPE member channel 2 with the 48-semitone bend range, like the drone/arp, so
  the same calibration applies and the lead is not a special case.
- Articulation is the FX-relevant surface: `velocity` and `gate_beats` must be
  live and beat-quantized. A short gate leaves the FX tail alone; a long gate
  fills it.
- Monophonic note handling: note-off the held note before the next note-on, and
  every stop/disable path must note-off + CC 123. No stuck notes, and no stale
  late note-off cutting a new note (use a token or the same pattern as
  `BassScheduler`).
- No SC-side FX processing in this stage. Document a suggested Ableton chain in
  the README, but do not build DSP.

### 4.8 Bridge / OSC / quantize

New addresses (UI → bridge):

```text
/ji/lead/set_enabled        int 0/1
/ji/lead/set_grid           string 1/4|1/8|1/16|1/8T
/ji/lead/set_phrase_bars    int 1..8
/ji/lead/set_density        float 0.1..1.0
/ji/lead/set_gate           float beats 0.05..4.0
/ji/lead/set_velocity       int 1..127
/ji/lead/set_octave_range   int 0..2
/ji/lead/set_base_octave    int -2..2
/ji/lead/set_variation      string (the eight names)
/ji/lead/set_variation_bars int 0..16
/ji/lead/set_swing          float 0..1
/ji/lead/set_groove_link    int 0/1
/ji/lead/set_seed           int 0..2^31-1
/ji/lead/vary               none (advance one variation)
```

- `PHASE_LEAD` in `control/quantize.py`, order
  `mutation, octave, groove, drums, bass, arp, lead, drone, midi`. While the
  transport runs, lead edits wait for the next quarter beat; stopped → immediate.
  `/ji/lead/vary` follows the same rule.
- `JIBridge` sends `/ji/lead/state` at start, on `/ji/ui/hello`, after every
  applied change, after a transport change, and after a variation — one small
  message per event, never per note.
- Forward BPM/meter/transport to the lead (`set_lead_bpm`, `set_lead_meter`,
  `set_lead_transport`) exactly like the bass, so the scheduler stays in the
  same clock domain.
- When the groove's swing changes (preset/modifier), push it to the lead when
  `groove_link` is on; one call per change, no per-note traffic.
- `MIDIRig` gains `lead` (independent of the arp/drone) plus `lead_state()`,
  `set_lead_setting()`, `vary_lead()` and `lead_port_ok`.
- `--midi` off, `--lead` off (the default), or a missing Bus 5: handlers log one
  clear line and leave everything else running. `/ji/lead/state` must still be
  well-formed (disabled, `port_ok = 0`) so the GUI can show why.

### 4.9 GUI

Add a compact **Lead** group (below Arp, above Status) with no tabs:

- `Lead on` checkbox, `Grid`, `Variation`, `Phrase` (bars), `Vary` button.
- Knobs: Density, Gate, Velocity, Swing.
- Spins: Octave range, Base octave, Seed; `Var every` (bars) spin.
- Checkbox: `Groove link`.
- Status line: `Lead: on | 1/8 · 2 bars · 0.75 | identity #0 | seed 1`.
- Disabled state when Bus 5 is unavailable (`Lead: disabled ...`), and a
  `playing` indicator if you can fit it.
- Knobs coalesce at ~30 Hz; buttons/combos/spins send immediately; incoming
  state syncs with signals blocked (no feedback OSC). The pending line reflects
  queued lead changes while running.
- Keep the window compact; if the groups no longer fit, the existing scroll area
  is the answer — never clip Status.

---

## 5. Part B — Drone pad options

### 5.1 Keep `hold` exactly

`AnchorDrone` is the Stage 6 drone and the default mode. Its `on_set` behavior
(one held note at the anchor, pitch-bend-only retunes, retrigger on a note
change, no retrigger on an unchanged frequency) must stay **byte-for-byte**
identical in `hold` mode, and `tests/test_mpe.py` must keep passing.

Add modes without forking that behavior: either extend `AnchorDrone` behind a
mode switch or wrap it in a `DroneRig` that owns the `MPEOutput`, the
`AnchorDrone` (hold) and a mode scheduler. `hold` must not need a thread and must
keep the current log line shape.

### 5.2 Modes

| Mode | Behavior |
|---|---|
| `hold` | today's behavior: one sustained anchor note, retuned on mutation, never retriggered while the frequency is unchanged |
| `pulse` | a repeating pattern of `pulse_notes` notes on the anchor pitch class, each at an octave offset drawn from a seeded pattern over `pulse_octave_span`; `pulse_grid` and `pulse_gate` set the rhythm. Monophonic. A short release between notes is fine; the pad character comes from the instrument |
| `bend` | hold the anchor for `hold_beats`, then slide the pitch `slide_semitones` down (or up) over `slide_seconds` as a continuous pitch bend, then note-off, wait for the next quarter beat, and start again at the base pitch |

Rules:

- `bend` must **not** re-trigger the note to slide: it is one held note whose
  pitch bend is interpolated (roughly 30–60 updates per second is plenty).
  Because the bend range is 48 semitones, a ±12-semitone slide is well within
  resolution.
- The slide is a bend, so the receiving instrument must keep the same 48 st
  range; document it.
- A set mutation during `hold_beats` retunes the base pitch as today; a mutation
  during the slide may defer to the next cycle (document which you chose).
- `pulse` and `bend` are transport-synced like the drums: they start on the
  transport start, follow BPM/meter, and stop with note-off + CC 123.
- The `bend` cycle period is **derived**: `hold_beats + slide_seconds in beats`,
  rounded up to the next quarter beat, so the gesture stays on the grid and
  always leaves a small rest before the next note. There is no separate cycle
  control to keep valid — do not add one.
- `hold` remains valid whether or not the transport runs (it must not become
  transport-gated — that would regress Stage 6/7.1).
- The top-row `Drone` switch stays the only mute; the mode does not add a second
  enable path.

### 5.3 Parameters and defaults

`config/drone_config.json` (valid JSON; `pulse_gate` and `hold_beats` are in
quarter beats):

```json
{
  "port": "IAC Driver Bus 1",
  "mode": "hold",
  "velocity": 90,
  "pulse_notes": 4,
  "pulse_grid": "1/4",
  "pulse_gate": 0.5,
  "pulse_octave_span": 1,
  "slide_direction": "down",
  "slide_semitones": 2,
  "slide_seconds": 0.4,
  "hold_beats": 2.5,
  "seed": 1
}
```

`hold` is the shipped default, so a rig without a drone config file (or with the
old Bus 1 wiring) behaves exactly as in Stage 8.3c. The `bend` defaults are
chosen to land on a 4-beat cycle at 120 BPM (2.5 beats hold + 0.4 s slide =
3.5 beats → rounded up to 4), which is a useful starting point for a pad fall.

Derived values are **not** config keys: the `bend` cycle length is
`hold_beats + slide_seconds * bpm / 60`, rounded up to the next quarter beat;
the state reports it as `cycle_beats` so the GUI can show the period.

| Control | Range | Notes |
|---|---|---|
| Mode | hold, pulse, bend | `hold` = Stage 6 |
| Velocity | 1..127 | |
| Pulse notes | 2..8 | pattern length |
| Pulse grid | 1/4, 1/8, 1/16 | |
| Pulse gate | 0.05..2.0 beats | clamped to ≤ the grid step |
| Pulse octave span | 0..2 | ± octaves around the base octave |
| Slide direction | down, up | |
| Slide semitones | 1..12 | |
| Slide seconds | 0.05..4.0 | |
| Hold beats | 0.25..16 | hold before the slide |
| Seed | 0..2^31−1 | reproducible pulse pattern |

Cycle length (`hold` + `slide`, rounded up to the next quarter beat) is derived
and read-only.

`/ji/drone/state` (14 values):

```text
0 int port_ok       1 string mode     2 int velocity
3 int pulse_notes   4 string pulse_grid  5 float pulse_gate
6 int octave_span   7 string direction 8 int slide_semitones
9 float slide_seconds 10 float hold_beats 11 float cycle_beats (derived)
12 int seed         13 int playing
```

### 5.4 Bridge / OSC / quantize

```text
/ji/drone/set_mode            string hold|pulse|bend
/ji/drone/set_velocity        int 1..127
/ji/drone/set_pulse_notes     int 2..8
/ji/drone/set_pulse_grid      string 1/4|1/8|1/16
/ji/drone/set_pulse_gate      float 0.05..2.0
/ji/drone/set_pulse_span      int 0..2
/ji/drone/set_slide_direction string down|up
/ji/drone/set_slide_semitones int 1..12
/ji/drone/set_slide_seconds   float 0.05..4.0
/ji/drone/set_hold_beats      float 0.25..16
/ji/drone/set_seed            int 0..2^31-1
```

- `PHASE_DRONE` between `lead` and `midi`; same immediate/queued rule.
- Send `/ji/drone/state` at start, on hello, and after every applied change.
- Forward BPM/meter/transport with `set_drone_bpm` / `set_drone_meter` /
  `set_drone_transport`.
- `MIDIRig` owns the drone as today (`self.drone`) plus the new mode rig; keep
  the `drone_enabled` switch semantics.

### 5.5 GUI

A compact **Drone** group (next to Arp; one or two rows):

- `Mode` combo, `Velocity` knob.
- Pulse row: `Notes` spin, `Grid` combo, `Gate` knob, `Span` spin.
- Bend row: `Dir` combo, `Semis` spin, `Slide` knob (seconds), `Hold` spin
  (the derived cycle/period is shown in the status line, not as a control).
- Status line: `Drone: hold | 90 | anchor 3/2` / `Drone: pulse 4 · 1/4 | 90` /
  `Drone: bend down 2 st / 0.40 s every 4 beats`.
- Controls disabled when Bus 1 is not open; mode-irrelevant controls disabled
  (pulse row in `bend`, bend row in `pulse`, both in `hold`).

---

## 6. Tests

### 6.1 New pure tests

```bash
uv run --with python-osc python tests/test_lead.py
uv run --with python-osc python tests/test_drone.py
```

`tests/test_lead.py` must cover at least:

- config/settings validation and clamps (grid, bars, density, gate, velocity,
  octave range/base, variation names, variation bars, swing, groove link, seed).
- deterministic motif generation: same seed → same phrase; different seed →
  different phrase; phrase length matches `phrase_bars * steps_per_bar`.
- density: 0.1 produces fewer notes than 1.0; bar downbeats always on.
- **consistency through mutation** (the core test):
  1. build a motif, `on_set` the same set → identical frequencies;
  2. `on_set` a set that keeps every motif ratio → identical frequencies;
  3. `on_set` a set that replaces one used ratio → that one note changes, every
     other note keeps its frequency, and the up/down contour is unchanged;
  4. a set that shrinks to 3 classes still plays (degrees wrap).
- variations: each of the eight produces a valid motif; `identity` is
  unchanged; `retrograde` is the reverse; `transpose` shifts degrees; same
  `(seed, variation_index)` → same output; `vary` advances the index.
- automatic variation every N bars (fake clock or a short real-time run).
- groove lock: swing offsets follow the linked groove; `groove_link=off` uses
  the lead's own swing; a transport stop/start restarts the phrase at the bar
  origin.
- scheduler timing with a fake output: notes land on grid steps (with generous
  tolerances), gate length honored, monophonic, note-off + CC 123 on stop, no
  stuck note, no stale note-off cutting the next note.
- independence and graceful failure: `midi=False, lead=True` opens only Bus 5
  and plays; missing Bus 5 disables only the lead; `--no-lead` (the default)
  never opens Bus 5; a bad lead config disables only the lead.
- bridge quantization: stopped → immediate; running → pending then applied;
  coalescing; transport-stop flush; `/ji/lead/state` shape and handlers.
- no `mido` import during pure construction.

`tests/test_drone.py` must cover at least:

- `hold` mode is **byte-for-byte** the Stage 6 drone: same message stream for
  unchanged-frequency, retune-only, retrigger, and stop (`test_mpe.py` already
  pins part of this — keep it passing, do not duplicate it badly).
- config/settings validation and clamps (mode, velocity, notes, grid, gate,
  span, direction, semitones, seconds, hold; the derived cycle is not a setter).
- `pulse`: N notes per cycle, grid timing, gate length, octave span respected,
  monophonic, deterministic by seed, stop sends note-off + CC 123.
- `bend`: continuous bend updates (monotonic toward the target, within
  ±8191), the note is not retriggered during the slide, the slide returns to the
  base pitch on the next cycle, `slide_direction` up/down, and the derived cycle
  (`hold` + `slide`, rounded up to the next beat) is honored.
- a set mutation during hold retunes; during a slide your documented rule holds.
- transport gating for `pulse`/`bend`; `hold` is not transport-gated.
- missing Bus 1 behavior and `midi=False` behavior are clear and non-fatal where
  the existing rig allows it.
- no `mido` import during pure construction.

### 6.2 Existing tests

```bash
uv run --with python-osc python tests/test_mutation.py
uv run --with python-osc python tests/test_mutation_controls.py
uv run --with python-osc python tests/test_rhythm.py
uv run --with python-osc python tests/test_groove.py
uv run --with python-osc python tests/test_quantize.py
uv run --with python-osc python tests/test_bass.py
uv run --with python-osc python tests/test_arp.py
uv run --with python-osc python tests/test_startup.py
python tests/test_mpe.py --pure
uv run --with python-osc python tests/test_ui.py --pure
```

All must pass. In particular: `MIDIRig(midi=False, drums=False, bass=True)` and
`MIDIRig(midi=False, lead=True)` still work; `midi=False` never opens Bus 1/2,
`lead=False` never opens Bus 5; `JIBridge(...)` still constructs without mido;
the arp defaults and sync/evolution are unchanged; `AnchorDrone` in `hold` is
unchanged.

### 6.3 Optional headless Qt test

```bash
QT_QPA_PLATFORM=offscreen uv run --with python-osc --with PyQt6 \
  python tests/test_ui.py --qt
```

Add checks for the Lead and Drone groups (widgets, defaults, signal-blocked
state sync, no feedback OSC, disabled states, queued/immediate message text,
window still compact). Skip cleanly if PyQt6/Qt is unavailable.

### 6.4 Launcher / docs tests

`tests/test_startup.py` must cover the new flags (`--lead/--no-lead`,
`--lead-config`, `--drone-config`) and
`docs/START_HERE.md` must contain the new flags, the Bus 5 / Lead track setup
row, and the stop procedure unchanged.

### 6.5 Human checks

Audio, MIDI, Ableton, IAC, sclang timing and the live GUI are human-only.
Provide a short checklist in `docs/README_stage8_3d.md` and keep
`docs/START_HERE.md` current.

---

## 7. Human acceptance checklist

Lead (start the rig with `--lead` and tick **Lead on**):

1. `Lead on` plays a melody on Bus 5 through the Ableton Lead track; it starts
   and stops with the transport, stays on the grid, and follows tempo changes.
2. Changing the groove preset (or its swing) with `Groove link` on makes the lead
   move with the drums.
3. `Vary` steps through recognizable variations; the same seed reproduces the
   same phrase and the same variation sequence.
4. Pressing **Mutate** keeps the melody recognizable: the notes whose classes
   survive keep their pitch, and the contour does not collapse.
5. Density/gate/velocity/octave/variation changes are audible and (while the
   transport runs) land on the next beat.
6. The FX chain on the Lead track works; no stuck notes when stopping, muting,
   or closing the GUI.

Drone:

7. `hold` sounds exactly as before.
8. `pulse` repeats N notes on the anchor with the chosen grid/gate/span.
9. `bend` holds, then slides down (and up) at the end of the cycle, repeating
   cleanly; no click/stuck note, and the receiving instrument keeps 48 st.
10. Drone/Arp switches, arp sync/evolve, drums, bass, groove, quantize,
    mutation, autotune, loops and the existing GUI do not regress; the window
    stays compact.

11. Record the result. **8.3d is not complete until the human confirms the lead
    and the drone modes with no audio/MIDI regressions.**

---

## 8. Diagnostics

Use the existing prefixes and add useful lines without per-note spam:

```text
[LEAD] port=IAC Driver Bus 5 channel=2 bend=48 grid=1/8 bars=2 density=0.75
[LEAD] phrase seed=1 variation=identity #0 steps=16 notes=12
[LEAD] variation=invert #3 (phrase 2 bars)
[LEAD] disabled: IAC Driver Bus 5 not available; drone/arp/drums/bass continue
[DRONE] mode=pulse notes=4 grid=1/4 span=1
[DRONE] mode=bend down 2.00 st in 0.800 s every 4.000 beats
[DRONE] slide down 2.00 st over 0.800 s
```

Do not log per-note MIDI during performance (the existing per-step arp line is
opt-in; keep that). Log phrase/variation changes, mode changes, disables, and
the first note after a change.

---

## 9. Risks / decisions to document

- **Consistency model.** Positional degrees plus a per-note ratio hint; document
  the exact fallback and why the melody survives most mutations.
- **Polyphony.** A true multi-note pad needs MPE voice allocation across member
  channels; this stage stays monophonic (one member channel) and gets pad
  character from long notes, slides, and the Ableton instrument. Say so
  explicitly.
- **Transport gating.** Lead (and drone `pulse`/`bend`) are transport-gated like
  the drums/bass; the arp deliberately free-runs. Keep that difference visible.
- **Bus 5 failure.** The lead must never take the rig down; the drone/arp bus
  opening keeps its existing fatal behavior (Bus 1 is core).
- **Swing reuse.** One swing definition across drums/bass/lead; do not fork it.
- **Bend resolution.** 48 st range is fixed; a 12-semitone slide uses a quarter
  of the range, which is plenty. Do not change the range.
- **GUI density.** Two more groups must not push Status off-screen; use the
  scroll area and compact rows.
- **Docs drift.** `docs/START_HERE.md` and the `--help` text are part of the
  acceptance, not an afterthought.

---

## 10. Deliverables

```text
ji-live/
├── control/
│   ├── lead.py                    NEW: LeadConfig/Settings/Motif/Engine/
│   │                              Scheduler/Rig, variations, mutation memory
│   ├── drone.py                   MOD: modes hold/pulse/bend (hold unchanged)
│   ├── midi_rig.py                MOD: lead bus 5, drone modes, state/setters
│   ├── ji_bridge.py               MOD: --lead, /ji/lead/*, /ji/drone/*,
│   │                              PHASE_LEAD/PHASE_DRONE, state, forwarding
│   ├── osc_server.py              MOD: lead/drone handlers
│   └── quantize.py                MOD: PHASE_LEAD, PHASE_DRONE
├── config/
│   ├── lead_config.json           NEW
│   ├── drone_config.json          NEW
│   └── mpe_config.json            MOD only if a shared default really belongs here
├── ui/
│   ├── osc_control.py             MOD: builders/parsers/state for lead+drone
│   └── ji_ui.py                   MOD: compact Lead and Drone groups
├── run/ji_station.py              MOD: --lead/--no-lead, configs, --check
├── tests/
│   ├── test_lead.py               NEW
│   ├── test_drone.py              NEW
│   ├── test_ui.py                 MOD
│   └── test_startup.py            MOD (new flags)
├── docs/
│   ├── README_stage8_3d.md        NEW: design, controls, human checklist
│   ├── START_HERE.md              MOD: flags, Bus 5 / Lead track, stop
│   ├── README_stage8_3c.md        MOD: link to 8.3d
│   ├── README_mpe.md              MOD: lead bus + drone modes
│   └── design_doc_10.28.10.50     MOD: §6.12/§6.13, new §6.18, §7.1, §7.5,
│                                      §8.9, §9, §14
└── README.md                      MOD: status, links, layout
```

If a different split is clearer, keep the same layers and document it. Prefer
additive changes.

---

## 11. Self-review requirement (final response)

Provide:

- Files added/changed.
- For each requirement above, the code that satisfies it or a concrete concern.
- "If I had to bet on which line is most likely to fail on the human's machine,
  which is it, and why? Give a one-line verification command."
- Assumptions, max 5 bullets.
- Explicit statement that audio, MIDI, Ableton, IAC, sclang timing and the live
  GUI were not tested by the agent; 8.3d awaits human acceptance.

Also include the exact one-sclang + one-Python start sequence in the final
response so the human can copy it without opening another file.

# Stage 8.3d — Lead/melody engine + drone pad options

Stage 8.3d is the last melodic-MIDI increment of Stage 8.3. It adds **two
things** and changes no audio DSP, no SynthDef, and no sclang file:

1. **Lead** — a monophonic JI melody voice on its own bus (`IAC Driver Bus 5`),
   transport-locked, groove-linked, and *consistent through mutation*.
2. **Drone pad modes** — `hold` (the Stage 6 drone, unchanged), `pulse` (a
   repeating N-note pattern on the anchor pitch class), `sequence` (a repeating
   figure that walks the current set from the anchor and comes back, one held
   note per **bar** — the harmonic-movement pad the owner asked for) and `bend`
   (hold, then a continuous pitch slide at the end of the cycle).

> **8.3d.1 follow-up:** after the first acceptance pass the owner asked for a
> drone mode that moves between *different notes of the set*, not just octave
> repeats of the anchor, and kept `pulse`. `sequence` is that mode; it is
> additive (mode 4, three new settings, `/ji/drone/state` grew from 14 to 17
> values with the old prefix unchanged).
>
> **8.3d.2 follow-up:** the owner then asked for those notes to be **held for a
> bar or several** so the movement unfolds over bars. `seq_grid` became
> `seq_bars` (note length in bars: `1/4`, `1/2`, `1`, `2`, `4`; default `1`) and
> `seq_gate` is now a **fraction of the note** (`0.1..1.0`, default `1.0` =
> legato with a ~10 ms gap). At 120 BPM in 4/4 one note = 2 s, so the shipped
> 5-note figure takes 10 s. The state field order is unchanged.

Status: implemented and covered by pure tests, **human-approved 2026-11-01**
(the owner used the lead and the drone modes live and confirmed them). Audio,
MIDI, Ableton, IAC, sclang timing and the live GUI remain human-only checks; the
checklist stays in §8 for future regression runs. **The next stage — Stage 8.4
home sets, deterministic journeys and smooth return — is built and awaits its own
human checklist:** [README_stage8_4.md](README_stage8_4.md) (prompt:
[prompts/PROMPT_stage8_4_home_return.md](prompts/PROMPT_stage8_4_home_return.md)).
8.4 adds no lead/drone control and leaves the 8.3d contracts untouched.

---

## 1. What ships where

| Piece | File | Notes |
|---|---|---|
| Lead engine | `control/lead.py` | `LeadConfig/Settings/Note/Motif/Engine/Scheduler/Rig` |
| Lead defaults | `config/lead_config.json` | `enabled: false` — the owner ticks **Lead on** |
| Drone modes | `control/drone.py` | `AnchorDrone` (hold) is untouched; `DroneSettings/Config/Rig` + `PulseScheduler`, `BendScheduler` |
| Drone defaults | `config/drone_config.json` | `mode: hold` |
| Bus/ownership | `control/midi_rig.py` | `lead=True` opens Bus 5 only; `drone_rig` wraps the Stage 6 drone |
| Queue phases | `control/quantize.py` | `PHASE_LEAD` (6), `PHASE_DRONE` (7), `PHASE_MIDI` (8) |
| OSC | `control/osc_server.py` | `/ji/lead/*`, `/ji/drone/*` |
| Bridge | `control/ji_bridge.py` | `--lead`, `--lead-config`, `--drone-config`, state, forwarding |
| Launcher | `run/ji_station.py` | `--lead/--no-lead` (ships **off**), config paths, `--check` |
| GUI | `ui/ji_ui.py`, `ui/osc_control.py` | compact **Lead** and **Drone modes** groups |
| Tests | `tests/test_lead.py`, `tests/test_drone.py` | plus the new groups in `tests/test_ui.py` |

The bus map after 8.3d:

```text
Bus 1  Drone   MPE member 2, 48 st   (hold | pulse | bend)
Bus 2  Arp     MPE member 2, 48 st
Bus 3  Drums   channel 10            (optional)
Bus 4  Bass    main 2 / sub 3, 2 st  (optional)
Bus 5  Lead    MPE member 2, 48 st   (optional, ships off)
```

---

## 2. Lead

### 2.1 Output contract

* `control/mpe_output.MPEOutput` on the lead's own port, MPE **member channel 2**
  and the same **48-semitone** bend range as the drone/arp, so the same Ableton
  calibration applies and the lead is not a special case.
* Monophonic: one member channel, one note at a time. A true multi-note MPE pad
  would need voice allocation across member channels; that is out of scope, so
  the pad character comes from long gates, slides and the instrument/FX chain.
* `config/lead_config.json` is the source of the defaults; the lead never
  reads `config/mpe_config.json`.
* `--lead` is independent of `--midi`, exactly like `--bass`:
  `MIDIRig(midi=False, lead=True)` opens **only** Bus 5 and plays.
* `--lead` ships **off**; the launcher's default command still starts the 8.3c
  rig. Without the flag the bridge never opens Bus 5 and the Lead group shows
  `Lead: disabled ...`.
* A missing/unopenable Bus 5 prints one line and leaves everything else alone:

  ```text
  [LEAD] disabled: IAC Driver Bus 5 not available; drone/arp/drums/bass continue
  ```

  (A `[LEAD] reason: ...` line follows with the underlying error.)

### 2.2 The consistency contract (heart of the stage)

Each motif note is stored **set-relative**, never as an absolute ratio:

```text
LeadNote: step, length, degree, octave, ratio_hint, velocity
```

Projection against the *current* sorted class list is exactly:

1. if `ratio_hint` is still in the set → keep that class (**exact pitch
   preserved**);
2. otherwise → use `current_set[degree % len(current_set)]` (wrap when the set
   shrank);
3. the octave offset is unchanged.

Consequences (all covered by `tests/test_lead.py`):

* a mutation that leaves the motif's ratios alone changes **nothing**;
* a mutation that removes one used ratio changes **only** that note and leaves
  the up/down contour of everything around it intact;
* a set that shrinks to 3 classes still plays (degrees wrap);
* `LeadEngine.on_set(ratios, anchor, fundamental)` stores the snapshot and
  re-projects — it **never** regenerates the motif.

The motif is regenerated only for parameters that define the generated *shape*:
`seed`, `grid`, `phrase_bars`, `density`, `octave_range` (and a meter change,
which changes the phrase length), a `variation`, or `vary()`. Everything else —
`velocity`, `gate_beats`, `base_octave`, `swing`, `groove_link`,
`variation_bars`, `enabled` — is playback-only and applies to the notes already
stored.

> Why `density` is a regeneration trigger: the brief requires density to be
> audible immediately, and density *is* the placement rule. Because the draw is
> seeded only by the seed, density 0.1 is literally a subset of density 1.0 for
> the same seed (tested).

### 2.3 Generating the motif

`LeadMotif` is derived deterministically from `seed`, `variation_index`,
`phrase_bars`, `grid`, `density` (and the set at birth):

* phrase length in grid steps = `phrase_bars × steps_per_bar`, with
  `steps_per_bar = bar_quarters / grid_quarters` (4/4: 4, 8, 16 and 12 steps for
  1/4, 1/8, 1/16 and 1/8T);
* placement: every grid step is on with probability `density` (seeded), **step 0
  of every bar is always on**, and no more than 3 consecutive steps stay empty
  (the 4th empty step is forced on);
* degrees: start on the anchor's degree when the anchor is in the set, else
  degree 0; then move by −2..+2 degrees per note with a stepwise bias (7 of 8
  moves are ±1, ≈70% ±1); when the walk would leave the set — or when
  `octave_range > 0` and a seeded 15% coin passes — the **octave** moves instead
  of the degree, clamped to ±`octave_range`;
* velocity: bar downbeats get **+15** (clamped to 127), everything else uses the
  configured velocity;
* note lengths: `gate_beats` converted to grid steps, then clamped to the next
  note's start (or the phrase end), so the gate can never overlap the next note.

**Worked example** — seed 1, 1 bar, 1/8 grid, density 0.5, JI major set
(1, 9/8, 5/4, 4/3, 3/2, 5/3, 15/8) with anchor 1/1, octave range 1:
`steps = (0, 2, 4, 5)`, `degrees = (0, 0, 1, 0)`, `octaves = (0, -1, -1, -1)`:

| step | length | degree | octave | ratio_hint | velocity |
|---|---|---|---|---|---|
| 0 | 1 | 0 | +0 | 1/1 | 105 (downbeat accent) |
| 2 | 1 | 0 | −1 | 1/1 | 90 |
| 4 | 1 | 1 | −1 | 9/8 | 90 |
| 5 | 1 | 0 | −1 | 1/1 | 90 |

With the shipped defaults (2 bars, 1/8, density 0.75) the phrase is 16 steps
with 13 notes: `steps = (0,2,4,5,6,7,8,9,10,12,13,14,15)`,
`degrees = (0,0,1,0,2,3,5,4,6,6,4,3,2)`.

### 2.4 Variations

`variation` selects the transformation; `Vary` (the button and
`/ji/lead/vary`) advances `variation_index` by one and applies it to the
**current** motif. Everything is seeded by `(seed, variation_index)`, so the
same settings reproduce the same line and the same variation sequence.

| Variation | Transformation |
|---|---|
| `identity` | the motif as generated |
| `transpose` | shift every degree by +1 or +2 (seeded, wrapping) |
| `invert` | mirror degrees around the first note's degree |
| `retrograde` | reverse the note order, keep the rhythm (steps/lengths) |
| `thin` | drop every 2nd or 3rd note (seeded) — sparser, same pitch order |
| `ornament` | insert one passing tone at an empty step between two leaps |
| `rhythm` | keep the pitch sequence, redraw which grid steps are on |
| `random` | a fresh seeded motif with the same shape rules |

Two triggers:

* **manual** — `Vary` / `/ji/lead/vary`: next quarter beat while the transport
  runs, immediate while it is stopped.
* **automatic** — `variation_bars` > 0 applies the next variation every N bars,
  aligned to the bar grid from the transport origin, while the transport runs.

Every applied variation logs once:

```text
[LEAD] variation=invert #3 (phrase 2 bars)
```

Choosing a `variation` in the combo also applies it immediately (at the current
index), so the control is audible when the transport is stopped.

### 2.5 Rhythm, groove lock, phase

* Grid: `1/4`, `1/8`, `1/16`, `1/8T`.
* Phase: the scheduler starts at step 0 on a transport start, so the phrase
  origin is the transport origin and step 0 of every bar is the downbeat. The
  bridge's `BeatClock` remains the only phase model; the lead adds no second
  one. Tempo changes never restart the phrase or drop a note (the next step just
  lands later/earlier).
* Swing: the existing `bass.swing_offset` rule is reused — odd 8th/16th steps
  are delayed by `swing × 0.5 × step_seconds`; a 1/4 grid is straight. There is
  one swing definition, never a fork.
* `groove_link` (default on) means the lead follows the **drum groove's current
  swing**, pushed once per groove change; off means its own `swing`.
* The lead is **transport-gated** like the drums/bass (it starts on a transport
  start and stops with note-off + CC 123). The arp deliberately free-runs; that
  difference is unchanged.

### 2.6 Controls and state

| Control | Range | Notes |
|---|---|---|
| Grid | 1/4, 1/8, 1/16, 1/8T | phrase resolution |
| Phrase bars | 1..8 | motif length |
| Density | 0.1..1.0 | fraction of grid steps with a note |
| Gate | 0.05..4.0 beats | clamped to ≤ the next note |
| Velocity | 1..127 | base velocity; downbeats accented |
| Octave range | 0..2 | how far the line may travel |
| Base octave | −2..+2 | relative to the fundamental |
| Variation | the eight names | |
| Variation bars | 0..16 | 0 = manual only |
| Swing | 0.0..1.0 | used only when `groove_link` is off |
| Groove link | bool | follow the drum groove's swing |
| Seed | 0..2²³¹−1 | reproducible motif + variations |
| Enabled | bool | lead on/off; the port stays open |

`/ji/lead/state` — 16 values, in order:

```text
0 int enabled      1 int port_ok        2 string grid
3 int phrase_bars  4 float density      5 float gate_beats
6 int velocity     7 int octave_range   8 int base_octave
9 string variation 10 int variation_index  11 int variation_bars
12 float swing     13 int groove_link   14 int seed
15 int playing
```

`/ji/lead/note` — `[hz, midi_note, velocity]`, the last triggered note (for the
GUI "Last:" line only).

### 2.7 The "simple output for FX"

Bus 5 exists so the lead is a ready insert point: route `IAC Driver Bus 5` to an
Ableton **Lead** track with any instrument and any FX chain. Suggested starting
chain (no DSP is built in this stage):

```text
Instrument -> EQ Eight (HP 120 Hz) -> Saturator (soft clip, drive 3 dB)
           -> Ping Pong Delay (1/8 dotted, 25% wet, filtered)
           -> Hybrid Reverb (2.5 s, 20% wet) -> Utility
```

Articulation is the FX-relevant surface: `velocity` and `gate_beats` are live and
beat-quantized, so a short gate leaves the delay/reverb tail alone and a long
gate fills it.

---

## 3. Drone modes

### 3.1 `hold` is unchanged

`AnchorDrone` is untouched. In `hold` mode the rig forwards every set update to
it, so the message stream is **byte-for-byte** the Stage 6 stream: unchanged
frequency → nothing, same note/new bend → pitch bend only, new note → note-off +
bend + note-on, stop → note-off + CC 123. `tests/test_drone.py` compares the two
streams message by message, and `tests/test_mpe.py --pure` still passes.

`hold` never needs a thread and is **not** transport-gated (that would regress
Stage 6/7.1).

### 3.2 `pulse`

A repeating pattern of `pulse_notes` notes on the anchor pitch class, each at an
octave offset drawn from a seeded pattern over `pulse_octave_span`
(`random.Random("ji-drone-pulse|seed|notes|span")`, one pattern per seed). The
pattern repeats every N notes; `pulse_grid` and `pulse_gate` set the rhythm.
Monophonic; the gate is clamped to the grid step. Transport-gated like the drums.

### 3.2.1 `sequence` (harmonic movement through the set)

A repeating figure that **moves through the current classes**, not just the
anchor: `seq_notes` notes per cycle, each held for `seq_bars` **bars**, with
`seq_gate` as a fraction of that length.

The contour is `0, +1, +2, … peak, … +1, 0` in **set steps** (one step = the next
class up or down in the current sorted set). It starts on the anchor, climbs, and
comes back; an odd length ends on the anchor, an even length returns to it at the
loop point. Steps that run past the top of the set wrap into the next octave, so
a small set still rises:

```text
set 1/1, 5/4, 3/2   anchor 1/1   seq_notes 5  -> 220, 275, 330, 440, 550 Hz
set 1/1 9/8 5/4 4/3 3/2 5/3 15/8  anchor 1/1  -> 220, 247.5, 275, 247.5, 220 Hz
```

Every note is resolved against the **current** snapshot, so pressing **Mutate**
changes the pitches but never the shape, and the anchor always starts and closes
the figure. Transport-gated like the drums, monophonic.

Timing is meter-aware: one note = `seq_bars × bar_quarters × 60 / bpm`. At
120 BPM in 4/4 that is 2 s per bar-note (a 5-note figure = 10 s), 1.5 s in 3/4,
and 1 s per note at 240 BPM. `seq_gate` 1.0 sounds the note until a ~10 ms gap
before the next note-on; 0.5 leaves a rest between the held notes.

Contours by length: 2 `0,1`; 3 `0,1,0`; 4 `0,1,2,1`; 5 `0,1,2,1,0`; 6
`0,1,2,3,2,1`; 7 `0,1,2,3,2,1,0`; 8 `0,1,2,3,4,3,2,1`.

`pulse` is unchanged: it repeats the anchor **pitch class** at seeded octave
offsets. `sequence` moves through classes; both are pad options.

### 3.3 `bend`

One held note, then a continuous pitch slide — the note is **never retriggered to
slide**:

```text
note-on (base pitch)
  hold            hold_beats
  slide           slide_seconds, ~50 bend updates/s, monotonic, ±8191 clamped
  note-off
  rest            until the derived cycle boundary
```

The cycle is **derived**, never configured:

```text
cycle_beats = ceil(hold_beats + slide_seconds × bpm / 60)
```

The shipped defaults (2.5 beats hold + 0.4 s slide at 120 BPM) give
`2.5 + 0.8 = 3.3 → 4` beats, a useful pad fall that always leaves a rest.

Because the MPE bend range is 48 semitones, a ±12-semitone slide uses a quarter
of the range; the receiving instrument **must keep 48 st**.

Mutation rule during a cycle (documented choice):

* during `hold_beats` the base pitch is retuned exactly as `hold` does
  (bend-only when the MIDI note is unchanged, retrigger when it changes);
* during the slide the new base is **deferred** to the next cycle, because
  re-bending mid-gesture would jump the pitch.

### 3.4 Drone controls and state

| Control | Range |
|---|---|
| Mode | hold, pulse, bend |
| Velocity | 1..127 |
| Pulse notes | 2..8 |
| Pulse grid | 1/4, 1/8, 1/16 |
| Pulse gate | 0.05..2.0 beats |
| Pulse octave span | 0..2 |
| Slide direction | down, up |
| Slide semitones | 1..12 |
| Slide seconds | 0.05..4.0 |
| Hold beats | 0.25..16 |
| Seed | 0..2²³¹−1 |

Cycle length is derived and read-only; `/ji/drone/state` reports it.

`/ji/drone/state` — 17 values, in order (the first 14 are unchanged from the
first 8.3d pass; the three `seq_*` values are appended):

```text
0 int port_ok       1 string mode     2 int velocity
3 int pulse_notes   4 string pulse_grid  5 float pulse_gate
6 int octave_span   7 string direction 8 int slide_semitones
9 float slide_seconds 10 float hold_beats 11 float cycle_beats (derived)
12 int seed         13 int playing
14 int seq_notes    15 string seq_bars   16 float seq_gate
```

`hold` is the shipped default, so a rig without `config/drone_config.json` (or
with the old Bus 1 wiring) behaves exactly as in 8.3c. The bus itself stays
`mpe_config.json`'s `drone_port`; `drone_config.json` owns only the mode and its
parameters.

---

## 4. Bridge / OSC / quantize

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
/ji/lead/vary               none

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
/ji/drone/set_seq_notes       int 2..8
/ji/drone/set_seq_bars        string 1/4|1/2|1|2|4 (bars per note)
/ji/drone/set_seq_gate        float 0.05..2.0
```

* Phase order is `mutation, octave, groove, drums, bass, arp, lead, drone, midi`.
  While the transport runs, every lead/drone edit waits for the next quarter
  beat; stopped → immediate (the Stage 7/8.1 rule).
* `/ji/lead/state` and `/ji/drone/state` are sent at start, on `/ji/ui/hello`,
  after every applied change, after a transport change, after a variation, and
  (for the drone) after a tempo change because `cycle_beats` moves. One small
  message per event, never per note.
* BPM/meter/transport are forwarded with `set_lead_bpm/meter/transport` and
  `set_drone_bpm/meter/transport`, exactly like the bass, so both voices share
  the bridge's clock domain.
* A groove preset/modifier change pushes the groove's swing to the lead once
  (`set_lead_groove_swing`) when `groove_link` is on.
* With `--midi` off, `--lead` off, or a missing Bus 5 / Bus 1, the handlers log
  one clear line and `/ji/lead/state` (or `/ji/drone/state`) stays well-formed
  with `port_ok = 0`.

---

## 5. GUI

* **Lead** group (below Arp, above Status): `Lead on`, `Grid`, `Variation`,
  `Phrase`, `Vary`; knobs Density, Gate, Velocity, Swing; spins Octave range,
  Base octave, Var every, Seed; checkbox `Groove link`; status line
  `Lead: on | 1/8 · 2 bars · 0.75 | identity #0 | seed 1` plus the last note.
* **Drone modes** group (next to Arp): `Mode`, `Velocity`; pulse row `Notes`,
  `Grid`, `Gate`, `Span`; bend row `Dir`, `Semis`, `Slide`, `Hold`; sequence row
  `Seq notes`, `Length` (bars), `Gate` (%); status line `Drone: hold | 90 | anchor 3/2`,
  `Drone: pulse 4 · 1/4 | 90`,
  `Drone: sequence 5 notes · 1 bar | 90` (or `… · 2 bars | 90`) or
  `Drone: bend down 2 st / 0.40 s every 4 beats`. A gated mode (`pulse`,
  `bend`, `sequence`) appends `| waiting for transport (press Play)` while the
  transport is stopped and `| playing` while it runs, so a silent pad is never
  a mystery. `hold` never shows either suffix.
* Knobs coalesce at ~30 Hz; buttons/combos/spins send immediately; incoming
  state syncs with signals blocked (no feedback OSC); the pending line says
  "queued for the next beat" while the transport runs and "applied immediately"
  when it is stopped.
* Both groups are disabled when their port is not open; only the row for the
  current mode is live (pulse row in `pulse`, bend row in `bend`, sequence row in
  `sequence`, none in `hold`). The window
  stays compact and the existing scroll area is the safety net — Status is
  never clipped.

---

## 6. Diagnostics

```text
[LEAD] port=IAC Driver Bus 5 channel=2 bend=48 grid=1/8 bars=2 density=0.75 ...
[LEAD] phrase seed=1 variation=identity #0 steps=16 notes=13
[LEAD] variation=invert #3 (phrase 2 bars)
[LEAD] variation=invert #2 (auto at bar 4, phrase 2 bars)
[LEAD] disabled: IAC Driver Bus 5 not available; drone/arp/drums/bass continue
[DRONE] mode=pulse notes=4 grid=1/4 span=1 seed=1
[DRONE] modes=hold|pulse|bend|sequence mode=hold (pulse/bend/sequence wait for the transport)
[DRONE] mode=sequence notes=5 bars=1 gate=1.00 (starts with the transport (press Play))
[DRONE] sequence started
[DRONE] mode=bend down 2.00 st in 0.400 s every 4.000 beats
[DRONE] slide down 2.00 st over 0.400 s
```

There is no per-note MIDI logging during performance; phrase/variation changes,
mode changes, disables and slides are logged once.

---

## 7. Decisions and risks (as required by the stage prompt)

* **Consistency model.** Positional degrees plus a per-note ratio hint. The hint
  wins when the class survives, so most mutations move nothing at all; a removed
  class falls back to its positional degree (wrapped), so the contour survives.
  Documented in §2.2 and pinned by tests.
* **Polyphony.** Out of scope: a true multi-note pad needs MPE voice allocation
  across member channels. The lead and all drone modes stay monophonic on one
  member channel and get pad character from long notes, slides and the Ableton
  instrument.
* **Transport gating.** Lead and drone `pulse`/`bend` are transport-gated like
  the drums/bass; the arp deliberately free-runs; `hold` is never gated.
* **Bus 5 failure.** The lead never takes the rig down. Bus 1/2 keep their
  existing fatal-on-missing behavior because the drone/arp are core.
* **Swing reuse.** One `bass.swing_offset` definition across drums/bass/lead.
* **Bend resolution.** 48 st is fixed; a 12 st slide uses a quarter of the range.
* **GUI density.** Two more groups; the scroll area absorbs small screens and
  Status stays visible.
* **Docs drift.** `docs/START_HERE.md` and the `--help` text are part of the
  acceptance, not an afterthought.

---

## 7.1 "Only `hold` makes a sound"

`pulse`, `bend` and `sequence` are **transport-gated** (the Stage 8.3d
requirement, the same rule as the drums/bass): they start on the transport start
and stop with note-off + CC 123. `hold` is deliberately not gated (Stage 6/7.1).

So if the transport is stopped, only `hold` sounds. Press **Play** in the Rhythm
group; the drone line then reads `… | playing`, and the bridge logs
`[DRONE] sequence started`. While stopped it reads
`… | waiting for transport (press Play)` and the bridge logs
`(starts with the transport (press Play))`.

The bridge's start-up line names the loaded modes —
`[DRONE] modes=hold|pulse|bend|sequence mode=hold` — so an old bridge process
that does not know `sequence` is obvious in the log. If that line is missing or
says only three modes, an old bridge is still running on port 9000 (closing the
GUI does **not** stop the bridge): quit it and restart `run/ji_station.py`.

## 8. Human acceptance checklist

Start the rig with `--lead` (see §9), open the **Lead** group and tick
**Lead on**:

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
8b. `sequence` walks up through the set and back to the anchor, one held note per
    bar (N notes, `Length` 1/2/4 bars, `Gate` %): a slowly moving pad that still
    follows mutations and the tempo.
9. `bend` holds, then slides down (and up) at the end of the cycle, repeating
   cleanly; no click/stuck note, and the receiving instrument keeps 48 st.
10. Drone/Arp switches, arp sync/evolve, drums, bass, groove, quantize, mutation,
    autotune, loops and the existing GUI do not regress; the window stays
    compact.

11. Record the result. **8.3d is not complete until the human confirms the lead
    and the drone modes with no audio/MIDI regressions.**

---

## 9. Start / test commands

```bash
cd ji-live
# normal rig (8.3c behavior, lead off)
uv run --with python-osc --with mido --with python-rtmidi --with PyQt6 \
  python run/ji_station.py

# with the lead (create IAC Driver Bus 5 + the Ableton Lead track first)
uv run --with python-osc --with mido --with python-rtmidi --with PyQt6 \
  python run/ji_station.py --lead
```

```bash
uv run --with python-osc python tests/test_lead.py
uv run --with python-osc python tests/test_drone.py
QT_QPA_PLATFORM=offscreen uv run --with python-osc --with PyQt6 \
  python tests/test_ui.py --qt
```

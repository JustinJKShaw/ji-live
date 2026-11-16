# JI Station — Live Performance Rig

A SuperCollider + Python + Ableton live-performance environment built around a
mutating just-intonation pitch-class set, two live autotuned inputs, four loop
voices, two MPE MIDI buses (anchor drone + set arp), and a drum voice. Stage 7
adds a single-window PyQt6 control surface; Stage 7.1 adds independent
Drone/Arp switches; Stage 8.1 adds transport/tempo/meter, free or beat-locked
loops, and the drum engine; Stage 8.2 adds groove generation (presets, density,
syncopation, swing, probability, a per-step grid) and beat-quantized state
changes. Stage 8.3a adds the monophonic bass voice. Stage 8.3d adds the lead/
melody voice (Bus 5) and the drone pad modes (hold/pulse/bend). Stage 9.0 adds
a per-loop post-`PitchShift` FX bus with a harmonic-series delay (Cascade and
continuous Delay, free or fixed musical time) and repairs the loop count-in.
Stage 9.1 makes the lead playable — MIDI controller or QWERTY, nearest class of
the current set, bar-aligned recording/quantisation, saved melodies, and the
played phrase inside the existing lead Variation system with Revert. Stage 9.2
lets the input override the loop live, makes the count-in audible, and adds
recorded pitch-bend / mod-wheel expression.

**Current status:** the owner reports the live GUI/audio/MIDI rig works well.
Stage 7/7.1, Stage 8.1, and Stage 8.2 are human-tested, accepted, and in live
use (2026-10-31). Stage 8.3a (bass) is **human-confirmed** (2026-10-31).
**On 2026-11-02 the owner approved every stage that was awaiting a human
checklist: 8.3b, 8.3c, 8.3d, 8.4 and 8.5.** The earlier drone-receive concern is
closed in the current rig; see [docs/README_stage7.md](docs/README_stage7.md).

**Stage 8.1 is human-confirmed.** It adds rhythm foundation + drum engine v1
(transport, tempo, meter, free/beat loops, per-loop phase, drum MIDI on an
optional third bus, four-on-the-floor, lane toggles, half/double time). Setup,
controls, and limitations are in [docs/README_stage8.md](docs/README_stage8.md).

**Stage 8.2 is human-confirmed.** It adds groove presets (`four`, `break`,
`half`, `techno`), density, syncopation, swing, probability, a seeded per-step
16-lane grid, and a bridge-side next-quarter-beat queue for mutation/octave,
Drone/Arp/drum enables, drum rate/lanes, and groove edits (immediate while the
transport is stopped). The Stage 8.1 beat-mode loop scheduling bug is fixed in
8.2.2. Setup, controls, the groove model, quantization semantics, and the
acceptance record are in [docs/README_stage8_2.md](docs/README_stage8_2.md).

**Stage 8.3a is human-confirmed.** It adds a monophonic, transport-synced JI bass
on an optional `IAC Driver Bus 4` (main channel 2, optional sub channel 3):
`pulse`/`techno`/`chaos` presets, density/syncopation/swing/gate/accent,
root/anchor/walk/random/cycle pitch modes, octave movement, a seeded line, a
1/4–1/16 grid, and beat-quantized bass changes. Setup, controls, and the human
checklist are in [docs/README_stage8_3.md](docs/README_stage8_3.md).

**Stage 8.3b is owner-approved (2026-11-02).** It adds mutable arp
parameters (step, gate, velocity, three octave offsets, shared mode, playback
order, seed — beat-quantized like the other performance controls) and a
one-sclang + one-Python startup path.

**Stage 8.3c is owner-approved (2026-11-02).** It can lock the arp to the
beat (`Sync free|beat` + a `1/4`, `1/8`, `1/16`, `1/8T` division, snapped to the
bridge tempo) and let it evolve on its own (`Evolve` every N beats with a depth
control) without any mutation. Both are off by default, so the accepted
free-running arp is unchanged.

**Stage 8.3d is human-approved (2026-11-01).** It adds a monophonic
transport-locked **lead/melody** voice on its own optional `IAC Driver Bus 5`
(MPE member 2, 48 st — a ready FX insert point) with a seeded motif that keeps
its identity through frequency-set mutations, eight deterministic variations
(manual `Vary` or every N bars), groove-linked swing, and beat-quantized
controls. It also gives the drone pad modes on top of the untouched Stage 6
`hold`: `pulse` (a repeating N-note pattern on the anchor pitch class),
`sequence` (a repeating figure that walks the current set from the anchor and
back — harmonic movement that still follows mutations) and `bend` (hold, then one
continuous pitch slide with a derived cycle). `--lead` ships **off**, so the
default command still starts the 8.3c rig. Design, the consistency contract, the
worked example and the human checklist are in
[docs/README_stage8_3d.md](docs/README_stage8_3d.md).

**Stage 8.4 is owner-approved (2026-11-02).** It adds four **home** slots
(`A`–`D`), a seeded **journey**, an optional **drift** cap, **Back**, and a
beat-spaced **return** that lands exactly on a captured set in
`ceil(distance / max_changes)` quarter beats — the basis for set chord
progressions. The mutation algorithm is untouched; the "future state" idea lives
in a bridge-layer drift policy that ships off. Design, the worked example, the
OSC/state surface and the human checklist are in
[docs/README_stage8_4.md](docs/README_stage8_4.md). The prompt stays at
[docs/prompts/PROMPT_stage8_4_home_return.md](docs/prompts/PROMPT_stage8_4_home_return.md).

**Stage 8.4.1 is owner-confirmed (2026-11-02).** It makes the Mutation
**Glide** knob the stepwise/replaced **class melt** time: when a mutation
replaces one class with another, the class itself travels from the old ratio to
the new one over the knob time (30 Hz, cents-linear) and every voice follows —
live autotune, loops, drone, bass, lead and the arp (the arp keeps its pattern
and plays the nearest current class until the final rebuild). Pure add/remove
edits snap at 0.005 s and no longer use the knob. No new flag, OSC address or
config key; no sclang/SynthDef/DSP change. Design and checklist:
[docs/README_stage8_4.md](docs/README_stage8_4.md) §6.1.

**Stage 8.5 is owner-approved (2026-11-02).** It saves the whole
performance configuration under a name and recalls it later without restarting
anything: frequency set, mutation controls, home slots/journey, tempo/meter,
drums, groove + grid, and every bass/arp/lead/drone setting (plus the lead
variation index). The **State** row (Name / Save / Load / Delete / Refresh) sits
under Home; a recall is **one** set change and **one** `/ji/freqset/update`,
immediate while stopped and on the next quarter beat while running. Loops
(explicitly excluded by the owner), the sclang autotune parameters, the transport
and the Back history are **not** part of a state. Files live in
`ji-live/states/<name>.json` (`--state-dir` overrides). Design, format, procedure
and the human checklist: [docs/README_stage8_5.md](docs/README_stage8_5.md).

**Stage 9.0 is built (2026-11-09) and awaits the owner's acceptance.** It adds a
per-loop, post-`PitchShift` **FX bus + harmonic-series delay** with selectable
**Cascade** (captured segment, harmonics entering in order) and **Delay**
(continuous harmonized buffer delay) modes, a **fixed musical time** option
(quarter notes, dotted, triplets, a bar; owner follow-up), and the **count-in
repair** (free-mode second-Rec skip, stale/generation state bugs, ceil bar
rounding). Four private audio buses (18–21 on the owner's 2-in/16-out boot;
the actual indices are reported), one 2 s scratch buffer per loop,
one Harm GUI row (1280×1198). No bridge/state/config change; loop FX are
session-only. Design and checklist:
[docs/README_stage9_0.md](docs/README_stage9_0.md). Prompt:
[docs/prompts/PROMPT_stage9_0_harmonic_delay.md](docs/prompts/PROMPT_stage9_0_harmonic_delay.md).

**Stage 9.1 is owner-confirmed functional (2026-11-14).** It makes
the lead **playable**: a MIDI controller or the QWERTY keyboard plays notes that
sound as the **nearest frequency class** of the current set (in the register
played), the line records on a silent bar-line count-in and is quantised to the
lead grid on the **swung** groove (the stored step stays unswung), and it can be
saved as a named melody in `melodies/` or inline in a performance state. The
take becomes the lead motif, so `Vary`, `Var every` and all eight variations
keep working; **Revert** restores the recorded base. One compact **Lead input**
group (Port/Refresh, Keys/Live/Rec/Stop/Revert/Clear, Quant/Count-in/Bars, the
melody library) plus a `Note len` checkbox next to the Lead `Gate` knob; the
window is 1280×1258. The input is **Python-only** and adds no bus, SynthDef or
sclang change. Start with `--lead --lead-in` (both off by default). Design,
mapping maths, the quantiser, OSC and the human checklist:
[docs/README_stage9_1.md](docs/README_stage9_1.md). Prompt:
[docs/prompts/PROMPT_stage9_1_lead_input.md](docs/prompts/PROMPT_stage9_1_lead_input.md).

**Stage 9.2 is built (2026-11-14) and awaits the owner's acceptance.** It makes
the lead feel like one continuous instrument: the input **always overrides** the
loop (no `Live` toggle — a played key releases the loop's sounding note and the
loop's attack resumes on key-up when its gate is still open, else on the next
step), the **count-in is audible** (a key held across the bar line is in the
take from step 0), and **moving pitch bend and mod wheel** are output on top of
the class chosen at key-down and **recorded with the take**, replayed by the
scheduler with the phrase and round-tripped through melodies and performance
states. `source=live` is a deprecated alias; `record` still stops the sequencer
for a clean take. The `Lead` input group loses its `Live` checkbox and its
status line shows `bend +12 ¢ - mod 64`; the window stays 1280×1258. The
controller's bend range is `bend_range_semitones` in
`config/lead_input_config.json` (default 2; Bus 5 stays 48 st). The input is
**Python-only** and adds no bus, SynthDef or sclang change. Design, the exact
override sequence, the bend maths, the data model and the human checklist:
[docs/README_stage9_2.md](docs/README_stage9_2.md). Prompt:
[docs/prompts/PROMPT_stage9_2_lead_override_expression.md](docs/prompts/PROMPT_stage9_2_lead_override_expression.md).

**Stage 8.8 is built (2026-11-05) and awaits the owner's acceptance.** It adds a
read-only **Fretboard** improvisation aid: for every ratio in the live JI set it
shows the nearest 12-TET note and the signed cents, then marks every occurrence
of those notes on a full guitar neck. The tuning is fully editable — nine
presets including **Drop D** (`D2 A2 D3 G3 B3 E4`), DADGAD, Open G/D and the
7/8-string defaults — plus one combo per string for any MIDI note 0–127, 6–8
strings and 0–24 frets. It is a **pitch-class** map (a note is marked in every
octave), follows the existing `/ji/freqset/changed` with no restart, and is
**read-only**: the tuning is a session-only UI setting (it resets to Standard E
when the GUI restarts), sends no OSC, and is never saved in a performance state
or `config/`. The group grows the default window to 1280×1172 (9.0's Harm row
grows it again to 1280×1198). Details, the exact maths, the worked A-major
table, the risks and the human checklist:
[docs/README_stage8_8.md](docs/README_stage8_8.md).

**Stage 8.7 is built (2026-11-03) and awaits the human listening check.** It adds
a **breathing rhythm engine** and an optional **bass hit row**. One master breath
makes the played grid inhale/exhale — events arrive early/late and velocities
swell — with adjustable `Depth`, `Period` (1–32 bars), `Shape`
(sine/triangle/inhale/exhale), `Intensity`, `Phase`, an `Arp local` amount
that makes the arp breathe half a cycle against the groove, and an `Arp sync`
option that puts the arp on the master phase so it breathes with everything
else (8.7.1). The **canonical
tempo, the bar grid, the quantized `PHASE_*` queue and sclang loop timing are
never touched**: the breath is a clamped (±45 % of a step) scheduling offset
inside each voice, and the curve has zero mean slope, so there is no drift.
Breath off is exactly 8.6.2. Each loop voice also gains its own local grain
breath (`Breath`/`B.Rate` in the Morph row, 0 % = exact bypass). The bass gains
one editable row of hits per bar (no pitch, no per-step velocity) with `Hits`,
`Copy` and `Clear`; the row is optional, re-shapes with the meter/subdivision,
is saved in the performance state, and never overloads `grid` (the
subdivision). The Rhythm group gains one Breath row, the Bass group a hidden
**Hits** row, and the window became 1280×966 (1280×1172 with 8.8's Fretboard
group). Details, the maths, the OSC/GUI surface, the risks and the human
checklist: [docs/README_stage8_7.md](docs/README_stage8_7.md).

**Stage 8.6.2 is built (2026-11-03) and awaits the human loop check.** One
**Morph** slider now walks a loop from its **original playback** into the
smeared grain cloud: 0 % is the dry loop through the accepted autotune, 100 %
the full grain texture, in between an equal-power crossfade of the two. A
**Melt** toggle starts a loop as the original and progressively melts it into
the grains (and back), and a post-autotune **moving filter** (a sweeping lowpass
plus a low cut) fuses the two layers for extra texture. The defaults leave the
accepted loop bit-identical. Details:
[docs/README_stage8_6.md](docs/README_stage8_6.md) §15.

**Stage 8.6.1 is built (2026-11-03) and awaits the human loop check.** It
redesigns the granular half of 8.6 (which the owner rejected as "annoying noises
playing grains over and over"): the grain read position now **travels** through
the buffer (`Motion` off/forward/backward/pingpong/drift with `Speed`,
`Depth` and `Freeze`), the cloud has real density/size defaults (Rate 20,
Size 120 ms, Pos 50 %, Scatter 10 %) with per-grain `Size Jit` and `Pitch Jit`,
`Pitch` snaps to the **current JI set** (`Snap`, with the effective ratio in the
status line and a re-snap on every set mutation), a post-autotune `Smear` adds
blur/space (0 = dry), and beat mode fires a small **burst** per division instead
of one tick. It also fixes the `Div` combo jumping back to 1/16 (sclang compared
division Strings by identity, so GUI division/count-in edits were silently
rejected). One more compact GUI row; the window is 1280×910 (1280×940 with the
8.6.2 Morph row). Normal playback is
unchanged and still bit-identical to the accepted loop. Details:
[docs/README_stage8_6.md](docs/README_stage8_6.md) §14.

**Stage 8.6 is built (2026-11-02) and awaits the human loop check.** It adds
loop editing on top of the accepted loop: session-only memory slots (`A`–`D`,
a copy of a loop's audio **and** edit state — no files), overdub (`Dub` records
over a loop for one pass; `Rec` still replaces), reverse, varispeed playback at
11 fixed length ratios (`2/1` … `1/2`) that re-pitches the loop but still runs it
through the JI autotune (the set may pull it partly back — that is the intended
"new material"), granular playback (free or beat-synced) with
rate/size/position/scatter/pitch/per-grain-reverse, per-loop gain, a selectable
count-in before a Rec (`off` / `1 beat` / `1 bar` / `2 bars`) and auto-play when
a take ends. Everything ships at the accepted defaults (rate `1/1`, normal,
forward, gain 0.70, count-in off, auto-play off), so a loop still sounds exactly
as before. The four-loop count, the four-channel audio map, the bridge and the
performance-state format are unchanged, and **loops are still never written to
disk**. Details, the DSP graph, the OSC surface and the human checklist:
[docs/README_stage8_6.md](docs/README_stage8_6.md).

**Start here:** [docs/START_HERE.md](docs/START_HERE.md) is the authoritative
start/stop/flag sheet. It is updated with every stage. Stage 8.3b details are in
[docs/README_stage8_3b.md](docs/README_stage8_3b.md); the arp sync/evolution
controls and their human checklist are in
[docs/README_stage8_3c.md](docs/README_stage8_3c.md).

## Layout

```text
ji-live/
├── README.md                  # this file
├── ji_start.scd               # NEW: the only sclang file you evaluate
├── startup.scd                # server/IO/SynthDefs (loaded by ji_start.scd)
├── run/
│   ├── ji_station.py          # the only Python command (bridge + GUI)
│   └── midi_probe.py          # Stage 9.2 read-only controller/wheel probe
├── synthdefs/                 # SuperCollider SynthDefs (do not edit casually)
│   ├── ji_loop_voice.scd      # Stage 9.0 normal/reverse/varispeed/granular+morph+FX send
│   ├── ji_harmonic_delay.scd  # Stage 9.0 per-loop Cascade/Delay harmonic FX
│   ├── ji_buf_copy.scd        # Stage 8.6 server-side mono buffer copy
├── control/                   # mutation engine, bridge, MPE MIDI, rhythm, drums
│   ├── rhythm.py              # tempo/meter/transport, BeatClock, math
│   ├── breath.py              # Stage 8.7 breathing curves + shared BreathClock
│   ├── groove.py              # Stage 8.2 presets, grid, modifiers, seed
│   ├── quantize.py            # Stage 8.2 next-quarter-beat state queue
│   ├── drums.py               # drum config, generated pattern, swing, scheduler
│   ├── arp.py                 # Stage 6/8.3b/8.3c arp: settings, orders,
│   │                          # beat sync, evolution
│   ├── bass.py                # Stage 8.3a monophonic JI bass voice
│   ├── lead.py                # Stage 8.3d/9.1/9.2 lead: motif, variations,
│   │                          # played takes, override, expression playback
│   ├── lead_input.py          # Stage 9.1/9.2 map, input port, recorder, bend/mod
│   ├── melody.py              # Stage 9.1 melodies/<name>.json library
│   ├── drone.py               # Stage 6 hold drone + Stage 8.3d pulse/bend
│   ├── glide.py               # Stage 8.4.1 class-melt planner + scheduler
│   ├── home.py                # Stage 8.4 slots, planner, journeys, history
│   └── state.py               # Stage 8.5 performance states: format/library
├── ui/                        # PyQt6 GUI, pure OSC layer, sclang UI surface
│   ├── ji_ui.py               # one window (Lead input, Fretboard, 9.0 Harm row)
│   ├── osc_control.py         # pure builders/parsers/store/controller
│   └── fretboard.py           # Stage 8.8 pure note/tuning/fretboard maths
├── config/                    # routing, MPE, rhythm, bass, home, lead, drone,
│                              # lead_input.json (Stage 9.1 input defaults)
├── melodies/                  # Stage 9.1 saved melodies (created on first save)
├── tests/
│   ├── test_mutation.py
│   ├── test_mutation_controls.py
│   ├── test_mpe.py
│   ├── test_rhythm.py
│   ├── test_breath.py         # Stage 8.7 curves/clock/drift/scheduler seams
│   ├── test_groove.py
│   ├── test_quantize.py
│   ├── test_bass.py
│   ├── test_arp.py            # arp settings/orders/timing/bridge
│   ├── test_lead.py           # Stage 8.3d/9.1 lead: motif/consistency/scheduler
│   ├── test_lead_input.py     # Stage 9.1/9.2 map/record/library/override order
│   ├── test_lead_expression.py # Stage 9.2 bend maths/override/recorder/count-in
│   ├── test_drone.py          # Stage 8.3d drone: hold/pulse/bend
│   ├── test_home.py           # Stage 8.4 distance/plan/return/back/drift
│   ├── test_glide.py          # Stage 8.4.1 class melt + bridge cancel/fan-out
│   ├── test_state.py          # Stage 8.5 state format/library/recall
│   ├── test_fretboard.py      # Stage 8.8 note maths/tuning/fretboard cells
│   ├── test_startup.py        # launcher/start-file/docs
│   ├── test_ui.py
│   └── sc/                    # sclang test scripts
│       ├── test_tartini.scd
│       ├── test_autotune.scd
│       ├── test_bridge.scd
│       ├── test_dual_loop.scd
│       ├── test_ableton_routing.scd
│       ├── test_live_rig.scd
│       ├── test_loop_edit.scd # Stage 9.0 human-run loop-edit/FX/count-in check
│       └── test_rhythm.scd
└── docs/
    ├── START_HERE.md          # NEW: authoritative start/stop/flag sheet
    ├── design_doc_10.28.10.50
    ├── README_stage0.md … README_stage8.md
    ├── README_stage8_2.md
    ├── README_stage8_3.md
    ├── README_stage8_3b.md
    ├── README_stage8_3c.md
    ├── README_stage8_3d.md    # lead/melody + drone pad modes
    ├── README_stage8_4.md     # home sets / journeys / smooth return
    ├── README_stage8_5.md     # performance state save / recall
    ├── README_stage8_6.md     # loop editing: slots/dub/reverse/rates/grain
    ├── README_stage8_7.md     # breathing rhythm engine + bass hit row
    ├── README_stage8_8.md     # 12-TET fretboard improvisation aid
    ├── README_stage9_0.md     # loop FX bus + harmonic-series delay + count-in fix
    ├── README_stage9_1.md     # lead MIDI/QWERTY input, recording, melodies
    ├── README_stage9_2.md     # lead override, audible count-in, bend/mod curves
    ├── START_HERE_stage8_1.txt … _stage8_3.txt   (archival)
    ├── README_live_rig.md
    ├── README_mpe.md
    └── prompts/
        ├── PROMPT_stage5.md
        ├── PROMPT_stage7_gui.md
        ├── PROMPT_stage8_1_rhythm.md
        ├── PROMPT_stage8_2_groove.md
        ├── PROMPT_stage8_3_bass.md
        ├── PROMPT_stage8_3b_arp_startup.md
        ├── PROMPT_stage8_3d_lead_drone.md
        ├── PROMPT_stage8_4_home_return.md
        ├── PROMPT_stage8_5_state_recall.md
        ├── PROMPT_stage8_6_loop_editing.md
        ├── PROMPT_stage8_7_breath.md
        ├── PROMPT_stage8_8_fretboard.md
        ├── PROMPT_stage9_0_harmonic_delay.md
        └── PROMPT_stage9_1_lead_input.md
```

Run all commands from `ji-live/`.

## Quick start (two actions)

Full detail, every flag, the one-time IAC/Ableton setup, and troubleshooting are
in **[docs/START_HERE.md](docs/START_HERE.md)**.

1. **sclang** — evaluate `ji_start.scd` (one file; it loads `startup.scd`,
   `tests/sc/test_live_rig.scd`, then `ui/control_surface.scd` and prints a
   `[JI] ... ready ...` line).
2. **terminal** — one command starts the bridge and the GUI:

   ```bash
   cd ji-live
   uv run --with python-osc --with mido --with python-rtmidi --with PyQt6 \
     python run/ji_station.py --midi --drums --bass
   ```

   `--midi --drums --bass` are the defaults, so `python run/ji_station.py`
   alone is enough. Use `--no-midi`, `--no-drums`, `--no-bass`, `--no-gui`
   or `--no-bridge` to drop a voice; add `--lead` for the Stage 8.3d melody
   (off by default, needs IAC Bus 5 + an Ableton Lead track), and
   `--lead --lead-in` to play/record it from a MIDI controller or the QWERTY
   keys (`--lead-in-port "<name>"`, `--melody-dir <path>`); `--dry-run`
   prints the exact child commands; `--check` validates imports, configs, the
   state directory, and mido output names; `--state-dir` points the State row at
   another folder (default `ji-live/states/`).

3. **Ableton** must be running with the Drone / Arp / Drums / Bass / Sub tracks
   (and Lead when `--lead` is used); see §1 of `docs/START_HERE.md`.

The older three-file sclang path and the two direct Python commands still work
for debugging:

```bash
uv run --with python-osc --with mido --with python-rtmidi \
  python control/ji_bridge.py --interactive --midi --drums --bass --glide-time 0.15
uv run --with python-osc --with PyQt6 python ui/ji_ui.py
```

If `ji_start.scd` (or `tests/sc/test_live_rig.scd` by hand) is re-evaluated,
re-evaluate `ui/control_surface.scd` too — `ji_start.scd` does that for you.

## Tests

Python harnesses can be run directly from `ji-live/`; each adds the project
root to `sys.path`, so they work from their subdirectory.

```bash
uv run --with python-osc python tests/test_mutation.py
uv run --with python-osc python tests/test_mutation_controls.py
python tests/test_mpe.py --pure
uv run --with python-osc python tests/test_rhythm.py
uv run --with python-osc python tests/test_breath.py
uv run --with python-osc python tests/test_groove.py
uv run --with python-osc python tests/test_quantize.py
uv run --with python-osc python tests/test_bass.py
uv run --with python-osc python tests/test_arp.py
uv run --with python-osc python tests/test_lead.py
uv run --with python-osc python tests/test_lead_input.py
uv run --with python-osc python tests/test_lead_expression.py
uv run --with python-osc python tests/test_drone.py
uv run --with python-osc python tests/test_home.py
uv run --with python-osc python tests/test_state.py
uv run --with python-osc python tests/test_startup.py
uv run --with python-osc python tests/test_ui.py --pure
uv run python tests/test_fretboard.py          # Stage 8.8 (stdlib only)
# Stage 8.6.2 loop editing (slots/dub/rates/granular/morph) is covered by
# test_ui (pure + Qt) and, for audio, by the human-run tests/sc/test_loop_edit.scd
# Stage 8.7 breath + bass hits: tests/test_breath.py (curves/clock/drift),
# test_bass.py, test_state.py and test_ui.py (pure + Qt)
# Stage 8.8 fretboard: tests/test_fretboard.py (pure) + the Qt additions in
# tests/test_ui.py (group, live set, Drop D, Custom, strings/frets, height)
# Stage 9.1 lead input: tests/test_lead_input.py (mapping/quantiser/recorder/
# port/rig/record machine/library/played motif/inline states) + test_lead,
# test_state, test_ui --pure and test_startup additions
# Stage 9.2 lead override + expression: tests/test_lead_expression.py (bend
# maths/port/rig/recorder/validation/scheduler/override/count-in) + additive
# test_lead_input, test_lead, test_state, test_ui --pure and test_startup checks
# Stage 9.0 loop FX + count-in: tests/test_ui.py --pure (harm constants/parser/
# builders/fx_fire, ceil count-in), the Qt Harm row, and the human-run
# tests/sc/test_loop_edit.scd / test_live_rig.scd

# optional headless GUI smoke test (needs PyQt6)
QT_QPA_PLATFORM=offscreen uv run --with python-osc --with PyQt6 \
  python tests/test_ui.py --qt
```

The package form also works:

```bash
python -m tests.test_mutation
python -m tests.test_ui --pure
```

`tests/sc/*.scd` are evaluated in sclang, not by Python.

## Documentation

- [START HERE — start/stop/flag sheet](docs/START_HERE.md)
- [GUI parameter reference — every control, one line each](docs/GUI_PARAMETERS.md)
- [Stage 9.2 lead override, audible count-in, pitch-bend / mod-wheel expression](docs/README_stage9_2.md)
- [Stage 9.1 lead MIDI/QWERTY input, recording, quantisation, saved melodies](docs/README_stage9_1.md)
- [Stage 9.0 loop FX bus + harmonic-series delay + count-in repair](docs/README_stage9_0.md)
- [Stage 8.8 12-TET fretboard improvisation aid](docs/README_stage8_8.md)
- [Stage 8.7 breathing rhythm + bass hit row](docs/README_stage8_7.md)
- [Stage 8.6.2 morph — original loop ↔ grains, melt, moving filter](docs/README_stage8_6.md#15-stage-862--morph-melt-and-the-moving-filter-2026-11-03)
- [Stage 8.6.1 granular redesign — motion, cloud, JI snap, smear](docs/README_stage8_6.md#14-stage-861--granular-redesign-2026-11-03)
- [Stage 8.6 loop editing — slots, dub, reverse, rates, granular](docs/README_stage8_6.md)
- [Stage 8.5 performance state save / recall](docs/README_stage8_5.md)
- [Stage 8.4 home sets / journeys / smooth return](docs/README_stage8_4.md)
- [Stage 8.3d lead/melody + drone pad modes](docs/README_stage8_3d.md)
- [Stage 8.3c beat-synced arp + self-evolution](docs/README_stage8_3c.md)
- [Stage 8.3b arp parameters + one-entry startup](docs/README_stage8_3b.md)
- [Stage 8.3a monophonic bass](docs/README_stage8_3.md)
- [Stage 8.2 groove + beat-quantized state](docs/README_stage8_2.md)
- [Stage 8.1 rhythm foundation + drum engine](docs/README_stage8.md)
- [Stage 8.4 home sets / journeys / smooth return prompt](docs/prompts/PROMPT_stage8_4_home_return.md)
- [Stage 8.4.1 stepwise/replaced class-glide patch prompt](docs/prompts/PROMPT_stage8_4_1_stepwise_glide.md)
- [Stage 8.5 performance state save/recall prompt](docs/prompts/PROMPT_stage8_5_state_recall.md)
- [Stage 9.2 lead override + expression prompt](docs/prompts/PROMPT_stage9_2_lead_override_expression.md)
- [Stage 9.1 lead input prompt](docs/prompts/PROMPT_stage9_1_lead_input.md)
- [Stage 9.0 loop FX bus + harmonic-series delay prompt](docs/prompts/PROMPT_stage9_0_harmonic_delay.md)
- [Stage 8.8 12-TET fretboard improvisation aid prompt](docs/prompts/PROMPT_stage8_8_fretboard.md)
- [Stage 8.7 breathing rhythm + bass hits prompt](docs/prompts/PROMPT_stage8_7_breath.md)
- [Stage 8.6.1 granular redesign prompt](docs/prompts/PROMPT_stage8_6_1_granular_redesign.md)
- [Stage 8.6 loop editing prompt](docs/prompts/PROMPT_stage8_6_loop_editing.md)
- [Stage 8.3d lead/melody + drone pad options prompt](docs/prompts/PROMPT_stage8_3d_lead_drone.md)
- [Stage 8.3b arp + startup prompt](docs/prompts/PROMPT_stage8_3b_arp_startup.md)
- [Stage 8.3a bass prompt](docs/prompts/PROMPT_stage8_3_bass.md)
- [Stage 8.2 groove + quantized-state prompt](docs/prompts/PROMPT_stage8_2_groove.md)
- [Stage 8.1 rhythm/drum prompt](docs/prompts/PROMPT_stage8_1_rhythm.md)
- [Stage 7 GUI and Stage 7.1 MIDI switches](docs/README_stage7.md)
- [Stage 6 MPE MIDI rig](docs/README_mpe.md)
- [Stage 5.5 live rig](docs/README_live_rig.md)
- [Design document](docs/design_doc_10.28.10.50)
- Older stage handoffs are in [docs/](docs/) and
  [docs/prompts/](docs/prompts/); those prompts predate the `tests/`/`docs/`
  layout and carry a layout note at the top.

## Path notes

- `control/midi_rig.py` resolves its default `config/mpe_config.json` relative
  to the package, not the current working directory.
- `control/drums.py` resolves `config/rhythm_config.json` the same way.
- `control/bass.py` resolves `config/bass_config.json` the same way.
- `control/lead.py` resolves `config/lead_config.json` the same way, and
  `control/drone.py` resolves `config/drone_config.json` (mode/parameters only;
  the drone bus stays `mpe_config.json`'s `drone_port`).
- `control/home.py` resolves `config/home_config.json` the same way.
- `control/ji_bridge.py --mpe-config` / `--rhythm-config` / `--bass-config` /
  `--home-config` / `--lead-config` / `--drone-config` default to those
  package-relative configs; pass an explicit path only if you keep a config
  elsewhere.
- `startup.scd` and `ji_start.scd` stay at the project root: `startup.scd`
  resolves `synthdefs/*.scd` from `thisProcess.nowExecutingPath`, and
  `ji_start.scd` resolves the other three files from its own path.
- `run/ji_station.py` resolves the project root from its own location, so it can
  be started from any working directory.
- `tests/test_*.py` add the project root to `sys.path`, so direct execution
  and `python -m tests...` both work.
- `tests/sc/test_tartini.scd` falls back to the project root by walking three
  parents up; normal use has `~ji_root` set by `startup.scd`.

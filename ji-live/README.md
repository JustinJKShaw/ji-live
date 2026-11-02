# JI Station — Live Performance Rig

A SuperCollider + Python + Ableton live-performance environment built around a
mutating just-intonation pitch-class set, two live autotuned inputs, four loop
voices, two MPE MIDI buses (anchor drone + set arp), and a drum voice. Stage 7
adds a single-window PyQt6 control surface; Stage 7.1 adds independent
Drone/Arp switches; Stage 8.1 adds transport/tempo/meter, free or beat-locked
loops, and the drum engine; Stage 8.2 adds groove generation (presets, density,
syncopation, swing, probability, a per-step grid) and beat-quantized state
changes. Stage 8.3a adds the monophonic bass voice. Stage 8.3d adds the lead/
melody voice (Bus 5) and the drone pad modes (hold/pulse/bend).

**Current status:** the owner reports the live GUI/audio/MIDI rig works well.
Stage 7/7.1, Stage 8.1, and Stage 8.2 are human-tested, accepted, and in live
use (2026-10-31). Stage 8.3a (bass) is **human-confirmed** (2026-10-31). The earlier drone-receive concern is
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

**Stage 8.3b is built and awaits human acceptance.** It adds mutable arp
parameters (step, gate, velocity, three octave offsets, shared mode, playback
order, seed — beat-quantized like the other performance controls) and a
one-sclang + one-Python startup path.

**Stage 8.3c is built and awaits human acceptance.** It can lock the arp to the
beat (`Sync free|beat` + a `1/4`, `1/8`, `1/16`, `1/8T` division, snapped to the
bridge tempo) and let it evolve on its own (`Evolve` every N beats with a depth
control) without any mutation. Both are off by default, so the accepted
free-running arp is unchanged.

**Stage 8.3d is built and awaits human acceptance.** It adds a monophonic
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

**Next:** human acceptance of 8.3d (lead + drone modes), then Stage 9 (SC-side
FX). The prompt is
[docs/prompts/PROMPT_stage8_3d_lead_drone.md](docs/prompts/PROMPT_stage8_3d_lead_drone.md).

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
│   └── ji_station.py          # NEW: the only Python command (bridge + GUI)
├── synthdefs/                 # SuperCollider SynthDefs (do not edit casually)
├── control/                   # mutation engine, bridge, MPE MIDI, rhythm, drums
│   ├── rhythm.py              # tempo/meter/transport, BeatClock, math
│   ├── groove.py              # Stage 8.2 presets, grid, modifiers, seed
│   ├── quantize.py            # Stage 8.2 next-quarter-beat state queue
│   ├── drums.py               # drum config, generated pattern, swing, scheduler
│   ├── arp.py                 # Stage 6/8.3b/8.3c arp: settings, orders,
│   │                          # beat sync, evolution
│   ├── bass.py                # Stage 8.3a monophonic JI bass voice
│   ├── lead.py                # Stage 8.3d lead: motif, variations, scheduler
│   └── drone.py               # Stage 6 hold drone + Stage 8.3d pulse/bend
├── ui/                        # PyQt6 GUI, pure OSC layer, sclang UI surface
├── config/                    # routing, MPE, rhythm, bass, lead, drone JSON
├── tests/
│   ├── test_mutation.py
│   ├── test_mutation_controls.py
│   ├── test_mpe.py
│   ├── test_rhythm.py
│   ├── test_groove.py
│   ├── test_quantize.py
│   ├── test_bass.py
│   ├── test_arp.py            # arp settings/orders/timing/bridge
│   ├── test_lead.py           # Stage 8.3d lead: motif/consistency/scheduler
│   ├── test_drone.py          # Stage 8.3d drone: hold/pulse/bend
│   ├── test_startup.py        # launcher/start-file/docs
│   ├── test_ui.py
│   └── sc/                    # sclang test scripts
│       ├── test_tartini.scd
│       ├── test_autotune.scd
│       ├── test_bridge.scd
│       ├── test_dual_loop.scd
│       ├── test_ableton_routing.scd
│       ├── test_live_rig.scd
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
    ├── README_stage8_3b.md
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
        └── PROMPT_stage8_3d_lead_drone.md
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
   (off by default, needs IAC Bus 5 + an Ableton Lead track); `--dry-run`
   prints the exact child commands; `--check` validates imports, configs, and
   mido output names.

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
uv run --with python-osc python tests/test_groove.py
uv run --with python-osc python tests/test_quantize.py
uv run --with python-osc python tests/test_bass.py
uv run --with python-osc python tests/test_arp.py
uv run --with python-osc python tests/test_lead.py
uv run --with python-osc python tests/test_drone.py
uv run --with python-osc python tests/test_startup.py
uv run --with python-osc python tests/test_ui.py --pure

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
- [Stage 8.3d lead/melody + drone pad modes](docs/README_stage8_3d.md)
- [Stage 8.3c beat-synced arp + self-evolution](docs/README_stage8_3c.md)
- [Stage 8.3b arp parameters + one-entry startup](docs/README_stage8_3b.md)
- [Stage 8.3a monophonic bass](docs/README_stage8_3.md)
- [Stage 8.2 groove + beat-quantized state](docs/README_stage8_2.md)
- [Stage 8.1 rhythm foundation + drum engine](docs/README_stage8.md)
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
- `control/ji_bridge.py --mpe-config` / `--rhythm-config` / `--bass-config` /
  `--lead-config` / `--drone-config` default to those package-relative configs;
  pass an explicit path only if you keep a config elsewhere.
- `startup.scd` and `ji_start.scd` stay at the project root: `startup.scd`
  resolves `synthdefs/*.scd` from `thisProcess.nowExecutingPath`, and
  `ji_start.scd` resolves the other three files from its own path.
- `run/ji_station.py` resolves the project root from its own location, so it can
  be started from any working directory.
- `tests/test_*.py` add the project root to `sys.path`, so direct execution
  and `python -m tests...` both work.
- `tests/sc/test_tartini.scd` falls back to the project root by walking three
  parents up; normal use has `~ji_root` set by `startup.scd`.

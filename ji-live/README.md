# JI Station — Live Performance Rig

A SuperCollider + Python + Ableton live-performance environment built around a
mutating just-intonation pitch-class set, two live autotuned inputs, four loop
voices, two MPE MIDI buses (anchor drone + set arp), and a drum voice. Stage 7
adds a single-window PyQt6 control surface; Stage 7.1 adds independent
Drone/Arp switches; Stage 8.1 adds transport/tempo/meter, free or beat-locked
loops, and the drum engine; Stage 8.2 adds groove generation (presets, density,
syncopation, swing, probability, a per-step grid) and beat-quantized state
changes. Stage 8.3a (next) adds the monophonic bass voice.

**Current status:** the owner reports the live GUI/audio/MIDI rig works well.
Stage 7/7.1, Stage 8.1, and Stage 8.2 are human-tested, accepted, and in live
use (2026-10-31). The earlier drone-receive concern is closed in the current
rig; see [docs/README_stage7.md](docs/README_stage7.md). Stage 8.3a (bass) is
next.

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

**Next:** Stage 8.3a — the monophonic bass voice. The agent prompt is
[docs/prompts/PROMPT_stage8_3_bass.md](docs/prompts/PROMPT_stage8_3_bass.md).

## Layout

```text
ji-live/
├── README.md                  # this file
├── startup.scd                # evaluate first; loads synthdefs/, sets up I/O
├── synthdefs/                 # SuperCollider SynthDefs (do not edit casually)
├── control/                   # mutation engine, bridge, MPE MIDI, rhythm, drums
│   ├── rhythm.py              # tempo/meter/transport, BeatClock, math
│   ├── groove.py              # Stage 8.2 presets, grid, modifiers, seed
│   ├── quantize.py            # Stage 8.2 next-quarter-beat state queue
│   └── drums.py               # drum config, generated pattern, swing, scheduler
├── ui/                        # PyQt6 GUI, pure OSC layer, sclang UI surface
├── config/                    # routing, MPE, and rhythm JSON configs
├── tests/
│   ├── test_mutation.py
│   ├── test_mutation_controls.py
│   ├── test_mpe.py
│   ├── test_rhythm.py
│   ├── test_groove.py
│   ├── test_quantize.py
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
    ├── design_doc_10.28.10.50
    ├── README_stage0.md … README_stage8.md
    ├── README_stage8_2.md
    ├── README_live_rig.md
    ├── README_mpe.md
    └── prompts/
        ├── PROMPT_stage5.md
        ├── PROMPT_stage7_gui.md
        ├── PROMPT_stage8_1_rhythm.md
        ├── PROMPT_stage8_2_groove.md
        └── PROMPT_stage8_3_bass.md
```

Run all commands from `ji-live/`.

## Quick start

1. Evaluate `startup.scd` in sclang.
2. Evaluate `tests/sc/test_live_rig.scd`.
3. Start the bridge (add `--midi` for the Stage 6 rig and `--drums` for
   Stage 8.2 drums on `IAC Driver Bus 3`):

   ```bash
   uv run --with python-osc --with mido --with python-rtmidi \
     python control/ji_bridge.py --interactive --midi --drums --glide-time 0.15
   ```

   Without MIDI/drums, the original invocation still works:

   ```bash
   uv run --with python-osc python control/ji_bridge.py --interactive --glide-time 0.15
   ```

4. Evaluate `ui/control_surface.scd` (after `tests/sc/test_live_rig.scd`).
5. Launch the GUI:

   ```bash
   uv run --with python-osc --with PyQt6 python ui/ji_ui.py
   ```

If `tests/sc/test_live_rig.scd` is re-evaluated, re-evaluate
`ui/control_surface.scd`.

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

- [Stage 8.2 groove + beat-quantized state](docs/README_stage8_2.md)
- [Stage 8.1 rhythm foundation + drum engine](docs/README_stage8.md)
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
- `control/ji_bridge.py --mpe-config` / `--rhythm-config` default to those
  package-relative configs; pass an explicit path only if you keep a config
  elsewhere.
- `startup.scd` stays at the project root so its `synthdefs/*.scd` glob remains
  correct.
- `tests/test_*.py` add the project root to `sys.path`, so direct execution
  and `python -m tests...` both work.
- `tests/sc/test_tartini.scd` falls back to the project root by walking three
  parents up; normal use has `~ji_root` set by `startup.scd`.

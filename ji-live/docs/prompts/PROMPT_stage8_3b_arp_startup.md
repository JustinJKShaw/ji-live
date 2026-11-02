# Stage 8.3b Agent Prompt — Arp Parameters + One-Entry Startup

You are implementing **Stage 8.3b** of the JI live-performance system. Stages
0–7.1, 8.1, 8.2, and 8.3a are implemented. The owner has verified 8.3a (the
monophonic bass) and is using the rig. Stage 8.3b has two deliverables:

1. **A clean startup path.** Today the human evaluates three sclang files
   (`startup.scd`, `tests/sc/test_live_rig.scd`, `ui/control_surface.scd`) and
   starts two Python processes (bridge + GUI). The owner wants this reduced to
   **one sclang entry point and one Python entry point**, plus a short,
   authoritative file that lists every start/stop command and explains every
   flag. That document must be updated after every future stage.

2. **Mutable arp parameters.** The Stage 6 set arp has fixed timing
   (0.25 s step / 0.20 s gate) and fixed octave/shared-mode/seed settings that
   are only editable by hand in `config/mpe_config.json`. The owner wants the
   useful arp parameters exposed through the bridge, the GUI, and OSC, with the
   same beat-quantized behavior as the other performance controls.

Do not claim human audio/MIDI acceptance. The owner is the authority.

---

## 1. Read these files first

Read the code and docs, not just the summaries:

```text
docs/design_doc_10.28.10.50        §6.11, §6.12, §6.13, §6.14, §6.15, §6.16,
                                   §7.1, §8.9, §9, §13, §14
docs/README_stage8_3.md            accepted 8.3a bass behavior and startup
docs/README_stage8_2.md            groove + quantize model
docs/README_stage8.md              rhythm/drum foundation
docs/README_mpe.md                 Stage 6 drone/arp, IAC/Ableton setup
docs/prompts/PROMPT_stage8_3_bass.md
                                   previous stage contract/tone
docs/prompts/PROMPT_stage8_2_groove.md
                                   quantizer contract/tone
control/arp.py                     SetArp, build_groups, ArpPattern, order,
                                   fixed 0.25/0.20 timing, per-step logs
control/mpe_output.py              MPE pitch math/lazy mido/fake seam
control/midi_rig.py                MPEConfig, MIDIRig, drone/arp switches
control/rhythm.py                  BPM/meter/transport math
control/quantize.py                BeatQuantizer phases
control/bass.py                    Stage 8.3a settings/quantize pattern to copy
control/ji_bridge.py               bridge ownership, status, quantized requests
control/osc_server.py              OSC handlers/callbacks
control/mutation.py                engine API only; do not modify
ui/osc_control.py                  pure builders/parsers/store
ui/ji_ui.py                        compact PyQt6 window
config/mpe_config.json             drone/arp ports, timing, octaves, seed
config/rhythm_config.json          drum/groove config
startup.scd                        server boot, buses, SynthDefs
tests/sc/test_live_rig.scd         live autotune/loop rig
ui/control_surface.scd             tempo/loop/autotune/status OSC surface
tests/test_mpe.py                  fake MPE output tests
tests/test_bass.py                 most recent pure-voice tests
tests/test_quantize.py             queue tests
tests/test_ui.py                   pure + headless Qt tests
README.md                          current run commands/status
docs/START_HERE_stage8_1.txt       historical start notes
docs/START_HERE_stage8_2.txt       historical start notes
docs/START_HERE_stage8_3.txt       historical start notes
```

Run Python tests from `ji-live/`.

---

## 2. Current state you must preserve

- Stage 5.5 audio: MiniFuse 2 input / BlackHole 16ch output, 2 in / 16 out,
  48 kHz, blockSize 64. SC 0/1 = live autotuned inputs, 2/3 = loops.
- Stage 6: drone on IAC Bus 1, arp on IAC Bus 2, member channel 2,
  48-semitone MPE bend range. The arp is monophonic and alternates two
  three-note groups that share exactly one ratio.
- Stage 7/7.1: compact one-window GUI, Status, independent Drone/Arp
  switches in the top-row MIDI group.
- Stage 8.1/8.2: Python bridge owns BPM/meter/transport; `RhythmEngine`/
  `BeatClock`; drums on optional Bus 3; groove presets/grid/modifiers;
  `control/quantize.py` next-quarter-beat queue; `/ji/quantize/state`.
- Stage 8.3a: monophonic bass on optional Bus 4, `PHASE_BASS`, `/ji/bass/*`,
  compact Bass group. Accepted by the owner; do not regress it.
- Do not change `/ji/freqset/update`, `synthdefs/*`, `startup.scd` audio
  behavior, `tests/sc/test_live_rig.scd` audio DSP, `test_dual_loop.scd`,
  `test_ableton_routing.scd`, `control/mutation.py`, Stage 5.5 routing/loop
  semantics, or the Stage 6 MPE bus/channel/bend contracts.
- Existing tests must keep passing:
  `tests/test_mutation.py`, `tests/test_mutation_controls.py`,
  `tests/test_rhythm.py`, `tests/test_groove.py`, `tests/test_quantize.py`,
  `tests/test_bass.py`, `tests/test_mpe.py --pure`,
  `tests/test_ui.py --pure`, and the optional Qt test.
- Compatibility rule: while the transport is stopped, every quantized action
  applies immediately. Direct engine setters and `MutationOSCServer.trigger()`
  remain immediate; quantization belongs to the bridge/UI request layer.
- Keep the compact 8 pt / 32 px / scroll-area GUI. The Bass, Groove, Rhythm,
  Status, and top MIDI groups must still fit or scroll without clipping.

---

## 3. Owner's brief

> a) i want the whole start up process cleaned up. running three sc files and
> then two python files seems like it could be more efficient. at the very
> least i always want a short file that lists everything necessary to start up
> and a little documentation on what the flags of a command do so after every
> feature implementation know exactly what i need to do.
>
> b) i want to have parameters for the arp as well that i can modify.

Interpretation for this stage:

- The **only** sclang file the human should need to evaluate is one master file
  (suggested `ji_start.scd`). It may internally load the existing three files in
  order; do not rewrite the audio DSP.
- The **only** Python command the human should need is one launcher
  (suggested `run/ji_station.py`). It may spawn the existing bridge and GUI as
  child processes; do not merge the GUI event loop into the bridge process.
- The authoritative startup document (suggested `docs/START_HERE.md`) must list
  every step, every command, every flag, the stop procedure, and common
  troubleshooting. It must state that every future stage updates it.
- The arp keeps its current sound and timing by default. Expose the existing
  Stage 6 settings plus a musical order control; do **not** tempo-sync the arp
  in this stage.

---

## 4. Part A — One-entry startup

### 4.1 `ji_start.scd`

Create `ji-live/ji_start.scd` at the project root. Evaluating this one file from
a fresh sclang must:

1. Capture the project root from `thisProcess.nowExecutingPath`.
2. Evaluate `startup.scd` (or `load` it) so ServerOptions, buses, and SynthDefs
   are set up exactly as today.
3. Boot the server if it is not already running (the current `startup.scd`
   assumes something else booted it; the master file should not depend on the
   human clicking Boot, unless that is impossible on the owner's SC version —
   document the chosen behavior).
4. Wait for boot completion and for SynthDefs to be available (`s.sync` or an
   equivalent), then evaluate `tests/sc/test_live_rig.scd`.
5. Wait/sync again, then evaluate `ui/control_surface.scd`.
6. Print one clear `[JI] ...` line saying the sclang side is ready, with the
   exact Python launcher command (and a reminder that the bridge must be
   started after the sclang rig).

Requirements:

- Re-evaluating `ji_start.scd` must be safe: it must not stack duplicate
  routines, OSCdefs, tempo clocks, or Synth instances. Reuse the cleanup that
  `test_live_rig.scd` and `control_surface.scd` already perform.
- Do not duplicate the audio code into the master file. Loading the existing
  files is preferred so Stage 5.5/8.1 behavior and tests stay authoritative.
- If boot fails or a prerequisite is missing, print a specific error with the
  missing device/file/global; do not silently continue.
- Document in `docs/START_HERE.md` whether the human should evaluate
  `ji_start.scd` only, and which old files are now archival.

The old files remain valid and independently runnable for debugging and for
`tests/sc/*` human checks. Do not delete or repurpose them.

### 4.2 `run/ji_station.py` (or equivalent single launcher)

Create a single Python entry point that starts the bridge and GUI with one
command. Suggested invocation:

```bash
cd ji-live
uv run --with python-osc --with mido --with python-rtmidi --with PyQt6 \
  python run/ji_station.py --midi --drums --bass
```

Required behavior:

- Spawn the existing `control/ji_bridge.py` and `ui/ji_ui.py` as child
  processes using `sys.executable`, so the same `uv --with ...` environment is
  inherited.
- Defaults should match the owner's current rig: `--midi`, `--drums`, and
  `--bass` on. Provide `--no-midi`, `--no-drums`, `--no-bass`, `--no-gui`,
  `--no-bridge`, `--glide-time`, `--listen-port`, `--ui-port`,
  `--bridge-args "..."`, and `--dry-run`. `--check` should validate imports,
  config parsing, and mido output names, then exit without starting anything.
- Print the exact child command lines with a `[LAUNCH]` prefix so a failure is
  reproducible from the terminal.
- Start the bridge first; start the GUI only after the bridge process is alive
  (a short wait or readiness poll is fine). If the GUI is closed, keep the
  bridge running, matching the current workflow. On Ctrl+C or SIGTERM, stop the
  children cleanly (SIGINT/SIGTERM, wait, then kill if needed) and print a
  `[LAUNCH] stopped` line.
- If the bridge exits early, print its exit code and stop the GUI; do not leave
  a dead GUI process.
- Do not start sclang. Print a reminder that `ji_start.scd` must be evaluated
  first.
- `--dry-run` prints exactly what would be started and exits 0. It must not
  import PyQt6/mido itself.
- Keep the existing direct commands working unchanged:
  `python control/ji_bridge.py ...` and `python ui/ji_ui.py ...`.

### 4.3 `docs/START_HERE.md` — the authoritative start/stop sheet

Create one short document (suggested at the repo root or `docs/`; link it from
`README.md`). It must contain:

- **One-time setup:** MiniFuse 2 / BlackHole 16ch rates, IAC Buses 1–4, Ableton
  tracks (Drone, Arp, Drums, Bass, Sub), MPE 48 st vs bass 2 st, and the exact
  channel numbers.
- **Every run, in order:** one sclang action (`ji_start.scd`) and one Python
  command (`run/ji_station.py ...`), plus what must be running in Ableton.
- **Flag reference:** every launcher/bridge/GUI flag, what it does, its default,
  and when to use it. Include `--bass-config`, `--mpe-config`,
  `--rhythm-config`, `--glide-time`, `--listen-port`, `--ui-port`, `--midi`,
  `--drums`, `--bass`, `--interactive`.
- **Stop:** GUI close, Ctrl+C on the launcher, and `~ji_stop_all.();`.
- **Troubleshooting:** missing IAC bus (copy the available names), missing
  mido/rtmidi, stale sclang buffer (re-evaluate `ji_start.scd`), bridge port
  already in use, GUI waiting/disabled states.
- **Maintenance rule:** after every feature/stage, update this file and the
  command `--help` text in the same change. It is the source of truth; old
  `START_HERE_stage*.txt` files are archival only.

The document should be skimmable in under a minute and should not duplicate the
entire stage design docs.

---

## 5. Part B — Arp parameters

### 5.1 Defaults must reproduce the Stage 6 arp exactly

The shipped default remains:

```text
step_seconds      0.25
gate_seconds      0.20
velocity          90
octave_offsets    [0, 1, 2]
shared_mode       anchor
order             forward
seed              1
```

With these defaults, the arp pattern, sequence, timing, and MIDI output must be
byte-for-byte equivalent to today's behavior. `config/mpe_config.json` remains
the source of defaults; add an additive `"arp_order": "forward"` key.

### 5.2 Required parameters

| Control | Range | Notes |
|---|---|---|
| Step seconds | 0.05–2.0 | Time between arp steps; free-run, not tempo-synced |
| Gate seconds | 0.01–2.0, clamped/rejected to ≤ step | Note length; changing it does not cut a currently held note |
| Velocity | 1–127 | Note-on velocity |
| Octave offsets | three integers −8..+8 | Positional `(shared, non_shared_1, non_shared_2)`; shared offset identical in A and B |
| Shared mode | `anchor`, `random` | Keep the existing shared-note semantics |
| Order | `forward`, `reverse`, `pingpong`, `random` | New; see exact definitions below |
| Seed | 0..2^31−1 | Reproducible shared pick/random order |

Explicitly **out of scope for 8.3b**: tempo-syncing the arp to BPM, swing,
rests/probability, changing the 3+3 group size, polyphony/MPE voice allocation,
and changing the Stage 6 bend range. Those can be a later stage.

### 5.3 Order definitions (document and test exactly)

Let the stored six playback steps be `[A1, A2, A3, B1, B2, B3]` (indices
0–5). Define:

- `forward`: `0,1,2,3,4,5` (the current behavior).
- `reverse`: `5,4,3,2,1,0`.
- `pingpong`: `0,1,2,3,4,5,4,3,2,1` (ten steps; endpoints are not repeated
  back-to-back; then repeat).
- `random`: a seeded permutation of `0..5`, generated when the pattern or
  order/seed changes; same seed + same pattern → same order.

Order changes regenerate the order at the next applied step. Document whether
the step cursor resets; prefer resetting to the first element of the new order
when the order/pattern changes, while never cutting the currently held note.
Step/gate/velocity changes apply from the next step; octave/shared/seed/order
changes rebuild the pattern for the next step.

### 5.4 Thread-safety / application semantics

- `SetArp` currently exposes mutable public attributes without a lock and only
  rebuilds on `on_set`. Add a small validated settings object or setters that:
  - update settings under the existing lock;
  - store the last `(ratios, anchor, fundamental)` snapshot;
  - regenerate the pattern/order from that snapshot so parameter changes do
    **not** require waiting for a mutation;
  - never cut or retrigger the currently held note merely because settings
    changed; the next trigger uses the new settings;
  - keep the drift-corrected step deadline behavior and stop responsiveness;
  - never leave a stuck note after a stop, gate change, or pattern change.
- A gate longer than the step is not allowed: clamp explicitly and log.
- A step change must not burst through missed steps; if the thread falls behind,
  resume from `now`.
- A shared/seed/order change while the arp is disabled is stored and used on the
  next start.

### 5.5 Bridge / OSC / quantization

Add `/ji/arp/*` (UI → bridge) and `/ji/arp/state` (bridge → UI). Suggested map:

```text
/ji/arp/set_step          float seconds 0.05..2.0
/ji/arp/set_gate          float seconds 0.01..2.0 (≤ step)
/ji/arp/set_velocity      int 1..127
/ji/arp/set_octaves       three ints -8..8 (shared, non_shared_1, non_shared_2)
/ji/arp/set_shared_mode   string anchor|random
/ji/arp/set_order         string forward|reverse|pingpong|random
/ji/arp/set_seed          int 0..2^31-1
/ji/arp/state             int enabled, int port_ok, float step_seconds,
                          float gate_seconds, int velocity,
                          int oct_shared, int oct_non_shared_1,
                          int oct_non_shared_2, string shared_mode,
                          string order, int seed
```

- Do **not** add a second arp enable path. The existing top-row Drone/Arp
  switches and `/ji/midi/set_enabled` remain the enable/disable controls.
- Add `PHASE_ARP` to `control/quantize.py`: order `mutation, octave, groove,
  drums, bass, arp, midi`. Arp parameter changes are queued to the next
  quarter-note beat while the transport runs, and immediate while stopped.
  Last-wins per key; different keys all run.
- Tempo/meter/transport remain immediate. Do not double-queue beat-mode loop
  Record/Play.
- `MIDIRig` needs arp state/setters (e.g. `arp_state()`,
  `set_arp_setting(name, value)` or explicit setters). Keep `midi=False`
  behavior unchanged: no arp, no port, no crash.
- `JIBridge` sends `/ji/arp/state` at start, on `/ji/ui/hello`, after every
  applied arp change, and after transport changes if it affects the UI. Do not
  flood.
- Log applied arp config once per change (not per step). Keep or reduce the
  current per-step `[ARP] step=...` log; do not spam the bridge log with it.
- If `--midi` is off or Bus 2 is unavailable, the handlers must log clearly and
  leave the rest of the rig running.

### 5.6 GUI

Add a compact **Arp** group (suggested below Bass) without tabs or a redesign:

- Step knob (0.05–2.0 s), Gate knob (0.01–2.0 s), Velocity knob (1–127,
  integer), Order combo, Shared-mode combo, Seed spin.
- Three octave offset spins (−8..+8), labelled shared / 1 / 2.
- Status line: `Arp: on | 0.25 s / 0.20 s | forward | anchor | seed 1`.
- The existing top-row Drone/Arp switches remain; do not duplicate them.
- Knobs coalesce at ≈30 Hz; buttons/spins send immediately; incoming
  `/ji/arp/state` syncs with signals blocked (no feedback OSC).
- If MIDI/arp is absent, show a waiting/disabled state and disable the controls.
- The window must stay compact (8 pt, 32 px knobs, scroll area); do not push
  Status or the existing groups off-screen.
- The pending line must reflect queued arp changes while running.

---

## 6. Tests

### 6.1 New pure tests

Run from `ji-live/`:

```bash
uv run --with python-osc python tests/test_arp.py
uv run --with python-osc python tests/test_startup.py
```

`tests/test_arp.py` must cover at least:

- config validation: step/gate (gate ≤ step), velocity, three octaves −8..8,
  shared mode, order, seed; invalid values rejected or clamped safely.
- default values reproduce the current arp sequence/timing.
- forward/reverse/pingpong/random exact sequences; random deterministic by
  seed; order change applies at the next step without a stuck note.
- shared mode anchor/random; shared note is identical in A and B.
- step/gate/velocity/octave changes apply from the next step; no restart,
  no burst, no stale note-off, no stuck note; long gate and short gate.
- pattern regeneration after a parameter change without a new mutation.
- `MIDIRig` arp state/setters with a fake output; `midi=False` has no arp and
  does not require Bus 2; missing Bus 2 behavior is clear.
- bridge quantization: stopped → immediate; running → pending then applied at
  the next beat; coalescing; transport-stop flush.
- `/ji/arp/state` shape and `/ji/osc` handlers validate safely.
- no mido import during pure construction.

`tests/test_startup.py` must cover at least:

- the launcher's argv builders for default/`--no-*`/`--dry-run`/`--check`
  (no subprocesses, no real ports);
- `ji_start.scd` references `startup.scd`, `tests/sc/test_live_rig.scd`, and
  `ui/control_surface.scd` in the correct order;
- `docs/START_HERE.md` exists and contains the launcher command and the flag
  names (a simple text check is enough);
- `README.md` links the canonical startup file.

### 6.2 Existing tests

```bash
uv run --with python-osc python tests/test_mutation.py
uv run --with python-osc python tests/test_mutation_controls.py
uv run --with python-osc python tests/test_rhythm.py
uv run --with python-osc python tests/test_groove.py
uv run --with python-osc python tests/test_quantize.py
uv run --with python-osc python tests/test_bass.py
python tests/test_mpe.py --pure
uv run --with python-osc python tests/test_ui.py --pure
```

All must pass. In particular:

- `MIDIRig(midi=False, drums=False, bass=True)` still works;
- `midi=False` still never opens Bus 1/2;
- `JIBridge(...)` still constructs without mido;
- `SetArp` defaults still sound/behave like Stage 6.

### 6.3 Optional headless Qt test

```bash
QT_QPA_PLATFORM=offscreen uv run --with python-osc --with PyQt6 \
  python tests/test_ui.py --qt
```

Add checks for the Arp group/widgets/defaults, signal-blocked state sync, no
feedback OSC, disabled state without MIDI/Bus 2, queued/immediate messages, and
the window staying compact. Skip cleanly if PyQt6/Qt is unavailable.

### 6.4 Human checks

Audio, MIDI, Ableton, IAC, sclang timing, and the live GUI are human-only. The
next agent must provide a short checklist in `docs/README_stage8_3b.md` and a
single-entry startup checklist in `docs/START_HERE.md`.

---

## 7. Human acceptance checklist

1. On a fresh sclang, evaluating `ji_start.scd` alone boots the server, loads
   SynthDefs, builds the live rig, installs the control surface, and prints one
   ready line; no other sclang file is needed.
2. From one terminal, `run/ji_station.py` starts the bridge and GUI; closing
   the GUI keeps the bridge running; Ctrl+C stops both cleanly.
3. `--dry-run`, `--check`, `--no-midi`, `--no-drums`, `--no-bass`, `--no-gui`,
   and `--no-bridge` behave as documented.
4. `docs/START_HERE.md` is short, complete, and its commands work verbatim.
5. The arp still sounds exactly as before with defaults (0.25/0.20, forward,
   anchor, [0,1,2], seed 1).
6. Step/gate/velocity/octave/shared/order/seed changes are audible from the GUI;
   while the transport runs they apply at the next quarter beat, while stopped
   immediately.
7. `forward`, `reverse`, `pingpong`, and `random` produce the documented
   sequences; random is reproducible by seed.
8. Gate changes, stop, disable, and pattern changes never leave a stuck note or
   cut the next note with a stale note-off.
9. Drone/Arp switches, drums, bass, groove, quantized state, mutation, autotune,
   loops, and the existing GUI do not regress; the window stays compact.
10. Record the result. **8.3b is not complete until the human confirms the
    startup path and arp controls and no audio/MIDI regressions.**

---

## 8. Diagnostics

Use the existing prefixes and add useful lines without per-step spam:

```text
[LAUNCH] bridge: .../python control/ji_bridge.py --interactive --midi --drums --bass ...
[LAUNCH] gui: .../python ui/ji_ui.py
[LAUNCH] stopped
[JI] sclang rig + control surface ready; next: ... run/ji_station.py ...
[ARP] preset change applied: step=0.250 gate=0.200 order=forward ...
[ARP] state step=0.250 gate=0.200 velocity=90 octaves=0,1,2 shared=anchor order=forward seed=1
[QUANT] queued arp order (1 pending) due beat 42 (in 0.250 s)
```

Do not log every scheduled arp note during performance. Log pattern changes,
the first scheduled step after a change, and disable/stop events. If the
existing `[ARP] step=...` per-step line is kept, make it opt-in or suppress it
from the normal UI log; document the choice.

---

## 9. Risks / decisions to document

- **One entry point vs. old files:** `ji_start.scd` loads the existing files; it
  must not fork the audio code. Old files remain valid for debugging.
- **Boot ownership:** document whether `ji_start.scd` calls `s.boot` or assumes
  the SC IDE has booted. The goal is that a fresh interpreter + one evaluation
  works.
- **Launcher process model:** bridge and GUI stay separate processes; the
  launcher supervises them. Closing the GUI must not kill the bridge.
- **Free-run arp:** step/gate are seconds, not tempo divisions. Tempo sync is
  deferred; do not silently add it.
- **Order/pattern phase:** document the exact sequence and whether a change
  resets the step cursor.
- **Gate changes:** the currently held note keeps its existing gate; the new
  gate applies from the next trigger.
- **Quantization latency:** arp changes wait up to one quarter beat while
  running (3 s at 20 BPM); stopping makes them immediate.
- **GUI density:** the Arp group must not push Status/Bass/Groove off-screen.
- **Docs drift:** `docs/START_HERE.md` and `--help` must be updated for every
  new flag; this is part of the acceptance, not an afterthought.

---

## 10. Deliverables

```text
ji-live/
├── ji_start.scd                    NEW: one sclang entry point
├── run/
│   ├── ji_station.py               NEW: one Python launcher (bridge + GUI)
│   └── (optional check/helper)     NEW if useful
├── control/
│   ├── arp.py                      MODIFIED additively: settings, setters,
│   │                               order, thread-safe regeneration
│   ├── midi_rig.py                 MODIFIED additively: arp state/setters
│   ├── ji_bridge.py                MODIFIED additively: /ji/arp/*, state,
│   │                               PHASE_ARP requests
│   ├── osc_server.py               MODIFIED additively: /ji/arp/* handlers
│   └── quantize.py                 MODIFIED additively: PHASE_ARP
├── config/
│   └── mpe_config.json             MODIFIED additively: arp_order
├── ui/
│   ├── osc_control.py              MODIFIED additively: builders/parsers/state
│   └── ji_ui.py                    MODIFIED additively: compact Arp group
├── tests/
│   ├── test_arp.py                 NEW: arp settings/order/timing/bridge tests
│   ├── test_startup.py             NEW: launcher/start-file/docs checks
│   └── test_ui.py                  MODIFIED additively: Arp UI/Qt coverage
├── docs/
│   ├── START_HERE.md               NEW: authoritative start/stop + flag guide
│   ├── README_stage8_3b.md         NEW: arp/startup stage documentation
│   ├── README_stage8_3.md          MODIFIED: link 8.3b
│   ├── README_mpe.md               MODIFIED: arp controls/timing note
│   └── design_doc_10.28.10.50      UPDATED: §6.13, §6.17/new startup section,
│                                   §7.1, §8.9, §9, §14
└── README.md                       UPDATED: two-step start, links, status
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
- Explicit statement that audio, MIDI, Ableton, IAC, sclang timing, and the live
  GUI were not tested by the agent; 8.3b awaits human acceptance.

Also include the exact one-sclang + one-Python start sequence in the final
response so the human can copy it without opening another file.

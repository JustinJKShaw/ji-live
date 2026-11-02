# START HERE — JI Station start / stop sheet

**This file is the source of truth for starting and stopping the rig.**
Every future stage updates it *and* the `--help` text in the same change.
The old `START_HERE_stage*.txt` files are archival only.

Every GUI control (range, default, what it does) is in
[docs/GUI_PARAMETERS.md](GUI_PARAMETERS.md).

Two actions start everything:

| # | Where | Action |
|---|---|---|
| 1 | sclang | evaluate **`ji-live/ji_start.scd`** (one file, Cmd+Return) |
| 2 | terminal | run **`python run/ji_station.py`** (one command) |

Nothing else. `ji_start.scd` loads `startup.scd`, `tests/sc/test_live_rig.scd`
and `ui/control_surface.scd` in that order; `run/ji_station.py` starts the
bridge and the GUI and supervises them.

---

## 1. One-time setup (once per machine / per Ableton set)

### Audio devices (Audio MIDI Setup, macOS)

| Device | Role | Channels | Rate |
|---|---|---|---|
| MiniFuse 2 | input | 2 in | 48000 Hz |
| BlackHole 16ch | output | 16 out | 48000 Hz |

`startup.scd` sets blockSize 64, 2 in / 16 out, and deliberately does **not**
force the sample rate on the BlackHole path. Set both devices to 48000 Hz by
hand; a post-boot check warns if the server did not reach it.

### MIDI buses (IAC Driver, Audio MIDI Setup → MIDI Studio → IAC Driver)

Tick **Device is online**, then create four buses with these exact names
(five if you use the 8.3d lead):

| Bus | Used by | Channel | Bend range / MPE |
|---|---|---|---|
| `IAC Driver Bus 1` | Drone (`hold`/`pulse`/`bend`) | member 2 | MPE, **48 semitones** |
| `IAC Driver Bus 2` | Set arp | member 2 | MPE, **48 semitones** |
| `IAC Driver Bus 3` | Drums | 10 (drum map) | plain MIDI |
| `IAC Driver Bus 4` | Bass + Sub | main 2, sub 3 | plain MIDI, **2 semitones** |
| `IAC Driver Bus 5` | Lead (optional, `--lead`) | member 2 | MPE, **48 semitones** |

`config/mpe_config.json` (`member_channel: 2`, `master_channel: 1`,
`bend_range_semitones: 48`) must match Ableton's MPE zone settings.

### Ableton Live tracks

| Track | Input | Notes |
|---|---|---|
| **Drone** | IAC Bus 1 | MPE enabled, pitch-bend range **48 st**, Audio To BlackHole 16ch (unused for monitoring) |
| **Arp** | IAC Bus 2 | MPE enabled, pitch-bend range **48 st** |
| **Drums** | IAC Bus 3 | plain MIDI, drum rack |
| **Bass** | IAC Bus 4, channel 2 | plain MIDI, pitch-bend range **2 st** |
| **Sub** | IAC Bus 4, channel 3 | same bus, one octave lower (optional) |
| **Lead** | IAC Bus 5 | MPE enabled, pitch-bend range **48 st**; FX insert point (optional, needs `--lead`) |

Audio: four mono tracks from BlackHole 16ch inputs 1–4 (SC 0 = autotuned input
0, SC 1 = autotuned input 1, SC 2 = loop 1, SC 3 = loop 2), Monitor In, master
to MiniFuse 2. See `docs/README_live_rig.md`.

---

## 2. Every run, in order

### Step 1 — sclang (the only sclang file)

```text
evaluate  ji-live/ji_start.scd
```

Success looks like one line (the launcher command is printed for copy/paste):

```text
[JI] sclang rig + control surface ready; langPort=57120 sr=48000 Hz out=16 in=2
[JI] next: cd /path/to/ji-live && uv run --with python-osc ... python run/ji_station.py --midi --drums --bass
```

It boots scsynth itself (the IDE Boot button is not needed), waits for the
SynthDefs, installs the live rig and the control surface. If a device, bus, or
SynthDef is missing it prints a specific error and installs nothing.

### Step 2 — terminal (the only Python command)

```bash
cd ji-live
uv run --with python-osc --with mido --with python-rtmidi --with PyQt6 \
  python run/ji_station.py --midi --drums --bass
```

`--midi --drums --bass` are the defaults, so the short form also works:

```bash
uv run --with python-osc --with mido --with python-rtmidi --with PyQt6 \
  python run/ji_station.py
```

Add `--lead` when Bus 5 and the Ableton **Lead** track exist (it ships **off**):

```bash
uv run --with python-osc --with mido --with python-rtmidi --with PyQt6 \
  python run/ji_station.py --lead
```

The launcher prints the exact child command lines with a `[LAUNCH]` prefix,
starts the bridge first, then the GUI. Missing IAC buses degrade one voice only
(see §5).

### Step 3 — Ableton

Ableton must be running with the five tracks armed/monitoring as in §1, and
`IAC Driver` online. The bridge does not start Ableton.

### Arp sync / evolve (runtime, no command)

The arp starts **free-running** as in 8.3b. Two controls in the **Arp** group
change that while the rig runs:

| Control | What it does |
|---|---|
| `Sync` = `beat` + `Div` | the step becomes that division of the bridge BPM (`1/4`, `1/8`, `1/16`, `1/8T`) and the next step snaps to the beat. `1/8` at 120 BPM = 0.25 s (the free default). `Sync` = `free` restores the Step knob (seconds) |
| `Evolve` + `Every N b` + `Depth` | the arp regenerates its own pattern from the current set every N quarter beats, with no mutation. Depth 0 re-picks only the free notes; higher depth also drifts the shared note and the octaves |

Both are off by default. The config equivalents are `arp_sync`, `arp_division`,
`arp_evolve`, `arp_evolve_beats`, `arp_evolve_depth` in
`config/mpe_config.json`. Details:
[docs/README_stage8_3c.md](README_stage8_3c.md).

### Lead + drone modes (Stage 8.3d, runtime, no command)

The **Lead** group and the **Drone modes** group add these controls while the
rig runs:

| Control | What it does |
|---|---|
| **Lead** `Lead on` | starts/stops the melody on Bus 5; the port stays open |
| **Lead** `Grid`, `Phrase`, `Density`, `Gate`, `Velocity`, `Oct`/`Base` | phrase resolution/length and articulation; beat-quantized while running |
| **Lead** `Variation` + `Vary` | step through the eight seeded variations (identity, transpose, invert, retrograde, thin, ornament, rhythm, random) |
| **Lead** `Var every` | 0 = manual only; N = the next variation every N bars, on the bar grid |
| **Lead** `Groove link` | on = follow the drum groove's swing; off = the lead's own `Swing` |
| **Lead** `Seed` | the same seed reproduces the same phrase and variation sequence |
| **Drone modes** `Mode` | `hold` (Stage 6, default), `pulse` (repeats the anchor **pitch class** at seeded octave offsets), `sequence` (a repeating figure that climbs the current set from the anchor and comes back), `bend` (hold, then a slide) |
| **Drone modes** pulse row | `Notes`, `Grid`, `Gate`, `Span` |
| **Drone modes** sequence row | `Seq notes` (2–8), `Length` (1/4, 1/2, 1, 2 or 4 **bars** per held note), `Gate` (% of the note); each step is the next class up/down in the current set, so the figure moves harmonically over bars and still follows mutations |
| **Drone modes** bend row | `Dir`, `Semis`, `Slide`, `Hold`; the cycle (hold + slide, rounded up to the next beat) is derived and shown in the status line |

Mutating the frequency set does **not** rewrite the melody: every note keeps its
class when that class survives, and only notes whose class disappeared move (to
their fallback degree). `hold` sounds exactly as before. The top-row `Drone`
switch is still the only drone mute. Details:
[docs/README_stage8_3d.md](README_stage8_3d.md).

### Home sets + smooth return (Stage 8.4, runtime, no command)

The **Home** group captures a frequency set you can return to. It needs no flag
and no new bus; it reuses `/ji/freqset/update`.

| Control | What it does |
|---|---|
| `Slot` + `Set home` | capture the current set into `A`–`D` and make it the active home |
| `Go` | smooth return to the selected slot: one batch per quarter beat |
| `Clear` | forget the selected slot |
| `Return` | return to the active home in `ceil(distance / max_changes)` beats |
| `Back` | restore the immediately previous set; press again to walk further back |
| `Jump` | instant set swap (not a smooth return) |
| `Auto` + `Every` | after N mutations, return automatically on the next beat |
| `Drift` | maximum distance from home during a journey (`0` = unlimited) |
| `Seed` | journey seed; same home + seed + mutation count reproduces the journey |

The status line reads `Home: A · 7 cl | dist 2 | auto 8 | drift 3 | back 12`
(or `Home: returning to A · 2/3 beats`, or `Home: none (Set home to capture)`).

**Procedure.** Set `Slot` = `A`, press `Set home`, then mutate. `dist` grows;
press `Return` while the transport runs and the set walks home one batch per
quarter beat. With the transport stopped the whole return applies at once (the
Stage 7/8.1 compatibility rule). While a return is running, a manual `Mutate` /
`Octave` / `Back` / `Go` or a transport stop cancels it (one `[HOME]` line).
**Back** restores the previous set and keeps walking back. `Auto` is off by
default; the shipped `config/home_config.json` is `every: 8`, `seed: 1`,
`drift: 0`, `return_glide: 0.06`, `history_depth: 64`. Details:
[docs/README_stage8_4.md](README_stage8_4.md).

### Stepwise class glide (Stage 8.4.1, runtime, no command)

The Mutation group's **Glide** knob is the **replacement melt** time, not a
global autotune retune. When a `Mutate` produces a stepwise/replaced event (one
class disappears and another appears), the class itself travels from its old
ratio to the new ratio over the knob time (30 Hz, cents-linear) and **every**
voice follows: live autotune, loops, drone, bass, lead and the arp. The arp keeps
its pattern shape and plays the nearest current class until the final rebuild;
the UI updates once at the end.

* `Glide = 0` is effectively instant.
* A **pure add/remove** has no old→new pair, so it snaps immediately with a
  0.005 s update glide; the knob does not apply. This is a deliberate change from
  the older "every mutation retunes over Glide" behavior. The Live Autotune
  `Retune` knob is still the persistent retune setting for the rest of the rig.
* A new `Mutate`, `Return`/`Go`, `Back` or `Jump` during a melt cancels it
  (one `[GLIDE] cancelled by …` line) and snaps to the previous final set; a
  transport stop does **not** cancel it.
* Expected log: one `[GLIDE] replaced 1.500000 -> 1.400000 over 0.150 s
  (5 steps)` line, the sclang receiver's per-update lines, then one
  `[GLIDE] done (0.150 s)`.
* No new flag, OSC address or config key: the knob stays 0–2 s, default 0.15.
  Details: [docs/README_stage8_4.md](README_stage8_4.md) §6.1.

### Order matters

1. Ableton up (or at least reachable) → 2. `ji_start.scd` → 3. `ji_station.py`.
The bridge must start **after** the sclang rig: the control surface asks for the
canonical tempo/meter/transport when it installs.

---

## 3. Flag reference

### 3.1 Launcher — `run/ji_station.py`

| Flag | Default | Effect / when to use |
|---|---|---|
| `--midi` / `--no-midi` | on | Stage 6 drone (Bus 1) + arp (Bus 2). `--no-midi` when you only want drums/bass or no MIDI at all |
| `--drums` / `--no-drums` | on | Stage 8.1 drum output on Bus 3. `--no-drums` spares the bus |
| `--bass` / `--no-bass` | on | Stage 8.3a bass on Bus 4. `--no-bass` spares the bus |
| `--lead` / `--no-lead` | **off** | Stage 8.3d lead on Bus 5. Off by default: without it the bridge never opens Bus 5 and the Lead group shows `disabled`. A missing Bus 5 disables the lead only |
| `--gui` / `--no-gui` | on | `--no-gui` runs headless (bridge only), e.g. over SSH |
| `--bridge` / `--no-bridge` | on | `--no-bridge` starts only the GUI (bridge already running elsewhere) |
| `--interactive` | off | forwards stdin commands to the bridge: `m`/Enter mutate, `o` octave, `c <n>` changes, `t <0..1>` temperature, `a <0..1>` anchor mobility, `g <s>` glide melt time, `s` show, `q` quit |
| `--glide-time <s>` | `0.15` | stepwise/replaced class melt time: the replaced class travels to its new ratio over this time, for every voice (0 = instant). Pure add/remove mutations snap at 0.005 s and ignore it |
| `--listen-port <n>` | `9000` | bridge UDP listen port; the GUI sends its controls here |
| `--ui-port <n>` | `9001` | bridge → GUI status port; the GUI listens here |
| `--sc-port <n>` | `57120` | sclang language port (printed by `ji_start.scd`; change only if it warns) |
| `--listen-host` / `--ui-host` / `--sc-host` | `127.0.0.1` | hosts for the three streams |
| `--mpe-config <path>` | package default | alternative `config/mpe_config.json` |
| `--rhythm-config <path>` | package default | alternative `config/rhythm_config.json` |
| `--bass-config <path>` | package default | alternative `config/bass_config.json` |
| `--home-config <path>` | package default | alternative `config/home_config.json` (slots, auto/every/seed/drift, return glide, history depth) |
| `--lead-config <path>` | package default | alternative `config/lead_config.json` |
| `--drone-config <path>` | package default | alternative `config/drone_config.json` (mode + pad parameters; the bus stays `--mpe-config`'s `drone_port`) |
| `--bridge-args "<flags>"` | empty | extra bridge flags, quoted: `--bridge-args "--change-count 2 --temperature 0.2"` |
| `--dry-run` | off | print exactly what would start, then exit 0 (imports no PyQt6/mido) |
| `--check` | off | validate imports, config files, and mido output names, then exit |

`--check` is the fastest way to diagnose "it will not start":

```bash
uv run --with python-osc --with mido --with python-rtmidi --with PyQt6 \
  python run/ji_station.py --check
```

### 3.2 Bridge — `control/ji_bridge.py` (started by the launcher)

| Flag | Default | Effect |
|---|---|---|
| `--midi` | off | build the MPE drone/arp buses |
| `--drums` | off | build the drum output (works with or without `--midi`) |
| `--bass` | off | build the bass output (works with or without `--midi`/`--drums`) |
| `--lead` / `--no-lead` | off | build the lead output on Bus 5 (works with or without `--midi`/`--drums`/`--bass`; a bad config or a missing bus disables the lead only) |
| `--interactive` | off | stdin command line (see `--interactive` above) |
| `--glide-time <s>` | `0.15` | same as the launcher flag: the stepwise/replaced class melt time |
| `--listen-host` / `--listen-port` | `127.0.0.1` / `9000` | OSC control listen socket |
| `--ui-host` / `--ui-port` | `127.0.0.1` / `9001` | UI status destination |
| `--sc-host` / `--sc-port` | `127.0.0.1` / `57120` | sclang destination for `/ji/freqset/update` and `/ji/rhythm/tempo` |
| `--mpe-config` / `--rhythm-config` / `--bass-config` / `--home-config` / `--lead-config` / `--drone-config` | package defaults | config overrides |
| `--seed`, `--max-changes`, `--change-count`, `--temperature`, `--anchor-mobility` | see `--help` | mutation-engine defaults |

### 3.3 GUI — `ui/ji_ui.py` (started by the launcher)

| Flag | Default | Effect |
|---|---|---|
| `--bridge-host` / `--bridge-port` | `127.0.0.1` / `9000` | where controls are sent |
| `--sc-host` / `--sc-port` | `127.0.0.1` / `57120` | autotune/loop commands |
| `--ui-host` / `--ui-port` | `127.0.0.1` / `9001` | where status is received |

### 3.4 Old direct commands (still valid)

The launcher is a convenience: these keep working unchanged for debugging.

```bash
cd ji-live
uv run --with python-osc --with mido --with python-rtmidi \
  python control/ji_bridge.py --interactive --midi --drums --bass --glide-time 0.15
uv run --with python-osc --with PyQt6 python ui/ji_ui.py
```

---

## 4. Stop

1. Close the GUI window → **the bridge keeps running** (same as before).
2. `Ctrl+C` in the launcher terminal → sends SIGINT to both children, waits,
   then kills if needed, and prints `[LAUNCH] stopped`.
3. In sclang:

   ```supercollider
   ~ji_stop_all.();
   ~ji_dual_lookup_routine.stop;
   ~ji_dual_cpu_routine.stop;
   ~ji_ui_status_routine.stop;
   ~ji_tempo_clock.stop;
   ~ji_freqset_oscdef.free;
   ~ji_buf_loop1.free; ~ji_buf_loop2.free; ~ji_buf_loop3.free; ~ji_buf_loop4.free;
   ~ji_buf_freqset.free;
   ```

`~ji_stop_all.();` alone stops all audio, loops and recorders; the rest frees
the receivers, timers, clock, and buffers. Quitting sclang or rebooting scsynth
without restarting the interpreter changes the control-bus numbering — re-run
`ji_start.scd` from a restarted interpreter if you see the bus warning.

---

## 5. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `*** WARNING: input/output device not found` at boot | The names in `startup.scd` do not match. Run `ServerOptions.inDevices.postln;` / `ServerOptions.outDevices.postln;` and copy the exact names |
| `scsynth FAILED TO BOOT` | Device names, 48 kHz mismatch, or MiniFuse 2 not at 2 inputs / BlackHole not at 16 outputs |
| `[JI] startup error: missing SynthDef` | `synthdefs/*.scd` failed to load; scroll up in the post window for the load error, then re-evaluate `ji_start.scd` |
| `mido` / `rtmidi` import error | Run with `uv run --with mido --with python-rtmidi ...` (the launcher never imports mido itself) |
| `[MIDI] arp unavailable: ... IAC Driver Bus 2 is not open` | The bus is missing/renamed. `python run/ji_station.py --check` prints the available mido output names — copy one into `config/mpe_config.json` |
| `[MIDI] drums disabled: ...` | Bus 3 missing. Drums are skipped; everything else keeps running. Fix the bus or run `--no-drums` |
| `[MIDI] bass disabled: ...` | Bus 4 missing. Bass is skipped; run `--no-bass` to silence the warning |
| `[LEAD] disabled: IAC Driver Bus 5 not available ...` | Bus 5 missing while `--lead` is on (or `lead_config.json` is bad). The lead is skipped, the rest keeps running: create the bus, or drop `--lead`. `[LEAD] reason:` carries the exact error |
| Lead group greyed out | Correct until the bridge reports `port_ok = 1` for Bus 5 — start with `--lead` and tick **Lead on** |
| Drone modes greyed out | Correct when Bus 1 is not open. In `hold` all pad rows are disabled by design; pick `pulse`, `sequence` or `bend` |
| Only `hold` sounds, the drone line ends in `waiting for transport (press Play)` | Correct: `pulse`/`bend`/`sequence` are transport-gated like the drums. Press **Play**; the line switches to `| playing` and the bridge logs `[DRONE] <mode> started`. `hold` is never gated |
| The Drone group never shows `sequence`, or its controls are grey after an update | A stale bridge or GUI process is still running. Closing the GUI does **not** stop the bridge: quit the terminal running `ji_station.py` (or `pkill -f ji_bridge.py`) and restart. The bridge's first lines list `[DRONE] modes=hold\|pulse\|bend\|sequence` |
| `Address already in use` / bridge exits immediately | Another bridge still holds port 9000/9001. Quit the old one, or pass `--listen-port 9010 --ui-port 9011` to both sides (the launcher does both) |
| GUI shows `waiting for bridge` | The bridge is not running, or ports differ. Compare `[LAUNCH] bridge:`/`[LAUNCH] gui:` lines (they must use the same ports) |
| GUI controls greyed out | Correct: the arp/bass groups stay disabled until the bridge reports `port_ok = 1` for that bus |
| Controls react late (up to one beat) | Expected: while the transport runs, mutation/octave/home/drums/groove/bass/arp changes wait for the next quarter beat. Stop the transport to apply immediately |
| `Return` takes one more beat than expected | Check `dist` and `--max-changes`: the return is exactly `ceil(dist / max_changes)` beats. If a manual `Mutate`/`Octave`/`Back`/`Go` arrived, the return was cancelled (look for `[HOME] return cancelled by ...`) |
| `Go`/`Return`/`Back` greyed out | Correct: `Go`/`Clear` need a captured selected slot, `Return`/`Jump` need `dist > 0`, `Back` needs at least two history snapshots, and all set actions disable while a return is in flight |
| `Auto` greyed out | Correct until a slot is captured (`Set home`) |
| Home line shows `waiting for bridge` | The bridge is not running, or an old bridge without `/ji/home/state` still holds port 9000. Quit it and restart `run/ji_station.py` |
| The set stops moving near home with a high `Drift` value | Expected: the drift cap rejects proposals that would exceed `Drift`; `0` = unlimited |
| Arp does not sit on the grid | `Sync` must be `beat`; press Play once so the arp snaps to the transport origin, or change `Div` (both re-snap). With the transport stopped the arp still runs at the BPM but has no beat to lock to |
| Stale behaviour after editing a `.scd` file | The editor buffer, not the file, is what gets evaluated. Re-evaluate `ji_start.scd` (it re-installs everything cleanly) |
| Bus numbering warnings | scsynth was rebooted without restarting sclang. Language → Restart Interpreter, then re-evaluate `ji_start.scd` |

---

## 6. Maintenance rule

After every feature or stage:

1. update **this file** (new flags, new config keys, new stop steps),
2. update the matching `--help` text,
3. update the stage README and the design doc,
4. run the Python suites listed in `README.md`.

`docs/START_HERE_stage8_1.txt`, `docs/START_HERE_stage8_2.txt` and
`docs/START_HERE_stage8_3.txt` are historical notes. Do not follow them; they are
kept only for the record.

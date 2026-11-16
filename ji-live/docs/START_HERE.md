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

To play/record that lead from a MIDI controller (or the QWERTY keys), add
`--lead-in` as well:

```bash
uv run --with python-osc --with mido --with python-rtmidi --with PyQt6 \
  python run/ji_station.py --lead --lead-in
```

The controller name comes from `config/lead_input_config.json` (`Launchkey` by
default); override it with `--lead-in-port "Launchkey 49"`.

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
| **Lead** `Note len` | off = the `Gate` knob caps every note (generated line); on = each note's own length sets the gate (recorded takes) |
| **Drone modes** `Mode` | `hold` (Stage 6, default), `pulse` (repeats the anchor **pitch class** at seeded octave offsets), `sequence` (a repeating figure that climbs the current set from the anchor and comes back), `bend` (hold, then a slide) |
| **Drone modes** pulse row | `Notes`, `Grid`, `Gate`, `Span` |
| **Drone modes** sequence row | `Seq notes` (2–8), `Length` (1/4, 1/2, 1, 2 or 4 **bars** per held note), `Gate` (% of the note); each step is the next class up/down in the current set, so the figure moves harmonically over bars and still follows mutations |
| **Drone modes** bend row | `Dir`, `Semis`, `Slide`, `Hold`; the cycle (hold + slide, rounded up to the next beat) is derived and shown in the status line |

Mutating the frequency set does **not** rewrite the melody: every note keeps its
class when that class survives, and only notes whose class disappeared move (to
their fallback degree). `hold` sounds exactly as before. The top-row `Drone`
switch is still the only drone mute. Details:
[docs/README_stage8_3d.md](README_stage8_3d.md).

### Lead input — play, record, save a melody (Stage 9.1/9.2, runtime, no command)

Start with **both** flags: `--lead --lead-in`. The input is off by default;
`--lead` alone is exactly the old generated-line rig, and `--lead-in` without
`--lead` prints one line and is ignored. The controller is configured in
`config/lead_input_config.json` (`port`, `match`, `channel`, `quantize`,
`count_in`, `bars`, `velocity_scale`, `octave_shift`, `bend_range_semitones`)
or overridden with `--lead-in-port "<name>"` / `--lead-in-config <path>`.
Melodies are saved in `melodies/` (override with `--melody-dir`); the folder is
created on the first save.

| Control | What it does |
|---|---|
| `Port` + `Refresh` | the controller the bridge opened (e.g. `Launchkey 49`); `Refresh` closes/re-opens it after editing the config |
| `Keys` | QWERTY play: `a w s e d f t g y h u j`, `z`/`x` shift the octave. GUI-local; the input always monitors, and a played key takes the lead |
| `Rec` | record from the next bar line (needs Play); an **audible** `Count-in` first (`off`/`1 beat`/`1 bar`/`2 bars`) |
| `Stop` | cancel the count-in, or keep the partial take |
| `Revert` | restore the recorded base (all variations undone) |
| `Clear` | drop the melody and return to the generated line |
| `Quant` | 0–100 %: how hard onsets/lengths snap to the (swung) grid |
| `Count-in`, `Bars` | the audible count and the take length (1–8 bars) |
| `Melody`, `Name`, `Save`, `Load`, `Del` | the named melody library |

**No Live toggle (9.2).** The input always monitors: a played key overrides the
lead immediately (the loop's sounding note stops, the input sounds), and on
release the loop's current note attacks again if it was still sounding, else the
next step enters. The pedal keeps the override until pedal-up. `Record` still
stops the sequencer so the take is clean.

**Pitch bend + mod wheel (9.2).** The class is the note from the key; the wheel
is added on top of it and sent as MPE pitch bend on Bus 5 (no re-attack, no
class snap). The mod wheel is CC 1 on the same channel. The controller's bend
range must match `bend_range_semitones` (default 2); set it in the controller's
own settings if it differs. The movement is recorded with the take, plays back
with the phrase, and round-trips through melodies and states. The status line
shows it: `Input: Launchkey 49 - idle - 13 notes - bend +12 ¢ - mod 64`.

**Procedure.** Press Play and play: the first key takes the lead, releasing it
returns to the loop. Press `Rec`; the count-in is audible and the take opens on
the next bar line (a note held across the line is in the take from step 0) and
closes by itself after `Bars`. The take plays back quantised (and on the swung
grid when the groove swings) with the recorded bend/mod movement; `Vary` /
`Var every` keep transforming it, `Revert` puts the recorded take back. Type a
name and press `Save`; after a bridge restart press `Refresh` and `Load`.
`Clear` returns to the generated line.

Pitch mapping is **pitch-faithful**: a key sounds the nearest frequency class
of the current set in the register played (a key on a class is exact). There is
no fixed scale-degree keyboard. The bridge is the authority on ports: the GUI
never enumerates MIDI devices. If the controller is not found, edit the config
(or pass `--lead-in-port`) and press `Refresh`.

**Ableton routing:** CoreMIDI lets several applications open the same source.
Turn **off** Ableton's Track input for this controller (Live → Preferences →
MIDI Ports), or every note will sound twice (the snapped Bus 5 note plus
Ableton's raw input). Nothing new is needed in Audio MIDI Setup and no new bus
is created. Details: [docs/README_stage9_1.md](README_stage9_1.md).

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

### Performance states — save / recall a state (Stage 8.5, runtime, no command)

The **State** row (under **Home**) saves the whole performance configuration
under a name and recalls it later, without restarting anything. It needs no flag
and no new bus; a recall reuses `/ji/freqset/update`.

| Control | What it does |
|---|---|
| `Name` | an existing state (sorted), or type a new name |
| `Save` | save the current set, mutation controls, home slots, tempo/meter, drums, groove and bass/arp/lead/drone settings (immediate) |
| `Load` | recall the state: immediate when stopped, next quarter beat while running |
| `Delete` | delete the state file (immediate) |
| `Refresh` | ask the bridge for a fresh list + status |

The status line reads `State: 3 saved | saved A-drone | loaded A-drone`, or
`State: none saved`, or `State: error …`. `Save` is disabled while the name is
empty; `Load`/`Delete` only enable for a name in the list.

**Procedure.**

1. Make the rig sound the way you want, type `A-drone` in `Name`, press `Save`.
   The log shows `[STATE] saved A-drone (… fields, states/)`.
2. Change the set, controls, tempo/meter, groove, homes and voice settings.
3. Select `A-drone`, press `Load`. Stopped: one set change and one
   `/ji/freqset/update` at once. Running: it lands on the next beat, still one
   update, no burst.
4. Quit and restart the bridge (same state folder), press `Refresh`, load `A`
   again: the file persists.

A state is a **colour, not a play command**: the transport keeps its current
state. **Loops and the sclang autotune parameters are not part of a state** (the
owner excluded loop recall), and nothing writes into `config/`. The files live
in `ji-live/states/<name>.json` (created on the first save); `--state-dir`
overrides the folder. Format, name rules, atomic-save notes, diagnostics and the
human checklist: [docs/README_stage8_5.md](README_stage8_5.md).

### Loop editing — memory slots, dub, reverse, ratios, granular, morph (Stage 8.6.2, runtime, no command)

Every **Loops** box gains a `Dub` button; the group gains one **Loop edit** row,
one **Grain** row, one **Grain motion** row (8.6.1) and one **Morph** row
(8.6.2) for the loop chosen in that row's `Loop` combo. No flag, no config key,
no new bridge: loops stay sclang-owned. (9.0 later adds the private FX audio
buses and the Harm row described below.)

| Control | What it does |
|---|---|
| **Dub** | Overdub: records **over** the loop for exactly one pass (`preLevel 1`), keeping what is there; the loop keeps playing. On an empty loop it records a fresh take. `Stop Rec` ends a dub early. `Rec` still **replaces** |
| `Rev` | Reverse playback. The read head keeps its position and the direction flips (no restart) |
| `Rate` | Varispeed at a fixed length ratio: `2/1`, `3/2`, `5/3`, `5/4`, `4/3`, `1/1`, `3/4`, `4/5`, `2/3`, `3/5`, `1/2`. It also re-pitches the loop (2/1 = +12 st, 2/3 = −7.02 st) |
| `Gain` | Per-loop output gain 0–1, default 0.70 (today's loudness) |
| `Count-in` | `off` / `1 beat` / `1 bar` / `2 bars`: delays a **Rec** by that many beats on the tempo clock, starting at the next beat. A bar is the current meter's bar **rounded up** to a whole quarter beat (4/4 = 4, 3/4 = 3, 6/8 = 3, 5/8 = 3, 7/8 = 4). A second Rec during the count skips it and records immediately. Rec only, never a dub |
| `Auto` | Auto-play: starts the loop as soon as a take finishes (early `Stop Rec` included). Beat mode needs a running transport |
| `Mem` + **Save** / **Recall** / **Clr** | Session-only memory slots `A`–`D`: a copy of the loop's audio **and** its edit state. `Recall` leaves the loop stopped — press Play |
| `Grain`, `Sync`, `Div`, `Rate`, `Size`, `Pos`, `Scatter`, `Pitch`, `Rev%` | Granular playback instead of normal playback. `Sync = free` runs on its own clock (transport stopped is fine); `Sync = beat` fires a small burst per `Div` and follows the transport. Defaults (8.6.1): Rate 20, Size 120 ms, Pos 50 %, Scatter 10 % — a continuous cloud |
| `Motion`, `Speed`, `Depth` | The grain read position **travels** through the loop: `off` / `forward` / `backward` / `pingpong` / `drift`, at `Speed` loop-lengths per second (0.03 reaches the end of an 8 s loop in ~33 s) over a `Depth` of the buffer (100 % = half the loop each way). This is what makes the texture evolve |
| `Size Jit`, `Pitch Jit` | Per-grain size variation (thickens the cloud) and per-grain pitch scatter (shimmer; heavy values make the autotune warble) |
| `Snap` | Snap the grain ratio to the nearest ratio of the **current JI set** (× octaves). The status line then shows the effective ratio (e.g. `pitch ×3/2`), and a set mutation re-snaps a playing grain loop without a click |
| `Smear` | Post-autotune diffusion (two short allpasses): blur/space on top of the tuned loop. 0 % is the dry accepted chain |
| `Freeze` | Hold the motion where it is (the cloud keeps sounding); unfreezing continues from the same position |
| `Morph` | **The main gesture:** 0 % is the original loop (normal playback, through the autotune), 100 % is the full grain cloud, in between the two are smeared together. Needs `Grain` on |
| `Melt` | Tick it (or press Play with it ticked) and the loop starts as the original, then melts into the grains over ~4 s; untick to melt back. Moving `Morph` while melting re-targets the ramp |
| `Filter` / `F.Rate` | Moving melt filter (post-autotune): a lowpass and a low cut on two slow LFOs fuse the dry and grain layers; `F.Rate` is the sweep speed (0.12 ≈ 8 s per breath). 0 % is bypass and it also works with Grain off |
| `Breath` / `B.Rate` | The loop's **own** grain breath (8.7): density and grain size swell on a slow LFO inside the voice. `B.Rate` is the speed (0.03 is a long breath, 0.3 a pulse). 0 % is an exact bypass. Needs `Grain` on |
| `Loop` (combo) | Which loop the edit and grain rows show and change |

**The loop always stays in the JI autotune** (Tartini → nearest class →
`PitchShift`, `mix = 1.0`). Varispeed and granular change what goes *into* that
chain, never bypass it. So a repitched loop is re-analysed at its new pitch and
the current set may pull it partly back toward a class; a grain cloud may sound
tuned, detuned or both. That interaction is **intended** — "new material", not
an exact transposition. `Rate = 1/1`, normal mode sounds exactly like the
accepted loop.

**The 8.6.1 granular redesign** (owner: "the goal is to get cool evolving
effects … moving through the buffer") is why the grain row has `Motion`,
`Speed`, `Depth`, `Size Jit`, `Pitch Jit`, `Snap`, `Smear` and `Freeze`: the
cloud travels, jitters and snaps to the set instead of repeating one slice.
**8.6.2** adds the `Morph` slider (original ↔ grains), `Melt` (start dry and
progressively glitch) and the moving `Filter`. Grain off disables the grain,
motion and morph rows (the Filter/F.Rate stay usable on the plain loop).
Details: [docs/README_stage8_6.md](README_stage8_6.md) §14 and §15.

**Edit changes are immediate** (reverse, rate, gain, mode and every grain
parameter); Record/Play keep their free-immediate / beat-next-beat rule. The
count-in and auto-play ship **off**, so the accepted Rec/Play behavior is
unchanged. Memory slots are **session-only**: nothing is written to disk and
re-evaluating the rig empties them. Design, the DSP graph, the OSC surface and
the human checklist: [docs/README_stage8_6.md](README_stage8_6.md); the 8.6.1
granular redesign (motion, cloud, JI snap, smear, the Div fix) is §14 there and
its acceptance checklist is §14.12.

### Harmonic-series delay FX + count-in repair (Stage 9.0, runtime, no command)

One **Harm** row sits under the Morph row for the selected loop. It is a
**post-`PitchShift`** FX on a private per-loop audio bus: the loop keeps the
accepted autotune, and only the tuned signal is harmonized. `Harm = off` (the
default) creates no FX synth and adds no wet.

| Control | What it does |
|---|---|
| `Harm` | `off` / `Cascade` / `Delay`. `Cascade` captures one segment and plays it once with the harmonics entering in order; `Delay` is a continuous harmonized buffer delay (varispeed character, no trigger) |
| `Mix` | Wet return 0–100 % (default 35 %). The dry loop is always upstream, so 0 % is the accepted loop |
| `Time` | Free time in seconds (0.05–2.00, the 2 s FX buffer): Cascade segment length / Delay base window |
| `TimeSync` | `free`, or a fixed musical time: `1/4` quarter, `1/4d` dotted, `1/4t` triplet, `1/8`, `1/16`, `1/32`, `1/2`, `1/1`, `1 bar`. Fixed values follow the bridge tempo; the seconds spin then shows the derived value read-only |
| `Steps` | Harmonics 1–8 (default 4): 1 = fundamental only, 2 adds the octave, 3 the 12th, 4 the 16th … |
| `Space` | Cascade entry spacing 0–1000 ms (default 80). 0 = all harmonics together. Inert in Delay |
| `Fall` | High-harmonic roll-off in both modes: amplitude `1 / k^(0.5 + 1.5 × Fall)`; 0 % = gentle `1/√k`, 100 % = `1/k²` |
| `Auto` | Cascade auto-fire (default on); first capture after one segment, then after each cascade. Inert in Delay |
| **Fire** | One Cascade pass now. Enabled only in Cascade while the selected loop is playing. It sends `/ji/loop/fx_fire`, not an edit |

A fixed-time FX retimes when the BPM changes; a free-time FX does not. For a
plain fixed-time delay, use `Delay` + `Steps 1` + a `TimeSync` note value.
Longer fixed values clamp to the 2 s buffer. Details, the DSP and the count-in
fixes:
[docs/README_stage9_0.md](README_stage9_0.md).

### Breathing rhythm + bass hit row (Stage 8.7, runtime, no command)

The **Rhythm** group gains one **Breath** row; the **Bass** group gains a
**Hits** row. Nothing new to launch: the bridge owns the clock and the voices
follow.

| Control | What it does |
|---|---|
| `Breath` | Makes the played grid breathe: events arrive early/late and velocities swell in a slow, physical cycle. The BPM readout and every bar line stay exactly put. Off is exactly today's timing |
| `Depth` | How far events move early/late: up to ±½ of a 16th note at 100 % (35 % default) |
| `Period` | One full inhale + exhale in bars (1 = a fast pulse, 16 = a long swell) |
| `Shape` | `sine`/`triangle` are even; `inhale` fills fast and releases slowly; `exhale` the opposite |
| `Intensity` | The velocity/expression swell (0 % = timing only, 100 % = strong) |
| `Phase` | Rotate the breath against the bar (0 % starts at the bottom of the inhale) |
| `Arp local` | The arp's extra breath depth. With `Arp sync` off it also uses a half-cycle offset (it breathes against the groove); with sync on it is just deeper |
| `Arp sync` | Off: the arp breathes half a cycle out of phase with the groove. On: the arp uses the master phase, so it inhales/exhales **with** the drums, bass and lead |
| Bass `Hits` | One editable row of bass hits per bar (no pitch, no per-step velocity). Enabling seeds the row from the generated pattern if it is empty; while Hits is on, `Len` is not used |
| Bass `Copy` / `Clear` | Seed the row from the generated pattern of bar 1 / turn every hit off (deliberate silence) |

**What breathes:** drums, bass, arp and lead get timing **and** velocity; the
drone gets velocity only. The arp is the only local voice; `Arp sync` makes it
follow the master phase instead of its own half-cycle offset. Each **loop** also
has its own grain breath (`Breath`/`B.Rate` in the Morph row), completely
independent of the master breath and of the loop's timing. **The canonical
tempo, the bar grid, the quantized `PHASE_*` queue and the sclang loop clock are
never touched** — breath is a scheduling offset inside each voice. The breath
setting is **not saved in a performance state** (it is a live feel control); the
bass hit row **is** saved. Details:
[docs/README_stage8_7.md](README_stage8_7.md).

### Fretboard improvisation aid (Stage 8.8, runtime, no command)

The **Fretboard** group (full width, between Lead and Status) shows the nearest
12-TET notes to the live JI set and marks every occurrence of them on a full
guitar neck. It is a **read-only** aid: no flag, no bus, no OSC address, no new
config or state key. The set arrives on the existing `/ji/freqset/changed`, so
it follows mutations, state loads and hello with no restart.

| Control | What it does |
|---|---|
| `Strings` | 6, 7 or 8 strings. Adding one prepends a low fourth (7-string B adds B1; 8-string F# adds F#1); removing drops the lowest strings |
| `Frets` | 0–24 frets. 24 is a full neck; 12 is a shorter diagram |
| `Tuning` | Whole-neck presets: Standard E, Drop D, Drop C, DADGAD, Open G, Open D, Half-step down, 7-string B, 8-string F#. Editing one string below makes it `Custom` |
| String combos | One per string, low → high, any MIDI note 0–127. The label shows the exact note (`E2`, `D2`, …) |
| `Names` | Draws the note name inside the markers; off keeps the markers |
| Set label | `12-TET set (A = 220.00 Hz): A B C# D E F# G#`, or `Fretboard: waiting for the set` |
| Legend | `A 1/1 +0¢ · B 9/8 +4¢ · C# 5/4 -14¢ · …`. The cents are signed: **negative = the set's frequency is flat** of the 12-TET note. A pitch class reached by two ratios shows the closest first and the other in parentheses |
| Board | Root in orange, other scale notes in blue, the anchor with a purple ring; hover a marked position for the JI fraction, cents and one-octave frequency |

**Procedure.** With the bridge running, read the set label and legend; every
note of that set is highlighted all over the neck. For Drop D, pick `Drop D`
(open low `D2` is marked and the rest of the board redraws); for anything else,
set each string combo by hand. `Strings` 7/8 shows the extra low string. Frets
24 → 12 shortens the diagram.

**The tuning is session-only and never sent.** It is not saved in a performance
state or in `config/`, and restarting the GUI returns to Standard E. The board
is an aid, not part of the rig. Details:
[docs/README_stage8_8.md](README_stage8_8.md).

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
| `--state-dir <path>` | `states/` | Stage 8.5 performance-state folder. A relative path resolves from `ji-live/` (the project root) |
| `--lead-config <path>` | package default | alternative `config/lead_config.json` |
| `--lead-in` / `--no-lead-in` | **off** | Stage 9.1 MIDI/QWERTY input for the lead. Needs `--lead`; without it the input is ignored. A missing/failing controller disables only the input |
| `--lead-in-port <name>` | config | controller name/substring, overriding `config/lead_input_config.json` |
| `--lead-in-config <path>` | package default | alternative `config/lead_input_config.json` (port, match, channel, quantize, count-in, bars, velocity scale, octave shift, bend range) |
| `--melody-dir <path>` | `melodies/` | Stage 9.1 melody library folder; created on the first save. A relative path resolves from `ji-live/` |
| `--drone-config <path>` | package default | alternative `config/drone_config.json` (mode + pad parameters; the bus stays `--mpe-config`'s `drone_port`) |
| `--bridge-args "<flags>"` | empty | extra bridge flags, quoted: `--bridge-args "--change-count 2 --temperature 0.2"` |
| `--dry-run` | off | print exactly what would start, then exit 0 (imports no PyQt6/mido) |
| `--check` | off | validate imports, config files, the state/melody directories, and mido output + input names, then exit |

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
| `--lead-in` / `--no-lead-in` | off | build the Stage 9.1 MIDI/QWERTY input rig (only when `--lead` is on) |
| `--lead-in-port <name>` | config | controller name/substring override |
| `--lead-in-config <path>` | package default | alternative `config/lead_input_config.json` |
| `--melody-dir <path>` | `melodies/` | melody library folder (relative paths resolve from the project root) |
| `--interactive` | off | stdin command line (see `--interactive` above) |
| `--glide-time <s>` | `0.15` | same as the launcher flag: the stepwise/replaced class melt time |
| `--listen-host` / `--listen-port` | `127.0.0.1` / `9000` | OSC control listen socket |
| `--ui-host` / `--ui-port` | `127.0.0.1` / `9001` | UI status destination |
| `--sc-host` / `--sc-port` | `127.0.0.1` / `57120` | sclang destination for `/ji/freqset/update` and `/ji/rhythm/tempo` |
| `--mpe-config` / `--rhythm-config` / `--bass-config` / `--home-config` / `--lead-config` / `--drone-config` | package defaults | config overrides |
| `--state-dir <path>` | `states/` | Stage 8.5 performance-state folder (relative paths resolve from the project root) |
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
| `State: error invalid state: …` | The state file has a bad value (e.g. an anchor not in its set) or a newer `version`. Nothing was applied. Compare with `docs/README_stage8_5.md` §2, fix or re-save it |
| `State: error no such state` / `[STATE] gap: …` | The name is not in the list (deleted, or a different `--state-dir`). Press `Refresh`; check `[STATE] dir=…` at bridge start |
| `State: error invalid state: …name…` on Save | The typed name breaks the rules (1–40 chars, starts alphanumeric, only `A-Z a-z 0-9 space . _ -`, no `..`/`/`/`\`). Rename it |
| `[STATE] save failed: state directory … is not writable` | The folder is read-only or cannot be created. Point `--state-dir` somewhere writable; states are never written into `config/` |
| `[STATE] invalid state "set.json": … invalid JSON …` | The file is hand-corrupted. Delete it (or fix the JSON) and re-save from the GUI; nothing was applied |
| `[STATE] bass/drums/arp/lead/drone skipped: … not available` | That bus is missing (e.g. `--no-bass`). Only that voice was skipped; the rest of the state landed. Fix the bus and load again |
| The load changed the sound but not the transport | Correct: a state is a colour, not a play command. Press Play/Stop yourself |
| Load does nothing while running | Expected up to one quarter beat. Look for `[QUANT] state load …` on the next beat. While stopped it is immediate |
| A loop or the autotune changed after a recall | It should not; loops and autotune are excluded from a state. If it happens, it is a bug: record the `[STATE]` lines |
| `Dub` does nothing / records a fresh take | A dub needs a loop buffer. On an `empty` loop it allocates and records a fresh take and says so (`[LOOP i] no buffer -- dub records a fresh take instead`). A dub during a `recording`/`dubbing` loop is refused; `Stop Rec` first |
| `[LOOP 2] mem A is empty` on Recall | The slot was never saved (or was cleared, or the rig was re-evaluated). Save it first. Recall only enables for a filled slot |
| Beat-synced grains are silent | `Sync = beat` is transport-gated: press Play. The grain row shows `beat grains need the transport (press Play)` while stopped. `Sync = free` runs with the transport stopped |
| A reversed loop sounds "backwards" | Expected. `Rev` plays the buffer end → start; the autotune still retunes it note by note |
| A varispeeded loop still sounds in tune | Expected: the loop still runs through the JI autotune, which pulls it toward the current set. The rate changes speed/pitch of the *input* to that chain; the set wins harmonically |
| The count-in counts with the transport stopped (free mode) | Expected: free loops are not transport-gated, so the count runs on the tempo clock. In **beat** mode a transport stop cancels the count-in (`count-in cancelled (transport stop)`) |
| `auto-play skipped: transport stopped (beat mode)` | Expected: `Auto` in beat mode needs a running transport. The take stays `recorded`; press Play. Free mode auto-plays with the transport stopped |
| The loop label shows `counting` and never moves | Fixed in 9.0 (A1/A2): a stored `\counting` is never restored. If it still happens with 9.0, re-evaluate `ji_start.scd` — an old control surface is still loaded. `Stop Rec` during a count restores `empty`/`recorded` |
| A second Rec during a free-mode count restarts the count | Fixed in 9.0 (A1). The log must say `[LOOP i] count-in skipped (second Rec)`; if not, the old `control_surface.scd` is loaded |
| Rec queues (beat) or is pressed (free) but no take starts | Check the post window for `[LOOP i] record requested` and `[LOOP i] recorder running`. If neither appears, the control surface/rig is stale: Restart Interpreter, re-evaluate `ji_start.scd` (it must end with the 9.0 ready line), then restart the Python GUI. If `record requested` appears but not `recorder running`, `SynthDescLib.global[\ji_looper_recorder]` is nil — startup did not load every SynthDef; fix the post-window load error first |
| `~ji_ui_control_surface_version` is not `9.0.0` | The old control surface is still installed. Re-evaluate `ji_start.scd`; re-evaluating only one file can leave a half-updated rig |
| A Harm edit switches the mode back to `off` | Fixed in the 9.0.1 UI/rig pass: the GUI re-sends the visible mode before every Harm edit/Fire, repeated modes do not restart the FX, and a missing FX synth is recreated on the next parameter edit. Re-evaluate `ji_start.scd` and restart the Python GUI |
| Diagnose Rec/Harm without the GUI | Evaluate `tests/sc/debug_stage9.scd` after `ji_start.scd`; it prints `[DEBUG]` lines for the direct mode edit, the 39-value echo, the core recorder and the FX node. It overwrites loop 4. |
| Harm is on but there is no wet sound | Check `[LOOP i] harm … started (… in 18 -> out 2 …)` in the post window. `Cascade` needs Auto on or Fire; `Delay` is continuous after one `Time` window. The wet is summed into the loop's usual output (SC 2/3), not a separate channel. If the line is absent, check `~ji_loop_players[i-1]` and `~ji_loop_fx_synths[i-1]` |
| A bar count-in in 5/8 or 7/8 starts early/late | Fixed in 9.0 (A4): bars are rounded **up** to whole quarter beats (5/8 → 3, 7/8 → 4). Re-evaluate `ji_start.scd` if the old rounding is still audible |
| Harm does nothing | `Harm` must not be `off`; `Fire` only works in `Cascade` while that loop is playing. `Delay` needs no trigger. Check `[LOOP i] harm … started` in the post window |
| No harmonized sound / a silent FX | The FX reads the loop's private send, so the loop must be playing. Check `s.plotTree` for a `ji_harmonic_delay` node after the player, and the `in … -> out …` line (the actual FX bus indices are printed by startup, e.g. 18–21 on a 2-in/16-out boot; output 2/3). A Fire before `Time` seconds of audio exist plays a partial segment |
| Cascade keeps restarting / auto fires too often | `Auto` is on and fires every segment + cascade + 0.1 s; untick it and use `Fire`. A manual Fire during a cascade restarts the same frozen segment by design |
| Fixed time does not match the tempo | `TimeSync` must be a note value (not `free`); the spin is then read-only. A BPM change retimes a playing fixed FX. Values longer than 2 s clamp to the buffer (`1 bar` at slow tempos) |
| Harm settings survived a state load | They should not: loop FX are session-only and excluded from performance states. If they did, it is a bug — record the `[STATE]` lines |
| Memory slots are empty after re-evaluating the rig | Expected: slots are session-only (`Buffer` copies, no files). Re-evaluate `ji_start.scd` only between sessions; re-save the slots you need |
| Morph does nothing | `Grain` must be on (the Morph row is disabled otherwise). Morph 0 % is intentionally the original loop; raise it |
| Melt runs too fast / too slow | The ramp is `~ji_loop_melt_seconds` (4 s default) in sclang: evaluate `~ji_loop_melt_seconds = 8.0;` after the rig |
| The loop jumps instead of melting | `Melt` is off: with Melt off the Morph slider is direct. Tick Melt (and press Play) for the ramp |
| The Filter seems to do nothing | It is bypassed at 0 %; raise it past ~20 % and watch `F.Rate`. It is subtle on a dry loop — most obvious on a morphed one |
| The morphed loop sounds quieter in the middle | Expected: the dry/grain crossfade is equal-power, so the two layers sum differently than a linear fader. Put `Gain` where it sounds right |
| Grains still sound static / like one slice repeating | `Motion` must not be `off` (or `Freeze` is on) and `Depth` must be > 0 `%`. `Pos` is the centre of the scan; a low `Scatter` with `Motion = off` deliberately repeats one slice |
| The grain cloud is silent | `Pos 0 %` with `Motion = off` reads the start of the loop: if the take begins with silence the cloud is silent. Move `Pos`/`Scatter`/`Motion` |
| The Pitch control "does nothing" | `Snap` is on and the nearest set ratio in that octave is the one you asked for: read the **effective** ratio in the status line (`pitch ×3/2`); `Snap` off passes the raw ratio |
| The cloud warbles | Expected with heavy `Pitch Jit` (and a jittery cloud tracking badly); lower `Pitch Jit`. Grain mode never retunes faster than 0.15 s |
| The Div combo jumps back to 1/16 | Fixed in 8.6.1 (sclang compared division Strings by identity, so GUI div/count-in edits were rejected). If it still happens, re-evaluate `ji_start.scd` — an old control surface is still loaded |
| Beat-synced grains sound like one tick | Raise `Rate` (it sets the burst density; default 20/s) and/or `Burst`; the burst decays over about half the current division |
| Clicks on a rate or reverse change | Rate/reverse are smoothed (0.02 s `Lag`) and a mode switch is crossfaded, so a click is a bug. Record the rate, the mode and the buffer duration; try `1/1` to confirm the baseline, then report |
| Breath does nothing | Check `Breath` is ticked, `Depth` is above 0 and `Period` is not absurdly long (32 bars is a very slow swell). Stopped transport is fine for the arp but drums/bass/lead only run while the transport does |
| Everything sounds late or early, then normal | Expected only inside the cycle: `Depth` and `Phase` move events up to ±½ of a 16th. Set `Depth 0` for exactly the old timing; the BPM readout and bar lines never move |
| The arp still breathes when the groove doesn't | `Arp local` is above 0 (and/or `Breath` with depth is on): the arp has its own half-cycle breath. Set `Arp local 0` to stop it, or tick `Arp sync` to put it on the master phase |
| Arp sync seems to do nothing | With `Arp local 0` and/or `Depth 0` there is little to hear. Raise the master `Depth` and `Intensity`; `Arp sync` changes the phase, and `Arp local` adds depth on top |
| The bass plays the old pattern | `Hits` is off, so the generated pattern is playing. Tick `Hits` (it seeds from the generated pattern) or press `Copy` |
| My bass hits vanished | `Clear` turns every cell off deliberately; a recalled state may carry an all-off row; press `Copy` to re-seed from the generated pattern. A meter/grid change re-shapes (never clears) the row |
| The loop breath does nothing | `Grain` must be on (the loop `Breath`/`B.Rate` controls are disabled otherwise) and `Breath` must be above 0 |
| The fretboard board is empty / says `waiting for the set` | The bridge is not running yet or has not broadcast `/ji/freqset/changed`; press `Mutate`, load a state, or wait for hello. A fresh GUI before the bridge starts shows the bare neck by design |
| A note is marked that sounds wrong | It is the **nearest 12-TET note** to a set frequency, not the JI frequency itself. Read the legend's signed cents (`-14¢` = the JI note is 14 cents flat of the marked 12-TET note) |
| The open G is missing in A major | Correct: A major is `A B C# D E F# G#`; G natural is not in it. G# is marked at fret 1 on the G string |
| My guitar tuning resets | Session-only by design: the tuning is a local aid, not part of the performance state or `config/`, and a GUI restart returns to Standard E. Re-pick the preset or edit the strings again |
| Lead input group missing / `Port: --` | Start with **both** `--lead --lead-in`; `--lead` alone is the generated-line rig and the input group stays inert. Restart the bridge after changing flags |
| `[LEAD-IN] disabled: no input port configured` | `config/lead_input_config.json` has an empty `port`. Set the controller name/substring, or pass `--lead-in-port`, then press `Refresh` |
| `[LEAD-IN] disabled: no input matched "…"` | The controller name does not match `mido.get_input_names()`. `python run/ji_station.py --check --lead-in` prints the input names; copy one (contains match is case-insensitive) |
| `[LEAD-IN] disabled: 2 inputs matched "MIDI"` | The configured substring is ambiguous; use a longer, unique name (the line lists the matches) |
| `[LEAD-IN] input lost: …` | The controller was unplugged/renamed. Reconnect it and press `Refresh`; the rest of the rig keeps running |
| A played note sounds twice | Ableton is also listening to the controller. Turn off its Track input (Live → Preferences → MIDI Ports); CoreMIDI allows several apps to open one source |
| Keys do nothing | `Keys` is off, the Lead switch is off, a text box has focus, a modifier is held, or the input port is not open. The status line shows `no input port` or an `idle` phase when the input is up |
| `Rec` is greyed out | The transport is stopped or the input port is not open. Press Play; `Port:` must show the controller |
| `[LEAD-IN] record needs the transport (press Play)` | The beat clock is frozen while stopped; recording always needs a running transport |
| The count-in is silent | 9.2 made it audible: anything before this build is a stale bridge. Playing during the count-in sounds; notes that end before the bar line are not stored |
| A played key does not take the lead | The input needs `--lead-in --lead`, an open port, the Lead switch on, and the `Keys` toggle for QWERTY. `source` is irrelevant (there is no Live mode) |
| The loop note keeps sounding while a key is held | A stale bridge from 9.1 is running (no override). Restart `run/ji_station.py`; the log line is `[LEAD-IN] override on at step N` |
| The wheel barely moves the pitch / it jumps too far | The **controller's** pitch-bend range does not match `bend_range_semitones` in `config/lead_input_config.json` (default 2). Set it in the controller, or edit the config and press `Refresh` |
| The pitch bend is very subtle / barely audible | The rig sends **±341** of the 14-bit bend at a full wheel (±200 ¢ at the input). That is **±2 st** at a 48 st instrument but only **±8.3 ¢** at a 2 st instrument — a full wheel must sound like a whole tone. If it does not, the Lead instrument's MPE pitch-bend range is not 48: set the instrument/MPE zone to **48 st** (the range `config/lead_config.json` assumes), or set `bend_range_semitones` in `config/lead_config.json` to the instrument's actual range (e.g. 2) and restart. The same mismatch makes the JI lead's bends 24× too small (at 2 st it plays essentially 12-TET), so fixing it also restores the lead's tuning. `--check` prints both ranges and the resulting interval |
| I want a wider bend than ±2 st | Raise `bend_range_semitones` in `config/lead_input_config.json` (0–48, the full wheel's span in semitones) and restart. It scales the sent bend proportionally: 12 gives ±12 st at a 48 st instrument. At a 2 st instrument the maximum possible is 2 st whatever the setting |
| The pitch wheel does nothing | Triage in this order. **1)** Hold a key and move the wheel: the bridge log must print `[LEAD-IN] wheel: pitch bend …` once, and `[LEAD-IN] wheel: sent pitch bend … while note … sounds` once per gesture. **2)** No `wheel:` line: run the read-only probe `uv run --with mido --with python-rtmidi python run/midi_probe.py` and move the wheel — no `pitchwheel` line means the controller is not sending it (check its pitch-wheel assignment/MIDI channel). **3)** The probe prints `pitchwheel` but the bridge does not: the `channel` in `config/lead_input_config.json` is filtering it (0 = omni). **4)** The log says `sent pitch bend …` but you hear nothing: the Lead track/instrument is not applying bend — the track must be MPE-enabled (or have its own bend range set) with a **48 st** range, and the instrument must follow pitch bend; the same range is in `config/lead_config.json`. The GUI status line shows the received wheel (`bend +NN ¢ - mod N`) at ~10 Hz while you move it |
| The mod wheel does nothing | The rig sends literal CC 1 on Bus 5. Watch the log for `[LEAD-IN] wheel: CC 1 (mod) … received` (one line) and the status line's `mod N`. If the instrument maps a different CC (MPE timbre is 74), remap it in Ableton |
| The mod wheel does nothing | CC 1 is forwarded literally. If the instrument maps a different CC (e.g. MPE timbre 74), remap it in Ableton; the rig sends only CC 1 |
| A recorded take has no bend/mod movement | The wheel must move while the note is held during the take (a take keeps the movement from its raw note-on). The log reports `N bend curves, M mod curves` |
| A take stops early / `recording aborted (transport stop)` | The transport stopped mid-take. The notes played so far are kept and loaded; press Play to hear/continue |
| `Revert` is greyed out | There is no played line (`source` is `generated`). Record or load a melody first |
| `Save` is greyed out | Type a melody name; a played take is required (record or load first) |
| `[LEAD-IN] melody load … meter … differs` | The saved melody was made in another meter; it still plays, but its bars will not line up with the bridge bars. Re-save it in the current meter |
| A melody is gone after a restart | Melodies live in `melodies/` (or `--melody-dir`). Check `[LEAD-IN] melody dir=… melodies=N` at bridge start and the `--melody-dir` path |
| `Melody` combo is empty | No saved melodies, or the bridge has not replied yet. Press `Refresh`/`Load` after `/ji/lead/melody/list`; check `[LEAD-IN] melody dir=…` |
| Editing a melody name plays notes | The QWERTY handler ignores keys while a `QLineEdit`/`QComboBox`/spin box has focus; if it still happens, click outside the box first (a stale GUI is the usual cause) |
| There is no `Live` checkbox | Correct from 9.2: the input always overrides. A stale GUI may still send the old toggle; the bridge treats it as monitor on/off only |
| Played notes are stuck after closing the GUI | Closing the GUI does not stop the bridge; but every exit path (source change, Lead off, bridge stop, port loss) sends note-off + CC 123 and clears the override. If a note hangs, restart the bridge |
| A state recall loses the played line | States saved before 9.1 have no inline notes and load as a generated line. Re-save the state after recording; the new `lead.notes` keys are self-contained |
| The Fretboard group is missing after an update | A stale GUI process is still running. Close it and restart `run/ji_station.py` (closing the window does **not** stop the bridge) |

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

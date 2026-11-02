# JI Station — Stage 6 MPE MIDI (Anchor Drone + Set Arp)

**Status:** implemented and **owner-confirmed 2026-10-30** — both IAC buses,
calibration, the drone/arp sound, and no-stuck-note behavior were verified by
the human; the existing four-channel audio rig was unchanged. The agent's pure
Python harness passes without `mido`, but the human report is the authority.
**Stage 7/7.1 is implemented** (see `README_stage7.md` and design doc §8.8).
**Stage 8.1** adds a separate, non-MPE drum output on `IAC Driver Bus 3`,
channel 10 (see `README_stage8.md`); it does not change either MPE bus.
**Stage 8.3a** adds the bass on Bus 4 (see `README_stage8_3.md`); **Stage 8.3b**
makes the arp parameters live (`/ji/arp/*`, a compact GUI group) without changing
the default sound or either MPE contract (see `README_stage8_3b.md`);
**Stage 8.3c** adds arp beat sync + self-evolution (see `README_stage8_3c.md`);
**Stage 8.3d** adds a third MPE bus for the lead/melody voice and pad modes for
the drone (see `README_stage8_3d.md`).

Stage 6 is MIDI-only. It adds **no SuperCollider SynthDef, no audio bus, and no
new OSC address**. The existing Stage 5.5 audio rig and the
`/ji/freqset/update` payload are unchanged.

```text
MutationEngine
  ├─ /ji/freqset/update → sclang (existing Stage 3/5.5 path, unchanged)
  └─ MIDI rig
       ├─ DroneRig (hold | pulse | bend) → IAC Bus 1 → Ableton "Drone"
       ├─ SetArp                          → IAC Bus 2 → Ableton "Arp"
       ├─ DrumRig (optional)              → IAC Bus 3 → Ableton "Drums"
       ├─ BassRig (optional)              → IAC Bus 4 → Ableton "Bass"/"Sub"
       └─ LeadRig (optional, --lead)      → IAC Bus 5 → Ableton "Lead"
```

The two MIDI outputs are **IAC buses/ports**, not audio buses or MIDI channels.
Both are monophonic MPE streams and both use MIDI member channel 2 (mido channel
index 1); the MPE master channel 1 is not used for note data. One member channel
per bus is sufficient because each stream is monophonic.

---

## 1. Files

```text
ji-live/
├── control/
│   ├── mpe_output.py     NEW: MPE pitch math, port adapter, note/bend messages
│   ├── drone.py          NEW: AnchorDrone
│   ├── arp.py            NEW: SetArp + two-group generator
│   ├── midi_rig.py       NEW: MIDIRig orchestrator + config validation
│   └── ji_bridge.py      MODIFIED: optional --midi / --mpe-config
├── config/
│   └── mpe_config.json   NEW: buses, channels, bend range, timing, seed
├── tests/test_mpe.py     NEW: pure tests + --ports/--calibrate/--run
└── docs/README_mpe.md    NEW: this file
```

No accepted `.scd` file, `control/mutation.py`, `control/osc_server.py`,
`tests/test_mutation.py`, or `tests/test_mutation_controls.py` was changed.

---

## 2. What the two voices do

### 2.1 Anchor drone — IAC Driver Bus 1

The drone holds one note at the current anchor pitch class:

```text
frequency = engine.anchor_ratio * engine.fundamental
```

It deliberately does **not** use the analysis voicing octave. If the anchor is
missing, it uses `current_ratios[0]` and logs a warning. The MPE helper only
transposes by whole octaves if the frequency is outside MIDI note range.

On the first set it sends pitch bend, then note-on, then holds. On every set
update:

- anchor frequency unchanged → send nothing; no retrigger;
- same MIDI note, different bend → send only a pitch bend;
- different MIDI note → note-off old note, then pitch bend + note-on new note.

On stop it sends note-off and CC 123 (All Notes Off).

### 2.2 Set arp — IAC Driver Bus 2

On every set update the arp regenerates two three-note groups:

```text
A = [shared, a1, a2]
B = [shared, b1, b2]
```

- `shared` defaults to the current `anchor_ratio` if it is in the set, otherwise
  a random class (`arp_shared_mode`: `"anchor"` or `"random"`).
- The shared note is the **same ratio, frequency, and octave offset** in A and
  B. It is constructed once and reused.
- At set sizes 5–12, the four non-shared slots are four distinct classes, so A
  and B share exactly one ratio. Below 5 classes the non-shared pool is reused
  gracefully; the arp still runs, but exact-one-shared/four-distinct is only
  guaranteed at sizes ≥ 5.
- Default octave offsets are `[0, 1, 2]`: shared at octave 0, the two
  non-shared notes at +1 and +2 octaves, in both groups.

Playback is a fixed six-step loop:

```text
A1, A2, A3, B1, B2, B3, A1, A2, A3, B1, B2, B3, ...
```

Each step note-offs the previous arp note (if still held), pitch-bends to the
new frequency, then note-ons. Default timing is **0.25 s per step, 0.20 s gate**
— a placeholder; rhythm, swing, tempo sync, and sequencing are deferred.

A set update regenerates the pattern and applies it at the next step boundary.
It does not restart the clock or cut a held note. The RNG is a dedicated
`random.Random(arp_seed)`, so the same seed reproduces the arp.

A regeneration logs, for example:

```text
[ARP] shared=3/2 (329.63 Hz)  A=[3/2, 1/1, 5/4]  B=[3/2, 5/3, 15/8]
```

### 2.3 Live arp parameters (Stage 8.3b, awaiting human acceptance)

The Stage 6 defaults above are unchanged, but the following are now editable
while the rig runs — from the compact **Arp** GUI group, or by OSC
(`/ji/arp/set_*`) to the bridge:

| Control | Range | Notes |
|---|---|---|
| Step seconds | 0.05–2.0 | **free-run**, not tempo-synced (no BPM division, no swing) |
| Gate seconds | 0.01–2.0 | clamped to ≤ step (logged); does not cut the held note |
| Velocity | 1–127 | note-on velocity |
| Octave offsets | three ints −8..+8 | positional `(shared, non_shared_1, non_shared_2)`; the shared offset is identical in A and B |
| Shared mode | `anchor` / `random` | the Stage 6 shared-note rule |
| Order | `forward` / `reverse` / `pingpong` / `random` | `0,1,2,3,4,5` / `5,4,3,2,1,0` / `0,1,2,3,4,5,4,3,2,1` / seeded permutation |
| Seed | 0..2³¹−1 | same seed + same pattern reproduces the pattern and the random order |

`config/mpe_config.json` gains one additive key, `"arp_order": "forward"`; a
config without it loads as `forward`. Changing step/gate/velocity applies from
the next trigger; octave/shared/seed rebuild the pattern from the last set
snapshot (no mutation needed) and order rebuilds the sequence; every change
resets the cursor to the first element of the order and never cuts the held
note.

While the transport runs, arp edits wait for the next quarter beat
(`PHASE_ARP`, after bass and before the MIDI enables); while it is stopped they
apply immediately. The Stage 7.1 Drone/Arp switches remain the only
enable/disable controls. `/ji/arp/state` carries `enabled, port_ok, step, gate,
velocity, three octaves, shared mode, order, seed`.

### 2.4 Beat sync and self-evolution (Stage 8.3c, awaiting human acceptance)

Two optional behaviors, both **off by default** so the free-running arp above is
unchanged:

- **Beat sync.** `sync_mode` is `free` (Stage 6/8.3b) or `beat`. In `beat` the
  step is a division of the bridge's quarter-note BPM — `1/4`, `1/8`, `1/16` or
  `1/8T` — and the next step snaps to that division boundary on the transport
  grid. `1/8` at 120 BPM is exactly the free default of 0.25 s. The gate stays
  in seconds and is clamped to the derived step. The bridge owns the phase: it
  snaps on sync-on, division change, transport start, and when the Drone/Arp
  switch re-enables the arp. A tempo change keeps the phase and only changes the
  step duration. With the transport stopped, beat mode keeps running at the
  current BPM (the Drone/Arp switch is still the only mute).
- **Self-evolution.** `evolve` regenerates the pattern from the current set
  every `evolve_beats` quarter beats (1–64), with no mutation. `evolve_depth`
  0 re-picks only the four non-shared notes; higher depth may also let the
  shared note move (probability = depth) and nudge one octave offset by ±1
  (probability = depth/2). The frequency set itself never changes. The seed
  reproduces the evolution sequence.

Config keys (additive, all optional): `arp_sync`, `arp_division`, `arp_evolve`,
`arp_evolve_beats`, `arp_evolve_depth`. Runtime: `/ji/arp/set_sync`,
`set_division`, `set_evolve`, `set_evolve_beats`, `set_evolve_depth`, and the
`/ji/arp/state` payload grows to 17 values (effective step, sync mode,
division, evolve, evolve beats, evolve depth). See `README_stage8_3c.md`.

Swing, rests/probability, a different group size, polyphony, and any change to
the 48-semitone bend range stay out of scope.

---

### 2.3 Stage 8.3d: the lead bus and the drone modes

**Lead (`IAC Driver Bus 5`, optional).** A monophonic JI melody voice using the
same MPE contract as the drone/arp: member channel 2, 48-semitone bend range.
It has its own config (`config/lead_config.json`) and its own flag (`--lead`,
**off** by default), so `--midi` without `--lead` never opens Bus 5. A missing
Bus 5 prints one `[LEAD] disabled: ...; drone/arp/drums/bass continue` line and
leaves everything else running.

Because Bus 5 is a separate port, it is a ready FX insert point: route it to an
Ableton **Lead** track with any instrument and any chain. The lead is
transport-gated (starts on a transport start at phrase step 0, stops with
note-off + CC 123) and groove-linked (the drum groove's swing is pushed once per
change).

**Drone modes (same Bus 1, same member channel 2, 48 st).**

| Mode | Behavior |
|---|---|
| `hold` | Stage 6, unchanged: one sustained anchor note, pitch-bend-only retunes, no retrigger while the frequency is unchanged |
| `pulse` | a repeating pattern of N notes on the anchor pitch class, seeded octave offsets within the chosen span, `1/4`/`1/8`/`1/16` grid + gate; monophonic, transport-gated |
| `sequence` | a repeating figure that walks the current set from the anchor and back (`0,+1,…,peak,…,0` in set steps, octave-wrapping past the top), N = 2–8, each note held for 1/4, 1/2, 1, 2 or 4 **bars** with a gate as a fraction of the note; it follows mutations, tempo and meter; monophonic, transport-gated |
| `bend` | one held note, then a continuous pitch slide of 1–12 semitones (down or up) after the hold; note-off, rest, repeat. The cycle is `ceil(hold_beats + slide_seconds × bpm / 60)` quarter beats |

`bend` does not retrigger the note to slide — it is one note whose pitch bend is
interpolated at ~50 updates/s and clamped to the 14-bit range. **The receiving
instrument must keep the 48-semitone range**; a 12-semitone slide uses a quarter
of it. `hold` never becomes transport-gated (that would regress Stage 6/7.1).

The drone bus itself is still `mpe_config.json`'s `drone_port`; the new
`config/drone_config.json` owns only the mode and its parameters, and `hold`
remains the shipped default, so an old rig behaves exactly as in 8.3c.

## 3. Configuration

`config/mpe_config.json`:

```json
{
  "drone_port": "IAC Driver Bus 1",
  "arp_port": "IAC Driver Bus 2",
  "member_channel": 2,
  "master_channel": 1,
  "bend_range_semitones": 48,
  "drone_velocity": 90,
  "arp_velocity": 90,
  "step_seconds": 0.25,
  "gate_seconds": 0.20,
  "arp_octave_offsets": [0, 1, 2],
  "arp_shared_mode": "anchor",
  "arp_order": "forward",
  "arp_sync": "free",
  "arp_division": "1/8",
  "arp_evolve": false,
  "arp_evolve_beats": 4,
  "arp_evolve_depth": 0.0,
  "arp_seed": 1
}
```

| Field | Meaning |
|---|---|
| `drone_port` / `arp_port` | Exact mido output names. Must be the two IAC buses. |
| `member_channel` | **1-based** MIDI member channel. Mido receives `member_channel - 1`. Default 2. |
| `master_channel` | 1-based MPE master channel; not used for note data. |
| `bend_range_semitones` | Must equal the instrument's MPE pitch-bend range. Ableton's rig uses **48**. |
| `drone_velocity` / `arp_velocity` | Note-on velocity, 1–127. |
| `step_seconds` / `gate_seconds` | Fixed arp timing; gate must be ≤ step. |
| `arp_octave_offsets` | Positional `(shared, non_shared_1, non_shared_2)` octave offsets. |
| `arp_shared_mode` | `"anchor"` or `"random"`. |
| `arp_seed` | Dedicated RNG seed for reproducible arps. |

If either exact port name is missing, the code fails with the available MIDI
output list. It never silently substitutes the other bus.

**Ableton's MPE pitch-bend range is 48 semitones for this rig.** A mismatch
between `bend_range_semitones` and the instrument makes every JI pitch wrong.
If the instrument is not MPE-capable it will play 12-TET; do not use it.

---

## 4. Human setup: IAC Driver (macOS)

1. Open **Audio MIDI Setup** (`/Applications/Utilities`).
2. **Window → Show MIDI Studio**.
3. Double-click **IAC Driver**.
4. Check **Device is online**.
5. If the list shows only **IAC Driver Bus 1**, click **+** to add a second bus.
   Rename/confirm the buses are exactly:
   - `IAC Driver Bus 1`
   - `IAC Driver Bus 2`
6. Confirm both appear:

   ```bash
   cd ji-live
   uv run --with mido --with python-rtmidi python tests/test_mpe.py --ports
   ```

   The command prints `mido.get_output_names()` and fails if either configured
   name is absent.

---

## 5. Human setup: Ableton Live

1. **Live → Preferences → Link/Tempo/MIDI**.
2. In the MIDI Ports table, enable **Track** for both **IAC Driver Bus 1** and
   **IAC Driver Bus 2**. (Leave Remote/Sync off unless you need them.)
3. Create two **MIDI tracks**:

   | Track | Name | MIDI From | Monitor | Instrument |
   |---|---|---|---|---|
   | 1 | `Drone` | IAC Driver Bus 1 | **In** | MPE-capable synth |
   | 2 | `Arp` | IAC Driver Bus 2 | **In** | MPE-capable synth |

   **Important:** in the **MIDI From channel selector** below each input, choose
   **MPE** (Live 11+) or **All Channels**. Do **not** leave either track on
   **Ch. 1**. The rig sends note data on MIDI **member channel 2**; channel 1 is
   the MPE master channel and is deliberately unused. A track filtered to
   Ch. 1 receives no notes even when the IAC port is correct.

4. On both instruments, enable **MPE** and set the **MPE pitch-bend range** to
   **48 semitones** (or the exact `bend_range_semitones` in the config).
   - In Live 11+, the track's MPE MIDI effect exposes the range.
   - Also check the synth's own pitch-bend range parameter.
5. Set both tracks' **Audio To** to **Master** (or to the same monitor path as
   the existing four audio tracks).
6. Keep the existing four audio tracks (BlackHole 16ch inputs 1–4) and the
   MiniFuse 2 monitoring path unchanged. The MIDI tracks add sound; they do not
   replace the audio tracks.
7. Do not route the master back to BlackHole.

If an instrument has no MPE mode, it will receive ordinary notes and ignore the
per-note bend; pitches will be 12-TET. Use an MPE-capable instrument.

---

## 6. Commands

### 6.1 Pure tests — no mido, no hardware

```bash
cd ji-live
python tests/test_mpe.py --pure
```

Covers pitch round-trips, 440/220/275 Hz, octave transposition, group
generation, the A/B sequence, drone retrigger rules, stop/All Notes Off, the
MIDI rig with fake ports, and the config. Prints `ALL MPE PURE TESTS PASSED`
and exits 0; any failure exits nonzero.

### 6.2 Port check

```bash
cd ji-live
uv run --with mido --with python-rtmidi python tests/test_mpe.py --ports
```

Prints `mido.get_output_names()` and verifies both configured names.

### 6.3 Calibration

```bash
cd ji-live
uv run --with mido --with python-rtmidi python tests/test_mpe.py --calibrate
```

Sends **note 60** (C4, 261.63 Hz) with centered bend on each bus for about one
second, one bus after the other. Both Ableton tracks should show MIDI activity
and play C4. If one is silent, fix its **MIDI From**, **Monitor = In**, and
instrument first.

### 6.4 Standalone drone + arp

```bash
cd ji-live
uv run --with mido --with python-rtmidi python tests/test_mpe.py --run 15
```

Starts the drone at anchor `1/1` (220 Hz) and the arp from the Stage 5.5
initial set for 15 seconds, then stops both buses. Use this to confirm sound and
pitch before involving the audio bridge.

### 6.5 Bridge with MIDI

MIDI is **off by default**; the existing invocation is unchanged:

```bash
uv run --with python-osc python control/ji_bridge.py --interactive --glide-time 0.15
```

Stage 6 product invocation:

```bash
uv run --with python-osc --with mido --with python-rtmidi \
  python control/ji_bridge.py --interactive --midi --glide-time 0.15
```

Optional:

```bash
--mpe-config path/to/mpe_config.json
```

Every `/ji/freqset/update` send (initial set and each mutation) also hands the
same snapshot to the MIDI rig. The bridge's OSC payload is unchanged.

**Stage 7.1 UI switches:** when the GUI is running, the Status group's
**Drone on** / **Arp on** checkboxes send `/ji/midi/set_enabled [drone, arp]` to
the bridge. The bridge mutes or restarts one bus without closing either port.
Toggling Drone off/on is also the quickest way to retrigger a drone note that
was missed because the Drone track was not ready at bridge start.

---

## 7. Expected diagnostics

```text
[MIDI] drone port=IAC Driver Bus 1 channel=2 bend_range=48
[MIDI] arp port=IAC Driver Bus 2 channel=2 step=0.25s gate=0.20s
[DRONE] anchor=1.000000 freq=220.00 note=57 bend=0 (start)
[ARP] shared=1/1 (220.00 Hz)  A=[1/1, 5/4, 5/3]  B=[1/1, 9/8, 15/8]
[ARP] step=1 freq=220.00 note=57 bend=0
[ARP] step=2 freq=550.00 note=73 bend=-23
...
[BRIDGE] -> /ji/freqset/update classes=7 glide=0.000 fundamental=220.0
```

When the anchor changes, the drone prints `(retune)` for a bend-only change or
`(retrigger)` for a new MIDI note. An unchanged anchor prints nothing and does
not retrigger.

On `q` / Ctrl+C the bridge stops the arp thread, sends note-off/CC 123 on both
buses, and closes both ports.

---

## 8. Calibration / pitch verification

1. Run `tests/test_mpe.py --calibrate`; confirm both tracks play C4.
2. Run `tests/test_mpe.py --run 30` with a tuner on the Drone track. At anchor
   `1/1` it should read ~220 Hz (A3, MIDI note 57).
3. Confirm a bent JI pitch. For example, 275 Hz (5/4 above A4, or the 5/4 class)
   should sound at **275 Hz**, not 277.18 Hz (C#4) and not 261.63 Hz (C4).
   `tests/test_mpe.py --pure` shows the expected `note=61 bend=-23` at 48 semitones.
4. If the tuner is consistently sharp/flat by a fixed ratio, the instrument's
   MPE pitch-bend range does not match `bend_range_semitones`. Fix the Ableton
   instrument/MPE range or the config and repeat.
5. If pitches are correct but the note is an octave off, check the instrument's
   octave setting; the code transposes only when the requested frequency is
   outside MIDI range.

Record the verified value in `config/mpe_config.json`.

---

## 9. Failure modes

| Symptom | Likely cause | Fix |
|---|---|---|
| `MIDI output 'IAC Driver Bus 2' not found` | Only Bus 1 exists, or the name differs | Audio MIDI Setup → IAC Driver → add bus 2 / correct the name |
| `mido is required` | Invocation missing `--with mido` | Add `--with mido --with python-rtmidi` |
| `could not enumerate MIDI outputs` | `python-rtmidi` missing/broken | Install `python-rtmidi`; rerun `--ports` |
| No MIDI activity in Ableton | Track not enabled in Link/Tempo/MIDI, or wrong MIDI From | Enable **Track** for both IAC buses; set each track's input to its bus |
| One MIDI track silent while the other works | MIDI From channel filter left on **Ch. 1**; both tracks on the same bus; Monitor Auto without arm; instrument not loaded | Set the channel selector to **MPE**/All Channels; Drone = Bus 1, Arp = Bus 2; Monitor In; check the instrument |
| Arp works, drone silent | Drone was not ready when the bridge started (it sends one held note, not a repeating sequence); or the Drone track input/filter is wrong | In the Stage 7 UI, toggle **Drone off/on** to retrigger; confirm the bridge printed `[DRONE] ... (start)` and the Drone track MIDI From is `IAC Driver Bus 1` with All/MPE channels and Monitor In |
| Notes play but all pitches are 12-TET | Instrument/track not MPE, or MPE disabled | Use an MPE-capable instrument and enable MPE |
| All pitches sharp/flat by a fixed amount | MPE bend-range mismatch | Set instrument/MPE range to 48 st (or match config) |
| Drone retriggers on every mutation | Anchor changed to a different MIDI note, or an old build | Check `[DRONE] ... (retrigger)`; unchanged anchor must print nothing |
| Arp has two shared notes / not exactly one | Set has fewer than five classes | Expected below 5; at ≥5 the four non-shared slots are distinct |
| Stuck notes after quit | Bridge killed without `stop()` | Re-run and quit with `q`/Ctrl+C; CC 123 is sent on both buses |
| No sound but MIDI meters move | Synth track muted / instrument not loaded | Check instrument, track volume, master, monitor path |

---

## 9.1 Failure modes added by 8.3d

| Symptom | Cause / fix |
|---|---|
| `[LEAD] disabled: IAC Driver Bus 5 not available ...` | `--lead` is on but Bus 5 (or a valid `config/lead_config.json`) is missing. Everything else keeps running; create the bus or drop `--lead` |
| Lead group disabled in the GUI | Same: the bridge reports `port_ok = 0` for Bus 5 |
| Drone modes greyed out | Bus 1 is not open, or the mode is `hold` (both pad rows are inert there by design) |
| The `bend` slide is wrong by a fixed ratio | The receiving track is not set to the 48-semitone MPE range |

## 10. Limitations

- Stage 6 is a **MIDI connectivity and pitch test**, not a rhythm system.
  Timing is a fixed 0.25 s step / 0.20 s gate; no tempo sync, swing, or
  sequencing.
- No MIDI mutation mapping and no new OSC addresses.
- No polyphonic MPE voice allocation. Both buses are monophonic and use one
  member channel. Polyphonic MPE is deferred.
- The arp's exact-one-shared/four-distinct property is guaranteed only for set
  sizes ≥ 5.
- MIDI and Ableton acceptance cannot be tested in the agent environment. Only
  pure Python tests were run there.

---

## 11. Human acceptance checklist (completed 2026-10-30)

1. `mido.get_output_names()` lists both `IAC Driver Bus 1` and
   `IAC Driver Bus 2`; `tests/test_mpe.py --ports` passes.
2. Ableton has the `Drone` and `Arp` MIDI tracks, both receive notes, and both
   instruments have MPE enabled with a 48-semitone bend range.
3. `tests/test_mpe.py --calibrate` produces C4 on both tracks.
4. Start the Stage 5.5 live rig, then
   `python control/ji_bridge.py --interactive --midi --glide-time 0.15`.
5. The drone plays a constant anchor tone; an unchanged anchor does not
   retrigger; a changed anchor retunes (bend-only when possible).
6. The arp alternates two three-note groups, shares exactly one frequency, and
   uses the +1/+2 octave spread.
7. Press `m`: the set updates; the drone follows the anchor; the arp regenerates
   from the new set and keeps the shared-frequency property.
8. Quit the bridge: both MIDI tracks stop, with no stuck notes.
9. The existing four-channel audio rig and `/ji/freqset/update` behavior are
   unchanged.
10. **Owner-confirmed 2026-10-30:** the drone and arp were heard through
    Ableton, MIDI connectivity/calibration was accepted, and no stuck notes were
    observed. The existing four-channel audio rig and `/ji/freqset/update`
    behavior were unchanged. **Stage 6 is complete; the next feature is Stage 7,
    the minimal performance GUI.**

---

## 12. Verification available in the agent environment

The agent environment has no macOS IAC Driver, no Ableton, and no MIDI hardware.
The agent ran only the pure tests:

```bash
cd ji-live
python tests/test_mpe.py --pure
```

and re-ran the existing Stage 2/3.5 harnesses without mido:

```bash
uv run --with python-osc python tests/test_mutation.py
uv run --with python-osc python tests/test_mutation_controls.py
```

All passed. The agent environment did not verify MIDI, IAC, Ableton, the MPE
bend range, or audio; the human verified those on 2026-10-30 and Stage 6 is
owner-confirmed. Stage 7 is the next feature.

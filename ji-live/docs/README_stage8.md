# JI Station — Stage 8.1 Rhythm Foundation + Drum Engine v1

**Status:** **human-tested and accepted as part of the Stage 8.2 acceptance
run on 2026-10-31.** The owner confirmed the live audio/MIDI/GUI rig, including
transport, drums, free/beat loops, and the beat-mode loop fix. The agent's own
verification remains limited to the pure Python harnesses.

Stage 8.1 adds a shared tempo/meter/transport foundation, beat-locked (or free)
loops, and the first new MIDI voice: a drum engine on an optional third IAC bus.
It adds **no new audio DSP** and does not change the Stage 5.5 four-channel map,
the Stage 6 drone/arp, the Stage 7 GUI controls, or `/ji/freqset/update`.

**Next:** Stage 8.2 (groove generation, per-step grid, swing, probability,
and beat-quantized state changes) is human-accepted; see
[README_stage8_2.md](README_stage8_2.md). Stage 8.3a is the bass voice.

---

## 1. What Stage 8.1 adds

1. **Transport, tempo, and meter** in the GUI. The Python bridge owns the
   canonical rhythm state (quarter-note BPM 20–300, meter 1–16 over
   1/2/4/8/16, transport 0/1) and broadcasts it to sclang.
2. **Drum MIDI output** on `IAC Driver Bus 3`, channel 10, opt-in with
   `--drums`. The default groove is four-on-the-floor at 120 BPM, 4/4.
3. **16 lane toggles**, a drum enable switch, and half/normal/double time.
4. **Free or beat-locked loops** with per-loop lengths (seconds or beats), so
   loops can phase against each other.
5. **Status** for tempo/meter/transport, drum port/rate/lane mask, per-loop
   mode/length, and the existing loop states.
6. **A compact layout** with the Drone/Arp switches in a visible top-row
   **MIDI** group, one-row autotune channels, denser loop/rhythm groups, and a
   scroll area as a safety net on small screens.

Still out of scope: groove presets, density, syncopation, swing, probability, a
per-step drum grid, bass/lead voices, sequencing/pattern mutation, save/recall,
more than four loops, overdub, per-loop routing, loop-player autotune,
time-stretching an existing buffer, and tempo-syncing the Stage 6 arp.

### Files

| File | Status | Purpose |
|---|---|---|
| `control/rhythm.py` | new | pure rhythm state, validation, bar/beat/step math, `BeatClock`, quantization helpers |
| `control/drums.py` | new | drum config, 16-lane map, default pattern, lane/rate state, lazy MIDI output, scheduler, `DrumRig` |
| `control/midi_rig.py` | modified additively | optional third drum output; `midi=False` drums-only rig |
| `control/ji_bridge.py` | modified additively | canonical rhythm state, `--drums`/`--rhythm-config`, `/ji/rhythm/*`, `/ji/drums/*` status |
| `control/osc_server.py` | modified additively | rhythm/drum OSC handlers and callbacks |
| `ui/osc_control.py` | modified additively | builders, parsers, `RhythmStatus`/`DrumStatus`/`LoopConfig`, store fields |
| `ui/ji_ui.py` | modified additively | Rhythm group, per-loop Free/Beat + length, new status lines |
| `ui/control_surface.scd` | modified additively | `TempoClock`, beat quantization, per-loop mode/beats/free length, `/ji/loop/config`, tempo resync request |
| `config/rhythm_config.json` | new | drum port/channel/note map/defaults |
| `tests/test_rhythm.py` | new | pure rhythm/drum tests with a fake output |
| `tests/test_ui.py` | modified additively | rhythm/drum messages, bridge state, Rhythm group (optional Qt) |
| `tests/sc/test_rhythm.scd` | new (optional) | human-run sclang quantization/loop check |
| `docs/README_stage8.md` | new | this document |
| `README.md`, `docs/design_doc_10.28.10.50` | updated | status, OSC map, changelog |

Not modified: `synthdefs/*`, `startup.scd` audio behavior,
`tests/sc/test_live_rig.scd` audio DSP, `tests/sc/test_dual_loop.scd`,
`tests/sc/test_ableton_routing.scd`, `control/mutation.py`, the
`/ji/freqset/update` payload/receiver, and the Stage 5.5 routing/loop-length
semantics.

---

## 2. Requirements and setup

### 2.1 Audio/MIDI rig

- The accepted Stage 5.5 rig: MiniFuse 2 input, BlackHole 16ch output, 2 in /
  16 out, 48 kHz, blockSize 64.
- The Stage 6 rig if drone/arp is wanted: `IAC Driver Bus 1` / `Bus 2`, member
  channel 2, 48-semitone MPE bend range.
- Python 3.11+ with `python-osc`; add `mido` + `python-rtmidi` for MIDI.
- PyQt6 for the window.

### 2.2 IAC Bus 3 (drums)

1. **Audio MIDI Setup → Window → Show MIDI Studio**.
2. Double-click **IAC Driver**, check **Device is online**.
3. Click **+** until a third bus exists; rename it exactly
   **`IAC Driver Bus 3`**.
4. Confirm the bus is listed:

   ```bash
   cd ji-live
   uv run --with mido --with python-rtmidi python -c \
     "import mido; print(mido.get_output_names())"
   ```

The bridge is opt-in: `--midi` alone never opens Bus 3. With `--drums`, a
missing Bus 3 prints `[MIDI] drums disabled: ...; drone/arp continue` and the
drone, arp, and audio rig keep running.

### 2.3 Ableton drum track

1. **Live → Preferences → Link/Tempo/MIDI**: enable **Track** for
   `IAC Driver Bus 3`.
2. Create a **MIDI track** named `Drums`:
   - **MIDI From**: `IAC Driver Bus 3`, channel **10** (or All Channels).
   - **Monitor**: **In**.
   - **Instrument**: Drum Rack (or any GM-style drum instrument).
3. **MIDI octave convention:** the rig sends MIDI note **36** for kick, 37 for
   kick 2, …, 51 for cowbell. In the standard numbering used here, **C1 = 36**.
   Ableton's Drum Rack may display the same MIDI number as **C3** (Ableton's
   octave convention). Map pads by **MIDI number**, not by the octave label:

   | MIDI | Lane | Ableton may label |
   |---|---|---|
   | 36 | kick1 | C1 or C3 |
   | 37 | kick2 | C#1 or C#3 |
   | 38 | snare1 | D1 or D3 |
   | 39 | snare2 | D#1 or D#3 |
   | 40–51 | perc1–perc12 | E1…D#2 or E3…D#4 |

   Load a Drum Rack and make sure pads 36–51 are populated, or use a GM drum
   kit. If the kick is on the wrong pad, check the octave convention first.
4. Set the track **Audio To → Master** (or the same monitor path as the four
   audio tracks). Do not route the master back to BlackHole.

### 2.4 Configuration

`config/rhythm_config.json` ships (abbreviated lane list):

```json
{
  "port": "IAC Driver Bus 3",
  "channel": 10,
  "velocity": 100,
  "gate_seconds": 0.05,
  "enabled": true,
  "rate": 1.0,
  "lane_mask": 69,
  "bpm": 120,
  "meter": [4, 4],
  "lanes": [
    {"note": 36, "name": "kick1", "label": "Kick 1", "role": "four-on-the-floor kick"},
    ...
    {"note": 51, "name": "perc12", "label": "Perc 12", "role": "cowbell"}
  ]
}
```

| Field | Meaning |
|---|---|
| `port` | Exact mido output name; must be the third IAC bus. |
| `channel` | **1-based** MIDI channel. 10 = mido index 9 (the GM drum channel). |
| `velocity` | Note-on velocity 1–127. |
| `gate_seconds` | Note-on duration; capped at 80% of a step at fast tempos. |
| `enabled` | Initial drum enable state (the GUI can toggle it). |
| `rate` | 0.5 (half), 1.0 (normal), or 2.0 (double). |
| `lane_mask` | 16-bit mask; **bit 0 = lane 1 / note 36, bit 15 = lane 16 / note 51**. `69` = `0x0045` = kick1 + snare1 + perc3. |
| `bpm`, `meter` | Initial rhythm defaults when drums are configured. |
| `lanes` | Exactly 16 lane definitions (`note`, `name`, `label`, optional `role`). Notes/names must be unique. |

The UI's lane labels use the same default 16-lane list. If you change the
config notes, update the labels in `ui/osc_control.py` (`DRUM_LANE_DEFS`) or
accept that the GUI shows the default labels; lane-mask bit order follows the
list position in both places.

---

## 3. Start order

Run everything from `ji-live/`.

1. Evaluate `startup.scd`.
2. Evaluate `tests/sc/test_live_rig.scd`.
3. Start the bridge (drums opt-in):

   ```bash
   uv run --with python-osc --with mido --with python-rtmidi \
     python control/ji_bridge.py --interactive --midi --drums --glide-time 0.15
   ```

   Drums without the MPE buses (Bus 3 only):

   ```bash
   uv run --with python-osc --with mido --with python-rtmidi \
     python control/ji_bridge.py --interactive --drums --glide-time 0.15
   ```

   Existing Stage 6/7 invocation without drums is unchanged:

   ```bash
   uv run --with python-osc --with mido --with python-rtmidi \
     python control/ji_bridge.py --interactive --midi --glide-time 0.15
   ```

4. Evaluate `ui/control_surface.scd` **after** `tests/sc/test_live_rig.scd`.
   It prints `[UI] control surface installed -> ...; autotune + loop + rhythm +
   status OSC ready` and sends `/ji/rhythm/request` so the bridge re-broadcasts
   the current tempo even though the bridge started earlier.
5. Launch the GUI:

   ```bash
   uv run --with python-osc --with PyQt6 python ui/ji_ui.py
   ```

6. Perform. Closing the window leaves the bridge, audio, loops, and MIDI
   running.

If `tests/sc/test_live_rig.scd` is re-evaluated, re-evaluate
`ui/control_surface.scd` afterward.

If the bridge listens on a non-default control port, set
`~ji_bridge_host = "127.0.0.1"; ~ji_bridge_port = <port>;` before evaluating
`ui/control_surface.scd`; the install-time `/ji/rhythm/request` and the
**Resync** button use that address.

### CLI

| Flag | Default | Meaning |
|---|---|---|
| `--midi` | off | Stage 6 MPE drone/arp buses |
| `--mpe-config` | `config/mpe_config.json` | Stage 6 config path |
| `--drums` | off | Stage 8.1 drum output (works with or without `--midi`) |
| `--rhythm-config` | `config/rhythm_config.json` | drum/rhythm config path |

---

## 4. Controls

### 4.1 Rhythm group (UI → bridge, port 9000)

| Control | OSC | Range/default |
|---|---|---|
| Play/Stop | `/ji/rhythm/transport` | `int 1/0` |
| BPM | `/ji/rhythm/set_tempo` | 20–300, default 120 |
| Meter numerator | `/ji/rhythm/set_meter` | 1–16 |
| Meter denominator | `/ji/rhythm/set_meter` | 1/2/4/8/16, default 4 |
| Drums on | `/ji/drums/set_enabled` | `int 0/1` |
| Lane toggle | `/ji/drums/set_lane` | `int midi_note, int 0/1` |
| Rate | `/ji/drums/set_rate` | `0.5`, `1.0`, `2.0` |
| Resync | `/ji/rhythm/request` | no args; asks the bridge to re-broadcast tempo |

BPM knob moves are coalesced (≈30 Hz); buttons and toggles send immediately.
Out-of-range values are clamped by the UI and again by the bridge; an
unsupported denominator or rate is rejected, never silently changed.

Transport **Play** starts only rhythm scheduling: it does not restart the audio
engine, the mutation set, the drone, or the arp. **Stop** stops the drum
scheduler, sends note-offs for any held hit and CC 123 (All Notes Off), and
tells sclang the transport is stopped. It also cancels pending beat-mode
actions and stops any beat-mode recording/playback; free loops keep running.
Beat-mode loops do not auto-resume on the next Play, so press Play again for
each loop after the transport starts.

### 4.2 Loops (UI → sclang, port 57120)

Each loop 1–4 now has:

- **Mode**: `Free` or `Beat`.
- **Length**: seconds (1.0–30.0, default 8.0) in free mode; beats (1–64,
  default 4) in beat mode.

| Action | OSC |
|---|---|
| Set mode | `/ji/loop/set_mode` (`index`, `"free"` or `"beat"`) |
| Set beat length | `/ji/loop/set_beats [index, beats 1..64]` |
| Set free length | `/ji/loop/set_length [index, seconds 1..30]` |
| Record | `/ji/loop/record_length [index, seconds]` (free) or `/ji/loop/record [index]` (beat; sclang computes the take length) |
| Play | `/ji/loop/play [index]` |
| Stop / Stop Rec / Clear / Stop All | unchanged, immediate |

- **Free mode** keeps the Stage 7 behavior: Record/Play happen immediately and
  the take is the displayed seconds length. The global Length knob still acts
  as the default free length; loops whose per-loop length you have not edited
  follow it. Editing a loop's own length stops that loop from following the
  global default.
- **Beat mode** schedules Record/Play at the next beat on sclang's
  `TempoClock`. The take is `beats * 60 / bpm` seconds at record time.
  Stop/Clear/Stop Rec cancel a pending action and act immediately.
  **Fixed in 8.2.2:** beat-mode scheduling previously called `action.value`
  without the loop index, so synced Record/Play fired with `index = nil` and
  did nothing. Re-evaluate `ui/control_surface.scd` after the live rig to pick
  up the one-line fix (`action.value(index)`).
- A beat-mode Record/Play with the transport stopped is rejected by the GUI
  (`Loop N is beat-locked; press Play (transport) first`) and by sclang.
- The beat-scheduled action calls the existing `~ji_record_loop` /
  `~ji_play_loop` at the boundary. Recording still allocates its buffer at that
  moment (`Buffer.alloc` + `s.sync`), so the recorded audio starts a few
  milliseconds after the scheduling beat, exactly as a Stage 7 record does.
  This is not sample-accurate punch-in; it is beat-aligned start.
- Different beat lengths phase because each loop has its own length; loops are
  not restarted together.
- **Tempo changes do not time-stretch an existing buffer.** Beat mode stores a
  recorded buffer whose duration was fixed at record time. To change the
  tempo of an existing loop, stop and re-record it, or use free mode. This is
  a deliberate 8.1 limitation.

### 4.3 Status

| Message | Args | Source |
|---|---|---|
| `/ji/rhythm/state` | `float bpm, int num, int den, int running, int drums_enabled, float drum_rate` | bridge, at start/on hello/after every change |
| `/ji/drums/state` | `int enabled, int port_ok, float rate, int lane_mask` | bridge, at start/on hello/after every change |
| `/ji/loop/config` | `int index, string mode, float beats, float seconds` | sclang, at install/query/after mode/beats/length changes |
| `/ji/loop/state` | `int index, string state` | sclang, unchanged |

The Rhythm group shows the current BPM/meter/transport/drum state and a drum
status line. A missing Bus 3 shows `Drums: disabled (drum port not available; see bridge log)`, disables the drum controls, and does not crash the
window. A missing bridge/sclang still leaves the window usable with waiting
labels.

Lane mask bit order is documented in §2.4: bit 0 = lane 1 (default note 36),
bit 15 = lane 16 (default note 51).

### 4.4 Drum pattern (v1)

The grid is 16th notes. The default pattern for 4/4 is:

- **kick1** (36) on every quarter beat: steps 0, 4, 8, 12;
- **snare1** (38) on quarter beats 2 and 4: steps 4 and 12;
- **perc3 / closed hat** (42) on offbeat 8ths: steps 2, 6, 10, 14;
- every other lane is silent in the 8.1 default pattern.

For non-4/4 meters the pattern is truncated/extended against
`steps_per_bar = numerator * 16 / denominator`:

| Meter | 16th steps/bar | Kick | Snare | Hat |
|---|---|---|---|---|
| 4/4 | 16 | 0,4,8,12 | 4,12 | 2,6,10,14 |
| 3/4 | 12 | 0,4,8 | 4 | 2,6,10 |
| 6/8 | 12 | 0,4,8 | 4 (bar is 3 quarters) | 2,6,10 |
| 5/4 | 20 | 0,4,8,12,16 | 4,12 | 2,6,10,14,18 |
| 7/8 | 14 | 0,4,8,12 | 4,12 (bar is 3.5 quarters) | 2,6,10 |
| 12/8 | 24 | 0,4,…,20 | 4,12 | 2,6,…,22 |

The 16 lane toggles gate the lanes the pattern uses. In 8.1, enabling a lane
that the default pattern never places (for example kick2 or a tom) adds no hits;
per-lane patterns and a per-step grid arrive in 8.2. This is deliberate: 8.1 is
a foundation, not a groove generator.

**Rate** 0.5/1/2 scales the *step duration only*:

- normal: `60 / bpm / 4` per step;
- double: half that duration (faster);
- half: twice that duration (slower).

Tempo and meter state do not change.

**Note-off semantics:** every hit is note-on, then note-off after
`gate_seconds` (default 50 ms, capped at 80% of the step at fast tempos). Stop
also note-offs any held hit and sends CC 123. Drums cannot leave stuck notes.

---

## 5. Timing, clocks, and drift

- The Python bridge is the canonical rhythm state and owns the drum scheduler.
  It uses `time.monotonic()` with drift-corrected deadlines; it does not
  busy-wait or block the Qt event loop.
- sclang sets its `TempoClock` from `/ji/rhythm/tempo` and uses it to quantize
  loop record/play. It is a **separate clock** from Python's drum clock.
- On transport start the bridge resets its step counter to 0; sclang moves its
  quantization origin to the next clock beat. The two clocks are aligned only
  at that point. Over a long session they can drift by a few milliseconds.
- **Resync path:** press **Resync** in the Rhythm group (or re-evaluate
  `ui/control_surface.scd`, which requests a resync on install). The bridge
  re-broadcasts the canonical tempo/meter/transport, so a sclang surface that
  missed a change catches up. While the transport is already running this
  does **not** re-phase the two clocks; to re-anchor both, stop and start the
  transport (Python resets its step counter; sclang moves its beat origin).
  Drift is documented, not hidden.
- **Tempo changes do not time-stretch buffers** (see §4.2).

### Time-signature semantics

BPM is quarter-note based; the denominator changes the bar length, not the
quarter-note beat. `bar_quarters = numerator * 4 / denominator`.

- 4/4: 4 quarters/bar, 16 sixteenth steps;
- 3/4: 3 quarters/bar, 12 steps;
- 6/8: 3 quarters/bar (compound meter is interpreted as two dotted-quarter
  groups only insofar as the snare lands on beat 2; no triplet subdivision);
- 7/8: 3.5 quarters/bar, 14 steps; a beat-4 snare at step 12 is included;
- 5/4: 5 quarters/bar, 20 steps.

The drum pattern always uses straight 16ths; there is no swing or triplet grid
in 8.1.

---

## 6. Diagnostics

```text
[RHYTHM] bpm=120.0 meter=4/4 running=1
[RHYTHM] loop 1 record mode=beat beats=4.000 seconds=2.000
[RHYTHM] loop 1 play scheduled for beat 12 (in 0.250 s)
[DRUMS] port=IAC Driver Bus 3 channel=10 rate=1.0
[DRUMS] pattern 4/4 kick1=quarter snare1=2,4 perc3=offbeat8 lanes=0x0045
[DRUMS] enabled=1 lanes=0x0045
[MIDI] drums disabled: drum MIDI output 'IAC Driver Bus 3' not found ...;
       drone/arp continue
```

Per-step drum hits are not logged during performance; pattern/config changes,
tempo/meter/transport changes, lane/rate changes, and the drum state lines are
logged instead.

---

## 7. Failure modes

| Symptom | Likely cause | Fix |
|---|---|---|
| `[MIDI] drums disabled: ...` | Bus 3 missing/renamed, or mido/python-rtmidi missing | Create `IAC Driver Bus 3`; run with `--with mido --with python-rtmidi`; check `config/rhythm_config.json` |
| Drums show `port_ok=0` in the GUI | Same as above | Restart the bridge after fixing the bus; drone/arp/audio keep running meanwhile |
| No drum sound but MIDI activity | Drum Rack empty, wrong pads, track not armed/Monitor In, wrong MIDI From | Map MIDI 36–51; MIDI From Bus 3, channel 10/All; Monitor In |
| Kick on the wrong pad | Ableton shows note 36 as C3 instead of C1 | Map by MIDI number, not octave label |
| No kick at all | All lanes off, drums disabled, or channel 10 filtered out | Check `Drums on`, lane toggles, and the track's channel selector |
| Drums drift against loops | Python and sclang clocks are separate | Press **Resync**; transport stop→start re-anchors both |
| Beat-mode Play does nothing | Transport stopped | Press **Play** in the Rhythm group |
| Beat-mode loop length wrong after a tempo change | Buffers are not time-stretched in 8.1 | Stop and re-record at the new tempo, or use free mode |
| Loop state stays `recording` | Auto-stop report lags ≈0.5 s | Wait; press Stop Rec |
| `[UI] evaluate tests/sc/test_live_rig.scd first` | Control surface evaluated before the rig | Evaluate the live rig, then the control surface |
| UI shows waiting labels | Bridge/sclang not running or port mismatch | Start the missing process; check `--bridge-port`/`--sc-port` |
| Closing the UI stops sound | Bridge process also closed | Close only the UI window |

---

## 8. Tests

Run from `ji-live/`.

### 8.1 New pure rhythm/drum tests

```bash
uv run --with python-osc python tests/test_rhythm.py
```

Covers tempo/meter/transport clamping, 4/4/3/4/6/8/5/4/7/8 math, beat-mode take
lengths, loop mode/index/beat validation, the 36–51 note map, the deterministic
four-on-the-floor pattern, lane masks/toggles, half/normal/double rates, a fake
drum output (note numbers/order, note-offs, CC 123, no notes when disabled), a
missing Bus 3 degrading without raising, a drums-only `MIDIRig`, and invalid
configs/rates.

### 8.2 Existing harnesses (must still pass)

```bash
uv run --with python-osc python tests/test_mutation.py
uv run --with python-osc python tests/test_mutation_controls.py
python tests/test_mpe.py --pure
uv run --with python-osc python tests/test_ui.py --pure
```

`JIBridge(...)` still constructs without mido and MIDI disabled; `--midi`
without `--drums` never requires Bus 3.

### 8.3 Optional headless Qt test

```bash
QT_QPA_PLATFORM=offscreen uv run --with python-osc --with PyQt6 \
  python tests/test_ui.py --qt
```

Adds the Rhythm group, BPM/meter/transport/lane/rate/loop-mode sends, status
sync, beat-mode transport guard, and missing-drum-port disabled state. It skips
cleanly if PyQt6/Qt is unavailable.

### 8.4 Sclang / audio checks (human-run)

See [`tests/sc/test_rhythm.scd`](../tests/sc/test_rhythm.scd). Evaluate
`startup.scd`, `tests/sc/test_live_rig.scd`, `ui/control_surface.scd`, then the
test file. It checks the language-side clock/config state and prints manual
instructions; audio and MIDI acceptance are human-only.

---

## 9. Human acceptance checklist

1. The GUI has a **Rhythm** group: Play/Stop, BPM, meter, drum enable/rate,
   16 lane toggles, drum status, and Resync.
2. Start the bridge with `--midi --drums` and a configured
   `IAC Driver Bus 3`; the default four-on-the-floor groove is audible at
   120 BPM, 4/4, on the drum track.
3. Tempo and meter changes take effect: changing to 3/4/100 BPM changes the
   drum pattern length and step rate; half/normal/double time works.
4. Individual drum lanes toggle on/off without stuck notes.
5. Loops 1–4 can be Free or Beat. Beat-mode Record takes the requested number
   of beats at the current tempo and playback starts on the beat.
6. Different beat lengths (4, 3, 5, 7) phase against each other rather than
   hard-restarting together.
7. Free loops behave exactly as in Stage 7.
8. Bus 3 missing: the bridge keeps drone/arp/audio running, reports drums
   disabled, and the GUI does not crash.
9. Existing mutation, autotune, loop, drone/arp, and GUI behavior is unchanged.
10. Closing the GUI leaves the rig running.
11. Record the result. **Done: the owner confirmed the rhythm/drum controls
    and no audio/MIDI regressions on 2026-10-31.**

---

## 10. What the agent verified (and did not)

Verified in the agent environment:

- `tests/test_rhythm.py` passed (pure; fake drum output; no mido).
- `tests/test_ui.py --pure`, `tests/test_mutation.py`,
  `tests/test_mutation_controls.py`, and `tests/test_mpe.py --pure` passed.
- The Qt test **skipped** because the agent container lacks Qt system libraries
  (`libEGL.so.1`); the window was therefore not executed by the agent.
- The sclang control surface could not be evaluated: no SuperCollider runtime
  is available in the agent container.

Not verified by the agent: audio, MIDI, IAC Bus 3, Ableton, the drum rack,
`TempoClock` quantization, loop phasing, GUI behavior on the human's machine,
and the human listening test. **Stage 8.1 awaits human acceptance.**

---

## 11. Limitations

- No groove presets, density, syncopation, swing, probability, or per-step
  lane grid (8.2).
- No bass/lead voices (8.3), pattern/sequencing mutation (8.4), save/recall or
  seed reproduction (8.5), more loops/overdub/routing (8.6).
- Tempo changes do not time-stretch existing loop buffers.
- The Stage 6 arp keeps its fixed 0.25 s/0.20 s timing; it is not tempo-synced.
- Python drums and sclang loop quantization are separate clocks; drift is
  possible over long sessions. Use Resync or stop/start.
- The drum pattern places hits only on kick1/snare1/perc3 by default. Lane
  toggles for the other lanes are ready but silent until 8.2.
- The GUI edits beat lengths as whole beats; sclang/OSC accept fractional
  values.
- Drum status is the bridge's configured port state, not a live per-message
  probe of Ableton.

# JI Station — Stage 7 Minimal Performance GUI

**Status:** the owner reports the live GUI/audio/MIDI rig works well, with one
open item: the drone MIDI was not received (the arp worked). Stage 7.1 adds
independent **Drone on** / **Arp on** switches and a retrigger path for exactly
that case; the updated Qt test and the live drone/toggle check are still pending.
Stage 7 claims **no** new audio DSP, no new MIDI voices, no rhythm sequencer, no
save/recall, and no visual process. It is a control surface for the accepted
Stage 5.5 audio rig and Stage 6 MIDI rig.

**Next:** rerun the headless Qt test, then check the Drone/Arp switches and the
drone path in §11. Stage 7 is not complete until the owner confirms the GUI
controls the rig and audio/MIDI behavior is unchanged.

**Stage 8.1 note:** the GUI now also has a compact Rhythm group, per-loop
Free/Beat lengths, and a top-row **MIDI** group holding the Drone/Arp switches
(so they stay visible). Those additions are documented in
[README_stage8.md](README_stage8.md); every Stage 7 control described below is
unchanged.

---

## 1. What Stage 7 adds

One PyQt6 window, launched as a separate process, with four sections:

1. **Mutation** — Mutate/Octave buttons and every mutation knob already exposed
   by the bridge.
2. **Live Autotune** — the two live input channels' six exposed Synth
   parameters, plus a Link toggle.
3. **Loops** — loops 1–4 with Record, Stop Rec, Play, Stop, Clear, a visible
   state label, Stop All, and a 1–30 s length control.
4. **Status** — ratio/Hz set, anchor, fundamental, bridge control state, CPU,
   and loop states. Stage 8.1 moved MIDI enabled/ports and the independent
   **Drone on** / **Arp on** checkboxes to a compact top-row **MIDI** group.

The MIDI checkboxes are a Stage 7.1 extension requested after the first live
check. They switch the existing Stage 6 buses; they add no new MIDI voice or
generator.

The UI never talks to Ableton or scsynth directly. It sends mutation controls to
the Python bridge, sends autotune/loop controls to sclang, and listens for
status on one UDP port. Closing the window stops only its own listener; the
bridge, audio, loops, and MIDI keep running.

### Files

| File | Status | Purpose |
|---|---|---|
| `ui/__init__.py` | new | package marker; does not import PyQt6 |
| `ui/osc_control.py` | new | pure message builders, range/clamp helpers, status parsers, `StatusStore`, `OSCController`, UDP listener |
| `ui/ji_ui.py` | new | one-window PyQt6 application |
| `ui/control_surface.scd` | new | sclang autotune/loop/status OSC; evaluate after `tests/sc/test_live_rig.scd` |
| `tests/test_ui.py` | new | pure OSC tests plus optional headless Qt smoke test |
| `control/osc_server.py` | modified additively | `/ji/ui/hello` and `/ji/midi/set_enabled` callbacks |
| `control/ji_bridge.py` | modified additively | UI status stream, `/ji/ui/hello` reply, `/ji/midi/state`, `--ui-host`/`--ui-port` |
| `control/midi_rig.py` | modified additively | Stage 7.1 independent Drone/Arp switches |
| `tests/test_mpe.py` | extended additively | per-bus switch regression test |
| `docs/README_mpe.md` | updated | drone-only troubleshooting note |
| `docs/README_stage7.md` | new | this document |
| `docs/design_doc_10.28.10.50` | updated | Stage 7/7.1 status, implementation notes, changelog |

Not modified: `synthdefs/*`, `startup.scd`, `tests/sc/test_live_rig.scd` audio behavior,
`tests/sc/test_dual_loop.scd`, `tests/sc/test_ableton_routing.scd`, `control/mutation.py`, and the
`/ji/freqset/update` payload/receiver.

---

## 2. Requirements

- The accepted Stage 5.5 rig: MiniFuse 2 input / BlackHole 16ch output, 2 in /
  16 out, 48 kHz, blockSize 64.
- The Stage 6 MIDI rig if the drone/arp is wanted (IAC Bus 1/2, member channel
  2, 48-semitone bend range).
- Python 3.11+ via `uv`.
- `python-osc` for the bridge and UI.
- **PyQt6** for the window. The recommended invocation downloads it into the
  `uv` environment:

  ```bash
  uv run --with python-osc --with PyQt6 python ui/ji_ui.py
  ```

  A local install also works:

  ```bash
  python -m pip install python-osc PyQt6
  python ui/ji_ui.py
  ```

  PyQt6 must match the machine's Python/OS (Apple Silicon wheels exist for
  current Python 3.11–3.13). If Qt cannot load a system library, the import
  prints a clear `PyQt6 is required...` message; fix the Python environment
  rather than silently falling back.

---

## 3. Start order

Run everything from `ji-live/`.

1. Evaluate `startup.scd` in sclang.
2. Evaluate `tests/sc/test_live_rig.scd`.
3. Start the bridge (add MIDI flags for Stage 6):

   ```bash
   uv run --with python-osc --with mido --with python-rtmidi \
     python control/ji_bridge.py --interactive --midi --glide-time 0.15
   ```

   Without MIDI:

   ```bash
   uv run --with python-osc python control/ji_bridge.py \
     --interactive --glide-time 0.15
   ```

4. Evaluate `ui/control_surface.scd` in sclang **after** `tests/sc/test_live_rig.scd`.
   It prints:

   ```text
   [UI] control surface installed -> 127.0.0.1:9001; autotune + loop + status OSC ready
   ```

   If it prints `[UI] evaluate tests/sc/test_live_rig.scd first`, the live rig globals are
   missing; evaluate `tests/sc/test_live_rig.scd`, then re-evaluate the control surface.

5. Launch the GUI:

   ```bash
   uv run --with python-osc --with PyQt6 python ui/ji_ui.py
   ```

6. Keep the window open for the performance. Closing it leaves the bridge,
   audio, loops, and MIDI running.

If `tests/sc/test_live_rig.scd` is re-evaluated, re-evaluate `ui/control_surface.scd`
afterward (the rig replaces its synths and loop arrays).

### Ports and CLI

| Purpose | Default | Flag |
|---|---|---|
| Bridge control/listen | `127.0.0.1:9000` | bridge `--listen-host/--listen-port`; UI `--bridge-host/--bridge-port` |
| sclang OSC | `127.0.0.1:57120` | bridge `--sc-host/--sc-port`; UI `--sc-host/--sc-port` |
| UI status listener | `127.0.0.1:9001` | UI `--ui-host/--ui-port`; bridge `--ui-host/--ui-port`; sclang `~ji_ui_host` / `~ji_ui_port` |

The UI binds `9001` first, then sends `/ji/ui/hello` to the bridge and
`/ji/status/query` to sclang. If `9001` is already in use, the window fails with
a clear message instead of silently using another port.

If you change the UI port, change it in all three places:

```text
bridge:  --ui-port <port>
sclang:  ~ji_ui_port = <port>;  then re-evaluate ui/control_surface.scd
UI:      --ui-port <port>
```

---

## 4. Control and range reference

### 4.1 Mutation (UI → bridge, port 9000)

Knob moves are coalesced to ≈30 Hz; buttons send immediately.

| Control | OSC address | Range | Default |
|---|---|---|---|
| Randomness | `/ji/mutation/set_randomness` | 0.0–1.0 | 0.55 |
| Dissonance | `/ji/mutation/set_dissonance_bias` | −1.0–1.0 | 0.0 |
| Stepwise | `/ji/mutation/set_stepwise_bias` | 0.0–1.0 | 0.25 |
| Temperature | `/ji/mutation/set_temperature` | 0.0–1.0 | 0.0 |
| Anchor mobility | `/ji/mutation/set_anchor_mobility` | 0.0–1.0 | 0.25 |
| Changes | `/ji/mutation/set_change_count` | 1–`max_changes` | 1 |
| Glide | `/ji/mutation/set_glide_time` | 0.0–2.0 s | 0.15 s |
| **Mutate** | `/ji/mutation/trigger` | — | — |
| **Octave** | `/ji/mutation/octave` | — | — |

`max_changes` comes from `/ji/ui/state`; until the first bridge message the UI
uses 1–3. The bridge clamps values again, so an out-of-range UI value is safe.

### 4.2 Live Autotune (UI → sclang, port 57120)

Two columns, **Input 0** and **Input 1**. Each knob sends:

```text
/ji/autotune/set   channel   param   value
```

`channel` is `0` or `1` (SC input 0/1); `param` is the exact Synth argument
name. The sclang surface whitelists these six names and clamps them; an
arbitrary string is rejected, never passed to `Synth.set`.

| Parameter | Range | Default |
|---|---|---|
| `retuneTime` | 0.001–2.0 s | 0.05 s |
| `mix` | 0.0–1.0 | 1.0 |
| `windowSize` | 0.02–0.5 s | 0.1 s |
| `pitchRangeLimit` | 0–24 semitones | 12 |
| `tartiniThreshold` | 0.0–1.0 | 0.9 |
| `inputGain` | 0.0–2.0 | 1.0 |

**Link** checked means any autotune knob move sends the same value to both
channels. Loop-player autotune knobs are not in Stage 7.

### 4.3 Loops (UI → sclang)

| Action | OSC address | Args |
|---|---|---|
| Record at displayed length | `/ji/loop/record_length` | `int index, float seconds` |
| Record at sclang default | `/ji/loop/record` | `int index` |
| Stop recording early | `/ji/loop/stop_record` | `int index` |
| Play | `/ji/loop/play` | `int index` |
| Stop playback | `/ji/loop/stop` | `int index` |
| Stop all | `/ji/loop/stop_all` | none |
| Clear | `/ji/loop/clear` | `int index` |
| Set default length | `/ji/loop/set_default_length` | `float seconds` |

- `index` is 1–4. Invalid indices are rejected/logged in both Python and
  sclang.
- The UI's **Record** button always sends `/ji/loop/record_length` with the
  length shown on the knob (1.0–30.0 s, default 8.0 s, rounded to the displayed
  0.1 s), so the take uses the displayed length even if the default-length
  message has not been applied yet.
- Moving the length knob also sends `/ji/loop/set_default_length` (coalesced,
  same 0.1 s rounding).
- **Clear** stops the recorder and player, frees the buffer after the player's
  30 ms fade (about 0.1 s), and sets the state to `empty`. A later Record
  allocates a fresh buffer. After a Clear, that loop's `~ji_buf_loopN` is `nil`
  until the next Record; if you later run the manual closing lines in
  `tests/sc/test_live_rig.scd`, guard buffer frees with `~ji_buf_loop1 !? { |b| b.free }`
  (or re-record first).
- The sclang control surface calls the existing `~ji_record_loop`,
  `~ji_stop_record`, `~ji_play_loop`, `~ji_stop_loop`, `~ji_set_loop_default`,
  and buffer bookkeeping. Loop logic is not duplicated.
- **Stop All Loops** calls `~ji_stop_record.(1..4)` and `~ji_stop_loop.(1..4)`.
  It deliberately does **not** call `~ji_stop_all`, because that function also
  frees the live autotune synths and stops the lookup/CPU routines (it is the
  whole-rig stop). The live inputs and the rest of the rig keep running.
- Visible loop states: `empty`, `recording`, `recorded`, `playing`, `stopped`
  (plus `waiting` until the first sclang status message).

### 4.4 Status (sclang/bridge → UI, port 9001)

| Message | Args | Source |
|---|---|---|
| `/ji/freqset/changed` | `float fundamental, float anchor (-1 = none), float ratio_1 ... ratio_N` | bridge, after every set and on hello |
| `/ji/ui/state` | `float randomness, float dissonance_bias, float stepwise_bias, float glide_time, int change_count, float temperature, float anchor_mobility, int max_changes` | bridge, after every set and on hello |
| `/ji/midi/status` | `int enabled (0/1), string drone_port, string arp_port` | bridge, after every set and on hello; empty ports when disabled |
| `/ji/midi/state` | `int drone_on (0/1), int arp_on (0/1)` | bridge, after every set, on hello, and after a UI switch |
| `/ji/cpu` | `float percent (0–100)` | sclang, ≈2 Hz |
| `/ji/loop/state` | `int index (1–4), string state` | sclang, after every action and ≈2 Hz |
| `/ji/autotune/state` | `int channel, float retuneTime, mix, windowSize, pitchRangeLimit, tartiniThreshold, inputGain` | sclang, after a set, on status query, and at control-surface install |

The UI shows `BRIDGE: waiting for bridge` and `SCLANG: waiting for sclang`
until the first corresponding message. Missing UDP messages are never fatal;
the window stays usable and sends controls even if a peer is absent.

### 4.4.1 MIDI output switches (Stage 7.1)

The top-right MIDI group (Stage 8.1; formerly the Status group) has two
checkboxes, **Drone on** and **Arp on**. They are
enabled only when the bridge reports MIDI enabled; both default on. The UI sends
both current values on every toggle:

```text
/ji/midi/set_enabled   int drone (0/1)   int arp (0/1)
```

The bridge maps this to `MIDIRig.set_drone_enabled()` and
`MIDIRig.set_arp_enabled()`:

- Disabling **Drone** note-offs the held anchor and sends All Notes Off on the
  drone bus; the port stays open.
- Enabling **Drone** re-applies the last set snapshot, which note-ons the
  current anchor again.
- Disabling **Arp** stops the arp thread and sends All Notes Off on the arp bus.
- Enabling **Arp** restarts the arp thread from the current set.
- Neither switch closes a port, affects audio, or changes the frequency set.

**Drone-only troubleshooting:** the drone is a single held note sent when the
bridge starts (and only retriggered when the anchor's MIDI note changes). The
arp continuously sends new notes, so it recovers from a late track/instrument
setup while the drone does not. If the Drone track/instrument was not ready at
startup, toggle **Drone** off and on to retrigger it, or restart the bridge.
Then check the Ableton Drone track's MIDI From (IAC Bus 1, All/MPE channels),
Monitor In/arm, instrument, and MPE range; see `README_mpe.md` §9.

### 4.5 UI startup and shutdown

On startup:

1. Bind the UI listener on `--ui-port` (default 9001) first, so replies cannot
   race it.
2. Send `/ji/ui/hello` to the bridge.
3. Send `/ji/status/query` to sclang.
4. Show the documented defaults; incoming state syncs knobs with widget signals
   blocked, so no feedback OSC is emitted.

On close: stop the UI listener and close the UI's client sockets. Do **not**
stop the bridge, audio, loops, or MIDI.

---

## 5. Architecture

```text
UI (PyQt6, one window)
 ├─ OSC 9000  → ji_bridge.py   (mutation controls; /ji/midi/set_enabled)
 ├─ OSC 57120 → sclang         (/ji/autotune/set, loop transport/length,
 │                              /ji/status/query)
 └─ OSC 9001  ← sclang         (/ji/cpu, /ji/loop/state, /ji/autotune/state)
               ← bridge         (/ji/freqset/changed, /ji/ui/state,
                                  /ji/midi/status, /ji/midi/state)
```

Implementation notes:

- `ui/osc_control.py` has no PyQt6 import. It owns the message builders,
  parsers, `StatusStore`, outgoing clients, and the background UDP listener.
- The listener runs in a daemon thread. `ui/ji_ui.py` wires its callback to a
  `pyqtSignal`; the queued connection updates widgets only on the Qt main
  thread.
- The receive queue and Qt event loop are never blocked on OSC/socket calls.
- Knob sends are coalesced by a 33 ms `QTimer`; buttons call the controller
  immediately.
- Incoming `/ji/ui/state` and `/ji/autotune/state` set widget values with
  `QSignalBlocker`, so synchronization does not echo controls back.
- `JIBridge` creates a second `SimpleUDPClient` for UI status. Existing
  `bridge.client.sent` assertions still see exactly one
  `/ji/freqset/update` per set.

---

## 6. Runtime behavior details

### 6.1 Bridge additions

- `JIBridge(..., ui_host="127.0.0.1", ui_port=9001)` appends optional
  constructor values after the existing parameters.
- After every `send_set`, the bridge sends:
  - `/ji/freqset/changed` with the current fundamental, anchor (`-1.0` if none),
    and the octave-reduced ratios sorted by pitch;
  - `/ji/ui/state` with the current controls and `max_changes`;
  - `/ji/midi/status` with enabled/ports;
  - `/ji/midi/state` with the Drone/Arp switch state.
- On `/ji/ui/hello`, it sends the same four messages. It does not send another
  `/ji/freqset/update`.
- `/ji/midi/status` is sent at bridge start through the initial `send_set`, even
  when MIDI is disabled.
- `/ji/midi/set_enabled` is handled by `MutationOSCServer` and forwarded to the
  current `MIDIRig`; it never touches audio or the frequency set.
- `JIBridge.stop()` does not touch the UI; the UI is a separate process.

### 6.2 sclang control surface

- Checks the live-rig globals; if missing, prints
  `[UI] evaluate tests/sc/test_live_rig.scd first` and installs nothing.
- Frees old Stage 7 `OSCdef`s and stops the old status routine before
  installing new ones. Keys are distinct from
  `\ji_freqset_update_live_rig`:
  `\ji_ui_autotune`, `\ji_ui_loop_*`, `\ji_ui_status`.
- Sends status to `~ji_ui_addr = NetAddr("127.0.0.1", 9001)` by default;
  `~ji_ui_host` and `~ji_ui_port` are exposed for manual changes.
- `/ji/status/query` immediately sends `/ji/cpu`, both autotune states, and all
  four loop states, then refreshes the autotune state from the live Synth
  arguments.
- Status traffic: CPU and loop states at 2 Hz plus after every action;
  autotune state on set/query/install. This is deliberately modest.

### 6.3 Loop state timing

Record/re-record allocates a fresh buffer asynchronously inside
`~ji_record_loop`; the sclang surface reports `recording` about 0.1 s after the
action. Stop/Play/Stop/Clear set their state synchronously and report
immediately. Auto-stop at the end of a buffer is reported by the 2 Hz status
routine (and the action echo), so a state label may lag by up to about half a
second after a take fills.

---

## 7. Failure modes

| Symptom | Likely cause | Fix |
|---|---|---|
| `ModuleNotFoundError: No module named 'PyQt6'` | GUI launched without PyQt6 | Use `uv run --with python-osc --with PyQt6 python ui/ji_ui.py` or `pip install PyQt6` |
| `PyQt6 is required...` message at launch | PyQt6 package or a system Qt library missing | Install/fix PyQt6 for this Python; on Linux, install the Qt/EGL system libraries |
| `cannot listen for UI status on 127.0.0.1:9001: ... Address already in use` | Another UI instance, or another process owns 9001 | Close the other UI; or change `--ui-port` on the UI **and** `--ui-port` on the bridge, and `~ji_ui_port` in sclang |
| `[UI] evaluate tests/sc/test_live_rig.scd first` in sclang | Control surface evaluated before the live rig, or the rig globals were freed | Evaluate `tests/sc/test_live_rig.scd`, then re-evaluate `ui/control_surface.scd` |
| UI shows `waiting for bridge` | Bridge not running, or `--bridge-port`/`--listen-port` mismatch | Start the bridge; make the UI's `--bridge-port` match the bridge's `--listen-port` |
| UI shows `waiting for sclang` | Control surface not evaluated, or `--sc-port` mismatch | Evaluate `ui/control_surface.scd`; check `NetAddr.langPort` and the UI/bridge `--sc-port` |
| Mutation knobs/buttons do nothing | Bridge not receiving on 9000, or mutation engine failed | Check the bridge's `[OSC]`/`[BRIDGE]` lines and the UI's `OSC send failed` message |
| Autotune knobs do nothing | Control surface missing, wrong channel, or synth freed | Evaluate the control surface; check sclang's `[UI] autotune ch=...` line |
| Loop Play prints `no buffer` | Nothing recorded, or Clear freed the buffer | Record first; Clear intentionally empties the loop |
| Loop state stays `recording` after the length | Auto-stop has not reached the 2 Hz report yet, or Stop Rec was missed | Wait half a second; press Stop Rec; check sclang `[LOOP]` lines |
| MIDI shows disabled | Bridge started without `--midi` | Restart with `--midi`; MIDI remains off by default |
| MIDI shows enabled but no sound | IAC/Ableton path, instrument, or MPE range problem | Use `README_mpe.md`; UI status only reflects the bridge's config |
| Arp works, drone silent | Drone track not ready at bridge start (the drone sends one held note and does not repeat); or Drone track MIDI From is Bus 2 / channel Ch. 1; Monitor Auto without arm; different instrument/MPE setting | Toggle **Drone off/on** in the MIDI group to retrigger; confirm Drone track MIDI From = `IAC Driver Bus 1`, All/MPE channels, Monitor In; see `README_mpe.md` §9 |
| Drone/Arp checkboxes are greyed out | Bridge started without `--midi`, or `/ji/midi/status` has not arrived | Restart the bridge with `--midi`; check the UI's `MIDI:` status line |
| Drone/Arp toggle does nothing | Bridge is not running with MIDI, or the bridge is an older build | Check the bridge's `[MIDI] drone enabled/disabled` / `[MIDI] arp enabled/disabled` lines |
| Closing the UI stops sound | The bridge/audio process was also closed | Expected UI behavior is to leave the rig running; launch the UI separately and close only its window |
| Knob value snaps back | Incoming `/ji/ui/state` or `/ji/autotune/state` is syncing the knob | Normal after a mutation/echo; the last move wins |

---

## 8. Tests

Run from `ji-live/`.

### 8.1 Pure tests (no PyQt6, no hardware, no real sockets)

```bash
uv run --with python-osc python tests/test_ui.py --pure
```

Covers every message builder (address, argument order, int/float types,
clamping), mutation `change_count` bounds, both autotune channels/all six
parameters, loop transport/length builders, every status parser (including
anchor and `-1.0`), `StatusStore` waiting flags, the `/ji/ui/hello` server
callback, `JIBridge` UI status (exactly one `/ji/freqset/update` per set), and
the `OSCController` listener-first lifecycle. `--pure` exits nonzero on failure
and does not import PyQt6.

### 8.2 Optional headless Qt smoke test

```bash
QT_QPA_PLATFORM=offscreen uv run --with python-osc --with PyQt6 \
  python tests/test_ui.py --qt
```

Builds the window with a fake controller, checks the four sections and the
initial waiting labels, moves a knob and clicks every loop action (asserting the
1-based loop index), verifies Link and loop-record/length messages, feeds status
messages, exercises the Drone/Arp checkboxes (including no feedback on state
sync), and closes the window (asserting the fake controller stopped). **It does
not need SuperCollider, the bridge, audio, or MIDI** — it uses fake clients
inside the controller. If PyQt6/Qt cannot be imported it skips with a clear
message and exits 0; `--pure` never depends on Qt.

### 8.3 Existing harnesses

```bash
uv run --with python-osc python tests/test_mutation.py
uv run --with python-osc python tests/test_mutation_controls.py
python tests/test_mpe.py --pure
```

All must still pass. MIDI remains off by default and `JIBridge(...)` constructs
without mido.

---

## 9. What has been verified

In the agent environment:

- `tests/test_ui.py --pure` passed.
- `tests/test_mutation.py`, `tests/test_mutation_controls.py`, and `tests/test_mpe.py --pure`
  passed after the additive bridge/server changes.
- `tests/test_ui.py --qt` **skipped** in the agent container because the PyQt6 wheel
  imports but its Qt system libraries (for example `libEGL.so.1`) are not
  installed. The Qt window was therefore not executed by the agent.

Human-run check (2026-10-30):

- `QT_QPA_PLATFORM=offscreen uv run --with python-osc --with PyQt6 python tests/test_ui.py --qt`
  **passed on the owner's macOS machine**: the four groups, knob/button sends,
  loop indices, status sync, waiting labels, and clean close all succeeded.
  This validates window construction and signal wiring only. It does **not**
  validate SuperCollider, audio, MIDI, Ableton, IAC, or live performance, and
  it is not Stage 7 acceptance.
- The Stage 7.1 Drone/Arp checkboxes were added after that run, so the updated
  Qt test still needs one owner rerun.
- The owner reported on 2026-10-30 that the live GUI/audio/MIDI rig works well,
  with one exception: the **drone MIDI was not received** (the arp was fine).
  The Stage 7.1 Drone/Arp switches and the drone retrigger note below were added
  in response; the live drone path and the new switches still need a check.

No audio, MIDI, Ableton, IAC, or live GUI behavior has been tested by the agent.
The agent makes no acceptance claim.

---

## 10. Limitations

- No tabs, menus, dialogs, theming pass, animations, or multi-window workflow.
- No rhythm/tempo/sequencing, extra MIDI voices, MIDI-generation parameters,
  save/recall, more than four loops, overdub, per-loop routing, loop-player
  autotune knobs, or loop file persistence. Those are Stage 8+.
- MIDI status is the bridge's configured enabled/ports state, not a live probe
  of every port.
- The Drone/Arp switch state lives in the bridge process, resets to both-on
  when the bridge restarts, and mutes the bus (it does not mute the Ableton
  track or save/recall state).
- Autotune state is a local sclang mirror refreshed at control-surface install
  and on `/ji/status/query` (UI startup); changes made only in a terminal after
  that are not pushed continuously.
- Loop auto-stop state may lag by up to about half a second.
- The UI shows no history/graphs, no per-loop waveforms, and no per-channel
  pitch readout; it is a minimal control surface.

---

## 11. Human acceptance checklist

Run in this order and record the result:

1. One window opens with **Mutation**, **Live Autotune**, **Loops**,
   **Rhythm**, **MIDI**, and **Status** sections.
2. Frequency set, anchor, fundamental, CPU, loop states, and MIDI status update
   live. Before the bridge/sclang are up, the UI shows a waiting state and does
   not crash.
3. Mutation knobs and Mutate/Octave change the audio/mutation behavior exactly
   as the existing terminal controls do.
4. Live-autotune knobs audibly change `retuneTime`, `mix`, `windowSize`,
   `pitchRangeLimit`, `tartiniThreshold`, and `inputGain`; **Link** sends both
   channels.
5. Loop 1–4 Record / Stop Rec / Play / Stop / Clear work; the length control sets
   the take length; states update; no clicks or stuck loops.
6. The Stage 6 drone/arp still works and gains no stuck notes.
7. **Drone on** and **Arp on** independently mute/unmute their buses; toggling
   Drone off/on retriggers a held drone note; neither switch closes a port or
   affects the other bus.
8. Closing the UI leaves the audio/MIDI rig running.
9. No audio dropouts, no Qt freeze under live use; CPU remains within the live
   budget.
10. Record the result. **Stage 7 is not complete until the human confirms the
    GUI controls the rig and the audio/MIDI behavior is unchanged.**

---

## 12. One-line sanity checks

```bash
cd ji-live
python tests/test_mpe.py --pure
uv run --with python-osc python tests/test_ui.py --pure
uv run --with python-osc --with PyQt6 python -c "import PyQt6; print('PyQt6 ok')"
```

# Stage 7 Agent Prompt — Minimal Performance GUI

You are implementing **Stage 7** of the JI live-performance system: the first
single-window performance GUI. Stages 0–6 are implemented, and the project owner
has confirmed all audio and MIDI acceptance checks, including Stage 6
(2026-10-30). Stage 7 is a **control-surface stage**: no new audio DSP, no new
MIDI voices, no rhythm sequencer, no save/recall, and no visual process.

The owner's intent is to make the working system practical for live
improvisation. One window must expose the existing mutation, live-autotune, loop,
and status controls. Keep it barebones and reliable; aesthetics come later.

Do not claim GUI/audio acceptance. The human is the authority.

---

## 1. Read these files first

Read the current code and status, not just summaries:

```text
ji-live/design_doc_10.28.10.50          §2.15–§2.16, §4.1, §4.3, §4.4, §6.11–§6.13,
                                        §7.1, §8.7–§8.12, §9, §13, §14
ji-live/README_live_rig.md               four-channel audio rig, loop functions, start order
ji-live/README_mpe.md                    Stage 6 MIDI rig, owner-confirmed state
ji-live/test_live_rig.scd                current sclang globals/functions; do not change audio behavior
ji-live/synthdefs/ji_autotune_ch.scd     live autotune parameters and ranges
ji-live/synthdefs/ji_looper_player.scd   loop-player parameters
ji-live/synthdefs/ji_looper_recorder.scd recorder parameters
ji-live/control/ji_bridge.py             bridge lifecycle, send_set, CLI
ji-live/control/osc_server.py            existing OSC surface and callback pattern
ji-live/control/mutation.py              engine API only; do not modify
ji-live/test_mutation_controls.py        existing bridge/server harness style
ji-live/test_mpe.py                      fake-output / pure-test style
ji-live/config/live_rig_routing.json     four mono audio channels
ji-live/config/mpe_config.json           Stage 6 MIDI config
```

Also read `PROMPT_stage5.md` for the project's handoff tone and
`README_stage5.md` for the no-Aggregate audio setup. Do not use `PROMPT_stage5.md`
as the current spec.

---

## 2. Current state you must preserve

- Stage 5.5 audio rig: MiniFuse 2 input / BlackHole 16ch output, 2 in / 16 out,
  48 kHz, blockSize 64. SC outputs 0/1 = live autotuned inputs, 2/3 = loops.
- `test_live_rig.scd` owns the two live autotune synths, four loop voices, the
  20 Hz lookup, `/ji/freqset/update` receiver, and loop functions:
  `~ji_record_loop`, `~ji_stop_record`, `~ji_play_loop`, `~ji_stop_loop`,
  `~ji_stop_all`, `~ji_set_loop_default`.
- Stage 6 MIDI: drone on `IAC Driver Bus 1`, arp on `IAC Driver Bus 2`, both
  member channel 2, 48-semitone bend range. Owner-confirmed 2026-10-30.
- The Python bridge owns the `MutationEngine` and sends exactly:
  `/ji/freqset/update fundamental glide_time ratio_1 ... ratio_N`.
- `JIBridge(...)` must still construct without mido and MIDI disabled.
- Existing harnesses must keep passing:
  `test_mutation.py`, `test_mutation_controls.py`, `test_mpe.py --pure`.
- The four-channel audio routing and the Stage 6 MIDI path are not allowed to
  regress.

**Do not modify:** `synthdefs/*`, `startup.scd`, `test_live_rig.scd` audio
behavior, `test_dual_loop.scd`, `test_ableton_routing.scd`, `control/mutation.py`,
the `/ji/freqset/update` payload/receiver, or the Stage 5.5 routing/loop-length
semantics. Add `ui/control_surface.scd` as a separate sclang file. If a tiny
additive change to `test_live_rig.scd` is unavoidable, preserve every default and
document it; prefer no change.

Stage 6's `JIBridge` and `MutationOSCServer` may be extended **additively** for
UI status/hello only. Existing behavior and tests must remain intact.

---

## 3. Goal

One PyQt6 window, launched as a separate process, that controls:

1. Every mutation parameter already exposed by the bridge, plus Mutate and
   Octave buttons.
2. The two live autotune channels' exposed Synth parameters.
3. Loop 1–4 record / stop-record / play / stop, Stop All, and loop length.
4. Live status: frequency set/ratios/Hz, anchor, fundamental, bridge control
   state, CPU, loop states, and MIDI enabled/ports.

The window is one screen. No tabs, menus, multi-window workflows, theming pass,
animations, or aesthetic redesign. Use standard `QDial` knobs, `QPushButton`
buttons, `QGroupBox` sections, and labels. Keep it readable and performable.

---

## 4. Scope

### In scope

- `ui/ji_ui.py` — single-window PyQt6 application.
- `ui/osc_control.py` — pure-ish OSC message/state layer with no PyQt6 import.
- `ui/control_surface.scd` — sclang OSCdefs and status feedback for autotune
  and loop transport, plus `/ji/cpu`, `/ji/loop/state`, `/ji/autotune/state`.
- `ui/__init__.py` — package marker if needed for imports/tests.
- `test_ui.py` — pure message/state tests plus an optional headless Qt smoke test.
- `README_stage7.md` — setup, start order, controls, failures, acceptance.
- Additive UI-status support in `control/ji_bridge.py` and
  `control/osc_server.py`:
  - `/ji/ui/hello` UI → bridge, bridge replies with current state.
  - `/ji/freqset/changed`, `/ji/ui/state`, `/ji/midi/status` bridge → UI.
- Documentation updates in `design_doc_10.28.10.50` and Stage 7 README.
- Run scripts/commands documented below.

### Out of scope for Stage 7

- Rhythm, tempo, time signature, syncopation, sequencing.
- Additional MIDI outputs/voices (drums, bass, lead, etc.).
- MIDI-generation algorithm controls (arp groups/offsets/seed editing, etc.).
- Save/load/recall of states.
- More than the existing four loops, overdub, per-loop routing, loop-player
  autotune knobs (gain/mix/window/range/threshold), or loop file persistence.
- Granular/glitch FX, GUI aesthetics/UX redesign, visual process, Ableton API
  automation, new audio SynthDefs, or new audio buses.

If a requested control is not in the existing system, do not invent a new audio
feature for it in this stage. Defer it to Stage 8.

---

## 5. Architecture

Use this topology; it keeps the accepted audio/MIDI paths untouched:

```text
UI (PyQt6, one window)
 ├─ OSC 9000  → ji_bridge.py   (mutation controls; existing addresses)
 ├─ OSC 57120 → sclang         (live autotune; loop transport/length)
 └─ OSC 9001  ← sclang         (/ji/cpu, /ji/loop/state, /ji/autotune/state)
               ← bridge         (/ji/freqset/changed, /ji/ui/state, /ji/midi/status)
```

- The UI never talks to Ableton or scsynth directly.
- The UI does not start/stop the bridge or the audio/MIDI rig.
- The OSC listener runs in a background thread. Do not update Qt widgets from
  that thread; emit a Qt signal or use `QMetaObject.invokeMethod` with a queued
  connection.
- The UI must run without a live bridge/sclang (show "waiting for status"),
  and must close cleanly if either is absent.
- The UI must not block the Qt event loop on OSC or socket operations.

Ports should be CLI-overridable, with these defaults:

```text
--bridge-host 127.0.0.1  --bridge-port 9000
--sc-host     127.0.0.1  --sc-port     57120
--ui-host     127.0.0.1  --ui-port     9001
```

---

## 6. Required behavior

### 6.1 One window

- One top-level window, one screen, grouped sections:
  **Mutation**, **Live Autotune**, **Loops**, **Status**.
- No tabs, menus, dialogs, or separate windows for normal operation.
- `QDial` for continuous knobs; each knob has a nearby label showing its
  current value and units.
- `QPushButton` for actions.
- The window should be usable at a typical laptop resolution; do not rely on
  hover tooltips for essential information.

### 6.2 Mutation section

Buttons:

- **Mutate** → `/ji/mutation/trigger`
- **Octave** → `/ji/mutation/octave`

Knobs (send on change, coalesced to ≈30 Hz; buttons send immediately):

| Control | Address | Range | Default |
|---|---|---|---|
| Randomness | `/ji/mutation/set_randomness` | 0.0–1.0 | 0.55 |
| Dissonance | `/ji/mutation/set_dissonance_bias` | −1.0–1.0 | 0.0 |
| Stepwise | `/ji/mutation/set_stepwise_bias` | 0.0–1.0 | 0.25 |
| Temperature | `/ji/mutation/set_temperature` | 0.0–1.0 | 0.0 |
| Anchor mobility | `/ji/mutation/set_anchor_mobility` | 0.0–1.0 | 0.25 |
| Changes | `/ji/mutation/set_change_count` | 1–max_changes | 1 |
| Glide | `/ji/mutation/set_glide_time` | 0.0–2.0 s | 0.15 |

The UI gets `change_count` and `max_changes` from `/ji/ui/state`; until then use
1 and 3. The bridge clamps out-of-range values anyway.

### 6.3 Live autotune section

Two columns, **Input 0** and **Input 1**, plus a **Link** checkbox. When Link is
on, moving any autotune knob sends the same value to both channels.

Per-channel knobs:

| Parameter | Range | Default |
|---|---|---|
| `retuneTime` | 0.001–2.0 s | 0.05 |
| `mix` | 0.0–1.0 | 1.0 |
| `windowSize` | 0.02–0.5 s | 0.1 |
| `pitchRangeLimit` | 0–24 semitones | 12 |
| `tartiniThreshold` | 0.0–1.0 | 0.9 |
| `inputGain` | 0.0–2.0 | 1.0 |

Send:

```text
/ji/autotune/set   channel   param   value
```

`channel` is 0 or 1 (SC input 0/1). `param` is the exact Synth argument name
string. The sclang control surface must whitelist these parameters and clamp
them; never pass an arbitrary string to `Synth.set`.

Do not add loop-player autotune knobs in Stage 7. They are Stage 8.

### 6.4 Loop section

Loop 1–4, each with:

- **Record** → record at the current length.
- **Stop Rec** → stop early.
- **Play** → play the buffer.
- **Stop** → stop playback.
- **Clear** → stop and clear the buffer (safe when empty).
- A visible state label: `empty`, `recording`, `recorded`, `playing`, `stopped`.

Global:

- **Stop All Loops** → `/ji/loop/stop_all`.
- **Length** knob/field, 1.0–30.0 s, default 8.0 s. Changing it sends
  `/ji/loop/set_default_length`; pressing Record sends
  `/ji/loop/record_length [index, seconds]` so the take uses the displayed
  length even if the default was not applied yet.

OSC:

```text
/ji/loop/record          int index
/ji/loop/record_length   int index, float seconds
/ji/loop/stop_record     int index
/ji/loop/play            int index
/ji/loop/stop            int index
/ji/loop/stop_all        none
/ji/loop/clear           int index
/ji/loop/set_default_length float seconds
```

The sclang control surface calls the existing `~ji_*` functions; do not
reimplement loop logic in Python or duplicate it in the UI.

### 6.5 Status section

Display:

- Current frequency set: ratios and Hz, sorted by pitch.
- Anchor ratio (or `none`), fundamental.
- Bridge control state (the knobs' values), so a UI restart can resync.
- CPU percent.
- Loop states.
- MIDI: enabled/disabled, drone port, arp port.

Data sources:

- Bridge → `/ji/freqset/changed` `[fundamental, anchor, ratio_1 ... ratio_N]`;
  anchor is `-1.0` when there is no anchor.
- Bridge → `/ji/ui/state` `[randomness, dissonance_bias, stepwise_bias,
  glide_time, change_count, temperature, anchor_mobility, max_changes]`.
- Bridge → `/ji/midi/status` `[enabled 0/1, drone_port, arp_port]`; empty
  strings when disabled.
- sclang → `/ji/cpu` `[percent]`.
- sclang → `/ji/loop/state` `[index 1..4, state string]`.
- sclang → `/ji/autotune/state` `[channel, retuneTime, mix, windowSize,
  pitchRangeLimit, tartiniThreshold, inputGain]`.

The UI should show `waiting for bridge` and `waiting for sclang` until the
first corresponding message arrives. It must not treat missing UDP messages as
a fatal error.

### 6.6 UI startup and shutdown

- On startup:
  - Start the UI listener on 9001 first, so replies cannot race it.
  - Send `/ji/ui/hello` to the bridge (9000).
  - Send `/ji/status/query` to sclang (57120).
  - Show defaults; sync from incoming state without emitting feedback OSC.
- On close:
  - Shut down the UI OSC listener cleanly.
  - Do **not** stop the bridge, audio, loops, or MIDI. The rig continues.
- If a port is already in use or PyQt6 is missing, fail with a clear message;
  do not silently use a different port.

---

## 7. Python bridge / OSC additions

Keep changes additive.

### 7.1 `control/osc_server.py`

- Add `UI_HELLO_ADDRESS = "/ji/ui/hello"`.
- Add an optional `on_ui_hello: Callable[[], None] | None = None` constructor
  argument; map the address to a handler that calls it after the server lock is
  released.
- Do not change existing mutation addresses, callbacks, or behavior.

### 7.2 `control/ji_bridge.py`

Add optional constructor/CLI values, appended after existing parameters so old
construction still works:

```text
ui_host: str = "127.0.0.1"
ui_port: int = 9001
```

- Create a UI `SimpleUDPClient` (or equivalent). Existing tests monkeypatch
  `SimpleUDPClient`; make sure the existing `bridge.client.sent` assertions still
  pass.
- Add `_send_ui_state()`:
  - `/ji/freqset/changed` with the current engine snapshot.
  - `/ji/ui/state` with the current controls and `max_changes`.
  - `/ji/midi/status` with MIDI enabled and configured ports.
- Call it:
  - after every `send_set` (including the initial set),
  - when `/ji/ui/hello` arrives.
- Send `/ji/midi/status` at bridge start even if MIDI is disabled.
- Keep `send_set`'s `/ji/freqset/update` payload exactly unchanged.
- Add `--ui-host` / `--ui-port` CLI options with defaults; existing invocation
  remains valid.
- `JIBridge.stop()` must not need to stop the UI; UI is a separate process.

### 7.3 Status message details

- `/ji/freqset/changed`: `[float fundamental, float anchor_or_-1, float ratio_1 ...]`.
- `/ji/ui/state`: `[float randomness, float dissonance_bias, float stepwise_bias,
  float glide_time, int change_count, float temperature, float anchor_mobility,
  int max_changes]`.
- `/ji/midi/status`: `[int enabled, string drone_port, string arp_port]`.

Do not send malformed/empty ratio lists; the engine always has 1..12 classes.

---

## 8. sclang control surface (`ui/control_surface.scd`)

This file is evaluated **after** `test_live_rig.scd`. It must:

- Check that the live rig globals exist; if not, print a clear error such as
  `[UI] evaluate test_live_rig.scd first` and do not install broken handlers.
- Be safe to re-evaluate: free old OSCdefs and stop the old status routine before
  installing new ones. Use distinct keys (e.g. `\ji_ui_autotune`, `\ji_ui_loop`,
  `\ji_ui_status`) so it does not conflict with `\ji_freqset_update_live_rig`.
- Send status to `~ji_ui_addr = NetAddr("127.0.0.1", 9001)` by default; expose
  `~ji_ui_host`/`~ji_ui_port` variables for manual changes.

Implement:

- `/ji/autotune/set` `[channel, param, value]`:
  - channel 0/1 selects `~ji_autotune_synth_ch0` / `~ji_autotune_synth_ch1`.
  - whitelist and clamp the six parameters.
  - use `Synth.set(param.asSymbol, value)`, never arbitrary args.
  - send `/ji/autotune/state` for that channel afterward.
- `/ji/loop/record`, `/ji/loop/record_length`, `/ji/loop/stop_record`,
  `/ji/loop/play`, `/ji/loop/stop`, `/ji/loop/stop_all`,
  `/ji/loop/clear`, `/ji/loop/set_default_length`:
  - validate index 1..4 and seconds > 0 (clamp to 0.1–60 s).
  - call the existing `~ji_*` functions; do not duplicate loop logic.
  - clear: stop the loop, free its buffer if present, set the state to `empty`.
  - send `/ji/loop/state` after every action.
- `/ji/status/query`:
  - immediately send `/ji/cpu`, both `/ji/autotune/state` messages, and all four
    `/ji/loop/state` messages.
- Status routine:
  - `/ji/cpu` at ≈2 Hz from `s.avgCPU` (0–100).
  - `/ji/loop/state` on change and at least once per second.
  - No flood: keep status traffic modest.

Use the existing loop state symbols (`\empty`, `\recording`, `\recorded`,
`\playing`, `\stopped`) converted with `.asString`.

Do not change the autotune/loop SynthDefs or the lookup/audio path.

---

## 9. Python UI layer

### 9.1 `ui/osc_control.py`

Keep this module free of PyQt6 imports so pure tests work without Qt. It should
provide:

- A small OSC controller that owns:
  - bridge client (9000),
  - sclang client (57120),
  - UI listener (9001) in a background thread,
  - callback hooks for status messages,
  - clean `start()`/`stop()`.
- Pure message builders for every control:
  - `set_mutation(param, value)`, `trigger_mutation()`, `trigger_octave()`;
  - `set_autotune(channel, param, value)`;
  - `loop_record(index, seconds=None)`, `loop_stop_record(index)`,
    `loop_play(index)`, `loop_stop(index)`, `loop_stop_all()`,
    `loop_clear(index)`, `loop_set_default_length(seconds)`;
  - `request_state()`.
- Range/clamp helpers shared by tests and widgets.
- Status parsers/updaters for `/ji/freqset/changed`, `/ji/ui/state`,
  `/ji/midi/status`, `/ji/cpu`, `/ji/loop/state`, `/ji/autotune/state`.
- Dependency injection for clients/listener so tests use fakes and never touch
  hardware.

Do not import PyQt6 here. Do not open real sockets in constructor; start them
explicitly.

### 9.2 `ui/ji_ui.py`

- PyQt6 application and one main window.
- Accept `--bridge-host/port`, `--sc-host/port`, `--ui-host/port`.
- Use the OSC controller; wire status callbacks to Qt signals, not direct widget
  access from the OSC thread.
- Block widget signals when updating controls from incoming state to avoid
  feedback loops.
- Coalesce knob sends (≈30 Hz); send buttons immediately.
- Display current values next to knobs.
- Do not add a theme pass; default Qt styling is acceptable.
- On close, stop the UI listener only. Do not stop audio/MIDI.

---

## 10. Deliverables

```text
ji-live/
├── ui/
│   ├── __init__.py
│   ├── ji_ui.py              NEW: one-window PyQt6 GUI
│   ├── osc_control.py        NEW: pure OSC message/state layer
│   └── control_surface.scd   NEW: sclang autotune/loop/status OSC
├── control/
│   ├── ji_bridge.py          MODIFIED: UI status/hello, --ui-host/--ui-port
│   └── osc_server.py         MODIFIED: /ji/ui/hello callback, additive only
├── test_ui.py                NEW: pure + optional headless Qt tests
├── README_stage7.md          NEW: setup, controls, failures, acceptance
└── design_doc_10.28.10.50    UPDATED: Stage 7 status, §8.8 details, §9, §14
```

`README_stage7.md` must document files, PyQt6 installation, start order, the
control/range table, the OSC map, failure modes, and the human acceptance
checklist.

Do not modify `synthdefs/*`, `startup.scd`, `test_live_rig.scd` audio behavior,
`test_dual_loop.scd`, `test_ableton_routing.scd`, `control/mutation.py`, or the
`/ji/freqset/update` payload/receiver.

---

## 11. Tests

### 11.1 Pure tests (no PyQt6, no hardware)

```bash
cd ji-live
uv run --with python-osc python test_ui.py --pure
```

Cover:

- Every message builder: address, argument order, int/float types, clamping.
- Mutation controls, including `change_count` bounds.
- Autotune messages for both channels and every allowed parameter; reject/clamp
  invalid params/ranges.
- Loop messages for record, record_length, stop_record, play, stop, stop_all,
  clear, set_default_length.
- Status parsing:
  - `/ji/freqset/changed` with anchor and with `-1.0`;
  - `/ji/ui/state` including `max_changes`;
  - `/ji/midi/status` enabled/disabled;
  - `/ji/cpu`, `/ji/loop/state`, `/ji/autotune/state`.
- Bridge UI status: construct `JIBridge` with fake `SimpleUDPClient`s; call
  `send_set`, `_send_ui_state`, and the UI-hello callback; assert the bridge
  still sends exactly one `/ji/freqset/update` per set and that UI messages are
  well formed.
- `--pure` exits nonzero on failure.

### 11.2 Optional headless Qt smoke test

```bash
cd ji-live
QT_QPA_PLATFORM=offscreen uv run --with python-osc --with PyQt6 \
  python test_ui.py --qt
```

- Construct the main window with a fake OSC controller.
- Verify the four sections/widgets exist.
- Move a knob and click a button; assert the fake controller received the
  expected messages.
- Close the window and assert the listener stopped.
- Skip with a clear message if PyQt6 is unavailable; do not make `--pure` depend
  on PyQt6.

### 11.3 Existing tests

```bash
cd ji-live
uv run --with python-osc python test_mutation.py
uv run --with python-osc python test_mutation_controls.py
python test_mpe.py --pure
```

All must pass. MIDI must remain off by default and `JIBridge(...)` must still
construct without mido.

---

## 12. Run order

1. Evaluate `startup.scd`.
2. Evaluate `test_live_rig.scd`.
3. Start the bridge (add `--midi` for Stage 6):
   `uv run --with python-osc --with mido --with python-rtmidi python control/ji_bridge.py --interactive --midi --glide-time 0.15`
4. Evaluate `ui/control_surface.scd`.
5. Launch the GUI:
   `uv run --with python-osc --with PyQt6 python ui/ji_ui.py`
6. Keep the GUI open for the performance. Closing it must not stop the bridge,
   audio, loops, or MIDI.

If `test_live_rig.scd` is re-evaluated, re-evaluate `ui/control_surface.scd`
afterward.

---

## 13. Human acceptance

1. One window opens with Mutation, Live Autotune, Loops, and Status sections.
2. The frequency set, anchor, fundamental, CPU, loop states, and MIDI status
   update live; before the bridge/sclang is up, the UI shows a waiting state and
   does not crash.
3. Mutation knobs and Mutate/Octave buttons change the audio/mutation behavior
   exactly as the existing terminal controls do.
4. Live-autotune knobs audibly change retuneTime/mix/window/range/threshold/gain;
   Link sends both channels.
5. Loop 1–4 Record / Stop Rec / Play / Stop / Clear work; the length knob sets
   the take length; states update; no clicks or stuck loops.
6. The Stage 6 drone/arp still works and does not gain stuck notes.
7. Closing the UI leaves the audio/MIDI rig running.
8. No audio dropouts, no Qt freeze under live use; CPU remains within the live
   budget.
9. Record the result. **Stage 7 is not complete until the human confirms the
   GUI controls the rig and the audio/MIDI behavior is unchanged.**

---

## 14. Self-review requirement (final response)

Provide:

- Files added/changed.
- For each requirement above, the code that satisfies it or a concrete concern.
- "If I had to bet on which line is most likely to fail on the human's machine,
  which is it, and why? Give a one-line verification command." Candidates: a
  PyQt6 install/runtime issue, the UI OSC port already in use, a sclang control
  surface evaluated before `test_live_rig.scd`, or a loop state/record timing
  mismatch.
- Assumptions, max 5 bullets.
- Explicit statement that the GUI/audio was not tested by the agent unless the
  human reports it; Stage 7 awaits human acceptance.

---

## 15. One-line sanity checks

```bash
cd ji-live
python test_mpe.py --pure
uv run --with python-osc python test_ui.py --pure
uv run --with python-osc --with PyQt6 python -c "import PyQt6; print('PyQt6 ok')"
```

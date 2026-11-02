# JI Station — Stage 8.3b: arp parameters + one-entry startup

**Stage 8.3c follows this stage** (beat-synced arp + self-evolution, both off by
default): see [README_stage8_3c.md](README_stage8_3c.md). It extends the Arp
group and `/ji/arp/state` (17 fields) without changing anything described here.

**Status:** implemented by the agent, **awaiting human acceptance**. Audio, MIDI,
Ableton, IAC, sclang timing, and the live GUI were **not** tested by the agent
(no audio hardware, no IAC buses, no SuperCollider in the agent environment).
The owner is the authority; 8.3b is not complete until the checklist in §7 is
confirmed.

Two deliverables:

1. **A clean startup path** — one sclang file (`ji_start.scd`) and one Python
   command (`run/ji_station.py`), plus `docs/START_HERE.md`, the authoritative
   start/stop/flag sheet.
2. **Mutable arp parameters** — step, gate, velocity, three positional octave
   offsets, shared mode, playback order, and seed, exposed through OSC, the
   bridge, the GUI and `config/mpe_config.json`, beat-quantized like the other
   performance controls.

Stage 8.3a bass behavior, the Stage 5.5 audio rig, the Stage 6 MPE bus/channel/
bend contracts, and `control/mutation.py` are unchanged.

---

## 1. Files

```text
ji-live/
├── ji_start.scd                  NEW  one sclang entry point (loads the rest)
├── run/
│   ├── __init__.py               NEW  package marker (python -m run.ji_station)
│   └── ji_station.py             NEW  one Python launcher (bridge + GUI)
├── control/
│   ├── arp.py                    MOD  ArpSettings, orders, setters, logging
│   ├── midi_rig.py               MOD  arp_order config, arp_state(), setters
│   ├── quantize.py               MOD  PHASE_ARP (between bass and midi)
│   ├── osc_server.py             MOD  /ji/arp/set_* handlers
│   └── ji_bridge.py              MOD  arp request queue + /ji/arp/state
├── config/mpe_config.json        MOD  additive "arp_order": "forward"
├── ui/
│   ├── osc_control.py            MOD  arp builders/parsers/status/store
│   └── ji_ui.py                  MOD  compact Arp group
├── tests/
│   ├── test_arp.py               NEW  settings/orders/timing/rig/bridge tests
│   ├── test_startup.py           NEW  launcher/start-file/docs checks
│   └── test_ui.py                MOD  arp builders + Qt coverage
├── docs/
│   ├── START_HERE.md             NEW  authoritative start/stop + flags
│   ├── README_stage8_3b.md       NEW  this file
│   ├── README_stage8_3.md        MOD  link to 8.3b
│   └── README_mpe.md             MOD  arp controls/timing note
├── docs/design_doc_10.28.10.50   MOD  §6.13, §6.17, §7.1, §8.9, §9, §14
└── README.md                     MOD  two-step start + links + status
```

---

## 2. One-entry startup

### 2.1 `ji_start.scd`

Evaluate it from a fresh interpreter; nothing else on the sclang side is needed.

1. Captures the project root from `thisProcess.nowExecutingPath` (and stops an
   earlier `~ji_start_routine` so re-evaluating cannot stack waiters).
2. Verifies that `startup.scd`, `tests/sc/test_live_rig.scd`,
   `ui/control_surface.scd` and `run/ji_station.py` exist — a missing file is a
   specific `*** [JI] startup error` and the rig is not installed.
3. Loads `startup.scd`, which sets `ServerOptions`, allocates buses 0–18, loads
   `synthdefs/*.scd`, and **boots the server itself** via `s.waitForBoot`.

   **Boot ownership:** `ji_start.scd` does not call `s.boot` directly; the
   proven `startup.scd` path owns the boot. The master file waits for it.
4. Waits (25 s limit) for `s.serverRunning`, then calls `s.sync` to flush the
   server command queue so every SynthDef is compiled, then polls until the
   required SynthDefs *and* the Stage 4 buses exist (10 s limit).
5. Loads `tests/sc/test_live_rig.scd`, `s.sync`, then `ui/control_surface.scd`.
6. Prints one `[JI] sclang rig + control surface ready ...` line with the exact
   launcher command and the reminder that the bridge starts **after** the rig.

No audio code is duplicated: the master file only loads the accepted files.
Re-evaluating it is safe because those files already stop their routines, free
their synths/buffers, and free their OSCdefs; `startup.scd` re-uses the control
buses instead of reallocating them.

The three old files remain independently evaluable for debugging and for the
`tests/sc/*` human checks. They are not deprecated, just no longer the daily
path; `docs/START_HERE.md` says so.

### 2.2 `run/ji_station.py`

One command starts the bridge and then the GUI as child processes of the same
`uv --with ...` interpreter (`sys.executable`), so no environment is lost.

- Defaults match the owner's rig: `--midi --drums --bass` on.
- `--no-midi`, `--no-drums`, `--no-bass`, `--no-gui`, `--no-bridge`,
  `--interactive`, `--glide-time`, `--listen-port`, `--ui-port`, `--sc-port`,
  `--*-host`, `--mpe-config`, `--rhythm-config`, `--bass-config`,
  `--bridge-args "..."`, `--dry-run`, `--check`.
- Prints every child command line with a `[LAUNCH]` prefix so a failure is
  reproducible from the terminal.
- Starts the bridge first and waits for a readiness window (1 s alive) before
  starting the GUI; one second after the GUI starts it sends a best-effort
  `/ji/ui/hello` so a slow bridge still fills the window.
- Closing the GUI keeps the bridge running. `Ctrl+C`/SIGTERM stops the children
  (SIGTERM, wait 5 s, kill) and prints `[LAUNCH] stopped`.
- An early bridge exit prints its code and stops the GUI; a dead GUI is never
  left behind.
- `--dry-run` prints the plan and exits 0 without importing PyQt6 or mido.
- `--check` validates imports, parses all three config files, and lists the
  available mido output names, then exits.
- Never starts sclang; always prints the reminder.

The direct commands (`python control/ji_bridge.py ...`, `python ui/ji_ui.py ...`)
are unchanged.

---

## 3. Arp parameters

### 3.1 Defaults (unchanged Stage 6 sound)

```text
step_seconds      0.25
gate_seconds      0.20
velocity          90
octave_offsets    [0, 1, 2]     (shared, non_shared_1, non_shared_2)
shared_mode       anchor
order             forward
seed              1
```

`config/mpe_config.json` stays the source of defaults; `"arp_order": "forward"`
is the only additive key. `MPEConfig` treats a missing `arp_order` as
`"forward"`, so an older config still loads.

With those defaults the pattern, the six-step sequence, the 0.25 s / 0.20 s
timing, the note/bend messages, and the per-pattern log line are the Stage 6
behavior: `SetArp.on_set` still consumes one `random.Random(seed)` exactly like
before, and the order RNG is a separate, derived stream.

### 3.2 Controls

| Control | Range | Notes |
|---|---|---|
| Step seconds | 0.05–2.0 | time between steps; free-run, **not** tempo-synced |
| Gate seconds | 0.01–2.0 | clamped to ≤ step (clamp is logged); does not cut the held note |
| Velocity | 1–127 | note-on velocity |
| Octave offsets | three ints −8..+8 | positional `(shared, non_shared_1, non_shared_2)`; the shared offset is identical in A and B |
| Shared mode | `anchor`, `random` | existing Stage 6 semantics |
| Order | `forward`, `reverse`, `pingpong`, `random` | new |
| Seed | 0..2³¹−1 | reproducible shared pick, pitch picks, and random order |

Out of scope for 8.3b (deferred): tempo-syncing the arp to BPM, swing,
rests/probability, changing the 3+3 group size, polyphony/MPE voice allocation,
and any change to the Stage 6 bend range.

### 3.3 Order definitions

Over the stored six steps `[A1, A2, A3, B1, B2, B3]` (indices 0–5):

| Order | Sequence |
|---|---|
| `forward` | `0,1,2,3,4,5` (then repeat) |
| `reverse` | `5,4,3,2,1,0` (then repeat) |
| `pingpong` | `0,1,2,3,4,5,4,3,2,1` (ten steps; endpoints are not repeated back to back) |
| `random` | a seeded permutation of `0..5`, derived from the seed **and** the stored pattern |

`random` is generated when the pattern, order, or seed changes; the same seed
plus the same pattern always reproduces the same permutation
(`sha256(seed:pattern)` seeds a private RNG).

**Cursor reset:** any pattern/order/seed change resets the step cursor to the
first element of the new order. The change is stored immediately and applied at
the next trigger — the currently held note is never cut or retriggered.

`step`/`gate`/`velocity` changes apply from the next trigger without rebuilding
the pattern. `octave_offsets`/`shared_mode`/`seed` rebuild the pattern from the
last stored `(ratios, anchor, fundamental)` snapshot, so a parameter change does
**not** require a new mutation. `order` rebuilds only the visiting sequence.

### 3.4 Timing / thread safety

`SetArp` now owns an immutable `ArpSettings` snapshot read under one `RLock`:

- the thread copies `(pattern, order, step, gate, velocity)` in one locked read,
  so a change can never be observed half-applied;
- the drift-corrected deadline behavior is unchanged, and a late thread resumes
  from *now* instead of bursting through missed steps;
- a gate longer than the step is clamped explicitly and logged;
- every exit path (stop, disable, error) note-offs the held note, and `stop()`
  also sends CC 123.

Per-step logging is **opt-in** (`SetArp(..., verbose_steps=True)`); the default
log is one line per applied change plus the first scheduled step afterwards:

```text
[ARP] change applied: order=pingpong
[ARP] state step=0.250 gate=0.200 velocity=90 octaves=0,1,2 shared=anchor order=pingpong seed=1
[ARP] first step after change: order=pingpong position=1 freq=220.00 note=57
```

---

## 4. OSC / bridge / quantization

New addresses (UI → bridge):

```text
/ji/arp/set_step          float seconds 0.05..2.0
/ji/arp/set_gate          float seconds 0.01..2.0 (clamped to <= step)
/ji/arp/set_velocity      int 1..127
/ji/arp/set_octaves       three ints -8..8 (shared, non_shared_1, non_shared_2)
/ji/arp/set_shared_mode   string anchor|random
/ji/arp/set_order         string forward|reverse|pingpong|random
/ji/arp/set_seed          int 0..2^31-1
```

Status (bridge → UI):

```text
/ji/arp/state  int enabled, int port_ok, float step_seconds, float gate_seconds,
               int velocity, int oct_shared, int oct_non_shared_1,
               int oct_non_shared_2, string shared_mode, string order, int seed
```

- There is **no** second enable path: the Stage 7.1 top-row Drone/Arp switches
  and `/ji/midi/set_enabled` remain the only enable/disable controls.
- `PHASE_ARP` sits between `bass` and `midi` in the beat queue
  (`mutation, octave, groove, drums, bass, arp, midi`). While the transport
  runs, arp changes wait for the next quarter beat; while it is stopped they
  apply immediately. Keys coalesce last-wins; different keys all run.
- Tempo/meter/transport stay immediate; beat-mode loop Record/Play are not
  double-queued.
- `JIBridge` sends `/ji/arp/state` at start, on `/ji/ui/hello`, after every
  applied arp change, and after the Drone/Arp switch applies. It is one small
  message per change — never per step.
- With `--midi` off, or Bus 2 unavailable, the handlers log a specific line and
  leave drone/drums/bass/audio running; the UI shows a disabled Arp group.

---

## 5. GUI

A compact **Arp** group sits below **Bass** (no tabs, no redesign):

- Step / Gate / Vel knobs (32 px, 8 pt); Order and Shared combos; Seed spin.
- Three octave spins labelled `sh`, `1`, `2` (−8..+8).
- Status line `Arp: on | 0.25 s / 0.20 s | forward | anchor | seed 1`; when the
  bus is unavailable it reads `Arp: disabled (MIDI/Bus 2 not available; see
  bridge log)` and every control is disabled.
- Knobs coalesce at ~30 Hz through the existing pending timer; combos and spins
  send immediately. Incoming `/ji/arp/state` syncs with `_arp_sync` set, so no
  feedback OSC is emitted.
- The window stays within the compact budget (8 pt font, 32 px knobs, scroll
  area); the Arp group adds two rows plus a status line, the same height as Bass,
  and does not push Status or the other groups off-screen.
- The shared pending/next-beat line reflects queued arp changes while running.

---

## 6. Tests

```bash
cd ji-live
uv run --with python-osc python tests/test_arp.py
uv run --with python-osc python tests/test_startup.py
uv run --with python-osc python tests/test_mutation.py
uv run --with python-osc python tests/test_mutation_controls.py
uv run --with python-osc python tests/test_rhythm.py
uv run --with python-osc python tests/test_groove.py
uv run --with python-osc python tests/test_quantize.py
uv run --with python-osc python tests/test_bass.py
python tests/test_mpe.py --pure
uv run --with python-osc python tests/test_ui.py --pure
# optional, needs PyQt6:
QT_QPA_PLATFORM=offscreen uv run --with python-osc --with PyQt6 \
  python tests/test_ui.py --qt
```

`tests/test_arp.py` covers defaults, validation/clamps, the four exact order
sequences, random determinism, shared-note identity, next-step application,
long/short gate, no-burst, no-stuck-note, pattern regeneration without a
mutation, `MIDIRig` state/setters, `midi=False`, a missing Bus 2, the OSC
handlers, the 11-field state, bridge quantize/coalesce/flush, and no mido import.

`tests/test_startup.py` covers the launcher argv builders (`--dry-run`,
`--check`, `--no-*`, `--bridge-args`), the `ji_start.scd` load order, and the
presence of the documented flags in `docs/START_HERE.md`.

---

## 7. Human acceptance checklist

Startup:

1. From a **fresh interpreter**, evaluate `ji_start.scd` only. It boots the
   server, loads the SynthDefs, builds the live rig, installs the control
   surface, and prints one `[JI] ... ready ...` line. No other sclang file is
   needed.
2. In one terminal, `run/ji_station.py` starts the bridge and the GUI. Closing
   the GUI keeps the bridge running. `Ctrl+C` stops both and prints
   `[LAUNCH] stopped`.
3. `--dry-run`, `--check`, `--no-midi`, `--no-drums`, `--no-bass`, `--no-gui`
   and `--no-bridge` behave as documented.
4. `docs/START_HERE.md` is short, complete, and its commands work verbatim.

Arp:

5. With the defaults (0.25/0.20, forward, anchor, `[0,1,2]`, seed 1) the arp
   sounds exactly as before.
6. Step / gate / velocity / octave / shared / order / seed changes are audible
   from the GUI; while the transport runs they apply at the next quarter beat,
   while it is stopped immediately.
7. `forward`, `reverse`, `pingpong` and `random` produce the documented
   sequences; `random` is reproducible when you return to the same seed.
8. Gate changes, stop, disable, and pattern changes never leave a stuck note or
   cut the next note with a stale note-off.

No regressions:

9. Drone/Arp switches, drums, bass, groove, quantized state, mutation,
   autotune, loops, and the existing GUI behave as in 8.3a; the window stays
   compact.
10. Record the result. **8.3b is not complete until the human confirms the
    startup path and the arp controls and no audio/MIDI regressions.**

---

## 8. Decisions and risks

- **One entry point, no forked audio code.** `ji_start.scd` loads the accepted
  files; it never re-implements the DSP, so the Stage 5.5/8.1 behavior and the
  `tests/sc/*` checks stay authoritative. The old files stay runnable.
- **Boot ownership.** `startup.scd` (via `s.waitForBoot`) owns the boot;
  `ji_start.scd` waits, `s.sync`s, and verifies the SynthDefs and Stage 4 buses
  before installing the rig. If the owner's SC version cannot boot from
  `waitForBoot`, the fallback is to press Boot once first — the master file then
  still works, it simply does not have to boot.
- **Launcher process model.** Bridge and GUI stay separate processes; the
  launcher supervises them and never merges event loops. Closing the GUI does not
  kill the bridge.
- **Free-run arp.** Step/gate are seconds, not tempo divisions. Tempo sync is
  deferred, not silently added.
- **Order/pattern phase.** Documented in §3.3, including the cursor reset.
- **Gate changes.** The held note keeps its gate; the new gate applies from the
  next trigger. A gate longer than the step is clamped (and logged), never
  allowed to overlap the next step.
- **Quantization latency.** Arp changes can wait up to one quarter beat while
  running (3 s at 20 BPM). Stopping the transport makes them immediate.
- **GUI density.** The Arp group is two rows plus one status line; if a future
  stage adds another row, fold or scroll rather than widen the window.
- **Docs drift.** `docs/START_HERE.md` and the `--help` text are part of the
  acceptance, not an afterthought.

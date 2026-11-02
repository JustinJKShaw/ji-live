# JI Station — Stage 8.3c: beat-synced arp + self-evolution

**Status:** implemented by the agent, **awaiting human acceptance**. Audio, MIDI,
Ableton, IAC, sclang timing, and the live GUI were **not** tested by the agent.
The owner is the authority; 8.3c is not complete until the checklist in §7 is
confirmed.

Two additions to the Stage 8.3b arp, both **off by default** so the accepted
free-running arp is unchanged until you switch them on:

1. **Beat sync** — the arp can snap to the bridge tempo instead of the free
   seconds-based step: `free | beat`, with a `1/4`, `1/8`, `1/16`, `1/8T`
   division. The next step lands on the division boundary.
2. **Self-evolution** — the arp can regenerate its own pattern from the current
   frequency set, with no mutation, every N quarter beats, with a depth control
   for how much changes.

Everything from 8.3b (step/gate/velocity/octaves/shared/order/seed, the beat
queue, `/ji/arp/*`, the compact Arp group) still works the same way.

**Stage 8.3d (built, awaiting acceptance) does not change the arp.** It adds a
separate lead/melody voice on `IAC Driver Bus 5` and drone pad modes
(`hold`/`pulse`/`bend`) on Bus 1; the arp keeps free-running by design while the
lead is transport-gated. See
[README_stage8_3d.md](README_stage8_3d.md).

---

## 1. Files

```text
ji-live/
├── control/
│   ├── arp.py                    MOD  sync mode/division, BPM + phase snap,
│   │                                  evolution clock/depth, 17-field state
│   ├── midi_rig.py               MOD  config keys, set_arp_bpm/transport,
│   │                                  sync_arp_phase
│   ├── ji_bridge.py              MOD  forwards BPM/transport, re-phases on
│   │                                  sync/division/transport/enable
│   └── osc_server.py             MOD  /ji/arp/set_sync|set_division|set_evolve|
│                                      set_evolve_beats|set_evolve_depth
├── config/mpe_config.json        MOD  additive arp_sync, arp_division,
│                                      arp_evolve, arp_evolve_beats,
│                                      arp_evolve_depth
├── ui/
│   ├── osc_control.py            MOD  builders/validators, 17-field status
│   └── ji_ui.py                  MOD  Sync + Div combos, Evolve row, gating
├── tests/
│   ├── test_arp.py               MOD  sync math, phase snap, evolution,
│   │                                  bridge snap/BPM forwarding
│   └── test_ui.py                MOD  builders, parser, Qt group
├── run/ji_station.py             MOD  --check prints sync/evolve
├── docs/README_stage8_3c.md      NEW  this file
├── docs/START_HERE.md            MOD  arp sync/evolve note
├── docs/README_stage8_3b.md      MOD  link to 8.3c
├── docs/README_mpe.md            MOD  arp sync/evolve section
├── docs/design_doc_10.28.10.50   MOD  §6.13, §7.1, §7.5, §8.9, §9, §14
└── README.md                     MOD  status + link
```

No sclang file changed. `ji_start.scd`, `startup.scd`,
`tests/sc/test_live_rig.scd`, `ui/control_surface.scd`, `/ji/freqset/update`,
the Stage 5.5 audio map, and `control/mutation.py` are untouched.

---

## 2. Beat sync

### 2.1 Model

`sync_mode` is `free` (default) or `beat`.

| Mode | Step | Phase |
|---|---|---|
| `free` | the **Step** knob in seconds (0.05–2.0), exactly 8.3b | free-running; no snap |
| `beat` | derived from the bridge BPM × the **Div** combo | snaps to the next division boundary |

| Division | Quarter notes per step | at 120 BPM | at 60 BPM | at 300 BPM |
|---|---|---|---|---|
| `1/4` | 1.0 | 0.500 s | 1.000 s | 0.200 s |
| `1/8` | 0.5 | 0.250 s | 0.500 s | 0.100 s |
| `1/16` | 0.25 | 0.125 s | 0.250 s | 0.050 s |
| `1/8T` | 1/3 | 0.167 s | 0.333 s | 0.067 s |

`1/8` at 120 BPM is **exactly** the free-mode default (0.25 s), so switching to
beat mode at the default tempo changes the phase, not the feel.

The derived step is clamped to the same 0.05–2.0 s hardware range as free mode
(e.g. at 20 BPM, `1/4` would be 3 s and is clamped to 2 s).

The **Gate** stays in seconds and is clamped to the *effective* step, so a
`0.20 s` gate at `1/16`/120 BPM becomes `0.125 s` (the state echo shows the value
in effect). A change of division therefore shortens the gate, never overlapping
the next step.

### 2.2 Phase

The bridge owns the canonical BPM/meter/transport and the beat clock, so it owns
the phase. It asks the arp to snap when:

- sync is switched to `beat`,
- the division changes,
- the transport starts (Pulse/Play),
- the Drone/Arp switch re-enables the arp,
- the bridge starts with `arp_sync: "beat"` in the config.

The snap target is `rhythm.next_beat_time(division_quarters)` — the next
boundary of that division on the transport grid. The arp waits for it, restarts
the order at position 0, and takes the next step from there. A held note keeps
its gate; the snap lands at the next step boundary.

With the transport **stopped** there is no beat to lock to, so beat mode runs
at the current BPM and starts the grid immediately (the status line still shows
`beat 1/8 = …`). The arp is not muted by the transport stop — the Drone/Arp
switch remains the only mute.

A tempo change does **not** re-phase (the grid stays aligned; only the step
duration changes).

---

## 3. Self-evolution

`evolve`, `evolve_beats` (1–64), `evolve_depth` (0–1). Off by default.

Every `evolve_beats` **quarter beats** — `evolve_beats * 60 / bpm` seconds, so
`4` at 120 BPM is 2 s — the arp regenerates its pattern **from the last
frequency-set snapshot**: no mutation, no sclang message, no new set.

| Depth | What one evolution changes |
|---|---|
| `0.0` | re-picks the four non-shared notes only; the shared note keeps following `shared_mode`; octaves fixed |
| `0 < d ≤ 1` | as above, plus with probability `d` the shared note may move freely for that evolution, and with probability `d/2` one octave offset moves by ±1 (clamped to −8..+8) |

- The **set itself never changes**; evolution re-picks within the current
  classes.
- An evolution restarts the order at position 0 and continues from there; it
  never cuts the held note and never leaves a stuck note.
- Evolution works in both sync modes (the clock is the BPM, not the arp step).
- Determinism: the evolution RNG is `sha256(f"ji-arp-evolve|{seed}|{count}")`, so
  the same seed reproduces the same evolution sequence (absent other pattern
  rebuilds, which also advance the pattern RNG).
- Turning `evolve` on, changing its rate/depth, or changing the seed restarts the
  countdown.
- One log line per evolution, never per step:

```text
[ARP] evolved #3 depth=0.30 shared=anchor shared_ratio=3/2 octaves=0,1,2
[ARP] evolved #4 depth=0.30 shared=random shared_ratio=5/3 octaves=0,1,3 octave[2]=+1
```

There is also `SetArp.evolve_now()` (regenerate immediately, returns how many
evolutions ran) — used by the tests and handy for a future "evolve now" control;
the timed path is the same code.

---

## 4. OSC

| Address | Args | Direction |
|---|---|---|
| `/ji/arp/set_sync` | string `free`\|`beat` (or 0/1) | UI → Bridge |
| `/ji/arp/set_division` | string `1/4`\|`1/8`\|`1/16`\|`1/8T` | UI → Bridge |
| `/ji/arp/set_evolve` | int 0/1 | UI → Bridge |
| `/ji/arp/set_evolve_beats` | int 1..64 | UI → Bridge |
| `/ji/arp/set_evolve_depth` | float 0..1 | UI → Bridge |

`/ji/arp/state` grew from 11 to **17** values (the Stage 8.3b fields keep their
positions up to `seed`, then the new ones follow):

```text
 0 int    enabled
 1 int    port_ok
 2 float  step_seconds        (free-mode step setting)
 3 float  step_effective      (what the thread uses: derived in beat mode)
 4 float  gate_seconds        (effective, <= step_effective)
 5 int    velocity
 6 int    oct_shared
 7 int    oct_non_shared_1
 8 int    oct_non_shared_2
 9 string shared_mode
10 string order
11 int    seed
12 string sync_mode          free|beat
13 string division           1/4|1/8|1/16|1/8T
14 int    evolve              0|1
15 int    evolve_beats        1..64
16 float  evolve_depth        0..1
```

All five new setters are queued on `PHASE_ARP` like the other arp parameters:
immediate while the transport is stopped, at the next quarter beat while it runs.
The phase snap itself happens when the change is **applied**, so it snaps from
the moment you hear it.

---

## 5. GUI

The compact Arp group keeps three rows; the new controls share the existing ones:

```text
row 1  [Step][Gate][Vel]   Sync[free|beat]  Div[1/4|1/8|1/16|1/8T]  Order[..]  Shared[..]
row 2  [x] Evolve  Every [ 4 b]  (Depth)   Oct sh[0] 1[1] 2[2]   Seed [1]
row 3  Arp: on | beat 1/8 = 0.25 s / 0.20 s | forward | anchor | seed 1
                 | evolve 4 b depth 0.30          (only while evolving)
```

Gating:

- Steps knob is enabled in `free` and disabled in `beat` (the value comes from
  the BPM, and the derived step is in the status line).
- Div is enabled only in `beat`.
- Every/Depth are enabled only while Evolve is checked.
- Everything is disabled while the bridge reports `port_ok = 0`.
- Knobs coalesce at ~30 Hz; combos, the checkbox and spins send immediately;
  incoming state syncs with signals blocked (no feedback OSC).

---

## 6. Tests

```bash
cd ji-live
uv run --with python-osc python tests/test_arp.py
uv run --with python-osc python tests/test_ui.py --pure
uv run --with python-osc python tests/test_mutation.py
uv run --with python-osc python tests/test_mutation_controls.py
uv run --with python-osc python tests/test_rhythm.py
uv run --with python-osc python tests/test_groove.py
uv run --with python-osc python tests/test_quantize.py
uv run --with python-osc python tests/test_bass.py
uv run --with python-osc python tests/test_startup.py
python tests/test_mpe.py --pure
# optional, needs PyQt6:
QT_QPA_PLATFORM=offscreen uv run --with python-osc --with PyQt6 \
  python tests/test_ui.py --qt
```

`tests/test_arp.py` adds: the four division values and the BPM math (including
clamps), free-mode independence from BPM, gate ≤ effective step, sync validation
and aliases, the phase snap (first step delayed to the boundary, re-phase while
running, free mode ignores it), evolution depth 0 vs 1, evolution determinism by
seed, the evolution period/clock, timed evolution in the real thread, and the
no-stuck-note invariant across evolutions, plus bridge BPM forwarding and the
snap triggers.

---

## 7. Human acceptance checklist

1. Arp group defaults are unchanged: `Sync free`, `Div 1/8`, `Evolve off`; with
   no other change the arp sounds exactly as in 8.3b.
2. Switch `Sync` to `beat`: the arp re-starts on the next beat, and the status
   line reads `beat 1/8 = 0.25 s / …`. Change Div to `1/16` and `1/8T` and hear
   the rate follow the tempo; change the BPM (Rhythm group) and hear the step
   follow while staying on the grid.
3. Press Play/Stop on the transport: the synced arp re-locks at the start, and
   keeps running (tempo-relative) when the transport stops.
4. The Step knob is disabled in beat mode and usable again in free mode.
5. Tick `Evolve`, set `Every 4 b`, Depth 0: the arp keeps its shared note and
   changes the other notes every bar (at 120 BPM, 2 s). Raise Depth to 1: the
   shared note and the octaves drift too. Untick and the arp stays put.
6. No stuck notes when evolving, when switching sync on/off, when changing
   division, or when stopping; a gate change never cuts the held note.
7. Drone/Arp switches, drums, bass, groove, quantize, mutation, autotune, loops,
   and the rest of the GUI do not regress; the window stays compact.
8. Record the result. **8.3c is not complete until the human confirms both
   behaviors and no regressions.**

---

## 8. Decisions / limitations

- **Sync is tempo-relative, not transport-gated.** With the transport stopped,
  beat mode keeps a `1/8` grid at the current BPM instead of going silent; the
  Drone/Arp switch is still the only mute. This was chosen so a stopped
  transport cannot silently kill the arp mid-set.
- **The bridge owns the phase.** The arp module has no beat clock; it derives
  the step from the BPM it is fed and takes a one-shot snap request. That keeps
  `control/arp.py` free of bridge dependencies and one source of truth for the
  beat.
- **Gate stays in seconds.** Beat mode clamps it to the derived step rather than
  introducing a second gate unit; the state echo shows the clamped value.
- **Evolution re-picks within the set.** It never mutates the frequency set, so
  the arp can drift for a long time without touching the harmonic material. A
  set update still regenerates the pattern as before.
- **Evolution phase:** an evolution restarts the six-step order at position 0.
  At musical rates that is a no-op on the grid (4 beats = 8 eighths), and in
  free mode it re-phases by less than one step.
- **No triplet-swing or dotted divisions** beyond `1/8T`; swing is still out of
  scope (the drums keep their own swing).

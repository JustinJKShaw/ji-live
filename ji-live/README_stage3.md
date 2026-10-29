# JI Station — Stage 3 (mutation → autotune bridge)

**Status:** the Stage 3 bridge is accepted by the project owner (audio and
Python↔sclang communication working). The mutation-control extension in §12
is implemented and awaiting human audio acceptance; its Python harness passes.

Stage 3 connects the Stage 2 mutation engine to the Stage 1 autotune chain.
The FrequencySet stays in Python. Every applied mutation sends the complete
set to sclang, which updates the already-verified nearest-class lookup. A
sustained input therefore retunes on the next lookup tick — no replay, no
note-on, no direct scsynth message.

```text
held input
  → Tartini (in \ji_autotune_ch)
  → nearest class in the current set, in the input's own octave
  → targetFreq / PitchShift
  → mutation arrives over OSC
  → set updates; next lookup finds the new nearest class
  → retuneTime is set from the event's glide_time, then targetFreq changes
```

---

## 1. Files

```text
ji-live/
├── control/
│   ├── ji_bridge.py       # NEW: engine + OSC listener + /ji/freqset/update
│   ├── osc_server.py      # MODIFIED: public trigger()/octave()/glide setters,
│   │                      #           new controls, and callbacks
│   └── mutation.py        # EXTENDED: MutationBatch, temperature, anchor mobility
├── test_bridge.scd        # NEW: sclang receiver + lookup + status/CPU
├── test_mutation_controls.py  # NEW: batch/temperature/anchor-mobility harness
└── README_stage3.md       # NEW: this file
```

Unchanged: `startup.scd`, `synthdefs/ji_autotune_ch.scd`, `test_autotune.scd`,
and the Stage 2 `test_mutation.py` harness (its default path still passes).

There is deliberately **no** `synthdefs/ji_freqset_receiver.scd`. The OSC
receiver in `test_bridge.scd` replaces that old server-side lookup deliverable.
A 12-slot Hz Buffer mirror is written for future stages, but no SynthDef reads
it in Stage 3.

---

## 2. Prerequisites

- macOS, SuperCollider 3.15 dev, sc3-plugins/Tartini verified (Stages 0–1).
- `startup.scd` evaluated: server booted, buses 0–7 allocated, and
  `synthdefs/ji_autotune_ch.scd` loaded.
- Python 3.11+ available through `uv`; `python-osc` is fetched by `uv run`.
- Headphones. The input is live and the output is a pitch-shifted copy of it;
  speakers will feed back.
- No Stage 0/1 autotune test left running. `test_bridge.scd` stops the known
  Stage 0/1/3 variables, but if you started a Synth under a different variable,
  free it yourself.

---

## 3. Start order

### a. SuperCollider: boot the rig

Evaluate `startup.scd` (Cmd+Return). Confirm the bus line and that
`ji_autotune_ch.scd` was loaded.

If a Stage 0/1 test is still running, stop it first:

```supercollider
~ji_tartini_routine.stop;
~ji_cpu_routine.stop;
~ji_tartini_debug_synth.free;
~ji_lookup_routine.stop;
~ji_cpu_routine_stage1.stop;
~ji_autotune_synth.free;
```

### b. SuperCollider: start the Stage 3 receiver

Evaluate the whole of `test_bridge.scd` (Cmd+Return). It:

- checks the server and the Stage 1 buses;
- stops the known old Stage 0/1/3 routines and synth;
- prints `NetAddr.langPort` (normally `57120`);
- resets the seven-class Stage 1 set at A3 = 220 Hz;
- allocates `~ji_buf_freqset = Buffer.alloc(s, 12, 1)`;
- installs the `/ji/freqset/update` `OSCdef`;
- starts `\ji_autotune_ch` on input 0 → output 0, buses 5/6;
- starts the 20 Hz nearest-class lookup and the 1 s CPU/status lines.

### c. Terminal: start the Python bridge

From `ji-live/`:

```bash
uv run --with python-osc python control/ji_bridge.py --interactive --glide-time 0.15
```

Defaults: listener `127.0.0.1:9000`, destination `127.0.0.1:57120`, seed `1`,
glide `0.15`, change count `1`, max changes `3`, temperature `0.0`, anchor
mobility `0.25`. The options are:

```text
--sc-host 127.0.0.1
--sc-port 57120
--listen-host 127.0.0.1
--listen-port 9000
--seed 1
--glide-time 0.15
--max-changes 3
--change-count 1
--temperature 0.0
--anchor-mobility 0.25
--interactive
```

Without `--interactive`, the bridge runs headless until Ctrl+C.

---

## 4. OSC contract

### 4.1 External control → Python (port 9000)

The Stage 2 addresses are unchanged:

| Address | Args | Effect |
|---|---|---|
| `/ji/mutation/trigger` | none | propose/apply one add, remove, or replace |
| `/ji/mutation/octave` | none | propose/apply one analysis-voicing octave move |
| `/ji/mutation/set_randomness` | float `[0,1]` | update randomness |
| `/ji/mutation/set_dissonance_bias` | float `[-1,1]` | update harmonic bias |
| `/ji/mutation/set_stepwise_bias` | float `[0,1]` | update stepwise probability |
| `/ji/mutation/set_glide_time` | float ≥ 0 | update the glide carried by the next event |
| `/ji/mutation/set_change_count` | number | edits per trigger, clamped to `1..max_changes` |
| `/ji/mutation/set_temperature` | float `[0,1]` | broadens the palette and flattens selection |
| `/ji/mutation/set_anchor_mobility` | float `[0,1]` | 0 = anchor fixed, 1 = no precedence |

### 4.2 Python → sclang (port 57120)

Python sends one message after the initial set and after every applied
mutation, including pure removals:

```text
/ji/freqset/update
    fundamental: float          Hz (currently 220.0)
    glide_time:  float          seconds; event glide, 0.0 for the initial set
    ratio_1 ... ratio_N: float  octave-reduced classes, 1 <= N <= 12
```

The payload is exactly:

```python
[engine.fundamental, glide_time, *engine.current_ratios]
```

The helper `build_freqset_args(engine, glide_time)` in
`control/ji_bridge.py` builds this list without touching a socket.

The initial message is sent at bridge startup with `glide_time = 0.0`. If
sclang is not listening yet, that datagram is lost; the first mutation still
sends the complete set, so trigger once after evaluating `test_bridge.scd`.

sclang validates the message before changing anything. Bad fundamental, glide,
ratio count (0 or >12), or non-positive ratios are ignored with a `[BRIDGE]`
error line. Nothing is sent directly to scsynth in Stage 3; sclang is the
receiver.

---

## 5. Interactive commands

At the `ji>` prompt:

| Input | Effect |
|---|---|
| `m` or empty line | mutate once |
| `o` | octave move once (analysis voicing only) |
| `c 3` | apply three edits per trigger (clamped to `max_changes`) |
| `c 1` | back to single-edit mutations |
| `t 0.7` | raise temperature / allow stranger intervals |
| `a 1.0` | let the anchor move as freely as any class |
| `a 0.0` | freeze the anchor class |
| `g 0.15` | set the glide for subsequent events |
| `g 0.005` | near-instant snap for subsequent events |
| `g 0.5` | slow portamento for subsequent events |
| `s` | print the current ratios, analysis frequencies, and controls |
| `q` | stop the OSC server and exit |

`Ctrl+C` also stops the server cleanly. `Ctrl+D` exits the interactive loop.

The `--glide-time` argument calls `MutationOSCServer.set_glide_time(...)` at
startup, before any mutation is triggered. It is the value carried by every
subsequent `MutationEvent`.

The new controls are set before the first trigger: `--change-count`,
`--temperature`, and `--anchor-mobility`; `--max-changes` sets the engine's
hard ceiling for the change count. Defaults (`1`, `0.0`, `0.25`, max `3`)
reproduce the original single-edit, no-extended-palette, mostly-fixed-anchor
behaviour.

### Triggering from another terminal

```bash
# mutate once
uv run --with python-osc python -c \
  "from pythonosc.udp_client import SimpleUDPClient; SimpleUDPClient('127.0.0.1', 9000).send_message('/ji/mutation/trigger', [])"

# set glide to 0.3 s
uv run --with python-osc python -c \
  "from pythonosc.udp_client import SimpleUDPClient; SimpleUDPClient('127.0.0.1', 9000).send_message('/ji/mutation/set_glide_time', 0.3)"

# octave move
uv run --with python-osc python -c \
  "from pythonosc.udp_client import SimpleUDPClient; SimpleUDPClient('127.0.0.1', 9000).send_message('/ji/mutation/octave', [])"

# three edits per trigger
uv run --with python-osc python -c \
  "from pythonosc.udp_client import SimpleUDPClient; SimpleUDPClient('127.0.0.1', 9000).send_message('/ji/mutation/set_change_count', 3)"

# maximum temperature
uv run --with python-osc python -c \
  "from pythonosc.udp_client import SimpleUDPClient; SimpleUDPClient('127.0.0.1', 9000).send_message('/ji/mutation/set_temperature', 1.0)"

# anchor has no precedence
uv run --with python-osc python -c \
  "from pythonosc.udp_client import SimpleUDPClient; SimpleUDPClient('127.0.0.1', 9000).send_message('/ji/mutation/set_anchor_mobility', 1.0)"
```

---

## 6. Human acceptance test

1. Set the audio device in `startup.scd`, then evaluate it.
2. Stop any Stage 0/1 test.
3. Evaluate `test_bridge.scd`. Confirm the `[BRIDGE]` startup lines and that
   `NetAddr.langPort` is `57120` (if not, use `--sc-port`).
4. Start the Python bridge:
   ```bash
   cd ji-live
   uv run --with python-osc python control/ji_bridge.py --interactive --glide-time 0.15
   ```
5. Hold a steady note into input 0. Watch `[AUTOTUNE]` settle on a target.
6. Press `m`/Enter repeatedly until the held note's current class is removed or
   replaced. A mutation that adds a class elsewhere may not change this note's
   target; keep triggering. The class is gone when the target changes.
7. Confirm all of the following:
   - Python prints the `[BRIDGE] -> /ji/freqset/update ...` line;
   - Python prints the event line (`removed=... new=... reason=...`);
   - sclang prints `[BRIDGE] set update: size=... fundamental=... glide=...
     buffer=... ratios=...`;
   - `[AUTOTUNE] target=` changes to a member of the new set;
   - the sound glides according to the glide time;
   - the target stays in the input's own octave (play A3/A4 to check);
   - set size stays between 3 and 12.
8. Trigger several mutations quickly. Confirm no clicks, dropouts, or zipper
   noise. If it is too abrupt, raise `g` to `0.2` or `0.3`; if it is too slow,
   lower it toward `0.005`.
9. Test `o` once. The analysis voicing moves (printed `[OCTAVE]` and
   `[BRIDGE] octave ...`) but the class set and live autotune target do not
   change.
10. Test the new mutation controls:
    - `c 3`, then `m`: Python prints `[BRIDGE] batch changes=3 ...` and one
      `/ji/freqset/update`; sclang prints one `set update` with the final set.
    - `t 1.0`, then several `m`: new classes should include higher-limit
      intervals (17, 19, 23, 29, 31 are represented); the texture can become
      more complex/rougher.
    - `a 0.0`, then many `m`: the anchor class never leaves.
    - `a 1.0`, then many `m`: eventually a `[BRIDGE]` event shows
      `removed=<anchor>`/`new=...`; the anchor class changes and the next
      `[BRIDGE]` line shows a different anchor.
    - `c 1`, `t 0.0`, `a 0.25` restores the defaults.
11. Stop with `q` (or Ctrl+C), then free the synth in SuperCollider (see §9).

The acceptance is audible: a sustained note retunes without being replayed,
with a smooth transition.

### Glide semantics

`glide_time` is written to the Synth's `retuneTime`, which is the `Lag.kr`
time constant, not a fixed linear duration. A value of `0.15` reaches about
63% of the interval in 0.15 s and is essentially settled in roughly 0.4–0.5 s.
Values below `0.005` are clamped to `0.005` by the sclang lookup.

The new `retuneTime` persists after the mutation that set it, so subsequent
note changes also use the most recent glide. To return to the Stage 1 default,
set it manually (the next mutation's glide will override it again):

```supercollider
~ji_autotune_synth.set(\retuneTime, 0.05);
```

---

## 7. Expected output

Python bridge:

```text
[OSC] glide_time = 0.150s
[OSC] change_count = 1
[OSC] temperature = 0.000
[OSC] MutationOSCServer listening on 127.0.0.1:9000
[BRIDGE] listening on 127.0.0.1:9000; sending to sclang 127.0.0.1:57120
[BRIDGE] initial set: 7 classes, anchor=1.000000
[BRIDGE] ratios: 1.000000  1.125000  1.250000  1.333333  1.500000  1.666667  1.875000
[BRIDGE] Hz:      220.00   247.50   275.00   293.33   330.00   366.67   412.50
[BRIDGE] controls: change_count=1/3 temperature=0.000 anchor_mobility=0.250 randomness=0.550 dissonance=+0.000 stepwise=0.250 glide=0.150
[BRIDGE] -> /ji/freqset/update classes=7 glide=0.000 fundamental=220.0
[BRIDGE] interactive: m/Enter=mutate, o=octave, c <n>=changes, t <0..1>=temperature, a <0..1>=anchor mobility, g <seconds>=glide, s=show, q=quit
ji> m
[MUTATION] removed=1.500000 new=1.458333 octave=0 glide=0.150s reason=replaced size=7 anchor=1.0
[BRIDGE] -> /ji/freqset/update classes=7 glide=0.150 fundamental=220.0
[BRIDGE] removed=1.500000 new=1.458333 reason=replaced size=7 anchor=1.000000
ji> c 3
[OSC] change_count = 3
ji> m
[MUTATION] batch changes=3 glide=0.150s size=7 anchor=1.0
[MUTATION] removed=none new=1.312500 octave=0 glide=0.150s reason=added size=7 anchor=1.0 score=...
[MUTATION] removed=1.125000 new=none octave=- glide=0.150s reason=removed size=7 anchor=1.0 score=...
[MUTATION] removed=1.333333 new=1.636364 octave=0 glide=0.150s reason=replaced size=7 anchor=1.0 score=...
[BRIDGE] -> /ji/freqset/update classes=7 glide=0.150 fundamental=220.0
[BRIDGE] batch changes=3 glide=0.150 size=7 anchor=1.000000
[BRIDGE]   1/3 removed=none new=1.312500 reason=added
[BRIDGE]   2/3 removed=1.125000 new=none reason=removed
[BRIDGE]   3/3 removed=1.333333 new=1.636364 reason=replaced
```

sclang:

```text
[BRIDGE] sclang language OSC port = 57120
[BRIDGE] Hz mirror allocated: bufnum=0 slots=12
[BRIDGE] \ji_autotune_ch started (node 1000)
[BRIDGE] set update: size=7 fundamental=220.00 Hz glide=0.150s buffer=0 ratios=[ 1.0, 1.125, 1.25, 1.3333333333333, 1.5, 1.4583333333333, 1.875 ]
[AUTOTUNE] freq=220.31 target=220.00 hasFreq=0.91 shift=-0.02st setSize=7 fund=220.00
CPU: 3.7%
```

The exact class values and set sizes vary because mutation selection is
stochastic. The event line and the `[AUTOTUNE] setSize=`/`fund=` fields are the
important diagnostics.

---

## 8. Failure modes

| Symptom | Likely cause | Fix |
|---|---|---|
| Python prints updates but sclang never prints `[BRIDGE] set update` | sclang is on a different language port, or another app owns 57120 | Check `NetAddr.langPort` in the post window; restart Python with `--sc-port <that port>` |
| Python prints `send failed` | Bad `--sc-host`/`--sc-port`, or the network stack rejected the address | Use `127.0.0.1` and the port printed by `NetAddr.langPort` |
| Python exits with `Address already in use` on port 9000 | Another Stage 2/3 bridge is running | Stop it, or use `--listen-port 9001` and send triggers there |
| `ModuleNotFoundError: pythonosc` | Ran without `uv --with python-osc` | Use the exact command in §3c |
| `ERROR: test_bridge: ... buses ... missing` | `startup.scd` not evaluated, or old Stage 0 startup | Evaluate the Stage 1/3 `startup.scd`, then `test_bridge.scd` |
| `SynthDef not found: ji_autotune_ch` | SynthDefs were not loaded | Evaluate `startup.scd` (it loads `synthdefs/*.scd`), then `test_bridge.scd` |
| No `[AUTOTUNE]` lines | Lookup routine not running, or server not booted | Re-evaluate `test_bridge.scd`; check the post window for an error |
| `hasFreq` stays `0.00`, no audio | Input too quiet, wrong input channel, or interface not selected | Raise interface gain, play into input 1, check `deviceName` in `startup.scd`; try `~ji_autotune_synth.set(\inputGain, 4)` |
| Audio is doubled/phasey | A Stage 0/1 test synth is still running under another variable | Free it: `~ji_autotune_synth.free; ~ji_tartini_debug_synth.free;` and any custom Synth |
| Target does not change after mutations | Held note's class survived, or the mutation only added a class elsewhere | Keep pressing `m`; watch the Python `removed=`/`new=` line. A pure `added` event cannot retune a held class |
| Target changes but no glide is audible | Mutation glide was overwritten by a later `g`, or the target change is tiny | Set `g 0.5`, then trigger until the target changes by a larger interval |
| Anchor never changes | Default mobility is `0.25`, so anchor removal is rare; the anchor is one class among seven | Set `a 1.0` (no precedence) and trigger many times. At `0.0` it is intentionally frozen. Watch for `removed=` equal to the anchor |
| `c 5` has no effect / still three changes | `max_changes` is `3` by default and the count is clamped | Start with `--max-changes 5` (up to 12) or accept the clamp; the `controls:` line shows `change_count=n/max` |
| Temperature sounds unchanged | Palette expansion is stochastic and rank selection already depends on `randomness`/`dissonance_bias` | Set `t 1.0`, send `/ji/mutation/set_randomness 0.8`, and trigger several times; use a positive `dissonance_bias` to favour rougher choices |
| A batch jumps the set size by several classes | `c 3` applies up to three add/remove/replace edits atomically | Use `c 1` for single edits; the action policy still keeps the set at 3–12 |
| Clicks at mutation time | Glide too short for the interval | Raise `g` to `0.02`–`0.15`; the lookup clamps to `0.005` minimum |
| Set size goes outside 3–12 | A bug or a malformed external update | sclang rejects invalid counts; check Python's `[BRIDGE] -> ... classes=` line and report it |
| `[BRIDGE] ignore /ji/freqset/update` | A sender used the old payload shape (just Hz values) | Stage 3 expects `fundamental, glide, ratios...`; use `ji_bridge.py`, not an old script |
| `[OSC] mutation callback failed` | Python's send failed inside the callback | Read the accompanying `send failed` line; the mutation itself was still applied |

---

## 9. Stopping

In the Python terminal: `q`, or Ctrl+C. This stops the OSC listener.

In SuperCollider:

```supercollider
~ji_bridge_lookup_routine.stop;
~ji_cpu_routine_stage3.stop;
~ji_autotune_synth.free;
~ji_freqset_oscdef.free;
~ji_buf_freqset.free;
```

`q` does not free the synth; free it explicitly if you are done. To run again,
re-evaluate `test_bridge.scd` (it resets the set, OSCdef, buffer, and synth).

---

## 10. Registers, octaves, and `new_octave`

Pitch classes are global and octave-reduced. Registers are local to voices.

Live autotune uses the input's own octave:

1. `oct = floor(log2(freq / fundamental))`;
2. fold `freq` into the fundamental octave;
3. snap to the nearest class (plus `2/1` as the octave-wrap candidate);
4. multiply back by `2^oct`.

`MutationEvent.new_octave` is **not** used to override a live input's octave.
It is an analysis/MIDI/drone hint for later stages. `OctaveEvent` changes only
the engine's `analysis_voicing`; it does not change set size or the live
autotune target. The `o` command is therefore a control-layer test, not an
audio retune.

`anchor_mobility` changes the anchor *class* (the drone's ratio), not the
220 Hz analysis reference or the fundamental sent to sclang. The latter stays
fixed unless you change it explicitly in the engine.

---

## 11. Deferred server-side lookup

The original Stage 3 plan included `synthdefs/ji_freqset_receiver.scd` and a
server-side nearest-class search. That is deferred/superseded:

- the Stage 1 sclang lookup is already verified, low-latency, and supports
  arbitrary input octaves;
- moving lookup into a SynthDef now would add unverified complexity without
  improving the immediate goal;
- Stage 3 writes a 12-slot Hz Buffer mirror for future stages, but no SynthDef
  reads it yet.

A server-side lookup can be added later if measurements justify it.

---

## 12. Mutation controls (post-acceptance extension)

These controls were added after the bridge was accepted. Their defaults
preserve the old behaviour: `change_count=1`, `temperature=0.0`,
`anchor_mobility=0.25`, `max_changes=3`.

| Control | CLI | OSC (port 9000) | Interactive |
|---|---|---|---|
| edits per trigger | `--change-count N` | `/ji/mutation/set_change_count N` | `c N` |
| hard ceiling | `--max-changes N` | (engine construction) | — |
| temperature | `--temperature X` | `/ji/mutation/set_temperature X` | `t X` |
| anchor mobility | `--anchor-mobility X` | `/ji/mutation/set_anchor_mobility X` | `a X` |

### 12.1 Several edits per mutation

`change_count` is the number of sequential add/remove/replace edits applied by
one trigger. `max_changes` is the hard ceiling (default 3; can be set up to 12
when constructing the engine or with `--max-changes`). The server clamps the
requested count to `1..max_changes`.

The edits are generated against the evolving set, so edit 2 sees edit 1. They
are applied atomically by `MutationEngine.mutate_many()`: if any edit fails,
the engine rolls back to its starting state. The bridge then sends **one**
`/ji/freqset/update` with the final set and one glide time, not intermediate
sets. This is why a 3-edit batch causes one retune, not three.

Example:

```text
[BRIDGE] batch changes=3 glide=0.150 size=7 anchor=1.000000
[BRIDGE]   1/3 removed=none new=1.312500 reason=added
[BRIDGE]   2/3 removed=1.125000 new=none reason=removed
[BRIDGE]   3/3 removed=1.333333 new=1.636364 reason=replaced
```

The action policy still enforces the 3–12 class range. At the floor it prefers
add/replace; at the ceiling it prefers remove/replace. A batch can therefore
change the size by more than one, but never outside the allowed range.

### 12.2 Temperature / allowed dissonance

`temperature` is a `0..1` control that makes the field more adventurous:

- **0.0** — exact Stage 2 palette: `INTERVAL_POOL`, rational neighbours with
  denominator limit 16, original score weights, no extra randomness.
- **above 0** — higher-limit intervals from `EXTENDED_INTERVAL_POOL` (primes
  17, 19, 23, 29, 31) are admitted probabilistically; the rational-neighbour
  denominator limit grows from 16 to 32; complexity, roughness, and anchor
  penalties are relaxed; effective rank randomness grows.
- **1.0** — all extended intervals are available and rank selection is uniform
  when `dissonance_bias` is 0.

`temperature` is additive with the existing `randomness` and
`dissonance_bias`: it does not replace them. `dissonance_bias` still provides a
directional preference for rougher or simpler choices.

### 12.3 Anchor mobility

`anchor_mobility` controls how freely the anchor/drone class can move:

- **0.0** — the anchor has zero removal weight. Normal mutations never remove
  or replace it, and `o` never chooses it for an octave move (unless it is the
  only class left).
- **0.25** — the default and the old behaviour: the anchor has one quarter of
  an ordinary class's removal weight, so it moves only occasionally.
- **1.0** — the anchor has the same removal weight as any other class. If it is
  removed, the new anchor is chosen randomly from the survivors, so there is no
  positional precedence either.

The 220 Hz analysis reference does **not** drift; `anchor_mobility` moves the
anchor *class* (the drone's ratio), which changes its sounded pitch relative to
the fixed reference. Live input still uses its own octave.

### 12.4 Harness

The engine/server/bridge controls have a standalone harness:

```bash
cd ji-live
uv run --with python-osc python test_mutation_controls.py
```

It ends with:

```text
ALL CONTROL TESTS PASSED
```

The harness covers batch width and rollback-visible bounds, temperature palette
expansion, anchor mobility 0 and 1, server clamping/batch callbacks, and the
bridge's one-update-per-batch behaviour.

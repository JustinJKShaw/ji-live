# JI Station — Stage 2 (decentralised mutation engine)

Stage 2 is the **control-layer mutation engine**. It maintains a drifting
just-intonation pitch-class field and emits mutation events. It produces no
audio and does not contact scsynth; Stage 3 connects these events to the
autotune/frequency bridge.

Stage 1's static seven-note ratio set still snaps input to the nearest member.
Stage 2 makes that set move.

---

## 1. What a mutation is

The central object is a **FrequencySet**: a set of pitch classes represented as
ratios relative to a fundamental. Pitch classes are octave-reduced:

```text
1/1, 9/8, 5/4, 4/3, 3/2, 5/3, 15/8
```

`2/1`, `1/2`, `9/4`, etc. fold into the same classes. Octave identity is a
**voicing** concern, not a set-membership concern.

A mutation is one of:

| Action | Meaning |
|---|---|
| `added` | a new class enters the set |
| `removed` | a class leaves the set |
| `replaced` | one class leaves and another enters |
| `stepwise` | a replacement whose new class is within ±1.5 semitones of the removed class |

The set is not optimised toward a final chord. It breathes around a target size
of **7**, with a hard maximum of **12** and a soft floor of **3**. The aim is
constant relational drift: the vocabulary keeps changing, but new classes are
derived from the current field rather than sampled from an arbitrary scale.

One class is designated the **anchor/drone**. It is protected from removal
(4× lower removal weight) and contributes a small anchor term to candidate
scoring. It can still move, and if it is removed the engine re-anchors to the
nearest surviving class.

---

## 2. Files

```text
ji-live/
├── control/
│   ├── mutation.py       # MutationEngine, MutationEvent, OctaveEvent
│   └── osc_server.py     # local OSC listener; no scsynth output
├── test_mutation.py      # CLI demo + seven behavioural tests
└── README_stage2.md      # this file
```

No SuperCollider files are changed in Stage 2.

---

## 3. Python setup

The project target is **Python 3.11+**. `uv` is the recommended way to manage
the interpreter and dependencies; a separate system `python3` is not required.

From `ji-live/`:

```bash
uv venv --python 3.11
source .venv/bin/activate
uv pip install python-osc
```

Or run without creating a persistent environment:

```bash
uv run --with python-osc python test_mutation.py
```

`numpy` is **not required by Stage 2**. The mutation tests use only the Python
standard library. `python-osc` is needed only if you import or run
`control/osc_server.py`.

Python 3.11 or newer is required for the type syntax and `dataclass(slots=True)`
used in `mutation.py`.

> Development note: the Stage 2 harness was executed under CPython 3.12 in a
> WASM environment because the isolated coding container did not ship a native
> Python interpreter. The native acceptance run on macOS is:
>
> ```bash
> uv run --with python-osc python test_mutation.py
> ```

---

## 4. Running it

```bash
cd ji-live
uv run --with python-osc python test_mutation.py
```

The harness starts with a visible eight-step demo:

```text
=== Demo: watching the frequency set mutate ===
start: size=7 anchor=1.000000
  ratios: 1.000000  1.125000  1.250000  1.333333  1.500000  1.666667  1.875000
  Hz:      220.00   247.50   275.00   293.33   330.00   366.67   412.50

step 1: replaced 1.500000 -> 1.458333 at octave +0  (glide 0.05s)
after step 1: size=7 anchor=1.000000
  ratios: ...
  Hz:     ...
...
```

Then it runs seven tests:

1. octave folding and deduplication;
2. size breathing around 7;
3. no dead-end from a full 12-class set;
4. continuous class drift;
5. stepwise interval bounds;
6. octave moves without changing set size;
7. anchor retention and re-anchoring.

It ends with:

```text
ALL TESTS PASSED
```

---

## 5. Mutation algorithm

### Candidate generation

For each current class `r` and each interval `q` in a vocabulary of small
rational intervals, generate:

```text
fold_octave(r * q)
```

Rational-neighbour candidates are added from each class's denominator.
Candidates that duplicate an existing class are removed. A near-unison guard
rejects candidates closer than 15 cents to a surviving class. If generation
ever produces nothing, an emergency rational scan finds a fresh class.

There is no fixed candidate pool to exhaust and no normal path where the engine
refuses to mutate.

### Scoring

Each candidate is scored by:

1. **All-pairs denominator complexity** — the primary, register-invariant
   relational term. For each surviving class, approximate the frequency ratio
   as a small rational and sum the denominators.
2. **Sethares roughness** — secondary texture term, computed on the actual
   analysis frequencies. It is not an objective to minimise.
3. **Anchor term** — a small bonus for relatedness to the drone class.
4. **Near-duplicate penalty** — discourages classes too close to be useful.

### Selection

Candidates are sorted by score and selected with:

```text
weight[i] = exp(alpha * normalized_rank[i])
alpha = -10 * (1 - randomness) + 10 * dissonance_bias
```

So:

- low `randomness` favours the top-ranked candidate;
- high `randomness` flattens the distribution;
- positive `dissonance_bias` favours rougher/more complex choices;
- negative `dissonance_bias` favours simpler/more consonant choices.

The best score is a bias, not a deterministic answer.

### Size policy

Add, remove and replace are weighted by distance from `target_size`:

- below 6: mostly add;
- near 7: add/remove/replace;
- above 8: mostly remove;
- at 12: never add.

The set can therefore breathe rather than filling to its maximum.

### Anchor

The anchor is one current class. Removal weight is divided by
`anchor_weight` (4.0). If the anchor is removed, the engine re-anchors to the
class nearest the old anchor. The anchor is therefore grounding without being
immovable.

---

## 6. Register / voicing layer

Pitch classes are global and octave-free. Registers are local to voices.
Stage 2 cannot see live voices, so it keeps **one representative analysis
voicing** — one octave per class — solely for roughness scoring and for
providing a hint to later stages.

Important:

- Live autotune does **not** use a global register. It detects the input's
  octave, folds it into the class space, snaps to a class, and returns that
  class in the input's octave.
- A guitar, voice, or any input can therefore play any class in any register.
- MIDI/drone/loop voices need concrete octaves; those are local voice
  decisions.
- Two voices may reference the same class in different octaves. That is one
  class and two voices, not two set members.
- Roughness is evaluated on actual voiced frequencies; it is register-dependent
  even though class membership is not.

The current engine exposes:

```python
engine.analysis_voicing       # dict: class ratio -> octave
engine.current_frequencies    # analysis frequencies in Hz
engine.propose_octave_motion()
engine.apply_octave_motion()
```

`OctaveEvent` moves one class in the analysis voicing by one octave. This is a
stand-in for per-voice register movement. Stage 3/4 should use actual voice
frequencies for roughness when available.

`MutationEvent.new_octave` is only a hint for MIDI/drone/analysis. Live
autotune should ignore it and use the input octave.

---

## 7. Events

```python
MutationEvent(
    removed_ratio,      # float | None
    new_ratio,          # float | None
    glide_time,         # seconds
    reason,             # "added" | "removed" | "replaced" | "stepwise"
    new_octave,         # optional int hint
    removed_octave,     # optional int hint
)

OctaveEvent(
    ratio,              # class whose analysis register moves
    old_octave,
    new_octave,
    glide_time,
    reason,             # "octave_up" | "octave_down"
)
```

A removal has `new_ratio=None`; an addition has `removed_ratio=None`.

---

## 8. OSC addresses

`control/osc_server.py` listens on `127.0.0.1:9000` by default.

| Address | Arguments | Effect |
|---|---|---|
| `/ji/mutation/trigger` | none | propose and apply one add/remove/replace |
| `/ji/mutation/octave` | none | propose and apply one octave move |
| `/ji/mutation/set_randomness` | float `[0,1]` | update randomness |
| `/ji/mutation/set_dissonance_bias` | float `[-1,1]` | update harmonic bias |
| `/ji/mutation/set_stepwise_bias` | float `[0,1]` | update stepwise probability |
| `/ji/mutation/set_glide_time` | float ≥ 0 | update glide hint |

No OSC is sent to scsynth. Stage 3 adds the outgoing bridge.

Run the listener manually:

```bash
uv run --with python-osc python -m control.osc_server
```

---

## 9. What Stage 2 does not do

- no audio output;
- no scsynth or SuperCollider messages;
- no actual per-voice octave allocator;
- no per-voice roughness from live inputs;
- no MIDI;
- no UI;
- no absolute fundamental/pitch drift (the anchor class and analysis voicing
  can move; the 220 Hz analysis reference is fixed).

## 10. Stage 3 handoff

Stage 3 should:

1. map `MutationEvent` to the current frequency-class buffer;
2. apply `glide_time` to the autotune target transitions;
3. retune live inputs using the input's own octave;
4. optionally use `new_octave` for drone/MIDI/analysis voices;
5. replace the single analysis voicing with actual active-voice frequencies
   when computing roughness;
6. keep the class set global and registers local.

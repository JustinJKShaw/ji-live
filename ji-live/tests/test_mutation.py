#!/usr/bin/env python3
"""CLI acceptance harness for the decentralised Stage 2 mutation engine.

Run from ``ji-live/``:

    python tests/test_mutation.py

No audio, no OSC server, no numpy dependency.
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

# Allow both ``python tests/test_mutation.py`` and ``python -m tests.test_mutation``.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from control.mutation import MutationEngine, MutationEvent

FUNDAMENTAL = 220.0
INITIAL_RATIOS = [1.0, 9.0 / 8.0, 5.0 / 4.0, 4.0 / 3.0, 3.0 / 2.0, 5.0 / 3.0, 15.0 / 8.0]
FULL_12 = [
    1.0,
    16.0 / 15.0,
    9.0 / 8.0,
    6.0 / 5.0,
    5.0 / 4.0,
    4.0 / 3.0,
    7.0 / 5.0,
    3.0 / 2.0,
    8.0 / 5.0,
    5.0 / 3.0,
    15.0 / 8.0,
    7.0 / 4.0,
]


def make_engine(
    ratios: list[float] | None = None, seed: int = 1, **kwargs
) -> MutationEngine:
    return MutationEngine(
        INITIAL_RATIOS if ratios is None else ratios,
        fundamental=FUNDAMENTAL,
        seed=seed,
        **kwargs,
    )


def fmt_event(event: MutationEvent) -> str:
    removed = "none" if event.removed_ratio is None else f"{event.removed_ratio:.6f}"
    new = "none" if event.new_ratio is None else f"{event.new_ratio:.6f}"
    return (
        f"removed={removed:>10}  new={new:>10}  "
        f"oct={str(event.new_octave):>4}  "
        f"reason={event.reason:<8}  glide={event.glide_time:.3f}s"
    )


def print_set(engine: MutationEngine, label: str) -> None:
    """Print the current classes and their voiced frequencies, sorted by pitch."""
    pairs = sorted(zip(engine.current_ratios, engine.current_frequencies))
    anchor = "none" if engine.anchor_ratio is None else f"{engine.anchor_ratio:.6f}"
    print(f"{label}: size={len(pairs)} anchor={anchor}")
    print("  ratios:", "  ".join(f"{ratio:.6f}" for ratio, _ in pairs))
    print("  Hz:    ", "  ".join(f"{frequency:7.2f}" for _, frequency in pairs))


def demo_mutation_sequence() -> None:
    print("\n=== Demo: watching the frequency set mutate ===")
    engine = make_engine(seed=20)
    print_set(engine, "start")

    for step in range(1, 9):
        event = engine.propose_mutation(
            randomness=0.6,
            dissonance_bias=0.0,
            stepwise_bias=0.25,
            glide_time=0.05,
        )
        engine.apply(event)

        if event.reason == "removed":
            action = f"removed {event.removed_ratio:.6f}"
        elif event.reason == "added":
            action = f"added {event.new_ratio:.6f} at octave {event.new_octave:+d}"
        else:
            action = (
                f"{event.reason} {event.removed_ratio:.6f} -> "
                f"{event.new_ratio:.6f} at octave {event.new_octave:+d}"
            )

        print(f"\nstep {step}: {action}  (glide {event.glide_time:.2f}s)")
        print_set(engine, f"after step {step}")
        assert 3 <= len(engine.current_ratios) <= 12


def test_octave_classes() -> None:
    print("\n=== Test 1: octaves fold to one pitch class ===")
    engine = make_engine(
        [0.5, 1.0, 2.0, 3.0, 9.0 / 8.0, 9.0 / 4.0, 3.0 / 2.0],
        seed=10,
    )
    classes = engine.current_ratios
    print(f"input folded to {[f'{r:.6f}' for r in classes]}")
    assert set(classes) == {1.0, 1.125, 1.5}, classes
    assert len(classes) == len(set(classes)), "octave duplicates were retained"
    assert all(1.0 <= ratio < 2.0 for ratio in classes), "class outside [1, 2)"
    assert engine.anchor_ratio in classes


def test_size_breathing() -> None:
    print("\n=== Test 2: size breathes around target 7, never exceeds 12 ===")
    engine = make_engine(seed=1)
    sizes: list[int] = []

    for index in range(120):
        event = engine.propose_mutation(
            randomness=0.65,
            dissonance_bias=0.0,
            stepwise_bias=0.2,
            glide_time=0.03,
        )
        engine.apply(event)
        ratios = engine.current_ratios
        sizes.append(len(ratios))
        assert 3 <= len(ratios) <= 12, f"size out of range at step {index + 1}"
        assert len(ratios) == len(set(ratios)), "duplicate class"
        assert all(1.0 <= ratio < 2.0 for ratio in ratios), "unfolded class"

    average = sum(sizes) / len(sizes)
    print(
        f"sizes min={min(sizes)} max={max(sizes)} mean={average:.2f} "
        f"counts={dict(sorted(Counter(sizes).items()))}"
    )
    print(f"final classes: {[f'{r:.6f}' for r in engine.current_ratios]}")
    print(f"final roughness: {engine.roughness_score():.6f}")
    assert min(sizes) < 7 and max(sizes) > 7, "size did not breathe around 7"
    assert abs(average - 7.0) < 1.0, "average drifted away from target"


def test_never_dead_ends_from_full() -> None:
    print("\n=== Test 3: a full 12-class set still mutates ===")
    engine = make_engine(FULL_12, seed=2)
    reasons: Counter[str] = Counter()
    lower_than_12 = False

    for index in range(100):
        before = set(engine.current_ratios)
        event = engine.propose_mutation(
            randomness=0.7,
            dissonance_bias=0.0,
            stepwise_bias=0.3,
            glide_time=0.02,
        )
        assert event.new_ratio is not None or event.removed_ratio is not None
        if event.new_ratio is not None:
            assert event.new_ratio not in before, "candidate was already present"
        engine.apply(event)
        reasons[event.reason] += 1
        size = len(engine.current_ratios)
        assert 3 <= size <= 12, f"size out of range at step {index + 1}"
        if size < 12:
            lower_than_12 = True

    print(f"reasons={dict(reasons)}")
    print(f"final size={len(engine.current_ratios)}")
    print(f"final classes: {[f'{r:.6f}' for r in engine.current_ratios]}")
    assert lower_than_12, "full set never made room by removing a class"
    assert reasons["removed"] > 0, "engine never removed a class"


def test_constant_flux() -> None:
    print("\n=== Test 4: the class field keeps changing ===")
    engine = make_engine(seed=3)
    new_classes: set[float] = set()
    scores: list[float] = []
    reasons: Counter[str] = Counter()

    for _ in range(100):
        old = tuple(sorted(engine.current_ratios))
        event = engine.propose_mutation(
            randomness=0.7,
            dissonance_bias=0.0,
            stepwise_bias=0.2,
            glide_time=0.0,
        )
        engine.apply(event)
        new = tuple(sorted(engine.current_ratios))
        assert new != old, "mutation did not change the class field"
        if event.new_ratio is not None:
            new_classes.add(event.new_ratio)
        scores.append(engine.roughness_score())
        reasons[event.reason] += 1

    print(f"distinct new classes={len(new_classes)}")
    print(f"reasons={dict(reasons)}")
    print(
        f"roughness min={min(scores):.6f} max={max(scores):.6f} "
        f"final={scores[-1]:.6f}"
    )
    print(f"final classes: {[f'{r:.6f}' for r in engine.current_ratios]}")
    assert len(new_classes) >= 30, "palette did not drift enough"
    assert max(scores) - min(scores) > 0.1, "roughness never varied"
    assert reasons["added"] > 0 and reasons["removed"] > 0, (
        "size-changing actions did not occur"
    )


def test_stepwise() -> None:
    print("\n=== Test 5: stepwise replacement stays within +/-1.5 semitones ===")
    engine = make_engine(FULL_12, seed=5)
    stepwise_count = 0
    pair_count = 0

    for _ in range(30):
        event = engine.propose_mutation(
            randomness=0.7,
            dissonance_bias=0.0,
            stepwise_bias=1.0,
            glide_time=0.02,
        )
        if event.removed_ratio is not None and event.new_ratio is not None:
            pair_count += 1
            if event.reason == "stepwise":
                stepwise_count += 1
                relative = event.new_ratio / event.removed_ratio
                print(f"  {fmt_event(event)}  new/removed={relative:.6f}")
                assert 0.917 <= relative <= 1.091, (
                    f"stepwise interval out of range: {relative:.9f}"
                )
        engine.apply(event)

    print(
        f"stepwise replacements={stepwise_count}/{pair_count}; "
        f"size={len(engine.current_ratios)}"
    )
    assert stepwise_count >= 5, "too few stepwise replacements were generated"
    assert len(engine.current_ratios) == len(set(engine.current_ratios))


def test_octave_motion() -> None:
    print("\n=== Test 6: octave moves do not change the class set ===")
    engine = make_engine(seed=6)
    size = len(engine.current_ratios)
    moved_classes: set[float] = set()

    for index in range(12):
        ratio = engine.current_ratios[index % size]
        before = engine.analysis_voicing[ratio]
        event = engine.propose_octave_motion(
            ratio=ratio,
            glide_time=0.03,
        )
        assert event.ratio == ratio
        assert event.old_octave == before
        assert event.new_octave == event.old_octave + (
            1 if event.reason == "octave_up" else -1
        )
        engine.apply_octave_motion(event)
        assert len(engine.current_ratios) == size, "octave move changed set size"
        assert engine.analysis_voicing[ratio] == event.new_octave
        moved_classes.add(ratio)
        print(
            f"  {event.reason:<10} ratio={ratio:.6f} "
            f"{event.old_octave:+d}->{event.new_octave:+d} "
            f"freq={ratio * FUNDAMENTAL * 2 ** event.new_octave:.2f}Hz"
        )

    print(f"moved distinct classes={len(moved_classes)}/{size}")
    print(f"final voicing={engine.analysis_voicing}")
    assert len(moved_classes) >= size - 1, "most classes should be movable"
    assert all(
        engine.octave_min <= octave <= engine.octave_max
        for octave in engine.analysis_voicing.values()
    )

    # A specified direction must be honoured.
    first = engine.current_ratios[0]
    before = engine.analysis_voicing[first]
    direction = 1 if before < engine.octave_max else -1
    event = engine.propose_octave_motion(ratio=first, direction=direction)
    engine.apply_octave_motion(event)
    assert event.new_octave == before + direction


def test_anchor_retention() -> None:
    print("\n=== Test 7: the anchor is grounded but can move ===")
    engine = make_engine(seed=7)
    removals = 0
    anchor_removals = 0

    for _ in range(120):
        anchor = engine.anchor_ratio
        event = engine.propose_mutation(
            randomness=0.7,
            dissonance_bias=0.0,
            stepwise_bias=0.2,
            glide_time=0.0,
        )
        if event.removed_ratio is not None:
            removals += 1
            if event.removed_ratio == anchor:
                anchor_removals += 1
        engine.apply(event)
        assert engine.anchor_ratio in engine.current_ratios
        assert engine.anchor_ratio is not None

    rate = anchor_removals / removals if removals else 0.0
    print(
        f"anchor removals={anchor_removals}/{removals} "
        f"({rate:.1%}); final anchor={engine.anchor_ratio:.6f}"
    )
    print(f"final classes: {[f'{r:.6f}' for r in engine.current_ratios]}")
    assert removals > 30, "not enough removal events to judge"
    assert rate < 0.20, "anchor was not protected sufficiently"

    # Removing the anchor explicitly must re-anchor to a surviving class.
    engine = make_engine(seed=8)
    old_anchor = engine.anchor_ratio
    assert old_anchor is not None
    engine.apply(
        MutationEvent(
            removed_ratio=old_anchor,
            new_ratio=None,
            glide_time=0.0,
            reason="removed",
        )
    )
    assert engine.anchor_ratio is not None
    assert engine.anchor_ratio in engine.current_ratios
    print(f"explicit anchor removal re-anchored to {engine.anchor_ratio:.6f}")


def main() -> int:
    print("JI decentralized mutation engine -- Stage 2 harness")
    print(f"fundamental={FUNDAMENTAL} Hz")
    print(f"initial={[round(ratio, 6) for ratio in INITIAL_RATIOS]}")

    demo_mutation_sequence()
    test_octave_classes()
    test_size_breathing()
    test_never_dead_ends_from_full()
    test_constant_flux()
    test_stepwise()
    test_octave_motion()
    test_anchor_retention()

    print("\nALL TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

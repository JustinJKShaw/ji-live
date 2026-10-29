#!/usr/bin/env python3
"""Acceptance harness for the Stage 3 mutation controls.

Run from ``ji-live/``:

    uv run --with python-osc python test_mutation_controls.py

Covers:
  1. batch width (1..max_changes) and atomic application;
  2. temperature broadening the palette / relaxing selection;
  3. anchor mobility 0 (fixed) and 1 (no precedence);
  4. OSC server setters and batch callbacks;
  5. bridge batch callback sends one final FrequencySet.

No audio is required.
"""

from __future__ import annotations

from control.mutation import (
    DEFAULT_ANCHOR_MOBILITY,
    MutationBatch,
    MutationEngine,
    MutationEvent,
)
from control.osc_server import MutationOSCServer

INITIAL = [1.0, 9.0 / 8.0, 5.0 / 4.0, 4.0 / 3.0, 3.0 / 2.0, 5.0 / 3.0, 15.0 / 8.0]
FUNDAMENTAL = 220.0


def make_engine(seed: int = 1, **kwargs) -> MutationEngine:
    return MutationEngine(
        INITIAL, fundamental=FUNDAMENTAL, seed=seed, max_changes=3, **kwargs
    )


def test_batch_width() -> None:
    print("\n=== Test 1: one trigger can apply 1..3 edits ===")
    for count in (1, 2, 3):
        engine = make_engine(seed=count)
        batch = engine.mutate_many(count, glide_time=0.2, temperature=0.2)
        assert isinstance(batch, MutationBatch)
        assert batch.count == count
        assert len(batch.events) == count
        assert batch.glide_time == 0.2
        assert all(isinstance(event, MutationEvent) for event in batch.events)
        assert 3 <= len(engine.current_ratios) <= 12
        assert len(engine.current_ratios) == len(set(engine.current_ratios))
        print(f"  count={count}: size={len(engine.current_ratios)}")

    try:
        make_engine().mutate_many(4)
    except ValueError:
        pass
    else:
        raise AssertionError("mutate_many accepted count above max_changes")

    try:
        MutationEngine(INITIAL, max_changes=0)
    except ValueError:
        pass
    else:
        raise AssertionError("max_changes=0 was accepted")


def test_temperature() -> None:
    print("\n=== Test 2: temperature broadens the palette ===")
    for temperature in (0.0, 0.25, 0.5, 1.0):
        engine = make_engine(seed=11)
        for _ in range(60):
            event = engine.propose_mutation(
                randomness=0.55, glide_time=0.0, temperature=temperature
            )
            engine.apply(event)
            assert 3 <= len(engine.current_ratios) <= 12
            assert all(1.0 <= ratio < 2.0 for ratio in engine.current_ratios)
        print(f"  temperature={temperature:.2f}: size={len(engine.current_ratios)}")

    engine = make_engine(seed=12)
    seen: set[float] = set()
    for _ in range(300):
        event = engine.propose_mutation(
            randomness=0.55, glide_time=0.0, temperature=1.0
        )
        if event.new_ratio is not None:
            seen.add(event.new_ratio)
        engine.apply(event)
    assert len(seen) > 20, f"temperature 1 produced only {len(seen)} new classes"
    print(f"  temperature=1.00: distinct new classes={len(seen)}")


def test_anchor_mobility_zero() -> None:
    print("\n=== Test 3: anchor mobility 0 freezes the anchor ===")
    engine = make_engine(seed=21, anchor_mobility=0.0)
    old_anchor = engine.anchor_ratio
    assert old_anchor is not None
    removals = 0

    for _ in range(250):
        event = engine.propose_mutation(
            randomness=0.7, stepwise_bias=0.0, glide_time=0.0
        )
        if event.removed_ratio is not None:
            assert event.removed_ratio != old_anchor, event
            removals += 1
        engine.apply(event)
        assert engine.anchor_ratio == old_anchor

    for _ in range(80):
        event = engine.propose_octave_motion()
        assert event.ratio != old_anchor

    assert removals > 20, f"only {removals} non-anchor removals"
    print(f"  removals={removals}; anchor stayed {old_anchor:.6f}")


def test_anchor_mobility_one() -> None:
    print("\n=== Test 4: anchor mobility 1 removes the anchor's precedence ===")
    engine = make_engine(seed=22, anchor_mobility=1.0)
    old_anchor = engine.anchor_ratio
    assert old_anchor is not None
    anchor_removals = 0
    history = {old_anchor}

    for _ in range(400):
        anchor = engine.anchor_ratio
        event = engine.propose_mutation(
            randomness=0.7, stepwise_bias=0.0, glide_time=0.0
        )
        if event.removed_ratio == anchor:
            anchor_removals += 1
        engine.apply(event)
        assert engine.anchor_ratio in engine.current_ratios
        history.add(engine.anchor_ratio)

    assert anchor_removals > 0, "anchor was never removed at mobility 1"
    assert len(history) > 1, history
    print(
        f"  anchor removals={anchor_removals}/400; "
        f"distinct anchors={len(history)}"
    )


def test_server_controls() -> None:
    print("\n=== Test 5: OSC server controls and batch callback ===")
    engine = make_engine(seed=31)
    seen: list[MutationEvent | MutationBatch] = []
    server = MutationOSCServer(
        engine, listen_port=19110, on_event=seen.append
    )

    server.set_change_count(3)
    server.set_temperature(0.5)
    server.set_anchor_mobility(0.0)
    assert server.change_count == 3
    assert server.temperature == 0.5
    assert server.anchor_mobility == 0.0
    assert server.max_changes == 3

    batch = server.trigger()
    assert isinstance(batch, MutationBatch)
    assert batch.count == 3
    assert seen == [batch]
    assert 3 <= len(engine.current_ratios) <= 12

    server.set_change_count(0)
    assert server.change_count == 1
    server.set_change_count(99)
    assert server.change_count == 3
    server.set_temperature(-2.0)
    assert server.temperature == 0.0
    server.set_temperature(2.0)
    assert server.temperature == 1.0
    server.set_anchor_mobility(-2.0)
    assert server.anchor_mobility == 0.0
    server.set_anchor_mobility(2.0)
    assert server.anchor_mobility == 1.0

    server.set_change_count(1)
    single = server.trigger()
    assert isinstance(single, MutationEvent)
    print("  setters clamp; single change still returns MutationEvent")


def test_bridge_batch() -> None:
    print("\n=== Test 6: bridge sends one set for a batch ===")
    import control.ji_bridge as bridge_module

    class FakeClient:
        def __init__(self, host: str, port: int) -> None:
            self.sent: list[tuple[str, list[float]]] = []

        def send_message(self, address: str, value: list[float]) -> None:
            self.sent.append((address, list(value)))

    original = bridge_module.SimpleUDPClient
    bridge_module.SimpleUDPClient = FakeClient
    try:
        bridge = bridge_module.JIBridge(
            seed=41,
            max_changes=3,
            change_count=2,
            temperature=0.3,
            anchor_mobility=0.5,
        )
        batch = bridge.engine.mutate_many(2, temperature=0.3)
        bridge._on_event(batch)
        assert len(bridge.client.sent) == 1
        address, payload = bridge.client.sent[0]
        assert address == "/ji/freqset/update"
        assert payload[2:] == bridge.engine.current_ratios
        assert bridge.server.change_count == 2
        assert bridge.server.temperature == 0.3
        assert bridge.server.anchor_mobility == 0.5
    finally:
        bridge_module.SimpleUDPClient = original
    print("  one full-set update for a 2-edit batch")


def main() -> int:
    print("JI mutation controls -- Stage 3 extension harness")
    print(f"default anchor mobility={DEFAULT_ANCHOR_MOBILITY}")
    test_batch_width()
    test_temperature()
    test_anchor_mobility_zero()
    test_anchor_mobility_one()
    test_server_controls()
    test_bridge_batch()
    print("\nALL CONTROL TESTS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Stage 8.4 home-set, journey, and smooth-return tests.

Run from ``ji-live/``::

    uv run --with python-osc python tests/test_home.py

No audio, MIDI, Qt, or real sockets: the bridge is built with a fake UDP client,
a fake monotonic clock drives the beat queue, and every MIDI/UI status send is
inspected in memory.  The live rig (sclang, Ableton, IAC, the GUI) remains a
human check.
"""

from __future__ import annotations

import contextlib
import io
import math
import random
import sys
import traceback
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import control.ji_bridge as bridge_module
from control.home import (
    DRIFT_MAX,
    EVERY_MAX,
    EVERY_MIN,
    HISTORY_DEPTH_MAX,
    HISTORY_DEPTH_MIN,
    MAX_DRIFT_TRIES,
    RETURN_GLIDE_MAX,
    RETURN_GLIDE_MIN,
    SEED_MAX,
    SLOT_NAMES,
    HomeConfig,
    HomeManager,
    HomePlanner,
    HomeState,
    plan_return,
)
from control.mutation import MutationEngine, SetSnapshot, fold_ratio
from control.osc_server import (
    HOME_BACK_ADDRESS,
    HOME_CAPTURE_ADDRESS,
    HOME_CLEAR_ADDRESS,
    HOME_GO_ADDRESS,
    HOME_JUMP_ADDRESS,
    HOME_RETURN_ADDRESS,
    HOME_SET_AUTO_ADDRESS,
    HOME_SET_DRIFT_ADDRESS,
    HOME_SET_EVERY_ADDRESS,
    HOME_SET_GLIDE_ADDRESS,
    HOME_SET_SEED_ADDRESS,
    MutationOSCServer,
)
from control.quantize import (
    PHASE_HOME,
    PHASE_MUTATION,
    PHASE_NAMES,
    PHASE_ORDER,
)
from control.rhythm import RhythmEngine
from ui import osc_control

INITIAL = [1.0, 9.0 / 8.0, 5.0 / 4.0, 4.0 / 3.0, 3.0 / 2.0, 5.0 / 3.0, 15.0 / 8.0]

#: The engine's propose/apply sequence for seed 5, recorded before Stage 8.4's
#: additive methods existed.  ``reseed``/``snapshot``/``apply_snapshot`` must not
#: change it.
PINNED_SEED_5 = [
    (1.666666666667, 1.2375, "replaced"),
    (1.2375, 1.666666666667, "replaced"),
    (1.5, None, "removed"),
    (1.333333333333, 1.4, "stepwise"),
    (None, 1.428571428572, "added"),
    (1.666666666667, 1.020408163266, "replaced"),
    (1.4, None, "removed"),
    (None, 1.669758812617, "added"),
]
PINNED_SEED_5_FINAL = (
    1.0,
    1.020408163266,
    1.125,
    1.25,
    1.428571428572,
    1.669758812617,
    1.875,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class FakeClock:
    def __init__(self, now: float = 100.0) -> None:
        self.now = float(now)

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += float(seconds)


class FakeClient:
    """Minimal SimpleUDPClient stand-in (records every message)."""

    instances: list["FakeClient"] = []

    def __init__(self, host: str, port: int) -> None:
        self.host = host
        self.port = port
        self.sent: list[tuple[str, list]] = []
        FakeClient.instances.append(self)

    def send_message(self, address: str, args: list) -> None:
        self.sent.append((address, list(args)))

    def addresses(self) -> list[str]:
        return [address for address, _ in self.sent]


@contextlib.contextmanager
def fake_bridge(**kwargs):
    """A bridge with a fake UDP client and a deterministic fake clock."""

    original = bridge_module.SimpleUDPClient
    FakeClient.instances = []
    bridge_module.SimpleUDPClient = FakeClient
    try:
        bridge = bridge_module.JIBridge(**kwargs)
        clock = FakeClock(100.0)
        rhythm = RhythmEngine(bpm=120.0, now=clock)
        bridge.rhythm = rhythm
        bridge.quantizer._rhythm = rhythm
        bridge.quantizer._now = clock
        client, ui_client = FakeClient.instances
        yield bridge, client, ui_client, clock, rhythm
    finally:
        bridge_module.SimpleUDPClient = original


def tick(bridge, clock, beats: int = 1) -> int:
    """Advance the fake clock one quarter beat and run the queue."""

    applied = 0
    for _ in range(beats):
        clock.advance(0.5)  # 120 BPM
        applied += bridge.quantizer.tick()
    return applied


def updates(client: FakeClient) -> list[list]:
    return [args for address, args in client.sent if address == "/ji/freqset/update"]


def home_states(ui_client: FakeClient) -> list[list]:
    return [args for address, args in ui_client.sent if address == "/ji/home/state"]


def as_set(snapshot) -> set[float]:
    return set(snapshot.ratios)


def apply_plan(classes, plan):
    """Apply ``(remove, add)`` batches to a set and return the final set."""

    current = set(classes)
    for batch in plan:
        for removed, added in batch:
            if removed is not None:
                assert removed in current, f"remove {removed} not in set"
                current.remove(removed)
            if added is not None:
                assert added not in current, f"add {added} already in set"
                current.add(added)
    return current


POOL = sorted(
    {
        fold_ratio(ratio)
        for ratio in (
            1.0,
            9.0 / 8.0,
            5.0 / 4.0,
            4.0 / 3.0,
            3.0 / 2.0,
            5.0 / 3.0,
            15.0 / 8.0,
            7.0 / 4.0,
            7.0 / 6.0,
            11.0 / 8.0,
            13.0 / 8.0,
            16.0 / 15.0,
            6.0 / 5.0,
            8.0 / 5.0,
            9.0 / 5.0,
            10.0 / 9.0,
            16.0 / 9.0,
            15.0 / 7.0,
            14.0 / 9.0,
            5.0 / 2.0,
        )
    }
)


def random_snapshot(rng: random.Random, size: int, anchor: bool = True):
    classes = rng.sample(POOL, size)
    chosen = rng.choice(classes) if (anchor and rng.random() < 0.7) else None
    return SetSnapshot.create(classes, chosen)


# ---------------------------------------------------------------------------
# 1. Config / state
# ---------------------------------------------------------------------------


def test_config() -> None:
    print("\n=== Test 1: HomeConfig validation, clamps, and the empty manager ===")
    shipped = HomeConfig.load()
    assert shipped.slots == 4
    assert shipped.auto is False
    assert shipped.every == 8 and shipped.seed == 1
    assert shipped.max_drift == 0
    assert math.isclose(shipped.return_glide, 0.06)
    assert shipped.history_depth == 64
    assert shipped.as_dict()["slots"] == 4

    assert HomeConfig.create(every=0).every == EVERY_MIN
    assert HomeConfig.create(every=999).every == EVERY_MAX
    assert HomeConfig.create(seed=-5).seed == 0
    assert HomeConfig.create(seed=2**40).seed == SEED_MAX
    assert HomeConfig.create(max_drift=-3).max_drift == 0
    assert HomeConfig.create(max_drift=999).max_drift == DRIFT_MAX
    assert math.isclose(
        HomeConfig.create(return_glide=0.0).return_glide, RETURN_GLIDE_MIN
    )
    assert math.isclose(
        HomeConfig.create(return_glide=9.0).return_glide, RETURN_GLIDE_MAX
    )
    assert HomeConfig.create(history_depth=1).history_depth == HISTORY_DEPTH_MIN
    assert (
        HomeConfig.create(history_depth=9999).history_depth == HISTORY_DEPTH_MAX
    )
    assert HomeConfig.create(auto=1).auto is True

    for bad in (3, 5, 0):
        try:
            HomeConfig.create(slots=bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"slots={bad} was accepted")

    mapped = HomeConfig.from_mapping({"every": 4, "ignored": True})
    assert mapped.every == 4
    assert HomeConfig.from_mapping(None) == HomeConfig.create()

    manager = HomeManager(shipped)
    state = manager.state()
    assert state == HomeState(
        has_home=False,
        active_slot=-1,
        home_classes=0,
        distance=0,
        pending_batches=0,
        history_depth=0,
        auto=False,
        every=8,
        seed=1,
        drift=0,
        returning=False,
        journey_mutations=0,
        slot_mask=0,
    )
    args = state.as_state_args()
    assert len(args) == 13 and all(isinstance(value, int) for value in args)
    assert args == [0, -1, 0, 0, 0, 0, 0, 8, 1, 0, 0, 0, 0]
    assert not manager.has_home and manager.home is None
    assert manager.distance(SetSnapshot.create(INITIAL)) == 0
    assert manager.slot_mask == 0 and manager.active_slot == -1

    resized = manager.update_config(history_depth=16)
    assert resized.history_depth == 16
    manager.update_config(seed=42)
    assert manager.state().seed == 42, "the state reports the configured seed"
    assert manager._history.maxlen == 16
    manager.record(SetSnapshot.create(INITIAL))
    manager.record(SetSnapshot.create(INITIAL))
    assert manager.history_depth == 1, "consecutive identical snapshots dedupe"
    print("  defaults, clamps, slots=4, empty manager, 13-value state")


# ---------------------------------------------------------------------------
# 2. Slots
# ---------------------------------------------------------------------------


def test_slots() -> None:
    print("\n=== Test 2: capture / clear / go and the active slot ===")
    manager = HomeManager()
    first = SetSnapshot.create(INITIAL, 1.0)
    second = SetSnapshot.create([1.0, 1.25, 1.5], 1.25)

    assert manager.capture(0, first) == first
    assert manager.has_home and manager.active_slot == 0
    assert manager.slot_mask == 0b0001
    assert manager.home == first
    assert manager.capture(1, second) == second
    assert manager.active_slot == 1 and manager.slot_mask == 0b0011
    assert manager.home == second
    assert manager.slot_snapshot(0) == first
    assert manager.slot_snapshot(2) is None

    assert manager.go(0) == first
    assert manager.active_slot == 0
    assert manager.go(2) is None, "an empty slot cannot be activated"
    assert manager.active_slot == 0

    assert manager.clear(0) is True
    assert manager.slot_mask == 0b0010
    assert manager.active_slot == -1, "clearing the active slot leaves no home"
    assert manager.home is None
    assert manager.clear(0) is False
    assert manager.clear(1) is True
    assert manager.has_home is False and manager.slot_mask == 0

    for bad in (-1, 4, "x"):
        try:
            manager.capture(bad, first)
        except (TypeError, ValueError):
            pass
        else:
            raise AssertionError(f"slot {bad!r} was accepted")
    assert SLOT_NAMES == ("A", "B", "C", "D")
    print("  capture A/B, go, clear active, invalid slots rejected")


# ---------------------------------------------------------------------------
# 3. Distance / planning
# ---------------------------------------------------------------------------


def test_distance_and_plan() -> None:
    print("\n=== Test 3: exact distance, batch count, and voice-led pairing ===")
    rng = random.Random(8404)
    trials = 0
    for _ in range(140):
        current = random_snapshot(rng, rng.randint(3, 12))
        home = random_snapshot(rng, rng.randint(3, 12))
        distance = HomePlanner().distance(current, home)
        expected = max(
            len(as_set(current) - as_set(home)),
            len(as_set(home) - as_set(current)),
        )
        assert distance == expected

        max_changes = rng.randint(1, 3)
        seed = rng.randint(0, 2**31 - 1)
        plan = plan_return(current, home, max_changes, seed)

        if distance == 0:
            assert plan == []
            continue
        trials += 1
        assert len(plan) == math.ceil(distance / max_changes), (
            distance,
            max_changes,
            plan,
        )
        for batch in plan:
            assert 1 <= len(batch) <= max_changes, batch

        # Landing, sizes, and the anchor rules after every batch.
        running = set(current.ratios)
        home_anchor = home.anchor
        anchor_present = home_anchor is not None and home_anchor in running
        for index, batch in enumerate(plan):
            for removed, added in batch:
                if removed is not None:
                    assert removed in running
                    if anchor_present:
                        assert removed != home_anchor, "home anchor was removed"
                    running.remove(removed)
                if added is not None:
                    assert added not in running
                    running.add(added)
            assert 3 <= len(running) <= 12, (index, len(running), plan)
        assert running == as_set(home), (running, as_set(home))
        assert not anchor_present or home_anchor in running

        if home_anchor is not None and home_anchor not in as_set(current):
            last_adds = [added for _removed, added in plan[-1]]
            assert home_anchor in last_adds, "home anchor must be added last"
            earlier = [
                added
                for batch in plan[:-1]
                for _removed, added in batch
                if added is not None
            ]
            assert home_anchor not in earlier

        # Determinism: the seed only breaks ties.
        assert plan_return(current, home, max_changes, seed) == plan
    assert trials > 100, trials
    print(f"  {trials} random plans: exact batches, landing, sizes, anchor")

    # Worked example from the stage prompt.
    current = SetSnapshot.create([1.0, 9.0 / 8.0, 4.0 / 3.0, 3.0 / 2.0, 5.0 / 3.0])
    home = SetSnapshot.create([1.0, 9.0 / 8.0, 5.0 / 4.0, 3.0 / 2.0, 5.0 / 3.0])
    plan = plan_return(current, home, 1, 7)
    assert plan == [[(fold_ratio(4.0 / 3.0), fold_ratio(5.0 / 4.0))]], plan
    assert len(plan) == 1
    print("  worked example: 1 batch removes 4/3 and adds 5/4")

    # Voice-led: a single removal takes the nearest home class.
    current = SetSnapshot.create([1.0, 4.0 / 3.0, 3.0 / 2.0])
    home = SetSnapshot.create([1.0, 5.0 / 4.0, 3.0 / 2.0])
    assert plan_return(current, home, 3, 1) == [
        [(fold_ratio(4.0 / 3.0), fold_ratio(5.0 / 4.0))]
    ]

    # Sizes differ: pure additions (home bigger) and pure removals.
    small = SetSnapshot.create([1.0, 5.0 / 4.0, 3.0 / 2.0])
    big = SetSnapshot.create([1.0, 5.0 / 4.0, 3.0 / 2.0, 5.0 / 3.0, 15.0 / 8.0])
    plan = plan_return(small, big, 1, 2)
    assert len(plan) == 2
    assert all(removed is None for batch in plan for removed, _ in batch)
    assert apply_plan(small.ratios, plan) == as_set(big)
    plan = plan_return(big, small, 2, 2)
    assert len(plan) == 1 and len(plan[0]) == 2
    assert all(added is None for removed, added in plan[0])
    print("  home bigger -> additions first; home smaller -> removals")

    # Invalid planner input is rejected.
    for bad_call in (
        lambda: plan_return(small, big, 0, 1),
        lambda: plan_return(small, big, 13, 1),
        lambda: plan_return(SetSnapshot.create([1.0, 1.1]), small, 1, 1),
    ):
        try:
            bad_call()
        except (TypeError, ValueError):
            pass
        else:
            raise AssertionError("invalid plan input was accepted")
    print("  exact ceil(distance/max_changes), at most max_changes per batch")


# ---------------------------------------------------------------------------
# 4. Engine additions
# ---------------------------------------------------------------------------


def test_engine_additions() -> None:
    print("\n=== Test 4: reseed / snapshot / apply_snapshot (additive only) ===")
    engine = MutationEngine(INITIAL, seed=5)
    initial = engine.snapshot()
    seen = []
    for _ in range(8):
        event = engine.propose_mutation(randomness=0.55, glide_time=0.0)
        engine.apply(event)
        seen.append((event.removed_ratio, event.new_ratio, event.reason))
    assert seen == PINNED_SEED_5, seen
    assert tuple(sorted(engine.current_ratios)) == PINNED_SEED_5_FINAL
    landed = engine.snapshot()
    print("  propose_mutation output is unchanged for the pinned seed")

    # reseed replaces the RNG but not the state; from the same starting state
    # the same seed replays the same journey.
    engine.reseed(5)
    assert engine.snapshot() == landed, "reseed must not touch the state"
    engine.apply_snapshot(initial)
    engine.reseed(5)
    replay = []
    for _ in range(8):
        event = engine.propose_mutation(randomness=0.55, glide_time=0.0)
        engine.apply(event)
        replay.append((event.removed_ratio, event.new_ratio, event.reason))
    assert replay == PINNED_SEED_5
    assert engine.snapshot() == landed

    other = MutationEngine(INITIAL, seed=6)
    first = other.propose_mutation(glide_time=0.0)
    reseeded = MutationEngine(INITIAL, seed=6)
    reseeded.reseed(7)
    second = reseeded.propose_mutation(glide_time=0.0)
    assert (first.removed_ratio, first.new_ratio) != (
        second.removed_ratio,
        second.new_ratio,
    ), "reseed must change the sequence"
    try:
        reseeded.reseed(1.5)  # type: ignore[arg-type]
    except TypeError:
        pass
    else:
        raise AssertionError("reseed accepted a non-integer seed")
    print("  reseed changes the sequence and leaves the set untouched")

    # Snapshot round-trip, including a non-zero voicing.
    engine.set_analysis_octave(1.25, 1)
    engine.set_analysis_octave(1.0, -1)
    snapshot = engine.snapshot()
    assert snapshot.ratios == tuple(sorted(engine.current_ratios))
    assert snapshot.anchor == engine.anchor_ratio
    assert isinstance(snapshot.octaves, tuple)
    engine.apply_snapshot(SetSnapshot.create([1.0, 1.25, 1.5], 1.25))
    assert engine.current_ratios == [1.0, 1.25, 1.5]
    engine.apply_snapshot(snapshot)
    assert tuple(sorted(engine.current_ratios)) == snapshot.ratios
    assert engine.anchor_ratio == snapshot.anchor
    assert engine.analysis_voicing == snapshot.octave_map()
    print("  snapshot/apply_snapshot round-trips ratios, anchor and octaves")

    # Duck typing and the plain 3-tuple form.
    loose = SimpleNamespace(
        ratios=[1.0, 1.5], anchor=1.5, octaves=[(1.0, 0), (1.5, 2)]
    )
    engine.apply_snapshot(loose)
    assert engine.current_ratios == [1.0, 1.5]
    assert engine.analysis_voicing == {1.0: 0, 1.5: 2}
    engine.apply_snapshot(([1.0, 1.25], 1.0, []))
    assert engine.current_ratios == [1.0, 1.25]

    # Invalid snapshots raise ValueError and leave the state untouched.
    engine.apply_snapshot(SetSnapshot.create(INITIAL, 1.0))
    before = engine.snapshot()
    for bad in (
        ([-1.0, 1.5], None, []),
        ([1.0, 1.5], 1.25, []),
        ([1.0, 1.5], 1.0, [(1.5, 99)]),
        ([1.0 + index * 0.01 for index in range(14)], None, []),
        ([], None, []),
        "not-a-snapshot",
    ):
        try:
            engine.apply_snapshot(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"invalid snapshot accepted: {bad!r}")
        assert engine.snapshot() == before
    print("  invalid snapshots raise and never touch the engine")


# ---------------------------------------------------------------------------
# 5. Stopped / running returns through the bridge
# ---------------------------------------------------------------------------


def test_stopped_return() -> None:
    print("\n=== Test 5: stopped return is one set change and one update ===")
    with fake_bridge(seed=1, max_changes=2) as (bridge, client, ui, clock, rhythm):
        bridge.request_home_capture(0)
        assert bridge.home.has_home
        assert bridge.quantizer.pending_count == 0, "capture is immediate when stopped"
        for _ in range(3):
            bridge.request_mutation()
        home = bridge.home.home
        distance = bridge.home.distance(bridge.engine.snapshot())
        assert distance >= 1, distance
        history_before = bridge.home.history_depth

        client.sent.clear()
        ui.sent.clear()
        bridge.request_home_return()
        sent = updates(client)
        assert len(sent) == 1, sent
        assert math.isclose(sent[0][1], 0.06), sent[0]
        assert as_set(bridge.engine.snapshot()) == as_set(home)
        assert bridge.engine.anchor_ratio == home.anchor
        assert bridge.home.distance(bridge.engine.snapshot()) == 0
        assert bridge.home.history_depth == history_before + 1
        assert bridge.home.journey_mutations == 0
        states = home_states(ui)
        assert states, "one /ji/home/state after the return"
        parsed = osc_control.parse_home_state(states[-1])
        assert parsed.distance == 0 and parsed.has_home
        print(f"  distance {distance}: one immediate set, one freqset update")


def test_running_return() -> None:
    print("\n=== Test 6: running return is exactly one batch per quarter beat ===")
    with fake_bridge(seed=7, max_changes=1) as (bridge, client, ui, clock, rhythm):
        bridge.request_home_capture(0)
        bridge._on_rhythm_transport(True)
        for _ in range(20):
            bridge.request_mutation()
            tick(bridge, clock)
            if bridge.home.distance(bridge.engine.snapshot()) >= 3:
                break
        home = bridge.home.home
        distance = bridge.home.distance(bridge.engine.snapshot())
        assert distance >= 3, distance
        batches = math.ceil(distance / bridge.engine.max_changes)

        client.sent.clear()
        ui.sent.clear()
        bridge.request_home_return()
        beats = 0
        per_beat: list[int] = []
        while bridge.quantizer.pending_count and beats <= batches + 2:
            before = len(updates(client))
            tick(bridge, clock)
            per_beat.append(len(updates(client)) - before)
            beats += 1
        assert beats == batches, (beats, batches, per_beat)
        assert per_beat == [1] * batches, per_beat
        assert bridge.quantizer.pending_count == 0
        assert as_set(bridge.engine.snapshot()) == as_set(home)
        assert bridge.engine.anchor_ratio == home.anchor
        assert bridge.home.distance(bridge.engine.snapshot()) == 0
        assert bridge.home.returning is False
        assert bridge.home.pending_batches == 0
        assert bridge.home.history_depth >= batches + 1
        print(f"  distance {distance}: {batches} beats, one update each, landed")


def test_anchor_added_last() -> None:
    print("\n=== Test 7: a missing home anchor is added in the last batch ===")
    with fake_bridge(seed=2, max_changes=1) as (bridge, client, ui, clock, rhythm):
        current = bridge.engine.snapshot()
        # Home keeps the current set but replaces 3/2 with 8/5 and anchors 8/5.
        home_classes = [ratio for ratio in current.ratios if ratio != 1.5] + [
            fold_ratio(8.0 / 5.0)
        ]
        home = SetSnapshot.create(home_classes, 8.0 / 5.0)
        bridge.home.capture(0, home)
        plan = bridge.home.plan(current, home, 1, 0)
        assert plan, plan
        assert home.anchor in [added for _removed, added in plan[-1]]
        assert all(
            added != home.anchor
            for batch in plan[:-1]
            for _removed, added in batch
        )

        bridge._on_rhythm_transport(True)
        bridge.request_home_return()
        beats = 0
        while bridge.quantizer.pending_count and beats < 6:
            tick(bridge, clock)
            beats += 1
        assert beats == len(plan)
        assert as_set(bridge.engine.snapshot()) == as_set(home)
        assert bridge.engine.anchor_ratio == home.anchor
        print(f"  anchor added in batch {len(plan)}/{len(plan)}; engine anchor set")


# ---------------------------------------------------------------------------
# 6. Journeys and auto return
# ---------------------------------------------------------------------------


def test_journey_determinism() -> None:
    print("\n=== Test 8: same home + seed + count reproduces the journey ===")

    def journey(seed: int, count: int = 5) -> list[tuple[float, ...]]:
        with fake_bridge(seed=1, max_changes=1) as (bridge, client, ui, clock, rhythm):
            assert bridge.home.start_journey(seed) is False, "needs a home first"
            bridge.request_home_capture(0)
            bridge.start_journey(seed)
            out = []
            for _ in range(count):
                bridge.request_mutation()
                out.append(tuple(sorted(bridge.engine.current_ratios)))
            return out

    first = journey(5)
    second = journey(5)
    other = journey(6)
    assert first == second, "same home+seed+count must reproduce the journey"
    assert first != other, "a different seed must produce a different journey"
    print("  seed 5 reproduces; seed 6 differs")


def test_auto_return() -> None:
    print("\n=== Test 9: Auto returns after Every mutations and resets ===")
    with fake_bridge(seed=3, max_changes=1) as (bridge, client, ui, clock, rhythm):
        bridge.request_home_capture(0)
        bridge._on_home_setting("auto", 1)
        bridge._on_home_setting("every", 2)
        assert bridge.home.config.auto and bridge.home.config.every == 2
        bridge._on_rhythm_transport(True)
        for _ in range(2):
            bridge.request_mutation()
            tick(bridge, clock)
        assert bridge.home.journey_mutations == 2
        assert bridge.home.journey_due()
        assert bridge.quantizer.pending_count == 1, "auto return queued next beat"
        beats = 0
        while bridge.quantizer.pending_count and beats < 6:
            tick(bridge, clock)
            beats += 1
        assert bridge.home.distance(bridge.engine.snapshot()) == 0
        assert bridge.home.journey_mutations == 0
        assert bridge.home.returning is False
        print(f"  2 mutations -> automatic return landed in {beats} beat(s)")


def test_return_cancellations() -> None:
    print("\n=== Test 10: mutation/octave/back/stop cancel a pending return ===")

    def setup(distance_target: int = 2):
        manager = fake_bridge(seed=11, max_changes=1)
        bridge, client, ui, clock, rhythm = manager.__enter__()
        bridge.request_home_capture(0)
        bridge._on_rhythm_transport(True)
        while bridge.home.distance(bridge.engine.snapshot()) < distance_target:
            bridge.request_mutation()
            tick(bridge, clock)
        bridge.request_home_return()
        tick(bridge, clock)  # batch 0 landed; more steps pending
        return manager, bridge, client, ui, clock

    # Manual mutation.
    manager, bridge, client, ui, clock = setup()
    try:
        assert bridge.home.returning
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            bridge.request_mutation()
        assert "[HOME] return cancelled by mutation" in out.getvalue()
        assert bridge.home.returning is False
        assert ("home", "step") not in bridge.quantizer.pending_keys
        before = tuple(bridge.engine.snapshot().ratios)
        tick(bridge, clock)
        assert tuple(bridge.engine.snapshot().ratios) != before
        assert bridge.home.returning is False
    finally:
        manager.__exit__(None, None, None)

    # Octave.
    manager, bridge, client, ui, clock = setup()
    try:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            bridge.request_octave()
        assert "[HOME] return cancelled by octave" in out.getvalue()
        assert bridge.home.returning is False
    finally:
        manager.__exit__(None, None, None)

    # Back.
    manager, bridge, client, ui, clock = setup()
    try:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            bridge.request_back()
        assert "[HOME] return cancelled by back" in out.getvalue()
        assert bridge.home.returning is False
        tick(bridge, clock)
        assert bridge.home.returning is False
    finally:
        manager.__exit__(None, None, None)

    # Transport stop.
    manager, bridge, client, ui, clock = setup()
    try:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            bridge._on_rhythm_transport(False)
        assert "[HOME] return cancelled by transport stop" in out.getvalue()
        assert bridge.home.returning is False
        assert bridge.quantizer.pending_count == 0
        assert bridge.rhythm.running is False
    finally:
        manager.__exit__(None, None, None)
    print("  mutation, octave, back, and transport stop each cancel once")


# ---------------------------------------------------------------------------
# 7. Drift cap
# ---------------------------------------------------------------------------


def test_drift_cap() -> None:
    print("\n=== Test 11: the drift cap keeps a journey near home ===")
    with fake_bridge(seed=13, max_changes=1) as (bridge, client, ui, clock, rhythm):
        bridge.request_home_capture(0)
        bridge._on_home_setting("max_drift", 2)
        distances = []
        for _ in range(24):
            bridge.request_mutation()
            distances.append(bridge.home.distance(bridge.engine.snapshot()))
        assert max(distances) <= 2, distances
        assert 0 <= min(distances) <= 2
        print(f"  drift 2 over 24 mutations: max distance {max(distances)}")

    with fake_bridge(seed=13, max_changes=1) as (bridge, client, ui, clock, rhythm):
        bridge.request_home_capture(0)
        assert bridge.home.config.max_drift == 0
        distances = []
        for _ in range(24):
            bridge.request_mutation()
            distances.append(bridge.home.distance(bridge.engine.snapshot()))
        assert max(distances) > 2, "drift 0 must be unlimited"
        print(f"  drift 0 over 24 mutations: max distance {max(distances)}")

    # Eight rejected tries accept the last proposal and log once.
    with fake_bridge(seed=13, max_changes=1) as (bridge, client, ui, clock, rhythm):
        bridge.request_home_capture(0)
        bridge._on_home_setting("max_drift", 1)
        bridge.home.distance = lambda current: 99  # force every try to fail
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            bridge.request_mutation()
        text = out.getvalue()
        assert f"[HOME] drift cap exceeded ({MAX_DRIFT_TRIES} tries)" in text
        print(f"  {MAX_DRIFT_TRIES} failed tries -> accepted + one log line")


# ---------------------------------------------------------------------------
# 8. Back
# ---------------------------------------------------------------------------


def test_back() -> None:
    print("\n=== Test 12: Back walks the history and stops at the oldest ===")
    with fake_bridge(seed=17, max_changes=1) as (bridge, client, ui, clock, rhythm):
        bridge.request_home_capture(0)
        sets = [tuple(bridge.engine.snapshot().ratios)]
        for _ in range(3):
            bridge.request_mutation()
            sets.append(tuple(bridge.engine.snapshot().ratios))
        assert bridge.home.history_depth == 4
        assert bridge.home.can_back

        bridge.request_back()
        assert tuple(bridge.engine.snapshot().ratios) == sets[-2]
        assert bridge.home.history_depth == 3
        bridge.request_back()
        assert tuple(bridge.engine.snapshot().ratios) == sets[-3]
        bridge.request_back()
        assert tuple(bridge.engine.snapshot().ratios) == sets[0]
        assert bridge.home.history_depth == 1
        assert not bridge.home.can_back

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            bridge.request_back()
        assert "already at the oldest snapshot" in out.getvalue()
        assert tuple(bridge.engine.snapshot().ratios) == sets[0]

        # Redo is available in the manager; a forward change clears it.
        assert bridge.home.can_redo
        restored = bridge.home.redo()
        assert restored is not None
        assert tuple(restored.ratios) == sets[1]
        bridge.home.back()
        assert bridge.home.can_redo
        bridge.request_mutation()
        assert not bridge.home.can_redo
        print("  three Backs reach the oldest; a forward change clears redo")


def test_back_restores_anchor_and_octaves() -> None:
    print("\n=== Test 13: Back restores ratios, anchor and voicing exactly ===")
    with fake_bridge(seed=19, max_changes=1) as (bridge, client, ui, clock, rhythm):
        bridge.engine.set_anchor_mobility(0.0)
        bridge.engine.set_analysis_octave(1.5, 2)
        bridge.engine.set_anchor(1.5)
        bridge.request_mutation()
        before = bridge.engine.snapshot()
        bridge.request_mutation()
        bridge.request_back()
        restored = bridge.engine.snapshot()
        assert restored == before
        assert restored.anchor == 1.5
        assert restored.octave_of(1.5) == 2
        print("  ratio set, anchor and octaves come back exactly")


# ---------------------------------------------------------------------------
# 9. Bridge / OSC / state surface
# ---------------------------------------------------------------------------


def test_bridge_quantization() -> None:
    print("\n=== Test 14: home requests are beat-quantized, immediate when stopped ===")
    with fake_bridge(seed=23, max_changes=1) as (bridge, client, ui, clock, rhythm):
        bridge.request_home_capture(0)
        assert bridge.home.has_home
        assert bridge.quantizer.pending_count == 0

        bridge._on_rhythm_transport(True)
        bridge.request_home_capture(1)
        assert bridge.quantizer.pending_count == 1
        assert bridge.home.slot_snapshot(1) is None
        tick(bridge, clock)
        assert bridge.home.slot_snapshot(1) is not None

        # Coalescing: two captures of the same slot before the beat -> one.
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            bridge.request_home_capture(2)
            bridge.request_home_capture(2)
        assert bridge.quantizer.pending_count == 1
        assert "coalesced 2 home capture C requests" in out.getvalue()
        tick(bridge, clock)
        assert bridge.home.slot_snapshot(2) is not None

        # A pending capture flushes on transport stop instead of being lost.
        bridge.request_home_capture(3)
        assert bridge.quantizer.pending_count == 1
        bridge._on_rhythm_transport(False)
        assert bridge.quantizer.pending_count == 0
        assert bridge.home.slot_snapshot(3) is not None
        print("  stopped immediate; running queued; coalescing; stop flush")

    # /ji/home/state shape.
    with fake_bridge(seed=29) as (bridge, client, ui, clock, rhythm):
        bridge._send_home_state()
        states = home_states(ui)
        assert len(states) == 1
        assert len(states[0]) == 13
        assert all(isinstance(value, int) for value in states[0])
        parsed = osc_control.parse_home_state(states[0])
        assert parsed.active_slot == -1 and not parsed.has_home
        assert parsed.status_line() == "Home: none (Set home to capture)"
        bridge.request_home_capture(0)
        bridge._send_home_state()
        parsed = osc_control.parse_home_state(home_states(ui)[-1])
        assert parsed.has_home and parsed.active_slot == 0
        assert parsed.home_classes == len(bridge.engine.current_ratios)
        assert parsed.slot_mask == 1
        assert "Home: A ·" in parsed.status_line()
        print("  13-value state round-trips through the pure parser")


def test_osc_handlers() -> None:
    print("\n=== Test 15: /ji/home/* handlers validate and never raise ===")
    engine = MutationEngine(INITIAL, seed=3)
    captured: list[int] = []
    cleared: list[int] = []
    went: list[int] = []
    returned: list[int] = []
    jumped: list[int] = []
    backed: list[int] = []
    settings: list[tuple[str, object]] = []
    server = MutationOSCServer(
        engine,
        listen_port=19313,
        on_home_capture=captured.append,
        on_home_clear=cleared.append,
        on_home_go=went.append,
        on_home_return=lambda: returned.append(1),
        on_home_jump=lambda: jumped.append(1),
        on_home_back=lambda: backed.append(1),
        on_home_setting=lambda name, value: settings.append((name, value)),
    )
    assert HOME_CAPTURE_ADDRESS == "/ji/home/capture"
    assert HOME_CLEAR_ADDRESS == "/ji/home/clear"
    assert HOME_GO_ADDRESS == "/ji/home/go"
    assert HOME_RETURN_ADDRESS == "/ji/home/return"
    assert HOME_JUMP_ADDRESS == "/ji/home/jump"
    assert HOME_BACK_ADDRESS == "/ji/home/back"
    assert HOME_SET_AUTO_ADDRESS == "/ji/home/set_auto"
    assert HOME_SET_EVERY_ADDRESS == "/ji/home/set_every"
    assert HOME_SET_SEED_ADDRESS == "/ji/home/set_seed"
    assert HOME_SET_DRIFT_ADDRESS == "/ji/home/set_drift"
    assert HOME_SET_GLIDE_ADDRESS == "/ji/home/set_glide"

    server._handle_home_capture("/ji/home/capture", 2)
    server._handle_home_capture("/ji/home/capture", 9)
    server._handle_home_capture("/ji/home/capture")
    assert captured == [2]
    server._handle_home_clear("/ji/home/clear", 1)
    assert cleared == [1]
    server._handle_home_go("/ji/home/go", 3)
    assert went == [3]
    server._handle_home_return("/ji/home/return")
    server._handle_home_return("/ji/home/return", "extra")
    assert returned == [1, 1]
    server._handle_home_jump("/ji/home/jump")
    server._handle_home_back("/ji/home/back")
    assert jumped == [1] and backed == [1]

    server._handle_home_auto("/ji/home/set_auto", 1)
    server._handle_home_auto("/ji/home/set_auto", 2)
    server._handle_home_every("/ji/home/set_every", 99)
    server._handle_home_every("/ji/home/set_every", 0)
    server._handle_home_seed("/ji/home/set_seed", 2**40)
    server._handle_home_seed("/ji/home/set_seed", -3)
    server._handle_home_drift("/ji/home/set_drift", 99)
    server._handle_home_drift("/ji/home/set_drift", -1)
    server._handle_home_glide("/ji/home/set_glide", 1.0)
    server._handle_home_glide("/ji/home/set_glide", 0.0)
    server._handle_home_glide("/ji/home/set_glide", float("nan"))
    assert settings == [
        ("auto", True),
        ("every", 32),
        ("every", 1),
        ("seed", 2**31 - 1),
        ("seed", 0),
        ("max_drift", 12),
        ("max_drift", 0),
        ("return_glide", 0.3),
        ("return_glide", 0.02),
    ], settings
    print("  slots 0..3, arity, and clamps (every/seed/drift/glide)")


def test_bridge_ui_home_state() -> None:
    print("\n=== Test 16: bridge sends home state at start/hello/change/transport ===")
    with fake_bridge(seed=31) as (bridge, client, ui, clock, rhythm):
        ui.sent.clear()
        bridge._send_home_state()
        assert ui.addresses() == ["/ji/home/state"]
        ui.sent.clear()
        bridge.request_home_capture(0)
        assert "/ji/home/state" in ui.addresses()
        ui.sent.clear()
        bridge._on_home_setting("every", 4)
        assert "/ji/home/state" in ui.addresses()
        ui.sent.clear()
        bridge._on_rhythm_transport(True)
        assert "/ji/home/state" in ui.addresses()
        ui.sent.clear()
        bridge._on_rhythm_transport(False)
        assert "/ji/home/state" in ui.addresses()
        ui.sent.clear()
        bridge.server._handle_ui_hello("/ji/ui/hello")
        assert "/ji/home/state" in ui.addresses()
        print("  start/hello/settings/transport all publish /ji/home/state")


def test_no_mido() -> None:
    print("\n=== Test 17: pure construction imports no mido ===")
    assert "mido" not in sys.modules
    with fake_bridge(seed=37):
        assert "mido" not in sys.modules
    manager = HomeManager()
    manager.capture(0, SetSnapshot.create(INITIAL, 1.0))
    plan_return(manager.home, manager.home, 3, 1)
    assert "mido" not in sys.modules
    print("  bridge/manager/planner never import mido")


def test_phase_order() -> None:
    print("\n=== Test 18: PHASE_HOME is first and the older phases keep their order ===")
    assert PHASE_HOME == 0
    assert PHASE_MUTATION == 1
    assert PHASE_NAMES[PHASE_HOME] == "home"
    assert PHASE_ORDER["home"] == 0
    order = [PHASE_NAMES[index] for index in sorted(PHASE_NAMES)]
    assert order == [
        "home",
        "mutation",
        "octave",
        "groove",
        "drums",
        "bass",
        "arp",
        "lead",
        "drone",
        "midi",
    ], order
    print("  home, mutation, octave, groove, drums, bass, arp, lead, drone, midi")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def run() -> None:
    print("JI Stage 8.4 home-set / return tests")
    test_config()
    test_slots()
    test_distance_and_plan()
    test_engine_additions()
    test_stopped_return()
    test_running_return()
    test_anchor_added_last()
    test_journey_determinism()
    test_auto_return()
    test_return_cancellations()
    test_drift_cap()
    test_back()
    test_back_restores_anchor_and_octaves()
    test_bridge_quantization()
    test_osc_handlers()
    test_bridge_ui_home_state()
    test_no_mido()
    test_phase_order()
    print("\nALL HOME TESTS PASSED")


if __name__ == "__main__":
    try:
        run()
    except Exception:
        traceback.print_exc()
        print("\nHOME TESTS FAILED", file=sys.stderr)
        raise SystemExit(1)

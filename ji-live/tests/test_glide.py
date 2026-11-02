#!/usr/bin/env python3
"""Stage 8.4.1 pure tests: the stepwise/replaced class glide.

Run from ``ji-live/``::

    uv run --with python-osc python tests/test_glide.py

No audio, MIDI, Qt, or real sockets: the planner and scheduler are pure, and the
bridge is driven through the existing fake-UDP / fake-clock harness.  The live
rig (sclang, Ableton, IAC, the GUI) remains a human check.
"""

from __future__ import annotations

import contextlib
import io
import math
import sys
import threading
import time
import traceback
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import control.glide as glide_module
import control.ji_bridge as bridge_module
from control.glide import (
    GLIDE_RATE_HZ,
    SNAP_GLIDE_SECONDS,
    GlidePlan,
    GlideScheduler,
    build_glide_plan,
)
from control.mutation import (
    MutationBatch,
    MutationEngine,
    MutationEvent,
    SetSnapshot,
    fold_ratio,
)
from control.rhythm import RhythmEngine

INITIAL = [1.0, 9.0 / 8.0, 5.0 / 4.0, 4.0 / 3.0, 3.0 / 2.0, 5.0 / 3.0, 15.0 / 8.0]

#: A class that genuinely moves (1.4 is not in the initial set, so the engine
#: removes 3/2 and adds 7/5 instead of collapsing the replacement into a removal).
MOVE = MutationEvent(
    removed_ratio=1.5,
    new_ratio=1.4,
    glide_time=0.15,
    reason="stepwise",
)
OLDS = (1.5,)
NEWS = (fold_ratio(1.4),)


def engine_after(event: MutationEvent) -> tuple[SetSnapshot, SetSnapshot]:
    """Return ``(before, after)`` for one event applied to a fresh engine."""

    engine = MutationEngine(INITIAL, seed=1)
    before = engine.snapshot()
    engine.apply(event)
    return before, engine.snapshot()


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


class FakeMidiRig:
    """Records the transient flag of every set fan-out."""

    midi_enabled = True
    drone_enabled = True
    arp_enabled = True

    def __init__(self) -> None:
        self.config = SimpleNamespace(drone_port="Bus 1", arp_port="Bus 2")
        self.calls: list[tuple[tuple[float, ...], float | None, bool]] = []

    def on_set(
        self,
        ratios,
        anchor,
        fundamental,
        *,
        transient: bool = False,
    ) -> None:
        self.calls.append((tuple(ratios), anchor, bool(transient)))


class ExplodingMidiRig(FakeMidiRig):
    def on_set(self, *args, **kwargs):  # noqa: ANN002, ANN003 - test double
        raise RuntimeError("no bus")


@contextlib.contextmanager
def fake_bridge(**kwargs):
    """A started bridge with fake UDP, a fake clock, and a manual glide clock."""

    original = bridge_module.SimpleUDPClient
    FakeClient.instances = []
    bridge_module.SimpleUDPClient = FakeClient
    try:
        clock = FakeClock(100.0)
        bridge = bridge_module.JIBridge(**kwargs)
        rhythm = RhythmEngine(bpm=120.0, now=clock)
        bridge.rhythm = rhythm
        bridge.quantizer._rhythm = rhythm
        bridge.quantizer._now = clock
        # Manual scheduler: tests never sleep and never leak a thread.
        bridge.glide = GlideScheduler(
            bridge._on_glide_step, now=clock, threaded=False
        )
        # The bridge only glides once start() has happened; this is the seam.
        bridge._started = True
        client, ui_client = FakeClient.instances
        yield bridge, client, ui_client, clock
    finally:
        bridge_module.SimpleUDPClient = original


def tick(bridge, clock, beats: int = 1) -> int:
    """Advance the fake clock one quarter beat and run the bridge queue."""

    applied = 0
    for _ in range(beats):
        clock.advance(0.5)  # 120 BPM
        applied += bridge.quantizer.tick()
        bridge.glide.tick()
    return applied


def updates(client: FakeClient) -> list[list]:
    return [args for address, args in client.sent if address == "/ji/freqset/update"]


def announce(bridge, event, before=None) -> None:
    """Apply ``event`` (unless already applied) and announce it through the bridge."""

    if before is None:
        before = bridge.engine.snapshot()
        bridge.engine.apply(event)
    bridge._pending_before = before
    bridge.server.announce(event)


# ---------------------------------------------------------------------------
# 1. Planner rejection / validation
# ---------------------------------------------------------------------------


def test_plan_rejections() -> None:
    print("\n=== Test 1: pure add/remove, zero duration, and bad input ===")
    before = SetSnapshot.create(INITIAL, 1.0)

    removed = MutationEvent(1.5, None, 0.15, "removed")
    after_removed = SetSnapshot.create(
        [ratio for ratio in before.ratios if ratio != 1.5], 1.0
    )
    assert build_glide_plan(before, after_removed, removed, 0.15) is None

    added = MutationEvent(None, 1.4, 0.15, "added")
    after_added = SetSnapshot.create(list(before.ratios) + [1.4], 1.0)
    assert build_glide_plan(before, after_added, added, 0.15) is None

    real_before, real_after = engine_after(MOVE)
    assert build_glide_plan(real_before, real_after, MOVE, 0.0) is None
    assert build_glide_plan(real_before, real_after, MOVE, -0.1) is None

    # Defensive consistency: the events must explain the engine's final set.
    wrong_after = SetSnapshot.create(INITIAL, 1.0)
    assert build_glide_plan(real_before, wrong_after, MOVE, 0.15) is None

    for bad_call in (
        lambda: build_glide_plan("nope", real_after, MOVE, 0.15),  # type: ignore[arg-type]
        lambda: build_glide_plan(real_before, real_after, 42, 0.15),  # type: ignore[arg-type]
        lambda: build_glide_plan(
            real_before,
            real_after,
            MutationEvent(None, None, 0.15, "added"),
            0.15,
        ),
        lambda: build_glide_plan(
            real_before,
            real_after,
            MutationEvent(1.05, 1.1, 0.15, "replaced"),
            0.15,
        ),
        lambda: build_glide_plan(
            real_before,
            real_after,
            [
                MutationEvent(1.5, 1.4, 0.15, "stepwise"),
                MutationEvent(1.5, 1.6, 0.15, "stepwise"),
            ],
            0.15,
        ),
        lambda: build_glide_plan(real_before, real_after, MOVE, float("nan")),
        lambda: build_glide_plan(real_before, real_after, MOVE, 0.15, rate=0.0),
        lambda: build_glide_plan(real_before, real_after, [], 0.15),
    ):
        try:
            bad_call()
        except ValueError:
            pass
        else:
            raise AssertionError(f"malformed plan input was accepted: {bad_call}")

    plan = build_glide_plan(real_before, real_after, MOVE, 0.15)
    assert plan is not None and plan.steps == 5
    print("  pure moves/duration 0 return None; malformed input raises")


# ---------------------------------------------------------------------------
# 2. One pair: endpoints, geometric midpoint, monotonic cents, rounding
# ---------------------------------------------------------------------------


def test_single_pair() -> None:
    print("\n=== Test 2: one pair melts cents-linearly and lands exactly ===")
    before, after = engine_after(MOVE)
    plan = build_glide_plan(before, after, MOVE, 0.15)
    assert plan is not None

    start = plan.snapshot_at(0.0)
    assert start.ratios == before.ratios, "t=0 is the before set"
    assert plan.snapshot_at(plan.duration) == after
    assert plan.final_snapshot == after
    assert plan.snapshot_at(plan.duration + 1.0) == after

    old, new = plan.pairs[0]
    assert (old, new) == (1.5, fold_ratio(1.4))
    midpoint = plan.snapshot_at(plan.duration / 2.0)
    expected_mid = fold_ratio(math.sqrt(old * new))
    assert expected_mid in midpoint.ratios, (midpoint.ratios, expected_mid)
    assert old not in midpoint.ratios, "the old class has left the set"
    assert new not in midpoint.ratios or math.isclose(
        fold_ratio(math.sqrt(old * new)), new
    )

    # The moving class descends monotonically in cents and is 12-decimal
    # canonical at every step.
    previous = None
    for index in range(plan.steps + 1):
        t = plan.duration * index / plan.steps
        snapshot = plan.snapshot_at(t)
        moving = [
            ratio
            for ratio in snapshot.ratios
            if math.isclose(
                ratio,
                fold_ratio(old * (new / old) ** (index / plan.steps)),
                rel_tol=0.0,
                abs_tol=1e-12,
            )
        ]
        assert moving, (index, snapshot.ratios)
        value = moving[0]
        assert value == round(value, 12), value
        assert 1.0 <= value < 2.0
        if previous is not None:
            assert value <= previous + 1e-12, (index, previous, value)
        previous = value
    print(
        f"  {plan.steps} steps: old {old:.6f} -> mid {expected_mid:.6f} -> "
        f"{new:.6f}, monotonic"
    )


# ---------------------------------------------------------------------------
# 3. Multiple pairs plus pure adds/removes
# ---------------------------------------------------------------------------


def test_batch_with_pure_edits() -> None:
    print("\n=== Test 3: two pairs travel together; pure edits apply at t=0 ===")
    engine = MutationEngine(INITIAL, seed=2)
    before = engine.snapshot()
    events = [
        MutationEvent(1.5, 1.4, 0.3, "stepwise"),
        MutationEvent(1.25, 1.2, 0.3, "replaced"),
        MutationEvent(None, 1.9, 0.3, "added"),
        MutationEvent(1.875, None, 0.3, "removed"),
    ]
    for event in events:
        engine.apply(event)
    after = engine.snapshot()
    batch = MutationBatch(tuple(events), 0.3)

    plan = build_glide_plan(before, after, batch, 0.3)
    assert plan is not None
    assert len(plan.pairs) == 2
    assert plan.pure_adds == (fold_ratio(1.9),)
    assert plan.pure_removes == (fold_ratio(1.875),)
    assert plan.steps == 9

    start = plan.snapshot_at(0.0)
    assert fold_ratio(1.9) in start.ratios, "pure addition applies at t=0"
    assert fold_ratio(1.875) not in start.ratios, "pure removal applies at t=0"
    for old, _new in plan.pairs:
        assert old in start.ratios, "a replacement is still at its old ratio at t=0"
    assert plan.snapshot_at(plan.duration) == after

    # Both moving classes are absent at the final step and present mid-melt.
    mid = plan.snapshot_at(plan.duration / 2.0)
    for old, new in plan.pairs:
        assert old not in mid.ratios
        assert new not in mid.ratios or new in before.ratios
    print("  two pairs + one add + one remove; t=0 and t=T are exact")


# ---------------------------------------------------------------------------
# 4. Anchor rules
# ---------------------------------------------------------------------------


def test_anchor_rules() -> None:
    print("\n=== Test 4: a replaced anchor travels; a removed one falls back ===")
    engine = MutationEngine(INITIAL, seed=3)
    engine.set_anchor(1.5)
    before = engine.snapshot()
    event = MutationEvent(1.5, 1.4, 0.15, "stepwise")
    engine.apply(event)
    after = engine.snapshot()
    assert before.anchor == 1.5
    plan = build_glide_plan(before, after, event, 0.15)
    assert plan is not None
    mid = plan.snapshot_at(plan.duration / 2.0)
    expected = fold_ratio(1.5 * (fold_ratio(1.4) / 1.5) ** 0.5)
    assert mid.anchor == expected, (mid.anchor, expected)
    for index in range(plan.steps):
        snapshot = plan.snapshot_at(plan.duration * index / plan.steps)
        assert snapshot.anchor is not None
        assert snapshot.anchor in snapshot.ratios, snapshot

    # A pure removal of the anchor uses the engine's re-anchored class.
    engine = MutationEngine(INITIAL, seed=4)
    engine.set_anchor(1.5)
    before = engine.snapshot()
    remove = MutationEvent(1.5, None, 0.15, "removed")
    add = MutationEvent(None, 1.4, 0.15, "added")
    engine.apply(remove)
    engine.apply(add)
    after = engine.snapshot()
    assert after.anchor != 1.5
    plan = build_glide_plan(before, after, [remove, add], 0.15)
    assert plan is None, "pure edits never glide"

    # Same removal plus a real pair elsewhere still glides and the fallback
    # anchor is the engine's final anchor once it is in the set.
    engine = MutationEngine(INITIAL, seed=5)
    engine.set_anchor(1.5)
    before = engine.snapshot()
    events = [
        MutationEvent(1.5, None, 0.15, "removed"),
        MutationEvent(1.25, 1.2, 0.15, "replaced"),
    ]
    for item in events:
        engine.apply(item)
    after = engine.snapshot()
    plan = build_glide_plan(before, after, events, 0.15)
    assert plan is not None
    start = plan.snapshot_at(0.0)
    assert start.anchor == after.anchor, "a removed anchor falls back immediately"
    assert start.anchor in start.ratios
    print("  replaced anchor travels with its pair; removed anchor falls back")


# ---------------------------------------------------------------------------
# 5. Steps, timing, and the final flag
# ---------------------------------------------------------------------------


def test_steps_and_callback_timing() -> None:
    print("\n=== Test 5: steps + 1 snapshots at exactly k*T/steps ===")
    before, after = engine_after(MOVE)
    assert build_glide_plan(before, after, MOVE, 0.15).steps == 5
    assert build_glide_plan(before, after, MOVE, 2.0).steps == 60
    assert build_glide_plan(before, after, MOVE, 0.01).steps == 1
    assert build_glide_plan(before, after, MOVE, 0.15, rate=1.0).rate == 5.0
    assert build_glide_plan(before, after, MOVE, 0.15, rate=999.0).rate == 60.0
    assert build_glide_plan(before, after, MOVE, 0.15, rate=GLIDE_RATE_HZ)

    plan = build_glide_plan(before, after, MOVE, 0.15)
    clock = FakeClock(0.0)
    stamps: list[tuple[float, bool]] = []
    scheduler = GlideScheduler(
        lambda snapshot, final: stamps.append((clock.now, final)),
        now=clock,
        threaded=False,
    )
    scheduler.start(plan)
    assert len(stamps) == 1 and stamps[0] == (0.0, False)
    for index in range(1, plan.steps + 1):
        clock.now = plan.duration * index / plan.steps
        assert scheduler.tick() == 1
    assert len(stamps) == plan.steps + 1
    assert stamps[0][0] == 0.0
    for index, (stamp, final) in enumerate(stamps):
        expected = plan.duration * index / plan.steps
        assert math.isclose(stamp, expected, abs_tol=1e-9), (index, stamp, expected)
        assert final is (index == plan.steps)
    assert sum(1 for _stamp, final in stamps if final) == 1
    print(f"  {plan.steps + 1} callbacks, final exactly once at t={plan.duration}")


# ---------------------------------------------------------------------------
# 6. Scheduler with a fake clock
# ---------------------------------------------------------------------------


def test_scheduler_fake_clock() -> None:
    print("\n=== Test 6: lazy thread, tick, cancel, restart, stop ===")
    before, after = engine_after(MOVE)
    plan = build_glide_plan(before, after, MOVE, 0.15)
    assert plan is not None

    threads_before = threading.active_count()
    clock = FakeClock(500.0)
    calls: list[tuple[tuple[float, ...], bool]] = []
    scheduler = GlideScheduler(
        lambda snapshot, final: calls.append((snapshot.ratios, final)),
        now=clock,
        threaded=False,
    )
    assert threading.active_count() == threads_before
    assert scheduler.started is False
    assert scheduler.active is False and scheduler.plan is None
    assert scheduler.progress == 0.0

    assert scheduler.start(plan) is True
    assert scheduler.active is True
    assert scheduler.plan is plan
    assert len(calls) == 1, "step 0 is synchronous"
    assert threading.active_count() == threads_before, "manual mode never starts a thread"

    assert scheduler.tick() == 0, "nothing is due yet"
    clock.now += 0.03
    assert scheduler.tick() == 1
    assert scheduler.tick() == 0, "each step is delivered once"
    assert math.isclose(scheduler.progress, 0.03 / 0.15, rel_tol=1e-6)

    clock.now += 10.0
    assert scheduler.tick() == plan.steps - 1
    assert scheduler.active is False and scheduler.plan is None
    assert scheduler.tick() == 0
    assert sum(1 for _ratios, final in calls if final) == 1

    # Cancel without snap: no extra callback.
    scheduler.start(plan)
    count = len(calls)
    assert scheduler.cancel(snap=False) is True
    assert len(calls) == count
    assert scheduler.cancel() is False, "cancel is idempotent"
    assert scheduler.active is False

    # Cancel with snap: exactly one final delivery.
    scheduler.start(plan)
    count = len(calls)
    assert scheduler.cancel() is True
    assert len(calls) == count + 1 and calls[-1] == (after.ratios, True)
    assert scheduler.cancel() is False

    # Start while active drops the previous plan (no snap).
    scheduler.start(plan)
    scheduler.start(plan)
    assert scheduler.active is True and scheduler.plan is plan
    assert scheduler.stop() is True
    assert scheduler.active is False

    # A failing callback never strands the glide.
    broken = GlideScheduler(
        lambda _snapshot, _final: (_ for _ in ()).throw(RuntimeError("boom")),
        now=clock,
        threaded=False,
    )
    with contextlib.redirect_stderr(io.StringIO()):
        broken.start(plan)
        clock.now += 10.0
        broken.tick()
    assert broken.active is False
    print("  synchronous step 0, due-step ticks, idempotent cancel/stop")


def test_scheduler_thread_lifecycle() -> None:
    print("\n=== Test 7: the worker is lazy, daemon, and joins cleanly ===")
    before, after = engine_after(MOVE)
    plan = build_glide_plan(before, after, MOVE, 0.05)
    assert plan is not None and plan.steps == 2

    done = threading.Event()
    calls: list[bool] = []

    def on_step(snapshot, final):  # noqa: ANN001 - test callback
        calls.append(final)
        if final:
            done.set()

    threads_before = threading.active_count()
    scheduler = GlideScheduler(on_step)
    assert scheduler.started is False
    assert threading.active_count() == threads_before, "no thread in construction"

    assert scheduler.start(plan) is True
    assert scheduler.started is True
    assert done.wait(2.0), "the worker did not finish the plan"
    for _ in range(200):
        if not scheduler.started:
            break
        time.sleep(0.01)
    assert scheduler.started is False, "the worker did not exit"
    assert calls == [False] * plan.steps + [True]
    assert scheduler.stop() is False

    # stop() interrupts a long plan and joins.
    long_plan = build_glide_plan(before, after, MOVE, 2.0)
    assert long_plan is not None
    scheduler = GlideScheduler(lambda _snapshot, _final: None)
    scheduler.start(long_plan)
    assert scheduler.started is True
    started = time.monotonic()
    assert scheduler.stop() is True
    assert time.monotonic() - started < 1.0, "stop did not join promptly"
    assert scheduler.started is False and scheduler.active is False
    print("  no construction thread; daemon worker exits; stop joins")


def test_no_mido() -> None:
    print("\n=== Test 8: the planner imports no mido ===")
    assert "mido" not in sys.modules
    before, after = engine_after(MOVE)
    build_glide_plan(before, after, MOVE, 0.15)
    assert "mido" not in sys.modules
    GlideScheduler(lambda _snapshot, _final: None, threaded=False)
    assert "mido" not in sys.modules
    print("  planner/scheduler never import mido")


# ---------------------------------------------------------------------------
# 9. Bridge: the bounded stream, MIDI transient fan-out, one UI mirror
# ---------------------------------------------------------------------------


def test_bridge_glide_stream() -> None:
    print("\n=== Test 9: steps+1 updates, transient MIDI, one UI mirror ===")
    with fake_bridge(seed=1) as (bridge, client, ui, clock):
        bridge.midi = FakeMidiRig()
        ui.sent.clear()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            announce(bridge, MOVE)
        plan = bridge.glide.plan
        assert plan is not None and plan.steps == 5
        assert bridge.glide.active is True
        assert "[GLIDE] replaced 1.500000 -> 1.400000 over 0.150 s (5 steps)" in (
            out.getvalue()
        ), out.getvalue()

        assert len(updates(client)) == 1, "only step 0 so far"
        assert [call[2] for call in bridge.midi.calls] == [True]
        assert "/ji/freqset/changed" not in ui.addresses()

        with contextlib.redirect_stdout(out):
            for index in range(1, plan.steps + 1):
                clock.now += plan.duration / plan.steps
                assert bridge.glide.tick() == 1
        sent = updates(client)
        assert len(sent) == plan.steps + 1, sent
        assert sent[-1][1] == 0.15
        assert tuple(sorted(sent[-1][2:])) == bridge.engine.snapshot().ratios
        assert [call[2] for call in bridge.midi.calls] == [True] * 5 + [False]
        mirror = [args for address, args in ui.sent if address == "/ji/freqset/changed"]
        assert len(mirror) == 1, mirror
        assert tuple(mirror[0][2:]) == bridge.engine.snapshot().ratios
        assert mirror[0][1] == bridge.engine.anchor_ratio
        assert "[GLIDE] done (0.150 s)" in out.getvalue(), out.getvalue()
        assert bridge.glide.active is False
        print("  6 updates, 5 transient MIDI sets, one final UI mirror")


def test_bridge_reasons_and_pure_changes() -> None:
    print("\n=== Test 10: 'replaced' glides; pure add/remove snaps in one update ===")
    with fake_bridge(seed=1) as (bridge, client, ui, clock):
        replaced = MutationEvent(1.5, 1.4, 0.2, "replaced")
        with contextlib.redirect_stdout(io.StringIO()):
            announce(bridge, replaced)
        assert bridge.glide.active is True
        assert len(updates(client)) == 1
        plan = bridge.glide.plan
        assert plan is not None and plan.steps == 6
        clock.now += 1.0
        bridge.glide.tick()
        assert len(updates(client)) == plan.steps + 1, "6 steps -> 7 updates"

    for event, after_ratios in (
        (MutationEvent(1.5, None, 0.15, "removed"), None),
        (MutationEvent(None, 1.4, 0.15, "added"), None),
    ):
        with fake_bridge(seed=1) as (bridge, client, ui, clock):
            with contextlib.redirect_stdout(io.StringIO()):
                announce(bridge, event)
            assert bridge.glide.active is False
            sent = updates(client)
            assert len(sent) == 1, sent
            assert math.isclose(sent[0][1], SNAP_GLIDE_SECONDS), sent[0]
            assert tuple(sorted(sent[0][2:])) == bridge.engine.snapshot().ratios
            assert len([a for a in ui.addresses() if a == "/ji/freqset/changed"]) == 1
    print("  replaced glides; a pure add/remove uses the 5 ms snap")


# ---------------------------------------------------------------------------
# 11. Bridge cancel rules
# ---------------------------------------------------------------------------


def test_bridge_cancel_rules() -> None:
    print("\n=== Test 11: a new mutation/return/go/back/jump cancels; stop does not ===")

    with fake_bridge(seed=1) as (bridge, client, ui, clock):
        with contextlib.redirect_stdout(io.StringIO()):
            announce(bridge, MOVE)
        first_after = bridge.engine.snapshot()
        clock.now += 0.03
        bridge.glide.tick()
        sent_before = len(updates(client))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            bridge._mutate()
        assert "[GLIDE] cancelled by mutation" in out.getvalue(), out.getvalue()
        sent = updates(client)
        assert len(sent) > sent_before
        assert tuple(sorted(sent[sent_before][2:])) == first_after.ratios, "one final snap"
        assert math.isclose(sent[sent_before][1], SNAP_GLIDE_SECONDS)
        # The new change either started its own glide or snapped; either way the
        # last broadcast is the engine's final set after it settles.
        with contextlib.redirect_stdout(io.StringIO()):
            clock.now += 1.0
            bridge.glide.tick()
        assert tuple(sorted(updates(client)[-1][2:])) == bridge.engine.snapshot().ratios

    def armed():
        manager = fake_bridge(seed=1)
        bridge, client, ui, clock = manager.__enter__()
        bridge.request_home_capture(0)
        with contextlib.redirect_stdout(io.StringIO()):
            announce(bridge, MOVE)
        assert bridge.glide.active is True
        return manager, bridge, client, ui, clock

    for label, action, message in (
        ("return", lambda b: b.request_home_return(), "[GLIDE] cancelled by return"),
        ("go", lambda b: b.request_home_go(0), "[GLIDE] cancelled by return"),
        ("back", lambda b: b.request_back(), "[GLIDE] cancelled by back"),
        ("jump", lambda b: b.request_home_jump(), "[GLIDE] cancelled by jump"),
    ):
        manager, bridge, client, ui, clock = armed()
        try:
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                action(bridge)
            assert message in out.getvalue(), (label, out.getvalue())
            assert bridge.glide.active is False, label
        finally:
            manager.__exit__(None, None, None)

    with fake_bridge(seed=1) as (bridge, client, ui, clock):
        with contextlib.redirect_stdout(io.StringIO()):
            announce(bridge, MOVE)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            bridge._on_rhythm_transport(False)
        assert bridge.glide.active is True, "a transport stop is not a set change"
        assert "[GLIDE] cancelled" not in out.getvalue()
    print("  mutation/return/go/back/jump cancel cleanly; stop leaves the morph")


# ---------------------------------------------------------------------------
# 12. State is final immediately; history/journey are unaffected
# ---------------------------------------------------------------------------


def test_bridge_state_is_final() -> None:
    print("\n=== Test 12: engine/history/journey are final while the set melts ===")
    with fake_bridge(seed=1) as (bridge, client, ui, clock):
        bridge.request_home_capture(0)
        depth = bridge.home.history_depth
        mutations = bridge.home.journey_mutations
        distance_before = bridge.home.distance(bridge.engine.snapshot())
        with contextlib.redirect_stdout(io.StringIO()):
            announce(bridge, MOVE)
        assert bridge.glide.active is True
        snapshot = bridge.engine.snapshot()
        assert snapshot != bridge.glide.plan.before
        assert bridge.home.history_depth == depth + 1
        assert bridge.home.journey_mutations == mutations + 1
        assert bridge.home.distance(snapshot) != distance_before
        assert bridge.home.history[-1] == snapshot
        # The UI is deliberately not spammed per step: the one mirror comes
        # with the final step and is the final set.
        mirror = [
            args for address, args in ui.sent if address == "/ji/freqset/changed"
        ]
        assert mirror == []
        clock.now += 1.0
        with contextlib.redirect_stdout(io.StringIO()):
            bridge.glide.tick()
        mirror = [
            args for address, args in ui.sent if address == "/ji/freqset/changed"
        ]
        assert len(mirror) == 1 and tuple(mirror[0][2:]) == snapshot.ratios
    print("  history/journey/distance use the final set; one final UI mirror")


# ---------------------------------------------------------------------------
# 13. Quantization: stopped starts now, running starts at the beat
# ---------------------------------------------------------------------------


def test_bridge_quantized_start() -> None:
    print("\n=== Test 13: stopped -> immediately; running -> the mutation's beat ===")
    with fake_bridge(seed=1) as (bridge, client, ui, clock):
        assert bridge.rhythm.running is False
        before = bridge.engine.snapshot()
        with contextlib.redirect_stdout(io.StringIO()):
            bridge.request_mutation()
        assert bridge.engine.snapshot() != before
        assert bridge.quantizer.pending_count == 0
        assert len(updates(client)) == 1, "one update (step 0 or the snap) immediately"

    with fake_bridge(seed=1) as (bridge, client, ui, clock):
        bridge._on_rhythm_transport(True)
        started = False
        for _ in range(30):
            client.sent.clear()
            before = bridge.engine.snapshot()
            with contextlib.redirect_stdout(io.StringIO()):
                bridge.request_mutation()
            assert bridge.engine.snapshot() == before, "the mutation waits for the beat"
            assert bridge.glide.active is False
            assert updates(client) == []
            assert bridge.quantizer.pending_count == 1
            with contextlib.redirect_stdout(io.StringIO()):
                tick(bridge, clock)
            assert bridge.quantizer.pending_count == 0
            assert bridge.engine.snapshot() != before
            if bridge.glide.active:
                started = True
                assert len(updates(client)) == 1, "step 0 lands with the beat"
                break
            assert len(updates(client)) == 1, "an add/remove snaps in one update"
        assert started, "no replacement mutation in 30 tries"
    print("  the mutation and its glide both start on the beat while running")


# ---------------------------------------------------------------------------
# 14. Degradation: no buses / a raising rig / a direct server trigger
# ---------------------------------------------------------------------------


def test_bridge_degradation() -> None:
    print("\n=== Test 14: missing/raising buses and a direct server trigger ===")
    with fake_bridge(seed=1) as (bridge, client, ui, clock):
        assert bridge.midi is None
        with contextlib.redirect_stdout(io.StringIO()):
            announce(bridge, MOVE)
        assert bridge.glide.active is True
        clock.now += 1.0
        with contextlib.redirect_stdout(io.StringIO()):
            bridge.glide.tick()
        assert tuple(sorted(updates(client)[-1][2:])) == bridge.engine.snapshot().ratios

    with fake_bridge(seed=1) as (bridge, client, ui, clock):
        bridge.midi = ExplodingMidiRig()
        log = io.StringIO()
        with contextlib.redirect_stderr(log), contextlib.redirect_stdout(io.StringIO()):
            announce(bridge, MOVE)
            clock.now += 1.0
            bridge.glide.tick()
        assert "[MIDI] set update failed" in log.getvalue()
        assert bridge.glide.active is False

    with fake_bridge(seed=1) as (bridge, client, ui, clock):
        # No captured ``before``: a direct server trigger must not glide.
        before = bridge.engine.snapshot()
        bridge.engine.apply(MOVE)
        assert bridge._pending_before is None
        with contextlib.redirect_stdout(io.StringIO()):
            bridge.server.announce(MOVE)
        assert bridge.glide.active is False
        sent = updates(client)
        assert len(sent) == 1 and math.isclose(sent[0][1], 0.15), sent
        assert tuple(sorted(sent[0][2:])) == bridge.engine.snapshot().ratios
        assert bridge.engine.snapshot() != before
    print("  no buses, a raising rig, and a direct trigger all stay safe")


def main() -> int:
    print("JI Stage 8.4.1 glide pure tests")
    test_plan_rejections()
    test_single_pair()
    test_batch_with_pure_edits()
    test_anchor_rules()
    test_steps_and_callback_timing()
    test_scheduler_fake_clock()
    test_scheduler_thread_lifecycle()
    test_no_mido()
    test_bridge_glide_stream()
    test_bridge_reasons_and_pure_changes()
    test_bridge_cancel_rules()
    test_bridge_state_is_final()
    test_bridge_quantized_start()
    test_bridge_degradation()
    print("\nALL GLIDE TESTS PASSED")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        traceback.print_exc()
        print("\nGLIDE TESTS FAILED")
        raise SystemExit(1)

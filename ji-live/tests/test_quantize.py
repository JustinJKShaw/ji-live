#!/usr/bin/env python3
"""Stage 8.2 pure beat-quantized queue tests.

Run from ``ji-live/``::

    uv run --with python-osc python tests/test_quantize.py

No audio, MIDI, Qt, or sleeping (except one short real-thread wake check).  The
queue is driven by ``RhythmEngine`` with an injectable fake clock, so every
timing assertion is exact.
"""

from __future__ import annotations

import contextlib
import io
import sys
import threading
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import control.ji_bridge as bridge_module
from control.quantize import (
    PHASE_DRUMS,
    PHASE_GROOVE,
    PHASE_MIDI,
    PHASE_MUTATION,
    PHASE_OCTAVE,
    BeatQuantizer,
)
from control.rhythm import RhythmEngine


class FakeClock:
    def __init__(self, now: float = 100.0) -> None:
        self.now = float(now)

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += float(seconds)


class FakeClient:
    """Minimal SimpleUDPClient stand-in for the no-thread bridge check."""

    instances: list["FakeClient"] = []

    def __init__(self, host: str, port: int) -> None:
        self.host = host
        self.port = port
        self.sent: list[tuple[str, list]] = []
        FakeClient.instances.append(self)

    def send_message(self, address: str, args: list) -> None:
        self.sent.append((address, list(args)))


def _running_engine(clock: FakeClock, bpm: float = 120.0) -> RhythmEngine:
    engine = RhythmEngine(bpm=bpm, now=clock)
    engine.set_running(True)
    return engine


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_immediate_when_stopped() -> None:
    print("\n=== Test 1: transport stopped -> immediate, no queue ===")
    clock = FakeClock(100.0)
    rhythm = RhythmEngine(now=clock)
    applied: list[str] = []
    changes: list[int] = []
    queue = BeatQuantizer(rhythm, now=clock, on_change=lambda: changes.append(1))

    queued = queue.schedule(
        ("mutation",),
        lambda: applied.append("mutation"),
        phase=PHASE_MUTATION,
        label="mutation",
    )
    assert queued is False
    assert applied == ["mutation"]
    assert queue.pending_count == 0
    assert queue.seconds_to_next_beat() == -1.0
    assert changes, "on_change must fire for an immediate action"

    # A second immediate call still runs.
    queue.schedule(("octave",), lambda: applied.append("octave"))
    assert applied == ["mutation", "octave"]
    print("  stopped actions run immediately; pending stays 0; seconds = -1")


def test_next_beat_boundary() -> None:
    print("\n=== Test 2: running -> exactly the next quarter beat ===")
    clock = FakeClock(100.0)
    rhythm = _running_engine(clock)  # 120 BPM: beat = 0.5 s
    applied: list[float] = []
    queue = BeatQuantizer(rhythm, now=clock)

    # 100.0 is exactly beat 0; the next boundary is 100.5, never 100.0.
    queue.schedule(("mutation",), lambda: applied.append(clock.now))
    assert queue.pending_count == 1
    assert abs(queue.seconds_to_next_beat() - 0.5) < 1e-9
    queue.tick(100.0)
    queue.tick(100.25)
    assert applied == []
    queue.tick(100.499)
    assert applied == []
    clock.now = 100.5
    queue.tick(100.5)
    assert applied == [100.5]
    assert queue.pending_count == 0

    # A request exactly on the boundary waits for the following beat.
    queue.schedule(("mutation",), lambda: applied.append(clock.now))
    queue.tick(100.5)
    assert applied == [100.5]
    clock.now = 101.0
    queue.tick(101.0)
    assert applied == [100.5, 101.0]
    print("  never applies before or at the current instant; boundary waits")


def test_coalescing() -> None:
    print("\n=== Test 3: coalescing last-wins per key ===")
    clock = FakeClock(200.0)
    rhythm = _running_engine(clock)
    applied: list[str] = []
    queue = BeatQuantizer(rhythm, now=clock)

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        queue.schedule(
            ("mutation",),
            lambda: applied.append("first"),
            phase=PHASE_MUTATION,
            label="mutation",
        )
        clock.now = 200.1
        queue.schedule(
            ("mutation",),
            lambda: applied.append("second"),
            phase=PHASE_MUTATION,
            label="mutation",
        )
    assert queue.pending_count == 1
    assert "coalesced 2 mutation requests" in out.getvalue()
    clock.now = 200.5
    queue.tick(200.5)
    assert applied == ["second"], applied

    # Different keys all run.
    applied.clear()
    clock.now = 200.6
    queue.schedule(("a",), lambda: applied.append("a"), phase=PHASE_GROOVE)
    queue.schedule(("b",), lambda: applied.append("b"), phase=PHASE_GROOVE)
    queue.tick(201.0)
    assert applied == ["a", "b"], applied

    # coalesce=False keeps the first pending action for that key.
    applied.clear()
    clock.now = 201.1
    queue.schedule(
        ("c",), lambda: applied.append("c-first"), phase=PHASE_DRUMS, coalesce=False
    )
    queue.schedule(
        ("c",), lambda: applied.append("c-second"), phase=PHASE_DRUMS, coalesce=False
    )
    queue.tick(201.5)
    assert applied == ["c-first"], applied
    print("  one action per key per beat, last-wins, log count; distinct keys run")


def test_deterministic_order() -> None:
    print("\n=== Test 4: documented phase order at a beat ===")
    clock = FakeClock(300.0)
    rhythm = _running_engine(clock)
    applied: list[str] = []
    queue = BeatQuantizer(rhythm, now=clock)

    # Schedule in reverse order; phase order must still win.
    for name, phase in (
        ("midi", PHASE_MIDI),
        ("drums", PHASE_DRUMS),
        ("groove", PHASE_GROOVE),
        ("octave", PHASE_OCTAVE),
        ("mutation", PHASE_MUTATION),
    ):
        queue.schedule(
            (name,), lambda n=name: applied.append(n), phase=phase, label=name
        )
    queue.tick(300.5)
    assert applied == ["mutation", "octave", "groove", "drums", "midi"], applied
    print("  mutation, octave, groove, drums, midi")


def test_tempo_change_reschedules() -> None:
    print("\n=== Test 5: tempo change recomputes, never drops or applies early ===")
    clock = FakeClock(400.0)
    rhythm = _running_engine(clock, bpm=120.0)
    applied: list[str] = []
    queue = BeatQuantizer(rhythm, now=clock)

    queue.schedule(("mutation",), lambda: applied.append("mutation"))
    assert abs(queue.seconds_to_next_beat() - 0.5) < 1e-9

    clock.now = 400.2
    rhythm.set_bpm(60.0)  # beat = 1.0 s; beat position is 0.4
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        queue.tempo_changed()
    # Beat position is 0.4 at the tempo change, so the next beat is 400.8
    # (0.6 s away at the new 60 BPM), not the old 400.5.
    assert "tempo change: next beat recalculated" in out.getvalue()
    queue.tick(400.5)
    assert applied == []
    queue.tick(400.79)
    assert applied == []
    queue.tick(400.8)
    assert applied == ["mutation"]
    print("  pending action survives a tempo change at the new boundary")


def test_flush_and_stop() -> None:
    print("\n=== Test 6: transport stop flushes in order; cancel works ===")
    clock = FakeClock(500.0)
    rhythm = _running_engine(clock)
    applied: list[str] = []
    queue = BeatQuantizer(rhythm, now=clock)

    queue.schedule(("groove",), lambda: applied.append("groove"), phase=PHASE_GROOVE)
    queue.schedule(("mutation",), lambda: applied.append("mutation"), phase=PHASE_MUTATION)
    assert queue.pending_count == 2
    rhythm.set_running(False)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        flushed = queue.flush("transport stop")
    assert flushed == 2
    assert applied == ["mutation", "groove"], applied
    assert "flushed 2 pending actions on transport stop" in out.getvalue()
    assert queue.pending_count == 0

    # Pending action while stopped applies immediately (compatibility rule).
    applied.clear()
    queue.schedule(("drums",), lambda: applied.append("drums"))
    assert applied == ["drums"]

    # cancel removes a pending key.
    rhythm.set_running(True)
    clock.now = 501.0
    queue.schedule(("x",), lambda: applied.append("x"))
    assert queue.pending_count == 1
    assert queue.cancel(("x",)) is True
    assert queue.cancel(("x",)) is False
    queue.tick(502.0)
    assert "x" not in applied
    print("  flush applies in phase order; cancel drops one key")


def test_bridge_has_no_thread_until_start() -> None:
    print("\n=== Test 7: JIBridge.__init__ creates no quantizer thread ===")
    original = bridge_module.SimpleUDPClient
    FakeClient.instances = []
    bridge_module.SimpleUDPClient = FakeClient
    before = threading.active_count()
    try:
        bridge = bridge_module.JIBridge(seed=3)
        after = threading.active_count()
        assert after == before, (before, after)
        assert bridge.quantizer.started is False
        assert bridge.quantizer.pending_count == 0
        assert bridge.quantizer.seconds_to_next_beat() == -1.0

        # start()/stop() is explicit and clean; no server socket is opened here.
        bridge.quantizer.start()
        assert bridge.quantizer.started is True
        bridge.quantizer.stop()
        assert bridge.quantizer.started is False
        bridge.stop()  # server/MIDI were never started: must be safe
    finally:
        bridge_module.SimpleUDPClient = original
    print("  no thread in __init__; start/stop are explicit and clean")


def test_on_change_and_snapshot() -> None:
    print("\n=== Test 8: on_change, snapshot, pending countdown ===")
    clock = FakeClock(600.0)
    rhythm = _running_engine(clock)
    changes: list[int] = []
    queue = BeatQuantizer(rhythm, now=clock, on_change=lambda: changes.append(1))
    queue.schedule(("a",), lambda: None, phase=PHASE_GROOVE)
    assert changes
    assert queue.snapshot() == (1, 0.5)
    clock.now = 600.25
    assert queue.snapshot() == (1, 0.25)
    clock.now = 600.5
    queue.tick(600.5)
    assert queue.snapshot() == (0, 0.5)
    assert len(changes) >= 2
    print("  one on_change per queue/apply; snapshot countdown is exact")


def test_real_thread_wakes() -> None:
    print("\n=== Test 9: worker thread wakes at the beat (real time) ===")
    rhythm = RhythmEngine(bpm=300.0)  # beat = 0.2 s
    rhythm.set_running(True)
    queue = BeatQuantizer(rhythm)
    fired = threading.Event()
    stamps: list[float] = []

    def action() -> None:
        stamps.append(time.monotonic())
        fired.set()

    queue.start()
    try:
        started = time.monotonic()
        queue.schedule(("mutation",), action, phase=PHASE_MUTATION)
        assert fired.wait(1.5), "worker did not fire"
        elapsed = stamps[0] - started
        assert 0.02 <= elapsed <= 1.5, elapsed
        assert queue.pending_count == 0
    finally:
        queue.stop()
    assert not queue.started
    print(f"  fired after {elapsed:.3f} s without busy-waiting")


def run_pure() -> None:
    print("JI Stage 8.2 pure quantize tests")
    test_immediate_when_stopped()
    test_next_beat_boundary()
    test_coalescing()
    test_deterministic_order()
    test_tempo_change_reschedules()
    test_flush_and_stop()
    test_bridge_has_no_thread_until_start()
    test_on_change_and_snapshot()
    test_real_thread_wakes()
    print("\nALL QUANTIZE TESTS PASSED")


def main() -> int:
    try:
        run_pure()
    except Exception:
        traceback.print_exc()
        print("\nQUANTIZE TESTS FAILED", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

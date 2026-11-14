#!/usr/bin/env python3
"""Stage 8.7 pure tests: breath settings, curves, clock and scheduler seams.

Run from ``ji-live/``::

    uv run --with python-osc python tests/test_breath.py

No audio, mido, PyQt6, or sockets.  The scheduler checks use an injected fake
MIDI output and an injected fake breath object; only the positive-delay check
uses a short real-time window (the offset is far above thread jitter).
"""

from __future__ import annotations

import math
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import control.breath as breath_module
from control.arp import SetArp
from control.bass import BassConfig, BassEngine, BassOutput, BassScheduler
from control.breath import (
    BREATH_MAX_SHIFT_STEPS,
    BREATH_SHAPES,
    BREATH_VELOCITY_SWING,
    BreathClock,
    BreathSettings,
    breath_offset_steps,
    breath_slope,
    breath_volume,
)
from control.drone import PulseScheduler
from control.drums import DrumConfig, DrumEngine, DrumOutput, DrumScheduler


class FakeNow:
    """A settable monotonic clock (tests never sleep)."""

    def __init__(self, start: float = 0.0) -> None:
        self.value = float(start)

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += float(seconds)


class FakeBreath:
    """A duck-typed BreathClock with a fixed offset/delta."""

    def __init__(self, offset: float = 0.0, delta: int = 0) -> None:
        self.offset = float(offset)
        self.delta = int(delta)
        self.calls: list[tuple[str, str]] = []

    def offset_seconds(self, voice: str, at=None) -> float:  # noqa: ANN001
        self.calls.append(("offset", voice))
        return self.offset

    def velocity_delta(self, voice: str, at=None) -> int:  # noqa: ANN001
        self.calls.append(("delta", voice))
        return self.delta


class FakeDrumPort:
    def __init__(self) -> None:
        self.note_on_times: list[float] = []
        self.messages: list = []

    def send(self, message) -> None:  # noqa: ANN001 - duck-typed message
        self.messages.append(message)
        if getattr(message, "type", "") == "note_on":
            self.note_on_times.append(time.monotonic())

    def close(self) -> None:
        pass


class FakeBassPort:
    def __init__(self) -> None:
        self.messages: list = []

    def send(self, message) -> None:  # noqa: ANN001
        self.messages.append(message)

    def close(self) -> None:
        pass


class FakeMpeOutput:
    is_open = True

    def all_notes_off(self) -> None:
        pass


# ---------------------------------------------------------------------------


def test_settings() -> None:
    print("\n=== Test 1: settings validation/clamps ===")
    defaults = BreathSettings()
    assert defaults.enabled is False
    assert defaults.depth == 0.35 and defaults.period_bars == 4
    assert defaults.shape == "inhale"
    assert defaults.intensity == 0.5 and defaults.phase == 0.0
    assert defaults.arp_local == 0.0

    created = BreathSettings.create(
        enabled=1,
        depth=2.0,
        period_bars=99,
        shape="SINE",
        intensity=-1.0,
        phase=9.0,
        arp_local=-5.0,
    )
    assert created.enabled is True
    assert created.depth == 1.0 and created.period_bars == 32
    assert created.shape == "sine"
    assert created.intensity == 0.0
    assert created.phase == 1.0 and created.arp_local == 0.0

    changed = defaults.with_change("depth", 0.6)
    assert changed.depth == 0.6
    assert changed.with_change("period", 8).period_bars == 8
    assert changed.with_change("shape", "triangle").shape == "triangle"
    assert changed.with_change("enabled", 0).enabled is False

    for bad in (
        lambda: BreathSettings.create(shape="square"),
        lambda: defaults.with_change("shape", "square"),
        lambda: defaults.with_change("nope", 1),
        lambda: BreathSettings.create(enabled="yes"),
        lambda: BreathSettings.create(depth=object()),
    ):
        try:
            bad()
        except (TypeError, ValueError):
            pass
        else:
            raise AssertionError("invalid breath setting accepted")

    # period is rounded, then clamped to 1..32
    assert BreathSettings.create(period_bars=0.4).period_bars == 1
    assert BreathSettings.create(period_bars=6.6).period_bars == 7

    args = BreathSettings.create(
        enabled=True,
        depth=0.25,
        period_bars=6,
        shape="exhale",
        intensity=0.75,
        phase=0.5,
        arp_local=0.4,
    ).state_args()
    assert args == [1, 0.25, 6, "exhale", 0.75, 0.5, 0.4, 0]
    synced = BreathSettings.create(arp_sync=1)
    assert synced.arp_sync is True
    assert synced.with_change("arp_sync", 0).arp_sync is False
    assert synced.state_args()[7] == 1
    print("  defaults, clamps, unknown keys rejected, 8-value state order")


def test_curves() -> None:
    print("\n=== Test 2: volume/slope/offset maths ===")
    for shape in BREATH_SHAPES:
        for index in range(101):
            value = breath_volume(shape, index / 100.0)
            assert -1e-12 <= value <= 1.0 + 1e-12, (shape, index, value)
        assert abs(breath_volume(shape, 0.0)) < 1e-12
        assert abs(breath_volume(shape, 1.0)) < 1e-12
        assert abs(breath_volume(shape, 2.0)) < 1e-12  # periodic

        # The mean slope over one cycle is zero: no cumulative drift, ever.
        total = sum(
            breath_slope(shape, index / 2048.0) for index in range(2048)
        )
        assert abs(total / 2048.0) < 1e-3, (shape, total / 2048.0)

        for depth in (0.25, 0.5, 1.0):
            # The 512-sample normalisation can under-estimate the true peak by
            # a few 1e-5; the documented bound is half a step.
            limit = depth * BREATH_MAX_SHIFT_STEPS + 1e-3
            for index in range(1000):
                offset = breath_offset_steps(shape, index / 1000.0, depth)
                assert abs(offset) <= limit, (shape, depth, index, offset)
        assert breath_offset_steps(shape, 0.3, 0.0) == 0.0
        assert breath_offset_steps(shape, 0.3, -2.0) == 0.0

    # The peak shift is exactly half a 16th at depth 1.
    peak = max(
        abs(breath_offset_steps("sine", index / 4096.0, 1.0))
        for index in range(4096)
    )
    assert math.isclose(peak, BREATH_MAX_SHIFT_STEPS, rel_tol=1e-3)

    for bad in (
        lambda: breath_volume("square", 0.1),
        lambda: breath_volume("sine", float("nan")),
    ):
        try:
            bad()
        except (TypeError, ValueError):
            pass
        else:
            raise AssertionError("invalid curve input accepted")
    print("  V in 0..1, V(0)=V(1)=0, mean slope ~ 0, offset <= depth*1/2")


def test_clock() -> None:
    print("\n=== Test 3: BreathClock phase, tempo, reset, voices ===")
    now = FakeNow(100.0)
    settings = BreathSettings.create(
        enabled=True,
        depth=0.4,
        period_bars=1,
        shape="sine",
        intensity=1.0,
        arp_local=0.6,
    )
    clock = BreathClock(
        settings, bpm=120, meter_num=4, meter_den=4, now=now
    )
    assert math.isclose(clock.period_seconds, 2.0)  # one 4/4 bar at 120
    assert clock.phase_at() == 0.0
    now.advance(0.5)
    assert math.isclose(clock.phase_at(), 0.25, abs_tol=1e-12)
    now.advance(2.0)
    assert math.isclose(clock.phase_at(), 0.25, abs_tol=1e-12)  # wrapped

    # A tempo change keeps the phase continuous (new period 4 s).
    clock.set_tempo(60, 4, 4)
    assert math.isclose(clock.period_seconds, 4.0)
    assert math.isclose(clock.phase_at(), 0.25, abs_tol=1e-12)
    now.advance(1.0)
    assert math.isclose(clock.phase_at(), 0.5, abs_tol=1e-12)

    # Reset starts exactly at settings.phase.
    clock.reset()
    assert clock.phase_at() == settings.phase
    now.advance(1.0)
    assert math.isclose(clock.phase_at(), 0.25, abs_tol=1e-12)
    clock.reset(at=now.value + 3.0)
    assert clock.phase_at(now.value + 3.0) == settings.phase

    # A stopped transport does not freeze the clock: time keeps moving it.
    before = clock.phase_at()
    now.advance(0.25)
    assert clock.phase_at() != before

    # A phase change rotates on purpose; other changes preserve the phase.
    now.advance(0.5)
    held = clock.phase_at()
    clock.set_settings(clock.settings.with_change("depth", 0.9))
    assert math.isclose(clock.phase_at(), held, abs_tol=1e-12)
    clock.set_settings(clock.settings.with_change("phase", 0.5))
    assert math.isclose(clock.phase_at(), 0.5, abs_tol=1e-12)

    # The arp is half a cycle out of phase and its depth includes arp_local.
    clock.set_settings(
        BreathSettings.create(
            enabled=True,
            depth=0.4,
            period_bars=1,
            shape="sine",
            intensity=1.0,
            arp_local=0.6,
            phase=0.0,
        )
    )
    clock.reset()
    now.advance(0.25)  # phase 0.125; arp phase 0.625
    drums = clock.offset_seconds("drums")
    arp = clock.offset_seconds("arp")
    assert drums * arp < 0.0, (drums, arp)  # they breathe against each other
    assert abs(arp) > abs(drums)  # arp depth 1.0 vs drums depth 0.4

    # Stage 8.7.1: arp_sync uses the master phase.  With no extra local depth
    # the arp is exactly the groove's breath; arp_local still adds depth.
    synced = BreathClock(
        BreathSettings.create(
            enabled=True,
            depth=0.4,
            period_bars=1,
            shape="sine",
            intensity=1.0,
            arp_local=0.0,
            arp_sync=True,
            phase=0.0,
        ),
        bpm=120,
        now=FakeNow(0.0),
    )
    for at in (0.0, 0.25, 0.5, 0.75, 1.0, 1.3):
        assert math.isclose(
            synced.offset_seconds("arp", at=at),
            synced.offset_seconds("drums", at=at),
            abs_tol=1e-12,
        ), at
        assert math.isclose(
            synced.level("arp", at=at),
            synced.level("drums", at=at),
            abs_tol=1e-12,
        ), at
    synced_deeper = BreathClock(
        BreathSettings.create(
            enabled=True,
            depth=0.4,
            period_bars=1,
            shape="sine",
            intensity=1.0,
            arp_local=0.6,
            arp_sync=True,
            phase=0.0,
        ),
        bpm=120,
        now=FakeNow(0.0),
    )
    # Same sign/phase, deeper: the arp's offset is 2.5x the drums' at 0.125.
    at = 0.25
    a = synced_deeper.offset_seconds("arp", at=at)
    d = synced_deeper.offset_seconds("drums", at=at)
    assert a * d > 0.0 and abs(a) > abs(d), (a, d)
    assert math.isclose(abs(a / d), 1.0 / 0.4, rel_tol=1e-6)

    # Disabled / depth 0 is exactly 0 s and a flat level.
    off = BreathClock(
        BreathSettings.create(enabled=False, depth=1.0),
        bpm=120,
        now=FakeNow(0.0),
    )
    assert off.offset_seconds("drums", at=0.3) == 0.0
    assert off.level("drums", at=0.3) == 0.5
    assert off.velocity_delta("drums", at=0.3) == 0
    flat = BreathClock(
        BreathSettings.create(enabled=True, depth=0.0, intensity=0.0),
        bpm=120,
        now=FakeNow(0.0),
    )
    assert flat.offset_seconds("drums", at=0.3) == 0.0
    assert flat.velocity_delta("drums", at=0.3) == 0

    for bad_voice in ("drums2", "", "lead2"):
        try:
            clock.offset_seconds(bad_voice)
        except ValueError:
            pass
        else:
            raise AssertionError(f"unknown voice {bad_voice!r} accepted")
    print("  wrap, tempo continuity, reset, rotating phase, arp half-cycle")


def test_velocity_delta() -> None:
    print("\n=== Test 4: velocity delta ===")
    now = FakeNow(0.0)
    clock = BreathClock(
        BreathSettings.create(
            enabled=True, shape="sine", period_bars=1, intensity=1.0
        ),
        bpm=120,
        now=now,
    )
    # V(0) = 0 -> -14; V(0.25) = 0.5 -> 0; V(0.5) = 1 -> +14.
    assert clock.velocity_delta("drums", at=0.0) == -BREATH_VELOCITY_SWING
    assert clock.velocity_delta("drums", at=0.5) == 0  # 1/4 cycle
    assert clock.velocity_delta("drums", at=1.0) == BREATH_VELOCITY_SWING
    for index in range(200):
        delta = clock.velocity_delta("drums", at=index * 0.01)
        assert -BREATH_VELOCITY_SWING <= delta <= BREATH_VELOCITY_SWING
    quiet = BreathClock(
        BreathSettings.create(enabled=True, intensity=0.0),
        bpm=120,
        now=FakeNow(0.0),
    )
    assert quiet.velocity_delta("drums", at=0.5) == 0
    print("  zero at level 0.5, bounded by +/-14, zero at intensity 0")


def test_drift_proof() -> None:
    print("\n=== Test 5: 1000-step drift proof ===")
    step = 60.0 / 120.0 / 4.0
    now = FakeNow(0.0)
    clock = BreathClock(
        BreathSettings.create(
            enabled=True, depth=1.0, period_bars=1, shape="inhale",
            intensity=0.0,
        ),
        bpm=120,
        meter_num=4,
        meter_den=4,
        now=now,
    )
    deadline = 0.0
    offsets = []
    for _ in range(1000):
        offset = clock.offset_seconds("drums", at=deadline)
        offsets.append(offset)
        # The curve is bounded by half a step; the scheduler clamps it to 45 %
        # and adds that clamped value to the sleep target only.
        assert abs(offset) <= 0.5 * step + 1e-3 * step
        target = deadline + max(-0.45 * step, min(0.45 * step, offset))
        assert abs(target - deadline) <= 0.45 * step + 1e-12
        deadline += step
    assert math.isclose(deadline, 1000.0 * step, rel_tol=0.0, abs_tol=1e-12)

    # Over exactly 64 one-bar cycles the sampled offsets have no systematic
    # bias beyond a small fraction of one step (the continuous mean slope is
    # zero; the per-step sample of a warped shape is only approximately so).
    clock.reset()
    total = sum(
        clock.offset_seconds("drums", at=index * step)
        for index in range(1024)
    )
    assert abs(total / 1024.0) < 0.1 * step, total
    print("  nominal deadline exact; offsets clamp; sampled mean bias tiny")


def test_scheduler_seams() -> None:
    print("\n=== Test 6: scheduler breath seams (drums/bass/arp/drone) ===")

    # -- drums: offset clamp, velocity, None == old timing ----------------
    port = FakeDrumPort()
    engine = DrumEngine(
        DrumConfig.from_mapping(
            {"enabled": True, "lane_mask": 0xFFFF, "velocity": 100}
        )
    )
    output = DrumOutput("fake drums", output=port, velocity=100)
    scheduler = DrumScheduler(output, engine, bpm=120)
    assert scheduler._breath_offset(0.1) == 0.0
    assert scheduler._velocity_with_breath() == 100

    positive = FakeBreath(offset=1.0)
    scheduler.set_breath(positive)
    assert math.isclose(scheduler._breath_offset(0.1), 0.045)
    negative = FakeBreath(offset=-1.0)
    scheduler.set_breath(negative)
    assert math.isclose(scheduler._breath_offset(0.1), -0.045)
    scheduler.set_breath(FakeBreath(offset=0.0, delta=10))
    assert scheduler._velocity_with_breath() == 110
    scheduler.set_breath(FakeBreath(offset=0.0, delta=-1000))
    assert scheduler._velocity_with_breath() == 1
    scheduler.set_breath(FakeBreath(offset=0.0, delta=1000))
    assert scheduler._velocity_with_breath() == 127
    scheduler.set_breath(None)
    assert scheduler._breath_offset(0.1) == 0.0
    assert scheduler._velocity_with_breath() == 100

    # A real thread with a positive offset fires the first hit late (the
    # offset is larger than ordinary thread jitter).
    late_port = FakeDrumPort()
    late_output = DrumOutput("fake drums", output=late_port, velocity=100)
    late = DrumScheduler(
        late_output,
        DrumEngine(
            DrumConfig.from_mapping(
                {"enabled": True, "lane_mask": 0xFFFF, "rate": 1.0}
            )
        ),
        bpm=120,
        breath=FakeBreath(offset=0.05),
    )
    started = time.monotonic()
    late.start()
    try:
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and not late_port.note_on_times:
            time.sleep(0.002)
    finally:
        late.stop()
    assert late_port.note_on_times, "the breathy drum scheduler never fired"
    delay = late_port.note_on_times[0] - started
    assert delay >= 0.03, delay

    # -- bass: same seam -------------------------------------------------
    bass_port = FakeBassPort()
    bass_engine = BassEngine(BassConfig.from_mapping({"enabled": True}))
    bass_scheduler = BassScheduler(
        BassOutput("fake bass", output=bass_port), bass_engine, bpm=120
    )
    assert bass_scheduler._breath_offset(0.25) == 0.0
    assert bass_scheduler._breath_velocity_delta() == 0
    bass_scheduler.set_breath(FakeBreath(offset=0.4, delta=-7))
    assert math.isclose(
        bass_scheduler._breath_offset(0.25), 0.45 * 0.25
    )
    assert bass_scheduler._breath_velocity_delta() == -7
    bass_scheduler.set_breath(None)
    assert bass_scheduler._breath_offset(0.25) == 0.0

    # -- arp: offset inside _clamp_wait, velocity -----------------------
    arp = SetArp(FakeMpeOutput(), bpm=120)
    assert arp._breath_offset(0.25) == 0.0
    assert arp._velocity_with_breath(90) == 90
    arp.set_breath(FakeBreath(offset=0.2, delta=8))
    assert math.isclose(arp._breath_offset(0.25), 0.45 * 0.25)
    assert arp._velocity_with_breath(90) == 98
    arp.set_breath(FakeBreath(offset=0.0, delta=-500))
    assert arp._velocity_with_breath(90) == 1
    arp.set_breath(None)
    assert arp._velocity_with_breath(90) == 90

    # -- drone: velocity only -------------------------------------------
    pulse = PulseScheduler(object())
    assert pulse._velocity_with_breath(80) == 80
    pulse.set_breath(FakeBreath(offset=0.0, delta=6))
    assert pulse._velocity_with_breath(80) == 86
    pulse.set_breath(FakeBreath(offset=5.0, delta=-500))
    assert pulse._velocity_with_breath(80) == 1
    pulse.set_breath(None)
    assert pulse._velocity_with_breath(80) == 80
    print("  clamp/velocity per voice; None is exactly the old timing")


# ---------------------------------------------------------------------------


def run() -> None:
    print("JI Stage 8.7 breath pure tests")
    test_settings()
    test_curves()
    test_clock()
    test_velocity_delta()
    test_drift_proof()
    test_scheduler_seams()
    print("\nALL BREATH TESTS PASSED")


if __name__ == "__main__":
    try:
        run()
    except Exception:
        traceback.print_exc()
        print("\nBREATH TESTS FAILED", file=sys.stderr)
        raise SystemExit(1)

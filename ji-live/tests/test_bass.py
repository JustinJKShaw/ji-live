#!/usr/bin/env python3
"""Stage 8.3a pure tests: config, presets, patterns, pitch, sub, scheduler.

Run from ``ji-live/``::

    uv run --with python-osc python tests/test_bass.py

No audio, mido, PyQt6, or sockets.  The scheduler checks use an injected fake
MIDI output and short real-time windows, like the drum scheduler tests.
"""

from __future__ import annotations

import contextlib
import io
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import control.bass as bass
import control.ji_bridge as bridge_module
from control.bass import (
    BassConfig,
    BassEngine,
    BassNote,
    BassOutput,
    BassPitchPicker,
    BassRig,
    BassScheduler,
    BassSettings,
    build_pattern,
    frequency_to_pitch,
    resolve_length,
    swing_offset,
)
from control.midi_rig import MIDIRig

INITIAL_RATIOS = [1.0, 9.0 / 8.0, 5.0 / 4.0, 4.0 / 3.0, 3.0 / 2.0, 5.0 / 3.0, 15.0 / 8.0]
FUNDAMENTAL = 220.0


class FakePort:
    """Duck-typed MIDI output capturing :class:`MPEMessage` values."""

    def __init__(self) -> None:
        self.messages: list = []
        self.closed = False

    def send(self, message) -> None:  # noqa: ANN001 - duck-typed message
        self.messages.append(message)

    def close(self) -> None:
        self.closed = True

    def types(self) -> list[str]:
        return [message.type for message in self.messages]

    def on_channel(self, channel: int) -> list:
        return [m for m in self.messages if m.channel == channel]


def _engine(preset: str = "pulse", **overrides):
    data = {"preset": preset}
    data.update(overrides)
    config = BassConfig.from_mapping(data)
    engine = BassEngine(config)
    engine.on_set(INITIAL_RATIOS, 1.0, FUNDAMENTAL)
    return engine, config


def _wait_for(predicate, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return False


# ---------------------------------------------------------------------------
# Config / presets
# ---------------------------------------------------------------------------


def test_config() -> None:
    print("\n=== Test 1: config validation and shipped defaults ===")
    config = bass.load_config()
    assert config.port == "IAC Driver Bus 4"
    assert config.channel == 2 and config.sub_channel == 3
    assert config.bend_range_semitones == 2.0
    assert config.velocity == 90 and config.sub_velocity == 80
    settings = config.settings
    assert settings.preset == "pulse"
    assert settings.grid == "1/4"
    assert settings.gate_beats == 3.5
    assert settings.base_octave == -2
    assert settings.seed == 1
    assert settings.enabled is False

    shortcut = BassConfig.from_mapping({"preset": "techno"})
    assert shortcut.settings.grid == "1/16"
    assert shortcut.settings.density == 0.55
    assert shortcut.settings.pitch_mode == "walk"
    assert shortcut.settings.octave_range == 1

    for bad in (
        {"channel": 0},
        {"channel": 17},
        {"channel": 2, "sub_channel": 2},
        {"bend_range_semitones": 0},
        {"velocity": 0},
        {"velocity": 128},
        {"grid": "1/3"},
        {"preset": "nope"},
        {"octave_range": 4},
        {"base_octave": 1},
        {"sub_octaves": 3},
    ):
        try:
            BassConfig.from_mapping(bad)
        except (TypeError, ValueError):
            pass
        else:
            raise AssertionError(f"invalid bass config accepted: {bad}")

    # Safe clamps.
    clamped = BassConfig.from_mapping(
        {"density": 3, "syncopation": -2, "swing": 9, "seed": -5, "gate_beats": 99}
    )
    assert clamped.settings.density == 1.0
    assert clamped.settings.syncopation == 0.0
    assert clamped.settings.swing == 1.0
    assert clamped.settings.seed == 0
    assert clamped.settings.gate_beats == 8.0
    print("  shipped Bus 4 config, channels, bend, clamps, invalid values rejected")


def test_presets_and_custom() -> None:
    print("\n=== Test 2: presets and manual-edit -> custom ===")
    settings = BassSettings.create(seed=9, sub_enabled=True, sub_octaves=2)
    techno = settings.with_preset("techno")
    assert techno.preset == "techno"
    assert techno.seed == 9 and techno.sub_enabled and techno.sub_octaves == 2
    assert techno.grid == "1/16" and techno.pitch_mode == "walk"
    assert techno.length_steps is None

    chaos = settings.with_preset("chaos")
    assert chaos.density == 0.80 and chaos.octave_range == 2

    manual = techno.with_change("density", 0.2)
    assert manual.preset == bass.PRESET_CUSTOM
    assert manual.density == 0.2
    assert manual.grid == "1/16"

    # Enable/sub changes do not claim the pattern is custom.
    assert techno.with_change("enabled", True).preset == "techno"
    assert techno.with_change("sub_octaves", 2).preset == "techno"

    pulse = BassSettings.create().with_preset("pulse")
    assert pulse.grid == "1/4" and pulse.density == 0.0
    assert pulse.gate_beats == 3.5 and pulse.pitch_mode == "root"
    print("  preset values applied; manual edits become custom; seed/sub survive")


# ---------------------------------------------------------------------------
# Grid / pattern
# ---------------------------------------------------------------------------


def test_grid_and_length() -> None:
    print("\n=== Test 3: grid, step length, and meter changes ===")
    assert bass.grid_steps("1/4") == 1
    assert bass.grid_steps("1/8") == 2
    assert bass.grid_steps("1/16") == 4
    assert resolve_length(BassSettings.create(), 4, 4) == 4
    assert resolve_length(BassSettings.create(grid="1/8"), 4, 4) == 8
    assert resolve_length(BassSettings.create(grid="1/16"), 4, 4) == 16
    assert resolve_length(BassSettings.create(grid="1/16"), 7, 8) == 14
    assert resolve_length(BassSettings.create(grid="1/16"), 6, 8) == 12
    assert resolve_length(BassSettings.create(grid="1/4"), 5, 4) == 5
    custom = BassSettings.create(grid="1/8", length_steps=7)
    assert resolve_length(custom, 4, 4) == 7
    assert resolve_length(custom, 7, 8) == 7
    assert BassSettings.create(grid="1/16").step_beats == 0.25

    engine, _ = _engine()
    engine.set_meter(7, 8)
    assert engine.length_steps == 4  # pulse grid 1/4, 3.5-bar -> 4 steps
    engine.set_setting("grid", "1/16")
    assert engine.length_steps == 14
    print("  1/4, 1/8, 1/16 lengths; bar length follows the meter; custom survives")


def test_pattern() -> None:
    print("\n=== Test 4: density, syncopation, and seeded determinism ===")
    pulse = build_pattern(BassSettings.create(), 4, 4, 0)
    assert pulse.triggers[0] is True
    assert not any(pulse.triggers[1:])

    dense = build_pattern(
        BassSettings.create(grid="1/16", density=1.0), 4, 4, 0
    )
    assert all(dense.triggers)
    assert len(dense.triggers) == 16

    straight = build_pattern(
        BassSettings.create(grid="1/16", density=0.5), 4, 4, 0
    )
    sync = build_pattern(
        BassSettings.create(grid="1/16", density=0.5, syncopation=1.0),
        4,
        4,
        0,
    )
    assert sync.triggers[0] is True
    assert sum(sync.triggers) >= sum(straight.triggers)
    for step in range(16):
        if straight.triggers[step]:
            assert sync.triggers[step], (step, straight, sync)
    for seed in range(12):
        for bar in range(4):
            built = build_pattern(
                BassSettings.create(grid="1/16", density=0.6, syncopation=0.6, seed=seed),
                4,
                4,
                bar,
            )
            assert built.triggers[0] is True
            assert len(built.triggers) == 16

    same = build_pattern(
        BassSettings.create(grid="1/16", density=0.6, syncopation=0.4, seed=3),
        4,
        4,
        2,
    )
    again = build_pattern(
        BassSettings.create(grid="1/16", density=0.6, syncopation=0.4, seed=3),
        4,
        4,
        2,
    )
    assert same.triggers == again.triggers
    different = build_pattern(
        BassSettings.create(grid="1/16", density=0.6, syncopation=0.4, seed=9),
        4,
        4,
        2,
    )
    assert different.triggers != same.triggers
    print("  downbeat, density 0/1, sync never removes hits, deterministic seeds")


# ---------------------------------------------------------------------------
# Pitch
# ---------------------------------------------------------------------------


def test_pitch_modes() -> None:
    print("\n=== Test 5: root/anchor/walk/random/cycle and octaves ===")
    root_engine, _ = _engine(pitch_mode="root")
    note = root_engine.note_at(0, 0)
    assert note is not None
    assert note.ratio == 1.0 and note.octave_offset == 0
    assert abs(note.frequency - 55.0) < 1e-9

    anchor_engine, _ = _engine(
        pitch_mode="anchor", root_downbeat=False, density=1.0
    )
    anchor_engine.on_set(INITIAL_RATIOS, 1.25, FUNDAMENTAL)
    note = anchor_engine.note_at(0, 0)
    assert note is not None and abs(note.ratio - 1.25) < 1e-9

    missing_engine, _ = _engine(
        pitch_mode="anchor", root_downbeat=False, density=1.0
    )
    missing_engine.on_set(INITIAL_RATIOS, 1.3, FUNDAMENTAL)
    note = missing_engine.note_at(0, 0)
    assert note is not None and note.ratio == 1.0

    hold_engine, _ = _engine(
        pitch_mode="walk", movement=0.0, root_downbeat=False, density=1.0,
        grid="1/16",
    )
    held = [hold_engine.note_at(step, 0) for step in range(8)]
    assert all(item is not None and item.ratio == 1.0 for item in held)

    walk_engine, _ = _engine(
        pitch_mode="walk", movement=1.0, root_downbeat=False, density=1.0,
        grid="1/16",
    )
    walked = [walk_engine.note_at(step, 0) for step in range(24)]
    assert all(item is not None for item in walked)
    assert all(item.ratio in INITIAL_RATIOS for item in walked)
    assert len({round(item.ratio, 6) for item in walked}) > 1

    for mode in ("walk", "random", "cycle"):
        first, _ = _engine(
            pitch_mode=mode, movement=0.7, root_downbeat=False, density=1.0,
            grid="1/16", seed=4, octave_range=2, octave_jump=0.6,
        )
        second, _ = _engine(
            pitch_mode=mode, movement=0.7, root_downbeat=False, density=1.0,
            grid="1/16", seed=4, octave_range=2, octave_jump=0.6,
        )
        first_notes = [first.note_at(step, 0) for step in range(16)]
        second_notes = [second.note_at(step, 0) for step in range(16)]
        assert [n.ratio for n in first_notes] == [n.ratio for n in second_notes]
        assert [n.octave_offset for n in first_notes] == [
            n.octave_offset for n in second_notes
        ]
        assert all(
            -2 <= n.octave_offset <= 2 for n in first_notes if n is not None
        )

    cycle_engine, _ = _engine(
        pitch_mode="cycle", root_downbeat=False, density=1.0, grid="1/16",
        movement=0.0,
    )
    sequence = [cycle_engine.note_at(step, 0).ratio for step in range(7)]
    assert sequence == [
        1.0,
        1.125,
        1.25,
        4.0 / 3.0,
        1.5,
        5.0 / 3.0,
        1.875,
    ], sequence

    # A set update changes the pool for the next trigger; no held-note action.
    update_engine, _ = _engine(
        pitch_mode="cycle", root_downbeat=False, density=1.0, grid="1/16"
    )
    first = update_engine.note_at(1, 0)
    update_engine.on_set([1.0, 1.5], 1.0, FUNDAMENTAL)
    after = update_engine.note_at(2, 0)
    assert first is not None and after is not None
    assert after.ratio in (1.0, 1.5)
    assert after.frequency in (FUNDAMENTAL * 0.25, FUNDAMENTAL * 0.25 * 1.5)
    print("  root/anchor/walk/random/cycle deterministic; octaves in range")


# ---------------------------------------------------------------------------
# Sub
# ---------------------------------------------------------------------------


def test_sub() -> None:
    print("\n=== Test 6: sub octave transposition and skip below MIDI 0 ===")
    note = BassNote(
        ratio=1.0,
        octave_offset=0,
        frequency=55.0,
        step=0,
        bar_index=0,
        is_downbeat=True,
        mode="root",
    )
    assert note.sub_frequency(1) == 27.5
    assert note.sub_frequency(2) == 13.75

    fake = FakePort()
    config = BassConfig.from_mapping(
        {"preset": "pulse", "enabled": True, "sub_enabled": True,
         "sub_octaves": 1, "gate_beats": 2.0}
    )
    rig = BassRig(config, output=fake)
    rig.engine.on_set(INITIAL_RATIOS, 1.0, FUNDAMENTAL)
    rig.start()
    assert rig.port_ok
    rig.set_transport(True)
    assert _wait_for(lambda: len(fake.types()) >= 4)
    rig.set_transport(False)
    rig.stop()
    channels = [m.channel for m in fake.messages if m.type == "note_on"]
    assert 1 in channels and 2 in channels
    sub = frequency_to_pitch(27.5, 2.0)
    assert 2 in [m.channel for m in fake.messages if m.type == "note_on"]

    # Below MIDI note 0: the sub is skipped, not transposed up.
    silent_fake = FakePort()
    deep = BassConfig.from_mapping(
        {"preset": "pulse", "enabled": True, "sub_enabled": True,
         "sub_octaves": 2, "base_octave": -3, "gate_beats": 0.1}
    )
    deep_rig = BassRig(deep, output=silent_fake)
    deep_rig.engine.on_set([1.0], 1.0, FUNDAMENTAL)
    deep_rig.start()
    deep_rig.set_transport(True)
    assert _wait_for(lambda: any(m.type == "note_on" for m in silent_fake.messages))
    deep_rig.set_transport(False)
    deep_rig.stop()
    assert all(
        m.channel != 2
        for m in silent_fake.messages
        if m.type == "note_on"
    ), [m for m in silent_fake.messages if m.type == "note_on"]
    print(f"  sub 1/2 octaves on ch 3; below-note-0 skip; main pitch {sub.note}")


# ---------------------------------------------------------------------------
# Output / scheduler
# ---------------------------------------------------------------------------


def test_output_and_scheduler() -> None:
    print("\n=== Test 7: fake MIDI output, bend-before-note, gates, CC 123 ===")
    fake = FakePort()
    config = BassConfig.from_mapping(
        {"preset": "techno", "enabled": True, "gate_beats": 0.12}
    )
    rig = BassRig(config, output=fake)
    rig.engine.on_set(INITIAL_RATIOS, 1.0, FUNDAMENTAL)
    assert rig.start()
    rig.set_transport(True)
    assert _wait_for(
        lambda: sum(1 for m in fake.messages if m.type == "note_on") >= 6
    ), fake.types()
    rig.set_transport(False)
    rig.stop()

    # Channel-relative last-message checks: never a stale note-off, never a
    # second note-on without a note-off, and pitch bend always precedes on.
    for channel in (1, 2):
        held = False
        last_type = None
        for message in fake.on_channel(channel):
            if message.type == "pitchwheel":
                last_type = "pitchwheel"
            elif message.type == "note_on":
                assert last_type == "pitchwheel", (channel, message)
                assert not held, f"double note-on on channel {channel}"
                held = True
                last_type = "note_on"
            elif message.type == "note_off":
                assert held, f"stale note-off on channel {channel}"
                held = False
                last_type = "note_off"
    controls = [m for m in fake.messages if m.type == "control_change"]
    main_controls = [m.control for m in controls if m.channel == 1]
    sub_controls = [m.control for m in controls if m.channel == 2]
    assert main_controls and set(main_controls) == {123}
    assert sub_controls and set(sub_controls) == {123}

    # Swing math: 1/4 is straight; odd 1/8 and 1/16 steps get half a step.
    assert swing_offset(1, 1.0, 0.125, 4) == 0.0625
    assert swing_offset(0, 1.0, 0.125, 4) == 0.0
    assert swing_offset(1, 0.0, 0.125, 4) == 0.0
    assert swing_offset(1, 1.0, 0.125, 1) == 0.0
    assert swing_offset(1, 0.5, 0.2, 2) == 0.05

    # Stop must be prompt and leave nothing sounding.
    started = time.monotonic()
    rig2 = BassRig(
        BassConfig.from_mapping(
            {"preset": "chaos", "enabled": True, "gate_beats": 8.0}
        ),
        output=FakePort(),
    )
    rig2.engine.on_set(INITIAL_RATIOS, 1.0, FUNDAMENTAL)
    rig2.start()
    rig2.set_transport(True)
    time.sleep(0.15)
    rig2.set_transport(False)
    rig2.stop()
    assert time.monotonic() - started < 2.0
    print("  bend-before-on, monophonic offs, CC 123 on both channels, prompt stop")


def test_missing_port_and_bass_only_rig() -> None:
    print("\n=== Test 8: missing Bus 4 is isolated; bass-only MIDIRig ===")
    missing = BassRig(BassConfig.from_mapping({"port": "No Such Bass Bus 999"}))
    assert missing.start() is False
    assert missing.port_ok is False
    assert missing.engine.enabled is False
    missing.stop()

    fake = FakePort()
    rig = MIDIRig(
        midi=False,
        drums=False,
        bass=True,
        bass_config={
            "preset": "techno",
            "enabled": True,
            "output": fake,
        },
        bass_output=fake,
    )
    assert rig.midi_enabled is False and rig.drums is None
    assert rig.bass is not None and rig.bass.port_ok
    rig.on_set(INITIAL_RATIOS, 1.0, FUNDAMENTAL)
    rig.start()
    rig.set_bass_transport(True)
    assert _wait_for(
        lambda: sum(1 for m in fake.messages if m.type == "note_on") >= 2
    ), fake.types()
    rig.set_bass_transport(False)
    rig.stop()
    assert "mido" not in sys.modules
    print("  missing bus logs and disables bass only; midi=False,bass=True works")


# ---------------------------------------------------------------------------
# Bridge quantization
# ---------------------------------------------------------------------------


def test_bridge_quantization() -> None:
    print("\n=== Test 9: bridge bass changes are immediate/queued ===")
    original = bridge_module.SimpleUDPClient

    class FakeClient:
        instances: list["FakeClient"] = []

        def __init__(self, host: str, port: int) -> None:
            self.host = host
            self.port = port
            self.sent: list[tuple[str, list]] = []
            FakeClient.instances.append(self)

        def send_message(self, address: str, args: list) -> None:
            self.sent.append((address, list(args)))

    bridge_module.SimpleUDPClient = FakeClient
    try:
        bridge = bridge_module.JIBridge(
            bass=True, listen_port=19040, ui_port=19041, sc_port=19042
        )
        fake = FakePort()
        rig = BassRig(BassConfig.from_mapping({"preset": "pulse"}), output=fake)
        rig.engine.on_set(INITIAL_RATIOS, 1.0, FUNDAMENTAL)
        rig.start()
        bridge.midi.bass = rig

        bridge.rhythm.set_running(False)
        bridge._on_bass_change("preset", "techno")
        assert rig.engine.settings.preset == "techno"
        assert bridge.quantizer.pending_count == 0

        bridge.rhythm.set_running(True)
        bridge._on_bass_change("density", 0.4)
        assert bridge.quantizer.pending_count == 1
        assert rig.engine.settings.density != 0.4
        bridge.quantizer.flush("test beat")
        assert bridge.quantizer.pending_count == 0
        assert rig.engine.settings.density == 0.4

        # Transport stop flushes a pending bass edit like any other action.
        bridge._on_bass_change("octave_range", 2)
        assert bridge.quantizer.pending_count == 1
        bridge._on_rhythm_transport(False)
        assert bridge.quantizer.pending_count == 0
        assert rig.engine.settings.octave_range == 2
        bridge.stop()
    finally:
        bridge_module.SimpleUDPClient = original
    print("  stopped -> immediate; running -> pending then applied; stop flushes")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def run() -> None:
    print("JI Stage 8.3a bass pure tests")
    test_config()
    test_presets_and_custom()
    test_grid_and_length()
    test_pattern()
    test_pitch_modes()
    test_sub()
    test_output_and_scheduler()
    test_missing_port_and_bass_only_rig()
    test_bridge_quantization()
    print("\nALL BASS TESTS PASSED")


if __name__ == "__main__":
    try:
        run()
    except Exception:
        traceback.print_exc()
        raise SystemExit(1)

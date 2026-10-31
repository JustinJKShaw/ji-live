#!/usr/bin/env python3
"""Stage 7 GUI harness: pure OSC tests plus an optional headless Qt smoke test.

Run from ``ji-live/``.

Pure tests (no PyQt6, no sockets, no audio/MIDI)::

    uv run --with python-osc python tests/test_ui.py --pure

Optional offscreen Qt smoke test (needs PyQt6)::

    QT_QPA_PLATFORM=offscreen uv run --with python-osc --with PyQt6 \
      python tests/test_ui.py --qt

The Qt test uses a fake controller and never opens a real OSC port.  Neither
mode proves that the GUI controls the human's live rig; that is the Stage 7
human acceptance check.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import traceback
from pathlib import Path
from types import SimpleNamespace

# Allow both ``python tests/test_ui.py`` and ``python -m tests.test_ui``.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import control.ji_bridge as bridge_module
from control.mutation import MutationEngine
from control.osc_server import (
    DRUMS_RESET_GRID_ADDRESS,
    DRUMS_SET_DENSITY_ADDRESS,
    DRUMS_SET_ENABLED_ADDRESS,
    DRUMS_SET_LANE_ADDRESS,
    DRUMS_SET_PRESET_ADDRESS,
    DRUMS_SET_PROBABILITY_ADDRESS,
    DRUMS_SET_RATE_ADDRESS,
    DRUMS_SET_SEED_ADDRESS,
    DRUMS_SET_STEP_ADDRESS,
    DRUMS_SET_SWING_ADDRESS,
    DRUMS_SET_SYNCOPATION_ADDRESS,
    MIDI_SET_ENABLED_ADDRESS,
    MutationOSCServer,
    RHYTHM_REQUEST_ADDRESS,
    RHYTHM_SET_METER_ADDRESS,
    RHYTHM_SET_TEMPO_ADDRESS,
    RHYTHM_TRANSPORT_ADDRESS,
    UI_HELLO_ADDRESS,
)
from ui import osc_control
from ui.osc_control import (
    AUTOTUNE_PARAMS,
    BridgeControlState,
    DRUM_LANE_DEFAULT_MASK,
    DrumStatus,
    FrequencySet,
    GrooveStatus,
    LoopConfig,
    MidiStatus,
    MidiToggleState,
    OSCController,
    QuantizeStatus,
    RhythmStatus,
    StatusStore,
    build_loop_clear,
    build_loop_play,
    build_loop_record,
    build_loop_set_beats,
    build_loop_set_default_length,
    build_loop_set_length,
    build_loop_set_mode,
    build_loop_stop,
    build_loop_stop_all,
    build_loop_stop_record,
    build_request_state,
    build_reset_drum_grid,
    build_rhythm_request,
    build_set_autotune,
    build_set_drum_density,
    build_set_drum_lane,
    build_set_drum_probability,
    build_set_drum_rate,
    build_set_drum_seed,
    build_set_drum_step,
    build_set_drum_swing,
    build_set_drum_syncopation,
    build_set_drums_enabled,
    build_set_drums_preset,
    build_set_groove_modifier,
    build_set_meter,
    build_set_midi_enabled,
    build_set_mutation,
    build_set_tempo,
    build_transport,
    build_trigger_mutation,
    build_trigger_octave,
    build_ui_hello,
    drum_lane_mask,
    drum_lane_states,
    grid_steps_for_meter,
    parse_autotune_state,
    parse_cpu,
    parse_drums_state,
    parse_freqset,
    parse_grid,
    parse_groove,
    parse_loop_config,
    parse_loop_state,
    parse_midi_status,
    parse_midi_state,
    parse_quantize,
    parse_rhythm_state,
    parse_ui_state,
)

INITIAL = [1.0, 9.0 / 8.0, 5.0 / 4.0, 4.0 / 3.0, 3.0 / 2.0, 5.0 / 3.0, 15.0 / 8.0]


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeClient:
    """Minimal SimpleUDPClient stand-in that records messages."""

    instances: list["FakeClient"] = []

    def __init__(self, host: str, port: int) -> None:
        self.host = host
        self.port = port
        self.sent: list[tuple[str, list]] = []
        self.closed = False
        FakeClient.instances.append(self)

    def send_message(self, address: str, args: list) -> None:
        if self.closed:
            raise RuntimeError("send after close")
        self.sent.append((address, list(args)))

    def close(self) -> None:
        self.closed = True

    def addresses(self) -> list[str]:
        return [address for address, _ in self.sent]


class FakeDrumRig:
    """Stand-in for DrumRig's state/mutations (no mido, no ports)."""

    def __init__(self) -> None:
        self.enabled = True
        self.port_ok = True
        self.rate = 1.0
        self.lane_mask = DRUM_LANE_DEFAULT_MASK
        self.config = SimpleNamespace(bpm=120.0, meter_num=4, meter_den=4)
        self.calls: list[tuple] = []

    def drums_state(self) -> tuple[bool, bool, float, int]:
        return self.enabled, self.port_ok, self.rate, self.lane_mask

    def set_enabled(self, enabled: bool) -> bool:
        self.enabled = bool(enabled)
        self.calls.append(("enabled", self.enabled))
        return True

    def set_lane(self, note: int, enabled: bool) -> bool:
        bit = note - 36
        if enabled:
            self.lane_mask |= 1 << bit
        else:
            self.lane_mask &= ~(1 << bit)
        self.calls.append(("lane", note, bool(enabled)))
        return True

    def set_rate(self, rate: float) -> float:
        self.rate = float(rate)
        self.calls.append(("rate", self.rate))
        return self.rate


class FakeMidiRig:
    """Stand-in for MIDIRig's per-bus switches (no mido, no ports)."""

    def __init__(self, drums: FakeDrumRig | None = None) -> None:
        self.config = SimpleNamespace(
            drone_port="IAC Driver Bus 1", arp_port="IAC Driver Bus 2"
        )
        self.drone_enabled = True
        self.arp_enabled = True
        self.midi_enabled = True
        self.drums = drums
        self.calls: list[tuple[str, bool]] = []
        self.drum_calls: list[tuple] = []

    def set_drone_enabled(self, enabled: bool) -> None:
        self.drone_enabled = bool(enabled)
        self.calls.append(("drone", bool(enabled)))

    def set_arp_enabled(self, enabled: bool) -> None:
        self.arp_enabled = bool(enabled)
        self.calls.append(("arp", bool(enabled)))

    def drums_state(self) -> tuple[bool, bool, float, int]:
        if self.drums is None:
            return False, False, 1.0, DRUM_LANE_DEFAULT_MASK
        return self.drums.drums_state()

    def set_drums_enabled(self, enabled: bool) -> bool:
        self.drum_calls.append(("enabled", bool(enabled)))
        if self.drums is None:
            return False
        return self.drums.set_enabled(enabled)

    def set_drums_lane(self, note: int, enabled: bool) -> bool:
        self.drum_calls.append(("lane", int(note), bool(enabled)))
        if self.drums is None:
            raise RuntimeError("no drums")
        return self.drums.set_lane(note, enabled)

    def set_drums_rate(self, rate: float) -> float:
        self.drum_calls.append(("rate", float(rate)))
        if self.drums is None:
            raise RuntimeError("no drums")
        return self.drums.set_rate(rate)

    def set_drums_transport(self, running: bool) -> None:
        self.drum_calls.append(("transport", bool(running)))

    def set_drums_bpm(self, bpm: float) -> None:
        self.drum_calls.append(("bpm", float(bpm)))

    def set_drums_meter(self, num: int, den: int) -> None:
        self.drum_calls.append(("meter", int(num), int(den)))


class FakeListener:
    def __init__(self, host: str, port: int, handler) -> None:
        self.host = host
        self.port = port
        self.handler = handler
        self.started = False
        self.stopped = False
        self.start_count = 0
        self.order: list[str] = []

    def start(self) -> None:
        self.started = True
        self.start_count += 1
        self.order.append("listen")

    def stop(self) -> None:
        self.stopped = True
        self.order.append("stop")


# ---------------------------------------------------------------------------
# Message builders
# ---------------------------------------------------------------------------


def test_mutation_builders() -> None:
    print("\n=== Test 1: mutation message builders ===")
    expected = {
        "randomness": "/ji/mutation/set_randomness",
        "dissonance_bias": "/ji/mutation/set_dissonance_bias",
        "stepwise_bias": "/ji/mutation/set_stepwise_bias",
        "temperature": "/ji/mutation/set_temperature",
        "anchor_mobility": "/ji/mutation/set_anchor_mobility",
        "change_count": "/ji/mutation/set_change_count",
        "glide_time": "/ji/mutation/set_glide_time",
    }
    for key, address in expected.items():
        built_address, args = build_set_mutation(key, 0.5)
        assert built_address == address, (key, built_address)
        assert len(args) == 1
        assert isinstance(args[0], (int, float))

    assert build_set_mutation("randomness", 1.5) == (
        "/ji/mutation/set_randomness",
        [1.0],
    )
    assert build_set_mutation("randomness", -0.5)[1] == [0.0]
    assert build_set_mutation("dissonance_bias", -2.0)[1] == [-1.0]
    assert build_set_mutation("dissonance_bias", 2.0)[1] == [1.0]
    assert build_set_mutation("stepwise_bias", 2.0)[1] == [1.0]
    assert build_set_mutation("temperature", 2.0)[1] == [1.0]
    assert build_set_mutation("anchor_mobility", 2.0)[1] == [1.0]
    assert build_set_mutation("glide_time", 5.0)[1] == [2.0]

    count_address, count_args = build_set_mutation(
        "change_count", 99, max_changes=5
    )
    assert count_address == "/ji/mutation/set_change_count"
    assert count_args == [5]
    assert isinstance(count_args[0], int)
    assert build_set_mutation("change_count", 0, max_changes=5)[1] == [1]
    assert build_set_mutation("change_count", 3.6, max_changes=5)[1] == [4]

    assert build_trigger_mutation() == ("/ji/mutation/trigger", [])
    assert build_trigger_octave() == ("/ji/mutation/octave", [])

    try:
        build_set_mutation("nope", 0.5)
    except ValueError:
        pass
    else:
        raise AssertionError("unknown mutation parameter was accepted")
    print("  addresses, types, change_count bounds, and clamping are correct")


def test_autotune_builders() -> None:
    print("\n=== Test 2: autotune message builders ===")
    for channel in (0, 1):
        for spec in AUTOTUNE_PARAMS:
            address, args = build_set_autotune(
                channel, spec.key, spec.default
            )
            assert address == "/ji/autotune/set"
            assert args[0] == channel and isinstance(args[0], int)
            assert args[1] == spec.key and isinstance(args[1], str)
            assert isinstance(args[2], float)

    assert build_set_autotune(0, "retuneTime", 0.0)[1] == [0, "retuneTime", 0.001]
    assert build_set_autotune(1, "retuneTime", 99.0)[1] == [1, "retuneTime", 2.0]
    assert build_set_autotune(0, "mix", -1.0)[1] == [0, "mix", 0.0]
    assert build_set_autotune(0, "windowSize", 0.0)[1] == [0, "windowSize", 0.02]
    assert build_set_autotune(0, "pitchRangeLimit", 12.6)[1] == [
        0,
        "pitchRangeLimit",
        13.0,
    ]
    assert build_set_autotune(1, "tartiniThreshold", 2.0)[1] == [
        1,
        "tartiniThreshold",
        1.0,
    ]
    assert build_set_autotune(1, "inputGain", 3.0)[1] == [1, "inputGain", 2.0]

    for bad in (
        lambda: build_set_autotune(2, "mix", 0.5),
        lambda: build_set_autotune(-1, "mix", 0.5),
        lambda: build_set_autotune(0, "arbitrary", 0.5),
        lambda: build_set_autotune(0, "freqBus", 1.0),
    ):
        try:
            bad()
        except ValueError:
            pass
        else:
            raise AssertionError("invalid autotune channel/param was accepted")
    print("  both channels, all six params, and whitelist checks are correct")


def test_loop_builders() -> None:
    print("\n=== Test 3: loop message builders ===")
    assert build_loop_record(1) == ("/ji/loop/record", [1])
    assert build_loop_record(1, 8.0) == (
        "/ji/loop/record_length",
        [1, 8.0],
    )
    assert build_loop_record(4, 0.0)[1] == [4, 1.0]
    assert build_loop_record(4, 99.0)[1] == [4, 30.0]
    assert unique_type(build_loop_record(3)[1][0]) is int

    for index in (1, 2, 3, 4):
        assert build_loop_stop_record(index) == (
            "/ji/loop/stop_record",
            [index],
        )
        assert build_loop_play(index) == ("/ji/loop/play", [index])
        assert build_loop_stop(index) == ("/ji/loop/stop", [index])
        assert build_loop_clear(index) == ("/ji/loop/clear", [index])

    assert build_loop_stop_all() == ("/ji/loop/stop_all", [])
    assert build_loop_set_default_length(8.0) == (
        "/ji/loop/set_default_length",
        [8.0],
    )
    assert build_loop_set_default_length(-1.0)[1] == [1.0]
    assert build_loop_set_default_length(100.0)[1] == [30.0]
    assert build_set_midi_enabled(True, False) == (
        "/ji/midi/set_enabled",
        [1, 0],
    )
    assert build_request_state() == ("/ji/status/query", [])
    assert build_ui_hello() == ("/ji/ui/hello", [])

    for bad_index in (0, 5, -2):
        for build in (
            lambda i=bad_index: build_loop_record(i),
            lambda i=bad_index: build_loop_play(i),
            lambda i=bad_index: build_loop_clear(i),
        ):
            try:
                build()
            except ValueError:
                pass
            else:
                raise AssertionError(f"invalid loop index {bad_index} accepted")
    print("  record/record_length/transport/clear/all/query builders are correct")


def unique_type(value) -> type:
    return int if isinstance(value, int) and not isinstance(value, bool) else float


def test_rhythm_drum_builders() -> None:
    print("\n=== Test 3b: rhythm / drum / loop-config message builders ===")
    assert build_set_tempo(120) == ("/ji/rhythm/set_tempo", [120.0])
    assert build_set_tempo(5)[1] == [20.0]
    assert build_set_tempo(1000)[1] == [300.0]
    assert build_set_meter(4, 4) == ("/ji/rhythm/set_meter", [4, 4])
    assert build_set_meter(99, 8) == ("/ji/rhythm/set_meter", [16, 8])
    try:
        build_set_meter(4, 3)
    except ValueError:
        pass
    else:
        raise AssertionError("unsupported meter denominator accepted")
    assert build_transport(True) == ("/ji/rhythm/transport", [1])
    assert build_transport(False) == ("/ji/rhythm/transport", [0])
    assert build_rhythm_request() == ("/ji/rhythm/request", [])

    assert build_set_drums_enabled(True) == ("/ji/drums/set_enabled", [1])
    assert build_set_drum_lane(36, False) == ("/ji/drums/set_lane", [36, 0])
    assert build_set_drum_lane(999, True) == ("/ji/drums/set_lane", [127, 1])
    assert build_set_drum_rate(0.5) == ("/ji/drums/set_rate", [0.5])
    assert build_set_drum_rate(2) == ("/ji/drums/set_rate", [2.0])
    try:
        build_set_drum_rate(1.5)
    except ValueError:
        pass
    else:
        raise AssertionError("unsupported drum rate accepted")

    assert build_set_drums_preset("Break") == ("/ji/drums/set_preset", ["break"])
    assert build_set_groove_modifier("density", 0.5) == (
        "/ji/drums/set_density",
        [0.5],
    )
    assert build_set_drum_density(2) == ("/ji/drums/set_density", [1.0])
    assert build_set_drum_syncopation(-1) == (
        "/ji/drums/set_syncopation",
        [0.0],
    )
    assert build_set_drum_swing(0.25) == ("/ji/drums/set_swing", [0.25])
    assert build_set_drum_probability(0.75) == (
        "/ji/drums/set_probability",
        [0.75],
    )
    assert build_set_drum_step(2, 5, True) == ("/ji/drums/set_step", [2, 5, 1])
    assert build_set_drum_step(15, 31, False) == (
        "/ji/drums/set_step",
        [15, 31, 0],
    )
    assert build_set_drum_seed(7) == ("/ji/drums/set_seed", [7])
    assert build_reset_drum_grid() == ("/ji/drums/reset_grid", [])
    assert grid_steps_for_meter(4, 4) == 16
    assert grid_steps_for_meter(7, 8) == 14
    assert grid_steps_for_meter(16, 2) == 32
    for bad in (
        lambda: build_set_drums_preset("nope"),
        lambda: build_set_groove_modifier("seed", 1),
        lambda: build_set_drum_step(16, 0, True),
        lambda: build_set_drum_step(0, 32, True),
        lambda: build_set_drum_seed(float("nan")),
    ):
        try:
            bad()
        except (TypeError, ValueError):
            pass
        else:
            raise AssertionError("invalid groove builder value accepted")

    assert build_loop_set_mode(1, "Beat") == ("/ji/loop/set_mode", [1, "beat"])
    assert build_loop_set_beats(2, 99) == ("/ji/loop/set_beats", [2, 64.0])
    assert build_loop_set_beats(2, 0) == ("/ji/loop/set_beats", [2, 1.0])
    assert build_loop_set_length(3, 0) == ("/ji/loop/set_length", [3, 1.0])
    assert build_loop_set_length(3, 99) == ("/ji/loop/set_length", [3, 30.0])
    for bad in (
        lambda: build_set_tempo(float("nan")),
        lambda: build_set_meter(4, 0),
        lambda: build_set_drum_rate(3),
        lambda: build_loop_set_mode(0, "beat"),
        lambda: build_loop_set_mode(1, "nope"),
        lambda: build_loop_set_beats(5, 4),
    ):
        try:
            bad()
        except (TypeError, ValueError):
            pass
        else:
            raise AssertionError("invalid rhythm/drum builder value accepted")

    assert drum_lane_states(0x0045)[36] is True
    assert drum_lane_states(0x0045)[38] is True
    assert drum_lane_states(0x0045)[42] is True
    assert drum_lane_states(0x0045)[37] is False
    assert drum_lane_mask({36: True, 38: True, 42: True}) == 0x0045
    print("  tempo/meter/transport/drums/loop-mode builders and lane masks")


# ---------------------------------------------------------------------------
# Status parsers
# ---------------------------------------------------------------------------


def test_status_parsers() -> None:
    print("\n=== Test 4: status parsers ===")
    freqset = parse_freqset([220.0, 1.0, 1.0, 1.125, 1.5])
    assert isinstance(freqset, FrequencySet)
    assert freqset.fundamental == 220.0
    assert freqset.anchor == 1.0
    assert freqset.ratios == [1.0, 1.125, 1.5]
    assert freqset.pairs == [(1.0, 220.0), (1.125, 247.5), (1.5, 330.0)]

    no_anchor = parse_freqset([220.0, -1.0, 1.0, 1.25])
    assert no_anchor.anchor is None

    for bad in (
        [220.0],
        [0.0, 1.0, 1.0],
        [220.0, 1.0, 0.0],
        [220.0, 1.0, float("nan")],
    ):
        try:
            parse_freqset(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"invalid freqset accepted: {bad}")

    control = parse_ui_state(
        [0.55, -0.2, 0.25, 0.15, 2, 0.4, 0.3, 5]
    )
    assert isinstance(control, BridgeControlState)
    assert control.change_count == 2 and isinstance(control.change_count, int)
    assert control.max_changes == 5 and isinstance(control.max_changes, int)
    assert control.randomness == 0.55
    assert control.dissonance_bias == -0.2
    assert control.glide_time == 0.15
    assert control.temperature == 0.4

    clamped = parse_ui_state([2.0, -3.0, 3.0, 9.0, 99, 9.0, 9.0, 4])
    assert clamped.randomness == 1.0
    assert clamped.dissonance_bias == -1.0
    assert clamped.stepwise_bias == 1.0
    assert clamped.glide_time == 2.0
    assert clamped.change_count == 4
    assert clamped.temperature == 1.0
    assert clamped.anchor_mobility == 1.0

    midi = parse_midi_status([1, "IAC Driver Bus 1", "IAC Driver Bus 2"])
    assert isinstance(midi, MidiStatus)
    assert midi.enabled is True
    assert midi.drone_port == "IAC Driver Bus 1"
    assert midi.arp_port == "IAC Driver Bus 2"
    disabled = parse_midi_status([0, "", ""])
    assert disabled.enabled is False
    assert disabled.drone_port == "" and disabled.arp_port == ""

    toggle = parse_midi_state([1, 0])
    assert isinstance(toggle, MidiToggleState)
    assert toggle.drone_enabled is True and toggle.arp_enabled is False
    assert parse_midi_state([0, 1]).arp_enabled is True

    assert parse_cpu([12.5]) == 12.5
    assert parse_cpu([150.0]) == 100.0
    assert parse_loop_state([3, "recording"]) == (3, "recording")
    assert parse_loop_state([1, "empty"]) == (1, "empty")

    rhythm = parse_rhythm_state([140.0, 3, 4, 1, 1, 0.5])
    assert isinstance(rhythm, RhythmStatus)
    assert rhythm.bpm == 140.0
    assert rhythm.meter == "3/4"
    assert rhythm.running is True
    assert rhythm.drums_enabled is True
    assert rhythm.drum_rate == 0.5
    clamped = parse_rhythm_state([9999, 99, 8, 0, 0, 1.0])
    assert clamped.bpm == 300.0 and clamped.meter_num == 16
    for bad in (
        lambda: parse_rhythm_state([120, 4, 3, 1, 1, 1.0]),
        lambda: parse_rhythm_state([120, 4, 4, 2, 1, 1.0]),
        lambda: parse_rhythm_state([120, 4, 4, 1, 1, 1.5]),
        lambda: parse_rhythm_state([120, 4, 4, 1, 1]),
    ):
        try:
            bad()
        except (TypeError, ValueError):
            pass
        else:
            raise AssertionError("invalid rhythm state accepted")

    drums = parse_drums_state([1, 0, 1.0, 0x0045])
    assert isinstance(drums, DrumStatus)
    assert drums.enabled is True and drums.port_ok is False
    assert drums.lane_states[36] is True
    assert parse_drums_state([0, 1, 2.0, 70000]).lane_mask == 0xFFFF
    for bad in (
        lambda: parse_drums_state([2, 0, 1.0, 0]),
        lambda: parse_drums_state([1, 0, 1.5, 0]),
        lambda: parse_drums_state([1, 0, 1.0]),
    ):
        try:
            bad()
        except (TypeError, ValueError):
            pass
        else:
            raise AssertionError("invalid drum state accepted")

    groove_state = parse_groove(["break", 0.3, 0.2, 0.15, 0.7, 4, 16])
    assert isinstance(groove_state, GrooveStatus)
    assert groove_state.preset == "break"
    assert groove_state.density == 0.3
    assert groove_state.modifiers["probability"] == 0.7
    assert groove_state.seed == 4 and groove_state.grid_steps == 16
    clamped_groove = parse_groove(["four", 2, -1, 9, 1, 1, 99])
    assert clamped_groove.density == 1.0
    assert clamped_groove.syncopation == 0.0
    assert clamped_groove.swing == 1.0
    assert clamped_groove.grid_steps == 32
    for bad in (
        lambda: parse_groove(["nope", 0, 0, 0, 0, 1, 16]),
        lambda: parse_groove(["four", 0, 0, 0, 0, 1]),
    ):
        try:
            bad()
        except (TypeError, ValueError):
            pass
        else:
            raise AssertionError("invalid groove state accepted")

    masks = parse_grid([0x1111, 0] + [0] * 30)
    assert masks[0] == 0x1111
    assert len(masks) == 16
    assert parse_grid([0] * 30 + [0xFFFF, 0xFFFF])[15] == 0xFFFFFFFF
    for bad in (lambda: parse_grid([0] * 31), lambda: parse_grid([0] * 33)):
        try:
            bad()
        except (TypeError, ValueError):
            pass
        else:
            raise AssertionError("invalid grid payload accepted")

    quantize = parse_quantize([2, 0.32])
    assert isinstance(quantize, QuantizeStatus)
    assert quantize.pending == 2 and quantize.seconds == 0.32
    assert quantize.immediate is False
    stopped = parse_quantize([0, -1.0])
    assert stopped.immediate is True and stopped.seconds == -1.0
    assert parse_quantize([-3, 5.0]).pending == 0
    for bad in (
        lambda: parse_quantize([1]),
        lambda: parse_quantize([1, float("nan")]),
    ):
        try:
            bad()
        except (TypeError, ValueError):
            pass
        else:
            raise AssertionError("invalid quantize payload accepted")

    index, config = parse_loop_config([2, "beat", 7, 3.5])
    assert index == 2
    assert isinstance(config, LoopConfig)
    assert config.mode == "beat" and config.beats == 7.0
    assert config.seconds == 3.5
    assert parse_loop_config([1, "free", 0, 0])[1] == LoopConfig(
        mode="free", beats=1.0, seconds=0.0
    )
    for bad in (
        lambda: parse_loop_config([5, "free", 4, 8]),
        lambda: parse_loop_config([1, "loop", 4, 8]),
        lambda: parse_loop_config([1, "free", 4, -1]),
        lambda: parse_loop_config([1, "free", 4]),
    ):
        try:
            bad()
        except (TypeError, ValueError):
            pass
        else:
            raise AssertionError("invalid loop config accepted")

    for bad in (
        lambda: parse_midi_status([2, "a", "b"]),
        lambda: parse_midi_status([1, "a"]),
        lambda: parse_midi_state([1]),
        lambda: parse_midi_state([2, 0]),
        lambda: parse_loop_state([5, "empty"]),
        lambda: parse_loop_state([1, "nonsense"]),
        lambda: parse_cpu([float("nan")]),
    ):
        try:
            bad()
        except (TypeError, ValueError):
            pass
        else:
            raise AssertionError("invalid status payload was accepted")

    channel, values = parse_autotune_state(
        [1, 0.1, 0.8, 0.2, 7, 0.7, 0.5]
    )
    assert channel == 1
    assert values["mix"] == 0.8
    assert values["pitchRangeLimit"] == 7.0
    assert values["windowSize"] == 0.2
    assert parse_autotune_state([0, 9.0, 9.0, 9.0, 99, 9.0, 9.0])[1][
        "retuneTime"
    ] == 2.0
    print("  freqset/ui/midi/cpu/loop/autotune parsers and ranges are correct")


def test_status_store() -> None:
    print("\n=== Test 5: StatusStore waiting flags and updates ===")
    store = StatusStore()
    assert store.waiting_for_bridge and store.waiting_for_sclang
    assert store.max_changes == 3
    assert store.loop_states == {1: "waiting", 2: "waiting", 3: "waiting", 4: "waiting"}
    assert store.autotune[0]["mix"] == 1.0

    assert store.apply("/ji/ui/state", [0.55, 0.0, 0.25, 0.15, 1, 0.0, 0.25, 5])
    assert store.waiting_for_bridge is False
    assert store.waiting_for_sclang is True
    assert store.max_changes == 5
    assert store.control is not None and store.control.max_changes == 5

    assert store.apply("/ji/cpu", [14.0])
    assert store.waiting_for_sclang is False
    assert store.cpu == 14.0

    assert store.apply("/ji/loop/state", [2, "recorded"])
    assert store.loop_states[2] == "recorded"
    assert store.apply(
        "/ji/autotune/state", [0, 0.05, 1.0, 0.1, 12.0, 0.9, 1.0]
    )
    assert store.autotune[0]["retuneTime"] == 0.05

    assert store.apply("/ji/freqset/changed", [220.0, -1.0, 1.0, 1.5])
    assert store.freqset is not None and store.freqset.anchor is None
    assert store.apply("/ji/midi/status", [0, "", ""])
    assert store.midi is not None and store.midi.enabled is False
    assert store.apply("/ji/midi/state", [1, 0])
    assert store.midi_state is not None
    assert store.midi_state.drone_enabled is True
    assert store.midi_state.arp_enabled is False

    assert store.rhythm is None and store.drums is None
    assert store.loop_configs[1] == LoopConfig()
    assert store.apply("/ji/rhythm/state", [140.0, 3, 4, 1, 1, 0.5])
    assert store.rhythm is not None and store.rhythm.bpm == 140.0
    assert store.rhythm.meter == "3/4" and store.rhythm.running is True
    assert store.apply("/ji/drums/state", [1, 1, 2.0, 0x0045])
    assert store.drums is not None and store.drums.port_ok is True
    assert store.drums.lane_states[42] is True
    assert store.groove is None and store.grid is None and store.quantize is None
    assert store.apply("/ji/drums/groove", ["break", 0.4, 0.2, 0.1, 0.9, 7, 16])
    assert store.groove is not None
    assert store.groove.preset == "break" and store.groove.seed == 7
    assert store.groove.modifiers["density"] == 0.4
    assert store.apply("/ji/drums/grid", [1, 0] + [0] * 30)
    assert store.grid is not None and store.grid[0] == 1
    assert store.apply("/ji/quantize/state", [3, 0.25])
    assert store.quantize is not None
    assert store.quantize.pending == 3 and store.quantize.seconds == 0.25
    assert store.apply("/ji/loop/config", [3, "beat", 7, 3.5])
    assert store.loop_configs[3].mode == "beat"
    assert store.loop_configs[3].beats == 7.0
    assert store.loop_configs[3].seconds == 3.5

    assert store.apply("/unknown", []) is False
    try:
        store.apply("/ji/cpu", ["not-a-number"])
    except TypeError:
        pass
    else:
        raise AssertionError("malformed status did not raise")
    print("  waiting flags, snapshots, and malformed-message rejection are correct")


# ---------------------------------------------------------------------------
# Bridge / server additions
# ---------------------------------------------------------------------------


def test_osc_server_hello() -> None:
    print("\n=== Test 6: MutationOSCServer hello, MIDI, and rhythm callbacks ===")
    engine = MutationEngine(INITIAL, seed=3)
    called: list[int] = []
    midi_called: list[tuple[bool, bool]] = []
    tempo_called: list[float] = []
    meter_called: list[tuple[int, int]] = []
    transport_called: list[bool] = []
    drums_enabled_called: list[bool] = []
    drums_lane_called: list[tuple[int, bool]] = []
    drums_rate_called: list[float] = []
    rhythm_requested: list[int] = []
    preset_called: list[str] = []
    density_called: list[float] = []
    sync_called: list[float] = []
    swing_called: list[float] = []
    probability_called: list[float] = []
    step_called: list[tuple[int, int, bool]] = []
    seed_called: list[int] = []
    reset_called: list[int] = []
    server = MutationOSCServer(
        engine,
        listen_port=19311,
        on_ui_hello=lambda: called.append(1),
        on_midi_enabled=lambda drone, arp: midi_called.append((drone, arp)),
        on_rhythm_tempo=tempo_called.append,
        on_rhythm_meter=lambda num, den: meter_called.append((num, den)),
        on_rhythm_transport=transport_called.append,
        on_drums_enabled=drums_enabled_called.append,
        on_drums_lane=lambda note, on: drums_lane_called.append((note, on)),
        on_drums_rate=drums_rate_called.append,
        on_rhythm_request=lambda: rhythm_requested.append(1),
        on_drums_preset=preset_called.append,
        on_drums_density=density_called.append,
        on_drums_syncopation=sync_called.append,
        on_drums_swing=swing_called.append,
        on_drums_probability=probability_called.append,
        on_drums_step=lambda lane, step, on: step_called.append(
            (lane, step, on)
        ),
        on_drums_seed=seed_called.append,
        on_drums_reset_grid=lambda: reset_called.append(1),
    )
    assert UI_HELLO_ADDRESS == "/ji/ui/hello"
    assert server.UI_HELLO_ADDRESS == "/ji/ui/hello"
    assert MIDI_SET_ENABLED_ADDRESS == "/ji/midi/set_enabled"
    assert RHYTHM_SET_TEMPO_ADDRESS == "/ji/rhythm/set_tempo"
    assert RHYTHM_SET_METER_ADDRESS == "/ji/rhythm/set_meter"
    assert RHYTHM_TRANSPORT_ADDRESS == "/ji/rhythm/transport"
    assert RHYTHM_REQUEST_ADDRESS == "/ji/rhythm/request"
    assert DRUMS_SET_ENABLED_ADDRESS == "/ji/drums/set_enabled"
    assert DRUMS_SET_LANE_ADDRESS == "/ji/drums/set_lane"
    assert DRUMS_SET_RATE_ADDRESS == "/ji/drums/set_rate"
    assert DRUMS_SET_PRESET_ADDRESS == "/ji/drums/set_preset"
    assert DRUMS_SET_DENSITY_ADDRESS == "/ji/drums/set_density"
    assert DRUMS_SET_SYNCOPATION_ADDRESS == "/ji/drums/set_syncopation"
    assert DRUMS_SET_SWING_ADDRESS == "/ji/drums/set_swing"
    assert DRUMS_SET_PROBABILITY_ADDRESS == "/ji/drums/set_probability"
    assert DRUMS_SET_STEP_ADDRESS == "/ji/drums/set_step"
    assert DRUMS_SET_SEED_ADDRESS == "/ji/drums/set_seed"
    assert DRUMS_RESET_GRID_ADDRESS == "/ji/drums/reset_grid"
    server._handle_ui_hello("/ji/ui/hello")
    assert called == [1]
    server._handle_ui_hello("/ji/ui/hello", "extra")
    assert called == [1, 1]

    server._handle_midi_enabled("/ji/midi/set_enabled", 1, 0)
    assert midi_called == [(True, False)]
    server._handle_midi_enabled("/ji/midi/set_enabled", 0, 1)
    assert midi_called[-1] == (False, True)
    # Invalid arity/values are rejected without calling back.
    server._handle_midi_enabled("/ji/midi/set_enabled", 1)
    server._handle_midi_enabled("/ji/midi/set_enabled", 2, 0)
    assert len(midi_called) == 2

    server._handle_set_tempo("/ji/rhythm/set_tempo", 1000.0)
    assert tempo_called == [300.0]
    server._handle_set_tempo("/ji/rhythm/set_tempo", float("nan"))
    assert len(tempo_called) == 1
    server._handle_set_meter("/ji/rhythm/set_meter", 99, 8)
    assert meter_called == [(16, 8)]
    server._handle_set_meter("/ji/rhythm/set_meter", 4, 3)
    assert len(meter_called) == 1
    server._handle_transport("/ji/rhythm/transport", 1)
    server._handle_transport("/ji/rhythm/transport", 2)
    assert transport_called == [True]
    server._handle_drums_enabled("/ji/drums/set_enabled", 1)
    server._handle_drums_enabled("/ji/drums/set_enabled", 2)
    assert drums_enabled_called == [True]
    server._handle_drums_lane("/ji/drums/set_lane", 38, 0)
    assert drums_lane_called == [(38, False)]
    server._handle_drums_lane("/ji/drums/set_lane", 200, 1)
    assert len(drums_lane_called) == 1
    server._handle_drums_rate("/ji/drums/set_rate", 0.5)
    server._handle_drums_rate("/ji/drums/set_rate", 1.5)
    assert drums_rate_called == [0.5]
    server._handle_rhythm_request("/ji/rhythm/request")
    assert rhythm_requested == [1]

    server._handle_drums_preset("/ji/drums/set_preset", "Break")
    server._handle_drums_preset("/ji/drums/set_preset", "nope")
    assert preset_called == ["break"]
    server._handle_drums_density("/ji/drums/set_density", 0.5)
    server._handle_drums_density("/ji/drums/set_density", 2.0)
    server._handle_drums_density("/ji/drums/set_density", float("nan"))
    assert density_called == [0.5, 1.0]
    server._handle_drums_syncopation("/ji/drums/set_syncopation", -1.0)
    assert sync_called == [0.0]
    server._handle_drums_swing("/ji/drums/set_swing", 0.25)
    assert swing_called == [0.25]
    server._handle_drums_probability("/ji/drums/set_probability", 0.75)
    assert probability_called == [0.75]
    server._handle_drums_step("/ji/drums/set_step", 2, 5, 1)
    assert step_called == [(2, 5, True)]
    server._handle_drums_step("/ji/drums/set_step", 16, 5, 1)
    server._handle_drums_step("/ji/drums/set_step", 2, 32, 1)
    assert len(step_called) == 1
    server._handle_drums_seed("/ji/drums/set_seed", 9)
    assert seed_called == [9]
    server._handle_drums_reset_grid("/ji/drums/reset_grid")
    assert reset_called == [1]

    # Stage 8.2 request hooks: the handler asks the bridge to queue, while the
    # direct trigger()/octave() API stays immediate.
    mutation_requests: list[int] = []
    octave_requests: list[int] = []
    hooked = MutationOSCServer(
        engine,
        listen_port=19312,
        on_mutation_request=lambda: mutation_requests.append(1),
        on_octave_request=lambda: octave_requests.append(1),
    )
    before = list(engine.current_ratios)
    hooked._handle_trigger("/ji/mutation/trigger")
    hooked._handle_octave("/ji/mutation/octave")
    assert mutation_requests == [1]
    assert octave_requests == [1]
    assert engine.current_ratios == before  # handler did not apply directly
    hooked.trigger()  # direct API remains immediate
    assert engine.current_ratios != before
    print("  hello, MIDI, rhythm/drum/groove callbacks; quantize hook is soft")


def test_bridge_ui_status() -> None:
    print("\n=== Test 7: JIBridge UI status and hello ===")
    original = bridge_module.SimpleUDPClient
    FakeClient.instances = []
    bridge_module.SimpleUDPClient = FakeClient
    try:
        bridge = bridge_module.JIBridge(
            seed=41,
            max_changes=5,
            change_count=3,
            temperature=0.4,
            anchor_mobility=0.6,
            glide_time=0.3,
        )
        assert len(FakeClient.instances) == 2
        client, ui_client = FakeClient.instances
        assert bridge.client is client
        assert bridge.ui_client is ui_client
        assert bridge.midi is None

        assert bridge.send_set(0.2) is True
        assert client.addresses() == ["/ji/freqset/update"]
        assert ui_client.addresses() == [
            "/ji/freqset/changed",
            "/ji/ui/state",
            "/ji/midi/status",
            "/ji/midi/state",
        ]

        freq_address, freq_args = ui_client.sent[0]
        assert freq_address == "/ji/freqset/changed"
        assert freq_args[0] == 220.0
        assert freq_args[1] == 1.0
        assert len(freq_args) == 2 + len(bridge.engine.current_ratios)
        assert all(isinstance(value, float) for value in freq_args)
        assert freq_args[2:] == sorted(bridge.engine.current_ratios)

        _, state_args = ui_client.sent[1]
        assert len(state_args) == 8
        assert isinstance(state_args[0], float)
        assert isinstance(state_args[1], float)
        assert isinstance(state_args[2], float)
        assert isinstance(state_args[3], float)
        assert isinstance(state_args[4], int) and state_args[4] == 3
        assert isinstance(state_args[5], float)
        assert isinstance(state_args[6], float)
        assert isinstance(state_args[7], int) and state_args[7] == 5

        _, midi_args = ui_client.sent[2]
        assert midi_args == [0, "", ""]
        _, midi_state_args = ui_client.sent[3]
        assert midi_state_args == [0, 0]

        # The Stage 3 payload must remain exactly one update per set.
        assert len(client.sent) == 1
        assert client.sent[0][0] == "/ji/freqset/update"
        assert client.sent[0][1][1] == 0.2

        # A UI hello replies with a snapshot without sending another set.
        ui_client.sent.clear()
        bridge.server._handle_ui_hello("/ji/ui/hello")
        assert ui_client.addresses() == [
            "/ji/freqset/changed",
            "/ji/ui/state",
            "/ji/midi/status",
            "/ji/midi/state",
            "/ji/rhythm/state",
            "/ji/drums/state",
            "/ji/drums/groove",
            "/ji/drums/grid",
            "/ji/quantize/state",
        ]
        assert len(client.sent) == 1
        rhythm_args = ui_client.sent[4][1]
        assert rhythm_args == [120.0, 4, 4, 0, 0, 1.0]
        assert ui_client.sent[5][1] == [0, 0, 1.0, DRUM_LANE_DEFAULT_MASK]
        groove_args = ui_client.sent[6][1]
        assert groove_args[:2] == ["four", 0.0]
        assert groove_args[5] == 1 and groove_args[6] == 16
        assert len(ui_client.sent[7][1]) == 32
        assert ui_client.sent[8][1] == [0, -1.0]

        # Rhythm commands: state changes go to sclang and the UI.
        client.sent.clear()
        ui_client.sent.clear()
        bridge._on_rhythm_tempo(140.0)
        assert ("/ji/rhythm/tempo", [140.0, 4, 4, 0]) in [
            (address, list(args)) for address, args in client.sent
        ]
        assert ui_client.addresses() == [
            "/ji/rhythm/state",
            "/ji/quantize/state",
        ]
        assert ui_client.sent[0][1][0] == 140.0

        ui_client.sent.clear()
        bridge._on_rhythm_meter(3, 4)
        assert ui_client.sent[0][1][1:3] == [3, 4]
        ui_client.sent.clear()
        bridge._on_rhythm_transport(True)
        assert ui_client.sent[0][1][3] == 1
        assert bridge.rhythm.running is True
        # Transport changes also refresh the quantize state (countdown).
        assert ui_client.addresses()[-1] == "/ji/quantize/state"
        bridge._on_rhythm_transport(False)
        assert bridge.rhythm.running is False

        ui_client.sent.clear()
        bridge._on_rhythm_request()
        assert client.addresses()[-1] == "/ji/rhythm/tempo"

        # Drums are unavailable without --drums: state is reported, no crash.
        ui_client.sent.clear()
        bridge._on_drums_enabled(True)
        assert ui_client.addresses() == [
            "/ji/drums/state",
            "/ji/rhythm/state",
        ]
        assert ui_client.sent[0][1] == [0, 0, 1.0, DRUM_LANE_DEFAULT_MASK]
        ui_client.sent.clear()
        bridge._on_drums_lane(36, True)
        assert ui_client.addresses() == ["/ji/drums/state"]
        ui_client.sent.clear()
        bridge._on_drums_rate(2.0)
        assert ui_client.addresses() == ["/ji/drums/state"]
        assert ui_client.sent[0][1] == [0, 0, 1.0, DRUM_LANE_DEFAULT_MASK]

        # Explicit MIDI status with an injected rig, then independent toggles.
        fake_midi = FakeMidiRig(drums=FakeDrumRig())
        bridge.midi = fake_midi
        ui_client.sent.clear()
        bridge._send_ui_state()
        assert ui_client.addresses()[-2:] == [
            "/ji/midi/status",
            "/ji/midi/state",
        ]
        assert ui_client.sent[-2][1] == [
            1,
            "IAC Driver Bus 1",
            "IAC Driver Bus 2",
        ]
        assert ui_client.sent[-1][1] == [1, 1]

        ui_client.sent.clear()
        bridge._set_midi_enabled(False, True)
        assert fake_midi.drone_enabled is False
        assert fake_midi.arp_enabled is True
        assert fake_midi.calls == [("drone", False), ("arp", True)]
        assert ui_client.addresses() == [
            "/ji/midi/state",
            "/ji/quantize/state",
        ]
        assert ui_client.sent[0][1] == [0, 1]

        ui_client.sent.clear()
        bridge._set_midi_enabled(True, False)
        assert fake_midi.drone_enabled is True
        assert fake_midi.arp_enabled is False
        assert ui_client.sent[0][1] == [1, 0]

        # Drum state/mutations with the injected drum rig.
        ui_client.sent.clear()
        bridge._on_drums_enabled(True)
        assert fake_midi.drum_calls[-1] == ("enabled", True)
        assert ui_client.addresses() == [
            "/ji/drums/state",
            "/ji/rhythm/state",
            "/ji/quantize/state",
        ]
        assert ui_client.sent[0][1] == [1, 1, 1.0, DRUM_LANE_DEFAULT_MASK]

        ui_client.sent.clear()
        bridge._on_drums_lane(37, True)
        assert fake_midi.drums is not None
        assert fake_midi.drums.lane_mask & (1 << 1)
        assert ui_client.sent[0][0] == "/ji/drums/state"
        assert ui_client.sent[0][1][3] == fake_midi.drums.lane_mask

        ui_client.sent.clear()
        bridge._on_drums_rate(0.5)
        assert fake_midi.drums.rate == 0.5
        assert ui_client.sent[0][1][2] == 0.5
        assert ui_client.sent[1][1][5] == 0.5

        client.sent.clear()
        bridge._on_rhythm_tempo(90.0)
        assert fake_midi.drum_calls[-1][0] == "bpm"
        ui_client.sent.clear()
        bridge._on_rhythm_meter(5, 4)
        assert fake_midi.drum_calls[-1] == ("meter", 5, 4)
        bridge._on_rhythm_transport(False)
        assert fake_midi.drum_calls[-1] == ("transport", False)

        # --- Stage 8.2 groove state and quantized edits ----------------
        bridge._on_rhythm_transport(False)
        ui_client.sent.clear()
        bridge._on_drums_preset("break")
        assert bridge.groove.spec.preset == "break"
        assert "/ji/drums/groove" in ui_client.addresses()
        assert "/ji/drums/grid" in ui_client.addresses()
        assert bridge.quantizer.pending_count == 0

        # With the transport running, a groove edit is queued and only lands
        # when the queue flushes at the beat.
        bridge._on_rhythm_transport(True)
        bridge._on_drums_preset("techno")
        assert bridge.groove.spec.preset == "break"
        assert bridge.quantizer.pending_count == 1
        ui_client.sent.clear()
        bridge._on_quantize_change()
        assert ui_client.addresses() == ["/ji/quantize/state"]
        assert ui_client.sent[0][1][0] == 1
        bridge.quantizer.flush("test beat")
        assert bridge.groove.spec.preset == "techno"
        assert bridge.quantizer.pending_count == 0

        bridge._on_rhythm_transport(False)
        bridge._on_groove_modifier("density", 0.5)
        assert bridge.groove.spec.density == 0.5
        bridge._on_drums_seed(7)
        assert bridge.groove.spec.seed == 7
        bridge._on_drums_step(0, 1, True)
        assert bridge.groove.grid.is_on(0, 1)
        bridge._on_drums_reset_grid()
        assert not bridge.groove.grid.is_on(0, 1)

        # Mutation requests obey the same rule: immediate when stopped,
        # queued while running.
        bridge._on_rhythm_transport(False)
        before = list(bridge.engine.current_ratios)
        bridge.request_mutation()
        assert bridge.engine.current_ratios != before
        assert bridge.quantizer.pending_count == 0
        bridge._on_rhythm_transport(True)
        after_immediate = list(bridge.engine.current_ratios)
        bridge.request_octave()
        assert bridge.quantizer.pending_count == 1
        bridge.quantizer.flush("test beat")
        assert bridge.quantizer.pending_count == 0
        assert list(bridge.engine.current_ratios) == after_immediate
        bridge._on_rhythm_transport(False)

        # The CLI defaults remain compatible.
        parser = bridge_module.build_parser()
        defaults = parser.parse_args([])
        assert defaults.ui_host == "127.0.0.1"
        assert defaults.ui_port == 9001
        assert defaults.drums is False
        assert defaults.rhythm_config is None
        assert parser.parse_args(["--drums"]).drums is True
        assert parser.parse_args(["--midi"]).drums is False
    finally:
        bridge_module.SimpleUDPClient = original
    print("  one /ji/freqset/update per set; UI/rhythm/drum messages are well formed")


# ---------------------------------------------------------------------------
# OSCController
# ---------------------------------------------------------------------------


def test_osc_controller() -> None:
    print("\n=== Test 8: OSCController lifecycle and sends ===")
    bridge_client = FakeClient("127.0.0.1", 9000)
    sc_client = FakeClient("127.0.0.1", 57120)
    listener = FakeListener("127.0.0.1", 9001, lambda address, args: None)
    statuses: list[tuple[str, list]] = []

    controller = OSCController(
        bridge_client=bridge_client,
        sc_client=sc_client,
        listener=listener,
        on_status=lambda address, args: statuses.append((address, args)),
    )
    assert controller.started is False
    controller.start()
    assert controller.started is True
    assert listener.started is True
    assert listener.order[0] == "listen"

    # hello goes to the bridge; query goes to sclang.
    assert bridge_client.addresses() == ["/ji/ui/hello"]
    assert sc_client.addresses() == ["/ji/status/query"]

    bridge_client.sent.clear()
    sc_client.sent.clear()
    controller.set_mutation("randomness", 0.7)
    controller.trigger_mutation()
    controller.trigger_octave()
    controller.set_midi_enabled(True, False)
    assert bridge_client.addresses() == [
        "/ji/mutation/set_randomness",
        "/ji/mutation/trigger",
        "/ji/mutation/octave",
        "/ji/midi/set_enabled",
    ]
    assert bridge_client.sent[0][1] == [0.7]
    assert bridge_client.sent[-1][1] == [1, 0]

    controller.set_autotune(1, "mix", 0.5)
    controller.loop_record(2, 12.0)
    controller.loop_stop_record(3)
    controller.loop_play(1)
    controller.loop_stop(2)
    controller.loop_clear(4)
    controller.loop_stop_all()
    controller.loop_set_default_length(10.0)
    controller.loop_set_mode(1, "beat")
    controller.loop_set_beats(1, 7)
    controller.loop_set_length(2, 12.0)
    controller.request_state()
    assert sc_client.addresses() == [
        "/ji/autotune/set",
        "/ji/loop/record_length",
        "/ji/loop/stop_record",
        "/ji/loop/play",
        "/ji/loop/stop",
        "/ji/loop/clear",
        "/ji/loop/stop_all",
        "/ji/loop/set_default_length",
        "/ji/loop/set_mode",
        "/ji/loop/set_beats",
        "/ji/loop/set_length",
        "/ji/status/query",
    ]
    assert sc_client.sent[1][1] == [2, 12.0]
    assert sc_client.sent[-4][1] == [1, "beat"]
    assert sc_client.sent[-3][1] == [1, 7.0]
    assert sc_client.sent[-2][1] == [2, 12.0]

    bridge_client.sent.clear()
    controller.set_tempo(140)
    controller.set_meter(3, 4)
    controller.set_transport(True)
    controller.set_drums_enabled(True)
    controller.set_drum_lane(36, False)
    controller.set_drum_rate(2.0)
    controller.set_drums_preset("Break")
    controller.set_groove_modifier("density", 0.4)
    controller.set_drum_step(2, 5, True)
    controller.set_drum_seed(9)
    controller.reset_drum_grid()
    controller.request_rhythm_resync()
    assert bridge_client.addresses() == [
        "/ji/rhythm/set_tempo",
        "/ji/rhythm/set_meter",
        "/ji/rhythm/transport",
        "/ji/drums/set_enabled",
        "/ji/drums/set_lane",
        "/ji/drums/set_rate",
        "/ji/drums/set_preset",
        "/ji/drums/set_density",
        "/ji/drums/set_step",
        "/ji/drums/set_seed",
        "/ji/drums/reset_grid",
        "/ji/rhythm/request",
    ]
    assert bridge_client.sent[0][1] == [140.0]
    assert bridge_client.sent[1][1] == [3, 4]
    assert bridge_client.sent[2][1] == [1]
    assert bridge_client.sent[4][1] == [36, 0]
    assert bridge_client.sent[5][1] == [2.0]
    assert bridge_client.sent[6][1] == ["break"]
    assert bridge_client.sent[7][1] == [0.4]
    assert bridge_client.sent[8][1] == [2, 5, 1]
    assert bridge_client.sent[9][1] == [9]
    assert bridge_client.sent[10][1] == []
    assert bridge_client.sent[-1][1] == []

    # Status handling updates the store and invokes the callback.
    controller._handle_status("/ji/cpu", [22.5])
    assert controller.store.cpu == 22.5
    assert statuses[-1] == ("/ji/cpu", [22.5])
    before = len(statuses)
    controller._handle_status("/ji/cpu", ["bad"])
    assert len(statuses) == before

    controller.stop()
    assert controller.started is False
    assert listener.stopped is True
    assert bridge_client.closed is False  # injected clients are not owned

    # A second start reuses the same injected listener and re-announces.
    controller.start()
    assert listener.start_count == 2
    controller.stop()
    print("  listener-first start, hello/query, sends, callbacks, clean stop")


def test_controller_startup_send_failure_is_nonfatal() -> None:
    print("\n=== Test 9: missing bridge/sclang is non-fatal ===")

    class FailingClient(FakeClient):
        def send_message(self, address, args):
            raise OSError("no route")

    listener = FakeListener("127.0.0.1", 9001, lambda address, args: None)
    controller = OSCController(
        bridge_client=FailingClient("127.0.0.1", 9000),
        sc_client=FailingClient("127.0.0.1", 57120),
        listener=listener,
    )
    controller.start()
    assert controller.started is True
    controller.stop()
    print("  startup announce errors are logged, not fatal")


# ---------------------------------------------------------------------------
# Optional headless Qt smoke test
# ---------------------------------------------------------------------------


class FakeController:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.store = StatusStore()
        self.started = False
        self.stopped = False
        self.callback = None

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def set_status_callback(self, callback) -> None:
        self.callback = callback

    def set_mutation(self, param, value) -> None:
        self.calls.append(("set_mutation", param, value))

    def trigger_mutation(self) -> None:
        self.calls.append(("trigger_mutation",))

    def trigger_octave(self) -> None:
        self.calls.append(("trigger_octave",))

    def set_autotune(self, channel, param, value) -> None:
        self.calls.append(("set_autotune", channel, param, value))

    def set_midi_enabled(self, drone, arp) -> None:
        self.calls.append(("set_midi_enabled", drone, arp))

    def loop_record(self, index, seconds=None) -> None:
        self.calls.append(("loop_record", index, seconds))

    def loop_stop_record(self, index) -> None:
        self.calls.append(("loop_stop_record", index))

    def loop_play(self, index) -> None:
        self.calls.append(("loop_play", index))

    def loop_stop(self, index) -> None:
        self.calls.append(("loop_stop", index))

    def loop_clear(self, index) -> None:
        self.calls.append(("loop_clear", index))

    def loop_stop_all(self) -> None:
        self.calls.append(("loop_stop_all",))

    def loop_set_default_length(self, seconds) -> None:
        self.calls.append(("loop_set_default_length", seconds))

    def loop_set_mode(self, index, mode) -> None:
        self.calls.append(("loop_set_mode", index, mode))

    def loop_set_beats(self, index, beats) -> None:
        self.calls.append(("loop_set_beats", index, beats))

    def loop_set_length(self, index, seconds) -> None:
        self.calls.append(("loop_set_length", index, seconds))

    def set_tempo(self, bpm) -> None:
        self.calls.append(("set_tempo", bpm))

    def set_meter(self, numerator, denominator) -> None:
        self.calls.append(("set_meter", numerator, denominator))

    def set_transport(self, running) -> None:
        self.calls.append(("set_transport", running))

    def request_rhythm_resync(self) -> None:
        self.calls.append(("request_rhythm_resync",))

    def set_drums_enabled(self, enabled) -> None:
        self.calls.append(("set_drums_enabled", enabled))

    def set_drum_lane(self, note, enabled) -> None:
        self.calls.append(("set_drum_lane", note, enabled))

    def set_drum_rate(self, rate) -> None:
        self.calls.append(("set_drum_rate", rate))

    def set_drums_preset(self, preset) -> None:
        self.calls.append(("set_drums_preset", preset))

    def set_groove_modifier(self, param, value) -> None:
        self.calls.append(("set_groove_modifier", param, value))

    def set_drum_step(self, lane, step, enabled) -> None:
        self.calls.append(("set_drum_step", lane, step, enabled))

    def set_drum_seed(self, seed) -> None:
        self.calls.append(("set_drum_seed", seed))

    def reset_drum_grid(self) -> None:
        self.calls.append(("reset_drum_grid",))


def run_qt_tests() -> int:
    print("\n=== Qt smoke test: one window, widgets, signals, close ===")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    try:
        import PyQt6  # noqa: F401
        from PyQt6.QtWidgets import QApplication, QGroupBox
    except ImportError as exc:
        print(
            "SKIP: PyQt6/Qt could not be imported "
            f"({exc}); --pure does not need it."
        )
        return 0

    from ui.ji_ui import MainWindow

    app = QApplication.instance() or QApplication([])
    fake = FakeController()
    window = MainWindow(fake, autostart=False)
    window.show()
    app.processEvents()

    titles = {group.title() for group in window.findChildren(QGroupBox)}
    for expected in (
        "Mutation",
        "Live Autotune",
        "Loops",
        "Rhythm",
        "Groove",
        "MIDI",
        "Status",
    ):
        assert expected in titles, f"missing {expected} group: {titles}"
    assert window.show_grid_checkbox.isChecked() is False
    assert window.grid_scroll.isVisible() is False
    assert len(window.step_cells) == 0, "grid must be built lazily"
    assert "waiting" in window.quantize_label.text()
    assert window.width() <= 1400 and window.height() <= 820, (
        window.width(),
        window.height(),
    )
    assert len(window.loop_state_labels) == 4
    assert len(window.autotune_knobs) == 2 * len(AUTOTUNE_PARAMS)
    assert len(window.lane_checkboxes) == 16
    assert len(window.loop_mode_combos) == 4
    assert len(window.loop_length_spins) == 4
    assert fake.callback is not None
    # No status has arrived: the window must show waiting, not crash.
    assert "waiting for bridge" in window.bridge_status_label.text()
    assert "waiting for sclang" in window.sc_status_label.text()

    # Mutation knob is coalesced, then flushed by the 30 Hz timer path.
    window.mutation_knobs["randomness"].set_value(0.8, emit=True)
    window._flush_pending()
    assert fake.calls[-1][0] == "set_mutation"
    assert fake.calls[-1][1] == "randomness"
    assert abs(fake.calls[-1][2] - 0.8) < 1e-6

    fake.calls.clear()
    window.mutate_button.click()
    window.octave_button.click()
    assert fake.calls == [("trigger_mutation",), ("trigger_octave",)]

    # Link mirrors an autotune knob to both channels.
    fake.calls.clear()
    window.link_checkbox.setChecked(True)
    window.autotune_knobs[(0, "mix")].set_value(0.4, emit=True)
    window._flush_pending()
    linked = [call for call in fake.calls if call[0] == "set_autotune"]
    assert len(linked) == 2, linked
    assert {call[1] for call in linked} == {0, 1}
    assert all(call[2] == "mix" and abs(call[3] - 0.4) < 1e-6 for call in linked)

    # Record always sends the length displayed by the knob (0.1 s precision).
    fake.calls.clear()
    window.length_spin.setValue(12.0)
    displayed = round(window.length_spin.value(), 1)
    assert abs(displayed - 12.0) < 1e-9, (
        f"length spin shows {window.length_spin.value()!r}, expected 12.0"
    )
    window.loop_buttons[(2, "record")].click()
    record_calls = [call for call in fake.calls if call[0] == "loop_record"]
    assert len(record_calls) == 1, f"record calls: {fake.calls!r}"
    assert record_calls[0][1] == 2
    assert abs(record_calls[0][2] - displayed) < 1e-9, (
        f"record seconds {record_calls[0][2]!r}, displayed {displayed!r}"
    )
    window._flush_pending()
    length_calls = [
        call for call in fake.calls if call[0] == "loop_set_default_length"
    ]
    assert len(length_calls) == 1, f"length calls: {fake.calls!r}"
    assert abs(length_calls[0][1] - displayed) < 1e-9, (
        f"default length {length_calls[0][1]!r}, displayed {displayed!r}"
    )

    # Every loop action must carry its own 1-based index.  QPushButton.clicked
    # emits a bool, so a lambda like ``lambda i=index`` would receive False.
    fake.calls.clear()
    for index in range(1, 5):
        for name in ("stop_record", "play", "stop", "clear"):
            window.loop_buttons[(index, name)].click()
    for name in ("loop_stop_record", "loop_play", "loop_stop", "loop_clear"):
        indices = [call[1] for call in fake.calls if call[0] == name]
        assert indices == [1, 2, 3, 4], f"{name} indices: {indices!r}"
    window.stop_all_button.click()
    assert ("loop_stop_all",) in fake.calls

    # Status labels update from parsed messages.
    window._on_status_message(
        "/ji/ui/state", [0.55, 0.0, 0.25, 0.15, 2, 0.1, 0.3, 5]
    )
    assert window.mutation_knobs["change_count"].value() == 2.0
    assert "changes 2/5" in window.control_label.text()

    window._on_status_message(
        "/ji/freqset/changed", [220.0, 1.0, 1.0, 1.125, 1.5]
    )
    assert "1.125000" in window.freqset_label.text()
    assert "247.50" in window.freqset_label.text()

    window._on_status_message(
        "/ji/autotune/state", [1, 0.1, 0.8, 0.2, 7.0, 0.7, 0.5]
    )
    assert abs(window.autotune_knobs[(1, "mix")].value() - 0.8) < 1e-6

    window._on_status_message("/ji/cpu", [12.5])
    assert "12.5%" in window.cpu_label.text()

    window._on_status_message(
        "/ji/midi/status", [1, "IAC Driver Bus 1", "IAC Driver Bus 2"]
    )
    assert "IAC Driver Bus 1" in window.midi_label.text()
    assert window.midi_drone_checkbox.isEnabled()
    assert window.midi_arp_checkbox.isEnabled()

    # Per-bus toggles: state sync must not emit feedback OSC.
    window._on_status_message("/ji/midi/state", [1, 0])
    assert window.midi_drone_checkbox.isChecked()
    assert not window.midi_arp_checkbox.isChecked()
    assert "(on)" in window.midi_label.text()
    assert "(off)" in window.midi_label.text()

    fake.calls.clear()
    window.midi_arp_checkbox.setChecked(True)
    assert ("set_midi_enabled", True, True) in fake.calls, fake.calls

    fake.calls.clear()
    window._on_status_message("/ji/midi/state", [0, 1])
    assert not window.midi_drone_checkbox.isChecked()
    assert window.midi_arp_checkbox.isChecked()
    assert not [c for c in fake.calls if c[0] == "set_midi_enabled"], fake.calls

    window._on_status_message("/ji/midi/status", [0, "", ""])
    assert not window.midi_drone_checkbox.isEnabled()
    assert not window.midi_arp_checkbox.isEnabled()

    window._on_status_message("/ji/loop/state", [2, "recording"])
    assert "recording" in window.loop_state_labels[2].text()

    # --- Stage 8.1 Rhythm group: sends and status sync -------------------
    fake.calls.clear()
    window.bpm_spin.setValue(140)
    window._flush_pending()
    assert ("set_tempo", 140.0) in fake.calls, fake.calls

    fake.calls.clear()
    window.meter_num_spin.setValue(3)
    assert ("set_meter", 3, 4) in fake.calls, fake.calls
    window.meter_den_combo.setCurrentText("8")
    assert ("set_meter", 3, 8) in fake.calls, fake.calls

    fake.calls.clear()
    window.transport_button.click()
    assert ("set_transport", True) in fake.calls, fake.calls
    assert window.transport_button.text() == "Stop"
    window.transport_button.click()
    assert ("set_transport", False) in fake.calls, fake.calls
    assert window.transport_button.text() == "Play"

    fake.calls.clear()
    window.lane_checkboxes[36].setChecked(False)
    assert ("set_drum_lane", 36, False) in fake.calls, fake.calls
    window.lane_checkboxes[36].setChecked(True)
    window.drum_rate_combo.setCurrentIndex(2)
    assert ("set_drum_rate", 2.0) in fake.calls, fake.calls
    window.resync_button.click()
    assert ("request_rhythm_resync",) in fake.calls, fake.calls

    # Per-loop mode switch and length control (beats in beat mode).
    fake.calls.clear()
    window.loop_mode_combos[1].setCurrentIndex(1)
    assert ("loop_set_mode", 1, "beat") in fake.calls, fake.calls
    assert window.loop_length_spins[1].suffix() == " b"
    window.loop_length_spins[1].setValue(7)
    window._flush_pending()
    assert ("loop_set_beats", 1, 7.0) in fake.calls, fake.calls

    # Incoming rhythm/drums state syncs the widgets without feedback OSC.
    fake.calls.clear()
    window._on_status_message(
        "/ji/rhythm/state", [132.0, 5, 4, 0, 1, 0.5]
    )
    assert abs(window.bpm_spin.value() - 132.0) < 1e-6
    assert window.meter_num_spin.value() == 5
    assert window.meter_den_combo.currentText() == "4"
    assert not window.transport_button.isChecked()
    assert "132.0 BPM" in window.rhythm_label.text()
    assert "5/4" in window.rhythm_label.text()

    window._on_status_message("/ji/drums/state", [1, 1, 2.0, 0x0045])
    assert window.drum_enable_checkbox.isEnabled()
    assert window.drum_enable_checkbox.isChecked()
    assert window.drum_rate_combo.currentIndex() == 2
    assert window.lane_checkboxes[36].isChecked()
    assert not window.lane_checkboxes[37].isChecked()
    assert "Drums: on" in window.drum_label.text()
    assert not [call for call in fake.calls if call[0].startswith("set_")], fake.calls

    # Beat-mode Play is rejected locally while the transport is stopped.
    window._on_status_message("/ji/loop/config", [1, "beat", 4, 2.0])
    assert window.loop_mode_combos[1].currentIndex() == 1
    assert window.loop_length_spins[1].suffix() == " b"
    assert window.loop_length_spins[1].value() == 4.0
    fake.calls.clear()
    window.loop_buttons[(1, "play")].click()
    assert not [call for call in fake.calls if call[0] == "loop_play"], fake.calls
    assert "beat-locked" in window.message_label.text()

    # --- Stage 8.2 Groove group and lazy step grid ----------------------
    fake.calls.clear()
    window.show_grid_checkbox.setChecked(True)
    app.processEvents()
    expected_steps = grid_steps_for_meter(5, 4)  # meter from the earlier sync
    assert window.grid_scroll.isVisible() is True
    assert window._grid_built_steps == expected_steps
    assert len(window.step_cells) == 16 * expected_steps
    assert all(cell.width() == 14 for cell in list(window.step_cells.values())[:8])

    window.step_cells[(0, 1)].click()
    assert ("set_drum_step", 0, 1, True) in fake.calls, fake.calls

    fake.calls.clear()
    window.groove_preset_combo.setCurrentIndex(1)
    assert ("set_drums_preset", "break") in fake.calls, fake.calls
    window.groove_knobs["density"].set_value(0.5, emit=True)
    window._flush_pending()
    assert ("set_groove_modifier", "density", 0.5) in fake.calls, fake.calls
    window.groove_seed_spin.setValue(7)
    assert ("set_drum_seed", 7) in fake.calls, fake.calls
    window.reset_grid_button.click()
    assert ("reset_drum_grid",) in fake.calls, fake.calls

    # Incoming groove/grid state syncs widgets without feedback OSC.
    fake.calls.clear()
    window._on_status_message(
        "/ji/drums/groove", ["break", 0.5, 0.2, 0.1, 0.8, 7, 16]
    )
    assert window.groove_preset_combo.currentIndex() == 1
    assert abs(window.groove_knobs["density"].value() - 0.5) < 1e-6
    assert abs(window.groove_knobs["probability"].value() - 0.8) < 1e-6
    assert window.groove_seed_spin.value() == 7
    assert not [c for c in fake.calls if c[0].startswith("set_")], fake.calls

    window._on_status_message("/ji/drums/grid", [1, 0] + [0] * 30)
    assert window.step_cells[(0, 0)].isChecked()
    assert not window.step_cells[(0, 1)].isChecked()
    assert not [c for c in fake.calls if c[0] == "set_drum_step"], fake.calls

    # Pending/quantize status line: running shows a countdown and count;
    # stopped shows the immediate mode.
    window._on_status_message("/ji/rhythm/state", [120, 4, 4, 1, 1, 1.0])
    window._on_status_message("/ji/quantize/state", [2, 0.32])
    assert "2 pending" in window.quantize_label.text()
    assert "quantized off" not in window.quantize_label.text()

    fake.calls.clear()
    window.mutate_button.click()
    assert ("trigger_mutation",) in fake.calls
    assert "queued" in window.message_label.text().lower()

    window._on_status_message("/ji/rhythm/state", [120, 4, 4, 0, 1, 1.0])
    window._on_status_message("/ji/quantize/state", [0, -1.0])
    assert "quantized off" in window.quantize_label.text()

    fake.calls.clear()
    window.mutate_button.click()
    assert ("trigger_mutation",) in fake.calls
    assert "immediately" in window.message_label.text().lower()

    # Missing drum port: show disabled state, disable the controls, no crash.
    window._on_status_message("/ji/drums/state", [0, 0, 1.0, 0x0045])
    assert not window.drum_enable_checkbox.isEnabled()
    assert not window.drum_rate_combo.isEnabled()
    assert not window.lane_checkboxes[36].isEnabled()
    assert "not available" in window.drum_label.text()

    window.close()
    app.processEvents()
    assert fake.stopped is True
    window.deleteLater()
    app.processEvents()
    print("  seven groups, groove/grid sync, pending status, and close succeeded")
    return 0


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def run_pure_tests() -> None:
    assert "PyQt6" not in sys.modules, "ui.osc_control imported PyQt6"
    test_mutation_builders()
    test_autotune_builders()
    test_loop_builders()
    test_rhythm_drum_builders()
    test_status_parsers()
    test_status_store()
    test_osc_server_hello()
    test_bridge_ui_status()
    test_osc_controller()
    test_controller_startup_send_failure_is_nonfatal()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Stage 7 UI harness")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--pure",
        action="store_true",
        help="run pure OSC/message tests (no PyQt6, no sockets)",
    )
    group.add_argument(
        "--qt",
        action="store_true",
        help="run the optional offscreen Qt smoke test (requires PyQt6)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.pure:
            print("JI Stage 7 GUI -- pure OSC/message harness")
            run_pure_tests()
            print("\nALL UI PURE TESTS PASSED")
            return 0
        return run_qt_tests()
    except Exception:
        traceback.print_exc()
        print("\nUI TESTS FAILED", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

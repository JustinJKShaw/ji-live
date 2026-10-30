#!/usr/bin/env python3
"""Stage 7 GUI harness: pure OSC tests plus an optional headless Qt smoke test.

Run from ``ji-live/``.

Pure tests (no PyQt6, no sockets, no audio/MIDI)::

    uv run --with python-osc python test_ui.py --pure

Optional offscreen Qt smoke test (needs PyQt6)::

    QT_QPA_PLATFORM=offscreen uv run --with python-osc --with PyQt6 \
      python test_ui.py --qt

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
from types import SimpleNamespace

import control.ji_bridge as bridge_module
from control.mutation import MutationEngine
from control.osc_server import (
    MIDI_SET_ENABLED_ADDRESS,
    MutationOSCServer,
    UI_HELLO_ADDRESS,
)
from ui import osc_control
from ui.osc_control import (
    AUTOTUNE_PARAMS,
    BridgeControlState,
    FrequencySet,
    MidiStatus,
    MidiToggleState,
    OSCController,
    StatusStore,
    build_loop_clear,
    build_loop_play,
    build_loop_record,
    build_loop_set_default_length,
    build_loop_stop,
    build_loop_stop_all,
    build_loop_stop_record,
    build_request_state,
    build_set_autotune,
    build_set_midi_enabled,
    build_set_mutation,
    build_trigger_mutation,
    build_trigger_octave,
    build_ui_hello,
    parse_autotune_state,
    parse_cpu,
    parse_freqset,
    parse_loop_state,
    parse_midi_status,
    parse_midi_state,
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


class FakeMidiRig:
    """Stand-in for MIDIRig's per-bus switches (no mido, no ports)."""

    def __init__(self) -> None:
        self.config = SimpleNamespace(
            drone_port="IAC Driver Bus 1", arp_port="IAC Driver Bus 2"
        )
        self.drone_enabled = True
        self.arp_enabled = True
        self.calls: list[tuple[str, bool]] = []

    def set_drone_enabled(self, enabled: bool) -> None:
        self.drone_enabled = bool(enabled)
        self.calls.append(("drone", bool(enabled)))

    def set_arp_enabled(self, enabled: bool) -> None:
        self.arp_enabled = bool(enabled)
        self.calls.append(("arp", bool(enabled)))


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
    print("\n=== Test 6: MutationOSCServer hello and MIDI-toggle callbacks ===")
    engine = MutationEngine(INITIAL, seed=3)
    called: list[int] = []
    midi_called: list[tuple[bool, bool]] = []
    server = MutationOSCServer(
        engine,
        listen_port=19311,
        on_ui_hello=lambda: called.append(1),
        on_midi_enabled=lambda drone, arp: midi_called.append((drone, arp)),
    )
    assert UI_HELLO_ADDRESS == "/ji/ui/hello"
    assert server.UI_HELLO_ADDRESS == "/ji/ui/hello"
    assert MIDI_SET_ENABLED_ADDRESS == "/ji/midi/set_enabled"
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
    print("  hello and MIDI-toggle callbacks run; invalid values are rejected")


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
        ]
        assert len(client.sent) == 1

        # Explicit MIDI status with an injected rig, then independent toggles.
        fake_midi = FakeMidiRig()
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
        assert ui_client.addresses() == ["/ji/midi/state"]
        assert ui_client.sent[0][1] == [0, 1]

        ui_client.sent.clear()
        bridge._set_midi_enabled(True, False)
        assert fake_midi.drone_enabled is True
        assert fake_midi.arp_enabled is False
        assert ui_client.sent[0][1] == [1, 0]

        # The CLI defaults remain compatible.
        parser = bridge_module.build_parser()
        defaults = parser.parse_args([])
        assert defaults.ui_host == "127.0.0.1"
        assert defaults.ui_port == 9001
    finally:
        bridge_module.SimpleUDPClient = original
    print("  one /ji/freqset/update per set; UI messages are well formed")


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
        "/ji/status/query",
    ]
    assert sc_client.sent[1][1] == [2, 12.0]

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
    for expected in ("Mutation", "Live Autotune", "Loops", "Status"):
        assert expected in titles, f"missing {expected} group: {titles}"
    assert len(window.loop_state_labels) == 4
    assert len(window.autotune_knobs) == 2 * len(AUTOTUNE_PARAMS)
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
    window.length_knob.set_value(12.0, emit=True)
    displayed = round(window.length_knob.value(), 1)
    assert abs(displayed - 12.0) < 1e-9, (
        f"length knob shows {window.length_knob.value()!r}, expected 12.0"
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

    window.close()
    app.processEvents()
    assert fake.stopped is True
    window.deleteLater()
    app.processEvents()
    print("  four groups, knob/button sends, status sync, and close succeeded")
    return 0


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def run_pure_tests() -> None:
    assert "PyQt6" not in sys.modules, "ui.osc_control imported PyQt6"
    test_mutation_builders()
    test_autotune_builders()
    test_loop_builders()
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

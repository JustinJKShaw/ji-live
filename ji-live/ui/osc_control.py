#!/usr/bin/env python3
"""Stage 7 pure OSC message/state layer for the performance GUI.

This module is deliberately free of PyQt6 imports so ``test_ui.py --pure`` can
run without Qt.  It owns:

* pure message builders (address + argument list) for every Stage 7 control;
* range/clamp helpers shared by the widgets and the tests;
* parsers for every Stage 7 status message;
* :class:`StatusStore`, a plain snapshot of the last known bridge/sclang state;
* :class:`OSCController`, which owns the two outgoing UDP clients and the
  background status listener.

Topology (see ``README_stage7.md``)::

    mutation controls -> bridge  (default 127.0.0.1:9000)
    autotune/loops/query -> sclang (default 127.0.0.1:57120)
    status listener    <- sclang + bridge (default 127.0.0.1:9001)

No socket is opened in the constructor; :meth:`OSCController.start` does that
explicitly.  Clients and the listener are injectable so tests never touch real
ports.
"""

from __future__ import annotations

import math
import sys
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from pythonosc.dispatcher import Dispatcher
from pythonosc.osc_server import ThreadingOSCUDPServer
from pythonosc.udp_client import SimpleUDPClient

# ---------------------------------------------------------------------------
# Addresses (Stage 7 contract)
# ---------------------------------------------------------------------------

MUTATION_TRIGGER = "/ji/mutation/trigger"
MUTATION_OCTAVE = "/ji/mutation/octave"
MUTATION_SET_RANDOMNESS = "/ji/mutation/set_randomness"
MUTATION_SET_DISSONANCE = "/ji/mutation/set_dissonance_bias"
MUTATION_SET_STEPWISE = "/ji/mutation/set_stepwise_bias"
MUTATION_SET_GLIDE = "/ji/mutation/set_glide_time"
MUTATION_SET_CHANGE_COUNT = "/ji/mutation/set_change_count"
MUTATION_SET_TEMPERATURE = "/ji/mutation/set_temperature"
MUTATION_SET_ANCHOR_MOBILITY = "/ji/mutation/set_anchor_mobility"

UI_HELLO = "/ji/ui/hello"
FREQSET_CHANGED = "/ji/freqset/changed"
UI_STATE = "/ji/ui/state"
MIDI_STATUS = "/ji/midi/status"
MIDI_STATE = "/ji/midi/state"
MIDI_SET_ENABLED = "/ji/midi/set_enabled"

AUTOTUNE_SET = "/ji/autotune/set"
AUTOTUNE_STATE = "/ji/autotune/state"

LOOP_RECORD = "/ji/loop/record"
LOOP_RECORD_LENGTH = "/ji/loop/record_length"
LOOP_STOP_RECORD = "/ji/loop/stop_record"
LOOP_PLAY = "/ji/loop/play"
LOOP_STOP = "/ji/loop/stop"
LOOP_STOP_ALL = "/ji/loop/stop_all"
LOOP_CLEAR = "/ji/loop/clear"
LOOP_SET_DEFAULT_LENGTH = "/ji/loop/set_default_length"
LOOP_STATE = "/ji/loop/state"

STATUS_QUERY = "/ji/status/query"
CPU = "/ji/cpu"

STATUS_ADDRESSES = (
    FREQSET_CHANGED,
    UI_STATE,
    MIDI_STATUS,
    MIDI_STATE,
    CPU,
    LOOP_STATE,
    AUTOTUNE_STATE,
)

# ---------------------------------------------------------------------------
# Ranges / defaults
# ---------------------------------------------------------------------------

MUTATION_DEFAULT_MAX_CHANGES = 3
LOOP_INDEX_MIN = 1
LOOP_INDEX_MAX = 4
LOOP_LENGTH_MIN = 1.0
LOOP_LENGTH_MAX = 30.0
LOOP_LENGTH_DEFAULT = 8.0
# The sclang control surface accepts a wider one-take length (0.1..60 s).
SC_LOOP_SECONDS_MIN = 0.1
SC_LOOP_SECONDS_MAX = 60.0

LOOP_STATES = ("empty", "recording", "recorded", "playing", "stopped")


@dataclass(frozen=True)
class ParamSpec:
    """One continuous/integer control: address, range, default, formatting."""

    key: str
    label: str
    address: str
    low: float
    high: float
    default: float
    integer: bool = False
    fmt: str = "{:.2f}"
    suffix: str = ""


MUTATION_PARAMS: tuple[ParamSpec, ...] = (
    ParamSpec(
        "randomness",
        "Randomness",
        MUTATION_SET_RANDOMNESS,
        0.0,
        1.0,
        0.55,
    ),
    ParamSpec(
        "dissonance_bias",
        "Dissonance",
        MUTATION_SET_DISSONANCE,
        -1.0,
        1.0,
        0.0,
        fmt="{:+.2f}",
    ),
    ParamSpec(
        "stepwise_bias",
        "Stepwise",
        MUTATION_SET_STEPWISE,
        0.0,
        1.0,
        0.25,
    ),
    ParamSpec(
        "temperature",
        "Temperature",
        MUTATION_SET_TEMPERATURE,
        0.0,
        1.0,
        0.0,
    ),
    ParamSpec(
        "anchor_mobility",
        "Anchor mobility",
        MUTATION_SET_ANCHOR_MOBILITY,
        0.0,
        1.0,
        0.25,
    ),
    ParamSpec(
        "change_count",
        "Changes",
        MUTATION_SET_CHANGE_COUNT,
        1.0,
        float(MUTATION_DEFAULT_MAX_CHANGES),
        1.0,
        integer=True,
        fmt="{:d}",
    ),
    ParamSpec(
        "glide_time",
        "Glide",
        MUTATION_SET_GLIDE,
        0.0,
        2.0,
        0.15,
        fmt="{:.3f}",
        suffix=" s",
    ),
)
MUTATION_PARAM_MAP: dict[str, ParamSpec] = {
    spec.key: spec for spec in MUTATION_PARAMS
}

AUTOTUNE_PARAMS: tuple[ParamSpec, ...] = (
    ParamSpec(
        "retuneTime",
        "Retune",
        AUTOTUNE_SET,
        0.001,
        2.0,
        0.05,
        fmt="{:.3f}",
        suffix=" s",
    ),
    ParamSpec("mix", "Mix", AUTOTUNE_SET, 0.0, 1.0, 1.0),
    ParamSpec(
        "windowSize",
        "Window",
        AUTOTUNE_SET,
        0.02,
        0.5,
        0.1,
        fmt="{:.3f}",
        suffix=" s",
    ),
    ParamSpec(
        "pitchRangeLimit",
        "Range",
        AUTOTUNE_SET,
        0.0,
        24.0,
        12.0,
        integer=True,
        fmt="{:d}",
        suffix=" st",
    ),
    ParamSpec("tartiniThreshold", "Threshold", AUTOTUNE_SET, 0.0, 1.0, 0.9),
    ParamSpec("inputGain", "Gain", AUTOTUNE_SET, 0.0, 2.0, 1.0),
)
AUTOTUNE_PARAM_MAP: dict[str, ParamSpec] = {
    spec.key: spec for spec in AUTOTUNE_PARAMS
}

AUTOTUNE_CHANNELS = (0, 1)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def clamp(value: float, low: float, high: float) -> float:
    """Return ``value`` limited to ``[low, high]`` (NaN-safe via validation)."""

    if not math.isfinite(value):
        raise ValueError("value must be finite")
    return max(low, min(high, value))


def clamp_int(value: float, low: int, high: int) -> int:
    """Round then clamp an integer control."""

    numeric = _as_finite_float(value, "value")
    return max(int(low), min(int(high), int(round(numeric))))


def _as_finite_float(value: Any, label: str) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        raise TypeError(f"{label} must be a real number") from None
    if not math.isfinite(numeric):
        raise ValueError(f"{label} must be finite")
    return numeric


def _validate_index(index: Any) -> int:
    numeric = _as_finite_float(index, "loop index")
    value = int(round(numeric))
    if not LOOP_INDEX_MIN <= value <= LOOP_INDEX_MAX:
        raise ValueError(
            f"loop index must be {LOOP_INDEX_MIN}..{LOOP_INDEX_MAX}, got {index!r}"
        )
    return value


def _validate_channel(channel: Any) -> int:
    numeric = _as_finite_float(channel, "autotune channel")
    value = int(round(numeric))
    if value not in AUTOTUNE_CHANNELS:
        raise ValueError("autotune channel must be 0 or 1")
    return value


# ---------------------------------------------------------------------------
# Pure message builders
# ---------------------------------------------------------------------------


def build_set_mutation(
    param: str, value: Any, max_changes: int = MUTATION_DEFAULT_MAX_CHANGES
) -> tuple[str, list[Any]]:
    """Build one mutation-control message, clamped to its UI range.

    ``change_count`` is clamped to ``1..max_changes``; the bridge clamps it
    again against its own ceiling.
    """

    key = str(param)
    spec = MUTATION_PARAM_MAP.get(key)
    if spec is None:
        raise ValueError(f"unknown mutation parameter {param!r}")
    if spec.integer:
        ceiling = max(1, int(max_changes))
        return spec.address, [clamp_int(value, 1, ceiling)]
    return spec.address, [clamp(_as_finite_float(value, key), spec.low, spec.high)]


def build_trigger_mutation() -> tuple[str, list[Any]]:
    return MUTATION_TRIGGER, []


def build_trigger_octave() -> tuple[str, list[Any]]:
    return MUTATION_OCTAVE, []


def build_set_autotune(
    channel: Any, param: str, value: Any
) -> tuple[str, list[Any]]:
    """Build ``/ji/autotune/set [channel, param, value]`` with clamping.

    ``param`` must be one of the six whitelisted Synth argument names; arbitrary
    strings are rejected here as well as in sclang.
    """

    channel_value = _validate_channel(channel)
    key = str(param)
    spec = AUTOTUNE_PARAM_MAP.get(key)
    if spec is None:
        raise ValueError(f"unknown autotune parameter {param!r}")
    numeric = _as_finite_float(value, key)
    clamped = clamp(numeric, spec.low, spec.high)
    if spec.integer:
        clamped = float(int(round(clamped)))
    return AUTOTUNE_SET, [channel_value, key, float(clamped)]


def build_loop_record(
    index: Any, seconds: float | None = None
) -> tuple[str, list[Any]]:
    """Build Record or Record-at-length.

    With ``seconds is None`` this uses the sclang default; with a value it uses
    ``/ji/loop/record_length`` so the take uses the length shown in the UI even
    if ``/ji/loop/set_default_length`` has not arrived yet.
    """

    loop_index = _validate_index(index)
    if seconds is None:
        return LOOP_RECORD, [loop_index]
    length = clamp(
        _as_finite_float(seconds, "loop seconds"), LOOP_LENGTH_MIN, LOOP_LENGTH_MAX
    )
    return LOOP_RECORD_LENGTH, [loop_index, length]


def build_loop_stop_record(index: Any) -> tuple[str, list[Any]]:
    return LOOP_STOP_RECORD, [_validate_index(index)]


def build_loop_play(index: Any) -> tuple[str, list[Any]]:
    return LOOP_PLAY, [_validate_index(index)]


def build_loop_stop(index: Any) -> tuple[str, list[Any]]:
    return LOOP_STOP, [_validate_index(index)]


def build_loop_clear(index: Any) -> tuple[str, list[Any]]:
    return LOOP_CLEAR, [_validate_index(index)]


def build_loop_stop_all() -> tuple[str, list[Any]]:
    return LOOP_STOP_ALL, []


def build_loop_set_default_length(seconds: Any) -> tuple[str, list[Any]]:
    length = clamp(
        _as_finite_float(seconds, "loop default length"),
        LOOP_LENGTH_MIN,
        LOOP_LENGTH_MAX,
    )
    return LOOP_SET_DEFAULT_LENGTH, [length]


def build_set_midi_enabled(
    drone: bool, arp: bool
) -> tuple[str, list[Any]]:
    """Build ``/ji/midi/set_enabled [int drone, int arp]``."""

    return MIDI_SET_ENABLED, [1 if drone else 0, 1 if arp else 0]


def build_request_state() -> tuple[str, list[Any]]:
    return STATUS_QUERY, []


def build_ui_hello() -> tuple[str, list[Any]]:
    return UI_HELLO, []


# ---------------------------------------------------------------------------
# Status state objects and parsers
# ---------------------------------------------------------------------------


@dataclass
class FrequencySet:
    """Last set reported by the bridge."""

    fundamental: float
    anchor: float | None
    ratios: list[float]

    @property
    def pairs(self) -> list[tuple[float, float]]:
        """``(ratio, Hz)`` sorted by pitch (ratio order)."""

        return sorted((ratio, ratio * self.fundamental) for ratio in self.ratios)


@dataclass
class BridgeControlState:
    """Current mutation-control values reported by the bridge."""

    randomness: float
    dissonance_bias: float
    stepwise_bias: float
    glide_time: float
    change_count: int
    temperature: float
    anchor_mobility: float
    max_changes: int


@dataclass
class MidiStatus:
    enabled: bool
    drone_port: str
    arp_port: str


@dataclass
class MidiToggleState:
    """Independent Drone/Arp switch state reported by the bridge."""

    drone_enabled: bool
    arp_enabled: bool


def parse_freqset(args: Sequence[Any]) -> FrequencySet:
    """Parse ``[fundamental, anchor, ratio_1 ... ratio_N]``.

    ``anchor < 0`` means "no anchor" and becomes ``None``.
    """

    if len(args) < 3:
        raise ValueError("freqset needs fundamental, anchor, and >=1 ratio")
    if len(args) > 2 + 12:
        raise ValueError("freqset has more than 12 ratios")

    fundamental = _as_finite_float(args[0], "fundamental")
    if fundamental <= 0.0:
        raise ValueError("fundamental must be > 0")
    anchor_value = _as_finite_float(args[1], "anchor")
    anchor = None if anchor_value < 0.0 else anchor_value
    ratios = [_as_finite_float(raw, "ratio") for raw in args[2:]]
    if any(ratio <= 0.0 for ratio in ratios):
        raise ValueError("every ratio must be > 0")
    return FrequencySet(fundamental=fundamental, anchor=anchor, ratios=ratios)


def parse_ui_state(args: Sequence[Any]) -> BridgeControlState:
    if len(args) != 8:
        raise ValueError(
            "ui state needs randomness, dissonance, stepwise, glide, "
            "change_count, temperature, anchor_mobility, max_changes"
        )
    randomness = clamp(_as_finite_float(args[0], "randomness"), 0.0, 1.0)
    dissonance = clamp(_as_finite_float(args[1], "dissonance_bias"), -1.0, 1.0)
    stepwise = clamp(_as_finite_float(args[2], "stepwise_bias"), 0.0, 1.0)
    glide = clamp(_as_finite_float(args[3], "glide_time"), 0.0, 2.0)
    max_changes = clamp_int(args[7], 1, 12)
    change_count = clamp_int(args[4], 1, max_changes)
    temperature = clamp(_as_finite_float(args[5], "temperature"), 0.0, 1.0)
    mobility = clamp(_as_finite_float(args[6], "anchor_mobility"), 0.0, 1.0)
    return BridgeControlState(
        randomness=randomness,
        dissonance_bias=dissonance,
        stepwise_bias=stepwise,
        glide_time=glide,
        change_count=change_count,
        temperature=temperature,
        anchor_mobility=mobility,
        max_changes=max_changes,
    )


def parse_midi_status(args: Sequence[Any]) -> MidiStatus:
    if len(args) != 3:
        raise ValueError("midi status needs enabled, drone_port, arp_port")
    try:
        enabled = int(round(_as_finite_float(args[0], "midi enabled")))
    except (TypeError, ValueError):
        raise ValueError("midi enabled must be 0 or 1") from None
    if enabled not in (0, 1):
        raise ValueError("midi enabled must be 0 or 1")
    drone_port = str(args[1]) if args[1] is not None else ""
    arp_port = str(args[2]) if args[2] is not None else ""
    if not enabled:
        # The contract sends empty strings when disabled; normalise anyway.
        drone_port = ""
        arp_port = ""
    return MidiStatus(bool(enabled), drone_port, arp_port)


def parse_midi_state(args: Sequence[Any]) -> MidiToggleState:
    """Parse ``[int drone_on, int arp_on]`` (0/1 each)."""

    if len(args) != 2:
        raise ValueError("midi state needs drone and arp 0/1 values")
    values = []
    for label, raw in zip(("drone", "arp"), args):
        try:
            number = int(round(_as_finite_float(raw, label)))
        except (TypeError, ValueError):
            raise ValueError(f"midi {label} must be 0 or 1") from None
        if number not in (0, 1):
            raise ValueError(f"midi {label} must be 0 or 1")
        values.append(bool(number))
    return MidiToggleState(drone_enabled=values[0], arp_enabled=values[1])


def parse_cpu(args: Sequence[Any]) -> float:
    if len(args) != 1:
        raise ValueError("cpu needs one percentage")
    return clamp(_as_finite_float(args[0], "cpu"), 0.0, 100.0)


def parse_loop_state(args: Sequence[Any]) -> tuple[int, str]:
    if len(args) != 2:
        raise ValueError("loop state needs index and state string")
    index = _validate_index(args[0])
    state = str(args[1])
    if state not in LOOP_STATES:
        raise ValueError(f"unknown loop state {state!r}")
    return index, state


def parse_autotune_state(
    args: Sequence[Any],
) -> tuple[int, dict[str, float]]:
    if len(args) != 1 + len(AUTOTUNE_PARAMS):
        raise ValueError(
            "autotune state needs channel plus "
            f"{len(AUTOTUNE_PARAMS)} parameter values"
        )
    channel = _validate_channel(args[0])
    values: dict[str, float] = {}
    for spec, raw in zip(AUTOTUNE_PARAMS, args[1:]):
        value = clamp(_as_finite_float(raw, spec.key), spec.low, spec.high)
        if spec.integer:
            value = float(int(round(value)))
        values[spec.key] = value
    return channel, values


STATUS_PARSERS: dict[str, Callable[[Sequence[Any]], Any]] = {
    FREQSET_CHANGED: parse_freqset,
    UI_STATE: parse_ui_state,
    MIDI_STATUS: parse_midi_status,
    MIDI_STATE: parse_midi_state,
    CPU: parse_cpu,
    LOOP_STATE: parse_loop_state,
    AUTOTUNE_STATE: parse_autotune_state,
}


def default_autotune_values() -> dict[str, float]:
    return {spec.key: float(spec.default) for spec in AUTOTUNE_PARAMS}


class StatusStore:
    """Plain snapshot of the last status messages (no Qt, no sockets)."""

    def __init__(self) -> None:
        self.freqset: FrequencySet | None = None
        self.control: BridgeControlState | None = None
        self.midi: MidiStatus | None = None
        self.midi_state: MidiToggleState | None = None
        self.cpu: float | None = None
        self.loop_states: dict[int, str] = {
            index: "waiting" for index in range(LOOP_INDEX_MIN, LOOP_INDEX_MAX + 1)
        }
        self.autotune: dict[int, dict[str, float]] = {
            channel: default_autotune_values()
            for channel in AUTOTUNE_CHANNELS
        }
        self.bridge_seen = False
        self.sclang_seen = False

    @property
    def waiting_for_bridge(self) -> bool:
        return not self.bridge_seen

    @property
    def waiting_for_sclang(self) -> bool:
        return not self.sclang_seen

    @property
    def max_changes(self) -> int:
        if self.control is not None:
            return self.control.max_changes
        return MUTATION_DEFAULT_MAX_CHANGES

    def apply(self, address: str, args: Sequence[Any]) -> bool:
        """Apply one status message.

        Returns ``True`` if the address is recognised.  Malformed payloads raise
        ``ValueError``/``TypeError`` so the caller can log and keep listening.
        """

        parser = STATUS_PARSERS.get(address)
        if parser is None:
            return False

        parsed = parser(args)
        if address == FREQSET_CHANGED:
            self.freqset = parsed
            self.bridge_seen = True
        elif address == UI_STATE:
            self.control = parsed
            self.bridge_seen = True
        elif address == MIDI_STATUS:
            self.midi = parsed
            self.bridge_seen = True
        elif address == MIDI_STATE:
            self.midi_state = parsed
            self.bridge_seen = True
        elif address == CPU:
            self.cpu = parsed
            self.sclang_seen = True
        elif address == LOOP_STATE:
            index, state = parsed
            self.loop_states[index] = state
            self.sclang_seen = True
        elif address == AUTOTUNE_STATE:
            channel, values = parsed
            self.autotune[channel] = values
            self.sclang_seen = True
        return True


# ---------------------------------------------------------------------------
# Listener / controller
# ---------------------------------------------------------------------------


class OSCStatusListener:
    """UDP listener for Stage 7 status messages, served in a daemon thread."""

    def __init__(
        self,
        host: str,
        port: int,
        handler: Callable[[str, list[Any]], None],
    ) -> None:
        self.host = str(host)
        self.port = int(port)
        self._handler = handler
        self._server: ThreadingOSCUDPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._server is not None:
            raise RuntimeError("OSCStatusListener is already running")
        dispatcher = Dispatcher()
        for address in STATUS_ADDRESSES:
            dispatcher.map(address, self._dispatch)
        try:
            server = ThreadingOSCUDPServer((self.host, self.port), dispatcher)
        except OSError as exc:
            raise RuntimeError(
                f"cannot listen for UI status on {self.host}:{self.port}: {exc}"
            ) from exc
        self._server = server
        self._thread = threading.Thread(
            target=server.serve_forever,
            name="ji-ui-osc",
            daemon=True,
        )
        self._thread.start()

    def _dispatch(self, address: str, *args: Any) -> None:
        try:
            self._handler(address, list(args))
        except Exception as exc:  # never let a bad message kill the listener
            print(f"[UI] status handler failed for {address}: {exc!r}", file=sys.stderr)

    def stop(self) -> None:
        server = self._server
        thread = self._thread
        self._server = None
        self._thread = None
        if server is None:
            return
        server.shutdown()
        server.server_close()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2.0)

    @property
    def is_running(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()


class OSCController:
    """Own the Stage 7 outgoing clients and the incoming status listener."""

    def __init__(
        self,
        bridge_host: str = "127.0.0.1",
        bridge_port: int = 9000,
        sc_host: str = "127.0.0.1",
        sc_port: int = 57120,
        ui_host: str = "127.0.0.1",
        ui_port: int = 9001,
        client_factory: Callable[[str, int], Any] = SimpleUDPClient,
        listener_factory: Callable[
            [str, int, Callable[[str, list[Any]], None]], Any
        ] = OSCStatusListener,
        bridge_client: Any | None = None,
        sc_client: Any | None = None,
        listener: Any | None = None,
        store: StatusStore | None = None,
        on_status: Callable[[str, list[Any]], None] | None = None,
    ) -> None:
        self.bridge_host = str(bridge_host)
        self.bridge_port = int(bridge_port)
        self.sc_host = str(sc_host)
        self.sc_port = int(sc_port)
        self.ui_host = str(ui_host)
        self.ui_port = int(ui_port)

        self._client_factory = client_factory
        self._listener_factory = listener_factory
        self._bridge_client = bridge_client
        self._sc_client = sc_client
        self._listener = listener
        self._created_bridge = False
        self._created_sc = False
        self._listener_active = False
        self._started = False

        self.store = store if store is not None else StatusStore()
        self._on_status = on_status
        self._lock = threading.RLock()

    # -- lifecycle -----------------------------------------------------

    @property
    def started(self) -> bool:
        return self._started

    @property
    def max_changes(self) -> int:
        return self.store.max_changes

    def start(self) -> None:
        """Bind the status listener first, then announce to bridge/sclang."""

        with self._lock:
            if self._started:
                return
            if self._listener is None:
                self._listener = self._listener_factory(
                    self.ui_host, self.ui_port, self._handle_status
                )
            self._listener.start()
            self._listener_active = True
            try:
                if self._bridge_client is None:
                    self._bridge_client = self._client_factory(
                        self.bridge_host, self.bridge_port
                    )
                    self._created_bridge = True
                if self._sc_client is None:
                    self._sc_client = self._client_factory(
                        self.sc_host, self.sc_port
                    )
                    self._created_sc = True
            except Exception:
                self._stop_listener()
                raise
            self._started = True

        # Listener is up before these sends, so replies cannot race it.  A
        # missing bridge/sclang is not fatal: the UI stays open and waits.
        for announce in (self.hello, self.request_state):
            try:
                announce()
            except (OSError, RuntimeError, ValueError) as exc:
                print(f"[UI] startup announce failed: {exc}", file=sys.stderr)

    def stop(self) -> None:
        """Stop the status listener; the bridge, audio, loops, and MIDI stay up."""

        with self._lock:
            self._started = False
            self._stop_listener()
            for client, created in (
                (self._bridge_client, self._created_bridge),
                (self._sc_client, self._created_sc),
            ):
                if not created:
                    continue
                close = getattr(client, "close", None)
                if callable(close):
                    try:
                        close()
                    except Exception:
                        pass
            if self._created_bridge:
                self._bridge_client = None
                self._created_bridge = False
            if self._created_sc:
                self._sc_client = None
                self._created_sc = False

    def _stop_listener(self) -> None:
        listener = self._listener
        if listener is not None and self._listener_active:
            listener.stop()
            self._listener_active = False

    def set_status_callback(
        self, callback: Callable[[str, list[Any]], None] | None
    ) -> None:
        """Install/replace the status callback (used by the Qt window)."""

        self._on_status = callback

    # -- status callback ----------------------------------------------

    def _handle_status(self, address: str, args: list[Any]) -> None:
        try:
            recognised = self.store.apply(address, args)
        except (TypeError, ValueError) as exc:
            print(f"[UI] ignore {address}: {exc}", file=sys.stderr)
            return
        if not recognised:
            return
        callback = self._on_status
        if callback is not None:
            try:
                callback(address, list(args))
            except Exception as exc:  # Qt callback errors must not kill the thread
                print(f"[UI] status callback failed: {exc!r}", file=sys.stderr)

    # -- low-level sends ----------------------------------------------

    def _send_bridge(self, address: str, args: list[Any]) -> None:
        self._require_started()
        if self._bridge_client is None:
            raise RuntimeError("bridge client is not available")
        self._bridge_client.send_message(address, args)

    def _send_sc(self, address: str, args: list[Any]) -> None:
        self._require_started()
        if self._sc_client is None:
            raise RuntimeError("sclang client is not available")
        self._sc_client.send_message(address, args)

    def _require_started(self) -> None:
        if not self._started:
            raise RuntimeError("OSCController.start() has not been called")

    # -- control API ---------------------------------------------------

    def set_mutation(self, param: str, value: Any) -> tuple[str, list[Any]]:
        message = build_set_mutation(param, value, self.max_changes)
        self._send_bridge(*message)
        return message

    def trigger_mutation(self) -> tuple[str, list[Any]]:
        message = build_trigger_mutation()
        self._send_bridge(*message)
        return message

    def trigger_octave(self) -> tuple[str, list[Any]]:
        message = build_trigger_octave()
        self._send_bridge(*message)
        return message

    def set_autotune(
        self, channel: Any, param: str, value: Any
    ) -> tuple[str, list[Any]]:
        message = build_set_autotune(channel, param, value)
        self._send_sc(*message)
        return message

    def set_midi_enabled(
        self, drone: bool, arp: bool
    ) -> tuple[str, list[Any]]:
        message = build_set_midi_enabled(drone, arp)
        self._send_bridge(*message)
        return message

    def loop_record(
        self, index: Any, seconds: float | None = None
    ) -> tuple[str, list[Any]]:
        message = build_loop_record(index, seconds)
        self._send_sc(*message)
        return message

    def loop_stop_record(self, index: Any) -> tuple[str, list[Any]]:
        message = build_loop_stop_record(index)
        self._send_sc(*message)
        return message

    def loop_play(self, index: Any) -> tuple[str, list[Any]]:
        message = build_loop_play(index)
        self._send_sc(*message)
        return message

    def loop_stop(self, index: Any) -> tuple[str, list[Any]]:
        message = build_loop_stop(index)
        self._send_sc(*message)
        return message

    def loop_clear(self, index: Any) -> tuple[str, list[Any]]:
        message = build_loop_clear(index)
        self._send_sc(*message)
        return message

    def loop_stop_all(self) -> tuple[str, list[Any]]:
        message = build_loop_stop_all()
        self._send_sc(*message)
        return message

    def loop_set_default_length(self, seconds: Any) -> tuple[str, list[Any]]:
        message = build_loop_set_default_length(seconds)
        self._send_sc(*message)
        return message

    def hello(self) -> tuple[str, list[Any]]:
        message = build_ui_hello()
        self._send_bridge(*message)
        return message

    def request_state(self) -> tuple[str, list[Any]]:
        message = build_request_state()
        self._send_sc(*message)
        return message


__all__ = [
    "AUTOTUNE_PARAMS",
    "AUTOTUNE_PARAM_MAP",
    "BridgeControlState",
    "FrequencySet",
    "LOOP_LENGTH_DEFAULT",
    "LOOP_LENGTH_MAX",
    "LOOP_LENGTH_MIN",
    "LOOP_STATES",
    "MUTATION_PARAMS",
    "MUTATION_PARAM_MAP",
    "MidiStatus",
    "MidiToggleState",
    "OSCController",
    "OSCStatusListener",
    "ParamSpec",
    "StatusStore",
    "build_loop_clear",
    "build_loop_play",
    "build_loop_record",
    "build_loop_set_default_length",
    "build_loop_stop",
    "build_loop_stop_all",
    "build_loop_stop_record",
    "build_request_state",
    "build_set_autotune",
    "build_set_midi_enabled",
    "build_set_mutation",
    "build_trigger_mutation",
    "build_trigger_octave",
    "build_ui_hello",
    "clamp",
    "clamp_int",
    "parse_autotune_state",
    "parse_cpu",
    "parse_freqset",
    "parse_loop_state",
    "parse_midi_status",
    "parse_midi_state",
    "parse_ui_state",
]

"""Minimal OSC control surface for the decentralised mutation engine.

No audio and no scsynth calls live here.  Addresses:

``/ji/mutation/trigger``
    Propose and apply one add/remove/replace.
``/ji/mutation/octave``
    Propose and apply one octave/register move.
``/ji/mutation/set_randomness`` (float)
``/ji/mutation/set_dissonance_bias`` (float)
``/ji/mutation/set_stepwise_bias`` (float)
``/ji/mutation/set_glide_time`` (float)
``/ji/mutation/set_change_count`` (int)
``/ji/mutation/set_temperature`` (float)
``/ji/mutation/set_anchor_mobility`` (float)
    Update a parameter.
``/ji/ui/hello``
    Stage 7 UI handshake.  Calls the optional ``on_ui_hello`` callback after the
    server lock has been released, so the bridge can reply with a status
    snapshot without holding the mutation lock.
``/ji/midi/set_enabled`` (int drone 0/1, int arp 0/1)
    Stage 7.1 UI switches for the two MIDI buses.  Calls the optional
    ``on_midi_enabled`` callback; it never touches audio.

The server runs in a daemon thread so ``start()`` returns immediately.  Stage 3
attaches an outgoing bridge through the optional ``on_event`` / ``on_octave``
callbacks; this module still sends no audio OSC itself.
"""

from __future__ import annotations

import math
import sys
import threading
from typing import Any, Callable

from pythonosc.dispatcher import Dispatcher
from pythonosc.osc_server import ThreadingOSCUDPServer

try:  # Support both ``python -m control.osc_server`` and direct execution.
    from . import rhythm
    from .mutation import MutationBatch, MutationEngine, MutationEvent, OctaveEvent
except ImportError:  # pragma: no cover - exercised only by direct execution
    import rhythm  # type: ignore[no-redef]
    from mutation import (  # type: ignore[no-redef]
        MutationBatch,
        MutationEngine,
        MutationEvent,
        OctaveEvent,
    )

# Stage 7 UI handshake.  Kept as a module constant so callers can import it
# without constructing a server.
UI_HELLO_ADDRESS = "/ji/ui/hello"
# Stage 7.1: independent drone/arp bus switches from the UI.
MIDI_SET_ENABLED_ADDRESS = "/ji/midi/set_enabled"
# Stage 8.1 rhythm/transport and drum controls (UI -> Bridge).
RHYTHM_SET_TEMPO_ADDRESS = "/ji/rhythm/set_tempo"
RHYTHM_SET_METER_ADDRESS = "/ji/rhythm/set_meter"
RHYTHM_TRANSPORT_ADDRESS = "/ji/rhythm/transport"
RHYTHM_REQUEST_ADDRESS = "/ji/rhythm/request"
DRUMS_SET_ENABLED_ADDRESS = "/ji/drums/set_enabled"
DRUMS_SET_LANE_ADDRESS = "/ji/drums/set_lane"
DRUMS_SET_RATE_ADDRESS = "/ji/drums/set_rate"


class MutationOSCServer:
    """Listen on UDP and edit a :class:`MutationEngine` from OSC messages."""

    TRIGGER_ADDRESS = "/ji/mutation/trigger"
    OCTAVE_ADDRESS = "/ji/mutation/octave"
    RANDOMNESS_ADDRESS = "/ji/mutation/set_randomness"
    DISSONANCE_ADDRESS = "/ji/mutation/set_dissonance_bias"
    STEPWISE_ADDRESS = "/ji/mutation/set_stepwise_bias"
    GLIDE_ADDRESS = "/ji/mutation/set_glide_time"
    CHANGE_COUNT_ADDRESS = "/ji/mutation/set_change_count"
    TEMPERATURE_ADDRESS = "/ji/mutation/set_temperature"
    ANCHOR_MOBILITY_ADDRESS = "/ji/mutation/set_anchor_mobility"
    UI_HELLO_ADDRESS = UI_HELLO_ADDRESS
    MIDI_SET_ENABLED_ADDRESS = MIDI_SET_ENABLED_ADDRESS
    RHYTHM_SET_TEMPO_ADDRESS = RHYTHM_SET_TEMPO_ADDRESS
    RHYTHM_SET_METER_ADDRESS = RHYTHM_SET_METER_ADDRESS
    RHYTHM_TRANSPORT_ADDRESS = RHYTHM_TRANSPORT_ADDRESS
    RHYTHM_REQUEST_ADDRESS = RHYTHM_REQUEST_ADDRESS
    DRUMS_SET_ENABLED_ADDRESS = DRUMS_SET_ENABLED_ADDRESS
    DRUMS_SET_LANE_ADDRESS = DRUMS_SET_LANE_ADDRESS
    DRUMS_SET_RATE_ADDRESS = DRUMS_SET_RATE_ADDRESS

    def __init__(
        self,
        engine: MutationEngine,
        listen_host: str = "127.0.0.1",
        listen_port: int = 9000,
        on_event: Callable[[MutationEvent | MutationBatch], None] | None = None,
        on_octave: Callable[[OctaveEvent], None] | None = None,
        on_ui_hello: Callable[[], None] | None = None,
        on_midi_enabled: Callable[[bool, bool], None] | None = None,
        on_rhythm_tempo: Callable[[float], None] | None = None,
        on_rhythm_meter: Callable[[int, int], None] | None = None,
        on_rhythm_transport: Callable[[bool], None] | None = None,
        on_drums_enabled: Callable[[bool], None] | None = None,
        on_drums_lane: Callable[[int, bool], None] | None = None,
        on_drums_rate: Callable[[float], None] | None = None,
        on_rhythm_request: Callable[[], None] | None = None,
    ) -> None:
        if not isinstance(engine, MutationEngine):
            raise TypeError("engine must be a MutationEngine")
        if isinstance(listen_port, bool) or not isinstance(listen_port, int):
            raise TypeError("listen_port must be an integer")
        if not 1 <= listen_port <= 65535:
            raise ValueError("listen_port must be between 1 and 65535")
        for label, callback in (
            ("on_event", on_event),
            ("on_octave", on_octave),
            ("on_ui_hello", on_ui_hello),
            ("on_midi_enabled", on_midi_enabled),
            ("on_rhythm_tempo", on_rhythm_tempo),
            ("on_rhythm_meter", on_rhythm_meter),
            ("on_rhythm_transport", on_rhythm_transport),
            ("on_drums_enabled", on_drums_enabled),
            ("on_drums_lane", on_drums_lane),
            ("on_drums_rate", on_drums_rate),
            ("on_rhythm_request", on_rhythm_request),
        ):
            if callback is not None and not callable(callback):
                raise TypeError(f"{label} must be callable or None")

        self._engine = engine
        self.listen_host = str(listen_host)
        self.listen_port = listen_port
        self._on_event = on_event
        self._on_octave = on_octave
        self._on_ui_hello = on_ui_hello
        self._on_midi_enabled = on_midi_enabled
        self._on_rhythm_tempo = on_rhythm_tempo
        self._on_rhythm_meter = on_rhythm_meter
        self._on_rhythm_transport = on_rhythm_transport
        self._on_drums_enabled = on_drums_enabled
        self._on_drums_lane = on_drums_lane
        self._on_drums_rate = on_drums_rate
        self._on_rhythm_request = on_rhythm_request

        self._lock = threading.RLock()
        # Serializes apply -> print -> callback so a threaded OSC server cannot
        # reorder outgoing set updates.  This is separate from ``_lock`` so the
        # engine/parameter lock is released before a callback runs.
        self._event_lock = threading.RLock()
        self._randomness = 0.55
        self._dissonance_bias = 0.0
        self._stepwise_bias = 0.25
        self._glide_time = 0.0
        self._change_count = 1
        self._temperature = 0.0

        self._server: ThreadingOSCUDPServer | None = None
        self._thread: threading.Thread | None = None

        dispatcher = Dispatcher()
        dispatcher.map(self.TRIGGER_ADDRESS, self._handle_trigger)
        dispatcher.map(self.OCTAVE_ADDRESS, self._handle_octave)
        dispatcher.map(self.RANDOMNESS_ADDRESS, self._handle_set_randomness)
        dispatcher.map(self.DISSONANCE_ADDRESS, self._handle_set_dissonance_bias)
        dispatcher.map(self.STEPWISE_ADDRESS, self._handle_set_stepwise_bias)
        dispatcher.map(self.GLIDE_ADDRESS, self._handle_set_glide_time)
        dispatcher.map(self.CHANGE_COUNT_ADDRESS, self._handle_set_change_count)
        dispatcher.map(self.TEMPERATURE_ADDRESS, self._handle_set_temperature)
        dispatcher.map(
            self.ANCHOR_MOBILITY_ADDRESS, self._handle_set_anchor_mobility
        )
        dispatcher.map(self.UI_HELLO_ADDRESS, self._handle_ui_hello)
        dispatcher.map(
            self.MIDI_SET_ENABLED_ADDRESS, self._handle_midi_enabled
        )
        dispatcher.map(self.RHYTHM_SET_TEMPO_ADDRESS, self._handle_set_tempo)
        dispatcher.map(self.RHYTHM_SET_METER_ADDRESS, self._handle_set_meter)
        dispatcher.map(self.RHYTHM_TRANSPORT_ADDRESS, self._handle_transport)
        dispatcher.map(self.RHYTHM_REQUEST_ADDRESS, self._handle_rhythm_request)
        dispatcher.map(self.DRUMS_SET_ENABLED_ADDRESS, self._handle_drums_enabled)
        dispatcher.map(self.DRUMS_SET_LANE_ADDRESS, self._handle_drums_lane)
        dispatcher.map(self.DRUMS_SET_RATE_ADDRESS, self._handle_drums_rate)
        self._dispatcher = dispatcher

    # ------------------------------------------------------------------
    # Stored parameters
    # ------------------------------------------------------------------

    @property
    def randomness(self) -> float:
        with self._lock:
            return self._randomness

    @property
    def dissonance_bias(self) -> float:
        with self._lock:
            return self._dissonance_bias

    @property
    def stepwise_bias(self) -> float:
        with self._lock:
            return self._stepwise_bias

    @property
    def glide_time(self) -> float:
        with self._lock:
            return self._glide_time

    @property
    def change_count(self) -> int:
        with self._lock:
            return self._change_count

    @property
    def temperature(self) -> float:
        with self._lock:
            return self._temperature

    @property
    def anchor_mobility(self) -> float:
        with self._lock:
            return self._engine.anchor_mobility

    @property
    def max_changes(self) -> int:
        return self._engine.max_changes

    # ------------------------------------------------------------------
    # Public event API (Stage 3)
    # ------------------------------------------------------------------

    def trigger(self) -> MutationEvent | MutationBatch | None:
        """Apply one mutation trigger.

        With ``change_count == 1`` this returns a single ``MutationEvent`` and
        preserves the Stage 2/3 callback shape.  Above 1 it applies a
        ``MutationBatch`` atomically and returns that batch.  The configured
        ``on_event`` callback runs after application and lock release.
        """

        event: MutationEvent | None = None
        batch: MutationBatch | None = None
        with self._event_lock:
            with self._lock:
                try:
                    if self._change_count == 1:
                        event = self._engine.propose_mutation(
                            randomness=self._randomness,
                            dissonance_bias=self._dissonance_bias,
                            stepwise_bias=self._stepwise_bias,
                            glide_time=self._glide_time,
                            temperature=self._temperature,
                        )
                        self._engine.apply(event)
                        batch = None
                    else:
                        batch = self._engine.mutate_many(
                            self._change_count,
                            randomness=self._randomness,
                            dissonance_bias=self._dissonance_bias,
                            stepwise_bias=self._stepwise_bias,
                            glide_time=self._glide_time,
                            temperature=self._temperature,
                        )
                        event = None
                except (RuntimeError, ValueError, TypeError) as exc:
                    _warn(f"mutation trigger failed: {exc}")
                    return None

            if batch is not None:
                _print_batch(batch, self._engine)
                self._notify(self._on_event, batch, "mutation batch")
                return batch

            if event is None:  # defensive; both branches above assign one
                return None
            _print_mutation(event, self._engine)
            self._notify(self._on_event, event, "mutation")
            return event

    def octave(self) -> OctaveEvent | None:
        """Propose and apply one octave/register move.

        Returns the applied event, or ``None`` if no move is possible.  The
        configured ``on_octave`` callback runs after the event has been applied
        and after the internal lock is released.
        """

        with self._event_lock:
            with self._lock:
                try:
                    event = self._engine.propose_octave_motion(
                        glide_time=self._glide_time
                    )
                    self._engine.apply_octave_motion(event)
                except (RuntimeError, ValueError, TypeError) as exc:
                    _warn(f"octave trigger failed: {exc}")
                    return None

            _print_octave(event, self._engine)
            self._notify(self._on_octave, event, "octave")
            return event

    def set_glide_time(self, seconds: float) -> None:
        """Set the glide hint used by the next mutation/octave event.

        Negative values are clamped to zero, matching OSC and Stage 2
        behaviour.  Non-finite values are rejected.
        """

        try:
            value = float(seconds)
        except (TypeError, ValueError):
            raise TypeError("glide time must be a real number") from None
        if not math.isfinite(value):
            raise ValueError("glide time must be finite")

        with self._lock:
            self._glide_time = max(0.0, value)
            print(f"[OSC] glide_time = {self._glide_time:.3f}s")

    def set_change_count(self, count: int) -> None:
        """Set how many class edits one trigger applies (1..max_changes)."""

        try:
            numeric = float(count)
        except (TypeError, ValueError):
            raise TypeError("change_count must be a real number") from None
        if not math.isfinite(numeric):
            raise ValueError("change_count must be finite")

        value = max(1, min(self._engine.max_changes, int(round(numeric))))
        with self._lock:
            self._change_count = value
            print(f"[OSC] change_count = {self._change_count}")

    def set_temperature(self, value: float) -> None:
        """Set the mutation temperature (0..1)."""

        try:
            numeric = float(value)
        except (TypeError, ValueError):
            raise TypeError("temperature must be a real number") from None
        if not math.isfinite(numeric):
            raise ValueError("temperature must be finite")

        with self._lock:
            self._temperature = max(0.0, min(1.0, numeric))
            print(f"[OSC] temperature = {self._temperature:.3f}")

    def set_anchor_mobility(self, value: float) -> None:
        """Set anchor mobility (0 = fixed, 1 = no precedence)."""

        try:
            numeric = float(value)
        except (TypeError, ValueError):
            raise TypeError("anchor_mobility must be a real number") from None
        if not math.isfinite(numeric):
            raise ValueError("anchor_mobility must be finite")

        mobility = max(0.0, min(1.0, numeric))
        with self._lock:
            self._engine.set_anchor_mobility(mobility)
            print(f"[OSC] anchor_mobility = {self._engine.anchor_mobility:.3f}")

    @staticmethod
    def _notify(
        callback: Callable[[Any], None] | None,
        event: Any,
        label: str,
    ) -> None:
        """Run a bridge callback without letting it kill the server thread."""

        if callback is None:
            return
        try:
            callback(event)
        except Exception as exc:  # deliberately broad: bridge errors are soft
            _warn(f"{label} callback failed: {exc!r}")

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Bind the UDP socket and serve in a daemon thread."""

        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                raise RuntimeError("MutationOSCServer is already running")

            server = ThreadingOSCUDPServer(
                (self.listen_host, self.listen_port), self._dispatcher
            )
            self._server = server
            self._thread = threading.Thread(
                target=server.serve_forever,
                name="ji-mutation-osc",
                daemon=True,
            )
            self._thread.start()

        host, port = self._server.server_address[:2]
        print(f"[OSC] MutationOSCServer listening on {host}:{port}")

    def stop(self) -> None:
        """Stop the server and close its socket.  Safe when already stopped."""

        with self._lock:
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
        print("[OSC] MutationOSCServer stopped")

    @property
    def is_running(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    # ------------------------------------------------------------------
    # OSC handlers
    # ------------------------------------------------------------------

    def _handle_trigger(self, _address: str, *args: Any) -> None:
        if args:
            _warn(f"{self.TRIGGER_ADDRESS} takes no arguments; ignoring extras")
        self.trigger()

    def _handle_octave(self, _address: str, *args: Any) -> None:
        if args:
            _warn(f"{self.OCTAVE_ADDRESS} takes no arguments; ignoring extras")
        self.octave()

    def _handle_set_randomness(self, _address: str, *args: Any) -> None:
        value = _one_float(args, self.RANDOMNESS_ADDRESS)
        if value is None:
            return
        with self._lock:
            self._randomness = _clamp(value, 0.0, 1.0)
            print(f"[OSC] randomness = {self._randomness:.3f}")

    def _handle_set_dissonance_bias(self, _address: str, *args: Any) -> None:
        value = _one_float(args, self.DISSONANCE_ADDRESS)
        if value is None:
            return
        with self._lock:
            self._dissonance_bias = _clamp(value, -1.0, 1.0)
            print(f"[OSC] dissonance_bias = {self._dissonance_bias:+.3f}")

    def _handle_set_stepwise_bias(self, _address: str, *args: Any) -> None:
        value = _one_float(args, self.STEPWISE_ADDRESS)
        if value is None:
            return
        with self._lock:
            self._stepwise_bias = _clamp(value, 0.0, 1.0)
            print(f"[OSC] stepwise_bias = {self._stepwise_bias:.3f}")

    def _handle_set_glide_time(self, _address: str, *args: Any) -> None:
        value = _one_float(args, self.GLIDE_ADDRESS)
        if value is None:
            return
        self.set_glide_time(value)

    def _handle_set_change_count(self, _address: str, *args: Any) -> None:
        value = _one_float(args, self.CHANGE_COUNT_ADDRESS)
        if value is None:
            return
        self.set_change_count(value)

    def _handle_set_temperature(self, _address: str, *args: Any) -> None:
        value = _one_float(args, self.TEMPERATURE_ADDRESS)
        if value is None:
            return
        self.set_temperature(value)

    def _handle_set_anchor_mobility(self, _address: str, *args: Any) -> None:
        value = _one_float(args, self.ANCHOR_MOBILITY_ADDRESS)
        if value is None:
            return
        self.set_anchor_mobility(value)

    def _handle_ui_hello(self, _address: str, *args: Any) -> None:
        """Stage 7 handshake: reply with status after releasing the lock.

        Handlers run outside ``self._lock``, and the callback is a plain
        snapshot send, so this must not take the parameter lock.  A failing
        bridge callback is logged and never kills the OSC thread.
        """

        if args:
            _warn(f"{self.UI_HELLO_ADDRESS} takes no arguments; ignoring extras")
        callback = self._on_ui_hello
        if callback is None:
            return
        try:
            callback()
        except Exception as exc:  # deliberately broad: UI status is soft
            _warn(f"ui hello callback failed: {exc!r}")

    def _handle_midi_enabled(self, _address: str, *args: Any) -> None:
        """Stage 7.1: ``[int drone, int arp]`` switches the two MIDI buses."""

        if len(args) != 2:
            _warn(
                f"{self.MIDI_SET_ENABLED_ADDRESS} expects drone and arp "
                f"0/1 values; got {len(args)} arguments"
            )
            return
        try:
            drone = _one_bool(args[0], "drone")
            arp = _one_bool(args[1], "arp")
        except (TypeError, ValueError) as exc:
            _warn(f"{self.MIDI_SET_ENABLED_ADDRESS}: {exc}")
            return
        callback = self._on_midi_enabled
        if callback is None:
            return
        try:
            callback(drone, arp)
        except Exception as exc:  # MIDI/UI errors must not kill the OSC thread
            _warn(f"midi enabled callback failed: {exc!r}")

    # -- Stage 8.1 rhythm / drums ---------------------------------------

    def _handle_set_tempo(self, _address: str, *args: Any) -> None:
        value = _one_float(args, self.RHYTHM_SET_TEMPO_ADDRESS)
        if value is None:
            return
        try:
            bpm = rhythm.clamp_bpm(value)
        except (TypeError, ValueError) as exc:
            _warn(f"{self.RHYTHM_SET_TEMPO_ADDRESS}: {exc}")
            return
        if not math.isclose(bpm, value, rel_tol=0.0, abs_tol=1e-9):
            _warn(
                f"{self.RHYTHM_SET_TEMPO_ADDRESS}: {value:.3f} clamped to {bpm:.3f}"
            )
        self._call_callback(self._on_rhythm_tempo, "rhythm tempo", bpm)

    def _handle_set_meter(self, _address: str, *args: Any) -> None:
        if len(args) != 2:
            _warn(
                f"{self.RHYTHM_SET_METER_ADDRESS} expects numerator and "
                f"denominator; got {len(args)} arguments"
            )
            return
        try:
            numerator = rhythm.clamp_meter_numerator(args[0])
            denominator = rhythm.validate_meter_denominator(args[1])
        except (TypeError, ValueError) as exc:
            _warn(f"{self.RHYTHM_SET_METER_ADDRESS}: {exc}")
            return
        self._call_callback(
            self._on_rhythm_meter, "rhythm meter", numerator, denominator
        )

    def _handle_transport(self, _address: str, *args: Any) -> None:
        if len(args) != 1:
            _warn(
                f"{self.RHYTHM_TRANSPORT_ADDRESS} expects one 0/1 value; "
                f"got {len(args)} arguments"
            )
            return
        try:
            running = _one_bool(args[0], "transport")
        except (TypeError, ValueError) as exc:
            _warn(f"{self.RHYTHM_TRANSPORT_ADDRESS}: {exc}")
            return
        self._call_callback(
            self._on_rhythm_transport, "rhythm transport", running
        )

    def _handle_rhythm_request(self, _address: str, *args: Any) -> None:
        """sclang/UI asks the bridge to re-broadcast the canonical tempo."""

        if args:
            _warn(f"{self.RHYTHM_REQUEST_ADDRESS} takes no arguments; ignoring extras")
        self._call_callback(self._on_rhythm_request, "rhythm request")

    def _handle_drums_enabled(self, _address: str, *args: Any) -> None:
        if len(args) != 1:
            _warn(
                f"{self.DRUMS_SET_ENABLED_ADDRESS} expects one 0/1 value; "
                f"got {len(args)} arguments"
            )
            return
        try:
            enabled = _one_bool(args[0], "drums enabled")
        except (TypeError, ValueError) as exc:
            _warn(f"{self.DRUMS_SET_ENABLED_ADDRESS}: {exc}")
            return
        self._call_callback(self._on_drums_enabled, "drums enabled", enabled)

    def _handle_drums_lane(self, _address: str, *args: Any) -> None:
        if len(args) != 2:
            _warn(
                f"{self.DRUMS_SET_LANE_ADDRESS} expects a MIDI note and a "
                f"0/1 value; got {len(args)} arguments"
            )
            return
        try:
            note = _one_int(args[0], "drum note")
            if not 0 <= note <= 127:
                raise ValueError("drum note must be 0..127")
            enabled = _one_bool(args[1], "lane enabled")
        except (TypeError, ValueError) as exc:
            _warn(f"{self.DRUMS_SET_LANE_ADDRESS}: {exc}")
            return
        self._call_callback(
            self._on_drums_lane, "drums lane", note, enabled
        )

    def _handle_drums_rate(self, _address: str, *args: Any) -> None:
        value = _one_float(args, self.DRUMS_SET_RATE_ADDRESS)
        if value is None:
            return
        try:
            rate = rhythm.validate_drum_rate(value)
        except (TypeError, ValueError) as exc:
            _warn(f"{self.DRUMS_SET_RATE_ADDRESS}: {exc}")
            return
        self._call_callback(self._on_drums_rate, "drums rate", rate)

    @staticmethod
    def _call_callback(
        callback: Callable[..., None] | None, label: str, *args: Any
    ) -> None:
        if callback is None:
            return
        try:
            callback(*args)
        except Exception as exc:  # bridge/MIDI errors must not kill the thread
            _warn(f"{label} callback failed: {exc!r}")


def _one_int(value: Any, label: str) -> int:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        raise TypeError(f"{label} must be a real number") from None
    if not math.isfinite(numeric):
        raise ValueError(f"{label} must be finite")
    return int(round(numeric))


def _one_bool(value: Any, label: str) -> bool:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        raise TypeError(f"{label} must be 0 or 1") from None
    if not math.isfinite(numeric):
        raise ValueError(f"{label} must be 0 or 1")
    rounded = int(round(numeric))
    if rounded not in (0, 1):
        raise ValueError(f"{label} must be 0 or 1")
    return bool(rounded)


def _one_float(args: tuple[Any, ...], address: str) -> float | None:
    if len(args) != 1:
        _warn(f"{address} expects exactly one float; got {len(args)} arguments")
        return None
    try:
        value = float(args[0])
    except (TypeError, ValueError):
        _warn(f"{address} expects a float; got {args[0]!r}")
        return None
    if not math.isfinite(value):
        _warn(f"{address} expects a finite float; got {args[0]!r}")
        return None
    return value


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _print_mutation(event: MutationEvent, engine: MutationEngine) -> None:
    removed = "none" if event.removed_ratio is None else f"{event.removed_ratio:.6f}"
    new = "none" if event.new_ratio is None else f"{event.new_ratio:.6f}"
    print(
        "[MUTATION] "
        f"removed={removed} new={new} "
        f"octave={event.new_octave if event.new_octave is not None else '-'} "
        f"glide={event.glide_time:.3f}s reason={event.reason} "
        f"size={len(engine.current_ratios)} "
        f"anchor={engine.anchor_ratio if engine.anchor_ratio is not None else '-'} "
        f"score={engine.roughness_score():.6f}"
    )


def _print_batch(batch: MutationBatch, engine: MutationEngine) -> None:
    print(
        "[MUTATION] "
        f"batch changes={batch.count} glide={batch.glide_time:.3f}s "
        f"size={len(engine.current_ratios)} "
        f"anchor={engine.anchor_ratio if engine.anchor_ratio is not None else '-'}"
    )
    for event in batch.events:
        _print_mutation(event, engine)


def _print_octave(event: OctaveEvent, engine: MutationEngine) -> None:
    print(
        "[OCTAVE] "
        f"ratio={event.ratio:.6f} {event.old_octave:+d}->{event.new_octave:+d} "
        f"glide={event.glide_time:.3f}s reason={event.reason} "
        f"size={len(engine.current_ratios)} "
        f"score={engine.roughness_score():.6f}"
    )


def _warn(message: str) -> None:
    print(f"[OSC] {message}", file=sys.stderr)


if __name__ == "__main__":  # pragma: no cover - manual smoke test
    _engine = MutationEngine(
        [1.0, 9.0 / 8.0, 5.0 / 4.0, 4.0 / 3.0, 3.0 / 2.0, 5.0 / 3.0, 15.0 / 8.0]
    )
    _server = MutationOSCServer(_engine)
    _server.start()
    print("Press Ctrl+C to stop.")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        _server.stop()

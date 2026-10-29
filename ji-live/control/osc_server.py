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
    Update a parameter.

The server runs in a daemon thread so ``start()`` returns immediately.  Stage 3
will attach the outgoing scsynth/autotune bridge.
"""

from __future__ import annotations

import math
import sys
import threading
from typing import Any

from pythonosc.dispatcher import Dispatcher
from pythonosc.osc_server import ThreadingOSCUDPServer

try:  # Support both ``python -m control.osc_server`` and direct execution.
    from .mutation import MutationEngine, MutationEvent, OctaveEvent
except ImportError:  # pragma: no cover - exercised only by direct execution
    from mutation import MutationEngine, MutationEvent, OctaveEvent


class MutationOSCServer:
    """Listen on UDP and edit a :class:`MutationEngine` from OSC messages."""

    TRIGGER_ADDRESS = "/ji/mutation/trigger"
    OCTAVE_ADDRESS = "/ji/mutation/octave"
    RANDOMNESS_ADDRESS = "/ji/mutation/set_randomness"
    DISSONANCE_ADDRESS = "/ji/mutation/set_dissonance_bias"
    STEPWISE_ADDRESS = "/ji/mutation/set_stepwise_bias"
    GLIDE_ADDRESS = "/ji/mutation/set_glide_time"

    def __init__(
        self,
        engine: MutationEngine,
        listen_host: str = "127.0.0.1",
        listen_port: int = 9000,
    ) -> None:
        if not isinstance(engine, MutationEngine):
            raise TypeError("engine must be a MutationEngine")
        if isinstance(listen_port, bool) or not isinstance(listen_port, int):
            raise TypeError("listen_port must be an integer")
        if not 1 <= listen_port <= 65535:
            raise ValueError("listen_port must be between 1 and 65535")

        self._engine = engine
        self.listen_host = str(listen_host)
        self.listen_port = listen_port

        self._lock = threading.RLock()
        self._randomness = 0.55
        self._dissonance_bias = 0.0
        self._stepwise_bias = 0.25
        self._glide_time = 0.0

        self._server: ThreadingOSCUDPServer | None = None
        self._thread: threading.Thread | None = None

        dispatcher = Dispatcher()
        dispatcher.map(self.TRIGGER_ADDRESS, self._handle_trigger)
        dispatcher.map(self.OCTAVE_ADDRESS, self._handle_octave)
        dispatcher.map(self.RANDOMNESS_ADDRESS, self._handle_set_randomness)
        dispatcher.map(self.DISSONANCE_ADDRESS, self._handle_set_dissonance_bias)
        dispatcher.map(self.STEPWISE_ADDRESS, self._handle_set_stepwise_bias)
        dispatcher.map(self.GLIDE_ADDRESS, self._handle_set_glide_time)
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

        with self._lock:
            try:
                event = self._engine.propose_mutation(
                    randomness=self._randomness,
                    dissonance_bias=self._dissonance_bias,
                    stepwise_bias=self._stepwise_bias,
                    glide_time=self._glide_time,
                )
                self._engine.apply(event)
            except (RuntimeError, ValueError, TypeError) as exc:
                _warn(f"mutation trigger failed: {exc}")
                return
            _print_mutation(event, self._engine)

    def _handle_octave(self, _address: str, *args: Any) -> None:
        if args:
            _warn(f"{self.OCTAVE_ADDRESS} takes no arguments; ignoring extras")

        with self._lock:
            try:
                event = self._engine.propose_octave_motion(
                    glide_time=self._glide_time
                )
                self._engine.apply_octave_motion(event)
            except (RuntimeError, ValueError, TypeError) as exc:
                _warn(f"octave trigger failed: {exc}")
                return
            _print_octave(event, self._engine)

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
        with self._lock:
            self._glide_time = max(0.0, value)
            print(f"[OSC] glide_time = {self._glide_time:.3f}s")


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

#!/usr/bin/env python3
"""Stage 3 bridge: mutation engine -> sclang autotune frequency set.

Python owns the :class:`~control.mutation.MutationEngine`.  After every applied
mutation trigger (and once at startup) the bridge sends the complete,
octave-reduced class set to sclang.  A multi-edit trigger is applied as one
atomic batch and still sends one final set:

    /ji/freqset/update  fundamental  glide_time  ratio_1 ... ratio_N

sclang keeps the verified Stage 1 nearest-class lookup, updates
``~ji_ji_set_ratios`` / ``~ji_ji_fundamental``, and applies the event's glide
to the autotune synth.  A held note therefore retunes on the next lookup tick
without being replayed.

No OSC is sent directly to scsynth in Stage 3.

Stage 6 adds optional MPE MIDI output (``--midi``).  The same engine snapshot is
handed to :class:`~control.midi_rig.MIDIRig`, which drives an anchor drone on
IAC Bus 1 and a set arp on IAC Bus 2.  MIDI is off by default; mido is imported
lazily only when a real port is opened.  The OSC payload above is unchanged.

Stage 7 adds an optional UI status stream to ``--ui-host``/``--ui-port``
(default ``127.0.0.1:9001``): one ``/ji/freqset/changed``, one ``/ji/ui/state``,
and one ``/ji/midi/status`` after every set and on ``/ji/ui/hello``.  The UI is a
separate process; the bridge never reads or controls it.
"""

from __future__ import annotations

import argparse
import math
import sys
import threading
from typing import Any

from pythonosc.udp_client import SimpleUDPClient

try:  # Support both ``python -m control.ji_bridge`` and direct execution.
    from .mutation import MutationBatch, MutationEngine, MutationEvent, OctaveEvent
    from .osc_server import MutationOSCServer
except ImportError:  # pragma: no cover - exercised only by direct execution
    from mutation import MutationBatch, MutationEngine, MutationEvent, OctaveEvent
    from osc_server import MutationOSCServer

FUNDAMENTAL = 220.0
INITIAL_RATIOS = [1.0, 9.0 / 8.0, 5.0 / 4.0, 4.0 / 3.0, 3.0 / 2.0, 5.0 / 3.0, 15.0 / 8.0]
FREQSET_ADDRESS = "/ji/freqset/update"
FREQSET_CHANGED_ADDRESS = "/ji/freqset/changed"
UI_STATE_ADDRESS = "/ji/ui/state"
MIDI_STATUS_ADDRESS = "/ji/midi/status"
MAX_RATIOS = 12


def build_freqset_args(engine: MutationEngine, glide_time: float) -> list[float]:
    """Return ``[fundamental, glide_time, ratio_1, ..., ratio_N]``.

    Pure helper: no sockets, no engine mutation.  Raises ``ValueError`` if the
    engine state or glide cannot be represented by the Stage 3 OSC contract.
    """

    ratios = engine.current_ratios
    if not 1 <= len(ratios) <= MAX_RATIOS:
        raise ValueError(
            f"frequency set has {len(ratios)} classes; expected 1..{MAX_RATIOS}"
        )

    try:
        fundamental = float(engine.fundamental)
        glide = float(glide_time)
        payload = [float(ratio) for ratio in ratios]
    except (TypeError, ValueError):
        raise ValueError(
            "fundamental, glide_time and ratios must be real numbers"
        ) from None

    if not math.isfinite(fundamental) or fundamental <= 0.0:
        raise ValueError("fundamental must be finite and strictly positive")
    if not math.isfinite(glide) or glide < 0.0:
        raise ValueError("glide_time must be finite and non-negative")
    if not all(math.isfinite(ratio) and ratio > 0.0 for ratio in payload):
        raise ValueError("every ratio must be finite and strictly positive")
    if not all(1.0 <= ratio < 2.0 for ratio in payload):
        raise ValueError("every ratio must be octave-reduced into [1, 2)")

    return [fundamental, glide, *payload]


class JIBridge:
    """Own the mutation engine and forward its set to sclang over UDP."""

    def __init__(
        self,
        sc_host: str = "127.0.0.1",
        sc_port: int = 57120,
        listen_host: str = "127.0.0.1",
        listen_port: int = 9000,
        seed: int | None = 1,
        glide_time: float = 0.15,
        max_changes: int = 3,
        change_count: int = 1,
        temperature: float = 0.0,
        anchor_mobility: float = 0.25,
        midi: bool = False,
        mpe_config: str | None = None,
        ui_host: str = "127.0.0.1",
        ui_port: int = 9001,
    ) -> None:
        self.sc_host = str(sc_host)
        self.sc_port = int(sc_port)
        self.listen_host = str(listen_host)
        self.listen_port = int(listen_port)
        self.ui_host = str(ui_host)
        self.ui_port = int(ui_port)

        self.engine = MutationEngine(
            INITIAL_RATIOS,
            fundamental=FUNDAMENTAL,
            target_size=7,
            max_size=MAX_RATIOS,
            min_size=3,
            seed=seed,
            max_changes=max_changes,
            anchor_mobility=anchor_mobility,
        )
        self.client = SimpleUDPClient(self.sc_host, self.sc_port)
        # Stage 7 UI status is a separate, best-effort UDP stream.  It never
        # feeds back into the mutation engine or the sclang frequency set.
        self.ui_client = SimpleUDPClient(self.ui_host, self.ui_port)
        self.server = MutationOSCServer(
            self.engine,
            listen_host=self.listen_host,
            listen_port=self.listen_port,
            on_event=self._on_event,
            on_octave=self._on_octave,
            on_ui_hello=self._send_ui_state,
        )
        # Stage 2 API: this is the glide carried by every subsequent event.
        self.server.set_glide_time(glide_time)
        self.server.set_change_count(change_count)
        self.server.set_temperature(temperature)

        # Stage 6 MIDI is optional and off by default.  Importing/constructing
        # the rig never imports mido; ports open in MIDIRig.start().
        self.mpe_config = mpe_config
        self.midi = None
        if midi:
            try:
                from .midi_rig import MIDIRig
            except ImportError:  # pragma: no cover - direct execution
                from midi_rig import MIDIRig

            self.midi = MIDIRig(mpe_config or "config/mpe_config.json")

    # ------------------------------------------------------------------
    # Lifecycle / transport
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the listener, send the initial set, and print status."""

        self.server.start()
        print(
            f"[BRIDGE] listening on {self.listen_host}:{self.listen_port}; "
            f"sending to sclang {self.sc_host}:{self.sc_port}"
        )
        print_set(self.engine, "initial set")
        print_controls(self.server)
        # Send the initial OSC set first.  If MIDI is enabled, its on_set is
        # stored as a snapshot and MIDIRig.start applies it immediately, so the
        # drone starts and the arp has a pattern before its thread begins.
        self.send_set(0.0)
        if self.midi is not None:
            self.midi.start()

    def stop(self) -> None:
        try:
            self.server.stop()
        finally:
            if self.midi is not None:
                self.midi.stop()

    def send_set(self, glide_time: float) -> bool:
        """Send the full current set to sclang.  Returns ``True`` on success."""

        args = build_freqset_args(self.engine, glide_time)
        sent = True
        try:
            self.client.send_message(FREQSET_ADDRESS, args)
        except (OSError, ValueError) as exc:
            print(f"[BRIDGE] send failed: {exc}", file=sys.stderr)
            sent = False

        # The MIDI rig receives the same snapshot, whether or not the sclang UDP
        # send succeeded.  Before MIDIRig.start it stores the initial snapshot;
        # start applies it so the drone and arp begin from the initial set.
        self._notify_midi()

        # Stage 7: mirror the resulting state to any listening UI.  This is
        # best-effort and deliberately independent of the sclang send result.
        self._send_ui_state()

        if sent:
            print(
                f"[BRIDGE] -> {FREQSET_ADDRESS} classes={len(args) - 2} "
                f"glide={args[1]:.3f} fundamental={args[0]:.1f}"
            )
        return sent

    def _notify_midi(self) -> None:
        """Hand a snapshot to the MIDI rig; MIDI errors never break the bridge."""

        if self.midi is None:
            return
        try:
            self.midi.on_set(
                ratios=list(self.engine.current_ratios),
                anchor=self.engine.anchor_ratio,
                fundamental=self.engine.fundamental,
            )
        except Exception as exc:  # deliberately broad: MIDI is non-critical
            print(f"[MIDI] set update failed: {exc}", file=sys.stderr)

    # ------------------------------------------------------------------
    # Stage 7 UI status
    # ------------------------------------------------------------------

    def _send_ui_state(self) -> None:
        """Send the current set, control state, and MIDI state to the UI.

        Called after every :meth:`send_set` and on ``/ji/ui/hello``.  The
        frequencies are the octave-reduced classes sorted by pitch; the UI
        derives Hz from the fundamental and each ratio.  A missing anchor is
        encoded as ``-1.0``, matching the Stage 7 contract.
        """

        ratios = sorted(float(ratio) for ratio in self.engine.current_ratios)
        anchor = self.engine.anchor_ratio
        freqset_args = [
            float(self.engine.fundamental),
            float(anchor) if anchor is not None else -1.0,
            *ratios,
        ]

        server = self.server
        ui_state_args = [
            float(server.randomness),
            float(server.dissonance_bias),
            float(server.stepwise_bias),
            float(server.glide_time),
            int(server.change_count),
            float(server.temperature),
            float(server.anchor_mobility),
            int(server.max_changes),
        ]

        self._send_ui_message(FREQSET_CHANGED_ADDRESS, freqset_args)
        self._send_ui_message(UI_STATE_ADDRESS, ui_state_args)
        self._send_midi_status()

    def _send_midi_status(self) -> None:
        """Send ``[enabled, drone_port, arp_port]``; empty ports when off."""

        if self.midi is None:
            args: list[Any] = [0, "", ""]
        else:
            config = getattr(self.midi, "config", None)
            drone_port = str(getattr(config, "drone_port", "") or "")
            arp_port = str(getattr(config, "arp_port", "") or "")
            args = [1, drone_port, arp_port]
        self._send_ui_message(MIDI_STATUS_ADDRESS, args)

    def _send_ui_message(self, address: str, args: list[Any]) -> None:
        """Best-effort UI UDP send; a missing UI must never break the bridge."""

        try:
            self.ui_client.send_message(address, args)
        except (OSError, ValueError) as exc:
            print(f"[BRIDGE] UI status send failed: {exc}", file=sys.stderr)

    # ------------------------------------------------------------------
    # MutationOSCServer callbacks (called after the engine lock is released)
    # ------------------------------------------------------------------

    def _on_event(self, event: MutationEvent | MutationBatch) -> None:
        self.send_set(event.glide_time)
        if isinstance(event, MutationBatch):
            print(
                f"[BRIDGE] batch changes={event.count} "
                f"glide={event.glide_time:.3f} "
                f"size={len(self.engine.current_ratios)} "
                f"anchor={_fmt_ratio(self.engine.anchor_ratio)}"
            )
            for index, edit in enumerate(event.events, 1):
                print(
                    f"[BRIDGE]   {index}/{event.count} "
                    f"removed={_fmt_ratio(edit.removed_ratio)} "
                    f"new={_fmt_ratio(edit.new_ratio)} "
                    f"reason={edit.reason}"
                )
            return

        print(
            "[BRIDGE] "
            f"removed={_fmt_ratio(event.removed_ratio)} "
            f"new={_fmt_ratio(event.new_ratio)} "
            f"reason={event.reason} "
            f"size={len(self.engine.current_ratios)} "
            f"anchor={_fmt_ratio(self.engine.anchor_ratio)}"
        )

    def _on_octave(self, event: OctaveEvent) -> None:
        # Octave events move the analysis voicing only; live autotune uses the
        # input's own octave, so no set update is sent.
        print(
            f"[BRIDGE] octave ratio={event.ratio:.6f} "
            f"{event.old_octave:+d}->{event.new_octave:+d} "
            f"reason={event.reason} size={len(self.engine.current_ratios)}"
        )


def print_set(engine: MutationEngine, label: str = "current set") -> None:
    """Print the current classes and analysis frequencies, sorted by pitch."""

    pairs = sorted(zip(engine.current_ratios, engine.current_frequencies))
    print(
        f"[BRIDGE] {label}: {len(pairs)} classes, "
        f"anchor={_fmt_ratio(engine.anchor_ratio)}"
    )
    print("[BRIDGE] ratios: " + "  ".join(f"{ratio:.6f}" for ratio, _ in pairs))
    print(
        "[BRIDGE] Hz:     "
        + "  ".join(f"{frequency:7.2f}" for _, frequency in pairs)
    )


def print_controls(server: MutationOSCServer) -> None:
    """Print the mutation controls in one compact line."""

    print(
        "[BRIDGE] controls: "
        f"change_count={server.change_count}/{server.max_changes} "
        f"temperature={server.temperature:.3f} "
        f"anchor_mobility={server.anchor_mobility:.3f} "
        f"randomness={server.randomness:.3f} "
        f"dissonance={server.dissonance_bias:+.3f} "
        f"stepwise={server.stepwise_bias:.3f} "
        f"glide={server.glide_time:.3f}"
    )


def run_interactive(bridge: JIBridge) -> None:
    """Read simple commands from stdin until ``q`` or EOF."""

    print(
        "[BRIDGE] interactive: m/Enter=mutate, o=octave, "
        "c <n>=changes, t <0..1>=temperature, a <0..1>=anchor mobility, "
        "g <seconds>=glide, s=show, q=quit"
    )
    while True:
        try:
            raw = input("ji> ")
        except EOFError:
            print()
            return

        line = raw.strip()
        if not line:
            bridge.server.trigger()
            continue

        parts = line.split()
        command = parts[0].lower()
        if command in ("m", "mutate"):
            bridge.server.trigger()
        elif command in ("o", "octave"):
            bridge.server.octave()
        elif command in ("c", "changes", "change-count"):
            if len(parts) != 2:
                print("[BRIDGE] usage: c <count>")
                continue
            try:
                bridge.server.set_change_count(float(parts[1]))
            except (TypeError, ValueError) as exc:
                print(f"[BRIDGE] invalid change count: {exc}")
        elif command in ("t", "temperature"):
            if len(parts) != 2:
                print("[BRIDGE] usage: t <0..1>")
                continue
            try:
                bridge.server.set_temperature(float(parts[1]))
            except (TypeError, ValueError) as exc:
                print(f"[BRIDGE] invalid temperature: {exc}")
        elif command in ("a", "anchor", "anchor-mobility"):
            if len(parts) != 2:
                print("[BRIDGE] usage: a <0..1>")
                continue
            try:
                bridge.server.set_anchor_mobility(float(parts[1]))
            except (TypeError, ValueError) as exc:
                print(f"[BRIDGE] invalid anchor mobility: {exc}")
        elif command in ("g", "glide"):
            if len(parts) != 2:
                print("[BRIDGE] usage: g <seconds>")
                continue
            try:
                seconds = float(parts[1])
                bridge.server.set_glide_time(seconds)
            except (TypeError, ValueError) as exc:
                print(f"[BRIDGE] invalid glide time: {exc}")
        elif command in ("s", "show"):
            print_set(bridge.engine)
            print_controls(bridge.server)
        elif command in ("q", "quit", "exit"):
            return
        else:
            print(
                "[BRIDGE] unknown command; use m, o, c <n>, t <0..1>, "
                "a <0..1>, g <seconds>, s, q"
            )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Stage 3/6/7 bridge: mutation engine -> sclang "
            "/ji/freqset/update, optional MPE MIDI rig, and Stage 7 UI status"
        )
    )
    parser.add_argument("--sc-host", default="127.0.0.1")
    parser.add_argument("--sc-port", type=int, default=57120)
    parser.add_argument("--listen-host", default="127.0.0.1")
    parser.add_argument("--listen-port", type=int, default=9000)
    parser.add_argument(
        "--ui-host",
        default="127.0.0.1",
        help="Stage 7 UI status destination host (default 127.0.0.1)",
    )
    parser.add_argument(
        "--ui-port",
        type=int,
        default=9001,
        help="Stage 7 UI status destination port (default 9001)",
    )
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--glide-time", type=float, default=0.15)
    parser.add_argument(
        "--max-changes",
        type=int,
        default=3,
        help="hard ceiling for edits per trigger (default 3)",
    )
    parser.add_argument(
        "--change-count",
        type=int,
        default=1,
        help="edits per trigger, 1..max-changes (default 1)",
    )
    parser.add_argument(
        "--temperature",
        type=float,
        default=0.0,
        help="0..1; broaden palette and flatten rank selection (default 0)",
    )
    parser.add_argument(
        "--anchor-mobility",
        type=float,
        default=0.25,
        help="0 = anchor never leaves, 1 = no precedence (default 0.25)",
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="accept m/o/c/t/a/g/s/q commands from stdin",
    )
    parser.add_argument(
        "--midi",
        action="store_true",
        help="enable the Stage 6 MPE MIDI rig (default: off)",
    )
    parser.add_argument(
        "--mpe-config",
        default="config/mpe_config.json",
        help="Stage 6 MIDI config path (default: config/mpe_config.json)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not 1 <= args.sc_port <= 65535:
        parser.error("--sc-port must be between 1 and 65535")
    if not 1 <= args.listen_port <= 65535:
        parser.error("--listen-port must be between 1 and 65535")
    if not 1 <= args.ui_port <= 65535:
        parser.error("--ui-port must be between 1 and 65535")
    if not math.isfinite(args.glide_time) or args.glide_time < 0.0:
        parser.error("--glide-time must be finite and non-negative")
    if not 1 <= args.max_changes <= MAX_RATIOS:
        parser.error(f"--max-changes must be between 1 and {MAX_RATIOS}")
    if not 1 <= args.change_count <= args.max_changes:
        parser.error("--change-count must be between 1 and --max-changes")
    if not math.isfinite(args.temperature) or not 0.0 <= args.temperature <= 1.0:
        parser.error("--temperature must be between 0 and 1")
    if (
        not math.isfinite(args.anchor_mobility)
        or not 0.0 <= args.anchor_mobility <= 1.0
    ):
        parser.error("--anchor-mobility must be between 0 and 1")

    bridge = JIBridge(
        sc_host=args.sc_host,
        sc_port=args.sc_port,
        listen_host=args.listen_host,
        listen_port=args.listen_port,
        seed=args.seed,
        glide_time=args.glide_time,
        max_changes=args.max_changes,
        change_count=args.change_count,
        temperature=args.temperature,
        anchor_mobility=args.anchor_mobility,
        midi=args.midi,
        mpe_config=args.mpe_config,
        ui_host=args.ui_host,
        ui_port=args.ui_port,
    )

    try:
        bridge.start()
        if args.interactive:
            run_interactive(bridge)
        else:
            print("[BRIDGE] headless; press Ctrl+C to stop")
            threading.Event().wait()
    except KeyboardInterrupt:
        print()
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"[BRIDGE] error: {exc}", file=sys.stderr)
        return 1
    finally:
        bridge.stop()

    return 0


def _fmt_ratio(value: float | None) -> str:
    return "none" if value is None else f"{value:.6f}"


if __name__ == "__main__":
    raise SystemExit(main())

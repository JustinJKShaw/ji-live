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

Stage 8.1 makes the bridge the canonical owner of quarter-note BPM, meter, and
transport.  ``--drums`` adds the optional drum output on IAC Bus 3 and the
``/ji/rhythm/*`` + ``/ji/drums/*`` control/status surface; ``/ji/rhythm/tempo``
is broadcast to sclang for loop quantization.  A missing Bus 3 disables drums
but leaves the drone/arp/audio rig running.

Stage 8.2 adds a next-quarter-beat queue for major state changes (mutation/
octave, MIDI/drum enables, drum rate/lanes, groove edits) and the groove status
surface (``/ji/drums/groove``, ``/ji/drums/grid``, ``/ji/quantize/state``).
With the transport stopped every queued action applies immediately.
"""

from __future__ import annotations

import argparse
import math
import sys
import threading
from typing import Any, Callable

from pythonosc.udp_client import SimpleUDPClient

try:  # Support both ``python -m control.ji_bridge`` and direct execution.
    from .drums import DEFAULT_LANE_MASK, DRUM_RATE_DEFAULT
    from .groove import Groove, validate_preset
    from .mutation import MutationBatch, MutationEngine, MutationEvent, OctaveEvent
    from .osc_server import MutationOSCServer
    from .quantize import (
        PHASE_DRUMS,
        PHASE_GROOVE,
        PHASE_MIDI,
        PHASE_MUTATION,
        PHASE_OCTAVE,
        BeatQuantizer,
    )
    from .rhythm import RhythmEngine, RhythmState
except ImportError:  # pragma: no cover - exercised only by direct execution
    from drums import DEFAULT_LANE_MASK, DRUM_RATE_DEFAULT  # type: ignore[no-redef]
    from groove import Groove, validate_preset  # type: ignore[no-redef]
    from mutation import (  # type: ignore[no-redef]
        MutationBatch,
        MutationEngine,
        MutationEvent,
        OctaveEvent,
    )
    from osc_server import MutationOSCServer  # type: ignore[no-redef]
    from quantize import (  # type: ignore[no-redef]
        PHASE_DRUMS,
        PHASE_GROOVE,
        PHASE_MIDI,
        PHASE_MUTATION,
        PHASE_OCTAVE,
        BeatQuantizer,
    )
    from rhythm import RhythmEngine, RhythmState  # type: ignore[no-redef]

FUNDAMENTAL = 220.0
INITIAL_RATIOS = [1.0, 9.0 / 8.0, 5.0 / 4.0, 4.0 / 3.0, 3.0 / 2.0, 5.0 / 3.0, 15.0 / 8.0]
FREQSET_ADDRESS = "/ji/freqset/update"
FREQSET_CHANGED_ADDRESS = "/ji/freqset/changed"
UI_STATE_ADDRESS = "/ji/ui/state"
MIDI_STATUS_ADDRESS = "/ji/midi/status"
MIDI_STATE_ADDRESS = "/ji/midi/state"
RHYTHM_STATE_ADDRESS = "/ji/rhythm/state"
RHYTHM_TEMPO_ADDRESS = "/ji/rhythm/tempo"
DRUMS_STATE_ADDRESS = "/ji/drums/state"
DRUMS_GROOVE_ADDRESS = "/ji/drums/groove"
DRUMS_GRID_ADDRESS = "/ji/drums/grid"
QUANTIZE_STATE_ADDRESS = "/ji/quantize/state"
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
        drums: bool = False,
        rhythm_config: str | None = None,
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
        # Stage 8.1: the bridge owns the canonical rhythm state.  The default
        # is 120 BPM, 4/4, transport stopped.  A rhythm config's bpm/meter are
        # applied below when drums are configured.
        self.rhythm = RhythmEngine()
        # Stage 8.2: major state changes are queued to the next quarter beat
        # while the transport runs.  The worker thread starts in start(), never
        # here, so constructing a bridge in a pure test creates no threads.
        self.quantizer = BeatQuantizer(
            self.rhythm, on_change=self._on_quantize_change
        )
        self.server = MutationOSCServer(
            self.engine,
            listen_host=self.listen_host,
            listen_port=self.listen_port,
            on_event=self._on_event,
            on_octave=self._on_octave,
            on_ui_hello=self._on_ui_hello,
            on_midi_enabled=self._set_midi_enabled,
            on_rhythm_tempo=self._on_rhythm_tempo,
            on_rhythm_meter=self._on_rhythm_meter,
            on_rhythm_transport=self._on_rhythm_transport,
            on_drums_enabled=self._on_drums_enabled,
            on_drums_lane=self._on_drums_lane,
            on_drums_rate=self._on_drums_rate,
            on_rhythm_request=self._on_rhythm_request,
            on_mutation_request=self.request_mutation,
            on_octave_request=self.request_octave,
            on_drums_preset=self._on_drums_preset,
            on_drums_density=self._on_drums_density,
            on_drums_syncopation=self._on_drums_syncopation,
            on_drums_swing=self._on_drums_swing,
            on_drums_probability=self._on_drums_probability,
            on_drums_step=self._on_drums_step,
            on_drums_seed=self._on_drums_seed,
            on_drums_reset_grid=self._on_drums_reset_grid,
        )
        # Stage 2 API: this is the glide carried by every subsequent event.
        self.server.set_glide_time(glide_time)
        self.server.set_change_count(change_count)
        self.server.set_temperature(temperature)

        # Stage 6 MIDI and/or the Stage 8.1 drum output are optional and off by
        # default.  Constructing either never imports mido; ports open in
        # MIDIRig.start().
        self.mpe_config = mpe_config
        self.midi = None
        if midi or drums:
            try:
                from .midi_rig import MIDIRig
            except ImportError:  # pragma: no cover - direct execution
                from midi_rig import MIDIRig

            # None lets MIDIRig use its package-relative default config, so
            # the bridge no longer depends on the current working directory.
            try:
                self.midi = MIDIRig(
                    mpe_config,
                    midi=midi,
                    drums=drums,
                    drum_config=rhythm_config,
                )
            except Exception as exc:
                if not drums:
                    raise
                # A bad rhythm config must not take the audio/MIDI rig down.
                print(
                    f"[MIDI] drums disabled: {exc}; drone/arp continue",
                    file=sys.stderr,
                )
                self.midi = (
                    MIDIRig(mpe_config, midi=midi, drums=False) if midi else None
                )

        # When drums are configured, their config supplies the initial rhythm
        # defaults (120 BPM, 4/4 by default).  Explicit changes arrive later
        # through /ji/rhythm/*.
        drum_rig = getattr(self.midi, "drums", None)
        if drum_rig is not None:
            config = drum_rig.config
            self.rhythm.set_bpm(config.bpm)
            self.rhythm.set_meter(config.meter_num, config.meter_den)
            # The drum engine owns the canonical groove state, so the bridge's
            # status/apply paths and the scheduler share one Grid.
            self.groove = drum_rig.engine.groove
        else:
            # No drum output: keep a valid groove state so the GUI/status layer
            # still works.  Edits are accepted and reported but make no sound.
            state = self.rhythm.state
            self.groove = Groove(
                meter_num=state.meter_num, meter_den=state.meter_den
            )

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
        self._sync_drums_context()
        # Broadcast the canonical tempo even if the UI was not running when
        # the bridge started.  A control surface installed later asks for a
        # resync with /ji/rhythm/request.
        self._broadcast_rhythm()
        self._print_rhythm()
        self._send_rhythm_state()
        self._send_drums_state()
        self._send_groove_state()
        self._send_grid_state()
        self.quantizer.start()
        self._send_quantize_state()

    def stop(self) -> None:
        # Stop the queue first so no action fires while the server/MIDI rig is
        # shutting down.  (Pending actions are dropped and logged.)
        self.quantizer.stop()
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
        self._send_midi_state()

    def _send_midi_status(self) -> None:
        """Send ``[enabled, drone_port, arp_port]``; empty ports when off."""

        enabled = self.midi is not None and bool(
            getattr(self.midi, "midi_enabled", True)
        )
        if not enabled:
            args: list[Any] = [0, "", ""]
        else:
            config = getattr(self.midi, "config", None)
            drone_port = str(getattr(config, "drone_port", "") or "")
            arp_port = str(getattr(config, "arp_port", "") or "")
            args = [1, drone_port, arp_port]
        self._send_ui_message(MIDI_STATUS_ADDRESS, args)

    def _send_midi_state(self) -> None:
        """Send ``[drone_on, arp_on]`` so the UI checkboxes resync."""

        enabled = self.midi is not None and bool(
            getattr(self.midi, "midi_enabled", True)
        )
        if not enabled:
            args: list[Any] = [0, 0]
        else:
            args = [
                1 if bool(getattr(self.midi, "drone_enabled", True)) else 0,
                1 if bool(getattr(self.midi, "arp_enabled", True)) else 0,
            ]
        self._send_ui_message(MIDI_STATE_ADDRESS, args)

    def _set_midi_enabled(self, drone: bool, arp: bool) -> None:
        """Stage 7.1/8.2: independent Drone/Arp switches, beat-quantized."""

        if self.midi is None or not bool(
            getattr(self.midi, "midi_enabled", True)
        ):
            print(
                "[MIDI] UI toggle ignored: MIDI is disabled "
                "(start the bridge with --midi)",
                file=sys.stderr,
            )
            self._send_midi_state()
            return

        def apply() -> None:
            try:
                self.midi.set_drone_enabled(drone)
                self.midi.set_arp_enabled(arp)
            except Exception as exc:  # MIDI is non-critical; keep alive
                print(f"[MIDI] UI toggle failed: {exc}", file=sys.stderr)
            finally:
                self._send_midi_state()

        self.quantizer.schedule(
            ("midi", "enabled"),
            apply,
            phase=PHASE_MIDI,
            label="midi enables",
        )

    # ------------------------------------------------------------------
    # Stage 8.2 quantize / groove status
    # ------------------------------------------------------------------

    def _on_quantize_change(self) -> None:
        """Queue status callback: one ``/ji/quantize/state`` per change."""

        self._send_quantize_state()

    def _send_quantize_state(self) -> None:
        pending = self.quantizer.pending_count
        seconds = self.quantizer.seconds_to_next_beat()
        if not self.rhythm.running:
            seconds = -1.0
        self._send_ui_message(
            QUANTIZE_STATE_ADDRESS, [int(pending), float(seconds)]
        )

    def _send_groove_state(self) -> None:
        self._send_ui_message(
            DRUMS_GROOVE_ADDRESS, list(self.groove.as_status_args())
        )

    def _send_grid_state(self) -> None:
        self._send_ui_message(DRUMS_GRID_ADDRESS, self.groove.grid.payload())

    def _log_groove(self) -> None:
        spec = self.groove.spec
        print(
            f"[GROOVE] preset={spec.preset} density={spec.density:.2f} "
            f"sync={spec.syncopation:.2f} swing={spec.swing:.2f} "
            f"probability={spec.probability:.2f} seed={spec.seed}"
        )

    def _applied_groove_log(self) -> None:
        if self.rhythm.running:
            beat = int(round(self.rhythm.beat_at()))
            print(f"[DRUMS] applied groove at beat {beat}")

    # ------------------------------------------------------------------
    # Stage 8.1 rhythm / drum status
    # ------------------------------------------------------------------

    def _on_ui_hello(self) -> None:
        """Reply to a UI handshake with the full Stage 7/8.2 snapshot."""

        self._send_ui_state()
        self._send_rhythm_state()
        self._send_drums_state()
        self._send_groove_state()
        self._send_grid_state()
        self._send_quantize_state()

    def _drums_snapshot(self) -> tuple[bool, bool, float, int]:
        getter = getattr(self.midi, "drums_state", None)
        if callable(getter):
            try:
                return getter()
            except Exception as exc:  # keep the UI status alive
                print(f"[DRUMS] state failed: {exc}", file=sys.stderr)
        return False, False, DRUM_RATE_DEFAULT, DEFAULT_LANE_MASK

    def _send_rhythm_state(self) -> None:
        enabled, _port_ok, rate, _mask = self._drums_snapshot()
        state: RhythmState = self.rhythm.state
        self._send_ui_message(
            RHYTHM_STATE_ADDRESS, state.as_state_args(enabled, rate)
        )

    def _send_drums_state(self) -> None:
        enabled, port_ok, rate, lane_mask = self._drums_snapshot()
        self._send_ui_message(
            DRUMS_STATE_ADDRESS,
            [int(enabled), int(port_ok), float(rate), int(lane_mask)],
        )

    def _broadcast_rhythm(self) -> None:
        """Send the canonical tempo/meter/transport to sclang."""

        state = self.rhythm.state
        try:
            self.client.send_message(
                RHYTHM_TEMPO_ADDRESS, state.as_tempo_args()
            )
        except (OSError, ValueError) as exc:
            print(f"[RHYTHM] sclang tempo send failed: {exc}", file=sys.stderr)

    def _print_rhythm(self) -> None:
        state = self.rhythm.state
        print(
            f"[RHYTHM] bpm={state.bpm:.1f} meter={state.meter} "
            f"running={int(state.running)}"
        )

    def _sync_drums_context(self) -> None:
        state = self.rhythm.state
        self._midi_call("set_drums_bpm", state.bpm)
        self._midi_call("set_drums_meter", state.meter_num, state.meter_den)
        self._midi_call("set_drums_transport", state.running)

    def _midi_call(self, name: str, *args: Any) -> Any:
        """Call a MIDIRig drum method if the injected rig provides it."""

        if self.midi is None:
            return None
        method = getattr(self.midi, name, None)
        if not callable(method):
            return None
        try:
            return method(*args)
        except Exception as exc:  # drums are non-critical
            print(f"[DRUMS] {name} failed: {exc}", file=sys.stderr)
            return None

    def _on_rhythm_tempo(self, bpm: float) -> None:
        try:
            state = self.rhythm.set_bpm(bpm)
        except (TypeError, ValueError) as exc:
            print(f"[RHYTHM] invalid tempo: {exc}", file=sys.stderr)
            return
        self._midi_call("set_drums_bpm", state.bpm)
        # Recompute the pending actions' next beat at the new tempo; never drop
        # them and never apply them immediately just because the tempo moved.
        self.quantizer.tempo_changed()
        self._broadcast_rhythm()
        self._send_rhythm_state()
        # A tempo change moves the next-beat countdown even with no pending
        # actions; send one fresh quantize state so the UI does not count down
        # against a stale interval.  (tempo_changed already sent one if pending.)
        if self.quantizer.pending_count == 0:
            self._send_quantize_state()
        self._print_rhythm()

    def _on_rhythm_meter(self, meter_num: int, meter_den: int) -> None:
        try:
            state = self.rhythm.set_meter(meter_num, meter_den)
        except (TypeError, ValueError) as exc:
            print(f"[RHYTHM] invalid meter: {exc}", file=sys.stderr)
            return
        if self._drum_engine() is None:
            self.groove.set_meter(state.meter_num, state.meter_den)
            # A test/fake rig may still expose the setter; real no-drums rigs
            # simply return None from _midi_call.
            self._midi_call("set_drums_meter", state.meter_num, state.meter_den)
            print(
                f"[GROOVE] grid reloaded for meter={state.meter} "
                f"steps={self.groove.grid_steps} "
                f"(preset={self.groove.spec.preset})"
            )
            if self.groove.steps_per_bar > self.groove.grid_steps:
                print(
                    f"[GROOVE] grid capped at {self.groove.grid_steps} "
                    f"editable columns (bar has {self.groove.steps_per_bar} steps)"
                )
        else:
            # DrumRig logs the reload; this also replaces manual grid edits.
            self._midi_call("set_drums_meter", state.meter_num, state.meter_den)
        self._broadcast_rhythm()
        self._send_rhythm_state()
        self._send_groove_state()
        self._send_grid_state()
        self._print_rhythm()

    def _on_rhythm_transport(self, running: bool) -> None:
        if not running:
            # Flush before stopping: the last command is not lost, and the
            # queue cannot carry stale state into the next start.
            self.quantizer.flush("transport stop")
        state = self.rhythm.set_running(bool(running))
        self.quantizer.transport_changed()
        # Stopping the scheduler sends note-offs + CC 123; starting it resets
        # to step 0.  Drums run only while the transport is running.
        self._midi_call("set_drums_transport", state.running)
        self._broadcast_rhythm()
        self._send_rhythm_state()
        self._send_quantize_state()
        self._print_rhythm()

    def _on_rhythm_request(self) -> None:
        """Resync: re-broadcast the canonical tempo without changing state."""

        self._broadcast_rhythm()

    # ------------------------------------------------------------------
    # Stage 8.2 quantized mutation requests
    # ------------------------------------------------------------------

    def request_mutation(self) -> None:
        """UI/OSC/console mutation request: next beat or immediate if stopped."""

        self.quantizer.schedule(
            ("mutation",),
            self.server.trigger,
            phase=PHASE_MUTATION,
            label="mutation",
        )

    def request_octave(self) -> None:
        """UI/OSC/console octave request: next beat or immediate if stopped."""

        self.quantizer.schedule(
            ("octave",),
            self.server.octave,
            phase=PHASE_OCTAVE,
            label="octave",
        )

    def _on_drums_enabled(self, enabled: bool) -> None:
        _current, port_ok, _rate, _mask = self._drums_snapshot()
        if self.midi is None or not port_ok:
            print(
                "[MIDI] drums unavailable: start the bridge with --drums and "
                "a configured IAC Driver Bus 3",
                file=sys.stderr,
            )
            self._send_drums_state()
            self._send_rhythm_state()
            return

        def apply() -> None:
            self._midi_call("set_drums_enabled", bool(enabled))
            self._send_drums_state()
            self._send_rhythm_state()
            self._print_drums_state()

        self.quantizer.schedule(
            ("drums", "enabled"),
            apply,
            phase=PHASE_DRUMS,
            label="drums enabled",
        )

    def _on_drums_lane(self, note: int, enabled: bool) -> None:
        _current, port_ok, _rate, _mask = self._drums_snapshot()
        if self.midi is None or not port_ok:
            print(
                f"[DRUMS] lane {note} ignored: drums are unavailable",
                file=sys.stderr,
            )
            self._send_drums_state()
            return

        def apply() -> None:
            try:
                self._midi_call("set_drums_lane", int(note), bool(enabled))
            except (TypeError, ValueError):
                pass
            self._send_drums_state()
            self._print_drums_state()

        self.quantizer.schedule(
            ("drums", "lane", int(note)),
            apply,
            phase=PHASE_DRUMS,
            label=f"drum lane {int(note)}",
        )

    def _on_drums_rate(self, rate: float) -> None:
        _current, port_ok, _rate, _mask = self._drums_snapshot()
        if self.midi is None or not port_ok:
            print("[DRUMS] rate ignored: drums are unavailable", file=sys.stderr)
            self._send_drums_state()
            return

        def apply() -> None:
            self._midi_call("set_drums_rate", rate)
            self._send_drums_state()
            self._send_rhythm_state()
            self._print_drums_state()

        self.quantizer.schedule(
            ("drums", "rate"),
            apply,
            phase=PHASE_DRUMS,
            label="drum rate",
        )

    # -- Stage 8.2 groove requests ---------------------------------------

    def _drum_engine(self) -> Any:
        drum_rig = getattr(self.midi, "drums", None)
        return getattr(drum_rig, "engine", None)

    def _groove_apply(
        self,
        key: tuple[Any, ...],
        action: Callable[[], None],
        label: str,
        *,
        send_grid: bool = False,
    ) -> None:
        """Common groove request path: queue while running, else immediate."""

        def apply() -> None:
            action()
            self._log_groove()
            self._applied_groove_log()
            self._send_groove_state()
            if send_grid:
                self._send_grid_state()

        self.quantizer.schedule(
            key, apply, phase=PHASE_GROOVE, label=label
        )

    def _on_drums_preset(self, preset: str) -> None:
        engine = self._drum_engine()

        def action() -> None:
            if engine is not None:
                engine.set_preset(preset)
            else:
                self.groove.set_preset(preset)

        try:
            # Validate before queueing so a bad name cannot poison the queue.
            validate_preset(preset)
        except (TypeError, ValueError) as exc:
            print(f"[GROOVE] invalid preset: {exc}", file=sys.stderr)
            self._send_groove_state()
            return

        self._groove_apply(
            ("groove", "preset"),
            action,
            "groove preset",
            send_grid=True,
        )

    def _on_drums_density(self, value: float) -> None:
        self._on_groove_modifier("density", value)

    def _on_drums_syncopation(self, value: float) -> None:
        self._on_groove_modifier("syncopation", value)

    def _on_drums_swing(self, value: float) -> None:
        self._on_groove_modifier("swing", value)

    def _on_drums_probability(self, value: float) -> None:
        self._on_groove_modifier("probability", value)

    def _on_groove_modifier(self, name: str, value: float) -> None:
        engine = self._drum_engine()

        def action() -> None:
            if engine is not None:
                engine.set_groove_modifier(name, value)
            else:
                self.groove.set_modifier(name, value)

        self._groove_apply(
            ("groove", name), action, f"groove {name}"
        )

    def _on_drums_seed(self, seed: int) -> None:
        engine = self._drum_engine()

        def action() -> None:
            if engine is not None:
                engine.set_seed(seed)
            else:
                self.groove.set_seed(seed)

        self._groove_apply(("groove", "seed"), action, "groove seed")

    def _on_drums_step(self, lane: int, step: int, enabled: bool) -> None:
        engine = self._drum_engine()

        def action() -> None:
            if engine is not None:
                engine.set_step(lane, step, enabled)
            else:
                self.groove.set_cell(lane, step, enabled)

        self._groove_apply(
            ("groove", "step", int(lane), int(step)),
            action,
            f"groove step {int(lane)}:{int(step)}",
            send_grid=True,
        )

    def _on_drums_reset_grid(self) -> None:
        engine = self._drum_engine()

        def action() -> None:
            if engine is not None:
                engine.reset_grid()
            else:
                self.groove.reset_grid()

        self._groove_apply(
            ("groove", "reset"), action, "groove reset", send_grid=True
        )

    def _print_drums_state(self) -> None:
        enabled, port_ok, rate, mask = self._drums_snapshot()
        print(
            f"[DRUMS] enabled={int(enabled)} port_ok={int(port_ok)} "
            f"rate={rate:.1f} lanes=0x{mask:04x}"
        )

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
            bridge.request_mutation()
            continue

        parts = line.split()
        command = parts[0].lower()
        if command in ("m", "mutate"):
            bridge.request_mutation()
        elif command in ("o", "octave"):
            bridge.request_octave()
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
            "Stage 3/6/7/8.1 bridge: mutation engine -> sclang /ji/freqset/update, "
            "optional MPE MIDI rig, drum output, rhythm state, and UI status"
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
        default=None,
        help=(
            "Stage 6 MIDI config path "
            "(default: config/mpe_config.json next to the project)"
        ),
    )
    parser.add_argument(
        "--drums",
        action="store_true",
        help=(
            "enable the Stage 8.1 drum MIDI output on IAC Driver Bus 3 "
            "(default: off; works with or without --midi)"
        ),
    )
    parser.add_argument(
        "--rhythm-config",
        default=None,
        help=(
            "drum/rhythm config path "
            "(default: config/rhythm_config.json next to the project)"
        ),
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
        drums=args.drums,
        rhythm_config=args.rhythm_config,
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

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

Stage 8.3a adds the optional monophonic bass voice (``--bass``, default
``IAC Driver Bus 4``) and its ``/ji/bass/*`` request/status surface.  Bass
enable/preset/grid/length/density/syncopation/swing/gate/accent/root/pitch/
movement/octave/seed/base/sub changes use the same next-quarter-beat queue with
``PHASE_BASS`` between drums and arp.

Stage 8.3b adds mutable arp parameters (``/ji/arp/set_*``) and the
``/ji/arp/state`` status message.  Step/gate/velocity/octave/shared-mode/order/
seed changes are queued on ``PHASE_ARP`` (after bass, before the MIDI enables)
while the transport runs and apply immediately while it is stopped.  The Stage
7.1 Drone/Arp switches remain the only enable/disable path.

Stage 8.3d adds the optional lead/melody voice (``--lead``, default
``IAC Driver Bus 5``) and the drone pad modes (``hold``/``pulse``/``bend`` on the
existing Bus 1).  ``/ji/lead/set_*``, ``/ji/lead/vary``, ``/ji/drone/set_*`` and
the two state messages use ``PHASE_LEAD`` and ``PHASE_DRONE`` (between arp and
the MIDI enables).  The lead degrades alone when Bus 5 is missing.

Stage 8.4 adds home sets: four captured snapshots (``A``-``D``), a deterministic
journey (``reseed`` + a mutation counter), an optional drift cap, **Back**, and a
beat-spaced **return** that lands exactly on the home set in
``ceil(distance / max_changes)`` quarter beats.  All five home requests are
queued on ``PHASE_HOME`` (the first phase, so a return batch lands before a
mutation requested for the same beat) and apply immediately while the transport
is stopped (the whole return then becomes one set change).  Every batch reuses
:meth:`JIBridge.send_set`, so sclang, MIDI, the UI and the history all see it.
``/ji/home/state`` is the 13-value status message.

Stage 8.5 adds **performance states**: a named, versioned JSON snapshot of the
whole performance configuration (frequency set, mutation controls, home slots,
tempo/meter, drums, groove, bass/arp/lead/drone settings) in ``states/``.
``/ji/state/save|load|delete|request`` are the control surface, ``/ji/state/list``
and ``/ji/state/state`` the replies.  Save/delete/list are immediate file
operations; a **load is beat-quantized on** ``PHASE_STATE`` (last, so it wins
the beat) and applies as one atomic set change: cancel a pending return/glide,
restore everything, then one ``send_set`` with the saved glide time.  Loops and
the sclang-owned autotune parameters are deliberately **not** part of a state.
"""

from __future__ import annotations

import argparse
import math
import sys
from datetime import datetime
import threading
import time
from typing import Any, Callable

from pythonosc.udp_client import SimpleUDPClient

try:  # Support both ``python -m control.ji_bridge`` and direct execution.
    from . import arp as arp_module
    from . import bass as bass_module
    from . import breath as breath_module
    from . import drone as drone_module
    from . import glide as glide_module
    from . import home as home_module
    from . import lead as lead_module
    from . import lead_input as lead_input_module
    from . import melody as melody_module
    from . import state as state_module
    from .drums import DEFAULT_LANE_MASK, DRUM_RATE_DEFAULT, LANE_COUNT
    from .groove import Groove, validate_preset
    from .mutation import (
        MutationBatch,
        MutationEngine,
        MutationEvent,
        OctaveEvent,
        SetSnapshot,
    )
    from .osc_server import MutationOSCServer
    from .quantize import (
        PHASE_ARP,
        PHASE_BASS,
        PHASE_DRONE,
        PHASE_DRUMS,
        PHASE_GROOVE,
        PHASE_HOME,
        PHASE_LEAD,
        PHASE_MIDI,
        PHASE_MUTATION,
        PHASE_OCTAVE,
        PHASE_STATE,
        BeatQuantizer,
    )
    from .rhythm import RhythmEngine, RhythmState, bar_quarters
except ImportError:  # pragma: no cover - exercised only by direct execution
    import arp as arp_module  # type: ignore[no-redef]
    import bass as bass_module  # type: ignore[no-redef]
    import breath as breath_module  # type: ignore[no-redef]
    import drone as drone_module  # type: ignore[no-redef]
    import glide as glide_module  # type: ignore[no-redef]
    import home as home_module  # type: ignore[no-redef]
    import lead as lead_module  # type: ignore[no-redef]
    import lead_input as lead_input_module  # type: ignore[no-redef]
    import melody as melody_module  # type: ignore[no-redef]
    import state as state_module  # type: ignore[no-redef]
    from drums import (  # type: ignore[no-redef]
        DEFAULT_LANE_MASK,
        DRUM_RATE_DEFAULT,
        LANE_COUNT,
    )
    from groove import Groove, validate_preset  # type: ignore[no-redef]
    from mutation import (  # type: ignore[no-redef]
        MutationBatch,
        MutationEngine,
        MutationEvent,
        OctaveEvent,
        SetSnapshot,
    )
    from osc_server import MutationOSCServer  # type: ignore[no-redef]
    from quantize import (  # type: ignore[no-redef]
        PHASE_ARP,
        PHASE_BASS,
        PHASE_DRONE,
        PHASE_DRUMS,
        PHASE_GROOVE,
        PHASE_HOME,
        PHASE_LEAD,
        PHASE_MIDI,
        PHASE_MUTATION,
        PHASE_OCTAVE,
        PHASE_STATE,
        BeatQuantizer,
    )
    from rhythm import RhythmEngine, RhythmState, bar_quarters  # type: ignore[no-redef]

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
BASS_STATE_ADDRESS = "/ji/bass/state"
BASS_HITS_ADDRESS = "/ji/bass/hits"
BASS_NOTE_ADDRESS = "/ji/bass/note"
BREATH_STATE_ADDRESS = "/ji/breath/state"
ARP_STATE_ADDRESS = "/ji/arp/state"
LEAD_STATE_ADDRESS = "/ji/lead/state"
LEAD_NOTE_ADDRESS = "/ji/lead/note"
LEAD_INPUT_STATE_ADDRESS = "/ji/lead/input/state"
LEAD_MELODY_LIST_ADDRESS = "/ji/lead/melody/list"
LEAD_MELODY_STATE_ADDRESS = "/ji/lead/melody/state"
DRONE_STATE_ADDRESS = "/ji/drone/state"
HOME_STATE_ADDRESS = "/ji/home/state"
STATE_LIST_ADDRESS = "/ji/state/list"
STATE_STATE_ADDRESS = "/ji/state/state"
MAX_RATIOS = 12


def build_snapshot_args(
    snapshot: SetSnapshot, fundamental: float, glide_time: float
) -> list[float]:
    """Return ``[fundamental, glide_time, ratio_1, ..., ratio_N]`` for a snapshot.

    Pure helper: no sockets, no engine mutation.  Raises ``ValueError`` if the
    snapshot or glide cannot be represented by the Stage 3 OSC contract.
    """

    ratios = tuple(snapshot.ratios)
    if not 1 <= len(ratios) <= MAX_RATIOS:
        raise ValueError(
            f"frequency set has {len(ratios)} classes; expected 1..{MAX_RATIOS}"
        )

    try:
        fundamental = float(fundamental)
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


def build_freqset_args(engine: MutationEngine, glide_time: float) -> list[float]:
    """Return ``[fundamental, glide_time, ratio_1, ..., ratio_N]``.

    Pure helper: no sockets, no engine mutation.  Raises ``ValueError`` if the
    engine state or glide cannot be represented by the Stage 3 OSC contract.

    The payload keeps the engine's insertion order (``current_ratios``), so the
    accepted Stage 3/8.4 byte stream is unchanged.
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
        bass: bool = False,
        bass_config: str | None = None,
        lead: bool = False,
        lead_config: str | None = None,
        drone_config: str | None = None,
        home_config: str | None = None,
        state_dir: str | None = None,
        lead_in: bool = False,
        lead_in_config: str | None = None,
        lead_in_port: str | None = None,
        melody_dir: str | None = None,
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
        # Stage 8.7: one breath clock, shared (by reference) with every voice
        # scheduler.  It never touches BeatClock, the quantizer or the state.
        self.breath = breath_module.BreathClock(bpm=self.rhythm.bpm)
        # Stage 8.2: major state changes are queued to the next quarter beat
        # while the transport runs.  The worker thread starts in start(), never
        # here, so constructing a bridge in a pure test creates no threads.
        self.quantizer = BeatQuantizer(
            self.rhythm, on_change=self._on_quantize_change
        )
        # Stage 8.4.1: one bounded class glide at a time.  Constructing the
        # scheduler creates no thread (like the quantizer); the worker starts
        # lazily on the first plan.  The stage-8.5 state load calls
        # ``cancel_glide()`` before restoring a snapshot.
        self.glide = glide_module.GlideScheduler(self._on_glide_step)
        self._started = False
        # ``_mutate`` captures the pre-change snapshot here; ``_on_event``
        # consumes and clears it, so a direct ``server.trigger()`` outside the
        # bridge never glides.
        self._pending_before: SetSnapshot | None = None
        self._glide_duration = 0.0
        self._glide_step_seconds = 1.0 / glide_module.GLIDE_RATE_HZ
        self._glide_cancelling = False
        # Stage 8.4: home slots, journeys, history and the return planner.  The
        # manager is always constructed; empty slots cost nothing and the
        # accepted rig is unchanged until the player captures a home.
        self.home_config_path = home_config
        self.home = home_module.HomeManager(self._load_home_config(home_config))
        # The initial set is the first history entry, so Back works from the
        # very first mutation even before a home is captured.
        self.home.record(self.engine.snapshot())
        # Stage 8.5: the state library is always constructed; a missing
        # directory costs nothing and is created on the first save.
        self.state_dir = state_module.resolve_state_dir(state_dir)
        self.states = state_module.StateLibrary(self.state_dir)
        self._state_lock = threading.RLock()
        self._state_last_saved = ""
        self._state_last_loaded = ""
        self._state_message = ""
        # Stage 9.1: the melody library is always constructed (file ops are
        # harmless without --lead); the MIDI input rig is built in start()
        # only when --lead-in and --lead are both on.
        self.melody_dir = melody_module.resolve_melody_dir(melody_dir)
        self.melodies = melody_module.MelodyLibrary(self.melody_dir)
        self._melody_lock = threading.RLock()
        self._melody_last_saved = ""
        self._melody_last_loaded = ""
        self._melody_message = ""
        self.lead_in_requested = bool(lead_in and lead)
        self.lead_in_config = lead_in_config
        self.lead_in_port = lead_in_port
        self.lead_input = None
        if lead_in and not lead:
            print("[LEAD-IN] --lead-in ignored: start with --lead for Bus 5")
        self._lead_input_default_state = [
            0,
            0,
            "",
            1.0,
            lead_input_module.DEFAULT_COUNT_IN,
            2,
            0,
            0.0,
            0,
            "generated",
            "",
            -1,
            -1,
            0.0,
        ]
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
            on_bass_change=self._on_bass_change,
            on_bass_hits_enabled=self._on_bass_hits_enabled,
            on_bass_hit=self._on_bass_hit,
            on_bass_clear_hits=self._on_bass_clear_hits,
            on_bass_copy_hits=self._on_bass_copy_hits,
            on_breath_change=self._on_breath_change,
            on_arp_change=self._on_arp_change,
            on_lead_change=self._on_lead_change,
            on_lead_vary=self._on_lead_vary,
            on_lead_input_enabled=self._on_lead_input_enabled,
            on_lead_input_quantize=self._on_lead_input_quantize,
            on_lead_input_count_in=self._on_lead_input_count_in,
            on_lead_input_bars=self._on_lead_input_bars,
            on_lead_input_note_on=self._on_lead_input_note_on,
            on_lead_input_note_off=self._on_lead_input_note_off,
            on_lead_input_record=self._on_lead_input_record,
            on_lead_input_stop=self._on_lead_input_stop,
            on_lead_input_revert=self._on_lead_input_revert,
            on_lead_input_clear=self._on_lead_input_clear,
            on_lead_input_refresh=self._on_lead_input_refresh,
            on_lead_melody_save=self._on_lead_melody_save,
            on_lead_melody_load=self._on_lead_melody_load,
            on_lead_melody_delete=self._on_lead_melody_delete,
            on_lead_melody_request=self._on_lead_melody_request,
            on_drone_change=self._on_drone_change,
            on_home_capture=self.request_home_capture,
            on_home_clear=self.request_home_clear,
            on_home_go=self.request_home_go,
            on_home_return=self.request_home_return,
            on_home_jump=self.request_home_jump,
            on_home_back=self.request_back,
            on_home_setting=self._on_home_setting,
            on_state_save=self.request_state_save,
            on_state_load=self.request_state_load,
            on_state_delete=self.request_state_delete,
            on_state_request=self.request_state_list,
        )
        # Stage 2 API: this is the glide carried by every subsequent event.
        self.server.set_glide_time(glide_time)
        self.server.set_change_count(change_count)
        self.server.set_temperature(temperature)

        # Stage 6 MIDI and/or the Stage 8.1 drum output are optional and off by
        # default.  Constructing either never imports mido; ports open in
        # MIDIRig.start().
        self.mpe_config = mpe_config
        self.bass_config = bass_config
        self.lead_config = lead_config
        self.drone_config = drone_config
        self.midi = None
        if midi or drums or bass or lead:
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
                    bass=bass,
                    bass_config=bass_config,
                    lead=lead,
                    lead_config=lead_config,
                    drone_config=drone_config,
                )
            except Exception as exc:
                if not drums:
                    raise
                # A bad rhythm config must not take the audio/MIDI rig down.
                print(
                    f"[MIDI] drums disabled: {exc}; drone/arp/bass continue",
                    file=sys.stderr,
                )
                self.midi = MIDIRig(
                    mpe_config,
                    midi=midi,
                    drums=False,
                    bass=bass,
                    bass_config=bass_config,
                    lead=lead,
                    lead_config=lead_config,
                    drone_config=drone_config,
                ) if (midi or bass or lead) else None

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
        # Stage 8.7: the breath clock follows the canonical tempo/meter that was
        # just established (a config change re-anchors without a jump).
        initial = self.rhythm.state
        self.breath.set_tempo(
            initial.bpm, initial.meter_num, initial.meter_den
        )
        # A status payload for the no-bass case, so the GUI always receives a
        # well-formed /ji/bass/state snapshot.
        self._bass_default_state = bass_module.BassEngine(
            bass_module.BassConfig()
        ).state_args(False)
        # Same idea for the arp: a well-formed /ji/arp/state when --midi is off.
        self._arp_default_state = arp_module.ArpSettings().state_args(False, False)
        # Stage 8.3d: well-formed lead/drone state when the buses are off.
        self._lead_default_state = lead_module.LeadSettings().state_args(
            False, False
        )
        self._drone_default_state = drone_module.DroneSettings().state_args(False)

    # ------------------------------------------------------------------
    # Lifecycle / transport
    # ------------------------------------------------------------------

    def _load_home_config(
        self, path: str | None
    ) -> home_module.HomeConfig:
        """Load the home config; a bad/missing file falls back to defaults."""

        try:
            return home_module.HomeConfig.load(path) if path else home_module.HomeConfig.load()
        except (OSError, TypeError, ValueError) as exc:
            print(
                f"[HOME] config: {exc}; using the shipped defaults",
                file=sys.stderr,
            )
            return home_module.HomeConfig()

    def _print_home_config(self) -> None:
        config = self.home.config
        print(
            f"[HOME] slots={config.slots} auto={int(config.auto)} "
            f"every={config.every} seed={config.seed} drift={config.max_drift} "
            f"glide={config.return_glide:.3f} history={config.history_depth}"
        )

    def start(self) -> None:
        """Start the listener, send the initial set, and print status."""

        self.server.start()
        self._started = True
        print(
            f"[BRIDGE] listening on {self.listen_host}:{self.listen_port}; "
            f"sending to sclang {self.sc_host}:{self.sc_port}"
        )
        print_set(self.engine, "initial set")
        print_controls(self.server)
        self._print_home_config()
        # Send the initial OSC set first.  If MIDI is enabled, its on_set is
        # stored as a snapshot and MIDIRig.start applies it immediately, so the
        # drone starts and the arp has a pattern before its thread begins.
        self.send_set(0.0)
        if self.midi is not None:
            self.midi.start()
            self._midi_call("set_bass_note_callback", self._on_bass_note)
            self._midi_call("set_lead_note_callback", self._on_lead_note)
        self._start_lead_input()
        self._sync_drums_context()
        # If the arp is configured beat-synced, start it on the grid rather
        # than at an arbitrary phase.
        self._realign_arp()
        # Broadcast the canonical tempo even if the UI was not running when
        # the bridge started.  A control surface installed later asks for a
        # resync with /ji/rhythm/request.
        self._broadcast_rhythm()
        self._print_rhythm()
        self._print_breath()
        self._send_rhythm_state()
        self._send_drums_state()
        self._send_groove_state()
        self._send_grid_state()
        self._send_bass_state()
        self._send_bass_hits()
        self._send_breath_state()
        self._send_arp_state()
        self._send_lead_state()
        self._send_lead_input_state()
        self._send_melody_list()
        self._send_melody_state()
        self._send_drone_state()
        self._send_home_state()
        self._print_state_dir()
        self._send_state_list()
        self._send_state_state()
        if self._bass_rig() is None:
            print(
                "[BASS] not configured; start the bridge with --bass "
                "(default port IAC Driver Bus 4)"
            )
        if self._lead_rig() is None:
            print(
                "[LEAD] not configured; start the bridge with --lead "
                "(default port IAC Driver Bus 5)"
            )
        self.quantizer.start()
        self._send_quantize_state()

    def stop(self) -> None:
        # Release the lead input before the MIDI buses close, so no played
        # note can be left hanging.
        if self.lead_input is not None:
            try:
                self.lead_input.stop()
            except Exception as exc:  # input is never fatal
                print(f"[LEAD-IN] stop failed: {exc}", file=sys.stderr)
        # Stop the queue first so no action fires while the server/MIDI rig is
        # shutting down.  (Pending actions are dropped and logged.)
        self.quantizer.stop()
        # Stage 8.4.1: a class glide never outlives the bridge.  No snap here:
        # the audio/MIDI rigs are closing too.
        self._started = False
        self.glide.stop()
        try:
            self.server.stop()
        finally:
            if self.midi is not None:
                self.midi.stop()

    def send_set(self, glide_time: float) -> bool:
        """Send the full current set to sclang.  Returns ``True`` on success.

        Byte-identical to the Stage 3/8.4 behavior: the payload keeps the
        engine's insertion order (``current_ratios``), the UI mirror and the
        single ``[BRIDGE] ->`` log line are unchanged.
        """

        args = build_freqset_args(self.engine, glide_time)
        return self._send_args(
            args,
            self.engine.snapshot(),
            ui=True,
            transient=False,
            log=True,
        )

    def send_snapshot(
        self,
        snapshot: SetSnapshot,
        glide_time: float,
        *,
        ui: bool = True,
        transient: bool = False,
        log: bool = True,
    ) -> bool:
        """Send one snapshot to every voice.

        The snapshot-aware sibling of :meth:`send_set` (Stage 8.4.1):

        * sclang: one ``/ji/freqset/update [fundamental, glide_time, *ratios]``;
        * MIDI: ``on_set(ratios, anchor, fundamental, transient=transient)``;
        * UI: the final set only (``ui=True``), using the engine's final state;
        * log: one ``[BRIDGE] ->`` line only for a non-transient/final update.

        A transient call is one step of a class glide: the arp keeps its
        pattern and cursor, the voices follow the moving set, and no per-step
        Python log line is printed.
        """

        args = build_snapshot_args(snapshot, self.engine.fundamental, glide_time)
        return self._send_args(
            args,
            snapshot,
            ui=ui,
            transient=transient,
            log=log,
        )

    def _send_args(
        self,
        args: list[float],
        snapshot: SetSnapshot,
        *,
        ui: bool,
        transient: bool,
        log: bool,
    ) -> bool:
        """Shared send path for :meth:`send_set` and :meth:`send_snapshot`."""

        sent = True
        try:
            self.client.send_message(FREQSET_ADDRESS, args)
        except (OSError, ValueError) as exc:
            print(f"[BRIDGE] send failed: {exc}", file=sys.stderr)
            sent = False

        # The MIDI rig receives the same snapshot, whether or not the sclang UDP
        # send succeeded.  Before MIDIRig.start it stores the initial snapshot;
        # start applies it so the drone and arp begin from the initial set.
        self._notify_midi(snapshot, transient=transient)

        # Stage 7: mirror the resulting state to any listening UI.  This is
        # best-effort and deliberately independent of the sclang send result.
        if ui:
            self._send_ui_state()

        if log and sent:
            print(
                f"[BRIDGE] -> {FREQSET_ADDRESS} classes={len(args) - 2} "
                f"glide={args[1]:.3f} fundamental={args[0]:.1f}"
            )
        return sent

    def _notify_midi(
        self, snapshot: SetSnapshot | None = None, *, transient: bool = False
    ) -> None:
        """Hand a snapshot to the MIDI rig; MIDI errors never break the bridge."""

        if self.midi is None:
            return
        snap = self.engine.snapshot() if snapshot is None else snapshot
        try:
            self.midi.on_set(
                ratios=list(snap.ratios),
                anchor=snap.anchor,
                fundamental=self.engine.fundamental,
                transient=transient,
            )
        except Exception as exc:  # deliberately broad: MIDI is non-critical
            print(f"[MIDI] set update failed: {exc}", file=sys.stderr)

    # ------------------------------------------------------------------
    # Stage 8.4.1: class glide
    # ------------------------------------------------------------------

    def _on_glide_step(self, snapshot: SetSnapshot, final: bool) -> None:
        """Scheduler callback: broadcast one intermediate or the final set."""

        if final:
            # ``cancel(snap=True)`` delivers the final set too; that is a snap,
            # not a completed melt, so it uses the 5 ms minimum and logs the
            # cancel line instead of "[GLIDE] done".  ``send_set`` keeps the
            # final broadcast byte-identical to a normal one-update change.
            cancelling = self._glide_cancelling
            self.send_set(
                glide_module.SNAP_GLIDE_SECONDS
                if cancelling
                else self._glide_duration
            )
            if not cancelling:
                print(f"[GLIDE] done ({self._glide_duration:.3f} s)")
            return
        # Intermediate step: the same fan-out, but silent and UI-free.  A
        # retune of one step interval keeps each 30 Hz step reachable without
        # lagging the whole melt.
        self.send_snapshot(
            snapshot,
            self._glide_step_seconds,
            ui=False,
            transient=True,
            log=False,
        )

    def _cancel_glide(self, reason: str) -> bool:
        """Snap an active glide to its final set and log one line.

        Called first by every overlapping set change (a new mutation, a return,
        a jump, Back).  A transport stop deliberately does not cancel a glide:
        it is a set morph, not a note.
        """

        self._glide_cancelling = True
        try:
            cancelled = self.glide.cancel(snap=True)
        finally:
            self._glide_cancelling = False
        if cancelled:
            print(f"[GLIDE] cancelled by {reason}")
        return cancelled

    def cancel_glide(self, reason: str = "load") -> bool:
        """Public hook for the Stage 8.5 state load (cancels with a snap)."""

        return self._cancel_glide(reason)

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
                if arp:
                    self._realign_arp()
            except Exception as exc:  # MIDI is non-critical; keep alive
                print(f"[MIDI] UI toggle failed: {exc}", file=sys.stderr)
            finally:
                self._send_midi_state()
                self._send_arp_state()

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
        """Reply to a UI handshake with the full Stage 7/8.1/8.2/8.3a snapshot."""

        self._send_ui_state()
        self._send_rhythm_state()
        self._send_drums_state()
        self._send_groove_state()
        self._send_grid_state()
        self._send_bass_state()
        self._send_bass_hits()
        self._send_breath_state()
        self._send_arp_state()
        self._send_lead_state()
        self._send_lead_input_state()
        self._send_melody_list()
        self._send_melody_state()
        self._send_drone_state()
        self._send_home_state()
        self._send_state_list()
        self._send_state_state()
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

    # -- Stage 8.3a bass status ------------------------------------------

    def _bass_rig(self) -> Any:
        return getattr(self.midi, "bass", None)

    def _send_bass_state(self) -> None:
        rig = self._bass_rig()
        if rig is not None:
            try:
                args = rig.state_args()
            except Exception as exc:  # keep the UI status alive
                print(f"[BASS] state failed: {exc}", file=sys.stderr)
                args = self._bass_default_state
        else:
            args = self._bass_default_state
        self._send_ui_message(BASS_STATE_ADDRESS, list(args))

    def _print_bass_state(self) -> None:
        rig = self._bass_rig()
        if rig is None:
            print("[BASS] not configured")
            return
        settings = rig.engine.settings
        print(
            f"[BASS] enabled={int(settings.enabled)} "
            f"port_ok={int(rig.port_ok)} preset={settings.preset} "
            f"grid={settings.grid} pitch={settings.pitch_mode}"
        )

    def _on_bass_note(
        self, hz: float, midi_note: int, velocity: int
    ) -> None:
        self._send_ui_message(
            BASS_NOTE_ADDRESS,
            [float(hz), int(midi_note), int(velocity)],
        )

    # -- Stage 8.7 breath status / controls ------------------------------

    def _send_breath_state(self) -> None:
        """Send one ``/ji/breath/state`` snapshot (never raises)."""

        try:
            args = self.breath.settings.state_args()
        except Exception as exc:  # keep the UI status alive
            print(f"[BREATH] state failed: {exc}", file=sys.stderr)
            args = breath_module.BreathSettings().state_args()
        self._send_ui_message(BREATH_STATE_ADDRESS, list(args))

    def _print_breath(self) -> None:
        settings = self.breath.settings
        print(
            f"[BREATH] {'on' if settings.enabled else 'off'} "
            f"depth={settings.depth:.2f} period={settings.period_bars} bars "
            f"shape={settings.shape} intensity={settings.intensity:.2f} "
            f"arp_local={settings.arp_local:.2f} "
            f"arp_sync={'on' if settings.arp_sync else 'off'}"
        )

    def _on_breath_change(self, name: str, value: Any) -> None:
        """Apply one immediate breath change, log it and echo the state."""

        try:
            settings = self.breath.settings.with_change(name, value)
            self.breath.set_settings(settings)
        except (TypeError, ValueError) as exc:
            print(f"[BREATH] invalid {name}: {exc}", file=sys.stderr)
            self._send_breath_state()
            return
        self._print_breath()
        self._send_breath_state()

    # -- Stage 8.7 bass hit row ------------------------------------------

    def _bass_hits_payload(self) -> list[int]:
        """``[enabled, count, hit_...]``; a well-formed ``[0, 0]`` without a rig."""

        rig = self._bass_rig()
        if rig is None:
            return [0, 0]
        getter = getattr(rig, "hits_payload", None)
        if not callable(getter):
            getter = getattr(self.midi, "bass_hits_payload", None)
        if not callable(getter):
            return [0, 0]
        try:
            payload = list(getter())
        except Exception as exc:  # keep the UI status alive
            print(f"[BASS] hits state failed: {exc}", file=sys.stderr)
            return [0, 0]
        return [int(item) for item in payload]

    def _send_bass_hits(self) -> None:
        self._send_ui_message(BASS_HITS_ADDRESS, self._bass_hits_payload())

    def _print_bass_hits(self, label: str) -> None:
        rig = self._bass_rig()
        if rig is None:
            return
        try:
            settings = rig.engine.settings
            count = len(rig.engine.effective_hits())
        except Exception:
            return
        print(
            f"[BASS] {label} row={settings.grid} steps={count}"
        )

    def _on_bass_hits_enabled(self, enabled: bool) -> None:
        """Enable/disable the hit row immediately (a performance gesture)."""

        rig = self._bass_rig()
        if rig is None:
            print(
                "[MIDI] bass unavailable: start the bridge with --bass and a "
                "configured IAC Driver Bus 4",
                file=sys.stderr,
            )
            self._send_bass_hits()
            return
        try:
            self._midi_call("set_bass_hits_enabled", bool(enabled))
        except (TypeError, ValueError, RuntimeError) as exc:
            print(f"[BASS] hits enabled failed: {exc}", file=sys.stderr)
        self._send_bass_hits()
        self._send_bass_state()
        self._print_bass_hits("hits enabled" if enabled else "hits off")

    def _on_bass_hit(self, index: int, on: bool) -> None:
        rig = self._bass_rig()
        if rig is None:
            self._send_bass_hits()
            return
        try:
            self._midi_call("set_bass_hit", int(index), bool(on))
        except (TypeError, ValueError, RuntimeError) as exc:
            print(f"[BASS] hit {index} failed: {exc}", file=sys.stderr)
        self._send_bass_hits()
        self._print_bass_hits(f"hit {int(index)} {'on' if on else 'off'}")

    def _on_bass_clear_hits(self) -> None:
        rig = self._bass_rig()
        if rig is None:
            self._send_bass_hits()
            return
        try:
            self._midi_call("clear_bass_hits")
        except (TypeError, ValueError, RuntimeError) as exc:
            print(f"[BASS] clear hits failed: {exc}", file=sys.stderr)
        self._send_bass_hits()
        self._print_bass_hits("hits cleared")

    def _on_bass_copy_hits(self) -> None:
        rig = self._bass_rig()
        if rig is None:
            self._send_bass_hits()
            return
        try:
            self._midi_call("copy_bass_hits")
        except (TypeError, ValueError, RuntimeError) as exc:
            print(f"[BASS] copy hits failed: {exc}", file=sys.stderr)
        self._send_bass_hits()
        self._print_bass_hits("hits copied")

    # -- Stage 8.3b arp state --------------------------------------------

    def _arp_rig(self) -> Any:
        return getattr(self.midi, "arp", None)

    def _send_arp_state(self) -> None:
        """Send one ``/ji/arp/state`` snapshot (never raises, never floods)."""

        if self._arp_rig() is None:
            args: list[Any] = list(self._arp_default_state)
        else:
            try:
                args = list(self.midi.arp_state())
            except Exception as exc:  # keep the UI status alive
                print(f"[ARP] state failed: {exc}", file=sys.stderr)
                args = list(self._arp_default_state)
        self._send_ui_message(ARP_STATE_ADDRESS, args)

    def _realign_arp(self) -> None:
        """Snap a beat-synced arp to the next division boundary.

        Called when sync is turned on, the division changes, the transport
        starts, and when the Drone/Arp switch re-enables the bus.  Free mode is
        a no-op, so the free arp never re-phases by itself.
        """

        rig = self._arp_rig()
        if rig is None:
            return
        try:
            settings = rig.settings
            quantum = arp_module.division_quarters(settings.division)
        except (TypeError, ValueError) as exc:
            print(f"[ARP] sync skipped: {exc}", file=sys.stderr)
            return
        if settings.sync_mode != "beat":
            return
        if self.rhythm.running:
            now = time.monotonic()
            target = float(self.rhythm.next_beat_time(quantum, at=now))
            delay = max(0.0, target - now)
            print(
                f"[ARP] sync: snapping to the next {settings.division} "
                f"boundary in {delay:.3f} s"
            )
        else:
            delay = 0.0
            print(
                f"[ARP] sync: transport stopped; the {settings.division} "
                "grid starts now"
            )
        self._midi_call("sync_arp_phase", delay)

    def _on_arp_change(self, name: str, value: Any) -> None:
        """Queue one validated arp setting for the next quarter beat."""

        rig = self._arp_rig()
        midi_enabled = (
            self.midi is not None
            and bool(getattr(self.midi, "midi_enabled", True))
        )
        if rig is None or not midi_enabled:
            print(
                "[MIDI] arp unavailable: start the bridge with --midi and a "
                "configured IAC Driver Bus 2",
                file=sys.stderr,
            )
            self._send_arp_state()
            return
        if not bool(getattr(self.midi, "arp_port_ok", False)):
            print(
                "[MIDI] arp unavailable: IAC Driver Bus 2 is not open; "
                "drone/drums/bass continue",
                file=sys.stderr,
            )
            self._send_arp_state()
            return

        try:
            # Validate before queueing so a bad value cannot poison the queue.
            # Gate is clamped against the *live* step, not the shipped default.
            key = arp_module.normalize_setting_name(name)
            rig.settings.with_change(key, value)
        except (TypeError, ValueError) as exc:
            print(f"[ARP] invalid {name}: {exc}", file=sys.stderr)
            self._send_arp_state()
            return

        def apply() -> None:
            try:
                self._midi_call("set_arp_setting", key, value)
            except (TypeError, ValueError, RuntimeError) as exc:
                print(f"[ARP] {key} failed: {exc}", file=sys.stderr)
            if key in ("sync_mode", "division"):
                self._realign_arp()
            self._send_arp_state()
            if self.rhythm.running:
                print(
                    f"[ARP] applied at beat "
                    f"{int(round(self.rhythm.beat_at()))}"
                )

        self.quantizer.schedule(
            ("arp", key),
            apply,
            phase=PHASE_ARP,
            label=f"arp {key}",
        )

    # -- Stage 8.3d lead --------------------------------------------------

    def _lead_rig(self) -> Any:
        return getattr(self.midi, "lead", None)

    def _send_lead_state(self) -> None:
        """One ``/ji/lead/state`` snapshot (never raises, never per note)."""

        if self._lead_rig() is None:
            args: list[Any] = list(self._lead_default_state)
        else:
            try:
                args = list(self.midi.lead_state())
            except Exception as exc:  # keep the UI status alive
                print(f"[LEAD] state failed: {exc}", file=sys.stderr)
                args = list(self._lead_default_state)
        self._send_ui_message(LEAD_STATE_ADDRESS, args)

    def _on_lead_note(self, hz: float, midi_note: int, velocity: int) -> None:
        self._send_ui_message(
            LEAD_NOTE_ADDRESS, [float(hz), int(midi_note), int(velocity)]
        )

    def _lead_available(self) -> bool:
        rig = self._lead_rig()
        return bool(rig is not None and getattr(rig, "port_ok", False))

    def _on_lead_change(self, name: str, value: Any) -> None:
        """Queue one validated lead setting for the next quarter beat."""

        if not self._lead_available():
            print(
                "[LEAD] unavailable: start the bridge with --lead and a "
                "configured IAC Driver Bus 5",
                file=sys.stderr,
            )
            self._send_lead_state()
            return
        try:
            # Validate before queueing so a bad value cannot poison the queue.
            lead_module.LeadSettings().with_change(name, value)
        except (TypeError, ValueError) as exc:
            print(f"[LEAD] invalid {name}: {exc}", file=sys.stderr)
            self._send_lead_state()
            return

        def apply() -> None:
            try:
                self._midi_call("set_lead_setting", name, value)
            except (TypeError, ValueError, RuntimeError) as exc:
                print(f"[LEAD] {name} failed: {exc}", file=sys.stderr)
            if (
                name == "enabled"
                and not bool(value)
                and self.lead_input is not None
            ):
                self.lead_input.release_all()
            self._send_lead_state()
            if self.rhythm.running:
                print(
                    f"[LEAD] applied at beat "
                    f"{int(round(self.rhythm.beat_at()))}"
                )

        self.quantizer.schedule(
            ("lead", name),
            apply,
            phase=PHASE_LEAD,
            label=f"lead {name}",
        )

    def _on_lead_vary(self) -> None:
        """Manual variation: the next quarter beat, or immediate when stopped."""

        if not self._lead_available():
            print(
                "[LEAD] unavailable: start the bridge with --lead and a "
                "configured IAC Driver Bus 5",
                file=sys.stderr,
            )
            self._send_lead_state()
            return

        def apply() -> None:
            try:
                self._midi_call("vary_lead")
            except (TypeError, ValueError, RuntimeError) as exc:
                print(f"[LEAD] vary failed: {exc}", file=sys.stderr)
            self._send_lead_state()
            if self.rhythm.running:
                print(
                    f"[LEAD] variation applied at beat "
                    f"{int(round(self.rhythm.beat_at()))}"
                )

        self.quantizer.schedule(
            ("lead", "vary"),
            apply,
            phase=PHASE_LEAD,
            label="lead vary",
        )

    # -- Stage 9.1 lead input and melodies --------------------------------

    def _melody_dir_label(self) -> str:
        directory = self.melodies.directory
        try:
            relative = directory.relative_to(state_module.PROJECT_ROOT)
        except ValueError:
            return str(directory)
        return f"{relative}/"

    def _print_melody_dir(self) -> None:
        print(
            f"[LEAD-IN] melody dir={self._melody_dir_label()} "
            f"melodies={self.melodies.count()}"
        )

    def _lead_input_settings(self) -> Any:
        try:
            settings = lead_input_module.load_config(self.lead_in_config)
            if self.lead_in_port:
                settings = settings.with_change("port", self.lead_in_port)
            return settings
        except (OSError, TypeError, ValueError) as exc:
            print(f"[LEAD-IN] disabled: {exc}", file=sys.stderr)
            return None

    def _start_lead_input(self) -> None:
        """Build and open the input rig (only with --lead-in and --lead)."""

        if not self.lead_in_requested:
            return
        rig = self._lead_rig()
        if rig is None or not bool(getattr(rig, "port_ok", False)):
            print("[LEAD-IN] unavailable: start with --lead for Bus 5")
            return
        settings = self._lead_input_settings()
        if settings is None:
            return
        try:
            self.lead_input = lead_input_module.LeadInputRig(
                rig,
                self.rhythm,
                settings,
                library=self.melodies,
                on_change=self._on_lead_input_change,
                on_note=self._on_lead_note,
            )
        except (TypeError, ValueError) as exc:
            print(f"[LEAD-IN] disabled: {exc}", file=sys.stderr)
            self.lead_input = None
            return
        self.lead_input.start()
        self._print_melody_dir()

    def _on_lead_input_change(self) -> None:
        """One status pair per record/settings change (never per note)."""

        self._send_lead_state()
        self._send_lead_input_state()

    def _send_lead_input_state(self) -> None:
        rig = self.lead_input
        if rig is not None:
            try:
                args: list[Any] = list(rig.state_args())
            except Exception as exc:  # keep the UI status alive
                print(f"[LEAD-IN] state failed: {exc}", file=sys.stderr)
                args = list(self._lead_input_default_state)
        else:
            args = list(self._lead_input_default_state)
            lead = self._lead_rig()
            if lead is not None:
                args[9] = lead.source
        self._send_ui_message(LEAD_INPUT_STATE_ADDRESS, args)

    def _on_lead_input_enabled(self, enabled: bool) -> None:
        """The Live toggle: immediate, and the scheduler is stopped not muted."""

        rig = self._lead_rig()
        if self.lead_input is None or rig is None:
            print(
                "[LEAD-IN] input unavailable: start with --lead-in --lead",
                file=sys.stderr,
            )
            self._send_lead_input_state()
            return
        if enabled:
            rig.set_source("live")
        else:
            if self.lead_input is not None:
                self.lead_input.release_all()
            name = "played" if rig.engine.motif.source == "played" else "generated"
            at_time: float | None = None
            if self.rhythm.running:
                state = self.rhythm.state
                bar = max(1e-9, bar_quarters(state.meter_num, state.meter_den))
                target = math.ceil(
                    (self.rhythm.beat_at() + 1e-9) / bar
                ) * bar
                at_time = self.rhythm.clock.time_of_beat(target)
                print(
                    f"[LEAD-IN] live off; phrase resumes at bar "
                    f"{int(round(target / bar)) + 1}"
                )
            else:
                print("[LEAD-IN] live off; phrase resumes on Play")
            rig.set_source(name, at_time=at_time)
        self._send_lead_state()
        self._send_lead_input_state()

    def _set_lead_input_setting(
        self, name: str, value: Any, label: str
    ) -> None:
        if self.lead_input is None:
            print("[LEAD-IN] input unavailable", file=sys.stderr)
            self._send_lead_input_state()
            return
        try:
            self.lead_input.set_setting(name, value)
        except (TypeError, ValueError) as exc:
            print(f"[LEAD-IN] invalid {label}: {exc}", file=sys.stderr)
            self._send_lead_input_state()
            return
        print(f"[LEAD-IN] {label}={value}")
        self._send_lead_input_state()

    def _on_lead_input_quantize(self, value: float) -> None:
        self._set_lead_input_setting("quantize", value, "quantize")

    def _on_lead_input_count_in(self, value: str) -> None:
        self._set_lead_input_setting("count_in", value, "count-in")

    def _on_lead_input_bars(self, value: int) -> None:
        self._set_lead_input_setting("bars", value, "bars")

    def _on_lead_input_note_on(self, midi: int, velocity: int) -> None:
        if self.lead_input is None:
            return
        try:
            self.lead_input.note_on(midi, velocity)
        except (TypeError, ValueError) as exc:
            print(f"[LEAD-IN] invalid note-on: {exc}", file=sys.stderr)

    def _on_lead_input_note_off(self, midi: int) -> None:
        if self.lead_input is None:
            return
        try:
            self.lead_input.note_off(midi)
        except (TypeError, ValueError) as exc:
            print(f"[LEAD-IN] invalid note-off: {exc}", file=sys.stderr)

    def _on_lead_input_record(self) -> None:
        if self.lead_input is None:
            print(
                "[LEAD-IN] record unavailable: start with --lead-in --lead",
                file=sys.stderr,
            )
            self._send_lead_input_state()
            return
        try:
            self.lead_input.start_record()
        except Exception as exc:  # the record supervisor is non-critical
            print(f"[LEAD-IN] record failed: {exc}", file=sys.stderr)
        self._send_lead_input_state()

    def _on_lead_input_stop(self) -> None:
        if self.lead_input is None:
            return
        self.lead_input.stop_record("user")
        self._send_lead_input_state()

    def _on_lead_input_refresh(self) -> None:
        """Close and re-open the configured input device (the GUI Refresh)."""

        if self.lead_input is None:
            print("[LEAD-IN] input unavailable", file=sys.stderr)
            self._send_lead_input_state()
            return
        try:
            self.lead_input.refresh()
        except Exception as exc:  # input is never fatal
            print(f"[LEAD-IN] refresh failed: {exc}", file=sys.stderr)
        self._send_lead_state()
        self._send_lead_input_state()

    def _on_lead_input_revert(self) -> None:
        """Revert to the recorded base: next beat or immediate when stopped."""

        if not self._lead_available():
            print(
                "[LEAD] unavailable: start the bridge with --lead and a "
                "configured IAC Driver Bus 5",
                file=sys.stderr,
            )
            self._send_lead_state()
            return

        def apply() -> None:
            rig = self._lead_rig()
            if rig is not None and rig.engine.motif.source == "played":
                rig.revert()
            else:
                print("[LEAD-IN] no played line to revert", file=sys.stderr)
            self._send_lead_state()
            self._send_lead_input_state()

        self.quantizer.schedule(
            ("lead", "revert"), apply, phase=PHASE_LEAD, label="lead revert"
        )

    def _on_lead_input_clear(self) -> None:
        """Clear the melody and return to the generated line."""

        if not self._lead_available():
            self._send_lead_state()
            return

        def apply() -> None:
            rig = self._lead_rig()
            if self.lead_input is not None:
                self.lead_input.release_all()
            if rig is not None:
                rig.clear_melody()
                rig.set_source("generated")
            print("[LEAD-IN] cleared the melody; generated line restored")
            self._send_lead_state()
            self._send_lead_input_state()

        self.quantizer.schedule(
            ("lead", "clear"), apply, phase=PHASE_LEAD, label="lead clear"
        )

    # -- melody library --------------------------------------------------

    def _set_melody_status(
        self,
        message: str | None = None,
        *,
        saved: str | None = None,
        loaded: str | None = None,
    ) -> None:
        with self._melody_lock:
            if message is not None:
                self._melody_message = str(message)
            if saved is not None:
                self._melody_last_saved = str(saved)
            if loaded is not None:
                self._melody_last_loaded = str(loaded)

    def _send_melody_list(self) -> None:
        try:
            names = list(self.melodies.names())
        except Exception as exc:
            print(f"[LEAD-IN] melody list failed: {exc}", file=sys.stderr)
            names = []
        self._send_ui_message(
            LEAD_MELODY_LIST_ADDRESS, [len(names), *names]
        )

    def _send_melody_state(self) -> None:
        with self._melody_lock:
            saved = self._melody_last_saved
            loaded = self._melody_last_loaded
            message = self._melody_message
        self._send_ui_message(
            LEAD_MELODY_STATE_ADDRESS,
            [int(self.melodies.count()), saved, loaded, message],
        )

    def _reply_melody(
        self,
        message: str | None = None,
        *,
        saved: str | None = None,
        loaded: str | None = None,
    ) -> None:
        self._set_melody_status(message, saved=saved, loaded=loaded)
        self._send_melody_list()
        self._send_melody_state()

    def _on_lead_melody_request(self) -> None:
        self._reply_melody("refreshed")

    def _on_lead_melody_save(self, name: str) -> None:
        """Save the current played take (immediate file op)."""

        try:
            valid = melody_module.validate_melody_name(name)
        except melody_module.MelodyError as exc:
            print(f"[LEAD-IN] invalid melody name {name!r}: {exc}")
            self._reply_melody(f"invalid melody: {exc}")
            return
        rig = self._lead_rig()
        if rig is None or not bool(getattr(rig, "port_ok", False)):
            print("[LEAD-IN] no lead output to save from", file=sys.stderr)
            self._reply_melody("lead unavailable")
            return
        motif = rig.engine.motif
        if motif.source != "played" or not motif.base_notes:
            print("[LEAD-IN] no played line to save", file=sys.stderr)
            self._reply_melody("no played line")
            return
        settings = rig.engine.settings
        quantize = (
            self.lead_input.settings.quantize
            if self.lead_input is not None
            else 1.0
        )
        try:
            melody = melody_module.Melody(
                name=valid,
                grid=settings.grid,
                phrase_bars=settings.phrase_bars,
                meter_num=motif.meter_num,
                meter_den=motif.meter_den,
                bpm=self.rhythm.bpm,
                quantize=quantize,
                swing=rig.engine.effective_swing(),
                notes=motif.notes,
                base_notes=motif.base_notes,
                history=motif.history,
                variation=motif.variation,
                variation_index=motif.variation_index,
                ratios=rig.engine.ratios,
                fundamental=rig.engine.fundamental,
            )
            self.melodies.save(melody)
        except (melody_module.MelodyError, TypeError, ValueError) as exc:
            print(f"[LEAD-IN] melody save failed: {exc}", file=sys.stderr)
            self._reply_melody(f"save failed: {exc}")
            return
        rig.engine.set_melody_name(valid)
        print(
            f'[LEAD-IN] melody saved "{valid}" ({len(melody.notes)} notes, '
            f"{self._melody_dir_label()})"
        )
        self._reply_melody("saved", saved=valid)
        self._send_lead_state()
        self._send_lead_input_state()

    def _on_lead_melody_load(self, name: str) -> None:
        """Load a melody: beat-quantized while running, immediate when stopped."""

        try:
            valid = melody_module.validate_melody_name(name)
        except melody_module.MelodyError as exc:
            print(f"[LEAD-IN] invalid melody name {name!r}: {exc}")
            self._reply_melody(f"invalid melody: {exc}")
            return
        self.quantizer.schedule(
            ("melody", "load"),
            lambda: self._apply_melody_load(valid),
            phase=PHASE_LEAD,
            label=f"melody load {valid}",
        )

    def _apply_melody_load(self, name: str) -> None:
        try:
            melody = self.melodies.load(name)
        except melody_module.MelodyError as exc:
            if exc.kind == "missing":
                print(
                    f'[LEAD-IN] gap: no melody named "{name}" '
                    f"({self.melodies.count()} saved)"
                )
                self._reply_melody("no such melody")
            else:
                print(f'[LEAD-IN] invalid melody "{name}": {exc}')
                self._reply_melody(f"invalid melody: {exc}")
            return
        rig = self._lead_rig()
        if rig is None or not bool(getattr(rig, "port_ok", False)):
            print("[LEAD] unavailable: start the bridge with --lead", file=sys.stderr)
            return
        meter = self.rhythm.state
        if (melody.meter_num, melody.meter_den) != (
            meter.meter_num,
            meter.meter_den,
        ):
            print(
                f"[LEAD-IN] melody meter {melody.meter_num}/{melody.meter_den} "
                f"differs from the bridge {meter.meter}; it will not align to "
                "the bar"
            )
        rig.load_motif(
            melody.notes,
            grid=melody.grid,
            phrase_bars=melody.phrase_bars,
            meter_num=melody.meter_num,
            meter_den=melody.meter_den,
            name=melody.name,
            base_notes=melody.base_notes,
            history=melody.history,
            variation=melody.variation,
            variation_index=melody.variation_index,
        )
        if self.lead_input is not None:
            self.lead_input.release_all()
        rig.set_source("played")
        print(
            f'[LEAD-IN] melody loaded "{melody.name}" ({len(melody.notes)} '
            f"notes, grid {melody.grid}, {melody.phrase_bars} bars)"
        )
        self._reply_melody("loaded", loaded=melody.name)
        self._send_lead_state()
        self._send_lead_input_state()

    def _on_lead_melody_delete(self, name: str) -> None:
        try:
            valid = melody_module.validate_melody_name(name)
        except melody_module.MelodyError as exc:
            print(f"[LEAD-IN] invalid melody name {name!r}: {exc}")
            self._reply_melody(f"invalid melody: {exc}")
            return
        try:
            deleted = self.melodies.delete(valid)
        except melody_module.MelodyError as exc:
            print(f"[LEAD-IN] melody delete failed: {exc}", file=sys.stderr)
            self._reply_melody(f"delete failed: {exc}")
            return
        if not deleted:
            print(
                f'[LEAD-IN] gap: no melody named "{valid}" '
                f"({self.melodies.count()} saved)"
            )
            self._reply_melody("no such melody")
            return
        with self._melody_lock:
            if self._melody_last_saved.casefold() == valid.casefold():
                self._melody_last_saved = ""
            if self._melody_last_loaded.casefold() == valid.casefold():
                self._melody_last_loaded = ""
        rig = self._lead_rig()
        if rig is not None and rig.engine.motif.melody_name == valid:
            rig.engine.set_melody_name("")
        print(f'[LEAD-IN] melody deleted "{valid}"')
        self._reply_melody("deleted")
        self._send_lead_state()
        self._send_lead_input_state()

    # -- Stage 8.3d drone modes -------------------------------------------

    def _drone_rig(self) -> Any:
        return getattr(self.midi, "drone_rig", None)

    def _send_drone_state(self) -> None:
        """One ``/ji/drone/state`` snapshot (never raises, never per note)."""

        if self._drone_rig() is None:
            args: list[Any] = list(self._drone_default_state)
        else:
            try:
                args = list(self.midi.drone_state())
            except Exception as exc:  # keep the UI status alive
                print(f"[DRONE] state failed: {exc}", file=sys.stderr)
                args = list(self._drone_default_state)
        self._send_ui_message(DRONE_STATE_ADDRESS, args)

    def _on_drone_change(self, name: str, value: Any) -> None:
        """Queue one validated drone mode/pad setting for the next beat."""

        if self._drone_rig() is None:
            print(
                "[MIDI] drone modes unavailable: start the bridge with --midi",
                file=sys.stderr,
            )
            self._send_drone_state()
            return
        try:
            drone_module.DroneSettings().with_change(name, value)
        except (TypeError, ValueError) as exc:
            print(f"[DRONE] invalid {name}: {exc}", file=sys.stderr)
            self._send_drone_state()
            return

        def apply() -> None:
            try:
                self._midi_call("set_drone_setting", name, value)
            except (TypeError, ValueError, RuntimeError) as exc:
                print(f"[DRONE] {name} failed: {exc}", file=sys.stderr)
            self._send_drone_state()
            if self.rhythm.running:
                print(
                    f"[DRONE] applied at beat "
                    f"{int(round(self.rhythm.beat_at()))}"
                )

        self.quantizer.schedule(
            ("drone", name),
            apply,
            phase=PHASE_DRONE,
            label=f"drone {name}",
        )

    def _push_lead_swing(self) -> None:
        """Push the drum groove's swing to the lead (one call per change)."""

        rig = self._lead_rig()
        if rig is None:
            return
        try:
            swing = float(self.groove.spec.swing)
        except Exception:
            return
        self._midi_call("set_lead_groove_swing", swing)

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
        # Stage 8.3a: the bass follows the same canonical tempo/meter/transport.
        self._midi_call("set_bass_bpm", state.bpm)
        self._midi_call("set_bass_meter", state.meter_num, state.meter_den)
        self._midi_call("set_bass_transport", state.running)
        # Stage 8.3c: the arp needs the canonical tempo for beat sync and for
        # its evolve clock (it is otherwise independent of the transport).
        self._midi_call("set_arp_bpm", state.bpm)
        self._midi_call("set_arp_transport", state.running)
        # Stage 8.3d: the lead shares the same clock domain (transport-gated,
        # bar-locked) and the drone modes are quarter-note based.
        self._midi_call("set_lead_bpm", state.bpm)
        self._midi_call("set_lead_meter", state.meter_num, state.meter_den)
        self._midi_call("set_lead_transport", state.running)
        self._midi_call("set_drone_bpm", state.bpm)
        self._midi_call("set_drone_meter", state.meter_num, state.meter_den)
        self._midi_call("set_drone_transport", state.running)
        # Stage 8.7: the one breath clock, shared with every voice scheduler.
        # Idempotent and safe to call again when a rig is created later.
        self.breath.set_tempo(state.bpm, state.meter_num, state.meter_den)
        self._midi_call("set_breath", self.breath)
        self._push_lead_swing()

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
        self._midi_call("set_bass_bpm", state.bpm)
        self._midi_call("set_arp_bpm", state.bpm)
        self._midi_call("set_lead_bpm", state.bpm)
        self._midi_call("set_drone_bpm", state.bpm)
        # Stage 8.7: re-anchor the breath phase at the new period (no jump).
        self.breath.set_tempo(state.bpm, state.meter_num, state.meter_den)
        # Recompute the pending actions' next beat at the new tempo; never drop
        # them and never apply them immediately just because the tempo moved.
        self.quantizer.tempo_changed()
        self._broadcast_rhythm()
        self._send_rhythm_state()
        # The arp's derived beat-sync step depends on the BPM, so refresh its
        # status once per tempo change (one small message, never per step).
        self._send_arp_state()
        # The derived bend cycle is tempo-dependent too; one small message.
        self._send_drone_state()
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
        self._midi_call("set_bass_meter", state.meter_num, state.meter_den)
        self._midi_call("set_lead_meter", state.meter_num, state.meter_den)
        self._midi_call("set_drone_meter", state.meter_num, state.meter_den)
        # The breath period is in bars and the bass hit row is one bar long, so
        # both follow a meter change.
        self.breath.set_tempo(state.bpm, state.meter_num, state.meter_den)
        self._broadcast_rhythm()
        self._send_rhythm_state()
        self._send_groove_state()
        self._send_grid_state()
        self._send_bass_state()
        self._send_bass_hits()
        self._send_lead_state()
        self._print_rhythm()

    def _on_rhythm_transport(self, running: bool) -> None:
        cancelled_return = False
        if not running:
            # A pending return is cancelled before the flush, so the stop does
            # not machine-gun the remaining batches (one per queue tick).
            cancelled_return = self._cancel_return(silent=True)
            # Flush before stopping: the last command is not lost, and the
            # queue cannot carry stale state into the next start.
            self.quantizer.flush("transport stop")
        state = self.rhythm.set_running(bool(running))
        if not running and self.lead_input is not None:
            # A transport stop finishes an in-flight take (kept), never restarts
            # the sequencer (owner decision 2: live keeps monitoring).
            self.lead_input.transport_stopped()
        if not running:
            # A flushed go/return may have started a return while the transport
            # was still running; drop its next step now.
            cancelled_return = self._cancel_return(silent=True) or cancelled_return
            if cancelled_return:
                print("[HOME] return cancelled by transport stop")
        self.quantizer.transport_changed()
        # Stopping the scheduler sends note-offs + CC 123; starting it resets
        # to step 0.  Drums run only while the transport is running.
        self._midi_call("set_drums_transport", state.running)
        self._midi_call("set_bass_transport", state.running)
        self._midi_call("set_arp_transport", state.running)
        # Stage 8.3d: the lead and the drone pulse/bend modes are gated by the
        # transport (the Stage 6 hold drone deliberately is not).
        self._midi_call("set_lead_transport", state.running)
        self._midi_call("set_drone_transport", state.running)
        if state.running:
            # A beat-synced arp locks to the new transport origin.
            self._realign_arp()
            # Stage 8.7: every take begins at the bottom of the inhale.  A stop
            # does not freeze the clock (a free-running arp keeps breathing).
            self.breath.reset()
        self._broadcast_rhythm()
        self._send_rhythm_state()
        self._send_bass_state()
        self._send_lead_state()
        self._send_lead_input_state()
        self._send_drone_state()
        self._send_home_state()
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

        self._cancel_return("mutation")
        self.quantizer.schedule(
            ("mutation",),
            self._mutate,
            phase=PHASE_MUTATION,
            label="mutation",
        )

    def request_octave(self) -> None:
        """UI/OSC/console octave request: next beat or immediate if stopped."""

        self._cancel_return("octave")
        self.quantizer.schedule(
            ("octave",),
            self._apply_octave,
            phase=PHASE_OCTAVE,
            label="octave",
        )

    def _apply_octave(self) -> None:
        # A go()/return queued for the same beat may have started a return in
        # the home phase; a manual octave move always cancels it.
        self._cancel_return("octave")
        self.server.octave()

    # ------------------------------------------------------------------
    # Stage 8.4 home sets, journeys, smooth return
    # ------------------------------------------------------------------

    def _send_home_state(self) -> None:
        """One ``/ji/home/state`` (13 values); never per note."""

        state = self.home.state(self.engine.snapshot())
        self._send_ui_message(HOME_STATE_ADDRESS, state.as_state_args())

    def _record_set(self) -> None:
        """Record the applied set in the bounded Back history."""

        self.home.record(self.engine.snapshot())

    def _cancel_return(
        self, reason: str | None = None, *, silent: bool = False
    ) -> bool:
        """Drop a pending return (state + queued step); log once when asked."""

        cancelled = self.home.cancel_return()
        if self.quantizer.cancel(("home", "step")):
            cancelled = True
        if self.quantizer.cancel(("home", "return")):
            cancelled = True
        if cancelled and not silent and reason:
            print(f"[HOME] return cancelled by {reason}")
        return cancelled

    # -- capture / clear / go ------------------------------------------

    def request_home_capture(self, slot: int) -> None:
        name = home_module.slot_name(slot)
        self.quantizer.schedule(
            ("home", "capture", int(slot)),
            lambda: self._capture_home(int(slot)),
            phase=PHASE_HOME,
            label=f"home capture {name}",
        )

    def _capture_home(self, slot: int) -> None:
        name = home_module.slot_name(slot)
        snapshot = self.home.capture(slot, self.engine.snapshot())
        print(
            f"[HOME] slot={name} captured classes={len(snapshot.ratios)} "
            f"anchor={_fmt_ratio(snapshot.anchor)}"
        )
        # A capture starts a reproducible journey from the configured seed.
        self.start_journey()
        self._send_home_state()

    def request_home_clear(self, slot: int) -> None:
        name = home_module.slot_name(slot)
        self.quantizer.schedule(
            ("home", "clear", int(slot)),
            lambda: self._clear_home(int(slot)),
            phase=PHASE_HOME,
            label=f"home clear {name}",
        )

    def _clear_home(self, slot: int) -> None:
        name = home_module.slot_name(slot)
        if self.home.clear(slot):
            print(f"[HOME] slot={name} cleared")
        else:
            print(f"[HOME] gap: slot={name} is empty")
        self._send_home_state()

    def request_home_go(self, slot: int) -> None:
        name = home_module.slot_name(slot)
        self.quantizer.schedule(
            ("home", "go", int(slot)),
            lambda: self._begin_return(int(slot), reason="go"),
            phase=PHASE_HOME,
            label=f"home go {name}",
        )

    def request_home_return(self) -> None:
        self.quantizer.schedule(
            ("home", "return"),
            lambda: self._begin_return(None, reason="return"),
            phase=PHASE_HOME,
            label="home return",
        )

    def request_home_jump(self) -> None:
        self.quantizer.schedule(
            ("home", "jump"),
            self._jump_home,
            phase=PHASE_HOME,
            label="home jump",
        )

    def request_back(self) -> None:
        self._cancel_return("back")
        self.quantizer.schedule(
            ("home", "back"),
            self._back_home,
            phase=PHASE_HOME,
            label="home back",
        )

    # -- journeys -------------------------------------------------------

    def start_journey(self, seed: int | None = None) -> bool:
        """Reseed the engine and reset the mutation counter for a journey."""

        if not self.home.start_journey(seed):
            print("[HOME] journey needs an active home (Set home first)")
            return False
        value = self.home.journey_seed
        self.engine.reseed(value)
        config = self.home.config
        print(
            f"[HOME] journey seed={value} every={config.every} "
            f"drift={config.max_drift} mutations=0/{config.every}"
        )
        return True

    def _after_mutation(self) -> None:
        """Bookkeeping after one announced mutation (history/journey/auto)."""

        self._record_set()
        if self.home.home is None:
            self._send_home_state()
            return
        self.home.note_mutation()
        config = self.home.config
        print(
            f"[HOME] journey seed={self.home.journey_seed} "
            f"every={config.every} drift={config.max_drift} "
            f"mutations={self.home.journey_mutations}/{config.every}"
        )
        self._send_home_state()
        if self.home.journey_due():
            self._queue_auto_return()

    def _queue_auto_return(self) -> None:
        """Queue the automatic return on the next beat (never mid-beat)."""

        self.quantizer.schedule(
            ("home", "return"),
            lambda: self._begin_return(None, reason="auto"),
            phase=PHASE_HOME,
            label="home return (auto)",
        )

    def _mutate(self) -> None:
        """Apply one mutation, honoring the optional drift cap.

        The cap is a bridge-layer policy: the engine's ``propose_mutation`` is
        untouched.  Each rejected proposal rolls the *state* back (the RNG keeps
        advancing, so the retries differ) and only the accepted trigger is
        announced.
        """

        self._cancel_return("mutation")
        # A new set change cancels an active class melt first: it snaps to the
        # previous change's final set, then this mutation starts its own glide.
        self._cancel_glide("mutation")
        before = self.engine.snapshot()
        self._pending_before = before
        try:
            config = self.home.config
            home = self.home.home
            if config.max_drift <= 0 or home is None:
                self.server.trigger()
                return

            accepted: MutationEvent | MutationBatch | None = None
            for attempt in range(home_module.MAX_DRIFT_TRIES):
                if attempt:
                    self.engine.apply_snapshot(before)
                accepted = self.server.apply_trigger()
                if accepted is None:
                    return
                if self.home.distance(self.engine.snapshot()) <= config.max_drift:
                    self.server.announce(accepted)
                    return
            self.server.announce(accepted)
            print(
                f"[HOME] drift cap exceeded "
                f"({home_module.MAX_DRIFT_TRIES} tries)"
            )
        finally:
            # ``_on_event`` consumes it on every announced path; clear it so a
            # failed apply can never glide against a stale snapshot.
            self._pending_before = None

    # -- return / jump / back ------------------------------------------

    def _begin_return(self, slot: int | None = None, reason: str = "return") -> bool:
        """Plan and start a smooth return (or apply it whole while stopped)."""

        # A return (or Go) is a new set change: cancel an active class melt
        # first.  Return/Go share the same log reason.
        self._cancel_glide("return")
        target_slot = self.home.active_slot if slot is None else int(slot)
        if target_slot < 0:
            print("[HOME] gap: no active home (Set home to capture)")
            self._send_home_state()
            return False
        name = home_module.slot_name(target_slot)
        target = self.home.slot_snapshot(target_slot)
        if target is None:
            print(f"[HOME] gap: slot={name} is empty")
            self._send_home_state()
            return False

        if self.home.returning:
            self._cancel_return(reason if reason == "go" else None)
        self.home.go(target_slot)
        current = self.engine.snapshot()
        plan = self.home.plan(
            current, target, self.engine.max_changes, self.home.journey_seed
        )
        distance = self.home.distance(current)
        print(
            f"[HOME] return {name} distance={distance} batches={len(plan)} "
            f"changes={self.engine.max_changes}"
        )

        if not plan:
            self._set_home_anchor(target)
            if self.engine.snapshot() != current:
                self.send_set(self.home.config.return_glide)
                self._record_set()
            print(f"[HOME] already at {name} (distance 0)")
            self.home.reset_journey()
            self._send_home_state()
            return True

        if not self.rhythm.running:
            # Compatibility rule: the whole return is one set change.
            for batch in plan:
                self._apply_return_batch(batch)
            self._set_home_anchor(target)
            self.send_set(self.home.config.return_glide)
            self._record_set()
            print(
                f"[HOME] returned to {name} immediately "
                f"({len(plan)} batches, transport stopped)"
            )
            self.home.reset_journey()
            self._send_home_state()
            return True

        # Running: batch 0 lands now (the request already sat on a beat); each
        # later batch reschedules itself on the following quarter beat.
        self.home.begin_return(target_slot, len(plan))
        self._send_home_state()
        self._apply_return_step(plan, 0, target, target_slot)
        return True

    def _apply_return_batch(
        self, batch: list[tuple[float | None, float | None]]
    ) -> None:
        for removed, added in batch:
            if removed is not None and added is not None:
                reason = "replaced"
            elif removed is not None:
                reason = "removed"
            else:
                reason = "added"
            self.engine.apply(
                MutationEvent(
                    removed_ratio=removed,
                    new_ratio=added,
                    glide_time=0.0,
                    reason=reason,
                )
            )

    def _set_home_anchor(self, target: Any) -> None:
        anchor = getattr(target, "anchor", None)
        if anchor is None:
            return
        try:
            self.engine.set_anchor(anchor)
        except (TypeError, ValueError):
            pass

    def _apply_return_step(
        self,
        plan: list[list[tuple[float | None, float | None]]],
        index: int,
        target: Any,
        slot: int,
    ) -> None:
        if not self.home.returning:
            return
        self._apply_return_batch(plan[index])
        last = index + 1 >= len(plan)
        if last:
            self._set_home_anchor(target)
        self.send_set(self.home.config.return_glide)
        self._record_set()
        self.home.note_return_step()
        name = home_module.slot_name(slot)
        if last:
            self.home.reset_journey()
            print(
                f"[HOME] returned to {name} in {len(plan)} beats "
                f"({len(plan)} batches)"
            )
            self._send_home_state()
            return
        self._send_home_state()
        self.quantizer.schedule(
            ("home", "step"),
            lambda: self._apply_return_step(plan, index + 1, target, slot),
            phase=PHASE_HOME,
            label=f"home return {index + 2}/{len(plan)}",
        )

    def _jump_home(self) -> None:
        """Instant exact set swap to the active home (not a smooth return)."""

        self._cancel_return("jump")
        self._cancel_glide("jump")
        slot = self.home.active_slot
        target = self.home.home
        if target is None or slot < 0:
            print("[HOME] gap: no active home (Set home to capture)")
            self._send_home_state()
            return
        self.engine.apply_snapshot(target)
        self.send_set(self.server.glide_time)
        self._record_set()
        print(
            f"[HOME] jumped to {home_module.slot_name(slot)} "
            f"classes={len(target.ratios)} anchor={_fmt_ratio(target.anchor)}"
        )
        self.home.reset_journey()
        self._send_home_state()

    def _back_home(self) -> None:
        """Restore the immediately previous snapshot (repeated Back walks back)."""

        # A go()/return queued for the same beat may have started a return in
        # the home phase; Back always cancels it first, and an active class
        # melt is a set change too.
        self._cancel_return("back")
        self._cancel_glide("back")
        target = self.home.back()
        if target is None:
            print("[HOME] back: already at the oldest snapshot")
            self._send_home_state()
            return
        self.engine.apply_snapshot(target)
        self.send_set(self.server.glide_time)
        print(
            f"[HOME] back -> classes={len(target.ratios)} "
            f"anchor={_fmt_ratio(target.anchor)} "
            f"(history {self.home.history_depth})"
        )
        self._send_home_state()

    # -- settings -------------------------------------------------------

    def _on_home_setting(self, name: str, value: Any) -> None:
        """Apply one validated home setting (immediate, not beat-quantized)."""

        try:
            if name == "auto":
                self.home.update_config(auto=bool(value))
                config = self.home.config
                print(
                    f"[HOME] auto={int(config.auto)} every={config.every} "
                    f"drift={config.max_drift}"
                )
                if config.auto and self.home.journey_due():
                    self._queue_auto_return()
            elif name == "every":
                self.home.update_config(every=value)
                print(f"[HOME] every={self.home.config.every}")
            elif name == "seed":
                self.home.update_config(seed=value)
                if self.home.home is not None:
                    self.start_journey(self.home.config.seed)
                else:
                    print(f"[HOME] seed={self.home.config.seed}")
            elif name == "max_drift":
                self.home.update_config(max_drift=value)
                print(f"[HOME] drift={self.home.config.max_drift}")
            elif name == "return_glide":
                self.home.update_config(return_glide=value)
                print(f"[HOME] glide={self.home.config.return_glide:.3f}")
            else:
                print(f"[HOME] unknown setting {name!r}", file=sys.stderr)
        except (TypeError, ValueError) as exc:
            print(f"[HOME] invalid {name}: {exc}", file=sys.stderr)
        self._send_home_state()

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

    # -- Stage 8.3a bass requests ----------------------------------------

    def _on_bass_change(self, name: str, value: Any) -> None:
        """Queue one validated bass setting for the next quarter beat."""

        rig = self._bass_rig()
        if rig is None or not rig.port_ok:
            print(
                "[MIDI] bass unavailable: start the bridge with --bass and a "
                "configured IAC Driver Bus 4",
                file=sys.stderr,
            )
            self._send_bass_state()
            return
        try:
            # Validate before queueing so a bad value cannot poison the queue.
            bass_module.BassSettings().with_change(name, value)
        except (TypeError, ValueError) as exc:
            print(f"[BASS] invalid {name}: {exc}", file=sys.stderr)
            self._send_bass_state()
            return

        def apply() -> None:
            try:
                self._midi_call("set_bass_setting", name, value)
            except (TypeError, ValueError, RuntimeError) as exc:
                print(f"[BASS] {name} failed: {exc}", file=sys.stderr)
            self._send_bass_state()
            self._print_bass_state()
            # A subdivision or preset change re-shapes the hit row to the bar.
            if str(name).strip().lower() in ("grid", "preset"):
                self._send_bass_hits()
            if self.rhythm.running:
                print(
                    f"[BASS] applied at beat "
                    f"{int(round(self.rhythm.beat_at()))}"
                )

        self.quantizer.schedule(
            ("bass", name),
            apply,
            phase=PHASE_BASS,
            label=f"bass {name}",
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
            # Stage 8.3d: one swing push per groove change (never per note).
            self._push_lead_swing()
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

    # ------------------------------------------------------------------
    # Stage 8.5 performance states
    # ------------------------------------------------------------------

    def _state_dir_label(self) -> str:
        directory = self.states.directory
        try:
            relative = directory.relative_to(state_module.PROJECT_ROOT)
        except ValueError:
            return str(directory)
        return f"{relative}/"

    def _print_state_dir(self) -> None:
        print(f"[STATE] dir={self._state_dir_label()} states={self.states.count()}")

    def _set_state_status(
        self,
        message: str | None = None,
        *,
        saved: str | None = None,
        loaded: str | None = None,
    ) -> None:
        with self._state_lock:
            if message is not None:
                self._state_message = str(message)
            if saved is not None:
                self._state_last_saved = str(saved)
            if loaded is not None:
                self._state_last_loaded = str(loaded)

    def _send_state_list(self) -> None:
        """One ``/ji/state/list`` ``[count, name_1 ... name_N]`` (sorted)."""

        try:
            names = list(self.states.names())
        except Exception as exc:  # a broken directory must not kill the UI feed
            print(f"[STATE] list failed: {exc}", file=sys.stderr)
            names = []
        self._send_ui_message(STATE_LIST_ADDRESS, [len(names), *names])

    def _send_state_state(self) -> None:
        """One ``/ji/state/state`` ``[count, last_saved, last_loaded, message]``."""

        with self._state_lock:
            saved = self._state_last_saved
            loaded = self._state_last_loaded
            message = self._state_message
        self._send_ui_message(
            STATE_STATE_ADDRESS,
            [int(self.states.count()), saved, loaded, message],
        )

    def _reply_state(
        self,
        message: str | None = None,
        *,
        saved: str | None = None,
        loaded: str | None = None,
    ) -> None:
        """Update the status string and send both reply messages."""

        self._set_state_status(message, saved=saved, loaded=loaded)
        self._send_state_list()
        self._send_state_state()

    # -- capture --------------------------------------------------------

    def capture_state(self, name: str) -> state_module.PerformanceState:
        """Build a validated :class:`PerformanceState` from the live rig.

        Missing buses are captured at their shipped defaults, so a state saved
        without ``--bass`` never invents a bass setting.
        """

        server = self.server
        bass_rig = self._bass_rig()
        arp_rig = self._arp_rig()
        lead_rig = self._lead_rig()
        drone_rig = self._drone_rig()
        home = self.home
        config = home.config
        enabled, _port_ok, rate, lane_mask = self._drums_snapshot()

        lead_settings = (
            lead_rig.engine.settings
            if lead_rig is not None
            else lead_module.LeadSettings()
        )
        lead_index = (
            int(lead_rig.engine.motif.variation_index)
            if lead_rig is not None
            else 0
        )
        lead_play = state_module.LeadPlaySection.create()
        if lead_rig is not None:
            motif = lead_rig.engine.motif
            if motif.source == "played" and motif.notes:
                # Self-contained: the take travels inline, so a recall needs no
                # melody file (owner decision 5).
                lead_play = state_module.LeadPlaySection.create(
                    notes=motif.notes,
                    base_notes=motif.base_notes or motif.notes,
                    history=motif.history,
                    melody=motif.melody_name,
                    source="played",
                )
        return state_module.PerformanceState.create(
            name=name,
            snapshot=self.engine.snapshot(),
            fundamental=self.engine.fundamental,
            saved_at=datetime.now().astimezone().isoformat(timespec="seconds"),
            mutation=state_module.MutationSettings.create(
                randomness=server.randomness,
                dissonance_bias=server.dissonance_bias,
                stepwise_bias=server.stepwise_bias,
                temperature=server.temperature,
                anchor_mobility=server.anchor_mobility,
                change_count=server.change_count,
                glide_time=server.glide_time,
                max_changes=self.engine.max_changes,
            ),
            home=state_module.HomeSection.create(
                slots=home.slots,
                active_slot=home.active_slot,
                auto=config.auto,
                every=config.every,
                seed=config.seed,
                max_drift=config.max_drift,
                return_glide=config.return_glide,
                journey_seed=home.journey_seed,
                journey_mutations=home.journey_mutations,
            ),
            rhythm=self.rhythm.state,
            drums=state_module.DrumsSection.create(
                enabled=enabled, rate=rate, lane_mask=lane_mask
            ),
            groove=self.groove.spec,
            grid=self.groove.grid,
            bass=(
                bass_rig.engine.settings
                if bass_rig is not None
                else bass_module.BassSettings()
            ),
            arp=(
                arp_rig.settings
                if arp_rig is not None
                else arp_module.ArpSettings()
            ),
            lead=lead_settings,
            lead_variation_index=lead_index,
            lead_play=lead_play,
            drone=(
                drone_rig.settings
                if drone_rig is not None
                else drone_module.DroneSettings()
            ),
        )

    # -- requests -------------------------------------------------------

    def request_state_save(self, name: str) -> None:
        """Save the live configuration under ``name`` (immediate file op)."""

        try:
            valid = state_module.validate_state_name(name)
        except state_module.StateError as exc:
            print(f"[STATE] invalid name {name!r}: {exc}")
            self._reply_state(f"invalid state: {exc}")
            return
        try:
            # Serialise against a load so a save never captures a half-applied
            # state (a load holds the same lock).
            with self._state_lock:
                state = self.capture_state(valid)
                self.states.save(state)
        except state_module.StateError as exc:
            print(f"[STATE] save failed: {exc}")
            self._reply_state(f"save failed: {exc}")
            return
        print(
            f"[STATE] saved {valid} "
            f"({state.field_count()} fields, {self._state_dir_label()})"
        )
        self._reply_state("saved", saved=valid)

    def request_state_load(self, name: str) -> None:
        """Queue one state recall for the next beat (immediate when stopped)."""

        try:
            valid = state_module.validate_state_name(name)
        except state_module.StateError as exc:
            print(f"[STATE] invalid name {name!r}: {exc}")
            self._reply_state(f"invalid state: {exc}")
            return
        self.quantizer.schedule(
            ("state", "load"),
            lambda: self._apply_state_load(valid),
            phase=PHASE_STATE,
            label=f"state load {valid}",
        )

    def request_state_delete(self, name: str) -> None:
        """Delete one state file (immediate; it changes no sound)."""

        try:
            valid = state_module.validate_state_name(name)
        except state_module.StateError as exc:
            print(f"[STATE] invalid name {name!r}: {exc}")
            self._reply_state(f"invalid state: {exc}")
            return
        try:
            deleted = self.states.delete(valid)
        except state_module.StateError as exc:
            print(f"[STATE] delete failed: {exc}")
            self._reply_state(f"delete failed: {exc}")
            return
        if not deleted:
            print(
                f'[STATE] gap: no state named "{valid}" '
                f"({self.states.count()} saved)"
            )
            self._reply_state("no such state")
            return
        with self._state_lock:
            if self._state_last_saved.casefold() == valid.casefold():
                self._state_last_saved = ""
            if self._state_last_loaded.casefold() == valid.casefold():
                self._state_last_loaded = ""
        print(f"[STATE] deleted {valid}")
        self._reply_state("deleted")

    def request_state_list(self) -> None:
        """Reply with a fresh list + status (immediate; changes no sound)."""

        self._reply_state("refreshed")

    # -- applying a load ------------------------------------------------

    def _preflight_state(self, state: state_module.PerformanceState) -> None:
        """Reject a state this bridge cannot apply *before* touching anything."""

        engine = self.engine
        if len(state.freqset.ratios) > engine.max_size:
            raise state_module.StateError(
                f"frequency set has {len(state.freqset.ratios)} classes; "
                f"this bridge allows {engine.max_size}"
            )
        for ratio, octave in state.freqset.octaves:
            if not engine.octave_min <= octave <= engine.octave_max:
                raise state_module.StateError(
                    f"analysis octave {octave:+d} for {ratio:.6f} is outside "
                    f"{engine.octave_min}..{engine.octave_max}"
                )

    def _restore_mutation(self, state: state_module.PerformanceState) -> None:
        server = self.server
        settings = state.mutation
        server.set_randomness(settings.randomness, log=False)
        server.set_dissonance_bias(settings.dissonance_bias, log=False)
        server.set_stepwise_bias(settings.stepwise_bias, log=False)
        server.set_temperature(settings.temperature, log=False)
        server.set_anchor_mobility(settings.anchor_mobility, log=False)
        server.set_glide_time(settings.glide_time, log=False)
        change_count = min(int(settings.change_count), self.engine.max_changes)
        if change_count != settings.change_count:
            print(
                f"[STATE] change_count {settings.change_count} clamped to "
                f"{change_count} (bridge --max-changes)"
            )
        server.set_change_count(change_count, log=False)

    def _restore_home(self, state: state_module.PerformanceState) -> None:
        home = self.home
        section = state.home
        home.update_config(
            auto=section.auto,
            every=section.every,
            seed=section.seed,
            max_drift=section.max_drift,
            return_glide=section.return_glide,
        )
        for index, slot in enumerate(section.slots):
            if slot is None:
                home.clear(index)
            else:
                home.capture(index, slot)
        if section.active_slot < 0:
            home.clear_active()
        else:
            home.set_active_slot(section.active_slot)
        home.restore_journey(section.journey_seed, section.journey_mutations)
        # A deterministic stream from the saved seed; a mid-journey RNG stream
        # is deliberately not resumed exactly (documented).
        self.engine.reseed(section.journey_seed)

    def _restore_rhythm(self, state: state_module.PerformanceState) -> None:
        rhythm = self.rhythm
        rhythm.set_bpm(state.rhythm.bpm)
        rhythm.set_meter(state.rhythm.meter_num, state.rhythm.meter_den)
        current = rhythm.state
        self._midi_call("set_drums_bpm", current.bpm)
        self._midi_call("set_bass_bpm", current.bpm)
        self._midi_call("set_arp_bpm", current.bpm)
        self._midi_call("set_lead_bpm", current.bpm)
        self._midi_call("set_drone_bpm", current.bpm)
        # A drum meter reloads the preset grid; the exact grid is forced below.
        self._midi_call("set_drums_meter", current.meter_num, current.meter_den)
        if self._drum_engine() is None:
            self.groove.set_meter(current.meter_num, current.meter_den)
        self._midi_call("set_bass_meter", current.meter_num, current.meter_den)
        self._midi_call("set_lead_meter", current.meter_num, current.meter_den)
        self._midi_call("set_drone_meter", current.meter_num, current.meter_den)
        self.breath.set_tempo(
            current.bpm, current.meter_num, current.meter_den
        )
        self._broadcast_rhythm()

    def _restore_groove(self, state: state_module.PerformanceState) -> None:
        engine = self._drum_engine()
        groove = self.groove
        spec = state.groove.spec
        if engine is not None:
            engine.set_preset(spec.preset)
            for name, value in (
                ("density", spec.density),
                ("syncopation", spec.syncopation),
                ("swing", spec.swing),
                ("probability", spec.probability),
            ):
                engine.set_groove_modifier(name, value)
            engine.set_seed(spec.seed)
        else:
            groove.set_preset(spec.preset)
            for name, value in (
                ("density", spec.density),
                ("syncopation", spec.syncopation),
                ("swing", spec.swing),
                ("probability", spec.probability),
            ):
                groove.set_modifier(name, value)
            groove.set_seed(spec.seed)

        target = state.groove.grid
        current = self.groove.grid
        for lane in range(LANE_COUNT):
            for step in range(32):
                wanted = target.is_on(lane, step)
                if wanted == current.is_on(lane, step):
                    continue
                if engine is not None:
                    engine.set_step(lane, step, wanted)
                else:
                    self.groove.set_cell(lane, step, wanted)

    def _lane_notes(self) -> list[int]:
        rig = getattr(self.midi, "drums", None)
        engine = getattr(rig, "engine", None)
        lanes = getattr(engine, "lanes", None)
        if lanes is not None:
            try:
                value = lanes() if callable(lanes) else lanes
                return [int(lane.note) for lane in value]
            except Exception:  # a fake/legacy rig falls back to the map
                pass
        return [36 + index for index in range(LANE_COUNT)]

    def _restore_drums(self, state: state_module.PerformanceState) -> None:
        enabled, port_ok, rate, lane_mask = self._drums_snapshot()
        drums = state.drums
        if not port_ok:
            print(
                "[STATE] drums skipped: Bus 3 not available; "
                "the rest of the state is applied"
            )
            return
        notes = self._lane_notes()
        if drums.lane_mask != lane_mask:
            for index in range(LANE_COUNT):
                wanted = bool(drums.lane_mask & (1 << index))
                if wanted != bool(lane_mask & (1 << index)):
                    self._midi_call("set_drums_lane", notes[index], wanted)
        if not math.isclose(drums.rate, rate, rel_tol=0.0, abs_tol=1e-9):
            self._midi_call("set_drums_rate", drums.rate)
        self._midi_call("set_drums_enabled", drums.enabled)

    def _midi_enabled(self) -> bool:
        return self.midi is not None and bool(
            getattr(self.midi, "midi_enabled", True)
        )

    def _arp_port_ok(self) -> bool:
        return bool(getattr(self.midi, "arp_port_ok", False))

    def _drone_port_ok(self) -> bool:
        return bool(getattr(self.midi, "drone_port_ok", False))

    def _restore_voices(self, state: state_module.PerformanceState) -> None:
        """Restore bass/arp/lead/drone; one missing bus degrades one voice."""

        midi_enabled = self._midi_enabled()

        bass_rig = self._bass_rig()
        if bass_rig is not None and bool(getattr(bass_rig, "port_ok", False)):
            self._midi_call("set_bass_settings", state.bass)
        else:
            print(
                "[STATE] bass skipped: Bus 4 not available; "
                "the rest of the state is applied"
            )

        if midi_enabled and self._arp_rig() is not None and self._arp_port_ok():
            self._midi_call("set_arp_settings", state.arp)
        else:
            print(
                "[STATE] arp skipped: Bus 2 not available; "
                "the rest of the state is applied"
            )

        lead_rig = self._lead_rig()
        if lead_rig is not None and bool(getattr(lead_rig, "port_ok", False)):
            self._midi_call("set_lead_settings", state.lead)
        else:
            print(
                "[STATE] lead skipped: Bus 5 not available; "
                "the rest of the state is applied"
            )

        drone_rig = self._drone_rig()
        if drone_rig is not None and midi_enabled and self._drone_port_ok():
            self._midi_call("set_drone_settings", state.drone)
        else:
            print(
                "[STATE] drone skipped: Bus 1 not available; "
                "the rest of the state is applied"
            )

    def _restore_lead_play(self, state: state_module.PerformanceState) -> None:
        """Recall the inline played line (or fall back to a generated one).

        Order matters (Stage 9.1): the settings/grid/phrase were applied by
        ``_restore_voices``; the motif is loaded here (the inline notes or the
        melody win), then the saved variation index is replayed.  The single
        ``send_set`` in ``_apply_state_load`` follows so the lead re-projects.
        """

        rig = self._lead_rig()
        if rig is None or not bool(getattr(rig, "port_ok", False)):
            return  # _restore_voices already logged the skipped lead
        if self.lead_input is not None:
            self.lead_input.release_all()
        # The history replay derives its new ratio hints from the engine's
        # current set, so give the lead the recalled set first.  ``send_set``
        # later re-projects the same snapshot (no second sound change).
        try:
            rig.on_set(state.freqset.ratios, state.freqset.anchor, state.fundamental)
        except Exception as exc:
            print(f"[LEAD] set restore failed: {exc}", file=sys.stderr)
        play = state.lead_play
        if play.notes:
            rig.load_motif(
                play.notes,
                grid=state.lead.grid,
                phrase_bars=state.lead.phrase_bars,
                meter_num=state.rhythm.meter_num,
                meter_den=state.rhythm.meter_den,
                name=play.melody,
                base_notes=play.base_notes,
                history=play.history,
                variation=state.lead.variation,
                variation_index=state.lead_variation_index,
            )
            rig.set_source("played")
            print(
                f"[LEAD] played line restored "
                f"({len(play.notes)} notes, source=played)"
            )
        else:
            rig.clear_melody()
            rig.set_source("generated")
        rig.set_variation_index(state.lead_variation_index, force=True)

    def _apply_state_values(self, state: state_module.PerformanceState) -> None:
        """Apply every restored value except the single set broadcast."""

        self.engine.apply_snapshot(state.freqset)
        # Fundamental is part of the frequency-set identity, not the snapshot.
        self.engine.fundamental = float(state.fundamental)
        if state.freqset.anchor is not None:
            self._set_home_anchor(state.freqset)
        self._restore_mutation(state)
        self._restore_home(state)
        self._restore_rhythm(state)
        self._restore_groove(state)
        self._restore_drums(state)
        self._restore_voices(state)
        self._restore_lead_play(state)
        self._push_lead_swing()

    def _apply_state_load(self, name: str) -> None:
        """One atomic recall: validate, restore, one set update, one log."""

        try:
            state = self.states.load(name)
        except state_module.StateError as exc:
            if exc.kind == "missing":
                print(
                    f'[STATE] gap: no state named "{name}" '
                    f"({self.states.count()} saved)"
                )
                self._reply_state("no such state")
            else:
                print(f'[STATE] invalid state "{name}": {exc}')
                self._reply_state(f"invalid state: {exc}")
            return

        try:
            self._preflight_state(state)
        except state_module.StateError as exc:
            print(f'[STATE] invalid state "{name}": {exc}')
            self._reply_state(f"invalid state: {exc}")
            return

        # A load is a set change: a pending return and an active class glide
        # must not overwrite it later.
        if self._cancel_return(silent=True):
            print("[STATE] return cancelled by state load")
        self._cancel_glide("state load")

        with self._state_lock:
            self._apply_state_values(state)
            # Exactly one set update, with the recalled glide time (a jump, not
            # a smooth return).
            self.send_set(state.mutation.glide_time)
            self._record_set()

        self._send_home_state()
        self._send_rhythm_state()
        self._send_drums_state()
        self._send_groove_state()
        self._send_grid_state()
        self._send_bass_state()
        self._send_bass_hits()
        self._send_arp_state()
        self._send_lead_state()
        self._send_lead_input_state()
        self._send_melody_list()
        self._send_drone_state()

        distance = self.home.distance(self.engine.snapshot())
        self._set_state_status("loaded", loaded=state.name)
        print(f"[STATE] loaded {state.name}")
        print(
            f"[STATE] load {state.name}: distance {distance}, 1 set update"
        )
        self._send_state_list()
        self._send_state_state()

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
        before = self._pending_before
        self._pending_before = None

        if before is not None and self._started:
            plan: glide_module.GlidePlan | None
            try:
                plan = glide_module.build_glide_plan(
                    before,
                    self.engine.snapshot(),
                    event,
                    float(event.glide_time),
                )
            except (TypeError, ValueError) as exc:
                print(f"[GLIDE] plan skipped: {exc}", file=sys.stderr)
                plan = None
            if plan is not None:
                self._start_glide(plan)
                self._print_event(event)
                # Stage 8.4: history, journey counter, /ji/home/state, auto
                # return.  The engine is already final; only the broadcast is
                # intermediate.  This runs once, immediately.
                self._after_mutation()
                return

        # No glide: one final set update.  Without a captured ``before`` (a
        # direct MutationOSCServer.trigger() outside the bridge) or before
        # bridge.start(), keep the accepted Stage 3 one-update path
        # byte-identical and drop any pending plan.
        if before is None or not self._started:
            self.glide.cancel(snap=False)
            self.send_set(float(event.glide_time))
            self._print_event(event)
            self._after_mutation()
            return

        # A pure add/remove snaps: the Glide knob is the replacement melt only,
        # so this update uses sclang's 5 ms minimum instead.
        self.glide.cancel(snap=False)
        self.send_set(glide_module.SNAP_GLIDE_SECONDS)
        self._print_event(event)
        # Stage 8.4: history, journey counter, /ji/home/state, auto return.
        self._after_mutation()

    def _start_glide(self, plan: glide_module.GlidePlan) -> None:
        """Log one line per glide, then start its bounded 30 Hz stream."""

        pairs = ", ".join(
            f"{old:.6f} -> {new:.6f}" for old, new in plan.pairs
        )
        print(
            f"[GLIDE] replaced {pairs} over {plan.duration:.3f} s "
            f"({plan.steps} steps)"
        )
        self._glide_duration = plan.duration
        self._glide_step_seconds = min(plan.duration, 1.0 / plan.rate)
        self.glide.start(plan)

    def _print_event(self, event: MutationEvent | MutationBatch) -> None:
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
        else:
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
            "Stage 3/6/7/8.1/8.3a/8.3b/8.3d/8.4/8.5 bridge: mutation engine -> "
            "sclang /ji/freqset/update, optional MPE MIDI rig (drone/arp "
            "parameters), drum and bass outputs, rhythm state, home sets and "
            "smooth return, performance states, and UI status"
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
    parser.add_argument(
        "--glide-time",
        type=float,
        default=0.15,
        help=(
            "stepwise/replaced class melt time in seconds (0 = instant); "
            "pure add/remove edits snap at 0.005 s"
        ),
    )
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
    parser.add_argument(
        "--bass",
        action="store_true",
        help=(
            "enable the Stage 8.3a monophonic bass MIDI output on "
            "IAC Driver Bus 4 (default: off; works with or without "
            "--midi/--drums)"
        ),
    )
    parser.add_argument(
        "--bass-config",
        default=None,
        help=(
            "bass config path "
            "(default: config/bass_config.json next to the project)"
        ),
    )
    parser.add_argument(
        "--lead",
        action=argparse.BooleanOptionalAction,
        default=False,
        help=(
            "enable the Stage 8.3d lead/melody output on IAC Driver Bus 5 "
            "(default: off; works with or without --midi/--drums/--bass)"
        ),
    )
    parser.add_argument(
        "--lead-config",
        default=None,
        help=(
            "lead config path "
            "(default: config/lead_config.json next to the project)"
        ),
    )
    parser.add_argument(
        "--drone-config",
        default=None,
        help=(
            "drone mode/pad config path "
            "(default: config/drone_config.json next to the project; the bus "
            "stays --mpe-config's drone_port)"
        ),
    )
    parser.add_argument(
        "--home-config",
        default=None,
        help=(
            "Stage 8.4 home config path "
            "(default: config/home_config.json next to the project: slots, "
            "auto, every, seed, max_drift, return_glide, history_depth)"
        ),
    )
    parser.add_argument(
        "--state-dir",
        default=None,
        help=(
            "Stage 8.5 performance-state directory "
            "(default: states/ next to the project; a relative path resolves "
            "from the project root)"
        ),
    )
    parser.add_argument(
        "--lead-in",
        action=argparse.BooleanOptionalAction,
        default=False,
        help=(
            "enable the Stage 9.1 MIDI/QWERTY input for the lead (default: "
            "off; ignored unless --lead is on)"
        ),
    )
    parser.add_argument(
        "--lead-in-config",
        default=None,
        help=(
            "lead input config path (default: config/lead_input_config.json: "
            "port, match, channel, quantize, count_in, bars, velocity_scale, "
            "octave_shift)"
        ),
    )
    parser.add_argument(
        "--lead-in-port",
        default=None,
        help="MIDI input name/substring (overrides the config's port)",
    )
    parser.add_argument(
        "--melody-dir",
        default=None,
        help=(
            "Stage 9.1 melody library directory (default: melodies/ next to "
            "the project; a relative path resolves from the project root)"
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
        bass=args.bass,
        bass_config=args.bass_config,
        lead=args.lead,
        lead_config=args.lead_config,
        drone_config=args.drone_config,
        home_config=args.home_config,
        state_dir=args.state_dir,
        lead_in=args.lead_in,
        lead_in_config=args.lead_in_config,
        lead_in_port=args.lead_in_port,
        melody_dir=args.melody_dir,
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

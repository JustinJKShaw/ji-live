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

``/ji/bass/set_*`` (Stage 8.3a)
    One validated bass setting each; the bridge queues it to the next quarter
    beat.  The callback receives ``(setting_name, value)``.
``/ji/arp/set_step`` (float seconds 0.05..2.0)
``/ji/arp/set_gate`` (float seconds 0.01..2.0, clamped to <= step)
``/ji/arp/set_velocity`` (int 1..127)
``/ji/arp/set_octaves`` (int shared, int non_shared_1, int non_shared_2, -8..8)
``/ji/arp/set_shared_mode`` (string ``anchor``|``random``)
``/ji/arp/set_order`` (string ``forward``|``reverse``|``pingpong``|``random``)
``/ji/arp/set_seed`` (int 0..2**31-1)
    Stage 8.3b arp parameters.  They never enable/disable the arp: the Stage 7.1
    Drone/Arp switches on ``/ji/midi/set_enabled`` stay the only enable path.

``/ji/lead/set_*`` (Stage 8.3d)
    One validated lead setting each, plus ``/ji/lead/vary`` (no argument) which
    advances one variation.  The callback receives ``(setting_name, value)``;
    ``vary`` calls ``on_lead_vary``.  Quantization is the bridge's job.

``/ji/drone/set_mode`` and ``/ji/drone/set_*`` (Stage 8.3d)
    The drone mode (``hold``/``pulse``/``bend``) and its pad parameters.  They
    never enable/disable the drone: the top-row Drone switch stays the only mute.

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
    from . import arp, bass, drone, groove, lead, rhythm
    from .mutation import MutationBatch, MutationEngine, MutationEvent, OctaveEvent
except ImportError:  # pragma: no cover - exercised only by direct execution
    import arp  # type: ignore[no-redef]
    import bass  # type: ignore[no-redef]
    import drone  # type: ignore[no-redef]
    import groove  # type: ignore[no-redef]
    import lead  # type: ignore[no-redef]
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
# Stage 8.2 groove controls (UI -> Bridge).  Quantization is the bridge's job.
DRUMS_SET_PRESET_ADDRESS = "/ji/drums/set_preset"
DRUMS_SET_DENSITY_ADDRESS = "/ji/drums/set_density"
DRUMS_SET_SYNCOPATION_ADDRESS = "/ji/drums/set_syncopation"
DRUMS_SET_SWING_ADDRESS = "/ji/drums/set_swing"
DRUMS_SET_PROBABILITY_ADDRESS = "/ji/drums/set_probability"
DRUMS_SET_STEP_ADDRESS = "/ji/drums/set_step"
DRUMS_SET_SEED_ADDRESS = "/ji/drums/set_seed"
DRUMS_RESET_GRID_ADDRESS = "/ji/drums/reset_grid"
# Stage 8.3a bass controls (UI -> Bridge).  Quantization is the bridge's job;
# handlers validate and pass ``(setting_name, value)`` to the bridge.
BASS_SET_ENABLED_ADDRESS = "/ji/bass/set_enabled"
BASS_SET_PRESET_ADDRESS = "/ji/bass/set_preset"
BASS_SET_GRID_ADDRESS = "/ji/bass/set_grid"
BASS_SET_LENGTH_ADDRESS = "/ji/bass/set_length"
BASS_SET_DENSITY_ADDRESS = "/ji/bass/set_density"
BASS_SET_SYNCOPATION_ADDRESS = "/ji/bass/set_syncopation"
BASS_SET_SWING_ADDRESS = "/ji/bass/set_swing"
BASS_SET_GATE_ADDRESS = "/ji/bass/set_gate"
BASS_SET_ACCENT_ADDRESS = "/ji/bass/set_accent"
BASS_SET_ROOT_DOWNBEAT_ADDRESS = "/ji/bass/set_root_downbeat"
BASS_SET_PITCH_MODE_ADDRESS = "/ji/bass/set_pitch_mode"
BASS_SET_MOVEMENT_ADDRESS = "/ji/bass/set_movement"
BASS_SET_OCTAVE_RANGE_ADDRESS = "/ji/bass/set_octave_range"
BASS_SET_OCTAVE_JUMP_ADDRESS = "/ji/bass/set_octave_jump"
BASS_SET_BASE_OCTAVE_ADDRESS = "/ji/bass/set_base_octave"
BASS_SET_SEED_ADDRESS = "/ji/bass/set_seed"
BASS_SET_SUB_ENABLED_ADDRESS = "/ji/bass/set_sub_enabled"
BASS_SET_SUB_OCTAVES_ADDRESS = "/ji/bass/set_sub_octaves"
# Stage 8.3b arp parameters (UI -> Bridge).  The Drone/Arp enable switches stay
# on /ji/midi/set_enabled; these only change the arp settings.  Quantization is
# the bridge's job.
ARP_SET_STEP_ADDRESS = "/ji/arp/set_step"
ARP_SET_GATE_ADDRESS = "/ji/arp/set_gate"
ARP_SET_VELOCITY_ADDRESS = "/ji/arp/set_velocity"
ARP_SET_OCTAVES_ADDRESS = "/ji/arp/set_octaves"
ARP_SET_SHARED_MODE_ADDRESS = "/ji/arp/set_shared_mode"
ARP_SET_ORDER_ADDRESS = "/ji/arp/set_order"
ARP_SET_SEED_ADDRESS = "/ji/arp/set_seed"
# Stage 8.3c: beat sync + self-evolution.
ARP_SET_SYNC_ADDRESS = "/ji/arp/set_sync"
ARP_SET_DIVISION_ADDRESS = "/ji/arp/set_division"
ARP_SET_EVOLVE_ADDRESS = "/ji/arp/set_evolve"
ARP_SET_EVOLVE_BEATS_ADDRESS = "/ji/arp/set_evolve_beats"
ARP_SET_EVOLVE_DEPTH_ADDRESS = "/ji/arp/set_evolve_depth"
# Stage 8.3d lead controls (UI -> Bridge).  Quantization is the bridge's job.
LEAD_SET_ENABLED_ADDRESS = "/ji/lead/set_enabled"
LEAD_SET_GRID_ADDRESS = "/ji/lead/set_grid"
LEAD_SET_PHRASE_BARS_ADDRESS = "/ji/lead/set_phrase_bars"
LEAD_SET_DENSITY_ADDRESS = "/ji/lead/set_density"
LEAD_SET_GATE_ADDRESS = "/ji/lead/set_gate"
LEAD_SET_VELOCITY_ADDRESS = "/ji/lead/set_velocity"
LEAD_SET_OCTAVE_RANGE_ADDRESS = "/ji/lead/set_octave_range"
LEAD_SET_BASE_OCTAVE_ADDRESS = "/ji/lead/set_base_octave"
LEAD_SET_VARIATION_ADDRESS = "/ji/lead/set_variation"
LEAD_SET_VARIATION_BARS_ADDRESS = "/ji/lead/set_variation_bars"
LEAD_SET_SWING_ADDRESS = "/ji/lead/set_swing"
LEAD_SET_GROOVE_LINK_ADDRESS = "/ji/lead/set_groove_link"
LEAD_SET_SEED_ADDRESS = "/ji/lead/set_seed"
LEAD_VARY_ADDRESS = "/ji/lead/vary"
# Stage 8.3d drone mode/pad controls (UI -> Bridge).
DRONE_SET_MODE_ADDRESS = "/ji/drone/set_mode"
DRONE_SET_VELOCITY_ADDRESS = "/ji/drone/set_velocity"
DRONE_SET_PULSE_NOTES_ADDRESS = "/ji/drone/set_pulse_notes"
DRONE_SET_PULSE_GRID_ADDRESS = "/ji/drone/set_pulse_grid"
DRONE_SET_PULSE_GATE_ADDRESS = "/ji/drone/set_pulse_gate"
DRONE_SET_PULSE_SPAN_ADDRESS = "/ji/drone/set_pulse_span"
DRONE_SET_SLIDE_DIRECTION_ADDRESS = "/ji/drone/set_slide_direction"
DRONE_SET_SLIDE_SEMITONES_ADDRESS = "/ji/drone/set_slide_semitones"
DRONE_SET_SLIDE_SECONDS_ADDRESS = "/ji/drone/set_slide_seconds"
DRONE_SET_HOLD_BEATS_ADDRESS = "/ji/drone/set_hold_beats"
DRONE_SET_SEED_ADDRESS = "/ji/drone/set_seed"
DRONE_SET_SEQ_NOTES_ADDRESS = "/ji/drone/set_seq_notes"
DRONE_SET_SEQ_BARS_ADDRESS = "/ji/drone/set_seq_bars"
DRONE_SET_SEQ_GATE_ADDRESS = "/ji/drone/set_seq_gate"


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
    DRUMS_SET_PRESET_ADDRESS = DRUMS_SET_PRESET_ADDRESS
    DRUMS_SET_DENSITY_ADDRESS = DRUMS_SET_DENSITY_ADDRESS
    DRUMS_SET_SYNCOPATION_ADDRESS = DRUMS_SET_SYNCOPATION_ADDRESS
    DRUMS_SET_SWING_ADDRESS = DRUMS_SET_SWING_ADDRESS
    DRUMS_SET_PROBABILITY_ADDRESS = DRUMS_SET_PROBABILITY_ADDRESS
    DRUMS_SET_STEP_ADDRESS = DRUMS_SET_STEP_ADDRESS
    DRUMS_SET_SEED_ADDRESS = DRUMS_SET_SEED_ADDRESS
    DRUMS_RESET_GRID_ADDRESS = DRUMS_RESET_GRID_ADDRESS
    BASS_SET_ENABLED_ADDRESS = BASS_SET_ENABLED_ADDRESS
    BASS_SET_PRESET_ADDRESS = BASS_SET_PRESET_ADDRESS
    BASS_SET_GRID_ADDRESS = BASS_SET_GRID_ADDRESS
    BASS_SET_LENGTH_ADDRESS = BASS_SET_LENGTH_ADDRESS
    BASS_SET_DENSITY_ADDRESS = BASS_SET_DENSITY_ADDRESS
    BASS_SET_SYNCOPATION_ADDRESS = BASS_SET_SYNCOPATION_ADDRESS
    BASS_SET_SWING_ADDRESS = BASS_SET_SWING_ADDRESS
    BASS_SET_GATE_ADDRESS = BASS_SET_GATE_ADDRESS
    BASS_SET_ACCENT_ADDRESS = BASS_SET_ACCENT_ADDRESS
    BASS_SET_ROOT_DOWNBEAT_ADDRESS = BASS_SET_ROOT_DOWNBEAT_ADDRESS
    BASS_SET_PITCH_MODE_ADDRESS = BASS_SET_PITCH_MODE_ADDRESS
    BASS_SET_MOVEMENT_ADDRESS = BASS_SET_MOVEMENT_ADDRESS
    BASS_SET_OCTAVE_RANGE_ADDRESS = BASS_SET_OCTAVE_RANGE_ADDRESS
    BASS_SET_OCTAVE_JUMP_ADDRESS = BASS_SET_OCTAVE_JUMP_ADDRESS
    BASS_SET_BASE_OCTAVE_ADDRESS = BASS_SET_BASE_OCTAVE_ADDRESS
    BASS_SET_SEED_ADDRESS = BASS_SET_SEED_ADDRESS
    BASS_SET_SUB_ENABLED_ADDRESS = BASS_SET_SUB_ENABLED_ADDRESS
    BASS_SET_SUB_OCTAVES_ADDRESS = BASS_SET_SUB_OCTAVES_ADDRESS
    ARP_SET_STEP_ADDRESS = ARP_SET_STEP_ADDRESS
    ARP_SET_GATE_ADDRESS = ARP_SET_GATE_ADDRESS
    ARP_SET_VELOCITY_ADDRESS = ARP_SET_VELOCITY_ADDRESS
    ARP_SET_OCTAVES_ADDRESS = ARP_SET_OCTAVES_ADDRESS
    ARP_SET_SHARED_MODE_ADDRESS = ARP_SET_SHARED_MODE_ADDRESS
    ARP_SET_ORDER_ADDRESS = ARP_SET_ORDER_ADDRESS
    ARP_SET_SEED_ADDRESS = ARP_SET_SEED_ADDRESS
    ARP_SET_SYNC_ADDRESS = ARP_SET_SYNC_ADDRESS
    ARP_SET_DIVISION_ADDRESS = ARP_SET_DIVISION_ADDRESS
    ARP_SET_EVOLVE_ADDRESS = ARP_SET_EVOLVE_ADDRESS
    ARP_SET_EVOLVE_BEATS_ADDRESS = ARP_SET_EVOLVE_BEATS_ADDRESS
    ARP_SET_EVOLVE_DEPTH_ADDRESS = ARP_SET_EVOLVE_DEPTH_ADDRESS
    LEAD_SET_ENABLED_ADDRESS = LEAD_SET_ENABLED_ADDRESS
    LEAD_SET_GRID_ADDRESS = LEAD_SET_GRID_ADDRESS
    LEAD_SET_PHRASE_BARS_ADDRESS = LEAD_SET_PHRASE_BARS_ADDRESS
    LEAD_SET_DENSITY_ADDRESS = LEAD_SET_DENSITY_ADDRESS
    LEAD_SET_GATE_ADDRESS = LEAD_SET_GATE_ADDRESS
    LEAD_SET_VELOCITY_ADDRESS = LEAD_SET_VELOCITY_ADDRESS
    LEAD_SET_OCTAVE_RANGE_ADDRESS = LEAD_SET_OCTAVE_RANGE_ADDRESS
    LEAD_SET_BASE_OCTAVE_ADDRESS = LEAD_SET_BASE_OCTAVE_ADDRESS
    LEAD_SET_VARIATION_ADDRESS = LEAD_SET_VARIATION_ADDRESS
    LEAD_SET_VARIATION_BARS_ADDRESS = LEAD_SET_VARIATION_BARS_ADDRESS
    LEAD_SET_SWING_ADDRESS = LEAD_SET_SWING_ADDRESS
    LEAD_SET_GROOVE_LINK_ADDRESS = LEAD_SET_GROOVE_LINK_ADDRESS
    LEAD_SET_SEED_ADDRESS = LEAD_SET_SEED_ADDRESS
    LEAD_VARY_ADDRESS = LEAD_VARY_ADDRESS
    DRONE_SET_MODE_ADDRESS = DRONE_SET_MODE_ADDRESS
    DRONE_SET_VELOCITY_ADDRESS = DRONE_SET_VELOCITY_ADDRESS
    DRONE_SET_PULSE_NOTES_ADDRESS = DRONE_SET_PULSE_NOTES_ADDRESS
    DRONE_SET_PULSE_GRID_ADDRESS = DRONE_SET_PULSE_GRID_ADDRESS
    DRONE_SET_PULSE_GATE_ADDRESS = DRONE_SET_PULSE_GATE_ADDRESS
    DRONE_SET_PULSE_SPAN_ADDRESS = DRONE_SET_PULSE_SPAN_ADDRESS
    DRONE_SET_SLIDE_DIRECTION_ADDRESS = DRONE_SET_SLIDE_DIRECTION_ADDRESS
    DRONE_SET_SLIDE_SEMITONES_ADDRESS = DRONE_SET_SLIDE_SEMITONES_ADDRESS
    DRONE_SET_SLIDE_SECONDS_ADDRESS = DRONE_SET_SLIDE_SECONDS_ADDRESS
    DRONE_SET_HOLD_BEATS_ADDRESS = DRONE_SET_HOLD_BEATS_ADDRESS
    DRONE_SET_SEED_ADDRESS = DRONE_SET_SEED_ADDRESS
    DRONE_SET_SEQ_NOTES_ADDRESS = DRONE_SET_SEQ_NOTES_ADDRESS
    DRONE_SET_SEQ_BARS_ADDRESS = DRONE_SET_SEQ_BARS_ADDRESS
    DRONE_SET_SEQ_GATE_ADDRESS = DRONE_SET_SEQ_GATE_ADDRESS

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
        on_mutation_request: Callable[[], None] | None = None,
        on_octave_request: Callable[[], None] | None = None,
        on_drums_preset: Callable[[str], None] | None = None,
        on_drums_density: Callable[[float], None] | None = None,
        on_drums_syncopation: Callable[[float], None] | None = None,
        on_drums_swing: Callable[[float], None] | None = None,
        on_drums_probability: Callable[[float], None] | None = None,
        on_drums_step: Callable[[int, int, bool], None] | None = None,
        on_drums_seed: Callable[[int], None] | None = None,
        on_drums_reset_grid: Callable[[], None] | None = None,
        on_bass_change: Callable[[str, Any], None] | None = None,
        on_arp_change: Callable[[str, Any], None] | None = None,
        on_lead_change: Callable[[str, Any], None] | None = None,
        on_lead_vary: Callable[[], None] | None = None,
        on_drone_change: Callable[[str, Any], None] | None = None,
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
            ("on_mutation_request", on_mutation_request),
            ("on_octave_request", on_octave_request),
            ("on_drums_preset", on_drums_preset),
            ("on_drums_density", on_drums_density),
            ("on_drums_syncopation", on_drums_syncopation),
            ("on_drums_swing", on_drums_swing),
            ("on_drums_probability", on_drums_probability),
            ("on_drums_step", on_drums_step),
            ("on_drums_seed", on_drums_seed),
            ("on_drums_reset_grid", on_drums_reset_grid),
            ("on_bass_change", on_bass_change),
            ("on_arp_change", on_arp_change),
            ("on_lead_change", on_lead_change),
            ("on_lead_vary", on_lead_vary),
            ("on_drone_change", on_drone_change),
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
        self._on_mutation_request = on_mutation_request
        self._on_octave_request = on_octave_request
        self._on_drums_preset = on_drums_preset
        self._on_drums_density = on_drums_density
        self._on_drums_syncopation = on_drums_syncopation
        self._on_drums_swing = on_drums_swing
        self._on_drums_probability = on_drums_probability
        self._on_drums_step = on_drums_step
        self._on_drums_seed = on_drums_seed
        self._on_drums_reset_grid = on_drums_reset_grid
        self._on_bass_change = on_bass_change
        self._on_arp_change = on_arp_change
        self._on_lead_change = on_lead_change
        self._on_lead_vary = on_lead_vary
        self._on_drone_change = on_drone_change

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
        dispatcher.map(self.DRUMS_SET_PRESET_ADDRESS, self._handle_drums_preset)
        dispatcher.map(
            self.DRUMS_SET_DENSITY_ADDRESS, self._handle_drums_density
        )
        dispatcher.map(
            self.DRUMS_SET_SYNCOPATION_ADDRESS,
            self._handle_drums_syncopation,
        )
        dispatcher.map(self.DRUMS_SET_SWING_ADDRESS, self._handle_drums_swing)
        dispatcher.map(
            self.DRUMS_SET_PROBABILITY_ADDRESS,
            self._handle_drums_probability,
        )
        dispatcher.map(self.DRUMS_SET_STEP_ADDRESS, self._handle_drums_step)
        dispatcher.map(self.DRUMS_SET_SEED_ADDRESS, self._handle_drums_seed)
        dispatcher.map(
            self.DRUMS_RESET_GRID_ADDRESS, self._handle_drums_reset_grid
        )
        # Stage 8.3a bass controls.
        bass_map = (
            (self.BASS_SET_ENABLED_ADDRESS, self._handle_bass_enabled),
            (self.BASS_SET_PRESET_ADDRESS, self._handle_bass_preset),
            (self.BASS_SET_GRID_ADDRESS, self._handle_bass_grid),
            (self.BASS_SET_LENGTH_ADDRESS, self._handle_bass_length),
            (self.BASS_SET_DENSITY_ADDRESS, self._handle_bass_density),
            (self.BASS_SET_SYNCOPATION_ADDRESS, self._handle_bass_syncopation),
            (self.BASS_SET_SWING_ADDRESS, self._handle_bass_swing),
            (self.BASS_SET_GATE_ADDRESS, self._handle_bass_gate),
            (self.BASS_SET_ACCENT_ADDRESS, self._handle_bass_accent),
            (
                self.BASS_SET_ROOT_DOWNBEAT_ADDRESS,
                self._handle_bass_root_downbeat,
            ),
            (self.BASS_SET_PITCH_MODE_ADDRESS, self._handle_bass_pitch_mode),
            (self.BASS_SET_MOVEMENT_ADDRESS, self._handle_bass_movement),
            (
                self.BASS_SET_OCTAVE_RANGE_ADDRESS,
                self._handle_bass_octave_range,
            ),
            (
                self.BASS_SET_OCTAVE_JUMP_ADDRESS,
                self._handle_bass_octave_jump,
            ),
            (self.BASS_SET_BASE_OCTAVE_ADDRESS, self._handle_bass_base_octave),
            (self.BASS_SET_SEED_ADDRESS, self._handle_bass_seed),
            (self.BASS_SET_SUB_ENABLED_ADDRESS, self._handle_bass_sub_enabled),
            (self.BASS_SET_SUB_OCTAVES_ADDRESS, self._handle_bass_sub_octaves),
        )
        for address, handler in bass_map:
            dispatcher.map(address, handler)
        # Stage 8.3b arp parameters.
        arp_map = (
            (self.ARP_SET_STEP_ADDRESS, self._handle_arp_step),
            (self.ARP_SET_GATE_ADDRESS, self._handle_arp_gate),
            (self.ARP_SET_VELOCITY_ADDRESS, self._handle_arp_velocity),
            (self.ARP_SET_OCTAVES_ADDRESS, self._handle_arp_octaves),
            (self.ARP_SET_SHARED_MODE_ADDRESS, self._handle_arp_shared_mode),
            (self.ARP_SET_ORDER_ADDRESS, self._handle_arp_order),
            (self.ARP_SET_SEED_ADDRESS, self._handle_arp_seed),
            (self.ARP_SET_SYNC_ADDRESS, self._handle_arp_sync),
            (self.ARP_SET_DIVISION_ADDRESS, self._handle_arp_division),
            (self.ARP_SET_EVOLVE_ADDRESS, self._handle_arp_evolve),
            (self.ARP_SET_EVOLVE_BEATS_ADDRESS, self._handle_arp_evolve_beats),
            (self.ARP_SET_EVOLVE_DEPTH_ADDRESS, self._handle_arp_evolve_depth),
        )
        for address, handler in arp_map:
            dispatcher.map(address, handler)
        # Stage 8.3d lead parameters and the manual variation trigger.
        lead_map = (
            (self.LEAD_SET_ENABLED_ADDRESS, self._handle_lead_enabled),
            (self.LEAD_SET_GRID_ADDRESS, self._handle_lead_grid),
            (self.LEAD_SET_PHRASE_BARS_ADDRESS, self._handle_lead_phrase_bars),
            (self.LEAD_SET_DENSITY_ADDRESS, self._handle_lead_density),
            (self.LEAD_SET_GATE_ADDRESS, self._handle_lead_gate),
            (self.LEAD_SET_VELOCITY_ADDRESS, self._handle_lead_velocity),
            (self.LEAD_SET_OCTAVE_RANGE_ADDRESS, self._handle_lead_octave_range),
            (self.LEAD_SET_BASE_OCTAVE_ADDRESS, self._handle_lead_base_octave),
            (self.LEAD_SET_VARIATION_ADDRESS, self._handle_lead_variation),
            (
                self.LEAD_SET_VARIATION_BARS_ADDRESS,
                self._handle_lead_variation_bars,
            ),
            (self.LEAD_SET_SWING_ADDRESS, self._handle_lead_swing),
            (self.LEAD_SET_GROOVE_LINK_ADDRESS, self._handle_lead_groove_link),
            (self.LEAD_SET_SEED_ADDRESS, self._handle_lead_seed),
            (self.LEAD_VARY_ADDRESS, self._handle_lead_vary),
        )
        for address, handler in lead_map:
            dispatcher.map(address, handler)
        # Stage 8.3d drone mode/pad parameters.
        drone_map = (
            (self.DRONE_SET_MODE_ADDRESS, self._handle_drone_mode),
            (self.DRONE_SET_VELOCITY_ADDRESS, self._handle_drone_velocity),
            (self.DRONE_SET_PULSE_NOTES_ADDRESS, self._handle_drone_pulse_notes),
            (self.DRONE_SET_PULSE_GRID_ADDRESS, self._handle_drone_pulse_grid),
            (self.DRONE_SET_PULSE_GATE_ADDRESS, self._handle_drone_pulse_gate),
            (self.DRONE_SET_PULSE_SPAN_ADDRESS, self._handle_drone_pulse_span),
            (
                self.DRONE_SET_SLIDE_DIRECTION_ADDRESS,
                self._handle_drone_slide_direction,
            ),
            (
                self.DRONE_SET_SLIDE_SEMITONES_ADDRESS,
                self._handle_drone_slide_semitones,
            ),
            (
                self.DRONE_SET_SLIDE_SECONDS_ADDRESS,
                self._handle_drone_slide_seconds,
            ),
            (self.DRONE_SET_HOLD_BEATS_ADDRESS, self._handle_drone_hold_beats),
            (self.DRONE_SET_SEED_ADDRESS, self._handle_drone_seed),
            (
                self.DRONE_SET_SEQ_NOTES_ADDRESS,
                self._handle_drone_seq_notes,
            ),
            (
                self.DRONE_SET_SEQ_BARS_ADDRESS,
                self._handle_drone_seq_bars,
            ),
            (
                self.DRONE_SET_SEQ_GATE_ADDRESS,
                self._handle_drone_seq_gate,
            ),
        )
        for address, handler in drone_map:
            dispatcher.map(address, handler)
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
        callback = self._on_mutation_request
        if callback is not None:
            self._call_callback(callback, "mutation request")
            return
        self.trigger()

    def _handle_octave(self, _address: str, *args: Any) -> None:
        if args:
            _warn(f"{self.OCTAVE_ADDRESS} takes no arguments; ignoring extras")
        callback = self._on_octave_request
        if callback is not None:
            self._call_callback(callback, "octave request")
            return
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

    # -- Stage 8.2 groove controls ---------------------------------------

    def _handle_drums_preset(self, _address: str, *args: Any) -> None:
        if len(args) != 1:
            _warn(
                f"{self.DRUMS_SET_PRESET_ADDRESS} expects one preset name; "
                f"got {len(args)} arguments"
            )
            return
        try:
            preset = groove.validate_preset(args[0])
        except (TypeError, ValueError) as exc:
            _warn(f"{self.DRUMS_SET_PRESET_ADDRESS}: {exc}")
            return
        self._call_callback(self._on_drums_preset, "drums preset", preset)

    def _handle_groove_modifier(
        self, args: tuple[Any, ...], address: str, label: str, callback: Any
    ) -> None:
        value = _one_float(args, address)
        if value is None:
            return
        if not 0.0 <= value <= 1.0:
            _warn(f"{address}: {value:.3f} clamped to 0..1")
        try:
            clamped = groove.clamp_unit(value, label)
        except (TypeError, ValueError) as exc:
            _warn(f"{address}: {exc}")
            return
        self._call_callback(callback, label, clamped)

    def _handle_drums_density(self, _address: str, *args: Any) -> None:
        self._handle_groove_modifier(
            args,
            self.DRUMS_SET_DENSITY_ADDRESS,
            "density",
            self._on_drums_density,
        )

    def _handle_drums_syncopation(self, _address: str, *args: Any) -> None:
        self._handle_groove_modifier(
            args,
            self.DRUMS_SET_SYNCOPATION_ADDRESS,
            "syncopation",
            self._on_drums_syncopation,
        )

    def _handle_drums_swing(self, _address: str, *args: Any) -> None:
        self._handle_groove_modifier(
            args,
            self.DRUMS_SET_SWING_ADDRESS,
            "swing",
            self._on_drums_swing,
        )

    def _handle_drums_probability(self, _address: str, *args: Any) -> None:
        self._handle_groove_modifier(
            args,
            self.DRUMS_SET_PROBABILITY_ADDRESS,
            "probability",
            self._on_drums_probability,
        )

    def _handle_drums_step(self, _address: str, *args: Any) -> None:
        if len(args) != 3:
            _warn(
                f"{self.DRUMS_SET_STEP_ADDRESS} expects lane, step and 0/1; "
                f"got {len(args)} arguments"
            )
            return
        try:
            lane = groove.validate_lane(_one_int(args[0], "lane"))
            step = groove.validate_step(_one_int(args[1], "step"))
            enabled = _one_bool(args[2], "step enabled")
        except (TypeError, ValueError) as exc:
            _warn(f"{self.DRUMS_SET_STEP_ADDRESS}: {exc}")
            return
        self._call_callback(
            self._on_drums_step, "drums step", lane, step, enabled
        )

    def _handle_drums_seed(self, _address: str, *args: Any) -> None:
        if len(args) != 1:
            _warn(
                f"{self.DRUMS_SET_SEED_ADDRESS} expects one seed; "
                f"got {len(args)} arguments"
            )
            return
        try:
            seed = groove.validate_seed(_one_int(args[0], "seed"))
        except (TypeError, ValueError) as exc:
            _warn(f"{self.DRUMS_SET_SEED_ADDRESS}: {exc}")
            return
        self._call_callback(self._on_drums_seed, "drums seed", seed)

    def _handle_drums_reset_grid(self, _address: str, *args: Any) -> None:
        if args:
            _warn(
                f"{self.DRUMS_RESET_GRID_ADDRESS} takes no arguments; "
                f"ignoring extras"
            )
        self._call_callback(self._on_drums_reset_grid, "drums reset grid")

    # -- Stage 8.3a bass --------------------------------------------------

    def _handle_bass_setting(
        self,
        args: tuple[Any, ...],
        address: str,
        key: str,
        parser: Callable[[Any], Any],
    ) -> None:
        if len(args) != 1:
            _warn(f"{address} expects one value; got {len(args)} arguments")
            return
        try:
            value = parser(args[0])
        except (TypeError, ValueError) as exc:
            _warn(f"{address}: {exc}")
            return
        self._call_callback(self._on_bass_change, "bass change", key, value)

    def _handle_bass_enabled(self, _address: str, *args: Any) -> None:
        self._handle_bass_setting(
            args, self.BASS_SET_ENABLED_ADDRESS, "enabled", bass.validate_bool
        )

    def _handle_bass_preset(self, _address: str, *args: Any) -> None:
        self._handle_bass_setting(
            args, self.BASS_SET_PRESET_ADDRESS, "preset", bass.validate_preset
        )

    def _handle_bass_grid(self, _address: str, *args: Any) -> None:
        self._handle_bass_setting(
            args, self.BASS_SET_GRID_ADDRESS, "grid", _bass_grid_value
        )

    def _handle_bass_length(self, _address: str, *args: Any) -> None:
        self._handle_bass_setting(
            args,
            self.BASS_SET_LENGTH_ADDRESS,
            "length_steps",
            lambda value: bass.validate_length(_one_int(value, "length")),
        )

    def _handle_bass_density(self, _address: str, *args: Any) -> None:
        self._handle_bass_setting(
            args,
            self.BASS_SET_DENSITY_ADDRESS,
            "density",
            lambda value: bass.clamp_unit(value, "density"),
        )

    def _handle_bass_syncopation(self, _address: str, *args: Any) -> None:
        self._handle_bass_setting(
            args,
            self.BASS_SET_SYNCOPATION_ADDRESS,
            "syncopation",
            lambda value: bass.clamp_unit(value, "syncopation"),
        )

    def _handle_bass_swing(self, _address: str, *args: Any) -> None:
        self._handle_bass_setting(
            args,
            self.BASS_SET_SWING_ADDRESS,
            "swing",
            lambda value: bass.clamp_unit(value, "swing"),
        )

    def _handle_bass_gate(self, _address: str, *args: Any) -> None:
        self._handle_bass_setting(
            args, self.BASS_SET_GATE_ADDRESS, "gate_beats", bass.validate_gate_beats
        )

    def _handle_bass_accent(self, _address: str, *args: Any) -> None:
        self._handle_bass_setting(
            args,
            self.BASS_SET_ACCENT_ADDRESS,
            "accent",
            lambda value: bass.clamp_unit(value, "accent"),
        )

    def _handle_bass_root_downbeat(self, _address: str, *args: Any) -> None:
        self._handle_bass_setting(
            args,
            self.BASS_SET_ROOT_DOWNBEAT_ADDRESS,
            "root_downbeat",
            lambda value: bass.validate_bool(value, "root_downbeat"),
        )

    def _handle_bass_pitch_mode(self, _address: str, *args: Any) -> None:
        self._handle_bass_setting(
            args,
            self.BASS_SET_PITCH_MODE_ADDRESS,
            "pitch_mode",
            bass.validate_pitch_mode,
        )

    def _handle_bass_movement(self, _address: str, *args: Any) -> None:
        self._handle_bass_setting(
            args,
            self.BASS_SET_MOVEMENT_ADDRESS,
            "movement",
            lambda value: bass.clamp_unit(value, "movement"),
        )

    def _handle_bass_octave_range(self, _address: str, *args: Any) -> None:
        self._handle_bass_setting(
            args,
            self.BASS_SET_OCTAVE_RANGE_ADDRESS,
            "octave_range",
            bass.validate_octave_range,
        )

    def _handle_bass_octave_jump(self, _address: str, *args: Any) -> None:
        self._handle_bass_setting(
            args,
            self.BASS_SET_OCTAVE_JUMP_ADDRESS,
            "octave_jump",
            lambda value: bass.clamp_unit(value, "octave_jump"),
        )

    def _handle_bass_base_octave(self, _address: str, *args: Any) -> None:
        self._handle_bass_setting(
            args,
            self.BASS_SET_BASE_OCTAVE_ADDRESS,
            "base_octave",
            bass.validate_base_octave,
        )

    def _handle_bass_seed(self, _address: str, *args: Any) -> None:
        self._handle_bass_setting(
            args,
            self.BASS_SET_SEED_ADDRESS,
            "seed",
            lambda value: bass.validate_seed(_one_int(value, "seed")),
        )

    def _handle_bass_sub_enabled(self, _address: str, *args: Any) -> None:
        self._handle_bass_setting(
            args,
            self.BASS_SET_SUB_ENABLED_ADDRESS,
            "sub_enabled",
            lambda value: bass.validate_bool(value, "sub_enabled"),
        )

    def _handle_bass_sub_octaves(self, _address: str, *args: Any) -> None:
        self._handle_bass_setting(
            args,
            self.BASS_SET_SUB_OCTAVES_ADDRESS,
            "sub_octaves",
            lambda value: bass.validate_sub_octaves(
                _one_int(value, "sub_octaves")
            ),
        )

    # -- Stage 8.3b arp parameters ----------------------------------------

    def _handle_arp_setting(
        self,
        args: tuple[Any, ...],
        address: str,
        key: str,
        parser: Callable[[Any], Any],
        *,
        arity: int = 1,
    ) -> None:
        if len(args) != arity:
            _warn(
                f"{address} expects {arity} value(s); got {len(args)} arguments"
            )
            return
        try:
            if arity == 1:
                value = parser(args[0])
            else:
                value = parser(*args)
        except (TypeError, ValueError) as exc:
            _warn(f"{address}: {exc}")
            return
        self._call_callback(self._on_arp_change, "arp change", key, value)

    def _handle_arp_step(self, _address: str, *args: Any) -> None:
        self._handle_arp_setting(
            args, self.ARP_SET_STEP_ADDRESS, "step_seconds",
            arp.validate_step_seconds,
        )

    def _handle_arp_gate(self, _address: str, *args: Any) -> None:
        self._handle_arp_setting(
            args, self.ARP_SET_GATE_ADDRESS, "gate_seconds",
            arp.validate_gate_seconds,
        )

    def _handle_arp_velocity(self, _address: str, *args: Any) -> None:
        self._handle_arp_setting(
            args, self.ARP_SET_VELOCITY_ADDRESS, "velocity",
            arp.validate_velocity,
        )

    def _handle_arp_octaves(self, _address: str, *args: Any) -> None:
        self._handle_arp_setting(
            args,
            self.ARP_SET_OCTAVES_ADDRESS,
            "octave_offsets",
            lambda shared, first, second: arp.validate_octave_offsets(
                [shared, first, second]
            ),
            arity=3,
        )

    def _handle_arp_shared_mode(self, _address: str, *args: Any) -> None:
        self._handle_arp_setting(
            args, self.ARP_SET_SHARED_MODE_ADDRESS, "shared_mode",
            arp.validate_shared_mode,
        )

    def _handle_arp_order(self, _address: str, *args: Any) -> None:
        self._handle_arp_setting(
            args, self.ARP_SET_ORDER_ADDRESS, "order", arp.validate_order
        )

    def _handle_arp_seed(self, _address: str, *args: Any) -> None:
        self._handle_arp_setting(
            args, self.ARP_SET_SEED_ADDRESS, "seed", arp.validate_seed
        )

    def _handle_arp_sync(self, _address: str, *args: Any) -> None:
        self._handle_arp_setting(
            args, self.ARP_SET_SYNC_ADDRESS, "sync_mode", arp.validate_sync_mode
        )

    def _handle_arp_division(self, _address: str, *args: Any) -> None:
        self._handle_arp_setting(
            args,
            self.ARP_SET_DIVISION_ADDRESS,
            "division",
            arp.validate_division,
        )

    def _handle_arp_evolve(self, _address: str, *args: Any) -> None:
        self._handle_arp_setting(
            args, self.ARP_SET_EVOLVE_ADDRESS, "evolve", arp.validate_evolve
        )

    def _handle_arp_evolve_beats(self, _address: str, *args: Any) -> None:
        self._handle_arp_setting(
            args,
            self.ARP_SET_EVOLVE_BEATS_ADDRESS,
            "evolve_beats",
            arp.validate_evolve_beats,
        )

    def _handle_arp_evolve_depth(self, _address: str, *args: Any) -> None:
        self._handle_arp_setting(
            args,
            self.ARP_SET_EVOLVE_DEPTH_ADDRESS,
            "evolve_depth",
            arp.validate_evolve_depth,
        )

    # -- Stage 8.3d lead --------------------------------------------------

    def _handle_lead_setting(
        self,
        args: tuple[Any, ...],
        address: str,
        key: str,
        parser: Callable[[Any], Any],
    ) -> None:
        if len(args) != 1:
            _warn(f"{address} expects one value; got {len(args)} arguments")
            return
        try:
            value = parser(args[0])
        except (TypeError, ValueError) as exc:
            _warn(f"{address}: {exc}")
            return
        self._call_callback(self._on_lead_change, "lead change", key, value)

    def _handle_lead_enabled(self, _address: str, *args: Any) -> None:
        self._handle_lead_setting(
            args, self.LEAD_SET_ENABLED_ADDRESS, "enabled",
            lambda value: lead.validate_bool(value, "enabled"),
        )

    def _handle_lead_grid(self, _address: str, *args: Any) -> None:
        self._handle_lead_setting(
            args, self.LEAD_SET_GRID_ADDRESS, "grid", lead.validate_grid
        )

    def _handle_lead_phrase_bars(self, _address: str, *args: Any) -> None:
        self._handle_lead_setting(
            args,
            self.LEAD_SET_PHRASE_BARS_ADDRESS,
            "phrase_bars",
            lead.validate_phrase_bars,
        )

    def _handle_lead_density(self, _address: str, *args: Any) -> None:
        self._handle_lead_setting(
            args, self.LEAD_SET_DENSITY_ADDRESS, "density", lead.validate_density
        )

    def _handle_lead_gate(self, _address: str, *args: Any) -> None:
        self._handle_lead_setting(
            args,
            self.LEAD_SET_GATE_ADDRESS,
            "gate_beats",
            lead.validate_gate_beats,
        )

    def _handle_lead_velocity(self, _address: str, *args: Any) -> None:
        self._handle_lead_setting(
            args, self.LEAD_SET_VELOCITY_ADDRESS, "velocity", lead.validate_velocity
        )

    def _handle_lead_octave_range(self, _address: str, *args: Any) -> None:
        self._handle_lead_setting(
            args,
            self.LEAD_SET_OCTAVE_RANGE_ADDRESS,
            "octave_range",
            lead.validate_octave_range,
        )

    def _handle_lead_base_octave(self, _address: str, *args: Any) -> None:
        self._handle_lead_setting(
            args,
            self.LEAD_SET_BASE_OCTAVE_ADDRESS,
            "base_octave",
            lead.validate_base_octave,
        )

    def _handle_lead_variation(self, _address: str, *args: Any) -> None:
        self._handle_lead_setting(
            args,
            self.LEAD_SET_VARIATION_ADDRESS,
            "variation",
            lead.validate_variation,
        )

    def _handle_lead_variation_bars(self, _address: str, *args: Any) -> None:
        self._handle_lead_setting(
            args,
            self.LEAD_SET_VARIATION_BARS_ADDRESS,
            "variation_bars",
            lead.validate_variation_bars,
        )

    def _handle_lead_swing(self, _address: str, *args: Any) -> None:
        self._handle_lead_setting(
            args,
            self.LEAD_SET_SWING_ADDRESS,
            "swing",
            lambda value: lead.clamp_unit(value, "swing"),
        )

    def _handle_lead_groove_link(self, _address: str, *args: Any) -> None:
        self._handle_lead_setting(
            args,
            self.LEAD_SET_GROOVE_LINK_ADDRESS,
            "groove_link",
            lambda value: lead.validate_bool(value, "groove_link"),
        )

    def _handle_lead_seed(self, _address: str, *args: Any) -> None:
        self._handle_lead_setting(
            args, self.LEAD_SET_SEED_ADDRESS, "seed", lead.validate_seed
        )

    def _handle_lead_vary(self, _address: str, *args: Any) -> None:
        if args:
            _warn(f"{self.LEAD_VARY_ADDRESS} takes no arguments; ignoring extras")
        self._call_callback(self._on_lead_vary, "lead vary")

    # -- Stage 8.3d drone modes -------------------------------------------

    def _handle_drone_setting(
        self,
        args: tuple[Any, ...],
        address: str,
        key: str,
        parser: Callable[[Any], Any],
    ) -> None:
        if len(args) != 1:
            _warn(f"{address} expects one value; got {len(args)} arguments")
            return
        try:
            value = parser(args[0])
        except (TypeError, ValueError) as exc:
            _warn(f"{address}: {exc}")
            return
        self._call_callback(self._on_drone_change, "drone change", key, value)

    def _handle_drone_mode(self, _address: str, *args: Any) -> None:
        self._handle_drone_setting(
            args, self.DRONE_SET_MODE_ADDRESS, "mode", drone.validate_mode
        )

    def _handle_drone_velocity(self, _address: str, *args: Any) -> None:
        self._handle_drone_setting(
            args, self.DRONE_SET_VELOCITY_ADDRESS, "velocity",
            drone.validate_velocity,
        )

    def _handle_drone_pulse_notes(self, _address: str, *args: Any) -> None:
        self._handle_drone_setting(
            args, self.DRONE_SET_PULSE_NOTES_ADDRESS, "pulse_notes",
            drone.validate_pulse_notes,
        )

    def _handle_drone_pulse_grid(self, _address: str, *args: Any) -> None:
        self._handle_drone_setting(
            args, self.DRONE_SET_PULSE_GRID_ADDRESS, "pulse_grid",
            drone.validate_pulse_grid,
        )

    def _handle_drone_pulse_gate(self, _address: str, *args: Any) -> None:
        self._handle_drone_setting(
            args, self.DRONE_SET_PULSE_GATE_ADDRESS, "pulse_gate",
            drone.validate_pulse_gate,
        )

    def _handle_drone_pulse_span(self, _address: str, *args: Any) -> None:
        self._handle_drone_setting(
            args, self.DRONE_SET_PULSE_SPAN_ADDRESS, "pulse_octave_span",
            drone.validate_pulse_span,
        )

    def _handle_drone_slide_direction(self, _address: str, *args: Any) -> None:
        self._handle_drone_setting(
            args, self.DRONE_SET_SLIDE_DIRECTION_ADDRESS, "slide_direction",
            drone.validate_slide_direction,
        )

    def _handle_drone_slide_semitones(self, _address: str, *args: Any) -> None:
        self._handle_drone_setting(
            args, self.DRONE_SET_SLIDE_SEMITONES_ADDRESS, "slide_semitones",
            drone.validate_slide_semitones,
        )

    def _handle_drone_slide_seconds(self, _address: str, *args: Any) -> None:
        self._handle_drone_setting(
            args, self.DRONE_SET_SLIDE_SECONDS_ADDRESS, "slide_seconds",
            drone.validate_slide_seconds,
        )

    def _handle_drone_hold_beats(self, _address: str, *args: Any) -> None:
        self._handle_drone_setting(
            args, self.DRONE_SET_HOLD_BEATS_ADDRESS, "hold_beats",
            drone.validate_hold_beats,
        )

    def _handle_drone_seed(self, _address: str, *args: Any) -> None:
        self._handle_drone_setting(
            args, self.DRONE_SET_SEED_ADDRESS, "seed", drone.validate_seed
        )

    def _handle_drone_seq_notes(self, _address: str, *args: Any) -> None:
        self._handle_drone_setting(
            args, self.DRONE_SET_SEQ_NOTES_ADDRESS, "seq_notes",
            drone.validate_seq_notes,
        )

    def _handle_drone_seq_bars(self, _address: str, *args: Any) -> None:
        self._handle_drone_setting(
            args, self.DRONE_SET_SEQ_BARS_ADDRESS, "seq_bars",
            drone.validate_seq_bars,
        )

    def _handle_drone_seq_gate(self, _address: str, *args: Any) -> None:
        self._handle_drone_setting(
            args, self.DRONE_SET_SEQ_GATE_ADDRESS, "seq_gate",
            drone.validate_seq_gate,
        )

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


def _bass_grid_value(value: Any) -> str:
    """Accept ``"1/4"|"1/8"|"1/16"`` or the steps-per-beat ``1|2|4``."""

    if isinstance(value, str):
        return bass.validate_grid(value)
    if isinstance(value, bool):
        raise TypeError("grid must be a string or 1, 2, 4")
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        raise TypeError("grid must be a string or 1, 2, 4") from None
    if not math.isfinite(numeric):
        raise ValueError("grid must be finite")
    mapping = {1: "1/4", 2: "1/8", 4: "1/16"}
    steps = int(round(numeric))
    if steps not in mapping:
        raise ValueError("grid must be 1/4, 1/8, 1/16 or 1, 2, 4")
    return mapping[steps]


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

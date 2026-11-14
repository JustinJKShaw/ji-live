#!/usr/bin/env python3
"""Stage 7/8.2 pure OSC message/state layer for the performance GUI.

This module is deliberately free of PyQt6 imports so ``tests/test_ui.py --pure`` can
run without Qt.  It owns:

* pure message builders (address + argument list) for every Stage 7 control;
* range/clamp helpers shared by the widgets and the tests;
* parsers for every Stage 7 status message;
* :class:`StatusStore`, a plain snapshot of the last known bridge/sclang state;
* :class:`OSCController`, which owns the two outgoing UDP clients and the
  background status listener.

Topology (see ``docs/README_stage7.md``)::

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

RHYTHM_SET_TEMPO = "/ji/rhythm/set_tempo"
RHYTHM_SET_METER = "/ji/rhythm/set_meter"
RHYTHM_TRANSPORT = "/ji/rhythm/transport"
RHYTHM_STATE = "/ji/rhythm/state"
RHYTHM_REQUEST = "/ji/rhythm/request"

DRUMS_SET_ENABLED = "/ji/drums/set_enabled"
DRUMS_SET_LANE = "/ji/drums/set_lane"
DRUMS_SET_RATE = "/ji/drums/set_rate"
DRUMS_STATE = "/ji/drums/state"

# Stage 8.2 groove controls and status.
DRUMS_SET_PRESET = "/ji/drums/set_preset"
DRUMS_SET_DENSITY = "/ji/drums/set_density"
DRUMS_SET_SYNCOPATION = "/ji/drums/set_syncopation"
DRUMS_SET_SWING = "/ji/drums/set_swing"
DRUMS_SET_PROBABILITY = "/ji/drums/set_probability"
DRUMS_SET_STEP = "/ji/drums/set_step"
DRUMS_SET_SEED = "/ji/drums/set_seed"
DRUMS_RESET_GRID = "/ji/drums/reset_grid"
DRUMS_GROOVE = "/ji/drums/groove"
DRUMS_GRID = "/ji/drums/grid"
QUANTIZE_STATE = "/ji/quantize/state"

# Stage 8.3a bass controls and status.
BASS_SET_ENABLED = "/ji/bass/set_enabled"
BASS_SET_PRESET = "/ji/bass/set_preset"
BASS_SET_GRID = "/ji/bass/set_grid"
BASS_SET_LENGTH = "/ji/bass/set_length"
BASS_SET_DENSITY = "/ji/bass/set_density"
BASS_SET_SYNCOPATION = "/ji/bass/set_syncopation"
BASS_SET_SWING = "/ji/bass/set_swing"
BASS_SET_GATE = "/ji/bass/set_gate"
BASS_SET_ACCENT = "/ji/bass/set_accent"
BASS_SET_ROOT_DOWNBEAT = "/ji/bass/set_root_downbeat"
BASS_SET_PITCH_MODE = "/ji/bass/set_pitch_mode"
BASS_SET_MOVEMENT = "/ji/bass/set_movement"
BASS_SET_OCTAVE_RANGE = "/ji/bass/set_octave_range"
BASS_SET_OCTAVE_JUMP = "/ji/bass/set_octave_jump"
BASS_SET_BASE_OCTAVE = "/ji/bass/set_base_octave"
BASS_SET_SEED = "/ji/bass/set_seed"
BASS_SET_SUB_ENABLED = "/ji/bass/set_sub_enabled"
BASS_SET_SUB_OCTAVES = "/ji/bass/set_sub_octaves"
BASS_STATE = "/ji/bass/state"
BASS_NOTE = "/ji/bass/note"
# Stage 8.7 bass hit row (UI -> bridge, immediate) and its status echo.
BASS_SET_HITS_ENABLED = "/ji/bass/set_hits_enabled"
BASS_SET_HIT = "/ji/bass/set_hit"
BASS_CLEAR_HITS = "/ji/bass/clear_hits"
BASS_COPY_HITS = "/ji/bass/copy_hits"
BASS_HITS = "/ji/bass/hits"

# Stage 8.7 breathing rhythm engine (UI -> bridge, immediate).
BREATH_SET_ENABLED = "/ji/breath/set_enabled"
BREATH_SET_DEPTH = "/ji/breath/set_depth"
BREATH_SET_PERIOD = "/ji/breath/set_period"
BREATH_SET_SHAPE = "/ji/breath/set_shape"
BREATH_SET_INTENSITY = "/ji/breath/set_intensity"
BREATH_SET_PHASE = "/ji/breath/set_phase"
BREATH_SET_ARP_LOCAL = "/ji/breath/set_arp_local"
BREATH_SET_ARP_SYNC = "/ji/breath/set_arp_sync"
BREATH_STATE = "/ji/breath/state"

# Stage 8.3b arp parameters and status.
ARP_SET_STEP = "/ji/arp/set_step"
ARP_SET_GATE = "/ji/arp/set_gate"
ARP_SET_VELOCITY = "/ji/arp/set_velocity"
ARP_SET_OCTAVES = "/ji/arp/set_octaves"
ARP_SET_SHARED_MODE = "/ji/arp/set_shared_mode"
ARP_SET_ORDER = "/ji/arp/set_order"
ARP_SET_SEED = "/ji/arp/set_seed"
ARP_SET_SYNC = "/ji/arp/set_sync"
ARP_SET_DIVISION = "/ji/arp/set_division"
ARP_SET_EVOLVE = "/ji/arp/set_evolve"
ARP_SET_EVOLVE_BEATS = "/ji/arp/set_evolve_beats"
ARP_SET_EVOLVE_DEPTH = "/ji/arp/set_evolve_depth"
ARP_STATE = "/ji/arp/state"

#: Stage 8.3d lead/melody controls (UI -> bridge).
LEAD_SET_ENABLED = "/ji/lead/set_enabled"
LEAD_SET_GRID = "/ji/lead/set_grid"
LEAD_SET_PHRASE_BARS = "/ji/lead/set_phrase_bars"
LEAD_SET_DENSITY = "/ji/lead/set_density"
LEAD_SET_GATE = "/ji/lead/set_gate"
LEAD_SET_VELOCITY = "/ji/lead/set_velocity"
LEAD_SET_OCTAVE_RANGE = "/ji/lead/set_octave_range"
LEAD_SET_BASE_OCTAVE = "/ji/lead/set_base_octave"
LEAD_SET_VARIATION = "/ji/lead/set_variation"
LEAD_SET_VARIATION_BARS = "/ji/lead/set_variation_bars"
LEAD_SET_SWING = "/ji/lead/set_swing"
LEAD_SET_GROOVE_LINK = "/ji/lead/set_groove_link"
LEAD_SET_SEED = "/ji/lead/set_seed"
LEAD_SET_GATE_MODE = "/ji/lead/set_gate_mode"
LEAD_VARY = "/ji/lead/vary"
LEAD_STATE = "/ji/lead/state"
LEAD_NOTE = "/ji/lead/note"
#: Stage 9.1 lead input (MIDI/QWERTY play-in, recording, melodies).
LEAD_INPUT_SET_ENABLED = "/ji/lead/input/set_enabled"
LEAD_INPUT_SET_QUANTIZE = "/ji/lead/input/set_quantize"
LEAD_INPUT_SET_COUNT_IN = "/ji/lead/input/set_count_in"
LEAD_INPUT_SET_BARS = "/ji/lead/input/set_bars"
LEAD_INPUT_NOTE_ON = "/ji/lead/input/note_on"
LEAD_INPUT_NOTE_OFF = "/ji/lead/input/note_off"
LEAD_INPUT_RECORD = "/ji/lead/input/record"
LEAD_INPUT_STOP = "/ji/lead/input/stop"
LEAD_INPUT_REVERT = "/ji/lead/input/revert"
LEAD_INPUT_CLEAR = "/ji/lead/input/clear"
LEAD_INPUT_REFRESH = "/ji/lead/input/refresh"
LEAD_INPUT_STATE = "/ji/lead/input/state"
LEAD_MELODY_SAVE = "/ji/lead/melody/save"
LEAD_MELODY_LOAD = "/ji/lead/melody/load"
LEAD_MELODY_DELETE = "/ji/lead/melody/delete"
LEAD_MELODY_REQUEST = "/ji/lead/melody/request"
LEAD_MELODY_LIST = "/ji/lead/melody/list"
LEAD_MELODY_STATE = "/ji/lead/melody/state"

#: Stage 8.3d drone modes/pad parameters (UI -> bridge).
DRONE_SET_MODE = "/ji/drone/set_mode"
DRONE_SET_VELOCITY = "/ji/drone/set_velocity"
DRONE_SET_PULSE_NOTES = "/ji/drone/set_pulse_notes"
DRONE_SET_PULSE_GRID = "/ji/drone/set_pulse_grid"
DRONE_SET_PULSE_GATE = "/ji/drone/set_pulse_gate"
DRONE_SET_PULSE_SPAN = "/ji/drone/set_pulse_span"
DRONE_SET_SLIDE_DIRECTION = "/ji/drone/set_slide_direction"
DRONE_SET_SLIDE_SEMITONES = "/ji/drone/set_slide_semitones"
DRONE_SET_SLIDE_SECONDS = "/ji/drone/set_slide_seconds"
DRONE_SET_HOLD_BEATS = "/ji/drone/set_hold_beats"
DRONE_SET_SEED = "/ji/drone/set_seed"
DRONE_SET_SEQ_NOTES = "/ji/drone/set_seq_notes"
DRONE_SET_SEQ_BARS = "/ji/drone/set_seq_bars"
DRONE_SET_SEQ_GATE = "/ji/drone/set_seq_gate"
DRONE_STATE = "/ji/drone/state"

# Stage 8.4 home sets (UI -> Bridge) and the 13-value state message.
HOME_CAPTURE = "/ji/home/capture"
HOME_CLEAR = "/ji/home/clear"
HOME_GO = "/ji/home/go"
HOME_RETURN = "/ji/home/return"
HOME_JUMP = "/ji/home/jump"
HOME_BACK = "/ji/home/back"
HOME_SET_AUTO = "/ji/home/set_auto"
HOME_SET_EVERY = "/ji/home/set_every"
HOME_SET_SEED = "/ji/home/set_seed"
HOME_SET_DRIFT = "/ji/home/set_drift"
HOME_SET_GLIDE = "/ji/home/set_glide"
HOME_STATE = "/ji/home/state"

# Stage 8.5 performance states (UI -> Bridge) and the two replies.
STATE_SAVE = "/ji/state/save"
STATE_LOAD = "/ji/state/load"
STATE_DELETE = "/ji/state/delete"
STATE_REQUEST = "/ji/state/request"
STATE_LIST = "/ji/state/list"
STATE_STATE = "/ji/state/state"

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
LOOP_SET_MODE = "/ji/loop/set_mode"
LOOP_SET_BEATS = "/ji/loop/set_beats"
LOOP_SET_LENGTH = "/ji/loop/set_length"
LOOP_CONFIG = "/ji/loop/config"
LOOP_STATE = "/ji/loop/state"

#: Stage 8.6 loop editing.  UI -> sclang (loops stay sclang-owned).
LOOP_DUB = "/ji/loop/dub"
LOOP_EDIT_SET = "/ji/loop/edit_set"
#: Stage 9.0: one-shot trigger for Cascade mode (UI -> sclang only).
LOOP_FX_FIRE = "/ji/loop/fx_fire"
LOOP_MEM_SAVE = "/ji/loop/mem_save"
LOOP_MEM_RECALL = "/ji/loop/mem_recall"
LOOP_MEM_CLEAR = "/ji/loop/mem_clear"
#: sclang -> UI status replies.
LOOP_EDIT = "/ji/loop/edit"
LOOP_MEM = "/ji/loop/mem"
LOOP_COUNT = "/ji/loop/count"

STATUS_QUERY = "/ji/status/query"
CPU = "/ji/cpu"

STATUS_ADDRESSES = (
    FREQSET_CHANGED,
    UI_STATE,
    MIDI_STATUS,
    MIDI_STATE,
    RHYTHM_STATE,
    DRUMS_STATE,
    DRUMS_GROOVE,
    DRUMS_GRID,
    QUANTIZE_STATE,
    BASS_STATE,
    BASS_NOTE,
    BASS_HITS,
    BREATH_STATE,
    ARP_STATE,
    LEAD_STATE,
    LEAD_NOTE,
    LEAD_INPUT_STATE,
    LEAD_MELODY_LIST,
    LEAD_MELODY_STATE,
    DRONE_STATE,
    HOME_STATE,
    STATE_LIST,
    STATE_STATE,
    CPU,
    LOOP_STATE,
    LOOP_CONFIG,
    LOOP_EDIT,
    LOOP_MEM,
    LOOP_COUNT,
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

#: ``dubbing`` (overdub in progress) and ``counting`` (count-in pending)
#: are Stage 8.6 additions to the Stage 7 set.
LOOP_STATES = (
    "empty",
    "recording",
    "recorded",
    "playing",
    "stopped",
    "dubbing",
    "counting",
)

# Stage 8.1 rhythm / drum ranges.  Kept as plain constants (this module has no
# PyQt6 or control-package import so it can run as a standalone UI module).
BPM_MIN = 20.0
BPM_MAX = 300.0
BPM_DEFAULT = 120.0
METER_NUM_MIN = 1
METER_NUM_MAX = 16
METER_NUM_DEFAULT = 4
METER_DENOMINATORS = (1, 2, 4, 8, 16)
METER_DEN_DEFAULT = 4
DRUM_RATES = (0.5, 1.0, 2.0)
DRUM_RATE_DEFAULT = 1.0
DRUM_RATE_LABELS = ("Half", "Normal", "Double")
LOOP_MODES = ("free", "beat")
LOOP_MODE_DEFAULT = "free"
LOOP_BEATS_MIN = 1.0
LOOP_BEATS_MAX = 64.0
LOOP_BEATS_DEFAULT = 4.0
LANE_MASK_ALL = 0xFFFF

# ---------------------------------------------------------------------------
# Stage 8.6 loop editing: rates, divisions, count-in, memory slots
# ---------------------------------------------------------------------------

#: The permitted varispeed ratios: ``(label, value)``.  The value is what goes
#: on the wire; sclang rejects anything else.  2/1 = +12 st, 3/2 = +7.02 st,
#: 2/3 = -7.02 st, 3/5 = -8.84 st against the recorded material.
LOOP_RATES: tuple[tuple[str, float], ...] = (
    ("2/1", 2.0),
    ("3/2", 1.5),
    ("5/3", 1.6666667),
    ("5/4", 1.25),
    ("4/3", 1.3333333),
    ("1/1", 1.0),
    ("3/4", 0.75),
    ("4/5", 0.8),
    ("2/3", 0.6666667),
    ("3/5", 0.6),
    ("1/2", 0.5),
)
LOOP_RATE_LABELS: tuple[str, ...] = tuple(label for label, _ in LOOP_RATES)
LOOP_RATE_VALUES: tuple[float, ...] = tuple(value for _, value in LOOP_RATES)
LOOP_RATE_DEFAULT = 1.0
LOOP_RATE_LABEL_DEFAULT = "1/1"

#: Grain subdivisions: ``(label, beats)``.  ``1`` is one beat.
LOOP_DIVISIONS: tuple[tuple[str, float], ...] = (
    ("1/16", 0.0625),
    ("1/8", 0.125),
    ("1/4", 0.25),
    ("1/2", 0.5),
    ("1", 1.0),
)
LOOP_DIVISION_LABELS: tuple[str, ...] = tuple(label for label, _ in LOOP_DIVISIONS)
LOOP_DIVISION_DEFAULT = "1/16"

#: Count-in options and the beats-per-bar rule (a bar is the current meter's
#: bar: 4/4 = 4 quarter beats, 3/4 = 3, 6/8 = 3).
LOOP_COUNT_IN_OPTIONS: tuple[str, ...] = ("off", "1 beat", "1 bar", "2 bars")
LOOP_COUNT_IN_DEFAULT = "off"
LOOP_AUTO_PLAY_DEFAULT = False
LOOP_MEM_SLOTS = 4
LOOP_MEM_SLOT_NAMES: tuple[str, ...] = ("A", "B", "C", "D")

# ---------------------------------------------------------------------------
# Stage 8.6.1 granular redesign: motion, cloud defaults, JI pitch snap, smear
# ---------------------------------------------------------------------------

#: Grain motion modes.  The index is the ``\ji_loop_voice`` ``grainMotion``
#: argument: 0 off (the position stays at ``Pos``), 1 forward, 2 backward,
#: 3 pingpong, 4 drift (a slow random walk).
LOOP_GRAIN_MOTIONS: tuple[str, ...] = (
    "off",
    "forward",
    "backward",
    "pingpong",
    "drift",
)
LOOP_GRAIN_MOTION_DEFAULT = "forward"
LOOP_GRAIN_SPEED_MIN = 0.001
LOOP_GRAIN_SPEED_MAX = 0.5
LOOP_GRAIN_SPEED_DEFAULT = 0.03
LOOP_GRAIN_DEPTH_DEFAULT = 0.6
LOOP_GRAIN_SIZE_JITTER_DEFAULT = 0.2
LOOP_GRAIN_PITCH_JITTER_DEFAULT = 0.0
LOOP_GRAIN_PITCH_SNAP_DEFAULT = 1
LOOP_GRAIN_SMEAR_DEFAULT = 0.0
LOOP_GRAIN_FREEZE_DEFAULT = 0
LOOP_GRAIN_BURST_DEFAULT = 0.6
LOOP_GRAIN_RATE_DEFAULT = 20.0
LOOP_GRAIN_RATE_MAX = 80.0
LOOP_GRAIN_SIZE_DEFAULT_MS = 120.0
LOOP_GRAIN_POS_DEFAULT = 0.5
LOOP_GRAIN_SCATTER_DEFAULT = 0.1
LOOP_GRAIN_PITCH_DEFAULT = 1.0
LOOP_GRAIN_REVERSE_DEFAULT = 0.0

#: Stage 8.6.2: the dry-loop <-> grain-cloud morph, the "melt" ramp toggle and
#: the moving post-autotune filter.  Morph 0 in grain mode is the ORIGINAL loop
#: (the dry path through the autotune); Morph 1 is the full grain cloud.
LOOP_GRAIN_MORPH_DEFAULT = 0.0
LOOP_GRAIN_MELT_DEFAULT = 0
LOOP_GRAIN_FILTER_DEFAULT = 0.0
LOOP_GRAIN_FILTER_RATE_MIN = 0.01
LOOP_GRAIN_FILTER_RATE_MAX = 2.0
LOOP_GRAIN_FILTER_RATE_DEFAULT = 0.12

#: Stage 8.7: the loop's own grain-breath LFO (independent of the bridge's
#: breathing rhythm engine; it lives entirely inside the loop voice).
LOOP_GRAIN_BREATH_MIN = 0.0
LOOP_GRAIN_BREATH_MAX = 1.0
LOOP_GRAIN_BREATH_DEFAULT = 0.0
LOOP_GRAIN_BREATH_RATE_MIN = 0.02
LOOP_GRAIN_BREATH_RATE_MAX = 1.0
LOOP_GRAIN_BREATH_RATE_DEFAULT = 0.08

#: Stage 9.0: per-loop harmonic-series delay FX (post-PitchShift).
#:
#: ``harm_mode`` is ``off``, ``cascade`` (one captured segment, harmonics
#: entering in ascending order) or ``delay`` (continuous harmonized buffer
#: delay).  The numeric controls are shared by both modes; ``harm_spacing``
#: and ``harm_auto`` are inert in delay mode.  ``harm_time`` is the FREE time
#: in seconds; ``harm_time_sync`` may lock it to a musical division instead
#: (owner follow-up, 2026-11-09: "fixed (ex quarter notes, dotted, etc) time
#: delay").  Both are clamped to the 2 s FX buffer.
LOOP_HARM_MODES: tuple[str, ...] = ("off", "cascade", "delay")
LOOP_HARM_MODE_DEFAULT = "off"
LOOP_HARM_MIX_DEFAULT = 0.35
LOOP_HARM_TIME_MIN = 0.05
LOOP_HARM_TIME_MAX = 2.0
LOOP_HARM_TIME_DEFAULT = 0.5
LOOP_HARM_STEPS_MIN = 1
LOOP_HARM_STEPS_MAX = 8
LOOP_HARM_STEPS_DEFAULT = 4
LOOP_HARM_SPACING_MIN = 0.0
LOOP_HARM_SPACING_MAX = 1.0
LOOP_HARM_SPACING_DEFAULT = 0.08
LOOP_HARM_FALLOFF_MIN = 0.0
LOOP_HARM_FALLOFF_MAX = 1.0
LOOP_HARM_FALLOFF_DEFAULT = 0.5
LOOP_HARM_AUTO_DEFAULT = 1
LOOP_HARM_MAX_SECONDS = 2.0

#: Musical divisions for the fixed-time delay.  ``free`` uses ``harm_time``
#: seconds; every other value is a quarter-note count at the bridge tempo.
#: ``d`` = dotted (x1.5), ``t`` = triplet (x2/3); ``1 bar`` follows the
#: current meter.  All resulting times clamp to 0.05..2.0 s (the FX buffer).
LOOP_HARM_TIME_SYNCS: tuple[str, ...] = (
    "free",
    "1/32",
    "1/32d",
    "1/32t",
    "1/16",
    "1/16d",
    "1/16t",
    "1/8",
    "1/8d",
    "1/8t",
    "1/4",
    "1/4d",
    "1/4t",
    "1/2",
    "1/2d",
    "1/2t",
    "1/1",
    "1/1d",
    "1/1t",
    "1 bar",
)
LOOP_HARM_TIME_SYNC_DEFAULT = "free"

#: Base note value in quarter beats before the dotted/triplet modifier.
LOOP_HARM_TIME_BASE_BEATS: dict[str, float] = {
    "1/32": 0.125,
    "1/16": 0.25,
    "1/8": 0.5,
    "1/4": 1.0,
    "1/2": 2.0,
    "1/1": 4.0,
}

#: JI ratio vocabulary used to render the EFFECTIVE grain pitch in the status
#: line (``×3/2`` rather than ``x1.500``).  The superset of the shipped set and
#: its +/- 3 octaves; an unknown value falls back to ``x1.234``.
JI_RATIO_LABELS: tuple[tuple[float, str], ...] = (
    (0.5, "1/2"),
    (0.6, "3/5"),
    (0.6666667, "2/3"),
    (0.75, "3/4"),
    (0.8, "4/5"),
    (1.0, "1/1"),
    (1.125, "9/8"),
    (1.25, "5/4"),
    (1.3333333, "4/3"),
    (1.5, "3/2"),
    (1.6666667, "5/3"),
    (1.875, "15/8"),
    (2.0, "2/1"),
    (2.25, "9/4"),
    (2.5, "5/2"),
    (2.6666667, "8/3"),
    (3.0, "3/1"),
    (3.3333333, "10/3"),
    (3.75, "15/4"),
    (4.0, "4/1"),
)


def ji_ratio_label(value: Any) -> str:
    """Render a snapped grain-pitch ratio as a JI fraction (``1.5 -> "3/2"``).

    The effective ratio is a set ratio times an octave, so nearest-by-cents in
    the vocabulary above is exact for every shipped set.  The fallback keeps
    the status line honest for an exotic value.
    """

    numeric = _as_finite_float(value, "grain pitch")
    if numeric <= 0.0:
        return "0"
    best_label = f"{numeric:.3f}"
    best_distance = None
    for candidate, label in JI_RATIO_LABELS:
        distance = abs(math.log2(candidate / numeric))
        if best_distance is None or distance < best_distance:
            best_distance = distance
            best_label = label
    return best_label


@dataclass(frozen=True)
class LoopEditParam:
    """One ``/ji/loop/edit_set`` parameter: name, label, range, default, kind.

    ``kind`` is ``"bool"`` (0/1), ``"float"`` or ``"choice"`` (a string from
    ``choices``).  The ranges mirror sclang's clamps and the GUI widgets.
    """

    name: str
    label: str
    low: float
    high: float
    default: Any
    kind: str = "float"
    choices: tuple[str, ...] = ()
    fmt: str = "{:.2f}"


LOOP_EDIT_PARAMS: tuple[LoopEditParam, ...] = (
    LoopEditParam("reverse", "Rev", 0.0, 1.0, 0, kind="bool"),
    LoopEditParam(
        "rate", "Rate", 0.5, 2.0, LOOP_RATE_DEFAULT, kind="float", fmt="{:.4f}"
    ),
    LoopEditParam("gain", "Gain", 0.0, 1.0, 0.7),
    LoopEditParam("mode", "Grain", 0.0, 1.0, 0, kind="bool"),
    LoopEditParam("sync", "Sync", 0.0, 1.0, 0, kind="bool"),
    LoopEditParam(
        "division",
        "Div",
        0.0,
        0.0,
        LOOP_DIVISION_DEFAULT,
        kind="choice",
        choices=LOOP_DIVISION_LABELS,
    ),
    LoopEditParam(
        "grain_rate", "Rate", 0.5, LOOP_GRAIN_RATE_MAX, LOOP_GRAIN_RATE_DEFAULT
    ),
    LoopEditParam(
        "grain_size",
        "Size",
        5.0,
        500.0,
        LOOP_GRAIN_SIZE_DEFAULT_MS,
        fmt="{:.0f}",
    ),
    LoopEditParam(
        "grain_pos", "Pos", 0.0, 1.0, LOOP_GRAIN_POS_DEFAULT
    ),
    LoopEditParam(
        "grain_scatter", "Scatter", 0.0, 1.0, LOOP_GRAIN_SCATTER_DEFAULT
    ),
    LoopEditParam(
        "grain_pitch", "Pitch", 0.25, 4.0, LOOP_GRAIN_PITCH_DEFAULT
    ),
    LoopEditParam(
        "grain_size_jitter",
        "Size Jit",
        0.0,
        1.0,
        LOOP_GRAIN_SIZE_JITTER_DEFAULT,
    ),
    LoopEditParam(
        "grain_pitch_jitter",
        "Pitch Jit",
        0.0,
        1.0,
        LOOP_GRAIN_PITCH_JITTER_DEFAULT,
    ),
    LoopEditParam(
        "grain_pitch_snap",
        "Snap",
        0.0,
        1.0,
        LOOP_GRAIN_PITCH_SNAP_DEFAULT,
        kind="bool",
    ),
    LoopEditParam(
        "grain_smear", "Smear", 0.0, 1.0, LOOP_GRAIN_SMEAR_DEFAULT
    ),
    LoopEditParam(
        "grain_freeze",
        "Freeze",
        0.0,
        1.0,
        LOOP_GRAIN_FREEZE_DEFAULT,
        kind="bool",
    ),
    LoopEditParam(
        "grain_motion",
        "Motion",
        0.0,
        0.0,
        LOOP_GRAIN_MOTION_DEFAULT,
        kind="choice",
        choices=LOOP_GRAIN_MOTIONS,
    ),
    LoopEditParam(
        "grain_speed",
        "Speed",
        LOOP_GRAIN_SPEED_MIN,
        LOOP_GRAIN_SPEED_MAX,
        LOOP_GRAIN_SPEED_DEFAULT,
        fmt="{:.3f}",
    ),
    LoopEditParam(
        "grain_depth", "Depth", 0.0, 1.0, LOOP_GRAIN_DEPTH_DEFAULT
    ),
    LoopEditParam(
        "grain_burst", "Burst", 0.0, 1.0, LOOP_GRAIN_BURST_DEFAULT
    ),
    LoopEditParam(
        "grain_morph", "Morph", 0.0, 1.0, LOOP_GRAIN_MORPH_DEFAULT
    ),
    LoopEditParam(
        "grain_melt",
        "Melt",
        0.0,
        1.0,
        LOOP_GRAIN_MELT_DEFAULT,
        kind="bool",
    ),
    LoopEditParam(
        "grain_filter", "Filter", 0.0, 1.0, LOOP_GRAIN_FILTER_DEFAULT
    ),
    LoopEditParam(
        "grain_filter_rate",
        "F.Rate",
        LOOP_GRAIN_FILTER_RATE_MIN,
        LOOP_GRAIN_FILTER_RATE_MAX,
        LOOP_GRAIN_FILTER_RATE_DEFAULT,
        fmt="{:.2f}",
    ),
    LoopEditParam(
        "grain_breath",
        "Breath",
        LOOP_GRAIN_BREATH_MIN,
        LOOP_GRAIN_BREATH_MAX,
        LOOP_GRAIN_BREATH_DEFAULT,
    ),
    LoopEditParam(
        "grain_breath_rate",
        "B.Rate",
        LOOP_GRAIN_BREATH_RATE_MIN,
        LOOP_GRAIN_BREATH_RATE_MAX,
        LOOP_GRAIN_BREATH_RATE_DEFAULT,
        fmt="{:.2f}",
    ),
    LoopEditParam(
        "grain_reverse", "Rev%", 0.0, 1.0, LOOP_GRAIN_REVERSE_DEFAULT
    ),
    LoopEditParam(
        "count_in",
        "Count-in",
        0.0,
        0.0,
        LOOP_COUNT_IN_DEFAULT,
        kind="choice",
        choices=LOOP_COUNT_IN_OPTIONS,
    ),
    LoopEditParam(
        "auto_play",
        "Auto",
        0.0,
        1.0,
        1 if LOOP_AUTO_PLAY_DEFAULT else 0,
        kind="bool",
    ),
    # --- Stage 9.0 harmonic-series delay FX ------------------------------
    LoopEditParam(
        "harm_mode",
        "Harm",
        0.0,
        0.0,
        LOOP_HARM_MODE_DEFAULT,
        kind="choice",
        choices=LOOP_HARM_MODES,
    ),
    LoopEditParam(
        "harm_mix", "Mix", 0.0, 1.0, LOOP_HARM_MIX_DEFAULT
    ),
    LoopEditParam(
        "harm_time",
        "Time",
        LOOP_HARM_TIME_MIN,
        LOOP_HARM_TIME_MAX,
        LOOP_HARM_TIME_DEFAULT,
        fmt="{:.3f}",
    ),
    LoopEditParam(
        "harm_time_sync",
        "TimeSync",
        0.0,
        0.0,
        LOOP_HARM_TIME_SYNC_DEFAULT,
        kind="choice",
        choices=LOOP_HARM_TIME_SYNCS,
    ),
    LoopEditParam(
        "harm_steps",
        "Steps",
        float(LOOP_HARM_STEPS_MIN),
        float(LOOP_HARM_STEPS_MAX),
        LOOP_HARM_STEPS_DEFAULT,
        kind="int",
        fmt="{:d}",
    ),
    LoopEditParam(
        "harm_spacing",
        "Space",
        LOOP_HARM_SPACING_MIN,
        LOOP_HARM_SPACING_MAX,
        LOOP_HARM_SPACING_DEFAULT,
        fmt="{:.3f}",
    ),
    LoopEditParam(
        "harm_falloff",
        "Fall",
        LOOP_HARM_FALLOFF_MIN,
        LOOP_HARM_FALLOFF_MAX,
        LOOP_HARM_FALLOFF_DEFAULT,
    ),
    LoopEditParam(
        "harm_auto",
        "Auto",
        0.0,
        1.0,
        1 if LOOP_HARM_AUTO_DEFAULT else 0,
        kind="bool",
    ),
)
LOOP_EDIT_PARAM_MAP: dict[str, LoopEditParam] = {
    spec.name: spec for spec in LOOP_EDIT_PARAMS
}


def loop_rate_label(rate: Any) -> str:
    """Return the GUI label (``"3/2"``) of the nearest permitted ratio."""

    numeric = float(rate)
    label = LOOP_RATE_LABEL_DEFAULT
    best = None
    for candidate_label, candidate in LOOP_RATES:
        distance = abs(candidate - numeric)
        if best is None or distance < best:
            best = distance
            label = candidate_label
    return label


def loop_rate_value(label: Any) -> float:
    """Map a GUI rate label to its permitted float (``"3/2" -> 1.5``)."""

    text = str(label)
    for candidate_label, candidate in LOOP_RATES:
        if candidate_label == text:
            return float(candidate)
    raise ValueError(f"unknown loop rate {label!r}; use one of {LOOP_RATE_LABELS}")


def loop_snap_pitch(ratio: Any, ratios: Sequence[Any]) -> float:
    """Nearest member of the set (x +/-3 octaves, 0.25..4.0) to ``ratio``.

    Pure mirror of sclang's ``~ji_loop_snap_ratio``: the loop material is
    already tuned by the autotune, so a *set ratio* is a consonant interval.
    Used by the GUI's optimistic status line; sclang is the authority and its
    echo overwrites this value.
    """

    target = clamp(_as_finite_float(ratio, "grain pitch"), 0.25, 4.0)
    best = target
    best_distance: float | None = None
    for raw in ratios or (1.0,):
        base = _as_finite_float(raw, "set ratio")
        if base <= 0.0:
            continue
        for octave in range(-3, 4):
            candidate = base * (2.0 ** octave)
            if candidate < 0.25 or candidate > 4.0:
                continue
            distance = abs(math.log2(candidate / target))
            if best_distance is None or distance < best_distance:
                best_distance = distance
                best = candidate
    return float(best)


def harm_time_beats(label: Any, bar_beats: Any = 4.0) -> float | None:
    """Quarter beats for a ``harm_time_sync`` label (``None`` for ``free``).

    Straight notes use their base value; a trailing ``d`` is dotted (x1.5)
    and a trailing ``t`` is a triplet (x2/3).  ``1 bar`` is the current meter
    bar in quarter beats (exact, not rounded up: a 5/8 bar is 2.5 beats).
    This mirrors sclang's ``~ji_loop_harm_sync_beats``.
    """

    text = str(label)
    if text == "free":
        return None
    if text not in LOOP_HARM_TIME_SYNCS:
        raise ValueError(
            f"unknown time sync {label!r}; use one of {LOOP_HARM_TIME_SYNCS}"
        )
    if text == "1 bar":
        return max(1.0, float(bar_beats))
    suffix = text[-1]
    base = text[:-1] if suffix in ("d", "t") else text
    beats = LOOP_HARM_TIME_BASE_BEATS[base]
    if suffix == "d":
        beats *= 1.5
    elif suffix == "t":
        beats *= 2.0 / 3.0
    return float(beats)


def harm_time_seconds(
    sync: Any,
    seconds: Any,
    bpm: Any = 120.0,
    bar_beats: Any = 4.0,
) -> float:
    """Effective FX time in seconds, clamped to the 2 s FX buffer.

    ``free`` returns ``seconds``; a musical sync converts
    ``beats * 60 / bpm``.  The tempo is clamped to the bridge range 20..300
    so a bad snapshot cannot divide by zero.  sclang's
    ``~ji_loop_fx_time`` is the authority; this is the GUI mirror.
    """

    text = str(sync)
    if text == "free":
        value = _as_finite_float(seconds, "harm time")
    else:
        beats = harm_time_beats(text, bar_beats)
        tempo = clamp(_as_finite_float(bpm, "bpm"), 20.0, 300.0)
        value = beats * 60.0 / tempo
    return clamp(value, LOOP_HARM_TIME_MIN, LOOP_HARM_TIME_MAX)


# The UI's default lane labels.  config/rhythm_config.json ships the same map;
# the lane_mask bit order is by list position (bit 0 = first lane / note 36).
DRUM_LANE_DEFS: tuple[tuple[int, str, str], ...] = (
    (36, "kick1", "Kick 1"),
    (37, "kick2", "Kick 2"),
    (38, "snare1", "Snare 1"),
    (39, "snare2", "Snare 2"),
    (40, "perc1", "Perc 1"),
    (41, "perc2", "Perc 2"),
    (42, "perc3", "Perc 3"),
    (43, "perc4", "Perc 4"),
    (44, "perc5", "Perc 5"),
    (45, "perc6", "Perc 6"),
    (46, "perc7", "Perc 7"),
    (47, "perc8", "Perc 8"),
    (48, "perc9", "Perc 9"),
    (49, "perc10", "Perc 10"),
    (50, "perc11", "Perc 11"),
    (51, "perc12", "Perc 12"),
)
DRUM_LANE_DEFAULT_MASK = 0x0045  # kick1 + snare1 + perc3

# Stage 8.2 groove ranges/defaults.  Kept here (no control-package import) so
# the pure UI layer and the Qt window share one source of truth.
GROOVE_PRESETS: tuple[str, ...] = ("four", "break", "half", "techno")
GROOVE_DEFAULT_PRESET = "four"
GROOVE_DEFAULT_DENSITY = 0.0
GROOVE_DEFAULT_SYNCOPATION = 0.0
GROOVE_DEFAULT_SWING = 0.0
GROOVE_DEFAULT_PROBABILITY = 0.0
GROOVE_DEFAULT_SEED = 1
GROOVE_SEED_MAX = 2**31 - 1
MAX_GRID_STEPS = 32

# Stage 8.3a bass ranges/defaults.  Mirrors control/bass.py without importing
# the control package (the pure UI layer stays standalone).
BASS_PRESETS: tuple[str, ...] = ("pulse", "techno", "chaos")
BASS_PRESET_CUSTOM = "custom"
BASS_PRESET_DISPLAY: tuple[str, ...] = (*BASS_PRESETS, BASS_PRESET_CUSTOM)
BASS_GRIDS: tuple[str, ...] = ("1/4", "1/8", "1/16")
BASS_GRID_STEPS: dict[str, int] = {"1/4": 1, "1/8": 2, "1/16": 4}
BASS_PITCH_MODES: tuple[str, ...] = (
    "root",
    "anchor",
    "walk",
    "random",
    "cycle",
)
BASS_DEFAULT_ENABLED = False
BASS_DEFAULT_PRESET = "pulse"
BASS_DEFAULT_GRID = "1/4"
BASS_DEFAULT_DENSITY = 0.0
BASS_DEFAULT_SYNCOPATION = 0.0
BASS_DEFAULT_SWING = 0.0
BASS_DEFAULT_GATE_BEATS = 3.5
BASS_DEFAULT_ACCENT = 0.0
BASS_DEFAULT_ROOT_DOWNBEAT = True
BASS_DEFAULT_PITCH_MODE = "root"
BASS_DEFAULT_MOVEMENT = 0.0
BASS_DEFAULT_OCTAVE_RANGE = 0
BASS_DEFAULT_OCTAVE_JUMP = 0.0
BASS_DEFAULT_BASE_OCTAVE = -2
BASS_DEFAULT_SEED = 1
BASS_LENGTH_MIN = 1
BASS_LENGTH_MAX = 32
BASS_GATE_MIN = 0.05
BASS_GATE_MAX = 8.0
BASS_OCTAVE_RANGE_MIN = 0
BASS_OCTAVE_RANGE_MAX = 3
BASS_BASE_OCTAVE_MIN = -3
BASS_BASE_OCTAVE_MAX = 0
BASS_SUB_OCTAVES: tuple[int, ...] = (1, 2)
BASS_SEED_MAX = 2**31 - 1

# Stage 8.7 bass hit row: one row, one bar of the current grid, capped.
BASS_HITS_MAX = 32
BASS_HITS_DEFAULT_ENABLED = False
BASS_HITS_DEFAULT_COUNT = 0

# Stage 8.7 breathing rhythm engine.  Mirrors control/breath.py.
BREATH_SHAPES: tuple[str, ...] = ("sine", "triangle", "inhale", "exhale")
BREATH_DEFAULT_SHAPE = "inhale"
BREATH_DEPTH_MAX = 1.0
BREATH_PERIOD_BARS_MIN = 1
BREATH_PERIOD_BARS_MAX = 32
BREATH_DEFAULT_PERIOD_BARS = 4
BREATH_DEFAULT_DEPTH = 0.35
BREATH_DEFAULT_INTENSITY = 0.5
BREATH_DEFAULT_PHASE = 0.0
BREATH_DEFAULT_ARP_LOCAL = 0.0
BREATH_DEFAULT_ARP_SYNC = False
BREATH_PARAM_NAMES: tuple[str, ...] = (
    "enabled",
    "depth",
    "period_bars",
    "shape",
    "intensity",
    "phase",
    "arp_local",
    "arp_sync",
)
BREATH_PARAM_ADDRESSES: dict[str, str] = {
    "enabled": BREATH_SET_ENABLED,
    "depth": BREATH_SET_DEPTH,
    "period_bars": BREATH_SET_PERIOD,
    "shape": BREATH_SET_SHAPE,
    "intensity": BREATH_SET_INTENSITY,
    "phase": BREATH_SET_PHASE,
    "arp_local": BREATH_SET_ARP_LOCAL,
    "arp_sync": BREATH_SET_ARP_SYNC,
}

# Stage 8.3b arp ranges/defaults.  These mirror control/arp.py exactly; the
# shipped defaults must reproduce the Stage 6 arp (0.25 s / 0.20 s / forward /
# anchor / [0, 1, 2] / seed 1).
ARP_STEP_MIN = 0.05
ARP_STEP_MAX = 2.0
ARP_STEP_DEFAULT = 0.25
ARP_GATE_MIN = 0.01
ARP_GATE_MAX = 2.0
ARP_GATE_DEFAULT = 0.20
ARP_VELOCITY_MIN = 1
ARP_VELOCITY_MAX = 127
ARP_VELOCITY_DEFAULT = 90
ARP_OCTAVE_MIN = -8
ARP_OCTAVE_MAX = 8
ARP_DEFAULT_OCTAVES: tuple[int, int, int] = (0, 1, 2)
ARP_SHARED_MODES: tuple[str, ...] = ("anchor", "random")
ARP_DEFAULT_SHARED_MODE = "anchor"
ARP_ORDERS: tuple[str, ...] = ("forward", "reverse", "pingpong", "random")
ARP_DEFAULT_ORDER = "forward"
ARP_SEED_MAX = 2**31 - 1
ARP_DEFAULT_SEED = 1

# Stage 8.3c: beat sync + self-evolution.
ARP_SYNC_MODES: tuple[str, ...] = ("free", "beat")
ARP_DEFAULT_SYNC = "free"
ARP_DIVISIONS: tuple[str, ...] = ("1/4", "1/8", "1/16", "1/8T")
ARP_DEFAULT_DIVISION = "1/8"
ARP_DIVISION_QUARTERS: dict[str, float] = {
    "1/4": 1.0,
    "1/8": 0.5,
    "1/16": 0.25,
    "1/8T": 1.0 / 3.0,
}
ARP_EVOLVE_BEATS_MIN = 1
ARP_EVOLVE_BEATS_MAX = 64
ARP_DEFAULT_EVOLVE_BEATS = 4
ARP_DEFAULT_EVOLVE = False
ARP_EVOLVE_DEPTH_DEFAULT = 0.0

# Stage 8.3d lead constants (mirror control/lead.py).
LEAD_DEFAULT_ENABLED = False
LEAD_GRID_STEPS: dict[str, float] = {
    "1/4": 1.0,
    "1/8": 0.5,
    "1/16": 0.25,
    "1/8T": 1.0 / 3.0,
}
LEAD_GRIDS: tuple[str, ...] = ("1/4", "1/8", "1/16", "1/8T")
LEAD_DEFAULT_GRID = "1/8"
LEAD_PHRASE_BARS_MIN = 1
LEAD_PHRASE_BARS_MAX = 8
LEAD_DEFAULT_PHRASE_BARS = 2
LEAD_DENSITY_MIN = 0.1
LEAD_DENSITY_MAX = 1.0
LEAD_DEFAULT_DENSITY = 0.75
LEAD_GATE_MIN = 0.05
LEAD_GATE_MAX = 4.0
LEAD_DEFAULT_GATE = 0.5
LEAD_VELOCITY_MIN = 1
LEAD_VELOCITY_MAX = 127
LEAD_DEFAULT_VELOCITY = 90
LEAD_OCTAVE_RANGE_MIN = 0
LEAD_OCTAVE_RANGE_MAX = 2
LEAD_DEFAULT_OCTAVE_RANGE = 1
LEAD_BASE_OCTAVE_MIN = -2
LEAD_BASE_OCTAVE_MAX = 2
LEAD_DEFAULT_BASE_OCTAVE = 0
LEAD_VARIATIONS: tuple[str, ...] = (
    "identity",
    "transpose",
    "invert",
    "retrograde",
    "thin",
    "ornament",
    "rhythm",
    "random",
)
LEAD_DEFAULT_VARIATION = "identity"
LEAD_VARIATION_BARS_MIN = 0
LEAD_VARIATION_BARS_MAX = 16
LEAD_DEFAULT_VARIATION_BARS = 0
LEAD_SEED_MAX = 2**31 - 1
LEAD_DEFAULT_SEED = 1

#: Stage 9.1 lead input defaults (mirror control/lead_input.py).
LEAD_INPUT_COUNT_INS: tuple[str, ...] = ("off", "1 beat", "1 bar", "2 bars")
LEAD_INPUT_COUNT_IN_DEFAULT = "1 bar"
LEAD_INPUT_QUANTIZE_DEFAULT = 1.0
LEAD_INPUT_BARS_MIN = 1
LEAD_INPUT_BARS_MAX = 8
LEAD_INPUT_BARS_DEFAULT = 2
LEAD_INPUT_SOURCES: tuple[str, ...] = ("generated", "played", "live", "record")
LEAD_INPUT_GATE_MODES: tuple[str, ...] = ("beat", "note")
LEAD_DEFAULT_GATE_MODE = "beat"

# Stage 8.3d drone constants (mirror control/drone.py).
DRONE_MODES: tuple[str, ...] = ("hold", "pulse", "bend", "sequence")
DRONE_DEFAULT_MODE = "hold"
DRONE_VELOCITY_MIN = 1
DRONE_VELOCITY_MAX = 127
DRONE_DEFAULT_VELOCITY = 90
DRONE_PULSE_NOTES_MIN = 2
DRONE_PULSE_NOTES_MAX = 8
DRONE_DEFAULT_PULSE_NOTES = 4
DRONE_PULSE_GRIDS: tuple[str, ...] = ("1/4", "1/8", "1/16")
DRONE_PULSE_GRID_QUARTERS: dict[str, float] = {
    "1/4": 1.0,
    "1/8": 0.5,
    "1/16": 0.25,
}
DRONE_DEFAULT_PULSE_GRID = "1/4"
DRONE_PULSE_GATE_MIN = 0.05
DRONE_PULSE_GATE_MAX = 2.0
DRONE_DEFAULT_PULSE_GATE = 0.5
DRONE_PULSE_SPAN_MIN = 0
DRONE_PULSE_SPAN_MAX = 2
DRONE_DEFAULT_PULSE_SPAN = 1
DRONE_SLIDE_DIRECTIONS: tuple[str, ...] = ("down", "up")
DRONE_DEFAULT_SLIDE_DIRECTION = "down"
DRONE_SLIDE_SEMITONES_MIN = 1
DRONE_SLIDE_SEMITONES_MAX = 12
DRONE_DEFAULT_SLIDE_SEMITONES = 2
DRONE_SLIDE_SECONDS_MIN = 0.05
DRONE_SLIDE_SECONDS_MAX = 4.0
DRONE_DEFAULT_SLIDE_SECONDS = 0.4
DRONE_HOLD_BEATS_MIN = 0.25
DRONE_HOLD_BEATS_MAX = 16.0
DRONE_DEFAULT_HOLD_BEATS = 2.5
DRONE_SEED_MAX = 2**31 - 1
DRONE_DEFAULT_SEED = 1

# Stage 8.3d.1: drone ``sequence`` (harmonic movement through the set).
DRONE_SEQ_NOTES_MIN = 2
DRONE_SEQ_NOTES_MAX = 8
DRONE_DEFAULT_SEQ_NOTES = 5
DRONE_SEQ_BARS: tuple[str, ...] = ("1/4", "1/2", "1", "2", "4")
DRONE_SEQ_BARS_VALUE: dict[str, float] = {
    "1/4": 0.25,
    "1/2": 0.5,
    "1": 1.0,
    "2": 2.0,
    "4": 4.0,
}
DRONE_DEFAULT_SEQ_BARS = "1"
DRONE_SEQ_GATE_MIN = 0.1
DRONE_SEQ_GATE_MAX = 1.0
DRONE_DEFAULT_SEQ_GATE = 1.0

# Stage 8.4 home sets.  Kept here (no control-package import) so the pure UI
# layer and the Qt window share one source of truth.
HOME_SLOTS = 4
HOME_SLOT_NAMES: tuple[str, ...] = ("A", "B", "C", "D")
HOME_EVERY_MIN = 1
HOME_EVERY_MAX = 32
HOME_EVERY_DEFAULT = 8
HOME_SEED_MIN = 0
HOME_SEED_MAX = 2**31 - 1
HOME_SEED_DEFAULT = 1
HOME_DRIFT_MIN = 0
HOME_DRIFT_MAX = 12
HOME_DRIFT_DEFAULT = 0
HOME_GLIDE_MIN = 0.02
HOME_GLIDE_MAX = 0.30
HOME_GLIDE_DEFAULT = 0.06
HOME_HISTORY_DEFAULT = 64


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

GROOVE_PARAMS: tuple[ParamSpec, ...] = (
    ParamSpec(
        "density", "Density", DRUMS_SET_DENSITY, 0.0, 1.0, GROOVE_DEFAULT_DENSITY
    ),
    ParamSpec(
        "syncopation",
        "Syncopation",
        DRUMS_SET_SYNCOPATION,
        0.0,
        1.0,
        GROOVE_DEFAULT_SYNCOPATION,
    ),
    ParamSpec(
        "swing", "Swing", DRUMS_SET_SWING, 0.0, 1.0, GROOVE_DEFAULT_SWING
    ),
    ParamSpec(
        "probability",
        "Probability",
        DRUMS_SET_PROBABILITY,
        0.0,
        1.0,
        GROOVE_DEFAULT_PROBABILITY,
    ),
)
GROOVE_PARAM_MAP: dict[str, ParamSpec] = {
    spec.key: spec for spec in GROOVE_PARAMS
}

BASS_PARAMS: tuple[ParamSpec, ...] = (
    ParamSpec(
        "density", "Bass density", BASS_SET_DENSITY, 0.0, 1.0, BASS_DEFAULT_DENSITY
    ),
    ParamSpec(
        "syncopation",
        "Bass sync",
        BASS_SET_SYNCOPATION,
        0.0,
        1.0,
        BASS_DEFAULT_SYNCOPATION,
    ),
    ParamSpec(
        "swing", "Bass swing", BASS_SET_SWING, 0.0, 1.0, BASS_DEFAULT_SWING
    ),
    ParamSpec(
        "gate_beats",
        "Gate",
        BASS_SET_GATE,
        BASS_GATE_MIN,
        BASS_GATE_MAX,
        BASS_DEFAULT_GATE_BEATS,
        fmt="{:.2f}",
        suffix=" b",
    ),
    ParamSpec(
        "accent", "Accent", BASS_SET_ACCENT, 0.0, 1.0, BASS_DEFAULT_ACCENT
    ),
    ParamSpec(
        "movement",
        "Movement",
        BASS_SET_MOVEMENT,
        0.0,
        1.0,
        BASS_DEFAULT_MOVEMENT,
    ),
    ParamSpec(
        "octave_jump",
        "Oct jump",
        BASS_SET_OCTAVE_JUMP,
        0.0,
        1.0,
        BASS_DEFAULT_OCTAVE_JUMP,
    ),
)
BASS_PARAM_MAP: dict[str, ParamSpec] = {
    spec.key: spec for spec in BASS_PARAMS
}

BASS_SETTING_ADDRESSES: dict[str, str] = {
    "enabled": BASS_SET_ENABLED,
    "preset": BASS_SET_PRESET,
    "grid": BASS_SET_GRID,
    "length_steps": BASS_SET_LENGTH,
    "density": BASS_SET_DENSITY,
    "syncopation": BASS_SET_SYNCOPATION,
    "swing": BASS_SET_SWING,
    "gate_beats": BASS_SET_GATE,
    "accent": BASS_SET_ACCENT,
    "root_downbeat": BASS_SET_ROOT_DOWNBEAT,
    "pitch_mode": BASS_SET_PITCH_MODE,
    "movement": BASS_SET_MOVEMENT,
    "octave_range": BASS_SET_OCTAVE_RANGE,
    "octave_jump": BASS_SET_OCTAVE_JUMP,
    "base_octave": BASS_SET_BASE_OCTAVE,
    "seed": BASS_SET_SEED,
    "sub_enabled": BASS_SET_SUB_ENABLED,
    "sub_octaves": BASS_SET_SUB_OCTAVES,
}

ARP_PARAMS: tuple[ParamSpec, ...] = (
    ParamSpec(
        "step_seconds",
        "Step",
        ARP_SET_STEP,
        ARP_STEP_MIN,
        ARP_STEP_MAX,
        ARP_STEP_DEFAULT,
        fmt="{:.2f}",
        suffix=" s",
    ),
    ParamSpec(
        "gate_seconds",
        "Gate",
        ARP_SET_GATE,
        ARP_GATE_MIN,
        ARP_GATE_MAX,
        ARP_GATE_DEFAULT,
        fmt="{:.2f}",
        suffix=" s",
    ),
    ParamSpec(
        "velocity",
        "Vel",
        ARP_SET_VELOCITY,
        float(ARP_VELOCITY_MIN),
        float(ARP_VELOCITY_MAX),
        float(ARP_VELOCITY_DEFAULT),
        integer=True,
        fmt="{:d}",
    ),
)
ARP_PARAM_MAP: dict[str, ParamSpec] = {
    spec.key: spec for spec in ARP_PARAMS
}

#: The evolution depth is a knob of its own (row 2 of the Arp group).
ARP_EVOLVE_DEPTH_PARAM = ParamSpec(
    "evolve_depth",
    "Depth",
    ARP_SET_EVOLVE_DEPTH,
    0.0,
    1.0,
    ARP_EVOLVE_DEPTH_DEFAULT,
)

ARP_SETTING_ADDRESSES: dict[str, str] = {
    "step_seconds": ARP_SET_STEP,
    "gate_seconds": ARP_SET_GATE,
    "velocity": ARP_SET_VELOCITY,
    "octave_offsets": ARP_SET_OCTAVES,
    "shared_mode": ARP_SET_SHARED_MODE,
    "order": ARP_SET_ORDER,
    "seed": ARP_SET_SEED,
    "sync_mode": ARP_SET_SYNC,
    "division": ARP_SET_DIVISION,
    "evolve": ARP_SET_EVOLVE,
    "evolve_beats": ARP_SET_EVOLVE_BEATS,
    "evolve_depth": ARP_SET_EVOLVE_DEPTH,
}
ARP_SETTING_ALIASES: dict[str, str] = {
    "step": "step_seconds",
    "gate": "gate_seconds",
    "octaves": "octave_offsets",
    "shared": "shared_mode",
    "sync": "sync_mode",
    "evolve_every": "evolve_beats",
    "depth": "evolve_depth",
}

# -- Stage 8.3d lead -------------------------------------------------------

LEAD_SETTING_ADDRESSES: dict[str, str] = {
    "enabled": LEAD_SET_ENABLED,
    "grid": LEAD_SET_GRID,
    "phrase_bars": LEAD_SET_PHRASE_BARS,
    "density": LEAD_SET_DENSITY,
    "gate_beats": LEAD_SET_GATE,
    "velocity": LEAD_SET_VELOCITY,
    "octave_range": LEAD_SET_OCTAVE_RANGE,
    "base_octave": LEAD_SET_BASE_OCTAVE,
    "variation": LEAD_SET_VARIATION,
    "variation_bars": LEAD_SET_VARIATION_BARS,
    "swing": LEAD_SET_SWING,
    "groove_link": LEAD_SET_GROOVE_LINK,
    "seed": LEAD_SET_SEED,
    "gate_mode": LEAD_SET_GATE_MODE,
}
LEAD_SETTING_ALIASES: dict[str, str] = {
    "gate": "gate_beats",
    "bars": "phrase_bars",
    "phrase": "phrase_bars",
    "octaves": "octave_range",
    "base": "base_octave",
    "var_every": "variation_bars",
    "link": "groove_link",
}

LEAD_PARAMS: tuple[ParamSpec, ...] = (
    ParamSpec(
        "density",
        "Density",
        LEAD_SET_DENSITY,
        LEAD_DENSITY_MIN,
        LEAD_DENSITY_MAX,
        LEAD_DEFAULT_DENSITY,
    ),
    ParamSpec(
        "gate_beats",
        "Gate",
        LEAD_SET_GATE,
        LEAD_GATE_MIN,
        LEAD_GATE_MAX,
        LEAD_DEFAULT_GATE,
        fmt="{:.2f}",
        suffix=" b",
    ),
    ParamSpec(
        "velocity",
        "Vel",
        LEAD_SET_VELOCITY,
        float(LEAD_VELOCITY_MIN),
        float(LEAD_VELOCITY_MAX),
        float(LEAD_DEFAULT_VELOCITY),
        integer=True,
        fmt="{:d}",
    ),
    ParamSpec(
        "swing", "Swing", LEAD_SET_SWING, 0.0, 1.0, 0.0
    ),
)
LEAD_PARAM_MAP: dict[str, ParamSpec] = {
    spec.key: spec for spec in LEAD_PARAMS
}

# -- Stage 8.3d drone modes ------------------------------------------------

DRONE_SETTING_ADDRESSES: dict[str, str] = {
    "mode": DRONE_SET_MODE,
    "velocity": DRONE_SET_VELOCITY,
    "pulse_notes": DRONE_SET_PULSE_NOTES,
    "pulse_grid": DRONE_SET_PULSE_GRID,
    "pulse_gate": DRONE_SET_PULSE_GATE,
    "pulse_octave_span": DRONE_SET_PULSE_SPAN,
    "slide_direction": DRONE_SET_SLIDE_DIRECTION,
    "slide_semitones": DRONE_SET_SLIDE_SEMITONES,
    "slide_seconds": DRONE_SET_SLIDE_SECONDS,
    "hold_beats": DRONE_SET_HOLD_BEATS,
    "seed": DRONE_SET_SEED,
    "seq_notes": DRONE_SET_SEQ_NOTES,
    "seq_bars": DRONE_SET_SEQ_BARS,
    "seq_gate": DRONE_SET_SEQ_GATE,
}
DRONE_SETTING_ALIASES: dict[str, str] = {
    "span": "pulse_octave_span",
    "pulse_span": "pulse_octave_span",
    "dir": "slide_direction",
    "semis": "slide_semitones",
    "slide": "slide_seconds",
    "hold": "hold_beats",
}

DRONE_VELOCITY_PARAM = ParamSpec(
    "velocity",
    "Vel",
    DRONE_SET_VELOCITY,
    float(DRONE_VELOCITY_MIN),
    float(DRONE_VELOCITY_MAX),
    float(DRONE_DEFAULT_VELOCITY),
    integer=True,
    fmt="{:d}",
)
DRONE_PULSE_GATE_PARAM = ParamSpec(
    "pulse_gate",
    "Gate",
    DRONE_SET_PULSE_GATE,
    DRONE_PULSE_GATE_MIN,
    DRONE_PULSE_GATE_MAX,
    DRONE_DEFAULT_PULSE_GATE,
    fmt="{:.2f}",
    suffix=" b",
)
DRONE_SEQ_GATE_PARAM = ParamSpec(
    "seq_gate",
    "Gate",
    DRONE_SET_SEQ_GATE,
    DRONE_SEQ_GATE_MIN,
    DRONE_SEQ_GATE_MAX,
    DRONE_DEFAULT_SEQ_GATE,
    fmt="{:.0%}",
)

DRONE_SLIDE_PARAM = ParamSpec(
    "slide_seconds",
    "Slide",
    DRONE_SET_SLIDE_SECONDS,
    DRONE_SLIDE_SECONDS_MIN,
    DRONE_SLIDE_SECONDS_MAX,
    DRONE_DEFAULT_SLIDE_SECONDS,
    fmt="{:.2f}",
    suffix=" s",
)

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


def _validate_bpm(value: Any) -> float:
    return clamp(_as_finite_float(value, "bpm"), BPM_MIN, BPM_MAX)


def _validate_meter_num(value: Any) -> int:
    return clamp_int(value, METER_NUM_MIN, METER_NUM_MAX)


def _validate_meter_den(value: Any) -> int:
    denominator = int(round(_as_finite_float(value, "meter denominator")))
    if denominator not in METER_DENOMINATORS:
        raise ValueError(
            "meter denominator must be one of "
            + ", ".join(str(item) for item in METER_DENOMINATORS)
        )
    return denominator


def _validate_drum_rate(value: Any) -> float:
    numeric = _as_finite_float(value, "drum rate")
    for allowed in DRUM_RATES:
        if math.isclose(numeric, allowed, rel_tol=0.0, abs_tol=1e-9):
            return allowed
    raise ValueError("drum rate must be 0.5, 1.0, or 2.0")


def _validate_drum_note(value: Any) -> int:
    note = clamp_int(value, 0, 127)
    return note


def _validate_groove_preset(value: Any) -> str:
    preset = str(value).strip().lower()
    if preset not in GROOVE_PRESETS:
        raise ValueError(
            "preset must be one of " + ", ".join(GROOVE_PRESETS)
        )
    return preset


def _validate_unit(value: Any, label: str = "value") -> float:
    return clamp(_as_finite_float(value, label), 0.0, 1.0)


def _validate_grid_lane(value: Any) -> int:
    lane = clamp_int(value, 0, 15)
    numeric = _as_finite_float(value, "lane")
    if not math.isclose(float(lane), numeric, rel_tol=0.0, abs_tol=1e-9):
        raise ValueError("lane must be a whole number in 0..15")
    return lane


def _validate_grid_step(value: Any) -> int:
    step = clamp_int(value, 0, MAX_GRID_STEPS - 1)
    numeric = _as_finite_float(value, "step")
    if not math.isclose(float(step), numeric, rel_tol=0.0, abs_tol=1e-9):
        raise ValueError(f"step must be a whole number in 0..{MAX_GRID_STEPS - 1}")
    return step


def _validate_seed(value: Any) -> int:
    return clamp_int(value, 0, GROOVE_SEED_MAX)


def _validate_bass_preset(value: Any) -> str:
    preset = str(value).strip().lower()
    if preset not in BASS_PRESETS:
        raise ValueError(
            "bass preset must be one of " + ", ".join(BASS_PRESETS)
        )
    return preset


def _validate_bass_grid(value: Any) -> str:
    grid = str(value).strip()
    if grid not in BASS_GRID_STEPS:
        raise ValueError(
            "bass grid must be one of " + ", ".join(BASS_GRIDS)
        )
    return grid


def _validate_bass_pitch_mode(value: Any) -> str:
    mode = str(value).strip().lower()
    if mode not in BASS_PITCH_MODES:
        raise ValueError(
            "bass pitch mode must be one of " + ", ".join(BASS_PITCH_MODES)
        )
    return mode


def _validate_bass_length(value: Any) -> int:
    return clamp_int(value, BASS_LENGTH_MIN, BASS_LENGTH_MAX)


def _validate_bass_gate(value: Any) -> float:
    return clamp(
        _as_finite_float(value, "bass gate"), BASS_GATE_MIN, BASS_GATE_MAX
    )


def _validate_bass_octave_range(value: Any) -> int:
    return clamp_int(value, BASS_OCTAVE_RANGE_MIN, BASS_OCTAVE_RANGE_MAX)


def _validate_bass_base_octave(value: Any) -> int:
    return clamp_int(value, BASS_BASE_OCTAVE_MIN, BASS_BASE_OCTAVE_MAX)


def _validate_bass_seed(value: Any) -> int:
    return clamp_int(value, 0, BASS_SEED_MAX)


def _validate_bass_sub_octaves(value: Any) -> int:
    numeric = _as_finite_float(value, "sub octaves")
    octaves = int(round(numeric))
    if octaves not in BASS_SUB_OCTAVES:
        raise ValueError("sub octaves must be 1 or 2")
    return octaves


# -- Stage 8.3b arp validators ---------------------------------------------


def _validate_arp_setting_name(name: Any) -> str:
    key = str(name).strip().lower()
    key = ARP_SETTING_ALIASES.get(key, key)
    if key not in ARP_SETTING_ADDRESSES:
        raise ValueError(f"unknown arp setting {name!r}")
    return key


def _validate_arp_step(value: Any) -> float:
    return clamp(_as_finite_float(value, "arp step"), ARP_STEP_MIN, ARP_STEP_MAX)


def _validate_arp_gate(value: Any, step: Any = None) -> float:
    """Clamp the gate to 0.01..2.0 and, when given, to the step duration."""

    high = ARP_GATE_MAX
    if step is not None:
        high = min(ARP_GATE_MAX, _validate_arp_step(step))
    return clamp(_as_finite_float(value, "arp gate"), ARP_GATE_MIN, high)


def _validate_arp_velocity(value: Any) -> int:
    return clamp_int(value, ARP_VELOCITY_MIN, ARP_VELOCITY_MAX)


def _validate_arp_octaves(value: Any) -> tuple[int, int, int]:
    if isinstance(value, (str, bytes)):
        raise TypeError("arp octaves must be three integers")
    try:
        values = list(value)
    except TypeError:
        raise TypeError("arp octaves must be three integers") from None
    if len(values) != 3:
        raise ValueError("arp octaves must contain exactly three integers")
    cleaned = [
        clamp_int(item, ARP_OCTAVE_MIN, ARP_OCTAVE_MAX) for item in values
    ]
    return cleaned[0], cleaned[1], cleaned[2]


def _validate_arp_shared_mode(value: Any) -> str:
    text = str(value).strip().lower()
    if text not in ARP_SHARED_MODES:
        raise ValueError("arp shared_mode must be 'anchor' or 'random'")
    return text


def _validate_arp_order(value: Any) -> str:
    text = str(value).strip().lower()
    if text not in ARP_ORDERS:
        raise ValueError("arp order must be one of " + ", ".join(ARP_ORDERS))
    return text


def _validate_arp_seed(value: Any) -> int:
    return clamp_int(value, 0, ARP_SEED_MAX)


def _validate_arp_sync(value: Any) -> str:
    if isinstance(value, bool):
        return "beat" if value else "free"
    text = str(value).strip().lower()
    if text in ("beat", "sync", "synced", "on", "true", "1"):
        return "beat"
    if text in ("free", "off", "false", "0"):
        return "free"
    raise ValueError("arp sync must be 'free' or 'beat'")


def _validate_arp_division(value: Any) -> str:
    text = str(value).strip().upper().replace(" ", "")
    if text not in ARP_DIVISION_QUARTERS:
        raise ValueError("arp division must be one of " + ", ".join(ARP_DIVISIONS))
    return text


def _validate_arp_evolve(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    try:
        numeric = _as_finite_float(value, "arp evolve")
    except TypeError:
        raise TypeError("arp evolve must be a boolean or 0/1") from None
    if numeric not in (0.0, 1.0):
        raise ValueError("arp evolve must be 0 or 1")
    return bool(numeric)


def _validate_arp_evolve_beats(value: Any) -> int:
    return clamp_int(value, ARP_EVOLVE_BEATS_MIN, ARP_EVOLVE_BEATS_MAX)


def _validate_arp_evolve_depth(value: Any) -> float:
    return clamp(_as_finite_float(value, "arp evolve depth"), 0.0, 1.0)


# -- Stage 8.3d lead validators --------------------------------------------


def _validate_lead_setting_name(name: Any) -> str:
    key = str(name).strip().lower()
    key = LEAD_SETTING_ALIASES.get(key, key)
    if key not in LEAD_SETTING_ADDRESSES:
        raise ValueError(f"unknown lead setting {name!r}")
    return key


def _validate_lead_grid(value: Any) -> str:
    grid = str(value).strip().upper().replace(" ", "")
    if grid not in LEAD_GRIDS:
        raise ValueError("lead grid must be one of " + ", ".join(LEAD_GRIDS))
    return grid


def _validate_lead_phrase_bars(value: Any) -> int:
    return clamp_int(value, LEAD_PHRASE_BARS_MIN, LEAD_PHRASE_BARS_MAX)


def _validate_lead_density(value: Any) -> float:
    return clamp(
        _as_finite_float(value, "lead density"),
        LEAD_DENSITY_MIN,
        LEAD_DENSITY_MAX,
    )


def _validate_lead_gate(value: Any) -> float:
    return clamp(
        _as_finite_float(value, "lead gate"), LEAD_GATE_MIN, LEAD_GATE_MAX
    )


def _validate_lead_velocity(value: Any) -> int:
    return clamp_int(value, LEAD_VELOCITY_MIN, LEAD_VELOCITY_MAX)


def _validate_lead_octave_range(value: Any) -> int:
    return clamp_int(value, LEAD_OCTAVE_RANGE_MIN, LEAD_OCTAVE_RANGE_MAX)


def _validate_lead_base_octave(value: Any) -> int:
    return clamp_int(value, LEAD_BASE_OCTAVE_MIN, LEAD_BASE_OCTAVE_MAX)


def _validate_lead_variation(value: Any) -> str:
    text = str(value).strip().lower()
    if text not in LEAD_VARIATIONS:
        raise ValueError(
            "lead variation must be one of " + ", ".join(LEAD_VARIATIONS)
        )
    return text


def _validate_lead_variation_bars(value: Any) -> int:
    return clamp_int(value, LEAD_VARIATION_BARS_MIN, LEAD_VARIATION_BARS_MAX)


def _validate_lead_seed(value: Any) -> int:
    return clamp_int(value, 0, LEAD_SEED_MAX)


def _validate_lead_groove_link(value: Any) -> bool:
    return _to_bool(value, "lead groove_link")


def _validate_lead_gate_mode(value: Any) -> str:
    text = str(value).strip().lower()
    if text not in LEAD_INPUT_GATE_MODES:
        raise ValueError(
            "lead gate_mode must be one of " + ", ".join(LEAD_INPUT_GATE_MODES)
        )
    return text


def _validate_lead_input_count_in(value: Any) -> str:
    text = str(value).strip().lower()
    for option in LEAD_INPUT_COUNT_INS:
        if option == text:
            return option
    raise ValueError(
        "count-in must be one of " + ", ".join(LEAD_INPUT_COUNT_INS)
    )


def _validate_lead_input_bars(value: Any) -> int:
    return clamp_int(value, LEAD_INPUT_BARS_MIN, LEAD_INPUT_BARS_MAX)


def _validate_lead_input_source(value: Any) -> str:
    text = str(value).strip().lower()
    if text not in LEAD_INPUT_SOURCES:
        raise ValueError(
            "lead source must be one of " + ", ".join(LEAD_INPUT_SOURCES)
        )
    return text


def _validate_midi_note(value: Any) -> int:
    return clamp_int(value, 0, 127)


# -- Stage 8.3d drone validators -------------------------------------------


def _validate_drone_mode(value: Any) -> str:
    text = str(value).strip().lower()
    if text not in DRONE_MODES:
        raise ValueError("drone mode must be one of " + ", ".join(DRONE_MODES))
    return text


def _validate_drone_velocity(value: Any) -> int:
    return clamp_int(value, DRONE_VELOCITY_MIN, DRONE_VELOCITY_MAX)


def _validate_drone_pulse_notes(value: Any) -> int:
    return clamp_int(value, DRONE_PULSE_NOTES_MIN, DRONE_PULSE_NOTES_MAX)


def _validate_drone_pulse_grid(value: Any) -> str:
    grid = str(value).strip().upper().replace(" ", "")
    if grid not in DRONE_PULSE_GRIDS:
        raise ValueError(
            "drone pulse grid must be one of " + ", ".join(DRONE_PULSE_GRIDS)
        )
    return grid


def _validate_drone_pulse_gate(value: Any) -> float:
    return clamp(
        _as_finite_float(value, "drone pulse gate"),
        DRONE_PULSE_GATE_MIN,
        DRONE_PULSE_GATE_MAX,
    )


def _validate_drone_pulse_span(value: Any) -> int:
    return clamp_int(value, DRONE_PULSE_SPAN_MIN, DRONE_PULSE_SPAN_MAX)


def _validate_drone_slide_direction(value: Any) -> str:
    text = str(value).strip().lower()
    if text not in DRONE_SLIDE_DIRECTIONS:
        raise ValueError(
            "drone slide direction must be one of "
            + ", ".join(DRONE_SLIDE_DIRECTIONS)
        )
    return text


def _validate_drone_slide_semitones(value: Any) -> int:
    return clamp_int(
        value, DRONE_SLIDE_SEMITONES_MIN, DRONE_SLIDE_SEMITONES_MAX
    )


def _validate_drone_slide_seconds(value: Any) -> float:
    return clamp(
        _as_finite_float(value, "drone slide seconds"),
        DRONE_SLIDE_SECONDS_MIN,
        DRONE_SLIDE_SECONDS_MAX,
    )


def _validate_drone_hold_beats(value: Any) -> float:
    return clamp(
        _as_finite_float(value, "drone hold beats"),
        DRONE_HOLD_BEATS_MIN,
        DRONE_HOLD_BEATS_MAX,
    )


def _validate_drone_seed(value: Any) -> int:
    return clamp_int(value, 0, DRONE_SEED_MAX)


def _validate_drone_seq_notes(value: Any) -> int:
    return clamp_int(value, DRONE_SEQ_NOTES_MIN, DRONE_SEQ_NOTES_MAX)


def _validate_drone_seq_bars(value: Any) -> str:
    text = str(value).strip().replace(" ", "").replace("bar", "").replace("s", "")
    if text in DRONE_SEQ_BARS_VALUE:
        return text
    try:
        numeric = float(text)
    except (TypeError, ValueError):
        raise ValueError(
            "drone sequence length must be one of " + ", ".join(DRONE_SEQ_BARS)
        ) from None
    for name, bars in DRONE_SEQ_BARS_VALUE.items():
        if abs(numeric - bars) < 1e-9:
            return name
    raise ValueError(
        "drone sequence length must be one of " + ", ".join(DRONE_SEQ_BARS)
    )


def _validate_drone_seq_gate(value: Any) -> float:
    return clamp(
        _as_finite_float(value, "drone sequence gate"),
        DRONE_SEQ_GATE_MIN,
        DRONE_SEQ_GATE_MAX,
    )


def grid_steps_for_meter(numerator: Any, denominator: Any) -> int:
    """Editable 16th columns for a meter, capped at :data:`MAX_GRID_STEPS`."""

    num = _validate_meter_num(numerator)
    den = _validate_meter_den(denominator)
    steps = num * (16 // den)
    return max(1, min(MAX_GRID_STEPS, steps))


def _validate_loop_mode(value: Any) -> str:
    mode = str(value).strip().lower()
    if mode not in LOOP_MODES:
        raise ValueError("loop mode must be 'free' or 'beat'")
    return mode


def _validate_loop_beats(value: Any) -> float:
    return clamp(_as_finite_float(value, "loop beats"), LOOP_BEATS_MIN, LOOP_BEATS_MAX)


def _validate_loop_slot(value: Any) -> int:
    return clamp_int(
        _as_finite_float(value, "loop memory slot"), 0, LOOP_MEM_SLOTS - 1
    )


def _validate_loop_rate_value(value: Any) -> float:
    """Snap a rate to the nearest permitted ratio (Stage 8.6 §4.2)."""

    numeric = _as_finite_float(value, "loop rate")
    nearest = min(LOOP_RATE_VALUES, key=lambda allowed: abs(allowed - numeric))
    return float(nearest)


def _validate_loop_edit_param(param: Any) -> "LoopEditParam":
    name = str(param)
    spec = LOOP_EDIT_PARAM_MAP.get(name)
    if spec is None:
        raise ValueError(
            f"unknown loop edit parameter {param!r}; use one of "
            f"{tuple(sorted(LOOP_EDIT_PARAM_MAP))}"
        )
    return spec


def _loop_bit(value: Any, label: str) -> int:
    """Tolerant 0/1 conversion used by the parsers (clamped, never raises)."""

    if isinstance(value, bool):
        return 1 if value else 0
    numeric = _as_finite_float(value, label)
    return 1 if numeric >= 0.5 else 0


def _loop_edit_value(spec: "LoopEditParam", value: Any) -> Any:
    """Validate/clamp one edit value for the wire."""

    if spec.kind == "choice":
        text = str(value)
        if text not in spec.choices:
            raise ValueError(
                f"unknown {spec.name} {value!r}; use one of {spec.choices}"
            )
        return text
    if spec.name == "rate":
        return _validate_loop_rate_value(value)
    if isinstance(value, bool):
        numeric = 1.0 if value else 0.0
    elif isinstance(value, str) and spec.kind == "bool":
        text = value.strip().lower()
        if text in ("1", "on", "true", "yes", "grain", "beat"):
            return 1
        if text in ("0", "off", "false", "no", "normal", "free", ""):
            return 0
        raise ValueError(f"unknown {spec.name} {value!r}; use 0/1 or on/off")
    else:
        numeric = _as_finite_float(value, spec.name)
    clamped = clamp(numeric, spec.low, spec.high)
    if spec.kind == "bool":
        return 1 if clamped >= 0.5 else 0
    if spec.kind == "int":
        return int(round(clamped))
    return float(clamped)


def _validate_loop_seconds(value: Any) -> float:
    return clamp(
        _as_finite_float(value, "loop seconds"), LOOP_LENGTH_MIN, LOOP_LENGTH_MAX
    )


def drum_lane_bit(note: Any) -> int:
    """Return the ``lane_mask`` bit index for a MIDI note (0 for 36)."""

    numeric = int(round(_as_finite_float(note, "drum note")))
    for index, (lane_note, _name, _label) in enumerate(DRUM_LANE_DEFS):
        if lane_note == numeric:
            return index
    raise ValueError(f"note {numeric} is not one of the 16 drum lanes")


def drum_lane_states(mask: Any) -> dict[int, bool]:
    """Decode a 16-bit lane mask into ``{midi_note: enabled}``."""

    value = clamp_int(mask, 0, LANE_MASK_ALL)
    return {
        note: bool(value & (1 << index))
        for index, (note, _name, _label) in enumerate(DRUM_LANE_DEFS)
    }


def drum_lane_mask(states: Mapping[int, bool]) -> int:
    """Encode ``{midi_note: enabled}`` into a 16-bit lane mask."""

    mask = 0
    for note, enabled in states.items():
        if enabled:
            mask |= 1 << drum_lane_bit(note)
    return mask & LANE_MASK_ALL


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


# ---------------------------------------------------------------------------
# Stage 8.1 rhythm / drum / loop-config builders
# ---------------------------------------------------------------------------


def build_set_tempo(bpm: Any) -> tuple[str, list[Any]]:
    """Build ``/ji/rhythm/set_tempo [float bpm]`` clamped to 20-300."""

    return RHYTHM_SET_TEMPO, [float(_validate_bpm(bpm))]


def build_set_meter(numerator: Any, denominator: Any) -> tuple[str, list[Any]]:
    """Build ``/ji/rhythm/set_meter [int num, int den]``.

    The numerator is clamped to 1-16; an unsupported denominator raises
    ``ValueError`` instead of being silently changed.
    """

    return RHYTHM_SET_METER, [
        int(_validate_meter_num(numerator)),
        int(_validate_meter_den(denominator)),
    ]


def build_transport(running: bool) -> tuple[str, list[Any]]:
    """Build ``/ji/rhythm/transport [int running]``."""

    return RHYTHM_TRANSPORT, [1 if running else 0]


def build_rhythm_request() -> tuple[str, list[Any]]:
    """Build ``/ji/rhythm/request`` (ask the bridge to re-broadcast tempo)."""

    return RHYTHM_REQUEST, []


def build_set_drums_enabled(enabled: bool) -> tuple[str, list[Any]]:
    return DRUMS_SET_ENABLED, [1 if enabled else 0]


def build_set_drum_lane(note: Any, enabled: bool) -> tuple[str, list[Any]]:
    return DRUMS_SET_LANE, [int(_validate_drum_note(note)), 1 if enabled else 0]


def build_set_drum_rate(rate: Any) -> tuple[str, list[Any]]:
    return DRUMS_SET_RATE, [float(_validate_drum_rate(rate))]


def build_set_drums_preset(preset: Any) -> tuple[str, list[Any]]:
    return DRUMS_SET_PRESET, [_validate_groove_preset(preset)]


def build_set_groove_modifier(param: Any, value: Any) -> tuple[str, list[Any]]:
    """Build one Stage 8.2 groove modifier message, clamped to 0..1."""

    key = str(param).strip().lower()
    spec = GROOVE_PARAM_MAP.get(key)
    if spec is None:
        raise ValueError(f"unknown groove modifier {param!r}")
    return spec.address, [float(_validate_unit(value, key))]


def build_set_drum_density(value: Any) -> tuple[str, list[Any]]:
    return build_set_groove_modifier("density", value)


def build_set_drum_syncopation(value: Any) -> tuple[str, list[Any]]:
    return build_set_groove_modifier("syncopation", value)


def build_set_drum_swing(value: Any) -> tuple[str, list[Any]]:
    return build_set_groove_modifier("swing", value)


def build_set_drum_probability(value: Any) -> tuple[str, list[Any]]:
    return build_set_groove_modifier("probability", value)


def build_set_drum_step(
    lane: Any, step: Any, enabled: bool
) -> tuple[str, list[Any]]:
    return DRUMS_SET_STEP, [
        int(_validate_grid_lane(lane)),
        int(_validate_grid_step(step)),
        1 if enabled else 0,
    ]


def build_set_drum_seed(seed: Any) -> tuple[str, list[Any]]:
    return DRUMS_SET_SEED, [int(_validate_seed(seed))]


def build_reset_drum_grid() -> tuple[str, list[Any]]:
    return DRUMS_RESET_GRID, []


# ---------------------------------------------------------------------------
# Stage 8.3a bass builders
# ---------------------------------------------------------------------------


def build_set_bass_setting(name: Any, value: Any) -> tuple[str, list[Any]]:
    """Build one ``/ji/bass/set_*`` message with a validated argument."""

    key = str(name).strip().lower()
    address = BASS_SETTING_ADDRESSES.get(key)
    if address is None:
        raise ValueError(f"unknown bass setting {name!r}")
    if key in ("enabled", "root_downbeat", "sub_enabled"):
        return address, [1 if bool(value) else 0]
    if key == "preset":
        return address, [_validate_bass_preset(value)]
    if key == "grid":
        return address, [_validate_bass_grid(value)]
    if key == "pitch_mode":
        return address, [_validate_bass_pitch_mode(value)]
    if key == "length_steps":
        return address, [int(_validate_bass_length(value))]
    if key == "seed":
        return address, [int(_validate_bass_seed(value))]
    if key == "octave_range":
        return address, [int(_validate_bass_octave_range(value))]
    if key == "base_octave":
        return address, [int(_validate_bass_base_octave(value))]
    if key == "sub_octaves":
        return address, [int(_validate_bass_sub_octaves(value))]
    if key == "gate_beats":
        return address, [float(_validate_bass_gate(value))]
    return address, [float(_validate_unit(value, key))]


def build_set_bass_enabled(enabled: Any) -> tuple[str, list[Any]]:
    return build_set_bass_setting("enabled", enabled)


def build_set_bass_preset(preset: Any) -> tuple[str, list[Any]]:
    return build_set_bass_setting("preset", preset)


def build_set_bass_grid(grid: Any) -> tuple[str, list[Any]]:
    return build_set_bass_setting("grid", grid)


def build_set_bass_length(steps: Any) -> tuple[str, list[Any]]:
    return build_set_bass_setting("length_steps", steps)


def build_set_bass_param(param: Any, value: Any) -> tuple[str, list[Any]]:
    return build_set_bass_setting(param, value)


def build_set_bass_pitch_mode(mode: Any) -> tuple[str, list[Any]]:
    return build_set_bass_setting("pitch_mode", mode)


def build_set_bass_root_downbeat(enabled: Any) -> tuple[str, list[Any]]:
    return build_set_bass_setting("root_downbeat", enabled)


def build_set_bass_octave_range(value: Any) -> tuple[str, list[Any]]:
    return build_set_bass_setting("octave_range", value)


def build_set_bass_base_octave(value: Any) -> tuple[str, list[Any]]:
    return build_set_bass_setting("base_octave", value)


def build_set_bass_seed(seed: Any) -> tuple[str, list[Any]]:
    return build_set_bass_setting("seed", seed)


def build_set_bass_sub_enabled(enabled: Any) -> tuple[str, list[Any]]:
    return build_set_bass_setting("sub_enabled", enabled)


def build_set_bass_sub_octaves(value: Any) -> tuple[str, list[Any]]:
    return build_set_bass_setting("sub_octaves", value)


# ---------------------------------------------------------------------------
# Stage 8.7 breath / bass hit-row builders
# ---------------------------------------------------------------------------


def _breath_param_name(name: Any) -> str:
    key = str(name).strip().lower()
    if key not in BREATH_PARAM_ADDRESSES:
        raise ValueError(
            f"unknown breath parameter {name!r}; use one of "
            f"{BREATH_PARAM_NAMES}"
        )
    return key


def _validate_breath_shape(value: Any) -> str:
    shape = str(value).strip().lower()
    if shape not in BREATH_SHAPES:
        raise ValueError(
            f"unknown breath shape {value!r}; use one of {BREATH_SHAPES}"
        )
    return shape


def _validate_breath_period(value: Any) -> int:
    return clamp_int(
        _as_finite_float(value, "period_bars"),
        BREATH_PERIOD_BARS_MIN,
        BREATH_PERIOD_BARS_MAX,
    )


def build_breath_param(name: Any, value: Any) -> tuple[str, list[Any]]:
    """Build one ``/ji/breath/set_*`` message (value validated/clamped)."""

    key = _breath_param_name(name)
    if key == "enabled":
        return BREATH_SET_ENABLED, [1 if _to_bool(value, "breath enabled") else 0]
    if key == "arp_sync":
        return BREATH_SET_ARP_SYNC, [1 if _to_bool(value, "arp sync") else 0]
    if key == "period_bars":
        return BREATH_SET_PERIOD, [_validate_breath_period(value)]
    if key == "shape":
        return BREATH_SET_SHAPE, [_validate_breath_shape(value)]
    clamped = clamp(
        _as_finite_float(value, f"breath {key}"),
        0.0,
        BREATH_DEPTH_MAX,
    )
    return BREATH_PARAM_ADDRESSES[key], [float(clamped)]


def build_bass_hits_enabled(enabled: Any) -> tuple[str, list[Any]]:
    return BASS_SET_HITS_ENABLED, [1 if _to_bool(enabled, "hits enabled") else 0]


def build_bass_hit(index: Any, on: Any) -> tuple[str, list[Any]]:
    position = clamp_int(_as_finite_float(index, "hit index"), 0, BASS_HITS_MAX - 1)
    return BASS_SET_HIT, [position, 1 if _to_bool(on, "hit on") else 0]


def build_bass_clear_hits() -> tuple[str, list[Any]]:
    return BASS_CLEAR_HITS, []


def build_bass_copy_hits() -> tuple[str, list[Any]]:
    return BASS_COPY_HITS, []


# ---------------------------------------------------------------------------
# Stage 8.3b arp builders
# ---------------------------------------------------------------------------


def build_set_arp_setting(name: Any, value: Any) -> tuple[str, list[Any]]:
    """Build one ``/ji/arp/set_*`` message with a validated argument."""

    key = _validate_arp_setting_name(name)
    address = ARP_SETTING_ADDRESSES[key]
    if key == "octave_offsets":
        return address, [int(item) for item in _validate_arp_octaves(value)]
    if key == "shared_mode":
        return address, [_validate_arp_shared_mode(value)]
    if key == "order":
        return address, [_validate_arp_order(value)]
    if key == "velocity":
        return address, [int(_validate_arp_velocity(value))]
    if key == "seed":
        return address, [int(_validate_arp_seed(value))]
    if key == "sync_mode":
        return address, [_validate_arp_sync(value)]
    if key == "division":
        return address, [_validate_arp_division(value)]
    if key == "evolve":
        return address, [1 if _validate_arp_evolve(value) else 0]
    if key == "evolve_beats":
        return address, [int(_validate_arp_evolve_beats(value))]
    if key == "evolve_depth":
        return address, [float(_validate_arp_evolve_depth(value))]
    if key == "step_seconds":
        return address, [float(_validate_arp_step(value))]
    return address, [float(_validate_arp_gate(value))]


def build_set_arp_step(seconds: Any) -> tuple[str, list[Any]]:
    return build_set_arp_setting("step_seconds", seconds)


def build_set_arp_gate(seconds: Any, step: Any = None) -> tuple[str, list[Any]]:
    return ARP_SET_GATE, [float(_validate_arp_gate(seconds, step))]


def build_set_arp_velocity(velocity: Any) -> tuple[str, list[Any]]:
    return build_set_arp_setting("velocity", velocity)


def build_set_arp_octaves(
    shared: Any, non_shared_1: Any, non_shared_2: Any
) -> tuple[str, list[Any]]:
    return build_set_arp_setting(
        "octave_offsets", [shared, non_shared_1, non_shared_2]
    )


def build_set_arp_shared_mode(mode: Any) -> tuple[str, list[Any]]:
    return build_set_arp_setting("shared_mode", mode)


def build_set_arp_order(order: Any) -> tuple[str, list[Any]]:
    return build_set_arp_setting("order", order)


def build_set_arp_seed(seed: Any) -> tuple[str, list[Any]]:
    return build_set_arp_setting("seed", seed)


def build_set_arp_sync(sync: Any) -> tuple[str, list[Any]]:
    return build_set_arp_setting("sync_mode", sync)


def build_set_arp_division(division: Any) -> tuple[str, list[Any]]:
    return build_set_arp_setting("division", division)


def build_set_arp_evolve(enabled: Any) -> tuple[str, list[Any]]:
    return build_set_arp_setting("evolve", enabled)


def build_set_arp_evolve_beats(beats: Any) -> tuple[str, list[Any]]:
    return build_set_arp_setting("evolve_beats", beats)


def build_set_arp_evolve_depth(depth: Any) -> tuple[str, list[Any]]:
    return build_set_arp_setting("evolve_depth", depth)


# -- Stage 8.3d lead -------------------------------------------------------

_LEAD_COERCE: dict[str, Callable[[Any], Any]] = {
    "enabled": lambda value: 1 if _to_bool(value, "lead enabled") else 0,
    "grid": _validate_lead_grid,
    "phrase_bars": _validate_lead_phrase_bars,
    "density": _validate_lead_density,
    "gate_beats": _validate_lead_gate,
    "velocity": _validate_lead_velocity,
    "octave_range": _validate_lead_octave_range,
    "base_octave": _validate_lead_base_octave,
    "variation": _validate_lead_variation,
    "variation_bars": _validate_lead_variation_bars,
    "swing": lambda value: _validate_unit(value, "lead swing"),
    "groove_link": lambda value: 1 if _to_bool(value, "lead groove_link") else 0,
    "seed": _validate_lead_seed,
    "gate_mode": _validate_lead_gate_mode,
}


def build_set_lead_setting(name: Any, value: Any) -> tuple[str, list[Any]]:
    """Build one validated ``/ji/lead/set_*`` message."""

    key = _validate_lead_setting_name(name)
    return LEAD_SETTING_ADDRESSES[key], [_LEAD_COERCE[key](value)]


def build_set_lead_enabled(enabled: Any) -> tuple[str, list[Any]]:
    return build_set_lead_setting("enabled", enabled)


def build_lead_vary() -> tuple[str, list[Any]]:
    return LEAD_VARY, []


# -- Stage 9.1 lead input and melody builders ------------------------------


def build_set_lead_input_enabled(enabled: Any) -> tuple[str, list[Any]]:
    return LEAD_INPUT_SET_ENABLED, [1 if _to_bool(enabled, "lead input enabled") else 0]


def build_set_lead_input_quantize(value: Any) -> tuple[str, list[Any]]:
    return LEAD_INPUT_SET_QUANTIZE, [_validate_unit(value, "lead input quantize")]


def build_set_lead_input_count_in(value: Any) -> tuple[str, list[Any]]:
    return LEAD_INPUT_SET_COUNT_IN, [_validate_lead_input_count_in(value)]


def build_set_lead_input_bars(value: Any) -> tuple[str, list[Any]]:
    return LEAD_INPUT_SET_BARS, [_validate_lead_input_bars(value)]


def build_lead_input_note_on(midi: Any, velocity: Any) -> tuple[str, list[Any]]:
    return LEAD_INPUT_NOTE_ON, [
        _validate_midi_note(midi),
        clamp_int(velocity, 0, 127),
    ]


def build_lead_input_note_off(midi: Any) -> tuple[str, list[Any]]:
    return LEAD_INPUT_NOTE_OFF, [_validate_midi_note(midi)]


def build_lead_input_record() -> tuple[str, list[Any]]:
    return LEAD_INPUT_RECORD, []


def build_lead_input_stop() -> tuple[str, list[Any]]:
    return LEAD_INPUT_STOP, []


def build_lead_input_revert() -> tuple[str, list[Any]]:
    return LEAD_INPUT_REVERT, []


def build_lead_input_clear() -> tuple[str, list[Any]]:
    return LEAD_INPUT_CLEAR, []


def build_lead_input_refresh() -> tuple[str, list[Any]]:
    return LEAD_INPUT_REFRESH, []


def build_lead_melody_save(name: Any) -> tuple[str, list[Any]]:
    return LEAD_MELODY_SAVE, [_state_name(name)]


def build_lead_melody_load(name: Any) -> tuple[str, list[Any]]:
    return LEAD_MELODY_LOAD, [_state_name(name)]


def build_lead_melody_delete(name: Any) -> tuple[str, list[Any]]:
    return LEAD_MELODY_DELETE, [_state_name(name)]


def build_lead_melody_request() -> tuple[str, list[Any]]:
    return LEAD_MELODY_REQUEST, []


# -- Stage 8.3d drone modes ------------------------------------------------

_DRONE_COERCE: dict[str, Callable[[Any], Any]] = {
    "mode": _validate_drone_mode,
    "velocity": _validate_drone_velocity,
    "pulse_notes": _validate_drone_pulse_notes,
    "pulse_grid": _validate_drone_pulse_grid,
    "pulse_gate": _validate_drone_pulse_gate,
    "pulse_octave_span": _validate_drone_pulse_span,
    "slide_direction": _validate_drone_slide_direction,
    "slide_semitones": _validate_drone_slide_semitones,
    "slide_seconds": _validate_drone_slide_seconds,
    "hold_beats": _validate_drone_hold_beats,
    "seed": _validate_drone_seed,
    "seq_notes": _validate_drone_seq_notes,
    "seq_bars": _validate_drone_seq_bars,
    "seq_gate": _validate_drone_seq_gate,
}


def build_set_drone_setting(name: Any, value: Any) -> tuple[str, list[Any]]:
    """Build one validated ``/ji/drone/set_*`` message."""

    key = str(name).strip().lower()
    key = DRONE_SETTING_ALIASES.get(key, key)
    if key not in DRONE_SETTING_ADDRESSES:
        raise ValueError(f"unknown drone setting {name!r}")
    return DRONE_SETTING_ADDRESSES[key], [_DRONE_COERCE[key](value)]


# -- Stage 8.4 home sets ---------------------------------------------------

HOME_SETTING_ADDRESSES: dict[str, str] = {
    "auto": HOME_SET_AUTO,
    "every": HOME_SET_EVERY,
    "seed": HOME_SET_SEED,
    "max_drift": HOME_SET_DRIFT,
    "return_glide": HOME_SET_GLIDE,
}

HOME_SETTING_ALIASES: dict[str, str] = {
    "drift": "max_drift",
    "glide": "return_glide",
    "return_glide_seconds": "return_glide",
}

_HOME_COERCE: dict[str, Callable[[Any], Any]] = {
    "auto": lambda value: 1 if _to_bool(value, "home auto") else 0,
    "every": lambda value: clamp_int(value, HOME_EVERY_MIN, HOME_EVERY_MAX),
    "seed": lambda value: clamp_int(value, HOME_SEED_MIN, HOME_SEED_MAX),
    "max_drift": lambda value: clamp_int(value, HOME_DRIFT_MIN, HOME_DRIFT_MAX),
    "return_glide": lambda value: clamp(value, HOME_GLIDE_MIN, HOME_GLIDE_MAX),
}


def _validate_home_slot(slot: Any) -> int:
    return clamp_int(slot, 0, HOME_SLOTS - 1)


def build_home_capture(slot: Any) -> tuple[str, list[Any]]:
    return HOME_CAPTURE, [_validate_home_slot(slot)]


def build_home_clear(slot: Any) -> tuple[str, list[Any]]:
    return HOME_CLEAR, [_validate_home_slot(slot)]


def build_home_go(slot: Any) -> tuple[str, list[Any]]:
    return HOME_GO, [_validate_home_slot(slot)]


def build_home_return() -> tuple[str, list[Any]]:
    return HOME_RETURN, []


def build_home_jump() -> tuple[str, list[Any]]:
    return HOME_JUMP, []


def build_home_back() -> tuple[str, list[Any]]:
    return HOME_BACK, []


def build_set_home_setting(name: Any, value: Any) -> tuple[str, list[Any]]:
    """Build one validated ``/ji/home/set_*`` message."""

    key = str(name).strip().lower()
    key = HOME_SETTING_ALIASES.get(key, key)
    if key not in HOME_SETTING_ADDRESSES:
        raise ValueError(f"unknown home setting {name!r}")
    return HOME_SETTING_ADDRESSES[key], [_HOME_COERCE[key](value)]


def build_set_home_auto(enabled: Any) -> tuple[str, list[Any]]:
    return build_set_home_setting("auto", enabled)


def build_set_home_every(every: Any) -> tuple[str, list[Any]]:
    return build_set_home_setting("every", every)


def build_set_home_seed(seed: Any) -> tuple[str, list[Any]]:
    return build_set_home_setting("seed", seed)


def build_set_home_drift(drift: Any) -> tuple[str, list[Any]]:
    return build_set_home_setting("max_drift", drift)


def build_set_home_glide(seconds: Any) -> tuple[str, list[Any]]:
    return build_set_home_setting("return_glide", seconds)


# Stage 8.5 performance states.  The bridge owns the name rules and replies
# with one /ji/state/state line; the GUI only refuses an empty name.


def _state_name(value: Any) -> str:
    name = str(value if value is not None else "").strip()
    if not name:
        raise ValueError("a state name cannot be empty")
    return name


def build_state_save(name: Any) -> tuple[str, list[Any]]:
    return STATE_SAVE, [_state_name(name)]


def build_state_load(name: Any) -> tuple[str, list[Any]]:
    return STATE_LOAD, [_state_name(name)]


def build_state_delete(name: Any) -> tuple[str, list[Any]]:
    return STATE_DELETE, [_state_name(name)]


def build_state_request() -> tuple[str, list[Any]]:
    return STATE_REQUEST, []


def build_loop_set_mode(index: Any, mode: Any) -> tuple[str, list[Any]]:
    return LOOP_SET_MODE, [_validate_index(index), str(_validate_loop_mode(mode))]


def build_loop_set_beats(index: Any, beats: Any) -> tuple[str, list[Any]]:
    return LOOP_SET_BEATS, [_validate_index(index), float(_validate_loop_beats(beats))]


def build_loop_set_length(index: Any, seconds: Any) -> tuple[str, list[Any]]:
    return LOOP_SET_LENGTH, [
        _validate_index(index),
        float(_validate_loop_seconds(seconds)),
    ]


# --- Stage 8.6 loop editing builders --------------------------------------


def build_loop_dub(index: Any) -> tuple[str, list[Any]]:
    """Start an overdub on that loop (records fresh when the loop is empty)."""

    return LOOP_DUB, [_validate_index(index)]


def build_loop_fx_fire(index: Any) -> tuple[str, list[Any]]:
    """Build one Cascade ``/ji/loop/fx_fire [index]`` trigger.

    This is a UI -> sclang trigger only: it does not change the edit state and
    sends no reply.  sclang ignores it unless the loop is in cascade mode with
    a live FX synth.
    """

    return LOOP_FX_FIRE, [_validate_index(index)]


def build_set_loop_edit(
    index: Any, param: Any, value: Any
) -> tuple[str, list[Any]]:
    """One ``/ji/loop/edit_set [index, param, value]`` change."""

    spec = _validate_loop_edit_param(param)
    return LOOP_EDIT_SET, [
        _validate_index(index),
        spec.name,
        _loop_edit_value(spec, value),
    ]


def build_loop_mem_save(slot: Any, index: Any) -> tuple[str, list[Any]]:
    return LOOP_MEM_SAVE, [_validate_loop_slot(slot), _validate_index(index)]


def build_loop_mem_recall(slot: Any, index: Any) -> tuple[str, list[Any]]:
    return LOOP_MEM_RECALL, [_validate_loop_slot(slot), _validate_index(index)]


def build_loop_mem_clear(slot: Any) -> tuple[str, list[Any]]:
    return LOOP_MEM_CLEAR, [_validate_loop_slot(slot)]


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


@dataclass(frozen=True)
class RhythmStatus:
    """Bridge rhythm/transport snapshot from ``/ji/rhythm/state``."""

    bpm: float
    meter_num: int
    meter_den: int
    running: bool
    drums_enabled: bool
    drum_rate: float

    @property
    def meter(self) -> str:
        return f"{self.meter_num}/{self.meter_den}"


@dataclass(frozen=True)
class DrumStatus:
    """Bridge drum snapshot from ``/ji/drums/state``."""

    enabled: bool
    port_ok: bool
    rate: float
    lane_mask: int

    @property
    def lane_states(self) -> dict[int, bool]:
        return drum_lane_states(self.lane_mask)


@dataclass(frozen=True)
class GrooveStatus:
    """Bridge groove snapshot from ``/ji/drums/groove``."""

    preset: str
    density: float
    syncopation: float
    swing: float
    probability: float
    seed: int
    grid_steps: int

    @property
    def modifiers(self) -> dict[str, float]:
        return {
            "density": self.density,
            "syncopation": self.syncopation,
            "swing": self.swing,
            "probability": self.probability,
        }


@dataclass(frozen=True)
class QuantizeStatus:
    """``/ji/quantize/state`` snapshot: pending count and next-beat seconds."""

    pending: int
    seconds: float

    @property
    def immediate(self) -> bool:
        """True when the transport is stopped / the queue is offline."""

        return self.seconds < 0.0


@dataclass(frozen=True)
class BassStatus:
    """``/ji/bass/state`` snapshot (20 fields, Stage 8.3a order)."""

    enabled: bool
    port_ok: bool
    preset: str
    grid: str
    length_steps: int
    density: float
    syncopation: float
    swing: float
    gate_beats: float
    accent: float
    pitch_mode: str
    movement: float
    octave_range: int
    octave_jump: float
    seed: int
    base_octave: int
    sub_enabled: bool
    sub_octaves: int
    main_channel: int
    sub_channel: int

    @property
    def available(self) -> bool:
        return self.port_ok

    @property
    def steps_per_beat(self) -> int:
        return BASS_GRID_STEPS.get(self.grid, 1)


@dataclass(frozen=True)
class BreathStatus:
    """``/ji/breath/state`` snapshot (Stage 8.7 order)."""

    enabled: bool = False
    depth: float = BREATH_DEFAULT_DEPTH
    period_bars: int = BREATH_DEFAULT_PERIOD_BARS
    shape: str = BREATH_DEFAULT_SHAPE
    intensity: float = BREATH_DEFAULT_INTENSITY
    phase: float = BREATH_DEFAULT_PHASE
    arp_local: float = BREATH_DEFAULT_ARP_LOCAL
    arp_sync: bool = BREATH_DEFAULT_ARP_SYNC

    def status_line(self) -> str:
        if not self.enabled:
            return "Breath: off"
        line = (
            f"Breath: on · depth {self.depth * 100:.0f} % · "
            f"{self.period_bars} bars · {self.shape} · "
            f"int {self.intensity * 100:.0f} % · "
            f"arp {self.arp_local * 100:.0f} %"
        )
        if self.arp_sync:
            line += " · arp sync"
        return line


@dataclass(frozen=True)
class ArpStatus:
    """``/ji/arp/state`` snapshot (11 fields, Stage 8.3b order)."""

    enabled: bool
    port_ok: bool
    step_seconds: float
    step_effective: float
    gate_seconds: float
    velocity: int
    oct_shared: int
    oct_non_shared_1: int
    oct_non_shared_2: int
    shared_mode: str
    order: str
    seed: int
    sync_mode: str = ARP_DEFAULT_SYNC
    division: str = ARP_DEFAULT_DIVISION
    evolve: bool = ARP_DEFAULT_EVOLVE
    evolve_beats: int = ARP_DEFAULT_EVOLVE_BEATS
    evolve_depth: float = ARP_EVOLVE_DEPTH_DEFAULT

    @property
    def available(self) -> bool:
        """True when the arp MIDI port is open."""

        return self.port_ok

    @property
    def synced(self) -> bool:
        return self.sync_mode == "beat"

    @property
    def octave_offsets(self) -> tuple[int, int, int]:
        return (self.oct_shared, self.oct_non_shared_1, self.oct_non_shared_2)

    def status_line(self) -> str:
        """``on | 0.25 s / 0.20 s | forward | anchor | seed 1``.

        In beat mode the timing part reads ``beat 1/8 = 0.25 s / 0.20 s``; an
        active evolution appends ``| evolve 4 b depth 0.30``.
        """

        state = "on" if self.enabled else "off"
        if self.synced:
            timing = (
                f"beat {self.division} = {self.step_effective:.2f} s"
                f" / {self.gate_seconds:.2f} s"
            )
        else:
            timing = f"{self.step_seconds:.2f} s / {self.gate_seconds:.2f} s"
        line = (
            f"{state} | {timing}"
            f" | {self.order} | {self.shared_mode} | seed {self.seed}"
        )
        if self.evolve:
            line += f" | evolve {self.evolve_beats} b depth {self.evolve_depth:.2f}"
        return line


@dataclass(frozen=True)
class LeadStatus:
    """``/ji/lead/state`` snapshot (16 fields, Stage 8.3d order)."""

    enabled: bool
    port_ok: bool
    grid: str
    phrase_bars: int
    density: float
    gate_beats: float
    velocity: int
    octave_range: int
    base_octave: int
    variation: str
    variation_index: int
    variation_bars: int
    swing: float
    groove_link: bool
    seed: int
    playing: bool
    gate_mode: str = LEAD_DEFAULT_GATE_MODE

    @property
    def available(self) -> bool:
        """True when the lead MIDI port (Bus 5) is open."""

        return self.port_ok

    @property
    def note_gate(self) -> bool:
        return self.gate_mode == "note"

    def status_line(self) -> str:
        """``on | 1/8 · 2 bars · 0.75 | identity #0 | seed 1``."""

        state = "on" if self.enabled else "off"
        line = (
            f"{state} | {self.grid} · {self.phrase_bars} bars · "
            f"{self.density:.2f} | {self.variation} #{self.variation_index} "
            f"| seed {self.seed}"
        )
        if self.variation_bars > 0:
            line += f" | vary every {self.variation_bars} b"
        if self.playing:
            line += " | playing"
        return line


@dataclass(frozen=True)
class DroneStatus:
    """``/ji/drone/state`` snapshot (14 fields, Stage 8.3d order)."""

    port_ok: bool
    mode: str
    velocity: int
    pulse_notes: int
    pulse_grid: str
    pulse_gate: float
    pulse_octave_span: int
    slide_direction: str
    slide_semitones: int
    slide_seconds: float
    hold_beats: float
    cycle_beats: float
    seed: int
    playing: bool
    seq_notes: int = DRONE_DEFAULT_SEQ_NOTES
    seq_bars: str = DRONE_DEFAULT_SEQ_BARS
    seq_gate: float = DRONE_DEFAULT_SEQ_GATE

    @property
    def available(self) -> bool:
        return self.port_ok

    @property
    def gated(self) -> bool:
        """True for the modes that wait for the transport (not ``hold``)."""

        return self.mode in ("pulse", "bend", "sequence")

    def status_line(self, anchor_label: str | None = None) -> str:
        """One compact line per mode (see ``docs/README_stage8_3d.md``)."""

        if self.mode == "pulse":
            return (
                f"pulse {self.pulse_notes} · {self.pulse_grid} | "
                f"{self.velocity}"
            )
        if self.mode == "bend":
            return (
                f"bend {self.slide_direction} {self.slide_semitones} st / "
                f"{self.slide_seconds:.2f} s every {self.cycle_beats:g} beats"
            )
        if self.mode == "sequence":
            bars = float(DRONE_SEQ_BARS_VALUE.get(self.seq_bars, 1.0))
            unit = "bar" if bars <= 1.0 else "bars"
            return (
                f"sequence {self.seq_notes} notes · {self.seq_bars} {unit} | "
                f"{self.velocity}"
            )
        anchor = f" | anchor {anchor_label}" if anchor_label else ""
        return f"hold | {self.velocity}{anchor}"


@dataclass(frozen=True)
class HomeStatus:
    """``/ji/home/state`` snapshot (13 fields, Stage 8.4 order)."""

    has_home: bool
    active_slot: int
    home_classes: int
    distance: int
    pending_batches: int
    history_depth: int
    auto: bool
    every: int
    seed: int
    drift: int
    returning: bool
    journey_mutations: int
    slot_mask: int

    @property
    def active_name(self) -> str:
        if 0 <= self.active_slot < len(HOME_SLOT_NAMES):
            return HOME_SLOT_NAMES[self.active_slot]
        return "-"

    def slot_captured(self, slot: Any) -> bool:
        index = clamp_int(slot, 0, HOME_SLOTS - 1)
        return bool(self.slot_mask & (1 << index))

    def status_line(self, return_total: int | None = None) -> str:
        """``Home: A · 7 cl | dist 2 | auto 8 | drift 3 | back 12``.

        While returning it reads ``Home: returning to A · 2/3 beats``; with no
        captured slot it reads ``Home: none (Set home to capture)``.  The
        ``/ji/home/state`` payload carries the *pending* count only, so the GUI
        passes the total it tracked when the return started (``return_total``);
        without it the line falls back to ``pending + 1``, the value right
        after the first batch landed.
        """

        if not self.has_home:
            return "Home: none (Set home to capture)"
        if self.active_slot < 0:
            return "Home: no active home (Go or Set home)"
        if self.returning:
            total = max(
                1,
                int(return_total)
                if return_total is not None
                else self.pending_batches + 1,
            )
            done = max(0, min(total, total - self.pending_batches))
            return (
                f"Home: returning to {self.active_name} · "
                f"{done}/{total} beats"
            )
        return (
            f"Home: {self.active_name} · {self.home_classes} cl | "
            f"dist {self.distance} | auto {self.every} | "
            f"drift {self.drift} | back {self.history_depth}"
        )


@dataclass(frozen=True)
class StateStatus:
    """``/ji/state/state`` snapshot: count, last saved/loaded, message."""

    count: int
    last_saved: str
    last_loaded: str
    message: str

    @property
    def is_error(self) -> bool:
        message = self.message.strip().lower()
        return message.startswith(
            ("invalid", "no such", "save failed", "delete failed", "cannot")
        )

    def status_line(self) -> str:
        """``State: 3 saved | saved A-drone | loaded A-drone`` (or an error)."""

        message = self.message.strip()
        if self.is_error:
            return f"State: error {message}"
        if self.count <= 0:
            return "State: none saved"
        parts = [f"State: {int(self.count)} saved"]
        if self.last_saved:
            parts.append(f"saved {self.last_saved}")
        if self.last_loaded:
            parts.append(f"loaded {self.last_loaded}")
        return " | ".join(parts)


@dataclass(frozen=True)
class LeadInputStatus:
    """``/ji/lead/input/state`` snapshot (14 fields, Stage 9.1 order)."""

    live: bool
    port_ok: bool
    port_name: str
    quantize: float
    count_in: str
    bars: int
    recording: bool
    count_beats: float
    note_count: int
    source: str
    melody_name: str
    last_in_note: int
    last_out_note: int
    last_cents: float

    @property
    def available(self) -> bool:
        return self.port_ok

    @property
    def counting(self) -> bool:
        return self.recording and self.count_beats > 0.0

    def input_line(self) -> str:
        """``Launchkey 49 - idle - 13 notes`` (port - phase - notes)."""

        if not self.port_ok:
            return "no input port"
        if self.counting:
            phase = f"count {int(round(self.count_beats))}"
        elif self.recording:
            phase = "rec"
        elif self.live:
            phase = "live"
        else:
            phase = "idle"
        return f"{self.port_name} - {phase} - {self.note_count} notes"

    def melody_line(self) -> str:
        """``intro line (13 notes) - played`` or ``none (generated line)``."""

        if not self.melody_name:
            return "none (generated line)"
        return f"{self.melody_name} ({self.note_count} notes) - {self.source}"


@dataclass(frozen=True)
class MelodyStatus:
    """``/ji/lead/melody/state`` snapshot."""

    count: int
    last_saved: str
    last_loaded: str
    message: str


@dataclass(frozen=True)
class LeadNoteStatus:
    """``/ji/lead/note`` snapshot: the last triggered lead note."""

    hz: float
    midi_note: int
    velocity: int

    @property
    def note_name(self) -> str:
        names = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
        note = max(0, min(127, int(self.midi_note)))
        return f"{names[note % 12]}{note // 12 - 1}"


@dataclass(frozen=True)
class BassNoteStatus:
    """``/ji/bass/note`` snapshot: the last triggered bass note."""

    hz: float
    midi_note: int
    velocity: int

    @property
    def note_name(self) -> str:
        names = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
        note = max(0, min(127, int(self.midi_note)))
        return f"{names[note % 12]}{note // 12 - 1}"


@dataclass(frozen=True)
class LoopConfig:
    """Per-loop mode/length reported by sclang's ``/ji/loop/config``."""

    mode: str = LOOP_MODE_DEFAULT
    beats: float = LOOP_BEATS_DEFAULT
    seconds: float = LOOP_LENGTH_DEFAULT


@dataclass
class LoopEdit:
    """One loop's edit state (the 39-value ``/ji/loop/edit``).

    ``mode``/``sync``/``auto_play``/``reverse``/``grain_pitch_snap``/
    ``grain_freeze``/``grain_melt``/``harm_auto`` are 0/1 as on the wire.
    ``grain_size_ms`` is milliseconds on the wire and in the GUI; sclang stores
    seconds internally.  ``grain_pitch`` is the REQUESTED ratio;
    ``effective_pitch`` is the ratio the voice actually receives (the JI-snapped
    one while ``grain_pitch_snap``).  There is no ``mix``/``tune`` field: the
    loop always stays in the accepted JI autotune chain.
    ``grain_breath``/``grain_breath_rate`` are the loop's own grain-breath LFO.
    ``harm_*`` is the Stage 9.0 post-PitchShift FX bus/delay; ``harm_time`` is
    the free time and ``harm_time_sync`` may lock it to a musical division.
    """

    index: int = LOOP_INDEX_MIN
    reverse: int = 0
    rate: float = LOOP_RATE_DEFAULT
    gain: float = 0.7
    mode: int = 0
    sync: int = 0
    division: str = LOOP_DIVISION_DEFAULT
    grain_rate: float = LOOP_GRAIN_RATE_DEFAULT
    grain_size_ms: float = LOOP_GRAIN_SIZE_DEFAULT_MS
    grain_pos: float = LOOP_GRAIN_POS_DEFAULT
    grain_scatter: float = LOOP_GRAIN_SCATTER_DEFAULT
    grain_pitch: float = LOOP_GRAIN_PITCH_DEFAULT
    grain_size_jitter: float = LOOP_GRAIN_SIZE_JITTER_DEFAULT
    grain_pitch_jitter: float = LOOP_GRAIN_PITCH_JITTER_DEFAULT
    grain_pitch_snap: int = LOOP_GRAIN_PITCH_SNAP_DEFAULT
    grain_smear: float = LOOP_GRAIN_SMEAR_DEFAULT
    grain_freeze: int = LOOP_GRAIN_FREEZE_DEFAULT
    grain_motion: str = LOOP_GRAIN_MOTION_DEFAULT
    grain_speed: float = LOOP_GRAIN_SPEED_DEFAULT
    grain_depth: float = LOOP_GRAIN_DEPTH_DEFAULT
    grain_burst: float = LOOP_GRAIN_BURST_DEFAULT
    grain_morph: float = LOOP_GRAIN_MORPH_DEFAULT
    grain_melt: int = LOOP_GRAIN_MELT_DEFAULT
    grain_filter: float = LOOP_GRAIN_FILTER_DEFAULT
    grain_filter_rate: float = LOOP_GRAIN_FILTER_RATE_DEFAULT
    grain_reverse: float = LOOP_GRAIN_REVERSE_DEFAULT
    count_in: str = LOOP_COUNT_IN_DEFAULT
    auto_play: int = 0
    effective_pitch: float = LOOP_GRAIN_PITCH_DEFAULT
    grain_breath: float = LOOP_GRAIN_BREATH_DEFAULT
    grain_breath_rate: float = LOOP_GRAIN_BREATH_RATE_DEFAULT
    harm_mode: str = LOOP_HARM_MODE_DEFAULT
    harm_mix: float = LOOP_HARM_MIX_DEFAULT
    harm_time: float = LOOP_HARM_TIME_DEFAULT
    harm_time_sync: str = LOOP_HARM_TIME_SYNC_DEFAULT
    harm_steps: int = LOOP_HARM_STEPS_DEFAULT
    harm_spacing: float = LOOP_HARM_SPACING_DEFAULT
    harm_falloff: float = LOOP_HARM_FALLOFF_DEFAULT
    harm_auto: int = LOOP_HARM_AUTO_DEFAULT

    @property
    def is_grain(self) -> bool:
        return bool(self.mode)

    @property
    def is_harm(self) -> bool:
        return self.harm_mode != "off"

    @property
    def is_beat(self) -> bool:
        return bool(self.sync)

    @property
    def auto_plays(self) -> bool:
        return bool(self.auto_play)

    @property
    def is_snapped(self) -> bool:
        return bool(self.grain_pitch_snap)

    @property
    def is_frozen(self) -> bool:
        return bool(self.grain_freeze)

    @property
    def is_melting(self) -> bool:
        return bool(self.grain_melt)

    @property
    def is_morphed(self) -> bool:
        return bool(self.mode) and self.grain_morph > 0.0

    @property
    def rate_label(self) -> str:
        return loop_rate_label(self.rate)

    @property
    def pitch_label(self) -> str:
        """The ratio the voice gets, as a JI fraction (``×3/2`` form)."""

        return ji_ratio_label(self.effective_pitch)

    def harm_time_seconds(
        self, bpm: Any = 120.0, bar_beats: Any = 4.0
    ) -> float:
        """Effective FX time in seconds (free seconds or a musical division)."""

        return harm_time_seconds(
            self.harm_time_sync, self.harm_time, bpm, bar_beats
        )

    def count_in_beats(self, bar_beats: Any = 4) -> int:
        """Quarter beats of count-in for the current meter's bar length.

        Stage 9.0: round the bar **up** to the next whole quarter beat so odd
        1/8 meters never start early: 4/4 -> 4, 3/4 -> 3, 6/8 -> 3,
        5/8 (2.5) -> 3, 7/8 (3.5) -> 4.  Mirrors
        ``~ji_loop_count_in_quarter_beats`` exactly.
        """

        beats_per_bar = max(1, int(math.ceil(float(bar_beats))))
        if self.count_in == "1 beat":
            return 1
        if self.count_in == "1 bar":
            return beats_per_bar
        if self.count_in == "2 bars":
            return beats_per_bar * 2
        return 0

    def status_line(self, bpm: Any = 120.0, bar_beats: Any = 4.0) -> str:
        parts = [f"Loop {self.index}", f"×{self.rate_label}"]
        if self.reverse:
            parts.append("rev")
        if self.is_grain:
            parts.append("grain/beat" if self.is_beat else "grain/free")
            parts.append(f"pos {self.grain_pos * 100:.0f} %")
            parts.append(f"motion {self.grain_motion}")
            parts.append(f"pitch ×{self.pitch_label}")
            if self.is_snapped:
                parts.append("snap")
            if self.grain_morph > 0.0:
                parts.append(f"morph {self.grain_morph * 100:.0f} %")
            if self.is_melting:
                parts.append("melt")
            if self.is_frozen:
                parts.append("freeze")
            if self.grain_breath > 0.0:
                parts.append(f"breath {self.grain_breath * 100:.0f} %")
        if self.is_harm:
            parts.append(f"harm {self.harm_mode}")
            parts.append(f"{self.harm_steps}×")
            if self.harm_time_sync != "free":
                parts.append(self.harm_time_sync)
            parts.append(f"{self.harm_time_seconds(bpm, bar_beats):.2f} s")
            parts.append(f"mix {self.harm_mix * 100:.0f} %")
        parts.append(f"count-in {self.count_in}")
        if self.auto_play:
            parts.append("auto")
        parts.append(f"gain {self.gain:.2f}")
        return " · ".join(parts)


@dataclass
class LoopMemory:
    """One session-only memory slot (``/ji/loop/mem``).

    The slot holds a server-side copy of a loop buffer plus its edit state.
    ``filled`` is 0/1 as on the wire.  Nothing is ever written to disk.
    """

    slot: int = 0
    filled: int = 0
    frames: int = 0
    seconds: float = 0.0

    @property
    def name(self) -> str:
        if 0 <= self.slot < LOOP_MEM_SLOTS:
            return LOOP_MEM_SLOT_NAMES[self.slot]
        return "?"

    @property
    def is_filled(self) -> bool:
        return bool(self.filled)

    def status_line(self) -> str:
        if not self.filled:
            return f"{self.name}: empty"
        return f"{self.name}: {self.seconds:.1f} s"


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


def parse_rhythm_state(args: Sequence[Any]) -> RhythmStatus:
    """Parse ``[bpm, num, den, running, drums_enabled, drum_rate]``."""

    if len(args) != 6:
        raise ValueError(
            "rhythm state needs bpm, numerator, denominator, running, "
            "drums_enabled, drum_rate"
        )
    return RhythmStatus(
        bpm=_validate_bpm(args[0]),
        meter_num=_validate_meter_num(args[1]),
        meter_den=_validate_meter_den(args[2]),
        running=_to_bool(args[3], "running"),
        drums_enabled=_to_bool(args[4], "drums_enabled"),
        drum_rate=_validate_drum_rate(args[5]),
    )


def parse_drums_state(args: Sequence[Any]) -> DrumStatus:
    """Parse ``[enabled, port_ok, rate, lane_mask]``."""

    if len(args) != 4:
        raise ValueError("drums state needs enabled, port_ok, rate, lane_mask")
    return DrumStatus(
        enabled=_to_bool(args[0], "drums enabled"),
        port_ok=_to_bool(args[1], "drums port_ok"),
        rate=_validate_drum_rate(args[2]),
        lane_mask=clamp_int(args[3], 0, LANE_MASK_ALL),
    )


def parse_groove(args: Sequence[Any]) -> GrooveStatus:
    """Parse ``[preset, density, syncopation, swing, probability, seed, steps]``."""

    if len(args) != 7:
        raise ValueError(
            "groove state needs preset, density, syncopation, swing, "
            "probability, seed, grid_steps"
        )
    return GrooveStatus(
        preset=_validate_groove_preset(args[0]),
        density=_validate_unit(args[1], "density"),
        syncopation=_validate_unit(args[2], "syncopation"),
        swing=_validate_unit(args[3], "swing"),
        probability=_validate_unit(args[4], "probability"),
        seed=_validate_seed(args[5]),
        grid_steps=max(1, min(MAX_GRID_STEPS, clamp_int(args[6], 1, MAX_GRID_STEPS))),
    )


def parse_grid(args: Sequence[Any]) -> tuple[int, ...]:
    """Parse the 32-int ``/ji/drums/grid`` payload into 16 lane masks."""

    if len(args) != 2 * len(DRUM_LANE_DEFS):
        raise ValueError(
            f"grid state needs {2 * len(DRUM_LANE_DEFS)} integers "
            "(lo, hi per lane)"
        )
    masks: list[int] = []
    for lane in range(len(DRUM_LANE_DEFS)):
        low = clamp_int(args[2 * lane], 0, 0xFFFF)
        high = clamp_int(args[2 * lane + 1], 0, 0xFFFF)
        masks.append(low | (high << 16))
    return tuple(masks)


def parse_quantize(args: Sequence[Any]) -> QuantizeStatus:
    """Parse ``[pending_count, seconds_to_next_beat]`` (seconds < 0 = off)."""

    if len(args) != 2:
        raise ValueError("quantize state needs pending_count and seconds")
    pending = clamp_int(args[0], 0, 1_000_000)
    seconds = _as_finite_float(args[1], "seconds_to_next_beat")
    if seconds < 0.0:
        return QuantizeStatus(pending=pending, seconds=-1.0)
    return QuantizeStatus(pending=pending, seconds=max(0.0, seconds))


def parse_bass_state(args: Sequence[Any]) -> BassStatus:
    """Parse the 20-argument ``/ji/bass/state`` payload."""

    if len(args) != 20:
        raise ValueError(
            "bass state needs 20 values: enabled, port_ok, preset, grid, "
            "length_steps, density, syncopation, swing, gate_beats, accent, "
            "pitch_mode, movement, octave_range, octave_jump, seed, "
            "base_octave, sub_enabled, sub_octaves, main_channel, sub_channel"
        )
    return BassStatus(
        enabled=_to_bool(args[0], "bass enabled"),
        port_ok=_to_bool(args[1], "bass port_ok"),
        preset=(
            BASS_PRESET_CUSTOM
            if str(args[2]).strip().lower() == BASS_PRESET_CUSTOM
            else _validate_bass_preset(args[2])
        ),
        grid=_validate_bass_grid(args[3]),
        length_steps=_validate_bass_length(args[4]),
        density=_validate_unit(args[5], "bass density"),
        syncopation=_validate_unit(args[6], "bass syncopation"),
        swing=_validate_unit(args[7], "bass swing"),
        gate_beats=_validate_bass_gate(args[8]),
        accent=_validate_unit(args[9], "bass accent"),
        pitch_mode=_validate_bass_pitch_mode(args[10]),
        movement=_validate_unit(args[11], "bass movement"),
        octave_range=_validate_bass_octave_range(args[12]),
        octave_jump=_validate_unit(args[13], "bass octave_jump"),
        seed=_validate_bass_seed(args[14]),
        base_octave=_validate_bass_base_octave(args[15]),
        sub_enabled=_to_bool(args[16], "bass sub_enabled"),
        sub_octaves=_validate_bass_sub_octaves(args[17]),
        main_channel=clamp_int(args[18], 1, 16),
        sub_channel=clamp_int(args[19], 1, 16),
    )


def parse_breath_state(args: Sequence[Any]) -> "BreathStatus":
    """Parse ``/ji/breath/state`` (7 values, or 8 with ``arp_sync``).

    A 7-value payload from an older bridge leaves ``arp_sync`` at its default
    (off), so a rolling UI update cannot break the status row.
    """

    if len(args) not in (7, 8):
        raise ValueError(
            "breath state needs 7 or 8 values: enabled, depth, period_bars, "
            "shape, intensity, phase, arp_local[, arp_sync]"
        )
    return BreathStatus(
        enabled=_to_bool(args[0], "breath enabled"),
        depth=clamp(_as_finite_float(args[1], "breath depth"), 0.0, 1.0),
        period_bars=_validate_breath_period(args[2]),
        shape=_validate_breath_shape(args[3]),
        intensity=clamp(
            _as_finite_float(args[4], "breath intensity"), 0.0, 1.0
        ),
        phase=clamp(_as_finite_float(args[5], "breath phase"), 0.0, 1.0),
        arp_local=clamp(
            _as_finite_float(args[6], "breath arp_local"), 0.0, 1.0
        ),
        arp_sync=(
            _to_bool(args[7], "breath arp_sync") if len(args) == 8 else False
        ),
    )


def parse_bass_hits(args: Sequence[Any]) -> tuple[bool, tuple[bool, ...]]:
    """Parse ``/ji/bass/hits``: ``[enabled, count, hit_0 ... hit_{count-1}]``.

    Strict: the payload length must be exactly ``2 + count`` and ``count`` must
    be 0..:data:`BASS_HITS_MAX`.  A mismatch is rejected.
    """

    if len(args) < 2:
        raise ValueError("bass hits needs enabled, count and the hit cells")
    enabled = _to_bool(args[0], "bass hits enabled")
    count = clamp_int(
        _as_finite_float(args[1], "bass hits count"), 0, BASS_HITS_MAX
    )
    if len(args) != 2 + count:
        raise ValueError(
            f"bass hits count {count} does not match "
            f"{len(args) - 2} hit cells"
        )
    hits = tuple(
        _to_bool(value, f"hit {index}")
        for index, value in enumerate(args[2:])
    )
    return enabled, hits


def parse_bass_note(args: Sequence[Any]) -> BassNoteStatus:
    """Parse ``[hz, midi_note, velocity]`` from ``/ji/bass/note``."""

    if len(args) != 3:
        raise ValueError("bass note needs hz, midi_note, velocity")
    hz = _as_finite_float(args[0], "bass note hz")
    if hz <= 0.0:
        raise ValueError("bass note hz must be strictly positive")
    return BassNoteStatus(
        hz=hz,
        midi_note=clamp_int(args[1], 0, 127),
        velocity=clamp_int(args[2], 0, 127),
    )


def parse_arp_state(args: Sequence[Any]) -> ArpStatus:
    """Parse the 17-argument ``/ji/arp/state`` payload."""

    if len(args) != 17:
        raise ValueError(
            "arp state needs 17 values: enabled, port_ok, step_seconds, "
            "step_effective, gate_seconds, velocity, oct_shared, "
            "oct_non_shared_1, oct_non_shared_2, shared_mode, order, seed, "
            "sync_mode, division, evolve, evolve_beats, evolve_depth"
        )
    return ArpStatus(
        enabled=_to_bool(args[0], "arp enabled"),
        port_ok=_to_bool(args[1], "arp port_ok"),
        step_seconds=_validate_arp_step(args[2]),
        step_effective=_validate_arp_step(args[3]),
        gate_seconds=_validate_arp_gate(args[4], args[3]),
        velocity=_validate_arp_velocity(args[5]),
        oct_shared=clamp_int(args[6], ARP_OCTAVE_MIN, ARP_OCTAVE_MAX),
        oct_non_shared_1=clamp_int(args[7], ARP_OCTAVE_MIN, ARP_OCTAVE_MAX),
        oct_non_shared_2=clamp_int(args[8], ARP_OCTAVE_MIN, ARP_OCTAVE_MAX),
        shared_mode=_validate_arp_shared_mode(args[9]),
        order=_validate_arp_order(args[10]),
        seed=_validate_arp_seed(args[11]),
        sync_mode=_validate_arp_sync(args[12]),
        division=_validate_arp_division(args[13]),
        evolve=_to_bool(args[14], "arp evolve"),
        evolve_beats=_validate_arp_evolve_beats(args[15]),
        evolve_depth=_validate_arp_evolve_depth(args[16]),
    )


def parse_lead_state(args: Sequence[Any]) -> LeadStatus:
    """Parse the 16- or 17-value ``/ji/lead/state`` payload (Stage 9.1)."""

    if len(args) not in (16, 17):
        raise ValueError(
            "lead state needs 16 or 17 values: enabled, port_ok, grid, "
            "phrase_bars, density, gate_beats, velocity, octave_range, "
            "base_octave, variation, variation_index, variation_bars, swing, "
            "groove_link, seed, playing[, gate_mode]"
        )
    return LeadStatus(
        enabled=_to_bool(args[0], "lead enabled"),
        port_ok=_to_bool(args[1], "lead port_ok"),
        grid=_validate_lead_grid(args[2]),
        phrase_bars=_validate_lead_phrase_bars(args[3]),
        density=_validate_lead_density(args[4]),
        gate_beats=_validate_lead_gate(args[5]),
        velocity=_validate_lead_velocity(args[6]),
        octave_range=_validate_lead_octave_range(args[7]),
        base_octave=_validate_lead_base_octave(args[8]),
        variation=_validate_lead_variation(args[9]),
        variation_index=clamp_int(args[10], 0, 1_000_000),
        variation_bars=_validate_lead_variation_bars(args[11]),
        swing=_validate_unit(args[12], "lead swing"),
        groove_link=_to_bool(args[13], "lead groove_link"),
        seed=_validate_lead_seed(args[14]),
        playing=_to_bool(args[15], "lead playing"),
        gate_mode=(
            _validate_lead_gate_mode(args[16])
            if len(args) == 17
            else LEAD_DEFAULT_GATE_MODE
        ),
    )


def parse_lead_input_state(args: Sequence[Any]) -> LeadInputStatus:
    """Parse the 14-value ``/ji/lead/input/state`` payload."""

    if len(args) != 14:
        raise ValueError(
            "lead input state needs 14 values: live, port_ok, port_name, "
            "quantize, count_in, bars, recording, count_beats, note_count, "
            "source, melody_name, last_in_note, last_out_note, last_cents"
        )
    count_beats = _as_finite_float(args[7], "lead input count_beats")
    if count_beats < 0.0:
        raise ValueError("lead input count_beats must be >= 0")
    note_count = int(round(_as_finite_float(args[8], "lead input note_count")))
    if note_count < 0:
        raise ValueError("lead input note_count must be >= 0")
    return LeadInputStatus(
        live=_to_bool(args[0], "lead input live"),
        port_ok=_to_bool(args[1], "lead input port_ok"),
        port_name=str(args[2] or ""),
        quantize=_validate_unit(args[3], "lead input quantize"),
        count_in=_validate_lead_input_count_in(args[4]),
        bars=_validate_lead_input_bars(args[5]),
        recording=_to_bool(args[6], "lead input recording"),
        count_beats=count_beats,
        note_count=note_count,
        source=_validate_lead_input_source(args[9]),
        melody_name=str(args[10] or ""),
        last_in_note=clamp_int(args[11], -1, 127),
        last_out_note=clamp_int(args[12], -1, 127),
        last_cents=_as_finite_float(args[13], "lead input last_cents"),
    )


def parse_melody_list(args: Sequence[Any]) -> tuple[str, ...]:
    """Parse ``[count, name_1 .. name_N]`` from ``/ji/lead/melody/list``."""

    if not args:
        raise ValueError("melody list needs a count")
    count = int(round(_as_finite_float(args[0], "melody count")))
    names = tuple(str(name) for name in args[1:])
    if count != len(names):
        raise ValueError(
            f"melody list count {count} does not match {len(names)} names"
        )
    return names


def parse_melody_state(args: Sequence[Any]) -> MelodyStatus:
    """Parse the 4-value ``/ji/lead/melody/state`` payload."""

    if len(args) != 4:
        raise ValueError(
            "melody state needs count, last_saved, last_loaded, message"
        )
    return MelodyStatus(
        count=int(round(_as_finite_float(args[0], "melody count"))),
        last_saved=str(args[1] or ""),
        last_loaded=str(args[2] or ""),
        message=str(args[3] or ""),
    )


def parse_lead_note(args: Sequence[Any]) -> LeadNoteStatus:
    """Parse ``[hz, midi_note, velocity]`` from ``/ji/lead/note``."""

    if len(args) != 3:
        raise ValueError("lead note needs hz, midi_note, velocity")
    hz = _as_finite_float(args[0], "lead note hz")
    if hz <= 0.0:
        raise ValueError("lead note hz must be strictly positive")
    return LeadNoteStatus(
        hz=hz,
        midi_note=clamp_int(args[1], 0, 127),
        velocity=clamp_int(args[2], 0, 127),
    )


def parse_drone_state(args: Sequence[Any]) -> DroneStatus:
    """Parse ``/ji/drone/state`` (17 values; a 14-value 8.3d payload works).

    Accepting the shorter payload keeps a GUI and a bridge from different
    builds talking while the rig is being updated; the missing ``seq_*``
    values fall back to their shipped defaults.
    """

    if len(args) not in (14, 17):
        raise ValueError(
            "drone state needs 14 or 17 values: port_ok, mode, velocity, "
            "pulse_notes, pulse_grid, pulse_gate, pulse_octave_span, "
            "slide_direction, slide_semitones, slide_seconds, hold_beats, "
            "cycle_beats, seed, playing[, seq_notes, seq_bars, seq_gate]"
        )
    return DroneStatus(
        port_ok=_to_bool(args[0], "drone port_ok"),
        mode=_validate_drone_mode(args[1]),
        velocity=_validate_drone_velocity(args[2]),
        pulse_notes=_validate_drone_pulse_notes(args[3]),
        pulse_grid=_validate_drone_pulse_grid(args[4]),
        pulse_gate=_validate_drone_pulse_gate(args[5]),
        pulse_octave_span=_validate_drone_pulse_span(args[6]),
        slide_direction=_validate_drone_slide_direction(args[7]),
        slide_semitones=_validate_drone_slide_semitones(args[8]),
        slide_seconds=_validate_drone_slide_seconds(args[9]),
        hold_beats=_validate_drone_hold_beats(args[10]),
        cycle_beats=max(1.0, _as_finite_float(args[11], "drone cycle_beats")),
        seed=_validate_drone_seed(args[12]),
        playing=_to_bool(args[13], "drone playing"),
        seq_notes=(
            _validate_drone_seq_notes(args[14])
            if len(args) == 17
            else DRONE_DEFAULT_SEQ_NOTES
        ),
        seq_bars=(
            _validate_drone_seq_bars(args[15])
            if len(args) == 17
            else DRONE_DEFAULT_SEQ_BARS
        ),
        seq_gate=(
            _validate_drone_seq_gate(args[16])
            if len(args) == 17
            else DRONE_DEFAULT_SEQ_GATE
        ),
    )


def parse_home_state(args: Sequence[Any]) -> "HomeStatus":
    """Parse the 13-value ``/ji/home/state`` payload."""

    if len(args) != 13:
        raise ValueError(
            "home state needs has_home, active_slot, home_classes, distance, "
            "pending_batches, history_depth, auto, every, seed, drift, "
            "returning, journey_mutations, slot_mask"
        )
    return HomeStatus(
        has_home=_to_bool(args[0], "home has_home"),
        active_slot=clamp_int(args[1], -1, HOME_SLOTS - 1),
        home_classes=max(0, int(round(_as_finite_float(args[2], "home_classes")))),
        distance=max(0, int(round(_as_finite_float(args[3], "home distance")))),
        pending_batches=max(
            0, int(round(_as_finite_float(args[4], "home pending_batches")))
        ),
        history_depth=max(
            0, int(round(_as_finite_float(args[5], "home history_depth")))
        ),
        auto=_to_bool(args[6], "home auto"),
        every=clamp_int(args[7], HOME_EVERY_MIN, HOME_EVERY_MAX),
        seed=clamp_int(args[8], HOME_SEED_MIN, HOME_SEED_MAX),
        drift=clamp_int(args[9], HOME_DRIFT_MIN, HOME_DRIFT_MAX),
        returning=_to_bool(args[10], "home returning"),
        journey_mutations=max(
            0, int(round(_as_finite_float(args[11], "home journey_mutations")))
        ),
        slot_mask=clamp_int(args[12], 0, (1 << HOME_SLOTS) - 1),
    )


def parse_state_list(args: Sequence[Any]) -> tuple[str, ...]:
    """Parse ``[int count, name_1 ... name_N]``; the count must match."""

    if len(args) < 1:
        raise ValueError("state list needs a count and zero or more names")
    count = int(round(_as_finite_float(args[0], "state count")))
    if count < 0:
        raise ValueError("state count must be non-negative")
    names = [str(value) for value in args[1:]]
    if len(names) != count:
        raise ValueError(
            f"state list says {count} states but carries {len(names)} names"
        )
    return tuple(names)


def parse_state_status(args: Sequence[Any]) -> StateStatus:
    """Parse ``[count, last_saved, last_loaded, message]``."""

    if len(args) != 4:
        raise ValueError(
            "state status needs count, last_saved, last_loaded, message"
        )
    count = int(round(_as_finite_float(args[0], "state count")))
    if count < 0:
        raise ValueError("state count must be non-negative")
    return StateStatus(
        count=count,
        last_saved="" if args[1] is None else str(args[1]),
        last_loaded="" if args[2] is None else str(args[2]),
        message="" if args[3] is None else str(args[3]),
    )


def parse_loop_config(args: Sequence[Any]) -> tuple[int, LoopConfig]:
    """Parse ``[index, mode, beats, seconds]``."""

    if len(args) != 4:
        raise ValueError("loop config needs index, mode, beats, seconds")
    index = _validate_index(args[0])
    mode = _validate_loop_mode(args[1])
    beats = _validate_loop_beats(args[2])
    seconds = _as_finite_float(args[3], "loop seconds")
    if seconds < 0.0:
        raise ValueError("loop seconds must be non-negative")
    return index, LoopConfig(mode=mode, beats=beats, seconds=seconds)


def _to_bool(value: Any, label: str) -> bool:
    numeric = _as_finite_float(value, label)
    rounded = int(round(numeric))
    if rounded not in (0, 1):
        raise ValueError(f"{label} must be 0 or 1")
    return bool(rounded)


def parse_loop_state(args: Sequence[Any]) -> tuple[int, str]:
    if len(args) != 2:
        raise ValueError("loop state needs index and state string")
    index = _validate_index(args[0])
    state = str(args[1])
    if state not in LOOP_STATES:
        raise ValueError(f"unknown loop state {state!r}")
    return index, state


def parse_loop_edit(args: Sequence[Any]) -> LoopEdit:
    """Parse the strict 39-value ``/ji/loop/edit`` payload.

    Values are clamped like the other parsers; an unknown ``division``,
    ``count_in``, ``grain_motion``, ``harm_mode`` or ``harm_time_sync`` string
    is rejected (each wire vocabulary is closed).  ``grain_pitch`` is the
    requested ratio, ``effective_pitch`` the JI-snapped ratio the voice actually
    received.  Stage 8.7 appended ``grain_breath`` and ``grain_breath_rate``
    after ``grain_filter_rate``; Stage 9.0 appends the eight ``harm_*`` values
    (including the owner-requested fixed musical ``harm_time_sync``).
    """

    if len(args) != 39:
        raise ValueError(
            "loop edit needs 39 values: index, reverse, rate, gain, mode, sync, "
            "division, grain_rate, grain_size_ms, grain_pos, grain_scatter, "
            "grain_pitch, grain_size_jitter, grain_pitch_jitter, "
            "grain_pitch_snap, grain_smear, grain_freeze, grain_motion, "
            "grain_speed, grain_depth, grain_burst, grain_reverse, count_in, "
            "auto_play, effective_pitch, grain_morph, grain_melt, "
            "grain_filter, grain_filter_rate, grain_breath, grain_breath_rate, "
            "harm_mode, harm_mix, harm_time, harm_time_sync, harm_steps, "
            "harm_spacing, harm_falloff, harm_auto"
        )
    index = _validate_index(args[0])
    division = str(args[6])
    if division not in LOOP_DIVISION_LABELS:
        raise ValueError(f"unknown loop division {division!r}")
    motion = str(args[17])
    if motion not in LOOP_GRAIN_MOTIONS:
        raise ValueError(f"unknown grain motion {motion!r}")
    count_in = str(args[22])
    if count_in not in LOOP_COUNT_IN_OPTIONS:
        raise ValueError(f"unknown count-in {count_in!r}")
    harm_mode = str(args[31])
    if harm_mode not in LOOP_HARM_MODES:
        raise ValueError(f"unknown harm mode {harm_mode!r}")
    harm_time_sync = str(args[34])
    if harm_time_sync not in LOOP_HARM_TIME_SYNCS:
        raise ValueError(f"unknown harm time sync {harm_time_sync!r}")
    return LoopEdit(
        index=index,
        reverse=_loop_bit(args[1], "reverse"),
        rate=_validate_loop_rate_value(args[2]),
        gain=clamp(_as_finite_float(args[3], "loop gain"), 0.0, 1.0),
        mode=_loop_bit(args[4], "mode"),
        sync=_loop_bit(args[5], "sync"),
        division=division,
        grain_rate=clamp(
            _as_finite_float(args[7], "grain_rate"), 0.5, LOOP_GRAIN_RATE_MAX
        ),
        grain_size_ms=clamp(
            _as_finite_float(args[8], "grain_size_ms"), 5.0, 500.0
        ),
        grain_pos=clamp(_as_finite_float(args[9], "grain_pos"), 0.0, 1.0),
        grain_scatter=clamp(
            _as_finite_float(args[10], "grain_scatter"), 0.0, 1.0
        ),
        grain_pitch=clamp(
            _as_finite_float(args[11], "grain_pitch"), 0.25, 4.0
        ),
        grain_size_jitter=clamp(
            _as_finite_float(args[12], "grain_size_jitter"), 0.0, 1.0
        ),
        grain_pitch_jitter=clamp(
            _as_finite_float(args[13], "grain_pitch_jitter"), 0.0, 1.0
        ),
        grain_pitch_snap=_loop_bit(args[14], "grain_pitch_snap"),
        grain_smear=clamp(
            _as_finite_float(args[15], "grain_smear"), 0.0, 1.0
        ),
        grain_freeze=_loop_bit(args[16], "grain_freeze"),
        grain_motion=motion,
        grain_speed=clamp(
            _as_finite_float(args[18], "grain_speed"),
            LOOP_GRAIN_SPEED_MIN,
            LOOP_GRAIN_SPEED_MAX,
        ),
        grain_depth=clamp(_as_finite_float(args[19], "grain_depth"), 0.0, 1.0),
        grain_burst=clamp(_as_finite_float(args[20], "grain_burst"), 0.0, 1.0),
        grain_reverse=clamp(
            _as_finite_float(args[21], "grain_reverse"), 0.0, 1.0
        ),
        count_in=count_in,
        auto_play=_loop_bit(args[23], "auto_play"),
        effective_pitch=clamp(
            _as_finite_float(args[24], "effective_pitch"), 0.25, 4.0
        ),
        grain_morph=clamp(_as_finite_float(args[25], "grain_morph"), 0.0, 1.0),
        grain_melt=_loop_bit(args[26], "grain_melt"),
        grain_filter=clamp(
            _as_finite_float(args[27], "grain_filter"), 0.0, 1.0
        ),
        grain_filter_rate=clamp(
            _as_finite_float(args[28], "grain_filter_rate"),
            LOOP_GRAIN_FILTER_RATE_MIN,
            LOOP_GRAIN_FILTER_RATE_MAX,
        ),
        grain_breath=clamp(
            _as_finite_float(args[29], "grain_breath"),
            LOOP_GRAIN_BREATH_MIN,
            LOOP_GRAIN_BREATH_MAX,
        ),
        grain_breath_rate=clamp(
            _as_finite_float(args[30], "grain_breath_rate"),
            LOOP_GRAIN_BREATH_RATE_MIN,
            LOOP_GRAIN_BREATH_RATE_MAX,
        ),
        harm_mode=harm_mode,
        harm_mix=clamp(
            _as_finite_float(args[32], "harm_mix"), 0.0, 1.0
        ),
        harm_time=clamp(
            _as_finite_float(args[33], "harm_time"),
            LOOP_HARM_TIME_MIN,
            LOOP_HARM_TIME_MAX,
        ),
        harm_time_sync=harm_time_sync,
        harm_steps=clamp_int(
            _as_finite_float(args[35], "harm_steps"),
            LOOP_HARM_STEPS_MIN,
            LOOP_HARM_STEPS_MAX,
        ),
        harm_spacing=clamp(
            _as_finite_float(args[36], "harm_spacing"),
            LOOP_HARM_SPACING_MIN,
            LOOP_HARM_SPACING_MAX,
        ),
        harm_falloff=clamp(
            _as_finite_float(args[37], "harm_falloff"),
            LOOP_HARM_FALLOFF_MIN,
            LOOP_HARM_FALLOFF_MAX,
        ),
        harm_auto=_loop_bit(args[38], "harm_auto"),
    )


def parse_loop_mem(args: Sequence[Any]) -> LoopMemory:
    """Parse the strict 4-value ``/ji/loop/mem`` payload."""

    if len(args) != 4:
        raise ValueError("loop memory needs slot, filled, frames, seconds")
    slot = clamp_int(
        _as_finite_float(args[0], "loop memory slot"), 0, LOOP_MEM_SLOTS - 1
    )
    frames = max(0, int(round(_as_finite_float(args[2], "memory frames"))))
    seconds = max(0.0, _as_finite_float(args[3], "memory seconds"))
    return LoopMemory(
        slot=slot,
        filled=_loop_bit(args[1], "memory filled"),
        frames=frames,
        seconds=seconds,
    )


def parse_loop_count(args: Sequence[Any]) -> tuple[int, int]:
    """Parse ``[index, beats_left]`` (beats_left 0..64, 0 = counting done)."""

    if len(args) != 2:
        raise ValueError("loop count needs index and beats_left")
    index = _validate_index(args[0])
    beats_left = clamp_int(_as_finite_float(args[1], "beats_left"), 0, 64)
    return index, beats_left


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
    RHYTHM_STATE: parse_rhythm_state,
    DRUMS_STATE: parse_drums_state,
    DRUMS_GROOVE: parse_groove,
    DRUMS_GRID: parse_grid,
    QUANTIZE_STATE: parse_quantize,
    BASS_STATE: parse_bass_state,
    BASS_NOTE: parse_bass_note,
    BASS_HITS: parse_bass_hits,
    BREATH_STATE: parse_breath_state,
    ARP_STATE: parse_arp_state,
    LEAD_STATE: parse_lead_state,
    LEAD_NOTE: parse_lead_note,
    LEAD_INPUT_STATE: parse_lead_input_state,
    LEAD_MELODY_LIST: parse_melody_list,
    LEAD_MELODY_STATE: parse_melody_state,
    DRONE_STATE: parse_drone_state,
    HOME_STATE: parse_home_state,
    STATE_LIST: parse_state_list,
    STATE_STATE: parse_state_status,
    CPU: parse_cpu,
    LOOP_STATE: parse_loop_state,
    LOOP_CONFIG: parse_loop_config,
    LOOP_EDIT: parse_loop_edit,
    LOOP_MEM: parse_loop_mem,
    LOOP_COUNT: parse_loop_count,
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
        self.rhythm: RhythmStatus | None = None
        self.drums: DrumStatus | None = None
        self.groove: GrooveStatus | None = None
        self.grid: tuple[int, ...] | None = None
        self.quantize: QuantizeStatus | None = None
        self.bass: BassStatus | None = None
        self.bass_note: BassNoteStatus | None = None
        self.bass_hits: tuple[bool, ...] | None = None
        self.bass_hits_enabled = False
        self.breath: BreathStatus | None = None
        self.arp: ArpStatus | None = None
        self.lead: LeadStatus | None = None
        self.lead_note: LeadNoteStatus | None = None
        self.lead_input: LeadInputStatus | None = None
        self.melody_names: tuple[str, ...] = ()
        self.melody: MelodyStatus | None = None
        self.drone: DroneStatus | None = None
        self.home: HomeStatus | None = None
        self.state_names: tuple[str, ...] = ()
        self.state: StateStatus | None = None
        self.cpu: float | None = None
        self.loop_states: dict[int, str] = {
            index: "waiting" for index in range(LOOP_INDEX_MIN, LOOP_INDEX_MAX + 1)
        }
        self.loop_configs: dict[int, LoopConfig] = {
            index: LoopConfig()
            for index in range(LOOP_INDEX_MIN, LOOP_INDEX_MAX + 1)
        }
        self.loop_edits: dict[int, LoopEdit] = {
            index: LoopEdit(index=index)
            for index in range(LOOP_INDEX_MIN, LOOP_INDEX_MAX + 1)
        }
        self.loop_mem: dict[int, LoopMemory] = {
            slot: LoopMemory(slot=slot) for slot in range(LOOP_MEM_SLOTS)
        }
        self.loop_counts: dict[int, int] = {
            index: 0 for index in range(LOOP_INDEX_MIN, LOOP_INDEX_MAX + 1)
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
        elif address == RHYTHM_STATE:
            self.rhythm = parsed
            self.bridge_seen = True
        elif address == DRUMS_STATE:
            self.drums = parsed
            self.bridge_seen = True
        elif address == DRUMS_GROOVE:
            self.groove = parsed
            self.bridge_seen = True
        elif address == DRUMS_GRID:
            self.grid = parsed
            self.bridge_seen = True
        elif address == QUANTIZE_STATE:
            self.quantize = parsed
            self.bridge_seen = True
        elif address == BASS_STATE:
            self.bass = parsed
            self.bridge_seen = True
        elif address == BASS_NOTE:
            self.bass_note = parsed
            self.bridge_seen = True
        elif address == BASS_HITS:
            enabled, hits = parsed
            self.bass_hits_enabled = bool(enabled)
            self.bass_hits = tuple(hits)
            self.bridge_seen = True
        elif address == BREATH_STATE:
            self.breath = parsed
            self.bridge_seen = True
        elif address == ARP_STATE:
            self.arp = parsed
            self.bridge_seen = True
        elif address == LEAD_STATE:
            self.lead = parsed
            self.bridge_seen = True
        elif address == LEAD_NOTE:
            self.lead_note = parsed
            self.bridge_seen = True
        elif address == LEAD_INPUT_STATE:
            self.lead_input = parsed
            self.bridge_seen = True
        elif address == LEAD_MELODY_LIST:
            self.melody_names = tuple(parsed)
            self.bridge_seen = True
        elif address == LEAD_MELODY_STATE:
            self.melody = parsed
            self.bridge_seen = True
        elif address == DRONE_STATE:
            self.drone = parsed
            self.bridge_seen = True
        elif address == HOME_STATE:
            self.home = parsed
            self.bridge_seen = True
        elif address == STATE_LIST:
            self.state_names = tuple(parsed)
            self.bridge_seen = True
        elif address == STATE_STATE:
            self.state = parsed
            self.bridge_seen = True
        elif address == CPU:
            self.cpu = parsed
            self.sclang_seen = True
        elif address == LOOP_STATE:
            index, state = parsed
            self.loop_states[index] = state
            self.sclang_seen = True
        elif address == LOOP_CONFIG:
            index, config = parsed
            self.loop_configs[index] = config
            self.sclang_seen = True
        elif address == LOOP_EDIT:
            self.loop_edits[parsed.index] = parsed
            self.sclang_seen = True
        elif address == LOOP_MEM:
            self.loop_mem[parsed.slot] = parsed
            self.sclang_seen = True
        elif address == LOOP_COUNT:
            index, beats_left = parsed
            self.loop_counts[index] = beats_left
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

    # -- Stage 8.1 rhythm / drums --------------------------------------

    def set_tempo(self, bpm: Any) -> tuple[str, list[Any]]:
        message = build_set_tempo(bpm)
        self._send_bridge(*message)
        return message

    def set_meter(
        self, numerator: Any, denominator: Any
    ) -> tuple[str, list[Any]]:
        message = build_set_meter(numerator, denominator)
        self._send_bridge(*message)
        return message

    def set_transport(self, running: bool) -> tuple[str, list[Any]]:
        message = build_transport(running)
        self._send_bridge(*message)
        return message

    def request_rhythm_resync(self) -> tuple[str, list[Any]]:
        message = build_rhythm_request()
        self._send_bridge(*message)
        return message

    def set_drums_enabled(self, enabled: bool) -> tuple[str, list[Any]]:
        message = build_set_drums_enabled(enabled)
        self._send_bridge(*message)
        return message

    def set_drum_lane(
        self, note: Any, enabled: bool
    ) -> tuple[str, list[Any]]:
        message = build_set_drum_lane(note, enabled)
        self._send_bridge(*message)
        return message

    def set_drum_rate(self, rate: Any) -> tuple[str, list[Any]]:
        message = build_set_drum_rate(rate)
        self._send_bridge(*message)
        return message

    def set_drums_preset(self, preset: Any) -> tuple[str, list[Any]]:
        message = build_set_drums_preset(preset)
        self._send_bridge(*message)
        return message

    def set_groove_modifier(
        self, param: Any, value: Any
    ) -> tuple[str, list[Any]]:
        message = build_set_groove_modifier(param, value)
        self._send_bridge(*message)
        return message

    def set_drum_step(
        self, lane: Any, step: Any, enabled: bool
    ) -> tuple[str, list[Any]]:
        message = build_set_drum_step(lane, step, enabled)
        self._send_bridge(*message)
        return message

    def set_drum_seed(self, seed: Any) -> tuple[str, list[Any]]:
        message = build_set_drum_seed(seed)
        self._send_bridge(*message)
        return message

    def reset_drum_grid(self) -> tuple[str, list[Any]]:
        message = build_reset_drum_grid()
        self._send_bridge(*message)
        return message

    # -- Stage 8.3a bass ------------------------------------------------

    def set_bass_setting(
        self, name: Any, value: Any
    ) -> tuple[str, list[Any]]:
        message = build_set_bass_setting(name, value)
        self._send_bridge(*message)
        return message

    def set_bass_enabled(self, enabled: Any) -> tuple[str, list[Any]]:
        message = build_set_bass_enabled(enabled)
        self._send_bridge(*message)
        return message

    def set_bass_preset(self, preset: Any) -> tuple[str, list[Any]]:
        message = build_set_bass_preset(preset)
        self._send_bridge(*message)
        return message

    def set_bass_grid(self, grid: Any) -> tuple[str, list[Any]]:
        message = build_set_bass_grid(grid)
        self._send_bridge(*message)
        return message

    def set_bass_length(self, steps: Any) -> tuple[str, list[Any]]:
        message = build_set_bass_length(steps)
        self._send_bridge(*message)
        return message

    def set_bass_param(
        self, param: Any, value: Any
    ) -> tuple[str, list[Any]]:
        message = build_set_bass_param(param, value)
        self._send_bridge(*message)
        return message

    def set_bass_pitch_mode(self, mode: Any) -> tuple[str, list[Any]]:
        message = build_set_bass_pitch_mode(mode)
        self._send_bridge(*message)
        return message

    def set_bass_root_downbeat(self, enabled: Any) -> tuple[str, list[Any]]:
        message = build_set_bass_root_downbeat(enabled)
        self._send_bridge(*message)
        return message

    def set_bass_octave_range(self, value: Any) -> tuple[str, list[Any]]:
        message = build_set_bass_octave_range(value)
        self._send_bridge(*message)
        return message

    def set_bass_base_octave(self, value: Any) -> tuple[str, list[Any]]:
        message = build_set_bass_base_octave(value)
        self._send_bridge(*message)
        return message

    def set_bass_seed(self, seed: Any) -> tuple[str, list[Any]]:
        message = build_set_bass_seed(seed)
        self._send_bridge(*message)
        return message

    def set_bass_sub_enabled(self, enabled: Any) -> tuple[str, list[Any]]:
        message = build_set_bass_sub_enabled(enabled)
        self._send_bridge(*message)
        return message

    def set_bass_sub_octaves(self, value: Any) -> tuple[str, list[Any]]:
        message = build_set_bass_sub_octaves(value)
        self._send_bridge(*message)
        return message

    # -- Stage 8.7 breath + bass hit row -------------------------------

    def set_breath_param(
        self, name: Any, value: Any
    ) -> tuple[str, list[Any]]:
        message = build_breath_param(name, value)
        self._send_bridge(*message)
        return message

    def bass_set_hits_enabled(
        self, enabled: Any
    ) -> tuple[str, list[Any]]:
        message = build_bass_hits_enabled(enabled)
        self._send_bridge(*message)
        return message

    def bass_set_hit(self, index: Any, on: Any) -> tuple[str, list[Any]]:
        message = build_bass_hit(index, on)
        self._send_bridge(*message)
        return message

    def bass_clear_hits(self) -> tuple[str, list[Any]]:
        message = build_bass_clear_hits()
        self._send_bridge(*message)
        return message

    def bass_copy_hits(self) -> tuple[str, list[Any]]:
        message = build_bass_copy_hits()
        self._send_bridge(*message)
        return message

    # -- Stage 8.3b arp ------------------------------------------------

    def set_arp_setting(
        self, name: Any, value: Any
    ) -> tuple[str, list[Any]]:
        message = build_set_arp_setting(name, value)
        self._send_bridge(*message)
        return message

    def set_arp_param(self, name: Any, value: Any) -> tuple[str, list[Any]]:
        return self.set_arp_setting(name, value)

    def set_arp_step(self, seconds: Any) -> tuple[str, list[Any]]:
        message = build_set_arp_step(seconds)
        self._send_bridge(*message)
        return message

    def set_arp_gate(
        self, seconds: Any, step: Any = None
    ) -> tuple[str, list[Any]]:
        message = build_set_arp_gate(seconds, step)
        self._send_bridge(*message)
        return message

    def set_arp_velocity(self, velocity: Any) -> tuple[str, list[Any]]:
        message = build_set_arp_velocity(velocity)
        self._send_bridge(*message)
        return message

    def set_arp_octaves(
        self, shared: Any, non_shared_1: Any, non_shared_2: Any
    ) -> tuple[str, list[Any]]:
        message = build_set_arp_octaves(shared, non_shared_1, non_shared_2)
        self._send_bridge(*message)
        return message

    def set_arp_shared_mode(self, mode: Any) -> tuple[str, list[Any]]:
        message = build_set_arp_shared_mode(mode)
        self._send_bridge(*message)
        return message

    def set_arp_order(self, order: Any) -> tuple[str, list[Any]]:
        message = build_set_arp_order(order)
        self._send_bridge(*message)
        return message

    def set_arp_seed(self, seed: Any) -> tuple[str, list[Any]]:
        message = build_set_arp_seed(seed)
        self._send_bridge(*message)
        return message

    def set_arp_sync(self, sync: Any) -> tuple[str, list[Any]]:
        message = build_set_arp_sync(sync)
        self._send_bridge(*message)
        return message

    def set_arp_division(self, division: Any) -> tuple[str, list[Any]]:
        message = build_set_arp_division(division)
        self._send_bridge(*message)
        return message

    def set_arp_evolve(self, enabled: Any) -> tuple[str, list[Any]]:
        message = build_set_arp_evolve(enabled)
        self._send_bridge(*message)
        return message

    def set_arp_evolve_beats(self, beats: Any) -> tuple[str, list[Any]]:
        message = build_set_arp_evolve_beats(beats)
        self._send_bridge(*message)
        return message

    def set_arp_evolve_depth(self, depth: Any) -> tuple[str, list[Any]]:
        message = build_set_arp_evolve_depth(depth)
        self._send_bridge(*message)
        return message

    # -- Stage 8.3d lead ------------------------------------------------

    def set_lead_setting(
        self, name: Any, value: Any
    ) -> tuple[str, list[Any]]:
        message = build_set_lead_setting(name, value)
        self._send_bridge(*message)
        return message

    def set_lead_param(self, name: Any, value: Any) -> tuple[str, list[Any]]:
        return self.set_lead_setting(name, value)

    def set_lead_enabled(self, enabled: Any) -> tuple[str, list[Any]]:
        message = build_set_lead_enabled(enabled)
        self._send_bridge(*message)
        return message

    def vary_lead(self) -> tuple[str, list[Any]]:
        message = build_lead_vary()
        self._send_bridge(*message)
        return message

    # -- Stage 9.1 lead input / melodies --------------------------------

    def set_lead_input_enabled(self, enabled: Any) -> tuple[str, list[Any]]:
        message = build_set_lead_input_enabled(enabled)
        self._send_bridge(*message)
        return message

    def set_lead_input_quantize(self, value: Any) -> tuple[str, list[Any]]:
        message = build_set_lead_input_quantize(value)
        self._send_bridge(*message)
        return message

    def set_lead_input_count_in(self, value: Any) -> tuple[str, list[Any]]:
        message = build_set_lead_input_count_in(value)
        self._send_bridge(*message)
        return message

    def set_lead_input_bars(self, value: Any) -> tuple[str, list[Any]]:
        message = build_set_lead_input_bars(value)
        self._send_bridge(*message)
        return message

    def lead_input_note_on(
        self, midi: Any, velocity: Any
    ) -> tuple[str, list[Any]]:
        message = build_lead_input_note_on(midi, velocity)
        self._send_bridge(*message)
        return message

    def lead_input_note_off(self, midi: Any) -> tuple[str, list[Any]]:
        message = build_lead_input_note_off(midi)
        self._send_bridge(*message)
        return message

    def lead_input_record(self) -> tuple[str, list[Any]]:
        message = build_lead_input_record()
        self._send_bridge(*message)
        return message

    def lead_input_stop(self) -> tuple[str, list[Any]]:
        message = build_lead_input_stop()
        self._send_bridge(*message)
        return message

    def lead_input_revert(self) -> tuple[str, list[Any]]:
        message = build_lead_input_revert()
        self._send_bridge(*message)
        return message

    def lead_input_clear(self) -> tuple[str, list[Any]]:
        message = build_lead_input_clear()
        self._send_bridge(*message)
        return message

    def refresh_lead_input(self) -> tuple[str, list[Any]]:
        message = build_lead_input_refresh()
        self._send_bridge(*message)
        return message

    def melody_save(self, name: Any) -> tuple[str, list[Any]]:
        message = build_lead_melody_save(name)
        self._send_bridge(*message)
        return message

    def melody_load(self, name: Any) -> tuple[str, list[Any]]:
        message = build_lead_melody_load(name)
        self._send_bridge(*message)
        return message

    def melody_delete(self, name: Any) -> tuple[str, list[Any]]:
        message = build_lead_melody_delete(name)
        self._send_bridge(*message)
        return message

    def request_melody_list(self) -> tuple[str, list[Any]]:
        message = build_lead_melody_request()
        self._send_bridge(*message)
        return message

    # -- Stage 8.3d drone modes -----------------------------------------

    def set_drone_setting(
        self, name: Any, value: Any
    ) -> tuple[str, list[Any]]:
        message = build_set_drone_setting(name, value)
        self._send_bridge(*message)
        return message

    def set_drone_param(self, name: Any, value: Any) -> tuple[str, list[Any]]:
        return self.set_drone_setting(name, value)

    # -- Stage 8.4 home sets --------------------------------------------

    def home_capture(self, slot: Any) -> tuple[str, list[Any]]:
        message = build_home_capture(slot)
        self._send_bridge(*message)
        return message

    def home_clear(self, slot: Any) -> tuple[str, list[Any]]:
        message = build_home_clear(slot)
        self._send_bridge(*message)
        return message

    def home_go(self, slot: Any) -> tuple[str, list[Any]]:
        message = build_home_go(slot)
        self._send_bridge(*message)
        return message

    def home_return(self) -> tuple[str, list[Any]]:
        message = build_home_return()
        self._send_bridge(*message)
        return message

    def home_jump(self) -> tuple[str, list[Any]]:
        message = build_home_jump()
        self._send_bridge(*message)
        return message

    def home_back(self) -> tuple[str, list[Any]]:
        message = build_home_back()
        self._send_bridge(*message)
        return message

    def set_home_setting(
        self, name: Any, value: Any
    ) -> tuple[str, list[Any]]:
        message = build_set_home_setting(name, value)
        self._send_bridge(*message)
        return message

    def set_home_auto(self, enabled: Any) -> tuple[str, list[Any]]:
        return self.set_home_setting("auto", enabled)

    def set_home_every(self, every: Any) -> tuple[str, list[Any]]:
        return self.set_home_setting("every", every)

    def set_home_seed(self, seed: Any) -> tuple[str, list[Any]]:
        return self.set_home_setting("seed", seed)

    def set_home_drift(self, drift: Any) -> tuple[str, list[Any]]:
        return self.set_home_setting("max_drift", drift)

    def set_home_glide(self, seconds: Any) -> tuple[str, list[Any]]:
        return self.set_home_setting("return_glide", seconds)

    # -- Stage 8.5 performance states -----------------------------------

    def state_save(self, name: Any) -> tuple[str, list[Any]]:
        message = build_state_save(name)
        self._send_bridge(*message)
        return message

    def state_load(self, name: Any) -> tuple[str, list[Any]]:
        message = build_state_load(name)
        self._send_bridge(*message)
        return message

    def state_delete(self, name: Any) -> tuple[str, list[Any]]:
        message = build_state_delete(name)
        self._send_bridge(*message)
        return message

    def request_state_list(self) -> tuple[str, list[Any]]:
        message = build_state_request()
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

    def loop_set_mode(self, index: Any, mode: Any) -> tuple[str, list[Any]]:
        message = build_loop_set_mode(index, mode)
        self._send_sc(*message)
        return message

    def loop_set_beats(self, index: Any, beats: Any) -> tuple[str, list[Any]]:
        message = build_loop_set_beats(index, beats)
        self._send_sc(*message)
        return message

    def loop_set_length(self, index: Any, seconds: Any) -> tuple[str, list[Any]]:
        message = build_loop_set_length(index, seconds)
        self._send_sc(*message)
        return message

    # -- Stage 8.6 loop editing (sclang-owned) --------------------------

    def loop_dub(self, index: Any) -> tuple[str, list[Any]]:
        message = build_loop_dub(index)
        self._send_sc(*message)
        return message

    def loop_fx_fire(self, index: Any) -> tuple[str, list[Any]]:
        """Trigger one Cascade FX pass (UI -> sclang, never the bridge)."""

        message = build_loop_fx_fire(index)
        self._send_sc(*message)
        return message

    def set_loop_edit(
        self, index: Any, param: Any, value: Any
    ) -> tuple[str, list[Any]]:
        message = build_set_loop_edit(index, param, value)
        self._send_sc(*message)
        return message

    def loop_mem_save(self, slot: Any, index: Any) -> tuple[str, list[Any]]:
        message = build_loop_mem_save(slot, index)
        self._send_sc(*message)
        return message

    def loop_mem_recall(self, slot: Any, index: Any) -> tuple[str, list[Any]]:
        message = build_loop_mem_recall(slot, index)
        self._send_sc(*message)
        return message

    def loop_mem_clear(self, slot: Any) -> tuple[str, list[Any]]:
        message = build_loop_mem_clear(slot)
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


__all__ = ["AUTOTUNE_PARAMS",
    "AUTOTUNE_PARAM_MAP",
    "BPM_DEFAULT",
    "BPM_MAX",
    "BPM_MIN",
    "BridgeControlState",
    "DRUM_LANE_DEFAULT_MASK",
    "DRUM_LANE_DEFS",
    "DRUM_RATES",
    "DRUM_RATE_DEFAULT",
    "DrumStatus",
    "FrequencySet",
    "LOOP_BEATS_DEFAULT",
    "LOOP_BEATS_MAX",
    "LOOP_BEATS_MIN",
    "LOOP_LENGTH_DEFAULT",
    "LOOP_LENGTH_MAX",
    "LOOP_LENGTH_MIN",
    "LOOP_MODES",
    "LOOP_STATES",
    "LoopConfig",
    "MUTATION_PARAMS",
    "MUTATION_PARAM_MAP",
    "METER_DENOMINATORS",
    "MidiStatus",
    "MidiToggleState",
    "OSCController",
    "OSCStatusListener",
    "ParamSpec",
    "RhythmStatus",
    "StatusStore",
    "DRONE_DEFAULT_MODE",
    "DRONE_MODES",
    "DRONE_PULSE_GRIDS",
    "DRONE_SETTING_ADDRESSES",
    "DRONE_SLIDE_DIRECTIONS",
    "DRONE_DEFAULT_SEQ_GATE",
    "DRONE_DEFAULT_SEQ_BARS",
    "DRONE_DEFAULT_SEQ_NOTES",
    "DRONE_SEQ_GATE_MAX",
    "DRONE_SEQ_GATE_MIN",
    "DRONE_SEQ_BARS",
    "DRONE_SEQ_BARS_VALUE",
    "DRONE_SEQ_NOTES_MAX",
    "DRONE_SEQ_NOTES_MIN",
    "DRONE_SEQ_GATE_PARAM",
    "DRONE_STATE",
    "DroneStatus",
    "LEAD_DEFAULT_DENSITY",
    "LEAD_DEFAULT_GATE",
    "LEAD_DEFAULT_GRID",
    "LEAD_DEFAULT_PHRASE_BARS",
    "LEAD_DEFAULT_SEED",
    "LEAD_DEFAULT_VARIATION",
    "LEAD_DEFAULT_VELOCITY",
    "LEAD_DENSITY_MAX",
    "LEAD_DENSITY_MIN",
    "LEAD_GATE_MAX",
    "LEAD_GATE_MIN",
    "LEAD_GRIDS",
    "LEAD_GRID_STEPS",
    "LEAD_VARIATIONS",
    "LEAD_BASE_OCTAVE_MAX",
    "LEAD_BASE_OCTAVE_MIN",
    "LEAD_OCTAVE_RANGE_MAX",
    "LEAD_OCTAVE_RANGE_MIN",
    "LEAD_PARAMS",
    "LEAD_PARAM_MAP",
    "LEAD_PHRASE_BARS_MAX",
    "LEAD_PHRASE_BARS_MIN",
    "LEAD_SEED_MAX",
    "LEAD_SETTING_ADDRESSES",
    "LEAD_STATE",
    "LEAD_VARIATION_BARS_MAX",
    "LEAD_VARIATION_BARS_MIN",
    "LEAD_VELOCITY_MAX",
    "LEAD_VELOCITY_MIN",
    "LeadNoteStatus",
    "LeadInputStatus",
    "LeadStatus",
    "MelodyStatus",
    "build_lead_input_clear",
    "build_lead_input_note_off",
    "build_lead_input_note_on",
    "build_lead_input_record",
    "build_lead_input_refresh",
    "build_lead_input_revert",
    "build_lead_input_stop",
    "build_lead_melody_delete",
    "build_lead_melody_load",
    "build_lead_melody_request",
    "build_lead_melody_save",
    "build_lead_vary",
    "build_set_drone_setting",
    "build_set_lead_enabled",
    "build_set_lead_input_bars",
    "build_set_lead_input_count_in",
    "build_set_lead_input_enabled",
    "build_set_lead_input_quantize",
    "build_set_lead_setting",
    "parse_drone_state",
    "parse_lead_input_state",
    "parse_lead_note",
    "parse_lead_state",
    "parse_melody_list",
    "parse_melody_state",
    "build_loop_clear",
    "build_loop_dub",
    "build_loop_fx_fire",
    "LOOP_FX_FIRE",
    "build_loop_mem_clear",
    "build_loop_mem_recall",
    "build_loop_mem_save",
    "build_loop_play",
    "build_loop_record",
    "build_loop_set_beats",
    "build_loop_set_default_length",
    "build_loop_set_length",
    "build_loop_set_mode",
    "build_loop_stop",
    "build_loop_stop_all",
    "build_loop_stop_record",
    "build_set_loop_edit",
    "build_request_state",
    "build_rhythm_request",
    "build_set_autotune",
    "build_set_drum_lane",
    "build_set_drum_rate",
    "build_set_drums_enabled",
    "build_set_meter",
    "build_set_midi_enabled",
    "build_set_mutation",
    "build_set_tempo",
    "build_transport",
    "build_trigger_mutation",
    "build_trigger_octave",
    "build_ui_hello",
    "clamp",
    "clamp_int",
    "drum_lane_bit",
    "drum_lane_mask",
    "drum_lane_states",
    "parse_autotune_state",
    "parse_cpu",
    "parse_drums_state",
    "parse_freqset",
    "parse_loop_config",
    "parse_loop_count",
    "parse_loop_edit",
    "parse_loop_mem",
    "parse_loop_state",
    "LoopEdit",
    "LoopMemory",
    "LoopEditParam",
    "LOOP_AUTO_PLAY_DEFAULT",
    "LOOP_HARM_MODES",
    "LOOP_HARM_MODE_DEFAULT",
    "LOOP_HARM_MIX_DEFAULT",
    "LOOP_HARM_TIME_MIN",
    "LOOP_HARM_TIME_MAX",
    "LOOP_HARM_TIME_DEFAULT",
    "LOOP_HARM_TIME_SYNCS",
    "LOOP_HARM_TIME_SYNC_DEFAULT",
    "LOOP_HARM_TIME_BASE_BEATS",
    "LOOP_HARM_STEPS_MIN",
    "LOOP_HARM_STEPS_MAX",
    "LOOP_HARM_STEPS_DEFAULT",
    "LOOP_HARM_SPACING_MIN",
    "LOOP_HARM_SPACING_MAX",
    "LOOP_HARM_SPACING_DEFAULT",
    "LOOP_HARM_FALLOFF_MIN",
    "LOOP_HARM_FALLOFF_MAX",
    "LOOP_HARM_FALLOFF_DEFAULT",
    "LOOP_HARM_AUTO_DEFAULT",
    "LOOP_HARM_MAX_SECONDS",
    "harm_time_beats",
    "harm_time_seconds",
    "LOOP_COUNT_IN_DEFAULT",
    "LOOP_COUNT_IN_OPTIONS",
    "LOOP_DIVISION_DEFAULT",
    "LOOP_DIVISION_LABELS",
    "LOOP_DIVISIONS",
    "LOOP_EDIT_PARAMS",
    "LOOP_EDIT_PARAM_MAP",
    "LOOP_MEM_SLOTS",
    "LOOP_MEM_SLOT_NAMES",
    "LOOP_RATES",
    "LOOP_RATE_DEFAULT",
    "LOOP_RATE_LABELS",
    "LOOP_RATE_VALUES",
    "LOOP_GRAIN_MOTIONS",
    "LOOP_GRAIN_MOTION_DEFAULT",
    "LOOP_GRAIN_SPEED_MIN",
    "LOOP_GRAIN_SPEED_MAX",
    "LOOP_GRAIN_SPEED_DEFAULT",
    "LOOP_GRAIN_DEPTH_DEFAULT",
    "LOOP_GRAIN_SIZE_JITTER_DEFAULT",
    "LOOP_GRAIN_PITCH_JITTER_DEFAULT",
    "LOOP_GRAIN_PITCH_SNAP_DEFAULT",
    "LOOP_GRAIN_SMEAR_DEFAULT",
    "LOOP_GRAIN_FREEZE_DEFAULT",
    "LOOP_GRAIN_BURST_DEFAULT",
    "LOOP_GRAIN_RATE_DEFAULT",
    "LOOP_GRAIN_RATE_MAX",
    "LOOP_GRAIN_SIZE_DEFAULT_MS",
    "LOOP_GRAIN_POS_DEFAULT",
    "LOOP_GRAIN_SCATTER_DEFAULT",
    "LOOP_GRAIN_PITCH_DEFAULT",
    "LOOP_GRAIN_REVERSE_DEFAULT",
    "LOOP_GRAIN_MORPH_DEFAULT",
    "LOOP_GRAIN_MELT_DEFAULT",
    "LOOP_GRAIN_FILTER_DEFAULT",
    "LOOP_GRAIN_FILTER_RATE_MIN",
    "LOOP_GRAIN_FILTER_RATE_MAX",
    "LOOP_GRAIN_FILTER_RATE_DEFAULT",
    "LOOP_GRAIN_BREATH_MIN",
    "LOOP_GRAIN_BREATH_MAX",
    "LOOP_GRAIN_BREATH_DEFAULT",
    "LOOP_GRAIN_BREATH_RATE_MIN",
    "LOOP_GRAIN_BREATH_RATE_MAX",
    "LOOP_GRAIN_BREATH_RATE_DEFAULT",
    "JI_RATIO_LABELS",
    "ji_ratio_label",
    "loop_rate_label",
    "loop_snap_pitch",
    "loop_rate_value",
    "parse_midi_status",
    "parse_midi_state",
    "parse_rhythm_state",
    "parse_ui_state",
    "GROOVE_DEFAULT_DENSITY",
    "GROOVE_DEFAULT_PRESET",
    "GROOVE_DEFAULT_PROBABILITY",
    "GROOVE_DEFAULT_SEED",
    "GROOVE_DEFAULT_SWING",
    "GROOVE_DEFAULT_SYNCOPATION",
    "GROOVE_PARAMS",
    "GROOVE_PARAM_MAP",
    "GROOVE_PRESETS",
    "GROOVE_SEED_MAX",
    "GrooveStatus",
    "MAX_GRID_STEPS",
    "QuantizeStatus",
    "build_reset_drum_grid",
    "build_set_drum_density",
    "build_set_drum_probability",
    "build_set_drum_seed",
    "build_set_drum_step",
    "build_set_drum_swing",
    "build_set_drum_syncopation",
    "build_set_drums_preset",
    "build_set_groove_modifier",
    "grid_steps_for_meter",
    "parse_grid",
    "parse_groove",
    "parse_quantize",
    "BASS_BASE_OCTAVE_MAX",
    "BASS_BASE_OCTAVE_MIN",
    "BASS_DEFAULT_ACCENT",
    "BASS_DEFAULT_BASE_OCTAVE",
    "BASS_DEFAULT_DENSITY",
    "BASS_DEFAULT_ENABLED",
    "BASS_DEFAULT_GATE_BEATS",
    "BASS_DEFAULT_GRID",
    "BASS_DEFAULT_MOVEMENT",
    "BASS_DEFAULT_OCTAVE_JUMP",
    "BASS_DEFAULT_OCTAVE_RANGE",
    "BASS_DEFAULT_PITCH_MODE",
    "BASS_DEFAULT_PRESET",
    "BASS_DEFAULT_ROOT_DOWNBEAT",
    "BASS_DEFAULT_SEED",
    "BASS_DEFAULT_SYNCOPATION",
    "BASS_DEFAULT_SWING",
    "BASS_GATE_MAX",
    "BASS_GATE_MIN",
    "BASS_GRIDS",
    "BASS_GRID_STEPS",
    "BASS_LENGTH_MAX",
    "BASS_LENGTH_MIN",
    "BASS_OCTAVE_RANGE_MAX",
    "BASS_OCTAVE_RANGE_MIN",
    "BASS_PARAMS",
    "BASS_PARAM_MAP",
    "BASS_PITCH_MODES",
    "BASS_PRESETS",
    "BASS_PRESET_CUSTOM",
    "BASS_PRESET_DISPLAY",
    "BASS_SEED_MAX",
    "BASS_SETTING_ADDRESSES",
    "BASS_SUB_OCTAVES",
    "BASS_HITS_MAX",
    "BASS_HITS_DEFAULT_ENABLED",
    "BASS_HITS_DEFAULT_COUNT",
    "BASS_SET_HITS_ENABLED",
    "BASS_SET_HIT",
    "BASS_CLEAR_HITS",
    "BASS_COPY_HITS",
    "BASS_HITS",
    "BREATH_SHAPES",
    "BREATH_DEFAULT_SHAPE",
    "BREATH_DEPTH_MAX",
    "BREATH_PERIOD_BARS_MIN",
    "BREATH_PERIOD_BARS_MAX",
    "BREATH_DEFAULT_PERIOD_BARS",
    "BREATH_DEFAULT_DEPTH",
    "BREATH_DEFAULT_INTENSITY",
    "BREATH_DEFAULT_PHASE",
    "BREATH_DEFAULT_ARP_LOCAL",
    "BREATH_DEFAULT_ARP_SYNC",
    "BREATH_PARAM_NAMES",
    "BREATH_PARAM_ADDRESSES",
    "BREATH_SET_ENABLED",
    "BREATH_SET_DEPTH",
    "BREATH_SET_PERIOD",
    "BREATH_SET_SHAPE",
    "BREATH_SET_INTENSITY",
    "BREATH_SET_PHASE",
    "BREATH_SET_ARP_LOCAL",
    "BREATH_SET_ARP_SYNC",
    "BreathStatus",
    "build_breath_param",
    "build_bass_hits_enabled",
    "build_bass_hit",
    "build_bass_clear_hits",
    "build_bass_copy_hits",
    "parse_breath_state",
    "parse_bass_hits",
    "BassNoteStatus",
    "BassStatus",
    "build_set_bass_base_octave",
    "build_set_bass_enabled",
    "build_set_bass_grid",
    "build_set_bass_length",
    "build_set_bass_octave_range",
    "build_set_bass_param",
    "build_set_bass_pitch_mode",
    "build_set_bass_preset",
    "build_set_bass_root_downbeat",
    "build_set_bass_seed",
    "build_set_bass_setting",
    "build_set_bass_sub_enabled",
    "build_set_bass_sub_octaves",
    "parse_bass_note",
    "parse_bass_state",
    "HOME_BACK",
    "HOME_CAPTURE",
    "HOME_CLEAR",
    "HOME_DRIFT_DEFAULT",
    "HOME_DRIFT_MAX",
    "HOME_DRIFT_MIN",
    "HOME_EVERY_DEFAULT",
    "HOME_EVERY_MAX",
    "HOME_EVERY_MIN",
    "HOME_GLIDE_DEFAULT",
    "HOME_GLIDE_MAX",
    "HOME_GLIDE_MIN",
    "HOME_GO",
    "HOME_HISTORY_DEFAULT",
    "HOME_JUMP",
    "HOME_RETURN",
    "HOME_SEED_DEFAULT",
    "HOME_SEED_MAX",
    "HOME_SEED_MIN",
    "HOME_SET_AUTO",
    "HOME_SET_DRIFT",
    "HOME_SET_EVERY",
    "HOME_SET_GLIDE",
    "HOME_SET_SEED",
    "HOME_SETTING_ADDRESSES",
    "HOME_SLOT_NAMES",
    "HOME_SLOTS",
    "HOME_STATE",
    "HomeStatus",
    "STATE_DELETE",
    "STATE_LIST",
    "STATE_LOAD",
    "STATE_REQUEST",
    "STATE_SAVE",
    "STATE_STATE",
    "StateStatus",
    "build_state_delete",
    "build_state_load",
    "build_state_request",
    "build_state_save",
    "parse_state_list",
    "parse_state_status",
    "build_home_back",
    "build_home_capture",
    "build_home_clear",
    "build_home_go",
    "build_home_jump",
    "build_home_return",
    "build_set_home_auto",
    "build_set_home_drift",
    "build_set_home_every",
    "build_set_home_glide",
    "build_set_home_seed",
    "build_set_home_setting",
    "parse_home_state",
]

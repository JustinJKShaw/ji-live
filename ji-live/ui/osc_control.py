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
LEAD_VARY = "/ji/lead/vary"
LEAD_STATE = "/ji/lead/state"
LEAD_NOTE = "/ji/lead/note"

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
    ARP_STATE,
    LEAD_STATE,
    LEAD_NOTE,
    DRONE_STATE,
    CPU,
    LOOP_STATE,
    LOOP_CONFIG,
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
}


def build_set_lead_setting(name: Any, value: Any) -> tuple[str, list[Any]]:
    """Build one validated ``/ji/lead/set_*`` message."""

    key = _validate_lead_setting_name(name)
    return LEAD_SETTING_ADDRESSES[key], [_LEAD_COERCE[key](value)]


def build_set_lead_enabled(enabled: Any) -> tuple[str, list[Any]]:
    return build_set_lead_setting("enabled", enabled)


def build_lead_vary() -> tuple[str, list[Any]]:
    return LEAD_VARY, []


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


def build_loop_set_mode(index: Any, mode: Any) -> tuple[str, list[Any]]:
    return LOOP_SET_MODE, [_validate_index(index), str(_validate_loop_mode(mode))]


def build_loop_set_beats(index: Any, beats: Any) -> tuple[str, list[Any]]:
    return LOOP_SET_BEATS, [_validate_index(index), float(_validate_loop_beats(beats))]


def build_loop_set_length(index: Any, seconds: Any) -> tuple[str, list[Any]]:
    return LOOP_SET_LENGTH, [
        _validate_index(index),
        float(_validate_loop_seconds(seconds)),
    ]


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

    @property
    def available(self) -> bool:
        """True when the lead MIDI port (Bus 5) is open."""

        return self.port_ok

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
    """Parse the 16-argument ``/ji/lead/state`` payload."""

    if len(args) != 16:
        raise ValueError(
            "lead state needs 16 values: enabled, port_ok, grid, phrase_bars, "
            "density, gate_beats, velocity, octave_range, base_octave, "
            "variation, variation_index, variation_bars, swing, groove_link, "
            "seed, playing"
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
    ARP_STATE: parse_arp_state,
    LEAD_STATE: parse_lead_state,
    LEAD_NOTE: parse_lead_note,
    DRONE_STATE: parse_drone_state,
    CPU: parse_cpu,
    LOOP_STATE: parse_loop_state,
    LOOP_CONFIG: parse_loop_config,
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
        self.arp: ArpStatus | None = None
        self.lead: LeadStatus | None = None
        self.lead_note: LeadNoteStatus | None = None
        self.drone: DroneStatus | None = None
        self.cpu: float | None = None
        self.loop_states: dict[int, str] = {
            index: "waiting" for index in range(LOOP_INDEX_MIN, LOOP_INDEX_MAX + 1)
        }
        self.loop_configs: dict[int, LoopConfig] = {
            index: LoopConfig()
            for index in range(LOOP_INDEX_MIN, LOOP_INDEX_MAX + 1)
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
        elif address == ARP_STATE:
            self.arp = parsed
            self.bridge_seen = True
        elif address == LEAD_STATE:
            self.lead = parsed
            self.bridge_seen = True
        elif address == LEAD_NOTE:
            self.lead_note = parsed
            self.bridge_seen = True
        elif address == DRONE_STATE:
            self.drone = parsed
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

    # -- Stage 8.3d drone modes -----------------------------------------

    def set_drone_setting(
        self, name: Any, value: Any
    ) -> tuple[str, list[Any]]:
        message = build_set_drone_setting(name, value)
        self._send_bridge(*message)
        return message

    def set_drone_param(self, name: Any, value: Any) -> tuple[str, list[Any]]:
        return self.set_drone_setting(name, value)

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
    "LeadStatus",
    "build_lead_vary",
    "build_set_drone_setting",
    "build_set_lead_enabled",
    "build_set_lead_setting",
    "parse_drone_state",
    "parse_lead_note",
    "parse_lead_state",
    "build_loop_clear",
    "build_loop_play",
    "build_loop_record",
    "build_loop_set_beats",
    "build_loop_set_default_length",
    "build_loop_set_length",
    "build_loop_set_mode",
    "build_loop_stop",
    "build_loop_stop_all",
    "build_loop_stop_record",
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
    "parse_loop_state",
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
]

#!/usr/bin/env python3
"""Stage 7/8.2 performance GUI: one PyQt6 window.

Run from ``ji-live/`` after ``tests/sc/test_live_rig.scd`` and
``ui/control_surface.scd`` are evaluated, and after the bridge is running::

    uv run --with python-osc --with PyQt6 python ui/ji_ui.py

The window is a control surface only.  It never talks to scsynth/Ableton
directly, never starts or stops the audio/MIDI rig, and closing it stops only
its own OSC listener.  All ranges, message builders, and status parsing live in
:mod:`ui.osc_control` so the pure tests do not need PyQt6.
"""

from __future__ import annotations

import argparse
import sys
import time
from typing import Any, Callable

try:
    from PyQt6.QtCore import QObject, QSignalBlocker, Qt, QTimer, pyqtSignal
    from PyQt6.QtGui import QFont
    from PyQt6.QtWidgets import (
        QApplication,
        QCheckBox,
        QComboBox,
        QDial,
        QDoubleSpinBox,
        QGridLayout,
        QGroupBox,
        QHBoxLayout,
        QLabel,
        QMainWindow,
        QMessageBox,
        QPushButton,
        QScrollArea,
        QSpinBox,
        QVBoxLayout,
        QWidget,
    )
except ImportError as exc:  # pragma: no cover - depends on the human's env
    raise SystemExit(
        "PyQt6 is required for the Stage 7/8.2 GUI.\n"
        "Install/run it with: uv run --with python-osc --with PyQt6 "
        "python ui/ji_ui.py\n"
        "See docs/README_stage8.md for details."
    ) from exc

if __package__:  # ``python -m ui.ji_ui`` and normal package imports
    from . import osc_control
    from .osc_control import (
        AUTOTUNE_CHANNELS,
        AUTOTUNE_PARAMS,
        BPM_DEFAULT,
        BPM_MAX,
        BPM_MIN,
        DRUM_LANE_DEFAULT_MASK,
        DRUM_LANE_DEFS,
        DRUM_RATE_LABELS,
        DRUM_RATES,
        GROOVE_DEFAULT_PRESET,
        GROOVE_DEFAULT_SEED,
        GROOVE_PARAMS,
        GROOVE_PRESETS,
        GROOVE_SEED_MAX,
        LOOP_BEATS_DEFAULT,
        LOOP_BEATS_MAX,
        LOOP_BEATS_MIN,
        LOOP_LENGTH_DEFAULT,
        LOOP_LENGTH_MAX,
        LOOP_LENGTH_MIN,
        METER_DEN_DEFAULT,
        METER_DENOMINATORS,
        METER_NUM_DEFAULT,
        METER_NUM_MAX,
        METER_NUM_MIN,
        MAX_GRID_STEPS,
        MUTATION_PARAMS,
        OSCController,
        StatusStore,
        grid_steps_for_meter,
    )
else:  # pragma: no cover - direct ``python ui/ji_ui.py``
    import osc_control  # type: ignore[no-redef]
    from osc_control import (  # type: ignore[no-redef]
        AUTOTUNE_CHANNELS,
        AUTOTUNE_PARAMS,
        BPM_DEFAULT,
        BPM_MAX,
        BPM_MIN,
        DRUM_LANE_DEFAULT_MASK,
        DRUM_LANE_DEFS,
        DRUM_RATE_LABELS,
        DRUM_RATES,
        GROOVE_DEFAULT_PRESET,
        GROOVE_DEFAULT_SEED,
        GROOVE_PARAMS,
        GROOVE_PRESETS,
        GROOVE_SEED_MAX,
        LOOP_BEATS_DEFAULT,
        LOOP_BEATS_MAX,
        LOOP_BEATS_MIN,
        LOOP_LENGTH_DEFAULT,
        LOOP_LENGTH_MAX,
        LOOP_LENGTH_MIN,
        METER_DEN_DEFAULT,
        METER_DENOMINATORS,
        METER_NUM_DEFAULT,
        METER_NUM_MAX,
        METER_NUM_MIN,
        MAX_GRID_STEPS,
        MUTATION_PARAMS,
        OSCController,
        StatusStore,
        grid_steps_for_meter,
    )


# ---------------------------------------------------------------------------
# Reusable knob
# ---------------------------------------------------------------------------


class Knob(QWidget):
    """A compact QDial with a caption and a live value/units label."""

    valueChanged = pyqtSignal(float)

    def __init__(
        self,
        caption: str,
        low: float,
        high: float,
        default: float,
        *,
        steps: int = 1000,
        integer: bool = False,
        fmt: str = "{:.2f}",
        suffix: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._low = float(low)
        self._high = float(high)
        self._integer = bool(integer)
        self._steps = max(1, int(steps) if not integer else int(round(high - low)))
        self._fmt = fmt
        self._suffix = suffix

        self.caption_label = QLabel(caption)
        self.caption_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)

        self.dial = QDial()
        self.dial.setRange(0, self._steps)
        self.dial.setNotchesVisible(False)
        self.dial.setFixedSize(32, 32)

        self.value_label = QLabel()
        self.value_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.value_label.setMinimumWidth(46)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.caption_label)
        layout.addWidget(self.dial, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(self.value_label)

        self.dial.valueChanged.connect(self._on_dial)
        self.set_value(default, emit=False)

    # -- conversion ------------------------------------------------------

    def _value_from_position(self, position: int) -> float:
        if self._integer:
            return float(int(round(self._low)) + int(position))
        span = self._high - self._low
        value = self._low + (span * float(position) / float(self._steps))
        return max(self._low, min(self._high, value))

    def _position_from_value(self, value: float) -> int:
        if self._integer:
            return int(round(value - self._low))
        span = self._high - self._low
        if span <= 0.0:
            return 0
        return int(round((value - self._low) / span * self._steps))

    # -- public ----------------------------------------------------------

    def value(self) -> float:
        return self._value_from_position(self.dial.value())

    def set_value(self, value: float, *, emit: bool = False) -> None:
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return
        if self._integer:
            numeric = float(int(round(numeric)))
        numeric = max(self._low, min(self._high, numeric))
        # Defensive: QDial's default maximum is 99, so re-assert our range
        # before applying a position that may be larger.
        if self.dial.maximum() != self._steps:
            self.dial.setRange(0, self._steps)
        position = self._position_from_value(numeric)
        blocker = QSignalBlocker(self.dial)
        self.dial.setValue(position)
        del blocker
        self._refresh_label(numeric)
        if emit:
            self.valueChanged.emit(self.value())

    def set_range(
        self, low: float, high: float, *, emit: bool = False
    ) -> None:
        current = self.value()
        self._low = float(low)
        self._high = float(high)
        if self._integer:
            self._steps = max(1, int(round(self._high - self._low)))
            blocker = QSignalBlocker(self.dial)
            self.dial.setRange(0, self._steps)
            del blocker
        self.set_value(current, emit=emit)

    def _refresh_label(self, value: float) -> None:
        if self._integer:
            text = self._fmt.format(int(round(value)))
        else:
            text = self._fmt.format(value)
        self.value_label.setText(text + self._suffix)

    def _on_dial(self, _position: int) -> None:
        value = self.value()
        self._refresh_label(value)
        self.valueChanged.emit(value)


# ---------------------------------------------------------------------------
# Qt status relay
# ---------------------------------------------------------------------------


class _StatusRelay(QObject):
    """Carries status messages from the OSC thread to the Qt main thread."""

    message = pyqtSignal(str, object)


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------


class MainWindow(QMainWindow):
    """One-screen performance surface: Mutation, Live Autotune, Loops, Rhythm, Status."""

    def __init__(
        self,
        controller: Any,
        *,
        autostart: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.controller = controller
        self.store = StatusStore()
        self._pending: dict[tuple[Any, ...], float] = {}
        self._status_seen = False
        self._started = False
        self._midi_sync = False
        self._rhythm_sync = False
        self._drum_sync = False
        self._loop_sync = False
        self._loop_custom: set[int] = set()
        self._groove_sync = False
        self._grid_sync = False
        self._grid_built_steps = 0
        self.step_cells: dict[tuple[int, int], QPushButton] = {}
        self._quantize_deadline: float | None = None
        self._quantize_pending = 0

        self._relay = _StatusRelay()
        self._relay.message.connect(
            self._on_status_message, Qt.ConnectionType.QueuedConnection
        )
        self._wire_controller_callback()

        self.setWindowTitle("JI Station - Stage 8.2 Performance")
        self.resize(1280, 720)
        self.setMinimumSize(1080, 600)
        compact = QFont()
        compact.setPointSize(8)
        self.setFont(compact)

        central = QWidget()
        central.setMinimumWidth(1040)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(4, 4, 4, 4)
        outer.setSpacing(3)

        top_row = QHBoxLayout()
        top_row.setSpacing(4)
        top_row.addWidget(self._build_mutation_group(), 3)
        top_row.addWidget(self._build_midi_group(), 2)
        outer.addLayout(top_row)
        outer.addWidget(self._build_autotune_group())
        outer.addWidget(self._build_loops_group())
        outer.addWidget(self._build_rhythm_group())
        outer.addWidget(self._build_groove_group())
        outer.addWidget(self._build_status_group())
        outer.addStretch(1)

        # A scroll area is only a safety net for small screens; the compact
        # layout fits at the default size without scrolling.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(central)
        self.setCentralWidget(scroll)

        self._send_timer = QTimer(self)
        self._send_timer.setInterval(33)  # ~30 Hz coalesced knob sends
        self._send_timer.timeout.connect(self._flush_pending)
        self._send_timer.start()

        # Local countdown for the pending/quantize status line.  The bridge
        # sends one /ji/quantize/state per change, not per animation frame.
        self._quantize_timer = QTimer(self)
        self._quantize_timer.setInterval(100)
        self._quantize_timer.timeout.connect(self._update_quantize_label)
        self._quantize_timer.start()

        self._refresh_status_labels()

        if autostart:
            self.start_controller()

    # -- controller / relay ---------------------------------------------

    def _wire_controller_callback(self) -> None:
        setter = getattr(self.controller, "set_status_callback", None)
        if callable(setter):
            setter(self._relay.message.emit)

    def start_controller(self) -> None:
        """Start the OSC controller (listener first, then hello/query)."""

        self.controller.start()
        self._started = True

    def _on_status_message(self, address: str, args: Any) -> None:
        try:
            recognised = self.store.apply(address, list(args))
        except (TypeError, ValueError) as exc:
            self._set_message(f"Ignored malformed {address}: {exc}")
            return
        if not recognised:
            return
        self._status_seen = True

        if address == osc_control.UI_STATE and self.store.control is not None:
            self._sync_mutation_knobs(self.store.control)
        if address == osc_control.AUTOTUNE_STATE:
            channel = int(round(float(args[0])))
            self._sync_autotune_knobs(channel, self.store.autotune[channel])
        if address == osc_control.LOOP_STATE:
            index = int(round(float(args[0])))
            self._refresh_loop_label(index)
        if address == osc_control.LOOP_CONFIG:
            index = int(round(float(args[0])))
            self._sync_loop_config(index)
        if address in (osc_control.MIDI_STATUS, osc_control.MIDI_STATE):
            self._sync_midi_controls()
        if address == osc_control.RHYTHM_STATE:
            self._sync_rhythm_controls()
            self._update_quantize_label()
        if address == osc_control.DRUMS_STATE:
            self._sync_drum_controls()
        if address == osc_control.DRUMS_GROOVE:
            self._sync_groove_controls()
        if address == osc_control.DRUMS_GRID:
            self._sync_grid()
        if address == osc_control.QUANTIZE_STATE:
            self._sync_quantize()

        self._refresh_status_labels()

    # -- widget construction --------------------------------------------

    def _build_mutation_group(self) -> QGroupBox:
        group = QGroupBox("Mutation")
        layout = QHBoxLayout(group)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(1)

        self.mutation_knobs: dict[str, Knob] = {}
        for spec in MUTATION_PARAMS:
            steps = (
                int(round(spec.high - spec.low))
                if spec.integer
                else (2000 if spec.high - spec.low >= 2.0 else 1000)
            )
            knob = Knob(
                spec.label,
                spec.low,
                spec.high,
                spec.default,
                steps=steps,
                integer=spec.integer,
                fmt=spec.fmt,
                suffix=spec.suffix,
            )
            knob.valueChanged.connect(
                lambda value, key=spec.key: self._on_mutation_knob(key, value)
            )
            self.mutation_knobs[spec.key] = knob
            layout.addWidget(knob)

        layout.addSpacing(6)
        self.mutate_button = QPushButton("Mutate")
        self.octave_button = QPushButton("Octave")
        for button in (self.mutate_button, self.octave_button):
            button.setMinimumHeight(26)
            button.setMinimumWidth(78)
        self.mutate_button.clicked.connect(self._on_mutate_clicked)
        self.octave_button.clicked.connect(self._on_octave_clicked)
        layout.addWidget(self.mutate_button)
        layout.addWidget(self.octave_button)
        layout.addStretch(1)
        return group

    def _build_midi_group(self) -> QGroupBox:
        group = QGroupBox("MIDI")
        layout = QVBoxLayout(group)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(1)

        row = QHBoxLayout()
        row.setSpacing(6)
        self.midi_drone_checkbox = QCheckBox("Drone on")
        self.midi_arp_checkbox = QCheckBox("Arp on")
        self.midi_drone_checkbox.setEnabled(False)
        self.midi_arp_checkbox.setEnabled(False)
        self.midi_drone_checkbox.toggled.connect(self._on_midi_toggle)
        self.midi_arp_checkbox.toggled.connect(self._on_midi_toggle)
        row.addWidget(self.midi_drone_checkbox)
        row.addWidget(self.midi_arp_checkbox)
        row.addStretch(1)
        layout.addLayout(row)

        self.midi_label = QLabel("MIDI: waiting for bridge")
        self.midi_label.setWordWrap(True)
        self.midi_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        layout.addWidget(self.midi_label)
        return group

    def _build_autotune_group(self) -> QGroupBox:
        group = QGroupBox("Live Autotune")
        layout = QHBoxLayout(group)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(4)

        self.link_checkbox = QCheckBox("Link")
        self.link_checkbox.setToolTip("Move both input channels together")
        layout.addWidget(self.link_checkbox, 0, Qt.AlignmentFlag.AlignVCenter)

        self.autotune_knobs: dict[tuple[int, str], Knob] = {}
        for channel in AUTOTUNE_CHANNELS:
            channel_group = QGroupBox(f"Input {channel}")
            row = QHBoxLayout(channel_group)
            row.setContentsMargins(3, 1, 3, 1)
            row.setSpacing(1)
            for spec in AUTOTUNE_PARAMS:
                steps = (
                    int(round(spec.high - spec.low))
                    if spec.integer
                    else (
                        2000
                        if spec.high - spec.low >= 2.0
                        else 1000
                    )
                )
                knob = Knob(
                    spec.label,
                    spec.low,
                    spec.high,
                    spec.default,
                    steps=steps,
                    integer=spec.integer,
                    fmt=spec.fmt,
                    suffix=spec.suffix,
                )
                knob.valueChanged.connect(
                    lambda value, ch=channel, key=spec.key: self._on_autotune_knob(
                        ch, key, value
                    )
                )
                self.autotune_knobs[(channel, spec.key)] = knob
                row.addWidget(knob)
            layout.addWidget(channel_group)
        layout.addStretch(1)
        return group

    def _build_loops_group(self) -> QGroupBox:
        group = QGroupBox("Loops")
        layout = QVBoxLayout(group)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(3)

        loop_row = QHBoxLayout()
        loop_row.setSpacing(3)
        self.loop_state_labels: dict[int, QLabel] = {}
        self.loop_buttons: dict[tuple[int, str], QPushButton] = {}
        self.loop_mode_combos: dict[int, QComboBox] = {}
        self.loop_length_spins: dict[int, QDoubleSpinBox] = {}

        for index in range(1, 5):
            loop_group = QGroupBox(f"Loop {index}")
            loop_layout = QVBoxLayout(loop_group)
            loop_layout.setContentsMargins(3, 1, 3, 1)
            loop_layout.setSpacing(1)

            state_label = QLabel("waiting")
            state_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            self.loop_state_labels[index] = state_label
            loop_layout.addWidget(state_label)

            controls = QHBoxLayout()
            controls.setSpacing(2)
            mode_combo = QComboBox()
            mode_combo.addItems(["Free", "Beat"])
            mode_combo.setCurrentIndex(0)
            mode_combo.setFixedWidth(68)
            mode_combo.setToolTip(
                "Free: seconds, immediate. Beat: next-beat quantized. Tempo "
                "changes do not time-stretch an existing loop; stop and "
                "re-record, or use free mode."
            )
            mode_combo.currentIndexChanged.connect(
                lambda _position, i=index: self._on_loop_mode_changed(i)
            )
            self.loop_mode_combos[index] = mode_combo

            length_spin = QDoubleSpinBox()
            length_spin.setDecimals(1)
            length_spin.setRange(LOOP_LENGTH_MIN, LOOP_LENGTH_MAX)
            length_spin.setSingleStep(0.5)
            length_spin.setValue(LOOP_LENGTH_DEFAULT)
            length_spin.setSuffix(" s")
            length_spin.setFixedWidth(78)
            length_spin.setToolTip(
                "Free: 1-30 seconds. Beat: 1-64 beats, taken as "
                "beats * 60 / bpm at record time."
            )
            length_spin.valueChanged.connect(
                lambda value, i=index: self._on_loop_length_changed(i, value)
            )
            self.loop_length_spins[index] = length_spin

            controls.addWidget(mode_combo)
            controls.addWidget(length_spin)
            controls.addStretch(1)
            loop_layout.addLayout(controls)

            grid = QGridLayout()
            grid.setContentsMargins(0, 0, 0, 0)
            grid.setSpacing(1)
            # QPushButton.clicked emits a bool; bind it to ``_checked`` so it
            # cannot override the loop index default.
            actions = (
                (
                    "record",
                    "Rec",
                    lambda _checked=False, i=index: self._record_loop(i),
                ),
                (
                    "stop_record",
                    "Stop Rec",
                    lambda _checked=False, i=index: self._guard(
                        self.controller.loop_stop_record, i
                    ),
                ),
                (
                    "play",
                    "Play",
                    lambda _checked=False, i=index: self._play_loop(i),
                ),
                (
                    "stop",
                    "Stop",
                    lambda _checked=False, i=index: self._guard(
                        self.controller.loop_stop, i
                    ),
                ),
                (
                    "clear",
                    "Clear",
                    lambda _checked=False, i=index: self._guard(
                        self.controller.loop_clear, i
                    ),
                ),
            )
            placements = (
                (0, 0, 1, 1),
                (0, 1, 1, 1),
                (0, 2, 1, 1),
                (1, 0, 1, 1),
                (1, 1, 1, 2),
            )
            for (name, text, slot), (row, column, row_span, col_span) in zip(
                actions, placements
            ):
                button = QPushButton(text)
                button.setMinimumHeight(20)
                button.clicked.connect(slot)
                self.loop_buttons[(index, name)] = button
                grid.addWidget(button, row, column, row_span, col_span)
            loop_layout.addLayout(grid)
            loop_row.addWidget(loop_group)

        layout.addLayout(loop_row)

        bottom = QHBoxLayout()
        bottom.setSpacing(5)
        self.stop_all_button = QPushButton("Stop All")
        self.stop_all_button.setMinimumHeight(24)
        self.stop_all_button.setMinimumWidth(90)
        self.stop_all_button.clicked.connect(
            lambda: self._guard(self.controller.loop_stop_all)
        )
        self.length_spin = QDoubleSpinBox()
        self.length_spin.setDecimals(1)
        self.length_spin.setRange(LOOP_LENGTH_MIN, LOOP_LENGTH_MAX)
        self.length_spin.setSingleStep(0.5)
        self.length_spin.setValue(LOOP_LENGTH_DEFAULT)
        self.length_spin.setSuffix(" s")
        self.length_spin.setFixedWidth(80)
        self.length_spin.setToolTip(
            "Default free-loop length; loops you have not set individually "
            "follow this value."
        )
        self.length_spin.valueChanged.connect(self._on_length_changed)
        bottom.addWidget(self.stop_all_button)
        bottom.addWidget(QLabel("Default len"))
        bottom.addWidget(self.length_spin)
        bottom.addStretch(1)
        layout.addLayout(bottom)
        return group

    def _build_rhythm_group(self) -> QGroupBox:
        group = QGroupBox("Rhythm")
        layout = QVBoxLayout(group)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(1)

        top = QHBoxLayout()
        top.setSpacing(4)

        self.transport_button = QPushButton("Play")
        self.transport_button.setCheckable(True)
        self.transport_button.setMinimumHeight(24)
        self.transport_button.setMinimumWidth(64)
        self.transport_button.setToolTip(
            "Start/stop rhythm scheduling. Beat-mode loops stop on transport "
            "stop; free loops keep running."
        )
        self.transport_button.toggled.connect(self._on_transport_toggled)
        top.addWidget(self.transport_button)

        top.addWidget(QLabel("BPM"))
        self.bpm_spin = QSpinBox()
        self.bpm_spin.setRange(int(BPM_MIN), int(BPM_MAX))
        self.bpm_spin.setValue(int(BPM_DEFAULT))
        self.bpm_spin.setFixedWidth(64)
        self.bpm_spin.setToolTip(
            "Quarter-note BPM (20-300). Changes while running take effect on "
            "the next drum step and on sclang's loop clock."
        )
        self.bpm_spin.valueChanged.connect(self._on_bpm_changed)
        top.addWidget(self.bpm_spin)

        top.addSpacing(2)
        top.addWidget(QLabel("Meter"))
        self.meter_num_spin = QSpinBox()
        self.meter_num_spin.setRange(METER_NUM_MIN, METER_NUM_MAX)
        self.meter_num_spin.setValue(METER_NUM_DEFAULT)
        self.meter_num_spin.setFixedWidth(46)
        self.meter_num_spin.valueChanged.connect(self._on_meter_changed)
        self.meter_den_combo = QComboBox()
        self.meter_den_combo.addItems(
            [str(value) for value in METER_DENOMINATORS]
        )
        self.meter_den_combo.setCurrentText(str(METER_DEN_DEFAULT))
        self.meter_den_combo.setFixedWidth(52)
        self.meter_den_combo.currentIndexChanged.connect(self._on_meter_changed)
        top.addWidget(self.meter_num_spin)
        top.addWidget(QLabel("/"))
        top.addWidget(self.meter_den_combo)

        top.addSpacing(4)
        self.drum_enable_checkbox = QCheckBox("Drums on")
        # Disabled until /ji/drums/state says the port is available; the state
        # message also sets the checked value.
        self.drum_enable_checkbox.setChecked(False)
        self.drum_enable_checkbox.setEnabled(False)
        self.drum_enable_checkbox.toggled.connect(self._on_drum_enabled_toggled)
        self.drum_rate_combo = QComboBox()
        self.drum_rate_combo.addItems(list(DRUM_RATE_LABELS))
        self.drum_rate_combo.setCurrentIndex(DRUM_RATES.index(1.0))
        self.drum_rate_combo.setFixedWidth(80)
        self.drum_rate_combo.setEnabled(False)
        self.drum_rate_combo.currentIndexChanged.connect(
            self._on_drum_rate_changed
        )
        top.addWidget(self.drum_enable_checkbox)
        top.addWidget(self.drum_rate_combo)

        self.resync_button = QPushButton("Resync")
        self.resync_button.setMinimumHeight(24)
        self.resync_button.setMinimumWidth(60)
        self.resync_button.clicked.connect(
            lambda: self._guard(self.controller.request_rhythm_resync)
        )
        top.addWidget(self.resync_button)
        top.addStretch(1)
        layout.addLayout(top)

        self.rhythm_label = QLabel("Rhythm: waiting for bridge")
        self.drum_label = QLabel("Drums: waiting for bridge")
        self.quantize_label = QLabel("Next beat: waiting for bridge")
        status_row = QHBoxLayout()
        status_row.setSpacing(12)
        for label in (self.rhythm_label, self.drum_label, self.quantize_label):
            label.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
            )
            status_row.addWidget(label)
        status_row.addStretch(1)
        layout.addLayout(status_row)

        lane_grid = QGridLayout()
        lane_grid.setHorizontalSpacing(4)
        lane_grid.setVerticalSpacing(0)
        self.lane_checkboxes: dict[int, QCheckBox] = {}
        for position, (note, _name, label) in enumerate(DRUM_LANE_DEFS):
            checkbox = QCheckBox(label)
            checkbox.setChecked(bool(DRUM_LANE_DEFAULT_MASK & (1 << position)))
            checkbox.setEnabled(False)  # enabled by /ji/drums/state
            checkbox.toggled.connect(
                lambda checked, n=note: self._on_lane_toggled(n, checked)
            )
            self.lane_checkboxes[note] = checkbox
            lane_grid.addWidget(checkbox, position // 8, position % 8)
        layout.addLayout(lane_grid)
        return group

    def _build_groove_group(self) -> QGroupBox:
        group = QGroupBox("Groove")
        layout = QVBoxLayout(group)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(2)

        row = QHBoxLayout()
        row.setSpacing(3)
        row.addWidget(QLabel("Preset"))
        self.groove_preset_combo = QComboBox()
        self.groove_preset_combo.addItems(list(GROOVE_PRESETS))
        try:
            self.groove_preset_combo.setCurrentIndex(
                list(GROOVE_PRESETS).index(GROOVE_DEFAULT_PRESET)
            )
        except ValueError:  # pragma: no cover - constant lists stay aligned
            self.groove_preset_combo.setCurrentIndex(0)
        self.groove_preset_combo.setFixedWidth(82)
        self.groove_preset_combo.currentIndexChanged.connect(
            self._on_groove_preset_changed
        )
        row.addWidget(self.groove_preset_combo)

        self.groove_knobs: dict[str, Knob] = {}
        for spec in GROOVE_PARAMS:
            knob = Knob(
                spec.label,
                spec.low,
                spec.high,
                spec.default,
                steps=1000,
                fmt="{:.2f}",
            )
            knob.valueChanged.connect(
                lambda value, key=spec.key: self._on_groove_knob(key, value)
            )
            self.groove_knobs[spec.key] = knob
            row.addWidget(knob)

        row.addWidget(QLabel("Seed"))
        self.groove_seed_spin = QSpinBox()
        self.groove_seed_spin.setRange(0, int(GROOVE_SEED_MAX))
        self.groove_seed_spin.setValue(int(GROOVE_DEFAULT_SEED))
        self.groove_seed_spin.setFixedWidth(90)
        self.groove_seed_spin.setToolTip(
            "Groove seed. Same preset + modifiers + seed + bar reproduce the "
            "same generated ghosts."
        )
        self.groove_seed_spin.valueChanged.connect(self._on_groove_seed_changed)
        row.addWidget(self.groove_seed_spin)

        self.show_grid_checkbox = QCheckBox("Show grid")
        self.show_grid_checkbox.setChecked(False)
        self.show_grid_checkbox.setToolTip(
            "Hidden by default to keep the window compact. Builds the 16-lane "
            "step grid lazily on first show."
        )
        self.show_grid_checkbox.toggled.connect(self._on_show_grid)
        row.addWidget(self.show_grid_checkbox)
        self.reset_grid_button = QPushButton("Reset grid")
        self.reset_grid_button.setMinimumHeight(22)
        self.reset_grid_button.clicked.connect(
            lambda: self._guard(self.controller.reset_drum_grid)
        )
        row.addWidget(self.reset_grid_button)
        row.addStretch(1)
        layout.addLayout(row)

        self.grid_scroll = QScrollArea()
        self.grid_scroll.setWidgetResizable(True)
        self.grid_scroll.setVisible(False)
        self.grid_scroll.setMaximumHeight(190)
        self.grid_container = QWidget()
        self.grid_layout = QGridLayout(self.grid_container)
        self.grid_layout.setContentsMargins(0, 0, 0, 0)
        self.grid_layout.setSpacing(0)
        self.grid_scroll.setWidget(self.grid_container)
        layout.addWidget(self.grid_scroll)
        return group

    # -- Stage 8.2 groove grid ------------------------------------------

    def _grid_steps(self) -> int:
        groove = self.store.groove
        if groove is not None:
            return max(1, min(MAX_GRID_STEPS, int(groove.grid_steps)))
        rhythm = self.store.rhythm
        if rhythm is not None:
            return grid_steps_for_meter(rhythm.meter_num, rhythm.meter_den)
        return 16

    def _ensure_grid(self, steps: int | None = None) -> None:
        """Lazily build the 16 x N step grid; safe to call repeatedly."""

        wanted = self._grid_steps() if steps is None else int(steps)
        wanted = max(1, min(MAX_GRID_STEPS, wanted))
        if self.step_cells and self._grid_built_steps == wanted:
            self._apply_grid_from_store()
            return

        self._grid_sync = True
        try:
            while self.grid_layout.count():
                item = self.grid_layout.takeAt(0)
                widget = item.widget()
                if widget is not None:
                    widget.deleteLater()
            self.step_cells.clear()

            corner = QLabel("Lane")
            corner.setFixedWidth(52)
            self.grid_layout.addWidget(corner, 0, 0)
            for step in range(wanted):
                header = QLabel(str(step + 1))
                header.setAlignment(Qt.AlignmentFlag.AlignHCenter)
                header.setFixedSize(14, 14)
                self.grid_layout.addWidget(header, 0, step + 1)

            for lane, (_note, _name, label) in enumerate(DRUM_LANE_DEFS):
                lane_label = QLabel(label)
                lane_label.setFixedWidth(52)
                lane_label.setToolTip(label)
                self.grid_layout.addWidget(lane_label, lane + 1, 0)
                for step in range(wanted):
                    cell = QPushButton()
                    cell.setCheckable(True)
                    cell.setFixedSize(14, 14)
                    cell.setStyleSheet(
                        "QPushButton{padding:0; margin:0; border:1px solid "
                        "#888888;} QPushButton:checked{background:#4a90d9;}"
                    )
                    cell.setToolTip(f"{label} step {step + 1}")
                    cell.toggled.connect(
                        lambda checked, l=lane, s=step: self._on_grid_cell(
                            l, s, checked
                        )
                    )
                    self.step_cells[(lane, step)] = cell
                    self.grid_layout.addWidget(cell, lane + 1, step + 1)
            self._grid_built_steps = wanted
        finally:
            self._grid_sync = False
        self._apply_grid_from_store()

    def _apply_grid_from_store(self) -> None:
        masks = self.store.grid
        if masks is None or not self.step_cells:
            return
        self._grid_sync = True
        try:
            for (lane, step), cell in self.step_cells.items():
                on = bool(masks[lane] & (1 << step))
                blocker = QSignalBlocker(cell)
                cell.setChecked(on)
                del blocker
        finally:
            self._grid_sync = False

    def _on_show_grid(self, checked: bool) -> None:
        self.grid_scroll.setVisible(bool(checked))
        if checked:
            self._ensure_grid()

    def _on_grid_cell(self, lane: int, step: int, checked: bool) -> None:
        if self._grid_sync:
            return
        self._guard(self.controller.set_drum_step, int(lane), int(step), bool(checked))
        self._set_message(
            f"Grid cell {lane}:{step} {'on' if checked else 'off'} queued"
        )

    def _on_groove_preset_changed(self, position: int) -> None:
        if self._groove_sync:
            return
        if not 0 <= position < len(GROOVE_PRESETS):
            return
        self._guard(
            self.controller.set_drums_preset, GROOVE_PRESETS[position]
        )
        self._set_message(
            f"Groove preset {GROOVE_PRESETS[position]} queued for the next beat"
            if self._transport_running()
            else f"Groove preset {GROOVE_PRESETS[position]} applied immediately"
        )

    def _on_groove_knob(self, key: str, value: float) -> None:
        self._pending[("groove", key)] = value

    def _on_groove_seed_changed(self, value: int) -> None:
        if self._groove_sync:
            return
        self._guard(self.controller.set_drum_seed, int(value))

    def _transport_running(self) -> bool:
        rhythm = self.store.rhythm
        return rhythm is not None and bool(rhythm.running)

    def _sync_groove_controls(self) -> None:
        groove = self.store.groove
        if groove is None:
            return
        self._groove_sync = True
        try:
            if groove.preset in GROOVE_PRESETS:
                self.groove_preset_combo.setCurrentIndex(
                    list(GROOVE_PRESETS).index(groove.preset)
                )
            for spec in GROOVE_PARAMS:
                knob = self.groove_knobs.get(spec.key)
                if knob is not None:
                    knob.set_value(groove.modifiers.get(spec.key, spec.default))
            self.groove_seed_spin.setValue(int(groove.seed))
        finally:
            self._groove_sync = False
        if self.grid_scroll.isVisible():
            if self._grid_built_steps != groove.grid_steps:
                self._ensure_grid(groove.grid_steps)
            else:
                self._apply_grid_from_store()

    def _sync_grid(self) -> None:
        if not self.grid_scroll.isVisible():
            return
        self._ensure_grid()
        self._apply_grid_from_store()

    def _sync_quantize(self) -> None:
        quantize = self.store.quantize
        if quantize is None:
            self._quantize_deadline = None
            self._quantize_pending = 0
        elif quantize.seconds < 0.0:
            self._quantize_deadline = None
            self._quantize_pending = int(quantize.pending)
        else:
            self._quantize_deadline = time.monotonic() + float(quantize.seconds)
            self._quantize_pending = int(quantize.pending)
        self._update_quantize_label()

    def _update_quantize_label(self) -> None:
        rhythm = self.store.rhythm
        if rhythm is not None and not rhythm.running:
            self.quantize_label.setText("Next beat: - | quantized off (immediate)")
            return
        if self.store.quantize is None:
            self.quantize_label.setText("Next beat: waiting for bridge")
            return
        if self._quantize_deadline is None:
            self.quantize_label.setText(
                f"Next beat: - | {self._quantize_pending} pending"
            )
            return
        remaining = max(0.0, self._quantize_deadline - time.monotonic())
        self.quantize_label.setText(
            f"Next beat: {remaining:.2f} s | {self._quantize_pending} pending"
        )

    def _build_status_group(self) -> QGroupBox:
        group = QGroupBox("Status")
        grid = QGridLayout(group)
        grid.setContentsMargins(4, 2, 4, 2)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(1)

        fixed = QFont("Monospace")
        fixed.setStyleHint(QFont.StyleHint.TypeWriter)
        fixed.setPointSize(8)

        self.bridge_status_label = QLabel("BRIDGE: waiting for bridge")
        self.sc_status_label = QLabel("SCLANG: waiting for sclang")
        self.freqset_label = QLabel("Set: waiting for bridge")
        self.anchor_label = QLabel("Anchor: waiting for bridge")
        self.fundamental_label = QLabel("Fundamental: waiting for bridge")
        self.control_label = QLabel("Controls: waiting for bridge")
        self.cpu_label = QLabel("CPU: waiting for sclang")
        self.loop_status_label = QLabel("Loop states: waiting for sclang")
        self.message_label = QLabel("")
        self.message_label.setWordWrap(True)

        for label in (
            self.bridge_status_label,
            self.sc_status_label,
            self.freqset_label,
            self.anchor_label,
            self.fundamental_label,
            self.control_label,
            self.cpu_label,
            self.loop_status_label,
            self.message_label,
        ):
            label.setFont(fixed)
            label.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse
            )

        self.freqset_label.setWordWrap(True)
        self.control_label.setWordWrap(True)

        grid.addWidget(self.bridge_status_label, 0, 0)
        grid.addWidget(self.sc_status_label, 0, 1)
        grid.addWidget(self.cpu_label, 0, 2)
        grid.addWidget(self.loop_status_label, 0, 3)
        grid.addWidget(self.freqset_label, 1, 0, 1, 4)
        grid.addWidget(self.anchor_label, 2, 0)
        grid.addWidget(self.fundamental_label, 2, 1)
        grid.addWidget(self.control_label, 2, 2, 1, 2)
        grid.addWidget(self.message_label, 3, 0, 1, 4)
        return group

    # -- knob queueing ---------------------------------------------------

    def _on_mutation_knob(self, key: str, value: float) -> None:
        self._pending[("mutation", key)] = value

    def _on_autotune_knob(self, channel: int, key: str, value: float) -> None:
        channels = AUTOTUNE_CHANNELS if self.link_checkbox.isChecked() else (channel,)
        for target in channels:
            self._pending[("autotune", target, key)] = value

    def _displayed_length(self) -> float:
        """The default free length shown by the spin box, in seconds."""

        return round(self.length_spin.value(), 1)

    def _on_length_changed(self, value: float) -> None:
        # The spin box shows one decimal; send exactly that value.  Free-mode
        # loops that the user has not customised follow this default, which
        # keeps the Stage 7 global-length behavior.
        seconds = round(value, 1)
        self._pending[("length",)] = seconds
        for index in range(1, 5):
            if index in self._loop_custom:
                continue
            config = self.store.loop_configs.get(index)
            if config is None:
                continue
            self.store.loop_configs[index] = osc_control.LoopConfig(
                mode=config.mode, beats=config.beats, seconds=seconds
            )
            if config.mode == "free":
                spin = self.loop_length_spins.get(index)
                if spin is not None:
                    was_sync = self._loop_sync
                    self._loop_sync = True
                    try:
                        spin.setValue(seconds)
                    finally:
                        self._loop_sync = was_sync

    # -- Stage 8.1 rhythm / drum / loop controls -----------------------

    def _on_bpm_changed(self, value: int) -> None:
        self._pending[("bpm",)] = float(round(value))

    def _on_transport_toggled(self, checked: bool) -> None:
        self.transport_button.setText("Stop" if checked else "Play")
        if self._rhythm_sync:
            return
        self._guard(self.controller.set_transport, bool(checked))

    def _on_meter_changed(self, _value: Any = None) -> None:
        if self._rhythm_sync:
            return
        self._guard(
            self.controller.set_meter,
            int(self.meter_num_spin.value()),
            int(self.meter_den_combo.currentText()),
        )

    def _on_drum_enabled_toggled(self, checked: bool) -> None:
        if self._drum_sync:
            return
        self._guard(self.controller.set_drums_enabled, bool(checked))
        if self._transport_running():
            self._set_message("Drums on/off queued for the next beat")

    def _on_drum_rate_changed(self, position: int) -> None:
        if self._drum_sync:
            return
        if 0 <= position < len(DRUM_RATES):
            self._guard(self.controller.set_drum_rate, DRUM_RATES[position])
            if self._transport_running():
                self._set_message("Drum rate queued for the next beat")

    def _on_lane_toggled(self, note: int, checked: bool) -> None:
        if self._drum_sync:
            return
        self._guard(self.controller.set_drum_lane, int(note), bool(checked))
        if self._transport_running():
            self._set_message("Drum lane queued for the next beat")

    def _loop_mode(self, index: int) -> str:
        combo = self.loop_mode_combos[index]
        return "beat" if combo.currentIndex() == 1 else "free"

    def _on_loop_mode_changed(self, index: int) -> None:
        if self._loop_sync:
            return
        mode = self._loop_mode(index)
        self._guard(self.controller.loop_set_mode, index, mode)
        self._configure_loop_length(index, mode)
        self._refresh_loop_label(index)

    def _configure_loop_length(self, index: int, mode: str) -> None:
        """Switch the per-loop length control between seconds and beats."""

        spin = self.loop_length_spins[index]
        config = self.store.loop_configs.get(
            index, osc_control.LoopConfig()
        )
        was_sync = self._loop_sync
        self._loop_sync = True
        try:
            if mode == "beat":
                spin.setDecimals(0)
                spin.setRange(LOOP_BEATS_MIN, LOOP_BEATS_MAX)
                spin.setSingleStep(1.0)
                spin.setSuffix(" b")
                spin.setValue(float(round(config.beats)))
            else:
                spin.setDecimals(1)
                spin.setRange(LOOP_LENGTH_MIN, LOOP_LENGTH_MAX)
                spin.setSingleStep(0.5)
                spin.setSuffix(" s")
                spin.setValue(float(config.seconds))
        finally:
            self._loop_sync = was_sync

    def _on_loop_length_changed(self, index: int, value: float) -> None:
        if self._loop_sync:
            return
        if self._loop_mode(index) == "beat":
            self._pending[("loop_beats", index)] = float(round(value))
        else:
            self._loop_custom.add(index)
            self._pending[("loop_length", index)] = round(float(value), 1)

    def _loop_transport_ready(self, index: int) -> bool:
        """Reject beat-mode record/play while the transport is stopped."""

        if self._loop_mode(index) != "beat":
            return True
        rhythm = self.store.rhythm
        if rhythm is not None and not rhythm.running:
            self._set_message(
                f"Loop {index} is beat-locked; press Play (transport) first"
            )
            return False
        return True

    def _record_loop(self, index: int) -> None:
        if not self._loop_transport_ready(index):
            return
        if self._loop_mode(index) == "beat":
            # sclang computes beats * 60 / bpm at record time.
            self._guard(self.controller.loop_record, index)
            self._set_message(
                f"Loop {index} record queued for the next beat"
            )
            return
        # Free mode keeps the Stage 7 behavior but uses this loop's seconds.
        config = self.store.loop_configs.get(index)
        seconds = (
            config.seconds if config is not None else self._displayed_length()
        )
        self._guard(self.controller.loop_record, index, seconds)

    def _play_loop(self, index: int) -> None:
        if not self._loop_transport_ready(index):
            return
        self._guard(self.controller.loop_play, index)
        if self._loop_mode(index) == "beat":
            self._set_message(f"Loop {index} play queued for the next beat")

    def _on_mutate_clicked(self) -> None:
        self._guard(self.controller.trigger_mutation)
        self._set_message(
            "Mutation queued for the next beat"
            if self._transport_running()
            else "Mutation applied immediately (transport stopped)"
        )

    def _on_octave_clicked(self) -> None:
        self._guard(self.controller.trigger_octave)
        self._set_message(
            "Octave queued for the next beat"
            if self._transport_running()
            else "Octave applied immediately (transport stopped)"
        )

    def _on_midi_toggle(self, _checked: bool) -> None:
        if self._midi_sync:
            return
        self._guard(
            self.controller.set_midi_enabled,
            self.midi_drone_checkbox.isChecked(),
            self.midi_arp_checkbox.isChecked(),
        )
        if self._transport_running():
            self._set_message("Drone/Arp change queued for the next beat")

    def _sync_midi_controls(self) -> None:
        """Reflect bridge MIDI enabled/ports and per-bus switch state."""

        midi = self.store.midi
        state = self.store.midi_state
        enabled = bool(midi.enabled) if midi is not None else False
        drone_on = state.drone_enabled if state is not None else True
        arp_on = state.arp_enabled if state is not None else True
        self._midi_sync = True
        try:
            self.midi_drone_checkbox.setEnabled(enabled)
            self.midi_arp_checkbox.setEnabled(enabled)
            self.midi_drone_checkbox.setChecked(bool(enabled and drone_on))
            self.midi_arp_checkbox.setChecked(bool(enabled and arp_on))
        finally:
            self._midi_sync = False

    def _flush_pending(self) -> None:
        if not self._pending:
            return
        pending = self._pending
        self._pending = {}
        for key, value in pending.items():
            if key[0] == "mutation":
                self._guard(self.controller.set_mutation, key[1], value)
            elif key[0] == "autotune":
                self._guard(
                    self.controller.set_autotune, key[1], key[2], value
                )
            elif key[0] == "length":
                self._guard(self.controller.loop_set_default_length, value)
            elif key[0] == "bpm":
                self._guard(self.controller.set_tempo, value)
            elif key[0] == "loop_beats":
                self._guard(self.controller.loop_set_beats, key[1], value)
            elif key[0] == "loop_length":
                self._guard(self.controller.loop_set_length, key[1], value)
            elif key[0] == "groove":
                self._guard(self.controller.set_groove_modifier, key[1], value)

    def _guard(self, action: Callable[..., Any], *args: Any) -> None:
        try:
            action(*args)
        except Exception as exc:  # UI must stay alive if a send fails
            self._set_message(f"OSC send failed: {exc}")
            print(f"[UI] send failed: {exc}", file=sys.stderr)

    # -- syncing incoming state -----------------------------------------

    def _sync_mutation_knobs(self, control: Any) -> None:
        for key, knob in self.mutation_knobs.items():
            if key == "change_count":
                knob.set_range(1, control.max_changes)
            knob.set_value(getattr(control, key), emit=False)

    def _sync_autotune_knobs(self, channel: int, values: dict[str, float]) -> None:
        for spec in AUTOTUNE_PARAMS:
            knob = self.autotune_knobs.get((channel, spec.key))
            if knob is not None and spec.key in values:
                knob.set_value(values[spec.key], emit=False)

    def _sync_rhythm_controls(self) -> None:
        """Reflect the bridge tempo/meter/transport snapshot."""

        rhythm = self.store.rhythm
        if rhythm is None:
            return
        self._rhythm_sync = True
        try:
            self.bpm_spin.setValue(int(round(rhythm.bpm)))
            self.meter_num_spin.setValue(int(rhythm.meter_num))
            self.meter_den_combo.setCurrentText(str(rhythm.meter_den))
            self.transport_button.setChecked(bool(rhythm.running))
            self.transport_button.setText(
                "Stop" if rhythm.running else "Play"
            )
        finally:
            self._rhythm_sync = False

    def _sync_drum_controls(self) -> None:
        """Reflect drum enabled/port/rate/lane-mask state without echoing OSC."""

        drums = self.store.drums
        if drums is None:
            return
        available = bool(drums.port_ok)
        self._drum_sync = True
        try:
            self.drum_enable_checkbox.setEnabled(available)
            self.drum_enable_checkbox.setChecked(
                bool(available and drums.enabled)
            )
            self.drum_rate_combo.setEnabled(available)
            if drums.rate in DRUM_RATES:
                self.drum_rate_combo.setCurrentIndex(
                    DRUM_RATES.index(drums.rate)
                )
            states = drums.lane_states
            for note, checkbox in self.lane_checkboxes.items():
                checkbox.setEnabled(available)
                checkbox.setChecked(bool(available and states.get(note, False)))
        finally:
            self._drum_sync = False

    def _sync_loop_config(self, index: int) -> None:
        """Apply one ``/ji/loop/config`` snapshot to that loop's controls."""

        config = self.store.loop_configs.get(index)
        combo = self.loop_mode_combos.get(index)
        if config is None or combo is None:
            return
        self._loop_sync = True
        try:
            combo.setCurrentIndex(1 if config.mode == "beat" else 0)
            self._configure_loop_length(index, config.mode)
        finally:
            self._loop_sync = False
        self._refresh_loop_label(index)

    # -- status labels ---------------------------------------------------

    def _refresh_loop_label(self, index: int) -> None:
        label = self.loop_state_labels.get(index)
        if label is None:
            return
        state = self.store.loop_states.get(index, "waiting")
        config = self.store.loop_configs.get(
            index, osc_control.LoopConfig()
        )
        if config.mode == "beat":
            length = f"beat {config.beats:g}"
        else:
            length = f"free {config.seconds:g}s"
        label.setText(f"{state} | {length}")

    def _refresh_rhythm_labels(self) -> None:
        store = self.store
        if store.rhythm is None:
            self.rhythm_label.setText("Rhythm: waiting for bridge")
        else:
            rhythm = store.rhythm
            transport = "playing" if rhythm.running else "stopped"
            drums = "on" if rhythm.drums_enabled else "off"
            self.rhythm_label.setText(
                f"Rhythm: {rhythm.bpm:.1f} BPM | {rhythm.meter} | "
                f"transport {transport} | drums {drums} | "
                f"rate x{rhythm.drum_rate:g}"
            )

        if store.drums is None:
            self.drum_label.setText("Drums: waiting for bridge")
        elif not store.drums.port_ok:
            self.drum_label.setText(
                "Drums: disabled (drum port not available; see bridge log)"
            )
        elif not store.drums.enabled:
            self.drum_label.setText("Drums: off (port available)")
        else:
            self.drum_label.setText(
                f"Drums: on | rate x{store.drums.rate:g} | "
                f"lanes 0x{store.drums.lane_mask:04x}"
            )

    def _refresh_status_labels(self) -> None:
        store = self.store

        bridge_text = (
            "waiting for bridge" if store.waiting_for_bridge else "connected"
        )
        sc_text = "waiting for sclang" if store.waiting_for_sclang else "connected"
        self.bridge_status_label.setText(f"BRIDGE: {bridge_text}")
        self.sc_status_label.setText(f"SCLANG: {sc_text}")

        if store.freqset is None:
            self.freqset_label.setText("Set: waiting for bridge")
            self.anchor_label.setText("Anchor: waiting for bridge")
            self.fundamental_label.setText("Fundamental: waiting for bridge")
        else:
            freqset = store.freqset
            ratios = "  ".join(f"{ratio:.6f}" for ratio, _ in freqset.pairs)
            frequencies = "  ".join(f"{hz:8.2f}" for _, hz in freqset.pairs)
            self.freqset_label.setText(
                f"Set ({len(freqset.ratios)} classes)\n"
                f"ratios: {ratios}\n"
                f"Hz:     {frequencies}"
            )
            if freqset.anchor is None:
                self.anchor_label.setText("Anchor: none")
            else:
                anchor_hz = freqset.anchor * freqset.fundamental
                self.anchor_label.setText(
                    f"Anchor: {freqset.anchor:.6f} ({anchor_hz:.2f} Hz)"
                )
            self.fundamental_label.setText(
                f"Fundamental: {freqset.fundamental:.2f} Hz"
            )

        if store.control is None:
            self.control_label.setText("Controls: waiting for bridge")
        else:
            control = store.control
            self.control_label.setText(
                "Controls: "
                f"rand {control.randomness:.2f} | "
                f"diss {control.dissonance_bias:+.2f} | "
                f"step {control.stepwise_bias:.2f} | "
                f"temp {control.temperature:.2f} | "
                f"mobility {control.anchor_mobility:.2f} | "
                f"changes {control.change_count}/{control.max_changes} | "
                f"glide {control.glide_time:.3f} s"
            )

        if store.cpu is None:
            self.cpu_label.setText("CPU: waiting for sclang")
        else:
            self.cpu_label.setText(f"CPU: {store.cpu:.1f}%")

        if store.midi is None:
            self.midi_label.setText("MIDI: waiting for bridge")
        elif not store.midi.enabled:
            self.midi_label.setText("MIDI: disabled")
        else:
            state = store.midi_state
            drone_state = "on" if (state is None or state.drone_enabled) else "off"
            arp_state = "on" if (state is None or state.arp_enabled) else "off"
            self.midi_label.setText(
                f"MIDI: enabled | drone: {store.midi.drone_port or '?'} "
                f"({drone_state}) | arp: {store.midi.arp_port or '?'} "
                f"({arp_state})"
            )

        if store.waiting_for_sclang:
            self.loop_status_label.setText("Loop states: waiting for sclang")
        else:
            states = " | ".join(
                f"{index} {store.loop_states.get(index, '?')}"
                for index in range(1, 5)
            )
            self.loop_status_label.setText(f"Loop states: {states}")

        self._refresh_rhythm_labels()

        for index in range(1, 5):
            self._refresh_loop_label(index)

    def _set_message(self, text: str) -> None:
        self.message_label.setText(text)

    # -- close -----------------------------------------------------------

    def closeEvent(self, event: Any) -> None:  # noqa: N802 (Qt API)
        try:
            self._flush_pending()
        finally:
            self._send_timer.stop()
            self._quantize_timer.stop()
            stop = getattr(self.controller, "stop", None)
            if callable(stop):
                try:
                    stop()
                except Exception as exc:
                    print(f"[UI] controller stop failed: {exc}", file=sys.stderr)
            self._started = False
        event.accept()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Stage 7/8.2 performance GUI (PyQt6, one window)"
    )
    parser.add_argument("--bridge-host", default="127.0.0.1")
    parser.add_argument("--bridge-port", type=int, default=9000)
    parser.add_argument("--sc-host", default="127.0.0.1")
    parser.add_argument("--sc-port", type=int, default=57120)
    parser.add_argument("--ui-host", default="127.0.0.1")
    parser.add_argument("--ui-port", type=int, default=9001)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    for label, port in (
        ("--bridge-port", args.bridge_port),
        ("--sc-port", args.sc_port),
        ("--ui-port", args.ui_port),
    ):
        if not 1 <= port <= 65535:
            parser.error(f"{label} must be between 1 and 65535")

    app = QApplication(sys.argv[:1])
    app.setApplicationName("JI Station Stage 8.2")

    controller = OSCController(
        bridge_host=args.bridge_host,
        bridge_port=args.bridge_port,
        sc_host=args.sc_host,
        sc_port=args.sc_port,
        ui_host=args.ui_host,
        ui_port=args.ui_port,
    )
    window = MainWindow(controller, autostart=False)

    try:
        window.start_controller()
    except Exception as exc:
        message = f"Could not start the UI OSC listener:\n{exc}"
        print(f"[UI] {message}", file=sys.stderr)
        QMessageBox.critical(None, "JI Stage 8.2 UI", message)
        window.close()
        return 1

    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

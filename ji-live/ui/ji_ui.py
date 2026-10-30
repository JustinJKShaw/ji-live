#!/usr/bin/env python3
"""Stage 7 minimal performance GUI: one PyQt6 window.

Run from ``ji-live/`` after ``test_live_rig.scd`` and
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
from typing import Any, Callable

try:
    from PyQt6.QtCore import QObject, QSignalBlocker, Qt, QTimer, pyqtSignal
    from PyQt6.QtGui import QFont
    from PyQt6.QtWidgets import (
        QApplication,
        QCheckBox,
        QDial,
        QGridLayout,
        QGroupBox,
        QHBoxLayout,
        QLabel,
        QMainWindow,
        QMessageBox,
        QPushButton,
        QVBoxLayout,
        QWidget,
    )
except ImportError as exc:  # pragma: no cover - depends on the human's env
    raise SystemExit(
        "PyQt6 is required for the Stage 7 GUI.\n"
        "Install/run it with: uv run --with python-osc --with PyQt6 "
        "python ui/ji_ui.py\n"
        "See README_stage7.md for details."
    ) from exc

if __package__:  # ``python -m ui.ji_ui`` and normal package imports
    from . import osc_control
    from .osc_control import (
        AUTOTUNE_CHANNELS,
        AUTOTUNE_PARAMS,
        LOOP_LENGTH_DEFAULT,
        LOOP_LENGTH_MAX,
        LOOP_LENGTH_MIN,
        MUTATION_PARAMS,
        OSCController,
        StatusStore,
    )
else:  # pragma: no cover - direct ``python ui/ji_ui.py``
    import osc_control  # type: ignore[no-redef]
    from osc_control import (  # type: ignore[no-redef]
        AUTOTUNE_CHANNELS,
        AUTOTUNE_PARAMS,
        LOOP_LENGTH_DEFAULT,
        LOOP_LENGTH_MAX,
        LOOP_LENGTH_MIN,
        MUTATION_PARAMS,
        OSCController,
        StatusStore,
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
        self.dial.setNotchesVisible(True)
        self.dial.setFixedSize(44, 44)

        self.value_label = QLabel()
        self.value_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        self.value_label.setMinimumWidth(58)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 1, 2, 1)
        layout.setSpacing(1)
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
    """One-screen performance surface: Mutation, Live Autotune, Loops, Status."""

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

        self._relay = _StatusRelay()
        self._relay.message.connect(
            self._on_status_message, Qt.ConnectionType.QueuedConnection
        )
        self._wire_controller_callback()

        self.setWindowTitle("JI Station - Stage 7 Performance")
        self.resize(1180, 760)

        central = QWidget()
        outer = QVBoxLayout(central)
        outer.setContentsMargins(6, 6, 6, 6)
        outer.setSpacing(5)
        outer.addWidget(self._build_mutation_group())
        outer.addWidget(self._build_autotune_group())
        outer.addWidget(self._build_loops_group())
        outer.addWidget(self._build_status_group(), stretch=1)
        self.setCentralWidget(central)

        self._send_timer = QTimer(self)
        self._send_timer.setInterval(33)  # ~30 Hz coalesced knob sends
        self._send_timer.timeout.connect(self._flush_pending)
        self._send_timer.start()

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

        self._refresh_status_labels()

    # -- widget construction --------------------------------------------

    def _build_mutation_group(self) -> QGroupBox:
        group = QGroupBox("Mutation")
        layout = QVBoxLayout(group)
        layout.setSpacing(3)

        knob_row = QHBoxLayout()
        knob_row.setSpacing(2)
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
            knob_row.addWidget(knob)
        knob_row.addStretch(1)
        layout.addLayout(knob_row)

        button_row = QHBoxLayout()
        self.mutate_button = QPushButton("Mutate")
        self.octave_button = QPushButton("Octave")
        for button in (self.mutate_button, self.octave_button):
            button.setMinimumHeight(34)
            button.setMinimumWidth(110)
        self.mutate_button.clicked.connect(
            lambda: self._guard(self.controller.trigger_mutation)
        )
        self.octave_button.clicked.connect(
            lambda: self._guard(self.controller.trigger_octave)
        )
        button_row.addWidget(self.mutate_button)
        button_row.addWidget(self.octave_button)
        button_row.addStretch(1)
        layout.addLayout(button_row)
        return group

    def _build_autotune_group(self) -> QGroupBox:
        group = QGroupBox("Live Autotune")
        layout = QVBoxLayout(group)
        layout.setSpacing(3)

        top = QHBoxLayout()
        self.link_checkbox = QCheckBox("Link: move both inputs together")
        top.addWidget(self.link_checkbox)
        top.addStretch(1)
        layout.addLayout(top)

        channels = QHBoxLayout()
        channels.setSpacing(4)
        self.autotune_knobs: dict[tuple[int, str], Knob] = {}
        for channel in AUTOTUNE_CHANNELS:
            channel_group = QGroupBox(f"Input {channel}")
            grid = QGridLayout(channel_group)
            grid.setSpacing(2)
            for position, spec in enumerate(AUTOTUNE_PARAMS):
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
                grid.addWidget(knob, position // 3, position % 3)
            channels.addWidget(channel_group)
        layout.addLayout(channels)
        return group

    def _build_loops_group(self) -> QGroupBox:
        group = QGroupBox("Loops")
        layout = QVBoxLayout(group)
        layout.setSpacing(4)

        loop_row = QHBoxLayout()
        loop_row.setSpacing(4)
        self.loop_state_labels: dict[int, QLabel] = {}
        self.loop_buttons: dict[tuple[int, str], QPushButton] = {}

        for index in range(1, 5):
            loop_group = QGroupBox(f"Loop {index}")
            loop_layout = QVBoxLayout(loop_group)
            loop_layout.setSpacing(2)

            state_label = QLabel("State: waiting")
            state_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            self.loop_state_labels[index] = state_label
            loop_layout.addWidget(state_label)

            grid = QGridLayout()
            grid.setSpacing(2)
            # QPushButton.clicked emits a bool; bind it to ``_checked`` so it
            # cannot override the loop index default.
            actions = (
                (
                    "record",
                    "Record",
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
                    lambda _checked=False, i=index: self._guard(
                        self.controller.loop_play, i
                    ),
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
            positions = ((0, 0), (0, 1), (1, 0), (1, 1), (2, 0))
            for (name, text, slot), (row, column) in zip(actions, positions):
                button = QPushButton(text)
                button.setMinimumHeight(26)
                button.clicked.connect(slot)
                self.loop_buttons[(index, name)] = button
                if name == "clear":
                    grid.addWidget(button, row, column, 1, 2)
                else:
                    grid.addWidget(button, row, column)
            loop_layout.addLayout(grid)
            loop_row.addWidget(loop_group)

        layout.addLayout(loop_row)

        bottom = QHBoxLayout()
        self.stop_all_button = QPushButton("Stop All Loops")
        self.stop_all_button.setMinimumHeight(32)
        self.stop_all_button.setMinimumWidth(150)
        self.stop_all_button.clicked.connect(
            lambda: self._guard(self.controller.loop_stop_all)
        )
        self.length_knob = Knob(
            "Length",
            LOOP_LENGTH_MIN,
            LOOP_LENGTH_MAX,
            LOOP_LENGTH_DEFAULT,
            steps=int(round((LOOP_LENGTH_MAX - LOOP_LENGTH_MIN) * 10)),
            fmt="{:.1f}",
            suffix=" s",
        )
        self.length_knob.valueChanged.connect(self._on_length_knob)
        bottom.addWidget(self.stop_all_button)
        bottom.addSpacing(10)
        bottom.addWidget(self.length_knob)
        bottom.addStretch(1)
        layout.addLayout(bottom)
        return group

    def _build_status_group(self) -> QGroupBox:
        group = QGroupBox("Status")
        grid = QGridLayout(group)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(2)

        fixed = QFont("Monospace")
        fixed.setStyleHint(QFont.StyleHint.TypeWriter)
        fixed.setPointSize(9)

        self.bridge_status_label = QLabel("BRIDGE: waiting for bridge")
        self.sc_status_label = QLabel("SCLANG: waiting for sclang")
        self.freqset_label = QLabel("Set: waiting for bridge")
        self.anchor_label = QLabel("Anchor: waiting for bridge")
        self.fundamental_label = QLabel("Fundamental: waiting for bridge")
        self.control_label = QLabel("Controls: waiting for bridge")
        self.cpu_label = QLabel("CPU: waiting for sclang")
        self.midi_label = QLabel("MIDI: waiting for bridge")
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
            self.midi_label,
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
        grid.addWidget(self.freqset_label, 1, 0, 1, 2)
        grid.addWidget(self.anchor_label, 2, 0)
        grid.addWidget(self.fundamental_label, 2, 1)
        grid.addWidget(self.control_label, 3, 0, 1, 2)
        grid.addWidget(self.cpu_label, 4, 0)
        grid.addWidget(self.midi_label, 4, 1)
        grid.addWidget(self.loop_status_label, 5, 0, 1, 2)
        grid.addWidget(self.message_label, 6, 0, 1, 2)
        return group

    # -- knob queueing ---------------------------------------------------

    def _on_mutation_knob(self, key: str, value: float) -> None:
        self._pending[("mutation", key)] = value

    def _on_autotune_knob(self, channel: int, key: str, value: float) -> None:
        channels = AUTOTUNE_CHANNELS if self.link_checkbox.isChecked() else (channel,)
        for target in channels:
            self._pending[("autotune", target, key)] = value

    def _displayed_length(self) -> float:
        """The length shown by the knob (one decimal place, in seconds)."""

        return round(self.length_knob.value(), 1)

    def _on_length_knob(self, value: float) -> None:
        # The label shows one decimal; send exactly that value.
        self._pending[("length",)] = round(value, 1)

    def _record_loop(self, index: int) -> None:
        # Always send record_length so the take uses the length shown here,
        # even if the default-length message has not been applied yet.
        self._guard(self.controller.loop_record, index, self._displayed_length())

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

    # -- status labels ---------------------------------------------------

    def _refresh_loop_label(self, index: int) -> None:
        label = self.loop_state_labels.get(index)
        if label is None:
            return
        state = self.store.loop_states.get(index, "waiting")
        label.setText(f"State: {state}")

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
            self.midi_label.setText(
                f"MIDI: enabled | drone: {store.midi.drone_port or '?'} | "
                f"arp: {store.midi.arp_port or '?'}"
            )

        if store.waiting_for_sclang:
            self.loop_status_label.setText("Loop states: waiting for sclang")
        else:
            states = " | ".join(
                f"{index} {store.loop_states.get(index, '?')}"
                for index in range(1, 5)
            )
            self.loop_status_label.setText(f"Loop states: {states}")

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
        description="Stage 7 minimal performance GUI (PyQt6, one window)"
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
    app.setApplicationName("JI Station Stage 7")

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
        QMessageBox.critical(None, "JI Stage 7 UI", message)
        window.close()
        return 1

    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""One Python entry point for the JI Station rig: bridge + GUI.

This launcher is the single Python command the player runs.  It supervises the
two existing processes as children (it never merges the GUI event loop into the
bridge, and it never starts sclang)::

    cd ji-live
    uv run --with python-osc --with mido --with python-rtmidi --with PyQt6 \
      python run/ji_station.py --midi --drums --bass [--lead]

Behavior:

* The bridge starts first; the GUI starts only after the bridge process has
  stayed alive through a short readiness window.
* Closing the GUI keeps the bridge running (the current workflow); Ctrl+C or
  SIGTERM on this launcher stops both children cleanly and prints
  ``[LAUNCH] stopped``.
* If the bridge exits early, its exit code is printed and the GUI is stopped;
  a dead GUI is never left behind.
* ``--dry-run`` prints exactly what would start and exits 0 without importing
  PyQt6 or mido.  ``--check`` validates imports, config parsing, and the mido
  output names, then exits without starting anything.
* The direct commands (``python control/ji_bridge.py ...`` and
  ``python ui/ji_ui.py ...``) keep working unchanged.

See ``docs/START_HERE.md`` for the authoritative start/stop/flag reference.
"""

from __future__ import annotations

import argparse
import importlib.util
import math
import os
import shlex
import signal
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONTROL_DIR = ROOT / "control"
GUI_DIR = ROOT / "ui"
BRIDGE_SCRIPT = CONTROL_DIR / "ji_bridge.py"
GUI_SCRIPT = GUI_DIR / "ji_ui.py"

DEFAULT_GLIDE_TIME = 0.15
DEFAULT_LISTEN_HOST = "127.0.0.1"
DEFAULT_LISTEN_PORT = 9000
DEFAULT_UI_HOST = "127.0.0.1"
DEFAULT_UI_PORT = 9001
DEFAULT_SC_HOST = "127.0.0.1"
DEFAULT_SC_PORT = 57120

#: How long the bridge must stay alive before the GUI is started.
BRIDGE_READY_SECONDS = 1.0
#: How often the supervisor checks its children.
POLL_SECONDS = 0.2
#: Grace period between SIGTERM/SIGINT and SIGKILL on shutdown.
STOP_TIMEOUT_SECONDS = 5.0
#: Delay after the GUI starts before the launcher nudges one status resync.
GUI_RESYNC_DELAY = 1.0


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "JI Station one-command launcher: start the bridge and the PyQt6 "
            "GUI in one terminal (evaluate ji_start.scd in sclang first)."
        ),
        epilog=(
            "Evaluate ji_start.scd in sclang BEFORE running this command; this "
            "launcher never starts sclang.  See docs/START_HERE.md."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--midi",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="enable the Stage 6 MPE rig (drone Bus 1 + arp Bus 2)",
    )
    parser.add_argument(
        "--drums",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="enable the Stage 8.1 drum output (Bus 3; missing bus disables drums only)",
    )
    parser.add_argument(
        "--bass",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="enable the Stage 8.3a bass output (Bus 4; missing bus disables bass only)",
    )
    parser.add_argument(
        "--lead",
        action=argparse.BooleanOptionalAction,
        default=False,
        help=(
            "enable the Stage 8.3d lead/melody output (Bus 5; ships OFF and a "
            "missing bus disables the lead only)"
        ),
    )
    parser.add_argument(
        "--lead-config",
        default=None,
        help="lead config path (default: config/lead_config.json)",
    )
    parser.add_argument(
        "--drone-config",
        default=None,
        help=(
            "drone mode/pad config path (default: config/drone_config.json; "
            "the bus stays --mpe-config's drone_port)"
        ),
    )
    parser.add_argument(
        "--gui",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="start the PyQt6 GUI as a child process",
    )
    parser.add_argument(
        "--bridge",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="start the bridge as a child process",
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="forward stdin commands (m/o/c/t/a/g/s/q) to the bridge",
    )
    parser.add_argument(
        "--glide-time",
        type=float,
        default=DEFAULT_GLIDE_TIME,
        help="autotune glide carried by every mutation event, in seconds",
    )
    parser.add_argument(
        "--listen-port",
        type=int,
        default=DEFAULT_LISTEN_PORT,
        help="bridge UDP listen port (the GUI sends its controls here)",
    )
    parser.add_argument(
        "--ui-port",
        type=int,
        default=DEFAULT_UI_PORT,
        help="bridge -> GUI status port (the GUI listens here)",
    )
    parser.add_argument(
        "--sc-port",
        type=int,
        default=DEFAULT_SC_PORT,
        help="sclang language UDP port (printed by ji_start.scd)",
    )
    parser.add_argument(
        "--listen-host", default=DEFAULT_LISTEN_HOST, help="bridge listen host"
    )
    parser.add_argument(
        "--ui-host", default=DEFAULT_UI_HOST, help="bridge -> GUI status host"
    )
    parser.add_argument(
        "--sc-host", default=DEFAULT_SC_HOST, help="sclang language host"
    )
    parser.add_argument(
        "--mpe-config",
        default=None,
        help="MPE config path (default: config/mpe_config.json next to the project)",
    )
    parser.add_argument(
        "--rhythm-config",
        default=None,
        help="drum/rhythm config path (default: config/rhythm_config.json)",
    )
    parser.add_argument(
        "--bass-config",
        default=None,
        help="bass config path (default: config/bass_config.json)",
    )
    parser.add_argument(
        "--bridge-args",
        default="",
        metavar="ARGS",
        help=(
            "extra flags appended to the bridge command line, quoted: "
            "--bridge-args \"--change-count 2 --temperature 0.2\""
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the commands that would run, then exit 0",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help=(
            "validate imports, config parsing, and mido output names, then "
            "exit without starting anything"
        ),
    )
    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = build_parser()
    args = parser.parse_args(argv)
    for label, port in (
        ("--listen-port", args.listen_port),
        ("--ui-port", args.ui_port),
        ("--sc-port", args.sc_port),
    ):
        if not 1 <= port <= 65535:
            parser.error(f"{label} must be between 1 and 65535")
    if not math.isfinite(float(args.glide_time)) or float(args.glide_time) < 0.0:
        parser.error("--glide-time must be finite and non-negative")
    if not args.bridge and not args.gui:
        parser.error("--no-bridge --no-gui would start nothing")
    return args


# ---------------------------------------------------------------------------
# Command lines
# ---------------------------------------------------------------------------


def build_bridge_argv(args: argparse.Namespace) -> list[str]:
    """Return the exact bridge child command line."""

    argv = [
        sys.executable,
        str(BRIDGE_SCRIPT),
        "--sc-host",
        str(args.sc_host),
        "--sc-port",
        str(int(args.sc_port)),
        "--listen-host",
        str(args.listen_host),
        "--listen-port",
        str(int(args.listen_port)),
        "--ui-host",
        str(args.ui_host),
        "--ui-port",
        str(int(args.ui_port)),
        "--glide-time",
        f"{float(args.glide_time):g}",
    ]
    if args.midi:
        argv.append("--midi")
    if args.drums:
        argv.append("--drums")
    if args.bass:
        argv.append("--bass")
    if args.lead:
        argv.append("--lead")
    if args.interactive:
        argv.append("--interactive")
    if args.mpe_config:
        argv.extend(["--mpe-config", str(args.mpe_config)])
    if args.rhythm_config:
        argv.extend(["--rhythm-config", str(args.rhythm_config)])
    if args.bass_config:
        argv.extend(["--bass-config", str(args.bass_config)])
    if args.lead_config:
        argv.extend(["--lead-config", str(args.lead_config)])
    if args.drone_config:
        argv.extend(["--drone-config", str(args.drone_config)])
    argv.extend(shlex.split(str(args.bridge_args or "")))
    return argv


def build_gui_argv(args: argparse.Namespace) -> list[str]:
    """Return the exact GUI child command line."""

    return [
        sys.executable,
        str(GUI_SCRIPT),
        "--bridge-host",
        str(args.listen_host),
        "--bridge-port",
        str(int(args.listen_port)),
        "--sc-host",
        str(args.sc_host),
        "--sc-port",
        str(int(args.sc_port)),
        "--ui-host",
        str(args.ui_host),
        "--ui-port",
        str(int(args.ui_port)),
    ]


@dataclass
class LaunchPlan:
    """The child commands this launcher would start."""

    bridge: list[str] | None = None
    gui: list[str] | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def commands(self) -> list[list[str]]:
        return [cmd for cmd in (self.bridge, self.gui) if cmd is not None]


def build_plan(args: argparse.Namespace) -> LaunchPlan:
    """Build the launch plan from parsed arguments (pure; no processes)."""

    plan = LaunchPlan()
    if args.bridge:
        plan.bridge = build_bridge_argv(args)
    if args.gui:
        plan.gui = build_gui_argv(args)
    if not args.midi:
        plan.notes.append("MIDI off: drone/arp buses are not opened")
    if not args.drums:
        plan.notes.append("drums off: Bus 3 is not opened")
    if not args.bass:
        plan.notes.append("bass off: Bus 4 is not opened")
    if not args.lead:
        plan.notes.append("lead off: Bus 5 is not opened (default)")
    return plan


def format_command(argv: list[str]) -> str:
    """Shell-quotable one-line form of a child command."""

    return shlex.join([str(item) for item in argv])


def print_plan(plan: LaunchPlan) -> None:
    """Print the plan with the ``[LAUNCH]`` prefix used by the docs."""

    print(
        "[LAUNCH] sclang: evaluate ji_start.scd there FIRST; "
        "this launcher does not start sclang"
    )
    if plan.bridge is not None:
        print(f"[LAUNCH] bridge: {format_command(plan.bridge)}")
    else:
        print("[LAUNCH] bridge: not started (--no-bridge)")
    if plan.gui is not None:
        print(f"[LAUNCH] gui:    {format_command(plan.gui)}")
    else:
        print("[LAUNCH] gui:    not started (--no-gui)")
    for note in plan.notes:
        print(f"[LAUNCH] note: {note}")


# ---------------------------------------------------------------------------
# --check
# ---------------------------------------------------------------------------


def run_checks(args: argparse.Namespace) -> int:
    """Validate imports, config parsing, and mido output names."""

    problems: list[str] = []
    print("[CHECK] JI Station launcher checks")

    def need(module: str, why: str) -> None:
        if importlib.util.find_spec(module) is None:
            problems.append(f"missing Python package {module!r} ({why})")

    need("pythonosc", "the bridge and the GUI both need python-osc")
    if args.midi or args.drums or args.bass or args.lead:
        need("mido", "MIDI output needs mido")
        need("rtmidi", "MIDI output needs python-rtmidi")
    if args.gui:
        need("PyQt6", "the GUI needs PyQt6")

    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

    ports: list[str] = []
    try:
        from control.midi_rig import load_config as load_mpe_config

        mpe = load_mpe_config(args.mpe_config) if args.mpe_config else load_mpe_config()
        ports = [mpe.drone_port, mpe.arp_port]
        print(
            f"[CHECK] mpe config ok: drone={mpe.drone_port!r} "
            f"arp={mpe.arp_port!r} member_channel={mpe.member_channel} "
            f"bend_range={mpe.bend_range_semitones:g} "
            f"step={mpe.step_seconds:.3f} gate={mpe.gate_seconds:.3f} "
            f"order={mpe.arp_order} sync={mpe.arp_sync}"
            f"/{mpe.arp_division} evolve={int(mpe.arp_evolve)} "
            f"seed={mpe.arp_seed}"
        )
    except (OSError, TypeError, ValueError) as exc:
        problems.append(f"MPE config: {exc}")

    try:
        from control.drums import load_config as load_drum_config

        drums = (
            load_drum_config(args.rhythm_config)
            if args.rhythm_config
            else load_drum_config()
        )
        ports.append(drums.port)
        print(
            f"[CHECK] rhythm config ok: drums={drums.port!r} "
            f"bpm={drums.bpm:g} meter={drums.meter_num}/{drums.meter_den}"
        )
    except (OSError, TypeError, ValueError) as exc:
        problems.append(f"rhythm config: {exc}")

    try:
        from control.bass import load_config as load_bass_config

        bass = (
            load_bass_config(args.bass_config)
            if args.bass_config
            else load_bass_config()
        )
        ports.append(bass.port)
        print(
            f"[CHECK] bass config ok: bass={bass.port!r} "
            f"main_channel={bass.channel} sub_channel={bass.sub_channel} "
            f"bend_range={bass.bend_range_semitones:g}"
        )
    except (OSError, TypeError, ValueError) as exc:
        problems.append(f"bass config: {exc}")

    try:
        from control.lead import load_config as load_lead_config

        lead = (
            load_lead_config(args.lead_config)
            if args.lead_config
            else load_lead_config()
        )
        print(
            f"[CHECK] lead config ok: lead={lead.port!r} "
            f"channel={lead.member_channel} "
            f"bend_range={lead.bend_range_semitones:g} "
            f"grid={lead.settings.grid} bars={lead.settings.phrase_bars} "
            f"seed={lead.settings.seed}"
        )
        if args.lead:
            ports.append(lead.port)
    except (OSError, TypeError, ValueError) as exc:
        problems.append(f"lead config: {exc}")

    try:
        from control.drone import load_config as load_drone_config

        drone = (
            load_drone_config(args.drone_config)
            if args.drone_config
            else load_drone_config()
        )
        print(
            f"[CHECK] drone config ok: mode={drone.settings.mode} "
            f"velocity={drone.settings.velocity} "
            f"pulse={drone.settings.pulse_notes}x{drone.settings.pulse_grid} "
            f"seq={drone.settings.seq_notes}x{drone.settings.seq_bars}bar "
            f"hold={drone.settings.hold_beats:g} b "
            f"slide={drone.settings.slide_seconds:g} s"
        )
    except (OSError, TypeError, ValueError) as exc:
        problems.append(f"drone config: {exc}")

    if importlib.util.find_spec("mido") is not None:
        try:
            import mido  # noqa: PLC0415 - only --check imports mido

            available = list(mido.get_output_names())
            print(f"[CHECK] mido output names: {available!r}")
            for name in ports:
                if name and name not in available:
                    problems.append(
                        f"mido output {name!r} is not available; create the "
                        "IAC Driver bus or fix the config"
                    )
        except Exception as exc:  # mido/rtmidi backends vary
            problems.append(f"mido output enumeration failed: {exc!r}")
    else:
        print("[CHECK] mido output names not checked (mido is not installed)")

    print(
        f"[CHECK] target ports: listen={args.listen_host}:{args.listen_port} "
        f"ui={args.ui_host}:{args.ui_port} sc={args.sc_host}:{args.sc_port}"
    )
    if problems:
        for problem in problems:
            print(f"[CHECK] FAIL: {problem}", file=sys.stderr)
        print(f"[CHECK] {len(problems)} problem(s) found", file=sys.stderr)
        return 1
    print("[CHECK] OK: imports, configs, and MIDI output names look usable")
    return 0


# ---------------------------------------------------------------------------
# Supervision
# ---------------------------------------------------------------------------


def _osc_packet(address: str, *args: int) -> bytes:
    """Encode a minimal OSC message (ints only) without python-osc."""

    def padded(text: str) -> bytes:
        raw = text.encode("utf-8") + b"\0"
        return raw + b"\0" * ((4 - len(raw) % 4) % 4)

    tags = "," + "i" * len(args)
    payload = b"".join(int(value).to_bytes(4, "big", signed=True) for value in args)
    return padded(address) + padded(tags) + payload


def _nudge_ui_resync(host: str, port: int) -> None:
    """Best-effort ``/ji/ui/hello`` so a late bridge still fills the GUI."""

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.sendto(_osc_packet("/ji/ui/hello"), (str(host), int(port)))
    except OSError as exc:  # pragma: no cover - best effort
        print(f"[LAUNCH] ui resync nudge failed: {exc}", file=sys.stderr)


def _terminate(proc: subprocess.Popen, name: str, log=print) -> None:
    """Ask a child to stop, then kill it if it does not."""

    if proc is None or proc.poll() is not None:
        return
    log(f"[LAUNCH] stopping {name} (pid {proc.pid})")
    try:
        proc.terminate()
    except OSError as exc:  # pragma: no cover - process already gone
        log(f"[LAUNCH] {name} terminate failed: {exc}")
        return
    try:
        proc.wait(timeout=STOP_TIMEOUT_SECONDS)
        return
    except subprocess.TimeoutExpired:
        log(f"[LAUNCH] {name} did not stop in {STOP_TIMEOUT_SECONDS:g} s; killing")
    try:
        proc.kill()
        proc.wait(timeout=STOP_TIMEOUT_SECONDS)
    except (OSError, subprocess.TimeoutExpired) as exc:  # pragma: no cover
        log(f"[LAUNCH] {name} kill failed: {exc}")


def _wait_for_bridge(
    proc: subprocess.Popen, timeout: float = BRIDGE_READY_SECONDS
) -> bool:
    """Return True when the bridge is still alive after the readiness window."""

    deadline = time.monotonic() + max(0.0, timeout)
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return False
        time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
    return proc.poll() is None


def run_plan(plan: LaunchPlan, args: argparse.Namespace | None = None) -> int:
    """Start the children, supervise them, and return the process exit code."""

    env = dict(os.environ)
    bridge: subprocess.Popen | None = None
    gui: subprocess.Popen | None = None
    stopping = threading.Event()
    resync_at: float | None = None
    resync_host = DEFAULT_LISTEN_HOST
    resync_port = DEFAULT_LISTEN_PORT
    if args is not None:
        resync_host = str(args.listen_host)
        resync_port = int(args.listen_port)

    def _signal_handler(signum, _frame):  # pragma: no cover - signal path
        print(f"\n[LAUNCH] signal {signum} received; stopping children")
        stopping.set()

    previous = {}
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            previous[sig] = signal.signal(sig, _signal_handler)
        except (ValueError, OSError):  # non-main thread
            pass

    exit_code = 0
    try:
        if plan.bridge is not None:
            bridge = subprocess.Popen(
                plan.bridge,
                cwd=str(ROOT),
                env=env,
                stdin=None if args is None or args.interactive else subprocess.DEVNULL,
            )
            if not _wait_for_bridge(bridge):
                code = bridge.poll()
                print(
                    f"[LAUNCH] bridge exited early with code {code}; "
                    "not starting the GUI",
                    file=sys.stderr,
                )
                print(
                    "[LAUNCH] re-run the printed bridge command directly to see "
                    "its error",
                    file=sys.stderr,
                )
                bridge = None
                return 1 if code is None else int(code)

        if plan.gui is not None:
            gui = subprocess.Popen(plan.gui, cwd=str(ROOT), env=env)
            resync_at = time.monotonic() + GUI_RESYNC_DELAY
            print("[LAUNCH] gui started; closing it keeps the bridge running")

        print("[LAUNCH] running; press Ctrl+C to stop")

        while not stopping.is_set():
            if (
                resync_at is not None
                and time.monotonic() >= resync_at
                and bridge is not None
                and bridge.poll() is None
            ):
                _nudge_ui_resync(resync_host, resync_port)
                resync_at = None

            if bridge is not None and bridge.poll() is not None:
                code = bridge.returncode
                print(
                    f"[LAUNCH] bridge exited with code {code}; stopping the GUI",
                    file=sys.stderr,
                )
                exit_code = 0 if not code else int(code)
                break

            if gui is not None and gui.poll() is not None:
                code = gui.returncode
                if code:
                    print(
                        f"[LAUNCH] gui exited with code {code}; the bridge keeps "
                        "running (Ctrl+C to stop)",
                        file=sys.stderr,
                    )
                else:
                    print(
                        "[LAUNCH] gui closed; the bridge keeps running "
                        "(Ctrl+C to stop)"
                    )
                gui = None
                if bridge is None:
                    break

            if bridge is None and gui is None:
                break
            time.sleep(POLL_SECONDS)
    except KeyboardInterrupt:  # pragma: no cover - interactive path
        print()
    finally:
        _terminate(gui, "gui")
        _terminate(bridge, "bridge")
        for sig, handler in previous.items():
            try:
                signal.signal(sig, handler)
            except (ValueError, OSError):  # pragma: no cover
                pass
        print("[LAUNCH] stopped")
    return exit_code


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    plan = build_plan(args)

    if args.dry_run:
        print_plan(plan)
        print("[LAUNCH] dry run: nothing started")
        return 0

    if args.check:
        return run_checks(args)

    print_plan(plan)
    if plan.bridge is None and plan.gui is None:
        print("[LAUNCH] nothing to start", file=sys.stderr)
        return 2
    return run_plan(plan, args)


if __name__ == "__main__":
    raise SystemExit(main())

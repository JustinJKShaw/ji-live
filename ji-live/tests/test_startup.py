#!/usr/bin/env python3
"""Stage 8.3d startup tests: launcher argv, ji_start.scd order, docs.

Run from ``ji-live/``::

    uv run --with python-osc python tests/test_startup.py

No subprocesses, no sockets, no MIDI ports: every check is a pure argv/plan or a
text check on the start files and docs.
"""

from __future__ import annotations

import contextlib
import io
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from run import ji_station
from run.ji_station import (
    BRIDGE_SCRIPT,
    GUI_SCRIPT,
    build_bridge_argv,
    build_gui_argv,
    build_plan,
    format_command,
    main,
    parse_args,
)

START_SCD = ROOT / "ji_start.scd"
START_HERE = ROOT / "docs" / "START_HERE.md"
STAGE_DOC = ROOT / "docs" / "README_stage8_3d.md"
OLD_STAGE_DOC = ROOT / "docs" / "README_stage8_3b.md"
README = ROOT / "README.md"


def _dry_run(argv: list[str]) -> tuple[int, str]:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = main(argv + ["--dry-run"])
    return code, out.getvalue()


def _option(argv: list[str], flag: str) -> str | None:
    """Return the value after ``flag`` in an argv list."""

    if flag not in argv:
        return None
    index = argv.index(flag)
    if index + 1 >= len(argv):
        return None
    return argv[index + 1]


def test_default_plan() -> None:
    print("\n=== Test 1: default plan is midi + drums + bass + gui + bridge ===")
    args = parse_args([])
    assert args.midi and args.drums and args.bass
    assert args.lead is False, "--lead ships off"
    assert args.gui and args.bridge
    assert args.glide_time == 0.15
    assert args.listen_port == 9000 and args.ui_port == 9001
    assert args.sc_port == 57120

    plan = build_plan(args)
    assert plan.bridge is not None and plan.gui is not None
    assert plan.bridge[0] == sys.executable
    assert plan.bridge[1] == str(BRIDGE_SCRIPT)
    assert plan.gui[1] == str(GUI_SCRIPT)
    for flag in ("--midi", "--drums", "--bass"):
        assert flag in plan.bridge, plan.bridge
    assert "--lead" not in plan.bridge, "--lead must ship off"
    assert "--interactive" not in plan.bridge
    # Ports are passed to both children and therefore always agree.
    assert _option(plan.bridge, "--listen-port") == "9000"
    assert _option(plan.gui, "--bridge-port") == "9000"
    assert _option(plan.bridge, "--ui-port") == "9001"
    assert _option(plan.gui, "--ui-port") == "9001"
    assert _option(plan.bridge, "--sc-port") == "57120"
    assert _option(plan.gui, "--sc-port") == "57120"
    assert plan.notes == ["lead off: Bus 5 is not opened (default)"]
    print("  bridge and gui share listen/ui/sc ports; lead off; no --interactive")


def test_no_flags() -> None:
    print("\n=== Test 2: --no-* flags remove flags and children ===")
    args = parse_args(["--no-midi", "--no-drums", "--no-bass", "--no-lead"])
    bridge = build_bridge_argv(args)
    for flag in ("--midi", "--drums", "--bass", "--lead"):
        assert flag not in bridge, bridge
    plan = build_plan(args)
    assert len(plan.notes) == 4, plan.notes
    assert any("lead off" in note for note in plan.notes)

    no_gui = build_plan(parse_args(["--no-gui"]))
    assert no_gui.gui is None and no_gui.bridge is not None
    no_bridge = build_plan(parse_args(["--no-bridge"]))
    assert no_bridge.bridge is None and no_bridge.gui is not None
    # Neither child at all is a user error, not a silent exit.
    try:
        parse_args(["--no-bridge", "--no-gui"])
    except SystemExit:
        pass
    else:
        raise AssertionError("--no-bridge --no-gui must be rejected")

    # --interactive forwards to the bridge only.
    interactive = parse_args(["--interactive"])
    assert "--interactive" in build_bridge_argv(interactive)
    assert "--interactive" not in build_gui_argv(interactive)
    # --lead is opt-in and forwards --lead plus both 8.3d config paths.
    lead_args = parse_args(
        ["--lead", "--lead-config", "cfg/lead.json",
         "--drone-config", "cfg/drone.json"]
    )
    lead_bridge = build_bridge_argv(lead_args)
    assert "--lead" in lead_bridge, lead_bridge
    assert _option(lead_bridge, "--lead-config") == "cfg/lead.json"
    assert _option(lead_bridge, "--drone-config") == "cfg/drone.json"
    assert build_plan(lead_args).notes == []
    print(
        "  --no-midi/--no-drums/--no-bass/--no-lead/--no-gui/--no-bridge, "
        "--interactive, --lead"
    )


def test_extra_args() -> None:
    print("\n=== Test 3: --bridge-args, configs, glide, ports ===")
    args = parse_args(
        [
            "--glide-time",
            "0.3",
            "--listen-port",
            "9100",
            "--ui-port",
            "9101",
            "--sc-port",
            "57130",
            "--mpe-config",
            "cfg/mpe.json",
            "--rhythm-config",
            "cfg/rhythm.json",
            "--bass-config",
            "cfg/bass.json",
            "--bridge-args",
            "--change-count 2 --temperature 0.2",
        ]
    )
    bridge = build_bridge_argv(args)
    assert _option(bridge, "--glide-time") == "0.3"
    assert _option(bridge, "--listen-port") == "9100"
    assert _option(bridge, "--ui-port") == "9101"
    assert _option(bridge, "--sc-port") == "57130"
    assert _option(bridge, "--mpe-config") == "cfg/mpe.json"
    assert _option(bridge, "--rhythm-config") == "cfg/rhythm.json"
    assert _option(bridge, "--bass-config") == "cfg/bass.json"
    # --bridge-args is split, not passed as one token.
    assert "--change-count" in bridge and "2" in bridge
    assert "--temperature" in bridge and "0.2" in bridge
    gui = build_gui_argv(args)
    assert _option(gui, "--bridge-port") == "9100"
    assert _option(gui, "--ui-port") == "9101"

    # Bad ports and glide values are rejected by argparse.
    for bad in (
        ["--listen-port", "0"],
        ["--ui-port", "70000"],
        ["--sc-port", "-1"],
        ["--glide-time", "-0.1"],
    ):
        try:
            parse_args(bad)
        except SystemExit:
            pass
        else:
            raise AssertionError(f"invalid argument accepted: {bad}")
    print("  quoted --bridge-args, config paths, and port/glide validation")


def test_dry_run() -> None:
    print("\n=== Test 4: --dry-run prints the plan and starts nothing ===")
    assert "PyQt6" not in sys.modules
    assert "mido" not in sys.modules
    code, text = _dry_run([])
    assert code == 0, code
    assert "[LAUNCH] bridge:" in text, text
    assert "[LAUNCH] gui:" in text, text
    assert "ji_start.scd" in text, text
    assert "nothing started" in text, text
    # The printed lines must be reproducible shell commands.
    for line in text.splitlines():
        if line.startswith("[LAUNCH] bridge: "):
            assert "ji_bridge.py" in line
        if line.startswith("[LAUNCH] gui: "):
            assert "ji_ui.py" in line

    code, text = _dry_run(["--no-midi", "--no-drums", "--no-bass", "--no-gui"])
    assert code == 0
    assert "not started (--no-gui)" in text
    assert "--midi" not in text.split("[LAUNCH] gui:")[0]
    assert "MIDI off" in text and "drums off" in text and "bass off" in text
    # --dry-run must never import the optional GUI/MIDI stacks.
    assert "PyQt6" not in sys.modules and "mido" not in sys.modules
    print("  dry run prints exact commands, exits 0, imports no PyQt6/mido")


def test_check_mode_is_side_effect_free() -> None:
    print("\n=== Test 5: --check reports without starting anything ===")
    out = io.StringIO()
    err = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(["--check"])
    text = out.getvalue() + err.getvalue()
    assert code in (0, 1), code
    assert "[CHECK]" in text, text
    assert "mpe config ok" in text, text
    assert "rhythm config ok" in text, text
    assert "bass config ok" in text, text
    assert "lead config ok" in text, text
    assert "drone config ok" in text, text
    assert "target ports" in text, text
    print(f"  --check exit={code}; config parsing exercised without children")


def test_format_command() -> None:
    print("\n=== Test 6: [LAUNCH] command formatting is shell-safe ===")
    printed = format_command(["/usr/bin/python", "/a b/c.py", "--flag", "x y"])
    assert "'/a b/c.py'" in printed, printed
    assert "'x y'" in printed, printed
    assert printed.startswith("/usr/bin/python ")
    print("  spaces are quoted so the command can be pasted back")


def _positions(text: str, needles: list[str]) -> list[int]:
    positions = []
    for needle in needles:
        index = text.find(needle)
        assert index >= 0, f"missing {needle!r}"
        positions.append(index)
    return positions


def test_ji_start_scd() -> None:
    print("\n=== Test 7: ji_start.scd loads the rig files in order ===")
    assert START_SCD.exists(), START_SCD
    text = START_SCD.read_text(encoding="utf-8")

    # The three loads must appear in dependency order.
    startup, live, surface = _positions(
        text,
        [
            "startupPath  = ~ji_start_require.",
            "liveRigPath  = ~ji_start_require.",
            "surfacePath  = ~ji_start_require.",
        ],
    )
    assert startup < live < surface, (startup, live, surface)
    load_startup = text.find("startupPath.load")
    load_live = text.find("liveRigPath.load")
    load_surface = text.find("surfacePath.load")
    assert -1 < load_startup < load_live < load_surface, (
        load_startup,
        load_live,
        load_surface,
    )

    # It must resolve the project root, sync, and print one ready line.
    assert "thisProcess.nowExecutingPath" in text
    assert "s.sync" in text
    assert "[JI] sclang rig + control surface ready" in text
    assert "ji_start.scd" in text
    assert "run/ji_station.py" in text
    assert "~ji_start_routine !?" in text, "re-evaluation must stop the old waiter"
    assert "PathName(path).parentPath" in text
    # It must not fork the audio code: no SynthDef or Synth definitions.
    for forbidden in ("SynthDef(", "Synth(", "OSCdef("):
        assert forbidden not in text, f"ji_start.scd must not define {forbidden}"
    # Named prerequisites so a missing file cannot pass silently.
    for needed in ("startup.scd", "tests/sc/test_live_rig.scd",
                   "ui/control_surface.scd", "run/ji_station.py"):
        assert needed in text, needed
    print("  root capture, startup -> live rig -> control surface, [JI] ready line")


def test_start_here_docs() -> None:
    print("\n=== Test 8: docs/START_HERE.md is complete and current ===")
    assert START_HERE.exists(), START_HERE
    text = START_HERE.read_text(encoding="utf-8")
    assert "ji_start.scd" in text
    assert "run/ji_station.py" in text
    assert "~ji_stop_all.();" in text
    for flag in (
        "--midi",
        "--no-midi",
        "--drums",
        "--no-drums",
        "--bass",
        "--no-bass",
        "--no-gui",
        "--no-bridge",
        "--interactive",
        "--glide-time",
        "--listen-port",
        "--ui-port",
        "--bridge-args",
        "--dry-run",
        "--check",
        "--mpe-config",
        "--rhythm-config",
        "--bass-config",
        "--lead",
        "--no-lead",
        "--lead-config",
        "--drone-config",
    ):
        assert flag in text, f"START_HERE.md must document {flag}"
    # One-time setup facts the owner asked for.
    for fact in (
        "IAC Driver Bus 1",
        "IAC Driver Bus 2",
        "IAC Driver Bus 3",
        "IAC Driver Bus 4",
        "IAC Driver Bus 5",
        "48 semitones",
        "2 semitones",
        "BlackHole 16ch",
        "MiniFuse 2",
        "48000",
        "Drone",
        "Arp",
        "Drums",
        "Bass",
        "Sub",
        "Lead",
    ):
        assert fact in text, f"START_HERE.md must mention {fact}"
    assert "source of truth" in text
    assert "START_HERE_stage8_1.txt" in text, "archival note missing"
    print("  commands, every flag, setup, stop, troubleshooting, maintenance rule")


def test_readme_links() -> None:
    print("\n=== Test 9: README.md links the canonical start sheet ===")
    assert README.exists(), README
    text = README.read_text(encoding="utf-8")
    assert "docs/START_HERE.md" in text, "README must link docs/START_HERE.md"
    assert "ji_start.scd" in text
    assert "run/ji_station.py" in text
    assert "docs/README_stage8_3d.md" in text
    assert STAGE_DOC.exists(), STAGE_DOC
    assert OLD_STAGE_DOC.exists(), OLD_STAGE_DOC
    stage = STAGE_DOC.read_text(encoding="utf-8")
    for heading in (
        "Human acceptance checklist",
        "Lead",
        "Drone",
        "consistency",
    ):
        assert heading in stage, heading
    print("  README links START_HERE + 8.3d; stage doc has the checklist")


def run() -> None:
    print("JI Stage 8.3d startup tests")
    test_default_plan()
    test_no_flags()
    test_extra_args()
    test_dry_run()
    test_check_mode_is_side_effect_free()
    test_format_command()
    test_ji_start_scd()
    test_start_here_docs()
    test_readme_links()
    print("\nALL STARTUP TESTS PASSED")


if __name__ == "__main__":
    try:
        run()
    except Exception:
        traceback.print_exc()
        print("\nSTARTUP TESTS FAILED")
        raise SystemExit(1)

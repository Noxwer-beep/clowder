"""Clowder — one front door for the four tools.

    clowder                     the dashboard
    clowder run warden check    run a tool through the same door
    clowder web                 the same dashboard in a browser
    clowder doctor              what is installed, where does it keep state

Exit codes: 0 ok · 1 error · 2 usage · 3 tool missing
            · 4 attention at or above --fail-on
"""

from __future__ import annotations

import argparse
import subprocess
import sys

from . import __version__, config, reads, ui
from .config import ATTENTION_RANK

FAIL_ON = ("critical", "fail", "high", "warn", "medium", "low", "info")


def cmd_status(args: argparse.Namespace) -> int:
    lab = reads.read_all()
    if args.json:
        ui.emit_json(lab)
    else:
        ui.banner()
        ui.tools_table()
        ui.attention_block(lab)
        ui.scan_block(lab)
        ui.health_block(lab)
        ui.cases_block(lab)
        ui.hint("whisker reads a capture on demand — clowder read <file.pcap>")

    if args.fail_on:
        limit = ATTENTION_RANK[args.fail_on]
        worst = min((ATTENTION_RANK.get(state, 9) for state, _, _ in lab.attention()),
                    default=9)
        if worst <= limit:
            return 4
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    lab = reads.read_all()
    if args.json:
        ui.emit_json(lab)
    else:
        ui.doctor(lab)
    return 0 if all(tool.installed for tool in config.TOOLS) else 3


def cmd_run(args: argparse.Namespace) -> int:
    """Hand the rest of the line to the tool itself — same argv, same exit code."""
    tool = config.BY_KEY.get(args.tool)
    if tool is None:
        ui.fail(f"unknown tool: {args.tool} (try {', '.join(config.BY_KEY)})")
        return 2
    command = tool.command()
    if command is None:
        ui.fail(f"{tool.key} is not installed and no launcher was found at "
                f"{tool.directory() / tool.launcher}")
        return 3
    argv = [str(command), *args.rest]
    try:
        completed = subprocess.run(argv, cwd=str(tool.directory()))
    except KeyboardInterrupt:
        return 130
    except OSError as exc:
        ui.fail(f"could not run {tool.key}: {exc}")
        return 1
    return completed.returncode


def cmd_web(args: argparse.Namespace) -> int:
    try:
        from .web.app import serve
    except ModuleNotFoundError:
        ui.fail("the dashboard needs flask:  pip install flask")
        return 3
    serve(host=args.host, port=args.port, refresh=args.refresh)
    return 0


def cmd_cat(args: argparse.Namespace) -> int:
    ui.cat()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="clowder",
        description="One front door for the lab: vantage, warden, bastet, whisker.",
    )
    parser.add_argument("--version", action="version", version=f"clowder {__version__}")
    sub = parser.add_subparsers(dest="command")

    status = sub.add_parser("status", help="the dashboard (default)")
    status.add_argument("--json", action="store_true")
    status.add_argument("--fail-on", choices=FAIL_ON)
    status.set_defaults(func=cmd_status)

    doctor = sub.add_parser("doctor", help="what is installed and where is its data")
    doctor.add_argument("--json", action="store_true")
    doctor.set_defaults(func=cmd_doctor)

    run = sub.add_parser("run", help="run one of the tools")
    run.add_argument("tool", help="vantage · warden · bastet · whisker")
    run.add_argument("rest", nargs=argparse.REMAINDER,
                     help="everything after this goes to the tool")
    run.set_defaults(func=cmd_run)

    web = sub.add_parser("web", help="the dashboard in a browser")
    web.add_argument("--host", default="127.0.0.1")
    web.add_argument("--port", type=int, default=8780)
    web.add_argument("--refresh", type=int, default=30, help="seconds between rereads")
    web.set_defaults(func=cmd_web)

    cat = sub.add_parser("cat", help="the cat")
    cat.set_defaults(func=cmd_cat)

    # shortcuts: clowder scan … == clowder run vantage scan …
    for tool in ("vantage", "warden", "bastet", "whisker"):
        shortcut = sub.add_parser(tool, help=f"run {tool}")
        shortcut.add_argument("rest", nargs=argparse.REMAINDER)
        shortcut.set_defaults(func=cmd_run, tool_name=tool)

    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()

    # a shortcut subparser carries tool_name instead of tool
    if argv and argv[0] in config.BY_KEY:
        args = parser.parse_args(argv)
        args.tool = args.tool_name
        return args.func(args)

    if not argv:
        argv = ["status"]
    args = parser.parse_args(argv)
    func = getattr(args, "func", None)
    if func is None:
        parser.print_help()
        return 0
    try:
        return func(args)
    except BrokenPipeError:
        return 0
    except KeyboardInterrupt:
        return 130

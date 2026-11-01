"""Terminal rendering for the console."""

from __future__ import annotations

import json
import sys

from pathlib import Path

from . import __version__
from .config import ACCENT, SEVERITY_STYLE, TOOLS
from .reads import Lab, age

try:
    from rich import box
    from rich.console import Console
    from rich.table import Table
    from rich.text import Text

    RICH = True
except ModuleNotFoundError:  # pragma: no cover
    RICH = False

    class _Fake:
        def print(self, *args, **kwargs):
            print(*[str(a) for a in args])

    Console = _Fake  # type: ignore

console = Console(width=None if sys.stdout.isatty() else 118)
err_console = Console(stderr=True)

CAT = [
    r"   /\_/\      ",
    r"  ( o.o )     ",
    r"   > ^ <      ",
]

STATUS_TEXT = {
    "ok": Text("ok", style="#8bd67a"),
    "warn": Text("warn", style="#f0c45a"),
    "fail": Text("fail", style="bold #ff5c5c"),
    "unknown": Text("unknown", style="dim"),
    "never": Text("never", style="dim"),
}


def severity(value: str):
    if not RICH:
        return value.upper()
    return Text(value.upper(), style=SEVERITY_STYLE.get(value, "dim"))


def status(value: str):
    if not RICH:
        return value
    return STATUS_TEXT.get(value, Text(value, style="dim"))


def banner() -> None:
    if not RICH:
        console.print("clowder — one front door for the lab")
        return
    mark = Text()
    for index, line in enumerate(CAT):
        if index:
            mark.append("\n")
        mark.append(line, style=ACCENT)
    side = Text()
    side.append("CLOWDER", style="bold")
    side.append(f"  {__version__}\n", style="dim")
    side.append("a group of cats is a clowder\n", style="dim")
    side.append("vantage · warden · bastet · whisker", style="dim")
    grid = Table.grid(padding=(0, 3))
    grid.add_row(mark, side)
    console.print()
    console.print(grid)
    console.print()


def head(title: str, subtitle: str = "") -> None:
    if RICH:
        text = Text(title.upper(), style="bold")
        if subtitle:
            text.append(f"  {subtitle}", style="dim")
        console.print(text)
    else:
        console.print(f"{title.upper()}  {subtitle}")
    console.print()


def tools_table() -> None:
    """What is installed, where its data is, and when it last did something."""
    table = Table(box=box.SIMPLE_HEAD, show_edge=False, pad_edge=False)
    for heading in ("TOOL", "DOES", "STATE", "DATA", ""):
        table.add_column(heading, no_wrap=heading in ("TOOL", "STATE"), overflow="fold")
    for tool in TOOLS:
        if tool.installed:
            state = status("ok")
            where = str(tool.data_dir())
        else:
            state = status("missing")
            where = str(tool.directory())
        table.add_row(tool.key, tool.title, state, _short(where))
    console.print(table)
    console.print()


def scan_block(lab: Lab) -> None:
    scan = lab.scan
    head("vantage", "last scan")
    if scan is None:
        console.print("  [dim]nothing scanned yet — clowder run vantage scan <target>[/]"
                      if RICH else "  nothing scanned yet")
        console.print()
        return
    counts = scan.counts
    spread = "  ".join(
        f"{counts.get(sev, 0)} {sev}" for sev in ("critical", "high", "medium", "low", "info")
        if counts.get(sev)
    ) or "no findings"
    lines = [
        ("target", f"{scan.target}  ·  {scan.strategy}  ·  {scan.hosts} host(s), {scan.ports} open port(s)"),
        ("when", f"{scan.started}  ({age(scan.started)})  ·  ran {scan.duration_s / 60:.1f} min"),
        ("findings", f"{scan.total} — {spread}   ({scan.confirmed} confirmed)"),
        ("risk", f"{scan.score}/100  ·  grade {scan.grade}"),
        ("report", scan.path),
    ]
    _kv(lines)


def health_block(lab: Lab) -> None:
    health = lab.health
    head("warden", "last check")
    if health is None:
        console.print("  [dim]nothing checked yet — clowder run warden check[/]"
                      if RICH else "  nothing checked yet")
        console.print()
        return
    lines = [
        ("inventory", health.inventory),
        ("when", f"{health.started}  ({age(health.started)})"),
        ("services", f"{health.services} — {health.ok} ok, {health.warn} warn, "
                     f"{health.fail} fail, {health.unknown} unknown"),
    ]
    _kv(lines)
    if health.attention:
        table = Table(box=box.SIMPLE_HEAD, show_edge=False, pad_edge=False)
        for heading in ("SERVICE", "STATE", "DETAIL"):
            table.add_column(heading, overflow="fold")
        for row in health.attention:
            table.add_row(row.service, status(row.status),
                          (row.detail or f"{row.address}:{row.port}")[:70])
        console.print(table)
        console.print()


def cases_block(lab: Lab) -> None:
    head("bastet", "cases")
    if not lab.cases:
        console.print("  [dim]no cases yet — clowder run bastet case add[/]"
                      if RICH else "  no cases yet")
        console.print()
        return
    table = Table(box=box.SIMPLE_HEAD, show_edge=False, pad_edge=False)
    for heading, width in (("CASE", 14), ("SEV", 9), ("STATE", 9), ("TITLE", 52), ("NOTES", 7)):
        table.add_column(heading, overflow="fold", width=width + 3)
    for case in lab.cases:
        table.add_row(case.case_id, severity(case.severity), status(case.status),
                      case.title[:52], str(case.events))
    console.print(table)
    console.print()


def attention_block(lab: Lab) -> None:
    """The one list that justifies the console: worst first, whichever tool made it."""
    head("attention", "across all four")
    items = lab.attention()
    if not items:
        console.print("  [dim]nothing needs a look[/]" if RICH else "  nothing needs a look")
        console.print()
        return
    table = Table(box=box.SIMPLE_HEAD, show_edge=False, pad_edge=False)
    for heading, width in (("STATE", 9), ("WHERE", 26), ("WHAT", 56)):
        table.add_column(heading, overflow="fold", width=width + 3)
    for state, where, detail in items:
        table.add_row(status(state), where, detail[:70])
    console.print(table)
    console.print()


def doctor(lab: Lab) -> None:
    head("doctor")
    table = Table(box=box.SIMPLE_HEAD, show_edge=False, pad_edge=False)
    for heading in ("TOOL", "PATH", "DATA DIR", "DATA"):
        table.add_column(heading, overflow="fold")
    for tool in TOOLS:
        command = tool.command()
        table.add_row(tool.key, str(_short(str(command))) if command else "missing",
                      str(tool.data_dir()),
                      status("ok") if tool.data_dir().exists() else status("never"))
    console.print(table)
    console.print()
    if lab.problems:
        for problem in lab.problems:
            console.print(f"  [dim]· {problem}[/]" if RICH else f"  · {problem}")
        console.print()


def _short(path: str) -> str:
    """Show paths relative to home; they are long and the interesting part is the end."""
    home = str(Path.home())
    return path.replace(home, "~", 1)


def _kv(rows) -> None:
    if not RICH:
        for key, value in rows:
            console.print(f"{key}: {value}")
        console.print()
        return
    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="dim", no_wrap=True)
    grid.add_column()
    for key, value in rows:
        grid.add_row(key, value if isinstance(value, Text) else str(value))
    console.print(grid)
    console.print()


def emit_json(lab: Lab) -> None:
    payload = {
        "tools": [{"key": t.key, "installed": t.installed, "data": str(t.data_dir())}
                  for t in TOOLS],
        "scan": None if lab.scan is None else {
            "id": lab.scan.scan_id, "target": lab.scan.target,
            "strategy": lab.scan.strategy, "started": lab.scan.started,
            "score": lab.scan.score, "grade": lab.scan.grade,
            "counts": lab.scan.counts, "hosts": lab.scan.hosts, "ports": lab.scan.ports,
        },
        "health": None if lab.health is None else {
            "run": lab.health.run_id, "started": lab.health.started,
            "services": lab.health.services, "ok": lab.health.ok,
            "warn": lab.health.warn, "fail": lab.health.fail,
            "attention": [{"service": r.service, "status": r.status, "detail": r.detail}
                          for r in lab.health.attention],
        },
        "cases": [{"id": c.case_id, "title": c.title, "severity": c.severity,
                   "status": c.status, "events": c.events} for c in lab.cases],
        "attention": [{"state": s, "where": w, "detail": d} for s, w, d in lab.attention()],
        "problems": lab.problems,
    }
    json.dump(payload, sys.stdout, indent=2, ensure_ascii=False)
    sys.stdout.write("\n")


def fail(message: str) -> None:
    err_console.print(f"[bold #ff5c5c]x[/] {message}" if RICH else f"x {message}")


def hint(message: str) -> None:
    console.print(f"  [dim]{message}[/]" if RICH else f"  {message}")
    console.print()


def cat() -> None:
    console.print()
    for line in CAT:
        console.print(f"  [white]{line}[/]" if RICH else f"  {line}")
    console.print()
    console.print("  [dim]vantage · warden · bastet · whisker — a clowder is a group of cats[/]"
                  if RICH else "  a clowder is a group of cats")
    console.print()

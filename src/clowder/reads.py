"""Reading the other tools' state.

Every reader is defensive on purpose: a console that crashes because a database has an
older schema is worse than one that says "no runs yet". Nothing here computes a
severity, a risk score or a health verdict — those belong to the tool that made them.

Sources, exactly as the tools write them:

    vantage  ~/.local/share/vantage/reports/<scan_id>/report.json
    warden   <data>/warden.db     tables: runs, checks
    bastet   <data>/cases.db      tables: cases, events
    whisker  nothing persisted — analysed on demand
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .config import ATTENTION_RANK, BY_KEY, SEVERITY_ORDER


# --------------------------------------------------------------------- models

@dataclass
class ScanView:
    """The last Vantage run, as it describes itself."""

    scan_id: str = ""
    target: str = ""
    strategy: str = ""
    started: str = ""
    duration_s: float = 0.0
    hosts: int = 0
    ports: int = 0
    counts: dict[str, int] = field(default_factory=dict)
    score: int = 0
    grade: str = ""
    confirmed: int = 0
    path: str = ""

    @property
    def total(self) -> int:
        return sum(self.counts.values())

    @property
    def worst(self) -> str:
        for severity in ("critical", "high", "medium", "low", "info"):
            if self.counts.get(severity):
                return severity
        return "info"


@dataclass
class CheckRow:
    service: str = ""
    address: str = ""
    port: int | None = None
    status: str = ""
    detail: str = ""


@dataclass
class HealthView:
    """The last Warden run."""

    run_id: str = ""
    started: str = ""
    finished: str = ""
    inventory: str = ""
    services: int = 0
    ok: int = 0
    warn: int = 0
    fail: int = 0
    unknown: int = 0
    attention: list[CheckRow] = field(default_factory=list)

    @property
    def worst(self) -> str:
        if self.fail:
            return "fail"
        if self.warn:
            return "warn"
        if self.ok:
            return "ok"
        return "unknown"


@dataclass
class CaseView:
    """One Bastet case."""

    case_id: str = ""
    title: str = ""
    severity: str = ""
    status: str = ""
    updated: str = ""
    ttps: str = ""
    events: int = 0


@dataclass
class Lab:
    """Everything the console knows, in one object."""

    scan: ScanView | None = None
    health: HealthView | None = None
    cases: list[CaseView] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    @property
    def open_cases(self) -> list[CaseView]:
        return [c for c in self.cases if c.status != "closed"]

    def attention(self) -> list[tuple[str, str, str]]:
        """(severity, what, detail) — one list, worst first, across all tools."""
        items: list[tuple[str, str, str]] = []
        if self.health:
            for row in self.health.attention:
                items.append((row.status, f"warden · {row.service}",
                              row.detail or f"{row.address}:{row.port}"))
        if self.scan:
            items.append((self.scan.worst if self.scan.counts else "info",
                          f"vantage · {self.scan.target}",
                          f"{self.scan.counts.get(self.scan.worst, 0)} {self.scan.worst} "
                          f"of {self.scan.total} findings, score {self.scan.score}/100"))
        for case in self.open_cases:
            items.append((case.severity, f"bastet · {case.case_id}", case.title))
        items.sort(key=lambda item: (ATTENTION_RANK.get(item[0], 9), item[1]))
        return items


# --------------------------------------------------------------------- readers

def _age(stamp: str) -> str:
    """'3h ago' from an ISO timestamp, without a dependency for it."""
    if not stamp:
        return "never"
    try:
        then = datetime.fromisoformat(stamp)
    except ValueError:
        return stamp
    seconds = (datetime.now() - then).total_seconds()
    if seconds < 0:
        return "just now"
    if seconds < 90:
        return f"{seconds:.0f}s ago"
    if seconds < 5400:
        return f"{seconds / 60:.0f}m ago"
    if seconds < 172800:
        return f"{seconds / 3600:.1f}h ago"
    return f"{seconds / 86400:.0f}d ago"


def age(stamp: str) -> str:
    return _age(stamp)


def _open_sqlite(path: Path) -> sqlite3.Connection | None:
    if not path.exists():
        return None
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        return connection
    except sqlite3.Error:
        return None


def _strategy(payload: dict) -> str:
    """`strategy` is a {"name", "title"} object in the report; older runs used a string."""
    value = payload.get("strategy") or payload.get("profile") or ""
    if isinstance(value, dict):
        return str(value.get("title") or value.get("name") or "")
    return str(value)


def read_vantage() -> tuple[ScanView | None, str | None]:
    """The newest report.json, and a problem string when there is nothing to show."""
    root = BY_KEY["vantage"].data_dir() / "reports"
    if not root.exists():
        return None, "no vantage reports yet"
    reports = sorted(root.glob("*/report.json"), key=lambda p: p.stat().st_mtime)
    if not reports:
        return None, "no vantage reports yet"
    try:
        payload = json.loads(reports[-1].read_text())
    except (json.JSONDecodeError, OSError) as exc:
        return None, f"could not read the last vantage report: {exc}"

    summary = payload.get("summary") or {}
    hosts = payload.get("hosts") or []
    ports = sum(len(host.get("ports") or []) for host in hosts)
    return ScanView(
        scan_id=str(payload.get("scan_id") or reports[-1].parent.name),
        target=str(payload.get("target") or ""),
        strategy=_strategy(payload),
        started=str(payload.get("started") or ""),
        duration_s=float(payload.get("duration_s") or 0.0),
        hosts=len(hosts),
        ports=ports,
        counts={k: int(v) for k, v in (summary.get("counts") or {}).items()},
        score=int(summary.get("score") or 0),
        grade=str(summary.get("grade") or ""),
        confirmed=int(summary.get("confirmed") or 0),
        path=str(reports[-1].parent),
    ), None


def read_warden() -> tuple[HealthView | None, str | None]:
    path = BY_KEY["warden"].data_dir() / "warden.db"
    connection = _open_sqlite(path)
    if connection is None:
        return None, "no warden database yet"
    try:
        run = connection.execute(
            "SELECT * FROM runs ORDER BY started DESC LIMIT 1"
        ).fetchone()
        if run is None:
            return None, "warden has not run yet"
        rows = connection.execute(
            'SELECT service, address, port, status, detail FROM checks '
            'WHERE run_id = ? AND status IN ("fail", "warn", "unknown") '
            'ORDER BY CASE status WHEN "fail" THEN 0 WHEN "warn" THEN 1 ELSE 2 END, service',
            (run["id"],),
        ).fetchall()
    except sqlite3.Error as exc:
        return None, f"could not read warden state: {exc}"
    finally:
        connection.close()

    return HealthView(
        run_id=str(run["id"]),
        started=str(run["started"] or ""),
        finished=str(run["finished"] or ""),
        inventory=str(run["inventory"] or ""),
        services=int(run["n_services"] or 0),
        ok=int(run["n_ok"] or 0),
        warn=int(run["n_warn"] or 0),
        fail=int(run["n_fail"] or 0),
        unknown=int(run["n_unknown"] or 0),
        attention=[CheckRow(service=str(r["service"]), address=str(r["address"] or ""),
                            port=r["port"], status=str(r["status"] or ""),
                            detail=str(r["detail"] or "")) for r in rows],
    ), None


def read_bastet() -> tuple[list[CaseView], str | None]:
    path = BY_KEY["bastet"].data_dir() / "cases.db"
    connection = _open_sqlite(path)
    if connection is None:
        return [], "no bastet cases yet"
    try:
        rows = connection.execute(
            "SELECT c.id, c.title, c.severity, c.status, c.updated, c.ttps, "
            "(SELECT COUNT(*) FROM events e WHERE e.case_id = c.id) AS events "
            "FROM cases c "
            "ORDER BY CASE c.status WHEN 'open' THEN 0 ELSE 1 END, c.updated DESC"
        ).fetchall()
    except sqlite3.Error as exc:
        return [], f"could not read bastet cases: {exc}"
    finally:
        connection.close()

    return [
        CaseView(case_id=str(r["id"]), title=str(r["title"] or ""),
                 severity=str(r["severity"] or "medium"), status=str(r["status"] or ""),
                 updated=str(r["updated"] or ""), ttps=str(r["ttps"] or ""),
                 events=int(r["events"] or 0))
        for r in rows
    ], None


def read_all() -> Lab:
    """One call, four tools, no exceptions escaping."""
    lab = Lab()
    lab.scan, problem = read_vantage()
    if problem:
        lab.problems.append(problem)
    lab.health, problem = read_warden()
    if problem:
        lab.problems.append(problem)
    lab.cases, problem = read_bastet()
    if problem:
        lab.problems.append(problem)
    return lab


def worst_rank(severity: str) -> int:
    return SEVERITY_ORDER.get(severity, 9)

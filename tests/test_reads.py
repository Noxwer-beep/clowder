"""Readers: real data shapes, missing data, and older or broken stores."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from clowder import reads  # noqa: E402


def make_vantage_report(root: Path, *, strategy="standard", broken=False) -> Path:
    run = root / "reports" / "20261101-214652-e849"
    run.mkdir(parents=True, exist_ok=True)
    if broken:
        (run / "report.json").write_text("{not json")
        return run
    payload = {
        "scan_id": "20261101-214652-e849",
        "target": "10.10.20.101",
        "strategy": {"name": strategy, "title": "Standard assessment"} if strategy else "",
        "profile": "standard",
        "started": "2026-11-01T21:46:52",
        "finished": "2026-11-01T21:55:34",
        "duration_s": 522.0,
        "hosts": [{"ports": [{}, {}]}, {"ports": [{}]}],
        "summary": {"total": 3, "counts": {"critical": 1, "high": 2}, "score": 87,
                    "grade": "critical", "confirmed": 2},
    }
    (run / "report.json").write_text(json.dumps(payload))
    return run


def make_warden_db(root: Path, *, legacy=False) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    db = root / "warden.db"
    connection = sqlite3.connect(db)
    if legacy:
        connection.execute("CREATE TABLE runs (id TEXT PRIMARY KEY)")
        connection.execute("CREATE TABLE checks (run_id TEXT, service TEXT)")
        connection.commit()
        connection.close()
        return db
    connection.executescript(
        """
        CREATE TABLE runs (id TEXT PRIMARY KEY, started TEXT, finished TEXT,
            inventory TEXT, n_services INTEGER, n_ok INTEGER, n_warn INTEGER,
            n_fail INTEGER, n_unknown INTEGER);
        CREATE TABLE checks (run_id TEXT, service TEXT, "check" TEXT, address TEXT,
            port INTEGER, status TEXT, latency_ms REAL, detail TEXT);
        """
    )
    connection.execute(
        "INSERT INTO runs VALUES ('20261101-215632-2eab','2026-11-01T21:56:32',"
        "'2026-11-01T21:56:36','/home/nox/lab/warden.toml',16,13,1,2,0)"
    )
    rows = [
        ("ok-port", "10.0.0.1", 80, "ok", "fine"),
        ("bad-ntp", "10.0.0.2", 123, "fail", "ICMP unreachable"),
        ("slow-snmp", "10.0.0.3", 161, "warn", "no reply"),
    ]
    connection.executemany(
        'INSERT INTO checks (run_id, service, "check", address, port, status, detail) '
        "VALUES ('20261101-215632-2eab',?, 'tcp', ?,?,?,?)",
        [(name, address, port, status, detail) for name, address, port, status, detail in rows],
    )
    connection.commit()
    connection.close()
    return db


def make_bastet_db(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    db = root / "cases.db"
    connection = sqlite3.connect(db)
    connection.executescript(
        """
        CREATE TABLE cases (id TEXT PRIMARY KEY, title TEXT, severity TEXT, status TEXT,
            created TEXT, updated TEXT, summary TEXT, ttps TEXT, resolution TEXT);
        CREATE TABLE events (case_id TEXT, at TEXT, kind TEXT, message TEXT);
        """
    )
    connection.executemany(
        "INSERT INTO cases VALUES (?,?,?,?,?,?,'','','')",
        [
            ("IR-1", "old closed thing", "low", "closed", "2026-10-01T10:00:00",
             "2026-10-02T10:00:00"),
            ("IR-2", "the live one", "high", "open", "2026-11-01T21:47:09",
             "2026-11-01T21:55:52"),
        ],
    )
    connection.executemany(
        "INSERT INTO events VALUES ('IR-2', '2026-11-01T21:47:14', 'log', 'x')", []
    )
    connection.executemany(
        "INSERT INTO events VALUES ('IR-2', ?, 'log', 'x')",
        [("2026-11-01T21:47:14",), ("2026-11-01T21:55:52",)],
    )
    connection.commit()
    connection.close()
    return db


class Fixture(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="clowder-"))
        self.vantage = self.tmp / "vantage"
        self.warden = self.tmp / "warden"
        self.bastet = self.tmp / "bastet"
        self.env = mock.patch.dict(os.environ, {
            "VANTAGE_DATA_DIR": str(self.vantage),
            "WARDEN_DATA_DIR": str(self.warden),
            "BASTET_DATA_DIR": str(self.bastet),
            "CLOWDER_CONFIG": str(self.tmp / "none.toml"),
        })
        self.env.start()

    def tearDown(self) -> None:
        self.env.stop()


class VantageReader(Fixture):
    def test_reads_the_summary(self) -> None:
        make_vantage_report(self.vantage)
        scan, problem = reads.read_vantage()
        self.assertIsNone(problem)
        self.assertEqual(scan.target, "10.10.20.101")
        self.assertEqual(scan.strategy, "Standard assessment")   # dict, not str()
        self.assertEqual(scan.total, 3)
        self.assertEqual(scan.score, 87)
        self.assertEqual(scan.ports, 3)
        self.assertEqual(scan.hosts, 2)
        self.assertEqual(scan.worst, "critical")

    def test_no_reports_is_a_problem_not_a_crash(self) -> None:
        scan, problem = reads.read_vantage()
        self.assertIsNone(scan)
        self.assertIn("no vantage reports", problem)

    def test_broken_json_is_survivable(self) -> None:
        make_vantage_report(self.vantage, broken=True)
        scan, problem = reads.read_vantage()
        self.assertIsNone(scan)
        self.assertIn("could not read", problem)

    def test_string_strategy_from_older_runs(self) -> None:
        make_vantage_report(self.vantage, strategy="")
        run = self.vantage / "reports" / "20261101-214652-e849" / "report.json"
        payload = json.loads(run.read_text())
        payload["strategy"] = "standard"
        run.write_text(json.dumps(payload))
        scan, _ = reads.read_vantage()
        self.assertEqual(scan.strategy, "standard")


class WardenReader(Fixture):
    def test_reads_the_last_run(self) -> None:
        make_warden_db(self.warden)
        health, problem = reads.read_warden()
        self.assertIsNone(problem)
        self.assertEqual(health.services, 16)
        self.assertEqual((health.ok, health.warn, health.fail), (13, 1, 2))
        self.assertEqual(health.worst, "fail")
        self.assertEqual([row.service for row in health.attention], ["bad-ntp", "slow-snmp"])

    def test_failing_services_come_first(self) -> None:
        make_warden_db(self.warden)
        health, _ = reads.read_warden()
        self.assertEqual(health.attention[0].status, "fail")

    def test_missing_database(self) -> None:
        health, problem = reads.read_warden()
        self.assertIsNone(health)
        self.assertIn("no warden database", problem)

    def test_older_schema_reports_instead_of_raising(self) -> None:
        make_warden_db(self.warden, legacy=True)
        health, problem = reads.read_warden()
        self.assertIsNone(health)
        self.assertIn("could not read warden", problem)


class BastetReader(Fixture):
    def test_open_cases_first(self) -> None:
        make_bastet_db(self.bastet)
        cases, problem = reads.read_bastet()
        self.assertIsNone(problem)
        self.assertEqual([c.case_id for c in cases], ["IR-2", "IR-1"])
        self.assertEqual(cases[0].events, 2)

    def test_missing_database(self) -> None:
        cases, problem = reads.read_bastet()
        self.assertEqual(cases, [])
        self.assertIn("no bastet cases", problem)


class LabView(Fixture):
    def build(self) -> reads.Lab:
        make_vantage_report(self.vantage)
        make_warden_db(self.warden)
        make_bastet_db(self.bastet)
        return reads.read_all()

    def test_read_all_collects_problems_without_raising(self) -> None:
        lab = reads.read_all()
        self.assertIsNotNone(lab)
        self.assertEqual(len(lab.problems), 3)

    def test_open_cases_excludes_closed(self) -> None:
        lab = self.build()
        self.assertEqual([c.case_id for c in lab.open_cases], ["IR-2"])

    def test_attention_is_ordered_by_what_matters(self) -> None:
        lab = self.build()
        states = [state for state, _, _ in lab.attention()]
        # a confirmed critical finding outranks a service that is down
        self.assertEqual(states[0], "critical")
        self.assertEqual(states[1], "fail")
        self.assertLessEqual(states.index("fail"), states.index("high"))

    def test_attention_mentions_every_tool_that_has_something(self) -> None:
        lab = self.build()
        joined = " ".join(where for _, where, _ in lab.attention())
        for needle in ("warden", "vantage", "bastet"):
            self.assertIn(needle, joined)

    def test_age_formatting(self) -> None:
        self.assertEqual(reads.age(""), "never")
        self.assertIn("d ago", reads.age("2020-01-01T00:00:00"))
        self.assertEqual(reads.age("2999-01-01T00:00:00"), "just now")


if __name__ == "__main__":
    unittest.main()

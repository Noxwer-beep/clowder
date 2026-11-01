"""The console's own behaviour: the dashboard, the passthrough, and the web page."""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from clowder import cli, config, ui  # noqa: E402
from test_reads import make_bastet_db, make_vantage_report, make_warden_db  # noqa: E402


def run(argv: list[str]) -> tuple[int, str]:
    """Call main() and capture rich output (rich owns its stream, so swap the console)."""
    buffer = io.StringIO()
    from rich.console import Console

    fresh = Console(file=buffer, width=140, force_terminal=False)
    previous, previous_err = ui.console, ui.err_console
    ui.console = ui.err_console = fresh
    try:
        code = cli.main(argv)
    finally:
        ui.console, ui.err_console = previous, previous_err
    return code, buffer.getvalue()


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="clowder-cli-"))
        self.projects = self.tmp / "projects"
        self.projects.mkdir()
        self.env = mock.patch.dict(os.environ, {
            "VANTAGE_DATA_DIR": str(self.tmp / "vantage"),
            "WARDEN_DATA_DIR": str(self.tmp / "warden"),
            "BASTET_DATA_DIR": str(self.tmp / "bastet"),
            "CLOWDER_CONFIG": str(self.tmp / "none.toml"),
            "CLOWDER_PROJECTS": str(self.projects),
        })
        self.env.start()
        self.previous_projects = config.PROJECTS
        config.PROJECTS = self.projects
        config.TOOLS  # rebuild nothing: Tool.directory() reads PROJECTS at call time

    def tearDown(self) -> None:
        config.PROJECTS = self.previous_projects
        self.env.stop()


class Dashboard(Base):
    def test_empty_lab_still_renders(self) -> None:
        code, output = run(["status"])
        self.assertEqual(code, 0)
        self.assertIn("CLOWDER", output)
        self.assertIn("nothing scanned yet", output.lower())

    def test_full_lab_shows_all_four(self) -> None:
        make_vantage_report(self.tmp / "vantage")
        make_warden_db(self.tmp / "warden")
        make_bastet_db(self.tmp / "bastet")
        code, output = run([])
        self.assertEqual(code, 0)
        for needle in ("vantage", "warden", "bastet", "whisker", "attention"):
            self.assertIn(needle.lower(), output.lower())
        self.assertIn("10.10.20.101", output)
        self.assertIn("the live one", output)

    def test_json_is_valid_and_complete(self) -> None:
        make_vantage_report(self.tmp / "vantage")
        make_warden_db(self.tmp / "warden")
        make_bastet_db(self.tmp / "bastet")
        buffer = io.StringIO()
        previous, sys.stdout = sys.stdout, buffer
        try:
            code = cli.main(["status", "--json"])
        finally:
            sys.stdout = previous
        self.assertEqual(code, 0)
        payload = json.loads(buffer.getvalue())
        self.assertEqual(payload["scan"]["target"], "10.10.20.101")
        self.assertEqual(payload["health"]["fail"], 2)
        self.assertEqual(payload["cases"][0]["id"], "IR-2")
        self.assertTrue(payload["attention"])

    def test_fail_on_returns_four(self) -> None:
        make_warden_db(self.tmp / "warden")
        self.assertEqual(run(["status", "--fail-on", "fail"])[0], 4)

    def test_fail_on_passes_when_clean(self) -> None:
        self.assertEqual(run(["status", "--fail-on", "critical"])[0], 0)


class ToolsAndPassthrough(Base):
    def test_doctor_reports_missing_tools(self) -> None:
        code, output = run(["doctor"])
        self.assertEqual(code, 3)            # nothing is installed in the temp project dir
        self.assertIn("missing", output.lower())

    def test_unknown_tool(self) -> None:
        code, output = run(["run", "nmap"])
        self.assertEqual(code, 2)
        self.assertIn("unknown tool", output)

    def test_missing_launcher(self) -> None:
        code, output = run(["run", "warden", "check"])
        self.assertEqual(code, 3)
        self.assertIn("not installed", output)

    def test_passthrough_hands_over_argv_and_exit_code(self) -> None:
        project = self.projects / "warden"
        project.mkdir()
        launcher = project / "warden"
        launcher.write_text(
            "#!/bin/sh\n"
            "echo \"ran in $PWD with $*\"\n"
            "exit 7\n"
        )
        launcher.chmod(0o755)
        completed = cli.main(["warden", "check", "--plain", "--days", "2"])
        self.assertEqual(completed, 7)       # the tool's exit code, not the console's

    def test_shortcut_and_run_agree(self) -> None:
        project = self.projects / "bastet"
        project.mkdir()
        launcher = project / "bastet"
        launcher.write_text("#!/bin/sh\n[ \"$1\" = cat ] && exit 0\nexit 9\n")
        launcher.chmod(0o755)
        self.assertEqual(cli.main(["bastet", "cat"]), 0)
        self.assertEqual(cli.main(["run", "bastet", "other"]), 9)

    def test_cat(self) -> None:
        code, output = run(["cat"])
        self.assertEqual(code, 0)
        self.assertIn("> ^ <", output)


try:
    from clowder.web.app import create_app

    HAVE_FLASK = True
except ModuleNotFoundError:  # pragma: no cover
    HAVE_FLASK = False


@unittest.skipUnless(HAVE_FLASK, "flask is required for the dashboard")
class Web(Base):
    def test_page_renders_the_lab(self) -> None:
        make_vantage_report(self.tmp / "vantage")
        make_warden_db(self.tmp / "warden")
        make_bastet_db(self.tmp / "bastet")
        app = create_app(refresh=30)
        app.config.update(TESTING=True)
        client = app.test_client()
        response = client.get("/")
        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("CLOWDER", body)
        self.assertIn("10.10.20.101", body)
        self.assertIn("the live one", body)
        self.assertIn("bad-ntp", body)

    def test_empty_lab_page(self) -> None:
        app = create_app()
        app.config.update(TESTING=True)
        body = app.test_client().get("/").get_data(as_text=True)
        self.assertIn("No scan yet", body)

    def test_api_status(self) -> None:
        make_vantage_report(self.tmp / "vantage")
        app = create_app()
        app.config.update(TESTING=True)
        payload = app.test_client().get("/api/status").get_json()
        self.assertEqual(payload["scan"]["score"], 87)

    def test_static_css(self) -> None:
        app = create_app()
        app.config.update(TESTING=True)
        response = app.test_client().get("/static/app.css")
        self.assertEqual(response.status_code, 200)
        response.close()


if __name__ == "__main__":
    unittest.main()

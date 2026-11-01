"""The dashboard.

Four panels, one attention list, and a refresh that re-reads the tools' own stores —
which is why nothing on this page can disagree with what the tools report.
"""

from __future__ import annotations

from flask import Flask, jsonify, redirect, render_template

from .. import __version__
from .. import config, reads


def create_app(refresh: int = 30) -> Flask:
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config["REFRESH"] = refresh

    @app.context_processor
    def inject() -> dict:
        return {"version": __version__, "accent": config.ACCENT, "age": reads.age}

    @app.get("/")
    def index():
        lab = reads.read_all()
        return render_template("index.html", lab=lab, refresh=refresh, tools=config.TOOLS)

    @app.get("/api/status")
    def api_status():
        lab = reads.read_all()
        payload = {
            "scan": None if lab.scan is None else {
                "id": lab.scan.scan_id, "target": lab.scan.target,
                "strategy": lab.scan.strategy, "started": lab.scan.started,
                "score": lab.scan.score, "grade": lab.scan.grade,
                "counts": lab.scan.counts, "hosts": lab.scan.hosts,
                "ports": lab.scan.ports, "total": lab.scan.total,
            },
            "health": None if lab.health is None else {
                "run": lab.health.run_id, "started": lab.health.started,
                "services": lab.health.services, "ok": lab.health.ok,
                "warn": lab.health.warn, "fail": lab.health.fail,
                "attention": [{"service": r.service, "status": r.status,
                               "detail": r.detail, "port": r.port}
                              for r in lab.health.attention],
            },
            "cases": [{"id": c.case_id, "title": c.title, "severity": c.severity,
                       "status": c.status, "events": c.events, "updated": c.updated}
                      for c in lab.cases],
            "attention": [{"state": s, "where": w, "detail": d}
                          for s, w, d in lab.attention()],
            "problems": lab.problems,
        }
        return jsonify(payload)

    @app.get("/api/health")
    def api_health():
        return jsonify({"ok": True, "version": __version__})

    @app.get("/favicon.ico")
    def favicon():
        return redirect("/static/cat.svg")

    return app


def serve(host: str = "127.0.0.1", port: int = 8780, refresh: int = 30) -> None:
    app = create_app(refresh=refresh)
    app.run(host=host, port=port, debug=False, use_reloader=False)

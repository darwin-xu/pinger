"""pinger — Flask web UI + background probes.

Usage
─────
  python app.py [--port 8080]

Opens http://localhost:8080 with:
  - Live dashboard (auto-refreshing)
  - Host configuration page (add / edit / remove VPS)
  - Settings page (intervals, thresholds)

Background probe threads start automatically on launch.
"""
from __future__ import annotations

import argparse
import threading

from flask import Flask, jsonify, redirect, render_template, request, url_for

from engine import ProbeEngine, load_config, save_config
import storage

app = Flask(__name__)

# Global engine reference (initialised in main)
engine: ProbeEngine | None = None


def _cfg() -> dict:
    assert engine is not None
    return engine.cfg


# ── Dashboard ─────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    cfg = _cfg()
    snap_r, snap_h = engine.snapshot()
    return render_template(
        "index.html",
        hosts=cfg.get("hosts", []),
        thresholds=cfg.get("thresholds", {}),
        results=snap_r,
        history=snap_h,
        settings={
            "probe_interval": cfg.get("probe_interval", 30),
            "ping_count": cfg.get("ping_count", 10),
            "iperf3_duration": cfg.get("iperf3_duration", 5),
            "iperf3_port": cfg.get("iperf3_port", 5201),
        },
        running=engine.running,
    )


# ── JSON API for live refresh ─────────────────────────────────────────────────

@app.route("/api/status")
def api_status():
    cfg = _cfg()
    snap_r, snap_h = engine.snapshot()
    return jsonify(
        hosts=cfg.get("hosts", []),
        thresholds=cfg.get("thresholds", {}),
        results=snap_r,
        history=snap_h,
        running=engine.running,
    )


# ── Host CRUD ─────────────────────────────────────────────────────────────────

@app.route("/hosts/add", methods=["POST"])
def add_host():
    cfg = _cfg()
    host_entry = {
        "name":     request.form["name"].strip(),
        "host":     request.form["host"].strip(),
        "ssh_user": request.form["ssh_user"].strip(),
        "ssh_port": int(request.form.get("ssh_port") or 22),
    }
    password = request.form.get("password", "").strip()
    if password:
        host_entry["password"] = password

    if "hosts" not in cfg:
        cfg["hosts"] = []
    cfg["hosts"].append(host_entry)
    save_config(cfg)
    engine.reload_config(cfg)
    return redirect(url_for("index"))


@app.route("/hosts/<int:idx>/edit", methods=["POST"])
def edit_host(idx: int):
    cfg = _cfg()
    hosts = cfg.get("hosts", [])
    if 0 <= idx < len(hosts):
        hosts[idx] = {
            "name":     request.form["name"].strip(),
            "host":     request.form["host"].strip(),
            "ssh_user": request.form["ssh_user"].strip(),
            "ssh_port": int(request.form.get("ssh_port") or 22),
        }
        password = request.form.get("password", "").strip()
        if password:
            hosts[idx]["password"] = password
        else:
            hosts[idx].pop("password", None)
        save_config(cfg)
        engine.reload_config(cfg)
    return redirect(url_for("index"))


@app.route("/hosts/<int:idx>/delete", methods=["POST"])
def delete_host(idx: int):
    cfg = _cfg()
    hosts = cfg.get("hosts", [])
    if 0 <= idx < len(hosts):
        hosts.pop(idx)
        save_config(cfg)
        engine.reload_config(cfg)
    return redirect(url_for("index"))


@app.route("/hosts/<int:idx>/trigger-iperf3", methods=["POST"])
def trigger_iperf3(idx: int):
    cfg = _cfg()
    hosts = cfg.get("hosts", [])
    if not (0 <= idx < len(hosts)):
        return jsonify(ok=False, error="host not found"), 404
    h = hosts[idx]
    threading.Thread(
        target=engine._probe_iperf3, args=(h,), daemon=True, name=f"iperf3-manual-{idx}"
    ).start()
    return jsonify(ok=True)


# ── History API ───────────────────────────────────────────────────

@app.route("/api/history/<host>")
def api_history(host: str):
    limit = request.args.get("limit", 300, type=int)
    return jsonify(
        ping=storage.recent(host, "ping", limit=limit),
        tcp=storage.recent(host, "tcp", limit=limit),
        iperf3=storage.recent(host, "iperf3", limit=limit),
    )


# ── Settings ──────────────────────────────────────────────────────────────────

@app.route("/settings", methods=["POST"])
def update_settings():
    cfg = _cfg()
    for key in ("probe_interval", "ping_count", "iperf3_duration", "iperf3_port"):
        val = request.form.get(key)
        if val is not None:
            cfg[key] = int(val)

    if "thresholds" not in cfg:
        cfg["thresholds"] = {}
    for key in ("ping_warn_ms", "ping_crit_ms", "loss_warn_pct", "loss_crit_pct",
                "tcp_warn_ms", "tcp_crit_ms", "bw_warn_mbps", "bw_crit_mbps"):
        val = request.form.get(key)
        if val is not None:
            cfg["thresholds"][key] = int(val)

    save_config(cfg)
    engine.reload_config(cfg)
    return redirect(url_for("index"))


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    global engine

    parser = argparse.ArgumentParser(description="pinger web UI")
    parser.add_argument("--port", type=int, default=8080, help="Web UI port (default: 8080)")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address (default: 127.0.0.1)")
    parser.add_argument("--config", default="config.yaml", help="Config file path")
    args = parser.parse_args()

    cfg = load_config(args.config)
    engine = ProbeEngine(cfg)
    engine.start()

    print(f"Dashboard: http://{args.host}:{args.port}")
    app.run(host=args.host, port=args.port, debug=False)


if __name__ == "__main__":
    main()

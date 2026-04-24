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
import os
import threading
from datetime import datetime
from checksum import compute_repo_checksum

from flask import Flask, jsonify, redirect, render_template, request, url_for

from engine import ProbeEngine, load_config, save_config
import storage
from formatting import fmt_duration

app = Flask(__name__)
app.jinja_env.filters["fmt_duration"] = fmt_duration

# Global engine reference (initialised in main)
engine: ProbeEngine | None = None
# Server start timestamp (ISO 8601 UTC) — set in main()
server_start: str | None = None


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


# ── Version API ───────────────────────────────────────────────────

@app.route("/api/version")
def api_version():
    # Return checksum (from repository files) and server start time.
    try:
        checksum = compute_repo_checksum()
    except Exception:
        checksum = None
    return jsonify({"checksum": checksum, "server_start": server_start})


# ── History API ───────────────────────────────────────────────────

@app.route("/api/history/<host>")
def api_history(host: str):
    # Resolve display name → IP so history is stable across renames
    cfg = _cfg()
    host_ip = next(
        (h["host"] for h in cfg.get("hosts", []) if h["name"] == host),
        host,  # fall back to the value itself (e.g. direct IP lookup)
    )
    limit = min(request.args.get("limit", 1000, type=int), 50000)
    since = request.args.get("since") or None
    return jsonify(
        ping=storage.recent(host_ip, "ping", limit=limit, since=since),
        tcp=storage.recent(host_ip, "tcp",  limit=limit, since=since),
        iperf3=storage.recent(host_ip, "iperf3", limit=limit, since=since),
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
    global server_start

    parser = argparse.ArgumentParser(description="pinger web UI")
    parser.add_argument("--port", type=int, default=8080, help="Web UI port (default: 8080)")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address (default: 127.0.0.1)")
    parser.add_argument("--config", default="config.yaml", help="Config file path")
    args = parser.parse_args()

    cfg = load_config(args.config)
    engine = ProbeEngine(cfg)
    engine.start()
    # Record server start time for version API (UTC ISO format)
    server_start = datetime.utcnow().isoformat() + 'Z'

    print(f"Dashboard: http://{args.host}:{args.port}")
    app.run(host=args.host, port=args.port, debug=False)


if __name__ == "__main__":
    main()

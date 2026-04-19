"""Shared probe engine used by both main.py (CLI) and app.py (web)."""
from __future__ import annotations

import sys
import threading
import time
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import yaml

import storage
from probes import iperf3 as iperf3_probe
from probes import ping as ping_probe
from probes import tcp as tcp_probe

CONFIG_PATH = "config.yaml"


def load_config(path: str | None = None) -> dict:
    try:
        with open(path or CONFIG_PATH) as fh:
            return yaml.safe_load(fh) or {}
    except FileNotFoundError:
        return {"hosts": [], "thresholds": {}}


def save_config(cfg: dict, path: str | None = None) -> None:
    with open(path or CONFIG_PATH, "w") as fh:
        yaml.dump(cfg, fh, default_flow_style=False, sort_keys=False)


class ProbeEngine:
    """Background probe scheduler.  Thread-safe reads via snapshot()."""

    def __init__(self, cfg: dict) -> None:
        self.cfg = cfg
        self.results: dict = defaultdict(dict)
        self.history: dict = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._running = False

        storage.init_db()
        self._init_hosts()

    # ── Host management ───────────────────────────────────────────────────

    def _init_hosts(self) -> None:
        hosts = self.cfg.get("hosts", [])
        with self._lock:
            for h in hosts:
                name = h["name"]
                if name not in self.history:
                    self.history[name] = deque(maxlen=20)
                # Pre-load from DB
                for probe_name in ("ping", "tcp", "iperf3"):
                    row = storage.latest(name, probe_name)
                    if row:
                        self.results[name][probe_name] = row
                for row in reversed(storage.recent(name, "ping", limit=20)):
                    if row.get("success") and row.get("avg") is not None:
                        self.history[name].append(row["avg"])

    def reload_config(self, cfg: dict) -> None:
        """Hot-reload the config (e.g. after a web UI edit)."""
        self.cfg = cfg
        self._init_hosts()

    # ── Probe workers ─────────────────────────────────────────────────────

    def _probe_ping_tcp(self, h: dict) -> None:
        name     = h["name"]
        host     = h["host"]
        ssh_port = h.get("ssh_port", 22)

        ping_r = ping_probe.probe(host, count=self.cfg.get("ping_count", 10))
        tcp_r  = tcp_probe.probe(host, port=ssh_port)

        storage.save(name, "ping", ping_r)
        storage.save(name, "tcp",  tcp_r)

        ts = datetime.utcnow().isoformat()
        with self._lock:
            self.results[name]["ping"] = {"ts": ts, **ping_r}
            self.results[name]["tcp"]  = {"ts": ts, **tcp_r}
            if ping_r.get("success") and ping_r.get("avg") is not None:
                if name not in self.history:
                    self.history[name] = deque(maxlen=20)
                self.history[name].append(ping_r["avg"])

    def _probe_iperf3(self, h: dict) -> None:
        name = h["name"]
        r = iperf3_probe.probe(
            h,
            duration=self.cfg.get("iperf3_duration", 5),
            iperf3_port=self.cfg.get("iperf3_port", 5201),
        )
        storage.save(name, "iperf3", r)
        ts = datetime.utcnow().isoformat()
        with self._lock:
            self.results[name]["iperf3"] = {"ts": ts, **r}

    # ── Background loops ──────────────────────────────────────────────────

    def _ping_loop(self) -> None:
        interval = self.cfg.get("probe_interval", 30)
        while not self._stop.is_set():
            hosts = self.cfg.get("hosts", [])
            if hosts:
                with ThreadPoolExecutor(max_workers=len(hosts)) as pool:
                    futs = {
                        pool.submit(self._probe_ping_tcp, h): h for h in hosts
                    }
                    for f in as_completed(futs):
                        try:
                            f.result()
                        except Exception as exc:
                            print(
                                f"[probe error] {futs[f]['name']}: {exc}",
                                file=sys.stderr,
                            )
            self._stop.wait(timeout=interval)

    def _iperf3_loop(self) -> None:
        interval = self.cfg.get("iperf3_interval", 300)
        while not self._stop.is_set():
            targets = [h for h in self.cfg.get("hosts", []) if h.get("iperf3")]
            for h in targets:
                if self._stop.is_set():
                    break
                try:
                    self._probe_iperf3(h)
                except Exception as exc:
                    print(f"[iperf3 error] {h['name']}: {exc}", file=sys.stderr)
            self._stop.wait(timeout=interval)

    # ── Start / stop ──────────────────────────────────────────────────────

    def start(self) -> None:
        if self._running:
            return
        self._stop.clear()
        t1 = threading.Thread(target=self._ping_loop, daemon=True, name="ping-loop")
        t2 = threading.Thread(target=self._iperf3_loop, daemon=True, name="iperf3-loop")
        t1.start()
        t2.start()
        self._threads = [t1, t2]
        self._running = True

    def stop(self) -> None:
        self._stop.set()
        self._running = False

    @property
    def running(self) -> bool:
        return self._running

    # ── Snapshot for display ──────────────────────────────────────────────

    def snapshot(self) -> tuple[dict, dict]:
        """Return (results_copy, history_copy) under lock."""
        with self._lock:
            snap_r = {k: dict(v) for k, v in self.results.items()}
            snap_h = {k: list(v) for k, v in self.history.items()}
        return snap_r, snap_h

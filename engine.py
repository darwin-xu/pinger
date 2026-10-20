"""Shared probe engine used by both main.py (CLI) and app.py (web)."""
from __future__ import annotations

import sys
import threading
import time
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

import yaml

import storage
from probes import iperf3 as iperf3_probe
from probes import ping as ping_probe
from probes import tcp as tcp_probe

CONFIG_PATH = "config.yaml"

DEFAULT_RETENTION_DAYS = 90
MAINTENANCE_INTERVAL_S = 600


def retention_days(cfg: dict) -> int:
    """Days of raw ping/tcp rows to keep; 0 keeps everything."""
    value = cfg.get("retention_days", DEFAULT_RETENTION_DAYS)
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return DEFAULT_RETENTION_DAYS


def interval_minutes(value: object, default: int = 60) -> int:
    """Parse the periodic iperf3 interval as whole minutes."""
    if value is None:
        return default
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    raise ValueError("iperf3_interval must be an integer number of minutes")


def load_config(path: str | None = None) -> dict:
    global CONFIG_PATH
    if path is not None:
        CONFIG_PATH = path
    try:
        with open(CONFIG_PATH) as fh:
            cfg = yaml.safe_load(fh) or {}
    except FileNotFoundError:
        return {"hosts": [], "thresholds": {}}
    # Password auth is unsupported (SSH keys/agent only); drop any legacy
    # secrets so they are never served and are purged on the next save.
    for h in cfg.get("hosts") or []:
        if isinstance(h, dict):
            h.pop("password", None)
    return cfg


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
        self._config_changed = threading.Event()
        self._threads: list[threading.Thread] = []
        self._running = False
        self._iperf3_state: dict = {
            "enabled": False,
            "thread_alive": False,
            "interval_minutes": None,
            "scheduled_hosts": [],
            "last_cycle_started": None,
            "last_cycle_finished": None,
            "next_cycle_due": None,
            "last_error": None,
        }

        storage.init_db()
        self._init_hosts()

    # ── Host management ───────────────────────────────────────────────────

    def _init_hosts(self) -> None:
        hosts = self.cfg.get("hosts", [])
        # Read the DB before taking the lock so snapshot() is never blocked on I/O.
        loaded = []
        for h in hosts:
            probes = {}
            # Keyed by IP so renames don't lose history
            for probe_name in ("ping", "tcp", "iperf3"):
                row = storage.latest(h["host"], probe_name)
                if row:
                    probes[probe_name] = row
            pings = [
                row["avg"]
                for row in reversed(storage.recent(h["host"], "ping", limit=20))
                if row.get("success") and row.get("avg") is not None
            ]
            loaded.append((h["name"], probes, pings))
        with self._lock:
            for name, probes, pings in loaded:
                if name not in self.history:
                    self.history[name] = deque(maxlen=20)
                self.results[name].update(probes)
                self.history[name].extend(pings)

    def reload_config(self, cfg: dict) -> None:
        """Hot-reload the config (e.g. after a web UI edit)."""
        self.cfg = cfg
        self._init_hosts()
        self._config_changed.set()

    # ── Probe workers ─────────────────────────────────────────────────────

    def _probe_ping_tcp(self, h: dict) -> None:
        name     = h["name"]
        host     = h["host"]
        ssh_port = h.get("ssh_port", 22)

        ping_r = ping_probe.probe(host, count=self.cfg.get("ping_count", 10))
        tcp_r  = tcp_probe.probe(host, port=ssh_port)

        storage.save(host, "ping", ping_r)
        storage.save(host, "tcp",  tcp_r)

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
        storage.save(h["host"], "iperf3", r)
        ts = datetime.utcnow().isoformat()
        with self._lock:
            self.results[name]["iperf3"] = {"ts": ts, **r}

    def _set_iperf3_state(self, **updates: object) -> None:
        with self._lock:
            self._iperf3_state.update(updates)

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
        self._set_iperf3_state(thread_alive=True)
        while not self._stop.is_set():
            try:
                minutes = interval_minutes(self.cfg.get("iperf3_interval", 60))
                hosts = list(self.cfg.get("hosts", []))
                host_names = [h.get("name", h.get("host")) for h in hosts]
                if minutes <= 0:
                    self._set_iperf3_state(
                        enabled=False,
                        thread_alive=True,
                        interval_minutes=minutes,
                        scheduled_hosts=host_names,
                        next_cycle_due=None,
                        last_error=None,
                    )
                    self._config_changed.wait(timeout=60)
                    self._config_changed.clear()
                    continue

                started = datetime.utcnow()
                self._set_iperf3_state(
                    enabled=True,
                    thread_alive=True,
                    interval_minutes=minutes,
                    scheduled_hosts=host_names,
                    last_cycle_started=started.isoformat(),
                    next_cycle_due=None,
                    last_error=None,
                )
                for h in hosts:
                    if self._stop.is_set():
                        break
                    try:
                        self._probe_iperf3(h)
                    except Exception as exc:
                        msg = f"{h['name']}: {exc}"
                        self._set_iperf3_state(last_error=msg)
                        print(f"[iperf3 error] {msg}", file=sys.stderr)

                finished = datetime.utcnow()
                self._set_iperf3_state(
                    last_cycle_finished=finished.isoformat(),
                    next_cycle_due=(finished + timedelta(minutes=minutes)).isoformat(),
                )
                self._config_changed.wait(timeout=minutes * 60)
                self._config_changed.clear()
            except Exception as exc:
                msg = str(exc)
                self._set_iperf3_state(
                    enabled=False,
                    thread_alive=True,
                    last_error=msg,
                    next_cycle_due=None,
                )
                print(
                    f"[iperf3 scheduler error] {msg}",
                    file=sys.stderr,
                )
                self._config_changed.wait(timeout=60)
                self._config_changed.clear()
        self._set_iperf3_state(thread_alive=False)

    def run_maintenance(self) -> None:
        """Roll closed hours into the rollup table, then prune old raw rows."""
        ips = list(dict.fromkeys(h["host"] for h in self.cfg.get("hosts", [])))
        for ip in ips:
            for probe in storage.ROLLUP_PROBES:
                if self._stop.is_set():
                    return
                storage.rollup_hours(ip, probe)
        days = retention_days(self.cfg)
        if days <= 0:
            return
        cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
        for ip in ips:
            for probe in storage.ROLLUP_PROBES:
                if self._stop.is_set():
                    return
                storage.prune_raw(ip, probe, cutoff)

    def _maintenance_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.run_maintenance()
            except Exception as exc:
                print(f"[maintenance error] {exc}", file=sys.stderr)
            self._stop.wait(timeout=MAINTENANCE_INTERVAL_S)

    # ── Start / stop ──────────────────────────────────────────────────────

    def start(self) -> None:
        if self._running:
            return
        self._stop.clear()
        self._config_changed.clear()
        t1 = threading.Thread(target=self._ping_loop, daemon=True, name="ping-loop")
        t2 = threading.Thread(target=self._iperf3_loop, daemon=True, name="iperf3-loop")
        t3 = threading.Thread(target=self._maintenance_loop, daemon=True, name="maintenance-loop")
        t1.start()
        t2.start()
        t3.start()
        self._threads = [t1, t2, t3]
        self._running = True

    def stop(self) -> None:
        self._stop.set()
        self._config_changed.set()
        self._running = False

    @property
    def running(self) -> bool:
        return self._running

    # ── Snapshot for display ──────────────────────────────────────────────

    def snapshot(self) -> tuple[dict, dict]:
        """Return (results_copy, history_copy) under lock."""
        with self._lock:
            snap_r = {
                host: {
                    probe: dict(data) if isinstance(data, dict) else data
                    for probe, data in probes.items()
                }
                for host, probes in self.results.items()
            }
            snap_h = {k: list(v) for k, v in self.history.items()}
        return snap_r, snap_h

    def scheduler_state(self) -> dict:
        with self._lock:
            return {"iperf3": dict(self._iperf3_state)}

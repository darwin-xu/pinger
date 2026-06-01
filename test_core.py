"""Regression tests for core storage, config, checksum, and API behavior."""
from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import app as app_module
import checksum
import engine
from probes import iperf3 as iperf3_probe
import storage


class TestChecksumFileSelection(unittest.TestCase):
    def test_include_and_exclude_patterns_define_deployable_files(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for rel in (
                "app.py",
                "deploy.sh",
                "checksum.py",
                "test_formatting.py",
                "test_core.py",
                "requirements.txt",
                "templates/index.html",
                "probes/tcp.py",
                "venv/lib/site.py",
                "__pycache__/app.pyc",
                ".pytest_cache/cache.py",
                "README.md",
            ):
                path = root / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(rel)

            files = [p.relative_to(root).as_posix() for p in checksum.list_included_files(root)]

        self.assertEqual(
            files,
            [
                "app.py",
                "checksum.py",
                "probes/tcp.py",
                "requirements.txt",
                "templates/index.html",
            ],
        )

    def test_checksum_ignores_excluded_files(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "app.py").write_text("print('one')\n")
            (root / "test_core.py").write_text("ignored one\n")
            first = checksum.compute_repo_checksum(root)
            (root / "test_core.py").write_text("ignored two\n")
            self.assertEqual(checksum.compute_repo_checksum(root), first)
            (root / "app.py").write_text("print('two')\n")
            self.assertNotEqual(checksum.compute_repo_checksum(root), first)

    def test_files_only_output_matches_public_file_list(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "app.py").write_text("")
            (root / "templates").mkdir()
            (root / "templates" / "index.html").write_text("")

            with patch("sys.stdout") as stdout:
                checksum.main(["--root", str(root), "--files-only"])

            self.assertEqual(
                "".join(call.args[0] for call in stdout.write.call_args_list),
                "app.py\ntemplates/index.html\n",
            )


class StorageTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_db_path = storage.DB_PATH
        if storage._conn is not None:
            storage._conn.close()
            storage._conn = None
        storage.DB_PATH = Path(self.tmp.name) / "metrics.db"
        storage.init_db()

    def tearDown(self):
        if storage._conn is not None:
            storage._conn.close()
            storage._conn = None
        storage.DB_PATH = self.old_db_path
        self.tmp.cleanup()

    def _insert(self, ts, host, probe, data):
        storage._db().execute(
            "INSERT INTO metrics (ts, host, probe, data) VALUES (?, ?, ?, ?)",
            (ts, host, probe, json.dumps(data)),
        )
        storage._db().commit()

    def test_recent_filters_by_host_probe_since_and_orders_newest_first(self):
        self._insert("2026-01-01T00:00:00", "1.1.1.1", "ping", {"avg": 10})
        self._insert("2026-01-01T00:01:00", "1.1.1.1", "ping", {"avg": 20})
        self._insert("2026-01-01T00:02:00", "1.1.1.1", "tcp", {"rtt": 30})
        self._insert("2026-01-01T00:03:00", "2.2.2.2", "ping", {"avg": 40})

        rows = storage.recent("1.1.1.1", "ping", since="2026-01-01T00:00:30")

        self.assertEqual([row["avg"] for row in rows], [20])
        self.assertEqual(rows[0]["ts"], "2026-01-01T00:01:00")

    def test_latest_returns_newest_row_or_none(self):
        self.assertIsNone(storage.latest("missing", "ping"))
        self._insert("2026-01-01T00:00:00", "1.1.1.1", "ping", {"avg": 10})
        self._insert("2026-01-01T00:01:00", "1.1.1.1", "ping", {"avg": 20})

        self.assertEqual(storage.latest("1.1.1.1", "ping")["avg"], 20)


class Iperf3ProbeTestCase(unittest.TestCase):
    def test_probe_preserves_partial_download_error(self):
        class FakeClient:
            def close(self):
                pass

        with (
            patch.object(iperf3_probe.shutil, "which", return_value="/usr/bin/iperf3"),
            patch.object(iperf3_probe, "_make_client", return_value=FakeClient()),
            patch.object(
                iperf3_probe,
                "_exec",
                side_effect=[
                    (0, "/usr/bin/iperf3"),
                    (0, ""),
                ],
            ),
            patch.object(iperf3_probe, "_start_server", return_value=(True, "")),
            patch.object(iperf3_probe, "_with_server_log", side_effect=lambda err, _client, _port: err),
            patch.object(iperf3_probe.time, "sleep", return_value=None),
            patch.object(
                iperf3_probe,
                "_run_client",
                side_effect=[
                    (123.45, None),
                    (None, "download: unable to receive from server"),
                    (None, "download: unable to receive from server"),
                ],
            ),
        ):
            result = iperf3_probe.probe(
                {"host": "203.0.113.10", "ssh_user": "root"},
                duration=5,
            )

        self.assertTrue(result["success"])
        self.assertEqual(result["upload_mbps"], 123.5)
        self.assertIsNone(result["download_mbps"])
        self.assertEqual(result["download_error"], "download: unable to receive from server")

    def test_probe_retries_download_after_restarting_server(self):
        class FakeClient:
            def close(self):
                pass

        with (
            patch.object(iperf3_probe.shutil, "which", return_value="/usr/bin/iperf3"),
            patch.object(iperf3_probe, "_make_client", return_value=FakeClient()),
            patch.object(iperf3_probe, "_exec", side_effect=[(0, "/usr/bin/iperf3"), (0, "")]),
            patch.object(iperf3_probe, "_start_server", side_effect=[(True, ""), (True, "")]) as start_server,
            patch.object(iperf3_probe, "_with_server_log", side_effect=lambda err, _client, _port: err),
            patch.object(
                iperf3_probe,
                "_run_client",
                side_effect=[
                    (100.0, None),
                    (None, "download: Connection reset by peer"),
                    (95.0, None),
                ],
            ),
        ):
            result = iperf3_probe.probe(
                {"host": "203.0.113.10", "ssh_user": "root"},
                duration=5,
            )

        self.assertTrue(result["success"])
        self.assertEqual(result["upload_mbps"], 100.0)
        self.assertEqual(result["download_mbps"], 95.0)
        self.assertNotIn("download_error", result)
        self.assertEqual(start_server.call_count, 2)


class TestConfigPersistence(unittest.TestCase):
    def test_load_missing_config_returns_empty_schema(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(
                engine.load_config(str(Path(td) / "missing.yaml")),
                {"hosts": [], "thresholds": {}},
            )

    def test_save_and_load_config_round_trip(self):
        cfg = {"probe_interval": 15, "hosts": [{"name": "Tokyo", "host": "1.2.3.4"}]}
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "config.yaml"
            engine.save_config(cfg, str(path))
            self.assertEqual(engine.load_config(str(path)), cfg)

    def test_load_config_updates_default_save_path(self):
        old_path = engine.CONFIG_PATH
        try:
            with tempfile.TemporaryDirectory() as td:
                path = Path(td) / "custom.yaml"
                path.write_text("hosts: []\nthresholds: {}\n")

                engine.load_config(str(path))
                engine.save_config({"hosts": [{"name": "Tokyo", "host": "1.2.3.4"}]})

                self.assertIn("Tokyo", path.read_text())
        finally:
            engine.CONFIG_PATH = old_path

    def test_interval_minutes_requires_integer_minutes(self):
        self.assertEqual(engine.interval_minutes(None), 60)
        self.assertEqual(engine.interval_minutes(3), 3)
        self.assertEqual(engine.interval_minutes("3"), 3)
        with self.assertRaises(ValueError):
            engine.interval_minutes("3.5")


class FakeEngine:
    running = True

    def __init__(self):
        self.cfg = {
            "hosts": [{"name": "Tokyo", "host": "1.2.3.4"}],
            "thresholds": {},
        }

    def snapshot(self):
        return {"Tokyo": {"ping": {"success": True}}}, {"Tokyo": [1, 2, 3]}

    def scheduler_state(self):
        return {"iperf3": {"enabled": True, "thread_alive": True}}

    def reload_config(self, cfg):
        self.cfg = cfg


class TestAppApi(unittest.TestCase):
    def setUp(self):
        self.old_engine = app_module.engine
        self.old_start = app_module.server_start
        app_module.engine = FakeEngine()
        app_module.server_start = "2026-01-01T00:00:00Z"
        self.client = app_module.app.test_client()

    def tearDown(self):
        app_module.engine = self.old_engine
        app_module.server_start = self.old_start

    def test_history_resolves_display_name_to_host_ip(self):
        calls = []

        def fake_recent(host, probe, limit=20, since=None, until=None):
            calls.append((host, probe, limit, since, until))
            return [{"ts": "2026-01-01T00:00:00", "probe": probe}]

        with patch.object(app_module.storage, "recent", side_effect=fake_recent):
            resp = self.client.get("/api/history/Tokyo?limit=7&since=2026-01-01T00:00:00")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            calls,
            [
                ("1.2.3.4", "iperf3", 7, "2026-01-01T00:00:00", None),
                ("Tokyo", "iperf3", 7, "2026-01-01T00:00:00", None),
                ("1.2.3.4", "ping", 7, "2026-01-01T00:00:00", None),
                ("1.2.3.4", "tcp", 7, "2026-01-01T00:00:00", None),
            ],
        )

    def test_history_merges_iperf3_by_ip_and_display_name(self):
        def fake_recent(host, probe, limit=20, since=None, until=None):
            if probe != "iperf3":
                return []
            if host == "1.2.3.4":
                return [{"ts": "2026-01-02T00:00:00", "host_key": "ip"}]
            if host == "Tokyo":
                return [{"ts": "2026-01-01T00:00:00", "host_key": "name"}]
            return []

        with patch.object(app_module.storage, "recent", side_effect=fake_recent):
            data = self.client.get("/api/history/Tokyo?limit=7").get_json()

        self.assertEqual([row["host_key"] for row in data["iperf3"]], ["ip", "name"])

    def test_history_limit_is_capped(self):
        limits = []

        def fake_recent(host, probe, limit=20, since=None, until=None):
            limits.append(limit)
            return []

        with patch.object(app_module.storage, "recent", side_effect=fake_recent):
            self.client.get("/api/history/1.2.3.4?limit=999999")

        self.assertEqual(limits, [50000, 50000, 50000])

    def test_edit_host_drops_legacy_iperf3_field(self):
        app_module.engine.cfg["hosts"][0]["iperf3"] = True

        with patch.object(app_module, "save_config"):
            resp = self.client.post(
                "/hosts/0/edit",
                data={
                    "name": "Tokyo",
                    "host": "1.2.3.4",
                    "ssh_user": "root",
                    "ssh_port": "22",
                },
            )

        self.assertEqual(resp.status_code, 302)
        self.assertNotIn("iperf3", app_module.engine.cfg["hosts"][0])

    def test_version_uses_env_checksum_when_checksum_module_unavailable(self):
        old_compute = app_module.compute_repo_checksum
        old_env = os.environ.get("PINGER_CHECKSUM")
        app_module.compute_repo_checksum = None
        os.environ["PINGER_CHECKSUM"] = "abc123"
        try:
            data = self.client.get("/api/version").get_json()
        finally:
            app_module.compute_repo_checksum = old_compute
            if old_env is None:
                os.environ.pop("PINGER_CHECKSUM", None)
            else:
                os.environ["PINGER_CHECKSUM"] = old_env

        self.assertEqual(data, {"checksum": "abc123", "server_start": "2026-01-01T00:00:00Z"})

    def test_settings_accepts_integer_iperf3_interval_minutes(self):
        with patch.object(app_module, "save_config") as save_config:
            resp = self.client.post(
                "/settings",
                data={
                    "probe_interval": "30",
                    "iperf3_interval": "15",
                    "ping_count": "10",
                    "iperf3_duration": "5",
                    "iperf3_port": "5201",
                },
            )

        self.assertEqual(resp.status_code, 302)
        self.assertEqual(app_module.engine.cfg["iperf3_interval"], 15)
        self.assertIsInstance(app_module.engine.cfg["iperf3_interval"], int)
        save_config.assert_called_once()


class TestEngineSnapshot(unittest.TestCase):
    def test_snapshot_returns_copies(self):
        inst = object.__new__(engine.ProbeEngine)
        inst.results = {"Tokyo": {"ping": {"avg": 10}}}
        inst.history = {"Tokyo": [10, 20]}

        inst._lock = threading.Lock()

        results, history = inst.snapshot()
        results["Tokyo"]["tcp"] = {"rtt": 1}
        results["Tokyo"]["ping"]["avg"] = 999
        history["Tokyo"].append(30)

        self.assertNotIn("tcp", inst.results["Tokyo"])
        self.assertEqual(inst.results["Tokyo"]["ping"]["avg"], 10)
        self.assertEqual(inst.history["Tokyo"], [10, 20])

    def test_reload_config_wakes_periodic_iperf3_loop(self):
        inst = object.__new__(engine.ProbeEngine)
        inst.cfg = {"hosts": []}
        inst.history = {}
        inst.results = {}
        inst._lock = threading.Lock()
        inst._config_changed = threading.Event()

        with patch.object(engine.ProbeEngine, "_init_hosts", return_value=None):
            inst.reload_config({"iperf3_interval": 1, "hosts": []})

        self.assertTrue(inst._config_changed.is_set())

    def test_stop_wakes_periodic_iperf3_loop(self):
        inst = object.__new__(engine.ProbeEngine)
        inst._stop = threading.Event()
        inst._config_changed = threading.Event()
        inst._running = True

        inst.stop()

        self.assertTrue(inst._stop.is_set())
        self.assertTrue(inst._config_changed.is_set())
        self.assertFalse(inst.running)

    def test_iperf3_loop_runs_all_hosts_sequentially(self):
        inst = object.__new__(engine.ProbeEngine)
        inst.cfg = {
            "iperf3_interval": 1,
            "hosts": [
                {"name": "first", "host": "1.2.3.4", "iperf3": False},
                {"name": "second", "host": "5.6.7.8"},
            ],
        }
        inst._stop = threading.Event()
        inst._config_changed = threading.Event()
        inst._lock = threading.Lock()
        inst._iperf3_state = {}
        calls = []

        def fake_probe(host):
            calls.append(host["name"])
            if len(calls) == 2:
                inst._stop.set()
                inst._config_changed.set()

        inst._probe_iperf3 = fake_probe

        inst._iperf3_loop()

        self.assertEqual(calls, ["first", "second"])
        state = inst.scheduler_state()["iperf3"]
        self.assertEqual(state["scheduled_hosts"], ["first", "second"])
        self.assertEqual(state["interval_minutes"], 1)

    def test_iperf3_loop_reports_bad_interval_without_exiting(self):
        inst = object.__new__(engine.ProbeEngine)
        inst.cfg = {"iperf3_interval": "3.5", "hosts": []}
        inst._stop = threading.Event()
        inst._config_changed = threading.Event()
        inst._lock = threading.Lock()
        inst._iperf3_state = {}

        def stop_after_wait(timeout):
            inst._stop.set()
            return False

        inst._config_changed.wait = stop_after_wait

        inst._iperf3_loop()

        state = inst.scheduler_state()["iperf3"]
        self.assertIn("integer number of minutes", state["last_error"])
        self.assertFalse(state["thread_alive"])


if __name__ == "__main__":
    unittest.main()

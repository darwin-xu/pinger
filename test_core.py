"""Regression tests for core storage, config, checksum, and API behavior."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app as app_module
import checksum
import engine
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
                "probes/tcp.py",
                "requirements.txt",
                "templates/index.html",
            ],
        )

    def test_checksum_ignores_excluded_files(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "app.py").write_text("print('one')\n")
            (root / "checksum.py").write_text("ignored one\n")
            first = checksum.compute_repo_checksum(root)
            (root / "checksum.py").write_text("ignored two\n")
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


class FakeEngine:
    running = True

    def __init__(self):
        self.cfg = {
            "hosts": [{"name": "Tokyo", "host": "1.2.3.4"}],
            "thresholds": {},
        }

    def snapshot(self):
        return {"Tokyo": {"ping": {"success": True}}}, {"Tokyo": [1, 2, 3]}

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

        def fake_recent(host, probe, limit=20, since=None):
            calls.append((host, probe, limit, since))
            return [{"ts": "2026-01-01T00:00:00", "probe": probe}]

        with patch.object(app_module.storage, "recent", side_effect=fake_recent):
            resp = self.client.get("/api/history/Tokyo?limit=7&since=2026-01-01T00:00:00")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(
            calls,
            [
                ("1.2.3.4", "ping", 7, "2026-01-01T00:00:00"),
                ("1.2.3.4", "tcp", 7, "2026-01-01T00:00:00"),
                ("1.2.3.4", "iperf3", 7, "2026-01-01T00:00:00"),
            ],
        )

    def test_history_limit_is_capped(self):
        limits = []

        def fake_recent(host, probe, limit=20, since=None):
            limits.append(limit)
            return []

        with patch.object(app_module.storage, "recent", side_effect=fake_recent):
            self.client.get("/api/history/1.2.3.4?limit=999999")

        self.assertEqual(limits, [50000, 50000, 50000])

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


class TestEngineSnapshot(unittest.TestCase):
    def test_snapshot_returns_copies(self):
        inst = object.__new__(engine.ProbeEngine)
        inst.results = {"Tokyo": {"ping": {"avg": 10}}}
        inst.history = {"Tokyo": [10, 20]}
        import threading

        inst._lock = threading.Lock()

        results, history = inst.snapshot()
        results["Tokyo"]["tcp"] = {"rtt": 1}
        results["Tokyo"]["ping"]["avg"] = 999
        history["Tokyo"].append(30)

        self.assertNotIn("tcp", inst.results["Tokyo"])
        self.assertEqual(inst.results["Tokyo"]["ping"]["avg"], 10)
        self.assertEqual(inst.history["Tokyo"], [10, 20])


if __name__ == "__main__":
    unittest.main()

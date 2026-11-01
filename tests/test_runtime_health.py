"""Test health reporting and the real polling loop without importing Pi drivers."""
import ast
import os
from pathlib import Path
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch


SOURCE = Path(__file__).resolve().parents[1] / "rpi_support.py"
TREE = ast.parse(SOURCE.read_text(encoding="utf-8"))
HEALTH_FUNCTION = next(node for node in TREE.body if isinstance(node, ast.FunctionDef) and node.name == "update_health_status")
POLLING_LOOP = next(node for node in TREE.body if isinstance(node, ast.While))


class HealthTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.health = Path(self.directory.name) / "health"
        self.environment = patch.dict(os.environ, {"RPI_SUPPORT_HEALTH_FILE": str(self.health)})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.namespace = {"os": os, "time": time}
        module = ast.Module(body=[HEALTH_FUNCTION], type_ignores=[])
        exec(compile(module, str(SOURCE), "exec"), self.namespace)
        self.report = self.namespace["update_health_status"]

    def test_success_publishes_current_process_and_timestamp(self):
        self.report(True)
        pid, timestamp = map(int, self.health.read_text().split())
        self.assertEqual(pid, os.getpid())
        self.assertLessEqual(abs(time.time() - timestamp), 2)
        self.assertEqual(list(self.health.parent.glob("*.tmp")), [])

    def test_failure_removes_previous_success(self):
        self.report(True)
        self.report(False)
        self.report(False)
        self.assertFalse(self.health.exists())

    def test_manual_run_needs_no_health_file(self):
        with patch.dict(os.environ):
            os.environ.pop("RPI_SUPPORT_HEALTH_FILE", None)
            self.report(True)
        self.assertFalse(self.health.exists())

    def run_loop(self, fail, fan_service=None):
        stats = object()
        logs = []

        def collect():
            if fail:
                raise RuntimeError("collection failed")
            return stats

        def sleep(_):
            self.namespace["running"] = False

        self.namespace.update(running=True, disp=None, fan_service=fan_service, init_display=lambda: None,
                              get_system_stat=collect, log_stat_monitor=logs.append,
                              sleep=sleep, DISP_REF_INTERVAL=1)
        module = ast.Module(body=[POLLING_LOOP], type_ignores=[])
        exec(compile(module, str(SOURCE), "exec"), self.namespace)
        return logs

    def test_successful_poll_reports_health(self):
        self.assertEqual(len(self.run_loop(False)), 1)
        self.assertTrue(self.health.exists())

    def test_failed_poll_invalidates_health_and_skips_stale_stats(self):
        self.report(True)
        self.assertEqual(self.run_loop(True), [])
        self.assertFalse(self.health.exists())

    def test_failing_fan_control_prevents_healthy_startup(self):
        self.report(True)
        self.run_loop(False, SimpleNamespace(healthy=False))
        self.assertFalse(self.health.exists())


if __name__ == "__main__":
    unittest.main()

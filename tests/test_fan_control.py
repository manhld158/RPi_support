import json
from pathlib import Path
import sys
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from assets.fan_control import (CPU_CURVE, DISK_CURVE, FAN_CHANNELS, FanService,
                                HardwarePWM, TemperatureCurve, DiskTemperaturePolicy,
                                format_fan_status, find_pwm_chip,
                                read_disk_temperatures, smart_temperature)


class CurveTests(unittest.TestCase):
    def test_cpu_table(self):
        for temp, expected in [(39, 30), (40, 40), (50, 55), (60, 70), (70, 85), (75, 100)]:
            self.assertEqual(TemperatureCurve(CPU_CURVE).update(temp, 0), expected)

    def test_disk_table(self):
        for temp, expected in [(29, 30), (30, 40), (35, 55), (40, 70), (45, 85), (50, 100)]:
            self.assertEqual(TemperatureCurve(DISK_CURVE).update(temp, 0), expected)

    def test_increase_is_immediate(self):
        curve = TemperatureCurve(CPU_CURVE)
        curve.update(30, 0)
        self.assertEqual(curve.update(80, 1), 100)

    def test_decrease_needs_hysteresis_and_ten_seconds(self):
        curve = TemperatureCurve(CPU_CURVE)
        curve.update(55, 0)
        self.assertEqual(curve.update(49, 1), 55)
        self.assertEqual(curve.update(48, 2), 55)
        self.assertEqual(curve.update(48, 11), 55)
        self.assertEqual(curve.update(48, 12), 40)

    def test_fluctuation_cancels_decrease(self):
        curve = TemperatureCurve(CPU_CURVE)
        curve.update(55, 0)
        curve.update(48, 1)
        curve.update(49, 9)
        self.assertEqual(curve.update(48, 11), 55)
        self.assertEqual(curve.update(48, 21), 40)

    def test_invalid_temperature_immediately_uses_full_speed(self):
        for temp in [None, float("nan"), float("inf")]:
            self.assertEqual(TemperatureCurve(CPU_CURVE).update(temp, 0), 100)


class FixtureTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)


class PWMTests(FixtureTests):
    def make_chip(self):
        for number in FAN_CHANNELS.values():
            channel = self.root / f"pwm{number}"
            channel.mkdir()
            for filename in ["enable", "period", "duty_cycle", "polarity"]:
                (channel / filename).write_text("0")
        return HardwarePWM(self.root)

    def test_mapping_positive_polarity_and_25khz(self):
        self.assertEqual(FAN_CHANNELS, {"main": 1, "convection": 2, "disk": 3})
        pwm = self.make_chip()
        for number in FAN_CHANNELS.values():
            channel = self.root / f"pwm{number}"
            self.assertEqual((channel / "polarity").read_text(), "normal")
            self.assertEqual((channel / "period").read_text(), "40000")
            self.assertEqual((channel / "duty_cycle").read_text(), "40000")
            self.assertEqual((channel / "enable").read_text(), "1")
        pwm.set("main", 55)
        self.assertEqual((self.root / "pwm1/duty_cycle").read_text(), "22000")

    def test_full_speed_keeps_all_channels_enabled(self):
        pwm = self.make_chip()
        for name in FAN_CHANNELS:
            pwm.set(name, 30)
        self.assertEqual(pwm.full_speed(), [])
        for number in FAN_CHANNELS.values():
            self.assertEqual((self.root / f"pwm{number}/duty_cycle").read_text(), "40000")
            self.assertEqual((self.root / f"pwm{number}/enable").read_text(), "1")

    def test_does_not_guess_a_pwm_chip(self):
        chip = self.root / "pwmchip0"
        chip.mkdir()
        (chip / "npwm").write_text("4")
        with self.assertRaises(RuntimeError):
            find_pwm_chip(self.root)


class DiskTests(FixtureTests):
    def test_only_whole_sdx_devices_and_maximum_temperature(self):
        for name in ["sda", "sdb", "sdaa", "sda1", "nvme0n1", "mmcblk0", "loop0"]:
            (self.root / name).mkdir()
        calls = []

        def run(args, **kwargs):
            calls.append(args[-1])
            self.assertEqual(kwargs["timeout"], 3)
            temp = {"/dev/sda": 31, "/dev/sdb": 43, "/dev/sdaa": 38}[args[-1]]
            return SimpleNamespace(returncode=0, stdout=json.dumps({"temperature": {"current": temp}}))

        values, missing = read_disk_temperatures(self.root, run)
        self.assertEqual(set(calls), {"/dev/sda", "/dev/sdb", "/dev/sdaa"})
        self.assertEqual(max(values.values()), 43)
        self.assertEqual(missing, {})

    def test_health_warning_does_not_discard_valid_temperature(self):
        (self.root / "sda").mkdir()
        run = lambda *a, **kw: SimpleNamespace(returncode=8, stdout='{"temperature":{"current":44}}')
        self.assertEqual(read_disk_temperatures(self.root, run), ({"/dev/sda": 44}, {}))

    def test_missing_or_failed_smart_is_reported(self):
        (self.root / "sda").mkdir()
        for result in [SimpleNamespace(returncode=0, stdout="{}"),
                       SimpleNamespace(returncode=4, stdout='{"temperature":{"current":44}}'),
                       SimpleNamespace(returncode=0, stdout="invalid")]:
            values, missing = read_disk_temperatures(self.root, lambda *a, **kw: result)
            self.assertEqual(values, {})
            self.assertEqual(set(missing), {"/dev/sda"})
            self.assertTrue(missing["/dev/sda"])

    def test_temperature_attribute_low_byte(self):
        data = {"ata_smart_attributes": {"table": [{"id": 194, "raw": {"value": (50 << 16) | 37}}]}}
        self.assertEqual(smart_temperature(data), 37)

    def test_timeout_names_the_failed_drive_and_reason(self):
        (self.root / "sda").mkdir()

        def timeout(args, **kwargs):
            raise subprocess.TimeoutExpired(args, 3)

        values, missing = read_disk_temperatures(self.root, timeout)
        self.assertEqual(values, {})
        self.assertIn("TimeoutExpired", missing["/dev/sda"])
        self.assertIn("3 seconds", missing["/dev/sda"])


class FakePWM:
    def __init__(self):
        self.values = {}
        self.fallbacks = 0

    def set(self, name, value):
        self.values[name] = value

    def full_speed(self):
        self.fallbacks += 1
        self.values = {name: 100 for name in FAN_CHANNELS}
        return []


class ControlTests(FixtureTests):
    def setUp(self):
        super().setUp()
        self.cpu = self.root / "cpu"
        self.cpu.write_text("56000")
        self.pwm = FakePWM()
        self.service = FanService(self.pwm, cpu_path=self.cpu)
        self.service.started_at = 0
        self.service.disk_sample = (100, {"/dev/sda": 43, "/dev/sdb": 35}, [])

    def test_convection_tracks_faster_disk_fan(self):
        self.service.step(100)
        self.assertEqual(self.pwm.values, {"main": 55, "disk": 70, "convection": 70})
        self.assertEqual(self.service.snapshot()["disk_max_c"], 43)

    def test_convection_tracks_faster_cpu_fan(self):
        self.cpu.write_text("76000")
        self.service.step(100)
        self.assertEqual(self.pwm.values, {"main": 100, "disk": 70, "convection": 100})

    def test_missing_cpu_sensor_is_full_speed(self):
        self.cpu.unlink()
        self.service.step(100)
        self.assertEqual(self.pwm.values["main"], 100)
        self.assertEqual(self.pwm.values["convection"], 100)

    def test_one_unknown_disk_with_another_readable_disk_uses_seventy_percent(self):
        self.service.disk_sample = (100, {"/dev/sda": 30}, ["/dev/sdb"])
        self.service.step(100)
        self.assertEqual(self.pwm.values["disk"], 70)
        self.assertEqual(self.pwm.values["convection"], 70)
        self.assertEqual(self.service.snapshot()["disk_max_c"], 30)

    def test_hot_readable_disk_overrides_seventy_percent_minimum(self):
        self.service.disk_sample = (100, {"/dev/sda": 51}, ["/dev/sdb"])
        self.service.step(100)
        self.assertEqual(self.pwm.values["disk"], 100)

    def test_cached_hot_disk_does_not_cause_a_spike_to_full_speed(self):
        self.service.step(100)
        self.service.disk_sample = (130, {"/dev/sdb": 35}, {"/dev/sda": "timeout"})
        self.service.step(130)
        self.assertEqual(self.pwm.values["disk"], 70)
        self.assertEqual(self.service.snapshot()["disk_max_c"], 43)
        self.assertEqual(self.service.snapshot()["disk_cached"], {"/dev/sda": 30})

    def test_recovery_from_unknown_disk_reduces_speed_after_delay(self):
        self.service.disk_sample = (100, {"/dev/sda": 30}, {"/dev/sdb": "timeout"})
        self.service.step(100)
        self.service.disk_sample = (130, {"/dev/sda": 30, "/dev/sdb": 31}, {})
        self.service.step(130)
        self.assertEqual(self.pwm.values["disk"], 70)
        self.service.step(140)
        self.assertEqual(self.pwm.values["disk"], 40)

    def test_stale_disk_readings_are_full_speed(self):
        self.service.step(161)
        self.assertEqual(self.pwm.values["disk"], 100)

    def test_no_drives_use_minimum_disk_pwm(self):
        self.service.disk_sample = (100, {}, [])
        self.service.step(100)
        self.assertEqual(self.pwm.values["disk"], 30)

    def test_startup_kick_is_full_speed(self):
        self.service.started_at = 99
        self.service.step(100)
        self.assertEqual(self.pwm.values, {name: 100 for name in FAN_CHANNELS})

    def test_pwm_failure_sets_all_fans_to_full_speed(self):
        with patch.object(self.pwm, "set", side_effect=OSError("PWM unavailable")):
            with patch.object(self.service.stop_event, "wait", side_effect=lambda _: self.service.stop_event.set()):
                self.service._control_loop()
        self.assertFalse(self.service.healthy)
        self.assertEqual(self.pwm.fallbacks, 1)
        self.assertEqual(self.pwm.values, {name: 100 for name in FAN_CHANNELS})

    def test_smart_worker_logs_failed_drive_and_recovery(self):
        scans = iter([({}, {"/dev/sda": "SMART timeout"}), ({"/dev/sda": 38}, {})])
        self.service.disk_reader = lambda _: next(scans)
        waits = []

        def wait(_):
            waits.append(True)
            if len(waits) == 2:
                self.service.stop_event.set()

        with patch.object(self.service.stop_event, "wait", side_effect=wait):
            with patch("builtins.print") as output:
                self.service._disk_loop()
        messages = [call.args[0] for call in output.call_args_list]
        self.assertIn("Disk SMART read error: /dev/sda: SMART timeout", messages)
        self.assertIn("Disk SMART reading restored: /dev/sda", messages)


class DiskPolicyTests(unittest.TestCase):
    def setUp(self):
        self.policy = DiskTemperaturePolicy()

    def test_all_readable_uses_hottest_disk(self):
        state = self.policy.evaluate((100, {"/dev/sda": 35, "/dev/sdb": 44}, {}), 100)
        self.assertEqual(state["disk_max_c"], 44)
        self.assertEqual(state["disk_status"], "ok")

    def test_temporary_failure_uses_cache_without_losing_hottest_disk(self):
        self.policy.evaluate((100, {"/dev/sda": 35, "/dev/sdb": 44}, {}), 100)
        state = self.policy.evaluate((130, {"/dev/sda": 36}, {"/dev/sdb": "timeout"}), 130)
        self.assertEqual(state["disk_max_c"], 44)
        self.assertEqual(state["disk_status"], "cached")
        self.assertEqual(state["disk_cached"], {"/dev/sdb": 30})
        self.assertEqual(state["disk_minimum_pwm"], 0)

    def test_cache_expires_after_120_seconds_not_after_each_failure(self):
        self.policy.evaluate((100, {"/dev/sda": 35, "/dev/sdb": 44}, {}), 100)
        self.policy.evaluate((160, {"/dev/sda": 35}, {"/dev/sdb": "timeout"}), 160)
        sample = (220, {"/dev/sda": 35}, {"/dev/sdb": "timeout"})
        at_limit = self.policy.evaluate(sample, 220)
        self.assertEqual(at_limit["disk_cached"], {"/dev/sdb": 120})
        expired = self.policy.evaluate(sample, 220.1)
        self.assertEqual(expired["disk_cached"], {})
        self.assertEqual(expired["disk_max_c"], 35)
        self.assertEqual(expired["disk_missing"], {"/dev/sdb": "timeout"})
        self.assertEqual(expired["disk_minimum_pwm"], 70)

    def test_repeated_control_ticks_do_not_refresh_cache(self):
        sample = (100, {"/dev/sda": 35}, {})
        self.policy.evaluate(sample, 100)
        for now in range(101, 220):
            self.policy.evaluate(sample, now)
        state = self.policy.evaluate((221, {"/dev/sdb": 36}, {"/dev/sda": "timeout"}), 221)
        self.assertEqual(state["disk_minimum_pwm"], 70)
        self.assertEqual(set(state["disk_missing"]), {"/dev/sda"})

    def test_all_current_reads_failed_is_full_speed_even_with_cache(self):
        self.policy.evaluate((100, {"/dev/sda": 35}, {}), 100)
        state = self.policy.evaluate((130, {}, {"/dev/sda": "timeout"}), 130)
        self.assertEqual(state["disk_minimum_pwm"], 100)
        self.assertEqual(state["disk_cached"], {"/dev/sda": 30})

    def test_disconnected_drive_is_removed_from_cache(self):
        self.policy.evaluate((100, {"/dev/sda": 35, "/dev/sdb": 55}, {}), 100)
        state = self.policy.evaluate((130, {"/dev/sda": 35}, {}), 130)
        self.assertEqual(state["disk_max_c"], 35)
        self.assertNotIn("/dev/sdb", self.policy.cache)

    def test_no_drives_uses_minimum_but_stale_empty_scan_is_not_trusted(self):
        sample = (100, {}, {})
        self.assertEqual(self.policy.evaluate(sample, 100)["disk_minimum_pwm"], 30)
        self.assertEqual(self.policy.evaluate(sample, 161)["disk_minimum_pwm"], 100)

    def test_scanner_error_uses_full_speed_and_does_not_refresh_cache(self):
        self.policy.evaluate((100, {"/dev/sda": 35}, {}), 100)
        state = self.policy.evaluate(None, 130)
        self.assertEqual(state["disk_minimum_pwm"], 100)
        self.assertEqual(state["disk_cached"], {"/dev/sda": 30})

    def test_logs_distinguish_cache_and_unknown_disks(self):
        state = {"main": 55, "disk": 70, "convection": 70,
                 "disk_max_c": 43, "disk_status": "degraded",
                 "disk_cached": {"/dev/sdb": 65}, "disk_missing": {"/dev/sdc": "timeout"}}
        text = format_fan_status(state)
        self.assertIn("HDD:43.0C[degraded]", text)
        self.assertIn("cached:sdb(65s)", text)
        self.assertIn("missing:sdc", text)
        state["disk_max_c"] = None
        self.assertIn("HDD:N/A", format_fan_status(state))
        self.assertNotIn("NoneC", format_fan_status(state))


if __name__ == "__main__":
    unittest.main()

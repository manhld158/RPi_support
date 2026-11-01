import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from assets.fan_control import (CPU_CURVE, DISK_CURVE, FAN_CHANNELS, FanService,
                                HardwarePWM, TemperatureCurve, find_pwm_chip,
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
        self.assertEqual(missing, [])

    def test_health_warning_does_not_discard_valid_temperature(self):
        (self.root / "sda").mkdir()
        run = lambda *a, **kw: SimpleNamespace(returncode=8, stdout='{"temperature":{"current":44}}')
        self.assertEqual(read_disk_temperatures(self.root, run), ({"/dev/sda": 44}, []))

    def test_missing_or_failed_smart_is_reported(self):
        (self.root / "sda").mkdir()
        for result in [SimpleNamespace(returncode=0, stdout="{}"),
                       SimpleNamespace(returncode=4, stdout='{"temperature":{"current":44}}'),
                       SimpleNamespace(returncode=0, stdout="invalid")]:
            values, missing = read_disk_temperatures(self.root, lambda *a, **kw: result)
            self.assertEqual(values, {})
            self.assertEqual(missing, ["/dev/sda"])

    def test_temperature_attribute_low_byte(self):
        data = {"ata_smart_attributes": {"table": [{"id": 194, "raw": {"value": (50 << 16) | 37}}]}}
        self.assertEqual(smart_temperature(data), 37)


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

    def test_any_missing_disk_sensor_is_full_speed(self):
        self.service.disk_sample = (100, {"/dev/sda": 30}, ["/dev/sdb"])
        self.service.step(100)
        self.assertEqual(self.pwm.values["disk"], 100)

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


if __name__ == "__main__":
    unittest.main()

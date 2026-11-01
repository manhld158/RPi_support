"""Exercise the driver's I2C calls against the smbus2-compatible API."""
from pathlib import Path
import sys
from types import ModuleType
import unittest
from unittest.mock import Mock, patch


class INA219BackendTests(unittest.TestCase):
    def test_driver_uses_smbus2_without_system_smbus(self):
        bus = Mock()
        bus.read_i2c_block_data.return_value = [0x27, 0x10]
        backend = ModuleType("smbus2")
        backend.SMBus = Mock(return_value=bus)
        namespace = {"__name__": "ina219_backend_test"}
        source = Path(__file__).resolve().parents[1] / "assets" / "INA219.py"
        with patch.dict(sys.modules, {"smbus2": backend}):
            exec(compile(source.read_text(encoding="utf-8"), str(source), "exec"), namespace)
            sensor = namespace["INA219"](i2c_bus=1, addr=0x40)
            self.assertEqual(sensor.getBusVoltage_V(), 5.0)
            sensor.getCurrent_mA()
            sensor.getPower_W()
        backend.SMBus.assert_called_once_with(1)
        bus.read_i2c_block_data.assert_any_call(0x40, 0x02, 2)
        bus.read_i2c_block_data.assert_any_call(0x40, 0x04, 2)
        bus.read_i2c_block_data.assert_any_call(0x40, 0x03, 2)
        self.assertGreaterEqual(bus.write_i2c_block_data.call_count, 2)

    def test_failed_calibration_closes_bus_and_preserves_original_error(self):
        bus = Mock()
        bus.write_i2c_block_data.side_effect = OSError("sensor disconnected")
        bus.close.side_effect = OSError("close failed")
        backend = ModuleType("smbus2")
        backend.SMBus = Mock(return_value=bus)
        namespace = {"__name__": "ina219_backend_test"}
        source = Path(__file__).resolve().parents[1] / "assets" / "INA219.py"
        with patch.dict(sys.modules, {"smbus2": backend}):
            exec(compile(source.read_text(encoding="utf-8"), str(source), "exec"), namespace)
            with self.assertRaisesRegex(OSError, "sensor disconnected"):
                namespace["INA219"]()
        bus.close.assert_called_once()

    def test_driver_close_is_idempotent(self):
        bus = Mock()
        backend = ModuleType("smbus2")
        backend.SMBus = Mock(return_value=bus)
        namespace = {"__name__": "ina219_backend_test"}
        source = Path(__file__).resolve().parents[1] / "assets" / "INA219.py"
        with patch.dict(sys.modules, {"smbus2": backend}):
            exec(compile(source.read_text(encoding="utf-8"), str(source), "exec"), namespace)
            sensor = namespace["INA219"]()
            sensor.close()
            sensor.close()
        bus.close.assert_called_once()


class INA219ReconnectTests(unittest.TestCase):
    def setUp(self):
        namespace = {"__name__": "ina219_backend_test"}
        source = Path(__file__).resolve().parents[1] / "assets" / "INA219.py"
        with patch.dict(sys.modules, {"smbus2": ModuleType("smbus2")}):
            exec(compile(source.read_text(encoding="utf-8"), str(source), "exec"), namespace)
        self.clock = Mock(return_value=0.0)
        self.logger = Mock()
        self.sensor = Mock()
        self.sensor.getBusVoltage_V.return_value = 4.8
        self.sensor.getCurrent_mA.return_value = 500.0
        self.sensor.getPower_W.return_value = 2.4
        self.factory = Mock(return_value=self.sensor)
        self.monitor = namespace["INA219Monitor"](factory=self.factory, clock=self.clock, logger=self.logger)

    def test_healthy_sensor_is_reused(self):
        self.assertEqual(self.monitor.read(), (4.8, 0.5, 2.4))
        self.clock.return_value = 1
        self.assertEqual(self.monitor.read(), (4.8, 0.5, 2.4))
        self.factory.assert_called_once_with(i2c_bus=1, addr=0x40)
        self.sensor.close.assert_not_called()

    def test_sensor_missing_at_startup_recovers_after_five_seconds(self):
        self.factory.side_effect = [OSError("not attached"), self.sensor]
        self.assertIsNone(self.monitor.read())
        self.clock.return_value = 4.99
        self.assertIsNone(self.monitor.read())
        self.assertEqual(self.factory.call_count, 1)
        self.clock.return_value = 5
        self.assertEqual(self.monitor.read(), (4.8, 0.5, 2.4))
        self.assertEqual(self.factory.call_count, 2)

    def test_repeated_initialization_errors_are_rate_limited(self):
        self.factory.side_effect = OSError("not attached")
        for instant in [0, 1, 2, 3, 4, 5, 6, 9, 10]:
            self.clock.return_value = instant
            self.assertIsNone(self.monitor.read())
        self.assertEqual(self.factory.call_count, 3)
        self.assertEqual(self.logger.call_count, 3)

    def test_read_failure_closes_old_connection_before_reconnect(self):
        self.monitor.read()
        self.sensor.getCurrent_mA.side_effect = OSError("unplugged")
        self.clock.return_value = 1
        self.assertIsNone(self.monitor.read())
        self.sensor.close.assert_called_once()
        self.assertIsNone(self.monitor.sensor)
        self.clock.return_value = 5.99
        self.assertIsNone(self.monitor.read())
        self.factory.assert_called_once()
        replacement = Mock()
        replacement.getBusVoltage_V.return_value = 5.0
        replacement.getCurrent_mA.return_value = 600.0
        replacement.getPower_W.return_value = 3.0
        self.factory.return_value = replacement
        self.clock.return_value = 6
        self.assertEqual(self.monitor.read(), (5.0, 0.6, 3.0))

    def test_partial_sample_is_discarded(self):
        self.sensor.getPower_W.side_effect = OSError("read failed")
        self.assertIsNone(self.monitor.read())
        self.sensor.close.assert_called_once()

    def test_close_error_does_not_prevent_retry(self):
        self.sensor.getBusVoltage_V.side_effect = OSError("unplugged")
        self.sensor.close.side_effect = OSError("close failed")
        self.assertIsNone(self.monitor.read())
        self.assertIsNone(self.monitor.sensor)
        self.clock.return_value = 5
        self.assertIsNone(self.monitor.read())
        self.assertEqual(self.factory.call_count, 2)

    def test_retry_interval_starts_after_failed_initialization_finishes(self):
        def slow_failure(**kwargs):
            self.clock.return_value = 3
            raise OSError("timeout")
        self.factory.side_effect = slow_failure
        self.assertIsNone(self.monitor.read())
        self.clock.return_value = 7.99
        self.assertIsNone(self.monitor.read())
        self.factory.assert_called_once()
        self.clock.return_value = 8
        self.monitor.read()
        self.assertEqual(self.factory.call_count, 2)

    def test_shutdown_closes_once_and_stops_retrying(self):
        self.monitor.read()
        self.monitor.close()
        self.monitor.close()
        self.clock.return_value = 100
        self.assertIsNone(self.monitor.read())
        self.sensor.close.assert_called_once()
        self.factory.assert_called_once()


if __name__ == "__main__":
    unittest.main()

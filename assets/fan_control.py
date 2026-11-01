"""Positive hardware PWM for three FET-driven fans on Raspberry Pi 5."""
import json
import argparse
import math
import os
from pathlib import Path
import re
import subprocess
import socket
import threading
import time


CPU_CURVE = ((-math.inf, 30), (40, 40), (50, 55), (60, 70), (70, 85), (75, 100))
DISK_CURVE = ((-math.inf, 30), (30, 40), (35, 55), (40, 70), (45, 85), (50, 100))
FAN_CHANNELS = {"main": 1, "convection": 2, "disk": 3}
DISK_SCAN_MAX_AGE = 60
DISK_CACHE_MAX_AGE = 120
CONTROL_SOCKET = "/run/rpi_support/fan.sock"


class TemperatureCurve:
    def __init__(self, curve, hysteresis=2.0, down_delay=10.0):
        self.curve = curve
        self.hysteresis = hysteresis
        self.down_delay = down_delay
        self.level = None
        self.pending = None
        self.pending_since = None

    def update(self, temperature, now):
        if temperature is None or not math.isfinite(temperature):
            self.level = len(self.curve) - 1
            self.pending = None
            return 100
        target = max(i for i, (threshold, _) in enumerate(self.curve) if temperature >= threshold)
        if self.level is None or target >= self.level:
            self.level = target
            self.pending = None
        elif temperature <= self.curve[self.level][0] - self.hysteresis:
            if self.pending != target:
                self.pending = target
                self.pending_since = now
            elif now - self.pending_since >= self.down_delay:
                self.level = target
                self.pending = None
        else:
            self.pending = None
        return self.curve[self.level][1]


def find_pwm_chip(root=Path("/sys/class/pwm")):
    """Identify RP1's header PWM block, not the separate onboard fan block."""
    for chip in sorted(Path(root).glob("pwmchip*")):
        node = chip / "device" / "of_node"
        try:
            if (node.resolve().name == "pwm@98000"
                    and b"raspberrypi,rp1-pwm" in (node / "compatible").read_bytes().split(b"\0")
                    and int((chip / "npwm").read_text()) >= 4):
                return chip
        except (OSError, ValueError):
            continue
    raise RuntimeError("RP1 header PWM unavailable. Run sudo ./install.sh configure-fans, reboot, then install again.")


class HardwarePWM:
    def __init__(self, chip, frequency=25000):
        if not 25 <= frequency <= 100000:
            raise ValueError("Fan PWM frequency must be between 25 and 100000 Hz")
        self.chip = Path(chip)
        self.period = round(1_000_000_000 / frequency)
        self.channels = {}
        self.last = {}
        try:
            for name, number in FAN_CHANNELS.items():
                path = self.chip / f"pwm{number}"
                if not path.exists():
                    (self.chip / "export").write_text(str(number))
                    deadline = time.monotonic() + 1
                    while not path.exists():
                        if time.monotonic() >= deadline:
                            raise TimeoutError(f"PWM channel {number} did not appear")
                        time.sleep(0.01)
                self.channels[name] = path
                (path / "enable").write_text("0")
                (path / "duty_cycle").write_text("0")
                (path / "polarity").write_text("normal")
                (path / "period").write_text(str(self.period))
                (path / "duty_cycle").write_text(str(self.period))
                (path / "enable").write_text("1")
                self.last[name] = 100
        except Exception:
            self.full_speed()
            raise

    def set(self, name, percent):
        percent = max(0, min(100, percent))
        if self.last.get(name) != percent:
            (self.channels[name] / "duty_cycle").write_text(str(round(self.period * percent / 100)))
            self.last[name] = percent

    def full_speed(self):
        errors = []
        for name, path in self.channels.items():
            try:
                (path / "duty_cycle").write_text(str(self.period))
                (path / "enable").write_text("1")
                self.last[name] = 100
            except OSError as error:
                errors.append(str(error))
        return errors


def smart_temperature(data):
    value = data.get("temperature", {}).get("current")
    if value is None:
        value = data.get("scsi_temperature", {}).get("current")
    if value is None:
        for attribute in data.get("ata_smart_attributes", {}).get("table", []):
            if attribute.get("id") in (190, 194):
                # The low byte is temperature; upper bytes can encode min/max.
                raw = attribute.get("raw", {}).get("value")
                if isinstance(raw, int):
                    value = raw & 0xff
                    break
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) and 0 < value < 120 else None


def read_disk_temperatures(block_root=Path("/sys/block"), runner=subprocess.run):
    temperatures = {}
    missing = {}
    for disk in sorted(Path(block_root).iterdir()):
        if not re.fullmatch(r"sd[a-z]+", disk.name):
            continue
        device = f"/dev/{disk.name}"
        try:
            result = runner(["smartctl", "-j", "-A", device], capture_output=True, text=True, timeout=3)
            # Higher status bits report disk-health findings, not invalid JSON.
            if result.returncode < 0 or result.returncode & 0x07:
                raise ValueError(f"smartctl exit status {result.returncode}")
            temperature = smart_temperature(json.loads(result.stdout))
            if temperature is None:
                raise ValueError("Temperature unavailable")
            temperatures[device] = temperature
        except (OSError, subprocess.TimeoutExpired, ValueError, TypeError, AttributeError) as error:
            missing[device] = f"{type(error).__name__}: {error}"
    return temperatures, missing


class DiskTemperaturePolicy:
    """Keep each attached drive's last good reading without refreshing its age."""
    def __init__(self):
        self.cache = {}
        self.devices = set()
        self.last_sample = None
        self.errors = {}

    def evaluate(self, sample, now):
        fresh_scan = sample is not None and 0 <= now - sample[0] <= DISK_SCAN_MAX_AGE
        temperatures = sample[1] if sample is not None else {}
        if sample is not None and sample is not self.last_sample:
            scanned_at, temperatures, errors = sample
            self.devices = set(temperatures) | set(errors)
            self.errors = dict(errors) if isinstance(errors, dict) else dict.fromkeys(errors, "temperature unavailable")
            self.cache = {device: value for device, value in self.cache.items() if device in self.devices}
            for device, temperature in temperatures.items():
                self.cache[device] = (temperature, scanned_at)
            self.last_sample = sample

        usable, cached, unknown = {}, {}, {}
        fresh_count = 0
        for device in sorted(self.devices):
            previous = self.cache.get(device)
            if previous is not None and 0 <= now - previous[1] <= DISK_CACHE_MAX_AGE:
                usable[device] = previous[0]
                if fresh_scan and device in temperatures:
                    fresh_count += 1
                else:
                    cached[device] = int(now - previous[1])
            else:
                unknown[device] = self.errors.get(device, "no recent temperature")

        maximum = max(usable.values()) if usable else None
        if not self.devices and fresh_scan:
            status, minimum = "no-drives", 30
        elif not fresh_count:
            status, minimum = ("waiting" if self.last_sample is None else "unavailable"), 100
        elif unknown:
            status, minimum = "degraded", 70
        elif cached:
            status, minimum = "cached", 0
        else:
            status, minimum = "ok", 0
        return {"disk_max_c": maximum, "disk_status": status,
                "disk_cached": cached, "disk_missing": unknown, "disk_minimum_pwm": minimum}


def format_fan_status(state):
    temperature = state["disk_max_c"]
    value = "N/A" if temperature is None else f"{temperature:.1f}C"
    text = (f"FANS:{state['main']}/{state['disk']}/{state['convection']}% "
            f"HDD:{value}[{state['disk_status']}]")
    if state["disk_cached"]:
        text += " cached:" + ",".join(f"{Path(device).name}({age}s)" for device, age in state["disk_cached"].items())
    if state["disk_missing"]:
        text += " missing:" + ",".join(Path(device).name for device in state["disk_missing"])
    return text


class FanService:
    def __init__(self, pwm, cpu_path=Path("/sys/class/thermal/thermal_zone0/temp"),
                 block_root=Path("/sys/block"), disk_reader=read_disk_temperatures):
        self.pwm = pwm
        self.cpu_path = Path(cpu_path)
        self.block_root = Path(block_root)
        self.disk_reader = disk_reader
        self.curves = {"main": TemperatureCurve(CPU_CURVE), "disk": TemperatureCurve(DISK_CURVE)}
        self.stop_event = threading.Event()
        self.guard = threading.Lock()
        self.disk_sample = None
        self.disk_policy = DiskTemperaturePolicy()
        self.state = {"main": 100, "disk": 100, "convection": 100,
                      **self.disk_policy.evaluate(None, 0)}
        self.healthy = False
        self.started_at = time.monotonic()
        self.lock_file = None
        self.command_socket = None
        self.overrides = {}

    def command(self, request, now):
        """Commands run on the control thread; deadlines use the monotonic clock."""
        if not isinstance(request, dict):
            raise ValueError("Expected a JSON object")
        action = request.get("action")
        fan = request.get("fan", "all")
        if fan not in (*FAN_CHANNELS, "all"):
            raise ValueError("Unknown fan")
        names = list(FAN_CHANNELS) if fan == "all" else [fan]
        if action == "set":
            duties = request.get("duties", dict.fromkeys(names, request.get("percent")))
            duration = request.get("seconds", 300)
            if not isinstance(duties, dict) or not duties or any(name not in FAN_CHANNELS for name in duties):
                raise ValueError("Expected PWM values keyed by main, disk, or convection")
            if any(type(percent) is not int or not 0 <= percent <= 100 for percent in duties.values()):
                raise ValueError("PWM must be an integer from 0 to 100")
            if type(duration) is not int or not 1 <= duration <= 86400:
                raise ValueError("Duration must be 1 to 86400 seconds")
            for name, percent in duties.items():
                self.overrides[name] = (percent, now + duration)
        elif action == "auto":
            for name in names:
                self.overrides.pop(name, None)
        elif action != "status":
            raise ValueError("Unknown action")
        return {"ok": True, "state": self.snapshot(),
                "overrides": {name: {"percent": duty, "remaining_seconds": math.ceil(deadline - now)}
                              for name, (duty, deadline) in self.overrides.items() if deadline > now}}

    def _poll_command(self):
        if self.command_socket is None:
            return
        try:
            connection, _ = self.command_socket.accept()
        except BlockingIOError:
            return
        with connection:
            connection.settimeout(0.2)
            try:
                data = b""
                while b"\n" not in data and len(data) <= 4096:
                    chunk = connection.recv(4096)
                    if not chunk:
                        break
                    data += chunk
                if len(data) > 4096 or b"\n" not in data:
                    raise ValueError("Expected a JSON line of at most 4096 bytes")
                result = self.command(json.loads(data.split(b"\n", 1)[0]), time.monotonic())
            except (ValueError, TypeError, OSError) as error:
                result = {"ok": False, "error": str(error)}
            try:
                connection.sendall((json.dumps(result) + "\n").encode())
            except OSError:
                pass

    @classmethod
    def from_environment(cls):
        if os.environ.get("RPI_SUPPORT_FANS_ENABLED", "1") == "0":
            return None
        # Hold a process lock as well: a manual monitor must not fight systemd.
        import fcntl
        lock_file = open("/run/lock/rpi_support.fans.lock", "a")
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if not Path("/proc/device-tree/rpi-support-fans").exists():
                raise RuntimeError("Fan overlay is not active. Run configure-fans and reboot first.")
            pwm = HardwarePWM(find_pwm_chip(), int(os.environ.get("RPI_SUPPORT_PWM_HZ", "25000")))
            service = cls(pwm)
            service.lock_file = lock_file
            service.start()
            return service
        except Exception:
            lock_file.close()
            raise

    def start(self):
        self.command_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            Path(CONTROL_SOCKET).parent.mkdir(mode=0o755, parents=True, exist_ok=True)
            Path(CONTROL_SOCKET).unlink(missing_ok=True)
            self.command_socket.bind(CONTROL_SOCKET)
            os.chmod(CONTROL_SOCKET, 0o600)
            self.command_socket.listen(8)
            self.command_socket.setblocking(False)
        except Exception:
            self.command_socket.close()
            self.pwm.full_speed()
            raise
        self.control_thread = threading.Thread(target=self._control_loop, name="fan-control", daemon=True)
        self.disk_thread = threading.Thread(target=self._disk_loop, name="disk-temperature", daemon=True)
        self.control_thread.start()
        self.disk_thread.start()

    def _disk_loop(self):
        previous_errors = {}
        while not self.stop_event.is_set():
            try:
                # Use scan start as a conservative timestamp for every reading.
                # Slow scans must not make their earliest readings look newer.
                scanned_at = time.monotonic()
                temperatures, missing = self.disk_reader(self.block_root)
                errors = dict(missing) if isinstance(missing, dict) else dict.fromkeys(missing, "temperature unavailable")
                for device, reason in errors.items():
                    if previous_errors.get(device) != reason:
                        print(f"Disk SMART read error: {device}: {reason}", flush=True)
                for device in previous_errors.keys() & temperatures.keys():
                    print(f"Disk SMART reading restored: {device}", flush=True)
                previous_errors = errors
                sample = (scanned_at, temperatures, errors)
            except Exception as error:
                print(f"Disk temperature error: {error}", flush=True)
                sample = None
            with self.guard:
                self.disk_sample = sample
            self.stop_event.wait(30)

    def step(self, now):
        try:
            cpu = int(self.cpu_path.read_text()) / 1000
            if not 0 < cpu < 150:
                cpu = None
        except (OSError, ValueError):
            cpu = None
        with self.guard:
            sample = self.disk_sample
        disk_state = self.disk_policy.evaluate(sample, now)
        minimum = disk_state["disk_minimum_pwm"]
        disk_temperature = disk_state["disk_max_c"]
        if minimum == 100:
            disk_temperature = None
        else:
            disk_temperature = disk_temperature if disk_temperature is not None else 1
            if minimum:
                # Apply the minimum through the curve so recovery still uses
                # the same hysteresis and delayed speed reduction.
                floor_temperature = next(threshold for threshold, pwm in DISK_CURVE if pwm >= minimum)
                disk_temperature = max(disk_temperature, floor_temperature)
        disk_duty = self.curves["disk"].update(disk_temperature, now)
        main_duty = self.curves["main"].update(cpu, now)
        self.overrides = {name: value for name, value in self.overrides.items() if value[1] > now}
        main_duty = self.overrides.get("main", (main_duty,))[0]
        disk_duty = self.overrides.get("disk", (disk_duty,))[0]
        state = {"main": main_duty, "disk": disk_duty,
                 "convection": self.overrides.get("convection", (max(main_duty, disk_duty),))[0],
                 **disk_state}
        if now - self.started_at < 2:
            state.update(dict.fromkeys(FAN_CHANNELS, 100))  # Startup kick.
        for name in FAN_CHANNELS:
            self.pwm.set(name, state[name])
        with self.guard:
            self.state = state
            self.healthy = True

    def _control_loop(self):
        while not self.stop_event.is_set():
            try:
                self._poll_command()
                self.step(time.monotonic())
            except Exception as error:
                with self.guard:
                    self.healthy = False
                errors = self.pwm.full_speed()
                print(f"Fan PWM error: {error}; full-speed fallback errors: {errors}", flush=True)
            self.stop_event.wait(1)

    def snapshot(self):
        with self.guard:
            return dict(self.state)

    def stop(self):
        self.stop_event.set()
        self.control_thread.join(timeout=2)
        if self.command_socket is not None:
            self.command_socket.close()
            Path(CONTROL_SOCKET).unlink(missing_ok=True)
        # Leave hardware PWM enabled at 100% while the service is stopped.
        errors = self.pwm.full_speed()
        if errors:
            print(f"Could not set all fans to full speed: {errors}", flush=True)
        if self.lock_file is not None:
            self.lock_file.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Temporarily override the running fan service (requires sudo)")
    commands = parser.add_subparsers(dest="action", required=True)
    set_parser = commands.add_parser("set", help="Override PWM; automatically expires after 300 seconds")
    set_parser.add_argument("settings", nargs="+", metavar="FAN PWM",
                            help="Pairs such as main 60 disk 70 convection 80, or all 60")
    set_parser.add_argument("--seconds", type=int, default=300)
    auto_parser = commands.add_parser("auto", help="Cancel overrides immediately")
    auto_parser.add_argument("fan", choices=[*FAN_CHANNELS, "all"], nargs="?", default="all")
    commands.add_parser("status", help="Show last applied PWM and active overrides")
    args = parser.parse_args(argv)
    if args.action == "set" and not 1 <= args.seconds <= 86400:
        parser.error("--seconds must be between 1 and 86400")
    request = vars(args).copy()
    if args.action == "set":
        settings = request.pop("settings")
        if len(settings) % 2:
            parser.error("set expects FAN PWM pairs")
        duties = {}
        for fan, raw_percent in zip(settings[::2], settings[1::2]):
            if fan not in (*FAN_CHANNELS, "all"):
                parser.error(f"Unknown fan: {fan}")
            try:
                percent = int(raw_percent)
            except ValueError:
                parser.error("PWM must be an integer from 0 to 100")
            if not 0 <= percent <= 100:
                parser.error("PWM must be an integer from 0 to 100")
            if fan == "all" and len(settings) != 2:
                parser.error("Use all alone, or specify individual fans")
            names = list(FAN_CHANNELS) if fan == "all" else [fan]
            for name in names:
                if name in duties:
                    parser.error(f"Duplicate fan: {name}")
                duties[name] = percent
        request["duties"] = duties
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(5)
            client.connect(CONTROL_SOCKET)
            client.sendall((json.dumps(request) + "\n").encode())
            with client.makefile("r") as response:
                result = json.loads(response.readline(65536))
        if not result.get("ok"):
            parser.exit(1, f"Fan command failed: {result.get('error')}\n")
        print(json.dumps(result, indent=2))
    except (OSError, ValueError) as error:
        parser.exit(1, f"Cannot contact fan service: {error}. Use sudo and check rpi_support is running.\n")


if __name__ == "__main__":
    main()

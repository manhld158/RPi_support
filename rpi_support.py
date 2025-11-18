import os
import sys
import socket
import psutil
import signal
import subprocess
import re
import time
from time import sleep
from luma.core.interface.serial import i2c
from luma.core.error import DeviceNotFoundError
from luma.oled.device import ssd1306
from PIL import Image, ImageDraw, ImageFont
from dataclasses import dataclass, field
from assets.INA219 import INA219

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ICON_DIR = os.path.join(BASE_DIR, "assets", "png")
DISP_REF_INTERVAL = 1 #Interval 1sec
IP_SHOW_TIME = 5 #Seconds to show each IP
OLED_W = 128
OLED_H = 64

@dataclass
class SystemStats:
    #CPU
    cpu_freq_ghz: float = 0.0
    cpu_percent: float = 0.0
    cpu_temp_c: float = 0.0
    #RAM
    ram_total_gb: float = 0.0
    ram_used_gb: float = 0.0
    ram_percent: float = 0.0
    #DISK
    disk_total_gb: float = 0.0
    disk_used_gb: float = 0.0
    disk_percent: float = 0.0
    #NETWORK
    ip_list: list[tuple[str, str]] = field(default_factory=list)
    ip_internet: str = ""
    download_speed_mbps: float = 0.0
    upload_speed_mbps: float = 0.0
    #POWER
    power_total_w: float = 0.0
    power_rails_w: dict[str, float] = field(default_factory=dict)
    current_rails_a: dict[str, float] = field(default_factory=dict)
    voltage_rails_v: dict[str, float] = field(default_factory=dict)
    #INA219
    ina219_voltage_v: float = 0.0
    ina219_current_a: float = 0.0
    ina219_power_w: float = 0.0
    #ALARM
    under_voltage: bool = False
    throttling_heat: bool = False
    throttling_voltage: bool = False
    under_voltage_trg: bool = False
    throttling_heat_trg: bool = False

font8_sz = 8
font8 = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", font8_sz)
font10_sz = 10
font10 = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", font10_sz)

running = True

# Initialize INA219
ina219_sensor = None
try:
    ina219_sensor = INA219(i2c_bus=1, addr=0x40)
    print("INA219 initialized successfully")
except Exception as e:
    print(f"INA219 initialization failed: {e}")

def init_display(width = OLED_W, height = OLED_H):
    try:
        serial = i2c(port=1, address=0x3C)
        disp_temp = ssd1306(serial, width=width, height=height)
        print("OLED init done")
    except (OSError, DeviceNotFoundError) as e:
        print("OLED init failed:", e)
    except Exception as e:
        print("OLED error: ", e)
    else:
        return disp_temp
    return None

old_tick = time.perf_counter()
old_network_vl = psutil.net_io_counters()
def get_system_stat():
    global old_tick
    global old_network_vl
    tempData = SystemStats()
    
    #CPU
    tempData.cpu_freq_ghz = psutil.cpu_freq().current / 1000.0
    tempData.cpu_percent = psutil.cpu_percent(interval=None)
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            tempData.cpu_temp_c = int(f.read()) / 1000.0
    except Exception as e:
        print("Get cpu temp error:", e)
        tempData.cpu_temp_c = 0
    
    #RAM
    ram = psutil.virtual_memory()
    tempData.ram_total_gb = ram.total / (1024 * 1024 * 1024)
    tempData.ram_used_gb = ram.used / (1024 * 1024 * 1024)
    tempData.ram_percent = ram.percent
    
    #DISK
    disk = psutil.disk_usage("/")
    tempData.disk_total_gb = disk.total / (1024 * 1024 * 1024)
    tempData.disk_used_gb = disk.used / (1024 * 1024 * 1024)
    tempData.disk_percent = disk.percent
    
    #NETWORK
    raw_response = subprocess.check_output(["ip", "-4", "-o", "addr", "show"], text=True)
    pattern = re.compile(r'\d+: (\S+)\s+inet (\d+\.\d+\.\d+\.\d+)/\d+')
    for m in pattern.finditer(raw_response):
        ifname = m.group(1)
        ip = m.group(2)
        if ip.startswith("127.") or ip.startswith("169.254."):
            continue
        tempData.ip_list.append((ifname, ip))
    ip_numb = len(tempData.ip_list)
    if ip_numb == 0:
        tempData.ip_internet = "No Internet"
    else:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            tempData.ip_internet = s.getsockname()[0]
        except Exception:
            tempData.ip_internet = "No Internet"
        finally:
            s.close()
    duration = time.perf_counter() - old_tick
    old_tick = time.perf_counter()
    new_network_vl = psutil.net_io_counters()
    tempData.download_speed_mbps = ((new_network_vl.bytes_recv - old_network_vl.bytes_recv) / duration) * 8.0 / 1_000_000.0
    tempData.upload_speed_mbps = ((new_network_vl.bytes_sent - old_network_vl.bytes_sent) / duration) * 8.0 / 1_000_000.0
    old_network_vl = new_network_vl
    
    #POWER
    raw_adc_result = subprocess.run(["vcgencmd", "pmic_read_adc"], capture_output=True, text=True)
    if raw_adc_result.returncode == 0:
        text_adc = raw_adc_result.stdout
        current_pattern = re.compile(r"([\w\d_]+)_A\s+current\(\d+\)=([0-9]*\.?[0-9]+)A")
        voltage_pattern = re.compile(r"([\w\d_]+)_V\s+volt\(\d+\)=([0-9]*\.?[0-9]+)V")
        tempData.current_rails_a = {name: float(value) for name, value in current_pattern.findall(text_adc)}
        tempData.voltage_rails_v = {name: float(value) for name, value in voltage_pattern.findall(text_adc)}
        tempData.power_rails_w = {name: float(curr * volt) for name, curr in tempData.current_rails_a.items() for vname, volt in tempData.voltage_rails_v.items() if name == vname}
        tempData.power_total_w = sum(tempData.power_rails_w.values())
    
    #INA219 POWER MEASUREMENT
    global ina219_sensor
    if ina219_sensor:
        try:
            tempData.ina219_voltage_v = ina219_sensor.getBusVoltage_V()
            tempData.ina219_current_a = ina219_sensor.getCurrent_mA() / 1000.0
            tempData.ina219_power_w = ina219_sensor.getPower_W()
            # Add INA219 power to total power
            tempData.power_total_w += tempData.ina219_power_w
        except Exception as e:
            print(f"INA219 read error: {e}")

    #ALARM
    raw_result = subprocess.run(["vcgencmd", "get_throttled"], capture_output=True, text=True)
    val = int(raw_result.stdout.strip().split("=")[1], 16)
    tempData.under_voltage = val & 0x1
    tempData.throttling_heat = val & 0x2
    tempData.throttling_voltage = val & 0x4
    tempData.under_voltage_trg = val & 0x10000
    tempData.throttling_heat_trg = val & 0x20000
    
    
    return tempData

def draw_progress_bar(draw, xy, progress, maxProg=100, wallThick=1, gap = 0, verticaly=False, invert = False):
    x1, y1, x2, y2 = xy
    if wallThick < 0:
        wallThick = 0
    if wallThick > 0:
        if gap < 0:
            gap = 0
        x1 += wallThick + gap
        y1 += wallThick + gap
        x2 -= wallThick + gap
        y2 -= wallThick + gap
        draw.rectangle(xy, outline="white", width=wallThick, fill=None)
    if verticaly:
        progDispNon = int((y2 - y1) * (maxProg - progress) / maxProg)
        if invert:
            y2 -= progDispNon
        else:
            y1 += progDispNon
    else:
        progDispNon = int((x2 - x1) * (maxProg - progress) / maxProg)
        if invert:
            x1 += progDispNon
        else:
            x2 -= progDispNon
    draw.rectangle((x1, y1, x2, y2), outline="white", fill="white")

def exit_handler(signum, frame):
    global disp, running
    
    print(f"Exit signum: {signum}")
    running = False
    if disp == None:
        disp = init_display()
    
    if disp:
        disp.clear()
        disp.cleanup()
    sys.exit(0)

def log_stat_monitor(stats: SystemStats):
    # Đếm số dòng sẽ in
    lines = []
    lines.append("")
    lines.append("-" * 40)
    lines.append("CPU: {:.1f}GHz {:.1f}% {:.1f}C".format(
        stats.cpu_freq_ghz,
        stats.cpu_percent,
        stats.cpu_temp_c))
    lines.append("RAM: {:.2f}/{:.2f}GB {:.1f}%".format(
        stats.ram_used_gb,
        stats.ram_total_gb,
        stats.ram_percent))
    lines.append("DISK: {:.2f}/{:.2f}GB {:.1f}%".format(
        stats.disk_used_gb,
        stats.disk_total_gb,
        stats.disk_percent))
    
    network_line = "NETWORK: IPs: "
    for ifname, ip in stats.ip_list:
        network_line += f"{ifname}:{ip}; "
    network_line += f"Internet:{stats.ip_internet}"
    lines.append(network_line)
    
    lines.append(" DL: {:.2f}Mbps UL: {:.2f}Mbps".format(
        stats.download_speed_mbps,
        stats.upload_speed_mbps))
    lines.append("POWER: Total: {:.1f}W".format(stats.power_total_w))
    
    for rail, power in stats.power_rails_w.items():
        lines.append("  {}: {:.2f}W".format(rail, power))
    
    if stats.ina219_voltage_v > 0 or stats.ina219_current_a > 0 or stats.ina219_power_w > 0:
        lines.append("INA219: Voltage: {:.3f}V Current: {:.3f}A Power: {:.3f}W".format(
            stats.ina219_voltage_v,
            stats.ina219_current_a,
            stats.ina219_power_w))
    
    lines.append("ALARMS: UV:{} TH:{} TV:{} UVT:{} THT:{}".format(
        stats.under_voltage,
        stats.throttling_heat,
        stats.throttling_voltage,
        stats.under_voltage_trg,
        stats.throttling_heat_trg))
    
    # In tất cả các dòng
    for line in lines:
        print(line)
    
    # Di chuyển con trỏ lên để cập nhật tại chỗ lần sau
    print(f"\33[{len(lines)}A", end="")

#Main code
ip_show_time = time.perf_counter()
ip_index: int = 0
signal.signal(signal.SIGTERM, exit_handler)
signal.signal(signal.SIGINT, exit_handler)
disp = init_display()

if disp != None:
    try:
        logo_path = os.path.join(ICON_DIR, "logo_rpi.png")
        logo_rpi = Image.open(logo_path).convert("1")
        screen = Image.new("1", (disp.width, disp.height))
        screen.paste(logo_rpi, (int((screen.width - logo_rpi.width) / 2), 0))
        disp.display(screen)
    except FileNotFoundError as e:
        print("Logo not found:", e)
    except (OSError, DeviceNotFoundError) as e:
        print("OLED lost connect:", e)
        disp = None
    else:
        sleep(3)

while running:
    if disp == None:
        disp = init_display()
    
    try:
        sysStats = get_system_stat()
    except Exception as e:
        print("Get stats error:", e)
    else:
        log_stat_monitor(sysStats)

    if disp:
        try:
            canvas = Image.new("1", (disp.width, disp.height), "black")
            draw = ImageDraw.Draw(canvas)
            
            #CPU
            draw.rounded_rectangle((0, 0, 55, 35), radius=3, outline="white", width=1, fill=None)
            path_icon = os.path.join(ICON_DIR, "icon_cpu.png")
            icon = Image.open(path_icon).convert("1")
            canvas.paste(icon, (2, 2))
            
            draw_progress_bar(draw, (46, 2, 53, 33), sysStats.cpu_percent, verticaly=True, gap=1)
            
            text_draw = f"{sysStats.cpu_percent:.0f}%"
            text_length = draw.textlength(text_draw, font=font10)
            draw.text((31 - int(text_length / 2), 3), text_draw, font=font10, fill=255)
            
            text_draw = f"{sysStats.cpu_freq_ghz:.1f}GHz"
            text_length = draw.textlength(text_draw, font=font10)
            draw.text((23 - int(text_length / 2), 16), text_draw, font=font10, fill=255)
            
            text_draw = f"{sysStats.cpu_temp_c:.1f}°C"
            text_length = draw.textlength(text_draw, font=font10)
            draw.text((23 - int(text_length / 2), 24), f"{sysStats.cpu_temp_c:.1f}°C", font=font10, fill=255)
            
            #RAM
            draw.rounded_rectangle((57, 0, 91, 35), radius=3, outline="white", width=1, fill=None)
            path_icon = os.path.join(ICON_DIR, "icon_ram.png")
            icon = Image.open(path_icon).convert("1")
            canvas.paste(icon, (59, 2))
            
            draw_progress_bar(draw, (82, 2, 89, 33), sysStats.ram_percent, verticaly=True, gap=1)
            
            text_draw = (f"{sysStats.ram_used_gb:.2f}"
                        if sysStats.ram_used_gb < 10 else
                        f"{sysStats.ram_used_gb:.1f}"
                        if sysStats.ram_used_gb < 100 else
                        f"{sysStats.ram_used_gb:.0f}")
            text_length = draw.textlength(text_draw, font=font10)
            draw.text((70 - int(text_length / 2), 16), text_draw, font=font10, fill=255)
            
            text_draw = (f"{sysStats.ram_total_gb:.2f}"
                        if sysStats.ram_total_gb < 10 else
                        f"{sysStats.ram_total_gb:.1f}"
                        if sysStats.ram_total_gb < 100 else
                        f"{sysStats.ram_total_gb:.0f}")
            text_length = draw.textlength(text_draw, font=font10)
            draw.text((70 - int(text_length / 2), 24), text_draw, font=font10, fill=255)
            
            #DISK
            draw.rounded_rectangle((93, 0, 127, 35), radius=3, outline="white", width=1, fill=None)
            path_icon = os.path.join(ICON_DIR, "icon_disk.png")
            icon = Image.open(path_icon).convert("1")
            canvas.paste(icon, (95, 2))
            
            draw_progress_bar(draw, (118, 2, 125, 33), sysStats.disk_percent, verticaly=True, gap=1)
            
            text_draw = (f"{sysStats.disk_used_gb:.2f}"
                        if sysStats.disk_used_gb < 10 else
                        f"{sysStats.disk_used_gb:.1f}"
                        if sysStats.disk_used_gb < 100 else
                        f"{sysStats.disk_used_gb:.0f}")
            text_length = draw.textlength(text_draw, font=font10)
            draw.text((106 - int(text_length / 2), 16), text_draw, font=font10, fill=255)
            
            text_draw = (f"{sysStats.disk_total_gb:.2f}"
                        if sysStats.disk_total_gb < 10 else
                        f"{sysStats.disk_total_gb:.1f}"
                        if sysStats.disk_total_gb < 100 else
                        f"{sysStats.disk_total_gb:.0f}")
            text_length = draw.textlength(text_draw, font=font10)
            draw.text((106 - int(text_length / 2), 24), text_draw, font=font10, fill=255)
            
            #NETWORK
            draw.rounded_rectangle((0, 37, 91, 63), radius=3, outline="white", width=1, fill=None)
            path_icon = os.path.join(ICON_DIR, "icon_network.png")
            icon = Image.open(path_icon).convert("1")
            canvas.paste(icon, (2, 47))
            
            ip_numb = len(sysStats.ip_list)
            ip_name = ""
            ip_addr = ""
            if ip_numb == 0:
                ip_addr = "No connection"
            else:
                if ip_index >= ip_numb:
                    ip_addr = sysStats.ip_internet
                else:
                    ip_name, ip_addr = sysStats.ip_list[ip_index]
                if (time.perf_counter() - ip_show_time) > IP_SHOW_TIME:
                    ip_show_time = time.perf_counter()
                    ip_index = (ip_index + 1) % (ip_numb + 1)
            ip_name = ip_name[:4]
            draw.text((2, 37), ip_name, font=font8, fill=255)
            text_length = draw.textlength(ip_addr, font=font8)
            draw.text((54 - int(text_length / 2), 37), ip_addr, font=font8, fill=255)
            
            path_icon = os.path.join(ICON_DIR, "icon_download.png")
            icon = Image.open(path_icon).convert("1")
            canvas.paste(icon, (24, 45))
            
            path_icon = os.path.join(ICON_DIR, "icon_upload.png")
            icon = Image.open(path_icon).convert("1")
            canvas.paste(icon, (24, 53))
            
            text_draw = f"{sysStats.download_speed_mbps:.2f}"
            text_length = draw.textlength(text_draw, font=font8)
            draw.text((48 - int(text_length / 2), 45), text_draw, font=font8, fill=255)
            
            text_draw = f"{sysStats.upload_speed_mbps:.2f}"
            text_length = draw.textlength(text_draw, font=font8)
            draw.text((48 - int(text_length / 2), 53), text_draw, font=font8, fill=255)
            
            draw.text((66, 48), "Mbps", font=font8, fill=255)
            
            #ALARM
            draw.rounded_rectangle((93, 37, 127, 63), radius=3, outline="white", width=1, fill=None)
            if not (sysStats.under_voltage
                    or sysStats.throttling_heat
                    or sysStats.throttling_voltage
                    or sysStats.under_voltage_trg
                    or sysStats.throttling_heat_trg):
                path_icon = os.path.join(ICON_DIR, "icon_power.png")
                icon = Image.open(path_icon).convert("1")
                canvas.paste(icon, (105, 39))
                text_draw = f"{sysStats.power_total_w:.1f}W"
                text_length = draw.textlength(text_draw, font=font10)
                draw.text((110 - int(text_length / 2), 49), text_draw, font=font10, fill=255)
            else:
                if sysStats.under_voltage:
                    path_icon = os.path.join(ICON_DIR, "icon_underVoltage.png")
                    icon = Image.open(path_icon).convert("1")
                    canvas.paste(icon, (95, 39))
                if sysStats.throttling_heat:
                    path_icon = os.path.join(ICON_DIR, "icon_throttlingHeat.png")
                    icon = Image.open(path_icon).convert("1")
                    canvas.paste(icon, (106, 39))
                if sysStats.throttling_voltage:
                    path_icon = os.path.join(ICON_DIR, "icon_throttlingVoltage.png")
                    icon = Image.open(path_icon).convert("1")
                    canvas.paste(icon, (117, 39))
                if sysStats.under_voltage_trg:
                    path_icon = os.path.join(ICON_DIR, "icon_underVoltageTrg.png")
                    icon = Image.open(path_icon).convert("1")
                    canvas.paste(icon, (98, 52))
                if sysStats.throttling_heat_trg:
                    path_icon = os.path.join(ICON_DIR, "icon_throttlingHeatTrg.png")
                    icon = Image.open(path_icon).convert("1")
                    canvas.paste(icon, (113, 52))
            
            disp.contrast(1)
            disp.display(canvas)
        except (OSError, DeviceNotFoundError) as e:
            print("OLED lost connect:", e)
            disp = None
        except Exception as e:
            print("Try display system stat error:", e)
    
    sleep(DISP_REF_INTERVAL)

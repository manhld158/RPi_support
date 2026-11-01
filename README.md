# RPi_support

🖥️ **Raspberry Pi System Monitor with OLED Display**

A comprehensive system monitoring service for Raspberry Pi that displays real-time system information on an OLED screen. This service monitors CPU, RAM, disk usage, network activity, and system health status with visual indicators.

## 🌟 Features

### System Monitoring
- **CPU Information**: Frequency, usage percentage, and temperature
- **Memory Usage**: Total RAM, used RAM, and usage percentage  
- **Disk Usage**: Total storage, used storage, and usage percentage
- **Network Information**: IP addresses, internet connectivity, and speed monitoring

### System Health Alerts
- **Under-voltage detection** with visual warnings
- **CPU throttling monitoring** (heat and voltage-based)
- **Visual status indicators** with corresponding icons

### OLED Display
- **128x64 pixel OLED support** (SSD1306)
- **Real-time updates** with customizable refresh intervals
- **Icon-based interface** with PNG graphics
- **Multi-page information display**

## 🛠️ Hardware Requirements

- **Raspberry Pi** (any model with GPIO)
- **SSD1306 OLED Display** (128x64 pixels)
- **I2C Connection** between Pi and OLED

### Wiring
```
Raspberry Pi    →    OLED Display
3.3V           →    VCC
GND            →    GND  
GPIO 2 (SDA)   →    SDA
GPIO 3 (SCL)   →    SCL
```

## 📦 Installation

### Automatic Installation (Recommended)

1. **Clone the repository:**
   ```bash
   git clone https://github.com/manhld158/RPi_support.git
   cd RPi_support
   ```

2. **Prepare the Raspberry Pi 5 fan PWM pins once, then reboot:**
   ```bash
   sudo ./install.sh configure-fans
   sudo reboot
   ```

3. **After reboot, return to the repository and install:**
   ```bash
   sudo ./install.sh
   ```

The installer will automatically:
- ✅ Check for root privileges
- ✅ Install required system packages
- ✅ Prepare a release and its Python virtualenv before stopping the current service
- ✅ Point `/opt/rpi_support/` to the prepared release in `/opt/rpi_support-releases/`
- ✅ Create and enable systemd service
- ✅ Start the monitoring service

During an update, the installer backs up the existing application and service file.
The new service must remain active for 10 seconds without restarting and report
successful system statistics within the last 5 seconds, from its current PID.
If switching or startup fails, the installer restores the previous files and its enabled/running
state. This also supports upgrades from the original directory-based installation.
If automatic recovery fails, the installer prints the backup location and keeps it
for manual recovery. System packages and I2C configuration are not rolled back.
Install, remove, remove-all and restart share one lock; a concurrent operation
exits without changing the installation. The statistics check does not require
an OLED or INA219 to be connected.

### Manual Installation

If you prefer manual installation:

First run `sudo ./install.sh configure-fans` and reboot to enable the fan pins.

1. **Install dependencies:**
   ```bash
   sudo apt update
   sudo apt install python3 python3-pip python3-venv python3-dev build-essential libfreetype6-dev libjpeg-dev libopenjp2-7 python3-smbus i2c-tools fonts-dejavu-core smartmontools
   sudo mkdir -p /opt/rpi_support
   sudo python3 -m venv --system-site-packages /opt/rpi_support/.venv
   sudo /opt/rpi_support/.venv/bin/python -m pip install --ignore-installed psutil luma.oled Pillow
   ```

2. **Copy files:**
   ```bash
   sudo mkdir -p /opt/rpi_support
   sudo cp rpi_support.py /opt/rpi_support/
   sudo cp -r assets /opt/rpi_support/
   sudo cp rpi_support.service /etc/systemd/system/
   ```

3. **Enable and start service:**
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable rpi_support
   sudo systemctl start rpi_support
   ```

## 🚀 Usage

### Service Management

```bash
# Check service status
sudo ./install.sh status

# Restart service  
sudo ./install.sh restart

# View live logs
sudo ./install.sh logs

# Stop service
sudo systemctl stop rpi_support

# Start service
sudo systemctl start rpi_support
```

### Manual Commands

```bash
# Check service status
sudo systemctl status rpi_support

# View logs
sudo journalctl -u rpi_support -f

# Restart service
sudo systemctl restart rpi_support
```

## 🔧 Configuration

The service runs automatically and monitors system resources every second. Configuration can be modified in the Python script:

- **Display refresh interval**: `DISP_REF_INTERVAL = 1` (seconds)
- **OLED dimensions**: `OLED_W = 128`, `OLED_H = 64` (pixels)
- **Font settings**: Configurable font paths and sizes

### Three-fan cooling on Raspberry Pi 5

Two-wire fans are driven through external FET circuits using positive hardware
PWM (`normal` polarity). GPIO numbering is BCM. The RP1 header PWM controller is
identified by its device-tree node rather than assuming a fixed `pwmchip` number.

| Fan | BCM GPIO | RP1 PWM channel | Control input |
|---|---:|---:|---|
| Main | 13 | 1 | CPU temperature |
| Convection | 18 | 2 | Maximum of main and disk fan duty |
| Disk | 19 | 3 | Hottest whole `sd[a-z]+` drive |

| CPU temperature | Main PWM | Hottest disk temperature | Disk PWM |
|---|---:|---|---:|
| Below 40°C | 30% | Below 30°C | 30% |
| 40 to below 50°C | 40% | 30 to below 35°C | 40% |
| 50 to below 60°C | 55% | 35 to below 40°C | 55% |
| 60 to below 70°C | 70% | 40 to below 45°C | 70% |
| 70 to below 75°C | 85% | 45 to below 50°C | 85% |
| At least 75°C | 100% | At least 50°C | 100% |

PWM percentages specify duty cycle, not measured RPM. Curves are defined in
`assets/fan_control.py`. Output increases immediately. A decrease requires the
temperature to remain at least 2°C below the current level's lower threshold for
10 seconds. All fans receive an initial full-speed kick for at least 2 seconds.

The control thread reads CPU temperature every second. A separate worker reads
SMART temperatures every 30 seconds with a 3-second timeout per disk. Partitions,
NVMe and SD-card devices are excluded. If any selected drive cannot report a
temperature, or a scan becomes older than 60 seconds, the disk fan goes to 100%.
Missing CPU readings make the main fan run at 100%. With no `sdX` drives attached,
the disk fan uses the minimum 30%. Convection always follows the higher duty.

The default PWM frequency is 25 kHz. To change it, set the following in
`/etc/default/rpi_support`, then restart the service:

```bash
RPI_SUPPORT_PWM_HZ=25000
```

The service startup check also requires fan control to be operating. Fan duties
and the maximum disk temperature are included in logs. On a normal stop or an
unhandled process error, the controller attempts to leave all three outputs at
100% and enabled; it does not unexport them. Software cannot guarantee fallback
output when the PWM hardware itself is unavailable.

`configure-fans` installs `rpi-support-fans.dtbo` and adds an unconditional boot
entry. Application installation refuses to proceed until the overlay is active
after reboot, preserving an existing application during this preparation step.
The overlay assigns GPIO13/18/19 to this service; do not assign other peripherals
to those same pins. The boot overlay is retained during application uninstall.

## 📁 File Structure

```
/opt/rpi_support/
├── .venv/                 # Python environment for this release
├── rpi_support.py          # Main monitoring script
└── assets/
    └── png/
        ├── icon_cpu.png         # CPU status icon
        ├── icon_ram.png         # RAM status icon  
        ├── icon_disk.png        # Disk status icon
        ├── icon_network.png     # Network status icon
        ├── icon_download.png    # Download speed icon
        ├── icon_upload.png      # Upload speed icon
        ├── icon_goodState.png   # System OK icon
        ├── icon_underVoltage.png     # Under-voltage warning
        ├── icon_throttlingHeat.png   # Heat throttling warning
        ├── icon_throttlingVoltage.png # Voltage throttling warning
        └── logo_rpi.png         # Raspberry Pi logo
```

## 🔄 Uninstallation

To completely remove the service and all files:

```bash
sudo ./install.sh remove
```

This will:
- Stop and disable the service
- Remove the application link, managed releases and their virtualenvs
- Clean up systemd configuration

Shared system packages, I2C settings and journal logs are retained. Recovery
backups from a failed automatic rollback are also retained for manual recovery.
`remove-all` is a compatibility alias for `remove`; it also retains shared
dependencies. Only the `rpi_support` service has its failed state reset.

## 🐛 Troubleshooting

### Service Not Starting
```bash
# Check service status and logs
sudo systemctl status rpi_support
sudo journalctl -u rpi_support -n 20
```

### OLED Display Not Working
1. **Enable I2C:**
   ```bash
   sudo raspi-config
   # Navigate to Interface Options → I2C → Enable
   ```

2. **Check I2C connection:**
   ```bash
   sudo i2cdetect -y 1
   # Should show device at address 0x3C or 0x3D
   ```

3. **Verify wiring** according to the hardware requirements

### Permission Issues
```bash
# Ensure proper permissions
sudo chown -R root:root /opt/rpi_support
sudo chmod +x /opt/rpi_support/rpi_support.py
```

## 📝 Dependencies

### System Packages
- `python3` - Python interpreter
- `python3-pip` - Python package manager
- `python3-dev` - Python development headers
- `build-essential` - Compilation tools
- `libfreetype6-dev`, `libjpeg-dev`, `libopenjp2-7`, `libtiff5` - Image processing libraries

### Python Packages
- `psutil` - System and process monitoring
- `luma.oled` - OLED display driver
- `pillow` - Image processing and fonts

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## 📄 License

This project is open source and available under the [MIT License](LICENSE).

## 👨‍💻 Author

**manhld158**
- GitHub: [@manhld158](https://github.com/manhld158)

## 🙏 Acknowledgments

- [Luma.OLED](https://github.com/rm-hull/luma.oled) for the excellent OLED display library
- [psutil](https://github.com/giampaolo/psutil) for system monitoring capabilities
- [Pillow](https://python-pillow.org/) for image processing

---

**⭐ If this project helped you, please give it a star!**

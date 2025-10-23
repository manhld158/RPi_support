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

2. **Run the installation script:**
   ```bash
   sudo ./install.sh
   ```

The installer will automatically:
- ✅ Check for root privileges
- ✅ Install required system packages
- ✅ Install Python dependencies
- ✅ Copy files to `/opt/rpi_support/`
- ✅ Create and enable systemd service
- ✅ Start the monitoring service

### Manual Installation

If you prefer manual installation:

1. **Install dependencies:**
   ```bash
   sudo apt update
   sudo apt install python3 python3-pip python3-dev build-essential libfreetype6-dev libjpeg-dev libopenjp2-7 libtiff5
   sudo pip3 install psutil luma.oled pillow
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

## 📁 File Structure

```
/opt/rpi_support/
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
- Remove all installed files
- Clean up systemd configuration

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
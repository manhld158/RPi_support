#!/bin/bash

# Install script for service automation
# This script will install the service and required dependencies

set -e

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Configuration
SERVICE_NAME="rpi_support"
INSTALL_DIR="/opt/${SERVICE_NAME}"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Function to print colored output
print_status() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Function to check if running as root
check_root() {
    if [ "$EUID" -ne 0 ]; then
        print_error "This script must be run as root. Please use sudo."
        exit 1
    fi
}

# Function to check if I2C is already enabled
is_i2c_enabled() {
    # Check if I2C kernel module is loaded
    if lsmod | grep -q "^i2c_dev"; then
        # Check if I2C device files exist
        if [ -e /dev/i2c-0 ] || [ -e /dev/i2c-1 ]; then
            return 0  # I2C is enabled
        fi
    fi
    return 1  # I2C is not enabled
}

# Function to check if I2C is enabled in config.txt
is_i2c_in_config() {
    local config_file="$1"
    
    if [ ! -f "$config_file" ]; then
        return 1  # File doesn't exist
    fi
    
    # Check for enabled I2C (uncommented line)
    if grep -q "^[[:space:]]*dtparam=i2c_arm=on" "$config_file"; then
        return 0  # I2C is enabled in config
    fi
    
    return 1  # I2C is not enabled in config
}

# Function to enable I2C interface on Raspberry Pi
enable_i2c() {
    print_status "Checking I2C interface status..."
    
    # First check if I2C is already working
    if is_i2c_enabled; then
        print_status "I2C interface is already enabled and working."
        
        # Verify with i2cdetect if available
        if command -v i2cdetect &> /dev/null; then
            print_status "I2C buses detected:"
            i2cdetect -l 2>/dev/null || true
        fi
        
        return 0
    fi
    
    print_status "I2C interface is not enabled. Enabling now..."
    
    # Check if raspi-config is available (Raspberry Pi specific)
    if command -v raspi-config &> /dev/null; then
        print_status "Using raspi-config to enable I2C..."
        
        # Enable I2C using raspi-config (non-interactive mode)
        if raspi-config nonint do_i2c 0; then
            print_status "I2C interface enabled via raspi-config."
        else
            print_error "Failed to enable I2C via raspi-config. Trying manual method..."
        fi
        
    else
        print_warning "raspi-config not found. Using manual configuration..."
        
        # Determine which config file to use
        local config_file=""
        if [ -f /boot/firmware/config.txt ]; then
            config_file="/boot/firmware/config.txt"
        elif [ -f /boot/config.txt ]; then
            config_file="/boot/config.txt"
        else
            print_error "Could not find config.txt file."
            return 1
        fi
        
        print_status "Using config file: $config_file"
        
        # Check if I2C is already in config (but maybe commented out)
        if is_i2c_in_config "$config_file"; then
            print_status "I2C is already enabled in $config_file"
        else
            # Check if there's a commented I2C line
            if grep -q "^[[:space:]]*#.*dtparam=i2c_arm" "$config_file"; then
                # Uncomment existing I2C line
                print_status "Uncommenting existing I2C configuration..."
                sed -i 's/^[[:space:]]*#[[:space:]]*\(dtparam=i2c_arm=on\)/\1/' "$config_file"
            else
                # Add new I2C configuration
                print_status "Adding I2C configuration to $config_file..."
                
                # Check if there's a [all] section
                if grep -q "^\[all\]" "$config_file"; then
                    # Add after [all] section
                    sed -i '/^\[all\]/a dtparam=i2c_arm=on' "$config_file"
                else
                    # Just append to end of file
                    echo "" >> "$config_file"
                    echo "# Enable I2C interface" >> "$config_file"
                    echo "dtparam=i2c_arm=on" >> "$config_file"
                fi
            fi
            
            print_status "I2C enabled in $config_file"
        fi
        
        # Configure I2C kernel module
        print_status "Configuring I2C kernel module..."
        
        # Check if i2c-dev is already in /etc/modules
        if grep -q "^[[:space:]]*i2c-dev" /etc/modules; then
            print_status "i2c-dev already in /etc/modules"
        else
            # Check if there's a commented i2c-dev line
            if grep -q "^[[:space:]]*#.*i2c-dev" /etc/modules; then
                print_status "Uncommenting i2c-dev in /etc/modules..."
                sed -i 's/^[[:space:]]*#[[:space:]]*\(i2c-dev\)/\1/' /etc/modules
            else
                print_status "Adding i2c-dev to /etc/modules..."
                echo "i2c-dev" >> /etc/modules
            fi
        fi
        
        # Try to load module now (will fail gracefully if already loaded)
        print_status "Loading i2c-dev kernel module..."
        if modprobe i2c-dev 2>/dev/null; then
            print_status "i2c-dev module loaded successfully."
        else
            print_warning "Could not load i2c-dev module now. It will load after reboot."
        fi
        
        # Try to load i2c-bcm2835 for Raspberry Pi (optional)
        if modprobe i2c-bcm2835 2>/dev/null; then
            print_status "i2c-bcm2835 module loaded successfully."
        fi
    fi
    
    # Final verification
    print_status "Verifying I2C configuration..."
    sleep 1
    
    if is_i2c_enabled; then
        print_status "✓ I2C interface is now enabled and working!"
        
        # Show detected I2C buses
        if command -v i2cdetect &> /dev/null; then
            echo ""
            print_status "Detected I2C buses:"
            i2cdetect -l 2>/dev/null || true
            echo ""
        fi
        
        # Show loaded modules
        print_status "Loaded I2C modules:"
        lsmod | grep i2c || print_warning "No I2C modules shown (may require reboot)"
        
    else
        print_warning "I2C configuration completed, but interface is not active yet."
        print_warning "A reboot is required for changes to take effect."
    fi
    
    print_status "I2C configuration completed."
}

# Function to install required packages
install_dependencies() {
    print_status "Installing required packages..."
    
    # Update package list
    apt-get update
    
    # Install Python3 and pip if not already installed
    apt-get install -y python3 python3-pip
    
    # Install system dependencies for the Python packages
    apt-get install -y python3-dev build-essential libfreetype6-dev libjpeg-dev libopenjp2-7
    
    # Install I2C tools and SMBus library for INA219 sensor
    apt-get install -y i2c-tools python3-smbus
    
    # Install DejaVu fonts (required by the application)
    apt-get install -y fonts-dejavu-core
    
    # Install Python packages
    pip3 install psutil luma.oled Pillow --break-system-packages
    
    print_status "Dependencies installed successfully."
}

# Function to create installation directory and copy files
install_files() {
    print_status "Installing application files..."
    
    # Create installation directory
    mkdir -p "$INSTALL_DIR"
    
    # Copy Python script
    cp "$SCRIPT_DIR/${SERVICE_NAME}.py" "$INSTALL_DIR/"
    
    # Copy assets directory
    cp -r "$SCRIPT_DIR/assets" "$INSTALL_DIR/"
    
    # Set appropriate permissions
    chown -R root:root "$INSTALL_DIR"
    chmod +x "$INSTALL_DIR/${SERVICE_NAME}.py"
    chmod -R 755 "$INSTALL_DIR"
    
    print_status "Application files installed successfully."
}

# Function to install and enable service
install_service() {
    print_status "Installing systemd service..."
    
    # Copy service file
    cp "$SCRIPT_DIR/${SERVICE_NAME}.service" "$SERVICE_FILE"
    
    # Set appropriate permissions for service file
    chmod 644 "$SERVICE_FILE"
    
    # Reload systemd
    systemctl daemon-reload
    
    # Enable service to start on boot
    systemctl enable "$SERVICE_NAME"
    
    print_status "Service installed and enabled successfully."
}

# Function to start service
start_service() {
    print_status "Starting service..."
    systemctl start "$SERVICE_NAME"
    
    # Wait a moment for service to start
    sleep 3
    
    # Check if service is running
    if systemctl is-active --quiet "$SERVICE_NAME"; then
        print_status "Service started successfully."
        print_status "Service is running and will start automatically on boot."
    else
        print_error "Failed to start service."
        print_warning "Checking service status and logs..."
        systemctl status "$SERVICE_NAME" --no-pager
        echo ""
        echo "Recent logs:"
        journalctl -u "$SERVICE_NAME" --no-pager -n 10
        exit 1
    fi
}

# Function to remove installation
remove_installation() {
    print_status "Removing ${SERVICE_NAME} installation..."
    
    # Stop and disable service
    if systemctl is-active --quiet "$SERVICE_NAME"; then
        print_status "Stopping service..."
        systemctl stop "$SERVICE_NAME"
    fi
    
    if systemctl is-enabled --quiet "$SERVICE_NAME" 2>/dev/null; then
        print_status "Disabling service..."
        systemctl disable "$SERVICE_NAME"
    fi
    
    # Remove service file
    if [ -f "$SERVICE_FILE" ]; then
        rm "$SERVICE_FILE"
        print_status "Service file removed."
    fi
    
    # Reload systemd
    systemctl daemon-reload
    systemctl reset-failed 2>/dev/null || true
    
    # Remove installation directory
    if [ -d "$INSTALL_DIR" ]; then
        rm -rf "$INSTALL_DIR"
        print_status "Installation directory removed."
    fi
    
    print_status "${SERVICE_NAME} has been completely removed."
}

# Function to remove installation and all dependencies
remove_all() {
    print_status "Removing ${SERVICE_NAME} installation and all dependencies..."
    
    # First remove the service installation
    remove_installation
    
    # Remove Python packages
    print_status "Removing Python packages..."
    pip3 uninstall -y psutil luma.oled Pillow 2>/dev/null || true
    
    # Ask user if they want to remove system packages
    echo ""
    print_warning "The following system packages will be removed:"
    print_warning "  - python3-dev"
    print_warning "  - build-essential"
    print_warning "  - libfreetype6-dev"
    print_warning "  - libjpeg-dev"
    print_warning "  - libopenjp2-7"
    print_warning "  - i2c-tools"
    print_warning "  - python3-smbus"
    print_warning "  - fonts-dejavu-core"
    echo ""
    print_warning "Note: python3 and python3-pip will NOT be removed as they may be used by other applications."
    echo ""
    read -p "Do you want to remove these system packages? (y/N): " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        print_status "Removing system packages..."
        apt-get remove -y python3-dev build-essential libfreetype6-dev libjpeg-dev libopenjp2-7 i2c-tools python3-smbus fonts-dejavu-core
        apt-get autoremove -y
        print_status "System packages removed."
    else
        print_status "System packages kept."
    fi
    
    print_status "============================================"
    print_status "Complete removal finished!"
    print_status "============================================"
}

# Function to show service status
show_status() {
    echo "=== ${SERVICE_NAME} Service Status ==="
    if systemctl is-active --quiet "$SERVICE_NAME"; then
        print_status "Service is running"
    else
        print_warning "Service is not running"
    fi
    
    if systemctl is-enabled --quiet "$SERVICE_NAME" 2>/dev/null; then
        print_status "Service is enabled (will start on boot)"
    else
        print_warning "Service is not enabled"
    fi
    
    echo ""
    systemctl status "$SERVICE_NAME" --no-pager || true
    echo ""
    echo "=== Recent logs ==="
    journalctl -u "$SERVICE_NAME" --no-pager -n 20 || true
}

# Function to show usage
show_usage() {
    echo "Usage: $0 [install|remove|remove-all|status|restart|logs]"
    echo ""
    echo "Commands:"
    echo "  install    - Install the ${SERVICE_NAME} service"
    echo "  remove     - Remove the ${SERVICE_NAME} service completely"
    echo "  remove-all - Remove the ${SERVICE_NAME} service and all dependencies"
    echo "  status     - Show service status and recent logs"
    echo "  restart    - Restart the service"
    echo "  logs       - Show live logs from the service"
    echo ""
    echo "If no command is specified, 'install' will be executed."
}

# Main installation function
main_install() {
    print_status "Starting ${SERVICE_NAME} installation..."
    
    check_root
    
    # Check if already installed
    if [ -d "$INSTALL_DIR" ] || [ -f "$SERVICE_FILE" ]; then
        print_warning "${SERVICE_NAME} appears to be already installed."
        echo "Installation directory: $INSTALL_DIR"
        echo "Service file: $SERVICE_FILE"
        echo ""
        read -p "Do you want to continue and overwrite the existing installation? (y/N): " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            print_status "Installation cancelled."
            exit 0
        fi
        
        # Stop service if running
        if systemctl is-active --quiet "$SERVICE_NAME"; then
            print_status "Stopping existing service..."
            systemctl stop "$SERVICE_NAME"
        fi
    fi
    
    install_dependencies
    enable_i2c
    install_files
    install_service
    start_service
    
    print_status "============================================"
    print_status "Installation completed successfully!"
    print_status "============================================"
    print_status "Service name: $SERVICE_NAME"
    print_status "Installation path: $INSTALL_DIR"
    print_status "Service file: $SERVICE_FILE"
    print_status ""
    print_warning "IMPORTANT: A reboot is recommended for I2C changes to take full effect."
    print_warning "You can reboot now with: sudo reboot"
    print_status ""
    print_status "Useful commands:"
    print_status "  Check status: sudo systemctl status $SERVICE_NAME"
    print_status "  View logs: sudo journalctl -u $SERVICE_NAME -f"
    print_status "  Restart: sudo systemctl restart $SERVICE_NAME"
    print_status "  Stop: sudo systemctl stop $SERVICE_NAME"
    print_status "  Start: sudo systemctl start $SERVICE_NAME"
}

# Main script logic
case "${1:-install}" in
    install)
        main_install
        ;;
    remove)
        check_root
        remove_installation
        ;;
    remove-all)
        check_root
        remove_all
        ;;
    status)
        show_status
        ;;
    restart)
        check_root
        print_status "Restarting service..."
        systemctl restart "$SERVICE_NAME"
        sleep 3
        show_status
        ;;
    logs)
        print_status "Showing live logs (Press Ctrl+C to exit)..."
        journalctl -u "$SERVICE_NAME" -f
        ;;
    help|--help|-h)
        show_usage
        ;;
    *)
        print_error "Unknown command: $1"
        show_usage
        exit 1
        ;;
esac

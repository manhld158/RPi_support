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
RELEASES_DIR="/opt/${SERVICE_NAME}-releases"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
LOCK_FILE="/run/lock/${SERVICE_NAME}.install.lock"
HEALTH_FILE="/run/${SERVICE_NAME}/health"
FAN_CLI="/usr/local/bin/rpi-fan"
FAN_OVERLAY_MARKER="/proc/device-tree/rpi-support-fans"
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

acquire_operation_lock() {
    exec 9>"$LOCK_FILE"
    if ! flock -n 9; then
        print_error "Another install, removal or restart is running."
        return 1
    fi
}

check_fan_hardware() {
    if [ ! -e "$FAN_OVERLAY_MARKER" ]; then
        print_error "Fan PWM overlay is not active. Run sudo ./install.sh configure-fans, reboot, then rerun install."
        return 1
    fi
}

# Explicit one-time boot setup, kept separate from application rollback.
configure_fans() {
    check_root
    acquire_operation_lock || return 1
    if ! grep -aq 'Raspberry Pi 5' /proc/device-tree/model; then
        print_error "The three-fan overlay requires Raspberry Pi 5."
        return 1
    fi
    local config_file overlay_dir temporary_overlay
    if [ -f /boot/firmware/config.txt ]; then
        config_file=/boot/firmware/config.txt
        overlay_dir=/boot/firmware/overlays
    elif [ -f /boot/config.txt ]; then
        config_file=/boot/config.txt
        overlay_dir=/boot/overlays
    else
        print_error "Boot config.txt not found."
        return 1
    fi
    apt-get update
    apt-get install -y device-tree-compiler
    temporary_overlay="$(mktemp "$overlay_dir/.rpi-support-fans.XXXXXX")"
    if ! dtc -@ -I dts -O dtb -o "$temporary_overlay" "$SCRIPT_DIR/assets/rpi-support-fans-overlay.dts"; then
        rm -- "$temporary_overlay"
        return 1
    fi
    chmod 644 "$temporary_overlay"
    mv -f "$temporary_overlay" "$overlay_dir/rpi-support-fans.dtbo"
    if ! awk '
        BEGIN { section = "[all]"; enabled = 0 }
        { sub(/#.*/, ""); gsub(/^[[:space:]]+|[[:space:]]+$/, "") }
        /^\[/ { section = $0 }
        /^dtoverlay=rpi-support-fans$/ { if (section == "[all]") enabled = 1 }
        END { exit !enabled }
    ' "$config_file"; then
        printf '\n# rpi_support three-fan PWM on GPIO13/18/19\n[all]\ndtoverlay=rpi-support-fans\n' >> "$config_file"
    fi
    print_status "Fan overlay prepared. Reboot, then run sudo ./install.sh install."
}

# Function to check if I2C is already enabled
is_i2c_enabled() {
    # Check if I2C kernel module is loaded
    if lsmod | grep -q "^i2c_dev"; then
        # Check if I2C device files exist
        if [ -e /dev/i2c-1 ]; then
            return 0  # I2C is enabled
        fi
    fi
    return 1  # I2C is not enabled
}

# Preserve comments and append the exact module name only when it is absent.
configure_i2c_module() {
    local modules_file="$1"
    local module_pattern='^[[:space:]]*i2c-dev([[:space:]]*(#.*)?)?$'
    if ! grep -Eq "$module_pattern" "$modules_file"; then
        printf '\ni2c-dev\n' >> "$modules_file" || return 1
    fi
    if ! grep -Eq "$module_pattern" "$modules_file"; then
        print_error "Could not verify i2c-dev in $modules_file"
        return 1
    fi
}

# Function to check if I2C is enabled in config.txt
is_i2c_in_config() {
    local config_file="$1"
    
    if [ ! -f "$config_file" ]; then
        return 1  # File doesn't exist
    fi
    
    # Only trust an unconditional setting with no later I2C override.
    # A setting in a model-specific section may not apply to this Pi.
    awk '
        BEGIN { section = "[all]"; enabled = 0 }
        {
            sub(/#.*/, "")
            gsub(/^[[:space:]]+|[[:space:]]+$/, "")
        }
        /^\[/ { section = $0; next }
        /^dtparam[[:space:]]*=.*i2c_arm[[:space:]]*=/ {
            enabled = (section == "[all]" &&
                $0 ~ /^dtparam[[:space:]]*=[[:space:]]*i2c_arm[[:space:]]*=[[:space:]]*on$/)
        }
        END { exit !enabled }
    ' "$config_file"
}

# Append an explicit unconditional setting instead of editing conditional or
# commented settings. Repeated installation leaves a valid config unchanged.
configure_i2c_file() {
    local config_file="$1"

    if ! is_i2c_in_config "$config_file"; then
        printf '\n# Enable I2C interface for rpi_support\n[all]\ndtparam=i2c_arm=on\n' >> "$config_file" || return 1
    fi

    if ! is_i2c_in_config "$config_file"; then
        print_error "Could not verify I2C configuration in $config_file"
        return 1
    fi
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
    
    local manual_config_needed=true
    # Check if raspi-config is available (Raspberry Pi specific)
    if command -v raspi-config &> /dev/null; then
        print_status "Using raspi-config to enable I2C..."
        
        # Enable I2C using raspi-config (non-interactive mode)
        if raspi-config nonint do_i2c 0; then
            print_status "I2C interface enabled via raspi-config."
            manual_config_needed=false
        else
            print_error "Failed to enable I2C via raspi-config. Trying manual method..."
        fi
        
    else
        print_warning "raspi-config not found. Using manual configuration..."
    fi

    if [ "$manual_config_needed" = true ]; then
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
        
        configure_i2c_file "$config_file"
        print_status "I2C configuration verified in $config_file"
        
        # Configure I2C kernel module
        print_status "Configuring I2C kernel module..."
        
        configure_i2c_module /etc/modules
        print_status "i2c-dev module configuration verified."
        
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
    apt-get install -y python3 python3-pip python3-venv
    
    # Install system dependencies for the Python packages
    apt-get install -y python3-dev build-essential libfreetype6-dev libjpeg-dev libopenjp2-7
    
    # SMBus Python support is installed inside the application's isolated venv.
    apt-get install -y i2c-tools smartmontools
    
    # Install DejaVu fonts (required by the application)
    apt-get install -y fonts-dejavu-core
    
    print_status "Dependencies installed successfully."
}

# Function to create installation directory and copy files
install_files() {
    print_status "Installing application files..."
    
    # Create installation directory
    mkdir -p "$RELEASES_DIR"
    NEW_RELEASE="$(mktemp -d "$RELEASES_DIR/release.XXXXXX")"
    
    # Copy Python script
    cp "$SCRIPT_DIR/${SERVICE_NAME}.py" "$NEW_RELEASE/"
    
    # Copy assets directory
    cp -r "$SCRIPT_DIR/assets" "$NEW_RELEASE/"
    
    # Set appropriate permissions
    chown -R root:root "$NEW_RELEASE"
    chmod -R u=rwX,go=rX "$NEW_RELEASE"
    chmod 755 "$NEW_RELEASE"
    chmod 755 "$NEW_RELEASE/assets/rpi-fan"

    # Keep the venv at its permanent path; moving a venv breaks script shebangs.
    # Do not inherit distro Python packages: their metadata can fail pip check
    # (for example apt-listchanges/ debconf on Debian/OpenMediaVault).
    python3 -m venv "$NEW_RELEASE/.venv"
    "$NEW_RELEASE/.venv/bin/python" -m pip install psutil luma.oled Pillow smbus2
    "$NEW_RELEASE/.venv/bin/python" -m pip check
    "$NEW_RELEASE/.venv/bin/python" -c 'import psutil, smbus2; from luma.oled.device import ssd1306; from PIL import Image, ImageDraw, ImageFont'
    "$NEW_RELEASE/.venv/bin/python" -m py_compile "$NEW_RELEASE/${SERVICE_NAME}.py" "$NEW_RELEASE/assets/INA219.py" "$NEW_RELEASE/assets/fan_control.py"
    
    print_status "Application files installed successfully."
}

# Function to install and enable service
install_service() {
    print_status "Installing systemd service..."
    
    # Copy service file
    cp "$SCRIPT_DIR/${SERVICE_NAME}.service" "$TRANSACTION_DIR/new.service"
    
    # Set appropriate permissions for service file
    chmod 644 "$TRANSACTION_DIR/new.service"
    SERVICE_CHANGED=true
    mv -f "$TRANSACTION_DIR/new.service" "$SERVICE_FILE"
    
    # Reload systemd
    systemctl daemon-reload
    
    # Enable service to start on boot
    systemctl enable "$SERVICE_NAME"
    
    print_status "Service installed and enabled successfully."
}

# Diagnostic commands may fail themselves; always try both before rollback.
show_service_diagnostics() {
    print_warning "Service status:"
    systemctl status "$SERVICE_NAME" --no-pager || true
    print_warning "Recent service logs:"
    journalctl -u "$SERVICE_NAME" --no-pager -n 20 || print_warning "Could not read service logs."
}

# Function to start service
start_service() {
    print_status "Starting service..."
    if ! systemctl start "$SERVICE_NAME"; then
        print_error "Failed to start the new service."
        show_service_diagnostics
        return 1
    fi
    
    # Require continuous activity beyond the startup logo, without restarts.
    local initial_restarts current_restarts attempt
    initial_restarts="$(systemctl show "$SERVICE_NAME" -p NRestarts --value)"
    for attempt in {1..10}; do
        sleep 1
        current_restarts="$(systemctl show "$SERVICE_NAME" -p NRestarts --value)"
        if ! systemctl is-active --quiet "$SERVICE_NAME" || [ "$current_restarts" != "$initial_restarts" ]; then
            print_error "New service failed its startup check."
            show_service_diagnostics
            return 1
        fi
    done
    if ! service_has_fresh_stats; then
        print_error "Service is running but has not reported successful, recent system statistics."
        show_service_diagnostics
        return 1
    fi
    print_status "Service passed the startup check."
}

service_has_fresh_stats() {
    local health_pid health_time extra main_pid now
    [ -r "$HEALTH_FILE" ] || return 1
    read -r health_pid health_time extra < "$HEALTH_FILE" || return 1
    [[ "$health_pid" =~ ^[1-9][0-9]*$ && "$health_time" =~ ^[0-9]{1,10}$ ]] || return 1
    [ -z "$extra" ] || return 1
    main_pid="$(systemctl show "$SERVICE_NAME" -p MainPID --value)" || return 1
    [ "$health_pid" = "$main_pid" ] || return 1
    now="$(date +%s)" || return 1
    (( 10#$health_time <= now && now - 10#$health_time <= 5 ))
}

# Delete only release directories owned by this installer, never external targets.
remove_release() {
    local release="$1"
    case "$release" in
        "$RELEASES_DIR"/release.*)
            [ "$(dirname "$release")" = "$RELEASES_DIR" ] || return 1
            rm -rf -- "$release"
            ;;
        *) return 1 ;;
    esac
}

rollback_install() {
    print_warning "Restoring the previous installation..."
    if [ "${FAN_CLI_CREATED:-false}" = true ] && [ -L "$FAN_CLI" ] && [ "$(readlink "$FAN_CLI")" = "$INSTALL_DIR/assets/rpi-fan" ]; then
        rm -- "$FAN_CLI" || return 1
    fi
    # Do not change files under a service that could not be stopped.
    if [ "$SERVICE_EXISTED" = true ] || [ "$SERVICE_CHANGED" = true ]; then
        systemctl stop "$SERVICE_NAME" || return 1
    fi
    if [ -e "$TRANSACTION_DIR/application" ] || [ -L "$TRANSACTION_DIR/application" ] || [ "$APPLICATION_SAVED" = true ]; then
        if [ -L "$INSTALL_DIR" ] && [ "$(readlink "$INSTALL_DIR")" = "$NEW_RELEASE" ]; then
            rm -- "$INSTALL_DIR" || return 1
        fi
        if [ -n "$PREVIOUS_RELEASE" ]; then
            ln -s "$PREVIOUS_RELEASE" "$INSTALL_DIR" || return 1
        else
            mv "$TRANSACTION_DIR/application" "$INSTALL_DIR" || return 1
        fi
    elif [ -L "$INSTALL_DIR" ] && [ "$(readlink "$INSTALL_DIR")" = "$NEW_RELEASE" ]; then
        rm -- "$INSTALL_DIR" || return 1
    fi
    if [ "$SERVICE_CHANGED" = true ]; then
        systemctl disable "$SERVICE_NAME" || return 1
        if [ -f "$TRANSACTION_DIR/previous.service" ]; then
            cp -p "$TRANSACTION_DIR/previous.service" "$SERVICE_FILE" || return 1
        else
            rm -- "$SERVICE_FILE" || return 1
        fi
    fi
    systemctl daemon-reload || return 1
    if [ "$SERVICE_WAS_ENABLED" = true ]; then
        systemctl enable "$SERVICE_NAME" || return 1
    fi
    systemctl reset-failed "$SERVICE_NAME" 2>/dev/null || true
    if [ "$SERVICE_WAS_ACTIVE" = true ]; then
        systemctl start "$SERVICE_NAME" || return 1
    fi
    print_status "Previous installation restored."
}

finish_install() {
    local result=$? rollback_failed=false
    trap - EXIT INT TERM
    if [ "$INSTALL_COMMITTED" != true ] && [ "$SWITCH_STARTED" = true ]; then
        rollback_install || rollback_failed=true
    fi
    if [ "$rollback_failed" = true ]; then
        print_error "Automatic recovery failed. Backups retained at $TRANSACTION_DIR; new release retained at $NEW_RELEASE"
        exit 1
    fi
    if [ "$INSTALL_COMMITTED" = true ]; then
        if [ -n "$PREVIOUS_RELEASE" ]; then
            remove_release "$PREVIOUS_RELEASE" || print_warning "Previous release retained at $PREVIOUS_RELEASE"
        fi
    elif [ -n "$NEW_RELEASE" ]; then
        remove_release "$NEW_RELEASE" || print_warning "Unused release retained at $NEW_RELEASE"
    fi
    if [ -n "$TRANSACTION_DIR" ]; then
        rm -rf -- "$TRANSACTION_DIR" || print_warning "Backup retained at $TRANSACTION_DIR"
    fi
    exit "$result"
}

# Function to remove installation
remove_installation() {
    print_status "Removing ${SERVICE_NAME} installation..."
    acquire_operation_lock || return 1

    # LoadState also finds units still loaded after their file was deleted.
    # An auto-restarting unit is not necessarily "active", but must be stopped.
    local load_state
    if ! load_state="$(systemctl show "$SERVICE_NAME" -p LoadState --value)" || [ -z "$load_state" ]; then
        print_error "Could not determine service state. No files were removed."
        return 1
    fi
    if [ "$load_state" != not-found ]; then
        print_status "Stopping service and cancelling pending restarts..."
        if ! systemctl stop "$SERVICE_NAME"; then
            print_error "Could not stop service. No files were removed."
            show_service_diagnostics
            return 1
        fi
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
    systemctl reset-failed "$SERVICE_NAME" 2>/dev/null || true
    
    # Remove installation directory
    if [ -L "$FAN_CLI" ] && [ "$(readlink "$FAN_CLI")" = "$INSTALL_DIR/assets/rpi-fan" ]; then
        rm -- "$FAN_CLI"
    fi
    if [ -e "$INSTALL_DIR" ] || [ -L "$INSTALL_DIR" ]; then
        rm -rf "$INSTALL_DIR"
        print_status "Installation directory removed."
    fi

    # Virtualenvs belong to the application, so remove managed releases too.
    local release
    for release in "$RELEASES_DIR"/release.*; do
        if [ -d "$release" ]; then
            remove_release "$release"
        fi
    done
    if [ -d "$RELEASES_DIR" ]; then
        rmdir "$RELEASES_DIR" 2>/dev/null || print_warning "Recovery files retained in $RELEASES_DIR"
    fi
    
    print_status "${SERVICE_NAME} has been completely removed."
}

# Function to remove installation and all dependencies
remove_all() {
    print_warning "remove-all is an alias for remove; shared dependencies are retained."
    remove_installation
}

# Function to show service status
restart_service() {
    acquire_operation_lock || return 1
    print_status "Restarting service..."
    if ! systemctl restart "$SERVICE_NAME"; then
        show_service_diagnostics
        return 1
    fi
    sleep 3
    show_status
}

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
    echo "Usage: $0 [install|configure-fans|remove|remove-all|status|restart|logs]"
    echo ""
    echo "Commands:"
    echo "  install    - Install the ${SERVICE_NAME} service"
    echo "  configure-fans - Prepare Raspberry Pi 5 GPIO13/18/19 PWM (reboot required)"
    echo "  remove     - Remove the ${SERVICE_NAME} service completely"
    echo "  remove-all - Alias for remove (shared dependencies are retained)"
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

    # Reject incomplete sources before installing packages or stopping anything.
    local source_file
    for source_file in "${SERVICE_NAME}.py" "${SERVICE_NAME}.service" assets/INA219.py assets/fan_control.py assets/rpi-fan assets/rpi-support-fans-overlay.dts; do
        if [ ! -f "$SCRIPT_DIR/$source_file" ]; then
            print_error "Missing source file: $SCRIPT_DIR/$source_file"
            return 1
        fi
    done
    if [ ! -d "$SCRIPT_DIR/assets/png" ]; then
        print_error "Missing assets/png directory."
        return 1
    fi

    # Serialize installers so a second update cannot overwrite recovery state.
    acquire_operation_lock || return 1
    check_fan_hardware || return 1
    if [ -e "$FAN_CLI" ] || [ -L "$FAN_CLI" ]; then
        if [ ! -L "$FAN_CLI" ] || [ "$(readlink "$FAN_CLI")" != "$INSTALL_DIR/assets/rpi-fan" ]; then
            print_error "$FAN_CLI already exists and is not managed by this installer."
            return 1
        fi
    fi
    
    # Check if already installed
    if [ -e "$INSTALL_DIR" ] || [ -L "$INSTALL_DIR" ] || [ -f "$SERVICE_FILE" ]; then
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
        
    fi

    NEW_RELEASE=""
    PREVIOUS_RELEASE=""
    TRANSACTION_DIR=""
    APPLICATION_SAVED=false
    FAN_CLI_CREATED=false
    SERVICE_CHANGED=false
    SERVICE_EXISTED=false
    SWITCH_STARTED=false
    INSTALL_COMMITTED=false
    SERVICE_WAS_ACTIVE=false
    SERVICE_WAS_ENABLED=false
    trap finish_install EXIT
    trap 'exit 130' INT
    trap 'exit 143' TERM
    
    install_dependencies
    install_files
    enable_i2c

    TRANSACTION_DIR="$(mktemp -d "$RELEASES_DIR/.transaction.XXXXXX")"
    if [ -f "$SERVICE_FILE" ]; then
        cp -p "$SERVICE_FILE" "$TRANSACTION_DIR/previous.service"
    fi
    if systemctl is-active --quiet "$SERVICE_NAME"; then SERVICE_WAS_ACTIVE=true; fi
    if systemctl is-enabled --quiet "$SERVICE_NAME"; then SERVICE_WAS_ENABLED=true; fi
    if [ -L "$INSTALL_DIR" ]; then
        PREVIOUS_RELEASE="$(readlink -f "$INSTALL_DIR")"
    fi

    # All downloads and validation have completed. Only now interrupt the service.
    SWITCH_STARTED=true
    if systemctl cat "$SERVICE_NAME" >/dev/null 2>&1; then
        SERVICE_EXISTED=true
        systemctl stop "$SERVICE_NAME"
    fi
    if [ -e "$INSTALL_DIR" ] || [ -L "$INSTALL_DIR" ]; then
        mv "$INSTALL_DIR" "$TRANSACTION_DIR/application"
        APPLICATION_SAVED=true
    fi
    ln -s "$NEW_RELEASE" "$INSTALL_DIR"
    install_service
    if [ ! -L "$FAN_CLI" ]; then
        mkdir -p "$(dirname "$FAN_CLI")"
        FAN_CLI_CREATED=true
        ln -s "$INSTALL_DIR/assets/rpi-fan" "$FAN_CLI"
    fi
    start_service
    INSTALL_COMMITTED=true
    
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
    print_status "  Fan override (5 minutes): sudo rpi-fan set main 60 disk 70 convection 80"
    print_status "  Check status: sudo systemctl status $SERVICE_NAME"
    print_status "  View logs: sudo journalctl -u $SERVICE_NAME -f"
    print_status "  Restart: sudo systemctl restart $SERVICE_NAME"
    print_status "  Stop: sudo systemctl stop $SERVICE_NAME"
    print_status "  Start: sudo systemctl start $SERVICE_NAME"
}

# Main script logic
case "${1:-install}" in
    configure-fans)
        configure_fans
        ;;
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
        restart_service
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

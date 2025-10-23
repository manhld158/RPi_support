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

# Function to install required packages
install_dependencies() {
    print_status "Installing required packages..."
    
    # Update package list
    apt-get update
    
    # Install Python3 and pip if not already installed
    apt-get install -y python3 python3-pip
    
    # Install system dependencies for the Python packages
    apt-get install -y python3-dev build-essential libfreetype6-dev libjpeg-dev libopenjp2-7 libtiff5
    
    # Install Python packages
    pip3 install psutil luma.oled pillow
    
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
    echo "Usage: $0 [install|remove|status|restart|logs]"
    echo ""
    echo "Commands:"
    echo "  install  - Install the ${SERVICE_NAME} service"
    echo "  remove   - Remove the ${SERVICE_NAME} service completely"
    echo "  status   - Show service status and recent logs"
    echo "  restart  - Restart the service"
    echo "  logs     - Show live logs from the service"
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

#!/bin/bash

# Ask for CURRENT_USER
read -p "Please enter the username for the PyCloudDaemon installation: " CURRENT_USER
if [ -z "$CURRENT_USER" ]; then
    log_message "Username cannot be empty. Exiting."
    exit 1
fi

# --- Configuration ---
INSTALL_DIR="/home/$CURRENT_USER/.local/share/PyCloudDaemon"
VENV_DIR="$INSTALL_DIR/venv"
MAIN_PY_SOURCE="main.py" # Assumes main.py is in the same directory as the install.sh script
USR_BIN_SCRIPT="/usr/bin/pycloud-daemon"
SYSTEMD_SERVICE_FILE="/etc/systemd/system/pycloud-daemon.service"
LOG_FILE="$INSTALL_DIR/daemon_log.txt" # Ensure this path is consistent with main.py

# --- Functions ---
log_message() {
    echo "$(date +'%Y-%m-%d %H:%M:%S') - $1" | tee -a "$LOG_FILE"
}

check_root() {
    if [[ $EUID -ne 0 ]]; then
        log_message "This script must be run as root. Please use sudo."
        exit 1
    fi
}

install_dependencies() {
    log_message "Installing Python dependencies..."
    "$VENV_DIR/bin/pip" install psutil distro || { log_message "Failed to install Python dependencies."; exit 1; }
    log_message "Python dependencies installed."
}

create_daemon_wrapper() {
    log_message "Creating PyCloudMonitorDaemon wrapper script in $USR_BIN_SCRIPT..."
    cat << EOF | tee "$USR_BIN_SCRIPT"
#!/bin/bash
DAEMON_DIR="$INSTALL_DIR"
source "\$DAEMON_DIR/venv/bin/activate"
exec python3 "\$DAEMON_DIR/main.py"
EOF
    chmod +x "$USR_BIN_SCRIPT" || { log_message "Failed to make wrapper script executable."; exit 1; }
    log_message "Wrapper script created."
}

create_systemd_service() {
    log_message "Creating systemd service file in $SYSTEMD_SERVICE_FILE..."
    cat << EOF | tee "$SYSTEMD_SERVICE_FILE"
[Unit]
Description=PyCloudMonitorDaemon
After=network.target

[Service]
Type=simple
ExecStart=$USR_BIN_SCRIPT
Restart=on-failure
User=$CURRENT_USER
Group=$CURRENT_USER
WorkingDirectory=$INSTALL_DIR
StandardOutput=append:$LOG_FILE
StandardError=append:$LOG_FILE

[Install]
WantedBy=multi-user.target
EOF
    log_message "Systemd service file created."
}

# --- Main Installation Logic ---
check_root

log_message "Starting PyCloudMonitorDaemon installation for user: $CURRENT_USER..."

# 1. Create installation directory
log_message "Creating installation directory: $INSTALL_DIR"
mkdir -p "$INSTALL_DIR" || { log_message "Failed to create installation directory."; exit 1; }
chown "$CURRENT_USER":"$CURRENT_USER" "$INSTALL_DIR" || { log_message "Failed to set ownership of installation directory."; exit 1; }

# 2. Copy main.py
log_message "Copying $MAIN_PY_SOURCE to $INSTALL_DIR..."
cp "$MAIN_PY_SOURCE" "$INSTALL_DIR/" || { log_message "Failed to copy main.py."; exit 1; }
chown "$CURRENT_USER":"$CURRENT_USER" "$INSTALL_DIR/$MAIN_PY_SOURCE" || { log_message "Failed to set ownership of main.py."; exit 1; }

# 3. Create Python Virtual Environment
log_message "Creating Python virtual environment in $VENV_DIR..."
python3 -m venv "$VENV_DIR" || { log_message "Failed to create virtual environment. Ensure python3-venv is installed."; exit 1; }
chown -R "$CURRENT_USER":"$CURRENT_USER" "$VENV_DIR" || { log_message "Failed to set ownership of virtual environment."; exit 1; }
log_message "Virtual environment created."

# 4. Install Python dependencies
install_dependencies

# 5. Create /usr/bin/pycloud-daemon wrapper script
create_daemon_wrapper

# 6. Create systemd service file
create_systemd_service

# 7. Reload systemd, enable, and start the service
log_message "Reloading systemd daemon..."
systemctl daemon-reload || { log_message "Failed to reload systemd daemon."; exit 1; }

log_message "Enabling pycloud-daemon.service..."
systemctl enable pycloud-daemon.service || { log_message "Failed to enable service."; exit 1; }

log_message "Starting pycloud-daemon.service..."
systemctl start pycloud-daemon.service || { log_message "Failed to start service."; exit 1; }

log_message "PyCloudMonitorDaemon installation complete!"
log_message "Service status:"
systemctl status pycloud-daemon.service
log_message "You can view logs in: $LOG_FILE"

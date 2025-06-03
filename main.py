import socket
import time
import os
import psutil
import platform
import hashlib # For basic password hashing
import json
import threading
from datetime import datetime, timedelta

# --- Configuration ---
HOST = '0.0.0.0'  # Listen on all available interfaces
PORT = 65432
PASSWORD_HASH = hashlib.sha256("your_secret_password".encode()).hexdigest() # Store hash, not plain text
LOG_FILE = "daemon_log.txt"
UPTIME_TRACKING_FILE = "uptime_data.json" # To store boot times for percentage calculation

# --- Helper Functions ---

def log_message(message):
    """Logs a message to the log file and prints to console."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    full_message = f"[{timestamp}] {message}"
    print(full_message)
    with open(LOG_FILE, "a") as f:
        f.write(full_message + "\n")

def get_uptime_seconds():
    """Gets system uptime in seconds."""
    return time.time() - psutil.boot_time()

def load_boot_times():
    """Loads historical boot times from a file."""
    if os.path.exists(UPTIME_TRACKING_FILE):
        try:
            with open(UPTIME_TRACKING_FILE, "r") as f:
                return json.load(f)
        except json.JSONDecodeError:
            return []
    return []

def save_boot_time():
    """Saves the current boot time."""
    boot_times = load_boot_times()
    current_boot_time_ts = psutil.boot_time()
    # Avoid duplicate entries if daemon restarts without reboot
    if not any(abs(bt - current_boot_time_ts) < 60 for bt in boot_times): # Check if already logged within a minute
        boot_times.append(current_boot_time_ts)
    with open(UPTIME_TRACKING_FILE, "w") as f:
        json.dump(boot_times, f)

def get_uptime_percentage_last_7_days():
    """Calculates uptime percentage for the last 7 days."""
    now = time.time()
    seven_days_ago = now - timedelta(days=7).total_seconds()
    boot_times = load_boot_times()
    current_boot_time = psutil.boot_time()

    total_time_in_period = timedelta(days=7).total_seconds()
    up_time_in_period = 0

    # Consider current uptime session
    session_start = max(current_boot_time, seven_days_ago)
    up_time_in_period += (now - session_start)

    if not boot_times: # If no historical data, assume current uptime is the only data point
        if current_boot_time > seven_days_ago:
             # Uptime is how long it's been up since boot, capped at 7 days
            uptime_this_session = now - current_boot_time
            relevant_uptime = min(uptime_this_session, total_time_in_period)
            return (relevant_uptime / total_time_in_period) * 100
        else: # Booted more than 7 days ago, so 100% uptime in the last 7 days
            return 100.0

    if current_boot_time >= seven_days_ago:
        actual_uptime_seconds_in_window = now - current_boot_time
    else: # Booted before the 7-day window started
        actual_uptime_seconds_in_window = total_time_in_period

    return (actual_uptime_seconds_in_window / total_time_in_period) * 100 if total_time_in_period > 0 else 0


def get_system_info():
    """Gathers all required system information."""
    uptime_sec = get_uptime_seconds()
    uptime_hours = uptime_sec // 3600
    uptime_minutes = (uptime_sec % 3600) // 60

    info = {
        "uptime_string": f"{int(uptime_hours)}h {int(uptime_minutes)}m",
        "uptime_percentage_last_7_days": f"{get_uptime_percentage_last_7_days():.2f}%",
        "ram_usage": {
            "total_gb": f"{psutil.virtual_memory().total / (1024**3):.2f}",
            "available_gb": f"{psutil.virtual_memory().available / (1024**3):.2f}",
            "percent_used": f"{psutil.virtual_memory().percent:.2f}%"
        },
        "cpu_usage_percent": f"{psutil.cpu_percent(interval=1):.2f}%", # Blocking call for 1 sec
        "disk_usage_root": {
            "total_gb": f"{psutil.disk_usage('/').total / (1024**3):.2f}",
            "used_gb": f"{psutil.disk_usage('/').used / (1024**3):.2f}",
            "free_gb": f"{psutil.disk_usage('/').free / (1024**3):.2f}",
            "percent_used": f"{psutil.disk_usage('/').percent:.2f}%"
        },
        "kernel_version": platform.release() if platform.system() == "Linux" else "N/A",
        "distro_name": "N/A"
    }
    if platform.system() == "Linux":
        try:
            # Try to get distro information
            # This might need the 'distro' package: pip install distro
            import distro
            info["distro_name"] = distro.name(pretty=True)
        except ImportError:
            try:
                # Fallback for older systems or if 'distro' is not installed
                info["distro_name"] = " ".join(platform.linux_distribution())
            except AttributeError: # platform.linux_distribution() removed in Python 3.8+
                 # Further fallback if platform.linux_distribution() is unavailable
                try:
                    with open("/etc/os-release") as f:
                        for line in f:
                            if line.startswith("PRETTY_NAME="):
                                info["distro_name"] = line.split("=")[1].strip().strip('"')
                                break
                except FileNotFoundError:
                    info["distro_name"] = "Linux (Unknown Distro)"
        except Exception as e:
            info["distro_name"] = f"Linux (Error fetching distro: {e})"


    return info

def handle_client(conn, addr):
    """Handles a single client connection."""
    log_message(f"Connected by {addr}")
    try:
        # 1. Authentication
        conn.sendall(b"Password: ")
        password_attempt = conn.recv(1024).strip().decode()
        if hashlib.sha256(password_attempt.encode()).hexdigest() != PASSWORD_HASH:
            conn.sendall(b"Authentication failed.\n")
            log_message(f"Authentication failed for {addr}")
            return

        conn.sendall(b"Authentication successful. Sending data...\n")
        log_message(f"Authentication successful for {addr}")

        # 2. Send Data
        system_data = get_system_info()
        data_json = json.dumps(system_data, indent=4)
        conn.sendall(data_json.encode())
        log_message(f"Sent data to {addr}")

    except socket.timeout:
        log_message(f"Connection timed out for {addr}")
    except Exception as e:
        log_message(f"Error handling client {addr}: {e}")
    finally:
        conn.close()
        log_message(f"Connection closed with {addr}")

def daemon_main():
    """Main daemon loop."""
    # Save current boot time when daemon starts
    save_boot_time()

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1) # Allow reuse of address
    try:
        s.bind((HOST, PORT))
        s.listen()
        log_message(f"Daemon listening on {HOST}:{PORT}")
        while True:
            conn, addr = s.accept()
            conn.settimeout(60) # Set a timeout for client operations
            # Handle each client in a new thread for concurrency
            client_thread = threading.Thread(target=handle_client, args=(conn, addr))
            client_thread.daemon = True # Allow main program to exit even if threads are running
            client_thread.start()
    except OSError as e:
        log_message(f"OSError: {e}. Could not bind to {HOST}:{PORT}. Port might be in use or permission denied.")
    except Exception as e:
        log_message(f"Critical daemon error: {e}")
    finally:
        log_message("Daemon shutting down.")
        s.close()

# --- Daemonization (Very Basic - for proper daemonization, use systemd or other tools) ---
def become_daemon():
    if os.fork(): # First fork: parent exits
        os._exit(0)
    os.setsid() # Create new session and process group
    if os.fork(): # Second fork: child exits, grandchild becomes daemon
        os._exit(0)

    log_message("Daemon process started.")
    daemon_main()


if __name__ == "__main__":
    log_message("Starting daemon (foreground mode for this example)...")
    daemon_main()

import socket
import time
import os
import psutil
import platform
import hashlib # For basic password hashing
import json
import threading
import subprocess # For running external commands
from datetime import datetime, timedelta

# --- Configuration ---
HOST = '0.0.0.0'  # Listen on all available interfaces
PORT = 65432
# IMPORTANT: Change this password in a real deployment!
PASSWORD_HASH = hashlib.sha256("your_secret_password".encode()).hexdigest() # Store hash, not plain text
LOG_FILE = "daemon_log.txt"
UPTIME_TRACKING_FILE = "uptime_data.json" # To store boot times for percentage calculation

# --- Helper Functions ---

def log_message(message, is_error=False):
    """
    Logs a message to the log file (only if is_error is True) and prints to console.
    """
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    full_message = f"[{timestamp}] {message}"
    print(full_message) # Always print to console

    if is_error:
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
            log_message(f"Error decoding JSON from {UPTIME_TRACKING_FILE}. Returning empty list.", is_error=True)
            return []
    return []

def save_boot_time():
    """Saves the current boot time."""
    boot_times = load_boot_times()
    current_boot_time_ts = psutil.boot_time()
    # Avoid duplicate entries if daemon restarts without reboot
    if not any(abs(bt - current_boot_time_ts) < 60 for bt in boot_times): # Check if already logged within a minute
        boot_times.append(current_boot_time_ts)
    try:
        with open(UPTIME_TRACKING_FILE, "w") as f:
            json.dump(boot_times, f)
    except IOError as e:
        log_message(f"Error saving boot time to {UPTIME_TRACKING_FILE}: {e}", is_error=True)


def get_uptime_percentage_last_7_days():
    """Calculates uptime percentage for the last 7 days."""
    now = time.time()
    seven_days_ago = now - timedelta(days=7).total_seconds()
    current_boot_time = psutil.boot_time()

    total_time_in_period = timedelta(days=7).total_seconds()

    # If the system booted *before* the 7-day window started,
    # it has been up for the entire 7-day window.
    if current_boot_time <= seven_days_ago:
        return 100.0

    # If the system booted *within* the 7-day window,
    # the uptime is from the boot time until now.
    uptime_this_session_in_window = now - current_boot_time

    # Safety check in case of clock issues or very recent boot
    if uptime_this_session_in_window < 0:
        uptime_this_session_in_window = 0

    return (uptime_this_session_in_window / total_time_in_period) * 100 if total_time_in_period > 0 else 0


def get_system_info_data():
    """Gathers all required system information. Returns a dict."""
    uptime_sec = get_uptime_seconds()
    uptime_hours = uptime_sec // 3600
    uptime_minutes = (uptime_sec % 3600) // 60

    info = {
        "uptime_string": f"{int(uptime_hours)}h {int(uptime_minutes)}m",
        "uptime_seconds_current_session": int(uptime_sec),
        "uptime_percentage_last_7_days": f"{get_uptime_percentage_last_7_days():.2f}%",
        "ram_usage": {
            "total_gb": f"{psutil.virtual_memory().total / (1024**3):.2f}",
            "available_gb": f"{psutil.virtual_memory().available / (1024**3):.2f}",
            "percent_used": f"{psutil.virtual_memory().percent:.2f}%"
        },
        "cpu_usage_percent": f"{psutil.cpu_percent(interval=0.5):.2f}%", # Reduced interval slightly
        "disk_usage_root": {
            "total_gb": f"{psutil.disk_usage('/').total / (1024**3):.2f}",
            "used_gb": f"{psutil.disk_usage('/').used / (1024**3):.2f}",
            "free_gb": f"{psutil.disk_usage('/').free / (1024**3):.2f}",
            "percent_used": f"{psutil.disk_usage('/').percent:.2f}%"
        },
        "kernel_version": platform.release() if platform.system() == "Linux" else "N/A",
        "distro_name": "N/A",
        "platform_system": platform.system(),
        "platform_node": platform.node(),
    }
    if platform.system() == "Linux":
        try:
            import distro
            info["distro_name"] = distro.name(pretty=True)
        except ImportError:
            try:
                # Fallback for older systems or if 'distro' is not installed
                # platform.linux_distribution() was removed in Python 3.8
                if hasattr(platform, 'linux_distribution'):
                    info["distro_name"] = " ".join(platform.linux_distribution()).strip()
                if not info["distro_name"] or info["distro_name"].lower() == "n/a": # Further fallback
                    with open("/etc/os-release") as f:
                        for line in f:
                            if line.startswith("PRETTY_NAME="):
                                info["distro_name"] = line.split("=", 1)[1].strip().strip('"')
                                break
            except FileNotFoundError:
                info["distro_name"] = "Linux (Unknown Distro - /etc/os-release not found)"
            except Exception as e:
                log_message(f"Linux (Error fetching distro: {e})", is_error=True)
                info["distro_name"] = f"Linux (Error fetching distro: {e})"
        except Exception as e:
             log_message(f"Linux (Distro lookup error: {e})", is_error=True)
             info["distro_name"] = f"Linux (Distro lookup error: {e})"
    return info

def send_response(conn, data):
    """Sends a JSON response to the client."""
    try:
        response_json = json.dumps(data)
        conn.sendall(response_json.encode('utf-8'))
    except Exception as e:
        log_message(f"Error sending response: {e}", is_error=True)

# --- Remotely Callable Functions ---
def handle_get_system_info(conn):
    """Handles the 'get_system_info' action."""
    # Only log errors, so no log_message for success
    system_data = get_system_info_data()
    send_response(conn, {"status": "success", "data": system_data})

def handle_reboot_system(conn, addr):
    """Handles the 'reboot' action."""
    # SECURITY: Ensure the user running this script has sudo NOPASSWD for 'reboot'
    # or run this script as root (less recommended).
    command = ["sudo", "reboot", "now"]
    try:
        send_response(conn, {"status": "success", "message": "Reboot command issued. Server will shut down."})
        # Give a moment for the message to be sent before rebooting
        time.sleep(1)
        subprocess.run(command, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        # The script will likely terminate here if reboot is successful
    except FileNotFoundError:
        log_message(f"Error: 'sudo' command not found. Cannot reboot.", is_error=True)
        send_response(conn, {"status": "error", "message": "Reboot command 'sudo' not found on server."})
    except subprocess.CalledProcessError as e:
        log_message(f"Error during reboot: {e.stderr.decode() if e.stderr else e}", is_error=True)
        # May not be able to send this if reboot has already started partially
        try:
            send_response(conn, {"status": "error", "message": f"Reboot failed: {e.stderr.decode() if e.stderr else e}"})
        except:
            pass # Connection might be dead
    except Exception as e:
        log_message(f"An unexpected error occurred during reboot: {e}", is_error=True)
        try:
            send_response(conn, {"status": "error", "message": f"An unexpected error occurred during reboot: {e}"})
        except:
            pass


def handle_update_system(conn, addr):
    """Handles the 'update' action using UnifiedUpdater."""
    # Ensure UnifiedUpdater is in PATH or use its full path.
    # Example: command = ["/path/to/your/UnifiedUpdater"]
    command = ["UnifiedUpdater"] # Assuming it's in PATH
    try:
        # Using a timeout for the updater process can be a good idea
        process = subprocess.run(command, capture_output=True, text=True, check=False, timeout=3600) # 1 hour timeout

        stdout = process.stdout.strip()
        stderr = process.stderr.strip()

        if process.returncode == 0:
            # Success, no log_message to file, but still print to console
            print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] UnifiedUpdater completed successfully.")
            send_response(conn, {"status": "success", "message": "Update process completed.", "output": stdout, "error": stderr if stderr else ""})
        else:
            log_message(f"UnifiedUpdater failed. Return code: {process.returncode}\nStdout:\n{stdout}\nStderr:\n{stderr}", is_error=True)
            send_response(conn, {"status": "error", "message": "Update process failed.", "output": stdout, "error": stderr, "return_code": process.returncode})

    except FileNotFoundError:
        log_message(f"Error: '{command[0]}' command not found. Cannot update.", is_error=True)
        send_response(conn, {"status": "error", "message": f"Update command '{command[0]}' not found on server."})
    except subprocess.TimeoutExpired:
        log_message(f"Error: UnifiedUpdater command timed out.", is_error=True)
        send_response(conn, {"status": "error", "message": "Update process timed out."})
    except subprocess.CalledProcessError as e: # Should be caught by check=False and returncode check, but as a fallback
        log_message(f"Error during update (CalledProcessError): {e.stderr if e.stderr else e}", is_error=True)
        send_response(conn, {"status": "error", "message": f"Update failed: {e.stderr if e.stderr else e}"})
    except Exception as e:
        log_message(f"An unexpected error occurred during update: {e}", is_error=True)
        send_response(conn, {"status": "error", "message": f"An unexpected error occurred during update: {e}"})


# --- Main Client Handler ---
def handle_client(conn, addr):
    """Handles a single client connection."""
    # Success, so no log_message to file, but still print to console
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Connected by {addr}")
    try:
        # 1. Authentication
        conn.sendall(b"Password: ")
        password_attempt_bytes = conn.recv(1024)
        if not password_attempt_bytes:
            log_message(f"Client {addr} disconnected before sending password.", is_error=True)
            return
        password_attempt = password_attempt_bytes.strip().decode('utf-8')

        if hashlib.sha256(password_attempt.encode('utf-8')).hexdigest() != PASSWORD_HASH:
            conn.sendall(b"Authentication failed.\n")
            log_message(f"Authentication failed for {addr}", is_error=True)
            return

        conn.sendall(b"Authentication successful. Send JSON command.\n")
        # Success, so no log_message to file, but still print to console
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Authentication successful for {addr}. Waiting for command.")

        # 2. Receive Action Command
        request_bytes = conn.recv(2048) # Increased buffer size for JSON
        if not request_bytes:
            log_message(f"Client {addr} disconnected before sending command.", is_error=True)
            return

        request_str = request_bytes.decode('utf-8').strip()
        # Success, so no log_message to file, but still print to console
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Received command string from {addr}: {request_str}")

        try:
            request_data = json.loads(request_str)
            action = request_data.get("action")
        except json.JSONDecodeError:
            log_message(f"Invalid JSON received from {addr}: {request_str}", is_error=True)
            send_response(conn, {"status": "error", "message": "Invalid JSON command."})
            return
        except Exception as e: # Catch any other error during parsing
            log_message(f"Error processing command from {addr}: {e}", is_error=True)
            send_response(conn, {"status": "error", "message": f"Could not parse command: {e}"})
            return


        # 3. Dispatch Action
        if action == "get_system_info":
            handle_get_system_info(conn)
        elif action == "reboot":
            handle_reboot_system(conn, addr)
        elif action == "update":
            handle_update_system(conn, addr)
        else:
            log_message(f"Unknown action '{action}' requested by {addr}", is_error=True)
            send_response(conn, {"status": "error", "message": f"Unknown action: {action}"})

    except socket.timeout:
        log_message(f"Connection timed out for {addr}", is_error=True)
    except BrokenPipeError:
        log_message(f"Client {addr} disconnected abruptly (BrokenPipeError).", is_error=True)
    except ConnectionResetError:
        log_message(f"Connection reset by {addr}.", is_error=True)
    except Exception as e:
        log_message(f"Error handling client {addr}: {type(e).__name__} - {e}", is_error=True)
        # Attempt to send an error to the client if the connection is still somewhat alive
        try:
            send_response(conn, {"status": "error", "message": "An unexpected server error occurred."})
        except Exception as send_e:
            log_message(f"Could not send final error to client {addr}: {send_e}", is_error=True)
    finally:
        conn.close()
        # Success, so no log_message to file, but still print to console
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Connection closed with {addr}")

def daemon_main():
    """Main daemon loop."""
    save_boot_time() # Save current boot time when daemon starts or restarts

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind((HOST, PORT))
        s.listen()
        # Success, so no log_message to file, but still print to console
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Daemon listening on {HOST}:{PORT}")
        while True:
            try:
                conn, addr = s.accept()
                conn.settimeout(60) # Timeout for individual socket operations
                client_thread = threading.Thread(target=handle_client, args=(conn, addr))
                client_thread.daemon = True
                client_thread.start()
            except Exception as e: # Catch errors in the accept loop itself
                log_message(f"Error accepting connection: {e}", is_error=True)
                time.sleep(1) # Avoid fast spinning on persistent accept errors
    except OSError as e:
        log_message(f"OSError: {e}. Could not bind to {HOST}:{PORT}. Port might be in use or permission denied.", is_error=True)
    except Exception as e:
        log_message(f"Critical daemon error in main loop: {e}", is_error=True)
    finally:
        log_message("Daemon shutting down.", is_error=True) # Log daemon shutdown to ensure it's recorded
        s.close()

# --- Daemonization (Basic) ---
def become_daemon():
    # This basic daemonization is often not robust enough for production.
    # Consider using systemd, supervisor, or a library like 'python-daemon'.
    if platform.system() == "Windows":
        log_message("Daemonization (fork) is not supported on Windows. Running in foreground.", is_error=True)
        daemon_main()
        return

    try:
        pid = os.fork()
        if pid > 0:
            # Exit first parent
            os._exit(0)
    except OSError as e:
        log_message(f"fork #1 failed: {e.errno} ({e.strerror})", is_error=True)
        os._exit(1)

    os.chdir("/")
    os.setsid()
    os.umask(0)

    try:
        pid = os.fork()
        if pid > 0:
            # Exit second parent
            os._exit(0)
    except OSError as e:
        log_message(f"fork #2 failed: {e.errno} ({e.strerror})", is_error=True)
        os._exit(1)

    # Success, so no log_message to file, but still print to console
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Daemon process started.")

    # Redirect standard file descriptors (optional but good practice for daemons)
    # sys.stdout.flush()
    # sys.stderr.flush()
    # si = open(os.devnull, 'r')
    # so = open(os.devnull, 'a+') # Or a log file
    # se = open(os.devnull, 'a+')
    # os.dup2(si.fileno(), sys.stdin.fileno())
    # os.dup2(so.fileno(), sys.stdout.fileno()) # Be careful if you are also logging to console via print
    # os.dup2(se.fileno(), sys.stderr.fileno())

    daemon_main()


if __name__ == "__main__":
    # For testing, you might want to run it directly without full daemonization:
    # Success, so no log_message to file, but still print to console
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Starting daemon (foreground mode for this example)...")
    # To run as a daemon (on Linux/macOS):
    # become_daemon()
    daemon_main() # For direct execution

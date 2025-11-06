import os
import platform
import subprocess
import time
import threading
from datetime import datetime
import socket
import requests
from flask import Flask, render_template, jsonify

# === Configuration ===
WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # Optional webhook for alerts
AUTO_REFRESH = int(os.getenv("AUTO_REFRESH", 5))  # Dashboard refresh rate (seconds)
PING_COUNT = int(os.getenv("PING_COUNT", 1))  # Number of ping attempts per check
PING_PARAM = "-n" if platform.system().lower() == "windows" else "-c"

# === State Tracking ===
status_data = {}

app = Flask(__name__)

# === Utility Functions ===
def ping_host(ip):
    """Ping host and return True if reachable."""
    try:
        result = subprocess.run(
            ["ping", PING_PARAM, str(PING_COUNT), ip],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        return result.returncode == 0
    except Exception:
        return False


def tcp_check(ip, ports=(80, 443)):
    """Fallback TCP check for hosts that block ICMP ping."""
    for port in ports:
        try:
            with socket.create_connection((ip, port), timeout=2):
                return True
        except Exception:
            continue
    return False


def is_host_up(ip):
    """Combined ICMP + TCP check."""
    if ping_host(ip):
        return True
    return tcp_check(ip)


def send_webhook(message):
    """Send an alert to webhook if configured."""
    if not WEBHOOK_URL:
        return
    try:
        payload = {"text": message}
        resp = requests.post(WEBHOOK_URL, json=payload)
        if resp.status_code not in (200, 201, 202, 204):
            print(f"⚠️ Webhook failed: {resp.status_code}")
    except Exception as e:
        print(f"[ERROR] Sending webhook: {e}")


# === Monitoring Logic ===
def monitor():
    """Main monitoring loop that runs continuously."""
    # Load targets from ips.txt
    with open("ips.txt") as f:
        targets = []
        for line in f:
            if line.strip() and "," in line:
                h, ip = line.strip().split(",", 1)
                targets.append((h.strip(), ip.strip()))

    # Initialize host statuses
    for h, ip in targets:
        status_data[ip] = {"hostname": h, "is_up": None, "last_change": "Never"}

    print("🚀 Starting real-time monitoring...")
    while True:
        for h, ip in targets:
            is_up = is_host_up(ip)
            prev = status_data[ip]["is_up"]
            now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

            # Detect state change
            if prev is None:
                # Initial state
                status_data[ip].update(is_up=is_up, last_change=now)
                continue

            if is_up != prev:
                status_data[ip]["is_up"] = is_up
                status_data[ip]["last_change"] = now
                if is_up:
                    send_webhook(f"✅ **RECOVERY:** {h} ({ip}) is back UP")
                    print(f"[{now}] ✅ {h} ({ip}) is back UP")
                else:
                    send_webhook(f"🚨 **ALERT:** {h} ({ip}) is DOWN")
                    print(f"[{now}] 🚨 {h} ({ip}) is DOWN")

        time.sleep(1)  # 1-second interval for near-live updates


# === Flask Web Dashboard ===
@app.route("/")
def index():
    # Sort DOWN hosts first
    rows = sorted(
        [
            {
                "hostname": v["hostname"],
                "ip": ip,
                "is_up": v["is_up"],
                "last_change": v["last_change"]
            }
            for ip, v in status_data.items()
        ],
        key=lambda x: (x["is_up"] is True, x["hostname"])
    )
    return render_template(
        "index.html",
        status=rows,
        last_updated=datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
        auto_refresh=AUTO_REFRESH
    )


@app.route("/status.json")
def status_json():
    """JSON endpoint for API use or external monitoring."""
    return jsonify(status_data)


# === Start Monitor Thread and Web Server ===
if __name__ == "__main__":
    threading.Thread(target=monitor, daemon=True).start()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)

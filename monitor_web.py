import os
import platform
import subprocess
import time
import threading
from datetime import datetime
from flask import Flask, render_template, jsonify, request
import requests
import socket

# Environment settings
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
TCP_PORTS = os.getenv("TCP_PORTS", "")  # Comma-separated, e.g., "80,443"
AUTO_REFRESH = int(os.getenv("AUTO_REFRESH", 5))  # seconds

PING_COUNT = 1
PING_PARAM = "-n" if platform.system().lower() == "windows" else "-c"

# Track host states
status_data = {}
alert_sent = {}

app = Flask(__name__)

def ping_host(ip):
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

def tcp_check(ip, port):
    try:
        with socket.create_connection((ip, port), timeout=3):
            return True
    except Exception:
        return False

def send_webhook(message):
    if not WEBHOOK_URL:
        print("[WARN] No WEBHOOK_URL set")
        return
    try:
        payload = {"text": message}
        resp = requests.post(WEBHOOK_URL, json=payload)
        if resp.status_code in (200, 201, 202, 204):
            print("✅ Alert sent")
        else:
            print(f"❌ Webhook failed: {resp.status_code}")
    except Exception as e:
        print(f"[ERROR] {e}")

def monitor():
    # Load targets from ips.txt
    with open("ips.txt") as f:
        targets = []
        for line in f:
            if line.strip() and "," in line:
                h, ip = line.strip().split(",", 1)
                targets.append((h.strip(), ip.strip()))

    # Initialize state
    for h, ip in targets:
        status_data[ip] = {"hostname": h, "is_up": True, "last_change": "Never", "tcp_ok": True}
        alert_sent[ip] = False

    tcp_ports = [int(p.strip()) for p in TCP_PORTS.split(",") if p.strip()]

    while True:
        for h, ip in targets:
            ping_ok = ping_host(ip)
            tcp_ok = all(tcp_check(ip, port) for port in tcp_ports) if tcp_ports else True
            is_up = ping_ok and tcp_ok
            prev = status_data[ip]["is_up"]

            # Update status
            status_data[ip].update(is_up=is_up, last_change=datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"), tcp_ok=tcp_ok)

            # Send alerts if status changed
            if is_up and prev is False and alert_sent[ip]:
                send_webhook(f"✅ **RECOVERY:** {h} ({ip}) is back UP")
                alert_sent[ip] = False
            elif not is_up and prev is True:
                send_webhook(f"🚨 **ALERT:** {h} ({ip}) is DOWN")
                alert_sent[ip] = True

        time.sleep(1)  # tiny delay to prevent CPU overuse

# Start monitoring in background thread
threading.Thread(target=monitor, daemon=True).start()

# Flask routes
@app.route("/")
def index():
    # Sort DOWN hosts to the top
    rows = sorted(
        [
            {"hostname": v["hostname"], "ip": ip, "is_up": v["is_up"], "last_change": v["last_change"], "tcp_ok": v.get("tcp_ok", True)}
            for ip, v in status_data.items()
        ],
        key=lambda x: x["is_up"]
    )
    return render_template("index.html", status=rows, last_updated=datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"), auto_refresh=AUTO_REFRESH)

@app.route("/status.json")
def status_json():
    return jsonify(status_data)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))

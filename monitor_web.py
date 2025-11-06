import os
import platform
import subprocess
import time
import threading
from datetime import datetime
from flask import Flask, render_template, jsonify, request
import requests

# Load settings from environment
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
PING_COUNT = int(os.getenv("PING_COUNT", 3))
GRACE_PERIOD = int(os.getenv("GRACE_PERIOD", 300))     # seconds
AUTO_REFRESH = int(os.getenv("AUTO_REFRESH", 30))      # seconds

PING_PARAM = "-n" if platform.system().lower() == "windows" else "-c"

# Track host states
status_data = {}
down_since = {}
alert_sent = {}

app = Flask(__name__)

def ping_host(ip):
    """Return True if host is reachable via ping."""
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
    """Constantly monitor all hosts."""
    # Load targets from ips.txt
    with open("ips.txt") as f:
        targets = []
        for line in f:
            if line.strip() and "," in line:
                h, ip = line.strip().split(",", 1)
                targets.append((h.strip(), ip.strip()))

    # Initialize state
    for h, ip in targets:
        status_data[ip] = {"hostname": h, "ip": ip, "is_up": True, "last_change": "Never"}
        alert_sent[ip] = False

    while True:
        now = time.time()
        for h, ip in targets:
            is_up = ping_host(ip)
            prev = status_data[ip]["is_up"]

            if is_up:
                status_data[ip].update(is_up=True, last_change=datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"))
                down_since.pop(ip, None)
                if prev is False and alert_sent[ip]:
                    send_webhook(f"✅ **RECOVERY:** {h} ({ip}) is back UP")
                    alert_sent[ip] = False
            else:
                if ip not in down_since:
                    down_since[ip] = now
                if prev and (now - down_since[ip] >= GRACE_PERIOD) and not alert_sent[ip]:
                    send_webhook(f"🚨 **ALERT:** {h} ({ip}) has been DOWN for {GRACE_PERIOD//60}+ minutes")
                    status_data[ip].update(is_up=False, last_change=datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"))
                    alert_sent[ip] = True
            status_data[ip]["is_up"] = is_up

        time.sleep(1)  # constantly check

# Start monitoring in a background thread
threading.Thread(target=monitor, daemon=True).start()

@app.route("/")
def index():
    """Render the dashboard."""
    rows = list(status_data.values())
    # DOWN hosts first
    rows.sort(key=lambda x: x["is_up"])
    dark_mode = request.args.get("dark") == "1"
    return render_template(
        "index.html",
        status=rows,
        last_updated=datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
        auto_refresh=AUTO_REFRESH,
        dark_mode=dark_mode
    )

@app.route("/status.json")
def status_json():
    """API endpoint for host status."""
    rows = list(status_data.values())
    rows.sort(key=lambda x: x["is_up"])
    return jsonify({
        "status": rows,
        "last_updated": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
        "auto_refresh": AUTO_REFRESH
    })

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))

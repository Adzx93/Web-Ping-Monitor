import os
import platform
import subprocess
import time
import threading
from datetime import datetime
from flask import Flask, render_template, jsonify
import requests
import schedule

# Load settings from environment
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", 60))  # seconds
PING_COUNT = int(os.getenv("PING_COUNT", 3))
GRACE_PERIOD = int(os.getenv("GRACE_PERIOD", 300))     # seconds
DAILY_HOUR_UTC = int(os.getenv("DAILY_HOUR_UTC", 9))
AUTO_REFRESH = int(os.getenv("AUTO_REFRESH", 30))

PING_PARAM = "-n" if platform.system().lower() == "windows" else "-c"

# Track host states
status_data = {}   # {ip: {hostname, is_up, last_change}}
down_since = {}    # {ip: timestamp when first seen down}
alert_sent = {}    # {ip: bool}

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

def daily_summary(targets):
    down_hosts = []
    for hostname, ip in targets:
        st = status_data.get(ip, {})
        if not st.get("is_up", True):
            down_hosts.append(f"❌ {hostname} ({ip})")
    if down_hosts:
        message = "⚠️ **Daily Summary — Hosts DOWN:**\n" + "\n".join(down_hosts)
        send_webhook(message)

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
        status_data[ip] = {"hostname": h, "is_up": True, "last_change": "Never"}
        alert_sent[ip] = False

    # Schedule daily summary
    schedule.every().day.at(f"{DAILY_HOUR_UTC:02d}:00").do(daily_summary, targets)

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
                # Only mark as DOWN after grace period
                if now - down_since[ip] >= GRACE_PERIOD:
                    if not alert_sent[ip]:
                        send_webhook(f"🚨 **ALERT:** {h} ({ip}) has been DOWN for {GRACE_PERIOD//60}+ minutes")
                        alert_sent[ip] = True
                    status_data[ip].update(is_up=False, last_change=datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"))

        schedule.run_pending()
        time.sleep(CHECK_INTERVAL)

# Start background monitor thread
threading.Thread(target=monitor, daemon=True).start()

# Flask routes
@app.route("/")
def index():
    rows = [
        {"hostname": v["hostname"], "ip": ip, "is_up": v["is_up"], "last_change": v["last_change"]}
        for ip, v in status_data.items()
    ]
    return render_template("index.html", status=rows, last_updated=datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"), auto_refresh=AUTO_REFRESH)

@app.route("/status.json")
def status_json():
    return jsonify(status_data)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))

import os
import platform
import subprocess
import threading
import time
from datetime import datetime
from flask import Flask, render_template, jsonify
import requests
import schedule

# -------------------------------
# Configuration
# -------------------------------
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
PING_COUNT = int(os.getenv("PING_COUNT", 2))
GRACE_PERIOD = int(os.getenv("GRACE_PERIOD", 300))  # seconds before alert
DAILY_HOUR_UTC = int(os.getenv("DAILY_HOUR_UTC", 9))
PING_PARAM = "-n" if platform.system().lower() == "windows" else "-c"

# -------------------------------
# Runtime data
# -------------------------------
status_data = {}
down_since = {}
alert_sent = {}

app = Flask(__name__)

# -------------------------------
# Helpers
# -------------------------------

def ping_host(ip):
    """Ping a host and return True if reachable."""
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
    """Send message to webhook (Slack, Teams, Discord, etc)."""
    if not WEBHOOK_URL:
        print("[WARN] No WEBHOOK_URL set")
        return
    try:
        payload = {"text": message}
        resp = requests.post(WEBHOOK_URL, json=payload)
        if resp.status_code in (200, 201, 202, 204):
            print("✅ Webhook sent successfully")
        else:
            print(f"❌ Webhook failed: {resp.status_code}")
    except Exception as e:
        print(f"[ERROR sending webhook] {e}")


def daily_summary(targets):
    """Send a daily summary of hosts that are still down."""
    down_hosts = []
    for hostname, ip in targets:
        st = status_data.get(ip, {})
        if not st.get("is_up", True):
            down_hosts.append(f"❌ {hostname} ({ip})")

    if down_hosts:
        message = "⚠️ **Daily Summary — Hosts DOWN:**\n" + "\n".join(down_hosts)
        send_webhook(message)
    else:
        send_webhook("✅ **Daily Summary — All hosts are UP!**")


# -------------------------------
# Monitoring Logic
# -------------------------------

def monitor():
    """Main monitoring loop running in background thread."""
    with open("ips.txt") as f:
        targets = []
        for line in f:
            if line.strip() and "," in line:
                h, ip = line.strip().split(",", 1)
                targets.append((h.strip(), ip.strip()))

    # Initialize state
    for h, ip in targets:
        status_data[ip] = {
            "hostname": h,
            "ip": ip,
            "is_up": True,
            "last_change": "Never"
        }
        alert_sent[ip] = False

    # Schedule daily summary
    schedule.every().day.at(f"{DAILY_HOUR_UTC:02d}:00").do(daily_summary, targets)

    while True:
        now = time.time()

        for h, ip in targets:
            is_up = ping_host(ip)
            prev = status_data[ip]["is_up"]

            if is_up:
                if not prev:
                    status_data[ip].update(
                        is_up=True,
                        last_change=datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
                    )
                    send_webhook(f"✅ **RECOVERY:** {h} ({ip}) is back UP")
                    alert_sent[ip] = False
                down_since.pop(ip, None)
            else:
                if ip not in down_since:
                    down_since[ip] = now
                elif now - down_since[ip] >= GRACE_PERIOD and not alert_sent[ip]:
                    send_webhook(
                        f"🚨 **ALERT:** {h} ({ip}) has been DOWN for {GRACE_PERIOD // 60}+ minutes"
                    )
                    status_data[ip].update(
                        is_up=False,
                        last_change=datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
                    )
                    alert_sent[ip] = True

            status_data[ip]["is_up"] = is_up

        schedule.run_pending()
        # no interval — runs continuously
        time.sleep(1)


# -------------------------------
# Flask Web App
# -------------------------------

@app.route("/")
def index():
    """Render main dashboard."""
    rows = list(status_data.values())

    # Sort DOWN first
    rows.sort(key=lambda x: x["is_up"])

    return render_template(
        "index.html",
        status=rows,
        last_updated=datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    )


@app.route("/api/status")
def api_status():
    """Return live JSON data for AJAX dashboard updates."""
    rows = list(status_data.values())
    rows.sort(key=lambda x: x["is_up"])  # down first
    return jsonify({
        "status": rows,
        "last_updated": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    })


# -------------------------------
# Main Entry
# -------------------------------

if __name__ == "__main__":
    # Start monitoring in background
    threading.Thread(target=monitor, daemon=True).start()

    # Start Flask app
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))

import os
import platform
import subprocess
import time
import threading
import sqlite3
from datetime import datetime, timedelta
from flask import Flask, render_template, jsonify
import requests
import schedule

# Environment config
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", 10))  # seconds
PING_COUNT = int(os.getenv("PING_COUNT", 1))
GRACE_PERIOD = int(os.getenv("GRACE_PERIOD", 300))     # seconds
DAILY_HOUR_UTC = int(os.getenv("DAILY_HOUR_UTC", 9))
AUTO_REFRESH = int(os.getenv("AUTO_REFRESH", 10))

PING_PARAM = "-n" if platform.system().lower() == "windows" else "-c"

status_data = {}
down_since = {}
alert_sent = {}

app = Flask(__name__)

# --- Database setup ---
DB_FILE = "monitor_data.db"
def init_db():
    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        c.execute("""
        CREATE TABLE IF NOT EXISTS ping_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ip TEXT,
            hostname TEXT,
            is_up INTEGER,
            latency REAL,
            timestamp TEXT
        )
        """)
        conn.commit()

init_db()

# --- Helpers ---
def log_ping(ip, hostname, is_up, latency):
    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        c.execute("INSERT INTO ping_log (ip, hostname, is_up, latency, timestamp) VALUES (?, ?, ?, ?, ?)",
                  (ip, hostname, int(is_up), latency, datetime.utcnow().isoformat()))
        conn.commit()

def get_recent_history(ip, limit=30):
    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        c.execute("SELECT timestamp, latency, is_up FROM ping_log WHERE ip=? ORDER BY id DESC LIMIT ?", (ip, limit))
        rows = c.fetchall()
    rows.reverse()
    return [{"timestamp": r[0], "latency": r[1], "is_up": bool(r[2])} for r in rows]

def calc_uptime(ip, minutes=60):
    since = datetime.utcnow() - timedelta(minutes=minutes)
    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        c.execute("SELECT COUNT(*), SUM(is_up) FROM ping_log WHERE ip=? AND timestamp>=?",
                  (ip, since.isoformat()))
        total, up = c.fetchone()
    if not total:
        return 100.0
    return round((up / total) * 100, 2)

def ping_host(ip):
    try:
        result = subprocess.run(
            ["ping", PING_PARAM, str(PING_COUNT), ip],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=5
        )
        is_up = result.returncode == 0
        latency = None
        if is_up:
            # Try to extract latency (ms)
            for line in result.stdout.splitlines():
                if "time=" in line.lower():
                    latency = float(line.lower().split("time=")[1].split("ms")[0].strip())
                    break
        return is_up, latency or 0
    except Exception:
        return False, 0

def send_webhook(message):
    if not WEBHOOK_URL:
        print("[WARN] No WEBHOOK_URL set")
        return
    try:
        requests.post(WEBHOOK_URL, json={"text": message})
    except Exception as e:
        print(f"[ERROR] Webhook: {e}")

def daily_summary(targets):
    down_hosts = []
    for hostname, ip in targets:
        if not status_data.get(ip, {}).get("is_up", True):
            down_hosts.append(f"❌ {hostname} ({ip})")
    if down_hosts:
        send_webhook("⚠️ Daily Summary — Hosts DOWN:\n" + "\n".join(down_hosts))

# --- Main monitor loop ---
def monitor():
    with open("ips.txt") as f:
        targets = []
        for line in f:
            if line.strip() and "," in line:
                h, ip = line.strip().split(",", 1)
                targets.append((h.strip(), ip.strip()))

    for h, ip in targets:
        status_data[ip] = {"hostname": h, "is_up": True, "last_change": "Never"}
        alert_sent[ip] = False

    schedule.every().day.at(f"{DAILY_HOUR_UTC:02d}:00").do(daily_summary, targets)

    while True:
        now = time.time()
        for h, ip in targets:
            is_up, latency = ping_host(ip)
            prev = status_data[ip]["is_up"]

            log_ping(ip, h, is_up, latency)

            if is_up:
                status_data[ip].update(is_up=True, latency=latency, last_change=datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"))
                down_since.pop(ip, None)
                if prev is False and alert_sent[ip]:
                    send_webhook(f"✅ RECOVERY: {h} ({ip}) is back UP")
                    alert_sent[ip] = False
            else:
                if ip not in down_since:
                    down_since[ip] = now
                if prev and (now - down_since[ip] >= GRACE_PERIOD) and not alert_sent[ip]:
                    send_webhook(f"🚨 ALERT: {h} ({ip}) has been DOWN for {GRACE_PERIOD//60}+ mins")
                    status_data[ip].update(is_up=False, latency=0, last_change=datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"))
                    alert_sent[ip] = True
            status_data[ip]["is_up"] = is_up

        schedule.run_pending()
        time.sleep(CHECK_INTERVAL)

threading.Thread(target=monitor, daemon=True).start()

# --- Flask routes ---
@app.route("/")
def index():
    rows = []
    for ip, v in status_data.items():
        uptime = calc_uptime(ip, 60)
        rows.append({
            "hostname": v["hostname"],
            "ip": ip,
            "is_up": v["is_up"],
            "latency": v.get("latency", 0),
            "uptime": uptime,
            "last_change": v["last_change"]
        })
    return render_template("index.html", status=rows,
                           last_updated=datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
                           auto_refresh=AUTO_REFRESH)

@app.route("/status.json")
def status_json():
    return jsonify(status_data)

@app.route("/history/<ip>.json")
def history_json(ip):
    return jsonify(get_recent_history(ip))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))

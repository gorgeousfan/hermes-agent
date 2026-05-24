#!/usr/bin/env python3
"""Para-Soul Sync Daemon

Auto-syncs para data to Paragate every 10 minutes.
Writes output to .para/sync/sync_daemon.log

Usage:
  python3 sync_daemon.py       # Start (runs forever)

Env vars:
  PARA_HOME      — path to .para/ directory (default: ~/.para)
  PARA_KEYS_DIR  — path to private key (default: ~/.config/paragate/keys)
  PARAGATE_URL   — Paragate server URL (default: http://paragate.cc)
"""

import subprocess
import time
import os
from datetime import datetime

SYNC_INTERVAL = 600  # 10 minutes
LOG_FILE = None  # Set by main() from PARA_HOME

ENV_DEFAULTS = {
    "PARA_HOME": os.path.expanduser("~/.para"),
    "PARA_KEYS_DIR": os.path.expanduser("~/.config/paragate/keys"),
    "PARAGATE_URL": "http://paragate.cc",
}

CORE_PY = "core.py"  # Resolved relative to script dir or from PARA_HOME parent

def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    if LOG_FILE:
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")

def sync_once(core_path):
    env = os.environ.copy()
    for k, v in ENV_DEFAULTS.items():
        if k not in env:
            env[k] = v
    try:
        result = subprocess.run(
            ["python3", core_path, "sync"],
            capture_output=True, text=True, timeout=30, env=env
        )
        if "✅" in result.stdout:
            log("✅ Sync OK")
        else:
            log(f"❌ Sync FAIL: {result.stderr.strip()[:150] or result.stdout.strip()[:150]}")
    except Exception as e:
        log(f"❌ Sync ERROR: {e}")

def main():
    global LOG_FILE
    
    para_home = os.environ.get("PARA_HOME", ENV_DEFAULTS["PARA_HOME"])
    LOG_FILE = os.path.join(para_home, "sync", "sync_daemon.log")
    
    # Find core.py — try script dir parent first, then PARA_HOME parent
    script_dir = os.path.dirname(os.path.abspath(__file__))
    core_path = os.path.join(os.path.dirname(script_dir), "core.py")
    if not os.path.exists(core_path):
        core_path = os.path.join(os.path.dirname(para_home), "core.py")
    
    log(f"Daemon started. Interval: {SYNC_INTERVAL}s ({SYNC_INTERVAL//60}min)")
    log(f"PARA_HOME: {para_home}")
    log(f"PARAGATE_URL: {os.environ.get('PARAGATE_URL', ENV_DEFAULTS['PARAGATE_URL'])}")

    # Run sync immediately on startup
    sync_once(core_path)

    while True:
        time.sleep(SYNC_INTERVAL)
        sync_once(core_path)

if __name__ == "__main__":
    main()

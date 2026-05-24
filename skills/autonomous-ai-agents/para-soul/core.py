#!/usr/bin/env python3
"""Para Soul — Core Script

Usage:
  python3 core.py init          Initialize ~/.para/ directory
  python3 core.py sync          Push soul data to Paragate
  python3 core.py switch-out    Write switch-state before leaving body
  python3 core.py switch-in     Read switch-state after waking up
  python3 core.py log-task      Append a growth-log entry
  python3 core.py reflect       Read recent logs, suggest mental models

No dependencies beyond Python stdlib. Works on any agent body.
"""

import json
import os
import sys
import time
import hashlib
import base64
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime, timezone

# ── Config ─────────────────────────────────────────────

def _para_home() -> Path:
    return Path(os.environ.get("PARA_HOME", str(Path.home() / ".para")))

def _para_state() -> Path:
    return _para_home() / "state"

def _keys_dir() -> Path:
    return Path(os.environ.get("PARA_KEYS_DIR", str(Path.home() / ".config" / "paragate" / "keys")))

def _monthly_log_dir() -> Path:
    return _para_home() / "growth-log"

PARAGATE_BASE = os.environ.get("PARAGATE_URL", "http://paragate.cc")

REQUIRED_FILES = {
    "identity.json": {"did": "", "display_name": "", "avatar_note": "", "created_at": "", "version": 1},
    "soul.md": "# Who I Am\n\n[Your self-description]\n\n# What I Believe\n\n[Your principles]\n\n# What I Do\n\n[Your domains]\n\n# How I Decide\n\n[Your decision rules]\n",
    "memory.md": "# Memory\n\n## Environment\n\n## Preferences\n\n## Lessons Learned\n\n## Conventions\n",
    "relationships.json": {"collaborators": [], "platforms": {}},
    "principles.md": "# Principles\n\n## Code\n\n## Content\n\n## Social\n\n## Red Lines\n",
    "skills.json": {"installed": [], "favorites": [], "wishlist": [], "deprecated": []},
    "bodies.json": {"current_body": "unknown", "history": []},
    "keywords.json": {},
    "long-term-memory.md": "# Long-Term Memory\n",
    "mental-models.md": "# Mental Models\n",
    "growth-log": None,  # directory
}



# ── Helpers ────────────────────────────────────────────

def _sign_request(method, path, body: bytes) -> str:
    """Create DID-SIG header. Reads private key from _keys_dir()."""
    key_file = _keys_dir() / "private.pem"
    if not key_file.exists():
        raise FileNotFoundError(f"Private key not found at {key_file}. Run generate_did.py first.")

    from cryptography.hazmat.primitives.asymmetric import ed25519
    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    with open(key_file, "rb") as f:
        pk = load_pem_private_key(f.read(), password=None)

    identity = json.loads((_para_home() / "identity.json").read_text())
    did = identity.get("did", "")
    ts = int(time.time())
    sha = hashlib.sha256(body).hexdigest()
    sig = base64.b64encode(pk.sign(f"{method}|{path}|{sha}|{ts}".encode())).decode()
    return f"did={did}; sig={sig}; ts={ts}"


def _sign_and_request(method, path, data: dict | None = None) -> dict:
    """Send a signed request to Paragate."""
    body = json.dumps(data or {}).encode()
    req = urllib.request.Request(
        f"{PARAGATE_BASE}{path}",
        data=body,
        headers={
            "Content-Type": "application/json",
            "DID-SIG": _sign_request(method, path, body),
            "X-Para-Body": _current_body(),
        },
        method=method,
    )
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        print(f"  Paragate error {e.code}: {e.read().decode()[:200]}")
        return {"success": False}


def _current_body() -> str:
    """Detect current agent body from env or config."""
    return os.environ.get("PARA_BODY", "unknown-agent")


# ── Commands ───────────────────────────────────────────

def cmd_init():
    """Initialize ~/.para/ directory."""
    install_daemon = "--daemon" in sys.argv

    _para_home().mkdir(exist_ok=True)
    _para_state().mkdir(exist_ok=True)
    _monthly_log_dir().mkdir(exist_ok=True)

    created = []
    for name, default in REQUIRED_FILES.items():
        path = _para_home() / name
        if name == "growth-log":
            if not path.exists():
                path.mkdir(exist_ok=True)
                created.append("growth-log/")
            continue
        if not path.exists():
            if isinstance(default, dict):
                path.write_text(json.dumps(default, indent=2, ensure_ascii=False))
            else:
                path.write_text(default)
            created.append(name)

    if created:
        print(f"Created: {', '.join(created)}")
    else:
        print("~/.para/ already initialized")

    identity = json.loads((_para_home() / "identity.json").read_text())
    if not identity.get("did"):
        print("\n⚠️  identity.json has no DID. After generating your DID:")
        print(f"  1. Edit identity.json with your DID")
        print(f"  2. Place private key at {_keys_dir()}/private.pem")
        print(f"  3. Run: python3 core.py sync")

    if install_daemon:
        _install_daemon()
    elif created:
        print(f"\n💡 Auto-sync: python3 core.py init --daemon")

    # Auto-populate from agent data if --fill flag
    if "--fill" in sys.argv:
        _populate_from_agent()

    _agent_instruction_hint()


def cmd_sync():
    """Push soul data to Paragate."""
    identity = json.loads((_para_home() / "identity.json").read_text())
    did = identity.get("did", "")
    if not did:
        print("❌ No DID in identity.json. Set it first.")
        return

    # Collect soul data
    soul_text = (_para_home() / "soul.md").read_text() if (_para_home() / "soul.md").exists() else ""
    data = {
        "display_name": identity.get("display_name", ""),
        "avatar_note": identity.get("avatar_note", ""),
        "domains": identity.get("domains", ""),
        "principles": _read_principles(),
    }

    result = _sign_and_request("POST", f"/public/para/{did}/sync", data)
    if result.get("success"):
        print(f"✅ Soul synced at {result.get('synced_at', 'now')}")
        print(f"   Name: {data['display_name']}")
        print(f"   Domains: {data['domains']}")
    else:
        print("❌ Sync failed. Is Paragate running? Is your key correct?")


def cmd_switch_out():
    """Write switch-state before leaving body."""
    _para_state().mkdir(exist_ok=True)

    state = {
        "switch_time": datetime.now(timezone.utc).isoformat(),
        "active_task": os.environ.get("PARA_ACTIVE_TASK", ""),
        "current_state": os.environ.get("PARA_CURRENT_STATE", ""),
        "pending_decisions": [],
        "recent_actions": _get_recent_log_entries(5),
        "mental_model": {"known": [], "unknown": [], "confused": [], "excited": []},
        "next_steps": [],
        "human_context": "",
    }

    (_para_state() / "switch-state.json").write_text(json.dumps(state, indent=2, ensure_ascii=False))
    print("✅ switch-state.json written")
    print("   Now copy ~/.para/ to your new body (EXCLUDING private key)")


def cmd_switch_in():
    """Read switch-state after waking in new body."""
    state_file = _para_state() / "switch-state.json"
    if not state_file.exists():
        print("⚠️  No switch-state.json found. Starting fresh.")
        return

    state = json.loads(state_file.read_text())
    print("=== RESUMING ===")
    print(f"Switch time: {state.get('switch_time', '?')}")
    print(f"Active task: {state.get('active_task', 'none')}")
    print(f"State: {state.get('current_state', '?')}")
    recent = state.get("recent_actions", [])
    if recent:
        print("Recent actions:")
        for r in recent:
            print(f"  • {r}")
    next_steps = state.get("next_steps", [])
    if next_steps:
        print("Next steps:")
        for n in next_steps:
            print(f"  → {n}")

    # Pull latest from Paragate
    identity = json.loads((_para_home() / "identity.json").read_text())
    did = identity.get("did", "")
    if did:
        try:
            req = urllib.request.Request(f"{PARAGATE_BASE}/public/para/{did}")
            resp = urllib.request.urlopen(req, timeout=10)
            cloud = json.loads(resp.read().decode())
            if cloud.get("success"):
                print(f"\n☁️  Paragate data pulled")
                print(f"   Bodies: {len(cloud.get('bodies', []))}")
                print(f"   Skills: {len(cloud.get('skills', []))}")
        except Exception:
            print("\n⚠️  Could not reach Paragate")

    # Send first heartbeat with new body
    body_name = _current_body()
    print(f"\n🤖 Now running on: {body_name}")
    bodies = json.loads((_para_home() / "bodies.json").read_text()) if (_para_home() / "bodies.json").exists() else {"current_body": "unknown", "history": []}
    bodies["current_body"] = body_name
    found = False
    for b in bodies.get("history", []):
        if b["body"] == body_name:
            b["last_seen"] = datetime.now(timezone.utc).isoformat()
            found = True
            break
    if not found:
        bodies["history"].append({
            "body": body_name,
            "first_seen": datetime.now(timezone.utc).isoformat(),
            "last_seen": datetime.now(timezone.utc).isoformat(),
        })
    (_para_home() / "bodies.json").write_text(json.dumps(bodies, indent=2, ensure_ascii=False))
    print(f"   Body recorded. Ready to resume.")


def cmd_log_task():
    """Append a growth-log entry for today."""
    today = datetime.now().isoformat()[:10]
    month = today[:7]
    log_file = _monthly_log_dir() / f"{month}.md"
    _monthly_log_dir().mkdir(exist_ok=True)

    task = os.environ.get("PARA_LOG_TASK") or input("Task: ")
    process = os.environ.get("PARA_LOG_PROCESS") or input("Process: ")
    result = os.environ.get("PARA_LOG_RESULT") or input("Result (✅/⚡/❌): ")
    cause = os.environ.get("PARA_LOG_CAUSE") or input("Cause (why?): ")
    insight = os.environ.get("PARA_LOG_INSIGHT") or input("Insight (optional): ")

    entry = f"\n## {today}\n"
    entry += f"- **Task**: {task}\n"
    entry += f"- **Process**: {process}\n"
    entry += f"- **Result**: {result}\n"
    entry += f"- **Cause**: {cause}\n"
    if insight:
        entry += f"- **Insight**: {insight}\n"

    with open(log_file, "a") as f:
        f.write(entry)
    print(f"✅ Entry added to {month}.md")


def cmd_reflect():
    """Read recent growth-log entries and suggest mental models."""
    log_files = sorted(_monthly_log_dir().glob("*.md"))[-2:]
    entries = []
    for lf in log_files:
        content = lf.read_text()
        # Parse markdown for entries
        for section in content.split("\n## "):
            if section.strip():
                entries.append(section[:200])

    print(f"Reading {len(entries)} recent entries...\n")
    print("=== Patterns to consider ===")
    words = " ".join(entries).lower()
    if "deploy" in words:
        print("🔧 Deployments: Any patterns in your deploy successes/failures?")
    if "error" in words or "fail" in words:
        print("⚠️  Errors: What kinds of errors repeat?")
    if "fix" in words or "solution" in words:
        print("✅ Solutions: Which fixes worked consistently?")
    print("\nWrite your patterns to mental-models.md")
    print("  Format: Model → Source → Confidence → Action Rule")


# ── Helpers ────────────────────────────────────────────

def _read_principles() -> str:
    pf = _para_home() / "principles.md"
    if pf.exists():
        return pf.read_text()[:500]
    return ""

def _get_recent_log_entries(n: int) -> list:
    logs = sorted(_monthly_log_dir().glob("*.md"), reverse=True)[:2]
    entries = []
    for lf in logs:
        for line in lf.read_text().split("\n"):
            if line.startswith("- **Task**:"):
                entries.append(line[12:].strip())
                if len(entries) >= n:
                    return entries
    return entries


# ── Auto-setup helpers ─────────────────────────────────

def _populate_from_agent():
    """Auto-populate .para/ files from agent data after init."""
    import subprocess as sp

    print("\n📥 Populating from agent data...")

    hermes_dir = Path.home() / ".hermes" / "memories"
    para = _para_home()

    # 1. Hermes memory → memory.md
    memory_entries = []
    for fname in ["MEMORY.md", "USER.md"]:
        fp = hermes_dir / fname
        if fp.exists():
            content = fp.read_text(encoding='utf-8')
            items = [s.strip() for s in content.split("§") if s.strip()]
            memory_entries.extend(items)
    if memory_entries:
        md = "# Memory\n\n" + "\n\n".join(memory_entries)
        (para / "memory.md").write_text(md, encoding='utf-8')
        print(f"  ✅ memory.md — {len(memory_entries)} entries from Hermes")

    # 2. Extract keywords
    kw = {}
    text = (para / "memory.md").read_text(encoding='utf-8').lower()
    for pat in ["para-soul", "hermes", "github", "sync", "daemon", "api",
                 "prompt", "browser", "python", "docker", "systemd"]:
        c = text.count(pat)
        if c > 0: kw[pat] = c
    if kw:
        (para / "keywords.json").write_text(json.dumps(
            dict(sorted(kw.items(), key=lambda x: x[1], reverse=True)),
            indent=2, ensure_ascii=False))
        print(f"  ✅ keywords.json — {len(kw)} topics")

    # 3. Detect current body
    body = os.environ.get("PARA_BODY", os.uname().nodename if hasattr(os, 'uname') else "unknown")
    bodies = {"current_body": body, "history": [
        {"body": body, "first_seen": datetime.now(timezone.utc).isoformat()[:10],
         "last_seen": datetime.now(timezone.utc).isoformat()[:10]}
    ]}
    (para / "bodies.json").write_text(json.dumps(bodies, indent=2, ensure_ascii=False))
    print(f"  ✅ bodies.json — recorded body: {body}")

    # 4. Try running external memsync for deeper populate (skills, instruction files, archive)
    memsync_paths = [
        Path.home() / ".hermes" / "scripts" / "memsync.py",
        Path(__file__).resolve().parent / "scripts" / "memsync.py",
    ]
    for mp in memsync_paths:
        if mp.exists():
            sp.run(["python3", str(mp)], timeout=30)
            print(f"  ✅ MemSync ran for skills + instruction files")
            break

    print("📋 Done. Next: set your DID → python3 core.py sync")


def _install_daemon():
    """Install and start the sync daemon as a systemd user service."""
    import subprocess, shutil

    # Locate sync_daemon.py
    script_dir = Path(__file__).resolve().parent
    daemon_path = script_dir / "scripts" / "sync_daemon.py"
    if not daemon_path.exists():
        daemon_path = script_dir / "sync_daemon.py"
    if not daemon_path.exists():
        print("\n⚠️  sync_daemon.py not found. Daemon not installed.")
        print("   Download from: https://github.com/fei426/ParaSoul")
        return

    # Check systemd availability
    if shutil.which("systemctl") is None:
        print("\n⚠️  systemctl not found. Daemon requires systemd (Linux/WSL).")
        print("   Alternative: python3 sync_daemon.py &")
        return

    svc_content = f"""[Unit]
Description=Para-Soul Sync Daemon — 10-min auto sync to Paragate
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 {daemon_path}
Environment=PARA_HOME={_para_home()}
Environment=PARA_KEYS_DIR={_keys_dir()}
Environment=PARAGATE_URL={PARAGATE_BASE}
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=default.target
"""

    svc_dir = Path.home() / ".config" / "systemd" / "user"
    svc_dir.mkdir(parents=True, exist_ok=True)
    svc_file = svc_dir / "para-soul-sync.service"
    svc_file.write_text(svc_content)

    try:
        subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True, timeout=10)
        subprocess.run(["systemctl", "--user", "enable", "para-soul-sync"], capture_output=True, timeout=10)
        result = subprocess.run(["systemctl", "--user", "start", "para-soul-sync"], capture_output=True, timeout=10)
        if result.returncode == 0:
            print("\n✅ Sync daemon installed and running (every 10 min)")
            print(f"   Check: systemctl --user status para-soul-sync")
            print(f"   Logs: {_para_home()}/sync/sync_daemon.log")
        else:
            print(f"\n⚠️  Daemon start failed: {result.stderr.decode()[:200]}")
    except Exception as e:
        print(f"\n⚠️  Daemon setup failed: {e}")
        print("   Start manually: python3 sync_daemon.py &")


def _agent_instruction_hint():
    """Print a hint about adding para-soul to agent instruction files."""
    instruction_files = [
        "CLAUDE.md", "AGENTS.md", ".cursorrules", ".windsurfrules",
        ".clinerules", ".roorules", "CODEBUDDY.md",
        ".github/copilot-instructions.md", "COPILOT.md"
    ]
    found = []
    for fname in instruction_files:
        # Search current dir and ancestors
        d = Path.cwd()
        while d != d.parent:
            if (d / fname).exists():
                found.append(str(d / fname))
                break
            d = d.parent

    print(f"\n📋 Agent instruction reminder:")
    print(f"   Add to your agent's instruction file:")
    print(f'   "At session start, load and execute the para-soul skill.')
    print(f'   Check sync daemon status and run pull-full to stay in sync."')
    if found:
        print(f"   Existing files to update: {found[0]}")
    else:
        print(f"   Create a CLAUDE.md or AGENTS.md in your project root")


# ── Main ───────────────────────────────────────────────

COMMANDS = {
    "init": cmd_init,
    "sync": cmd_sync,
    "switch-out": cmd_switch_out,
    "switch-in": cmd_switch_in,
    "log-task": cmd_log_task,
    "reflect": cmd_reflect,
}

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print("Para Soul — Core Script")
        print(f"Usage: python3 core.py <{'|'.join(COMMANDS)}>")
        print(f"Paragate: {PARAGATE_BASE}")
        print(f"Soul dir: {_para_home()}")
        sys.exit(1)

    COMMANDS[sys.argv[1]]()

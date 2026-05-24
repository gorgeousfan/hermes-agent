#!/usr/bin/env python3
"""
atlas_briefing.py — pull Atlas's daily morning briefing for Phoebe-Hermes.

SSHes to Atlas (192.168.1.212), reads the persisted briefing file, formats output
for Phoebe. Atlas builds the brief and writes it to ~/morning_briefing_*.md
(persistence added to phoebe_atlas.py _deliver() 2026-05-19). Stdlib only.
"""

import argparse
import datetime as dt
import subprocess
import sys

ATLAS_HOST = "punchtaylor@phoebe-atlas"
ATLAS_HOST_FALLBACK = "punchtaylor@192.168.1.212"
SSH_TIMEOUT_SEC = 10


def ssh_run(remote_cmd: str) -> tuple[bool, str]:
    """SSH to Atlas and run a command. Returns (success, stdout)."""
    for host in (ATLAS_HOST, ATLAS_HOST_FALLBACK):
        try:
            result = subprocess.run(
                ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
                 host, remote_cmd],
                capture_output=True, text=True, timeout=SSH_TIMEOUT_SEC
            )
            if result.returncode == 0:
                return True, result.stdout
        except (subprocess.TimeoutExpired, OSError):
            continue
    return False, ""


def atlas_reachable() -> bool:
    ok, _ = ssh_run("true")
    return ok


def read_briefing_file(remote_path: str) -> tuple[bool, str]:
    """Cat a brief file on Atlas. Returns (exists, content)."""
    ok, _ = ssh_run(f"test -f {remote_path}")
    if not ok:
        return False, ""
    _, content = ssh_run(f"cat {remote_path}")
    return True, content


def cmd_today(args):
    """Print today's brief from ~/morning_briefing_latest.md."""
    exists, content = read_briefing_file("~/morning_briefing_latest.md")
    if not exists:
        # Try today's dated file as fallback
        today_iso = dt.date.today().isoformat()
        exists, content = read_briefing_file(f"~/morning_briefing_{today_iso}.md")
    if not exists:
        print("Atlas has not built a morning briefing today.")
        print("(Brief fires on first-activity-of-day after 5:30 AM. Check Atlas process status if expected by now.)")
        return
    if not content.strip():
        print("Morning brief file exists but is empty.")
        return
    print(content)


def cmd_date(args):
    """Print brief from a specific date."""
    try:
        target = dt.date.fromisoformat(args.date)
    except ValueError:
        print(f"Invalid date '{args.date}'. Expected YYYY-MM-DD.", file=sys.stderr)
        sys.exit(2)
    exists, content = read_briefing_file(f"~/morning_briefing_{target.isoformat()}.md")
    if not exists:
        print(f"No morning briefing on file for {target.isoformat()}.")
        print("(Atlas persistence began 2026-05-19; earlier dates will never have files.)")
        return
    print(content)


def cmd_summary(args):
    """One-line health check: brief existence + recency + size."""
    ok, ls_out = ssh_run("ls -la ~/morning_briefing_latest.md 2>/dev/null || echo MISSING")
    if not ok or "MISSING" in ls_out:
        print("No morning briefing on file. Atlas may not have run today yet.")
        return
    # Pull stat info
    _, stat_out = ssh_run("stat -c '%y %s' ~/morning_briefing_latest.md 2>/dev/null")
    if stat_out.strip():
        parts = stat_out.strip().rsplit(" ", 1)
        mtime = parts[0] if len(parts) >= 2 else stat_out.strip()
        size = parts[1] if len(parts) >= 2 else "?"
        print(f"Morning briefing: {size} bytes, generated {mtime}")
    else:
        print(f"Morning briefing exists ({ls_out.strip()}); stat unavailable.")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="atlas_briefing",
        description="Read-only access to Atlas's daily morning briefing."
    )
    sub = p.add_subparsers(dest="cmd", required=True, metavar="SUBCOMMAND")

    sub.add_parser("today", help="Today's brief").set_defaults(func=cmd_today)

    date_p = sub.add_parser("date", help="Brief from a specific date")
    date_p.add_argument("date", help="Date in YYYY-MM-DD")
    date_p.set_defaults(func=cmd_date)

    sub.add_parser("summary", help="Brief health check").set_defaults(func=cmd_summary)

    return p


def main():
    parser = build_parser()
    args = parser.parse_args()
    if not atlas_reachable():
        print(f"Atlas is unreachable — SSH to {ATLAS_HOST} (or {ATLAS_HOST_FALLBACK}) failed.", file=sys.stderr)
        print(f"Verify with: ssh -o BatchMode=yes {ATLAS_HOST} 'true'", file=sys.stderr)
        sys.exit(2)
    args.func(args)


if __name__ == "__main__":
    main()

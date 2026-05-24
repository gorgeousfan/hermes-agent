#!/usr/bin/env python3
"""
orion_state.py — read-only visibility into Orion paper-trading node state.

SSHes to Orion (192.168.1.200), reads cached state files, formats output for Phoebe
to consume. No modifications to Orion. No external dependencies — stdlib only.
"""

import argparse
import datetime as dt
import json
import subprocess
import sys
from typing import Optional

ORION_HOST = "punchtaylor@phoebe-orion"
SSH_TIMEOUT_SEC = 10


def ssh_run(remote_cmd: str) -> tuple[bool, str]:
    """SSH to Orion and run a command. Returns (success, stdout)."""
    try:
        result = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
             ORION_HOST, remote_cmd],
            capture_output=True, text=True, timeout=SSH_TIMEOUT_SEC
        )
        if result.returncode == 0:
            return True, result.stdout
        return False, ""
    except (subprocess.TimeoutExpired, OSError):
        return False, ""


def ssh_read(remote_path: str) -> str:
    """SSH to Orion and cat a file. Returns content, or empty on failure."""
    _, content = ssh_run(f"cat {remote_path}")
    return content


def ssh_cmd(remote_cmd: str) -> str:
    """SSH to Orion and run a command. Returns stdout, or empty on failure."""
    _, content = ssh_run(remote_cmd)
    return content


def orion_reachable() -> bool:
    """Quick SSH liveness probe. Returns True if Orion is reachable, False otherwise."""
    ok, _ = ssh_run("true")
    return ok


def try_json(text: str, fallback=None):
    """Parse JSON, return fallback on any error."""
    if not text:
        return fallback
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return fallback


def age_phrase(ts: Optional[float]) -> str:
    """Convert unix timestamp to a 'Nm ago' / 'Nh ago' / 'Nd ago' phrase."""
    if not ts:
        return ""
    delta_sec = dt.datetime.now().timestamp() - ts
    if delta_sec < 60:
        return f"{int(delta_sec)}s ago"
    if delta_sec < 3600:
        return f"{int(delta_sec / 60)}m ago"
    if delta_sec < 86400:
        return f"{int(delta_sec / 3600)}h ago"
    return f"{int(delta_sec / 86400)}d ago"


def cmd_summary(args):
    pending = try_json(ssh_read("~/orion_pending_open.json"), fallback=[]) or []
    decisions_text = ssh_read("~/orion_decisions.jsonl")
    today = dt.date.today().isoformat()
    today_decisions = [l for l in decisions_text.splitlines() if today in l]
    last_trade = ssh_cmd("tail -1 ~/orion_trades.log").strip()

    print(f"Orion status:")
    print(f"  Open positions: {len(pending)}")
    print(f"  Today's decisions logged: {len(today_decisions)}")
    if last_trade:
        print(f"  Last trade-log entry: {last_trade}")
    else:
        print(f"  No trade-log entries (or Orion unreachable)")


def cmd_positions(args):
    pending = try_json(ssh_read("~/orion_pending_open.json"), fallback=None)
    if pending is None:
        print("Open positions: unreadable (Orion unreachable or file missing).")
        return
    if not pending:
        print("Open positions: none.")
        return
    print(f"Open positions ({len(pending)}):")
    for pos in pending:
        if isinstance(pos, dict):
            ticker = pos.get("ticker") or pos.get("symbol") or "?"
            qty = pos.get("qty") or pos.get("quantity") or "?"
            entry = pos.get("entry_price") or pos.get("avg_entry_price") or pos.get("price") or "?"
            print(f"  {ticker}: qty={qty} entry={entry}")
        else:
            print(f"  {pos}")


def cmd_today(args):
    today = dt.date.today().isoformat()
    trades_text = ssh_read("~/orion_trades.log")
    if not trades_text:
        print(f"Trade log unreadable (Orion unreachable).")
        return

    today_lines = [l for l in trades_text.splitlines() if l.startswith(today)]
    if not today_lines:
        print(f"Orion has no recorded activity today ({today}).")
        return

    fires = []
    blocks_by_reason: dict[str, list[str]] = {}

    for line in today_lines:
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 3:
            continue
        result_code = parts[1]
        ticker_field = parts[2] if len(parts) > 2 else "?"
        if "BLOCKED" in result_code:
            blocks_by_reason.setdefault(result_code, []).append(ticker_field)
        else:
            fires.append(ticker_field)

    print(f"Orion today ({today}):")
    if fires:
        print(f"  Fires ({len(fires)}):")
        for f in fires[:15]:
            print(f"    {f}")
        if len(fires) > 15:
            print(f"    ...and {len(fires)-15} more")
    else:
        print(f"  No fires today.")

    if blocks_by_reason:
        print(f"  Blocks by reason:")
        for reason in sorted(blocks_by_reason, key=lambda r: -len(blocks_by_reason[r])):
            tickers = blocks_by_reason[reason]
            unique_tickers = list(dict.fromkeys(tickers))[:8]
            sample = ", ".join(unique_tickers)
            extra = f" (+{len(set(tickers))-len(unique_tickers)} more)" if len(set(tickers)) > len(unique_tickers) else ""
            print(f"    {reason}: {len(tickers)} attempts [{sample}{extra}]")


def cmd_watchlist(args):
    data = try_json(ssh_read("~/cheap_tradable_universe.json"), fallback=None)
    if data is None:
        print("Watchlist: unreadable.")
        return

    if isinstance(data, list):
        tickers = data
        meta = {}
    elif isinstance(data, dict) and isinstance(data.get("tickers"), list):
        tickers = data["tickers"]
        meta = data
    elif isinstance(data, dict):
        tickers = list(data.keys())
        meta = {}
    else:
        print(f"Watchlist: unexpected shape ({type(data).__name__}).")
        return

    age = age_phrase(meta.get("ts"))
    age_str = f" (refreshed {age})" if age else ""
    print(f"Watchlist: {len(tickers)} tickers{age_str}")
    if meta:
        equity = meta.get("equity")
        max_price = meta.get("max_price")
        min_vol = meta.get("min_avg_volume")
        params = []
        if equity is not None:
            params.append(f"equity ${equity:,.2f}")
        if max_price is not None:
            params.append(f"max_price ${max_price}")
        if min_vol is not None:
            params.append(f"min_avg_volume {min_vol:,}")
        if params:
            print(f"  Filters: {' / '.join(params)}")
    sample = [str(t) for t in tickers[:15]]
    print(f"  Sample: {', '.join(sample)}")


def cmd_news(args):
    n = args.n
    data = try_json(ssh_read("~/news_cache.json"), fallback=None)
    if data is None:
        print("News cache unreadable.")
        return
    headlines = data.get("headlines", []) if isinstance(data, dict) else []
    ts = data.get("ts") if isinstance(data, dict) else None
    age = age_phrase(ts)
    age_str = f" (cached {age})" if age else ""
    print(f"Recent headlines{age_str}:")
    for h in headlines[:n]:
        print(f"  - {h}")
    if not headlines:
        print(f"  (no headlines in cache)")


def cmd_macros(args):
    macros = [
        ("BDI",     "~/bdi_cache.json"),
        ("BTC",     "~/btc_cache.json"),
        ("Copper",  "~/copper_cache.json"),
        ("DXY",     "~/dxy_cache.json"),
        ("NatGas",  "~/natgas_cache.json"),
        ("Oil",     "~/oil_cache.json"),
    ]
    print("Macro indicators:")
    for name, path in macros:
        data = try_json(ssh_read(path), fallback=None)
        if data is None:
            print(f"  {name}: unreadable")
            continue
        if isinstance(data, dict):
            value = data.get("value") or data.get("price") or data.get("level") or "?"
            ts = data.get("ts") or data.get("timestamp")
            age = age_phrase(ts)
            age_str = f" ({age})" if age else ""
            if isinstance(value, float):
                value_str = f"{value:.2f}"
            else:
                value_str = str(value)
            print(f"  {name}: {value_str}{age_str}")
        else:
            print(f"  {name}: {data}")


def cmd_risk(args):
    data = try_json(ssh_read("~/monte_carlo_latest.json"), fallback=None)
    if data is None:
        print("Monte Carlo results unavailable.")
        return

    equity = data.get("equity")
    ts = data.get("ts")
    age = age_phrase(ts)
    age_str = f" ({age})" if age else ""
    print(f"Risk model{age_str}:")
    if equity:
        print(f"  Current equity: ${equity:,.2f}")
    else:
        print(f"  Equity unknown")

    scenario = data.get("results", {}).get("p_win_0.55", {})
    if scenario:
        print(f"  p_win=0.55 scenario over {scenario.get('n_trades', '?')} trades:")
        print(f"    Mean final equity: ${scenario.get('mean_final_equity', 0):,.2f}")
        p5 = scenario.get('p5_final_equity', 0)
        p95 = scenario.get('p95_final_equity', 0)
        print(f"    p5 / p95: ${p5:,.0f} / ${p95:,.0f}")
        print(f"    p_ruin: {scenario.get('p_ruin', 0)*100:.1f}%")
        print(f"    p_profitable: {scenario.get('p_profitable', 0)*100:.1f}%")
        print(f"    Mean max drawdown: {scenario.get('mean_max_drawdown_pct', 0):.1f}%")


def cmd_morning(args):
    """Composite morning brief — for Phoebe's morning routine to include."""
    print("=== Orion Overnight Brief ===")
    print()
    cmd_positions(args)
    print()
    cmd_today(args)
    print()
    cmd_risk(args)
    print()
    cmd_macros(args)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="orion_state",
        description="Read-only visibility into Orion paper-trading node state."
    )
    sub = p.add_subparsers(dest="cmd", required=True, metavar="SUBCOMMAND")

    sub.add_parser("summary", help="One-line overview").set_defaults(func=cmd_summary)
    sub.add_parser("positions", help="Open positions").set_defaults(func=cmd_positions)
    sub.add_parser("today", help="Today's fires + blocks").set_defaults(func=cmd_today)
    sub.add_parser("watchlist", help="Watchlist summary").set_defaults(func=cmd_watchlist)

    news_p = sub.add_parser("news", help="Recent headlines")
    news_p.add_argument("-n", type=int, default=8, help="Max headlines to show")
    news_p.set_defaults(func=cmd_news)

    sub.add_parser("macros", help="Macro indicator dashboard").set_defaults(func=cmd_macros)
    sub.add_parser("risk", help="Monte Carlo risk model").set_defaults(func=cmd_risk)
    sub.add_parser("morning", help="Composite morning brief").set_defaults(func=cmd_morning)

    return p


def main():
    parser = build_parser()
    args = parser.parse_args()
    if not orion_reachable():
        print(f"Orion is unreachable — SSH to {ORION_HOST} failed.", file=sys.stderr)
        print(f"Verify with: ssh -o BatchMode=yes {ORION_HOST} 'true'", file=sys.stderr)
        sys.exit(2)
    args.func(args)


if __name__ == "__main__":
    main()

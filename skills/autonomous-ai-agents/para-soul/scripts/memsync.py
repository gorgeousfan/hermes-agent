#!/usr/bin/env python3
"""Sync agent memory from Hermes + instruction files into Para-Soul .para/ files.
Reads ~/.hermes/memories/MEMORY.md and USER.md directly (no API needed).
"""

import json, os, sys
from pathlib import Path
from datetime import datetime

PARA_HOME = Path(os.environ.get("PARA_HOME", Path.home() / ".para"))
HERMES_MEMORIES = Path.home() / ".hermes" / "memories"

# ── Source 1: Hermes memory files (direct read) ────

def read_hermes_memories():
    """Read MEMORY.md and USER.md from ~/.hermes/memories/"""
    entries = []
    for fname in ["MEMORY.md", "USER.md"]:
        fp = HERMES_MEMORIES / fname
        if fp.exists():
            content = fp.read_text(encoding='utf-8')
            # Parse §-delimited entries
            items = [s.strip() for s in content.split("§") if s.strip()]
            entries.append((fname, items))
    return entries


# ── Source 2: Instruction files (Claude Code, OpenCode, Cursor, etc.) ────

INSTRUCTION_FILES = [
    "CLAUDE.md", "AGENTS.md", ".cursorrules", ".windsurfrules",
    ".clinerules", ".roorules", "CODEBUDDY.md",
    ".github/copilot-instructions.md", "COPILOT.md", "CONVENTIONS.md",
]

def scan_instruction_files():
    """Scan known directories for agent instruction files."""
    found = {}
    search_dirs = [
        Path.cwd(), Path.home(),
        Path.home() / ".claude",
        Path.home() / ".config" / "claude",
    ]
    for base in search_dirs:
        if not base.exists(): continue
        d = base
        while d != d.parent:
            for fname in INSTRUCTION_FILES:
                fp = d / fname
                if fp.exists() and str(fp) not in found:
                    try:
                        content = fp.read_text(encoding='utf-8')[:5000]
                        if len(content.strip()) > 50:
                            found[str(fp)] = content[:3000]
                    except Exception: pass
            d = d.parent
    return found


# ── Merge and write ────

def build_memory_md(hermes, instructions):
    lines = ["# Memory", "", f"Auto-synced: {datetime.now().isoformat()[:19]}", ""]
    
    for fname, entries in hermes:
        lines.append(f"## {fname}")
        for entry in entries:
            lines.append(f"{entry}")
            lines.append("")
        lines.append("")
    
    if instructions:
        lines.append("## Agent Instructions")
        for path, content in instructions.items():
            fname = Path(path).name
            lines.append(f"### {fname} ({path})")
            for line in content.split("\n"):
                line = line.strip()
                if line and len(line) > 10:
                    lines.append(f"- {line[:200]}")
            lines.append("")
    
    return "\n".join(lines)


# ── Keyword extraction ────

KEYWORD_PATTERNS = [
    "Para-Soul", "Paragate", "Hermes", "Claude", "OpenCode", "Cursor", "Copilot",
    "GitHub", "systemd", "cron", "sync", "daemon", "Docker", "WSL",
    "browser-use", "Chrome CDP", "API", "REST", "GraphQL",
]

def extract_keywords(text):
    """Extract keyword frequencies from memory text."""
    kw = {}
    text_lower = text.lower()
    for pattern in KEYWORD_PATTERNS:
        count = text_lower.count(pattern.lower())
        if count > 0:
            kw[pattern] = count
    return dict(sorted(kw.items(), key=lambda x: x[1], reverse=True))


# ── Growth-log archiving ────

def archive_growth_log():
    """Archive growth-log entries older than 30 days to long-term-memory.md."""
    growth_dir = PARA_HOME / "growth-log"
    ltm_file = PARA_HOME / "long-term-memory.md"
    
    if not growth_dir.exists():
        return 0
    
    cutoff = datetime.now().replace(day=1)
    if cutoff.month == 1:
        cutoff = cutoff.replace(year=cutoff.year - 1, month=12)
    else:
        cutoff = cutoff.replace(month=cutoff.month - 1)
    
    archived = 0
    for log_file in sorted(growth_dir.glob("*.md")):
        try:
            month_str = log_file.stem
            if month_str < cutoff.strftime("%Y-%m"):
                content = log_file.read_text(encoding='utf-8')
                if len(content.strip()) > 10:
                    ltm = ltm_file.read_text(encoding='utf-8')
                    ltm += f"\n\n## {month_str}\n{content}"
                    ltm_file.write_text(ltm, encoding='utf-8')
                log_file.unlink()
                archived += 1
        except Exception:
            pass
    
    return archived


# ── Main ────

def main():
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] MemSync")
    
    hermes = read_hermes_memories()
    instructions = scan_instruction_files()
    
    # Build and write memory.md
    memory_content = build_memory_md(hermes, instructions)
    (PARA_HOME / "memory.md").write_text(memory_content, encoding='utf-8')
    
    # Auto-generate keywords.json from memory content
    keywords = extract_keywords(memory_content)
    (PARA_HOME / "keywords.json").write_text(json.dumps(keywords, indent=2, ensure_ascii=False), encoding='utf-8')
    
    # Auto-archive growth-log entries older than 30 days
    archive_count = archive_growth_log()
    
    print(f"  Hermes files: {len(hermes)} ({sum(len(e) for _,e in hermes)} entries)")
    print(f"  Instruction files: {len(instructions)}")
    print(f"  Wrote: memory.md ({len(memory_content)} chars)")
    print(f"  Keywords: {len(keywords)} topics")
    if archive_count:
        print(f"  Archived: {archive_count} old growth-log entries → long-term-memory.md")
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] MemSync done")

if __name__ == "__main__":
    main()

from __future__ import annotations

import json
import os
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

from .util import ensure, load_state, stats_dir


def usage_log() -> Path:
    return stats_dir() / "usage.jsonl"


def record_event(event: dict) -> None:
    event = {**event, "ts": event.get("ts") or time.time()}
    log = usage_log()
    ensure(log.parent)
    with log.open("a") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


def hook_entrypoint() -> None:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return
    tool = data.get("tool_name") or data.get("toolName") or ""
    if tool != "Skill":
        return
    inp = data.get("tool_input") or data.get("toolInput") or {}
    skill_name = inp.get("skill") or inp.get("name") or ""
    if not skill_name:
        return
    try:
        record_event({
            "event": "skill_invoke",
            "skill": skill_name,
            "args": (inp.get("args") or "")[:200],
            "session": data.get("session_id") or os.environ.get("CLAUDE_SESSION_ID", ""),
            "cwd": data.get("cwd") or os.getcwd(),
        })
    except Exception:
        pass


def show_stats(days: int = 30, skill: str | None = None) -> None:
    log = usage_log()
    if not log.exists():
        print("(no usage data yet - install hooks with `skman install-hook --write`)")
        return
    cutoff = time.time() - days * 86400
    events = []
    with log.open() as f:
        for line in f:
            try:
                e = json.loads(line)
            except Exception:
                continue
            if e.get("ts", 0) < cutoff:
                continue
            if skill and skill not in (e.get("skill") or ""):
                continue
            events.append(e)

    state = load_state()
    # Agents identify a skill by its folder name in the discovery dir, which
    # is exactly our state key. Disabled skills have no symlink, so they
    # aren't "managed" for stats purposes.
    managed_keys = {k for k, info in state.get("skills", {}).items()
                    if info.get("enabled", True)}

    if not events:
        print(f"(no skill invocations in the last {days} days)")
    else:
        counts = Counter(e["skill"] for e in events if e.get("event") == "skill_invoke")
        last_used: dict[str, float] = {}
        sessions: dict[str, set] = {}
        for e in events:
            s = e.get("skill")
            if not s:
                continue
            last_used[s] = max(last_used.get(s, 0), e.get("ts", 0))
            sessions.setdefault(s, set()).add(e.get("session") or "?")

        width = max(38, max((len(sk) for sk in counts), default=38))
        print(f"Usage over last {days} day(s) — {sum(counts.values())} invocation(s)\n")
        print(f"{'':1}{'SKILL':<{width}} {'COUNT':>6} {'SESSIONS':>9}  LAST USED")
        print("-" * (width + 30))
        for sk, n in counts.most_common():
            last = datetime.fromtimestamp(last_used[sk]).strftime("%Y-%m-%d %H:%M")
            marker = " " if sk in managed_keys else "*"
            print(f"{marker}{sk:<{width}} {n:>6} {len(sessions[sk]):>9}  {last}")
        if any(sk not in managed_keys for sk in counts):
            print("\n  * = invoked but not managed by skman")

    if managed_keys:
        invoked = {e.get("skill") for e in events}
        unused = sorted(k for k in managed_keys if k not in invoked)
        print()
        print("Quality signals:")
        print(f"  managed skills:           {len(managed_keys)}")
        print(f"  unused in window:         {len(unused)}")
        if unused and len(unused) <= 10:
            print(f"  unused list:              {', '.join(unused)}")

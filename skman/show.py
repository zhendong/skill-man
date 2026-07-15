from __future__ import annotations

import shutil
import textwrap

from .sync import resolve_skill_key
from .util import fmt_ts, load_state, skill_path, split_frontmatter


def _wrap(text: str, indent: str = "  ") -> str:
    if not text:
        return f"{indent}(none)"
    cols = shutil.get_terminal_size(fallback=(100, 24)).columns
    wrapper = textwrap.TextWrapper(
        width=max(40, cols - len(indent)),
        initial_indent=indent,
        subsequent_indent=indent,
    )
    return "\n".join(wrapper.wrap(text))


def show_skill(name_or_key: str, show_all: bool = False) -> None:
    state_key = resolve_skill_key(name_or_key)
    if state_key is None:
        raise SystemExit(f"unknown skill: {name_or_key}")
    state = load_state()
    info = state["skills"][state_key]
    path = skill_path(info)

    fields = [
        ("link name", state_key),
        ("source", info.get("source") or "-"),
        ("path", str(path)),
        ("status", "enabled" if info.get("enabled", True) else "disabled"),
        ("installed", fmt_ts(info.get("installed_at"))),
        ("updated", fmt_ts(info.get("updated_at"))),
        ("commit", info.get("commit") or "-"),
    ]
    label_width = max(len(label) for label, _ in fields)
    for label, value in fields:
        print(f"{label.upper():<{label_width}}  {value}")

    print()
    print("DESCRIPTION")
    print(_wrap(info.get("description", "")))

    if not show_all:
        return

    md = path / "SKILL.md"
    if not md.exists():
        print()
        print(f"(SKILL.md not found at {md})")
        return

    _front, body = split_frontmatter(md.read_text(errors="replace"))
    body = body.strip("\n")

    rule = "─" * min(shutil.get_terminal_size(fallback=(100, 24)).columns, 72)
    print()
    print(rule)
    print("SKILL.md")
    print(rule)
    print(body if body else "(empty)")

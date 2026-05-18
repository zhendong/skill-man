from __future__ import annotations

import os
from pathlib import Path

from .util import ensure, load_state, skill_path, sources_dir


def target_dirs() -> list[Path]:
    """Where managed skills get symlinked.

    Defaults: ~/.agents/skills, ~/.claude/skills. Override with the
    SKMAN_TARGET_DIRS env var (colon-separated); used by tests.

    Codex deliberately omitted: it discovers skills from ~/.agents/skills as
    its cross-agent fallback, so linking there is enough.
    """
    override = os.environ.get("SKMAN_TARGET_DIRS")
    if override:
        return [Path(p).expanduser() for p in override.split(":") if p]
    return [
        Path.home() / ".agents" / "skills",
        Path.home() / ".claude" / "skills",
    ]


def _points_into_sources(link: Path) -> bool:
    """True if `link` is a symlink whose target lives under our sources/ tree."""
    try:
        resolved = (link.parent / link.readlink()).resolve(strict=False)
    except OSError:
        return False
    try:
        return resolved.is_relative_to(sources_dir().resolve())
    except (ValueError, OSError):
        return False


def refresh_links() -> None:
    state = load_state()
    enabled_keys = {k for k, info in state.get("skills", {}).items()
                    if info.get("enabled", True)}
    dirs = [ensure(d) for d in target_dirs()]

    n_stale = 0
    for d in dirs:
        for entry in d.iterdir():
            if not entry.is_symlink():
                continue
            if not _points_into_sources(entry):
                continue
            if entry.name not in enabled_keys:
                entry.unlink()
                n_stale += 1

    n_ok = 0
    n_skip = 0
    for state_key in enabled_keys:
        info = state["skills"][state_key]
        target = skill_path(info)
        if not target.exists():
            continue
        for d in dirs:
            link = d / state_key
            if link.is_symlink():
                if _points_into_sources(link):
                    link.unlink()
                else:
                    n_skip += 1
                    continue
            elif link.exists():
                n_skip += 1
                continue
            link.symlink_to(target)
            n_ok += 1

    summary = f"linked {n_ok} symlink(s) across {len(dirs)} dir(s) "
    summary += f"({', '.join(str(d) for d in dirs)})"
    if n_stale:
        summary += f"; removed {n_stale} stale link(s)"
    if n_skip:
        summary += f"; skipped {n_skip} (foreign symlink or real path at destination)"
    print(summary)

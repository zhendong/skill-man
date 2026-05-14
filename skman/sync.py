from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from .sources import fetch_source, resolve_to_key
from .util import (
    load_state,
    now_iso,
    read_skill_meta,
    save_state,
    skill_state_key,
)

_SKIP_DIRS = {".git", "node_modules", ".venv", "__pycache__", ".pytest_cache"}


def _discover_skills_in(root: Path) -> list[Path]:
    found = []
    for skill_md in root.rglob("SKILL.md"):
        if any(part in _SKIP_DIRS for part in skill_md.parts):
            continue
        found.append(skill_md.parent)
    return found


def _scan_root(src_path: Path) -> Path:
    skills_subdir = src_path / "skills"
    if skills_subdir.is_dir():
        return skills_subdir
    return src_path


def _warn_duplicates(state: dict) -> None:
    groups: dict[tuple[str, str], list[str]] = defaultdict(list)
    for state_key, info in state["skills"].items():
        ident = (info.get("name", "").strip(), info.get("description", "").strip())
        if not ident[0]:
            continue
        groups[ident].append(state_key)
    dups = {k: v for k, v in groups.items() if len(v) > 1}
    if not dups:
        return
    print()
    print("WARNING: duplicate skills detected (same name + description):")
    for (name, desc), keys in dups.items():
        short_desc = (desc[:60] + "…") if len(desc) > 60 else desc
        print(f"  name={name!r}  description={short_desc!r}")
        for k in keys:
            info = state["skills"][k]
            print(f"    - {k}  (from source {info['source']})")
    print("Remove one of the sources to resolve.")


def resolve_skill_key(name_or_key: str) -> str | None:
    state = load_state()
    if name_or_key in state["skills"]:
        return name_or_key
    by_slug = [k for k, info in state["skills"].items() if info.get("slug") == name_or_key]
    if len(by_slug) == 1:
        return by_slug[0]
    if len(by_slug) > 1:
        raise SystemExit(f"ambiguous skill {name_or_key!r}; matches: {', '.join(by_slug)}")
    by_name = [k for k, info in state["skills"].items() if info.get("name") == name_or_key]
    if len(by_name) == 1:
        return by_name[0]
    if len(by_name) > 1:
        raise SystemExit(f"ambiguous skill {name_or_key!r}; matches: {', '.join(by_name)}")
    return None


def sync_source(key: str) -> list[str]:
    state = load_state()
    if key not in state["sources"]:
        raise SystemExit(f"unknown source: {key}")
    info = state["sources"][key]
    print(f"[sync] fetching source {key} ({info['url']})")
    src_path, commit = fetch_source(key, info)
    print(f"[sync] {key} @ {commit or '(no commit)'}")

    scan_root = _scan_root(src_path)

    discovered: list[str] = []
    for sd in _discover_skills_in(scan_root):
        slug = sd.name
        state_key = skill_state_key(slug, key)
        rel_in_source = sd.relative_to(src_path).as_posix()
        meta = read_skill_meta(sd)
        now = now_iso()
        existing = state["skills"].get(state_key)
        state["skills"][state_key] = {
            "slug": slug,
            "name": meta["name"],
            "description": meta["description"],
            "source": key,
            "path": rel_in_source,
            "commit": commit,
            "installed_at": (existing or {}).get("installed_at", now),
            "updated_at": now,
            "enabled": (existing or {}).get("enabled", True),
        }
        discovered.append(state_key)
        print(f"  - {state_key}")

    seen = set(discovered)
    stale = [
        s for s, info in state["skills"].items()
        if info.get("source") == key and s not in seen
    ]
    for s in stale:
        del state["skills"][s]
        print(f"  - removed (no longer in source): {s}")

    save_state(state)
    print(f"[sync] {key}: {len(discovered)} skill(s)")
    return discovered


def sync_all() -> list[str]:
    state = load_state()
    if not state["sources"]:
        print("(no sources configured; add one with `skman source add`)")
        return []
    all_keys: list[str] = []
    for src_key in list(state["sources"].keys()):
        all_keys += sync_source(src_key)

    state = load_state()
    _warn_duplicates(state)

    from .links import refresh_links
    refresh_links()
    return all_keys


def sync_skill(name_or_key: str) -> None:
    state_key = resolve_skill_key(name_or_key)
    if state_key is None:
        raise SystemExit(f"unknown skill: {name_or_key}")
    state = load_state()
    sync_source(state["skills"][state_key]["source"])
    state = load_state()
    _warn_duplicates(state)
    from .links import refresh_links
    refresh_links()


def sync_by_name(name_or_url: str) -> None:
    key = resolve_to_key(name_or_url)
    if key is None:
        raise SystemExit(f"unknown source: {name_or_url}")
    sync_source(key)
    state = load_state()
    _warn_duplicates(state)
    from .links import refresh_links
    refresh_links()

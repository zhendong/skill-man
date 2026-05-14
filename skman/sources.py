from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

from .util import (
    ensure,
    expand,
    load_state,
    run,
    save_state,
    source_dir_for,
    sources_dir,
)


def _is_existing_local(url: str) -> Path | None:
    p = expand(url)
    return p if p.exists() else None


def _detect_type(url: str) -> str:
    p = _is_existing_local(url)
    if p is not None and (p / ".git").exists():
        return "git"
    if p is not None and p.is_dir():
        return "local"
    return "git"


def canonicalize_url(url: str) -> str:
    s = url.strip()
    p = _is_existing_local(s)
    if p is not None:
        return str(p.resolve())
    m = re.match(r"^[\w.+-]+@([\w.-]+):(.*)$", s)
    if m:
        host = m.group(1).lower()
        path = m.group(2).rstrip("/")
        if path.endswith(".git"):
            path = path[:-4]
        return f"{host}/{path}" if path else host
    if "://" in s:
        _, _, rest = s.partition("://")
        host, _, path = rest.partition("/")
        host = host.lower()
        path = path.rstrip("/")
        if path.endswith(".git"):
            path = path[:-4]
        return f"{host}/{path}" if path else host
    return s.rstrip("/")


def _apply_github_mirror(url: str) -> str:
    mirror = os.environ.get("SKMAN_GITHUB_MIRROR", "").strip()
    if not mirror:
        return url
    m = re.match(r"^[\w.+-]+@github\.com:(.*)$", url, re.IGNORECASE)
    if m:
        path = m.group(1)
        if "://" in mirror:
            return f"{mirror.rstrip('/')}/https://github.com/{path}"
        return f"https://{mirror}/{path}"
    if "://" in url and re.search(r"(?i)//github\.com[/:]", url):
        if "://" in mirror:
            return f"{mirror.rstrip('/')}/{url}"
        return re.sub(r"(?i)github\.com", mirror, url, count=1)
    return url


def resolve_to_key(name_or_url: str) -> str | None:
    state = load_state()
    canonical = canonicalize_url(name_or_url)
    if canonical in state["sources"]:
        return canonical
    needle = name_or_url.strip()
    matches = [k for k in state["sources"] if needle in k or canonical in k]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise SystemExit(
            f"ambiguous source identifier {name_or_url!r}; matches: {', '.join(matches)}"
        )
    return None


def _rollback_source(key: str) -> None:
    state = load_state()
    state["sources"].pop(key, None)
    for sk in [k for k, info in state["skills"].items() if info.get("source") == key]:
        del state["skills"][sk]
    save_state(state)
    disk = source_dir_for(key)
    if disk.is_symlink():
        disk.unlink()
    elif disk.exists():
        shutil.rmtree(disk)


def add_source(url: str, ref: str = "main", only: list[str] | None = None,
               auto_sync: bool = True) -> str:
    state = load_state()
    key = canonicalize_url(url)
    if key in state["sources"]:
        raise SystemExit(f"source already added: {key} (stored as {state['sources'][key]['url']})")
    detected = _detect_type(url)
    stored_url = str(expand(url)) if _is_existing_local(url) is not None else url
    record = {"type": detected, "url": stored_url, "ref": ref}
    state["sources"][key] = record
    save_state(state)
    print(f"added source {key} ({detected})")
    print(f"  clone url: {stored_url}")
    print(f"  on disk:   {source_dir_for(key)}")
    if not auto_sync:
        return key

    from .sync import sync_source
    from .links import refresh_links
    print()
    try:
        sync_source(key)
    except BaseException:
        _rollback_source(key)
        raise

    if only:
        only = sorted(set(only))
        state = load_state()
        available = sorted({info["slug"] for info in state["skills"].values()
                            if info.get("source") == key})
        missing = [s for s in only if s not in available]
        if missing:
            _rollback_source(key)
            raise SystemExit(
                f"skill(s) not found in source {key}: {', '.join(missing)}\n"
                f"available: {', '.join(available) or '(none)'}"
            )
        for sk, info in state["skills"].items():
            if info.get("source") == key:
                info["enabled"] = info["slug"] in only
        save_state(state)
        disabled = [s for s in available if s not in only]
        print(f"\nenabled: {', '.join(only)}")
        if disabled:
            print(f"disabled: {', '.join(disabled)}")

    refresh_links()
    return key


def remove_source(name_or_url: str) -> None:
    state = load_state()
    key = resolve_to_key(name_or_url)
    if key is None:
        raise SystemExit(f"unknown source: {name_or_url}")
    del state["sources"][key]
    skills_to_remove = [s for s, info in state["skills"].items() if info.get("source") == key]
    for s in skills_to_remove:
        del state["skills"][s]
    save_state(state)
    disk = source_dir_for(key)
    if disk.is_symlink():
        disk.unlink()
    elif disk.exists():
        shutil.rmtree(disk)
    print(f"removed source {key} (and {len(skills_to_remove)} skill(s) it provided)")
    from .links import refresh_links
    refresh_links()


def list_sources() -> None:
    state = load_state()
    if not state["sources"]:
        print("(no sources; add one with `skman source add <url>`)")
        return
    for i, (key, info) in enumerate(state["sources"].items()):
        if i:
            print()
        print(key)
        print(f"  type:   {info['type']}")
        print(f"  clone:  {info['url']}")
        print(f"  ref:    {info.get('ref', 'main')}")
        print(f"  dir:    {source_dir_for(key)}")


def fetch_source(key: str, info: dict) -> tuple[Path, str]:
    ensure(sources_dir())
    target = source_dir_for(key)
    if info["type"] == "git":
        ref = info.get("ref") or "main"
        clone_url = _apply_github_mirror(info["url"])
        if clone_url != info["url"]:
            print(f"  (using github mirror: {clone_url})")
        if target.exists() and (target / ".git").exists():
            run(["git", "remote", "set-url", "origin", clone_url], cwd=target, check=False)
            run(["git", "fetch", "--all", "--prune"], cwd=target)
            run(["git", "checkout", ref], cwd=target, check=False)
            run(["git", "reset", "--hard", f"origin/{ref}"], cwd=target, check=False)
        else:
            if target.is_symlink():
                target.unlink()
            elif target.exists():
                shutil.rmtree(target)
            run(["git", "clone", clone_url, str(target)])
            run(["git", "checkout", ref], cwd=target, check=False)
        commit = ""
        try:
            proc = run(["git", "rev-parse", "--short", "HEAD"], cwd=target, capture=True)
            commit = proc.stdout.strip()
        except Exception:
            commit = ""
        return target, commit

    src = expand(info["url"])
    if not src.exists():
        raise SystemExit(f"local source path does not exist: {src}")
    if target.is_symlink():
        target.unlink()
    elif target.exists():
        shutil.rmtree(target)
    target.symlink_to(src)
    commit = ""
    if (src / ".git").exists():
        try:
            proc = run(["git", "rev-parse", "--short", "HEAD"], cwd=src, capture=True)
            commit = proc.stdout.strip()
        except Exception:
            commit = ""
    return target, commit

"""Migrate skills already on disk (Claude Code, Codex, skills.sh) into skman.

Discovers SKILL.md dirs in:
  - ~/.claude/skills/            (Claude Code personal skills)
  - ~/.codex/skills/             (Codex personal skills; skips .system/)
  - ~/.agents/skills/            (cross-agent dir; also where skills.sh writes)
  - ~/.agents/.skill-lock.json   (skills.sh lockfile; authoritative source URLs)

For each unmanaged dir: prefer the source URL we know about (lockfile, or git
`origin` of the enclosing repo) → register as a git source. If we have no URL,
adopt the dir as a local source by copying it into ~/.skman/imported/<name>/.

The original on-disk dir is removed after migration so the host agent only
sees the skman-managed copy. Dot-prefixed entries (e.g. Codex's `.system/`)
are skipped — those are built-in, not user-managed.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path

from . import sources as sources_mod
from .util import load_state, root, run, skill_path, sources_dir

# Names that git ignores by default in source repos and that skills.sh's
# folder hash (a GitHub tree SHA) will never include. We filter them when
# recomputing the hash locally so OS noise (e.g. .DS_Store) doesn't trigger
# false dirty-folder warnings.
_HASH_SKIP = {".DS_Store", "Thumbs.db", ".git", "node_modules",
              "__pycache__", ".venv", ".pytest_cache"}


def _is_managed_symlink(p: Path) -> bool:
    if not p.is_symlink():
        return False
    try:
        resolved = (p.parent / p.readlink()).resolve(strict=False)
    except OSError:
        return False
    try:
        return resolved.is_relative_to(sources_dir().resolve())
    except (ValueError, OSError):
        return False


def _git_info_for(skill_dir: Path) -> tuple[str | None, Path | None]:
    """Walk up from skill_dir looking for .git. Returns (origin_url, repo_root)
    where either may be None if not found."""
    cur = skill_dir.resolve()
    for _ in range(20):
        if (cur / ".git").exists():
            try:
                proc = run(["git", "remote", "get-url", "origin"], cwd=cur, capture=True)
                url = proc.stdout.strip() or None
            except Exception:
                url = None
            return url, cur
        if cur.parent == cur:
            return None, None
        cur = cur.parent
    return None, None


def _git_local_changes(repo_root: Path) -> str | None:
    """Return a short reason string if the repo has uncommitted changes or
    commits that aren't on the upstream branch. Returns None when the working
    tree is clean and HEAD matches its upstream.

    Errors are reported as reasons so we err on the side of caution — better
    to skip and warn than to silently re-clone over local work.
    """
    try:
        proc = run(["git", "status", "--porcelain"], cwd=repo_root, capture=True)
    except Exception as e:
        return f"could not run `git status`: {e}"
    if proc.stdout.strip():
        return "uncommitted changes (working tree dirty)"

    # `git rev-list @{u}..HEAD` exits non-zero when no upstream is configured.
    try:
        proc = run(["git", "rev-list", "--count", "@{u}..HEAD"],
                   cwd=repo_root, capture=True, check=False)
    except Exception as e:
        return f"could not check upstream: {e}"
    if proc.returncode != 0:
        return "no upstream branch configured (changes may not be pushed)"
    count = proc.stdout.strip()
    if count.isdigit() and int(count) > 0:
        return f"{count} commit(s) ahead of upstream (unpushed)"
    return None


def _read_skill_lock() -> dict[str, dict]:
    """Parse ~/.agents/.skill-lock.json defensively.

    Returns {name: {"url": str|None, "hash": str|None}}. `hash` is the
    skills.sh `skillFolderHash` — a git tree SHA-1 for GitHub-sourced skills.
    The exact schema isn't fully standardised; we look defensively under
    common keys.
    """
    p = Path.home() / ".agents" / ".skill-lock.json"
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text())
    except (OSError, json.JSONDecodeError):
        return {}

    out: dict[str, dict] = {}

    def looks_like_url(v: object) -> bool:
        if not isinstance(v, str) or not v:
            return False
        if "://" in v:
            return True
        if v.endswith(".git"):
            return True
        if ":" in v.split("/", 1)[0]:  # git@host:path
            return True
        try:
            return Path(v).expanduser().is_dir()
        except (OSError, ValueError):
            return False

    def harvest(name: str, entry: object) -> None:
        if not isinstance(entry, dict):
            return
        url: str | None = None
        # skills.sh v3 uses "sourceUrl"; other tools use various conventions.
        for key in ("sourceUrl", "source_url", "repoUrl", "repo_url",
                    "url", "repo", "git", "origin", "from", "source"):
            v = entry.get(key)
            if looks_like_url(v):
                url = v  # type: ignore[assignment]
                break
        h = entry.get("skillFolderHash") or entry.get("hash")
        if not isinstance(h, str) or not h.strip():
            h = None
        if url or h:
            out[name] = {"url": url, "hash": h}

    skills = data.get("skills") if isinstance(data, dict) else None
    if skills is None and isinstance(data, dict):
        skills = data.get("packages") or data.get("dependencies")

    if isinstance(skills, dict):
        for name, entry in skills.items():
            harvest(name, entry)
    elif isinstance(skills, list):
        for entry in skills:
            if isinstance(entry, dict):
                name = entry.get("name") or entry.get("slug") or entry.get("id")
                if isinstance(name, str):
                    harvest(name, entry)

    return out


def _git_blob_sha(content: bytes) -> str:
    h = hashlib.sha1()
    h.update(f"blob {len(content)}".encode() + b"\0")
    h.update(content)
    return h.hexdigest()


def _git_tree_sha(entries: list[tuple[str, str, str]]) -> str:
    """`entries`: list of (mode-as-string, name, sha-hex). Returns tree SHA hex.

    Git sorts tree entries by the entry name, but directory names are compared
    as if they had a trailing `/` appended. We replicate that.
    """
    def sort_key(e: tuple[str, str, str]) -> bytes:
        mode, name, _ = e
        suffix = b"/" if mode == "40000" else b""
        return name.encode() + suffix

    body = b""
    for mode, name, sha_hex in sorted(entries, key=sort_key):
        body += f"{mode} {name}".encode() + b"\0" + bytes.fromhex(sha_hex)
    h = hashlib.sha1()
    h.update(f"tree {len(body)}".encode() + b"\0")
    h.update(body)
    return h.hexdigest()


def compute_skill_folder_hash(folder: Path) -> str | None:
    """Compute the git tree SHA-1 of `folder`, matching GitHub's tree-hash
    format used by skills.sh `skillFolderHash`.

    Filters out files that wouldn't be in the source repo (`.DS_Store`,
    `node_modules`, etc.) so OS noise doesn't trigger spurious mismatches.
    Returns None on I/O error.
    """
    def hash_folder(p: Path) -> str | None:
        try:
            children = list(p.iterdir())
        except OSError:
            return None
        entries: list[tuple[str, str, str]] = []
        for child in children:
            if child.name in _HASH_SKIP:
                continue
            if child.is_symlink():
                try:
                    target = os.readlink(child)
                except OSError:
                    return None
                entries.append(("120000", child.name, _git_blob_sha(target.encode())))
            elif child.is_dir():
                sub = hash_folder(child)
                if sub is None:
                    return None
                entries.append(("40000", child.name, sub))
            elif child.is_file():
                try:
                    content = child.read_bytes()
                except OSError:
                    return None
                mode = "100755" if os.access(child, os.X_OK) else "100644"
                entries.append((mode, child.name, _git_blob_sha(content)))
        return _git_tree_sha(entries)

    return hash_folder(folder)


def _candidate_dirs() -> list[Path]:
    home = Path.home()
    return [d for d in (
        home / ".claude" / "skills",
        home / ".codex" / "skills",
        home / ".agents" / "skills",
    ) if d.is_dir()]


def discover(extra_paths: list[Path]) -> list[dict]:
    state = load_state()
    managed_paths = {skill_path(info).resolve() for info in state["skills"].values()}
    lock_map = _read_skill_lock()

    out: list[dict] = []
    seen: set[Path] = set()
    scan = _candidate_dirs() + [p.resolve() for p in extra_paths]

    for d in scan:
        for entry in sorted(d.iterdir()):
            # Skip dot-prefixed entries: Codex ships built-ins under `.system/`,
            # and other agents may use similar infra dirs (`.cache`, etc.).
            if entry.name.startswith("."):
                continue
            if not (entry / "SKILL.md").exists():
                continue
            # Skip symlinks entirely — only migrate real on-disk skill dirs.
            # Cross-agent symlinks (e.g. ~/.codex/skills/foo → ~/.claude/skills/foo)
            # would otherwise cause double migration of the same skill.
            if entry.is_symlink():
                continue
            target = entry.resolve()
            if target in managed_paths or target in seen:
                continue
            seen.add(target)

            name = entry.name
            git_url, repo_root = _git_info_for(target)
            lock = lock_map.get(name) or {}
            url = lock.get("url") or git_url

            # Skip skills whose local copy doesn't match the recorded version
            # — either uncommitted/unpushed changes in a git checkout, or a
            # lockfile-hash mismatch (user edited the skill in place).
            dirty_reason = None
            if lock.get("hash"):
                local_hash = compute_skill_folder_hash(target)
                if local_hash is None:
                    dirty_reason = "could not hash local folder to verify"
                elif local_hash != lock["hash"]:
                    dirty_reason = (
                        f"local content (tree {local_hash[:12]}) differs from "
                        f"lockfile hash ({lock['hash'][:12]}) — folder modified "
                        "after install"
                    )
            elif git_url and not lock.get("url") and repo_root is not None:
                dirty_reason = _git_local_changes(repo_root)

            out.append({
                "name": name,
                "link": entry,
                "path": target,
                "source_url": url,
                "from_lock": name in lock_map,
                "dirty_reason": dirty_reason,
                "repo_root": repo_root,
            })
    return out


def _import_local(skill_dir: Path, name: str) -> Path:
    dst_root = root() / "imported"
    dst_root.mkdir(parents=True, exist_ok=True)
    dst = dst_root / name
    n = 2
    while dst.exists():
        dst = dst_root / f"{name}-{n}"
        n += 1
    shutil.copytree(skill_dir, dst, symlinks=False)
    return dst


def migrate(*, dry_run: bool = False, yes: bool = False,
            keep_originals: bool = False,
            extra_paths: list[Path] | None = None) -> None:
    extra = extra_paths or []
    candidates = discover(extra)

    dirty = [c for c in candidates if c["dirty_reason"]]
    clean = [c for c in candidates if not c["dirty_reason"]]

    if dirty:
        print("Skipping skills with local git changes (migrate these manually):\n")
        for c in dirty:
            print(f"  - {c['name']}")
            print(f"      at:     {c['link']}")
            print(f"      repo:   {c['repo_root']}")
            print(f"      reason: {c['dirty_reason']}")
        print(
            "\nCommit and push the changes upstream, then re-run `skman migrate`.\n"
            "Or manage these skills outside of skman.\n"
        )

    if not clean:
        if dirty:
            print("Nothing else to migrate.")
        else:
            print("nothing to migrate — no unmanaged skills found.")
        return

    plan: list[tuple[dict, str]] = []
    queued_urls: set[str] = set()
    for c in clean:
        if c["source_url"]:
            tag = "git (from skill-lock)" if c["from_lock"] else "git"
            if c["source_url"] in queued_urls:
                plan.append((c, f"piggyback on {c['source_url']}"))
            else:
                plan.append((c, f"add-source [{tag}] {c['source_url']}"))
                queued_urls.add(c["source_url"])
        else:
            plan.append((c, "adopt as local source (copy into ~/.skman/imported/)"))

    print(f"Found {len(clean)} unmanaged skill(s):\n")
    for c, action in plan:
        print(f"  - {c['name']}")
        print(f"      at:     {c['link']}")
        print(f"      action: {action}")

    if dry_run:
        print("\n(dry run — nothing changed)")
        return

    if not yes:
        try:
            ans = input("\nProceed? [y/N] ").strip().lower()
        except EOFError:
            ans = ""
        if ans not in ("y", "yes"):
            print("aborted")
            return

    print()
    added_urls: set[str] = set()
    succeeded: list[dict] = []
    for c, action in plan:
        url = c["source_url"]
        try:
            if url:
                if url not in added_urls:
                    sources_mod.add_source(url)
                    added_urls.add(url)
                succeeded.append(c)
            else:
                imported = _import_local(c["path"], c["name"])
                try:
                    sources_mod.add_source(str(imported))
                    succeeded.append(c)
                except BaseException:
                    shutil.rmtree(imported, ignore_errors=True)
                    raise
        except SystemExit as e:
            msg = str(e)
            if "already added" in msg:
                # Source already known to skman — treat as success so we can
                # still tidy up the original on-disk copy.
                succeeded.append(c)
                print(f"  (note: {url} already a source)")
            else:
                print(f"  ! failed for {c['name']}: {msg}")

    if not keep_originals and succeeded:
        removed = 0
        for c in succeeded:
            link = c["link"]
            if _is_managed_symlink(link):
                continue  # already replaced by a skman link of the same name
            try:
                if link.is_symlink():
                    link.unlink()
                elif link.is_dir():
                    shutil.rmtree(link)
                else:
                    link.unlink()
                removed += 1
            except OSError as e:
                print(f"  ! could not remove {link}: {e}")
        if removed:
            print(f"\nremoved {removed} original skill dir(s); "
                  f"skman now provides them via its own symlinks.")

    print("\nDone. Run `skman list` to see managed skills.")

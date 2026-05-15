from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

HOME = Path.home()


def root() -> Path:
    return Path(os.environ.get("SKMAN_ROOT", HOME / ".skman"))


def sources_dir() -> Path:
    return root() / "sources"


def stats_dir() -> Path:
    return root() / "stats"


def _url_hash(canonical_url: str) -> str:
    import hashlib
    return hashlib.sha256(canonical_url.encode("utf-8")).hexdigest()


def source_dir_for(canonical_url: str) -> Path:
    """On-disk folder for a source, named after a short hash of its URL."""
    return sources_dir() / _url_hash(canonical_url)[:12]


def source_short_id(canonical_url: str) -> str:
    """Short identifier used to disambiguate skill symlink names across sources."""
    return _url_hash(canonical_url)[:6]


def source_basename(canonical_url: str) -> str:
    """Display-friendly basename of a source's canonical URL.

    Used as the slug when a source ships a single skill at its root
    (no `skills/<name>/` wrapper). E.g. `/Users/me/foo` -> `foo`,
    `github.com/obra/superpowers` -> `superpowers`.
    """
    name = Path(canonical_url).name
    return name or canonical_url


def skill_state_key(slug: str, canonical_url: str) -> str:
    return f"{slug}-{source_short_id(canonical_url)}"


def skill_path(info: dict) -> Path:
    """Resolve the on-disk path for a skill record from state.json."""
    return source_dir_for(info["source"]) / info["path"]


def state_path() -> Path:
    return root() / "state.json"


def ensure(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    return p


def load_state() -> dict:
    p = state_path()
    if not p.exists():
        return {"version": 1, "sources": {}, "skills": {}}
    raw = p.read_text() or "{}"
    state = json.loads(raw)
    state.setdefault("version", 1)
    state.setdefault("sources", {})
    state.setdefault("skills", {})
    return state


def save_state(state: dict) -> None:
    p = state_path()
    ensure(p.parent)
    p.write_text(json.dumps(state, indent=2, ensure_ascii=False))


def run(cmd, cwd=None, check=True, capture=False):
    return subprocess.run(
        cmd, cwd=cwd, check=check, capture_output=capture, text=True
    )


def copytree_clean(src: Path, dst: Path) -> None:
    ensure(dst.parent)
    if dst.exists() or dst.is_symlink():
        if dst.is_symlink():
            dst.unlink()
        else:
            shutil.rmtree(dst)
    shutil.copytree(src, dst, symlinks=False)


def expand(p: str | os.PathLike) -> Path:
    return Path(os.path.expanduser(os.path.expandvars(str(p))))


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def fmt_ts(iso: str | None) -> str:
    if not iso:
        return "-"
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return iso
    return dt.astimezone().strftime("%Y-%m-%d %H:%M")


def parse_skill_frontmatter(text: str) -> dict:
    """Minimal YAML-ish frontmatter parser.

    Extracts top-level scalar keys (name, description, …). Handles
    single-line strings and `|`/`>` block scalars. Nested mappings are
    ignored (we only care about name/description).
    """
    if not text.startswith("---"):
        return {}
    try:
        end = text.index("\n---", 3)
    except ValueError:
        return {}
    body = text[3:end].lstrip("\n")

    out: dict[str, str] = {}
    current_key: str | None = None
    block_style: str | None = None
    block_lines: list[str] = []
    block_indent: int | None = None

    def commit() -> None:
        nonlocal current_key, block_lines, block_style, block_indent
        if current_key is None:
            return
        if block_style == "|":
            out[current_key] = "\n".join(block_lines).rstrip()
        elif block_style == ">":
            out[current_key] = " ".join(line.strip() for line in block_lines).strip()
        current_key = None
        block_style = None
        block_lines = []
        block_indent = None

    for raw in body.splitlines():
        if not raw.strip() and current_key and block_style:
            block_lines.append("")
            continue
        if raw.startswith(" ") or raw.startswith("\t"):
            if current_key and block_style:
                if block_indent is None:
                    block_indent = len(raw) - len(raw.lstrip())
                block_lines.append(raw[block_indent:] if len(raw) >= block_indent else raw.lstrip())
            continue
        if raw.startswith("#") or not raw.strip():
            continue
        if ":" not in raw:
            continue
        commit()
        key, _, val = raw.partition(":")
        current_key = key.strip()
        val = val.strip()
        if val in ("|", ">"):
            block_style = val
            block_lines = []
            block_indent = None
        else:
            if (len(val) >= 2 and val[0] == val[-1] and val[0] in ('"', "'")):
                val = val[1:-1]
            out[current_key] = val
            current_key = None

    commit()
    return out


def read_skill_meta(skill_dir: Path) -> dict:
    """Return {'name': ..., 'description': ...} from a skill's SKILL.md."""
    md = skill_dir / "SKILL.md"
    if not md.exists():
        return {"name": skill_dir.name, "description": ""}
    front = parse_skill_frontmatter(md.read_text(errors="replace"))
    return {
        "name": (front.get("name") or skill_dir.name).strip(),
        "description": (front.get("description") or "").strip(),
    }

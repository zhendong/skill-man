from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import yaml

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


_FRONTMATTER_FENCE = re.compile(r"^---[ \t]*$", re.MULTILINE)


def _frontmatter_bounds(text: str) -> tuple[str, str] | None:
    """Locate a leading `---`-fenced block; return (raw frontmatter, body) or None."""
    if not text.startswith("---"):
        return None
    first_nl = text.find("\n")
    if first_nl == -1:
        return None
    m = _FRONTMATTER_FENCE.search(text, first_nl + 1)
    if not m:
        return None
    return text[first_nl + 1:m.start()], text[m.end():].lstrip("\n")


def parse_skill_frontmatter(text: str) -> dict:
    """Parse a SKILL.md's YAML frontmatter into a dict."""
    bounds = _frontmatter_bounds(text)
    if bounds is None:
        return {}
    front_text, _body = bounds
    try:
        data = yaml.safe_load(front_text)
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


def split_frontmatter(text: str) -> tuple[dict, str]:
    """Split a SKILL.md's raw text into (frontmatter dict, body text)."""
    bounds = _frontmatter_bounds(text)
    if bounds is None:
        return {}, text
    front_text, body = bounds
    try:
        data = yaml.safe_load(front_text)
    except yaml.YAMLError:
        data = None
    return (data if isinstance(data, dict) else {}), body


def read_skill_meta(skill_dir: Path) -> dict:
    """Return {'name': ..., 'description': ...} from a skill's SKILL.md."""
    md = skill_dir / "SKILL.md"
    if not md.exists():
        return {"name": skill_dir.name, "description": ""}
    front = parse_skill_frontmatter(md.read_text(errors="replace"))
    name = front.get("name")
    description = front.get("description")
    return {
        "name": (str(name).strip() if name else skill_dir.name),
        "description": (str(description).strip() if description else ""),
    }

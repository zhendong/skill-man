from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def run(*args, env=None):
    e = os.environ.copy()
    if env:
        e.update(env)
    return subprocess.run(
        [sys.executable, "-m", "skill_man", *args],
        capture_output=True, text=True, env=e, cwd=ROOT,
    )


def make_repo(path: Path, skills: dict[str, tuple[str, str]]) -> Path:
    path.mkdir(parents=True)
    for slug, (name, desc) in skills.items():
        sd = path / "skills" / slug
        sd.mkdir(parents=True)
        (sd / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: {desc}\n---\n\nbody for {slug}\n"
        )
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=path, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-q", "-m", "init"], cwd=path, check=True)
    return path


def _canonical_local(p: Path) -> str:
    return str(p.resolve())


def _suffix_for(canonical: str) -> str:
    from skill_man.util import source_short_id
    return source_short_id(canonical)


def test_end_to_end_with_auto_sync_and_suffixed_links():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        agents_dir = tmp / "agents" / "skills"
        claude_dir = tmp / "claude" / "skills"
        state_root = tmp / "state"
        env = {
            "SKILL_MAN_ROOT": str(state_root),
            "SKILL_MAN_TARGET_DIRS": f"{agents_dir}:{claude_dir}",
        }

        repo_a = make_repo(tmp / "repo-a", {
            "hello-world": ("hello-world", "greets"),
            "farewell": ("farewell", "says goodbye"),
        })
        key_a = _canonical_local(repo_a)
        suf_a = _suffix_for(key_a)

        # `source add` should auto-sync — no separate `sync` call needed
        r = run("source", "add", str(repo_a), env=env)
        assert r.returncode == 0, r.stderr
        assert "[sync]" in r.stdout
        assert f"hello-world-{suf_a}" in r.stdout

        # Symlinks are suffixed with the source-hash
        for d in (agents_dir, claude_dir):
            link = d / f"hello-world-{suf_a}"
            assert link.is_symlink(), f"expected suffixed symlink at {link}; got: {list(d.iterdir())}"
            assert (link / "SKILL.md").exists()

        state = json.loads((state_root / "state.json").read_text())
        # State key is suffixed; raw slug is preserved in the record.
        rec = state["skills"][f"hello-world-{suf_a}"]
        assert rec["slug"] == "hello-world"
        assert rec["source"] == key_a

        # Add a SECOND source that ships the same slug. No filesystem collision.
        repo_b = make_repo(tmp / "repo-b", {
            "hello-world": ("hello-world-elsewhere", "different greeting"),
        })
        key_b = _canonical_local(repo_b)
        suf_b = _suffix_for(key_b)
        assert suf_a != suf_b

        r = run("source", "add", str(repo_b), env=env)
        assert r.returncode == 0, r.stderr

        for d in (agents_dir, claude_dir):
            assert (d / f"hello-world-{suf_a}").is_symlink()
            assert (d / f"hello-world-{suf_b}").is_symlink()
            # Each resolves to its own source's content
            a_text = (d / f"hello-world-{suf_a}" / "SKILL.md").read_text()
            b_text = (d / f"hello-world-{suf_b}" / "SKILL.md").read_text()
            assert "name: hello-world" in a_text
            assert "name: hello-world-elsewhere" in b_text

        state = json.loads((state_root / "state.json").read_text())
        assert f"hello-world-{suf_a}" in state["skills"]
        assert f"hello-world-{suf_b}" in state["skills"]

        # Drop the farewell skill upstream and re-sync
        (repo_a / "skills" / "farewell" / "SKILL.md").unlink()
        (repo_a / "skills" / "farewell").rmdir()
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                        "commit", "-am", "drop"], cwd=repo_a, check=True)
        r = run("sync", "--source", "repo-a", env=env)
        assert r.returncode == 0, r.stderr
        state = json.loads((state_root / "state.json").read_text())
        assert f"farewell-{suf_a}" not in state["skills"]
        for d in (agents_dir, claude_dir):
            assert not (d / f"farewell-{suf_a}").exists()

        # Foreign symlink stays put
        foreign = tmp / "external"
        foreign.mkdir()
        (foreign / "SKILL.md").write_text("foreign\n")
        my_link = claude_dir / "my-own"
        my_link.symlink_to(foreign)
        r = run("refresh", env=env)
        assert r.returncode == 0, r.stderr
        assert my_link.is_symlink()

        # `sync --skill` accepts the raw slug if unambiguous; ambiguous here -> error
        r = run("sync", "--skill", "hello-world", env=env)
        assert r.returncode != 0
        assert "ambiguous" in r.stderr

        # By state key, unambiguous
        r = run("sync", "--skill", f"hello-world-{suf_a}", env=env)
        assert r.returncode == 0, r.stderr


def test_url_canonicalization():
    from skill_man.sources import canonicalize_url
    from skill_man.util import source_dir_for
    variants = [
        "https://github.com/obra/superpowers",
        "https://github.com/obra/superpowers.git",
        "https://github.com/obra/superpowers/",
        "https://GITHUB.com/obra/superpowers",
        "git@github.com:obra/superpowers",
        "git@github.com:obra/superpowers.git",
    ]
    keys = [canonicalize_url(v) for v in variants]
    assert all(k == "github.com/obra/superpowers" for k in keys), keys
    dirs = {source_dir_for(k).name for k in keys}
    assert len(dirs) == 1, dirs


def test_github_mirror_rewriting():
    from skill_man.sources import _apply_github_mirror
    cases_host_swap = [
        ("https://github.com/obra/superpowers.git",        "kgithub.com",            "https://kgithub.com/obra/superpowers.git"),
        ("https://GITHUB.com/obra/superpowers",            "kgithub.com",            "https://kgithub.com/obra/superpowers"),
        ("git@github.com:obra/superpowers.git",            "kgithub.com",            "https://kgithub.com/obra/superpowers.git"),
    ]
    cases_prefix = [
        ("https://github.com/obra/superpowers.git",        "https://ghproxy.com",    "https://ghproxy.com/https://github.com/obra/superpowers.git"),
        ("git@github.com:obra/superpowers.git",            "https://ghproxy.com/",   "https://ghproxy.com/https://github.com/obra/superpowers.git"),
    ]
    for input_url, mirror, want in cases_host_swap + cases_prefix:
        os.environ["SKILL_MAN_GITHUB_MIRROR"] = mirror
        got = _apply_github_mirror(input_url)
        assert got == want, f"in={input_url} mirror={mirror} got={got} want={want}"

    # Non-github URLs untouched
    os.environ["SKILL_MAN_GITHUB_MIRROR"] = "kgithub.com"
    assert _apply_github_mirror("https://gitlab.com/x/y") == "https://gitlab.com/x/y"
    assert _apply_github_mirror("/local/path") == "/local/path"

    # No mirror configured -> untouched
    os.environ.pop("SKILL_MAN_GITHUB_MIRROR", None)
    assert _apply_github_mirror("https://github.com/x/y.git") == "https://github.com/x/y.git"


def test_duplicate_url_rejected():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        env = {
            "SKILL_MAN_ROOT": str(tmp / "state"),
            "SKILL_MAN_TARGET_DIRS": f"{tmp/'a'}:{tmp/'c'}",
        }
        repo = make_repo(tmp / "r", {"x": ("x", "x")})
        r = run("source", "add", str(repo), env=env)
        assert r.returncode == 0, r.stderr
        r = run("source", "add", str(repo) + "/", env=env)
        assert r.returncode != 0
        assert "already added" in r.stderr


def test_hook_records_and_stats_identifies_managed():
    """The hook receives whatever identifier Claude Code uses to refer to a
    skill — i.e. the folder name in the agent's discovery dir, which is our
    state key (e.g. `brainstorming-0f764f`). Stats must match against state
    keys, not against the SKILL.md `name:` field.
    """
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        env = {
            "SKILL_MAN_ROOT": str(tmp / "state"),
            "SKILL_MAN_TARGET_DIRS": f"{tmp/'a'}:{tmp/'c'}",
        }
        repo = make_repo(tmp / "repo", {"brainstorming": ("brainstorming", "explore")})
        r = run("source", "add", str(repo), env=env)
        assert r.returncode == 0, r.stderr

        state = json.loads((tmp / "state" / "state.json").read_text())
        managed_key = next(iter(state["skills"]))
        assert managed_key.startswith("brainstorming-")
        unmanaged_key = "tdd-deadbe"  # something Claude Code might also send

        def fire(payload: str):
            r = subprocess.run(
                [sys.executable, "-m", "skill_man", "hook"],
                input=payload, capture_output=True, text=True,
                env={**os.environ, **env},
            )
            assert r.returncode == 0, r.stderr

        # The hook payload uses the same folder name Claude Code sees in
        # `~/.claude/skills/`, which is our state key.
        fire(json.dumps({
            "tool_name": "Skill",
            "tool_input": {"skill": managed_key},
            "session_id": "S1",
        }))
        fire(json.dumps({
            "tool_name": "Skill",
            "tool_input": {"skill": managed_key},
            "session_id": "S1",
        }))
        fire(json.dumps({
            "tool_name": "Skill",
            "tool_input": {"skill": unmanaged_key},
            "session_id": "S2",
        }))

        # Non-Skill tools are ignored
        fire(json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls"}}))

        # Malformed JSON and missing skill name do not crash
        fire("not json")
        fire("")
        fire(json.dumps({"tool_name": "Skill", "tool_input": {}}))

        log = (tmp / "state" / "stats" / "usage.jsonl").read_text().strip().splitlines()
        assert len(log) == 3, log

        r = run("stats", "--days", "1", env=env)
        assert r.returncode == 0, r.stderr
        assert managed_key in r.stdout
        assert unmanaged_key in r.stdout
        lines = r.stdout.splitlines()
        bs_line = next(l for l in lines if managed_key in l and "COUNT" not in l)
        tdd_line = next(l for l in lines if unmanaged_key in l and "COUNT" not in l)
        assert not bs_line.lstrip().startswith("*") and bs_line.startswith(" "), \
            f"managed skill should not be marked *: {bs_line!r}"
        assert tdd_line.startswith("*"), \
            f"unmanaged skill should be marked *: {tdd_line!r}"
        assert "unused in window:         0" in r.stdout

        # Substring filter still works against suffixed state keys
        r = run("stats", "--days", "1", "--skill", "brainstorming", env=env)
        assert r.returncode == 0, r.stderr
        assert managed_key in r.stdout
        assert unmanaged_key not in r.stdout


def test_install_hook_write_idempotent_and_safe():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        home = tmp / "home"
        env = {
            "HOME": str(home),
            "SKILL_MAN_ROOT": str(tmp / "state"),
        }
        # First write
        r = run("install-hook", "--write", env=env)
        assert r.returncode == 0, r.stderr
        settings_path = home / ".claude" / "settings.json"
        cfg = json.loads(settings_path.read_text())
        assert len(cfg["hooks"]["PreToolUse"]) == 1

        # Idempotent
        r = run("install-hook", "--write", env=env)
        assert r.returncode == 0, r.stderr
        assert "already present" in r.stdout
        cfg = json.loads(settings_path.read_text())
        assert len(cfg["hooks"]["PreToolUse"]) == 1

        # Preserves unrelated settings
        cfg["env"] = {"FOO": "bar"}
        cfg["hooks"].setdefault("UserPromptSubmit", []).append(
            {"matcher": "", "hooks": [{"type": "command", "command": "echo other"}]}
        )
        settings_path.write_text(json.dumps(cfg))
        r = run("install-hook", "--write", env=env)
        assert r.returncode == 0, r.stderr
        cfg2 = json.loads(settings_path.read_text())
        assert cfg2["env"] == {"FOO": "bar"}
        assert len(cfg2["hooks"]["UserPromptSubmit"]) == 1
        assert len(cfg2["hooks"]["PreToolUse"]) == 1

        # Refuses to clobber malformed JSON
        settings_path.write_text("not json {{{")
        r = run("install-hook", "--write", env=env)
        assert r.returncode != 0
        assert "not valid JSON" in r.stderr
        assert settings_path.read_text() == "not json {{{"


def test_source_add_with_skill_whitelist():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        env = {
            "SKILL_MAN_ROOT": str(tmp / "state"),
            "SKILL_MAN_TARGET_DIRS": f"{tmp/'a'}:{tmp/'c'}",
        }
        repo = make_repo(tmp / "repo", {
            "alpha": ("alpha", "a"),
            "beta": ("beta", "b"),
            "gamma": ("gamma", "g"),
        })

        # Add with whitelist of two; the third is discovered but disabled
        r = run("source", "add", str(repo), "alpha,beta", env=env)
        assert r.returncode == 0, r.stderr
        assert "enabled: alpha, beta" in r.stdout
        assert "disabled: gamma" in r.stdout

        state = json.loads((tmp / "state" / "state.json").read_text())
        rows = {info["slug"]: info["enabled"] for info in state["skills"].values()}
        assert rows == {"alpha": True, "beta": True, "gamma": False}

        # Only enabled skills have symlinks
        agents = tmp / "a"
        suf = next(iter(state["skills"])).rsplit("-", 1)[1]
        assert (agents / f"alpha-{suf}").is_symlink()
        assert (agents / f"beta-{suf}").is_symlink()
        assert not (agents / f"gamma-{suf}").exists()

        # Re-sync preserves the enabled flags
        r = run("sync", env=env)
        assert r.returncode == 0, r.stderr
        state = json.loads((tmp / "state" / "state.json").read_text())
        rows = {info["slug"]: info["enabled"] for info in state["skills"].values()}
        assert rows == {"alpha": True, "beta": True, "gamma": False}


def test_source_add_validates_skill_names_and_rolls_back():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        env = {
            "SKILL_MAN_ROOT": str(tmp / "state"),
            "SKILL_MAN_TARGET_DIRS": f"{tmp/'a'}:{tmp/'c'}",
        }
        repo = make_repo(tmp / "repo", {"alpha": ("alpha", "a")})

        r = run("source", "add", str(repo), "alpha,bogus", env=env)
        assert r.returncode != 0
        assert "not found in source" in r.stderr
        assert "bogus" in r.stderr
        assert "available: alpha" in r.stderr

        # State must be empty — no source, no skills, no clone left behind
        state = json.loads((tmp / "state" / "state.json").read_text())
        assert state["sources"] == {}
        assert state["skills"] == {}
        assert not (tmp / "state" / "sources").exists() or \
               not any((tmp / "state" / "sources").iterdir())


def test_enable_disable_toggles_state_and_symlink():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        env = {
            "SKILL_MAN_ROOT": str(tmp / "state"),
            "SKILL_MAN_TARGET_DIRS": f"{tmp/'a'}:{tmp/'c'}",
        }
        repo = make_repo(tmp / "repo", {"foo": ("foo", "f"), "bar": ("bar", "b")})
        r = run("source", "add", str(repo), env=env)
        assert r.returncode == 0, r.stderr

        state = json.loads((tmp / "state" / "state.json").read_text())
        foo_key = next(k for k, info in state["skills"].items() if info["slug"] == "foo")

        # both symlinks present
        for d in (tmp / "a", tmp / "c"):
            assert (d / foo_key).is_symlink()

        # disable by slug
        r = run("disable", "foo", env=env)
        assert r.returncode == 0, r.stderr
        state = json.loads((tmp / "state" / "state.json").read_text())
        assert state["skills"][foo_key]["enabled"] is False
        for d in (tmp / "a", tmp / "c"):
            assert not (d / foo_key).exists()

        # idempotent
        r = run("disable", "foo", env=env)
        assert r.returncode == 0, r.stderr
        assert "already disabled" in r.stdout

        # sync preserves disabled
        r = run("sync", env=env)
        assert r.returncode == 0, r.stderr
        state = json.loads((tmp / "state" / "state.json").read_text())
        assert state["skills"][foo_key]["enabled"] is False
        for d in (tmp / "a", tmp / "c"):
            assert not (d / foo_key).exists()

        # enable by state key
        r = run("enable", foo_key, env=env)
        assert r.returncode == 0, r.stderr
        state = json.loads((tmp / "state" / "state.json").read_text())
        assert state["skills"][foo_key]["enabled"] is True
        for d in (tmp / "a", tmp / "c"):
            assert (d / foo_key).is_symlink()

        # unknown skill errors
        r = run("disable", "nonexistent", env=env)
        assert r.returncode != 0


def test_stats_excludes_disabled_from_managed():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        env = {
            "SKILL_MAN_ROOT": str(tmp / "state"),
            "SKILL_MAN_TARGET_DIRS": f"{tmp/'a'}:{tmp/'c'}",
        }
        repo = make_repo(tmp / "repo", {"x": ("x", "x"), "y": ("y", "y")})
        r = run("source", "add", str(repo), "x", env=env)
        assert r.returncode == 0, r.stderr
        # x is enabled, y is disabled
        # Fire a hook event for y (the disabled one) — should show as `*`
        state = json.loads((tmp / "state" / "state.json").read_text())
        y_key = next(k for k, info in state["skills"].items() if info["slug"] == "y")

        r = subprocess.run(
            [sys.executable, "-m", "skill_man", "hook"],
            input=json.dumps({"tool_name": "Skill",
                              "tool_input": {"skill": y_key},
                              "session_id": "S1"}),
            capture_output=True, text=True,
            env={**os.environ, **env},
        )
        assert r.returncode == 0, r.stderr

        r = run("stats", "--days", "1", env=env)
        assert r.returncode == 0, r.stderr
        # `managed skills` should be 1, not 2 (disabled is excluded)
        assert "managed skills:           1" in r.stdout
        # y should appear with `*` (unmanaged from stats' POV)
        y_line = next(l for l in r.stdout.splitlines() if y_key in l and "COUNT" not in l)
        assert y_line.startswith("*"), f"disabled skill should be marked unmanaged: {y_line!r}"


def test_dirs_created_on_demand():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        env = {
            "SKILL_MAN_ROOT": str(tmp / "fresh"),
            "SKILL_MAN_TARGET_DIRS": f"{tmp/'a'}:{tmp/'b'}",
        }
        assert not (tmp / "fresh").exists()
        repo = make_repo(tmp / "repo", {"sk1": ("sk1", "first")})
        r = run("source", "add", str(repo), env=env)
        assert r.returncode == 0, r.stderr
        assert (tmp / "fresh" / "state.json").exists()
        # auto-sync should have populated state["skills"]
        state = json.loads((tmp / "fresh" / "state.json").read_text())
        assert len(state["skills"]) == 1


if __name__ == "__main__":
    test_url_canonicalization()
    test_github_mirror_rewriting()
    test_duplicate_url_rejected()
    test_source_add_with_skill_whitelist()
    test_source_add_validates_skill_names_and_rolls_back()
    test_enable_disable_toggles_state_and_symlink()
    test_stats_excludes_disabled_from_managed()
    test_dirs_created_on_demand()
    test_hook_records_and_stats_identifies_managed()
    test_install_hook_write_idempotent_and_safe()
    test_end_to_end_with_auto_sync_and_suffixed_links()
    print("OK")

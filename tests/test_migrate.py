from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _run(*args, env=None, input_=None):
    e = os.environ.copy()
    if env:
        e.update(env)
    return subprocess.run(
        [sys.executable, "-m", "skman", *args],
        input=input_, capture_output=True, text=True, env=e, cwd=ROOT,
    )


def _write_skill(dir_path: Path, name: str, desc: str = "test skill") -> Path:
    dir_path.mkdir(parents=True)
    (dir_path / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {desc}\n---\n\nbody\n"
    )
    return dir_path


def _make_git_repo_with_skill(repo: Path, slug: str, name: str) -> Path:
    sd = repo / "skills" / slug
    sd.mkdir(parents=True)
    (sd / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: from repo\n---\nbody\n"
    )
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-q", "-m", "init"], cwd=repo, check=True)
    return repo


def _scenario(tmp: Path) -> dict[str, str]:
    """Build a HOME-rooted environment for migrate tests."""
    home = tmp / "home"
    (home / ".claude" / "skills").mkdir(parents=True)
    (home / ".codex" / "skills").mkdir(parents=True)
    (home / ".agents" / "skills").mkdir(parents=True)
    return {
        "HOME": str(home),
        "SKMAN_ROOT": str(home / ".skman"),
    }


def test_migrate_adopts_loose_claude_skill_as_local_source():
    """A SKILL.md dropped into ~/.claude/skills/<name>/ with no git origin
    should be adopted: copied into ~/.skman/imported/, registered as a local
    source, and the original removed."""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        env = _scenario(tmp)
        home = Path(env["HOME"])
        skill_dir = home / ".claude" / "skills" / "loose-skill"
        _write_skill(skill_dir, "loose-skill")

        r = _run("migrate", "--yes", env=env)
        assert r.returncode == 0, r.stderr

        state = json.loads((home / ".skman" / "state.json").read_text())
        slugs = [info["slug"] for info in state["skills"].values()]
        assert "loose-skill" in slugs

        # Original removed; skman provides its own suffixed symlink instead.
        assert not skill_dir.exists()
        suffixed = list((home / ".claude" / "skills").iterdir())
        assert any(p.name.startswith("loose-skill-") and p.is_symlink()
                   for p in suffixed), suffixed


def test_migrate_uses_skill_lock_for_git_source():
    """When ~/.agents/.skill-lock.json (skills.sh v3 schema) maps a skill name
    to a sourceUrl, migrate should register that URL as a git source instead
    of adopting locally."""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        env = _scenario(tmp)
        home = Path(env["HOME"])

        # Build a real git repo to serve as the "upstream"
        upstream = _make_git_repo_with_skill(
            tmp / "upstream", "lockskill", "lockskill",
        )

        # Drop a SKILL.md in ~/.agents/skills/ — content doesn't matter; the
        # lockfile is authoritative for the source URL.
        loose = home / ".agents" / "skills" / "lockskill"
        _write_skill(loose, "lockskill")

        # Omit skillFolderHash so the lockfile entry is used without
        # hash verification. (Other tests cover the match/mismatch paths.)
        # Real skills.sh v3 schema uses sourceUrl (camelCase).
        (home / ".agents" / ".skill-lock.json").write_text(json.dumps({
            "version": 3,
            "skills": {
                "lockskill": {
                    "source": "fake-org/lockskill",  # short form, not a URL
                    "sourceType": "github",
                    "sourceUrl": str(upstream),
                    "skillPath": "skills/lockskill/SKILL.md",
                },
            },
        }))

        r = _run("migrate", "--yes", env=env)
        assert r.returncode == 0, r.stderr

        state = json.loads((home / ".skman" / "state.json").read_text())
        # Source was registered using the upstream path, not the loose dir.
        assert any(str(upstream.resolve()) in k for k in state["sources"]), \
            list(state["sources"].keys())

        # The loose original was removed.
        assert not loose.exists()


def test_migrate_detects_git_origin_when_no_lock():
    """If a skill is inside a git checkout (no skill-lock), the enclosing
    repo's `origin` URL becomes the source."""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        env = _scenario(tmp)
        home = Path(env["HOME"])

        # "Upstream" we'll point origin at — must be a real git dir. Allow
        # pushes into its checked-out branch so we can sync the test's new
        # commit before migrate runs the dirty-check.
        upstream = _make_git_repo_with_skill(
            tmp / "upstream", "skl", "skl",
        )
        subprocess.run(
            ["git", "-C", str(upstream), "config",
             "receive.denyCurrentBranch", "ignore"],
            check=True,
        )

        # Clone of upstream landing at ~/.claude/skills/... (unusual layout,
        # but enough to make `git remote get-url origin` return upstream).
        checkout = home / ".claude" / "skills" / "checkout-host"
        subprocess.run(["git", "clone", "-q", str(upstream), str(checkout)],
                       check=True)
        # Migrate scans top-level dirs of ~/.claude/skills; the checkout dir
        # itself has no SKILL.md at its root, so create + commit one so the
        # working tree stays clean (otherwise the dirty-check would skip it).
        (checkout / "SKILL.md").write_text(
            "---\nname: skl-host\ndescription: x\n---\nbody\n"
        )
        subprocess.run(
            ["git", "-c", "user.email=t@t", "-c", "user.name=t",
             "add", "-A"], cwd=checkout, check=True,
        )
        subprocess.run(
            ["git", "-c", "user.email=t@t", "-c", "user.name=t",
             "commit", "-q", "-m", "add host SKILL.md"], cwd=checkout, check=True,
        )
        # And push so HEAD matches the upstream tracking branch.
        subprocess.run(
            ["git", "push", "-q", "origin", "main"], cwd=checkout, check=True,
        )

        r = _run("migrate", "--yes", env=env)
        assert r.returncode == 0, r.stderr

        state = json.loads((home / ".skman" / "state.json").read_text())
        # Source should have been added using the upstream URL discovered via
        # `git remote get-url origin`.
        src_keys = list(state["sources"].keys())
        assert any(str(upstream.resolve()) in k for k in src_keys), src_keys


def test_migrate_dry_run_changes_nothing():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        env = _scenario(tmp)
        home = Path(env["HOME"])
        sd = home / ".claude" / "skills" / "dryskill"
        _write_skill(sd, "dryskill")

        r = _run("migrate", "--dry-run", "--yes", env=env)
        assert r.returncode == 0, r.stderr
        assert "dry run" in r.stdout.lower()

        # Original untouched; no state, no imported dir.
        assert (sd / "SKILL.md").exists()
        assert not (home / ".skman" / "state.json").exists()
        assert not (home / ".skman" / "imported").exists()


def test_migrate_skips_already_managed_symlinks():
    """Symlinks already pointing into ~/.skman/sources/ must be ignored."""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        env = _scenario(tmp)
        home = Path(env["HOME"])

        # Add a normal source first so skman symlinks exist.
        repo = _make_git_repo_with_skill(tmp / "src", "alpha", "alpha")
        r = _run("source", "add", str(repo), env=env)
        assert r.returncode == 0, r.stderr

        # Now migrate finds nothing — the alpha-xxxx symlink is managed.
        r = _run("migrate", "--yes", env=env)
        assert r.returncode == 0, r.stderr
        assert "nothing to migrate" in r.stdout


def test_migrate_keep_originals_flag():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        env = _scenario(tmp)
        home = Path(env["HOME"])
        sd = home / ".claude" / "skills" / "keeper"
        _write_skill(sd, "keeper")

        r = _run("migrate", "--yes", "--keep-originals", env=env)
        assert r.returncode == 0, r.stderr
        # Original is still present.
        assert (sd / "SKILL.md").exists()
        # But skman also manages a copy.
        state = json.loads((home / ".skman" / "state.json").read_text())
        assert any(info["slug"] == "keeper" for info in state["skills"].values())


def test_migrate_discovers_codex_skills_and_skips_system():
    """Skills under ~/.codex/skills/ are migrated. Dot-prefixed entries (like
    Codex's built-in .system/) must be skipped."""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        env = _scenario(tmp)
        home = Path(env["HOME"])

        # A user skill in ~/.codex/skills — should be migrated.
        codex_skill = home / ".codex" / "skills" / "codex-user-skill"
        _write_skill(codex_skill, "codex-user-skill")

        # A built-in skill in ~/.codex/skills/.system/ — must be ignored.
        sys_skill = home / ".codex" / "skills" / ".system" / "plan"
        _write_skill(sys_skill, "plan")

        r = _run("migrate", "--yes", env=env)
        assert r.returncode == 0, r.stderr

        state = json.loads((home / ".skman" / "state.json").read_text())
        slugs = [info["slug"] for info in state["skills"].values()]
        assert "codex-user-skill" in slugs
        assert "plan" not in slugs  # .system entry was skipped

        # Built-in dir is untouched.
        assert (sys_skill / "SKILL.md").exists()


def test_links_skip_codex_target_dir():
    """skman should NOT link into ~/.codex/skills — Codex reads from
    ~/.agents/skills as its cross-agent fallback, so linking there is enough."""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        env = _scenario(tmp)
        home = Path(env["HOME"])

        repo = _make_git_repo_with_skill(tmp / "src", "shared", "shared")
        r = _run("source", "add", str(repo), env=env)
        assert r.returncode == 0, r.stderr

        codex_links = [p for p in (home / ".codex" / "skills").iterdir()
                       if p.is_symlink()]
        assert codex_links == [], \
            f"expected no symlinks in ~/.codex/skills, found: {[p.name for p in codex_links]}"

        # But the Claude + agents dirs should have the skill.
        for d in (home / ".claude" / "skills", home / ".agents" / "skills"):
            assert any(p.name.startswith("shared-") and p.is_symlink()
                       for p in d.iterdir()), list(d.iterdir())


def test_migrate_skips_skill_in_dirty_git_checkout():
    """When a skill lives inside a git checkout that has uncommitted changes
    or unpushed commits, re-cloning would lose those changes. Migrate must
    skip it, leave the original alone, and tell the user what to do."""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        env = _scenario(tmp)
        home = Path(env["HOME"])

        # Upstream repo, then a "checkout" landing under ~/.claude/skills/.
        upstream = _make_git_repo_with_skill(tmp / "upstream", "dirtyskill", "x")
        checkout = home / ".claude" / "skills" / "dirtyskill"
        subprocess.run(["git", "clone", "-q", str(upstream), str(checkout)],
                       check=True)
        # Move the skill content up so SKILL.md is at the entry root.
        skill_md_src = checkout / "skills" / "dirtyskill" / "SKILL.md"
        (checkout / "SKILL.md").write_text(skill_md_src.read_text())
        # And introduce an uncommitted local edit.
        (checkout / "SKILL.md").write_text(
            "---\nname: dirtyskill\ndescription: locally edited\n---\nlocal mod\n"
        )

        r = _run("migrate", "--yes", env=env)
        assert r.returncode == 0, r.stderr
        assert "Skipping skills with local git changes" in r.stdout
        assert "dirtyskill" in r.stdout
        assert "uncommitted" in r.stdout.lower() or "dirty" in r.stdout.lower()

        # Original untouched.
        assert (checkout / "SKILL.md").exists()
        # No source was added.
        state_file = home / ".skman" / "state.json"
        if state_file.exists():
            state = json.loads(state_file.read_text())
            assert state["sources"] == {}, state["sources"]


def test_compute_skill_folder_hash_matches_git_write_tree():
    """Our pure-Python tree hash must match what `git write-tree` produces
    for the same folder. Critical correctness check — if this drifts,
    every lockfile-hash verification turns into a false dirty warning."""
    from skman.migrate import compute_skill_folder_hash

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        folder = tmp / "skill"
        folder.mkdir()
        (folder / "SKILL.md").write_text(
            "---\nname: x\ndescription: y\n---\nbody\n"
        )
        (folder / "scripts").mkdir()
        (folder / "scripts" / "run.sh").write_text("#!/bin/sh\necho hi\n")
        os.chmod(folder / "scripts" / "run.sh", 0o755)
        (folder / "references").mkdir()
        (folder / "references" / "data.txt").write_text("ref\n")

        # Compute via real git: init a bare repo, populate an index with the
        # folder contents, write-tree.
        bare = tmp / "bare.git"
        idx = tmp / "idx"
        subprocess.run(["git", "init", "--bare", "--quiet", str(bare)], check=True)

        env = {**os.environ,
               "GIT_DIR": str(bare),
               "GIT_INDEX_FILE": str(idx),
               "GIT_WORK_TREE": str(folder)}
        subprocess.run(["git", "add", "-A"], cwd=folder, env=env, check=True)
        proc = subprocess.run(["git", "write-tree"],
                              env=env, capture_output=True, text=True, check=True)
        git_hash = proc.stdout.strip()

        ours = compute_skill_folder_hash(folder)
        assert ours == git_hash, f"\n  ours: {ours}\n  git : {git_hash}"


def test_migrate_skips_lockfile_hash_mismatch():
    """If skill-lock says hash=H but the local folder hashes to something
    else, the user has modified the skill in place — skip migration."""
    from skman.migrate import compute_skill_folder_hash

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        env = _scenario(tmp)
        home = Path(env["HOME"])

        upstream = _make_git_repo_with_skill(tmp / "upstream", "hskill", "hskill")

        loose = home / ".agents" / "skills" / "hskill"
        _write_skill(loose, "hskill")
        # Compute the hash that "would have been" recorded at install time…
        installed_hash = compute_skill_folder_hash(loose)
        assert installed_hash is not None
        # …then edit the skill locally so the on-disk hash drifts.
        (loose / "SKILL.md").write_text(
            "---\nname: hskill\ndescription: locally edited!\n---\nedited\n"
        )

        (home / ".agents" / ".skill-lock.json").write_text(json.dumps({
            "version": 3,
            "skills": {
                "hskill": {
                    "sourceUrl": str(upstream),
                    "skillFolderHash": installed_hash,
                },
            },
        }))

        r = _run("migrate", "--yes", env=env)
        assert r.returncode == 0, r.stderr
        assert "Skipping skills with local" in r.stdout
        assert "hskill" in r.stdout
        assert "lockfile hash" in r.stdout

        # Original kept; nothing added.
        assert (loose / "SKILL.md").exists()
        state_file = home / ".skman" / "state.json"
        if state_file.exists():
            assert json.loads(state_file.read_text())["sources"] == {}


def test_migrate_accepts_lockfile_hash_match():
    """Sanity-check the positive path: hash matches, migrate proceeds."""
    from skman.migrate import compute_skill_folder_hash

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        env = _scenario(tmp)
        home = Path(env["HOME"])

        upstream = _make_git_repo_with_skill(tmp / "upstream", "match", "match")

        loose = home / ".agents" / "skills" / "match"
        _write_skill(loose, "match")
        good_hash = compute_skill_folder_hash(loose)

        (home / ".agents" / ".skill-lock.json").write_text(json.dumps({
            "version": 3,
            "skills": {
                "match": {
                    "sourceUrl": str(upstream),
                    "skillFolderHash": good_hash,
                },
            },
        }))

        r = _run("migrate", "--yes", env=env)
        assert r.returncode == 0, r.stderr
        assert "Skipping skills with local" not in r.stdout

        state = json.loads((home / ".skman" / "state.json").read_text())
        assert any(str(upstream.resolve()) in k for k in state["sources"]), \
            list(state["sources"].keys())


def test_compute_hash_ignores_ds_store_etc():
    """OS noise (.DS_Store, __pycache__) must not affect the hash, otherwise
    every macOS user would see false-positive mismatches."""
    from skman.migrate import compute_skill_folder_hash

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        folder = tmp / "skill"
        folder.mkdir()
        (folder / "SKILL.md").write_text("---\nname: n\ndescription: d\n---\nb\n")
        baseline = compute_skill_folder_hash(folder)

        # Drop noise into the folder
        (folder / ".DS_Store").write_bytes(b"\x00\x01\x02")
        (folder / "__pycache__").mkdir()
        (folder / "__pycache__" / "x.pyc").write_bytes(b"junk")

        after = compute_skill_folder_hash(folder)
        assert baseline == after, f"hash changed after adding noise: {baseline} vs {after}"


def test_migrate_skips_unpushed_commits():
    """Same protection for commits that exist locally but aren't on the
    upstream branch yet."""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        env = _scenario(tmp)
        home = Path(env["HOME"])

        upstream = _make_git_repo_with_skill(tmp / "upstream", "unp", "x")
        checkout = home / ".claude" / "skills" / "unp"
        subprocess.run(["git", "clone", "-q", str(upstream), str(checkout)],
                       check=True)
        (checkout / "SKILL.md").write_text(
            "---\nname: unp\ndescription: x\n---\nbody\n"
        )
        # Commit a local change so the working tree is CLEAN but HEAD is
        # ahead of origin/main.
        subprocess.run(
            ["git", "-c", "user.email=t@t", "-c", "user.name=t",
             "add", "-A"], cwd=checkout, check=True,
        )
        subprocess.run(
            ["git", "-c", "user.email=t@t", "-c", "user.name=t",
             "commit", "-q", "-m", "local change"], cwd=checkout, check=True,
        )

        r = _run("migrate", "--yes", env=env)
        assert r.returncode == 0, r.stderr
        assert "Skipping skills with local git changes" in r.stdout
        assert "unp" in r.stdout
        # No source was added.
        state_file = home / ".skman" / "state.json"
        if state_file.exists():
            state = json.loads(state_file.read_text())
            assert state["sources"] == {}, state["sources"]


def test_migrate_is_idempotent():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        env = _scenario(tmp)
        home = Path(env["HOME"])
        _write_skill(home / ".claude" / "skills" / "once", "once")

        r1 = _run("migrate", "--yes", env=env)
        assert r1.returncode == 0, r1.stderr

        r2 = _run("migrate", "--yes", env=env)
        assert r2.returncode == 0, r2.stderr
        assert "nothing to migrate" in r2.stdout


def test_setup_runs_install_hook_and_migrate():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        env = _scenario(tmp)
        home = Path(env["HOME"])
        _write_skill(home / ".claude" / "skills" / "setupskill", "setupskill")

        r = _run("setup", "--yes", env=env)
        assert r.returncode == 0, r.stderr

        # Hook landed in settings.json
        cfg = json.loads((home / ".claude" / "settings.json").read_text())
        assert any(
            e.get("matcher") == "Skill"
            for e in cfg.get("hooks", {}).get("PreToolUse", [])
        )
        codex_cfg = json.loads((home / ".codex" / "hooks.json").read_text())
        assert any(
            e.get("matcher") == "^Skill$"
            for e in codex_cfg.get("hooks", {}).get("PreToolUse", [])
        )

        # Migrate ran
        state = json.loads((home / ".skman" / "state.json").read_text())
        assert any(info["slug"] == "setupskill" for info in state["skills"].values())


if __name__ == "__main__":
    test_migrate_adopts_loose_claude_skill_as_local_source()
    test_migrate_uses_skill_lock_for_git_source()
    test_migrate_detects_git_origin_when_no_lock()
    test_migrate_dry_run_changes_nothing()
    test_migrate_skips_already_managed_symlinks()
    test_migrate_keep_originals_flag()
    test_migrate_discovers_codex_skills_and_skips_system()
    test_links_skip_codex_target_dir()
    test_migrate_skips_skill_in_dirty_git_checkout()
    test_compute_skill_folder_hash_matches_git_write_tree()
    test_migrate_skips_lockfile_hash_mismatch()
    test_migrate_accepts_lockfile_hash_match()
    test_compute_hash_ignores_ds_store_etc()
    test_migrate_skips_unpushed_commits()
    test_migrate_is_idempotent()
    test_setup_runs_install_hook_and_migrate()
    print("OK")

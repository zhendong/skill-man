# skman

> 中文版:[README.zh-CN.md](README.zh-CN.md)

A dead simple CLI for managing skills used by coding agents — works with **Claude Code**,
**Codex CLI**, and any other agent that discovers skills from `~/.agents/skills`
(the cross-agent dir; also where `skills.sh` / `npx skills` installs).

## What it does

1. **Download** skills from git repos (or local directories).
2. **Sync** them on demand — pulls upstream, refreshes state, updates symlinks.
3. **Symlink** every managed skill into `~/.agents/skills` and
   `~/.claude/skills`. Codex picks the same skills up automatically via its
   cross-agent fallback to `~/.agents/skills`. The dirs are created on first
   sync — nothing to set up beforehand.
4. **Track state** in `~/.skman/state.json`: slug, name, description,
   source, short commit id, install/last-sync times, and enabled flag
   per skill.
5. **Disambiguate** skills from different sources by suffixing each
   symlink with a short id derived from the source URL, so two sources
   shipping the same skill name coexist without collision. A warning is
   still printed when `(name, description)` matches across sources, so
   you can spot true duplicates.
6. **Record usage** via a Claude Code `PreToolUse` hook and show aggregate
   stats.

> Note: pluggable user-edits-as-patches is intentionally out of scope for now.

## Install

### One-line install (recommended)

```bash
curl -fsSL https://raw.githubusercontent.com/zhendong/skill-man/main/install.sh | sh
```

Works on macOS and Linux. The installer uses [uv](https://docs.astral.sh/uv/)
to fetch a Python toolchain and install `skman` from PyPI into an isolated
environment — you don't need Python or pip beforehand.

Env overrides:
- `SKMAN_FROM_GIT=1` — install from the GitHub repo instead of PyPI (and
  `SKMAN_REF=<branch-or-tag>` to pick a ref).
- `SKMAN_NO_UV=1` — fall back to `pipx`/`pip` instead of uv.

### Via pip / pipx / uv

```bash
pipx install skman              # recommended for global CLI install
uv tool install skman           # uv equivalent
pip install --user skman        # plain pip
```

### From source

```bash
cd skill-man   # repo dir keeps its name; the tool is `skman`
pip install -e .          # exposes `skman` on PATH
# or:
uv tool install .
```

You can also run it without installing:

```bash
python3 -m skman <args>
```

State lives in `~/.skman/` (override with `$SKMAN_ROOT`).

### Windows

There is no native Windows build. Use **WSL** (Windows Subsystem for Linux) —
install a distro (Ubuntu/Debian/etc.), open its shell, and run the one-line
install above from inside the Linux environment. Your agent CLI (Claude
Code, Codex, etc.) should also run inside WSL so skman's symlinks land in
the Linux home dir where the agent looks for them.

## First-run setup

After install, the fastest way to a working state is:

```bash
skman setup
```

This installs the Claude Code usage hook and migrates any skills already
on disk (see below). It's safe to re-run.

## Quick start

```bash
skman source add https://github.com/obra/superpowers.git           # slug auto-derived as `superpowers`
skman sync                                              # clones, finds SKILL.md files, links into both target dirs

skman list                                              # see what's managed (with install/update times + commit)
skman install-hook --write                              # records skill invocations
skman stats                                              # see what got used
```

There is no `init` step. All directories — including `~/.agents/skills` and
`~/.claude/skills` — are created the first time something needs to write into
them.

### Source layout convention

Sources follow the standard pattern: a top-level `skills/` directory holding
one folder per skill, each with a `SKILL.md` plus any helper files:

```
<source-repo>/
└── skills/
    ├── brainstorming/
    │   └── SKILL.md
    └── tdd/
        ├── SKILL.md
        └── examples/
```

skman auto-detects: if `skills/` exists at the source root it scans
there; otherwise it scans the whole repo. Sub-categorisation (e.g.
`skills/foundations/tdd/`) is fine — `SKILL.md` is found recursively.

### Source identifiers

You don't pick a name. The slug is derived from the URL's last path segment
(lowercased, `.git` stripped, unsafe chars replaced):

| Input URL                                          | Derived slug         |
|----------------------------------------------------|----------------------|
| `https://github.com/obra/superpowers.git`          | `superpowers`        |
| `git@github.com:obra/superpowers`                  | `superpowers`        |
| `/Users/me/dev/my-skills`                          | `my-skills`          |
| second repo whose last segment is also `superpowers` | `superpowers-2`    |

Adding the same URL twice errors out — `https://h/o/r`, `https://h/o/r/`,
`https://h/o/r.git`, and `git@h:o/r` are all recognised as the same source.
Remove with `skman source remove <slug>` or `skman source remove <url>`.

## State

Everything lives in one JSON file: `~/.skman/state.json`.

```jsonc
{
  "version": 1,
  "sources": {
    "superpowers": { "type": "git", "url": "...", "ref": "main" }
  },
  "skills": {
    "brainstorming-ab12cd": {
      "slug": "brainstorming",
      "name": "brainstorming",
      "description": "You MUST use this before any creative work...",
      "source": "superpowers",
      "path": "skills/brainstorming",
      "commit": "a1b2c3d",
      "installed_at": "2026-05-14T10:00:00+00:00",
      "updated_at": "2026-05-14T12:00:00+00:00",
      "enabled": true
    }
  }
}
```

The map key (`brainstorming-ab12cd`) is also the symlink name in the
target dirs. The `-ab12cd` suffix is a 6-char hash of the source URL —
it lets two sources share a slug without collision.

`skman list` renders the state as a table:

```
LINK NAME             SLUG           SOURCE       COMMIT   STATUS    INSTALLED         UPDATED
brainstorming-ab12cd  brainstorming  superpowers  a1b2c3d  enabled   2026-05-14 10:00  2026-05-14 12:00
tdd-ab12cd            tdd            superpowers  a1b2c3d  enabled   2026-05-14 10:00  2026-05-14 12:00
```

## Duplicate detection

After every sync, skman groups skills by `(name, description)` from their
SKILL.md frontmatter and prints a warning when any pair appears in more
than one state entry — e.g. when two sources both ship a `brainstorming`
skill with identical frontmatter.

The warning is informational: both skills remain installed. Symlink names
include a short id derived from the source URL (`brainstorming-ab12cd`,
`brainstorming-ef34gh`), so there's no collision at the filesystem level.
Resolve true duplicates by removing one of the sources, or by disabling
one with `skman disable <link-name>`.

## Stats

`skman install-hook --write` adds a Claude Code `PreToolUse` hook so
every Skill tool call is recorded to `~/.skman/stats/usage.jsonl`.
`skman stats` aggregates:

- per-skill invocation count, distinct sessions, last-used time
- count of managed skills that went unused in the window

```bash
skman stats                    # last 30 days
skman stats --days 7
skman stats --skill brainstorming
```

## Migrating from other tools

If you've been using Claude Code, Codex, or `skills.sh` (`npx skills …`),
you'll likely have skills scattered across these dirs:

- `~/.claude/skills/*` — Claude Code personal skills
- `~/.codex/skills/*` — Codex personal skills (`.system/` is skipped — Codex
  built-ins live there)
- `~/.agents/skills/*` — cross-agent dir; also where `skills.sh` installs

`skman migrate` walks those locations, looks for `SKILL.md` dirs that
aren't already managed by skman, and adopts them:

- Reads `~/.agents/.skill-lock.json` (skills.sh v3) when present and uses
  the recorded `sourceUrl` — your `npx skills` installs become git sources
  tracked by skman, deduplicating skills that share a repo.
- Else, if the skill lives inside a git checkout, registers the enclosing
  repo as a git source via its `origin`.
- Else, copies the skill into `~/.skman/imported/<name>/` and registers
  that as a local source.

`skman migrate` refuses to overwrite skills you may have edited locally:

- **In a git checkout** with uncommitted changes or unpushed commits →
  skipped. Commit + push upstream, then re-run.
- **In `~/.agents/skills/` with a `skillFolderHash`** in
  `.skill-lock.json` (skills.sh v3) → the local folder's git tree SHA-1
  is recomputed and compared. A mismatch means the folder was edited
  after install; skman skips it. (Macros: `.DS_Store`, `__pycache__`,
  `.git`, `node_modules` are filtered to avoid false positives.)

In both cases skman tells you which skill, where it lives, and why —
then leaves it alone. Resolve manually (commit/push, or revert your
edits, or just don't manage it with skman) and re-run.

After migration, skman manages the skill via its own suffixed symlinks
(`brainstorming-ab12cd`) and removes the original loose copy so the host
agent doesn't see both.

```bash
skman migrate --dry-run            # preview what would happen
skman migrate                      # interactive (asks for confirmation)
skman migrate --yes                # non-interactive
skman migrate --keep-originals     # don't remove the on-disk copies after import
skman migrate --scan ~/elsewhere   # scan an additional dir (repeatable)
```

`skman setup` runs `install-hook --write` followed by `migrate` and is the
recommended first-run command.

## Commands

```
skman paths
skman setup      [--yes] [--keep-originals]
skman migrate    [--dry-run] [--yes] [--keep-originals] [--scan PATH]
skman source     add <url> [skills-to-enable] | remove <slug-or-url> | list
skman sync       [--source NAME | --skill SLUG]
skman list
skman refresh
skman enable     <skill>
skman disable    <skill>
skman stats      [--days N] [--skill SLUG]
skman hook
skman install-hook [--write]
```

`skills-to-enable` is an optional comma-separated whitelist of skill
slugs. When set, only those skills are enabled after sync; the rest are
recorded in state but left disabled (no symlink). Examples:

```bash
skman source add https://github.com/obra/superpowers.git              # enable everything in the source
skman source add https://github.com/obra/superpowers.git brainstorming,tdd
                                                                      # enable only those two; others stay disabled
```

### Environment overrides (advanced)

- `SKMAN_ROOT` — state dir (default `~/.skman`)
- `SKMAN_TARGET_DIRS` — colon-separated list of agent skill dirs
  (default `~/.agents/skills:~/.claude/skills`). Mainly used by tests.
- `SKMAN_GITHUB_MIRROR` — rewrite GitHub clone URLs through a mirror
  (useful in regions where `github.com` is slow or blocked). Two forms:
    - **Hostname** (e.g. `hub.fastgit.org`) — replaces `github.com` in
      the URL. `git@github.com:o/r` is converted to HTTPS first, so SSH
      sources work too.
    - **Full URL** (e.g. `https://ghproxy.com`) — treated as a prefix;
      the original `https://github.com/o/r` URL is appended.
  The original `url` recorded in `state.json` is unchanged; the mirror
  only applies at clone/fetch time, and sync prints the rewritten URL.

## Publishing (maintainers)

The version is read from `skman/__init__.py` (`__version__`). Bump it,
commit, then build and upload:

```bash
# 1. Bump skman/__init__.py __version__ and commit
# 2. Tag the release (optional but recommended)
git tag v$(python3 -c "import skman; print(skman.__version__)")
git push --tags

# 3. Build
python3 -m pip install --upgrade build twine
rm -rf dist/ && python3 -m build           # produces dist/skman-X.Y.Z-py3-none-any.whl and .tar.gz

# 4. Sanity-check the artifacts
python3 -m twine check dist/*

# 5. Upload to TestPyPI first, then PyPI
python3 -m twine upload --repository testpypi dist/*
python3 -m twine upload dist/*
```

Configure credentials in `~/.pypirc` (or use API tokens via
`TWINE_USERNAME=__token__ TWINE_PASSWORD=<pypi-token>`).

## License

[MIT](LICENSE).

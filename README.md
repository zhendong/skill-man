# skman

A CLI for managing skills used by coding agents (Claude Code and any other
agent that discovers skills from `~/.agents/skills` or `~/.claude/skills`).

## What it does

1. **Download** skills from git repos (or local directories).
2. **Sync** them on demand — pulls upstream, refreshes state, updates symlinks.
3. **Symlink** every managed skill into both `~/.agents/skills` and
   `~/.claude/skills`. The dirs are created on first sync — nothing to set up
   beforehand.
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

Requires Python 3.10+ and `git`.

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

## Commands

```
skman paths
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

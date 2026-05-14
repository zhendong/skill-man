from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import links, sources, stats
from . import sync as sync_mod
from .util import fmt_ts, load_state, save_state


def _parse_skill_list(raw: str | None) -> list[str] | None:
    if not raw:
        return None
    items = [s.strip() for s in raw.split(",") if s.strip()]
    return items or None


def cmd_source_add(args):
    only = _parse_skill_list(args.skills)
    sources.add_source(args.url, args.ref or "main", only=only)


def cmd_source_remove(args):
    sources.remove_source(args.name_or_url)


def cmd_source_list(args):
    sources.list_sources()


def cmd_sync(args):
    if args.skill:
        sync_mod.sync_skill(args.skill)
    elif args.source:
        sync_mod.sync_by_name(args.source)
    else:
        sync_mod.sync_all()


def cmd_list(args):
    state = load_state()
    skills = state.get("skills", {})
    if not skills:
        print("(no skills; add a source and run `skman sync`)")
        return
    rows = []
    for state_key, info in sorted(skills.items()):
        rows.append((
            state_key,
            info.get("slug", state_key),
            info.get("source", "-"),
            info.get("commit") or "-",
            "enabled" if info.get("enabled", True) else "disabled",
            fmt_ts(info.get("installed_at")),
            fmt_ts(info.get("updated_at")),
        ))
    w_key = max(len("LINK NAME"), max(len(r[0]) for r in rows))
    w_slug = max(len("SLUG"), max(len(r[1]) for r in rows))
    w_src = max(len("SOURCE"), max(len(r[2]) for r in rows))
    w_commit = max(len("COMMIT"), max(len(r[3]) for r in rows))
    w_status = max(len("STATUS"), max(len(r[4]) for r in rows))
    header = (f"{'LINK NAME':<{w_key}}  {'SLUG':<{w_slug}}  {'SOURCE':<{w_src}}  "
              f"{'COMMIT':<{w_commit}}  {'STATUS':<{w_status}}  {'INSTALLED':<16}  UPDATED")
    print(header)
    print("-" * len(header))
    for state_key, slug, src, commit, status, installed, updated in rows:
        print(f"{state_key:<{w_key}}  {slug:<{w_slug}}  {src:<{w_src}}  "
              f"{commit:<{w_commit}}  {status:<{w_status}}  {installed:<16}  {updated}")


def cmd_refresh(args):
    links.refresh_links()


def _set_enabled(skill_arg: str, enabled: bool) -> None:
    state = load_state()
    key = sync_mod.resolve_skill_key(skill_arg)
    if key is None:
        raise SystemExit(f"unknown skill: {skill_arg}")
    info = state["skills"][key]
    if info.get("enabled", True) == enabled:
        print(f"{key} is already {'enabled' if enabled else 'disabled'}")
        return
    info["enabled"] = enabled
    save_state(state)
    print(f"{'enabled' if enabled else 'disabled'} {key}")
    links.refresh_links()


def cmd_enable(args):
    _set_enabled(args.skill, True)


def cmd_disable(args):
    _set_enabled(args.skill, False)


def cmd_stats(args):
    stats.show_stats(days=args.days, skill=args.skill)


def cmd_hook(args):
    stats.hook_entrypoint()


def cmd_paths(args):
    from .util import root
    r = root()
    print(f"root:    {r}")
    print(f"state:   {r / 'state.json'}")
    print(f"sources: {r / 'sources'}")
    print(f"stats:   {r / 'stats'}")
    print("targets:")
    for d in links.target_dirs():
        print(f"  {d}")


def cmd_install_hook(args):
    entry = {
        "matcher": "Skill",
        "hooks": [{"type": "command", "command": "skman hook"}],
    }
    if args.write:
        settings = Path("~/.claude/settings.json").expanduser()
        settings.parent.mkdir(parents=True, exist_ok=True)
        cfg = {}
        if settings.exists():
            text = settings.read_text() or "{}"
            try:
                cfg = json.loads(text)
            except json.JSONDecodeError:
                raise SystemExit(
                    f"{settings} is not valid JSON; not modifying. Merge the hook manually."
                )
        hooks = cfg.setdefault("hooks", {})
        pre = hooks.setdefault("PreToolUse", [])
        already = any(
            e.get("matcher") == "Skill"
            and any(h.get("command") == "skman hook" for h in e.get("hooks", []))
            for e in pre
        )
        if already:
            print(f"hook already present in {settings}")
            return
        pre.append(entry)
        settings.write_text(json.dumps(cfg, indent=2, ensure_ascii=False))
        print(f"installed PreToolUse Skill hook into {settings}")
    else:
        print(json.dumps({"hooks": {"PreToolUse": [entry]}}, indent=2))
        print("\nMerge the above into ~/.claude/settings.json,")
        print("or run `skman install-hook --write` to do it automatically.")


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="skman",
        description="Manage and sync coding-agent skills.",
    )
    sp = ap.add_subparsers(dest="cmd", required=True)

    sp.add_parser("paths", help="print on-disk paths used by skman").set_defaults(func=cmd_paths)

    src = sp.add_parser("source", help="manage skill sources").add_subparsers(dest="sub", required=True)
    p = src.add_parser("add", help="register a git URL or local dir as a source")
    p.add_argument("url")
    p.add_argument("skills", nargs="?", default=None,
                   help="optional comma-separated whitelist of skill slugs to enable; "
                        "others from this source are kept in state but disabled")
    p.add_argument("--ref")
    p.set_defaults(func=cmd_source_add)
    p = src.add_parser("remove", help="remove a source by slug or URL")
    p.add_argument("name_or_url", metavar="slug-or-url")
    p.set_defaults(func=cmd_source_remove)
    src.add_parser("list").set_defaults(func=cmd_source_list)

    p = sp.add_parser("sync", help="fetch sources, refresh state, refresh symlinks")
    p.add_argument("--source", help="sync only this source")
    p.add_argument("--skill", help="sync only the source that provides this skill")
    p.set_defaults(func=cmd_sync)

    sp.add_parser("list", help="list managed skills with install/update times").set_defaults(func=cmd_list)
    sp.add_parser("refresh", help="re-create symlinks for every enabled skill").set_defaults(func=cmd_refresh)

    p = sp.add_parser("enable", help="enable a skill (re-create its symlink)")
    p.add_argument("skill"); p.set_defaults(func=cmd_enable)
    p = sp.add_parser("disable", help="disable a skill (remove its symlink; state preserved)")
    p.add_argument("skill"); p.set_defaults(func=cmd_disable)

    p = sp.add_parser("stats", help="show skill usage")
    p.add_argument("--days", type=int, default=30)
    p.add_argument("--skill")
    p.set_defaults(func=cmd_stats)

    sp.add_parser("hook", help="hook entrypoint (reads Claude Code hook JSON from stdin)").set_defaults(func=cmd_hook)

    p = sp.add_parser("install-hook", help="show or write Claude Code settings.json hook entry")
    p.add_argument("--write", action="store_true")
    p.set_defaults(func=cmd_install_hook)

    return ap


def main(argv: list[str] | None = None) -> None:
    ap = build_parser()
    args = ap.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()

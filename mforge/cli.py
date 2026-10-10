"""Command line interface: ``python macroforge.py <command>``."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Callable

from . import agent, shotcheck, setup, startup, watcher
from .cache import stage_caches
from .checker import run_checks
from .errors import MacroForgeError, ValidationError
from .fetch import fetch_all
from .fsutil import atomic_write_text
from .icons import load_icons
from .install import build_addon, create_slots, install
from .probe import render_report
from .savedvars import load_sv
from .settings import DEFAULT_ROOT, Settings
from .spec import load_spec, read_spec
from .spells import class_spells, load_spells
from .validate import strip_bad_chars
from .watcher import load_replies

DESCRIPTION = ("MacroForge: turn a macros.json spec into WoW macros. "
               "Run a command with --help for details.")
SCRIPT = Path(__file__).resolve().parent.parent / "macroforge.py"


def show(text: object) -> str:
    """Text from files or downloads, made safe to print (no terminal escape sequences)."""
    return "\n".join(strip_bad_chars(line, "?") for line in str(text).split("\n"))


def cmd_icon(settings: Settings, args: argparse.Namespace) -> int:
    """Search icon names."""
    words = [w.lower() for w in args.words]
    icons = load_icons(settings.data)
    hits = sorted((n, i) for n, i in icons.items() if all(w in n for w in words))
    for name, file_id in hits[:args.limit]:
        print(f"{file_id:>8}  {show(name)}")
    print(f"-- {len(hits)} match(es)", file=sys.stderr)
    return 0


def cmd_spell(settings: Settings, args: argparse.Namespace) -> int:
    """Show a spell's icon and ids."""
    hit = load_spells(settings.data).get(args.name.lower())
    if hit:
        print(f"{args.name}: icon {hit[0]}, spell ids {hit[1][:12]}")
    else:
        print(f"{args.name}: not found")
    return 0


def cmd_spells(settings: Settings, args: argparse.Namespace) -> int:
    """List every spell a class can learn and save the table for the agent."""
    rows = class_spells(settings.data, args.cls)
    if args.max_level:
        rows = [r for r in rows if 0 < r["level"] <= args.max_level]
    if args.find:
        rows = [r for r in rows if args.find.lower() in r["name"].lower()]
    name = args.cls.lower()  # known to be a real class: class_spells raised otherwise
    atomic_write_text(settings.data / f"spells_{name}.json", json.dumps(rows, indent=1))
    for r in rows:
        print(f"L{r['level']:>2}  {show(r['name']):<28} {show(r['rank']):<10} id={r['id']:<7} "
              f"icon={r['icon']:<8} [{show(r['line'])}]")
    print(f"-- {len(rows)} spell(s); saved data/spells_{name}.json", file=sys.stderr)
    return 0


def cmd_fetch(settings: Settings, args: argparse.Namespace) -> int:
    """Download the game data for the installed client build."""
    fetch_all(settings, args.build, args.no_icons)
    return 0


def cmd_build(settings: Settings, args: argparse.Namespace) -> int:
    """Generate the addon data from macros.json, then check the macros."""
    spec = load_spec(settings)
    build_addon(settings, spec, load_replies(settings))
    count = len(spec["account"]) + sum(len(v) for v in spec["chars"].values())
    print(f"built {count} macro(s) -> {settings.build}")
    run_checks(settings, read_spec(settings.spec))
    return 0


def cmd_install(settings: Settings, args: argparse.Namespace) -> int:
    """Copy the built addon into the game folder and refresh the slots."""
    dest, slots = install(settings)
    note = f" (+{slots} slots)" if slots else " (no slots installed; see `slots`)"
    print(f"installed -> {dest}{note}")
    print("In game: /mf live (needs slots) or /reload.")
    return 0


def cmd_slots(settings: Settings, args: argparse.Namespace) -> int:
    """Create the load-on-demand slot addons used by ``/mf live``."""
    addons = create_slots(settings, args.count)
    print(f"{args.count} slot addons in {addons}\n"
          "Fully restart WoW once so the client discovers them, then use /mf live.")
    return 0


def cmd_check(settings: Settings, args: argparse.Namespace) -> int:
    """Check macros.json for unknown commands, conditionals and spells."""
    run_checks(settings, read_spec(settings.spec))
    return 0


def cmd_cache(settings: Settings, args: argparse.Namespace) -> int:
    """Stage (and with --write, install) merged macros-cache.txt files."""
    for line in stage_caches(settings, load_spec(settings), args.write):
        print(line)
    return 0


def cmd_spellbook(settings: Settings, args: argparse.Namespace) -> int:
    """Print the saved variables the addon wrote."""
    path = settings.saved_vars_file
    if path.exists():
        print(show(path.read_text(encoding="utf-8", errors="replace")))
    else:
        print(f"{path} not found - run /mf export then /reload")
    return 0


def cmd_report(settings: Settings, args: argparse.Namespace) -> int:
    """Write the compatibility report from the probe results."""
    probe = load_sv(settings.saved_vars_file, "MacroForgeProbe")
    if not probe:
        raise ValidationError(
            "no probe data: in game run `/mf probe`, then `/reload`, then re-run this")
    report = render_report(probe)
    settings.data.mkdir(exist_ok=True)
    atomic_write_text(settings.data / "probe.json", json.dumps(probe, indent=1))
    out = settings.root / "reference" / f"compat-{report.toc}.md"
    out.parent.mkdir(exist_ok=True)
    atomic_write_text(out, report.text)
    print(f"wrote {out}\n{report.slash_count} slash commands, "
          f"{report.supported}/{report.total} conditionals confirmed supported")
    return 0


def cmd_watch(settings: Settings, args: argparse.Namespace) -> int:
    """Answer in-game ``/mf ask`` requests until interrupted."""
    watcher.run_watcher(settings)
    return 0


def cmd_login(settings: Settings, args: argparse.Namespace) -> int:
    """Open the Claude CLI so the user can sign in."""
    return agent.login(settings)


def cmd_startup(settings: Settings, args: argparse.Namespace) -> int:
    """Add or remove the Windows Startup launcher for the watcher."""
    if args.remove:
        print(f"removed {startup.remove_startup()}")
        return 0
    target = startup.install_startup(settings, SCRIPT, args.now)
    print(f"wrote {target}\n"
          "The watcher will start hidden at your next login (log: data/watcher.log).")
    if args.now:
        print("started it now")
    return 0


def cmd_rollback(settings: Settings, args: argparse.Namespace) -> int:
    """Restore the newest backup of macros.json."""
    folder = settings.data / "backups"
    backups = sorted(folder.glob("macros-*.json")) if folder.exists() else []
    if not backups:
        raise ValidationError("no backups yet")
    atomic_write_text(settings.spec, backups[-1].read_text(encoding="utf-8"))
    print(f"restored macros.json from {backups[-1].name}; run build + install to apply")
    return 0


def cmd_shotcheck(settings: Settings, args: argparse.Namespace) -> int:
    """Decode the data strip in the newest screenshot (diagnostic)."""
    print(shotcheck.shot_check(settings))
    return 0


def cmd_setup(settings: Settings | None, args: argparse.Namespace) -> int:
    """Guided first-time setup (runs before any settings exist, so it gets none)."""
    wizard = setup.Wizard(Path(os.environ.get("MACROFORGE_HOME") or DEFAULT_ROOT),
                          assume_yes=args.yes)
    return wizard.run(args.wow_dir, args.account, args.skip_fetch,
                      False if args.no_requests else None)


def build_parser() -> argparse.ArgumentParser:
    """The argument parser with every subcommand."""
    parser = argparse.ArgumentParser(prog="macroforge", description=DESCRIPTION)
    sub = parser.add_subparsers(dest="command", required=True, metavar="<command>")

    def add(name: str, func: Callable, help_text: str) -> argparse.ArgumentParser:
        """Register one subcommand and return its parser for extra arguments."""
        sp = sub.add_parser(name, help=help_text, description=help_text)
        sp.set_defaults(func=func)
        return sp

    sp = add("icon", cmd_icon, "search icon names, e.g. `icon skull`")
    sp.add_argument("words", nargs="+")
    sp.add_argument("-n", "--limit", type=int, default=40)
    sp = add("spell", cmd_spell, "show the icon FileDataID for a spell name")
    sp.add_argument("name")
    sp = add("spells", cmd_spells, "list every spell a class can learn (from the game tables)")
    sp.add_argument("cls", help="class file name, e.g. warlock")
    sp.add_argument("--max-level", type=int)
    sp.add_argument("--find", help="substring filter on spell name")
    sp = add("fetch", cmd_fetch,
             "download game data for the installed client build (run after each update)")
    sp.add_argument("--build", help="override the build, e.g. 1.60.1.70291")
    sp.add_argument("--no-icons", action="store_true", help="keep the existing icon list")
    add("build", cmd_build, "macros.json -> build/MacroForge/ (addon + data), then check it")
    add("install", cmd_install,
        "copy build/MacroForge/ into the game's AddOns folder and refresh the slots")
    sp = add("slots", cmd_slots,
             "create the load-on-demand slot addons for /mf live (restart WoW once after)")
    sp.add_argument("--count", type=int, default=120)
    add("check", cmd_check, "validate macros.json (commands, conditionals, spell names)")
    sp = add("cache", cmd_cache,
             "merge macros into macros-cache.txt (game must be closed with --write)")
    sp.add_argument("--write", action="store_true",
                    help="actually replace the game files (backup first)")
    add("spellbook", cmd_spellbook,
        "print the saved variables the addon wrote (/mf export, then /reload)")
    add("report", cmd_report, "turn /mf probe results into reference/compat-<toc>.md")
    add("watch", cmd_watch,
        "answer in-game `/mf ask` requests with a restricted headless Claude")
    add("login", cmd_login, "open the Claude CLI once so you can /login (needed by `watch`)")
    sp = add("startup", cmd_startup, "run the watcher hidden at Windows login")
    sp.add_argument("--remove", action="store_true", help="remove the Startup launcher")
    sp.add_argument("--now", action="store_true", help="also start it right away")
    add("rollback", cmd_rollback, "restore the last backup of macros.json")
    sp = add("setup", cmd_setup, "guided first-time setup (finds the game, installs everything)")
    sp.set_defaults(standalone=True)
    sp.add_argument("--wow-dir", help="client folder, e.g. C:/Games/WoW/_classic_beta_")
    sp.add_argument("--account", help="account folder name under WTF/Account")
    sp.add_argument("--yes", action="store_true", help="take the default answer to every question")
    sp.add_argument("--skip-fetch", action="store_true", help="do not download game data")
    sp.add_argument("--no-requests", action="store_true", help="skip the /mf ask watcher")
    add("shotcheck", cmd_shotcheck,
        "decode the request strip in the newest TGA screenshot (diagnostic)")
    return parser


def ensure_spec(settings: Settings) -> None:
    """``macros.json`` is personal and untracked: start from the example on first use."""
    example = settings.root / "macros.example.json"
    if not settings.spec.exists() and example.exists():
        shutil.copy2(example, settings.spec)
        print("created macros.json from macros.example.json")


def main(argv: list[str] | None = None) -> int:
    """Run the CLI; returns the process exit code."""
    args = build_parser().parse_args(argv)
    try:
        if getattr(args, "standalone", False):
            return args.func(None, args)
        settings = Settings.load()
        ensure_spec(settings)
        return args.func(settings, args)
    except MacroForgeError as exc:
        print(exc, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130

"""Guided first-time setup: find the game, write ``config.json``, download data, install the addon.

Run it with ``setup.cmd`` (or ``python macroforge.py setup``). It is safe to run again: it keeps
your ``macros.json``, only downloads what is missing and asks before changing anything optional.
"""
from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import agent, startup
from .errors import MacroForgeError, ValidationError
from .fetch import fetch_all, installed_build
from .fsutil import atomic_write_text
from .install import build_addon, check_addons_dir, create_slots, install
from .settings import Settings
from .spec import load_spec
from .watcher import load_replies

SLOT_COUNT = 120
FLAVOR_WORDS = ("beta", "classic", "era", "retail", "ptr", "xptr")  # folders like _classic_beta_


@dataclass(frozen=True)
class GameFolder:
    """One client folder (``_classic_beta_`` and friends) inside a World of Warcraft install."""

    path: Path
    version: str  # from .build.info, or "" when unknown

    @property
    def label(self) -> str:
        """How the folder is shown in a menu."""
        return f"{self.path}  (version {self.version or 'unknown'})"


def candidate_roots() -> list[Path]:
    """Places a World of Warcraft install usually lives."""
    roots: list[Path] = []
    for var in ("ProgramFiles(x86)", "ProgramFiles"):
        base = os.environ.get(var)
        if base:
            roots.append(Path(base) / "World of Warcraft")
    for drive in ("C:", "D:", "E:"):
        roots += [Path(drive + "\\World of Warcraft"), Path(drive + "\\Games\\World of Warcraft")]
    return roots


def read_versions(root: Path) -> dict[str, str]:
    """``{product: version}`` from Blizzard's ``.build.info`` (empty if it cannot be read)."""
    try:
        lines = (root / ".build.info").read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    if not lines:
        return {}
    columns = {h.split("!")[0]: i for i, h in enumerate(lines[0].split("|"))}
    product_at, version_at = columns.get("Product"), columns.get("Version")
    if product_at is None or version_at is None:
        return {}
    found = {}
    for row in lines[1:]:
        fields = row.split("|")
        if len(fields) > max(product_at, version_at):
            found[fields[product_at]] = fields[version_at]
    return found


def find_games(roots: list[Path] | None = None) -> list[GameFolder]:
    """Client folders that contain an ``Interface`` or ``WTF`` folder, under the given roots."""
    games: list[GameFolder] = []
    seen: set[Path] = set()
    for root in roots if roots is not None else candidate_roots():
        try:
            children = sorted(root.iterdir())
        except OSError:
            continue
        versions = read_versions(root)
        for child in children:
            name = child.name
            if not (name.startswith("_") and name.endswith("_") and child.is_dir()):
                continue
            if not any(word in name for word in FLAVOR_WORDS) or child.resolve() in seen:
                continue
            if (child / "Interface").is_dir() or (child / "WTF").is_dir():
                seen.add(child.resolve())
                games.append(GameFolder(child, versions.get("wow_" + name.strip("_"), "")))
    return games


def list_accounts(wow_dir: Path) -> list[str]:
    """Account folders under ``WTF/Account`` (they exist once you have logged in at least once)."""
    try:
        return sorted(d.name for d in (wow_dir / "WTF" / "Account").iterdir()
                      if d.is_dir() and d.name != "SavedVariables")
    except OSError:
        return []


def check_writable(addons: Path) -> None:
    """Raise a helpful error unless files can be created in the AddOns folder."""
    probe = addons / ".macroforge-write-test"
    try:
        probe.write_text("x", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        raise ValidationError(
            f"Cannot write to {addons} ({exc}).\n"
            "Windows protects 'Program Files'. Either run setup.cmd once as administrator "
            "(right-click, Run as administrator), or give your user write access to the "
            "AddOns folder.") from None


def write_config(root: Path, updates: dict) -> Path:
    """Merge ``updates`` into ``config.json`` (other keys are kept)."""
    path = root / "config.json"
    current: dict = {}
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as exc:
            raise ValidationError(f"cannot read {path}: {exc}") from None
        if isinstance(loaded, dict):
            current = loaded
    current.update(updates)
    atomic_write_text(path, json.dumps(current, indent=2) + "\n")
    return path


def _read_line(path: Path) -> str:
    """First line of a small text file, or an empty string."""
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


class Wizard:
    """The questions and steps of ``setup``; input and output are injected so tests can drive it."""

    def __init__(self, root: Path, ask: Callable[[str], str] = input,
                 out: Callable[[str], None] = print, assume_yes: bool = False,
                 fetch: Callable[..., None] = fetch_all,
                 roots: list[Path] | None = None) -> None:
        self.root = Path(root)
        self._ask, self.out, self.fetch, self.roots = ask, out, fetch, roots
        self.assume_yes = assume_yes

    # -- small helpers -----------------------------------------------------------------------
    def yes(self, question: str, default: bool = True) -> bool:
        """Ask a yes/no question (Enter takes the default)."""
        if self.assume_yes:
            return default
        answer = self._ask(f"{question} [{'Y/n' if default else 'y/N'}] ").strip().lower()
        return default if not answer else answer.startswith("y")

    def choose(self, title: str, options: list[str]) -> int:
        """Show a numbered menu and return the chosen index (a single option is taken as is)."""
        if len(options) == 1 or self.assume_yes:
            return 0
        self.out(title)
        for number, label in enumerate(options, 1):
            self.out(f"  {number}. {label}")
        while True:
            answer = self._ask("Number: ").strip()
            if answer.isdigit() and 1 <= int(answer) <= len(options):
                return int(answer) - 1
            self.out("Please type one of the numbers above.")

    # -- steps -------------------------------------------------------------------------------
    def pick_game(self, wow_dir: str | None) -> Path:
        """Find or ask for the client folder."""
        if wow_dir:
            path = Path(wow_dir)
            if not (path / "Interface").is_dir():
                raise ValidationError(f"{path} has no Interface folder; is it the client folder "
                                      "(for example ...\\World of Warcraft\\_classic_beta_)?")
            return path
        games = find_games(self.roots)
        if not games:
            typed = self._ask("Could not find World of Warcraft. Paste the client folder "
                              "(for example C:\\Games\\World of Warcraft\\_classic_beta_): ")
            return self.pick_game(typed.strip().strip('"'))
        return games[self.choose("Which game folder should MacroForge use?",
                                 [g.label for g in games])].path

    def pick_account(self, wow_dir: Path, account: str | None) -> str:
        """Choose the account folder (like ``<number>#1``); ``""`` means detect it later.

        Nothing in the install needs the account (only the watcher, which reads the saved
        variables), so a brand-new game with no account folder yet is fine.
        """
        if account:
            return account
        names = list_accounts(wow_dir)
        if not names:
            return ""
        return names[self.choose("Which account folder is yours?", names)]

    def run(self, wow_dir: str | None = None, account: str | None = None,
            skip_fetch: bool = False, requests: bool | None = None) -> int:
        """Run every step; returns the exit code."""
        self.out("MacroForge setup\n")
        game = self.pick_game(wow_dir)
        chosen = self.pick_account(game, account)
        write_config(self.root, {"wow_dir": str(game), **({"account": chosen} if chosen else {})})
        settings = Settings.load(self.root)
        check_writable(check_addons_dir(settings))
        shown = chosen or "not created yet (found automatically later)"
        self.out(f"Game: {game}\nAccount: {shown}\nSaved to {self.root / 'config.json'}\n")
        self.step_data(settings, skip_fetch)
        self.step_addon(settings)
        self.step_requests(settings, requests)
        self.out("\nAlmost done. If the game is running, fully quit it and start it again (it only "
                 "notices new addon folders at launch).\n"
                 "Then in game: /mf  (apply macros), /mf live  (no reload), /mf ask <request>.\n"
                 "Re-run setup any time; it will not touch your macros.json.")
        return 0

    def step_data(self, settings: Settings, skip: bool) -> None:
        """Use a local data pack if there is one, else download; refresh if the client is newer."""
        if skip:
            return
        pack = settings.root / "datapack"
        if not (settings.data / "icons.csv").exists():
            if (pack / "icons.csv").exists():
                settings.data.mkdir(exist_ok=True)
                for source in pack.iterdir():
                    if source.suffix in (".csv", "") and source.name != "NOTICE.md":
                        shutil.copy2(source, settings.data / source.name)
                self.out("Game data: copied the bundled tables.")
            else:
                self.out("Downloading game data (about 150 MB, a few minutes)...")
                self.fetch(settings)
                return
        have = _read_line(settings.data / "BUILD")
        try:
            installed = installed_build(settings)
        except ValidationError:
            return
        if have != installed:
            self.out(f"Your game is build {installed}; refreshing the spell tables...")
            try:
                self.fetch(settings, no_icons=True)
            except MacroForgeError as exc:
                self.out(f"  Could not refresh ({exc}). Using the bundled tables, which is fine "
                         "unless the newest spells are missing.")

    def step_addon(self, settings: Settings) -> None:
        """Create macros.json if missing, build the addon, make the slots and install."""
        if not settings.spec.exists():
            example = settings.root / "macros.example.json"
            if example.exists():
                atomic_write_text(settings.spec, example.read_text(encoding="utf-8"))
        build_addon(settings, load_spec(settings), load_replies(settings))
        create_slots(settings, SLOT_COUNT)
        dest, slots = install(settings)
        self.out(f"Installed the addon and {slots} slots in {dest}\n")

    def step_requests(self, settings: Settings, requests: bool | None) -> None:
        """Optionally set up the watcher that answers ``/mf ask`` (needs the Claude CLI)."""
        if requests is False:
            return
        try:
            exe = agent.find_claude(settings)
        except MacroForgeError:
            exe = ""
        if not exe:
            self.out("Optional: /mf ask (type requests in game) needs the free Claude Code CLI. "
                     "Install it, sign in, then run setup again:\n"
                     "  PowerShell:  irm https://claude.ai/install.ps1 | iex\n"
                     "  then:        python macroforge.py login\n")
            return
        if requests is None and not self.yes(
                "Set up /mf ask now? (a small program on this PC answers in-game requests)"):
            return
        if not self.assume_yes and self.yes("Sign in to Claude now? (skip if already signed in)",
                                            False):
            agent.login(settings)
        target = startup.install_startup(settings, settings.root / "macroforge.py", True)
        self.out(f"The watcher is running and will start with Windows ({target}).\n"
                 f"Log: {settings.data / 'watcher.log'}\n")

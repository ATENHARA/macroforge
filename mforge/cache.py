"""The raw-file route: merge macros into WoW's ``macros-cache.txt`` files (game must be closed)."""
from __future__ import annotations

import csv
import re
import subprocess
from pathlib import Path

from .errors import ValidationError
from .fsutil import atomic_write_bytes, atomic_write_text, is_link, system_exe
from .settings import Settings
from .validate import has_bad_chars, norm

BLOCK = re.compile(r'^VER (\d+) ([0-9A-Fa-f]{16}) "(.*)" "(.*)"$')
ACCOUNT_ID_BASE, CHAR_ID_BASE = 0, 0x0100000000000000  # id ranges observed in real cache files


def parse_cache(path: Path) -> list[dict]:
    """Parse a ``macros-cache.txt`` into ``{ver, id, name, icon, body: [lines]}`` blocks."""
    blocks: list[dict] = []
    current = None
    # split on "\n" only: str.splitlines() also breaks on U+2028, \x85 and other separators
    for line in Path(path).read_text(encoding="utf-8").replace("\r\n", "\n").split("\n"):
        match = BLOCK.match(line)
        if match:
            current = {"ver": match[1], "id": match[2], "name": match[3], "icon": match[4],
                       "body": []}
        elif line == "END" and current:
            blocks.append(current)
            current = None
        elif current is not None:
            current["body"].append(line)
    return blocks


def merge_cache(path: Path, entries: list[dict], id_base: int, limit: int) -> str:
    """Return the cache text with ``entries`` merged in; macros we don't manage are kept."""
    blocks = parse_cache(path) if Path(path).exists() else []
    used = {int(b["id"], 16) for b in blocks}
    by_name = {b["name"]: b for b in blocks}
    for macro in entries:
        if '"' in macro["name"] or has_bad_chars(macro["name"]):
            raise ValidationError(f"{macro['name']!r}: name cannot be written to macros-cache.txt")
        for line in macro["body"].split("\n"):
            if line == "END" or BLOCK.match(line) or has_bad_chars(line):
                raise ValidationError(
                    f"{macro['name']!r}: a body line would corrupt macros-cache.txt: {line[:30]!r}")
        block = by_name.get(macro["name"])
        if not block:
            try:
                new_id = next(i for i in range(id_base + 1, id_base + limit + 1) if i not in used)
            except StopIteration:
                raise ValidationError("no free macro slot left in the cache file") from None
            used.add(new_id)
            block = {"ver": "3", "id": f"{new_id:016X}", "name": macro["name"]}
            blocks.append(block)
        block["icon"], block["body"] = str(macro["icon"]), macro["body"].split("\n")
    return "".join(
        f'VER {b["ver"]} {b["id"]} "{b["name"]}" "{b["icon"]}"\n' + "\n".join(b["body"]) + "\nEND\n"
        for b in blocks)


def char_dir(settings: Settings, name: str) -> Path | None:
    """The ``WTF/Account/<acct>/<realm>/<char>`` folder (``Name Surname`` = ``Name-Surname``)."""
    account = settings.account_dir
    if not account.is_dir():
        raise ValidationError(f"{account} does not exist; check wow_dir/account in config.json")
    for realm in account.iterdir():
        if realm.is_dir() and realm.name != "SavedVariables" and not is_link(realm):
            for char in realm.iterdir():
                if char.is_dir() and not is_link(char) and norm(char.name) == norm(name):
                    return char
    return None


def game_running() -> bool:
    """True if a WoW client process (any ``wow*.exe``) may be running.

    The game rewrites these files on logout, so when the process list cannot be read this fails
    closed and reports True.
    """
    try:
        listing = subprocess.run([system_exe("tasklist.exe"), "/FO", "CSV", "/NH"],
                                 capture_output=True, text=True, check=False)
    except OSError:
        return True
    if listing.returncode != 0:
        return True
    names = (row[0].lower() for row in csv.reader(listing.stdout.splitlines()) if row)
    return any(name.startswith("wow") and name.endswith(".exe") for name in names)


def stage_caches(settings: Settings, spec: dict, write: bool) -> list[str]:
    """Write merged caches to ``build/cache/``; with ``write`` also replace the game files.

    Each replaced file is first copied to ``macros-cache.txt.bak``. Returns the lines to print.
    """
    if not settings.account and len(settings.account_dirs) > 1:
        raise ValidationError('several account folders found: set "account" in config.json to '
                              "the one whose macros should be written")
    jobs = [(settings.account_dir / "macros-cache.txt", spec["account"], ACCOUNT_ID_BASE,
             settings.max_account_macros, "account")]
    messages: list[str] = []
    for char, entries in spec["chars"].items():
        folder = char_dir(settings, char)
        if not folder:
            messages.append(f"skip {char!r}: no character folder under {settings.account_dir} "
                            "(log in once with that character first)")
            continue
        jobs.append((folder / "macros-cache.txt", entries, CHAR_ID_BASE,
                     settings.max_char_macros, char))
    if write and game_running():
        raise ValidationError("WoW is running - close it first (it rewrites these files on logout)")
    for target, entries, base, limit, label in jobs:
        if not entries:
            continue
        text = merge_cache(target, entries, base, limit)
        staged = settings.build.parent / "cache" / norm(label) / "macros-cache.txt"
        staged.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(staged, text)
        if write:
            backup = target.with_name("macros-cache.txt.bak")
            if is_link(backup) or is_link(target):
                raise ValidationError(f"{target.name} or its backup is a link; refusing to write")
            if target.exists():
                atomic_write_bytes(backup, target.read_bytes())
            atomic_write_text(target, text)
            messages.append(f"wrote {target} (backup: macros-cache.txt.bak)")
        else:
            messages.append(f"staged {staged}  ->  would replace {target}")
    return messages

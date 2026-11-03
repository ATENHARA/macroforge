"""``update``: get the newest MacroForge. Only ever runs when the player asks for it.

A git clone is updated with ``git pull --ff-only``. Any other copy downloads the repository as a zip
from GitHub, checks every entry (no paths outside the folder, no links, size limits), and swaps the
program files in. The player's own files (``config.json``, ``macros.json``, ``data/``, ``build/``,
the data pack) are never touched, and the replaced program files are kept in ``data/backups/``.
"""
from __future__ import annotations

import re
import shutil
import stat
import subprocess
import sys
import time
import zipfile
from pathlib import Path, PurePosixPath
from typing import Callable

from .errors import MacroForgeError, ValidationError
from .fetch import open_url
from .settings import Settings

KEEP = frozenset({"config.json", "macros.json", "data", "build", "datapack", ".git", "__pycache__"})
MAX_ZIP_BYTES = 30_000_000
MAX_ENTRIES = 3000
MAX_UNPACKED_BYTES = 80_000_000
MAX_VERSION_BYTES = 64_000
_VERSION = re.compile(r'^__version__\s*=\s*"(\d+(?:\.\d+){1,3})"', re.M)


def parse_version(text: str) -> tuple[int, ...]:
    """``"1.5.0"`` -> ``(1, 5, 0)``; anything else is a ``ValidationError``."""
    if not re.fullmatch(r"\d+(?:\.\d+){1,3}", text):
        raise ValidationError(f"not a version number: {text!r}")
    return tuple(int(part) for part in text.split("."))


def version_in(text: str) -> str:
    """The ``__version__`` string in the text of ``mforge/__init__.py``."""
    match = _VERSION.search(text)
    if not match:
        raise ValidationError("no version number found")
    return match.group(1)


def local_version(root: Path) -> str:
    """The version of the MacroForge in ``root``."""
    try:
        return version_in((root / "mforge" / "__init__.py").read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValidationError(f"cannot read the local version: {exc}") from exc


def remote_version(settings: Settings) -> str:
    """The version of the newest MacroForge on GitHub (one small download)."""
    with open_url(settings, settings.update_version_url, 30) as resp:
        data = resp.read(MAX_VERSION_BYTES + 1)
    if len(data) > MAX_VERSION_BYTES:
        raise ValidationError("the version file is unexpectedly large")
    return version_in(data.decode("utf-8", "replace"))


def is_git_clone(root: Path) -> bool:
    """True if this copy of MacroForge is a git clone (then git does the update)."""
    return (root / ".git").is_dir()


# -- git ---------------------------------------------------------------------------------------

def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    exe = shutil.which("git")
    if not exe:
        raise MacroForgeError("this copy is a git clone but git was not found; install git, or "
                              "download MacroForge again and copy your config.json, macros.json "
                              "and data folder into it")
    return subprocess.run([exe, "-C", str(root), *args], capture_output=True, text=True,
                          timeout=300, check=False)


def update_git(root: Path) -> str:
    """``git pull --ff-only``; refuses when MacroForge's own files were changed by hand."""
    dirty = _git(root, "status", "--porcelain", "--untracked-files=no")
    if dirty.returncode != 0:
        raise MacroForgeError(f"git status failed: {dirty.stderr.strip()[:200]}")
    if dirty.stdout.strip():
        raise MacroForgeError("you have changed some of MacroForge's own files, so git will not "
                              "update them; undo those changes (git status shows them) or "
                              "commit them")
    pulled = _git(root, "pull", "--ff-only")
    if pulled.returncode != 0:
        raise MacroForgeError(f"git pull failed: {(pulled.stderr or pulled.stdout).strip()[:300]}")
    return pulled.stdout.strip()


# -- zip ---------------------------------------------------------------------------------------

def download_zip(settings: Settings, dest: Path) -> None:
    """Download the repository zip to ``dest`` (size-limited, https and allowed hosts only)."""
    part = dest.with_name(dest.name + ".part")
    total = 0
    try:
        with open_url(settings, settings.update_zip_url, 120) as resp, open(part, "wb") as out:
            while chunk := resp.read(65536):
                total += len(chunk)
                if total > MAX_ZIP_BYTES:
                    raise ValidationError("the download is larger than expected; stopped")
                out.write(chunk)
        part.replace(dest)
    finally:
        part.unlink(missing_ok=True)


def _safe_name(name: str) -> PurePosixPath:
    path = PurePosixPath(name)
    if name.startswith("/") or "\\" in name or ":" in name or ".." in path.parts or not path.parts:
        raise ValidationError(f"unsafe path in the download: {name!r}")
    return path


def unpack(archive: Path, target: Path) -> Path:
    """Unpack a GitHub repository zip into ``target`` after checking every entry.

    Returns the folder holding the files (the zip's single top folder).
    """
    try:
        with zipfile.ZipFile(archive) as zf:
            entries = zf.infolist()
            if len(entries) > MAX_ENTRIES:
                raise ValidationError("the download has too many files")
            if sum(e.file_size for e in entries) > MAX_UNPACKED_BYTES:
                raise ValidationError("the download unpacks to too much data")
            tops = {_safe_name(e.filename).parts[0] for e in entries}
            if len(tops) != 1:
                raise ValidationError("the download is not a single MacroForge folder")
            for entry in entries:
                path = _safe_name(entry.filename)
                if stat.S_ISLNK(entry.external_attr >> 16):
                    raise ValidationError(f"the download contains a link: {entry.filename!r}")
                out = target.joinpath(*path.parts)
                if entry.is_dir():
                    out.mkdir(parents=True, exist_ok=True)
                    continue
                out.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(entry) as src, open(out, "wb") as dst:
                    shutil.copyfileobj(src, dst)
    except zipfile.BadZipFile as exc:
        raise ValidationError(f"the download is not a valid zip: {exc}") from exc
    folder = target / next(iter(tops))
    for needed in ("macroforge.py", "mforge/__init__.py", "addon/MacroForge/MacroForge.toc"):
        if not (folder / needed).is_file():
            raise ValidationError(f"the download is not MacroForge (no {needed})")
    return folder


def swap_in(new: Path, root: Path, backup: Path) -> list[str]:
    """Move the program files of ``new`` into ``root``; the old ones go to ``backup``.

    The player's files (``KEEP``) are skipped. If anything fails, everything moved so far is put
    back. Returns the names that were replaced or added.
    """
    moved: list[tuple[Path, Path]] = []  # (from, to), to undo in reverse
    names = sorted(p.name for p in new.iterdir() if p.name not in KEEP)
    try:
        backup.mkdir(parents=True, exist_ok=True)
        for name in names:
            current = root / name
            if current.exists():
                current.replace(backup / name)
                moved.append((backup / name, current))
            (new / name).replace(current)
            moved.append((current, new / name))
    except OSError as exc:
        for source, destination in reversed(moved):
            try:
                source.replace(destination)
            except OSError:
                pass
        raise MacroForgeError(f"could not replace the program files ({exc}); nothing was "
                              "changed") from exc
    return names


def update_zip(settings: Settings, say: Callable[[str], None]) -> Path:
    """Download, check and swap in the newest MacroForge. Returns the backup folder."""
    work = settings.data / "update"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    try:
        archive = work / "macroforge.zip"
        say("Downloading MacroForge...")
        download_zip(settings, archive)
        new = unpack(archive, work / "unpacked")
        stamp = time.strftime("%Y%m%d-%H%M%S")
        backup = settings.data / "backups" / f"program-{local_version(settings.root)}-{stamp}"
        swap_in(new, settings.root, backup)
        return backup
    finally:
        shutil.rmtree(work, ignore_errors=True)


# -- the command -------------------------------------------------------------------------------

def finish(settings: Settings, say: Callable[[str], None]) -> None:
    """After new files are in place: rebuild and reinstall with the new code (a fresh process),
    then show what else is left (``status``: for example new game data after a game patch)."""
    for step in ("build", "install"):
        say(f"Running {step}...")
        done = subprocess.run([sys.executable, "macroforge.py", step], cwd=settings.root,
                              check=False)
        if done.returncode != 0:
            raise MacroForgeError(f"`{step}` failed after the update; run it again by hand to "
                                  "see why")
    subprocess.run([sys.executable, "macroforge.py", "status"], cwd=settings.root, check=False)


def run_update(settings: Settings, check_only: bool, say: Callable[[str], None] = print,
               after: Callable[[Settings, Callable[[str], None]], None] = finish) -> int:
    """Check for a newer MacroForge and (unless ``check_only``) install it. Returns an exit code."""
    mine = local_version(settings.root)
    newest = remote_version(settings)
    if parse_version(newest) <= parse_version(mine):
        say(f"MacroForge {mine} is the newest version.")
        return 0
    say(f"MacroForge {newest} is available (you have {mine}).")
    if check_only:
        say("Run `python macroforge.py update` (or double-click update.cmd) to install it.")
        return 0
    if is_git_clone(settings.root):
        say(update_git(settings.root) or "Updated with git.")
    else:
        backup = update_zip(settings, say)
        say(f"Your previous version is kept in {backup}.")
    after(settings, say)
    say(f"Updated to MacroForge {newest}. In game: /reload (restart the game if MacroForge says it "
        "is out of date). If you use in-game requests, restart the watcher: stop pythonw.exe in "
        "Task Manager, then run `python macroforge.py startup --now`.")
    return 0

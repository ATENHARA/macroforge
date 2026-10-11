"""Run the watcher hidden at Windows login by placing a small launcher in the Startup folder."""
from __future__ import annotations

import codecs
import os
import subprocess
import sys
from pathlib import Path

from .errors import ValidationError
from .fsutil import atomic_write_bytes, system_exe
from .settings import Settings

LAUNCHER_NAME = "MacroForge Watcher.vbs"
MARKER = "' MacroForge watcher launcher (created by `macroforge startup`)"


def startup_file() -> Path:
    """Where the launcher lives (resolved when called, so tests can redirect ``APPDATA``)."""
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise ValidationError("APPDATA is not set; `startup` only works on Windows")
    folder = Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    return folder / LAUNCHER_NAME


def vbs_quote(text: str) -> str:
    """Return ``text`` as the inside of a VBScript string literal (quotes doubled)."""
    if any(ch in text for ch in "\r\n\0"):
        raise ValidationError("path contains a line break")
    return text.replace('"', '""')


def launcher_text(root: Path, pythonw: Path, script: Path) -> str:
    """VBScript that starts the watcher with no console window (window style 0, do not wait)."""
    for path in (root, pythonw, script):
        if '"' in str(path) or "%" in str(path):
            raise ValidationError(f"path contains a quote or a % sign: {path}")
    command = f'"{pythonw}" "{script}" watch'
    return (f"{MARKER}\n"
            'Set shell = CreateObject("WScript.Shell")\n'
            f'shell.CurrentDirectory = "{vbs_quote(str(root))}"\n'
            f'shell.Run "{vbs_quote(command)}", 0, False\n')


DETACHED = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
BREAKAWAY = 0x01000000  # CREATE_BREAKAWAY_FROM_JOB


def launch_detached(command: list[str]) -> None:
    """Start a program that keeps running after this one (and the app that started it) exits.

    Windows ends every process in the parent's job when the job closes, which killed a watcher
    started from inside the Claude app. Breaking away from the job avoids that; if the job forbids
    it, fall back to a plain detached start.
    """
    try:
        subprocess.Popen(command, creationflags=DETACHED | BREAKAWAY, close_fds=True)
    except OSError:
        subprocess.Popen(command, creationflags=DETACHED, close_fds=True)


def read_launcher(path: Path) -> str:
    """The text of a launcher file (UTF-16 with a byte-order mark, or UTF-8 from older versions)."""
    data = Path(path).read_bytes()
    utf16 = data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE))
    return data.decode("utf-16" if utf16 else "utf-8")


def install_startup(settings: Settings, script: Path, start_now: bool) -> Path:
    """Write the launcher (and start it now if asked). Returns its path."""
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    if not pythonw.exists():
        raise ValidationError("pythonw.exe not found next to python.exe")
    target = startup_file()
    target.parent.mkdir(parents=True, exist_ok=True)
    # Windows Script Host reads a .vbs as ANSI unless it has a UTF-16 mark: a path with accented
    # characters would be garbled in a UTF-8 file.
    text = launcher_text(settings.root, pythonw, script.resolve())
    atomic_write_bytes(target, codecs.BOM_UTF16_LE + text.encode("utf-16-le"))
    if start_now:
        launch_detached([system_exe("wscript.exe"), str(target)])
    return target


def remove_startup() -> Path:
    """Delete only the launcher this tool created. Returns its path."""
    target = startup_file()
    if target.exists():
        try:
            first = read_launcher(target).splitlines()[0]
        except (OSError, UnicodeDecodeError, IndexError):
            first = ""
        if first != MARKER:
            raise ValidationError(f"{target} was not created by MacroForge; leaving it alone")
        target.unlink()
    return target

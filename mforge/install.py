"""Build the addon data file and install it, plus the load-on-demand slot addons, into the game."""
from __future__ import annotations

import re
import shutil
import time
from pathlib import Path

from .assets import read_text
from .errors import ValidationError
from .fsutil import atomic_write_bytes, atomic_write_text, is_link
from .lua import render_data
from .settings import Settings

SLOT_PREFIX = "MacroForge_S"
MAX_SLOTS = 200  # the addon scans slot names up to this number
_SLOT_NAME = re.compile(r"^MacroForge_S\d{3}$")


def check_addons_dir(settings: Settings) -> Path:
    """The AddOns folder, after refusing links (junctions) on it or its parent."""
    addons = settings.addons
    if not addons.is_dir():
        raise ValidationError(f"{addons} does not exist; check wow_dir in config.json")
    for folder in (addons, addons.parent):
        if is_link(folder):
            raise ValidationError(f"{folder} is a link; refusing to write through it")
    return addons


def build_addon(settings: Settings, spec: dict, replies: list[dict]) -> int:
    """Copy the addon sources to ``build/MacroForge`` and generate ``MacroForgeData.lua``.

    Returns the ``rev`` stamp written into the data (the addon compares it to detect new data).
    """
    out = settings.build
    out.mkdir(parents=True, exist_ok=True)
    for source in settings.addon_src.iterdir():
        if source.is_file():
            shutil.copy2(source, out / source.name)
    rev = int(time.time())
    atomic_write_text(out / "MacroForgeData.lua", render_data(spec, rev, replies))
    return rev


def slot_dirs(settings: Settings) -> list[Path]:
    """Existing ``MacroForge_S###`` folders (links are ignored)."""
    return sorted(p for p in settings.addons.glob(SLOT_PREFIX + "*")
                  if _SLOT_NAME.match(p.name) and p.is_dir() and not is_link(p))


def write_slots(settings: Settings) -> int:
    """Write the latest data into every slot; returns how many were updated.

    The game loads whichever slot is next, so all slots carry the same content. The global is
    renamed so slot data never clashes with the login-time data.
    """
    source = (settings.build / "MacroForgeData.lua").read_text(encoding="utf-8")
    text = source.replace("MacroForgeData = {", "MacroForgeSlotData = {", 1)
    done = 0
    for folder in slot_dirs(settings):
        try:
            atomic_write_text(folder / "Slot.lua", text)
            done += 1
        except OSError as exc:
            print(f"  skip {folder.name}: {exc}")
    return done


def create_slots(settings: Settings, count: int) -> Path:
    """Create ``count`` slot addons. The game only discovers addon folders at launch."""
    if not 1 <= count <= MAX_SLOTS:
        raise ValidationError(f"--count must be between 1 and {MAX_SLOTS}")
    check_addons_dir(settings)
    template = read_text("slot.toc")
    for number in range(1, count + 1):
        folder = settings.addons / f"{SLOT_PREFIX}{number:03d}"
        if is_link(folder):
            raise ValidationError(f"{folder} is a link; refusing to write through it")
        folder.mkdir(exist_ok=True)
        atomic_write_text(folder / f"{folder.name}.toc", template.format(n=number))
        if not (folder / "Slot.lua").exists():
            atomic_write_text(folder / "Slot.lua", "MacroForgeSlotData = nil\n")
    return settings.addons


def install(settings: Settings) -> tuple[Path, int]:
    """Copy the built addon into the game folder and refresh the slots.

    Returns the addon folder and the number of slots updated.
    """
    if not (settings.build / "MacroForgeData.lua").exists():
        raise ValidationError("run `build` first")
    check_addons_dir(settings)
    dest = settings.addons / "MacroForge"
    if is_link(dest):
        raise ValidationError(f"{dest} is a link; refusing to write through it")
    dest.mkdir(exist_ok=True)
    current = {source.name for source in settings.build.iterdir() if source.is_file()}
    for source in settings.build.iterdir():
        if source.is_file():
            atomic_write_bytes(dest / source.name, source.read_bytes())
    for old in dest.iterdir():  # files of earlier versions that the addon no longer has
        if old.is_file() and old.suffix in (".lua", ".toc", ".xml") and old.name not in current:
            old.unlink()
    return dest, write_slots(settings)

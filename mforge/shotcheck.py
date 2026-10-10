"""Diagnostics for the screenshot channel: decode the newest picture (see ``stripcodec.py``)."""
from __future__ import annotations

from pathlib import Path

from .errors import ValidationError
from .settings import Settings
from .stripcodec import find_strip, read_tga


def screenshots_dir(settings: Settings) -> Path:
    """The folder the game saves screenshots to."""
    return settings.wow_dir / "Screenshots"


def newest_screenshot(settings: Settings) -> Path:
    """The most recently written screenshot file."""
    folder = screenshots_dir(settings)
    files = [p for p in folder.glob("*.*") if p.suffix.lower() == ".tga"]
    if not files:
        raise ValidationError(f"no TGA screenshots in {folder}")
    return max(files, key=lambda p: p.stat().st_mtime)


def shot_check(settings: Settings) -> str:
    """Decode the data strip in the newest screenshot; returns a one-paragraph report."""
    path = newest_screenshot(settings)
    image = read_tga(path)
    message_id, payload, details = find_strip(image)
    return (f"{path.name}: {image.width}x{image.height}, strip found "
            f"(cell {details['cell_px']} px at {details['origin']}), id {message_id}, "
            f"payload {payload.decode('utf-8', 'replace')!r}")

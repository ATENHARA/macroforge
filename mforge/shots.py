"""Requests that arrive as screenshots: ``/mf ask`` draws the request as a data strip and the game
saves a TGA picture of it immediately, so the watcher can read it without any reload.

Payload (UTF-8 text, fields separated by tabs): ``MFREQ``, request id, level, class, character,
request text and, optionally, the mode (``macro`` or ``script``). Every field still goes through
``validate.parse_request`` like any other request.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

from .errors import ValidationError
from .stripcodec import find_strip, read_tga

log = logging.getLogger("mforge.shots")

TAG = "MFREQ"
RECENT_SECONDS = 600  # pictures older than this at watcher start are not requests
SETTLE_SECONDS = 1.0  # the game may still be writing a file this young
STUCK_RETRIES = 30  # about a minute of 2-second polls
GIVE_UP_SECONDS = 60  # a picture that still does not decode after this long is skipped


@dataclass
class ShotScanner:
    """Remembers which screenshot files were already looked at."""

    folder: Path
    started: float = field(default_factory=time.time)
    seen: dict[str, float] = field(default_factory=dict)
    stuck: dict[Path, int] = field(default_factory=dict)  # undeletable files -> failed attempts

    def fresh_files(self, now: float) -> list[Path]:
        """New TGA files, oldest first, that are old enough to be completely written."""
        found = []
        try:
            candidates = list(self.folder.glob("*.tga"))
        except OSError:
            return []
        for path in candidates:
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            if self.seen.get(path.name) == mtime:
                continue
            if mtime < self.started - RECENT_SECONDS:
                self.seen[path.name] = mtime  # an old picture from before we started
                continue
            if now - mtime < SETTLE_SECONDS:
                continue
            found.append((mtime, path))
        return [path for _, path in sorted(found)]


def request_from_payload(payload: bytes) -> dict:
    """The request table (as ``parse_request`` expects it) described by a strip payload."""
    try:
        parts = payload.decode("utf-8").split("\t")
    except UnicodeDecodeError:
        raise ValidationError("strip text is not UTF-8") from None
    if len(parts) not in (6, 7) or parts[0] != TAG:  # the 7th field (macro/script) is optional
        raise ValidationError("strip is not a MacroForge request")
    try:
        req_id, level = int(parts[1]), int(parts[2])
    except ValueError:
        raise ValidationError("strip has a bad id or level") from None
    return {"id": req_id, "level": level, "class": parts[3], "char": parts[4], "text": parts[5],
            "mode": parts[6] if len(parts) == 7 else "macro"}


def read_request(path: Path) -> dict:
    """Decode one screenshot into a request table, or raise ``ValidationError``."""
    _message, payload, _details = find_strip(read_tga(path))
    return request_from_payload(payload)


def cleanup(path: Path) -> list[Path]:
    """Delete a request picture and the JPEG the game wrote just after it (within a few seconds).

    Returns the files that could not be deleted (locked by the game, read-only, ...); the caller
    retries them later. Never raises.
    """
    failed: list[Path] = []
    try:
        stamp = path.stat().st_mtime
        siblings = [p for p in path.parent.glob("*.jpg") if 0 <= p.stat().st_mtime - stamp <= 5]
    except OSError:
        siblings = []
    for target in siblings + [path]:
        try:
            target.unlink(missing_ok=True)
        except OSError:
            failed.append(target)
    return failed


def retry_stuck(scanner: "ShotScanner") -> None:
    """Try again to delete pictures that were locked earlier; give up after a while."""
    for target in list(scanner.stuck):
        try:
            target.unlink(missing_ok=True)
        except OSError:
            scanner.stuck[target] += 1
            if scanner.stuck[target] >= STUCK_RETRIES:
                log.warning("could not delete %s; please delete it by hand", target)
                del scanner.stuck[target]
            continue
        log.info("deleted %s on retry", target.name)
        del scanner.stuck[target]

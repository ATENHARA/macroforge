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
STALE_MAX_AGE = 7 * 24 * 3600  # older unanswered request pictures are left alone
STALE_LOOK_LIMIT = 10  # old pictures examined per poll (decoding one takes about a second)


@dataclass
class ShotScanner:
    """Remembers which screenshot files were already looked at."""

    folder: Path
    started: float = field(default_factory=time.time)
    seen: dict[str, float] = field(default_factory=dict)
    stuck: dict[Path, int] = field(default_factory=dict)  # undeletable files -> failed attempts
    stale: list[Path] = field(default_factory=list)  # old pictures that may be unanswered requests

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
                if mtime > self.started - STALE_MAX_AGE:
                    self.stale.append(path)  # maybe a request sent while the watcher was off
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
    if len(parts) not in (6, 7, 8) or parts[0] != TAG:  # the mode and the scope are optional
        raise ValidationError("strip is not a MacroForge request")
    try:
        req_id, level = int(parts[1]), int(parts[2])
    except ValueError:
        raise ValidationError("strip has a bad id or level") from None
    return {"id": req_id, "level": level, "class": parts[3], "char": parts[4], "text": parts[5],
            "mode": parts[6] if len(parts) >= 7 else "macro",
            "scope": parts[7] if len(parts) == 8 else "char"}


def read_request(path: Path) -> dict:
    """Decode one screenshot into a request table, or raise ``ValidationError``."""
    _message, payload, _details = find_strip(read_tga(path))
    return request_from_payload(payload)


def cleanup(path: Path) -> list[Path]:
    """Delete a request picture (and nothing else in the folder).

    The addon takes exactly one picture per request, so there is no second file to look for; the
    player's own screenshots, JPEG or not, are never touched. Returns the files that could not be
    deleted (locked by the game, read-only, ...); the caller retries them later. Never raises.
    """
    try:
        path.unlink(missing_ok=True)
    except OSError:
        return [path]
    return []


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

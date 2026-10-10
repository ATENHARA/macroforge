"""Validation of everything that crosses a trust boundary: requests, macros and replies.

Nothing here touches the filesystem, so every rule can be tested with plain strings.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from .errors import ValidationError

MAX_NAME = 16
MAX_BODY = 255
MAX_REPLY = 600

# First name plus optional surname parts: letters, spaces, apostrophes and hyphens only.
_CHAR_RE = re.compile(r"^[^\W\d_](?:[^\W\d_]|[ '\-]){1,47}$")
# Control, format (zero-width, bidi) and line/paragraph separator characters. This covers \x85 (NEL)
# and U+2028/U+2029, which some parsers treat as line breaks even though the game's macro text only
# uses "\n".
_BAD_CATEGORIES = frozenset({"Cc", "Cf", "Zl", "Zp"})
# Blizzard UI escape sequences (colors, hyperlinks, textures, line breaks) start with a pipe.
_ESCAPE_RE = re.compile(r"\|[A-Za-z0-9|]")
_COMMAND_RE = re.compile(r"^\s*/\s*([A-Za-z0-9_]+)")


def has_bad_chars(text: str, allow_newline: bool = False) -> bool:
    """True if ``text`` has a control, format or line-separator character (maybe except ``\\n``)."""
    return any(unicodedata.category(ch) in _BAD_CATEGORIES and not (allow_newline and ch == "\n")
               for ch in text)


def strip_bad_chars(text: str, replacement: str = " ") -> str:
    """Replace every control, format or line-separator character (newline included)."""
    return "".join(replacement if unicodedata.category(ch) in _BAD_CATEGORIES else ch
                   for ch in text)


def norm(text: object) -> str:
    """Lowercase and drop everything except letters and digits.

    ``Firstname Surname``, ``Firstname-Surname`` and ``firstnamesurname`` compare equal. The addon
    uses the same rule.
    """
    return re.sub(r"[^a-z0-9]", "", str(text).lower())


@dataclass(frozen=True)
class Request:
    """A macro request written by the in-game ``/mf ask`` command, after validation."""

    id: int
    text: str
    char: str
    cls: str
    level: int


def parse_request(raw: object, allowed_classes: frozenset[str] | set[str],
                  max_text: int = 500) -> Request:
    """Validate one entry read from the saved-variables file.

    The file is written by the game but lives in a user-writable folder, so every field is checked
    before it is used in a file name, a prompt or a path.
    """
    if not isinstance(raw, dict):
        raise ValidationError("request is not a table")
    req_id = raw.get("id")
    if not isinstance(req_id, int) or isinstance(req_id, bool) or req_id <= 0:
        raise ValidationError("request id must be a positive integer")
    text = raw.get("text")
    if not isinstance(text, str) or not text.strip():
        raise ValidationError("request text is missing")
    char = raw.get("char")
    if not isinstance(char, str) or not _CHAR_RE.match(char):
        raise ValidationError("request character name is not valid")
    cls = raw.get("class")
    cls = cls.lower() if isinstance(cls, str) else ""
    if cls not in allowed_classes:
        raise ValidationError("request class is not a known class")
    level = raw.get("level")
    if not isinstance(level, int) or isinstance(level, bool) or not 1 <= level <= 100:
        raise ValidationError("request level must be 1..100")
    text = strip_bad_chars(text.strip())[:max_text]
    return Request(id=req_id, text=text, char=char, cls=cls, level=level)


def clean_macro(name: object, body: object) -> tuple[str, str]:
    """Check a macro's name and body and return them normalized.

    Rejects the wrong types, over-long text, control characters and ``|`` escape sequences.
    """
    if not isinstance(name, str) or not name:
        raise ValidationError("macro name must be a non-empty string")
    if not isinstance(body, str):
        raise ValidationError(f"{name!r}: body must be a string")
    body = body.replace("\r\n", "\n").strip("\n")
    size = len(body.encode("utf-8"))  # the game's limit counts bytes, not characters
    if size > MAX_BODY:
        raise ValidationError(f"{name!r}: body is {size} bytes (max {MAX_BODY})")
    if len(name) > MAX_NAME:
        raise ValidationError(f"{name!r}: name longer than {MAX_NAME} chars")
    if has_bad_chars(name) or '"' in name or "|" in name:
        raise ValidationError(f"{name!r}: name contains a control character, quote or '|'")
    if has_bad_chars(body, allow_newline=True):
        raise ValidationError(f"{name!r}: body contains a control or line-separator character")
    if _ESCAPE_RE.search(body):
        raise ValidationError(f"{name!r}: body contains a '|' escape sequence")
    return name, body


def command_of(line: str) -> str | None:
    """The slash command that starts ``line`` (lowercase, without the slash), if any."""
    match = _COMMAND_RE.match(line)
    return match.group(1).lower() if match else None


def check_blocked(name: str, body: str, blocked: frozenset[str] | set[str]) -> None:
    """Raise if any line of ``body`` starts with a blocked slash command."""
    for line in body.split("\n"):
        cmd = command_of(line)
        if cmd in blocked:
            raise ValidationError(f"macro {name!r} uses blocked command /{cmd}")


def sanitize_reply(text: str, limit: int = MAX_REPLY) -> str:
    """Make a reply safe to print in the game's chat window.

    Keeps printable ASCII only and drops ``|`` so the chat cannot render colors, links or textures.
    """
    ascii_text = strip_bad_chars(text.encode("ascii", "replace").decode()).replace("|", "")
    return re.sub(r"\s+", " ", ascii_text).strip()[:limit]

"""Read the Lua tables WoW writes to ``WTF/Account/<account>/SavedVariables/*.lua``.

The file sits in a user-writable folder, so it is treated as untrusted: the parser enforces size and
depth limits and only ever raises :class:`ValidationError`.
"""
from __future__ import annotations

import re
from pathlib import Path

from .errors import ValidationError

MAX_SIZE = 5_000_000
MAX_DEPTH = 50

_NUMBER = re.compile(r"-?(?:0[xX][0-9a-fA-F]+|\d+\.?\d*(?:[eE][-+]?\d+)?|\.\d+(?:[eE][-+]?\d+)?)")
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_FIELD = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)[ \t\r\n]*=(?!=)")
_LONG_OPEN = re.compile(r"\[(=*)\[")
_SIMPLE_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "a": "\a", "b": "\b", "f": "\f", "v": "\v"}


class LuaParser:
    """Recursive-descent parser for the data subset of Lua used by saved variables."""

    def __init__(self, text: str) -> None:
        if len(text) > MAX_SIZE:
            raise ValidationError("saved-variables file is too large")
        self.text = text
        self.pos = 0
        self.depth = 0

    # -- helpers ---------------------------------------------------------------------------
    def _fail(self, message: str) -> ValidationError:
        return ValidationError(f"saved variables: {message} at offset {self.pos}")

    def _peek(self) -> str:
        return self.text[self.pos] if self.pos < len(self.text) else ""

    def _long_open(self) -> int | None:
        """If a long bracket (``[[`` or ``[=[``) starts here, consume it and return its level."""
        match = _LONG_OPEN.match(self.text, self.pos)
        if not match:
            return None
        self.pos = match.end()
        return len(match.group(1))

    def _long_body(self, level: int) -> str:
        close = "]" + "=" * level + "]"
        end = self.text.find(close, self.pos)
        if end < 0:
            raise self._fail("unterminated long bracket")
        body = self.text[self.pos:end]
        self.pos = end + len(close)
        return body[1:] if body.startswith("\n") else body

    def skip_ws(self) -> None:
        """Skip whitespace and ``--`` comments (line and long form)."""
        while self.pos < len(self.text):
            ch = self.text[self.pos]
            if ch in " \t\r\n":
                self.pos += 1
            elif self.text.startswith("--", self.pos):
                self.pos += 2
                level = self._long_open()
                if level is not None:
                    self._long_body(level)
                else:
                    end = self.text.find("\n", self.pos)
                    self.pos = len(self.text) if end < 0 else end + 1
            else:
                break

    def _expect(self, ch: str) -> None:
        self.skip_ws()
        if self._peek() != ch:
            raise self._fail(f"expected {ch!r}")
        self.pos += 1

    # -- values ----------------------------------------------------------------------------
    def value(self):
        """Parse one value: table, string, number, boolean or nil."""
        self.skip_ws()
        ch = self._peek()
        if ch == "":
            raise self._fail("unexpected end of file")
        if ch == "{":
            return self._table()
        if ch in "\"'":
            return self._string(ch)
        if ch == "[":
            level = self._long_open()
            if level is None:
                raise self._fail("unexpected '['")
            return self._long_body(level)
        match = _NUMBER.match(self.text, self.pos)
        if match:
            self.pos = match.end()
            return self._number(match.group(0))
        for word, result in (("true", True), ("false", False), ("nil", None)):
            if self.text.startswith(word, self.pos):
                after = self.text[self.pos + len(word):self.pos + len(word) + 1]
                if not (after.isalnum() or after == "_"):
                    self.pos += len(word)
                    return result
        raise self._fail("unsupported syntax")

    def _number(self, token: str):
        """Convert a numeric token; absurdly long or malformed numbers are a parse error."""
        if len(token) > 40:
            raise self._fail("number is too long")
        try:
            if token.lower().lstrip("-").startswith("0x"):
                return int(token, 16)
            return float(token) if any(c in token for c in ".eE") else int(token)
        except ValueError:
            raise self._fail("malformed number") from None

    def _string(self, quote: str) -> str:
        self.pos += 1
        out: list[str] = []
        while True:
            if self.pos >= len(self.text):
                raise self._fail("unterminated string")
            ch = self.text[self.pos]
            if ch == quote:
                self.pos += 1
                return "".join(out)
            if ch == "\n":
                raise self._fail("newline in string")
            if ch != "\\":
                out.append(ch)
                self.pos += 1
                continue
            self.pos += 1
            esc = self._peek()
            if esc == "":
                raise self._fail("unterminated string")
            if esc in _SIMPLE_ESCAPES:
                out.append(_SIMPLE_ESCAPES[esc])
                self.pos += 1
            elif esc.isdigit():
                digits = re.match(r"\d{1,3}", self.text[self.pos:]).group(0)
                code = int(digits)
                if code > 255:
                    raise self._fail("bad decimal escape")
                out.append(chr(code))
                self.pos += len(digits)
            elif esc == "x":
                hex_digits = self.text[self.pos + 1:self.pos + 3]
                if not re.fullmatch(r"[0-9a-fA-F]{2}", hex_digits):
                    raise self._fail("bad hex escape")
                out.append(chr(int(hex_digits, 16)))
                self.pos += 3
            elif esc == "z":
                self.pos += 1
                while self._peek() and self._peek() in " \t\r\n":
                    self.pos += 1
            else:  # \\ \" \' and a backslash-newline
                out.append(esc)
                self.pos += 1

    def _table(self):
        self.depth += 1
        if self.depth > MAX_DEPTH:
            raise self._fail("tables nested too deeply")
        self.pos += 1
        items: list = []
        keyed: dict = {}
        while True:
            self.skip_ws()
            ch = self._peek()
            if ch == "":
                raise self._fail("unterminated table")
            if ch == "}":
                self.pos += 1
                break
            if ch == "[" and not _LONG_OPEN.match(self.text, self.pos):
                self.pos += 1
                key = self.value()
                self._expect("]")
                self._expect("=")
                if key is None or isinstance(key, (dict, list)):
                    raise self._fail("unsupported table key")
                keyed[key] = self.value()
            else:
                ident = _FIELD.match(self.text, self.pos)  # "name =", not "name =="
                if ident:
                    self.pos = ident.end(1)
                    self._expect("=")
                    keyed[ident.group(1)] = self.value()
                else:
                    items.append(self.value())
            self.skip_ws()
            if self._peek() in (",", ";"):
                self.pos += 1
            elif self._peek() != "}":
                raise self._fail("expected ',' or '}'")
        self.depth -= 1
        if keyed and items:
            return {**{i + 1: v for i, v in enumerate(items)}, **keyed}
        return keyed if keyed else items

    def statements(self) -> dict:
        """Parse ``Name = value`` assignments until the end of the file."""
        out: dict = {}
        while True:
            self.skip_ws()
            if self.pos >= len(self.text):
                return out
            ident = _IDENT.match(self.text, self.pos)
            if not ident:
                raise self._fail("expected a variable name")
            self.pos = ident.end()
            self._expect("=")
            out[ident.group(0)] = self.value()
            self.skip_ws()
            if self._peek() == ";":
                self.pos += 1


def parse_sv(text: str) -> dict:
    """Parse the text of a saved-variables file into ``{variable name: value}``."""
    return LuaParser(text).statements()


def load_sv(path: Path, variable: str):
    """Value of ``variable`` in the saved-variables file at ``path``.

    Returns None if the file or the variable does not exist.
    """
    path = Path(path)
    if not path.exists():
        return None
    return parse_sv(path.read_text(encoding="utf-8", errors="replace")).get(variable)

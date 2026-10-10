"""A small scanner for syntax that LuaJIT accepts but WoW's Lua 5.1 rejects.

LuaJIT is the closest interpreter we can install, but it also understands ``goto``, labels, the
``\\x`` and ``\\z`` string escapes and 64-bit literals. Code using them would pass the LuaJIT tests
and then fail to load in the game, so every addon file and every generated data file is scanned.
"""
from __future__ import annotations

import re

_LONG_OPEN = re.compile(r"\[(=*)\[")
_BAD_CODE = (
    (re.compile(r"\bgoto\b"), "goto is not in Lua 5.1"),
    (re.compile(r"::"), "labels (::name::) are not in Lua 5.1"),
    (re.compile(r"\b\d+(?:LL|ULL|ll|ull)\b"), "64-bit literals are LuaJIT-only"),
    (re.compile(r"\b\d+[iI]\b"), "imaginary literals are LuaJIT-only"),
    (re.compile(r"//"), "integer division is not in Lua 5.1"),
    (re.compile(r"\b0[xX][0-9a-fA-F.]*[pP][+-]?\d"), "hexadecimal floats are not in Lua 5.1"),
)
_BAD_ESCAPES = {"x": r"\x", "z": r"\z", "u": r"\u"}


def scan_lua51(source: str) -> list[str]:
    """Return a list of problems (empty if the source only uses Lua 5.1 syntax)."""
    problems: list[str] = []
    code: list[str] = []
    i, n, line = 0, len(source), 1
    while i < n:
        char = source[i]
        if source.startswith("--", i):
            opening = _LONG_OPEN.match(source, i + 2)
            if opening:
                close = "]" + "=" * len(opening.group(1)) + "]"
                end = source.find(close, opening.end())
                if end < 0:
                    problems.append(f"line {line}: unterminated long comment")
                    break
                line += source.count("\n", i, end)
                i = end + len(close)
            else:
                end = source.find("\n", i)
                i = n if end < 0 else end
            continue
        if char in "\"'":
            j = i + 1
            while j < n and source[j] != char:
                if source[j] == "\n":
                    problems.append(f"line {line}: newline inside a string")
                    break
                if source[j] == "\\":
                    escape = source[j + 1:j + 2]
                    if escape in _BAD_ESCAPES:
                        problems.append(f"line {line}: escape {_BAD_ESCAPES[escape]} is not in Lua 5.1")
                    if escape == "\n":
                        line += 1
                    j += 2
                    continue
                j += 1
            code.append("S")
            i = j + 1
            continue
        opening = _LONG_OPEN.match(source, i) if char == "[" else None
        if opening:
            close = "]" + "=" * len(opening.group(1)) + "]"
            end = source.find(close, opening.end())
            if end < 0:
                problems.append(f"line {line}: unterminated long string")
                break
            line += source.count("\n", i, end)
            code.append("S")
            i = end + len(close)
            continue
        code.append(char)
        if char == "\n":
            line += 1
        i += 1
    text = "".join(code)
    for pattern, message in _BAD_CODE:
        for match in pattern.finditer(text):
            problems.append(f"line {text.count(chr(10), 0, match.start()) + 1}: {message}")
    return problems

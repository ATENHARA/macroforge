"""A small Lua 5.1 lexer and parser, used to inspect scripts before they reach the game.

It reads exactly the Lua that World of Warcraft runs (5.1: no ``goto``, no ``\\x`` or ``\\z``
escapes, no integer division) and turns it into a light syntax tree. Anything it does not
understand is a ``ValidationError``: for a safety check, "cannot read it" must mean "refuse it".
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .errors import ValidationError

MAX_SOURCE = 4096  # characters; a macro line is far shorter, this only bounds hostile input
MAX_DEPTH = 60  # nesting of brackets, calls, blocks and functions

KEYWORDS = frozenset({
    "and", "break", "do", "else", "elseif", "end", "false", "for", "function", "if", "in", "local",
    "nil", "not", "or", "repeat", "return", "then", "true", "until", "while"})
_DIGITS = "0123456789"
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_NUMBER = re.compile(r"0[xX][0-9a-fA-F]+|(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][+-]?[0-9]+)?")
_LONG_OPEN = re.compile(r"\[(=*)\[")
_THREE = ("...",)
_TWO = ("==", "~=", "<=", ">=", "..")
_ONE = "+-*/%^#<>=(){}[];:,."
_SIMPLE_ESCAPES = {"a": "\a", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t", "v": "\v",
                   "\\": "\\", '"': '"', "'": "'"}


@dataclass(frozen=True)
class Token:
    """One lexical item: ``kind`` is name, keyword, number, string, op or eof."""

    kind: str
    value: object
    pos: int


def _fail(message: str, pos: int) -> ValidationError:
    return ValidationError(f"script: {message} (character {pos + 1})")


def _long_string(src: str, start: int) -> tuple[str, int]:
    """Read a ``[[...]]`` or ``[=[...]=]`` block starting at ``start``; returns text and end."""
    match = _LONG_OPEN.match(src, start)
    if not match:
        raise _fail("bad long bracket", start)
    close = "]" + match.group(1) + "]"
    end = src.find(close, match.end())
    if end < 0:
        raise _fail("unfinished long string or comment", start)
    text = src[match.end():end]
    if not match.group(1) and "[[" in text:  # the game's Lua refuses a [[ inside a [[ ]] block
        raise _fail("nesting of [[...]] is not allowed", start)
    return text, end + len(close)


def _quoted(src: str, start: int) -> tuple[str, int]:
    """Read a quoted string starting at ``start``; returns its decoded value and end."""
    quote, i, out = src[start], start + 1, []
    while i < len(src):
        ch = src[i]
        if ch == quote:
            return "".join(out), i + 1
        if ch == "\n":
            raise _fail("unfinished string", start)
        if ch != "\\":
            out.append(ch)
            i += 1
            continue
        i += 1
        if i >= len(src):
            break
        esc = src[i]
        if esc in _SIMPLE_ESCAPES:
            out.append(_SIMPLE_ESCAPES[esc])
            i += 1
        elif esc in _DIGITS:
            digits = re.match(r"[0-9]{1,3}", src[i:]).group(0)
            if int(digits) > 255:
                raise _fail("escape sequence too large", i)
            out.append(chr(int(digits)))
            i += len(digits)
        elif esc in "xzXZ" or esc == "\n":
            raise _fail(f"the escape \\{esc.strip() or 'newline'} is not Lua 5.1", i)
        else:
            out.append(esc)  # Lua 5.1 drops the backslash of an unknown escape
            i += 1
    raise _fail("unfinished string", start)


def tokenize(src: str) -> list[Token]:
    """Split ``src`` into tokens (comments and spaces dropped); the last token is ``eof``."""
    if len(src) > MAX_SOURCE:
        raise ValidationError("script: the code is far too long")
    tokens: list[Token] = []
    i = 0
    while i < len(src):
        ch = src[i]
        if ch in " \t\r\n":
            i += 1
        elif src.startswith("--", i):
            if _LONG_OPEN.match(src, i + 2):
                _, i = _long_string(src, i + 2)
            else:
                end = src.find("\n", i)
                i = len(src) if end < 0 else end
        elif ch in "\"'":
            value, end = _quoted(src, i)
            tokens.append(Token("string", value, i))
            i = end
        elif ch == "[" and _LONG_OPEN.match(src, i):
            value, end = _long_string(src, i)
            tokens.append(Token("string", value, i))
            i = end
        elif ch.isascii() and (ch.isalpha() or ch == "_"):
            word = _NAME.match(src, i).group(0)
            tokens.append(Token("keyword" if word in KEYWORDS else "name", word, i))
            i += len(word)
        elif ch in _DIGITS or (ch == "." and src[i + 1:i + 2] in tuple(_DIGITS)):
            text = _NUMBER.match(src, i).group(0)
            nxt = src[i + len(text):i + len(text) + 1]
            if nxt.isascii() and (nxt.isalnum() or nxt == "_"):
                raise _fail("malformed number", i)
            try:
                value = float(int(text, 16)) if text[:2] in ("0x", "0X") else float(text)
            except OverflowError:  # a hex number too big for a double is infinite, as in Lua
                value = float("inf")
            tokens.append(Token("number", value, i))
            i += len(text)
        elif src.startswith(_THREE, i):
            tokens.append(Token("op", "...", i))
            i += 3
        elif src[i:i + 2] in _TWO:
            tokens.append(Token("op", src[i:i + 2], i))
            i += 2
        elif ch in _ONE:
            tokens.append(Token("op", ch, i))
            i += 1
        else:
            raise _fail(f"unexpected character {ch!r}", i)
    tokens.append(Token("eof", "<end>", len(src)))
    return tokens


# -- syntax tree ------------------------------------------------------------------------------

@dataclass
class Lit:
    """A literal: ``number``, ``string``, ``nil``, ``true``, ``false`` or ``vararg``."""

    kind: str
    value: object = None


@dataclass
class Name:
    """A variable name."""

    name: str
    pos: int = 0


@dataclass
class Index:
    """``base.key`` (``dot`` true, key is a string literal) or ``base[key]``."""

    base: object
    key: object
    dot: bool


@dataclass
class Call:
    """``func(args)`` or, with ``method``, ``func:method(args)``."""

    func: object
    args: list
    method: str | None = None
    pos: int = 0


@dataclass
class Func:
    """A function expression: parameter names and the body block."""

    params: list[str]
    body: list


@dataclass
class Bin:
    """A binary operation."""

    op: str
    left: object
    right: object


@dataclass
class Un:
    """A unary operation (``not``, ``-``, ``#``)."""

    op: str
    operand: object


@dataclass
class Table:
    """A table constructor: ``(key or None, value)`` pairs."""

    items: list[tuple[object, object]]


@dataclass
class Paren:
    """A parenthesized expression."""

    expr: object


@dataclass
class Stat:
    """A statement; ``kind`` says which fields are used (see the parser)."""

    kind: str
    names: list[str] = field(default_factory=list)
    exprs: list = field(default_factory=list)
    targets: list = field(default_factory=list)
    body: list = field(default_factory=list)
    clauses: list = field(default_factory=list)
    orelse: list = field(default_factory=list)
    func: Func | None = None
    pos: int = 0


class Parser:
    """Recursive-descent parser for Lua 5.1 chunks."""

    def __init__(self, src: str) -> None:
        self.tokens = tokenize(src)
        self.i = 0
        self.depth = 0
        self.vararg = [True]  # may `...` be used here? (main chunk: yes; functions: if declared)
        self.loops = [0]  # loops being parsed in the current function (``break`` needs one)

    # -- token helpers ---------------------------------------------------------------------
    @property
    def tok(self) -> Token:
        """The current token."""
        return self.tokens[self.i]

    def error(self, what: str = "") -> ValidationError:
        """A syntax error at the current token."""
        shown = self.tok.value if self.tok.kind != "string" else "a string"
        return _fail(f"syntax error near {shown!r}{(' (' + what + ')') if what else ''}",
                     self.tok.pos)

    def is_op(self, value: str) -> bool:
        """True if the current token is this operator."""
        return self.tok.kind == "op" and self.tok.value == value

    def is_kw(self, value: str) -> bool:
        """True if the current token is this keyword."""
        return self.tok.kind == "keyword" and self.tok.value == value

    def accept_op(self, value: str) -> bool:
        """Consume the operator if it is next."""
        if self.is_op(value):
            self.i += 1
            return True
        return False

    def accept_kw(self, value: str) -> bool:
        """Consume the keyword if it is next."""
        if self.is_kw(value):
            self.i += 1
            return True
        return False

    def expect_op(self, value: str) -> None:
        """Consume the operator or fail."""
        if not self.accept_op(value):
            raise self.error(f"expected {value!r}")

    def expect_kw(self, value: str) -> None:
        """Consume the keyword or fail."""
        if not self.accept_kw(value):
            raise self.error(f"expected {value!r}")

    def expect_name(self) -> str:
        """Consume a name and return it."""
        if self.tok.kind != "name":
            raise self.error("expected a name")
        self.i += 1
        return self.tokens[self.i - 1].value

    def enter(self) -> None:
        """Count one nesting level."""
        self.depth += 1
        if self.depth > MAX_DEPTH:
            raise ValidationError("script: nested too deeply")

    def leave(self) -> None:
        """Leave one nesting level."""
        self.depth -= 1

    # -- statements ------------------------------------------------------------------------
    def parse_chunk(self) -> list[Stat]:
        """The whole script as a block; fails unless every token is used."""
        block = self.block()
        if self.tok.kind != "eof":
            raise self.error()
        return block

    def block(self) -> list[Stat]:
        """Statements up to a block-ending keyword (``return``/``break`` must be last)."""
        self.enter()
        out: list[Stat] = []
        while self.tok.kind != "eof" and not (self.tok.kind == "keyword" and self.tok.value in (
                "end", "else", "elseif", "until")):
            if self.is_kw("return") or self.is_kw("break"):
                out.append(self.last_statement())
                self.accept_op(";")
                break
            out.append(self.statement())
            self.accept_op(";")
        self.leave()
        return out

    def last_statement(self) -> Stat:
        """``return [exprs]`` or ``break``."""
        pos = self.tok.pos
        if self.accept_kw("break"):
            if not self.loops[-1]:
                raise self.error("break outside a loop")
            return Stat("break", pos=pos)
        self.expect_kw("return")
        exprs: list = []
        if not (self.tok.kind == "eof" or self.is_op(";") or (self.tok.kind == "keyword" and
                self.tok.value in ("end", "else", "elseif", "until"))):
            exprs = self.expr_list()
        return Stat("return", exprs=exprs, pos=pos)

    def loop_body(self) -> list[Stat]:
        """A block inside a loop (so ``break`` is allowed in it)."""
        self.loops[-1] += 1
        body = self.block()
        self.loops[-1] -= 1
        return body

    def statement(self) -> Stat:
        """One statement."""
        pos = self.tok.pos
        if self.accept_kw("do"):
            body = self.block()
            self.expect_kw("end")
            return Stat("do", body=body, pos=pos)
        if self.accept_kw("while"):
            cond = self.expr()
            self.expect_kw("do")
            body = self.loop_body()
            self.expect_kw("end")
            return Stat("while", exprs=[cond], body=body, pos=pos)
        if self.accept_kw("repeat"):
            body = self.loop_body()
            self.expect_kw("until")
            return Stat("repeat", exprs=[self.expr()], body=body, pos=pos)
        if self.accept_kw("if"):
            return self.if_statement(pos)
        if self.accept_kw("for"):
            return self.for_statement(pos)
        if self.accept_kw("function"):
            return self.function_statement(pos)
        if self.accept_kw("local"):
            return self.local_statement(pos)
        return self.expression_statement(pos)

    def if_statement(self, pos: int) -> Stat:
        """``if ... then ... {elseif ...} [else ...] end`` (``if`` already consumed)."""
        clauses = []
        cond = self.expr()
        self.expect_kw("then")
        clauses.append((cond, self.block()))
        orelse: list = []
        while True:
            if self.accept_kw("elseif"):
                cond = self.expr()
                self.expect_kw("then")
                clauses.append((cond, self.block()))
            elif self.accept_kw("else"):
                orelse = self.block()
                self.expect_kw("end")
                break
            else:
                self.expect_kw("end")
                break
        return Stat("if", clauses=clauses, orelse=orelse, pos=pos)

    def for_statement(self, pos: int) -> Stat:
        """Numeric or generic ``for`` (``for`` already consumed)."""
        first = self.expect_name()
        if self.accept_op("="):
            exprs = [self.expr()]
            self.expect_op(",")
            exprs.append(self.expr())
            if self.accept_op(","):
                exprs.append(self.expr())
            self.expect_kw("do")
            body = self.loop_body()
            self.expect_kw("end")
            return Stat("numfor", names=[first], exprs=exprs, body=body, pos=pos)
        names = [first]
        while self.accept_op(","):
            names.append(self.expect_name())
        self.expect_kw("in")
        exprs = self.expr_list()
        self.expect_kw("do")
        body = self.loop_body()
        self.expect_kw("end")
        return Stat("genfor", names=names, exprs=exprs, body=body, pos=pos)

    def function_statement(self, pos: int) -> Stat:
        """``function a.b:c() ... end`` (``function`` already consumed)."""
        target: object = Name(self.expect_name(), self.tokens[self.i - 1].pos)
        method = False
        while self.is_op(".") or self.is_op(":"):
            method = self.is_op(":")
            self.i += 1
            target = Index(target, Lit("string", self.expect_name()), True)
            if method:
                break
        func = self.function_body(["self"] if method else [])
        return Stat("function", targets=[target], func=func, pos=pos)

    def local_statement(self, pos: int) -> Stat:
        """``local function f`` or ``local a, b = ...`` (``local`` already consumed)."""
        if self.accept_kw("function"):
            name = self.expect_name()
            return Stat("localfunction", names=[name], func=self.function_body([]), pos=pos)
        names = [self.expect_name()]
        while self.accept_op(","):
            names.append(self.expect_name())
        exprs = self.expr_list() if self.accept_op("=") else []
        return Stat("local", names=names, exprs=exprs, pos=pos)

    def expression_statement(self, pos: int) -> Stat:
        """An assignment or a function call used as a statement."""
        first = self.suffixed_expr()
        if self.is_op("=") or self.is_op(","):
            targets = [first]
            while self.accept_op(","):
                targets.append(self.suffixed_expr())
            self.expect_op("=")
            for target in targets:
                if not isinstance(target, (Name, Index)):
                    raise self.error("cannot assign to this")
            return Stat("assign", targets=targets, exprs=self.expr_list(), pos=pos)
        if not isinstance(first, Call):
            raise self.error("expected a statement")
        return Stat("call", exprs=[first], pos=pos)

    def function_body(self, params: list[str]) -> Func:
        """``(params) block end`` after ``function``."""
        self.expect_op("(")
        params = list(params)
        has_vararg = False
        if not self.is_op(")"):
            while True:
                if self.accept_op("..."):
                    has_vararg = True
                    break
                params.append(self.expect_name())
                if not self.accept_op(","):
                    break
        self.expect_op(")")
        self.vararg.append(has_vararg)
        self.loops.append(0)  # a function body starts outside any loop
        body = self.block()
        self.loops.pop()
        self.vararg.pop()
        self.expect_kw("end")
        return Func(params, body)

    # -- expressions -----------------------------------------------------------------------
    def expr_list(self) -> list:
        """One or more comma-separated expressions."""
        out = [self.expr()]
        while self.accept_op(","):
            out.append(self.expr())
        return out

    _BINARY = {"or": (1, 1), "and": (2, 2), "<": (3, 3), ">": (3, 3), "<=": (3, 3), ">=": (3, 3),
               "~=": (3, 3), "==": (3, 3), "..": (5, 4), "+": (6, 6), "-": (6, 6), "*": (7, 7),
               "/": (7, 7), "%": (7, 7), "^": (10, 9)}
    _UNARY_LEVEL = 8

    def expr(self, limit: int = 0) -> object:
        """An expression, binding at least as tightly as ``limit`` (precedence climbing)."""
        self.enter()
        if self.tok.kind == "keyword" and self.tok.value == "not" or (
                self.tok.kind == "op" and self.tok.value in ("-", "#")):
            op = self.tok.value
            self.i += 1
            left: object = Un(op, self.expr(self._UNARY_LEVEL))
        else:
            left = self.simple_expr()
        while self.tok.kind in ("op", "keyword") and self.tok.value in self._BINARY:
            op = self.tok.value
            left_prec, right_prec = self._BINARY[op]
            if left_prec <= limit:
                break
            self.i += 1
            left = Bin(op, left, self.expr(right_prec))
        self.leave()
        return left

    def simple_expr(self) -> object:
        """A literal, table, function or suffixed expression."""
        tok = self.tok
        if tok.kind == "number":
            self.i += 1
            return Lit("number", tok.value)
        if tok.kind == "string":
            self.i += 1
            return Lit("string", tok.value)
        if tok.kind == "keyword" and tok.value in ("nil", "true", "false"):
            self.i += 1
            return Lit(tok.value)
        if self.is_op("..."):
            if not self.vararg[-1]:
                raise self.error("... is only allowed in a function declared with ...")
            self.i += 1
            return Lit("vararg")
        if self.is_op("{"):
            return self.table()
        if self.accept_kw("function"):
            return self.function_body([])
        return self.suffixed_expr()

    def primary_expr(self) -> object:
        """A name or a parenthesized expression."""
        if self.tok.kind == "name":
            self.i += 1
            return Name(self.tokens[self.i - 1].value, self.tokens[self.i - 1].pos)
        if self.accept_op("("):
            inner = self.expr()
            self.expect_op(")")
            return Paren(inner)
        raise self.error("expected an expression")

    def suffixed_expr(self) -> object:
        """A primary expression followed by ``.name``, ``[key]``, ``:method(...)`` or calls."""
        node = self.primary_expr()
        while True:
            pos = self.tok.pos
            if self.accept_op("."):
                node = Index(node, Lit("string", self.expect_name()), True)
            elif self.accept_op("["):
                key = self.expr()
                self.expect_op("]")
                node = Index(node, key, False)
            elif self.accept_op(":"):
                method = self.expect_name()
                node = Call(node, self.call_args(), method, pos)
            elif self.is_op("(") or self.is_op("{") or self.tok.kind == "string":
                node = Call(node, self.call_args(), None, pos)
            else:
                return node

    def call_args(self) -> list:
        """Arguments of a call: ``(a, b)``, a table or a string."""
        if self.tok.kind == "string":
            self.i += 1
            return [Lit("string", self.tokens[self.i - 1].value)]
        if self.is_op("{"):
            return [self.table()]
        self.expect_op("(")
        args = [] if self.is_op(")") else self.expr_list()
        self.expect_op(")")
        return args

    def table(self) -> Table:
        """A table constructor."""
        self.expect_op("{")
        items: list[tuple[object, object]] = []
        while not self.is_op("}"):
            if self.accept_op("["):
                key = self.expr()
                self.expect_op("]")
                self.expect_op("=")
                items.append((key, self.expr()))
            elif self.tok.kind == "name" and self.tokens[self.i + 1].kind == "op" and \
                    self.tokens[self.i + 1].value == "=":
                key = Lit("string", self.tok.value)
                self.i += 2
                items.append((key, self.expr()))
            else:
                items.append((None, self.expr()))
            if not (self.accept_op(",") or self.accept_op(";")):
                break
        self.expect_op("}")
        return Table(items)


def parse(src: str) -> list[Stat]:
    """Parse one chunk of Lua 5.1 source into a block of statements."""
    return Parser(src).parse_chunk()

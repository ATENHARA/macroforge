"""Check a ``/run`` script against the rules in ``resources/script_rules.toml``.

The script is parsed with :mod:`luaparse` and walked once. Structural rules close the doors a plain
denylist cannot (computed names, runaway loops, recursion, timers); the denylist names the dangerous
families of game functions; everything else the game exposes stays allowed. A script may change game
state at most ``max_actions`` times ("one key press, one action") and never inside a loop.

This is a safety net designed around Blizzard's published rules and the community's reading of them;
it is not a promise that every script is acceptable to Blizzard.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache

from .assets import load_toml
from .errors import ValidationError
from .luaparse import (Bin, Call, Func, Index, Lit, Name, Paren, Stat, Table, Un, parse)

OWN_PREFIX = "MF_"  # the only global names a script may create


@dataclass(frozen=True)
class ApiKnowledge:
    """The Blizzard functions this client has (from the in-game probe), to catch invented names."""

    names: frozenset[str]
    members: dict[str, frozenset[str]] = field(default_factory=dict)


@dataclass
class ScriptReport:
    """What the checker found out about an accepted script."""

    actions: int = 0
    notes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Rules:
    """``script_rules.toml`` with its patterns compiled."""

    max_actions: int
    max_loop_depth: int
    max_loop_bound: int
    banned: dict[str, str]
    free_globals: frozenset[str]
    free_namespaces: frozenset[str]
    output_globals: frozenset[str]
    output_methods: frozenset[str]
    query_name: re.Pattern
    query_member: re.Pattern
    constant: re.Pattern
    denied_namespaces: frozenset[str]
    denied: tuple[tuple[re.Pattern, str], ...]
    denied_cvars: tuple[re.Pattern, ...]


@lru_cache(maxsize=1)
def load_rules() -> Rules:
    """The rules shipped with MacroForge."""
    data = load_toml("script_rules.toml")
    free = data["free"]
    return Rules(
        max_actions=data["max_actions"], max_loop_depth=data["max_loop_depth"],
        max_loop_bound=data["max_loop_bound"], banned=dict(data["banned_names"]),
        free_globals=frozenset(free["globals"]), free_namespaces=frozenset(free["namespaces"]),
        output_globals=frozenset(free["output_globals"]),
        output_methods=frozenset(free["output_methods"]),
        query_name=re.compile(free["query_name"]), query_member=re.compile(free["query_member"]),
        constant=re.compile(free["constant"]),
        denied_namespaces=frozenset(data["denied_namespaces"]),
        denied=tuple((re.compile(d["pattern"]), d["reason"]) for d in data["denied"]),
        denied_cvars=tuple(re.compile(p, re.I) for p in data["denied_cvars"]))


def refuse(message: str) -> ValidationError:
    """The error raised for a script that breaks a rule."""
    return ValidationError(f"script: {message}")


class _Checker:
    """One walk over one script."""

    def __init__(self, rules: Rules, max_actions: int, known: ApiKnowledge | None) -> None:
        self.rules, self.max_actions, self.known = rules, max_actions, known
        self.scopes: list[dict[str, int]] = [{}]  # local name -> function depth where declared
        self.func_depth = 0
        self.loop_depth = 0
        self.actions = 0
        self.notes: set[str] = set()

    # -- scopes ----------------------------------------------------------------------------
    def declare(self, name: str) -> None:
        """Add a local variable to the current scope."""
        self.check_banned(name)
        self.scopes[-1][name] = self.func_depth

    def local_depth(self, name: str) -> int | None:
        """Function depth where this local was declared, or None if it is not a local."""
        for scope in reversed(self.scopes):
            if name in scope:
                return scope[name]
        return None

    def check_banned(self, name: str) -> None:
        """Refuse a banned name wherever it appears."""
        if name in self.rules.banned:
            raise refuse(f"{name} is not allowed ({self.rules.banned[name]})")

    # -- chains ----------------------------------------------------------------------------
    def chain(self, node: object) -> tuple[str | None, list[str], bool]:
        """(base name, field names, computed) of ``a.b.c`` / ``a[x].b``; base is None if no name."""
        path: list[str] = []
        computed = False
        while isinstance(node, Index):
            if node.dot:
                path.append(node.key.value)
            else:
                computed = True
            node = node.base
        path.reverse()
        return (node.name if isinstance(node, Name) else None), path, computed

    def visit_chain_parts(self, node: object) -> None:
        """Visit the expressions inside a chain: bracket keys and a non-name base."""
        while isinstance(node, Index):
            if not node.dot:
                self.expr(node.key)
            node = node.base
        if not isinstance(node, Name):
            self.expr(node)

    def classify(self, base: str, path: list[str]) -> str:
        """``free``, ``query`` or ``action`` for a global name (after the denylist)."""
        rules = self.rules
        if base.startswith(OWN_PREFIX) or base in rules.free_namespaces:
            return "free"
        if not path:
            if base in rules.output_globals or base in rules.free_globals:
                return "free"
            if rules.constant.match(base) or rules.query_name.match(base):
                return "query"
            return "action"
        if rules.constant.match(base):
            return "free"
        if len(path) == 1 and rules.query_member.match(path[0]):
            return "query"
        return "action"

    def check_global(self, base: str, path: list[str], computed: bool) -> str:
        """Apply the denylist and the known-API check to a global chain; returns its class."""
        rules = self.rules
        for name in [base, *path]:
            self.check_banned(name)
        if base.startswith(OWN_PREFIX):
            return "free"
        if computed and not rules.constant.match(base):
            raise refuse(f"{base} cannot be indexed with a computed name")
        if base in rules.free_namespaces:
            return "free"
        if base in rules.denied_namespaces:
            raise refuse(f"{base} is not allowed (it is only for actions on other players, "
                         "mail, trades, auctions or timers)")
        func = path[-1] if path else base
        for pattern, reason in rules.denied:
            if pattern.match(func):
                raise refuse(f"{'.'.join([base, *path])} is not allowed: it {reason}")
        self.check_known(base, path)
        return self.classify(base, path)

    def check_known(self, base: str, path: list[str]) -> None:
        """If the client's API list is known, refuse names that do not exist in this game."""
        known, rules = self.known, self.rules
        if known is None:
            self.notes.add("the game's function list is not loaded, so names were not checked")
            return
        if base in rules.free_globals or rules.constant.match(base):
            return
        if base in known.members:
            if path and path[0] not in known.members[base]:
                raise refuse(f"{base}.{path[0]} does not exist in this game")
        elif base not in known.names:
            raise refuse(f"{base} does not exist in this game (check the spelling)")

    # -- expressions -----------------------------------------------------------------------
    def reference(self, node: object, callee: bool = False) -> str:
        """Check a name or field chain; returns its class (local names are ``free``)."""
        self.visit_chain_parts(node)
        base, path, computed = self.chain(node)
        if base is None:
            return "free"
        if self.local_depth(base) is not None:
            for name in path:
                self.check_banned(name)
            self.check_banned(base)
            return "free"
        kind = self.check_global(base, path, computed)
        if kind == "action" and not callee:
            raise refuse(f"{'.'.join([base, *path])} is not one of your variables (declare "
                         "it with local), and game functions that change things must be "
                         "called directly, not stored or passed around")
        return kind

    def expr(self, node: object) -> None:
        """Walk an expression."""
        if isinstance(node, (Name, Index)):
            self.reference(node)
        elif isinstance(node, Call):
            self.call(node)
        elif isinstance(node, Func):
            self.function(node)
        elif isinstance(node, Bin):
            self.expr(node.left)
            self.expr(node.right)
            if node.op == ".." and self.loop_depth:
                self.check_doubling(node)
        elif isinstance(node, Un):
            self.expr(node.operand)
        elif isinstance(node, Paren):
            self.expr(node.expr)
        elif isinstance(node, Table):
            for key, value in node.items:
                if key is not None:
                    self.expr(key)
                self.expr(value)
        elif not isinstance(node, Lit):
            raise refuse("unsupported expression")

    def check_doubling(self, node: Bin) -> None:
        """In a loop, ``s = s .. s`` doubles a string every time round: refuse one name twice."""
        names: list[str] = []

        def gather(item: object) -> None:
            """Collect the local names that are joined together in this ``..`` chain."""
            if isinstance(item, Bin) and item.op == "..":
                gather(item.left)
                gather(item.right)
            elif isinstance(item, Name) and self.local_depth(item.name) is not None:
                names.append(item.name)

        gather(node)
        if len(names) != len(set(names)):
            raise refuse("a string joined with itself inside a loop grows without limit")

    def call(self, node: Call) -> None:
        """Check one call and count it if it changes the game."""
        for arg in node.args:
            self.expr(arg)
        if node.method is not None:
            self.method_call(node)
            return
        func = node.func
        base, path, _computed = self.chain(func)
        if base is None:
            self.expr(func)
            return
        depth = self.local_depth(base)
        if depth is not None:
            self.reference(func, callee=True)
            if depth < self.func_depth:
                raise refuse("a function cannot call a variable from outside itself "
                             "(no recursion or callbacks into your own code)")
            return
        if base.startswith(OWN_PREFIX) and self.func_depth:
            raise refuse("a function cannot call your own MF_ functions (no recursion)")
        kind = self.reference(func, callee=True)
        if base == "SetCVar" and not path:
            self.check_setcvar(node)
        if kind == "action":
            self.count_action(".".join([base, *path]))

    def method_call(self, node: Call) -> None:
        """``receiver:method(...)``: only harmless methods on game objects."""
        self.check_banned(node.method)
        receiver = node.func
        self.visit_chain_parts(receiver)
        base, path, computed = self.chain(receiver)
        if base is None or self.local_depth(base) is not None:
            if base is not None:
                self.check_banned(base)
            return
        self.check_global(base, path, computed)
        method = node.method
        if not (method in self.rules.output_methods or self.rules.query_member.match(method)):
            raise refuse(f"{base}:{method}() is not allowed (scripts may only read from, or "
                         "add messages to, game objects)")

    def check_setcvar(self, node: Call) -> None:
        """``SetCVar`` needs a plain setting name that is not on the denied list."""
        first = node.args[0] if node.args else None
        if not (isinstance(first, Lit) and first.kind == "string"):
            raise refuse("SetCVar needs the setting name written out in quotes")
        for pattern in self.rules.denied_cvars:
            if pattern.fullmatch(first.value):
                raise refuse(f"the setting {first.value!r} cannot be changed by scripts")

    def count_action(self, name: str) -> None:
        """Count a state-changing call: at most ``max_actions``, never in a loop or function."""
        if self.loop_depth or self.func_depth:
            raise refuse(f"{name}() changes the game, so it cannot be inside a loop or a function")
        self.actions += 1
        if self.actions > self.max_actions:
            raise refuse("one key press may do only one thing to the game "
                         f"(this script has {self.actions} actions; the limit is "
                         f"{self.max_actions})")

    def function(self, node: Func) -> None:
        """A function expression: its own scope, with its parameters as locals."""
        self.func_depth += 1
        self.scopes.append({})
        for param in node.params:
            self.declare(param)
        self.block(node.body, new_scope=False)
        self.scopes.pop()
        self.func_depth -= 1

    # -- statements ------------------------------------------------------------------------
    def block(self, body: list[Stat], new_scope: bool = True) -> None:
        """Walk a block of statements."""
        if new_scope:
            self.scopes.append({})
        for stat in body:
            self.statement(stat)
        if new_scope:
            self.scopes.pop()

    def statement(self, stat: Stat) -> None:
        """Walk one statement."""
        kind = stat.kind
        if kind in ("while", "repeat"):
            raise refuse("loops that can run forever (while, repeat) are not allowed; use for")
        if kind == "local":
            for expr in stat.exprs:
                self.expr(expr)
            for name in stat.names:
                self.declare(name)
        elif kind == "localfunction":
            self.declare(stat.names[0])
            self.function(stat.func)
        elif kind == "function":
            self.check_target(stat.targets[0])
            self.function(stat.func)
        elif kind == "assign":
            for expr in stat.exprs:
                self.expr(expr)
            for target in stat.targets:
                self.check_target(target)
        elif kind == "call":
            self.expr(stat.exprs[0])
        elif kind == "do":
            self.block(stat.body)
        elif kind == "if":
            for cond, body in stat.clauses:
                self.expr(cond)
                self.block(body)
            self.block(stat.orelse)
        elif kind == "numfor":
            self.numeric_for(stat)
        elif kind == "genfor":
            self.generic_for(stat)
        elif kind == "return":
            for expr in stat.exprs:
                self.expr(expr)

    def check_target(self, target: object) -> None:
        """An assignment may only change locals and MF_ variables."""
        self.visit_chain_parts(target)
        base, path, _computed = self.chain(target)
        if base is None:
            raise refuse("cannot assign to this")
        for name in [base, *path]:
            self.check_banned(name)
        if self.local_depth(base) is None and not base.startswith(OWN_PREFIX):
            raise refuse(f"{base} is a game name: scripts may only create variables with local "
                         f"or names starting {OWN_PREFIX}")

    def loop(self, stat: Stat, names: list[str]) -> None:
        """Walk a loop body one level deeper."""
        self.loop_depth += 1
        if self.loop_depth > self.rules.max_loop_depth:
            raise refuse(f"loops may be nested at most {self.rules.max_loop_depth} deep")
        self.scopes.append({})
        for name in names:
            self.declare(name)
        self.block(stat.body, new_scope=False)
        self.scopes.pop()
        self.loop_depth -= 1

    def bound_ok(self, node: object) -> bool:
        """A loop count we can trust: a small literal, or the length of something."""
        if isinstance(node, Paren):
            return self.bound_ok(node.expr)
        if isinstance(node, Lit):
            return node.kind == "number" and abs(node.value) <= self.rules.max_loop_bound
        if isinstance(node, Un):
            return node.op == "#" or (node.op == "-" and self.bound_ok(node.operand))
        return False

    def numeric_for(self, stat: Stat) -> None:
        """``for i = a, b[, step]``: counts must be small literals or ``#something``."""
        for expr in stat.exprs:
            if not self.bound_ok(expr):
                raise refuse("a for loop needs plain numbers up to "
                             f"{self.rules.max_loop_bound} (or #list) as its range")
            self.expr(expr)
        if len(stat.exprs) == 3 and isinstance(stat.exprs[2], Lit) and stat.exprs[2].value == 0:
            raise refuse("a for loop step cannot be 0")
        self.loop(stat, stat.names)

    def generic_for(self, stat: Stat) -> None:
        """``for k, v in iterator``: only ipairs, pairs and gmatch."""
        only = stat.exprs[0] if len(stat.exprs) == 1 else None
        if not isinstance(only, Call):
            raise refuse("for ... in needs ipairs(...), pairs(...) or gmatch(...)")
        self.expr(only)
        base, path, _computed = self.chain(only.func)
        name = only.method or (path[-1] if path else base)
        if name not in ("ipairs", "pairs", "gmatch"):
            raise refuse("for ... in needs ipairs(...), pairs(...) or gmatch(...)")
        self.loop(stat, stat.names)


def check_script(code: str, max_actions: int | None = None, known: ApiKnowledge | None = None,
                 rules: Rules | None = None) -> ScriptReport:
    """Check one line of Lua (what follows ``/run``). Raises ``ValidationError`` if refused."""
    rules = rules or load_rules()
    checker = _Checker(rules, rules.max_actions if max_actions is None else max_actions, known)
    checker.block(parse(code))
    return ScriptReport(actions=checker.actions, notes=sorted(checker.notes))


def script_lines(body: str) -> list[str]:
    """The code of each ``/run`` line of a script macro body; anything else is refused."""
    codes = []
    for line in body.split("\n"):
        text = line.strip()
        if not text:
            continue
        match = re.match(r"^/run\s+(\S.*)$", text)
        if not match:
            raise refuse("every line of a script macro must start with /run (use /run, not "
                         "/script, and no other commands)")
        codes.append(match.group(1))
    if not codes:
        raise refuse("the script macro has no /run line")
    return codes


def check_script_body(body: str, max_actions: int | None = None,
                      known: ApiKnowledge | None = None) -> ScriptReport:
    """Check a script macro body (one or more ``/run`` lines); actions add up across lines."""
    rules = load_rules()
    limit = rules.max_actions if max_actions is None else max_actions
    total = ScriptReport()
    for code in script_lines(body):
        report = check_script(code, limit - total.actions, known, rules)
        total.actions += report.actions
        total.notes = sorted(set(total.notes) | set(report.notes))
    return total


def rules_summary(rules: Rules | None = None, max_actions: int | None = None) -> str:
    """The rules in plain words for the AI's prompt, built from the same data the checker uses."""
    rules = rules or load_rules()
    limit = rules.max_actions if max_actions is None else max_actions
    lines = [
        f"- At most {limit} state-changing call(s) per script, never inside a loop or function "
        "(one key press does one thing). Reading information and printing are unlimited.",
        f"- for loops: plain numbers up to {rules.max_loop_bound} or #list, nested at most "
        f"{rules.max_loop_depth} deep; for ... in only with ipairs, pairs or gmatch. No while, "
        "repeat or recursion.",
        f"- New global variables must start with {OWN_PREFIX}; everything else is local.",
        "- Refused names (any use): " + ", ".join(sorted(rules.banned)) + ".",
        "- Refused whole systems: " + ", ".join(sorted(rules.denied_namespaces)) + ".",
    ]
    for _pattern, reason in rules.denied:
        lines.append(f"- A function that {reason} is refused.")
    return "\n".join(lines)

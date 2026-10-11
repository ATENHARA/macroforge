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
# The addon checks `^%s*/run%s+(%S.*)$` on each line: ASCII whitespace only.
_RUN_LINE = re.compile(r"^[ \t\n\v\f\r]*/run[ \t\n\v\f\r]+([^ \t\n\v\f\r].*)$", re.S)


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
    max_work: int
    unknown_trips: int
    string_methods: frozenset[str]
    higher_order: frozenset[str]
    no_loop_functions: frozenset[str]
    growers: frozenset[str]
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
        max_loop_bound=data["max_loop_bound"], max_work=data["max_work"],
        unknown_trips=data["unknown_trips"], string_methods=frozenset(free["string_methods"]),
        higher_order=frozenset(free["higher_order"]),
        no_loop_functions=frozenset(free["no_loop_functions"]),
        growers=frozenset(free["growers"]), banned=dict(data["banned_names"]),
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


@dataclass
class _Local:
    """A local variable: where it was declared and what we know it can hold."""

    depth: int  # function depth where it was declared
    kind: str = "value"  # "func" (defined in the script), "alias" (a safe game function) or "value"
    cost: int = 0  # for a func: the loop iterations one call can take


def unwrap(node: object) -> object:
    """The expression inside any number of parentheses."""
    while isinstance(node, Paren):
        node = node.expr
    return node


def children(node: object) -> list:
    """The direct sub-nodes of a syntax tree node (statements, expressions, function bodies)."""
    if isinstance(node, Stat):
        out: list = [*node.exprs, *node.targets, *node.body, *node.orelse]
        for cond, body in node.clauses:
            out.extend([cond, *body])
        if node.func is not None:
            out.append(node.func)
        return out
    if isinstance(node, Call):
        return [node.func, *node.args]
    if isinstance(node, Func):
        return list(node.body)
    if isinstance(node, Index):
        return [node.base, node.key]
    if isinstance(node, Bin):
        return [node.left, node.right]
    if isinstance(node, Un):
        return [node.operand]
    if isinstance(node, Paren):
        return [node.expr]
    if isinstance(node, Table):
        return [part for pair in node.items for part in pair if part is not None]
    return []


def walk(node: object):
    """Every node of a tree, the node itself first."""
    stack = [node]
    while stack:
        item = stack.pop()
        yield item
        stack.extend(children(item))


def assigned_in(body: list) -> set[str]:
    """The base names that are assigned to anywhere inside ``body`` (function bodies included)."""
    names: set[str] = set()
    for stat in body:
        for node in walk(stat):
            if isinstance(node, Stat) and node.kind == "assign":
                for target in node.targets:
                    while isinstance(target, Index):
                        target = target.base
                    if isinstance(unwrap(target), Name):
                        names.add(unwrap(target).name)
    return names


class _Checker:
    """One walk over one script."""

    def __init__(self, rules: Rules, max_actions: int, known: ApiKnowledge | None) -> None:
        self.rules, self.max_actions, self.known = rules, max_actions, known
        self.scopes: list[dict[str, _Local]] = [{}]
        self.func_depth = 0
        self.loop_depth = 0
        self.mult = 1  # iterations of the loops around the code being walked
        self.work = 0  # loop iterations this piece of code can do in total
        self.carried: list[set[str]] = []  # per enclosing loop: names assigned inside it
        self.actions = 0
        self.notes: set[str] = set()

    # -- scopes ----------------------------------------------------------------------------
    def declare(self, name: str, kind: str = "value", cost: int = 0) -> _Local:
        """Add a local variable to the current scope."""
        self.check_banned(name)
        local = _Local(self.func_depth, kind, cost)
        self.scopes[-1][name] = local
        return local

    def local(self, name: str) -> _Local | None:
        """The local variable of this name, or None if it is not a local."""
        for scope in reversed(self.scopes):
            if name in scope:
                return scope[name]
        return None

    def check_banned(self, name: str) -> None:
        """Refuse a banned name wherever it appears."""
        if name in self.rules.banned:
            raise refuse(f"{name} is not allowed ({self.rules.banned[name]})")

    def check_field(self, name: str) -> None:
        """A field read off a value we cannot trace: not banned, not a denied function family."""
        self.check_banned(name)
        for pattern, reason in self.rules.denied:
            if pattern.match(name):
                raise refuse(f"{name} is not allowed: it {reason}")

    def charge(self, amount: int) -> None:
        """Add loop steps to the work this code can do; refuse a script that can do too much."""
        self.work += amount
        if self.work > self.rules.max_work:
            raise refuse("this script can run too many loop steps and would freeze the game")

    # -- chains ----------------------------------------------------------------------------
    def chain(self, node: object) -> tuple[str | None, list[str], bool]:
        """(base name, field names, computed) of ``a.b.c`` / ``a[x].b``; base is None if no name.

        Parentheses are looked through, so ``(a).b`` is the same chain as ``a.b``.
        """
        path: list[str] = []
        computed = False
        node = unwrap(node)
        while isinstance(node, Index):
            if node.dot:
                path.append(node.key.value)
            else:
                computed = True
            node = unwrap(node.base)
        path.reverse()
        return (node.name if isinstance(node, Name) else None), path, computed

    def visit_chain_parts(self, node: object) -> None:
        """Visit the expressions inside a chain: bracket keys and a non-name base."""
        node = unwrap(node)
        while isinstance(node, Index):
            if not node.dot:
                self.expr(node.key)
            node = unwrap(node.base)
        if not isinstance(node, Name):
            self.expr(node)

    def classify(self, base: str, path: list[str]) -> str:
        """``free``, ``query``, ``data`` or ``action`` for a global name (after the denylist)."""
        rules = self.rules
        if base.startswith(OWN_PREFIX) or base in rules.free_namespaces:
            return "free"
        if not path:
            if base in rules.output_globals or base in rules.free_globals:
                return "free"
            if rules.constant.match(base):
                return "data"
            if rules.query_name.match(base):
                return "query"
            return "action"
        if rules.constant.match(base):
            return "data"
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
        if base is None:  # a field of something we cannot trace (a call result, a literal)
            for name in path:
                self.check_field(name)
            return "data"
        if self.local(base) is not None:
            for name in [base, *path]:
                self.check_banned(name)
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
            raise refuse("a function can only be declared with local or passed straight to "
                         "a call")
        elif isinstance(node, Bin):
            self.expr(node.left)
            self.expr(node.right)
            if node.op == "..":
                self.check_concat(node)
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

    def variables_in(self, nodes: list) -> list[str]:
        """The base names of the script's own variables used anywhere inside ``nodes``."""
        found = []
        for root in nodes:
            for node in walk(root):
                if isinstance(node, Name) and (
                        self.local(node.name) is not None or node.name.startswith(OWN_PREFIX)):
                    found.append(node.name)
        return found

    def carried_count(self, nodes: list) -> int:
        """How many times the variables that an enclosing loop changes are used in ``nodes``."""
        carried = set().union(*self.carried) if self.carried else set()
        return sum(1 for name in self.variables_in(nodes) if name in carried)

    def check_concat(self, node: Bin) -> None:
        """Refuse ``..`` chains that can double a string: one variable twice, or in a loop two
        variables that the loop changes (``b = a; a = a .. b``)."""
        parts: list[object] = []

        def gather(item: object) -> None:
            """Collect the operands of a ``..`` chain."""
            item = unwrap(item)
            if isinstance(item, Bin) and item.op == "..":
                gather(item.left)
                gather(item.right)
            else:
                parts.append(item)

        gather(node)
        names = []
        for part in parts:
            base, path, _computed = self.chain(part)
            if base is not None and (self.local(base) is not None or base.startswith(OWN_PREFIX)):
                names.append(".".join([base, *path]))
        if len(names) != len(set(names)):
            raise refuse("a string joined with itself grows without limit")
        if self.carried_count(parts) > 1:
            raise refuse("inside a loop, a string may only be extended with something the loop "
                         "does not change (it could double every time round)")

    def callback(self, node: Func) -> None:
        """A function passed straight to a call; the call may run it many times."""
        cost = self.function(node)
        self.charge(cost * self.rules.unknown_trips * self.mult)

    def check_callbacks(self, name: str, args: list) -> None:
        """Functions that call their arguments (sort, gsub, ...) only get functions of your own."""
        if name not in self.rules.higher_order:
            return
        for arg in args:
            base, _path, _computed = self.chain(arg)
            if isinstance(unwrap(arg), (Name, Index)) and base is not None \
                    and self.local(base) is None:
                raise refuse(f"{name}() calls the function you give it, so pass a function "
                             "written in the script, not a game function")

    def check_growth(self, name: str, parts: list) -> None:
        """Inside a loop: no gsub, and string builders may use the loop's own variables once."""
        if not self.carried:
            return
        if name in self.rules.no_loop_functions:
            raise refuse(f"{name}() is not allowed inside a loop (it can grow a string "
                         "without limit)")
        if name in self.rules.growers and self.carried_count(parts) > 1:
            raise refuse(f"{name}() would use a variable the loop changes more than once, which "
                         "can double a string every time round")

    def call(self, node: Call) -> None:
        """Check one call and count it if it changes the game."""
        for arg in node.args:
            if isinstance(arg, Func):
                self.callback(arg)
            else:
                self.expr(arg)
        if node.method is not None:
            self.method_call(node)
            return
        base, path, computed = self.chain(node.func)
        if base is None:
            raise refuse("only functions that have a name can be called (not the result of "
                         "another call or an expression)")
        name = path[-1] if path else base
        self.check_callbacks(name, node.args)
        self.check_growth(name, [*node.args, node.func])
        local = self.local(base)
        if local is not None:
            self.reference(node.func, callee=True)
            if path or computed:
                raise refuse("a function cannot be called through a field of a variable")
            if local.kind == "func":
                if local.depth < self.func_depth:
                    raise refuse("a function cannot call a variable from outside itself "
                                 "(no recursion or callbacks into your own code)")
                self.charge(self.mult * local.cost)
            elif local.kind != "alias":
                raise refuse(f"{base} is not a function written in this script, so it cannot "
                             "be called")
            return
        if base.startswith(OWN_PREFIX):
            raise refuse("your own MF_ variables hold values; they cannot be called")
        kind = self.reference(node.func, callee=True)
        if kind == "data":
            raise refuse(f"{'.'.join([base, *path])} is data, not a function")
        if kind == "action" and path and not base.startswith("C_"):
            raise refuse(f"{'.'.join([base, *path])}() is not allowed: only reading functions "
                         "(Get..., Is..., Has...) can be called through a game table")
        if base == "SetCVar" and not path:
            self.check_setcvar(node)
        if kind == "action":
            self.count_action(".".join([base, *path]))

    def method_call(self, node: Call) -> None:
        """``receiver:method(...)``: only reading, printing and string methods, on any receiver."""
        method, receiver = node.method, node.func
        self.check_banned(method)
        self.visit_chain_parts(receiver)
        base, path, computed = self.chain(receiver)
        if base is None:
            for name in path:
                self.check_field(name)
        elif self.local(base) is not None:
            for name in [base, *path]:
                self.check_banned(name)
        else:
            self.check_global(base, path, computed)
        rules = self.rules
        if not (method in rules.output_methods or method in rules.string_methods
                or rules.query_member.match(method)):
            raise refuse(f":{method}() is not allowed (scripts may only read from, or add "
                         "messages to, game objects, and use string methods)")
        self.check_callbacks(method, node.args)
        self.check_growth(method, [receiver, *node.args])

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

    def function(self, node: Func) -> int:
        """A function expression: its own scope and work count; returns the work of one call."""
        self.func_depth += 1
        self.scopes.append({})
        saved = self.mult, self.work
        self.mult, self.work = 1, 0
        for param in node.params:
            self.declare(param)
        self.block(node.body, new_scope=False)
        cost = self.work
        self.mult, self.work = saved
        self.scopes.pop()
        self.func_depth -= 1
        return cost

    # -- statements ------------------------------------------------------------------------
    def block(self, body: list[Stat], new_scope: bool = True) -> None:
        """Walk a block of statements."""
        if new_scope:
            self.scopes.append({})
        for stat in body:
            self.statement(stat)
        if new_scope:
            self.scopes.pop()

    def alias_of(self, node: object) -> bool:
        """Is this expression a safe game function (``local f = format``) that may be called?"""
        base, path, computed = self.chain(node)
        if base is None or computed or not isinstance(unwrap(node), (Name, Index)):
            return False
        if self.local(base) is not None or base.startswith(OWN_PREFIX):
            return False
        return self.classify(base, path) in ("free", "query") and not (
            base in self.rules.free_namespaces and not path)

    def statement(self, stat: Stat) -> None:
        """Walk one statement."""
        kind = stat.kind
        if kind in ("while", "repeat"):
            raise refuse("loops that can run forever (while, repeat) are not allowed; use for")
        if kind == "local":
            found: list[tuple[str, int]] = []
            for expr in stat.exprs:
                if isinstance(expr, Func):
                    found.append(("func", self.function(expr)))
                else:
                    self.expr(expr)
                    found.append(("alias" if self.alias_of(expr) else "value", 0))
            for index, name in enumerate(stat.names):
                what, cost = found[index] if index < len(found) else ("value", 0)
                self.declare(name, what, cost)
        elif kind == "localfunction":
            local = self.declare(stat.names[0], "func")
            local.cost = self.function(stat.func)
        elif kind == "function":
            raise refuse("declare a function with local function, not as a global or a field")
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
        local = self.local(base)
        if local is None and not base.startswith(OWN_PREFIX):
            raise refuse(f"{base} is a game name: scripts may only create variables with local "
                         f"or names starting {OWN_PREFIX}")
        if local is not None and not path and local.kind != "value":
            raise refuse(f"{base} holds a function and cannot be given another value")

    def loop(self, stat: Stat, names: list[str], trips: int) -> None:
        """Walk a loop body one level deeper; ``trips`` is the most times it can run."""
        self.loop_depth += 1
        if self.loop_depth > self.rules.max_loop_depth:
            raise refuse(f"loops may be nested at most {self.rules.max_loop_depth} deep")
        saved = self.mult
        self.mult = saved * max(trips, 1)
        self.charge(self.mult)
        self.carried.append(assigned_in(stat.body))
        self.scopes.append({})
        for name in names:
            self.declare(name)
        self.block(stat.body, new_scope=False)
        self.scopes.pop()
        self.carried.pop()
        self.mult = saved
        self.loop_depth -= 1

    @staticmethod
    def number(node: object) -> float | None:
        """The value of a plain number written in the script (``5``, ``-5``), else None."""
        if isinstance(node, Lit) and node.kind == "number":
            return node.value
        if isinstance(node, Un) and node.op == "-" and isinstance(node.operand, Lit) \
                and node.operand.kind == "number":
            return -node.operand.value
        return None

    def numeric_for(self, stat: Stat) -> None:
        """``for i = a, b[, step]``: written-out numbers (or ``#list``), a whole non-zero step."""
        limit = self.rules.max_loop_bound
        values = [self.number(e) for e in stat.exprs]
        for index, (expr, value) in enumerate(zip(stat.exprs, values)):
            counted = index < 2 and isinstance(expr, Un) and expr.op == "#"
            if value is None and not counted or value is not None and not abs(value) <= limit:
                raise refuse(f"a for loop needs plain numbers up to {limit} (or #list) as its "
                             "range, and a whole number as its step")
            self.expr(expr)
        if len(values) == 3 and not (values[2] is not None and values[2] == int(values[2])
                                     and values[2] != 0):
            raise refuse("a for loop step must be a whole number that is not 0")
        trips = self.rules.unknown_trips
        if values[0] is not None and values[1] is not None:
            step = values[2] if len(values) == 3 else 1
            span = (values[1] - values[0]) if step > 0 else (values[0] - values[1])
            trips = max(0, int(span // abs(step)) + 1)
        self.loop(stat, stat.names, trips)

    def generic_for(self, stat: Stat) -> None:
        """``for k, v in iterator``: only the game's own ipairs, pairs and gmatch."""
        only = stat.exprs[0] if len(stat.exprs) == 1 else None
        message = "for ... in needs ipairs(...), pairs(...) or gmatch(...)"
        if not isinstance(only, Call):
            raise refuse(message)
        if only.method is not None:
            allowed = only.method == "gmatch"
        else:
            base, path, _computed = self.chain(only.func)
            allowed = base is not None and self.local(base) is None and (
                (not path and base in ("ipairs", "pairs", "gmatch"))
                or (base == "string" and path == ["gmatch"]))
        if not allowed:
            raise refuse(message)
        self.expr(only)
        self.loop(stat, stat.names, self.rules.unknown_trips)


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
        if not line:
            continue
        match = _RUN_LINE.match(line)
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
        f"- for loops: plain numbers up to {rules.max_loop_bound} or #list (a whole, non-zero "
        f"step), nested at most {rules.max_loop_depth} deep and about {rules.max_work} steps in "
        "total; for ... in only with ipairs, pairs or gmatch. No while, repeat or recursion.",
        "- Functions are declared with local function (or local f = function) and called by that "
        "name; they cannot be stored in tables, passed around or call each other. A game function "
        "may be stored in a local (local f = format) if it only reads or prints.",
        "- Through a game table only reading functions (Get..., Is..., Has...) can be called, "
        "except in C_ namespaces; the only methods allowed on any object are reading ones "
        "(Get..., Is..., Has...), AddMessage and string methods.",
        "- Inside a loop: no gsub, and a string may only be extended with something the loop does "
        "not change.",
        f"- New global variables must start with {OWN_PREFIX}; everything else is local.",
        "- Refused names (any use): " + ", ".join(sorted(rules.banned)) + ".",
        "- Refused whole systems: " + ", ".join(sorted(rules.denied_namespaces)) + ".",
    ]
    for _pattern, reason in rules.denied:
        lines.append(f"- A function that {reason} is refused.")
    return "\n".join(lines)

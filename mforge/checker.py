"""Static checks for macro text: commands, conditionals and spell names."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Callable

from .errors import ValidationError
from .probe import classify, command_aliases, wiki_commands, wiki_conditionals
from .savedvars import load_sv
from .settings import Settings
from .spells import class_spells, load_spells
from .validate import norm

SPELL_COMMANDS = {"cast", "castsequence", "castrandom"}
_COMMAND = re.compile(r"^\s*/\s*([A-Za-z0-9_]+)(.*)$")
_CLAUSE = re.compile(r"^\s*((?:\[[^\]]*\]\s*)*)(.*)$")
_RESET = re.compile(r"^reset=\S+\s*")
_RANK = re.compile(r"\(.*\)$")
_ALWAYS_KNOWN_SPELLS = {"attack", "shoot", "auto shot"}


@dataclass(frozen=True)
class Finding:
    """One problem found in a macro.

    ``kind`` is command, conditional, metacommand, syntax or spell.
    """

    kind: str
    text: str


def _load_probe(settings: Settings) -> dict | None:
    """The newest usable probe result (saved variables, else the cached copy), or None."""
    candidates = []
    try:
        candidates.append(load_sv(settings.saved_vars_file, "MacroForgeProbe"))
    except ValidationError:
        pass
    cached = settings.data / "probe.json"
    if cached.exists():
        try:
            candidates.append(json.loads(cached.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass
    for probe in candidates:
        try:  # a probe that is damaged or hostile is ignored, not trusted
            slash = {str(c).lstrip("/") for c in probe["slash"]}
            conditionals = {t.split(":")[0] for t, c in probe["conditionals"].items()
                            if classify(c, probe["control"]) != "unsupported"}
        except (KeyError, TypeError, AttributeError, ValueError):
            continue
        if slash and conditionals:
            return {"commands": slash, "conditionals": conditionals | {"mod", "btn"}}
    return None


def known_sets(settings: Settings, agent_mode: bool = False) -> tuple[set[str], set[str], bool]:
    """Commands and conditionals to check against, and whether they came from the client probe.

    Normally the client's own list is used when a probe exists (it includes other addons' commands).
    For agent-written macros the allowlist is fixed instead: wiki commands and their aliases minus
    the blocked ones, and a probe may only narrow it, never widen it.
    """
    probe = _load_probe(settings)
    if not agent_mode:
        if probe:
            return probe["commands"], probe["conditionals"], True
        return wiki_commands(), wiki_conditionals(), False
    commands = (wiki_commands() | command_aliases()) - settings.blocked_commands
    conditionals = wiki_conditionals()
    if probe:
        commands &= probe["commands"]
        conditionals &= probe["conditionals"]
    return commands, conditionals, probe is not None


def check_macro(macro: dict, class_spell_names: set[str] | None, other_spells: set[str],
                commands: set[str], conditionals: set[str], verified: bool) -> list[Finding]:
    """Check one macro; ``class_spell_names`` is None when the character's class is unknown."""
    findings: list[Finding] = []
    for raw in macro["body"].split("\n"):
        line = raw.strip()
        if not line:
            continue
        command = None
        if line.startswith("#"):
            head, _, args = line.partition(" ")
            if head not in ("#show", "#showtooltip"):
                findings.append(Finding("metacommand", f"unknown metacommand {head!r}"))
            command = "cast" if args else None
        elif line.startswith("/"):
            match = _COMMAND.match(line)
            if not match:
                findings.append(Finding("syntax", f"cannot read a command in {line[:30]!r}"))
                continue
            command, args = match.group(1).lower(), match.group(2).strip()
            if command not in commands:
                where = "client" if verified else "wiki list"
                findings.append(Finding("command", f"/{command}: not a command the {where} knows"))
        else:
            findings.append(Finding("syntax", f"line does not start with / or #: {line[:30]!r}"))
            continue
        for group in re.findall(r"\[([^\]]*)\]", args):
            for token in (t.strip() for t in group.split(",")):
                if not token or token.startswith("@") or token.startswith("target="):
                    continue
                name = token.split(":")[0].lower()
                base = name[2:] if name.startswith("no") and name[2:] in conditionals else name
                if base not in conditionals:
                    findings.append(Finding("conditional", f"conditional [{token}] not recognised"))
        if command in SPELL_COMMANDS and class_spell_names:
            findings.extend(_spell_findings(command, args, class_spell_names, other_spells))
    return findings


def _spell_findings(command: str, args: str, class_spell_names: set[str],
                    other_spells: set[str]) -> list[Finding]:
    findings = []
    for clause in args.split(";"):
        rest = _RESET.sub("", _CLAUSE.match(clause).group(2).strip())
        names = rest.split(",") if command != "cast" else [rest]
        for name in (n.strip().lstrip("!") for n in names):
            name = _RANK.sub("", name).strip()
            lowered = name.lower()
            if name and lowered not in class_spell_names and lowered not in other_spells:
                findings.append(Finding(
                    "spell", f"'{name}' is not a known spell for this class "
                             "(could be an item; check spelling)"))
    return findings


def _class_map(spec: dict) -> tuple[dict[str, str], str | None]:
    """The ``classes`` mapping and default ``class`` of a spec (wrongly typed entries ignored)."""
    raw = spec.get("classes")
    classes: dict[str, str] = {}
    if isinstance(raw, dict):
        classes = {norm(k): v for k, v in raw.items() if isinstance(v, str)}
    default = spec.get("class")
    return classes, default if isinstance(default, str) else None


def check_spec(settings: Settings, spec: dict,
               agent_mode: bool = False) -> tuple[list[tuple[str, Finding]], bool]:
    """Check every macro of a parsed ``macros.json``.

    Returns ``[(macro name, finding)]`` and whether the lists came from a client probe. With
    ``agent_mode`` the fixed, safer command allowlist is used (see :func:`known_sets`).
    """
    commands, conditionals, verified = known_sets(settings, agent_mode)
    other = set(load_spells(settings.data))
    classes, default_class = _class_map(spec)
    spell_names: dict[str, set[str]] = {}
    results: list[tuple[str, Finding]] = []
    for macro in spec["macros"]:
        scope = macro.get("char", "account")
        cls = classes.get(norm(scope if isinstance(scope, str) else "account"), default_class)
        if cls and cls not in spell_names:
            spell_names[cls] = {s["name"].lower() for s in class_spells(settings.data, cls)}
            spell_names[cls] |= _ALWAYS_KNOWN_SPELLS
        found = check_macro(macro, spell_names.get(cls), other, commands, conditionals, verified)
        results.extend((macro["name"], finding) for finding in found)
    return results, verified


def run_checks(settings: Settings, spec: dict, out: Callable[[str], None] = print) -> int:
    """Print warnings for a parsed spec and return how many there were."""
    results, verified = check_spec(settings, spec)
    for name, finding in results:
        out(f"  warn [{name}]: {finding.text}")
    source = ("this client's probe data" if verified
              else "the wiki list only (run /mf probe in game for client-verified checks)")
    out(f"checked {len(spec['macros'])} macro(s) against {source}: {len(results)} warning(s)")
    return len(results)

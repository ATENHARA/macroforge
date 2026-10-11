"""The macro ideas and script examples the in-game window offers, checked before they ship.

``suggestions.toml`` holds hand-written entries. Each one goes through the same checks as an
AI-written macro (length, characters, blocked commands, known commands, conditionals and spell names
for its class), and a script through the script checker. An entry that fails is left out and
reported in ``Suggestions.problems`` instead of reaching the game.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .assets import load_toml
from .checker import check_macro, known_sets, known_spell_names, spell_names_in
from .errors import MacroForgeError, ValidationError
from .icons import load_icons, resolve_icon
from .luacheck import check_script_body
from .probe import load_known_api
from .settings import Settings
from .spells import class_spells, load_spells
from .validate import check_blocked, clean_macro, has_bad_chars

CLASSES = ("DRUID", "HUNTER", "MAGE", "PALADIN", "PRIEST", "ROGUE", "SHAMAN", "WARLOCK", "WARRIOR")
MAX_WHY = 120  # the card has room for about two lines
_EXAMPLE = re.compile(r"### ([^\n]+)\n```\n(/run [^\n]*)\n```")


@dataclass
class Suggestions:
    """What passed the checks (``macros`` by class token, then ``scripts``) and what did not."""

    macros: dict[str, list[dict]] = field(default_factory=dict)
    scripts: list[dict] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


def _text(entry: dict, key: str, limit: int | None = None) -> str:
    value = entry.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{key} must be non-empty text")
    if has_bad_chars(value) or "|" in value or (limit and len(value) > limit):
        raise ValidationError(f"{key} has a bad character or is longer than {limit}")
    return value


def _minimum_level(settings: Settings, cls: str, body: str, cache: dict) -> int:
    """The level at which the spells of a macro are all available (0 if none is a class spell)."""
    if cls not in cache:
        levels: dict[str, int] = {}
        for row in class_spells(settings.data, cls):
            if row["level"] > 0:
                key = row["name"].lower()
                levels[key] = min(levels.get(key, 999), row["level"])
        cache[cls] = levels
    return max((cache[cls].get(n.lower(), 0) for n in spell_names_in(body)), default=0)


def _check_macro(settings: Settings, entry: dict, tools: dict) -> dict:
    cls = entry.get("class")
    if cls not in CLASSES:
        raise ValidationError(f"class must be one of {', '.join(CLASSES)}")
    name, body = clean_macro(entry.get("name"), entry.get("body"))
    check_blocked(name, body, settings.blocked_commands)
    why = _text(entry, "why", MAX_WHY)
    icon = resolve_icon(entry.get("icon"), tools["icons"], tools["spells"])
    if cls not in tools["names"]:
        tools["names"][cls] = known_spell_names(settings, cls)
    # no "other spells" allowance: an idea may only use its own class's spells
    findings = check_macro({"name": name, "body": body}, tools["names"][cls], set(),
                           tools["commands"], tools["conditionals"], tools["verified"])
    if findings:
        raise ValidationError("; ".join(f.text for f in findings))
    level = _minimum_level(settings, cls, body, tools["levels"])
    return {"name": name, "icon": icon, "level": level, "why": why, "body": body}


def _examples(settings: Settings) -> dict[str, str]:
    path = settings.root / "reference" / "script-examples.md"
    try:
        return dict(_EXAMPLE.findall(path.read_text(encoding="utf-8")))
    except OSError as exc:
        raise ValidationError(f"cannot read {path.name}: {exc.strerror}") from exc


def _check_script(settings: Settings, entry: dict, tools: dict) -> dict:
    title = _text(entry, "example")
    body = tools["examples"].get(title)
    if body is None:
        raise ValidationError(f"no example titled {title!r} in script-examples.md")
    name, body = clean_macro(entry.get("name"), body)
    check_script_body(body, max_actions=settings.script_max_actions, known=tools["api"])
    return {"name": name, "icon": resolve_icon(entry.get("icon"), tools["icons"], tools["spells"]),
            "why": _text(entry, "why", MAX_WHY), "body": body, "script": True}


def load_suggestions(settings: Settings, raw: dict | None = None) -> Suggestions:
    """Check every entry of ``suggestions.toml`` (or ``raw``, for tests) and keep the good ones."""
    raw = load_toml("suggestions.toml") if raw is None else raw
    result = Suggestions()
    try:
        commands, conditionals, verified = known_sets(settings, agent_mode=True)
        tools = {"icons": load_icons(settings.data), "spells": load_spells(settings.data),
                 "commands": commands, "conditionals": conditionals, "verified": verified,
                 "names": {}, "levels": {}, "api": load_known_api(settings.data),
                 "examples": _examples(settings)}
    except MacroForgeError as exc:
        result.problems.append(f"no suggestions were built: {exc}")
        return result
    for kind, check in (("macro", _check_macro), ("script", _check_script)):
        for entry in raw.get(kind, []):
            label = entry.get("name", "?") if isinstance(entry, dict) else "?"
            try:
                if not isinstance(entry, dict):
                    raise ValidationError("an entry must be a table")
                good = check(settings, entry, tools)
            except MacroForgeError as exc:
                result.problems.append(f"{kind} idea {label!r} left out: {exc}")
                continue
            if kind == "macro":
                result.macros.setdefault(entry["class"], []).append(good)
            else:
                result.scripts.append(good)
    return result

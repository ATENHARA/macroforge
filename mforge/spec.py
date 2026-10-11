"""Load, validate and resolve ``macros.json``."""
from __future__ import annotations

import json
from pathlib import Path

from .errors import ValidationError
from .icons import load_icons, resolve_icon
from .luacheck import check_script_body
from .probe import load_known_api
from .settings import Settings
from .spells import load_spells
from .validate import check_blocked, clean_macro, norm


KINDS = ("macro", "script")  # an entry may be marked "kind": "script" (a /run macro)


def read_spec(path: Path) -> dict:
    """Parse ``macros.json`` and check its overall shape."""
    try:
        spec = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise ValidationError(f"{path} not found") from exc
    except ValueError as exc:
        raise ValidationError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(spec, dict) or not isinstance(spec.get("macros"), list):
        raise ValidationError(f"{path} must be an object with a 'macros' list")
    return spec


def _scope(entry: dict) -> str:
    scope = entry.get("char", "account")
    if not isinstance(scope, str) or not scope:
        raise ValidationError(f"{entry.get('name')!r}: 'char' must be a non-empty string")
    return scope


def resolve_spec(spec: dict, settings: Settings, blocked: frozenset[str] | None = None,
                 icon_notes: list[str] | None = None) -> dict:
    """Validate a parsed spec and resolve icons.

    Returns ``{"account": [...], "chars": {name: [...]}, "remove": {"account": [...],
    "chars": {name: [...]}}}`` where every macro is ``{"name", "icon", "body"}``. ``blocked`` lists
    slash commands that must not appear (used for agent-written specs). With ``icon_notes`` (a
    list), an icon that cannot be found does not fail the spec: the entry in ``spec`` gets the
    default question-mark icon instead and a note is appended (AI answers invent icon names).
    """
    icons, spells = load_icons(settings.data), load_spells(settings.data)
    known = None  # the game's function list, loaded only if a script needs it
    out: dict = {"account": [], "chars": {}, "remove": {"account": [], "chars": {}}}
    for item in spec.get("remove", []):
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            raise ValidationError("each 'remove' entry needs a 'name'")
        scope = _scope(item)
        target = out["remove"]["account"] if scope == "account" \
            else out["remove"]["chars"].setdefault(scope, [])
        target.append(item["name"])
    seen: set[tuple[str, str]] = set()
    created: set[tuple[str, str]] = set()
    for entry in spec["macros"]:
        if not isinstance(entry, dict):
            raise ValidationError("each macro must be an object")
        name, body = clean_macro(entry.get("name"), entry.get("body"))
        kind = entry.get("kind", "macro")
        if kind not in KINDS:
            raise ValidationError(f"{name!r}: kind must be macro or script")
        if kind == "script":  # scripts have their own, stricter checker
            known = known or load_known_api(settings.data)
            check_script_body(body, max_actions=settings.script_max_actions, known=known)
        elif blocked:
            check_blocked(name, body, blocked)
        scope = _scope(entry)
        if (scope, name) in seen:
            raise ValidationError(f"{name!r}: duplicate macro name for {scope}")
        seen.add((scope, name))
        created.add(("account" if scope == "account" else norm(scope), name))
        try:
            icon = resolve_icon(entry.get("icon"), icons, spells)
        except ValidationError as exc:
            if icon_notes is None:
                raise
            entry["icon"] = None  # the default icon (with #showtooltip the game shows the spell's)
            icon = resolve_icon(None, icons, spells)
            icon_notes.append(f"{name!r}: {exc}")
        macro = {"name": name, "icon": icon, "body": body}
        if kind == "script":
            macro["script"] = True
        (out["account"] if scope == "account" else out["chars"].setdefault(scope, [])).append(macro)
    removed = {("account", n) for n in out["remove"]["account"]}
    removed |= {(norm(c), n) for c, names in out["remove"]["chars"].items() for n in names}
    conflicts = sorted(name for _scope, name in created & removed)
    if conflicts:
        raise ValidationError(
            "listed under both 'macros' and 'remove' (it would be created and then deleted): "
            + ", ".join(conflicts))
    if len(out["account"]) > settings.max_account_macros:
        raise ValidationError(
            f"too many account macros ({len(out['account'])} > {settings.max_account_macros})")
    for char, macros in out["chars"].items():
        if len(macros) > settings.max_char_macros:
            raise ValidationError(
                f"too many macros for {char} ({len(macros)} > {settings.max_char_macros})")
    return out


def _key(entry: dict) -> tuple[str, str]:
    scope = entry.get("char", "account")
    return (norm(scope) if isinstance(scope, str) and scope != "account" else "account",
            str(entry.get("name")))


def _state(entry: dict) -> tuple:
    """What makes a macro entry different from another: its body, icon and kind."""
    return entry.get("body"), entry.get("icon"), entry.get("kind")


def _macro_map(spec: dict) -> dict[tuple[str, str], tuple]:
    """``{(scope, name): (body, icon, kind)}`` for every macro entry of a parsed spec."""
    return {_key(m): _state(m) for m in spec.get("macros", []) if isinstance(m, dict)}


def changed_macros(old: dict, new: dict) -> list[dict]:
    """The entries of ``new`` that were added or edited compared with ``old``."""
    before = _macro_map(old)
    return [m for m in new.get("macros", []) if isinstance(m, dict)
            and before.get(_key(m)) != _state(m)]


def _remove_set(spec: dict) -> set[tuple[str, str]]:
    """``{(scope, name)}`` for every ``remove`` entry of a parsed spec."""
    return {_key(r) for r in spec.get("remove", []) if isinstance(r, dict)}


def diff_specs(old: dict, new: dict) -> dict[str, list[str]]:
    """What changed between two parsed ``macros.json`` files, by macro name.

    Returns lists ``added``, ``edited``, ``dropped`` (no longer in the spec) and ``removals`` (newly
    asked to be deleted in the game).
    """
    before, after = _macro_map(old), _macro_map(new)
    return {
        "added": sorted(k[1] for k in after if k not in before),
        "edited": sorted(k[1] for k in after if k in before and after[k] != before[k]),
        "dropped": sorted(k[1] for k in before if k not in after),
        "removals": sorted(k[1] for k in _remove_set(new) - _remove_set(old)),
    }


def check_removals(old: dict, new: dict) -> None:
    """Refuse new ``remove`` entries for macros MacroForge never managed.

    The addon deletes every in-game macro with a listed name, so an agent must not be able to
    name your hand-made macros.
    """
    managed = set(_macro_map(old))
    fresh = _remove_set(new) - _remove_set(old)
    unmanaged = sorted(name for scope, name in fresh if (scope, name) not in managed)
    if unmanaged:
        raise ValidationError(
            "refusing to remove macros MacroForge does not manage: " + ", ".join(unmanaged)[:120])


def describe_changes(changes: dict[str, list[str]]) -> str:
    """One short line for the in-game reply, built from the real diff (not from the model)."""
    parts = []
    for label, key in (("Added", "added"), ("Edited", "edited"), ("Removed", "removals"),
                       ("Dropped", "dropped")):
        names = changes[key]
        if names:
            parts.append(f"{label}: {', '.join(names)[:80]}")
    return "; ".join(parts) if parts else "No macro changes"


def load_spec(settings: Settings, blocked: frozenset[str] | None = None) -> dict:
    """Read ``macros.json`` from the project folder and resolve it."""
    return resolve_spec(read_spec(settings.spec), settings, blocked)

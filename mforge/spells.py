"""Spell data from the game tables downloaded by ``fetch``."""
from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path

from .errors import ValidationError


def read_csv(data_dir: Path, name: str) -> list[dict]:
    """Rows of ``data/<name>.csv`` as dictionaries."""
    path = Path(data_dir) / f"{name}.csv"
    try:
        with open(path, encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))
    except FileNotFoundError as exc:
        raise ValidationError(f"missing game data {path.name}: run `macroforge fetch`") from exc
    except (csv.Error, UnicodeDecodeError, OSError) as exc:
        raise ValidationError(f"cannot read game data {path.name}: {str(exc)[:80]}") from exc


def _int(row: dict, key: str) -> int:
    try:
        return int(row[key])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValidationError(f"bad value in game data column {key!r}: {row.get(key)!r}") from exc


def load_spells(data_dir: Path) -> dict[str, tuple[int, list[int]]]:
    """Map lowercase spell name to (most common icon FileDataID, [spell ids])."""
    names = {_int(r, "ID"): r["Name_lang"] for r in read_csv(data_dir, "SpellName")}
    icon_by_id: dict[int, int] = {}
    for row in read_csv(data_dir, "SpellMisc"):
        icon = _int(row, "SpellIconFileDataID")
        if icon:
            icon_by_id[_int(row, "SpellID")] = icon
    by_name: dict[str, list[int]] = {}
    for spell_id, name in names.items():
        if spell_id in icon_by_id:
            by_name.setdefault(name.lower(), []).append(spell_id)
    return {name: (Counter(icon_by_id[s] for s in ids).most_common(1)[0][0], ids)
            for name, ids in by_name.items()}


def class_spells(data_dir: Path, class_name: str) -> list[dict]:
    """All trainable spells of a class as ``{level, name, rank, id, icon, line}`` rows.

    Joins ChrClasses -> SkillRaceClassInfo/SkillLine (category 7 = class lines) -> SkillLineAbility
    -> SpellName/Spell/SpellLevels/SpellMisc. Talents live in other tables and are not included.
    """
    classes = {r["Filename"].lower(): _int(r, "ID") for r in read_csv(data_dir, "ChrClasses")}
    class_id = classes.get(class_name.lower())
    if not class_id:
        raise ValidationError(f"unknown class {class_name!r}; one of {sorted(classes)}")
    mask = 1 << (class_id - 1)
    lines = {_int(r, "ID"): r["DisplayName_lang"]
             for r in read_csv(data_dir, "SkillLine") if r["CategoryID"] == "7"}
    # only lines restricted to exactly this class (shared lines like Mounts have an all-class mask)
    mine = {_int(r, "SkillID") for r in read_csv(data_dir, "SkillRaceClassInfo")
            if _int(r, "SkillID") in lines and _int(r, "ClassMask") == mask}
    names = {_int(r, "ID"): r["Name_lang"] for r in read_csv(data_dir, "SpellName")}
    ranks = {_int(r, "ID"): r["NameSubtext_lang"] for r in read_csv(data_dir, "Spell")}
    levels = {_int(r, "SpellID"): max(_int(r, "BaseLevel"), _int(r, "SpellLevel"))
              for r in read_csv(data_dir, "SpellLevels") if r["DifficultyID"] == "0"}
    icons = {_int(r, "SpellID"): _int(r, "SpellIconFileDataID")
             for r in read_csv(data_dir, "SpellMisc")}
    out: list[dict] = []
    seen: set[int] = set()
    for row in read_csv(data_dir, "SkillLineAbility"):
        line, spell, class_mask = _int(row, "SkillLine"), _int(row, "Spell"), _int(row, "ClassMask")
        if line not in mine or (class_mask and not class_mask & mask) or spell in seen \
                or spell not in names:
            continue
        seen.add(spell)
        out.append({"level": levels.get(spell, 0), "name": names[spell],
                    "rank": ranks.get(spell, ""), "id": spell, "icon": icons.get(spell, 0),
                    "line": lines[line]})
    # level 0 = no trainer level recorded (talents, runes, passives): list those last
    return sorted(out, key=lambda s: (s["level"] == 0, s["level"], s["name"], s["rank"]))


PET_LINE_PREFIX = "Pet - "
_PET_NOISE = ("(DND)", "Scaling")  # internal passives that no macro ever casts


def pet_abilities(data_dir: Path) -> dict[str, list[str]]:
    """Ability names of every pet family, e.g. ``{"Felhunter": ["Devour Magic", "Spell Lock"]}``.

    The game data files each pet family's abilities under a skill line called ``Pet - <Family>``,
    so a macro for Spell Lock can be tied to ``[pet:Felhunter]`` even when no pet is out.
    """
    lines = {_int(r, "ID"): r["DisplayName_lang"][len(PET_LINE_PREFIX):]
             for r in read_csv(data_dir, "SkillLine")
             if r["DisplayName_lang"].startswith(PET_LINE_PREFIX)}
    names = {_int(r, "ID"): r["Name_lang"] for r in read_csv(data_dir, "SpellName")}
    families: dict[str, set[str]] = {}
    for row in read_csv(data_dir, "SkillLineAbility"):
        family = lines.get(_int(row, "SkillLine"))
        name = names.get(_int(row, "Spell"), "")
        if family and name and not any(noise in name for noise in _PET_NOISE):
            families.setdefault(family, set()).add(name)
    return {family: sorted(found) for family, found in sorted(families.items())}

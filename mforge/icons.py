"""Icon lookup: icon file names and spell icons to numeric FileDataIDs."""
from __future__ import annotations

from pathlib import Path

from .errors import ValidationError

#: With ``#showtooltip`` the game shows the spell's own icon for this "question mark" icon.
QUESTION_MARK = 134400


def load_icons(data_dir: Path) -> dict[str, int]:
    """Map lowercase icon file name (no extension) to FileDataID, from ``data/icons.csv``."""
    icons: dict[str, int] = {}
    path_in = Path(data_dir) / "icons.csv"
    try:
        # utf-8-sig: older PowerShell versions write a byte-order mark
        with open(path_in, encoding="utf-8-sig") as handle:
            for line in handle:
                file_id, _, path = line.strip().partition(";")
                if path and file_id.isdigit():
                    icons[Path(path).stem.lower()] = int(file_id)
    except FileNotFoundError as exc:
        raise ValidationError("missing game data icons.csv: run `macroforge fetch`") from exc
    except (UnicodeDecodeError, OSError, ValueError) as exc:
        raise ValidationError(f"cannot read icons.csv: {str(exc)[:80]}") from exc
    return icons


def resolve_icon(spec: object, icons: dict[str, int], spells: dict) -> int:
    """Turn a macro's ``icon`` field into a FileDataID.

    Accepts a number, ``"spell:Fear"``, ``"icon:spell_shadow_possession"`` or ``None``
    (the question-mark icon).
    """
    if spec is None:
        return QUESTION_MARK
    if isinstance(spec, bool):
        raise ValidationError(f"bad icon spec {spec!r}")
    if isinstance(spec, int):
        return spec
    kind, _, value = str(spec).partition(":")
    if kind == "spell":
        hit = spells.get(value.strip().lower())
        if not hit:
            raise ValidationError(f"unknown spell for icon: {value!r} (try `spell {value}`)")
        return hit[0]
    if kind == "icon":
        file_id = icons.get(value.strip().lower())
        if not file_id:
            raise ValidationError(f"unknown icon name: {value!r} (try `icon ...`)")
        return file_id
    if str(spec).isdigit():
        return int(spec)
    raise ValidationError(f"bad icon spec {spec!r}: use a number, 'spell:Name' or 'icon:name'")

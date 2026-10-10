"""Small file helpers: atomic writes and path safety checks."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

from .errors import ValidationError


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` so readers never see a half-written file."""
    path = Path(path)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def atomic_write_text(path: Path, text: str) -> None:
    """UTF-8 text version of :func:`atomic_write_bytes` (always ``\\n`` line endings)."""
    atomic_write_bytes(path, text.encode("utf-8"))


def system_exe(name: str) -> str:
    """Absolute path of a Windows system tool such as ``tasklist.exe``.

    Called by full path so a same-named program in the current folder is never run instead.
    Falls back to the bare name where there is no System32 folder.
    """
    root = os.environ.get("SystemRoot") or os.environ.get("WINDIR")
    if root:
        candidate = Path(root) / "System32" / name
        if candidate.is_file():
            return str(candidate)
    return name


def which_absolute(name: str, extensions: tuple[str, ...] = (".exe", ".cmd")) -> str | None:
    """Find ``name`` on PATH, looking only in absolute folders (never the current folder)."""
    for folder in os.environ.get("PATH", "").split(os.pathsep):
        if not folder or not Path(folder).is_absolute():
            continue
        for ext in extensions if os.name == "nt" else ("",):
            candidate = Path(folder) / (name + ext)
            if candidate.is_file():
                return str(candidate)
    return None


def is_link(path: Path) -> bool:
    """True for symlinks and Windows junctions."""
    path = Path(path)
    return path.is_symlink() or bool(getattr(os.path, "isjunction", lambda _p: False)(path))


def ensure_inside(base: Path, path: Path) -> Path:
    """Return ``path`` resolved, or raise if it escapes ``base``."""
    base_resolved = Path(base).resolve()
    resolved = Path(path).resolve()
    if base_resolved != resolved and base_resolved not in resolved.parents:
        raise ValidationError(f"path {path} is outside {base}")
    return resolved

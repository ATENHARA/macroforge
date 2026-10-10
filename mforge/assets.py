"""Read the data files shipped inside the package (prompt, command lists, templates)."""
from __future__ import annotations

import tomllib
from importlib import resources


def read_text(name: str) -> str:
    """Return the text of ``mforge/resources/<name>``."""
    return resources.files("mforge").joinpath("resources", name).read_text(encoding="utf-8")


def load_toml(name: str) -> dict:
    """Parse ``mforge/resources/<name>`` as TOML."""
    return tomllib.loads(read_text(name))

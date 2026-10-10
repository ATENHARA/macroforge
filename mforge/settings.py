"""Settings from ``config.json`` and the paths derived from them.

Nothing is read at import time: call :meth:`Settings.load` once, then pass the object around.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path

from .assets import load_toml
from .errors import ConfigError

log = logging.getLogger(__name__)

DEFAULT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_WOW_DIR = r"C:\Program Files (x86)\World of Warcraft\_classic_beta_"
DEFAULT_WAGO_URL = "https://wago.tools/db2/{table}/csv?build={build}"
DEFAULT_LISTFILE_API = "https://api.github.com/repos/wowdev/wow-listfile/releases/latest"

KNOWN_KEYS = {
    "wow_dir", "account", "product", "max_account_macros", "max_char_macros", "claude_path",
    "agent_model", "agent_max_budget_usd", "agent_timeout_seconds", "max_requests_per_hour",
    "max_request_chars", "blocked_commands", "fetch_allowed_hosts", "fetch_allow_http",
    "fetch_wago_url", "fetch_listfile_api", "delete_request_shots",
}


def _typed(cfg: dict, key: str, default, kind: type):
    value = cfg.get(key, default)
    if kind is float and isinstance(value, int) and not isinstance(value, bool):
        value = float(value)
    if not isinstance(value, kind) or (kind is int and isinstance(value, bool)):
        raise ConfigError(f"config.json: {key!r} must be {kind.__name__}")
    return value


def _string_list(cfg: dict, key: str, default: list[str]) -> tuple[str, ...]:
    value = cfg.get(key, default)
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ConfigError(f"config.json: {key!r} must be a list of strings")
    return tuple(value)


def detect_account(wow_dir: Path) -> str:
    """The folder under ``WTF/Account``: auto-detected when there is exactly one."""
    try:
        names = [d.name for d in (wow_dir / "WTF" / "Account").iterdir()
                 if d.is_dir() and d.name != "SavedVariables"]
    except OSError:
        names = []
    return names[0] if len(names) == 1 else "YOUR_ACCOUNT_FOLDER"


@dataclass(frozen=True)
class Settings:
    """Everything the tool needs to know about this machine and this install."""

    root: Path
    wow_dir: Path
    account: str
    product: str
    max_account_macros: int
    max_char_macros: int
    claude_path: str
    agent_model: str
    agent_max_budget_usd: float
    agent_timeout_seconds: int
    max_requests_per_hour: int
    max_request_chars: int
    blocked_commands: frozenset[str]
    fetch_allowed_hosts: tuple[str, ...]
    fetch_allow_http: bool
    fetch_wago_url: str
    fetch_listfile_api: str
    delete_request_shots: bool = True

    # -- derived paths ---------------------------------------------------------------------
    @property
    def data(self) -> Path:
        """Downloaded game tables, caches, backups and logs."""
        return self.root / "data"

    @property
    def build(self) -> Path:
        """Where ``build`` assembles the addon before ``install`` copies it."""
        return self.root / "build" / "MacroForge"

    @property
    def addon_src(self) -> Path:
        """The addon's source files in this repository."""
        return self.root / "addon" / "MacroForge"

    @property
    def spec(self) -> Path:
        """The personal macro spec."""
        return self.root / "macros.json"

    @property
    def addons(self) -> Path:
        """The game's AddOns folder."""
        return self.wow_dir / "Interface" / "AddOns"

    @property
    def account_dir(self) -> Path:
        """``WTF/Account/<account>`` in the game folder."""
        return self.wow_dir / "WTF" / "Account" / self.account

    @property
    def saved_vars_file(self) -> Path:
        """The addon's saved variables (exports, probe results and requests)."""
        return self.account_dir / "SavedVariables" / "MacroForge.lua"

    @property
    def build_info(self) -> Path:
        """Blizzard's ``.build.info``, one level above the client folder."""
        return self.wow_dir.parent / ".build.info"

    # -- loading ---------------------------------------------------------------------------
    @classmethod
    def load(cls, root: Path | None = None) -> "Settings":
        """Read ``<root>/config.json`` (optional) and apply defaults.

        ``root`` defaults to ``$MACROFORGE_HOME`` or the folder that contains ``mforge``.
        """
        root = Path(root or os.environ.get("MACROFORGE_HOME") or DEFAULT_ROOT).resolve()
        cfg: dict = {}
        path = root / "config.json"
        if path.exists():
            try:
                cfg = json.loads(path.read_text(encoding="utf-8-sig"))
            except (OSError, ValueError) as exc:
                raise ConfigError(f"cannot read {path}: {exc}") from exc
            if not isinstance(cfg, dict):
                raise ConfigError(f"{path} must contain a JSON object")
        for key in cfg:
            if key not in KNOWN_KEYS:
                log.warning("config.json: unknown key %r ignored", key)
        defaults = load_toml("defaults.toml")
        wow_dir = Path(_typed(cfg, "wow_dir", DEFAULT_WOW_DIR, str))
        blocked = _string_list(cfg, "blocked_commands", defaults["blocked_commands"])
        return cls(
            root=root,
            wow_dir=wow_dir,
            account=_typed(cfg, "account", "", str) or detect_account(wow_dir),
            product=_typed(cfg, "product", "", str) or "wow_" + wow_dir.name.strip("_"),
            max_account_macros=_typed(cfg, "max_account_macros", 120, int),
            max_char_macros=_typed(cfg, "max_char_macros", 18, int),
            claude_path=_typed(cfg, "claude_path", "", str),
            agent_model=_typed(cfg, "agent_model", "", str),
            agent_max_budget_usd=_typed(cfg, "agent_max_budget_usd", 0.5, float),
            agent_timeout_seconds=_typed(cfg, "agent_timeout_seconds", 300, int),
            max_requests_per_hour=_typed(cfg, "max_requests_per_hour", 10, int),
            max_request_chars=_typed(cfg, "max_request_chars", 500, int),
            blocked_commands=frozenset(c.lower().lstrip("/") for c in blocked),
            fetch_allowed_hosts=_string_list(cfg, "fetch_allowed_hosts",
                                             defaults["fetch_allowed_hosts"]),
            fetch_allow_http=_typed(cfg, "fetch_allow_http", False, bool),
            fetch_wago_url=_typed(cfg, "fetch_wago_url", DEFAULT_WAGO_URL, str),
            fetch_listfile_api=_typed(cfg, "fetch_listfile_api", DEFAULT_LISTFILE_API, str),
            delete_request_shots=_typed(cfg, "delete_request_shots", True, bool),
        )

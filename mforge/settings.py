"""Settings from ``config.json`` and the paths derived from them.

Nothing is read at import time: call :meth:`Settings.load` once, then pass the object around.
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

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
    "agent_backend", "llm_base_url", "llm_api_key_env", "llm_max_tokens", "llm_json_mode",
    "llm_retries", "agent_context_file", "script_context_file", "script_max_actions",
    "confirm_changes_at",
}
BACKENDS = ("claude", "openai")  # "openai" = any OpenAI-style chat endpoint (GPT, Ollama, ...)
DEFAULT_LLM_URL = "https://api.openai.com/v1"
LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")


def _typed(cfg: dict, key: str, default, kind: type):
    value = cfg.get(key, default)
    if kind is float and isinstance(value, int) and not isinstance(value, bool):
        value = float(value)
    if not isinstance(value, kind) or (kind is int and isinstance(value, bool)):
        raise ConfigError(f"config.json: {key!r} must be {kind.__name__}")
    return value


def _choice(cfg: dict, key: str, default: str, allowed: tuple[str, ...]) -> str:
    value = _typed(cfg, key, default, str)
    if value not in allowed:
        raise ConfigError(f"config.json: {key!r} must be one of {', '.join(allowed)}")
    return value


def _ranged(cfg: dict, key: str, default: int, low: int, high: int) -> int:
    value = _typed(cfg, key, default, int)
    if not low <= value <= high:
        raise ConfigError(f"config.json: {key!r} must be between {low} and {high}")
    return value


def _env_name(name: str) -> str:
    if not _ENV_NAME.match(name):
        raise ConfigError("config.json: 'llm_api_key_env' must be the NAME of an environment "
                          "variable, not the key itself")
    return name


def validate_llm_url(url: str) -> str:
    """The chat endpoint's base URL, or ``ConfigError``.

    https is required; plain http is allowed only for this PC (a local model server), so an API key
    can never travel unencrypted. No user name or password in the URL. The host is whatever you
    choose: only point it at a service you trust with your requests and key.
    """
    parts = urlparse(url)
    try:
        host = (parts.hostname or "").lower()
        _ = parts.port  # raises ValueError for a malformed port
    except ValueError:
        raise ConfigError(f"llm_base_url {url!r} has a bad port") from None
    if parts.username or parts.password:
        raise ConfigError("llm_base_url must not contain a user name or password")
    if parts.scheme == "https" and host:
        return url
    if parts.scheme == "http" and host in LOOPBACK_HOSTS:
        return url
    raise ConfigError(f"llm_base_url {url!r} must be https (http is only allowed for localhost)")


def _string_list(cfg: dict, key: str, default: list[str]) -> tuple[str, ...]:
    value = cfg.get(key, default)
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ConfigError(f"config.json: {key!r} must be a list of strings")
    return tuple(value)


def account_last_used(folder: Path) -> float:
    """When the game last wrote in an account folder (0 if unknown): the newest folder time."""
    times = []
    for path in (folder, folder / "SavedVariables"):
        try:
            times.append(path.stat().st_mtime)
        except OSError:
            continue
    return max(times, default=0.0)


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
    agent_backend: str = "claude"
    llm_base_url: str = DEFAULT_LLM_URL
    llm_api_key_env: str = "MACROFORGE_API_KEY"
    llm_max_tokens: int = 2000
    llm_json_mode: bool = True
    llm_retries: int = 1
    agent_context_file: str = ""
    script_context_file: str = ""
    script_max_actions: int = 1
    confirm_changes_at: int = 3  # macros one request may remove or edit before CONFIRM is needed

    # -- derived paths ---------------------------------------------------------------------
    @property
    def context_file(self) -> Path:
        """The rules file the AI reads: ``agent_context_file`` if set, else the bundled brief."""
        if self.agent_context_file:
            return Path(self.root, self.agent_context_file)
        return self.root / "reference" / "agent-brief.md"

    @property
    def script_context(self) -> Path:
        """The rules file for script requests: ``script_context_file`` or the bundled brief."""
        if self.script_context_file:
            return Path(self.root, self.script_context_file)
        return self.root / "reference" / "script-brief.md"

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
    def account_dirs(self) -> list[Path]:
        """The account folders this install covers: the configured one, else all of them.

        Newest first. The addon itself is shared by every account (one AddOns folder); the account
        folders only matter for its saved data. With no account folder yet, a placeholder path.
        """
        root = self.wow_dir / "WTF" / "Account"
        if self.account:
            return [root / self.account]
        try:
            found = [d for d in root.iterdir() if d.is_dir() and d.name != "SavedVariables"]
        except OSError:
            found = []
        found.sort(key=account_last_used, reverse=True)
        return found or [root / "YOUR_ACCOUNT_FOLDER"]

    @property
    def account_dir(self) -> Path:
        """The configured account folder, or the most recently used one."""
        return self.account_dirs[0]

    @property
    def saved_vars_files(self) -> list[Path]:
        """The addon's saved variables (exports, probe results, requests) of every account."""
        return [d / "SavedVariables" / "MacroForge.lua" for d in self.account_dirs]

    @property
    def saved_vars_file(self) -> Path:
        """The saved variables of the configured or most recently used account."""
        return self.saved_vars_files[0]

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
            account=_typed(cfg, "account", "", str),  # empty = every account folder
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
            agent_backend=_choice(cfg, "agent_backend", "claude", BACKENDS),
            llm_base_url=validate_llm_url(_typed(cfg, "llm_base_url", DEFAULT_LLM_URL, str)),
            llm_api_key_env=_env_name(_typed(cfg, "llm_api_key_env", "MACROFORGE_API_KEY", str)),
            llm_max_tokens=_ranged(cfg, "llm_max_tokens", 2000, 100, 16000),
            llm_json_mode=_typed(cfg, "llm_json_mode", True, bool),
            llm_retries=_ranged(cfg, "llm_retries", 1, 0, 3),
            agent_context_file=_typed(cfg, "agent_context_file", "", str),
            script_context_file=_typed(cfg, "script_context_file", "", str),
            script_max_actions=_ranged(cfg, "script_max_actions", 1, 1, 5),
            confirm_changes_at=_ranged(cfg, "confirm_changes_at", 3, 1, 100),
        )

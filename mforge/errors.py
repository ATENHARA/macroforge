"""Exceptions raised by library code. Only the command line turns them into exit codes."""
from __future__ import annotations


class MacroForgeError(Exception):
    """Base class for every error MacroForge raises on purpose."""


class ConfigError(MacroForgeError):
    """config.json (or the environment it describes) is unusable."""


class ValidationError(MacroForgeError):
    """Input that crosses a trust boundary is malformed or not allowed."""


class AgentError(MacroForgeError):
    """The headless Claude run failed or could not be started."""

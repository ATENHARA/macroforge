"""Run the headless ``claude`` CLI on a macro request, inside a throwaway workspace.

Security model: the agent never sees the project folder. Each request gets a fresh temporary
workspace holding copies of only the files it needs, ``--restricted`` mode removes code-running
tools and confines file access to that folder, and the result is validated by the caller before
anything is copied back.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from string import Template

from .assets import read_text
from .errors import AgentError, MacroForgeError
from .fsutil import atomic_write_text, system_exe, which_absolute
from .settings import Settings
from .spells import class_spells, pet_abilities
from .validate import Request

log = logging.getLogger(__name__)

#: Environment variables passed to the child. Everything else (tokens, host variables) is dropped.
ENV_ALLOW = {
    "PATH", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "USERPROFILE", "HOME",
    "HOMEDRIVE", "HOMEPATH", "APPDATA", "LOCALAPPDATA", "PROGRAMDATA", "PROGRAMFILES",
    "PROGRAMFILES(X86)", "TEMP", "TMP", "USERNAME", "LANG", "LC_ALL", "CLAUDE_CONFIG_DIR",
    "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "ALL_PROXY", "SSL_CERT_FILE", "NODE_EXTRA_CA_CERTS",
    "ANTHROPIC_API_KEY",
}
MAX_OUTPUT_BYTES = 5_000_000
MAX_SPEC_BYTES = 1_000_000


class AgentTimeout(AgentError):
    """The agent did not finish in time."""


@dataclass
class AgentResult:
    """What a finished agent run produced."""

    text: str
    spec_text: str
    usage: dict = field(default_factory=dict)


def agent_env(environ: dict[str, str] | None = None) -> dict[str, str]:
    """Allowlisted environment for the child process."""
    source = os.environ if environ is None else environ
    return {k: v for k, v in source.items() if k.upper() in ENV_ALLOW}


def _version_key(path: Path) -> tuple[int, ...]:
    """Sort key from a ``.../claude-code/2.1.293/<hash>/claude.exe`` path (newest version last)."""
    parts = re.findall(r"\d+", path.parent.parent.name)
    return tuple(int(p) for p in parts) or (0,)


def find_claude(settings: Settings) -> str:
    """Locate the standalone ``claude`` executable (config, PATH, standard install folders)."""
    if settings.claude_path:
        if not Path(settings.claude_path).is_file():
            raise AgentError(f"claude_path {settings.claude_path!r} is not a file")
        return settings.claude_path
    found = which_absolute("claude")
    if found:
        return found
    home = Path.home()
    appdata = Path(os.environ.get("APPDATA", ""))
    candidates = (
        home / ".local" / "bin" / "claude.exe",
        home / ".claude" / "local" / "claude.exe",
        appdata / "npm" / "claude.cmd",
    )
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    # Last resort: the copy bundled with the Claude desktop app. A packaged desktop app keeps it in
    # private storage that a normal terminal (or a watcher started at login) cannot see.
    bundled = sorted((appdata / "Claude" / "claude-code").glob("*/*/claude.exe"),
                     key=_version_key, reverse=True)
    if bundled:
        return str(bundled[0])
    raise AgentError(
        "claude CLI not found. Install Claude Code "
        "(PowerShell: irm https://claude.ai/install.ps1 | iex), open a new terminal, "
        "then run `python macroforge.py login`. Or set claude_path in config.json.")


def agent_root() -> Path:
    """Parent folder of the per-request workspaces (outside the project, in the user's profile)."""
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
    return Path(base) / "macroforge-agent"


def ensure_spell_table(settings: Settings, cls: str) -> Path | None:
    """Make sure ``data/spells_<class>.json`` exists; ``cls`` must be a validated class name."""
    table = settings.data / f"spells_{cls}.json"
    if not table.exists():
        try:
            rows = class_spells(settings.data, cls)
            atomic_write_text(table, json.dumps(rows, indent=1))
        except (MacroForgeError, OSError) as exc:  # missing game data must not stop the request
            log.warning("no spell table for %s: %s", cls, str(exc)[:80])
            return None
    return table


def ensure_pet_table(settings: Settings) -> Path | None:
    """Make sure ``data/pets.json`` (pet abilities by family) exists."""
    table = settings.data / "pets.json"
    if not table.exists():
        try:
            atomic_write_text(table, json.dumps(pet_abilities(settings.data), indent=1))
        except (MacroForgeError, OSError) as exc:  # missing game data must not stop the request
            log.warning("no pet table: %s", str(exc)[:80])
            return None
    return table


def prepare_workspace(settings: Settings, req: Request) -> Path:
    """Create a fresh workspace holding copies of the only files the agent needs."""
    runs = agent_root() / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    workspace = Path(tempfile.mkdtemp(prefix=f"r{req.id}-", dir=runs))
    shutil.copy2(settings.spec, workspace / "macros.json")
    brief = settings.root / "reference" / "agent-brief.md"
    if brief.exists():
        shutil.copy2(brief, workspace / "agent-brief.md")
    table = ensure_spell_table(settings, req.cls)
    if table and table.exists():
        shutil.copy2(table, workspace / "spells.json")
    pets = ensure_pet_table(settings)
    if pets and pets.exists():
        shutil.copy2(pets, workspace / "pets.json")
    icons = settings.data / "icons.csv"
    if icons.exists():
        shutil.copy2(icons, workspace / "icons.csv")
    return workspace


def build_prompt(req: Request, export: dict | None) -> str:
    """The request text plus what the addon exported about the character."""
    export = export if isinstance(export, dict) else {}
    spells = sorted({str(s.get("name")) for s in export.get("spells", []) if isinstance(s, dict)
                     and not s.get("passive") and isinstance(s.get("name"), str)})
    pet = export.get("pet") if isinstance(export.get("pet"), dict) else None
    lines = [f"Character: {req.char} (class {req.cls}, level {req.level})."]
    if pet:
        lines.append(f"Current pet: family {pet.get('family')}, abilities {pet.get('actions')}.")
    lines.append("Known spells: " + ", ".join(spells) if spells else "Known spells: unavailable.")
    return "\n".join(lines) + f"\n\nPlayer request:\n{req.text}\n"


def system_prompt(settings: Settings) -> str:
    """The agent's rules, from ``resources/prompt.md``."""
    blocked = ", ".join("/" + c for c in sorted(settings.blocked_commands))
    return Template(read_text("prompt.md")).safe_substitute(blocked=blocked)


def build_command(settings: Settings, claude: str) -> list[str]:
    """Command line for one headless run (the prompt itself goes on stdin)."""
    cmd = [claude, "-p", "--output-format", "json", "--restricted",
           "--tools", "Read,Glob,Grep,Edit", "--permission-mode", "acceptEdits",
           "--no-session-persistence", "--strict-mcp-config", "--disable-slash-commands",
           "--append-system-prompt", system_prompt(settings)]
    if settings.agent_model:
        cmd += ["--model", settings.agent_model]
    if settings.agent_max_budget_usd:
        cmd += ["--max-budget-usd", str(settings.agent_max_budget_usd)]
    return cmd


def run_process(cmd: list[str], input_text: str, cwd: Path, env: dict[str, str],
                timeout: int) -> tuple[int, str, str]:
    """Run a command with a time limit; on timeout the whole process tree is killed."""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
    pipe = subprocess.PIPE
    proc = subprocess.Popen(cmd, stdin=pipe, stdout=pipe, stderr=pipe, cwd=cwd, env=env, text=True,
                            encoding="utf-8", errors="replace", creationflags=flags)
    try:
        out, err = proc.communicate(input_text, timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        proc.communicate()
        raise AgentTimeout(f"timed out after {timeout}s") from None
    return proc.returncode, out, err


def _kill_tree(proc: subprocess.Popen) -> None:
    if sys.platform == "win32":
        subprocess.run([system_exe("taskkill.exe"), "/PID", str(proc.pid), "/T", "/F"],
                       capture_output=True, check=False)
    else:
        proc.kill()


def _error_message(returncode: int, out: str, err: str) -> str:
    message = (err or out or f"claude exited with code {returncode}").strip()
    try:  # the CLI reports errors as JSON; show its message, not the raw blob
        message = json.loads(out).get("result") or message
    except ValueError:
        pass
    if "login" in message.lower():
        message += " (run `python macroforge.py login` once and use /login)"
    return message[-300:]


def run_agent(settings: Settings, req: Request, export: dict | None,
              runner=run_process) -> AgentResult:
    """Run the agent on one request and return its reply text and the macros.json it left behind.

    The workspace is deleted afterwards whatever happens. The caller must validate ``spec_text``.
    """
    workspace = prepare_workspace(settings, req)
    try:
        cmd = build_command(settings, find_claude(settings))
        returncode, out, err = runner(cmd, build_prompt(req, export), workspace, agent_env(),
                                      settings.agent_timeout_seconds)
        if len(out) > MAX_OUTPUT_BYTES:
            raise AgentError("agent output is too large")
        if returncode != 0:
            raise AgentError(_error_message(returncode, out, err))
        try:
            parsed = json.loads(out)
        except ValueError:
            raise AgentError("agent output is not valid JSON") from None
        if not isinstance(parsed, dict):
            raise AgentError("agent output is not a JSON object")
        if parsed.get("is_error"):
            raise AgentError(str(parsed.get("result"))[:300])
        spec_file = workspace / "macros.json"
        if not spec_file.is_file() or spec_file.stat().st_size > MAX_SPEC_BYTES:
            raise AgentError("agent left no usable macros.json")
        usage = {"usage": parsed.get("usage"), "cost_usd": parsed.get("total_cost_usd"),
                 "turns": parsed.get("num_turns")}
        return AgentResult(text=str(parsed.get("result", "")),
                           spec_text=spec_file.read_text(encoding="utf-8"), usage=usage)
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def extract_reply(text: str) -> str:
    """The part of the agent's answer after ``REPLY:`` (or its last 300 characters)."""
    match = re.search(r"REPLY:\s*(.*)", text, re.S)
    return (match.group(1) if match else text[-300:]).strip()


def login(settings: Settings) -> int:
    """Open the CLI interactively (clean environment) so the user can run ``/login`` once."""
    exe = find_claude(settings)
    print(f"Starting {exe}\nType /login, finish the sign-in in your browser, then type /exit.\n")
    return subprocess.call([exe], env=agent_env())

"""``status``: read-only checks that say whether anything needs updating, and what to do about it.

Each check compares two things that must agree (the game's build and the downloaded game data, the
addon source and the installed copy, the probe and the addon's interface number, ...). Nothing is
written, downloaded or started. Each problem comes with what the player can do about it.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from .agent import find_claude
from .assets import read_text
from .checker import check_spec
from .errors import MacroForgeError
from .fetch import installed_build
from .install import slot_dirs
from .luacheck import check_script_body
from .probe import load_known_api
from .settings import Settings
from .spec import read_spec
from .startup import startup_file
from .suggest import load_suggestions

_INTERFACE = re.compile(r"^## Interface:\s*(\d+)", re.M)
_EXAMPLE = re.compile(r"### ([^\n]+)\n```\n(/run [^\n]*)\n```")
GENERATED = {"MacroForgeData.lua", "MacroForgeSuggestions.lua"}  # differ by design (a stamp)
UPDATE_OR_REPORT = ("update MacroForge (python macroforge.py update); if you already have the "
                    "newest, report it on GitHub")


@dataclass
class Check:
    """One comparison: ``ok``, what was found, and (when not ok) what to do."""

    name: str
    ok: bool
    detail: str
    action: str = ""


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def check_game_data(settings: Settings) -> Check:
    """The downloaded game tables are for the build the game is on."""
    try:
        game = installed_build(settings)
    except MacroForgeError as exc:
        return Check("game build", False, str(exc), "check wow_dir and product in config.json")
    data = _read(settings.data / "BUILD").strip()
    if data == game:
        return Check("game build", True, f"{game}; the game data matches")
    return Check("game build", False, f"the game is {game}, the downloaded data is "
                 f"{data or 'missing'}", "python macroforge.py fetch, then build and install")


def _probe(settings: Settings) -> dict:
    try:
        probe = json.loads(_read(settings.data / "probe.json") or "{}")
    except ValueError:
        return {}
    return probe if isinstance(probe, dict) and isinstance(probe.get("build"), dict) else {}


def check_probe(settings: Settings) -> Check:
    """The client was probed on this build (commands, conditionals and script functions)."""
    probe, game = _probe(settings), ""
    try:
        game = installed_build(settings)
    except MacroForgeError:
        pass
    redo = "in game: /mf probe, then /reload; then python macroforge.py report"
    if not probe:
        return Check("probe", False, "no probe results yet", redo)
    probed = f"{probe['build'].get('version')}.{probe['build'].get('build')}"
    if game and probed != game:
        return Check("probe", False, f"probed on {probed}, the game is now {game}", redo)
    return Check("probe", True, f"probed on {probed}")


def check_interface(settings: Settings) -> Check:
    """The addon's ``## Interface:`` number matches the client (else the game says out of date)."""
    toc = _probe(settings).get("build", {}).get("toc")
    addon = _INTERFACE.search(_read(settings.addon_src / "MacroForge.toc"))
    slot = _INTERFACE.search(read_text("slot.toc"))
    numbers = {int(m.group(1)) for m in (addon, slot) if m}
    if not isinstance(toc, int):
        return Check("interface number", True, f"addon {sorted(numbers)}; no probe to compare with")
    if numbers == {toc}:
        return Check("interface number", True, f"{toc} in both .toc files")
    return Check("interface number", False,
                 f"the client is {toc}, the .toc files say {sorted(numbers)}",
                 "update MacroForge (python macroforge.py update); until a new version is out, "
                 "tick 'Load out of date AddOns' in the game's AddOns list")


def check_compat_report(settings: Settings) -> Check:
    """There is a compatibility report for the probed interface number."""
    toc = _probe(settings).get("build", {}).get("toc")
    if not isinstance(toc, int):
        return Check("compat report", True, "no probe yet")
    report = settings.root / "reference" / f"compat-{toc}.md"
    if report.is_file():
        return Check("compat report", True, report.name)
    return Check("compat report", False, f"{report.name} is missing", "python macroforge.py report")


def check_addon_installed(settings: Settings) -> Check:
    """The addon in the game folder is the same as the source (generated data files aside)."""
    installed = settings.addons / "MacroForge"
    if not installed.is_dir():
        return Check("addon", False, "not installed", "python macroforge.py build, then install")
    stale = [source.name for source in sorted(settings.addon_src.iterdir())
             if source.is_file() and source.name not in GENERATED
             and _bytes(installed / source.name) != _bytes(source)]
    if stale:
        return Check("addon", False, f"installed copy differs: {', '.join(stale)}",
                     "python macroforge.py build, then install; then /reload in game")
    return Check("addon", True, "the installed copy matches the source")


def _bytes(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except OSError:
        return None


def check_slots(settings: Settings) -> Check:
    """Load-on-demand slot addons exist (needed for /mf live and in-game requests)."""
    count = len(slot_dirs(settings)) if settings.addons.is_dir() else 0
    if count:
        return Check("slots", True, f"{count} slot addons")
    return Check("slots", False, "none", "python macroforge.py slots, then restart the game")


def check_macros(settings: Settings) -> Check:
    """``macros.json`` passes the checks against this client."""
    try:
        findings, _verified = check_spec(settings, read_spec(settings.spec))
    except MacroForgeError as exc:
        return Check("macros", False, str(exc)[:160],
                     "fix macros.json (python macroforge.py check)")
    if findings:
        return Check("macros", False, f"{len(findings)} warning(s)", "python macroforge.py check")
    return Check("macros", True, "no warnings")


def check_ideas(settings: Settings) -> Check:
    """Every shipped macro idea and script example still checks out on this client."""
    problems = load_suggestions(settings).problems
    if problems:
        return Check("ideas", False, f"{len(problems)} left out: {problems[0][:120]}",
                     UPDATE_OR_REPORT)
    return Check("ideas", True, "all ideas pass")


def check_script_examples(settings: Settings) -> Check:
    """The script examples the AI learns from use only functions this client has."""
    known = load_known_api(settings.data)
    text = _read(settings.root / "reference" / "script-examples.md")
    failed = []
    for title, body in _EXAMPLE.findall(text):
        try:
            check_script_body(body, known=known)
        except MacroForgeError as exc:
            failed.append(f"{title}: {exc}")
    if failed:
        return Check("script examples", False, f"{len(failed)} fail: {failed[0][:120]}",
                     UPDATE_OR_REPORT)
    scope = "this client's functions" if known else "the rules (no probe function list)"
    return Check("script examples", True, f"all pass against {scope}")


def check_watcher(settings: Settings) -> Check:
    """The watcher (in-game requests) is running and was started after the code last changed."""
    from .watcher import pid_running
    uses_requests = startup_file_exists()
    pid_file = settings.data / "watcher.pid"
    try:
        pid = int(_read(pid_file).strip() or "0")
    except ValueError:
        pid = 0
    if not pid or not pid_running(pid):
        if uses_requests:
            return Check("watcher", False, "not running", "python macroforge.py startup --now")
        return Check("watcher", True, "not set up (only needed for in-game requests)")
    started = pid_file.stat().st_mtime
    code = [p for p in (settings.root / "mforge").rglob("*") if p.is_file() and p.suffix in (".py",
            ".md", ".toml", ".toc") and p.stat().st_mtime > started]
    if code:
        return Check("watcher", False, f"running (pid {pid}) but older than {code[0].name}",
                     "stop it (Task Manager > pythonw.exe), then "
                     "python macroforge.py startup --now")
    return Check("watcher", True, f"running (pid {pid}), up to date")


def startup_file_exists() -> bool:
    """True if the login launcher is installed (the player uses in-game requests)."""
    try:
        return startup_file().is_file()
    except MacroForgeError:
        return False


def check_ai(settings: Settings) -> Check:
    """The AI the watcher uses can be found (no network is used)."""
    if settings.agent_backend == "openai":
        return Check("AI", True, f"chat endpoint {settings.llm_base_url} (model "
                     f"{settings.agent_model or 'not set'}); test it with llm-test")
    try:
        return Check("AI", True, f"Claude CLI at {Path(find_claude(settings)).name}")
    except MacroForgeError as exc:
        if not startup_file_exists():
            return Check("AI", True, "Claude CLI not found (only needed for in-game requests)")
        return Check("AI", False, str(exc)[:160], "install the Claude CLI, then macroforge login")


def check_client_folder(settings: Settings) -> Check:
    """The configured client folder exists; other client folders are listed (a new game flavor?)."""
    if not settings.wow_dir.is_dir():
        return Check("client folder", False, f"{settings.wow_dir} does not exist",
                     "run setup (python macroforge.py setup) to pick the client folder")
    root = settings.wow_dir.parent
    others = sorted(p.name for p in root.iterdir()
                    if p.is_dir() and p.name.startswith("_") and p.name.endswith("_")
                    and p != settings.wow_dir and (p / "Interface").is_dir())
    detail = settings.wow_dir.name + (f"; also found {', '.join(others)}" if others else "")
    return Check("client folder", True, detail)


CHECKS = (check_client_folder, check_game_data, check_probe, check_interface, check_compat_report,
          check_addon_installed, check_slots, check_macros, check_ideas, check_script_examples,
          check_ai, check_watcher)


def gather(settings: Settings) -> list[Check]:
    """Run every check; one that fails unexpectedly is reported, not raised."""
    results = []
    for check in CHECKS:
        try:
            results.append(check(settings))
        except (MacroForgeError, OSError) as exc:
            results.append(Check(check.__name__.removeprefix("check_").replace("_", " "), False,
                                 f"could not check: {str(exc)[:120]}"))
    return results


def render(results: list[Check]) -> str:
    """The report printed by ``status``: one line per check, with the action under it."""
    lines = []
    for result in results:
        lines.append(f"{'OK    ' if result.ok else 'ACTION'}  {result.name}: {result.detail}")
        if not result.ok and result.action:
            lines.append(f"        -> {result.action}")
    todo = sum(not r.ok for r in results)
    lines.append("Everything is up to date." if not todo else f"{todo} thing(s) to do.")
    return "\n".join(lines)

"""The request watcher: answers in-game ``/mf ask`` requests.

Two ways in: the addon draws the request as a data strip and takes a screenshot (no reload; see
``shots.py``), or it saves the request to SavedVariables and the UI reloads. The watcher then runs
the agent in a sandbox, validates the macros it produced, rebuilds and installs, and ships a reply
that the addon picks up from a spare slot (or the next ``/mf live``).
"""
from __future__ import annotations

import csv
import json
import logging
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import agent as agent_mod
from . import llm
from .assets import load_toml
from .checker import check_spec
from .errors import MacroForgeError, ValidationError
from .fsutil import atomic_write_bytes, atomic_write_text, system_exe
from .install import build_addon, install
from .savedvars import load_sv
from .settings import Settings
from .shots import GIVE_UP_SECONDS, ShotScanner, cleanup, read_request, retry_stuck
from .spec import (changed_macros, check_removals, describe_changes, diff_specs, load_spec,
                   read_spec, resolve_spec)
from .validate import Request, check_blocked, clean_macro, parse_request, sanitize_reply

log = logging.getLogger("mforge.watcher")

HOUR = 3600
MAX_BACKUPS = 50
MAX_HANDLED = 1000  # answered request ids remembered (the newest)
MAX_REPLIES_KEPT = 20


# -- single-instance lock ---------------------------------------------------------------------

def pid_running(pid: int) -> bool:
    """True if a process with exactly this id exists."""
    if sys.platform != "win32":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True
    out = subprocess.run([system_exe("tasklist.exe"), "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                         capture_output=True, text=True, check=False).stdout
    return any(len(row) > 1 and row[1].strip() == str(pid) for row in csv.reader(out.splitlines()))


class WatcherLock:
    """A pid file created atomically; a stale one (dead process) is taken over."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def _holder(self) -> int | None:
        try:
            return int(self.path.read_text().strip())
        except (OSError, ValueError):
            return None

    def acquire(self) -> None:
        """Take the lock or raise if a live watcher already holds it."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for _attempt in range(3):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                holder = self._holder()
                if holder is not None and holder != os.getpid() and pid_running(holder):
                    raise MacroForgeError(f"a watcher is already running (pid {holder})") from None
                self.path.unlink(missing_ok=True)  # stale: the holder is gone
                continue
            with os.fdopen(fd, "w") as handle:
                handle.write(str(os.getpid()))
            return
        raise MacroForgeError("could not take the watcher lock")

    def release(self) -> None:
        """Remove the lock file, but only if this process created it."""
        if self._holder() == os.getpid():
            self.path.unlink(missing_ok=True)

    def __enter__(self) -> "WatcherLock":
        self.acquire()
        return self

    def __exit__(self, *exc_info) -> None:
        self.release()


# -- persisted state ----------------------------------------------------------------------------

@dataclass
class WatchState:
    """Request ids already answered and the times of recent runs (for the hourly limit)."""

    path: Path
    handled: set[int] = field(default_factory=set)
    recent: list[float] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> "WatchState":
        """Read the state file; a missing or damaged file gives an empty state."""
        state = cls(Path(path))
        try:
            raw = json.loads(state.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return state
        if isinstance(raw, list):  # older format: just the ids
            raw = {"handled": raw}
        if isinstance(raw, dict):
            handled, recent = raw.get("handled"), raw.get("recent")
            if isinstance(handled, list):
                state.handled = {i for i in handled if isinstance(i, int)}
            if isinstance(recent, list):
                state.recent = [t for t in recent if isinstance(t, (int, float))]
        return state

    def save(self) -> None:
        """Write the state atomically."""
        # request ids grow with time, so the newest are the ones that can still come back
        self.handled = set(sorted(self.handled)[-MAX_HANDLED:])
        payload = {"handled": sorted(self.handled), "recent": self.recent}
        atomic_write_text(self.path, json.dumps(payload))

    def rate_limited(self, limit: int, now: float) -> bool:
        """True if ``limit`` runs already happened in the last hour."""
        self.recent = [t for t in self.recent if now - t < HOUR]
        return len(self.recent) >= limit


# -- request handling -----------------------------------------------------------------------

def load_replies(settings: Settings) -> list[dict]:
    """Replies kept in ``data/replies.json`` (ids are checked; anything odd is dropped)."""
    path = settings.data / "replies.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [r for r in raw if isinstance(r, dict) and isinstance(r.get("id"), int)
            and isinstance(r.get("text"), str)] if isinstance(raw, list) else []


def audit(settings: Settings, entry: dict) -> None:
    """Append one JSON line to ``data/watch.log`` (request text is already length-limited)."""
    settings.data.mkdir(exist_ok=True)
    entry = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), **entry}
    with open(settings.data / "watch.log", "a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry) + "\n")


def backup_spec(settings: Settings) -> Path:
    """Copy ``macros.json`` to ``data/backups/`` (keeps the newest few)."""
    folder = settings.data / "backups"
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / f"macros-{time.strftime('%Y%m%d-%H%M%S')}.json"
    atomic_write_bytes(dest, settings.spec.read_bytes())
    for old in sorted(folder.glob("macros-*.json"))[:-MAX_BACKUPS]:
        old.unlink(missing_ok=True)
    return dest


def remember_class(settings: Settings, req: Request) -> None:
    """Record the requester's class in ``macros.json`` so ``check`` can verify spell names."""
    spec = read_spec(settings.spec)
    classes = spec.setdefault("classes", {})
    if classes.get(req.char) != req.cls:
        classes[req.char] = req.cls
        atomic_write_text(settings.spec, json.dumps(spec, indent=2) + "\n")


def _evaluate(settings: Settings, candidate_text: str,
              mode: str = "macro") -> tuple[dict, int, str]:
    """Validate the agent's macros.json against the current one.

    Returns the parsed spec, the number of soft warnings (unknown spell names) and a one-line
    description of what changed that is computed here, not taken from the model's reply.
    """
    try:
        candidate = json.loads(candidate_text)
    except (ValueError, RecursionError) as exc:
        raise ValidationError(f"macros.json is not valid JSON: {str(exc)[:80]}") from None
    if not isinstance(candidate, dict) or not isinstance(candidate.get("macros"), list):
        raise ValidationError("macros.json must be an object with a 'macros' list")
    current = read_spec(settings.spec)
    check_removals(current, candidate)
    for entry in changed_macros(current, candidate):  # a request may only make what it asked for
        is_script = entry.get("kind") == "script"
        if mode == "script" and not is_script:
            raise ValidationError("a script request may only add or change scripts "
                                  '(entries with "kind": "script")')
        if mode != "script" and is_script:
            raise ValidationError("scripts can only be made with /mf script")
    changed = changed_macros(current, candidate)
    resolve_spec(candidate, settings)  # everything must be valid; the agent rules follow
    for entry in changed:  # the agent-only rules apply to what it changed, not to your own macros
        if entry.get("kind") != "script":
            name, body = clean_macro(entry.get("name"), entry.get("body"))
            check_blocked(name, body, settings.blocked_commands)
    findings, _verified = check_spec(settings, candidate, agent_mode=True)
    mine = {entry.get("name") for entry in changed}
    findings = [(name, f) for name, f in findings if name in mine]
    hard = [f"[{name}] {f.text}" for name, f in findings if f.kind != "spell"]
    if hard:
        raise ValidationError("; ".join(hard)[:250])
    return candidate, len(findings), describe_changes(diff_specs(current, candidate))


ACK_TEXT = "Got your request, working on it..."


def publish_ack(settings: Settings, req_id: int) -> None:
    """Tell the game we have the request (a ``pending`` reply: it does not count as the answer)."""
    replies = [r for r in load_replies(settings) if not r.get("pending")]
    replies.append({"id": int(time.time() * 100), "req": req_id, "text": ACK_TEXT,
                    "pending": True})
    try:
        atomic_write_text(settings.data / "replies.json", json.dumps(replies, indent=1))
        build_addon(settings, load_spec(settings), replies)
        install(settings)
    except (MacroForgeError, OSError) as exc:
        log.warning("could not send the acknowledgement: %s", exc)


def publish(settings: Settings, req_id: int, reply: str) -> str:
    """Store the reply, rebuild and install so it ships with the data. Returns the final reply."""
    replies = [r for r in load_replies(settings) if not r.get("pending")]
    entry = {"id": int(time.time() * 100), "req": req_id, "text": sanitize_reply(reply)}
    replies.append(entry)
    del replies[:-MAX_REPLIES_KEPT]
    path = settings.data / "replies.json"
    atomic_write_text(path, json.dumps(replies, indent=1))
    try:
        build_addon(settings, load_spec(settings), replies)
        install(settings)
    except (MacroForgeError, OSError) as exc:
        entry["text"] = sanitize_reply(f"{reply} [build failed: {str(exc)[:120]}]")
        atomic_write_text(path, json.dumps(replies, indent=1))
    return entry["text"]


def default_runner(settings: Settings) -> Callable:
    """The function that turns a request into macros: the Claude CLI or an OpenAI-style endpoint."""
    if settings.agent_backend == "openai":
        def run(settings: Settings, req: Request, export: dict | None):
            """Ask the chat model; its answer is checked for the kind of request it answers."""
            return llm.run_chat(settings, req, export,
                                check=lambda text: _evaluate(settings, text, req.mode))
        return run
    return agent_mod.run_agent


def handle_request(settings: Settings, raw: object, export: dict | None, state: WatchState,
                   runner: Callable | None = None,
                   now: Callable[[], float] = time.time) -> str | None:
    """Answer one request. Returns the reply sent to the game, or None if it was ignored."""
    allowed = frozenset(load_toml("classes.toml")["classes"])
    try:
        req = parse_request(raw, allowed, settings.max_request_chars)
    except ValidationError as exc:
        req_id = raw.get("id") if isinstance(raw, dict) else None
        log.warning("invalid request ignored: %s", exc)
        audit(settings, {"req": req_id if isinstance(req_id, int) else None, "rejected": str(exc),
                         "text": str(raw.get("text", ""))[:500] if isinstance(raw, dict) else ""})
        if isinstance(req_id, int) and not isinstance(req_id, bool) and req_id > 0:
            return publish(settings, req_id, f"Request rejected: {exc}")
        return None
    log.info("request %s from %s: %r", req.id, req.char, req.text[:100])
    started = now()
    usage: dict = {}
    changed = False
    backup_name = ""
    if state.rate_limited(settings.max_requests_per_hour, started):
        reply = f"Rate limit: {settings.max_requests_per_hour} requests per hour. Try again later."
    else:
        state.recent.append(started)
        state.save()
        publish_ack(settings, req.id)
        try:
            remember_class(settings, req)
            backup_name = backup_spec(settings).name
            result = (runner or default_runner(settings))(settings, req, export)
            usage = result.usage
            reply = agent_mod.extract_reply(result.text)
            candidate, warnings, summary = _evaluate(settings, result.spec_text, req.mode)
            if candidate != read_spec(settings.spec):
                atomic_write_text(settings.spec, result.spec_text)
                changed = True
            reply = f"[{summary}] {reply}"
            if warnings:
                reply += f" (check: {warnings} warning(s), see PC console)"
        except agent_mod.AgentTimeout:
            reply = f"Timed out after {settings.agent_timeout_seconds}s; no macros changed."
        except (MacroForgeError, OSError) as exc:
            reply = f"Rejected, nothing changed: {str(exc)[:250]}"
        except Exception:  # noqa: BLE001 - whatever went wrong, the game still gets an answer
            log.exception("request %s failed", req.id)
            reply = "Something went wrong on the PC; nothing was changed (see data/watcher.log)."
    reply = publish(settings, req.id, reply)
    audit(settings, {"req": req.id, "char": req.char, "text": req.text, "reply": reply,
                     "changed": changed, "backup": backup_name, **usage})
    log.info("reply: %s", reply)
    return reply


def _mtime(path: Path) -> float | None:
    """Modification time of a file, or None if it is missing."""
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def _name_parts(name: object) -> list[str]:
    """The words of a character name (``Testy-Mage`` and ``testy mage`` give the same words)."""
    return re.findall(r"[a-z0-9]+", str(name).lower())


def find_export(settings: Settings, char: str = "") -> dict | None:
    """The exported spellbook of this character, searching every account's saved data.

    Falls back to the most recently used account's export when no export names the character.
    """
    exports = []
    for path in settings.saved_vars_files:
        try:
            data = load_sv(path, "MacroForgeExport")
        except (ValidationError, OSError):
            continue
        if isinstance(data, dict):
            exports.append(data)
    wanted = _name_parts(char)
    for data in exports:
        first = _name_parts(data.get("char", ""))
        if wanted and first and wanted[:len(first)] == first:
            return data
    return exports[0] if exports else None


def process_new_requests(settings: Settings, state: WatchState,
                         runner: Callable | None = None,
                         now: Callable[[], float] = time.time) -> int:
    """Handle every unanswered request in the saved variables of every account. Returns how many."""
    handled = 0
    for path in settings.saved_vars_files:
        try:
            requests = load_sv(path, "MacroForgeRequests")
            export = load_sv(path, "MacroForgeExport")
        except (ValidationError, OSError) as exc:
            log.warning("cannot read saved variables: %s", exc)
            continue
        if isinstance(requests, dict):
            requests = list(requests.values())
        if not isinstance(requests, list):
            continue
        for raw in requests:
            req_id = raw.get("id") if isinstance(raw, dict) else None
            if not isinstance(req_id, int) or isinstance(req_id, bool) or req_id in state.handled:
                continue
            try:
                export_data = export if isinstance(export, dict) else None
                handle_request(settings, raw, export_data, state, runner, now)
                handled += 1
            except Exception:  # the watcher must survive anything a single request does
                log.exception("request %s failed", req_id)
            finally:
                state.handled.add(req_id)
                state.save()
    return handled


def process_new_shots(settings: Settings, state: WatchState, scanner: ShotScanner,
                      runner: Callable | None = None,
                      now: Callable[[], float] = time.time) -> int:
    """Handle requests that arrived as screenshots. Returns how many ran."""
    handled = 0
    retry_stuck(scanner)
    for path in scanner.fresh_files(now()):
        try:
            mtime = path.stat().st_mtime
            raw = read_request(path)
        except (ValidationError, OSError) as exc:
            if now() - _mtime_or_now(path, now) > GIVE_UP_SECONDS:
                scanner.seen[path.name] = _mtime_or_now(path, now)  # not one of ours
                log.debug("skipping %s: %s", path.name, exc)
            continue
        scanner.seen[path.name] = mtime
        req_id = raw["id"]
        if req_id in state.handled:
            continue
        try:
            handle_request(settings, raw, find_export(settings, raw["char"]), state, runner, now)
            handled += 1
        except Exception:  # the watcher must survive anything a single request does
            log.exception("request %s failed", req_id)
        finally:
            state.handled.add(req_id)
            state.save()
            if settings.delete_request_shots:
                for left in cleanup(path):
                    log.warning("could not delete %s yet; will retry", left.name)
                    scanner.stuck[left] = 0
    return handled


def _mtime_or_now(path: Path, now: Callable[[], float]) -> float:
    """Modification time of a file, or the current time if it vanished."""
    try:
        return path.stat().st_mtime
    except OSError:
        return now()


def configure_logging(settings: Settings) -> None:
    """Log to the console, or to ``data/watcher.log`` when started hidden (no console)."""
    root = logging.getLogger("mforge")
    if root.handlers:
        return
    if sys.stdout is None:
        settings.data.mkdir(exist_ok=True)
        handler: logging.Handler = logging.FileHandler(settings.data / "watcher.log",
                                                       encoding="utf-8")
    else:
        handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%H:%M:%S"))
    root.addHandler(handler)
    root.setLevel(logging.INFO)


def run_watcher(settings: Settings, runner: Callable | None = None,
                sleep: Callable[[float], None] = time.sleep, now: Callable[[], float] = time.time,
                stop: Callable[[], bool] = lambda: False, poll_seconds: float = 2.0,
                settle_seconds: float = 1.5) -> None:
    """Watch the saved-variables file and answer requests until ``stop()`` is true."""
    configure_logging(settings)
    settings.data.mkdir(exist_ok=True)
    runner = runner or default_runner(settings)
    if settings.agent_backend == "openai":
        llm.require_ready(settings)
        log.info("AI: %s at %s", settings.agent_model, settings.llm_base_url)
    with WatcherLock(settings.data / "watcher.pid"):
        state = WatchState.load(settings.data / "watch_state.json")
        log.info("watching the saved data of %d account folder(s): %s",
                 len(settings.saved_vars_files), ", ".join(d.name for d in settings.account_dirs))
        log.info("In game: /mf ask <request>   (Ctrl+C to stop)")
        last = None
        scanner = ShotScanner(settings.wow_dir / "Screenshots", started=now())
        while not stop():
            try:
                process_new_shots(settings, state, scanner, runner, now)
            except Exception:  # nothing a picture contains may stop the watcher
                log.exception("could not process screenshots")
            mtime = tuple(_mtime(path) for path in settings.saved_vars_files)
            if mtime != last:
                last = mtime
                sleep(settle_seconds)  # let the game finish writing
                try:
                    process_new_requests(settings, state, runner, now)
                except Exception:  # nothing a file contains may stop the watcher
                    log.exception("could not process the saved variables")
            sleep(poll_seconds)

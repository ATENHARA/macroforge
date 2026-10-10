"""Answer a macro request with any OpenAI-style chat endpoint (GPT, Ollama, LM Studio, ...).

Unlike the Claude CLI path, a chat model has no file tools. The rules and the player's current
macros go into the prompt, and the model returns a JSON change-set that is merged into
``macros.json`` here. The result has the same shape as an agent run, so the watcher validates it
exactly the same way.

The model can only return text. Its answer is parsed, merged and then checked by the caller
(``check``) before anything is installed.
"""
from __future__ import annotations

import copy
import json
import logging
import os
import re
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from string import Template
from typing import Callable
from urllib.parse import urlparse

from . import keystore
from .agent import AgentResult, AgentTimeout, build_prompt, ensure_pet_table, read_context
from .assets import read_text
from .errors import AgentError, ConfigError, ValidationError
from .settings import LOOPBACK_HOSTS, Settings
from .spec import read_spec
from .validate import Request, norm

log = logging.getLogger(__name__)

MAX_RESPONSE_BYTES = 1_000_000
MAX_UPSERTS = 8
MAX_FEEDBACK = 300  # characters of our own error text sent back to the model
_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S | re.I)
_KEYLIKE = re.compile(r"\b(?:sk|key|tok)-[A-Za-z0-9_\-]{12,}")


class ChatHttpError(AgentError):
    """The server answered with an HTTP error."""

    def __init__(self, message: str, code: int = 0, detail: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.detail = detail


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    """Never follow a redirect: the key must only go to the address in the config."""

    def redirect_request(self, *args, **kwargs):
        """Decline every redirect (the caller then sees the 3xx as an error)."""
        return None


# -- key and URL ---------------------------------------------------------------------------------

def is_loopback(settings: Settings) -> bool:
    """True if the endpoint is a server on this PC."""
    return (urlparse(settings.llm_base_url).hostname or "").lower() in LOOPBACK_HOSTS


def api_key(settings: Settings) -> str:
    """The API key: the environment variable named in the config, else Windows Credential Manager.

    The key is never read from ``config.json``.
    """
    name = settings.llm_api_key_env
    key = os.environ.get(name, "").strip()
    if not key:
        try:
            key = keystore.load(name)
        except keystore.KeystoreError as exc:
            log.warning("cannot read the stored API key: %s", exc)
    if not key and not is_loopback(settings):
        raise AgentError("no API key found: run `python macroforge.py setup` to save one, or set "
                         f"the environment variable {name}, then restart the watcher")
    return key


def redact(text: str, key: str) -> str:
    """Remove the API key (and anything shaped like one) from text that may be shown or logged."""
    if len(key) >= 6:
        text = text.replace(key, "***")
    return _KEYLIKE.sub("***", text)


def require_ready(settings: Settings) -> None:
    """Raise a clear error if the chat backend is not configured well enough to run."""
    if not settings.agent_model:
        raise ConfigError("set agent_model in config.json to the model name "
                          "(for example the name `ollama list` shows)")


# -- prompt --------------------------------------------------------------------------------------

def system_message(settings: Settings) -> str:
    """The rules for a chat model: ``prompt_chat.md`` followed by the context file."""
    blocked = ", ".join("/" + c for c in sorted(settings.blocked_commands))
    rules = Template(read_text("prompt_chat.md")).safe_substitute(blocked=blocked)
    return rules + "\n" + read_context(settings)


def _relevant_macros(settings: Settings, req: Request) -> list[dict]:
    """The player's macros for this character plus the account-wide ones."""
    try:
        macros = read_spec(settings.spec)["macros"]
    except ValidationError:
        return []
    mine = norm(req.char)
    keep = ("name", "char", "icon", "body")
    return [{k: m[k] for k in keep if k in m} for m in macros if isinstance(m, dict)
            and (not m.get("char") or norm(m.get("char")) == mine)]


def _relevant_pets(settings: Settings, req: Request, export: dict | None) -> dict[str, list[str]]:
    """Pet families worth showing: the current pet's, and any named by an ability in the request."""
    table_path = ensure_pet_table(settings)
    if not table_path:
        return {}
    try:
        table = json.loads(table_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    pet = export.get("pet") if isinstance(export, dict) else None
    current = str(pet.get("family")) if isinstance(pet, dict) else ""
    wanted = req.text.lower()
    return {family: abilities for family, abilities in table.items()
            if family == current or any(a.lower() in wanted for a in abilities)}


def build_messages(settings: Settings, req: Request, export: dict | None) -> list[dict]:
    """The chat messages for one request."""
    user = build_prompt(req, export)
    user += "\nCurrent macros (JSON):\n" + json.dumps(_relevant_macros(settings, req), indent=1)
    pets = _relevant_pets(settings, req, export)
    if pets:
        user += "\n\nPet abilities by family (JSON):\n" + json.dumps(pets)
    return [{"role": "system", "content": system_message(settings)},
            {"role": "user", "content": user}]


# -- the answer: parse, merge ------------------------------------------------------------------

def parse_change(content: str) -> dict:
    """The JSON object in the model's answer (code fences and chatter around it are tolerated)."""
    text = content.strip()
    fenced = _FENCE.search(text)
    if fenced:
        text = fenced.group(1).strip()
    start = text.find("{")
    if start < 0:
        raise ValidationError("the answer contains no JSON object")
    try:
        change, _end = json.JSONDecoder().raw_decode(text[start:])
    except ValueError as exc:
        raise ValidationError(f"the answer is not valid JSON ({exc})") from None
    if not isinstance(change, dict):
        raise ValidationError("the answer must be a JSON object")
    upsert = change.get("upsert", change.get("macros", []))
    remove = change.get("remove", [])
    if not isinstance(upsert, list) or not all(isinstance(m, dict) for m in upsert):
        raise ValidationError('"upsert" must be a list of macro objects')
    if not isinstance(remove, list) or not all(isinstance(m, dict) for m in remove):
        raise ValidationError('"remove" must be a list of {"name", "char"} objects')
    if len(upsert) > MAX_UPSERTS:
        raise ValidationError(f"too many macros in one answer (at most {MAX_UPSERTS})")
    reply = change.get("reply", "")
    return {"upsert": upsert, "remove": remove, "reply": reply if isinstance(reply, str) else ""}


def _key(entry: dict) -> tuple[str, str]:
    return str(entry.get("name", "")).strip().lower(), norm(entry.get("char", ""))


def merge_change(current: dict, change: dict) -> str:
    """``macros.json`` text with the change-set applied (same name and character = replaced)."""
    spec = copy.deepcopy(current)
    macros = spec["macros"]
    for entry in change["upsert"]:
        if not isinstance(entry.get("name"), str) or not isinstance(entry.get("body"), str):
            raise ValidationError("every macro needs a text name and a text body")
        clean = {k: entry[k] for k in ("name", "char", "icon", "body") if k in entry}
        for index, existing in enumerate(macros):
            if isinstance(existing, dict) and _key(existing) == _key(clean):
                macros[index] = clean
                break
        else:
            macros.append(clean)
    removals = spec.setdefault("remove", [])
    for entry in change["remove"]:
        if not isinstance(entry.get("name"), str):
            raise ValidationError('every "remove" entry needs a text name')
        clean = {k: entry[k] for k in ("name", "char") if k in entry}
        if not any(isinstance(r, dict) and _key(r) == _key(clean) for r in removals):
            removals.append(clean)
    return json.dumps(spec, indent=2) + "\n"


# -- the HTTP call -------------------------------------------------------------------------------

def _error_detail(exc: urllib.error.HTTPError, key: str) -> str:
    try:
        raw = exc.read(2000).decode("utf-8", "replace")
    except OSError:
        return ""
    try:
        data = json.loads(raw)
        message = data.get("error", data) if isinstance(data, dict) else raw
        raw = message.get("message", message) if isinstance(message, dict) else message
    except ValueError:
        pass
    return redact(re.sub(r"\s+", " ", str(raw)).strip(), key)[:200]


def _http_error(exc: urllib.error.HTTPError, key: str, settings: Settings) -> ChatHttpError:
    detail = _error_detail(exc, key)
    code = exc.code
    if code in (401, 403):
        text = (f"the AI service rejected the API key (HTTP {code}); check the value of "
                f"{settings.llm_api_key_env}")
    elif code == 404:
        text = (f"not found (HTTP 404); check llm_base_url and that the model "
                f"{settings.agent_model!r} exists")
    elif code == 429:
        text = "the AI service is rate limiting you or your quota is used up (HTTP 429)"
    elif 300 <= code < 400:
        text = "the server redirected the request; set llm_base_url to the final address"
    else:
        text = f"the AI service returned HTTP {code}"
    return ChatHttpError(f"{text}: {detail}" if detail else text, code, detail)


def post_chat(settings: Settings, payload: dict, key: str) -> dict:
    """POST one chat completion request and return the parsed JSON response."""
    url = settings.llm_base_url.rstrip("/") + "/chat/completions"
    headers = {"Content-Type": "application/json", "User-Agent": "macroforge"}
    if key:
        headers["Authorization"] = "Bearer " + key
    request = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                     headers=headers, method="POST")
    opener = urllib.request.build_opener(_NoRedirects())
    try:
        with opener.open(request, timeout=settings.agent_timeout_seconds) as response:
            body = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise _http_error(exc, key, settings) from None
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, (socket.timeout, TimeoutError)):
            raise AgentTimeout(f"no answer within {settings.agent_timeout_seconds}s") from None
        hint = " (is the model server running?)" if is_loopback(settings) else ""
        raise AgentError(f"cannot reach {settings.llm_base_url}{hint}: "
                         f"{redact(str(exc.reason), key)[:120]}") from None
    except (socket.timeout, TimeoutError):
        raise AgentTimeout(f"no answer within {settings.agent_timeout_seconds}s") from None
    except OSError as exc:
        raise AgentError(f"network error: {redact(str(exc), key)[:120]}") from None
    if len(body) > MAX_RESPONSE_BYTES:
        raise AgentError("the AI's response is too large")
    try:
        parsed = json.loads(body)
    except ValueError:
        raise AgentError("the AI service did not return JSON; is llm_base_url right?") from None
    if not isinstance(parsed, dict):
        raise AgentError("the AI service returned an unexpected response")
    return parsed


def chat_once(settings: Settings, messages: list[dict], key: str,
              post: Callable = post_chat, json_mode: bool | None = None) -> tuple[str, dict]:
    """One request; returns the answer text and the response (for usage). Retries without
    ``response_format`` once if the server rejects it."""
    use_json = settings.llm_json_mode if json_mode is None else json_mode
    payload = {"model": settings.agent_model, "messages": messages, "temperature": 0.2,
               "max_tokens": settings.llm_max_tokens}
    if use_json:
        payload["response_format"] = {"type": "json_object"}
    try:
        response = post(settings, payload, key)
    except ChatHttpError as exc:
        if use_json and exc.code == 400 and ("response_format" in exc.detail
                                              or "json" in exc.detail.lower()):
            return chat_once(settings, messages, key, post, json_mode=False)
        raise
    try:
        content = response["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise AgentError("the AI service answered in an unexpected format") from None
    if not isinstance(content, str) or not content.strip():
        raise AgentError("the AI service returned an empty answer")
    return content, response


# -- the runner the watcher uses ---------------------------------------------------------------

@dataclass
class _Totals:
    prompt: int = 0
    completion: int = 0
    model: str = ""

    def add(self, response: dict) -> None:
        """Add the token counts and model name of one response."""
        usage = response.get("usage") if isinstance(response.get("usage"), dict) else {}
        self.prompt += int(usage.get("prompt_tokens") or 0)
        self.completion += int(usage.get("completion_tokens") or 0)
        self.model = str(response.get("model") or self.model)[:60]


def run_chat(settings: Settings, req: Request, export: dict | None,
             check: Callable[[str], object] | None = None,
             post: Callable = post_chat) -> AgentResult:
    """Ask the chat model, merge its change-set into ``macros.json``, and return the result.

    ``check`` (the caller's validator) gets the merged ``macros.json`` text; if it, or parsing,
    rejects the answer the model is told why and may try again (``llm_retries`` times).
    """
    require_ready(settings)
    key = api_key(settings)
    messages = build_messages(settings, req, export)
    current = read_spec(settings.spec)
    totals = _Totals()
    started = time.monotonic()
    problem = ""
    for attempt in range(settings.llm_retries + 1):
        content, response = chat_once(settings, messages, key, post)
        totals.add(response)
        try:
            change = parse_change(content)
            spec_text = merge_change(current, change)
            if check is not None:
                check(spec_text)
        except ValidationError as exc:
            problem = redact(str(exc), key)[:MAX_FEEDBACK]
            messages = messages + [
                {"role": "assistant", "content": content[:4000]},
                {"role": "user", "content": f"That answer was rejected: {problem} Fix it and "
                                            "answer again with only the JSON object."}]
            continue
        usage = {"usage": {"prompt_tokens": totals.prompt, "completion_tokens": totals.completion},
                 "cost_usd": None, "turns": attempt + 1,
                 "models": [totals.model or settings.agent_model[:60]],
                 "seconds": round(time.monotonic() - started, 1)}
        return AgentResult(text="REPLY: " + change["reply"], spec_text=spec_text, usage=usage)
    raise AgentError(f"the AI's answer was rejected: {problem}")


def check_connection(settings: Settings, post: Callable = post_chat) -> str:
    """A tiny request to prove the URL, model and key work; returns a one-line report."""
    require_ready(settings)
    started = time.monotonic()
    messages = [{"role": "user", "content": "Reply with the single word ok."}]
    content, response = chat_once(settings, messages, api_key(settings), post, json_mode=False)
    model = str(response.get("model") or settings.agent_model)[:60]
    return f"{model} answered in {time.monotonic() - started:.1f}s: {content.strip()[:40]!r}"

"""The OpenAI-style chat backend, against a fake server on this PC (no real provider)."""
from __future__ import annotations

import dataclasses
import json
import os
import shutil
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

from mforge import agent, llm, watcher
from mforge.errors import AgentError, ConfigError
from mforge.settings import Settings, validate_llm_url
from mforge.spec import read_spec
from tests.test_agent_watcher import NEW_MACRO, WatcherCase, request
from tests.test_pets import write_tables

KEY = "test-key-0123456789abcdef"


def completion(content: str, model: str = "test-model") -> dict:
    """A minimal /chat/completions response."""
    return {"model": model, "choices": [{"message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 120, "completion_tokens": 30}}


def answer(upsert=None, remove=None, reply="Added it.") -> str:
    """The JSON the model is asked to return."""
    return json.dumps({"upsert": upsert if upsert is not None else [NEW_MACRO],
                       "remove": remove or [], "reply": reply})


class FakeLLM:
    """A tiny chat server: queue answers with ``reply``/``respond``, read what it received."""

    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.queue: list[tuple] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args) -> None:
                pass

            def do_POST(self) -> None:
                size = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(size) or b"{}")
                outer.requests.append({"path": self.path, "headers": dict(self.headers), "body": body})
                status, payload, headers, delay = outer.queue.pop(0) if outer.queue else (
                    200, completion(answer()), {}, 0)
                time.sleep(delay)
                data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
                try:
                    self.send_response(status)
                    for name, value in headers.items():
                        self.send_header(name, value)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                except OSError:
                    pass  # the client gave up (timeout test)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/v1"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def reply(self, content: str) -> None:
        self.queue.append((200, completion(content), {}, 0))

    def respond(self, status: int, payload, headers=None, delay: float = 0) -> None:
        self.queue.append((status, payload, headers or {}, delay))

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


class LlmCase(WatcherCase):
    def setUp(self):
        super().setUp()
        self.server = FakeLLM()
        self.addCleanup(self.server.close)
        env = mock.patch.dict(os.environ, {"MACROFORGE_API_KEY": KEY})
        env.start()
        self.addCleanup(env.stop)
        self.cfg = dataclasses.replace(self.settings, agent_backend="openai",
                                       agent_model="test-model", llm_base_url=self.server.url)

    def run_request(self, raw=None, settings=None):
        return watcher.handle_request(settings or self.cfg, raw or request(), None, self.state)

    def logs_text(self) -> str:
        parts = []
        for name in ("watch.log", "replies.json", "watcher.log"):
            path = self.sb.project / "data" / name
            if path.exists():
                parts.append(path.read_text(encoding="utf-8"))
        parts.append((self.sb.project / "macros.json").read_text(encoding="utf-8"))
        return "\n".join(parts)


class HappyPathTests(LlmCase):
    def test_a_request_adds_a_macro_end_to_end(self):
        self.server.reply(answer())
        reply = self.run_request()
        self.assertEqual(reply, "[Added: Corr] Added it.")
        self.assertIn("Corr", self.spec_names())
        sent = self.server.requests[0]
        self.assertEqual(sent["path"], "/v1/chat/completions")
        self.assertEqual(sent["headers"]["Authorization"], "Bearer " + KEY)
        self.assertEqual(sent["body"]["model"], "test-model")
        self.assertEqual(sent["body"]["response_format"], {"type": "json_object"})
        self.assertEqual([m["role"] for m in sent["body"]["messages"]], ["system", "user"])
        last = json.loads((self.sb.project / "data" / "watch.log").read_text().splitlines()[-1])
        self.assertEqual(last["usage"], {"prompt_tokens": 120, "completion_tokens": 30})
        self.assertEqual(last["models"], ["test-model"])

    def test_the_key_never_reaches_logs_replies_or_macros(self):
        self.server.reply(answer())
        self.run_request()
        self.assertNotIn(KEY, self.logs_text())
        self.assertNotIn(KEY, json.dumps(self.server.requests[0]["body"]))

    def test_json_wrapped_in_chatter_or_a_code_fence_is_accepted(self):
        self.server.reply("Sure! Here you go:\n```json\n" + answer() + "\n```\nHope that helps.")
        self.assertEqual(self.run_request(), "[Added: Corr] Added it.")

    def test_an_impossible_request_changes_nothing(self):
        before = self.spec_text()
        self.server.reply(answer(upsert=[], reply="There is no mana percent condition."))
        reply = self.run_request()
        self.assertIn("no mana percent", reply)
        self.assertEqual(self.spec_text(), before)

    def test_the_same_name_and_character_is_replaced_not_duplicated(self):
        edited = dict(NEW_MACRO, body="#showtooltip Fear\n/cast [@mouseover,harm,nodead] Fear")
        self.server.reply(answer(upsert=[NEW_MACRO]))
        self.server.reply(answer(upsert=[dict(edited, char="test  CHAR")], reply="Changed it."))
        self.run_request(request(rid=1))
        self.run_request(request(rid=2))
        corr = [m for m in read_spec(self.sb.project / "macros.json")["macros"] if m["name"] == "Corr"]
        self.assertEqual(len(corr), 1)
        self.assertIn("@mouseover", corr[0]["body"])

    def test_several_macros_in_one_request_are_all_added(self):
        three = [dict(NEW_MACRO, name=name) for name in ("Corr A", "Corr B", "Corr C")]
        self.server.reply(answer(upsert=three, reply="Added three macros."))
        self.assertEqual(self.run_request(), "[Added: Corr A, Corr B, Corr C] Added three macros.")
        self.assertTrue({"Corr A", "Corr B", "Corr C"} <= set(self.spec_names()))
        self.assertEqual(len(self.server.requests), 1)  # one question, one answer

    def test_one_request_can_add_and_edit_at_the_same_time(self):
        new = dict(NEW_MACRO, name="Fresh")
        edited = {"name": "Fear MO", "char": "Test Char", "icon": "spell:Fear",
                  "body": "#showtooltip Fear\n/cast [@focus,harm,nodead] Fear"}
        self.server.reply(answer(upsert=[new, edited], reply="Did both."))
        self.assertEqual(self.run_request(), "[Added: Fresh; Edited: Fear MO] Did both.")
        macros = {m["name"]: m for m in read_spec(self.sb.project / "macros.json")["macros"]}
        self.assertIn("@focus", macros["Fear MO"]["body"])
        self.assertEqual(len([n for n in self.spec_names() if n == "Fear MO"]), 1)

    def test_the_most_macros_allowed_in_one_answer_all_arrive(self):
        eight = [dict(NEW_MACRO, name=f"Bulk {i}") for i in range(llm.MAX_UPSERTS)]
        self.server.reply(answer(upsert=eight))
        self.run_request()
        self.assertTrue({m["name"] for m in eight} <= set(self.spec_names()))

    def test_default_runner_follows_the_backend(self):
        self.assertIs(watcher.default_runner(self.settings), agent.run_agent)
        runner = watcher.default_runner(self.cfg)
        self.assertTrue(callable(runner))
        self.assertIsNot(runner, agent.run_agent)


class PromptTests(LlmCase):
    def req(self):
        from mforge.validate import Request
        return Request(id=5, text="make a Spell Lock macro", char="Test Char", cls="warlock", level=30)

    def test_the_prompt_has_the_rules_the_macros_and_the_blocked_commands(self):
        system, user = [m["content"] for m in llm.build_messages(self.cfg, self.req(), None)]
        self.assertIn("/script", system)  # blocked commands are listed
        self.assertIn("## Spec entry", system)  # the brief
        self.assertIn("Character: Test Char", user)
        self.assertIn("Fear MO", user)  # this character's macro
        self.assertIn("Tap", user)  # an account-wide macro
        self.assertNotIn(KEY, system + user)

    def test_other_characters_macros_are_not_sent(self):
        spec = read_spec(self.sb.project / "macros.json")
        spec["macros"].append({"name": "Secret", "char": "Someone Else", "body": "/x"})
        (self.sb.project / "macros.json").write_text(json.dumps(spec), encoding="utf-8")
        user = llm.build_messages(self.cfg, self.req(), None)[1]["content"]
        self.assertNotIn("Secret", user)

    def test_pet_families_are_included_only_when_relevant(self):
        write_tables(self.cfg.data)
        (self.cfg.data / "pets.json").unlink(missing_ok=True)
        user = llm.build_messages(self.cfg, self.req(), None)[1]["content"]
        self.assertIn("Felhunter", user)
        self.assertNotIn("Succubus", user)
        plain = dataclasses.replace(self.req(), text="make a bandage macro")
        self.assertNotIn("Felhunter", llm.build_messages(self.cfg, plain, None)[1]["content"])
        with_pet = llm.build_messages(self.cfg, plain, {"pet": {"family": "Succubus", "actions": []}})
        self.assertIn("Succubus", with_pet[1]["content"])

    def test_a_custom_context_file_replaces_the_brief_for_both_backends(self):
        (self.sb.project / "my-rules.md").write_text("MY OWN RULES", encoding="utf-8")
        mine = dataclasses.replace(self.cfg, agent_context_file="my-rules.md")
        system = llm.build_messages(mine, self.req(), None)[0]["content"]
        self.assertIn("MY OWN RULES", system)
        self.assertNotIn("## Spec entry", system)
        workspace = agent.prepare_workspace(mine, self.req())
        self.addCleanup(shutil.rmtree, workspace, True)
        self.assertEqual((workspace / "agent-brief.md").read_text(encoding="utf-8"), "MY OWN RULES")

    def test_a_missing_context_file_is_a_clear_error(self):
        mine = dataclasses.replace(self.cfg, agent_context_file="nope.md")
        with self.assertRaises(ConfigError):
            llm.build_messages(mine, self.req(), None)


class RepairTests(LlmCase):
    def test_a_bad_answer_is_sent_back_once_and_the_second_try_is_used(self):
        self.server.reply("I think you want a macro, something like Corruption.")
        self.server.reply(answer())
        self.assertEqual(self.run_request(), "[Added: Corr] Added it.")
        self.assertEqual(len(self.server.requests), 2)
        feedback = self.server.requests[1]["body"]["messages"][-1]["content"]
        self.assertIn("rejected", feedback)

    def test_the_validator_can_trigger_the_repair(self):
        bad = dict(NEW_MACRO, body="/script print('hi')")
        self.server.reply(answer(upsert=[bad]))
        self.server.reply(answer())
        self.assertEqual(self.run_request(), "[Added: Corr] Added it.")
        self.assertIn("script", self.server.requests[1]["body"]["messages"][-1]["content"].lower())

    def test_it_gives_up_after_the_configured_retries(self):
        for _ in range(5):
            self.server.reply("not json at all")
        reply = self.run_request()
        self.assertTrue(reply.startswith("Rejected, nothing changed"), reply)
        self.assertEqual(len(self.server.requests), 2)  # llm_retries = 1
        none = dataclasses.replace(self.cfg, llm_retries=0)
        self.server.requests.clear()
        self.run_request(request(rid=2), settings=none)
        self.assertEqual(len(self.server.requests), 1)

    def test_removing_a_macro_that_is_not_managed_is_rejected(self):
        before = self.spec_text()
        sneaky = answer(upsert=[], remove=[{"name": "Not Mine", "char": "Test Char"}])
        for _ in range(3):
            self.server.reply(sneaky)
        reply = self.run_request()
        self.assertTrue(reply.startswith("Rejected, nothing changed"), reply)
        self.assertEqual(self.spec_text(), before)

    def test_too_many_macros_in_one_answer_are_rejected(self):
        many = [dict(NEW_MACRO, name=f"M{i}") for i in range(llm.MAX_UPSERTS + 1)]
        for _ in range(3):
            self.server.reply(answer(upsert=many))
        self.assertTrue(self.run_request().startswith("Rejected"))

    def test_an_oversize_macro_is_rejected(self):
        huge = dict(NEW_MACRO, body="/cast Fear\n" * 40)
        for _ in range(3):
            self.server.reply(answer(upsert=[huge]))
        self.assertTrue(self.run_request().startswith("Rejected"))


class TransportTests(LlmCase):
    def call(self, **changes):
        return llm.run_chat(dataclasses.replace(self.cfg, **changes), self.req(), None)

    def req(self):
        from mforge.validate import Request
        return Request(id=5, text="x", char="Test Char", cls="warlock", level=30)

    def assertFails(self, *words, **changes):
        with self.assertRaises(AgentError) as caught:
            self.call(**changes)
        message = str(caught.exception)
        self.assertNotIn(KEY, message)
        for word in words:
            self.assertIn(word, message)
        return message

    def test_http_errors_get_helpful_messages(self):
        self.server.respond(401, {"error": {"message": "bad key"}})
        self.assertFails("rejected the API key", "MACROFORGE_API_KEY")
        self.server.respond(404, {"error": {"message": "no such model"}})
        self.assertFails("llm_base_url", "test-model")
        self.server.respond(429, {"error": {"message": "slow down"}})
        self.assertFails("429")
        self.server.respond(500, b"upstream broke")
        self.assertFails("HTTP 500")

    def test_a_key_echoed_by_the_server_is_redacted(self):
        self.server.respond(400, {"error": {"message": f"invalid key {KEY} given"}})
        self.assertFails("invalid key", "***", llm_json_mode=False)

    def test_a_server_that_rejects_response_format_is_retried_without_it(self):
        self.server.respond(400, {"error": {"message": "response_format is not supported"}})
        self.server.reply(answer())
        result = self.call()
        self.assertIn("Added it.", result.text)
        self.assertIn("response_format", self.server.requests[0]["body"])
        self.assertNotIn("response_format", self.server.requests[1]["body"])

    def test_json_mode_can_be_turned_off(self):
        self.server.reply(answer())
        self.call(llm_json_mode=False)
        self.assertNotIn("response_format", self.server.requests[0]["body"])

    def test_nothing_listening_says_to_start_the_model_server(self):
        self.server.close()
        self.assertFails("is the model server running")

    def test_a_slow_server_times_out(self):
        self.server.respond(200, completion(answer()), delay=2)
        with self.assertRaises(agent.AgentTimeout):
            self.call(agent_timeout_seconds=1)

    def test_an_oversized_response_is_refused(self):
        self.server.respond(200, b"x" * (llm.MAX_RESPONSE_BYTES + 10))
        self.assertFails("too large")

    def test_redirects_are_not_followed(self):
        self.server.respond(302, b"", {"Location": "http://127.0.0.1:1/steal"})
        self.assertFails("redirected")
        self.assertEqual(len(self.server.requests), 1)

    def test_odd_responses_are_reported_not_crashed_on(self):
        for payload, word in ((b"<html>hi</html>", "did not return JSON"),
                              ({"choices": []}, "unexpected format"),
                              (completion("   "), "empty answer"),
                              ([1, 2], "unexpected response")):
            self.server.respond(200, payload)
            self.assertFails(word)

    def test_a_missing_key_for_a_remote_service_is_explained_before_any_request(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("MACROFORGE_API_KEY", None)
            self.assertFails("MACROFORGE_API_KEY", llm_base_url="https://api.example.org/v1")
        self.assertEqual(self.server.requests, [])

    def test_a_local_model_needs_no_key_and_sends_none(self):
        os.environ.pop("MACROFORGE_API_KEY", None)
        self.server.reply(answer())
        self.call()
        self.assertNotIn("Authorization", self.server.requests[0]["headers"])

    def test_the_model_name_is_required(self):
        with self.assertRaises(ConfigError):
            self.call(agent_model="")

    def test_check_connection(self):
        self.server.reply("ok")
        report = llm.check_connection(self.cfg)
        self.assertIn("test-model answered", report)
        self.server.respond(401, {"error": {"message": "nope"}})
        with self.assertRaises(AgentError):
            llm.check_connection(self.cfg)


class SettingsTests(unittest.TestCase):
    def load(self, **config):
        from tests.harness import Sandbox
        sb = Sandbox(config=config)
        self.addCleanup(sb.cleanup)
        return Settings.load(sb.project)

    def test_defaults_keep_the_claude_backend(self):
        s = self.load()
        self.assertEqual((s.agent_backend, s.llm_retries, s.llm_json_mode), ("claude", 1, True))

    def test_valid_openai_settings(self):
        s = self.load(agent_backend="openai", agent_model="gpt-x",
                      llm_base_url="http://localhost:11434/v1", llm_api_key_env="MY_KEY")
        self.assertEqual((s.agent_backend, s.llm_api_key_env), ("openai", "MY_KEY"))

    def test_bad_values_are_refused_with_a_clear_message(self):
        bad = [({"agent_backend": "bard"}, "agent_backend"),
               ({"llm_base_url": "http://api.example.org/v1"}, "https"),
               ({"llm_base_url": "http://localhost.evil.com/v1"}, "https"),
               ({"llm_base_url": "https://user:pw" + "@api.example.org/v1"}, "user name"),
               ({"llm_base_url": "ftp://localhost/v1"}, "https"),
               ({"llm_api_key_env": "sk-proj-abcdefghijklmnop"}, "NAME of an environment"),
               ({"llm_max_tokens": 5}, "llm_max_tokens"),
               ({"llm_retries": 9}, "llm_retries"),
               ({"llm_json_mode": "yes"}, "llm_json_mode")]
        for config, word in bad:
            with self.subTest(config=config), self.assertRaises(ConfigError) as caught:
                self.load(**config)
            self.assertIn(word, str(caught.exception))

    def test_url_rules(self):
        for url in ("https://api.openai.com/v1", "http://localhost:1234/v1", "http://127.0.0.1/v1",
                    "http://[::1]:8080/v1"):
            self.assertEqual(validate_llm_url(url), url)
        for url in ("", "localhost:11434", "http://0.0.0.0/v1", "http://127.0.0.1.evil.com/v1",
                    "https:///v1"):
            with self.assertRaises(ConfigError):
                validate_llm_url(url)

    def test_the_watcher_refuses_to_start_without_a_model(self):
        s = dataclasses.replace(self.load(), agent_backend="openai", agent_model="")
        with self.assertRaises(ConfigError):
            watcher.run_watcher(s, stop=lambda: True)


class LlmTestCommandTests(LlmCase):
    def configure(self, **updates):
        from mforge.setup import write_config
        write_config(self.sb.project, {"agent_backend": "openai", "agent_model": "m",
                                       "llm_base_url": self.server.url, **updates})

    def test_llm_test_reports_a_working_endpoint(self):
        self.configure()
        self.server.reply("ok")
        result = self.sb.run("llm-test", env={"MACROFORGE_API_KEY": KEY})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("answered", result.stdout)

    def test_llm_test_explains_a_failure_without_a_traceback_or_the_key(self):
        self.configure()
        self.server.respond(401, {"error": {"message": f"wrong key {KEY}"}})
        result = self.sb.run("llm-test", env={"MACROFORGE_API_KEY": KEY})
        self.assertEqual(result.returncode, 1)
        self.assertNotIn("Traceback", result.stderr)
        self.assertNotIn(KEY, result.stdout + result.stderr)
        self.assertIn("rejected the API key", result.stderr)

    def test_llm_test_with_the_claude_backend_says_what_to_change(self):
        result = self.sb.run("llm-test")
        self.assertEqual(result.returncode, 1)
        self.assertIn("agent_backend", result.stderr)


if __name__ == "__main__":
    unittest.main()

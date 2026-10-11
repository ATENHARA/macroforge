"""End-to-end tests of the request watcher and agent with a fake ``claude`` process.

The fake runner plays the part of the CLI: it edits the workspace it is given and returns JSON, so
the real code path (workspace, validation, rate limit, install, replies) runs without a network,
the game or a real Claude.
"""
from __future__ import annotations

import copy
import dataclasses
import json
import os
import subprocess
import sys
import time
import unittest
from functools import partial
from pathlib import Path
from unittest import mock

from mforge import agent, watcher
from mforge.errors import AgentError, MacroForgeError
from mforge.install import create_slots
from mforge.savedvars import parse_sv
from mforge.settings import Settings
from mforge.validate import Request
from tests.harness import DEFAULT_MACROS, Sandbox

NEW_MACRO = {"name": "Corr", "char": "Test Char", "icon": "spell:Fear",
             "body": "#showtooltip Fear\n/cast Fear"}


def request(rid=100, text="add a corruption macro", **over):
    base = {"id": rid, "text": text, "char": "Test Char", "class": "WARLOCK", "level": 30}
    base.update(over)
    return base


def cli_json(result="REPLY: ok", **extra):
    return json.dumps({"result": result, "usage": {"input_tokens": 3}, "total_cost_usd": 0.01,
                       "num_turns": 2, **extra})


def fake_process(edit=None, result="Done.\nREPLY: Added it.", rc=0, out=None, err=""):
    """A stand-in for ``run_process``: ``edit(workspace)`` mutates the agent's workspace."""
    calls = []

    def run(cmd, input_text, cwd, env, timeout):
        calls.append({"cmd": cmd, "input": input_text, "cwd": Path(cwd), "env": dict(env), "timeout": timeout})
        if edit:
            edit(Path(cwd))
        return rc, (cli_json(result) if out is None else out), err

    run.calls = calls
    return run


def add_macro(macro):
    def edit(workspace: Path):
        spec = json.loads((workspace / "macros.json").read_text(encoding="utf-8"))
        spec["macros"].append(macro)
        (workspace / "macros.json").write_text(json.dumps(spec), encoding="utf-8")
    return edit


def write_raw(text):
    return lambda ws: (ws / "macros.json").write_text(text, encoding="utf-8")


class WatcherCase(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox()
        self.addCleanup(self.sb.cleanup)
        env = mock.patch.dict(os.environ, {"LOCALAPPDATA": str(self.sb.tmp / "local")})
        env.start()
        self.addCleanup(env.stop)
        dummy = self.sb.tmp / "claude.exe"
        dummy.write_text("not really claude")
        self.settings = dataclasses.replace(Settings.load(self.sb.project), claude_path=str(dummy))
        self.state = watcher.WatchState(self.sb.project / "data" / "state.json")
        create_slots(self.settings, 2)

    def handle(self, raw=request, runner=None, state=None, settings=None, export=None):
        """Run one request; pass ``raw`` explicitly (even None) to test malformed requests."""
        runner = runner or fake_process(add_macro(NEW_MACRO))
        raw = request() if raw is request else raw
        return watcher.handle_request(settings or self.settings, raw, export,
                                      state or self.state, partial(agent.run_agent, runner=runner))

    def spec_text(self):
        return (self.sb.project / "macros.json").read_text(encoding="utf-8")

    def spec_names(self):
        return [m["name"] for m in json.loads(self.spec_text())["macros"]]

    def assertRejected(self, reply, *words):
        self.assertTrue(reply.startswith("Rejected, nothing changed"), reply)
        for word in words:
            self.assertIn(word, reply)


class HappyPathTests(WatcherCase):
    def test_several_macros_in_one_request_are_all_added(self):
        macros = [dict(NEW_MACRO, name=name) for name in ("Corr A", "Corr B", "Corr C")]

        def edit(workspace):
            for macro in macros:
                add_macro(macro)(workspace)

        reply = self.handle(runner=fake_process(edit))
        self.assertEqual(reply, "[Added: Corr A, Corr B, Corr C] Added it.")
        self.assertTrue({"Corr A", "Corr B", "Corr C"} <= set(self.spec_names()))

    def test_the_audit_log_records_which_model_answered(self):
        out = cli_json("Done.\nREPLY: ok", modelUsage={"claude-test-1": {"costUSD": 0.01}})
        self.handle(runner=fake_process(add_macro(NEW_MACRO), out=out))
        last = (self.sb.project / "data" / "watch.log").read_text(encoding="utf-8").splitlines()[-1]
        self.assertEqual(json.loads(last)["models"], ["claude-test-1"])

    def test_benign_request_is_applied_and_installed(self):
        reply = self.handle()
        self.assertEqual(reply, "[Added: Corr] Added it.")
        self.assertIn("Corr", self.spec_names())
        data = (self.settings.addons / "MacroForge" / "MacroForgeData.lua").read_text(encoding="utf-8")
        self.assertIn("Corr", data)
        parsed = parse_sv(data)["MacroForgeData"]
        self.assertEqual(parsed["replies"][-1]["text"], "[Added: Corr] Added it.")
        self.assertEqual(parsed["replies"][-1]["req"], 100)
        slot = (self.settings.addons / "MacroForge_S002" / "Slot.lua").read_text(encoding="utf-8")
        self.assertIn("Added it.", slot)
        self.assertIn("[Added: Corr]", slot)
        self.assertIn("MacroForgeSlotData", slot)

    def test_audit_log_backup_and_state(self):
        self.handle()
        line = json.loads((self.sb.project / "data" / "watch.log").read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual((line["req"], line["changed"], line["cost_usd"]), (100, True, 0.01))
        self.assertTrue((self.sb.project / "data" / "backups" / line["backup"]).exists())
        self.assertEqual(len(self.state.recent), 1)

    def test_workspace_holds_only_copies_and_is_deleted(self):
        runner = fake_process(add_macro(NEW_MACRO))
        self.handle(runner=runner)
        call = runner.calls[0]
        self.assertFalse(call["cwd"].exists(), "workspace must be deleted afterwards")
        self.assertNotIn(str(self.sb.project), str(call["cwd"]))
        self.assertEqual(call["cwd"].parent.parent.name, "macroforge-agent")

    def test_workspace_contents(self):
        seen = {}

        def edit(ws):
            seen["files"] = sorted(p.name for p in ws.iterdir())
            add_macro(NEW_MACRO)(ws)
        self.handle(runner=fake_process(edit))
        self.assertEqual(seen["files"], ["agent-brief.md", "icons.csv", "macros.json", "pets.json", "spells.json"])

    def test_class_is_remembered_for_new_characters(self):
        self.handle(request(rid=5, char="New Mage", **{"class": "mage"}))
        self.assertEqual(json.loads(self.spec_text())["classes"]["New Mage"], "mage")

    def test_prompt_contains_character_pet_and_spells(self):
        runner = fake_process(add_macro(NEW_MACRO))
        export = {"spells": [{"name": "Fear"}, {"name": "Passive Thing", "passive": True}],
                  "pet": {"family": "Imp", "actions": ["Firebolt"]}}
        self.handle(runner=runner, export=export)
        prompt = runner.calls[0]["input"]
        for text in ("Test Char", "class warlock", "level 30", "family Imp", "Fear", "add a corruption macro"):
            self.assertIn(text, prompt)
        self.assertNotIn("Passive Thing", prompt)


class RejectionTests(WatcherCase):
    def test_blocked_and_evading_commands_are_rejected(self):
        for body in ("/script print(1)", "/RUN x", "\t/run x", "  /Script y", "/run(print(1))", "/dump 1",
                     "/console x", "#showtooltip\n/cast Fear\n/script x"):
            before = self.spec_text()
            reply = self.handle(request(rid=abs(hash(body)) % 10**9 + 1),
                                runner=fake_process(add_macro({**NEW_MACRO, "body": body})))
            # a tab is rejected even earlier, as a control character
            self.assertRejected(reply, "control" if body.startswith("\t") else "blocked")
            self.assertEqual(self.spec_text(), before, body)

    def test_unknown_commands_and_conditionals_are_rejected_in_agent_mode(self):
        for body in ("/petattak", "/cast [flarb] Fear", "hello"):
            before = self.spec_text()
            reply = self.handle(request(rid=abs(hash(body)) % 10**9 + 1),
                                runner=fake_process(add_macro({**NEW_MACRO, "body": body})))
            self.assertRejected(reply)
            self.assertEqual(self.spec_text(), before, body)

    def test_unknown_spell_is_only_a_warning(self):
        reply = self.handle(runner=fake_process(add_macro({**NEW_MACRO, "body": "/cast Fear Blot"})))
        self.assertIn("(check: 1 warning(s)", reply)
        self.assertIn("Corr", self.spec_names())

    def test_malformed_results_are_rejected(self):
        cases = {"not json": write_raw("{nope"), "no macros": write_raw('{"x": 1}'),
                 "list": write_raw("[1,2]"), "too long": add_macro({**NEW_MACRO, "body": "/cast " + "x" * 300}),
                 "pipe": add_macro({**NEW_MACRO, "body": "/say |cffff0000x|r"}),
                 "control": add_macro({**NEW_MACRO, "body": "/say \x07"}),
                 "dup name": add_macro(DEFAULT_MACROS["macros"][0])}
        for label, edit in cases.items():
            before = self.spec_text()
            reply = self.handle(request(rid=abs(hash(label)) % 10**9 + 1), runner=fake_process(edit))
            self.assertRejected(reply)
            self.assertEqual(self.spec_text(), before, label)

    def test_agent_side_effects_never_reach_the_project(self):
        project_before = sorted(p.relative_to(self.sb.project).as_posix() for p in self.sb.project.rglob("*")
                                if "data" not in p.parts and "build" not in p.parts)

        def edit(ws):
            (ws / "stray.txt").write_text("x")
            (ws.parent / "outside.txt").write_text("x")
            (ws / "agent-brief.md").write_text("tampered")
            add_macro(NEW_MACRO)(ws)
        self.handle(runner=fake_process(edit))
        project_after = sorted(p.relative_to(self.sb.project).as_posix() for p in self.sb.project.rglob("*")
                               if "data" not in p.parts and "build" not in p.parts)
        self.assertEqual(project_before, project_after)
        self.assertNotIn("tampered", (self.sb.project / "reference" / "agent-brief.md").read_text("utf-8"))

    def test_timeout_login_failure_and_garbage_output(self):
        def timeout(*_a):
            raise agent.AgentTimeout("timed out")

        self.assertIn("Timed out after 300s", self.handle(runner=timeout))
        login = fake_process(rc=1, out=json.dumps({"result": "Not logged in · Please run /login"}))
        reply = self.handle(request(rid=2), runner=login)
        self.assertRejected(reply, "Not logged in", "login")
        self.assertRejected(self.handle(request(rid=3), runner=fake_process(out="<<<garbage>>>")))
        self.assertRejected(self.handle(request(rid=4), runner=fake_process(out="[1,2,3]")))
        self.assertRejected(self.handle(request(rid=5), runner=fake_process(
            out=json.dumps({"is_error": True, "result": "budget exceeded"})), ), "budget")
        self.assertNotIn("Corr", self.spec_names())

    def test_missing_or_oversized_spec_is_rejected(self):
        self.assertRejected(self.handle(request(rid=2), runner=fake_process(lambda ws: (ws / "macros.json").unlink())))
        big = write_raw(json.dumps({"macros": [], "pad": "x" * 2_000_000}))
        self.assertRejected(self.handle(request(rid=3), runner=fake_process(big)))

    def test_reply_is_sanitized_for_the_game_chat(self):
        evil = "REPLY: done |cffff0000RED|r |Hitem:19019|h[Thunderfury]|h café \x00 " + "x" * 3000
        reply = self.handle(runner=fake_process(add_macro(NEW_MACRO), result=evil))
        self.assertNotIn("|", reply)
        self.assertTrue(reply.isascii())
        self.assertLessEqual(len(reply), 600)

    def test_rejected_class_never_creates_files_outside_data(self):
        data = self.sb.project / "data"
        before = {p.name for p in data.iterdir()}
        reply = self.handle(request(rid=9, **{"class": "../../evil"}), runner=fake_process(add_macro(NEW_MACRO)))
        self.assertIn("Request rejected", reply)
        self.assertLessEqual({p.name for p in data.iterdir()} - before, {"replies.json", "watch.log"})
        self.assertFalse(any(".." in str(p.relative_to(self.sb.project)) for p in self.sb.project.rglob("*")))
        self.assertFalse((self.sb.project.parent / "evil").exists())
        self.assertNotIn("Corr", self.spec_names())

    def test_invalid_request_without_id_is_ignored(self):
        for raw in (None, "text", {"text": "x"}, {"id": "7", "text": "x"}, {"id": True}):
            self.assertIsNone(self.handle(raw))


class RateLimitTests(WatcherCase):
    def test_limit_applies_and_survives_restart(self):
        settings = dataclasses.replace(self.settings, max_requests_per_hour=2)
        noop = fake_process()
        for rid in (1, 2):
            self.handle(request(rid=rid), runner=noop, settings=settings)
        self.state.save()
        self.assertEqual(len(noop.calls), 2)
        reloaded = watcher.WatchState.load(self.state.path)
        reply = self.handle(request(rid=3), runner=noop, settings=settings, state=reloaded)
        self.assertIn("Rate limit", reply)
        self.assertEqual(len(noop.calls), 2)

    def test_old_runs_expire(self):
        self.state.recent = [time.time() - 7200] * 20
        self.assertFalse(self.state.rate_limited(10, time.time()))

    def test_legacy_and_corrupt_state_files(self):
        self.state.path.write_text("[5, 6, 7]")
        self.assertEqual(watcher.WatchState.load(self.state.path).handled, {5, 6, 7})
        self.state.path.write_text("{{{")
        self.assertEqual(watcher.WatchState.load(self.state.path).handled, set())
        self.state.path.write_text(json.dumps({"handled": [1, "x", 2.5, None], "recent": ["a", 3]}))
        loaded = watcher.WatchState.load(self.state.path)
        self.assertEqual((loaded.handled, loaded.recent), ({1}, [3]))


class ProcessRequestsTests(WatcherCase):
    def write_requests(self, requests_lua: str):
        (self.sb.saved_vars / "MacroForge.lua").write_text(
            f"MacroForgeRequests = {requests_lua}\nMacroForgeExport = {{ [\"char\"] = \"Test\" }}\n", encoding="utf-8")

    def test_hostile_requests_do_not_crash_the_watcher(self):
        self.write_requests('''{
            { ["id"] = 1, ["text"] = "ok", ["char"] = "Test Char", ["class"] = "WARLOCK", ["level"] = 30 },
            "just a string",
            { ["text"] = "no id" },
            { ["id"] = true },
            { ["id"] = 2, ["text"] = "x", ["char"] = "Test Char", ["class"] = "../../evil", ["level"] = 30 },
            { ["id"] = 3, ["text"] = "x", ["char"] = "Test Char", ["class"] = "WARLOCK", ["level"] = "high" },
            { ["id"] = 1, ["text"] = "duplicate id", ["char"] = "Test Char", ["class"] = "WARLOCK", ["level"] = 30 },
        }''')
        runner = partial(agent.run_agent, runner=fake_process(add_macro(NEW_MACRO)))
        count = watcher.process_new_requests(self.settings, self.state, runner)
        self.assertEqual(count, 3)  # ids 1, 2, 3; entries without a usable id and the repeat of 1 are skipped
        self.assertEqual(self.state.handled, {1, 2, 3})
        replies = json.loads((self.sb.project / "data" / "replies.json").read_text())
        texts = {r["req"]: r["text"] for r in replies}
        self.assertEqual(texts[1], "[Added: Corr] Added it.")
        self.assertIn("Request rejected", texts[2])
        self.assertIn("Request rejected", texts[3])

    def test_a_crashing_request_does_not_stop_the_next_one(self):
        self.write_requests('''{
            { ["id"] = 1, ["text"] = "a", ["char"] = "Test Char", ["class"] = "WARLOCK", ["level"] = 30 },
            { ["id"] = 2, ["text"] = "b", ["char"] = "Test Char", ["class"] = "WARLOCK", ["level"] = 30 },
        }''')
        calls = []

        def runner(settings, req, export):
            calls.append(req.id)
            if req.id == 1:
                raise RuntimeError("boom")
            return agent.run_agent(settings, req, export, runner=fake_process(add_macro(NEW_MACRO)))

        with self.assertLogs("mforge.watcher", level="ERROR"):
            watcher.process_new_requests(self.settings, self.state, runner)
        self.assertEqual(calls, [1, 2])
        self.assertEqual(self.state.handled, {1, 2})

    def test_unreadable_or_odd_saved_variables(self):
        for text in ("garbage = {", "MacroForgeRequests = 5\n", "MacroForgeRequests = {}\n", ""):
            (self.sb.saved_vars / "MacroForge.lua").write_text(text, encoding="utf-8")
            with self.assertLogs("mforge.watcher", level="WARNING") if "garbage" in text else mock.MagicMock():
                self.assertEqual(watcher.process_new_requests(self.settings, self.state), 0)

    def test_already_handled_requests_are_skipped(self):
        self.write_requests('{ { ["id"] = 1, ["text"] = "a", ["char"] = "Test Char", '
                            '["class"] = "WARLOCK", ["level"] = 30 } }')
        self.state.handled.add(1)
        runner = fake_process()
        self.assertEqual(watcher.process_new_requests(self.settings, self.state,
                                                      partial(agent.run_agent, runner=runner)), 0)
        self.assertEqual(runner.calls, [])


class RunLoopTests(WatcherCase):
    def test_loop_answers_a_request_and_releases_the_lock(self):
        (self.sb.saved_vars / "MacroForge.lua").write_text(
            'MacroForgeRequests = { { ["id"] = 7, ["text"] = "go", ["char"] = "Test Char", '
            '["class"] = "WARLOCK", ["level"] = 30 } }\n', encoding="utf-8")
        ticks = iter(range(100))
        runner = partial(agent.run_agent, runner=fake_process(add_macro(NEW_MACRO)))
        with self.assertLogs("mforge.watcher", level="INFO"):
            watcher.run_watcher(self.settings, runner=runner, sleep=lambda s: None,
                                stop=lambda: next(ticks) >= 3)
        self.assertIn("Corr", self.spec_names())
        self.assertFalse((self.sb.project / "data" / "watcher.pid").exists())
        self.assertEqual(watcher.WatchState.load(self.sb.project / "data" / "watch_state.json").handled, {7})

    def test_second_watcher_is_refused(self):
        other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        self.addCleanup(other.kill)
        lock_path = self.sb.project / "data" / "watcher.pid"
        lock_path.parent.mkdir(exist_ok=True)
        lock_path.write_text(str(other.pid))
        with self.assertRaisesRegex(MacroForgeError, "already running"):
            watcher.run_watcher(self.settings, sleep=lambda s: None, stop=lambda: True)
        other.kill()
        other.wait()
        with self.assertLogs("mforge.watcher", level="INFO"):
            watcher.run_watcher(self.settings, sleep=lambda s: None, stop=lambda: True)  # stale lock taken over


class LockTests(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox()
        self.addCleanup(self.sb.cleanup)
        self.path = self.sb.tmp / "w.pid"

    def test_acquire_and_release(self):
        lock = watcher.WatcherLock(self.path)
        lock.acquire()
        self.assertEqual(self.path.read_text(), str(os.getpid()))
        lock.release()
        self.assertFalse(self.path.exists())

    def test_garbage_pid_file_is_taken_over(self):
        self.path.write_text("not a pid")
        with watcher.WatcherLock(self.path):
            self.assertEqual(self.path.read_text(), str(os.getpid()))

    def test_release_does_not_delete_another_holders_file(self):
        self.path.write_text("999999")
        watcher.WatcherLock(self.path).release()
        self.assertTrue(self.path.exists())

    def test_pid_match_is_exact_not_substring(self):
        listing = '"python.exe","1234","Console","1","20,000 K"\n'
        with mock.patch.object(sys, "platform", "win32"), \
                mock.patch("subprocess.run", return_value=mock.Mock(stdout=listing)):
            self.assertTrue(watcher.pid_running(1234))
            self.assertFalse(watcher.pid_running(12))
            self.assertFalse(watcher.pid_running(234))
        self.assertTrue(watcher.pid_running(os.getpid()))


class AgentUnitTests(WatcherCase):
    def test_env_is_an_allowlist(self):
        planted = {"SECRET_TOKEN": "s3cret", "AWS_SECRET_ACCESS_KEY": "x", "CLAUDE_CODE_SIMPLE": "1",
                   "ANTHROPIC_BASE_URL": "http://host", "ANTHROPIC_API_KEY": "key", "GITHUB_TOKEN": "t",
                   "PATH": "p", "USERPROFILE": "u"}
        env = agent.agent_env(planted)
        self.assertEqual(env, {"ANTHROPIC_API_KEY": "key", "PATH": "p", "USERPROFILE": "u"})
        runner = fake_process(add_macro(NEW_MACRO))
        with mock.patch.dict(os.environ, planted):
            self.handle(runner=runner)
        self.assertNotIn("SECRET_TOKEN", runner.calls[0]["env"])
        self.assertNotIn("GITHUB_TOKEN", runner.calls[0]["env"])

    def test_command_line_is_restricted(self):
        cmd = agent.build_command(dataclasses.replace(self.settings, agent_model="sonnet"), "claude")
        self.assertIn("--restricted", cmd)
        self.assertEqual(cmd[cmd.index("--tools") + 1], "Read,Glob,Grep,Edit")
        self.assertNotIn("--add-dir", cmd)
        self.assertNotIn("Bash", " ".join(cmd[cmd.index("--tools"):cmd.index("--tools") + 2]))
        self.assertEqual(cmd[cmd.index("--model") + 1], "sonnet")
        self.assertEqual(cmd[cmd.index("--max-budget-usd") + 1], "0.5")
        system = cmd[cmd.index("--append-system-prompt") + 1]
        for blocked in ("/script", "/run", "/dump", "/console"):
            self.assertIn(blocked, system)

    def test_reply_rules_forbid_untested_style_disclaimers(self):
        system = agent.system_prompt(self.settings).lower()
        self.assertIn("do not add disclaimers", system)
        self.assertIn("never say it is untested", system)
        self.assertIn("one short clause", system)
        self.assertNotIn("anything you are unsure of", system)

    def test_budget_flag_can_be_disabled(self):
        cmd = agent.build_command(dataclasses.replace(self.settings, agent_max_budget_usd=0.0), "claude")
        self.assertNotIn("--max-budget-usd", cmd)

    def test_missing_cli_has_install_instructions(self):
        settings = dataclasses.replace(self.settings, claude_path="")
        with mock.patch("shutil.which", return_value=None), \
                mock.patch.dict(os.environ, {"APPDATA": str(self.sb.tmp / "nowhere")}), \
                mock.patch("pathlib.Path.home", return_value=self.sb.tmp / "nohome"):
            with self.assertRaisesRegex(AgentError, "install.ps1"):
                agent.find_claude(settings)

    def test_claude_path_must_be_a_file(self):
        with self.assertRaisesRegex(AgentError, "not a file"):
            agent.find_claude(dataclasses.replace(self.settings, claude_path=str(self.sb.tmp / "nope.exe")))

    def test_run_process_timeout_kills_the_child(self):
        started = time.time()
        with self.assertRaises(agent.AgentTimeout):
            agent.run_process([sys.executable, "-c", "import time; time.sleep(60)"], "",
                              self.sb.tmp, os.environ.copy(), 1)
        self.assertLess(time.time() - started, 20)

    def test_run_process_passes_stdin_env_and_exit_code(self):
        code = "import sys, os; print(sys.stdin.read().upper(), os.environ.get('PLANTED')); sys.exit(3)"
        rc, out, _err = agent.run_process([sys.executable, "-c", code], "hello", self.sb.tmp,
                                          {**os.environ, "PLANTED": "yes"}, 30)
        self.assertEqual((rc, out.split()), (3, ["HELLO", "yes"]))

    def test_extract_reply(self):
        self.assertEqual(agent.extract_reply("thinking...\nREPLY: Added X"), "Added X")
        self.assertEqual(agent.extract_reply("no marker here"), "no marker here")
        self.assertEqual(len(agent.extract_reply("y" * 1000)), 300)
        self.assertEqual(agent.extract_reply("REPLY: Nothing changed. REPLY: Ask for a macro."),
                         "Nothing changed. Ask for a macro.")
        self.assertEqual(agent.extract_reply("note\nREPLY: one\nREPLY: two"), "one two")


if __name__ == "__main__":
    unittest.main()

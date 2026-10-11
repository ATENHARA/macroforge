"""Script requests: the mode travels with the request and the watcher enforces it both ways."""
from __future__ import annotations

import json
import unittest
from functools import partial

from mforge import agent, watcher
from mforge.errors import ConfigError, ValidationError
from mforge.lua import render_data
from mforge.settings import Settings
from mforge.shots import request_from_payload
from mforge.spec import changed_macros, diff_specs, load_spec, resolve_spec
from mforge.validate import MODES, parse_request
from tests.test_agent_watcher import NEW_MACRO, WatcherCase, add_macro, fake_process, request
from tests.test_llm import LlmCase, answer

CLASSES = frozenset({"warlock", "mage"})
PET_SCRIPT = ('/run local n={"Milo","Rascal"};local i=random(#n);if i==MF_p then i=i%#n+1 end;'
              "MF_p=i;PetRename(n[i])")
SCRIPT = {"name": "Pet Name", "char": "Test Char", "kind": "script", "body": PET_SCRIPT}


def raw(**over):
    base = {"id": 7, "text": "x", "char": "Test Char", "class": "WARLOCK", "level": 30}
    base.update(over)
    return base


class RequestModeTests(unittest.TestCase):
    def test_default_mode_is_macro_so_old_requests_still_work(self):
        self.assertEqual(parse_request(raw(), CLASSES).mode, "macro")

    def test_script_mode(self):
        self.assertEqual(parse_request(raw(mode="script"), CLASSES).mode, "script")
        self.assertEqual(MODES, ("macro", "script"))

    def test_other_modes_are_refused(self):
        for bad in ("both", "", "SCRIPT", 5, None, ["script"], True):
            with self.assertRaises(ValidationError, msg=repr(bad)):
                parse_request(raw(mode=bad), CLASSES)

    def test_strip_payload_with_and_without_the_mode_field(self):
        base = "\t".join(["MFREQ", "42", "30", "WARLOCK", "Test Char", "make it"])
        self.assertEqual(request_from_payload(base.encode())["mode"], "macro")
        self.assertEqual(request_from_payload((base + "\tscript").encode())["mode"], "script")
        self.assertEqual(request_from_payload((base + "\tmacro").encode())["mode"], "macro")
        with self.assertRaises(ValidationError):
            request_from_payload((base + "\tscript\textra").encode())
        with self.assertRaises(ValidationError):
            request_from_payload(b"MFREQ\t1\t30\tWARLOCK\tA")

    def test_a_bogus_mode_in_a_picture_is_rejected_by_the_normal_validation(self):
        payload = "\t".join(["MFREQ", "42", "30", "WARLOCK", "Test Char", "x", "root"])
        with self.assertRaises(ValidationError):
            parse_request(request_from_payload(payload.encode()), CLASSES)


class OwnMacroGateTests(WatcherCase):
    """The agent-only rules apply to what a request changes, not to the player's own macros."""

    MINE = [{"name": "Mine Run", "body": "/run print('hi')"},
            {"name": "Mine Click", "body": "/click StaticPopup1Button1"},
            {"name": "Mine Addon", "body": "/dbm pull 10"}]

    def setUp(self):
        super().setUp()
        spec = json.loads(self.spec_text())
        spec["macros"].extend(dict(m, icon=134400) for m in self.MINE)
        self.sb.write_macros(spec)
        self.current = spec

    def candidate(self, *entries, edit=None):
        spec = json.loads(json.dumps(self.current))
        for entry in spec["macros"]:
            if edit and entry["name"] == edit[0]:
                entry["body"] = edit[1]
        spec["macros"].extend(entries)
        return json.dumps(spec)

    def test_a_request_is_not_refused_because_of_your_own_macros(self):
        _spec, _warnings, text = watcher._evaluate(self.settings, self.candidate(NEW_MACRO), "macro")
        self.assertIn("Corr", text)

    def test_the_new_macro_itself_is_still_checked(self):
        for body in ("/run print(1)", "/dbm pull 10", "/click X"):
            with self.subTest(body=body), self.assertRaises(ValidationError):
                watcher._evaluate(self.settings, self.candidate(dict(NEW_MACRO, body=body)), "macro")

    def test_an_edit_of_your_macro_is_checked_like_any_change(self):
        with self.assertRaises(ValidationError):
            watcher._evaluate(self.settings, self.candidate(edit=("Mine Run", "/run print(2)")), "macro")
        watcher._evaluate(self.settings, self.candidate(edit=("Mine Run", "/cast Fear")), "macro")

    def test_a_script_request_is_not_refused_because_of_your_own_macros_either(self):
        watcher._evaluate(self.settings, self.candidate(SCRIPT), "script")


class SpecKindTests(WatcherCase):
    def spec(self, *entries):
        spec = json.loads(self.spec_text())
        spec["macros"].extend(entries)
        return spec

    def test_a_script_entry_resolves_and_is_marked(self):
        resolved = resolve_spec(self.spec(SCRIPT), self.settings)
        pet = [m for m in resolved["chars"]["Test Char"] if m["name"] == "Pet Name"][0]
        self.assertTrue(pet["script"])
        self.assertNotIn("script", resolved["account"][0])

    def test_script_entries_get_the_script_checker_not_the_macro_checker(self):
        blocked = self.settings.blocked_commands
        resolve_spec(self.spec(SCRIPT), self.settings, blocked=blocked)  # /run is fine in a script
        plain = dict(SCRIPT, name="Plain")
        del plain["kind"]
        with self.assertRaises(ValidationError):  # the same body as a plain macro is refused for the agent
            resolve_spec(self.spec(plain), self.settings, blocked=blocked)
        resolve_spec(self.spec(plain), self.settings)  # hand-written macros keep today's freedom

    def test_a_dangerous_script_is_refused_when_the_spec_is_resolved(self):
        bad = dict(SCRIPT, body='/run SendChatMessage("hi","SAY")')
        with self.assertRaises(ValidationError) as caught:
            resolve_spec(self.spec(bad), self.settings)
        self.assertIn("sends chat messages", str(caught.exception))

    def test_a_script_macro_must_be_run_lines_only(self):
        for body in ("/cast Fear", "#showtooltip\n/run print(1)", "print(1)", "/script print(1)"):
            with self.assertRaises(ValidationError, msg=body):
                resolve_spec(self.spec(dict(SCRIPT, body=body)), self.settings)

    def test_unknown_kinds_are_refused(self):
        for kind in ("python", "", None, 3):
            with self.assertRaises(ValidationError, msg=repr(kind)):
                resolve_spec(self.spec(dict(SCRIPT, kind=kind)), self.settings)

    def test_the_action_limit_comes_from_the_settings(self):
        two = dict(SCRIPT, body='/run DoEmote("dance");PetRename("x")')
        with self.assertRaises(ValidationError):
            resolve_spec(self.spec(two), self.settings)
        relaxed = Settings.load(self.sb.project)
        import dataclasses
        relaxed = dataclasses.replace(relaxed, script_max_actions=2)
        resolve_spec(self.spec(two), relaxed)

    def test_the_game_data_marks_scripts_only(self):
        spec = self.spec(SCRIPT)
        (self.sb.project / "macros.json").write_text(json.dumps(spec), encoding="utf-8")
        text = render_data(load_spec(self.settings), 1, [])
        lines = [line for line in text.splitlines() if "Pet Name" in line or "Fear MO" in line]
        self.assertTrue(any("script = true" in line and "Pet Name" in line for line in lines))
        self.assertFalse(any("script = true" in line and "Fear MO" in line for line in lines))

    def test_changed_macros_and_the_diff_notice_a_kind_change(self):
        old = self.spec(dict(SCRIPT, name="X", kind="macro", body="/cast Fear"))
        new = json.loads(json.dumps(old))
        new["macros"][-1].update(kind="script", body="/run print(1)")
        self.assertEqual([m["name"] for m in changed_macros(old, new)], ["X"])
        self.assertEqual(diff_specs(old, new)["edited"], ["X"])
        self.assertEqual(changed_macros(old, old), [])

    def test_the_new_settings_have_sane_defaults_and_limits(self):
        self.assertEqual(self.settings.script_max_actions, 1)
        self.assertEqual(self.settings.script_context.name, "script-brief.md")
        self.assertEqual(self.settings.script_context.parent.name, "reference")


class ModeGateTests(WatcherCase):
    def candidate(self, *entries):
        spec = json.loads(self.spec_text())
        spec["macros"].extend(entries)
        return json.dumps(spec)

    def test_a_script_request_may_add_a_script(self):
        spec, _warnings, summary = watcher._evaluate(self.settings, self.candidate(SCRIPT), "script")
        self.assertEqual(summary, "Added: Pet Name")
        self.assertIn("Pet Name", [m["name"] for m in spec["macros"]])

    def test_a_script_request_may_not_add_a_plain_macro(self):
        with self.assertRaises(ValidationError) as caught:
            watcher._evaluate(self.settings, self.candidate(NEW_MACRO), "script")
        self.assertIn("only add or change scripts", str(caught.exception))

    def test_a_macro_request_may_not_add_a_script(self):
        with self.assertRaises(ValidationError) as caught:
            watcher._evaluate(self.settings, self.candidate(SCRIPT), "macro")
        self.assertIn("/mf script", str(caught.exception))
        with self.assertRaises(ValidationError):
            watcher._evaluate(self.settings, self.candidate(SCRIPT))  # the default mode is macro

    def test_a_macro_request_may_not_turn_a_macro_into_a_script(self):
        spec = json.loads(self.candidate())
        spec["macros"][0].update(kind="script", body="/run print(1)")
        with self.assertRaises(ValidationError):
            watcher._evaluate(self.settings, json.dumps(spec), "macro")

    def test_existing_scripts_do_not_get_in_the_way_of_macro_requests(self):
        (self.sb.project / "macros.json").write_text(self.candidate(SCRIPT), encoding="utf-8")
        watcher._evaluate(self.settings, self.candidate(NEW_MACRO), "macro")
        watcher._evaluate(self.settings, self.candidate(), "script")  # nothing changed: fine

    def test_a_dangerous_script_is_refused_even_in_script_mode(self):
        bad = dict(SCRIPT, body='/run C_Timer.After(1,print)')
        with self.assertRaises(ValidationError) as caught:
            watcher._evaluate(self.settings, self.candidate(bad), "script")
        self.assertIn("C_Timer", str(caught.exception))

    def test_removals_work_in_both_modes(self):
        spec = json.loads(self.candidate())
        spec["remove"] = spec.get("remove", []) + [{"name": "Fear MO", "char": "Test Char"}]
        spec["macros"] = [m for m in spec["macros"] if m["name"] != "Fear MO"]
        watcher._evaluate(self.settings, json.dumps(spec), "script")
        watcher._evaluate(self.settings, json.dumps(spec), "macro")


class EndToEndWithClaudeTests(WatcherCase):
    def run_request(self, mode, entry):
        process = fake_process(add_macro(entry), result="Done.\nREPLY: Made it.")
        runner = partial(agent.run_agent, runner=process)
        return watcher.handle_request(self.settings, request(mode=mode), None, self.state, runner)

    def test_a_script_request_installs_a_script_and_marks_it_in_the_game_data(self):
        self.assertEqual(self.run_request("script", SCRIPT), "[Added: Pet Name] Made it.")
        data = (self.sb.project / "build" / "MacroForge" / "MacroForgeData.lua").read_text(encoding="utf-8")
        self.assertIn("script = true", data)
        self.assertIn("PetRename", data)

    def test_a_script_hidden_in_a_macro_request_is_rejected_and_changes_nothing(self):
        before = self.spec_text()
        reply = self.run_request("macro", SCRIPT)
        self.assertTrue(reply.startswith("Rejected, nothing changed"), reply)
        self.assertEqual(self.spec_text(), before)

    def test_a_plain_macro_in_a_script_request_is_rejected(self):
        before = self.spec_text()
        reply = self.run_request("script", NEW_MACRO)
        self.assertTrue(reply.startswith("Rejected, nothing changed"), reply)
        self.assertEqual(self.spec_text(), before)

    def test_a_dangerous_script_is_rejected(self):
        reply = self.run_request("script", dict(SCRIPT, body='/run SendChatMessage("x","SAY")'))
        self.assertIn("sends chat messages", reply)


class EndToEndWithAChatModelTests(LlmCase):
    def test_a_script_request_through_the_chat_backend(self):
        self.server.reply(answer(upsert=[SCRIPT], reply="Renames your pet at random."))
        reply = watcher.handle_request(self.cfg, request(mode="script"), None, self.state)
        self.assertEqual(reply, "[Added: Pet Name] Renames your pet at random.")
        self.assertIn("Pet Name", self.spec_names())

    def test_the_chat_backend_cannot_smuggle_a_script_into_a_macro_request(self):
        for _ in range(3):
            self.server.reply(answer(upsert=[SCRIPT]))
        reply = watcher.handle_request(self.cfg, request(mode="macro"), None, self.state)
        self.assertTrue(reply.startswith("Rejected, nothing changed"), reply)
        self.assertNotIn("Pet Name", self.spec_names())

    def test_the_model_is_told_why_and_can_fix_a_refused_script(self):
        bad = dict(SCRIPT, body='/run SendChatMessage("x","SAY")')
        self.server.reply(answer(upsert=[bad]))
        self.server.reply(answer(upsert=[SCRIPT], reply="Fixed."))
        reply = watcher.handle_request(self.cfg, request(mode="script"), None, self.state)
        self.assertEqual(reply, "[Added: Pet Name] Fixed.")
        self.assertIn("sends chat messages", self.server.requests[1]["body"]["messages"][-1]["content"])


class ConfigTests(unittest.TestCase):
    def load(self, **config):
        from tests.harness import Sandbox
        sandbox = Sandbox(config=config)
        self.addCleanup(sandbox.cleanup)
        return Settings.load(sandbox.project)

    def test_limits(self):
        self.assertEqual(self.load(script_max_actions=3).script_max_actions, 3)
        for bad in (0, 6, "1", True):
            with self.assertRaises(ConfigError, msg=repr(bad)):
                self.load(script_max_actions=bad)

    def test_custom_script_context_file(self):
        settings = self.load(script_context_file="my-script-rules.md")
        self.assertEqual(settings.script_context.name, "my-script-rules.md")


if __name__ == "__main__":
    unittest.main()

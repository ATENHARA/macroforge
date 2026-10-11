"""What the AI is told for a script request: prompt, files, context file and the examples."""
from __future__ import annotations

import dataclasses
import json
import shutil
import unittest

from mforge import agent, llm, watcher
from mforge.errors import ConfigError
from mforge.validate import Request
from tests.test_agent_watcher import WatcherCase, request
from tests.test_llm import LlmCase, answer
from tests.test_script_mode import SCRIPT


def script_request() -> Request:
    return Request(id=5, text="print my coordinates", char="Test Char", cls="warlock", level=30,
                   mode="script")


def macro_request() -> Request:
    return dataclasses.replace(script_request(), mode="macro", text="a fear macro")


class ClaudePromptTests(WatcherCase):
    def test_the_script_prompt_is_the_script_one_with_the_live_rules(self):
        text = agent.system_prompt(self.settings, "script")
        for phrase in ("SCRIPT", '"kind": "script"', "script-brief.md", "script-examples.md",
                       "api-names.txt", "sends chat messages", "At most 1 state-changing",
                       "MF_", "C_Timer"):
            self.assertIn(phrase, text)
        self.assertNotIn("$rules", text)

    def test_the_macro_prompt_is_unchanged_and_never_mentions_scripts(self):
        text = agent.system_prompt(self.settings)
        self.assertEqual(text, agent.system_prompt(self.settings, "macro"))
        self.assertNotIn('"kind"', text)
        self.assertIn("agent-brief.md", text)

    def test_the_action_limit_in_the_prompt_follows_the_setting(self):
        relaxed = dataclasses.replace(self.settings, script_max_actions=3)
        self.assertIn("At most 3 state-changing", agent.system_prompt(relaxed, "script"))

    def test_the_command_line_carries_the_right_prompt(self):
        script = agent.build_command(self.settings, "claude", "script")
        macro = agent.build_command(self.settings, "claude")
        self.assertIn("SCRIPT", script[script.index("--append-system-prompt") + 1])
        self.assertNotIn("SCRIPT", macro[macro.index("--append-system-prompt") + 1])

    def test_the_request_text_says_it_is_a_script(self):
        self.assertIn("Request type: script", agent.build_prompt(script_request(), None))
        self.assertNotIn("Request type", agent.build_prompt(macro_request(), None))

    def test_the_workspace_for_a_script_has_the_script_files_only(self):
        workspace = agent.prepare_workspace(self.settings, script_request())
        self.addCleanup(shutil.rmtree, workspace, True)
        names = sorted(p.name for p in workspace.iterdir())
        self.assertEqual(names, ["icons.csv", "macros.json", "script-brief.md", "script-examples.md"])
        brief = (workspace / "script-brief.md").read_text(encoding="utf-8")
        self.assertIn("One key press, one action", brief)

    def test_the_workspace_for_a_macro_is_unchanged(self):
        workspace = agent.prepare_workspace(self.settings, macro_request())
        self.addCleanup(shutil.rmtree, workspace, True)
        names = sorted(p.name for p in workspace.iterdir())
        self.assertEqual(names, ["agent-brief.md", "icons.csv", "macros.json", "pets.json",
                                 "spells.json"])

    def test_a_custom_script_context_file_is_used_and_a_missing_one_is_an_error(self):
        (self.sb.project / "my-script-rules.md").write_text("MY SCRIPT RULES", encoding="utf-8")
        mine = dataclasses.replace(self.settings, script_context_file="my-script-rules.md")
        workspace = agent.prepare_workspace(mine, script_request())
        self.addCleanup(shutil.rmtree, workspace, True)
        self.assertEqual((workspace / "script-brief.md").read_text(encoding="utf-8"), "MY SCRIPT RULES")
        missing = dataclasses.replace(self.settings, script_context_file="nope.md")
        with self.assertRaises(ConfigError):
            agent.prepare_workspace(missing, script_request())

    def test_the_macro_context_file_setting_does_not_affect_scripts(self):
        (self.sb.project / "my-macro-rules.md").write_text("MACRO ONLY", encoding="utf-8")
        mine = dataclasses.replace(self.settings, agent_context_file="my-macro-rules.md")
        workspace = agent.prepare_workspace(mine, script_request())
        self.addCleanup(shutil.rmtree, workspace, True)
        self.assertNotIn("MACRO ONLY", (workspace / "script-brief.md").read_text(encoding="utf-8"))


class ChatPromptTests(LlmCase):
    def setUp(self):
        super().setUp()
        spec = json.loads((self.sb.project / "macros.json").read_text(encoding="utf-8"))
        spec["macros"].append(SCRIPT)
        (self.sb.project / "macros.json").write_text(json.dumps(spec), encoding="utf-8")

    def messages(self, req):
        return [m["content"] for m in llm.build_messages(self.cfg, req, None)]

    def test_the_system_message_for_scripts_has_the_contract_rules_brief_and_examples(self):
        system, _user = self.messages(script_request())
        for phrase in ('"kind": "script"', "At most 4 entries", "sends chat messages",
                       "At most 1 state-changing", "## Entry format", "### Show your coordinates",
                       "PetRename"):
            self.assertIn(phrase, system)
        self.assertNotIn("$rules", system)

    def test_the_system_message_for_macros_is_unchanged(self):
        system, _user = self.messages(macro_request())
        self.assertIn("## Spec entry", system)
        self.assertNotIn("script-examples", system)
        self.assertNotIn("### Show your coordinates", system)

    def test_a_script_request_sees_only_scripts_and_no_pet_table(self):
        _system, user = self.messages(script_request())
        self.assertIn("Current scripts", user)
        self.assertIn("Pet Name", user)
        self.assertNotIn("Fear MO", user)
        self.assertNotIn("Pet abilities by family", user)

    def test_a_macro_request_still_sees_every_macro_including_scripts_marked_as_such(self):
        _system, user = self.messages(macro_request())
        self.assertIn("Current macros", user)
        self.assertIn("Fear MO", user)
        self.assertIn('"kind": "script"', user)

    def test_at_most_four_scripts_per_answer(self):
        many = [dict(SCRIPT, name=f"S{i}") for i in range(llm.MAX_SCRIPT_UPSERTS + 1)]
        for _ in range(3):
            self.server.reply(answer(upsert=many))
        reply = watcher.handle_request(self.cfg, request(mode="script"), None, self.state)
        self.assertTrue(reply.startswith("Rejected, nothing changed"), reply)
        self.assertNotIn("S0", self.spec_names())

    def test_four_scripts_in_one_answer_are_fine(self):
        four = [dict(SCRIPT, name=f"S{i}") for i in range(llm.MAX_SCRIPT_UPSERTS)]
        self.server.reply(answer(upsert=four, reply="Made four."))
        reply = watcher.handle_request(self.cfg, request(mode="script"), None, self.state)
        self.assertEqual(reply, "[Added: S0, S1, S2, S3] Made four.")

    def test_the_kind_field_survives_the_merge(self):
        merged = json.loads(llm.merge_change(
            {"macros": []}, {"upsert": [SCRIPT], "remove": [], "reply": ""}))
        self.assertEqual(merged["macros"][0]["kind"], "script")


if __name__ == "__main__":
    unittest.main()

"""Where new macros go: this character or all of them, chosen in the window and enforced by the PC."""
from __future__ import annotations

import json
import unittest

from mforge import agent, llm, watcher
from mforge.assets import read_text
from mforge.errors import ValidationError
from mforge.shots import request_from_payload
from mforge.validate import SCOPES, Request, parse_request
from tests.test_agent_watcher import NEW_MACRO, WatcherCase, add_macro, fake_process, request

CLASSES = frozenset({"warlock"})
BASE = "\t".join(["MFREQ", "42", "30", "WARLOCK", "Test Char", "make it"])


def raw(**over):
    base = {"id": 7, "text": "x", "char": "Test Char", "class": "WARLOCK", "level": 30}
    base.update(over)
    return base


class RequestScopeTests(unittest.TestCase):
    def test_the_default_is_this_character_so_old_requests_still_work(self):
        self.assertEqual(parse_request(raw(), CLASSES).scope, "char")
        self.assertEqual(SCOPES, ("char", "account"))

    def test_account_is_accepted_and_anything_else_is_not(self):
        self.assertEqual(parse_request(raw(scope="account"), CLASSES).scope, "account")
        for bad in ("", "all", "ACCOUNT", None, 5, ["account"], True):
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                parse_request(raw(scope=bad), CLASSES)

    def test_the_strip_carries_the_scope_after_the_mode(self):
        self.assertEqual(request_from_payload(BASE.encode())["scope"], "char")
        self.assertEqual(request_from_payload((BASE + "\tscript").encode())["scope"], "char")
        both = request_from_payload((BASE + "\tmacro\taccount").encode())
        self.assertEqual((both["mode"], both["scope"]), ("macro", "account"))
        both = request_from_payload((BASE + "\tscript\taccount").encode())
        self.assertEqual((both["mode"], both["scope"]), ("script", "account"))
        with self.assertRaises(ValidationError):
            request_from_payload((BASE + "\tmacro\taccount\textra").encode())

    def test_a_bogus_scope_in_a_picture_is_rejected_by_the_normal_validation(self):
        parsed = request_from_payload((BASE + "\tmacro\troot").encode())
        with self.assertRaises(ValidationError):
            parse_request(parsed, CLASSES)


class PromptTests(WatcherCase):
    def req(self, **over):
        base = {"id": 1, "text": "make it", "char": "Test Char", "cls": "warlock", "level": 30}
        base.update(over)
        return Request(**base)

    def test_the_ai_is_told_where_the_player_wants_the_macro(self):
        mine = agent.build_prompt(self.req(), None)
        self.assertIn('Save to: this character: new entries get "char": "Test Char".', mine)
        everyone = agent.build_prompt(self.req(scope="account"), None)
        self.assertIn('Save to: all characters (account-wide): leave "char" out', everyone)
        self.assertNotIn("Save to: this character", everyone)

    def test_both_backends_and_both_modes_say_the_same(self):
        for mode in ("macro", "script"):
            with self.subTest(mode=mode):
                self.assertIn("all characters", agent.build_prompt(self.req(mode=mode, scope="account"), None))
        user = llm.build_messages(self.settings, self.req(scope="account"), None)[1]["content"]
        self.assertIn("Save to: all characters", user)

    def test_every_prompt_defers_to_the_save_to_line(self):
        for name in ("prompt.md", "prompt_script.md", "prompt_chat.md", "prompt_chat_script.md"):
            text = read_text(name)
            with self.subTest(prompt=name):
                self.assertIn('stated in the request as "Save to:"', text)
                self.assertNotIn("unless they ask for an account-wide", text)


class PlacementTests(WatcherCase):
    """The PC puts new macros where the player chose, whatever the model wrote."""

    def candidate(self, *entries):
        spec = json.loads(self.spec_text())
        spec["macros"].extend(entries)
        return json.dumps(spec)

    def place(self, scope, *entries, char="Test Char"):
        spec, _warnings, _summary = watcher._evaluate(self.settings, self.candidate(*entries),
                                                      "macro", "add one", scope, char)
        return {m["name"]: m for m in spec["macros"]}

    def test_account_removes_the_character_from_a_new_macro(self):
        macros = self.place("account", dict(NEW_MACRO, char="Test Char"))
        self.assertNotIn("char", macros[NEW_MACRO["name"]])

    def test_char_gives_a_new_macro_the_requesters_name_even_if_the_model_left_it_out(self):
        plain = {k: v for k, v in NEW_MACRO.items() if k != "char"}
        macros = self.place("char", plain)
        self.assertEqual(macros[NEW_MACRO["name"]]["char"], "Test Char")

    def test_char_overrides_a_wrong_name_too(self):
        macros = self.place("char", dict(NEW_MACRO, char="Someone Else"))
        self.assertEqual(macros[NEW_MACRO["name"]]["char"], "Test Char")

    def test_an_empty_scope_leaves_the_model_alone(self):
        macros = self.place("", dict(NEW_MACRO, char="Someone Else"))
        self.assertEqual(macros[NEW_MACRO["name"]]["char"], "Someone Else")
        macros = self.place("bogus", dict(NEW_MACRO, char="Someone Else"))
        self.assertEqual(macros[NEW_MACRO["name"]]["char"], "Someone Else")

    def test_existing_macros_stay_where_they_are_even_when_they_are_edited(self):
        spec = json.loads(self.spec_text())
        mine = [m for m in spec["macros"] if m["name"] == "Tap"][0]  # an account-wide macro
        mine["body"] = "/cast Life Tap"
        spec["macros"] = [m for m in spec["macros"] if m["name"] != "Tap"] + [mine]
        result, _w, _s = watcher._evaluate(self.settings, json.dumps(spec), "macro", "edit it", "char",
                                           "Test Char")
        edited = [m for m in result["macros"] if m["name"] == "Tap"][0]
        self.assertNotIn("char", edited)

    def test_all_new_macros_of_one_answer_are_placed(self):
        second = dict(NEW_MACRO, name="Second", char="Test Char")
        macros = self.place("account", dict(NEW_MACRO), second)
        self.assertNotIn("char", macros["Second"])
        self.assertNotIn("char", macros[NEW_MACRO["name"]])

    def test_end_to_end_the_macro_is_saved_for_all_characters(self):
        reply = self.handle(request(rid=201, scope="account"),
                            runner=fake_process(add_macro(dict(NEW_MACRO, char="Test Char"))))
        self.assertIn(f"Added: {NEW_MACRO['name']}", reply)
        saved = [m for m in json.loads(self.spec_text())["macros"] if m["name"] == NEW_MACRO["name"]]
        self.assertEqual(len(saved), 1)
        self.assertNotIn("char", saved[0])
        data = (self.settings.addons / "MacroForge" / "MacroForgeData.lua").read_text(encoding="utf-8")
        account_part = data.split("account = {")[1].split("chars = {")[0]
        self.assertIn(f'name = "{NEW_MACRO["name"]}"', account_part)

    def test_end_to_end_the_default_keeps_it_on_the_character(self):
        reply = self.handle(request(rid=202), runner=fake_process(add_macro(
            {k: v for k, v in NEW_MACRO.items() if k != "char"})))
        self.assertIn(f"Added: {NEW_MACRO['name']}", reply)
        saved = [m for m in json.loads(self.spec_text())["macros"] if m["name"] == NEW_MACRO["name"]]
        self.assertEqual(saved[0]["char"], "Test Char")


if __name__ == "__main__":
    unittest.main()

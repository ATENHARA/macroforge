"""Several WoW accounts: the addon is shared, MacroForge reads the saved data of all of them."""
from __future__ import annotations

import dataclasses
import os
import unittest
from functools import partial

from mforge import agent, watcher
from mforge.cache import stage_caches
from mforge.errors import ValidationError
from mforge.spec import load_spec
from tests.harness import ACCOUNT
from tests.test_agent_watcher import NEW_MACRO, WatcherCase, add_macro, fake_process


def export_lua(char: str, spell: str) -> str:
    return (f'MacroForgeExport = {{ ["char"] = "{char}", '
            f'["spells"] = {{ {{ ["name"] = "{spell}" }} }} }}\n')


class AccountsCase(WatcherCase):
    def setUp(self):
        super().setUp()
        self.root = self.sb.wow / "WTF" / "Account"
        self.other_vars = self.root / "OTHER#2" / "SavedVariables"
        self.other_vars.mkdir(parents=True)
        (self.sb.saved_vars / "MacroForge.lua").write_text(export_lua("Testy", "Fear"), encoding="utf-8")
        (self.other_vars / "MacroForge.lua").write_text(export_lua("Other", "Smite"), encoding="utf-8")
        old, new = 1_600_000_000, 1_790_000_000
        for path, stamp in ((self.root / ACCOUNT, old), (self.sb.saved_vars, old),
                            (self.root / "OTHER#2", new), (self.other_vars, new)):
            os.utime(path, (stamp, stamp))
        self.all = dataclasses.replace(self.settings, account="")


class ExportLookupTests(AccountsCase):
    def test_every_account_is_searched_newest_first(self):
        self.assertEqual([d.name for d in self.all.account_dirs], ["OTHER#2", ACCOUNT])

    def test_the_export_of_the_requesting_character_is_used(self):
        self.assertEqual(watcher.find_export(self.all, "Testy Mage")["char"], "Testy")
        self.assertEqual(watcher.find_export(self.all, "Other One")["char"], "Other")

    def test_an_unknown_character_gets_the_most_recently_used_accounts_export(self):
        self.assertEqual(watcher.find_export(self.all, "Nobody Known")["char"], "Other")
        self.assertEqual(watcher.find_export(self.all)["char"], "Other")

    def test_a_pinned_account_only_looks_at_itself(self):
        self.assertEqual(self.settings.account_dirs, [self.root / ACCOUNT])
        self.assertEqual(watcher.find_export(self.settings, "Other One")["char"], "Testy")

    def test_accounts_without_saved_data_are_skipped_quietly(self):
        (self.other_vars / "MacroForge.lua").unlink()
        self.assertEqual(watcher.find_export(self.all, "Other One")["char"], "Testy")
        (self.sb.saved_vars / "MacroForge.lua").unlink()
        self.assertIsNone(watcher.find_export(self.all, "Other One"))


class RequestsFromAnyAccountTests(AccountsCase):
    def test_a_request_saved_by_the_second_account_is_answered_with_its_own_spells(self):
        (self.other_vars / "MacroForge.lua").write_text(
            export_lua("Other", "Smite") +
            'MacroForgeRequests = { { ["id"] = 4001, ["text"] = "add a macro", '
            '["char"] = "Other One", ["class"] = "WARLOCK", ["level"] = 30 } }\n', encoding="utf-8")
        process = fake_process(add_macro(dict(NEW_MACRO, char="Other One")))
        runner = partial(agent.run_agent, runner=process)
        self.assertEqual(watcher.process_new_requests(self.all, self.state, runner), 1)
        self.assertIn(4001, self.state.handled)
        prompt = process.calls[0]["input"]
        self.assertIn("Smite", prompt)
        self.assertNotIn("Fear", prompt)  # the other account's spells are not mixed in

    def test_a_screenshot_request_gets_the_matching_accounts_spells(self):
        from tests.test_shots import FIELDS  # a request from "Test Char"
        del FIELDS
        raw = {"id": 5, "text": "x", "char": "Other One", "class": "WARLOCK", "level": 30}
        process = fake_process(add_macro(dict(NEW_MACRO, char="Other One")))
        watcher.handle_request(self.all, raw, watcher.find_export(self.all, raw["char"]), self.state,
                               partial(agent.run_agent, runner=process))
        self.assertIn("Smite", process.calls[0]["input"])


class CacheTests(AccountsCase):
    def test_the_raw_cache_route_refuses_to_guess_between_accounts(self):
        with self.assertRaises(ValidationError) as caught:
            stage_caches(self.all, load_spec(self.all), False)
        self.assertIn("several account folders", str(caught.exception))
        self.assertIn("account", str(caught.exception))


if __name__ == "__main__":
    unittest.main()

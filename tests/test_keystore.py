"""The API key vault: Windows Credential Manager, and how the chat backend finds a key."""
from __future__ import annotations

import dataclasses
import os
import sys
import unittest
import uuid
from unittest import mock

from mforge import keystore, llm
from mforge.errors import AgentError
from tests.test_agent_watcher import WatcherCase

WINDOWS = sys.platform == "win32"


@unittest.skipUnless(WINDOWS, "needs Windows Credential Manager")
class RealVaultTests(unittest.TestCase):
    """Uses the real vault with a throwaway entry that is always removed."""

    def setUp(self):
        self.name = "TEST_" + uuid.uuid4().hex[:10]
        self.addCleanup(keystore.delete, self.name)

    def test_save_read_overwrite_and_delete(self):
        self.assertEqual(keystore.load(self.name), "")
        keystore.store(self.name, "sk-first-\u00c4\u00d6-0123456789")
        self.assertEqual(keystore.load(self.name), "sk-first-\u00c4\u00d6-0123456789")
        keystore.store(self.name, "second-value")
        self.assertEqual(keystore.load(self.name), "second-value")
        self.assertTrue(keystore.delete(self.name))
        self.assertFalse(keystore.delete(self.name))
        self.assertEqual(keystore.load(self.name), "")

    def test_entries_are_named_so_the_user_can_find_them(self):
        self.assertEqual(keystore.TARGET_PREFIX, "MacroForge:")

    def test_empty_and_oversized_values_are_refused(self):
        for bad in ("", "x" * (keystore.MAX_KEY_CHARS + 1)):
            with self.assertRaises(keystore.KeystoreError):
                keystore.store(self.name, bad)
        self.assertEqual(keystore.load(self.name), "")


class NotWindowsTests(unittest.TestCase):
    def test_elsewhere_reading_gives_nothing_and_saving_explains(self):
        with mock.patch.object(keystore.sys, "platform", "linux"):
            self.assertEqual(keystore.load("ANY"), "")
            with self.assertRaises(keystore.KeystoreError):
                keystore.store("ANY", "value")


class KeyLookupTests(WatcherCase):
    def setUp(self):
        super().setUp()
        env = mock.patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("MACROFORGE_API_KEY", None)
        self.remote = dataclasses.replace(self.settings, agent_backend="openai", agent_model="m",
                                          llm_base_url="https://api.example.org/v1")

    def test_the_environment_variable_wins(self):
        os.environ["MACROFORGE_API_KEY"] = "from-environment"
        with mock.patch.object(keystore, "load", return_value="from-vault") as load:
            self.assertEqual(llm.api_key(self.remote), "from-environment")
        load.assert_not_called()

    def test_otherwise_the_vault_is_used(self):
        with mock.patch.object(keystore, "load", return_value="from-vault") as load:
            self.assertEqual(llm.api_key(self.remote), "from-vault")
        load.assert_called_once_with("MACROFORGE_API_KEY")

    def test_no_key_anywhere_for_an_online_service_says_how_to_fix_it(self):
        with mock.patch.object(keystore, "load", return_value=""):
            with self.assertRaises(AgentError) as caught:
                llm.api_key(self.remote)
        self.assertIn("setup", str(caught.exception))
        self.assertIn("MACROFORGE_API_KEY", str(caught.exception))

    def test_a_vault_error_is_treated_as_no_key_not_a_crash(self):
        local = dataclasses.replace(self.remote, llm_base_url="http://localhost:1234/v1")
        with mock.patch.object(keystore, "load", side_effect=keystore.KeystoreError("boom")):
            self.assertEqual(llm.api_key(local), "")
            with self.assertRaises(AgentError):
                llm.api_key(self.remote)

    def test_a_different_variable_name_uses_a_different_vault_entry(self):
        custom = dataclasses.replace(self.remote, llm_api_key_env="MY_AI_KEY")
        with mock.patch.object(keystore, "load", return_value="k") as load:
            llm.api_key(custom)
        load.assert_called_once_with("MY_AI_KEY")


if __name__ == "__main__":
    unittest.main()

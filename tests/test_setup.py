"""The guided setup: finding the game, the questions, the install steps and the setup command."""
from __future__ import annotations

import json
import shutil
import unittest
from pathlib import Path
from unittest import mock

from mforge import setup as setup_mod
from mforge.errors import MacroForgeError, ValidationError
from tests.harness import ACCOUNT, FIXTURES, PRODUCT_VERSION, Sandbox


class SetupCase(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox()
        self.addCleanup(self.sb.cleanup)
        (self.sb.project / "config.json").unlink()  # a friend starts without any config
        self.lines: list[str] = []
        self.fetched: list[object] = []
        self.answers: list[str] = []

    def ask(self, prompt: str) -> str:
        self.lines.append(prompt)
        return self.answers.pop(0) if self.answers else ""

    def wizard(self, **kw):
        kw.setdefault("fetch", lambda settings, **opts: self.fetched.append(opts))
        kw.setdefault("roots", [self.sb.wow_root])
        return setup_mod.Wizard(self.sb.project, ask=self.ask, out=self.lines.append, **kw)

    def config(self) -> dict:
        return json.loads((self.sb.project / "config.json").read_text(encoding="utf-8"))

    def text(self) -> str:
        return "\n".join(self.lines)


class FindingTheGameTests(SetupCase):
    def test_finds_the_client_folder_with_its_version(self):
        games = setup_mod.find_games([self.sb.wow_root])
        self.assertEqual([g.path for g in games], [self.sb.wow])
        self.assertEqual(games[0].version, PRODUCT_VERSION)

    def test_ignores_folders_that_are_not_clients(self):
        (self.sb.wow_root / "Data").mkdir()
        (self.sb.wow_root / "_retail_").mkdir()  # no Interface or WTF inside
        (self.sb.wow_root / "_classic_").mkdir()
        (self.sb.wow_root / "_classic_" / "Interface").mkdir()
        names = [g.path.name for g in setup_mod.find_games([self.sb.wow_root])]
        self.assertEqual(names, ["_classic_", "_classic_beta_"])

    def test_missing_roots_and_a_damaged_build_info_are_harmless(self):
        self.assertEqual(setup_mod.find_games([self.sb.tmp / "nowhere"]), [])
        (self.sb.wow_root / ".build.info").write_text("junk", encoding="utf-8")
        self.assertEqual(setup_mod.read_versions(self.sb.wow_root), {})
        self.assertEqual(setup_mod.find_games([self.sb.wow_root])[0].version, "")

    def test_accounts(self):
        self.assertEqual(setup_mod.list_accounts(self.sb.wow), [ACCOUNT])
        (self.sb.wow / "WTF" / "Account" / "SavedVariables").mkdir()
        (self.sb.wow / "WTF" / "Account" / "OTHER#2").mkdir()
        self.assertEqual(setup_mod.list_accounts(self.sb.wow), ["OTHER#2", ACCOUNT])
        self.assertEqual(setup_mod.list_accounts(self.sb.tmp), [])


class WizardTests(SetupCase):
    def test_a_full_run_installs_everything_and_writes_the_config(self):
        self.assertEqual(self.wizard(assume_yes=True).run(skip_fetch=True, requests=False), 0)
        self.assertEqual(self.config(), {"wow_dir": str(self.sb.wow), "account": ACCOUNT})
        self.assertTrue((self.sb.addons / "MacroForge" / "MacroForge.lua").exists())
        self.assertEqual(len(list(self.sb.addons.glob("MacroForge_S*"))), setup_mod.SLOT_COUNT)
        self.assertIn("fully quit", self.text())

    def test_existing_config_keys_and_macros_are_kept(self):
        (self.sb.project / "config.json").write_text(json.dumps({"agent_model": "x"}))
        before = (self.sb.project / "macros.json").read_text(encoding="utf-8")
        self.wizard(assume_yes=True).run(skip_fetch=True, requests=False)
        self.assertEqual(self.config()["agent_model"], "x")
        self.assertEqual((self.sb.project / "macros.json").read_text(encoding="utf-8"), before)

    def test_macros_json_is_created_from_the_example_when_missing(self):
        (self.sb.project / "macros.json").unlink()
        (self.sb.project / "macros.example.json").write_text(
            json.dumps({"classes": {}, "remove": [], "macros": []}), encoding="utf-8")
        self.wizard(assume_yes=True).run(skip_fetch=True, requests=False)
        self.assertTrue((self.sb.project / "macros.json").exists())

    def test_running_twice_is_fine(self):
        for _ in range(2):
            self.assertEqual(self.wizard(assume_yes=True).run(skip_fetch=True, requests=False), 0)

    def test_menu_with_two_game_folders(self):
        (self.sb.wow_root / "_classic_").mkdir()
        (self.sb.wow_root / "_classic_" / "Interface").mkdir()
        self.answers = ["x", "9", "2"]  # junk, out of range, then the second entry
        chosen = self.wizard().pick_game(None)
        self.assertEqual(chosen.name, "_classic_beta_")
        self.assertIn("Please type one of the numbers", self.text())

    def test_two_accounts_ask_which_one(self):
        (self.sb.wow / "WTF" / "Account" / "OTHER#2").mkdir()
        self.answers = ["1"]
        self.assertEqual(self.wizard().pick_account(self.sb.wow, None), "OTHER#2")

    def test_a_brand_new_game_without_an_account_folder_still_installs(self):
        import shutil
        shutil.rmtree(self.sb.wow / "WTF")
        self.assertEqual(self.wizard(assume_yes=True).run(skip_fetch=True, requests=False), 0)
        self.assertNotIn("account", self.config())
        self.assertTrue((self.sb.addons / "MacroForge" / "MacroForge.lua").exists())
        self.assertIn("not created yet", self.text())

    def test_unknown_game_folder_asks_for_a_path(self):
        self.answers = ['"%s"' % self.sb.wow]
        chosen = self.wizard(roots=[self.sb.tmp / "nothing"]).pick_game(None)
        self.assertEqual(chosen, self.sb.wow)
        self.assertIn("Could not find World of Warcraft", self.text())

    def test_wrong_folder_is_refused(self):
        with self.assertRaises(ValidationError):
            self.wizard().pick_game(str(self.sb.tmp))

    def test_unwritable_addons_folder_explains_administrator(self):
        real = Path.write_text

        def refuse(path, *args, **kwargs):
            if path.name == ".macroforge-write-test":
                raise PermissionError("access denied")
            return real(path, *args, **kwargs)

        with mock.patch.object(Path, "write_text", refuse):
            with self.assertRaises(ValidationError) as caught:
                self.wizard(assume_yes=True).run(skip_fetch=True, requests=False)
        self.assertIn("administrator", str(caught.exception))

    # -- game data: bundled pack first, downloads only when needed -------------------------------
    def make_pack(self, build: str = PRODUCT_VERSION) -> None:
        shutil.copytree(FIXTURES / "data", self.sb.project / "datapack")
        (self.sb.project / "datapack" / "BUILD").write_text(build + "\n", encoding="utf-8")
        (self.sb.project / "datapack" / "NOTICE.md").write_text("notice", encoding="utf-8")

    def drop_data(self) -> None:
        shutil.rmtree(self.sb.project / "data")

    def recorder(self):
        calls: list[dict] = []

        def fetch(settings, **kw):
            calls.append(kw)

        return calls, fetch

    def test_the_bundled_pack_is_copied_without_downloading(self):
        self.make_pack()
        self.drop_data()
        calls, fetch = self.recorder()
        self.assertEqual(self.wizard(assume_yes=True, fetch=fetch).run(requests=False), 0)
        self.assertEqual(calls, [])
        data = self.sb.project / "data"
        self.assertTrue((data / "icons.csv").exists())
        self.assertEqual((data / "BUILD").read_text(encoding="utf-8").strip(), PRODUCT_VERSION)
        self.assertFalse((data / "NOTICE.md").exists())
        self.assertIn("bundled", self.text())

    def test_existing_data_for_this_build_is_left_alone(self):
        (self.sb.project / "data" / "BUILD").write_text(PRODUCT_VERSION, encoding="utf-8")
        calls, fetch = self.recorder()
        self.wizard(assume_yes=True, fetch=fetch).run(requests=False)
        self.assertEqual(calls, [])

    def test_a_newer_client_refreshes_only_the_tables(self):
        self.make_pack(build="1.60.1.1")
        self.drop_data()
        calls, fetch = self.recorder()
        self.wizard(assume_yes=True, fetch=fetch).run(requests=False)
        self.assertEqual(calls, [{"no_icons": True}])

    def test_a_failed_refresh_falls_back_to_the_bundled_tables(self):
        self.make_pack(build="1.60.1.1")
        self.drop_data()

        def broken(settings, **kw):
            raise ValidationError("wago.tools does not have this build yet")

        self.assertEqual(self.wizard(assume_yes=True, fetch=broken).run(requests=False), 0)
        self.assertIn("Could not refresh", self.text())
        self.assertTrue((self.sb.project / "data" / "icons.csv").exists())

    def test_without_a_pack_or_data_everything_is_downloaded(self):
        saved = (self.sb.project / "data" / "icons.csv").read_bytes()
        self.drop_data()

        def download(settings, **kw):
            self.fetched.append(kw)
            (self.sb.project / "data").mkdir(exist_ok=True)
            for source in (FIXTURES / "data").iterdir():
                shutil.copy2(source, self.sb.project / "data" / source.name)
            (self.sb.project / "data" / "BUILD").write_text(PRODUCT_VERSION, encoding="utf-8")

        self.wizard(assume_yes=True, fetch=download).run(requests=False)
        self.assertEqual(self.fetched, [{}])
        self.assertTrue(saved)

    def test_fetch_failure_without_any_data_stops_with_a_message(self):
        self.drop_data()

        def broken(settings, **kw):
            raise ValidationError("wago.tools is down")

        with self.assertRaises(ValidationError):
            self.wizard(assume_yes=True, fetch=broken).run(requests=False)


class RequestsStepTests(SetupCase):
    def test_without_the_claude_cli_the_step_explains_how_to_get_it(self):
        with mock.patch.object(setup_mod.agent, "find_claude", side_effect=MacroForgeError("no")):
            self.wizard(assume_yes=True).run(skip_fetch=True)
        self.assertIn("install.ps1", self.text())

    def test_with_the_cli_the_watcher_is_started_and_set_to_run_at_login(self):
        with mock.patch.object(setup_mod.agent, "find_claude", return_value="claude.exe"), \
                mock.patch.object(setup_mod.startup, "install_startup",
                                  return_value=Path("launcher.vbs")) as installer:
            self.wizard(assume_yes=True).run(skip_fetch=True)
        args = installer.call_args.args
        self.assertEqual(args[1].name, "macroforge.py")
        self.assertTrue(args[2])  # start now
        self.assertIn("watcher is running", self.text())

    def test_declining_leaves_the_watcher_alone(self):
        self.answers = ["n"]
        with mock.patch.object(setup_mod.agent, "find_claude", return_value="claude.exe"), \
                mock.patch.object(setup_mod.startup, "install_startup") as installer:
            self.wizard().run(wow_dir=str(self.sb.wow), account=ACCOUNT, skip_fetch=True)
        installer.assert_not_called()


class ConfigTests(SetupCase):
    def test_damaged_config_is_reported_not_overwritten(self):
        (self.sb.project / "config.json").write_text("{not json", encoding="utf-8")
        with self.assertRaises(ValidationError):
            setup_mod.write_config(self.sb.project, {"a": 1})
        self.assertEqual((self.sb.project / "config.json").read_text(encoding="utf-8"), "{not json")


class SetupCommandTests(SetupCase):
    def test_setup_command_end_to_end(self):
        result = self.sb.run("setup", "--yes", "--skip-fetch", "--no-requests",
                             "--wow-dir", str(self.sb.wow), "--account", ACCOUNT,
                             env={"MACROFORGE_HOME": str(self.sb.project)})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("fully quit", result.stdout)
        self.assertTrue((self.sb.addons / "MacroForge_S120").is_dir())

    def test_setup_command_reports_problems_without_a_traceback(self):
        result = self.sb.run("setup", "--yes", "--wow-dir", str(self.sb.tmp),
                             env={"MACROFORGE_HOME": str(self.sb.project)})
        self.assertEqual(result.returncode, 1)
        self.assertNotIn("Traceback", result.stderr)

    def test_setup_cmd_exists_with_windows_line_endings(self):
        data = (Path(__file__).resolve().parent.parent / "setup.cmd").read_bytes()
        self.assertIn(b"macroforge.py setup", data)
        self.assertNotIn(b"\n", data.replace(b"\r\n", b""))


if __name__ == "__main__":
    unittest.main()

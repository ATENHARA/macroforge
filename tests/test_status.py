"""``status``: the read-only checks behind an update (game data, probe, addon, watcher, ...)."""
from __future__ import annotations

import json
import os
import time
from unittest import mock

from mforge import status
from mforge.install import build_addon, install
from mforge.spec import load_spec
from tests.harness import PRODUCT_VERSION
from tests.test_agent_watcher import WatcherCase

VERSION, BUILD = PRODUCT_VERSION.rsplit(".", 1)


class StatusCase(WatcherCase):
    def setUp(self):
        super().setUp()
        env = mock.patch.dict(os.environ, {"APPDATA": str(self.sb.tmp / "appdata")})
        env.start()
        self.addCleanup(env.stop)

    def probe(self, version=VERSION, build=BUILD, toc=16001):
        self.settings.data.mkdir(exist_ok=True)
        (self.settings.data / "probe.json").write_text(json.dumps(
            {"build": {"version": version, "build": build, "toc": toc}}), encoding="utf-8")

    def game_data(self, build=PRODUCT_VERSION):
        (self.settings.data / "BUILD").write_text(build + "\n", encoding="utf-8")

    def installed(self):
        build_addon(self.settings, load_spec(self.settings), [], report=lambda _m: None)
        install(self.settings)


class GameAndProbeTests(StatusCase):
    def test_game_data_for_another_build_needs_a_fetch(self):
        check = status.check_game_data(self.settings)
        self.assertFalse(check.ok)
        self.assertIn("missing", check.detail)
        self.assertIn("fetch", check.action)
        self.game_data("1.60.1.1")
        self.assertIn("the downloaded data is 1.60.1.1", status.check_game_data(self.settings).detail)
        self.game_data()
        self.assertTrue(status.check_game_data(self.settings).ok)

    def test_the_probe_must_be_from_this_build(self):
        self.assertFalse(status.check_probe(self.settings).ok)
        self.probe(build="1")
        check = status.check_probe(self.settings)
        self.assertFalse(check.ok)
        self.assertIn("/mf probe", check.action)
        self.probe()
        self.assertTrue(status.check_probe(self.settings).ok)

    def test_a_damaged_probe_file_counts_as_no_probe(self):
        self.settings.data.mkdir(exist_ok=True)
        (self.settings.data / "probe.json").write_text("{nope", encoding="utf-8")
        self.assertIn("no probe", status.check_probe(self.settings).detail)

    def test_the_interface_number_must_match_the_client(self):
        self.assertTrue(status.check_interface(self.settings).ok)  # nothing to compare yet
        self.probe(toc=16001)
        self.assertTrue(status.check_interface(self.settings).ok)
        self.probe(toc=16002)
        check = status.check_interface(self.settings)
        self.assertFalse(check.ok)
        self.assertIn("16002", check.detail)
        self.assertIn("python macroforge.py update", check.action)
        self.assertIn("Load out of date AddOns", check.action)

    def test_the_compat_report_for_the_probed_number(self):
        self.probe(toc=16001)
        self.assertTrue(status.check_compat_report(self.settings).ok)
        self.probe(toc=99999)
        check = status.check_compat_report(self.settings)
        self.assertFalse(check.ok)
        self.assertEqual(check.action, "python macroforge.py report")


class AddonTests(StatusCase):
    def test_an_addon_that_was_never_installed(self):
        check = status.check_addon_installed(self.settings)
        self.assertFalse(check.ok)
        self.assertIn("install", check.action)

    def test_an_installed_addon_matches_and_a_changed_source_file_is_named(self):
        self.installed()
        self.assertTrue(status.check_addon_installed(self.settings).ok)
        source = self.settings.addon_src / "UI.lua"
        source.write_text(source.read_text(encoding="utf-8") + "\n-- changed\n", encoding="utf-8")
        check = status.check_addon_installed(self.settings)
        self.assertFalse(check.ok)
        self.assertIn("UI.lua", check.detail)

    def test_the_generated_data_files_do_not_count(self):
        self.installed()
        (self.settings.addons / "MacroForge" / "MacroForgeData.lua").write_text("x", encoding="utf-8")
        self.assertTrue(status.check_addon_installed(self.settings).ok)

    def test_slots(self):
        self.assertTrue(status.check_slots(self.settings).ok)  # WatcherCase makes two
        for folder in status.slot_dirs(self.settings):
            for item in folder.iterdir():
                item.unlink()
            folder.rmdir()
        check = status.check_slots(self.settings)
        self.assertFalse(check.ok)
        self.assertIn("slots", check.action)


class ContentTests(StatusCase):
    def test_macro_warnings_are_counted(self):
        spec = json.loads(self.spec_text())
        spec["macros"].append({"name": "Odd", "icon": 134400, "body": "/frobnicate x"})
        self.sb.write_macros(spec)
        check = status.check_macros(self.settings)
        self.assertFalse(check.ok)
        self.assertIn("warning", check.detail)

    def test_a_broken_macros_file_is_reported(self):
        (self.sb.project / "macros.json").write_text("{nope", encoding="utf-8")
        self.assertFalse(status.check_macros(self.settings).ok)

    def test_ideas_that_no_longer_check_out_are_reported(self):
        check = status.check_ideas(self.settings)  # the test data lacks most class spells
        self.assertFalse(check.ok)
        self.assertIn("left out", check.detail)

    def test_script_examples_pass_and_an_invented_function_is_caught(self):
        self.assertTrue(status.check_script_examples(self.settings).ok)
        self.settings.data.mkdir(exist_ok=True)
        (self.settings.data / "probe.json").write_text(json.dumps(
            {"script": {"names": ["print"], "members": {}}}), encoding="utf-8")
        check = status.check_script_examples(self.settings)
        self.assertFalse(check.ok)
        self.assertIn("does not exist in this game", check.detail)


class WatcherAndAiTests(StatusCase):
    def launcher(self):
        target = status.startup_file()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x", encoding="utf-8")

    def test_no_watcher_is_fine_when_requests_are_not_used(self):
        check = status.check_watcher(self.settings)
        self.assertTrue(check.ok)
        self.assertIn("not set up", check.detail)

    def test_a_watcher_that_should_run_but_does_not(self):
        self.launcher()
        check = status.check_watcher(self.settings)
        self.assertFalse(check.ok)
        self.assertIn("startup --now", check.action)

    def test_a_running_watcher_older_than_the_code_needs_a_restart(self):
        pid_file = self.settings.data / "watcher.pid"
        self.settings.data.mkdir(exist_ok=True)
        pid_file.write_text(str(os.getpid()), encoding="utf-8")
        future = time.time() + 3600
        os.utime(pid_file, (future, future))
        self.assertTrue(status.check_watcher(self.settings).ok)
        os.utime(pid_file, (100, 100))
        check = status.check_watcher(self.settings)
        self.assertFalse(check.ok)
        self.assertIn("older than", check.detail)

    def test_a_stale_pid_file_counts_as_not_running(self):
        self.launcher()
        self.settings.data.mkdir(exist_ok=True)
        (self.settings.data / "watcher.pid").write_text("not a number", encoding="utf-8")
        self.assertFalse(status.check_watcher(self.settings).ok)

    def test_the_ai(self):
        self.assertTrue(status.check_ai(self.settings).ok)
        import dataclasses
        missing = dataclasses.replace(self.settings, claude_path=str(self.sb.tmp / "nope.exe"))
        self.assertTrue(status.check_ai(missing).ok)  # not needed without in-game requests
        self.launcher()
        self.assertFalse(status.check_ai(missing).ok)
        chat = dataclasses.replace(self.settings, agent_backend="openai", agent_model="m")
        self.assertIn("llm-test", status.check_ai(chat).detail)


class ClientFolderTests(StatusCase):
    def test_other_client_folders_are_listed(self):
        self.assertEqual(status.check_client_folder(self.settings).detail, "_forever_")
        (self.sb.wow_root / "_classic_" / "Interface").mkdir(parents=True)
        self.assertIn("also found _classic_", status.check_client_folder(self.settings).detail)

    def test_a_missing_client_folder(self):
        import dataclasses
        gone = dataclasses.replace(self.settings, wow_dir=self.sb.tmp / "nowhere" / "_x_")
        check = status.check_client_folder(gone)
        self.assertFalse(check.ok)
        self.assertIn("setup", check.action)


class ReportTests(StatusCase):
    def test_the_report_lists_every_check_and_counts_what_to_do(self):
        text = status.render(status.gather(self.settings))
        self.assertEqual(text.count("\n") + 1, len(text.splitlines()))
        for name in ("game build", "probe", "interface number", "addon", "slots", "watcher"):
            self.assertIn(name, text)
        self.assertRegex(text.splitlines()[-1], r"^\d+ thing\(s\) to do\.$")

    def test_everything_up_to_date(self):
        self.assertEqual(status.render([status.Check("x", True, "fine")]),
                         "OK      x: fine\nEverything is up to date.")

    def test_a_check_that_breaks_is_reported_not_raised(self):
        def broken(_settings):
            raise OSError("disk on fire")

        with mock.patch.object(status, "CHECKS", (broken,)):
            results = status.gather(self.settings)
        self.assertFalse(results[0].ok)
        self.assertIn("disk on fire", results[0].detail)

    def test_the_command_exits_1_when_there_is_something_to_do_and_writes_nothing(self):
        before = sorted(p.as_posix() for p in self.sb.tmp.rglob("*"))
        result = self.sb.run("status", env={"APPDATA": str(self.sb.tmp / "appdata")})
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("ACTION", result.stdout)
        self.assertIn("thing(s) to do", result.stdout)
        after = sorted(p.as_posix() for p in self.sb.tmp.rglob("*")
                       if "__pycache__" not in p.parts)
        self.assertEqual([p for p in before if "__pycache__" not in p], after)

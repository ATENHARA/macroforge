"""Unit tests: settings, file helpers, checker, cache writer, install and the probe report."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mforge import fsutil
from mforge.cache import char_dir, merge_cache, parse_cache
from mforge.checker import check_macro, check_spec, known_sets
from mforge.errors import ConfigError, ValidationError
from mforge.install import MAX_SLOTS, build_addon, create_slots, install, slot_dirs, write_slots
from mforge.probe import classify, render_report
from mforge.settings import Settings
from tests.harness import DEFAULT_MACROS, Sandbox


def symlink_or_skip(test: unittest.TestCase, target: Path, link: Path) -> None:
    try:
        os.symlink(target, link, target_is_directory=target.is_dir())
    except (OSError, NotImplementedError):
        test.skipTest("cannot create symlinks on this machine")


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))

    def write_config(self, text: str, encoding: str = "utf-8") -> None:
        (self.tmp / "config.json").write_bytes(text.encode(encoding))

    def test_defaults_without_config_file(self):
        s = Settings.load(self.tmp)
        self.assertEqual((s.max_account_macros, s.max_char_macros), (120, 18))
        self.assertEqual(s.agent_timeout_seconds, 300)
        for name in ("script", "run", "dump", "console", "tinspect", "click", "logout", "gdisband"):
            self.assertIn(name, s.blocked_commands)
        self.assertEqual(s.product, "wow_classic_beta")

    def test_values_come_from_config(self):
        self.write_config(json.dumps({"wow_dir": str(self.tmp / "_classic_"), "account": "A1",
                                      "max_char_macros": 5, "blocked_commands": ["/Script"]}))
        s = Settings.load(self.tmp)
        self.assertEqual((s.account, s.max_char_macros, s.product), ("A1", 5, "wow_classic"))
        self.assertEqual(s.blocked_commands, frozenset({"script"}))

    def test_byte_order_mark_is_tolerated(self):
        self.write_config('﻿{"max_char_macros": 7}')
        self.assertEqual(Settings.load(self.tmp).max_char_macros, 7)

    def test_wrong_types_are_config_errors(self):
        for text in ('{"max_char_macros": "18"}', '{"max_char_macros": true}', '{"claude_path": 5}',
                     '{"blocked_commands": "script"}', "[1]", "{not json"):
            self.write_config(text)
            with self.assertRaises(ConfigError, msg=text):
                Settings.load(self.tmp)

    def test_unknown_keys_warn(self):
        self.write_config('{"bogus": 1}')
        with self.assertLogs("mforge.settings", level="WARNING") as logs:
            Settings.load(self.tmp)
        self.assertIn("bogus", logs.output[0])

    def test_every_account_folder_is_covered_unless_one_is_configured(self):
        wow = self.tmp / "_classic_beta_"
        root = wow / "WTF" / "Account"
        (root / "ONE").mkdir(parents=True)
        (root / "SavedVariables").mkdir()  # the game's shared folder is not an account
        self.write_config(json.dumps({"wow_dir": str(wow)}))
        settings = Settings.load(self.tmp)
        self.assertEqual((settings.account, settings.account_dirs), ("", [root / "ONE"]))
        (root / "TWO").mkdir()
        os.utime(root / "ONE", (1_600_000_000, 1_600_000_000))
        settings = Settings.load(self.tmp)
        self.assertEqual(settings.account_dirs, [root / "TWO", root / "ONE"])  # newest first
        self.assertEqual(len(settings.saved_vars_files), 2)
        self.assertEqual(settings.account_dir, root / "TWO")
        self.write_config(json.dumps({"wow_dir": str(wow), "account": "ONE"}))
        self.assertEqual(Settings.load(self.tmp).account_dirs, [root / "ONE"])

    def test_with_no_account_folder_yet_a_placeholder_path_is_used(self):
        wow = self.tmp / "_classic_beta_"
        wow.mkdir()
        self.write_config(json.dumps({"wow_dir": str(wow)}))
        self.assertEqual(Settings.load(self.tmp).account_dir.name, "YOUR_ACCOUNT_FOLDER")

    def test_home_environment_variable(self):
        with mock.patch.dict(os.environ, {"MACROFORGE_HOME": str(self.tmp)}):
            self.assertEqual(Settings.load().root, self.tmp.resolve())


class FsUtilTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))

    def test_atomic_write_replaces_content(self):
        f = self.tmp / "a.txt"
        fsutil.atomic_write_text(f, "one")
        fsutil.atomic_write_text(f, "two\n")
        self.assertEqual(f.read_bytes(), b"two\n")
        self.assertEqual([p.name for p in self.tmp.iterdir()], ["a.txt"])

    def test_failed_write_keeps_old_file_and_leaves_no_temp(self):
        f = self.tmp / "a.txt"
        f.write_text("keep")
        with mock.patch("os.replace", side_effect=OSError("boom")):
            with self.assertRaises(OSError):
                fsutil.atomic_write_text(f, "new")
        self.assertEqual(f.read_text(), "keep")
        self.assertEqual([p.name for p in self.tmp.iterdir()], ["a.txt"])

    def test_ensure_inside(self):
        fsutil.ensure_inside(self.tmp, self.tmp / "x" / "y")
        for bad in (self.tmp.parent, self.tmp / ".." / "other"):
            with self.assertRaises(ValidationError):
                fsutil.ensure_inside(self.tmp, bad)

    def test_is_link(self):
        real = self.tmp / "real"
        real.mkdir()
        self.assertFalse(fsutil.is_link(real))
        symlink_or_skip(self, real, self.tmp / "link")
        self.assertTrue(fsutil.is_link(self.tmp / "link"))


class CheckerTests(unittest.TestCase):
    CMDS = {"cast", "stopmacro", "petattack", "castsequence"}
    CONDS = {"harm", "help", "nodead", "dead", "mod", "pet", "combat"}

    def check(self, body, spells=None, other=frozenset(), verified=False):
        return check_macro({"name": "m", "body": body}, spells, set(other), self.CMDS, self.CONDS, verified)

    def kinds(self, body, **kw):
        return {f.kind for f in self.check(body, **kw)}

    def test_clean_macro(self):
        self.assertEqual(self.check("#showtooltip\n/cast [@mouseover,harm,nodead][] Fear", {"fear"}), [])

    def test_unknown_command_conditional_metacommand_and_syntax(self):
        self.assertEqual(self.kinds("/petattak"), {"command"})
        self.assertEqual(self.kinds("/cast [flarb] X"), {"conditional"})
        self.assertEqual(self.kinds("#wat"), {"metacommand"})
        self.assertEqual(self.kinds("hello"), {"syntax"})

    def test_negated_and_target_forms_are_understood(self):
        self.assertEqual(self.check("/cast [nocombat,@focus,target=player,mod:shift/alt] X"), [])

    def test_spells_are_checked_when_class_known(self):
        found = self.check("/cast Shadow Blot", {"shadow bolt"})
        self.assertEqual([f.kind for f in found], ["spell"])
        self.assertEqual(self.check("/cast Shadow Bolt(Rank 2)", {"shadow bolt"}), [])
        self.assertEqual(self.check("/castsequence reset=combat Fear, Corruption", {"fear", "corruption"}), [])
        self.assertEqual(self.check("/cast Blot", None), [])  # class unknown: no spell check
        self.assertEqual(self.check("/cast Seduction", {"fear"}, other={"seduction"}), [])

    def test_wording_depends_on_source(self):
        self.assertIn("wiki list", self.check("/zzz")[0].text)
        self.assertIn("client", self.check("/zzz", verified=True)[0].text)

    def test_known_sets_from_probe_and_fallback(self):
        sb = Sandbox()
        self.addCleanup(sb.cleanup)
        settings = Settings.load(sb.project)
        commands, conditionals, verified = known_sets(settings)
        self.assertFalse(verified)
        self.assertIn("cast", commands)
        probe = ('MacroForgeProbe = {["slash"] = {"/cast","/zzaddon",}, ["control"] = '
                 '{["pos"]="Y",["neg"]="N",["both"]="Y",}, ["conditionals"] = {["harm"] = '
                 '{["pos"]="N",["neg"]="Y",["both"]="N",}, ["fake"] = {["pos"]="Y",["neg"]="N",'
                 '["both"]="Y",},},}\n')
        (sb.saved_vars / "MacroForge.lua").write_text(probe, encoding="utf-8")
        commands, conditionals, verified = known_sets(settings)
        self.assertTrue(verified)
        self.assertEqual(commands, {"cast", "zzaddon"})
        self.assertIn("harm", conditionals)
        self.assertNotIn("fake", conditionals)

    def test_corrupt_saved_variables_fall_back_to_wiki_list(self):
        sb = Sandbox()
        self.addCleanup(sb.cleanup)
        (sb.saved_vars / "MacroForge.lua").write_text("garbage = {", encoding="utf-8")
        self.assertFalse(known_sets(Settings.load(sb.project))[2])

    def test_check_spec_uses_class_table(self):
        sb = Sandbox()
        self.addCleanup(sb.cleanup)
        settings = Settings.load(sb.project)
        results, _ = check_spec(settings, DEFAULT_MACROS)
        self.assertEqual(results, [])
        bad = json.loads(json.dumps(DEFAULT_MACROS))
        bad["macros"][0]["body"] = "/cast Nonexistent Spell"  # character macro: class is known
        bad["macros"][1]["body"] = "/cast Nonexistent Spell"  # account macro: no class, not checked
        results, _ = check_spec(settings, bad)
        self.assertEqual([(n, f.kind) for n, f in results], [("Fear MO", "spell")])


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, ignore_errors=True))

    def macro(self, name="A", body="/cast A", icon=1):
        return {"name": name, "icon": icon, "body": body}

    def test_new_macros_get_free_ids_and_foreign_ones_survive(self):
        cache = self.tmp / "macros-cache.txt"
        cache.write_text('VER 3 0000000000000001 "Other" "5"\n/x\nEND\n', encoding="utf-8")
        text = merge_cache(cache, [self.macro("A"), self.macro("B", "/cast B\n/say hi", 9)], 0, 5)
        (self.tmp / "out.txt").write_text(text, encoding="utf-8")
        blocks = parse_cache(self.tmp / "out.txt")
        self.assertEqual([(b["id"], b["name"]) for b in blocks],
                         [("0000000000000001", "Other"), ("0000000000000002", "A"),
                          ("0000000000000003", "B")])
        self.assertEqual(blocks[2]["body"], ["/cast B", "/say hi"])

    def test_existing_macro_is_updated_in_place(self):
        cache = self.tmp / "macros-cache.txt"
        cache.write_text('VER 3 0000000000000007 "A" "5"\nold\nEND\n', encoding="utf-8")
        blocks = parse_cache(self._write(merge_cache(cache, [self.macro("A", "/new", 8)], 0, 5)))
        self.assertEqual((blocks[0]["id"], blocks[0]["icon"], blocks[0]["body"]),
                         ("0000000000000007", "8", ["/new"]))

    def _write(self, text):
        path = self.tmp / "t.txt"
        path.write_text(text, encoding="utf-8")
        return path

    def test_names_that_would_corrupt_the_file_are_rejected(self):
        for name in ('A"B', "A\nB"):
            with self.assertRaises(ValidationError):
                merge_cache(self.tmp / "none.txt", [self.macro(name)], 0, 5)

    def test_running_out_of_slots_is_an_error(self):
        with self.assertRaises(ValidationError):
            merge_cache(self.tmp / "none.txt", [self.macro("A"), self.macro("B")], 0, 1)

    def test_char_dir_matches_loosely_and_skips_links(self):
        sb = Sandbox()
        self.addCleanup(sb.cleanup)
        settings = Settings.load(sb.project)
        real = sb.account_dir / "Realm" / "Testy-Mage"
        real.mkdir(parents=True)
        self.assertEqual(char_dir(settings, "testy mage"), real)
        self.assertIsNone(char_dir(settings, "someone else"))
        elsewhere = sb.tmp / "elsewhere"
        elsewhere.mkdir()
        symlink_or_skip(self, elsewhere, sb.account_dir / "Realm" / "Linked-Char")
        self.assertIsNone(char_dir(settings, "linked char"))


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox()
        self.addCleanup(self.sb.cleanup)
        self.settings = Settings.load(self.sb.project)
        self.spec = {"account": [{"name": "T", "icon": 1, "body": "/x"}], "chars": {},
                     "remove": {"account": [], "chars": {}}}

    def test_slot_count_is_bounded(self):
        for bad in (0, -1, MAX_SLOTS + 1):
            with self.assertRaises(ValidationError):
                create_slots(self.settings, bad)
        create_slots(self.settings, 2)
        self.assertEqual(len(slot_dirs(self.settings)), 2)

    def test_install_needs_a_build_and_an_addons_folder(self):
        with self.assertRaisesRegex(ValidationError, "build"):
            install(self.settings)
        build_addon(self.settings, self.spec, [])
        broken = Settings.load(self.sb.project)
        object.__setattr__(broken, "wow_dir", self.sb.tmp / "missing")
        with self.assertRaises(ValidationError):
            install(broken)

    def test_install_refuses_to_write_through_a_link(self):
        build_addon(self.settings, self.spec, [])
        elsewhere = self.sb.tmp / "elsewhere"
        elsewhere.mkdir()
        symlink_or_skip(self, elsewhere, self.settings.addons / "MacroForge")
        with self.assertRaisesRegex(ValidationError, "link"):
            install(self.settings)
        self.assertEqual(list(elsewhere.iterdir()), [])

    def test_slots_receive_renamed_global_and_same_rev(self):
        create_slots(self.settings, 3)
        rev = build_addon(self.settings, self.spec, [])
        self.assertEqual(write_slots(self.settings), 3)
        for folder in slot_dirs(self.settings):
            text = (folder / "Slot.lua").read_text(encoding="utf-8")
            self.assertTrue(text.startswith("-- GENERATED"))
            self.assertIn("MacroForgeSlotData = {", text)
            self.assertNotIn("MacroForgeData = {", text)
            self.assertIn(f"rev = {rev},", text)

    def test_unrelated_folders_are_never_touched_as_slots(self):
        (self.settings.addons / "MacroForge_Sabc").mkdir()
        (self.settings.addons / "MacroForge_S001x").mkdir()
        self.assertEqual(slot_dirs(self.settings), [])


class ProbeTests(unittest.TestCase):
    CONTROL = {"pos": "Y", "neg": "N"}

    def test_classify(self):
        self.assertEqual(classify({"pos": "N", "neg": "Y", "both": "N"}, self.CONTROL), "supported")
        self.assertEqual(classify({"pos": "Y", "neg": "N", "both": "Y"}, self.CONTROL), "unsupported")
        self.assertEqual(classify({"pos": "ERROR", "neg": "N"}, self.CONTROL), "error")
        self.assertEqual(classify({"pos": "N", "neg": "Y"}, self.CONTROL), "supported")
        self.assertEqual(classify({"pos": "Y", "neg": "N"}, self.CONTROL), "unconfirmed")
        self.assertEqual(classify({"pos": "N", "neg": "N"}, {"pos": "N", "neg": "N"}), "unsupported")

    def test_render_report_rejects_malformed_probe(self):
        for bad in ({}, {"build": {}}, {"build": {"toc": "../../evil", "version": "1", "build": "1"},
                                          "control": self.CONTROL, "slash": [], "conditionals": {},
                                          "time": "t", "targets": {}, "api": {}}):
            with self.assertRaises(ValidationError):
                render_report(bad)


if __name__ == "__main__":
    unittest.main()


class InstallCleanupTests(unittest.TestCase):
    def test_install_removes_addon_files_that_no_longer_exist_but_keeps_other_files(self):
        from tests.harness import Sandbox
        sb = Sandbox()
        self.addCleanup(sb.cleanup)
        self.assertEqual(sb.run("build").returncode, 0)
        self.assertEqual(sb.run("install").returncode, 0)
        old = sb.addons / "MacroForge" / "Experiments.lua"
        old.write_text("-- from an older version", encoding="utf-8")
        note = sb.addons / "MacroForge" / "my-notes.txt"
        note.write_text("keep me", encoding="utf-8")
        self.assertEqual(sb.run("install").returncode, 0)
        self.assertFalse(old.exists())
        self.assertTrue(note.exists())
        self.assertTrue((sb.addons / "MacroForge" / "UI.lua").exists())

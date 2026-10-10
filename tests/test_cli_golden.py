"""Black-box tests of the command line against a fake game folder (behavior must not change)."""
from __future__ import annotations

import copy
import unittest

from tests.harness import (ACCOUNT, DEFAULT_MACROS, Sandbox, check_golden, normalize_rev)

PROBE_SAMPLE = """\
MacroForgeProbe = {
["time"] = "2026-10-08 17:38:18",
["build"] = {
["version"] = "1.60.1",
["build"] = "70291",
["toc"] = 16001,
["date"] = "Oct 8 2026",
},
["slash"] = {
"/cast",
"/script",
"/zzaddon",
},
["control"] = {
["pos"] = "Y",
["neg"] = "N",
["both"] = "Y",
},
["conditionals"] = {
["harm"] = {
["pos"] = "N",
["neg"] = "Y",
["both"] = "N",
},
["mod:shift"] = {
["pos"] = "N",
["neg"] = "Y",
["both"] = "N",
},
["madeupthing"] = {
["pos"] = "Y",
["neg"] = "N",
["both"] = "Y",
},
},
["targets"] = {
["player"] = "Y@player",
},
["api"] = {
["CreateMacro"] = "function",
["GetSpellInfo"] = "nil",
},
}
"""


class CliTestCase(unittest.TestCase):
    """Creates a fresh sandbox per test."""

    macros: dict | None = None

    def setUp(self) -> None:
        self.sb = Sandbox(macros=copy.deepcopy(self.macros) if self.macros else None)
        self.addCleanup(self.sb.cleanup)


class BuildInstallTests(CliTestCase):
    def test_build_generates_data_file(self):
        result = self.sb.run("build")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("built 2 macro(s)", result.stdout)
        data = normalize_rev(self.sb.read("build/MacroForge/MacroForgeData.lua"))
        check_golden(self, "MacroForgeData.lua", data)

    def test_build_copies_addon_files(self):
        self.sb.run("build")
        for name in ("MacroForge.lua", "MacroForge.toc", "Probe.lua", "MacroForgeData.lua"):
            self.assertTrue((self.sb.project / "build" / "MacroForge" / name).exists(), name)

    def test_install_copies_to_addons_folder(self):
        self.sb.run("build")
        result = self.sb.run("install")
        self.assertEqual(result.returncode, 0, result.stderr)
        installed = self.sb.addons / "MacroForge"
        built = self.sb.project / "build" / "MacroForge"
        for f in built.iterdir():
            self.assertEqual((installed / f.name).read_bytes(), f.read_bytes(), f.name)

    def test_install_before_build_fails_cleanly(self):
        result = self.sb.run("install")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("build", result.stderr + result.stdout)

    def test_slots_created_and_filled_by_install(self):
        self.assertEqual(self.sb.run("slots", "--count", "3").returncode, 0)
        dirs = sorted(p.name for p in self.sb.addons.iterdir())
        self.assertEqual(dirs, ["MacroForge_S001", "MacroForge_S002", "MacroForge_S003"])
        toc = (self.sb.addons / "MacroForge_S002" / "MacroForge_S002.toc").read_text("utf-8")
        check_golden(self, "slot_002.toc", toc)
        self.sb.run("build")
        self.assertEqual(self.sb.run("install").returncode, 0)
        for name in dirs:
            slot = (self.sb.addons / name / "Slot.lua").read_text("utf-8")
            check_golden(self, "Slot.lua", normalize_rev(slot))
        main = (self.sb.addons / "MacroForge" / "MacroForgeData.lua").read_text("utf-8")
        self.assertEqual(normalize_rev(main).replace("MacroForgeData = {", "MacroForgeSlotData = {"),
                         normalize_rev(slot))

    def test_all_slots_carry_the_same_rev(self):
        self.sb.run("slots", "--count", "3")
        self.sb.run("build")
        self.sb.run("install")
        revs = {(self.sb.addons / n / "Slot.lua").read_text("utf-8").split("rev = ")[1].split(",")[0]
                for n in ("MacroForge_S001", "MacroForge_S002", "MacroForge_S003")}
        self.assertEqual(len(revs), 1)


class SpecValidationTests(CliTestCase):
    def test_body_over_255_chars_is_rejected(self):
        spec = copy.deepcopy(DEFAULT_MACROS)
        spec["macros"][1]["body"] = "/say " + "x" * 300
        self.sb.write_macros(spec)
        result = self.sb.run("build")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("255", result.stderr)

    def test_name_over_16_chars_is_rejected(self):
        spec = copy.deepcopy(DEFAULT_MACROS)
        spec["macros"][1]["name"] = "ThisNameIsFarTooLong"
        self.sb.write_macros(spec)
        result = self.sb.run("build")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("16", result.stderr)

    def test_unknown_spell_icon_is_rejected(self):
        spec = copy.deepcopy(DEFAULT_MACROS)
        spec["macros"][1]["icon"] = "spell:No Such Spell"
        self.sb.write_macros(spec)
        result = self.sb.run("build")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unknown spell", result.stderr)

    def test_numeric_and_icon_name_icons(self):
        spec = copy.deepcopy(DEFAULT_MACROS)
        spec["macros"][0]["icon"] = 12345
        spec["macros"][1]["icon"] = "icon:spell_shadow_shadowbolt"
        self.sb.write_macros(spec)
        self.assertEqual(self.sb.run("build").returncode, 0)
        data = self.sb.read("build/MacroForge/MacroForgeData.lua")
        self.assertIn("icon = 12345", data)
        self.assertIn("icon = 136197", data)

    def test_missing_icon_defaults_to_question_mark(self):
        spec = copy.deepcopy(DEFAULT_MACROS)
        del spec["macros"][1]["icon"]
        self.sb.write_macros(spec)
        self.sb.run("build")
        self.assertIn("icon = 134400", self.sb.read("build/MacroForge/MacroForgeData.lua"))

    def test_special_characters_survive_into_lua(self):
        spec = copy.deepcopy(DEFAULT_MACROS)
        spec["macros"][1]["body"] = '/say "quoted" back\\slash'
        self.sb.write_macros(spec)
        self.assertEqual(self.sb.run("build").returncode, 0)
        data = self.sb.read("build/MacroForge/MacroForgeData.lua")
        self.assertIn(r'/say \"quoted\" back\\slash', data)


class CheckTests(CliTestCase):
    def test_clean_spec_has_no_warnings(self):
        result = self.sb.run("check")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("0 warning(s)", result.stdout)

    def test_typos_are_flagged(self):
        spec = copy.deepcopy(DEFAULT_MACROS)
        spec["macros"][0]["body"] = ("#showtooltip Fear\n/cast [@mouseover,harm,flarb] Fear\n"
                                     "/petattak\n/cast Shadow Blot")
        self.sb.write_macros(spec)
        result = self.sb.run("check")
        self.assertIn("flarb", result.stdout)
        self.assertIn("/petattak", result.stdout)
        self.assertIn("Shadow Blot", result.stdout)
        self.assertNotIn("0 warning(s)", result.stdout)


class DataLookupTests(CliTestCase):
    def test_spell_lookup(self):
        out = self.sb.run("spell", "Fear").stdout
        self.assertIn("Fear: icon 136183", out)
        self.assertIn("5782", out)

    def test_spell_lookup_unknown(self):
        self.assertIn("not found", self.sb.run("spell", "Nonsense").stdout)

    def test_icon_search(self):
        out = self.sb.run("icon", "shadow").stdout
        self.assertIn("136197", out)
        self.assertIn("spell_shadow_shadowbolt", out)

    def test_class_spell_table(self):
        out = self.sb.run("spells", "warlock").stdout
        for text in ("Shadow Bolt", "Rank 2", "Life Tap", "Fear", "L 8"):
            self.assertIn(text, out)

    def test_class_spell_table_unknown_class(self):
        result = self.sb.run("spells", "notaclass")
        self.assertNotEqual(result.returncode, 0)

    def test_help_lists_every_command(self):
        out = self.sb.run("--help").stdout
        for cmd in ("icon", "spell", "spells", "build", "install", "slots", "check", "cache",
                    "report", "spellbook", "watch", "rollback", "login", "startup", "fetch"):
            self.assertIn(cmd, out, cmd)


class CacheTests(CliTestCase):
    ACCOUNT_CACHE = 'VER 3 0000000000000001 "RS_MACRO" "667492"\n/targetexact Foo\nEND\n'

    def test_account_cache_merge_keeps_foreign_macros(self):
        (self.sb.account_dir / "macros-cache.txt").write_text(self.ACCOUNT_CACHE, encoding="utf-8")
        result = self.sb.run("cache")
        self.assertEqual(result.returncode, 0, result.stderr)
        staged = self.sb.read("build/cache/account/macros-cache.txt")
        check_golden(self, "account_cache.txt", staged)
        # nothing was written to the game folder without --write
        self.assertEqual((self.sb.account_dir / "macros-cache.txt").read_text("utf-8"),
                         self.ACCOUNT_CACHE)

    def test_character_cache_uses_character_id_range(self):
        char = self.sb.account_dir / "Realm" / "Test-Char"
        char.mkdir(parents=True)
        self.sb.run("cache")
        staged = self.sb.read("build/cache/testchar/macros-cache.txt")
        check_golden(self, "char_cache.txt", staged)

    def test_cache_skips_character_without_folder(self):
        result = self.sb.run("cache")
        self.assertIn("skip", result.stdout)


class ProbeReportTests(CliTestCase):
    def test_report_requires_probe_data(self):
        result = self.sb.run("report")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("/mf probe", result.stderr + result.stdout)

    def test_report_from_probe_sample(self):
        (self.sb.saved_vars / "MacroForge.lua").write_text(PROBE_SAMPLE, encoding="utf-8")
        result = self.sb.run("report")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("2/3 conditionals confirmed supported", result.stdout)
        check_golden(self, "compat-16001.md", self.sb.read("reference/compat-16001.md"))

    def test_spellbook_prints_saved_file(self):
        (self.sb.saved_vars / "MacroForge.lua").write_text("MacroForgeExport = {}\n", "utf-8")
        self.assertIn("MacroForgeExport", self.sb.run("spellbook").stdout)

    def test_spellbook_without_file(self):
        self.assertIn("not found", self.sb.run("spellbook").stdout)


class RemoveListTests(CliTestCase):
    def test_remove_list_reaches_data_file(self):
        self.sb.run("build")
        data = self.sb.read("build/MacroForge/MacroForgeData.lua")
        self.assertIn('["Test Char"] = { "Old" }', data)


class RollbackTests(CliTestCase):
    def test_rollback_without_backups_fails(self):
        self.assertNotEqual(self.sb.run("rollback").returncode, 0)


if __name__ == "__main__":
    unittest.main()

"""The macro ideas and script examples: every one is checked before it can reach the game."""
from __future__ import annotations

import unittest
from pathlib import Path

from mforge import suggest
from mforge.assets import load_toml
from mforge.install import build_addon
from mforge.lua import render_suggestions
from mforge.settings import Settings
from tests.harness import REPO
from tests.test_agent_watcher import WatcherCase

# class id, token, lowest level of each spell (id, name, level, icon id)
GAME = {
    "classes": [(8, "MAGE", 128), (9, "WARLOCK", 256)],
    "spells": {"MAGE": [(118, "Polymorph", 8, 100), (5504, "Conjure Water", 4, 101),
                        (5505, "Conjure Food", 6, 102)],
               "WARLOCK": [(5782, "Fear", 8, 103), (5783, "Fear", 32, 103),
                           (1490, "Curse of the Elements", 20, 104)]},
    "icons": [(134269, "INV_Misc_Map_01")],
}


def write_game_data(folder: Path) -> None:
    """A small game data set: two classes with a few spells each, plus one icon file name."""
    rows = {name: [] for name in ("ChrClasses", "SkillLine", "SkillRaceClassInfo", "SkillLineAbility",
                                  "SpellName", "Spell", "SpellLevels", "SpellMisc")}
    for index, (class_id, token, mask) in enumerate(GAME["classes"], start=1):
        rows["ChrClasses"].append(f"{class_id},{token}")
        rows["SkillLine"].append(f"{index},7,{token.title()}")
        rows["SkillRaceClassInfo"].append(f"{index},{index},{mask}")
        for spell_id, name, level, icon in GAME["spells"][token]:
            rows["SkillLineAbility"].append(f"{spell_id},{index},{spell_id},0")
            rows["SpellName"].append(f"{spell_id},{name}")
            rows["Spell"].append(f"{spell_id},Rank 1")
            rows["SpellLevels"].append(f"{spell_id},0,{level},{level},{spell_id}")
            rows["SpellMisc"].append(f"{spell_id},{icon},{spell_id}")
    heads = {"ChrClasses": "ID,Filename", "SkillLine": "ID,CategoryID,DisplayName_lang",
             "SkillRaceClassInfo": "ID,SkillID,ClassMask", "SkillLineAbility": "ID,SkillLine,Spell,ClassMask",
             "SpellName": "ID,Name_lang", "Spell": "ID,NameSubtext_lang",
             "SpellLevels": "ID,DifficultyID,BaseLevel,SpellLevel,SpellID",
             "SpellMisc": "ID,SpellIconFileDataID,SpellID"}
    for name, lines in rows.items():
        (folder / f"{name}.csv").write_text("\n".join([heads[name], *lines]) + "\n", encoding="utf-8")
    (folder / "icons.csv").write_text(
        "".join(f"{i};Interface/ICONS/{n}.blp\n" for i, n in GAME["icons"]), encoding="utf-8")


def macro(**changes) -> dict:
    entry = {"class": "MAGE", "name": "Mouseover Poly", "icon": "spell:Polymorph",
             "why": "Polymorphs the enemy under your mouse.",
             "body": "#showtooltip\n/cast [@mouseover,harm,nodead][] Polymorph"}
    entry.update(changes)
    return entry


def script(**changes) -> dict:
    entry = {"example": "Show your coordinates", "name": "Coords", "icon": "icon:inv_misc_map_01",
             "why": "Prints where you are."}
    entry.update(changes)
    return entry


class SuggestionCase(WatcherCase):
    def setUp(self):
        super().setUp()
        write_game_data(self.settings.data)

    def load(self, macros=(), scripts=()):
        return suggest.load_suggestions(self.settings, {"macro": list(macros), "script": list(scripts)})

    def refused(self, entry, text, kind="macro"):
        result = self.load(**{"macros" if kind == "macro" else "scripts": [entry]})
        self.assertEqual(result.macros, {})
        self.assertEqual(result.scripts, [])
        self.assertEqual(len(result.problems), 1, result.problems)
        self.assertIn(text, result.problems[0])


class MacroEntryTests(SuggestionCase):
    def test_a_good_entry_is_kept_with_its_icon_and_level(self):
        result = self.load([macro()])
        self.assertEqual(result.problems, [])
        self.assertEqual(result.macros["MAGE"], [
            {"name": "Mouseover Poly", "icon": 100, "level": 8,
             "why": "Polymorphs the enemy under your mouse.",
             "body": "#showtooltip\n/cast [@mouseover,harm,nodead][] Polymorph"}])

    def test_the_level_is_the_highest_level_among_the_spells_used(self):
        body = "#showtooltip\n/cast [mod:shift] Conjure Food; Conjure Water"
        result = self.load([macro(name="Conjure", icon="spell:Conjure Water", body=body)])
        self.assertEqual(result.macros["MAGE"][0]["level"], 6)

    def test_the_lowest_rank_decides_when_a_spell_has_several(self):
        body = "#showtooltip\n/cast Fear"
        result = self.load([macro(**{"class": "WARLOCK", "icon": "spell:Fear", "body": body})])
        self.assertEqual(result.macros["WARLOCK"][0]["level"], 8)

    def test_a_macro_with_no_class_spell_needs_no_level(self):
        result = self.load([macro(body="#showtooltip\n/cast Attack")])
        self.assertEqual(result.macros["MAGE"][0]["level"], 0)

    def test_entries_stay_in_file_order_and_are_grouped_by_class(self):
        fear = macro(**{"class": "WARLOCK", "name": "Fear", "icon": "spell:Fear",
                        "body": "#showtooltip\n/cast Fear"})
        result = self.load([macro(), fear, macro(name="Second")])
        self.assertEqual([m["name"] for m in result.macros["MAGE"]], ["Mouseover Poly", "Second"])
        self.assertEqual(list(result.macros), ["MAGE", "WARLOCK"])

    def test_a_spell_of_another_class_is_refused(self):
        self.refused(macro(body="#showtooltip\n/cast Fear"), "'Fear' is not a known spell")

    def test_an_invented_spell_is_refused(self):
        self.refused(macro(body="#showtooltip\n/cast Frostbolt Supreme"), "not a known spell")

    def test_an_unknown_command_or_conditional_is_refused(self):
        self.refused(macro(body="/frobnicate Polymorph"), "not a command")
        self.refused(macro(body="/cast [bogus] Polymorph"), "not recognised")

    def test_another_addons_command_is_refused_even_if_the_client_knows_it(self):
        import json
        probe = {"slash": ["/cast", "/zzaddoncmd"], "control": {"pos": "N", "neg": "N"},
                 "conditionals": {"mod": {"pos": "Y", "neg": "N", "both": "N"}}}
        (self.settings.data / "probe.json").write_text(json.dumps(probe), encoding="utf-8")
        self.refused(macro(body="/zzaddoncmd Polymorph"), "not a command")

    def test_blocked_commands_are_refused(self):
        for command in ("/run print(1)", "/script x=1", "/console x 1", "/dump 1"):
            with self.subTest(command=command):
                self.refused(macro(body=command), "blocked command")

    def test_size_and_name_limits(self):
        self.refused(macro(body="/cast Polymorph\n" + "-" * 255), "bytes")
        self.refused(macro(name="N" * 17), "longer than 16")
        self.refused(macro(name=""), "name")

    def test_the_class_must_be_a_real_one(self):
        for bad in ("", "mage", "DEATHKNIGHT", None, 5):
            with self.subTest(bad=bad):
                self.refused(macro(**{"class": bad}), "class must be one of")

    def test_the_explanation_is_required_short_and_plain(self):
        self.refused(macro(why=""), "why")
        self.refused(macro(why="x" * (suggest.MAX_WHY + 1)), "why")
        self.refused(macro(why="|cffff0000red|r"), "why")
        self.refused(macro(why="two\nlines"), "why")
        entry = macro()
        del entry["why"]
        self.refused(entry, "why")

    def test_a_bad_icon_is_refused(self):
        self.refused(macro(icon="spell:Nonexistent"), "unknown spell for icon")
        self.refused(macro(icon="icon:no_such_icon"), "unknown icon name")

    def test_a_pipe_escape_in_the_body_is_refused(self):
        self.refused(macro(body="/cast Polymorph |cffff0000"), "escape")

    def test_one_bad_entry_does_not_stop_the_others(self):
        result = self.load([macro(name="N" * 17), macro(name="Good")])
        self.assertEqual([m["name"] for m in result.macros["MAGE"]], ["Good"])
        self.assertEqual(len(result.problems), 1)
        self.assertIn("macro idea 'NNNNNNNNNNNNNNNNN' left out", result.problems[0])

    def test_something_that_is_not_a_table_is_reported(self):
        result = suggest.load_suggestions(self.settings, {"macro": ["text", 5, macro()]})
        self.assertEqual(len(result.macros["MAGE"]), 1)
        self.assertEqual(len(result.problems), 2)


class ScriptEntryTests(SuggestionCase):
    def test_a_script_takes_its_body_from_the_examples_file(self):
        result = self.load(scripts=[script()])
        self.assertEqual(result.problems, [])
        entry = result.scripts[0]
        self.assertEqual((entry["name"], entry["icon"], entry["script"]), ("Coords", 134269, True))
        self.assertTrue(entry["body"].startswith("/run "))
        self.assertLessEqual(len(entry["body"].encode()), 255)

    def test_an_unknown_example_title_is_refused(self):
        self.refused(script(example="No such example"), "no example titled", kind="script")

    def test_an_example_that_fails_the_script_checker_is_refused(self):
        path = self.sb.project / "reference" / "script-examples.md"
        path.write_text("### Chatty\n```\n/run SendChatMessage(\"hi\")\n```\n", encoding="utf-8")
        self.refused(script(example="Chatty"), "not allowed", kind="script")

    def test_a_missing_examples_file_leaves_everything_out(self):
        (self.sb.project / "reference" / "script-examples.md").unlink()
        result = self.load([macro()], [script()])
        self.assertEqual((result.macros, result.scripts), ({}, []))
        self.assertIn("script-examples.md", result.problems[0])

    def test_a_script_name_limit_applies(self):
        self.refused(script(name="N" * 17), "longer than 16", kind="script")

    def test_the_game_function_list_is_used_when_the_probe_has_one(self):
        import json
        probe = {"script": {"names": ["print"], "members": {}}}
        (self.settings.data / "probe.json").write_text(json.dumps(probe), encoding="utf-8")
        self.refused(script(), "does not exist in this game", kind="script")


class DataProblemTests(SuggestionCase):
    def test_missing_game_data_gives_one_problem_and_nothing_else(self):
        (self.settings.data / "SpellName.csv").unlink()
        result = self.load([macro()], [script()])
        self.assertEqual((result.macros, result.scripts), ({}, []))
        self.assertEqual(len(result.problems), 1)
        self.assertIn("no suggestions were built", result.problems[0])


class RenderTests(unittest.TestCase):
    def test_the_lua_file_has_a_table_per_class_and_the_scripts(self):
        entry = {"name": "A", "icon": 5, "level": 3, "why": "why", "body": "/cast X"}
        text = render_suggestions({"MAGE": [entry], "DRUID": [entry]},
                                  [dict(entry, script=True)])
        self.assertTrue(text.startswith("-- GENERATED"))
        self.assertLess(text.index('["DRUID"]'), text.index('["MAGE"]'))  # sorted
        self.assertIn('{ name = "A", icon = 5, level = 3, why = "why", body = "/cast X" },', text)
        self.assertIn('body = "/cast X", script = true }', text)

    def test_text_cannot_break_out_of_its_string(self):
        entry = {"name": 'A"B', "icon": 1, "level": 0, "why": "x\\y",
                 "body": '#showtooltip\n/cast "X"]]--'}
        text = render_suggestions({"MAGE": [entry]}, [])
        self.assertIn('name = "A\\"B"', text)
        self.assertIn('why = "x\\\\y"', text)
        self.assertIn('body = "#showtooltip\\n/cast \\"X\\"]]--"', text)

    def test_no_entries_still_gives_valid_empty_tables(self):
        text = render_suggestions({}, [])
        self.assertIn("macros = {\n\n  }", text)
        self.assertIn("scripts = {\n\n  }", text)


class BuildTests(SuggestionCase):
    def test_the_build_writes_the_ideas_and_reports_the_problems(self):
        spec = {"account": [], "chars": {}, "remove": {"account": [], "chars": {}}}
        messages: list[str] = []
        build_addon(self.settings, spec, [], report=messages.append)
        text = (self.settings.build / "MacroForgeSuggestions.lua").read_text(encoding="utf-8")
        self.assertIn('["MAGE"]', text)
        self.assertTrue(any("left out" in m for m in messages))  # the shipped druid ideas need data
        self.assertTrue(all(m.startswith("  warn: ") for m in messages))


class ShippedFileTests(unittest.TestCase):
    """The file in the repository: its shape never needs game data."""

    @classmethod
    def setUpClass(cls):
        cls.raw = load_toml("suggestions.toml")

    def test_every_class_has_three_ideas_with_distinct_names(self):
        for token in suggest.CLASSES:
            names = [m["name"] for m in self.raw["macro"] if m["class"] == token]
            with self.subTest(token=token):
                self.assertEqual(len(names), 3, names)
                self.assertEqual(len(set(names)), 3)

    def test_there_are_no_entries_for_unknown_classes(self):
        self.assertTrue({m["class"] for m in self.raw["macro"]} <= set(suggest.CLASSES))

    def test_a_few_simple_and_a_few_involved_scripts_and_no_pet_rename(self):
        scripts = self.raw["script"]
        self.assertGreaterEqual(len(scripts), 4)
        for entry in scripts:
            self.assertNotIn("pet", entry["example"].lower())
            self.assertNotIn("PetRename", entry["example"])

    def test_every_entry_has_its_text_within_the_limits(self):
        for entry in self.raw["macro"] + self.raw["script"]:
            with self.subTest(name=entry["name"]):
                self.assertLessEqual(len(entry["name"]), 16)
                self.assertLessEqual(len(entry["why"]), suggest.MAX_WHY)
                self.assertTrue(entry["icon"])

    def test_every_script_example_title_exists(self):
        text = (REPO / "reference" / "script-examples.md").read_text(encoding="utf-8")
        for entry in self.raw["script"]:
            self.assertIn(f"### {entry['example']}\n", text)


@unittest.skipUnless((REPO / "data" / "SpellMisc.csv").exists(), "the game data is not downloaded")
class ShippedAgainstRealDataTests(unittest.TestCase):
    """Every shipped idea against the real game tables (needs `fetch`; skipped in a fresh clone)."""

    def test_every_shipped_idea_passes_all_checks(self):
        result = suggest.load_suggestions(Settings.load(REPO))
        self.assertEqual(result.problems, [])
        self.assertEqual({c: len(v) for c, v in result.macros.items()},
                         {c: 3 for c in suggest.CLASSES})
        self.assertEqual(len(result.scripts), 5)


if __name__ == "__main__":
    unittest.main()

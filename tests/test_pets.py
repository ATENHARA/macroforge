"""Pet abilities by family, taken from the game data, and handed to the agent."""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from mforge import agent
from mforge.spells import pet_abilities
from mforge.validate import Request
from tests.test_agent_watcher import WatcherCase


def write_tables(folder: Path) -> None:
    """A tiny game data set: a Felhunter and a Succubus line, a class line and some noise."""
    (folder / "SkillLine.csv").write_text(
        "DisplayName_lang,ID,CategoryID\n"
        '"Pet - Felhunter",189,7\n"Pet - Succubus",205,7\n"Warlock",355,7\n', encoding="utf-8")
    (folder / "SpellName.csv").write_text(
        "ID,Name_lang\n19244,Spell Lock\n6358,Seduction\n1,Shadow Bolt\n2,Tamed Pet Passive (DND)\n"
        "3,Warlock Pet Scaling\n", encoding="utf-8")
    (folder / "SkillLineAbility.csv").write_text(
        "SkillLine,Spell,ClassMask\n189,19244,0\n189,19244,0\n205,6358,0\n355,1,256\n189,2,0\n"
        "205,3,0\n205,999,0\n", encoding="utf-8")


class PetTableTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.folder, True)
        write_tables(self.folder)

    def test_abilities_are_grouped_by_family_without_noise_or_duplicates(self):
        self.assertEqual(pet_abilities(self.folder),
                         {"Felhunter": ["Spell Lock"], "Succubus": ["Seduction"]})

    def test_class_skill_lines_and_unknown_spells_are_ignored(self):
        families = pet_abilities(self.folder)
        self.assertNotIn("Warlock", families)
        self.assertNotIn("Shadow Bolt", sum(families.values(), []))

    def test_missing_data_is_a_clear_error(self):
        (self.folder / "SkillLine.csv").unlink()
        with self.assertRaises(Exception) as caught:
            pet_abilities(self.folder)
        self.assertIn("SkillLine", str(caught.exception))


class AgentWorkspaceTests(WatcherCase):
    def request(self):
        return Request(id=5, text="x", char="Test Char", cls="warlock", level=30)

    def test_the_agent_gets_pets_json_built_from_the_game_data(self):
        write_tables(self.settings.data)
        workspace = agent.prepare_workspace(self.settings, self.request())
        self.addCleanup(shutil.rmtree, workspace, True)
        table = json.loads((workspace / "pets.json").read_text(encoding="utf-8"))
        self.assertEqual(table["Felhunter"], ["Spell Lock"])

    def test_a_missing_pet_table_does_not_stop_the_request(self):
        for name in ("SkillLine.csv", "SkillLineAbility.csv"):
            (self.settings.data / name).unlink()
        (self.settings.data / "pets.json").unlink(missing_ok=True)
        workspace = agent.prepare_workspace(self.settings, self.request())
        self.addCleanup(shutil.rmtree, workspace, True)
        self.assertFalse((workspace / "pets.json").exists())

    def test_the_brief_and_prompt_point_at_pets_json(self):
        from tests.harness import REPO
        self.assertIn("pets.json", (REPO / "reference" / "agent-brief.md").read_text(encoding="utf-8"))
        self.assertIn("pets.json", (REPO / "mforge" / "resources" / "prompt.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()

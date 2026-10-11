"""The vetted script examples: they fit a macro, pass the checker and really work (against a fake game)."""
from __future__ import annotations

import re
import subprocess
import tempfile
import unittest
from pathlib import Path

from mforge.luacheck import check_script_body
from tests.test_luacheck import find_luajit

EXAMPLES = Path(__file__).resolve().parent.parent / "reference" / "script-examples.md"
SCRIPT_RUNNER = Path(__file__).resolve().parent / "lua" / "scripts.lua"


def load_examples() -> list[tuple[str, str]]:
    """``[(title, "/run ...")]`` from the examples file."""
    text = EXAMPLES.read_text(encoding="utf-8")
    return re.findall(r"### ([^\n]+)\n```\n(/run [^\n]*)\n```", text)


def run_examples(luajit: str, items: list[tuple[int, str]]) -> list[tuple[str, str, str]]:
    """Run ``(repeat count, code)`` pairs; returns ``(status, prints or error, actions)``."""
    with tempfile.TemporaryDirectory() as tmp:
        data = Path(tmp) / "snippets.txt"
        data.write_bytes("".join(f"{runs}\t{code.encode().hex()}\n"
                                 for runs, code in items).encode("ascii"))  # no \r\n on Windows
        out = subprocess.run([luajit, str(SCRIPT_RUNNER), str(data)], capture_output=True, text=True,
                             timeout=60, check=True).stdout.splitlines()
    results = []
    for line in out:
        status, printed, calls = line.split("\t")
        results.append((status, bytes.fromhex(printed).decode(), bytes.fromhex(calls).decode()))
    return results


class ExampleFileTests(unittest.TestCase):
    def test_there_are_plenty_and_each_has_a_unique_title(self):
        examples = load_examples()
        self.assertGreaterEqual(len(examples), 15)
        titles = [title for title, _ in examples]
        self.assertEqual(len(titles), len(set(titles)))
        text = EXAMPLES.read_text(encoding="utf-8")
        self.assertEqual(text.count("\n### "), len(examples))  # every heading has one example

    def test_every_example_fits_in_a_macro_and_passes_the_script_checker(self):
        for title, body in load_examples():
            with self.subTest(title=title):
                self.assertLessEqual(len(body.encode("utf-8")), 255)
                check_script_body(body)

    def test_no_example_uses_a_pipe_character(self):
        for title, body in load_examples():
            self.assertNotIn("|", body, title)

    def test_the_brief_example_in_the_entry_format_is_valid_json_and_passes(self):
        import json
        brief = (EXAMPLES.parent / "script-brief.md").read_text(encoding="utf-8")
        block = re.search(r"```json\n(.*?)\n```", brief, re.S).group(1)
        entry = json.loads(block)
        self.assertEqual(entry["kind"], "script")
        check_script_body(entry["body"])


@unittest.skipUnless(find_luajit(), "LuaJIT is not installed")
class ExamplesRunTests(unittest.TestCase):
    """Run every example under LuaJIT against a fake game; undefined names are errors."""

    @classmethod
    def setUpClass(cls):
        cls.examples = load_examples()
        cls.luajit = find_luajit()

    def result(self, title: str, runs: int = 1) -> tuple[str, str, str]:
        code = dict(self.examples)[title][len("/run "):]
        return run_examples(self.luajit, [(runs, code)])[0]

    def test_every_example_runs_without_an_error(self):
        results = run_examples(self.luajit, [(1, body[len("/run "):]) for _, body in self.examples])
        for (title, _body), (status, printed, _calls) in zip(self.examples, results):
            self.assertEqual(status, "ok", f"{title}: {printed}")

    def test_what_the_information_examples_print(self):
        expected = {
            "Show your coordinates": "Elwynn Forest 50.0, 25.0",
            "Free bag slots": "15 free bag slots",
            "Average armor durability": "Durability 80%",
            "Latency": "35ms home, 60ms world",
            "Frame rate": "60 fps",
            "Experience to the next level": "XP 50%",
            "Your gold": "1g 2s 3c",
            "Who is my target": "Bob 30 Warlock",
            "Combat state": "Out of combat",
            "The time": "12:00",
        }
        for title, text in expected.items():
            with self.subTest(title=title):
                status, printed, calls = self.result(title)
                self.assertEqual((status, printed, calls), ("ok", text, ""))

    def test_the_setting_examples_flip_the_setting_once(self):
        self.assertEqual(self.result("Toggle sound effects")[2], "SetCVar(Sound_EnableSFX,0)")
        self.assertEqual(self.result("Toggle music")[2], "SetCVar(Sound_EnableMusic,0)")
        self.assertEqual(self.result("Toggle all sound")[2], "SetCVar(Sound_EnableAllSound,0)")
        self.assertEqual(self.result("Zoom the camera out as far as the game allows")[2],
                         "SetCVar(cameraDistanceMaxZoomFactor,2.6)")

    def test_one_press_is_one_action(self):
        for title, _body in self.examples:
            with self.subTest(title=title):
                calls = self.result(title)[2]
                self.assertLessEqual(len([c for c in calls.split(";") if c]), 1)

    def test_the_pet_name_never_repeats_twice_in_a_row(self):
        status, _printed, calls = self.result("Random pet name (never the same twice in a row)", 200)
        self.assertEqual(status, "ok")
        names = [c[len("PetRename("):-1] for c in calls.split(";")]
        self.assertEqual(len(names), 200)
        self.assertTrue(all(a != b for a, b in zip(names, names[1:])))
        self.assertGreater(len(set(names)), 3)

    def test_the_eight_ball_and_emote_pick_from_their_lists(self):
        answers = {self.result("Magic 8-ball")[1] for _ in range(30)}
        self.assertTrue(answers <= {"Yes", "No", "Maybe", "Ask again later", "Definitely not"})
        emote = self.result("Random emote")[2]
        self.assertRegex(emote, r"^DoEmote\((dance|cheer|laugh|wave|bow)\)$")

    def test_the_mark_examples(self):
        self.assertEqual(self.result("Mark your target with a skull (group only)")[2], "SetRaidTarget(target,8)")
        marks = self.result("Cycle the raid mark on your target (group only)", 10)[2].split(";")
        self.assertEqual(marks[:3], ["SetRaidTarget(target,1)", "SetRaidTarget(target,2)",
                                     "SetRaidTarget(target,3)"])
        self.assertEqual(marks[8], "SetRaidTarget(target,1)")  # wraps after eight marks


class RunnerTests(unittest.TestCase):
    @unittest.skipUnless(find_luajit(), "LuaJIT is not installed")
    def test_the_fake_game_reports_mistakes(self):
        results = run_examples(find_luajit(), [(1, "print(NotARealFunction())"), (1, "print(1"),
                                               (1, "print(MF_unset)")])
        self.assertEqual([status for status, _, _ in results], ["error", "error", "ok"])
        self.assertIn("undefined global NotARealFunction", results[0][1])


if __name__ == "__main__":
    unittest.main()

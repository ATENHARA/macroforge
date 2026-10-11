"""The game's function list from the probe: loading it, the report counts, and checking against it."""
from __future__ import annotations

import json
import unittest

import shutil

from mforge import agent, probe
from mforge.errors import ValidationError
from mforge.spec import resolve_spec
from tests.test_agent_watcher import WatcherCase
from tests.test_script_prompts import script_request

SCRIPT_PROBE = {"names": ["GetTime", "C_Map", "print", "PetRename"],
                "members": {"C_Map": ["GetBestMapForUnit"]}, "loadstring": "function"}


def entry(body: str) -> dict:
    return {"name": "Test", "icon": 134400, "kind": "script", "body": body}


class KnownApiTests(unittest.TestCase):
    def test_a_good_probe_is_loaded(self):
        known = probe.known_api({"script": SCRIPT_PROBE})
        self.assertIn("GetTime", known.names)
        self.assertEqual(known.members["C_Map"], frozenset({"GetBestMapForUnit"}))

    def test_missing_or_damaged_data_gives_none(self):
        for bad in (None, {}, [], {"script": None}, {"script": {"names": [], "members": {}}},
                    {"script": {"names": "x", "members": {}}},
                    {"script": {"names": ["a"], "members": []}},
                    {"script": 3}):
            with self.subTest(bad=bad):
                self.assertIsNone(probe.known_api(bad))

    def test_non_text_names_are_ignored(self):
        known = probe.known_api({"script": {"names": ["a", 5, None], "members": {"C_X": ["f", 3]}}})
        self.assertEqual(known.names, frozenset({"a"}))
        self.assertEqual(known.members["C_X"], frozenset({"f", "3"}))

    def test_the_names_file_lists_namespaces_by_member(self):
        text = probe.api_names_text(probe.known_api({"script": SCRIPT_PROBE}))
        self.assertEqual(text.splitlines(),
                         ["GetTime", "PetRename", "print", "C_Map.GetBestMapForUnit"])


class ReportTests(unittest.TestCase):
    def test_the_report_gives_counts_and_no_names(self):
        data = {"build": {"version": "1", "toc": 1, "build": "2"}, "control": {"pos": "N", "neg": "N"},
                "slash": ["/run"], "conditionals": {}, "time": "now", "targets": {}, "api": {},
                "script": SCRIPT_PROBE}
        text = probe.render_report(data).text
        self.assertIn("## Script API", text)
        self.assertIn("4 game functions and objects, 1 members of 1 C_ namespaces", text)
        self.assertNotIn("PetRename", text)
        del data["script"]
        self.assertNotIn("Script API", probe.render_report(data).text)


class CheckingTests(WatcherCase):
    def write_probe(self, script=SCRIPT_PROBE):
        self.settings.data.mkdir(exist_ok=True)
        (self.settings.data / "probe.json").write_text(json.dumps({"script": script}),
                                                       encoding="utf-8")

    def test_an_invented_function_is_refused_once_the_probe_is_known(self):
        spec = {"macros": [entry("/run print(NoSuchFunction())")]}
        resolve_spec(spec, self.settings)  # no probe data: the name is not checked
        self.write_probe()
        with self.assertRaises(ValidationError) as caught:
            resolve_spec(spec, self.settings)
        self.assertIn("does not exist in this game", str(caught.exception))

    def test_real_names_still_pass_with_the_probe(self):
        self.write_probe()
        resolve_spec({"macros": [entry("/run print(GetTime())")]}, self.settings)
        resolve_spec({"macros": [entry("/run print(C_Map.GetBestMapForUnit(1))")]}, self.settings)
        with self.assertRaises(ValidationError):
            resolve_spec({"macros": [entry("/run print(C_Map.Invented())")]}, self.settings)

    def test_a_broken_probe_file_does_not_block_scripts(self):
        self.settings.data.mkdir(exist_ok=True)
        (self.settings.data / "probe.json").write_text("{not json", encoding="utf-8")
        resolve_spec({"macros": [entry("/run print(1)")]}, self.settings)

    def test_macros_do_not_read_the_probe(self):
        self.write_probe()
        macro = {"name": "M", "icon": 134400, "body": "/cast Fear"}
        resolve_spec({"macros": [macro]}, self.settings)

    def test_the_names_file_reaches_the_script_workspace_only(self):
        self.settings.data.mkdir(exist_ok=True)
        (self.settings.data / "api-names.txt").write_text("GetTime\n", encoding="utf-8")
        workspace = agent.prepare_workspace(self.settings, script_request())
        self.addCleanup(shutil.rmtree, workspace, True)
        self.assertEqual((workspace / "api-names.txt").read_text(encoding="utf-8"), "GetTime\n")


if __name__ == "__main__":
    unittest.main()

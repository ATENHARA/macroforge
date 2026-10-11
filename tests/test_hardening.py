"""Regression tests for the robustness findings of the adversarial review.

Hostile or malformed output from the agent, the chat endpoint, downloads and files must end in a
``MacroForgeError`` (and an answer for the game), never in an exception nobody catches.
"""
from __future__ import annotations

import codecs
import json
import os
import unittest
from pathlib import Path
from unittest import mock

import tests.test_accounts as accounts
import tests.test_fetch_startup_cli as fetch_tests
from mforge import agent, cache, llm, startup, stripcodec, watcher
from mforge.errors import AgentError, ValidationError
from mforge.validate import Request
from tests.harness import REPO
from tests.test_agent_watcher import WatcherCase, fake_process


def a_request() -> Request:
    return Request(id=5, text="x", char="Test Char", cls="warlock", level=30)


class AgentOutputTests(WatcherCase):
    def test_an_error_message_survives_odd_json(self):
        for out in ("1", "[1]", '"text"', "null", '{"result":5}', '{"result":null}', '{"result":["x"]}'):
            with self.subTest(out=out):
                self.assertIsInstance(agent._error_message(1, out, ""), str)
        self.assertEqual(agent._error_message(1, '{"result":"nope"}', ""), "nope")
        self.assertIn("exited with code 3", agent._error_message(3, "", ""))

    def test_a_macros_file_that_is_not_text_is_an_agent_error(self):
        def edit(workspace):
            (workspace / "macros.json").write_bytes(b'{"macros": [\xff\xfe]}')

        with self.assertRaises(AgentError) as caught:
            agent.run_agent(self.settings, a_request(), None, runner=fake_process(edit))
        self.assertIn("not readable text", str(caught.exception))

    def test_any_failure_still_gets_an_answer_and_the_game_is_told(self):
        def boom(cmd, input_text, cwd, env, timeout):
            raise RuntimeError("something nobody expected")

        reply = self.handle(runner=boom)
        self.assertTrue(reply.startswith("Something went wrong on the PC"), reply)
        replies = json.loads((self.settings.data / "replies.json").read_text(encoding="utf-8"))
        self.assertEqual(replies[-1]["text"], reply)
        self.assertFalse(replies[-1].get("pending"))


class TokenCountTests(unittest.TestCase):
    def test_odd_usage_numbers_count_as_zero_or_are_clamped(self):
        totals = llm._Totals()
        for usage in ({"prompt_tokens": "abc"}, {"prompt_tokens": None}, {"prompt_tokens": True},
                      {"prompt_tokens": float("nan")}, {"prompt_tokens": -5}, {"prompt_tokens": [1]},
                      {"prompt_tokens": {"a": 1}}, "text", None):
            totals.add({"usage": usage})
        self.assertEqual((totals.prompt, totals.completion), (0, 0))
        totals.add({"usage": {"prompt_tokens": 12, "completion_tokens": 3.9}})
        totals.add({"usage": {"prompt_tokens": 10 ** 30}})
        self.assertEqual(totals.completion, 3)
        self.assertEqual(totals.prompt, 12 + 10 ** 9)


class NotTextFilesTests(fetch_tests.FetchCase):
    def test_a_download_that_is_not_text_is_a_validation_error(self):
        self.server.routes["/db2/SpellName.csv"] = (200, {}, b"ID,Name_lang\n1,\xff\xfe\xfa\n" * 3)
        self.server.routes["/db2/SpellName.csv"] = (200, {}, b"\xff\xfe\xfd\xfc\n")
        with self.assertRaises(ValidationError):
            self.run_fetch(no_icons=True)
        self.assertFalse(any(n.endswith(".part") for n in self.data_files()))

    def test_a_macro_cache_that_is_not_text_is_refused_not_crashed_on(self):
        path = self.sb.project / "cache.txt"
        path.write_bytes(b"VER 3 0000000000000001 \xff\xfe\n")
        with self.assertRaises(ValidationError) as caught:
            cache.parse_cache(path)
        self.assertIn("cache.txt", str(caught.exception))


class SavedVariablesSpeedTests(unittest.TestCase):
    def test_a_file_with_hundreds_of_thousands_of_keys_parses_in_linear_time(self):
        import time
        from mforge.savedvars import parse_sv
        text = "T = {" + ",".join(f"k{i}={i}" for i in range(200_000)) + "}"
        started = time.monotonic()
        self.assertEqual(len(parse_sv(text)["T"]), 200_000)
        self.assertLess(time.monotonic() - started, 4.0)  # the quadratic parser needed about 30 s


class PictureSpeedTests(unittest.TestCase):
    def solid(self, width=1920, height=1080):
        row = bytes([40, 30, 20]) * width
        return stripcodec.Image(width, height, 3, tuple(row for _ in range(height)))

    def test_looking_for_the_header_reads_only_the_first_cells(self):
        image = self.solid(1000, 200)
        full = stripcodec.read_strip(image, 8)
        self.assertGreater(len(full), 100)
        self.assertEqual(stripcodec.read_strip(image, 8, limit=8), full[:8])

    def test_a_picture_that_is_not_a_request_is_rejected_quickly(self):
        import time
        started = time.monotonic()
        with self.assertRaises(ValidationError):
            stripcodec.find_strip(self.solid())
        self.assertLess(time.monotonic() - started, 1.0)  # it took over 2 seconds

    def test_an_oversized_file_is_refused_before_it_is_read(self):
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "big.tga"
            path.write_bytes(bytes(300))
            reads = mock.patch.object(Path, "read_bytes", side_effect=AssertionError("was read"))
            with mock.patch.object(stripcodec, "MAX_TGA_BYTES", 100), reads:
                with self.assertRaisesRegex(ValidationError, "too large"):
                    stripcodec.read_tga(path)


class WatchStateTests(unittest.TestCase):
    def test_only_the_newest_answered_ids_are_remembered(self):
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            state = watcher.WatchState(Path(folder) / "state.json")
            state.handled = set(range(1, watcher.MAX_HANDLED + 501))
            state.save()
            loaded = watcher.WatchState.load(state.path)
            self.assertEqual(len(loaded.handled), watcher.MAX_HANDLED)
            self.assertIn(watcher.MAX_HANDLED + 500, loaded.handled)
            self.assertNotIn(500, loaded.handled)

    def test_a_state_file_with_wrongly_typed_fields_is_survived(self):
        import tempfile
        for raw in ('{"handled": 5, "recent": "x"}', '{"handled": {"a": 1}}', '{"recent": 3.5}',
                    '[1, "a", null]', '"text"', "7"):
            with self.subTest(raw=raw), tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / "state.json"
                path.write_text(raw, encoding="utf-8")
                state = watcher.WatchState.load(path)
                self.assertIsInstance(state.handled, set)
                self.assertIsInstance(state.recent, list)


class LauncherEncodingTests(fetch_tests.FetchCase):
    """Windows Script Host reads a .vbs as ANSI unless it starts with a UTF-16 mark."""

    def setUp(self):
        super().setUp()
        self.env = mock.patch.dict(os.environ, {"APPDATA": str(self.sb.tmp / "appdata")})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_the_launcher_is_utf16_with_a_mark_and_keeps_accents_intact(self):
        path = startup.install_startup(self.settings, Path(REPO / "macroforge.py"), start_now=False)
        self.assertTrue(path.read_bytes().startswith(codecs.BOM_UTF16_LE))
        text = startup.launcher_text(Path("D:\\Games\\Jos\u00e9\\mf"), Path("C:\\p.exe"),
                                     Path("C:\\m.py"))
        decoded = (codecs.BOM_UTF16_LE + text.encode("utf-16-le")).decode("utf-16")
        self.assertIn("Jos\u00e9", decoded)
        self.assertEqual(startup.read_launcher(path).splitlines()[0], startup.MARKER)

    def test_a_launcher_from_an_older_version_can_still_be_removed(self):
        target = startup.startup_file()
        target.parent.mkdir(parents=True)
        target.write_bytes((startup.MARKER + "\nrest\n").encode("utf-8"))
        self.assertEqual(startup.remove_startup(), target)
        self.assertFalse(target.exists())

    def test_a_foreign_launcher_is_left_alone_in_either_encoding(self):
        target = startup.startup_file()
        target.parent.mkdir(parents=True)
        for data in (b"Set x = 1\n", codecs.BOM_UTF16_LE + "Set x = 1\n".encode("utf-16-le")):
            target.write_bytes(data)
            with self.assertRaises(ValidationError):
                startup.remove_startup()
            self.assertTrue(target.exists())


class ExportMatchTests(accounts.AccountsCase):
    def test_a_longer_first_name_does_not_pick_up_a_shorter_ones_spellbook(self):
        self.assertEqual(watcher.find_export(self.all, "Testy Mage")["char"], "Testy")
        self.assertEqual(watcher.find_export(self.all, "Testy-Mage")["char"], "Testy")
        self.assertEqual(watcher.find_export(self.all, "Testyx Mage")["char"], "Other")  # no match
        self.assertEqual(watcher.find_export(self.all, "Tes")["char"], "Other")

    def test_odd_names_do_not_crash(self):
        for name in ("", "   ", "!!", None, 5):
            self.assertIn(watcher.find_export(self.all, name)["char"], ("Testy", "Other"))


if __name__ == "__main__":
    unittest.main()

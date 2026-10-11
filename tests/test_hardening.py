"""Regression tests for the robustness findings of the adversarial review.

Hostile or malformed output from the agent, the chat endpoint, downloads and files must end in a
``MacroForgeError`` (and an answer for the game), never in an exception nobody catches.
"""
from __future__ import annotations

import codecs
import dataclasses
import json
import os
import unittest
from pathlib import Path
from unittest import mock

import tests.test_accounts as accounts
import tests.test_fetch_startup_cli as fetch_tests
from mforge import agent, cache, llm, startup, stripcodec, watcher
from mforge.errors import AgentError, ConfigError, ValidationError
from mforge.settings import Settings
from mforge.spec import resolve_spec
from mforge.validate import Request
from tests.harness import REPO, Sandbox
from tests.test_agent_watcher import NEW_MACRO, WatcherCase, add_macro, fake_process, request


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


class ConfirmGateTests(WatcherCase):
    """Removing or editing several macros at once needs the word CONFIRM in the request."""

    NAMES = ["M1", "M2", "M3", "M4"]

    def setUp(self):
        super().setUp()
        self.current = {"classes": {"Test Char": "warlock"}, "macros": [
            {"name": n, "icon": 134400, "body": f"/cast Fear\n/say {n}"} for n in self.NAMES]}
        self.sb.write_macros(self.current)

    def candidate(self, drop=(), edit=(), add=()):
        spec = json.loads(json.dumps(self.current))
        spec["macros"] = [m for m in spec["macros"] if m["name"] not in drop]
        for macro in spec["macros"]:
            if macro["name"] in edit:
                macro["body"] = "/cast Shadow Bolt"
        spec["macros"].extend(add)
        spec["remove"] = [{"name": n} for n in drop]
        return json.dumps(spec)

    def evaluate(self, text="", settings=None, **kw):
        return watcher._evaluate(settings or self.settings, self.candidate(**kw), "macro", text)

    def test_removing_several_macros_needs_confirm(self):
        with self.assertRaises(ValidationError) as caught:
            self.evaluate("delete all my macros", drop=self.NAMES)
        message = str(caught.exception)
        self.assertIn("remove or change 4 of your macros", message)
        self.assertIn("M1, M2, M3, M4", message)
        self.assertIn("CONFIRM", message)
        self.assertIn("Nothing was changed", message)

    def test_the_word_confirm_lets_it_through_in_any_case_but_only_as_a_word(self):
        for text in ("confirm: delete all my macros", "Delete them all. CONFIRM", "Confirm."):
            with self.subTest(text=text):
                self.evaluate(text, drop=self.NAMES)
        for text in ("unconfirmed", "reconfirm", "confirmed", "con firm", ""):
            with self.subTest(text=text), self.assertRaises(ValidationError):
                self.evaluate(text, drop=self.NAMES)

    def test_editing_several_macros_counts_too_but_adding_never_does(self):
        with self.assertRaises(ValidationError):
            self.evaluate("add #showtooltip to all", edit=["M1", "M2", "M3"])
        extra = [{"name": f"N{i}", "icon": 134400, "body": "/cast Fear"} for i in range(8)]
        self.evaluate("make eight macros", add=extra)

    def test_a_smaller_change_needs_no_confirmation(self):
        self.evaluate("remove two", drop=["M1", "M2"])
        self.evaluate("edit two", edit=["M1", "M2"])
        self.evaluate("one of each", drop=["M1"], edit=["M2"])

    def test_the_threshold_is_a_setting(self):
        strict = dataclasses.replace(self.settings, confirm_changes_at=2)
        with self.assertRaises(ValidationError):
            self.evaluate("remove two", settings=strict, drop=["M1", "M2"])
        relaxed = dataclasses.replace(self.settings, confirm_changes_at=10)
        self.evaluate("delete all", settings=relaxed, drop=self.NAMES)

    def test_a_reply_that_rejects_a_bulk_change_leaves_everything_alone(self):
        def wipe(workspace):
            (workspace / "macros.json").write_text(self.candidate(drop=self.NAMES), encoding="utf-8")

        before = self.spec_text()
        reply = self.handle(request(text="delete all my macros"), runner=fake_process(wipe))
        self.assertRejected(reply, "CONFIRM", "M1")
        self.assertEqual(self.spec_text(), before)

    def test_with_confirm_the_bulk_change_goes_through(self):
        def wipe(workspace):
            (workspace / "macros.json").write_text(self.candidate(drop=self.NAMES), encoding="utf-8")

        reply = self.handle(request(rid=101, text="confirm: delete all my macros"),
                            runner=fake_process(wipe))
        self.assertIn("Removed: M1, M2, M3, M4", reply)
        self.assertEqual(json.loads(self.spec_text())["macros"], [])

    def test_the_setting_is_validated(self):
        self.assertEqual(self.settings.confirm_changes_at, 3)
        for bad in (0, 101, "3", True, 2.5):
            sb = Sandbox(config={"confirm_changes_at": bad})
            self.addCleanup(sb.cleanup)
            with self.subTest(bad=bad), self.assertRaises(ConfigError):
                Settings.load(sb.project)


class IconFallbackTests(WatcherCase):
    """An icon the AI invented does not throw the whole answer away."""

    BAD = {**NEW_MACRO, "icon": "icon:no_such_icon_name"}

    def candidate(self, macro):
        spec = json.loads(self.spec_text())
        spec["macros"].append(macro)
        return json.dumps(spec)

    def test_a_missing_icon_is_replaced_by_the_default_and_counted_as_a_warning(self):
        candidate, warnings, summary = watcher._evaluate(
            self.settings, self.candidate(self.BAD), "macro", "add one")
        added = [m for m in candidate["macros"] if m["name"] == NEW_MACRO["name"]][0]
        self.assertIsNone(added["icon"])
        self.assertGreaterEqual(warnings, 1)
        self.assertIn(NEW_MACRO["name"], summary)

    def test_a_missing_spell_icon_too(self):
        candidate, _warnings, _summary = watcher._evaluate(
            self.settings, self.candidate({**NEW_MACRO, "icon": "spell:No Such Spell"}), "macro")
        self.assertIsNone([m for m in candidate["macros"] if m["name"] == NEW_MACRO["name"]][0]["icon"])

    def test_end_to_end_the_macro_is_added_with_the_question_mark_icon(self):
        reply = self.handle(request(rid=102), runner=fake_process(add_macro(self.BAD)))
        self.assertIn(f"Added: {NEW_MACRO['name']}", reply)
        self.assertNotIn("Rejected", reply)
        saved = [m for m in json.loads(self.spec_text())["macros"] if m["name"] == NEW_MACRO["name"]]
        self.assertIsNone(saved[0]["icon"])
        data = (self.settings.addons / "MacroForge" / "MacroForgeData.lua").read_text(encoding="utf-8")
        self.assertIn(f'name = "{NEW_MACRO["name"]}", icon = 134400', data)

    def test_a_good_icon_is_left_alone(self):
        candidate, _warnings, _summary = watcher._evaluate(
            self.settings, self.candidate(NEW_MACRO), "macro")
        self.assertEqual([m for m in candidate["macros"] if m["name"] == NEW_MACRO["name"]][0]["icon"],
                         NEW_MACRO["icon"])

    def test_your_own_macros_file_is_still_checked_strictly(self):
        spec = json.loads(self.candidate(self.BAD))
        with self.assertRaises(ValidationError):
            resolve_spec(spec, self.settings)
        notes: list[str] = []
        resolve_spec(spec, self.settings, icon_notes=notes)
        self.assertEqual(len(notes), 1)
        self.assertIn(NEW_MACRO["name"], notes[0])


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

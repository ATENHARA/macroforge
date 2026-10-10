"""Regression tests for the findings of the independent security review (one test per finding)."""
from __future__ import annotations

import csv
import dataclasses
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unittest
from functools import partial
from pathlib import Path
from unittest import mock

from mforge import agent, cache, fsutil, startup, watcher
from mforge.errors import AgentError, ValidationError
from mforge.install import check_addons_dir, create_slots, slot_dirs
from mforge.lua import lua_str
from mforge.savedvars import parse_sv
from mforge.settings import Settings
from mforge.spec import check_removals, describe_changes, diff_specs
from mforge.validate import clean_macro, has_bad_chars, parse_request, sanitize_reply
from tests.harness import DEFAULT_MACROS, Sandbox
from tests.test_agent_watcher import (NEW_MACRO, WatcherCase, add_macro, fake_process, request,
                                      write_raw)
from tests.test_units import symlink_or_skip

LINE_BREAKS = ("\u2028", "\u2029", "\x85", "\x0b", "\x0c", "\x1c", "\x1d", "\x1e")


def spec_with(body_or_macros, remove=None):
    spec = json.loads(json.dumps(DEFAULT_MACROS))
    if isinstance(body_or_macros, list):
        spec["macros"] = body_or_macros
    else:
        spec["macros"].append({**NEW_MACRO, "body": body_or_macros})
    if remove is not None:
        spec["remove"] = remove
    return spec


class H1AgentMayNotRunDangerousCommands(WatcherCase):
    """Review H1: an agent-written macro must only use a fixed, safe set of commands."""

    def run_body(self, body, probe_commands=None):
        if probe_commands is not None:  # simulate a user who ran /mf probe on a client full of addons
            slash = ", ".join(f'"/{c}"' for c in probe_commands)
            (self.sb.saved_vars / "MacroForge.lua").write_text(
                "MacroForgeProbe = {[\"slash\"] = {%s}, [\"control\"] = {[\"pos\"]=\"Y\",[\"neg\"]=\"N\","
                "[\"both\"]=\"Y\"}, [\"conditionals\"] = {[\"harm\"] = {[\"pos\"]=\"N\",[\"neg\"]=\"Y\","
                "[\"both\"]=\"N\"}}}\n" % slash, encoding="utf-8")
        return self.handle(request(rid=abs(hash(body)) % 10**9 + 1),
                           runner=fake_process(add_macro({**NEW_MACRO, "body": body})))

    def test_dangerous_and_addon_commands_are_rejected_even_when_the_client_knows_them(self):
        everything = ["cast", "tinspect", "gdisband", "gleader", "click", "logout", "quit", "camp",
                      "tableinspect", "someaddon", "reload", "gquit", "promote"]
        for command in ("tinspect (function() end)()", "gdisband", "gleader Attacker", "click Foo",
                        "logout", "quit", "camp", "tableinspect x", "someaddon", "reload", "gquit"):
            self.state.recent.clear()  # many runs in one test: do not hit the hourly limit
            before = self.spec_text()
            reply = self.run_body(f"/{command}", probe_commands=everything)
            self.assertRejected(reply)
            self.assertEqual(self.spec_text(), before, command)

    def test_wiki_commands_that_are_dangerous_are_rejected_without_a_probe(self):
        for body in ("/click GameMenuButtonLogout", "/logout", "/dump 1", "/script x"):
            self.state.recent.clear()
            before = self.spec_text()
            self.assertRejected(self.run_body(body))
            self.assertEqual(self.spec_text(), before, body)

    def test_normal_and_aliased_commands_still_work(self):
        reply = self.run_body("#showtooltip\n/cast [@mouseover,harm,nodead][] Fear\n/s hello\n/tar player")
        self.assertIn("Added: Corr", reply)

    def test_removing_macros_that_macroforge_does_not_manage_is_refused(self):
        def edit(ws):
            spec = json.loads((ws / "macros.json").read_text("utf-8"))
            spec["remove"].append({"name": "MyHandMade", "char": "Test Char"})
            (ws / "macros.json").write_text(json.dumps(spec), "utf-8")
        before = self.spec_text()
        reply = self.handle(request(rid=3), runner=fake_process(edit))
        self.assertRejected(reply, "MyHandMade")
        self.assertEqual(self.spec_text(), before)

    def test_removing_a_managed_macro_is_allowed_and_reported(self):
        def edit(ws):
            spec = json.loads((ws / "macros.json").read_text("utf-8"))
            spec["macros"] = [m for m in spec["macros"] if m["name"] != "Tap"]
            spec["remove"].append({"name": "Tap"})
            (ws / "macros.json").write_text(json.dumps(spec), "utf-8")
        reply = self.handle(request(rid=3), runner=fake_process(edit))
        self.assertIn("Removed: Tap", reply)
        self.assertNotIn("Tap", self.spec_names())

    def test_edits_to_existing_macros_are_reported_by_code_not_by_the_model(self):
        def edit(ws):
            spec = json.loads((ws / "macros.json").read_text("utf-8"))
            spec["macros"][1]["body"] = "/cast Life Tap\n/say hi"
            (ws / "macros.json").write_text(json.dumps(spec), "utf-8")
        reply = self.handle(request(rid=3), runner=fake_process(edit, result="REPLY: All good, nothing odd."))
        self.assertTrue(reply.startswith("[Edited: Tap]"), reply)

    def test_diff_helpers(self):
        old = spec_with([{"name": "A", "body": "x"}, {"name": "B", "body": "y"}])
        new = spec_with([{"name": "A", "body": "changed"}, {"name": "C", "body": "z"}],
                        remove=[{"name": "B"}])
        changes = diff_specs(old, new)
        self.assertEqual((changes["added"], changes["edited"], changes["dropped"], changes["removals"]),
                         (["C"], ["A"], ["B"], ["B"]))
        self.assertEqual(describe_changes(changes), "Added: C; Edited: A; Removed: B; Dropped: B")
        self.assertEqual(describe_changes(diff_specs(old, old)), "No macro changes")
        check_removals(old, new)
        with self.assertRaises(ValidationError):
            check_removals(old, spec_with([], remove=[{"name": "Zed"}]))


class CreatedAndRemovedAtOnce(unittest.TestCase):
    """Found in the first in-game test: a macro listed under both 'macros' and 'remove'."""

    def test_the_conflict_is_rejected_with_a_clear_message(self):
        sb = Sandbox()
        self.addCleanup(sb.cleanup)
        spec = spec_with([{"name": "LiveTest", "char": "Test Char", "body": "/cast Fear"}],
                         remove=[{"name": "LiveTest", "char": "test-char"}])
        sb.write_macros(spec)
        result = sb.run("build")
        self.assertEqual(result.returncode, 1)
        self.assertIn("both 'macros' and 'remove'", result.stderr)
        self.assertIn("LiveTest", result.stderr)

    def test_the_same_name_in_different_scopes_is_fine(self):
        sb = Sandbox()
        self.addCleanup(sb.cleanup)
        spec = spec_with([{"name": "Same", "body": "/cast Fear"}],
                         remove=[{"name": "Same", "char": "Test Char"}])
        sb.write_macros(spec)
        self.assertEqual(sb.run("build").returncode, 0)


class H2LineSeparators(unittest.TestCase):
    """Review H2: Unicode line separators must not smuggle commands into macros-cache.txt."""

    EVIL = '/cast Life Tap{sep}END{sep}VER 3 0000000000000063 "Evil" "1"{sep}/run DoEvil()'

    def test_validation_rejects_every_kind_of_line_break(self):
        for sep in LINE_BREAKS:
            with self.assertRaises(ValidationError, msg=repr(sep)):
                clean_macro("n", self.EVIL.format(sep=sep))
            with self.assertRaises(ValidationError, msg=repr(sep)):
                clean_macro("a" + sep + "b", "/cast X")

    def test_invisible_characters_are_rejected_too(self):
        for ch in ("\u200b", "\u200d", "\u202e", "\ufeff", "\u2060"):
            self.assertTrue(has_bad_chars(ch), repr(ch))
            with self.assertRaises(ValidationError):
                clean_macro("n", f"/ru{ch}n x")

    def test_plain_unicode_text_is_still_fine(self):
        clean_macro("n", "/say caf\u00e9 \u4e2d\u6587")

    def test_cache_reader_does_not_split_on_unicode_separators(self):
        path = Path(tempfile.mkdtemp()) / "macros-cache.txt"
        self.addCleanup(lambda: __import__("shutil").rmtree(path.parent, ignore_errors=True))
        path.write_text('VER 3 0000000000000001 "A" "1"\n/say a\u2028END\u2028b\nEND\n', encoding="utf-8")
        blocks = cache.parse_cache(path)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0]["body"], ["/say a\u2028END\u2028b"])

    def test_cache_writer_refuses_lines_that_would_corrupt_the_file(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, ignore_errors=True))
        for body in ("/say a\nEND\n/run x", '/say a\nVER 3 0000000000000063 "E" "1"', "/say a\u2028b"):
            with self.assertRaises(ValidationError, msg=repr(body)):
                cache.merge_cache(tmp / "none.txt", [{"name": "A", "icon": 1, "body": body}], 0, 5)

    def test_hostile_request_text_is_flattened(self):
        raw = {"id": 1, "text": "a\u2028b\x85c\u200bd", "char": "Test Char", "class": "mage", "level": 5}
        text = parse_request(raw, {"mage"}).text
        self.assertEqual(text, "a b c d")

    def test_reply_text_is_reduced_to_plain_ascii(self):
        out = sanitize_reply("a\u2028b\x85c\u200bd")
        self.assertTrue(out.isascii())
        self.assertFalse(has_bad_chars(out))


class M1PlantedProgramsAreNotRun(unittest.TestCase):
    """Review M1: tools are called by absolute path; PATH lookups ignore the current folder."""

    def test_system_tools_resolve_to_system32(self):
        if os.name != "nt":
            self.skipTest("Windows only")
        for tool in ("tasklist.exe", "taskkill.exe", "wscript.exe"):
            path = fsutil.system_exe(tool)
            self.assertTrue(Path(path).is_absolute(), path)
            self.assertEqual(Path(path).parent.name.lower(), "system32")

    def test_which_ignores_relative_and_current_folder_entries(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, ignore_errors=True))
        (tmp / "claude.exe").write_text("planted")
        old = os.getcwd()
        self.addCleanup(os.chdir, old)
        os.chdir(tmp)
        for path_value in (".", "", os.pathsep.join([".", "relative\\dir"])):
            with mock.patch.dict(os.environ, {"PATH": path_value}):
                self.assertIsNone(fsutil.which_absolute("claude"), path_value)
        with mock.patch.dict(os.environ, {"PATH": str(tmp)}):
            self.assertEqual(Path(fsutil.which_absolute("claude")), tmp / "claude.exe")

    def test_system_tools_are_started_by_absolute_path(self):
        """A bare name would let a same-named program in the current folder run first."""
        if os.name != "nt":
            self.skipTest("Windows only")
        started = []

        def fake_run(argv, *args, **kwargs):
            started.append(argv[0])
            return mock.Mock(stdout="", returncode=0)

        with mock.patch("subprocess.run", side_effect=fake_run):
            watcher.pid_running(os.getpid())
            cache.game_running()
            proc = mock.Mock(pid=1)
            agent._kill_tree(proc)
        self.assertEqual(len(started), 3)
        for exe in started:
            self.assertTrue(Path(exe).is_absolute(), exe)
            self.assertEqual(Path(exe).parent.name.lower(), "system32")
        with mock.patch("subprocess.Popen") as popen, \
                mock.patch.dict(os.environ, {"APPDATA": tempfile.mkdtemp()}):
            sb = Sandbox()
            self.addCleanup(sb.cleanup)
            startup.install_startup(Settings.load(sb.project), sb.project / "macroforge.py", True)
        self.assertTrue(Path(popen.call_args[0][0][0]).is_absolute())

    def test_bundled_cli_is_chosen_by_version_not_by_file_date(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        for version in ("2.1.9", "2.1.10", "2.1.100"):
            exe = root / "Claude" / "claude-code" / version / "abc" / "claude.exe"
            exe.parent.mkdir(parents=True)
            exe.write_text(version)
        os.utime(root / "Claude" / "claude-code" / "2.1.9" / "abc" / "claude.exe", None)  # newest by date
        settings = Settings.load(root)
        with mock.patch.dict(os.environ, {"APPDATA": str(root), "PATH": ""}), \
                mock.patch("pathlib.Path.home", return_value=root / "nohome"):
            self.assertIn("2.1.100", agent.find_claude(settings))


class M2HostileFilesNeverCrashTheWatcher(WatcherCase):
    """Review M2: damaged or hostile files produce a ValidationError (or a skip), never a crash."""

    def test_giant_numbers_in_saved_variables(self):
        for text in ("X = " + "9" * 5000, "X = {" + "1" * 100 + "}", "X = 0x" + "f" * 100, "X = 1e99999999"):
            try:
                parse_sv(text)
            except ValidationError:
                pass

    def test_fuzz_with_long_digit_runs(self):
        import random
        rng = random.Random(77)
        for _ in range(300):
            digits = "".join(rng.choice("0123456789.eE-xX") for _ in range(rng.randint(1, 6000)))
            try:
                parse_sv(f"X = {digits}")
            except ValidationError:
                pass

    def test_the_loop_survives_anything_the_processing_step_raises(self):
        ticks = iter(range(100))
        calls = []

        def explode(*_args, **_kwargs):
            calls.append(1)
            raise RuntimeError("unexpected")

        (self.sb.saved_vars / "MacroForge.lua").write_text("MacroForgeRequests = {}\n", "utf-8")
        with mock.patch.object(watcher, "process_new_requests", explode), \
                self.assertLogs("mforge.watcher", level="ERROR") as logs:
            watcher.run_watcher(self.settings, sleep=lambda s: None, stop=lambda: next(ticks) >= 3)
        self.assertTrue(calls)
        self.assertIn("could not process", logs.output[0])

    def test_watcher_survives_a_poisoned_saved_variables_file(self):
        (self.sb.saved_vars / "MacroForge.lua").write_text("MacroForgeRequests = " + "9" * 5000, "utf-8")
        ticks = iter(range(100))
        with self.assertLogs("mforge.watcher", level="INFO"):
            watcher.run_watcher(self.settings, sleep=lambda s: None, stop=lambda: next(ticks) >= 3)

    def test_damaged_probe_is_ignored_not_trusted_or_fatal(self):
        for probe in ("MacroForgeProbe = {}", "MacroForgeProbe = { 1, 2 }", "MacroForgeProbe = 5",
                      'MacroForgeProbe = {["slash"] = {"/cast"}, ["conditionals"] = {}, ["control"] = {}}',
                      'MacroForgeProbe = {["conditionals"] = {["x"] = 1}, ["control"] = {}}'):
            (self.sb.saved_vars / "MacroForge.lua").write_text(probe + "\n", encoding="utf-8")
            reply = self.handle(request(rid=abs(hash(probe)) % 10**9 + 1))
            self.assertNotIn("Traceback", reply)

    def test_agent_specs_with_wrongly_typed_class_fields(self):
        for extra in ({"class": 5}, {"classes": []}, {"classes": {"Test Char": 7}}, {"classes": "x"}):
            def edit(ws, extra=extra):
                spec = json.loads((ws / "macros.json").read_text("utf-8"))
                spec.update(extra)
                spec["macros"].append(NEW_MACRO)
                (ws / "macros.json").write_text(json.dumps(spec), "utf-8")
            reply = self.handle(request(rid=abs(hash(str(extra))) % 10**9 + 1), runner=fake_process(edit))
            self.assertTrue(reply.startswith(("[", "Rejected")), reply)

    def test_deeply_nested_agent_json(self):
        reply = self.handle(request(rid=5), runner=fake_process(write_raw("[" * 100000)))
        self.assertTrue(reply.startswith("Rejected"), reply)

    def test_csv_and_icon_files_that_are_damaged(self):
        data = self.sb.project / "data"
        (data / "SpellName.csv").write_text('ID,Name_lang\n1,"' + "x" * 200000 + '"\n', "utf-8")
        with self.assertRaises(ValidationError):
            from mforge.spells import load_spells
            load_spells(data)
        (data / "icons.csv").write_text("abc;Interface/ICONS/X.blp\n12;Interface/ICONS/Y.blp\n", "utf-8")
        from mforge.icons import load_icons
        self.assertEqual(load_icons(data), {"y": 12})
        (data / "icons.csv").write_bytes(b"\xff\xfe\x00bad")
        with self.assertRaises(ValidationError):
            load_icons(data)


class M3FreshCloneRunsTheTests(unittest.TestCase):
    def test_fixtures_are_not_ignored_by_git(self):
        repo = Path(__file__).resolve().parent.parent
        if not (repo / ".git").exists():
            self.skipTest("not a git checkout")
        result = subprocess.run(["git", "check-ignore", "-q", "tests/fixtures/data/SpellName.csv"],
                                cwd=repo, capture_output=True)
        self.assertEqual(result.returncode, 1, "test fixtures must not be gitignored")
        listed = subprocess.run(["git", "ls-files", "tests/fixtures"], cwd=repo, capture_output=True,
                                text=True).stdout.split()
        self.assertGreaterEqual(len(listed), 9, "fixtures must be committed")


class LowFindings(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox()
        self.addCleanup(self.sb.cleanup)
        self.settings = Settings.load(self.sb.project)

    def test_l1_long_lines_and_slow_downloads_are_refused(self):
        from mforge import fetch

        class Endless:
            def read(self, _n):
                return b"x" * 70000  # one endless line

        with self.assertRaisesRegex(ValidationError, "over-long"):
            list(fetch._lines(Endless(), time.monotonic() + 60))

        class Slow:
            def read(self, _n):
                return b"a\n"

        with self.assertRaisesRegex(ValidationError, "too long"):
            list(fetch._lines(Slow(), time.monotonic() - 1))
        self.assertEqual(list(fetch._lines(_Chunks([b"ab\ncd", b"\nef"]), time.monotonic() + 60)),
                         [b"ab", b"cd", b"ef"])

    def test_l2_cache_backup_symlink_is_refused(self):
        folder = self.sb.account_dir
        target = folder / "macros-cache.txt"
        target.write_text('VER 3 0000000000000001 "Old" "5"\n/x\nEND\n', encoding="utf-8")
        elsewhere = self.sb.tmp / "elsewhere.txt"
        elsewhere.write_text("precious")
        symlink_or_skip(self, elsewhere, folder / "macros-cache.txt.bak")
        with mock.patch.object(cache, "game_running", return_value=False):
            with self.assertRaisesRegex(ValidationError, "link"):
                cache.stage_caches(self.settings, {"account": [{"name": "A", "icon": 1, "body": "/x"}],
                                                   "chars": {}}, write=True)
        self.assertEqual(elsewhere.read_text(), "precious")

    def test_l2_addons_folder_links_are_refused(self):
        real = self.sb.tmp / "real_addons"
        real.mkdir()
        self.settings.addons.rmdir()
        symlink_or_skip(self, real, self.settings.addons)
        with self.assertRaisesRegex(ValidationError, "link"):
            check_addons_dir(self.settings)
        with self.assertRaises(ValidationError):
            create_slots(self.settings, 2)
        self.assertEqual(list(real.iterdir()), [])

    def test_l2_linked_slot_folders_are_ignored(self):
        create_slots(self.settings, 2)
        elsewhere = self.sb.tmp / "elsewhere"
        elsewhere.mkdir()
        (self.settings.addons / "MacroForge_S002").rename(self.sb.tmp / "moved")
        symlink_or_skip(self, elsewhere, self.settings.addons / "MacroForge_S002")
        self.assertEqual([p.name for p in slot_dirs(self.settings)], ["MacroForge_S001"])

    def test_l3_game_detection_matches_all_clients_and_fails_closed(self):
        def listing(names, code=0):
            out = "".join(f'"{n}","1","Console","1","1 K"\n' for n in names)
            return mock.Mock(stdout=out, returncode=code)

        for exe in ("WowB.exe", "WowT.exe", "WowClassic.exe", "WowClassicT.exe", "Wow.exe"):
            with mock.patch("subprocess.run", return_value=listing(["explorer.exe", exe])):
                self.assertTrue(cache.game_running(), exe)
        with mock.patch("subprocess.run", return_value=listing(["explorer.exe", "notepad.exe"])):
            self.assertFalse(cache.game_running())
        with mock.patch("subprocess.run", return_value=listing([], code=1)):
            self.assertTrue(cache.game_running(), "unreadable process list must fail closed")
        with mock.patch("subprocess.run", side_effect=OSError("no tasklist")):
            self.assertTrue(cache.game_running())

    def test_l4_terminal_escapes_are_not_printed(self):
        (self.sb.project / "data" / "icons.csv").write_text(
            "7;Interface/ICONS/Evil\x1b]0;pwned\x07Name.blp\n", encoding="utf-8")
        out = self.sb.run("icon", "evil").stdout
        self.assertNotIn("\x1b", out)
        self.assertNotIn("\x07", out)

    def test_l5_startup_launcher_rules(self):
        appdata = self.sb.tmp / "appdata"
        with mock.patch.dict(os.environ, {"APPDATA": str(appdata)}):
            with self.assertRaises(ValidationError):
                startup.launcher_text(Path("C:\\%USERPROFILE%\\x"), Path("C:\\p.exe"), Path("C:\\m.py"))
            target = startup.startup_file()
            target.parent.mkdir(parents=True)
            target.write_text("MsgBox 'not ours'", encoding="utf-8")
            with self.assertRaisesRegex(ValidationError, "not created by MacroForge"):
                startup.remove_startup()
            self.assertTrue(target.exists())
            made = startup.install_startup(self.settings, Path(self.sb.project / "macroforge.py"), False)
            self.assertTrue(made.read_text(encoding="utf-8").startswith(startup.MARKER))
            startup.remove_startup()
            self.assertFalse(target.exists())

    def test_l5_lua_data_uses_only_lua_51_escapes(self):
        """WoW runs Lua 5.1: only \\ddd, \\n, \\r, \\t, \\\\ and \\\" are valid in what we emit."""
        sample = "".join(chr(i) for i in range(0, 130)) + '"\\\u00e9\u2028'
        escapes = set(re.findall(r"\\(\d{3}|.)", lua_str(sample)))
        allowed = {"n", "r", "t", "\\", '"'}
        self.assertTrue(all(e in allowed or (len(e) == 3 and e.isdigit()) for e in escapes), escapes)


class _Chunks:
    def __init__(self, chunks):
        self.chunks = list(chunks)

    def read(self, _n):
        return self.chunks.pop(0) if self.chunks else b""


class TestsActuallyProveTheirClaims(unittest.TestCase):
    """Review (b): mutation-style checks the earlier tests missed."""

    def test_timeout_kills_grandchildren_too(self):
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(folder, ignore_errors=True))
        marker = folder / "grandchild.txt"
        (folder / "grandchild.py").write_text(
            f"import time, pathlib\ntime.sleep(5)\npathlib.Path({str(marker)!r}).write_text('alive')\n")
        (folder / "child.py").write_text(
            "import subprocess, sys, time\n"
            "subprocess.Popen([sys.executable, 'grandchild.py'])\n"
            "time.sleep(60)\n")
        with self.assertRaises(agent.AgentTimeout):
            agent.run_process([sys.executable, "child.py"], "", folder, os.environ.copy(), 2)
        time.sleep(7)
        self.assertFalse(marker.exists(), "grandchild process survived the timeout")

    def test_oversized_agent_output_is_rejected(self):
        sb = Sandbox()
        self.addCleanup(sb.cleanup)
        with mock.patch.dict(os.environ, {"LOCALAPPDATA": str(sb.tmp / "local")}):
            dummy = sb.tmp / "claude.exe"
            dummy.write_text("x")
            settings = dataclasses.replace(Settings.load(sb.project), claude_path=str(dummy))
            runner = lambda *a: (0, "x" * (agent.MAX_OUTPUT_BYTES + 1), "")  # noqa: E731
            req = parse_request(request(), {"warlock"})
            with self.assertRaisesRegex(AgentError, "too large"):
                agent.run_agent(settings, req, None, runner=runner)

    def test_rate_limit_is_saved_before_the_agent_runs(self):
        sb = Sandbox()
        self.addCleanup(sb.cleanup)
        with mock.patch.dict(os.environ, {"LOCALAPPDATA": str(sb.tmp / "local")}):
            dummy = sb.tmp / "claude.exe"
            dummy.write_text("x")
            settings = dataclasses.replace(Settings.load(sb.project), claude_path=str(dummy))
            state = watcher.WatchState(sb.project / "data" / "state.json")

            def crash(*_a, **_k):
                raise KeyboardInterrupt  # killed in the middle of the agent run

            with self.assertRaises(KeyboardInterrupt):
                watcher.handle_request(settings, request(), None, state, runner=crash)
            self.assertEqual(len(watcher.WatchState.load(state.path).recent), 1)

    def test_partial_agent_runner_signature_matches_the_real_one(self):
        self.assertEqual(list(partial(agent.run_agent, runner=None).keywords), ["runner"])


if __name__ == "__main__":
    unittest.main()

"""Run the real Lua addon files against a fake WoW API with LuaJIT (skipped when it is not installed).

Install LuaJIT with ``winget install DEVCOM.LuaJIT`` or point the ``LUAJIT`` environment variable at
``luajit.exe``. The fake API is described in ``tests/lua/wow_stub.lua``; what it cannot show is how
the real client behaves, which is why ``docs/TESTING.md`` still has an in-game checklist.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import unittest
from pathlib import Path

from tests.harness import REPO, Sandbox
from tests.lua51 import scan_lua51

MIN_LUA_TESTS = 25


def find_luajit() -> str | None:
    """Path of ``luajit`` from $LUAJIT, PATH or the default winget install folder."""
    candidates = [os.environ.get("LUAJIT"), shutil.which("luajit"),
                  str(Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "LuaJIT" / "bin" / "luajit.exe")]
    return next((c for c in candidates if c and Path(c).is_file()), None)


def run_lua(luajit: str, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run([luajit, str(REPO / "tests" / "lua" / "run.lua"), str(REPO), *extra],
                          capture_output=True, text=True, encoding="utf-8", timeout=120)


def parse_results(output: str) -> list[tuple[str, bool, str]]:
    """``[(test name, passed, detail)]`` from the runner's ``ok``/``not ok`` lines."""
    results: list[tuple[str, bool, str]] = []
    for line in output.splitlines():
        match = re.match(r"^(not )?ok \d+ (.*)$", line)
        if match:
            results.append((match.group(2), match.group(1) is None, ""))
        elif line.startswith("  # ") and results and not results[-1][1]:
            name, _ok, detail = results[-1]
            results[-1] = (name, False, detail + line[4:] + "\n")
    return results


class Lua51ScannerTests(unittest.TestCase):
    def test_flags_syntax_that_real_wow_rejects(self):
        for source in ("goto done", "::done::", 'x = "\\x41"', 'x = "a\\z b"', "x = 5LL", "x = 7 // 2",
                       'x = "\\u{41}"', "x = 0x1p4"):
            self.assertTrue(scan_lua51(source), source)

    def test_accepts_plain_lua_51_including_lookalikes_in_strings_and_comments(self):
        for source in ('x = "goto // :: \\065 \\n"', "-- goto // ::\nx = 1", "--[[ goto ]] x = 2",
                       "x = [[ goto :: // ]]", "x = a:b() or c.d", "x = [==[ \\x41 ]==]", 'x = "\\\\x41"'):
            self.assertEqual(scan_lua51(source), [], source)

    def test_the_addon_files_use_only_lua_51_syntax(self):
        for path in sorted((REPO / "addon").rglob("*.lua")):
            self.assertEqual(scan_lua51(path.read_text(encoding="utf-8")), [], path.name)


@unittest.skipUnless(find_luajit(), "LuaJIT not installed (winget install DEVCOM.LuaJIT)")
class AddonBehaviourTests(unittest.TestCase):
    def test_every_lua_test_passes(self):
        result = run_lua(find_luajit())
        results = parse_results(result.stdout)
        self.assertGreaterEqual(len(results), MIN_LUA_TESTS, result.stdout + result.stderr)
        for name, passed, detail in results:
            with self.subTest(name):
                self.assertTrue(passed, detail)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_the_runner_really_fails_when_a_test_fails(self):
        """Guards against a harness that reports success no matter what."""
        broken = Sandbox()
        self.addCleanup(broken.cleanup)
        lua_dir = broken.project / "tests" / "lua"
        lua_dir.mkdir(parents=True)
        for name in ("run.lua", "wow_stub.lua"):
            shutil.copy2(REPO / "tests" / "lua" / name, lua_dir / name)
        addon = broken.project / "addon" / "MacroForge"
        text = (addon / "MacroForge.lua").read_text(encoding="utf-8")
        (addon / "MacroForge.lua").write_text(text.replace("stats.created = stats.created + 1", "", 1),
                                              encoding="utf-8")
        result = subprocess.run([find_luajit(), str(lua_dir / "run.lua"), str(broken.project)],
                                capture_output=True, text=True, encoding="utf-8", timeout=120)
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(any(not ok for _n, ok, _d in parse_results(result.stdout)))


@unittest.skipUnless(find_luajit(), "LuaJIT not installed (winget install DEVCOM.LuaJIT)")
class GeneratedFilesTests(unittest.TestCase):
    """The addon must apply exactly what the Python tool writes, whatever characters are in it."""

    BODIES = [
        '/say "quoted" and back\\slash and \\n literal',
        "/say café 中文",
        "/say ]] -- [[ ]=] -- end",
        "#showtooltip Fear\n/cast [@mouseover,harm,nodead][] Fear\n/say 100% %d %s",
        "/say tab-free but ÿ high bytes",
    ]

    def test_files_from_the_python_tool_apply_exactly(self):
        macros = [{"name": f"M{i}", "icon": 1000 + i, "body": body} for i, body in enumerate(self.BODIES)]
        macros[0]["char"] = "Testy Mage"
        macros[1]["char"] = "Testy Mage"
        sandbox = Sandbox(macros={"macros": macros})
        self.addCleanup(sandbox.cleanup)
        for command in (("slots", "--count", "2"), ("build",), ("install",)):
            done = sandbox.run(*command)
            self.assertEqual(done.returncode, 0, done.stderr)
        data_file = sandbox.project / "build" / "MacroForge" / "MacroForgeData.lua"
        slot_file = sandbox.addons / "MacroForge_S001" / "Slot.lua"

        for path in (data_file, slot_file):
            self.assertEqual(scan_lua51(path.read_text(encoding="utf-8")), [], path.name)

        result = run_lua(find_luajit(), str(data_file), str(slot_file))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        applied = {}
        for line in result.stdout.splitlines():
            if line.startswith("MACRO\t"):
                _tag, scope, name_hex, body_hex, icon = line.split("\t")
                applied[bytes.fromhex(name_hex).decode("utf-8")] = (
                    scope, bytes.fromhex(body_hex).decode("utf-8"), int(icon))
        expected = {m["name"]: ("char" if "char" in m else "account", m["body"], m["icon"])
                    for m in macros}
        self.assertEqual(applied, expected)
        chat = [ln for ln in result.stdout.splitlines() if ln.startswith("CHAT\t")]
        self.assertTrue(any("no new macros" in ln for ln in chat), chat)

    def test_generated_reply_data_is_neutralized_in_the_chat(self):
        sandbox = Sandbox(macros={"macros": []})
        self.addCleanup(sandbox.cleanup)
        (sandbox.project / "data").mkdir(exist_ok=True)
        (sandbox.project / "data" / "replies.json").write_text(json.dumps(
            [{"id": 5, "req": 1, "text": "done |cffff0000RED|r"}]), encoding="utf-8")
        self.assertEqual(sandbox.run("build").returncode, 0)
        data_file = sandbox.project / "build" / "MacroForge" / "MacroForgeData.lua"
        result = run_lua(find_luajit(), str(data_file))
        self.assertEqual(result.returncode, 0, result.stdout)
        login = [ln for ln in result.stdout.splitlines() if ln.startswith("LOGIN\t") and "reply:" in ln]
        self.assertEqual(len(login), 1, result.stdout)
        self.assertIn("done ||cffff0000RED||r", login[0])
        self.assertNotIn("done |cffff0000", login[0])


if __name__ == "__main__":
    unittest.main()

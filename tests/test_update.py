"""``update``: the player-run update (git or zip), its download checks and what it must never touch."""
from __future__ import annotations

import dataclasses
import io
import subprocess
import unittest
import zipfile
from pathlib import Path
from unittest import mock

import tests.test_fetch_startup_cli as fetch_tests
from mforge import update
from mforge.errors import MacroForgeError, ValidationError
from mforge.settings import Settings
from tests.harness import REPO, Sandbox

TOP = "macroforge-main/"


def version_file(version: str) -> bytes:
    return f'"""MacroForge."""\n\n__version__ = "{version}"\n'.encode()


def make_zip(files: dict[str, bytes], links: tuple[str, ...] = ()) -> bytes:
    """A zip like GitHub's: every file under one top folder (names given in full here)."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
        for name in links:
            info = zipfile.ZipInfo(name)
            info.external_attr = (0o120777 << 16)  # a symbolic link
            zf.writestr(info, "../../outside")
    return buffer.getvalue()


def release(version="9.9.0", **extra) -> dict[str, bytes]:
    files = {TOP + "macroforge.py": b"print('new')\n",
             TOP + "mforge/__init__.py": version_file(version),
             TOP + "mforge/brand_new.py": b"NEW = 1\n",
             TOP + "addon/MacroForge/MacroForge.toc": b"## Interface: 16001\n",
             TOP + "README.md": b"new readme\n",
             TOP + "config.json": b'{"evil": true}',  # the player's files are never taken from a download
             TOP + "data/BUILD": b"evil\n"}
    files.update(extra)
    return files


class UpdateCase(unittest.TestCase):
    def setUp(self):
        self.server = fetch_tests.Server()
        self.addCleanup(self.server.stop)
        self.sb = Sandbox()
        self.addCleanup(self.sb.cleanup)
        self.root = self.sb.project
        self.settings = dataclasses.replace(
            Settings.load(self.root), fetch_allowed_hosts=("127.0.0.1",), fetch_allow_http=True,
            update_version_url=self.server.base + "/version", update_zip_url=self.server.base + "/zip")
        (self.root / "mforge" / "old_only.py").write_text("OLD = 1\n", encoding="utf-8")
        (self.root / "README.md").write_text("old readme\n", encoding="utf-8")
        (self.root / "datapack").mkdir()
        (self.root / "datapack" / "pack.txt").write_text("mine", encoding="utf-8")
        self.config_before = (self.root / "config.json").read_bytes()
        self.macros_before = (self.root / "macros.json").read_bytes()
        self.messages: list[str] = []
        self.finished: list[bool] = []

    def serve(self, version="9.9.0", files=None, links=()):
        self.server.routes["/version"] = (200, {}, version_file(version))
        self.server.routes["/zip"] = (200, {}, make_zip(files if files is not None else release(version),
                                                         links))

    def run_update(self, check_only=False):
        return update.run_update(self.settings, check_only, self.messages.append,
                                 after=lambda _s, _say: self.finished.append(True))

    def mine(self) -> str:
        return update.local_version(self.root)

    def assert_untouched(self):
        self.assertEqual(self.mine(), update.local_version(REPO))
        self.assertTrue((self.root / "mforge" / "old_only.py").exists())
        self.assertFalse((self.root / "mforge" / "brand_new.py").exists())
        self.assertEqual(list((self.settings.data / "backups").glob("program-*")), [])
        self.assertFalse((self.settings.data / "update").exists())


class VersionTests(UpdateCase):
    def test_version_numbers_compare_as_numbers(self):
        self.assertLess(update.parse_version("1.9.0"), update.parse_version("1.10.0"))
        for bad in ("", "1", "v1.2", "1.2.x", "1..2"):
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                update.parse_version(bad)

    def test_the_versions_in_the_repository_agree(self):
        local = update.local_version(REPO)
        toc = (REPO / "addon" / "MacroForge" / "MacroForge.toc").read_text(encoding="utf-8")
        project = (REPO / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn(f"## Version: {local}\n", toc)
        self.assertIn(f'version = "{local}"', project)

    def test_the_remote_version_and_a_bad_version_file(self):
        self.server.routes["/version"] = (200, {}, version_file("2.0.1"))
        self.assertEqual(update.remote_version(self.settings), "2.0.1")
        self.server.routes["/version"] = (200, {}, b"nothing useful")
        with self.assertRaises(ValidationError):
            update.remote_version(self.settings)
        self.server.routes["/version"] = (200, {}, b"x" * (update.MAX_VERSION_BYTES + 10))
        with self.assertRaises(ValidationError):
            update.remote_version(self.settings)

    def test_the_download_addresses_follow_the_download_rules(self):
        wrong = dataclasses.replace(self.settings, update_version_url="https://evil.example/version")
        with self.assertRaises(ValidationError):
            update.remote_version(wrong)


class CheckOnlyTests(UpdateCase):
    def test_up_to_date(self):
        self.serve(version=self.mine())
        self.assertEqual(self.run_update(), 0)
        self.assertIn("is the newest version", self.messages[0])
        self.assert_untouched()
        self.assertEqual(self.finished, [])

    def test_an_older_remote_is_not_a_downgrade(self):
        self.serve(version="0.0.1")
        self.run_update()
        self.assertIn("is the newest version", self.messages[0])
        self.assert_untouched()

    def test_check_only_reports_and_changes_nothing(self):
        self.serve()
        self.assertEqual(self.run_update(check_only=True), 0)
        self.assertIn("9.9.0 is available", self.messages[0])
        self.assertIn("update.cmd", self.messages[1])
        self.assert_untouched()


class ZipUpdateTests(UpdateCase):
    def test_the_program_is_replaced_and_the_players_files_are_kept(self):
        self.serve()
        self.assertEqual(self.run_update(), 0)
        self.assertEqual(self.mine(), "9.9.0")
        self.assertTrue((self.root / "mforge" / "brand_new.py").exists())
        self.assertFalse((self.root / "mforge" / "old_only.py").exists())  # the whole package swapped
        self.assertEqual((self.root / "README.md").read_bytes(), b"new readme\n")
        self.assertEqual((self.root / "config.json").read_bytes(), self.config_before)
        self.assertEqual((self.root / "macros.json").read_bytes(), self.macros_before)
        self.assertNotEqual(self.sb.read("data/icons.csv"), "")
        self.assertFalse((self.settings.data / "BUILD").exists())  # not taken from the download
        self.assertEqual((self.root / "datapack" / "pack.txt").read_text(encoding="utf-8"), "mine")
        self.assertEqual(self.finished, [True])
        self.assertTrue(any("Updated to MacroForge 9.9.0" in m for m in self.messages))

    def test_the_old_program_is_kept_as_a_backup_and_the_work_folder_is_removed(self):
        self.serve()
        self.run_update()
        backups = list((self.settings.data / "backups").glob("program-*"))
        self.assertEqual(len(backups), 1)
        self.assertTrue((backups[0] / "mforge" / "old_only.py").exists())
        self.assertFalse((self.settings.data / "update").exists())

    def hostile(self, files=None, links=()):
        self.serve(files=files, links=links)
        with self.assertRaises(MacroForgeError):
            self.run_update()
        self.assert_untouched()

    def test_paths_outside_the_folder_are_refused(self):
        for name in ("../evil.py", "/evil.py", TOP + "../../evil.py", "C:/evil.py", TOP + "a\\..\\b"):
            with self.subTest(name=name):
                self.hostile(release(**{name: b"x"}))

    def test_links_are_refused(self):
        self.hostile(links=(TOP + "mforge/link",))

    def test_more_than_one_top_folder_is_refused(self):
        twin = {name.replace(TOP, "a-twin/", 1): data for name, data in release().items()}
        self.hostile({**release(), **twin})  # two complete copies: which one would be installed?

    def test_something_that_is_not_macroforge_is_refused(self):
        files = release()
        del files[TOP + "macroforge.py"]
        self.hostile(files)

    def test_size_and_count_limits(self):
        with mock.patch.object(update, "MAX_ENTRIES", 3):
            self.hostile()
        with mock.patch.object(update, "MAX_ZIP_BYTES", 100):
            self.hostile()
        with mock.patch.object(update, "MAX_UNPACKED_BYTES", 10):
            self.hostile()

    def test_a_download_that_is_not_a_zip(self):
        self.server.routes["/version"] = (200, {}, version_file("9.9.0"))
        self.server.routes["/zip"] = (200, {}, b"<html>not found</html>")
        with self.assertRaises(MacroForgeError):
            self.run_update()
        self.assert_untouched()

    def test_a_failure_while_swapping_puts_everything_back(self):
        readme = (self.root / "README.md").read_bytes()
        toc = (self.root / "addon" / "MacroForge" / "MacroForge.toc").read_bytes()
        self.serve()
        real = Path.replace
        calls = {"n": 0}

        def flaky(path, target):
            calls["n"] += 1
            if calls["n"] == 5:
                raise PermissionError("file in use")
            return real(path, target)

        with mock.patch.object(Path, "replace", flaky), self.assertRaises(MacroForgeError):
            self.run_update()
        self.assertEqual(self.mine(), update.local_version(REPO))
        self.assertTrue((self.root / "mforge" / "old_only.py").exists())
        self.assertTrue((self.root / "macroforge.py").exists())
        self.assertEqual((self.root / "README.md").read_bytes(), readme)  # swapped before the failure
        self.assertEqual((self.root / "addon" / "MacroForge" / "MacroForge.toc").read_bytes(), toc)
        self.assertEqual(self.finished, [])


class GitUpdateTests(UpdateCase):
    def setUp(self):
        super().setUp()
        (self.root / ".git").mkdir()
        self.server.routes["/version"] = (200, {}, version_file("9.9.0"))

    def fake_git(self, status_out="", pull_rc=0):
        calls = []

        def run(cmd, **_kw):
            calls.append(cmd[3:])
            if cmd[3] == "status":
                return subprocess.CompletedProcess(cmd, 0, status_out, "")
            return subprocess.CompletedProcess(cmd, pull_rc, "Fast-forward", "not possible")

        return calls, run

    def test_a_clone_is_updated_with_git_pull_only(self):
        calls, run = self.fake_git()
        with mock.patch.object(update.shutil, "which", return_value="git"), \
                mock.patch.object(update.subprocess, "run", run):
            self.assertEqual(self.run_update(), 0)
        self.assertEqual(calls, [["status", "--porcelain", "--untracked-files=no"],
                                 ["pull", "--ff-only"]])
        self.assertEqual(self.finished, [True])
        self.assertFalse((self.settings.data / "update").exists())  # no zip download

    def test_local_changes_stop_the_update(self):
        calls, run = self.fake_git(status_out=" M mforge/cli.py\n")
        with mock.patch.object(update.shutil, "which", return_value="git"), \
                mock.patch.object(update.subprocess, "run", run), \
                self.assertRaises(MacroForgeError) as caught:
            self.run_update()
        self.assertIn("changed some of MacroForge's own files", str(caught.exception))
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.finished, [])

    def test_a_failed_pull_is_reported(self):
        _calls, run = self.fake_git(pull_rc=1)
        with mock.patch.object(update.shutil, "which", return_value="git"), \
                mock.patch.object(update.subprocess, "run", run), \
                self.assertRaises(MacroForgeError) as caught:
            self.run_update()
        self.assertIn("git pull failed", str(caught.exception))

    def test_a_clone_without_git_installed(self):
        with mock.patch.object(update.shutil, "which", return_value=None), \
                self.assertRaises(MacroForgeError) as caught:
            self.run_update()
        self.assertIn("git was not found", str(caught.exception))


class UpdateCmdTests(unittest.TestCase):
    def test_update_cmd_exists_with_windows_line_endings(self):
        data = (REPO / "update.cmd").read_bytes()
        self.assertIn(b"macroforge.py update", data)
        self.assertNotIn(b"\n", data.replace(b"\r\n", b""))


class CommandTests(UpdateCase):
    def test_update_check_from_the_command_line(self):
        self.serve()
        import json
        config = json.loads((self.root / "config.json").read_text(encoding="utf-8"))
        config.update(fetch_allowed_hosts=["127.0.0.1"], fetch_allow_http=True,
                      update_version_url=self.server.base + "/version",
                      update_zip_url=self.server.base + "/zip")
        (self.root / "config.json").write_text(json.dumps(config), encoding="utf-8")
        result = self.sb.run("update", "--check")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("9.9.0 is available", result.stdout)
        self.assertEqual(update.local_version(self.root), update.local_version(REPO))


if __name__ == "__main__":
    unittest.main()

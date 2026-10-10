"""Download hardening (local server), the startup launcher and command-line smoke tests."""
from __future__ import annotations

import dataclasses
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from mforge import fetch, startup
from mforge.errors import MacroForgeError, ValidationError
from mforge.settings import Settings
from tests.harness import FIXTURES, REPO, Sandbox

TABLE_FILES = {p.stem: p.read_bytes() for p in (FIXTURES / "data").glob("*.csv") if p.stem != "icons"}
LISTFILE = ("100;Interface/ICONS/Spell_A.blp\n200;Interface/Other/Thing.blp\n"
            "300;interface/icons/Ability_B.blp\n400;Sound/Music/x.ogg\n").encode()


class Server:
    """A tiny HTTP server whose routes are a dict of path -> (status, headers, body)."""

    def __init__(self):
        self.routes: dict[str, tuple[int, dict, bytes]] = {}
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802 (http.server naming)
                status, headers, body = outer.routes.get(self.path.split("?")[0], (404, {}, b"missing"))
                self.send_response(status)
                for key, value in headers.items():
                    self.send_header(key, value)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class FetchCase(unittest.TestCase):
    def setUp(self):
        self.server = Server()
        self.addCleanup(self.server.stop)
        self.sb = Sandbox(with_data=False)
        self.addCleanup(self.sb.cleanup)
        self.settings = dataclasses.replace(
            Settings.load(self.sb.project), fetch_allowed_hosts=("127.0.0.1",), fetch_allow_http=True,
            fetch_wago_url=self.server.base + "/db2/{table}.csv?build={build}",
            fetch_listfile_api=self.server.base + "/api/release")
        for table, body in TABLE_FILES.items():
            self.server.routes[f"/db2/{table}.csv"] = (200, {}, body)
        self.out: list[str] = []

    def run_fetch(self, **kw):
        fetch.fetch_all(self.settings, "1.60.1.70291", out=self.out.append, **kw)

    def data_files(self):
        return sorted(p.name for p in self.settings.data.iterdir())

    def serve_listfile(self, body=LISTFILE, name=fetch.ICON_ASSET):
        self.server.routes["/listfile.csv"] = (200, {}, body)
        asset = {"name": name, "browser_download_url": self.server.base + "/listfile.csv"}
        self.server.routes["/api/release"] = (200, {}, json.dumps({"assets": [asset]}).encode())


class FetchTests(FetchCase):
    def test_good_download_without_icons(self):
        stale = self.settings.data
        stale.mkdir()
        (stale / "spells_warlock.json").write_text("[]")
        (stale / "icons.csv").write_text("1;Interface/ICONS/Old.blp\n")
        self.run_fetch(no_icons=True)
        self.assertEqual(self.data_files(), ["BUILD"] + sorted(f"{t}.csv" for t in TABLE_FILES) + ["icons.csv"])
        self.assertIn("icons: kept existing", self.out)

    def test_icons_are_filtered_to_the_icon_folder(self):
        self.serve_listfile()
        self.run_fetch()
        lines = (self.settings.data / "icons.csv").read_text(encoding="utf-8").splitlines()
        self.assertEqual(lines, ["100;Interface/ICONS/Spell_A.blp", "300;interface/icons/Ability_B.blp"])
        self.assertFalse(any(n.endswith(".part") for n in self.data_files()))

    def test_html_instead_of_csv_is_rejected_and_old_file_kept(self):
        self.settings.data.mkdir()
        (self.settings.data / "SpellName.csv").write_text("ID,Name_lang\n1,Old\n")
        self.server.routes["/db2/SpellName.csv"] = (200, {}, b"<html><body>Not found</body></html>")
        with self.assertRaisesRegex(ValidationError, "SpellName.*not the expected CSV"):
            self.run_fetch(no_icons=True)
        self.assertIn("1,Old", (self.settings.data / "SpellName.csv").read_text())
        self.assertFalse(any(n.endswith(".part") for n in self.data_files()))

    def test_missing_table_gives_a_helpful_error(self):
        del self.server.routes["/db2/Spell.csv"]
        with self.assertRaisesRegex(ValidationError, "Spell: failed.*pass --build"):
            self.run_fetch(no_icons=True)

    def test_oversized_download_is_rejected(self):
        with mock.patch.object(fetch, "MAX_TABLE_BYTES", 30):
            with self.assertRaisesRegex(ValidationError, "larger"):
                self.run_fetch(no_icons=True)
        self.assertFalse(any(n.endswith(".part") for n in self.data_files()))

    def test_redirect_to_a_foreign_host_is_refused(self):
        self.server.routes["/db2/SpellName.csv"] = (302, {"Location": "http://evil.example.invalid/x.csv"}, b"")
        with self.assertRaisesRegex(ValidationError, "refusing to download"):
            self.run_fetch(no_icons=True)

    def test_oversized_listfile_and_empty_listfile_are_rejected(self):
        self.serve_listfile()
        with mock.patch.object(fetch, "MAX_LISTFILE_BYTES", 40):
            with self.assertRaisesRegex(ValidationError, "larger"):
                self.run_fetch()
        self.assertNotIn("icons.csv", self.data_files())
        self.serve_listfile(body=b"1;Sound/x.ogg\n")
        with self.assertRaisesRegex(ValidationError, "no icons"):
            self.run_fetch()

    def test_release_without_the_expected_asset_or_with_bad_json(self):
        self.serve_listfile(name="something-else.csv")
        with self.assertRaisesRegex(ValidationError, "does not contain"):
            self.run_fetch()
        self.server.routes["/api/release"] = (200, {}, b"{not json")
        with self.assertRaisesRegex(ValidationError, "does not contain"):
            self.run_fetch()

    def test_build_string_is_validated(self):
        for bad in ("../x", "1.2", "1.60.1.70291; rm -rf", "a.b.c.d", "1.60.1.70291/../../x"):
            with self.assertRaises(ValidationError, msg=bad):
                fetch.fetch_all(self.settings, bad, out=self.out.append)

    def test_default_settings_only_allow_https_on_listed_hosts(self):
        default = Settings.load(self.sb.project)
        fetch.check_url(default, "https://wago.tools/db2/Spell/csv?build=1.2.3.4567")
        fetch.check_url(default, "https://objects.githubusercontent.com/x")
        for bad in ("http://wago.tools/x", "https://evil.com/x", "https://wago.tools.evil.com/x",
                    "ftp://wago.tools/x", "file:///c:/windows/win.ini", "https://evilwago.tools/x"):
            with self.assertRaises(ValidationError, msg=bad):
                fetch.check_url(default, bad)

    def test_network_failure_is_a_macroforge_error(self):
        self.server.stop()
        with self.assertRaises(MacroForgeError):
            fetch.open_url(self.settings, self.server.base + "/x", 2)

    def test_installed_build_reads_build_info(self):
        self.assertEqual(fetch.installed_build(self.settings), "1.60.1.70291")
        with self.assertRaisesRegex(ValidationError, "not found in .build.info"):
            fetch.installed_build(dataclasses.replace(self.settings, product="wow_other"))
        with self.assertRaisesRegex(ValidationError, "cannot read"):
            fetch.installed_build(dataclasses.replace(self.settings, wow_dir=self.sb.tmp / "x" / "_classic_beta_"))


class StartupTests(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox()
        self.addCleanup(self.sb.cleanup)
        self.appdata = self.sb.tmp / "appdata"
        patch = mock.patch.dict(os.environ, {"APPDATA": str(self.appdata)})
        patch.start()
        self.addCleanup(patch.stop)
        self.settings = Settings.load(self.sb.project)

    def test_launcher_is_written_and_removed(self):
        folder = startup.startup_file().parent
        folder.mkdir(parents=True)
        (folder / "other.lnk").write_text("keep me")
        path = startup.install_startup(self.settings, Path(REPO / "macroforge.py"), start_now=False)
        text = path.read_text(encoding="utf-8")
        self.assertIn('CreateObject("WScript.Shell")', text)
        self.assertIn(str(self.settings.root), text)
        self.assertIn('""' + str(REPO / "macroforge.py"), text.replace("\\\\", "\\"))
        self.assertTrue(text.rstrip().endswith(", 0, False"))
        self.assertEqual(startup.remove_startup(), path)
        self.assertFalse(path.exists())
        self.assertTrue((folder / "other.lnk").exists())

    def test_quotes_and_line_breaks_cannot_break_the_script(self):
        self.assertEqual(startup.vbs_quote('say "hi"'), 'say ""hi""')
        for bad in ("a\nb", "a\rb", "a\0b"):
            with self.assertRaises(ValidationError):
                startup.vbs_quote(bad)
        with self.assertRaises(ValidationError):
            startup.launcher_text(Path('C:\\x"y'), Path("C:\\pythonw.exe"), Path("C:\\m.py"))

    def test_needs_appdata(self):
        with mock.patch.dict(os.environ, clear=True):
            with self.assertRaisesRegex(ValidationError, "APPDATA"):
                startup.startup_file()


class CliSmokeTests(unittest.TestCase):
    """Run the real entry point from an unrelated working folder."""

    def run_cli(self, *args, cwd=None):
        return subprocess.run([sys.executable, str(REPO / "macroforge.py"), *args], cwd=cwd or tempfile.gettempdir(),
                              capture_output=True, text=True, encoding="utf-8", timeout=60)

    def test_help_from_another_folder(self):
        result = self.run_cli("--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("build", result.stdout)

    def test_every_subcommand_has_help(self):
        commands = ("icon", "spell", "spells", "fetch", "build", "install", "slots", "check", "cache",
                    "spellbook", "report", "watch", "login", "startup", "rollback", "shotcheck", "setup")
        for command in commands:
            result = self.run_cli(command, "--help")
            self.assertEqual(result.returncode, 0, f"{command}: {result.stderr}")

    def test_unknown_command_and_missing_arguments(self):
        self.assertEqual(self.run_cli("nonsense").returncode, 2)
        self.assertEqual(self.run_cli("spell").returncode, 2)
        self.assertEqual(self.run_cli().returncode, 2)

    def test_module_entry_point(self):
        result = subprocess.run([sys.executable, "-m", "mforge", "--help"], cwd=REPO, capture_output=True,
                                text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_slot_count_limits_through_the_cli(self):
        sb = Sandbox()
        self.addCleanup(sb.cleanup)
        for count in ("0", "201", "-5"):
            result = sb.run("slots", "--count", count)
            self.assertEqual(result.returncode, 1, count)
            self.assertIn("between 1 and 200", result.stderr)

    def test_bad_config_is_reported_not_a_traceback(self):
        sb = Sandbox()
        self.addCleanup(sb.cleanup)
        (sb.project / "config.json").write_text('{"max_char_macros": "many"}', encoding="utf-8")
        result = sb.run("check")
        self.assertEqual(result.returncode, 1)
        self.assertIn("max_char_macros", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_bad_macros_file_is_reported_not_a_traceback(self):
        sb = Sandbox()
        self.addCleanup(sb.cleanup)
        (sb.project / "macros.json").write_text("{broken", encoding="utf-8")
        result = sb.run("build")
        self.assertEqual(result.returncode, 1)
        self.assertNotIn("Traceback", result.stderr)

    def test_first_run_creates_macros_json_from_the_example(self):
        sb = Sandbox()
        self.addCleanup(sb.cleanup)
        (sb.project / "macros.json").unlink()
        (sb.project / "macros.example.json").write_text(json.dumps({"macros": []}), encoding="utf-8")
        result = sb.run("check")
        self.assertIn("created macros.json from macros.example.json", result.stdout)
        self.assertTrue((sb.project / "macros.json").exists())


if __name__ == "__main__":
    unittest.main()

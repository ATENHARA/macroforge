"""Test helpers: a throwaway copy of the project next to a fake WoW install.

Tests drive the real command line (``python macroforge.py <command>``) as a black box, so they keep
working while the code underneath is reorganized.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"
GOLDEN = Path(__file__).resolve().parent / "golden"

ACCOUNT = "TESTACCT"
PRODUCT_VERSION = "1.60.1.70291"

DEFAULT_MACROS = {
    "classes": {"Test Char": "warlock"},
    "remove": [{"name": "Old", "char": "Test Char"}],
    "macros": [
        {
            "name": "Fear MO",
            "char": "Test Char",
            "icon": "spell:Fear",
            "body": "#showtooltip Fear\n/cast [@mouseover,harm,nodead][] Fear",
        },
        {
            "name": "Tap",
            "icon": "spell:Life Tap",
            "body": "#showtooltip Life Tap\n/cast Life Tap",
        },
    ],
}


def normalize_rev(text: str) -> str:
    """Replace the build timestamp so generated files can be compared with golden files."""
    return re.sub(r"rev = \d+", "rev = REV", text)


def check_golden(test, name: str, actual: str) -> None:
    """Compare ``actual`` with tests/golden/<name>; set UPDATE_GOLDEN=1 to (re)write the file."""
    path = GOLDEN / name
    if os.environ.get("UPDATE_GOLDEN") == "1":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(actual, encoding="utf-8", newline="\n")
    test.assertEqual(path.read_text(encoding="utf-8"), actual, f"golden file {name} differs")


class Sandbox:
    """A temporary project folder plus a fake game folder; deleted by ``cleanup()``."""

    def __init__(self, macros: dict | None = None, config: dict | None = None,
                 with_data: bool = True) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="mforge-test-"))
        self.project = self.tmp / "project"
        self.wow_root = self.tmp / "World of Warcraft"
        self.wow = self.wow_root / "_classic_beta_"
        self.addons = self.wow / "Interface" / "AddOns"
        self.account_dir = self.wow / "WTF" / "Account" / ACCOUNT
        self.saved_vars = self.account_dir / "SavedVariables"
        self._copy_code()
        self._make_game()
        cfg = {"wow_dir": str(self.wow), "account": ACCOUNT}
        cfg.update(config or {})
        (self.project / "config.json").write_text(json.dumps(cfg), encoding="utf-8")
        self.write_macros(DEFAULT_MACROS if macros is None else macros)
        if with_data:
            shutil.copytree(FIXTURES / "data", self.project / "data", dirs_exist_ok=True)

    def _copy_code(self) -> None:
        self.project.mkdir()
        shutil.copy2(REPO / "macroforge.py", self.project / "macroforge.py")
        for name in ("mforge", "addon", "reference"):
            if (REPO / name).exists():
                shutil.copytree(REPO / name, self.project / name,
                                ignore=shutil.ignore_patterns("__pycache__"))

    def _make_game(self) -> None:
        self.addons.mkdir(parents=True)
        self.saved_vars.mkdir(parents=True)
        (self.wow_root / ".build.info").write_text(
            "Branch!STRING:0|Active!DEC:1|Build Key!HEX:16|Version!STRING:0|Product!STRING:0\n"
            f"us|1|abc|{PRODUCT_VERSION}|wow_classic_beta\n", encoding="utf-8")

    def write_macros(self, spec: dict) -> None:
        (self.project / "macros.json").write_text(json.dumps(spec, indent=2), encoding="utf-8")

    def read_macros(self) -> dict:
        return json.loads((self.project / "macros.json").read_text(encoding="utf-8"))

    def run(self, *args: str, timeout: int = 60, env: dict | None = None,
            input: str | None = None) -> subprocess.CompletedProcess:
        """Run ``python macroforge.py <args>`` inside the sandbox project."""
        full_env = dict(os.environ)
        full_env["PYTHONIOENCODING"] = "utf-8"
        full_env.update(env or {})
        return subprocess.run([sys.executable, "macroforge.py", *args], cwd=self.project,
                              capture_output=True, text=True, encoding="utf-8", timeout=timeout,
                              env=full_env, input=input)

    def read(self, rel: str) -> str:
        return (self.project / rel).read_text(encoding="utf-8")

    def cleanup(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

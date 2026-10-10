"""Nothing personal or specific to one PC may be committed: names, emails, account ids, local paths.

The generic checks always run. A maintainer can add private strings to ``.git/pii-patterns.txt`` (one
per line, never committed, because it lives inside ``.git``); the same test and the pre-commit hook
then refuse any tracked file that contains them.
"""
from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SKIP_DIRS = ("datapack/", "tests/fixtures/")  # bulk game data, checked by eye when refreshed
SELF = "tests/test_privacy.py"  # its own examples are made up, so it is not scanned
TEXT_SUFFIXES = {".py", ".lua", ".toc", ".md", ".json", ".toml", ".cmd", ".txt", ".gitignore",
                 ".gitattributes", ""}

GENERIC = {
    "an email address": re.compile(r"[A-Za-z0-9._+-]+@[A-Za-z0-9-]+\.[A-Za-z0-9.-]+"),
    "a Windows user-profile path": re.compile(r"[A-Za-z]:[\\/]+Users[\\/]+(?!Test|Public|Default)\w"),
    "a Battle.net account folder name": re.compile(r"\b\d{6,}#\d+\b"),
}
ALLOWED_EMAILS = re.compile(r"@users\.noreply\.github\.com$|@example\.(com|org)$")


def tracked_files() -> list[Path]:
    """Files in the git index (so staged new files count), minus bulk data folders."""
    out = subprocess.run(["git", "ls-files"], cwd=REPO, capture_output=True, text=True,
                         encoding="utf-8", check=True).stdout.splitlines()
    return [REPO / name for name in out
            if not name.startswith(SKIP_DIRS) and name != SELF
            and Path(name).suffix.lower() in TEXT_SUFFIXES]


def private_patterns() -> list[str]:
    """Strings from ``.git/pii-patterns.txt`` (empty when the maintainer has none)."""
    try:
        git_dir = subprocess.run(["git", "rev-parse", "--git-dir"], cwd=REPO, capture_output=True,
                                 text=True, encoding="utf-8", check=True).stdout.strip()
        lines = (REPO / git_dir / "pii-patterns.txt").read_text(encoding="utf-8").splitlines()
    except (OSError, subprocess.CalledProcessError):
        return []
    return [line.strip().lower() for line in lines if line.strip() and not line.startswith("#")]


class PrivacyTests(unittest.TestCase):
    def test_no_personal_or_machine_specific_details_in_tracked_files(self):
        found: list[str] = []
        private = private_patterns()
        for path in tracked_files():
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            name = path.relative_to(REPO).as_posix()
            for what, pattern in GENERIC.items():
                for match in pattern.finditer(text):
                    if what == "an email address" and ALLOWED_EMAILS.search(match.group(0)):
                        continue
                    found.append(f"{name}: {what}")
            lowered = (text + "\n" + name).lower()
            for number, needle in enumerate(private, 1):
                if needle in lowered:
                    found.append(f"{name}: private pattern #{number}")
        self.assertEqual(found, [], "personal or machine-specific details would be committed")

    def test_the_checks_themselves_work(self):
        self.assertTrue(GENERIC["an email address"].search("write to someone@mail.example.net"))
        self.assertTrue(GENERIC["a Windows user-profile path"].search("C:\\Users\\someone\\Documents"))
        self.assertFalse(GENERIC["a Windows user-profile path"].search("C:\\Users\\Test\\x"))
        self.assertTrue(GENERIC["a Battle.net account folder name"].search("WTF/Account/1234567#1"))
        self.assertTrue(ALLOWED_EMAILS.search("1+someone@users.noreply.github.com"))


if __name__ == "__main__":
    unittest.main()

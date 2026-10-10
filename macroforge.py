#!/usr/bin/env python3
"""Entry point: ``python macroforge.py <command>`` (see ``python macroforge.py --help``)."""
import sys
from pathlib import Path

if sys.version_info < (3, 11):
    sys.exit("MacroForge needs Python 3.11 or newer.")

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mforge.cli import main  # noqa: E402  (the path must be set first)

if __name__ == "__main__":
    sys.exit(main())

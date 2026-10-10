"""Allow ``python -m mforge <command>``."""
import sys

from .cli import main

sys.exit(main())

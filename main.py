"""Application entry point.

Run with ``python main.py <command> ...`` (or the ``pokeum`` console idiom used
throughout the docs). It configures logging once, then hands off to the command
line interface in :mod:`app.cli`, which owns argument parsing and dispatch.
"""

from __future__ import annotations

import sys

from app.cli import main as cli_main

if __name__ == "__main__":
    sys.exit(cli_main(sys.argv[1:]))

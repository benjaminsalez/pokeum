"""Application entry point.

Run with ``python main.py <command> ...`` (or the ``pokeum`` console idiom used
throughout the docs). It loads a local ``.env`` (if present) into the process
environment, then hands off to the command line interface in :mod:`app.cli`,
which owns argument parsing and dispatch.
"""

from __future__ import annotations

import sys

from dotenv import load_dotenv

from app.cli import main as cli_main

if __name__ == "__main__":
    # Real environment variables win over .env values (override=False default),
    # so deployments configured via the shell/platform behave unchanged.
    load_dotenv()
    sys.exit(cli_main(sys.argv[1:]))

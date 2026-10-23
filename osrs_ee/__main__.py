"""CLI entrypoint supporting `python -m osrs_ee <subcommand>` across Windows and Linux."""

from __future__ import annotations

import sys

from osrs_ee.cli import main

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

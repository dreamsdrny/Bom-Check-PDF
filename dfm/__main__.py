"""``python -m dfm`` 入口。"""
from __future__ import annotations

import sys


def main() -> int:
    from dfm.cli import main as cli_main

    args = sys.argv[1:]
    if not args:
        from dfm.app import run_gui

        return run_gui()
    return cli_main(args)


if __name__ == "__main__":
    sys.exit(main())

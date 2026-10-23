#!/usr/bin/env python
"""Evan DFM 启动脚本。

    python evan_dfm.py            启动图形界面
    python evan_dfm.py cli ...    等价于 python -m dfm ...
    python evan_dfm.py viewer     仅打开版图交叉定位窗口 (需先有分析结果)
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main() -> int:
    args = sys.argv[1:]
    if args and args[0] == "cli":
        from dfm.cli import main as cli_main

        return cli_main(args[1:])
    if args and args[0] == "viewer":
        import json

        from dfm.app import run_viewer

        if len(args) < 2:
            print("用法: python evan_dfm.py viewer <analysis.json>")
            return 2
        with open(args[1], encoding="utf-8-sig") as f:
            return run_viewer(json.load(f))
    from dfm.app import main as app_main

    return app_main()


if __name__ == "__main__":
    sys.exit(main())

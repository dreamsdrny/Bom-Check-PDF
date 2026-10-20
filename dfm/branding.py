"""品牌信息 (单一品牌: Evan DFM)。

窗口标题、任务栏 AppUserModelID、图标路径统一由此模块提供,
打包或换名时只需改这一处。
"""
from __future__ import annotations

import os

from dfm import paths

APP_NAME = "Evan DFM"
APP_SUBTITLE = "PCB 可制造性(DFM)专业分析平台"
APP_ID = "Evan.DFM.Professional"
ORGANIZATION = "Evan"
COPYRIGHT = "Copyright © Evan"


def version() -> str:
    from dfm import __version__

    return __version__


def win_title(sub: str = "") -> str:
    """窗口标题: `Evan DFM` 或 `子窗口 - Evan DFM v1.0.0`。"""
    base = f"{APP_NAME} v{version()}"
    return f"{sub} - {base}" if sub else base


def icon_ico() -> str:
    return paths.asset_path("evan.ico")


def icon_png() -> str:
    return paths.asset_path("evan.png")


def icon_exists() -> bool:
    return os.path.isfile(icon_ico()) or os.path.isfile(icon_png())

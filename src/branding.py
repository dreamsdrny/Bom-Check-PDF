"""
DFM 品牌与命名 (单一来源, 支持多品牌)
=====================================
同一套代码可打包为不同品牌的 exe:
  - Asher_DFM (默认): Asher_DFM.ico / Asher_DFM.png
  - Evan_DFM        : evan.ico / evan.png

选择方式 (优先级从高到低):
  1) 入口脚本调用 set_brand("Evan_DFM")
  2) 环境变量 DFM_BRAND=Evan_DFM
  3) 默认 Asher_DFM

所有用户可见的程序名/窗口标题/图标/AppUserModelID 均取自此模块,
便于整体改名与打包。
"""
import os
import sys

APP_TITLE = "PCB DFM & Constraint Generator"

BRANDS = {
    "Asher_DFM": {
        "ico": "Asher_DFM.ico", "png": "Asher_DFM.png",
        "app_id": "Asher.DFM.App", "title": APP_TITLE,
    },
    "Evan_DFM": {
        "ico": "evan.ico", "png": "evan.png",
        "app_id": "Evan.DFM.App", "title": APP_TITLE,
    },
}

_DEFAULT_BRAND = os.environ.get("DFM_BRAND", "Asher_DFM")
if _DEFAULT_BRAND not in BRANDS:
    _DEFAULT_BRAND = "Asher_DFM"

APP_NAME = _DEFAULT_BRAND
APP_USER_MODEL_ID = BRANDS[_DEFAULT_BRAND]["app_id"]
ICON_ICO = BRANDS[_DEFAULT_BRAND]["ico"]
ICON_PNG = BRANDS[_DEFAULT_BRAND]["png"]


def set_brand(name: str) -> str:
    """切换品牌 (必须在导入 ui_main / 创建窗口之前调用)"""
    global APP_NAME, APP_TITLE, APP_USER_MODEL_ID, ICON_ICO, ICON_PNG
    if name not in BRANDS:
        name = "Asher_DFM"
    b = BRANDS[name]
    APP_NAME = name
    APP_TITLE = b["title"]
    APP_USER_MODEL_ID = b["app_id"]
    ICON_ICO = b["ico"]
    ICON_PNG = b["png"]
    return APP_NAME


def app_name() -> str:
    return APP_NAME


def win_title(sub: str = "") -> str:
    """窗口标题: 有子标题 -> '<APP> - <sub>'; 否则主标题"""
    return f"{APP_NAME} - {sub}" if sub else f"{APP_NAME}: {APP_TITLE}"


def resource_path(name: str) -> str:
    """兼容 PyInstaller 打包 (sys._MEIPASS) 与源码运行的资源路径"""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(base, name)


def icon_ico() -> str:
    return resource_path(ICON_ICO)


def icon_png() -> str:
    return resource_path(ICON_PNG)
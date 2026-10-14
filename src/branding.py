"""
Asher_DFM: 品牌与命名 (单一来源)
================================
所有用户可见的程序名/窗口标题/图标/AppUserModelID 统一取自此模块,
便于整体改名与打包 (Asher_DFM.exe)。
"""
import os

APP_NAME = "Asher_DFM"
APP_TITLE = "PCB DFM & Constraint Generator"
APP_USER_MODEL_ID = "Asher.DFM.App"

ICON_ICO = "Asher_DFM.ico"
ICON_PNG = "Asher_DFM.png"


def win_title(sub: str = "") -> str:
    """窗口标题: 有子标题 -> 'Asher_DFM - <sub>'; 否则主标题"""
    return f"{APP_NAME} - {sub}" if sub else f"{APP_NAME}: {APP_TITLE}"


def resource_path(name: str) -> str:
    """兼容 PyInstaller 打包 (sys._MEIPASS) 与源码运行的资源路径"""
    import sys
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(base, name)


def icon_ico() -> str:
    return resource_path(ICON_ICO)


def icon_png() -> str:
    return resource_path(ICON_PNG)

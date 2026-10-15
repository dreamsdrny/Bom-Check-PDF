"""
Evan_DFM: 程序入口 (GUI)
================================
与 Asher_DFM 共用同一套框架 (ui_main.MainLauncher), 仅品牌/图标不同。
- 开启 Windows 高 DPI 感知 (解决程序/任务栏图标模糊)
- 设置 AppUserModelID (任务栏图标正确显示与分组)
- 套用 Evan 图标 (evan.ico / evan.png)

用法:
    python evan_dfm.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 必须在导入 ui_main / 创建窗口之前设定品牌与 DPI 感知
from src.branding import set_brand, icon_ico, icon_png, win_title
from src.win_dpi import (enable_dpi_awareness, set_app_user_model_id,
                         apply_tk_scaling)

set_brand("Evan_DFM")
enable_dpi_awareness()
from src import branding as _branding
set_app_user_model_id(_branding.APP_USER_MODEL_ID)


def apply_icon(root) -> None:
    """任务栏/标题栏图标: 多尺寸 ico + png; 应用到根窗口(随即可用于 Toplevel)"""
    try:
        ico = icon_ico()
        if os.path.exists(ico):
            for fn in ("iconbitmap", "wm_iconbitmap"):
                try:
                    getattr(root, fn)(ico)
                except Exception:
                    pass
            try:
                root.iconbitmap(default=ico)
            except Exception:
                pass

        png = icon_png()
        if os.path.exists(png):
            from tkinter import PhotoImage
            try:
                img = PhotoImage(file=png)
                root.iconphoto(True, img)
                root._evan_icon = img
            except Exception:
                pass
    except Exception:
        pass


def main():
    from ui_main import MainLauncher

    app = MainLauncher()
    try:
        app.root.title(win_title())
    except Exception:
        pass
    apply_tk_scaling(app.root)
    apply_icon(app.root)
    app.run()


if __name__ == "__main__":
    main()
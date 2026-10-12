"""
Evan_DFM: 程序入口 (GUI)
=================================
DFM / 约束自动生成工具入口, 为窗口与可执行文件套用 Evan 图标 (evan.ico / evan.png)。

用法:
    python evan_dfm.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

APP_NAME = "Evan_DFM"


def resource_path(name: str) -> str:
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


def apply_icon(root) -> None:
    try:
        ico = resource_path("evan.ico")
        if os.path.exists(ico):
            try:
                root.iconbitmap(default=ico)
            except Exception:
                pass
            try:
                root.wm_iconbitmap(ico)
            except Exception:
                pass

        png = resource_path("evan.png")
        if os.path.exists(png):
            from tkinter import PhotoImage
            img = PhotoImage(file=png)
            root.iconphoto(True, img)
            root._evan_icon = img
    except Exception:
        pass


def main():
    from ui_main import MainLauncher

    app = MainLauncher()
    try:
        app.root.title(f"{APP_NAME}: PCB DFM & Constraint Generator")
    except Exception:
        pass
    apply_icon(app.root)
    app.run()


if __name__ == "__main__":
    main()
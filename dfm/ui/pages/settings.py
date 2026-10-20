"""设置页: 外观、输出、运行环境、数据与关于。"""
from __future__ import annotations

import os
import sys
from typing import Any, Dict

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QGridLayout,
                               QHBoxLayout, QLabel, QLineEdit, QMessageBox, QScrollArea,
                               QVBoxLayout, QWidget)

from dfm import paths
from dfm.ui.pages.base import BasePage
from dfm.ui.theme import colors
from dfm.ui.widgets.common import Card, PathRow, button, muted


class SettingsPage(BasePage):
    page_id = "settings"
    title = "设置"
    subtitle = "外观 · 输出 · 运行环境 · 关于"
    icon = "gear"
    group = "系统"
    order = 90

    def build(self) -> None:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        host = QWidget()
        lay = QVBoxLayout(host)
        lay.setContentsMargins(0, 0, 8, 0)
        lay.setSpacing(14)
        scroll.setWidget(host)
        self.root.addWidget(scroll, 1)

        lay.addWidget(self._appearance_card())
        lay.addWidget(self._output_card())
        lay.addWidget(self._env_card())
        lay.addWidget(self._data_card())
        lay.addWidget(self._about_card())
        lay.addStretch(1)

    # ---------------- 卡片 ----------------
    def _appearance_card(self) -> Card:
        card = Card("外观", "切换后立即生效, 并写入用户设置")
        row = QHBoxLayout()
        row.setSpacing(10)
        row.addWidget(QLabel("主题"))
        self.theme_box = QComboBox()
        self.theme_box.addItem("深色 (推荐)", "dark")
        self.theme_box.addItem("浅色", "light")
        i = self.theme_box.findData(self.state.setting("theme", "dark"))
        self.theme_box.setCurrentIndex(max(0, i))
        self.theme_box.currentIndexChanged.connect(self._on_theme)
        row.addWidget(self.theme_box)
        row.addStretch(1)
        card.add_layout(row)
        return card

    def _output_card(self) -> Card:
        card = Card("输出", "报告默认导出位置")
        self.out_row = PathRow("默认目录", "dir", "留空则使用 %APPDATA%/EvanDFM/reports")
        self.out_row.set_text(str(self.state.setting("output_dir") or ""))
        self.out_row.edit.textChanged.connect(
            lambda t: self.state.set_setting("output_dir", t.strip()))
        card.add_widget(self.out_row)
        card.add_widget(muted("每次导出可在「报告导出」页临时修改目录与文件名。"))
        return card

    def _env_card(self) -> Card:
        card = Card("运行环境", "Allegro .brd 直读需要 extracta.exe; 无 extracta 时请用 SKILL 导出 JSON")
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)
        self.extracta_row = PathRow("extracta.exe", "file", "留空自动查找 PATH 与 Cadence 安装目录",
                                    "可执行文件 (*.exe);;所有文件 (*.*)", label_width=110)
        self.extracta_row.set_text(str(self.state.config.get("extracta") or ""))
        self.extracta_row.edit.textChanged.connect(
            lambda t: self.state.set_config(extracta=t.strip()))
        grid.addWidget(self.extracta_row, 0, 0, 1, 2)
        grid.addWidget(button("自动探测", "search", "", self._detect), 0, 2)

        self.plugin_box = QCheckBox("加载插件检查项 (plugins 目录)")
        self.plugin_box.setChecked(bool(self.state.setting("load_plugins", True)))
        self.plugin_box.toggled.connect(
            lambda on: self.state.set_setting("load_plugins", on))
        grid.addWidget(self.plugin_box, 1, 0, 1, 3)

        dirs = QLabel("规则库目录:\n  " + "\n  ".join(paths.rules_dirs())
                      + "\n插件目录:\n  " + "\n  ".join(paths.plugins_dirs()))
        dirs.setStyleSheet(f"font-family:Consolas,monospace;color:{colors()['fg_mut']};"
                           f"font-size:11px;")
        dirs.setTextInteractionFlags(Qt.TextSelectableByMouse)
        grid.addWidget(dirs, 2, 0, 1, 3)
        grid.setColumnStretch(1, 1)
        card.add_layout(grid)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        bar.addWidget(button("导出 SKILL 脚本", "export", "", self._skill))
        bar.addWidget(button("打开插件目录", "folder", "", self._open_plugins))
        bar.addStretch(1)
        card.add_layout(bar)
        return card

    def _data_card(self) -> Card:
        card = Card("数据与日志")
        self.log_label = QLabel(f"日志文件: {paths.log_path()}\n"
                                f"用户设置: {paths.settings_path()}")
        self.log_label.setStyleSheet(f"font-family:Consolas,monospace;"
                                     f"color:{colors()['fg_mut']};font-size:11px;")
        self.log_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        card.add_widget(self.log_label)
        bar = QHBoxLayout()
        bar.setSpacing(8)
        bar.addWidget(button("打开用户数据目录", "folder", "", self._open_user_dir))
        bar.addWidget(button("打开日志", "file", "", self._open_log))
        bar.addWidget(button("清空最近工程", "trash", "Danger", self._clear_recent))
        bar.addStretch(1)
        card.add_layout(bar)
        return card

    def _about_card(self) -> Card:
        from dfm import __product__, __version__
        from dfm.branding import APP_SUBTITLE, COPYRIGHT

        card = Card("关于")
        title = QLabel(f"{__product__} v{__version__}")
        title.setStyleSheet("font-size:17px;font-weight:700;")
        card.add_widget(title)
        card.add_widget(muted(f"{APP_SUBTITLE}\n{COPYRIGHT}"))

        deps = []
        for name, mod in (("PySide6", "PySide6"), ("openpyxl", "openpyxl"),
                          ("PyYAML", "yaml"), ("Pillow", "PIL"), ("NumPy", "numpy")):
            try:
                m = __import__(mod)
                ver = getattr(m, "__version__", "已安装")
                deps.append(f"{name} {ver}")
            except ImportError:
                deps.append(f"{name} 未安装")
        info = QLabel(
            "运行环境\n"
            f"  Python {sys.version.split()[0]} ({os.path.dirname(sys.executable)})\n"
            f"  程序目录  {paths.app_root()}\n"
            f"  资源目录  {paths.bundle_dir()}\n"
            f"  用户目录  {paths.user_data_dir(create=False)}\n"
            f"  依赖      {', '.join(deps)}")
        info.setStyleSheet(f"font-family:Consolas,monospace;color:{colors()['fg_dim']};"
                           f"font-size:11px;")
        info.setTextInteractionFlags(Qt.TextSelectableByMouse)
        card.add_widget(info)
        bar = QHBoxLayout()
        bar.addWidget(button("检查更新说明", "book", "", self._docs))
        bar.addWidget(button("命令行帮助", "results", "", self._cli_help))
        bar.addStretch(1)
        card.add_layout(self._wrap(bar))
        return card

    def _wrap(self, layout) -> QWidget:
        host = QWidget()
        host.setLayout(layout)
        layout.setContentsMargins(0, 0, 0, 0)
        return host

    # ---------------- 动作 ----------------
    def _on_theme(self, *_args) -> None:
        theme = self.theme_box.currentData() or "dark"
        self.state.set_setting("theme", theme)
        if self.window is not None:
            self.window.apply_theme(theme)

    def _detect(self) -> None:
        from dfm.io.extracta import find_extracta

        path = find_extracta()
        if path:
            self.extracta_row.set_text(path.replace("\\", "/"))
            QMessageBox.information(self, "已找到", path)
        else:
            QMessageBox.warning(self, "未找到",
                                "未找到 extracta.exe。\n可手动指定, 或改用 SKILL 导出 JSON。")

    def _skill(self) -> None:
        from dfm.io.skill_export import EXPORT_SKILL_FILENAME, write_export_skill

        path = write_export_skill(os.path.join(self.state.output_dir(), EXPORT_SKILL_FILENAME))
        QMessageBox.information(self, "已导出", path)

    def _open_plugins(self) -> None:
        d = os.path.join(paths.user_data_dir(), "plugins")
        os.makedirs(d, exist_ok=True)
        os.startfile(d)  # noqa: S606

    def _open_user_dir(self) -> None:
        os.startfile(paths.user_data_dir())  # noqa: S606

    def _open_log(self) -> None:
        p = paths.log_path()
        if os.path.isfile(p):
            os.startfile(p)  # noqa: S606
        else:
            os.startfile(os.path.dirname(p))  # noqa: S606

    def _clear_recent(self) -> None:
        if QMessageBox.question(self, "确认", "清空最近工程记录?"):
            self.state.set_setting("recent", [])

    def _docs(self) -> None:
        docs = os.path.join(paths.app_root(), "docs")
        if os.path.isdir(docs):
            os.startfile(docs)  # noqa: S606
        else:
            QMessageBox.information(self, "文档", "文档目录未找到 (docs/)")

    def _cli_help(self) -> None:
        QMessageBox.information(
            self, "命令行",
            "python -m dfm gui                     启动界面\n"
            "python -m dfm analyze <路径> -o <输出>  分析并导出报告\n"
            "python -m dfm info <路径>               仅解析并查看统计\n"
            "python -m dfm rules list|show|template  规则库管理\n"
            "python -m dfm checks                    列出检查项(含插件)\n"
            "python -m dfm skill-export              导出 Allegro SKILL 脚本\n"
            "python -m dfm version                   版本与环境\n\n"
            "详细参数: python -m dfm analyze -h")

    def on_show(self) -> None:
        i = self.theme_box.findData(self.state.setting("theme", "dark"))
        self.theme_box.setCurrentIndex(max(0, i))
        self.out_row.set_text(str(self.state.setting("output_dir") or ""))
        self.extracta_row.set_text(str(self.state.config.get("extracta") or ""))

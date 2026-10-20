"""报告页: 选择格式与输出位置, 一键导出 (Excel / HTML / JSON / TXT / SVG)。"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout,
                               QLabel, QLineEdit, QMessageBox, QVBoxLayout, QWidget)

from dfm.report import FORMATS
from dfm.ui.pages.base import BasePage
from dfm.ui.theme import colors
from dfm.ui.widgets.common import Card, LogView, button, muted
from dfm.ui.workers import TaskWorker

FORMAT_INFO = {
    "excel": ("Excel 报告 (.xlsx)", "Valor 式多页: 封面/问题清单/Info/开短路网表/逐项明细, 可内嵌版图截图"),
    "html": ("HTML 交互报告 (.html)", "单文件, 含评分仪表/过滤器/版图交叉定位, 适合邮件与内网分发"),
    "text": ("文本报告 (.txt)", "纯文本摘要, 便于贴入邮件或 MES/PLM 系统"),
    "json": ("结构化数据 (.json)", "机器可读, 供二次开发/MES 集成与历史趋势分析"),
    "svg": ("版图矢量图 (.svg)", "整板图形 + 问题标记, 可无损放大与再编辑"),
}


class ReportPage(BasePage):
    page_id = "report"
    title = "报告导出"
    subtitle = "Excel / HTML / JSON / TXT / SVG 一键导出"
    icon = "report"
    group = "输出"
    order = 70

    def build(self) -> None:
        self.root.addWidget(self._build_summary_card())
        self.root.addWidget(self._build_format_card())
        self.root.addWidget(self._build_output_card(), 1)

    def _build_summary_card(self) -> Card:
        card = Card("待导出内容")
        self.summary_label = muted("尚未执行分析")
        card.add_widget(self.summary_label)
        return card

    def _build_format_card(self) -> Card:
        card = Card("报告格式", "可多选; Excel 与 HTML 是最常用的交付形式")
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(6)
        self.boxes: Dict[str, QCheckBox] = {}
        selected = set(self.state.setting("formats") or ["excel", "html"])
        for i, fmt in enumerate(FORMATS):
            title, desc = FORMAT_INFO[fmt]
            box = QCheckBox(title)
            box.setChecked(fmt in selected)
            box.toggled.connect(self._save_formats)
            self.boxes[fmt] = box
            grid.addWidget(box, i, 0)
            grid.addWidget(muted(desc), i, 1)
        grid.setColumnStretch(1, 1)
        card.add_layout(grid)

        opt = QHBoxLayout()
        opt.setSpacing(16)
        self.embed_box = QCheckBox("Excel 内嵌版图截图 (整板概览 + 逐问题放大图)")
        self.embed_box.setChecked(bool(self.state.setting("embed_images", True)))
        self.embed_box.toggled.connect(
            lambda on: self.state.set_setting("embed_images", on))
        opt.addWidget(self.embed_box)
        opt.addStretch(1)
        card.add_layout(opt)
        return card

    def _build_output_card(self) -> Card:
        card = Card("输出位置")
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)

        self.dir_edit = QLineEdit(self.state.output_dir())
        grid.addWidget(QLabel("输出目录"), 0, 0)
        grid.addWidget(self.dir_edit, 0, 1)
        grid.addWidget(button("浏览...", "folder", "", self._pick_dir), 0, 2)

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("留空则使用板名")
        grid.addWidget(QLabel("文件名"), 1, 0)
        grid.addWidget(self.name_edit, 1, 1)
        grid.addWidget(button("另存为...", "save", "", self._pick_file), 1, 2)

        self.customer_edit = QLineEdit()
        self.customer_edit.setText(str(self.state.config.get("customer") or ""))
        self.customer_edit.textChanged.connect(
            lambda t: self.state.set_config(customer=t))
        grid.addWidget(QLabel("客户"), 2, 0)
        grid.addWidget(self.customer_edit, 2, 1)

        self.submitter_edit = QLineEdit()
        self.submitter_edit.setText(str(self.state.config.get("submitter") or ""))
        self.submitter_edit.textChanged.connect(
            lambda t: self.state.set_config(submitter=t))
        grid.addWidget(QLabel("提交人"), 2, 2, Qt.AlignLeft)
        grid.setColumnStretch(1, 1)
        card.add_layout(grid)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.export_btn = button("导出报告", "export", "Primary", self._export)
        self.export_btn.setMinimumHeight(36)
        bar.addWidget(self.export_btn)
        bar.addWidget(button("打开输出目录", "folder", "", self._open_dir))
        bar.addWidget(button("生成规则集模板", "sliders", "", self._template))
        bar.addStretch(1)
        card.add_layout(bar)

        self.log = LogView()
        self.log.setMaximumHeight(150)
        card.add_widget(self.log)
        return card

    # ---------------- 动作 ----------------
    def _pick_dir(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "选择输出目录", self.dir_edit.text())
        if d:
            self.dir_edit.setText(d.replace("\\", "/"))
            self.state.set_setting("output_dir", self.dir_edit.text())

    def _pick_file(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "另存为", os.path.join(
            self.dir_edit.text(), self.name_edit.text() or "dfm_report") + ".xlsx",
            "Excel (*.xlsx);;HTML (*.html);;所有文件 (*.*)")
        if path:
            self.dir_edit.setText(os.path.dirname(path).replace("\\", "/"))
            self.name_edit.setText(os.path.splitext(os.path.basename(path))[0])

    def _open_dir(self) -> None:
        d = self.dir_edit.text().strip() or self.state.output_dir()
        os.makedirs(d, exist_ok=True)
        os.startfile(d)  # noqa: S606

    def _template(self) -> None:
        from dfm.core.rules_config import default_config_path, template_text

        path = default_config_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(template_text())
        QMessageBox.information(self, "已生成", f"规则集模板:\n{path}\n\n可在「规则库」页编辑。")

    def _save_formats(self, *_args) -> None:
        self.state.set_setting("formats", self.selected_formats())

    def selected_formats(self) -> List[str]:
        return [f for f in FORMATS if self.boxes[f].isChecked()]

    def _export(self) -> None:
        analysis = self.state.analysis
        if not analysis:
            QMessageBox.warning(self, "提示", "请先执行分析")
            return
        formats = self.selected_formats()
        if not formats:
            QMessageBox.warning(self, "提示", "请至少勾选一种报告格式")
            return
        out_dir = self.dir_edit.text().strip() or self.state.output_dir()
        name = self.name_edit.text().strip() or str(
            (analysis.get("board_meta") or {}).get("board_name") or "dfm_report")
        name = "".join(c for c in name if c not in '\\/:*?"<>|').strip() or "dfm_report"
        target = os.path.join(out_dir, name)
        os.makedirs(out_dir, exist_ok=True)
        self.log.clear_log()
        self.log.write(f"导出中: {', '.join(formats)} -> {target}.*", "info")
        self.export_btn.setEnabled(False)
        self.set_status("正在导出报告 ...")

        def job():
            from dfm.report import export_report

            return export_report(analysis, target, formats=formats,
                                 embed_images=self.embed_box.isChecked())

        self._worker = TaskWorker(job)
        self._worker.finished_.connect(self._done)
        self._worker.failed.connect(self._failed)
        self._worker.start()

    def _done(self, result: Dict[str, str]) -> None:
        self.export_btn.setEnabled(True)
        for fmt, p in result.items():
            self.log.write(f"  [{fmt}] {p}", "ok")
        self.set_status(f"报告已导出: {len(result)} 个文件")
        QMessageBox.information(self, "导出完成",
                                "\n".join(f"{k}: {v}" for k, v in result.items()))

    def _failed(self, msg: str) -> None:
        self.export_btn.setEnabled(True)
        self.log.write(msg, "err")
        self.set_status("导出失败")
        QMessageBox.critical(self, "导出失败", msg.split("\n")[0])

    # ---------------- 刷新 ----------------
    def on_analysis(self, analysis: Dict[str, Any]) -> None:
        summary = analysis.get("issue_summary") or {}
        risk = analysis.get("risk") or {}
        meta = analysis.get("board_meta") or {}
        self.summary_label.setText(
            f"板名: {meta.get('board_name', '—')}   版本: {meta.get('version') or '—'}\n"
            f"规则集: {analysis.get('factory_label', '')} / "
            f"{analysis.get('board_type_label', '')} / {analysis.get('capability_label', '')}\n"
            f"问题: {summary.get('总计', 0)} 项 (危险 {summary.get('危险', 0)} / "
            f"高 {summary.get('高', 0)} / 中 {summary.get('中', 0)} / 低 {summary.get('低', 0)})"
            f"   评分: {risk.get('score')}/100 ({risk.get('risk_level')})\n"
            f"几何数据: {'有 (可内嵌截图/矢量图)' if analysis.get('fab_data') else '无 (仅表格与统计)'}"
            f"   定位点: {len(analysis.get('instances') or [])}")
        if not self.name_edit.text().strip():
            self.name_edit.setPlaceholderText(meta.get("board_name") or "dfm_report")

    def on_show(self) -> None:
        if self.state.analysis:
            self.on_analysis(self.state.analysis)
        else:
            self.summary_label.setText("尚未执行分析")
        self.dir_edit.setText(self.state.output_dir())

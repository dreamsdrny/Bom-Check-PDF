"""分析页: 勾选检查项、执行分析、查看进度与日志。"""
from __future__ import annotations

from typing import Any, Dict, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QFrame, QHBoxLayout, QLabel,
                               QProgressBar, QScrollArea, QSplitter,
                               QVBoxLayout, QWidget)

from dfm.core import registry
from dfm.ui.pages.base import BasePage
from dfm.ui.theme import colors
from dfm.ui.widgets.common import (Card, LogView, StatusBadge, button)
from dfm.ui.workers import AnalysisWorker


class CheckRow(QFrame):
    """单个检查项的勾选行 (名称 + 说明 + 依赖 + 来源)。"""

    def __init__(self, spec: dict, checked: bool = True, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("CardFlat")
        self.check_id = spec["id"]
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(10)
        self.box = QCheckBox()
        self.box.setChecked(checked)
        self.box.setText("")
        lay.addWidget(self.box)
        box = QVBoxLayout()
        box.setSpacing(2)
        head = QHBoxLayout()
        head.setSpacing(8)
        name = QLabel(spec.get("name") or spec["id"])
        name.setStyleSheet("font-weight:600;")
        head.addWidget(name)
        tag = QLabel(spec["id"])
        tag.setStyleSheet(f"color:{colors()['fg_mut']};font-family:Consolas,monospace;"
                          f"font-size:11px;")
        head.addWidget(tag)
        grp = QLabel(spec.get("group") or "")
        grp.setObjectName("BadgeIdle")
        head.addWidget(grp)
        if spec.get("source") != "builtin":
            plug = QLabel("插件")
            plug.setObjectName("BadgeWarn")
            head.addWidget(plug)
        head.addStretch(1)
        box.addLayout(head)
        desc = QLabel(spec.get("description") or "")
        desc.setObjectName("Muted")
        desc.setWordWrap(True)
        box.addWidget(desc)
        req = spec.get("requires") or []
        if req:
            need = QLabel("需要数据: " + ", ".join(req))
            need.setStyleSheet(f"color:{colors()['fg_mut']};font-size:11px;")
            box.addWidget(need)
        lay.addLayout(box, 1)
        self.toggled = self.box.toggled

    def is_checked(self) -> bool:
        return self.box.isChecked()

    def set_checked(self, on: bool) -> None:
        self.box.setChecked(on)


class AnalysisPage(BasePage):
    page_id = "analysis"
    title = "检查与执行"
    subtitle = "勾选检查项 → 执行分析 → 实时进度与日志"
    icon = "run"
    group = "分析"
    order = 30

    def build(self) -> None:
        splitter = QSplitter(Qt.Vertical)
        splitter.setHandleWidth(6)
        self.root.addWidget(splitter, 1)

        # ---- 上: 检查项 ----
        top = QWidget()
        top_lay = QVBoxLayout(top)
        top_lay.setContentsMargins(0, 0, 0, 0)
        top_lay.setSpacing(10)
        card = Card("② 检查项", "内置检查 + 插件检查; 数据不满足时会自动跳过")
        bar = QHBoxLayout()
        bar.setSpacing(8)
        bar.addWidget(button("全选", "check", "", lambda: self._set_all(True)))
        bar.addWidget(button("全不选", "close", "", lambda: self._set_all(False)))
        bar.addWidget(button("仅制造 DFM", "chip", "", lambda: self._select_group("制造 DFM")))
        bar.addWidget(button("刷新插件", "refresh", "", self.reload_checks))
        bar.addStretch(1)
        self.count_label = QLabel("")
        self.count_label.setObjectName("Muted")
        bar.addWidget(self.count_label)
        card.add_header_widget(self._bar_widget(bar))

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        host = QWidget()
        self.checks_lay = QVBoxLayout(host)
        self.checks_lay.setContentsMargins(0, 0, 8, 0)
        self.checks_lay.setSpacing(6)
        self.checks_lay.addStretch(1)
        scroll.setWidget(host)
        card.add_widget(scroll, 1)
        top_lay.addWidget(card, 1)
        splitter.addWidget(top)

        # ---- 下: 执行 + 日志 ----
        bottom = QWidget()
        bot_lay = QVBoxLayout(bottom)
        bot_lay.setContentsMargins(0, 0, 0, 0)
        bot_lay.setSpacing(10)

        run_card = Card("③ 执行")
        run_bar = QHBoxLayout()
        run_bar.setSpacing(10)
        self.run_btn = button("执行分析", "run", "Primary", self._run)
        self.run_btn.setMinimumHeight(38)
        run_bar.addWidget(self.run_btn)
        self.badge = StatusBadge()
        run_bar.addWidget(self.badge)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setVisible(False)
        self.progress.setFixedWidth(200)
        run_bar.addWidget(self.progress)
        run_bar.addStretch(1)
        run_bar.addWidget(button("清空日志", "trash", "Ghost", self._clear_log))
        run_bar.addWidget(button("查看结果", "results", "", lambda: self.navigate("results")))
        run_card.add_layout(run_bar)

        self.summary_label = QLabel("尚未执行分析")
        self.summary_label.setObjectName("Muted")
        self.summary_label.setWordWrap(True)
        run_card.add_widget(self.summary_label)
        bot_lay.addWidget(run_card)

        log_card = Card("执行日志")
        self.log = LogView()
        log_card.add_widget(self.log, 1)
        bot_lay.addWidget(log_card, 1)
        splitter.addWidget(bottom)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)

        self.reload_checks()

    def _bar_widget(self, layout) -> QWidget:
        host = QWidget()
        host.setLayout(layout)
        layout.setContentsMargins(0, 0, 0, 0)
        return host

    # ---------------- 检查项 ----------------
    def reload_checks(self) -> None:
        registry.load_builtin_checks()
        try:
            registry.load_plugins()
        except Exception as exc:
            self.log.write(f"[WARN] 插件加载失败: {exc}", "warn")
        while self.checks_lay.count() > 1:
            item = self.checks_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.rows: Dict[str, CheckRow] = {}
        selected = set(self.state.config.get("modules") or [])
        specs = [c.to_dict() for c in registry.list_checks()]
        for i, spec in enumerate(specs):
            row = CheckRow(spec, checked=(not selected) or (spec["id"] in selected))
            row.toggled.connect(self._on_toggle)
            self.rows[spec["id"]] = row
            self.checks_lay.insertWidget(i, row)
        self.count_label.setText(f"共 {len(specs)} 项检查 (内置 "
                                 f"{len(registry.builtin_checks())} / 插件 "
                                 f"{len(registry.plugin_checks())})")
        self._on_toggle()

    def _on_toggle(self, *_args) -> None:
        mods = [cid for cid, row in self.rows.items() if row.is_checked()]
        all_ids = set(self.rows)
        self.state.set_config(modules=None if set(mods) == all_ids else mods)
        self.count_label.setText(f"已勾选 {len(mods)} / {len(all_ids)} 项")

    def _set_all(self, on: bool) -> None:
        for row in self.rows.values():
            row.set_checked(on)

    def _select_group(self, group: str) -> None:
        for spec in registry.list_checks():
            row = self.rows.get(spec.id)
            if row is not None:
                row.set_checked(spec.group == group)

    # ---------------- 执行 ----------------
    def _run(self) -> None:
        self.run_analysis()

    def start_analysis(self) -> None:
        """由主窗口调用: 真正启动后台分析。"""
        cfg = self.state.analysis_config()
        mods = cfg.get("modules")
        self.log.clear_log()
        self.badge.set_state("running")
        self.progress.setVisible(True)
        self.run_btn.setEnabled(False)
        self.summary_label.setText("分析进行中 ...")
        self.log.write(f"数据源: {cfg.get('source_type') or '自动'} "
                       f"{cfg.get('source_path') or '(无)'}", "info")
        self.log.write(f"规则集: {cfg.get('factory')} / {cfg.get('board_type')} / "
                       f"{cfg.get('capability_level')} / 铜厚 {cfg.get('copper_um')}um", "info")
        self.log.write(f"检查项: {', '.join(mods) if mods else '全部'}", "info")
        self._worker = AnalysisWorker(cfg)
        self._worker.progress.connect(self._on_progress)
        self._worker.step.connect(self._on_step)
        self._worker.finished_.connect(self._on_done)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _on_step(self, done: int, total: int, name: str) -> None:
        """确定性进度: 检查项逐个完成时推进进度条。"""
        if total <= 0:
            return
        self.progress.setRange(0, total)
        self.progress.setValue(done)
        self.progress.setFormat(f"%v/%m  {name}")

    def _on_progress(self, msg: str) -> None:
        level = "err" if "[ERROR]" in msg else ("warn" if "[WARN]" in msg or "[提示]" in msg
                                                else "")
        self.log.write(msg, level)
        self.set_status(msg.strip())

    def _on_done(self, analysis: Dict[str, Any]) -> None:
        self.badge.set_state("done")
        self.progress.setRange(0, 1)
        self.progress.setValue(1)
        self.progress.setFormat("完成")
        self.progress.setVisible(False)
        self.run_btn.setEnabled(True)
        s = analysis.get("issue_summary") or {}
        risk = analysis.get("risk") or {}
        text = (f"完成: 问题 {s.get('总计', 0)} 项 (危险 {s.get('危险', 0)} / "
                f"高 {s.get('高', 0)} / 中 {s.get('中', 0)} / 低 {s.get('低', 0)})   "
                f"评分 {risk.get('score')}/100 ({risk.get('risk_level')})   "
                f"检查: {', '.join(analysis.get('checks_run') or [])}")
        self.summary_label.setText(text + "\n" + str(risk.get("verdict", "")))
        skipped = analysis.get("checks_skipped") or []
        if skipped:
            self.log.write("跳过的检查: " + "; ".join(
                f"{x['id']}({x['reason']})" for x in skipped), "warn")
        self.log.write(text, "ok")
        self.log.write(f"结论: {risk.get('verdict', '')}", "ok")
        self.set_status(text)
        self.state.set_analysis(analysis)

    def _on_failed(self, msg: str) -> None:
        self.badge.set_state("error")
        self.progress.setVisible(False)
        self.run_btn.setEnabled(True)
        self.log.write(msg, "err")
        self.summary_label.setText("分析失败, 详见日志")
        self.set_status("分析失败")

    def _clear_log(self) -> None:
        self.log.clear_log()

    def on_show(self) -> None:
        if not getattr(self, "rows", None):
            self.reload_checks()

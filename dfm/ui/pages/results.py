"""结果页: 问题清单 + 明细 + 分级统计, 与版图页双向联动。"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QListWidget,
                               QListWidgetItem, QScrollArea, QSplitter, QVBoxLayout,
                               QWidget)

from dfm.ui.pages.base import BasePage
from dfm.ui.theme import SEVERITY_COLORS, colors
from dfm.ui.widgets.common import (Card, EmptyHint, ScoreGauge, SeverityBadge, StatCard,
                                   button, muted, stat_row)
from dfm.ui.widgets.issue_table import IssueTable


class ResultsPage(BasePage):
    page_id = "results"
    title = "问题清单"
    subtitle = "分级问题清单、明细与定位"
    icon = "results"
    group = "分析"
    order = 40

    def build(self) -> None:
        self.stats_host = QWidget()
        self.stats_lay = QHBoxLayout(self.stats_host)
        self.stats_lay.setContentsMargins(0, 0, 0, 0)
        self.stats_lay.setSpacing(12)
        self.root.addWidget(self.stats_host)

        self.score_host = QHBoxLayout()
        self.score_host.setSpacing(14)
        self.gauge = ScoreGauge(size=150)
        self.score_host.addWidget(self.gauge)
        self.verdict_box = QVBoxLayout()
        self.verdict_box.setSpacing(4)
        self.verdict_title = QLabel("尚未分析")
        self.verdict_title.setStyleSheet("font-size:15px;font-weight:600;")
        self.verdict_text = muted("")
        self.verdict_box.addWidget(self.verdict_title)
        self.verdict_box.addWidget(self.verdict_text)
        self.verdict_box.addStretch(1)
        score_card = Card("可制造性评分")
        score_inner = QHBoxLayout()
        score_inner.setSpacing(12)
        score_inner.addWidget(self.gauge)
        score_inner.addLayout(self.verdict_box, 1)
        score_card.add_layout(score_inner)
        score_card.setMaximumHeight(190)
        self.root.addWidget(score_card)

        splitter = QSplitter(Qt.Vertical)
        splitter.setHandleWidth(6)
        self.root.addWidget(splitter, 1)

        self.table = IssueTable()
        self.table.issue_selected.connect(self._on_selected)
        self.table.issue_activated.connect(self._on_activated)
        splitter.addWidget(self.table)

        detail_card = Card("问题明细")
        self.detail_host = QWidget()
        self.detail_lay = QVBoxLayout(self.detail_host)
        self.detail_lay.setContentsMargins(0, 0, 0, 0)
        self.detail_lay.setSpacing(8)
        detail_card.add_widget(self.detail_host, 1)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.prev_btn = button("上一处", "chevron-right", "", self._prev)
        self.next_btn = button("下一处", "chevron-right", "", self._next)
        self.locate_btn = button("在版图中定位", "target", "Primary", self._locate)
        bar.addWidget(self.prev_btn)
        bar.addWidget(self.next_btn)
        bar.addWidget(self.locate_btn)
        bar.addStretch(1)
        bar.addWidget(button("导出报告", "export", "", lambda: self.navigate("report")))
        detail_card.add_layout(bar)

        self.locations = QListWidget()
        self.locations.setMaximumHeight(96)
        self.locations.itemDoubleClicked.connect(self._on_location_double)
        detail_card.add_widget(self.locations)

        wrap = QWidget()
        wrap_lay = QVBoxLayout(wrap)
        wrap_lay.setContentsMargins(0, 0, 0, 0)
        wrap_lay.addWidget(detail_card)
        splitter.addWidget(wrap)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)

        self._current = -1
        self._show_empty()

    # ---------------- 显示 ----------------
    def _show_empty(self) -> None:
        self._clear(self.detail_lay)
        self.detail_lay.addWidget(EmptyHint(
            "尚无分析结果\n请在「数据源」页选择输入, 再到「检查与执行」页运行分析",
            "results", "去数据源页", lambda: self.navigate("project")))
        self.locations.clear()
        self.gauge.set_score(None)
        self.verdict_title.setText("尚未分析")
        self.verdict_text.setText("")
        self.table.clear()
        self._clear(self.stats_lay)

    def _clear(self, layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
            elif item.layout() is not None:
                self._clear(item.layout())

    def on_analysis(self, analysis: Dict[str, Any]) -> None:
        summary = analysis.get("issue_summary") or {}
        risk = analysis.get("risk") or {}
        meta = analysis.get("board_meta") or {}

        self._clear(self.stats_lay)
        cards = [StatCard("问题总计", summary.get("总计", 0), colors()["accent"]),
                 StatCard("危险", summary.get("危险", 0), SEVERITY_COLORS["危险"]),
                 StatCard("高", summary.get("高", 0), SEVERITY_COLORS["高"]),
                 StatCard("中", summary.get("中", 0), SEVERITY_COLORS["中"]),
                 StatCard("低", summary.get("低", 0), SEVERITY_COLORS["低"]),
                 StatCard("定位点", len(analysis.get("instances") or []), colors()["info"]),
                 StatCard("已执行检查", len(analysis.get("checks_run") or []),
                          colors()["fg_dim"])]
        self.stats_lay.addWidget(stat_row(cards))

        self.gauge.set_score(risk.get("score"), risk.get("risk_level", ""))
        self.verdict_title.setText(
            f"{meta.get('board_name', '')}  ·  {risk.get('risk_level', '')}风险")
        ye = risk.get("yield_estimate") or ["—", "—"]
        self.verdict_text.setText(
            f"{risk.get('verdict', '')}\n"
            f"{analysis.get('factory_label', '')} / {analysis.get('board_type_label', '')} / "
            f"{analysis.get('capability_label', '')} · 铜厚 {analysis.get('copper_um')}um\n"
            f"预估良率 {ye[0]}% ~ {ye[1]}% · 扣分 {risk.get('penalty', 0)}")

        self.table.set_issues(analysis.get("issues") or [])
        self._current = -1
        self._clear(self.detail_lay)
        self.detail_lay.addWidget(muted("在上方表格中单击一行查看明细, 双击直接定位到版图。"))
        self.locations.clear()
        self.set_status(f"问题 {summary.get('总计', 0)} 项 · 评分 {risk.get('score')}/100")

    def on_show(self) -> None:
        if self.state.analysis:
            self.on_analysis(self.state.analysis)
        elif not self.table.model.rowCount():
            self._show_empty()

    # ---------------- 明细 ----------------
    def _on_selected(self, index: int) -> None:
        self._current = index
        issues = self.state.issues
        if not (0 <= index < len(issues)):
            return
        it = issues[index]
        self._clear(self.detail_lay)

        head = QHBoxLayout()
        head.setSpacing(8)
        head.addWidget(SeverityBadge(it.get("severity", "")))
        code = QLabel(str(it.get("code", "")))
        code.setStyleSheet(f"font-family:Consolas,monospace;color:{colors()['fg_dim']};")
        head.addWidget(code)
        rule = QLabel(str(it.get("rule", "")))
        rule.setStyleSheet("font-size:14px;font-weight:600;")
        head.addWidget(rule)
        cat = QLabel(f"{it.get('category', '')} · {it.get('check_name') or it.get('module', '')}")
        cat.setObjectName("Muted")
        head.addWidget(cat)
        head.addStretch(1)
        if it.get("x") is not None:
            pos = QLabel(f"X {it['x']:.3f}   Y {it['y']:.3f} mm")
            pos.setStyleSheet(f"font-family:Consolas,monospace;color:{colors()['info']};")
            head.addWidget(pos)
        self.detail_lay.addLayout(head)

        desc = QLabel(str(it.get("description", "")))
        desc.setWordWrap(True)
        desc.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.detail_lay.addWidget(desc)

        grid = QHBoxLayout()
        grid.setSpacing(18)
        for label, value in (("实测值", f"{it.get('measured')} {it.get('unit', '')}".strip()),
                             ("要求值", f"{it.get('required')} {it.get('unit', '')}".strip()),
                             ("数量", str(it.get("count", 1))),
                             ("层", str(it.get("layer") or "—")),
                             ("网络", str(it.get("net") or "—")),
                             ("涉及", f"{it.get('from') or '—'} → {it.get('to') or '—'}")):
            box = QVBoxLayout()
            box.setSpacing(1)
            k = QLabel(label)
            k.setObjectName("Muted")
            v = QLabel(value if value not in ("None", "") else "—")
            v.setStyleSheet("font-weight:600;")
            v.setTextInteractionFlags(Qt.TextSelectableByMouse)
            box.addWidget(k)
            box.addWidget(v)
            cell = QWidget()
            cell.setLayout(box)
            grid.addWidget(cell)
        grid.addStretch(1)
        self.detail_lay.addLayout(grid)

        if it.get("suggestion"):
            sug = QLabel(f"专家建议: {it['suggestion']}")
            sug.setWordWrap(True)
            sug.setStyleSheet(f"color:{colors()['ok']};")
            sug.setTextInteractionFlags(Qt.TextSelectableByMouse)
            self.detail_lay.addWidget(sug)
        if it.get("note"):
            self.detail_lay.addWidget(muted(f"备注: {it['note']}"))

        self.locations.clear()
        locs = it.get("locations") or []
        if locs:
            for i, (x, y) in enumerate(locs[:500], 1):
                QListWidgetItem(f"#{i}   X {x:.3f}   Y {y:.3f} mm", self.locations)
            if len(locs) > 500:
                QListWidgetItem(f"... 其余 {len(locs) - 500} 处未列出", self.locations)
        self.locate_btn.setEnabled(it.get("x") is not None)

    def _on_activated(self, index: int) -> None:
        self._current = index
        self._locate()

    def _on_location_double(self, item: QListWidgetItem) -> None:
        self.navigate("board")

    def _locate(self) -> None:
        if self._current >= 0:
            self.state.select_issue(self._current)
        self.navigate("board")

    def _step(self, delta: int) -> None:
        issues = self.state.issues
        if not issues:
            return
        nxt = (self._current + delta) % len(issues)
        self.table.select_issue(nxt)
        self._current = nxt
        self.state.select_issue(nxt)

    def _prev(self) -> None:
        self._step(-1)

    def _next(self) -> None:
        self._step(1)

    def select_issue(self, index: int) -> None:
        self.table.select_issue(index)

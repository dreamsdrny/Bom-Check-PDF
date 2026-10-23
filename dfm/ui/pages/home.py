"""概览页: 快速开始 / 最近工程 / 当前结果评分与重点问题。"""
from __future__ import annotations

from typing import Any, Dict, List

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton,
                               QScrollArea, QVBoxLayout, QWidget)

from dfm.ui.icons import icon, logo_pixmap
from dfm.ui.pages.base import BasePage
from dfm.ui.theme import SEVERITY_COLORS, colors
from dfm.ui.widgets.common import (Card, EmptyHint, ScoreGauge, SeverityBadge, StatCard,
                                   button, muted, stat_row)

STEPS = [
    ("project", "① 选择数据源", "支持 Allegro .brd、SKILL/extracta 导出 JSON、Gerber+钻孔、ODB++ 工程"),
    ("analysis", "② 配置规则与检查", "选择板厂能力库/等级/铜厚, 勾选需要的检查项 (含插件)"),
    ("analysis", "③ 执行分析", "后台线程执行, 实时日志与进度; 问题自动分级并定位 XY"),
    ("results", "④ 复核问题清单", "按严重度/检查/关键字过滤, 双击定位到版图"),
    ("report", "⑤ 导出报告", "Excel(Valor 式, 内嵌截图) / HTML(交互) / JSON / TXT / SVG"),
]


class HomePage(BasePage):
    page_id = "home"
    title = "概览"
    subtitle = "工程状态、评分与快速开始"
    icon = "home"
    group = "工作台"
    order = 10

    def build(self) -> None:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        host = QWidget()
        self.lay = QVBoxLayout(host)
        self.lay.setContentsMargins(0, 0, 8, 0)
        self.lay.setSpacing(14)
        scroll.setWidget(host)
        self.root.addWidget(scroll, 1)

        self.hero = self._build_hero()
        self.lay.addWidget(self.hero)

        self.result_card = Card("当前分析结果")
        self.result_host = QVBoxLayout()
        self.result_card.add_layout(self.result_host)
        self.lay.addWidget(self.result_card)

        row = QHBoxLayout()
        row.setSpacing(14)
        row.setAlignment(Qt.AlignTop)
        self.lay.addLayout(row, 1)

        self.steps_card = Card("标准作业流程", "点击步骤直达对应页面")
        steps_host = QWidget()
        grid = QGridLayout(steps_host)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(8)
        for i, (page, title, desc) in enumerate(STEPS):
            btn = QPushButton(title)
            btn.setIcon(icon("chevron-right", colors()["accent"], 15))
            btn.setObjectName("Ghost")
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda _=False, p=page: self.navigate(p))
            grid.addWidget(btn, i, 0)
            grid.addWidget(muted(desc), i, 1)
        grid.setColumnStretch(1, 1)
        self.steps_card.add_widget(steps_host)
        row.addWidget(self.steps_card, 3)

        self.recent_card = Card("最近工程")
        self.recent_host = QVBoxLayout()
        self.recent_host.setSpacing(6)
        self.recent_card.add_layout(self.recent_host)
        row.addWidget(self.recent_card, 2)

        self._build_empty_result()
        self.refresh_recent()

    # ---------- 构建 ----------
    def _build_hero(self) -> QFrame:
        hero = QFrame()
        hero.setObjectName("Card")
        lay = QHBoxLayout(hero)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(16)
        logo = QLabel()
        logo.setPixmap(logo_pixmap(52))
        logo.setFixedSize(52, 52)
        lay.addWidget(logo)
        box = QVBoxLayout()
        box.setSpacing(3)
        from dfm.branding import APP_NAME, APP_SUBTITLE, version

        t = QLabel(APP_NAME)
        t.setStyleSheet("font-size:20px;font-weight:700;")
        s = QLabel(f"{APP_SUBTITLE} · v{version()}")
        s.setObjectName("Muted")
        box.addWidget(t)
        box.addWidget(s)
        lay.addLayout(box, 1)

        self.badges = QHBoxLayout()
        self.badges.setSpacing(8)
        lay.addLayout(self.badges)
        return hero

    def _build_empty_result(self) -> None:
        self._clear(self.result_host)
        hint = EmptyHint(
            "尚未执行分析\n选择数据源后点右上角「执行分析」，或用命令行 python -m dfm analyze <路径>",
            "chip", "打开数据源", lambda: self.navigate("project"))
        self.result_host.addWidget(hint)
        self.result_card.set_title("当前分析结果")

    def _clear(self, layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
            elif item.layout() is not None:
                self._clear(item.layout())

    # ---------- 刷新 ----------
    def on_show(self) -> None:
        self.refresh_recent()
        if self.state.analysis:
            self.on_analysis(self.state.analysis)
        else:
            self._build_empty_result()
        self._refresh_hero()

    def _refresh_hero(self) -> None:
        while self.badges.count():
            item = self.badges.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        cfg = self.state.config
        from dfm.core.capability import factory_label

        info = [("板厂", factory_label(cfg.get("factory") or "jlcpcb")),
                ("板型", "硬板" if cfg.get("board_type") == "rigid" else "软板"),
                ("等级", str(cfg.get("capability_level") or "standard")),
                ("铜厚", f"{cfg.get('copper_um') or 35}um")]
        for k, v in info:
            chip = QLabel(f"{k} · {v}")
            chip.setObjectName("BadgeIdle")
            self.badges.addWidget(chip)

    def on_config_changed(self) -> None:
        self._refresh_hero()

    def on_analysis(self, analysis: Dict[str, Any]) -> None:
        self._clear(self.result_host)
        risk = analysis.get("risk") or {}
        summary = analysis.get("issue_summary") or {}
        meta = analysis.get("board_meta") or {}
        self.result_card.set_title(
            f"当前分析结果 — {meta.get('board_name', '')}",
            f"{analysis.get('factory_label', '')} / {analysis.get('board_type_label', '')}"
            f" / {analysis.get('capability_label', '')} · "
            f"{meta.get('finish_time', '')}")

        top = QHBoxLayout()
        top.setSpacing(18)
        gauge_host = QVBoxLayout()
        gauge_host.setAlignment(Qt.AlignCenter)
        gauge = ScoreGauge(size=210)
        gauge.set_score(risk.get("score"), risk.get("risk_level", ""),
                        risk.get("verdict", ""))
        gauge_host.addWidget(gauge)
        verdict = QLabel(risk.get("verdict", ""))
        verdict.setAlignment(Qt.AlignCenter)
        verdict.setObjectName("Muted")
        verdict.setWordWrap(True)
        gauge_host.addWidget(verdict)
        ye = risk.get("yield_estimate") or ["—", "—"]
        ylab = QLabel(f"预估良率 {ye[0]}% ~ {ye[1]}%")
        ylab.setAlignment(Qt.AlignCenter)
        ylab.setObjectName("Muted")
        gauge_host.addWidget(ylab)
        wrap = QWidget()
        wrap.setLayout(gauge_host)
        wrap.setFixedWidth(250)
        top.addWidget(wrap)

        cards = [StatCard("问题总计", summary.get("总计", 0), colors()["accent"]),
                 StatCard("危险", summary.get("危险", 0), SEVERITY_COLORS["危险"]),
                 StatCard("高", summary.get("高", 0), SEVERITY_COLORS["高"]),
                 StatCard("中", summary.get("中", 0), SEVERITY_COLORS["中"]),
                 StatCard("低", summary.get("低", 0), SEVERITY_COLORS["低"]),
                 StatCard("定位点", len(analysis.get("instances") or []),
                          colors()["info"])]
        right = QVBoxLayout()
        right.setSpacing(10)
        right.addWidget(stat_row(cards[:3]))
        right.addWidget(stat_row(cards[3:]))

        actions = QHBoxLayout()
        actions.setSpacing(8)
        actions.addWidget(button("查看问题清单", "results", "Primary",
                                 lambda: self.navigate("results")))
        actions.addWidget(button("版图定位", "layers", "",
                                 lambda: self.navigate("board")))
        actions.addWidget(button("导出报告", "export", "",
                                 lambda: self.navigate("report")))
        actions.addStretch(1)
        right.addLayout(actions)
        top.addLayout(right, 1)
        self.result_host.addLayout(top)

        top_issues: List[dict] = (analysis.get("issues") or [])[:8]
        if top_issues:
            self.result_host.addWidget(muted("重点问题 (前 8 项)"))
            for it in top_issues:
                self.result_host.addWidget(self._issue_row(it))

    def _issue_row(self, it: dict) -> QWidget:
        row = QFrame()
        row.setObjectName("CardFlat")
        lay = QHBoxLayout(row)
        lay.setContentsMargins(10, 7, 10, 7)
        lay.setSpacing(9)
        badge = SeverityBadge(it.get("severity", ""))
        lay.addWidget(badge)
        code = QLabel(str(it.get("code", "")))
        code.setStyleSheet(f"font-family:Consolas,monospace;color:{colors()['fg_mut']};")
        lay.addWidget(code)
        desc = QLabel(str(it.get("description", "")))
        desc.setWordWrap(False)
        desc.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(desc, 1)
        cnt = QLabel(f"x{it.get('count', 1)}")
        cnt.setObjectName("Muted")
        lay.addWidget(cnt)
        if it.get("x") is not None:
            go = QPushButton("定位")
            go.setObjectName("Ghost")
            go.setCursor(Qt.PointingHandCursor)
            idx = self._index_of(it)
            go.clicked.connect(lambda _=False, i=idx: self._goto(i))
            lay.addWidget(go)
        return row

    def _index_of(self, issue: dict) -> int:
        for i, it in enumerate(self.state.issues):
            if it is issue or (it.get("code") == issue.get("code")
                               and it.get("rule") == issue.get("rule")):
                return i
        return -1

    def _goto(self, index: int) -> None:
        if index >= 0:
            self.state.select_issue(index)
        self.navigate("board" if self.state.has_geometry() else "results")

    def refresh_recent(self) -> None:
        self._clear(self.recent_host)
        recent = self.state.recent()
        if not recent:
            self.recent_host.addWidget(muted("暂无历史工程"))
            return
        for item in recent[:8]:
            row = QFrame()
            row.setObjectName("CardFlat")
            lay = QHBoxLayout(row)
            lay.setContentsMargins(10, 6, 10, 6)
            lay.setSpacing(8)
            box = QVBoxLayout()
            box.setSpacing(1)
            name = QLabel(item.get("board") or item.get("path") or "")
            name.setStyleSheet("font-weight:600;")
            sub = QLabel(f"{item.get('type', '')} · {item.get('factory', '')} · "
                         f"{item.get('time', '')}")
            sub.setObjectName("Muted")
            box.addWidget(name)
            box.addWidget(sub)
            lay.addLayout(box, 1)
            score = item.get("score")
            if score is not None:
                lab = QLabel(f"{score}")
                col = ("#3fb950" if score >= 85 else "#d29922" if score >= 60 else "#f0554d")
                lab.setStyleSheet(f"color:{col};font-weight:700;font-size:15px;")
                lay.addWidget(lab)
            lay.addWidget(muted(f"问题 {item.get('issues', 0)}"))
            self.recent_host.addWidget(row)

"""Evan DFM — PCB 可制造性(DFM)专业分析软件.

公开 API 摘要::

    from dfm import analyze, analyze_file, export_report, list_factories

    result = analyze_file("board.brd", factory="jlcpcb", board_type="rigid")
    export_report(result, "report.xlsx")

分层结构::

    dfm.core      规则模型 / 制程能力库 / 检查编排 / 插件注册
    dfm.checks    制造 DFM / 装配 SMT DFM / 开短路与网表
    dfm.io        BRD(extracta/SKILL) / Gerber / ODB++ / Excellon 解析
    dfm.report    Excel(Valor 式) / HTML / 文本 / JSON / SVG / PNG
    dfm.ui        PySide6 桌面界面
    dfm.cli       命令行与自动化
"""
from __future__ import annotations

__version__ = "1.1.0"
__product__ = "Evan DFM"

from dfm.core.pipeline import CHECK_MODULES, analyze, analyze_file
from dfm.core.capability import (
    factory_labels,
    list_board_types,
    list_factories,
    list_levels,
    load_capability,
)
from dfm.core.issue import (
    SEVERITY_LEVELS,
    compute_risk_score,
    make_issue,
    summarize_issues,
)
from dfm.core.registry import list_checks, register_check
from dfm.report import export_report

__all__ = [
    "CHECK_MODULES",
    "SEVERITY_LEVELS",
    "__product__",
    "__version__",
    "analyze",
    "analyze_file",
    "compute_risk_score",
    "export_report",
    "factory_labels",
    "list_board_types",
    "list_checks",
    "list_factories",
    "list_levels",
    "load_capability",
    "make_issue",
    "register_check",
    "summarize_issues",
]

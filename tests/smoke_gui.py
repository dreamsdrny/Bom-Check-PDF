"""GUI 离屏冒烟测试 (CI 用)。

在 ``QT_QPA_PLATFORM=offscreen`` 下构建全部页面、跑一次真实分析、
验证表格过滤/版图定位/主题切换/规则库页/五种报告导出, 任一步失败即以非零码退出。

运行::

    set QT_QPA_PLATFORM=offscreen      (Windows)
    export QT_QPA_PLATFORM=offscreen   (Linux/macOS)
    python tests/smoke_gui.py
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PAGES = ["home", "project", "analysis", "results", "board", "rules", "report", "settings"]


def main() -> int:
    from dfm.app import create_app
    from dfm.core.pipeline import analyze
    from dfm.report import export_report
    from dfm.ui.main_window import MainWindow
    from dfm.ui.state import AppState

    sample = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "samples", "brd_export_sample.json")
    if not os.path.isfile(sample):
        print(f"[FAIL] 缺少样例数据: {sample}")
        return 1

    app = create_app("dark")
    state = AppState()
    win = MainWindow(state)
    win.show()

    for pid in PAGES:
        win.navigate(pid)
        app.processEvents()
        assert win.page_title.text(), f"页面 {pid} 无标题"
    print(f"[OK] {len(PAGES)} 个页面构建与切换")

    result = analyze({
        "source_type": "json", "source_path": sample,
        "factory": "jlcpcb", "board_type": "rigid", "capability_level": "standard",
        "copper_um": 35, "board_thickness": 1.6, "load_plugins": False,
    })
    state.set_analysis(result)
    app.processEvents()
    assert result["issues"], "分析未产出问题"
    assert result["checks_run"], "无检查被执行"
    print(f"[OK] 分析: 问题 {result['issue_summary']['总计']} 项, "
          f"评分 {result['risk']['score']}, 检查 {len(result['checks_run'])} 项")

    for pid in PAGES:
        win.navigate(pid)
        app.processEvents()
    print("[OK] 带结果的页面刷新")

    results = win.pages["results"]
    board = win.pages["board"]
    assert results.table.model.rowCount() == len(result["issues"]), "问题表行数不符"
    assert board.view.scene().items(), "版图场景为空"
    assert board.view._scene._marker_items, "版图无问题标记"
    results.table.select_issue(0)
    board.select_issue(1)
    app.processEvents()
    print(f"[OK] 表格 {results.table.model.rowCount()} 行 / "
          f"版图标记 {len(board.view._scene._marker_items)} 个 / 交叉定位")

    results.table.filter_bar.search.setText("线宽")
    app.processEvents()
    filtered = results.table.model.rowCount()
    results.table.filter_bar.reset()
    app.processEvents()
    assert 0 < filtered < len(result["issues"]), "过滤未生效"
    print(f"[OK] 过滤: {filtered} → {results.table.model.rowCount()} 行")

    win.apply_theme("light")
    app.processEvents()
    win.apply_theme("dark")
    app.processEvents()
    print("[OK] 主题切换")

    rules = win.pages["rules"]
    assert rules.cap_table.rowCount() >= 1, "能力库列表为空"
    assert rules.check_table.rowCount() >= 8, "检查项列表为空"
    print(f"[OK] 规则库页: 能力库 {rules.cap_table.rowCount()} 家 / "
          f"检查 {rules.check_table.rowCount()} 项")

    with tempfile.TemporaryDirectory() as out:
        paths = export_report(result, os.path.join(out, "smoke"),
                              formats=["excel", "html", "json", "text", "svg"])
        assert set(paths) == {"excel", "html", "json", "text", "svg"}, paths
        for fmt, p in paths.items():
            size = os.path.getsize(p)
            assert size > 500, f"{fmt} 报告过小: {size}"
            print(f"[OK] 报告 {fmt:<6} {size:>7} bytes")

    print("GUI SMOKE OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())

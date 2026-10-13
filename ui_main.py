"""
Evan_DFM: 多级图形界面
=============================================
一级界面 (MainLauncher): 导航首页, 提供四个相互独立的二级界面入口:
    分析BRD      -> BRD_AIGUI        (Cadence Allegro BRD 约束/Checklist)
    分析Gerber   -> FabWindow(gerber)(Gerber RS-274X + NC Drill 解析/完整性/网络比对)
    分析ODB      -> FabWindow(odb++) (ODB++ 解析/完整性/网络比对)
    DFM          -> DFMDialog        (制程能力分析 + 报告; 含自动拼板入口)

每个二级界面均为独立 Toplevel, 拥有各自的输入/日志/结果状态, 互不干扰。
"""
import os
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from gui import BRD_AIGUI, DFMDialog, PanelDialog
from src.panel_ui import PanelDesigner
from src.gerber_viewer import GerberViewer
from src.win_dpi import apply_window, enable_dpi_awareness, set_app_user_model_id


class MainLauncher:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Evan_DFM: PCB DFM & Constraint Generator")
        apply_window(self.root, 760, 560)
        self.root.resizable(True, True)
        self._windows = {}
        self._build_ui()

    def _build_ui(self):
        main = ttk.Frame(self.root, padding=18)
        main.pack(fill=tk.BOTH, expand=True)

        ttk.Label(main, text="Evan_DFM", font=("Arial", 22, "bold")).pack(anchor=tk.W)
        ttk.Label(main, text="PCB 可制造性(DFM) / 约束自动生成 工作台",
                  font=("Arial", 11), foreground="#555").pack(anchor=tk.W, pady=(2, 4))
        ttk.Separator(main, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=(6, 14))

        grid = ttk.Frame(main)
        grid.pack(fill=tk.BOTH, expand=True)

        cards = [
            ("分析BRD", "读取 Allegro .brd\n生成约束/Checklist/DFM 校验", self._open_brd, "#2F5496"),
            ("分析Gerber", "RS-274X + NC Drill 解析\n完整性检查 / 网络比对 / 交叉定位", self._open_gerber, "#0a7a3a"),
            ("分析ODB", "ODB++ 工程解析\n完整性检查 / 网络比对 / 交叉定位", self._open_odb, "#B8860B"),
            ("DFM", "板厂制程能力比对\n问题分级报告 / 风险评分 / 自动拼板", self._open_dfm, "#7030A0"),
        ]
        for i, (title, desc, cmd, color) in enumerate(cards):
            card = ttk.LabelFrame(grid, text=title, padding=12)
            card.grid(row=i // 2, column=i % 2, sticky="nsew", padx=8, pady=8)
            ttk.Label(card, text=desc, justify=tk.LEFT).pack(anchor=tk.W, pady=(0, 8))
            ttk.Button(card, text=f"打开 {title}", command=cmd, width=18).pack(anchor=tk.E)
            grid.rowconfigure(i // 2, weight=1)
            grid.columnconfigure(i % 2, weight=1)

        tools = ttk.Frame(main)
        tools.pack(fill=tk.X, pady=(12, 0))
        ttk.Button(tools, text="排版设计 (拼版 / 三图联动)", command=self._open_panel).pack(side=tk.LEFT)
        ttk.Button(tools, text="Gerber查看器 (叠加对比)", command=self._open_gerber_viewer).pack(
            side=tk.LEFT, padx=6)

        self.status_var = tk.StringVar(value="Ready - 请选择上方的分析模块")
        ttk.Label(main, textvariable=self.status_var, relief=tk.SUNKEN,
                  anchor=tk.W).pack(fill=tk.X, side=tk.BOTTOM, pady=(10, 0))

    # ---- 二级界面入口 (相互独立) ----
    def _open_brd(self):
        win = BRD_AIGUI(self.root)
        self._windows.setdefault("brd", []).append(win)
        self.status_var.set("已打开: 分析BRD")

    def _open_gerber(self):
        win = FabWindow(self.root, mode="gerber")
        self._windows.setdefault("gerber", []).append(win)
        self.status_var.set("已打开: 分析Gerber")

    def _open_odb(self):
        win = FabWindow(self.root, mode="odb")
        self._windows.setdefault("odb", []).append(win)
        self.status_var.set("已打开: 分析ODB++")

    def _open_dfm(self):
        DFMDialog(self.root)
        self.status_var.set("已打开: DFM 制程能力分析")

    def _open_panel(self):
        PanelDesigner(self.root)
        self.status_var.set("已打开: 排版设计")

    def _open_gerber_viewer(self):
        GerberViewer(self.root)
        self.status_var.set("已打开: Gerber 查看器 (叠加对比)")

    def run(self):
        self.root.mainloop()


class FabWindow:
    """Gerber / ODB++ 制造数据 DFM 二级界面 (独立窗口)"""

    def __init__(self, master, mode="gerber"):
        self.mode = mode
        self.is_odb = mode == "odb"
        self.sources = []
        self.result = None
        self.issues = []
        self.comparison = None

        title = "Evan_DFM - 分析ODB++" if self.is_odb else "Evan_DFM - 分析Gerber"
        self.win = tk.Toplevel(master)
        self.win.title(title)
        apply_window(self.win, 940, 680)

        top = ttk.Frame(self.win, padding=10)
        top.pack(fill=tk.X)

        if self.is_odb:
            ttk.Button(top, text="选择 ODB++ 目录...", command=self._pick_dir).grid(
                row=0, column=0, sticky=tk.W)
        else:
            ttk.Button(top, text="选择 Gerber/钻孔文件...",
                       command=self._pick_files).grid(row=0, column=0, sticky=tk.W)
            ttk.Button(top, text="选择目录...", command=self._pick_dir).grid(
                row=0, column=1, sticky=tk.W, padx=6)

        self.sources_label = tk.StringVar(value="(未选择)")
        ttk.Label(top, textvariable=self.sources_label, foreground="#333").grid(
            row=1, column=0, columnspan=4, sticky=tk.W, pady=(6, 0))

        ttk.Label(top, text="CAD 网络表(可选):").grid(row=2, column=0, sticky=tk.W, pady=(8, 0))
        self.cad_path = tk.StringVar(value="")
        ttk.Entry(top, textvariable=self.cad_path, width=60).grid(
            row=2, column=1, columnspan=2, sticky=tk.W, padx=4, pady=(8, 0))
        ttk.Button(top, text="浏览...", command=self._pick_cad).grid(
            row=2, column=3, sticky=tk.W, pady=(8, 0))

        ttk.Label(top, text="(用于网络表比对: {\"net\": [\"R1.1\", ...]} 的 JSON)",
                  foreground="gray").grid(row=3, column=1, columnspan=3, sticky=tk.W)

        btns = ttk.Frame(self.win, padding=(10, 4))
        btns.pack(fill=tk.X)
        ttk.Button(btns, text="执行分析", command=self._run, width=14).pack(side=tk.LEFT)
        ttk.Button(btns, text="打开交叉定位", command=self._view, width=14).pack(side=tk.LEFT, padx=6)
        ttk.Button(btns, text="导出报告", command=self._export, width=14).pack(side=tk.LEFT, padx=6)
        ttk.Button(btns, text="清空日志", command=lambda: self.log.delete(1.0, tk.END)).pack(side=tk.LEFT, padx=6)
        ttk.Button(btns, text="关闭", command=self.win.destroy).pack(side=tk.RIGHT)

        log_frame = ttk.LabelFrame(self.win, text="分析结果", padding=6)
        log_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(4, 0))
        self.log = tk.Text(log_frame, wrap=tk.WORD, font=("Consolas", 10),
                           bg="#1e1e1e", fg="#d4d4d4", insertbackground="white")
        sb = ttk.Scrollbar(log_frame, orient=tk.VERTICAL, command=self.log.yview)
        self.log.configure(yscrollcommand=sb.set)
        self.log.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.log.tag_configure("err", foreground="#f44747")
        self.log.tag_configure("warn", foreground="#dcdcaa")
        self.log.tag_configure("ok", foreground="#4ec9b0")
        self.log.tag_configure("info", foreground="#569cd6")

        self.status_var = tk.StringVar(value="Ready")
        ttk.Label(self.win, textvariable=self.status_var, relief=tk.SUNKEN,
                  anchor=tk.W).pack(fill=tk.X, side=tk.BOTTOM)

    # ---- 选择 ----
    def _pick_files(self):
        paths = filedialog.askopenfilenames(
            title="选择 Gerber / NC Drill 文件",
            filetypes=[("Gerber/Drill", "*.gbr *.ger *.art *.pho *.drl"),
                       ("All files", "*.*")])
        if paths:
            self.sources = list(paths)
            self.sources_label.set(f"已选 {len(self.sources)} 个文件")

    def _pick_dir(self):
        path = filedialog.askdirectory(title="选择目录")
        if path:
            self.sources = [path]
            self.sources_label.set(path)

    def _pick_cad(self):
        path = filedialog.askopenfilename(title="选择 CAD 网络表 JSON",
                                          filetypes=[("JSON", "*.json"), ("All files", "*.*")])
        if path:
            self.cad_path.set(path)

    # ---- 执行 ----
    def _log(self, text, tag=None):
        self.log.insert(tk.END, text + "\n", tag)
        self.log.see(tk.END)

    def _run(self):
        import json as _json
        from src.fab_data_reader import analyze_fab, compare_netlists, netlist_compare_issues, normalize_cad_nets
        if not self.sources:
            messagebox.showwarning("提示", "请先选择制造数据 (文件或目录)")
            return
        self.log.delete(1.0, tk.END)
        self.status_var.set("分析中...")
        src = self.sources[0] if len(self.sources) == 1 else self.sources
        try:
            self.result = analyze_fab(src)
        except Exception as exc:  # noqa: BLE001
            self._log(f"[ERROR] 解析失败: {exc}", "err")
            self.status_var.set("失败")
            return
        stats = self.result["stats"]
        self.issues = list(self.result["issues"])
        self._log("=" * 66, "info")
        self._log(f"  制造数据 DFM ({'ODB++' if self.is_odb else 'Gerber'})", "info")
        self._log("=" * 66, "info")
        self._log(f"  Gerber 层: {len(stats['gerber_layers'])}   钻孔文件: {len(stats['drill_files'])}   "
                  f"ODB++: {len(stats['odb_jobs'])}", "ok")
        mf = stats['min_line_width_mm']
        mh = stats['min_hole_mm']
        self._log(f"  图形数: {stats['feature_count']}   孔数: {stats['hole_count']}   "
                  f"最小线宽: {mf if mf is not None else 'N/A'}mm   最小孔径: {mh if mh is not None else 'N/A'}mm", "ok")

        self.comparison = None
        cad = self.cad_path.get().strip()
        if cad and os.path.exists(cad):
            with open(cad, "r", encoding="utf-8-sig") as f:
                cad_nets = normalize_cad_nets(_json.load(f))
            self.comparison = compare_netlists(cad_nets, stats.get("nets", {}))
            self.issues += netlist_compare_issues(self.comparison)
            cmp = self.comparison
            tag = "ok" if cmp["passed"] else "err"
            self._log(f"  网络比对: CAD {cmp['cad_net_count']} / FAB {cmp['fab_net_count']} 网络   "
                      f"Open {cmp['open_count']} / Short {cmp['short_count']}   "
                      f"{'PASS' if cmp['passed'] else 'FAIL'}", tag)

        self._log("-" * 66, "info")
        if self.issues:
            for it in self.issues:
                loc = f" @({it['x']:.2f},{it['y']:.2f})" if it.get("x") is not None else ""
                tag = "err" if it["severity"] == "危险" else ("warn" if it["severity"] == "高" else "info")
                self._log(f"  [{it['severity']}] {it['code']} {it['rule']}: {it['description']}{loc}", tag)
        else:
            self._log("  未发现制造数据问题", "ok")
        self.status_var.set(f"完成 - 问题 {len(self.issues)} 项")
        if self.issues and any(it.get("x") is not None for it in self.issues):
            self._log("", None)
            self._log("  提示: 点『打开交叉定位』可在版图中高亮查看", "info")

    # ---- 交叉定位 ----
    def _view(self):
        if not self.result:
            messagebox.showwarning("提示", "请先执行分析")
            return
        from src.fab_viewer import FabViewer
        FabViewer(self.win, self.result["data"], self.issues)

    # ---- 导出 ----
    def _export(self):
        if not self.result:
            messagebox.showwarning("提示", "请先执行分析")
            return
        path = filedialog.asksaveasfilename(title="导出 DFM 报告", defaultextension=".xlsx",
                                            initialfile="fab_dfm_report.xlsx",
                                            filetypes=[("Excel", "*.xlsx"), ("All files", "*.*")])
        if not path:
            return
        try:
            from src.fab_data_reader import build_fab_report
            from src.dfm_report import generate_dfm_excel, generate_dfm_text
            src = self.sources[0] if self.sources else ""
            report = build_fab_report(self.result, self.comparison, src)
            board_meta = {"board_name": os.path.basename(os.path.normpath(src)) if src else "Fabrication"}
            generate_dfm_excel(report, path, board_meta)
            txt = os.path.splitext(path)[0] + ".txt"
            generate_dfm_text(report, txt, board_meta)
            messagebox.showinfo("导出完成", f"Excel: {path}\nText:  {txt}")
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("导出失败", str(exc))


def main():
    try:
        enable_dpi_awareness()
        set_app_user_model_id()
    except Exception:
        pass
    app = MainLauncher()
    app.run()


if __name__ == "__main__":
    main()
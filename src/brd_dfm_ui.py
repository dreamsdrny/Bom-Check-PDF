"""
BRD-AI: 分析BRD-DFM 二级界面 (SKILL 导出 JSON -> Valor 式 DFM)
=============================================================
工作流:
  ① 导出 SKILL 脚本 (brd_dfm_export.il)  -> 在 Allegro 内运行, 得到 JSON
  ② 选择该 JSON, 选择板厂/板型/能力等级/铜厚 (可选制造数据做 CAD vs FAB 比对)
  ③ 执行分析: 几何 DFM(线宽/间距/孔环/孔径/板边/阻焊桥/DRC) + 开短路 + 网表一致性
     所有 warning/fail 定位到 XY
  ④ 交叉定位: 在版图中高亮问题点
  ⑤ 导出 Valor 式 DFM 报告 (Excel/Text)
"""
import os
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from src.win_dpi import apply_window
from src.branding import win_title


class BrdDFMWindow:
    def __init__(self, master):
        self.win = tk.Toplevel(master)
        self.win.title(win_title("分析BRD-DFM (直接读.brd / SKILL导出, 定位XY)"))
        apply_window(self.win, 1080, 720)

        self.result = None

        self.factory = tk.StringVar(value="hongban")
        self.board_type = tk.StringVar(value="rigid")
        self.capability_level = tk.StringVar(value="standard")
        self.copper_um = tk.StringVar(value="35")
        self.copper_oz = tk.StringVar(value="")
        self.export_path = tk.StringVar(value="")
        self.fab_path = tk.StringVar(value="")

        self._build_ui()
        self._sync_levels()

    # ---------------- UI ----------------
    def _build_ui(self):
        top = ttk.Frame(self.win, padding=8)
        top.pack(fill=tk.X)

        # 第 1 行: SKILL 导出 + JSON 选择
        ttk.Button(top, text="① 导出SKILL脚本(.il)…", command=self._save_skill).grid(
            row=0, column=0, sticky=tk.W)
        ttk.Label(top, text="② .brd 或 导出的JSON:").grid(row=0, column=1, sticky=tk.E, padx=(10, 2))
        ttk.Entry(top, textvariable=self.export_path, width=46).grid(row=0, column=2, sticky=tk.W)
        ttk.Button(top, text="浏览…", command=self._pick_export).grid(row=0, column=3, sticky=tk.W, padx=4)

        # 第 2 行: 选项
        ttk.Label(top, text="板厂:").grid(row=1, column=0, sticky=tk.E, pady=(8, 0))
        opt = ttk.Frame(top)
        opt.grid(row=1, column=1, columnspan=3, sticky=tk.W, pady=(8, 0))
        ttk.Combobox(opt, textvariable=self.factory, values=["hongban", "sihui"],
                     state="readonly", width=9).pack(side=tk.LEFT)
        self.factory.trace_add("write", lambda *a: self._sync_levels())
        ttk.Label(opt, text=" 板型:").pack(side=tk.LEFT)
        ttk.Combobox(opt, textvariable=self.board_type, values=["rigid", "flex"],
                     state="readonly", width=7).pack(side=tk.LEFT)
        self.board_type.trace_add("write", lambda *a: self._sync_levels())
        ttk.Label(opt, text=" 能力等级:").pack(side=tk.LEFT)
        self.level_combo = ttk.Combobox(opt, textvariable=self.capability_level,
                                        values=["standard", "automotive", "extreme"],
                                        state="readonly", width=11)
        self.level_combo.pack(side=tk.LEFT)
        ttk.Label(opt, text=" 铜厚um:").pack(side=tk.LEFT)
        ttk.Entry(opt, textvariable=self.copper_um, width=5).pack(side=tk.LEFT)
        ttk.Label(opt, text=" oz:").pack(side=tk.LEFT)
        ttk.Entry(opt, textvariable=self.copper_oz, width=5).pack(side=tk.LEFT)

        # 第 3 行: 可选制造数据
        ttk.Label(top, text="制造数据(可选):").grid(row=2, column=0, sticky=tk.E, pady=(6, 0))
        ttk.Entry(top, textvariable=self.fab_path, width=46).grid(row=2, column=1, columnspan=2,
                                                                  sticky=tk.W, pady=(6, 0))
        ttk.Button(top, text="浏览…", command=self._pick_fab).grid(row=2, column=3, sticky=tk.W, padx=4, pady=(6, 0))
        ttk.Label(top, text="(Gerber/ODB++ 目录或文件, 用于 CAD vs FAB 网表比对)",
                  foreground="gray").grid(row=3, column=1, columnspan=3, sticky=tk.W)

        # 按钮行
        btns = ttk.Frame(self.win, padding=(8, 4))
        btns.pack(fill=tk.X)
        self.run_btn = ttk.Button(btns, text="③ 执行分析", command=self._run, width=14)
        self.run_btn.pack(side=tk.LEFT)
        ttk.Button(btns, text="④ 打开交叉定位", command=self._view, width=14).pack(side=tk.LEFT, padx=6)
        ttk.Button(btns, text="⑤ 导出报告", command=self._export, width=14).pack(side=tk.LEFT, padx=6)
        ttk.Button(btns, text="清空日志", command=self._clear_log, width=10).pack(side=tk.LEFT, padx=6)
        ttk.Button(btns, text="关闭", command=self.win.destroy).pack(side=tk.RIGHT)

        # 主体: 左日志 / 右问题表
        body = ttk.Frame(self.win, padding=8)
        body.pack(fill=tk.BOTH, expand=True)

        left = ttk.LabelFrame(body, text="分析日志", padding=6)
        left.pack(side=tk.LEFT, fill=tk.Y)
        self.log = tk.Text(left, width=42, height=28, wrap=tk.WORD, font=("Consolas", 10),
                           bg="#1e1e1e", fg="#d4d4d4", insertbackground="white")
        lsb = ttk.Scrollbar(left, orient=tk.VERTICAL, command=self.log.yview)
        self.log.configure(yscrollcommand=lsb.set)
        self.log.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        lsb.pack(side=tk.RIGHT, fill=tk.Y)
        for tag, color in (("err", "#f44747"), ("warn", "#dcdcaa"),
                           ("ok", "#4ec9b0"), ("info", "#569cd6")):
            self.log.tag_configure(tag, foreground=color)

        right = ttk.LabelFrame(body, text="DFM 问题 (双击定位, 含XY)", padding=6)
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(8, 0))
        cols = ("sev", "code", "rule", "cnt", "x", "y", "layer", "net")
        self.tree = ttk.Treeview(right, columns=cols, show="headings", height=26)
        heads = {"sev": ("严重度", 60), "code": ("编号", 90), "rule": ("问题点", 130),
                 "cnt": ("数量", 50), "x": ("X mm", 70), "y": ("Y mm", 70),
                 "layer": ("层", 70), "net": ("网络", 110)}
        for c in cols:
            text, w = heads[c]
            self.tree.heading(c, text=text)
            self.tree.column(c, width=w, anchor=tk.CENTER if c in ("sev", "cnt", "x", "y") else tk.W)
        tsb = ttk.Scrollbar(right, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=tsb.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tsb.pack(side=tk.RIGHT, fill=tk.Y)
        for sev, color in (("危险", "#c00000"), ("高", "#d2691e"),
                           ("中", "#b8860b"), ("低", "#33691e")):
            self.tree.tag_configure(sev, foreground=color)
        self.tree.bind("<Double-1>", self._on_tree_double)

        self.status_var = tk.StringVar(value="Ready - 直接选择 .brd 执行分析 (需本机 Allegro); 或选择 SKILL 导出的 JSON")
        ttk.Label(self.win, textvariable=self.status_var, relief=tk.SUNKEN,
                  anchor=tk.W).pack(fill=tk.X, side=tk.BOTTOM)

    # ---------------- 交互 ----------------
    def _log(self, text, tag=None):
        self.log.insert(tk.END, text + "\n", tag)
        self.log.see(tk.END)

    def _clear_log(self):
        self.log.delete(1.0, tk.END)

    def _sync_levels(self):
        try:
            from src.dfm_engine import list_levels
            levels = list_levels(self.board_type.get(), factory=self.factory.get()) or ["standard"]
        except Exception:  # noqa: BLE001
            levels = ["standard"]
        self.level_combo.configure(values=levels)
        if self.capability_level.get() not in levels:
            self.capability_level.set(levels[0])

    def _save_skill(self):
        from src.skill_export import write_export_skill, EXPORT_SKILL_FILENAME
        path = filedialog.asksaveasfilename(
            title="保存 SKILL 导出脚本", defaultextension=".il",
            initialfile=EXPORT_SKILL_FILENAME,
            filetypes=[("SKILL", "*.il"), ("All files", "*.*")])
        if not path:
            return
        try:
            write_export_skill(path)
            messagebox.showinfo(
                "已导出 SKILL 脚本",
                f"已保存:\n{path}\n\n"
                "在 Allegro PCB Editor 中:\n"
                "  1) 打开目标 .brd\n"
                f"  2) Command 窗口执行: skill load \"{os.path.basename(path)}\"\n"
                "  3) 执行: brdExportJSON\n"
                "  4) 回到本窗口选择生成的 JSON")
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("导出失败", str(exc))

    def _pick_export(self):
        path = filedialog.askopenfilename(
            title="选择 .brd (直接分析) 或 Allegro 导出的 JSON",
            filetypes=[("BRD / JSON", "*.brd *.json"), ("BRD files", "*.brd"),
                       ("JSON", "*.json"), ("All files", "*.*")])
        if path:
            self.export_path.set(path)

    def _pick_fab(self):
        path = filedialog.askopenfilename(
            title="选择制造数据文件 (取消则选目录)",
            filetypes=[("Gerber/Drill", "*.gbr *.ger *.art *.pho *.drl"), ("All files", "*.*")])
        if not path:
            path = filedialog.askdirectory(title="选择 ODB++ / 制造数据目录")
        if path:
            self.fab_path.set(path)

    # ---------------- 分析 ----------------
    def _run(self):
        path = self.export_path.get().strip()
        if not path or not os.path.exists(path):
            messagebox.showwarning("提示", "请先选择 .brd 文件 (直接分析) 或 Allegro 导出的 JSON")
            return
        try:
            copper_um = float(self.copper_um.get() or 35)
        except ValueError:
            copper_um = 35.0
        copper_oz = None
        if self.copper_oz.get().strip():
            try:
                copper_oz = float(self.copper_oz.get())
            except ValueError:
                copper_oz = None
        fab = self.fab_path.get().strip() or None

        self.run_btn.config(state=tk.DISABLED)
        is_brd = path.lower().endswith(".brd")
        self.status_var.set("extracta 无界面提取中 (约1-2分钟)..." if is_brd else "分析中...")
        self._clear_log()
        if is_brd:
            self._log("  直接分析 .brd: 调用 Cadence extracta 无界面提取几何/网表/DRC...", "info")
            self._log("  (需本机已安装 Allegro; 大板约需 1-2 分钟, 请耐心等待)", "info")
        threading.Thread(target=self._analyze_worker, daemon=True, args=(
            path, self.factory.get(), self.board_type.get(),
            self.capability_level.get(), copper_um, copper_oz, fab)).start()

    def _analyze_worker(self, path, factory, board_type, level, copper_um, copper_oz, fab):
        try:
            if path.lower().endswith(".brd"):
                from src.brd_dfm import analyze_brd_file
                base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                name = os.path.splitext(os.path.basename(path))[0]
                json_out = os.path.join(base, "output", "extracta", name, "brd_export.json")
                self.result = analyze_brd_file(
                    path, factory=factory, board_type=board_type,
                    capability_level=level, copper_um=copper_um,
                    copper_oz=copper_oz, fab_path=fab, json_out=json_out)
            else:
                from src.brd_dfm import analyze_brd_dfm
                self.result = analyze_brd_dfm(
                    path, factory=factory, board_type=board_type,
                    capability_level=level, copper_um=copper_um,
                    copper_oz=copper_oz, fab_path=fab)
            self.win.after(0, self._render_result)
        except Exception as exc:  # noqa: BLE001
            import traceback
            err = traceback.format_exc()
            self.win.after(0, lambda: self._on_error(exc, err))

    def _on_error(self, exc, tb):
        self.run_btn.config(state=tk.NORMAL)
        self.status_var.set("失败")
        self._log(f"[ERROR] {exc}", "err")
        self._log(tb, "err")

    def _render_result(self):
        self.run_btn.config(state=tk.NORMAL)
        r = self.result
        gs = r["geometry_stats"]
        nl = r["netlist_summary"]
        self._log("=" * 44, "info")
        self._log(f"  BRD DFM: {r['board_meta'].get('board_name','')}", "info")
        self._log(f"  {r['factory_label']} {r['board_type_label']} - {r['capability_label']} "
                  f"(铜厚 {r['copper_um']}um)", "info")
        self._log("=" * 44, "info")
        self._log(f"  网络 {gs['net_count']} / 节点 {gs['node_count']} / 器件 {gs['component_count']} / "
                  f"过孔 {gs['via_count']} / 焊盘 {gs['pad_count']}", "ok")
        self._log(f"  走线 {gs['cline_count']} / 铺铜 {gs['shape_count']} / "
                  f"测试点 {gs['testpoint_count']} / Allegro DRC {gs['drc_count']}", "ok")
        self._log("-" * 44, "info")
        self._log(f"  开短路: 开路网络 {nl['open_net_count']} / 短路网络对 {nl['short_net_count']} / "
                  f"无铜节点 {nl['nodes_without_copper']}",
                  "err" if (nl['open_net_count'] or nl['short_net_count']) else "ok")
        self._log(f"  物理连通域 {nl['physical_component_count']} / 逻辑网络 {nl['logical_net_count']}", "ok")
        if nl.get("fab_compare"):
            fc = nl["fab_compare"]
            self._log(f"  CAD vs FAB: CAD {fc['cad_net_count']} / FAB {fc['fab_net_count']} 网络, "
                      f"Open {fc['open_count']} / Short {fc['short_count']} "
                      f"({'PASS' if fc['passed'] else 'FAIL'})",
                      "ok" if fc["passed"] else "err")
        self._log("-" * 44, "info")
        summary = r["issue_summary"]
        self._log(f"  问题总数 {summary.get('总计', 0)}:  危险 {summary.get('危险',0)} / "
                  f"高 {summary.get('高',0)} / 中 {summary.get('中',0)} / 低 {summary.get('低',0)}",
                  "warn")
        risk = r.get("risk", {})
        self._log(f"  可制造性评分 {risk.get('score','-')}/100  风险 {risk.get('risk_level','-')}  "
                  f"{risk.get('verdict','')}", "warn")

        # 填充问题表
        for row in self.tree.get_children():
            self.tree.delete(row)
        for i, it in enumerate(r["issues"]):
            self.tree.insert("", tk.END, iid=str(i), values=(
                it.get("severity", ""), it.get("code", ""), it.get("rule", ""),
                it.get("count", 1),
                f"{it['x']:.3f}" if it.get("x") is not None else "",
                f"{it['y']:.3f}" if it.get("y") is not None else "",
                it.get("layer", ""), it.get("net", "")),
                tags=(it.get("severity", ""),))
        self.status_var.set(
            f"完成 - 问题 {summary.get('总计',0)} 项 (危险 {summary.get('危险',0)}), "
            f"开路 {nl['open_net_count']} / 短路 {nl['short_net_count']}, "
            f"实例 {len(r['instances'])} 处")
        self._log("", None)
        self._log("  提示: 双击问题行或点『打开交叉定位』在版图中高亮 XY", "info")

    # ---------------- 交叉定位 ----------------
    def _viewer_issues(self):
        """把汇总问题展开成带 XY 的实例供查看器逐点高亮"""
        r = self.result
        inst = list(r.get("instances", []))
        if inst:
            return inst
        return [it for it in r["issues"] if it.get("x") is not None]

    def _view(self):
        if not self.result:
            messagebox.showwarning("提示", "请先执行分析")
            return
        from src.fab_viewer import FabViewer
        FabViewer(self.win, self.result["fab_data"], self._viewer_issues(),
                  title="BRD DFM 交叉定位 (SKILL 导出几何)")

    def _on_tree_double(self, _event):
        sel = self.tree.selection()
        if not sel or not self.result:
            return
        from src.fab_viewer import FabViewer
        FabViewer(self.win, self.result["fab_data"], self._viewer_issues(),
                  title="BRD DFM 交叉定位 (SKILL 导出几何)")

    # ---------------- 导出报告 ----------------
    def _export(self):
        if not self.result:
            messagebox.showwarning("提示", "请先执行分析")
            return
        path = filedialog.asksaveasfilename(
            title="导出 DFM 报告", defaultextension=".xlsx",
            initialfile="brd_dfm_report.xlsx",
            filetypes=[("Excel", "*.xlsx"), ("All files", "*.*")])
        if not path:
            return
        try:
            import json
            from src.dfm_report import generate_dfm_excel, generate_dfm_text
            board_meta = self.result.get("board_meta", {})
            generate_dfm_excel(self.result, path, board_meta)
            txt = os.path.splitext(path)[0] + ".txt"
            generate_dfm_text(self.result, txt, board_meta)
            js = os.path.splitext(path)[0] + ".json"
            slim = {k: v for k, v in self.result.items()
                    if k not in ("fab_data", "instances")}
            slim["instance_count"] = len(self.result.get("instances", []))
            with open(js, "w", encoding="utf-8") as f:
                json.dump(slim, f, indent=2, ensure_ascii=False, default=str)
            messagebox.showinfo("导出完成", f"Excel: {path}\nText:  {txt}\nJSON:  {js}")
            self._log(f"  已导出报告: {path}", "ok")
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("导出失败", str(exc))

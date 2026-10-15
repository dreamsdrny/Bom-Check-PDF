"""
DFM 检查中心: 多级界面 (二级=检查中心, 三级=结果/交叉定位/报告)
=============================================================
一级 (MainLauncher) → 二级 (DfmHubWindow: 数据源 + 检查模块 + 执行)
                    → 三级 (结果清单 / 统计评分 / 图形交叉定位 / Valor 式报告导出)

统一编排 src.dfm_hub.run_hub(), 输出与 dfm_report 兼容的单一 analysis。
"""
import json
import os
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from src.branding import win_title
from src.dfm_hub import CHECK_MODULES, run_hub
from src.win_dpi import apply_window

SOURCE_TYPES = [
    ("BRD 文件 (.brd, 需 Allegro extracta)", "brd"),
    ("导出 JSON (extracta / SKILL)", "json"),
    ("Gerber 文件/目录 (RS-274X + NC Drill)", "gerber"),
    ("ODB++ 工程目录", "odb"),
    ("无源文件 (仅参数化检查)", "none"),
]

SEV_COLOR = {"危险": "#c00000", "高": "#d2691e", "中": "#b8860b", "低": "#33691e", "关闭": "#666666"}


class DfmHubWindow:
    def __init__(self, master):
        self.win = tk.Toplevel(master)
        self.win.title(win_title("DFM 检查中心"))
        apply_window(self.win, 1180, 800)

        self.analysis = None
        self._busy = False
        self.mod_vars = {}
        self._q = queue.Queue()          # 工作线程 -> UI 线程 (线程安全)
        self._build()
        self._poll()

    # ---------------- UI ----------------
    def _row(self, parent, r, label, var, browse=None, width=64, hint=""):
        ttk.Label(parent, text=label).grid(row=r, column=0, sticky=tk.W, pady=2)
        ttk.Entry(parent, textvariable=var, width=width).grid(row=r, column=1, sticky="ew", padx=4)
        if browse:
            ttk.Button(parent, text="浏览...", command=browse).grid(row=r, column=2, sticky=tk.W)
        if hint:
            ttk.Label(parent, text=hint, foreground="gray").grid(row=r, column=3, sticky=tk.W, padx=6)
        return r + 1

    def _build(self):
        outer = ttk.Frame(self.win, padding=8)
        outer.pack(fill=tk.BOTH, expand=True)

        # ---- 二级: 数据源 + 检查配置 ----
        cfg = ttk.LabelFrame(outer, text="① 数据源与检查配置", padding=8)
        cfg.pack(fill=tk.X)

        self.source_var = tk.StringVar(value=SOURCE_TYPES[0][0])
        ttk.Label(cfg, text="数据源:").grid(row=0, column=0, sticky=tk.W)
        self.source_combo = ttk.Combobox(cfg, textvariable=self.source_var, state="readonly",
                                         values=[s[0] for s in SOURCE_TYPES], width=38)
        self.source_combo.grid(row=0, column=1, sticky=tk.W, padx=4)

        ttk.Label(cfg, text="板厂:").grid(row=0, column=2, sticky=tk.E, padx=(16, 2))
        self.factory_var = tk.StringVar(value="hongban")
        self.factory_combo = ttk.Combobox(cfg, textvariable=self.factory_var, state="readonly",
                                          values=self._factories(), width=10)
        self.factory_combo.grid(row=0, column=3, sticky=tk.W)
        self.factory_combo.bind("<<ComboboxSelected>>", lambda e: self._sync_levels())

        ttk.Label(cfg, text="板型:").grid(row=1, column=2, sticky=tk.E, padx=(16, 2))
        self.board_type_var = tk.StringVar(value="rigid")
        self.type_combo = ttk.Combobox(cfg, textvariable=self.board_type_var, state="readonly",
                                       values=["rigid", "flex"], width=10)
        self.type_combo.grid(row=1, column=3, sticky=tk.W)
        self.type_combo.bind("<<ComboboxSelected>>", lambda e: self._sync_levels())

        ttk.Label(cfg, text="能力等级:").grid(row=2, column=2, sticky=tk.E, padx=(16, 2))
        self.level_var = tk.StringVar(value="standard")
        self.level_combo = ttk.Combobox(cfg, textvariable=self.level_var, state="readonly",
                                        values=["standard", "automotive", "extreme"], width=10)
        self.level_combo.grid(row=2, column=3, sticky=tk.W)
        self._sync_levels()

        ttk.Label(cfg, text="铜厚um/oz:").grid(row=3, column=2, sticky=tk.E, padx=(16, 2))
        f = ttk.Frame(cfg)
        f.grid(row=3, column=3, sticky=tk.W)
        self.copper_um_var = tk.StringVar(value="35")
        self.copper_oz_var = tk.StringVar(value="")
        ttk.Entry(f, textvariable=self.copper_um_var, width=6).pack(side=tk.LEFT)
        ttk.Label(f, text=" / ").pack(side=tk.LEFT)
        ttk.Entry(f, textvariable=self.copper_oz_var, width=6).pack(side=tk.LEFT)

        ttk.Label(cfg, text="板厚mm:").grid(row=4, column=2, sticky=tk.E, padx=(16, 2))
        self.thickness_var = tk.StringVar(value="")
        ttk.Entry(cfg, textvariable=self.thickness_var, width=10).grid(row=4, column=3, sticky=tk.W)
        ttk.Label(cfg, text="(Gerber 无板厚信息, 纵横比检查需填写)",
                  foreground="gray").grid(row=4, column=4, sticky=tk.W, padx=6)

        self.src_path = tk.StringVar()
        self.fab_path = tk.StringVar()
        self.cad_path = tk.StringVar()
        self.design_path = tk.StringVar()
        self.panel_path = tk.StringVar()

        r = 1
        r = self._row(cfg, r, "BRD / JSON:", self.src_path, self._pick_src,
                      hint=".brd 或导出的 JSON")
        r = self._row(cfg, r, "制造数据:", self.fab_path, self._pick_fab,
                      hint="Gerber/ODB++/Drill 目录或文件")
        r = self._row(cfg, r, "CAD 网表:", self.cad_path, self._pick_cad,
                      hint='JSON: {"net":["R1.1",...]}')
        r = self._row(cfg, r, "设计参数:", self.design_path, self._pick_design,
                      hint="制程能力 DFM 参数 JSON")
        r = self._row(cfg, r, "拼版参数:", self.panel_path, self._pick_panel,
                      hint="拼版 DFM 参数 JSON")
        cfg.columnconfigure(1, weight=1)

        # ---- 检查模块 ----
        mods = ttk.LabelFrame(outer, text="② 检查模块 (可组合)", padding=8)
        mods.pack(fill=tk.X, pady=(8, 0))
        for i, (mid, label) in enumerate(CHECK_MODULES):
            var = tk.BooleanVar(value=True)
            ttk.Checkbutton(mods, text=label, variable=var).grid(
                row=i // 2, column=i % 2, sticky=tk.W, padx=6, pady=2)
            self.mod_vars[mid] = var
        ttk.Button(mods, text="生成设计参数模板", command=self._tpl_design).grid(
            row=0, column=2, rowspan=2, sticky=tk.N, padx=10)
        ttk.Button(mods, text="生成拼版参数模板", command=self._tpl_panel).grid(
            row=2, column=2, rowspan=2, sticky=tk.N, padx=10)

        # ---- 操作 ----
        bar = ttk.Frame(outer)
        bar.pack(fill=tk.X, pady=(8, 0))
        self.run_btn = ttk.Button(bar, text="③ 执行分析", command=self._run, width=16)
        self.run_btn.pack(side=tk.LEFT)
        ttk.Button(bar, text="④ 打开交叉定位", command=self._open_viewer, width=16).pack(side=tk.LEFT, padx=6)
        ttk.Button(bar, text="⑤ 导出报告", command=self._export, width=14).pack(side=tk.LEFT)
        ttk.Separator(bar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=8)
        ttk.Button(bar, text="参数 DFM 对话框", command=self._open_param_dfm).pack(side=tk.LEFT)
        ttk.Button(bar, text="BRD-DFM 窗口", command=self._open_brd_dfm).pack(side=tk.LEFT, padx=6)
        self.progress = ttk.Progressbar(bar, mode="indeterminate", length=160)
        self.progress.pack(side=tk.RIGHT)

        # ---- 三级: 结果 ----
        nb = ttk.Notebook(outer)
        nb.pack(fill=tk.BOTH, expand=True, pady=(8, 0))

        # 问题清单
        tab_issues = ttk.Frame(nb)
        nb.add(tab_issues, text="问题清单")
        cols = ("sev", "code", "module", "cat", "rule", "cnt", "x", "y", "desc")
        self.tree = ttk.Treeview(tab_issues, columns=cols, show="headings", height=16)
        heads = {"sev": "严重度", "code": "代码", "module": "模块", "cat": "类别",
                 "rule": "规则", "cnt": "数量", "x": "X(mm)", "y": "Y(mm)", "desc": "问题描述 / 建议"}
        widths = {"sev": 60, "code": 80, "module": 80, "cat": 70, "rule": 130,
                  "cnt": 50, "x": 70, "y": 70, "desc": 520}
        for c in cols:
            self.tree.heading(c, text=heads[c])
            self.tree.column(c, width=widths[c], anchor=tk.W if c == "desc" else tk.CENTER)
        vsb = ttk.Scrollbar(tab_issues, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        for sev, col in SEV_COLOR.items():
            self.tree.tag_configure(sev, foreground=col)
        self.tree.bind("<Double-Button-1>", lambda e: self._open_viewer())

        # 统计
        tab_stat = ttk.Frame(nb)
        nb.add(tab_stat, text="统计与评分")
        self.stat_text = tk.Text(tab_stat, wrap=tk.WORD, font=("Consolas", 10))
        self.stat_text.pack(fill=tk.BOTH, expand=True)

        # 日志
        tab_log = ttk.Frame(nb)
        nb.add(tab_log, text="执行日志")
        self.log = tk.Text(tab_log, wrap=tk.WORD, font=("Consolas", 10),
                           bg="#1e1e1e", fg="#d4d4d4", insertbackground="white")
        self.log.pack(fill=tk.BOTH, expand=True)

        self.status_var = tk.StringVar(value="Ready — 选择数据源与检查模块后点『③ 执行分析』")
        ttk.Label(self.win, textvariable=self.status_var, relief=tk.SUNKEN,
                  anchor=tk.W).pack(fill=tk.X, side=tk.BOTTOM)

    # ---------------- helpers ----------------
    def _factories(self):
        try:
            from src.dfm_engine import FACTORY_LABELS
            return list(FACTORY_LABELS.keys())
        except Exception:
            return ["hongban", "sihui"]

    def _sync_levels(self):
        try:
            from src.dfm_engine import list_levels
            levels = list_levels(self.board_type_var.get(), factory=self.factory_var.get()) or ["standard"]
            self.level_combo.configure(values=levels)
            if self.level_var.get() not in levels:
                self.level_var.set(levels[0])
        except Exception:
            pass

    def _log(self, text):
        self.log.insert(tk.END, text + "\n")
        self.log.see(tk.END)

    def _pick_src(self):
        label = self.source_var.get()
        stype = next((v for k, v in SOURCE_TYPES if k == label), "none")
        if stype in ("gerber", "odb"):
            p = filedialog.askdirectory(title="选择 Gerber / ODB++ 目录")
            if not p:
                p = filedialog.askopenfilename(
                    title="或选择单个 Gerber / 钻孔文件",
                    filetypes=[("Gerber/Drill", "*.gbr *.ger *.art *.pho *.drl *.gto *.gbo"),
                               ("All files", "*.*")])
        else:
            p = filedialog.askopenfilename(title="选择 BRD 或导出 JSON",
                                           filetypes=[("BRD/JSON", "*.brd *.json"),
                                                      ("All files", "*.*")])
        if p:
            self.src_path.set(p)

    def _pick_fab(self):
        p = filedialog.askdirectory(title="选择制造数据目录 (Gerber/ODB++)")
        if not p:
            p = filedialog.askopenfilename(title="或选择单个 Gerber/钻孔文件",
                                           filetypes=[("Gerber/Drill", "*.gbr *.ger *.drl *.pho"),
                                                      ("All files", "*.*")])
        if p:
            self.fab_path.set(p)

    def _pick_cad(self):
        p = filedialog.askopenfilename(title="选择 CAD 网表 JSON",
                                       filetypes=[("JSON", "*.json"), ("All files", "*.*")])
        if p:
            self.cad_path.set(p)

    def _pick_design(self):
        p = filedialog.askopenfilename(title="选择设计参数 JSON",
                                       filetypes=[("JSON", "*.json"), ("All files", "*.*")])
        if p:
            self.design_path.set(p)

    def _pick_panel(self):
        p = filedialog.askopenfilename(title="选择拼版参数 JSON",
                                       filetypes=[("JSON", "*.json"), ("All files", "*.*")])
        if p:
            self.panel_path.set(p)

    def _tpl_design(self):
        p = filedialog.asksaveasfilename(title="保存设计参数模板", defaultextension=".json",
                                         initialfile="dfm_design_params.json",
                                         filetypes=[("JSON", "*.json")])
        if not p:
            return
        tpl = {
            "board_size": [100, 80], "min_line_width": 0.10, "min_spacing": 0.10,
            "copper_um": 35, "copper_oz": 1.0, "min_mech_drill": 0.2, "max_mech_drill": 3.2,
            "min_laser_via": 0.1, "min_annular_ring": 0.075, "board_thickness": 1.6,
            "pth_to_pth_spacing": 0.3, "copper_to_edge_inner": 0.25, "copper_to_edge_outer": 0.2,
            "npth_to_edge": 0.5, "solder_mask_bridge": 0.1, "solder_mask_opening": 0.1,
            "silkscreen_width": 0.15, "silkscreen_clearance": 0.15,
            "plug_hole_diameter": 0.3, "test_pad_2wire": 0.4, "surface_finish": "ENIG",
        }
        with open(p, "w", encoding="utf-8") as f:
            json.dump(tpl, f, indent=2, ensure_ascii=False)
        self.design_path.set(p)
        messagebox.showinfo("已生成", p)

    def _tpl_panel(self):
        p = filedialog.asksaveasfilename(title="保存拼版参数模板", defaultextension=".json",
                                         initialfile="panel_params.json",
                                         filetypes=[("JSON", "*.json")])
        if not p:
            return
        tpl = {"mode": "single", "arrangement": "forward", "board_w": 31, "board_h": 31.2,
               "wp_nl_option": "18.4x24.4", "rail_mm": 4, "set_gap": 2, "pcs_gap": 0,
               "impedance": {"enabled": False, "count": 2, "length_mm": 20, "width_mm": 3, "gap_mm": 5}}
        with open(p, "w", encoding="utf-8") as f:
            json.dump(tpl, f, indent=2, ensure_ascii=False)
        self.panel_path.set(p)
        messagebox.showinfo("已生成", p)

    # ---------------- 执行 ----------------
    def _collect_cfg(self):
        label = self.source_var.get()
        stype = next((v for k, v in SOURCE_TYPES if k == label), "none")
        mods = [mid for mid, var in self.mod_vars.items() if var.get()]
        return {
            "source_type": stype,
            "source_path": self.src_path.get().strip(),
            "fab_path": self.fab_path.get().strip() or None,
            "cad_nets": self.cad_path.get().strip() or None,
            "design_params": self.design_path.get().strip() or None,
            "panel_params": self.panel_path.get().strip() or None,
            "factory": self.factory_var.get(),
            "board_type": self.board_type_var.get(),
            "capability_level": self.level_var.get(),
            "copper_um": float(self.copper_um_var.get() or 35),
            "copper_oz": float(self.copper_oz_var.get()) if self.copper_oz_var.get().strip() else None,
            "board_thickness": (float(self.thickness_var.get())
                                if self.thickness_var.get().strip() else None),
            "modules": mods,
        }

    def _run(self):
        if self._busy:
            return
        cfg = self._collect_cfg()
        if not cfg["modules"]:
            messagebox.showwarning("提示", "请至少勾选一个检查模块")
            return
        if cfg["source_type"] != "none" and not cfg["source_path"]:
            messagebox.showwarning("提示", "请选择 BRD / JSON 数据源")
            return
        if "capability" in cfg["modules"] and not cfg["design_params"] and cfg["source_type"] == "none":
            messagebox.showwarning("提示", "制程能力 DFM 需要『设计参数 JSON』")
            return
        self._busy = True
        self.run_btn.config(state=tk.DISABLED)
        self.progress.start(12)
        self.log.delete(1.0, tk.END)
        self.status_var.set("分析中 ...")
        self._log(f"数据源: {cfg['source_type']} {cfg['source_path']}")
        self._log(f"模块: {', '.join(cfg['modules'])}")
        self._log(f"规则集: {cfg['factory']} / {cfg['board_type']} / {cfg['capability_level']} / "
                  f"铜厚 {cfg['copper_um']}um")

        def worker():
            try:
                res = run_hub(cfg, progress=lambda m: self._q.put(("log", m)))
                self._q.put(("done", res))
            except Exception as exc:  # noqa: BLE001
                import traceback
                self._q.put(("fail", (exc, traceback.format_exc())))

        threading.Thread(target=worker, daemon=True).start()

    def _poll(self):
        """在主线程中取出工作线程消息 (避免跨线程操作 Tk)"""
        try:
            while True:
                kind, payload = self._q.get_nowait()
                if kind == "log":
                    self._log(payload)
                    self.status_var.set(payload)
                elif kind == "done":
                    self._done(payload)
                elif kind == "fail":
                    self._fail(*payload)
        except queue.Empty:
            pass
        except Exception as exc:  # noqa: BLE001
            try:
                self._log(f"[UI ERROR] {exc}")
            except Exception:
                pass
        try:
            self.win.after(80, self._poll)
        except Exception:
            pass

    def _fail(self, exc, tb):
        self._busy = False
        self.run_btn.config(state=tk.NORMAL)
        self.progress.stop()
        self.status_var.set("分析失败")
        self._log(f"[ERROR] {exc}\n{tb}")
        messagebox.showerror("分析失败", str(exc))

    def _done(self, res):
        self._busy = False
        self.run_btn.config(state=tk.NORMAL)
        self.progress.stop()
        self.analysis = res
        self._fill_issues(res)
        self._fill_stats(res)
        s = res["issue_summary"]
        self.status_var.set(
            f"完成: 问题 {s.get('总计', 0)} 项 (危险 {s.get('危险',0)} / 高 {s.get('高',0)} / "
            f"中 {s.get('中',0)} / 低 {s.get('低',0)})  评分 {res['risk'].get('score')}/100  "
            f"模块: {', '.join(res.get('modules_run', []))}")
        self._log("-" * 60)
        self._log(self.status_var.get())

    def _fill_issues(self, res):
        self.tree.delete(*self.tree.get_children())
        for it in res.get("issues", []):
            x = it.get("x"); y = it.get("y")
            self.tree.insert("", tk.END, values=(
                it.get("severity", ""), it.get("code", ""), it.get("module", ""),
                it.get("category", ""), it.get("rule", ""), it.get("count", 1),
                f"{x:.3f}" if isinstance(x, (int, float)) else "",
                f"{y:.3f}" if isinstance(y, (int, float)) else "",
                (it.get("description", "") + "  ||  建议: " + it.get("suggestion", "")).strip(),
            ), tags=(it.get("severity", ""),))

    def _fill_stats(self, res):
        t = self.stat_text
        t.delete(1.0, tk.END)
        s = res.get("issue_summary", {})
        risk = res.get("risk", {})
        t.insert(tk.END, "=== DFM 检查中心 结果 ===\n")
        t.insert(tk.END, f"板厂/板型/等级: {res.get('factory_label')} / {res.get('board_type_label')} / "
                         f"{res.get('capability_label')}   铜厚: {res.get('copper_um')}um\n")
        t.insert(tk.END, f"已执行模块: {', '.join(res.get('modules_run', [])) or '(无)'}\n")
        t.insert(tk.END, f"请求模块: {', '.join(res.get('modules_requested', []))}\n\n")
        t.insert(tk.END, f"问题总数: {s.get('总计', 0)}   "
                         f"危险 {s.get('危险',0)} / 高 {s.get('高',0)} / 中 {s.get('中',0)} / 低 {s.get('低',0)}\n")
        t.insert(tk.END, f"可制造性评分: {risk.get('score')} / 100    风险等级: {risk.get('risk_level')}\n")
        ye = risk.get("yield_estimate", ["-", "-"])
        t.insert(tk.END, f"预估良率区间: {ye[0]}% ~ {ye[1]}%\n")
        t.insert(tk.END, f"结论: {risk.get('verdict')}\n\n")
        ns = res.get("netlist_summary")
        if ns:
            t.insert(tk.END, "--- 开短路 / 网表 ---\n")
            for k, v in ns.items():
                t.insert(tk.END, f"  {k}: {v}\n")
            t.insert(tk.END, "\n")
        fn = res.get("fabnet_summary")
        if fn:
            t.insert(tk.END, "--- CAD vs FAB 网络比对 ---\n")
            for k, v in fn.items():
                t.insert(tk.END, f"  {k}: {v}\n")
            t.insert(tk.END, "\n")
        fs = res.get("fab_stats")
        if fs:
            t.insert(tk.END, "--- 制造数据统计 ---\n")
            for k, v in fs.items():
                t.insert(tk.END, f"  {k}: {v}\n")
            t.insert(tk.END, "\n")
        pn = res.get("panel")
        if pn:
            t.insert(tk.END, "--- 拼版 ---\n")
            t.insert(tk.END, f"  建议: {pn.get('recommendation')}\n")
            t.insert(tk.END, f"  利用率: {pn.get('utilization')}\n")
            t.insert(tk.END, f"  成本: {pn.get('cost')}\n\n")
        gs = res.get("geometry_stats")
        if gs:
            t.insert(tk.END, "--- 几何统计 ---\n")
            for k, v in gs.items():
                t.insert(tk.END, f"  {k}: {v}\n")
            t.insert(tk.END, "\n")
        gm = res.get("gerber_meta")
        if gm:
            t.insert(tk.END, "--- Gerber 层识别 ---\n")
            t.insert(tk.END, f"  铜层: {gm.get('copper_layers')}\n")
            t.insert(tk.END, f"  阻焊: {gm.get('mask_layers')}   丝印: {gm.get('silk_layers')}\n")
            t.insert(tk.END, f"  外形: {gm.get('profile_layers')}   钻孔数: {gm.get('drill_count')}\n")
            t.insert(tk.END, f"  网络来源: {'X2 属性' if gm.get('has_x2_net') else '几何连通性派生'}\n")
            for nm, how in (gm.get("layer_identification") or {}).items():
                t.insert(tk.END, f"    {nm} -> {how}\n")
            t.insert(tk.END, "\n")
        for n in res.get("notes") or []:
            t.insert(tk.END, f"[提示] {n}\n")
        for k in ("brd_error", "gerber_error", "capability_error", "fab_error",
                  "fabnet_error", "panel_error", "design_params_error"):
            if res.get(k):
                t.insert(tk.END, f"[提示] {k}: {res[k]}\n")

    # ---------------- 三级: 交叉定位 / 报告 ----------------
    def _open_viewer(self):
        if not self.analysis:
            messagebox.showwarning("提示", "请先执行分析")
            return
        data = self.analysis.get("fab_data") or []
        if not data:
            messagebox.showinfo("提示", "当前结果无版图几何数据 (需提供 BRD/JSON 或制造数据)")
            return
        from src.fab_viewer import FabViewer
        FabViewer(self.win, data, self.analysis.get("issues", []))

    def _export(self):
        if not self.analysis:
            messagebox.showwarning("提示", "请先执行分析")
            return
        p = filedialog.asksaveasfilename(title="导出 DFM 报告", defaultextension=".xlsx",
                                         initialfile="dfm_hub_report.xlsx",
                                         filetypes=[("Excel", "*.xlsx"), ("All files", "*.*")])
        if not p:
            return
        try:
            from src.dfm_report import generate_dfm_excel, generate_dfm_text
            meta = self.analysis.get("board_meta") or {}
            generate_dfm_excel(self.analysis, p, meta)
            txt = os.path.splitext(p)[0] + ".txt"
            generate_dfm_text(self.analysis, txt, meta)
            js = os.path.splitext(p)[0] + ".json"
            slim = {k: v for k, v in self.analysis.items()
                    if k not in ("fab_data", "instances", "capability_items")}
            with open(js, "w", encoding="utf-8") as f:
                json.dump(slim, f, indent=2, ensure_ascii=False, default=str)
            messagebox.showinfo("导出完成", f"Excel: {p}\nText:  {txt}\nJSON:  {js}")
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("导出失败", str(exc))

    def _open_param_dfm(self):
        from gui import DFMDialog
        DFMDialog(self.win)

    def _open_brd_dfm(self):
        from src.brd_dfm_ui import BrdDFMWindow
        BrdDFMWindow(self.win)
"""
Evan_DFM: 拼版设计主界面 (选择 + 操作 + 三图联动 + 记录)
=============================================================
多级界面:
  主界面(拼版工作台)
    ├─ 顶部「操作」工具条: 生成 / 视图 / 单元编辑 / 导入 / 导出
    ├─ 左侧「选择」Notebook
    │    ① 参数选择   基本 · 单PCS · 三级工艺边与间距 · Panel/大板 · 选型依据
    │    ② 功能选项   旋转 · 连接筋 · 分板方式 · 邮票孔 · 折断边 · 交替拼 · 中裁 · 阻抗条 · Mark/定位孔
    │    ③ 模板/板材库 参数模板(增删改查) · 板材库(多板对比利用率)
    │    ④ 方案记录   草稿→确认→发布→归档 · 标记最终采用 · 操作记录
    └─ 右侧「三图」 PCS(+连接筋+工艺边) / ARRAY(+工艺边) / PANEL(工作稿下的 ARRAY)

三图联动: 滚轮缩放、右键平移; 选中单元可隐藏/放大/缩小/旋转/镜像/删除/拖动。
"""
import copy
import json
import os
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from src.panel_engine import (ARRANGEMENT_TYPES, INCH_TO_MM, MIL_TO_MM, MM_TO_MIL,
                              STACKUP_TYPES, WPNL_OPTIONS_INCH, design_panel,
                              format_dim, render_plan_svgs, rotated_corners)
from src.panel_store import SCHEME_STATUS, PanelStore, compare_materials
from src.win_dpi import apply_window

SEPARATION_TYPES = [("按开关(V-Cut/邮票孔)", ""), ("V-Cut 切割", "vcut"), ("邮票孔", "stamp"),
                    ("铣槽 routed", "routed"), ("仅折断边", "break")]
RIB_SIDES = [("四边", "all"), ("长边", "long"), ("短边", "short")]
ALT_MODES = [("不启用", "off"), ("镜像·按行", "mirror_row"), ("镜像·按列", "mirror_col"),
             ("旋转90°·按行", "rotate_row"), ("旋转90°·按列", "rotate_col")]
MIDCUT_MODES = [("不启用", "off"), ("垂直中裁", "v"), ("水平中裁", "h")]


class PanelDesigner:
    def __init__(self, master):
        self.win = tk.Toplevel(master)
        self.win.title("Evan_DFM - 拼版设计 (主界面)")
        apply_window(self.win, 1320, 820)

        self.plan = None
        self.zoom = 1.0
        self.pan = [0.0, 0.0]
        self._pans = {}
        self.board_outline = None
        self.pdf_bg = None
        self.pdf_size = None
        self.selected = None
        self._drag = None
        self._orig_items = {}
        self._last_unit = "mm"
        self.vars = {}
        self.store = PanelStore()

        self._build_toolbar()
        body = ttk.Frame(self.win)
        body.pack(fill=tk.BOTH, expand=True)
        self._build_left(body)
        self._build_right(body)

        self.status_var = tk.StringVar(value="Ready — 选择参数后点『生成排版』")
        ttk.Label(self.win, textvariable=self.status_var, relief=tk.SUNKEN,
                  anchor=tk.W).pack(fill=tk.X, side=tk.BOTTOM)
        self._reload_templates()
        self._reload_materials()
        self._reload_schemes()

    # ================= 表单小工具 =================
    def _entry(self, parent, row, label, key, default, width=10):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky=tk.W, pady=1)
        var = tk.StringVar(value=str(default))
        ttk.Entry(parent, textvariable=var, width=width).grid(row=row, column=1, sticky=tk.W, padx=4)
        self.vars[key] = var
        return row + 1

    def _combo(self, parent, row, label, key, values, default, width=13):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky=tk.W, pady=1)
        var = tk.StringVar(value=default)
        ttk.Combobox(parent, textvariable=var, values=values, state="readonly",
                     width=width).grid(row=row, column=1, sticky=tk.W, padx=4)
        self.vars[key] = var
        return row + 1

    def _check(self, parent, row, label, key, default=True):
        var = tk.BooleanVar(value=default)
        ttk.Checkbutton(parent, text=label, variable=var).grid(
            row=row, column=0, columnspan=2, sticky=tk.W)
        self.vars[key] = var
        return row + 1

    def _section(self, parent, row, text):
        ttk.Label(parent, text=text, font=("Arial", 9, "bold"),
                  foreground="#1F3864").grid(row=row, column=0, columnspan=2, sticky=tk.W, pady=(6, 1))
        return row + 1

    def _sep(self, parent, row):
        ttk.Separator(parent, orient=tk.HORIZONTAL).grid(
            row=row, column=0, columnspan=2, sticky="ew", pady=4)
        return row + 1

    def _scroll_tab(self, text):
        tab = ttk.Frame(self.nb)
        self.nb.add(tab, text=text)
        cv = tk.Canvas(tab, width=352, highlightthickness=0)
        sb = ttk.Scrollbar(tab, orient=tk.VERTICAL, command=cv.yview)
        f = ttk.Frame(cv)
        f.bind("<Configure>", lambda e: cv.configure(scrollregion=cv.bbox("all")))
        cv.create_window((0, 0), window=f, anchor=tk.NW)
        cv.configure(yscrollcommand=sb.set)
        cv.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        return f

    # ================= 顶部操作工具条 =================
    def _build_toolbar(self):
        bar = ttk.Frame(self.win, padding=(8, 6))
        bar.pack(fill=tk.X)
        ttk.Label(bar, text="操作:", font=("Arial", 9, "bold")).pack(side=tk.LEFT)
        self.run_btn = ttk.Button(bar, text="生成排版", command=self._run, width=10)
        self.run_btn.pack(side=tk.LEFT, padx=4)
        ttk.Button(bar, text="PCS示例", width=8, command=self._pcs_example).pack(side=tk.LEFT, padx=2)
        ttk.Button(bar, text="适应", width=5, command=self._fit).pack(side=tk.LEFT)
        ttk.Button(bar, text="放大", width=5, command=lambda: self._zoom_all(1.25)).pack(side=tk.LEFT, padx=2)
        ttk.Button(bar, text="缩小", width=5, command=lambda: self._zoom_all(0.8)).pack(side=tk.LEFT)
        ttk.Separator(bar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=6)
        ttk.Label(bar, text="选中单元:").pack(side=tk.LEFT)
        for text, cmd, w in (("隐藏", self._hide_sel, 5), ("放大", lambda: self._scale_sel(1.25), 5),
                             ("缩小", lambda: self._scale_sel(0.8), 5), ("旋转90", self._rotate_sel, 6),
                             ("镜像", self._mirror_sel, 5), ("删除", self._delete_sel, 5),
                             ("显示全部", self._show_all, 8), ("重置", self._reset_edit, 5)):
            ttk.Button(bar, text=text, width=w, command=cmd).pack(side=tk.LEFT, padx=1)
        ttk.Separator(bar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=6)
        ttk.Button(bar, text="导入DXF", command=self._import_dxf).pack(side=tk.LEFT)
        ttk.Button(bar, text="导入PDF", command=self._import_pdf).pack(side=tk.LEFT, padx=2)
        ttk.Button(bar, text="导出PNG", command=self._export_png).pack(side=tk.LEFT, padx=2)
        ttk.Button(bar, text="导出PDF", command=self._export_pdf).pack(side=tk.LEFT, padx=2)
        ttk.Button(bar, text="导出SVG/JSON", command=self._export).pack(side=tk.LEFT)

    # ================= 左侧「选择」 =================
    def _build_left(self, parent):
        left = ttk.Frame(parent)
        left.pack(side=tk.LEFT, fill=tk.Y)
        self.nb = ttk.Notebook(left)
        self.nb.pack(fill=tk.BOTH, expand=True, padx=(6, 2), pady=6)
        self._build_params_tab(self._scroll_tab("① 参数选择"))
        self._build_options_tab(self._scroll_tab("② 功能选项"))
        self._build_store_tab()
        self._build_records_tab()

    def _build_params_tab(self, f):
        r = 0
        r = self._section(f, r, "基本")
        r = self._combo(f, r, "料号模式", "mode", ["single", "ab", "mixed"], "single")
        r = self._combo(f, r, "排列方式", "arrangement", [t[0] for t in ARRANGEMENT_TYPES],
                        ARRANGEMENT_TYPES[0][0])
        r = self._combo(f, r, "叠板定位工艺", "stackup", [t[1] for t in STACKUP_TYPES],
                        STACKUP_TYPES[0][1])
        self.unit_var = tk.StringVar(value="mm")
        ttk.Label(f, text="尺寸单位").grid(row=r, column=0, sticky=tk.W, pady=1)
        uc = ttk.Combobox(f, textvariable=self.unit_var, values=["mm", "mil"],
                          state="readonly", width=12)
        uc.grid(row=r, column=1, sticky=tk.W, padx=4)
        uc.bind("<<ComboboxSelected>>", lambda e: self._on_unit_change())
        self.vars["units"] = self.unit_var
        r += 1
        r = self._entry(f, r, "PCS旋转角度(NA=不转)", "rotation_candidates", "NA", width=16)

        r = self._sep(f, r)
        r = self._section(f, r, "单 PCS 尺寸")
        r = self._entry(f, r, "PCS 宽 board_w", "board_w", 31.0)
        r = self._entry(f, r, "PCS 高 board_h", "board_h", 31.2)
        r = self._entry(f, r, "A 宽 board_a_w", "board_a_w", 31.0)
        r = self._entry(f, r, "A 高 board_a_h", "board_a_h", 31.2)
        r = self._entry(f, r, "B 宽 board_b_w", "board_b_w", 40.0)
        r = self._entry(f, r, "B 高 board_b_h", "board_b_h", 25.0)
        r = self._entry(f, r, "混合料号(label,w,h;...)", "mixed_text", "", width=20)

        r = self._sep(f, r)
        r = self._section(f, r, "三级工艺边 / 间距 / 连接筋")
        r = self._entry(f, r, "PCS 工艺边 pcs_rail", "pcs_rail_mm", 2.0)
        r = self._entry(f, r, "ARRAY 工艺边 array_rail", "array_rail_mm", 3.0)
        r = self._entry(f, r, "PANEL 工艺边 rail", "rail_mm", 4.0)
        r = self._entry(f, r, "PCS 间距 pcs_gap", "pcs_gap", 0.0)
        r = self._entry(f, r, "ARRAY 间距(距阻抗条)", "set_gap", 2.0)
        r = self._entry(f, r, "PCS 到工艺边间距", "pcs_to_edge", 0.0)
        r = self._entry(f, r, "PANEL 间距 wpnl_gap", "wpnl_gap", 4.0)
        r = self._entry(f, r, "ARRAY 行(空=自动)", "set_rows", "")
        r = self._entry(f, r, "ARRAY 列(空=自动)", "set_cols", "")

        r = self._sep(f, r)
        r = self._section(f, r, "Panel(工作稿) / 大板")
        self._wpnl_map = {}
        opt_labels = ["(自定义)"]
        for lbl, wi, hi in WPNL_OPTIONS_INCH:
            mm_w, mm_h = wi * INCH_TO_MM, hi * INCH_TO_MM
            disp = f"{lbl}in = {mm_w:.1f}x{mm_h:.1f}mm = {wi*1000:.0f}x{hi*1000:.0f}mil"
            opt_labels.append(disp)
            self._wpnl_map[disp] = (lbl, mm_w, mm_h)
        ttk.Label(f, text="WPNL标准(0.4in赠送不计成本)").grid(row=r, column=0, columnspan=2, sticky=tk.W)
        r += 1
        self.wpnl_option_var = tk.StringVar(value=opt_labels[0])
        cb = ttk.Combobox(f, textvariable=self.wpnl_option_var, values=opt_labels,
                          state="readonly", width=38)
        cb.grid(row=r, column=0, columnspan=2, sticky="ew", padx=2)
        cb.bind("<<ComboboxSelected>>", lambda e: self._on_wpnl_select())
        r += 1
        r = self._entry(f, r, "工作稿宽 wpnl_w", "wpnl_w", 457.2)
        r = self._entry(f, r, "工作稿高 wpnl_h", "wpnl_h", 609.6)
        r = self._entry(f, r, "凑表值(wpnl尺寸表)", "wpnl_table", "")
        r = self._entry(f, r, "大板宽 sheet_w", "sheet_w", 660.4)
        r = self._entry(f, r, "大板高 sheet_h", "sheet_h", 830.0)

        r = self._sep(f, r)
        r = self._section(f, r, "选型依据(自动建议)")
        r = self._entry(f, r, "需求数量 board_qty", "board_qty", "")
        r = self._entry(f, r, "板层数 layer_count", "layer_count", "")
        r = self._check(f, r, "HDI/高精度叠板", "hdi", False)
        r = self._check(f, r, "异形/不规则板", "irregular", False)

    def _build_options_tab(self, f):
        r = 0
        r = self._section(f, r, "旋转 / 单元")
        r = self._check(f, r, "允许旋转(0-360°)", "allow_rotate", False)
        r = self._check(f, r, "单PCS+工艺边(每稿1PCS)", "panel_style_single", False)

        r = self._sep(f, r)
        r = self._section(f, r, "连接筋 (PCS ↔ 工艺边)")
        r = self._entry(f, r, "连接筋宽度 mm", "edge_rib_mm", 2.0)
        r = self._entry(f, r, "每边连接筋数量", "rail_rib_count", 3)
        r = self._combo(f, r, "连接筋位置", "rib_sides", [t[0] for t in RIB_SIDES], RIB_SIDES[0][0])
        r = self._check(f, r, "单元间连接筋(ribs)", "ribs_enabled", False)
        r = self._entry(f, r, "单元连接筋 宽/数量", "ribs_cfg", "1.0,1", width=14)

        r = self._sep(f, r)
        r = self._section(f, r, "分板方式")
        r = self._combo(f, r, "分板方式", "separation", [t[0] for t in SEPARATION_TYPES],
                        SEPARATION_TYPES[0][0])
        r = self._check(f, r, "V-Cut 切割线", "vcut", True)
        r = self._check(f, r, "邮票孔", "stamp_holes", False)
        r = self._entry(f, r, "邮票孔间距", "stamp_pitch", 2.0)
        r = self._entry(f, r, "邮票孔孔径", "stamp_diameter_mm", 0.5)
        r = self._check(f, r, "折断边", "break_off", True)
        r = self._entry(f, r, "折断边宽度", "break_off_mm", 3.0)

        r = self._sep(f, r)
        r = self._section(f, r, "交替拼 / 中裁")
        r = self._combo(f, r, "交替拼", "alternate", [t[0] for t in ALT_MODES], ALT_MODES[0][0])
        r = self._combo(f, r, "中裁", "midcut", [t[0] for t in MIDCUT_MODES], MIDCUT_MODES[0][0])
        r = self._entry(f, r, "中裁槽宽 mm", "midcut_width", 2.0)
        r = self._entry(f, r, "中裁数量", "midcut_count", 1)

        r = self._sep(f, r)
        r = self._section(f, r, "阻抗条 / 铜箔 / Mark")
        r = self._check(f, r, "阻抗条", "impedance_enabled", False)
        r = self._entry(f, r, "阻抗条 数量/长/宽/间距", "impedance_cfg", "2,20,3,5", width=16)
        r = self._check(f, r, "铜箔开料核算", "copper_foil", False)
        r = self._entry(f, r, "Mark 点数", "mark_count", 3)
        r = self._entry(f, r, "定位孔数", "tooling_count", 2)

    # ---------- 模板 / 板材库 ----------
    def _build_store_tab(self):
        tab = ttk.Frame(self.nb)
        self.nb.add(tab, text="③ 模板/板材库")
        box = ttk.Frame(tab, padding=6)
        box.pack(fill=tk.BOTH, expand=True)

        g1 = ttk.LabelFrame(box, text="参数模板 (增删改查)", padding=6)
        g1.pack(fill=tk.X)
        row = ttk.Frame(g1)
        row.pack(fill=tk.X)
        ttk.Label(row, text="名称:").pack(side=tk.LEFT)
        self.tpl_name = tk.StringVar()
        ttk.Entry(row, textvariable=self.tpl_name, width=18).pack(side=tk.LEFT, padx=4)
        self.tpl_list = tk.Listbox(g1, height=6, exportselection=False)
        self.tpl_list.pack(fill=tk.X, pady=4)
        b = ttk.Frame(g1)
        b.pack(fill=tk.X)
        ttk.Button(b, text="保存当前", command=self._tpl_save).pack(side=tk.LEFT)
        ttk.Button(b, text="加载", command=self._tpl_load).pack(side=tk.LEFT, padx=4)
        ttk.Button(b, text="删除", command=self._tpl_delete).pack(side=tk.LEFT)
        ttk.Label(g1, text=f"存储: {self.store.path}", foreground="gray",
                  wraplength=320).pack(anchor=tk.W, pady=(4, 0))

        g2 = ttk.LabelFrame(box, text="板材库 (勾选后分别计算并对比利用率)", padding=6)
        g2.pack(fill=tk.BOTH, expand=True, pady=(8, 0))
        cols = ("use", "brand", "model", "w", "h", "thk", "cu")
        self.mat_tree = ttk.Treeview(g2, columns=cols, show="headings", height=6)
        for c, t, w in (("use", "用", 34), ("brand", "品牌", 66), ("model", "型号", 84),
                        ("w", "宽mm", 56), ("h", "长mm", 56), ("thk", "厚mm", 46), ("cu", "铜厚", 46)):
            self.mat_tree.heading(c, text=t)
            self.mat_tree.column(c, width=w, anchor=tk.CENTER)
        self.mat_tree.pack(fill=tk.X)
        self.mat_tree.bind("<Button-1>", self._mat_toggle)
        fr = ttk.Frame(g2)
        fr.pack(fill=tk.X, pady=4)
        self.mat_fields = {}
        for i, (k, lab, dflt) in enumerate((("brand", "品牌", ""), ("model", "型号", ""),
                                            ("w", "宽", "457.2"), ("h", "长", "609.6"),
                                            ("thk", "厚", "1.6"), ("cu", "铜厚", "1oz"))):
            ttk.Label(fr, text=lab).grid(row=0, column=i, sticky=tk.W)
            v = tk.StringVar(value=dflt)
            ttk.Entry(fr, textvariable=v, width=7).grid(row=1, column=i, sticky=tk.W, padx=1)
            self.mat_fields[k] = v
        b2 = ttk.Frame(g2)
        b2.pack(fill=tk.X)
        ttk.Button(b2, text="添加", command=self._mat_add).pack(side=tk.LEFT)
        ttk.Button(b2, text="修改", command=self._mat_update).pack(side=tk.LEFT, padx=4)
        ttk.Button(b2, text="删除", command=self._mat_delete).pack(side=tk.LEFT)
        ttk.Button(b2, text="按板材库对比", command=self._mat_compare).pack(side=tk.LEFT, padx=8)
        self.mat_status = tk.StringVar(value="")
        ttk.Label(g2, textvariable=self.mat_status, foreground="#333",
                  wraplength=330, justify=tk.LEFT).pack(anchor=tk.W, pady=(4, 0))

    # ---------- 方案记录 ----------
    def _build_records_tab(self):
        tab = ttk.Frame(self.nb)
        self.nb.add(tab, text="④ 方案记录")
        box = ttk.Frame(tab, padding=6)
        box.pack(fill=tk.BOTH, expand=True)

        g1 = ttk.LabelFrame(box, text="方案管理 (草稿→确认→发布→归档)", padding=6)
        g1.pack(fill=tk.X)
        cols = ("name", "status", "util", "cost", "final", "updated")
        self.sch_tree = ttk.Treeview(g1, columns=cols, show="headings", height=8)
        for c, t, w in (("name", "方案名", 96), ("status", "状态", 46), ("util", "利用率", 56),
                        ("cost", "成本", 46), ("final", "采用", 40), ("updated", "更新时间", 108)):
            self.sch_tree.heading(c, text=t)
            self.sch_tree.column(c, width=w, anchor=tk.CENTER)
        self.sch_tree.pack(fill=tk.X)
        fr = ttk.Frame(g1)
        fr.pack(fill=tk.X, pady=4)
        ttk.Label(fr, text="名称:").pack(side=tk.LEFT)
        self.sch_name = tk.StringVar()
        ttk.Entry(fr, textvariable=self.sch_name, width=14).pack(side=tk.LEFT, padx=2)
        ttk.Label(fr, text="状态:").pack(side=tk.LEFT)
        self.sch_status = tk.StringVar(value=SCHEME_STATUS[0])
        ttk.Combobox(fr, textvariable=self.sch_status, values=SCHEME_STATUS,
                     state="readonly", width=6).pack(side=tk.LEFT, padx=2)
        b = ttk.Frame(g1)
        b.pack(fill=tk.X)
        ttk.Button(b, text="保存方案", command=self._sch_save).pack(side=tk.LEFT)
        ttk.Button(b, text="加载", command=self._sch_load).pack(side=tk.LEFT, padx=2)
        ttk.Button(b, text="改状态", command=self._sch_status_set).pack(side=tk.LEFT, padx=2)
        ttk.Button(b, text="标记采用", command=self._sch_final).pack(side=tk.LEFT, padx=2)
        ttk.Button(b, text="删除", command=self._sch_delete).pack(side=tk.LEFT)

        g2 = ttk.LabelFrame(box, text="操作记录", padding=6)
        g2.pack(fill=tk.BOTH, expand=True, pady=(8, 0))
        self.his_tree = ttk.Treeview(g2, columns=("time", "action", "detail"),
                                     show="headings", height=10)
        for c, t, w in (("time", "时间", 116), ("action", "动作", 70), ("detail", "详情", 150)):
            self.his_tree.heading(c, text=t)
            self.his_tree.column(c, width=w, anchor=tk.W)
        self.his_tree.pack(fill=tk.BOTH, expand=True)
        ttk.Button(g2, text="刷新记录", command=self._reload_history).pack(anchor=tk.E, pady=(4, 0))

    # ================= 右侧三图 =================
    def _build_right(self, parent):
        right = ttk.Frame(parent)
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        bar = ttk.Frame(right)
        bar.pack(fill=tk.X, padx=6, pady=(6, 0))
        ttk.Label(bar, text="三图联动 (滚轮缩放 / 右键平移 / 左键选中并拖动单元):",
                  foreground="#555").pack(side=tk.LEFT)
        self.stat_var = tk.StringVar(value="等待生成")
        ttk.Label(bar, textvariable=self.stat_var, foreground="#333").pack(side=tk.RIGHT)

        info = ttk.Frame(right)
        info.pack(fill=tk.X, padx=6, pady=(2, 0))
        ttk.Label(info, text="PCS 信息:", font=("Arial", 9, "bold")).pack(side=tk.LEFT)
        self.pcs_info_var = tk.StringVar(value="—")
        ttk.Label(info, textvariable=self.pcs_info_var, foreground="#1F3864").pack(side=tk.LEFT, padx=6)

        grid = ttk.Frame(right)
        grid.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)
        self.canvases = {}
        titles = [("pcs", "① PCS 图\n(单PCS + 连接筋 + 工艺边)"),
                  ("array", "② ARRAY 图\n(PCS 阵列 + 工艺边)"),
                  ("panel", "③ PANEL 图\n(工作稿下的 ARRAY)")]
        for i, (key, t) in enumerate(titles):
            card = ttk.LabelFrame(grid, text=t.replace("\n", " "), padding=4)
            card.grid(row=0, column=i, sticky="nsew", padx=4, pady=4)
            cv = tk.Canvas(card, width=330, height=620, bg="#fbfbfb", highlightthickness=1,
                           highlightbackground="#c4c7ce")
            cv.pack(fill=tk.BOTH, expand=True)
            cv.bind("<MouseWheel>", lambda e: self._zoom_all(1.2 if e.delta > 0 else 0.8))
            cv.bind("<ButtonPress-1>", lambda e, k=key: self._on_press(e, k))
            cv.bind("<B1-Motion>", lambda e, k=key: self._on_motion(e, k))
            cv.bind("<ButtonRelease-1>", lambda e, k=key: self._on_release(e, k))
            cv.bind("<ButtonPress-3>", lambda e, k=key: self._pan_start(e, k))
            cv.bind("<B3-Motion>", lambda e, k=key: self._pan_move(e, k))
            cv.bind("<Configure>", lambda e: self._redraw())
            self.canvases[key] = cv
            grid.columnconfigure(i, weight=1)
        grid.rowconfigure(0, weight=1)

    # ================= WPNL / 单位 =================
    def _on_wpnl_select(self):
        disp = self.wpnl_option_var.get()
        if disp in getattr(self, "_wpnl_map", {}):
            _lbl, mm_w, mm_h = self._wpnl_map[disp]
            if self.unit_var.get() == "mil":
                self.vars["wpnl_w"].set(f"{mm_w * MM_TO_MIL:.1f}")
                self.vars["wpnl_h"].set(f"{mm_h * MM_TO_MIL:.1f}")
            else:
                self.vars["wpnl_w"].set(f"{mm_w:.3f}")
                self.vars["wpnl_h"].set(f"{mm_h:.3f}")

    _LEN_KEYS = ("board_w", "board_h", "board_a_w", "board_a_h", "board_b_w", "board_b_h",
                 "pcs_gap", "set_gap", "rail_mm", "array_rail_mm", "pcs_rail_mm",
                 "pcs_to_edge", "wpnl_w", "wpnl_h", "wpnl_gap", "sheet_w", "sheet_h",
                 "break_off_mm", "stamp_pitch", "stamp_diameter_mm", "edge_rib_mm",
                 "midcut_width")

    def _on_unit_change(self):
        new = self.unit_var.get()
        if new == self._last_unit:
            return
        factor = MM_TO_MIL if new == "mil" else MIL_TO_MM
        for k in self._LEN_KEYS:
            v = self.vars[k].get().strip()
            if not v:
                continue
            try:
                self.vars[k].set(f"{float(v) * factor:.3f}")
            except ValueError:
                pass
        self._last_unit = new
        if self.plan:
            try:
                self._run()
            except Exception:  # noqa: BLE001
                pass

    # ================= 参数收集 =================
    def _collect(self):
        def fnum(key, default=None):
            txt = self.vars[key].get().strip()
            return float(txt) if txt else default

        def pick(pairs, label):
            return next((v for k, v in pairs if k == label), pairs[0][1])

        p = {
            "mode": self.vars["mode"].get(),
            "arrangement": next((t[1] for t in ARRANGEMENT_TYPES
                                 if t[0] == self.vars["arrangement"].get()), "forward"),
            "stackup_method": next((t[0] for t in STACKUP_TYPES
                                    if t[1] == self.vars["stackup"].get()), "auto"),
            "panel_style": "single_pcs" if self.vars["panel_style_single"].get() else "array",
            "hdi": self.vars["hdi"].get(),
            "irregular_board": self.vars["irregular"].get(),
            "board_qty": fnum("board_qty"),
            "layer_count": fnum("layer_count"),
            "units": self.vars["units"].get(),
            "board_w": fnum("board_w", 31.0), "board_h": fnum("board_h", 31.2),
            "board_outline": self.board_outline,
            "board_a_w": fnum("board_a_w"), "board_a_h": fnum("board_a_h"),
            "board_b_w": fnum("board_b_w"), "board_b_h": fnum("board_b_h"),
            "pcs_gap": fnum("pcs_gap", 0.0), "set_gap": fnum("set_gap", 2.0),
            "rail_mm": fnum("rail_mm", 4.0),
            "array_rail_mm": fnum("array_rail_mm"),
            "pcs_rail_mm": fnum("pcs_rail_mm"),
            "pcs_to_edge": fnum("pcs_to_edge", 0.0),
            "edge_rib_mm": fnum("edge_rib_mm", 2.0),
            "rail_rib_count": int(fnum("rail_rib_count", 3)),
            "rib_sides": next((v for k, v in RIB_SIDES if k == self.vars["rib_sides"].get()), "all"),
            "wpnl_gap": fnum("wpnl_gap", 4.0),
            "set_rows": int(fnum("set_rows")) if self.vars["set_rows"].get().strip() else None,
            "set_cols": int(fnum("set_cols")) if self.vars["set_cols"].get().strip() else None,
            "wpnl_w": fnum("wpnl_w", 457.2), "wpnl_h": fnum("wpnl_h", 609.6),
            "sheet_w": fnum("sheet_w", 660.4), "sheet_h": fnum("sheet_h", 830.0),
            "allow_rotate": self.vars["allow_rotate"].get(),
            "separation": next((v for k, v in SEPARATION_TYPES
                                if k == self.vars["separation"].get()), ""),
            "vcut": self.vars["vcut"].get(), "stamp_holes": self.vars["stamp_holes"].get(),
            "stamp_pitch": fnum("stamp_pitch", 2.0),
            "stamp_diameter_mm": fnum("stamp_diameter_mm", 0.5),
            "break_off": self.vars["break_off"].get(), "break_off_mm": fnum("break_off_mm", 3.0),
            "mark_count": int(fnum("mark_count", 3)), "tooling_count": int(fnum("tooling_count", 2)),
            "copper_foil": {"enabled": self.vars["copper_foil"].get()},
        }

        rot = self.vars["rotation_candidates"].get().strip()
        angles = []
        if rot.upper() not in ("", "NA", "N/A", "NONE", "不旋转"):
            for x in rot.replace("，", ",").split(","):
                x = x.strip()
                if x:
                    try:
                        angles.append(float(x) % 360)
                    except ValueError:
                        pass
        p["rotation_candidates"] = angles or None

        rconf = self.vars["ribs_cfg"].get().strip()
        rw, rc = 1.0, 1
        if rconf:
            parts = [x.strip() for x in rconf.replace("，", ",").split(",")]
            if parts and parts[0]:
                rw = float(parts[0])
            if len(parts) >= 2 and parts[1]:
                rc = int(float(parts[1]))
        p["ribs"] = {"enabled": self.vars["ribs_enabled"].get(), "width": rw, "count": rc}

        alt = next((v for k, v in ALT_MODES if k == self.vars["alternate"].get()), "off")
        if alt == "off":
            p["alternate"] = {"enabled": False}
        else:
            mode, by = alt.split("_")
            p["alternate"] = {"enabled": True, "mode": mode, "by": by}

        mcd = next((v for k, v in MIDCUT_MODES if k == self.vars["midcut"].get()), "off")
        p["mid_cut"] = {"enabled": mcd != "off", "direction": mcd if mcd != "off" else "v",
                        "width": fnum("midcut_width", 2.0), "count": int(fnum("midcut_count", 1))}

        table = self.vars["wpnl_table"].get().strip()
        sizes = []
        if table:
            for part in table.replace("；", ";").split(";"):
                part = part.strip()
                if not part:
                    continue
                sep = "x" if "x" in part else ("X" if "X" in part else ("*" if "*" in part else None))
                if sep:
                    a, b = part.split(sep)[:2]
                    sizes.append([float(a), float(b)])
                else:
                    sizes.append([float(part), float(part)])
        if sizes:
            p["wpnl_sizes"] = sizes
            p["wpnl_w"], p["wpnl_h"] = sizes[0]

        imp = self.vars["impedance_enabled"].get()
        cfg = self.vars["impedance_cfg"].get().strip()
        count, ilen, iwid, igap = 2, 20.0, 3.0, 5.0
        if cfg:
            parts = [x.strip() for x in cfg.replace("，", ",").split(",")]
            if len(parts) >= 1 and parts[0]:
                count = int(float(parts[0]))
            if len(parts) >= 2 and parts[1]:
                ilen = float(parts[1])
            if len(parts) >= 3 and parts[2]:
                iwid = float(parts[2])
            if len(parts) >= 4 and parts[3]:
                igap = float(parts[3])
        p["impedance"] = {"enabled": imp, "count": count, "length_mm": ilen,
                          "width_mm": iwid, "gap_mm": igap}

        mixed = []
        mt = self.vars["mixed_text"].get().strip()
        if mt:
            for part in mt.replace("；", ";").split(";"):
                part = part.strip()
                if not part:
                    continue
                bits = [x.strip() for x in part.split(",")]
                if len(bits) >= 3:
                    mixed.append({"label": bits[0], "w": float(bits[1]), "h": float(bits[2])})
        p["mixed_boards"] = mixed

        disp = self.wpnl_option_var.get()
        if disp in getattr(self, "_wpnl_map", {}):
            lbl, mm_w, mm_h = self._wpnl_map[disp]
            p["wp_nl_option"] = lbl
            p["wpnl_w"], p["wpnl_h"] = mm_w, mm_h
            p.pop("wpnl_sizes", None)

        if p.get("units") == "mil":
            keys = ["board_w", "board_h", "board_a_w", "board_a_h", "board_b_w", "board_b_h",
                    "pcs_gap", "set_gap", "rail_mm", "array_rail_mm", "pcs_rail_mm",
                    "pcs_to_edge", "wpnl_gap", "sheet_w", "sheet_h", "break_off_mm",
                    "stamp_pitch", "stamp_diameter_mm", "edge_rib_mm", "midcut_width"]
            if not p.get("wp_nl_option"):
                keys += ["wpnl_w", "wpnl_h"]
            for k in keys:
                if p.get(k) is not None:
                    p[k] = round(p[k] * MIL_TO_MM, 4)
            if p.get("wpnl_sizes"):
                p["wpnl_sizes"] = [[round(a * MIL_TO_MM, 4), round(b * MIL_TO_MM, 4)]
                                   for a, b in p["wpnl_sizes"]]
            p["mixed_boards"] = [{"label": b["label"], "w": round(b["w"] * MIL_TO_MM, 4),
                                  "h": round(b["h"] * MIL_TO_MM, 4)} for b in p["mixed_boards"]]
            impd = p.get("impedance") or {}
            if impd.get("enabled"):
                for k in ("length_mm", "width_mm", "gap_mm"):
                    if impd.get(k) is not None:
                        impd[k] = round(impd[k] * MIL_TO_MM, 4)
            p["mid_cut"]["width"] = round(p["mid_cut"]["width"] * MIL_TO_MM, 4)
        return p

    # ================= 生成 / 绘制 =================
    def _run(self):
        try:
            params = self._collect()
        except ValueError as exc:
            messagebox.showwarning("参数错误", f"数值解析失败: {exc}")
            return
        self.last_params = params
        self.plan = design_panel(params)
        if not self.plan.get("ok"):
            self.stat_var.set(self.plan.get("error", "失败"))
            for cv in self.canvases.values():
                cv.delete("all")
            for it in self.plan.get("issues", []):
                messagebox.showwarning("排版提示", it["description"])
            return
        self.selected = None
        self._orig_items = {k: copy.deepcopy(v.get("items", []))
                            for k, v in self.plan["views"].items()}
        u = self.plan["utilization"]
        pnl = self.plan.get("panel", {})
        pw_mm = pnl.get("full_mm", [None, None])
        pw_mil = pnl.get("full_mil", [None, None])
        rec = self.plan.get("layout_recommendation", {})
        sk = self.plan.get("stackup_recommendation", {})
        rails = (self.plan.get("features") or {}).get("rails", {})
        self.stat_var.set(
            f"Panel {pw_mm[0]}x{pw_mm[1]}mm = {pw_mil[0]}x{pw_mil[1]}mil | "
            f"工艺边 PCS/ARRAY/PANEL = {rails.get('pcs')}/{rails.get('array')}/{rails.get('panel')}mm | "
            f"利用率 {u.get('panel', u.get('wpnl', 0))*100:.1f}% "
            f"(计成本 {u.get('panel_charged', 0)*100:.1f}%) | 成本指数 {self.plan['cost']['cost_index']} | "
            f"建议: {rec.get('label','')} ; {sk.get('label','')}")
        self.store.log("生成排版", f"{self.plan.get('mode')}/{self.plan.get('arrangement')} "
                                  f"利用率 {u.get('panel', 0)}")
        self._reload_history()
        self._update_pcs_info()
        self._fit()

    def _update_pcs_info(self):
        """显示单电路板(PCS)尺寸信息, 单位随 mm/mil 切换"""
        try:
            from src.panel_engine import pcs_summary
            s = pcs_summary(self.plan)
        except Exception:  # noqa: BLE001
            s = {}
        if not s or s.get("board_w") is None:
            self.pcs_info_var.set("—")
            return
        unit = s.get("unit", "mm")
        bw, bh = format_dim(s.get("board_w"), unit), format_dim(s.get("board_h"), unit)
        ow, oh = format_dim(s.get("outer_w"), unit), format_dim(s.get("outer_h"), unit)
        sides = {"all": "四边", "long": "长边", "short": "短边"}.get(s.get("rib_sides"), s.get("rib_sides"))
        txt = (f"单板 {bw} × {bh}   |   含工艺边 {ow} × {oh}   |   "
               f"工艺边 {format_dim(s.get('rail_mm'), unit)}   |   "
               f"连接筋 {format_dim(s.get('rib_mm'), unit)} × "
               f"{s.get('rib_count')}/{sides}   |   单板面积 {s.get('board_area_cm2')}cm²")
        if s.get("outline_points"):
            txt += f"   |   外形轮廓点 {s['outline_points']}"
        self.pcs_info_var.set(txt)

    def _pcs_example(self):
        """PCS 示例: 一键载入单电路板示例 (矩形 / 圆角 / L形 / 圆形)"""
        from src.panel_engine import PCS_EXAMPLES, make_pcs_example
        dlg = tk.Toplevel(self.win)
        dlg.title("PCS 示例 (单电路板)")
        dlg.geometry("340x240")
        dlg.transient(self.win)
        ttk.Label(dlg, text="选择一个 PCS 示例, 应用后自动重算三图:").pack(
            anchor=tk.W, padx=10, pady=(10, 4))
        lb = tk.Listbox(dlg, height=6, exportselection=False)
        for k in PCS_EXAMPLES:
            lb.insert(tk.END, k)
        lb.pack(fill=tk.BOTH, expand=True, padx=10)
        lb.selection_set(0)

        def apply():
            sel = lb.curselection()
            if not sel:
                return
            kind = PCS_EXAMPLES[sel[0]]
            ex = make_pcs_example(kind)
            self.board_outline = ex.get("board_outline")
            f = MM_TO_MIL if self.unit_var.get() == "mil" else 1.0
            self.vars["board_w"].set(f"{ex['board_w'] * f:.3f}")
            self.vars["board_h"].set(f"{ex['board_h'] * f:.3f}")
            self.store.log("PCS示例", kind)
            self._reload_history()
            dlg.destroy()
            self._run()

        def clear():
            self.board_outline = None
            self.store.log("PCS示例", "清除外形(恢复矩形)")
            self._reload_history()
            dlg.destroy()
            self._run()

        bf = ttk.Frame(dlg)
        bf.pack(pady=10)
        ttk.Button(bf, text="应用示例", command=apply).pack(side=tk.LEFT, padx=6)
        ttk.Button(bf, text="清除外形", command=clear).pack(side=tk.LEFT, padx=6)
        ttk.Button(bf, text="取消", command=dlg.destroy).pack(side=tk.LEFT, padx=6)

    def _fit(self):
        self.zoom = 1.0
        self.pan = [0.0, 0.0]
        self._redraw()

    def _zoom_all(self, factor):
        self.zoom *= factor
        self._redraw()

    def _pan_start(self, event, key):
        self._pans[key] = (event.x, event.y, self.pan[0], self.pan[1])

    def _pan_move(self, event, key):
        if key not in self._pans:
            return
        ax, ay, ox, oy = self._pans[key]
        self.pan = [ox + (event.x - ax), oy + (event.y - ay)]
        self._redraw()

    def _redraw(self):
        if not self.plan or not self.plan.get("ok"):
            return
        for key, cv in self.canvases.items():
            self._draw_view(key, cv, self.plan["views"][key])

    def _draw_view(self, key, cv, view):
        cv.delete("all")
        cw, ch = view["container"]
        W = cv.winfo_width() or 330
        H = cv.winfo_height() or 620
        base = min(W / max(cw, 1e-6), H / max(ch, 1e-6)) * 0.88
        s = base * self.zoom
        ox = (W - cw * s) / 2 + self.pan[0]
        oy = (H - ch * s) / 2 + self.pan[1]

        def tx(x):
            return ox + x * s

        def ty(y):
            return oy + (ch - y) * s

        step = 10.0
        gx = 0.0
        while gx <= cw + 1e-6:
            cv.create_line(tx(gx), ty(ch), tx(gx), ty(0), fill="#eaeaea")
            gx += step
        gy = 0.0
        while gy <= ch + 1e-6:
            cv.create_line(tx(0), ty(gy), tx(cw), ty(gy), fill="#eaeaea")
            gy += step
        cv.create_rectangle(tx(0), ty(ch), tx(cw), ty(0), outline="#333", fill="")

        for f in view.get("features", []):
            t = f.get("type")
            if t == "vcut":
                cv.create_line(tx(f["x1"]), ty(f["y1"]), tx(f["x2"]), ty(f["y2"]),
                               fill="#c62828", dash=() if f.get("solid") else (5, 3))
            elif t == "stamp":
                rr = max(1.0, (f.get("d") or 0.5) / 2 * s)
                cv.create_oval(tx(f["x"]) - rr, ty(f["y"]) - rr, tx(f["x"]) + rr, ty(f["y"]) + rr,
                               fill="#555", outline="")
            elif t == "midcut":
                cv.create_line(tx(f["x1"]), ty(f["y1"]), tx(f["x2"]), ty(f["y2"]),
                               fill="#00695c", width=max(1, int((f.get("w") or 2.0) * s)), dash=(8, 4))
            elif t == "impedance":
                cv.create_rectangle(tx(f["x"] - f["w"] / 2), ty(f["y"] + f["h"]),
                                    tx(f["x"] + f["w"] / 2), ty(f["y"]),
                                    fill="#7e57c2", outline="", stipple="gray50")
            elif t == "break_off":
                cv.create_rectangle(tx(0), ty(f["h"]), tx(f["w"]), ty(0), outline="#0a7a3a", width=2)
            elif t == "mark":
                cv.create_oval(tx(f["x"]) - 3, ty(f["y"]) - 3, tx(f["x"]) + 3, ty(f["y"]) + 3,
                               fill="#ffd966", outline="#bf8f00")
            elif t == "tooling":
                cv.create_oval(tx(f["x"]) - 2.5, ty(f["y"]) - 2.5, tx(f["x"]) + 2.5, ty(f["y"]) + 2.5,
                               fill="#8ea9db", outline="#2f5496")
            elif t == "rib":
                cv.create_rectangle(tx(f["x"]), ty(f["y"] + f["h"]), tx(f["x"] + f["w"]), ty(f["y"]),
                                    fill="#0a7a3a", outline="")
            elif t == "rail":
                cv.create_rectangle(tx(f["x"]), ty(f["y"] + f["h"]), tx(f["x"] + f["w"]), ty(f["y"]),
                                    fill="#bdbdbd", outline="")
            elif t == "direction":
                cv.create_polygon(tx(f["x"]), ty(f["y"]), tx(f["x"] + 3), ty(f["y"]),
                                  tx(f["x"]), ty(f["y"] + 3), fill="#c00000", outline="")
            elif t == "leftover":
                cv.create_rectangle(tx(f["x"]), ty(f["y"] + f["h"]), tx(f["x"] + f["w"]), ty(f["y"]),
                                    fill="#d9d9d9", outline="#999", dash=(4, 3), stipple="gray50")
            elif t == "dim":
                x1, y1, x2, y2 = tx(f["x1"]), ty(f["y1"]), tx(f["x2"]), ty(f["y2"])
                txt = format_dim(f.get("value"), view.get("unit", "mm"))
                if f.get("label"):
                    txt = f"{f['label']} {txt}"
                if f.get("orient") == "v":
                    xx = x1 - 16
                    cv.create_line(xx, y1, xx, y2, fill="#c00")
                    cv.create_line(xx - 4, y1, xx + 4, y1, fill="#c00")
                    cv.create_line(xx - 4, y2, xx + 4, y2, fill="#c00")
                    cv.create_text(xx - 4, (y1 + y2) / 2, text=txt, fill="#c00",
                                   angle=90, font=("Arial", 8))
                else:
                    yy = y1 + 16
                    cv.create_line(x1, yy, x2, yy, fill="#c00")
                    cv.create_line(x1, yy - 4, x1, yy + 4, fill="#c00")
                    cv.create_line(x2, yy - 4, x2, yy + 4, fill="#c00")
                    cv.create_text((x1 + x2) / 2, yy + 12, text=txt, fill="#c00", font=("Arial", 8))

        for idx, it in enumerate(view.get("items", [])):
            if it.get("hidden"):
                continue
            x, y, w, h = it["x"], it["y"], it["w"], it["h"]
            color = it.get("color", "#2f7ed8")
            angle = it.get("angle", 0.0) or 0.0
            ow = it.get("orig_w") or w
            oh = it.get("orig_h") or h
            poly = it.get("poly")
            frame = it.get("frame")
            outline = "#000000" if frame else color
            fill = "" if frame else color
            stipple = "" if frame else "gray25"
            if poly:
                pts = []
                for px, py in poly:
                    pts.extend([tx(px), ty(py)])
                cv.create_polygon(pts, fill=fill, outline=outline, stipple=stipple, width=1)
            elif abs(angle % 360) > 1e-6:
                cx, cy = x + w / 2, y + h / 2
                pts = []
                for px, py in rotated_corners(cx, cy, ow, oh, angle):
                    pts.extend([tx(px), ty(py)])
                cv.create_polygon(pts, fill=fill, outline=outline, stipple=stipple, width=1)
            else:
                cv.create_rectangle(tx(x), ty(y + h), tx(x + w), ty(y),
                                    fill=fill, outline=outline, stipple=stipple, width=1)
            label = it.get("label", "")
            if label and w * s > 12 and abs(angle % 360) < 1e-6 and not poly:
                cv.create_text(tx(x + w / 2), ty(y + h / 2), text=label, fill=outline,
                               font=("Arial", 8))
            if it.get("mirror"):
                cv.create_line(tx(x), ty(y), tx(x + w), ty(y + h), fill="#c00000", width=2)
            if self.selected == (key, idx):
                cv.create_rectangle(tx(x) - 2, ty(y + h) - 2, tx(x + w) + 2, ty(y) + 2,
                                    outline="#ff9800", width=2, dash=(4, 2))

    # ================= 单元编辑 =================
    def _view_scale(self, key):
        cv = self.canvases[key]
        view = self.plan["views"][key]
        cw, ch = view["container"]
        W = cv.winfo_width() or 330
        H = cv.winfo_height() or 620
        base = min(W / max(cw, 1e-6), H / max(ch, 1e-6)) * 0.88
        return base * self.zoom, W, H, cw, ch

    def _canvas_to_mm(self, key, px, py):
        s, W, H, cw, ch = self._view_scale(key)
        ox = (W - cw * s) / 2 + self.pan[0]
        oy = (H - ch * s) / 2 + self.pan[1]
        return (px - ox) / s, ch - (py - oy) / s

    def _hit_item(self, key, px, py):
        mx, my = self._canvas_to_mm(key, px, py)
        items = self.plan["views"][key]["items"]
        for i in range(len(items) - 1, -1, -1):
            it = items[i]
            if it.get("hidden"):
                continue
            if it["x"] <= mx <= it["x"] + it["w"] and it["y"] <= my <= it["y"] + it["h"]:
                return i
        return None

    def _on_press(self, event, key):
        idx = self._hit_item(key, event.x, event.y)
        mx, my = self._canvas_to_mm(key, event.x, event.y)
        if idx is not None:
            self.selected = (key, idx)
            it = self.plan["views"][key]["items"][idx]
            self._drag = {"mode": "item", "key": key, "idx": idx, "mx": mx, "my": my,
                          "ox": it["x"], "oy": it["y"],
                          "poly": [list(q) for q in it.get("poly", [])]}
            self._redraw()
        else:
            self.selected = None
            self._drag = {"mode": "pan", "key": key, "px": event.x, "py": event.y,
                          "ox": self.pan[0], "oy": self.pan[1]}
            self._redraw()

    def _on_motion(self, event, key):
        if not self._drag:
            return
        if self._drag["mode"] == "item":
            mx, my = self._canvas_to_mm(key, event.x, event.y)
            it = self.plan["views"][key]["items"][self._drag["idx"]]
            dx = mx - self._drag["mx"]
            dy = my - self._drag["my"]
            it["x"] = round(self._drag["ox"] + dx, 3)
            it["y"] = round(self._drag["oy"] + dy, 3)
            if self._drag["poly"]:
                it["poly"] = [(round(px + dx, 3), round(py + dy, 3)) for px, py in self._drag["poly"]]
            self._redraw()
        else:
            self.pan = [self._drag["ox"] + (event.x - self._drag["px"]),
                        self._drag["oy"] + (event.y - self._drag["py"])]
            self._redraw()

    def _on_release(self, event, key):
        self._drag = None

    def _scale_poly(self, it, factor):
        if it.get("poly"):
            cx, cy = it["x"] + it["w"] / 2, it["y"] + it["h"] / 2
            it["poly"] = [(round(cx + (px - cx) * factor, 3), round(cy + (py - cy) * factor, 3))
                          for px, py in it["poly"]]

    def _with_sel(self):
        if not self.selected:
            messagebox.showinfo("提示", "请先在图上点选一个 PCS / ARRAY 单元")
            return None
        key, idx = self.selected
        items = self.plan["views"][key]["items"]
        if idx >= len(items):
            self.selected = None
            return None
        return key, idx, items[idx]

    def _hide_sel(self):
        r = self._with_sel()
        if r:
            r[2]["hidden"] = True
            self._redraw()

    def _show_all(self):
        for v in self.plan["views"].values():
            for it in v.get("items", []):
                it.pop("hidden", None)
        self._redraw()

    def _scale_sel(self, factor):
        r = self._with_sel()
        if r:
            it = r[2]
            cx, cy = it["x"] + it["w"] / 2, it["y"] + it["h"] / 2
            nw, nh = it["w"] * factor, it["h"] * factor
            it["x"], it["y"] = cx - nw / 2, cy - nh / 2
            it["w"], it["h"] = nw, nh
            self._scale_poly(it, factor)
            self._redraw()

    def _rotate_sel(self):
        r = self._with_sel()
        if r:
            it = r[2]
            cx, cy = it["x"] + it["w"] / 2, it["y"] + it["h"] / 2
            if it.get("poly"):
                it["poly"] = [(round(cx - (py - cy), 3), round(cy + (px - cx), 3))
                              for px, py in it["poly"]]
            it["w"], it["h"] = it["h"], it["w"]
            it["x"], it["y"] = cx - it["w"] / 2, cy - it["h"] / 2
            it["angle"] = (it.get("angle", 0) + 90) % 360
            self._redraw()

    def _mirror_sel(self):
        r = self._with_sel()
        if r:
            r[2]["mirror"] = not r[2].get("mirror")
            self._redraw()

    def _delete_sel(self):
        r = self._with_sel()
        if r:
            key, idx, _ = r
            self.plan["views"][key]["items"].pop(idx)
            self.selected = None
            self._redraw()

    def _reset_edit(self):
        for key, orig in self._orig_items.items():
            self.plan["views"][key]["items"] = copy.deepcopy(orig)
        self.selected = None
        self._redraw()

    # ================= 模板 / 板材库 =================
    def _reload_templates(self):
        prev = list(self.tpl_list.curselection())
        self.tpl_list.delete(0, tk.END)
        for t in self.store.templates():
            self.tpl_list.insert(tk.END, f"{t.get('name')}  ({t.get('updated','')})")
        for i in prev:
            if i < self.tpl_list.size():
                self.tpl_list.selection_set(i)

    def _tpl_save(self):
        name = self.tpl_name.get().strip()
        if not name:
            messagebox.showwarning("提示", "请填写模板名称")
            return
        try:
            params = self._collect()
        except ValueError as exc:
            messagebox.showwarning("参数错误", str(exc))
            return
        params.pop("board_outline", None)
        self.store.save_template(name, params)
        self._reload_templates()
        self._reload_history()

    def _tpl_load(self):
        sel = self.tpl_list.curselection()
        if not sel:
            messagebox.showwarning("提示", "请先选择一个模板")
            return
        t = self.store.templates()[sel[0]]
        self._apply_params(t.get("params") or {})
        self.tpl_name.set(t.get("name", ""))
        self.store.log("模板加载", t.get("name", ""))
        self._reload_history()
        self._run()

    def _tpl_delete(self):
        sel = self.tpl_list.curselection()
        if not sel:
            return
        name = self.store.templates()[sel[0]].get("name")
        if messagebox.askyesno("确认", f"删除模板 {name}?"):
            self.store.delete_template(name)
            self._reload_templates()
            self._reload_history()

    def _apply_params(self, params: dict):
        """把保存的参数回填到界面 (仅回填存在的界面字段)"""
        direct = {
            "mode": "mode", "board_w": "board_w", "board_h": "board_h",
            "board_a_w": "board_a_w", "board_a_h": "board_a_h",
            "board_b_w": "board_b_w", "board_b_h": "board_b_h",
            "pcs_gap": "pcs_gap", "set_gap": "set_gap", "rail_mm": "rail_mm",
            "array_rail_mm": "array_rail_mm", "pcs_rail_mm": "pcs_rail_mm",
            "pcs_to_edge": "pcs_to_edge", "edge_rib_mm": "edge_rib_mm",
            "rail_rib_count": "rail_rib_count", "wpnl_gap": "wpnl_gap",
            "wpnl_w": "wpnl_w", "wpnl_h": "wpnl_h", "sheet_w": "sheet_w", "sheet_h": "sheet_h",
            "break_off_mm": "break_off_mm", "stamp_pitch": "stamp_pitch",
            "stamp_diameter_mm": "stamp_diameter_mm", "mark_count": "mark_count",
            "tooling_count": "tooling_count", "board_qty": "board_qty",
            "layer_count": "layer_count", "midcut_width": "mid_cut.width",
            "midcut_count": "mid_cut.count",
        }
        for pk, vk in direct.items():
            if pk in params and params[pk] is not None and vk in self.vars:
                self.vars[vk].set(params[pk])
        for bk, vk in (("hdi", "hdi"), ("irregular_board", "irregular"),
                       ("panel_style_single", None), ("allow_rotate", "allow_rotate"),
                       ("vcut", "vcut"), ("stamp_holes", "stamp_holes"),
                       ("break_off", "break_off"), ("copper_foil", None),
                       ("ribs_enabled", None), ("impedance_enabled", None)):
            if bk in params and vk and vk in self.vars:
                self.vars[vk].set(bool(params[bk]))
        if "panel_style" in params:
            self.vars["panel_style_single"].set(params["panel_style"] == "single_pcs")
        if isinstance(params.get("copper_foil"), dict):
            self.vars["copper_foil"].set(bool(params["copper_foil"].get("enabled")))
        if isinstance(params.get("ribs"), dict):
            rb = params["ribs"]
            self.vars["ribs_enabled"].set(bool(rb.get("enabled")))
            self.vars["ribs_cfg"].set(f"{rb.get('width', 1.0)},{rb.get('count', 1)}")
        if isinstance(params.get("impedance"), dict):
            im = params["impedance"]
            self.vars["impedance_enabled"].set(bool(im.get("enabled")))
            self.vars["impedance_cfg"].set(
                f"{im.get('count', 2)},{im.get('length_mm', 20)},{im.get('width_mm', 3)},{im.get('gap_mm', 5)}")
        if params.get("rotation_candidates"):
            self.vars["rotation_candidates"].set(",".join(str(a) for a in params["rotation_candidates"]))
        else:
            self.vars["rotation_candidates"].set("NA")
        if params.get("rib_sides"):
            self.vars["rib_sides"].set(next((k for k, v in RIB_SIDES if v == params["rib_sides"]), "四边"))
        if params.get("separation") is not None:
            self.vars["separation"].set(next((k for k, v in SEPARATION_TYPES
                                              if v == params.get("separation")), SEPARATION_TYPES[0][0]))
        alt = params.get("alternate") or {}
        if alt.get("enabled"):
            tag = f"{alt.get('mode', 'mirror')}_{alt.get('by', 'row')}"
            self.vars["alternate"].set(next((k for k, v in ALT_MODES if v == tag), ALT_MODES[0][0]))
        else:
            self.vars["alternate"].set(ALT_MODES[0][0])
        mc = params.get("mid_cut") or {}
        if mc.get("enabled"):
            self.vars["midcut"].set(next((k for k, v in MIDCUT_MODES if v == mc.get("direction")),
                                         MIDCUT_MODES[0][0]))
        else:
            self.vars["midcut"].set(MIDCUT_MODES[0][0])
        if params.get("mixed_boards"):
            self.vars["mixed_text"].set(";".join(
                f"{b.get('label')},{b.get('w')},{b.get('h')}" for b in params["mixed_boards"]))
        if params.get("wpnl_sizes"):
            self.vars["wpnl_table"].set(";".join(f"{a}x{b}" for a, b in params["wpnl_sizes"]))

    def _reload_materials(self):
        self.mat_tree.delete(*self.mat_tree.get_children())
        for i, m in enumerate(self.store.materials()):
            self.mat_tree.insert("", tk.END, iid=str(i), values=(
                "☑" if m.get("enabled") else "☐", m.get("brand", ""), m.get("model", ""),
                m.get("w"), m.get("h"), m.get("thk") if m.get("thk") is not None else m.get("thickness"),
                m.get("copper", "")))

    def _mat_toggle(self, event):
        if self.mat_tree.identify_region(event.x, event.y) != "cell":
            return
        col = self.mat_tree.identify_column(event.x)
        row = self.mat_tree.identify_row(event.y)
        if col == "#1" and row:
            i = int(row)
            mats = self.store.materials()
            mats[i]["enabled"] = not mats[i].get("enabled", True)
            self.store.save()
            self._reload_materials()

    def _mat_add(self):
        f = self.mat_fields
        try:
            self.store.add_material(f["brand"].get().strip(), f["model"].get().strip(),
                                    float(f["w"].get()), float(f["h"].get()),
                                    float(f["thk"].get()) if f["thk"].get().strip() else None,
                                    f["cu"].get().strip())
        except ValueError as exc:
            messagebox.showwarning("参数错误", str(exc))
            return
        self._reload_materials()
        self._reload_history()

    def _mat_update(self):
        sel = self.mat_tree.selection()
        if not sel:
            messagebox.showwarning("提示", "请先选择一行板材")
            return
        f = self.mat_fields
        try:
            self.store.update_material(int(sel[0]), brand=f["brand"].get().strip(),
                                       model=f["model"].get().strip(),
                                       w=float(f["w"].get()), h=float(f["h"].get()),
                                       thickness=float(f["thk"].get()) if f["thk"].get().strip() else None,
                                       copper=f["cu"].get().strip())
        except ValueError as exc:
            messagebox.showwarning("参数错误", str(exc))
            return
        self._reload_materials()
        self._reload_history()

    def _mat_delete(self):
        sel = self.mat_tree.selection()
        if not sel:
            return
        self.store.delete_material(int(sel[0]))
        self._reload_materials()
        self._reload_history()

    def _mat_compare(self):
        mats = self.store.enabled_materials()
        if not mats:
            messagebox.showwarning("提示", "请先在板材库中添加并勾选板材 (点『用』列切换勾选)")
            return
        try:
            base = self._collect()
        except ValueError as exc:
            messagebox.showwarning("参数错误", str(exc))
            return
        base.pop("board_outline", None)
        res = compare_materials(base, mats, design_panel)
        lines = []
        for r in res:
            m = r["material"]
            if r.get("error"):
                lines.append(f"{m.get('brand')} {m.get('model')}: 失败 {r['error']}")
                continue
            u = r.get("utilization") or 0
            lines.append(f"{m.get('brand')} {m.get('model')} {m.get('w')}x{m.get('h')}mm → "
                         f"利用率 {u*100:.1f}% | 每稿 {r.get('counts', {}).get('pcs_per_panel')} pcs | "
                         f"成本指数 {r.get('cost_index')}")
        self.mat_status.set("板材库对比 (按利用率排序):\n" + "\n".join(lines))
        self.store.log("板材库对比", f"{len(mats)} 种板材")
        self._reload_history()

    # ================= 方案记录 =================
    def _reload_schemes(self):
        prev = list(self.sch_tree.selection())
        self.sch_tree.delete(*self.sch_tree.get_children())
        for s in self.store.schemes():
            u = (s.get("metrics") or {}).get("utilization")
            c = (s.get("metrics") or {}).get("cost_index")
            self.sch_tree.insert("", tk.END, iid=s.get("id"), values=(
                s.get("name"), s.get("status"),
                f"{u*100:.1f}%" if isinstance(u, (int, float)) else "",
                c if c is not None else "", "★" if s.get("final") else "", s.get("updated", "")))
        keep = [i for i in prev if self.sch_tree.exists(i)]
        if keep:
            self.sch_tree.selection_set(keep)
        self._reload_history()

    def _reload_history(self):
        if not hasattr(self, "his_tree"):
            return
        self.his_tree.delete(*self.his_tree.get_children())
        for i, h in enumerate(self.store.history(120)):
            self.his_tree.insert("", tk.END, iid=str(i),
                                 values=(h.get("time"), h.get("action"), h.get("detail")))

    def _scheme_metrics(self):
        if not self.plan or not self.plan.get("ok"):
            return {}
        u = self.plan.get("utilization") or {}
        return {"utilization": u.get("panel", u.get("wpnl")), "overall": u.get("overall"),
                "cost_index": (self.plan.get("cost") or {}).get("cost_index"),
                "counts": self.plan.get("counts"),
                "recommendation": self.plan.get("recommendation")}

    def _sch_save(self):
        name = self.sch_name.get().strip()
        if not name:
            messagebox.showwarning("提示", "请填写方案名称")
            return
        try:
            params = self._collect()
        except ValueError as exc:
            messagebox.showwarning("参数错误", str(exc))
            return
        params.pop("board_outline", None)
        self.store.save_scheme(name, params, self._scheme_metrics(), self.sch_status.get())
        self._reload_schemes()

    def _selected_scheme(self):
        sel = self.sch_tree.selection()
        return sel[0] if sel else None

    def _sch_load(self):
        sid = self._selected_scheme()
        if not sid:
            messagebox.showwarning("提示", "请先选择一个方案")
            return
        s = self.store.get_scheme(sid)
        if not s:
            return
        self._apply_params(s.get("params") or {})
        self.sch_name.set(s.get("name", ""))
        self.sch_status.set(s.get("status", "草稿"))
        self.store.log("方案加载", s.get("name", ""))
        self._reload_history()
        self._run()

    def _sch_status_set(self):
        sid = self._selected_scheme()
        if not sid:
            return
        self.store.set_scheme_status(sid, self.sch_status.get())
        self._reload_schemes()

    def _sch_final(self):
        sid = self._selected_scheme()
        if not sid:
            return
        self.store.set_scheme_final(sid, True)
        self._reload_schemes()

    def _sch_delete(self):
        sid = self._selected_scheme()
        if sid and messagebox.askyesno("确认", "删除该方案?"):
            self.store.delete_scheme(sid)
            self._reload_schemes()

    # ================= 导入 / 导出 =================
    def _import_dxf(self):
        path = filedialog.askopenfilename(title="导入 DXF 外形",
                                          filetypes=[("DXF", "*.dxf"), ("All files", "*.*")])
        if not path:
            return
        try:
            from src.dxf_reader import read_dxf
            d = read_dxf(path)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("DXF 导入失败", str(exc))
            return
        if not d.get("outline"):
            messagebox.showwarning("DXF", d.get("error", "未提取到外形轮廓"))
            return
        self.board_outline = d["outline"]
        f = MM_TO_MIL if self.unit_var.get() == "mil" else 1.0
        self.vars["board_w"].set(f"{d['bbox'][0] * f:.3f}")
        self.vars["board_h"].set(f"{d['bbox'][1] * f:.3f}")
        self.store.log("导入DXF", f"{os.path.basename(path)} {d['bbox'][0]}x{d['bbox'][1]}mm")
        self._reload_history()
        self.status_var.set(f"DXF外形: {os.path.basename(path)} "
                            f"{d['bbox'][0]:.2f}x{d['bbox'][1]:.2f}mm 点数 {len(d['outline'])}")
        self._run()

    def _import_pdf(self):
        path = filedialog.askopenfilename(title="导入 PDF 参考",
                                          filetypes=[("PDF", "*.pdf"), ("All files", "*.*")])
        if not path:
            return
        try:
            from src.pdf_reader import read_pdf
            d = read_pdf(path, dpi=150)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("PDF 导入失败", str(exc))
            return
        if d.get("error"):
            messagebox.showwarning("PDF", d["error"])
            return
        self.pdf_bg = d.get("image")
        self.pdf_size = [d["page_w_mm"], d["page_h_mm"]]
        self.store.log("导入PDF", f"{os.path.basename(path)} {d['page_w_mm']}x{d['page_h_mm']}mm")
        self._reload_history()
        self.status_var.set(f"PDF参考: {os.path.basename(path)} "
                            f"{d['page_w_mm']:.1f}x{d['page_h_mm']:.1f}mm")

    def _backgrounds(self):
        return {"panel": self.pdf_bg} if self.pdf_bg is not None else None

    def _export(self):
        if not self.plan or not self.plan.get("ok"):
            messagebox.showwarning("提示", "请先生成排版")
            return
        out_dir = filedialog.askdirectory(title="选择导出目录")
        if not out_dir:
            return
        try:
            svgs = render_plan_svgs(self.plan, 700)
            for key, svg in svgs.items():
                with open(os.path.join(out_dir, f"panel_{key}.svg"), "w", encoding="utf-8") as f:
                    f.write(svg)
            with open(os.path.join(out_dir, "panel_plan.json"), "w", encoding="utf-8") as f:
                json.dump(self.plan, f, indent=2, ensure_ascii=False, default=str)
            self.store.log("导出SVG/JSON", out_dir)
            self._reload_history()
            messagebox.showinfo("导出完成",
                                f"{out_dir}\npanel_pcs.svg / panel_array.svg / panel_panel.svg\npanel_plan.json")
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("导出失败", str(exc))

    def _export_png(self):
        if not self.plan or not self.plan.get("ok"):
            messagebox.showwarning("提示", "请先生成排版")
            return
        out_dir = filedialog.askdirectory(title="选择 PNG 导出目录")
        if not out_dir:
            return
        try:
            from src.panel_export import export_png
            paths = export_png(self.plan, out_dir, 1500, self._backgrounds())
            self.store.log("导出PNG", out_dir)
            self._reload_history()
            messagebox.showinfo("导出完成", "\n".join(paths.values()))
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("导出失败", str(exc))

    def _export_pdf(self):
        if not self.plan or not self.plan.get("ok"):
            messagebox.showwarning("提示", "请先生成排版")
            return
        path = filedialog.asksaveasfilename(title="导出 PDF", defaultextension=".pdf",
                                            initialfile="panel.pdf",
                                            filetypes=[("PDF", "*.pdf"), ("All files", "*.*")])
        if not path:
            return
        try:
            from src.panel_export import export_pdf
            export_pdf(self.plan, path, 1500, self._backgrounds())
            self.store.log("导出PDF", path)
            self._reload_history()
            messagebox.showinfo("导出完成", path)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("导出失败", str(exc))
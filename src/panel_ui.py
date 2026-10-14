"""
Evan_DFM: 排版(拼版)设计界面
=============================================
按《排版方式.docx》的三图联动可视化:
    SET图  /  拼版图  /  开料图
三张画布共享同一方案数据, 缩放/平移联动; A(蓝)/B(橙) 分色;
支持单料号 / 大小拼(AB) / 多料号混合, 错位拼 / 横直料, 凑表值,
工艺边 / V-Cut / 邮票孔 / 折断边 / 阻抗条 / 铜箔开料 / Mark / 定位孔。
"""
import json
import logging
import os
import copy
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from src.panel_engine import (design_panel, render_plan_svgs, rotated_corners, format_dim,
                              WPNL_OPTIONS_INCH, INCH_TO_MM, MIL_TO_MM, MM_TO_MIL,
                              ARRANGEMENT_TYPES, STACKUP_TYPES)
from src.branding import win_title

logger = logging.getLogger(__name__)


class PanelDesigner:
    def __init__(self, master):
        self.win = tk.Toplevel(master)
        self.win.title(win_title("排版设计 (拼版)"))
        from src.win_dpi import apply_window
        apply_window(self.win, 1180, 760)

        self.plan = None
        self.zoom = 1.0
        self.pan = [0.0, 0.0]
        self._pans = {}
        self.board_outline = None
        self.pdf_bg = None
        self.pdf_size = None
        self.selected = None          # (view_key, index) 当前选中单元
        self._drag = None             # 拖动/平移状态
        self._orig_items = {}         # 原始单元备份 (重置用)

        self.vars = {}
        self._build_left()
        self._build_right()

    # ---------------- 左侧参数 ----------------
    def _entry(self, parent, row, label, key, default, width=10):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky=tk.W, pady=1)
        var = tk.StringVar(value=str(default))
        ttk.Entry(parent, textvariable=var, width=width).grid(row=row, column=1, sticky=tk.W, padx=4)
        self.vars[key] = var
        return row + 1

    def _combo(self, parent, row, label, key, values, default):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky=tk.W, pady=1)
        var = tk.StringVar(value=default)
        ttk.Combobox(parent, textvariable=var, values=values, state="readonly", width=12).grid(
            row=row, column=1, sticky=tk.W, padx=4)
        self.vars[key] = var
        return row + 1

    def _check(self, parent, row, label, key, default=True):
        var = tk.BooleanVar(value=default)
        ttk.Checkbutton(parent, text=label, variable=var).grid(row=row, column=0, columnspan=2, sticky=tk.W)
        self.vars[key] = var
        return row + 1

    def _build_left(self):
        outer = ttk.Frame(self.win)
        outer.pack(side=tk.LEFT, fill=tk.Y)
        canvas = tk.Canvas(outer, width=330, highlightthickness=0)
        sb = ttk.Scrollbar(outer, orient=tk.VERTICAL, command=canvas.yview)
        f = ttk.Frame(canvas)
        f.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.create_window((0, 0), window=f, anchor=tk.NW)
        canvas.configure(yscrollcommand=sb.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y)

        r = 0
        ttk.Label(f, text="基本", font=("Arial", 10, "bold")).grid(row=r, column=0, columnspan=2, sticky=tk.W)
        r += 1
        r = self._combo(f, r, "料号模式", "mode", ["single", "ab", "mixed"], "single")
        arr_labels = [t[0] for t in ARRANGEMENT_TYPES]
        r = self._combo(f, r, "排列方式", "arrangement", arr_labels, arr_labels[0])
        r = self._combo(f, r, "叠板定位工艺", "stackup", [t[1] for t in STACKUP_TYPES],
                        STACKUP_TYPES[0][1])
        self._last_unit = "mm"
        ttk.Label(f, text="尺寸单位").grid(row=r, column=0, sticky=tk.W, pady=1)
        self.unit_var = tk.StringVar(value="mm")
        uc = ttk.Combobox(f, textvariable=self.unit_var, values=["mm", "mil"],
                          state="readonly", width=12)
        uc.grid(row=r, column=1, sticky=tk.W, padx=4)
        uc.bind("<<ComboboxSelected>>", lambda e: self._on_unit_change())
        self.vars["units"] = self.unit_var
        r += 1
        r = self._entry(f, r, "PCS旋转角度(逗号, NA=不转)", "rotation_candidates", "NA", width=16)

        ttk.Separator(f, orient=tk.HORIZONTAL).grid(row=r, column=0, columnspan=2, sticky="ew", pady=4)
        r += 1
        ttk.Label(f, text="单PCS 尺寸 (PCS/AB-A)", font=("Arial", 9, "bold")).grid(
            row=r, column=0, columnspan=2, sticky=tk.W)
        r += 1
        r = self._entry(f, r, "PCS 宽 board_w", "board_w", 31.0)
        r = self._entry(f, r, "PCS 高 board_h", "board_h", 31.2)
        r = self._entry(f, r, "A 宽 board_a_w", "board_a_w", 31.0)
        r = self._entry(f, r, "A 高 board_a_h", "board_a_h", 31.2)
        r = self._entry(f, r, "B 宽 board_b_w", "board_b_w", 40.0)
        r = self._entry(f, r, "B 高 board_b_h", "board_b_h", 25.0)
        r = self._entry(f, r, "混合料号(label,w,h;...)", "mixed_text", "", width=22)

        ttk.Separator(f, orient=tk.HORIZONTAL).grid(row=r, column=0, columnspan=2, sticky="ew", pady=4)
        r += 1
        ttk.Label(f, text="间距/留边 (mm)", font=("Arial", 9, "bold")).grid(
            row=r, column=0, columnspan=2, sticky=tk.W)
        r += 1
        r = self._entry(f, r, "PCS 间距 pcs_gap", "pcs_gap", 0.0)
        r = self._entry(f, r, "ARRAY 间距 set_gap(距阻抗条)", "set_gap", 2.0)
        r = self._entry(f, r, "工艺边 rail_mm(2/5/10)", "rail_mm", 4.0)
        r = self._entry(f, r, "Panel 间距 wpnl_gap", "wpnl_gap", 4.0)
        r = self._entry(f, r, "ARRAY 行 set_rows(空=自动)", "set_rows", "")
        r = self._entry(f, r, "ARRAY 列 set_cols(空=自动)", "set_cols", "")

        ttk.Separator(f, orient=tk.HORIZONTAL).grid(row=r, column=0, columnspan=2, sticky="ew", pady=4)
        r += 1
        ttk.Label(f, text="Panel(工作稿)/大板 (mm)", font=("Arial", 9, "bold")).grid(
            row=r, column=0, columnspan=2, sticky=tk.W)
        r += 1
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
                          state="readonly", width=34)
        cb.grid(row=r, column=0, columnspan=2, sticky="ew", padx=2)
        cb.bind("<<ComboboxSelected>>", lambda e: self._on_wpnl_select())
        r += 1
        r = self._entry(f, r, "工作稿宽 wpnl_w", "wpnl_w", 457.2)
        r = self._entry(f, r, "工作稿高 wpnl_h", "wpnl_h", 609.6)
        r = self._entry(f, r, "PCS到工艺边间距", "pcs_to_edge", 0.0)
        r = self._entry(f, r, "凑表值(wpnl尺寸表)", "wpnl_table", "")
        r = self._entry(f, r, "大板宽 sheet_w", "sheet_w", 660.4)
        r = self._entry(f, r, "大板高 sheet_h", "sheet_h", 830.0)

        ttk.Separator(f, orient=tk.HORIZONTAL).grid(row=r, column=0, columnspan=2, sticky="ew", pady=4)
        r += 1
        ttk.Label(f, text="选型依据(自动建议)", font=("Arial", 9, "bold")).grid(
            row=r, column=0, columnspan=2, sticky=tk.W)
        r += 1
        r = self._entry(f, r, "需求数量 board_qty", "board_qty", "")
        r = self._entry(f, r, "板层数 layer_count", "layer_count", "")
        r = self._check(f, r, "HDI/高精度叠板", "hdi", False)
        r = self._check(f, r, "异形/不规则板", "irregular", False)

        ttk.Separator(f, orient=tk.HORIZONTAL).grid(row=r, column=0, columnspan=2, sticky="ew", pady=4)
        r += 1
        ttk.Label(f, text="工艺选项", font=("Arial", 9, "bold")).grid(row=r, column=0, columnspan=2, sticky=tk.W)
        r += 1
        r = self._check(f, r, "允许旋转(0-360°)", "allow_rotate", False)
        r = self._check(f, r, "单PCS+工艺边", "panel_style_single", False)
        r = self._entry(f, r, "PCS-工艺边连接筋宽mm(2.0)", "edge_rib_mm", 2.0)
        r = self._entry(f, r, "每边连接筋数量", "rail_rib_count", 3)
        r = self._check(f, r, "V-Cut 切割线", "vcut", True)
        r = self._check(f, r, "邮票孔", "stamp_holes", False)
        r = self._entry(f, r, "邮票孔间距", "stamp_pitch", 2.0)
        r = self._check(f, r, "连接筋(连接)", "ribs_enabled", False)
        r = self._entry(f, r, "连接筋宽度/数量", "ribs_cfg", "1.0,1", width=14)
        r = self._check(f, r, "折断边", "break_off", True)
        r = self._entry(f, r, "折断边宽度", "break_off_mm", 3.0)
        r = self._check(f, r, "阻抗条", "impedance_enabled", False)
        r = self._entry(f, r, "阻抗条 数量/长/宽/间距", "impedance_cfg", "2,20,3,5", width=16)
        r = self._check(f, r, "铜箔开料核算", "copper_foil", False)
        r = self._entry(f, r, "Mark 点数", "mark_count", 3)
        r = self._entry(f, r, "定位孔数", "tooling_count", 2)

        btns = ttk.Frame(f)
        btns.grid(row=r, column=0, columnspan=2, pady=8, sticky="ew")
        ttk.Button(btns, text="生成排版", command=self._run).pack(fill=tk.X, pady=2)
        ttk.Button(btns, text="导入DXF外形", command=self._import_dxf).pack(fill=tk.X, pady=2)
        ttk.Button(btns, text="导入PDF参考", command=self._import_pdf).pack(fill=tk.X, pady=2)
        self.src_label = tk.StringVar(value="未导入外形/参考")
        ttk.Label(f, textvariable=self.src_label, foreground="gray", wraplength=300).grid(
            row=r, column=0, columnspan=2, sticky=tk.W, pady=(2, 6))
        ttk.Button(btns, text="导出PNG图片", command=self._export_png).pack(fill=tk.X, pady=2)
        ttk.Button(btns, text="导出PDF文档", command=self._export_pdf).pack(fill=tk.X, pady=2)

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
                 "pcs_gap", "set_gap", "rail_mm", "pcs_to_edge", "wpnl_w", "wpnl_h",
                 "wpnl_gap", "sheet_w", "sheet_h", "break_off_mm", "stamp_pitch")

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
        if self.plan:                          # 单位变化后按新单位重绘尺寸标注
            try:
                self._run()
            except Exception as exc:  # noqa: BLE001
                logger.warning("单位切换后重绘失败: %s", exc)

    # ---------------- 右侧三图 ----------------
    def _build_right(self):
        right = ttk.Frame(self.win)
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        bar = ttk.Frame(right)
        bar.pack(fill=tk.X)
        ttk.Label(bar, text="三图联动:").pack(side=tk.LEFT)
        ttk.Button(bar, text="适应", command=self._fit).pack(side=tk.LEFT, padx=4)
        ttk.Button(bar, text="视图放大", command=lambda: self._zoom_all(1.25)).pack(side=tk.LEFT)
        ttk.Button(bar, text="视图缩小", command=lambda: self._zoom_all(0.8)).pack(side=tk.LEFT, padx=4)
        ttk.Separator(bar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=6)
        ttk.Label(bar, text="选中单元:").pack(side=tk.LEFT)
        ttk.Button(bar, text="隐藏", width=5, command=self._hide_sel).pack(side=tk.LEFT, padx=2)
        ttk.Button(bar, text="放大", width=5, command=lambda: self._scale_sel(1.25)).pack(side=tk.LEFT, padx=2)
        ttk.Button(bar, text="缩小", width=5, command=lambda: self._scale_sel(0.8)).pack(side=tk.LEFT, padx=2)
        ttk.Button(bar, text="旋转90", width=6, command=self._rotate_sel).pack(side=tk.LEFT, padx=2)
        ttk.Button(bar, text="镜像", width=5, command=self._mirror_sel).pack(side=tk.LEFT, padx=2)
        ttk.Button(bar, text="删除", width=5, command=self._delete_sel).pack(side=tk.LEFT, padx=2)
        ttk.Button(bar, text="显示全部", width=8, command=self._show_all).pack(side=tk.LEFT, padx=2)
        ttk.Button(bar, text="重置编辑", width=8, command=self._reset_edit).pack(side=tk.LEFT, padx=2)
        self.stat_var = tk.StringVar(value="等待生成")
        ttk.Label(bar, textvariable=self.stat_var, foreground="#333").pack(side=tk.RIGHT)

        grid = ttk.Frame(right)
        grid.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)
        self.canvases = {}
        titles = [("pcs", "PCS图"), ("array", "ARRAY图"), ("panel", "PANEL图")]
        for i, (key, t) in enumerate(titles):
            card = ttk.LabelFrame(grid, text=t, padding=4)
            card.grid(row=0, column=i, sticky="nsew", padx=4, pady=4)
            cv = tk.Canvas(card, width=330, height=600, bg="#fbfbfb", highlightthickness=1,
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

    # ---------------- 参数收集 ----------------
    def _collect(self):
        def fnum(key, default=None):
            txt = self.vars[key].get().strip()
            return float(txt) if txt else default

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
            "rail_mm": fnum("rail_mm", 4.0), "wpnl_gap": fnum("wpnl_gap", 4.0),
            "pcs_to_edge": fnum("pcs_to_edge", 0.0),
            "edge_rib_mm": fnum("edge_rib_mm", 2.0),
            "rail_rib_count": int(fnum("rail_rib_count", 3)),
            "set_rows": int(fnum("set_rows")) if self.vars["set_rows"].get().strip() else None,
            "set_cols": int(fnum("set_cols")) if self.vars["set_cols"].get().strip() else None,
            "wpnl_w": fnum("wpnl_w", 457.2), "wpnl_h": fnum("wpnl_h", 609.6),
            "sheet_w": fnum("sheet_w", 660.4), "sheet_h": fnum("sheet_h", 830.0),
            "allow_rotate": self.vars["allow_rotate"].get(),
            "vcut": self.vars["vcut"].get(), "stamp_holes": self.vars["stamp_holes"].get(),
            "stamp_pitch": fnum("stamp_pitch", 2.0),
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
            for i, part in enumerate(mt.replace("；", ";").split(";")):
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
            keys = ["board_w", "board_h", "board_a_w", "board_a_h", "board_b_w",
                    "board_b_h", "pcs_gap", "set_gap", "rail_mm", "pcs_to_edge",
                    "wpnl_gap", "sheet_w", "sheet_h", "break_off_mm", "stamp_pitch"]
            if not p.get("wp_nl_option"):
                keys += ["wpnl_w", "wpnl_h"]
            for k in keys:
                if p.get(k) is not None:
                    p[k] = round(p[k] * MIL_TO_MM, 4)
            if p.get("wpnl_sizes"):
                p["wpnl_sizes"] = [[round(a * MIL_TO_MM, 4), round(b * MIL_TO_MM, 4)]
                                   for a, b in p["wpnl_sizes"]]
            p["mixed_boards"] = [{"label": b["label"],
                                  "w": round(b["w"] * MIL_TO_MM, 4),
                                  "h": round(b["h"] * MIL_TO_MM, 4)} for b in p["mixed_boards"]]
            impd = p.get("impedance") or {}
            if impd.get("enabled"):
                for k in ("length_mm", "width_mm", "gap_mm"):
                    if impd.get(k) is not None:
                        impd[k] = round(impd[k] * MIL_TO_MM, 4)
        return p

    # ---------------- 生成/绘制 ----------------
    def _run(self):
        try:
            params = self._collect()
        except ValueError as exc:
            messagebox.showwarning("参数错误", f"数值解析失败: {exc}")
            return
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
        self.stat_var.set(
            f"Panel {pw_mm[0]}x{pw_mm[1]}mm = {pw_mil[0]}x{pw_mil[1]}mil  |  "
            f"利用率 工作稿 {u.get('panel', u.get('wpnl', 0))*100:.1f}% "
            f"(计成本 {u.get('panel_charged', 0)*100:.1f}%) / 整体 {u['overall']*100:.1f}%  "
            f"| 成本指数 {self.plan['cost']['cost_index']}  ||  "
            f"选型建议: {rec.get('label','')} ; {sk.get('label','')}")
        self._fit()

    def _fit(self):
        self.zoom = 1.0
        self.pan = [0.0, 0.0]
        self._redraw()

    def _zoom_all(self, factor):
        self.zoom *= factor
        self._redraw()

    def _pan_start(self, event, key):
        self._pans[key] = (event.x, event.y, self.pan[0], self.pan[1])

    # ---------------- 单元选中/拖动/编辑 ----------------
    def _view_scale(self, key):
        cv = self.canvases[key]
        view = self.plan["views"][key]
        cw, ch = view["container"]
        W = cv.winfo_width() or 330
        H = cv.winfo_height() or 600
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
            self._drag = {"mode": "item", "key": key, "idx": idx,
                          "mx": mx, "my": my, "ox": it["x"], "oy": it["y"],
                          "poly": [list(p) for p in it.get("poly", [])]}
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
                it["poly"] = [(round(px + dx, 3), round(py + dy, 3))
                              for px, py in self._drag["poly"]]
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
            it["poly"] = [(cx + (px - cx) * factor, cy + (py - cy) * factor)
                          for px, py in it["poly"]]
            it["poly"] = [(round(px, 3), round(py, 3)) for px, py in it["poly"]]

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
                it["poly"] = [(cx - (py - cy), cy + (px - cx)) for px, py in it["poly"]]
                it["poly"] = [(round(px, 3), round(py, 3)) for px, py in it["poly"]]
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
        H = cv.winfo_height() or 600
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
                               fill="#c62828", dash=(5, 3))
            elif t == "stamp":
                cv.create_oval(tx(f["x"]) - 1.5, ty(f["y"]) - 1.5, tx(f["x"]) + 1.5, ty(f["y"]) + 1.5,
                               fill="#555", outline="")
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

    # ---------------- 导出 ----------------
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
            messagebox.showinfo("导出完成",
                                f"{out_dir}\npanel_pcs.svg / panel_array.svg / panel_panel.svg\npanel_plan.json")
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("导出失败", str(exc))

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
        self.src_label.set(f"DXF外形: {os.path.basename(path)}  "
                           f"{d['bbox'][0]:.2f}x{d['bbox'][1]:.2f}mm  点数 {len(d['outline'])}")
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
        self.src_label.set(f"PDF参考: {os.path.basename(path)}  "
                           f"{d['page_w_mm']:.1f}x{d['page_h_mm']:.1f}mm  页数 {d.get('page_count')}")

    def _backgrounds(self):
        # panel_export.render_plan_images 仅识别 pcs/array/panel 三个视图键;
        # PDF 参考底图(开料/工作稿)叠加到 panel 视图
        return {"panel": self.pdf_bg} if self.pdf_bg is not None else None

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
            messagebox.showinfo("导出完成", path)
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("导出失败", str(exc))
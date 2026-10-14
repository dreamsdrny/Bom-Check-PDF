"""
Evan_DFM: Gerber 查看器 (KiCad 风格) + 双 Gerber 叠加对比
=============================================================
功能:
  - 导入多个 Gerber 文件 (线路/阻焊/丝印/钻孔等), 图层列表 + 显隐 + 颜色
  - 缩放(滚轮)/平移(右键拖动)/适应窗口
  - 选中图层后可单独拖动 (左键拖动 = 移动该图层; 用于两 Gerber 单PCS 重叠对比对齐)
  - 双 Gerber 叠加对比: A/B 分色, 各自可拖动对齐, 支持镜像/闪烁高亮
  - 导出 PNG

复用 src.fab_data_reader.parse_gerber / parse_excellon 的几何数据。
"""
import math
import os
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from src.fab_data_reader import parse_gerber, parse_excellon
from src.branding import win_title

_PALETTE = ["#e53935", "#1e88e5", "#43a047", "#fb8c00", "#8e24aa",
            "#00acc1", "#6d4c41", "#3949ab", "#c0ca33", "#d81b60"]


class GerberLayer:
    def __init__(self, name, data, color):
        self.name = name
        self.data = data
        self.color = color
        self.visible = True
        self.offset = [0.0, 0.0]
        self.mirror_x = False
        self.kind = data.get("kind", "gerber")

    def bbox(self):
        b = self.data.get("bbox")
        if not b or b[0] is None:
            return None
        minx, miny, maxx, maxy = b
        if self.mirror_x:
            minx, maxx = -maxx, -minx
        dx, dy = self.offset
        return [minx + dx, miny + dy, maxx + dx, maxy + dy]

    def local_bbox(self):
        """不含偏移的包围盒 (镜像已应用), 用于位图缓存"""
        b = self.data.get("bbox")
        if not b or b[0] is None:
            return None
        minx, miny, maxx, maxy = b
        if self.mirror_x:
            minx, maxx = -maxx, -minx
        return [minx, miny, maxx, maxy]


class GerberViewer:
    def __init__(self, master, title="Gerber 查看器 (叠加对比)"):
        self.win = tk.Toplevel(master)
        self.win.title(win_title(title))
        from src.win_dpi import apply_window
        apply_window(self.win, 1180, 760)

        self.layers = []
        self.selected = None
        self.zoom = 1.0
        self.pan = [0.0, 0.0]
        self._drag = None
        self._img_cache = {}          # 图层位图缓存: id(layer) -> entry
        self._render_job = None       # 渲染节流 (缩放/平移合并刷新)
        self._view_bbox = None        # 固定视图范围 (拖动时不变, 便于缓存复用)
        self._pil = None              # 延迟加载 PIL

        self._build()

    # ---------------- UI ----------------
    def _build(self):
        top = ttk.Frame(self.win, padding=6)
        top.pack(fill=tk.X)
        ttk.Button(top, text="导入Gerber...", command=self._import_gerber).pack(side=tk.LEFT)
        ttk.Button(top, text="导入钻孔...", command=self._import_drill).pack(side=tk.LEFT, padx=4)
        ttk.Button(top, text="导入全部(目录)...", command=self._import_dir).pack(side=tk.LEFT)
        ttk.Separator(top, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=6)
        ttk.Button(top, text="对比: 导入A", command=lambda: self._compare_load("A")).pack(side=tk.LEFT)
        ttk.Button(top, text="对比: 导入B", command=lambda: self._compare_load("B")).pack(side=tk.LEFT, padx=4)
        ttk.Button(top, text="清空", command=self._clear).pack(side=tk.LEFT, padx=6)
        ttk.Button(top, text="导出PNG", command=self._export_png).pack(side=tk.LEFT)
        ttk.Button(top, text="关闭", command=self.win.destroy).pack(side=tk.RIGHT)

        bar = ttk.Frame(self.win, padding=(6, 0))
        bar.pack(fill=tk.X)
        ttk.Button(bar, text="适应", command=self.fit).pack(side=tk.LEFT)
        ttk.Button(bar, text="视图放大", command=lambda: self._zoom(1.25)).pack(side=tk.LEFT, padx=4)
        ttk.Button(bar, text="视图缩小", command=lambda: self._zoom(0.8)).pack(side=tk.LEFT)
        ttk.Separator(bar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=6)
        ttk.Label(bar, text="选中图层:").pack(side=tk.LEFT)
        ttk.Button(bar, text="居中", command=self._center_sel).pack(side=tk.LEFT, padx=2)
        ttk.Button(bar, text="重置偏移", command=self._reset_sel).pack(side=tk.LEFT, padx=2)
        ttk.Button(bar, text="镜像", command=self._mirror_sel).pack(side=tk.LEFT, padx=2)
        ttk.Button(bar, text="上移", command=lambda: self._move_layer(1)).pack(side=tk.LEFT, padx=2)
        ttk.Button(bar, text="下移", command=lambda: self._move_layer(-1)).pack(side=tk.LEFT, padx=2)
        ttk.Button(bar, text="删除", command=self._delete_sel).pack(side=tk.LEFT, padx=2)
        ttk.Button(bar, text="对齐中心", command=self._align_center).pack(side=tk.LEFT, padx=2)
        ttk.Button(bar, text="对齐左下", command=self._align_bl).pack(side=tk.LEFT, padx=2)
        self.move_mode = tk.BooleanVar(value=True)
        ttk.Checkbutton(bar, text="拖动移动图层", variable=self.move_mode).pack(side=tk.LEFT, padx=6)
        self.info = tk.StringVar(value="左键拖动=移动选中图层；右键拖动=平移；滚轮=缩放")
        ttk.Label(bar, textvariable=self.info, foreground="#555").pack(side=tk.RIGHT)

        body = ttk.Frame(self.win)
        body.pack(fill=tk.BOTH, expand=True)

        left = ttk.LabelFrame(body, text="图层 (点『开/关』列开关)", padding=4)
        left.pack(side=tk.LEFT, fill=tk.Y)
        btnbar = ttk.Frame(left)
        btnbar.pack(fill=tk.X, pady=(0, 4))
        ttk.Button(btnbar, text="全开", width=5, command=self._all_on).pack(side=tk.LEFT)
        ttk.Button(btnbar, text="一键全关", width=8, command=self._all_off).pack(side=tk.LEFT, padx=2)
        ttk.Button(btnbar, text="开选中", width=6, command=lambda: self._set_sel(True)).pack(side=tk.LEFT)
        ttk.Button(btnbar, text="关选中", width=6, command=lambda: self._set_sel(False)).pack(side=tk.LEFT, padx=2)
        ttk.Button(btnbar, text="反选", width=5, command=self._invert).pack(side=tk.LEFT)

        style = ttk.Style(self.win)
        try:
            style.configure("GerberLayers.Treeview", font=("Microsoft YaHei UI", 11), rowheight=30)
            style.configure("GerberLayers.Treeview.Heading",
                            font=("Microsoft YaHei UI", 10, "bold"))
        except Exception:
            pass
        self.tree = ttk.Treeview(left, columns=("st", "name"), show="headings",
                                 height=26, selectmode="extended",
                                 style="GerberLayers.Treeview")
        self.tree.heading("st", text="开/关")
        self.tree.heading("name", text="图层")
        self.tree.column("st", width=70, anchor=tk.CENTER, stretch=False)
        self.tree.column("name", width=220, anchor=tk.W)
        self.tree.pack(side=tk.LEFT, fill=tk.Y)
        tsb = ttk.Scrollbar(left, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=tsb.set)
        tsb.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.tag_configure("on", foreground="#0a7a3a")
        self.tree.tag_configure("off", foreground="#999999")
        self.tree.bind("<Button-1>", self._on_tree_click)
        self.tree.bind("<Double-Button-1>", self._on_tree_dbl)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)

        self.canvas = tk.Canvas(body, bg="#0f1115", highlightthickness=0)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.canvas.bind("<MouseWheel>", lambda e: self._zoom(1.2 if e.delta > 0 else 0.8))
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_motion)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<ButtonPress-3>", self._pan_start)
        self.canvas.bind("<B3-Motion>", self._pan_move)
        self.canvas.bind("<Motion>", self._on_hover)
        self.canvas.bind("<Configure>", lambda e: self._render())

        self.status = tk.StringVar(value="Ready")
        ttk.Label(self.win, textvariable=self.status, relief=tk.SUNKEN,
                  anchor=tk.W).pack(fill=tk.X, side=tk.BOTTOM)

    # ---------------- 图层管理 ----------------
    def add_layer(self, path, color=None, name=None, fit_now=True):
        data = parse_gerber(path, layer_name=name or os.path.basename(path))
        if color is None:
            color = _PALETTE[len(self.layers) % len(_PALETTE)]
        layer = GerberLayer(name or os.path.basename(path), data, color)
        layer.visible = False                    # 默认关闭 (需要时点亮)
        self.layers.append(layer)
        if fit_now:
            self._refresh_list()
            self.selected = len(self.layers) - 1
            self._sync_selection()
            self.fit()
        return layer

    def add_drill(self, path, fit_now=True):
        data = parse_excellon(path)
        color = "#ffffff"
        layer = GerberLayer(os.path.basename(path), data, color)
        layer.kind = "drill"
        layer.visible = False
        self.layers.append(layer)
        if fit_now:
            self._refresh_list()
            self.selected = len(self.layers) - 1
            self._sync_selection()
            self.fit()
        return layer

    def _import_gerber(self):
        paths = filedialog.askopenfilenames(
            title="导入 Gerber 文件",
            filetypes=[("Gerber", "*.gbr *.ger *.art *.pho *.gto *.gbo *.gts *.gbs *.gtl *.gbl"),
                       ("All files", "*.*")])
        self._add_paths(paths)

    def _import_drill(self):
        paths = filedialog.askopenfilenames(title="导入钻孔 (NC Drill)",
                                            filetypes=[("Drill", "*.drl *.txt"), ("All files", "*.*")])
        self._add_paths(paths)

    def _import_dir(self):
        d = filedialog.askdirectory(title="导入目录下全部 Gerber/钻孔")
        if not d:
            return
        exts = (".gbr", ".ger", ".art", ".pho", ".gto", ".gbo", ".gts", ".gbs",
                ".gtl", ".gbl", ".gtp", ".gbp", ".gko", ".gm1", ".drl")
        paths = []
        for root_, _dirs, files in os.walk(d):
            for fn in sorted(files):
                if fn.lower().endswith(exts):
                    paths.append(os.path.join(root_, fn))
        if not paths:
            messagebox.showinfo("导入", "目录下未找到 Gerber/钻孔文件")
            return
        self._add_paths(paths)

    def _add_paths(self, paths):
        paths = list(paths or [])
        if not paths:
            return
        n = len(paths)
        errs = []
        for i, p in enumerate(paths, 1):
            try:
                self.status.set(f"解析中 {i}/{n}: {os.path.basename(p)}")
                self.win.update_idletasks()
                low = p.lower()
                if low.endswith(".drl") or "drill" in low:
                    self.add_drill(p, fit_now=False)
                else:
                    self.add_layer(p, fit_now=False)
            except Exception as exc:  # noqa: BLE001
                errs.append(f"{os.path.basename(p)}: {exc}")
        self.selected = len(self.layers) - 1 if self.layers else None
        self._refresh_list()
        self.fit()
        self.status.set(f"已导入 {n} 个文件, 图层默认关闭 — 点『开/关』列点亮 (可 Shift 多选后『开选中』)")
        if errs:
            messagebox.showerror("部分导入失败", "\n".join(errs[:10]))

    def _compare_load(self, tag):
        path = filedialog.askopenfilename(title=f"对比: 导入 Gerber {tag}",
                                          filetypes=[("Gerber", "*.gbr *.ger *.art *.pho"), ("All files", "*.*")])
        if not path:
            return
        color = "#e53935" if tag == "A" else "#1e88e5"
        layer = self.add_layer(path, color=color, name=f"[{tag}] {os.path.basename(path)}")
        layer.mirror_x = (tag == "B")
        layer.visible = True
        self._refresh_list()
        self.fit()
        self.info.set(f"对比模式: A=红 B=蓝; 选中图层后左键拖动即可重叠对齐")

    def _clear(self):
        self.layers = []
        self.selected = None
        self._img_cache.clear()
        self._refresh_list()
        self._render()

    def _refresh_list(self):
        self._recompute_view_bbox()
        self.tree.delete(*self.tree.get_children())
        for i, ly in enumerate(self.layers):
            mark = "■" if ly.visible else "□"      # 方形开关: 亮(实心)=开, 空心=关
            tag = "on" if ly.visible else "off"
            self.tree.insert("", tk.END, iid=str(i),
                             values=(mark, f"{ly.name}"), tags=(tag,))
        self._sync_selection()

    def _on_tree_click(self, event):
        row = self.tree.identify_row(event.y)
        col = self.tree.identify_column(event.x)
        if not row:
            return
        mods = event.state & 0x0005   # Shift(0x1) 或 Ctrl(0x4)
        if col == "#1" and not mods:  # 点『开/关』列: 单层切换
            i = int(row)
            self.layers[i].visible = not self.layers[i].visible
            self.selected = i
            self._refresh_list()
            self._render()
            return "break"

    def _on_tree_dbl(self, event):
        row = self.tree.identify_row(event.y)
        if not row:
            return
        i = int(row)
        self.layers[i].visible = not self.layers[i].visible
        self.selected = i
        self._refresh_list()
        self._render()

    def _on_select(self, _e=None):
        sel = self.tree.selection()
        if sel:
            self.selected = int(sel[0])
            self._render()

    def _sync_selection(self):
        self.tree.selection_remove(*self.tree.selection())
        if self.selected is not None and 0 <= self.selected < len(self.layers):
            self.tree.selection_set(str(self.selected))
            self.tree.see(str(self.selected))

    def _selected_indices(self):
        return [int(s) for s in self.tree.selection() if s.isdigit()]

    def _set_sel(self, value):
        for i in self._selected_indices():
            self.layers[i].visible = value
        self._refresh_list()
        self._render()

    def _all_on(self):
        for ly in self.layers:
            ly.visible = True
        self._refresh_list()
        self._render()

    def _all_off(self):
        for ly in self.layers:
            ly.visible = False
        self._refresh_list()
        self._render()

    def _invert(self):
        for ly in self.layers:
            ly.visible = not ly.visible
        self._refresh_list()
        self._render()

    def _move_layer(self, direction):
        i = self.selected
        if i is None:
            return
        j = i + direction
        if 0 <= j < len(self.layers):
            self.layers[i], self.layers[j] = self.layers[j], self.layers[i]
            self.selected = j
            self._refresh_list()
            self._render()

    def _delete_sel(self):
        if self.selected is not None:
            self.layers.pop(self.selected)
            self.selected = None
            self._img_cache.clear()
            self._refresh_list()
            self._render()

    def _sel_layer(self):
        if self.selected is not None and 0 <= self.selected < len(self.layers):
            return self.layers[self.selected]
        return None

    def _center_sel(self):
        ly = self._sel_layer()
        if ly:
            ly.offset = [0.0, 0.0]
            self._recompute_view_bbox()
            self._render()

    def _reset_sel(self):
        ly = self._sel_layer()
        if ly:
            ly.offset = [0.0, 0.0]
            ly.mirror_x = False
            self._recompute_view_bbox()
            self._render()

    def _mirror_sel(self):
        ly = self._sel_layer()
        if ly:
            ly.mirror_x = not ly.mirror_x
            self._render()

    def _ref_layer(self):
        for i, ly in enumerate(self.layers):
            if i != self.selected and ly.visible and ly.data.get("bbox") and ly.data["bbox"][0] is not None:
                return ly
        return None

    def _align_center(self):
        ly = self._sel_layer()
        ref = self._ref_layer()
        if not ly or not ref:
            return
        b = ly.data.get("bbox"); rb = ref.data.get("bbox")
        if not b or not rb or b[0] is None or rb[0] is None:
            return
        ly.offset = [ (rb[0]+rb[2])/2 - (b[0]+b[2])/2,
                      (rb[1]+rb[3])/2 - (b[1]+b[3])/2 ]
        ly.mirror_x = ref.mirror_x
        self._recompute_view_bbox()
        self._render()
        self.info.set(f"{ly.name}: 已按中心对齐到 {ref.name}")

    def _align_bl(self):
        ly = self._sel_layer()
        ref = self._ref_layer()
        if not ly or not ref:
            return
        b = ly.data.get("bbox"); rb = ref.data.get("bbox")
        if not b or not rb or b[0] is None or rb[0] is None:
            return
        ly.offset = [rb[0] - b[0], rb[1] - b[1]]
        self._recompute_view_bbox()
        self._render()
        self.info.set(f"{ly.name}: 已按左下角对齐到 {ref.name}")

    # ---------------- 变换 ----------------
    def _bbox_all(self):
        boxes = [ly.bbox() for ly in self.layers if ly.visible and ly.bbox()]
        if not boxes:                     # 全部隐藏时, 仍按全部图层适配
            boxes = [ly.bbox() for ly in self.layers if ly.bbox()]
        if not boxes:
            return None
        return (min(b[0] for b in boxes), min(b[1] for b in boxes),
                max(b[2] for b in boxes), max(b[3] for b in boxes))

    def _recompute_view_bbox(self):
        """固定视图范围: 仅在导入/删除/适配/对齐时更新。
        拖动图层时不改变比例, 从而复用位图缓存, 避免明显卡顿。"""
        b = self._bbox_all()
        self._view_bbox = b or (0.0, 0.0, 100.0, 100.0)
        return self._view_bbox

    def _scale(self):
        vb = self._view_bbox
        if vb is None:
            vb = self._recompute_view_bbox()
        minx, miny, maxx, maxy = vb
        W = self.canvas.winfo_width() or 800
        H = self.canvas.winfo_height() or 600
        base = min(W / max(maxx - minx, 1e-6), H / max(maxy - miny, 1e-6)) * 0.9
        return base * self.zoom, minx, miny, maxx, maxy, W, H

    def _to_canvas(self, x, y):
        s, minx, miny, maxx, maxy, W, H = self._scale()
        cx = (x - (minx + maxx) / 2) * s + W / 2 + self.pan[0]
        cy = H / 2 - (y - (miny + maxy) / 2) * s + self.pan[1]
        return cx, cy

    def _canvas_to_mm(self, px, py):
        s, minx, miny, maxx, maxy, W, H = self._scale()
        x = (px - W / 2 - self.pan[0]) / s + (minx + maxx) / 2
        y = (maxy + miny) / 2 - (py - H / 2 - self.pan[1]) / s
        return x, y

    def fit(self):
        self._recompute_view_bbox()
        self.zoom = 1.0
        self.pan = [0.0, 0.0]
        self._render()

    def _zoom(self, factor):
        self.zoom *= factor
        self._schedule_render()

    def _schedule_render(self):
        """合并高频刷新(滚轮缩放/平移), 避免连续全量重绘造成卡顿"""
        if self._render_job is not None:
            try:
                self.win.after_cancel(self._render_job)
            except Exception:
                pass
        try:
            self._render_job = self.win.after(35, self._do_render)
        except Exception:
            self._render()

    def _do_render(self):
        self._render_job = None
        self._render()

    # ---------------- 交互 ----------------
    def _on_press(self, event):
        if not getattr(self, "move_mode", None) or not self.move_mode.get():
            self._pan_start(event)
            return
        self._drag = {"mode": "layer", "px": event.x, "py": event.y}
        ly = self._sel_layer()
        if ly:
            self._drag["ox"], self._drag["oy"] = ly.offset

    def _on_motion(self, event):
        if not self._drag or self._drag["mode"] != "layer":
            return
        ly = self._sel_layer()
        if not ly:
            return
        s = self._scale()[0]
        ly.offset[0] = self._drag["ox"] + (event.x - self._drag["px"]) / s
        ly.offset[1] = self._drag["oy"] - (event.y - self._drag["py"]) / s
        self._render()
        self.info.set(f"{ly.name}: 偏移 X={ly.offset[0]:.3f}mm Y={ly.offset[1]:.3f}mm")

    def _on_release(self, _event):
        self._drag = None

    def _pan_start(self, event):
        self._drag = {"mode": "pan", "px": event.x, "py": event.y,
                      "ox": self.pan[0], "oy": self.pan[1]}

    def _pan_move(self, event):
        if self._drag and self._drag["mode"] == "pan":
            self.pan = [self._drag["ox"] + (event.x - self._drag["px"]),
                        self._drag["oy"] + (event.y - self._drag["py"])]
            self._schedule_render()

    def _on_hover(self, event):
        x, y = self._canvas_to_mm(event.x, event.y)
        self.status.set(f"X={x:.3f}mm  Y={y:.3f}mm   (mil: {x*39.3701:.1f} x {y*39.3701:.1f})")

    # ---------------- 渲染 ----------------
    def _render(self):
        cv = self.canvas
        cv.delete("all")
        for ly in self.layers:
            if not ly.visible:
                continue
            self._draw_layer(cv, ly, selected=(ly is self._sel_layer()))

    def _ap_shape(self, ly, p):
        """返回闪拍光圈形状: (shape, w_mm, h_mm, edges, rot)"""
        aps = ly.data.get("apertures") or {}
        ap = aps.get(p.get("aperture"))
        scale = 25.4 if ly.data.get("unit") == "inch" else 1.0
        if not ap:
            w = p.get("width") or 0.2
            return ("C", w, w, 0, 0.0)
        shape = str(ap.get("shape", "C"))
        params = [x for x in (ap.get("params") or []) if isinstance(x, (int, float))]
        if shape == "C" or not params:
            w = (params[0] if params else (p.get("width") or 0.2)) * scale
            return ("C", w, w, 0, 0.0)
        if shape == "R":
            w = params[0] * scale
            h = (params[1] if len(params) > 1 else params[0]) * scale
            return ("R", w, h, 0, 0.0)
        if shape == "O":
            w = params[0] * scale
            h = (params[1] if len(params) > 1 else params[0]) * scale
            return ("O", w, h, 0, 0.0)
        if shape == "P":
            d = params[0] * scale
            edges = int(params[1]) if len(params) > 1 else 5
            rot = params[2] if len(params) > 2 else 0.0
            return ("P", d, d, edges, rot)
        return ("C", (params[0] * scale if params else 0.2), 0, 0, 0.0)

    def _draw_flash(self, cv, ly, p, color):
        shape, w, h, edges, rot = self._ap_shape(ly, p)
        dx, dy = ly.offset
        cx, cy = self._to_canvas(p["x1"] + dx, p["y1"] + dy)
        s = self._scale()[0]
        if shape == "C":
            r = max(1.0, w / 2 * s)
            cv.create_oval(cx - r, cy - r, cx + r, cy + r, fill=color, outline="")
        elif shape == "R":
            hw, hh = w / 2 * s, h / 2 * s
            cv.create_rectangle(cx - hw, cy - hh, cx + hw, cy + hh, fill=color, outline="")
        elif shape == "O":
            hw, hh = w / 2 * s, h / 2 * s
            rr = min(hw, hh)
            if w >= h:
                cv.create_oval(cx - hw, cy - hh, cx - hw + 2 * rr, cy + hh, fill=color, outline="")
                cv.create_oval(cx + hw - 2 * rr, cy - hh, cx + hw, cy + hh, fill=color, outline="")
                cv.create_rectangle(cx - hw + rr, cy - hh, cx + hw - rr, cy + hh, fill=color, outline="")
            else:
                cv.create_oval(cx - hw, cy - hh, cx + hw, cy - hh + 2 * rr, fill=color, outline="")
                cv.create_oval(cx - hw, cy + hh - 2 * rr, cx + hw, cy + hh, fill=color, outline="")
                cv.create_rectangle(cx - hw, cy - hh + rr, cx + hw, cy + hh - rr, fill=color, outline="")
        elif shape == "P":
            r = w / 2 * s
            pts = []
            for k in range(max(3, edges)):
                a = math.radians(rot + k * 360.0 / edges)
                pts.extend([cx + r * math.cos(a), cy + r * math.sin(a)])
            if len(pts) >= 6:
                cv.create_polygon(pts, fill=color, outline="")

    def _rgb(self, hexcolor):
        h = (hexcolor or "#2f7ed8").lstrip("#")
        if len(h) == 3:
            h = "".join(ch * 2 for ch in h)
        try:
            return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
        except ValueError:
            return (47, 126, 216)

    def _layer_image(self, ly):
        """整层渲染为位图并缓存: 每层只占 1 个画布图元,
        平移/拖动图层时无需重绘几何, 显著减少卡顿。"""
        if self._pil is None:
            from PIL import Image, ImageDraw, ImageTk
            self._pil = (Image, ImageDraw, ImageTk)
        Image, ImageDraw, ImageTk = self._pil

        lb = ly.local_bbox()
        if not lb:
            return None
        minx, miny, maxx, maxy = lb
        w = max(maxx - minx, 1e-6)
        h = max(maxy - miny, 1e-6)
        s = self._scale()[0]
        W = max(2, int(round(w * s)) + 3)
        H = max(2, int(round(h * s)) + 3)
        sig = (W, H, ly.mirror_x, ly.color)
        cached = self._img_cache.get(id(ly))
        if cached and cached[0] == sig:
            return cached[1]

        cap = 2500.0
        s_eff = s
        if max(W, H) > cap:            # 限制位图尺寸, 防止内存暴涨
            s_eff = cap / max(w, h)
        Wr = max(2, int(round(w * s_eff)) + 3)
        Hr = max(2, int(round(h * s_eff)) + 3)
        img = Image.new("RGBA", (Wr, Hr), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        col = self._rgb(ly.color)
        bgcol = (15, 17, 21, 255)
        mirror = ly.mirror_x

        def T(x, y):
            lx = -x if mirror else x
            return ((lx - minx) * s_eff + 1.5, Hr - ((y - miny) * s_eff + 1.5))

        for p in (ly.data.get("primitives") or ly.data.get("features") or []):
            t = p.get("type")
            if t == "error" or p.get("x1") is None:
                continue
            c = bgcol if p.get("clear") else col
            if t == "flash":
                shape, fw, fh, edges, rot = self._ap_shape(ly, p)
                cx, cy = T(p["x1"], p["y1"])
                if shape == "R":
                    hw, hh = fw / 2 * s_eff, fh / 2 * s_eff
                    d.rectangle([cx - hw, cy - hh, cx + hw, cy + hh], fill=c)
                elif shape == "O":
                    hw, hh = fw / 2 * s_eff, fh / 2 * s_eff
                    rr = min(hw, hh)
                    if fw >= fh:
                        d.ellipse([cx - hw, cy - hh, cx - hw + 2 * rr, cy + hh], fill=c)
                        d.ellipse([cx + hw - 2 * rr, cy - hh, cx + hw, cy + hh], fill=c)
                        d.rectangle([cx - hw + rr, cy - hh, cx + hw - rr, cy + hh], fill=c)
                    else:
                        d.ellipse([cx - hw, cy - hh, cx + hw, cy - hh + 2 * rr], fill=c)
                        d.ellipse([cx - hw, cy + hh - 2 * rr, cx + hw, cy + hh], fill=c)
                        d.rectangle([cx - hw, cy - hh + rr, cx + hw, cy + hh - rr], fill=c)
                elif shape == "P" and edges >= 3:
                    r = fw / 2 * s_eff
                    pts = [(cx + r * math.cos(math.radians(rot + k * 360.0 / edges)),
                            cy + r * math.sin(math.radians(rot + k * 360.0 / edges)))
                           for k in range(edges)]
                    d.polygon(pts, fill=c)
                else:
                    r = max(0.7, fw / 2 * s_eff)
                    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=c)
            elif t == "arc":
                pts = [T(px, py) for px, py in self._arc_points(p)]
                if len(pts) >= 2:
                    d.line(pts, fill=c)
            elif t == "region":
                pts = [T(px, py) for px, py in p.get("points", [])]
                if len(pts) >= 3:
                    d.polygon(pts, fill=c)
            else:
                d.line([T(p["x1"], p["y1"]),
                        T(p.get("x2", p["x1"]), p.get("y2", p["y1"]))], fill=c)

        if ly.kind == "drill":
            for hd in ly.data.get("holes", []):
                if hd.get("x") is None:
                    continue
                cx, cy = T(hd["x"], hd["y"])
                r = max(0.8, (hd.get("diameter_mm") or 0.3) / 2 * s_eff)
                d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=col)

        if (Wr, Hr) != (W, H):
            img = img.resize((W, H), Image.BILINEAR)
        photo = ImageTk.PhotoImage(img)
        entry = (sig, (photo, minx, miny, maxx, maxy))
        self._img_cache[id(ly)] = entry
        return entry[1]

    def _draw_layer(self, cv, ly, selected=False):
        ent = self._layer_image(ly)
        if ent is not None:
            photo, minx, miny, maxx, maxy = ent
            x0, y0 = self._to_canvas(minx + ly.offset[0], maxy + ly.offset[1])
            cv.create_image(x0, y0, image=photo, anchor="nw")
        if selected:
            b = ly.bbox()
            if b:
                c1 = self._to_canvas(b[0], b[3])
                c2 = self._to_canvas(b[2], b[1])
                cv.create_rectangle(c1[0], c1[1], c2[0], c2[1], outline="#ff9800", dash=(5, 3))

    def _arc_points(self, p, seg=16):
        x1, y1, x2, y2 = p.get("x1"), p.get("y1"), p.get("x2"), p.get("y2")
        i, j = p.get("i", 0.0), p.get("j", 0.0)
        cx, cy = x1 + i, y1 + j
        r = math.hypot(x1 - cx, y1 - cy)
        a1 = math.atan2(y1 - cy, x1 - cx)
        a2 = math.atan2(y2 - cy, x2 - cx)
        if p.get("cw"):
            while a2 > a1:
                a2 -= 2 * math.pi
        else:
            while a2 < a1:
                a2 += 2 * math.pi
        pts = []
        for k in range(seg + 1):
            a = a1 + (a2 - a1) * k / seg
            pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
        return pts

    # ---------------- 导出 ----------------
    def _export_png(self):
        try:
            from PIL import Image, ImageDraw
        except Exception:
            messagebox.showerror("导出失败", "未安装 Pillow")
            return
        path = filedialog.asksaveasfilename(title="导出 PNG", defaultextension=".png",
                                            initialfile="gerber_view.png",
                                            filetypes=[("PNG", "*.png")])
        if not path:
            return
        W, H = 2000, 1400
        img = Image.new("RGB", (W, H), (15, 17, 21))
        draw = ImageDraw.Draw(img, "RGBA")
        minx, miny, maxx, maxy = self._bbox_all()
        s = min(W / max(maxx - minx, 1e-6), H / max(maxy - miny, 1e-6)) * 0.92
        ox = (W - (maxx - minx) * s) / 2 - minx * s
        oy = (H - (maxy - miny) * s) / 2 + maxy * s

        def T(x, y):
            return ox + x * s, oy - y * s

        for ly in self.layers:
            if not ly.visible:
                continue
            col = tuple(int(ly.color.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
            dx, dy = ly.offset
            for p in (ly.data.get("primitives") or ly.data.get("features") or []):
                t = p.get("type")
                x1, y1, x2, y2 = p.get("x1"), p.get("y1"), p.get("x2"), p.get("y2")
                if x1 is None:
                    continue
                if t == "region":
                    pts = [T(px + dx, py + dy) for px, py in p.get("points", [])]
                    if len(pts) >= 3:
                        draw.polygon(pts, fill=col + (120,))
                elif t == "flash":
                    w = p.get("width") or 0.2
                    r = max(1.5, w / 2 * s)
                    cx, cy = T(x1 + dx, y1 + dy)
                    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=col)
                elif t == "arc":
                    pts = [T(px + dx, py + dy) for px, py in self._arc_points(p)]
                    if len(pts) >= 2:
                        draw.line(pts, fill=col)
                else:
                    draw.line([T(x1 + dx, y1 + dy), T((x2 or x1) + dx, (y2 or y1) + dy)], fill=col)
        img.save(path)
        messagebox.showinfo("导出完成", path)
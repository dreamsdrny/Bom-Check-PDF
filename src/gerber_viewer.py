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
        dx, dy = self.offset
        return [b[0] + dx, b[1] + dy, b[2] + dx, b[3] + dy]


class GerberViewer:
    def __init__(self, master, title="Gerber 查看器 (叠加对比)"):
        self.win = tk.Toplevel(master)
        self.win.title(f"Evan_DFM - {title}")
        self.win.geometry("1180x760")

        self.layers = []
        self.selected = None
        self.zoom = 1.0
        self.pan = [0.0, 0.0]
        self._drag = None

        self._build()

    # ---------------- UI ----------------
    def _build(self):
        top = ttk.Frame(self.win, padding=6)
        top.pack(fill=tk.X)
        ttk.Button(top, text="导入Gerber...", command=self._import_gerber).pack(side=tk.LEFT)
        ttk.Button(top, text="导入钻孔...", command=self._import_drill).pack(side=tk.LEFT, padx=4)
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
        self.info = tk.StringVar(value="左键拖动=移动选中图层；右键拖动=平移；滚轮=缩放")
        ttk.Label(bar, textvariable=self.info, foreground="#555").pack(side=tk.RIGHT)

        body = ttk.Frame(self.win)
        body.pack(fill=tk.BOTH, expand=True)

        left = ttk.LabelFrame(body, text="图层", padding=4)
        left.pack(side=tk.LEFT, fill=tk.Y)
        self.listbox = tk.Listbox(left, width=34, height=30, activestyle="dotbox",
                                  font=("Consolas", 9), exportselection=False)
        self.listbox.pack(side=tk.LEFT, fill=tk.Y)
        sb = ttk.Scrollbar(left, orient=tk.VERTICAL, command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.listbox.bind("<<ListboxSelect>>", self._on_select)
        self.listbox.bind("<Double-Button-1>", self._toggle_visible)

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
    def add_layer(self, path, color=None, name=None):
        data = parse_gerber(path, layer_name=name or os.path.basename(path))
        if color is None:
            color = _PALETTE[len(self.layers) % len(_PALETTE)]
        layer = GerberLayer(name or os.path.basename(path), data, color)
        self.layers.append(layer)
        self._refresh_list()
        self.selected = len(self.layers) - 1
        self._sync_selection()
        self.fit()
        return layer

    def add_drill(self, path):
        data = parse_excellon(path)
        color = "#ffffff"
        layer = GerberLayer(os.path.basename(path), data, color)
        layer.kind = "drill"
        self.layers.append(layer)
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
        for p in paths:
            try:
                self.add_layer(p)
            except Exception as exc:  # noqa: BLE001
                messagebox.showerror("导入失败", f"{p}\n{exc}")

    def _import_drill(self):
        paths = filedialog.askopenfilenames(title="导入钻孔 (NC Drill)",
                                            filetypes=[("Drill", "*.drl *.txt"), ("All files", "*.*")])
        for p in paths:
            try:
                self.add_drill(p)
            except Exception as exc:  # noqa: BLE001
                messagebox.showerror("导入失败", f"{p}\n{exc}")

    def _compare_load(self, tag):
        path = filedialog.askopenfilename(title=f"对比: 导入 Gerber {tag}",
                                          filetypes=[("Gerber", "*.gbr *.ger *.art *.pho"), ("All files", "*.*")])
        if not path:
            return
        color = "#e53935" if tag == "A" else "#1e88e5"
        layer = self.add_layer(path, color=color, name=f"[{tag}] {os.path.basename(path)}")
        layer.mirror_x = (tag == "B")
        self._refresh_list()
        self.fit()
        self.info.set(f"对比模式: A=红 B=蓝; 选中图层后左键拖动即可重叠对齐")

    def _clear(self):
        self.layers = []
        self.selected = None
        self._refresh_list()
        self._render()

    def _refresh_list(self):
        self.listbox.delete(0, tk.END)
        for i, ly in enumerate(self.layers):
            mark = "●" if ly.visible else "○"
            self.listbox.insert(tk.END, f"{mark} {ly.name}  ({ly.color})")
        if self.selected is not None and 0 <= self.selected < len(self.layers):
            self.listbox.selection_set(self.selected)
            self.listbox.itemconfig(self.selected, fg=self.layers[self.selected].color)

    def _on_select(self, _e=None):
        sel = self.listbox.curselection()
        if sel:
            self.selected = sel[0]
            self._render()

    def _sync_selection(self):
        self.listbox.selection_clear(0, tk.END)
        if self.selected is not None and 0 <= self.selected < len(self.layers):
            self.listbox.selection_set(self.selected)
            self.listbox.see(self.selected)

    def _toggle_visible(self, _e=None):
        if self.selected is not None:
            self.layers[self.selected].visible = not self.layers[self.selected].visible
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
            self._render()

    def _reset_sel(self):
        ly = self._sel_layer()
        if ly:
            ly.offset = [0.0, 0.0]
            ly.mirror_x = False
            self._render()

    def _mirror_sel(self):
        ly = self._sel_layer()
        if ly:
            ly.mirror_x = not ly.mirror_x
            self._render()

    # ---------------- 变换 ----------------
    def _bbox_all(self):
        boxes = [ly.bbox() for ly in self.layers if ly.visible and ly.bbox()]
        if not boxes:
            return (0.0, 0.0, 100.0, 100.0)
        return (min(b[0] for b in boxes), min(b[1] for b in boxes),
                max(b[2] for b in boxes), max(b[3] for b in boxes))

    def _scale(self):
        minx, miny, maxx, maxy = self._bbox_all()
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
        self.zoom = 1.0
        self.pan = [0.0, 0.0]
        self._render()

    def _zoom(self, factor):
        self.zoom *= factor
        self._render()

    # ---------------- 交互 ----------------
    def _on_press(self, event):
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
            self._render()

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

    def _draw_layer(self, cv, ly, selected=False):
        color = ly.color
        dx, dy = ly.offset
        for p in (ly.data.get("primitives") or ly.data.get("features") or []):
            t = p.get("type")
            def T(px, py):
                if px is None:
                    return None
                if ly.mirror_x:
                    px = -px
                return self._to_canvas(px + dx, py + dy)
            x1 = p.get("x1"); y1 = p.get("y1"); x2 = p.get("x2"); y2 = p.get("y2")
            if t in ("draw", "flash") or (t not in ("arc", "region", "error")):
                if x1 is None:
                    continue
                c1 = T(x1, y1); c2 = T(x2 if x2 is not None else x1, y2 if y2 is not None else y1)
                if t == "flash":
                    w = p.get("width") or 0.2
                    r = max(1.0, w / 2 * self._scale()[0])
                    cv.create_oval(c1[0] - r, c1[1] - r, c1[0] + r, c1[1] + r, fill=color, outline="")
                else:
                    cv.create_line(c1[0], c1[1], c2[0], c2[1], fill=color)
            elif t == "arc":
                pts = self._arc_points(p)
                if len(pts) >= 2:
                    flat = []
                    for px, py in pts:
                        cx, cy = T(px, py)
                        flat.extend([cx, cy])
                    cv.create_line(*flat, fill=color)
            elif t == "region":
                flat = []
                for px, py in p.get("points", []):
                    cx, cy = T(px, py)
                    flat.extend([cx, cy])
                if len(flat) >= 6:
                    cv.create_polygon(flat, fill=color, outline="", stipple="gray50")
        if ly.kind == "drill":
            for h in ly.data.get("holes", []):
                if h.get("x") is None:
                    continue
                cx, cy = T(h["x"], h["y"])
                r = max(1.0, (h.get("diameter_mm") or 0.3) / 2 * self._scale()[0])
                cv.create_oval(cx - r, cy - r, cx + r, cy + r, fill=color, outline="")
        if selected:
            b = ly.bbox()
            if b:
                c1 = self._to_canvas(b[0], b[3]); c2 = self._to_canvas(b[2], b[1])
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
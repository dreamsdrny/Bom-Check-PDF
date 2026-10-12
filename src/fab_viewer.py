"""
BRD-AI: 图形化交叉定位高亮 (Gerber/ODB++ 版图 + DFM 问题)
=============================================================
提供:
  - render_fab_svg(): 将制造几何 + DFM 问题渲染为 SVG (可离线输出/测试)
  - FabViewer: tkinter 版图查看器, 支持:
      * 缩放(滚轮)/平移(拖动)/图层显隐
      * 问题清单 <-> 版图 双向交叉定位:
          点击问题 -> 版图高亮并平移定位
          点击版图上的问题标记 -> 选中对应问题行
"""
import hashlib
import math
import os
from typing import Dict, List, Optional, Tuple

from src.dfm_rules import SEVERITY_COLOR

_PALETTE = ["#c00000", "#0a7a3a", "#b8860b", "#2f5496", "#7030a0",
            "#00838f", "#6d4c41", "#455a64", "#ad1457", "#33691e"]


def _layer_color(name: str) -> str:
    h = int(hashlib.md5(str(name).encode("utf-8")).hexdigest(), 16)
    return _PALETTE[h % len(_PALETTE)]


def _collect_bbox(fab_data: List[dict]) -> Optional[Tuple[float, float, float, float]]:
    boxes = [d.get("bbox") for d in fab_data if d.get("bbox") and d["bbox"][0] is not None]
    if not boxes:
        return None
    minx = min(b[0] for b in boxes)
    miny = min(b[1] for b in boxes)
    maxx = max(b[2] for b in boxes)
    maxy = max(b[3] for b in boxes)
    if maxx <= minx:
        maxx = minx + 1.0
    if maxy <= miny:
        maxy = miny + 1.0
    return minx, miny, maxx, maxy


def _iter_primitives(fab_data: List[dict]):
    for d in fab_data:
        if d["kind"] in ("gerber", "odbpp"):
            for p in d.get("primitives") or d.get("features") or []:
                yield d, p


def render_fab_svg(fab_data: List[dict], issues: Optional[List[dict]] = None,
                   output_path: Optional[str] = None,
                   highlight: Optional[int] = None,
                   width: int = 1100,
                   hidden_layers: Optional[set] = None) -> str:
    issues = issues or []
    hidden_layers = hidden_layers or set()
    bbox = _collect_bbox(fab_data)
    if bbox is None:
        svg = "<svg xmlns='http://www.w3.org/2000/svg' width='300' height='120'>" \
              "<text x='10' y='60'>No geometry</text></svg>"
        if output_path:
            _write(output_path, svg)
        return svg

    minx, miny, maxx, maxy = bbox
    pad = 5.0
    spanx = (maxx - minx) + 2 * pad
    spany = (maxy - miny) + 2 * pad
    scale = width / spanx
    height = int(spany * scale) + 20

    def tx(x):
        return (x - (minx - pad)) * scale + 10

    def ty(y):
        return height - ((y - (miny - pad)) * scale + 10)

    parts = [f"<svg xmlns='http://www.w3.org/2000/svg' width='{width}' height='{height}'>",
             f"<rect x='0' y='0' width='{width}' height='{height}' fill='#111'/>"]
    parts.append(f"<rect x='{tx(minx):.1f}' y='{ty(maxy):.1f}' width='{(maxx-minx)*scale:.1f}' "
                 f"height='{(maxy-miny)*scale:.1f}' fill='#1c1c1c' stroke='#555'/>")

    for d, p in _iter_primitives(fab_data):
        layer = p.get("layer", d.get("name", ""))
        if layer in hidden_layers:
            continue
        color = _layer_color(layer)
        t = p.get("type")
        if t in ("draw", "region_start"):
            if p.get("x1") is None or p.get("x2") is None:
                continue
            parts.append(f"<line x1='{tx(p['x1']):.1f}' y1='{ty(p['y1']):.1f}' "
                         f"x2='{tx(p['x2']):.1f}' y2='{ty(p['y2']):.1f}' "
                         f"stroke='{color}' stroke-width='1'/>")
        elif t == "arc":
            if p.get("x1") is None:
                continue
            parts.append(f"<line x1='{tx(p['x1']):.1f}' y1='{ty(p['y1']):.1f}' "
                         f"x2='{tx(p['x2']):.1f}' y2='{ty(p['y2']):.1f}' "
                         f"stroke='{color}' stroke-width='1' stroke-dasharray='3,2'/>")
        elif t == "flash":
            w = p.get("width") or 0.2
            r = max(1.0, w / 2 * scale)
            parts.append(f"<circle cx='{tx(p['x1']):.1f}' cy='{ty(p['y1']):.1f}' r='{r:.1f}' "
                         f"fill='{color}'/>")
        elif t == "region":
            pts = " ".join(f"{tx(px):.1f},{ty(py):.1f}" for px, py in p.get("points", []))
            if pts:
                parts.append(f"<polygon points='{pts}' fill='{color}' fill-opacity='0.5'/>")

    for d in fab_data:
        if d["kind"] == "drill":
            for h in d.get("holes", []):
                if h.get("x") is None:
                    continue
                r = max(1.0, (h.get("diameter_mm") or 0.3) / 2 * scale)
                parts.append(f"<circle cx='{tx(h['x']):.1f}' cy='{ty(h['y']):.1f}' r='{r:.1f}' "
                             f"fill='#000' stroke='#888'/>")

    for i, it in enumerate(issues):
        x, y = it.get("x"), it.get("y")
        if x is None or y is None:
            continue
        fill = "#" + SEVERITY_COLOR.get(it.get("severity"), "999999")
        hl = (i == highlight)
        r = 7 if hl else 4
        parts.append(f"<circle class='issue' data-idx='{i}' cx='{tx(x):.1f}' cy='{ty(y):.1f}' "
                     f"r='{r}' fill='{fill}' stroke='{'#fff' if hl else '#000'}' stroke-width='2'/>")
    parts.append("</svg>")
    svg = "".join(parts)
    if output_path:
        _write(output_path, svg)
    return svg


def _write(path: str, content: str):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def _iter_points(fab_data: List[dict]):
    for d, p in _iter_primitives(fab_data):
        if p.get("x1") is not None:
            yield p["x1"], p["y1"]
        if p.get("x2") is not None:
            yield p["x2"], p["y2"]


class FabViewer:
    """tkinter 版图查看器 + DFM 问题双向交叉定位"""

    def __init__(self, master, fab_data: List[dict], issues: List[dict],
                 title: str = "版图 DFM 交叉定位"):
        import tkinter as tk
        from tkinter import ttk

        self.tk = tk
        self.ttk = ttk
        self.fab_data = fab_data
        self.issues = issues
        self.bbox = _collect_bbox(fab_data) or (0, 0, 100, 100)
        self.zoom = 1.0
        self.offset = [0.0, 0.0]
        self.selected = None
        self.hidden_layers = set()

        self.win = tk.Toplevel(master)
        self.win.title(title)
        self.win.geometry("1100x720")

        toolbar = ttk.Frame(self.win, padding=6)
        toolbar.pack(fill=tk.X)
        ttk.Button(toolbar, text="放大", command=lambda: self._zoom(1.25)).pack(side=tk.LEFT)
        ttk.Button(toolbar, text="缩小", command=lambda: self._zoom(0.8)).pack(side=tk.LEFT, padx=4)
        ttk.Button(toolbar, text="适应窗口", command=self.fit).pack(side=tk.LEFT, padx=4)
        self.status = tk.StringVar(value="滚轮缩放 / 拖动平移 / 点击问题定位")
        ttk.Label(toolbar, textvariable=self.status).pack(side=tk.RIGHT)

        body = ttk.Frame(self.win)
        body.pack(fill=tk.BOTH, expand=True)

        self.canvas = tk.Canvas(body, bg="#111", highlightthickness=0)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.canvas.bind("<Button-1>", self._on_canvas_click)
        self.canvas.bind("<ButtonPress-3>", self._pan_start)
        self.canvas.bind("<B3-Motion>", self._pan_move)
        self.canvas.bind("<MouseWheel>", self._on_wheel)
        self.canvas.bind("<ButtonPress-2>", self._pan_start)
        self.canvas.bind("<B2-Motion>", self._pan_move)
        self.canvas.bind("<Configure>", lambda e: self.redraw())

        right = ttk.Frame(body, width=340)
        right.pack(side=tk.LEFT, fill=tk.Y)
        right.pack_propagate(False)
        ttk.Label(right, text="DFM 问题清单 (点击定位)").pack(anchor=tk.W)
        cols = ("sev", "rule")
        self.tree = ttk.Treeview(right, columns=cols, show="headings", height=26)
        self.tree.heading("sev", text="严重度")
        self.tree.heading("rule", text="问题")
        self.tree.column("sev", width=60, anchor=tk.CENTER)
        self.tree.column("rule", width=250)
        tsb = ttk.Scrollbar(right, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=tsb.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tsb.pack(side=tk.RIGHT, fill=tk.Y)
        for sev, color in (("危险", "#c00000"), ("高", "#d2691e"),
                           ("中", "#b8860b"), ("低", "#33691e")):
            self.tree.tag_configure(sev, foreground=color)
        for i, it in enumerate(issues):
            label = f"{it.get('rule','')} [{it.get('code','')}]"
            if it.get("x") is not None:
                label += f" @({it['x']:.1f},{it['y']:.1f})"
            self.tree.insert("", tk.END, iid=str(i),
                             values=(it.get("severity", ""), label),
                             tags=(it.get("severity", ""),))
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)

        self._item_to_issue = {}
        self.fit()

    # ---- transform ----
    def _compute_scale(self):
        minx, miny, maxx, maxy = self.bbox
        w = self.canvas.winfo_width() or 800
        h = self.canvas.winfo_height() or 600
        base = min(w / max(maxx - minx, 1e-6), h / max(maxy - miny, 1e-6)) * 0.9
        return base * self.zoom

    def _to_canvas(self, x, y):
        minx, miny, maxx, maxy = self.bbox
        s = self._compute_scale()
        w = self.canvas.winfo_width() or 800
        h = self.canvas.winfo_height() or 600
        cx = (x - (minx + maxx) / 2) * s + w / 2 + self.offset[0]
        cy = h / 2 - (y - (miny + maxy) / 2) * s + self.offset[1]
        return cx, cy

    def fit(self):
        self.zoom = 1.0
        self.offset = [0.0, 0.0]
        self.redraw()

    def _zoom(self, factor):
        self.zoom *= factor
        self.redraw()

    def _on_wheel(self, event):
        self._zoom(1.2 if event.delta > 0 else 0.8)

    def _pan_start(self, event):
        self._pan_anchor = (event.x, event.y, self.offset[0], self.offset[1])

    def _pan_move(self, event):
        ax, ay, ox, oy = self._pan_anchor
        self.offset = [ox + (event.x - ax), oy + (event.y - ay)]
        self.canvas.move("all", event.x - ax, event.y - ay)
        self._pan_anchor = (event.x, event.y, self.offset[0], self.offset[1])

    # ---- draw ----
    def redraw(self):
        c = self.canvas
        c.delete("all")
        self._item_to_issue = {}
        color_palette = {}
        for d, p in _iter_primitives(self.fab_data):
            layer = p.get("layer", d.get("name", ""))
            if layer in self.hidden_layers:
                continue
            color = color_palette.setdefault(layer, _layer_color(layer))
            t = p.get("type")
            if t in ("draw", "arc") and p.get("x1") is not None:
                x1, y1 = self._to_canvas(p["x1"], p["y1"])
                x2, y2 = self._to_canvas(p.get("x2", p["x1"]), p.get("y2", p["y1"]))
                c.create_line(x1, y1, x2, y2, fill=color)
            elif t == "flash" and p.get("x1") is not None:
                x, y = self._to_canvas(p["x1"], p["y1"])
                r = max(1.5, (p.get("width") or 0.2) / 2 * self._compute_scale())
                c.create_oval(x - r, y - r, x + r, y + r, fill=color, outline="")
            elif t == "region" and p.get("points"):
                coords = []
                for px, py in p["points"]:
                    cx, cy = self._to_canvas(px, py)
                    coords.extend([cx, cy])
                if len(coords) >= 6:
                    c.create_polygon(coords, fill=color, outline="", stipple="gray50")
        for d in self.fab_data:
            if d["kind"] != "drill":
                continue
            for h in d.get("holes", []):
                if h.get("x") is None:
                    continue
                x, y = self._to_canvas(h["x"], h["y"])
                r = max(1.5, (h.get("diameter_mm") or 0.3) / 2 * self._compute_scale())
                c.create_oval(x - r, y - r, x + r, y + r, fill="#000", outline="#888")
        for i, it in enumerate(self.issues):
            if it.get("x") is None:
                continue
            x, y = self._to_canvas(it["x"], it["y"])
            hl = (i == self.selected)
            r = 8 if hl else 5
            fill = "#" + SEVERITY_COLOR.get(it.get("severity"), "999999")
            item = c.create_oval(x - r, y - r, x + r, y + r,
                                 fill=fill, outline="#fff" if hl else "#000", width=2)
            self._item_to_issue[item] = i

    # ---- cross-probing ----
    def _on_tree_select(self, _event):
        sel = self.tree.selection()
        if not sel:
            return
        self.select_issue(int(sel[0]), focus=True)

    def select_issue(self, idx: int, focus: bool = False):
        self.selected = idx
        if focus:
            it = self.issues[idx]
            if it.get("x") is not None:
                minx, miny, maxx, maxy = self.bbox
                w = self.canvas.winfo_width() or 800
                h = self.canvas.winfo_height() or 600
                s = self._compute_scale()
                self.offset = [
                    -(it["x"] - (minx + maxx) / 2) * s,
                    (it["y"] - (miny + maxy) / 2) * s,
                ]
        self.redraw()
        if str(idx) in self.tree.get_children():
            self.tree.selection_set(str(idx))
            self.tree.see(str(idx))
        it = self.issues[idx]
        self.status.set(f"问题 {idx+1}: [{it.get('severity')}] {it.get('rule')} - "
                        f"{it.get('description','')}")

    def _on_canvas_click(self, event):
        items = self.canvas.find_overlapping(event.x - 4, event.y - 4,
                                             event.x + 4, event.y + 4)
        for item in reversed(items):
            if item in self._item_to_issue:
                self.select_issue(self._item_to_issue[item], focus=False)
                return
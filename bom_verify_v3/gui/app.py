"""V3 GUI - Matches original bom_pdf_verify.py layout.

3 comparison types: Excel vs Excel | Excel vs PDF | PDF vs PDF
Sub-modes within Excel vs PDF: 位号核对(推荐) | 全文匹配 | 位号对比
"""

import json
import os
import sys
import time
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from threading import Thread

from ..models import CompareMode, CompareResult, CompareStats, CompareStatus
from ..comparators import run_comparison
from ..reports import generate_report, MODE_COLUMNS
from .theme import Theme


# ---------- DPI & Config ----------
def _cfg_path():
    return os.path.join(os.path.expanduser("~"), ".bom_verify_v3_config.json")


def _load_config():
    try:
        with open(_cfg_path(), "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_config(cfg):
    try:
        with open(_cfg_path(), "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _enable_dpi():
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            try:
                import ctypes
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass


def _detect_scaling(root):
    try:
        import ctypes
        dpi = float(ctypes.windll.shcore.GetScaleFactorForDevice(0))
    except Exception:
        dpi = 96.0
    scale = dpi / 96.0
    try:
        root.tk.call("tk", "scaling", scale)
    except Exception:
        pass
    return scale


def _f(*size_weight):
    size = size_weight[0]
    weight = size_weight[1] if len(size_weight) > 1 else "normal"
    return ("Segoe UI", int(size), weight)


def _style(theme, scale=1.0):
    s = ttk.Style()
    try:
        s.theme_use("clam")
    except Exception:
        pass
    base = max(10, round(10 * scale))
    s.configure(".", background=theme.BG, foreground=theme.TEXT, font=_f(base))
    s.configure("Card.TFrame", background=theme.CARD)
    s.configure("Card.TLabelframe", background=theme.CARD, bordercolor=theme.LINE,
                relief="solid", borderwidth=1)
    s.configure("Card.TLabelframe.Label", background=theme.CARD,
                foreground=theme.BRAND_DK, font=_f(base, "bold"))
    s.configure("TLabel", background=theme.BG, foreground=theme.TEXT, font=_f(base))
    s.configure("CLabel.TLabel", background=theme.CARD, font=_f(base))
    s.configure("Sub.TLabel", background=theme.CARD, foreground=theme.SUBTEXT,
                font=_f(max(9, base - 1)))
    s.configure("Title.TLabel", background=theme.CARD, foreground=theme.BRAND_DK,
                font=_f(max(13, round(base * 1.3)), "bold"))
    s.configure("Count.TLabel", background=theme.CARD, font=_f(max(11, base), "bold"))
    s.configure("OK.TLabel", background=theme.CARD, foreground=theme.OK_FG, font=_f(base, "bold"))
    s.configure("BAD.TLabel", background=theme.CARD, foreground=theme.BAD_FG, font=_f(base, "bold"))
    s.configure("TButton", background="#E7EDF6", foreground=theme.TEXT, bordercolor=theme.LINE,
                padding=(12, 6), font=_f(base))
    s.map("TButton", background=[("active", "#D5E2F4")])
    s.configure("Primary.TButton", background=theme.BRAND, foreground="#FFFFFF",
                bordercolor=theme.BRAND, padding=(14, 6), font=_f(base, "bold"))
    s.map("Primary.TButton", background=[("active", theme.BRAND_DK)])
    s.configure("TEntry", fieldbackground="#FFF", foreground=theme.TEXT,
                bordercolor=theme.LINE, font=_f(base))
    s.configure("TCombobox", fieldbackground="#FFF", foreground=theme.TEXT,
                bordercolor=theme.LINE, arrowcolor=theme.BRAND, font=_f(base))
    s.configure("Treeview", background="#FFFFFF", fieldbackground="#FFFFFF",
                foreground=theme.TEXT, rowheight=max(26, round(26 * scale)),
                bordercolor=theme.LINE, font=_f(base))
    s.configure("Treeview.Heading", background=theme.BRAND_LT, foreground=theme.BRAND_DK,
                font=_f(base, "bold"), relief="flat")
    s.map("Treeview", background=[("selected", "#BFD8F2")])
    s.configure("Vertical.TScrollbar", background="#DAE4F0", troughcolor=theme.BG,
                arrowcolor=theme.BRAND_DK)


def _make_card(parent, title, padding=(10, 8, 10, 8)):
    return ttk.LabelFrame(parent, text=title, style="Card.TLabelframe", padding=padding)


# ---------- Mode definitions ----------
COMPARE_TYPES = ["Excel vs Excel", "Excel vs PDF", "PDF vs PDF"]
SUB_MODES_EXCEL_PDF = ["位号核对(推荐)", "全文匹配", "位号对比"]
SUB_MODES_EXCEL_EXCEL = ["MPN对比"]
SUB_MODES_PDF_PDF = ["位号对比"]


def _app_mode_to_v3_mode(compare_type, sub_mode):
    """Map GUI compare type + sub mode to V3 CompareMode."""
    if compare_type == "Excel vs PDF":
        if sub_mode == "全文匹配":
            return CompareMode.EXCEL_VS_PDF
        return CompareMode.EXCEL_VS_PDF
    elif compare_type == "Excel vs Excel":
        return CompareMode.EXCEL_VS_EXCEL_MPN
    elif compare_type == "PDF vs PDF":
        return CompareMode.PDF_VS_PDF
    return CompareMode.EXCEL_VS_PDF


def run_gui():
    _enable_dpi()
    root = tk.Tk()
    root.title("BOM ↔ PDF 原理图 器件核对工具 V3")
    scale = _detect_scaling(root)
    _style(Theme, scale)

    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    W = min(int(1280 * scale), sw - 40)
    H = min(int(860 * scale), sh - 80)
    root.geometry(f"{W}x{H}")
    root.minsize(960, 640)
    root.configure(bg=Theme.BG, padx=6, pady=6)

    # ---------- App state ----------
    app = {
        "path_a": "", "path_b": "",
        "results": [], "stats": None,
        "compare_type": "Excel vs PDF",
        "sub_mode": "位号核对(推荐)",
    }
    label_a = tk.StringVar()
    label_b = tk.StringVar()
    var_compare = tk.StringVar(value="Excel vs PDF")
    var_sub = tk.StringVar(value="位号核对(推荐)")
    var_status = tk.StringVar(value="就绪：请选择对比类型与文件，然后点击「开始核对」")

    # ---------- Config restore ----------
    cfg = _load_config()
    for key, var, apk in (("path_a", label_a, "path_a"), ("path_b", label_b, "path_b")):
        p = cfg.get(key)
        if p and os.path.exists(p):
            var.set(os.path.basename(p))
            app[apk] = p
    if cfg.get("compare_type"):
        app["compare_type"] = cfg["compare_type"]
        var_compare.set(cfg["compare_type"])
    if cfg.get("sub_mode"):
        app["sub_mode"] = cfg["sub_mode"]
        var_sub.set(cfg["sub_mode"])

    # ---------- File label helpers ----------
    def _label_a_text():
        ct = app["compare_type"]
        if ct == "Excel vs PDF":
            return "BOM (Excel):"
        return "文件A:"

    def _label_b_text():
        ct = app["compare_type"]
        if ct == "Excel vs PDF":
            return "PDF 原理图:"
        return "文件B:"

    def _file_types(key):
        ct = app["compare_type"]
        if ct == "Excel vs PDF":
            if key == "a":
                return [("Excel", "*.xlsx;*.xls;*.csv;*.tsv;*.txt;*.xlsm")]
            return [("PDF", "*.pdf")]
        if ct == "PDF vs PDF":
            return [("PDF", "*.pdf")]
        return [("Excel", "*.xlsx;*.xls;*.csv;*.tsv;*.txt;*.xlsm")]

    # ---------- Mode change ----------
    def _on_compare_change(*_):
        app["compare_type"] = var_compare.get()
        ct = app["compare_type"]
        if ct == "Excel vs PDF":
            sub_cb["values"] = SUB_MODES_EXCEL_PDF
            var_sub.set("位号核对(推荐)")
            app["sub_mode"] = "位号核对(推荐)"
        elif ct == "Excel vs Excel":
            sub_cb["values"] = SUB_MODES_EXCEL_EXCEL
            var_sub.set("MPN对比")
            app["sub_mode"] = "MPN对比"
        else:
            sub_cb["values"] = SUB_MODES_PDF_PDF
            var_sub.set("位号对比")
            app["sub_mode"] = "位号对比"
        _update_labels()
        _save_config({"compare_type": app["compare_type"], "sub_mode": app["sub_mode"],
                       "path_a": app["path_a"], "path_b": app["path_b"]})

    def _on_sub_change(*_):
        app["sub_mode"] = var_sub.get()
        _save_config({"compare_type": app["compare_type"], "sub_mode": app["sub_mode"],
                       "path_a": app["path_a"], "path_b": app["path_b"]})

    def _update_labels():
        la.config(text=_label_a_text())
        lb.config(text=_label_b_text())

    def _pick_file(key):
        path = filedialog.askopenfilename(filetypes=_file_types(key))
        if path:
            if key == "a":
                app["path_a"] = path
                label_a.set(os.path.basename(path))
            else:
                app["path_b"] = path
                label_b.set(os.path.basename(path))
            _save_config({"compare_type": app["compare_type"], "sub_mode": app["sub_mode"],
                          "path_a": app["path_a"], "path_b": app["path_b"]})

    def _populate_tree():
        tree.delete(*tree.get_children())
        mode = _app_mode_to_v3_mode(app["compare_type"], app["sub_mode"])
        mode_cfg = MODE_COLUMNS.get(mode, MODE_COLUMNS[CompareMode.EXCEL_VS_PDF])
        cols = mode_cfg["columns"]
        from ..reports.generator import _resolve_value
        for r in app["results"]:
            vals = [_resolve_value(r, kp) for _, kp in cols]
            tag = "ok" if r.is_match else "bad"
            tree.insert("", "end", values=vals, tags=(tag,))

    def _rebuild_tree_columns():
        mode = _app_mode_to_v3_mode(app["compare_type"], app["sub_mode"])
        mode_cfg = MODE_COLUMNS.get(mode, MODE_COLUMNS[CompareMode.EXCEL_VS_PDF])
        cols = mode_cfg["columns"]
        col_ids = [f"col{i}" for i in range(len(cols))]
        tree["columns"] = col_ids
        for i, (h, _) in enumerate(cols):
            tree.heading(col_ids[i], text=h)
            tree.column(col_ids[i], width=120, minwidth=60)
        tree.delete(*tree.get_children())

    # ---------- Run comparison ----------
    def _run():
        if not app["path_a"] or not app["path_b"]:
            messagebox.showwarning("提示", "请先选择两个文件")
            return
        btn_run.config(state="disabled")
        var_status.set("正在对比...")
        progress["value"] = 0
        root.update_idletasks()

        def work():
            try:
                progress["value"] = 30
                root.update_idletasks()
                mode = _app_mode_to_v3_mode(app["compare_type"], app["sub_mode"])
                results, stats = run_comparison(mode, app["path_a"], app["path_b"])
                progress["value"] = 80
                root.update_idletasks()
                app["results"] = results
                app["stats"] = stats
                root.after(0, _on_complete)
            except Exception as e:
                import traceback
                root.after(0, lambda: _on_error(str(e) + "\n" + traceback.format_exc()))

        Thread(target=work, daemon=True).start()

    def _on_complete():
        _rebuild_tree_columns()
        _populate_tree()
        btn_run.config(state="normal")
        progress["value"] = 100
        stats = app["stats"]
        if stats:
            s = stats.summary
            var_status.set(
                f"完成: 一致={stats.matched} 不一致={stats.field_mismatch + stats.designator_diff} "
                f"仅A={stats.only_a} 仅B={stats.only_b} 待确认={stats.pending}"
            )

    def _on_error(msg):
        btn_run.config(state="normal")
        progress["value"] = 0
        var_status.set("出错")
        messagebox.showerror("错误", msg[:500])

    def _export():
        if not app["results"] or not app["stats"]:
            messagebox.showinfo("提示", "请先执行对比")
            return
        out_dir = filedialog.askdirectory(title="选择输出目录")
        if not out_dir:
            return
        try:
            xlsx, txt = generate_report(
                app["results"], app["stats"], out_dir,
                source_label=os.path.basename(app["path_a"]),
                path_a=app["path_a"], path_b=app["path_b"],
            )
            messagebox.showinfo("完成", f"报告已生成:\n{xlsx}\n{txt}")
        except Exception as e:
            messagebox.showerror("错误", str(e))

    # ========== Layout ==========
    # Top bar
    top = ttk.Frame(root, style="Card.TFrame", padding=8)
    top.pack(fill="x", pady=(0, 4))

    ttk.Label(top, text="BOM ↔ PDF 原理图 器件核对工具 V3",
              style="Title.TLabel").pack(side="left")
    ttk.Button(top, text="导出报告", command=_export,
               style="Primary.TButton").pack(side="right", padx=4)

    # Settings card
    set_card = _make_card(root, "对比设置")
    set_card.pack(fill="x", pady=(0, 4))

    r0 = ttk.Frame(set_card, style="Card.TFrame")
    r0.pack(fill="x", pady=2)
    ttk.Label(r0, text="对比类型:", style="CLabel.TLabel", width=10).pack(side="left")
    ct_cb = ttk.Combobox(r0, textvariable=var_compare, values=COMPARE_TYPES,
                          state="readonly", width=22)
    ct_cb.pack(side="left", padx=6)
    ct_cb.bind("<<ComboboxSelected>>", _on_compare_change)

    ttk.Label(r0, text="核对方式:", style="CLabel.TLabel", width=10).pack(side="left", padx=(20, 0))
    sub_cb = ttk.Combobox(r0, textvariable=var_sub, values=SUB_MODES_EXCEL_PDF,
                           state="readonly", width=22)
    sub_cb.pack(side="left", padx=6)
    sub_cb.bind("<<ComboboxSelected>>", _on_sub_change)

    # File selection card
    file_card = _make_card(root, "文件选择")
    file_card.pack(fill="x", pady=(0, 4))

    f1 = ttk.Frame(file_card, style="Card.TFrame")
    f1.pack(fill="x", pady=2)
    la = ttk.Label(f1, text=_label_a_text(), style="CLabel.TLabel", width=12)
    la.pack(side="left")
    ttk.Label(f1, textvariable=label_a, style="CLabel.TLabel",
              foreground=Theme.BRAND_DK, font=_f(10, "bold")).pack(side="left", padx=6)
    ttk.Button(f1, text="选择...", command=lambda: _pick_file("a")).pack(side="left", padx=4)

    f2 = ttk.Frame(file_card, style="Card.TFrame")
    f2.pack(fill="x", pady=2)
    lb = ttk.Label(f2, text=_label_b_text(), style="CLabel.TLabel", width=12)
    lb.pack(side="left")
    ttk.Label(f2, textvariable=label_b, style="CLabel.TLabel",
              foreground=Theme.BRAND_DK, font=_f(10, "bold")).pack(side="left", padx=6)
    ttk.Button(f2, text="选择...", command=lambda: _pick_file("b")).pack(side="left", padx=4)

    btn_run = ttk.Button(file_card, text="开始核对", command=_run,
                          style="Primary.TButton")
    btn_run.pack(pady=(6, 2))

    progress = ttk.Progressbar(file_card, mode="determinate", maximum=100)
    progress.pack(fill="x", pady=(2, 0))

    # Results card
    result_card = _make_card(root, "对比结果")
    result_card.pack(fill="both", expand=True, pady=(0, 4))

    tree_frame = ttk.Frame(result_card)
    tree_frame.pack(fill="both", expand=True)

    tree = ttk.Treeview(tree_frame, show="headings")
    vs = ttk.Scrollbar(tree_frame, orient="vertical", command=tree.yview)
    hs = ttk.Scrollbar(tree_frame, orient="horizontal", command=tree.xview)
    tree.configure(yscrollcommand=vs.set, xscrollcommand=hs.set)
    tree.grid(row=0, column=0, sticky="nsew")
    vs.grid(row=0, column=1, sticky="ns")
    hs.grid(row=1, column=0, sticky="ew")
    tree_frame.rowconfigure(0, weight=1)
    tree_frame.columnconfigure(0, weight=1)

    tree.tag_configure("ok", background=Theme.OK_BG, foreground=Theme.OK_FG)
    tree.tag_configure("bad", background=Theme.BAD_BG, foreground=Theme.BAD_FG)

    _rebuild_tree_columns()

    # Status bar
    status_bar = ttk.Frame(root, style="Card.TFrame", padding=(8, 4))
    status_bar.pack(fill="x", side="bottom")
    ttk.Label(status_bar, textvariable=var_status, style="Sub.TLabel").pack(side="left")

    root.mainloop()
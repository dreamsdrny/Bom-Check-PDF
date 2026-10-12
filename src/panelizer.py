"""
BRD-AI: 自动拼板与成本优化 (Panelization & Cost Optimization)
=============================================================
功能:
  - 依据单板尺寸/需求数量/工作板(拼板)尺寸, 自动计算行列排布 (支持 0/90 旋转)
  - 自动添加工艺边 (rail)、Mark 点、定位孔 (Tooling Hole), 可选邮票孔/V-Cut 标注
  - 计算板材利用率, 预估所需拼板数量与相对成本, 输出综合改善建议
  - 输出 placements (每个单板在拼板中的坐标), 供图形化预览/报告

输入参数 (dict, 长度单位 mm):
  board_w, board_h            单板尺寸
  board_qty                   需求数量 (默认 1)
  panel_w, panel_h            最大工作板尺寸 (默认 544.068 x 620.268)
  rail_mm                     工艺边宽度 (默认 4)
  gap_mm                      单板间距 (默认 1.6)
  allow_rotate                是否允许 90° 旋转 (默认 True)
  mark_count                  每面 Mark 点数 (默认 3)
  mark_to_edge_mm             Mark 到工艺边距离 (默认 3.5)
  tooling_hole_count          定位孔数量 (默认 2)
  min_utilization             可接受的最低利用率 (默认 0.7)
"""
import math
from typing import Dict, List, Optional

from src.dfm_rules import make_issue

PANEL_DEFAULTS = {
    "panel_w": 544.068,
    "panel_h": 620.268,
    "rail_mm": 4.0,
    "gap_mm": 1.6,
    "allow_rotate": True,
    "mark_count": 3,
    "mark_to_edge_mm": 3.5,
    "tooling_hole_count": 2,
    "tooling_hole_mm": 3.0,
    "min_utilization": 0.7,
}


def _num(v, default=None):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _fit(panel_usable_w, panel_usable_h, bw, bh, gap):
    nx = int(math.floor((panel_usable_w + gap) / (bw + gap))) if bw + gap > 0 else 0
    ny = int(math.floor((panel_usable_h + gap) / (bh + gap))) if bh + gap > 0 else 0
    nx = max(0, nx)
    ny = max(0, ny)
    return nx, ny


def optimize_panel(params: dict) -> dict:
    p = dict(PANEL_DEFAULTS)
    p.update({k: v for k, v in (params or {}).items() if v is not None})

    bw = _num(p.get("board_w"))
    bh = _num(p.get("board_h"))
    panel_w = _num(p.get("panel_w"))
    panel_h = _num(p.get("panel_h"))
    rail = _num(p.get("rail_mm"), 4.0)
    gap = _num(p.get("gap_mm"), 1.6)
    qty = int(_num(p.get("board_qty"), 1) or 1)

    if not bw or not bh or not panel_w or not panel_h:
        return {
            "ok": False,
            "error": "缺少 board_w/board_h/panel_w/panel_h",
            "issues": [make_issue(code="PNL-00", category="拼板", rule="参数缺失",
                                  severity="高", description="拼板参数不完整",
                                  suggestion="提供单板与拼板尺寸")],
        }

    usable_w = panel_w - 2 * rail
    usable_h = panel_h - 2 * rail

    candidates = []
    orientations = [(bw, bh, False)]
    if p.get("allow_rotate", True) and abs(bw - bh) > 1e-6:
        orientations.append((bh, bw, True))
    for w, h, rotated in orientations:
        nx, ny = _fit(usable_w, usable_h, w, h, gap)
        count = nx * ny
        util = (count * w * h) / (panel_w * panel_h) if panel_w * panel_h else 0
        candidates.append({
            "rotated": rotated, "nx": nx, "ny": ny, "count": count,
            "utilization": round(util, 4), "cell_w": w, "cell_h": h,
        })

    candidates.sort(key=lambda c: (c["count"], c["utilization"]), reverse=True)
    best = candidates[0]
    best = dict(best)

    placements = []
    if best["count"] > 0:
        origin_x = rail
        origin_y = rail
        for iy in range(best["ny"]):
            for ix in range(best["nx"]):
                placements.append({
                    "row": iy, "col": ix,
                    "x": round(origin_x + ix * (best["cell_w"] + gap), 3),
                    "y": round(origin_y + iy * (best["cell_h"] + gap), 3),
                    "w": best["cell_w"], "h": best["cell_h"],
                    "rotated": best["rotated"],
                })

    pcs_per_panel = best["count"]
    panels_needed = math.ceil(qty / pcs_per_panel) if pcs_per_panel else None
    leftover = (panels_needed * pcs_per_panel - qty) if panels_needed else None

    fids, toolings = _add_process_features(panel_w, panel_h, rail, p, best)

    cost_index = round(1.0 / best["utilization"], 3) if best["utilization"] else None

    issues = []
    if pcs_per_panel == 0:
        issues.append(make_issue(code="PNL-01", category="拼板", rule="板尺寸",
                                 severity="危险",
                                 description=f"单板 {bw}x{bh}mm 无法放入工作板 {panel_w}x{panel_h}mm",
                                 measured=f"{bw}x{bh}", required=f"<= {usable_w}x{usable_h}",
                                 unit="mm", suggestion="缩小单板或选用更大工作板"))
    else:
        if best["utilization"] < _num(p.get("min_utilization"), 0.7):
            issues.append(make_issue(code="PNL-02", category="拼板", rule="板材利用率",
                                     severity="中",
                                     description=f"板材利用率仅 {best['utilization']*100:.1f}% "
                                                 f"({pcs_per_panel} pcs/拼板)",
                                     measured=best["utilization"],
                                     required=_num(p.get("min_utilization"), 0.7),
                                     unit="ratio",
                                     suggestion="调整排布/旋转或选用合适工作板以降低成本"))
        if best["nx"] == 1 and best["ny"] == 1:
            issues.append(make_issue(code="PNL-03", category="拼板", rule="拼板方式",
                                     severity="高",
                                     description="当前仅能单拼, 建议增加拼板数量以适配 SMT 传送轨道",
                                     measured="单拼", required="多拼",
                                     suggestion="优化单板尺寸或选更大工作板进行多拼"))
    if len(fids) < int(_num(p.get("mark_count"), 3)):
        issues.append(make_issue(code="PNL-04", category="拼板", rule="Mark点数量",
                                 severity="中",
                                 description=f"Mark 点空间不足, 仅能布置 {len(fids)} 个",
                                 suggestion="增大工艺边以放置 3 个 Mark 点"))
    if cost_index and cost_index > 1.5:
        issues.append(make_issue(code="PNL-05", category="拼板", rule="成本优化",
                                 severity="低",
                                 description=f"相对成本指数 {cost_index} (利用率倒数), 材料浪费较大",
                                 measured=cost_index, required=1.5,
                                 suggestion="提高板材利用率以降低单位成本"))

    return {
        "ok": pcs_per_panel > 0,
        "panel_w": panel_w, "panel_h": panel_h,
        "board_w": bw, "board_h": bh,
        "rail_mm": rail, "gap_mm": gap,
        "best": best,
        "candidates": candidates,
        "pcs_per_panel": pcs_per_panel,
        "panels_needed": panels_needed,
        "leftover": leftover,
        "utilization": best["utilization"],
        "cost_index": cost_index,
        "placements": placements,
        "mark_points": fids,
        "tooling_holes": toolings,
        "issues": issues,
        "recommendation": _recommend(best, pcs_per_panel, cost_index),
    }


def _add_process_features(panel_w, panel_h, rail, p, best):
    fids = []
    mt = _num(p.get("mark_to_edge_mm"), 3.5)
    radius = 1.0
    if rail >= mt:
        fids.append({"x": round(mt, 3), "y": round(mt, 3), "r": radius, "type": "mark"})
        fids.append({"x": round(panel_w - mt, 3), "y": round(mt, 3), "r": radius, "type": "mark"})
        fids.append({"x": round(panel_w - mt, 3), "y": round(panel_h - mt, 3), "r": radius, "type": "mark"})
    toolings = []
    tc = int(_num(p.get("tooling_hole_count"), 2))
    th = _num(p.get("tooling_hole_mm"), 3.0)
    if rail >= 3.0:
        cx = rail / 2
        ys = [panel_h * 0.25, panel_h * 0.75]
        for i in range(min(tc, len(ys))):
            toolings.append({"x": round(cx, 3), "y": round(ys[i], 3), "d": th, "type": "tooling"})
    return fids, toolings


def _recommend(best, pcs_per_panel, cost_index):
    if not pcs_per_panel:
        return "无可行拼板方案, 请调整单板或工作板尺寸"
    parts = [f"每拼板 {pcs_per_panel} pcs (阵列 {best['nx']}x{best['ny']}"
             + (", 旋转90°)" if best["rotated"] else ")")]
    parts.append(f"板材利用率 {best['utilization']*100:.1f}%")
    if cost_index:
        parts.append(f"相对成本指数 {cost_index}")
    return "; ".join(parts)


def panel_svg(result: dict) -> str:
    """将拼板结果渲染为 SVG 字符串 (用于预览/报告)"""
    if not result.get("ok"):
        return "<svg xmlns='http://www.w3.org/2000/svg' width='200' height='80'>" \
               "<text x='10' y='40'>No feasible panelization</text></svg>"
    pw, ph = result["panel_w"], result["panel_h"]
    scale = 800.0 / max(pw, ph)
    W, H = pw * scale + 20, ph * scale + 20
    parts = [f"<svg xmlns='http://www.w3.org/2000/svg' width='{W:.0f}' height='{H:.0f}'>"]
    parts.append(f"<rect x='10' y='10' width='{pw*scale:.1f}' height='{ph*scale:.1f}' "
                 f"fill='#f2f2f2' stroke='#333' stroke-width='1'/>")
    for pl in result["placements"]:
        x = 10 + pl["x"] * scale
        y = 10 + (ph - pl["y"] - pl["h"]) * scale
        parts.append(f"<rect x='{x:.1f}' y='{y:.1f}' width='{pl['w']*scale:.1f}' "
                     f"height='{pl['h']*scale:.1f}' fill='#c6e0b4' stroke='#548235'/>")
    for m in result.get("mark_points", []):
        cx = 10 + m["x"] * scale
        cy = 10 + (ph - m["y"]) * scale
        parts.append(f"<circle cx='{cx:.1f}' cy='{cy:.1f}' r='{m['r']*scale:.1f}' fill='#ffd966' stroke='#bf8f00'/>")
    for t in result.get("tooling_holes", []):
        cx = 10 + t["x"] * scale
        cy = 10 + (ph - t["y"]) * scale
        parts.append(f"<circle cx='{cx:.1f}' cy='{cy:.1f}' r='{t['d']/2*scale:.1f}' fill='#8ea9db' stroke='#2f5496'/>")
    parts.append("</svg>")
    return "".join(parts)
"""报告导出统一入口。

支持格式: ``excel`` (Valor 式多页工作簿, 内嵌版图截图) / ``html`` (交互式单文件) /
``text`` / ``json`` / ``svg`` (版图矢量图)。

::

    from dfm.report import export_report
    paths = export_report(analysis, "D:/out/report.xlsx")            # 仅 Excel(+同名 txt)
    paths = export_report(analysis, "D:/out", formats=["excel", "html", "json"])
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, Iterable, List, Optional

from dfm.report.html import generate_dfm_html
from dfm.report.writers import generate_dfm_excel, generate_dfm_text

FORMATS = ("excel", "html", "text", "json", "svg")
_EXT = {"excel": ".xlsx", "html": ".html", "text": ".txt", "json": ".json", "svg": ".svg"}
_BY_EXT = {v: k for k, v in _EXT.items()}

__all__ = ["FORMATS", "export_report", "generate_dfm_excel", "generate_dfm_text",
           "generate_dfm_html", "slim_analysis", "save_json", "save_svg"]


def slim_analysis(analysis: Dict[str, Any],
                  drop: Iterable[str] = ("fab_data",)) -> Dict[str, Any]:
    """去掉体积巨大的渲染几何, 保留全部结论字段 (JSON 导出用)。"""
    drop = set(drop)
    out = {k: v for k, v in analysis.items() if k not in drop}
    if "instances" in out:
        out["instance_count"] = len(out["instances"])
        out["instances"] = [
            {k: v for k, v in it.items() if k != "locations"}
            for it in out["instances"][:5000]
        ]
    for it in out.get("issues", []):
        if isinstance(it.get("locations"), list) and len(it["locations"]) > 500:
            it = dict(it)
            it["locations"] = it["locations"][:500]
            it["locations_truncated"] = True
    return out


def save_json(analysis: Dict[str, Any], path: str) -> str:
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(slim_analysis(analysis), f, indent=2, ensure_ascii=False, default=str)
    return path


def save_svg(analysis: Dict[str, Any], path: str, width: int = 1600) -> str:
    from dfm.report.svg import render_board_svg, save_svg as _save

    issues = [it for it in (analysis.get("instances") or analysis.get("issues") or [])
              if it.get("x") is not None]
    svg = render_board_svg(analysis.get("fab_data") or [], issues, width=width,
                           title=str((analysis.get("board_meta") or {}).get("board_name", "")))
    return _save(svg, path)


def export_report(analysis: Dict[str, Any], path: str,
                  formats: Optional[Iterable[str]] = None,
                  board_meta: Optional[dict] = None,
                  embed_images: bool = True) -> Dict[str, str]:
    """导出报告, 返回 {格式: 实际路径}。

    :param path: 输出文件路径 (扩展名决定格式) 或输出目录 (配合 formats)
    :param formats: 需要导出的格式列表; 为空时按 path 扩展名推断
    """
    board_meta = board_meta or analysis.get("board_meta") or {}
    fmts: List[str] = []
    if formats:
        fmts = [f.lower() for f in formats if str(f).lower() in _EXT]
    if not fmts:
        ext = os.path.splitext(path)[1].lower()
        fmts = [_BY_EXT[ext]] if ext in _BY_EXT else ["excel"]

    if os.path.splitext(path)[1].lower() in _BY_EXT:
        base = os.path.splitext(path)[0]
    else:
        base = os.path.join(path, str(board_meta.get("board_name") or "dfm_report"))
    os.makedirs(os.path.dirname(os.path.abspath(base)) or ".", exist_ok=True)

    out: Dict[str, str] = {}
    if "excel" in fmts:
        out["excel"] = generate_dfm_excel(analysis, base + ".xlsx", board_meta,
                                          embed_images=embed_images)
    if "html" in fmts:
        out["html"] = generate_dfm_html(analysis, base + ".html", board_meta)
    if "text" in fmts:
        out["text"] = generate_dfm_text(analysis, base + ".txt", board_meta)
    if "json" in fmts:
        out["json"] = save_json(analysis, base + ".json")
    if "svg" in fmts and analysis.get("fab_data"):
        out["svg"] = save_svg(analysis, base + ".svg")
    return out

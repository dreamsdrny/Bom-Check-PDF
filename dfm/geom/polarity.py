"""极性合成 — 处理 Gerber 负片图元 (``%LPC`` / ``%IP`` / ODB++ polarity)。

**为什么必须做**: 内层电源/地层几乎全是负片图形 (anti-pad、散热隔离环、
槽孔让位)，若把 clear 图元当成铜箔，会产生大量**假短路**与**假间距违规**；
反之若直接丢弃 clear 图元，负片平面的"挖空"效果又无法体现。

正确做法 (本模块): 同层 ``dark`` 图元的并集减去 ``clear`` 图元的并集,
逐元素裁剪 (保留每个元素自身的网络/位号归属, 供开短路与定位使用)::

    element.geom = element.shape.geom  −  union(clear_geoms of same layer)

被完全挖除的元素丢弃; 被部分挖除的元素标记 ``clipped=True`` (报告中可见)。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence

try:
    from shapely.ops import unary_union
except ImportError as exc:  # pragma: no cover
    raise ImportError("极性合成需要 shapely: pip install shapely") from exc

from dfm.geom.elements import POLARITY_CLEAR, Element

#: 单次布尔运算参与的最大 clear 图元数 (超出分块, 防止极端数据卡死)
CLEAR_CHUNK = 4000


@dataclass
class PolarityStats:
    """极性合成统计 (进入 analysis["data_quality"])。"""

    layers_with_clear: int = 0
    clear_elements: int = 0
    clipped_elements: int = 0
    dropped_elements: int = 0
    notes: List[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "含负片图形的层数": self.layers_with_clear,
            "负片(clear)图元数": self.clear_elements,
            "被部分挖除的铜箔": self.clipped_elements,
            "被完全挖除的铜箔": self.dropped_elements,
        }


def compose_layer(elements: Sequence[Element],
                  stats: Optional[PolarityStats] = None,
                  layer: str = "") -> List[Element]:
    """对**同一层**的元素做极性合成, 返回存活的铜箔元素 (clear 元素不返回)。

    :param elements: 同层元素 (dark + clear 混合)
    :param stats: 可选统计累加器
    :param layer: 层名 (仅用于统计信息)
    """
    stats = stats if stats is not None else PolarityStats()
    dark = [e for e in elements if e.polarity != POLARITY_CLEAR]
    clear = [e for e in elements if e.polarity == POLARITY_CLEAR]
    stats.clear_elements += len(clear)
    if layer and clear:
        stats.layers_with_clear += 1

    if not clear:
        # 无负片图形: 不预构建几何 (惰性), 让快路径走解析距离
        return list(dark)

    # clear 图元可能重叠, 先合并 (分块以控制单次运算规模)
    unions = []
    for i in range(0, len(clear), CLEAR_CHUNK):
        chunk = [_safe_geom(e) for e in clear[i:i + CLEAR_CHUNK]]
        chunk = [g for g in chunk if g is not None]
        if chunk:
            unions.append(unary_union(chunk) if len(chunk) > 1 else chunk[0])
    if not unions:
        return list(dark)          # 负片图形无有效几何, 同样保持惰性
    cut = unions[0] if len(unions) == 1 else unary_union(unions)
    try:
        cut_bbox = cut.bounds
    except Exception:
        cut_bbox = None

    survivors: List[Element] = []
    for e in dark:
        g = _safe_geom(e)
        if g is None:
            continue
        if cut_bbox is not None and not _bbox_hit(e, cut_bbox):
            e.geom = g
            survivors.append(e)
            continue
        try:
            diff = g.difference(cut)
        except Exception:
            e.geom = g
            survivors.append(e)
            continue
        if diff is None or diff.is_empty:
            stats.dropped_elements += 1
            continue
        if diff.area < g.area - 1e-12:
            stats.clipped_elements += 1
            e.clipped = True
        e.set_geometry(diff)
        survivors.append(e)
    return survivors


def _safe_geom(e: Element):
    try:
        g = e.shape.geom
        return None if g.is_empty else g
    except Exception:
        return None


def _bbox_hit(e: Element, bounds) -> bool:
    x0, y0, x1, y1 = bounds
    return not (e.maxx < x0 or e.minx > x1 or e.maxy < y0 or e.miny > y1)


def compose_all(elements: Iterable[Element],
                stats: Optional[PolarityStats] = None) -> Dict[str, List[Element]]:
    """按层分组做极性合成, 返回 {层名: 存活元素}。"""
    stats = stats if stats is not None else PolarityStats()
    by_layer: Dict[str, List[Element]] = {}
    for e in elements:
        by_layer.setdefault(e.layer, []).append(e)
    out: Dict[str, List[Element]] = {}
    for layer, items in by_layer.items():
        out[layer] = compose_layer(items, stats, layer)
    return out

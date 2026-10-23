"""几何内核 (Geometry Kernel) — DFM 判定的精度基础。

设计目标 (对应旧实现的三类缺陷):
  1. **真实形状**: 矩形/椭圆/多边形焊盘不再用外接圆近似, 走线用真实线宽,
     铺铜用真实多边形 → 消除系统性误报
  2. **极性合成**: Gerber 负片 (``%LPC`` / IP=clear) 图元作为"挖除"参与运算,
     内层平面/anti-pad/散热环不再被当成铜箔 → 消除假短路与假间距违规
  3. **共享索引**: 一次构建、多检查复用 (制造 DFM / 开短路 / 装配), 避免重复建表

依赖 shapely 2.x (GEOS) 做布尔与最近距离; 未安装时给出明确错误提示。
"""
from dfm.geom.board import BoardGeometry, GeometryStats
from dfm.geom.clearance import pair_clearance, pair_candidates
from dfm.geom.elements import (ELEMENT_HOLE, ELEMENT_PAD, ELEMENT_POUR,
                               ELEMENT_TRACE, ELEMENT_VIA, Element)
from dfm.geom.polarity import compose_layer
from dfm.geom.shapes import (SHAPE_ARC, SHAPE_CIRCLE, SHAPE_POLY, SHAPE_RECT,
                             SHAPE_ROUND, SHAPE_SEG, Shape)

HAS_SHAPELY = True

__all__ = [
                               "ELEMENT_HOLE",
                               "ELEMENT_PAD",
                               "ELEMENT_POUR",
                               "ELEMENT_TRACE",
                               "ELEMENT_VIA",
                               "HAS_SHAPELY",
                               "SHAPE_ARC",
                               "SHAPE_CIRCLE",
                               "SHAPE_POLY",
                               "SHAPE_RECT",
                               "SHAPE_ROUND",
                               "SHAPE_SEG",
                               "BoardGeometry",
                               "Element",
                               "GeometryStats",
                               "Shape",
                               "compose_layer",
                               "pair_candidates",
                               "pair_clearance",
]

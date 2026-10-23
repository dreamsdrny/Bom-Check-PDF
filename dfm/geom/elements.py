"""几何元素 (Element) — 带层/网络/极性归属的形状。

所有检查共用同一套元素模型:
  * ``trace``  走线 (真实线宽, 圆头/平头)
  * ``pad``    焊盘 (圆/矩形/椭圆/多边形真实形状)
  * ``via``    过孔焊盘
  * ``pour``   铺铜 (真实多边形, 而非包围盒)
  * ``hole``   钻孔 (含槽孔)
"""
from __future__ import annotations

from typing import Optional

from dfm.geom.shapes import Shape

ELEMENT_TRACE = "trace"
ELEMENT_PAD = "pad"
ELEMENT_VIA = "via"
ELEMENT_POUR = "pour"
ELEMENT_HOLE = "hole"

POLARITY_DARK = "dark"
POLARITY_CLEAR = "clear"


class Element:
    """一个铜箔/钻孔元素。``geom`` 为极性合成后的实际几何 (可被 clear 裁剪)。"""

    __slots__ = (
        "attrs",
        "clipped",
        "geom",
        "kind",
        "layer",
        "maxx",
        "maxy",
        "minx",
        "miny",
        "net",
        "pin",
        "polarity",
        "ref",
        "shape",
    )

    def __init__(self, kind: str, shape: Shape, layer: str = "", net: str = "",
                 ref: str = "", pin: str = "", polarity: str = POLARITY_DARK,
                 attrs: Optional[dict] = None):
        self.kind = kind
        self.shape = shape
        self.layer = layer or ""
        self.net = net or ""
        self.ref = ref or ""
        self.pin = pin or ""
        self.polarity = polarity or POLARITY_DARK
        self.attrs: dict = attrs or {}
        self.clipped = False
        bx0, by0, bx1, by1 = shape.bbox
        self.minx, self.miny, self.maxx, self.maxy = bx0, by0, bx1, by1
        self.geom = None            # 惰性; 极性合成后写入

    # ---------------- 几何 ----------------
    @property
    def geometry(self):
        """当前有效几何 (被 clear 裁剪过则为裁剪结果)。"""
        if self.geom is None:
            self.geom = self.shape.geom
        return self.geom

    def set_geometry(self, geom) -> None:
        self.geom = geom
        if geom is None:
            return
        try:
            self.minx, self.miny, self.maxx, self.maxy = geom.bounds
        except Exception:
            pass

    @property
    def is_copper(self) -> bool:
        return self.kind != ELEMENT_HOLE

    @property
    def x(self) -> float:
        return self.shape.x

    @property
    def y(self) -> float:
        return self.shape.y

    def label(self) -> str:
        if self.ref:
            return f"{self.ref}.{self.pin}" if self.pin else self.ref
        return self.net or self.kind

    def __repr__(self) -> str:  # pragma: no cover
        return (f"<Element {self.kind}/{self.layer} net={self.net or '-'} "
                f"{self.shape.describe()} pol={self.polarity}>")





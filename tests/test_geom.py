"""几何内核测试: 真实形状 / 极性合成 / 精确净距 / 共享模型。

这些用例直接对应修复的三类误判:
  * 矩形焊盘外接圆近似 → 假间距违规
  * 负片图元被当成铜箔 → 假短路
  * 铺铜按包围盒 → 面到面距离失真
"""
import unittest

from tests.helpers import design, make_raw

from dfm.geom.board import BoardGeometry
from dfm.geom.clearance import element_clearance, pair_candidates
from dfm.geom.elements import (ELEMENT_PAD, ELEMENT_POUR, ELEMENT_TRACE, Element,
                               POLARITY_CLEAR)
from dfm.geom.polarity import PolarityStats, compose_layer
from dfm.geom.shapes import Shape


def el(shape, layer="TOP", net="N", kind=ELEMENT_TRACE, polarity="dark", **attrs):
    return Element(kind, shape, layer=layer, net=net, polarity=polarity, attrs=attrs)


class TestShapes(unittest.TestCase):
    def test_bbox_analytic_no_geometry_build(self):
        s = Shape.rect(10, 20, 4, 2, rot=0)
        self.assertEqual(s.bbox, (8.0, 19.0, 12.0, 21.0))
        self.assertIsNone(s._geom)          # 包围盒不应触发 shapely 构建

    def test_rotated_rect_bbox_grows(self):
        a = Shape.rect(0, 0, 10, 2, rot=0).bbox
        b = Shape.rect(0, 0, 10, 2, rot=90).bbox
        self.assertGreater(b[3] - b[1], a[3] - a[1])

    def test_segment_area_matches_width(self):
        import math

        g = Shape.segment(0, 0, 10, 0, 0.2).geom
        # 圆头端帽会各加半个圆: 1[REDACTED-UNKNOWN].2 + π*0.1²
        self.assertAlmostEqual(g.area, 10 * 0.2 + math.pi * 0.01, delta=0.01)

    def test_square_cap_segment_exact(self):
        g = Shape.segment(0, 0, 10, 0, 0.2, square_cap=True).geom
        self.assertAlmostEqual(g.area, 10.2 * 0.2, delta=0.01)   # 两端各外延半宽

    def test_circle_area(self):
        import math

        g = Shape.circle(0, 0, 1.0).geom
        self.assertAlmostEqual(g.area, math.pi / 4, delta=0.01)

    def test_oblong_area_between_rect_and_ellipse(self):
        g = Shape.oblong(0, 0, 2.0, 1.0).geom
        self.assertGreater(g.area, 2.0 * 1.0 * 0.78)
        self.assertLess(g.area, 2.0 * 1.0 + 0.8)

    def test_arc_points_span(self):
        pts = Shape.arc(1, 0, 0, 1, 0, 0, ccw=False, width=0.1).arc_points()
        self.assertGreaterEqual(len(pts), 3)
        self.assertAlmostEqual(pts[0][0], 1.0, places=6)
        self.assertAlmostEqual(pts[-1][1], 1.0, places=6)

    def test_polygon_invalid_self_intersection_fixed(self):
        g = Shape.polygon([(0, 0), (1, 1), (1, 0), (0, 1)]).geom   # 自交蝴蝶形
        self.assertTrue(g.is_valid)
        self.assertGreater(g.area, 0)


class TestClearanceExactness(unittest.TestCase):
    """核心回归: 矩形焊盘不能按外接圆算净距。"""

    def test_rect_pad_clearance_not_underestimated(self):
        # 两个 [REDACTED-UNKNOWN] 矩形焊盘, 中心距 6mm → 真实净距 2mm
        a = el(Shape.rect(0, 0, 4, 1), kind=ELEMENT_PAD, net="A")
        b = el(Shape.rect(6, 0, 4, 1), kind=ELEMENT_PAD, net="B")
        d, _x, _y = element_clearance(a, b)
        self.assertAlmostEqual(d, 2.0, places=3)

    def test_old_circle_approximation_would_be_wrong(self):
        # 若按外接圆 (d=4) 近似: 6 - 2 - 2 = 2 (此例巧合相同),
        # 换成细长焊盘 10x1 中心距 12 → 真实 2mm, 外接圆近似 12-5-5 = 2 (仍同);
        # 但**垂直方向**差异巨大: 长边平行放置时外接圆会给出负值 (误报短路)
        a = el(Shape.rect(0, 0, 10, 1), kind=ELEMENT_PAD, net="A")
        b = el(Shape.rect(0, 3, 10, 1), kind=ELEMENT_PAD, net="B")
        d, _x, _y = element_clearance(a, b)
        self.assertAlmostEqual(d, 2.0, places=3)          # 真实净距 2mm
        circle_approx = 3.0 - 5.0 - 5.0                   # 外接圆近似 → -7 (假短路)
        self.assertLess(circle_approx, 0)

    def test_pour_uses_true_polygon_not_bbox(self):
        # L 形铺铜: 包围盒会覆盖 (5,5), 真实多边形不覆盖
        pts = [(0, 0), (10, 0), (10, 2), (2, 2), (2, 10), (0, 10)]
        pour = el(Shape.polygon(pts), kind=ELEMENT_POUR, net="A")
        pad = el(Shape.circle(5, 5, 0.4), kind=ELEMENT_PAD, net="B")
        d, _x, _y = element_clearance(pour, pad)
        self.assertGreater(d, 2.0)          # 真实距离 ≈ 3 - 0.2 = 2.8

    def test_touching_returns_zero_and_location(self):
        a = el(Shape.circle(0, 0, 1.0), kind=ELEMENT_PAD, net="A")
        b = el(Shape.circle(0.5, 0, 1.0), kind=ELEMENT_PAD, net="B")
        d, x, y = element_clearance(a, b)
        self.assertEqual(d, 0.0)
        self.assertIsNotNone(x)

    def test_candidates_respect_tolerance(self):
        els = [el(Shape.circle(i * 10, 0, 1.0), net=f"N{i}") for i in range(5)]
        pairs = list(pair_candidates(els, tol=1.0))
        self.assertEqual(pairs, [])                       # 相距 10mm, 容差 1mm
        pairs = list(pair_candidates(els, tol=12.0))
        self.assertTrue(pairs)


class TestPolarity(unittest.TestCase):
    """负片合成: clear 图元挖除铜箔, 且自身不作为铜。"""

    def test_clear_removes_copper(self):
        plane = el(Shape.polygon([(0, 0), (10, 0), (10, 10), (0, 10)]),
                   kind=ELEMENT_POUR, net="GND")
        antipad = el(Shape.circle(5, 5, 2.0), kind=ELEMENT_PAD, net="GND",
                     polarity=POLARITY_CLEAR)
        stats = PolarityStats()
        alive = compose_layer([plane, antipad], stats, "L2")
        self.assertEqual(len(alive), 1)                   # clear 元素不返回
        self.assertTrue(alive[0].clipped)
        self.assertAlmostEqual(alive[0].geometry.area, 100 - 3.14159, delta=0.05)
        self.assertEqual(stats.clear_elements, 1)
        self.assertEqual(stats.clipped_elements, 1)

    def test_fully_consumed_element_dropped(self):
        small = el(Shape.circle(5, 5, 1.0), kind=ELEMENT_PAD, net="N1")
        big_clear = el(Shape.circle(5, 5, 4.0), kind=ELEMENT_PAD, net="",
                        polarity=POLARITY_CLEAR)
        stats = PolarityStats()
        alive = compose_layer([small, big_clear], stats, "TOP")
        self.assertEqual(alive, [])
        self.assertEqual(stats.dropped_elements, 1)

    def test_no_clear_keeps_lazy_geometry(self):
        e = el(Shape.circle(0, 0, 1.0), kind=ELEMENT_PAD, net="N")
        alive = compose_layer([e], PolarityStats(), "TOP")
        self.assertEqual(len(alive), 1)
        self.assertIsNone(alive[0].geom)                  # 惰性: 未预构建 shapely 几何

    def test_clear_not_treated_as_copper_in_board(self):
        g = BoardGeometry(design=None, fab_data=[{
            "kind": "gerber", "name": "L2", "unit": "mm",
            "features": [
                {"type": "region", "points": [(0, 0), (10, 0), (10, 10), (0, 10)],
                 "layer": "L2", "net": "GND", "clear": False},
                {"type": "flash", "x1": 5, "y1": 5, "w": 2.0, "h": 2.0,
                 "shape": "circle", "layer": "L2", "net": "GND", "clear": True},
            ], "bbox": [0, 0, 10, 10]}])
        self.assertEqual(len(g.elements), 1)              # clear 图元被排除
        self.assertEqual(g.polarity.clear_elements, 1)


class TestBoardGeometry(unittest.TestCase):
    def test_build_from_design(self):
        g = BoardGeometry(design=design())
        self.assertGreater(len(g.elements), 10)
        self.assertEqual(len(g.holes), 3)
        self.assertIn("TOP", g.layers())
        self.assertEqual(g.outer_layers(), ["TOP", "BOTTOM"])

    def test_true_pad_shapes_from_fab_data(self):
        g = BoardGeometry(design=None, fab_data=[{
            "kind": "gerber", "name": "top", "unit": "mm",
            "features": [
                {"type": "flash", "x1": 0, "y1": 0, "w": 4.0, "h": 1.0,
                 "shape": "rect", "layer": "TOP", "net": "A"},
                {"type": "draw", "x1": 10, "y1": 0, "x2": 20, "y2": 0,
                 "width": 0.2, "layer": "TOP", "net": "B"},
            ], "bbox": [-2, -2, 20, 2]}])
        kinds = {e.kind for e in g.elements}
        self.assertEqual(kinds, {ELEMENT_PAD, ELEMENT_TRACE})
        pad = [e for e in g.elements if e.kind == ELEMENT_PAD][0]
        self.assertEqual(pad.shape.kind, "rect")
        self.assertAlmostEqual(pad.shape.w, 4.0)

    def test_zero_width_cline_treated_as_boundary(self):
        raw = make_raw()
        raw["clines"] = [{"net": "N", "layer": "TOP", "width": 0.0,
                          "points": [[0, 0], [10, 0], [10, 10], [0, 0]]}]
        g = BoardGeometry(design=design(**raw))
        self.assertEqual(g.stats.trace_count, 0)
        self.assertEqual(g.stats.boundary_cline_count, 1)

    def test_pour_polygon_keeps_vertices(self):
        raw = make_raw()
        raw["shapes"] = [{"net": "GND", "layer": "BOTTOM",
                          "points": [[0, 0], [5, 0], [5, 5], [0, 5]],
                          "bbox": [0, 0, 5, 5]}]
        g = BoardGeometry(design=design(**raw))
        pours = [e for e in g.elements if e.kind == ELEMENT_POUR]
        self.assertEqual(len(pours), 1)
        self.assertAlmostEqual(pours[0].geometry.area, 25.0, places=3)
        self.assertFalse(pours[0].attrs.get("approx"))

    def test_bbox_only_pour_flagged_approx(self):
        g = BoardGeometry(design=design())       # 样例 shapes 只有 bbox
        pours = [e for e in g.elements if e.kind == ELEMENT_POUR]
        self.assertTrue(pours)
        self.assertTrue(all(p.attrs.get("approx") for p in pours))
        self.assertTrue(any("包围盒" in w for w in g.warnings))

    def test_element_cap_truncation_flag(self):
        g = BoardGeometry(design=design(), element_cap=3)
        self.assertTrue(g.stats.truncated)
        self.assertLessEqual(len(g.all_elements), 3)
        self.assertTrue(any("截断" in w for w in g.warnings))

    def test_shared_geometry_reused_between_checks(self):
        from dfm.core.registry import CheckContext

        ctx = CheckContext(design=design(), capability={"trace": {}})
        g1 = ctx.geometry
        g2 = ctx.geometry
        self.assertIs(g1, g2)                   # 只构建一次

    def test_summary_dict(self):
        s = BoardGeometry(design=design()).summary()
        self.assertIn("铜箔元素总数", s)
        self.assertIn("走线段", s)


if __name__ == "__main__":
    unittest.main()

"""渲染层回归测试: 真实形状 / 负片 / 槽孔 必须体现在 SVG、Qt 场景与 PNG 截图中。

旧实现把所有焊盘画成圆、把负片图元画成铜箔、把槽孔画成圆孔 —— 判定修对了,
图上看不出来就等于没修（评审时无法核对）。
"""
import os
import shutil
import tempfile
import unittest

from tests.helpers import write_gerber_set  # noqa: F401  (确保 sys.path)

from dfm.report.svg import render_board_svg

RECT_PAD = {"type": "flash", "x1": 10.0, "y1": 10.0, "x2": 10.0, "y2": 10.0,
            "shape": "rect", "w": 4.0, "h": 1.0, "rot": 0.0, "width": 1.0,
            "layer": "TOP", "net": "A"}
OVAL_PAD = {"type": "flash", "x1": 20.0, "y1": 10.0, "x2": 20.0, "y2": 10.0,
            "shape": "oval", "w": 3.0, "h": 1.2, "width": 1.2,
            "layer": "TOP", "net": "B"}
POLY_PAD = {"type": "flash", "x1": 30.0, "y1": 10.0, "x2": 30.0, "y2": 10.0,
            "shape": "poly", "points": [(29, 9), (31, 9), (31, 11), (29, 11)],
            "w": 2.0, "h": 2.0, "width": 2.0, "layer": "TOP", "net": "C"}
CLEAR_PAD = {"type": "flash", "x1": 40.0, "y1": 10.0, "x2": 40.0, "y2": 10.0,
             "shape": "circle", "w": 2.0, "h": 2.0, "width": 2.0, "clear": True,
             "layer": "TOP", "net": ""}
POUR_WITH_HOLE = {"type": "region",
                  "points": [(0, 0), (50, 0), (50, 20), (0, 20)],
                  "holes": [[(24, 9), (26, 9), (26, 11), (24, 11)]],
                  "x1": 0, "y1": 0, "x2": 50, "y2": 20,
                  "layer": "TOP", "net": "GND"}

FAB = [{
    "kind": "gerber", "name": "top", "unit": "mm",
    "primitives": [RECT_PAD, OVAL_PAD, POLY_PAD, CLEAR_PAD, POUR_WITH_HOLE,
                   {"type": "draw", "x1": 5, "y1": 15, "x2": 45, "y2": 15,
                    "width": 0.2, "layer": "TOP", "net": "D", "square_cap": True}],
    "bbox": [0, 0, 50, 20], "feature_count": 6,
}, {
    "kind": "drill", "name": "drill", "unit": "mm", "plating": "PTH",
    "holes": [{"x": 10, "y": 5, "diameter_mm": 0.3, "plating": "PTH"},
              {"x": 20, "y": 5, "x2": 24, "y2": 5, "diameter_mm": 0.8,
               "plating": "PTH", "is_slot": True, "length_mm": 4.0}],
    "hole_count": 2, "bbox": [0, 0, 50, 20],
}]


class TestSvgRendering(unittest.TestCase):
    def setUp(self):
        self.svg = render_board_svg(FAB, [], width=1000)

    def test_rect_pad_drawn_as_rect(self):
        self.assertIn("<rect ", self.svg)
        self.assertIn("width='4.0000'", self.svg)
        self.assertIn("height='1.0000'", self.svg)

    def test_oval_pad_drawn_as_ellipse(self):
        self.assertIn("<ellipse ", self.svg)
        self.assertIn("rx='1.5000'", self.svg)
        self.assertIn("ry='0.6000'", self.svg)

    def test_poly_pad_drawn_as_path(self):
        self.assertIn("<path d='M 29.0000", self.svg.replace("cy='", ""))

    def test_pour_hole_uses_evenodd(self):
        self.assertIn("fill-rule='evenodd'", self.svg)

    def test_slot_drawn_as_line_not_circle(self):
        # 槽孔: 以粗线段绘制 (stroke-width = 直径)
        self.assertIn("stroke-width='0.8000'", self.svg)
        self.assertIn("stroke-linecap='round'", self.svg)

    def test_square_cap_trace(self):
        self.assertIn("stroke-linecap='butt'", self.svg)

    def test_empty_data_placeholder(self):
        self.assertIn("无几何数据", render_board_svg([], []))


class TestSnapshotRendering(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_overview_png_renders(self):
        from dfm.report.snapshot import render_overview_png

        out = os.path.join(self.tmp, "ov.png")
        path = render_overview_png(FAB, [], out, width=320)
        self.assertTrue(path and os.path.isfile(path))
        self.assertGreater(os.path.getsize(path), 500)

    def test_crop_png_renders(self):
        from dfm.report.snapshot import render_crop_png

        out = os.path.join(self.tmp, "crop.png")
        path = render_crop_png(FAB, 10.0, 10.0, out, half_span_mm=4.0, size=200)
        self.assertTrue(path and os.path.isfile(path))

    def test_rect_pad_pixels_differ_from_circle(self):
        """矩形焊盘与同面积圆焊盘的像素分布不同 (证明按真实形状绘制)。"""
        from PIL import Image

        from dfm.report.snapshot import render_overview_png

        rect_only = [dict(FAB[0], primitives=[RECT_PAD], bbox=[8, 9, 12, 11])]
        circ_only = [dict(FAB[0], primitives=[
            dict(RECT_PAD, shape="circle", w=2.0, h=2.0, width=2.0)],
            bbox=[8, 9, 12, 11])]
        p1 = os.path.join(self.tmp, "r.png")
        p2 = os.path.join(self.tmp, "c.png")
        render_overview_png(rect_only, [], p1, width=160)
        render_overview_png(circ_only, [], p2, width=160)
        a = list(Image.open(p1).convert("L").getdata())
        b = list(Image.open(p2).convert("L").getdata())
        self.assertNotEqual(a, b)


class TestQtSceneRendering(unittest.TestCase):
    """Qt 场景构建 (离屏): 真实形状路径 + 负片 + 槽孔。"""

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from dfm.app import create_app

        cls.app = create_app("dark")

    def test_scene_builds_with_true_shapes(self):
        from dfm.ui.widgets.board_view import BoardScene

        scene = BoardScene()
        scene.build(FAB)
        items = scene.items()
        self.assertGreater(len(items), 5)
        self.assertTrue(scene.layers())
        # 槽孔与圆孔都应进入钻孔层
        self.assertIn("__drill__", scene._layer_items)
        self.assertGreaterEqual(len(scene._layer_items["__drill__"]), 2)

    def test_markers_and_selection(self):
        from dfm.ui.widgets.board_view import BoardScene

        scene = BoardScene()
        scene.build(FAB)
        issues = [{"severity": "危险", "code": "T-01", "rule": "r",
                   "description": "d", "x": 10.0, "y": 10.0}]
        scene.add_markers(issues)
        self.assertEqual(len(scene._marker_items), 1)
        scene.set_selected(0)
        self.assertAlmostEqual(scene._marker_items[0].scale(), 1.7)
        self.assertIsNotNone(scene.marker_pos(0))

    def test_layer_visibility_toggle(self):
        from dfm.ui.widgets.board_view import BoardScene

        scene = BoardScene()
        scene.build(FAB)
        scene.set_layer_visible("TOP", False)
        self.assertNotIn("TOP", scene.visible_layers())
        scene.set_all_layers_visible(True)
        self.assertIn("TOP", scene.visible_layers())


if __name__ == "__main__":
    unittest.main()

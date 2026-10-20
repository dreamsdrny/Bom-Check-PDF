"""报告导出单元测试: Excel / HTML / JSON / TXT / SVG。"""
import json
import os
import shutil
import tempfile
import unittest

from tests.helpers import SAMPLE_JSON, SAMPLE_PARAMS

from dfm.core.pipeline import analyze
from dfm.report import export_report, save_json, save_svg, slim_analysis
from dfm.report.html import generate_dfm_html
from dfm.report.svg import collect_bbox, layer_names, render_board_svg
from dfm.report.writers import generate_dfm_excel, generate_dfm_text


class ReportTestBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.res = analyze({
            "source_type": "json", "source_path": SAMPLE_JSON,
            "design_params": SAMPLE_PARAMS, "factory": "jlcpcb",
            "board_type": "rigid", "capability_level": "standard",
            "copper_um": 35.0, "board_thickness": 1.6, "load_plugins": False,
        })

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestExcel(ReportTestBase):
    def test_generate(self):
        path = os.path.join(self.tmp, "r.xlsx")
        out = generate_dfm_excel(self.res, path, self.res["board_meta"])
        self.assertTrue(os.path.isfile(out))
        self.assertGreater(os.path.getsize(out), 5000)

    def test_workbook_sheets(self):
        import openpyxl

        path = os.path.join(self.tmp, "r.xlsx")
        generate_dfm_excel(self.res, path, self.res["board_meta"], embed_images=False)
        wb = openpyxl.load_workbook(path)
        names = wb.sheetnames
        self.assertTrue(any("Main" in n or "封面" in n or n == "Main" for n in names))
        self.assertGreaterEqual(len(names), 3)
        wb.close()

    def test_without_images(self):
        path = os.path.join(self.tmp, "noimg.xlsx")
        generate_dfm_excel(self.res, path, self.res["board_meta"], embed_images=False)
        self.assertTrue(os.path.isfile(path))


class TestText(ReportTestBase):
    def test_generate(self):
        path = os.path.join(self.tmp, "r.txt")
        generate_dfm_text(self.res, path, self.res["board_meta"])
        text = open(path, "r", encoding="utf-8").read()
        self.assertIn("DEMO-2L-SAMPLE", text)
        self.assertIn("可制造性评分", text)
        self.assertGreater(len(text), 500)


class TestHtml(ReportTestBase):
    def test_generate(self):
        path = os.path.join(self.tmp, "r.html")
        generate_dfm_html(self.res, path)
        html = open(path, "r", encoding="utf-8").read()
        self.assertIn("<!DOCTYPE html>", html)
        self.assertIn("dfm-data", html)
        self.assertIn("issue-row", html)
        self.assertIn("DEMO-2L-SAMPLE", html)
        self.assertGreater(len(html), 8000)

    def test_svg_embedded(self):
        path = os.path.join(self.tmp, "r2.html")
        generate_dfm_html(self.res, path)
        html = open(path, "r", encoding="utf-8").read()
        self.assertIn("<svg", html)
        self.assertIn("dfm-marker", html)

    def test_no_geometry(self):
        res = dict(self.res)
        res["fab_data"] = []
        path = os.path.join(self.tmp, "r3.html")
        generate_dfm_html(res, path)
        html = open(path, "r", encoding="utf-8").read()
        self.assertIn("无几何数据", html)

    def test_escaping(self):
        res = json.loads(json.dumps({k: v for k, v in self.res.items()
                                     if k not in ("fab_data", "instances")},
                                    default=str))
        res["issues"] = [dict(self.res["issues"][0],
                              description="<script>alert(1)</script>")]
        res["fab_data"] = []
        path = os.path.join(self.tmp, "r4.html")
        generate_dfm_html(res, path)
        html = open(path, "r", encoding="utf-8").read()
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;", html)


class TestSvg(ReportTestBase):
    def test_render(self):
        svg = render_board_svg(self.res["fab_data"], self.res["issues"], width=800)
        self.assertTrue(svg.startswith("<svg"))
        self.assertIn("dfm-marker", svg)

    def test_bbox_and_layers(self):
        bbox = collect_bbox(self.res["fab_data"])
        self.assertIsNotNone(bbox)
        self.assertLess(bbox[0], bbox[2])
        self.assertTrue(layer_names(self.res["fab_data"]))

    def test_save(self):
        path = os.path.join(self.tmp, "b.svg")
        save_svg(self.res, path)
        self.assertTrue(os.path.isfile(path))

    def test_empty_data(self):
        svg = render_board_svg([], [])
        self.assertIn("无几何数据", svg)


class TestJsonAndFacade(ReportTestBase):
    def test_slim_drops_geometry(self):
        slim = slim_analysis(self.res)
        self.assertNotIn("fab_data", slim)
        self.assertIn("issues", slim)
        self.assertIn("instance_count", slim)

    def test_save_json(self):
        path = os.path.join(self.tmp, "r.json")
        save_json(self.res, path)
        data = json.load(open(path, "r", encoding="utf-8"))
        self.assertEqual(data["issue_summary"]["总计"], len(self.res["issues"]))

    def test_export_all_formats(self):
        base = os.path.join(self.tmp, "report")
        out = export_report(self.res, base,
                            formats=["excel", "html", "text", "json", "svg"])
        self.assertEqual(set(out), {"excel", "html", "text", "json", "svg"})
        for path in out.values():
            self.assertTrue(os.path.isfile(path), path)
            self.assertGreater(os.path.getsize(path), 500)

    def test_export_by_extension(self):
        path = os.path.join(self.tmp, "auto.html")
        out = export_report(self.res, path)
        self.assertEqual(list(out), ["html"])
        self.assertTrue(os.path.isfile(path))

    def test_export_to_directory(self):
        out = export_report(self.res, self.tmp, formats=["text"])
        self.assertTrue(os.path.isfile(out["text"]))
        self.assertEqual(os.path.dirname(out["text"]), self.tmp)

    def test_unknown_format_ignored(self):
        out = export_report(self.res, os.path.join(self.tmp, "x"), formats=["text"])
        self.assertEqual(list(out), ["text"])


if __name__ == "__main__":
    unittest.main()

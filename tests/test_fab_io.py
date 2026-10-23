"""制造数据解析 (Gerber / Excellon / ODB++) 与完整性检查单元测试。"""
import os
import shutil
import tempfile
import unittest

from tests.helpers import write_gerber_set

from dfm.io.fab import (analyze_fab, collect_fab_nets, compare_netlists, integrity_check,
                        netlist_compare_issues, normalize_cad_nets, parse_excellon,
                        parse_gerber, read_fab_data, summarize_fab)


class TestGerberParse(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.paths = write_gerber_set(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_parse_gerber_units_and_features(self):
        res = parse_gerber(self.paths["top"])
        self.assertEqual(res["kind"], "gerber")
        self.assertEqual(res["unit"], "mm")
        self.assertTrue(res["primitives"])
        self.assertTrue(res["apertures"])
        bbox = res["bbox"]
        self.assertIsNotNone(bbox[0])
        self.assertAlmostEqual(bbox[0], 5.0, places=2)     # 50000 -> 5.0mm (3:4 格式)

    def test_aperture_definition(self):
        res = parse_gerber(self.paths["top"])
        shapes = {code: ap.get("shape") for code, ap in res["apertures"].items()}
        self.assertIn("C", shapes.values())
        self.assertIn("R", shapes.values())

    def test_parse_excellon(self):
        res = parse_excellon(self.paths["drill"])
        self.assertEqual(res["kind"], "drill")
        self.assertEqual(len(res["holes"]), 4)          # 3 圆孔 + 1 槽孔
        diameters = sorted(round(h["diameter_mm"], 3) for h in res["holes"])
        self.assertEqual(diameters, [0.15, 0.3, 0.3, 0.8])
        self.assertEqual(res["plating"], "PTH")
        slots = [h for h in res["holes"] if h.get("is_slot")]
        self.assertEqual(len(slots), 1)                  # G85 槽孔被识别
        self.assertEqual(res["slot_count"], 1)
        self.assertAlmostEqual(slots[0]["length_mm"], 4.0, places=3)
        self.assertEqual(res["tools"][1]["hit_count"], 2)

    def test_excellon_repeat_and_tool_table(self):
        from tests.helpers import DRILL_REPEAT

        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "repeat.drl")
            with open(p, "w", encoding="utf-8") as f:
                f.write(DRILL_REPEAT)
            res = parse_excellon(p)
            self.assertEqual(len(res["holes"]), 4)       # R04 → 4 个孔
            self.assertEqual(res["repeat_count"], 3)
            self.assertAlmostEqual(res["holes"][0]["diameter_mm"], 0.25)

    def test_read_fab_data_directory(self):
        data = read_fab_data(self.tmp)
        kinds = sorted(d["kind"] for d in data)
        self.assertIn("gerber", kinds)
        self.assertIn("drill", kinds)

    def test_summarize_and_integrity(self):
        data = read_fab_data(self.tmp)
        stats = summarize_fab(data)
        self.assertEqual(stats["hole_count"], 4)          # 含 1 个槽孔
        self.assertTrue(stats["gerber_layers"])
        issues = integrity_check(data)
        self.assertIsInstance(issues, list)
        for it in issues:
            self.assertIn("severity", it)
            self.assertIn("rule", it)

    def test_analyze_fab(self):
        result = analyze_fab(self.tmp)
        self.assertIn("data", result)
        self.assertIn("issues", result)
        self.assertIn("stats", result)
        self.assertEqual(result["stats"]["hole_count"], 4)


class TestNetlistCompare(unittest.TestCase):
    def test_normalize_formats(self):
        a = normalize_cad_nets({"GND": ["R1.1", "C1-2"]})
        self.assertEqual(a["GND"], {"R1.1", "C1.2"})
        b = normalize_cad_nets([{"net": "VCC", "nodes": ["U1.1"]}])
        self.assertEqual(b["VCC"], {"U1.1"})

    def test_compare_open_and_short(self):
        cad = {"N1": {"R1.1", "R1.2"}, "N2": {"C1.1"}}
        fab = {"N1": {"R1.1"}, "N2": {"C1.1", "C1.2"}}
        cmp = compare_netlists(cad, fab)
        self.assertFalse(cmp["passed"])
        self.assertGreaterEqual(cmp["open_count"] + cmp["short_count"], 1)
        issues = netlist_compare_issues(cmp)
        self.assertTrue(issues)
        self.assertTrue(all(it["category"] == "网络比对" for it in issues))

    def test_compare_pass(self):
        nets = {"N1": {"R1.1", "R1.2"}}
        cmp = compare_netlists(nets, dict(nets))
        self.assertTrue(cmp["passed"])
        issues = netlist_compare_issues(cmp)
        self.assertEqual(len(issues), 1)               # 通过时输出一条「网络一致性」信息项
        self.assertEqual(issues[0]["rule"], "网络一致性")
        self.assertEqual(issues[0]["severity"], "低")

    def test_collect_fab_nets(self):
        data = [{"kind": "gerber", "name": "L1", "nets": {"A": {"R1.1"}}},
                {"kind": "gerber", "name": "L2", "nets": {"A": {"R1.2"}, "B": set()}}]
        nets = collect_fab_nets(data)
        self.assertEqual(nets["A"], {"R1.1", "R1.2"})
        self.assertIn("B", nets)


class TestOdbParse(unittest.TestCase):
    def test_missing_matrix_raises_or_empty(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "matrix"))
            with open(os.path.join(d, "matrix", "matrix"), "w", encoding="utf-8") as f:
                f.write("UNITS=MM\n")
            data = read_fab_data(d)
            self.assertIsInstance(data, list)


if __name__ == "__main__":
    unittest.main()

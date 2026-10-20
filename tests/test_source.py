"""数据源加载与参数派生单元测试。"""
import json
import os
import tempfile
import unittest

from tests.helpers import ROOT, SAMPLE_JSON, SAMPLE_PARAMS, design, write_gerber_set

from dfm.core.source import (SOURCE_GERBER, SOURCE_JSON, SOURCE_ODB, detect_source_type,
                             derive_params, load_source)


class TestDetect(unittest.TestCase):
    def test_by_extension(self):
        self.assertEqual(detect_source_type("board.brd"), "brd")
        self.assertEqual(detect_source_type("export.json"), SOURCE_JSON)
        self.assertEqual(detect_source_type("top.gtl"), SOURCE_GERBER)
        self.assertEqual(detect_source_type("plated.drl"), SOURCE_GERBER)
        self.assertEqual(detect_source_type(""), "none")

    def test_by_directory(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(detect_source_type(d), SOURCE_GERBER)
            os.makedirs(os.path.join(d, "matrix"))
            with open(os.path.join(d, "matrix", "matrix"), "w", encoding="utf-8") as f:
                f.write("UNITS=MM\n")
            self.assertEqual(detect_source_type(d), SOURCE_ODB)


class TestLoadJsonSource(unittest.TestCase):
    def test_load(self):
        src = load_source({"source_path": SAMPLE_JSON})
        self.assertEqual(src.kind, SOURCE_JSON)
        self.assertTrue(src.has_design)
        self.assertEqual(src.board_name, "DEMO-2L_SAMPLE")
        self.assertEqual(src.board_meta["version"], "A")
        self.assertTrue(src.fab_data)                 # to_fab_data 结果供渲染
        self.assertEqual(src.info_stats["manufacturing"]["铜层数"], 2)
        self.assertIn("geometry_stats", src.extras)

    def test_derive_params(self):
        src = load_source({"source_path": SAMPLE_JSON})
        p = src.params
        self.assertAlmostEqual(p["min_line_width"], 0.05)     # SIG1 走线
        self.assertAlmostEqual(p["min_mech_drill"], 0.15)     # 最小钻孔
        self.assertAlmostEqual(p["min_annular_ring"], 0.05)   # (0.45-0.35)/2
        self.assertEqual(p["board_thickness"], 1.6)
        self.assertEqual(p["comp_count"], 5)
        self.assertEqual(p["via_count"], 3)
        self.assertGreater(p["min_component_spacing"], 0.0)   # 器件本体最小间距已量测
        self.assertEqual(p["smd_count"], 8)
        self.assertEqual(p["fiducial_count_top"], 0)          # 样例无基准点

    def test_user_params_win_over_derived(self):
        src = load_source({"source_path": SAMPLE_JSON, "design_params": SAMPLE_PARAMS})
        with open(SAMPLE_PARAMS, "r", encoding="utf-8-sig") as f:
            user = json.load(f)
        self.assertEqual(src.params["min_line_width"], user["min_line_width"])

    def test_params_dict_input(self):
        src = load_source({"source_path": SAMPLE_JSON,
                           "design_params": {"min_line_width": 0.3}})
        self.assertEqual(src.params["min_line_width"], 0.3)

    def test_bad_params_json_warns(self):
        src = load_source({"source_path": SAMPLE_JSON,
                           "design_params": os.path.join(ROOT, "__missing__.json")})
        self.assertTrue(any("设计参数" in w for w in src.warnings))

    def test_thickness_override(self):
        src = load_source({"source_path": SAMPLE_JSON, "board_thickness": 2.0})
        self.assertEqual(src.params["board_thickness"], 2.0)   # 显式值优先


class TestLoadGerberSource(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        write_gerber_set(self.tmp)

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_load_gerber_dir(self):
        src = load_source({"source_path": self.tmp, "board_thickness": 1.6})
        self.assertEqual(src.kind, SOURCE_GERBER)
        self.assertTrue(src.has_design)
        self.assertTrue(src.fab_stats.get("gerber_layers"))
        self.assertIn("gerber_meta", src.extras)
        meta = src.extras["gerber_meta"]
        self.assertTrue(meta.get("copper_layers"))
        self.assertTrue(meta.get("profile_layers"))
        # 无 X2 网络属性 → 应有派生网络提示
        self.assertTrue(any("X2" in w for w in src.warnings) or meta.get("has_x2_net"))

    def test_missing_source_raises_warning(self):
        src = load_source({"source_type": "gerber",
                           "source_path": os.path.join(self.tmp, "no_such_dir")})
        self.assertTrue(src.warnings)
        self.assertFalse(src.has_design)

    def test_design_outline_from_profile(self):
        src = load_source({"source_path": self.tmp})
        self.assertGreaterEqual(len(src.design.outline), 4)


class TestInfoStats(unittest.TestCase):
    def test_stats_filter_empty(self):
        src = load_source({"source_path": SAMPLE_JSON})
        man = src.info_stats["manufacturing"]
        self.assertNotIn(None, man.values())
        self.assertTrue(man.get("网络数"))


if __name__ == "__main__":
    unittest.main()

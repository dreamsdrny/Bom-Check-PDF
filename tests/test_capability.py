"""制程能力库 (自动发现 / 别名 / 分档) 单元测试。"""
import os
import shutil
import tempfile
import unittest

import yaml

from tests.helpers import ROOT  # noqa: F401

from dfm.core import capability as cap


class TestDiscovery(unittest.TestCase):
    def test_builtin_factories(self):
        ids = cap.list_factories()
        self.assertIn("hongban", ids)
        self.assertIn("sihui", ids)
        self.assertIn("jlcpcb", ids)
        self.assertEqual(ids[0], cap.DEFAULT_FACTORY)

    def test_labels_and_aliases(self):
        labels = cap.factory_labels()
        self.assertTrue(labels["hongban"])
        self.assertEqual(cap.normalize_factory("板厂A"), "hongban")
        self.assertEqual(cap.normalize_factory("嘉立创"), "jlcpcb")
        self.assertEqual(cap.normalize_factory("SIHUI"), "sihui")
        self.assertEqual(cap.normalize_factory("不存在的板厂"), cap.DEFAULT_FACTORY)
        self.assertEqual(cap.factory_label("hongban"), labels["hongban"])

    def test_capability_file_path(self):
        path = cap.capability_file("sihui")
        self.assertTrue(path and os.path.isfile(path))
        self.assertTrue(path.endswith(".yaml"))

    def test_levels_and_board_types(self):
        types = cap.list_board_types("hongban")
        self.assertIn("rigid", types)
        levels = cap.list_levels("rigid", factory="hongban")
        self.assertIn("standard", levels)
        self.assertEqual(cap.normalize_level("advanced"), "extreme")
        self.assertEqual(cap.normalize_level("车规"), "automotive")

    def test_new_factory_yaml_auto_discovered(self):
        """把新 YAML 放进用户规则目录即可新增板厂, 无需改代码。"""
        cap.clear_capability_cache()
        tmp = tempfile.mkdtemp()
        rules_dir = os.path.join(tmp, "rules", "capability")
        os.makedirs(rules_dir)
        db = {
            "meta": {"factory": "测试板厂", "aliases": ["测试厂"]},
            "board_types": {
                "rigid": {
                    "label": "硬板",
                    "levels": ["standard"],
                    "level_labels": {"standard": "常规"},
                    "standard": {
                        "trace": {"min_line_spacing_by_copper": [
                            {"copper_max_um": 35, "line_um": 90, "space_um": 90}]},
                        "drill": {"min_mech_drill": {"value": 0.25, "unit": "mm"}},
                    },
                }
            },
        }
        with open(os.path.join(rules_dir, "testfab.yaml"), "w", encoding="utf-8") as f:
            yaml.safe_dump(db, f, allow_unicode=True)
        old = os.environ.get("EVAN_DFM_RULES")
        os.environ["EVAN_DFM_RULES"] = os.path.join(tmp, "rules")
        try:
            cap.clear_capability_cache()
            self.assertIn("testfab", cap.list_factories())
            self.assertEqual(cap.normalize_factory("测试厂"), "testfab")
            self.assertEqual(cap.factory_label("testfab"), "测试板厂")
            c = cap.load_capability("rigid", "standard", factory="testfab")
            line, space = cap.get_line_spacing_capability(c, 35.0)
            self.assertEqual((line, space), (90.0, 90.0))
        finally:
            if old is None:
                os.environ.pop("EVAN_DFM_RULES", None)
            else:
                os.environ["EVAN_DFM_RULES"] = old
            cap.clear_capability_cache()
            shutil.rmtree(tmp, ignore_errors=True)


class TestCapabilityValues(unittest.TestCase):
    def setUp(self):
        cap.clear_capability_cache()
        self.c = cap.load_capability("rigid", "standard", factory="hongban")

    def test_line_spacing_by_copper(self):
        thin = cap.get_line_spacing_capability(self.c, 18.0)
        thick = cap.get_line_spacing_capability(self.c, 70.0)
        self.assertIsNotNone(thin[0])
        self.assertIsNotNone(thick[0])
        self.assertLessEqual(thin[0], thick[0])      # 铜越厚, 最小线宽越大

    def test_to_mm_unit_conversion(self):
        self.assertAlmostEqual(cap._to_mm({"value": 1000, "unit": "um"}), 1.0)
        self.assertAlmostEqual(cap._to_mm({"value": 100, "unit": "mil"}), 2.54)
        self.assertAlmostEqual(cap._to_mm({"value": 1.5, "unit": "mm"}), 1.5)
        self.assertIsNone(cap._to_mm({"value": "abc"}))

    def test_analyze_design_detects_violation(self):
        result = cap.analyze_design(
            {"min_line_width": 0.05, "min_spacing": 0.05, "min_mech_drill": 0.15,
             "board_thickness": 1.6, "copper_um": 35},
            board_type="rigid", capability_level="standard", factory="hongban")
        self.assertFalse(result["passed"])
        self.assertTrue(result["issues"])
        fails = {it["item"] for it in result["items"] if it["status"] == "fail"}
        self.assertTrue(fails & {"最小线宽", "最小线距", "最小机械钻孔"})

    def test_analyze_design_pass(self):
        result = cap.analyze_design(
            {"min_line_width": 0.25, "min_spacing": 0.25, "min_mech_drill": 0.3,
             "board_thickness": 1.6, "copper_um": 35},
            board_type="rigid", capability_level="standard", factory="hongban")
        self.assertTrue(result["passed"])

    def test_empty_params_no_issues(self):
        result = cap.analyze_design({}, factory="hongban")
        self.assertEqual(result["items"], [])
        self.assertTrue(result["passed"])


if __name__ == "__main__":
    unittest.main()

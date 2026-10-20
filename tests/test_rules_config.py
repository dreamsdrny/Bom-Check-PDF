"""规则集 (checks.yaml) 单元测试: 加载 / 覆盖改判 / 自定义规则。"""
import os
import tempfile
import unittest

import yaml

from tests.helpers import ROOT  # noqa: F401

from dfm.core.issue import make_issue
from dfm.core.rules_config import (DEFAULT_CONFIG, RulesConfig, evaluate_custom_rules,
                                   template_text)


class TestLoad(unittest.TestCase):
    def test_builtin_file_exists_and_valid(self):
        path = os.path.join(ROOT, "rules", "checks.yaml")
        self.assertTrue(os.path.isfile(path), "出厂规则集缺失")
        cfg = RulesConfig.load(path)
        self.assertEqual(cfg.source, path)
        self.assertIn("checks", cfg.data)
        self.assertIn("assembly", cfg.data)

    def test_defaults_when_missing(self):
        cfg = RulesConfig.load(os.path.join(tempfile.gettempdir(), "__no_such_rules__.yaml"))
        self.assertEqual(cfg.data["scoring"]["base"], DEFAULT_CONFIG["scoring"]["base"])
        self.assertTrue(cfg.check_enabled("fabrication"))
        self.assertEqual(cfg.param("assembly", "min_component_spacing"), 0.5)

    def test_template_is_valid_yaml(self):
        data = yaml.safe_load(template_text())
        self.assertIn("custom_rules", data)
        self.assertTrue(data["custom_rules"])

    def test_user_override_merges(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "checks.yaml")
            with open(path, "w", encoding="utf-8") as f:
                yaml.safe_dump({"assembly": {"min_component_spacing": 0.8},
                                "checks": {"netclass": {"enabled": False}}}, f)
            cfg = RulesConfig.load(path)
            self.assertEqual(cfg.param("assembly", "min_component_spacing"), 0.8)
            self.assertEqual(cfg.param("assembly", "fiducial_min"), 2)   # 未覆盖项保留默认
            self.assertFalse(cfg.check_enabled("netclass"))
            self.assertTrue(cfg.check_enabled("fabrication"))


class TestApplyOverrides(unittest.TestCase):
    def _cfg(self, rules):
        return RulesConfig({"rules": rules})

    def test_disable_rule(self):
        cfg = self._cfg({"最小线宽": {"enabled": False}})
        issues = [make_issue(code="A-1", category="线路", rule="最小线宽",
                             severity="危险", description="d", measured=0.05)]
        self.assertEqual(cfg.apply(issues), [])

    def test_severity_and_suggestion_override(self):
        cfg = self._cfg({"最小线宽": {"severity": "低", "suggestion": "自定义建议"}})
        issues = [make_issue(code="A-1", category="线路", rule="最小线宽",
                             severity="危险", description="d", measured=0.05)]
        out = cfg.apply(issues)[0]
        self.assertEqual(out["severity"], "低")
        self.assertEqual(out["suggestion"], "自定义建议")

    def test_min_threshold_tightens(self):
        cfg = self._cfg({"最小线宽": {"min": 0.12, "severity": "危险"}})
        worse = cfg.apply([make_issue(code="A", category="线路", rule="最小线宽",
                                      severity="低", description="d", measured=0.10)])
        self.assertEqual(len(worse), 1)
        self.assertEqual(worse[0]["severity"], "危险")
        self.assertEqual(worse[0]["required"], 0.12)

    def test_min_threshold_drops_compliant(self):
        cfg = self._cfg({"最小线宽": {"min": 0.08}})
        ok = cfg.apply([make_issue(code="A", category="线路", rule="最小线宽",
                                   severity="危险", description="d", measured=0.15)])
        self.assertEqual(ok, [])

    def test_warn_band_downgrades(self):
        cfg = self._cfg({"最小线宽": {"min": 0.10, "warn_ratio": 1.2, "severity": "危险"}})
        out = cfg.apply([make_issue(code="A", category="线路", rule="最小线宽",
                                    severity="中", description="d", measured=0.11)])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["severity"], "低")     # 原「中」降一级

    def test_max_threshold(self):
        cfg = self._cfg({"板厚": {"max": 2.0}})
        bad = cfg.apply([make_issue(code="A", category="板厚", rule="板厚",
                                    severity="低", description="d", measured=2.4)])
        self.assertEqual(len(bad), 1)
        good = cfg.apply([make_issue(code="A", category="板厚", rule="板厚",
                                     severity="低", description="d", measured=1.0)])
        self.assertEqual(good, [])


class TestCustomRules(unittest.TestCase):
    def test_ge_violation(self):
        rules = [{"id": "CUS-01", "name": "线宽", "category": "线路",
                  "param": "min_line_width", "op": "ge", "value": 0.1,
                  "severity": "高", "unit": "mm"}]
        out = evaluate_custom_rules({"min_line_width": 0.08}, rules)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["code"], "CUS-01")
        self.assertEqual(out[0]["severity"], "高")
        self.assertEqual(out[0]["measured"], 0.08)

    def test_operators(self):
        params = {"board_thickness": 1.6, "surface_finish": "OSP",
                  "impedance": False, "layer_count": 8}
        cases = [
            ({"param": "board_thickness", "op": "eq", "value": 1.6}, 0),
            ({"param": "board_thickness", "op": "eq", "value": 2.0}, 1),
            ({"param": "board_thickness", "op": "between", "value": [0.8, 2.0]}, 0),
            ({"param": "board_thickness", "op": "outside", "value": [0.8, 2.0]}, 1),
            ({"param": "surface_finish", "op": "in", "value": ["ENIG", "ENEPIG"]}, 1),
            ({"param": "surface_finish", "op": "not_in", "value": ["OSP"]}, 1),
            ({"param": "surface_finish", "op": "not_in", "value": ["ENIG"]}, 0),
            ({"param": "impedance", "op": "true", "value": None}, 1),
            ({"param": "impedance", "op": "false", "value": None}, 0),
            ({"param": "missing_key", "op": "missing", "value": None}, 1),
            ({"param": "board_thickness", "op": "missing", "value": None}, 0),
            ({"param": "layer_count", "op": "le", "value": 12}, 0),
            ({"param": "layer_count", "op": "gt", "value": 12}, 1),
            ({"param": "layer_count", "op": "ne", "value": 8}, 1),
        ]
        for rule, expected in cases:
            rule = dict(rule, name="r", severity="中")
            got = evaluate_custom_rules(params, [rule])
            self.assertEqual(len(got), expected, f"规则 {rule} 期望 {expected} 条")

    def test_custom_code_preserved_in_pipeline(self):
        rules = [{"id": "MYCODE-7", "name": "r", "param": "board_thickness",
                  "op": "eq", "value": 9.9, "severity": "中"}]
        out = evaluate_custom_rules({"board_thickness": 1.6}, rules)
        self.assertEqual(out[0]["code"], "MYCODE-7")
        self.assertTrue(out[0]["keep_code"])

    def test_message_placeholders(self):
        rules = [{"name": "板厚", "param": "board_thickness", "op": "eq", "value": 1.6,
                  "severity": "高", "message": "板厚 {value}mm ≠ {target}mm"}]
        out = evaluate_custom_rules({"board_thickness": 1.2}, rules)
        self.assertIn("1.2", out[0]["description"])
        self.assertIn("1.6", out[0]["description"])

    def test_disabled_and_invalid_rules_skipped(self):
        rules = [
            {"name": "停用", "param": "x", "op": "ge", "value": 1, "enabled": False},
            {"name": "无参数", "op": "ge", "value": 1},
            {"name": "未知运算符", "param": "x", "op": "???", "value": 1},
        ]
        self.assertEqual(evaluate_custom_rules({"x": 0}, rules), [])

    def test_config_driven(self):
        cfg = RulesConfig({"custom_rules": [
            {"id": "C1", "name": "铜厚", "param": "copper_um", "op": "ge",
             "value": 35, "severity": "中"}]})
        self.assertEqual(len(evaluate_custom_rules({"copper_um": 18}, config=cfg)), 1)
        self.assertEqual(len(evaluate_custom_rules({"copper_um": 70}, config=cfg)), 0)


if __name__ == "__main__":
    unittest.main()

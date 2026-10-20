"""分析编排 (pipeline) 端到端单元测试。"""
import json
import os
import tempfile
import unittest

from tests.helpers import (ROOT, SAMPLE_JSON, SAMPLE_PARAMS, design, make_raw,
                           write_gerber_set)

from dfm.core.pipeline import analyze, analyze_file, list_modules
from dfm.core.rules_config import RulesConfig


def base_cfg(**over):
    cfg = {
        "source_type": "json",
        "source_path": SAMPLE_JSON,
        "factory": "hongban",
        "board_type": "rigid",
        "capability_level": "standard",
        "copper_um": 35.0,
        "board_thickness": 1.6,
        "load_plugins": False,
    }
    cfg.update(over)
    return cfg


class TestAnalyzeJson(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.res = analyze(base_cfg())

    def test_result_structure(self):
        for key in ("issues", "issue_summary", "risk", "board_meta", "info_stats",
                    "fab_data", "instances", "checks_run", "checks_skipped",
                    "factory_label", "capability_label", "passed", "source"):
            self.assertIn(key, self.res)

    def test_checks_executed(self):
        for cid in ("fabdata", "fabrication", "netlist", "assembly", "capability",
                    "panelization", "netclass"):
            self.assertIn(cid, self.res["checks_run"], f"{cid} 未执行")
        skipped = {s["id"]: s["reason"] for s in self.res["checks_skipped"]}
        self.assertIn("fabnet", skipped)             # 未提供 CAD 网表

    def test_issues_found_and_coded(self):
        issues = self.res["issues"]
        self.assertTrue(issues)
        codes = [it["code"] for it in issues]
        self.assertEqual(len(codes), len(set(codes)), "问题代码重复")
        for it in issues:
            self.assertIn(it["severity"], ("危险", "高", "中", "低", "关闭"))
            self.assertTrue(it["rule"])
            self.assertTrue(it["suggestion"])
            self.assertTrue(it["module"])

    def test_scoring(self):
        risk = self.res["risk"]
        self.assertLessEqual(risk["score"], 100.0)
        self.assertGreaterEqual(risk["score"], 0.0)
        self.assertFalse(self.res["passed"])         # 样例板故意含致命问题
        self.assertEqual(self.res["issue_summary"]["总计"], len(self.res["issues"]))

    def test_instances_have_xy(self):
        inst = self.res["instances"]
        self.assertTrue(inst)
        located = [i for i in inst if i.get("x") is not None]
        self.assertTrue(located, "几何实例应带 XY 定位")
        fab = [i for i in inst if i.get("category") in ("线路", "钻孔", "阻焊", "测试")]
        self.assertTrue(fab)
        self.assertTrue(all(i.get("x") is not None for i in fab))

    def test_no_duplicate_rule_across_modules(self):
        """几何检查已定位的规则, 参数化检查不应重复报告。"""
        geo = {(it["module"], it["rule"]) for it in self.res["issues"]
               if it["module"] in ("fabrication", "netlist", "assembly")}
        cap_rules = {it["rule"] for it in self.res["issues"] if it["module"] == "capability"}
        geo_rules = {r for _m, r in geo}
        self.assertEqual(cap_rules & geo_rules, set())

    def test_board_meta(self):
        meta = self.res["board_meta"]
        self.assertEqual(meta["board_name"], "DEMO-2L_SAMPLE")
        self.assertIn("板厂A", meta["rule_set"])
        self.assertTrue(meta["finish_time"])

    def test_design_params_exposed(self):
        self.assertAlmostEqual(self.res["design_params"]["min_line_width"], 0.05)

    def test_netclass_extras(self):
        nc = self.res.get("net_classes") or {}
        self.assertEqual(nc.get("total_nets"), 5)
        self.assertIn("summary", nc)


class TestModuleSelection(unittest.TestCase):
    def test_only_selected_modules_run(self):
        res = analyze(base_cfg(modules=["fabrication"]))
        self.assertEqual(res["checks_run"], ["fabrication"])
        self.assertTrue(all(it["module"] == "fabrication" for it in res["issues"]))

    def test_params_only_source(self):
        res = analyze({
            "source_type": "params", "source_path": "",
            "design_params": SAMPLE_PARAMS, "factory": "hongban",
            "board_type": "rigid", "capability_level": "standard",
            "load_plugins": False,
        })
        self.assertIn("capability", res["checks_run"])
        self.assertNotIn("fabrication", res["checks_run"])
        self.assertTrue(res["issues"])

    def test_list_modules_metadata(self):
        mods = list_modules()
        self.assertTrue(mods)
        for m in mods:
            self.assertIn("id", m)
            self.assertIn("name", m)
            self.assertIn("requires", m)


class TestRulesIntegration(unittest.TestCase):
    def test_disable_check_via_rules(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "checks.yaml")
            import yaml

            with open(path, "w", encoding="utf-8") as f:
                yaml.safe_dump({"checks": {"netlist": {"enabled": False}}}, f)
            res = analyze(base_cfg(rules_path=path))
            self.assertNotIn("netlist", res["checks_run"])
            skipped = {s["id"]: s["reason"] for s in res["checks_skipped"]}
            self.assertIn("netlist", skipped)

    def test_rule_override_applied(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "checks.yaml")
            import yaml

            with open(path, "w", encoding="utf-8") as f:
                yaml.safe_dump({"rules": {"最小线宽": {"enabled": False}}}, f)
            res = analyze(base_cfg(rules_path=path))
            self.assertNotIn("最小线宽", {it["rule"] for it in res["issues"]})

    def test_custom_rule_fires(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "checks.yaml")
            import yaml

            with open(path, "w", encoding="utf-8") as f:
                yaml.safe_dump({"custom_rules": [
                    {"id": "CUS-T1", "name": "板厚必须2.0", "category": "板厚",
                     "param": "board_thickness", "op": "eq", "value": 2.0,
                     "severity": "高", "unit": "mm"}]}, f)
            res = analyze(base_cfg(rules_path=path))
            codes = {it["code"] for it in res["issues"]}
            self.assertTrue(any(c.startswith("CUS") for c in codes))
            self.assertIn("custom_rules", res["checks_run"])

    def test_scoring_weights_change_score(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "checks.yaml")
            import yaml

            base = analyze(base_cfg())
            with open(path, "w", encoding="utf-8") as f:
                yaml.safe_dump({"scoring": {"base": 100.0, "cap_penalty": 100.0,
                                            "weights": {"危险": 1.0, "高": 0.5,
                                                        "中": 0.2, "低": 0.1}}}, f)
            light = analyze(base_cfg(rules_path=path))
            self.assertGreater(light["risk"]["score"], base["risk"]["score"])


class TestGerberSource(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        write_gerber_set(self.tmp)

    def tearDown(self):
        import shutil

        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_analyze_gerber_dir(self):
        res = analyze({
            "source_type": "gerber", "source_path": self.tmp, "fab_path": self.tmp,
            "factory": "hongban", "board_type": "rigid", "capability_level": "standard",
            "copper_um": 35.0, "board_thickness": 1.6, "load_plugins": False,
        })
        self.assertIn("fabdata", res["checks_run"])
        self.assertIn("fabrication", res["checks_run"])
        self.assertTrue(res["fab_data"])
        self.assertIn("gerber_meta", res)

    def test_analyze_file_helper(self):
        res = analyze_file(SAMPLE_JSON, factory="sihui", board_type="rigid",
                           capability_level="standard", modules=["fabrication"],
                           rules_path=None)
        self.assertEqual(res["source"]["type"], "json")
        self.assertIn("板厂B", res["factory_label"])


class TestCadNets(unittest.TestCase):
    def test_fabnet_runs_with_cad_nets(self):
        with tempfile.TemporaryDirectory() as d:
            cad_path = os.path.join(d, "cad.json")
            with open(cad_path, "w", encoding="utf-8") as f:
                json.dump({"GND": ["R1.1", "R2.1"], "SIG1": ["U1.1", "U1.2"]}, f)
            res = analyze(base_cfg(cad_nets=cad_path))
            self.assertIn("fabnet", res["checks_run"])
            self.assertIn("fabnet_summary", res)


class TestPluginInPipeline(unittest.TestCase):
    def test_plugin_check_participates(self):
        plugin_dir = tempfile.mkdtemp()
        try:
            with open(os.path.join(plugin_dir, "pl_check.py"), "w", encoding="utf-8") as f:
                f.write(
                    "from dfm.core.issue import make_issue\n"
                    "from dfm.core.registry import REQ_DESIGN, register_check\n\n"
                    "@register_check(id='pl_demo', name='插件示例', group='测试',\n"
                    "                requires=(REQ_DESIGN,), order=95)\n"
                    "def run(ctx):\n"
                    "    return [make_issue(code='PL-01', category='线路',\n"
                    "                       rule='插件规则', severity='低',\n"
                    "                       description='plugin issue', x=1.0, y=1.0)]\n")
            import os as _os

            old = _os.environ.get("EVAN_DFM_PLUGINS")
            _os.environ["EVAN_DFM_PLUGINS"] = plugin_dir
            try:
                res = analyze(base_cfg(load_plugins=True))
            finally:
                if old is None:
                    _os.environ.pop("EVAN_DFM_PLUGINS", None)
                else:
                    _os.environ["EVAN_DFM_PLUGINS"] = old
            self.assertIn("pl_demo", res["checks_run"])
            self.assertTrue(any(it["module"] == "pl_demo" for it in res["issues"]))
        finally:
            import shutil

            from dfm.core import registry

            registry.unregister_check("pl_demo")
            shutil.rmtree(plugin_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()

"""判定覆盖率 / 数据质量 / 计数与截断 / 告警带统一 / 致命错误 测试。

对应用户反馈的四个"结论不可信"缺陷:
  * 能力库缺项静默跳过 → 覆盖率显式化
  * 实例截断把"留存数"当"处数" → 计数与留存分离
  * 接近极限告警系数硬编码, 规则集配置失效 → 统一 warn_threshold
  * 解析失败仍报"分析成功" → 致命错误直接抛出
"""
import os
import tempfile
import unittest

import yaml

from tests.helpers import SAMPLE_JSON, design, make_raw, write_gerber_set

from dfm.core.coverage import Coverage, DataQuality, quality_issues
from dfm.core.issue import (IssueCollector, aggregate_by_rule, classify_limit,
                            warn_threshold)
from dfm.core.pipeline import analyze
from dfm.core.rules_config import RulesConfig


class TestWarnThreshold(unittest.TestCase):
    """告警带全系统唯一实现 (旧实现: 检查层硬编码 1.2/0.8, 规则集配置无效)。"""

    def test_higher_is_better(self):
        # limit=0.10, ratio=0.8 → 0.1[REDACTED-UNKNOWN].125 判"接近极限"
        self.assertAlmostEqual(warn_threshold(0.10, 0.8, True), 0.125)
        self.assertEqual(classify_limit(0.09, 0.10, True, 0.8), "危险")
        self.assertEqual(classify_limit(0.11, 0.10, True, 0.8), "高")
        self.assertEqual(classify_limit(0.20, 0.10, True, 0.8), "")

    def test_lower_is_better(self):
        self.assertAlmostEqual(warn_threshold(0.20, 0.8, False), 0.16)
        self.assertEqual(classify_limit(0.25, 0.20, False, 0.8), "危险")
        self.assertEqual(classify_limit(0.18, 0.20, False, 0.8), "高")
        self.assertEqual(classify_limit(0.10, 0.20, False, 0.8), "")

    def test_ratio_is_configurable(self):
        # ratio 越小 → 告警带越宽 (limit/ratio); ratio 越接近 1 → 带越窄
        self.assertAlmostEqual(warn_threshold(0.10, 0.8, True), 0.125)
        self.assertAlmostEqual(warn_threshold(0.10, 0.5, True), 0.20)
        self.assertAlmostEqual(warn_threshold(0.10, 0.95, True), 0.10 / 0.95, places=6)
        self.assertEqual(classify_limit(0.15, 0.10, True, 0.5), "高")    # 宽带 → 仍告警
        self.assertEqual(classify_limit(0.15, 0.10, True, 0.95), "")     # 窄带 → 合格
        self.assertEqual(classify_limit(0.105, 0.10, True, 0.95), "高")

    def test_rules_config_rejudge_uses_same_helper(self):
        cfg = RulesConfig({"limit_warn_ratio": 0.8,
                           "rules": {"最小线宽": {"min": 0.10, "severity": "危险"}}})
        from dfm.core.issue import make_issue

        # 0.09 → 违规(危险); 0.11 → 接近极限(降一级); 0.20 → 合规剔除
        bad = cfg.apply([make_issue(code="A", category="线路", rule="最小线宽",
                                    severity="中", description="d", measured=0.09)])
        self.assertEqual(bad[0]["severity"], "危险")
        warn = cfg.apply([make_issue(code="A", category="线路", rule="最小线宽",
                                     severity="中", description="d", measured=0.11)])
        self.assertEqual(len(warn), 1)
        self.assertEqual(warn[0]["severity"], "低")        # 中 → 降一级
        ok = cfg.apply([make_issue(code="A", category="线路", rule="最小线宽",
                                   severity="危险", description="d", measured=0.20)])
        self.assertEqual(ok, [])

    def test_capability_warn_follows_rules_ratio(self):
        """能力比对的"接近制程极限"也受 limit_warn_ratio 控制。"""
        from dfm.core.capability import analyze_design

        # jlcpcb 1oz 铜厚能力: 最小线宽 0.10mm
        params = {"min_line_width": 0.15, "copper_um": 35}
        narrow = analyze_design(params, factory="jlcpcb",
                                rules=RulesConfig({"limit_warn_ratio": 0.95}))
        wide = analyze_design(params, factory="jlcpcb",
                              rules=RulesConfig({"limit_warn_ratio": 0.5}))
        n = {it["item"]: it["status"] for it in narrow["items"]}
        w = {it["item"]: it["status"] for it in wide["items"]}
        self.assertEqual(n["最小线宽"], "pass")          # 带窄: 0.15 > 0.105 → 合格
        self.assertEqual(w["最小线宽"], "warn_limit")    # 带宽: 0.15 < 0.20 → 接近极限


class TestIssueCounting(unittest.TestCase):
    """处数与留存分离 (旧实现把留存数当处数, 密集板会少报)。"""

    def test_collector_counts_beyond_cap(self):
        col = IssueCollector(cap=3)
        for i in range(10):
            col.add("线路", "最小线距", "危险", f"第{i}处", x=float(i), y=0.0,
                    measured=0.05, required=0.1, unit="mm")
        self.assertEqual(col.count("线路", "最小线距"), 10)      # 真实处数
        self.assertEqual(len(col.instances), 3)                  # 留存上限
        self.assertTrue(col.truncated("线路", "最小线距"))
        agg = col.aggregate()
        self.assertEqual(len(agg), 1)
        self.assertEqual(agg[0]["count"], 10)
        self.assertTrue(agg[0]["truncated"])
        self.assertEqual(agg[0]["instance_cap"], 3)
        self.assertEqual(len(agg[0]["locations"]), 3)
        self.assertIn("共发现 10 处", agg[0]["description"])

    def test_collector_not_truncated_below_cap(self):
        col = IssueCollector(cap=100)
        for i in range(5):
            col.add("钻孔", "最小机械钻孔", "危险", f"d{i}", x=float(i), y=0.0)
        agg = col.aggregate()
        self.assertEqual(agg[0]["count"], 5)
        self.assertFalse(agg[0].get("truncated"))

    def test_aggregate_with_external_counts(self):
        from dfm.core.issue import make_issue

        instances = [make_issue(code="X", category="线路", rule="R", severity="高",
                                description="d", x=1.0, y=1.0)]
        agg = aggregate_by_rule(instances, counts={("线路", "R"): 812})
        self.assertEqual(agg[0]["count"], 812)
        self.assertTrue(agg[0]["truncated"])

    def test_stats_summary(self):
        col = IssueCollector(cap=1)
        col.add("线路", "R1", "中", "a", x=0, y=0)
        col.add("线路", "R1", "中", "b", x=1, y=1)
        s = col.stats()
        self.assertEqual(s["违规处数合计"], 2)
        self.assertEqual(s["留存实例数"], 1)
        self.assertIn("线路/R1", s["被截断的规则"])


class TestCoverage(unittest.TestCase):
    def test_judged_and_missed(self):
        cov = Coverage(check_id="fabrication")
        cov.judged("线路", "最小线宽", 0.1)
        cov.missed("阻焊", "最小阻焊桥", "能力库无该项数据")
        self.assertEqual(cov.ratio, 0.5)
        d = cov.as_dict()
        self.assertEqual(d["judged_count"], 1)
        self.assertEqual(d["missed_count"], 1)
        self.assertEqual(d["missed"][0]["rule"], "阻焊/最小阻焊桥")

    def test_miss_if_none(self):
        cov = Coverage()
        self.assertTrue(cov.miss_if_none("钻孔", "最小激光孔", None))
        self.assertFalse(cov.miss_if_none("钻孔", "最小机械钻孔", 0.2))
        self.assertEqual(len(cov.skipped), 1)
        self.assertEqual(len(cov.evaluated), 1)

    def test_pipeline_reports_coverage(self):
        res = analyze({"source_type": "json", "source_path": SAMPLE_JSON,
                       "factory": "jlcpcb", "board_type": "rigid",
                       "capability_level": "standard", "copper_um": 35,
                       "board_thickness": 1.6, "load_plugins": False})
        self.assertIn("coverage", res)
        self.assertIn("coverage_ratio", res)
        self.assertIn("data_quality", res)
        self.assertTrue(res["coverage"])
        fab = [c for c in res["coverage"] if c["check"] == "fabrication"]
        self.assertTrue(fab)
        self.assertGreater(fab[0]["judged_count"], 0)
        # jlcpcb 能力库无塞孔/覆盖膜等细节 → 应有未判定项
        self.assertIsInstance(res["missed_rules"], list)

    def test_quality_issues_emitted(self):
        res = analyze({"source_type": "json", "source_path": SAMPLE_JSON,
                       "factory": "jlcpcb", "board_thickness": 1.6,
                       "load_plugins": False})
        dq_issues = [it for it in res["issues"] if it["module"] == "data_quality"]
        self.assertTrue(dq_issues)
        self.assertTrue(any("未判定" in it["rule"] for it in dq_issues))

    def test_quality_check_can_be_disabled(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "checks.yaml")
            with open(p, "w", encoding="utf-8") as f:
                yaml.safe_dump({"checks": {"data_quality": {"enabled": False}}}, f)
            res = analyze({"source_type": "json", "source_path": SAMPLE_JSON,
                           "factory": "jlcpcb", "board_thickness": 1.6,
                           "rules_path": p, "load_plugins": False})
            self.assertNotIn("data_quality", res["checks_run"])

    def test_data_quality_dict_shape(self):
        dq = DataQuality(source_kind="gerber")
        dq.add_notes(["宏光圈未展开"])
        dq.add_truncation("规则 X 超出留存上限")
        cov = Coverage(check_id="fabrication")
        cov.judged("线路", "最小线宽", 0.1)
        cov.missed("阻焊", "最小阻焊桥")
        dq.add_coverage(cov)
        d = dq.as_dict()
        self.assertEqual(d["数据源"], "gerber")
        self.assertEqual(d["未判定规则数"], 1)
        self.assertIn("规则判定覆盖率", d)
        issues = quality_issues(dq)
        rules = {it["rule"] for it in issues}
        self.assertIn("部分规则未判定", rules)
        self.assertIn("数据量超限截断", rules)
        self.assertIn("解析受限", rules)


class TestFatalErrors(unittest.TestCase):
    def test_unreadable_source_raises(self):
        with self.assertRaises(RuntimeError) as cm:
            analyze({"source_type": "json",
                     "source_path": os.path.join(tempfile.gettempdir(), "__no_such__.json"),
                     "factory": "jlcpcb", "load_plugins": False})
        self.assertIn("数据源解析失败", str(cm.exception))

    def test_params_only_source_still_works(self):
        res = analyze({"source_type": "params", "source_path": "",
                       "design_params": {"min_line_width": 0.05, "copper_um": 35},
                       "factory": "jlcpcb", "board_type": "rigid",
                       "capability_level": "standard", "load_plugins": False})
        self.assertIn("capability", res["checks_run"])

    def test_check_failure_recorded_not_silent(self):
        from dfm.core import registry

        @registry.register_check(id="tmp_boom", name="会抛异常的检查",
                                 requires=(), order=999)
        def _boom(ctx):
            raise ValueError("故意失败")

        try:
            res = analyze({"source_type": "params", "source_path": "",
                           "design_params": {"min_line_width": 0.2},
                           "factory": "jlcpcb", "modules": ["tmp_boom"],
                           "load_plugins": False})
            self.assertIn("tmp_boom_error", res)
            self.assertTrue(res["data_quality"]["致命问题"])
        finally:
            registry.unregister_check("tmp_boom")


class TestGeometryDrivenAccuracy(unittest.TestCase):
    """端到端: 真实形状与极性合成后, 误报应消失。"""

    def test_rect_pads_parallel_no_false_short(self):
        """两个长条矩形焊盘平行放置 (净距 2mm): 不应报短路/间距违规。"""
        raw = make_raw()
        raw["nets"] = [
            {"name": "A", "nodes": [{"refdes": "J1", "pin": "1", "x": 0, "y": 0, "layer": "TOP"}]},
            {"name": "B", "nodes": [{"refdes": "J2", "pin": "1", "x": 0, "y": 3, "layer": "TOP"}]},
        ]
        raw["pads"] = [
            {"refdes": "J1", "pin": "1", "net": "A", "x": 0, "y": 0, "layer": "TOP",
             "is_smd": True, "pad_w": 10.0, "pad_h": 1.0, "drill_mm": 0,
             "pad_shape": "rect"},
            {"refdes": "J2", "pin": "1", "net": "B", "x": 0, "y": 3, "layer": "TOP",
             "is_smd": True, "pad_w": 10.0, "pad_h": 1.0, "drill_mm": 0,
             "pad_shape": "rect"},
        ]
        raw["clines"] = []
        raw["vias"] = []
        raw["shapes"] = []
        raw["drc"] = []
        from dfm.checks.netlist import analyze_netlist

        issues, summary = analyze_netlist(design(**raw))
        self.assertEqual(summary["short_net_count"], 0)      # 旧实现(外接圆)会误报短路

    def test_clear_feature_not_copper(self):
        """负片 anti-pad 不应参与短路判定。"""
        raw = make_raw()
        raw["nets"] = [
            {"name": "A", "nodes": [{"refdes": "R1", "pin": "1", "x": 0, "y": 0, "layer": "TOP"}]},
            {"name": "B", "nodes": [{"refdes": "R2", "pin": "1", "x": 0.3, "y": 0, "layer": "TOP"}]},
        ]
        raw["pads"] = [
            {"refdes": "R1", "pin": "1", "net": "A", "x": 0, "y": 0, "layer": "TOP",
             "is_smd": True, "pad_w": 0.5, "pad_h": 0.5, "drill_mm": 0},
            # 负片图元: 位于两焊盘之间, 实际是挖空, 不构成铜
            {"refdes": "", "pin": "", "net": "B", "x": 0.3, "y": 0, "layer": "TOP",
             "is_smd": True, "pad_w": 0.5, "pad_h": 0.5, "drill_mm": 0,
             "polarity": "clear"},
        ]
        raw["clines"] = []
        raw["vias"] = []
        raw["shapes"] = []
        raw["drc"] = []
        raw["testpoints"] = []
        from dfm.geom.board import BoardGeometry

        g = BoardGeometry(design=design(**raw))
        self.assertEqual(len(g.elements), 1)                 # clear 元素被排除
        self.assertEqual(g.polarity.clear_elements, 1)
        self.assertEqual(g.polarity.dropped_elements, 0)


class TestCountingConsistency(unittest.TestCase):
    """回归: 多来源问题合并时不得产生"假截断"。

    网表检查既产出几何短路实例, 也会并入 CAD vs FAB 比对问题;
    若两者计数口径不一致 (counts 累加而 retained 未累加), 会误报
    "处数超出留存上限"。
    """

    def test_no_false_truncation_with_fab_compare(self):
        import json

        with tempfile.TemporaryDirectory() as d:
            write_gerber_set(d)
            cad = os.path.join(d, "cad.json")
            with open(cad, "w", encoding="utf-8") as f:
                json.dump({"GND": ["R1.1", "R2.1"], "SIG1": ["U1.1", "U1.2"]}, f)
            res = analyze({
                "source_type": "gerber", "source_path": d, "fab_path": d,
                "cad_nets": cad, "factory": "jlcpcb", "board_type": "rigid",
                "capability_level": "standard", "copper_um": 35.0,
                "board_thickness": 1.6, "load_plugins": False,
            })
            self.assertIn("fabnet", res["checks_run"])
            # 未设置极小上限时不应出现截断
            self.assertEqual(res["data_quality"]["截断"], [])
            self.assertTrue(all(not it.get("truncated") for it in res["issues"]))

    def test_real_truncation_reported_when_cap_small(self):
        res = analyze({"source_type": "json", "source_path": SAMPLE_JSON,
                       "factory": "jlcpcb", "board_thickness": 1.6,
                       "instance_cap": 1, "load_plugins": False})
        trunc = [it for it in res["issues"] if it.get("truncated")]
        self.assertTrue(trunc, "处数多于留存上限时应标记 truncated")
        for it in trunc:
            self.assertGreater(it["count"], it.get("instance_cap", 0))
        self.assertTrue(res["data_quality"]["截断"])


if __name__ == "__main__":
    unittest.main()

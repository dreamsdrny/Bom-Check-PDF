"""Issue 模型 / 汇总 / 评分 单元测试。"""
import unittest

from tests.helpers import design  # noqa: F401  (确保 sys.path 就绪)

from dfm.core.issue import (SEVERITY_LEVELS, aggregate_by_rule, compute_risk_score,
                            make_issue, renumber_issues, summarize_issues)


class TestIssueModel(unittest.TestCase):
    def test_make_issue_defaults(self):
        it = make_issue(code="T-01", category="线路", rule="最小线宽", severity="危险",
                        description="线宽不足")
        self.assertEqual(it["severity"], "危险")
        self.assertEqual(it["count"], 1)
        self.assertTrue(it["suggestion"])          # 规则名命中内置建议库
        self.assertEqual(it["layer"], "top")       # DIRECTION_HINT 回填

    def test_invalid_severity_falls_back(self):
        it = make_issue(code="T-02", category="线路", rule="x", severity="不存在",
                        description="d")
        self.assertEqual(it["severity"], "中")

    def test_summarize(self):
        issues = [make_issue(code=f"A-{i}", category="线路", rule="r", severity=s,
                             description="d")
                  for i, s in enumerate(["危险", "高", "中", "低", "中"])]
        s = summarize_issues(issues)
        self.assertEqual(s["总计"], 5)
        self.assertEqual(s["危险"], 1)
        self.assertEqual(s["中"], 2)
        self.assertEqual(set(SEVERITY_LEVELS) - set(s), set())


class TestAggregate(unittest.TestCase):
    def test_group_by_rule_and_locations(self):
        instances = [
            make_issue(code="X", category="线路", rule="最小线距", severity="危险",
                       description="a", x=1.0, y=2.0),
            make_issue(code="X", category="线路", rule="最小线距", severity="高",
                       description="b", x=3.0, y=4.0),
            make_issue(code="X", category="钻孔", rule="最小机械钻孔", severity="中",
                       description="c", x=5.0, y=6.0),
        ]
        agg = aggregate_by_rule(instances)
        self.assertEqual(len(agg), 2)
        first = [a for a in agg if a["rule"] == "最小线距"][0]
        self.assertEqual(first["count"], 2)
        self.assertEqual(first["severity"], "危险")          # 取组内最高
        self.assertEqual(len(first["locations"]), 2)
        self.assertIn("共发现 2 处", first["description"])

    def test_renumber_unique_and_sorted(self):
        issues = [
            make_issue(code="DUP", category="线路", rule="r1", severity="低", description="d"),
            make_issue(code="DUP", category="钻孔", rule="r2", severity="危险", description="d"),
            make_issue(code="DUP", category="网络比对", rule="开路(Open)", severity="高",
                       description="d"),
        ]
        out = renumber_issues(issues)
        codes = [it["code"] for it in out]
        self.assertEqual(len(set(codes)), 3)
        self.assertTrue(codes[0].startswith("FAB-DR"))     # 危险优先, 按类别前缀编码
        self.assertTrue(codes[1].startswith("NET"))        # 开路(Open) 用 NET 前缀
        self.assertEqual([it["severity"] for it in out], ["危险", "高", "低"])

    def test_keep_code_preserved(self):
        issues = [make_issue(code="CUS-99", category="自定义", rule="r", severity="中",
                             description="d")]
        issues[0]["keep_code"] = True
        out = renumber_issues(issues)
        self.assertEqual(out[0]["code"], "CUS-99")


class TestScoring(unittest.TestCase):
    def test_perfect_score(self):
        risk = compute_risk_score([])
        self.assertEqual(risk["score"], 100.0)
        self.assertEqual(risk["risk_level"], "低")
        self.assertIn("可投产", risk["verdict"])

    def test_penalty_and_floor(self):
        issues = [make_issue(code=f"A-{i}", category="线路", rule="r", severity="危险",
                             description="d") for i in range(3)]
        risk = compute_risk_score(issues)
        self.assertEqual(risk["score"], 70.0)                # 100 - 3*10
        self.assertEqual(risk["risk_level"], "危险")
        many = issues * 20
        self.assertEqual(compute_risk_score(many)["score"], 0.0)   # 扣分封顶

    def test_custom_weights(self):
        issues = [make_issue(code="A-1", category="线路", rule="r", severity="高",
                             description="d")]
        default = compute_risk_score(issues)["score"]
        custom = compute_risk_score(issues, {"weights": {"高": 20.0}})["score"]
        self.assertEqual(default, 95.0)
        self.assertEqual(custom, 80.0)


if __name__ == "__main__":
    unittest.main()

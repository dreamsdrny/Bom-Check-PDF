"""检查注册表与插件加载单元测试。"""
import os
import shutil
import tempfile
import unittest

from tests.helpers import design  # noqa: F401

from dfm.core import registry
from dfm.core.issue import make_issue
from dfm.core.rules_config import RulesConfig

PLUGIN_SRC = '''"""测试插件"""
from dfm.core.issue import make_issue
from dfm.core.registry import REQ_DESIGN, register_check


@register_check(id="tmp_test_check", name="临时测试检查", group="测试",
                description="unit test", requires=(REQ_DESIGN,), order=999)
def run(ctx):
    return [make_issue(code="TMP-01", category="线路", rule="临时规则",
                       severity="低", description="来自插件",
                       x=1.0, y=2.0)]
'''

BROKEN_PLUGIN_SRC = "raise RuntimeError('boom')\n"


class TestRegistry(unittest.TestCase):
    def setUp(self):
        registry.load_builtin_checks()

    def test_builtin_checks_registered(self):
        ids = [c.id for c in registry.list_checks()]
        for expect in ("fabdata", "fabrication", "netlist", "assembly",
                       "panelization", "capability", "fabnet", "netclass"):
            self.assertIn(expect, ids)

    def test_order_sorted(self):
        orders = [c.order for c in registry.list_checks()]
        self.assertEqual(orders, sorted(orders))

    def test_requires_validation(self):
        with self.assertRaises(ValueError):
            registry.register_check(id="bad", name="bad", requires=("nonexistent",))(
                lambda ctx: [])

    def test_context_availability(self):
        ctx = registry.CheckContext(params={"a": 1}, design=design(),
                                    capability={"trace": {}}, fab_data=[{"kind": "gerber"}],
                                    cad_nets={"N1": {"R1.1"}}, rules=RulesConfig())
        have = ctx.available()
        for req in ("design", "params", "capability", "fab", "cad"):
            self.assertIn(req, have)
        self.assertEqual(ctx.copper_um, 35.0)
        self.assertEqual(ctx.board_thickness, 1.6)     # 来自 design.thickness_mm

    def test_missing_requires(self):
        spec = registry.get_check("fabrication")
        ctx = registry.CheckContext()                  # 无任何数据
        self.assertEqual(set(spec.missing_requires(ctx)), {"design", "capability"})

    def test_register_and_unregister(self):
        @registry.register_check(id="tmp_inline", name="内联", requires=(), order=998)
        def _run(ctx):
            return []

        self.assertIsNotNone(registry.get_check("tmp_inline"))
        self.assertTrue(registry.unregister_check("tmp_inline"))
        self.assertIsNone(registry.get_check("tmp_inline"))

    def test_context_issue_helper(self):
        ctx = registry.CheckContext()
        ctx.issue(code="H-1", category="线路", rule="r", severity="中", description="d")
        self.assertEqual(len(ctx.issues), 1)


class TestPlugins(unittest.TestCase):
    def setUp(self):
        registry.load_builtin_checks()
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        registry.unregister_check("tmp_test_check")
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_load_plugin_registers_check(self):
        path = os.path.join(self.tmp, "tmp_plugin.py")
        with open(path, "w", encoding="utf-8") as f:
            f.write(PLUGIN_SRC)
        report = registry.load_plugins(dirs=[self.tmp], force=True)
        self.assertEqual(len(report), 1)
        self.assertEqual(report[0]["status"], "ok")
        self.assertIn("tmp_test_check", report[0]["checks"])
        spec = registry.get_check("tmp_test_check")
        self.assertIsNotNone(spec)
        out = spec.run(registry.CheckContext(design=design()))
        self.assertEqual(out[0]["code"], "TMP-01")

    def test_broken_plugin_reported_not_fatal(self):
        path = os.path.join(self.tmp, "broken_plugin.py")
        with open(path, "w", encoding="utf-8") as f:
            f.write(BROKEN_PLUGIN_SRC)
        report = registry.load_plugins(dirs=[self.tmp], force=True)
        self.assertEqual(report[0]["status"], "error")
        self.assertIn("boom", report[0]["error"])

    def test_underscore_files_skipped(self):
        path = os.path.join(self.tmp, "_ignored.py")
        with open(path, "w", encoding="utf-8") as f:
            f.write(PLUGIN_SRC)
        report = registry.load_plugins(dirs=[self.tmp], force=True)
        self.assertEqual(report, [])

    def test_package_plugin(self):
        pkg = os.path.join(self.tmp, "tmp_pkg")
        os.makedirs(pkg)
        with open(os.path.join(pkg, "__init__.py"), "w", encoding="utf-8") as f:
            f.write(PLUGIN_SRC)
        report = registry.load_plugins(dirs=[self.tmp], force=True)
        self.assertEqual(report[0]["status"], "ok")
        self.assertIn("tmp_test_check", report[0]["checks"])


if __name__ == "__main__":
    unittest.main()

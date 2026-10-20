"""各检查项单元测试: 几何制造 / 开短路 / 装配 SMT / 网络分类。"""
import unittest

from tests.helpers import design, make_raw

from dfm.checks.assembly import (check_chip_pad_symmetry, check_component_spacing,
                                 check_component_to_edge, check_fiducials,
                                 check_fine_pitch, check_test_points, check_via_in_pad,
                                 component_boxes)
from dfm.checks.fabrication import analyze_geometry
from dfm.checks.netclass import classify_net, find_diff_pairs, DEFAULT_PATTERNS
from dfm.checks.netlist import analyze_netlist
from dfm.core.capability import load_capability


def capability():
    return load_capability("rigid", "standard", factory="jlcpcb")


class TestFabricationGeometry(unittest.TestCase):
    def setUp(self):
        self.d = design()
        self.issues = analyze_geometry(self.d, capability(), copper_um=35.0,
                                       copper_oz=1.0, board_type="rigid")

    def test_finds_narrow_trace(self):
        rules = {it["rule"] for it in self.issues}
        self.assertIn("最小线宽", rules)
        narrow = [it for it in self.issues if it["rule"] == "最小线宽"][0]
        self.assertAlmostEqual(narrow["measured"], 0.05)
        self.assertIsNotNone(narrow["x"])
        self.assertEqual(narrow["severity"], "危险")

    def test_finds_small_drill_and_annular_ring(self):
        rules = {it["rule"] for it in self.issues}
        self.assertIn("最小机械钻孔", rules)
        self.assertIn("单边孔环(机械孔)", rules)

    def test_finds_solder_mask_bridge(self):
        rules = {it["rule"] for it in self.issues}
        self.assertIn("最小阻焊桥", rules)

    def test_drc_passthrough(self):
        drc = [it for it in self.issues if it["category"] == "DRC"]
        self.assertTrue(drc)
        self.assertEqual(drc[0]["severity"], "危险")
        self.assertAlmostEqual(drc[0]["x"], 35.0)

    def test_all_issues_have_coordinates(self):
        for it in self.issues:
            self.assertIsNotNone(it.get("x"), it["rule"])
            self.assertTrue(it["description"])
            self.assertTrue(it["suggestion"])

    def test_clean_board_has_no_geometry_issues(self):
        """把样例板改到全面满足能力后, 线宽/孔径/孔环类问题应消失。"""
        raw = make_raw()
        for cl in raw["clines"]:
            cl["width"] = max(cl["width"], 0.30)
        for v in raw["vias"]:
            v["drill_mm"] = 0.35
            v["pad_mm"] = 0.95          # 单边孔环 0.30mm, 远离能力极限
        raw["drc"] = []
        raw["pads"] = [p for p in raw["pads"] if p["refdes"] != "C1"]
        d = design(**raw)
        issues = analyze_geometry(d, capability(), 35.0, 1.0, "rigid")
        narrow = [it for it in issues if it["rule"] in ("最小线宽", "最小机械钻孔",
                                                        "单边孔环(机械孔)", "PTH纵横比")]
        self.assertEqual(narrow, [])


class TestNetlist(unittest.TestCase):
    def test_open_and_short_detected(self):
        d = design()
        issues, summary = analyze_netlist(d)
        rules = {it["rule"] for it in issues}
        self.assertIn("短路(Short)", rules)
        self.assertIn("开路(Open)", rules)
        self.assertGreaterEqual(summary["short_net_count"], 1)
        self.assertGreaterEqual(summary["open_net_count"], 1)
        for key in ("logical_net_count", "physical_component_count", "open_net_count",
                    "short_net_count"):
            self.assertIn(key, summary)

    def test_short_has_location(self):
        d = design()
        issues, _ = analyze_netlist(d)
        shorts = [it for it in issues if it["rule"] == "短路(Short)"]
        self.assertTrue(shorts)
        self.assertIsNotNone(shorts[0]["x"])

    def test_clean_netlist_passes(self):
        raw = make_raw()
        raw["nets"] = [{"name": "GND", "nodes": [
            {"refdes": "R1", "pin": "1", "x": 10, "y": 10, "layer": "TOP"},
            {"refdes": "R2", "pin": "1", "x": 10, "y": 20, "layer": "TOP"}]}]
        raw["clines"] = [{"net": "GND", "layer": "TOP", "width": 0.2,
                          "points": [[10, 10], [10, 20]]}]
        raw["pads"] = [p for p in raw["pads"] if p["net"] == "GND"]
        raw["vias"] = []
        raw["shapes"] = []
        d = design(**raw)
        issues, summary = analyze_netlist(d)
        self.assertEqual(summary["short_net_count"], 0)
        self.assertEqual(summary["open_net_count"], 0)


class TestAssembly(unittest.TestCase):
    def test_component_boxes(self):
        boxes = component_boxes(design())
        refs = {b["refdes"] for b in boxes}
        self.assertIn("R1", refs)
        self.assertIn("U1", refs)

    def test_component_spacing_violation(self):
        raw = make_raw()
        raw["pads"] += [
            {"refdes": "X1", "pin": "1", "net": "N1", "x": 30.0, "y": 30.0,
             "layer": "TOP", "is_smd": True, "pad_w": 1.0, "pad_h": 1.0, "drill_mm": 0},
            {"refdes": "X1", "pin": "2", "net": "N1", "x": 31.0, "y": 30.0,
             "layer": "TOP", "is_smd": True, "pad_w": 1.0, "pad_h": 1.0, "drill_mm": 0},
            {"refdes": "X2", "pin": "1", "net": "N2", "x": 31.4, "y": 30.0,
             "layer": "TOP", "is_smd": True, "pad_w": 1.0, "pad_h": 1.0, "drill_mm": 0},
            {"refdes": "X2", "pin": "2", "net": "N2", "x": 32.4, "y": 30.0,
             "layer": "TOP", "is_smd": True, "pad_w": 1.0, "pad_h": 1.0, "drill_mm": 0},
        ]
        out = check_component_spacing(design(**raw), 0.5)
        pairs = {(it["from"], it["to"]) for it in out}
        self.assertIn(("X1", "X2"), pairs)
        self.assertTrue(all(it["category"] == "组装" for it in out))

    def test_component_to_edge(self):
        raw = make_raw()
        raw["pads"] += [
            {"refdes": "E1", "pin": "1", "net": "N", "x": 0.4, "y": 20.0,
             "layer": "TOP", "is_smd": True, "pad_w": 0.6, "pad_h": 0.6, "drill_mm": 0},
            {"refdes": "E1", "pin": "2", "net": "N", "x": 1.0, "y": 20.0,
             "layer": "TOP", "is_smd": True, "pad_w": 0.6, "pad_h": 0.6, "drill_mm": 0},
        ]
        out = check_component_to_edge(design(**raw), 2.0)
        self.assertTrue(any(it["from"] == "" or "E1" in it["description"] for it in out))

    def test_fiducial_missing(self):
        out = check_fiducials(design(), 2, 1.0)
        self.assertTrue(out)
        self.assertEqual(out[0]["rule"], "基准点数量")
        self.assertEqual(out[0]["severity"], "高")

    def test_fiducial_detected(self):
        raw = make_raw()
        raw["components"] += [
            {"refdes": "FID1", "package": "FID", "x": 3, "y": 3, "rotation": 0,
             "side": "TOP", "pins": 1},
            {"refdes": "FID2", "package": "FID", "x": 47, "y": 3, "rotation": 0,
             "side": "TOP", "pins": 1},
            {"refdes": "FID3", "package": "FID", "x": 47, "y": 37, "rotation": 0,
             "side": "TOP", "pins": 1},
        ]
        raw["pads"] += [
            {"refdes": f"FID{i}", "pin": "1", "net": "", "x": x, "y": y, "layer": "TOP",
             "is_smd": True, "pad_w": 1.2, "pad_h": 1.2, "drill_mm": 0}
            for i, (x, y) in enumerate([(3, 3), (47, 3), (47, 37)], start=1)]
        out = check_fiducials(design(**raw), 2, 1.0)
        self.assertEqual(out, [])

    def test_chip_pad_symmetry(self):
        raw = make_raw()
        raw["pads"] += [
            {"refdes": "Y1", "pin": "1", "net": "N", "x": 25, "y": 5, "layer": "TOP",
             "is_smd": True, "pad_w": 1.0, "pad_h": 1.0, "drill_mm": 0},
            {"refdes": "Y1", "pin": "2", "net": "N", "x": 27, "y": 5, "layer": "TOP",
             "is_smd": True, "pad_w": 0.4, "pad_h": 0.4, "drill_mm": 0},
        ]
        out = check_chip_pad_symmetry(design(**raw), 0.8)
        self.assertTrue(any("Y1" in it["description"] for it in out))

    def test_via_in_pad(self):
        raw = make_raw()
        raw["pads"] += [
            {"refdes": "B1", "pin": "A1", "net": "N", "x": 25, "y": 25, "layer": "TOP",
             "is_smd": True, "pad_w": 0.8, "pad_h": 0.8, "drill_mm": 0},
        ]
        raw["vias"] += [{"net": "N", "x": 25, "y": 25, "drill_mm": 0.2, "pad_mm": 0.4,
                         "padstack": "V", "start_layer": "TOP", "end_layer": "BOTTOM",
                         "type": "through"}]
        out = check_via_in_pad(design(**raw), allowed=False)
        self.assertTrue(out)
        self.assertEqual(out[0]["rule"], "Via in Pad")
        self.assertEqual(out[0]["severity"], "高")
        self.assertEqual(check_via_in_pad(design(**raw), allowed=True)[0]["severity"], "低")

    def test_fine_pitch(self):
        pads = []
        for i in range(4):
            for j in range(4):
                pads.append({"refdes": "U9", "pin": f"{i}{j}", "net": "N",
                             "x": 20 + i * 0.35, "y": 20 + j * 0.35, "layer": "TOP",
                             "is_smd": True, "pad_w": 0.25, "pad_h": 0.25, "drill_mm": 0})
        raw = make_raw(pads=make_raw()["pads"] + pads)
        out = check_fine_pitch(design(**raw), 0.4, 0.05)
        self.assertTrue(any(it["rule"] == "BGA焊盘" for it in out))

    def test_test_points(self):
        d = design()
        out = check_test_points(d, coverage_min=0.5, pad_min=0.8, edge_min=1.27)
        rules = {it["rule"] for it in out}
        self.assertTrue(rules & {"测试点覆盖率", "测试点尺寸"})


class TestNetClass(unittest.TestCase):
    def test_classify(self):
        self.assertEqual(classify_net("GND", DEFAULT_PATTERNS)[0], "GND")
        self.assertEqual(classify_net("3V3_MB", DEFAULT_PATTERNS)[0], "POWER")
        self.assertEqual(classify_net("ETH_MDI0_P", DEFAULT_PATTERNS)[0], "ETHERNET")
        self.assertEqual(classify_net("DDR_DQ0", DEFAULT_PATTERNS)[0], "DDR")
        self.assertEqual(classify_net("SOME_RANDOM_NET", DEFAULT_PATTERNS)[0], "DEFAULT")

    def test_impedance_flag(self):
        cls, cfg = classify_net("PCIE_TX0_P", DEFAULT_PATTERNS)
        self.assertTrue(cfg.get("impedance"))
        self.assertTrue(cfg.get("diff"))

    def test_diff_pairs(self):
        pairs = find_diff_pairs(["USB_DP", "USB_DM", "CLK_25MHZ"])
        self.assertIn(("USB_DP", "USB_DM"), pairs)
        self.assertEqual(len(pairs), 1)

    def test_diff_pairs_variants(self):
        self.assertEqual(len(find_diff_pairs(["CANH", "CANL"])), 1)
        self.assertEqual(len(find_diff_pairs(["CAN_H", "CAN_L"])), 1)
        self.assertEqual(len(find_diff_pairs(["ETH_TX_POS", "ETH_TX_NEG"])), 1)
        self.assertEqual(len(find_diff_pairs(["A_P", "A_N", "B_P", "B_N"])), 2)
        self.assertEqual(find_diff_pairs(["GND", "VCC"]), [])

    def test_no_double_pairing(self):
        pairs = find_diff_pairs(["LVDS0_P", "LVDS0_N"])
        self.assertEqual(pairs, [("LVDS0_P", "LVDS0_N")])


if __name__ == "__main__":
    unittest.main()

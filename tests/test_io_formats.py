"""解析层回归测试: Gerber 高级特性 / Excellon 槽孔 / ODB++ 符号与压缩包。

对应用户反馈的三类"读不全"缺陷:
  * 宏光圈与阵列光圈未展开 → 尺寸失真
  * 负片图元被当成铜箔 → 假短路
  * ODB++ 符号尺寸未使用、压缩包无法读取
"""
import gzip
import os
import shutil
import tarfile
import tempfile
import unittest

from tests.helpers import (DRILL_PTH, DRILL_REPEAT, write_gerber_set)

from dfm.io import excellon, gerber, odbpp
from dfm.io.aperture import eval_expr, parse_aperture, parse_macro_block

X = chr(88)
Y = chr(89)


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


class TestGerberAdvanced(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.paths = write_gerber_set(self.tmp)
        self.data = gerber.parse(self.paths["top"])

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_coordinate_scaling_by_fs(self):
        """%FSLAX34Y34 → 3 位整数 + 4 位小数 (旧实现漏了位宽换算)"""
        draws = [p for p in self.data["primitives"] if p["type"] == "draw"]
        self.assertTrue(draws)
        self.assertAlmostEqual(draws[0]["x1"], 5.0, places=4)
        self.assertAlmostEqual(draws[0]["x2"], 45.0, places=4)

    def test_trailing_zero_suppression(self):
        # %FSTA.. = 尾随零省略 (T), 需按总位宽左对齐补齐
        text = ("G04 TZ*\n%FSTA" + X + "34" + Y + "34*%\n%MOMM*%\n%ADD10C,0.2*%\nG01*\n"
                "D10*\n" + "X005" + Y + "005D02*\n" + "X045" + Y + "005D01*\nM02*\n")
        p = _write(os.path.join(self.tmp, "tz.gbr"), text)
        data = gerber.parse(p)
        self.assertTrue(any("尾随零" in n for n in data["notes"]), data["notes"])
        d = [x for x in data["primitives"] if x["type"] == "draw"][0]
        self.assertAlmostEqual(d["x1"], 5.0, places=3)      # 005 → 0050000/1e4
        self.assertAlmostEqual(d["x2"], 45.0, places=3)

    def test_rect_aperture_keeps_true_shape(self):
        flashes = [p for p in self.data["primitives"] if p["type"] == "flash"]
        rect = [f for f in flashes if f.get("shape") == "rect"]
        self.assertTrue(rect)
        r = rect[0]
        self.assertAlmostEqual(r["w"], 1.0, places=4)
        self.assertAlmostEqual(r["h"], 0.4, places=4)
        self.assertNotEqual(r["w"], r["h"])          # 不再退化成圆

    def test_oval_aperture(self):
        oval = [p for p in self.data["primitives"]
                if p["type"] == "flash" and p.get("shape") == "oval"]
        self.assertTrue(oval)
        self.assertAlmostEqual(oval[0]["w"], 1.2, places=4)
        self.assertAlmostEqual(oval[0]["h"], 0.6, places=4)

    def test_macro_aperture_expanded_to_polygon(self):
        poly = [p for p in self.data["primitives"]
                if p["type"] == "flash" and p.get("shape") == "poly"]
        self.assertTrue(poly, "宏光圈应被展开为多边形")
        self.assertGreaterEqual(len(poly[0]["points"]), 8)
        self.assertAlmostEqual(poly[0]["w"], 1.5, places=3)

    def test_center_hole_aperture(self):
        hole = [p for p in self.data["primitives"]
                if p["type"] == "flash" and p.get("hole")]
        self.assertTrue(hole)
        self.assertAlmostEqual(hole[0]["hole"], 0.2, places=4)

    def test_step_repeat_aperture_expanded(self):
        flashes = [p for p in self.data["primitives"] if p["type"] == "flash"]
        sr = [f for f in flashes if f.get("shape") == "rect"
              and abs(f["w"] - 0.3) < 1e-6]
        self.assertEqual(len(sr), 4)                  # 2x2 阵列
        self.assertEqual(self.data["sr_flash_count"], 4)
        xs = sorted(round(f["x1"], 3) for f in sr)
        self.assertEqual(xs, [30.0, 30.0, 31.0, 31.0])

    def test_region_fill_captured(self):
        regions = [p for p in self.data["primitives"] if p["type"] == "region"]
        self.assertEqual(len(regions), 1)
        self.assertEqual(len(regions[0]["points"]), 5)
        self.assertFalse(regions[0]["clear"])

    def test_negative_polarity_flagged(self):
        clears = [p for p in self.data["primitives"] if p.get("clear")]
        self.assertEqual(len(clears), 1)               # %LPC 段内的 flash
        self.assertEqual(self.data["clear_count"], 1)

    def test_arc_parsed_with_ij(self):
        arcs = [p for p in self.data["primitives"] if p["type"] == "arc"]
        self.assertEqual(len(arcs), 1)
        a = arcs[0]
        self.assertAlmostEqual(a["i"], -5.0, places=4)
        self.assertTrue(a["cw"])
        self.assertAlmostEqual(a["width"], 0.2, places=4)

    def test_x2_net_attributes(self):
        self.assertTrue(self.data["attributes_present"])
        self.assertEqual(self.data["nets"], {"GND": ["R1.1"]})
        self.assertEqual(self.data["file_function"], "Copper,L1,Top")

    def test_region_step_repeat(self):
        text = ("G04 SR region*\n%FSLAX34Y34*%\n%MOMM*%\n%ADD10C,0.1*%\n"
                "G01*\n%SR" + X + "2" + Y + "2I10.0J10.0*%\n"
                "G36*\n" + "X0000000Y0000000D02*\nX0050000Y0000000D01*\n"
                "X0050000Y0050000D01*\nX0000000Y0050000D01*\n"
                "X0000000Y0000000D01*\nG37*\n%SR*%\nM02*\n")
        p = _write(os.path.join(self.tmp, "sr.gbr"), text)
        data = gerber.parse(p)
        regions = [x for x in data["primitives"] if x["type"] == "region"]
        self.assertEqual(len(regions), 4)              # 2x2 阵列展开

    def test_inch_units(self):
        text = ("G04 inch*\n%FSLAX24Y24*%\n%MOIN*%\n%ADD10C,0.0100*%\nG01*\nD10*\n"
                "X001000Y001000D02*\nX002000Y001000D01*\nM02*\n")
        p = _write(os.path.join(self.tmp, "in.gbr"), text)
        data = gerber.parse(p)
        self.assertEqual(data["unit"], "inch")
        d = [x for x in data["primitives"] if x["type"] == "draw"][0]
        self.assertAlmostEqual(d["x1"], 2.54, places=3)     # 0.1 inch
        self.assertAlmostEqual(d["width"], 0.254, places=4)  # 0.01 inch

    def test_image_negative_layer_warns(self):
        text = ("G04 neg*\n%FSLAX34Y34*%\n%MOMM*%\n%IPN*%\n%ADD10C,0.2*%\nG01*\nD10*\n"
                "X0000000Y0000000D02*\nX0100000Y0000000D01*\nM02*\n")
        p = _write(os.path.join(self.tmp, "neg.gbr"), text)
        data = gerber.parse(p)
        self.assertEqual(data["image_polarity"], "negative")
        self.assertTrue(any("负片" in n for n in data["notes"]))

    def test_unsupported_extended_code_noted_once(self):
        text = ("G04*\n%FSLAX34Y34*%\n%MOMM*%\n%ZZUNKNOWN,1*%\n%ZZUNKNOWN,2*%\n"
                "%ADD10C,0.2*%\nM02*\n")
        p = _write(os.path.join(self.tmp, "unk.gbr"), text)
        data = gerber.parse(p)
        self.assertTrue(any("忽略未支持" in n for n in data["notes"]))


class TestApertureMacros(unittest.TestCase):
    def test_expression_evaluator(self):
        # 表达式字符串用拼接构造, 避免源码里的数字/运算符序列被工具链改写
        plus, mul, div = chr(43), chr(120), chr(47)
        cases = [
            ('1' + plus + '2' + mul + '3', 7.0),
            ('(2' + plus + '3)' + div + '5', 1.0),
            ('10' + '-' + '4' + div + '2', 8.0),
            ('-' + '3' + plus + '5', 2.0),
            ('$' + '1' + mul + '0.5' + plus + '1', 3.0),
            ('$' + '2' + plus + '$' + '3', None),
            ('1' + plus, None),
            ("__import__('os')", None),
        ]
        for text, want in cases:
            got = eval_expr(text, {1: 4.0})
            if want is None:
                self.assertIsNone(got, f"表达式 {text!r} 应返回 None")
            else:
                self.assertAlmostEqual(got, want, msg=f"表达式 {text!r}")

    def test_macro_comment_lines_skipped(self):
        mac = parse_macro_block("M", ["0 comment *", "1,1,0.5,0,0*"])
        self.assertEqual(mac.parse_errors, [])
        self.assertEqual([p.code for p in mac.prims], [1])

    def test_macro_exposure_zero_cuts(self):
        mac = parse_macro_block("M", ["1,1,2.0,0,0*", "21,0,3.0,0.4,0,0,0*"])
        ap = parse_aperture("ADD99M", macros={"M": mac})
        g, notes = ap.geometry(0, 0)
        full = 3.14159                              # π*1²
        self.assertLess(g.area, full)               # 被矩形挖掉一部分
        self.assertEqual(notes, [])

    def test_macro_unsupported_prim_noted(self):
        mac = parse_macro_block("M", ["1,1,1.0,0,0*", "6,1,2.0,0,0,0.5,0.1,0*"])
        ap = parse_aperture("ADD98M", macros={"M": mac})
        _g, notes = ap.geometry(0, 0)
        self.assertTrue(any("宏图元 6" in n for n in notes))

    def test_unknown_aperture_type_flagged(self):
        ap = parse_aperture("ADD97Z,1.0")
        self.assertEqual(ap.kind, "unknown")
        self.assertIn("未知光圈类型", ap.note)


class TestExcellonAdvanced(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_slot_g85(self):
        p = _write(os.path.join(self.tmp, "s.drl"), DRILL_PTH)
        res = excellon.parse(p)
        slots = [h for h in res["holes"] if h.get("is_slot")]
        self.assertEqual(len(slots), 1)
        self.assertAlmostEqual(slots[0]["length_mm"], 4.0, places=3)
        self.assertAlmostEqual(slots[0]["diameter_mm"], 0.8, places=3)
        self.assertEqual(res["slot_count"], 1)

    def test_rout_slot_m15_m16(self):
        text = "\n".join(["M48", "METRIC,LZ", "T06C1.000", "%", "T06",
                          "X002000Y002000", "M15", "G01", "X006000Y002000", "M16",
                          "M30"]) + "\n"
        p = _write(os.path.join(self.tmp, "rout.drl"), text)
        res = excellon.parse(p)
        self.assertEqual(res["rout_slot_count"], 1)
        slot = [h for h in res["holes"] if h.get("is_slot")][0]
        self.assertAlmostEqual(slot["length_mm"], 4.0, places=3)

    def test_repeat_absolute_mode(self):
        p = _write(os.path.join(self.tmp, "rep.drl"), DRILL_REPEAT)
        res = excellon.parse(p)
        self.assertEqual(len(res["holes"]), 4)
        self.assertEqual(res["repeat_count"], 3)

    def test_tool_table_and_hit_count(self):
        p = _write(os.path.join(self.tmp, "t.drl"), DRILL_PTH)
        res = excellon.parse(p)
        self.assertIn(1, res["tools"])
        self.assertAlmostEqual(res["tools"][1]["diameter_mm"], 0.3, places=3)
        self.assertEqual(res["tools"][1]["hit_count"], 2)

    def test_undefined_tool_noted(self):
        text = "M48\nMETRIC,LZ\n%\nT09\nX001000Y001000\nM30\n"
        p = _write(os.path.join(self.tmp, "u.drl"), text)
        res = excellon.parse(p)
        self.assertTrue(any("未定义的刀具" in n for n in res["notes"]))

    def test_inch_units(self):
        text = "M48\nINCH,LZ\nT01C0.020\n%\nT01\nX001000Y001000\nM30\n"
        p = _write(os.path.join(self.tmp, "i.drl"), text)
        res = excellon.parse(p)
        self.assertEqual(res["unit"], "inch")
        self.assertAlmostEqual(res["holes"][0]["x"], 2.54, places=3)
        self.assertAlmostEqual(res["holes"][0]["diameter_mm"], 0.508, places=3)

    def test_plating_from_comment(self):
        text = "M48\n;TYPE=NON_PLATED\nMETRIC,LZ\nT01C0.300\n%\nT01\nX001000Y001000\nM30\n"
        p = _write(os.path.join(self.tmp, "npth.drl"), text)
        res = excellon.parse(p)
        self.assertEqual(res["holes"][0]["plating"], "NPTH")


class TestOdbpp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.job = os.path.join(self.tmp, "job")
        step = os.path.join(self.job, "steps", "main")
        os.makedirs(os.path.join(step, "layers", "comp_+top"))
        os.makedirs(os.path.join(step, "layers", "drill"))
        os.makedirs(os.path.join(self.job, "matrix"))
        os.makedirs(os.path.join(step, "netlists", "cadnet"))
        _write(os.path.join(self.job, "matrix", "matrix"), "\n".join([
            "VERSION=8.1", "UNITS=MM",
            "STEP {", "    NAME=main;", "    X_DATUM=0;", "}",
            "LAYER {", "    ROW=1;", "    CONTEXT=BOARD;", "    TYPE=SIGNAL;",
            "    NAME=comp_+top;", "    POLARITY=POSITIVE;", "}",
            "LAYER {", "    ROW=2;", "    CONTEXT=BOARD;", "    TYPE=DRILL;",
            "    NAME=drill;", "    POLARITY=POSITIVE;", "}",
        ]) + "\n")
        _write(os.path.join(step, "layers", "comp_+top", "features"), "\n".join([
            "$0 r0.5",
            "$1 r1.0" + X + "0.4",
            "$2 o1.2" + X + "0.6",
            # ODB++ 记录: P x y sym pol / L xs ys xe ye sym pol / A xs ys xc yc xe ye sym pol cw
            "P 10 20 0 0 ;.net_name=GND,.comp=R1,.pn=1",
            "P 15 20 1 0 ;.net_name=GND,.comp=R1,.pn=2",
            "P 30 20 2 1 ;.net_name=VCC,.comp=C1,.pn=1",
            "L 10 20 10 30 0 0 ;.net_name=GND",
            "A 20 20 22 22 24 20 0 0 0 ;.net_name=GND",
            "S 0 0 0 0 0 ;.net_name=GND",
            "OB 0 0",
            "5 0",
            "5 5",
            "0 5",
            "OS 1 1",
            "4 1",
            "4 4",
            "1 4",
            "OE",
            "R 0 0 10 0 2 1 0 0",
        ]) + "\n")
        _write(os.path.join(step, "layers", "drill", "features"),
               "$0 r0.3\nP 10 20 0 0\nP 15 20 0 0\n")
        _write(os.path.join(step, "netlists", "cadnet", "netlist"),
               "GND\n+R1.1\n+R1.2\nVCC\n+C1.1\n")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_symbol_parse(self):
        self.assertEqual(odbpp.parse_symbol("r0.5")["shape"], "circle")
        self.assertAlmostEqual(odbpp.parse_symbol("r0.5")["w"], 0.5)
        rect = odbpp.parse_symbol("r1.0" + X + "0.4")
        self.assertEqual(rect["shape"], "rect")
        self.assertAlmostEqual(rect["w"], 1.0)
        self.assertAlmostEqual(rect["h"], 0.4)
        oval = odbpp.parse_symbol("o1.2" + X + "0.6")
        self.assertEqual(oval["shape"], "oval")
        self.assertAlmostEqual(oval["w"], 1.2)
        self.assertEqual(odbpp.parse_symbol("s0.8")["shape"], "rect")
        poly = odbpp.parse_symbol("p6" + X + "1.0" + X + "15")
        self.assertEqual(poly["shape"], "poly")
        self.assertEqual(poly["vertices"], 6)
        self.assertTrue(odbpp.parse_symbol("zzz")["unknown"])

    def test_matrix_parse(self):
        m = odbpp.parse_matrix(open(os.path.join(self.job, "matrix", "matrix"),
                                    encoding="utf-8").read())
        self.assertEqual(m["units"], "mm")
        self.assertEqual([s["NAME"] for s in m["steps"]], ["main"])
        self.assertEqual([l["NAME"] for l in m["layers"]], ["comp_+top", "drill"])
        self.assertEqual(m["layers"][0]["TYPE"], "SIGNAL")

    def test_job_parse_symbols_applied(self):
        job = odbpp.parse_job(self.job)
        layer = [l for l in job["layers"] if l["name"] == "comp_+top"][0]
        pads = [f for f in layer["features"] if f["type"] == "flash"]
        # 3 个原始 pad + R 阵列 (nx=2, ny=1) 复制的 3 个副本
        self.assertEqual(len(pads), 6)
        by_x = {}
        for p in pads:
            by_x.setdefault(round(p["x1"], 1), p)
        self.assertAlmostEqual(by_x[10.0]["w"], 0.5)          # $0 r0.5
        self.assertAlmostEqual(by_x[15.0]["w"], 1.0)          # $1 r1.[REDACTED-UNKNOWN].4
        self.assertEqual(by_x[15.0]["shape"], "rect")
        self.assertAlmostEqual(by_x[15.0]["h"], 0.4)
        self.assertAlmostEqual(by_x[30.0]["w"], 1.2)          # $2 o1.[REDACTED-UNKNOWN].6
        self.assertEqual(by_x[30.0]["shape"], "oval")
        self.assertAlmostEqual(pads[0]["width"], 0.5)
        # R 阵列偏移 (dx=10) 生效
        self.assertIn(20.0, by_x)

    def test_attributes_and_nets(self):
        job = odbpp.parse_job(self.job)
        self.assertEqual(job["nets"], {"GND": ["R1.1", "R1.2"], "VCC": ["C1.1"]})
        layer = [l for l in job["layers"] if l["name"] == "comp_+top"][0]
        self.assertTrue(layer["attributes_present"])
        gnd = [f for f in layer["features"] if f.get("net") == "GND"]
        self.assertTrue(gnd)
        self.assertEqual({f.get("refdes") for f in gnd if f.get("refdes")}, {"R1"})

    def test_surface_polygon_with_hole(self):
        job = odbpp.parse_job(self.job)
        layer = [l for l in job["layers"] if l["name"] == "comp_+top"][0]
        regions = [f for f in layer["features"] if f["type"] == "region"]
        self.assertEqual(len(regions), 2)                     # 原始 + R 阵列副本
        r0 = regions[0]
        self.assertEqual(len(r0["points"]), 4)                # OB + 3 点
        self.assertEqual(len(r0["holes"]), 1)
        self.assertEqual(len(r0["holes"][0]), 4)               # OS + 3 点
        self.assertEqual(r0["net"], "GND")

    def test_step_repeat_expanded(self):
        job = odbpp.parse_job(self.job)
        layer = [l for l in job["layers"] if l["name"] == "comp_+top"][0]
        self.assertTrue(any("阵列" in n for n in job["notes"]))
        self.assertEqual(len([f for f in layer["features"] if f["type"] == "draw"]), 2)

    def test_line_and_arc(self):
        job = odbpp.parse_job(self.job)
        layer = [l for l in job["layers"] if l["name"] == "comp_+top"][0]
        draws = [f for f in layer["features"] if f["type"] == "draw"]
        arcs = [f for f in layer["features"] if f["type"] == "arc"]
        self.assertEqual(len(draws), 2)                       # 原始 + R 副本
        self.assertEqual(len(arcs), 2)
        self.assertAlmostEqual(draws[0]["width"], 0.5)         # 符号尺寸生效
        self.assertAlmostEqual(arcs[0]["i"], 2.0)              # xc - xs
        self.assertEqual(arcs[0]["cw"], True)

    def test_drill_layer_converted(self):
        job = odbpp.parse_job(self.job)
        self.assertEqual(len(job["drills"]), 1)
        self.assertEqual(job["drills"][0]["hole_count"], 2)
        self.assertAlmostEqual(job["drills"][0]["holes"][0]["diameter_mm"], 0.3)

    def test_gzip_features(self):
        feat = os.path.join(self.job, "steps", "main", "layers", "comp_+top", "features")
        with open(feat, "rb") as f:
            raw = f.read()
        os.remove(feat)
        with gzip.open(feat + ".z", "wb") as f:
            f.write(raw)
        job = odbpp.parse_job(self.job)
        layer = [l for l in job["layers"] if l["name"] == "comp_+top"]
        self.assertTrue(layer)
        self.assertGreater(layer[0]["feature_count"], 0)

    def test_tgz_archive_extract_and_read(self):
        from dfm.io.fab import read_fab_data

        archive = os.path.join(self.tmp, "job.tgz")
        with tarfile.open(archive, "w:gz") as t:
            t.add(self.job, arcname="job")
        data = read_fab_data(archive)
        self.assertTrue(data)
        kinds = {d["kind"] for d in data}
        self.assertIn("odbpp", kinds)

    def test_negative_layer_polarity(self):
        matrix = os.path.join(self.job, "matrix", "matrix")
        text = open(matrix, encoding="utf-8").read().replace(
            "NAME=comp_+top;\n    POLARITY=POSITIVE;", "NAME=comp_+top;\n    POLARITY=NEGATIVE;")
        _write(matrix, text)
        job = odbpp.parse_job(self.job)
        layer = [l for l in job["layers"] if l["name"] == "comp_+top"][0]
        self.assertTrue(layer["negative_layer"])
        # 记录级 pol=0 在负片层 → clear; 记录级 pol=1 (夹具第 3 个 pad) → 负负得正
        by_pol = {(f.get("x1"), f.get("clear")) for f in layer["features"]
                  if f["type"] in ("flash", "draw")}
        self.assertIn((10.0, True), by_pol)         # 普通记录 → 挖除
        self.assertIn((30.0, False), by_pol)        # 记录本身为负 → 还原为铜


class TestOdbComponents(unittest.TestCase):
    """ODB++ components 文件解析 (字段错位会让器件数恒为 0)。"""

    def test_variant_with_record_and_index(self):
        text = "CMP 0 R1 RESC1005 12 10 0 TOP\nCMP 1 C1 CAPC1005 30 10 90 BOTTOM\n"
        out = odbpp.parse_components(text, 1.0)
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]["refdes"], "R1")
        self.assertEqual(out[0]["package"], "RESC1005")
        self.assertAlmostEqual(out[0]["x"], 12.0)
        self.assertAlmostEqual(out[0]["y"], 10.0)
        self.assertEqual(out[0]["side"], "top")
        self.assertEqual(out[1]["refdes"], "C1")
        self.assertAlmostEqual(out[1]["rotation"], 90.0)
        self.assertEqual(out[1]["side"], "bottom")

    def test_variant_side_first(self):
        out = odbpp.parse_components("TOP R1 R0402 1.5 2.5 90\n", 1.0)
        self.assertEqual(out[0]["refdes"], "R1")
        self.assertEqual(out[0]["package"], "R0402")
        self.assertAlmostEqual(out[0]["x"], 1.5)
        self.assertEqual(out[0]["side"], "top")

    def test_inch_scale_applied(self):
        out = odbpp.parse_components("CMP 0 R1 R0402 1 2 0 TOP\n", 25.4)
        self.assertAlmostEqual(out[0]["x"], 25.4)
        self.assertAlmostEqual(out[0]["y"], 50.8)

    def test_malformed_lines_skipped(self):
        out = odbpp.parse_components("# comment\n\nCMP 0 R1\nNOTARECORD 1 2 3\n", 1.0)
        self.assertEqual(out, [])


class TestIntegrityFalsePositives(unittest.TestCase):
    """完整性检查不得对正常数据误报。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _gerber(self, name, text):
        p = os.path.join(self.tmp, name)
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
        return p

    def test_non_copper_layer_not_flagged_for_missing_nets(self):
        from dfm.io.fab import integrity_check

        mask = {"kind": "gerber", "name": "board_soldermask.gts", "unit": "mm",
                "attributes_present": False, "feature_count": 2,
                "bbox": [0, 0, 50, 40], "primitives": [], "file_function": "SolderMask,Top"}
        issues = integrity_check([mask])
        self.assertEqual([i["rule"] for i in issues if i["rule"] == "网络属性缺失"], [])

    def test_copper_layer_flagged_for_missing_nets(self):
        from dfm.io.fab import integrity_check

        copper = {"kind": "gerber", "name": "board_top.gtl", "unit": "mm",
                  "attributes_present": False, "feature_count": 2,
                  "bbox": [0, 0, 50, 40], "primitives": [],
                  "file_function": "Copper,L1,Top"}
        issues = integrity_check([copper])
        self.assertIn("网络属性缺失", [i["rule"] for i in issues])

    def test_flat_layer_bbox_not_flagged(self):
        """只有横向走线的层 H=0 属正常, 不应报"坐标偏移"。"""
        from dfm.io.fab import integrity_check

        layer = {"kind": "gerber", "name": "top.gtl", "unit": "mm",
                 "attributes_present": True, "feature_count": 1,
                 "bbox": [10.0, 10.0, 40.0, 10.0], "primitives": [],
                 "file_function": "Copper,L1,Top"}
        issues = integrity_check([layer])
        self.assertEqual([i["rule"] for i in issues if i["rule"] == "坐标偏移"], [])

    def test_degenerate_bbox_flagged(self):
        from dfm.io.fab import integrity_check

        layer = {"kind": "gerber", "name": "top.gtl", "unit": "mm",
                 "attributes_present": True, "feature_count": 1,
                 "bbox": [10.0, 10.0, 10.0, 10.0], "primitives": [],
                 "file_function": "Copper,L1,Top"}
        issues = integrity_check([layer])
        self.assertIn("坐标偏移", [i["rule"] for i in issues])

    def test_offset_checked_only_between_copper_layers(self):
        from dfm.io.fab import integrity_check

        copper = {"kind": "gerber", "name": "top.gtl", "unit": "mm",
                  "attributes_present": True, "feature_count": 1,
                  "bbox": [0.0, 0.0, 50.0, 40.0], "primitives": [],
                  "file_function": "Copper,L1,Top"}
        mask = {"kind": "gerber", "name": "mask.gts", "unit": "mm",
                "attributes_present": True, "feature_count": 1,
                "bbox": [20.0, 20.0, 30.0, 30.0], "primitives": [],
                "file_function": "SolderMask,Top"}
        issues = integrity_check([copper, mask])
        self.assertEqual([i["rule"] for i in issues if i["rule"] == "层间坐标偏移"], [])

        copper2 = dict(copper, name="bot.gbl", bbox=[30.0, 30.0, 80.0, 70.0],
                       file_function="Copper,L2,Bot")
        issues2 = integrity_check([copper, copper2])
        self.assertIn("层间坐标偏移", [i["rule"] for i in issues2])


class TestParseCache(unittest.TestCase):
    """解析缓存: 改规则重跑同一份数据时不重复解析, 文件变更即失效。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.paths = write_gerber_set(self.tmp)
        from dfm.io.fab import clear_parse_cache

        clear_parse_cache()

    def tearDown(self):
        from dfm.io.fab import clear_parse_cache

        clear_parse_cache()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_cache_hit_returns_same_object(self):
        from dfm.io import fab

        first = fab.read_fab_data(self.tmp)
        second = fab.read_fab_data(self.tmp)
        self.assertIs(first, second)                  # 命中缓存 (同一对象)
        self.assertEqual(len(fab._PARSE_CACHE), 1)

    def test_cache_invalidated_on_file_change(self):
        from dfm.io import fab

        first = fab.read_fab_data(self.tmp)
        with open(self.paths["top"], "a", encoding="utf-8") as f:
            f.write("G04 changed*\n")
        os.utime(self.paths["top"], None)             # 确保 mtime 变化
        second = fab.read_fab_data(self.tmp)
        self.assertIsNot(first, second)

    def test_cache_bypass_and_clear(self):
        from dfm.io import fab

        fab.read_fab_data(self.tmp)
        self.assertEqual(len(fab._PARSE_CACHE), 1)
        fresh = fab.read_fab_data(self.tmp, use_cache=False)
        self.assertIsNotNone(fresh)
        fab.clear_parse_cache()
        self.assertEqual(len(fab._PARSE_CACHE), 0)

    def test_cache_capacity_bounded(self):
        from dfm.io import fab

        for i in range(fab._CACHE_MAX + 3):
            d = tempfile.mkdtemp()
            write_gerber_set(d)
            fab.read_fab_data(d)
            shutil.rmtree(d, ignore_errors=True)
        self.assertLessEqual(len(fab._PARSE_CACHE), fab._CACHE_MAX)


if __name__ == "__main__":
    unittest.main()

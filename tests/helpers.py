"""测试公共工具: 构造合成设计数据。"""
from __future__ import annotations

import copy
import os
import sys
from typing import Any, Dict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

SAMPLE_JSON = os.path.join(ROOT, "samples", "brd_export_sample.json")
SAMPLE_PARAMS = os.path.join(ROOT, "samples", "dfm_design_params.json")


def sample_design_raw() -> Dict[str, Any]:
    import json

    with open(SAMPLE_JSON, encoding="utf-8-sig") as f:
        return json.load(f)


def make_raw(**overrides) -> Dict[str, Any]:
    """基于样例生成可定制的 design dict (深拷贝, 便于逐用例改动)。"""
    raw = sample_design_raw()
    for key, value in overrides.items():
        raw[key] = copy.deepcopy(value)
    return raw


def design(**overrides):
    """返回 BrdDesign 实例。"""
    from dfm.io.brd_model import BrdDesign

    return BrdDesign(make_raw(**overrides), source_path="test")


def _c(mm: float, dec: int = 4) -> str:
    """mm → Gerber 隐含小数字符串 (3:4 格式), 由程序生成以避免手写误差。"""
    return f"{int(round(mm * 10 ** dec)):0{3 + dec}d}"


def _xy(x_mm: float, y_mm: float) -> str:
    return "X" + _c(x_mm) + "Y" + _c(y_mm)


def gerber_top(features: str = "", extra_apertures: str = "", macros: str = "") -> str:
    """构造一份覆盖主流特性的顶层铜箔 Gerber (mm, 3:4, 多象限, X2 属性)。

    默认图形: 一条 0.2mm 走线、两个 1.0x0.4 矩形焊盘、一个 1.2x0.6 椭圆焊盘、
    一个宏光圈(热焊盘)、一个 [REDACTED-UNKNOWN] 阵列光圈、一段 G36 区域填充、一个负片(clear)图形。
    """
    head = [
        "G04 Evan DFM test gerber*",
        "%FSLAX34Y34*%",
        "%MOMM*%",
        "%TF.FileFunction,Copper,L1,Top*%",
        macros,
        "%ADD10C,0.2000*%",
        "%ADD11R,1.0000" + "X" + "0.4000*%",
        "%ADD12O,1.2000" + "X" + "0.6000*%",
        "%ADD13THERMAL,1.5000" + "X" + "0.3000" + "X" + "0.9000*%",
        "%ADD14C,0.5000" + "X" + "0.2000*%",
        "%ADD15R,0.3000" + "X" + "0.3000" + "X" + "2" + "Y" + "2" + "I1.0000J1.0000*%",
        extra_apertures,
        "G01*",
        "G75*",
    ]
    body = [
        "D10*",
        _xy(5, 5) + "D02*",
        _xy(45, 5) + "D01*",
        "G02" + _xy(40, 10) + "I" + _c(-5) + "J" + _c(0) + "D01*",
        "G01*",
        "D11*",
        _xy(10, 20) + "D03*",
        "D12*",
        _xy(15, 20) + "D03*",
        "D13*",
        _xy(20, 20) + "D03*",
        "D14*",
        _xy(25, 20) + "D03*",
        "D15*",
        _xy(30, 20) + "D03*",
        "G36*",
        _xy(5, 30) + "D02*",
        _xy(15, 30) + "D01*",
        _xy(15, 35) + "D01*",
        _xy(5, 35) + "D01*",
        _xy(5, 30) + "D01*",
        "G37*",
        "%LPC*%",
        "D10*",
        _xy(20, 20) + "D03*",
        "%LPD*%",
        "%TO.N,GND*%",
        "%TO.P,R1,1*%",
        "D10*",
        _xy(35, 5) + "D02*",
        _xy(35, 15) + "D01*",
        "%TD*%",
        features,
        "M02*",
    ]
    return "\n".join(x for x in head + body if x) + "\n"


THERMAL_MACRO = "%AMTHERMAL*\n0 thermal relief*\n1,1,$1,0,0*\n21,0,$2,$3,0,0,0*%\n"

GERBER_PROFILE = "".join([
    "G04 profile*\n",
    "%FSLAX34Y34*%\n",
    "%MOMM*%\n",
    "%TF.FileFunction,Profile,NP*%\n",
    "%ADD10C,0.0000*%\n",
    "G01*\n",
    "D10*\n",
    _xy(0, 0) + "D02*\n",
    _xy(50, 0) + "D01*\n",
    _xy(50, 40) + "D01*\n",
    _xy(0, 40) + "D01*\n",
    _xy(0, 0) + "D01*\n",
    "M02*\n",
])

DRILL_PTH = "\n".join([
    "M48",
    ";FILE_FORMAT=3:3",
    "METRIC,LZ",
    ";TYPE=PLATED",
    "T01C0.300",
    "T02C0.150",
    "T03C0.800",
    "%",
    "T01",
    "X010000Y010000",
    "X012000Y010000",
    "T02",
    "X030000Y020000",
    "T03",
    "X020000Y030000G85X024000Y030000",     # 槽孔 4mm x 0.8mm
    "M30",
]) + "\n"

DRILL_REPEAT = "\n".join([
    "M48",
    "METRIC,LZ",
    "T05C0.250",
    "%",
    "T05",
    "X005000Y005000R04",                   # 重复孔: 4 个
    "M30",
]) + "\n"


def write_gerber_set(directory: str, top_extra: str = "", macros: str = THERMAL_MACRO,
                     drill_text: str = "") -> Dict[str, str]:
    """在目录中写出一套 Gerber + 钻孔, 返回文件路径。"""
    os.makedirs(directory, exist_ok=True)
    paths = {
        "top": os.path.join(directory, "board_top.gtl"),
        "profile": os.path.join(directory, "board_profile.gko"),
        "drill": os.path.join(directory, "board_plated.drl"),
    }
    with open(paths["top"], "w", encoding="utf-8") as f:
        f.write(gerber_top(features=top_extra, macros=macros))
    with open(paths["profile"], "w", encoding="utf-8") as f:
        f.write(GERBER_PROFILE)
    with open(paths["drill"], "w", encoding="utf-8") as f:
        f.write(drill_text or DRILL_PTH)
    return paths

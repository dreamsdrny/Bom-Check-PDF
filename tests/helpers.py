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

    with open(SAMPLE_JSON, "r", encoding="utf-8-sig") as f:
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


GERBER_TOP = """G04 Evan DFM test gerber*
%FSLAX34Y34*%
%MOMM*%
%TF.FileFunction,Copper,L1,Top*%
%ADD10C,0.2000*%
%ADD11R,1.0000X1.0000*%
G01*
G75*
D10*
X0050000Y0050000D02*
X0450000Y0050000D01*
X0450000Y0350000D01*
D11*
X0100000Y0100000D03*
X0120000Y0100000D03*
M02*
"""

GERBER_PROFILE = """G04 profile*
%FSLAX34Y34*%
%MOMM*%
%TF.FileFunction,Profile,NP*%
%ADD10C,0.0000*%
G01*
D10*
X0000000Y0000000D02*
X0500000Y0000000D01*
X0500000Y0400000D01*
X0000000Y0400000D01*
X0000000Y0000000D01*
M02*
"""

DRILL_PTH = """M48
;FILE_FORMAT=3:3
METRIC,LZ
;TYPE=PLATED
T01C0.300
T02C0.150
%
T01
X010000Y010000
X012000Y010000
T02
X030000Y020000
M30
"""


def write_gerber_set(directory: str) -> Dict[str, str]:
    """在目录中写出一套最小 Gerber + 钻孔, 返回文件路径。"""
    os.makedirs(directory, exist_ok=True)
    paths = {
        "top": os.path.join(directory, "board_top.gtl"),
        "profile": os.path.join(directory, "board_profile.gko"),
        "drill": os.path.join(directory, "board_plated.drl"),
    }
    with open(paths["top"], "w", encoding="utf-8") as f:
        f.write(GERBER_TOP)
    with open(paths["profile"], "w", encoding="utf-8") as f:
        f.write(GERBER_PROFILE)
    with open(paths["drill"], "w", encoding="utf-8") as f:
        f.write(DRILL_PTH)
    return paths

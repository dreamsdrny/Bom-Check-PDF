"""网络分类与阻抗要求 (信息项, 并产出少量高风险提示)。

按网络名模式识别电源/地/时钟/差分/高速协议 (DDR/PCIe/USB/MIPI/LVDS/ETH...)，
输出到报告 Info 页, 用于:
  * 快速判断板子是否含高速/阻抗需求 (影响板厂与叠层选择)
  * 差分对是否成对存在 (只有一端命名时提示)
  * 阻抗网络是否缺少阻抗标注 (design['impedance'] 未声明时提示)

分类模式可在规则集 ``netclass.patterns`` 中扩展。
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from dfm.core.issue import make_issue
from dfm.core.registry import register_check

DEFAULT_PATTERNS: Dict[str, dict] = {
    "POWER": {"patterns": [r"^(24V|12V|5V|3V3|3\.3V|1V8|1\.8V|1V2|1\.2V|1V0|1\.0V|VCC|VDD|VIN|VBUS|VPWR|VSUPPLY|V_BAT|VREG)",
                           r"^(VCC_|VDD_|VIN_|VBUS_|VPWR_)", r"(_PWR$|_POWER$|_VCC$|_VDD$|_VIN$)"]},
    "GND": {"patterns": [r"^(GND|AGND|DGND|PGND|SGND|GND_|_GND)", r"^GND\d+$"]},
    "CLOCK": {"patterns": [r"^(CLK|CLOCK|OSC|XTAL|CRYSTAL)", r"(_CLK$|_CLOCK$|_OSC$|_XTAL$)"],
                "impedance": False},
    "DDR": {"patterns": [r"^(DDR|DDR_|DDR\d)", r"^DQ[0-9]|^DQS|^DM[0-9]",
                         r"(_DQ|_DQS|_DM|_CK_P|_CK_N|_ADDR|_CMD|_CTRL)"],
            "diff": True, "impedance": 100.0},
    "PCIE": {"patterns": [r"^(PCIE|PCI_E|PCI-?E)", r"(_PCIE|_PCI_E|_PCI-?E)"],
             "diff": True, "impedance": 85.0},
    "USB3": {"patterns": [r"^(USB3|USB_3|SS_TX|SS_RX)", r"(_USB3|_SS_TX|_SS_RX)"],
             "diff": True, "impedance": 90.0},
    "USB2": {"patterns": [r"^(USB_DP|USB_DM|USB2|USB_D)", r"(_USB|_USB_DP|_USB_DM)"],
             "diff": True, "impedance": 90.0},
    "MIPI": {"patterns": [r"^(MIPI|DSI|CSI)", r"(_MIPI|_DSI|_CSI|_CLKP|_CLKN|_D[0-9]P|_D[0-9]N)"],
             "diff": True, "impedance": 100.0},
    "LVDS": {"patterns": [r"^(LVDS|_LVDS)", r"(_LVDS|_LVDS_P|_LVDS_N)"],
             "diff": True, "impedance": 100.0},
    "ETHERNET": {"patterns": [r"^(ETH|ETHERNET|MDI|RGMII|RMII|SGMII|GMII|MII)",
                              r"(_ETH|_MDI|_RGMII|_RMII|_SGMII|_TX|_RX)"],
                 "diff": True, "impedance": 100.0},
    "CAN": {"patterns": [r"^(CAN|CAN_|CANH|CANL)", r"(_CAN|_CANH|_CANL)"],
            "diff": True, "impedance": 120.0},
    "RF": {"patterns": [r"^(RF|ANT|ANTENNA|RF_|RFIN|RFOUT|FEED)", r"(_RF|_ANT|_ANTENNA|_FEED)"],
           "impedance": 50.0},
    "SPI": {"patterns": [r"^(SPI|MISO|MOSI|SCLK|SCK|CS_)", r"(_SPI|_MISO|_MOSI|_SCLK|_SCK|_CS|_SS)"]},
    "I2C": {"patterns": [r"^(I2C|SDA|SCL)", r"(_I2C|_SDA|_SCL)"]},
    "UART": {"patterns": [r"^(UART|TX[0-9]|RX[0-9]|TXD|RXD)", r"(_UART|_TX|_RX|_TXD|_RXD)"]},
    "JTAG": {"patterns": [r"^(JTAG|TMS|TCK|TDI|TDO|TRST)", r"(_JTAG|_TMS|_TCK|_TDI|_TDO|_TRST)"]},
    "SENSE": {"patterns": [r"^(SEN|SENSE|ADC|VDET|VDETECT|ISENSE|VSENSE)",
                           r"(_SEN|_SENSE|_ADC|_VDET|_VDETECT|_ISENSE|_VSENSE)"]},
    "GATE_DRIVE": {"patterns": [r"^(GATE|DRV|PWM|HSG|LSG|HDRV|LDRV|SW_)",
                                r"(_GATE|_DRV|_PWM|_HSG|_LSG|_SW|_PHASE)"]},
    "LED": {"patterns": [r"^(LED|LED_)", r"(_LED)"]},
}

PRIORITY = ["GATE_DRIVE", "RF", "CAN", "MIPI", "LVDS", "PCIE", "USB3", "USB2",
            "ETHERNET", "DDR", "CLOCK", "SPI", "I2C", "UART", "JTAG", "SENSE",
            "LED", "POWER", "GND"]

def _patterns(ctx) -> Dict[str, dict]:
    custom = ctx.rule_param("netclass", "patterns", None) if ctx.rules else None
    if isinstance(custom, dict) and custom:
        merged = dict(DEFAULT_PATTERNS)
        merged.update(custom)
        return merged
    return DEFAULT_PATTERNS


def classify_net(name: str, patterns: Dict[str, dict]) -> Tuple[str, dict]:
    """返回 (类别名, 类别配置); 未命中返回 ("DEFAULT", {})"""
    best: Optional[Tuple[str, dict]] = None
    best_prio = 999
    for cls, cfg in patterns.items():
        for pat in cfg.get("patterns") or []:
            try:
                if re.search(pat, name, re.IGNORECASE):
                    break
            except re.error:
                continue
        else:
            continue
        prio = PRIORITY.index(cls) if cls in PRIORITY else 999
        if prio < best_prio:
            best_prio, best = prio, (cls, cfg)
    return best if best else ("DEFAULT", {})


#: 差分对后缀配对 (长后缀优先, 避免重复配对)
DIFF_SUFFIX_PAIRS = [
    ("_PLUS", "_MINUS"), ("_POS", "_NEG"), ("_DP", "_DM"), ("DP", "DM"),
    ("_P", "_N"), ("_H", "_L"), ("P", "N"), ("H", "L"), ("+", "-"),
]


def find_diff_pairs(names: List[str]) -> List[Tuple[str, str]]:
    """按命名后缀配对差分对。

    支持 ``_P/_N``、``_H/_L``、``_POS/_NEG``、``_PLUS/_MINUS``、``DP/DM``
    以及无下划线的 ``P/N``、``H/L`` (如 CANH/CANL)。
    """
    remaining = set(names)
    pairs: List[Tuple[str, str]] = []
    for pos_suf, neg_suf in DIFF_SUFFIX_PAIRS:
        buckets: Dict[str, Dict[str, str]] = {}
        for net in list(remaining):
            for suf, tag in ((pos_suf, "p"), (neg_suf, "n")):
                if net.upper().endswith(suf.upper()):
                    base = net[: len(net) - len(suf)]
                    if base:
                        buckets.setdefault(base.upper(), {}).setdefault(tag, net)
                    break
        for _base, ends in buckets.items():
            if "p" in ends and "n" in ends:
                pairs.append((ends["p"], ends["n"]))
                remaining.discard(ends["p"])
                remaining.discard(ends["n"])
    return pairs


@register_check(
    id="netclass",
    name="网络分类 / 阻抗需求",
    group="网表",
    description="按网络名识别电源/地/时钟/差分/高速协议, 统计阻抗需求并提示缺失",
    requires=(),
    order=80,
)
def run_netclass(ctx) -> List[dict]:
    patterns = _patterns(ctx)
    names: List[str] = []
    if ctx.design is not None:
        names = list(ctx.design.net_names())
    if not names:
        names = [str(n) for n in (ctx.params.get("net_names") or [])]
    if not names:
        return []

    summary: Dict[str, int] = {}
    impedance_nets: List[str] = []
    diff_classes: List[str] = []
    for nm in names:
        cls, cfg = classify_net(nm, patterns)
        summary[cls] = summary.get(cls, 0) + 1
        if cfg.get("impedance"):
            impedance_nets.append(nm)
        if cfg.get("diff"):
            diff_classes.append(cls)
    pairs = find_diff_pairs(names)

    issues: List[dict] = []
    declared = ctx.params.get("impedance")
    if impedance_nets and declared in (None, False, "", "No"):
        issues.append(make_issue(
            code="NLC-IM", category="网表", rule="阻抗需求未声明", severity="高",
            description=(f"识别到 {len(impedance_nets)} 个高速/差分网络 "
                         f"(如 {', '.join(impedance_nets[:5])})，但设计参数未声明阻抗要求"),
            measured=len(impedance_nets), required="需声明", unit="个",
            suggestion="在叠层中明确阻抗控制要求(单端/差分阻抗值与公差)，并在 Gerber 中标注阻抗条"))

    unpaired = _unpaired_diff(names, pairs, patterns)
    if unpaired:
        issues.append(make_issue(
            code="NLC-DP", category="网表", rule="差分对不成对", severity="中",
            description=(f"{len(unpaired)} 个差分网络未找到配对 (如 {', '.join(unpaired[:6])})，"
                         f"可能命名不规范或确实缺失"),
            measured=len(unpaired), required=0, unit="个",
            suggestion="核对差分命名规则(_P/_N)，缺失网络需补全，否则无法做等长/间距检查"))

    ctx.extras["net_classes"] = {
        "total_nets": len(names),
        "summary": dict(sorted(summary.items(), key=lambda kv: -kv[1])),
        "diff_pairs": [{"p": p, "n": n} for p, n in pairs],
        "diff_pair_count": len(pairs),
        "impedance_nets": impedance_nets[:200],
        "impedance_net_count": len(impedance_nets),
        "targets": sorted({str(cfg.get("impedance"))
                           for cls, cfg in patterns.items() if cfg.get("impedance")}),
    }
    man = ctx.extras.setdefault("manufacturing_extra", {})
    man["网络数"] = len(names)
    man["差分对数"] = len(pairs)
    man["阻抗网络数"] = len(impedance_nets)
    man["阻抗"] = "Yes" if impedance_nets else ("No" if declared is not None else "")
    ctx.note(f"    网络分类: {len(names)} 网络 / {len(summary)} 类 / 差分对 {len(pairs)}")
    return issues


def _unpaired_diff(names: List[str], pairs: List[Tuple[str, str]],
                   patterns: Dict[str, dict]) -> List[str]:
    """属于差分类别但没配上对的网络 (命名不规范或确实缺失)"""
    paired = {n for pair in pairs for n in pair}
    suffixes = tuple(s.upper() for pair in DIFF_SUFFIX_PAIRS for s in pair)
    out = []
    for nm in names:
        if nm in paired:
            continue
        _cls, cfg = classify_net(nm, patterns)
        if cfg.get("diff") and nm.upper().endswith(suffixes):
            out.append(nm)
    return out

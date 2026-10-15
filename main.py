"""
BRD-AI: 主入口
Cadence Allegro PCB Constraint 自动生成系统
=============================================
用法:
    python main.py                          # 使用默认参数
    python main.py --brd path/to/file.brd   # 指定BRD文件
    python main.py --capability advanced    # 使用高级制程能力
    python main.py --demo                   # 使用内置演示数据运行
"""
import argparse #导入 argparse 模块用于解析命令行参数
import json 
import os    # 系统模块，用于文件路径操作
import sys # 用于路径和系统操作
from datetime import datetime 

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.pcb_reader import extract_brd_info  # 导入PCB信息提取模块
from src.signal_classifier import classify_and_export
from src.rule_engine import compute_class_constraints
from src.dfm_engine import validate_constraints
from src.skill_generator import generate_skill_script
from src.report_generator import generate_excel_report, generate_text_report, write_analysis_json
from src.checklist_reader import read_checklist_excel, merge_checklist_with_defaults
from src.checklist_auto_fill import auto_fill_checklist, generate_checklist_summary


DEMO_NETS = [
    "24V", "24V_BEAD", "24V_CMC", "24V_MB",
    "3V3_MB", "3V3_PSE",
    "GND", "GND_BEAD", "GND_CMC", "GND_MB", "AGND", "GND1", "GND2",
    "CH1_GATE", "CH1_SEN", "CH2_GATE", "CH2_SEN",
    "CH3_GATE", "CH3_SEN", "CH4_GATE", "CH4_SEN",
    "CH5_GATE", "CH5_SEN", "CH6_GATE", "CH6_SEN",
    "CH7_GATE", "CH7_SEN", "CH8_GATE", "CH8_SEN",
    "CLK_25MHZ", "CLK_25MHZ_N",
    "RESET_N", "POR_B",
    "SPI_MISO", "SPI_MOSI", "SPI_SCLK", "SPI_CS_N",
    "I2C_SDA", "I2C_SCL",
    "ETH_MDI0_P", "ETH_MDI0_N", "ETH_MDI1_P", "ETH_MDI1_N",
    "ETH_MDI2_P", "ETH_MDI2_N", "ETH_MDI3_P", "ETH_MDI3_N",
    "ETH_RGMII_TX0", "ETH_RGMII_TX1", "ETH_RGMII_TX2", "ETH_RGMII_TX3",
    "ETH_RGMII_RX0", "ETH_RGMII_RX1", "ETH_RGMII_RX2", "ETH_RGMII_RX3",
    "ETH_RGMII_TXC", "ETH_RGMII_RXC",
    "ETH_RGMII_TX_CTL", "ETH_RGMII_RX_CTL",
    "LED_STATUS", "LED_FAULT",
    "JTAG_TMS", "JTAG_TCK", "JTAG_TDI", "JTAG_TDO", "JTAG_TRST",
    "UART_TX", "UART_RX",
    "SW1", "SW2", "SW3", "SW4",
    "TP1", "TP2", "TP3", "TP4", "TP5", "TP6",
    "VBUS", "VBUS_DET",
    "CANH", "CANL",
]


def run_pipeline(
    brd_path: str = None,
    use_demo: bool = False,
    capability_level: str = "standard",
    output_dir: str = None,
    checklist_path: str = None,
    autofill_path: str = None,
    checker_name: str = "BRD-AI",
    board_type: str = "rigid",
    copper_um: float = 35.0,
    copper_oz: float = None,
    factory: str = "hongban",
) -> dict:
    base_dir = os.path.dirname(os.path.abspath(__file__))
    if output_dir is None:
        output_dir = os.path.join(base_dir, "output")

    print("=" * 60)
    print("  BRD-AI: Cadence Allegro Constraint Generator")
    print("=" * 60)
    print(f"  Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)
    print()

    # ---- Step 1: 读取PCB信息 ----
    print("[1/6] 读取PCB数据...")
    if use_demo or not brd_path:
        if use_demo:
            print("  -> 使用内置演示数据 (Demo Board)")
        board_info = {
            "board_name": "DEMO_BOARD",
            "file_path": "(demo)",
            "file_size_mb": 0,
            "nets": DEMO_NETS,
            "net_count": len(DEMO_NETS),
            "layers": [
                {"name": "TOP", "type": "signal"},
                {"name": "L2_GND", "type": "plane"},
                {"name": "L3_SIG", "type": "signal"},
                {"name": "L4_GND", "type": "plane"},
                {"name": "L5_PWR", "type": "plane"},
                {"name": "L6_SIG", "type": "signal"},
                {"name": "L7_GND", "type": "plane"},
                {"name": "BOTTOM", "type": "signal"},
            ],
            "layer_count": 8,
            "power_nets": ["24V", "24V_BEAD", "24V_CMC", "24V_MB", "3V3_MB", "3V3_PSE", "GND", "GND_BEAD", "GND_CMC", "GND_MB", "AGND", "GND1", "GND2"],
            "diff_pairs": [],
        }
    else:
        board_info = extract_brd_info(brd_path)
        if "error" in board_info:
            print(f"  [ERROR] {board_info['error']}")
            return board_info
    print(f"  -> Nets: {board_info['net_count']}, Layers: {board_info['layer_count']}")
    print()

    # ---- Step 2: 信号分类 ----
    print("[2/6] 信号自动分类...")
    classification = classify_and_export(board_info.get("nets", []))
    print(f"  -> Net Classes: {classification['total_classes']}")
    print(f"  -> Diff Pairs: {classification['total_diff_pairs']}")
    for cls_name, count in sorted(classification["summary"].items()):
        print(f"     {cls_name:15s} {count:4d} nets")
    print()
    if checklist_path and os.path.exists(checklist_path):
        print(f"[2.5/6] 读取Checklist Excel: {os.path.basename(checklist_path)}...")
        checklist_data = read_checklist_excel(checklist_path)
        print(f"  -> Items: {checklist_data['total_items']}, Rules extracted: {checklist_data['total_rules_extracted']}")
        print()

    # ---- Step 3: 规则计算 ----
    print("[3/6] 计算约束规则...")
    constraints = compute_class_constraints(classification)
    print(f"  -> Physical: {len(constraints['physical_constraints'])}")
    print(f"  -> Spacing: {len(constraints['spacing_constraints'])}")
    print(f"  -> Electrical: {len(constraints['electrical_constraints'])}")
    print(f"  -> Via: {len(constraints['via_constraints'])}")
    print()

    # ---- Step 4: DFM校验 ----
    from src.dfm_engine import normalize_level, level_label, list_levels, normalize_factory, factory_label
    factory = normalize_factory(factory)
    if capability_level not in list_levels(board_type, factory=factory):
        capability_level = normalize_level(capability_level)
    if copper_oz is None:
        copper_oz = max(1.0, round(copper_um / 35.0, 1))
    print(f"[4/6] DFM制程能力校验 ({factory_label(factory)} {board_type}-{level_label(board_type, capability_level, factory=factory)}, 铜厚 {copper_um}um)...")
    dfm_result = validate_constraints(
        constraints, capability_level,
        board_type=board_type, copper_um=copper_um, copper_oz=copper_oz,
        factory=factory,
    )
    status = "PASS" if dfm_result["passed"] else "FAIL"
    print(f"  -> Result: {status}")
    cap_sum = dfm_result.get("capability_summary", {})
    if cap_sum:
        print(f"  -> 能力: L/S={cap_sum.get('min_line_spacing')}, "
              f"最小钻孔={cap_sum.get('min_mech_drill')}mm, "
              f"AR={cap_sum.get('pth_aspect_ratio')}:1")
    print(f"  -> Errors: {dfm_result['error_count']}, Warnings: {dfm_result['warning_count']}")
    if dfm_result["errors"]:
        for err in dfm_result["errors"]:
            print(f"     [ERROR] {err}")
    if dfm_result["warnings"]:
        for warn in dfm_result["warnings"]:
            print(f"     [WARN]  {warn}")
    print()

    # ---- Step 5: 生成SKILL脚本 ----
    print("[5/6] 生成SKILL脚本...")
    skill_script = generate_skill_script(
        constraints=constraints,
        signal_classification=classification,
        board_name=board_info.get("board_name", "Untitled"),
        capability_level=capability_level,
        output_path=os.path.join(output_dir, "auto_constraint.il"),
    )
    print(f"  -> Output: {os.path.join(output_dir, 'auto_constraint.il')}")
    print(f"  -> Size: {len(skill_script)} chars")
    print()

    # ---- Step 6: 生成报告 ----
    print("[6/6] 生成报告...")
    excel_path = generate_excel_report(
        constraints=constraints,
        signal_classification=classification,
        dfm_result=dfm_result,
        board_info=board_info,
        output_path=os.path.join(output_dir, "constraint_report.xlsx"),
    )
    print(f"  -> Excel: {excel_path}")

    text_path = generate_text_report(
        constraints=constraints,
        signal_classification=classification,
        dfm_result=dfm_result,
        board_info=board_info,
        output_path=os.path.join(output_dir, "constraint_report.txt"),
    )
    print(f"  -> Text:  {text_path}")

    json_path = os.path.join(output_dir, "constraint_data.json")
    write_analysis_json(json_path, board_info, classification, constraints, dfm_result)
    print(f"  -> JSON:  {json_path}")

    print()
    print("=" * 60)
    print("  Execution Complete!")
    print("=" * 60)
    print()
    print("  Next Steps:")
    print(f"  1. Review: {os.path.join(output_dir, 'constraint_report.xlsx')}")
    print(f"  2. Load SKILL in Allegro: skill load \"{os.path.join(output_dir, 'auto_constraint.il')}\"")
    print()

    # ---- Step 7: 自动填写Checklist (可选) ----
    if checklist_path and os.path.exists(checklist_path):
        print("[7/7] 自动填写Checklist...")
        filled_path = os.path.join(output_dir, os.path.basename(checklist_path).replace(".xlsx", "_filled.xlsx"))
        target_path = autofill_path if autofill_path else filled_path
        output_checklist, stats = auto_fill_checklist(
            checklist_path=checklist_path,
            brd_info=board_info,
            classification=classification,
            constraints=constraints,
            dfm_result=dfm_result,
            output_path=target_path,
            checker_name=checker_name,
        )
        print(f"  -> Output: {output_checklist}")
        print(f"  -> Auto PASS: {stats['auto_pass']}, Manual: {stats['manual']}, N/A: {stats['na']}")
        print(f"  -> Auto Rate: {stats['auto_pass']/max(stats['total'],1)*100:.1f}%")
        print()

    return {
        "board_info": board_info,
        "classification": classification,
        "constraints": constraints,
        "dfm": dfm_result,
        "output_dir": output_dir,
    }


def main():
    parser = argparse.ArgumentParser(
        description="BRD-AI: Cadence Allegro PCB Constraint Generator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py --demo
  python main.py --brd "../DEMO-2L_FAB_BRD_Rev_A_260721_3D.brd"
  python main.py --brd my_board.brd --capability advanced
  python main.py --brd my_board.brd --output ./my_output
        """,
    )
    parser.add_argument(
        "--brd", type=str, default=None,
        help="Path to Cadence Allegro .brd file"
    )
    parser.add_argument(
        "--demo", action="store_true",
        help="Use built-in demo data (Demo Board)"
    )
    parser.add_argument(
        "--capability", type=str, default="standard",
        choices=["standard", "automotive", "extreme", "advanced"],
        help="制程能力等级: standard(常规)/automotive(车规)/extreme(极限); advanced为旧版兼容"
    )
    parser.add_argument(
        "--board-type", type=str, default="rigid", dest="board_type",
        choices=["rigid", "flex"],
        help="板类型: rigid(硬板)/flex(软板及软硬结合板)"
    )
    parser.add_argument(
        "--copper", type=float, default=35.0,
        help="成品铜厚 (um), 用于线宽线距能力分档判断 (默认 35)"
    )
    parser.add_argument(
        "--copper-oz", type=float, default=None, dest="copper_oz",
        help="成品铜厚 (oz), 用于阻焊桥能力分档判断 (默认按 --copper 推算)"
    )
    parser.add_argument(
        "--output", type=str, default=None,
        help="Output directory for generated files"
    )
    parser.add_argument(
        "--nets", type=str, default=None,
        help="Path to a JSON file containing a list of net names (alternative to --brd)"
    )
    parser.add_argument(
        "--checklist", type=str, default=None,
        help="Path to Layout Checklist Excel file (.xlsx)"
    )
    parser.add_argument(
        "--gui", action="store_true",
        help="Launch graphical user interface"
    )
    parser.add_argument(
        "--autofill", type=str, default=None,
        help="Auto-fill checklist Excel: specify output path for filled checklist"
    )
    parser.add_argument(
        "--checker", type=str, default="BRD-AI",
        help="Checker name for auto-fill (default: BRD-AI)"
    )
    parser.add_argument(
        "--dfm", type=str, default=None, metavar="DESIGN_JSON",
        help="DFM功能分析模式: 传入设计参数JSON文件，直接输出制程能力比对报告 (不生成约束)"
    )
    parser.add_argument(
        "--factory", type=str, default="hongban",
        choices=["hongban", "sihui", "jlcpcb"],
        help="板厂制程能力库: hongban(板厂A)/sihui(板厂B)/jlcpcb(嘉立创) (默认: hongban)"
    )
    parser.add_argument(
        "--fab", type=str, default=None, metavar="PATH",
        help="制造数据 DFM: 传入 Gerber/NC Drill 文件或 ODB++ 目录, 执行解析+完整性检查"
    )
    parser.add_argument(
        "--cad-nets", type=str, default=None, dest="cad_nets", metavar="CAD_NETS_JSON",
        help="CAD 端网络表 JSON ({net: [refdes.pin,...]}), 配合 --fab 做网络表比对"
    )
    parser.add_argument(
        "--panelize", type=str, default=None, metavar="PANEL_JSON",
        help="自动拼板: 传入参数 JSON (或用 'template' 生成模板)"
    )
    parser.add_argument(
        "--view", action="store_true",
        help="打开图形化交叉定位查看器 (配合 --fab)"
    )
    parser.add_argument(
        "--skill-export", type=str, default=None, nargs="?", const="auto",
        dest="skill_export", metavar="PATH",
        help="写出 Allegro SKILL 数据导出脚本 brd_dfm_export.il (在 Allegro 内运行导出带XY的几何/网表)"
    )
    parser.add_argument(
        "--brd-dfm", type=str, default=None, dest="brd_dfm", metavar="BRD_OR_JSON",
        help="BRD DFM: 传入 .brd(自动用 Cadence extracta 无界面提取) 或 SKILL 导出的 .json, "
             "做几何DFM(定位XY)+开短路+网表测试, 输出 Valor 式报告"
    )

    parser.add_argument(
        "--dfm-hub", action="store_true", dest="dfm_hub",
        help="DFM 检查中心: 统一编排 能力/几何/开短路/制造数据/网络比对/拼版 多模块检查, 输出单一 Valor 式报告"
    )
    parser.add_argument(
        "--hub-modules", type=str, default=None, dest="hub_modules", metavar="LIST",
        help="逗号分隔的检查模块: capability,geometry,netlist,fab,fabnet,panel (默认全部)"
    )
    parser.add_argument(
        "--design-params", type=str, default=None, dest="design_params", metavar="JSON",
        help="DFM 检查中心: 设计参数 JSON (capability 模块; 亦可用 --dfm 指定)"
    )
    parser.add_argument(
        "--panel-params", type=str, default=None, dest="panel_params", metavar="JSON",
        help="DFM 检查中心: 拼版参数 JSON (panel 模块)"
    )
    parser.add_argument(
        "--board-thickness", type=float, default=None, dest="board_thickness", metavar="MM",
        help="板厚(mm): Gerber/ODB++ 无板厚信息, 指定后才能做 PTH 纵横比检查"
    )
    parser.add_argument(
        "--gerber-dfm", type=str, default=None, dest="gerber_dfm", metavar="PATH",
        help="Gerber DFM: 对 Gerber/ODB++/NC Drill 目录或文件做几何 DFM(定位XY)+开短路+网表, "
             "等价于 --dfm-hub 以制造数据为源"
    )

    args = parser.parse_args()

    if args.dfm_hub or args.gerber_dfm:
        from src.dfm_hub import CHECK_MODULES, run_hub
        from src.dfm_report import generate_dfm_excel, generate_dfm_text

        src = args.gerber_dfm or args.brd_dfm or args.brd or None
        stype = "none"
        if src:
            if not os.path.exists(src):
                print(f"[ERROR] 数据源不存在: {src}")
                sys.exit(1)
            if args.gerber_dfm:
                stype = "gerber"
            elif src.lower().endswith(".brd"):
                stype = "brd"
            else:
                stype = "json"
        mods = ([m.strip() for m in args.hub_modules.split(",") if m.strip()]
                if args.hub_modules else [m[0] for m in CHECK_MODULES])
        cfg = {
            "source_type": stype,
            "source_path": src or "",
            "fab_path": args.fab,
            "cad_nets": args.cad_nets,
            "design_params": args.design_params or (args.dfm if args.dfm and args.dfm != "template" else None),
            "panel_params": args.panel_params,
            "factory": args.factory,
            "board_type": args.board_type,
            "capability_level": args.capability,
            "copper_um": args.copper,
            "copper_oz": args.copper_oz,
            "board_thickness": args.board_thickness,
            "modules": mods,
        }
        print("=" * 70)
        print("  DFM 检查中心 (多模块统一编排)")
        print("=" * 70)
        print(f"  数据源: {stype} {src or '-'}   制造数据: {args.fab or '-'}")
        print(f"  模块:   {', '.join(mods)}")
        print(f"  规则集: {args.factory} / {args.board_type} / {args.capability} / 铜厚 {args.copper}um")
        print("-" * 70)
        result = run_hub(cfg, progress=lambda m: print(f"  {m}"))
        s = result["issue_summary"]
        risk = result["risk"]
        print("-" * 70)
        print(f"  已执行模块: {', '.join(result.get('modules_run', [])) or '(无)'}")
        print(f"  问题总数: {s.get('总计', 0)}  (危险 {s.get('危险',0)} / 高 {s.get('高',0)} / "
              f"中 {s.get('中',0)} / 低 {s.get('低',0)})")
        print(f"  可制造性评分: {risk.get('score')}/100   风险等级: {risk.get('risk_level')}   "
              f"结论: {risk.get('verdict')}")
        for it in result["issues"]:
            loc = f" @({it['x']:.2f},{it['y']:.2f})" if it.get("x") is not None else ""
            print(f"  [{it['severity']}] {it['code']} {it.get('module','')} {it['rule']}: "
                  f"{it['description']}{loc}")
        out_dir = args.output or os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
        os.makedirs(out_dir, exist_ok=True)
        meta = result.get("board_meta") or {}
        xlsx_out = generate_dfm_excel(result, os.path.join(out_dir, "dfm_hub_report.xlsx"), meta)
        txt_out = generate_dfm_text(result, os.path.join(out_dir, "dfm_hub_report.txt"), meta)
        json_out = os.path.join(out_dir, "dfm_hub_analysis.json")
        slim = {k: v for k, v in result.items()
                if k not in ("fab_data", "instances", "capability_items")}
        with open(json_out, "w", encoding="utf-8") as f:
            json.dump(slim, f, indent=2, ensure_ascii=False, default=str)
        print("-" * 70)
        print(f"  报告(Excel): {xlsx_out}")
        print(f"  报告(Text):  {txt_out}")
        print(f"  报告(JSON):  {json_out}")
        if args.view and result.get("fab_data"):
            import tkinter as tk
            from src.fab_viewer import FabViewer
            root = tk.Tk()
            root.withdraw()
            FabViewer(root, result["fab_data"], result["issues"], title="DFM 检查中心 - 交叉定位")
            root.mainloop()
        return

    if args.skill_export is not None:
        from src.skill_export import write_export_skill, EXPORT_SKILL_FILENAME
        if args.skill_export == "auto":
            out_dir = args.output or os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
            os.makedirs(out_dir, exist_ok=True)
            path = write_export_skill(os.path.join(out_dir, EXPORT_SKILL_FILENAME))
        else:
            path = write_export_skill(args.skill_export)
        print("=" * 70)
        print("  Allegro SKILL 数据导出脚本已写出")
        print("=" * 70)
        print(f"  文件: {path}")
        print("  用法: 在 Allegro PCB Editor 打开 .brd, Command 窗口执行:")
        print(f'        skill load "{os.path.basename(path)}"')
        print("        brdExportJSON            ; 导出到 .brd 同目录")
        print('        或 (brdExportJSON "D:/out/brd_export.json")')
        print("  然后把导出的 JSON 传给:  python main.py --brd-dfm <export.json>")
        return

    if args.brd_dfm:
        from src.brd_dfm import analyze_brd_dfm, analyze_brd_file
        from src.dfm_report import generate_dfm_excel, generate_dfm_text
        src_path = args.brd_dfm
        if not os.path.exists(src_path):
            print(f"[ERROR] 文件不存在: {src_path}")
            print("        支持 .brd (需本机 Allegro extracta 无界面提取) 或 SKILL 导出的 .json。")
            sys.exit(1)
        out_dir = args.output or os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
        os.makedirs(out_dir, exist_ok=True)
        if src_path.lower().endswith(".brd"):
            print("[1/2] 用 Cadence extracta 无界面提取 .brd (网表/几何/DRC/叠层), 约需 1-2 分钟...")
            try:
                result = analyze_brd_file(
                    src_path, factory=args.factory, board_type=args.board_type,
                    capability_level=args.capability, copper_um=args.copper,
                    copper_oz=args.copper_oz, fab_path=args.fab,
                    json_out=os.path.join(out_dir, "brd_export.json"))
            except RuntimeError as exc:
                print(f"[ERROR] {exc}")
                print("        可改用 SKILL 导出: python main.py --skill-export  然后在 Allegro 内运行。")
                sys.exit(1)
            print("[2/2] DFM 分析 (几何定位XY + 开短路 + 网表)...")
        else:
            result = analyze_brd_dfm(
                src_path, factory=args.factory, board_type=args.board_type,
                capability_level=args.capability, copper_um=args.copper,
                copper_oz=args.copper_oz, fab_path=args.fab)
        gs = result["geometry_stats"]
        nl = result["netlist_summary"]
        summary = result["issue_summary"]
        risk = result.get("risk", {})
        print("=" * 70)
        print(f"  BRD DFM 分析: {result['board_meta'].get('board_name','')}")
        print("=" * 70)
        print(f"  {result['factory_label']} {result['board_type_label']} - "
              f"{result['capability_label']} (铜厚 {result['copper_um']}um)")
        print(f"  网络 {gs['net_count']} / 节点 {gs['node_count']} / 器件 {gs['component_count']} / "
              f"过孔 {gs['via_count']} / 焊盘 {gs['pad_count']} / 走线 {gs['cline_count']}")
        print(f"  开短路: 开路网络 {nl['open_net_count']} / 短路网络对 {nl['short_net_count']} / "
              f"无铜节点 {nl['nodes_without_copper']} / 物理连通域 {nl['physical_component_count']}")
        if nl.get("fab_compare"):
            fc = nl["fab_compare"]
            print(f"  CAD vs FAB: CAD {fc['cad_net_count']} / FAB {fc['fab_net_count']} 网络, "
                  f"Open {fc['open_count']} / Short {fc['short_count']} "
                  f"({'PASS' if fc['passed'] else 'FAIL'})")
        print("-" * 70)
        print(f"  问题总数 {summary.get('总计',0)}: 危险 {summary.get('危险',0)} / "
              f"高 {summary.get('高',0)} / 中 {summary.get('中',0)} / 低 {summary.get('低',0)}")
        print(f"  可制造性评分 {risk.get('score','-')}/100  风险 {risk.get('risk_level','-')}  "
              f"{risk.get('verdict','')}")
        print("-" * 70)
        for it in result["issues"]:
            xy = f" @({it['x']:.2f},{it['y']:.2f})" if it.get("x") is not None else ""
            print(f"  [{it['severity']}] {it['code']} {it['rule']} x{it.get('count',1)}{xy}")
            print(f"        {it['description']}")
        board_meta = result.get("board_meta", {})
        xlsx_out = generate_dfm_excel(result, os.path.join(out_dir, "brd_dfm_report.xlsx"), board_meta)
        txt_out = generate_dfm_text(result, os.path.join(out_dir, "brd_dfm_report.txt"), board_meta)
        json_out = os.path.join(out_dir, "brd_dfm_analysis.json")
        slim = {k: v for k, v in result.items() if k not in ("fab_data", "instances")}
        slim["instance_count"] = len(result.get("instances", []))
        with open(json_out, "w", encoding="utf-8") as f:
            json.dump(slim, f, indent=2, ensure_ascii=False, default=str)
        print("-" * 70)
        print(f"  DFM报告(Excel): {xlsx_out}")
        print(f"  DFM报告(Text):  {txt_out}")
        print(f"  分析结果(JSON): {json_out}")
        if args.view:
            import tkinter as tk
            from src.fab_viewer import FabViewer
            root = tk.Tk()
            root.withdraw()
            FabViewer(root, result["fab_data"], result.get("instances", []),
                      title="BRD DFM 交叉定位")
            root.mainloop()
        return


    if args.panelize:
        from src.panelizer import optimize_panel, panel_svg
        if args.panelize == "template":
            print(json.dumps({
                "board_w": 31, "board_h": 31.2, "board_qty": 1000,
                "panel_w": 544.068, "panel_h": 620.268,
                "rail_mm": 4, "gap_mm": 1.6, "allow_rotate": True,
            }, indent=2, ensure_ascii=False))
            return
        if not os.path.exists(args.panelize):
            print(f"[ERROR] 拼板参数文件不存在: {args.panelize}")
            sys.exit(1)
        with open(args.panelize, "r", encoding="utf-8-sig") as f:
            panel_params = json.load(f)
        pnl = optimize_panel(panel_params)
        print("=" * 70)
        print("  自动拼板与成本优化")
        print("=" * 70)
        print(f"  结论: {pnl.get('recommendation', 'N/A')}")
        print(f"  每拼板: {pnl.get('pcs_per_panel')} pcs   所需拼板: {pnl.get('panels_needed')}   余量: {pnl.get('leftover')}")
        for it in pnl.get("issues", []):
            print(f"  [{it['severity']}] {it['description']}")
        out_dir = args.output or os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
        os.makedirs(out_dir, exist_ok=True)
        json_out = os.path.join(out_dir, "panel_result.json")
        with open(json_out, "w", encoding="utf-8") as f:
            json.dump(pnl, f, indent=2, ensure_ascii=False, default=str)
        svg_out = os.path.join(out_dir, "panel_preview.svg")
        with open(svg_out, "w", encoding="utf-8") as f:
            f.write(panel_svg(pnl))
        print(f"  详细结果: {json_out}")
        print(f"  拼板预览: {svg_out}")
        return

    if args.fab:
        from src.fab_data_reader import (analyze_fab, compare_netlists,
                                         netlist_compare_issues, normalize_cad_nets)
        from src.fab_viewer import render_fab_svg
        result = analyze_fab(args.fab)
        stats = result["stats"]
        issues = list(result["issues"])
        comparison = None
        if args.cad_nets:
            if not os.path.exists(args.cad_nets):
                print(f"[ERROR] CAD 网络表不存在: {args.cad_nets}")
                sys.exit(1)
            with open(args.cad_nets, "r", encoding="utf-8-sig") as f:
                cad_nets = normalize_cad_nets(json.load(f))
            comparison = compare_netlists(cad_nets, stats.get("nets", {}))
            issues += netlist_compare_issues(comparison)
        print("=" * 70)
        print(f"  制造数据 DFM: {args.fab}")
        print("=" * 70)
        print(f"  Gerber 层: {len(stats['gerber_layers'])}   "
              f"钻孔文件: {len(stats['drill_files'])}   "
              f"ODB++: {len(stats['odb_jobs'])}")
        mf = stats['min_line_width_mm']
        mh = stats['min_hole_mm']
        print(f"  图形数: {stats['feature_count']}   孔数: {stats['hole_count']}   "
              f"最小线宽: {mf if mf is not None else 'N/A'}mm   "
              f"最小孔径: {mh if mh is not None else 'N/A'}mm")
        if args.cad_nets:
            print(f"  网络比对: CAD {comparison['cad_net_count']} 网络 / FAB {comparison['fab_net_count']} 网络   "
                  f"Open {comparison['open_count']} / Short {comparison['short_count']}   "
                  f"{'PASS' if comparison['passed'] else 'FAIL'}")
        print("-" * 70)
        if issues:
            for it in issues:
                loc = f" @({it['x']:.2f},{it['y']:.2f})" if it.get("x") is not None else ""
                print(f"  [{it['severity']}] {it['code']} {it['rule']}: {it['description']}{loc}")
        else:
            print("  未发现制造数据问题")
        out_dir = args.output or os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
        os.makedirs(out_dir, exist_ok=True)
        json_out = os.path.join(out_dir, "fab_analysis.json")
        with open(json_out, "w", encoding="utf-8") as f:
            json.dump({"stats": {k: v for k, v in stats.items() if k != "nets"},
                       "issues": issues}, f, indent=2, ensure_ascii=False, default=str)
        svg_out = os.path.join(out_dir, "fab_view.svg")
        render_fab_svg(result["data"], issues, svg_out)
        print(f"  详细报告: {json_out}")
        print(f"  版图预览: {svg_out}")
        from src.fab_data_reader import build_fab_report
        from src.dfm_report import generate_dfm_excel, generate_dfm_text
        fab_report = build_fab_report(result, comparison, args.fab)
        board_meta = {"board_name": (stats.get("odb_jobs") or [result["data"][0]["name"] if result["data"] else "Fabrication"])[0]}
        xlsx_out = generate_dfm_excel(fab_report, os.path.join(out_dir, "fab_dfm_report.xlsx"), board_meta)
        txt_out = generate_dfm_text(fab_report, os.path.join(out_dir, "fab_dfm_report.txt"), board_meta)
        print(f"  DFM报告(Excel): {xlsx_out}")
        print(f"  DFM报告(Text):  {txt_out}")
        if args.view:
            import tkinter as tk
            from src.fab_viewer import FabViewer
            root = tk.Tk()
            root.withdraw()
            FabViewer(root, result["data"], issues)
            root.mainloop()
        return

    if args.dfm:
        from src.dfm_engine import analyze_design, load_capability_db, factory_label, normalize_factory
        dfm_factory = normalize_factory(args.factory)
        if args.dfm == "template":
            template = {
                "board_size": [100, 80],
                "min_line_width": 0.10, "min_spacing": 0.10,
                "copper_um": 35, "copper_oz": 1.0,
                "min_mech_drill": 0.2, "max_mech_drill": 3.2,
                "min_laser_via": 0.1, "min_annular_ring": 0.075,
                "board_thickness": 1.6,
                "pth_to_pth_spacing": 0.3,
                "copper_to_edge_inner": 0.25, "copper_to_edge_outer": 0.2,
                "npth_to_edge": 0.5,
                "solder_mask_bridge": 0.1, "solder_mask_opening": 0.1,
                "silkscreen_width": 0.15, "silkscreen_clearance": 0.15,
                "plug_hole_diameter": 0.3, "plug_board_thickness": 1.6,
                "test_pad_2wire": 0.4,
                "coverlay_spacing": 0.5, "coverlay_to_pad": 0.3,
                "coverlay_to_trace": 0.15, "coverlay_window": [0.6, 0.6],
            }
            print(json.dumps(template, indent=2, ensure_ascii=False))
            return
        if not os.path.exists(args.dfm):
            print(f"[ERROR] 设计参数文件不存在: {args.dfm} (可用 --dfm template 生成模板)")
            sys.exit(1)
        with open(args.dfm, "r", encoding="utf-8-sig") as f:
            design = json.load(f)
        result = analyze_design(design, board_type=args.board_type,
                                capability_level=args.capability,
                                factory=dfm_factory)
        print("=" * 70)
        print(f"  DFM制程能力分析报告: {factory_label(dfm_factory)} - {result['board_type_label']}"
              f" - {result['capability_label']} (铜厚 {result['copper_um']}um)")
        print("=" * 70)
        status_icon = {"pass": "[PASS]", "warn_limit": "[WARN]", "fail": "[FAIL]", "info": "[INFO]"}
        for it in result["items"]:
            print(f"  {status_icon[it['status']]} {it['message']}")
            if it.get("note"):
                print(f"          备注: {it['note']}")
        print("-" * 70)
        print(f"  结论: {'DFM PASS' if result['passed'] else 'DFM FAIL'}"
              f"  (错误 {result['error_count']}, 警告 {result['warning_count']})")
        out_dir = args.output or os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
        os.makedirs(out_dir, exist_ok=True)
        json_out = os.path.join(out_dir, "dfm_analysis.json")
        with open(json_out, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False, default=str)
        print(f"  详细报告(JSON): {json_out}")

        from src.dfm_report import generate_dfm_excel, generate_dfm_text
        board_meta = design.get("board_meta", {}) if isinstance(design, dict) else {}
        if isinstance(design.get("board_name"), str):
            board_meta.setdefault("board_name", design.get("board_name"))
        xlsx_out = generate_dfm_excel(result, os.path.join(out_dir, "dfm_report.xlsx"), board_meta)
        txt_out = generate_dfm_text(result, os.path.join(out_dir, "dfm_report.txt"), board_meta)
        print(f"  DFM报告(Excel): {xlsx_out}")
        print(f"  DFM报告(Text):  {txt_out}")
        risk = result.get("risk", {})
        if risk:
            print(f"  可制造性评分: {risk.get('score')}/100  风险等级: {risk.get('risk_level')}")
        return

    if args.gui:
        from gui import main as gui_main
        gui_main()
        return

    if args.brd and args.nets:
        print("[ERROR] --brd and --nets are mutually exclusive")
        sys.exit(1)

    if not args.brd and not args.demo and not args.nets:
        print("[INFO] No input specified. Using --demo mode.")
        args.demo = True

    if args.nets:
        with open(args.nets, "r", encoding="utf-8") as f:
            net_list = json.load(f)
        board_info = {
            "board_name": "Imported Nets",
            "file_path": args.nets,
            "file_size_mb": 0,
            "nets": net_list,
            "net_count": len(net_list),
            "layers": [],
            "layer_count": 0,
            "power_nets": [],
            "diff_pairs": [],
        }
        classification = classify_and_export(net_list)
        constraints = compute_class_constraints(classification)
        copper_oz = args.copper_oz if args.copper_oz else max(1.0, round(args.copper / 35.0, 1))
        dfm_result = validate_constraints(
            constraints, args.capability,
            board_type=args.board_type, copper_um=args.copper, copper_oz=copper_oz,
            factory=args.factory,
        )

        output_dir = args.output or os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
        generate_skill_script(
            constraints=constraints,
            signal_classification=classification,
            board_name=board_info["board_name"],
            capability_level=args.capability,
            output_path=os.path.join(output_dir, "auto_constraint.il"),
        )
        generate_excel_report(
            constraints=constraints,
            signal_classification=classification,
            dfm_result=dfm_result,
            board_info=board_info,
            output_path=os.path.join(output_dir, "constraint_report.xlsx"),
        )
        generate_text_report(
            constraints=constraints,
            signal_classification=classification,
            dfm_result=dfm_result,
            board_info=board_info,
            output_path=os.path.join(output_dir, "constraint_report.txt"),
        )
        print("[DONE]")
        return

    run_pipeline(
        brd_path=args.brd,
        use_demo=args.demo,
        capability_level=args.capability,
        output_dir=args.output,
        checklist_path=args.checklist,
        autofill_path=args.autofill,
        checker_name=args.checker,
        board_type=args.board_type,
        copper_um=args.copper,
        copper_oz=args.copper_oz,
        factory=args.factory,
    )


if __name__ == "__main__":
    main()
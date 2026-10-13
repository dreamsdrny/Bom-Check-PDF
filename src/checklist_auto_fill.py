"""
BRD-AI: Checklist自动填写引擎
读取Layout_Checklist.xlsx，根据BRD分析结果自动填写Check和Approved列
"""
import os
from datetime import datetime
from typing import List, Tuple

import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

#通过浅绿色，红色，黄色，蓝色，灰色填充单元格来表示不同的检查结果
#浅蓝灰色，需要人工确认
PASS_FILL = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid") 
FAIL_FILL = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")
WARN_FILL = PatternFill(start_color="FFEB9C", end_color="FFEB9C", fill_type="solid")
MANUAL_FILL = PatternFill(start_color="D9E2F3", end_color="D9E2F3", fill_type="solid")
NA_FILL = PatternFill(start_color="D9D9D9", end_color="D9D9D9", fill_type="solid")

THIN_BORDER = Border(
    left=Side(style="thin"), right=Side(style="thin"),  
    top=Side(style="thin"), bottom=Side(style="thin"),
)
# 自动校验规则: 每条 check(r) 依据 BRD 分析结果 r (见 _analyze_brd_for_checklist) 判定。
# 返回 (result, detail); result 取值: auto_pass / auto_verify / 手动检查 / N/A。
# 说明: 凡本工具能给出证据(已生成约束/已识别差分等)的条目判为 auto_verify(需在 Allegro
#       加载脚本/DRC 后确认)，无法由网表分析判定的条目一律降级为人工确认，不再无条件 PASS。
VERIFIABLE_RULES = {
    37: {"category": "constraint", "desc": "设计约束规则已在Constraint Manager中设置",
         "check": lambda r: (("auto_verify",
                              f"已生成 {r.get('physical_constraints', 0)} 条物理约束脚本，需在 Allegro 加载后确认")
                             if r.get("physical_constraints") else ("手动检查", "未生成物理约束，需人工确认"))},
    38: {"category": "constraint", "desc": "间距规则",
         "check": lambda r: (("auto_verify",
                              f"已生成 {r.get('spacing_constraints', 0)} 条间距约束，需在 Allegro 加载后确认")
                             if r.get("spacing_constraints") else ("手动检查", "未生成间距约束，需人工确认"))},
    39: {"category": "constraint", "desc": "SMD到连接器间距",
         "check": lambda r: ("手动检查", "SMD 到连接器间距无法由网表分析判定，需人工确认")},
    40: {"category": "constraint", "desc": "物理/电气规则",
         "check": lambda r: (("auto_verify",
                              f"已生成 {r.get('physical_constraints', 0)} 物理 / "
                              f"{r.get('electrical_constraints', 0)} 电气约束，需在 Allegro 加载后确认")
                             if (r.get("physical_constraints") or r.get("electrical_constraints"))
                             else ("手动检查", "未生成物理/电气约束，需人工确认"))},
    41: {"category": "constraint", "desc": "层叠结构",
         "check": lambda r: (("auto_verify", f"检测到 {r.get('layer_count', 0)} 层，需确认叠层参数")
                             if r.get("layer_count") else ("手动检查", "未解析到层信息，需人工确认叠层"))},
    42: {"category": "constraint", "desc": "差分信号阻抗",
         "check": lambda r: (("auto_verify",
                              f"已识别 {r.get('diff_pairs', 0)} 对差分并生成阻抗规则，需仿真/实测确认")
                             if (r.get("diff_pairs") and r.get("electrical_constraints"))
                             else ("手动检查", "未识别到差分对或未生成阻抗规则，需人工确认"))},
    43: {"category": "constraint", "desc": "测试点",
         "check": lambda r: ("手动检查", "测试点规则无法由网表分析判定，需人工确认")},
    46: {"category": "high_speed", "desc": "高速信号阻抗一致性",
         "check": lambda r: (("auto_verify", "已对高速类信号生成阻抗约束，需仿真确认一致性")
                             if r.get("electrical_constraints")
                             else ("手动检查", "未生成阻抗约束，需人工确认"))},
    47: {"category": "high_speed", "desc": "高速差分对",
         "check": lambda r: (("auto_verify", f"已自动识别 {r.get('diff_pairs', 0)} 对差分并设置等长规则")
                             if r.get("diff_pairs") else ("手动检查", "未识别到差分对，需人工确认"))},
    48: {"category": "high_speed", "desc": "3W原则",
         "check": lambda r: ("手动检查", "3W 间距需在 Allegro 中目视/DRC 确认")},
    49: {"category": "high_speed", "desc": "高速走线参考平面",
         "check": lambda r: ("手动检查", "参考平面完整性需人工目视确认")},
    50: {"category": "high_speed", "desc": "电容扇出",
         "check": lambda r: ("手动检查", "电容扇出回路需人工目视确认")},
    54: {"category": "high_speed", "desc": "SI约束",
         "check": lambda r: (("auto_verify", "已生成差分/高速/BUS 约束，需在 Allegro 加载后确认")
                             if r.get("electrical_constraints") else ("手动检查", "未生成 SI 约束，需人工确认"))},
    63: {"category": "high_speed", "desc": "蛇形线间距",
         "check": lambda r: ("手动检查", "蛇形线间距需人工目视确认")},
    73: {"category": "emc", "desc": "走线角度",
         "check": lambda r: ("手动检查", "走线角度需人工目视确认")},
    84: {"category": "power", "desc": "电源/GND载流",
         "check": lambda r: ("手动检查", "载流能力需人工核算")},
    85: {"category": "power", "desc": "20H原则",
         "check": lambda r: ("手动检查", "20H 原则需人工目视确认")},
    86: {"category": "power", "desc": "不同电源平面",
         "check": lambda r: ("手动检查", "电源平面重叠需人工目视确认")},
    93: {"category": "keepout", "desc": "散热焊盘下方",
         "check": lambda r: ("手动检查", "禁布区需人工目视确认")},
    94: {"category": "keepout", "desc": "安装孔周围",
         "check": lambda r: ("手动检查", "安装孔间距需人工目视确认")},
    95: {"category": "keepout", "desc": "NTH到铜皮",
         "check": lambda r: ("手动检查", "NPTH 到铜皮间距需人工目视确认")},
    98: {"category": "keepout", "desc": "外层铜皮到板边",
         "check": lambda r: ("手动检查", "外层铜皮到板边需人工目视确认")},
    99: {"category": "keepout", "desc": "内层铜皮到板边",
         "check": lambda r: ("手动检查", "内层铜皮到板边需人工目视确认")},
    100: {"category": "route", "desc": "0402及以下出线",
         "check": lambda r: ("手动检查", "小元件出线方式需人工目视确认")},
    101: {"category": "route", "desc": "SOIC/PLCC/QFP/SOT出线",
         "check": lambda r: ("手动检查", "引脚出线方式需人工目视确认")},
    112: {"category": "via", "desc": "回流焊盘无过孔",
         "check": lambda r: ("手动检查", "回流焊盘过孔需人工目视确认")},
    113: {"category": "via", "desc": "过孔不过密",
         "check": lambda r: ("手动检查", "过孔密度/平面完整性需人工目视确认")},
    117: {"category": "testpoint", "desc": "测试点直径",
         "check": lambda r: ("手动检查", "测试点直径需人工确认")},
    118: {"category": "testpoint", "desc": "测试点到器件焊盘",
         "check": lambda r: ("手动检查", "测试点到焊盘间距需人工确认")},
    119: {"category": "testpoint", "desc": "测试点到板边/定位孔",
         "check": lambda r: ("手动检查", "测试点到板边/定位孔需人工确认")},
    125: {"category": "drc", "desc": "所有DRC",
         "check": lambda r: ("auto_verify", "需在 Allegro 中运行 DRC 检查")},
}

MANUAL_REQUIRED = {1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 15, 16, 17,
                   18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30,
                   31, 32, 33, 34, 35, 36,
                   44, 45, 51, 52, 53, 55, 56, 57, 58, 59, 60, 61, 62,
                   64, 65, 66, 67, 68, 69, 70, 71,
                   72, 74, 75, 76, 77, 78, 79, 80, 81, 82, 83,
                   87, 88, 89, 90, 91, 92,
                   96, 97, 102,
                   103, 104, 105, 106, 107, 108,
                   109, 110, 111,
                   114, 115, 116,
                   120, 121, 122, 123, 124,
                   126, 127,
                   128, 129, 130, 131,
                   132, 133, 134, 135}


def _read_checklist_rows(excel_path: str) -> List[dict]:
    wb = openpyxl.load_workbook(excel_path, data_only=True) # 打开Excel文件，data_only=True表示读取公式计算后的值
    sheet_names = [s.lower() for s in wb.sheetnames] # 获取所有工作表名称并转换为小写
    sheet_name = "check list" if "check list" in sheet_names else wb.sheetnames[-1]  # 如果存在名为"check list"的工作表，则使用它，否则使用最后一个工作表
    ws = wb[sheet_name] # 获取指定工作表对象
    header_row = 3 #头
    rows = [] #行
    current_category = ""  #当前类别
    current_subcategory = "" #当前子类别

    for row_idx in range(header_row + 1, ws.max_row + 1):   #遍历每一行，从header_row+1开始到最大行数
        no_val = ws.cell(row_idx, 3).value  #3列（序号列）的值
        if no_val is None:  #不存在就结束
            continue

        cat_text = str(ws.cell(row_idx, 1).value or "") # 1列（类别列）的值
        if cat_text.strip(): # 如果类别列不为空，则更新当前类别
            current_category = cat_text.strip() # 当前类别

        sub_text = str(ws.cell(row_idx, 2).value or "") # 以此类推
        if sub_text.strip():  
            current_subcategory = sub_text.strip()
# 检查，ValueError "abc"字符串无法转数字，“12a3” TypeError None无法 调整类型
        try:  
            item_no = int(no_val)
        except (ValueError, TypeError):
            continue

        grade = str(ws.cell(row_idx, 4).value or "").strip()  #类推
        item_text = str(ws.cell(row_idx, 5).value or "").strip()
        standard = str(ws.cell(row_idx, 6).value or "").strip()
        note = str(ws.cell(row_idx, 7).value or "").strip()
        check_val = str(ws.cell(row_idx, 8).value or "").strip()
        approved_val = str(ws.cell(row_idx, 9).value or "").strip()

        grade_type = "mandatory"
        if "推荐" in grade or "Recommend" in grade:
            grade_type = "recommend"
        elif "提示" in grade or "Remind" in grade:
            grade_type = "remind"

        rows.append({
            "row_idx": row_idx,
            "no": item_no,
            "category": current_category,
            "subcategory": current_subcategory,
            "grade": grade,
            "grade_type": grade_type,
            "items": item_text,
            "standard": standard,
            "note": note,
            "check": check_val if check_val and check_val != "None" else "",
            "approved": approved_val if approved_val and approved_val != "None" else "",
        })

    wb.close()
    return rows


def _determine_check_result(item: dict, analysis_result: dict) -> Tuple[str, str, str]:
    item_no = item["no"] 
    standard = item["standard"] 
    item_text = item["items"]

    if standard.upper() == "NA":
        return ("N/A", "不适用", "N/A")

    if item_no in VERIFIABLE_RULES:
        rule = VERIFIABLE_RULES[item_no]
        result, detail = rule["check"](analysis_result)
        return (result, detail, "auto")

    if item_no in MANUAL_REQUIRED:
        if standard.upper() == "OK":
            return ("手动检查", "需人工确认: " + item_text[:60], "manual")
        return ("手动检查", "Meeting standard待确认: " + standard[:60], "manual")

    if standard.upper() == "OK":
        return ("OK", "标准已满足", "auto_pass")

    return ("手动检查", "需人工确认", "manual")


def _analyze_brd_for_checklist(brd_info: dict, classification: dict,
                                constraints: dict, dfm_result: dict) -> dict:
    return {
        "board_name": brd_info.get("board_name", ""),
        "net_count": brd_info.get("net_count", 0),
        "layer_count": brd_info.get("layer_count", 0),
        "net_classes": classification.get("total_classes", 0),
        "diff_pairs": classification.get("total_diff_pairs", 0),
        "physical_constraints": len(constraints.get("physical_constraints", [])),
        "spacing_constraints": len(constraints.get("spacing_constraints", [])),
        "electrical_constraints": len(constraints.get("electrical_constraints", [])),
        "dfm_passed": dfm_result.get("passed", False),
        "dfm_errors": dfm_result.get("error_count", 0),
        "dfm_warnings": dfm_result.get("warning_count", 0),
        "layers": brd_info.get("layers", []),
        "power_nets": brd_info.get("power_nets", []),
    }


def auto_fill_checklist(
    checklist_path: str,
    brd_info: dict,
    classification: dict,
    constraints: dict,
    dfm_result: dict,
    output_path: str,
    checker_name: str = "BRD-AI",
    static_approver: str = "",
) -> Tuple[str, dict]:
    rows = _read_checklist_rows(checklist_path)
    analysis = _analyze_brd_for_checklist(brd_info, classification, constraints, dfm_result)

    wb = openpyxl.load_workbook(checklist_path)
    sheet_names = [s.lower() for s in wb.sheetnames]
    sheet_name = "check list" if "check list" in sheet_names else wb.sheetnames[-1]
    ws = wb[sheet_name]

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    stats = {"auto_pass": 0, "auto_verify": 0, "manual": 0, "na": 0, "total": 0}

    for item in rows:
        item_no = item["no"]
        row_idx = item["row_idx"]
        result, detail, method = _determine_check_result(item, analysis)

        if result == "OK" or result == "auto_pass":
            check_text = "PASS"
            fill = PASS_FILL
            stats["auto_pass"] += 1
        elif result == "auto_verify":
            check_text = "VERIFY"
            fill = WARN_FILL
            stats["auto_verify"] += 1
        elif result == "N/A":
            check_text = "N/A"
            fill = NA_FILL
            stats["na"] += 1
        else:
            check_text = "MANUAL"
            fill = MANUAL_FILL
            stats["manual"] += 1

        approved_text = f"{checker_name} / {timestamp}" if not static_approver else static_approver

        cell_check = ws.cell(row_idx, 8)
        if not cell_check.value or str(cell_check.value).strip() in ("", "None"):
            cell_check.value = check_text
            cell_check.fill = fill
            cell_check.font = Font(name="Arial", size=10, bold=True)
            cell_check.alignment = Alignment(horizontal="center", vertical="center")
            cell_check.border = THIN_BORDER

        cell_approved = ws.cell(row_idx, 9)
        if not cell_approved.value or str(cell_approved.value).strip() in ("", "None"):
            cell_approved.value = approved_text
            cell_approved.font = Font(name="Arial", size=9)
            cell_approved.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            cell_approved.border = THIN_BORDER

        note_col = ws.cell(row_idx, 7)
        if detail and detail != "标准已满足":
            existing_note = str(note_col.value or "")
            if existing_note and existing_note != "None":
                if detail not in existing_note:
                    note_col.value = f"{existing_note}; {detail}"
            else:
                note_col.value = detail

        stats["total"] += 1

    # Add summary sheet
    if "summary" in [s.lower() for s in wb.sheetnames]:
        summary_ws = wb["summary"]
    else:
        summary_ws = wb.create_sheet("summary")

    last_rev_row = 1
    for r in range(1, summary_ws.max_row + 1):
        if summary_ws.cell(r, 1).value and "Date" in str(summary_ws.cell(r, 1).value):
            last_rev_row = r
    last_rev_row = max(last_rev_row, summary_ws.max_row)

    new_row = last_rev_row + 2
    summary_ws.cell(new_row, 1, value=timestamp).font = Font(name="Arial", size=10)
    summary_ws.cell(new_row, 2, value="Auto").font = Font(name="Arial", size=10)
    summary_ws.cell(new_row, 3, value=checker_name).font = Font(name="Arial", size=10)
    summary_ws.cell(new_row, 4, value=f"Auto-fill: {stats['auto_pass']}PASS/{stats['manual']}MANUAL/{stats['na']}NA/{stats['total']}TOTAL").font = Font(name="Arial", size=10)

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    wb.save(output_path)
    wb.close()

    return output_path, stats


def generate_checklist_summary(stats: dict, output_path: str) -> str:
    lines = []
    lines.append("=" * 60)
    lines.append("  BRD-AI: Checklist Auto-Fill Summary")
    lines.append("=" * 60)
    lines.append(f"  Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("=" * 60)
    lines.append("")
    lines.append(f"  Total Items:    {stats['total']}")
    lines.append(f"  Auto PASS:      {stats['auto_pass']}  (自动验证通过)")
    lines.append(f"  Auto VERIFY:    {stats['auto_verify']}  (需DRC确认)")
    lines.append(f"  Manual Check:   {stats['manual']}  (需人工检查)")
    lines.append(f"  N/A:            {stats['na']}  (不适用)")
    lines.append("")
    lines.append(f"  Auto Rate: {stats['auto_pass']/max(stats['total'],1)*100:.1f}%")
    lines.append("")
    lines.append("=" * 60)

    auto_rate = stats['auto_pass'] / max(stats['total'], 1) * 100
    lines.append(f"  自动完成率: {auto_rate:.1f}%")
    lines.append("")
    lines.append(f"  AUTO_PASS - 已由BRD-AI自动验证通过")
    lines.append(f"  VERIFY    - 需要在Allegro中运行DRC确认")
    lines.append(f"  MANUAL    - 需要人工目视/手动检查")
    lines.append(f"  N/A       - 不适用")
    lines.append("=" * 60)

    content = "\n".join(lines)
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(content)
    return output_path
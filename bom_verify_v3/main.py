"""BOM Verification Tool V3 - Entry point.

Usage:
    python -m bom_verify_v3.main              # Launch GUI
    python -m bom_verify_v3.main --cli        # CLI mode
"""

import argparse
import os
import sys

from .models import CompareMode
from .comparators import run_comparison
from .reports import generate_report


MODE_MAP = {
    "pdf2excel": CompareMode.EXCEL_VS_PDF,
    "mpn": CompareMode.EXCEL_VS_PDF_MPN,
    "excel_excel": CompareMode.EXCEL_VS_EXCEL,
    "excelmpn": CompareMode.EXCEL_VS_EXCEL_MPN,
    "pdf_pdf": CompareMode.PDF_VS_PDF,
    "group": CompareMode.GROUP,
}


def main_cli():
    parser = argparse.ArgumentParser(description="BOM <-> PDF 器件核对工具 V3")
    parser.add_argument("--mode", "-m", required=True,
                        choices=list(MODE_MAP.keys()),
                        help="对比模式")
    parser.add_argument("--file-a", "-a", required=True, help="文件A路径")
    parser.add_argument("--file-b", "-b", required=True, help="文件B路径")
    parser.add_argument("--out", "-o", default=".", help="输出目录")
    args = parser.parse_args()

    mode = MODE_MAP[args.mode]
    print(f"模式: {mode.value}")
    print(f"文件A: {args.file_a}")
    print(f"文件B: {args.file_b}")

    results, stats = run_comparison(mode, args.file_a, args.file_b)

    print(f"\n=== 汇总 ===")
    for k, v in stats.summary.items():
        print(f"  {k}: {v}")

    xlsx, txt = generate_report(
        results, stats, args.out,
        source_label=os.path.basename(args.file_a),
        path_a=args.file_a, path_b=args.file_b,
    )
    print(f"\n报告已生成:")
    print(f"  Excel: {xlsx}")
    print(f"  TXT:   {txt}")


def main():
    if "--cli" in sys.argv or "-c" in sys.argv:
        sys.argv = [a for a in sys.argv if a not in ("--cli", "-c")]
        main_cli()
    else:
        from .gui import run_gui
        run_gui()


if __name__ == "__main__":
    main()
"""Excel -> Excel designator set comparison."""

import os

from ..models import CompareResult, CompareStats, CompareMode, CompareStatus
from .base import BaseComparator, register


@register(CompareMode.EXCEL_VS_EXCEL)
class ExcelExcelComparator(BaseComparator):
    """Compare two Excel BOMs by designator set."""

    def compare(
        self, path_a: str, path_b: str, col_name: str = None, **kwargs
    ) -> tuple[list[CompareResult], CompareStats]:
        from ..extractors import extract_designators_from_excel

        set_a, map_a, col_a = extract_designators_from_excel(path_a, col_name)
        set_b, map_b, col_b = extract_designators_from_excel(path_b, col_name)

        common = sorted(set_a & set_b)
        only_a = sorted(set_a - set_b)
        only_b = sorted(set_b - set_a)

        results = []
        for d in common:
            results.append(CompareResult(
                key=d, status=CompareStatus.MATCH, mode=CompareMode.EXCEL_VS_EXCEL,
                source_a={"row": map_a.get(d, "")}, source_b={"row": map_b.get(d, "")},
                extra={"a": map_a.get(d, ""), "b": map_b.get(d, ""), "a_pages": "", "b_pages": ""},
            ))
        for d in only_a:
            results.append(CompareResult(
                key=d, status=CompareStatus.A_ONLY, mode=CompareMode.EXCEL_VS_EXCEL,
                source_a={"row": map_a.get(d, "")}, source_b={},
                extra={"a": map_a.get(d, ""), "b": "", "a_pages": "", "b_pages": ""},
            ))
        for d in only_b:
            results.append(CompareResult(
                key=d, status=CompareStatus.B_ONLY, mode=CompareMode.EXCEL_VS_EXCEL,
                source_a={}, source_b={"row": map_b.get(d, "")},
                extra={"a": "", "b": map_b.get(d, ""), "a_pages": "", "b_pages": ""},
            ))

        stats = CompareStats(
            mode=CompareMode.EXCEL_VS_EXCEL,
            total_keys=len(set_a | set_b),
            matched=len(common),
            only_a=len(only_a),
            only_b=len(only_b),
            extra_info={
                "文件A位号数": len(set_a),
                "文件B位号数": len(set_b),
                "两文件一致": len(common),
                "对比列": col_a,
            },
        )
        return results, stats
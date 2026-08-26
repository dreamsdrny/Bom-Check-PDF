"""PDF -> Excel designator comparison.
Exact logic match with original bom_pdf_verify.py compare_pdf_to_excel()."""

import re as _re

from ..models import CompareResult, CompareStats, CompareMode, CompareStatus, FieldDiff
from ..normalizer import normalize_value as _app_smart
from .base import BaseComparator, register


@register(CompareMode.EXCEL_VS_PDF)
class PdfToExcelComparator(BaseComparator):
    """Compare PDF designators against Excel BOM."""

    def compare(
        self, bom_path: str, pdf_path: str, primary: str = "Part Reference", **kwargs
    ) -> tuple[list[CompareResult], CompareStats]:
        from ..readers import load_bom, decode_pdf_blocks, _designator_col_name
        from ..extractors import build_pdf_designator_annotations, split_designators_text

        pages_words, _, _ = decode_pdf_blocks(pdf_path)
        annotations = build_pdf_designator_annotations(pages_words)

        headers, data = load_bom(bom_path)
        primary = (
            primary if primary in headers
            else (_designator_col_name(headers) or headers[0])
        )
        pidx = headers.index(primary)
        vidx = headers.index("Value") if "Value" in headers else None
        fidx = headers.index("PCB Footprint") if "PCB Footprint" in headers else None
        qidx = headers.index("Quantity") if "Quantity" in headers else None

        excel_index = {}
        for row in data:
            for d in split_designators_text(row["values"][pidx]):
                excel_index.setdefault(d.upper(), []).append(row)

        results = []
        n_found = n_pdf_only = n_ok = n_may = 0

        for des in sorted(annotations.keys()):
            ann = annotations[des]
            near = ann["near"]
            near_text = "; ".join(near[:8])
            excel_rows = excel_index.get(des)

            if not excel_rows:
                n_pdf_only += 1
                results.append(CompareResult(
                    key=des, status=CompareStatus.PDF_ONLY, mode=CompareMode.EXCEL_VS_PDF,
                    source_a={"designator": des, "pdf_pages": ann["pages"]},
                    source_b={},
                    extra={"near": near_text, "valueA": "", "footA": "", "qty": ""},
                ))
                continue

            n_found += 1
            er = excel_rows[0]
            ev = er["values"][vidx] if vidx is not None else ""
            ef = er["values"][fidx] if fidx is not None else ""
            eq = er["values"][qidx] if qidx is not None else ""

            nv = _app_smart(ev)
            nf = _app_smart(ef)
            hit = False
            candidates = [str(x) for x in [ev, nv, ef, nf] if x]
            for c in candidates:
                c = str(c).strip().lower()
                if not c:
                    continue
                for w in near:
                    wl = w.lower()
                    if c == wl:
                        hit = True
                        break
                    if _app_smart(c) == _app_smart(w):
                        hit = True
                        break
                    c_short = _re.fullmatch(r"[\d.]{1,6}", c)
                    if c_short and len(c) <= 5 and (c in wl or wl in c):
                        hit = True
                        break
                if hit:
                    break

            if hit:
                n_ok += 1
                results.append(CompareResult(
                    key=des, status=CompareStatus.MATCH, mode=CompareMode.EXCEL_VS_PDF,
                    source_a={"designator": des, "value": ev, "footprint": ef, "quantity": eq},
                    source_b={"near_text": near_text},
                    extra={"near": near_text, "valueA": ev, "footA": ef, "qty": eq},
                ))
            else:
                n_may += 1
                results.append(CompareResult(
                    key=des, status=CompareStatus.PENDING, mode=CompareMode.EXCEL_VS_PDF,
                    source_a={"designator": des, "value": ev, "footprint": ef, "quantity": eq},
                    source_b={"near_text": near_text},
                    diffs=[FieldDiff(field="值/封装", value_a=str(ev), value_b="PDF上未匹配")],
                    extra={"near": near_text, "valueA": ev, "footA": ef, "qty": eq},
                ))

        stats = CompareStats(
            mode=CompareMode.EXCEL_VS_PDF,
            total_keys=len(annotations),
            matched=n_ok,
            only_a=n_pdf_only,
            pending=n_may,
            extra_info={
                "主键": primary,
                "PDF位号总数": len(annotations),
                "Excel中找到": n_found,
                "PDF有Excel无": n_pdf_only,
                "值疑似一致": n_ok,
                "值疑似不一致/待确认": n_may,
            },
        )
        return results, stats
"""PDF -> Excel MPN comparison."""

from ..models import CompareResult, CompareStats, CompareMode, CompareStatus, FieldDiff
from ..normalizer import normalize_qty, normalize_key
from .base import BaseComparator, register


@register(CompareMode.PDF_TO_EXCEL_MPN)
class PdfToExcelMpnComparator(BaseComparator):
    """Compare PDF MPNs against Excel BOM."""

    def compare(
        self, bom_path: str, pdf_path: str, mpn_col: str = None, **kwargs
    ) -> tuple[list[CompareResult], CompareStats]:
        from ..readers import load_bom, decode_pdf_blocks, _find_col
        from ..extractors import (
            split_designators_text,
            _extract_pdf_mpn_index,
            _linked_designators,
        )
        from ..config import MPN_COL_KEYS, QTY_COL_KEYS, REF_COL_KEYS, MFG_COL_KEYS

        headers, data = load_bom(bom_path)
        if mpn_col not in headers:
            mpn_col = None
        mpn_col = mpn_col or _find_col(headers, MPN_COL_KEYS) or headers[0]
        qty_col = _find_col(headers, QTY_COL_KEYS)
        ref_col = _find_col(headers, REF_COL_KEYS)
        mfg_col = _find_col(headers, MFG_COL_KEYS)

        if mpn_col not in headers:
            raise RuntimeError("找不到 MPN/料号 列")

        mi = headers.index(mpn_col)
        qi = headers.index(qty_col) if qty_col in headers else None
        ri = headers.index(ref_col) if ref_col in headers else None
        fi = headers.index(mfg_col) if mfg_col in headers else None

        pages_words, _, _ = decode_pdf_blocks(pdf_path)
        pdf_mpn = _extract_pdf_mpn_index(pages_words)

        excel_rows = []
        for row in data:
            mpn = normalize_key(row["values"][mi])
            if not mpn:
                continue
            excel_rows.append({
                "mpn": mpn,
                "mpn_raw": row["values"][mi],
                "qty_n": (
                    str(row["values"][qi]).strip()
                    if qi is not None and row["values"][qi] not in (None, "")
                    else ""
                ),
                "refs": split_designators_text(row["values"][ri]) if ri is not None else [],
                "mfg": ("" if fi is None else row["values"][fi]) or "",
            })

        results = []
        seens = set()
        for er in excel_rows:
            mpn = er["mpn"]
            if mpn in seens:
                continue
            seens.add(mpn)
            phits = pdf_mpn.get(mpn, [])
            pages = sorted({p for p, _, _ in phits})

            if not phits:
                results.append(CompareResult(
                    key=mpn, status=CompareStatus.ONLY_A, mode=CompareMode.PDF_TO_EXCEL_MPN,
                    source_a={"mpn": mpn, "qty": er["qty_n"], "refs": er["refs"], "mfg": er["mfg"]},
                    source_b={},
                    diffs=[FieldDiff(field="MPN", value_a=mpn, value_b="PDF中未找到")],
                    extra={"qty_a": er["qty_n"], "qty_b": "", "refs_a": ",".join(er["refs"]),
                           "refs_b": "", "mfg": er["mfg"], "pages": ""},
                ))
                continue

            pdf_refs = []
            seen_refs = set()
            for p, y, x in phits:
                for d in _linked_designators(pages_words, p, y, x, mpn):
                    if d not in seen_refs:
                        seen_refs.add(d)
                        pdf_refs.append(d)

            notes = []
            eqty = normalize_qty(er["qty_n"])
            p0, y0, x0 = phits[0]
            pdf_est = _linked_designators(pages_words, p0, y0, x0, mpn)
            if eqty is not None and pdf_est and eqty != len(pdf_est):
                notes.append(f"Excel数量={eqty} 但PDF就近位号≈{len(pdf_est)}")
            er_set = set(er["refs"])
            missing = sorted(er_set - set(pdf_est))
            if missing and pdf_est:
                notes.append(f"PDF就近未到位号: {','.join(missing[:10])}")

            if not notes:
                status = CompareStatus.MATCH
            else:
                status = CompareStatus.PENDING
            diffs = [FieldDiff(field="位号/数量", value_a=str(er["refs"]), value_b="; ".join(notes))] if notes else []

            results.append(CompareResult(
                key=mpn, status=status, mode=CompareMode.PDF_TO_EXCEL_MPN,
                source_a={"mpn": mpn, "qty": er["qty_n"], "refs": er["refs"], "mfg": er["mfg"]},
                source_b={"pdf_refs": pdf_refs, "pages": pages},
                diffs=diffs,
                extra={
                    "qty_a": er["qty_n"], "qty_b": str(len(pdf_refs)),
                    "refs_a": ",".join(sorted(er["refs"])),
                    "refs_b": ",".join(sorted(pdf_refs)),
                    "mfg": er["mfg"], "pages": ",".join(str(p) for p in pages),
                },
            ))

        pdf_only = sorted(set(pdf_mpn.keys()) - seens)
        n_ok = sum(1 for r in results if r.status == CompareStatus.MATCH)
        n_confirm = sum(1 for r in results if r.status == CompareStatus.PENDING)
        n_only_a = sum(1 for r in results if r.status == CompareStatus.ONLY_A)

        stats = CompareStats(
            mode=CompareMode.PDF_TO_EXCEL_MPN,
            total_keys=len(seens) + len(pdf_only),
            matched=n_ok,
            only_a=n_only_a,
            only_b=len(pdf_only),
            pending=n_confirm,
            extra_info={
                "主键": mpn_col,
                "Excel 料号(MPN)总数": len(seens),
                "PDF 识别料号总数": len(pdf_mpn),
                "一致(数量/位号匹配)": n_ok,
                "待确认(数量或位号提示)": n_confirm,
                "仅Excel有(PDF无此料号)": n_only_a,
                "PDF有而Excel无": len(pdf_only),
            },
        )
        return results, stats
"""Excel -> Excel MPN (multi-key) comparison with aggregation."""

from ..models import CompareResult, CompareStats, CompareMode, CompareStatus, FieldDiff
from ..normalizer import normalize_key, normalize_qty, normalize_text
from .base import BaseComparator, register


@register(CompareMode.EXCEL_VS_EXCEL_MPN)
class ExcelExcelMpnComparator(BaseComparator):
    """Compare two Excel BOMs by MPN/primary key."""

    def compare(
        self, path_a: str, path_b: str,
        key_a=None, key_b=None, qty_a=None, qty_b=None, **kwargs
    ) -> tuple[list[CompareResult], CompareStats]:
        from ..readers import load_excel_table, _find_col
        from ..extractors import split_designators_text
        from ..config import (
            MPN_COL_KEYS, QTY_COL_KEYS, REF_COL_KEYS,
            MFG_COL_KEYS, ITM_COL_KEYS, DESC_COL_KEYS,
        )

        ha, da, hb, db = load_excel_table(path_a, path_b)

        def _resolve_multi(headers, cols, keys):
            if cols is None:
                cols = []
            if isinstance(cols, str):
                cols = [cols]
            cols = [c for c in cols if c and c in headers]
            if cols:
                return cols
            auto = _find_col(headers, keys)
            return [auto] if auto else ([headers[0]] if headers else [])

        ma = _resolve_multi(ha, key_a, MPN_COL_KEYS)
        mb = _resolve_multi(hb, key_b, MPN_COL_KEYS)
        qa = _find_col(ha, QTY_COL_KEYS)
        qb = _find_col(hb, QTY_COL_KEYS)
        if qty_a and qty_a in ha:
            qa = qty_a
        if qty_b and qty_b in hb:
            qb = qty_b
        ra = _find_col(ha, REF_COL_KEYS)
        rb = _find_col(hb, REF_COL_KEYS)
        fa = _find_col(ha, MFG_COL_KEYS)
        fb = _find_col(hb, MFG_COL_KEYS)
        ia_ = _find_col(ha, ITM_COL_KEYS)
        ib_ = _find_col(hb, ITM_COL_KEYS)
        da_ = _find_col(ha, DESC_COL_KEYS)
        db_ = _find_col(hb, DESC_COL_KEYS)

        if not ma or not mb:
            raise RuntimeError(f"文件A/文件B 中找不到主键列（{ma} / {mb}）")

        def build(headers, data, mcol_list, qcol, refcol, mfgcol, itmcol, desccol):
            mi = [headers.index(m) for m in mcol_list]
            qi = headers.index(qcol) if qcol in headers else None
            ri = headers.index(refcol) if refcol in headers else None
            fi = headers.index(mfgcol) if mfgcol in headers else None
            ii = headers.index(itmcol) if itmcol in headers else None
            di = headers.index(desccol) if desccol in headers else None
            idx = {}
            for row in data:
                pk = tuple(normalize_key(row["values"][i]) for i in mi)
                if not any(pk):
                    continue
                rec = {
                    "row": row,
                    "qty": (row["values"][qi] if qi is not None else "") or "",
                    "refs": split_designators_text(row["values"][ri]) if ri is not None else [],
                    "mfg": (row["values"][fi] if fi is not None else "") or "",
                    "itm": (row["values"][ii] if ii is not None else "") or "",
                    "desc": (row["values"][di] if di is not None else "") or "",
                }
                idx.setdefault(pk, []).append(rec)
            return idx

        def _key_label(pk, mcol_list):
            if len(mcol_list) == 1:
                return str(pk[0])
            return " | ".join(f"{m}={v}" for m, v in zip(mcol_list, pk) if v)

        ia = build(ha, da, ma, qa, ra, fa, ia_, da_)
        ib = build(hb, db, mb, qb, rb, fb, ib_, db_)
        keys = sorted(set(ia.keys()) | set(ib.keys()), key=lambda k: tuple(k))

        def aggregate(records):
            qty_values = [normalize_qty(r["qty"]) for r in records]
            numeric_qty = [q for q in qty_values if isinstance(q, (int, float))]
            qty_sum = sum(numeric_qty) if len(numeric_qty) == len(qty_values) and qty_values else None
            refs = sorted(set(d for r in records for d in r["refs"]))
            mfgs = [str(r["mfg"] or "").strip() for r in records if str(r["mfg"] or "").strip()]
            itms = [str(r["itm"] or "").strip() for r in records if str(r["itm"] or "").strip()]
            descs = [str(r["desc"] or "").strip() for r in records if str(r["desc"] or "").strip()]
            return {
                "row": records[0]["row"],
                "qty": qty_sum if qty_sum is not None else (records[0]["qty"] if records else ""),
                "refs": refs,
                "mfg": mfgs[0] if mfgs else "",
                "itm": itms[0] if itms else "",
                "desc": descs[0] if descs else "",
                "duplicate_count": len(records),
            }

        results = []
        n_ok = n_diff = n_only_a = n_only_b = 0
        for k in keys:
            label = _key_label(k, ma if k in ia else mb)
            ra_ = ia.get(k, [])
            rb_ = ib.get(k, [])
            if not rb_:
                n_only_a += 1
                results.append(CompareResult(
                    key=label, status=CompareStatus.A_ONLY, mode=CompareMode.EXCEL_VS_EXCEL_MPN,
                    source_a=aggregate(ra_) if ra_ else {},
                    source_b={},
                ))
                continue
            if not ra_:
                n_only_b += 1
                results.append(CompareResult(
                    key=label, status=CompareStatus.B_ONLY, mode=CompareMode.EXCEL_VS_EXCEL_MPN,
                    source_a={},
                    source_b=aggregate(rb_) if rb_ else {},
                ))
                continue

            ra0, rb0 = aggregate(ra_), aggregate(rb_)
            diffs = []

            qa_n, qb_n = normalize_qty(ra0["qty"]), normalize_qty(rb0["qty"])
            if qa_n != qb_n:
                diffs.append(FieldDiff(
                    field=f"数量({qa or 'Quantity'})",
                    value_a=str(ra0["qty"] or ""), value_b=str(rb0["qty"] or ""),
                ))
            sa, sb = set(ra0["refs"]), set(rb0["refs"])
            if sa != sb:
                diffs.append(FieldDiff(
                    field=f"位号({ra or 'Reference'})",
                    value_a=",".join(sorted(sa)), value_b=",".join(sorted(sb)),
                ))
            mfa_, mfb_ = str(ra0["mfg"] or "").strip(), str(rb0["mfg"] or "").strip()
            if normalize_text(mfa_) != normalize_text(mfb_):
                diffs.append(FieldDiff(
                    field="厂商(Manufacturer)", value_a=mfa_, value_b=mfb_,
                ))
            if normalize_text(ra0["itm"]) != normalize_text(rb0["itm"]):
                diffs.append(FieldDiff(
                    field="内部料号(Item Number)",
                    value_a=str(ra0["itm"] or ""), value_b=str(rb0["itm"] or ""),
                ))
            if normalize_text(ra0["desc"]) != normalize_text(rb0["desc"]):
                diffs.append(FieldDiff(
                    field="描述(Description)",
                    value_a=str(ra0["desc"] or ""), value_b=str(rb0["desc"] or ""),
                ))

            if diffs:
                n_diff += 1
                results.append(CompareResult(
                    key=label, status=CompareStatus.FIELD_MISMATCH,
                    mode=CompareMode.EXCEL_VS_EXCEL_MPN,
                    source_a=ra0, source_b=rb0, diffs=diffs,
                ))
            else:
                n_ok += 1
                results.append(CompareResult(
                    key=label, status=CompareStatus.MATCH,
                    mode=CompareMode.EXCEL_VS_EXCEL_MPN,
                    source_a=ra0, source_b=rb0,
                ))

        stats = CompareStats(
            mode=CompareMode.EXCEL_VS_EXCEL_MPN,
            total_keys=len(keys),
            matched=n_ok,
            field_mismatch=n_diff,
            only_a=n_only_a,
            only_b=n_only_b,
            extra_info={
                "文件A主键列": ", ".join(ma),
                "文件B主键列": ", ".join(mb),
                "文件A数量列": qa or "(未识别)",
                "文件B数量列": qb or "(未识别)",
                "重复主键组数": sum(1 for v in ia.values() if len(v) > 1)
                + sum(1 for v in ib.values() if len(v) > 1),
            },
        )
        return results, stats
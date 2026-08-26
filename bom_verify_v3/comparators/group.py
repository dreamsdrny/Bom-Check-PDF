"""Excel -> Excel group comparison (by material group)."""

from ..models import CompareResult, CompareStats, CompareMode, CompareStatus
from ..normalizer import normalize_value
from .base import BaseComparator, register


@register(CompareMode.GROUP)
class GroupComparator(BaseComparator):
    """Compare two Excel BOMs by material group."""

    def compare(
        self, path_a: str, path_b: str,
        group_fields=("Value", "Manufacturer PN"),
        qty_col="Quantity", ref_col="Part Reference", **kwargs
    ) -> tuple[list[CompareResult], CompareStats]:
        from ..readers import load_excel_table
        from ..extractors import split_designators_text

        ha, da, hb, db = load_excel_table(path_a, path_b)

        def group_index(headers, data):
            g = {}
            for row in data:
                vals = row["values"]
                key = tuple(
                    (
                        normalize_value(vals[headers.index(f)])
                        if f in headers else None
                    )
                    for f in group_fields
                )
                key = tuple("" if k is None else str(k) for k in key)
                refs = split_designators_text(vals[headers.index(ref_col)])
                g.setdefault(key, []).extend(refs)
            return g

        ga = group_index(ha, da)
        gb = group_index(hb, db)
        all_keys = sorted(set(ga.keys()) | set(gb.keys()))

        results = []
        n_ok = n_diff = n_only_a = n_only_b = 0
        for key in all_keys:
            ra = sorted(ga.get(key, []))
            rb = sorted(gb.get(key, []))
            if not rb:
                n_only_a += 1
                results.append(CompareResult(
                    key=" | ".join(str(k) for k in key),
                    status=CompareStatus.A_ONLY, mode=CompareMode.GROUP,
                    source_a={"refs": ra, "qty": len(ra)}, source_b={},
                    extra={"refs_a": ra, "refs_b": [], "qty_a": len(ra), "qty_b": 0},
                ))
                continue
            if not ra:
                n_only_b += 1
                results.append(CompareResult(
                    key=" | ".join(str(k) for k in key),
                    status=CompareStatus.B_ONLY, mode=CompareMode.GROUP,
                    source_a={}, source_b={"refs": rb, "qty": len(rb)},
                    extra={"refs_a": [], "refs_b": rb, "qty_a": 0, "qty_b": len(rb)},
                ))
                continue
            only_in_a = sorted(set(ra) - set(rb))
            only_in_b = sorted(set(rb) - set(ra))
            if only_in_a or only_in_b:
                n_diff += 1
                results.append(CompareResult(
                    key=" | ".join(str(k) for k in key),
                    status=CompareStatus.DESIGNATOR_DIFF, mode=CompareMode.GROUP,
                    source_a={"refs": ra, "qty": len(ra)},
                    source_b={"refs": rb, "qty": len(rb)},
                    extra={
                        "refs_a": ra, "refs_b": rb,
                        "qty_a": len(ra), "qty_b": len(rb),
                        "only_a": only_in_a, "only_b": only_in_b,
                    },
                ))
            else:
                n_ok += 1
                results.append(CompareResult(
                    key=" | ".join(str(k) for k in key),
                    status=CompareStatus.MATCH, mode=CompareMode.GROUP,
                    source_a={"refs": ra, "qty": len(ra)},
                    source_b={"refs": rb, "qty": len(rb)},
                    extra={"refs_a": ra, "refs_b": rb, "qty_a": len(ra), "qty_b": len(rb)},
                ))

        stats = CompareStats(
            mode=CompareMode.GROUP,
            total_keys=len(all_keys),
            matched=n_ok,
            designator_diff=n_diff,
            only_a=n_only_a,
            only_b=n_only_b,
            extra_info={
                "分组键": "、".join(group_fields),
                "物料分组数": len(all_keys),
            },
        )
        return results, stats
"""PDF -> PDF designator set comparison."""

import os

from ..models import CompareResult, CompareStats, CompareMode, CompareStatus
from .base import BaseComparator, register


@register(CompareMode.PDF_VS_PDF)
class PdfPdfComparator(BaseComparator):
    """Compare two PDF schematics by designator set."""

    def compare(
        self, path_a: str, path_b: str, **kwargs
    ) -> tuple[list[CompareResult], CompareStats]:
        from ..readers import decode_pdf_blocks
        from ..extractors import extract_designators_from_pdf

        pages_words_a, _, _ = decode_pdf_blocks(path_a)
        pages_words_b, _, _ = decode_pdf_blocks(path_b)
        set_a, tokens_a = extract_designators_from_pdf(pages_words_a)
        set_b, tokens_b = extract_designators_from_pdf(pages_words_b)

        common = sorted(set_a & set_b)
        only_a = sorted(set_a - set_b)
        only_b = sorted(set_b - set_a)

        results = []
        for d in common:
            results.append(CompareResult(
                key=d, status=CompareStatus.MATCH, mode=CompareMode.PDF_VS_PDF,
                source_a={"pages": tokens_a.get(d, [])},
                source_b={"pages": tokens_b.get(d, [])},
                extra={
                    "a_pages": ",".join(str(p) for p in tokens_a.get(d, [])),
                    "b_pages": ",".join(str(p) for p in tokens_b.get(d, [])),
                },
            ))
        for d in only_a:
            results.append(CompareResult(
                key=d, status=CompareStatus.A_ONLY, mode=CompareMode.PDF_VS_PDF,
                source_a={"pages": tokens_a.get(d, [])}, source_b={},
                extra={
                    "a_pages": ",".join(str(p) for p in tokens_a.get(d, [])),
                    "b_pages": "",
                },
            ))
        for d in only_b:
            results.append(CompareResult(
                key=d, status=CompareStatus.B_ONLY, mode=CompareMode.PDF_VS_PDF,
                source_a={}, source_b={"pages": tokens_b.get(d, [])},
                extra={
                    "a_pages": "",
                    "b_pages": ",".join(str(p) for p in tokens_b.get(d, [])),
                },
            ))

        stats = CompareStats(
            mode=CompareMode.PDF_VS_PDF,
            total_keys=len(set_a | set_b),
            matched=len(common),
            only_a=len(only_a),
            only_b=len(only_b),
            extra_info={
                "PDF A 位号数": len(set_a),
                "PDF B 位号数": len(set_b),
                "两文件一致": len(common),
            },
        )
        return results, stats
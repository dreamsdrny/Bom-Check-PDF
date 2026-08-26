"""Unified data models for all comparison modes.

Design principle:
    Every comparison produces a list of CompareResult, regardless of mode.
    The GUI / report layer consumes CompareResult without mode-specific branching.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class CompareStatus(Enum):
    MATCH = "一致"
    FIELD_MISMATCH = "字段不一致"
    PDF_ONLY = "PDF有Excel无"
    EXCEL_ONLY = "仅Excel有(PDF无此料号)"
    A_ONLY = "仅文件A"
    B_ONLY = "仅文件B"
    PENDING = "待确认(值/封装在PDF上未匹配)"
    DESIGNATOR_DIFF = "位号集合差异"


class CompareMode(Enum):
    EXCEL_VS_PDF = "pdf2excel"
    EXCEL_VS_PDF_MPN = "mpn"
    EXCEL_VS_EXCEL = "excel_excel"
    EXCEL_VS_EXCEL_MPN = "excelmpn"
    PDF_VS_PDF = "pdf_pdf"
    GROUP = "group"


@dataclass
class FieldDiff:
    """One field-level difference between source A and B."""
    field: str
    value_a: str
    value_b: str


@dataclass
class CompareResult:
    """Unified comparison result row.

    key       - Primary identifier (designator or MPN)
    status    - Enum: MATCH / FIELD_MISMATCH / ONLY_A / ONLY_B / PENDING
    source_a  - Free-form dict of source A data (BOM row / PDF annotation)
    source_b  - Free-form dict of source B data
    diffs     - List of FieldDiff when status == FIELD_MISMATCH
    extra     - Mode-specific metadata (pages, near_text, etc.)
    """
    key: str
    status: CompareStatus
    mode: CompareMode
    source_a: dict = field(default_factory=dict)
    source_b: dict = field(default_factory=dict)
    diffs: list[FieldDiff] = field(default_factory=list)
    extra: dict = field(default_factory=dict)

    @property
    def is_match(self) -> bool:
        return self.status == CompareStatus.MATCH

    @property
    def is_only_a(self) -> bool:
        return self.status in (CompareStatus.A_ONLY, CompareStatus.PDF_ONLY, CompareStatus.EXCEL_ONLY)

    @property
    def is_only_b(self) -> bool:
        return self.status == CompareStatus.B_ONLY


@dataclass
class CompareStats:
    """Summary statistics for a comparison run."""
    mode: CompareMode
    total_keys: int = 0
    matched: int = 0
    field_mismatch: int = 0
    only_a: int = 0
    only_b: int = 0
    pending: int = 0
    designator_diff: int = 0
    extra_info: dict = field(default_factory=dict)

    @property
    def summary(self) -> dict:
        return {
            "模式": self.mode.value,
            "主键总数": self.total_keys,
            "一致": self.matched,
            "字段不一致": self.field_mismatch,
            "仅A有": self.only_a,
            "仅B有": self.only_b,
            "待确认": self.pending,
            "位号集合差异": self.designator_diff,
            **self.extra_info,
        }

    def to_rows(self) -> list[dict]:
        """Convert to legacy format for backward compat."""
        return [
            {"item": self.mode.value, "status": k, "valueA": str(v), "valueB": "",
             "footA": "", "footB": "", "near": "", "qty": ""}
            for k, v in self.summary.items()
        ]
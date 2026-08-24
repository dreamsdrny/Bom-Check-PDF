"""Constants, regex patterns, and column key definitions."""

import re

# ---------- Designator patterns ----------
DESIGNATOR_RE = re.compile(r"^[A-Za-z]{1,4}\d{1,4}(?:[A-Za-z]\d{1,2})?$")
PREFIXES_PARTS = set("CRLUDQJRTFXSWYKT")

# ---------- CSV encoding detection ----------
CSV_ENCODINGS = ["utf-8-sig", "utf-8", "gb18030", "gbk", "big5", "utf-16", "latin-1"]

# ---------- Column key definitions ----------
MPN_COL_KEYS = (
    "mpn", "manufacturer part number", "manufacturer pn", "mfr pn",
    "manufacturer item number", "manufacturer item", "part number", "part no",
    "料号", "型号",
)
MFG_COL_KEYS = (
    "manufacturer", "manufacture", "mfg", "vendor", "厂家", "供应商", "厂商",
)
QTY_COL_KEYS = ("quantity", "qty", "数量", "pcs")
REF_COL_KEYS = (
    "reference", "refdes", "designator", "refs", "位号", "ref",
    "references", "reference designator",
)
ITM_COL_KEYS = (
    "item number", "part number", "part no", "item no", "ipn", "零件号", "料号",
)
DESC_COL_KEYS = (
    "item name", "description", "desc", "part", "value", "名称", "描述", "spec",
)
DESIGNATOR_KEYS = (
    "part reference", "reference", "refdes", "ref des", "designator",
    "位号", "refs", "references", "device designator", "comp designator",
)
HEADER_KEYS = (
    "reference", "value", "quantity", "qty", "footprint", "package",
    "mpn", "part number", "part no", "manufacturer", "description",
    "item", "designator", "位号", "型号", "数量", "封装", "厂家", "料号", "描述",
)
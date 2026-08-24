"""BOM file reader supporting xlsx/csv/tsv/xls/xlsb/ods/html."""

import csv
import io
import math
import os
import re

from ..config import CSV_ENCODINGS, HEADER_KEYS, DESIGNATOR_KEYS


def _find_header_row(rows: list[list]) -> tuple[int, bool]:
    """Auto-detect header row index in multi-row tables."""
    best_i, best_score = -1, -1
    for i, row in enumerate(rows[:24]):
        cells = [re.sub(r"\s+", "", str(c or "")).lower() for c in row]
        score = 0
        joined = " ".join(cells)
        for k in HEADER_KEYS:
            if k in joined:
                score += 1
        for k in DESIGNATOR_KEYS:
            if k in joined and any(
                c.startswith(k.replace(" ", "")) or k.replace(" ", "") in c
                for c in cells
            ):
                score += 2
        if score > best_score:
            best_i, best_score = i, score
    if best_i < 0 and best_score < 2:
        return 0, False
    return max(best_i, 0), True


def _detect_designator_col(headers: list[str]) -> int:
    """Auto-detect the designator/reference column index."""
    names = [str(x or "").strip() for x in headers]
    low = [n.lower().replace(" ", "") for n in names]
    for k in DESIGNATOR_KEYS:
        kl = k.replace(" ", "")
        for i, n in enumerate(low):
            if n == kl or n.endswith(kl) or n.startswith(kl):
                return i
    return 0


def _designator_col_name(headers: list[str]) -> str | None:
    if not headers:
        return None
    return str(headers[_detect_designator_col(headers)])


def _find_col(headers: list[str], keys: tuple[str, ...]) -> str | None:
    """Find column by semantic key."""
    low = [str(x or "").lower().replace(" ", "") for x in headers]
    for k in keys:
        kl = k.lower().replace(" ", "")
        for i, n in enumerate(low):
            if n == kl or n.endswith(kl) or n.startswith(kl):
                return headers[i]
    return None


def _csv_content_rows(bom_path: str) -> tuple[list[list[str]], str]:
    """Read CSV/TSV/text table with auto encoding + delimiter detection."""
    raw = open(bom_path, "rb").read()
    text = None
    for cand in CSV_ENCODINGS:
        try:
            text = raw.decode(cand)
            break
        except (UnicodeDecodeError, LookupError):
            continue
    if text is None:
        text = raw.decode("latin-1", errors="replace")
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        delim = dialect.delimiter
    except Exception:
        delim = ","
    return [row for row in csv.reader(io.StringIO(text), delimiter=delim)], "utf-8"


def _load_xls(bom_path: str) -> tuple[list[str], list[dict]]:
    import xlrd
    wb = xlrd.open_workbook(bom_path)
    ws = wb.sheet_by_index(0)
    headers = []
    data = []
    for r in range(ws.nrows):
        row = [ws.cell_value(r, c) for c in range(ws.ncols)]
        row = ["" if v is None else v for v in row]
        if r == 0:
            headers = [str(x) for x in row]
        else:
            data.append({"header_row": r + 1, "values": row})
    return headers, data


def _load_xlsb(bom_path: str) -> tuple[list[str], list[dict]]:
    import pyxlsb
    headers = []
    data = []
    with pyxlsb.open_workbook(bom_path) as wb:
        ws = wb.get_sheet_by_name(wb.sheetnames[0])
        r = 0
        for row in ws.rows():
            r += 1
            vals = [("" if c is None else c.v) for c in row]
            if r == 1:
                headers = [str(x) for x in vals]
            else:
                data.append({"header_row": r, "values": vals})
    return headers, data


def _load_ods(bom_path: str) -> tuple[list[str], list[dict]]:
    from odf.opendocument import load
    from odf.table import Table, TableRow, TableCell
    from odf.text import P
    doc = load(bom_path)
    headers = []
    data = []
    for tbl in doc.spreadsheet.getElementsByType(Table):
        r = 0
        for row in tbl.getElementsByType(TableRow):
            r += 1
            vals = []
            for cell in row.getElementsByType(TableCell):
                txt = ""
                for p in cell.getElementsByType(P):
                    for node in p.childNodes:
                        if node.nodeType == node.TEXT_NODE:
                            txt += node.data
                        elif node.nodeType == node.ELEMENT_NODE and node.qName == "a":
                            txt += "".join(
                                n.data for n in node.childNodes
                                if n.nodeType == n.TEXT_NODE
                            )
                repeat = 1
                try:
                    repeat = int(cell.getAttribute("numbercolumnsrepeated") or 1)
                except Exception:
                    pass
                for _ in range(repeat):
                    vals.append(txt)
            if r == 1:
                headers = [str(x) for x in vals]
            else:
                data.append({"header_row": r, "values": vals})
        break
    if not headers:
        raise RuntimeError("ODS 文件未找到表格数据")
    return headers, data


def _load_html(bom_path: str) -> tuple[list[str], list[dict]]:
    import html.parser

    class _TableParser(html.parser.HTMLParser):
        def __init__(self):
            super().__init__()
            self.in_table = False
            self.in_cell = False
            self.cur_cell = []
            self.cur_row = []
            self.tables = []

        def handle_starttag(self, tag, attrs):
            t = tag.lower()
            if t == "table":
                self.in_table = True
                self.tables.append([])
            elif t == "tr" and self.in_table:
                self.cur_row = []
            elif t in ("td", "th") and self.in_table:
                self.in_cell = True
                self.cur_cell = []
            elif t in ("br", "p") and self.in_cell:
                self.cur_cell.append(" ")

        def handle_data(self, data):
            if self.in_cell:
                self.cur_cell.append(data)

        def handle_endtag(self, tag):
            t = tag.lower()
            if t in ("td", "th") and self.in_cell:
                self.cur_row.append("".join(self.cur_cell).strip())
                self.in_cell = False
            elif t == "tr":
                if self.cur_row:
                    self.tables[-1].append(self.cur_row)
            elif t == "table":
                self.in_table = False

    with open(bom_path, "rb") as f:
        raw = f.read()
    text = None
    for cand in CSV_ENCODINGS:
        try:
            text = raw.decode(cand)
            break
        except Exception:
            continue
    parser = _TableParser()
    parser.feed(text or raw.decode("latin-1", errors="replace"))
    if not parser.tables:
        raise RuntimeError("HTML 文件中未找到 <table>")
    table = parser.tables[0]
    if not table:
        raise RuntimeError("HTML 表格为空")
    headers = [str(x) for x in table[0]]
    data = [
        {"header_row": i + 1, "values": row}
        for i, row in enumerate(table[1:])
    ]
    return headers, data


def load_bom(bom_path: str) -> tuple[list[str], list[dict]]:
    """Read BOM file. Returns (headers, rows).

    Each row: {"header_row": int, "values": [str, ...]}
    """
    ext = os.path.splitext(str(bom_path).lower())[1]
    if ext == ".xls":
        return _load_xls(bom_path)
    if ext == ".xlsb":
        return _load_xlsb(bom_path)
    if ext == ".ods":
        return _load_ods(bom_path)
    if ext in (".html", ".htm"):
        return _load_html(bom_path)
    if ext in (".csv", ".tsv", ".txt", ".tab", ".text"):
        rows, _ = _csv_content_rows(bom_path)
        rows = [
            [str(v) if not (v is None or isinstance(v, float) and math.isnan(v)) else ""
             for v in row]
            for row in rows
        ]
        hi, _ = _find_header_row(rows)
        headers = [str(x).strip() for x in rows[hi]]
        width = len(headers)
        data = []
        for i, row in enumerate(rows[hi + 1:], start=hi + 1):
            row = list(row[:width]) + [""] * max(0, width - len(row))
            if any(str(x).strip() for x in row):
                data.append({"header_row": i + 1, "values": row})
        return headers, data

    import openpyxl
    wb = openpyxl.load_workbook(bom_path, data_only=True)
    ws = wb.active
    allrows = []
    for r in range(1, ws.max_row + 1):
        row = []
        for c in range(1, ws.max_column + 1):
            v = ws.cell(row=r, column=c).value
            row.append(v if v is not None else "")
        allrows.append(row)
    hi, _ = _find_header_row(allrows)
    headers = [str(x).strip() for x in allrows[hi]]
    width = len(headers)
    data = []
    for r, row in enumerate(allrows[hi + 1:], start=hi + 2):
        row = list(row[:width]) + [""] * max(0, width - len(row))
        if any(str(x).strip() for x in row):
            data.append({"header_row": r, "values": row})
    return headers, data


def load_excel_table(path_a: str, path_b: str) -> tuple:
    """Load two Excel files, return (ha, da, hb, db)."""
    ha, da = load_bom(path_a)
    hb, db = load_bom(path_b)
    return ha, da, hb, db
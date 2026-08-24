"""Designator extraction from BOM text and PDF decoded words.

Unified logic: both BOM and PDF use the same parser for range expansion.
"""

import re
from collections import defaultdict

from ..config import DESIGNATOR_RE, PREFIXES_PARTS


def _split_designator_word(up: str) -> list[str]:
    """Split a word into designators. Handles: C2,C7, C300-C302, R2180R0201, etc."""
    parts = re.split(r"[,;\s\u3001]+", up)
    out = []
    for part in parts:
        if DESIGNATOR_RE.match(part) and part[0] in PREFIXES_PARTS:
            out.append(part)
            continue
        m = re.fullmatch(
            r"([A-Za-z]{1,4})(\d{1,4})-([A-Za-z]{1,4})(\d{1,4})", part
        )
        if m:
            p1, n1s, p2, n2s = m.groups()
            if p1 == p2:
                try:
                    n1, n2 = int(n1s), int(n2s)
                    if 0 <= n1 <= n2 <= 99999 and n2 - n1 <= 200:
                        out += [p1 + str(n) for n in range(n1, n2 + 1)]
                        continue
                except ValueError:
                    pass
        run = re.match(
            r"([A-Za-z]{1,4}\d{1,4})*[A-Za-z]{1,4}\d{1,4}", part
        )
        if run:
            seg = run.group(0)
            for fm in re.finditer(r"([A-Za-z]{1,4})(\d{1,4})", seg, re.I):
                pfx, num = fm.group(1), fm.group(2)
                if pfx.upper()[0] in PREFIXES_PARTS and DESIGNATOR_RE.match(
                    pfx + num
                ):
                    out.append((pfx + num).upper())
    return out


def split_designators_text(text: str) -> list[str]:
    """Split BOM cell text into designator list. Supports R1,R2 / R1-R5 / compact forms."""
    raw = str(text or "").replace("，", ",").replace("；", ";")
    parts = re.split(r"[,;\s\u3001]+", raw)
    out = []
    for part in parts:
        part = part.strip().upper()
        if not part:
            continue
        parsed = _split_designator_word(part)
        if parsed:
            out.extend(x.upper() for x in parsed)
            continue
        if DESIGNATOR_RE.fullmatch(part) and part[0] in PREFIXES_PARTS:
            out.append(part)
    return list(dict.fromkeys(out))


def extract_designators_from_pdf(pages_words: dict) -> tuple[dict, list]:
    """Extract designators from PDF decoded words.

    Returns (tokens: {designator: [page_numbers]}, all_words: list)
    """
    tokens = defaultdict(list)
    all_words = []
    for pno, pwords in pages_words.items():
        open_range = None
        for y, words in pwords:
            for w, x0, x1, top in words:
                up = w.upper().strip()
                if not up:
                    continue
                all_words.append((pno, y, up, x0, x1, top))
                merged = False
                if open_range:
                    pfix, n1 = open_range
                    mo = re.match(r"((?:[A-Za-z]{1,4})?)(\d{1,4})", up)
                    n2 = None
                    if mo:
                        n2 = int(mo.group(2))
                        mergable = mo.group(1) == "" or mo.group(1) == pfix
                    else:
                        mergable = False
                    if (
                        n2 is not None
                        and mergable
                        and 0 <= n1 <= n2 <= 99999
                        and n2 - n1 <= 200
                    ):
                        for n in range(n1, n2 + 1):
                            tokens[pfix + str(n)].append(pno)
                        merged = True
                    open_range = None
                if merged:
                    continue
                for d in _split_designator_word(up):
                    tokens[d].append(pno)
                mm = re.search(r"([A-Za-z]{1,4}\d{1,4})-+\s*$", up)
                if mm:
                    base = re.fullmatch(r"([A-Za-z]{1,4})(\d+)", mm.group(1))
                    if base:
                        open_range = (base.group(1), int(base.group(2)))
    return tokens, all_words


def build_pdf_designator_annotations(pages_words: dict) -> dict:
    """Find nearby annotation words for each PDF designator.

    Returns {designator: {"pages": [pages], "near": [nearby_words]}}
    """
    tokens, _ = extract_designators_from_pdf(pages_words)
    page_words = {}
    for pno, pwords in pages_words.items():
        plist = []
        for y, words in pwords:
            for w, x0, x1, top in words:
                if re.match(r"^[\w.+/@-]{2,}$", w):
                    plist.append({"y": y, "x": x0, "w": w.upper()})
        page_words[pno] = plist

    out = {}
    for des, pages in tokens.items():
        des_pos = None
        for pno, pwords in pages_words.items():
            if pno not in pages:
                continue
            for y, words in pwords:
                for w, x0, x1, top in words:
                    if w.upper() == des:
                        des_pos = (pno, y, x0)
                        break
                if des_pos:
                    break
            if des_pos:
                break
        near = []
        if des_pos:
            pno, dy, dx = des_pos
            for it in page_words.get(pno, []):
                if (
                    abs(it["y"] - dy) <= 120
                    and abs(it["x"] - dx) <= 260
                    and it["w"] != des
                ):
                    near.append(it["w"])
        out[des] = {"pages": pages, "near": sorted(set(near))[:15]}
    return out


def extract_designators_from_excel(bom_path: str, col_name: str = None) -> tuple:
    """Extract all designators from an Excel BOM."""
    from ..readers.bom_reader import load_bom, _designator_col_name

    headers, data = load_bom(bom_path)
    col = col_name or (_designator_col_name(headers) or headers[0])
    if col not in headers:
        col = headers[0]
    idx = headers.index(col)
    mapping = {}
    for row in data:
        raw = row["values"][idx]
        for d in split_designators_text(raw):
            if re.match(r"^[A-Za-z]{1,4}\d{1,4}$", d):
                mapping.setdefault(d, row["header_row"])
    return set(mapping.keys()), mapping, col
"""PDF schematic text decoder. Core value: reverse-engineers Type3 font encoding.

This is the most valuable part of the original codebase.
Kept intact with minimal refactoring - only wrapping in proper module structure.
"""

import re
from collections import defaultdict

from ..config import DESIGNATOR_RE, PREFIXES_PARTS


def _build_code2glyph(fonts: dict) -> dict:
    """Parse Type3 font Differences array into {font_name: {code: glyph_name}}."""
    code2glyph = {}
    for fn in fonts.keys():
        f = fonts[fn]
        enc = f.get("/Encoding")
        diffs = None
        if enc is not None and hasattr(enc, "get"):
            diffs = enc.get("/Differences", None)
        if diffs is None:
            continue
        try:
            arr = [str(x) for x in diffs]
        except Exception:
            continue
        gmap = {}
        start = None
        names = []

        def flush():
            nonlocal gmap, start, names
            if start is None:
                return
            cur = start
            for g in names:
                gmap[cur] = g
                cur += 1
            start = None
            names = []

        for x in arr:
            if re.fullmatch(r"-?\d+", x):
                flush()
                start = int(x)
            else:
                names.append(x)
        flush()
        code2glyph[str(fn)] = gmap
    return code2glyph


def _build_code2unicode(fonts: dict) -> dict:
    """Parse Type0/Type1/TrueType /ToUnicode CMap.

    Returns {font_name: {"map": {code: unicode}, "bytes": N}}
    """
    out = {}
    for fn in fonts.keys():
        f = fonts[fn]
        tu = f.get("/ToUnicode", None)
        if tu is None:
            continue
        try:
            raw = tu.read_bytes().decode("latin-1", errors="replace")
        except Exception:
            try:
                raw = bytes(tu).decode("latin-1", errors="replace")
            except Exception:
                continue
        m = {}
        nbytes = 2
        cm = re.search(
            r"begincodespacerange\s*<(?:0{0,2}[0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", raw
        )
        if cm:
            nbytes = 1 if len(cm.group(1)) <= 2 else 2
        for cm in re.finditer(r"beginbfchar\s*(.*?)\s*endbfchar", raw, re.S):
            for pair in re.findall(
                r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", cm.group(1)
            ):
                code, uni = int(pair[0], 16), _utf16be_to_str(pair[1])
                m[code] = uni
        for cm in re.finditer(r"beginbfrange\s*(.*?)\s*endbfrange", raw, re.S):
            block = cm.group(1)
            for trio in re.finditer(
                r"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", block
            ):
                lo, hi = int(trio.group(1), 16), int(trio.group(2), 16)
                dst = _utf16be_to_str(trio.group(3))
                if len(dst) == 1:
                    for code in range(lo, hi + 1):
                        m[code] = dst
                        dst = chr(ord(dst) + 1)
                else:
                    for code in range(lo, hi + 1):
                        m[code] = dst
        if m:
            out[str(fn)] = {"map": m, "bytes": nbytes}
    return out


def _utf16be_to_str(hexstr: str) -> str:
    h = hexstr.strip()
    try:
        if len(h) == 4:
            return chr(int(h, 16))
        b = bytes.fromhex(h)
        try:
            return b.decode("utf-16-be")
        except Exception:
            pass
        try:
            return b.decode("latin-1")
        except Exception:
            return ""
    except Exception:
        return ""


def _append_c2u(out: list, umap_obj: dict, b: bytes, tm: list, cur_size: float):
    umap, nbytes = umap_obj.get("map", {}), umap_obj.get("bytes", 2)
    i = 0
    n = len(b)
    while i < n:
        code = b[i]
        if nbytes == 2 and i + 1 < n:
            code = (b[i] << 8) | b[i + 1]
        ch = umap.get(code)
        if ch is None and nbytes == 2:
            ch = umap.get(b[i])
        if ch is None:
            ch = "?"
        for c in ch:
            out.append((c, tm[4], tm[5], cur_size))
        i += nbytes


def _append_char(out: list, gmap: dict, byte: int, tm: list, cur_size: float):
    glyph = gmap.get(byte)
    m = re.match(r"/g(\d+)$", glyph or "")
    num = int(m.group(1)) if m else None
    ch = chr(num + 29) if num is not None and 0 < num + 29 <= 0x10FFFF else "?"
    out.append((ch, tm[4], tm[5], cur_size))


def _get_char_boxes(pdf_path: str, pageno: int) -> list | None:
    """Get precise character bounding boxes from pdfplumber."""
    try:
        import pdfplumber
        with pdfplumber.open(pdf_path) as p:
            return [
                (c["x0"], c["top"], c["x1"], c["bottom"])
                for c in p.pages[pageno].chars
            ]
    except Exception:
        return None


def decode_page(pdf, pageno: int) -> list:
    """Decode one PDF page: returns [(char, x, y, font_size)]."""
    from pikepdf import parse_content_stream

    page = pdf.pages[pageno]
    c2g = _build_code2glyph(page["/Resources"]["/Font"])
    c2u = _build_code2unicode(page["/Resources"]["/Font"])
    ops = list(parse_content_stream(page))
    tm = [1, 0, 0, 1, 0, 0]
    tl = 0.0
    cur_font = None
    cur_size = 1.0
    out = []
    for op in ops:
        name = str(op.operator)
        ops_ = list(op.operands)
        if name == "BT":
            tm = [1, 0, 0, 1, 0, 0]
        elif name == "Tf":
            cur_font = str(ops_[0])
            try:
                cur_size = float(ops_[1])
            except Exception:
                pass
        elif name == "Tm":
            tm = [float(x) for x in ops_[:6]]
        elif name == "Td":
            tx, ty = float(ops_[0]), float(ops_[1])
            a, b, c, d, e, f = tm
            tm = [a, b, c, d, e + tx * a + ty * c, f + tx * b + ty * d]
        elif name == "T*":
            a, b, c, d, e, f = tm
            tm = [a, b, c, d, e, f + tl * d]
        elif name == "TL":
            tl = float(ops_[0])
        elif name in ("Tj", "'"):
            b = bytes(ops_[0]) if hasattr(ops_[0], "__bytes__") else b""
            if cur_font in c2u:
                _append_c2u(out, c2u[cur_font], b, tm, cur_size)
            else:
                gmap = c2g.get(cur_font, {})
                for byte in b:
                    _append_char(out, gmap, byte, tm, cur_size)
        elif name == "TJ":
            for item in ops_[0]:
                if hasattr(item, "__bytes__"):
                    b = bytes(item)
                    if cur_font in c2u:
                        _append_c2u(out, c2u[cur_font], b, tm, cur_size)
                    else:
                        gmap = c2g.get(cur_font, {})
                        for byte in b:
                            _append_char(out, gmap, byte, tm, cur_size)
    return out


def decode_pdf_blocks(pdf_path: str) -> tuple[dict, dict, int]:
    """Decode all PDF pages. Returns (pages_words, pages_text, page_count)."""
    import pikepdf

    pdf = pikepdf.open(pdf_path)
    pages_words = {}
    pages_text = {}
    page_count = len(pdf.pages)

    for pno in range(page_count):
        chars = decode_page(pdf, pno)
        boxes = _get_char_boxes(pdf_path, pno)
        if boxes and len(boxes) == len(chars):
            merged = [
                (ch, y, sz, x0, x1, top)
                for (ch, x, y, sz), (x0, top, x1, bottom) in zip(chars, boxes)
            ]
        else:
            merged = [(ch, y, sz, x, x, y) for (ch, x, y, sz) in chars]

        lines = defaultdict(list)
        for item in merged:
            ch, y, sz, x0, x1, top = item
            key = round(top, 7)
            lines[key].append((ch, x0, x1, sz, top))

        page_words = []
        page_text = []
        for yk in sorted(lines.keys()):
            seq = sorted(lines[yk], key=lambda t: t[1])
            words = []
            cur = ""
            cx0 = None
            prev_x1 = None
            line_text = ""
            gap_sz = None
            for ch, x0, x1, sz, top in seq:
                line_text += ch
                if cur == "":
                    cur = ch
                    cx0 = x0
                    gap_sz = max(0.5, sz * 0.30)
                else:
                    if x0 - prev_x1 > gap_sz:
                        words.append((cur, cx0, prev_x1, top))
                        cur = ch
                        cx0 = x0
                    else:
                        cur += ch
                    gap_sz = max(0.5, sz * 0.30)
                prev_x1 = x1
            if cur:
                words.append((cur, cx0, prev_x1, top))
            page_words.append((yk, words))
            page_text.append((yk, line_text))
        pages_words[pno + 1] = page_words
        pages_text[pno + 1] = page_text

    pdf.close()
    return pages_words, pages_text, page_count
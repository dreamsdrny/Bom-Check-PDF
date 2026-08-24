"""MPN extraction from PDF decoded words."""

import re
from collections import defaultdict

from ..config import DESIGNATOR_RE, PREFIXES_PARTS
from .designator import _split_designator_word


def _extract_pdf_mpn_index(pages_words: dict) -> dict:
    """Extract {MPN: [(page, y, x)]} from PDF decoded words."""
    mpn_re = re.compile(
        r"^(?=.*[A-Za-z])(?=.*\d)[A-Za-z0-9][A-Za-z0-9.\-]{4,}$"
    )
    out = defaultdict(list)
    for pno, pwords in pages_words.items():
        for y, words in pwords:
            for w, x0, x1, top in words:
                up = w.upper().strip()
                if not up:
                    continue
                if (
                    mpn_re.match(up)
                    and up[0].isalnum()
                    and len(re.sub(r"[\-.0-9]", "", up)) >= 1
                ):
                    for cand in _mpn_candidates(up):
                        out[cand].append((pno, y, x0))
    index = {}
    for mpn, hits in out.items():
        uniq = []
        seen = set()
        for p, y, x in hits:
            if (p, x) not in seen:
                uniq.append((p, y, x))
                seen.add((p, x))
        index[mpn] = uniq
    return index


def _mpn_candidates(up: str) -> list[str]:
    """Split a word into possible MPN candidates."""
    if (
        re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.\-]{4,}", up)
        and any(c.isdigit() for c in up)
    ):
        return [up]
    cands = []
    for m in re.finditer(r"[A-Za-z][A-Za-z0-9\-]{4,}\d[A-Za-z0-9\-]*", up):
        c = m.group(0)
        if any(cd in c for cd in "0123456789") and len(c) >= 5:
            cands.append(c)
    return cands


def _linked_designators(
    pages_words: dict, pno: int, y: float, x: float, mpn: str, radius: float = 90
) -> list[str]:
    """Find designators near an MPN on the same page."""
    mpn_clean = re.sub(r"[^A-Za-z0-9]", "", (mpn or "").upper())
    cands = []
    for yy, words in pages_words.get(pno, []):
        for w, x0, x1, top in words:
            up = w.upper().strip()
            for d in _split_designator_word(up):
                if (
                    not DESIGNATOR_RE.match(d)
                    or d[0] not in PREFIXES_PARTS
                    or len(d) > 6
                ):
                    continue
                if mpn_clean and (
                    mpn_clean == d or (mpn_clean.startswith(d) and len(d) >= 4)
                ):
                    continue
                dm = max(abs(yy - y), abs(x0 - x))
                if dm <= radius:
                    cands.append((dm, d))
    cands.sort()
    seen = set()
    out = []
    for _dm, d in cands:
        if d not in seen:
            seen.add(d)
            out.append(d)
    return out
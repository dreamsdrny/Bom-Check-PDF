"""Value / quantity / text normalization for comparison.

Each normalizer is a standalone function, avoiding the
"one function handles everything" anti-pattern.
"""

import re


def normalize_key(v) -> str:
    """Normalize primary key: strip whitespace, uppercase, collapse internal whitespace."""
    return re.sub(r"\s+", "", str(v or "").strip()).upper()


def normalize_qty(v) -> float | int | None:
    """Normalize quantity: 1, 1.0, 1,000 → 1; non-integer → float."""
    s = str(v or "").strip().replace(",", "")
    if not s:
        return None
    try:
        x = float(s)
        return int(x) if x.is_integer() else x
    except (ValueError, TypeError):
        return None


def normalize_text(v) -> str:
    """Lightweight text normalization: remove spaces, dashes, dots, slashes."""
    return re.sub(r"[\s\-_./]+", "", str(v or "").strip()).upper()


def normalize_cap_value(s) -> float | str | None:
    """Normalize capacitor value to pF."""
    s = (s or "").strip().lower().replace(" ", "")
    if not s:
        return None
    s = re.sub(r"[^0-9.a-z\u00b5\u03bc]", "", s)
    if not re.search(r"\d", s):
        return s or None
    m = re.match(r"^([\d.]+)([a-z\u00b5\u03bc]*)$", s)
    if not m:
        return s
    try:
        val = float(m.group(1))
    except ValueError:
        return s
    unit = m.group(2) or "f"
    table = {
        "pf": 1, "p": 1, "nf": 1000, "n": 1000,
        "uf": 1_000_000, "μf": 1_000_000, "µf": 1_000_000,
        "u": 1_000_000, "mf": 1_000_000, "f": 1_000_000_000,
    }
    mult = 1
    for u0, mm in sorted(table.items(), key=lambda kv: -len(kv[0])):
        if unit == u0 or unit.endswith(u0):
            mult = mm
            break
    return round(val * mult, 3)


def normalize_res_value(s) -> float | str | None:
    """Normalize resistor value to ohm. Supports 4R7, 2K4, 100R, 1K5, etc."""
    s = (s or "").strip().lower().replace(" ", "")
    if not s:
        return None
    if re.match(r"^\d+[rkm][\d.]+$", s):
        m = re.match(r"^(\d+)([rkm])([\d.]+)$", s)
        base = float(m.group(1) + "." + m.group(3))
        mult = {"r": 1, "k": 1000, "m": 1_000_000}[m.group(2)]
        return round(base * mult, 3)
    if re.match(r"^[\d.]+[rkm]$", s):
        m = re.match(r"^([\d.]+)([rkm])$", s)
        mult = {"r": 1, "k": 1000, "m": 1_000_000}[m.group(2)]
        return round(float(m.group(1)) * mult, 3)
    if "ohm" in s or "Ω" in s or "ω" in s:
        m = re.match(
            r"^([\d.]+)\s*([a-z\u03a9\u03c9]*)$",
            s.replace("mω", "m").replace("mΩ", "m").replace("kΩ", "k"),
        )
        if not m:
            return s
        num, unit = m.groups()
        table = {
            "pohm": 1e-6, "mohm": 1e-3, "ohm": 1, "Ω": 1, "ω": 1,
            "kohm": 1000, "kω": 1000, "mω": 1_000_000, "mohm": 1e-3,
        }
        mult = 1
        for u0, mm in sorted(table.items(), key=lambda kv: -len(kv[0])):
            if unit.endswith(u0):
                mult = mm
                break
        try:
            return round(float(num) * mult, 3)
        except ValueError:
            return s
    m = re.match(r"^([\d.]+)\s*([km]?)$", s)
    if m:
        num, unit = m.groups()
        try:
            mult = {"": 1, "k": 1000, "m": 1_000_000}.get(unit, 1)
            return round(float(num) * mult, 3)
        except ValueError:
            return s
    return s


def normalize_value(v) -> float | str | None:
    """Smart value normalization: capacitor → pF, resistor → ohm, pure number → float."""
    s = str(v or "").strip()
    if not s:
        return None
    s2 = s.lower()
    if any(u in s2 for u in ["uf", "μf", "µf", "nf", "pf", "mf"]):
        r = normalize_cap_value(s)
        if r is not None:
            return r
    if (
        any(u in s2 for u in ["ohm", "Ω", "ω", "k", "r"])
        and "f" not in s2
        and "uf" not in s2
        and "µ" not in s2
        and "μ" not in s2
    ):
        r = normalize_res_value(s)
        if r is not None and r != s:
            return r
    if re.fullmatch(r"[0-9.]+", s):
        try:
            return float(s)
        except ValueError:
            return s
    return s


def footprint_family(d) -> str:
    """Normalize footprint to family: CAPC1005 → 0402, etc."""
    s = str(d or "").strip().lower().replace(" ", "")
    fam = {
        "capc1005": "0402", "capc0603": "0201", "capc1608": "0603",
        "capc2012": "0805", "capc3216": "1206",
        "resc1005": "0402", "resc0603": "0201", "resc1608": "0603",
        "resc2013": "0805", "indc1005": "0402", "res": "",
        "0402": "0402", "0201": "0201", "0603": "0603",
        "0805": "0805", "1206": "1206",
    }
    for prefix, f in fam.items():
        if s.startswith(prefix):
            return f
    return s or ""
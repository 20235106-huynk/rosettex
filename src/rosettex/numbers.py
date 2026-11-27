from decimal import Decimal
import re
import unicodedata

_PH = re.compile(r"⟦/?[a-z]\d+⟧")
_FULLWIDTH_DIGITS = {0xFF10 + i: 0x30 + i for i in range(10)}  # CJK sources write １２３


def extract(text: str, locale: dict) -> list[Decimal]:
    text = unicodedata.normalize("NFC", text).translate(_FULLWIDTH_DIGITS)
    text = _PH.sub(" ; ", text)
    dec = re.escape(locale.get("decimal", "."))
    group_chars = set(locale.get("group", []))
    groups = "".join(re.escape(g) for g in group_chars)
    if groups:
        # Support both standard 3-digit grouping (100,000) and Indian grouping (1,00,000)
        pattern = (
            rf"(?:[0-9]{{1,2}}(?:[{groups}][0-9]{{2}})+[{groups}][0-9]{{3}}"
            rf"|[0-9]{{1,3}}(?:[{groups}][0-9]{{3}})+)"
            rf"(?:{dec}[0-9]+)?(?![0-9])|[0-9]+(?:{dec}[0-9]+)?"
        )
    else:
        pattern = rf"[0-9]+(?:{dec}[0-9]+)?"
    if locale.get("decimal", ".") != "." and "." in group_chars:
        # a decimal-comma locale: "2.4" (lemme 2.4, version 2.4) is not a 3-digit group, read it as written
        pattern = pattern.replace("(?![0-9])|", r"(?![0-9])|(?P<dot>[0-9]+(?:\.[0-9]+)+)(?![0-9])|", 1)
    out = []
    for m in re.finditer(pattern, text):
        s = m.group(0)
        if m.groupdict().get("dot"):  # 2.4 → 2.4; 2.4.1 → 2.4, 1 (as a point-decimal locale reads it)
            out += [Decimal(x) for x in re.findall(r"[0-9]+(?:\.[0-9]+)?", s)]
            continue
        for g in group_chars:
            if g != locale.get("decimal", "."):
                s = s.replace(g, "")
        out.append(Decimal(s.replace(locale.get("decimal", "."), ".")))
    return out


def extract_months(text: str, lang: dict) -> list[Decimal]:
    months = lang.get("months")
    if not months:
        return []
    text = _PH.sub(" ", text)
    lookup: dict[str, int] = {}
    for k, v in months.items():
        lookup[k] = v
        lookup[k.upper()] = v
    keys = sorted(lookup, key=len, reverse=True)
    pat = re.compile(r"(?<!\w)(?:" + "|".join(re.escape(k) for k in keys) + r")(?!\w)")
    return [Decimal(lookup[m.group(0)]) for m in pat.finditer(text)]

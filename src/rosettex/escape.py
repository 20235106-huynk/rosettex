import re
import unicodedata

_QUOTES = [("``", "“"), ("''", "”"), ("`", "‘")]
_ENCODE = {
    "\\": "\\textbackslash{}",
    "%": "\\%",
    "&": "\\&",
    "#": "\\#",
    "_": "\\_",
    "$": "\\$",
    "{": "\\{",
    "}": "\\}",
    "^": "\\^{}",
}
_PH = re.compile(r"⟦/?[a-z]\d+⟧")
_COMMENT = re.compile(r"(?<!\\)((?:\\\\)*)%[^\n]*(?:\r?\n[ \t]*)?")
# Han, Kana, CJK punctuation and full-width forms (not Hangul: Korean has word spaces). A line break
# between two of these is dropped by TeX's CJK packages, so it decodes to nothing (V report D3).
_CJK = r"[\u2E80-\u2FDF\u3000-\u303F\u3040-\u30FF\u31F0-\u31FF\u3400-\u4DBF\u4E00-\u9FFF\uF900-\uFAFF\uFF00-\uFFEF\U00020000-\U0003134F]"
_CJK_BREAK = re.compile(rf"(?<={_CJK})[ \t]*\r?\n[ \t]*(?={_CJK})")
_CJK_JOIN = re.compile(rf"(?<={_CJK})\s*\n\s*(?={_CJK})")
_MARKS = {"'": "\u0301", "`": "\u0300", "^": "\u0302", '"': "\u0308", "~": "\u0303", "=": "\u0304",
          ".": "\u0307", "u": "\u0306", "v": "\u030c", "H": "\u030b", "c": "\u0327", "d": "\u0323",
          "b": "\u0331", "k": "\u0328", "r": "\u030a"}
_LETTERS = {"ss": "ß", "ae": "æ", "AE": "Æ", "oe": "œ", "OE": "Œ", "aa": "å", "AA": "Å", "o": "ø", "O": "Ø",
            "l": "ł", "L": "Ł", "i": "ı", "j": "ȷ"}
_BASE = r"(?:\\([ij])(?![A-Za-z])\s*|([A-Za-z]))"
_ACC = (rf"\\(?:([\'`^\"~=.])\s*(?:\{{\s*{_BASE}\s*\}}|{_BASE})"
        rf"|([uvHcdbkr])(?:\s*\{{\s*{_BASE}\s*\}}|\s+([A-Za-z])))")
_ACCENT = re.compile(rf"\{{{_ACC}\}}|{_ACC}")
# a bare control word eats the spaces after it (TeX): \AA kesson → Åkesson
_LETTER = re.compile(r"\{\\(ss|ae|AE|oe|OE|aa|AA|o|O|l|L)\}|\\(ss|ae|AE|oe|OE|aa|AA|o|O|l|L)(?![A-Za-z])(?:\{\}|[ \t]*\n?[ \t]*)")


def _accent(m: re.Match) -> str:
    g = m.groups()  # marks at 0, 5 (symbol / letter accent, braced form) and 9, 14 (bare form); the rest are bases
    mark = next(g[i] for i in (0, 5, 9, 14) if g[i])
    base = next(x for i, x in enumerate(g) if x and i not in (0, 5, 9, 14))
    return unicodedata.normalize("NFC", base + _MARKS[mark])


def decode_accents(s: str) -> str:
    """Text accent macros (\\'{e}, \\'e, {\\"a}, \\c{c}, \\v s, \\'{\\i}) and \\ss, \\o … → Unicode (spec §7.5)."""
    if "\\" not in s:
        return s
    s = _ACCENT.sub(_accent, s)
    return _LETTER.sub(lambda m: _LETTERS[m.group(1) or m.group(2)], s)


def decode_chars(s: str, keep_newlines: bool = False) -> str:
    """keep_newlines: a whitespace run with a line break becomes one newline (joined text is then passed through
    join_cjk, so a break between CJK characters split over several nodes, e.g. around a comment, is dropped)."""
    s = s.replace("\\-", "")
    for a, b in _QUOTES:
        s = s.replace(a, b)
    s = s.replace("---", "—").replace("--", "–")
    s = _CJK_BREAK.sub("", s)
    if keep_newlines:
        return re.sub(r"\s+", lambda m: "\n" if "\n" in m.group(0) else " ", s)
    return re.sub(r"\s+", " ", s)


def join_cjk(s: str) -> str:
    """Drop whitespace with a line break between two CJK characters, then collapse whitespace."""
    return re.sub(r"\s+", " ", _CJK_JOIN.sub("", s))


def encode_text(s: str) -> str:
    out = []
    pos = 0
    for m in _PH.finditer(s):  # never encode inside placeholders
        out.append("".join(_ENCODE.get(c, c) for c in s[pos:m.start()]))
        out.append(m.group(0))
        pos = m.end()
    out.append("".join(_ENCODE.get(c, c) for c in s[pos:]))
    return "".join(out)


def normalize_raw(raw: str) -> str:
    """Canonical form used to compare a rebuild with its source span."""
    raw = decode_accents(_COMMENT.sub(r"\1", raw))
    raw = _CJK_JOIN.sub("", raw)
    for a, b in _QUOTES:
        raw = raw.replace(a, b)
    raw = raw.replace("---", "—").replace("--", "–").replace("\\-", "")
    return re.sub(r"\s+", " ", raw).strip()

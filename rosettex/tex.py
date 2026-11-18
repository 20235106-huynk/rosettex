"""Escape plain text for LaTeX (XeLaTeX + unicode-math)."""
import re
import unicodedata

from .checks import in_blocks

_ESC = {"\\": r"\textbackslash{}", "{": r"\{", "}": r"\}", "#": r"\#", "$": r"\$", "%": r"\%",
        "&": r"\&", "_": r"\_", "~": r"\textasciitilde{}", "^": r"\textasciicircum{}",
        # spaces some fonts lack (Termes has no U+202F): TeX's own spacing instead of a glyph ("1 000" in French)
        "\u00a0": "~", "\u202f": r"\,", "\u2009": r"\,"}


def _mathy(c: str) -> bool:
    o = ord(c)
    return 0x0370 <= o <= 0x03FF or 0x2190 <= o <= 0x22FF or 0x2A00 <= o <= 0x2AFF


def escape_latex(s: str) -> str:
    # Greek letters and math symbols go to math mode: the math font has all the glyphs, the text font may not.
    return "".join(_ESC.get(c) or (r"\ensuremath{" + c + "}" if _mathy(c) else c) for c in s)


_LATIN = "0-9A-Za-z\u00C0-\u024F"


_NO_BREAK_AFTER = set("\u0e40\u0e41\u0e42\u0e43\u0e44\u1039")  # Thai leading vowels, Myanmar stacking sign
_NO_BREAK_BEFORE = set("\u0e30\u0e32\u0e33\u0e45")  # Thai vowels written after the consonant they belong to
_CLOSES_SYLLABLE = set("\u1039\u103a")  # a Myanmar consonant carrying asat/virama ends the syllable before it


def _cls(blocks) -> str:
    return "".join(f"\\U{a:08X}-\\U{b:08X}" for a, b in blocks)


# Noto Naskh Arabic has no brackets, quotes or most ASCII punctuation. In rtl text they come from the Latin font
# (\rxltr). Brackets and quotes are written mirrored: the Latin glyph is not mirrored by the bidi engine, so a logical
# "(" (drawn at the right, opening) must use the ")" glyph.
_MIRROR = {"(": ")", ")": "(", "[": "]", "]": "[", "<": ">", ">": "<", "{": "}", "}": "{",
           "\u201c": "\u201d", "\u201d": "\u201c", "\u2018": "\u2019", "\u2019": "\u2018", "\u00ab": "\u00bb", "\u00bb": "\u00ab"}


_ARABIC_FONT_OK = set(".,:!")  # the only ASCII punctuation Noto Naskh Arabic has


def _neutrals(seg: str, blocks=()) -> str:
    """rtl text outside Latin runs: the Arabic font keeps its own script, spaces and . , : !; every other character
    (brackets, quotes, dashes, %, µ, ±, …) is set by the Latin font in \\rxltr{}, brackets and quotes mirrored."""
    out, plain = [], []
    for c in seg:
        if c.isspace() or c in _ARABIC_FONT_OK or (blocks and in_blocks(c, blocks)):
            plain.append(c)
            continue
        out.append(escape_latex("".join(plain)))
        plain = []
        out.append("\\rxltr{%s}" % escape_latex(_MIRROR.get(c, c)))
    out.append(escape_latex("".join(plain)))
    return "".join(out)


def wrap_runs(s: str, prof: dict) -> str:
    """Escape s. 'runs': target-script runs → \\rxs{}; 'rtl': Latin/digit runs → \\rxltr{}; 'zwsp': U+200B → break point."""
    mode, blocks = prof["layout"], prof["_blocks"]
    if mode not in ("runs", "rtl") or not blocks:
        return escape_latex(s)
    c = _cls(blocks)
    if mode == "runs":
        pat, macro = re.compile(f"[{c}]+(?:[ \\t]+[{c}]+)*"), "\\rxs"
    else:  # interior limited to letters, digits, spaces and . , : / - _ so parentheses stay in the RTL flow
        pat, macro = re.compile(f"[{_LATIN}](?:[{_LATIN} .,:/_\\-]*[{_LATIN}])?%?"), "\\rxltr"
    zw = prof["linebreak"] == "zwsp"

    def run(t: str) -> str:
        if not zw:
            return escape_latex(t)
        # U+200B is the word break; between clusters a costly emergency break (\\rxbrk) keeps an unspaced word from
        # overflowing a narrow table column. Never inside a cluster: not before a combining mark, not after a Thai
        # leading vowel, not around the Myanmar stacking sign.
        out = []
        for i, c in enumerate(t):
            if c == "\u200b":
                out.append("\\hspace{0pt plus 1pt}")
                continue
            prev, nxt = (t[i - 1] if i else ""), (t[i + 1] if i + 1 < len(t) else "")
            if (i and prev != "\u200b" and unicodedata.category(c) == "Lo" and c not in _NO_BREAK_BEFORE
                    and nxt not in _CLOSES_SYLLABLE and prev not in _NO_BREAK_AFTER
                    and unicodedata.category(prev) in ("Lo", "Mn", "Mc")):
                out.append("\\rxbrk{}")
            out.append(escape_latex(c))
        return "".join(out)

    rtl = mode == "rtl"
    out, pos = [], 0
    for m in pat.finditer(s):
        out += [_neutrals(s[pos:m.start()], blocks) if rtl else escape_latex(s[pos:m.start()]), macro + "{" + run(m.group()) + "}"]
        pos = m.end()
    out.append(_neutrals(s[pos:], blocks) if rtl else escape_latex(s[pos:]))
    return "".join(out)

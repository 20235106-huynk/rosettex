"""Escape plain text for LaTeX (XeLaTeX + unicode-math)."""
import re

_ESC = {"\\": r"\textbackslash{}", "{": r"\{", "}": r"\}", "#": r"\#", "$": r"\$", "%": r"\%",
        "&": r"\&", "_": r"\_", "~": r"\textasciitilde{}", "^": r"\textasciicircum{}"}


def _mathy(c: str) -> bool:
    o = ord(c)
    return 0x0370 <= o <= 0x03FF or 0x2190 <= o <= 0x22FF or 0x2A00 <= o <= 0x2AFF


def escape_latex(s: str) -> str:
    # Greek letters and math symbols go to math mode: the math font has all the glyphs, the text font may not.
    return "".join(_ESC.get(c) or (r"\ensuremath{" + c + "}" if _mathy(c) else c) for c in s)


_LATIN = "0-9A-Za-z\u00C0-\u024F"


def _cls(blocks) -> str:
    return "".join(f"\\U{a:08X}-\\U{b:08X}" for a, b in blocks)


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
        e = escape_latex(t)
        return e.replace("\u200b", "\\hspace{0pt plus 1pt}") if zw else e

    out, pos = [], 0
    for m in pat.finditer(s):
        out += [escape_latex(s[pos:m.start()]), macro + "{" + run(m.group()) + "}"]
        pos = m.end()
    out.append(escape_latex(s[pos:]))
    return "".join(out)

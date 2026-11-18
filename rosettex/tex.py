"""Escape plain text for LaTeX (XeLaTeX + unicode-math)."""

_ESC = {"\\": r"\textbackslash{}", "{": r"\{", "}": r"\}", "#": r"\#", "$": r"\$", "%": r"\%",
        "&": r"\&", "_": r"\_", "~": r"\textasciitilde{}", "^": r"\textasciicircum{}"}


def _mathy(c: str) -> bool:
    o = ord(c)
    return 0x0370 <= o <= 0x03FF or 0x2190 <= o <= 0x22FF or 0x2A00 <= o <= 0x2AFF


def escape_latex(s: str) -> str:
    # Greek letters and math symbols go to math mode: the math font has all the glyphs, the text font may not.
    return "".join(_ESC.get(c) or (r"\ensuremath{" + c + "}" if _mathy(c) else c) for c in s)

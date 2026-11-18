from rosettex.extract.tables import parse_table_html, table_shape
from rosettex.tex import escape_latex

HTML = ('<table><tbody><tr><td rowspan="2">Model</td><td rowspan="2">Size</td><td colspan="2">BLEU</td></tr>'
        '<tr><td>EN-DE</td><td>EN-FR</td></tr><tr><td>ByteNet</td><td>1.2B</td><td>23.75</td><td>-</td></tr>'
        '</tbody></table>')


def test_escape_specials():
    assert escape_latex("50% & a_b #1 {x} ~^\\") == (
        r"50\% \& a\_b \#1 \{x\} \textasciitilde{}\textasciicircum{}\textbackslash{}")


def test_escape_math_like_unicode_goes_to_math_mode():
    assert escape_latex("α ≤ β ∗") == r"\ensuremath{α} \ensuremath{≤} \ensuremath{β} \ensuremath{∗}"
    assert escape_latex("Tiếng Việt – “trích”") == "Tiếng Việt – “trích”"


def test_parse_table_html_spans():
    cells = parse_table_html(HTML)
    pos = {(c["r"], c["c"]): c for c in cells}
    assert pos[(0, 0)]["rowspan"] == 2 and pos[(0, 2)]["colspan"] == 2
    assert pos[(1, 2)]["text"] == "EN-DE" and pos[(1, 3)]["text"] == "EN-FR"
    assert pos[(2, 3)]["text"] == "-"
    assert table_shape(cells) == (3, 4)


def test_parse_table_html_bad_span_defaults_to_one():
    cells = parse_table_html('<table><tr><td rowspan="x">a</td><td>b</td></tr></table>')
    assert [(c["r"], c["c"], c["rowspan"]) for c in cells] == [(0, 0, 1), (0, 1, 1)]

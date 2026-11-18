import json

from conftest import FIX, fake_vi
from rosettex.checks import load_pack
from rosettex.extract.mineru_adapter import adapt
from rosettex.extract.segment import segment
from rosettex.render.latex import Renderer

VI = load_pack("vi")


def doc_short():
    d = FIX / "mineru-4.2.3-standard"
    doc, _ = segment(adapt([(json.loads((d / "short.middle.json").read_text(encoding="utf-8")), d)], d))
    return doc


def tr_of(doc):
    return {u["id"]: fake_vi(u["src"]) for u in doc["units"]}


def test_placeholders_filled_and_front_matter():
    doc = doc_short()
    tex = Renderer(doc, tr_of(doc), VI, set(), {}).document()
    assert r"\documentclass[10pt,twocolumn]{article}" in tex and r"\twocolumn[{" in tex
    assert r"\setdefaultlanguage{vietnamese}" in tex and "texgyretermes" in tex
    assert r"\cite{ref4}" in tex and r"\eqref{eq:1}" in tex and r"\ref{tab:1}" in tex
    assert r"\begin{equation}" in tex and r"\tag{1}\label{eq:1}" in tex
    assert r"\rxlabel{2}{sec:2}" in tex and r"\bibitem{ref4}" in tex
    assert "Alice Nguyen" in tex  # author kept unchanged
    assert "References" not in tex  # the references heading is printed by thebibliography
    assert tex.index(r"\begin{table") < tex.index("RX:p1b15")  # a float goes just before the paragraph that first mentions it


def test_escape_of_translated_text():  # Review Focus 3
    doc = doc_short()
    tr = tr_of(doc)
    tr["p1b7"] = "50% & a_b #1 {x} ~^\\ α ∗ ⟦c1⟧"
    tex = Renderer(doc, tr, VI, set(), {}).document()
    assert r"50\% \& a\_b \#1 \{x\} \textasciitilde{}\textasciicircum{}\textbackslash{} \ensuremath{α} \ensuremath{∗} \cite{ref4}" in tex


def test_table_spans():
    doc = doc_short()
    t = next(b for b in doc["blocks"] if b["id"] == "p1b17")
    tab = Renderer(doc, tr_of(doc), VI, set(), {}).tabular(t)
    assert r"\multirow{2}{*}" in tab and r"\multicolumn{2}{c}" in tab and "23.75" in tab
    assert tab.count(r" \\") == 6


def test_fallbacks():
    doc = doc_short()
    fb = {"p1b9:m1", "p1b10", "p1b17", "p1b18"}
    tex = Renderer(doc, tr_of(doc), VI, fb, {}).document()
    assert r"\fbox{\ttfamily\small x = (x\_\{1\},\textbackslash{}dots ,x\_\{n\})}" in tex
    assert "công thức/bảng chưa dựng được" in tex and r"\rxlabel{1}{eq:1}" in tex
    assert "thiếu ảnh" in tex


def test_figure_uses_copied_asset():
    doc = doc_short()
    tex = Renderer(doc, tr_of(doc), VI, set(), {"p1b18": "assets/p1b18.jpg"}).document()
    assert r"\includegraphics[width=\linewidth]{assets/p1b18.jpg}" in tex


def test_probe_isolates_every_formula():
    doc = doc_short()
    probe = Renderer(doc, tr_of(doc), VI, {"p1b9:m2"}, {}).probe()
    assert "RX:p1b9:m1" in probe and "RX:p1b9:m2" not in probe and "RX:p1b10" in probe and "RX:p1b17" in probe
    assert "twocolumn" not in probe


def test_fallback_block_text_is_plain_single_line():
    doc = doc_short()
    ids = {u["id"] for u in doc["units"]}
    h = next(b for b in doc["blocks"] if b["kind"] == "heading" and b["id"] in ids)
    tr = {h["id"]: "Giới thiệu\n\nphương pháp"}
    assert "\n\n" in Renderer(doc, tr, VI, set(), {}).heading(h)
    out = Renderer(doc, tr, VI, {h["id"]}, {}).heading(h)
    assert "\n" not in out and "Giới thiệu phương pháp" in out


def test_fallback_table_caption_keeps_links_but_cells_go_plain():
    doc = doc_short()
    tb = next(b for b in doc["blocks"] if b["kind"] == "table")
    cap = next(u for u in doc["units"] if u["id"] == tb["id"] + ".cap")
    tr = tr_of(doc)
    cap["ph"] = {"r1": {"src": "Table 1", "tex": r"\ref{tab:1}"}}
    tr[cap["id"]] = "Chú thích ⟦r1⟧"
    assert r"\ref{tab:1}" in Renderer(doc, tr, VI, {tb["id"]}, {}).text(cap["id"], [])


def test_ieee_authors_side_by_side():
    from rosettex.render.latex import Renderer
    mk = lambda i, x0, x1, y, t, role="authors": {"id": i, "kind": "paragraph", "role": role, "page": 1,  # noqa: E731
                                                  "bbox": [x0, y, x1, y + 0.01], "spans": [{"t": "text", "v": t}]}
    r = Renderer({"blocks": [], "units": [], "source": {"columns": 2, "page_size_pt": [[612, 792]]}}, {},
                 {"profile": {"layout": "plain", "_blocks": []}, "labels": {}}, set(), {})
    tex = r.author_block([mk("a", 0.2, 0.4, 0.22, "Ann Lee"), mk("b", 0.1, 0.5, 0.24, "Univ A"),
                          mk("c", 0.6, 0.8, 0.22, "Bob Ray"), mk("d", 0.5, 0.9, 0.24, "Univ B"),
                          mk("e", 0.55, 0.85, 0.26, "bob@b.org")])
    assert tex.count("\\begin{minipage}") == 2 and "\\hfill" in tex
    assert "Ann Lee\\\\ \\textit{Univ A}" in tex and "\\textit{Univ B}\\\\ bob@b.org" in tex

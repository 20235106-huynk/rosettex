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
    assert "Alice Nguyen" in tex  # tác giả giữ nguyên
    assert "References" not in tex  # tiêu đề mục tài liệu tham khảo do thebibliography in
    assert tex.index("RX:p1b15") < tex.index(r"\begin{table")


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

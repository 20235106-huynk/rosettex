import json
from pathlib import Path
import shutil

import pytest

from conftest import make_work, translate_all
from rosettex.checks import nfc
from rosettex.pdf import full_text
from rosettex.render.compile import culprit, render_core
from rosettex.tools import find_tex
from rosettex.workspace import read_json, write_json

needs_tex = pytest.mark.skipif(find_tex() is None, reason="needs Tectonic or XeLaTeX (scripts/get_tectonic.py)")


def test_culprit():
    assert culprit("RX:a\nRX:b\n! Undefined control sequence.\nRX:c") == "b"
    assert culprit("! LaTeX Error: x\nRX:a") is None


@needs_tex
def test_render_short_end_to_end(tmp_path):
    w = make_work(tmp_path)
    translate_all(w)
    out = tmp_path / "Kết quả" / "bài báo.vi.pdf"
    rep = render_core(w, out)
    assert out.is_file() and rep["postcheck"]["ok"], rep["postcheck"]
    assert rep["fallback"] == [] and rep["unreviewed"] is True
    text = " ".join(nfc(full_text(out)).split())  # the title wraps in a narrow column, text-layer text interleaves
    assert "việc" in text and "Alice Nguyen" in text and "Tài liệu" in text
    assert read_json(w.report)["output"] == str(out)


@needs_tex
def test_broken_formulas_fall_back_without_breaking_build(tmp_path):
    w = make_work(tmp_path)
    doc = read_json(w.document)
    eq = next(b for b in doc["blocks"] if b["kind"] == "equation")
    eq["tex"] = r"\frac{a"
    u = next(u for u in doc["units"] if any(n.startswith("m") for n in u["ph"]))
    name = next(n for n in u["ph"] if n.startswith("m") and u["ph"][n]["tex"].startswith("$"))
    u["ph"][name]["tex"] = r"$\nosuchcommand x$"
    write_json(w.document, doc)
    translate_all(w)
    tr = read_json(w.translation_path("b001", "vi"))
    first = next(iter(tr))
    tr[first] = tr[first] + " 50% & _ # ~ ^ α ∗"  # Review Focus 3: special characters in the translation
    write_json(w.translation_path("b001", "vi"), tr)
    rep = render_core(w, tmp_path / "out.pdf")
    ids = {f["id"] for f in rep["fallback"]}
    assert eq["id"] in ids and f"{u['id']}:{name}" in ids
    assert (tmp_path / "out.pdf").is_file() and rep["postcheck"]["ok"], rep["postcheck"]


def test_tex_sources_written_with_lf_newline(tmp_path, monkeypatch):
    import pathlib
    from conftest import make_work
    from rosettex.render import compile as C
    seen = {}
    real = pathlib.Path.write_text

    def spy(self, data, *a, **kw):
        if self.suffix == ".tex":
            seen[self.name] = kw.get("newline")
        return real(self, data, *a, **kw)
    monkeypatch.setattr(pathlib.Path, "write_text", spy)
    monkeypatch.setattr(C, "find_tex", lambda: ("fake", []))
    monkeypatch.setattr(C, "run_tex", lambda *a, **k: (True, "", ""))
    w = make_work(tmp_path)
    try:
        C.render_core(w, tmp_path / "o.pdf")
    except Exception:
        pass  # the fake PDF does not exist; only the way .tex is written matters
    assert seen == {"probe.tex": "\n", "main.tex": "\n"}


@needs_tex
def test_heading_with_blank_line_renders(tmp_path):
    from rosettex import translate
    w = make_work(tmp_path)
    translate_all(w)
    doc = read_json(w.document)
    h = next(u for u in doc["units"] if u["type"] == "heading" and next(b for b in doc["blocks"] if b["id"] == u["block"])["level"] > 1)
    bid = next(b for b in translate.batch_ids(w) if any(i["id"] == h["id"] for i in read_json(w.batch_path(b))["items"]))
    tr = read_json(w.translation_path(bid, "vi"))
    tr[h["id"]] = tr[h["id"]] + "\n\nphương pháp"
    f = tmp_path / "sub.json"
    f.write_text(json.dumps(tr, ensure_ascii=False), encoding="utf-8")
    assert translate.accept(w, bid, f)["ok"]
    assert "\n" not in read_json(w.translation_path(bid, "vi"))[h["id"]]
    rep = render_core(w, tmp_path / "out.pdf")
    assert (tmp_path / "out.pdf").is_file() and rep["postcheck"]["ok"], rep["postcheck"]


@needs_tex
def test_blank_line_in_heading_recovers_via_fallback(tmp_path):
    """Even if the saved translation has a blank line (old file), the fallback by block id must rescue the build."""
    w = make_work(tmp_path)
    translate_all(w)
    doc = read_json(w.document)
    h = next(u for u in doc["units"] if u["type"] == "heading" and next(b for b in doc["blocks"] if b["id"] == u["block"])["level"] > 1)
    from rosettex import translate
    bid = next(b for b in translate.batch_ids(w) if any(i["id"] == h["id"] for i in read_json(w.batch_path(b))["items"]))
    tr = read_json(w.translation_path(bid, "vi"))
    tr[h["id"]] += "\n\nphương pháp"
    write_json(w.translation_path(bid, "vi"), tr)
    rep = render_core(w, tmp_path / "out.pdf")
    assert (tmp_path / "out.pdf").is_file() and h["block"] in {f["id"] for f in rep["fallback"]}


@needs_tex
def test_fallback_table_with_citation_keeps_postcheck_ok(tmp_path):
    from rosettex.extract.segment import build_units, make_batches
    w = make_work(tmp_path)
    doc = read_json(w.document)
    tb = next(b for b in doc["blocks"] if b["kind"] == "table")
    cell = next(c for c in tb["cells"] if c["text"] and not c["text"][0].isdigit())
    cell["text"] += " [4]"
    doc["units"] = build_units(doc)
    assert any(n[0] == "c" for u in doc["units"] if u["id"] == cell["id"] for n in u["ph"])
    for p in w.batches.glob("b*.json"):
        p.unlink()
    for b in make_batches(doc["units"]):
        write_json(w.batch_path(b["batch"]), b)
    write_json(w.document, doc)
    translate_all(w)
    m = read_json(w.manifest)
    m["fallback"] = [tb["id"]]
    write_json(w.manifest, m)
    rep = render_core(w, tmp_path / "out.pdf")
    assert tb["id"] in {f["id"] for f in rep["fallback"]}
    assert rep["postcheck"]["ok"], rep["postcheck"]

from rosettex import fonts
from rosettex.fonts import FontMissing
from rosettex.render.postcheck import overfull_count, parse_missing

LOG = ("Missing character: There is no ০ (U+09E6) in font NotoSerifBengali-Regular.ttf:mode=harf;script=beng;!\n"
       "Missing character: There is no x in font foo!\n"
       "Overfull \\hbox (12.5pt too wide) in paragraph at lines 3--4\n"
       "Overfull \\hbox (2.0pt too wide) in paragraph at lines 5--6\n")


def test_parse_missing_and_overfull():
    m = parse_missing(LOG)
    assert m[0] == {"char": "০", "codepoint": "U+09E6", "font": "NotoSerifBengali-Regular.ttf"}
    assert m[1]["char"] == "x" and m[1]["font"] == "foo"
    assert overfull_count(LOG) == 1


def test_render_stops_on_damaged_package_font(tmp_path, monkeypatch):
    d = tmp_path / "fonts"
    shutil.copytree(fonts.package_dir(), d)
    (d / "NotoSerifBengali-Regular.ttf").write_bytes(b"broken")
    monkeypatch.setattr(fonts, "package_dir", lambda: d)
    w = make_work(tmp_path, lang="bn")
    translate_all(w)
    with pytest.raises(FontMissing):
        render_core(w, tmp_path / "o.pdf")
    assert not (tmp_path / "o.pdf").exists()


SAMPLES = json.loads((Path(__file__).parent / "script_samples.json").read_text(encoding="utf-8"))
XFAIL = {}  # from the Task 2 stop rules, e.g. {"ur": "<error line>"}


@needs_tex
@pytest.mark.parametrize("code", sorted(SAMPLES))
def test_render_short_in_every_script(tmp_path, code):
    from conftest import fake_script
    from rosettex.checks import in_blocks, load_pack
    if code in XFAIL:
        pytest.xfail(XFAIL[code])
    pack = load_pack(code)
    w = make_work(tmp_path, lang=code)
    translate_all(w, lambda s: fake_script(s, SAMPLES[code]))
    rep = render_core(w, tmp_path / f"out.{code}.pdf")
    pc = rep["postcheck"]
    assert pc["missing_glyphs"] == [], pc
    assert not [p for p in pc["problems"] if "dotted circle" in p or "undefined" in p], pc
    if pack["profile"]["layout"] != "rtl":  # Arabic text extraction is unreliable (S1b §3.1)
        text = full_text(tmp_path / f"out.{code}.pdf")
        blocks = pack["profile"]["_blocks"]
        if blocks:  # a real share of the letters is in the target script (English authors and references remain)
            letters = [c for c in text if c.isalpha()]
            share = sum(in_blocks(c, blocks) for c in letters) / max(len(letters), 1)
            assert share >= 0.3, share
        assert "0.05" in text and "28.4" in text
    assert rep["script"] == pack["profile"]["id"] and rep["fonts"]


WRAPPED = ("Missing character: There is no t (U+0074) in font [fonts/NotoNaskhArabic-Regula\n"
           "r.ttf]/OT:script=arab;language=dflt;!\n")


def test_parse_missing_survives_tex_line_wrapping():
    assert parse_missing(WRAPPED) == [{"char": "t", "codepoint": "U+0074", "font": "NotoNaskhArabic-Regular.ttf"}]
    import re
    from rosettex.render import postcheck as pc
    assert pc.re.search(r"LaTeX Warning: (?:Reference|Citation) `[^']*'[\s\S]{0,80}?undefined",
                        "LaTeX Warning: Reference `tab:long-label-name' on page 3\nundefined on input line 120.")


@needs_tex
@pytest.mark.parametrize("code", ["vi", "ar", "ko"])
def test_long_table_cells_wrap_instead_of_shrinking(tmp_path, code):
    from conftest import fake_script, fake_vi
    long_cell = SAMPLES[code]
    w = make_work(tmp_path, lang=code)
    tr = (lambda s: fake_vi(s) if code == "vi" else fake_script(s, SAMPLES[code]))
    translate_all(w, lambda s: (long_cell + " " + tr(s)) if len(s) <= 40 else tr(s))  # long text in every short unit
    rep = render_core(w, tmp_path / "o.pdf")
    tex = (w.out / "main.tex").read_text(encoding="utf-8")
    assert r"\usepackage{array}" in tex and r"\raggedright\arraybackslash}p{" in tex
    assert rep["postcheck"]["missing_glyphs"] == []


def test_roman_table_labels_are_kept(tmp_path):
    from rosettex.extract.mineru_adapter import _table_no
    assert _table_no([{"type": "table_caption", "content": [{"type": "text", "content": "TABLE IV"}]},
                      {"type": "table_caption", "content": [{"type": "text", "content": "Summary of results."}]}]) == "IV"
    assert _table_no([{"type": "table_caption", "content": [{"type": "text", "content": "Table results show"}]}]) is None


def test_table_title_blocks_fold_into_the_table():
    from rosettex.extract.mineru_adapter import _fold_table_titles
    h = {"kind": "heading", "id": "h", "spans": [{"t": "text", "v": "TABLE IV"}]}
    c = {"kind": "paragraph", "id": "c", "spans": [{"t": "text", "v": "Summary."}]}
    t = {"kind": "table", "id": "t", "caption": [], "label": None}
    out = _fold_table_titles([h, c, t])
    assert [b["id"] for b in out] == ["t"] and out[0]["number"] == "IV" and out[0]["caption"][0]["v"] == "Summary."
    assert len(_fold_table_titles([c, t])) == 2


def test_diagram_text_is_not_a_section():
    from rosettex.extract.mineru_adapter import _figure_text
    fig = {"kind": "figure", "id": "f", "page": 4, "bbox": [0.515, 0.276, 0.913, 0.716]}
    above = {"kind": "heading", "id": "h", "page": 4, "bbox": [0.516, 0.236, 0.795, 0.253]}
    inside = {"kind": "paragraph", "id": "p", "page": 4, "bbox": [0.52, 0.30, 0.9, 0.32]}
    real = {"kind": "heading", "id": "r", "page": 4, "bbox": [0.076, 0.655, 0.359, 0.67]}
    keep, gone = _figure_text([above, fig, inside, real])
    assert [b["id"] for b in keep] == ["f", "r"] and {g["block"] for g in gone} == {"h", "p"}


def test_figure_crop_reaches_the_caption_and_the_diagram_title(tmp_path):
    from rosettex.extract.mineru_adapter import _block, _figure_text
    from conftest import FIX
    from rosettex.pdf import render_region_png
    raw = {"type": "image", "index": 2, "bbox": [0.085, 0.233, 0.483, 0.546], "content": [
        {"type": "image_body", "bbox": [0.085, 0.233, 0.483, 0.546], "image_path": "x.jpg", "content": ""},
        {"type": "image_caption", "bbox": [0.075, 0.59, 0.493, 0.627], "content": [{"type": "text", "content": "Fig. 2. Cap."}]}]}
    fig = _block(raw, "figure", 4, tmp_path, tmp_path)
    assert abs(fig["crop"][3] - 0.586) < 1e-9  # down to just above the caption
    title = {"kind": "heading", "id": "h", "page": 4, "bbox": [0.09, 0.205, 0.40, 0.222]}
    _figure_text([title, fig])
    assert abs(fig["crop"][1] - 0.201) < 1e-9  # up to the diagram's own title
    out = render_region_png(FIX / "papers" / "short.pdf", 1, [0.1, 0.1, 0.5, 0.3], tmp_path / "r.png", dpi=72)
    assert out.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_figure_crop_stops_at_the_caption_above(tmp_path):
    from rosettex.extract.mineru_adapter import adapt
    import copy
    from conftest import FIX
    src = FIX / "mineru-4.2.3-standard"
    middle = json.loads((src / "short.middle.json").read_text(encoding="utf-8"))
    page = middle["pages"][0]
    cap_above = {"type": "image", "index": 900, "bbox": [0.08, 0.10, 0.92, 0.14], "content": [
        {"type": "image_body", "bbox": [0.08, 0.10, 0.92, 0.14], "content": ""},
        {"type": "image_caption", "bbox": [0.08, 0.15, 0.92, 0.17], "content": [{"type": "text", "content": "Fig. 9. Above."}]}]}
    fig = {"type": "image", "index": 901, "bbox": [0.08, 0.20, 0.48, 0.40], "content": [
        {"type": "image_body", "bbox": [0.08, 0.20, 0.48, 0.40], "content": ""},
        {"type": "image_caption", "bbox": [0.08, 0.42, 0.48, 0.44], "content": [{"type": "text", "content": "Fig. 10. Mine."}]}]}
    m = copy.deepcopy(middle)
    m["pages"][0]["blocks"] = [cap_above, fig]
    doc = adapt([(m, src)], src)
    crop = [b for b in doc["blocks"] if b["id"] == "p1b901"][0]["crop"]
    assert crop[1] >= 0.17  # never reaches into the caption of the figure above


def test_float_pages_from_log():
    from rosettex.render.compile import _off, float_pages, place_floats
    log = "RXP:r:fig:3:4\\nother\nRXP:r:fig:3:4\nRXP:f:fig:3:5\nRXP:r:tab:I:9\nRXP:f:tab:I:9\nRXP:f:fig:9:2\n"
    p = float_pages(log)
    assert p == {"fig:3": [4, 5], "tab:I": [9, 9]} and _off(p) == 1
    doc = {"blocks": [{"id": "a", "kind": "paragraph"}, {"id": "b", "kind": "paragraph"}, {"id": "c", "kind": "paragraph"},
                      {"id": "f", "kind": "figure", "label": "fig:3", "wide": True}]}
    log2 = "RXP:b:a:3\nRXP:b:b:4\nRXP:b:c:5\nRXP:r:fig:3:5\nRXP:f:fig:3:6\n"
    assert place_floats(doc, log2, {}) == {"fig:3": "c"}  # two-column: issued on page 4 (c starts there), lands on 5


def test_length_ratio_is_a_warning_not_a_problem(tmp_path, monkeypatch):
    from rosettex.checks import load_pack
    from rosettex.render import postcheck as pcm
    w = make_work(tmp_path, lang="my")
    doc = read_json(w.document)
    monkeypatch.setattr(pcm, "full_text", lambda p: "\U0001D465 " * 3)  # math characters present, nothing else wrong
    for code, ratio, warn in (("my", 2.0, False), ("my", 2.5, True), ("vi", 1.5, True)):
        heights = iter([ratio, 1.0])
        monkeypatch.setattr(pcm, "line_height_total", lambda p: next(heights))
        tex = "".join("\\ref{%s}" % n for u in doc["units"] for n in u["ph"] if n[0] in "cr")
        r = pcm.postcheck(w, w.src_pdf, "", tex, {**doc, "source": {**doc["source"], "counts": {}}}, {}, load_pack(code), set())
        assert not any("length" in p for p in r["problems"]) and bool(r["warnings"]) is warn, (code, ratio, r)


def test_numbered_section_above_a_figure_stays_a_section():
    from rosettex.extract.mineru_adapter import _figure_text
    fig = {"kind": "figure", "id": "f", "page": 4, "bbox": [0.08, 0.30, 0.48, 0.50]}
    sec = {"kind": "heading", "id": "h", "page": 4, "bbox": [0.08, 0.27, 0.30, 0.29], "number": "4"}
    others = [{"kind": "heading", "id": f"x{i}", "page": 1, "bbox": [0, 0, 1, 1], "number": str(i)} for i in (1, 2, 3)]
    keep, gone = _figure_text(others + [sec, fig])
    assert "h" in [b["id"] for b in keep] and not gone                 # a paper with numbered sections: a real heading
    keep, gone = _figure_text([sec, fig])
    assert [g["block"] for g in gone] == ["h"]                         # no numbered sections: it is the diagram's title


def test_figure_parts_outside_the_picture_are_still_reported_as_dropped():
    from rosettex.extract.mineru_adapter import _dropped
    cap = lambda y0, y1, t: {"type": "image_caption", "bbox": [0.1, y0, 0.5, y1], "content": [{"type": "text", "content": t}]}  # noqa: E731
    body = {"type": "image_body", "bbox": [0.1, 0.30, 0.5, 0.50], "content": ""}
    inside = {"type": "image", "index": 3, "content": [body, cap(0.51, 0.53, "note"), cap(0.55, 0.57, "Fig. 1. Real.")]}
    far = {"type": "image", "index": 4, "content": [cap(0.10, 0.12, "title"), body, cap(0.55, 0.57, "Fig. 1. Real.")]}
    assert not [d for d in _dropped(inside, "figure", 1) if d["what"] == "part"]
    assert [d["type"] for d in _dropped(far, "figure", 1) if d["what"] == "part"] == ["image_caption"]


def test_floats_keep_their_source_order_within_a_kind():
    from rosettex.render.latex import Renderer
    para = lambda i, t: {"id": i, "kind": "paragraph", "page": 1, "spans": [{"t": "text", "v": t}]}  # noqa: E731
    tab = lambda i, n: {"id": i, "kind": "table", "page": 1, "label": f"tab:{n}", "caption": [], "cells": [], "bbox": [0, 0, 1, 1]}  # noqa: E731
    doc = {"blocks": [para("p1", "x"), para("p2", "x"), tab("t1", 1), tab("t3", 3)], "source": {"columns": 1, "page_size_pt": [[612, 792]]},
           "units": [{"id": "p1", "type": "paragraph", "block": "p1", "src": "As Table 3 shows", "ph": {"r1": {"src": "3", "tex": "\\ref{tab:3}"}}},
                     {"id": "p2", "type": "paragraph", "block": "p2", "src": "Table 1 and ⟦r1⟧", "ph": {"r1": {"src": "1", "tex": "\\ref{tab:1}"}}}]}
    from rosettex.checks import load_pack
    tex = Renderer(doc, {"p1": "Table ⟦r1⟧", "p2": "Table ⟦r1⟧"}, load_pack("fr"), set(), {}).document()
    assert tex.index("label{tab:1}") < tex.index("label{tab:3}")  # Table 3 is mentioned first but must stay after Table 1


@needs_tex
def test_venue_class_font_error_falls_back_to_the_generic_build(tmp_path, monkeypatch):
    from rosettex.render import compile as C
    real, calls = C.run_tex, [0]

    def fake(tool, d, name, beat=None):
        calls[0] += 1
        if calls[0] == 1:  # the venue class asks for a font the TeX install does not have
            return False, 'Package fontspec Error: The font "LinLibertine_R" cannot be found.', ""
        return real(tool, d, name, beat)
    monkeypatch.setattr(C, "run_tex", fake)
    monkeypatch.setattr(C, "paper_style", lambda doc: "llncs")
    w = make_work(tmp_path)
    translate_all(w)
    rep = render_core(w, tmp_path / "o.pdf")
    assert rep["style"] == "generic" and rep["style_fallback"][0]["style"] == "llncs" and rep["postcheck"]["ok"]


@needs_tex
def test_a_failing_float_pass_keeps_the_finished_build(tmp_path, monkeypatch):
    from rosettex.render import compile as C
    real = C.run_tex

    def fake(tool, d, name, beat=None):
        if (d / "main.best.pdf").exists():  # only the float passes run with a best build on disk
            raise C.RenderError("compiling main.tex timed out")
        return real(tool, d, name, beat)
    monkeypatch.setattr(C, "run_tex", fake)
    monkeypatch.setattr(C, "place_floats", lambda doc, log, place: {**place, "fig:1": "p1b0"})  # force a re-pass
    monkeypatch.setattr(C, "_off", lambda pages: 1)
    w = make_work(tmp_path)
    translate_all(w)
    rep = render_core(w, tmp_path / "o.pdf")
    assert (tmp_path / "o.pdf").is_file() and rep["postcheck"]["ok"]

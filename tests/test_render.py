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

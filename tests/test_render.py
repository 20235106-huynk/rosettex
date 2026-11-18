import pytest

from conftest import make_work, translate_all
from rosettex.checks import nfc
from rosettex.pdf import full_text
from rosettex.render.compile import culprit, render_core
from rosettex.tools import find_tex
from rosettex.workspace import read_json, write_json

needs_tex = pytest.mark.skipif(find_tex() is None, reason="cần Tectonic hoặc XeLaTeX (scripts/get_tectonic.py)")


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
    text = " ".join(nfc(full_text(out)).split())  # tiêu đề bị ngắt dòng trong cột hẹp, text lớp chữ xen kẽ
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
    tr[first] = tr[first] + " 50% & _ # ~ ^ α ∗"  # Review Focus 3: ký tự đặc biệt trong bản dịch
    write_json(w.translation_path("b001", "vi"), tr)
    rep = render_core(w, tmp_path / "out.pdf")
    ids = {f["id"] for f in rep["fallback"]}
    assert eq["id"] in ids and f"{u['id']}:{name}" in ids
    assert (tmp_path / "out.pdf").is_file() and rep["postcheck"]["ok"], rep["postcheck"]

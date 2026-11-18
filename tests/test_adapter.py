import json

from conftest import FIX
from rosettex.extract.mineru_adapter import adapt, normalize_tex


def load(tier, name):
    d = FIX / f"mineru-4.2.3-{tier}"
    return json.loads((d / f"{name}.middle.json").read_text(encoding="utf-8")), d


def blocks(doc):
    return {b["id"]: b for b in doc["blocks"]}


def test_normalize_tex():
    assert normalize_tex(r"\lambda   =   0 . 0 1") == r"\lambda   =   0.01"
    assert normalize_tex(r"w = 4 0 0 0") == "w = 4000"
    assert normalize_tex(r"3 . 3 \times 1 0 ^ { 1 8 }") == r"3.3 \times 10 ^ { 18 }"
    assert normalize_tex(r"\operatorname { L a y e r N o r m } ( x )") == r"\operatorname{LayerNorm} ( x )"
    assert normalize_tex(r"\mathrm { E q . ~ ( 1 ) }") == r"\mathrm{Eq.~(1)}"
    assert normalize_tex(r"\text {Attention} (Q)") == r"\text{Attention} (Q)"


def test_adapt_short_standard():
    m, d = load("standard", "short")
    doc = adapt([(m, d)], d)
    src, B = doc["source"], blocks(doc)
    assert src["pages"] == 2 and src["columns"] == 2
    assert src["page_size_pt"] == [[612.0, 792.0], [612.0, 792.0]]
    assert src["counts"] == {"tables": 1, "figures": 1, "tagged_equations": 2, "references": 4}
    assert src["parser"].endswith("/standard")
    assert B["p1b0"]["kind"] == "heading" and B["p1b0"]["level"] == 1
    h = B["p1b8"]
    assert h["number"] == "2" and h["label"] == "sec:2" and h["level"] == 2 and h["spans"][0]["v"].strip() == "Method"
    eq = B["p1b10"]
    assert eq["tag"] == "1" and eq["label"] == "eq:1" and r"\tag" not in eq["tex"]
    assert eq["image"] == "images/page_0_equation_10.jpg"
    t = B["p1b17"]
    assert t["label"] == "tab:1" and len(t["cells"]) == 21 and t["cells"][0]["id"] == "p1b17r0c0"
    assert t["caption"][0]["v"].startswith("BLEU scores")
    f = B["p1b18"]
    assert f["kind"] == "figure" and f["label"] == "fig:1" and f["image"] == "images/page_0_chart_18.jpg"
    assert B["p1b22"]["mark"] == "∗" and B["p1b24"]["mark"] == "1"
    assert B["p1b24"]["spans"][0]["v"].startswith("We found warmup")
    assert B["p2b1"]["key"] == "1" and B["p2b1"]["spans"][0]["v"].startswith("A. Vaswani")
    assert "p1b25" not in B  # page_number bị bỏ
    assert [s["t"] for s in B["p1b9"]["spans"]].count("math") == 6


def test_adapt_basic_keeps_eq_reference_inside_math():
    m, d = load("basic", "short")
    doc = adapt([(m, d)], d)
    maths = [s["v"] for s in blocks(doc)["p1b11"]["spans"] if s["t"] == "math"]
    assert r"\mathrm{Eq.~(1)}" in maths


def test_adapt_long_dedupes_labels_and_counts():
    m, d = load("standard", "long")
    doc = adapt([(m, d)], d)
    labels = [b["label"] for b in doc["blocks"] if b.get("label")]
    assert len(labels) == len(set(labels)) and labels.count("eq:1") == 1
    assert doc["source"]["counts"] == {"tables": 28, "figures": 28, "tagged_equations": 56, "references": 4}
    assert doc["source"]["pages"] == 20


def _synthetic():
    m, d = load("standard", "short")
    page = m["pages"][0]
    page["blocks"] += [
        {"type": "list", "index": 90, "bbox": [0.1, 0.1, 0.4, 0.2], "content": "a list"},
        {"type": "text", "index": 91, "bbox": [0.1, 0.3, 0.4, 0.4],
         "content": [{"type": "text", "content": "x"}, {"type": "footnote_ref", "content": "1"}]},
        {"type": "table", "index": 92, "bbox": [0.1, 0.5, 0.4, 0.6],
         "content": [{"type": "table_body", "content": "<table><tr><td>a</td></tr></table>"},
                     {"type": "table_footnote", "content": [{"type": "text", "content": "note"}]}]},
    ]
    return m, d, page["page_idx"] + 1


def test_dropped_content_is_recorded():
    m, d, pg = _synthetic()
    doc = adapt([(m, d)], d)
    dropped = doc["source"]["dropped"]
    assert {"what": "block", "type": "list", "page": pg, "block": f"p{pg}b90"} in dropped
    assert {"what": "span", "type": "footnote_ref", "page": pg, "block": f"p{pg}b91"} in dropped
    assert {"what": "part", "type": "table_footnote", "page": pg, "block": f"p{pg}b92"} in dropped
    assert not any(x["type"] == "page_number" for x in dropped)  # nhiễu có chủ ý, không tính


def test_clean_fixture_records_nothing_dropped():
    m, d = load("standard", "short")
    assert adapt([(m, d)], d)["source"]["dropped"] == []

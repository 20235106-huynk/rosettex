import json

from conftest import FIX
from rosettex.extract.mineru_adapter import adapt
from rosettex.extract.segment import encode, segment


def seg(tier="standard", name="short", max_chars=3000):
    d = FIX / f"mineru-4.2.3-{tier}"
    m = json.loads((d / f"{name}.middle.json").read_text(encoding="utf-8"))
    return segment(adapt([(m, d)], d), max_chars)


def test_merge_across_columns_not_across_equation():
    doc, _ = seg()
    B = {b["id"]: b for b in doc["blocks"]}
    assert "p1b20" not in B and B["p1b15"]["merged"] == ["p1b15", "p1b20"]
    assert "p1b21" in B  # starts with a capital letter
    assert "p1b13" in B  # "with λ…" comes after an equation: do not merge across the equation


def test_roles():
    doc, _ = seg()
    B = {b["id"]: b for b in doc["blocks"]}
    assert B["p1b1"]["role"] == "authors" and B["p1b3"]["role"] == "authors"
    assert B["p1b22"]["role"] == "affiliation" and B["p1b23"]["role"] == "affiliation"
    assert "role" not in B["p1b24"] and B["p2b1"]["role"] == "reference"


def test_units_placeholders_and_links():
    doc, _ = seg()
    U = {u["id"]: u for u in doc["units"]}
    u = U["p1b7"]
    assert "⟦c1⟧" in u["src"] and u["ph"]["c1"] == {"src": "[4]", "tex": r"\cite{ref4}"}
    u = U["p1b11"]
    assert u["src"].startswith("As shown in Eq. ⟦r1⟧, the factor ⟦m1⟧")
    assert u["ph"]["r1"] == {"src": "(1)", "tex": r"\eqref{eq:1}"}
    assert any(p["tex"] == r"\cite{ref1,ref2}" for p in u["ph"].values())
    texs = [p["tex"] for p in U["p1b15"]["ph"].values()]
    assert r"\ref{tab:1}" in texs and r"\ref{fig:1}" in texs and r"\ref{sec:2}" in texs
    assert "p1b1" not in U and "p2b1" not in U  # kept as is: not in a batch
    assert "p1b0" in U and "p1b17.cap" in U and "p1b17r2c0" in U  # title, caption, cell "ByteNet"
    assert "p1b17r2c2" not in U and "p1b17r2c1" not in U  # number cells "23.75", "1.2B"


def test_eq_reference_recognized_inside_math_basic():
    doc, _ = seg("basic")
    u = {u["id"]: u for u in doc["units"]}["p1b11"]
    assert "Eq. ⟦r1⟧" in u["src"] and u["ph"]["r1"]["tex"] == r"\eqref{eq:1}"
    assert not any("Eq" in p["src"] for n, p in u["ph"].items() if n.startswith("m"))


def test_unknown_citation_and_label_stay_text():
    text, ph = encode([{"t": "text", "v": "see [9] and Figure 7"}], {"labels": {"fig:1"}, "refs": {"1"}})
    assert text == "see [9] and Figure 7" and ph == {}


def test_url_and_superscript():
    text, ph = encode([{"t": "text", "v": "code at https://x.org/a_b%20c."}, {"t": "sup", "v": "1"}],
                      {"labels": set(), "refs": set()})
    assert text == "code at ⟦u1⟧.⟦m1⟧"
    assert ph["u1"]["tex"] == r"\url{https://x.org/a_b\%20c}"
    assert ph["m1"]["tex"] == r"\textsuperscript{1}"


def test_batches_keep_tables_together_and_respect_size():
    doc, batches = seg("standard", "long", max_chars=3000)
    ids = [it["id"] for b in batches for it in b["items"]]
    assert len(ids) == len(set(ids)) == len(doc["units"])
    for b in batches:
        size = sum(len(it["text"]) for it in b["items"])
        tables = {it["id"].split("r")[0] for it in b["items"] if it["type"] == "cell"}
        assert size <= 3000 or len(b["items"]) == 1 or tables
    owner = {}
    for b in batches:
        for it in b["items"]:
            if it["type"] == "cell":
                owner.setdefault(it["id"].split("r")[0], set()).add(b["batch"])
    assert all(len(v) == 1 for v in owner.values())

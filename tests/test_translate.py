import json

import pytest

from conftest import TERMS, fake_vi, make_work
from rosettex import translate
from rosettex.workspace import UsageError, read_json


def write(path, obj, bom=False, crlf=False):
    text = json.dumps(obj, ensure_ascii=False, indent=1)
    if crlf:
        text = text.replace("\n", "\r\n")
    path.write_bytes((b"\xef\xbb\xbf" if bom else b"") + text.encode("utf-8"))
    return path


def test_next_requires_terms(tmp_path):
    w = make_work(tmp_path)
    with pytest.raises(UsageError) as e:
        translate.next_package(w)
    assert "--outline" in e.value.next_action


def test_terms_validation(tmp_path):
    w = make_work(tmp_path)
    bad = translate.write_terms(w, write(tmp_path / "t.json", {"terms": [{"src": "a"}, {"src": "a", "dst": "b"}]}))
    assert bad["ok"] is False and len(bad["errors"]) == 2
    assert translate.write_terms(w, write(tmp_path / "t.json", TERMS))["ok"] is True


def test_outline(tmp_path):
    o = translate.outline(make_work(tmp_path))
    assert o["title"].startswith("Attention-Based") and o["abstract"].startswith("We propose")
    assert "2 Method" in o["headings"]


def test_next_accept_loop_and_validate(tmp_path):
    w = make_work(tmp_path)
    translate.write_terms(w, write(tmp_path / "t.json", TERMS))
    first = translate.next_package(w)
    assert first["done"] is False and first["batch"]["batch"] == "b001" and first["previous"] is None
    assert all(p.endswith(".png") and (w.pages / p.split("/")[-1].split("\\")[-1]).exists() for p in first["page_images"])
    assert "--batch b001" in first["submit"]
    bad = translate.accept(w, "b001", write(tmp_path / "b.json", {it["id"]: it["text"] for it in first["batch"]["items"]}))
    assert bad["ok"] is False and not w.translation_path("b001", "vi").exists()
    seen = []
    while True:
        pkg = translate.next_package(w)
        if pkg["done"]:
            break
        bid = pkg["batch"]["batch"]
        if seen:
            assert pkg["previous"]["dst"] is not None
        sub = {it["id"]: fake_vi(it["text"]) for it in pkg["batch"]["items"]}
        res = translate.accept(w, bid, write(tmp_path / f"{bid}.json", sub, bom=True, crlf=True))
        assert res["ok"] is True, res["errors"]
        seen.append(bid)
    assert seen == translate.batch_ids(w)
    log = (w.logs / "accept.jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(log[0])["ok"] is False and len(log) == len(seen) + 1
    v = translate.validate(w)
    assert v["ok"] is True, v["errors"]


def test_accept_stores_nfc(tmp_path):
    import unicodedata
    w = make_work(tmp_path)
    translate.write_terms(w, write(tmp_path / "t.json", TERMS))
    pkg = translate.next_package(w)
    sub = {it["id"]: unicodedata.normalize("NFD", fake_vi(it["text"])) for it in pkg["batch"]["items"]}
    assert translate.accept(w, "b001", write(tmp_path / "n.json", sub))["ok"] is True
    stored = read_json(w.translation_path("b001", "vi"))
    assert all(v == unicodedata.normalize("NFC", v) for v in stored.values())


def test_validate_reports_untranslated_batches(tmp_path):
    w = make_work(tmp_path)
    translate.write_terms(w, write(tmp_path / "t.json", TERMS))
    v = translate.validate(w)
    assert v["ok"] is False and v["errors"]["b001"] == ["batch has not been translated"]


def test_terms_wrong_types_give_errors_not_exceptions(tmp_path):
    w = make_work(tmp_path)
    for bad in ({"topic": "x", "terms": None}, {"topic": "x", "keep": "abc"}, {"topic": "x", "abbreviations": 3},
                {"topic": "x", "terms": "ab"}):
        res = translate.write_terms(w, write(tmp_path / "t.json", bad))
        assert res["ok"] is False and res["errors"], bad


def test_accept_log_written_with_lf_newline(tmp_path, monkeypatch):
    import builtins
    w = make_work(tmp_path)
    seen = []
    real = builtins.open

    def spy(file, mode="r", *a, **kw):
        if str(file).endswith("accept.jsonl"):
            seen.append(kw.get("newline"))
        return real(file, mode, *a, **kw)
    monkeypatch.setattr(builtins, "open", spy)
    translate.write_terms(w, write(tmp_path / "t.json", TERMS))
    translate.accept(w, "b001", write(tmp_path / "b.json", {"x": "y"}))
    assert seen == ["\n"]

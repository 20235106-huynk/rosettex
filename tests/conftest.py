import json
import re
import shutil
import zipfile
from pathlib import Path

FIX = Path(__file__).parent / "fixtures"
_WORD = re.compile(r"⟦[a-z]\d+⟧|\S+")


def fake_vi(src: str) -> str:
    """Fake translator: keep placeholders and every word with a digit, replace other words with Vietnamese words."""
    out = []
    for i, w in enumerate(_WORD.findall(src)):
        out.append(w if w.startswith("⟦") or re.search(r"\d", w) else ("của" if i % 3 == 0 else "việc"))
    return " ".join(out)


def make_work(tmp_path, tier="standard", name="short", lang="vi"):
    """A work directory with extract already done, built straight from the fixture (no MinerU needed)."""
    from rosettex.extract.mineru_adapter import adapt
    from rosettex.extract.segment import segment
    from rosettex.workspace import Work, write_json

    w = Work(tmp_path / "Tài liệu" / "work dir")
    w.root.mkdir(parents=True)
    shutil.copyfile(FIX / "papers" / f"{name}.pdf", w.src_pdf)
    d = FIX / f"mineru-4.2.3-{tier}"
    if (d / f"{name}.zip").exists():
        gdir = w.mineru / "g001" / "x"
        with zipfile.ZipFile(d / f"{name}.zip") as z:
            z.extractall(gdir)
        middle = json.loads((gdir / "middle_json.json").read_text(encoding="utf-8"))
    else:
        gdir = d
        middle = json.loads((d / f"{name}.middle.json").read_text(encoding="utf-8"))
    doc, batches = segment(adapt([(middle, gdir)], w.root if gdir != d else d))
    for b in batches:
        write_json(w.batch_path(b["batch"]), b)
    write_json(w.flagged, [])
    write_json(w.document, doc)
    write_json(w.manifest, {"lang": lang, "out": str(tmp_path / "Kết quả" / f"{name}.{lang}.pdf"),
                            "source": "fixture", "sha256": "x", "pages": doc["source"]["pages"],
                            "tier": tier, "tier_reason": "default", "pages_per_group": 50,
                            "extract_cmd": "rosettex extract fixture"})
    return w


TERMS = {"topic": "Kiến trúc chỉ dùng cơ chế chú ý cho dịch máy",
         "terms": [{"src": "attention", "dst": "chú ý"}], "keep": ["BLEU"], "abbreviations": []}


def fake_script(src: str, sample: str) -> str:
    """Keep every placeholder and every number of the source; the words come from a sample in the target script."""
    keep = re.findall(r"⟦[a-z]\d+⟧|\d+(?:[.,]\d+)*", src)
    if len(src) <= 40:  # table cells and short labels: a short stand-in, so tables keep a readable size
        sample = sample[:12].rstrip("\u200b ")
    return sample + (" " + " ".join(keep) if keep else "")


def translate_all(work, translator=fake_vi):
    """Translate every batch with a fake translator (default fake_vi) and write the translation files directly."""
    from rosettex.translate import batch_ids
    from rosettex.workspace import read_json, write_json

    write_json(work.terms, TERMS)
    lang = read_json(work.manifest)["lang"]
    for bid in batch_ids(work):
        b = read_json(work.batch_path(bid))
        write_json(work.translation_path(bid, lang), {it["id"]: translator(it["text"]) for it in b["items"]})

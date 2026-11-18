"""Giao tiếp với agent dịch: terms, outline, next, accept, validate."""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from .checks import check_batch, load_pack, nfc, normalize_ws
from .pdf import render_page_png
from .workspace import UsageError, Work, q, read_json, read_manifest, write_json


def manifest(work: Work) -> dict:
    m = read_manifest(work)
    if m is None:
        raise UsageError(f"không phải thư mục làm việc rosettex: {work.root}")
    return m


def batch_ids(work: Work) -> list[str]:
    ids = [p.stem for p in work.batches.glob("b*.json") if re.fullmatch(r"b\d+", p.stem)]
    return sorted(ids, key=lambda s: int(s[1:]))


def translations(work: Work, lang: str) -> dict:
    out = {}
    for bid in batch_ids(work):
        out.update(read_json(work.translation_path(bid, lang), {}))
    return out


def check_terms(t) -> list[str]:
    if not isinstance(t, dict):
        return ["terms.json phải là một object JSON"]
    errs = []
    if not isinstance(t.get("topic"), str) or not t["topic"].strip():
        errs.append("thiếu 'topic' (một câu tóm tắt chủ đề)")
    seen = set()
    lists = {}
    for key, what in (("terms", "danh sách object"), ("keep", "danh sách chuỗi"), ("abbreviations", "danh sách object")):
        v = t.get(key, [])
        lists[key] = v if isinstance(v, list) else []
        if lists[key] is not v:
            errs.append(f"'{key}' phải là {what}")
    for i, x in enumerate(lists["terms"]):
        if not (isinstance(x, dict) and isinstance(x.get("src"), str) and isinstance(x.get("dst"), str)
                and x["src"].strip() and x["dst"].strip()):
            errs.append(f"terms[{i}] cần 'src' và 'dst' là chuỗi không rỗng")
            continue
        key = x["src"].strip().lower()
        if key in seen:
            errs.append(f"'src' bị trùng: {x['src']}")
        seen.add(key)
    if not all(isinstance(k, str) for k in lists["keep"]):
        errs.append("'keep' phải là danh sách chuỗi")
    for i, x in enumerate(lists["abbreviations"]):
        if not (isinstance(x, dict) and isinstance(x.get("abbr"), str) and isinstance(x.get("expansion"), str)):
            errs.append(f"abbreviations[{i}] cần 'abbr' và 'expansion'")
    return errs


def write_terms(work: Work, file) -> dict:
    manifest(work)
    try:
        t = json.loads(Path(file).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as e:
        return {"ok": False, "errors": [f"không đọc được JSON: {e}"]}
    errs = check_terms(t)
    if errs:
        return {"ok": False, "errors": errs}
    write_json(work.terms, t)
    return {"ok": True, "terms": len(t.get("terms", [])), "next_action": f"rosettex next {q(work.root)} --json"}


def _doc(work: Work) -> dict:
    doc = read_json(work.document)
    if doc is None:
        raise UsageError("chưa tách xong nội dung", next_action=f"rosettex status {q(work.root)} --json")
    return doc


def outline(work: Work) -> dict:
    doc = _doc(work)
    units = {u["id"]: u for u in doc["units"]}
    title, abstract, heads, after_abs = None, None, [], False
    for b in doc["blocks"]:
        if b["kind"] == "heading":
            text = "".join(s["v"] for s in b["spans"] if s["t"] == "text").strip()
            if b["level"] == 1 and title is None:
                title = text
                continue
            heads.append(((b.get("number") or "") + " " + text).strip())
            after_abs = text.lower() in ("abstract", "tóm tắt")
        elif b["kind"] == "paragraph" and after_abs and abstract is None and b["id"] in units:
            abstract = units[b["id"]]["src"]
            after_abs = False
    return {"title": title, "abstract": abstract, "headings": heads, "batches": len(batch_ids(work)),
            "units": len(doc["units"]),
            "terms_template": {"topic": "…", "terms": [{"src": "…", "dst": "…"}], "keep": [],
                               "abbreviations": [{"abbr": "…", "expansion": "…"}]},
            "next_action": f"rosettex terms {q(work.root)} --file <terms.json> --json"}


def page_image(work: Work, page: int) -> Path:
    out = work.pages / f"p{page:03d}.png"
    if not out.exists():
        render_page_png(work.src_pdf, page, out)
    return out


def next_package(work: Work, batch_id: str | None = None) -> dict:
    m = manifest(work)
    lang = m["lang"]
    terms = read_json(work.terms)
    if terms is None:
        raise UsageError("chưa có terms.json: đọc dàn ý bài rồi ghi thuật ngữ trước",
                         next_action=f"rosettex next {q(work.root)} --outline --json")
    ids = batch_ids(work)
    if batch_id is None:
        pending = [b for b in ids if not work.translation_path(b, lang).exists()]
        if not pending:
            return {"done": True, "next_action": f"rosettex validate {q(work.root)} --json"}
        batch_id = pending[0]
    if batch_id not in ids:
        raise UsageError(f"không có lô {batch_id}")
    batch = read_json(work.batch_path(batch_id))
    prev = None
    i = ids.index(batch_id)
    if i > 0:
        last = read_json(work.batch_path(ids[i - 1]))["items"][-1]
        prev = {"src": last["text"], "dst": read_json(work.translation_path(ids[i - 1], lang), {}).get(last["id"])}
    pages = sorted({it["page"] for it in batch["items"]})
    return {"done": False, "batch": batch, "topic": terms["topic"], "terms": terms, "section": batch["section"],
            "previous": prev, "page_images": [str(page_image(work, p)) for p in pages],
            "rules": load_pack(lang)["rules"],
            "format": 'JSON {"<id>": "<bản dịch>"} chứa đủ mọi id của lô',
            "submit": f"rosettex accept {q(work.root)} --batch {batch_id} --file <file.json> --json"}


def accept(work: Work, batch_id: str, file) -> dict:
    m = manifest(work)
    batch = read_json(work.batch_path(batch_id))
    if batch is None:
        raise UsageError(f"không có lô {batch_id}")
    try:
        sub = json.loads(Path(file).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as e:
        res = {"errors": {"_file": [f"không đọc được JSON: {e}"]}, "warnings": {}}
    else:
        if not isinstance(sub, dict) or not all(isinstance(v, str) for v in sub.values()):
            res = {"errors": {"_file": ['file phải là JSON {"<id>": "<bản dịch>"}']}, "warnings": {}}
        else:
            res = check_batch(batch, sub, load_pack(m["lang"]), read_json(work.terms, {}))
    ok = not res["errors"]
    if ok:
        typ = {it["id"]: it["type"] for it in batch["items"]}
        write_json(work.translation_path(batch_id, m["lang"]), {k: normalize_ws(typ[k], nfc(v)) for k, v in sub.items()})
    work.logs.mkdir(parents=True, exist_ok=True)
    # ponytail: nhiều subagent cùng ghi nối dòng ngắn; đủ an toàn cho nhật ký đo tỉ lệ từ chối, không phải trạng thái.
    with open(work.logs / "accept.jsonl", "a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps({"batch": batch_id, "ok": ok, "errors": sum(map(len, res["errors"].values())),
                             "t": time.time()}) + "\n")
    out = {"ok": ok, **res}
    if not ok:
        out["next_action"] = (f"sửa các id trong 'errors' rồi nộp lại cả lô: rosettex accept {q(work.root)} "
                              f"--batch {batch_id} --file <file.json> --json")
    return out


def _translations_mtime(work: Work, lang: str) -> float:
    return max((p.stat().st_mtime for p in work.batches.glob(f"b*.{lang}.json")), default=0.0)


def validate(work: Work) -> dict:
    m = manifest(work)
    lang = m["lang"]
    pack, terms, doc = load_pack(lang), read_json(work.terms, {}), _doc(work)
    errors, warnings = {}, {}
    for bid in batch_ids(work):
        tr = read_json(work.translation_path(bid, lang))
        if tr is None:
            errors[bid] = ["lô chưa được dịch"]
            continue
        res = check_batch(read_json(work.batch_path(bid)), tr, pack, terms)
        errors.update(res["errors"])
        warnings.update(res["warnings"])
    labels = {b["label"] for b in doc["blocks"] if b.get("label")}
    refs = {b["key"] for b in doc["blocks"] if b["kind"] == "reference"}
    for u in doc["units"]:
        for p in u["ph"].values():
            for lab in re.findall(r"\\(?:eq)?ref\{([^}]*)\}", p["tex"]):
                if lab not in labels:
                    errors.setdefault(u["id"], []).append(f"nhãn không tồn tại: {lab}")
            for keys in re.findall(r"\\cite\{([^}]*)\}", p["tex"]):
                for k in keys.split(","):
                    if k[3:] not in refs:
                        errors.setdefault(u["id"], []).append(f"tài liệu tham khảo không tồn tại: {k}")
    return {"ok": not errors, "errors": errors, "warnings": warnings, "mtime": _translations_mtime(work, lang)}

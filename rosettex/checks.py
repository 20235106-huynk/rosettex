"""Gói ngôn ngữ và kiểm tra bản dịch (accept từng lô, validate toàn bài). So sánh luôn trên NFC."""
from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter
from importlib.resources import files

PH_RE = re.compile(r"⟦([a-z])(\d+)⟧")
NUM_RE = re.compile(r"\d+(?:[.,]\d+)*")
THOUSANDS = re.compile(r"\d{1,3}(?:,\d{3})+")


def load_pack(code: str) -> dict:
    base = files("rosettex") / "langpacks"
    f = base / f"{code}.json"
    if re.fullmatch(r"[a-z]{2,3}(?:-[A-Za-z]{2,8})?", code) and f.is_file():
        return json.loads(f.read_text(encoding="utf-8"))
    pack = json.loads((base / "default.json").read_text(encoding="utf-8"))
    pack["code"] = code
    return pack


def nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


def normalize_ws(typ: str, s: str) -> str:
    """Chuẩn hóa khoảng trắng bản dịch: chỉ đoạn văn được giữ ngắt đoạn (một dòng trống);
    mọi loại khác (tiêu đề, chú thích, ô bảng, chú thích chân trang) thành một dòng, vì nằm trong đối số lệnh LaTeX."""
    if typ == "paragraph":
        return re.sub(r"\n(?:[ \t]*\n)+", "\n\n", re.sub(r"[ \t]+(?=\n)", "", s)).strip()
    return " ".join(s.split())


def placeholders(s: str) -> Counter:
    return Counter(m.group(0) for m in PH_RE.finditer(s))


def numbers(s: str) -> Counter:
    out = Counter()
    for m in NUM_RE.finditer(PH_RE.sub(" ", s)):
        v = m.group(0)
        out[v.replace(",", "") if THOUSANDS.fullmatch(v) else v] += 1
    return out


def looks_untranslated(src: str, dst: str, pack: dict, keep=()) -> bool:
    a, b = nfc(" ".join(src.split())), nfc(" ".join(dst.split()))
    rest = PH_RE.sub(" ", b)
    for k in keep:
        rest = rest.replace(k, " ")
    letters = [c for c in rest if c.isalpha()]
    if len(letters) < pack["min_len_check"]:
        return False
    if a == b:
        return True
    if not pack.get("letters"):
        return False
    special = set(pack["letters"])
    diacritic = sum(c in special for c in letters) / len(letters)
    words = re.findall(r"\w+", rest.lower())
    fw = set(pack["function_words"])
    function = sum(w in fw for w in words) / max(len(words), 1)
    return diacritic < pack["min_diacritic_ratio"] and function < pack["min_function_ratio"]


def bad_chars(src: str, dst: str, pack: dict) -> list[str]:
    allowed = set(pack.get("letters", "")) | set(src)
    bad = []
    for c in dst:
        if c in allowed or (c.isascii() and (c.isprintable() or c in "\n\t")):
            continue
        cat = unicodedata.category(c)
        if c == "\ufffd" or cat.startswith("C"):
            bad.append(c)
        elif cat.startswith("L") and pack.get("letters") and not 0x0370 <= ord(c) <= 0x03FF:
            bad.append(c)
    return bad


def check_item(item: dict, dst: str, pack: dict, terms: dict | None) -> tuple[list[str], list[str]]:
    errs, warns = [], []
    src, dst = nfc(item["text"]), nfc(dst or "")
    if not dst.strip():
        return ["đoạn rỗng"], []
    ps, pd = placeholders(src), placeholders(dst)
    if ps - pd:
        errs.append("thiếu placeholder: " + " ".join(sorted((ps - pd).elements())))
    if pd - ps:
        errs.append("thừa hoặc sai placeholder: " + " ".join(sorted((pd - ps).elements())))
    lost = numbers(src) - numbers(dst)
    if lost:
        errs.append("thiếu hoặc đổi số: " + " ".join(sorted(lost.elements()))
                    + " (giữ nguyên dạng số như bản gốc, kể cả dấu chấm thập phân)")
    if looks_untranslated(src, dst, pack, (terms or {}).get("keep", [])):
        errs.append("nghi chưa dịch")
    bad = bad_chars(src, dst, pack)
    if bad:
        errs.append("ký tự lạ (lỗi mã hóa?): " + "".join(sorted(set(bad))))
    lo, hi = pack["length_ratio"]
    if len(src) >= pack["min_len_check"] and not lo <= len(dst) / len(src) <= hi:
        warns.append(f"tỉ lệ độ dài {len(dst) / len(src):.2f} ngoài [{lo}, {hi}]")
    for t in (terms or {}).get("terms", []):
        if t["src"].lower() in src.lower() and nfc(t["dst"]).lower() not in dst.lower():
            warns.append(f"thuật ngữ '{t['src']}' nên dịch là '{t['dst']}'")
    return errs, warns


def check_batch(batch: dict, submitted: dict, pack: dict, terms: dict | None) -> dict:
    errors, warnings = {}, {}
    ids = [it["id"] for it in batch["items"]]
    for extra in sorted(set(submitted) - set(ids)):
        errors[extra] = ["id không thuộc lô này"]
    for it in batch["items"]:
        if it["id"] not in submitted:
            errors[it["id"]] = ["thiếu bản dịch cho id này"]
            continue
        e, w = check_item(it, submitted[it["id"]], pack, terms)
        if e:
            errors[it["id"]] = e
        if w:
            warnings[it["id"]] = w
    return {"errors": errors, "warnings": warnings}

"""Language packs and translation checks (accept per batch, validate for the whole paper). Comparisons are always done on NFC."""
from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter
from importlib.resources import files

PH_RE = re.compile(r"⟦([a-z])(\d+)⟧")
NUM_RE = re.compile(r"\d+(?:[.,]\d+)*")
THOUSANDS = re.compile(r"\d{1,3}(?:,\d{3})+")


CODE_RE = re.compile(r"[a-z]{2,3}(?:-[A-Za-z]{2,8})?")
RAW_CODE_RE = re.compile(r"[A-Za-z]{2,3}(?:[-_][A-Za-z]{2,8})?")


def normalize_code(code: str) -> list[str]:
    """Candidate codes, most specific first: 'ja-JP' -> ['ja-JP', 'ja']; 'sr_latn' -> ['sr-Latn', 'sr']; 'VI' -> ['vi']."""
    parts = code.replace("_", "-").split("-")
    lang = parts[0].lower()
    sub = parts[1] if len(parts) > 1 else ""
    sub = sub.title() if len(sub) == 4 else sub.upper() if len(sub) == 2 else sub
    return [f"{lang}-{sub}", lang] if sub else [lang]


DEFAULT_NUMBER_RULE = "Keep the value of every number, written with the ASCII digits 0-9 and as in the source (including the decimal point)."
DECIMAL_COMMA_RULE = ("Keep the value of every number and write it with the ASCII digits 0-9. You may write it as in the "
                      "source (0.05, 1,000) or in this language's usual style: decimal comma (0,05) and thousands "
                      "separated by a space or a dot (1 000 or 1.000). Never write 1,000 for one thousand: here a comma "
                      "is a decimal sign.")


def parse_blocks(spec) -> list[tuple[int, int]]:
    return [tuple(int(x, 16) for x in s.split("-")) for s in spec]


def in_blocks(c: str, blocks) -> bool:
    o = ord(c)
    return any(a <= o <= b for a, b in blocks)


def _read(p):
    return json.loads(p.read_text(encoding="utf-8"))


def load_profile(sid: str) -> dict:
    prof = _read(files("rosettex") / "langpacks" / "scripts" / f"{sid}.json")
    prof["_blocks"] = parse_blocks(prof["blocks"])
    return prof


def load_languages() -> dict:
    return _read(files("rosettex") / "langpacks" / "languages.json")


def load_pack(code: str) -> dict:
    base = files("rosettex") / "langpacks"
    default = _read(base / "default.json")
    langs = load_languages()
    valid = code != "default" and bool(RAW_CODE_RE.fullmatch(code))
    cands = normalize_code(code) if valid else []
    # the first candidate that is a pack file or a languages.json entry; the requested code is kept as pack["code"]
    key = next((c for c in cands if (base / f"{c}.json").is_file() or c in langs), None)
    entry = langs.get(key) if key else None
    target = key or code
    if entry and "alias" in entry:  # zh -> zh-Hans, sr -> sr-Cyrl: use the target's pack or entry
        target = entry["alias"]
        entry = langs[target]
    if key and (base / f"{target}.json").is_file() and target != "default":
        pack = {k: v for k, v in default.items() if k != "fonts"}  # the default's fonts never leak into a pack
        pack.update(_read(base / f"{target}.json"))
        pack["code"], found = code, True
    else:
        pack, found = {**default, "code": code}, False
        if entry:
            prof = load_profile(entry["script"])
            del pack["fonts"]
            pack.update(script=entry["script"], name=entry["name"],
                        polyglossia=entry.get("polyglossia") or prof["polyglossia_default"],
                        min_target_ratio=prof["min_target_ratio"])
            if prof["layout"] == "rtl":  # ASCII digits for generated numbers (polyglossia's Arabic default is Eastern)
                pack["polyglossia_options"] = "numerals=maghrib"
    pack["decimal_comma"] = bool((entry or {}).get("decimal_comma"))
    if pack["decimal_comma"]:  # the number rule becomes the language's own style (spec: slice 1b §15 item 18)
        pack["rules"] = [DECIMAL_COMMA_RULE if r == DEFAULT_NUMBER_RULE else r for r in pack["rules"]]
    pack["pack_found"], pack["script_known"] = found, found or entry is not None
    pack["profile"] = load_profile(pack["script"])
    pack.setdefault("fonts", pack["profile"]["fonts"])
    return pack


def nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


def normalize_ws(typ: str, s: str) -> str:
    """Normalize whitespace in a translation: only paragraphs keep paragraph breaks (one blank line);
    every other kind (heading, caption, table cell, footnote) becomes one line, because it sits in a LaTeX command argument."""
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


LOCAL_NUM_RE = re.compile(r"\d{1,3}(?:[ \u00a0\u202f]\d{3})+(?:,\d+)?|\d+(?:[.,]\d+)*")
GROUP_DOT = re.compile(r"\d{1,3}(?:\.\d{3})+(?:,\d+)?")


def _local_values(tok: str) -> set[str]:
    """Readings of a number written by a decimal-comma language: '0,05' -> 0.05, '1 000' / '1.000' -> 1000.
    The source reading stays possible ("0.05" kept as is), except English thousands with commas: "1,000" means 1 in
    these languages, so it must be rewritten ("1 000")."""
    out = set() if THOUSANDS.fullmatch(tok) else {tok}
    t = re.sub(r"[ \u00a0\u202f]", "", tok)
    if GROUP_DOT.fullmatch(t):
        t = t.replace(".", "")
    if "." not in t:
        out.add(t.replace(",", "."))
    else:
        out.add(t)
    return out


def missing_numbers(src: str, dst: str, pack: dict) -> Counter:
    """Source numbers not found in the translation. With "decimal_comma" in the pack a number may be written in the
    target style (0.05 -> 0,05, 1,000 -> 1 000); otherwise exactly as in the source (slice 1 rule)."""
    want = numbers(src)
    if not pack.get("decimal_comma"):
        return want - numbers(dst)
    have = [_local_values(m.group(0)) for m in LOCAL_NUM_RE.finditer(PH_RE.sub(" ", dst))]
    lost = Counter()
    for v, n in want.items():
        for _ in range(n):
            hit = next((i for i, vals in enumerate(have) if v in vals), None)
            if hit is None:
                lost[v] += 1
            else:
                have.pop(hit)
    return lost


def looks_untranslated(src: str, dst: str, pack: dict, keep=()) -> bool:
    a, b = nfc(" ".join(src.split())), nfc(" ".join(dst.split()))
    rest = PH_RE.sub(" ", b)
    for k in keep:
        rest = rest.replace(nfc(k), " ")
    letters = [c for c in rest if c.isalpha()]
    if len(letters) < pack["min_len_check"]:
        return False
    if a == b:
        return True
    special, blocks = set(pack.get("letters", "")), pack["profile"]["_blocks"]
    if not special and not blocks:
        return False
    target = sum(c in special or in_blocks(c, blocks) for c in letters) / len(letters)
    fw = set(pack["function_words"])
    if not fw:
        return target < pack["min_target_ratio"]
    words = re.findall(r"\w+", rest.lower())
    function = sum(w in fw for w in words) / max(len(words), 1)
    return target < pack["min_target_ratio"] and function < pack["min_function_ratio"]


def bad_chars(src: str, dst: str, pack: dict) -> list[str]:
    allowed = set(pack.get("letters", "")) | set(src)
    blocks = pack["profile"]["_blocks"]
    has_alphabet = bool(pack.get("letters") or blocks)
    # a known Latin-script language without its own alphabet (de, es, pt, ...): any letter outside Latin/Greek is wrong
    latin_only = pack["profile"]["id"] == "latin" and not has_alphabet and pack.get("script_known", False)
    bad = []
    for c in dst:
        if c in allowed or in_blocks(c, blocks) or (c.isascii() and (c.isprintable() or c in "\n\t")):
            continue
        cat = unicodedata.category(c)
        if c == "\ufffd" or (cat.startswith("C") and c not in "\u200b\u200c\u200d"):
            bad.append(c)
        elif cat.startswith("L") and has_alphabet and not 0x0370 <= ord(c) <= 0x03FF:
            bad.append(c)
        elif cat.startswith("L") and latin_only and not (0x00C0 <= ord(c) <= 0x024F or 0x1E00 <= ord(c) <= 0x1EFF
                                                         or 0x0370 <= ord(c) <= 0x03FF):
            bad.append(c)
    return bad


def foreign_digits(src: str, dst: str) -> list[str]:
    return [c for c in dst if unicodedata.category(c) == "Nd" and not c.isascii() and c not in src]


def missing_word_breaks(dst: str, pack: dict, limit: int = 25) -> bool:
    if pack["profile"]["linebreak"] != "zwsp":
        return False
    blocks, run = pack["profile"]["_blocks"], 0
    if "\u200b" not in dst and sum(in_blocks(c, blocks) and c.isalpha() for c in dst) >= 40:
        return True  # spaces between every word are not enough: Tectonic cannot break Thai/Myanmar at spaces-less words
    for c in dst:
        if c in "\u200b \t\n":
            run = 0
        elif in_blocks(c, blocks):
            run += 1
            if run >= limit:
                return True
        else:
            run = 0
    return False


def check_item(item: dict, dst: str, pack: dict, terms: dict | None) -> tuple[list[str], list[str]]:
    errs, warns = [], []
    src, dst = nfc(item["text"]), nfc(dst or "")
    if not dst.strip():
        return ["empty paragraph"], []
    ps, pd = placeholders(src), placeholders(dst)
    if ps - pd:
        errs.append("missing placeholder: " + " ".join(sorted((ps - pd).elements())))
    if pd - ps:
        errs.append("extra or wrong placeholder: " + " ".join(sorted((pd - ps).elements())))
    lost = missing_numbers(src, dst, pack)
    if lost:
        how = ("keep every number's value; write it as in the source or in the target style"
               if pack.get("decimal_comma") else "keep numbers exactly as in the source, including the decimal point")
        errs.append("missing or changed number: " + " ".join(sorted(lost.elements())) + f" ({how})")
    fd = foreign_digits(src, dst)
    if fd:
        errs.append("use the ASCII digits 0-9 exactly as in the source: " + "".join(fd))
    if missing_word_breaks(dst, pack):
        errs.append("put U+200B between words")
    if looks_untranslated(src, dst, pack, (terms or {}).get("keep", [])):
        errs.append("looks untranslated")
    bad = bad_chars(src, dst, pack)
    if bad:
        errs.append("unexpected characters (encoding error?): " + "".join(sorted(set(bad))))
    lo, hi = pack["length_ratio"]
    if len(src) >= pack["min_len_check"] and not lo <= len(dst) / len(src) <= hi:
        warns.append(f"length ratio {len(dst) / len(src):.2f} outside [{lo}, {hi}]")
    for t in (terms or {}).get("terms", []):
        if nfc(t["src"]).lower() in src.lower() and nfc(t["dst"]).lower() not in dst.lower():
            warns.append(f"term '{t['src']}' should be translated as '{t['dst']}'")
    return errs, warns


def check_batch(batch: dict, submitted: dict, pack: dict, terms: dict | None) -> dict:
    errors, warnings = {}, {}
    ids = [it["id"] for it in batch["items"]]
    for extra in sorted(set(submitted) - set(ids)):
        errors[extra] = ["id does not belong to this batch"]
    for it in batch["items"]:
        if it["id"] not in submitted:
            errors[it["id"]] = ["missing translation for this id"]
            continue
        e, w = check_item(it, submitted[it["id"]], pack, terms)
        if e:
            errors[it["id"]] = e
        if w:
            warnings[it["id"]] = w
    return {"errors": errors, "warnings": warnings}

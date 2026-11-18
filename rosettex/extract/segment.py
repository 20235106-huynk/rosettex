"""Merge paragraphs, keep roles, placeholders/links, translation units and batches (SG 9.4-9.8, slice 1 spec section 6)."""
from __future__ import annotations

import itertools
import re

from ..tex import escape_latex

FLOATS = {"table", "figure", "footnote"}
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
URL = re.compile(r"https?://[^\s)\]}>,]+[^\s)\]}>,.]|[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
CITE = re.compile(r"\[(\d+(?:\s*[,–-]\s*\d+)*)\]")
XREF = re.compile(r"\b(Figures?|Figs?\.|Tables?|Tabs?\.|Sections?|Secs?\.|Equations?|Eqs?\.)"
                  r"(\s*~?\s*)(\()?(\d+(?:\.\d+)*)(\))?")
TEXT_MATH = re.compile(r"^\\(?:mathrm|text)\{(.*)\}$")
AFFIL = re.compile(r"Universit|Institut|Laborator|\bLabs?\b|College|School of|Department|\bDept\.", re.I)
NUMERIC_CELL = re.compile(r"(?:[\s\d.,%±+\-–—−()×*/~<>=]|(?<=\d)[KMBGT])*")


def plain(spans) -> str:
    return "".join(s["v"] for s in spans if s["t"] in ("text", "sup")).strip()


def _ends_sentence(spans) -> bool:
    for s in reversed(spans):
        v = s["v"].strip()
        if v:
            return s["t"] == "text" and v.endswith((".", "!", "?", ":"))
    return True


def _starts_lower(spans) -> bool:
    for s in spans:
        v = s["v"].strip().lstrip("([{\"'\u201c\u2018")  # "(collusion) looks …" still continues a sentence
        if v:
            return s["t"] == "math" or (s["t"] == "text" and v[0].islower())
    return False


def _ends_mid_clause(spans) -> bool:
    """A paragraph that stops on a comma, semicolon or dash runs on into the next one, whatever its first letter."""
    for s in reversed(spans):
        v = s["v"].strip()
        if v:
            return s["t"] == "text" and v.endswith((",", ";", "\u2013", "\u2014"))
    return False


def merge_paragraphs(blocks) -> list:
    out, open_i = [], None
    for b in blocks:
        if b["kind"] in FLOATS:
            out.append(b)
            continue
        if b["kind"] == "paragraph" and open_i is not None and (_starts_lower(b["spans"])
                                                                 or _ends_mid_clause(out[open_i]["spans"])):
            a = out[open_i]
            a["spans"] = a["spans"] + [{"t": "text", "v": " "}] + b["spans"]
            a.setdefault("merged", [a["id"]]).append(b["id"])
        else:
            out.append(b)
            open_i = len(out) - 1 if b["kind"] == "paragraph" else None
            if open_i is None:
                continue
        if _ends_sentence(out[open_i]["spans"]):
            open_i = None
    return out


def assign_roles(blocks) -> None:
    in_front = False
    for b in blocks:
        k = b["kind"]
        if k == "heading":
            in_front = b["level"] == 1 and b["page"] == 1
            continue
        if k == "reference":
            b["role"] = "reference"
            continue
        if k not in ("paragraph", "footnote"):
            continue
        t = plain(b["spans"])
        if k == "paragraph" and in_front and len(t) < 120:
            b["role"] = "authors"
        elif len(t) < 200 and (EMAIL.search(t) or (k == "footnote" and AFFIL.search(t))):
            b["role"] = "affiliation"


def unwrap_text_math(spans) -> list:
    """`\\mathrm{Eq.~(1)}` (MinerU mistakes a reference for a formula) → text span."""
    out = []
    for s in spans:
        if s["t"] == "math":
            m = TEXT_MATH.match(s["v"].strip())
            if m and XREF.fullmatch(m.group(1).replace("~", " ").strip()):
                out.append({"t": "text", "v": " " + m.group(1).replace("~", " ").strip() + " "})
                continue
        out.append(s)
    return out


def _add(ph, counters, kind, src, tex) -> str:
    counters[kind] = counters.get(kind, 0) + 1
    name = f"{kind}{counters[kind]}"
    ph[name] = {"src": src, "tex": tex}
    return f"⟦{name}⟧"


def _expand(keys: str) -> list[str]:
    out = []
    for part in keys.split(","):
        bounds = re.split(r"[–-]", part)
        lo, hi = int(bounds[0]), int(bounds[-1])
        out += [str(k) for k in range(lo, hi + 1)] if 0 <= hi - lo < 50 else [str(lo), str(hi)]
    return out


def _resolve(kind, m, ctx):
    """→ (text before, src, tex, text after) or None when it is not a valid link."""
    if kind == "u":
        url = m.group(0)
        return "", url, "\\url{" + url.replace("%", "\\%").replace("#", "\\#") + "}", ""
    if kind == "c":
        keys = _expand(m.group(1))
        if not all(k in ctx["refs"] for k in keys):
            return None
        return "", m.group(0), "\\cite{" + ",".join("ref" + k for k in keys) + "}", ""
    word, gap, op, num, cl = m.groups()
    w = word.lower()
    lab = ("fig" if w.startswith("fig") else "tab" if w.startswith("tab")
           else "sec" if w.startswith("sec") else "eq") + ":" + num
    if lab not in ctx["labels"]:
        return None
    gap = gap.replace("~", " ")
    if op and cl:
        tex = "\\eqref{%s}" % lab if lab.startswith("eq:") else "(\\ref{%s})" % lab
        return word + gap, op + num + cl, tex, ""
    return word + gap + (op or ""), num, "\\ref{%s}" % lab, cl or ""


def _encode_text(v, ctx, parts, ph, counters) -> None:
    pos = 0
    while pos < len(v):
        best = None
        for kind, rx in (("u", URL), ("c", CITE), ("r", XREF)):
            m = rx.search(v, pos)
            if m and (best is None or m.start() < best[1].start()):
                best = (kind, m)
        if best is None:
            parts.append(v[pos:])
            return
        kind, m = best
        rep = _resolve(kind, m, ctx)
        if rep is None:
            parts.append(v[pos:m.end()])
        else:
            before, src, tex, after = rep
            parts.append(v[pos:m.start()] + before)
            parts.append(_add(ph, counters, kind, src, tex))
            parts.append(after)
        pos = m.end()


def encode(spans, ctx) -> tuple[str, dict]:
    parts, ph, counters = [], {}, {}
    for s in unwrap_text_math(spans):
        if s["t"] == "text":
            _encode_text(s["v"], ctx, parts, ph, counters)
        elif s["t"] == "math":
            parts.append(_add(ph, counters, "m", s["v"], "$" + s["v"] + "$"))
        elif s["t"] == "sup":
            parts.append(_add(ph, counters, "m", s["v"], "\\textsuperscript{" + escape_latex(s["v"]) + "}"))
    text = " ".join("".join(parts).split())
    return re.sub(r"\s+([,.;:)])", r"\1", text), ph


def _unit(uid, typ, block, section, src, ph) -> dict:
    return {"id": uid, "type": typ, "block": block["id"], "page": block["page"], "section": section,
            "src": src, "ph": ph}


def build_units(doc) -> list[dict]:
    blocks = doc["blocks"]
    ctx = {"labels": {b["label"] for b in blocks if b.get("label")},
           "refs": {b["key"] for b in blocks if b["kind"] == "reference" and b.get("key")}}
    units, section = [], ""
    for b in blocks:
        k = b["kind"]
        if b.get("role"):
            continue
        if k == "heading":
            text, ph = encode(b["spans"], ctx)
            section = ((b.get("number") or "") + " " + plain(b["spans"])).strip()
            if text:
                units.append(_unit(b["id"], "heading", b, section, text, ph))
        elif k in ("paragraph", "footnote"):
            text, ph = encode(b["spans"], ctx)
            if text:
                units.append(_unit(b["id"], k, b, section, text, ph))
        elif k in ("table", "figure"):
            text, ph = encode(b.get("caption") or [], ctx)
            if text:
                units.append(_unit(b["id"] + ".cap", "caption", b, section, text, ph))
            for cell in b.get("cells", []):
                if cell["text"] and not NUMERIC_CELL.fullmatch(cell["text"]):
                    text, ph = encode([{"t": "text", "v": cell["text"]}], ctx)
                    units.append(_unit(cell["id"], "cell", b, section, text, ph))
    return units


def make_batches(units, max_chars: int = 3000) -> list[dict]:
    groups = [list(g) for _, g in itertools.groupby(
        units, key=lambda u: u["block"] if u["type"] in ("cell", "caption") else u["id"])]
    batches, cur, size = [], [], 0
    for g in groups:
        n = sum(len(u["src"]) for u in g)
        if cur and size + n > max_chars:
            batches.append(cur)
            cur, size = [], 0
        cur.extend(g)
        size += n
    if cur:
        batches.append(cur)
    return [{"batch": f"b{i:03d}", "section": g[0]["section"],
             "items": [{"id": u["id"], "type": u["type"], "page": u["page"], "text": u["src"],
                        "placeholders": {k: v["src"] for k, v in u["ph"].items()}} for u in g]}
            for i, g in enumerate(batches, 1)]


def segment(doc, max_chars: int = 3000):
    doc["blocks"] = merge_paragraphs(doc["blocks"])
    assign_roles(doc["blocks"])
    doc["units"] = build_units(doc)
    return doc, make_batches(doc["units"], max_chars)

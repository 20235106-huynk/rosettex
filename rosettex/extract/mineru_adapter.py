"""MinerU `docvortex.middle` 2.0 (đầu ra --format zip) → document.json (`rosettex.document/1`).

Schema đã kiểm chứng ở SG 4.1: khối → span, chỉ khối có bbox (chuẩn hóa 0–1), page_idx tuyệt đối đếm từ 0.
"""
from __future__ import annotations

import re
from pathlib import Path

from .tables import parse_table_html

KIND = {"doc_title": "heading", "paragraph_title": "heading", "text": "paragraph", "equation": "equation",
        "table": "table", "chart": "figure", "image": "figure", "page_footnote": "footnote", "ref_text": "reference"}
NUM_HEAD = re.compile(r"^\s*(\d+(?:\.\d+)*)\.?\s+(?=\S)")
CAPTION = re.compile(r"^\s*(?:Figure|Fig\.|Table|Tab\.)\s*(\d+)\s*[:.]\s*", re.I)
TAG = re.compile(r"\\tag\s*\{([^{}]*)\}")
REFKEY = re.compile(r"^\s*\[(\d+)\]\s*")
FOOTMARK = re.compile(r"^\s*([∗*†‡§¶]+|\d{1,2})(?=[^\d\s])")
_DIGIT_DOT = re.compile(r"(?<=\d)\s*\.\s*(?=\d)")
_DIGIT_GAP = re.compile(r"(?<=\d)\s+(?=\d)")
_WORDCMD = re.compile(r"\\(operatorname|mathrm|text|mathit|mathbf|mathsf)\s*\{([^{}]*)\}")


def normalize_tex(s: str) -> str:
    """Chỉ gộp khoảng trắng MinerU chèn vào giữa chữ số và giữa chữ cái của lệnh chữ; không đổi ký hiệu."""
    s = _DIGIT_DOT.sub(".", s)
    s = _DIGIT_GAP.sub("", s)

    def join(m):
        toks = m.group(2).split()
        inner = "".join(toks) if toks and all(len(t) == 1 for t in toks) else m.group(2).strip()
        return "\\%s{%s}" % (m.group(1), inner)

    return _WORDCMD.sub(join, s).strip()


def spans_of(content) -> list[dict]:
    if isinstance(content, str):
        return [{"t": "text", "v": content}]
    out = []
    for s in content or []:
        t, v = s.get("type"), s.get("content", "")
        if t == "equation_inline":
            out.append({"t": "math", "v": normalize_tex(v)})
        elif t == "text":
            out.append({"t": "sup" if "superscript" in (s.get("styles") or []) else "text", "v": v})
    return out


def _strip(spans, rx):
    """Cắt tiền tố khớp `rx` khỏi span chữ đầu tiên; trả match hoặc None."""
    for s in spans:
        if s["t"] == "text" and s["v"].strip():
            m = rx.match(s["v"])
            if m:
                s["v"] = s["v"][m.end():]
            return m
    return None


def _img(part, gdir, root):
    p = part.get("image_path") if isinstance(part, dict) else None
    return (Path(gdir) / p).resolve().relative_to(Path(root).resolve()).as_posix() if p else None


def _block(b, kind, page, gdir, root) -> dict:
    out = {"id": f"p{page}b{b['index']}", "kind": kind, "page": page, "bbox": b["bbox"]}
    c = b["content"]
    if kind == "heading":
        spans = spans_of(c)
        m = _strip(spans, NUM_HEAD)
        number = m.group(1) if m else None
        level = 1 if b["type"] == "doc_title" else (2 + number.count(".") if number else b.get("level", 2))
        out.update(spans=spans, number=number, level=level, label=f"sec:{number}" if number else None)
    elif kind == "paragraph":
        out["spans"] = spans_of(c)
    elif kind == "equation":
        tex = c if isinstance(c, str) else "".join(s.get("content", "") for s in c)
        m = TAG.search(tex)
        tag = m.group(1).strip() if m else None
        out.update(tex=normalize_tex(TAG.sub("", tex)), tag=tag, label=f"eq:{tag}" if tag else None,
                   image=_img(b, gdir, root))
    elif kind in ("table", "figure"):
        parts = {p["type"].rsplit("_", 1)[-1]: p for p in c if isinstance(p, dict)}
        cap = spans_of(parts["caption"]["content"]) if "caption" in parts else []
        m = _strip(cap, CAPTION)
        out.update(caption=cap, label=(f"{'tab' if kind == 'table' else 'fig'}:{m.group(1)}" if m else None))
        body = parts.get("body", {})
        if kind == "table":
            cells = parse_table_html(body.get("content") or "")
            for cell in cells:
                cell["id"] = f"{out['id']}r{cell['r']}c{cell['c']}"
            out["cells"] = cells
        out["image"] = _img(body, gdir, root)
    elif kind == "footnote":
        spans = spans_of(c)
        if spans and spans[0]["t"] == "sup":  # dấu chú thích là span chỉ số trên riêng
            mark = spans.pop(0)["v"].strip()
        else:
            m = _strip(spans, FOOTMARK)
            mark = m.group(1) if m else ""
        out.update(spans=spans, mark=mark)
    elif kind == "reference":
        spans = spans_of(c)
        m = _strip(spans, REFKEY)
        out.update(spans=spans, key=m.group(1) if m else None)
    return out


def _columns(blocks) -> int:
    paras = [b for b in blocks if b["kind"] == "paragraph"]
    half = sum(1 for b in paras if b["bbox"][2] <= 0.52 or b["bbox"][0] >= 0.48)
    return 2 if paras and half / len(paras) >= 0.6 else 1


def adapt(groups, root) -> dict:
    sizes: dict[int, list[float]] = {}
    blocks: list[dict] = []
    seen: set[str] = set()
    counts = {"tables": 0, "figures": 0, "tagged_equations": 0, "references": 0}
    npages, producer, tier = 0, "mineru", "?"
    for middle, gdir in groups:
        npages = max(npages, middle["metadata"]["document"]["page_count"])
        pr = middle["metadata"].get("producer") or {}
        producer = f"{pr.get('name', 'mineru')}-{pr.get('version', '?')}"
        tier = middle["extensions"]["mineru"]["tier"]
        for lp in middle["extensions"]["docvortex_layout"]["pages"]:
            sizes[lp["page_idx"] + 1] = [lp["width_pt"], lp["height_pt"]]
        for page in middle["pages"]:
            for b in page["blocks"]:
                t = b["type"]
                counts["tables"] += t == "table"
                counts["figures"] += t in ("chart", "image")
                counts["references"] += t == "ref_text"
                counts["tagged_equations"] += t == "equation" and bool(TAG.search(str(b["content"])))
                kind = KIND.get(t)
                if kind is None:
                    continue
                blk = _block(b, kind, page["page_idx"] + 1, gdir, root)
                lab = blk.get("label")
                if lab in seen:
                    blk["label"] = None
                elif lab:
                    seen.add(lab)
                blocks.append(blk)
    nref = 0
    for blk in blocks:
        if blk["kind"] == "reference":
            nref += 1
            blk["key"] = blk["key"] or str(nref)
    cols = _columns(blocks)
    for blk in blocks:
        if blk["kind"] in ("table", "figure", "equation"):
            blk["wide"] = cols == 2 and blk["bbox"][2] - blk["bbox"][0] > 0.6
    default = sizes[min(sizes)] if sizes else [612.0, 792.0]
    return {"schema": "rosettex.document/1",
            "source": {"pages": npages, "page_size_pt": [sizes.get(i, default) for i in range(1, npages + 1)],
                       "columns": cols, "parser": f"{producer}/{tier}", "counts": counts},
            "blocks": blocks}

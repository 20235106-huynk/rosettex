"""Schema definition, validation, and I/O for ``pdf/layout.json``."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from rosettex.jsonio import read_json, write_json

SCHEMA = "rosettex.pdf-layout/1"

_OBJECT_KINDS = frozenset({"image", "rule", "vector", "form_figure"})
_ALIGNS = frozenset({"left", "right", "center", "justify"})
_ROTATIONS = frozenset({0, 90, 180, 270})
_TRANSLATED_KINDS = frozenset({"title", "heading", "paragraph", "caption", "footnote"})
_KEPT_KINDS = frozenset(
    {"front_matter", "header", "footer", "refs", "toc", "table", "figure", "display_math", "code"}
)
_ALL_KINDS = _TRANSLATED_KINDS | _KEPT_KINDS


def _is_num(val: Any) -> bool:
    return isinstance(val, (int, float)) and not isinstance(val, bool)


def _is_int(val: Any) -> bool:
    return isinstance(val, int) and not isinstance(val, bool)


def _check_bbox(val: Any, label: str, problems: list[str]) -> None:
    if not isinstance(val, list) or len(val) != 4 or not all(_is_num(v) for v in val):
        problems.append(f"{label}: expected [x0, y0, x1, y1] of 4 numbers")
        return
    if val[0] > val[2] + 1e-6 or val[1] > val[3] + 1e-6:
        problems.append(f"{label}: inverted bbox {val}")


def _check_char_ranges(val: Any, label: str, problems: list[str], *, allow_empty: bool = False) -> None:
    if not isinstance(val, list):
        problems.append(f"{label}: expected list of [start, end] ranges")
        return
    if not allow_empty and not val:
        problems.append(f"{label}: empty char range list")
        return
    for idx, rng in enumerate(val):
        if (
            not isinstance(rng, list)
            or len(rng) != 2
            or not _is_int(rng[0])
            or not _is_int(rng[1])
            or rng[0] < 0
            or rng[0] >= rng[1]
        ):
            problems.append(f"{label}[{idx}]: invalid char range {rng!r}")


def validate(layout: dict[str, Any], *, classified: bool = False) -> list[str]:
    """Validate a layout dict against ``rosettex.pdf-layout/1``. Returns a list of problems ([] when valid)."""
    problems: list[str] = []
    if not isinstance(layout, dict):
        return ["layout: expected dict"]

    if layout.get("schema") != SCHEMA:
        problems.append(f"schema: expected {SCHEMA!r}, got {layout.get('schema')!r}")

    sha = layout.get("pdf_sha256")
    if not isinstance(sha, str) or len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha):
        problems.append("pdf_sha256: expected 64-char lowercase hex string")
    if not isinstance(layout.get("producer"), str):
        problems.append("producer: expected str")
    if not isinstance(layout.get("creator"), str):
        problems.append("creator: expected str")

    # doc
    doc = layout.get("doc")
    n_pages = 0
    if not isinstance(doc, dict):
        problems.append("doc: expected dict")
    else:
        if not isinstance(doc.get("body_font"), str):
            problems.append("doc.body_font: expected str")
        if not _is_num(doc.get("body_size")) or doc["body_size"] <= 0:
            problems.append("doc.body_size: expected positive number")
        if not isinstance(doc.get("two_col"), bool):
            problems.append("doc.two_col: expected bool")
        gx = doc.get("gutter_x")
        if gx is not None and not _is_num(gx):
            problems.append("doc.gutter_x: expected number or null")
        if not _is_int(doc.get("pages")) or doc["pages"] < 1:
            problems.append("doc.pages: expected int >= 1")
        else:
            n_pages = doc["pages"]

    # pages
    pages = layout.get("pages")
    if not isinstance(pages, list):
        problems.append("pages: expected list")
    else:
        if n_pages and len(pages) != n_pages:
            problems.append(f"pages: length {len(pages)} != doc.pages {n_pages}")
        n_pages = len(pages)
        for i, p in enumerate(pages):
            lbl = f"pages[{i}]"
            if not isinstance(p, dict):
                problems.append(f"{lbl}: expected dict")
                continue
            if p.get("index") != i:
                problems.append(f"{lbl}.index: expected {i}, got {p.get('index')!r}")
            if not _is_num(p.get("width")) or p["width"] <= 0:
                problems.append(f"{lbl}.width: expected positive number")
            if not _is_num(p.get("height")) or p["height"] <= 0:
                problems.append(f"{lbl}.height: expected positive number")
            if p.get("rotate") not in _ROTATIONS:
                problems.append(f"{lbl}.rotate: expected one of 0, 90, 180, 270")
            _check_bbox(p.get("cropbox"), f"{lbl}.cropbox", problems)
            gr = p.get("garbage_ratio")
            if not _is_num(gr) or not (0.0 <= gr <= 1.0):
                problems.append(f"{lbl}.garbage_ratio: expected number in [0, 1]")
            objs = p.get("objects")
            if not isinstance(objs, list):
                problems.append(f"{lbl}.objects: expected list")
            else:
                for oi, obj in enumerate(objs):
                    olbl = f"{lbl}.objects[{oi}]"
                    if not isinstance(obj, dict):
                        problems.append(f"{olbl}: expected dict")
                        continue
                    if obj.get("kind") not in _OBJECT_KINDS:
                        problems.append(f"{olbl}.kind: invalid {obj.get('kind')!r}")
                    _check_bbox(obj.get("bbox"), f"{olbl}.bbox", problems)

    # blocks
    blocks = layout.get("blocks")
    block_ids: set[str] = set()
    page_char_ranges: dict[int, list[tuple[int, int, str]]] = {}
    if not isinstance(blocks, list):
        problems.append("blocks: expected list")
    else:
        orders: set[int] = set()
        for bi, b in enumerate(blocks):
            lbl = f"blocks[{bi}]"
            if not isinstance(b, dict):
                problems.append(f"{lbl}: expected dict")
                continue
            bid = b.get("id")
            if not isinstance(bid, str) or not bid:
                problems.append(f"{lbl}.id: expected non-empty str")
            elif bid in block_ids:
                problems.append(f"{lbl}.id: duplicate block id {bid!r}")
            else:
                block_ids.add(bid)

            pg = b.get("page")
            if not _is_int(pg) or not (0 <= pg < n_pages):
                problems.append(f"{lbl}.page: out of range {pg!r}")
            _check_bbox(b.get("bbox"), f"{lbl}.bbox", problems)

            lines = b.get("lines")
            if not isinstance(lines, list) or not lines:
                problems.append(f"{lbl}.lines: expected non-empty list")
            else:
                for li, ln in enumerate(lines):
                    _check_bbox(ln, f"{lbl}.lines[{li}]", problems)

            _check_char_ranges(b.get("chars"), f"{lbl}.chars", problems, allow_empty=False)
            if _is_int(pg) and isinstance(b.get("chars"), list):
                for rng in b["chars"]:
                    if isinstance(rng, list) and len(rng) == 2 and _is_int(rng[0]) and _is_int(rng[1]):
                        page_char_ranges.setdefault(pg, []).append((rng[0], rng[1], str(bid)))

            if not isinstance(b.get("text"), str):
                problems.append(f"{lbl}.text: expected str")
            if not isinstance(b.get("font"), str):
                problems.append(f"{lbl}.font: expected str")
            if not _is_num(b.get("size")) or b["size"] <= 0:
                problems.append(f"{lbl}.size: expected positive number")
            if not isinstance(b.get("bold"), bool):
                problems.append(f"{lbl}.bold: expected bool")
            if not isinstance(b.get("italic"), bool):
                problems.append(f"{lbl}.italic: expected bool")

            col = b.get("color")
            if (
                not isinstance(col, list)
                or len(col) != 3
                or not all(_is_int(c) and 0 <= c <= 255 for c in col)
            ):
                problems.append(f"{lbl}.color: expected [r, g, b] ints in 0..255")

            if b.get("align") not in _ALIGNS:
                problems.append(f"{lbl}.align: invalid {b.get('align')!r}")
            if b.get("column") not in (0, 1, None):
                problems.append(f"{lbl}.column: expected 0, 1, or null")

            order = b.get("order")
            if not _is_int(order) or order < 0:
                problems.append(f"{lbl}.order: expected non-negative int")
            elif order in orders:
                problems.append(f"{lbl}.order: duplicate order {order}")
            else:
                orders.add(order)

            if classified:
                kind = b.get("kind")
                if kind not in _ALL_KINDS:
                    problems.append(f"{lbl}.kind: invalid {kind!r}")
                conf = b.get("confidence")
                if not _is_num(conf) or not (0.0 <= conf <= 1.0):
                    problems.append(f"{lbl}.confidence: expected number in [0, 1]")
                tr = b.get("translate")
                if not isinstance(tr, bool):
                    problems.append(f"{lbl}.translate: expected bool")
                elif tr:
                    if not isinstance(b.get("segment"), str) or not b["segment"]:
                        problems.append(f"{lbl}.segment: expected non-empty str when translate=True")
                    box = b.get("box")
                    if not isinstance(box, dict):
                        problems.append(f"{lbl}.box: expected dict when translate=True")
                    else:
                        for k in ("x0", "x1", "y1", "y0_min"):
                            if not _is_num(box.get(k)):
                                problems.append(f"{lbl}.box.{k}: expected number")
                        if (
                            _is_num(box.get("x0"))
                            and _is_num(box.get("x1"))
                            and box["x0"] >= box["x1"]
                        ):
                            problems.append(f"{lbl}.box: x0 >= x1")
                        if (
                            _is_num(box.get("y0_min"))
                            and _is_num(box.get("y1"))
                            and box["y0_min"] > box["y1"] + 1e-6
                        ):
                            problems.append(f"{lbl}.box: y0_min > y1")

        # Verify no overlapping char ranges across blocks on any page (R4)
        for pg, rngs in page_char_ranges.items():
            rngs.sort(key=lambda r: (r[0], r[1]))
            for (s1, e1, b1), (s2, e2, b2) in zip(rngs, rngs[1:]):
                if s2 < e1:
                    problems.append(
                        f"page {pg}: overlapping char ranges [{s1},{e1}) ({b1}) and [{s2},{e2}) ({b2})"
                    )
                    break

    # links
    links = layout.get("links")
    if not isinstance(links, list):
        problems.append("links: expected list")
    else:
        for li, lk in enumerate(links):
            lbl = f"links[{li}]"
            if not isinstance(lk, dict):
                problems.append(f"{lbl}: expected dict")
                continue
            pg = lk.get("page")
            if not _is_int(pg) or not (0 <= pg < n_pages):
                problems.append(f"{lbl}.page: out of range {pg!r}")
            _check_bbox(lk.get("rect"), f"{lbl}.rect", problems)
            dest = lk.get("dest")
            if dest is not None:
                if not isinstance(dest, dict):
                    problems.append(f"{lbl}.dest: expected dict or null")
                else:
                    if not _is_int(dest.get("page")) or dest["page"] < 0:
                        problems.append(f"{lbl}.dest.page: expected int >= 0")
                    if dest.get("x") is not None and not _is_num(dest.get("x")):
                        problems.append(f"{lbl}.dest.x: expected number or null")
                    if dest.get("y") is not None and not _is_num(dest.get("y")):
                        problems.append(f"{lbl}.dest.y: expected number or null")
            uri = lk.get("uri")
            if uri is not None and not isinstance(uri, str):
                problems.append(f"{lbl}.uri: expected str or null")
            _check_char_ranges(lk.get("chars"), f"{lbl}.chars", problems, allow_empty=True)

    # segments & kept
    segments = layout.get("segments")
    if not isinstance(segments, list):
        problems.append("segments: expected list")
    elif classified:
        seg_ids: set[str] = set()
        for si, seg in enumerate(segments):
            lbl = f"segments[{si}]"
            if not isinstance(seg, dict):
                problems.append(f"{lbl}: expected dict")
                continue
            sid = seg.get("id")
            if not isinstance(sid, str) or not sid:
                problems.append(f"{lbl}.id: expected non-empty str")
            elif sid in seg_ids:
                problems.append(f"{lbl}.id: duplicate segment id {sid!r}")
            else:
                seg_ids.add(sid)
            if seg.get("kind") not in _TRANSLATED_KINDS:
                problems.append(f"{lbl}.kind: invalid {seg.get('kind')!r}")
            sblks = seg.get("blocks")
            if not isinstance(sblks, list) or not sblks:
                problems.append(f"{lbl}.blocks: expected non-empty list")
            else:
                for bid in sblks:
                    if bid not in block_ids:
                        problems.append(f"{lbl}.blocks: unknown block id {bid!r}")
            if not isinstance(seg.get("text"), str):
                problems.append(f"{lbl}.text: expected str")
            if not isinstance(seg.get("ph"), dict):
                problems.append(f"{lbl}.ph: expected dict")
            if not _is_int(seg.get("budget")) or seg["budget"] < 0:
                problems.append(f"{lbl}.budget: expected int >= 0")
            cmap = seg.get("cmap")
            if cmap is not None:
                if not isinstance(cmap, list):
                    problems.append(f"{lbl}.cmap: expected list")
                else:
                    for ci, run in enumerate(cmap):
                        if (
                            not isinstance(run, list)
                            or len(run) < 4
                            or not _is_int(run[0])
                            or not _is_int(run[1])
                            or not _is_int(run[2])
                            or not _is_int(run[3])
                            or run[0] < 0
                            or run[0] > run[1]
                            or not (0 <= run[2] < n_pages)
                            or run[3] < 0
                        ):
                            problems.append(f"{lbl}.cmap[{ci}]: invalid run {run!r}")

    kept = layout.get("kept")
    if not isinstance(kept, list):
        problems.append("kept: expected list")
    elif classified:
        for ki, item in enumerate(kept):
            lbl = f"kept[{ki}]"
            if not isinstance(item, dict):
                problems.append(f"{lbl}: expected dict")
                continue
            if item.get("block") not in block_ids:
                problems.append(f"{lbl}.block: unknown block id {item.get('block')!r}")
            if not isinstance(item.get("reason"), str) or not item["reason"]:
                problems.append(f"{lbl}.reason: expected non-empty str")

    return problems


def save(layout: dict[str, Any], path: Path) -> None:
    """Atomically write ``layout`` as UTF-8 JSON."""
    write_json(Path(path), layout)


def load(path: Path) -> dict[str, Any]:
    """Read a layout JSON file."""
    data = read_json(Path(path))
    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return data

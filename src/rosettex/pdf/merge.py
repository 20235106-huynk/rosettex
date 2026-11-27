"""Merge a LuaLaTeX overlay PDF onto a stripped base PDF and relocate links."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pypdf
from pypdf.generic import (
    ArrayObject,
    DecodedStreamObject,
    DictionaryObject,
    FloatObject,
    IndirectObject,
    NameObject,
    NumberObject,
)

from rosettex.pdf.stream import open_writer



def overlay_matrix(page: pypdf.PageObject) -> list[float]:
    """Return ``[a, b, c, d, e, f]`` mapping overlay space to layout space for ``page``.

    Accounts for ``/Rotate`` (mod 360, including inherited ``/Rotate`` from ``/Pages``)
    and the ``/CropBox`` origin.
    """
    cb = page.cropbox
    cx0 = float(cb.left)
    cy0 = float(cb.bottom)
    cx1 = float(cb.right)
    cy1 = float(cb.top)
    rot = int(page.rotation) % 360
    if rot == 0:
        return [1.0, 0.0, 0.0, 1.0, cx0, cy0]
    if rot == 90:
        return [0.0, 1.0, -1.0, 0.0, cx1, cy0]
    if rot == 180:
        return [-1.0, 0.0, 0.0, -1.0, cx1, cy1]
    if rot == 270:
        return [0.0, -1.0, 1.0, 0.0, cx0, cy1]
    raise ValueError(f"Unsupported page rotation: {rot}")


def _transform_rect(rect: list[float], matrix: list[float]) -> list[float]:
    """Transform an axis-aligned ``[x0, y0, x1, y1]`` rect by a 6-element affine matrix."""
    x0, y0, x1, y1 = rect
    a, b, c, d, e, f = matrix
    xs = (a * x0 + c * y0 + e, a * x1 + c * y0 + e, a * x1 + c * y1 + e, a * x0 + c * y1 + e)
    ys = (b * x0 + d * y0 + f, b * x1 + d * y0 + f, b * x1 + d * y1 + f, b * x0 + d * y1 + f)
    return [min(xs), min(ys), max(xs), max(ys)]


def _merge_intervals(intervals: list[tuple[int, int]]) -> list[tuple[int, int]]:
    if not intervals:
        return []
    ordered = sorted(intervals)
    merged: list[tuple[int, int]] = [ordered[0]]
    for s, e in ordered[1:]:
        ps, pe = merged[-1]
        if s <= pe:
            if e > pe:
                merged[-1] = (ps, e)
        else:
            merged.append((s, e))
    return merged


def _intervals_intersect(a: list[tuple[int, int]], b: list[tuple[int, int]]) -> bool:
    """Return True if sorted/merged interval lists ``a`` and ``b`` intersect."""
    i = j = 0
    while i < len(a) and j < len(b):
        as_, ae = a[i]
        bs, be = b[j]
        if as_ < be and bs < ae:
            return True
        if ae <= bs:
            i += 1
        else:
            j += 1
    return False


def translated_links(layout: dict[str, Any]) -> list[int]:
    """Return indices into ``layout["links"]`` whose ``chars`` intersect translated blocks."""
    per_page: dict[int, list[tuple[int, int]]] = {}
    for blk in layout.get("blocks") or []:
        if blk.get("translate") is not True:
            continue
        pg = blk.get("page")
        if pg is None:
            continue
        bucket = per_page.setdefault(int(pg), [])
        for rng in blk.get("chars") or []:
            if len(rng) == 2 and rng[0] < rng[1]:
                bucket.append((int(rng[0]), int(rng[1])))

    merged_by_page = {pg: _merge_intervals(ivs) for pg, ivs in per_page.items()}

    result: list[int] = []
    for idx, lk in enumerate(layout.get("links") or []):
        pg = lk.get("page")
        if pg is None or int(pg) not in merged_by_page:
            continue
        lk_ivs = _merge_intervals(
            [(int(r[0]), int(r[1])) for r in (lk.get("chars") or []) if len(r) == 2 and r[0] < r[1]]
        )
        if lk_ivs and _intervals_intersect(lk_ivs, merged_by_page[int(pg)]):
            result.append(idx)
    return result


def _ensure_private_xobject_dict(page: pypdf.PageObject, writer: pypdf.PdfWriter) -> DictionaryObject:
    """Return a page-private ``/Resources /XObject`` dict, cloning shared indirect dicts."""
    raw_res = page.raw_get("/Resources") if "/Resources" in page else None
    if raw_res is None:
        res_dict = DictionaryObject()
        page[NameObject("/Resources")] = res_dict
    elif isinstance(raw_res, IndirectObject):
        res_dict = DictionaryObject(raw_res.get_object())
        page[NameObject("/Resources")] = res_dict
    else:
        res_dict = raw_res

    raw_xo = res_dict.raw_get("/XObject") if "/XObject" in res_dict else None
    if raw_xo is None:
        xo_dict = DictionaryObject()
        res_dict[NameObject("/XObject")] = xo_dict
    elif isinstance(raw_xo, IndirectObject):
        xo_dict = DictionaryObject(raw_xo.get_object())
        res_dict[NameObject("/XObject")] = xo_dict
    else:
        xo_dict = raw_xo
    return xo_dict


def _ensure_private_annots(page: pypdf.PageObject) -> ArrayObject:
    """Return a page-private ``/Annots`` array, copying if it was an IndirectObject."""
    if "/Annots" not in page:
        arr = ArrayObject()
        page[NameObject("/Annots")] = arr
        return arr
    raw_ann = page.raw_get("/Annots")
    if isinstance(raw_ann, IndirectObject):
        arr = ArrayObject(raw_ann.get_object())
        page[NameObject("/Annots")] = arr
        return arr
    return raw_ann


def _norm_rect(rect_obj: Any) -> list[float]:
    vals = [float(v.get_object()) for v in rect_obj]
    return [min(vals[0], vals[2]), min(vals[1], vals[3]), max(vals[0], vals[2]), max(vals[1], vals[3])]


def _match_page_links(
    writer: pypdf.PdfWriter,
    layout: dict[str, Any],
    link_indices: list[int],
) -> dict[int, tuple[int, int]]:
    """Map each ``lk_idx`` in ``link_indices`` to ``(page_idx, annot_idx)`` in ``writer``."""
    links = layout.get("links") or []
    by_page: dict[int, list[int]] = {}
    for lk_idx in link_indices:
        if 0 <= lk_idx < len(links):
            pg = int(links[lk_idx]["page"])
            by_page.setdefault(pg, []).append(lk_idx)

    matched: dict[int, tuple[int, int]] = {}
    for pg, idxs in by_page.items():
        if not (0 <= pg < len(writer.pages)):
            continue
        page = writer.pages[pg]
        annots_raw = page.get("/Annots")
        if annots_raw is None:
            continue
        annots = annots_raw.get_object()
        candidates: list[tuple[int, list[float]]] = []
        for a_idx, ref in enumerate(annots):
            a_obj = ref.get_object()
            if isinstance(a_obj, DictionaryObject) and a_obj.get("/Subtype") == "/Link" and "/Rect" in a_obj:
                candidates.append((a_idx, _norm_rect(a_obj["/Rect"])))

        used: set[int] = set()
        for lk_idx in idxs:
            target = [float(v) for v in links[lk_idx]["rect"]]
            best_a_idx: int | None = None
            best_err = 0.5
            for a_idx, c_rect in candidates:
                if a_idx in used:
                    continue
                err = max(abs(c_rect[k] - target[k]) for k in range(4))
                if err < best_err:
                    best_err = err
                    best_a_idx = a_idx
            if best_a_idx is not None:
                used.add(best_a_idx)
                matched[lk_idx] = (pg, best_a_idx)
    return matched


def _clone_link_annot(
    src_annot: DictionaryObject,
    new_rect: list[float],
    page: pypdf.PageObject,
    writer: pypdf.PdfWriter,
) -> IndirectObject:
    """Create a new ``/Link`` annotation cloning ``/A`` or ``/Dest`` from ``src_annot``."""
    annot = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Annot"),
            NameObject("/Subtype"): NameObject("/Link"),
            NameObject("/Rect"): ArrayObject([FloatObject(v) for v in new_rect]),
        }
    )
    for key in ("/A", "/Dest", "/Border", "/F", "/H", "/C", "/BS"):
        if key in src_annot:
            raw_val = src_annot.raw_get(key)
            if isinstance(raw_val, (IndirectObject, DictionaryObject, ArrayObject)):
                annot[NameObject(key)] = raw_val.clone(writer)
            else:
                annot[NameObject(key)] = raw_val
    if "/Border" not in annot:
        annot[NameObject("/Border")] = ArrayObject([NumberObject(0), NumberObject(0), NumberObject(0)])
    if page.indirect_reference is not None:
        annot[NameObject("/P")] = page.indirect_reference
    return writer._add_object(annot)


def merge(
    stripped: Path,
    overlay: Path,
    out: Path,
    layout: dict[str, Any],
    link_pos: list[dict[str, Any]],
) -> dict[str, Any]:
    """Merge ``overlay`` pages onto ``stripped`` pages and relocate/split/delete links.

    Returns ``{"pages": int, "links_moved": int, "links_split": int,
    "links_deleted": int, "seconds": float}``.
    """
    t0 = time.perf_counter()
    writer = open_writer(stripped)
    ov_reader = pypdf.PdfReader(str(overlay))
    if ov_reader.is_encrypted:
        ov_reader.decrypt("")

    n_pages = len(writer.pages)
    if len(ov_reader.pages) != n_pages:
        raise ValueError(
            f"Page count mismatch: base has {n_pages} pages, overlay has {len(ov_reader.pages)}"
        )

    # Cache overlay_matrix per page before modifying anything
    matrices = [overlay_matrix(writer.pages[i]) for i in range(n_pages)]

    # 1. Stamp non-empty overlay pages as Form XObjects wrapped outside q...Q
    q_ref: IndirectObject | None = None
    for i in range(n_pages):
        ov_page = ov_reader.pages[i]
        ov_contents = ov_page.get_contents()
        ov_data = ov_contents.get_data() if ov_contents is not None else b""
        if not ov_data.strip():
            continue

        if q_ref is None:
            q_stream = DecodedStreamObject()
            q_stream.set_data(b"q\n")
            q_ref = writer._add_object(q_stream)

        base_page = writer.pages[i]
        ov_cb = ov_page.cropbox

        form = DecodedStreamObject()
        form[NameObject("/Type")] = NameObject("/XObject")
        form[NameObject("/Subtype")] = NameObject("/Form")
        form[NameObject("/FormType")] = NumberObject(1)
        form[NameObject("/BBox")] = ArrayObject(
            [
                FloatObject(float(ov_cb.left)),
                FloatObject(float(ov_cb.bottom)),
                FloatObject(float(ov_cb.right)),
                FloatObject(float(ov_cb.top)),
            ]
        )
        ov_res = ov_page.get("/Resources")
        if ov_res is not None:
            form[NameObject("/Resources")] = ov_res.clone(writer)
        else:
            form[NameObject("/Resources")] = DictionaryObject()
        form.set_data(ov_data)
        form_ref = writer._add_object(form.flate_encode())

        priv_xo = _ensure_private_xobject_dict(base_page, writer)
        ov_name = "/RxOv"
        suffix = 0
        while ov_name in priv_xo:
            suffix += 1
            ov_name = f"/RxOv{suffix}"
        priv_xo[NameObject(ov_name)] = form_ref

        a, b, c, d, e, f = matrices[i]
        tail = DecodedStreamObject()
        tail.set_data(
            f"Q\nq {a:.6g} {b:.6g} {c:.6g} {d:.6g} {e:.6g} {f:.6g} cm {ov_name} Do Q\n".encode("ascii")
        )
        tail_ref = writer._add_object(tail)

        if "/Contents" not in base_page:
            base_page[NameObject("/Contents")] = ArrayObject([q_ref, tail_ref])
        else:
            existing_raw = base_page.raw_get("/Contents")
            existing_obj = existing_raw.get_object()
            if isinstance(existing_obj, ArrayObject):
                new_arr = ArrayObject([q_ref])
                new_arr.extend(existing_obj)
                new_arr.append(tail_ref)
                base_page[NameObject("/Contents")] = new_arr
            else:
                base_page[NameObject("/Contents")] = ArrayObject([q_ref, existing_raw, tail_ref])

    # 2. Relocate, split, or delete translated links
    tl_indices = translated_links(layout)
    matched = _match_page_links(writer, layout, tl_indices)

    pos_by_link: dict[int, list[tuple[int, list[float]]]] = {}
    for entry in link_pos:
        lk_idx = int(entry["link"])
        pg = int(entry["page"])
        for r in entry.get("rects") or []:
            pos_by_link.setdefault(lk_idx, []).append((pg, [float(v) for v in r]))

    links_moved = 0
    links_split = 0
    links_deleted = 0

    to_delete: dict[int, set[int]] = {}
    to_create: list[tuple[int, DictionaryObject, list[float]]] = []
    relocated_in_place: set[tuple[int, int]] = set()

    for lk_idx in tl_indices:
        if lk_idx not in matched:
            continue
        src_pg, a_idx = matched[lk_idx]
        src_annot = writer.pages[src_pg]["/Annots"].get_object()[a_idx].get_object()
        rects = pos_by_link.get(lk_idx)
        if not rects:
            to_delete.setdefault(src_pg, set()).add(a_idx)
            links_deleted += 1
            continue

        first_pg, first_ov_rect = rects[0]
        first_layout_rect = _transform_rect(first_ov_rect, matrices[first_pg])
        if first_pg == src_pg:
            src_annot[NameObject("/Rect")] = ArrayObject([FloatObject(v) for v in first_layout_rect])
            if "/QuadPoints" in src_annot:
                del src_annot["/QuadPoints"]
            relocated_in_place.add((src_pg, a_idx))
        else:
            to_delete.setdefault(src_pg, set()).add(a_idx)
            to_create.append((first_pg, src_annot, first_layout_rect))

        for extra_pg, extra_ov_rect in rects[1:]:
            extra_layout_rect = _transform_rect(extra_ov_rect, matrices[extra_pg])
            to_create.append((extra_pg, src_annot, extra_layout_rect))
            links_split += 1

        links_moved += 1

    # R1 guard: delete any non-relocated /Link annot whose rect overlaps a translated block area
    tr_bboxes_by_page: dict[int, list[list[float]]] = {}
    for blk in layout.get("blocks") or []:
        if blk.get("translate") is True and blk.get("bbox"):
            pg = blk.get("page")
            if pg is not None:
                tr_bboxes_by_page.setdefault(int(pg), []).append([float(v) for v in blk["bbox"]])

    for pg, tr_bbs in tr_bboxes_by_page.items():
        if not (0 <= pg < n_pages):
            continue
        annots_raw = writer.pages[pg].get("/Annots")
        if annots_raw is None:
            continue
        annots = annots_raw.get_object()
        del_on_pg = to_delete.get(pg, set())
        for a_idx, ref in enumerate(annots):
            if (pg, a_idx) in relocated_in_place or a_idx in del_on_pg:
                continue
            a_obj = ref.get_object()
            if not (isinstance(a_obj, DictionaryObject) and a_obj.get("/Subtype") == "/Link" and "/Rect" in a_obj):
                continue
            a_rect = _norm_rect(a_obj["/Rect"])
            if any(
                min(a_rect[2], tb[2]) > max(a_rect[0], tb[0])
                and min(a_rect[3], tb[3]) > max(a_rect[1], tb[1])
                for tb in tr_bbs
            ):
                to_delete.setdefault(pg, set()).add(a_idx)
                links_deleted += 1

    for pg, del_idxs in to_delete.items():
        page = writer.pages[pg]
        annots = _ensure_private_annots(page)
        page[NameObject("/Annots")] = ArrayObject(
            [item for idx, item in enumerate(annots) if idx not in del_idxs]
        )

    for target_pg, template_annot, new_rect in to_create:
        page = writer.pages[target_pg]
        ref = _clone_link_annot(template_annot, new_rect, page, writer)
        _ensure_private_annots(page).append(ref)

    writer.compress_identical_objects(
        remove_duplicates=True, remove_unreferenced=True
    )

    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "wb") as fh:
        writer.write(fh)

    return {
        "pages": n_pages,
        "links_moved": links_moved,
        "links_split": links_split,
        "links_deleted": links_deleted,
        "seconds": round(time.perf_counter() - t0, 4),
    }

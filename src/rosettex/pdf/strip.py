"""Strip translated text and inline-math ops from a PDF with PDFium self-check."""

from __future__ import annotations

import ctypes
import io
import re
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

import pypdf
import pypdfium2 as pdfium
import pypdfium2.raw as raw

from rosettex.pdf import stream

_SUBSET_RE = re.compile(r"^[A-Z]{6}\+")
_CLIP_RENDER_MODES = frozenset({4, 5, 6, 7})


def _clean_font(name: str) -> str:
    return _SUBSET_RE.sub("", name or "").strip()


def _norm_u(ch: str) -> str:
    # PDFium flips line-end hyphen glyphs between U+0002 and U+002D when adjacent text is removed
    return "-" if ch == "\u0002" else ch


def _char_in_ranges(ci: int, ranges: list[list[int]]) -> bool:
    for rng in ranges:
        if rng[0] <= ci < rng[1]:
            return True
    return False


def _read_page_chars_for_check(page: Any) -> list[tuple[str, str, float, float]]:
    """Lightweight PDFium char reader matching extract.py's visible-char criteria.

    Returns list of ``(norm_u, clean_font, orig_x, orig_y)`` in unrotated layout space.
    """
    rotate = (raw.FPDFPage_GetRotation(page.raw) % 4) * 90

    cl, cb, cr, ct = ctypes.c_float(), ctypes.c_float(), ctypes.c_float(), ctypes.c_float()
    has_crop = bool(
        raw.FPDFPage_GetCropBox(
            page.raw, ctypes.byref(cl), ctypes.byref(cb), ctypes.byref(cr), ctypes.byref(ct)
        )
    )
    ml, mb, mr, mt = ctypes.c_float(), ctypes.c_float(), ctypes.c_float(), ctypes.c_float()
    has_media = bool(
        raw.FPDFPage_GetMediaBox(
            page.raw, ctypes.byref(ml), ctypes.byref(mb), ctypes.byref(mr), ctypes.byref(mt)
        )
    )
    if has_crop:
        px0, py0 = min(cl.value, cr.value), min(cb.value, ct.value)
        px1, py1 = max(cl.value, cr.value), max(cb.value, ct.value)
    elif has_media:
        px0, py0 = min(ml.value, mr.value), min(mb.value, mt.value)
        px1, py1 = max(ml.value, mr.value), max(mb.value, mt.value)
    else:
        sw, sh = page.get_size()
        if rotate in (90, 270):
            sw, sh = sh, sw
        px0, py0, px1, py1 = 0.0, 0.0, float(sw), float(sh)

    tp = raw.FPDFText_LoadPage(page.raw)
    if not tp:
        return []
    out: list[tuple[str, str, float, float]] = []
    try:
        n_ch = raw.FPDFText_CountChars(tp)
        rect = raw.FS_RECTF()
        l_, r_, b_, t_ = ctypes.c_double(), ctypes.c_double(), ctypes.c_double(), ctypes.c_double()
        ox, oy = ctypes.c_double(), ctypes.c_double()
        flags = ctypes.c_int()
        fbuf = ctypes.create_string_buffer(256)
        font_cache: dict[str, str] = {}

        i = 0
        while i < n_ch:
            if raw.FPDFText_IsGenerated(tp, i):
                i += 1
                continue
            uni = raw.FPDFText_GetUnicode(tp, i)
            if uni <= 0:
                i += 1
                continue

            if 0xD800 <= uni <= 0xDBFF and i + 1 < n_ch:
                uni2 = raw.FPDFText_GetUnicode(tp, i + 1)
                if 0xDC00 <= uni2 <= 0xDFFF:
                    cp = 0x10000 + ((uni - 0xD800) << 10) + (uni2 - 0xDC00)
                    ch = chr(cp)
                    step = 2
                else:
                    i += 1
                    continue
            elif 0xD800 <= uni <= 0xDFFF:
                i += 1
                continue
            else:
                cp = uni
                ch = chr(cp)
                step = 1

            if ch.isspace():
                i += step
                continue
            if cp < 32 and cp != 2:
                i += step
                continue

            ok_box = bool(raw.FPDFText_GetLooseCharBox(tp, i, ctypes.byref(rect)))
            if ok_box:
                x0, x1 = (rect.left, rect.right) if rect.left <= rect.right else (rect.right, rect.left)
                y0, y1 = (rect.bottom, rect.top) if rect.bottom <= rect.top else (rect.top, rect.bottom)
            else:
                ok_box = bool(
                    raw.FPDFText_GetCharBox(
                        tp, i, ctypes.byref(l_), ctypes.byref(r_), ctypes.byref(b_), ctypes.byref(t_)
                    )
                )
                if ok_box:
                    x0, x1 = (l_.value, r_.value) if l_.value <= r_.value else (r_.value, l_.value)
                    y0, y1 = (b_.value, t_.value) if b_.value <= t_.value else (t_.value, b_.value)
                else:
                    x0 = y0 = x1 = y1 = 0.0

            has_orig = bool(raw.FPDFText_GetCharOrigin(tp, i, ctypes.byref(ox), ctypes.byref(oy)))
            orig_x = ox.value if has_orig else x0
            orig_y = oy.value if has_orig else y0

            if not ok_box or (x1 - x0 < 0.01 and y1 - y0 < 0.01):
                if cp == 2 and has_orig:
                    x0, y0, x1, y1 = orig_x, orig_y, orig_x + 1.0, orig_y + 1.0
                else:
                    i += step
                    continue

            if x1 < px0 - 50.0 or x0 > px1 + 50.0 or y1 < py0 - 50.0 or y0 > py1 + 50.0:
                i += step
                continue

            nbytes = raw.FPDFText_GetFontInfo(tp, i, fbuf, 256, ctypes.byref(flags))
            if nbytes > 256:
                big_buf = ctypes.create_string_buffer(nbytes)
                raw.FPDFText_GetFontInfo(tp, i, big_buf, nbytes, ctypes.byref(flags))
                raw_fname = big_buf.value.decode("utf-8", errors="replace")
            elif nbytes > 0:
                raw_fname = fbuf.value.decode("utf-8", errors="replace")
            else:
                raw_fname = ""

            clean_f = font_cache.get(raw_fname)
            if clean_f is None:
                clean_f = _clean_font(raw_fname)
                font_cache[raw_fname] = clean_f

            out.append((_norm_u(ch), clean_f, orig_x, orig_y))
            i += step
    finally:
        raw.FPDFText_ClosePage(tp)
    return out


def _self_check_page(
    orig_kept: list[dict[str, Any]],
    stripped_chars: list[tuple[str, str, float, float]],
    tol_pt: float = 0.1,
) -> bool:
    """Return True iff every kept char matches within tol_pt (same font & char) and 0 residual chars remain."""
    if len(stripped_chars) != len(orig_kept):
        return False

    by_key: dict[tuple[str, str], list[tuple[float, float]]] = defaultdict(list)
    for nu, fn, sx, sy in stripped_chars:
        by_key[(nu, fn)].append((sx, sy))

    used: dict[tuple[str, str], list[bool]] = {k: [False] * len(v) for k, v in by_key.items()}
    tol2 = tol_pt * tol_pt

    for c in orig_kept:
        key = (_norm_u(c["u"]), c["font"])
        cands = by_key.get(key)
        if not cands:
            return False
        ox, oy = float(c["origin"][0]), float(c["origin"][1])
        u_used = used[key]
        best_idx = -1
        best_d2 = tol2 + 1.0
        for idx, (sx, sy) in enumerate(cands):
            if u_used[idx]:
                continue
            d2 = (sx - ox) ** 2 + (sy - oy) ** 2
            if d2 <= tol2 and d2 < best_d2:
                best_d2 = d2
                best_idx = idx
                if d2 < 1e-6:
                    break
        if best_idx < 0:
            return False
        u_used[best_idx] = True

    return True


def strip(
    pdf: Path,
    layout: dict[str, Any],
    chars: list[list[dict[str, Any]]],
    out: Path,
) -> dict[str, Any]:
    """Remove glyphs of translated blocks and non-text ops of their ``m`` placeholders, then self-check.

    Mutates ``layout`` in place: any segment that cannot be stripped safely has all its blocks set to
    ``translate = False``, is removed from ``layout["segments"]``, and each of its blocks is recorded in
    ``layout["kept"]`` with ``reason`` in ``{"unmatched_glyph", "clip_text", "mixed_actualtext", "self_check"}``.
    """
    t0 = time.perf_counter()
    n_pages = len(chars)

    blocks_by_id: dict[str, dict[str, Any]] = {b["id"]: b for b in layout.get("blocks") or []}
    segments: list[dict[str, Any]] = list(layout.get("segments") or [])
    seg_by_id: dict[str, dict[str, Any]] = {s["id"]: s for s in segments}
    kept_list: list[dict[str, Any]] = layout.setdefault("kept", [])

    # Map each initially-translated block to its segment id (or a synthetic solo key if detached)
    block_to_seg: dict[str, str] = {}
    seg_blocks: dict[str, list[str]] = {}
    for seg in segments:
        sid = seg["id"]
        bids = [bid for bid in seg.get("blocks") or [] if bid in blocks_by_id]
        seg_blocks[sid] = bids
        for bid in bids:
            block_to_seg[bid] = sid

    # Track which pages originally had at least one translated block
    originally_targeted_pages: set[int] = set()
    for blk in layout.get("blocks") or []:
        if blk.get("translate") is True:
            pg = int(blk["page"])
            originally_targeted_pages.add(pg)
            bid = blk["id"]
            if bid not in block_to_seg:
                solo_id = f"__solo_{bid}"
                block_to_seg[bid] = solo_id
                seg_blocks[solo_id] = [bid]

    active_segs: set[str] = set(seg_blocks.keys())
    demoted_segs: dict[str, str] = {}
    page_demotion_reasons: dict[int, list[str]] = defaultdict(list)

    def _demote_segment(sid: str, reason: str) -> set[int]:
        """Demote a segment (and all its blocks across pages). Returns affected page indices."""
        if sid not in active_segs:
            return set()
        active_segs.remove(sid)
        demoted_segs[sid] = reason
        affected_pages: set[int] = set()
        for bid in seg_blocks.get(sid, []):
            blk = blocks_by_id.get(bid)
            if blk is not None:
                blk["translate"] = False
                if "segment" in blk:
                    blk["segment"] = None
                pg = int(blk["page"])
                affected_pages.add(pg)
                if reason not in page_demotion_reasons[pg]:
                    page_demotion_reasons[pg].append(reason)
            kept_list.append({"block": bid, "reason": reason})
        return affected_pages

    # Precompute which PDFium char indices belong to each translated block
    block_char_indices: dict[str, list[int]] = {}
    blocks_on_page: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for blk in layout.get("blocks") or []:
        if blk.get("translate") is True:
            pg = int(blk["page"])
            blocks_on_page[pg].append(blk)

    for pg, blks in blocks_on_page.items():
        p_chars = chars[pg]
        for blk in blks:
            ranges = blk.get("chars") or []
            if not ranges:
                block_char_indices[blk["id"]] = []
                continue
            lo = min(r[0] for r in ranges)
            hi = max(r[1] for r in ranges)
            cis = [c["i"] for c in p_chars if lo <= c["i"] < hi and _char_in_ranges(c["i"], ranges)]
            block_char_indices[blk["id"]] = cis

    # Open writer, parse + match every page that has translated blocks (or all pages? Only targeted pages need parse!)
    writer: pypdf.PdfWriter = stream.open_writer(pdf)

    def _parse_and_match_pages(
        w: pypdf.PdfWriter, pages_to_parse: set[int]
    ) -> tuple[dict[int, stream.PageStream], dict[int, dict[int, stream.GlyphKey]]]:
        ps_map: dict[int, stream.PageStream] = {}
        match_map: dict[int, dict[int, stream.GlyphKey]] = {}
        for pg in sorted(pages_to_parse):
            ps = stream.parse(w, pg)
            m_map, _unm = stream.match(ps, chars[pg])
            ps_map[pg] = ps
            match_map[pg] = m_map
        return ps_map, match_map

    ps_by_page, match_by_page = _parse_and_match_pages(writer, originally_targeted_pages)

    # Phase 1: pre-edit R1 checks (unmatched_glyph, clip_text)
    for pg in sorted(originally_targeted_pages):
        ps = ps_by_page[pg]
        m_map = match_by_page[pg]
        glyph_by_key = {g.key: g for g in ps.glyphs}

        for blk in blocks_on_page.get(pg, []):
            sid = block_to_seg[blk["id"]]
            if sid not in active_segs:
                continue
            unmatched = False
            clip_text = False
            for ci in block_char_indices.get(blk["id"], []):
                gk = m_map.get(ci)
                if gk is None:
                    unmatched = True
                    break
                g = glyph_by_key.get(gk)
                if g is not None and g.mode in _CLIP_RENDER_MODES:
                    clip_text = True
            if unmatched:
                _demote_segment(sid, "unmatched_glyph")
            elif clip_text:
                _demote_segment(sid, "clip_text")

    def _compute_page_drops(
        ps_map: dict[int, stream.PageStream],
        match_map: dict[int, dict[int, stream.GlyphKey]],
    ) -> tuple[
        dict[int, set[stream.GlyphKey]],
        dict[int, set[stream.OpKey]],
        dict[int, set[int]],
    ]:
        """Compute per-page drop_glyphs, drop_ops, and removed char indices; demote mixed_actualtext."""
        while True:
            drop_g_by_page: dict[int, set[stream.GlyphKey]] = {}
            removed_ci_by_page: dict[int, set[int]] = {}
            glyph_owner_seg: dict[int, dict[stream.GlyphKey, set[str]]] = {}

            for pg in sorted(ps_map.keys()):
                m_map = match_map[pg]
                dg: set[stream.GlyphKey] = set()
                rci: set[int] = set()
                owners: dict[stream.GlyphKey, set[str]] = defaultdict(set)
                for blk in blocks_on_page.get(pg, []):
                    sid = block_to_seg[blk["id"]]
                    if sid not in active_segs:
                        continue
                    for ci in block_char_indices.get(blk["id"], []):
                        gk = m_map.get(ci)
                        if gk is not None:
                            dg.add(gk)
                            rci.add(ci)
                            owners[gk].add(sid)
                if dg:
                    ps = ps_map[pg]
                    # Drop overstruck \pmb / fake-bold duplicate glyphs (same code & font within 0.6 pt,
                    # not matched to any kept PDFium char) that PDFium deduplicated into one char.
                    kept_gks = {gk for ci, gk in m_map.items() if ci not in rci}
                    dropped_by_cf: dict[tuple[int, str], list[tuple[float, float]]] = defaultdict(list)
                    for g in ps.glyphs:
                        if g.key in dg:
                            dropped_by_cf[(g.code, g.font)].append(g.origin)
                    for g in ps.glyphs:
                        if g.key in dg or g.key in kept_gks:
                            continue
                        origins = dropped_by_cf.get((g.code, g.font))
                        if origins:
                            gx, gy = g.origin
                            for ox, oy in origins:
                                if abs(gx - ox) <= 0.6 and abs(gy - oy) <= 0.6:
                                    dg.add(g.key)
                                    break
                    dg |= stream.bridged_spaces(ps, dg)
                drop_g_by_page[pg] = dg
                removed_ci_by_page[pg] = rci
                glyph_owner_seg[pg] = owners

            # Check /ActualText spans for mixed removed/kept glyphs (loop until fixed point)
            newly_demoted = False
            for pg in sorted(ps_map.keys()):
                dg = drop_g_by_page[pg]
                if not dg:
                    continue
                ps = ps_map[pg]
                rci = removed_ci_by_page[pg]
                m_map = match_map[pg]
                kept_gks = {gk for ci, gk in m_map.items() if ci not in rci}
                owners = glyph_owner_seg[pg]
                for sp in ps.spans:
                    if sp["actual_text"] is None or not sp["glyphs"]:
                        continue
                    sp_glyphs = sp["glyphs"]
                    if not any(gk in dg for gk in sp_glyphs):
                        continue
                    # Span has at least one dropped glyph. If any kept PDFium char maps to a glyph
                    # in this span, it mixes removed and kept text -> R1 demote.
                    if any(gk in kept_gks for gk in sp_glyphs):
                        segs_to_demote: set[str] = set()
                        for gk in sp_glyphs:
                            segs_to_demote.update(owners.get(gk, ()))
                        for sid in segs_to_demote:
                            if sid in active_segs:
                                _demote_segment(sid, "mixed_actualtext")
                                newly_demoted = True
                    else:
                        # All visible chars in this /ActualText span are dropped; also drop any
                        # remaining glyphs (collapsed ActualText tail glyphs or spaces) so
                        # stream.edit() strips /ActualText cleanly.
                        dg.update(sp_glyphs)
            if not newly_demoted:
                break

        # Compute drop_ops for remaining active segments' "m" placeholders
        drop_o_by_page: dict[int, set[stream.OpKey]] = {pg: set() for pg in ps_map}
        for sid in active_segs:
            seg = seg_by_id.get(sid)
            if seg is None:
                continue
            for ph_key, ph in (seg.get("ph") or {}).items():
                if not ph_key.startswith("m"):
                    continue
                pg = int(ph["page"])
                if pg in ps_map:
                    drop_o_by_page[pg].update(stream.ops_in_box(ps_map[pg], ph["bbox"]))

        return drop_g_by_page, drop_o_by_page, removed_ci_by_page

    # Phase 2 & 3: edit + in-memory PDFium self-check loop (re-runs only if a page fails self-check)
    final_pdf_bytes: bytes = b""
    final_drop_g: dict[int, set[stream.GlyphKey]] = {}
    final_drop_o: dict[int, set[stream.OpKey]] = {}
    iteration = 0

    while True:
        iteration += 1
        if iteration > 1:
            # Re-open a clean writer so any demoted page (including cross-page segments & Form XObjects)
            # is guaranteed restored to its exact original bytes.
            writer = stream.open_writer(pdf)
            active_pages = {
                int(blk["page"])
                for pg in originally_targeted_pages
                for blk in blocks_on_page.get(pg, [])
                if block_to_seg[blk["id"]] in active_segs
            }
            ps_by_page, match_by_page = _parse_and_match_pages(writer, active_pages)

        drop_g_by_page, drop_o_by_page, removed_ci_by_page = _compute_page_drops(
            ps_by_page, match_by_page
        )

        edited_pages: list[int] = []
        for pg in sorted(originally_targeted_pages):
            dg = drop_g_by_page.get(pg, set())
            do = drop_o_by_page.get(pg, set())
            if dg or do:
                stream.edit(writer, pg, ps_by_page[pg], dg, do)
                edited_pages.append(pg)

        if edited_pages:
            # Prune orphaned pre-edit /Contents streams so stripped.pdf does not bloat
            writer.compress_identical_objects(remove_duplicates=False, remove_unreferenced=True)

        buf = io.BytesIO()
        writer.write(buf)
        final_pdf_bytes = buf.getvalue()
        final_drop_g = drop_g_by_page
        final_drop_o = drop_o_by_page

        if not edited_pages:
            break

        # Self-check every edited page with pypdfium2 from memory bytes
        failed_pages: list[int] = []
        check_doc = pdfium.PdfDocument(final_pdf_bytes)
        try:
            for pg in edited_pages:
                check_page = check_doc[pg]
                try:
                    stripped_chars = _read_page_chars_for_check(check_page)
                finally:
                    check_page.close()
                rci = removed_ci_by_page[pg]
                orig_kept = [c for c in chars[pg] if c["i"] not in rci]
                if not _self_check_page(orig_kept, stripped_chars, tol_pt=0.1):
                    failed_pages.append(pg)
        finally:
            check_doc.close()

        if not failed_pages:
            break

        # Demote all active segments that have any block on a failing page
        any_new_demotion = False
        for pg in failed_pages:
            for blk in blocks_on_page.get(pg, []):
                sid = block_to_seg[blk["id"]]
                if sid in active_segs:
                    _demote_segment(sid, "self_check")
                    any_new_demotion = True
        if not any_new_demotion:
            break

    # Mutate layout["segments"] to remove demoted segments
    layout["segments"] = [s for s in segments if s["id"] in active_segs]

    # Write final stripped PDF
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(final_pdf_bytes)

    # Build per-page summary
    pages_summary: list[dict[str, Any]] = []
    for pg in range(n_pages):
        n_rem = len(final_drop_g.get(pg, set()))
        n_ops = len(final_drop_o.get(pg, set()))
        if pg not in originally_targeted_pages:
            pages_summary.append(
                {
                    "index": pg,
                    "removed": 0,
                    "ops_removed": 0,
                    "status": "untouched",
                    "reason": None,
                }
            )
        elif n_rem > 0 or n_ops > 0:
            pages_summary.append(
                {
                    "index": pg,
                    "removed": n_rem,
                    "ops_removed": n_ops,
                    "status": "stripped",
                    "reason": None,
                }
            )
        else:
            reasons = page_demotion_reasons.get(pg, [])
            pages_summary.append(
                {
                    "index": pg,
                    "removed": 0,
                    "ops_removed": 0,
                    "status": "original",
                    "reason": ",".join(reasons) if reasons else "demoted",
                }
            )

    return {
        "pages": pages_summary,
        "seconds": round(time.perf_counter() - t0, 4),
    }

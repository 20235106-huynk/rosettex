"""PDF text, object, and layout extraction using pypdfium2 (read-only)."""

from __future__ import annotations

import ctypes
import hashlib
import math
import re
import statistics as st
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any

import pypdfium2 as pdfium
import pypdfium2.raw as raw

from rosettex.pdf.schema import SCHEMA

_SUBSET_RE = re.compile(r"^[A-Z]{6}\+")
_MONO_FONT_RE = re.compile(
    r"(?:mono|courier|cmtt|lmtt|txtt|consolas|inconsolata|bera|sourcecode|nimbusmon|typewriter)",
    re.I,
)
_BOLD_FONT_RE = re.compile(r"(?:bold|medi|demi|black|heavy|cmbx|lmbx|ecbx|sfbb|bx\d)", re.I)
_ITAL_FONT_RE = re.compile(r"(?:ital|obli|slant|cmti|cmsl|lmti|ecti|cmmi)", re.I)
_URL_RE = re.compile(r"https?://\S+|www\.\S+")
_CAPTION_RE = re.compile(
    r"^\s*(?:Supplementary\s+)?(?:Figure|Fig\.?|Table|Tab\.?)\s*[A-Z]?\d+[a-z]?(?:\s*[:.\-–—]|\s+[A-Z])",
    re.I,
)
_ALGO_RE = re.compile(r"^\s*(?:Algorithm|Listing|Procedure)\s+\d+\b", re.I)
_NUM_HEAD_RE = re.compile(
    r"^\s*(?:(\d+(?:\.\d+)*)\.?|([IVXLCDM]+)\.|([A-Z](?:\.\d+)*)\.)\s+([A-Z][a-zA-Z0-9\-: /]{1,85})$"
)
_ABSTRACT_HEAD_RE = re.compile(r"^\s*abstract\s*[:.\-–—]?\s*$", re.I)
_WS_RE = re.compile(r"\s+")

_SOFT_HYPHENS = frozenset({"\u0002", "\u00ad"})
_LINE_HYPHENS = frozenset({"-", "\u2010"})


class PdfError(Exception):
    """Raised when a PDF cannot be opened, has no usable text layer, or changes writing direction.

    Known error codes:
    - ``not_pdf``: file does not exist or is not a valid PDF.
    - ``encrypted_pdf``: PDF requires a user password.
    - ``no_text_layer``: PDF has no digital text or text layer is unusable.
    - ``direction_change``: PDF route refuses LTR <-> RTL writing direction change.
    """

    code: str

    def __init__(self, code: str, message: str = "") -> None:
        self.code = code
        super().__init__(message or code)


def _r2(val: float) -> float:
    return round(float(val), 2)


_FONT_STYLE_CACHE: dict[tuple[str, int], tuple[str, bool, bool, bool]] = {}
_MONO_CACHE: dict[str, bool] = {}


def _clean_font(name: str) -> str:
    return _SUBSET_RE.sub("", name or "").strip()


def _is_mono_font(name: str) -> bool:
    res = _MONO_CACHE.get(name)
    if res is None:
        res = bool(_MONO_FONT_RE.search(name))
        _MONO_CACHE[name] = res
    return res


def _font_style(raw_name: str, flags: int) -> tuple[str, bool, bool, bool]:
    key = (raw_name, flags)
    res = _FONT_STYLE_CACHE.get(key)
    if res is None:
        clean = _clean_font(raw_name)
        bold = bool(_BOLD_FONT_RE.search(clean))
        italic = bool(_ITAL_FONT_RE.search(clean)) or bool(flags & (1 << 6))
        mono = _is_mono_font(clean)
        res = (clean, bold, italic, mono)
        _FONT_STYLE_CACHE[key] = res
    return res


def _is_garbage_cp(cp: int) -> bool:
    if cp == 0xFFFD:
        return True
    if 0xE000 <= cp <= 0xF8FF or 0xF0000 <= cp <= 0xFFFFD or 0x100000 <= cp <= 0x10FFFD:
        return True
    if cp < 32 and cp not in (0x02, 0x09, 0x0A, 0x0D):
        return True
    if 0x7F <= cp <= 0x9F:
        return True
    return False


def _apply_matrix(
    m: tuple[float, float, float, float, float, float], x: float, y: float
) -> tuple[float, float]:
    a, b, c, d, e, f = m
    return a * x + c * y + e, b * x + d * y + f


def _mul_matrix(
    m1: tuple[float, float, float, float, float, float],
    m2: tuple[float, float, float, float, float, float],
) -> tuple[float, float, float, float, float, float]:
    a1, b1, c1, d1, e1, f1 = m1
    a2, b2, c2, d2, e2, f2 = m2
    return (
        a1 * a2 + c1 * b2,
        b1 * a2 + d1 * b2,
        a1 * c2 + c1 * d2,
        b1 * c2 + d1 * d2,
        a1 * e2 + c1 * f2 + e1,
        b1 * e2 + d1 * f2 + f1,
    )


def _transform_bounds(
    left: float,
    bottom: float,
    right: float,
    top: float,
    m: tuple[float, float, float, float, float, float],
) -> tuple[float, float, float, float]:
    pts = [_apply_matrix(m, x, y) for x in (left, right) for y in (bottom, top)]
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def _get_obj_bounds(obj: Any) -> tuple[float, float, float, float]:
    left, bottom, right, top = ctypes.c_float(), ctypes.c_float(), ctypes.c_float(), ctypes.c_float()
    raw.FPDFPageObj_GetBounds(
        obj, ctypes.byref(left), ctypes.byref(bottom), ctypes.byref(right), ctypes.byref(top)
    )
    return left.value, bottom.value, right.value, top.value


def _get_obj_matrix(obj: Any) -> tuple[float, float, float, float, float, float]:
    m = raw.FS_MATRIX()
    if raw.FPDFPageObj_GetMatrix(obj, ctypes.byref(m)):
        return (m.a, m.b, m.c, m.d, m.e, m.f)
    return (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def _count_form_subobjs(form_obj: Any, depth: int = 0) -> dict[int, int]:
    counts = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
    if depth > 6:
        return counts
    n = raw.FPDFFormObj_CountObjects(form_obj)
    for idx in range(n):
        sub = raw.FPDFFormObj_GetObject(form_obj, idx)
        otype = raw.FPDFPageObj_GetType(sub)
        counts[otype] = counts.get(otype, 0) + 1
        if otype == raw.FPDF_PAGEOBJ_FORM:
            sub_counts = _count_form_subobjs(sub, depth + 1)
            for k, v in sub_counts.items():
                counts[k] = counts.get(k, 0) + v
    return counts


def _rect_inter(a: list[float], b: list[float]) -> list[float] | None:
    x0 = max(a[0], b[0])
    y0 = max(a[1], b[1])
    x1 = min(a[2], b[2])
    y1 = min(a[3], b[3])
    if x1 <= x0 or y1 <= y0:
        return None
    return [x0, y0, x1, y1]


def _clip_path_bounds(obj: Any) -> tuple[bool, list[float] | None]:
    """Return ``(visible, bounds)`` for ``obj``'s clip path in its parent coordinate space.

    ``visible=False`` means the intersection of clip paths is empty (object is completely clipped away).
    ``bounds=None`` with ``visible=True`` means the object has no clip path.
    """
    cp = raw.FPDFPageObj_GetClipPath(obj)
    if not cp:
        return True, None
    n_paths = raw.FPDFClipPath_CountPaths(cp)
    if n_paths <= 0:
        return True, None
    res: list[float] | None = None
    for pi in range(n_paths):
        n_segs = raw.FPDFClipPath_CountPathSegments(cp, pi)
        if n_segs <= 0:
            continue
        xs: list[float] = []
        ys: list[float] = []
        for si in range(n_segs):
            seg = raw.FPDFClipPath_GetPathSegment(cp, pi, si)
            x, y = ctypes.c_float(), ctypes.c_float()
            if raw.FPDFPathSegment_GetPoint(seg, ctypes.byref(x), ctypes.byref(y)):
                xs.append(x.value)
                ys.append(y.value)
        if xs:
            pb = [min(xs), min(ys), max(xs), max(ys)]
            if res is None:
                res = pb
            else:
                res = _rect_inter(res, pb)
                if res is None:
                    return False, None
    return True, res


def _is_invisible_or_white_fill_path(obj: Any) -> bool:
    """Return True if a path object is neither stroked nor visibly filled (e.g. clip-only or white canvas rect)."""
    fill_mode = ctypes.c_int()
    stroke = raw.FPDF_BOOL()
    if not raw.FPDFPath_GetDrawMode(obj, ctypes.byref(fill_mode), ctypes.byref(stroke)):
        return False
    has_fill = fill_mode.value != raw.FPDF_FILLMODE_NONE
    has_stroke = bool(stroke.value)
    if not has_fill and not has_stroke:
        return True

    stroke_visible = False
    if has_stroke:
        sr, sg, sb, sa = ctypes.c_uint(), ctypes.c_uint(), ctypes.c_uint(), ctypes.c_uint()
        if raw.FPDFPageObj_GetStrokeColor(obj, ctypes.byref(sr), ctypes.byref(sg), ctypes.byref(sb), ctypes.byref(sa)):
            stroke_visible = sa.value > 0 and not (sr.value >= 253 and sg.value >= 253 and sb.value >= 253)
        else:
            stroke_visible = True
    if stroke_visible:
        return False

    if has_fill:
        fr, fg, fb, fa = ctypes.c_uint(), ctypes.c_uint(), ctypes.c_uint(), ctypes.c_uint()
        if raw.FPDFPageObj_GetFillColor(obj, ctypes.byref(fr), ctypes.byref(fg), ctypes.byref(fb), ctypes.byref(fa)):
            if fa.value == 0 or (fr.value >= 253 and fg.value >= 253 and fb.value >= 253):
                return True
    return False


def _tight_image_bounds(
    raw_doc: Any,
    page_raw: Any,
    img_obj: Any,
    bb: list[float],
    ctm: tuple[float, float, float, float, float, float],
    page_w: float,
    page_h: float,
) -> list[float] | None:
    """Tighten a large image's bbox to its non-transparent pixels when it carries an alpha mask (BGRA)."""
    bw = bb[2] - bb[0]
    bh = bb[3] - bb[1]
    if bw <= 0.01 or bh <= 0.01:
        return None
    # Only render-inspect large images that could cover significant page area
    if bw * bh < 0.08 * page_w * page_h and bw < 0.50 * page_w and bh < 0.50 * page_h:
        return bb
    bmp = raw.FPDFImageObj_GetRenderedBitmap(raw_doc, page_raw, img_obj)
    if not bmp:
        return bb
    try:
        fmt = raw.FPDFBitmap_GetFormat(bmp)
        if fmt != raw.FPDFBitmap_BGRA:
            return bb
        w = raw.FPDFBitmap_GetWidth(bmp)
        h = raw.FPDFBitmap_GetHeight(bmp)
        stride = raw.FPDFBitmap_GetStride(bmp)
        buf = raw.FPDFBitmap_GetBuffer(bmp)
        if w <= 0 or h <= 0 or not buf:
            return bb
        arr = ctypes.string_at(buf, stride * h)
        min_x, max_x = w, -1
        min_y, max_y = h, -1
        row_end = w * 4
        for y in range(h):
            off = y * stride
            alphas = arr[off + 3 : off + row_end : 4]
            if max(alphas) <= 5:
                continue
            if y < min_y:
                min_y = y
            max_y = y
            # Scan from left and right for first/last non-transparent pixel
            lx = 0
            while lx < w and alphas[lx] <= 5:
                lx += 1
            rx = w - 1
            while rx >= lx and alphas[rx] <= 5:
                rx -= 1
            if lx < min_x:
                min_x = lx
            if rx > max_x:
                max_x = rx
        if max_x < min_x or max_y < min_y:
            return None
        # PDF images live in the unit square [0, 1] x [0, 1] with bitmap y=0 at v=1 and y=h at v=0.
        # Transform through the full image CTM so flipped/rotated matrices map accurately.
        img_ctm = _mul_matrix(ctm, _get_obj_matrix(img_obj))
        u0 = min_x / w
        u1 = (max_x + 1) / w
        v0 = 1.0 - (max_y + 1) / h
        v1 = 1.0 - min_y / h
        nx0, ny0, nx1, ny1 = _transform_bounds(u0, v0, u1, v1, img_ctm)
        return [nx0, ny0, nx1, ny1]
    finally:
        raw.FPDFBitmap_Destroy(bmp)


def _form_drawn_bounds(
    raw_doc: Any,
    page_raw: Any,
    form_obj: Any,
    ctm: tuple[float, float, float, float, float, float],
    is_top: bool,
    page_clip: list[float] | None,
    page_w: float,
    page_h: float,
    depth: int = 0,
) -> list[float] | None:
    """Compute the drawn extent of a Form XObject's visible content, clipped by its /BBox and clip paths."""
    if depth > 8:
        return None
    vis, fc = _clip_path_bounds(form_obj)
    if not vis:
        return None
    cur_clip = page_clip
    if fc is not None:
        fc_t = list(_transform_bounds(fc[0], fc[1], fc[2], fc[3], ctm)) if not is_top else fc
        cur_clip = _rect_inter(cur_clip, fc_t) if cur_clip is not None else fc_t
        if cur_clip is None:
            return None

    # Clip to the Form XObject's own bounds (includes /BBox clip in PDF space)
    fl, fb, fr, ft = _get_obj_bounds(form_obj)
    if not is_top:
        fl, fb, fr, ft = _transform_bounds(fl, fb, fr, ft, ctm)
    fbox = [min(fl, fr), min(fb, ft), max(fl, fr), max(fb, ft)]
    cur_clip = _rect_inter(cur_clip, fbox) if cur_clip is not None else fbox
    if cur_clip is None:
        return None

    fm = _mul_matrix(ctm, _get_obj_matrix(form_obj))
    n = raw.FPDFFormObj_CountObjects(form_obj)
    xs0: list[float] = []
    ys0: list[float] = []
    xs1: list[float] = []
    ys1: list[float] = []
    for idx in range(n):
        sub = raw.FPDFFormObj_GetObject(form_obj, idx)
        ot = raw.FPDFPageObj_GetType(sub)
        if ot == raw.FPDF_PAGEOBJ_FORM:
            sb = _form_drawn_bounds(raw_doc, page_raw, sub, fm, False, cur_clip, page_w, page_h, depth + 1)
            if sb is not None:
                xs0.append(sb[0])
                ys0.append(sb[1])
                xs1.append(sb[2])
                ys1.append(sb[3])
            continue

        if ot == raw.FPDF_PAGEOBJ_PATH and _is_invisible_or_white_fill_path(sub):
            continue

        svis, sc = _clip_path_bounds(sub)
        if not svis:
            continue

        l, b, r, t = _transform_bounds(*_get_obj_bounds(sub), fm)
        sb_opt: list[float] | None = [min(l, r), min(b, t), max(l, r), max(b, t)]
        if sc is not None:
            sc_t = list(_transform_bounds(sc[0], sc[1], sc[2], sc[3], fm))
            sb_opt = _rect_inter(sb_opt, sc_t)
            if sb_opt is None:
                continue
        sb_opt = _rect_inter(sb_opt, cur_clip)
        if sb_opt is None:
            continue
        if ot == raw.FPDF_PAGEOBJ_IMAGE and sc is None:
            sb_opt = _tight_image_bounds(raw_doc, page_raw, sub, sb_opt, fm, page_w, page_h)
            if sb_opt is None:
                continue
            sb_opt = _rect_inter(sb_opt, cur_clip)
            if sb_opt is None:
                continue
        if sb_opt[2] - sb_opt[0] > 0.01 or sb_opt[3] - sb_opt[1] > 0.01:
            xs0.append(sb_opt[0])
            ys0.append(sb_opt[1])
            xs1.append(sb_opt[2])
            ys1.append(sb_opt[3])

    if not xs0:
        return None
    return [min(xs0), min(ys0), max(xs1), max(ys1)]


def _extract_rule_only_form(
    form_obj: Any,
    ctm: tuple[float, float, float, float, float, float],
    is_top: bool,
    page_clip: list[float] | None,
) -> tuple[bool, list[list[float]], list[list[float]]]:
    """If a Form XObject contains only thin stroke rules (no images/text/shadings/shapes), return them."""
    vis, fc = _clip_path_bounds(form_obj)
    if not vis:
        return True, [], []
    cur_clip = page_clip
    if fc is not None:
        fc_t = list(_transform_bounds(fc[0], fc[1], fc[2], fc[3], ctm)) if not is_top else fc
        cur_clip = _rect_inter(cur_clip, fc_t) if cur_clip is not None else fc_t
        if cur_clip is None:
            return True, [], []

    fm = _mul_matrix(ctm, _get_obj_matrix(form_obj))
    n = raw.FPDFFormObj_CountObjects(form_obj)
    hr: list[list[float]] = []
    vr: list[list[float]] = []
    for idx in range(n):
        sub = raw.FPDFFormObj_GetObject(form_obj, idx)
        ot = raw.FPDFPageObj_GetType(sub)
        if ot != raw.FPDF_PAGEOBJ_PATH:
            return False, [], []
        if _is_invisible_or_white_fill_path(sub):
            continue
        svis, sc = _clip_path_bounds(sub)
        if not svis:
            continue
        l, b, r, t = _transform_bounds(*_get_obj_bounds(sub), fm)
        sb: list[float] | None = [min(l, r), min(b, t), max(l, r), max(b, t)]
        if sc is not None:
            sc_t = list(_transform_bounds(sc[0], sc[1], sc[2], sc[3], fm))
            sb = _rect_inter(sb, sc_t)
            if sb is None:
                continue
        if cur_clip is not None:
            sb = _rect_inter(sb, cur_clip)
            if sb is None:
                continue
        bw, bh = sb[2] - sb[0], sb[3] - sb[1]
        if bw >= 22.0 and bh <= 3.5:
            hr.append(sb)
        elif bh >= 14.0 and bw <= 3.5:
            vr.append(sb)
        else:
            return False, [], []
    return True, hr, vr


def _collect_form_text_ptrs(form_obj: Any, form_id: int, out: dict[int, int], depth: int = 0) -> None:
    if depth > 8:
        return
    n = raw.FPDFFormObj_CountObjects(form_obj)
    for idx in range(n):
        sub = raw.FPDFFormObj_GetObject(form_obj, idx)
        otype = raw.FPDFPageObj_GetType(sub)
        if otype == raw.FPDF_PAGEOBJ_TEXT:
            ptr = ctypes.cast(sub, ctypes.c_void_p).value
            if ptr:
                out[ptr] = form_id
        elif otype == raw.FPDF_PAGEOBJ_FORM:
            _collect_form_text_ptrs(sub, form_id, out, depth + 1)


def _box_overlaps(a: list[float], b: list[float], pad: float = 0.0) -> bool:
    return not (a[2] + pad < b[0] or b[2] + pad < a[0] or a[3] + pad < b[1] or b[3] + pad < a[1])


def _box_union(boxes: list[list[float]]) -> list[float]:
    return [
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    ]


def _get_meta_text(raw_doc: Any, tag: bytes) -> str:
    nbytes = raw.FPDF_GetMetaText(raw_doc, tag, None, 0)
    if nbytes <= 2:
        return ""
    buf = ctypes.create_string_buffer(nbytes)
    raw.FPDF_GetMetaText(raw_doc, tag, buf, nbytes)
    return buf.raw[:nbytes].decode("utf-16-le", errors="replace").rstrip("\x00").strip()


def _coalesce_ranges(spans: list[tuple[int, int]]) -> list[list[int]]:
    """Coalesce (start, end) PDFium char-index spans into sorted, merged [start, end] ranges."""
    if not spans:
        return []
    ordered = sorted(spans)
    merged: list[list[int]] = [[ordered[0][0], ordered[0][1]]]
    for s, e in ordered[1:]:
        if s <= merged[-1][1]:
            if e > merged[-1][1]:
                merged[-1][1] = e
        else:
            merged.append([s, e])
    return merged


def _line_text_from_chars(chars: list[dict[str, Any]]) -> str:
    """Build text for a single line of char dicts, keeping trailing hyphens for normalise()."""
    if not chars:
        return ""
    out: list[str] = []
    prev: dict[str, Any] | None = None
    for cur in chars:
        ch = cur["u"]
        if prev is not None:
            sz = max(prev["size"], cur["size"])
            gap = cur["bbox"][0] - prev["bbox"][2]
            mono = _is_mono_font(prev["font"]) and _is_mono_font(cur["font"])
            thresh = 0.25 * sz if mono else 0.17 * sz
            if gap > thresh and prev["u"] not in _SOFT_HYPHENS and ch not in _SOFT_HYPHENS:
                out.append(" ")
        out.append(ch)
        prev = cur
    return "".join(out)


def normalise(chars: list[dict[str, Any]]) -> str:
    """Normalise a sequence of char records into clean text.

    - NFKC ligature expansion (fi, fl, ffi, ...)
    - U+0002 / U+00AD line-end hyphens dropped and joined without space
    - ASCII/Unicode line-end hyphens joined when breaking a lowercase word
    - Inter-word spaces inferred from horizontal gaps
    """
    if not chars:
        return ""

    # Partition chars into visual lines using baseline drops (PDF user space: higher y = higher on page)
    lines: list[list[dict[str, Any]]] = [[chars[0]]]
    for cur in chars[1:]:
        prev = lines[-1][-1]
        sz = max(prev["size"], cur["size"], 1.0)
        dy = prev["origin"][1] - cur["origin"][1]
        # New line if baseline dropped significantly or wrapped back horizontally with a vertical shift
        if dy > 0.45 * sz or (cur["origin"][0] < prev["origin"][0] - 2.0 * sz and abs(dy) > 0.25 * sz):
            lines.append([cur])
        else:
            lines[-1].append(cur)

    # Assemble lines with hyphen de-wrapping
    parts: list[str] = []
    for ln in lines:
        raw_line = _line_text_from_chars(ln).strip()
        if not raw_line:
            continue
        # Strip any internal soft hyphens not at line end
        if len(raw_line) > 1:
            tail = raw_line[-1]
            head = raw_line[:-1].replace("\u0002", "").replace("\u00ad", "")
            raw_line = head + tail
        if not parts:
            parts.append(raw_line)
            continue

        prev_part = parts[-1]
        if prev_part and prev_part[-1] in _SOFT_HYPHENS:
            parts[-1] = prev_part[:-1] + raw_line
        elif (
            prev_part
            and prev_part[-1] in _LINE_HYPHENS
            and len(prev_part) >= 3
            and prev_part[-2].isalpha()
            and raw_line[0].islower()
        ):
            parts[-1] = prev_part[:-1] + raw_line
        else:
            parts.append(raw_line)

    # Drop any trailing soft hyphen at the very end of the block
    if parts and parts[-1] and parts[-1][-1] in _SOFT_HYPHENS:
        parts[-1] = parts[-1][:-1]

    joined = " ".join(p for p in parts if p)
    joined = joined.replace("\u0002", "").replace("\u00ad", "")
    text = unicodedata.normalize("NFKC", joined)
    return _WS_RE.sub(" ", text).strip()


def _extract_page_raw(raw_doc: Any, page: Any, page_idx: int) -> dict[str, Any]:
    """Extract raw chars, non-text objects, links, and page metadata in unrotated PDF user space."""
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
        px0, py0, px1, py1 = min(cl.value, cr.value), min(cb.value, ct.value), max(cl.value, cr.value), max(cb.value, ct.value)
    elif has_media:
        px0, py0, px1, py1 = min(ml.value, mr.value), min(mb.value, mt.value), max(ml.value, mr.value), max(mb.value, mt.value)
    else:
        sw, sh = page.get_size()
        if rotate in (90, 270):
            sw, sh = sh, sw
        px0, py0, px1, py1 = 0.0, 0.0, float(sw), float(sh)

    W = max(1.0, px1 - px0)
    H = max(1.0, py1 - py0)
    cropbox = [_r2(px0), _r2(py0), _r2(px1), _r2(py1)]

    # --- 1. Non-text objects & Form XObject parentage ---
    images: list[list[float]] = []
    h_rules: list[list[float]] = []
    v_rules: list[list[float]] = []
    vec_paths: list[list[float]] = []
    figure_forms: list[list[float]] = []
    text_obj_to_form: dict[int, int] = {}

    n_obj = raw.FPDFPage_CountObjects(page.raw)
    top_objs = [raw.FPDFPage_GetObject(page.raw, i) for i in range(n_obj)]
    top_text_objs = sum(1 for o in top_objs if raw.FPDFPageObj_GetType(o) == raw.FPDF_PAGEOBJ_TEXT)

    def _clamp_box(left: float, bottom: float, right: float, top: float) -> list[float]:
        x0 = max(px0, min(left, right))
        y0 = max(py0, min(bottom, top))
        x1 = min(px1, max(left, right))
        y1 = min(py1, max(bottom, top))
        return [x0, y0, x1, y1]

    def _walk_objs(
        objs: list[Any],
        ctm: tuple[float, float, float, float, float, float],
        is_top: bool,
        depth: int = 0,
        page_clip: list[float] | None = None,
    ) -> None:
        if depth > 6:
            return
        for obj in objs:
            otype = raw.FPDFPageObj_GetType(obj)
            if otype == raw.FPDF_PAGEOBJ_TEXT:
                continue
            if otype == raw.FPDF_PAGEOBJ_PATH and _is_invisible_or_white_fill_path(obj):
                continue

            left, bottom, right, top = _get_obj_bounds(obj)
            if not is_top:
                left, bottom, right, top = _transform_bounds(left, bottom, right, top, ctm)
            bb_raw: list[float] | None = [min(left, right), min(bottom, top), max(left, right), max(bottom, top)]

            if otype in (raw.FPDF_PAGEOBJ_IMAGE, raw.FPDF_PAGEOBJ_PATH):
                vis, oc = _clip_path_bounds(obj)
                if not vis:
                    continue
                if oc is not None:
                    oc_t = list(_transform_bounds(oc[0], oc[1], oc[2], oc[3], ctm)) if not is_top else oc
                    bb_raw = _rect_inter(bb_raw, oc_t)
                    if bb_raw is None:
                        continue
                if page_clip is not None:
                    bb_raw = _rect_inter(bb_raw, page_clip)
                    if bb_raw is None:
                        continue
                if otype == raw.FPDF_PAGEOBJ_IMAGE and oc is None:
                    bb_raw = _tight_image_bounds(raw_doc, page.raw, obj, bb_raw, ctm, W, H)
                    if bb_raw is None:
                        continue

            bb = _clamp_box(bb_raw[0], bb_raw[1], bb_raw[2], bb_raw[3])
            bw, bh = bb[2] - bb[0], bb[3] - bb[1]
            if bw < 0.0 or bh < 0.0 or (bw <= 0.01 and bh <= 0.01):
                continue
            wf, hf = bw / W, bh / H

            if otype == raw.FPDF_PAGEOBJ_IMAGE:
                if bw >= 16.0 and bh >= 16.0 and not (wf > 0.92 and hf > 0.92):
                    images.append(bb)
            elif otype == raw.FPDF_PAGEOBJ_PATH:
                if bw >= 22.0 and bh <= 3.5:
                    h_rules.append(bb)
                elif bh >= 14.0 and bw <= 3.5:
                    v_rules.append(bb)
                elif not (wf > 0.90 and hf > 0.90):
                    if bw >= 2.0 or bh >= 2.0:
                        vec_paths.append(bb)
            elif otype == raw.FPDF_PAGEOBJ_FORM:
                sub = _count_form_subobjs(obj)
                if is_top and wf >= 0.55 and hf >= 0.65 and top_text_objs <= 5 and sub[1] >= 15:
                    fm = _mul_matrix(ctm, _get_obj_matrix(obj))
                    children = [
                        raw.FPDFFormObj_GetObject(obj, k)
                        for k in range(raw.FPDFFormObj_CountObjects(obj))
                    ]
                    _walk_objs(children, fm, is_top=False, depth=depth + 1, page_clip=page_clip)
                else:
                    # Rule-only Form XObjects (e.g. L-shaped page borders) emit their individual rules
                    # rather than one giant bounding box enclosing the empty interior.
                    if sub[1] == 0 and sub[3] == 0 and sub[4] == 0 and sub[5] == 0 and sub[2] > 0:
                        is_rule_only, f_hr, f_vr = _extract_rule_only_form(obj, ctm, is_top, page_clip)
                        if is_rule_only:
                            for rbb in f_hr:
                                cr = _clamp_box(rbb[0], rbb[1], rbb[2], rbb[3])
                                if cr[2] - cr[0] >= 22.0:
                                    h_rules.append(cr)
                            for rbb in f_vr:
                                cr = _clamp_box(rbb[0], rbb[1], rbb[2], rbb[3])
                                if cr[3] - cr[1] >= 14.0:
                                    v_rules.append(cr)
                            continue
                    has_gfx = sub[2] >= 2 or sub[3] >= 1 or sub[4] >= 1 or sub[5] >= 1
                    if has_gfx and (wf * hf >= 0.004 or (wf >= 0.06 and hf >= 0.04)):
                        drawn = _form_drawn_bounds(raw_doc, page.raw, obj, ctm, is_top, page_clip, W, H)
                        if drawn is None:
                            continue
                        dbb = _clamp_box(drawn[0], drawn[1], drawn[2], drawn[3])
                        dbw, dbh = dbb[2] - dbb[0], dbb[3] - dbb[1]
                        if dbw <= 0.01 and dbh <= 0.01:
                            continue
                        form_id = len(figure_forms)
                        figure_forms.append(dbb)
                        _collect_form_text_ptrs(obj, form_id, text_obj_to_form)

    _walk_objs(top_objs, (1.0, 0.0, 0.0, 1.0, 0.0, 0.0), is_top=True, depth=0)

    # Cluster dense vector paths (spatial grid union-find for O(N) scaling on heavy plots)
    vectors: list[list[float]] = []
    if len(vec_paths) >= 8:
        cand_paths = [p for p in vec_paths if (p[2] - p[0]) < 0.88 * W and (p[3] - p[1]) < 0.85 * H]
        if len(cand_paths) >= 12:
            cell = 16.0
            n_p = len(cand_paths)
            parent = list(range(n_p))

            def _find(x: int) -> int:
                while parent[x] != x:
                    parent[x] = parent[parent[x]]
                    x = parent[x]
                return x

            grid: dict[tuple[int, int], int] = {}
            for idx, p in enumerate(cand_paths):
                gx0 = int((p[0] - 5.0) // cell)
                gy0 = int((p[1] - 5.0) // cell)
                gx1 = int((p[2] + 5.0) // cell)
                gy1 = int((p[3] + 5.0) // cell)
                # Guard against huge sparse boxes
                if (gx1 - gx0 + 1) * (gy1 - gy0 + 1) > 400:
                    continue
                for gx in range(gx0, gx1 + 1):
                    for gy in range(gy0, gy1 + 1):
                        key = (gx, gy)
                        prev = grid.get(key)
                        if prev is None:
                            grid[key] = idx
                        else:
                            ra, rb = _find(idx), _find(prev)
                            if ra != rb:
                                parent[rb] = ra

            groups: dict[int, list[int]] = {}
            for idx in range(n_p):
                groups.setdefault(_find(idx), []).append(idx)

            for idxs in groups.values():
                if len(idxs) < 12:
                    continue
                cb_ = [
                    min(cand_paths[k][0] for k in idxs),
                    min(cand_paths[k][1] for k in idxs),
                    max(cand_paths[k][2] for k in idxs),
                    max(cand_paths[k][3] for k in idxs),
                ]
                cw, ch_ = cb_[2] - cb_[0], cb_[3] - cb_[1]
                if cw >= 40.0 and ch_ >= 35.0 and (cw * ch_) / (W * H) >= 0.012:
                    vectors.append(cb_)

    objects: list[dict[str, Any]] = []
    for bb in images:
        objects.append({"kind": "image", "bbox": [_r2(v) for v in bb]})
    for bb in h_rules + v_rules:
        objects.append({"kind": "rule", "bbox": [_r2(v) for v in bb]})
    for bb in vectors:
        objects.append({"kind": "vector", "bbox": [_r2(v) for v in bb]})
    for bb in figure_forms:
        objects.append({"kind": "form_figure", "bbox": [_r2(v) for v in bb]})

    # --- 2. Characters via FPDFText_* ---
    tp = raw.FPDFText_LoadPage(page.raw)
    n_ch = raw.FPDFText_CountChars(tp)
    chars: list[dict[str, Any]] = []
    char_meta: list[dict[str, Any]] = []  # parallel internal metadata
    total_non_ws = 0
    garbage_cnt = 0

    # Pre-allocate ctypes structs once per page
    rect = raw.FS_RECTF()
    l_, r_, b_, t_ = ctypes.c_double(), ctypes.c_double(), ctypes.c_double(), ctypes.c_double()
    ox, oy = ctypes.c_double(), ctypes.c_double()
    flags = ctypes.c_int()
    fbuf = ctypes.create_string_buffer(256)
    tm = raw.FS_MATRIX()
    cr_c, cg_c, cb_c, ca_c = ctypes.c_uint(), ctypes.c_uint(), ctypes.c_uint(), ctypes.c_uint()
    has_form_text = bool(text_obj_to_form)

    i = 0
    while i < n_ch:
        if raw.FPDFText_IsGenerated(tp, i):
            i += 1
            continue
        uni = raw.FPDFText_GetUnicode(tp, i)
        if uni <= 0:
            i += 1
            continue

        # UTF-16 surrogate pair decoding
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

        total_non_ws += 1
        if _is_garbage_cp(cp):
            garbage_cnt += 1

        # Skip non-visible control characters other than U+0002 (PDFium line-end hyphen)
        if cp < 32 and cp != 2:
            i += step
            continue

        # Loose char box (preferred), falling back to tight box
        ok_box = bool(raw.FPDFText_GetLooseCharBox(tp, i, ctypes.byref(rect)))
        if ok_box:
            x0, x1 = (rect.left, rect.right) if rect.left <= rect.right else (rect.right, rect.left)
            y0, y1 = (rect.bottom, rect.top) if rect.bottom <= rect.top else (rect.top, rect.bottom)
        else:
            ok_box = bool(
                raw.FPDFText_GetCharBox(tp, i, ctypes.byref(l_), ctypes.byref(r_), ctypes.byref(b_), ctypes.byref(t_))
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
                # Keep U+0002 hyphen with a tiny box at its origin
                x0, y0, x1, y1 = orig_x, orig_y, orig_x + 1.0, orig_y + 1.0
            else:
                i += step
                continue

        # Clip glyphs wildly outside the page
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
        fname, is_bold, is_italic, is_mono = _font_style(raw_fname, flags.value)

        fsize = float(raw.FPDFText_GetFontSize(tp, i))
        if raw.FPDFText_GetMatrix(tp, i, ctypes.byref(tm)):
            scale = math.hypot(tm.c, tm.d)
            if scale <= 1e-4:
                scale = math.hypot(tm.a, tm.b)
            if scale <= 1e-4:
                scale = 1.0
        else:
            scale = 1.0
        true_size = fsize * scale
        if true_size <= 0.1:
            true_size = max(1.0, y1 - y0)

        angle = float(raw.FPDFText_GetCharAngle(tp, i))
        if raw.FPDFText_GetFillColor(
            tp, i, ctypes.byref(cr_c), ctypes.byref(cg_c), ctypes.byref(cb_c), ctypes.byref(ca_c)
        ):
            color = [int(cr_c.value), int(cg_c.value), int(cb_c.value)]
        else:
            color = [0, 0, 0]

        form_id: int | None = None
        if has_form_text:
            tobj = raw.FPDFText_GetTextObject(tp, i)
            if tobj:
                form_id = text_obj_to_form.get(ctypes.cast(tobj, ctypes.c_void_p).value)

        char_rec = {
            "i": i,
            "u": ch,
            "bbox": [round(x0, 2), round(y0, 2), round(x1, 2), round(y1, 2)],
            "origin": [round(orig_x, 2), round(orig_y, 2)],
            "font": fname,
            "size": round(true_size, 2),
            "bold": is_bold,
            "italic": is_italic,
            "color": color,
            "block": None,
        }
        chars.append(char_rec)
        char_meta.append(
            {
                "end_i": i + step,
                "angle": angle,
                "mono": is_mono,
                "form_id": form_id,
            }
        )
        i += step

    raw.FPDFText_ClosePage(tp)

    garbage_ratio = round(garbage_cnt / total_non_ws, 4) if total_non_ws > 0 else 0.0

    # --- 3. Links ---
    links: list[dict[str, Any]] = []
    pos = ctypes.c_int(0)
    link_handle = raw.FPDF_LINK()
    while raw.FPDFLink_Enumerate(page.raw, ctypes.byref(pos), ctypes.byref(link_handle)):
        lrect = raw.FS_RECTF()
        if not raw.FPDFLink_GetAnnotRect(link_handle, ctypes.byref(lrect)):
            continue
        lx0, lx1 = min(lrect.left, lrect.right), max(lrect.left, lrect.right)
        ly0, ly1 = min(lrect.bottom, lrect.top), max(lrect.bottom, lrect.top)

        dest_obj = raw.FPDFLink_GetDest(raw_doc, link_handle)
        act = raw.FPDFLink_GetAction(link_handle)
        if not dest_obj and act:
            dest_obj = raw.FPDFAction_GetDest(raw_doc, act)

        dest: dict[str, Any] | None = None
        if dest_obj:
            dp = int(raw.FPDFDest_GetDestPageIndex(raw_doc, dest_obj))
            if dp >= 0:
                hx, hy, hz = raw.FPDF_BOOL(), raw.FPDF_BOOL(), raw.FPDF_BOOL()
                dx, dy, dz = ctypes.c_float(), ctypes.c_float(), ctypes.c_float()
                raw.FPDFDest_GetLocationInPage(
                    dest_obj,
                    ctypes.byref(hx),
                    ctypes.byref(hy),
                    ctypes.byref(hz),
                    ctypes.byref(dx),
                    ctypes.byref(dy),
                    ctypes.byref(dz),
                )
                dest = {
                    "page": dp,
                    "x": _r2(dx.value) if hx.value else None,
                    "y": _r2(dy.value) if hy.value else None,
                }

        uri: str | None = None
        if act and raw.FPDFAction_GetType(act) == raw.PDFACTION_URI:
            nb = raw.FPDFAction_GetURIPath(raw_doc, act, None, 0)
            if nb > 0:
                buf = ctypes.create_string_buffer(nb)
                raw.FPDFAction_GetURIPath(raw_doc, act, buf, nb)
                uri = buf.value.decode("utf-8", errors="replace")

        # Find chars covered by the link rect
        covered: list[tuple[int, int]] = []
        for c, cm in zip(chars, char_meta):
            bb = c["bbox"]
            cx = 0.5 * (bb[0] + bb[2])
            cy = 0.5 * (bb[1] + bb[3])
            if lx0 - 1.0 <= cx <= lx1 + 1.0 and ly0 - 1.0 <= cy <= ly1 + 1.0:
                covered.append((c["i"], cm["end_i"]))

        links.append(
            {
                "page": page_idx,
                "rect": [_r2(lx0), _r2(ly0), _r2(lx1), _r2(ly1)],
                "dest": dest,
                "uri": uri,
                "chars": _coalesce_ranges(covered),
            }
        )

    return {
        "index": page_idx,
        "width": _r2(W),
        "height": _r2(H),
        "rotate": rotate,
        "cropbox": cropbox,
        "garbage_ratio": garbage_ratio,
        "objects": objects,
        "h_rules": h_rules,
        "figure_forms": figure_forms,
        "chars": chars,
        "char_meta": char_meta,
        "links": links,
    }


def _make_line(items: list[tuple[dict[str, Any], dict[str, Any]]]) -> dict[str, Any]:
    cs = [c for c, _ in items]
    metas = [m for _, m in items]
    max_sz = max(c["size"] for c in cs)
    main_cs = [c for c in cs if c["size"] >= 0.82 * max_sz] or cs
    dom_font = Counter(c["font"] for c in main_cs).most_common(1)[0][0]
    dom_size = Counter(round(c["size"], 1) for c in main_cs).most_common(1)[0][0]
    base_med = float(st.median(c["origin"][1] for c in main_cs))
    return {
        "items": items,
        "x0": min(c["bbox"][0] for c in cs),
        "y0": min(c["bbox"][1] for c in cs),
        "x1": max(c["bbox"][2] for c in cs),
        "y1": max(c["bbox"][3] for c in cs),
        "base": base_med,
        "size": dom_size,
        "font": dom_font,
        "bold_frac": sum(1 for c in cs if c["bold"]) / len(cs),
        "mono_frac": sum(1 for m in metas if m["mono"]) / len(cs),
        "text": _line_text_from_chars(cs),
    }


def _group_chars_into_lines(
    items: list[tuple[dict[str, Any], dict[str, Any]]],
    body_size: float,
) -> tuple[list[dict[str, Any]], list[tuple[dict[str, Any], dict[str, Any]]]]:
    """Group non-form chars into horizontal lines (PDF user space: descending y); separate rotated chars."""
    horiz = [(c, m) for c, m in items if abs(m["angle"]) <= 0.26]
    rot = [(c, m) for c, m in items if abs(m["angle"]) > 0.26]
    if not horiz:
        return [], rot

    # Sort top-to-bottom (descending origin y), then left-to-right (ascending x0)
    raw_lines: list[dict[str, Any]] = []
    for c, m in sorted(horiz, key=lambda it: (-it[0]["origin"][1], it[0]["bbox"][0])):
        best = None
        best_d = 1e9
        cy = c["origin"][1]
        csz = c["size"]
        # Since chars arrive in descending origin[1] order, only recent lines can match
        for k in range(len(raw_lines) - 1, max(-1, len(raw_lines) - 12), -1):
            ln = raw_lines[k]
            d = abs(ln["base"] - cy)
            if d > 25.0 and ln["base"] > cy:
                break
            msz = csz if csz > ln["main_size"] else ln["main_size"]
            tol = 0.35 * msz
            if tol < 2.0:
                tol = 2.0
            if d < tol and d < best_d:
                best = ln
                best_d = d
        if best is not None:
            best["items"].append((c, m))
            if csz > best["main_size"]:
                best["main_size"] = csz
            if csz >= 0.85 * best["main_size"]:
                best["base_sum"] += cy
                best["base_cnt"] += 1
                best["base"] = best["base_sum"] / best["base_cnt"]
        else:
            raw_lines.append(
                {"base": cy, "base_sum": cy, "base_cnt": 1, "main_size": csz, "items": [(c, m)]}
            )

    # Attach isolated tiny sub/superscript lines to adjacent main line
    raw_lines.sort(key=lambda ln: -ln["base"])
    attached: set[int] = set()
    for idx, ln in enumerate(raw_lines):
        if len(ln["items"]) > 12 or ln["main_size"] >= 0.85 * body_size:
            continue
        lx0 = min(c["bbox"][0] for c, _ in ln["items"])
        lx1 = max(c["bbox"][2] for c, _ in ln["items"])
        best_j = None
        best_dy = 1e9
        for j, cand in enumerate(raw_lines):
            if idx == j or j in attached:
                continue
            if cand["main_size"] < 1.12 * ln["main_size"]:
                continue
            dy = abs(cand["base"] - ln["base"])
            if dy > 0.78 * cand["main_size"]:
                continue
            cx0 = min(c["bbox"][0] for c, _ in cand["items"])
            cx1 = max(c["bbox"][2] for c, _ in cand["items"])
            if lx1 >= cx0 - 8.0 and lx0 <= cx1 + 8.0 and dy < best_dy:
                best_j = j
                best_dy = dy
        if best_j is not None:
            raw_lines[best_j]["items"].extend(ln["items"])
            attached.add(idx)

    # Split each baseline at horizontal gaps > max(1.05*sz, 9.5) so 2-col lines never weld
    lines: list[dict[str, Any]] = []
    for idx, ln in enumerate(raw_lines):
        if idx in attached:
            continue
        ordered_items = sorted(ln["items"], key=lambda it: (it[0]["bbox"][0], it[0]["i"]))
        segs: list[list[tuple[dict[str, Any], dict[str, Any]]]] = [[ordered_items[0]]]
        for it in ordered_items[1:]:
            c = it[0]
            prev = segs[-1][-1][0]
            seg_x1 = max(p[0]["bbox"][2] for p in segs[-1][-3:])
            sz = max(prev["size"], c["size"], ln["main_size"])
            gap = c["bbox"][0] - seg_x1
            if gap > max(1.05 * sz, 9.5):
                segs.append([it])
            else:
                segs[-1].append(it)
        for seg in segs:
            lines.append(_make_line(seg))

    lines.sort(key=lambda ln: (-ln["y1"], ln["x0"]))
    return lines, rot


def _is_two_col_page(lines: list[dict[str, Any]], px0: float, W: float) -> tuple[bool, float]:
    """Detect if page body has a clean two-column gutter near page center."""
    if len(lines) < 8:
        return False, px0 + 0.5 * W
    body_lns = [ln for ln in lines if (ln["x1"] - ln["x0"]) < 0.52 * W]
    if len(body_lns) < 6:
        return False, px0 + 0.5 * W
    best_x = px0 + 0.5 * W
    best_score = -1.0
    for step in range(42, 59):
        gx = px0 + (step / 100.0) * W
        cross = sum(1 for ln in body_lns if ln["x0"] < gx - 3.0 and ln["x1"] > gx + 3.0)
        left = sum(1 for ln in body_lns if ln["x1"] <= gx + 3.0)
        right = sum(1 for ln in body_lns if ln["x0"] >= gx - 3.0)
        if cross <= max(1, int(0.05 * len(body_lns))) and left >= 3 and right >= 3:
            left_max = max((ln["x1"] for ln in body_lns if ln["x1"] <= gx + 3.0), default=px0)
            right_min = min((ln["x0"] for ln in body_lns if ln["x0"] >= gx - 3.0), default=px0 + W)
            gap = right_min - left_max
            score = (left + right) * 10.0 + gap - cross * 50.0
            if gap >= 8.0 and score > best_score:
                best_score = score
                best_x = 0.5 * (left_max + right_min)
    return (best_score > 0), best_x


def _remerge_intra_column_splits(
    lines: list[dict[str, Any]],
    two_col: bool,
    gutter_x: float | None,
) -> list[dict[str, Any]]:
    """Re-merge same-baseline line segments split by wide justified spaces when they don't cross gutter_x."""
    if len(lines) < 2:
        return lines
    by_y: list[list[dict[str, Any]]] = []
    for ln in sorted(lines, key=lambda x: (-x["base"], x["x0"])):
        if by_y and abs(ln["base"] - by_y[-1][0]["base"]) < max(
            2.0, 0.35 * max(ln["size"], by_y[-1][0]["size"])
        ):
            by_y[-1].append(ln)
        else:
            by_y.append([ln])

    out: list[dict[str, Any]] = []
    for row in by_y:
        row.sort(key=lambda x: x["x0"])
        merged: list[dict[str, Any]] = [row[0]]
        for ln in row[1:]:
            prev = merged[-1]
            gap = ln["x0"] - prev["x1"]
            sz = max(prev["size"], ln["size"])
            crosses_gutter = (
                two_col
                and gutter_x is not None
                and prev["x1"] <= gutter_x + 3.0
                and ln["x0"] >= gutter_x - 3.0
            )
            if not crosses_gutter and gap <= max(1.85 * sz, 15.0) and abs(prev["size"] - ln["size"]) <= 1.5:
                seg = sorted(prev["items"] + ln["items"], key=lambda it: (it[0]["bbox"][0], it[0]["i"]))
                merged[-1] = _make_line(seg)
            else:
                merged.append(ln)
        out.extend(merged)

    out.sort(key=lambda ln: (-ln["y1"], ln["x0"]))
    return out


def _find_sidebar_gutter(lines: list[dict[str, Any]], px0: float, W: float) -> float | None:
    """Detect an asymmetric sidebar gutter on a 1-column page (e.g. eLife / PLOS ONE metadata margin)."""
    if len(lines) < 10:
        return None
    non_full = [ln for ln in lines if (ln["x1"] - ln["x0"]) < 0.72 * W]
    if len(non_full) < 8:
        return None
    best_x: float | None = None
    best_score = -1.0
    for step in list(range(18, 41)) + list(range(60, 83)):
        gx = px0 + (step / 100.0) * W
        cross = sum(1 for ln in non_full if ln["x0"] < gx - 3.0 and ln["x1"] > gx + 3.0)
        left = [ln for ln in non_full if ln["x1"] <= gx + 3.0]
        right = [ln for ln in non_full if ln["x0"] >= gx - 3.0]
        if cross <= 2 and len(left) >= 3 and len(right) >= 5:
            left_max = max(ln["x1"] for ln in left)
            right_min = min(ln["x0"] for ln in right)
            gap = right_min - left_max
            # Ensure vertical overlap between left and right sides (truly side-by-side)
            ly0, ly1 = min(ln["y0"] for ln in left), max(ln["y1"] for ln in left)
            ry0, ry1 = min(ln["y0"] for ln in right), max(ln["y1"] for ln in right)
            v_ov = min(ly1, ry1) - max(ly0, ry0)
            if gap >= 10.0 and v_ov >= 40.0:
                score = (len(left) + len(right)) * 10.0 + gap - cross * 50.0
                if score > best_score:
                    best_score = score
                    best_x = 0.5 * (left_max + right_min)
    return best_x


def _xycut_order_lines(
    lines: list[dict[str, Any]],
    px0: float,
    W: float,
    body_size: float,
    two_col: bool,
    gutter_x: float | None,
) -> list[tuple[int | None, dict[str, Any]]]:
    """Pre-masked XY-Cut++ ordering in PDF user space (top-to-bottom = descending y).

    Returns list of (column, line) where column is 0 (left), 1 (right), or None (spanning / 1-col).
    """
    if not lines:
        return []
    sidebar_gutter: float | None = None
    if not two_col or gutter_x is None:
        sidebar_gutter = _find_sidebar_gutter(lines, px0, W)
        if sidebar_gutter is None:
            return [(None, ln) for ln in sorted(lines, key=lambda x: (-x["y1"], x["x0"]))]

    cut_x = gutter_x if (two_col and gutter_x is not None) else sidebar_gutter
    assert cut_x is not None
    span_w = 0.54 * W if two_col else 0.72 * W

    spanning: list[dict[str, Any]] = []
    col_lines: list[dict[str, Any]] = []
    for ln in lines:
        w = ln["x1"] - ln["x0"]
        if (ln["x0"] < cut_x - 8.0 and ln["x1"] > cut_x + 8.0) or w > span_w:
            spanning.append(ln)
        else:
            col_lines.append(ln)

    # Sort spanning lines top-to-bottom (descending y1)
    spanning.sort(key=lambda x: (-x["y1"], x["x0"]))
    # Group adjacent spanning lines into vertical bands (band_top = max y1, band_bot = min y0)
    span_bands: list[tuple[float, float, list[dict[str, Any]]]] = []
    for ln in spanning:
        if span_bands and span_bands[-1][1] - ln["y1"] <= max(2.4 * body_size, 22.0):
            bt, bb, bl = span_bands[-1]
            bl.append(ln)
            span_bands[-1] = (max(bt, ln["y1"]), min(bb, ln["y0"]), bl)
        else:
            span_bands.append((ln["y1"], ln["y0"], [ln]))

    out: list[tuple[int | None, dict[str, Any]]] = []
    emitted: set[int] = set()

    def _emit_cols(subset: list[dict[str, Any]]) -> None:
        left = sorted(
            [ln for ln in subset if 0.5 * (ln["x0"] + ln["x1"]) < cut_x],
            key=lambda x: (-x["y1"], x["x0"]),
        )
        right = sorted(
            [ln for ln in subset if 0.5 * (ln["x0"] + ln["x1"]) >= cut_x],
            key=lambda x: (-x["y1"], x["x0"]),
        )
        l_col = 0 if two_col else None
        r_col = 1 if two_col else None
        for ln in left:
            out.append((l_col, ln))
        for ln in right:
            out.append((r_col, ln))

    for bt, bb, bl in span_bands:
        # Lines whose vertical center is above the band's top (cy > bt)
        above = []
        for idx, ln in enumerate(col_lines):
            if idx in emitted:
                continue
            cy = 0.5 * (ln["y0"] + ln["y1"])
            if cy > bt:
                above.append(ln)
                emitted.add(idx)
        _emit_cols(above)

        for ln in sorted(bl, key=lambda x: (-x["y1"], x["x0"])):
            out.append((None, ln))

        # Lines whose center falls inside [bb, bt]
        inside = []
        for idx, ln in enumerate(col_lines):
            if idx in emitted:
                continue
            cy = 0.5 * (ln["y0"] + ln["y1"])
            if cy >= bb:
                inside.append(ln)
                emitted.add(idx)
        _emit_cols(inside)

    rest = [ln for idx, ln in enumerate(col_lines) if idx not in emitted]
    _emit_cols(rest)
    return out


def _has_hrule_between(
    h_rules: list[list[float]], y_low: float, y_high: float, x0: float, x1: float
) -> bool:
    for r in h_rules:
        ry = 0.5 * (r[1] + r[3])
        if y_low <= ry <= y_high and min(r[2], x1) > max(r[0], x0) + 10.0:
            return True
    return False


def _group_ordered_lines_into_blocks(
    ordered: list[tuple[int | None, dict[str, Any]]],
    h_rules: list[list[float]],
    body_size: float,
    body_leading: float,
    W: float,
) -> tuple[list[tuple[int | None, list[dict[str, Any]]]], dict[int | None, tuple[float, float]]]:
    """Group reading-ordered lines into blocks in PDF user space (descending y)."""
    if not ordered:
        return [], {}

    col_bounds: dict[int | None, tuple[float, float]] = {}
    for col_id in (None, 0, 1):
        cl = [ln for c, ln in ordered if c == col_id and (ln["x1"] - ln["x0"]) > 0.15 * W]
        if cl:
            xs0 = sorted(ln["x0"] for ln in cl)
            xs1 = sorted(ln["x1"] for ln in cl)
            col_bounds[col_id] = (xs0[int(0.15 * len(xs0))], xs1[int(0.85 * len(xs1))])

    blocks: list[tuple[int | None, list[dict[str, Any]]]] = []
    cur_col = ordered[0][0]
    cur_lns = [ordered[0][1]]

    def _flush() -> None:
        nonlocal cur_lns
        if cur_lns:
            blocks.append((cur_col, cur_lns))
            cur_lns = []

    for col, ln in ordered[1:]:
        prev = cur_lns[-1]
        split = False
        if col != cur_col:
            split = True
        else:
            # In PDF user space, moving downward means prev.base > ln.base
            dy_base = prev["base"] - ln["base"]
            dy_gap = prev["y0"] - ln["y1"]
            if dy_base < -2.0 or dy_base > max(1.52 * body_leading, 1.85 * max(prev["size"], ln["size"])):
                split = True
            elif dy_gap > max(0.75 * max(prev["size"], ln["size"]), 6.5):
                split = True
            elif _has_hrule_between(
                h_rules, ln["y1"] - 1.0, prev["y0"] + 1.0, min(prev["x0"], ln["x0"]), max(prev["x1"], ln["x1"])
            ):
                split = True
            elif abs(ln["size"] - prev["size"]) >= max(1.1, 0.12 * body_size):
                split = True
            elif (prev["bold_frac"] >= 0.85) != (ln["bold_frac"] >= 0.85) and (
                len(cur_lns) <= 2 or _NUM_HEAD_RE.match(ln["text"]) or _ABSTRACT_HEAD_RE.match(prev["text"])
            ):
                if not (
                    len(cur_lns) == 1
                    and prev["bold_frac"] >= 0.85
                    and (prev["x1"] - prev["x0"]) > 0.35 * W
                    and not _NUM_HEAD_RE.match(prev["text"])
                ):
                    split = True
            elif _CAPTION_RE.match(ln["text"]) or _NUM_HEAD_RE.match(ln["text"]) or _ALGO_RE.match(ln["text"]):
                split = True
            elif (
                (prev["mono_frac"] >= 0.75) != (ln["mono_frac"] >= 0.75)
                and not _URL_RE.search(prev["text"])
                and not _URL_RE.search(ln["text"])
            ):
                split = True
            elif col in col_bounds and abs(ln["size"] - body_size) <= 1.2:
                c_left, c_right = col_bounds[col]
                prev_short = prev["x1"] < c_right - max(2.8 * body_size, 24.0)
                cur_indented = (ln["x0"] - c_left) >= max(0.85 * body_size, 7.5) and (
                    ln["x0"] - prev["x0"]
                ) >= max(0.6 * body_size, 5.0)
                cur_hanging = (
                    len(cur_lns) >= 2
                    and abs(cur_lns[0]["x0"] - c_left) <= 4.5
                    and (prev["x0"] - ln["x0"]) >= max(0.6 * body_size, 5.0)
                    and abs(ln["x0"] - c_left) <= 4.5
                )
                if (
                    cur_indented
                    or (prev_short and prev["text"].rstrip().endswith((".", "?", "!", ":", '"', "”", ")")))
                    or cur_hanging
                ):
                    split = True
            if not split and min(prev["x1"], ln["x1"]) < max(prev["x0"], ln["x0"]):
                split = True

        if split:
            _flush()
            cur_col = col
            cur_lns = [ln]
        else:
            cur_lns.append(ln)

    _flush()
    return blocks, col_bounds


def _detect_align(
    lines: list[dict[str, Any]],
    col_bounds: tuple[float, float] | None,
    page_x0: float,
    W: float,
) -> str:
    if not lines:
        return "left"
    c_left, c_right = col_bounds if col_bounds else (page_x0 + 0.08 * W, page_x0 + 0.92 * W)
    c_mid = 0.5 * (c_left + c_right)
    c_w = max(1.0, c_right - c_left)

    if len(lines) == 1:
        ln = lines[0]
        l_mid = 0.5 * (ln["x0"] + ln["x1"])
        l_w = ln["x1"] - ln["x0"]
        if l_w < 0.85 * c_w and abs(l_mid - c_mid) <= 10.0 and (ln["x0"] - c_left) > 12.0:
            return "center"
        if l_w < 0.75 * c_w and abs(ln["x1"] - c_right) <= 6.0 and (ln["x0"] - c_left) > 20.0:
            return "right"
        return "left"

    # Multi-line block: inspect all lines except the last for justify
    body_lns = lines[:-1]
    left_flush = sum(1 for ln in body_lns if abs(ln["x0"] - c_left) <= 6.0)
    right_flush = sum(1 for ln in body_lns if abs(ln["x1"] - c_right) <= 6.0)
    if len(body_lns) >= 2 and left_flush >= 0.6 * len(body_lns) and right_flush >= 0.7 * len(body_lns):
        return "justify"
    if len(body_lns) == 1 and abs(lines[0]["x1"] - c_right) <= 6.0 and abs(lines[0]["x0"] - c_left) <= 18.0:
        if (lines[0]["x1"] - lines[0]["x0"]) >= 0.85 * c_w:
            return "justify"

    centered = sum(
        1
        for ln in lines
        if abs(0.5 * (ln["x0"] + ln["x1"]) - c_mid) <= 10.0 and (ln["x0"] - c_left) > 10.0
    )
    if centered >= 0.7 * len(lines):
        return "center"

    right_aligned = sum(
        1 for ln in lines if abs(ln["x1"] - c_right) <= 6.0 and (ln["x0"] - c_left) > 12.0
    )
    if right_aligned >= 0.7 * len(lines):
        return "right"
    return "left"


def _cluster_rotated_items(
    rot_items: list[tuple[dict[str, Any], dict[str, Any]]]
) -> list[list[tuple[dict[str, Any], dict[str, Any]]]]:
    """Group rotated chars into spatial clusters (e.g. margin stamps vs rotated plot labels)."""
    if not rot_items:
        return []
    clusters: list[tuple[list[float], list[tuple[dict[str, Any], dict[str, Any]]]]] = []
    for it in sorted(rot_items, key=lambda x: (x[0]["bbox"][0], -x[0]["bbox"][3])):
        bb = it[0]["bbox"]
        pad = max(12.0, 1.5 * it[0]["size"])
        hits = [idx for idx, (cb, _) in enumerate(clusters) if _box_overlaps(cb, bb, pad=pad)]
        if not hits:
            clusters.append((list(bb), [it]))
        else:
            nb = _box_union([bb] + [clusters[idx][0] for idx in hits])
            nitems = [it]
            for idx in hits:
                nitems.extend(clusters[idx][1])
            for idx in reversed(hits):
                clusters.pop(idx)
            clusters.append((nb, nitems))
    return [sorted(items, key=lambda x: x[0]["i"]) for _, items in clusters]


def _build_block(
    page_idx: int,
    b_num: int,
    order: int,
    col: int | None,
    lines: list[dict[str, Any]],
    col_bounds: tuple[float, float] | None,
    page_x0: float,
    W: float,
) -> dict[str, Any]:
    bid = f"p{page_idx + 1:03d}-b{b_num:03d}"
    all_items = [it for ln in lines for it in ln["items"]]
    all_cs = [c for c, _ in all_items]
    for c in all_cs:
        c["block"] = bid

    x0 = min(c["bbox"][0] for c in all_cs)
    y0 = min(c["bbox"][1] for c in all_cs)
    x1 = max(c["bbox"][2] for c in all_cs)
    y1 = max(c["bbox"][3] for c in all_cs)

    line_boxes = [[_r2(ln["x0"]), _r2(ln["y0"]), _r2(ln["x1"]), _r2(ln["y1"])] for ln in lines]
    char_ranges = _coalesce_ranges([(c["i"], m["end_i"]) for c, m in all_items])

    dom_font = Counter(c["font"] for c in all_cs).most_common(1)[0][0]
    dom_size = _r2(float(st.median(ln["size"] for ln in lines)))
    bold_frac = sum(1 for c in all_cs if c["bold"]) / max(1, len(all_cs))
    ital_frac = sum(1 for c in all_cs if c["italic"]) / max(1, len(all_cs))
    dom_color = list(Counter(tuple(c["color"]) for c in all_cs).most_common(1)[0][0])

    return {
        "id": bid,
        "page": page_idx,
        "bbox": [_r2(x0), _r2(y0), _r2(x1), _r2(y1)],
        "lines": line_boxes,
        "chars": char_ranges,
        "text": normalise(all_cs),
        "font": dom_font,
        "size": dom_size,
        "bold": bold_frac >= 0.65,
        "italic": ital_frac >= 0.65,
        "color": dom_color,
        "align": _detect_align(lines, col_bounds, page_x0, W),
        "column": col,
        "order": order,
        "kind": None,
        "confidence": None,
        "translate": None,
        "segment": None,
        "box": None,
    }


def extract(pdf_path: Path) -> tuple[dict[str, Any], list[list[dict[str, Any]]]]:
    """Extract raw layout and per-page character records from a born-digital PDF.

    Returns ``(layout, chars_by_page)`` where ``layout`` conforms to ``rosettex.pdf-layout/1``
    (unclassified part) and ``chars_by_page[page]`` is the in-memory list of char dicts.
    """
    pdf_path = Path(pdf_path)
    if not pdf_path.is_file():
        raise PdfError("not_pdf", f"File not found: {pdf_path}")

    data = pdf_path.read_bytes()
    if b"%PDF-" not in data[:1024]:
        raise PdfError("not_pdf", f"Not a PDF file: {pdf_path}")
    pdf_sha256 = hashlib.sha256(data).hexdigest()

    # Open with pypdfium2.raw first so we can distinguish password errors from format errors
    raw_doc = raw.FPDF_LoadDocument(str(pdf_path).encode("utf-8"), None)
    if not raw_doc:
        err = raw.FPDF_GetLastError()
        if err == raw.FPDF_ERR_PASSWORD:
            raise PdfError("encrypted_pdf", f"PDF requires a user password: {pdf_path}")
        raise PdfError("not_pdf", f"PDFium failed to open {pdf_path} (error {err})")

    # Wrap in PdfDocument for safe lifecycle management (read-only)
    raw.FPDF_CloseDocument(raw_doc)
    try:
        doc = pdfium.PdfDocument(str(pdf_path))
    except Exception as exc:
        raise PdfError("not_pdf", f"Failed to open PDF: {exc}") from exc

    try:
        n_pages = len(doc)
        if n_pages == 0:
            raise PdfError("not_pdf", f"PDF has 0 pages: {pdf_path}")

        producer = _get_meta_text(doc.raw, b"Producer")
        creator = _get_meta_text(doc.raw, b"Creator")

        raw_pages = [_extract_page_raw(doc.raw, doc[i], i) for i in range(n_pages)]
    finally:
        doc.close()

    # Check if every page has no text or garbage_ratio > 0.8
    if all((len(rp["chars"]) == 0 or rp["garbage_ratio"] > 0.8) for rp in raw_pages):
        raise PdfError("no_text_layer", f"PDF has no usable text layer: {pdf_path}")

    # 1. Document modal body font & size
    font_size_counts: Counter[tuple[str, float]] = Counter()
    for rp in raw_pages:
        for c, m in zip(rp["chars"], rp["char_meta"]):
            if abs(m["angle"]) <= 0.26 and c["u"].isalpha():
                font_size_counts[(c["font"], round(c["size"], 1))] += 1
    if not font_size_counts:
        for rp in raw_pages:
            for c in rp["chars"]:
                font_size_counts[(c["font"], round(c["size"], 1))] += 1
    if font_size_counts:
        (body_font, body_size), _ = font_size_counts.most_common(1)[0]
    else:
        body_font, body_size = "", 10.0

    # 2. Separate figure-Form-owned chars, then group remaining chars into preliminary lines
    prelim_pages: list[dict[str, Any]] = []
    for rp in raw_pages:
        form_buckets: dict[int, list[tuple[dict[str, Any], dict[str, Any]]]] = {}
        non_form_items: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for c, m in zip(rp["chars"], rp["char_meta"]):
            fid = m["form_id"]
            if fid is not None:
                form_buckets.setdefault(fid, []).append((c, m))
            else:
                non_form_items.append((c, m))
        lns, rot = _group_chars_into_lines(non_form_items, body_size)
        prelim_pages.append({"lines": lns, "rot": rot, "form_buckets": form_buckets})

    # 3. Document-level 2-column vote
    col_votes = [
        _is_two_col_page(pp["lines"], rp["cropbox"][0], rp["width"])
        for rp, pp in zip(raw_pages, prelim_pages)
    ]
    n_2col = sum(1 for ok, _ in col_votes if ok)
    min_votes = 1 if n_pages <= 2 else max(2, int(0.35 * n_pages))
    doc_two_col = n_2col >= min_votes
    doc_gutter = _r2(float(st.median(gx for ok, gx in col_votes if ok))) if doc_two_col else None

    # 4. Re-merge safe intra-column splits and estimate body_leading
    leadings: list[float] = []
    for pp in prelim_pages:
        lns = _remerge_intra_column_splits(pp["lines"], doc_two_col, doc_gutter)
        pp["lines"] = lns
        for a, b in zip(lns, lns[1:]):
            if (
                abs(a["size"] - body_size) <= 0.6
                and abs(b["size"] - body_size) <= 0.6
                and abs(a["x0"] - b["x0"]) < 15.0
            ):
                dy = a["base"] - b["base"]
                if 0.95 * body_size <= dy <= 1.8 * body_size:
                    leadings.append(dy)
    body_leading = float(st.median(leadings)) if leadings else 1.2 * body_size

    # 5. Per-page XY-Cut++, block grouping, figure-Form blocks, rotated blocks
    all_blocks: list[dict[str, Any]] = []
    all_links: list[dict[str, Any]] = []
    pages_out: list[dict[str, Any]] = []
    chars_by_page: list[list[dict[str, Any]]] = []
    global_order = 0

    for rp, pp in zip(raw_pages, prelim_pages):
        p_idx = rp["index"]
        px0 = rp["cropbox"][0]
        W = rp["width"]
        b_num = 0

        # Ordered body/page lines
        ordered = _xycut_order_lines(pp["lines"], px0, W, body_size, doc_two_col, doc_gutter)
        grouped, col_bounds = _group_ordered_lines_into_blocks(
            ordered, rp["h_rules"], body_size, body_leading, W
        )

        for col, blns in grouped:
            b_num += 1
            blk = _build_block(
                p_idx, b_num, global_order, col, blns, col_bounds.get(col), px0, W
            )
            all_blocks.append(blk)
            global_order += 1

        # Figure-Form-owned blocks (by object parentage, never by /BBox)
        for fid in sorted(pp["form_buckets"]):
            fitems = pp["form_buckets"][fid]
            flns, frot = _group_chars_into_lines(fitems, body_size)
            if frot:
                flns.append(_make_line(sorted(frot, key=lambda it: it[0]["i"])))
            flns.sort(key=lambda ln: (-ln["y1"], ln["x0"]))
            if not flns:
                continue
            b_num += 1
            fcol: int | None = None
            if doc_two_col and doc_gutter is not None:
                fx0 = min(ln["x0"] for ln in flns)
                fx1 = max(ln["x1"] for ln in flns)
                if fx1 <= doc_gutter + 4.0:
                    fcol = 0
                elif fx0 >= doc_gutter - 4.0:
                    fcol = 1
            blk = _build_block(p_idx, b_num, global_order, fcol, flns, None, px0, W)
            all_blocks.append(blk)
            global_order += 1

        # Rotated non-form chars (clustered spatially)
        for cluster in _cluster_rotated_items(pp["rot"]):
            b_num += 1
            rln = _make_line(cluster)
            blk = _build_block(p_idx, b_num, global_order, None, [rln], None, px0, W)
            all_blocks.append(blk)
            global_order += 1

        pages_out.append(
            {
                "index": p_idx,
                "width": rp["width"],
                "height": rp["height"],
                "rotate": rp["rotate"],
                "cropbox": rp["cropbox"],
                "garbage_ratio": rp["garbage_ratio"],
                "objects": rp["objects"],
            }
        )
        all_links.extend(rp["links"])
        chars_by_page.append(rp["chars"])

    layout: dict[str, Any] = {
        "schema": SCHEMA,
        "pdf_sha256": pdf_sha256,
        "producer": producer,
        "creator": creator,
        "doc": {
            "body_font": body_font,
            "body_size": _r2(body_size),
            "two_col": doc_two_col,
            "gutter_x": doc_gutter,
            "pages": n_pages,
        },
        "pages": pages_out,
        "blocks": all_blocks,
        "links": all_links,
        "segments": [],
        "kept": [],
    }
    return layout, chars_by_page

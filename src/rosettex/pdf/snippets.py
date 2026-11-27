"""Build inline-math vector snippets PDF (``inline_math.pdf``) from a source PDF and layout."""

from __future__ import annotations

import io
import struct
import time
import zlib
from collections import defaultdict
from pathlib import Path
from typing import Any

import pypdf
from pypdf.generic import (
    ArrayObject,
    ByteStringObject,
    ContentStream,
    DecodedStreamObject,
    DictionaryObject,
    FloatObject,
    IndirectObject,
    NameObject,
    NumberObject,
    StreamObject,
    TextStringObject,
)

from rosettex.pdf import stream
from rosettex.pdf.stream import Glyph, GlyphKey, OpKey, PageStream

_PAD = 0.5

_PATH_CONSTRUCT_OPS = frozenset({b"m", b"l", b"c", b"v", b"y", b"h", b"re"})
_PATH_PAINT_OPS = frozenset({b"S", b"s", b"f", b"F", b"f*", b"B", b"B*", b"b", b"b*"})
_CLIP_OPS = frozenset({b"W", b"W*"})
_TEXT_SHOW_OPS = frozenset({b"Tj", b"TJ", b"'", b'"'})
_TEXT_POS_OPS = frozenset({b"Td", b"TD", b"Tm", b"T*", b"TL"})
_TEXT_RESET_OPS = frozenset({b"Td", b"TD", b"Tm", b"T*", b"ET", b"'", b'"'})
_IDENTITY_MATRIX: tuple[float, float, float, float, float, float] = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
_DRAW_OR_TEXT_OPS = (
    _PATH_PAINT_OPS
    | _TEXT_SHOW_OPS
    | frozenset({b"BT", b"ET", b"Do", b"INLINE IMAGE", b"sh"})
)


def _mat_mul(
    m1: tuple[float, float, float, float, float, float],
    m2: tuple[float, float, float, float, float, float],
) -> tuple[float, float, float, float, float, float]:
    a1, b1, c1, d1, e1, f1 = m1
    a2, b2, c2, d2, e2, f2 = m2
    return (
        a1 * a2 + b1 * c2,
        a1 * b2 + b1 * d2,
        c1 * a2 + d1 * c2,
        c1 * b2 + d1 * d2,
        e1 * a2 + f1 * c2 + e2,
        e1 * b2 + f1 * d2 + f2,
    )


def _fmt_num(val: float) -> NumberObject | FloatObject:
    r = round(val, 4)
    if abs(r - round(r)) < 1e-4:
        return NumberObject(int(round(r)))
    return FloatObject(r)


def _encode_pdf_bytes(raw: bytes) -> bytes:
    """Encode a PDF string compactly as literal (...) when mostly printable ASCII, else <hex>."""
    if not raw:
        return b"()"
    # Count literal length (escaping \, (, )) vs hex length (2 * len + 2)
    lit = bytearray(b"(")
    use_lit = True
    for b in raw:
        if b in (0x28, 0x29, 0x5C):  # (, ), \
            lit.append(0x5C)
            lit.append(b)
        elif 0x20 <= b <= 0x7E:
            lit.append(b)
        else:
            use_lit = False
            break
    if use_lit:
        lit.append(0x29)
        if len(lit) <= len(raw) * 2 + 2:
            return bytes(lit)
    return b"<" + raw.hex().encode("ascii") + b">"


def _encode_operand(obj: Any) -> bytes:
    if isinstance(obj, ByteStringObject):
        return _encode_pdf_bytes(bytes(obj))
    if isinstance(obj, TextStringObject):
        return _encode_pdf_bytes(obj.original_bytes)
    if isinstance(obj, NumberObject):
        return str(int(obj)).encode("ascii")
    if isinstance(obj, FloatObject):
        s = f"{float(obj):.5f}".rstrip("0").rstrip(".")
        if s == "-0":
            s = "0"
        return s.encode("ascii")
    if isinstance(obj, NameObject):
        return str(obj).encode("latin-1")
    if isinstance(obj, ArrayObject):
        parts: list[bytes] = []
        prev_self_delim = True
        for item in obj:
            eb = _encode_operand(item)
            self_delim = eb[:1] in (b"(", b"<", b"[", b"/")
            if parts and not (prev_self_delim or self_delim):
                parts.append(b" ")
            parts.append(eb)
            prev_self_delim = eb[-1:] in (b")", b">", b"]")
        return b"[" + b"".join(parts) + b"]"
    tmp = io.BytesIO()
    obj.write_to_stream(tmp)
    return tmp.getvalue()


def _serialize_ops(ops: list[tuple[list[Any], bytes]], writer: pypdf.PdfWriter) -> bytes:
    """Serialize PDF content-stream operations compactly."""
    out: list[bytes] = []
    for operands, op in ops:
        if op == b"INLINE IMAGE":
            cs_tmp = ContentStream(None, writer)
            cs_tmp.operations = [(operands, op)]
            out.append(cs_tmp.get_data().strip())
            continue
        if op == b"TJ" and len(operands) == 1 and isinstance(operands[0], ArrayObject) and len(operands[0]) == 1:
            only = operands[0][0]
            if isinstance(only, (ByteStringObject, TextStringObject)):
                out.append(_encode_operand(only) + b"Tj")
                continue
        if not operands:
            out.append(op)
        else:
            enc = [_encode_operand(a) for a in operands]
            line = b" ".join(enc)
            if line[-1:] in (b")", b">", b"]"):
                out.append(line + op)
            else:
                out.append(line + b" " + op)
    return b"\n".join(out)


def _resolve(obj: Any) -> Any:
    return obj.get_object() if hasattr(obj, "get_object") else obj


def _str_bytes(obj: Any) -> bytes:
    if isinstance(obj, ByteStringObject):
        return bytes(obj)
    if isinstance(obj, TextStringObject):
        return obj.original_bytes
    if isinstance(obj, bytes):
        return obj
    return b""


def _fmt_tj_num(val: float) -> NumberObject | FloatObject:
    r = round(val, 4)
    if abs(r - round(r)) < 1e-4:
        return NumberObject(int(round(r)))
    return FloatObject(r)


def _has_glyph_bytes(operands: list[Any], op: bytes) -> bool:
    if op in (b"Tj", b"'"):
        return bool(operands) and len(_str_bytes(operands[0])) > 0
    if op == b'"':
        return len(operands) >= 3 and len(_str_bytes(operands[2])) > 0
    if op == b"TJ":
        if not operands:
            return False
        for item in operands[0]:
            if not isinstance(item, (NumberObject, FloatObject, int, float)):
                if len(_str_bytes(item)) > 0:
                    return True
        return False
    return False


def _strip_actual_text_bdc(operands: list[Any], resources: DictionaryObject | None) -> list[Any]:
    """Return BDC operands with /ActualText removed from the property dictionary."""
    if len(operands) < 2:
        return operands
    tag, props_op = operands[0], operands[1]
    props = _resolve(props_op)
    if isinstance(props, NameObject) and isinstance(resources, DictionaryObject):
        prop_res = _resolve(resources.get("/Properties"))
        if isinstance(prop_res, DictionaryObject):
            props = _resolve(prop_res.get(str(props)))
    if not isinstance(props, DictionaryObject):
        return operands
    if "/ActualText" not in props:
        return operands
    new_props = DictionaryObject()
    for k, v in props.items():
        if str(k) == "/ActualText":
            continue
        new_props[k] = v
    return [tag, new_props]


def _clone_shared(raw_val: Any, out_writer: pypdf.PdfWriter, cache: dict[int, Any]) -> Any:
    """Clone an object into ``out_writer``, sharing IndirectObjects across snippet pages."""
    if isinstance(raw_val, IndirectObject):
        hit = cache.get(raw_val.idnum)
        if hit is not None:
            return hit
        cloned = raw_val.clone(out_writer)
        cache[raw_val.idnum] = cloned
        return cloned
    if isinstance(raw_val, (DictionaryObject, ArrayObject, StreamObject)):
        return raw_val.clone(out_writer)
    return raw_val


class _ParsedStreamMeta:
    """Precomputed per-content-stream index parsed once per source page / Form XObject."""

    __slots__ = (
        "ops",
        "resources",
        "bt_to_et",
        "bt_persistent_ops",
        "path_paint_info",
        "path_construct_to_paint",
        "xobjs",
        "all_op_indices",
    )

    def __init__(
        self,
        ops: list[tuple[list[Any], bytes]],
        resources: DictionaryObject | None,
        all_op_indices: set[int],
    ) -> None:
        self.ops = ops
        self.resources = resources
        self.all_op_indices = all_op_indices

        # Index XObjects dict for quick Form/Image lookup
        self.xobjs: DictionaryObject | None = None
        if isinstance(resources, DictionaryObject) and "/XObject" in resources:
            xo_obj = _resolve(resources["/XObject"])
            if isinstance(xo_obj, DictionaryObject):
                self.xobjs = xo_obj

        # Precompute BT..ET spans and persistent state ops inside each BT..ET
        self.bt_to_et: dict[int, int] = {}
        self.bt_persistent_ops: dict[int, list[int]] = {}
        bt_start: int | None = None
        for idx, (_operands, op) in enumerate(ops):
            if op == b"BT" and bt_start is None:
                bt_start = idx
            elif op == b"ET" and bt_start is not None:
                self.bt_to_et[bt_start] = idx
                persist: list[int] = []
                for j in range(bt_start + 1, idx):
                    jop = ops[j][1]
                    if jop not in _TEXT_SHOW_OPS and jop not in _TEXT_POS_OPS:
                        persist.append(j)
                self.bt_persistent_ops[bt_start] = persist
                bt_start = None

        # Precompute path construction sequences -> paint op
        self.path_paint_info: dict[int, tuple[list[int], bool]] = {}
        self.path_construct_to_paint: dict[int, int] = {}
        cur_construct: list[int] = []
        has_clip = False
        for idx, (_operands, op) in enumerate(ops):
            if op in _PATH_CONSTRUCT_OPS:
                cur_construct.append(idx)
            elif op in _CLIP_OPS:
                has_clip = True
            elif op == b"n":
                cur_construct.clear()
                has_clip = False
            elif op in _PATH_PAINT_OPS:
                self.path_paint_info[idx] = (list(cur_construct), has_clip)
                if not has_clip:
                    for cidx in cur_construct:
                        self.path_construct_to_paint[cidx] = idx
                cur_construct.clear()
                has_clip = False


def _needs_trailing_kept_tm(
    ops: list[tuple[list[Any], bytes]],
    start_idx: int,
    et_idx: int,
    cid_keep_g: dict[int, dict[int, set[int]]],
) -> bool:
    """Return True if a kept text-showing op follows before any text-positioning reset."""
    for j in range(start_idx + 1, et_idx):
        op = ops[j][1]
        if op in _TEXT_RESET_OPS:
            return False
        if op in (b"Tj", b"TJ") and j in cid_keep_g:
            return True
    return False


def _rewrite_keep_text_items(
    cid: str,
    op_idx: int,
    items: list[Any],
    keep_item_bpos: dict[int, set[int]] | None,
    glyph_by_key: dict[GlyphKey, Glyph],
    preserve_trailing_tm: bool,
) -> ArrayObject:
    """Keep only glyphs in ``keep_item_bpos``; replace dropped glyphs by TJ displacement."""
    out_arr = ArrayObject()
    kept_buf = bytearray()
    pending_num = 0.0
    has_pending = False

    def _flush_buf() -> None:
        nonlocal kept_buf
        if kept_buf:
            out_arr.append(ByteStringObject(bytes(kept_buf)))
            kept_buf = bytearray()

    for item_idx, item in enumerate(items):
        if isinstance(item, (NumberObject, FloatObject, int, float)):
            if kept_buf:
                _flush_buf()
            pending_num += float(item)
            has_pending = True
        else:
            raw_bytes = _str_bytes(item)
            kept_bpos = keep_item_bpos.get(item_idx) if keep_item_bpos is not None else None
            pos = 0
            n = len(raw_bytes)
            while pos < n:
                g = glyph_by_key.get((cid, op_idx, item_idx, pos))
                if g is None:
                    if kept_bpos is not None:
                        if has_pending and abs(pending_num) > 1e-4:
                            out_arr.append(_fmt_tj_num(pending_num))
                        pending_num = 0.0
                        has_pending = False
                        kept_buf.append(raw_bytes[pos])
                    pos += 1
                    continue
                nb = g.nbytes
                if kept_bpos is not None and pos in kept_bpos:
                    if has_pending and abs(pending_num) > 1e-4:
                        out_arr.append(_fmt_tj_num(pending_num))
                    pending_num = 0.0
                    has_pending = False
                    kept_buf.extend(raw_bytes[pos : pos + nb])
                else:
                    if kept_buf:
                        _flush_buf()
                    pending_num += -g.advance
                    has_pending = True
                pos += nb

    _flush_buf()
    if has_pending and abs(pending_num) > 1e-4 and preserve_trailing_tm:
        out_arr.append(_fmt_tj_num(pending_num))
    return out_arr


def _prune_empty_gs_and_mc(ops: list[tuple[list[Any], bytes]]) -> list[tuple[list[Any], bytes]]:
    """Drop balanced ``q...Q`` blocks (outside BT..ET) with no drawing/text ops, and empty BDC/BMC..EMC."""
    # Pass A: collapse balanced q..Q blocks containing no drawing/text/Do/sh ops
    out: list[tuple[list[Any], bytes]] = []
    # Stack entries: [out_index_of_q, has_active, mc_depth_at_q, has_path_construct, in_bt_at_q]
    q_stack: list[list[Any]] = []
    mc_depth = 0
    in_bt = False

    for operands, op in ops:
        if op == b"BT":
            in_bt = True
            for frame in q_stack:
                frame[1] = True
            out.append((operands, op))
        elif op == b"ET":
            in_bt = False
            for frame in q_stack:
                frame[1] = True
            out.append((operands, op))
        elif op == b"q":
            q_stack.append([len(out), False, mc_depth, False, in_bt])
            out.append((operands, op))
        elif op == b"Q":
            if q_stack:
                q_pos, has_active, q_mc, has_path, q_in_bt = q_stack.pop()
                if (not has_active) and (not q_in_bt) and (not in_bt) and (mc_depth == q_mc) and (not has_path):
                    del out[q_pos:]
                    continue
            out.append((operands, op))
        elif op in (b"BDC", b"BMC"):
            mc_depth += 1
            out.append((operands, op))
        elif op == b"EMC":
            if mc_depth > 0:
                mc_depth -= 1
            out.append((operands, op))
        else:
            if q_stack:
                if op in _DRAW_OR_TEXT_OPS:
                    for frame in q_stack:
                        frame[1] = True
                elif op in _PATH_CONSTRUCT_OPS:
                    q_stack[-1][3] = True
                elif op == b"n":
                    q_stack[-1][3] = False
            out.append((operands, op))

    # Pass B: drop immediately empty BDC/BMC .. EMC pairs
    out2: list[tuple[list[Any], bytes]] = []
    for operands, op in out:
        if op == b"EMC" and out2 and out2[-1][1] in (b"BDC", b"BMC"):
            out2.pop()
            continue
        out2.append((operands, op))

    return out2


def _prune_dead_tf(
    ops: list[tuple[list[Any], bytes]],
    form_do_names: set[str],
) -> list[tuple[list[Any], bytes]]:
    """Drop unused ``Tf`` ops and replace font names on displacement-only ``Tf`` ops."""
    active_tf: int | None = None
    tf_stack: list[int | None] = []
    glyph_live: set[int] = set()
    tj_only_live: set[int] = set()

    for idx, (operands, op) in enumerate(ops):
        if op == b"q":
            tf_stack.append(active_tf)
        elif op == b"Q":
            active_tf = tf_stack.pop() if tf_stack else None
        elif op == b"Tf":
            active_tf = idx
        elif op in _TEXT_SHOW_OPS:
            if active_tf is not None:
                if _has_glyph_bytes(operands, op):
                    glyph_live.add(active_tf)
                else:
                    tj_only_live.add(active_tf)
        elif op == b"Do" and operands and str(operands[0]) in form_do_names:
            # A Form XObject can inherit Tf from its caller
            if active_tf is not None:
                glyph_live.add(active_tf)

    # Pick any glyph-live font name to substitute into displacement-only Tf ops
    # so stripped prose fonts are never referenced in /Resources /Font.
    fallback_font: NameObject | None = None
    for idx in sorted(glyph_live):
        if ops[idx][0]:
            fallback_font = ops[idx][0][0]
            break

    pruned: list[tuple[list[Any], bytes]] = []
    for idx, (operands, op) in enumerate(ops):
        if op == b"Tf":
            if idx in glyph_live:
                pruned.append((operands, op))
            elif idx in tj_only_live:
                if fallback_font is not None and len(operands) >= 2:
                    pruned.append(([fallback_font, operands[1]], op))
                else:
                    pruned.append((operands, op))
            # else: dead Tf, drop
        else:
            pruned.append((operands, op))
    return pruned


def _collect_used_resources(ops: list[tuple[list[Any], bytes]]) -> dict[str, set[str]]:
    used: dict[str, set[str]] = {
        "/Font": set(),
        "/XObject": set(),
        "/ExtGState": set(),
        "/Shading": set(),
        "/Properties": set(),
    }
    for operands, op in ops:
        if op == b"Tf" and operands:
            used["/Font"].add(str(operands[0]))
        elif op == b"Do" and operands:
            used["/XObject"].add(str(operands[0]))
        elif op == b"gs" and operands:
            used["/ExtGState"].add(str(operands[0]))
        elif op == b"sh" and operands:
            used["/Shading"].add(str(operands[0]))
        elif op == b"BDC" and len(operands) >= 2 and isinstance(operands[1], NameObject):
            used["/Properties"].add(str(operands[1]))
    return used


def _build_pruned_resources(
    src_res: DictionaryObject | None,
    used: dict[str, set[str]],
    custom_xobjs: dict[str, IndirectObject],
    out_writer: pypdf.PdfWriter,
    clone_cache: dict[int, Any],
) -> DictionaryObject:
    """Build a pruned ``/Resources`` dict referencing only used fonts/XObjects/ExtGState/etc."""
    new_res = DictionaryObject()
    if not isinstance(src_res, DictionaryObject):
        if custom_xobjs:
            xo_dict = DictionaryObject()
            for k, ref in custom_xobjs.items():
                xo_dict[NameObject(k)] = ref
            new_res[NameObject("/XObject")] = xo_dict
        return new_res

    for subkey in ("/Font", "/ExtGState", "/Shading", "/Properties"):
        wanted = used[subkey]
        if not wanted or subkey not in src_res:
            continue
        src_sub = _resolve(src_res[subkey])
        if not isinstance(src_sub, DictionaryObject):
            continue
        new_sub = DictionaryObject()
        for name in wanted:
            if name in src_sub:
                new_sub[NameObject(name)] = _clone_shared(src_sub.raw_get(name), out_writer, clone_cache)
        if len(new_sub) > 0:
            new_res[NameObject(subkey)] = new_sub

    # /XObject: prefer custom_xobjs (filtered Forms), else clone unmodified XObjects
    wanted_xo = used["/XObject"]
    if wanted_xo:
        src_xo = _resolve(src_res["/XObject"]) if "/XObject" in src_res else None
        new_xo = DictionaryObject()
        for name in wanted_xo:
            if name in custom_xobjs:
                new_xo[NameObject(name)] = custom_xobjs[name]
            elif isinstance(src_xo, DictionaryObject) and name in src_xo:
                new_xo[NameObject(name)] = _clone_shared(src_xo.raw_get(name), out_writer, clone_cache)
        if len(new_xo) > 0:
            new_res[NameObject("/XObject")] = new_xo

    # Keep small color/pattern/procset dicts when present (shared via clone_cache)
    for passthrough in ("/ColorSpace", "/Pattern", "/ProcSet"):
        if passthrough in src_res:
            new_res[NameObject(passthrough)] = _clone_shared(
                src_res.raw_get(passthrough), out_writer, clone_cache
            )

    return new_res


def _write_compressed_pdf(writer: pypdf.PdfWriter, out: Path) -> None:
    """Write ``writer`` as PDF 1.5 with Flate-compressed Object Streams (/ObjStm) and /XRef."""
    out.parent.mkdir(parents=True, exist_ok=True)
    objs = writer._objects
    n_objs = len(objs)

    root_ref = getattr(writer.root_object, "indirect_reference", None)
    if isinstance(root_ref, IndirectObject):
        root_id = root_ref.idnum
    else:
        root_id = next((i + 1 for i, o in enumerate(objs) if o is writer.root_object), 1)

    buf = io.BytesIO()
    buf.write(b"%PDF-1.5\n%\xe2\xe3\xcf\xd3\n")

    # xref entries: obj_id -> (type, field2, field3)
    xref: dict[int, tuple[int, int, int]] = {0: (0, 0, 65535)}
    packable: list[tuple[int, bytes]] = []

    for idx, obj in enumerate(objs):
        oid = idx + 1
        if obj is None:
            xref[oid] = (0, 0, 65535)
            continue
        if isinstance(obj, StreamObject):
            offset = buf.tell()
            buf.write(f"{oid} 0 obj\n".encode("ascii"))
            # Compact stream header when only /Filter and /Length are present
            if set(obj.keys()) <= {"/Filter", "/Length"}:
                raw_data = obj._data
                if "/Filter" in obj:
                    filt = obj["/Filter"]
                    if isinstance(filt, NameObject):
                        buf.write(f"<</Filter{filt}/Length {len(raw_data)}>>\nstream\n".encode("ascii"))
                        buf.write(raw_data)
                        buf.write(b"\nendstream\nendobj\n")
                        xref[oid] = (1, offset, 0)
                        continue
                else:
                    buf.write(f"<</Length {len(raw_data)}>>\nstream\n".encode("ascii"))
                    buf.write(raw_data)
                    buf.write(b"\nendstream\nendobj\n")
                    xref[oid] = (1, offset, 0)
                    continue
            obj.write_to_stream(buf)
            buf.write(b"\nendobj\n")
            xref[oid] = (1, offset, 0)
        else:
            tmp = io.BytesIO()
            obj.write_to_stream(tmp)
            packable.append((oid, tmp.getvalue()))

    # Pack non-stream objects into /ObjStm chunks (up to 256 objects per stream)
    next_id = n_objs + 1
    chunk_size = 256
    for start in range(0, len(packable), chunk_size):
        chunk = packable[start : start + chunk_size]
        stm_id = next_id
        next_id += 1

        hdr_parts: list[bytes] = []
        body_parts: list[bytes] = []
        rel_off = 0
        for pos_in_stm, (oid, raw_obj) in enumerate(chunk):
            hdr_parts.append(f"{oid} {rel_off}".encode("ascii"))
            body_parts.append(raw_obj)
            rel_off += len(raw_obj) + 1
            xref[oid] = (2, stm_id, pos_in_stm)

        hdr = b" ".join(hdr_parts) + b"\n"
        body = b"\n".join(body_parts)
        comp = zlib.compress(hdr + body, level=9)

        stm_offset = buf.tell()
        buf.write(
            f"{stm_id} 0 obj\n"
            f"<</Type/ObjStm/N {len(chunk)}/First {len(hdr)}/Filter/FlateDecode/Length {len(comp)}>>\n"
            f"stream\n".encode("ascii")
        )
        buf.write(comp)
        buf.write(b"\nendstream\nendobj\n")
        xref[stm_id] = (1, stm_offset, 0)

    # Write compressed /XRef stream
    xref_id = next_id
    size = xref_id + 1
    xref_offset = buf.tell()
    xref[xref_id] = (1, xref_offset, 0)

    xref_raw = bytearray()
    for oid in range(size):
        t, f2, f3 = xref.get(oid, (0, 0, 65535))
        xref_raw.extend(struct.pack(">BIH", t, f2, f3))
    xref_comp = zlib.compress(bytes(xref_raw), level=9)

    buf.write(
        f"{xref_id} 0 obj\n"
        f"<</Type/XRef/Size {size}/W[1 4 2]/Root {root_id} 0 R/Filter/FlateDecode/Length {len(xref_comp)}>>\n"
        f"stream\n".encode("ascii")
    )
    buf.write(xref_comp)
    buf.write(b"\nendstream\nendobj\n")
    buf.write(f"startxref\n{xref_offset}\n%%EOF\n".encode("ascii"))

    out.write_bytes(buf.getvalue())


def snippets(
    pdf: Path,
    layout: dict[str, Any],
    chars: list[list[dict[str, Any]]],
    out: Path,
) -> dict[str, Any]:
    """Build ``inline_math.pdf`` with one tight, pruned page per ``m`` placeholder.

    Mutates each ``m`` placeholder in ``layout["segments"]``:
      - ``ph["snippet"]`` = 0-based page index in ``out``
      - ``ph["size"]`` = ``[w, h]`` (pt, padded box)
      - ``ph["depth"]`` = ``round(ph["baseline"] - snippet_y0, 2)`` where ``snippet_y0 = ph["bbox"][1] - 0.5``

    Returns ``{"snippets": int, "bytes": int, "seconds": float}``.
    """
    t0 = time.perf_counter()
    pdf = Path(pdf)
    out = Path(out)

    # 1. Collect all "m" placeholders in exact order: segment order, then m1, m2, ...
    ph_entries: list[tuple[int, dict[str, Any]]] = []
    by_page: dict[int, list[tuple[int, dict[str, Any]]]] = defaultdict(list)

    for seg in layout.get("segments") or []:
        ph_map = seg.get("ph") or {}
        m_keys = sorted(
            (k for k in ph_map if k.startswith("m") and k[1:].isdigit()),
            key=lambda k: int(k[1:]),
        )
        for mk in m_keys:
            ph = ph_map[mk]
            sn_idx = len(ph_entries)
            ph_entries.append((sn_idx, ph))
            by_page[int(ph["page"])].append((sn_idx, ph))

    out_writer = pypdf.PdfWriter()

    if not ph_entries:
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "wb") as fh:
            out_writer.write(fh)
        return {
            "snippets": 0,
            "bytes": out.stat().st_size,
            "seconds": round(time.perf_counter() - t0, 4),
        }

    # Pre-allocate blank pages in exact output order so each source page is parsed once
    for sn_idx, ph in ph_entries:
        bx0, by0, bx1, by1 = (float(v) for v in ph["bbox"])
        sx0, sy0, sx1, sy1 = bx0 - _PAD, by0 - _PAD, bx1 + _PAD, by1 + _PAD
        w = round(sx1 - sx0, 2)
        h = round(sy1 - sy0, 2)
        out_writer.add_blank_page(width=max(w, 1.0), height=max(h, 1.0))
        ph["snippet"] = sn_idx
        ph["size"] = [w, h]
        ph["depth"] = round(float(ph["baseline"]) - sy0, 2)

    src_writer = stream.open_writer(pdf)
    clone_cache: dict[int, Any] = {}
    res_dedup_cache: dict[tuple[Any, ...], IndirectObject] = {}

    def _dedup_resources(
        page_res_obj: DictionaryObject | None,
        used: dict[str, set[str]],
        custom_xobjs: dict[str, IndirectObject],
    ) -> IndirectObject | DictionaryObject:
        if custom_xobjs:
            return _build_pruned_resources(page_res_obj, used, custom_xobjs, out_writer, clone_cache)
        res_id = id(page_res_obj)
        key = (
            res_id,
            frozenset(used["/Font"]),
            frozenset(used["/XObject"]),
            frozenset(used["/ExtGState"]),
            frozenset(used["/Shading"]),
            frozenset(used["/Properties"]),
        )
        hit = res_dedup_cache.get(key)
        if hit is not None:
            return hit
        pruned = _build_pruned_resources(page_res_obj, used, custom_xobjs, out_writer, clone_cache)
        ref = out_writer._add_object(pruned)
        res_dedup_cache[key] = ref
        return ref

    # Process each source page that has at least one "m" placeholder ONCE
    for page_idx in sorted(by_page.keys()):
        page_phs = by_page[page_idx]
        src_page = src_writer.pages[page_idx]
        src_rot = int(src_page.rotation) % 360

        ps: PageStream = stream.parse(src_writer, page_idx)
        matched, _unmatched = stream.match(ps, chars[page_idx])

        glyph_by_key: dict[GlyphKey, Glyph] = {g.key: g for g in ps.glyphs}

        # Index ps.ops and ps.glyphs by content_id for fast per-snippet filtering
        ops_by_cid: dict[str, set[int]] = defaultdict(set)
        for op in ps.ops:
            ops_by_cid[op.key[0]].add(op.key[1])

        glyphs_count_by_cid: dict[str, int] = defaultdict(int)
        for g in ps.glyphs:
            glyphs_count_by_cid[g.key[0]] += 1

        # Index BDC spans with /ActualText by (cid, bdc_op_idx) -> set[GlyphKey]
        bdc_actual_by_cid: dict[str, dict[int, set[GlyphKey]]] = defaultdict(dict)
        for sp in ps.spans:
            if sp["actual_text"] is not None:
                scid, sop = sp["key"]
                bdc_actual_by_cid[scid][sop] = set(sp["glyphs"])

        # Lazy cache of parsed ContentStream metadata per content_id
        meta_cache: dict[str, _ParsedStreamMeta] = {}

        def _get_meta(cid: str, stream_obj: Any, resources: DictionaryObject | None) -> _ParsedStreamMeta:
            m = meta_cache.get(cid)
            if m is not None:
                return m
            cs = ContentStream(stream_obj, src_writer)
            m = _ParsedStreamMeta(cs.operations, resources, ops_by_cid.get(cid, set()))
            meta_cache[cid] = m
            return m

        page_res = _resolve(src_page.get("/Resources")) if "/Resources" in src_page else None
        page_meta = _get_meta("page", src_page.get("/Contents"), page_res)

        for sn_idx, ph in page_phs:
            bx0, by0, bx1, by1 = (float(v) for v in ph["bbox"])
            sx0, sy0, sx1, sy1 = bx0 - _PAD, by0 - _PAD, bx1 + _PAD, by1 + _PAD
            sw, sh = sx1 - sx0, sy1 - sy0

            # Keep glyphs matching the placeholder's PDFium char ranges
            keep_glyphs: set[GlyphKey] = set()
            for r0, r1 in ph.get("chars") or []:
                for ci in range(int(r0), int(r1)):
                    gk = matched.get(ci)
                    if gk is not None:
                        keep_glyphs.add(gk)

            # Keep non-text ops inside ph.bbox + 0.5 pt
            keep_ops: set[OpKey] = set(stream.ops_in_box(ps, ph["bbox"], pad=_PAD))

            # Also keep ancestor Form Do ops for any kept glyph or kept op inside a Form XObject
            keep_form_dos: set[OpKey] = set()
            active_cids = {gk[0] for gk in keep_glyphs} | {ok[0] for ok in keep_ops}
            for cid in active_cids:
                cur = cid
                while "/" in cur:
                    parent_cid, op_str = cur.rsplit("/", 1)
                    keep_form_dos.add((parent_cid, int(op_str)))
                    cur = parent_cid

            # Index keep sets by cid
            keep_g_by_cid: dict[str, dict[int, dict[int, set[int]]]] = defaultdict(
                lambda: defaultdict(lambda: defaultdict(set))
            )
            keep_g_count_by_cid: dict[str, int] = defaultdict(int)
            for gk in keep_glyphs:
                cid, op_idx, item_idx, bpos = gk
                keep_g_by_cid[cid][op_idx][item_idx].add(bpos)
                keep_g_count_by_cid[cid] += 1

            keep_o_by_cid: dict[str, set[int]] = defaultdict(set)
            for cid, op_idx in keep_ops:
                keep_o_by_cid[cid].add(op_idx)

            keep_fd_by_cid: dict[str, set[int]] = defaultdict(set)
            for cid, op_idx in keep_form_dos:
                keep_fd_by_cid[cid].add(op_idx)

            def _filter_cid(
                cid: str,
                meta: _ParsedStreamMeta,
            ) -> tuple[list[tuple[list[Any], bytes]], dict[str, set[str]], dict[str, IndirectObject]]:
                ops = meta.ops
                resources = meta.resources
                cid_keep_g = keep_g_by_cid.get(cid, {})
                cid_keep_o = keep_o_by_cid.get(cid, set())
                cid_keep_fd = keep_fd_by_cid.get(cid, set())
                cid_bdc_actual = bdc_actual_by_cid.get(cid, {})
                all_ops = meta.all_op_indices

                # Precompute which BDC ops in this cid need /ActualText stripped
                strip_bdc: set[int] = set()
                for bdc_idx, sp_glyphs in cid_bdc_actual.items():
                    if not sp_glyphs or not (sp_glyphs & keep_glyphs):
                        strip_bdc.add(bdc_idx)

                stage1: list[tuple[list[Any], bytes]] = []
                custom_xobjs: dict[str, IndirectObject] = {}
                inherited_used: dict[str, set[str]] = {
                    "/Font": set(),
                    "/XObject": set(),
                    "/ExtGState": set(),
                    "/Shading": set(),
                    "/Properties": set(),
                }
                surviving_form_names: set[str] = set()
                do_clone_counter = 0

                idx = 0
                n_ops = len(ops)
                tl = 0.0
                tl_stack: list[float] = []
                while idx < n_ops:
                    operands, op = ops[idx]

                    # Fast-skip dead BT..ET blocks (keeping persistent state ops only)
                    if op == b"BT" and idx in meta.bt_to_et:
                        et_idx = meta.bt_to_et[idx]
                        has_kept_in_bt = any(k in cid_keep_g for k in range(idx + 1, et_idx))
                        if not has_kept_in_bt:
                            # Update tracked TL across dead BT..ET in case a later live BT uses T*
                            for j in range(idx + 1, et_idx):
                                j_ops, j_op = ops[j]
                                if j_op == b"TL" and j_ops:
                                    tl = float(j_ops[0])
                                elif j_op == b"TD" and len(j_ops) >= 2:
                                    tl = -float(j_ops[1])
                                elif j_op == b"q":
                                    tl_stack.append(tl)
                                elif j_op == b"Q" and tl_stack:
                                    tl = tl_stack.pop()
                            for j in meta.bt_persistent_ops[idx]:
                                j_ops, j_op = ops[j]
                                if j in strip_bdc and j_op == b"BDC":
                                    stage1.append((_strip_actual_text_bdc(j_ops, resources), j_op))
                                else:
                                    stage1.append((j_ops, j_op))
                            idx = et_idx + 1
                            continue

                        # Live BT..ET block: collapse positioning runs (Tm/Td/TD/T*/TL) into single Tm
                        stage1.append((operands, op))
                        tlm = _IDENTITY_MATRIX
                        dirty_tm = False

                        def _flush_tm() -> None:
                            nonlocal dirty_tm
                            if dirty_tm:
                                stage1.append(([_fmt_num(v) for v in tlm], b"Tm"))
                                dirty_tm = False

                        for j in range(idx + 1, et_idx):
                            j_ops, j_op = ops[j]
                            if j_op == b"Tm" and len(j_ops) == 6:
                                tlm = tuple(float(v) for v in j_ops)  # type: ignore[assignment]
                                dirty_tm = True
                            elif j_op == b"Td" and len(j_ops) >= 2:
                                tlm = _mat_mul((1.0, 0.0, 0.0, 1.0, float(j_ops[0]), float(j_ops[1])), tlm)
                                dirty_tm = True
                            elif j_op == b"TD" and len(j_ops) >= 2:
                                tx, ty = float(j_ops[0]), float(j_ops[1])
                                tl = -ty
                                tlm = _mat_mul((1.0, 0.0, 0.0, 1.0, tx, ty), tlm)
                                dirty_tm = True
                            elif j_op == b"T*":
                                tlm = _mat_mul((1.0, 0.0, 0.0, 1.0, 0.0, -tl), tlm)
                                dirty_tm = True
                            elif j_op == b"TL" and j_ops:
                                tl = float(j_ops[0])
                            elif j in strip_bdc and j_op == b"BDC":
                                stage1.append((_strip_actual_text_bdc(j_ops, resources), j_op))
                            elif j_op in _TEXT_SHOW_OPS:
                                keep_items = cid_keep_g.get(j)
                                need_tm = _needs_trailing_kept_tm(ops, j, et_idx, cid_keep_g)
                                if j_op == b"Tj":
                                    arr = _rewrite_keep_text_items(
                                        cid, j, [j_ops[0]], keep_items, glyph_by_key, need_tm
                                    )
                                    if len(arr) > 0:
                                        _flush_tm()
                                        stage1.append(([arr], b"TJ"))
                                elif j_op == b"TJ":
                                    arr = _rewrite_keep_text_items(
                                        cid, j, j_ops[0], keep_items, glyph_by_key, need_tm
                                    )
                                    if len(arr) > 0:
                                        _flush_tm()
                                        stage1.append(([arr], b"TJ"))
                                elif j_op == b"'":
                                    tlm = _mat_mul((1.0, 0.0, 0.0, 1.0, 0.0, -tl), tlm)
                                    dirty_tm = True
                                    arr = _rewrite_keep_text_items(
                                        cid, j, [j_ops[0]], keep_items, glyph_by_key, need_tm
                                    )
                                    if len(arr) > 0:
                                        _flush_tm()
                                        stage1.append(([arr], b"TJ"))
                                elif j_op == b'"':
                                    stage1.append(([j_ops[0]], b"Tw"))
                                    stage1.append(([j_ops[1]], b"Tc"))
                                    tlm = _mat_mul((1.0, 0.0, 0.0, 1.0, 0.0, -tl), tlm)
                                    dirty_tm = True
                                    arr = _rewrite_keep_text_items(
                                        cid, j, [j_ops[2]], keep_items, glyph_by_key, need_tm
                                    )
                                    if len(arr) > 0:
                                        _flush_tm()
                                        stage1.append(([arr], b"TJ"))
                            else:
                                if j_op == b"q":
                                    tl_stack.append(tl)
                                elif j_op == b"Q" and tl_stack:
                                    tl = tl_stack.pop()
                                stage1.append((j_ops, j_op))
                        stage1.append(ops[et_idx])
                        idx = et_idx + 1
                        continue

                    if op == b"TL" and operands:
                        tl = float(operands[0])
                        idx += 1
                        continue
                    if op == b"q":
                        tl_stack.append(tl)
                    elif op == b"Q" and tl_stack:
                        tl = tl_stack.pop()

                    # Path construction ops belonging to a dropped non-clipping path
                    if idx in meta.path_construct_to_paint:
                        paint_idx = meta.path_construct_to_paint[idx]
                        if paint_idx not in cid_keep_o:
                            idx += 1
                            continue

                    # Path paint ops
                    if op in _PATH_PAINT_OPS and idx in meta.path_paint_info:
                        if idx not in cid_keep_o:
                            _cops, has_clip = meta.path_paint_info[idx]
                            if has_clip:
                                stage1.append(([], b"n"))
                            idx += 1
                            continue

                    # Non-text ops (INLINE IMAGE, sh)
                    if op in (b"INLINE IMAGE", b"sh") and idx in all_ops:
                        if idx not in cid_keep_o:
                            idx += 1
                            continue

                    # Do (Image or Form XObject)
                    if op == b"Do" and idx in all_ops:
                        keep_this_do = (idx in cid_keep_o) or (idx in cid_keep_fd)
                        if not keep_this_do:
                            idx += 1
                            continue
                        xname = str(operands[0])
                        if meta.xobjs is not None and xname in meta.xobjs:
                            raw_xo = meta.xobjs.raw_get(xname)
                            xo = _resolve(raw_xo)
                            if isinstance(xo, StreamObject) and xo.get("/Subtype") == "/Form":
                                child_cid = f"{cid}/{idx}"
                                # Does the Form need filtering (i.e. has dropped glyphs/ops)?
                                child_has_dropped_glyphs = (
                                    glyphs_count_by_cid.get(child_cid, 0)
                                    > keep_g_count_by_cid.get(child_cid, 0)
                                )
                                child_all_o = ops_by_cid.get(child_cid, set())
                                child_kept_o = keep_o_by_cid.get(child_cid, set()) | keep_fd_by_cid.get(
                                    child_cid, set()
                                )
                                child_has_dropped_ops = bool(child_all_o - child_kept_o)
                                if child_has_dropped_glyphs or child_has_dropped_ops or (idx in cid_keep_fd):
                                    has_own_res = "/Resources" in xo
                                    sub_res = _resolve(xo.get("/Resources")) if has_own_res else resources
                                    child_meta = _get_meta(child_cid, xo, sub_res)
                                    c_ops, c_used, c_custom = _filter_cid(child_cid, child_meta)

                                    c_bytes = _serialize_ops(c_ops, out_writer)

                                    form_s = DecodedStreamObject()
                                    for fk, fv in xo.items():
                                        if fk in ("/Length", "/Filter", "/DecodeParms", "/Resources"):
                                            continue
                                        form_s[NameObject(str(fk))] = _clone_shared(
                                            xo.raw_get(fk), out_writer, clone_cache
                                        )
                                    if has_own_res:
                                        form_s[NameObject("/Resources")] = _build_pruned_resources(
                                            sub_res, c_used, c_custom, out_writer, clone_cache
                                        )
                                    else:
                                        # Form inherits parent's /Resources: propagate its used resources up
                                        for rk, rset in c_used.items():
                                            inherited_used[rk].update(rset)
                                        custom_xobjs.update(c_custom)
                                    form_s.set_data(c_bytes)
                                    form_ref = out_writer._add_object(form_s.flate_encode())

                                    out_xname = xname
                                    if out_xname in custom_xobjs:
                                        do_clone_counter += 1
                                        out_xname = f"{xname}_rx{do_clone_counter}"
                                    custom_xobjs[out_xname] = form_ref
                                    surviving_form_names.add(out_xname)
                                    stage1.append(([NameObject(out_xname)], b"Do"))
                                    idx += 1
                                    continue
                                else:
                                    surviving_form_names.add(xname)

                    if idx in strip_bdc and op == b"BDC":
                        stage1.append((_strip_actual_text_bdc(operands, resources), op))
                    else:
                        stage1.append((operands, op))
                    idx += 1

                # Step 2: prune empty q..Q, empty BDC/BMC..EMC, and dead Tf
                stage2 = _prune_empty_gs_and_mc(stage1)
                final_ops = _prune_dead_tf(stage2, surviving_form_names)

                used = _collect_used_resources(final_ops)
                for rk, rset in inherited_used.items():
                    used[rk].update(rset)

                return final_ops, used, custom_xobjs

            final_ops, used_res, custom_xobjs = _filter_cid("page", page_meta)

            # Serialize filtered content stream compactly and wrap in clip rect
            body_bytes = _serialize_ops(final_ops, out_writer)

            clip_prefix = f"q {sx0:.2f} {sy0:.2f} {sw:.2f} {sh:.2f} re W n\n".encode("ascii")
            full_bytes = clip_prefix + body_bytes + b"\nQ"
            comp_bytes = zlib.compress(full_bytes, level=9)
            content_s = DecodedStreamObject()
            if len(comp_bytes) + 19 < len(full_bytes):
                content_s._data = comp_bytes
                content_s[NameObject("/Filter")] = NameObject("/FlateDecode")
                content_s[NameObject("/Length")] = NumberObject(len(comp_bytes))
            else:
                content_s._data = full_bytes
                content_s[NameObject("/Length")] = NumberObject(len(full_bytes))
            content_ref = out_writer._add_object(content_s)

            pruned_res = _dedup_resources(page_res, used_res, custom_xobjs)

            sn_page = out_writer.pages[sn_idx]
            box_arr = ArrayObject([FloatObject(sx0), FloatObject(sy0), FloatObject(sx1), FloatObject(sy1)])
            sn_page[NameObject("/MediaBox")] = box_arr
            sn_page[NameObject("/CropBox")] = box_arr
            sn_page[NameObject("/Resources")] = pruned_res
            sn_page[NameObject("/Contents")] = content_ref
            if src_rot != 0:
                sn_page[NameObject("/Rotate")] = NumberObject(src_rot)

    _write_compressed_pdf(out_writer, out)

    return {
        "snippets": len(ph_entries),
        "bytes": out.stat().st_size,
        "seconds": round(time.perf_counter() - t0, 4),
    }

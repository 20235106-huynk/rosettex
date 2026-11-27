"""PDF content-stream text-state interpreter and glyph-level editor (pypdf)."""

from __future__ import annotations

import math
import re
import zlib
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pypdf
from pypdf._codecs import adobe_glyphs, core_font_metrics
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
from pypdf.generic._font import encoding_dict_from_named_encoding

GlyphKey = tuple[str, int, int, int]  # (content_id, op_index, item_index, byte_pos)
OpKey = tuple[str, int]  # (content_id, op_index)

_SUBSET_RE = re.compile(r"^[A-Z]{6}\+")
_HEX_TOKEN_RE = re.compile(r"<([0-9A-Fa-f]+)>")

_IDENTITY_MATRIX: tuple[float, float, float, float, float, float] = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
_PATH_CONSTRUCT_OPS = frozenset({b"m", b"l", b"c", b"v", b"y", b"h", b"re"})
_PATH_PAINT_OPS = frozenset({b"S", b"s", b"f", b"F", b"f*", b"B", b"B*", b"b", b"b*"})
_PATH_STROKE_OPS = frozenset({b"S", b"s", b"B", b"B*", b"b", b"b*"})
_CLIP_OPS = frozenset({b"W", b"W*"})

# Predefined variable-length CJK CMap codespace ranges (PDF 1.7 Table 118)
_PREDEFINED_VAR_CMAPS: dict[str, list[tuple[int, bytes, bytes]]] = {
    "GBK-EUC-H": [(1, b"\x00", b"\x80"), (2, b"\x81\x40", b"\xfe\xfe")],
    "GBK2K-H": [(1, b"\x00", b"\x80"), (2, b"\x81\x40", b"\xfe\xfe")],
    "GBpc-EUC-H": [(1, b"\x00", b"\x80"), (2, b"\xa1\xa1", b"\xfe\xfe")],
    "GB-EUC-H": [(1, b"\x00", b"\x80"), (2, b"\xa1\xa1", b"\xfe\xfe")],
    "90ms-RKSJ-H": [
        (1, b"\x00", b"\x80"),
        (1, b"\xa0", b"\xdf"),
        (1, b"\xfd", b"\xff"),
        (2, b"\x81\x40", b"\x9f\xfc"),
        (2, b"\xe0\x40", b"\xfc\xfc"),
    ],
    "90msp-RKSJ-H": [
        (1, b"\x00", b"\x80"),
        (1, b"\xa0", b"\xdf"),
        (1, b"\xfd", b"\xff"),
        (2, b"\x81\x40", b"\x9f\xfc"),
        (2, b"\xe0\x40", b"\xfc\xfc"),
    ],
    "EUC-H": [
        (1, b"\x00", b"\x7f"),
        (2, b"\x8e\xa1", b"\x8e\xdf"),
        (2, b"\xa1\xa1", b"\xfe\xfe"),
    ],
    "ETen-B5-H": [(1, b"\x00", b"\x80"), (2, b"\xa1\x40", b"\xfe\xfe")],
    "ETenms-B5-H": [(1, b"\x00", b"\x80"), (2, b"\xa1\x40", b"\xfe\xfe")],
    "B5pc-H": [(1, b"\x00", b"\x80"), (2, b"\xa1\x40", b"\xfe\xfe")],
    "HKscs-B5-H": [(1, b"\x00", b"\x80"), (2, b"\x81\x40", b"\xfe\xfe")],
    "KSC-EUC-H": [(1, b"\x00", b"\x80"), (2, b"\xa1\xa1", b"\xfe\xfe")],
    "KSCms-UHC-H": [(1, b"\x00", b"\x80"), (2, b"\x81\x41", b"\xfe\xfe")],
    "UniJIS-UTF8-H": [
        (1, b"\x00", b"\x7f"),
        (2, b"\xc2\x80", b"\xdf\xbf"),
        (3, b"\xe0\xa0\x80", b"\xef\xbf\xbf"),
        (4, b"\xf0\x90\x80\x80", b"\xf4\x8f\xbf\xbf"),
    ],
    "UniGB-UTF8-H": [
        (1, b"\x00", b"\x7f"),
        (2, b"\xc2\x80", b"\xdf\xbf"),
        (3, b"\xe0\xa0\x80", b"\xef\xbf\xbf"),
        (4, b"\xf0\x90\x80\x80", b"\xf4\x8f\xbf\xbf"),
    ],
    "UniCNS-UTF8-H": [
        (1, b"\x00", b"\x7f"),
        (2, b"\xc2\x80", b"\xdf\xbf"),
        (3, b"\xe0\xa0\x80", b"\xef\xbf\xbf"),
        (4, b"\xf0\x90\x80\x80", b"\xf4\x8f\xbf\xbf"),
    ],
    "UniKS-UTF8-H": [
        (1, b"\x00", b"\x7f"),
        (2, b"\xc2\x80", b"\xdf\xbf"),
        (3, b"\xe0\xa0\x80", b"\xef\xbf\xbf"),
        (4, b"\xf0\x90\x80\x80", b"\xf4\x8f\xbf\xbf"),
    ],
}


@dataclass(frozen=True)
class Glyph:
    key: GlyphKey
    code: int
    nbytes: int  # raw char code and its byte length (variable-length CMaps)
    origin: tuple[float, float]  # layout space, after Tm/Ts/CTM/Form /Matrix
    font: str  # /BaseFont without the "ABCDEF+" subset prefix
    size: float  # true size (Tfs * text-matrix scale)
    advance: float  # TJ units (thousandths of text space) incl. Tc/Tw/Tz, for the displacement
    mode: int  # text rendering mode 0-7
    mcid_span: int | None  # index of the enclosing BDC span in PageStream.spans, or None


@dataclass(frozen=True)
class Op:  # non-text drawing op: path paint, Do image/form, BI..EI, sh
    key: OpKey
    kind: str  # "path" | "image" | "inline_image" | "form" | "shading"
    bbox: tuple[float, float, float, float]  # layout space


@dataclass
class PageStream:
    glyphs: list[Glyph]
    ops: list[Op]
    spans: list[dict[str, Any]]  # {"key": OpKey of BDC, "actual_text": str|None, "glyphs": [GlyphKey ...]}
    forms: dict[str, int]  # content_id -> number of uses (Do count across the document)
    _space_keys: set[GlyphKey] = field(default_factory=set, repr=False)


# ---------------------------------------------------------------------------
# Matrix & low-level helpers
# ---------------------------------------------------------------------------


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


def _mat_pt(
    m: tuple[float, float, float, float, float, float], x: float, y: float
) -> tuple[float, float]:
    a, b, c, d, e, f = m
    return (a * x + c * y + e, b * x + d * y + f)


def _transform_rect(
    m: tuple[float, float, float, float, float, float],
    x0: float,
    y0: float,
    x1: float,
    y1: float,
) -> tuple[float, float, float, float]:
    pts = (
        _mat_pt(m, x0, y0),
        _mat_pt(m, x1, y0),
        _mat_pt(m, x0, y1),
        _mat_pt(m, x1, y1),
    )
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


def _str_obj_to_bytes(obj: Any) -> bytes:
    if isinstance(obj, ByteStringObject):
        return bytes(obj)
    if isinstance(obj, TextStringObject):
        return obj.original_bytes
    if isinstance(obj, bytes):
        return obj
    raise TypeError(f"Unexpected string operand type: {type(obj)}")


def _clean_font_name(name: str) -> str:
    name = name.lstrip("/")
    return _SUBSET_RE.sub("", name).strip()


def _fmt_tj_num(val: float) -> NumberObject | FloatObject:
    r = round(val, 4)
    if abs(r - round(r)) < 1e-4:
        return NumberObject(int(round(r)))
    return FloatObject(r)


def _resolve_obj(obj: Any) -> Any:
    return obj.get_object() if hasattr(obj, "get_object") else obj


# ---------------------------------------------------------------------------
# CMap & Font metrics
# ---------------------------------------------------------------------------


def _parse_cmap_stream(
    data: bytes,
) -> tuple[list[tuple[int, bytes, bytes]], dict[tuple[int, int], int], list[tuple[int, int, int, int]]]:
    """Parse codespace ranges, cidchar, and cidrange from a PDF CMap stream.

    Returns:
      - codespaces: list of (nbytes, low_bytes, high_bytes)
      - cid_chars: {(nbytes, code): cid}
      - cid_ranges: list of (nbytes, low_code, high_code, start_cid)
    """
    text = data.decode("latin-1", errors="ignore")
    codespaces: list[tuple[int, bytes, bytes]] = []
    cid_chars: dict[tuple[int, int], int] = {}
    cid_ranges: list[tuple[int, int, int, int]] = []

    for m in re.finditer(r"begincodespacerange\b(.*?)endcodespacerange\b", text, re.S):
        tokens = _HEX_TOKEN_RE.findall(m.group(1))
        for i in range(0, len(tokens) - 1, 2):
            lo_h, hi_h = tokens[i], tokens[i + 1]
            if len(lo_h) % 2:
                lo_h = "0" + lo_h
            if len(hi_h) % 2:
                hi_h = "0" + hi_h
            lo_b = bytes.fromhex(lo_h)
            hi_b = bytes.fromhex(hi_h)
            if len(lo_b) == len(hi_b) and 1 <= len(lo_b) <= 4:
                codespaces.append((len(lo_b), lo_b, hi_b))

    for m in re.finditer(r"begincidchar\b(.*?)endcidchar\b", text, re.S):
        for line in m.group(1).splitlines():
            line = line.strip()
            if not line:
                continue
            hm = _HEX_TOKEN_RE.search(line)
            if not hm:
                continue
            hex_s = hm.group(1)
            if len(hex_s) % 2:
                hex_s = "0" + hex_s
            code_b = bytes.fromhex(hex_s)
            rest = line[hm.end() :].strip().split()
            if rest:
                try:
                    cid = int(rest[0])
                    cid_chars[(len(code_b), int.from_bytes(code_b, "big"))] = cid
                except ValueError:
                    pass

    for m in re.finditer(r"begincidrange\b(.*?)endcidrange\b", text, re.S):
        for line in m.group(1).splitlines():
            line = line.strip()
            if not line:
                continue
            hms = list(_HEX_TOKEN_RE.finditer(line))
            if len(hms) < 2:
                continue
            lo_h, hi_h = hms[0].group(1), hms[1].group(1)
            if len(lo_h) % 2:
                lo_h = "0" + lo_h
            if len(hi_h) % 2:
                hi_h = "0" + hi_h
            lo_b = bytes.fromhex(lo_h)
            hi_b = bytes.fromhex(hi_h)
            rest = line[hms[1].end() :].strip().split()
            if len(lo_b) == len(hi_b) and rest:
                try:
                    start_cid = int(rest[0])
                    cid_ranges.append(
                        (
                            len(lo_b),
                            int.from_bytes(lo_b, "big"),
                            int.from_bytes(hi_b, "big"),
                            start_cid,
                        )
                    )
                except ValueError:
                    pass

    return codespaces, cid_chars, cid_ranges


def _parse_tounicode_spaces(fdict: DictionaryObject) -> set[tuple[int, int]]:
    """Return {(nbytes, code)} mapped to ASCII space (U+0020) in /ToUnicode, if present."""
    tu = fdict.get("/ToUnicode")
    if tu is None:
        return set()
    tu_obj = _resolve_obj(tu)
    if not isinstance(tu_obj, StreamObject):
        return set()
    try:
        text = tu_obj.get_data().decode("latin-1", errors="ignore")
    except Exception:
        return set()

    spaces: set[tuple[int, int]] = set()
    for m in re.finditer(r"beginbfchar\b(.*?)endbfchar\b", text, re.S):
        tokens = _HEX_TOKEN_RE.findall(m.group(1))
        for i in range(0, len(tokens) - 1, 2):
            src_h, dst_h = tokens[i], tokens[i + 1]
            if dst_h.upper() in ("0020", "00A0"):
                if len(src_h) % 2:
                    src_h = "0" + src_h
                b = bytes.fromhex(src_h)
                spaces.add((len(b), int.from_bytes(b, "big")))
    for m in re.finditer(r"beginbfrange\b(.*?)endbfrange\b", text, re.S):
        for line in m.group(1).splitlines():
            hms = _HEX_TOKEN_RE.findall(line)
            if len(hms) == 3 and "[" not in line:
                lo_h, hi_h, dst_h = hms
                if len(lo_h) % 2:
                    lo_h = "0" + lo_h
                if len(hi_h) % 2:
                    hi_h = "0" + hi_h
                lo_b, hi_b = bytes.fromhex(lo_h), bytes.fromhex(hi_h)
                if len(lo_b) == len(hi_b):
                    lo_i = int.from_bytes(lo_b, "big")
                    hi_i = int.from_bytes(hi_b, "big")
                    try:
                        dst_i = int(dst_h, 16)
                    except ValueError:
                        continue
                    for target in (0x0020, 0x00A0):
                        off = target - dst_i
                        if 0 <= off <= (hi_i - lo_i):
                            spaces.add((len(lo_b), lo_i + off))
    return spaces


def _build_code_to_char_map(fdict: DictionaryObject) -> dict[int, str]:
    enc = fdict.get("/Encoding")
    base_name = "/StandardEncoding"
    diffs = None
    if enc is not None:
        enc_obj = _resolve_obj(enc)
        if isinstance(enc_obj, NameObject):
            base_name = str(enc_obj)
        elif isinstance(enc_obj, DictionaryObject):
            if "/BaseEncoding" in enc_obj:
                base_name = str(_resolve_obj(enc_obj["/BaseEncoding"]))
            if "/Differences" in enc_obj:
                diffs = _resolve_obj(enc_obj["/Differences"])
    try:
        enc_map = encoding_dict_from_named_encoding(base_name)
    except Exception:
        enc_map = {}
    code_to_char: dict[int, str] = {}
    if isinstance(enc_map, dict):
        for k, v in enc_map.items():
            if isinstance(k, int) and isinstance(v, str):
                code_to_char[k] = v
    if not code_to_char:
        for c in range(32, 127):
            code_to_char[c] = chr(c)
    if diffs is not None:
        cur = 0
        for item in diffs:
            item = _resolve_obj(item)
            if isinstance(item, int):
                cur = int(item)
            elif isinstance(item, NameObject):
                gname = str(item)
                ch = adobe_glyphs.get(gname) or adobe_glyphs.get(gname.lstrip("/"))
                if ch:
                    code_to_char[cur] = ch
                cur += 1
    return code_to_char


class _FontMetrics:
    """Font width, CMap decoding, and Type3 FontMatrix metrics."""

    def __init__(self, fdict: DictionaryObject, trunc_simple_widths: bool = True) -> None:
        self.subtype = str(fdict.get("/Subtype", ""))
        self.base_font = str(fdict.get("/BaseFont", "")).lstrip("/")
        self.clean_name = _clean_font_name(self.base_font)
        self.is_cid = self.subtype == "/Type0"
        self.is_type3 = self.subtype == "/Type3"
        self.widths: dict[int, float] = {}
        self.default_width = 0.0
        self.font_matrix: tuple[float, float, float, float, float, float] = (
            0.001,
            0.0,
            0.0,
            0.001,
            0.0,
            0.0,
        )
        # CMap decoding state for Type0
        self._fixed_bytes: int | None = 1
        self._codespaces: list[tuple[int, bytes, bytes]] = []
        self._cid_chars: dict[tuple[int, int], int] = {}
        self._cid_ranges: list[tuple[int, int, int, int]] = []
        self._space_codes: set[tuple[int, int]] = _parse_tounicode_spaces(fdict)
        self._non_space_32 = False
        # Detect symbolic/math fonts where byte 32 is a visible glyph (e.g., rtxmi/CMMI10 0x20 = psi)
        if not self.is_cid:
            enc = fdict.get("/Encoding")
            if enc is not None:
                enc_obj = _resolve_obj(enc)
                if isinstance(enc_obj, DictionaryObject) and "/Differences" in enc_obj:
                    cur = 0
                    for item in _resolve_obj(enc_obj["/Differences"]):
                        item = _resolve_obj(item)
                        if isinstance(item, int):
                            cur = int(item)
                        elif isinstance(item, NameObject):
                            if cur == 32 and str(item) != "/space":
                                self._non_space_32 = True
                            cur += 1
            fd_raw = fdict.get("/FontDescriptor")
            if fd_raw is not None:
                fd_obj = _resolve_obj(fd_raw)
                if isinstance(fd_obj, DictionaryObject) and "/CharSet" in fd_obj:
                    cs_str = str(_resolve_obj(fd_obj["/CharSet"]))
                    if cs_str and "/space" not in cs_str:
                        self._non_space_32 = True

        if self.is_cid:
            self._fixed_bytes = 2
            self._load_cid_encoding(fdict.get("/Encoding"))
            desc = fdict.get("/DescendantFonts")
            if desc is not None:
                desc_obj = _resolve_obj(desc)
                if isinstance(desc_obj, ArrayObject) and len(desc_obj) > 0:
                    d0 = _resolve_obj(desc_obj[0])
                    if isinstance(d0, DictionaryObject):
                        self.default_width = float(d0.get("/DW", 1000))
                        w_arr = d0.get("/W")
                        if w_arr is not None:
                            w_arr = _resolve_obj(w_arr)
                            if isinstance(w_arr, ArrayObject):
                                i = 0
                                n = len(w_arr)
                                while i + 1 < n:
                                    c1 = int(_resolve_obj(w_arr[i]))
                                    nxt = _resolve_obj(w_arr[i + 1])
                                    if isinstance(nxt, ArrayObject):
                                        for j, val in enumerate(nxt):
                                            self.widths[c1 + j] = float(_resolve_obj(val))
                                        i += 2
                                    elif i + 2 < n:
                                        c2 = int(nxt)
                                        val = float(_resolve_obj(w_arr[i + 2]))
                                        for c in range(c1, c2 + 1):
                                            self.widths[c] = val
                                        i += 3
                                    else:
                                        break
            else:
                self.default_width = 1000.0
        else:
            if self.is_type3:
                fm = fdict.get("/FontMatrix")
                if fm is not None:
                    fm_obj = _resolve_obj(fm)
                    if isinstance(fm_obj, ArrayObject) and len(fm_obj) == 6:
                        self.font_matrix = tuple(float(_resolve_obj(v)) for v in fm_obj)  # type: ignore[assignment]
            fd = fdict.get("/FontDescriptor")
            if fd is not None:
                fd_obj = _resolve_obj(fd)
                if isinstance(fd_obj, DictionaryObject) and "/MissingWidth" in fd_obj:
                    self.default_width = float(_resolve_obj(fd_obj["/MissingWidth"]))
            fc = int(fdict.get("/FirstChar", 0))
            w_arr = fdict.get("/Widths")
            if w_arr is not None:
                w_arr = _resolve_obj(w_arr)
                if isinstance(w_arr, ArrayObject):
                    for idx, val in enumerate(w_arr):
                        fv = float(_resolve_obj(val))
                        if trunc_simple_widths and not self.is_type3:
                            fv = float(int(fv))
                        self.widths[fc + idx] = fv
            else:
                cfm = core_font_metrics.CORE_FONT_METRICS.get(self.clean_name)
                if cfm is not None:
                    c2ch = _build_code_to_char_map(fdict)
                    for code, ch in c2ch.items():
                        if ch in cfm.character_widths:
                            self.widths[code] = float(cfm.character_widths[ch])

    def _load_cid_encoding(self, enc: Any) -> None:
        if enc is None:
            self._fixed_bytes = 2
            return
        enc_obj = _resolve_obj(enc)
        if isinstance(enc_obj, NameObject):
            name = str(enc_obj).lstrip("/")
            if name in ("Identity-H", "Identity-V", "Adobe-Identity-0"):
                self._fixed_bytes = 2
            elif name in _PREDEFINED_VAR_CMAPS:
                self._codespaces = list(_PREDEFINED_VAR_CMAPS[name])
                self._fixed_bytes = None
            else:
                # Default for UCS2 / UTF16 / unknown predefined CMaps: 2-byte fixed
                self._fixed_bytes = 2
        elif isinstance(enc_obj, StreamObject):
            try:
                data = enc_obj.get_data()
            except Exception:
                self._fixed_bytes = 2
                return
            cs, cc, cr = _parse_cmap_stream(data)
            # Inherit /UseCMap if present
            use_cmap = enc_obj.get("/UseCMap")
            if use_cmap is not None:
                self._load_cid_encoding(use_cmap)
            if cs:
                self._codespaces = cs + self._codespaces
            self._cid_chars.update(cc)
            self._cid_ranges = cr + self._cid_ranges
            if self._codespaces:
                lengths = {r[0] for r in self._codespaces}
                if len(lengths) == 1 and not self._cid_chars and not self._cid_ranges:
                    # Single fixed length without remapping: still check if full range
                    only_len = next(iter(lengths))
                    self._fixed_bytes = only_len if only_len in (1, 2) else None
                else:
                    self._fixed_bytes = None
            else:
                self._fixed_bytes = 2

    def decode_codes(self, raw_bytes: bytes) -> list[tuple[int, int, int, bytes]]:
        """Decode raw string bytes into [(byte_pos, code, nbytes, bslice), ...]."""
        if self._fixed_bytes == 1:
            return [(pos, b, 1, raw_bytes[pos : pos + 1]) for pos, b in enumerate(raw_bytes)]
        if self._fixed_bytes == 2:
            out: list[tuple[int, int, int, bytes]] = []
            n = len(raw_bytes)
            for pos in range(0, n - 1, 2):
                bslice = raw_bytes[pos : pos + 2]
                out.append((pos, (bslice[0] << 8) | bslice[1], 2, bslice))
            return out

        # Variable-length CMap codespace decoding (PDF 1.7 §9.7.6.3)
        out = []
        n = len(raw_bytes)
        pos = 0
        # Sort codespace ranges by byte length ascending
        by_len: dict[int, list[tuple[bytes, bytes]]] = defaultdict(list)
        for length, lo, hi in self._codespaces:
            by_len[length].append((lo, hi))
        lengths = sorted(by_len.keys())

        while pos < n:
            matched_len = 0
            for length in lengths:
                if pos + length > n:
                    continue
                chunk = raw_bytes[pos : pos + length]
                for lo, hi in by_len[length]:
                    if all(lo[k] <= chunk[k] <= hi[k] for k in range(length)):
                        matched_len = length
                        break
                if matched_len:
                    break
            if matched_len == 0:
                matched_len = 1
            bslice = raw_bytes[pos : pos + matched_len]
            code = int.from_bytes(bslice, "big")
            out.append((pos, code, matched_len, bslice))
            pos += matched_len
        return out

    def _code_to_cid(self, code: int, nbytes: int) -> int:
        if not self.is_cid:
            return code
        cid = self._cid_chars.get((nbytes, code))
        if cid is not None:
            return cid
        for r_len, lo, hi, start_cid in self._cid_ranges:
            if r_len == nbytes and lo <= code <= hi:
                return start_cid + (code - lo)
        return code

    def raw_width(self, code: int, nbytes: int) -> float:
        cid = self._code_to_cid(code, nbytes)
        return self.widths.get(cid, self.default_width)

    def width_1000(self, code: int, nbytes: int) -> float:
        """Horizontal glyph advance in thousandths of text space."""
        w = self.raw_width(code, nbytes)
        if self.is_type3:
            return w * (self.font_matrix[0] * 1000.0)
        return w

    def is_space(self, code: int, nbytes: int) -> bool:
        if (nbytes, code) in self._space_codes:
            return True
        if (not self.is_cid) and nbytes == 1 and code == 32 and not self._non_space_32:
            return True
        return False


def _build_font_table(
    res: DictionaryObject | None, trunc_simple_widths: bool = True
) -> dict[str, _FontMetrics]:
    table: dict[str, _FontMetrics] = {}
    if not isinstance(res, DictionaryObject):
        return table
    fonts = res.get("/Font")
    if fonts is None:
        return table
    fonts = _resolve_obj(fonts)
    if not isinstance(fonts, DictionaryObject):
        return table
    for k, v in fonts.items():
        fdict = _resolve_obj(v)
        if isinstance(fdict, DictionaryObject):
            table[str(k)] = _FontMetrics(fdict, trunc_simple_widths=trunc_simple_widths)
    return table


# ---------------------------------------------------------------------------
# Document-wide Form XObject usage counting
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Fast content-stream tokenizer (same objects as pypdf's ContentStream.operations)
# ---------------------------------------------------------------------------

try:  # private pypdf helpers; without them every stream goes through pypdf
    from io import BytesIO as _BytesIO

    from pypdf.generic import FloatObject as _FloatObject
    from pypdf.generic import NumberObject as _NumberObject
    from pypdf.generic._data_structures import read_object as _pypdf_read_object
    from pypdf.generic._utils import StreamParsingBudget as _Budget
    from pypdf.generic._utils import create_string_object as _create_string_object
    from pypdf import get_configuration as _get_configuration

    _FAST_OK = True
except Exception:  # pragma: no cover
    _FAST_OK = False

_FT_WS = re.compile(rb"[\x00\t\n\f\r ]*")
_FT_NUM = re.compile(rb"[+-]?(?:\d+\.?\d*|\.\d+)(?![0-9+\-.])")
_FT_NAME = re.compile(rb"/[^\s()<>\[\]{}/%#\x80-\xff]*+(?![#\x80-\xff])")
_FT_STR = re.compile(rb"\(([^()\\]*)\)")
_FT_OP = re.compile(rb"[A-Za-z'\"][^\s()<>\[\]{}/%]*")
_FT_COMMENT = re.compile(rb"%[^\r\n]*[\r\n]?")
# numbers only, then an operator, all separated by plain whitespace: the common case in path-heavy streams.
# _FT_RUN matches a whole run of such lines; _FT_LINES then splits it into (operands, operator) in C.
_FT_LINE = (
    r"[\x00\t\n\f\r ]*(?:[+-]?(?:\d+\.?\d*|\.\d+)[\t\n\f\r ]+)*"
    r"(?!BI(?:[\s()<>\[\]{}/%]|$))[A-Za-z'\"][^\s()<>\[\]{}/%]{0,126}(?![^\s()<>\[\]{}/%])"
)
_FT_RUN = re.compile(("(?:" + _FT_LINE + ")+").encode())
_FT_LINES = re.compile(
    rb"[\x00\t\n\f\r ]*((?:[+-]?(?:\d+\.?\d*|\.\d+)[\t\n\f\r ]+)*)"
    rb"((?!BI(?:[\s()<>\[\]{}/%]|$))[A-Za-z'\"][^\s()<>\[\]{}/%]{0,126})(?![^\s()<>\[\]{}/%])"
)


class _FastFail(Exception):
    pass


_float_new = float.__new__
_int_new = int.__new__


def _fast_operations(data: bytes) -> list[tuple[Any, bytes]] | None:
    """Tokenize ``data`` into (operands, operator) like pypdf; None → caller uses pypdf.

    Operands of plain "numbers, then operator" lines are plain ``float`` (parse() only ever calls float()
    on them); every other operand is the same pypdf object pypdf would build.
    Handles numbers, plain names, strings without escapes/nesting, arrays of those; everything
    else in operand position is delegated to pypdf's own ``read_object`` at that offset.
    Inline images (BI) and anything unexpected give up on the whole stream.
    """
    if not _FAST_OK:
        return None
    n = len(data)
    ws = _FT_WS.match
    num_m = _FT_NUM.match
    name_m = _FT_NAME.match
    str_m = _FT_STR.match
    op_m = _FT_OP.match
    run_m = _FT_RUN.match
    lines = _FT_LINES.findall
    com_m = _FT_COMMENT.match
    mk_float = _FloatObject
    mk_int = _NumberObject
    mk_name = NameObject
    mk_str = _create_string_object
    bio: list[Any] = []

    def delegate(pos: int) -> tuple[Any, int]:
        if not bio:
            bio.append(_BytesIO(data))
        b = bio[0]
        b.seek(pos)
        obj = _pypdf_read_object(
            b, None, None, parsing_budget=_Budget(remaining_budget=_get_configuration().stream_parsing_work_maximum_count)
        )
        return obj, b.tell()

    def value(pos: int) -> tuple[Any, int]:
        c = data[pos]
        if c == 0x2F:  # /
            m = name_m(data, pos)
            if m is None or m.end() - pos > 100:
                return delegate(pos)
            return mk_name(m.group().decode("ascii")), m.end()
        if c == 0x28:  # (
            m = str_m(data, pos)
            if m is None:
                return delegate(pos)
            return mk_str(m.group(1), None), m.end()
        if c == 0x5B:  # [
            return array(pos)
        if c in b"0123456789+-.":
            m = num_m(data, pos)
            if m is None or m.end() - pos > 18:
                return delegate(pos)
            tok = m.group()
            return (mk_float(tok) if b"." in tok else mk_int(tok)), m.end()
        return delegate(pos)

    def array(pos: int) -> tuple[Any, int]:
        arr = ArrayObject()
        pos += 1
        while True:
            pos = ws(data, pos).end()
            if pos >= n:
                return arr, pos
            c = data[pos]
            if c == 0x5D:  # ]
                return arr, pos + 1
            if c == 0x25:  # % comment inside an array: leave to pypdf
                raise _FastFail
            obj, pos = value(pos)
            arr.append(obj)

    ops: list[tuple[Any, bytes]] = []
    operands: list[Any] = []
    pos = 0
    try:
        while True:
            pos = ws(data, pos).end()
            if pos >= n:
                break
            if not operands:
                m = run_m(data, pos)
                if m is not None:
                    end = m.end()
                    ops.extend((list(map(float, nm.split())), o) for nm, o in lines(data, pos, end))
                    pos = end
                    continue
            c = data[pos]
            if c == 0x25:  # %
                pos = com_m(data, pos).end()
                continue
            m = op_m(data, pos)
            if m is not None:
                op = m.group()
                if op == b"BI" or len(op) >= 128 or (op[0] == 0x52 and not op[1:2].isalpha()):
                    return None  # inline image, over-long operator or "N N R" (pypdf reads that as a reference)
                ops.append((operands, op))
                operands = []
                pos = m.end()
                continue
            obj, pos = value(pos)
            operands.append(obj)
    except Exception:
        return None
    return ops


def _cached_ops(writer: pypdf.PdfWriter, stream_obj: Any) -> list[Any]:
    """Parsed operations of a content stream, shared by the Do-count scan and every parse() call.

    Keyed by the decoded bytes, so a rewritten stream is never served stale. Read-only use.
    """
    cs = ContentStream(stream_obj, writer)
    cache = getattr(writer, "_rx_ops_cache", None)
    if cache is None:
        cache = {}
        setattr(writer, "_rx_ops_cache", cache)
    data = cs.get_data()
    ops = cache.get(data)
    if ops is None:
        ops = _fast_operations(data)
        if ops is None:
            ops = cs.operations
        cache[data] = ops
    return ops


def _count_document_form_uses(writer: pypdf.PdfWriter) -> dict[int, int]:
    """Count total Do invocations of each Form XObject idnum across all pages and forms."""
    cached = getattr(writer, "_rx_form_do_counts", None)
    if isinstance(cached, dict):
        return cached

    counts: Counter[int] = Counter()
    # Map form_idnum -> list of (child_idnum, count_in_parent)
    form_children: dict[int, list[tuple[int, int]]] = {}

    def _scan_stream_dos(
        stream_obj: Any,
        resources: DictionaryObject | None,
        multiplier: int,
        visiting: frozenset[int],
    ) -> None:
        if stream_obj is None or not isinstance(resources, DictionaryObject):
            return
        xobjs_raw = resources.get("/XObject")
        if xobjs_raw is None:
            return
        xobjs = _resolve_obj(xobjs_raw)
        if not isinstance(xobjs, DictionaryObject):
            return

        # Fast check: are there any Form XObjects in resources?
        form_map: dict[str, tuple[int | None, DictionaryObject]] = {}
        for k in xobjs.keys():
            raw_v = xobjs.raw_get(k)
            obj = _resolve_obj(raw_v)
            if isinstance(obj, DictionaryObject) and obj.get("/Subtype") == "/Form":
                idnum = raw_v.idnum if isinstance(raw_v, IndirectObject) else None
                form_map[str(k)] = (idnum, obj)
        if not form_map:
            return

        try:
            scan_ops = _cached_ops(writer, stream_obj)
        except Exception:
            return
        do_counts: Counter[str] = Counter(
            str(operands[0]) for operands, op in scan_ops if op == b"Do" and operands
        )
        for xname, n_calls in do_counts.items():
            entry = form_map.get(xname)
            if entry is None:
                continue
            idnum, fobj = entry
            total_calls = n_calls * multiplier
            if idnum is not None:
                counts[idnum] += total_calls
                if idnum in visiting:
                    continue
                sub_res = _resolve_obj(fobj.get("/Resources")) if "/Resources" in fobj else resources
                if idnum not in form_children:
                    # Scan once to record direct child form calls
                    sub_counts_before = dict(counts)
                    _scan_stream_dos(fobj, sub_res, total_calls, visiting | {idnum})
                    # Note: we scanned directly with total_calls; for subsequent visits we could reuse
                    _ = sub_counts_before
                else:
                    _scan_stream_dos(fobj, sub_res, total_calls, visiting | {idnum})
            else:
                sub_res = _resolve_obj(fobj.get("/Resources")) if "/Resources" in fobj else resources
                _scan_stream_dos(fobj, sub_res, total_calls, visiting)

    for page in writer.pages:
        res = _resolve_obj(page.get("/Resources")) if "/Resources" in page else None
        _scan_stream_dos(page.get("/Contents"), res, 1, frozenset())

    result = dict(counts)
    setattr(writer, "_rx_form_do_counts", result)
    return result


def _invalidate_form_cache(writer: pypdf.PdfWriter) -> None:
    if hasattr(writer, "_rx_form_do_counts"):
        delattr(writer, "_rx_form_do_counts")


# ---------------------------------------------------------------------------
# Public API: open_writer
# ---------------------------------------------------------------------------


def open_writer(pdf: Path) -> pypdf.PdfWriter:
    """Open a PDF with pypdf, decrypting owner-password PDFs with the empty password."""
    reader = pypdf.PdfReader(str(pdf))
    if reader.is_encrypted:
        reader.decrypt("")
    return pypdf.PdfWriter(clone_from=reader)


# ---------------------------------------------------------------------------
# Public API: parse
# ---------------------------------------------------------------------------


def _extract_actual_text(props_operand: Any, resources: DictionaryObject | None) -> str | None:
    props = _resolve_obj(props_operand)
    if isinstance(props, NameObject) and isinstance(resources, DictionaryObject):
        prop_res = _resolve_obj(resources.get("/Properties"))
        if isinstance(prop_res, DictionaryObject):
            props = _resolve_obj(prop_res.get(str(props)))
    if isinstance(props, DictionaryObject) and "/ActualText" in props:
        val = _resolve_obj(props["/ActualText"])
        if isinstance(val, (TextStringObject, ByteStringObject)):
            return str(val)
        return str(val) if val is not None else None
    return None


def parse(writer: pypdf.PdfWriter, page_index: int) -> PageStream:
    """Parse a page's content stream (and Form XObjects) into Glyphs, Ops, Spans, and Form counts."""
    page = writer.pages[page_index]
    doc_form_uses = _count_document_form_uses(writer)

    glyphs: list[Glyph] = []
    ops_out: list[Op] = []
    spans: list[dict[str, Any]] = []
    forms: dict[str, int] = {}
    space_keys: set[GlyphKey] = set()

    # MediaBox fallback for shading without /BBox
    mb = page.mediabox
    page_bbox = (float(mb.left), float(mb.bottom), float(mb.right), float(mb.top))

    # Graphics/text state carried across Form XObject boundaries (PDF 1.7 §8.10.2)
    class _State:
        __slots__ = (
            "ctm",
            "Tc",
            "Tw",
            "Th",
            "Tl",
            "Ts",
            "Tfs",
            "Tr",
            "lw",
            "cur_font",
            "cur_font_key",
            "Tm",
            "Tlm",
        )

        def __init__(self) -> None:
            self.ctm = _IDENTITY_MATRIX
            self.Tc = 0.0
            self.Tw = 0.0
            self.Th = 1.0
            self.Tl = 0.0
            self.Ts = 0.0
            self.Tfs = 0.0
            self.Tr = 0
            self.lw = 1.0
            self.cur_font: _FontMetrics | None = None
            self.cur_font_key = ""
            self.Tm = _IDENTITY_MATRIX
            self.Tlm = _IDENTITY_MATRIX

        def snapshot(self) -> tuple[Any, ...]:
            return (
                self.ctm,
                self.Tc,
                self.Tw,
                self.Th,
                self.Tl,
                self.Ts,
                self.Tfs,
                self.Tr,
                self.lw,
                self.cur_font,
                self.cur_font_key,
            )

        def restore(self, snap: tuple[Any, ...]) -> None:
            (
                self.ctm,
                self.Tc,
                self.Tw,
                self.Th,
                self.Tl,
                self.Ts,
                self.Tfs,
                self.Tr,
                self.lw,
                self.cur_font,
                self.cur_font_key,
            ) = snap

    st = _State()

    # Marked-content stack: entries are span_index (int) for BDC, or None for BMC
    mc_stack: list[int | None] = []

    def _walk(
        content_id: str,
        stream_obj: Any,
        resources: DictionaryObject | None,
        depth: int,
    ) -> None:
        if stream_obj is None or depth > 12:
            return
        ops = _cached_ops(writer, stream_obj)
        fonts = _build_font_table(resources)
        xobjs: DictionaryObject | None = None
        shadings: DictionaryObject | None = None
        if isinstance(resources, DictionaryObject):
            xo_raw = resources.get("/XObject")
            if xo_raw is not None:
                xo_obj = _resolve_obj(xo_raw)
                if isinstance(xo_obj, DictionaryObject):
                    xobjs = xo_obj
            sh_raw = resources.get("/Shading")
            if sh_raw is not None:
                sh_obj = _resolve_obj(sh_raw)
                if isinstance(sh_obj, DictionaryObject):
                    shadings = sh_obj

        do_local_counts: Counter[str] = Counter(
            str(operands[0]) for operands, op in ops if op == b"Do" and operands
        )

        gs_stack: list[tuple[Any, ...]] = []
        path_pts: list[tuple[float, float]] = []
        cur_pt: tuple[float, float] | None = None

        def _active_bdc_spans() -> list[int]:
            return [s for s in mc_stack if s is not None]

        def _emit_text_items(items: list[Any], op_idx: int) -> None:
            cur_font = st.cur_font
            if cur_font is None:
                return
            active_spans = _active_bdc_spans()
            inner_span = active_spans[-1] if active_spans else None

            # Precompute true font size for this op
            trm = _mat_mul(st.Tm, st.ctm)
            scale = math.hypot(trm[2], trm[3])
            if scale <= 1e-6:
                scale = math.hypot(trm[0], trm[1])
            true_size = abs(st.Tfs) * scale

            for item_idx, item in enumerate(items):
                if isinstance(item, (NumberObject, FloatObject, int, float)):
                    fv = float(item)
                    dx = (-fv / 1000.0 * st.Tfs) * st.Th
                    st.Tm = _mat_mul((1.0, 0.0, 0.0, 1.0, dx, 0.0), st.Tm)
                else:
                    raw_bytes = _str_obj_to_bytes(item)
                    for byte_pos, code, nbytes, _bslice in cur_font.decode_codes(raw_bytes):
                        if cur_font.is_type3:
                            fm = cur_font.font_matrix
                            ox_t = fm[4] * st.Tfs * st.Th
                            oy_t = fm[5] * st.Tfs + st.Ts
                        else:
                            ox_t = 0.0
                            oy_t = st.Ts
                        tx, ty = _mat_pt(st.Tm, ox_t, oy_t)
                        gx, gy = _mat_pt(st.ctm, tx, ty)

                        w1000 = cur_font.width_1000(code, nbytes)
                        is_tw_space = (not cur_font.is_cid) and nbytes == 1 and code == 32
                        extra = st.Tc + (st.Tw if is_tw_space else 0.0)
                        dx = (w1000 / 1000.0 * st.Tfs + extra) * st.Th
                        if cur_font.is_type3 and cur_font.font_matrix[1] != 0.0:
                            dy = cur_font.raw_width(code, nbytes) * cur_font.font_matrix[1] * st.Tfs
                        else:
                            dy = 0.0
                        st.Tm = _mat_mul((1.0, 0.0, 0.0, 1.0, dx, dy), st.Tm)

                        extra_tj = (1000.0 * extra / st.Tfs) if st.Tfs != 0.0 else 0.0
                        advance = w1000 + extra_tj

                        gkey: GlyphKey = (content_id, op_idx, item_idx, byte_pos)
                        glyphs.append(
                            Glyph(
                                key=gkey,
                                code=code,
                                nbytes=nbytes,
                                origin=(gx, gy),
                                font=cur_font.clean_name,
                                size=true_size,
                                advance=advance,
                                mode=st.Tr,
                                mcid_span=inner_span,
                            )
                        )
                        if cur_font.is_space(code, nbytes):
                            space_keys.add(gkey)
                        for sp_idx in active_spans:
                            spans[sp_idx]["glyphs"].append(gkey)

        for op_idx, (operands, op) in enumerate(ops):
            # path construction first: it dominates vector-heavy streams (order of tests does not matter)
            if op == b"m":
                pt = (float(operands[0]), float(operands[1]))
                path_pts.append(pt)
                cur_pt = pt
            elif op == b"l":
                pt = (float(operands[0]), float(operands[1]))
                path_pts.append(pt)
                cur_pt = pt
            elif op == b"c":
                p1 = (float(operands[0]), float(operands[1]))
                p2 = (float(operands[2]), float(operands[3]))
                p3 = (float(operands[4]), float(operands[5]))
                path_pts.extend((p1, p2, p3))
                cur_pt = p3
            elif op == b"v":
                if cur_pt is not None:
                    path_pts.append(cur_pt)
                p2 = (float(operands[0]), float(operands[1]))
                p3 = (float(operands[2]), float(operands[3]))
                path_pts.extend((p2, p3))
                cur_pt = p3
            elif op == b"y":
                p1 = (float(operands[0]), float(operands[1]))
                p3 = (float(operands[2]), float(operands[3]))
                path_pts.extend((p1, p3))
                cur_pt = p3
            elif op == b"re":
                rx, ry, rw, rh = (
                    float(operands[0]),
                    float(operands[1]),
                    float(operands[2]),
                    float(operands[3]),
                )
                path_pts.extend(((rx, ry), (rx + rw, ry), (rx, ry + rh), (rx + rw, ry + rh)))
                cur_pt = (rx, ry)
            elif op == b"n":
                path_pts.clear()
                cur_pt = None
            elif op == b"q":
                gs_stack.append(st.snapshot())
            elif op == b"Q":
                if gs_stack:
                    st.restore(gs_stack.pop())
            elif op == b"cm":
                if len(operands) == 6:
                    m = tuple(float(x) for x in operands)
                    st.ctm = _mat_mul(m, st.ctm)  # type: ignore[arg-type]
            elif op == b"w":
                if operands:
                    st.lw = float(operands[0])
            elif op == b"BT":
                st.Tm = _IDENTITY_MATRIX
                st.Tlm = _IDENTITY_MATRIX
            elif op == b"ET":
                pass
            elif op == b"Tc":
                st.Tc = float(operands[0])
            elif op == b"Tw":
                st.Tw = float(operands[0])
            elif op == b"Tz":
                st.Th = float(operands[0]) / 100.0
            elif op == b"TL":
                st.Tl = float(operands[0])
            elif op == b"Ts":
                st.Ts = float(operands[0])
            elif op == b"Tr":
                st.Tr = int(float(operands[0]))
            elif op == b"Tf":
                st.cur_font_key = str(operands[0])
                st.Tfs = float(operands[1])
                st.cur_font = fonts.get(st.cur_font_key)
            elif op == b"Td":
                tx, ty = float(operands[0]), float(operands[1])
                st.Tlm = _mat_mul((1.0, 0.0, 0.0, 1.0, tx, ty), st.Tlm)
                st.Tm = st.Tlm
            elif op == b"TD":
                tx, ty = float(operands[0]), float(operands[1])
                st.Tl = -ty
                st.Tlm = _mat_mul((1.0, 0.0, 0.0, 1.0, tx, ty), st.Tlm)
                st.Tm = st.Tlm
            elif op == b"Tm":
                if len(operands) == 6:
                    m = tuple(float(x) for x in operands)
                    st.Tm = m  # type: ignore[assignment]
                    st.Tlm = m  # type: ignore[assignment]
            elif op == b"T*":
                st.Tlm = _mat_mul((1.0, 0.0, 0.0, 1.0, 0.0, -st.Tl), st.Tlm)
                st.Tm = st.Tlm
            elif op == b"Tj":
                _emit_text_items([operands[0]], op_idx)
            elif op == b"TJ":
                _emit_text_items(operands[0], op_idx)
            elif op == b"'":
                st.Tlm = _mat_mul((1.0, 0.0, 0.0, 1.0, 0.0, -st.Tl), st.Tlm)
                st.Tm = st.Tlm
                _emit_text_items([operands[0]], op_idx)
            elif op == b'"':
                st.Tw = float(operands[0])
                st.Tc = float(operands[1])
                st.Tlm = _mat_mul((1.0, 0.0, 0.0, 1.0, 0.0, -st.Tl), st.Tlm)
                st.Tm = st.Tlm
                _emit_text_items([operands[2]], op_idx)
            elif op == b"BDC":
                actual = _extract_actual_text(operands[1], resources) if len(operands) >= 2 else None
                sp_idx = len(spans)
                spans.append({"key": (content_id, op_idx), "actual_text": actual, "glyphs": []})
                mc_stack.append(sp_idx)
            elif op == b"BMC":
                mc_stack.append(None)
            elif op == b"EMC":
                if mc_stack:
                    mc_stack.pop()
            elif op in _PATH_PAINT_OPS:
                if path_pts:
                    tpts = [_mat_pt(st.ctm, px, py) for px, py in path_pts]
                    xs = [p[0] for p in tpts]
                    ys = [p[1] for p in tpts]
                    pad_lw = 0.0
                    if op in _PATH_STROKE_OPS and st.lw > 0:
                        s_scale = max(
                            math.hypot(st.ctm[0], st.ctm[1]),
                            math.hypot(st.ctm[2], st.ctm[3]),
                        )
                        pad_lw = 0.5 * st.lw * s_scale
                    bbox = (min(xs) - pad_lw, min(ys) - pad_lw, max(xs) + pad_lw, max(ys) + pad_lw)
                    ops_out.append(Op(key=(content_id, op_idx), kind="path", bbox=bbox))
                path_pts.clear()
                cur_pt = None
            elif op == b"INLINE IMAGE":
                bbox = _transform_rect(st.ctm, 0.0, 0.0, 1.0, 1.0)
                ops_out.append(Op(key=(content_id, op_idx), kind="inline_image", bbox=bbox))
            elif op == b"sh":
                bbox = page_bbox
                if shadings is not None and operands:
                    sh_obj = _resolve_obj(shadings.get(str(operands[0])))
                    if isinstance(sh_obj, DictionaryObject) and "/BBox" in sh_obj:
                        sb = _resolve_obj(sh_obj["/BBox"])
                        if isinstance(sb, ArrayObject) and len(sb) == 4:
                            bbox = _transform_rect(
                                st.ctm,
                                float(_resolve_obj(sb[0])),
                                float(_resolve_obj(sb[1])),
                                float(_resolve_obj(sb[2])),
                                float(_resolve_obj(sb[3])),
                            )
                ops_out.append(Op(key=(content_id, op_idx), kind="shading", bbox=bbox))
            elif op == b"Do":
                if xobjs is None or not operands:
                    continue
                xname = str(operands[0])
                if xname not in xobjs:
                    continue
                raw_ref = xobjs.raw_get(xname)
                xo = _resolve_obj(raw_ref)
                if not isinstance(xo, DictionaryObject):
                    continue
                subtype = xo.get("/Subtype")
                if subtype == "/Image":
                    bbox = _transform_rect(st.ctm, 0.0, 0.0, 1.0, 1.0)
                    ops_out.append(Op(key=(content_id, op_idx), kind="image", bbox=bbox))
                elif subtype == "/Form":
                    fm = xo.get("/Matrix")
                    if fm is not None:
                        fm_arr = _resolve_obj(fm)
                        fm_t = tuple(float(_resolve_obj(v)) for v in fm_arr)
                        sub_ctm = _mat_mul(fm_t, st.ctm)  # type: ignore[arg-type]
                    else:
                        sub_ctm = st.ctm

                    fb = xo.get("/BBox")
                    if fb is not None:
                        fb_arr = _resolve_obj(fb)
                        if isinstance(fb_arr, ArrayObject) and len(fb_arr) == 4:
                            fbbox = _transform_rect(
                                sub_ctm,
                                float(_resolve_obj(fb_arr[0])),
                                float(_resolve_obj(fb_arr[1])),
                                float(_resolve_obj(fb_arr[2])),
                                float(_resolve_obj(fb_arr[3])),
                            )
                        else:
                            fbbox = page_bbox
                    else:
                        fbbox = page_bbox

                    ops_out.append(Op(key=(content_id, op_idx), kind="form", bbox=fbbox))

                    child_cid = f"{content_id}/{op_idx}"
                    idnum = raw_ref.idnum if isinstance(raw_ref, IndirectObject) else None
                    if idnum is not None:
                        forms[child_cid] = max(doc_form_uses.get(idnum, 1), do_local_counts[xname])
                    else:
                        forms[child_cid] = do_local_counts[xname]

                    # PDF 1.7 §8.10.2: save graphics/text state, inherit into Form, restore on exit
                    saved = st.snapshot()
                    saved_tm, saved_tlm = st.Tm, st.Tlm
                    st.ctm = sub_ctm
                    sub_res = _resolve_obj(xo.get("/Resources")) if "/Resources" in xo else resources
                    _walk(child_cid, xo, sub_res, depth + 1)
                    st.restore(saved)
                    st.Tm, st.Tlm = saved_tm, saved_tlm

    page_res = _resolve_obj(page.get("/Resources")) if "/Resources" in page else None
    _walk("page", page.get("/Contents"), page_res, 0)

    return PageStream(
        glyphs=glyphs,
        ops=ops_out,
        spans=spans,
        forms=forms,
        _space_keys=space_keys,
    )


# ---------------------------------------------------------------------------
# Public API: match
# ---------------------------------------------------------------------------


def match(ps: PageStream, chars: list[dict[str, Any]]) -> tuple[dict[int, GlyphKey], list[int]]:
    """Match PDFium char records to parsed stream Glyphs by origin (<= 0.1 pt) and font.

    Keys in the returned dict are c["i"] (PDFium's raw char index).
    """
    tol = 0.1
    tol2 = tol * tol
    lig_tol2 = 0.02 * 0.02

    # Spatial grid bucketed at 1.0 pt resolution
    grid: dict[tuple[int, int], list[int]] = defaultdict(list)
    for idx, g in enumerate(ps.glyphs):
        gx, gy = g.origin
        grid[(math.floor(gx), math.floor(gy))].append(idx)

    space_keys = ps._space_keys
    used = [False] * len(ps.glyphs)
    matched: dict[int, GlyphKey] = {}
    unmatched: list[int] = []

    for c in chars:
        ci = int(c["i"])
        ox, oy = float(c["origin"][0]), float(c["origin"][1])
        cf = _clean_font_name(str(c.get("font", "")))
        ch = str(c.get("u", ""))
        want_non_space = bool(ch) and not ch.isspace()
        bx, by = math.floor(ox), math.floor(oy)

        best_unused = -1
        best_unused_d2 = tol2 + 1.0
        best_used = -1
        best_used_d2 = lig_tol2 + 1.0
        best_sp_unused = -1
        best_sp_d2 = tol2 + 1.0

        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                bucket = grid.get((bx + dx, by + dy))
                if not bucket:
                    continue
                for gidx in bucket:
                    g = ps.glyphs[gidx]
                    if g.font != cf:
                        continue
                    d2 = (g.origin[0] - ox) ** 2 + (g.origin[1] - oy) ** 2
                    if d2 > tol2:
                        continue
                    is_sp = g.key in space_keys
                    if want_non_space and is_sp:
                        if not used[gidx] and d2 < best_sp_d2:
                            best_sp_d2 = d2
                            best_sp_unused = gidx
                        continue
                    if not used[gidx]:
                        if d2 < best_unused_d2:
                            best_unused_d2 = d2
                            best_unused = gidx
                    else:
                        if d2 <= lig_tol2 and d2 < best_used_d2:
                            best_used_d2 = d2
                            best_used = gidx

        if best_unused >= 0:
            used[best_unused] = True
            matched[ci] = ps.glyphs[best_unused].key
        elif best_used >= 0:
            # Multi-codepoint ToUnicode ligature expansion (e.g. 'fi' -> 'f', 'i' at same origin)
            matched[ci] = ps.glyphs[best_used].key
        elif best_sp_unused >= 0:
            used[best_sp_unused] = True
            matched[ci] = ps.glyphs[best_sp_unused].key
        else:
            unmatched.append(ci)

    return matched, unmatched


# ---------------------------------------------------------------------------
# Public API: ops_in_box
# ---------------------------------------------------------------------------


def ops_in_box(
    ps: PageStream, bbox: tuple[float, float, float, float] | list[float], pad: float = 0.5
) -> list[OpKey]:
    """Return non-text OpKeys whose bbox lies entirely inside bbox + pad."""
    bx0 = float(bbox[0]) - pad
    by0 = float(bbox[1]) - pad
    bx1 = float(bbox[2]) + pad
    by1 = float(bbox[3]) + pad
    out: list[OpKey] = []
    for op in ps.ops:
        ox0, oy0, ox1, oy1 = op.bbox
        if ox0 >= bx0 and oy0 >= by0 and ox1 <= bx1 and oy1 <= by1:
            out.append(op.key)
    return out


# ---------------------------------------------------------------------------
# Public API: bridged_spaces
# ---------------------------------------------------------------------------


def bridged_spaces(ps: PageStream, dropped: set[GlyphKey]) -> set[GlyphKey]:
    """Return space glyphs lying between two dropped glyphs of the same text line
    (or inside an op whose non-space glyphs are all dropped)."""
    if not dropped:
        return set()

    extra: set[GlyphKey] = set()

    def _is_space(g: Glyph) -> bool:
        return g.key in ps._space_keys

    # 1. Group glyphs by content_id and visual line (same baseline within tolerance)
    by_cid: dict[str, list[Glyph]] = defaultdict(list)
    for g in ps.glyphs:
        by_cid[g.key[0]].append(g)

    for _cid, cg in by_cid.items():
        # Split into lines by baseline jump
        lines: list[list[Glyph]] = []
        for g in cg:
            if not lines:
                lines.append([g])
            else:
                prev = lines[-1][-1]
                tol_y = max(1.0, 0.25 * max(prev.size, g.size, 1.0))
                if abs(g.origin[1] - prev.origin[1]) <= tol_y:
                    lines[-1].append(g)
                else:
                    lines.append([g])

        for line in lines:
            # Check if any glyph on this line is dropped
            if not any(g.key in dropped for g in line):
                continue
            n = len(line)
            for idx, g in enumerate(line):
                if g.key in dropped or not _is_space(g):
                    continue
                # Find nearest non-space before and after on the same line (same font as space)
                prev_drop = False
                for p in range(idx - 1, -1, -1):
                    if not _is_space(line[p]):
                        prev_drop = (line[p].key in dropped) and (line[p].font == g.font)
                        break
                next_drop = False
                for p in range(idx + 1, n):
                    if not _is_space(line[p]):
                        next_drop = (line[p].key in dropped) and (line[p].font == g.font)
                        break
                if prev_drop and next_drop:
                    extra.add(g.key)

    # 2. Within any single text-showing op that has >=1 dropped non-space glyph and 0 kept non-space glyphs,
    #    drop remaining spaces in that op so no orphan spaces stay.
    by_op: dict[tuple[str, int], list[Glyph]] = defaultdict(list)
    for g in ps.glyphs:
        by_op[(g.key[0], g.key[1])].append(g)

    for _op_key, og in by_op.items():
        has_dropped_nonsp = False
        has_kept_nonsp = False
        for g in og:
            if not _is_space(g):
                if g.key in dropped:
                    has_dropped_nonsp = True
                else:
                    has_kept_nonsp = True
        if has_dropped_nonsp and not has_kept_nonsp:
            for g in og:
                if g.key not in dropped and _is_space(g):
                    extra.add(g.key)

    return extra


# ---------------------------------------------------------------------------
# Public API: edit
# ---------------------------------------------------------------------------


def _needs_trailing_tm_preserve(ops: list[tuple[Any, bytes]], start_idx: int) -> bool:
    """Return True if a subsequent text-showing op in the same BT..ET occurs before
    any text-positioning reset (Td, TD, Tm, T*, ET, ', ")."""
    for j in range(start_idx + 1, len(ops)):
        op = ops[j][1]
        if op in (b"Td", b"TD", b"Tm", b"T*", b"ET", b"'", b'"'):
            return False
        if op in (b"Tj", b"TJ"):
            return True
    return False


def _ensure_private_xobject_dict(owner: DictionaryObject) -> DictionaryObject:
    """Ensure owner['/Resources']['/XObject'] is a private (non-shared) dict on owner."""
    res_val = owner.raw_get("/Resources") if "/Resources" in owner else None
    if isinstance(res_val, IndirectObject):
        res_copy = DictionaryObject(res_val.get_object())
        owner[NameObject("/Resources")] = res_copy
        res = res_copy
    elif isinstance(res_val, DictionaryObject):
        res = DictionaryObject(res_val)
        owner[NameObject("/Resources")] = res
    else:
        res = DictionaryObject()
        owner[NameObject("/Resources")] = res

    xo_val = res.raw_get("/XObject") if "/XObject" in res else None
    if isinstance(xo_val, IndirectObject):
        xo_copy = DictionaryObject(xo_val.get_object())
        res[NameObject("/XObject")] = xo_copy
        return xo_copy
    elif isinstance(xo_val, DictionaryObject):
        xo_copy = DictionaryObject(xo_val)
        res[NameObject("/XObject")] = xo_copy
        return xo_copy
    else:
        xo = DictionaryObject()
        res[NameObject("/XObject")] = xo
        return xo


def _strip_actual_text_from_bdc(
    operands: list[Any], resources: DictionaryObject | None
) -> list[Any]:
    """Return BDC operands with /ActualText removed from the property dictionary."""
    if len(operands) < 2:
        return operands
    tag, props_op = operands[0], operands[1]
    props = _resolve_obj(props_op)
    if isinstance(props, NameObject) and isinstance(resources, DictionaryObject):
        prop_res = _resolve_obj(resources.get("/Properties"))
        if isinstance(prop_res, DictionaryObject):
            props = _resolve_obj(prop_res.get(str(props)))
    if not isinstance(props, DictionaryObject):
        return operands
    new_props = DictionaryObject()
    for k, v in props.items():
        if str(k) == "/ActualText":
            continue
        new_props[k] = v
    return [tag, new_props]


def edit(
    writer: pypdf.PdfWriter,
    page_index: int,
    ps: PageStream,
    drop_glyphs: set[GlyphKey],
    drop_ops: set[OpKey],
) -> None:
    """Edit a page's content stream (and Form XObjects) in place on writer.

    - Rewrites only streams containing dropped items.
    - Replaces dropped glyphs with TJ displacements of -(advance).
    - Removes dropped ops (including path construction ops of dropped paths).
    - Clones shared Form XObjects before editing so other uses stay untouched.
    - Removes /ActualText from BDC spans whose glyphs are all dropped.
    - Keeps all other operators byte-identical.
    """
    if not drop_glyphs and not drop_ops:
        return

    # Index glyphs by key for O(1) lookup of advance and nbytes
    glyph_by_key: dict[GlyphKey, Glyph] = {g.key: g for g in ps.glyphs}

    # Index dropped glyphs by content_id -> op_idx -> item_idx -> set[byte_pos]
    drop_g_by_cid: dict[str, dict[int, dict[int, set[int]]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(set))
    )
    for gkey in drop_glyphs:
        cid, op_idx, item_idx, bpos = gkey
        drop_g_by_cid[cid][op_idx][item_idx].add(bpos)

    # Index dropped ops by content_id -> set[op_idx]
    drop_o_by_cid: dict[str, set[int]] = defaultdict(set)
    for cid, op_idx in drop_ops:
        drop_o_by_cid[cid].add(op_idx)

    # Find BDC spans whose /ActualText must be stripped (all glyphs dropped)
    strip_bdc_by_cid: dict[str, set[int]] = defaultdict(set)
    for sp in ps.spans:
        if sp["actual_text"] is not None and sp["glyphs"]:
            if all(gk in drop_glyphs for gk in sp["glyphs"]):
                cid, bdc_op_idx = sp["key"]
                strip_bdc_by_cid[cid].add(bdc_op_idx)

    # Compute set of all content_ids that have direct edits or affected descendants
    affected_cids: set[str] = set()
    for cid in set(drop_g_by_cid.keys()) | set(drop_o_by_cid.keys()) | set(strip_bdc_by_cid.keys()):
        cur = cid
        while True:
            affected_cids.add(cur)
            if "/" not in cur:
                break
            cur = cur.rsplit("/", 1)[0]

    if "page" not in affected_cids:
        return

    def _rewrite_text_items(
        cid: str,
        op_idx: int,
        items: list[Any],
        ops: list[tuple[Any, bytes]],
    ) -> ArrayObject:
        item_drops = drop_g_by_cid[cid][op_idx]
        # Build list of glyphs per item_idx from ps.glyphs (in exact byte order)
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
                raw_bytes = _str_obj_to_bytes(item)
                dropped_bpos = item_drops.get(item_idx)
                pos = 0
                n = len(raw_bytes)
                while pos < n:
                    g = glyph_by_key.get((cid, op_idx, item_idx, pos))
                    if g is None:
                        # Unparsed trailing byte; keep verbatim
                        if has_pending and abs(pending_num) > 1e-4:
                            out_arr.append(_fmt_tj_num(pending_num))
                        pending_num = 0.0
                        has_pending = False
                        kept_buf.append(raw_bytes[pos])
                        pos += 1
                        continue
                    nb = g.nbytes
                    bslice = raw_bytes[pos : pos + nb]
                    if dropped_bpos is not None and pos in dropped_bpos:
                        if kept_buf:
                            _flush_buf()
                        pending_num += -g.advance
                        has_pending = True
                    else:
                        if has_pending and abs(pending_num) > 1e-4:
                            out_arr.append(_fmt_tj_num(pending_num))
                        pending_num = 0.0
                        has_pending = False
                        kept_buf.extend(bslice)
                    pos += nb

        _flush_buf()
        if has_pending and abs(pending_num) > 1e-4:
            if _needs_trailing_tm_preserve(ops, op_idx):
                out_arr.append(_fmt_tj_num(pending_num))
        return out_arr

    def _edit_stream(
        cid: str,
        owner_dict: DictionaryObject,
        stream_obj: Any,
        resources: DictionaryObject | None,
        is_page: bool,
    ) -> bool:
        """Edit stream_obj and any affected child Form XObjects. Returns True if stream_obj itself was modified."""
        if cid not in affected_cids or stream_obj is None:
            return False

        cs = ContentStream(stream_obj, writer)
        ops = cs.operations

        xobjs: DictionaryObject | None = None
        if isinstance(resources, DictionaryObject) and "/XObject" in resources:
            xo_obj = _resolve_obj(resources["/XObject"])
            if isinstance(xo_obj, DictionaryObject):
                xobjs = xo_obj

        do_name_counts: Counter[str] = Counter(
            str(operands[0]) for operands, op in ops if op == b"Do" and operands
        )

        cid_drop_g = drop_g_by_cid.get(cid, {})
        cid_drop_o = drop_o_by_cid.get(cid, set())
        cid_strip_bdc = strip_bdc_by_cid.get(cid, set())

        # Precompute path construction op indices to omit for dropped path paint ops
        omit_ops: set[int] = set()
        replace_with_n: set[int] = set()
        if cid_drop_o:
            cur_path_ops: list[int] = []
            has_clip = False
            for idx, (_operands, op) in enumerate(ops):
                if op in _PATH_CONSTRUCT_OPS:
                    cur_path_ops.append(idx)
                elif op in _CLIP_OPS:
                    has_clip = True
                elif op == b"n":
                    cur_path_ops.clear()
                    has_clip = False
                elif op in _PATH_PAINT_OPS:
                    if idx in cid_drop_o:
                        if has_clip:
                            replace_with_n.add(idx)
                        else:
                            omit_ops.update(cur_path_ops)
                            omit_ops.add(idx)
                    cur_path_ops.clear()
                    has_clip = False

        new_ops: list[tuple[Any, bytes]] = []
        stream_modified = False
        do_clone_idx = 0

        for op_idx, (operands, op) in enumerate(ops):
            if op_idx in omit_ops:
                stream_modified = True
                continue
            if op_idx in replace_with_n:
                new_ops.append(([], b"n"))
                stream_modified = True
                continue
            if op_idx in cid_drop_o and op in (b"Do", b"INLINE IMAGE", b"sh"):
                stream_modified = True
                continue

            if op_idx in cid_strip_bdc and op == b"BDC":
                new_operands = _strip_actual_text_from_bdc(operands, resources)
                new_ops.append((new_operands, op))
                stream_modified = True
                continue

            if op_idx in cid_drop_g:
                if op == b"Tj":
                    out_arr = _rewrite_text_items(cid, op_idx, [operands[0]], ops)
                    stream_modified = True
                    if len(out_arr) > 0:
                        new_ops.append(([out_arr], b"TJ"))
                    continue
                elif op == b"TJ":
                    out_arr = _rewrite_text_items(cid, op_idx, operands[0], ops)
                    stream_modified = True
                    if len(out_arr) > 0:
                        new_ops.append(([out_arr], b"TJ"))
                    continue
                elif op == b"'":
                    out_arr = _rewrite_text_items(cid, op_idx, [operands[0]], ops)
                    stream_modified = True
                    new_ops.append(([], b"T*"))
                    if len(out_arr) > 0:
                        new_ops.append(([out_arr], b"TJ"))
                    continue
                elif op == b'"':
                    out_arr = _rewrite_text_items(cid, op_idx, [operands[2]], ops)
                    stream_modified = True
                    new_ops.append(([operands[0]], b"Tw"))
                    new_ops.append(([operands[1]], b"Tc"))
                    new_ops.append(([], b"T*"))
                    if len(out_arr) > 0:
                        new_ops.append(([out_arr], b"TJ"))
                    continue

            if op == b"Do" and xobjs is not None and operands:
                child_cid = f"{cid}/{op_idx}"
                if child_cid in affected_cids:
                    xname = str(operands[0])
                    if xname in xobjs:
                        raw_ref = xobjs.raw_get(xname)
                        xo = _resolve_obj(raw_ref)
                        if isinstance(xo, StreamObject) and xo.get("/Subtype") == "/Form":
                            sub_res = _resolve_obj(xo.get("/Resources")) if "/Resources" in xo else resources
                            is_shared = (ps.forms.get(child_cid, 1) > 1) or (do_name_counts[xname] > 1)
                            if is_shared:
                                clone_s = DecodedStreamObject()
                                for k, v in xo.items():
                                    if k in ("/Length", "/Filter", "/DecodeParms"):
                                        continue
                                    clone_s[k] = v
                                clone_s.set_data(xo.get_data())
                                child_res = (
                                    _resolve_obj(clone_s.get("/Resources"))
                                    if "/Resources" in clone_s
                                    else resources
                                )
                                _edit_stream(child_cid, clone_s, clone_s, child_res, is_page=False)
                                clone_ref = writer._add_object(clone_s)
                                priv_xo = _ensure_private_xobject_dict(owner_dict)
                                if do_name_counts[xname] > 1:
                                    do_clone_idx += 1
                                    new_name_str = f"{xname}_rx{do_clone_idx}"
                                    while new_name_str in priv_xo:
                                        do_clone_idx += 1
                                        new_name_str = f"{xname}_rx{do_clone_idx}"
                                    new_name = NameObject(new_name_str)
                                    priv_xo[new_name] = clone_ref
                                    xobjs = priv_xo
                                    new_ops.append(([new_name], b"Do"))
                                    stream_modified = True
                                else:
                                    priv_xo[NameObject(xname)] = clone_ref
                                    xobjs = priv_xo
                                    new_ops.append((operands, op))
                                _invalidate_form_cache(writer)
                                continue
                            else:
                                _edit_stream(child_cid, xo, xo, sub_res, is_page=False)
                                new_ops.append((operands, op))
                                continue

            new_ops.append((operands, op))

        if stream_modified:
            cs.operations = new_ops
            data = cs.get_data()
            if is_page:
                # Always allocate a brand-new stream so any other page sharing the old /Contents stays untouched
                new_stream = DecodedStreamObject()
                new_stream.set_data(data)
                compressed = new_stream.flate_encode()
                owner_dict[NameObject("/Contents")] = writer._add_object(compressed)
            else:
                if "/DecodeParms" in stream_obj:
                    del stream_obj["/DecodeParms"]
                comp_bytes = zlib.compress(data)
                stream_obj._data = comp_bytes
                stream_obj[NameObject("/Filter")] = NameObject("/FlateDecode")
                stream_obj[NameObject("/Length")] = NumberObject(len(comp_bytes))

        return stream_modified

    page = writer.pages[page_index]
    page_res = _resolve_obj(page.get("/Resources")) if "/Resources" in page else None
    _edit_stream("page", page, page.get("/Contents"), page_res, is_page=True)

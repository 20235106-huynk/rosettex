"""D14: correct inline-math errors from the agent's translation (spec §6.6). Pure functions; writes nothing."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any

from rosettex.pdf.classify import (
    CMAP_FLAG_ITALIC,
    CMAP_FLAG_MATH_FONT,
    CMAP_FLAG_NON_BODY_FONT,
    _coalesce_ranges,
    build_math_ph,
)

_WORD4 = re.compile(r"[^\W\d_]{4,}")
_MATH_CHAR = re.compile(r"[=<>≤≥∑∫√∈±×∂∇^_{}\\]")
SUSPECT_RE: re.Pattern[str] = _WORD4

_MARK_RE = re.compile(r"⟦m(\d+)\?⟧")
_PH_ANY_RE = re.compile(r"⟦([a-z])(\d+)⟧")
_PH_L_RE = re.compile(r"⟦l\d+⟧")
_PH_M_RE = re.compile(r"⟦(m\d+)⟧")
_TOKEN_RE = re.compile(r"⟦m\d+⟧|[^\s⟦⟧]+")
_PLAIN_NUM_RE = re.compile(r"^[\d.,\s]+$")
_LETTER_WORD_RE = re.compile(r"^[^\W\d_]+$")

_LEAD_PUNCT = frozenset(".,;:!?'\"“”‘’([{")
_TRAIL_PUNCT = frozenset(".,;:!?'\"“”‘’)]}")
_TRIM_LEAD = frozenset(".,;:!?'\"“”‘’")
_TRIM_TRAIL = frozenset(".,;:?'\"“”‘’")
_MATH_EXTRA = frozenset("%°′″")


def _is_non_prose_math_char(ch: str) -> bool:
    cp = ord(ch)
    if (
        0x0370 <= cp <= 0x03FF  # Greek and Coptic
        or 0x1F00 <= cp <= 0x1FFF  # Greek Extended
        or 0x2100 <= cp <= 0x214F  # Letterlike Symbols
        or 0x2190 <= cp <= 0x21FF  # Arrows
        or 0x27F0 <= cp <= 0x27FF  # Supplemental Arrows-A
        or 0x2900 <= cp <= 0x297F  # Supplemental Arrows-B
        or 0x1D400 <= cp <= 0x1D7FF  # Mathematical Alphanumeric Symbols
        or cp == 0x2217  # Asterisk operator ∗
    ):
        return True
    return unicodedata.category(ch) in ("Sm", "So")


def _is_prose_letter(ch: str) -> bool:
    if not ch.isalpha():
        return False
    cp = ord(ch)
    if (
        0x0370 <= cp <= 0x03FF
        or 0x1F00 <= cp <= 0x1FFF
        or 0x1D400 <= cp <= 0x1D7FF
    ):
        return False
    return True


def is_suspect(raw: str, pw: int | None = None) -> bool:
    """An m placeholder whose preview holds a >= 4-letter prose word and no math/Greek/symbol char (shown as ⟦mN?⟧).

    ``pw`` is the glyph evidence stored by classify (``ph["pw"]``: longest plain body-font upright word in the
    placeholder); when given, the word must really be set in plain body-font glyphs (italic/math-font/sub-superscript
    letters such as ``ETmiss``, ``mmin`` are math, not prose).
    """
    if pw is not None and pw < 4:
        return False
    if _MATH_CHAR.search(raw) or any(_is_non_prose_math_char(ch) for ch in raw):
        return False
    run = 0
    for ch in raw:
        if _is_prose_letter(ch):
            run += 1
            if run >= 4:
                return True
        else:
            run = 0
    return False


def norm_marks(translation: str) -> str:
    """Normalise flagged placeholder marks: ``⟦m3?⟧`` -> ``⟦m3⟧``."""
    return _MARK_RE.sub(r"⟦m\1⟧", translation)


@dataclass
class Wrap:
    src: tuple[int, int]
    dst: tuple[int, int]
    merged: list[str]
    code: str


@dataclass
class Recon:
    text: str
    translation: str
    wraps: list[Wrap]
    unwraps: list[str]


def _nfkc_with_map(s: str) -> tuple[str, list[int], list[int]]:
    """Return ``(norm_s, orig_starts, orig_ends)`` applying NFKC and mapping U+2212 minus to ASCII ``-``."""
    out: list[str] = []
    starts: list[int] = []
    ends: list[int] = []
    for i, ch in enumerate(s):
        norm = unicodedata.normalize("NFKC", ch).replace("\u2212", "-")
        for c in norm:
            out.append(c)
            starts.append(i)
            ends.append(i + 1)
    return "".join(out), starts, ends


def _is_word_char(ch: str) -> bool:
    return ch.isalnum() or ch == "_"


def _is_math_symbol(ch: str) -> bool:
    return unicodedata.category(ch) == "Sm" or ch in _MATH_EXTRA


def _tokenize_chunk(norm_src: str, chunk_start: int, chunk_end: int) -> list[tuple[int, int, bool]]:
    """Tokenize an ``l``-placeholder-free slice of ``norm_src`` into ``(start, end, is_ph)`` tuples."""
    tokens: list[tuple[int, int, bool]] = []
    for m in _TOKEN_RE.finditer(norm_src, chunk_start, chunk_end):
        a, b = m.start(), m.end()
        tok = m.group(0)
        if tok.startswith("⟦m") and tok.endswith("⟧"):
            tokens.append((a, b, True))
            continue
        # Peel leading punctuation/open-brackets and trailing punctuation/close-brackets into own tokens
        lead: list[tuple[int, int, bool]] = []
        while a < b and norm_src[a] in _LEAD_PUNCT:
            lead.append((a, a + 1, False))
            a += 1
        trail: list[tuple[int, int, bool]] = []
        while b > a and norm_src[b - 1] in _TRAIL_PUNCT:
            trail.append((b - 1, b, False))
            b -= 1
        tokens.extend(lead)
        if a < b:
            tokens.append((a, b, False))
        if trail:
            trail.reverse()
            tokens.extend(trail)
    return tokens


def _trim_span(s: str, a: int, b: int) -> tuple[int, int]:
    """Trim outer whitespace, sentence punctuation, and unmatched outer brackets from ``s[a:b]``."""
    while a < b:
        orig_a, orig_b = a, b
        while a < b and s[a].isspace():
            a += 1
        while b > a and s[b - 1].isspace():
            b -= 1
        while a < b and s[a] in _TRIM_LEAD:
            a += 1
        while b > a and (s[b - 1] in _TRIM_TRAIL or (s[b - 1] == "!" and (b - 1 == a or not s[b - 2].isalnum()))):
            b -= 1
        if a < b:
            sub = s[a:b]
            for op, cl in (("(", ")"), ("[", "]"), ("{", "}")):
                if a < b and s[a] == cl:
                    a += 1
                    sub = s[a:b]
                if a < b and s[b - 1] == op:
                    b -= 1
                    sub = s[a:b]
                if a < b and s[a] == op and sub.count(op) > sub.count(cl):
                    a += 1
                    sub = s[a:b]
                if a < b and s[b - 1] == cl and sub.count(cl) > sub.count(op):
                    b -= 1
                    sub = s[a:b]
        if a == orig_a and b == orig_b:
            break
    return a, b


def _parse_cmap_runs(seg: dict[str, Any]) -> list[tuple[int, int, int, int, float, list[tuple[int, int, int, int, float, float, float, float, int]]]]:
    """Parse ``seg['cmap']`` into ``(t0, t1, page, line_id, baseline, glyphs)`` tuples."""
    out = []
    for run in seg.get("cmap") or []:
        if not isinstance(run, list) or len(run) < 8:
            continue
        t0, t1, pg, _ci, _ce, lid, base, gstr = run[0], run[1], run[2], run[3], run[4], run[5], float(run[6]), run[7]
        glyphs: list[tuple[int, int, int, int, float, float, float, float, int]] = []
        if gstr:
            for item in gstr.split(";"):
                parts = item.split(",")
                if len(parts) != 9:
                    continue
                glyphs.append(
                    (
                        t0 + int(parts[0]),
                        t0 + int(parts[1]),
                        int(parts[2]),
                        int(parts[3]),
                        float(parts[4]),
                        float(parts[5]),
                        float(parts[6]),
                        float(parts[7]),
                        int(parts[8]),
                    )
                )
        out.append((int(t0), int(t1), int(pg), int(lid), base, glyphs))
    return out


def _has_glyph_evidence(
    cmap_runs: list[tuple[int, int, int, int, float, list[tuple[int, int, int, int, float, float, float, float, int]]]],
    orig_a: int,
    orig_b: int,
    blk_italic: bool,
) -> bool:
    """Return True if any glyph in ``[orig_a, orig_b)`` has italic (on upright block), math-font, or non-body-font evidence."""
    for t0, t1, _pg, _lid, _base, glyphs in cmap_runs:
        if t1 <= orig_a or t0 >= orig_b:
            continue
        for gt0, gt1, _ci, _ce, _x0, _y0, _x1, _y1, flags in glyphs:
            if gt1 <= gt0:
                continue
            if gt0 < orig_b and gt1 > orig_a:
                if flags & (CMAP_FLAG_MATH_FONT | CMAP_FLAG_NON_BODY_FONT):
                    return True
                if (flags & CMAP_FLAG_ITALIC) and not blk_italic:
                    return True
    return False


def _passes_wrap_filter(
    sub: str,
    orig_sub: str,
    orig_a: int,
    orig_b: int,
    kept_terms: set[str],
    kept_terms_lower: set[str],
    cmap_runs: list[Any],
    blk_italic: bool,
) -> bool:
    """Apply spec §6.6 wrap exclusion rules to a trimmed candidate span."""
    has_m_ph = bool(_PH_M_RE.search(sub))
    sub_no_ph = _PH_M_RE.sub("", sub)
    if not sub_no_ph.strip():
        return False

    # Punctuation-only outside placeholders (also rejects '⟦m1⟧; ⟦m2⟧' or '⟦m1⟧ (')
    if not any(ch.isalnum() or _is_math_symbol(ch) for ch in sub_no_ph):
        return False

    # Plain numbers: ^[\d.,\s]+$ after NFKC
    if not has_m_ph and _PLAIN_NUM_RE.match(sub):
        return False

    # Kept terms
    if (
        sub in kept_terms
        or orig_sub in kept_terms
        or sub.lower() in kept_terms_lower
        or orig_sub.lower() in kept_terms_lower
    ):
        return False

    # Word-only checks (when no m placeholder, no digits, no math symbols)
    if not has_m_ph and not any(ch.isdigit() or _is_math_symbol(ch) for ch in sub):
        # Split on whitespace and non-letter punctuation (e.g. hyphens, slashes, parens)
        letter_words = re.findall(r"[^\W\d_]+", sub)
        non_letter_non_space = [ch for ch in sub if not ch.isspace() and not ch.isalpha()]
        # Pure letter-only words (possibly separated by spaces or simple prose punctuation like -, ', .)
        if letter_words and all(ch in "-'’.,/()[]{}:;\"“”‘’" for ch in non_letter_non_space):
            if len(letter_words) >= 2:
                return False
            # Single letter-only word: require glyph evidence
            if len(letter_words) == 1:
                return _has_glyph_evidence(cmap_runs, orig_a, orig_b, blk_italic)

    return True


def _find_match_in_tr(
    needle: str,
    norm_tr: str,
    tr_starts: list[int],
    tr_ends: list[int],
    used_dst: list[tuple[int, int]],
    ph_ranges_tr: list[tuple[int, int]],
) -> tuple[int, int] | None:
    """Find the first unused boundary-respecting occurrence of ``needle`` in ``norm_tr``; return orig dst ``(a, b)``."""
    nlen = len(needle)
    if nlen == 0:
        return None
    need_left_b = _is_word_char(needle[0])
    need_right_b = _is_word_char(needle[-1])
    tr_len = len(norm_tr)
    pos = 0
    while True:
        idx = norm_tr.find(needle, pos)
        if idx < 0:
            return None
        end = idx + nlen
        pos = idx + 1
        if need_left_b and idx > 0 and _is_word_char(norm_tr[idx - 1]):
            continue
        if need_right_b and end < tr_len and _is_word_char(norm_tr[end]):
            continue
        # Never start or end strictly inside a placeholder token in norm_tr
        inside_ph = False
        for pa, pb in ph_ranges_tr:
            if (pa < idx < pb) or (pa < end < pb):
                inside_ph = True
                break
        if inside_ph:
            continue
        # Never split an NFKC-expanded char in translation
        if idx > 0 and tr_starts[idx] == tr_starts[idx - 1]:
            continue
        if end < tr_len and tr_starts[end] == tr_starts[end - 1]:
            continue
        dst_a = tr_starts[idx]
        dst_b = tr_ends[end - 1]
        if any(not (dst_b <= ua or dst_a >= ub) for ua, ub in used_dst):
            continue
        return dst_a, dst_b


def _has_any_match(needle: str, norm_tr: str, ph_ranges_tr: list[tuple[int, int]]) -> bool:
    """Fast check whether ``needle`` appears at a valid boundary anywhere in ``norm_tr``."""
    nlen = len(needle)
    if nlen == 0:
        return False
    need_left_b = _is_word_char(needle[0])
    need_right_b = _is_word_char(needle[-1])
    tr_len = len(norm_tr)
    pos = 0
    while True:
        idx = norm_tr.find(needle, pos)
        if idx < 0:
            return False
        end = idx + nlen
        pos = idx + 1
        if need_left_b and idx > 0 and _is_word_char(norm_tr[idx - 1]):
            continue
        if need_right_b and end < tr_len and _is_word_char(norm_tr[end]):
            continue
        if any((pa < idx < pb) or (pa < end < pb) for pa, pb in ph_ranges_tr):
            continue
        return True


def reconcile(seg: dict[str, Any], translation: str, kept_terms: set[str], layout: dict[str, Any]) -> Recon:
    """Reconcile one layout.json segment against its translation (spec §6.6). Pure: writes nothing."""
    src_text = seg.get("text") or ""
    tr = norm_marks(translation)
    ph_map = seg.get("ph") or {}

    # 1. Unwraps: flagged (is_suspect) m placeholders present in src_text but absent from tr
    tr_phs = {f"{m.group(1)}{m.group(2)}" for m in _PH_ANY_RE.finditer(tr)}
    unwraps: list[str] = []
    for code, info in ph_map.items():
        if not code.startswith("m") or not isinstance(info, dict):
            continue
        if is_suspect(info.get("raw", ""), info.get("pw")) and f"⟦{code}⟧" in src_text and code not in tr_phs:
            unwraps.append(code)

    # 2. Wraps: maximal spans of src_text (never crossing ⟦lN⟧) occurring verbatim in tr (NFKC + minus)
    norm_src, src_starts, src_ends = _nfkc_with_map(src_text)
    norm_tr, tr_starts, tr_ends = _nfkc_with_map(tr)
    ph_ranges_tr = [(m.start(), m.end()) for m in _PH_ANY_RE.finditer(norm_tr)]

    blocks_by_id = {b["id"]: b for b in (layout.get("blocks") or [])}
    seg_blks = [blocks_by_id[bid] for bid in (seg.get("blocks") or []) if bid in blocks_by_id]
    blk_italic = bool(seg_blks) and all(bool(b.get("italic", False)) for b in seg_blks)
    cmap_runs = _parse_cmap_runs(seg)
    kept_terms_lower = {t.lower() for t in kept_terms}

    # Split norm_src around ⟦lN⟧ placeholders
    l_bounds = [(m.start(), m.end()) for m in _PH_L_RE.finditer(norm_src)]
    chunks: list[tuple[int, int]] = []
    cur = 0
    for la, lb in l_bounds:
        if cur < la:
            chunks.append((cur, la))
        cur = lb
    if cur < len(norm_src):
        chunks.append((cur, len(norm_src)))

    # Collect maximal candidate spans (in norm_src coordinates)
    candidates: list[tuple[int, int, int, int]] = []  # (na, nb, orig_a, orig_b)
    seen_spans: set[tuple[int, int]] = set()
    for ca, cb in chunks:
        toks = _tokenize_chunk(norm_src, ca, cb)
        nt = len(toks)
        i = 0
        while i < nt:
            ta, tb, _ = toks[i]
            if not _has_any_match(norm_src[ta:tb], norm_tr, ph_ranges_tr):
                i += 1
                continue
            # Extend greedily as far as norm_src[ta:toks[j].end] matches in norm_tr
            j = i + 1
            while j < nt:
                cand_end = toks[j][1]
                if _has_any_match(norm_src[ta:cand_end], norm_tr, ph_ranges_tr):
                    j += 1
                else:
                    break
            na, nb = _trim_span(norm_src, ta, toks[j - 1][1])
            if na < nb and (na, nb) not in seen_spans:
                # Reject if na/nb splits an NFKC-expanded char in src_text
                if not (
                    (na > 0 and src_starts[na] == src_starts[na - 1])
                    or (nb < len(src_starts) and src_starts[nb] == src_starts[nb - 1])
                ):
                    orig_a = src_starts[na]
                    orig_b = src_ends[nb - 1]
                    sub = norm_src[na:nb]
                    orig_sub = src_text[orig_a:orig_b]
                    if _passes_wrap_filter(
                        sub,
                        orig_sub,
                        orig_a,
                        orig_b,
                        kept_terms,
                        kept_terms_lower,
                        cmap_runs,
                        blk_italic,
                    ):
                        seen_spans.add((na, nb))
                        candidates.append((na, nb, orig_a, orig_b))
            i = j

    # Longest first, each src & translation position used at most once
    candidates.sort(key=lambda x: (-(x[1] - x[0]), x[2]))
    used_src: list[tuple[int, int]] = []
    used_dst: list[tuple[int, int]] = []
    raw_wraps: list[tuple[tuple[int, int], tuple[int, int], list[str]]] = []

    for na, nb, orig_a, orig_b in candidates:
        if any(not (orig_b <= ua or orig_a >= ub) for ua, ub in used_src):
            continue
        dst_span = _find_match_in_tr(norm_src[na:nb], norm_tr, tr_starts, tr_ends, used_dst, ph_ranges_tr)
        if dst_span is None:
            continue
        used_src.append((orig_a, orig_b))
        used_dst.append(dst_span)
        merged = [m.group(1) for m in _PH_M_RE.finditer(src_text[orig_a:orig_b])]
        raw_wraps.append(((orig_a, orig_b), dst_span, merged))

    # Assign next free m numbers in source reading order
    existing_m = [int(k[1:]) for k in ph_map if k.startswith("m") and k[1:].isdigit()]
    next_m = max(existing_m, default=0) + 1
    raw_wraps.sort(key=lambda item: item[0][0])
    wraps: list[Wrap] = []
    for src_sp, dst_sp, merged in raw_wraps:
        wraps.append(Wrap(src=src_sp, dst=dst_sp, merged=merged, code=f"m{next_m}"))
        next_m += 1

    # Build reconciled source text: replace wraps with ⟦code⟧ and unwraps with ph[code]["raw"]
    src_edits: list[tuple[int, int, str]] = [(w.src[0], w.src[1], f"⟦{w.code}⟧") for w in wraps]
    unwrap_set = set(unwraps)
    if unwrap_set:
        for m in _PH_M_RE.finditer(src_text):
            code = m.group(1)
            if code in unwrap_set:
                raw_txt = (ph_map.get(code) or {}).get("raw", "")
                src_edits.append((m.start(), m.end(), raw_txt))
    src_edits.sort(key=lambda e: e[0], reverse=True)
    recon_text = src_text
    for a, b, rep in src_edits:
        recon_text = recon_text[:a] + rep + recon_text[b:]

    # Build reconciled translation: replace each wrap's dst span with ⟦code⟧
    dst_edits = sorted(((w.dst[0], w.dst[1], f"⟦{w.code}⟧") for w in wraps), key=lambda e: e[0], reverse=True)
    recon_tr = tr
    for a, b, rep in dst_edits:
        recon_tr = recon_tr[:a] + rep + recon_tr[b:]

    return Recon(text=recon_text, translation=recon_tr, wraps=wraps, unwraps=unwraps)


def _map_wrap_to_fragments(
    w: Wrap,
    orig_text: str,
    orig_ph: dict[str, dict[str, Any]],
    cmap_runs: list[tuple[int, int, int, int, float, list[tuple[int, int, int, int, float, float, float, float, int]]]],
) -> list[dict[str, Any]] | None:
    """Map ``w.src`` to one or more line-fragment math placeholder specs, or return None on R1 failure."""
    sa_all, sb_all = w.src
    # Split [sa_all, sb_all) into non-placeholder sub-spans and merged m placeholders
    sub_spans: list[tuple[int, int]] = []
    merged_codes: list[str] = []
    cur = sa_all
    for m in _PH_M_RE.finditer(orig_text, sa_all, sb_all):
        if cur < m.start():
            sub_spans.append((cur, m.start()))
        merged_codes.append(m.group(1))
        cur = m.end()
    if cur < sb_all:
        sub_spans.append((cur, sb_all))

    # Fragment dict keyed by (page, line_id) -> {page, baseline, chars, bboxes, min_t0, min_ci, raw_parts, link}
    frags: dict[tuple[int, int], dict[str, Any]] = {}

    for sa, sb in sub_spans:
        # Track coverage of every non-whitespace char in [sa, sb)
        needed = {pos for pos in range(sa, sb) if not orig_text[pos].isspace()}
        covered: set[int] = set()
        for t0, t1, pg, lid, base, glyphs in cmap_runs:
            if t1 < sa or t0 > sb:
                continue
            for gt0, gt1, ci, ce, x0, y0, x1, y1, _flags in glyphs:
                if gt1 == gt0:
                    # Zero-width detached de-hyphenation glyph: include iff strictly inside (sa, sb)
                    if sa < gt0 < sb:
                        f = frags.setdefault(
                            (pg, lid),
                            {
                                "page": pg,
                                "baseline": base,
                                "chars": [],
                                "bboxes": [],
                                "min_t0": gt0,
                                "min_ci": ci,
                                "t_spans": [],
                                "link": None,
                            },
                        )
                        f["chars"].append((ci, ce))
                        f["bboxes"].append([x0, y0, x1, y1])
                        f["min_t0"] = min(f["min_t0"], gt0)
                        f["min_ci"] = min(f["min_ci"], ci)
                    continue
                if gt1 <= sa or gt0 >= sb:
                    continue
                # Whole-glyph R1 check: glyph must not be partially split by [sa, sb)
                if gt0 < sa or gt1 > sb:
                    return None
                f = frags.setdefault(
                    (pg, lid),
                    {
                        "page": pg,
                        "baseline": base,
                        "chars": [],
                        "bboxes": [],
                        "min_t0": gt0,
                        "min_ci": ci,
                        "t_spans": [],
                        "link": None,
                    },
                )
                f["chars"].append((ci, ce))
                f["bboxes"].append([x0, y0, x1, y1])
                f["min_t0"] = min(f["min_t0"], gt0)
                f["min_ci"] = min(f["min_ci"], ci)
                f["t_spans"].append((gt0, gt1))
                for p in range(gt0, gt1):
                    covered.add(p)
        if not needed.issubset(covered):
            return None

    # Attach merged m placeholders to matching line fragment (or create one)
    synth_lid = -1
    for code in merged_codes:
        old = orig_ph.get(code)
        if not isinstance(old, dict) or not old.get("chars") or not old.get("bbox"):
            return None
        opg = int(old["page"])
        obase = float(old.get("baseline", old["bbox"][1]))
        best_key: tuple[int, int] | None = None
        best_dy = 3.01
        for key, f in frags.items():
            if f["page"] == opg:
                dy = abs(f["baseline"] - obase)
                if dy < best_dy:
                    best_dy = dy
                    best_key = key
        if best_key is None:
            best_key = (opg, synth_lid)
            synth_lid -= 1
            frags[best_key] = {
                "page": opg,
                "baseline": obase,
                "chars": [],
                "bboxes": [],
                "min_t0": sa_all,
                "min_ci": old["chars"][0][0],
                "t_spans": [],
                "link": None,
            }
        f = frags[best_key]
        for s_i, e_i in old["chars"]:
            f["chars"].append((int(s_i), int(e_i)))
        f["bboxes"].append(list(old["bbox"]))
        f["min_ci"] = min(f["min_ci"], int(old["chars"][0][0]))
        if old.get("link") is not None and f["link"] is None:
            f["link"] = old["link"]

    if not frags:
        return None

    # Order line fragments by reading order (page, min_t0, min_ci)
    ordered = sorted(frags.values(), key=lambda f: (f["page"], f["min_t0"], f["min_ci"]))

    # Build raw preview per fragment
    full_raw = _PH_M_RE.sub(lambda m: (orig_ph.get(m.group(1)) or {}).get("raw", ""), orig_text[sa_all:sb_all])
    out: list[dict[str, Any]] = []
    for idx, f in enumerate(ordered):
        if len(ordered) == 1:
            frag_raw = full_raw
        else:
            if f["t_spans"]:
                lo = min(a for a, _ in f["t_spans"])
                hi = max(b for _, b in f["t_spans"])
                frag_raw = _PH_M_RE.sub(
                    lambda m: (orig_ph.get(m.group(1)) or {}).get("raw", ""), orig_text[lo:hi]
                ).strip() or full_raw
            else:
                frag_raw = full_raw if idx == 0 else ""
        out.append(
            build_math_ph(
                frag_raw,
                f["page"],
                _coalesce_ranges(f["chars"]),
                f["bboxes"],
                f["baseline"],
                f["link"],
            )
        )
    return out


def apply(layout: dict[str, Any], recons: dict[str, Recon]) -> list[str]:
    """Mutate ``layout['segments'][*]['text'/'ph']`` from ``recons``; return R1 warnings for unmappable wraps."""
    warnings: list[str] = []
    for seg in layout.get("segments") or []:
        sid = seg["id"]
        recon = recons.get(sid)
        if recon is None or (not recon.wraps and not recon.unwraps):
            continue

        orig_text = seg.get("text") or ""
        ph_map = seg.setdefault("ph", {})
        orig_ph = dict(ph_map)
        cmap_runs = _parse_cmap_runs(seg)

        existing_m = [int(k[1:]) for k in ph_map if k.startswith("m") and k[1:].isdigit()]
        wrap_m = [int(w.code[1:]) for w in recon.wraps if w.code.startswith("m") and w.code[1:].isdigit()]
        next_m = max(existing_m + wrap_m, default=0) + 1

        # Apply unwraps: remove ph entries
        for code in recon.unwraps:
            ph_map.pop(code, None)

        # Apply wraps: build ph entries per line fragment, or revert with R1 warning
        accepted_wraps: list[Wrap] = []
        src_edits: list[tuple[int, int, str]] = []
        dst_edits: list[tuple[int, int, str]] = []

        for w in recon.wraps:
            frags = _map_wrap_to_fragments(w, orig_text, orig_ph, cmap_runs)
            if not frags:
                warnings.append(
                    f"{sid}: D14 wrap {orig_text[w.src[0]:w.src[1]]!r} cannot map to whole glyphs; kept as text"
                )
                continue
            codes: list[str] = []
            for fi, frag_ph in enumerate(frags):
                if fi == 0:
                    code = w.code
                else:
                    code = f"m{next_m}"
                    next_m += 1
                ph_map[code] = frag_ph
                codes.append(code)
            for old_code in w.merged:
                if old_code not in codes:
                    ph_map.pop(old_code, None)
            rep_toks = "".join(f"⟦{c}⟧" for c in codes)
            src_edits.append((w.src[0], w.src[1], rep_toks))
            dst_edits.append((w.dst[0], w.dst[1], rep_toks))
            accepted_wraps.append(w)

        # Rebuild seg["text"], recon.text, recon.translation consistently
        unwrap_set = set(recon.unwraps)
        if unwrap_set:
            for m in _PH_M_RE.finditer(orig_text):
                code = m.group(1)
                if code in unwrap_set:
                    src_edits.append((m.start(), m.end(), (orig_ph.get(code) or {}).get("raw", "")))
        src_edits.sort(key=lambda e: e[0], reverse=True)
        new_text = orig_text
        for a, b, rep in src_edits:
            new_text = new_text[:a] + rep + new_text[b:]

        # Rebuild recon.translation only if any wrap reverted or split into multiple fragments
        if len(accepted_wraps) != len(recon.wraps) or any(len(rep) > len(f"⟦{w.code}⟧") for (_, _, rep), w in zip(dst_edits, accepted_wraps)):
            # Recover pre-wrap translation by reverting recon.wraps on recon.translation, or re-applying accepted dst_edits
            # Since recon.translation replaced each w.dst with ⟦w.code⟧, reconstruct cleanly from recon.translation:
            # Map each w.code -> new token string (or original slice if reverted)
            rep_by_code = {w.code: rep for (_, _, rep), w in zip(dst_edits, accepted_wraps)}
            reverted_by_code = {
                w.code: orig_text[w.src[0] : w.src[1]]
                for w in recon.wraps
                if w.code not in rep_by_code
            }
            new_tr = recon.translation
            for code, rep in rep_by_code.items():
                tok = f"⟦{code}⟧"
                if rep != tok:
                    new_tr = new_tr.replace(tok, rep, 1)
            for code, orig_slice in reverted_by_code.items():
                new_tr = new_tr.replace(f"⟦{code}⟧", orig_slice, 1)
            recon.translation = new_tr

        seg["text"] = new_text
        recon.text = new_text
        recon.wraps = accepted_wraps

    return warnings

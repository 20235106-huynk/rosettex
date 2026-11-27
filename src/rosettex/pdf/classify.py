"""Block classification, inline placeholders, R3 box geometry, and segment assembly."""

from __future__ import annotations

import re
import statistics as st
import unicodedata
from collections import Counter
from typing import Any

from rosettex.langdata import load_lang
from rosettex.pdf.extract import normalise
from rosettex.segment import Placeholder, Segment

TRANSLATED = {"title", "heading", "paragraph", "caption", "footnote"}
KEPT = {
    "front_matter",
    "header",
    "footer",
    "refs",
    "toc",
    "table",
    "figure",
    "display_math",
    "code",
}

DEFAULT_CONF_THRESHOLD = 0.55

_DIGITS_RE = re.compile(r"\d+")
_PAGE_NUM_RE = re.compile(r"^(?:\d{1,4}|[ivxlcdm]{1,6}|[-–—]\s*\d{1,4}\s*[-–—])$", re.I)
_CAPTION_RE = re.compile(
    r"^\s*(?:Supplementary\s+)?(?:Figure|Fig\.?|Table|Tab\.?)\s*[A-Z]?\d+[a-z]?"
    r"(?:\s*[:.\-–—]|\s+[A-Z]|\s*$)",
    re.I,
)
_TABLE_CAP_RE = re.compile(
    r"^\s*(?:Supplementary\s+)?(?:Table|Tab\.?)\s*[A-Z]?\d+[a-z]?(?:\s*[:.\-–—]|\s+[A-Z]|\s*$)",
    re.I,
)
_ALGO_RE = re.compile(r"^\s*(?:Algorithm|Listing|Procedure)\s+\d+\b", re.I)
_ALGO_LINE_RE = re.compile(
    r"^\s*(?:\d+\s*:\s*|Require:|Ensure:|Input:|Output:|for\s+|while\s+|if\s+|else\b|"
    r"return\b|end\s+(?:for|while|if))",
    re.I,
)
_REF_HEAD_RE = re.compile(r"^\s*(?:\d+\.?\s+)?(?:references|bibliography)\s*$", re.I)
_TOC_HEAD_RE = re.compile(r"^\s*(?:table\s+of\s+contents|contents)\s*$", re.I)
_TOC_LINE_RE = re.compile(r"(?:\.\s*){4,}\s*\d{1,4}\s*$")
_TOC_NUM_LINE_RE = re.compile(r"^\s*(?:\d+(?:\.\d+)*\.?|[A-Z]\.)\s+\S.{2,90}\s+\d{1,4}\s*$")
_APPENDIX_HEAD_RE = re.compile(
    r"^\s*(?:appendix|appendices|supplementary|supplemental|acknowledg)", re.I
)
_ABSTRACT_HEAD_RE = re.compile(r"^\s*abstract\s*[:.\-–—]?\s*$", re.I)
_ABSTRACT_INLINE_RE = re.compile(r"^\s*abstract\s*(?:[.:—–-]\s*|\s+[A-Z])", re.I)
_BULLET_START_RE = re.compile(r"^\s*(?:[•◦▪▫●○■□▸►‣⁃❖➢➤]\s*\S|[–—−\-*∗]\s+\S)")
_NUM_HEAD_RE = re.compile(
    r"^\s*(?:(\d+(?:\.\d+)*)\.?|([IVXLCDM]+)\.|([A-Z](?:\.\d+)*)\.)\s+([A-Z][a-zA-Z0-9\-: /]{1,85})$"
)
_AUTHOR_INIT_RE = re.compile(r"^\s*[A-Z]\.\s*[A-Z][a-z]+,")
_NON_HEAD_RE = re.compile(
    r"^\s*(?:Theorem|Lemma|Proposition|Corollary|Definition|Remark|Example|Proof|Claim|"
    r"Assumption|Conjecture|Algorithm|Figure|Fig\.|Table|Tab\.|Equation|Eq\.|Step)\b",
    re.I,
)
_FRONT_HINT_RE = re.compile(
    r"(?:@|https?://|doi\.org|10\.\d{4,}/|\b(?:University|Institute|Department|Laboratory|"
    r"College|Research|Google|Meta|OpenAI|Microsoft|Facebook|DeepMind|Stanford|MIT|Berkeley|"
    r"CMU|ETH|Index\s+Terms|Key\s*words?|CCS\s+Concepts|Preprint|Published\s+as|Proceedings\s+of|"
    r"arXiv:|Equal\s+contribution|Corresponding\s+author|For\s+correspondence|Work\s+done|"
    r"Permission\s+to\s+make\s+digital|Copyright\s+held|Conference\s+on\s+Neural\s+Information|"
    r"E-mail|Email:|OPEN\s+ACCESS|Citation:|Editor:|Received:|Accepted:|Published:|"
    r"Copyright:|Data\s+Availability|Funding:|Competing\s+interests?|Check\s+for\s+updates|"
    r"Creative\s+Commons|CC\s+BY|Reviewed\s+preprint|ORCID|ISSN|ISBN)\b)",
    re.I,
)
_STRONG_FRONT_RE = re.compile(
    r"(?:@|\b(?:University|Institute|Department|Laboratory|College|School\s+of|Center\s+for|"
    r"Centre\s+for|Hospital|Faculty\s+of|Permission\s+to\s+make\s+digital|Copyright\s+held|"
    r"Proceedings\s+of|Citation:|Received:|Accepted:|Published:|Competing\s+interests?|"
    r"Creative\s+Commons|Equal\s+contribution|Corresponding\s+author|For\s+correspondence)\b)",
    re.I,
)
_KEYWORDS_START_RE = re.compile(
    r"^\s*(?:Index\s+Terms|Key\s*words?|CCS\s+Concepts|MSC|Subject\s+areas?|PACS)\s*[:.\-–—]?",
    re.I,
)
_VENUE_BADGE_RE = re.compile(
    r"^\s*(?:RESEARCH\s+ARTICLE|ORIGINAL\s+ARTICLE|REVIEW\s+ARTICLE|OPEN\s+ACCESS|"
    r"ARTICLE|LETTER|REPORT|COMMUNICATION|SHORT\s+PAPER|BRIEF\s+REPORT|TECHNICAL\s+NOTE|"
    r"PREPRINT|MANUSCRIPT)\s*$",
    re.I,
)
_CODE_HINT_RE = re.compile(
    r"(?:def\s+\w+\s*\(|class\s+\w+|import\s+\w+|return\s+|=\s*\w+\s*[.(]|->|::|\{\s*$|\}\s*$)"
)
_MONO_FONT_RE = re.compile(
    r"(?:mono|courier|cmtt|cmvtt|lmtt|lmvtt|sftt|ectt|txb?tt|pxb?tt|consolas|inconsolata|bera|sourcecode|nimbusmon|typewriter)",
    re.I,
)
_MATH_FONT_RE = re.compile(
    r"(?:cmmi|cmsy|cmex|msam|msbm|txmi|txsy|txex|pxmi|pxsy|pxex|math|symb|stix|"
    r"newcmmath|eufm|eusm|eurm|rsfs|lmm|bbold|dsrom|cmmib|cmbsy|wasy|stmary|rtxmi|esint|mt2)",
    re.I,
)
_NON_TEXT_FONT_RE = re.compile(
    r"(?:zapf|dingbat|wingding|webding|marvosym|euex|feyn|advp4c|symbol|chess|music|mtmi|mtsyn)",
    re.I,
)
_MATH_UNICODE_OPS = frozenset(
    "×±∓∼≈≃≅≡≠≤≥≪≫→←↔⇒⇐⇔↦∈∉⊂⊃⊆⊇∪∩∅∞∇∂∀∃⊗⊕⊙∝⊥∥⟨⟩∘∑∏∫√·−∗′¬∧∨⊢⊨"
)
_PROSE_FUNC_NAMES = frozenset(
    "log exp sin cos tan min max arg lim sup inf det dim Tr Pr argmin argmax softmax relu tanh sigmoid".split()
)
_FOOTNOTE_MARK_RE = re.compile(r"^[\d*∗†‡§¶a-z]{1,2}$")
_COMMON_1L = frozenset({"a", "A", "I"})
_COMMON_2L_WORDS = frozenset(
    "am an as at be by do go he hi if in is it me my no of ok on or so to up us we "
    "et al eg ie vs cf de la le en un".split()
)
_RELATION_OR_END_CHARS = frozenset("=<>≤≥≠≈∈∉⊂⊃⊆⊇≪≫∼→)]}|")
_URL_RE = re.compile(r"https?://\S+|www\.\S+")
_WS_RE = re.compile(r"\s+")
_SOFT_HYPHENS = frozenset({"\u0002", "\u00ad"})
_LINE_HYPHENS = frozenset({"-", "\u2010"})

# Subset prefix (e.g. ABCDEF+TimesNewRomanPS-ItalicMT)
_SUBSET_PREFIX_RE = re.compile(r"^[A-Z]{6}\+")
# Style/weight/width/encoding tokens stripped from trailing hyphen/underscore segments
_STYLE_TOKEN_RE = re.compile(
    r"^(?:bold|ital(?:ic)?|obli(?:que)?|slant(?:ed)?|regu(?:lar)?|reg|roma(?:n)?|rom|"
    r"medi(?:um)?|med|md|demi|semi|light|ligh|book|heavy|black|thin|ultra|extra|"
    r"cond(?:ensed)?|narrow|ext(?:ended)?|mt|ps|std|pro|lt|otf|tt|display|text|"
    r"caption|subhead|sc|osf|lf|tlf|tab|it|bd|bi|ri|rm|bx|ti|sl|bf|rg|el|ul)+$",
    re.I,
)
# Trailing unseparated foundry/style suffixes anchored at the end of a family stem
_TRAILING_STEM_STYLE_RE = re.compile(
    r"(?:MT|PS|OTF|Std|Pro|LT|Cond(?:ensed)?|Narrow|Ext(?:ended)?|Regular|Regu|"
    r"Roman|Roma|Bold|Italic|Ital|Oblique|Obli|Slanted|Medium|Medi|Md|Demi|Semi|"
    r"Light|Ligh|Book|Heavy|Black|Display|Text|Caption|Subhead)$",
)
_CM_FAMILY_RE = re.compile(r"^(cm|lm|ec|sf|tx|px|nimbusrom|nimbussan|nimbusmon|ptm|phv|pcr|utm|uhv)", re.I)


def _r2(val: float) -> float:
    return round(float(val), 2)


def _font_family(name: str) -> str:
    """Normalize a font name to its prose family (collapsing regular/bold/italic/size variants)."""
    if not name:
        return ""
    s = _SUBSET_PREFIX_RE.sub("", name.strip())
    # Computer Modern / Latin Modern / TeX family prefixes (e.g. CMR10, CMBX10, CMTI10, CMSL10 -> cm)
    m = _CM_FAMILY_RE.match(s)
    if m and not _MATH_FONT_RE.search(s) and not _MONO_FONT_RE.search(s):
        return m.group(1).lower()
    # Strip trailing optical size digits (e.g. Garamond10 -> Garamond)
    base = re.sub(r"\d+$", "", s)
    # Strip trailing separator-delimited style segments (e.g. URWPalladioL-Roma, Times-BoldItalic)
    parts = re.split(r"[-_,]+", base)
    while len(parts) > 1 and (not parts[-1] or _STYLE_TOKEN_RE.match(parts[-1])):
        parts.pop()
    stem = "".join(parts)
    # Strip trailing unseparated foundry/style suffixes (e.g. TimesNewRomanPSMT, FormataOTFCond)
    while len(stem) > 3:
        m2 = _TRAILING_STEM_STYLE_RE.search(stem)
        if not m2 or m2.start() < 3:
            break
        stem = stem[: m2.start()]
    norm = re.sub(r"[^a-zA-Z0-9]+", "", stem).lower()
    return norm or s.lower()


def _is_mono_font(name: str) -> bool:
    return bool(_MONO_FONT_RE.search(name or ""))


def _is_math_font(name: str) -> bool:
    return bool(_MATH_FONT_RE.search(name or ""))


def _is_math_unicode(ch: str) -> bool:
    if not ch:
        return False
    cp = ord(ch)
    return (
        ch in _MATH_UNICODE_OPS
        or 0x0370 <= cp <= 0x03FF  # Greek
        or 0x2100 <= cp <= 0x214F  # Letterlike symbols (Ω U+2126, ℓ, ℝ, ℤ, ℕ, ℂ, ℚ, ℏ)
        or 0x2190 <= cp <= 0x22FF  # Arrows + Math ops
        or 0x27C0 <= cp <= 0x27EF  # Misc math symbols-A
        or 0x2980 <= cp <= 0x2AFF  # Supplemental math
        or 0x1D400 <= cp <= 0x1D7FF  # Math alphanum
    )


def _char_end_i(c: dict[str, Any]) -> int:
    return c["i"] + (2 if ord(c["u"]) > 0xFFFF else 1)


def _coalesce_ranges(spans: list[tuple[int, int]]) -> list[list[int]]:
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


def _box_overlaps(a: list[float], b: list[float], pad: float = 0.0) -> bool:
    return not (a[2] + pad < b[0] or b[2] + pad < a[0] or a[3] + pad < b[1] or b[3] + pad < a[1])


def _box_area(b: list[float]) -> float:
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def _inter_area(a: list[float], b: list[float]) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    return ix * iy


def _y_top_frac(y: float, page: dict[str, Any]) -> float:
    """Convert PDF user-space y (bottom-left origin) to fraction down from page top [0..1]."""
    cy1 = page["cropbox"][3]
    H = max(1.0, page["height"])
    return (cy1 - y) / H


def _is_ref_heading_text(txt: str) -> bool:
    if _REF_HEAD_RE.match(txt):
        return True
    collapsed = re.sub(r"[^a-z]", "", txt.lower())
    return collapsed in ("references", "bibliography") and len(txt) <= 32


def _is_abstract_heading_text(txt: str) -> bool:
    if _ABSTRACT_HEAD_RE.match(txt):
        return True
    collapsed = re.sub(r"[^a-z]", "", txt.lower())
    return collapsed == "abstract" and len(txt) <= 22


def _normalize_spaced_caps(txt: str) -> str:
    """Collapse letter-spaced headings like 'R E F E R E N C E S' -> 'REFERENCES'."""
    toks = txt.split()
    if len(toks) >= 4 and sum(1 for t in toks if len(t) == 1 and t.isalpha()) >= 0.75 * len(toks):
        return "".join(toks)
    return txt


def _assign_chars_to_lines(
    bchars: list[dict[str, Any]], line_boxes: list[list[float]]
) -> list[list[dict[str, Any]]]:
    """Partition a block's char dicts into its visual lines, sorted left-to-right."""
    if not bchars:
        return []
    if len(line_boxes) <= 1:
        return [sorted(bchars, key=lambda c: (c["bbox"][0], c["i"]))]
    buckets: list[list[dict[str, Any]]] = [[] for _ in line_boxes]
    line_ys = [0.5 * (lb[1] + lb[3]) for lb in line_boxes]
    for c in bchars:
        cy = 0.5 * (c["bbox"][1] + c["bbox"][3])
        best_li = 0
        best_d = 1e9
        for li, (lb, ly) in enumerate(zip(line_boxes, line_ys)):
            # Prefer line whose vertical range contains cy, breaking ties by center distance
            in_y = lb[1] - 1.0 <= cy <= lb[3] + 1.0
            d = abs(cy - ly) - (100.0 if in_y else 0.0)
            if d < best_d:
                best_d = d
                best_li = li
        buckets[best_li].append(c)
    out: list[list[dict[str, Any]]] = []
    for bk in buckets:
        if bk:
            bk.sort(key=lambda c: (c["bbox"][0], c["i"]))
            out.append(bk)
    return out


def _line_stats(cs: list[dict[str, Any]]) -> dict[str, Any]:
    non_math_szs = [c["size"] for c in cs if not _is_math_font(c["font"])]
    ref_max = max(non_math_szs) if non_math_szs else max(c["size"] for c in cs)
    ref_sz = ref_max
    if non_math_szs:
        mode_sz, mode_cnt = Counter(round(s, 1) for s in non_math_szs).most_common(1)[0]
        # If a majority prose size dominates the line (e.g. a drop-cap or a larger heading
        # merged onto the same visual line), anchor to the modal size instead of ref_max.
        if mode_cnt >= 10 and mode_cnt >= 0.50 * len(non_math_szs) and mode_sz < 0.85 * ref_max:
            ref_sz = mode_sz
    main_cs = [c for c in cs if 0.82 * ref_sz <= c["size"] <= 1.25 * ref_sz] or cs
    dom_size = Counter(round(c["size"], 1) for c in main_cs).most_common(1)[0][0]
    base_med = float(st.median(c["origin"][1] for c in main_cs))
    bold_frac = sum(1 for c in cs if c["bold"]) / len(cs)
    ital_frac = sum(1 for c in cs if c["italic"]) / len(cs)
    mono_frac = sum(1 for c in cs if _is_mono_font(c["font"])) / len(cs)
    return {
        "cs": cs,
        "x0": min(c["bbox"][0] for c in cs),
        "y0": min(c["bbox"][1] for c in cs),
        "x1": max(c["bbox"][2] for c in cs),
        "y1": max(c["bbox"][3] for c in cs),
        "base": base_med,
        "size": dom_size,
        "bold_frac": bold_frac,
        "ital_frac": ital_frac,
        "mono_frac": mono_frac,
        "text": normalise(cs),
    }


def _is_rotated_block(b: dict[str, Any], bchars: list[dict[str, Any]]) -> bool:
    if len(bchars) < 4 or len(b["lines"]) > 1:
        return False
    bw = b["bbox"][2] - b["bbox"][0]
    bh = b["bbox"][3] - b["bbox"][1]
    if bh <= max(2.2 * bw, 35.0):
        return False
    xs = [c["origin"][0] for c in bchars]
    ys = [c["origin"][1] for c in bchars]
    return (max(ys) - min(ys)) > 2.0 * max(1.0, max(xs) - min(xs))


def _is_hyphenated_acronym_char(cs: list[dict[str, Any]], idx: int) -> bool:
    """Return True if cs[idx] is part of a hyphenated acronym like R-CNN, U-Net, T-SNE, k-NN.

    An italic or math-font variable immediately before '-' + upright word (e.g. $B$-mode,
    $k$-tuples, $d$-dimensional) is a math-variable compound, not a prose acronym.
    """
    c = cs[idx]
    if not c["u"].isalpha():
        return False
    # Check right: X-Word (e.g. R-CNN, U-Net)
    if idx + 2 < len(cs) and cs[idx + 1]["u"] in ("-", "\u2010", "\u2011"):
        if cs[idx + 1]["bbox"][0] - c["bbox"][2] < 0.25 * c["size"]:
            # Italic/math-font single letter + '-' + upright lowercase word ($k$-tuples, $B$-mode)
            if (c["italic"] or _is_math_font(c["font"])) and not cs[idx + 2]["italic"] and not _is_math_font(cs[idx + 2]["font"]) and cs[idx + 2]["u"].islower():
                return False
            # Count alpha chars after hyphen
            k = idx + 2
            while k < len(cs) and cs[k]["u"].isalpha() and cs[k]["bbox"][0] - cs[k - 1]["bbox"][2] < 0.22 * c["size"]:
                k += 1
            if k - (idx + 2) >= 2:
                return True
    # Check left: Word-X
    if idx >= 2 and cs[idx - 1]["u"] in ("-", "\u2010", "\u2011"):
        if c["bbox"][0] - cs[idx - 1]["bbox"][2] < 0.25 * c["size"]:
            k = idx - 2
            while k >= 0 and cs[k]["u"].isalpha() and cs[k + 1]["bbox"][0] - cs[k]["bbox"][2] < 0.22 * c["size"]:
                k -= 1
            if (idx - 1) - (k + 1) >= 2:
                return True
    return False


def _char_is_math_seed(
    cs: list[dict[str, Any]],
    idx: int,
    line_sz: float,
    line_base: float,
    line_bold_frac: float,
    line_ital_frac: float,
    body_font: str,
    body_family: str,
) -> bool:
    c = cs[idx]
    ch = c["u"]
    fn = c["font"]
    prev_c = cs[idx - 1] if idx > 0 else None
    next_c = cs[idx + 1] if idx + 1 < len(cs) else None

    # 1. Dedicated math font (excluding body font)
    if fn and fn != body_font and _is_math_font(fn):
        return True
    # 2. Unicode math / Greek / math alphanumerics
    if _is_math_unicode(ch):
        return True
    # 3. Sub/superscript shift in PDF user space (higher y = higher on page)
    if c["size"] < 0.82 * line_sz and abs(c["origin"][1] - line_base) >= 0.12 * line_sz:
        is_sup = (c["origin"][1] - line_base) >= 0.12 * line_sz
        if is_sup and _FOOTNOTE_MARK_RE.match(ch):
            if prev_c and (
                prev_c["u"] in ".,;:!?)\"”’"
                or (prev_c["u"].isalpha() and not prev_c["italic"] and _font_family(prev_c["font"]) == body_family)
            ):
                return False
        return True
    # 4. Isolated single-letter italic OR bold-vector variable
    if ch.isalpha() and not (prev_c is not None and prev_c["u"] in "'’"):
        left_sep = (
            prev_c is None
            or (c["bbox"][0] - prev_c["bbox"][2] > 0.15 * c["size"])
            or (prev_c["u"] in "([{\"=+-/*<>|,;:")
        )
        right_sep = (
            next_c is None
            or (next_c["bbox"][0] - c["bbox"][2] > 0.15 * c["size"])
            or (next_c["u"] in "()[]}\"=+-/*<>|,;:.!?")
        )
        # Disallow '(' on right unless left is separated (e.g. g(p), f(x), E(Λ))
        if next_c is not None and next_c["u"] == "(" and not left_sep:
            right_sep = False
        if left_sep and right_sep and not _is_hyphenated_acronym_char(cs, idx):
            is_bold_subfig = (
                c["bold"]
                and not c["italic"]
                and ch.islower()
                and prev_c is not None
                and next_c is not None
                and prev_c["u"] == "("
                and next_c["u"] == ")"
            )
            if line_ital_frac < 0.72:
                is_bold_var = c["bold"] and line_bold_frac < 0.45 and not is_bold_subfig
                if c["italic"] and ch not in _COMMON_1L:
                    return True
                if is_bold_var:
                    if ch not in _COMMON_1L:
                        return True
                    # Bold A/I/a (e.g. \mathbf{A}, \mathbf{I}) in a foreign bold font or next to math ops
                    is_heading_label = next_c is not None and next_c["u"] == "."
                    if not is_heading_label and (
                        (fn and _font_family(fn) != body_family)
                        or (prev_c is not None and prev_c["u"] in "=+-/*<>≤≥≠≈∈×·(")
                        or (next_c is not None and next_c["u"] in "=+-/*<>≤≥≠≈∈×·)")
                    ):
                        return True
            else:
                # All-italic theorem/lemma line: still seed isolated single-letter math variables
                # (skip 'a'/'A'/'I', lowercase list items '(a)'/'(b)'/'(i)', 'i.e.'/'e.g.', and 'B.'/'J.')
                is_paren_list = (
                    ch.islower()
                    and prev_c is not None
                    and next_c is not None
                    and prev_c["u"] == "("
                    and next_c["u"] == ")"
                    and (idx < 2 or not cs[idx - 2]["u"].isalpha())
                )
                is_abbrev_or_label = next_c is not None and next_c["u"] == "." and (
                    ch.isupper() or (idx + 2 < len(cs) and cs[idx + 2]["u"].isalpha())
                )
                if ch not in _COMMON_1L and not is_paren_list and not is_abbrev_or_label:
                    return True
    # 5. Explicit binary relations
    if ch in "=<>≈≠≤≥∈":
        return True
    return False


_SUP_CITE_CHARS = frozenset("0123456789,-–—*∗†‡§¶")


def _is_raised_sup_char(c: dict[str, Any], line_sz: float, line_base: float) -> bool:
    return c["size"] < 0.82 * line_sz and (c["origin"][1] - line_base) >= 0.12 * line_sz


def _find_superscript_citation_runs(
    cs: list[dict[str, Any]],
    line_sz: float,
    line_base: float,
    body_family: str,
) -> list[tuple[int, int]]:
    """Find raised superscript citation/footnote runs attached to prose words or punctuation.

    Also isolates leading footnote markers at the start of a line (e.g. '1Following', '2This',
    '∗Jean', 'cEight-factor') so the prose words after the marker are never swallowed.

    Returns list of (start_idx, end_idx_excl) covering ONLY the raised glyphs.
    """
    n = len(cs)
    runs: list[tuple[int, int]] = []
    i = 0

    # Leading footnote marker at start of line (raised 1-2 char digit/symbol/letter before prose)
    if n >= 2 and _is_raised_sup_char(cs[0], line_sz, line_base):
        j = 1
        while j < n and j < 3 and _is_raised_sup_char(cs[j], line_sz, line_base):
            j += 1
        lead_txt = "".join(cs[k]["u"] for k in range(0, j))
        if (
            j < n
            and _FOOTNOTE_MARK_RE.match(lead_txt)
            and not _is_raised_sup_char(cs[j], line_sz, line_base)
            and not _is_math_font(cs[j]["font"])
        ):
            nxt_u = cs[j]["u"]
            # Check that what follows is prose (a word of >= 2 letters, quote/paren + word, or URL)
            starts_prose = False
            if nxt_u.isalpha():
                k = j + 1
                while (
                    k < n
                    and cs[k]["u"].isalpha()
                    and not _is_raised_sup_char(cs[k], line_sz, line_base)
                    and cs[k]["bbox"][0] - cs[k - 1]["bbox"][2] <= 0.17 * line_sz
                ):
                    k += 1
                # Require >= 2 letters (or single-letter 'I'/'A' followed by space)
                if k - j >= 2 or (nxt_u in ("I", "A") and (k >= n or cs[k]["bbox"][0] - cs[k - 1]["bbox"][2] > 0.17 * line_sz)):
                    # If the marker is a lowercase letter (e.g. 'cEight-factor'), require uppercase start
                    if not lead_txt.isalpha() or nxt_u.isupper():
                        starts_prose = True
            elif nxt_u in "\"'“‘(":
                starts_prose = True
            if starts_prose:
                runs.append((0, j))
                i = j

    while i < n:
        if not _is_raised_sup_char(cs[i], line_sz, line_base) or cs[i]["u"] not in _SUP_CITE_CHARS:
            i += 1
            continue
        j = i + 1
        while j < n and _is_raised_sup_char(cs[j], line_sz, line_base) and cs[j]["u"] in _SUP_CITE_CHARS:
            j += 1
        # Must contain at least one digit or footnote symbol (not a stray raised comma/dash)
        run_txt = "".join(cs[k]["u"] for k in range(i, j))
        if any(ch.isdigit() or ch in "*∗†‡§¶" for ch in run_txt) and i > 0:
            prev_c = cs[i - 1]
            # Attached to sentence/clause punctuation
            after_punct = prev_c["u"] in ".,;:!?\"”’"
            # Attached to closing paren after an upright multi-letter word/acronym or multi-cite (e.g. (XFEL)8,9)
            after_paren_word = False
            if prev_c["u"] == ")":
                has_multi_cite = any(ch in ",-–—" for ch in run_txt)
                if has_multi_cite:
                    after_paren_word = True
                elif i >= 4 and cs[i - 2]["u"].isalpha() and not cs[i - 2]["italic"] and not _is_math_font(cs[i - 2]["font"]):
                    k = i - 2
                    while (
                        k > 0
                        and cs[k - 1]["u"].isalpha()
                        and not cs[k - 1]["italic"]
                        and cs[k]["bbox"][0] - cs[k - 1]["bbox"][2] <= 0.17 * line_sz
                    ):
                        k -= 1
                    if (i - 1) - k >= 3:
                        after_paren_word = True
            # Or attached to a multi-letter (>= 3) upright prose word (not a single-letter math variable or unit)
            after_word = False
            if prev_c["u"].isalpha() and not prev_c["italic"] and not _is_math_font(prev_c["font"]):
                k = i - 1
                while (
                    k > 0
                    and cs[k - 1]["u"].isalpha()
                    and not cs[k - 1]["italic"]
                    and not _is_raised_sup_char(cs[k - 1], line_sz, line_base)
                    and cs[k]["bbox"][0] - cs[k - 1]["bbox"][2] <= 0.22 * line_sz
                ):
                    k -= 1
                word_len = i - k
                if word_len >= 3:
                    after_word = True
            if after_punct or after_paren_word or after_word:
                runs.append((i, j))
        i = j
    return runs


_PROSE_WORD_RE = re.compile(r"[a-z]{3,}|[A-Z][a-z]{2,}")
_CARVE_KEEP_NEIGHBOURS = frozenset("-‐‑+=<>≤≥≈∼")
_CARVE_EDGE_CHARS = frozenset("-–—‐‑.,;:!?\"'“”‘’")


def _gap(cs: list[dict[str, Any]], a: int, b: int) -> float:
    return cs[b]["bbox"][0] - cs[a]["bbox"][2]


def _plain_prose_words(
    cs: list[dict[str, Any]],
    lo: int,
    hi: int,
    seeds: list[bool],
    abs_pos: set[int],
    line_sz: float,
    line_base: float,
    body_family: str,
    line_italic: bool,
    *,
    any_text_font: bool = False,
) -> list[tuple[int, int]]:
    """Index spans in cs[lo:hi] of dictionary-like words (>= 3 letters) set in plain, unshifted text-font glyphs.

    By default the glyphs must be body-font upright (italic is normal on an all-italic line); ``any_text_font``
    also accepts a word set uniformly in another text font/style (caption labels, bold run-in heads, oblique).
    """

    def plain(k: int) -> bool:
        c = cs[k]
        return (
            not seeds[k]
            and k not in abs_pos
            and c["u"].isascii()
            and c["u"].isalpha()
            and (any_text_font or line_italic or not c["italic"])
            and (any_text_font or _font_family(c["font"]) == body_family)
            and not _is_math_font(c["font"])
            and not _is_mono_font(c["font"])
            and not _NON_TEXT_FONT_RE.search(c["font"] or "")
            and c["size"] >= 0.82 * line_sz
            and abs(c["origin"][1] - line_base) < 0.12 * line_sz
        )

    words: list[tuple[int, int]] = []
    k = lo
    while k < hi:
        if not plain(k):
            k += 1
            continue
        e = k + 1
        while (
            e < hi
            and plain(e)
            and _gap(cs, e - 1, e) <= 0.17 * max(cs[e]["size"], cs[e - 1]["size"])
            and (not any_text_font or (cs[e]["italic"], cs[e]["bold"]) == (cs[e - 1]["italic"], cs[e - 1]["bold"]))
        ):
            e += 1
        left_ok = k == lo or not cs[k - 1]["u"].isalnum() or _gap(cs, k - 1, k) > 0.17 * line_sz
        right_ok = e == hi or not cs[e]["u"].isalnum() or _gap(cs, e - 1, e) > 0.17 * line_sz
        word = "".join(cs[m]["u"] for m in range(k, e))
        if (
            left_ok
            and right_ok
            and _PROSE_WORD_RE.fullmatch(word)
            and re.search("[aeiouy]", word, re.I)  # Gpc, ppbd: units, not words
            and word not in _PROSE_FUNC_NAMES
        ):
            words.append((k, e))
        k = e
    return words


def _carve_prose_words(
    cs: list[dict[str, Any]],
    span: tuple[int, int],
    seeds: list[bool],
    abs_pos: set[int],
    line_sz: float,
    line_base: float,
    body_family: str,
    line_italic: bool,
) -> list[tuple[int, int]]:
    """Split a math span around plain prose words; pieces without any math seed are dropped (they are prose)."""
    lo, hi = span
    cuts = []
    for k, e in _plain_prose_words(cs, lo, hi, seeds, abs_pos, line_sz, line_base, body_family, line_italic):
        # compounds (top-k, Z+jets), "name = value" and operator names (Vol(·)) stay whole:
        # GT treats them as one math term
        near = (cs[k - 1]["u"] if k > lo else "") + (cs[e]["u"] if e < hi else "")
        if not any(ch in _CARVE_KEEP_NEIGHBOURS for ch in near) and not (e < hi and cs[e]["u"] == "("):
            cuts.append((k, e))
    if not cuts:
        return [span]

    out: list[tuple[int, int]] = []
    cur = lo
    for a, b in [*cuts, (hi, hi)]:
        p0, p1 = cur, a
        while p0 < p1 and cs[p0]["u"] in _CARVE_EDGE_CHARS and not seeds[p0]:
            p0 += 1
        while p1 > p0 and cs[p1 - 1]["u"] in _CARVE_EDGE_CHARS and not seeds[p1 - 1]:
            p1 -= 1
        if any(seeds[m] or m in abs_pos for m in range(p0, p1)):
            out.append((p0, p1))
        cur = b
    return out


def _detect_inline_math_indices(
    ln: dict[str, Any],
    body_font: str,
    body_family: str,
    *,
    page_idx: int | None = None,
    absorbed_math_chars: set[tuple[int, int]] | None = None,
) -> list[tuple[int, int]]:
    """Return list of (start_idx, end_idx_excl) char spans within ln['cs'] that are inline math."""
    cs = ln["cs"]
    n = len(cs)
    if n == 0 or ln["mono_frac"] >= 0.65:
        return []
    line_sz = ln["size"]
    line_base = ln["base"]

    # Detect raised superscript citation runs attached to prose words/punctuation so they
    # stay isolated as their own spans covering only the raised glyphs.
    sup_cite_runs = _find_superscript_citation_runs(cs, line_sz, line_base, body_family)
    sup_cite_pos: set[int] = set()
    sup_cite_bounds: set[int] = set()
    for a, b in sup_cite_runs:
        sup_cite_pos.update(range(a, b))
        sup_cite_bounds.add(a)
        sup_cite_bounds.add(b)

    abs_pos: set[int] = set()
    if absorbed_math_chars and page_idx is not None:
        for i in range(n):
            if i not in sup_cite_pos and (page_idx, cs[i]["i"]) in absorbed_math_chars:
                abs_pos.add(i)

    seeds = [
        False
        if i in sup_cite_pos
        else (
            i in abs_pos
            or _char_is_math_seed(
                cs, i, line_sz, line_base, ln["bold_frac"], ln["ital_frac"], body_font, body_family
            )
        )
        for i in range(n)
    ]

    # Seed `O(` complexity notation (e.g. O(n log n))
    for i in range(n - 1):
        if i not in sup_cite_pos and cs[i]["u"] == "O" and cs[i + 1]["u"] == "(" and (cs[i]["italic"] or _is_math_font(cs[i]["font"])):
            seeds[i] = True

    # Tokenize line into words/runs by inter-char space gap (> 0.17*sz), superscript citation boundaries,
    # math-variable hyphen compounds (e.g. $B$-mode, $k$-tuples, $d$-dimensional), and tight prose->math gaps.
    tokens: list[tuple[int, int]] = []
    t0 = 0
    for i in range(1, n):
        sz = max(cs[i - 1]["size"], cs[i]["size"])
        gap = cs[i]["bbox"][0] - cs[i - 1]["bbox"][2]
        split_here = i in sup_cite_bounds or gap > 0.17 * sz
        if not split_here and cs[i]["u"] in ("-", "\u2010", "\u2011") and i + 2 < n:
            # Split italic/math-font variable before '-word' (upright lowercase word >= 2 letters)
            if (
                (cs[i - 1]["italic"] or _is_math_font(cs[i - 1]["font"]))
                and cs[i - 1]["u"].isalpha()
                and cs[i + 1]["u"].islower()
                and not cs[i + 1]["italic"]
                and not _is_math_font(cs[i + 1]["font"])
                and cs[i + 2]["u"].isalpha()
                and not cs[i + 2]["italic"]
                and not _is_math_font(cs[i + 2]["font"])
            ):
                split_here = True
        if not split_here and ln["ital_frac"] < 0.72 and gap > 0.09 * sz:
            # Split tight justified space between upright lowercase prose word and italic/math variable (e.g. 'of mH')
            if (
                cs[i - 1]["u"].islower()
                and not cs[i - 1]["italic"]
                and not _is_math_font(cs[i - 1]["font"])
                and (cs[i]["italic"] or _is_math_font(cs[i]["font"]))
                and cs[i]["u"].isalpha()
            ):
                split_here = True
        if split_here:
            tokens.append((t0, i))
            t0 = i
    tokens.append((t0, n))

    def _tok_str(a: int, b: int) -> str:
        return "".join(cs[k]["u"] for k in range(a, b))

    # Seed isolated 2-letter (and 3-letter mixed-case) italic math tokens on non-italic lines
    # (e.g. $TT$, $TE$, $EE$, $ET$, $tt$, $jj$, $WW$, $ttH$, $TE/ET$), never touching italic phrases.
    if ln["ital_frac"] < 0.72:
        def _tok_is_italic_word(ti_idx: int) -> bool:
            if ti_idx < 0 or ti_idx >= len(tokens):
                return False
            ta, tb = tokens[ti_idx]
            ts = _tok_str(ta, tb).strip(".,;:!?\"'“”‘’()[]")
            if len(ts) < 2 or not ts.isalpha():
                return False
            return all(cs[k]["italic"] for k in range(ta, tb) if cs[k]["u"].isalpha())

        def _is_short_italic_math_part(part: str) -> bool:
            if len(part) == 2 and part.isalpha():
                if part.isupper() or part[0] == part[1]:
                    return True
                if part[0].islower() and part[1].isupper():
                    return True
                if part.islower() and part not in _COMMON_2L_WORDS:
                    return True
            if len(part) == 3 and part.isalpha():
                if part[0].islower() and any(ch.isupper() for ch in part[1:]):
                    return True
            return False

        for ti, (a, b) in enumerate(tokens):
            if any(seeds[k] for k in range(a, b)) or any(k in sup_cite_pos for k in range(a, b)):
                continue
            # Trim outer punctuation to inspect inner token
            ia, ib = a, b
            while ia < ib and cs[ia]["u"] in ".,;:!?\"'“”‘’()[]{}":
                ia += 1
            while ib > ia and cs[ib - 1]["u"] in ".,;:!?\"'“”‘’()[]{}":
                ib -= 1
            if ia >= ib:
                continue
            alpha_cs = [cs[k] for k in range(ia, ib) if cs[k]["u"].isalpha()]
            if not alpha_cs or not all(c["italic"] for c in alpha_cs):
                continue
            core = "".join(cs[k]["u"] for k in range(ia, ib))
            parts = core.split("/")
            if all(_is_short_italic_math_part(p) for p in parts):
                # Skip if adjacent to an italic prose word (part of an italic phrase like 'in vitro')
                if _tok_is_italic_word(ti - 1) or _tok_is_italic_word(ti + 1):
                    # Allow all-caps/mixed-case math even next to italic punctuation/words only if not >=3L italic phrase
                    prev_3l = ti > 0 and _tok_is_italic_word(ti - 1) and len(_tok_str(*tokens[ti - 1]).strip(".,;:!?()[]")) >= 3
                    next_3l = ti + 1 < len(tokens) and _tok_is_italic_word(ti + 1) and len(_tok_str(*tokens[ti + 1]).strip(".,;:!?()[]")) >= 3
                    if prev_3l or next_3l:
                        continue
                for k in range(ia, ib):
                    seeds[k] = True

    if not any(seeds) and not sup_cite_runs:
        return []

    def _tok_is_prose_word(a: int, b: int) -> bool:
        s = _tok_str(a, b)
        core = s.strip(".,;:!?\"'“”‘’()[]")
        if len(core) >= 3 and core.isalpha() and core.lower() not in _PROSE_FUNC_NAMES:
            if not any(seeds[k] for k in range(a, b)):
                return True
        if (
            len(core) == 2
            and core.lower()
            in ("in", "on", "of", "to", "at", "by", "is", "as", "or", "an", "be", "we", "if", "so", "no", "it", "do")
            and not any(seeds[k] for k in range(a, b))
        ):
            return True
        return False

    def _tok_can_join(a: int, b: int, *, adj_op: bool = False) -> bool:
        if any(k in sup_cite_pos for k in range(a, b)):
            return False
        if any(seeds[k] for k in range(a, b)):
            return True
        s = _tok_str(a, b)
        if not s or _tok_is_prose_word(a, b):
            return False
        # Do not swallow numeric citations like [13], [22, 23] into adjacent math
        if re.fullmatch(r"\[\d+(?:[,–\-]\d+)*\]?", s):
            return False
        if re.fullmatch(r"[0-9.,+\-*/=<>|^_()\[\]{}!:'\"·]+", s):
            return True
        if s in _PROSE_FUNC_NAMES:
            return True
        if len(s) <= 2 and all(cs[k]["italic"] or cs[k]["u"] in "()[]{},.;:-+" for k in range(a, b)):
            return True
        if adj_op and len(s) == 1 and s.isalpha() and s != "a":
            return True
        return False

    def _tok_has_math_op(a: int, b: int) -> bool:
        return any(cs[k]["u"] in "=+-−/*<>≤≥≠≈∈×·≪≫∼→" for k in range(a, b))

    tok_math = [any(seeds[k] for k in range(a, b)) for a, b in tokens]
    changed = True
    while changed:
        changed = False
        for ti in range(len(tokens)):
            if not tok_math[ti]:
                continue
            if ti + 1 < len(tokens) and not tok_math[ti + 1]:
                a1, b1 = tokens[ti]
                a2, b2 = tokens[ti + 1]
                if cs[a2]["bbox"][0] - cs[b1 - 1]["bbox"][2] <= 1.2 * line_sz and _tok_can_join(
                    a2, b2, adj_op=_tok_has_math_op(a1, b1)
                ):
                    s2 = _tok_str(a2, b2)
                    if not (s2 in (".", ",", ";", ":", "!", "?") and (ti + 2 >= len(tokens) or not tok_math[ti + 2])):
                        tok_math[ti + 1] = True
                        changed = True
            if ti - 1 >= 0 and not tok_math[ti - 1]:
                a0, b0 = tokens[ti - 1]
                a1, b1 = tokens[ti]
                if cs[a1]["bbox"][0] - cs[b0 - 1]["bbox"][2] <= 1.2 * line_sz and _tok_can_join(
                    a0, b0, adj_op=_tok_has_math_op(a1, b1)
                ):
                    s0 = _tok_str(a0, b0)
                    if s0 not in (".", ",", ";", ":", "!", "?"):
                        tok_math[ti - 1] = True
                        changed = True

    spans: list[tuple[int, int]] = []
    i = 0
    while i < len(tokens):
        if not tok_math[i]:
            i += 1
            continue
        j = i
        # Track bracket depth so we split separate formulas across top-level ';', '.', or ','
        depth = 0
        for k in range(tokens[i][0], tokens[i][1]):
            u = cs[k]["u"]
            if u in "([{":
                depth += 1
            elif u in ")]}":
                depth = max(0, depth - 1)
        while j + 1 < len(tokens) and tok_math[j + 1]:
            cur_a, cur_b = tokens[j]
            nxt_a, nxt_b = tokens[j + 1]
            cur_last = cs[cur_b - 1]["u"]
            nxt_first = cs[nxt_a]["u"]
            has_gap = cs[nxt_a]["bbox"][0] - cs[cur_b - 1]["bbox"][2] > 0.17 * line_sz
            # Split across top-level sentence/clause boundary or parenthesized sibling formula
            if depth == 0 and has_gap:
                if cur_last in (";", ".") and not _tok_str(cur_a, cur_b).endswith(".."):
                    break
                if cur_last == "," and nxt_first not in ".·…":
                    left_txt = "".join(cs[k]["u"] for k in range(tokens[i][0], cur_b))
                    left_core = left_txt.rstrip(",")
                    if any(ch in _RELATION_OR_END_CHARS for ch in left_txt) or (
                        len(left_core) >= 2 and left_core.isalpha() and left_core.isupper()
                    ):
                        break
                if nxt_first == "(" and cur_last in "%0123456789)]}" or (
                    nxt_first == "(" and len(_tok_str(cur_a, cur_b)) >= 2 and not _tok_has_math_op(cur_a, cur_b) and _tok_str(cur_a, cur_b) not in _PROSE_FUNC_NAMES
                ):
                    break
            j += 1
            for k in range(nxt_a, nxt_b):
                u = cs[k]["u"]
                if u in "([{":
                    depth += 1
                elif u in ")]}":
                    depth = max(0, depth - 1)
        c_lo = tokens[i][0]
        c_hi = tokens[j][1]
        while c_lo < c_hi and cs[c_lo]["u"] in ".,;:!?\"'“”‘’" and not seeds[c_lo]:
            c_lo += 1
        while c_hi > c_lo and cs[c_hi - 1]["u"] in ".,;:!?\"'“”‘’" and not seeds[c_hi - 1]:
            c_hi -= 1
        if c_lo < c_hi:
            txt = "".join(cs[k]["u"] for k in range(c_lo, c_hi)).strip()
            has_abs = any(k in abs_pos for k in range(c_lo, c_hi))
            if txt and (has_abs or not re.fullmatch(r"[\d.,%]+", txt)):
                spans.append((c_lo, c_hi))
        i = j + 1

    # Prose words (plain body-font upright, >= 3 letters) never live inside a math placeholder: carve them out.
    spans = [
        piece
        for sp in spans
        for piece in _carve_prose_words(cs, sp, seeds, abs_pos, line_sz, line_base, body_family, ln["ital_frac"] >= 0.72)
    ]

    # Add isolated superscript citation runs (covering only the raised glyphs)
    if sup_cite_runs:
        spans.extend(sup_cite_runs)
        spans.sort(key=lambda sp: sp[0])
    return spans


def _block_math_ratio(
    line_stats: list[dict[str, Any]], body_font: str, body_family: str
) -> tuple[float, int]:
    """Return (fraction of chars in math spans, count of upright multi-letter prose words)."""
    tot_ch = 0
    math_ch = 0
    prose_words = 0
    for ln in line_stats:
        cs = ln["cs"]
        tot_ch += len(cs)
        spans = _detect_inline_math_indices(ln, body_font, body_family)
        m_pos: set[int] = set()
        for a, b in spans:
            m_pos.update(range(a, b))
        math_ch += len(m_pos)
        non_math_cs = [c for k, c in enumerate(cs) if k not in m_pos]
        non_math_txt = normalise(non_math_cs)
        for w in re.findall(r"[A-Za-z]{3,}", non_math_txt):
            if w.lower() not in _PROSE_FUNC_NAMES:
                prose_words += 1
    return (math_ch / max(1, tot_ch)), prose_words


def _detect_running_headers_footers(
    blocks: list[dict[str, Any]],
    pages: list[dict[str, Any]],
    b_meta: dict[str, dict[str, Any]],
) -> dict[str, str]:
    """Return {block_id: 'header' | 'footer'} for running headers/footers and page numbers."""
    n_pages = len(pages)
    occ: dict[tuple[str, str], list[tuple[int, str, float]]] = {}
    marked: dict[str, str] = {}

    for b in blocks:
        bid = b["id"]
        p = pages[b["page"]]
        bb = b["bbox"]
        cy_frac = _y_top_frac(0.5 * (bb[1] + bb[3]), p)
        txt = b["text"].strip()
        if not txt:
            continue

        # Rotated margin stamps (e.g. arXiv margin stamp)
        if b_meta[bid]["rotated"]:
            cx0, _, cx1, _ = p["cropbox"]
            W = p["width"]
            if bb[2] <= cx0 + 0.08 * W or bb[0] >= cx1 - 0.08 * W:
                marked[bid] = "header"
            continue

        where: str | None = None
        if cy_frac <= 0.10:
            where = "header"
        elif cy_frac >= 0.90:
            where = "footer"
        if where is None:
            continue

        if len(b["lines"]) <= 2 and _PAGE_NUM_RE.match(txt) and len(txt) <= 8:
            marked[bid] = where
            continue

        if len(b["lines"]) <= 2 and len(txt) <= 140:
            norm = " ".join(_DIGITS_RE.sub("", txt).split()).strip()
            if norm:
                occ.setdefault((where, norm), []).append((b["page"], bid, bb[3]))

    min_pages = 3 if n_pages >= 5 else (2 if n_pages >= 3 else 999)
    for (where, _norm), items in occ.items():
        items.sort(key=lambda x: x[2])
        clusters: list[list[tuple[int, str, float]]] = []
        for it in items:
            if clusters and abs(it[2] - clusters[-1][0][2]) <= 10.0:
                clusters[-1].append(it)
            else:
                clusters.append([it])
        for cl in clusters:
            if len({x[0] for x in cl}) >= min_pages:
                for _pg, bid, _y in cl:
                    marked[bid] = where
    return marked


def _detect_table_and_algo_zones(
    page: dict[str, Any],
    page_blocks: list[dict[str, Any]],
    body_size: float,
) -> tuple[list[list[float]], list[list[float]]]:
    """Detect ruled table zones (split at intervening Table N captions) and ruled algorithm zones.

    All coordinates are in PDF user space (bottom-left origin: higher y = higher on page).
    """
    W = page["width"]
    H = page["height"]
    h_rules = [
        o["bbox"]
        for o in page["objects"]
        if o["kind"] == "rule"
        and (o["bbox"][2] - o["bbox"][0]) >= 0.18 * W
        and (o["bbox"][3] - o["bbox"][1]) <= 4.0
        and 0.07 <= _y_top_frac(0.5 * (o["bbox"][1] + o["bbox"][3]), page) <= 0.92
    ]
    if len(h_rules) < 2:
        return [], []

    # Sort rules top-to-bottom (descending y)
    rules = sorted(h_rules, key=lambda r: -r[3])

    # 1. Ruled algorithm zones
    algo_zones: list[list[float]] = []
    algo_rule_used: set[int] = set()
    for b in page_blocks:
        if not _ALGO_RE.match(b["text"].strip()):
            continue
        bb = b["bbox"]
        top_ri = None
        for ri, r in enumerate(rules):
            # Rule near or slightly above the Algorithm title (higher y = above)
            if bb[1] - 6.0 <= r[1] <= bb[3] + 18.0 and min(r[2], bb[2]) > max(r[0], bb[0]):
                top_ri = ri
                break
        if top_ri is None:
            continue
        top_r = rules[top_ri]
        cand_below = [
            (ri, r)
            for ri, r in enumerate(rules)
            if r[3] < bb[1] - 10.0
            and top_r[3] - r[1] < 0.72 * H
            and (min(r[2], top_r[2]) - max(r[0], top_r[0]))
            / max(1.0, max(r[2] - r[0], top_r[2] - top_r[0]))
            >= 0.75
        ]
        if cand_below:
            bot_ri, bot_r = cand_below[1] if len(cand_below) >= 2 else cand_below[-1]
            algo_zones.append(
                [
                    min(top_r[0], bot_r[0]) - 4.0,
                    bot_r[1] - 2.0,
                    max(top_r[2], bot_r[2]) + 4.0,
                    max(top_r[3], bb[3]) + 2.0,
                ]
            )
            algo_rule_used.add(top_ri)
            algo_rule_used.add(bot_ri)

    # 2. Ruled table zones (split stacked tables at intervening Table N captions)
    table_caps = [b for b in page_blocks if _TABLE_CAP_RE.match(b["text"].strip())]
    all_caps = [b for b in page_blocks if _CAPTION_RE.match(b["text"].strip())]

    table_zones: list[list[float]] = []
    used: set[int] = set(algo_rule_used)
    for i, r1 in enumerate(rules):
        if i in used:
            continue
        grp = [i]
        for j in range(i + 1, len(rules)):
            if j in used:
                continue
            r2 = rules[j]
            prev_r = rules[grp[-1]]
            if prev_r[1] - r2[3] > 0.48 * H:
                break
            ov = min(r1[2], r2[2]) - max(r1[0], r2[0])
            mw = max(r1[2] - r1[0], r2[2] - r2[0])
            if mw > 0 and ov / mw >= 0.75:
                # Split stacked tables if an intervening Table/Figure caption sits between prev_r and r2
                intervening_cap = any(
                    r2[3] <= 0.5 * (cb["bbox"][1] + cb["bbox"][3]) <= prev_r[1]
                    and min(cb["bbox"][2], max(r1[2], r2[2]) + 30.0)
                    > max(cb["bbox"][0], min(r1[0], r2[0]) - 30.0)
                    for cb in all_caps
                )
                if intervening_cap:
                    break
                grp.append(j)
        if len(grp) < 2:
            continue

        x0 = min(rules[k][0] for k in grp)
        x1 = max(rules[k][2] for k in grp)
        y_top = rules[grp[0]][3]
        y_bot = rules[grp[-1]][1]
        if y_top - y_bot < 1.8 * body_size:
            continue

        inside = [
            b
            for b in page_blocks
            if y_bot - 2.0 <= 0.5 * (b["bbox"][1] + b["bbox"][3]) <= y_top + 2.0
            and min(b["bbox"][2], x1) > max(b["bbox"][0], x0)
        ]
        if not inside:
            continue
        if any(_ALGO_RE.match(b["text"].strip()) for b in inside):
            continue

        # Count visual rows & multi-segment rows inside the rule pair
        all_lns = [ln for b in inside for ln in b["lines"]]
        y_rows: list[list[list[float]]] = []
        for ln in sorted(all_lns, key=lambda z: -z[3]):
            cy = 0.5 * (ln[1] + ln[3])
            if y_rows and abs(cy - 0.5 * (y_rows[-1][0][1] + y_rows[-1][0][3])) < 0.5 * body_size:
                y_rows[-1].append(ln)
            else:
                y_rows.append([ln])
        multi_seg_rows = sum(1 for row in y_rows if len(row) >= 2)
        avg_words = sum(len(re.findall(r"[A-Za-z]{3,}", b["text"])) for b in inside) / max(1, len(y_rows))
        has_long_prose = any(
            len(b["lines"]) >= 3
            and len(re.findall(r"[A-Za-z]{3,}", b["text"])) >= 25
            and (len(re.findall(r"[A-Za-z]{3,}", b["text"])) / max(1, len(b["lines"]))) >= 7.5
            and b["size"] >= 0.92 * body_size
            for b in inside
        )

        if len(grp) >= 3 or (
            not has_long_prose
            and ((multi_seg_rows >= 2 and avg_words <= 10.0) or (len(y_rows) >= 2 and avg_words <= 8.0))
        ):
            ext_top = y_top + 2.0
            ext_bot = y_bot - 2.0

            # Attach 1 aligned unruled header row directly above y_top (e.g. IEEE/CVPR tables)
            cap_above = [
                cb
                for cb in table_caps
                if y_top - 2.0 <= cb["bbox"][1] <= y_top + 55.0
                and min(cb["bbox"][2], x1 + 40.0) > max(cb["bbox"][0], x0 - 40.0)
            ]
            if cap_above:
                cap_bot = min(cb["bbox"][1] for cb in cap_above)
                hdr_blks = [
                    b
                    for b in page_blocks
                    if y_top < 0.5 * (b["bbox"][1] + b["bbox"][3]) < cap_bot - 1.0
                    and not _CAPTION_RE.match(b["text"].strip())
                    and min(b["bbox"][2], x1 + 10.0) > max(b["bbox"][0], x0 - 10.0)
                    and b["size"] <= body_size + 0.5
                    and len(b["lines"]) <= 2
                    and len(b["text"]) <= 80
                ]
                if hdr_blks:
                    ext_top = max(ext_top, max(b["bbox"][3] for b in hdr_blks) + 1.0)
            else:
                # Even without a caption right above, attach 1 short aligned unruled header row within 2*body_size
                hdr_cand = [
                    b
                    for b in page_blocks
                    if y_top <= b["bbox"][1] <= y_top + 1.8 * body_size
                    and not _CAPTION_RE.match(b["text"].strip())
                    and len(b["lines"]) == 1
                    and b["bbox"][0] >= x0 - 8.0
                    and b["bbox"][2] <= x1 + 8.0
                    and b["size"] <= body_size
                    and len(re.findall(r"[A-Za-z]{3,}", b["text"])) <= 6
                    and not b["text"].strip().endswith(".")
                ]
                if hdr_cand:
                    ext_top = max(ext_top, max(b["bbox"][3] for b in hdr_cand) + 1.0)

            # Attach 1 aligned unruled footer/summary row directly below y_bot
            ftr_cand = [
                b
                for b in page_blocks
                if y_bot - 1.8 * body_size <= b["bbox"][3] <= y_bot + 1.0
                and not _CAPTION_RE.match(b["text"].strip())
                and len(b["lines"]) == 1
                and b["bbox"][0] >= x0 - 8.0
                and b["bbox"][2] <= x1 + 8.0
                and b["size"] <= body_size
                and (
                    any(ch.isdigit() for ch in b["text"])
                    or len(re.findall(r"[A-Za-z]{3,}", b["text"])) <= 4
                )
                and not b["text"].strip().endswith(".")
            ]
            if ftr_cand:
                ext_bot = min(ext_bot, min(b["bbox"][1] for b in ftr_cand) - 1.0)

            table_zones.append([x0 - 4.0, ext_bot, x1 + 4.0, ext_top])
            used.update(grp)

    return table_zones, algo_zones


def _detect_borderless_table_blocks(
    page_blocks: list[dict[str, Any]],
    body_size: float,
) -> set[str]:
    """Detect borderless tables by column alignment among short single-line blocks."""
    cands = [
        b
        for b in page_blocks
        if b["kind"] == "paragraph"
        and len(b["lines"]) == 1
        and len(b["text"].strip()) <= 45
        and not _CAPTION_RE.match(b["text"].strip())
        and not b["text"].strip().endswith((".", "?", "!"))
    ]
    if len(cands) < 6:
        return set()

    # Group candidates into visual rows by baseline/center y
    rows: list[list[dict[str, Any]]] = []
    for b in sorted(cands, key=lambda z: (-0.5 * (z["bbox"][1] + z["bbox"][3]), z["bbox"][0])):
        cy = 0.5 * (b["bbox"][1] + b["bbox"][3])
        if rows and abs(cy - 0.5 * (rows[-1][0]["bbox"][1] + rows[-1][0]["bbox"][3])) <= 0.55 * body_size:
            rows[-1].append(b)
        else:
            rows.append([b])

    multi_rows = [r for r in rows if len(r) >= 2]
    if len(multi_rows) < 3:
        return set()

    # Group vertically contiguous multi-cell rows into borderless table clusters
    marked: set[str] = set()
    cluster: list[list[dict[str, Any]]] = [multi_rows[0]]

    def _flush_cluster(cl: list[list[dict[str, Any]]]) -> None:
        if len(cl) < 3:
            return
        cells = [b for r in cl for b in r]
        # Require at least some numeric/short tabular content (not 2-col prose)
        num_cells = sum(1 for b in cells if re.search(r"\d", b["text"]))
        avg_words = sum(len(re.findall(r"[A-Za-z]{3,}", b["text"])) for b in cells) / len(cells)
        if num_cells >= 0.30 * len(cells) and avg_words <= 3.5:
            for b in cells:
                marked.add(b["id"])

    for r in multi_rows[1:]:
        prev_y = min(b["bbox"][1] for b in cluster[-1])
        cur_y = max(b["bbox"][3] for b in r)
        if prev_y - cur_y <= 2.8 * body_size:
            cluster.append(r)
        else:
            _flush_cluster(cluster)
            cluster = [r]
    _flush_cluster(cluster)
    return marked


def _split_bold_lead_heading_blocks(
    blocks: list[dict[str, Any]],
    block_chars: dict[str, list[dict[str, Any]]],
    body_size: float,
) -> list[dict[str, Any]]:
    """Split blocks whose first line is a standalone all-bold heading followed by normal body lines."""
    out: list[dict[str, Any]] = []
    for b in blocks:
        if len(b["lines"]) < 2 or _CAPTION_RE.match(b["text"].strip()) or _ALGO_RE.match(b["text"].strip()):
            out.append(b)
            continue
        bchars = block_chars.get(b["id"], [])
        if not bchars:
            out.append(b)
            continue
        line_cs = _assign_chars_to_lines(bchars, b["lines"])
        if len(line_cs) < 2:
            out.append(b)
            continue
        l0 = _line_stats(line_cs[0])
        l1 = _line_stats(line_cs[1])
        t0 = l0["text"].strip()
        if (
            l0["bold_frac"] >= 0.85
            and l1["bold_frac"] < 0.30
            and 2 <= len(t0) <= 90
            and len(t0.split()) <= 12
            and not _NON_HEAD_RE.match(t0)
            and not _FRONT_HINT_RE.search(t0)
            and not _KEYWORDS_START_RE.match(t0)
            and not t0.endswith((",", ";", "?", "!"))
            and abs(l0["size"] - body_size) <= 2.5
        ):
            # Split into two blocks cleanly partitioning all chars (R4)
            head_cs = line_cs[0]
            rest_cs = [c for lcs in line_cs[1:] for c in lcs]
            head_id = f"{b['id']}a"
            rest_id = f"{b['id']}b"
            for c in head_cs:
                c["block"] = head_id
            for c in rest_cs:
                c["block"] = rest_id
            block_chars[head_id] = head_cs
            block_chars[rest_id] = rest_cs
            del block_chars[b["id"]]

            def _make_sub(
                sub_id: str, sub_cs: list[dict[str, Any]], sub_lines: list[list[float]]
            ) -> dict[str, Any]:
                return {
                    "id": sub_id,
                    "page": b["page"],
                    "bbox": [
                        _r2(min(c["bbox"][0] for c in sub_cs)),
                        _r2(min(c["bbox"][1] for c in sub_cs)),
                        _r2(max(c["bbox"][2] for c in sub_cs)),
                        _r2(max(c["bbox"][3] for c in sub_cs)),
                    ],
                    "lines": sub_lines,
                    "chars": _coalesce_ranges([(c["i"], _char_end_i(c)) for c in sub_cs]),
                    "text": normalise(sub_cs),
                    "font": Counter(c["font"] for c in sub_cs).most_common(1)[0][0],
                    "size": _r2(float(st.median(c["size"] for c in sub_cs))),
                    "bold": (sum(1 for c in sub_cs if c["bold"]) / len(sub_cs)) >= 0.65,
                    "italic": (sum(1 for c in sub_cs if c["italic"]) / len(sub_cs)) >= 0.65,
                    "color": list(Counter(tuple(c["color"]) for c in sub_cs).most_common(1)[0][0]),
                    "align": b["align"],
                    "column": b["column"],
                    "order": b["order"],
                    "kind": None,
                    "confidence": None,
                    "translate": None,
                    "segment": None,
                    "box": None,
                }

            out.append(_make_sub(head_id, head_cs, [b["lines"][0]]))
            out.append(_make_sub(rest_id, rest_cs, b["lines"][1:]))
        else:
            out.append(b)

    # Reassign sequential global order
    for idx, blk in enumerate(out):
        blk["order"] = idx
    return out


_SAFE_PROSE_PUNCT = frozenset(" .,;:!?'\"-()[]/&+%\u0002\u00ad\u2010\u2013\u2014\u2018\u2019\u201c\u201d°")
_MATH_OP_CHARS = frozenset("=<>≤≥∑∫√∈±×∂∇^_{}\\")


def _is_safe_text_char(c: dict[str, Any], line_sz: float, line_base: float) -> bool:
    """Return True if c is an ordinary text-font character (not math/mono/symbol/PUA/sub-sup)."""
    fn = c["font"]
    if not fn or _is_math_font(fn) or _is_mono_font(fn) or _NON_TEXT_FONT_RE.search(fn):
        return False
    u = c["u"]
    if not u or _is_math_unicode(u) or u in _MATH_OP_CHARS:
        return False
    # Reject sub/superscripts
    if c["size"] < 0.82 * line_sz and abs(c["origin"][1] - line_base) >= 0.12 * line_sz:
        return False
    for ch in u:
        cp = ord(ch)
        if cp == 0xFFFD or 0xE000 <= cp <= 0xF8FF or cp >= 0xF0000:
            return False
        if cp < 32 and ch not in _SOFT_HYPHENS:
            return False
        if ch in "ðþÞ":  # common custom-encoding artifacts in Type1 math/symbol fonts
            return False
        if ch.isascii():
            if not (ch.isalnum() or ch in _SAFE_PROSE_PUNCT):
                return False
        elif ch not in _SAFE_PROSE_PUNCT:
            if not (ch.isalpha() and cp <= 0x024F):
                return False
    return True


def _is_text_font_prose_run(
    cs: list[dict[str, Any]],
    i: int,
    j: int,
    claimed: list[bool],
    allowed_families: set[str],
    line_sz: float,
    line_base: float,
) -> bool:
    """Return True if a foreign-font run cs[i:j] is ordinary text-font prose (ligature,
    small-caps, bold/italic/sans variant, or run-in heading/label) rather than a symbol/code/math run."""
    sp_cs = cs[i:j]
    if not sp_cs or not all(_is_safe_text_char(c, line_sz, line_base) for c in sp_cs):
        return False

    n = len(cs)
    # Case 1: Word-internal ligature or small-caps run (e.g. 'ff'/'ffi' in rtxr, 'bstract' in rtxsc)
    # directly attached without a word-space gap to unclaimed allowed-family letters.
    if all(c["u"].isalpha() or c["u"] in _SOFT_HYPHENS for c in sp_cs):
        left_len = 0
        k = i - 1
        while (
            k >= 0
            and not claimed[k]
            and cs[k]["u"].isalpha()
            and _font_family(cs[k]["font"]) in allowed_families
            and cs[k + 1]["bbox"][0] - cs[k]["bbox"][2] <= 0.17 * max(cs[k]["size"], cs[k + 1]["size"])
        ):
            left_len += 1
            k -= 1
        right_len = 0
        k = j
        while (
            k < n
            and not claimed[k]
            and cs[k]["u"].isalpha()
            and _font_family(cs[k]["font"]) in allowed_families
            and cs[k]["bbox"][0] - cs[k - 1]["bbox"][2] <= 0.17 * max(cs[k]["size"], cs[k - 1]["size"])
        ):
            right_len += 1
            k += 1
        if (left_len > 0 or right_len > 0) and (left_len + (j - i) + right_len) >= 3:
            return True

    # Case 2: Standalone text-font prose / heading / label run (e.g. 'ABSTRACT', 'A. RESEARCH OBJECTIVE',
    # 'Abstract.', 'Figure 1.', 'Materials and methods', 'Results', 'Anchors', 'Loss Function')
    raw = normalise(sp_cs)
    if not raw or not re.search(r"[A-Za-z]{3,}", raw):
        return False
    # Reject single-token code/identifier patterns (CamelCase, underscores, internal dots/brackets/digits)
    if " " not in raw:
        core = raw.strip(".,;:!?\"'“”‘’")
        if not core:
            return False
        if re.search(r"[a-z][A-Z]", core):  # CamelCase identifier (e.g. FedAvg, CamSpec)
            return False
        if re.search(r"[_()\[\]{}/\\]", core):
            return False
        if "." in core or any(ch.isdigit() for ch in core):
            return False
    return True


# Glyph evidence flag bits stored in seg["cmap"]
CMAP_FLAG_ITALIC = 1
CMAP_FLAG_MATH_FONT = 2
CMAP_FLAG_NON_BODY_FONT = 4


def build_math_ph(
    raw: str,
    page: int,
    char_ranges: list[list[int]],
    bboxes: list[list[float]],
    baseline: float,
    link: int | None = None,
) -> dict[str, Any]:
    """Build a math placeholder dict with bbox, baseline, and depth (shared by classify and reconcile)."""
    sx0 = min(b[0] for b in bboxes)
    sy0 = min(b[1] for b in bboxes)
    sx1 = max(b[2] for b in bboxes)
    sy1 = max(b[3] for b in bboxes)
    return {
        "raw": raw,
        "page": page,
        "chars": char_ranges,
        "bbox": [_r2(sx0), _r2(sy0), _r2(sx1), _r2(sy1)],
        "baseline": _r2(baseline),
        "depth": _r2(max(0.0, baseline - sy0)),
        "link": link,
    }


def _collapse_ws_with_map(
    chars: list[str],
    owners: list[tuple[int, ...]],
    hyphens: list[tuple[int, int]],
) -> tuple[str, list[tuple[int, ...]], list[tuple[int, int]]]:
    """Apply ``_WS_RE.sub(' ', ''.join(chars)).strip()`` while preserving per-char owners and hyphen positions."""
    n = len(chars)
    out_chars: list[str] = []
    out_owners: list[tuple[int, ...]] = []
    # Map each boundary position i in [0..n] to its index in out_chars
    pos_map = [0] * (n + 1)
    i = 0
    # Skip leading whitespace
    while i < n and chars[i].isspace():
        pos_map[i] = 0
        i += 1
    in_ws = False
    while i < n:
        ch = chars[i]
        if ch.isspace():
            pos_map[i] = len(out_chars)
            if not in_ws:
                out_chars.append(" ")
                out_owners.append(())
                in_ws = True
        else:
            in_ws = False
            pos_map[i] = len(out_chars)
            out_chars.append(ch)
            out_owners.append(owners[i])
        i += 1
    # Strip trailing space if any
    if out_chars and out_chars[-1] == " ":
        out_chars.pop()
        out_owners.pop()
    end_pos = len(out_chars)
    pos_map[n] = end_pos
    for j in range(n - 1, -1, -1):
        if pos_map[j] > end_pos:
            pos_map[j] = end_pos
        else:
            break
    out_hyphens = [(pos_map[min(max(p, 0), n)], gid) for p, gid in hyphens]
    return "".join(out_chars), out_owners, out_hyphens


def _normalise_with_map(
    chars: list[dict[str, Any]],
    gids: list[int],
) -> tuple[str, list[tuple[int, ...]], list[tuple[int, int]]]:
    """Mirror ``extract.normalise(chars)`` while returning per-char glyph owners and detached hyphen positions."""
    if not chars:
        return "", [], []

    # 1. Partition into visual lines (identical to extract.normalise)
    lines: list[list[tuple[dict[str, Any], int]]] = [[(chars[0], gids[0])]]
    for cur, gid in zip(chars[1:], gids[1:]):
        prev = lines[-1][-1][0]
        sz = max(prev["size"], cur["size"], 1.0)
        dy = prev["origin"][1] - cur["origin"][1]
        if dy > 0.45 * sz or (cur["origin"][0] < prev["origin"][0] - 2.0 * sz and abs(dy) > 0.25 * sz):
            lines.append([(cur, gid)])
        else:
            lines[-1].append((cur, gid))

    # 2. Assemble lines with hyphen de-wrapping (identical to extract.normalise)
    parts_c: list[list[str]] = []
    parts_o: list[list[tuple[int, ...]]] = []
    parts_h: list[list[tuple[int, int]]] = []

    for ln in lines:
        lc: list[str] = []
        lo: list[tuple[int, ...]] = []
        prev_c: dict[str, Any] | None = None
        for cur, gid in ln:
            ch = cur["u"]
            if prev_c is not None:
                sz = max(prev_c["size"], cur["size"])
                gap = cur["bbox"][0] - prev_c["bbox"][2]
                mono = _is_mono_font(prev_c["font"]) and _is_mono_font(cur["font"])
                thresh = 0.25 * sz if mono else 0.17 * sz
                if gap > thresh and prev_c["u"] not in _SOFT_HYPHENS and ch not in _SOFT_HYPHENS:
                    lc.append(" ")
                    lo.append(())
            lc.append(ch)
            lo.append((gid,))
            prev_c = cur

        # Strip leading/trailing whitespace
        s_i = 0
        e_i = len(lc)
        while s_i < e_i and lc[s_i].isspace():
            s_i += 1
        while e_i > s_i and lc[e_i - 1].isspace():
            e_i -= 1
        if s_i >= e_i:
            continue
        lc = lc[s_i:e_i]
        lo = lo[s_i:e_i]

        # Strip internal soft hyphens not at line end
        if len(lc) > 1:
            fc: list[str] = []
            fo: list[tuple[int, ...]] = []
            for ch, ow in zip(lc[:-1], lo[:-1]):
                if ch not in ("\u0002", "\u00ad"):
                    fc.append(ch)
                    fo.append(ow)
            fc.append(lc[-1])
            fo.append(lo[-1])
            lc, lo = fc, fo

        if not parts_c:
            parts_c.append(lc)
            parts_o.append(lo)
            parts_h.append([])
            continue

        pc = parts_c[-1]
        po = parts_o[-1]
        ph_list = parts_h[-1]
        if pc and pc[-1] in _SOFT_HYPHENS:
            pc.pop()
            h_ow = po.pop()
            join_pos = len(pc)
            for hgid in h_ow:
                ph_list.append((join_pos, hgid))
            pc.extend(lc)
            po.extend(lo)
        elif (
            pc
            and pc[-1] in _LINE_HYPHENS
            and len(pc) >= 3
            and pc[-2].isalpha()
            and lc[0].islower()
        ):
            pc.pop()
            h_ow = po.pop()
            join_pos = len(pc)
            for hgid in h_ow:
                ph_list.append((join_pos, hgid))
            pc.extend(lc)
            po.extend(lo)
        else:
            parts_c.append(lc)
            parts_o.append(lo)
            parts_h.append([])

    if parts_c and parts_c[-1] and parts_c[-1][-1] in _SOFT_HYPHENS:
        parts_c[-1].pop()
        parts_o[-1].pop()

    # 3. Join parts with space and drop remaining soft hyphens
    jc: list[str] = []
    jo: list[tuple[int, ...]] = []
    jh: list[tuple[int, int]] = []
    for pc, po, ph_list in zip(parts_c, parts_o, parts_h):
        if not pc:
            continue
        if jc:
            jc.append(" ")
            jo.append(())
        base_off = len(jc)
        # Track position shift when dropping any remaining \u0002/\u00ad inside pc
        shift_map = [0] * (len(pc) + 1)
        cur_out = base_off
        for idx, (ch, ow) in enumerate(zip(pc, po)):
            shift_map[idx] = cur_out
            if ch not in ("\u0002", "\u00ad"):
                jc.append(ch)
                jo.append(ow)
                cur_out += 1
        shift_map[len(pc)] = cur_out
        for hp, hgid in ph_list:
            jh.append((shift_map[min(max(hp, 0), len(pc))], hgid))

    # 4. NFKC normalisation with offset tracking
    raw_joined = "".join(jc)
    full_nfkc = unicodedata.normalize("NFKC", raw_joined)
    nc: list[str] = []
    no: list[tuple[int, ...]] = []
    nfkc_pos_map = [0] * (len(jc) + 1)

    # Fast path: per-char NFKC equals full NFKC (true unless combining marks compose)
    per_char = [unicodedata.normalize("NFKC", ch) for ch in jc]
    if "".join(per_char) == full_nfkc:
        for idx, (s_norm, ow) in enumerate(zip(per_char, jo)):
            nfkc_pos_map[idx] = len(nc)
            for c_out in s_norm:
                nc.append(c_out)
                no.append(ow)
        nfkc_pos_map[len(jc)] = len(nc)
    else:
        # Cluster base char + following combining marks
        idx = 0
        m_len = len(jc)
        while idx < m_len:
            nfkc_pos_map[idx] = len(nc)
            end = idx + 1
            while end < m_len and unicodedata.combining(jc[end]) != 0:
                nfkc_pos_map[end] = len(nc)
                end += 1
            cluster_str = unicodedata.normalize("NFKC", "".join(jc[idx:end]))
            cluster_ow: list[int] = []
            for k in range(idx, end):
                for g in jo[k]:
                    if g not in cluster_ow:
                        cluster_ow.append(g)
            ow_t = tuple(cluster_ow)
            for c_out in cluster_str:
                nc.append(c_out)
                no.append(ow_t)
            idx = end
        nfkc_pos_map[m_len] = len(nc)

    nh = [(nfkc_pos_map[min(max(hp, 0), len(jc))], hgid) for hp, hgid in jh]
    return _collapse_ws_with_map(nc, no, nh)


def _build_segment_placeholders_and_text(
    seg_blocks: list[dict[str, Any]],
    block_chars: dict[str, list[dict[str, Any]]],
    char_to_link: dict[tuple[int, int], int],
    body_font: str,
    body_family: str,
    absorbed_math_chars: set[tuple[int, int]] | None = None,
) -> tuple[str, dict[str, dict[str, Any]], int, list[list[Any]]]:
    """Build segment text with ⟦mN⟧ / ⟦lN⟧ placeholders and return (text, ph_dict, r1_count, cmap).

    Uses extract.normalise() semantics for prose runs and line-end hyphen joining, while tracking
    exact text-offset -> PDFium glyph provenance in ``cmap``.
    """
    ph: dict[str, dict[str, Any]] = {}
    m_cnt = 0
    l_cnt = 0
    r1_count = 0

    # Per-glyph records for this segment: (pg, ci, ce, line_id, line_base, x0, y0, x1, y1, flags)
    glyph_table: list[tuple[int, int, int, int, float, float, float, float, float, int]] = []
    line_id_counter = 0

    # Assemble visual lines across all blocks in the segment
    line_outputs: list[tuple[list[str], list[tuple[int, ...]], list[tuple[int, int]]]] = []

    for blk in seg_blocks:
        pg = blk["page"]
        bchars = block_chars.get(blk["id"], [])
        if not bchars:
            continue
        blk_dom_family = _font_family(blk["font"])
        # Allowed prose families for R1 safety net: body family + block's own non-math/non-mono prose family
        allowed_families = {body_family}
        if blk_dom_family and not _is_math_font(blk["font"]) and not _is_mono_font(blk["font"]):
            allowed_families.add(blk_dom_family)

        line_cs_list = _assign_chars_to_lines(bchars, blk["lines"])
        for lcs in line_cs_list:
            ln = _line_stats(lcs)
            cs = ln["cs"]
            n = len(cs)
            if n == 0:
                continue
            line_base = ln["base"]
            line_sz = ln["size"]
            cur_line_id = line_id_counter
            line_id_counter += 1

            # Register glyphs for this line in glyph_table
            line_gids: list[int] = []
            for c in cs:
                c_fam = _font_family(c["font"])
                flags = 0
                if c.get("italic"):
                    flags |= CMAP_FLAG_ITALIC
                if _is_math_font(c["font"]):
                    flags |= CMAP_FLAG_MATH_FONT
                if c_fam and c_fam not in allowed_families:
                    flags |= CMAP_FLAG_NON_BODY_FONT
                bb = c["bbox"]
                gid = len(glyph_table)
                glyph_table.append(
                    (
                        pg,
                        c["i"],
                        _char_end_i(c),
                        cur_line_id,
                        _r2(line_base),
                        _r2(bb[0]),
                        _r2(bb[1]),
                        _r2(bb[2]),
                        _r2(bb[3]),
                        flags,
                    )
                )
                line_gids.append(gid)

            # Precompute math seed mask on this line
            math_seeds = [
                _char_is_math_seed(
                    cs, i, line_sz, line_base, ln["bold_frac"], ln["ital_frac"], body_font, body_family
                )
                for i in range(n)
            ]

            # 1. Link spans (highest precedence)
            # tag[i] = None | ('l', span_id) | ('m', span_id) | ('r1', span_id)
            spans: list[tuple[int, int, str, int | None]] = []  # (a, b, kind: 'l'|'m'|'r1', link_idx)
            claimed = [False] * n

            i = 0
            while i < n:
                lk = char_to_link.get((pg, cs[i]["i"]))
                if lk is None:
                    i += 1
                    continue
                j = i + 1
                while j < n and char_to_link.get((pg, cs[j]["i"])) == lk:
                    j += 1
                has_math = any(math_seeds[k] for k in range(i, j))
                spans.append((i, j, "m" if has_math else "l", lk))
                for k in range(i, j):
                    claimed[k] = True
                i = j

            # 2. Inline math spans (unclaimed chars only)
            for a, b in _detect_inline_math_indices(
                ln,
                body_font,
                body_family,
                page_idx=pg,
                absorbed_math_chars=absorbed_math_chars,
            ):
                # Split around any already-claimed link chars
                cur = a
                while cur < b:
                    while cur < b and claimed[cur]:
                        cur += 1
                    if cur >= b:
                        break
                    end = cur
                    while end < b and not claimed[end]:
                        end += 1
                    spans.append((cur, end, "m", None))
                    for k in range(cur, end):
                        claimed[k] = True
                    cur = end

            # 3. R1 safety net: any remaining char outside allowed prose font families
            # Skip URLs in monospace so plain text URLs aren't broken if unlinked
            line_txt = ln["text"]
            is_url_line = bool(_URL_RE.search(line_txt))
            i = 0
            while i < n:
                if claimed[i]:
                    i += 1
                    continue
                cfam = _font_family(cs[i]["font"])
                is_foreign = bool(cfam) and cfam not in allowed_families and not (
                    is_url_line and _is_mono_font(cs[i]["font"])
                )
                if not is_foreign:
                    i += 1
                    continue
                j = i + 1
                i_mono = _is_mono_font(cs[i]["font"])
                i_math = _is_math_font(cs[i]["font"])
                while j < n and not claimed[j]:
                    nfam = _font_family(cs[j]["font"])
                    gap = cs[j]["bbox"][0] - cs[j - 1]["bbox"][2]
                    if (
                        bool(nfam)
                        and nfam not in allowed_families
                        and not (is_url_line and _is_mono_font(cs[j]["font"]))
                        and _is_mono_font(cs[j]["font"]) == i_mono
                        and _is_math_font(cs[j]["font"]) == i_math
                        and gap <= 0.8 * line_sz
                    ):
                        j += 1
                    else:
                        break
                if not _is_text_font_prose_run(cs, i, j, claimed, allowed_families, line_sz, line_base):
                    spans.append((i, j, "r1", None))
                    for k in range(i, j):
                        claimed[k] = True
                i = j

            spans.sort(key=lambda sp: sp[0])

            # Merge directly adjacent unlinked math/R1 placeholders on the same line
            if len(spans) >= 2:
                merged_spans: list[tuple[int, int, str, int | None]] = [spans[0]]
                for a, b, sp_kind, lk_idx in spans[1:]:
                    pa, pb, pkind, plk = merged_spans[-1]
                    if (
                        pb == a
                        and plk is None
                        and lk_idx is None
                        and pkind in ("m", "r1")
                        and sp_kind in ("m", "r1")
                        and cs[a]["bbox"][0] - cs[pb - 1]["bbox"][2] <= 0.8 * line_sz
                    ):
                        merged_kind = "m" if (pkind == "m" or sp_kind == "m") else "r1"
                        merged_spans[-1] = (pa, b, merged_kind, None)
                    else:
                        merged_spans.append((a, b, sp_kind, lk_idx))
                spans = merged_spans

            # Emit line text by interleaving normalise(prose_run) and placeholders
            line_c: list[str] = []
            line_o: list[tuple[int, ...]] = []
            line_h: list[tuple[int, int]] = []
            cursor = 0

            def _need_space(prev_c: dict[str, Any], next_c: dict[str, Any]) -> bool:
                sz = max(prev_c["size"], next_c["size"])
                gap = next_c["bbox"][0] - prev_c["bbox"][2]
                return gap > 0.17 * sz and prev_c["u"] not in _SOFT_HYPHENS and next_c["u"] not in _SOFT_HYPHENS

            for a, b, sp_kind, lk_idx in spans:
                if cursor < a:
                    prose_cs = cs[cursor:a]
                    prose_gids = line_gids[cursor:a]
                    ptxt, pown, phyp = _normalise_with_map(prose_cs, prose_gids)
                    if ptxt:
                        if line_c and cursor > 0 and _need_space(cs[cursor - 1], prose_cs[0]):
                            line_c.append(" ")
                            line_o.append(())
                        base_off = len(line_c)
                        line_c.extend(ptxt)
                        line_o.extend(pown)
                        for hp, hgid in phyp:
                            line_h.append((base_off + hp, hgid))
                sp_cs = cs[a:b]
                sp_raw = normalise(sp_cs)
                sp_ranges = _coalesce_ranges([(c["i"], _char_end_i(c)) for c in sp_cs])
                if sp_kind == "l":
                    l_cnt += 1
                    code = f"l{l_cnt}"
                    ph[code] = {
                        "raw": sp_raw,
                        "page": pg,
                        "chars": sp_ranges,
                        "link": lk_idx,
                    }
                else:
                    m_cnt += 1
                    code = f"m{m_cnt}"
                    if sp_kind == "r1":
                        r1_count += 1
                    ph[code] = build_math_ph(
                        sp_raw,
                        pg,
                        sp_ranges,
                        [c["bbox"] for c in sp_cs],
                        line_base,
                        lk_idx,
                    )
                    # glyph evidence for the "?" flag: longest plain body-font prose word held by this placeholder
                    ph[code]["pw"] = max(
                        (
                            e - k
                            for k, e in _plain_prose_words(
                                cs, a, b, math_seeds, set(), line_sz, line_base, body_family, ln["ital_frac"] >= 0.72,
                                any_text_font=True,
                            )
                        ),
                        default=0,
                    )
                if line_c and a > 0 and _need_space(cs[a - 1], sp_cs[0]):
                    line_c.append(" ")
                    line_o.append(())
                tok = f"⟦{code}⟧"
                line_c.extend(tok)
                line_o.extend([()] * len(tok))
                cursor = b

            if cursor < n:
                prose_cs = cs[cursor:n]
                prose_gids = line_gids[cursor:n]
                # Preserve trailing soft hyphen for cross-line joining below
                has_trail_soft = prose_cs[-1]["u"] in _SOFT_HYPHENS
                ptxt, pown, phyp = _normalise_with_map(prose_cs, prose_gids)
                if ptxt:
                    if line_c and cursor > 0 and _need_space(cs[cursor - 1], prose_cs[0]):
                        line_c.append(" ")
                        line_o.append(())
                    base_off = len(line_c)
                    line_c.extend(ptxt)
                    line_o.extend(pown)
                    for hp, hgid in phyp:
                        line_h.append((base_off + hp, hgid))
                if has_trail_soft:
                    line_c.append("\u0002")
                    line_o.append((prose_gids[-1],))

            # Strip leading/trailing whitespace on this line
            ls = 0
            le = len(line_c)
            while ls < le and line_c[ls].isspace():
                ls += 1
            while le > ls and line_c[le - 1].isspace():
                le -= 1
            if ls < le:
                adj_h = [(min(max(hp - ls, 0), le - ls), hgid) for hp, hgid in line_h]
                line_outputs.append((line_c[ls:le], line_o[ls:le], adj_h))

    # Join lines with hyphen de-wrapping (matching extract.normalise)
    parts_c: list[list[str]] = []
    parts_o: list[list[tuple[int, ...]]] = []
    parts_h: list[list[tuple[int, int]]] = []
    for raw_c, raw_o, raw_h in line_outputs:
        if not parts_c:
            parts_c.append(list(raw_c))
            parts_o.append(list(raw_o))
            parts_h.append(list(raw_h))
            continue
        pc = parts_c[-1]
        po = parts_o[-1]
        ph_list = parts_h[-1]
        if pc and pc[-1] in _SOFT_HYPHENS:
            pc.pop()
            h_ow = po.pop()
            join_pos = len(pc)
            for hgid in h_ow:
                ph_list.append((join_pos, hgid))
            for hp, hgid in raw_h:
                ph_list.append((join_pos + hp, hgid))
            pc.extend(raw_c)
            po.extend(raw_o)
        elif (
            pc
            and pc[-1] in _LINE_HYPHENS
            and len(pc) >= 3
            and pc[-2].isalpha()
            and raw_c
            and raw_c[0].islower()
        ):
            pc.pop()
            h_ow = po.pop()
            join_pos = len(pc)
            for hgid in h_ow:
                ph_list.append((join_pos, hgid))
            for hp, hgid in raw_h:
                ph_list.append((join_pos + hp, hgid))
            pc.extend(raw_c)
            po.extend(raw_o)
        else:
            parts_c.append(list(raw_c))
            parts_o.append(list(raw_o))
            parts_h.append(list(raw_h))

    if parts_c and parts_c[-1] and parts_c[-1][-1] in _SOFT_HYPHENS:
        parts_c[-1].pop()
        parts_o[-1].pop()

    # Final join, drop remaining soft hyphens, collapse whitespace
    fc: list[str] = []
    fo: list[tuple[int, ...]] = []
    fh: list[tuple[int, int]] = []
    for pc, po, ph_list in zip(parts_c, parts_o, parts_h):
        if not pc:
            continue
        if fc:
            fc.append(" ")
            fo.append(())
        base_off = len(fc)
        shift_map = [0] * (len(pc) + 1)
        cur_out = base_off
        for idx, (ch, ow) in enumerate(zip(pc, po)):
            shift_map[idx] = cur_out
            if ch not in ("\u0002", "\u00ad"):
                fc.append(ch)
                fo.append(ow)
                cur_out += 1
        shift_map[len(pc)] = cur_out
        for hp, hgid in ph_list:
            fh.append((shift_map[min(max(hp, 0), len(pc))], hgid))

    seg_text, final_o, final_h = _collapse_ws_with_map(fc, fo, fh)

    # Build compact cmap runs grouped by (page, line_id)
    # Collect text span [gt0, gt1) per gid
    gid_span: dict[int, list[int]] = {}
    for pos, ow in enumerate(final_o):
        for gid in ow:
            sp = gid_span.get(gid)
            if sp is None:
                gid_span[gid] = [pos, pos + 1]
            else:
                if pos < sp[0]:
                    sp[0] = pos
                if pos + 1 > sp[1]:
                    sp[1] = pos + 1
    for hp, hgid in final_h:
        if hgid not in gid_span:
            gid_span[hgid] = [hp, hp]

    # Group glyph entries by (page, line_id) in text order
    by_line: dict[tuple[int, int], list[tuple[int, int, int]]] = {}
    for gid, (gt0, gt1) in gid_span.items():
        pg, _ci, _ce, lid, _base, *_rest = glyph_table[gid]
        by_line.setdefault((pg, lid), []).append((gt0, gt1, gid))

    cmap: list[list[Any]] = []
    ordered_runs = sorted(
        by_line.items(),
        key=lambda kv: (min(x[0] for x in kv[1]), min(x[1] for x in kv[1]), kv[0][0], kv[0][1]),
    )
    for (pg, lid), entries in ordered_runs:
        entries.sort(key=lambda x: (x[0], x[1], glyph_table[x[2]][1]))
        t0 = min(x[0] for x in entries)
        t1 = max(x[1] for x in entries)
        min_ci = min(glyph_table[x[2]][1] for x in entries)
        max_ce = max(glyph_table[x[2]][2] for x in entries)
        line_base = glyph_table[entries[0][2]][4]
        glyph_str = ";".join(
            f"{gt0 - t0},{gt1 - t0},{glyph_table[gid][1]},{glyph_table[gid][2]},"
            f"{glyph_table[gid][5]},{glyph_table[gid][6]},{glyph_table[gid][7]},{glyph_table[gid][8]},"
            f"{glyph_table[gid][9]}"
            for gt0, gt1, gid in entries
        )
        cmap.append([t0, t1, pg, min_ci, max_ce, lid, line_base, glyph_str])

    return seg_text, ph, r1_count, cmap


_EQ_NUM_RE = re.compile(r"^\(\d+(?:\.\d+)*\)$")


def _is_absorbable_fragment(
    k: dict[str, Any],
    host: dict[str, Any],
    body_size: float,
) -> bool:
    """Return True if kept block k is a small inline fragment that can be safely absorbed into host."""
    kind = k["kind"]
    if kind in ("front_matter", "refs", "code"):
        return False
    ktxt = k["text"].strip()
    if _EQ_NUM_RE.match(ktxt):
        return False
    kb = k["bbox"]
    kw = kb[2] - kb[0]
    kh = kb[3] - kb[1]
    hw = max(1.0, host["bbox"][2] - host["bbox"][0])
    n_lines = len(k["lines"])

    if kind == "paragraph":
        return n_lines <= 2 and kw <= 90.0 and len(ktxt) <= 35
    if kind in ("table", "toc", "figure"):
        return (
            n_lines == 1
            and kw <= 40.0
            and kh <= 2.5 * body_size
            and len(ktxt) <= 12
            and len(re.findall(r"[A-Za-z]{3,}", ktxt)) == 0
        )
    if kind == "display_math":
        if n_lines == 1 and kw <= min(65.0, 0.30 * hw) and kh <= 3.5 * body_size:
            return True
        if n_lines == 2 and kw <= 40.0 and kh <= 3.0 * body_size:
            return True
        return False
    return False


def _try_split_stray_edge_line(
    host: dict[str, Any],
    k: dict[str, Any],
    block_chars: dict[str, list[dict[str, Any]]],
    used_ids: set[str],
) -> dict[str, Any] | None:
    """If host has >=2 lines and k overlaps only a narrow non-prose first/last line (e.g. equation number
    or fraction denominator), split that stray edge line off host into a new kept block.

    Updates host and block_chars in place and returns the new kept block, or None if not applicable.
    """
    lines = host["lines"]
    if len(lines) < 2:
        return None
    hchars = block_chars.get(host["id"], [])
    if not hchars:
        return None
    line_cs = _assign_chars_to_lines(hchars, lines)
    if len(line_cs) != len(lines):
        return None

    kb = k["bbox"]
    ka = _box_area(kb)
    hw = max(1.0, host["bbox"][2] - host["bbox"][0])
    max_stray_w = min(0.30 * hw, 70.0)

    def _union_bbox(lbs: list[list[float]]) -> list[float]:
        return [
            min(lb[0] for lb in lbs),
            min(lb[1] for lb in lbs),
            max(lb[2] for lb in lbs),
            max(lb[3] for lb in lbs),
        ]

    for edge in (0, -1):
        edge_lb = lines[edge]
        edge_w = edge_lb[2] - edge_lb[0]
        if edge_w >= max_stray_w:
            continue
        rest_lines = lines[1:] if edge == 0 else lines[:-1]
        rest_bb = _union_bbox(rest_lines)
        rest_ov = (_inter_area(kb, rest_bb) / ka) if ka > 0 else 0.0
        if rest_ov >= 0.15:
            continue
        # Edge line must actually be the one overlapping k vertically
        if min(kb[3], edge_lb[3]) - max(kb[1], edge_lb[1]) <= 0.0:
            continue
        edge_cs = line_cs[edge]
        rest_cs = [c for lcs in (line_cs[1:] if edge == 0 else line_cs[:-1]) for c in lcs]
        if not edge_cs or not rest_cs:
            continue
        edge_txt = normalise(edge_cs).strip()
        prose_w = len(re.findall(r"[A-Za-z]{3,}", edge_txt))
        is_eq_num = bool(_EQ_NUM_RE.match(edge_txt))
        if not is_eq_num and prose_w > 2:
            continue
        if edge == -1 and not is_eq_num and re.search(r"[.?!]$", edge_txt) and prose_w >= 1:
            continue

        # Split off the stray edge line
        base_id = f"{host['id']}s"
        new_id = base_id
        suf = 2
        while new_id in used_ids:
            new_id = f"{base_id}{suf}"
            suf += 1
        used_ids.add(new_id)

        for c in edge_cs:
            c["block"] = new_id
        block_chars[new_id] = edge_cs
        block_chars[host["id"]] = rest_cs

        host["lines"] = rest_lines
        host["bbox"] = [
            _r2(min(c["bbox"][0] for c in rest_cs)),
            _r2(min(c["bbox"][1] for c in rest_cs)),
            _r2(max(c["bbox"][2] for c in rest_cs)),
            _r2(max(c["bbox"][3] for c in rest_cs)),
        ]
        host["chars"] = _coalesce_ranges([(c["i"], _char_end_i(c)) for c in rest_cs])
        host["text"] = normalise(rest_cs)

        stray_blk: dict[str, Any] = {
            "id": new_id,
            "page": host["page"],
            "bbox": [
                _r2(min(c["bbox"][0] for c in edge_cs)),
                _r2(min(c["bbox"][1] for c in edge_cs)),
                _r2(max(c["bbox"][2] for c in edge_cs)),
                _r2(max(c["bbox"][3] for c in edge_cs)),
            ],
            "lines": [edge_lb],
            "chars": _coalesce_ranges([(c["i"], _char_end_i(c)) for c in edge_cs]),
            "text": edge_txt,
            "font": Counter(c["font"] for c in edge_cs).most_common(1)[0][0],
            "size": _r2(float(st.median(c["size"] for c in edge_cs))),
            "bold": (sum(1 for c in edge_cs if c["bold"]) / len(edge_cs)) >= 0.65,
            "italic": (sum(1 for c in edge_cs if c["italic"]) / len(edge_cs)) >= 0.65,
            "color": list(Counter(tuple(c["color"]) for c in edge_cs).most_common(1)[0][0]),
            "align": host["align"],
            "column": host["column"],
            "order": host["order"],
            "kind": k["kind"] if k["kind"] in KEPT else "display_math",
            "confidence": 0.90,
            "translate": False,
            "segment": None,
            "box": None,
        }
        return stray_blk
    return None


def _absorb_or_resolve_overlapping_kept_blocks(
    blocks: list[dict[str, Any]],
    block_chars: dict[str, list[dict[str, Any]]],
    kept: list[dict[str, str]],
    body_size: float,
    body_family: str,
) -> tuple[list[dict[str, Any]], list[dict[str, str]], set[tuple[int, int]]]:
    """Absorb small kept fragments overlapping translated blocks; split stray equation-number edge
    lines; demote host to kept (R1) if an overlapping kept block cannot be safely absorbed.

    Guarantees 0 kept blocks (except header/footer) overlap any translated block with >= 30% area.
    """
    absorbed_math_chars: set[tuple[int, int]] = set()
    used_ids = {b["id"] for b in blocks}
    removed_ids: set[str] = set()

    by_page: dict[int, list[dict[str, Any]]] = {}
    for b in blocks:
        by_page.setdefault(b["page"], []).append(b)

    for pg, p_blocks in by_page.items():
        for _it in range(12):
            changed = False
            trans = [b for b in p_blocks if b.get("translate")]
            if not trans:
                break
            kept_cands = [
                b for b in p_blocks if not b.get("translate") and b.get("kind") not in ("header", "footer")
            ]
            # Also allow absorbing tiny 1-line numeric/symbol superscripts misclassified as header/footer
            # when strictly contained inside a translated block (e.g. author affiliation numbers near top of page).
            tiny_hf_cands = [
                b
                for b in p_blocks
                if not b.get("translate")
                and b.get("kind") in ("header", "footer")
                and len(b["lines"]) == 1
                and len(b["text"].strip()) <= 4
                and bool(re.fullmatch(r"[\d,.*†‡§]+", b["text"].strip()))
            ]
            kept_cands.extend(tiny_hf_cands)
            if not kept_cands:
                break

            to_remove: set[str] = set()
            to_add: list[tuple[int, dict[str, Any]]] = []

            # Pass 1: Resolve non-absorbable overlapping kept blocks first (Step A split or Step C R1 demote)
            # so we never absorb small fragments into a host that will end up demoted to kept.
            non_abs_handled = False
            for k in kept_cands:
                kb = k["bbox"]
                ka = _box_area(kb)
                best_t: dict[str, Any] | None = None
                best_score = 0.0
                for t in trans:
                    if not t.get("translate"):
                        continue
                    tb = t["bbox"]
                    ov = (_inter_area(kb, tb) / ka) if ka > 0 else (1.0 if _box_overlaps(kb, tb) else 0.0)
                    if ov >= 0.30 and ov > best_score:
                        best_score = ov
                        best_t = t
                if best_t is None or _is_absorbable_fragment(k, best_t, body_size):
                    continue
                stray = _try_split_stray_edge_line(best_t, k, block_chars, used_ids)
                if stray is not None:
                    idx = p_blocks.index(best_t)
                    to_add.append((idx, stray))
                    changed = True
                    non_abs_handled = True
                    break
                best_t["translate"] = False
                best_t["box"] = None
                kept.append({"block": best_t["id"], "reason": f"overlaps_kept({k['id']})"})
                changed = True
                non_abs_handled = True

            if non_abs_handled:
                if to_add:
                    for idx, sb in reversed(to_add):
                        p_blocks.insert(idx, sb)
                continue

            # Pass 2: Absorb remaining small fragments into surviving translated hosts
            for k in kept_cands:
                if k["id"] in to_remove:
                    continue
                kb = k["bbox"]
                ka = _box_area(kb)
                kw = kb[2] - kb[0]
                kh = kb[3] - kb[1]
                ktxt = k["text"].strip()

                best_t = None
                best_score = 0.0

                for t in trans:
                    if not t.get("translate"):
                        continue
                    tb = t["bbox"]
                    if ka > 0:
                        ov = _inter_area(kb, tb) / ka
                    else:
                        ov = 1.0 if _box_overlaps(kb, tb) else 0.0
                    if ov >= 0.30 and ov > best_score:
                        best_score = ov
                        best_t = t

                # Secondary check: tiny 1-line superscript/operator touching a line's left/right edge
                if (
                    best_t is None
                    and len(k["lines"]) == 1
                    and kw <= 25.0
                    and kh <= 2.5 * body_size
                    and len(ktxt) <= 8
                    and not _EQ_NUM_RE.match(ktxt)
                    and k["kind"] in ("table", "display_math", "paragraph")
                ):
                    kcy = 0.5 * (kb[1] + kb[3])
                    best_gap = 1e9
                    for t in trans:
                        if not t.get("translate"):
                            continue
                        if k["column"] is not None and t["column"] is not None and k["column"] != t["column"]:
                            continue
                        if not _is_absorbable_fragment(k, t, body_size):
                            continue
                        for lb in t["lines"]:
                            iy = max(0.0, min(kb[3], lb[3]) - max(kb[1], lb[1]))
                            if kh > 0 and iy / kh < 0.30:
                                continue
                            # Horizontal gap from line edge
                            if kb[0] >= lb[2]:
                                hgap = kb[0] - lb[2]
                            elif kb[2] <= lb[0]:
                                hgap = lb[0] - kb[2]
                            else:
                                hgap = 0.0
                            if hgap <= 0.4 * body_size:
                                lcy = 0.5 * (lb[1] + lb[3])
                                score_d = hgap + 0.1 * abs(kcy - lcy)
                                if score_d < best_gap:
                                    best_gap = score_d
                                    best_t = t

                if best_t is None:
                    continue

                if not _is_absorbable_fragment(k, best_t, body_size):
                    best_t["translate"] = False
                    best_t["box"] = None
                    kept.append({"block": best_t["id"], "reason": f"overlaps_kept({k['id']})"})
                    changed = True
                    continue

                # Step B: Absorb small fragment k into host best_t
                k_chars = block_chars.pop(k["id"], [])
                h_chars = block_chars.get(best_t["id"], [])
                if not k_chars:
                    to_remove.add(k["id"])
                    changed = True
                    continue

                orig_lines = best_t["lines"]
                buckets = _assign_chars_to_lines(h_chars, orig_lines)
                while len(buckets) < len(orig_lines):
                    buckets.append([])

                line_ys = [0.5 * (lb[1] + lb[3]) for lb in orig_lines]
                for c in k_chars:
                    c["block"] = best_t["id"]
                    cy = 0.5 * (c["bbox"][1] + c["bbox"][3])
                    best_li = 0
                    best_d = 1e9
                    for li, (lb, ly) in enumerate(zip(orig_lines, line_ys)):
                        in_y = lb[1] - 1.0 <= cy <= lb[3] + 1.0
                        d = abs(cy - ly) - (100.0 if in_y else 0.0)
                        if d < best_d:
                            best_d = d
                            best_li = li
                    buckets[best_li].append(c)

                new_lines: list[list[float]] = []
                ordered_cs: list[dict[str, Any]] = []
                for li, bk in enumerate(buckets):
                    if not bk:
                        continue
                    bk.sort(key=lambda c: (c["bbox"][0], c["i"]))
                    ordered_cs.extend(bk)
                    olb = orig_lines[li]
                    # Keep original line y0/y1 so tall absorbed operators (e.g. CMEX ∫)
                    # do not shift line vertical centers and steal sub/superscripts from neighbors.
                    new_lines.append(
                        [
                            _r2(min(olb[0], min(c["bbox"][0] for c in bk))),
                            _r2(olb[1]),
                            _r2(max(olb[2], max(c["bbox"][2] for c in bk))),
                            _r2(olb[3]),
                        ]
                    )

                block_chars[best_t["id"]] = ordered_cs
                best_t["lines"] = new_lines
                best_t["bbox"] = [
                    _r2(min(c["bbox"][0] for c in ordered_cs)),
                    _r2(min(c["bbox"][1] for c in ordered_cs)),
                    _r2(max(c["bbox"][2] for c in ordered_cs)),
                    _r2(max(c["bbox"][3] for c in ordered_cs)),
                ]
                best_t["chars"] = _coalesce_ranges([(c["i"], _char_end_i(c)) for c in ordered_cs])
                best_t["text"] = normalise(ordered_cs)

                # Mark non-prose absorbed fragments so they become ⟦mN⟧ placeholders
                is_body_prose = (
                    k["kind"] == "paragraph"
                    and bool(re.search(r"[A-Za-z]{2,}", ktxt))
                    and _font_family(k["font"]) == body_family
                )
                if not is_body_prose:
                    for c in k_chars:
                        absorbed_math_chars.add((pg, c["i"]))

                to_remove.add(k["id"])
                removed_ids.add(k["id"])
                changed = True

            if to_remove:
                p_blocks[:] = [b for b in p_blocks if b["id"] not in to_remove]
            if to_add:
                for idx, sb in reversed(to_add):
                    p_blocks.insert(idx, sb)
            if not changed:
                break

    # Reverse containment: demote any translated block whose original bbox lies substantially inside a kept block
    # (e.g. figure labels inside a figure block, table row labels inside a table, math subscripts inside display_math).
    for pg, p_blocks in by_page.items():
        kept_list = [b for b in p_blocks if not b.get("translate")]
        if not kept_list:
            continue
        for t in p_blocks:
            if not t.get("translate"):
                continue
            tb = t["bbox"]
            ta = _box_area(tb)
            if ta <= 0.0:
                continue
            for k in kept_list:
                inter = _inter_area(tb, k["bbox"])
                if inter > 1.0 and (inter / ta) >= 0.45:
                    t["translate"] = False
                    t["box"] = None
                    kept.append({"block": t["id"], "reason": f"contained_in_kept({k['id']})"})
                    break

    if removed_ids or any(len(pb) for pb in by_page.values()):
        new_blocks: list[dict[str, Any]] = []
        for pg in sorted(by_page):
            new_blocks.extend(by_page[pg])
        for idx, blk in enumerate(new_blocks):
            blk["order"] = idx
        blocks = new_blocks
        if removed_ids:
            kept = [entry for entry in kept if entry["block"] not in removed_ids]

    return blocks, kept, absorbed_math_chars


def _rebuild_block_from_chars(blk: dict[str, Any], cs: list[dict[str, Any]]) -> None:
    """Recompute a block's lines, bbox, chars, and text from its owned char list."""
    for c in cs:
        c["block"] = blk["id"]
    # Group chars into visual lines by baseline origin[1]
    ordered_by_y = sorted(cs, key=lambda c: (-c["origin"][1], c["bbox"][0], c["i"]))
    line_groups: list[dict[str, Any]] = []
    for c in ordered_by_y:
        cy = c["origin"][1]
        csz = max(1.0, c["size"])
        best = None
        best_d = 1e9
        for lg in line_groups:
            d = abs(lg["base"] - cy)
            tol = max(2.2, 0.42 * max(csz, lg["max_sz"]))
            if d <= tol and d < best_d:
                best = lg
                best_d = d
        if best is not None:
            best["cs"].append(c)
            if csz > best["max_sz"]:
                best["max_sz"] = csz
            if csz >= 0.85 * best["max_sz"]:
                best["base_sum"] += cy
                best["base_cnt"] += 1
                best["base"] = best["base_sum"] / best["base_cnt"]
        else:
            line_groups.append({"base": cy, "base_sum": cy, "base_cnt": 1, "max_sz": csz, "cs": [c]})

    line_groups.sort(key=lambda lg: -max(c["bbox"][3] for c in lg["cs"]))
    new_lines: list[list[float]] = []
    ordered_cs: list[dict[str, Any]] = []
    for lg in line_groups:
        lcs = sorted(lg["cs"], key=lambda c: (c["bbox"][0], c["i"]))
        ordered_cs.extend(lcs)
        new_lines.append(
            [
                _r2(min(c["bbox"][0] for c in lcs)),
                _r2(min(c["bbox"][1] for c in lcs)),
                _r2(max(c["bbox"][2] for c in lcs)),
                _r2(max(c["bbox"][3] for c in lcs)),
            ]
        )

    blk["lines"] = new_lines
    blk["bbox"] = [
        _r2(min(c["bbox"][0] for c in ordered_cs)),
        _r2(min(c["bbox"][1] for c in ordered_cs)),
        _r2(max(c["bbox"][2] for c in ordered_cs)),
        _r2(max(c["bbox"][3] for c in ordered_cs)),
    ]
    blk["chars"] = _coalesce_ranges([(c["i"], _char_end_i(c)) for c in ordered_cs])
    blk["text"] = normalise(ordered_cs)
    if ordered_cs:
        szs = [float(c["size"]) for c in ordered_cs]
        med_sz = float(st.median(szs))
        big = [s for s in szs if s > 1.4 * med_sz]
        non_drop = [s for s in szs if s <= 1.4 * med_sz] if (big and len(big) < 0.05 * len(szs)) else szs
        ref_max = max(non_drop) if non_drop else max(szs)
        main_szs = [s for s in (non_drop or szs) if s >= 0.82 * ref_max] or (non_drop or szs)
        blk["size"] = _r2(float(st.median(main_szs)))
    cs[:] = ordered_cs


def _resolve_contained_translated_blocks(
    blocks: list[dict[str, Any]],
    block_chars: dict[str, list[dict[str, Any]]],
    pages: list[dict[str, Any]],
    doc: dict[str, Any],
    kept: list[dict[str, str]],
) -> list[dict[str, Any]]:
    """Resolve translated blocks whose original bboxes overlap or enclose each other.

    - Splits gutter-straddling blocks on two-column pages when they overlap single-column blocks.
    - Merges contained translated fragments into their enclosing translated block when glyphs are disjoint.
    - Demotes superimposed/clipped-out conflicting blocks to kept (R1) when glyphs collide on top of each other.
    """
    used_ids = {b["id"] for b in blocks}
    two_col = bool(doc.get("two_col"))
    gutter_x = doc.get("gutter_x")

    by_page: dict[int, list[dict[str, Any]]] = {}
    for b in blocks:
        by_page.setdefault(b["page"], []).append(b)

    def _new_id(pg: int, base: str) -> str:
        n = 1
        while True:
            cand = f"{base}s{n}"
            if cand not in used_ids:
                used_ids.add(cand)
                return cand
            n += 1

    def _outside_crop(bb: list[float], cb: list[float]) -> bool:
        return bb[0] < cb[0] - 2.0 or bb[1] < cb[1] - 2.0 or bb[2] > cb[2] + 2.0 or bb[3] > cb[3] + 2.0

    for pg, p_blocks in by_page.items():
        cb = pages[pg]["cropbox"]

        # Pass A: Split gutter-straddling col=None translated blocks that overlap single-column blocks
        if two_col and gutter_x is not None:
            additions: list[tuple[int, dict[str, Any]]] = []
            for idx, b in enumerate(p_blocks):
                if not b.get("translate") or b.get("column") is not None:
                    continue
                bb = b["bbox"]
                if not (bb[0] < gutter_x - 8.0 and bb[2] > gutter_x + 8.0):
                    continue
                hits_col = any(
                    o is not b
                    and o.get("translate")
                    and o.get("column") in (0, 1)
                    and _inter_area(bb, o["bbox"]) > 1.0
                    for o in p_blocks
                )
                if not hits_col:
                    continue
                cs = block_chars.get(b["id"], [])
                left_cs = [c for c in cs if 0.5 * (c["bbox"][0] + c["bbox"][2]) < gutter_x]
                right_cs = [c for c in cs if 0.5 * (c["bbox"][0] + c["bbox"][2]) >= gutter_x]
                if not left_cs or not right_cs:
                    continue
                b["column"] = 0
                block_chars[b["id"]] = left_cs
                _rebuild_block_from_chars(b, block_chars[b["id"]])

                rid = _new_id(pg, b["id"])
                rblk = dict(b)
                rblk["id"] = rid
                rblk["column"] = 1
                block_chars[rid] = right_cs
                _rebuild_block_from_chars(rblk, block_chars[rid])
                additions.append((idx + 1, rblk))
            for idx, rblk in reversed(additions):
                p_blocks.insert(idx, rblk)

        # Pass B: Merge contained translated blocks or demote superimposed conflicts
        for _ in range(12):
            tr = [b for b in p_blocks if b.get("translate")]
            if len(tr) < 2:
                break
            merged_or_demoted = False
            for i in range(len(tr)):
                if merged_or_demoted:
                    break
                a = tr[i]
                for j in range(i + 1, len(tr)):
                    b = tr[j]
                    inter = _inter_area(a["bbox"], b["bbox"])
                    if inter <= 1.0:
                        continue
                    aa = max(1.0, _box_area(a["bbox"]))
                    ba = max(1.0, _box_area(b["bbox"]))
                    ov_small = inter / min(aa, ba)
                    if ov_small < 0.45:
                        continue

                    large, small = (a, b) if aa >= ba else (b, a)
                    lcs = block_chars.get(large["id"], [])
                    scs = block_chars.get(small["id"], [])
                    if not scs:
                        p_blocks.remove(small)
                        merged_or_demoted = True
                        break

                    # Check if small's glyphs literally sit on top of large's glyphs (superimposed clipped text)
                    glyph_hits = 0
                    for sc in scs:
                        cx = 0.5 * (sc["bbox"][0] + sc["bbox"][2])
                        cy = 0.5 * (sc["bbox"][1] + sc["bbox"][3])
                        if any(
                            lc["bbox"][0] - 0.5 <= cx <= lc["bbox"][2] + 0.5
                            and lc["bbox"][1] - 0.5 <= cy <= lc["bbox"][3] + 0.5
                            for lc in lcs
                        ):
                            glyph_hits += 1

                    a_out = _outside_crop(a["bbox"], cb)
                    b_out = _outside_crop(b["bbox"], cb)
                    if (glyph_hits / len(scs)) >= 0.25 or (a_out != b_out):
                        # Superimposed or out-of-cropbox conflict: demote the offending block (R1)
                        if a_out and not b_out:
                            victim, other = a, b
                        elif b_out and not a_out:
                            victim, other = b, a
                        else:
                            victim, other = small, large
                        victim["translate"] = False
                        victim["box"] = None
                        kept.append({"block": victim["id"], "reason": f"overlaps_block({other['id']})"})
                        merged_or_demoted = True
                        break

                    # Disjoint glyphs: merge small into large
                    combined = lcs + scs
                    block_chars.pop(small["id"], None)
                    block_chars[large["id"]] = combined
                    _rebuild_block_from_chars(large, block_chars[large["id"]])
                    p_blocks.remove(small)
                    merged_or_demoted = True
                    break
            if not merged_or_demoted:
                break

    new_blocks: list[dict[str, Any]] = []
    for pg in sorted(by_page):
        new_blocks.extend(by_page[pg])
    for idx, blk in enumerate(new_blocks):
        blk["order"] = idx
    return new_blocks


def _page_matrix(page: dict[str, Any]) -> tuple[float, float, float, float, float, float]:
    """Return ``(a, b, c, d, e, f)`` mapping overlay space -> layout space (matches ``merge.overlay_matrix``)."""
    cx0, cy0, cx1, cy1 = page["cropbox"]
    rot = int(page.get("rotate", 0)) % 360
    if rot == 0:
        return (1.0, 0.0, 0.0, 1.0, float(cx0), float(cy0))
    if rot == 90:
        return (0.0, 1.0, -1.0, 0.0, float(cx1), float(cy0))
    if rot == 180:
        return (-1.0, 0.0, 0.0, -1.0, float(cx1), float(cy1))
    if rot == 270:
        return (0.0, -1.0, 1.0, 0.0, float(cx0), float(cy1))
    return (1.0, 0.0, 0.0, 1.0, float(cx0), float(cy0))


def _to_overlay_rect(m: tuple[float, ...], rect: list[float]) -> list[float]:
    a, b, c, d, e, f = m
    x0, y0, x1, y1 = rect
    p0x, p0y = a * (x0 - e) + b * (y0 - f), c * (x0 - e) + d * (y0 - f)
    p1x, p1y = a * (x1 - e) + b * (y0 - f), c * (x1 - e) + d * (y0 - f)
    p2x, p2y = a * (x1 - e) + b * (y1 - f), c * (x1 - e) + d * (y1 - f)
    p3x, p3y = a * (x0 - e) + b * (y1 - f), c * (x0 - e) + d * (y1 - f)
    return [min(p0x, p1x, p2x, p3x), min(p0y, p1y, p2y, p3y), max(p0x, p1x, p2x, p3x), max(p0y, p1y, p2y, p3y)]


def _to_layout_rect(m: tuple[float, ...], rect: list[float]) -> list[float]:
    a, b, c, d, e, f = m
    x0, y0, x1, y1 = rect
    p0x, p0y = a * x0 + c * y0 + e, b * x0 + d * y0 + f
    p1x, p1y = a * x1 + c * y0 + e, b * x1 + d * y0 + f
    p2x, p2y = a * x1 + c * y1 + e, b * x1 + d * y1 + f
    p3x, p3y = a * x0 + c * y1 + e, b * x0 + d * y1 + f
    return [min(p0x, p1x, p2x, p3x), min(p0y, p1y, p2y, p3y), max(p0x, p1x, p2x, p3x), max(p0y, p1y, p2y, p3y)]


def _rect_inter_area(a: list[float], b: list[float]) -> float:
    w = min(a[2], b[2]) - max(a[0], b[0])
    if w <= 0.0:
        return 0.0
    h = min(a[3], b[3]) - max(a[1], b[1])
    if h <= 0.0:
        return 0.0
    return w * h


def _same_vertical_band(a: list[float], b: list[float]) -> bool:
    """Return True if ``a`` and ``b`` share a vertical band (not merely a small top/bottom loose-bbox graze)."""
    v_ov = min(a[3], b[3]) - max(a[1], b[1])
    if v_ov <= 0.5:
        return False
    ha = max(1.0, a[3] - a[1])
    hb = max(1.0, b[3] - b[1])
    # Thin rule/edge grazing only the top or bottom edge of a taller block
    if ha >= 5.0 and (b[3] <= a[1] + 2.0 or b[1] >= a[3] - 2.0):
        return False
    # Adjacent block strictly above or below with only a small loose-bbox overlap
    if v_ov <= 0.35 * min(ha, hb) and (
        (b[1] < a[1] - 0.5 and b[3] < a[3] - 0.5) or (a[1] < b[1] - 0.5 and a[3] < b[3] - 0.5)
    ):
        return False
    return True


def _trim_box_against_obstacle(box: list[float], obs: list[float], orig: list[float]) -> None:
    """Trim ``box`` on whichever single side removes the least area to clear ``obs``, if enough box remains."""
    if _rect_inter_area(box, obs) <= 0.5:
        return
    orig_w = max(1.0, orig[2] - orig[0])
    orig_h = max(1.0, orig[3] - orig[1])
    orig_area = orig_w * orig_h
    min_w = min(orig_w, max(4.0, 0.5 * orig_w))
    min_h = min(orig_h, max(4.0, 0.5 * orig_h))

    best: list[float] | None = None
    best_area = -1.0
    eps = 0.05
    cands = (
        [obs[2] + eps, box[1], box[2], box[3]],  # trim left
        [box[0], box[1], obs[0] - eps, box[3]],  # trim right
        [box[0], obs[3] + eps, box[2], box[3]],  # trim bottom
        [box[0], box[1], box[2], obs[1] - eps],  # trim top
    )
    for c in cands:
        w = c[2] - c[0]
        h = c[3] - c[1]
        if w < min_w or h < min_h:
            continue
        area = w * h
        if area < 0.5 * orig_area:
            continue
        if area > best_area:
            best_area = area
            best = c
    if best is not None:
        box[0], box[1], box[2], box[3] = best


def _compute_boxes(
    layout: dict[str, Any],
    kept: list[dict[str, str]] | None = None,
) -> None:
    """Compute R3 column-width boxes with downward growth in visible reading orientation, disjoint from obstacles."""
    pages = layout["pages"]
    blocks = layout["blocks"]
    two_col = layout["doc"]["two_col"]
    gutter_x = layout["doc"]["gutter_x"]

    by_page: dict[int, list[dict[str, Any]]] = {}
    for b in blocks:
        by_page.setdefault(b["page"], []).append(b)

    for p in pages:
        p_idx = p["index"]
        p_blocks = by_page.get(p_idx, [])
        if not p_blocks:
            continue
        cx0, cy0, cx1, cy1 = p["cropbox"]
        rot = int(p.get("rotate", 0)) % 360
        m = _page_matrix(p)
        ov_w = float(cx1 - cx0)
        ov_h = float(cy1 - cy0)
        if rot in (90, 270):
            ov_w, ov_h = ov_h, ov_w

        # Transform all block bboxes and page objects into overlay space (visible reading orientation)
        ov_bb_by_id: dict[str, list[float]] = {
            b["id"]: _to_overlay_rect(m, [float(v) for v in b["bbox"]]) for b in p_blocks
        }
        obj_obs: list[list[float]] = [
            _to_overlay_rect(m, [float(v) for v in obj["bbox"]]) for obj in (p.get("objects") or [])
        ]

        # Estimate column edges in overlay space from body/paragraph blocks
        body_like = [
            b
            for b in p_blocks
            if b["kind"] in ("paragraph", "heading", "caption", "footnote", "title")
            and (ov_bb_by_id[b["id"]][2] - ov_bb_by_id[b["id"]][0]) >= 0.15 * ov_w
        ]
        if not body_like:
            body_like = p_blocks

        all_x0 = sorted(ov_bb_by_id[b["id"]][0] for b in body_like)
        all_x1 = sorted(ov_bb_by_id[b["id"]][2] for b in body_like)
        page_tx0 = max(0.0, all_x0[int(0.10 * len(all_x0))]) if all_x0 else 0.08 * ov_w
        page_tx1 = min(ov_w, all_x1[int(0.90 * len(all_x1))]) if all_x1 else 0.92 * ov_w

        ov_gutter: float | None = None
        left_edges: tuple[float, float] = (page_tx0, page_tx1)
        right_edges: tuple[float, float] = (page_tx0, page_tx1)
        if two_col and gutter_x is not None and rot in (0, 180):
            ov_gutter = float(gutter_x - cx0) if rot == 0 else float(cx1 - gutter_x)
            if 0.15 * ov_w < ov_gutter < 0.85 * ov_w:
                c0 = [ov_bb_by_id[b["id"]] for b in body_like if ov_bb_by_id[b["id"]][2] <= ov_gutter + 2.0]
                c1 = [ov_bb_by_id[b["id"]] for b in body_like if ov_bb_by_id[b["id"]][0] >= ov_gutter - 2.0]
                c0_x0 = min((r[0] for r in c0), default=page_tx0)
                c0_x1 = min(max((r[2] for r in c0), default=ov_gutter - 5.0), ov_gutter - 3.0)
                if c0_x1 <= c0_x0 + 20.0:
                    c0_x0, c0_x1 = page_tx0, ov_gutter - 5.0
                left_edges = (max(0.0, c0_x0), min(ov_w, c0_x1))

                c1_x0 = max(min((r[0] for r in c1), default=ov_gutter + 5.0), ov_gutter + 3.0)
                c1_x1 = max((r[2] for r in c1), default=page_tx1)
                if c1_x1 <= c1_x0 + 20.0:
                    c1_x0, c1_x1 = ov_gutter + 5.0, page_tx1
                right_edges = (max(0.0, c1_x0), min(ov_w, c1_x1))
            else:
                ov_gutter = None

        for _retry in range(16):
            tr_blocks = [b for b in p_blocks if b.get("translate")]
            kept_blocks = [b for b in p_blocks if not b.get("translate")]
            for b in kept_blocks:
                b["box"] = None
            if not tr_blocks:
                break

            static_obs: list[list[float]] = [ov_bb_by_id[b["id"]] for b in kept_blocks] + obj_obs

            n_tr = len(tr_blocks)
            orig_ov: list[list[float]] = [ov_bb_by_id[b["id"]] for b in tr_blocks]

            # Step 1: Horizontal widening to column edges, blocked by obstacles and same-line blocks
            boxes: list[list[float]] = []
            for i in range(n_tr):
                bb = orig_ov[i]
                if ov_gutter is not None:
                    if bb[2] <= ov_gutter + 2.0:
                        ex0, ex1 = left_edges
                    elif bb[0] >= ov_gutter - 2.0:
                        ex0, ex1 = right_edges
                    else:
                        ex0, ex1 = page_tx0, page_tx1
                else:
                    ex0, ex1 = page_tx0, page_tx1

                bx0 = min(ex0, bb[0])
                bx1 = max(ex1, bb[2])
                if ov_gutter is not None:
                    if bb[2] <= ov_gutter + 2.0:
                        bx1 = min(bx1, max(bb[2], ov_gutter - 1.0))
                    elif bb[0] >= ov_gutter - 2.0:
                        bx0 = max(bx0, min(bb[0], ov_gutter + 1.0))

                # Clamp against static obstacles overlapping vertically
                for obs in static_obs:
                    if not _same_vertical_band(bb, obs):
                        continue
                    if obs[2] <= bb[0] + 1.0:
                        bx0 = max(bx0, min(bb[0], obs[2] + 0.5))
                    elif obs[0] >= bb[2] - 1.0:
                        bx1 = min(bx1, max(bb[2], obs[0] - 0.5))
                    else:
                        # Static obstacle grazes or overlaps bb: don't widen past bb on that side
                        if obs[0] < bb[0]:
                            bx0 = max(bx0, bb[0])
                        if obs[2] > bb[2]:
                            bx1 = min(bx1, bb[2])

                # Clamp against other translated blocks sharing this vertical band (split gap symmetrically)
                for j in range(n_tr):
                    if j == i:
                        continue
                    obb = orig_ov[j]
                    if not _same_vertical_band(bb, obb):
                        continue
                    if obb[0] >= bb[2] - 1.0:
                        mid = 0.5 * (bb[2] + obb[0])
                        bx1 = min(bx1, max(bb[2], mid - 0.5))
                    elif obb[2] <= bb[0] + 1.0:
                        mid = 0.5 * (obb[2] + bb[0])
                        bx0 = max(bx0, min(bb[0], mid + 0.5))
                    else:
                        # Already horizontally overlapping on the same vertical band: don't widen
                        if obb[0] < bb[0]:
                            bx0 = max(bx0, bb[0])
                        if obb[2] > bb[2]:
                            bx1 = min(bx1, bb[2])

                # Preserve intentional left indent for left/justified blocks (e.g. indented abstracts,
                # block quotes, bullet items, and adjacent 1-line headings/lines aligned with an indented
                # multi-line block) only when enough horizontal room remains after same-band clamping.
                blk_i = tr_blocks[i]
                if (
                    blk_i.get("align") in ("left", "justify")
                    and bb[0] > bx0 + 8.0
                    and (bx1 - bb[0]) >= 0.40 * max(1.0, ex1 - ex0)
                ):
                    blk_sz = float(blk_i.get("size") or 10.0)
                    is_multi = len(blk_i.get("lines") or []) >= 2 and (bb[3] - bb[1]) >= 1.8 * blk_sz
                    is_bullet = bool(_BULLET_START_RE.match(blk_i.get("text") or ""))
                    aligned_with_indented = (
                        not is_multi
                        and not is_bullet
                        and any(
                            j != i
                            and len(tr_blocks[j].get("lines") or []) >= 2
                            and abs(orig_ov[j][0] - bb[0]) <= 4.0
                            and min(abs(orig_ov[j][1] - bb[3]), abs(bb[1] - orig_ov[j][3])) <= 45.0
                            for j in range(n_tr)
                        )
                    )
                    if is_multi or is_bullet or aligned_with_indented:
                        bx0 = bb[0]

                bx0 = max(0.0, min(bx0, bb[0]))
                bx1 = min(ov_w, max(bx1, bb[2]))
                if bx1 <= bx0 + 1.0:
                    bx1 = bx0 + max(1.0, bb[2] - bb[0])
                boxes.append([bx0, bb[1], bx1, bb[3]])

            # Step 2: Downward growth in overlay reading direction (decreasing y)
            bottom_margin = 0.04 * ov_h
            for i in range(n_tr):
                bx0, orig_y0, bx1, by1 = boxes[i]
                y0_min = min(bottom_margin, orig_y0)

                # Check static obstacles below
                for obs in static_obs:
                    if min(obs[2], bx1) <= max(obs[0], bx0) + 0.1:
                        continue
                    if obs[3] <= orig_y0 + 0.5:
                        cand = obs[3] + 1.0
                        if cand > y0_min:
                            y0_min = cand
                    elif obs[1] < orig_y0 - 0.5:
                        # Obstacle already overlaps i and extends below orig_y0: do not grow past orig_y0
                        y0_min = max(y0_min, orig_y0)

                # Check all other translated blocks below (using their widened horizontal extents)
                for j in range(n_tr):
                    if j == i:
                        continue
                    jx0, jy0, jx1, jy1 = boxes[j]
                    if min(jx1, bx1) <= max(jx0, bx0) + 0.1:
                        continue
                    if jy1 <= orig_y0 + 0.5:
                        cand = jy1 + 1.0
                        if cand > y0_min:
                            y0_min = cand
                    elif jy0 < orig_y0 - 0.5:
                        y0_min = max(y0_min, orig_y0)

                y0_min = min(y0_min, orig_y0)
                y0_min = max(y0_min, 0.0)
                boxes[i][1] = y0_min

            # Step 3: Resolve any remaining pairwise box overlaps (from overlapping original bboxes)
            for _ in range(3):
                changed = False
                for i in range(n_tr):
                    for j in range(i + 1, n_tr):
                        bi = boxes[i]
                        bj = boxes[j]
                        if _rect_inter_area(bi, bj) <= 0.5:
                            continue
                        ow = min(bi[2], bj[2]) - max(bi[0], bj[0])
                        oh = min(bi[3], bj[3]) - max(bi[1], bj[1])
                        wi = max(1.0, bi[2] - bi[0])
                        wj = max(1.0, bj[2] - bj[0])
                        hi = max(1.0, bi[3] - bi[1])
                        hj = max(1.0, bj[3] - bj[1])
                        orig_hi = max(1.0, orig_ov[i][3] - orig_ov[i][1])
                        orig_hj = max(1.0, orig_ov[j][3] - orig_ov[j][1])
                        orig_wi = max(1.0, orig_ov[i][2] - orig_ov[i][0])
                        orig_wj = max(1.0, orig_ov[j][2] - orig_ov[j][0])
                        if (oh / min(hi, hj)) <= (ow / min(wi, wj)):
                            # Vertical split ordered by vertical center
                            cyi = 0.5 * (bi[1] + bi[3])
                            cyj = 0.5 * (bj[1] + bj[3])
                            if cyi >= cyj:
                                top, bot = bi, bj
                                orig_ht, orig_hb = orig_hi, orig_hj
                            else:
                                top, bot = bj, bi
                                orig_ht, orig_hb = orig_hj, orig_hi
                            min_ht = min(orig_ht, max(4.0, 0.55 * orig_ht))
                            min_hb = min(orig_hb, max(4.0, 0.55 * orig_hb))
                            if top[3] - bot[3] >= max(min_ht, 0.7 * orig_ht):
                                top[1] = bot[3] + 0.05
                                changed = True
                            elif top[1] - bot[1] >= max(min_hb, 0.7 * orig_hb):
                                bot[3] = top[1] - 0.05
                                changed = True
                            else:
                                cut = 0.5 * (top[1] + bot[3])
                                if top[3] - cut >= min_ht and cut - bot[1] >= min_hb:
                                    top[1] = cut + 0.05
                                    bot[3] = cut - 0.05
                                    changed = True
                        else:
                            # Horizontal split ordered by horizontal center
                            cxi = 0.5 * (bi[0] + bi[2])
                            cxj = 0.5 * (bj[0] + bj[2])
                            if cxi <= cxj:
                                left, right = bi, bj
                                orig_wl, orig_wr = orig_wi, orig_wj
                            else:
                                left, right = bj, bi
                                orig_wl, orig_wr = orig_wj, orig_wi
                            min_wl = min(orig_wl, max(4.0, 0.55 * orig_wl))
                            min_wr = min(orig_wr, max(4.0, 0.55 * orig_wr))
                            if right[0] - left[0] >= max(min_wl, 0.7 * orig_wl):
                                left[2] = right[0] - 0.05
                                changed = True
                            elif right[2] - left[2] >= max(min_wr, 0.7 * orig_wr):
                                right[0] = left[2] + 0.05
                                changed = True
                            else:
                                cut = 0.5 * (left[2] + right[0])
                                if cut - left[0] >= min_wl and right[2] - cut >= min_wr:
                                    left[2] = cut - 0.05
                                    right[0] = cut + 0.05
                                    changed = True
                if not changed:
                    break

            # Step 4: Trim minor edge overlaps against static obstacles (e.g. margin stamps, adjacent rules)
            for i in range(n_tr):
                for obs in static_obs:
                    _trim_box_against_obstacle(boxes[i], obs, orig_ov[i])

            # Step 5: R1 safety net — if any box still overlaps an obstacle or another box > 1.0 pt^2
            # (cannot be cleanly trimmed/split without crushing), keep that block original and retry page.
            demoted_any = False
            for i in range(n_tr):
                if any(_rect_inter_area(boxes[i], obs) > 1.0 for obs in static_obs):
                    b = tr_blocks[i]
                    b["translate"] = False
                    b["box"] = None
                    if kept is not None:
                        kept.append({"block": b["id"], "reason": "overlaps_obstacle"})
                    demoted_any = True
            if demoted_any:
                continue

            for i in range(n_tr):
                if demoted_any:
                    break
                for j in range(i + 1, n_tr):
                    if _rect_inter_area(boxes[i], boxes[j]) > 1.0:
                        ai = _box_area(orig_ov[i])
                        aj = _box_area(orig_ov[j])
                        victim = tr_blocks[i] if ai <= aj else tr_blocks[j]
                        victim["translate"] = False
                        victim["box"] = None
                        if kept is not None:
                            kept.append({"block": victim["id"], "reason": "overlaps_block"})
                        demoted_any = True
                        break
            if demoted_any:
                continue

            # Transform final overlay-space boxes back to layout space
            for i, b in enumerate(tr_blocks):
                lrect = _to_layout_rect(m, boxes[i])
                lx0, ly0, lx1, ly1 = lrect
                if lx1 <= lx0 + 0.01:
                    lx1 = lx0 + 0.5
                if ly1 < ly0:
                    ly0 = ly1
                b["box"] = {
                    "x0": _r2(lx0),
                    "x1": _r2(lx1),
                    "y1": _r2(ly1),
                    "y0_min": _r2(ly0),
                }
            break


def classify(
    layout: dict[str, Any],
    chars: list[list[dict[str, Any]]],
    target_lang: str,
    *,
    conf_threshold: float = DEFAULT_CONF_THRESHOLD,
) -> dict[str, Any]:
    """Classify extracted blocks, detect placeholders, compute R3 boxes, and assemble segments."""
    doc = layout["doc"]
    pages = layout["pages"]
    body_font: str = doc["body_font"]
    body_size: float = float(doc["body_size"])
    body_family = _font_family(body_font)

    # Index chars by block id
    block_chars: dict[str, list[dict[str, Any]]] = {}
    for p_chars in chars:
        for c in p_chars:
            bid = c.get("block")
            if bid:
                block_chars.setdefault(bid, []).append(c)

    # Split standalone bold lead-in heading lines merged onto paragraph blocks
    blocks = _split_bold_lead_heading_blocks(layout["blocks"], block_chars, body_size)
    layout["blocks"] = blocks

    # Estimate body_leading from multi-line body blocks
    leadings: list[float] = []
    for b in blocks:
        if abs(b["size"] - body_size) <= 0.8 and len(b["lines"]) >= 2:
            for l1, l2 in zip(b["lines"], b["lines"][1:]):
                dy = 0.5 * (l1[1] + l1[3]) - 0.5 * (l2[1] + l2[3])
                if 0.9 * body_size <= dy <= 1.9 * body_size:
                    leadings.append(dy)
    body_leading = float(st.median(leadings)) if leadings else 1.2 * body_size

    # Precompute per-block helper metadata
    b_meta: dict[str, dict[str, Any]] = {}
    for b in blocks:
        bid = b["id"]
        bchars = block_chars.get(bid, [])
        mono_frac = (sum(1 for c in bchars if _is_mono_font(c["font"])) / len(bchars)) if bchars else 0.0
        ital_frac = (sum(1 for c in bchars if c["italic"]) / len(bchars)) if bchars else 0.0
        bold_frac = (sum(1 for c in bchars if c["bold"]) / len(bchars)) if bchars else 0.0
        line_cs = _assign_chars_to_lines(bchars, b["lines"])
        line_stats = [_line_stats(lcs) for lcs in line_cs]
        b_meta[bid] = {
            "mono_frac": mono_frac,
            "ital_frac": ital_frac,
            "bold_frac": bold_frac,
            "rotated": _is_rotated_block(b, bchars),
            "line_stats": line_stats,
        }

    # 1. Running headers, footers, page numbers, rotated margin stamps
    running = _detect_running_headers_footers(blocks, pages, b_meta)

    by_page: dict[int, list[dict[str, Any]]] = {}
    for b in blocks:
        by_page.setdefault(b["page"], []).append(b)

    # 2. Per-page classification
    for p in pages:
        p_idx = p["index"]
        p_blocks = by_page.get(p_idx, [])
        if not p_blocks:
            continue
        W = p["width"]

        table_zones, algo_zones = _detect_table_and_algo_zones(p, p_blocks, body_size)
        img_vec_objs = [o["bbox"] for o in p["objects"] if o["kind"] in ("image", "vector")]
        form_fig_objs = [o["bbox"] for o in p["objects"] if o["kind"] == "form_figure"]
        fn_rules = [
            o["bbox"]
            for o in p["objects"]
            if o["kind"] == "rule"
            and _y_top_frac(o["bbox"][3], p) >= 0.45
            and 0.08 * W <= (o["bbox"][2] - o["bbox"][0]) <= 0.55 * W
        ]

        # Identify figure-Form blocks: extract.py appends them at the end of each page's blocks
        # (after normal XY-Cut blocks), with chars inside a form_figure bbox — or clipped-out
        # Form XObject text whose bbox surrounds a form_figure with <=10% chars inside it.
        form_block_ids: set[str] = set()
        if form_fig_objs:
            cb = p["cropbox"]
            # Scan backwards from tail (skipping rotated blocks at very end)
            for idx_rev in range(len(p_blocks) - 1, -1, -1):
                b = p_blocks[idx_rev]
                bid = b["id"]
                if b_meta[bid]["rotated"]:
                    continue
                bchars = block_chars.get(bid, [])
                if not bchars:
                    continue
                inside_form = any(
                    sum(
                        1
                        for c in bchars
                        if fb[0] - 2.0 <= 0.5 * (c["bbox"][0] + c["bbox"][2]) <= fb[2] + 2.0
                        and fb[1] - 2.0 <= 0.5 * (c["bbox"][1] + c["bbox"][3]) <= fb[3] + 2.0
                    )
                    >= max(1, int(0.5 * len(bchars)))
                    for fb in form_fig_objs
                )
                if inside_form:
                    # Guard against normal bottom-of-page body paragraph that happens to sit inside a form bbox
                    prose_w = len(re.findall(r"[A-Za-z]{3,}", b["text"]))
                    is_body_para = (
                        len(b["lines"]) >= 3
                        and prose_w >= 25
                        and _font_family(b["font"]) == body_family
                        and abs(b["size"] - body_size) <= 1.0
                    )
                    if not is_body_para and not _CAPTION_RE.match(b["text"].strip()):
                        form_block_ids.add(bid)
                        continue
                # Clipped-out Form XObject text: surrounds a form_figure (lines above & below, or
                # extending outside cropbox) with <=10% chars inside the drawn form_figure, and
                # overlaps an earlier real block on the page. Clamp its bbox to the drawn form_figure
                # so its invisible clipped-out glyphs don't block translation of real page text.
                bb = b["bbox"]
                clipped_fb: list[float] | None = None
                for fb in form_fig_objs:
                    fa = _box_area(fb)
                    if fa <= 100.0 or _inter_area(bb, fb) < 0.50 * fa:
                        continue
                    in_f = sum(
                        1
                        for c in bchars
                        if fb[0] - 2.0 <= 0.5 * (c["bbox"][0] + c["bbox"][2]) <= fb[2] + 2.0
                        and fb[1] - 2.0 <= 0.5 * (c["bbox"][1] + c["bbox"][3]) <= fb[3] + 2.0
                    )
                    if in_f > 0.10 * len(bchars):
                        continue
                    has_above = any(ln[1] >= fb[3] - 4.0 for ln in b["lines"])
                    has_below = any(ln[3] <= fb[1] + 4.0 for ln in b["lines"])
                    out_crop = (
                        bb[0] < cb[0] - 2.0
                        or bb[1] < cb[1] - 2.0
                        or bb[2] > cb[2] + 2.0
                        or bb[3] > cb[3] + 2.0
                    )
                    if not ((has_above and has_below) or out_crop):
                        continue
                    overlaps_earlier = any(
                        _inter_area(bb, p_blocks[j]["bbox"]) > 10.0 for j in range(idx_rev)
                    )
                    if overlaps_earlier:
                        clipped_fb = fb
                        break
                if clipped_fb is not None:
                    form_block_ids.add(bid)
                    b["bbox"] = [
                        _r2(max(bb[0], clipped_fb[0])),
                        _r2(max(bb[1], clipped_fb[1])),
                        _r2(min(bb[2], clipped_fb[2])),
                        _r2(min(bb[3], clipped_fb[3])),
                    ]
                    continue
                break

        for b in p_blocks:
            bid = b["id"]
            bm = b_meta[bid]
            txt = b["text"].strip()
            bb = b["bbox"]
            cx = 0.5 * (bb[0] + bb[2])
            cy = 0.5 * (bb[1] + bb[3])
            top_frac = _y_top_frac(bb[3], p)

            # Default
            b["kind"] = "paragraph"
            b["confidence"] = 0.90

            if bid in running:
                b["kind"] = running[bid]
                b["confidence"] = 0.96
                continue
            if bm["rotated"]:
                b["kind"] = "figure"
                b["confidence"] = 0.92
                continue
            if bid in form_block_ids:
                b["kind"] = "figure"
                b["confidence"] = 0.95
                continue

            is_cap = bool(_CAPTION_RE.match(txt))

            # Ruled algorithm zones
            if any(az[0] <= cx <= az[2] and az[1] <= cy <= az[3] for az in algo_zones):
                b["kind"] = "code"
                b["confidence"] = 0.95
                continue

            # Ruled table zones (never swallow captions)
            if not is_cap and any(tz[0] <= cx <= tz[2] and tz[1] <= cy <= tz[3] for tz in table_zones):
                b["kind"] = "table"
                b["confidence"] = 0.94
                continue

            # Image / dense vector figure regions (never swallow captions or long body-font prose)
            if not is_cap and any(
                fv[0] - 2.0 <= cx <= fv[2] + 2.0 and fv[1] - 2.0 <= cy <= fv[3] + 2.0
                for fv in img_vec_objs
            ):
                prose_w = len(re.findall(r"[A-Za-z]{3,}", txt))
                if not (len(b["lines"]) >= 3 and prose_w >= 20 and _font_family(b["font"]) == body_family):
                    b["kind"] = "figure"
                    b["confidence"] = 0.92
                    continue

            # Caption
            if is_cap:
                b["kind"] = "caption"
                b["confidence"] = 0.95
                continue

            # Monospace code (URLs are NOT code)
            if (
                bm["mono_frac"] >= 0.70
                and not _URL_RE.search(txt)
                and (len(b["lines"]) >= 2 or len(txt) >= 30 or _CODE_HINT_RE.search(txt))
            ):
                b["kind"] = "code"
                b["confidence"] = 0.90
                continue

            # Unruled algorithm / pseudocode
            if _ALGO_RE.match(txt) or (
                len(b["lines"]) >= 3
                and sum(1 for ln in bm["line_stats"] if _ALGO_LINE_RE.match(ln["text"]))
                >= max(2, int(0.5 * len(b["lines"])))
            ):
                b["kind"] = "code"
                b["confidence"] = 0.88
                continue

            # Footnote (below footnote rule or small font near bottom)
            below_fn_rule = any(
                r[1] >= bb[3] - 4.0 and min(r[2], bb[2]) > max(r[0], bb[0]) and b["size"] <= 0.94 * body_size
                for r in fn_rules
            )
            bottom_fn = (
                top_frac >= 0.80
                and b["size"] <= 0.88 * body_size
                and (bool(re.match(r"^(?:\d{1,2}|[*∗†‡§])(?:\s|[A-Z]|http)", txt)) or bool(_URL_RE.match(txt)))
            )
            if below_fn_rule or bottom_fn:
                if p_idx == 0 and _FRONT_HINT_RE.search(txt):
                    b["kind"] = "front_matter"
                    b["confidence"] = 0.92
                else:
                    b["kind"] = "footnote"
                    b["confidence"] = 0.88
                continue

            # Display math (theorem-style italic prose is NOT math!)
            m_ratio, p_words = _block_math_ratio(bm["line_stats"], body_font, body_family)
            has_eq_num = bool(re.search(r"\(\d+(?:\.\d+)*\)\s*$", txt))
            is_italic_prose = bm["ital_frac"] >= 0.65 and len(re.findall(r"[A-Za-z]{3,}", txt)) >= 4
            if not is_italic_prose and (
                (m_ratio >= 0.52 and p_words <= 4)
                or (m_ratio >= 0.35 and p_words <= 2 and (has_eq_num or len(txt) <= 80))
                or (p_words == 0 and len(txt) <= 45 and bool(re.search(r"[=<>+\-∑∏∫≤≥≈∈∀∃]", txt)))
            ):
                b["kind"] = "display_math"
                b["confidence"] = 0.90
                continue

            # Heading candidates
            norm_h = _normalize_spaced_caps(txt)
            bchars_h = block_chars.get(bid, [])
            is_runin_prose = False
            if len(b["lines"]) == 1 and len(txt.split()) >= 8 and bchars_h:
                lead_c = bchars_h[0]
                tail_c = bchars_h[-1]
                tail_sz = tail_c["size"]
                # Styled lead-in (larger, bolder, or distinct prose font) followed by body-sized prose
                has_styled_lead = (
                    lead_c["size"] > tail_sz + 0.8
                    or (lead_c["bold"] and not tail_c["bold"])
                    or (lead_c["font"] != tail_c["font"] and not _is_math_font(lead_c["font"]) and not _is_mono_font(lead_c["font"]))
                )
                if has_styled_lead and tail_sz <= body_size + 0.8:
                    tail_prose_frac = sum(
                        1 for c in bchars_h if abs(c["size"] - tail_sz) <= 0.6 and c["font"] == tail_c["font"]
                    ) / len(bchars_h)
                    if tail_prose_frac >= 0.50:
                        is_runin_prose = True
            if (
                not is_runin_prose
                and len(b["lines"]) <= 3
                and 2 <= len(txt) <= 120
                and not _NON_HEAD_RE.match(txt)
                and not _BULLET_START_RE.match(txt)
                and not _FRONT_HINT_RE.search(txt)
                and not _KEYWORDS_START_RE.match(txt)
                and not _AUTHOR_INIT_RE.match(txt)
                and not txt.startswith("[")
                and (
                    _is_ref_heading_text(norm_h)
                    or _is_abstract_heading_text(norm_h)
                    or bool(_NUM_HEAD_RE.match(norm_h))
                    or (
                        b["size"] >= 1.09 * body_size
                        and not txt.endswith(".")
                        and bool(re.search(r"[A-Za-z]", txt))
                    )
                    or (
                        b["bold"]
                        and len(b["lines"]) == 1
                        and not txt.endswith((".", ",", ";", "?", "!"))
                        and len(txt.split()) <= 12
                        and p_words >= 1
                    )
                )
            ):
                b["kind"] = "heading"
                b["confidence"] = 0.90
                continue

            # Isolated numeric / axis tick / short code fragment
            if len(b["lines"]) == 1 and len(txt) <= 12 and re.fullmatch(r"[\d.,%+\-±()\[\]/×·]+", txt):
                near_fig = any(_box_overlaps(bb, fv, pad=22.0) for fv in img_vec_objs + form_fig_objs)
                b["kind"] = "figure" if near_fig else "table"
                b["confidence"] = 0.88
                continue

            # Multi-line numeric borderless table
            if len(b["lines"]) >= 3:
                toks = txt.split()
                num_toks = sum(1 for t in toks if re.search(r"\d", t))
                if len(toks) >= 8 and num_toks / len(toks) >= 0.45 and b["size"] <= 0.96 * body_size:
                    b["kind"] = "table"
                    b["confidence"] = 0.86
                    continue

            # Paragraph confidence calibration
            words_n = len(re.findall(r"[A-Za-z]{2,}", txt))
            if words_n >= 8 and abs(b["size"] - body_size) <= 1.2:
                b["confidence"] = 0.95
            elif words_n >= 4:
                b["confidence"] = 0.85
            elif words_n >= 2:
                b["confidence"] = 0.72
            else:
                b["confidence"] = 0.45  # single-word/symbol stray fragment -> R1 candidate

        # Borderless table pass by column alignment
        for bid in _detect_borderless_table_blocks(p_blocks, body_size):
            for b in p_blocks:
                if b["id"] == bid:
                    b["kind"] = "table"
                    b["confidence"] = 0.86

        # TOC detection on this page (dotted leaders, "Contents" heading, or dense numbered section list)
        toc_dotted = [
            b
            for b in p_blocks
            if b["kind"] in ("paragraph", "heading")
            and (
                bool(_TOC_LINE_RE.search(b["text"]))
                or any(_TOC_LINE_RE.search(ln["text"]) for ln in b_meta[b["id"]]["line_stats"])
            )
        ]
        toc_head = [b for b in p_blocks if _TOC_HEAD_RE.match(_normalize_spaced_caps(b["text"].strip()))]
        toc_num_page = [
            b
            for b in p_blocks
            if b["kind"] in ("paragraph", "heading")
            and len(b["lines"]) <= 2
            and bool(_TOC_NUM_LINE_RE.match(b["text"].strip()))
        ]
        toc_num_lns = [
            b
            for b in p_blocks
            if b["kind"] in ("paragraph", "heading")
            and len(b["lines"]) <= 2
            and (
                bool(_TOC_NUM_LINE_RE.match(b["text"].strip()))
                or bool(_NUM_HEAD_RE.match(_normalize_spaced_caps(b["text"].strip())))
            )
        ]
        is_toc_page = (
            len(toc_dotted) >= 3
            or (bool(toc_head) and (len(toc_dotted) >= 1 or len(toc_num_lns) >= 3))
            or len(toc_num_page) >= 5
        )
        if is_toc_page:
            seed_toc = toc_head + toc_dotted + toc_num_lns
            toc_y_top = max(b["bbox"][3] for b in seed_toc)
            toc_y_bot = min(b["bbox"][1] for b in seed_toc)
            for b in p_blocks:
                if b["kind"] in ("header", "footer", "figure", "table"):
                    continue
                cy = 0.5 * (b["bbox"][1] + b["bbox"][3])
                t = b["text"].strip()
                prose_w = len(re.findall(r"[A-Za-z]{3,}", t))
                # Mark blocks within the TOC vertical band unless they are long prose paragraphs
                if (
                    b in seed_toc
                    or (
                        toc_y_bot - 4.0 <= cy <= toc_y_top + 4.0
                        and not (len(b["lines"]) >= 3 and prose_w >= 25 and not _TOC_LINE_RE.search(t))
                    )
                ):
                    b["kind"] = "toc"
                    b["confidence"] = 0.95

    # 3. Document-level passes: Title, Front-matter, Keywords, References, Heading refinement
    p0 = pages[0]
    p0_blocks = [
        b for b in by_page.get(0, []) if b["kind"] not in ("header", "footer", "figure", "table", "footnote")
    ]
    top_p0 = [
        b
        for b in p0_blocks
        if _y_top_frac(b["bbox"][3], p0) <= 0.45
        and len(b["text"].strip()) >= 8
        and not _FRONT_HINT_RE.search(b["text"])
        and not _VENUE_BADGE_RE.match(b["text"].strip())
    ]
    title_blk: dict[str, Any] | None = None
    if top_p0:
        max_sz = max(b["size"] for b in top_p0)
        cands = [b for b in top_p0 if b["size"] >= max_sz - 0.6]
        # Highest on page = largest y1 in PDF user space
        title_blk = max(cands, key=lambda b: b["bbox"][3])
        title_blk["kind"] = "title"
        title_blk["confidence"] = 0.96

    # Find first body section y1 on page 0 (highest y1 among Abstract / Section 1 / Introduction)
    first_body_y1 = -1e9
    for b in p0_blocks:
        if b is title_blk:
            continue
        t = b["text"].strip()
        nt = _normalize_spaced_caps(t)
        if (
            _is_abstract_heading_text(nt)
            or bool(_ABSTRACT_INLINE_RE.match(t))
            or (b["kind"] == "heading" and bool(re.match(r"^(?:1\.?\s+|I\.\s+|Introduction\b)", nt, re.I)))
        ):
            if b["bbox"][3] > first_body_y1:
                first_body_y1 = b["bbox"][3]
            if _ABSTRACT_INLINE_RE.match(t):
                b["kind"] = "paragraph"
                b["confidence"] = 0.95

    if title_blk is not None:
        t_bot = title_blk["bbox"][1]
        t_top = title_blk["bbox"][3]
        for b in p0_blocks:
            if b is title_blk or b["kind"] in ("footnote", "caption", "code"):
                continue
            t = b["text"].strip()
            nt = _normalize_spaced_caps(t)
            if _is_abstract_heading_text(nt) or _ABSTRACT_INLINE_RE.match(t):
                continue
            # Above title (venue banner / journal badge)
            if b["bbox"][1] >= t_top - 2.0:
                b["kind"] = "front_matter"
                b["confidence"] = 0.95
            # Between title and Abstract/Section 1 (includes gutter-split author lines like 1703.06870 p1)
            elif first_body_y1 > -1e8 and b["bbox"][3] <= t_bot + 2.0 and b["bbox"][1] >= first_body_y1 - 2.0:
                words = len(re.findall(r"[A-Za-z]{2,}", t))
                if words < 45 or _STRONG_FRONT_RE.search(t):
                    b["kind"] = "front_matter"
                    b["confidence"] = 0.93
            # Directly below title when no Abstract/Section 1 heading was found: author list with superscripts
            elif (
                first_body_y1 <= -1e8
                and 0.0 <= t_bot - b["bbox"][3] <= 35.0
                and len(b["lines"]) <= 4
                and len(re.findall(r"[A-Za-z]{2,}", t)) < 35
                and (
                    sum(
                        len(_find_superscript_citation_runs(ln["cs"], ln["size"], ln["base"], body_family))
                        for ln in b_meta[b["id"]]["line_stats"]
                    )
                    >= 2
                    or "\uf02a" in t
                )
            ):
                b["kind"] = "front_matter"
                b["confidence"] = 0.93
            # Page-0 front-matter hints / sidebar metadata / copyright / keywords
            elif _KEYWORDS_START_RE.match(t) or (
                _FRONT_HINT_RE.search(t)
                and (
                    b["size"] <= 0.96 * body_size
                    or _y_top_frac(b["bbox"][3], p0) >= 0.72
                    or (b["bbox"][2] - b["bbox"][0]) < 0.32 * p0["width"]
                )
            ):
                b["kind"] = "front_matter"
                b["confidence"] = 0.93
            # Narrow sidebar metadata blocks on page 0 (e.g. eLife / PLOS ONE left margin)
            elif (
                not doc["two_col"]
                and (b["bbox"][2] - b["bbox"][0]) < 0.28 * p0["width"]
                and b["bbox"][2] < p0["cropbox"][0] + 0.34 * p0["width"]
                and b["size"] <= 0.92 * body_size
            ):
                b["kind"] = "front_matter"
                b["confidence"] = 0.90

    # Key words / Index Terms on page 0 or 1 -> front_matter
    for b in blocks:
        if b["page"] <= 1 and b["kind"] in ("paragraph", "heading") and _KEYWORDS_START_RE.match(b["text"].strip()):
            b["kind"] = "front_matter"
            b["confidence"] = 0.95

    # References section pass (including the References/Bibliography heading itself -> refs)
    in_refs = False
    for b in blocks:
        t = b["text"].strip()
        nt = _normalize_spaced_caps(t)
        is_app_head = (
            len(b["lines"]) <= 2
            and len(t) <= 80
            and (b["bold"] or b["size"] >= 1.08 * body_size)
            and not _AUTHOR_INIT_RE.match(t)
            and "," not in t
            and (
                bool(_APPENDIX_HEAD_RE.match(nt))
                or bool(re.match(r"^(?:[A-Z](?:\.\d+)+\.?|[A-Z]\.)\s+[A-Z][A-Za-z]{2,}", nt))
                or bool(re.match(r"^[A-Z]\s+[A-Z]{3,}", nt))
            )
            and not re.search(r"\b(?:19|20)\d{2}\b", t)
        )
        if b["kind"] in ("heading", "paragraph") and len(b["lines"]) <= 2 and _is_ref_heading_text(nt):
            in_refs = True
            b["kind"] = "refs"
            b["confidence"] = 0.97
            continue
        if in_refs:
            if is_app_head:
                in_refs = False
                b["kind"] = "heading"
                b["confidence"] = 0.90
            elif b["kind"] in ("paragraph", "display_math", "footnote", "heading"):
                b["kind"] = "refs"
                b["confidence"] = 0.96

    # Promote 1-line monospace blocks adjacent to code blocks
    for i, b in enumerate(blocks):
        if b["kind"] == "paragraph" and b_meta[b["id"]]["mono_frac"] >= 0.70 and not _URL_RE.search(b["text"]):
            prev_code = i > 0 and blocks[i - 1]["kind"] == "code" and blocks[i - 1]["page"] == b["page"]
            next_code = i + 1 < len(blocks) and blocks[i + 1]["kind"] == "code" and blocks[i + 1]["page"] == b["page"]
            if prev_code or next_code:
                b["kind"] = "code"
                b["confidence"] = 0.90

    # Heading refinement: demote false headings back to paragraph
    for b in blocks:
        if b["kind"] != "heading":
            continue
        t = b["text"].strip()
        nt = _normalize_spaced_caps(t)
        if _is_abstract_heading_text(nt) or _NUM_HEAD_RE.match(nt):
            continue
        if len(t) > 100 or t.endswith(".") or re.search(r"\.\s+[A-Z]", t):
            b["kind"] = "paragraph"
            b["confidence"] = 0.88

    # 3b. Orphan last lines: a 1-line block directly under a translated block (same column or
    #     short last line of a spanning block, same font/size, gap <= parent's line pitch)
    #     takes that block's kind and joins its segment.
    orphan_parent: dict[str, str] = {}
    for p_idx, p_blocks in by_page.items():
        if len(p_blocks) < 2:
            continue
        for o in p_blocks:
            if len(o["lines"]) != 1:
                continue
            if o["kind"] in ("header", "footer", "figure", "refs", "toc", "code", "display_math"):
                continue
            otxt = o["text"].strip()
            if not otxt:
                continue
            if (
                _CAPTION_RE.match(otxt)
                or _KEYWORDS_START_RE.match(otxt)
                or _ALGO_RE.match(otxt)
                or _BULLET_START_RE.match(otxt)
                or _is_abstract_heading_text(_normalize_spaced_caps(otxt))
                or _is_ref_heading_text(_normalize_spaced_caps(otxt))
                or _NUM_HEAD_RE.match(_normalize_spaced_caps(otxt))
                or _STRONG_FRONT_RE.search(otxt)
            ):
                continue

            obb = o["bbox"]
            o_fam = _font_family(o["font"])
            o_sz = o["size"]
            best_parent: dict[str, Any] | None = None
            best_gap = 1e9

            for cand in p_blocks:
                if cand is o or cand["kind"] not in TRANSLATED:
                    continue
                # Never merge a distinct heading/title into a paragraph, or a table cell unless caption continuation
                if o["kind"] in ("heading", "title") and cand["kind"] != o["kind"]:
                    continue
                if o["kind"] == "table":
                    if cand["kind"] != "caption":
                        continue
                    # Only rescue prose-like caption continuations, not short numeric table headers
                    if len(re.findall(r"[A-Za-z]{3,}", otxt)) < 3 and not _URL_RE.search(otxt):
                        continue
                if _FRONT_HINT_RE.search(otxt) and not (cand["kind"] == "caption" and _URL_RE.search(otxt)):
                    continue

                cbb = cand["bbox"]
                # Must be directly above o
                gap = cbb[1] - obb[3]
                if len(cand["lines"]) >= 2:
                    steps = [
                        0.5 * (l1[1] + l1[3]) - 0.5 * (l2[1] + l2[3])
                        for l1, l2 in zip(cand["lines"], cand["lines"][1:])
                        if 0.5 * (l1[1] + l1[3]) - 0.5 * (l2[1] + l2[3]) > 0.5 * cand["size"]
                    ]
                    c_pitch = float(st.median(steps)) if steps else max(1.3 * cand["size"], body_leading)
                else:
                    c_pitch = max(1.3 * cand["size"], body_leading)

                if not (-4.0 <= gap <= c_pitch):
                    continue

                # Parent must not end a completed sentence (except ':' which can continue on next line)
                ctxt = cand["text"].rstrip()
                if re.search(r'[.?!]["”’)\]]*$', ctxt):
                    continue

                # Column compatibility: same column, or short last line of a spanning block
                if o["column"] != cand["column"] and not (cand["column"] is None and o["column"] in (0, 1)):
                    continue

                # Horizontal alignment: within parent width, left-aligned or centered
                if obb[0] < cbb[0] - 8.0 or obb[2] > cbb[2] + 8.0:
                    continue
                left_aligned = abs(obb[0] - cbb[0]) <= 12.0 or abs(obb[0] - cand["lines"][-1][0]) <= 12.0
                centered = abs(0.5 * (obb[0] + obb[2]) - 0.5 * (cbb[0] + cbb[2])) <= 15.0
                if not (left_aligned or centered):
                    continue

                # Font & size match against parent block OR parent's last line (including its tail run)
                c_lstats = b_meta[cand["id"]]["line_stats"]
                c_last_sz = c_lstats[-1]["size"] if c_lstats else cand["size"]
                c_last_cs = c_lstats[-1]["cs"] if c_lstats else []
                c_last_tail_sz = c_last_cs[-1]["size"] if c_last_cs else c_last_sz
                c_last_fam = (
                    _font_family(Counter(c["font"] for c in c_last_cs).most_common(1)[0][0])
                    if c_last_cs
                    else _font_family(cand["font"])
                )
                c_last_tail_fam = _font_family(c_last_cs[-1]["font"]) if c_last_cs else c_last_fam
                c_fam = _font_family(cand["font"])
                font_ok = o_fam in (c_fam, c_last_fam, c_last_tail_fam)
                size_ok = (
                    abs(o_sz - cand["size"]) <= 0.8
                    or abs(o_sz - c_last_sz) <= 0.8
                    or abs(o_sz - c_last_tail_sz) <= 0.8
                )
                if not (font_ok and size_ok):
                    continue

                # No intervening block vertically between cand and o in the same horizontal span
                blocked = any(
                    mid is not cand
                    and mid is not o
                    and obb[3] - 0.5 < 0.5 * (mid["bbox"][1] + mid["bbox"][3]) < cbb[1] + 0.5
                    and min(mid["bbox"][2], obb[2]) > max(mid["bbox"][0], obb[0])
                    for mid in p_blocks
                )
                if blocked:
                    continue

                if gap < best_gap:
                    best_gap = gap
                    best_parent = cand

            if best_parent is not None:
                o["kind"] = best_parent["kind"]
                o["confidence"] = best_parent["confidence"]
                orphan_parent[o["id"]] = best_parent["id"]

    # Reorder orphan blocks immediately after their parent so reading order stays monotonic
    if orphan_parent:
        children_by_parent: dict[str, list[dict[str, Any]]] = {}
        orphan_ids = set(orphan_parent.keys())
        for b in blocks:
            if b["id"] in orphan_parent:
                children_by_parent.setdefault(orphan_parent[b["id"]], []).append(b)
        reordered: list[dict[str, Any]] = []
        for b in blocks:
            if b["id"] in orphan_ids:
                continue
            reordered.append(b)
            # Emit any attached orphan chain in top-to-bottom order
            stack = list(children_by_parent.get(b["id"], []))
            while stack:
                ch = stack.pop(0)
                reordered.append(ch)
                if ch["id"] in children_by_parent:
                    stack = list(children_by_parent[ch["id"]]) + stack
        for idx, blk in enumerate(reordered):
            blk["order"] = idx
        blocks = reordered
        layout["blocks"] = blocks

    # 3c. Merge safe same-page orphan last lines into their parent block (e.g. short last line of a
    #     spanning abstract split into col=0), and merge hanging-indent bullet continuations into
    #     their bullet-item block so each list item is one block/segment with proper hanging indent.
    by_page_3c: dict[int, list[dict[str, Any]]] = {}
    for b in blocks:
        by_page_3c.setdefault(b["page"], []).append(b)
    removed_3c: set[str] = set()
    blk_by_id_3c = {b["id"]: b for b in blocks}

    # 3c-i: Merge same-page orphan last line into parent when union bbox hits no other block/object
    for oid, pid in list(orphan_parent.items()):
        o = blk_by_id_3c.get(oid)
        par = blk_by_id_3c.get(pid)
        if o is None or par is None or o["id"] in removed_3c or par["id"] in removed_3c:
            continue
        if o["page"] != par["page"] or o["kind"] != par["kind"]:
            continue
        union_bb = [
            min(par["bbox"][0], o["bbox"][0]),
            min(par["bbox"][1], o["bbox"][1]),
            max(par["bbox"][2], o["bbox"][2]),
            max(par["bbox"][3], o["bbox"][3]),
        ]
        p_blocks_pg = by_page_3c.get(par["page"], [])
        p_objs_pg = pages[par["page"]].get("objects") or []
        hits_other = any(
            other is not par
            and other is not o
            and other["id"] not in removed_3c
            and _inter_area(union_bb, other["bbox"]) > 1.0
            for other in p_blocks_pg
        ) or any(_inter_area(union_bb, obj["bbox"]) > 1.0 for obj in p_objs_pg)
        if hits_other:
            continue
        p_cs = block_chars.get(par["id"], [])
        o_cs = block_chars.pop(o["id"], [])
        if o_cs:
            block_chars[par["id"]] = p_cs + o_cs
            _rebuild_block_from_chars(par, block_chars[par["id"]])
            removed_3c.add(o["id"])
            orphan_parent.pop(oid, None)

    # 3c-ii: Merge hanging-indent bullet-item continuation blocks into their bullet block
    for p_idx, p_blocks_pg in by_page_3c.items():
        p_objs_pg = pages[p_idx].get("objects") or []
        i = 0
        while i < len(p_blocks_pg):
            a = p_blocks_pg[i]
            if a["id"] in removed_3c or a["kind"] != "paragraph":
                i += 1
                continue
            atxt = a["text"].strip()
            if not _BULLET_START_RE.match(atxt):
                i += 1
                continue
            a_x0 = a["lines"][0][0]
            # Find post-bullet text x0 on first line for tight alignment check
            a_cs = block_chars.get(a["id"], [])
            l0_y0, l0_y1 = a["lines"][0][1], a["lines"][0][3]
            l0_cs = sorted(
                [c for c in a_cs if l0_y0 - 2.0 <= 0.5 * (c["bbox"][1] + c["bbox"][3]) <= l0_y1 + 2.0],
                key=lambda c: (c["bbox"][0], c["i"]),
            )
            post_bullet_x0: float | None = None
            for ci_l, ch in enumerate(l0_cs):
                if ci_l > 0 and ch["u"].strip():
                    post_bullet_x0 = ch["bbox"][0]
                    break

            j = i + 1
            while j < len(p_blocks_pg):
                b = p_blocks_pg[j]
                if b["id"] in removed_3c:
                    j += 1
                    continue
                if b["kind"] != "paragraph" or b["column"] != a["column"]:
                    break
                btxt = b["text"].strip()
                if (
                    not btxt
                    or _BULLET_START_RE.match(btxt)
                    or _CAPTION_RE.match(btxt)
                    or _KEYWORDS_START_RE.match(btxt)
                    or _NUM_HEAD_RE.match(_normalize_spaced_caps(btxt))
                ):
                    break
                gap = a["bbox"][1] - b["bbox"][3]
                if not (-4.0 <= gap <= max(1.5 * body_leading, 1.8 * a["size"])):
                    break
                b_x0 = b["lines"][0][0]
                if not (a_x0 + 3.0 <= b_x0 <= a_x0 + max(3.5 * a["size"], 36.0)):
                    break
                if not all(abs(ln[0] - b_x0) <= 6.0 for ln in b["lines"]):
                    break
                if b["bbox"][2] > max(a["bbox"][2], a["lines"][0][2]) + 15.0:
                    break
                # Font/size compatibility (check a's tail char too in case of bold lead-in)
                a_cs_cur = block_chars.get(a["id"], [])
                a_tail_fam = _font_family(a_cs_cur[-1]["font"]) if a_cs_cur else _font_family(a["font"])
                a_tail_sz = a_cs_cur[-1]["size"] if a_cs_cur else a["size"]
                b_fam = _font_family(b["font"])
                if b_fam not in (_font_family(a["font"]), a_tail_fam):
                    break
                if abs(b["size"] - a["size"]) > 0.8 and abs(b["size"] - a_tail_sz) > 0.8:
                    break
                # If a already ends a sentence, only merge if b aligns closely with post-bullet text
                a_ends_sent = bool(re.search(r'[.?!]["”’)\]]*$', a["text"].rstrip()))
                if a_ends_sent:
                    if post_bullet_x0 is None or abs(b_x0 - post_bullet_x0) > 4.5 or gap > 1.15 * body_leading:
                        break
                union_bb = [
                    min(a["bbox"][0], b["bbox"][0]),
                    min(a["bbox"][1], b["bbox"][1]),
                    max(a["bbox"][2], b["bbox"][2]),
                    max(a["bbox"][3], b["bbox"][3]),
                ]
                hits_other = any(
                    other is not a
                    and other is not b
                    and other["id"] not in removed_3c
                    and _inter_area(union_bb, other["bbox"]) > 1.0
                    for other in p_blocks_pg
                ) or any(_inter_area(union_bb, obj["bbox"]) > 1.0 for obj in p_objs_pg)
                if hits_other:
                    break
                b_cs = block_chars.pop(b["id"], [])
                if b_cs:
                    block_chars[a["id"]] = a_cs_cur + b_cs
                    _rebuild_block_from_chars(a, block_chars[a["id"]])
                    removed_3c.add(b["id"])
                j += 1
            i += 1

    if removed_3c:
        blocks = [b for b in blocks if b["id"] not in removed_3c]
        for idx, blk in enumerate(blocks):
            blk["order"] = idx
        layout["blocks"] = blocks

    # 4. Translate decision + R1 kept list (garbage_ratio > 0.2 or confidence < threshold)
    kept: list[dict[str, str]] = []
    for b in blocks:
        p = pages[b["page"]]
        b["confidence"] = _r2(b["confidence"])
        if p["garbage_ratio"] > 0.2:
            b["translate"] = False
            kept.append({"block": b["id"], "reason": f"garbage_page({p['garbage_ratio']:.2f})"})
        elif b["kind"] not in TRANSLATED:
            b["translate"] = False
        elif b["confidence"] < conf_threshold:
            b["translate"] = False
            kept.append({"block": b["id"], "reason": f"low_confidence({b['confidence']:.2f})"})
        else:
            b["translate"] = True

    # 4b. Absorb small kept fragments overlapping translated blocks (R4 exact ownership, R1 fallback)
    blocks, kept, absorbed_math_chars = _absorb_or_resolve_overlapping_kept_blocks(
        blocks, block_chars, kept, body_size, body_family
    )
    # 4c. Resolve contained/overlapping translated blocks (split gutter-straddlers, merge fragments, R1 conflicts)
    blocks = _resolve_contained_translated_blocks(blocks, block_chars, pages, doc, kept)
    layout["blocks"] = blocks

    # 4d. Refine block alignment using each block's own left/right edges so indented multi-line
    #     blocks (abstracts, wide-margin bodies, hanging-indent list items) are never misclassified
    #     as right-aligned or centered.
    for b in blocks:
        if not b.get("translate"):
            continue
        lns = b.get("lines") or []
        if len(lns) < 2:
            continue
        p = pages[b["page"]]
        col_w = 0.45 * p["width"] if (doc.get("two_col") and b.get("column") in (0, 1)) else 0.84 * p["width"]
        body_lns = lns[:-1]
        is_hanging = len(lns) >= 2 and (lns[1][0] - lns[0][0]) >= 3.0 and all(
            abs(ln[0] - lns[1][0]) <= 6.0 for ln in lns[1:]
        )
        if is_hanging:
            ref_lns = body_lns[1:] if len(body_lns) >= 2 else lns[1:]
            b_left = st.median(ln[0] for ln in ref_lns)
            left_flush = sum(1 for ln in ref_lns if abs(ln[0] - b_left) <= 6.0) / max(1, len(ref_lns))
        else:
            b_left = min(ln[0] for ln in body_lns)
            # Allow a first-line paragraph indent on line 0
            first_indented = len(body_lns) >= 2 and 3.0 < (lns[0][0] - b_left) <= max(3.5 * b["size"], 36.0)
            check_lns = body_lns[1:] if first_indented else body_lns
            left_flush = sum(1 for ln in check_lns if abs(ln[0] - b_left) <= 6.0) / max(1, len(check_lns))
        b_right = max(ln[2] for ln in body_lns)
        b_w = b_right - min(ln[0] for ln in lns)
        right_flush = sum(1 for ln in body_lns if abs(ln[2] - b_right) <= 6.0) / max(1, len(body_lns))
        if left_flush >= 0.7:
            if len(body_lns) >= 2 and right_flush >= 0.7 and b_w >= 0.35 * col_w:
                b["align"] = "justify"
            elif (
                len(body_lns) == 1
                and right_flush == 1.0
                and (lns[0][2] - lns[0][0]) >= 0.75 * col_w
            ):
                b["align"] = "justify"
            else:
                b["align"] = "left"
        else:
            mids = [0.5 * (ln[0] + ln[2]) for ln in lns]
            mid_med = st.median(mids)
            if sum(1 for m in mids if abs(m - mid_med) <= 10.0) >= 0.7 * len(lns):
                b["align"] = "center"
            elif (
                sum(1 for ln in lns if abs(ln[2] - b_right) <= 6.0) >= 0.7 * len(lns)
                and (max(ln[0] for ln in lns) - min(ln[0] for ln in lns)) > 10.0
            ):
                b["align"] = "right"
            else:
                b["align"] = "left"

    # 5. Box geometry (R3) for translated blocks
    _compute_boxes(layout, kept)

    # 6. Paragraph joins (cross-column, cross-page, and run-in heading first-line block)
    trans_blocks = [b for b in blocks if b["translate"]]

    # Column left/right edges per (page, col) for indent & full-line checks
    col_right: dict[tuple[int, int | None], float] = {}
    col_left: dict[tuple[int, int | None], float] = {}
    para_ids_by_pc: dict[tuple[int, int | None], list[str]] = {}
    for b in trans_blocks:
        if b["kind"] == "paragraph":
            key = (b["page"], b["column"])
            para_ids_by_pc.setdefault(key, []).append(b["id"])
            if len(b["lines"]) >= 2:
                col_right[key] = max(col_right.get(key, -1e9), b["bbox"][2])
                col_left[key] = min(col_left.get(key, 1e9), b["bbox"][0])

    last_in_col = {ids[-1] for ids in para_ids_by_pc.values() if ids}
    first_in_col = {ids[0] for ids in para_ids_by_pc.values() if ids}

    seg_groups: list[list[dict[str, Any]]] = []
    grp_by_block_id: dict[str, list[dict[str, Any]]] = {}
    for b in trans_blocks:
        merged = False
        # Orphan last line explicitly attached to its parent block's segment
        par_id = orphan_parent.get(b["id"])
        if par_id and par_id in grp_by_block_id:
            grp_by_block_id[par_id].append(b)
            grp_by_block_id[b["id"]] = grp_by_block_id[par_id]
            merged = True

        if not merged and b["kind"] == "paragraph" and seg_groups:
            # Find most recent paragraph segment (skipping intervening captions/footnotes)
            prev_grp = None
            for grp in reversed(seg_groups):
                gkind = grp[0]["kind"]
                if gkind in ("caption", "footnote"):
                    continue
                if gkind == "paragraph":
                    prev_grp = grp
                break

            if prev_grp is not None:
                pb = prev_grp[-1]
                ptxt = pb["text"].rstrip()
                btxt = b["text"].lstrip()
                ends_sent = bool(re.search(r'[.?!:]["”’)\]]*$', ptxt))
                ends_hyph = (
                    bool(ptxt)
                    and ptxt[-1] in ("-", "\u00ad", "\u2010")
                    and len(ptxt) >= 3
                    and ptxt[-2].isalpha()
                )
                starts_lower = bool(btxt) and (btxt[0].islower() or btxt[0] in ",;)]")

                # Case A: cross-column or cross-page join at column boundaries
                cross_col = pb["page"] == b["page"] and pb["column"] == 0 and b["column"] == 1
                cross_page = b["page"] == pb["page"] + 1
                if (
                    b["id"] in first_in_col
                    and pb["id"] in last_in_col
                    and (cross_col or cross_page)
                    and abs(pb["size"] - b["size"]) <= 0.8
                ):
                    pb_last_x1 = pb["lines"][-1][2]
                    pb_c_right = col_right.get((pb["page"], pb["column"]), pb["bbox"][2])
                    last_line_full = pb_last_x1 >= pb_c_right - max(3.0 * body_size, 28.0)
                    b_first_x0 = b["lines"][0][0]
                    b_c_left = col_left.get((b["page"], b["column"]), b["bbox"][0])
                    b_unindented = b_first_x0 <= b_c_left + max(0.6 * body_size, 6.0)
                    if (not ends_sent and (last_line_full or ends_hyph) and (b_unindented or starts_lower)) or (
                        ends_hyph and starts_lower
                    ):
                        prev_grp.append(b)
                        grp_by_block_id[b["id"]] = prev_grp
                        merged = True

                # Case B: same-page, same-column run-in heading first-line block (e.g. eLife p1 Abstract / p2 digest)
                if (
                    not merged
                    and pb["page"] == b["page"]
                    and pb["column"] == b["column"]
                    and len(pb["lines"]) == 1
                    and not ends_sent
                    and -6.0 <= (pb["bbox"][1] - b["bbox"][3]) <= max(1.6 * body_leading, 18.0)
                ):
                    pb_chars = block_chars.get(pb["id"], [])
                    pb_tail_sz = pb_chars[-1]["size"] if pb_chars else pb["size"]
                    pb_tail_font = pb_chars[-1]["font"] if pb_chars else pb["font"]
                    has_runin_lead = (
                        bool(pb_chars)
                        and (
                            pb_chars[0]["size"] > pb_tail_sz + 0.8
                            or (pb_chars[0]["bold"] and not pb_chars[-1]["bold"])
                            or (
                                pb_chars[0]["font"] != pb_tail_font
                                and not _is_math_font(pb_chars[0]["font"])
                                and not _is_mono_font(pb_chars[0]["font"])
                            )
                        )
                        and abs(pb_tail_sz - b["size"]) <= 1.2
                        and (not pb_chars[-1]["bold"] or b["bold"])
                    )
                    if has_runin_lead or (abs(pb["size"] - b["size"]) <= 1.2 and ends_hyph and starts_lower) or (
                        _ABSTRACT_INLINE_RE.match(ptxt)
                        and abs(pb_tail_sz - b["size"]) <= 1.2
                        and (starts_lower or abs(pb["bbox"][0] - b["bbox"][0]) <= 12.0)
                    ):
                        prev_grp.append(b)
                        grp_by_block_id[b["id"]] = prev_grp
                        merged = True

        if not merged:
            new_grp = [b]
            seg_groups.append(new_grp)
            grp_by_block_id[b["id"]] = new_grp

    # 7. Map links to char indices for placeholder construction
    char_to_link: dict[tuple[int, int], int] = {}
    for lk_idx, lk in enumerate(layout.get("links") or []):
        pg = lk["page"]
        for s, e in lk.get("chars") or []:
            for ci in range(s, e):
                if (pg, ci) not in char_to_link:
                    char_to_link[(pg, ci)] = lk_idx

    # Target language length_ratio midpoint
    try:
        lang_info = load_lang(target_lang)
        lr = lang_info.get("length_ratio", [1.0, 1.0])
        ratio_mid = 0.5 * (float(lr[0]) + float(lr[1]))
    except Exception:
        ratio_mid = 1.0

    # 8. Build segments with placeholders and character budgets
    segments: list[dict[str, Any]] = []
    r1_total = 0
    for grp in seg_groups:
        seg_text, ph, r1_cnt, cmap = _build_segment_placeholders_and_text(
            grp, block_chars, char_to_link, body_font, body_family, absorbed_math_chars
        )
        r1_total += r1_cnt
        if not seg_text:
            for b in grp:
                b["translate"] = False
                b["segment"] = None
                b["box"] = None
                kept.append({"block": b["id"], "reason": "empty_text"})
            continue

        sid = f"s{len(segments) + 1:04d}"
        for b in grp:
            b["segment"] = sid

        orig_area = sum(
            max(1.0, (b["bbox"][2] - b["bbox"][0]) * (b["bbox"][3] - b["bbox"][1]))
            for b in grp
        )
        box_area = sum(
            max(1.0, (b["box"]["x1"] - b["box"]["x0"]) * (b["box"]["y1"] - b["box"]["y0_min"]))
            for b in grp
            if b["box"] is not None
        )
        src_chars = sum(len(b["text"]) for b in grp) or len(seg_text)
        budget = max(1, int(round((src_chars / orig_area) * box_area * ratio_mid)))

        segments.append(
            {
                "id": sid,
                "kind": grp[0]["kind"],
                "blocks": [b["id"] for b in grp],
                "text": seg_text,
                "ph": ph,
                "budget": budget,
                "cmap": cmap,
            }
        )

    layout["segments"] = segments
    layout["kept"] = kept
    return layout


def to_segments(layout: dict[str, Any]) -> list[Segment]:
    """Convert classified layout.json segments into existing Segment/Placeholder objects."""
    block_by_id = {b["id"]: b for b in layout.get("blocks") or []}
    out: list[Segment] = []
    for seg in layout.get("segments") or []:
        blks = seg.get("blocks") or []
        first_pg = block_by_id[blks[0]]["page"] if blks and blks[0] in block_by_id else 0
        ph_objs = {
            code: Placeholder(code=code, raw=info.get("raw", ""))
            for code, info in (seg.get("ph") or {}).items()
        }
        out.append(
            Segment(
                id=seg["id"],
                kind=seg["kind"],
                file=f"p{first_pg + 1:03d}",
                start=0,
                end=0,
                text=seg["text"],
                ph=ph_objs,
            )
        )
    return out

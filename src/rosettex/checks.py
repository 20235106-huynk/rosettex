from __future__ import annotations

from bisect import bisect_right
from collections import Counter
from dataclasses import dataclass
import re
import unicodedata

from rosettex.langdata import load_coverage, load_profile
from rosettex.numbers import extract, extract_months
from rosettex.pdf.reconcile import norm_marks
from rosettex.segment import Segment

_TOKEN = re.compile(r"⟦(/?)([a-z])(\d+)⟧")
_WORD = re.compile(r"[^\W\d_]+")

_COVERAGE: dict[str, list[list[int]]] | None = None
_COVERED_RANGES_CACHE: dict[str, tuple[list[int], list[tuple[int, int]]]] = {}
_PARSED_BLOCKS_CACHE: dict[str, list[tuple[int, int]]] = {}


@dataclass
class Issue:
    segment: str
    code: str
    message: str
    level: str = "error"


def _get_coverage() -> dict[str, list[list[int]]]:
    global _COVERAGE
    if _COVERAGE is None:
        _COVERAGE = load_coverage()
    return _COVERAGE


def _get_font_coverage_checker(tgt_lang: dict) -> tuple[list[int], list[tuple[int, int]]]:
    script = tgt_lang.get("script", "")
    profile = tgt_lang.get("profile") or {}
    fonts = tuple(profile.get("fonts", []))
    cache_key = f"{script}:{','.join(fonts)}"
    if cache_key in _COVERED_RANGES_CACHE:
        return _COVERED_RANGES_CACHE[cache_key]

    cov = _get_coverage()
    all_fonts = list(fonts) + [
        "texgyretermes-regular.otf",
        "lmroman10-regular.otf",
        "NotoSerif-Regular.ttf",
        "Tempora-Regular.otf",
        "cmunrm.otf",
        "Arimo-Regular.ttf",
    ]
    all_ranges: list[list[int]] = []
    for f in all_fonts:
        all_ranges.extend(cov.get(f, []))

    if not all_ranges:
        res = ([], [])
        _COVERED_RANGES_CACHE[cache_key] = res
        return res

    sorted_r = sorted(all_ranges, key=lambda r: r[0])
    merged: list[list[int]] = [list(sorted_r[0])]
    for cur_start, cur_end in sorted_r[1:]:
        prev_start, prev_end = merged[-1]
        if cur_start <= prev_end + 1:
            merged[-1][1] = max(prev_end, cur_end)
        else:
            merged.append([cur_start, cur_end])

    starts = [r[0] for r in merged]
    merged_tuples = [(r[0], r[1]) for r in merged]
    res = (starts, merged_tuples)
    _COVERED_RANGES_CACHE[cache_key] = res
    return res


def _is_glyph_covered(cp: int, starts: list[int], merged: list[tuple[int, int]]) -> bool:
    if not starts:
        return True
    idx = bisect_right(starts, cp) - 1
    if idx >= 0 and merged[idx][0] <= cp <= merged[idx][1]:
        return True
    return False


def _get_parsed_blocks(profile: dict) -> list[tuple[int, int]]:
    pid = profile.get("id", "")
    if pid in _PARSED_BLOCKS_CACHE:
        return _PARSED_BLOCKS_CACHE[pid]
    raw_blocks = profile.get("blocks", [])
    parsed = []
    for b in raw_blocks:
        parts = b.split("-")
        if len(parts) == 2:
            parsed.append((int(parts[0], 16), int(parts[1], 16)))
        elif len(parts) == 1:
            val = int(parts[0], 16)
            parsed.append((val, val))
    _PARSED_BLOCKS_CACHE[pid] = parsed
    return parsed


def _in_blocks(c: str, parsed_blocks: list[tuple[int, int]]) -> bool:
    o = ord(c)
    return any(lo <= o <= hi for lo, hi in parsed_blocks)


def _placeholders(seg: Segment, tr: str) -> list[Issue]:
    issues = []
    want = Counter(m.group(0) for m in _TOKEN.finditer(seg.text))
    got = Counter(m.group(0) for m in _TOKEN.finditer(tr))
    missing = sorted(want - got)
    extra = sorted(k for k in got if k not in want)
    dup = sorted(k for k in got if k in want and got[k] > want[k])
    if missing:
        issues.append(Issue(seg.id, "placeholder_missing", "missing " + " ".join(missing)))
    if extra:
        issues.append(Issue(seg.id, "placeholder_extra", "not in the source: " + " ".join(extra)))
    if dup:
        issues.append(Issue(seg.id, "placeholder_duplicated", "more than once: " + " ".join(dup)))
    stack, broken = [], False
    last_open_pos: dict[str, int] = {}
    for m in _TOKEN.finditer(tr):
        closing, kind, num = m.groups()
        if kind != "b":
            continue
        key = f"b{num}"
        if not closing:
            stack.append(key)
            last_open_pos[key] = m.end()
        elif not stack or stack[-1] != key:
            broken = True
            break
        else:
            stack.pop()
            inner = tr[last_open_pos[key]:m.start()]
            if not inner.strip():
                broken = True
                break
    if broken or stack:
        issues.append(Issue(seg.id, "pair_broken", "paired codes ⟦bN⟧…⟦/bN⟧ must stay in order, nested, non-empty"))
    return issues


def _locale(lang: dict) -> dict:
    return {"decimal": lang.get("decimal", "."), "group": lang.get("group", [","])}


def check_translation(
    seg: Segment,
    translation: str,
    src_lang: dict,
    tgt_lang: dict,
    terms: list[tuple[str, str]],
    recorded_hash: str | None,
    budget: int | None = None,
) -> list[Issue]:
    tr = norm_marks(unicodedata.normalize("NFC", translation.strip()))
    issues: list[Issue] = []
    if recorded_hash is not None and recorded_hash != seg.hash:
        return [Issue(seg.id, "stale", "the source text changed since this translation was written")]
    issues += _placeholders(seg, tr)

    # 1. Non-ASCII digits are errors (Arabic-Indic, Devanagari… digits)
    foreign = [c for c in tr if unicodedata.category(c) == "Nd" and not c.isascii() and c not in seg.text]
    if foreign:
        issues.append(
            Issue(
                seg.id,
                "digits",
                "non-ASCII digits not allowed (keep digits ASCII 0-9): " + "".join(sorted(set(foreign))),
            )
        )

    # 2. Number values compared as multisets
    src_loc = _locale(src_lang)
    src_nums = Counter(extract(seg.text, src_loc))
    tgt_nums = Counter(extract(tr, _locale(tgt_lang)))
    month_nums = Counter(extract_months(seg.text, src_lang))

    def _nums_mismatch(sn: Counter) -> bool:
        return bool((sn - tgt_nums) or ((tgt_nums - sn) - month_nums))

    if _nums_mismatch(src_nums):
        alt_loc = {"decimal": ".", "group": [g for g in src_loc["group"] if g not in (".", ",")]}
        if src_loc["decimal"] != "," or _nums_mismatch(Counter(extract(seg.text, alt_loc))):
            issues.append(
                Issue(
                    seg.id,
                    "number_changed",
                    f"numbers differ (write them in the {tgt_lang['code']} style with the same values)",
                )
            )

    # 3. Untranslated detection
    tgt_profile = tgt_lang.get("profile")
    if not tgt_profile and tgt_lang.get("script"):
        tgt_profile = load_profile(tgt_lang["script"])
        tgt_lang["profile"] = tgt_profile
    tgt_script = (tgt_profile.get("id") if tgt_profile else None) or tgt_lang.get("script", "latin")

    src_profile = src_lang.get("profile")
    if not src_profile and src_lang.get("script"):
        src_profile = load_profile(src_lang["script"])
        src_lang["profile"] = src_profile
    src_script = (src_profile.get("id") if src_profile else None) or src_lang.get("script", "latin")

    if tgt_script != src_script:
        # Script ratio among letters outside placeholders and kept terms
        clean_tr = _TOKEN.sub(" ", tr)
        for term, target in sorted(terms, key=lambda kv: len(kv[0]), reverse=True):
            expect = term if target == "=" else target
            if expect:
                clean_tr = clean_tr.replace(expect, " ")
            if term:
                clean_tr = clean_tr.replace(term, " ")
        letters = [c for c in clean_tr if c.isalpha()]
        if len(letters) >= 6 and tgt_profile:
            parsed_blocks = _get_parsed_blocks(tgt_profile)
            target_hits = sum(1 for c in letters if _in_blocks(c, parsed_blocks))
            ratio = target_hits / len(letters)
            min_target_ratio = float(tgt_profile.get("min_target_ratio", 0.3))
            if ratio < min_target_ratio:
                issues.append(
                    Issue(
                        seg.id,
                        "untranslated",
                        f"target script ratio {ratio:.2f} below minimum {min_target_ratio:.2f}",
                    )
                )
        # Non-Latin source (ja, zh, ru …): letters of the source script left in the translation. A Latin source
        # is exempt: names, acronyms and code in Latin letters are normal in any translation.
        if src_script != "latin" and src_profile and tgt_profile:
            src_blocks, tgt_blocks = _get_parsed_blocks(src_profile), _get_parsed_blocks(tgt_profile)
            left = [c for c in letters if _in_blocks(c, src_blocks) and not _in_blocks(c, tgt_blocks)]
            if len(left) >= 3 and len(left) >= 0.05 * len(letters) and not any(
                i.code == "untranslated" for i in issues
            ):
                issues.append(
                    Issue(
                        seg.id,
                        "source_letters_left",
                        f"{len(left)} {src_script} letters left untranslated: " + "".join(dict.fromkeys(left))[:20],
                    )
                )
    else:
        # Function-word test for equal scripts
        words = [w.lower() for w in _WORD.findall(_TOKEN.sub(" ", tr))]
        if src_lang.get("code") != tgt_lang.get("code") and len(words) >= 6:
            src_fw = set(src_lang.get("function_words", [])) - set(tgt_lang.get("function_words", []))
            hits = sum(w in src_fw for w in words)
            if hits >= 3 and hits / len(words) > 0.15:
                issues.append(Issue(seg.id, "untranslated", f"{hits} {src_lang['code']} function words left"))

    # 3b. Missing ZWSP check for zwsp profiles (e.g. Myanmar)
    if tgt_profile and tgt_profile.get("linebreak") == "zwsp":
        parsed_blocks = _get_parsed_blocks(tgt_profile)
        runs = _TOKEN.sub(" ", tr).split()
        for run in runs:
            consec = 0
            for c in run:
                if c == "\u200b":
                    consec = 0
                elif _in_blocks(c, parsed_blocks):
                    consec += 1
                    if consec > 20:
                        issues.append(
                            Issue(
                                seg.id,
                                "missing_zwsp",
                                "spaces-free run longer than 20 characters of the script without U+200B",
                            )
                        )
                        break
                else:
                    consec = 0
            if consec > 20:
                break

    # 4. Glyph coverage
    starts, merged = _get_font_coverage_checker(tgt_lang)
    if starts:
        clean_no_ph = _TOKEN.sub("", tr)
        uncovered = sorted(
            set(
                c
                for c in clean_no_ph
                if c not in " \t\n\r\u200b\u200c\u200d\u200e\u200f"
                and not _is_glyph_covered(ord(c), starts, merged)
            )
        )
        if uncovered:
            issues.append(
                Issue(
                    seg.id,
                    "glyph_uncovered",
                    "characters outside font coverage: " + "".join(uncovered),
                )
            )

    # 5. Kept / fixed terms
    masked, tr_low = seg.text.lower(), tr.lower()
    for term, target in sorted(terms, key=lambda kv: len(kv[0]), reverse=True):
        pat = re.compile(rf"(?<![\w-]){re.escape(term.lower())}(?![\w-])")
        if pat.search(masked):
            masked = pat.sub(lambda m: "\x00" * len(m.group(0)), masked)
            expect = term if target == "=" else target
            if expect.lower() not in tr_low:
                issues.append(Issue(seg.id, "term_mismatch", f"'{term}' must be translated as '{expect}'"))

    # 6. Length ratio warning
    lo_hi = tgt_lang.get("length_ratio")
    if lo_hi and len(lo_hi) == 2:
        lo, hi = float(lo_hi[0]), float(lo_hi[1])
        clean_src = _TOKEN.sub(" ", seg.text).strip()
        clean_dst = _TOKEN.sub(" ", tr).strip()
        if len(clean_src) >= 40:
            ratio = len(clean_dst) / len(clean_src)
            if ratio < lo or ratio > hi:
                issues.append(
                    Issue(
                        seg.id,
                        "length",
                        f"length ratio {ratio:.2f} outside expected [{lo:.2f}, {hi:.2f}]",
                        level="warning",
                    )
                )

    # 7. Character budget warning (PDF route, spec §7.7: > 25% over)
    if budget and budget > 0:
        clean_dst = _TOKEN.sub(" ", tr).strip()
        if len(clean_dst) > 1.25 * budget:
            issues.append(
                Issue(
                    seg.id,
                    "budget",
                    f"translation is {len(clean_dst)} chars, >25% over budget {budget}",
                    level="warning",
                )
            )

    return issues


def check_all(
    segs: list[Segment],
    translations: dict[str, str],
    hashes: dict[str, str],
    src_lang: dict,
    tgt_lang: dict,
    terms: list[tuple[str, str]],
    budgets: dict[str, int] | None = None,
) -> list[Issue]:
    out = []
    for s in segs:
        tr = translations.get(s.id, "")
        if not tr.strip():
            out.append(Issue(s.id, "missing", "not translated yet"))
            continue
        budget = budgets.get(s.id) if budgets else None
        out += check_translation(s, tr, src_lang, tgt_lang, terms, hashes.get(s.id), budget=budget)
    return out

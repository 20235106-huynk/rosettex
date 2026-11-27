from collections import Counter
import re
from pathlib import Path

from rosettex.build.preamble import is_rosettex_font

_PAGES = re.compile(r"Output written on .*?\((\d+) pages?")
_OVERFULL = re.compile(r"Overfull \\hbox \((\d+(?:\.\d+)?)pt too wide\)")
_MISSING = re.compile(r"Missing character: There is no")
_UNDEF = re.compile(r"(?:Reference|Citation) `[^']+' on page \S+ undefined")
_ERROR = re.compile(r"^(?:! .*|.+:\d+: .*)$", re.M)
_FONT_SHAPE = re.compile(
    r"Font shape `([^']{1,160})'\s*undefined(?:\s*\(Font\)\s*using `([^']{1,160})'\s*instead)?",
    re.S,
)


def _scan_group(text: str, start: int) -> tuple[str, int] | None:
    i = text.find("{", start)
    if i < 0:
        return None
    depth = 0
    j = i
    while j < len(text):
        c = text[j]
        if c == "\\":
            j += 2
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[i + 1 : j], j + 1
        j += 1
    return None


class _LabelScanner:
    _ANCHOR = re.compile(r"^([a-z]+)\.")

    def findall(self, text: str) -> list[str]:
        results: list[str] = []
        pos = 0
        while True:
            idx = text.find(r"\newlabel", pos)
            if idx < 0:
                break
            pos = idx + 9
            g1 = _scan_group(text, pos)
            if not g1:
                continue
            pos = g1[1]
            g2 = _scan_group(text, pos)
            if not g2:
                continue
            pos = g2[1]
            inner = g2[0]
            sub_pos = 0
            subgroups: list[str] = []
            while True:
                sub = _scan_group(inner, sub_pos)
                if not sub:
                    break
                subgroups.append(sub[0])
                sub_pos = sub[1]
                if len(subgroups) == 4:
                    break
            if len(subgroups) >= 4:
                m = self._ANCHOR.match(subgroups[3])
                if m:
                    results.append(m.group(1))
        return results


_LABEL = _LabelScanner()


def _parse_font_shapes(text: str) -> dict:
    """Count and classify `Font shape ... undefined` substitutions into rosettex-caused vs paper's own.

    A substitution is rosettex-caused when:
    1. Its family is a font rosettex itself declared (checked against preamble.ROSETTEX_FONTS), or
    2. It is under Unicode `TU` encoding and the family changes (e.g. `TU/ptm/m/n -> TU/lmr/m/n`,
       caused by engine switch when no TU `.fd` exists for that family).
    Any other substitution (e.g. math or paper-native shapes) is reported as a warning.
    """
    pairs: list[tuple[str, str]] = []
    tu_missing_fams: set[str] = set()
    for m in _FONT_SHAPE.finditer(text):
        src = re.sub(r"[\r\n]+", "", m.group(1)).strip()
        dst = re.sub(r"[\r\n]+", "", m.group(2) or "").strip()
        pairs.append((src, dst))
        sp, dp = src.split("/"), dst.split("/")
        if len(sp) >= 2 and len(dp) >= 2 and sp[0] == "TU" and sp[1] != dp[1]:
            tu_missing_fams.add(sp[1])

    rx_counts: Counter[str] = Counter()
    other_counts: Counter[str] = Counter()
    for src, dst in pairs:
        sp = src.split("/")
        enc = sp[0] if sp else ""
        fam = sp[1] if len(sp) >= 2 else ""
        label = f"{src}→{dst}" if dst else src
        if is_rosettex_font(fam) or (enc == "TU" and fam in tu_missing_fams):
            rx_counts[label] += 1
        else:
            other_counts[label] += 1

    return {
        "font_shape_subs": len(pairs),
        "font_shape_rosettex": sum(rx_counts.values()),
        "font_shape_other": sum(other_counts.values()),
        "font_shape_rosettex_detail": [f"{n}× {k}" for k, n in rx_counts.most_common(10)],
        "font_shape_other_detail": [f"{n}× {k}" for k, n in other_counts.most_common(10)],
    }


def parse_log(text: str) -> dict:
    pages = _PAGES.findall(text)
    over = [float(x) for x in _OVERFULL.findall(text)]
    return {
        "pages": int(pages[-1]) if pages else None,
        "errors": len(_ERROR.findall(text)),
        "missing_chars": len(_MISSING.findall(text)),
        "overfull": len(over),
        "overfull_max_pt": max(over) if over else 0.0,
        "undefined": len(_UNDEF.findall(text)),
        **_parse_font_shapes(text),
    }


def parse_aux(text: str) -> dict:
    counts = {"figure": 0, "table": 0, "equation": 0, "section": 0}
    for kind in _LABEL.findall(text):
        if kind in counts:
            counts[kind] += 1
    counts["bibitems"] = text.count("\\bibcite{")
    return counts


def _read(path) -> str:
    return Path(path).read_text(encoding="utf-8", errors="replace") if path and Path(path).exists() else ""


def stats_of(result: dict) -> dict:
    return {"log": parse_log(_read(result.get("log"))), "aux": parse_aux(_read(result.get("aux")))}


def compare(baseline: dict, translated: dict, max_page_ratio: float = 1.6) -> dict:
    b, t = baseline, translated
    problems, warnings = [], []
    if t["log"]["errors"]:
        problems.append({"code": "tex_errors", "message": f"{t['log']['errors']} TeX errors"})
    if t["log"]["missing_chars"] > 0:
        problems.append(
            {
                "code": "missing_chars",
                "message": f"{t['log']['missing_chars']} missing characters (baseline {b['log']['missing_chars']})",
            }
        )
    if t["log"]["undefined"] > 0:
        problems.append(
            {
                "code": "undefined_refs",
                "message": f"{t['log']['undefined']} undefined references (baseline {b['log']['undefined']})",
            }
        )
    rx_subs = t["log"].get("font_shape_rosettex", 0)
    if rx_subs > 0:
        detail = ", ".join(t["log"].get("font_shape_rosettex_detail") or [])
        problems.append(
            {
                "code": "font_shape_substituted",
                "message": f"{rx_subs} rosettex font shape substitutions" + (f": {detail}" if detail else ""),
            }
        )
    other_subs = t["log"].get("font_shape_other", 0)
    if other_subs > 0:
        detail = ", ".join(t["log"].get("font_shape_other_detail") or [])
        warnings.append(
            {
                "code": "font_shape_warning",
                "message": f"{other_subs} paper font shape substitutions" + (f": {detail}" if detail else ""),
            }
        )
    changed = {k: (b["aux"][k], t["aux"][k]) for k in b["aux"] if b["aux"][k] != t["aux"].get(k)}
    if changed:
        problems.append(
            {"code": "count_changed", "message": ", ".join(f"{k}: {x}→{y}" for k, (x, y) in changed.items())}
        )
    if t["log"]["overfull"] > b["log"]["overfull"]:
        warnings.append(
            {
                "code": "overfull_grew",
                "message": (
                    f"overfull lines {b['log']['overfull']}→{t['log']['overfull']}, "
                    f"worst {t['log']['overfull_max_pt']}pt"
                ),
            }
        )
    bp, tp = b["log"]["pages"], t["log"]["pages"]
    if bp and tp and tp > bp:
        warnings.append(
            {"code": "pages_grew", "message": f"pages {bp}→{tp}"}
            if tp <= bp * max_page_ratio
            else {"code": "pages_grew", "message": f"pages {bp}→{tp} (above ×{max_page_ratio})"}
        )
    return {"problems": problems, "warnings": warnings, "stats": {"baseline": b, "translated": t}}


def page_ratio_for(lang: dict) -> float:
    profile = lang.get("profile") or {}
    if "page_ratio" in profile:
        return float(profile["page_ratio"])
    return float(lang.get("length_ratio", [0.8, 1.6])[1])


def report(build_result: dict, lang: dict) -> dict:
    ratio = page_ratio_for(lang)
    cmp = compare(stats_of(build_result["baseline"]), stats_of(build_result["translated"]), ratio)
    return {
        "ok": build_result["ok"] and not cmp["problems"],
        "engine": build_result["engine"],
        "pdf": build_result["pdf"],
        "ladder": build_result["ladder"],
        "kept_source": build_result["kept_source"],
        **cmp,
    }

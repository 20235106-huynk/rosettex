"""Post-build check (SG 11.3): log, characters, references, object counts, length ratio."""
from __future__ import annotations

import re

from ..checks import nfc
from ..pdf import full_text, line_height_total

MATH_CHARS = re.compile("[\U0001D400-\U0001D7FF]")
MISS_RE = re.compile(r"Missing character: There is no (.)(?: \(U\+([0-9A-F]+)\))? in font ([^!]*)!")
OVER_RE = re.compile(r"Overfull \\hbox \((\d+(?:\.\d+)?)pt too wide\)")


def parse_missing(log: str) -> list[dict]:
    out = []
    for ch, cp, font in MISS_RE.findall(log):
        name = re.match(r"\[?([^\]:]*)", font.replace("\n", "")).group(1).strip()  # TeX wraps log lines at 79 chars
        item = {"char": ch, "codepoint": "U+" + (cp or f"{ord(ch):04X}"), "font": name.rsplit("/", 1)[-1]}
        if item not in out:
            out.append(item)
    return out


def overfull_count(log: str, min_pt: float = 10.0) -> int:
    return sum(float(x) > min_pt for x in OVER_RE.findall(log))


def postcheck(work, pdf, log, tex, doc, tr, pack, fallback) -> dict:
    problems = []
    if re.search(r"^! ", log, re.M):
        problems.append("LaTeX log has errors")
    missing_glyphs = parse_missing(log)
    for font in dict.fromkeys(m["font"] for m in missing_glyphs):
        chars = "".join(sorted({m["char"] for m in missing_glyphs if m["font"] == font}))
        problems.append(f"font {font} is missing characters: {chars}")
    if re.search(r"LaTeX Warning: (?:Reference|Citation) `[^']*'[\s\S]{0,80}?undefined", log):
        problems.append("there are undefined references/citations")
    text = nfc(full_text(pdf))
    if "??" in text or "[?]" in text:
        problems.append("PDF contains '??' or '[?]'")
    has_math = any(b["kind"] == "equation" for b in doc["blocks"]) or any(
        p["tex"].startswith("$") for u in doc["units"] for p in u["ph"].values())
    if has_math and not MATH_CHARS.search(text):
        problems.append("no math characters found in the PDF text layer")
    missing = (set(nfc("".join(tr.values()))) & set(pack.get("check_chars", ""))) - set(text)
    if missing:
        problems.append("PDF is missing characters: " + "".join(sorted(missing)))
    # a block that fell back to the last-resort plain-text display no longer has links; the rest is still checked strictly
    expected_links = sum(1 for u in doc["units"] if not (u["block"] in fallback and u["type"] != "caption") for n in u["ph"] if n[0] in "cr")
    links = len(re.findall(r"\\(?:ref|eqref|cite)\{", tex))
    if links != expected_links:
        problems.append(f"internal link count {links} ≠ {expected_links}")
    counts = doc["source"].get("counts", {})
    fb_tags = sum(1 for b in doc["blocks"] if b["kind"] == "equation" and b.get("tag") and b["id"] in fallback)
    have = {"tables": len(re.findall(r"\\begin\{table\*?\}", tex)),
            "figures": len(re.findall(r"\\begin\{figure\*?\}", tex)),
            "tagged_equations": tex.count("\\tag{") + fb_tags,
            "references": tex.count("\\bibitem{")}
    for k, v in counts.items():
        if have.get(k) != v:
            problems.append(f"count of {k} in the build {have.get(k)} ≠ source {v}")
    layout = pack["profile"]["layout"]
    if "\u25cc" in text and "\u25cc" not in "".join(tr.values()) and layout != "rtl":
        problems.append("dotted circle in the PDF")
    n = overfull_count(log)
    if pack["profile"]["linebreak"] in ("cjk", "zwsp") and n > 5:
        problems.append(f"{n} overfull lines: line breaking for this script may not work")
    ratio = line_height_total(pdf) / max(line_height_total(work.src_pdf), 1e-9)
    # Measured and reported, never a failure (SG decision 5 amended 2026-11-18): languages are naturally longer or
    # shorter than English (0.66 Amharic … 2.0 Myanmar on one paper). Only a ratio beyond what the script usually
    # needs is a warning, a hint of a layout fault (wrong font size, duplicated text).
    warnings = []
    limit = pack.get("max_length_ratio") or pack["profile"].get("max_length_ratio", 1.3)
    if ratio > limit:
        warnings.append(f"content length ratio {ratio:.2f} > {limit} (usual for this language); check the layout")
    return {"ok": not problems, "problems": problems, "warnings": warnings, "length_ratio": round(ratio, 3),
            "missing_glyphs": missing_glyphs}

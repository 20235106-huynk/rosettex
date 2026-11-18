"""Post-build check (SG 11.3): log, characters, references, object counts, length ratio."""
from __future__ import annotations

import re

from ..checks import nfc
from ..pdf import full_text, line_height_total

MATH_CHARS = re.compile("[\U0001D400-\U0001D7FF]")


def postcheck(work, pdf, log, tex, doc, tr, pack, fallback) -> dict:
    problems = []
    if re.search(r"^! ", log, re.M):
        problems.append("LaTeX log has errors")
    if "Missing character" in log:
        miss = sorted(set(re.findall(r"Missing character: There is no (.)", log)))
        problems.append("font is missing characters: " + "".join(miss))
    if re.search(r"LaTeX Warning: (?:Reference|Citation) `[^']*' .*undefined", log):
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
    ratio = line_height_total(pdf) / max(line_height_total(work.src_pdf), 1e-9)
    if ratio > 1.3:
        problems.append(f"content length ratio {ratio:.2f} > 1.3")
    return {"ok": not problems, "problems": problems, "length_ratio": round(ratio, 3)}

"""Kiểm tra sau dựng (SG 11.3): log, ký tự, tham chiếu, số đối tượng, tỉ lệ độ dài."""
from __future__ import annotations

import re

from ..checks import nfc
from ..pdf import full_text, line_height_total

MATH_CHARS = re.compile("[\U0001D400-\U0001D7FF]")


def postcheck(work, pdf, log, tex, doc, tr, pack, fallback) -> dict:
    problems = []
    if re.search(r"^! ", log, re.M):
        problems.append("log LaTeX có lỗi")
    if "Missing character" in log:
        miss = sorted(set(re.findall(r"Missing character: There is no (.)", log)))
        problems.append("font thiếu ký tự: " + "".join(miss))
    if re.search(r"LaTeX Warning: (?:Reference|Citation) `[^']*' .*undefined", log):
        problems.append("có tham chiếu/trích dẫn chưa định nghĩa")
    text = nfc(full_text(pdf))
    if "??" in text or "[?]" in text:
        problems.append("PDF có '??' hoặc '[?]'")
    has_math = any(b["kind"] == "equation" for b in doc["blocks"]) or any(
        p["tex"].startswith("$") for u in doc["units"] for p in u["ph"].values())
    if has_math and not MATH_CHARS.search(text):
        problems.append("không thấy ký tự toán trong lớp chữ PDF")
    missing = (set(nfc("".join(tr.values()))) & set(pack.get("check_chars", ""))) - set(text)
    if missing:
        problems.append("PDF thiếu ký tự: " + "".join(sorted(missing)))
    expected_links = sum(1 for u in doc["units"] for n in u["ph"] if n[0] in "cr")
    links = len(re.findall(r"\\(?:ref|eqref|cite)\{", tex))
    if links != expected_links:
        problems.append(f"số liên kết nội bộ {links} ≠ {expected_links}")
    counts = doc["source"].get("counts", {})
    fb_tags = sum(1 for b in doc["blocks"] if b["kind"] == "equation" and b.get("tag") and b["id"] in fallback)
    have = {"tables": len(re.findall(r"\\begin\{table\*?\}", tex)),
            "figures": len(re.findall(r"\\begin\{figure\*?\}", tex)),
            "tagged_equations": tex.count("\\tag{") + fb_tags,
            "references": tex.count("\\bibitem{")}
    for k, v in counts.items():
        if have.get(k) != v:
            problems.append(f"số {k} trong bản dựng {have.get(k)} ≠ bản gốc {v}")
    ratio = line_height_total(pdf) / max(line_height_total(work.src_pdf), 1e-9)
    if ratio > 1.3:
        problems.append(f"tỉ lệ độ dài nội dung {ratio:.2f} > 1,3")
    return {"ok": not problems, "problems": problems, "length_ratio": round(ratio, 3)}

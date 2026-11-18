"""Giao thức cho agent: một nguồn (protocol.md), in bằng `rosettex guide`, sinh SKILL.md và AGENTS.md."""
from importlib.resources import files

from ..checks import load_pack

# Mô tả kích hoạt: tiếng Anh, tự đứng được, dùng chung cho SKILL.md (frontmatter) và AGENTS.md (đầu file)
# để harness chọn skill theo mô tả lẫn harness chỉ đọc file quy tắc đều biết khi nào dùng. Giữ <= 1024 ký tự, một dòng.
DESCRIPTION = (
    "Translate a whole scientific paper PDF (text layer required) into another language and rebuild it as a PDF "
    "that keeps formulas, tables, numbers, citations and clickable cross-references. Use when the user asks to "
    "translate a paper, article or research PDF, in any language (e.g. 'translate this paper to Vietnamese'). "
    "Needs a shell and file read/write; you do the translating and the rosettex CLI does extraction, checking and "
    "typesetting. Scanned or image-only PDFs are not supported yet."
)

FRONTMATTER = f"""---
name: rosettex
description: {DESCRIPTION}
---

Chạy `rosettex guide` để xem bản mới nhất của giao thức dưới đây, rồi làm theo.

"""

AGENTS_INTRO = f"**When to use:** {DESCRIPTION}\n\n"


def text(lang: str = "vi") -> str:
    src = (files("rosettex") / "protocol" / "protocol.md").read_text(encoding="utf-8")
    rules = "\n".join(f"- {r}" for r in load_pack(lang)["rules"])
    return src.replace("{{RULES}}", rules)


def skill_md() -> str:
    return FRONTMATTER + text("vi")


def agents_md() -> str:
    return AGENTS_INTRO + text("vi")

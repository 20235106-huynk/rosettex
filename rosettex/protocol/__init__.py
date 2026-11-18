"""Giao thức cho agent: một nguồn (protocol.md), in bằng `rosettex guide`, sinh SKILL.md và AGENTS.md."""
from importlib.resources import files

from ..checks import load_pack

FRONTMATTER = """---
name: rosettex
description: Dịch trọn bài báo khoa học PDF sang ngôn ngữ khác (hỏi người dùng ngôn ngữ đích nếu chưa nêu), giữ công thức, bảng, số liệu và trích dẫn, dựng lại PDF. Dùng khi người dùng nói "dịch bài báo", "dịch file PDF này sang…", "dịch paper", "translate this paper/PDF to…".
---

Chạy `rosettex guide` để xem bản mới nhất của giao thức dưới đây, rồi làm theo.

"""


def text(lang: str = "vi") -> str:
    src = (files("rosettex") / "protocol" / "protocol.md").read_text(encoding="utf-8")
    rules = "\n".join(f"- {r}" for r in load_pack(lang)["rules"])
    return src.replace("{{RULES}}", rules)


def skill_md() -> str:
    return FRONTMATTER + text("vi")


def agents_md() -> str:
    return text("vi")

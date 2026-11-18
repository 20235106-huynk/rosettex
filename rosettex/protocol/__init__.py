"""Agent protocol: a single source (protocol.md), printed by `rosettex guide`, generates SKILL.md and AGENTS.md."""
from importlib.resources import files

from ..checks import load_pack

# Activation description: self-contained, shared by SKILL.md (frontmatter) and AGENTS.md (top of file)
# so both a harness that picks skills by description and one that only reads a rules file know when to use it. Keep <= 1024 characters, one line.
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

Run `rosettex guide` to see the latest version of the protocol below, then follow it.

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

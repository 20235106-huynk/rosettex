"""Agent protocol: a single source (protocol.md), printed by `rosettex guide`, generates SKILL.md and AGENTS.md."""
from importlib.resources import files

from ..checks import load_pack

# Activation description: self-contained, shared by SKILL.md (frontmatter) and AGENTS.md (top of file)
# so both a harness that picks skills by description and one that only reads a rules file know when to use it. Keep <= 1024 characters, one line.
DESCRIPTION = (
    "Translate a whole scientific paper PDF (text layer required) into another language and rebuild it as a PDF "
    "that keeps formulas, tables, numbers, citations and clickable cross-references. Use when the user asks to "
    "translate a paper, article or research PDF, in any language (e.g. 'translate this paper into Japanese', '… into Arabic'). "
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


GENERIC_RULES = (
    "These rules hold for every target language. Each batch from `rosettex next` also carries `rules`: the rules of "
    "the target language (digits, number style, word separators, spacing). Follow them too; where they are more "
    "precise, they win. `rosettex guide --lang <code>` prints them in full.\n"
)


def text(lang: str | None = None) -> str:
    """The protocol; with a language code, its translation rules are those of that language's pack."""
    src = (files("rosettex") / "protocol" / "protocol.md").read_text(encoding="utf-8")
    pack = load_pack(lang or "default")
    rules = "\n".join(f"- {r}" for r in pack["rules"])
    if lang is None:
        rules = GENERIC_RULES + "\n" + rules + "\n- Write natural academic prose in the target language; add no translator notes."
    return src.replace("{{RULES}}", rules)


def skill_md() -> str:
    return FRONTMATTER + text()


def agents_md() -> str:
    return AGENTS_INTRO + text()

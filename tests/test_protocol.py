from pathlib import Path

from rosettex.protocol import agents_md, skill_md, text

ROOT = Path(__file__).parent.parent


def test_guide_has_every_step_and_rules():
    t = text("vi")
    for cmd in ("rosettex extract", "rosettex status", "rosettex next", "--outline", "rosettex terms",
                "rosettex accept", "rosettex validate", "rosettex render", "next_action"):
        assert cmd in t
    assert "0.05" in t and "{{RULES}}" not in t
    assert "&&" not in t and " | " not in t


def test_generated_files_in_sync():
    assert (ROOT / "skill" / "SKILL.md").read_text(encoding="utf-8") == skill_md()
    assert (ROOT / "skill" / "AGENTS.md").read_text(encoding="utf-8") == agents_md()


def test_skill_frontmatter_triggers():
    head = skill_md().split("---")[1]
    assert "name: rosettex" in head and "dịch bài báo" in head and "translate this paper" in head

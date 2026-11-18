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
    assert "name: rosettex" in head and "translate this paper" in head
    desc = [l for l in head.splitlines() if l.startswith("description: ")][0]
    assert len(desc) - len("description: ") <= 1024 and "\n" not in desc
    assert skill_md().count("description:") == 1


def test_agents_md_states_when_to_use():
    assert agents_md().startswith("**When to use:** Translate a whole scientific paper PDF")


def test_target_language_is_asked_not_defaulted():
    from rosettex.protocol import skill_md, text
    assert "Vietnamese by default" not in skill_md().split("---")[1] and "by default" not in skill_md().split("---")[1]
    assert "Do not guess the target language" in text("vi")

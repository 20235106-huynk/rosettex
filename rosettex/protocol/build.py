"""Write skill/SKILL.md and skill/AGENTS.md from protocol.md. Run: uv run python -m rosettex.protocol.build"""
from pathlib import Path

from . import agents_md, skill_md


def main() -> None:
    out = Path(__file__).resolve().parents[2] / "skill"
    out.mkdir(exist_ok=True)
    (out / "SKILL.md").write_text(skill_md(), encoding="utf-8", newline="\n")
    (out / "AGENTS.md").write_text(agents_md(), encoding="utf-8", newline="\n")
    print(out)


if __name__ == "__main__":
    main()

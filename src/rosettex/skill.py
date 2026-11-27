import argparse
from importlib import resources
import os
from pathlib import Path
import re
import shutil
import sys

from rosettex import __version__


def split_frontmatter(text: str) -> tuple[str, str]:
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            frontmatter = f"---{parts[1]}---"
            body = parts[2].lstrip("\r\n")
            return frontmatter, body
    return "", text


def render_skill(version: str) -> str:
    head_tmpl = (
        resources.files("rosettex")
        .joinpath("protocol", "skill_head.md")
        .read_text(encoding="utf-8")
    )
    head = head_tmpl.format(version=version).rstrip()
    instr_tmpl = (
        resources.files("rosettex.data")
        .joinpath("instructions.md")
        .read_text(encoding="utf-8")
    )
    generic_instr = instr_tmpl.format(
        lang="<lang>", work="<work>", sheets="read/*.md"
    ).rstrip()
    return f"{head}\n\n{generic_instr}\n"


def render_guide() -> str:
    full = render_skill(__version__)
    _, body = split_frontmatter(full)
    return body


def get_skill_locations(home: Path | None = None) -> list[Path]:
    base = home if home is not None else Path(os.environ.get("HOME", str(Path.home())))
    return [
        base / ".agents" / "skills" / "rosettex",
        base / ".claude" / "skills" / "rosettex",
        base / ".gemini" / "config" / "skills" / "rosettex",
    ]


def _copy_tree_or_write(loc: Path, content: str) -> None:
    loc.mkdir(parents=True, exist_ok=True)
    try:
        cand = resources.files("rosettex.data").joinpath("skill", "rosettex")
        if cand.is_dir():
            for item in cand.iterdir():
                if item.name == "SKILL.md":
                    continue
                dest = loc / item.name
                if item.is_file():
                    shutil.copy2(item, dest)
                elif item.is_dir():
                    shutil.copytree(item, dest, dirs_exist_ok=True)
    except Exception:
        pass
    (loc / "SKILL.md").write_text(content, encoding="utf-8")


def install_skill(
    remove: bool = False, force: bool = False, home: Path | None = None
) -> list[dict]:
    locations = get_skill_locations(home)
    results: list[dict] = []

    if remove:
        for loc in locations:
            if not loc.exists():
                results.append({"path": str(loc), "status": "skipped", "reason": "not installed"})
                continue
            skill_file = loc / "SKILL.md"
            if not skill_file.is_file():
                results.append(
                    {
                        "path": str(loc),
                        "status": "skipped",
                        "reason": "missing SKILL.md",
                    }
                )
                continue
            text = skill_file.read_text(encoding="utf-8")
            name_match = re.search(r"^\s*name:\s*rosettex\s*$", text, re.MULTILINE)
            ver_match = re.search(r"^\s*rosettex-version:\s*", text, re.MULTILINE)
            if name_match and ver_match:
                shutil.rmtree(loc)
                results.append({"path": str(loc), "status": "removed"})
            else:
                results.append(
                    {
                        "path": str(loc),
                        "status": "skipped",
                        "reason": "not a rosettex skill (missing name: rosettex or rosettex-version)",
                    }
                )
        return results

    content = render_skill(__version__)
    for loc in locations:
        skill_file = loc / "SKILL.md"
        if not loc.exists():
            _copy_tree_or_write(loc, content)
            results.append({"path": str(loc), "status": "installed"})
            continue

        if not skill_file.is_file():
            if not force:
                results.append(
                    {
                        "path": str(loc),
                        "status": "skipped",
                        "reason": "existing directory has no SKILL.md (use --force to overwrite)",
                    }
                )
                continue
            _copy_tree_or_write(loc, content)
            results.append({"path": str(loc), "status": "installed"})
            continue

        text = skill_file.read_text(encoding="utf-8")
        name_match = re.search(r"^\s*name:\s*rosettex\s*$", text, re.MULTILINE)
        ver_match = re.search(
            r"^\s*rosettex-version:\s*[\"']?([^\"'\n]+)[\"']?", text, re.MULTILINE
        )
        existing_version = ver_match.group(1).strip() if ver_match else None

        if not name_match:
            if not force:
                results.append(
                    {
                        "path": str(loc),
                        "status": "skipped",
                        "reason": "not a rosettex skill (use --force to overwrite)",
                    }
                )
                continue
        elif existing_version != __version__:
            if not force:
                results.append(
                    {
                        "path": str(loc),
                        "status": "skipped",
                        "reason": f"version mismatch: {existing_version or 'absent'} (use --force to overwrite)",
                    }
                )
                continue
        else:
            if text == content:
                results.append({"path": str(loc), "status": "up to date"})
                continue

        _copy_tree_or_write(loc, content)
        results.append({"path": str(loc), "status": "updated"})

    return results


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="rosettex.skill")
    parser.add_argument("--check", action="store_true", help="exit 1 when a copy differs from the source (CI)")
    a = parser.parse_args(argv)

    content = render_skill(__version__)
    root = Path(__file__).resolve().parents[2]
    # The four generated copies: the repo skill, the package data, the Claude Code plugin skill and the Gemini
    # context file (same text without frontmatter)
    copies = {
        root / "skill" / "rosettex" / "SKILL.md": content,
        root / "src" / "rosettex" / "data" / "skill" / "rosettex" / "SKILL.md": content,
        root / "plugins" / "claude-code" / "skills" / "rosettex" / "SKILL.md": content,
        root / "plugins" / "gemini" / "ROSETTEX.md": render_guide(),
    }
    if a.check:
        stale = [str(p) for p, text in copies.items() if not p.is_file() or p.read_text(encoding="utf-8") != text]
        for p in stale:
            print(f"stale: {p} (run: uv run python -m rosettex.skill)", file=sys.stderr)
        return 1 if stale else 0
    for p, text in copies.items():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    print(f"Wrote {len(copies)} skill copies ({len(content)} characters)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

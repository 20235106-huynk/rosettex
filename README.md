# rosettex

A tool for an **agent in a harness** (Claude Code, Codex, …) to translate a whole scientific PDF paper. rosettex does not translate by itself:
it extracts the content (MinerU), splits it into batches with placeholders, checks the translation and rebuilds the PDF with LaTeX, with formulas and tables as real characters.
Slice 1 supports PDFs with a text layer.

## Installation (slice 1: manual install)

```bash
uv tool install <path-to-rosettex-repo>            # or: pipx install <path>
uv venv --python 3.12 ~/.rosettex/mineru-venv
uv pip install --python ~/.rosettex/mineru-venv "mineru>=4,<5"
~/.rosettex/mineru-venv/bin/mineru-kit models download --tier standard --source modelscope
#   Windows: %USERPROFILE%\.rosettex\mineru-venv\Scripts\mineru-kit.exe models download --tier standard
#   Download hangs: stop and rerun (downloads resume), or switch to --source huggingface
uv run python scripts/get_tectonic.py               # Tectonic 0.17.0 → ~/.rosettex/bin (skip if xelatex is already installed)
```

Optional environment variables (the user sets them once): `ROSETTEX_MINERU` (path to `mineru-kit`), `ROSETTEX_TEX` (path to `tectonic`/`xelatex`), `ROSETTEX_HOME` (default `~/.rosettex`).

## Hooking into a harness

- **Claude Code:** copy `skill/SKILL.md` to `~/.claude/skills/rosettex/SKILL.md`.
- **Codex, OpenCode, Cursor and harnesses that read AGENTS.md:** copy the contents of `skill/AGENTS.md` into your `AGENTS.md` (the file starts with a "When to use" line so the agent knows when to apply it).
- **Gemini CLI:** copy the contents of `skill/AGENTS.md` into `GEMINI.md` (or name the context file according to your configuration).
- **Cline:** copy the contents of `skill/AGENTS.md` into `.clinerules`.
- **Other harnesses:** tell the agent "run `rosettex guide` and follow it".

After that, just say: *"Translate the paper ~/papers/attention.pdf into Vietnamese, save it in ~/translated/"*.

## Development

```bash
uv sync
uv run pytest -q                                   # ROSETTEX_REAL=1 also runs end-to-end with real MinerU
uv run python -m rosettex.protocol.build           # after editing rosettex/protocol/protocol.md
```

Specs: `docs/superpowers/specs/`. Plans: `docs/superpowers/plans/`.

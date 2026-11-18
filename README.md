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

## Languages and fonts

`rosettex` ships 21 language packs in 15 scripts (only `vi` is tested; the others are reported as "not tested"):

| Script | Packs |
|---|---|
| Latin | `vi`, `fr` |
| Cyrillic | `ru`, `uk` |
| Han | `zh-Hans`, `zh-Hant` |
| Kana | `ja` |
| Hangul | `ko` |
| Arabic | `ar`, `fa`, `ur` (laid out right to left) |
| Devanagari | `hi`, `mr` |
| Bengali, Telugu, Tamil, Gujarati, Kannada | `bn`, `te`, `ta`, `gu`, `kn` |
| Thai, Ethiopic, Myanmar | `th`, `am`, `my` |

Other language codes map to their script through `rosettex/langpacks/languages.json` (for example `bg` → Cyrillic, `ps` → Arabic, `es` → Latin) and use generic checks (`pack_found: false`). An unknown code is reported with `script_known: false`.

Every non-Latin font ships with rosettex (subset Noto fonts, SIL OFL 1.1, in `rosettex/fonts/`, with the licence texts in `rosettex/fonts/LICENSES/` and the exact sources in `rosettex/fonts/MANIFEST.json`); no font install is needed. Rebuild them with `uv run --with fonttools==4.68.0 python scripts/build_fonts.py --cache <dir>`.

Known limitation: Arabic text copied out of the PDF can be garbled (a limitation of XeTeX); the printed text is correct.

## Development

```bash
uv sync
uv run pytest -q                                   # ROSETTEX_REAL=1 also runs end-to-end with real MinerU
uv run python -m rosettex.protocol.build           # after editing rosettex/protocol/protocol.md
```

Specs: `docs/superpowers/specs/`. Plans: `docs/superpowers/plans/`.

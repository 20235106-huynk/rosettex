# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Status

**Slice 1 is implemented and merged into `main`** (end-to-end flow for text-layer PDFs; `vi` and `default` language packs; 233 tests pass, 2 are skipped because they need real MinerU and `ROSETTEX_REAL=1`). Its acceptance run on 2026-11-18 is recorded in `docs/superpowers/notes/2026-11-18-slice1-acceptance.md`. Read in this order before doing anything:

1. `docs/superpowers/specs/2026-11-18-rosettex-design.md`: the original spec (called **SG**). Section 4.1 holds the facts verified by spikes, section 19 holds the decisions already made. If you want to change something already decided, ask the user first.
2. `docs/superpowers/specs/2026-11-18-rosettex-slice1-design.md`: the slice 1 spec (implemented).
3. `docs/superpowers/plans/2026-11-18-rosettex-slice1.md`: the 15-task plan of slice 1 (done), with an **Amendment** at the top listing what slice 1 assumes about Vietnamese and slice 1b must generalize.
4. `docs/superpowers/notes/2026-11-18-slice1-acceptance.md`: the acceptance record and the open issues.

Phase 1 is built as **vertical slices**, each with its own spec and plan:
- Slice 1: the end-to-end flow for PDFs with a text layer. **Done.**
- **Slice 1b: script coverage. Done** (21 packs in 15 scripts, all `tested: false` except `vi`; fonts for the non-Latin scripts ship inside the package, `rosettex/fonts/`). Spec `docs/superpowers/specs/2026-11-18-rosettex-slice1b-design.md`, plan `docs/superpowers/plans/2026-11-18-rosettex-slice1b.md`, record `docs/superpowers/notes/2026-11-18-slice1b-acceptance.md` (reader checks per pack are open; the real-agent run with MinerU was not done). 233 tests pass, 2 skipped. Open items of slice 1b: `docs/superpowers/notes/2026-11-18-slice1b-open-items.md` (reader check, real agent run, threshold tuning, XeLaTeX/macOS/Windows).
- **Slice 2 (next): `review`.**
- Slice 3: `setup`, `doctor`, auto-download (including fonts per script), GPU.
- Slice 4: scan/OCR.

Do not build the parts of later slices ahead of time. Open items carried over from review and acceptance (none block slice 1): findings left for later (duplicate `\bibitem` keys and citation labels, float numbering drift, the adapter not yet supporting list/code/footnote blocks), the MinerU timeout killing only the `mineru-kit` client, protocol wording for harnesses that forbid `sleep`, and Windows/macOS/CI not yet exercised.

## Commands

```bash
uv sync                                         # install dependencies (Python 3.12 per .python-version)
uv run pytest -q                                # all tests
uv run pytest tests/test_checks.py::test_changed_number_rejected -v   # a single test
ROSETTEX_REAL=1 uv run pytest tests/test_cli.py -k real -v            # end-to-end with real MinerU (slow)
uv run python scripts/get_tectonic.py           # Tectonic 0.17.0 → ~/.rosettex/bin (PDF-building tests need it)
uv run --with fonttools==4.68.0 python scripts/build_fonts.py --cache <dir>   # rebuild rosettex/fonts (dev only)
uv run python -m rosettex.protocol.build        # regenerate skill/SKILL.md and skill/AGENTS.md after editing protocol.md
```

Tests that need TeX skip themselves when neither Tectonic nor XeLaTeX is found. End-to-end tests use `tests/fake_mineru.py` by default (set via `ROSETTEX_MINERU`), so real MinerU is not needed.

## Overall architecture

`rosettex` **does not translate by itself**. The agent in the harness is the translator; the CLI does only the deterministic parts:

```
PDF → [MinerU, subprocess, --format zip, by page group]
    → mineru_adapter: docvortex.middle 2.0 → document.json
    → segment: merge paragraphs, keep roles, placeholders ⟦m1⟧/⟦c1⟧/⟦r1⟧/⟦u1⟧, batches ≤ 3000 characters
    → agent: next → translate → accept (checked immediately) … → validate
    → render: probe.tex (isolate each formula/table) → main.tex → Tectonic/XeLaTeX → postcheck → report.json
```

Ideas that cut across several files:

- **Talking to the agent:** every command has `--json`, and every result has `next_action` (the `rosettex …` command the agent should run next). `rosettex status <work>` is the entry point after every interruption. Exit codes: `0` success, `1` check failure, `2` usage or environment error.
- **Background and resume:**
  - `extract` and `render` spawn a detached child process (`… --foreground`) and return immediately.
  - The child process writes a heartbeat to `progress.json`. A heartbeat older than 60 seconds counts as dead, and `status` returns the `resume` command.
  - Each MinerU page group has a `.done` marker file.
  - `document.json` is written **last**: if this file exists, extract finished completely.
- **Each state file has only one writer command:**
  - `manifest.json` is written by `extract` and `render`.
  - The translation is stored as a separate file per batch (`batches/bNNN.<lang>.json`), so several subagents can `accept` in parallel without contending.
  - `validate.json` is written by `validate`.
- **Last-resort fallback:** a formula or table that does not compile is shown as LaTeX source text in a box, without breaking the whole build. The failing item is identified by a `\typeout{RX:<id>}` line placed before each block, read back from the log. The fallback key has two forms: a block id (`p1b10`), or `<unit id>:<placeholder>` (`p1b9:m1`).
- **Translation checks** (`checks.py`, shared by `accept` and `validate`):
  - compared on NFC;
  - placeholders and numbers are compared as **multisets**: reordering is allowed, adding numbers is allowed, missing or changed values are not; `0.05` → `0,05` is an error;
  - "untranslated" detection uses the ratio of accented letters and the ratio of function words from the language pack, not the ratio of Han characters.
- **Language packs** are JSON (`rosettex/langpacks/<code>.json`); adding a language needs no code.
- **The agent protocol has a single source:** `rosettex/protocol/protocol.md`. It is printed by `rosettex guide`, and generates `skill/SKILL.md` and `skill/AGENTS.md`. A test requires the two generated files to match the source. Do not edit those two files by hand.

## Hard constraints (SG 3.1)

- Must run on Linux, macOS and Windows, CPU only, in **every harness** that has only a shell and file read/write access. The protocol uses no pipes, `&&`, or bash- or PowerShell-specific syntax.
- The only runtime dependency is `pypdfium2`. **PyMuPDF is forbidden** (AGPL); embedding AGPL code or code from a project without a license is forbidden (SG 18).
- Do not call system shell commands from code; do not use `shell=True`. Always read and write files as UTF-8 (read JSON with `utf-8-sig`), with `\n` line endings. Write JSON through a temp file then `os.replace`.
- MinerU configuration is passed only through the child process environment variables. Never read or write `~/.mineru/config.yaml`, never use `--remote`.

## Verified facts that are easy to get wrong (details in SG 4.1)

- The MinerU schema is block → span; **only blocks have a bbox** (normalized 0–1). `page_idx` is an absolute number, counted from 0, even when running with `--pages`. Tables come out as HTML strings.
- The `basic` tier adds fake `\dot{}` to formulas. The `standard` tier does not, so it is the default even on CPU only. Compiling does not mean correct.
- Forcing the device needs **two** variables: `MINERU_MODEL_ONNX_DEVICE ∈ {auto,cpu,gpu}` and `MINERU_LLAMA_CPP_BACKEND ∈ {auto,cpu,vulkan,metal}`. To read the hardware use `mineru-kit hardware detect --json`; the version without `--json` prints Chinese.
- With Tectonic, fonts must be loaded **by file name**: `texgyretermes` with `Extension=.otf`, and `latinmodern-math.otf`. The first Tectonic run needs the network; its Rust client fails intermittently when IPv6 is broken, so just rerun.
- On this dev machine, Python libraries that download from Hugging Face hang because IPv6 is broken. Use `--source modelscope`, or `curl -4`.
- Tectonic reads fonts only from its bundle or relative paths: packaged fonts are copied to `out/fonts/` and loaded with `Path=fonts/`. TeX Gyre Termes has no Cyrillic (Noto Serif is used). Tectonic has no ICU line breaking: Thai/Myanmar need U+200B between words (turned into `\hspace{0pt plus 1pt}`). polyglossia Arabic needs `numerals=maghrib` (`western` for Persian/Urdu) for ASCII digits. Arabic text extracted from the PDF is unreliable.
- `tests/fixtures/` holds **real** MinerU 4.2.3 output, the ground truth to compare against. If a test is wrong, fix the test to match the fixture, not the fixture.

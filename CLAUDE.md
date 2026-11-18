# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Status

**Slices 1 and 1b are implemented and merged into `main`** (261 tests: 259 pass, 2 are skipped because they need real MinerU and `ROSETTEX_REAL=1`). Read in this order before doing anything:

1. `docs/superpowers/specs/2026-11-18-rosettex-design.md`: the original spec (called **SG**). Section 4.1 holds the facts verified by spikes, section 19 holds the decisions already made. If you want to change something already decided, ask the user first. Two decisions were amended by the user on 2026-11-18 (struck through in place): **5** (the content length ratio is a warning, not a failure) and **15** (numbers keep their value; a decimal-comma language may write `0,05` and `1 000`).
2. `docs/superpowers/specs/2026-11-18-rosettex-slice1-design.md`: the slice 1 spec.
3. `docs/superpowers/specs/2026-11-18-rosettex-slice1b-design.md`: the slice 1b spec (scripts, fonts, layouts). **Section 15 (items 1–24) holds everything decided or fixed after the first draft** and wins over the earlier sections where they differ.
4. `docs/superpowers/notes/2026-11-18-slice1-acceptance.md`, `…-slice1b-acceptance.md`: acceptance records. `…-slice1b-open-items.md`: what is still open (tick items there).
5. `docs/superpowers/plans/`: the plans of slice 1 and 1b (done; the 1b plan is the argument behind the spec, not the final word).

Phase 1 is built as **vertical slices**, each with its own spec and plan:
- Slice 1: the end-to-end flow for PDFs with a text layer. **Done.**
- **Slice 1b: script coverage. Done.** 21 language packs in 15 scripts (`vi`, `fr`, `ru`, `uk`, `zh-Hans`, `zh-Hant`, `ja`, `ko`, `ar`, `fa`, `ur`, `hi`, `mr`, `bn`, `te`, `ta`, `gu`, `kn`, `th`, `am`, `my`; every pack is `tested: false` except `vi`), about 30 more language codes mapped to a script in `languages.json`, 14 subset OFL fonts shipped in `rosettex/fonts/`. Also built in this slice: venue styles (IEEE, Springer LNCS, Elsevier CAS, ACM), figure crops from the source PDF, floats placed on the page that first mentions them, XeLaTeX support.
- **Slice 2 (next): `review`.**
- Slice 3: `setup`, `doctor`, auto-download (including fonts per script), GPU.
- Slice 4: scan/OCR.

Do not build the parts of later slices ahead of time. Open items (none block use; details in the open-items note): native-speaker reading of the 20 Haiku translations and of the labels and rules of every pack (only then may a pack become `tested: true`); CI passes on Ubuntu, macOS and Windows (Python 3.11 and 3.12, Tectonic, fake MinerU), but a real MinerU run and XeLaTeX on macOS/Windows are not exercised; full-length papers of the ACM/Springer/Elsevier styles (one page of each was tried), Elsevier `elsarticle` and ACM single-column; thresholds (`min_target_ratio`, Thai/Myanmar U+200B limits, per-profile `scale`) still initial values; duplicate `\bibitem` keys and citation labels; the adapter not yet supporting list/code blocks; the MinerU timeout killing only the `mineru-kit` client.

## Commands

```bash
uv sync                                         # install dependencies (Python 3.12 per .python-version)
uv run pytest -q                                # all tests (about 100 s)
uv run pytest tests/test_checks.py::test_changed_number_rejected -v   # a single test
ROSETTEX_REAL=1 uv run pytest tests/test_cli.py -k real -v            # end-to-end with real MinerU (slow)
uv run python scripts/get_tectonic.py           # Tectonic 0.17.0 → ~/.rosettex/bin (PDF-building tests need it)
uv run --with fonttools==4.68.0 python scripts/build_fonts.py --cache <dir>   # rebuild rosettex/fonts (dev only)
uv run python -m rosettex.protocol.build        # regenerate skill/SKILL.md and skill/AGENTS.md after editing protocol.md
```

- **TeX:** tests that need TeX skip themselves when neither Tectonic nor XeLaTeX is found. XeLaTeX (TeX Live 2026, user-mode install in `~/texlive`) can replace Tectonic: `PATH=~/texlive/2026/bin/x86_64-linux:$PATH ROSETTEX_TEX=~/texlive/2026/bin/x86_64-linux/xelatex uv run pytest tests/test_render.py -q`. The extra TeX packages it needs are listed in slice 1b spec §15 item 23. Both engines must keep passing.
- **MinerU:** end-to-end tests use `tests/fake_mineru.py` by default (`ROSETTEX_MINERU`). The real MinerU 4.2.3 is in `~/.rosettex/mineru-venv` (`rosettex extract` finds it by itself).
- **Golden file:** `tests/golden/vi_short_main.tex` is the exact `main.tex` of the `vi` fixture. Change it only on purpose (regenerate with the snippet in slice 1b plan Task 3 Step 1) and say why.

## Overall architecture

`rosettex` **does not translate by itself**. The agent in the harness is the translator; the CLI does only the deterministic parts:

```
PDF → [MinerU, subprocess, --format zip, by page group]
    → mineru_adapter: docvortex.middle 2.0 → document.json (blocks, figure crops, table numbers)
    → segment: merge paragraphs, keep roles, placeholders ⟦m1⟧/⟦c1⟧/⟦r1⟧/⟦u1⟧, batches ≤ 3000 characters
    → agent: next → translate → accept (checked immediately) … → validate
    → render: fonts staged → probe.tex (isolate each formula/table) → main.tex → Tectonic/XeLaTeX
              → float placement passes → postcheck → report.json
```

Ideas that cut across several files:

- **Talking to the agent:** every command has `--json`, and every result has `next_action`. `rosettex status <work>` is the entry point after every interruption. Exit codes: `0` success, `1` check failure, `2` usage or environment error. `extract` and `render` accept `--foreground` for harnesses that cannot wait.
- **Background and resume:** `extract`/`render` spawn a detached child (`… --foreground`) that writes a heartbeat to `progress.json` (older than 60 s = dead; `status` returns the `resume` command). Each MinerU page group has a `.done` marker; `document.json` is written **last**.
- **Each state file has only one writer command:** `manifest.json` by `extract`/`render`; one translation file per batch (`batches/bNNN.<lang>.json`) so subagents can `accept` in parallel; `validate.json` by `validate`.
- **Last-resort fallback:** a formula or table that does not compile is shown as LaTeX source text in a box. The failing item is found from `\typeout{RX:<id>}` lines read back from the log (keys: a block id `p1b10`, or `<unit id>:<placeholder>`).
- **Languages and scripts are data.** `languages.json` maps a code to a script profile (`langpacks/scripts/<id>.json`: layout `plain`/`cjk`/`runs`/`rtl`, Unicode blocks, fonts, line-break mode, `scale`) and says whether the language writes a decimal comma. A pack (`langpacks/<code>.json`) only holds what differs from `default.json`. `load_pack` also accepts `ja-JP`, `zh_cn`, `VI`. Adding a language of a known script needs no code.
- **Fonts** (`fonts.py`): packaged fonts are copied to `out/fonts/` and loaded with `Path=fonts/`; a damaged font stops `extract`/`render` with `font_missing` before any long work. Bundle fonts (`texgyretermes`, `NotoSerif`, `latinmodern-math`) come from the TeX distribution.
- **Layouts** (`render/latex.py`, `tex.py`): `plain` for Latin/Cyrillic, `cjk` (xeCJK), `runs` (script runs in `\rxs{}`; Thai/Myanmar get U+200B breaks and `\rxbrk` emergency breaks), `rtl` (polyglossia + bidi; every non-Arabic character goes through `\rxltr{}` in the Latin font, brackets mirrored).
- **Venue styles:** `paper_style(doc)` recognises IEEE, Elsevier CAS, Springer LNCS and ACM from the source and builds with the venue's own class (`IEEEtran`, `cas-sc`/`cas-dc`, `llncs`, `acmart`); if the class is missing or does not compile the build falls back to `generic` (`report.json` has `style`, `style_fallback`).
- **Floats:** a figure or table is emitted just before the paragraph that first mentions it, in source order within its kind; marks written at shipout tell `render` which page each landed on and it rebuilds (at most 3 passes, best build wins). Figures are cropped from `src.pdf` with pypdfium2, not taken from MinerU's JPEG (which cuts off titles and notes).
- **Translation checks** (`checks.py`, shared by `accept` and `validate`): compared on NFC; placeholders and numbers compared as **multisets** (reordering and extra numbers allowed, missing or changed values not); in a `decimal_comma` language a number may be written in the local style (`0,05`, `1 000`, `1.000`) but not as English `1,000`; non-ASCII digits are errors; the "untranslated" detector uses the target-script ratio (plus function words where the pack has them), never the Han ratio; Thai/Myanmar need U+200B.
- **`postcheck`** returns `problems` (failures: log errors, missing glyphs, dotted circle, overfull lines for `cjk`/`zwsp`, undefined references, counts) and `warnings` (the content length ratio above the script's usual ceiling). Floats that could not be put on their page are in `floats_off_page`.
- **The agent protocol has a single source:** `rosettex/protocol/protocol.md`. It is printed by `rosettex guide [--lang <code>]` (language-neutral without `--lang`), and generates `skill/SKILL.md` and `skill/AGENTS.md`. A test requires the generated files to match. Do not edit those two files by hand.

## Hard constraints (SG 3.1)

- Must run on Linux, macOS and Windows, CPU only, in **every harness** that has only a shell and file read/write access. The protocol uses no pipes or bash- or PowerShell-specific syntax.
- The only runtime dependency is `pypdfium2`. **PyMuPDF is forbidden** (AGPL); embedding AGPL code or code from a project without a license is forbidden (SG 18). fontTools is used only by the dev script `scripts/build_fonts.py`.
- Do not call system shell commands from code; do not use `shell=True`. Always read and write files as UTF-8 (read JSON with `utf-8-sig`), with `\n` line endings. Write JSON through a temp file then `os.replace`.
- MinerU configuration is passed only through the child process environment variables. Never read or write `~/.mineru/config.yaml`, never use `--remote`.
- Do not read fonts from the operating system; do not edit `tests/fixtures/` (real MinerU output) or the font files except through `scripts/build_fonts.py`.

## Verified facts that are easy to get wrong (details in SG 4.1 and slice 1b spec §3, §15)

- The MinerU schema is block → span; **only blocks have a bbox** (normalized 0–1). `page_idx` is an absolute number counted from 0. Tables come out as HTML strings; "TABLE I" and a figure's title can be separate parts or blocks; text drawn inside a figure can come out as headings. MinerU writes italic variables as Unicode math letters (U+1D400…): they go to math mode.
- The `basic` tier adds fake `\dot{}` to formulas; `standard` is the default even on CPU only. Compiling does not mean correct.
- Forcing the device needs **two** variables: `MINERU_MODEL_ONNX_DEVICE` and `MINERU_LLAMA_CPP_BACKEND`. To read the hardware use `mineru-kit hardware detect --json`.
- With Tectonic, fonts must be loaded **by file name** or relative path (`Path=fonts/`); it has no ICU line breaking and no `cas-sc` class, and its first run needs the network (rerun when IPv6 is broken). TeX Gyre Termes has no Cyrillic (Noto Serif is used) and no U+202F.
- Noto Naskh Arabic has no Latin letters, brackets or most punctuation. polyglossia Arabic needs `numerals=maghrib` (`western` for Persian/Urdu) for ASCII digits; `otherlanguage{english}` around `thebibliography` fails in Tectonic; TeX Live's polyglossia wants a typewriter font (`\<lang>fonttt`) and `bidi` needs `\UseMathForPositioningText`. Arabic text extracted from the PDF is unreliable.
- On this dev machine IPv6 is broken: use `curl -4`, `--source modelscope`, and a `.curlrc` containing `ipv4` (via `CURL_HOME`) for `tlmgr`. `pkill -f <pattern>` kills the shell running it when its own command line contains the pattern: write `[p]attern`.
- `tests/fixtures/` holds **real** MinerU 4.2.3 output, the ground truth to compare against. If a test is wrong, fix the test to match the fixture, not the fixture.

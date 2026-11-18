# Slice 1 spec: `rosettex` — end-to-end flow for PDFs with a text layer

Date: 2026-11-18 · Status: **awaiting user approval** · Base spec: `2026-11-18-rosettex-design.md` (abbreviated **SG**)

> This slice implements part of the SG. Where slice 1 is silent, the SG decides. Where slice 1 deliberately differs from the SG or is more specific, it says "≠ SG" or "refines SG".

## 1. Goal of the slice

An agent in **any harness that has a shell and file read/write**, on **Linux, macOS or Windows**, receives a command such as "`Dịch bài báo X sang tiếng Việt, lưu ở Y`" (translate paper X into Vietnamese, save it at Y) and returns a Vietnamese PDF for a **paper with a text layer**. The output PDF has real characters for formulas and tables, clickable internal links, and passes the post-build checks (SG 11.3).

**Success when:**
1. The two sample papers `tests/fixtures/papers/short.pdf` and `long.pdf` (the `.tex` sources sit in the same directory) go through the whole `extract → terms → next/accept → validate → render` flow with no manual fixes. This can run automatically with a "fake translator" in tests, or be run by a real agent.
2. A real agent (Claude Code) translates `short.pdf` in full from a single command, relying only on the guide printed by `rosettex guide`.
3. Unit tests and PDF-build tests (no MinerU needed) pass on Linux, macOS and Windows.
4. SG 16 §7 is measured: the rate of batches rejected by `accept` on first submission, on `long.pdf`.

## 2. Scope

**In slice 1:**
- `extract`, `status`, `terms`, `next`, `accept`, `validate`, `render`, `guide`, `clean`.
- The MinerU adapter (`docvortex.middle 2.0`, zip output) → `document.json`.
- Paragraph merging, placeholders, internal links, automatic keep-as-is lists, text in table cells.
- HTML table parsing → `tabular`. The last-resort fallback (SG 10) for formulas/tables that do not compile.
- The `vi` language pack and the `default` pack.
- A single-source protocol → `rosettex guide`, `SKILL.md`, `AGENTS.md`.

**Outside slice 1** (left for later slices; slice 1 must leave a place to hook them in but not build them ahead of time):
- `review` (slice 2).
- `setup`, `doctor`, auto-download of models/Tectonic/fonts, `install-skill`, automatic fallback to CPU when the GPU fails (slice 3).
- Scanned pages and OCR (slice 4).
- Target languages in the 15 scripts of SG §11.5 (added 2026-11-18): script profiles, fonts, right-to-left, line breaking, per-script detector, missing-glyph post-check. Slice 1 ships only `vi` and `default`, but its language-pack format and checks were written for Vietnamese and must be generalized in **slice 1b**, which comes right after slice 1 (see the amendment at the top of the slice 1 plan).
- HTML (phase 2).

**Prerequisites** (slice 1 does not install them itself; if missing it reports a clear error and says how to install manually):

| Needed | Looked up in this order |
|---|---|
| MinerU 4.x | the `ROSETTEX_MINERU` variable (path to `mineru-kit`), `~/.rosettex/mineru-venv/{bin,Scripts}/mineru-kit`, `mineru-kit` on `PATH` |
| Models of the chosen tier | `mineru-kit models verify --tier <tier>` passes |
| LaTeX | the `ROSETTEX_TEX` variable, `~/.rosettex/bin/tectonic[.exe]`, `tectonic` on `PATH`, `xelatex` on `PATH` |

The `ROSETTEX_*` variables are set **by the user** once at install time, not by the agent at run time, so they do not violate SG 3.1.

## 3. Cross-platform constraints applied in this slice

All of SG 3.1 applies. Specifically for slice 1:

- **Runtime dependencies:** only `pypdfium2`. The standard library handles the rest: `argparse`, `json`, `html.parser`, `unicodedata`, `subprocess`, `zipfile`, `re`, `pathlib`. No PyMuPDF (AGPL). `pypdfium2` is used to count pages, extract text from the output PDF for the post-build check, and render page images for `next`.
- **Python:** `>=3.11`. Install with `uv tool install` or `pipx install`, or run `python -m rosettex` from source.
- **Background running:** `rosettex extract` and `rosettex render` spawn a detached child process (SG 3.1) and return immediately. The child writes a heartbeat at least every 10 seconds. `status` treats a task as **dead** when the heartbeat is older than 60 seconds and the state is not yet `done`/`failed`. The `--foreground` flag runs synchronously, for tests and debugging. `status` is **read-only** and writes no file. The `retries` counter in `progress.json` is incremented when a resumed process actually starts after a death; if the task dies a second time, or the child hits an unexpected error, `status` returns `next_action: null` with `error` and the log path (the agent tells the user and stops). Each MinerU or TeX call has a total time limit (MinerU: 900 seconds plus 120 seconds per page of the group; TeX: 300 seconds per run; multiplied by the `ROSETTEX_TIMEOUT_SCALE` environment variable). When the limit is exceeded the child process is killed and the task moves to `failed`.
- **Calling MinerU and LaTeX:** copy the source PDF to `work/src.pdf` (ASCII name), run with `cwd=work/…` and relative file names. Pass MinerU configuration through the child process's environment variables (SG 8.1); do not read or write `~/.mineru/config.yaml`.

## 4. Working directory

Default `<output-dir>/.rosettex/<base-name>.<lang-code>/` (it includes the language code so that translating the same PDF into several languages does not share a directory), changed with `-o`. `manifest.json` carries the marker `"schema": "rosettex.work/1"`; `clean`, `extract`, `status` and `render` accept only directories with a valid manifest. `extract` refuses (exit code 2) a directory that already has content but is not a rosettex working directory, and refuses a working directory whose `--lang` differs from the manifest. An empty or missing directory is created. A directory without a manifest is accepted again (and `clean` only cleans it) only when it has `src.pdf` and every entry either belongs to rosettex or is a `.*.tmp` temp file: that is the trace of an interrupted `extract`. `extract` checks the PDF (the page count must be readable) before creating the directory, so a corrupt or password-protected PDF leaves no directory behind:

```
src.pdf                  copy of the source PDF
manifest.json            overall state (§5)
progress.json            running background task: command, pid, heartbeat, step, n/total
logs/                    stdout/stderr of MinerU, LaTeX
mineru/g001/…            unpacked zip result of each page group
pages/p001.png           image of each page (150 dpi) for the agent to look at
document.json            intermediate format (§6)
batches/b001.json        translation batch (§7)
batches/b001.vi.json     accepted translation
terms.json
out/main.tex, out/*.log, out/assets/
report.json
```

**Write rule:** each file is written by **one** command only. Always write through a temp file then `os.replace`. A translated batch is its own file (`bNNN.<lang>.json`), so several `accept` subagents in parallel do not contend. `manifest.json` is written only by `extract` and `render`. `status` summarizes batch state by listing the `batches/` directory, and writes no file.

## 5. Commands

Every command accepts `--json`. Exit codes: `0` is success; `1` is a check failure (the list of errors by `id` is in the JSON); `2` is a usage or environment error (MinerU missing, LaTeX missing, wrong path).

| Command | What it does in slice 1 |
|---|---|
| `rosettex guide [--lang vi]` | Prints the protocol for the agent (§10) |
| `rosettex extract <pdf> --lang <code> -o <work> [--tier standard\|basic] [--pages-per-group N] [--foreground]` | §6, §7. Idempotent: skips page groups that already have `mineru/gNNN/.done` |
| `rosettex status <work> [--json]` | Current step, progress, whether the task is dead, and **`next_action`**: one concrete `rosettex …` command the agent should run next |
| `rosettex terms <work> --file <terms.json>` | Validates the structure then writes `terms.json` |
| `rosettex next <work> [--json]` | The translation package for a batch with no translation yet, in batch order (§7.3). When no batches remain it returns `{"done": true}` |
| `rosettex accept <work> --batch <id> --file <f>` | §8. On success writes `bNNN.<lang>.json`; otherwise exit code 1, with errors by paragraph `id` |
| `rosettex validate <work>` | §8, whole paper |
| `rosettex render <work> --out <path> [--overwrite] [--foreground]` | §9. If the destination exists and there is no `--overwrite`, exit code 2, and the protocol tells the agent to ask the user |
| `rosettex clean <work>` | Deletes only items created by rosettex (the `OWNED` list in `workspace.py`, plus `.*.tmp` temp files); deletes `manifest.json` last; removes the directory only when it is empty, while user files are kept and listed in `kept` |

**Default tier:** `standard` (SG 8.3 as amended). Slice 1 has no `doctor`, so `extract` drops to `basic` according to the rule in SG 8.3, reading available RAM with the standard library on each OS. If RAM cannot be read, it stays at `standard`. If the tier's models have not passed `verify`, exit code 2, with the manual download command.

**Page groups:** default 5 pages (about 90 seconds per group at `standard` on CPU, per SG 4.1). A failed group is retried once. On a second failure it is marked `failed` in `progress.json`, and `status` suggests `extract --tier basic` (if already at `basic`, it stops with `next_action: null`). A group that timed out (see §3) is not retried. Content returned by MinerU that the adapter does not understand (unknown block types, spans or parts) is written to `document.json` under `source.dropped` and counted in `report.json`, so the agent can tell the user; page numbers, headers and footers are deliberate noise and are not recorded.

## 6. `extract`: MinerU → `document.json`

### 6.1 Calling MinerU
`mineru-kit parse src.pdf -o mineru/gNNN --format zip --tier <tier> --pages <a-b>`, then unzip. The plan must verify whether `page_idx` in a group's result is an absolute or a relative index. The adapter normalizes to **absolute page numbers, counted from 1**.

### 6.2 Intermediate format (`document.json`, `schema: "rosettex.document/1"`)
```json
{"source": {"pages": 20, "page_size_pt": [[612,792], …], "columns": 2, "parser": "mineru-4.2.3/standard"},
 "blocks": [
  {"id": "p3b12", "kind": "paragraph", "page": 3, "bbox": [0.088,0.505,0.493,0.567],
   "spans": [{"t": "text", "v": "Given an input sequence "}, {"t": "math", "v": "x = (x_1,\\dots,x_n)"}],
   "continues": "p3b13", "role": "body"},
  {"id": "p3b10", "kind": "heading", "level": 2, "number": "2", "label": "sec:2", "spans": […]},
  {"id": "p3b14", "kind": "equation", "tex": "…", "tag": "1", "label": "eq:1", "image": "mineru/g001/images/…"},
  {"id": "p4b17", "kind": "table", "caption": [spans], "label": "tab:1",
   "cells": [{"id": "p4b17r0c0", "r": 0, "c": 0, "rowspan": 2, "colspan": 1, "spans": […]}, …]},
  {"id": "p4b18", "kind": "figure", "image": "…", "caption": [spans], "label": "fig:1", "wide": false},
  {"id": "p1b22", "kind": "footnote", …}, {"id": "p9b40", "kind": "reference", "key": "1", …}
 ]}
```
- `kind` is mapped from MinerU block types: `doc_title`/`paragraph_title` → `heading`; `text` → `paragraph`; `equation` → `equation`; `table` → `table`; `chart`/`image` → `figure`; `page_footnote` → `footnote`; `ref_text` → `reference`. `page_number`, `header`, `footer` are dropped.
- **`role`** marks blocks kept as-is automatically (SG 9.6): `authors` (short text blocks between the title and the Abstract on page 1), `affiliation`, `email`, `reference`.
- **Column count:** count `paragraph` blocks whose bbox lies entirely in the left or right half of the page. If ≥ 60% of blocks are like that, then `columns: 2`. A block wider than 60% of the page width gets `wide: true`.
- **LaTeX normalization** (SG 9.10): only merge whitespace between digits and between letters inside `\operatorname{…}`/`\mathrm{…}`. Symbols are not changed.
- **Tables:** `html.parser` reads `<tr>/<td rowspan colspan>` into `cells`. Cells whose value is purely numeric (after stripping whitespace) do not enter the translation batches.

### 6.3 Paragraph merging (SG 9.4)
A `paragraph` block A joins the next `paragraph` block B in reading order (even when B is in the next column or page, skipping float blocks such as figures, tables, equations and footnotes in between) when: A does not end with `. ! ? :` (after stripping whitespace), **and** B starts with a lowercase letter or a `math` span. The merged paragraph takes A's `id`. `continues` stores the list of original blocks for mapping positions back.

### 6.4 Placeholders and links (SG 9.5, 9.8)
Placeholders have the form `⟦<kind><number>⟧`, numbered separately within each paragraph:

| Kind | Source | Built as |
|---|---|---|
| `m` | `math` span | `$…$` |
| `c` | citations `[1]`, `[1, 2]`, `[3–5]` matching real keys in `reference` | `\cite{ref1,ref2}` |
| `r` | "Figure 3", "Fig. 3", "Table 2", "Section 4", "Sec. 4", "Eq. (5)", "Equation 5", "(5)" right after "Eq."; also searched inside `math` spans (SG 9.5 as amended) | `\ref`/`\eqref` to the corresponding label; the word "Figure/Hình" is translated by the agent, only the number is the placeholder |
| `u` | URLs, emails | `\url{…}` |

**≠ SG 9.8:** there is no `n` placeholder for numbers. Numbers stay in the text so the agent translates naturally, and they are checked as a multiset (§8). Also, a number with a unit such as "4.5 million" often has to change order or phrasing in translation, so wrapping it as a placeholder would make the translated sentence awkward.

### 6.5 Translation batches
- The translation unit is the **paragraph** (after merging), section headings, figure/table captions, footnotes, and table cells that contain text. A paragraph is never split across two batches.
- Each batch is about ≤ 3000 source characters. The cells of one table always sit in the same batch.
- Blocks with a keep-as-is `role` do not enter batches (kept verbatim at build time).
- `batches/bNNN.json`:
```json
{"batch": "b003", "section": "2 Method", "items": [
  {"id": "p3b9", "type": "paragraph", "page": 3, "text": "Given an input sequence ⟦m1⟧ with ⟦m2⟧, … following ⟦c1⟧ …",
   "placeholders": {"m1": "x = (x_1,\\dots,x_n)", "c1": "[1, 2]"}}]}
```

### 6.6 Flagging (SG 9.9, the part doable without `review`)
Written to `flagged.json` so the final report can list them:
- formulas containing `$`, or containing lowercase text such as `e.g.`/`i.e.` inside `\mathrm`;
- formulas with diacritics (`\dot`, `\ddot`, `\hat`, `\tilde`, `\bar`) where the PDF text layer inside the block's bbox has no corresponding accent character (the text layer is read with `pypdfium2`);
- formulas that do not compile (detected at `render`).

Slice 1 only **reports**, it does not fix. Fixing is slice 2's job.

## 7. `terms` and `next`

### 7.1 `terms.json`
```json
{"topic": "one sentence", "terms": [{"src": "attention", "dst": "cơ chế chú ý"}],
 "keep": ["Transformer", "BLEU"], "abbreviations": [{"abbr": "FFN", "expansion": "feed-forward network"}]}
```
`rosettex terms` rejects a file with a wrong structure, duplicate `src`, or empty strings.

### 7.2 Order
`next` requires `terms.json` to exist. If it does not, it returns an error with a `next_action` suggesting the agent read the title, abstract and section headings. That part is obtained with `rosettex next <work> --outline`, a command that prints the title, abstract and section tree.

### 7.3 Package returned by `next --json`
Contains: `batch` (the batch content), `topic`, `terms`, `section`, `previous` (the last paragraph of the previous batch, both original and translation, if the previous batch already has one), `page_images` (absolute paths to the images of the pages present in the batch), `rules` (abridged translation rules from the language pack), `submit` (the exact `accept` command to submit with).

Several subagents can call `next --batch <id>` to get a specific batch. `next` without `--batch` returns the **first batch with no translation**. Slice 1 does not "reserve" batches: parallel orchestration is the main agent's job.

### 7.4 File submitted to `accept`
JSON `{"<id>": "<translation>", …}`, containing every `id` of the batch.

## 8. Checks (`accept` per batch, `validate` for the whole paper)

All comparisons run on NFC-normalized text. An **error** causes rejection; a **warning** is still written, with the warning in the JSON.

| Check | Level | Notes |
|---|---|---|
| Missing or extra `id`; empty paragraph | error | |
| Placeholder missing, extra or modified (compared as a **multiset**) | error | **≠ earlier SG:** reordering is allowed (SG 11.2 as amended, awaiting approval) |
| A number of the original (outside placeholders) missing or changed in value (compared as a **multiset**) | error | **≠ earlier SG:** adding numbers is allowed. Normalize numbers before comparing: `1,000` = `1000`; the decimal point is kept, so `0,05` in place of `0.05` is an **error** (SG decision 15) |
| Suspected untranslated (language pack) | error | Skip paragraphs ≤ 40 letter characters, and ignore `keep`/proper names when computing |
| Characters outside the allowed alphabet | error | The `vi` alphabet: basic Latin, 134 accented letters, common Unicode punctuation, Greek, math symbols and units |
| Translation/original length ratio outside [0.7, 2.0] | warning | The thresholds live in the language pack |
| A term `src` appears in the original but its `dst` is not in the translation | warning | |

`validate` reruns all checks on every batch. In addition it checks: every batch has a translation; every label that `r`/`c` point to exists; the numbers of figures, tables, numbered equations and references in `document.json` match the counts from MinerU.

**The `vi` "untranslated" detector** (SG 11.4): a paragraph is considered suspected-untranslated when it matches the original text exactly, **or** when it satisfies both of the following conditions:
- the ratio of Vietnamese-specific accented characters < 3% of the letters;
- the ratio of Vietnamese function words < 5% of the words.

These initial thresholds are tuned in the plan on a real translation of `long.pdf`. There is a regression test: a correct Vietnamese translation must **pass**.

## 9. `render`

1. **Per-item pre-check:** gather every formula and table into one file `out/probe.tex`. Before each item write `\message{RX:<id>}` to identify which item causes an error. Compile this file. On an error, read the last `id` in the log, move that item to the **last-resort fallback** (SG 10: the raw LaTeX source as text, inside an `\fbox` box labeled "công thức chưa dựng được" ("formula could not be built")), and repeat until clean. At most N passes, where N is the number of items.
2. **Generate `out/main.tex`:**
   - `article` class, `twocolumn` if `columns: 2`.
   - `geometry` per the original paper size, margins taken from the smallest and largest bbox of the blocks.
   - `fontspec` with Termes loaded by file name, Noto Serif as fallback; `unicode-math` with `latinmodern-math.otf`; `polyglossia` with `vietnamese`; `hyperref`, `booktabs`, `multirow`, `graphicx`, `url`.
   - Escape `\ # $ % & _ { } ~ ^` in **all translated text outside placeholders**.
   - Figures and tables use a `[tbp]` float placed right after the paragraph containing the first reference to it; if there is no reference, place it by document order. Use `figure*`/`table*` when `wide: true`.
   - Tables: `tabular` with `\multirow`/`\multicolumn` per `cells`. A table that is too wide is wrapped in `\resizebox{\linewidth}{!}{…}`.
   - References: `thebibliography` kept verbatim, `\bibitem{refN}`.
3. **Compile:** Tectonic compiles once (it reruns itself as needed). With `xelatex`, run 2-3 passes until the log has no more "Rerun". If compiling the whole paper still fails, map the error line back to an `id` via the `%RX:<id>` comments placed in `main.tex`, move that item to the last-resort fallback and recompile (SG 11.3).
4. **Post-build check** (SG 11.3, using `pypdfium2`):
   - the log has no errors, no "Missing character", no undefined references;
   - the PDF text layer contains no `??` or `[?]`;
   - text can be extracted and the formula characters are visible;
   - the number of internal links matches;
   - all 134 accented letters are present if the paper uses them;
   - **content length ratio** ≤ 1.3, computed as follows: take the total height (pt) of all text lines in the output PDF divided by the corresponding total height in the original PDF. Both are measured with `pypdfium2` via the bounding box of each character, grouped by line.
5. **Write results:** write `report.json` (SG 6: path, topic, key terms, tier, items at the last-resort fallback with page numbers, flagged items, warnings) then copy the PDF to `--out`.

## 10. Agent protocol (single source)

- The single source is `rosettex/protocol/protocol.md`, with substitution points such as `{{lang_rules}}`.
- `rosettex guide` prints this content.
- `SKILL.md` (Claude Code) and `AGENTS.md` are **generated at build time** from the same source. The build step checks that the two generated files match the source, so they cannot drift apart.
- `SKILL.md` consists of frontmatter (name, and an activation description in Vietnamese and English sentences) plus one line: "Run `rosettex guide` and follow it." The full protocol text is inserted below it so that no extra command call is lost.

**Protocol content:** steps 0-8 of SG 12, **except step 1** (`setup`/`doctor`, left for slice 3) and **step 4** (`review`, left for slice 2). Plus these rules:
- run only single `rosettex …` commands, no pipes;
- after every interruption, run `rosettex status <work> --json` then do exactly the `next_action`;
- when `status` reports that a background task is running, call `status` again after about 30-60 seconds; how to wait is up to each harness, the protocol does not prescribe it.

**Manual install in slice 1:** the README says how to copy `SKILL.md` into Claude Code's skill directory and how to place `AGENTS.md`. For other harnesses, just tell the agent "run `rosettex guide`".

## 11. Code structure

```
rosettex/
  __main__.py, cli.py        argparse, UTF-8 --json output, exit codes
  workspace.py               paths, atomic writes, progress/heartbeat, process detaching (POSIX/Windows)
  tools.py                   find mineru-kit, tectonic/xelatex; read available RAM
  extract/
    run.py                   page groups, call mineru-kit, idempotent
    mineru_adapter.py        docvortex.middle 2.0 → document.json
    tables.py                HTML → cells
    segment.py               paragraph merging, keep-as-is role, placeholders, links, batches, flagging
  translate.py               terms, next, accept
  checks.py                  the §8 checks (shared by accept/validate)
  render/
    latex.py                 document + translation → main.tex, escaping
    compile.py               probe, compile, map errors → id, last-resort fallback
    postcheck.py             post-build check, report.json
  langpacks/vi.json, langpacks/default.json
  protocol/protocol.md, build_protocol.py
tests/
  fixtures/papers/, fixtures/mineru-4.2.3-{basic,standard}/   (already present from the spike)
  test_adapter.py, test_segment.py, test_checks.py, test_render.py, test_workspace.py, test_e2e_fake_translator.py
```

A language pack is JSON (font, check characters, alphabet, function words, thresholds, labels) rather than code. Adding a language only needs adding one file.

## 12. Testing

- **Unit**, run without MinerU, without LaTeX, on 3 OSes:
  - the adapter run on the real fixtures;
  - paragraph merging across columns and pages in `long`;
  - placeholders and links, including "Eq. (1)" inside `\mathrm`;
  - HTML tables with `rowspan`/`colspan`;
  - `accept` rejects: a changed number, `0.05` turned into `0,05`, a missing or modified placeholder, a submission of the unchanged English text;
  - `accept` accepts: a correct Vietnamese translation, a translation with reordered placeholders, a translation with added numbers, a translation in NFD form (normalized to NFC, the stored copy is always NFC);
  - LaTeX escaping;
  - process detaching and heartbeat-based dead-task detection (simulating a killed process).
- **PDF build**, needs LaTeX, on 3 OSes: `render` run from the fixture's `document.json` plus a "fake translator" (takes the original then adds Vietnamese function words and accented letters). Check: the output PDF is correct, the post-build check is clean, and a deliberately broken formula is moved to the last-resort fallback instead of breaking the whole build.
- **End-to-end**, needs MinerU, run on the dev machine: `short.pdf` and `long.pdf` with the fake translator; then translate `short.pdf` with a real agent (success criterion 2); measure the rejection rate (criterion 4).
- **CI:** a GitHub Actions matrix `ubuntu-latest`/`macos-latest`/`windows-latest`, Python 3.11 and 3.12. The PDF-build step downloads a pinned Tectonic version with a checksum check. The project directory is currently **not a git repo**; initializing the repo is the first step of the plan.

## 13. Decisions in this slice (need user approval)

1. The only runtime dependency is `pypdfium2`; no PyMuPDF because of AGPL.
2. Placeholders are compared as a multiset, allowing reordering.
3. Numbers are compared as a multiset: adding numbers is allowed, missing or changed values are not.
4. Numbers do not use placeholders; the SG's `n` is dropped.
5. Default tier `standard`; 5-page groups; batches ≤ 3000 characters.
6. Background processes are detected as dead by a 60-second heartbeat.
7. Single-source protocol, printed by `rosettex guide`; `SKILL.md` and `AGENTS.md` are generated from that source.
8. Slice 1 does not reserve batches for subagents; the main agent partitions batches itself.
9. Flagging suspicious formulas is for reporting only; fixing is slice 2's job.

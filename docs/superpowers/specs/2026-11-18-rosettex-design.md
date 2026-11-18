# Spec: `rosettex` — a tool for translating scientific PDF papers, for agent harnesses

Date: 2026-11-18 · Status: approved; updated with the results of the §16 spike (§4.1) · Implemented in slices, slice 1: `2026-11-18-rosettex-slice1-design.md` (same directory)

> **To the implementing agent:** read this whole document before writing code. §4 holds verified facts; do not assume otherwise. §16 lists the points that must be checked **before** building anything that depends on them. What is marked "decided by the builder" (§17) is your call. Everything else has been settled by the user; if you want to change it, ask the user.

---

## 1. Purpose

The user is a researcher who needs to **translate a whole** scientific paper in English (PDF) into another language (Vietnamese first) in order to read and work with it.

Existing tools (PDFMathTranslate, BabelDOC…) call an external translation engine or API. The user wants **the agent inside the harness itself** (Claude Code; later Codex, Antigravity, Hermes) to be the translator, so that no separate API key is needed and the harness subscription already in hand is put to use.

So `rosettex` **does not translate by itself**. It does the part the agent cannot do alone with a PDF: extracting content, splitting into batches, checking, and rebuilding the document. The agent translates, and reads images to cross-check and to fix misrecognized spots.

The tool is **installed into the harness**. The user says a single sentence, for example: "`Dịch bài báo ~/papers/attention.pdf sang tiếng Việt, đặt kết quả ở ~/dich/`" (translate the paper `~/papers/attention.pdf` into Vietnamese, put the result in `~/dich/`). The agent does everything from start to finish.

## 2. Goals, non-goals, things to avoid

**Goals**
- Input: an English scientific PDF, with a text layer or a scan (scan is a lower-quality mode).
- Output: a **PDF** in the target language, layout close to the original (two columns if the original has two), with differences at an acceptable level and minimized. Bilingual HTML in phase 2.
- **Formulas and tables are always real characters** (selectable, copyable, searchable), rebuilt in LaTeX. Never an image or a region cropped from the original, even when the input is a scan. Only figures are images cropped from the original.
- Preserve: figures and data (including decimal points), citations, formulas, section order, clickable internal links (citation → reference; references to figures, tables, sections, equations).
- Correct Vietnamese diacritics, including stacked ones (ệ, ữ, ẫ).
- Consistent terminology within one paper.
- Use the GPU automatically when present and suitable; fall back to CPU automatically when absent or when the GPU fails.
- Make use of resources already on the machine; download only what is missing; download in advance right after installation.
- Survive the harness's per-shell-command time limit; if interrupted, resume rather than redo.
- Work in any harness with just the permission to run a shell and read/write files.

**Non-goals**
- Pixel-for-pixel fidelity to the original.
- Translating from LaTeX source (arXiv).
- Translating text inside figures.
- A glossary stored across papers.
- Calling any remote translation, LLM or document-analysis service (including `mineru-kit --remote`).

**Things to avoid**
- A translation that corrupts formulas, figures or citations without anyone noticing.
- A single faulty formula or table breaking the whole PDF build.
- Depending on one specific harness.
- GPU errors, network errors or interrupted commands losing progress.
- Touching the user's other Python environments or installations.

## 3. Constraints
- Linux, macOS, Windows; must run with CPU only.
- Python 3.12 recommended (MinerU requires 3.11-3.14).
- No embedding of AGPL code (BabelDOC, pdf2zh).
- No copying code from projects without a license (§18).
- The minimum every harness has: running a shell, reading/writing files. Image reading and subagents are enhancements.
- Harnesses usually limit the time of each shell command (a few minutes): every long step must run in the background and be resumable.

### 3.1 Run on every machine, every operating system, every harness (hard constraint, all slices)
**Operating systems** (Linux, macOS Intel/Apple Silicon, Windows x64):
- Pure Python plus libraries that have prebuilt wheels for all three OSes. Do not call system shell commands (`cp`, `rm`, `fc-list`, `nohup`…) from code; use `pathlib`, `shutil`, `subprocess` with an argument list (no `shell=True`).
- **Do not use PyMuPDF** (AGPL, contrary to §3). Read/render PDFs with `pypdfium2` (Apache-2.0/BSD; MinerU already pulls it in).
- Background running: `subprocess.Popen` detaching the process (`start_new_session=True` on POSIX; `DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP` on Windows). Detect dead tasks by **heartbeat** (the update time in `progress.json`), not by signals or the PID of any one OS.
- Write state files via a temp file + `os.replace`, with a short retry when Windows reports the file is open.
- Every `--json` output is UTF-8 regardless of the console code page (reset `sys.stdout` to UTF-8); file reads/writes always use `encoding="utf-8"`, newline `\n`.
- Paths with spaces or non-ASCII characters (for example `~/Tài liệu/bài báo.pdf`): run LaTeX and MinerU with the working directory as `cwd` and relative file names; intermediate file names use ASCII only.
- Fonts are loaded by **file name** from the TeX package set or `~/.rosettex/fonts/` (not dependent on fontconfig).

**Machine:** must run with CPU only and about 8 GB RAM (tier `basic` when RAM/disk is short); the GPU is only an accelerator.

**Harness:** every interaction goes through single `rosettex …` commands and files — no pipes (`|`), no `&`, no environment variables set by the agent, no bash/PowerShell-specific syntax in the protocol. The protocol has **a single source**, printable with `rosettex guide` so that any harness with a shell can learn it by itself; `SKILL.md` (Claude Code) and `AGENTS.md` (Codex and harnesses that read AGENTS.md) are generated from that same source.

**Testing:** unit tests and PDF-build tests run on a Linux/macOS/Windows matrix (CI). End-to-end tests with real MinerU run on at least one machine per OS before phase 1 is closed.

## 4. Verified facts (trial of 2026-11-18)

**MinerU 4.2.3** (installed from PyPI `mineru>=4,<5`, about 666 MB not counting models):
- The main CLI is `mineru-kit`:
  `mineru-kit parse <inputs> -o <out> [--format markdown|middle_json|zip] [--tier flash|basic|standard|advanced] [--pages '1-5,8'] [--ocr-mode auto|txt|ocr] [--disable-image-analysis] [--remote …]`.
- `mineru-kit hardware detect` suggests the maximum tier suitable for the machine; `mineru-kit hardware info` reports the GPU, runtime and chosen device with the reason.
- `mineru-kit models show|download|verify` manages models. Default directory `~/.mineru/models`, changed with `model.base_dir` in `~/.mineru/config.yaml`; the `MINERU_MODEL_SOURCE` variable picks the download source.
  - Tier `basic` uses `MinerU-4_models_onnx`.
  - Tier `standard` additionally uses `MinerU2.5-Pro-2605-1.2B-GGUF` (run by llama.cpp, even on CPU).
  - Tiers `advanced` and `flash`: unclear, see `models show` on a real machine.
- **The default runtime is ONNX Runtime + llama.cpp; PyTorch is not installed by default.** The GPU is used through ONNX Runtime's providers and llama.cpp (Vulkan/CUDA). PyTorch is needed only for some branches (torch, vLLM, lmdeploy).
- There is no CLI flag to choose the device. Force it through the config `model.onnx_device` or the environment variable `MINERU_MODEL_ONNX_DEVICE` (ONNX branch); `MINERU_DEVICE_MODE` (torch branch).
- The schema has a span type `inline_equation`, converted into `{"type": "equation_inline", "content": "<LaTeX>"}`. ~~line-level bbox~~ → verified: **only block-level bbox** (§4.1).
- Both content list formats V1 and V2 exist, with different schemas; do not rename files to treat one as the other.

**Vietnamese LaTeX** (XeLaTeX of TeX Live 2023):
- `fontspec` displays all **134/134** accented letters, with no missing-character warnings, and text extracted back from the PDF is complete, with: TeX Gyre Termes, Noto Serif, Latin Modern Roman, DejaVu Serif, FreeSerif, Noto Serif CJK SC.
- The translation of a 1-page two-column paper spills onto page 2. The spill takes up: Termes about 3% of the column height, Latin Modern 10%, Noto Serif 19%, Noto CJK 32%.
- `polyglossia` + `\setdefaultlanguage{vietnamese}` switches labels to Vietnamese ("Tóm tắt nội dung", "Hình", "Bảng"). `unicode-math` + Latin Modern Math, `multirow`, `booktabs`, `hyperref` all work.

### 4.1 Results of the §16 spike (2026-11-18, Linux, 20 CPU cores, 31 GB RAM, NVIDIA RTX A1000 4 GB)
Data: `tests/fixtures/papers/` (sample `.tex` papers and PDFs compiled with Tectonic 0.17.0), `tests/fixtures/mineru-4.2.3-{basic,standard}/` (real output).

**`middle_json` schema** — `schema: "docvortex.middle"`, `schema_version: "2.0"`:
- `pages[].{page_idx, blocks[]}`; page size at `extensions.docvortex_layout.pages[].{width_pt,height_pt}`; tier at `extensions.mineru.tier`.
- Block: `{type, index, bbox:[x0,y0,x1,y1] normalized 0-1, content, level?}`. Block types seen: `doc_title`, `paragraph_title` (has `level`), `text`, `equation`, `table`, `chart`, `page_footnote`, `page_number`, `ref_text`.
- The `content` of a text block is a list of spans `{type: "text"|"equation_inline", content, styles?: ["superscript",…]}`. **Spans have no bbox; there is no line level.**
- `equation.content` is a LaTeX string, with `\tag{n}` when the equation is numbered.
- `table.content` = [`table_caption` (span), `table_body` (**HTML string** with `rowspan`/`colspan`)]. Bold formatting is lost; `--` becomes `-`.
- `chart.content` = [`chart_body` (empty), `chart_caption`].
- `--format zip` = the identical `middle_json.json` + `image_path` for `equation`/`table_body`/`chart_body` + pre-cropped `images/*.jpg` + `markdown.md` + `structured_content.json`. **Use zip.**

**Quality (2-column paper, 20 pages, CPU only):**

| | `basic` | `standard` |
|---|---|---|
| Time | 231 s (11.5 s/page) | 351 s (17.6 s/page) |
| Peak RAM | 4.3 GB | 4.4 GB |
| Formulas that compile | 56/56 display, 579/580 inline | 56/56, 564/564 |
| Recognition errors | spurious `\dot{}` 37 times; the same formula sometimes right, sometimes wrong; "Eq. (1)" recognized as a formula; `e.g. $…$` lets text slip into the formula | none seen |

Compiling is not the same as correct: the `\dot{V}` error still compiles.

**Devices:** `hardware detect --json` / `hardware info --json` are available (the version without `--json` prints Chinese). ONNX: `MINERU_MODEL_ONNX_DEVICE ∈ {auto,cpu,gpu}`; MinerU itself refuses GPUs < 6 GB. llama.cpp: `MINERU_LLAMA_CPP_BACKEND ∈ {auto,cpu,vulkan,metal}`. Forcing Vulkan on a 4 GB GPU works but is slower than a 20-core CPU (37.5 s versus 30 s, 2-page paper).

**Models:** `basic` = `MinerU-4_models_onnx` 819 MB; `standard` adds `MinerU2.5-Pro-2605-1.2B-GGUF` 1.24 GB; `flash` needs no models; `advanced` is not available on the ONNX runtime. `models verify` needs the `.mineru_complete` marker file; files downloaded externally are accepted if `models download` is rerun (it only writes the marker, does not redownload).

**Network:** a broken IPv6 makes Python libraries hang forever at `SYN-SENT`; ModelScope sometimes holds the connection without sending data; Tectonic's Rust client fails intermittently. `curl -4` to HF reaches ~9 MB/s. MinerU **creates `~/.mineru/config.yaml` by itself** on the first run.

**Tectonic 0.17.0:** passes 134/134 characters, polyglossia, unicode-math. Fonts in the package set must be loaded **by file name** (`texgyretermes` + `Extension=.otf`, `latinmodern-math.otf`). After warming up it runs with `--only-cached` (~1 s per run) — but each font size (for example `lmroman5` for subscripts) must have been loaded at least once during warm-up. The cache is at `~/.cache/tectonic` (~45 MB after warm-up).

**Three similar projects** (details in §18): their "untranslated" checkers rely on Chinese characters, so they flag a correct Vietnamese translation as an error. None of the projects matches figures and data outside formulas.

## 5. Approaches considered (settled)

| Question | Chosen | Rejected and why |
|---|---|---|
| How to rebuild | Build fresh from intermediate data → LaTeX → PDF | Overwriting text on the original page (inline formulas break easily); forking BabelDOC (internal API, AGPL) |
| Extractor | MinerU 4.x | Docling (inline formulas come out as plain text); Marker (needs an external LLM, GPL); PaddleOCR-VL later, through the same intermediate format |
| Builder | LaTeX (XeLaTeX via Tectonic) | Typst (would need LaTeX → Typst conversion); headless browser (heavy, poor column layout); cropping formulas/tables from the original (not structured characters) |
| Providing LaTeX | Use the copy already on the machine; if missing, auto-download Tectonic | Bundling it into the install package; making the user install it |
| Forking an existing project | Build it ourselves, borrowing ideas | The two closest projects have no license; the other MIT project is far from this architecture |

## 6. User experience

**The command** has three pieces of information, said naturally in Vietnamese or English:
- The PDF file (one or several).
- The target language — **required**.
- Where to save — **required**; a directory or a file path. If only a directory is given, the file name is `<base-name>.<lang-code>.pdf` (for example `attention.vi.pdf`).

If the language or save location is missing: the agent asks **once, combined in one sentence** ("Bạn muốn dịch sang ngôn ngữ nào, và lưu kết quả ở đâu?" — "Which language do you want to translate into, and where should the result be saved?"; if only one is missing, ask only the missing one), before running any command.

**The agent runs to the end without asking again**, except: language or save location missing; file not found; the result file already exists (ask before overwriting); tools not installed (ask once, combining install and download; §8.2). If the result directory does not exist, create it. Device, tier and terminology are decided by the agent.

**A scanned PDF does not stop the agent:** the agent says "the file is a scan, recognition is less accurate and takes longer" and still translates; below about 150 dpi the warning is stronger.

**While running:** brief reports by step (extracting content page 12/30; translating batch 3/12; building the PDF).

**When done**, a concise report: the path of the result file; the topic and a few key terms; the device and tier used (and whether it fell back to CPU); which pages are scans; formulas/tables that have not been cross-checked or are being shown as LaTeX source (§10), with page numbers.

**The target language** is the parameter `--lang <code>`. Everything specific to a language is gathered in a **language pack** (§11.4). Phase 1 has only the `vi` pack optimized and tested; other languages run with the default pack and the agent reports "untested".

## 7. Architecture

**Two layers:**
1. **The `rosettex` CLI** (Python). Deterministic, idempotent, makes no LLM calls. Every output for the agent is available as `--json`.
2. **A guidance layer for the harness** (`SKILL.md`, `AGENTS.md`): the §12 protocol, so the agent knows which commands to call, in which order, and the translation rules.

**Environment:**
- `rosettex` is a small Python package, installed with `uv tool install rosettex`.
- MinerU is installed into a **separate environment managed by `rosettex`** (for example `~/.rosettex/mineru-venv`, created with uv, version pinned within `>=4,<5`), called by subprocess. Reason: MinerU is heavy (≈666 MB + models); isolating it avoids dependency conflicts and allows independent upgrades. If the machine already has a usable `mineru-kit` 4.x, reuse it.
- Data directory: `~/.rosettex/` (`bin/`, `fonts/`, `mineru-venv/`, `device.json`, `resources.json`).

**Commands:**

| Command | What it does |
|---|---|
| `rosettex setup [--dry-run] [--yes] [--json]` | Finds existing resources, downloads what is missing, warms up the cache, ends with `doctor` (§8). Runs in the background. `--dry-run` only reports what can be reused and how much must be downloaded |
| `rosettex doctor [--json] [--refresh]` | Checks the environment, chooses device and tier, checks LaTeX (§8.3) |
| `rosettex extract <pdf> --lang <code> -o <work>` | Extracts content (§9). Runs in the background, returns immediately |
| `rosettex status [<work>] [--setup] [--json]` | Progress and the **next step** the agent must take |
| `rosettex terms <work> --file <terms.json>` | Writes and structure-checks the terminology table |
| `rosettex review <work> [--next] [--accept <id> --file <f>]` | Cross-checks formulas/tables (§10) |
| `rosettex next <work> [--json]` | Returns the next translation batch with context (§11.1) |
| `rosettex accept <work> --batch <id> --file <f>` | Receives the translation of one batch and **checks it immediately** (§11.2); on error it rejects and prints errors by `id` |
| `rosettex validate <work>` | Checks the whole paper before building |
| `rosettex render <work> --out <path> [--html]` | Builds LaTeX → PDF (§11.3). Runs in the background if long |
| `rosettex clean <work>` | Deletes the working directory |
| `rosettex install-skill --harness <name>` | Copies the guidance layer into the harness's proper place |

**Working directory:** default `<output-dir>/.rosettex/<base-name>.<lang-code>/`; kept after finishing for fast rebuilds.

**State and resuming:**
- `manifest.json` records the state of each step, each page group, each cross-check item, each batch (pending / running / done / failed).
- Background tasks write `progress.json` (with PID, last update time) and `logs/`.
- Every command is idempotent: rerunning skips what is already done.
- `status` detects that a background task has died and tells the agent to rerun the corresponding command.

## 8. Resources, installation, devices

### 8.1 Reuse first, download later
`setup` searches the machine first for every resource, reuses one only when it passes a check, downloads only what is missing. It records results in `~/.rosettex/resources.json`.

| Resource | Where to look | Condition for reuse | If missing |
|---|---|---|---|
| MinerU | `mineru-kit` on `PATH`; `~/.rosettex/mineru-venv` | Version 4.x; `mineru-kit version` runs | Create a dedicated venv with uv, install `mineru>=4,<5` (pinned version) |
| MinerU models | `mineru-kit models show` (reads the existing `model.base_dir`); `~/.mineru/models` | `mineru-kit models verify` passes for the tier to be used | `mineru-kit models download` only for the chosen tier, **plus tier `basic`** to be able to fall back to CPU |
| LaTeX compiler | `PATH`: `tectonic`, `xelatex` (TeX Live, MiKTeX, TinyTeX) | Can compile the sample document and passes the diacritics test of the target language | Prebuilt Tectonic for the right OS/architecture, **pinned version, checksum verified**, stored in `~/.rosettex/bin/` |
| TeX packages | Tectonic's cache; packages installed in TeX Live/MiKTeX | The sample document compiles | **Warm-up:** compile a sample document that uses every package of the LaTeX template, so later runs need no network |
| Fonts | TeX Gyre Termes in the TeX package set; system fonts (`fc-list`; the Fonts directory on Windows) | Covers the characters of the target language (smoke test) | A fallback font (Noto Serif, OFL) downloaded into `~/.rosettex/fonts/` |

- Cannot reach Tectonic's package server → try TinyTeX + XeLaTeX; if that still fails, say clearly what is still missing.
- Downloads can resume; files that have been verified are skipped.
- **Every download has a connection timeout and a hang watchdog** (no new bytes in ~60 s → cancel, resume the download). A hang or repeated failure → switch source (ModelScope ↔ Hugging Face) and force IPv4. Model files downloaded outside MinerU are checked by SHA-256 against Hugging Face's `lfs.oid`, then `mineru-kit models download --tier …` is rerun so MinerU writes `.mineru_complete`. Tectonic network failure during warm-up → retry.
- **LaTeX warm-up** with a sample document that uses every package **and every font size** (regular text, subscripts, second-level subscripts, `\operatorname`, footnotes).
- Never write into another Python environment of the user. **Do not rely on `~/.mineru/config.yaml`** (MinerU creates this file itself on first run): all MinerU configuration is passed through environment variables when calling the subprocess (`MINERU_MODEL_SOURCE`, `MINERU_MODEL_ONNX_DEVICE`, `MINERU_LLAMA_CPP_BACKEND`).

### 8.2 Installing through the agent
1. The agent asks the user **once**, combining install and download, using the estimated figure in `SKILL.md` ("Cần cài rosettex và tải tối đa khoảng X GB; phần đã có trên máy sẽ được dùng lại. Đồng ý không?" — "I need to install rosettex and download up to about X GB; what is already on the machine will be reused. Agree?").
2. Agreed → install `rosettex` → `rosettex setup --dry-run --json` to get the exact figure and tell the user (without asking again) → **immediately run `rosettex setup --yes`** (background) → follow it with `rosettex status --setup`.
3. `setup` ends with `doctor` and a smoke test.
4. If installing while a translation request is pending: once `setup` finishes, translate right away.

A manual install can still run `rosettex setup`; if skipped, the agent runs it on the first translation.

### 8.3 `doctor`: choosing device and tier
Based on MinerU's own tools, **without probing the GPU through PyTorch**:
1. Operating system, Python, RAM, free disk.
2. `mineru-kit version`, `mineru-kit models show` → which tier already has all its models.
3. `mineru-kit hardware info` → GPU, runtime, the device MinerU will use and why.
4. `mineru-kit hardware detect` → the maximum recommended tier.
5. **Choosing the tier:** default **`standard`** even with CPU only (spike 4.1: only 1.5 times slower than `basic`, but without `basic`'s systematic formula errors). Drop to `basic` when: available RAM < ~5 GB, the disk cannot hold the 1.24 GB GGUF model, or the user chooses `--tier basic`. Read `hardware detect --json`, not the printed text version.
6. **MinerU smoke test:** parse a 1-page sample PDF (shipped with the tool) with the chosen tier and device; measure time; catch out-of-memory and driver errors. On error, drop to CPU + `basic` and record the reason.
7. **LaTeX smoke test:** compile a sample page containing all the check characters of the language pack (Vietnamese: 134 accented letters) and one formula; extract the text back with `pypdfium2`, normalize to NFC, confirm all characters are present; the log has no "Missing character".
8. Write `~/.rosettex/device.json` (with the MinerU version, driver if any, LaTeX). It is invalidated when a version changes or on `--refresh`.

**Falling back on error:** if MinerU fails on the GPU midway, rerun **the remaining page groups** on CPU, same tier, with `MINERU_MODEL_ONNX_DEVICE=cpu` **and** `MINERU_LLAMA_CPP_BACKEND=cpu` (verified); on an out-of-memory error, drop further to `basic`. Record the reason in `manifest.json`, and the agent tells the user. The user can force it with `--device cpu|gpu` and `--tier`. GPU < 6 GB: MinerU itself chooses CPU for ONNX; do not force Vulkan for llama.cpp unless `doctor` measures it faster than CPU.

## 9. `extract`: extracting content
1. **Classify each page:** good text layer / no text layer (scan) / poor old OCR text layer (high ratio of odd characters, text not matching the image). Scanned or poor-OCR pages → run MinerU with `--ocr-mode ocr` for those pages (using `--pages`). A PDF mixing both kinds is accepted. Estimate the dpi of scanned pages to warn.
2. Run `mineru-kit parse … --format zip` by **page group** (`--pages`), saving each group's result into `mineru/` so it can be resumed. Never pass `--remote`. Note that `page_idx` in a group's result must be mapped back to the real page number.
3. **Convert to the intermediate format** `document.json`: block → span (MinerU has no line level). A block has a bbox (normalized 0-1) and a page; spans have type `text` / `inline_math`; separate blocks for `display_math` / `table` / `figure`; the source (text layer or OCR), LaTeX for formulas, cell structure for tables (**parsed from the HTML** `table_body`). The MinerU → intermediate format converter is a separate module; adding another extractor only needs adding an adapter.
4. **Merge paragraphs that were cut** across columns or pages; drop headers, footers, original page numbers.
5. **Detect internal links:** citations → references; "Figure 3", "Table 2", "Section 4", "Eq. (5)" → the corresponding object; record as labels and references to rebuild with `\label`/`\ref`/`\cite`. Also search **inside formula spans** (MinerU may recognize "Eq. (1)" as `\mathrm{Eq.~(1)}`) and extract them back out as text.
6. **Automatic keep-as-is list:** author names, affiliation lines, emails, the reference list.
7. Crop **figures** into images (`assets/`); render an image of each page (`pages/`).
8. Create the **translation batches** `batches/NNN.json`. Each paragraph has `id`, `type`, `section`, `page`, `text`, `placeholders`.
   - Inline formulas, citations, references, figures with units, and URLs are replaced by placeholders `⟦m1⟧`, `⟦c1⟧`, `⟦r1⟧`, `⟦n1⟧`…
   - **Text in table cells also enters batches** and must be translated; numbers in tables stay unchanged.
9. **Flag** into `flagged.json` (with a region image): formulas/tables whose LaTeX does not compile; paragraphs with signs of math (math font, Greek characters in the text layer) from which no formula could be extracted; an unusually long "formula" (a paragraph mistaken for one); formulas containing `$` or lowercase text (`e.g.`, `i.e.`) — text that slipped into a formula; formulas with diacritics (`\dot`, `\ddot`, `\hat`, `\tilde`, `\bar`) where the block's PDF text layer has no corresponding accent; OCR lines with figures (easy to confuse `0/O`, `1/l`).
10. **Normalize MinerU's LaTeX** before use: merge split digits (`0 . 0 1` → `0.01`, `4 0 0 0` → `4000`), merge `\operatorname { l o g }` → `\log`/`\operatorname{log}`; only whitespace changes, symbols do not.

## 10. `review`: cross-checking formulas and tables
Formulas and tables are always rebuilt from recognition, so this is the **core step** for keeping accuracy.
- `rosettex review` compiles each formula and table (in batches, in one LaTeX file with markers so errors can be separated by `id`) and creates a **comparison image**: the original region on the left, the rebuilt version on the right. MinerU gives only block-level bbox, so the comparison image of an inline formula is the **whole block** (the paragraph), and the rebuilt version is that paragraph with all of its inline formulas; the agent fixes the whole block in one go. Display formulas and tables use the images pre-cropped in MinerU's zip.
- For pages with a text layer: compare the characters of the PDF text layer inside the block's bbox with the characters of the rebuilt version to prioritize suspicious items (the text layer is a free "answer key"; compiling does not mean correct).
- `review --next` returns the next item to look at; `review --accept <id> --file <f>` takes the corrected LaTeX or table (or confirms no change) and trial-compiles it immediately.
- **Priority order:** flagged items → scanned pages → the rest. With GPU + a high tier: look at flagged items and sample the rest; with CPU or scans: look at everything.
- Harness cannot read images: skip the viewing step; the final report states clearly "formulas/tables have not been cross-checked".
- **Last resort:** if a formula/table still does not compile after fixing, show the **raw LaTeX source as text** in a marked box (still characters) and list it in the report.
- Tables: cell structure → `tabular` (`multirow`, `multicolumn`, `booktabs`); wide tables use `table*`, `\resizebox` or a rotated page.

## 11. Translating, checking, building

### 11.1 Terminology and context
- The agent reads the title, abstract and section headings; writes `terms.json` via `rosettex terms`: topic (one sentence), source → target term pairs, the keep-as-is list, abbreviations and symbols defined in the paper. Used only for this paper.
- `rosettex next --json` returns: the batch content, the paper summary, `terms.json`, the name of the section containing the batch, the **last paragraph of the previous batch** (original and translation), and the paths of the relevant page images. With subagents, several batches can be translated in parallel; each subagent calls `next` itself or receives exactly this package.

### 11.2 `accept` (check on submission) and `validate` (check the whole paper)
All comparisons run on text **normalized to Unicode NFC**. A batch is rejected if:
- an `id` is missing or a paragraph is empty;
- a placeholder is missing, extra or modified (compared as a **multiset**; *reordering is allowed* — Vietnamese word order differs from English; amended 2026-11-18, awaiting approval together with the slice 1 spec);
- **the multiset of figures** (outside placeholders): every number of the original must be present with exactly the same value; the translation may add numbers when the original wrote them in words ("two" → "2", "1990s" → "những năm 1990"), but may not drop or change a value (amended 2026-11-18, awaiting approval together with the slice 1 spec);
- **suspected untranslated** per the language pack (§11.4), except paragraphs on the keep-as-is list;
- the translation/original length ratio is outside the language pack's thresholds (a warning, not a rejection);
- a term in `terms.json` is not used consistently (a warning);
- there are characters outside the alphabet of the target language and outside the allowed character set (a sign of an encoding error).

`validate` reruns all checks on every batch, plus: every formula and table compiles individually; the numbers of figures, tables, numbered equations and references match the original. Errors are printed by `id`; the exit code is non-zero while errors remain.

Translation rules (written in `SKILL.md`): keep placeholders; keep number formatting as in the original (decimal point); do not translate proper names, method/model names, the reference list, author names, units, emails; preserve the degree of certainty of the original sentence (may, suggest, significant…); check negation and the direction of comparison carefully.

### 11.3 `render`
- Generate `out/main.tex`, compile with the compiler chosen in §8 (Tectonic, or the XeLaTeX already present), running enough passes for references to settle. Runs in the background if long.
- Paper size and margins close to the original; `twocolumn` if the original is two-column (detected from bboxes). Figures and tables are floats placed near their original position (`figure*`/`table*` for objects spanning two columns).
- **Default font: TeX Gyre Termes** (via `fontspec`); fallback Noto Serif. **Math font: Latin Modern Math** (`unicode-math`). Language via `polyglossia`.
- `hyperref` for internal links; the reference list stays in English.
- Whole-paper compile error: find the `id` causing the error from the log, move that item to the last resort (§10), recompile, record it in the report.
- **Post-build check** (mandatory before reporting done):
  - the log has no errors, no "Missing character", no undefined references;
  - the PDF text layer contains no `[?]` or `??`;
  - extracting text from the PDF shows the formulas and tables (as characters);
  - the number of internal links matches the number of citations and references;
  - **content length ratio** (total used column height of the translation / the original) ≤ about 1.3. Not measured in whole pages.
- Write `report.json` for the agent to use when reporting the result (§6).

### 11.4 Language packs
Each pack (`langpacks/<code>/`) consists of:
- **Fonts:** the font, the fallback font, the `polyglossia` configuration.
- **Display check character set:** for Vietnamese, the 134 accented letters.
- **Allowed alphabet** (for the encoding-error check).
- **The "untranslated" detector.** It does not use the ratio of Han characters. For Vietnamese, a paragraph long enough (threshold set by the builder) is considered suspected-untranslated when:
  - it matches the original text exactly after normalization, or
  - the ratio of Vietnamese-specific accented characters (ă â đ ê ô ơ ư and the letters carrying tone marks) is unusually low, **and** the ratio of Vietnamese function words (của, và, là, các, những, được, trong, cho, với, không…) is low.
  - Formulas, URLs, code, and proper names in `terms.json` are ignored in the computation.
- **Length-ratio thresholds.**
- **Display labels** for the report and HTML.

The default pack (for a language with no pack): Noto Serif, only checking for an exact match with the original.

## 12. The agent protocol (content of `SKILL.md`)
0. Determine the file, target language, save location. Language or save location missing → ask (one sentence, combined) and stop to wait. Result file already exists → ask before overwriting. `rosettex` not installed → the 8.2 flow.
1. `rosettex status --setup`: if `setup` is not finished, continue it; if the device cache has expired, run `rosettex doctor`.
2. `rosettex extract …` (background); call `rosettex status` periodically until done; give brief progress reports. If it is a scan, tell the user.
3. Read the title, abstract and section headings → `rosettex terms`.
4. Loop `rosettex review --next` / `--accept` until no items remain (if images can be read).
5. Loop `rosettex next` → translate → `rosettex accept` until no batches remain; a rejected batch is fixed per the errors and resubmitted. With subagents, translate in parallel.
6. `rosettex validate`; fix by `id` until clean.
7. `rosettex render --out …` (background if long). If images can be read, look at the first page, one page with a table and one page with formulas of the resulting PDF.
8. Report the result in the template of §6, using `report.json`.

If interrupted, or the context is compressed, at any step: run `rosettex status` and follow the "next step".

**The skill's description** must be triggerable by natural Vietnamese and English phrasing ("dịch bài báo" (translate the paper), "dịch file PDF này sang…" (translate this PDF file into…), "translate this paper/PDF to…"). `SKILL.md` records the estimated size figure for the question in 8.2.

## 13. Phases
Phase 1 is done in **vertical slices**, each with its own spec and plan:
- **Slice 1:** PDF with a text layer → extract → terms/next/accept → validate → render; manual install; formulas/tables that do not compile use the last resort.
- **Slice 2:** `review` (comparison images, fixing formulas/tables).
- **Slice 3:** `setup`, `doctor`, fault-tolerant model download, GPU and fallback to CPU, `install-skill`.
- **Slice 4:** scan, OCR, mixed PDFs.

- **Phase 1:** setup, doctor, extract (MinerU, device/tier choice, fallback to CPU, scan, paragraph merging, internal links, text in tables), status, terms, review, next/accept, validate, render (LaTeX, post-build check), the `vi` language pack and the default pack, `SKILL.md` + `install-skill` for Claude Code.
- **Phase 2:** bilingual HTML for offline reading (one self-contained file, KaTeX embedded, hover to see the original sentence, table of contents, search); an agent loop that looks at and fixes the layout by itself; guidance layers for Codex, Antigravity, Hermes; an MCP server; a PaddleOCR-VL adapter (optional).

## 14. Testing

**Self-made test set (no network needed):** generate sample papers in LaTeX in the test directory and compile them into PDFs; the content is known in advance so it can be checked exactly.
- Two columns (title, authors, abstract, inline formulas, numbered equations, a table with `multirow`, figures, footnotes, citations, reference list).
- A single column heavy in math.
- A multi-page paper with paragraphs crossing pages and columns.
- A "scan" version (rasterize the PDF into images and reassemble).
- A PDF mixing scanned pages and normal pages.

**Real test set:** 3-5 real papers supplied by the user or downloaded on a machine with network.

**Unit tests** (simulated, no MinerU or GPU needed):
- choosing tier/device from sample `hardware info/detect` output;
- falling back to CPU when a failure is simulated;
- resuming after a background task is killed;
- reusing existing resources (models, LaTeX, fonts);
- resuming a download after a network error;
- `accept` rejects: a changed figure (`0.05` turned into `0,05`), a broken/missing/value-changed placeholder, a submission of the unchanged English text;
- `accept` accepts: a correct Vietnamese translation (a regression test for an error seen in other projects), including a translation that reorders placeholders (compared as a multiset), a translation that adds numbers, and a translation in NFD form (normalized to NFC, the stored copy is always NFC);
- the MinerU → intermediate format converter, run on sample JSON files saved from a real MinerU run.

**Acceptance criteria for phase 1:**
- `validate` is clean; the PDF compiles successfully;
- formulas and tables are characters; text in table cells has been translated;
- content length ratio ≤ about 1.3; no overflowing text or overlapping figures;
- all Vietnamese diacritics present; citation links are clickable; no `[?]`/`??`;
- `doctor` chooses correctly on a CPU-only machine and a machine with a GPU (if available);
- `setup` does not re-download resources already present;
- the whole procedure runs in Claude Code from just one user command;
- the user reads it and finds it acceptable.

## 15. Proposed code structure (the builder may change it)
```
rosettex/
  cli.py                 # command definitions
  workspace.py           # manifest, progress, background running, idempotent
  setup/                 # resources, download, Tectonic, fonts, mineru venv
  device.py              # call mineru-kit hardware/models, choose tier, fallback
  extract/
    classify.py          # classify pages (text layer / scan / poor OCR)
    mineru_runner.py     # call mineru-kit by page group
    adapters/mineru.py   # MinerU middle_json → document.json
    segment.py           # paragraph merging, placeholders, batches, links, keep-as-is
  review.py              # comparison images, compile each formula/table
  translate_io.py        # terms, next, accept
  checks/                # checkers shared by accept/validate/render
  render/
    latex.py             # document.json + translation → main.tex
    compile.py           # Tectonic/XeLaTeX, read log, map errors back to id
    postcheck.py         # post-build check, report.json
  langpacks/vi/, langpacks/default/
  skill/SKILL.md, skill/AGENTS.md
tests/fixtures/          # sample .tex papers, sample MinerU JSON
```

## 16. To check first (the first task of the plan)
> **Status 2026-11-18:** items 1-6 were checked on Linux, results in §4.1. Remaining: item 2 on NVIDIA ≥ 6 GB and Apple Silicon; item 4 on macOS/Windows; item 6 on scanned pages; item 7 is measured in slice 1.

1. The output of `mineru-kit parse --format middle_json` on a real PDF: whether there is a bbox for each `inline_equation` or only at line level; the exact schema of blocks, lines, spans, tables (cell structure or HTML), figures.
2. A reliable way to force the device for `mineru-kit parse` (which environment variable takes effect with which tier); what `hardware detect` returns on a machine with an NVIDIA GPU and on Apple Silicon.
3. The models and size of each tier (`models show`); whether `models verify` can detect models placed in a different `model.base_dir`.
4. Tectonic: a prebuilt version can be downloaded on all three OSes; TeX Gyre Termes and Latin Modern Math can be loaded through `fontspec` from the package set; `polyglossia` + `vietnamese` works; a mode that runs without network after warm-up.
5. The proportion of MinerU's LaTeX that compiles right away at tiers `basic` and `standard`.
6. MinerU time on CPU for a 20-30 page paper at tiers `basic` and `standard` (to set the page-group size and the `status` cycle).
7. The agent keeps placeholders and number formatting across many batches (measure the rate of batches rejected by `accept`).

Items 1-3 and 6 need a machine with network access to Hugging Face/ModelScope. If your environment is blocked, stop and ask the user to run the following commands on their machine:

```bash
uv venv --python 3.12 mineru-test && source mineru-test/bin/activate
uv pip install "mineru>=4,<5"
mineru-kit hardware detect && mineru-kit hardware info && mineru-kit models show
time mineru-kit parse paper.pdf -o out --format middle_json --tier basic
time mineru-kit parse paper.pdf -o out_std --format middle_json --tier standard
```

Save the resulting JSON files into `tests/fixtures/` as data for the converter.

## 17. Decided by the builder
The CLI library; the size of translation batches and page groups; the thresholds of the checkers; the algorithms for detecting two columns, merging paragraphs, and recognizing links; the sampling rule when cross-checking on GPU; how to place floats near their original position; the content of the sample PDF for the smoke test; the pinned versions of MinerU and Tectonic; the estimated size figure in `SKILL.md`; the detailed code structure.

## 18. Reference: similar projects
| Project | License | Allowed | Ideas worth borrowing |
|---|---|---|---|
| craftsfool/PaperReader.skill | None (explicitly states no open-source license specified) | Only learn ideas, **do not copy code** | `next` returns a batch with page images and terms; `accept` checks each batch; a terminology table and notes as working memory between contexts |
| NonTrivialLiu/english-paper-pdf-translation | No LICENSE file | Only learn ideas, **do not copy code** | Look for `[?]`/`??` in the final PDF; count figures/tables/equations/references against the original; strict LaTeX log checking |
| Biomedical-Engineer-uestc/mineru-pdf-translate | MIT | Code may be reused, with attribution | How to call `mineru-kit parse --tier`; automatically keeping author and affiliation lines as-is; splitting references with a `REF` placeholder |

All three target Chinese. mineru-pdf-translate's "untranslated" checker flags a correct Vietnamese translation as an error; PaperReader accepts a translation whose figures were altered. These are two errors `rosettex` must avoid (with regression tests in §14).

## 19. Settled decisions
**Decided by the user:** the agent is the translator, no external services called · translate the whole paper, input is PDF · rebuild from scratch, accepting a layout that differs slightly from the original · MinerU as the extractor, with a mechanism to use the GPU · LaTeX as the builder, formulas and tables always characters · terminology per paper only · one command; target language and save location are required, ask if missing · reuse existing resources, pre-download after installing through the agent · build ourselves rather than fork.

**Proposed by the designer, approved by the user:**
1. Keep two columns if the original is two-column.
2. The reference list, author names, affiliations, emails are not translated.
3. Figures are cropped from the original pages.
4. PDF in phase 1, HTML in phase 2.
5. Content length ratio ≤ about 1.3 (measured by column height, not by page count).
6. ~~CPU only defaults to tier `basic`~~ → **amended 2026-11-18 per the spike:** default `standard` even with CPU only; `basic` when RAM/disk is short or the user chooses it.
7. The `doctor` result is saved, and expires automatically when a version changes.
8. A dedicated intermediate format so the extractor can be swapped.
9. The agent runs to the end without asking again, except for the cases in §6.
10. If only a directory is given, the file name is `<base-name>.<code>.pdf`; create the directory if missing; ask before overwriting.
11. The target language is a parameter; phase 1 only optimizes and tests Vietnamese.
12. Keep the working directory; delete it with `rosettex clean`.
13. Tectonic is the default compiler when the machine has no usable LaTeX; not bundled into the install package; TinyTeX as a fallback.
14. Last resort for formulas/tables that do not compile: LaTeX source as text in a marked box.
15. Keep the decimal point as in the original.
16. Font TeX Gyre Termes (fallback Noto Serif); math font Latin Modern Math.
17. Scanned PDFs are supported, with only a warning; below about 150 dpi a stronger warning.
18. ~~Phase 1 only builds the guidance layer for Claude Code~~ → **amended 2026-11-18 per the user** ("must run on every machine, every operating system and every harness"): from slice 1, the protocol has a single source, printed by `rosettex guide`, generating `SKILL.md` and `AGENTS.md`; the core complies with §3.1.
19. Only download MinerU models for the chosen tier, plus tier `basic` to fall back to CPU.
20. When installing through the agent: ask once combining install and download, then run `setup` immediately.
21. Hardware probing and model management go through `mineru-kit hardware` and `mineru-kit models`; MinerU is installed in a separate environment managed by `rosettex`.
22. Check each batch immediately on submission (`accept`), plus a whole-paper check (`validate`).
23. All comparisons run on NFC-normalized text.

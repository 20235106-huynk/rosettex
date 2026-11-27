---
name: rosettex
description: Translate a scientific paper from its LaTeX source (a folder, an archive, or an arXiv ID/link) or a born-digital PDF into another language and build the translated PDF with the paper's own layout. Use when the user asks to translate a paper/article. You translate; the rosettex CLI prepares, checks and typesets.
license: MIT
metadata:
  rosettex-version: "1.0.3"
---

# rosettex — translate a scientific paper

Hard rules:
- Never read rosettex's source code or installed package files (everything you need is here and in command results).
- Never write files outside the work directory (and the output PDF path).
- `rosettex check` is the only validator you need — never write your own scripts to check or generate translations.
- Always run the `next_argv` of the last result. If it contains a placeholder (`<code>`, `<source>` or `<candidate>`), replace <…> with the value `next_action` names; `build --out` may be changed to the path the user wants.
- Every result has `state`: `partial` = the time budget ran out at a safe point, run `next_argv` again (it continues); `ask_user` = ask the user `ask_user.question` (offer `ask_user.options`) before running `next_argv`; `error` = read `message`.
- Run one `rosettex … --json` command at a time.

Flow:
`rosettex fetch <source> -o <work>` (folder, archive, arXiv ID/link, or local `.pdf`) → `rosettex prepare <work> --lang <code>` → read `read/*.md` → fill `terms.md` (`field:`, Terms, Expressions) → append `sNNNN <translation>` lines to `out/NN.txt` → `rosettex check <work>` (fix by appending lines) → `rosettex build <work> --out <file.pdf>` → if `state: "needs_repair"`, follow `diagnosis` (TeX route: append a line to `out/*.txt`, `rosettex fix <work> --file <path>` then edit `work/fix/<path>`, or edit `work/build.json`; PDF route: shorten the overflowing translations listed in `diagnosis` by appending corrected `sNNNN <translation>` lines to `out/*.txt`) and build again.

How to run rosettex:
`rosettex …` (installed on PATH with `uv tool install`; also accept a launcher given by the user).

Translation loop in <work>:
1. Read sheets (read/*.md) in <work>/read/.
2. Fill <work>/terms.md: set `field:` first (from title + abstract), fill key Terms, and add any figurative/field-specific expressions you meet to Expressions.
3. Append answers (`sNNNN <translation>`) to <work>/out/NN.txt.
4. Run `rosettex check <work>` until ok, then `rosettex build <work> --out <file.pdf>`.

Translation rules:
- Placeholders (`⟦mN⟧`, `⟦rN⟧`, `⟦cN⟧`, `⟦sN⟧`, `⟦lN⟧`): keep each once; move freely. Keep `⟦bN⟧…⟦/bN⟧` ordered, nested, non-empty. Never copy `  ~ …` legends.
- `sNNNN = sMMMM` lines are auto-filled; do NOT answer them.
- Corrections: append a new `sNNNN <translation>` line to `out/*.txt` (later line wins; never rewrite whole files).
- Numbers/dates: keep exact values in the <lang> locale (e.g. decimal-comma locales like vi: `0,5`, `1.000`, lists `(1; 2)`). Dates like `December 2018` allow numeric months (`tháng 12 năm 2018`).
- Field & meaning: decide `field:` first. Translate by meaning in that field, never word for word. Render idioms, metaphors and figurative jargon with an equivalent expression in <lang> (e.g. "the workhorse of NLP" means the dominant/main model family, not a literal draft animal); prefer the field's established terms in <lang> over calques.
- Terms & Expressions: in `terms.md`, `=` keeps a term unchanged, empty rows are ignored, and `check` enforces every filled row across both Terms and Expressions.
- PDF route (when `route: "pdf"`):
  - Formula without a placeholder: copy it verbatim (rosettex wraps it automatically).
  - Flagged placeholder `⟦mN?⟧`: drop `⟦mN?⟧` and translate it only when its preview in the `~` legend is clearly an ordinary word of the sentence; when unsure (e.g. `ETmiss`, `mmin`), keep `⟦mN⟧`.
  - Budget line (`~ p.N · ≤ M chars`): character budget for the box on page N; stay within it when possible (`check` warns if >25% over).
  - Repair diagnosis (`state: "needs_repair"`): lists overflowing segments with target character counts; shorten those translations (keep placeholders), append corrected `sNNNN <translation>` lines to `out/*.txt`, and re-run `build`.
  - `direction_change` error: the PDF route cannot change writing direction (LTR↔RTL); tell the user to translate from the paper's TeX source instead.

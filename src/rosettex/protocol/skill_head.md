---
name: rosettex
description: Translate a scientific paper from its LaTeX source (a folder, an archive, or an arXiv ID/link) or a born-digital PDF into another language and build the translated PDF with the paper's own layout. Use when the user asks to translate a paper/article. You translate; the rosettex CLI prepares, checks and typesets.
license: MIT
metadata:
  rosettex-version: "{version}"
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

---
name: rosettex
description: Translate a whole scientific paper PDF (text layer required) into another language and rebuild it as a PDF that keeps formulas, tables, numbers, citations and clickable cross-references. Use when the user asks to translate a paper, article or research PDF, in any language (e.g. 'translate this paper to Vietnamese'). Needs a shell and file read/write; you do the translating and the rosettex CLI does extraction, checking and typesetting. Scanned or image-only PDFs are not supported yet.
---

Run `rosettex guide` to see the latest version of the protocol below, then follow it.

# rosettex — PDF paper translation protocol

You (the agent) are the translator. `rosettex` extracts the content, splits it into batches, checks it and builds the PDF. General rules:
- Run only **one** `rosettex …` command at a time, always with `--json`. Do not chain commands and do not use pipes (no `| head`), even to shorten the output.
- Never edit the files in the work directory by hand; change things only through `rosettex` commands. Keep your own files (terms, translations) in a folder of your choice, for example `<work>/agent/`.
- Every result has a `next_action`: that is the next command to run.
- A command that returns exit code 2 hit an environment or usage error: tell the user the `error` and the `next_action`, then stop.
- `next_action` is null together with `error` → report the error to the user and stop.

## 0. Before running
- You need all three of: the PDF file, the target language, and where to save (a directory or a .pdf file). Do not guess the target language (no default to Vietnamese or English). If the language or the save location is missing, ask the user **one combined question** (for example "Which language do you want to translate into, and where should I save the result?"; if only one is missing, ask only for that), then stop and wait for the answer.
- The user names the target language in words. Use its BCP-47 code (`fr`, `zh-Hans`, `fa`); for a language written in several scripts add the script subtag (`sr-Latn`, `sr-Cyrl`). Ask the user only if the language itself is unclear.
- If the `rosettex` command cannot be run, tell the user to install it following the rosettex README.

## 1. Extract
Run `rosettex extract "<pdf>" --lang <code> --out "<save location>" --json`.
- `output_exists: true`: ask the user whether to overwrite the output file, and remember the answer for step 5.
- `lang_tested: false`: tell the user "this language has not been tested".
- Tell the user the language, the code and the `script` from the result (for example: Persian → `fa` → Arabic script). `script_known: false`: ask the user which writing system the language uses, then stop. `pack_found: false`: tell the user "there is no language pack for <code>; the checks are generic for the <script> script". `font_missing: true`: tell the user that rosettex is damaged and must be reinstalled, with the files in `fonts_needed`; stop.
- Note the `work` value (the work directory).

Then every 30–60 seconds run `rosettex status "<work>" --json` once, until `state` is no longer `extract_running`. Give the user a short progress update based on `step`.
If your environment cannot wait between commands (no sleep allowed), add `--foreground` to the `extract` command instead: it returns only when extraction is finished (a few minutes).

## 2. Terms
Run `rosettex next "<work>" --outline --json` to read the title, abstract and section headings. Write a JSON file:
`{"topic": "<one sentence>", "terms": [{"src": "<source term>", "dst": "<translation>"}], "keep": ["<keep unchanged>"], "abbreviations": [{"abbr": "<abbreviation>", "expansion": "<full form>"}]}`
then run `rosettex terms "<work>" --file "<file>" --json`.

## 3. Translate batch by batch
Repeat these steps:
1. Run `rosettex next "<work>" --json`. If you get `done: true`, go to step 4.
2. Translate every `batch.items[].text` following the rules at the end of this document. Use `terms`, `topic`, `section` and `previous` (the last paragraph of the previous batch) to keep the text coherent.
3. Write a JSON file `{"<id>": "<translation>"}` containing every `id` of the batch and no other key: an empty table cell has no id, do not invent one. Write the file as UTF-8 JSON; a character you cannot type, such as U+200B, can be written as `\u200b`.
4. Run exactly the `rosettex accept …` command in `submit`, replacing `<file.json>` with the file you just wrote.
5. If you get `ok: false`, fix exactly the `id`s listed in `errors` and resubmit the whole batch.

The most common errors: a placeholder copied with the wrong number (`⟦r2⟧` where the source has `⟦r1⟧`; copy each one exactly), a number written as a word or in local digits ("two", "两", "दो", "٣"), an id that is not in the batch, text left in English, and for Thai and Myanmar a long run of letters with no U+200B between the words.
The reference list is not in any batch: it stays in English, as in the source, and is printed in the PDF. Text drawn inside figures is not translated either (figures are kept as pictures).

Also:
- If you can read images, look at `page_images` when a source sentence is hard to understand.
- If you have subagents, give each subagent one batch via `rosettex next "<work>" --batch <id> --json`. Each subagent submits its own batch.

## 4. Validate the whole paper
Run `rosettex validate "<work>" --json`. If errors remain, resubmit the batches containing the reported `id`s, until `ok: true`. If you resubmit any batch after a clean `validate`, run `validate` again before step 5.

## 5. Build the PDF
Run `rosettex render "<work>" --json`. Add `--overwrite` if the user agreed to overwrite in step 1. `font_missing: true`: same as in step 1.

Then every 30–60 seconds run `rosettex status "<work>" --json` once, until `state` is `done`. If your environment cannot wait between commands, run `rosettex render "<work>" --foreground --json` instead (add `--overwrite` as above); it returns the result when the PDF is built. If you can read images or PDFs, open the output file and look at the first page, a page with a table, and a page with a formula.
Never change the meaning or the notation of the source to make a check pass (for example do not replace "µs" or "—" with words because of a missing-glyph problem): report the problem to the user instead.

## 6. Report the result
Read the `report.json` file (its path is in `status`) and give the user a short report:
- the path of the output file;
- the topic and a few key terms;
- the tier used;
- the items in `fallback`, with pages: formulas or tables shown as LaTeX source;
- the items in `flagged`, with pages: suspicious formulas;
- `dropped`: the number of source content items that the extractor could not read and that are therefore missing from the translation (unknown block types, spans or table/figure parts, and text that belongs to a figure; details in `document.json`, `source.dropped`). If it is not 0, tell the user plainly that the translation may be missing those parts;
- `floats_off_page`: figures or tables that could not be placed on the page that first mentions them (usually one page later);
- the sentence "formulas and tables have not been checked against the source";
- the problems in `postcheck.problems`, if any, and the `postcheck.warnings` (for example a content length ratio unusual for the language: ask the user to look at the layout); `postcheck.missing_glyphs`: tell the user which characters could not be displayed and in which font.

## Interrupted or lost context
Run `rosettex status "<work>" --json` and follow `next_action`. If you do not remember `<work>`, it is at `<directory containing the output file>/.rosettex/<source PDF file name>.<language code>`.

## Translation rules
- Keep every placeholder ⟦…⟧ exactly as in the source (you may move it within the sentence to read naturally).
- Keep every number and its notation exactly as in the source, including the decimal point: 0.05 stays 0.05, do not write 0,05.
- Do not translate proper names, method/model/dataset names, units, emails, URLs.
- Keep the level of certainty of the source sentence (may, might, suggest, likely, significant…).
- Check negations and the direction of comparisons carefully (more/less, higher/lower, outperform, at least/at most).
- Use the terms in terms.json exactly, consistently across the whole paper.
- Table cells: translate the text, keep numbers and symbols unchanged.
- Write natural academic Vietnamese; add no translator notes.

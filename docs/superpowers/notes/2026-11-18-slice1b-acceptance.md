# Slice 1b acceptance record (2026-11-18)

Status: **implemented; reader verdicts and the real-agent run are open.** Suite: 219 passed, 2 skipped (real MinerU). `vi` unchanged (golden `tests/golden/vi_short_main.tex` byte-identical).

## What was verified here
- **Fonts:** `scripts/build_fonts.py` rebuilds all 14 packaged fonts byte for byte from the official releases (sha256 of every source zip checked). The wheel is 12.5 MB and ships the 14 fonts, `MANIFEST.json` and 14 licence texts.
- **Every pack builds:** `test_render_short_in_every_script` renders `short.pdf` in all 21 packs (`vi`, `fr`, `ru`, `uk`, `zh-Hans`, `zh-Hant`, `ja`, `ko`, `ar`, `fa`, `ur`, `hi`, `mr`, `bn`, `te`, `ta`, `gu`, `kn`, `th`, `am`, `my`) with `missing_glyphs == []`, no dotted circle, no undefined reference, script characters present in the PDF text, ASCII digits present.
- **Fail early:** a damaged packaged font stops `extract` (exit 2, `font_missing`, before MinerU and before creating anything) and `render`.
- Arabic, Urdu, Korean and Thai first pages were read as images: Arabic/Urdu first column on the right, ASCII digits, table mirrored, bibliography left-to-right, `المراجع`/`حوالہ جات` in the right order; Korean has spaces; Thai breaks inside paragraphs.
- Cold Tectonic cache (Task 11): see "CI" below.

## Sample PDFs and page-1 PNGs for the reader
`/home/admin/Desktop/Learn/rosettex-slice1b-samples/out.<code>.pdf` and `.png` (21 each; fake translations: one sample sentence repeated, so table cells are tiny and headings repeat — judge script, direction and breaking, not wording).

| Pack | What the reader judges | Reader | Verdict |
|---|---|---|---|
| ar, fa, ur | shaping, right-to-left columns, table direction, numbers, labels | | |
| hi, mr, bn, te, ta, gu, kn | conjuncts and matras, labels | | |
| th, my | word breaking, line ends, shaping | | |
| zh-Hans, zh-Hant, ja, ko | glyph set, line-start/line-end punctuation, Korean spacing, labels | | |
| am | Ge'ez shaping, labels | | |
| fr, ru, uk | labels, hyphenation | | |

Labels and pack rules were written by the builder and are unconfirmed. **No pack is marked `tested: true`.**

## Decisions and deviations (rulings)
- Task 2 mini-papers were replaced by the Task 9 builds of the real renderer (tables, `\multirow`, cites, footnotes, bibliography); `numerals` read from `gloss-persian.ldf`, `gloss-urdu.ldf` (`western`) and `gloss-arabic.ldf` (`maghrib`).
- RTL bibliography uses `{\selectlanguage{english}…}`; `otherlanguage{english}` around `thebibliography` fails in Tectonic (`Missing } inserted`, bisected). Its `\refname` is wrapped in `\RL{…}` so two-word labels keep their order.
- Noto Naskh Arabic has no Latin letters or parentheses: for `rtl`, `\text`, equation tags and whole English verbatim spans (authors, e-mails, daggers) are routed through the Latin font.
- Bundle-font licences were set from upstream knowledge (GUST Font License for TeX Gyre, OFL for Noto Serif), not re-fetched from CTAN.

## Font size (fix after first reading of the samples)
Script fonts drew larger than Termes (box height 5.5–6.8 pt against 4.7 pt). Now one visual size: `Scale=MatchLowercase` for `runs`/`rtl` script fonts, an explicit profile `scale` where that has no effect (Telugu 0.75, Kannada 0.75, Ethiopic 0.8, Bengali 0.9, Gujarati 0.9), `Scale=0.9` for Noto Serif (Cyrillic). Median body box height is now about 4.6–5.0 pt in most packs; Telugu, Kannada, Bengali and Gujarati stay at 5.4–5.9 pt (tall glyph boxes) and need the reader's eye. `vi` unchanged.

## Real run with MinerU 4.2.3 (2026-11-18)
Run by the builder session acting as the agent (not a fresh session). `short.pdf` → Arabic: 1 batch accepted at the first submission; first render failed `postcheck` because Noto Naskh Arabic has no brackets, quotes, en dash (the corrected log parsing found it); fixed (spec §15 item 11), second render `ok`, 0 missing glyphs, length ratio 0.91 (`~/Desktop/Learn/rosettex-ar-demo/short.ar.pdf`). `2611.23202v1.pdf` (13 pages) → Vietnamese: 21 batches, all accepted at the first submission except one (two cells not in the batch), PDF of 16 pages, `postcheck` only complains of length ratio 1.33 > 1.3; MinerU dropped 6 items (aside text and caption parts) and merged the last row of Table I. Chinese/Japanese, Hindi and "Klingon" were not run.

## Not done / open
- **Real agent run** (Arabic, Chinese/Japanese, Hindi, "Klingon") needs real MinerU and a fresh agent session: not run. Thresholds (`min_target_ratio`, `max_length_ratio`, Thai U+200B run limit) are therefore initial values.
- XeLaTeX (not installed), macOS, Windows: not tested.
- Samples: the fake translator now puts a short stand-in in table cells (before, a whole sentence per cell made `adjustbox` shrink the table to dots). Real translations with very long cells would still shrink a table; not changed (would alter `vi` output).
- Thai heading spacing looks loose (stretch of `\hspace{0pt plus 1pt}` in headings); reader to judge.
- Hyphenation is lost for packs without polyglossia (`zh-*`, `ja`, `ko`, `gu`, `my`).

## CI
Cold Tectonic cache (empty HOME, only the Tectonic binary copied): 21 builds passed in 7 min 31 s, under the 10-minute bar; no CI change made (.github/workflows/ci.yml untouched). Wheel: 12.5 MB.

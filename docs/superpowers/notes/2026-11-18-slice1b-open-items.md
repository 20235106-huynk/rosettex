# Slice 1b: open items (2026-11-18)

Slice 1b is implemented and merged-ready on branch `slice1b` (233 tests pass, 2 skipped). These items remain. Tick them off here.

## Needs you
- [ ] **Reader check of the 21 packs** (table in `2026-11-18-slice1b-acceptance.md`): shaping, direction, table direction, line breaking, labels, numbers. Samples (regenerated 2026-11-18 from the final code): `~/Desktop/Learn/rosettex-slice1b-samples/`; regenerate with `uv run pytest tests/test_render.py -k every_script --basetemp=<dir>`. Only then set `tested: true` for a pack.
- [ ] **Confirm labels and pack rules** (figure/table/references words and per-pack rules were written by the builder).
- [x] **Long table cells:** wrapping `p{}` columns when a table would shrink (slice 1b spec §15 item 10); `vi` golden unchanged.
- [x] **SG amended** with the slice 1b details (end of SG §11.5).

## Needs a real environment
- [ ] **Real agent run** (acceptance Task 12 step 2; `mineru-kit` is not installed on this machine, so it cannot be automated here): fresh Claude Code session, real MinerU, `ROSETTEX_REAL=1`: translate `tests/fixtures/papers/short.pdf` into Arabic, Simplified Chinese (or Japanese), Hindi by name; then "Klingon" (expect: asks for the writing system and stops). Record the code and script reported, questions asked, `lang_tested: false` reported, `postcheck`, rejected batches, `length_ratio`, overfull lines.
- [ ] **Tune thresholds from real translations:** `min_target_ratio` per pack, `max_length_ratio`, Thai/Myanmar U+200B run limit (25) and 40-letter rule, per-profile `scale` (Telugu 0.75, Kannada 0.75, Ethiopic 0.8, Bengali 0.9, Gujarati 0.9).
- [ ] **XeLaTeX** (not installed here), **macOS**, **Windows**: run `tests/test_render.py -k every_script`.
- [x] **Licences of the 3 bundle fonts** re-checked on CTAN 2026-11-18 (`tex-gyre`: gfl, `lm-math`: gfl, `noto`: ofl + lppl): the strings in the profiles are correct.

## Known and left on purpose
- Hyphenation is lost for packs without polyglossia (`zh-*`, `ja`, `ko`, `gu`, `my`).
- Thai headings look loose (stretch of `\hspace{0pt plus 1pt}`).
- CJK `scale` and the `\LARGE` title are not tuned.
- `scripts/build_fonts.py` writes the manifest without a temp file and calls `subprocess` (dev only); `fonts.py` rehashes fonts on every resolve (milliseconds).
- Latin-script languages without a pack only get the foreign-letter check (no untranslated detector beyond exact copy).
- Wrapped tables: the width estimate is heuristic (0.5 em per non-CJK character); read real tables in the reader check.
- Test `test_render_short_in_every_script` skips RTL text checks (Arabic extraction is unreliable).

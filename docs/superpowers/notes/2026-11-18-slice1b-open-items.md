# Slice 1b: open items (2026-11-18)

Slice 1b is implemented and merged-ready on branch `slice1b` (233 tests pass, 2 skipped). These items remain. Tick them off here.

## Needs you
- [ ] **Reader check of the 21 packs** (table in `2026-11-18-slice1b-acceptance.md`): shaping, direction, table direction, line breaking, labels, numbers. Samples (regenerated 2026-11-18 from the final code): `~/Desktop/Learn/rosettex-slice1b-samples/`; regenerate with `uv run pytest tests/test_render.py -k every_script --basetemp=<dir>`. Only then set `tested: true` for a pack.
- [ ] **Confirm labels and pack rules** (figure/table/references words and per-pack rules were written by the builder).
- [x] **Long table cells:** wrapping `p{}` columns when a table would shrink (slice 1b spec §15 item 10); `vi` golden unchanged.
- [x] **SG amended** with the slice 1b details (end of SG §11.5).

## Needs a real environment
- [x] **Real agent run**: done 2026-11-18 with MinerU 4.2.3 (it is installed in `~/.rosettex/mineru-venv`): 2611.23202 → Vietnamese and Arabic by the builder session, → the 20 other packs by Haiku subagents (see the acceptance record). "Klingon" (unknown language) was not run.
- [ ] **Tune thresholds from real translations:** `min_target_ratio` per pack, `max_length_ratio`, Thai/Myanmar U+200B run limit (25) and 40-letter rule, per-profile `scale` (Telugu 0.75, Kannada 0.75, Ethiopic 0.8, Bengali 0.9, Gujarati 0.9).
- [x] XeLaTeX (TeX Live 2026, Linux): the render tests pass (spec §15 item 23). [x] CI (GitHub Actions, Tectonic, fake MinerU): all 6 jobs (Ubuntu, macOS, Windows × Python 3.11, 3.12) pass on `main` (run 44317746745, 2026-11-18). [ ] A real translation with MinerU on macOS and Windows, and XeLaTeX there: for the user to test.
- [x] **Licences of the 3 bundle fonts** re-checked on CTAN 2026-11-18 (`tex-gyre`: gfl, `lm-math`: gfl, `noto`: ofl + lppl): the strings in the profiles are correct.

## Venue style (approach B, started 2026-11-18)
- [x] IEEE papers build with `IEEEtran` (2611.23202 → Vietnamese, 14 pages, `postcheck` clean).
- [x] ACM, Springer LNCS, Elsevier CAS venue styles: one page of a real arXiv paper of each translated to Vietnamese (spec §15 item 21). Open: Elsevier `elsarticle`, ACM single-column, a full-length paper of each.
- [x] IEEE authors side by side, italic affiliations (2026-11-18). Bold run-in heads inside paragraphs: not wanted (decided by the user).
- [ ] Reader check of the IEEE build against the source.

## 20-language run (2026-11-18)
- [x] 2611.23202 translated into the 20 other packs by Haiku subagents, PDFs in `~/Desktop/Learn/rosettex-demos/`; RTL glyphs, Thai/Myanmar overflow, decimal commas and split paragraphs fixed (spec §15 items 16–19).
- [x] Content length ratio: a warning, not a failure (SG decision 5 amended 2026-11-18; Tamil 1.7, Myanmar 2.3 ceilings).
- [ ] Native readers for all 20 (Haiku translations, unreviewed).

## Known and left on purpose
- Hyphenation is lost for packs without polyglossia (`zh-*`, `ja`, `ko`, `gu`, `my`).
- Arabic punctuation is mirrored by hand (spec §15 item 11); check on real texts with nested brackets and quotes.
- Thai headings look loose (stretch of `\hspace{0pt plus 1pt}`).
- CJK `scale` and the `\LARGE` title are not tuned.
- `scripts/build_fonts.py` writes the manifest without a temp file and calls `subprocess` (dev only); `fonts.py` rehashes fonts on every resolve (milliseconds).
- Latin-script languages without a pack only get the foreign-letter check (no untranslated detector beyond exact copy).
- Wrapped tables: the width estimate is heuristic (0.5 em per non-CJK character); read real tables in the reader check.
- Test `test_render_short_in_every_script` skips RTL text checks (Arabic extraction is unreliable).

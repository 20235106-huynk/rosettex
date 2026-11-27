Translation loop in {work}:
1. Read sheets ({sheets}) in {work}/read/.
2. Fill {work}/terms.md: set `field:` first (from title + abstract), fill key Terms, and add any figurative/field-specific expressions you meet to Expressions.
3. Append answers (`sNNNN <translation>`) to {work}/out/NN.txt.
4. Run `rosettex check {work}` until ok, then `rosettex build {work} --out <file.pdf>`.

Translation rules:
- Placeholders (`⟦mN⟧`, `⟦rN⟧`, `⟦cN⟧`, `⟦sN⟧`, `⟦lN⟧`): keep each once; move freely. Keep `⟦bN⟧…⟦/bN⟧` ordered, nested, non-empty. Never copy `  ~ …` legends.
- `sNNNN = sMMMM` lines are auto-filled; do NOT answer them.
- Corrections: append a new `sNNNN <translation>` line to `out/*.txt` (later line wins; never rewrite whole files).
- Numbers/dates: keep exact values in the {lang} locale (e.g. decimal-comma locales like vi: `0,5`, `1.000`, lists `(1; 2)`). Dates like `December 2018` allow numeric months (`tháng 12 năm 2018`).
- Field & meaning: decide `field:` first. Translate by meaning in that field, never word for word. Render idioms, metaphors and figurative jargon with an equivalent expression in {lang} (e.g. "the workhorse of NLP" means the dominant/main model family, not a literal draft animal); prefer the field's established terms in {lang} over calques.
- Terms & Expressions: in `terms.md`, `=` keeps a term unchanged, empty rows are ignored, and `check` enforces every filled row across both Terms and Expressions.
- PDF route (when `route: "pdf"`):
  - Formula without a placeholder: copy it verbatim (rosettex wraps it automatically).
  - Flagged placeholder `⟦mN?⟧`: drop `⟦mN?⟧` and translate it only when its preview in the `~` legend is clearly an ordinary word of the sentence; when unsure (e.g. `ETmiss`, `mmin`), keep `⟦mN⟧`.
  - Budget line (`~ p.N · ≤ M chars`): character budget for the box on page N; stay within it when possible (`check` warns if >25% over).
  - Repair diagnosis (`state: "needs_repair"`): lists overflowing segments with target character counts; shorten those translations (keep placeholders), append corrected `sNNNN <translation>` lines to `out/*.txt`, and re-run `build`.
  - `direction_change` error: the PDF route cannot change writing direction (LTR↔RTL); tell the user to translate from the paper's TeX source instead.

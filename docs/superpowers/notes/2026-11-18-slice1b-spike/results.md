# Slice 1b spike results

## Verified facts that change the design

1. **Tectonic loads fonts only from its bundle or from the working directory.** An absolute path fails with
   `Package fontspec Error: The font "…" cannot be found`. A font outside the bundle must be copied next to the
   `.tex` file before the build. (Checked with Noto Sans Bengali, Telugu, Kannada, Ethiopic, Gujarati, Arabic, Thai.)
2. **The bundle has no complete font for most scripts.** The bundle index lists Noto Sans and Noto Serif (Latin,
   Cyrillic, Greek only). It has no Noto Indic, Thai, Ethiopic, Myanmar or CJK fonts. Available bundle fonts:
   Amiri (Arabic), Garuda (Thai), Mukta (Devanagari), HindMadurai (Tamil), Baloo2 (Indic, incomplete), Padauk (Myanmar),
   HaranoAjiMincho/HaranoAjiGothic (Japanese glyph set, with Han and Kana), UnBatang (Hangul).
3. **Correction (added after review):** the bundle has FandolSong-Regular.otf and gbsn00lp.ttf, which compile Simplified Chinese with 0 missing glyphs. The first version of this note wrongly said there was no static Simplified font. Also, Tectonic reads a relative subfolder path (`Path=sub/`).
4. **Script fonts lack ASCII digits and punctuation** (Noto Sans Bengali, Thai, Ethiopic: the missing characters are
   `0`, `5`, `.`, `,`). Wrapping the Latin runs in a Latin font fixes this (see the snippet below). Noto Sans Telugu and
   Kannada already contain them.
5. **`\begin{arabic}` and `\begin{amharic}` fail** with `Missing number, treated as zero` (the environment name collides with
   the LaTeX counter macro). Use `\textarabic{…}` and `\textamharic{…}` instead.
6. **polyglossia needs a macro per script** named `\<language>font` (`\bengalifont`, `\telugufont`, `\thaifont`, …), or it
   stops with `The current latin font … does not contain the "<Script>" script`.

## Results per script (Tectonic 0.17.0, Linux)

| # | Script | Sample language | Font in the bundle (missing chars) | Font copied from the system (missing chars) | Setup that compiled | Status |
|---|---|---|---|---|---|---|
| 1 | Latin | vi | NotoSerif-Regular.ttf (0) | — | `polyglossia` vietnamese | OK |
| 2 | Cyrillic | ru | NotoSerif-Regular.ttf (0) | — | `polyglossia` russian | OK |
| 3 | Han | zh-Hans | FandolSong-Regular.otf (0). HaranoAjiMincho (2 missing: 这, 简) is the Japanese glyph set and is not enough | — (NotoSansCJK-VF.ttc fails in xdvipdfmx: `sfnt: table not found`) | xeCJK | OK with FandolSong (checked after the first spike; also bundle gbsn00lp.ttf) |
| 4 | Kana | ja | HaranoAjiMincho-Regular.otf (0) | — | xeCJK | OK |
| 5 | Hangul | ko | UnBatang.ttf (0) | — | xeCJK | OK (license to check) |
| 6 | Arabic | ar | Amiri-Regular.ttf (0) | NotoSansArabic (0) | `\textarabic` | OK (bidi, two columns not tested) |
| 7 | Devanagari | hi | Mukta-Regular.ttf (0) | — | `polyglossia` hindi | OK |
| 8 | Bengali | bn | Baloo2-Regular.ttf (31: incomplete) | NotoSansBengali (5: digits, punctuation; 0 with `\latin` wrap) | `polyglossia` bengali | OK only with copied font and wrap |
| 9 | Telugu | te | Baloo2-Regular.ttf (28: incomplete) | NotoSansTelugu (0) | `polyglossia` telugu | OK with copied font |
| 10 | Tamil | ta | HindMadurai-Regular.otf (0) | — | `polyglossia` tamil | OK |
| 11 | Gujarati | gu | Baloo2 (not reached) | NotoSansGujarati (not reached) | `\setotherlanguage{gujarati}` fails: `polyglossia/gujarati/hyphenmins requires a value`, also with `[hyphenmins={2,2}]` | **Open**: polyglossia setup fails, babel `[gujarati]` unknown option |
| 12 | Kannada | kn | Baloo2 (34: incomplete) | NotoSansKannada (0) | `polyglossia` kannada | OK with copied font |
| 13 | Thai | th | Garuda.otf (0) | NotoSansThai (4 digits; 0 with `\latin` wrap) | `polyglossia` thai | OK (line breaking not checked) |
| 14 | Ethiopic | am | none in the bundle | NotoSansEthiopic (4 digits; 0 with `\latin` wrap) | `\textamharic` | OK with copied font and wrap |
| 15 | Myanmar | my | Padauk-Regular.ttf (0) | — | fontspec `Script=Myanmar` (no polyglossia language tested) | OK (line breaking not checked) |

Missing-glyph count = number of `Missing character` lines in the TeX log. A count of 0 does not prove correct shaping.

## Working snippets

Latin fallback for a script font (the Latin runs use the Latin main font):

```latex
\setmainfont{NotoSerif-Regular.ttf}
\newcommand\latin[1]{{\fontspec{NotoSerif-Regular.ttf}#1}}
\usepackage{polyglossia}\setdefaultlanguage{english}
\newfontfamily\bengalifont[Script=Bengali]{NotoSansBengali.ttf}   % file copied next to the .tex
\setotherlanguage{bengali}
\textbengali{এটি বাংলায় সংখ্যা \latin{0.05}।}
```

Arabic (the bundle font):

```latex
\usepackage{polyglossia}\setdefaultlanguage{english}
\newfontfamily\arabicfont[Script=Arabic]{Amiri-Regular.ttf}
\setotherlanguage{arabic}
\textarabic{هذا مثال على نص عربي مع الرقم 0.05}
```

## Not verified in this spike

- XeLaTeX, macOS and Windows.
- Visual correctness of shaping (conjuncts, reordering), right-to-left in two-column layout, the line breaking of Han,
  Kana, Thai and Myanmar, and the Kana line-start/line-end rules.
- Font licenses: Noto, Amiri, Mukta, HindMadurai, Baloo2, Padauk and Harano Aji are SIL OFL in their upstream projects; the license of
  Garuda and UnBatang was not checked.
- The Gujarati polyglossia error (cause unknown).

## Follow-up (2026-11-18, after review)

- **Dictionary line breaking does not work in Tectonic 0.17.0.** `\XeTeXlinebreaklocale "th"` (and `"my"`) stops XeTeX with
  `failed to create linebreak iterator, status=2`: the engine has no ICU break data. Without break points a Thai or
  Myanmar paragraph is one overfull line (~1880 pt). U+200B between words alone changes nothing. Replacing each U+200B by
  `\hspace{0pt plus 1pt}` breaks lines correctly (largest overfull 7.6 pt for Thai, 0.6 pt for Myanmar). So the translator
  must put U+200B between words, and the renderer turns it into a break point.
- **Packaged fonts now come from the official Noto releases**, not from this machine: Noto Serif Bengali, Telugu,
  Gujarati, Kannada, Ethiopic, Devanagari, Tamil, Thai, Myanmar (static `unhinted/ttf/*-Regular.ttf` from
  `github.com/notofonts/<script>` releases), subset to the script blocks: 1.16 MB for the nine. All nine compile in the
  `runs` layout (Termes main font, script runs in the script font loaded with `Path=fonts/`) with 0 missing glyphs;
  Thai and Myanmar only without `\XeTeXlinebreaklocale` (see above). Release URLs and checksums: `rosettex/fonts/MANIFEST.json`.
- Gujarati compiles without polyglossia (plain fontspec family) with the packaged font.

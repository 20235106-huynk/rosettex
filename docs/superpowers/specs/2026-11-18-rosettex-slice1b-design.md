# Slice 1b spec: `rosettex` — script coverage (15 scripts)

Date: 2026-11-18 (rewritten after review) · Status: **decisions settled with the user; ready for the plan** · Base spec: `2026-11-18-rosettex-design.md` (**SG**) · Previous slice: `2026-11-18-rosettex-slice1-design.md` (implemented) · Spike: `docs/superpowers/notes/2026-11-18-slice1b-spike/` (`results.md`)

> This slice implements SG §11.5 and decision 24. Where this spec is silent, the SG and the slice 1 spec decide. It changes SG §11.5 rule 1 (fonts are packaged; amended in SG on 2026-11-18) and corrects a fact in the same rule (TeX Gyre Termes has no Cyrillic). No decision of SG §19 is changed. §19 lists the decisions of this slice, all confirmed by the user.

## 1. Goal of the slice

An agent in any harness translates a text-layer paper into a target language written in any of the 15 scripts of SG §11.5 (Latin, Han, Arabic, Devanagari, Bengali, Cyrillic, Kana, Telugu, Hangul, Tamil, Thai, Gujarati, Kannada, Ethiopic, Myanmar). The user names the language; `rosettex` maps it to its script (§5.3) and builds a PDF where the text is in a suitable font, shaped by the TeX engine, in the right direction, with working line breaks, while formulas, numbers, citations, references and URLs stay intact. The result is the same on every machine: every non-Latin font ships inside `rosettex` (§6).

**Success when:**
1. Each of the 21 target packs of §5.4 builds `short.pdf` through `render` with a fake translator that writes real text of its script (§17), with a clean post-build check (no missing glyph, no dotted circle, no undefined reference), with Tectonic on the dev machine, **without any font file outside the package and the Tectonic bundle**.
2. `accept` rejects the unchanged English source for every pack and accepts a correct sample translation for every pack.
3. `vi` behaves exactly as in slice 1: all slice 1 tests pass unchanged and `main.tex` for the `short` fixture is byte-identical to the slice 1 output (golden file, §14).
4. A real agent translates `short.pdf` into `ar`, `zh-Hans` (or `ja`) and `hi` from a single sentence of the user that names the language in words ("translate into Arabic"), using only `rosettex guide`; it reports the script it uses.
5. Every new pack ships `tested: false`; sample PDFs of all packs are handed to the user for a reader check (§12).

## 2. Scope

**In slice 1b:**
- Script profiles (§4), the generalised language pack (§5), the language→script table `languages.json` (§5.3).
- 21 target packs (§5.4) plus `default`.
- Packaged fonts (14 files, §6.1), their verification, staging into the build directory, licence records; a reproducible font build script.
- Four build layouts `plain`, `cjk`, `runs`, `rtl` (§7), right-to-left output (§8), line breaking (§9).
- Per-script checks: untranslated detector, alphabet, non-ASCII digits, word separators for Thai and Myanmar (§10).
- Post-build check: missing glyphs, broken shaping, overfull lines (§11).
- Protocol updates and regenerated `SKILL.md`/`AGENTS.md` (§13).

**Outside slice 1b** (must not be built ahead of time):
- Downloading anything at run time; `setup`, `doctor` (slice 3). A user font directory: not needed, since every font ships (decision 9).
- `review` (slice 2), OCR (slice 4), bilingual HTML (phase 2).
- Localised digits (decision 15: never).
- Vertical CJK, ruby, Zawgyi conversion, Nastaliq style for Urdu (Naskh is used).
- Scripts outside the 15 (Greek, Hebrew, Sinhala, Khmer, …): §15.
- Bold, italic and bold-italic of the packaged fonts (only Regular ships; §6.1).
- Making a pack `tested: true` without a human reader.

**Prerequisites:** as slice 1 (MinerU, Tectonic or XeLaTeX). No new user action.

## 3. Facts this spec builds on

All from `results.md` (Tectonic 0.17.0, Linux, 2026-11-18) unless stated.

### 3.1 Verified
- Tectonic loads fonts only from its bundle or **relative** to the working directory; absolute paths fail. A relative subdirectory works: `Path=fonts/` with the font in `out/fonts/` (TeX runs with `cwd = work/out`, `compile.py`).
- Script fonts lack ASCII digits and punctuation in some cases; the `runs` layout (Latin main font, script runs in the script font) avoids the problem by construction (§7.2).
- polyglossia needs `\<language>font` per language; `\begin{arabic}`/`\begin{amharic}` fail, `\text<language>{…}` works.
- polyglossia's Gujarati fails in the bundle (`hyphenmins` key); Gujarati compiles without polyglossia (plain fontspec family).
- **TeX Gyre Termes has no Cyrillic** (9 missing characters on one Russian sentence). Noto Serif (bundle) covers Latin, Cyrillic and Greek.
- **`\XeTeXlinebreaklocale` crashes Tectonic's XeTeX** (`failed to create linebreak iterator, status=2`): there is no ICU dictionary line breaking. A U+200B between words does nothing by itself; replacing it with `\hspace{0pt plus 1pt}` breaks Thai and Myanmar lines correctly (largest overfull 7.6 pt and 0.6 pt).
- **Arabic in two columns** with `\setdefaultlanguage{arabic}` (polyglossia, bidi), `hyperref` loaded before polyglossia: compiles; the first column is on the right; a `tabular` inside the RTL flow is mirrored by bidi (first column on the right); numbers and math stay LTR.
- polyglossia's Arabic prints **LaTeX-generated numbers (section, table, page) in Arabic-Indic digits** by default; `\setdefaultlanguage[numerals=maghrib]{arabic}` prints ASCII digits.
- A heading whose translated text starts with a number shows the number after the title unless the number is wrapped LTR: `\rxltr{1}\quad المقدمة` puts it first (on the right).
- **Arabic text extracted from the PDF is unreliable** (xdvipdfmx maps contextual glyph forms poorly; Amiri worse than Noto Naskh Arabic). Copy-paste from the PDF is imperfect for Arabic; checks must not depend on Arabic text extracted from the PDF.
- All 14 packaged fonts compile their sample text with 0 missing glyphs in their layout (§6.1).

### 3.2 Still open (closed by plan Task 2, with stop rules)
- A **whole document** per layout: headings, captions, `\cite`/`\ref`/`\url`, footnotes, tables with `\multirow`, two columns, `hyperref`, bibliography; Persian and Urdu (`numerals` option of `persian`/`urdu` in polyglossia); Korean spaces; Kana line-start/line-end rules.
- XeLaTeX (not installed on the dev machine), macOS, Windows.
- Licences of the three bundle fonts that remain in use (`texgyretermes`, Noto Serif, Latin Modern Math).

## 4. Script profiles

A **script profile** holds what depends on the script. One JSON file per profile: `rosettex/langpacks/scripts/<id>.json`. Adding a script needs no code as long as it fits one of the four layouts.

### 4.1 Format
```json
{
 "id": "bengali",
 "layout": "runs",
 "direction": "ltr",
 "otf_script": "Bengali",
 "blocks": ["0980-09FF", "0964-0965", "200C-200D"],
 "linebreak": "space",
 "layout_options": "",
 "polyglossia_default": null,
 "min_target_ratio": 0.3,
 "fonts": [{"file": "NotoSerifBengali-Regular.ttf", "source": "package", "license": "OFL-1.1"}]
}
```

| Key | Meaning |
|---|---|
| `layout` | `plain`, `cjk`, `runs`, `rtl` (§7) |
| `direction` | `ltr` or `rtl`; `rtl` ⇔ layout `rtl` |
| `otf_script` | fontspec `Script=` value (turns on HarfBuzz shaping for the script); `null` for Latin, Cyrillic and the CJK profiles (xeCJK sets up CJK fonts itself) |
| `blocks` | Unicode ranges (hex, inclusive) of the script, including the joiners and shared marks it needs. Used by the run splitter (§7.3), the detector (§10.1) and the alphabet check (§10.2). Empty for Latin |
| `linebreak` | `space`, `cjk` (xeCJK) or `zwsp` (U+200B word separators turned into break points, §9) |
| `layout_options` | options for the layout package (`CJKspace=true` for Hangul) |
| `polyglossia_default` | the polyglossia language used when a language of this script has no pack (§5.3). Required for `rtl` (`arabic`); `null` elsewhere |
| `min_target_ratio` | the detector threshold used when a language of this script has no pack |
| `fonts` | font candidates in order; each: `file` (file name only), `source` (`bundle` or `package`), `license`, optional `options` (raw fontspec options) |

### 4.2 The profiles

| Profile | Layout | Line break | Blocks | Font (source) | Packs |
|---|---|---|---|---|---|
| `latin` | plain | space | none | `texgyretermes` (bundle) | `vi`, `fr` |
| `cyrillic` | plain | space | 0400–052F | `NotoSerif-Regular.ttf` (bundle; Bold, Italic, BoldItalic via options) | `ru`, `uk` |
| `han` | cjk | cjk | 2E80–2FDF, 3000–303F, 3400–4DBF, 4E00–9FFF, F900–FAFF, FF00–FFEF, 20000–3134F | `NotoSerifSC-Regular.otf` (package); pack `zh-Hant` overrides with `NotoSerifTC-Regular.otf` | `zh-Hans`, `zh-Hant` |
| `kana` | cjk | cjk | han blocks + 3040–30FF, 31F0–31FF | `NotoSerifJP-Regular.otf` (package) | `ja` |
| `hangul` | cjk (`CJKspace=true`) | cjk | 1100–11FF, 3130–318F, A960–A97F, AC00–D7AF, D7B0–D7FF + han blocks | `NotoSerifKR-Regular.otf` (package) | `ko` |
| `arabic` | rtl | space | 0600–06FF, 0750–077F, 08A0–08FF, FB50–FDFF, FE70–FEFF, 200C–200F | `NotoNaskhArabic-Regular.ttf` (package) | `ar`, `fa`, `ur` |
| `devanagari` | runs | space | 0900–097F, A8E0–A8FF, 200C–200D | `NotoSerifDevanagari-Regular.ttf` (package) | `hi`, `mr` |
| `bengali` | runs | space | 0980–09FF, 0964–0965, 200C–200D | `NotoSerifBengali-Regular.ttf` (package) | `bn` |
| `telugu` | runs | space | 0C00–0C7F, 0964–0965, 200C–200D | `NotoSerifTelugu-Regular.ttf` (package) | `te` |
| `tamil` | runs | space | 0B80–0BFF, 200C–200D | `NotoSerifTamil-Regular.ttf` (package) | `ta` |
| `gujarati` | runs | space | 0A80–0AFF, 0964–0965, 200C–200D | `NotoSerifGujarati-Regular.ttf` (package) | `gu` (no polyglossia) |
| `kannada` | runs | space | 0C80–0CFF, 0964–0965, 200C–200D | `NotoSerifKannada-Regular.ttf` (package) | `kn` |
| `thai` | runs | zwsp | 0E00–0E7F, 200B | `NotoSerifThai-Regular.ttf` (package) | `th` |
| `ethiopic` | runs | space | 1200–139F, 2D80–2DDF, AB00–AB2F | `NotoSerifEthiopic-Regular.ttf` (package) | `am` |
| `myanmar` | runs | zwsp | 1000–109F, A9E0–A9FF, AA60–AA7F, 200B | `NotoSerifMyanmar-Regular.ttf` (package) | `my` (no polyglossia) |

Han, Kana and Hangul are three profiles because their fonts and detector sets differ; the two Han packs share one profile and differ by font.

### 4.3 Polyglossia is a language property, optional
The polyglossia language name lives in the pack (`polyglossia`), because languages of one script differ (`arabic`/`persian`/`urdu`). `null` means no polyglossia for the language: the script font is used through a plain fontspec family (§7.1). This is the setup for `gu` (polyglossia fails), `my` (no polyglossia language verified) and all CJK packs (xeCJK). Lost without polyglossia: hyphenation patterns, which Indic and Myanmar text in papers rarely needs.

## 5. Language packs

### 5.1 Format (refines slice 1 §8 and SG §11.4)
A pack keeps the keys of slice 1, gains `script`, and loses `font`.

| Key | Change |
|---|---|
| `script` | **new**, required: the profile id |
| `font` | **removed** from `vi.json` and `default.json`: the font comes from the profile; a pack may override with its own `fonts` list (`zh-Hant`) |
| `polyglossia` | may be `null` (§4.3) |
| `polyglossia_options` | **new, optional**: options of `\setdefaultlanguage[…]` for `rtl` packs, so that LaTeX-generated numbers use ASCII digits (`numerals=maghrib` for `ar`; the values for `fa`, `ur` come from plan Task 2) |
| `min_diacritic_ratio` | **renamed** `min_target_ratio` (same value for `vi`) |
| `function_words` | may be empty (then only the target ratio decides) |
| `labels` | gains optional `figure`, `table`, `references` → `\figurename`, `\tablename`, `\refname` (§7.4). `fallback` and `missing_image` stay English in non-Latin packs (printed in the monospaced fallback box) |
| `max_length_ratio` | **new, optional** (default 1.3, SG decision 5): post-build length threshold |

### 5.2 Loading (`load_pack(code)`)
1. Read `default.json`.
2. Resolve the code as in §5.3 to (pack file or none, profile id).
3. With a pack file: start from `default.json` **without its `fonts`**, update with the pack file (shallow). Without one: start from `default.json` with `code` set to the requested code; for a mapped language (§5.3 step 2) also set `script`, `polyglossia` (`languages.json` entry, else the profile's `polyglossia_default`), `min_target_ratio` (profile) and drop the default's `fonts`.
4. Attach the profile as `pack["profile"]` (blocks parsed once); `pack["fonts"]` = the pack's own `fonts`, else the profile's. Set `pack_found`.

### 5.3 How a language picks its script (decided 2026-11-18)
The user names a language in words; the agent turns it into a BCP-47 code (protocol, §13); `rosettex` maps the code to a script. The mapping is data, not the agent's memory, so every harness gets the same answer.
- `rosettex/langpacks/languages.json`: `{"<code>": {"name": "<English name>", "script": "<profile id>", "polyglossia": "<name>"|null}}`. It covers every shipped pack and the other common languages of the 15 scripts (for example `bg`, `sr`, `kk` → `cyrillic`; `ps`, `ckb`, `ug` → `arabic`; `ne` → `devanagari`; `as` → `bengali`; `ti` → `ethiopic`; `es`, `de`, `id`, `tr`, `pl`, `pt` → `latin`).
- An entry may be an **alias** instead: `{"name": …, "alias": "<code>"}` resolves as the target code while keeping the requested code (`zh`, `zh-CN` → `zh-Hans`; `zh-TW`, `zh-HK` → `zh-Hant`; `sr` → `sr-Cyrl`).
- Languages written in more than one script use a code with a script subtag, each listed: `sr-Cyrl`/`sr-Latn`; the bare code is an alias to the usual script. The code regex of slice 1 (`[a-z]{2,3}(-[A-Za-z]{2,8})?`) already accepts these codes.
- Languages without a pack get `"polyglossia": null` in `languages.json` unless the name was verified (an unknown polyglossia name stops the build); they lose hyphenation only. `rtl` languages fall back to the profile's `polyglossia_default`.
- Resolution: (1) a pack file `<code>.json` exists → it is used; a test checks its `script` equals the `languages.json` entry. (2) no pack file but a `languages.json` entry → the default pack with that script's profile and font (`pack_found: false`). (3) unknown code → the default pack as in slice 1 (`script: "latin"`, `pack_found: false`, `script_known: false`).
- `extract` and `status` return `script`, `pack_found`, `script_known` and `fonts` (file names). The agent tells the user the language, code and script ("Persian → `fa` → Arabic script") before translating; it asks the user first when `script_known` is false.

### 5.4 Packs shipped
`vi` (unchanged behaviour, `tested: true`), `default`, and 20 new packs, all `tested: false`:

| Script | Packs (polyglossia) |
|---|---|
| Latin | `fr` (`french`) |
| Cyrillic | `ru` (`russian`), `uk` (`ukrainian`) |
| Han | `zh-Hans`, `zh-Hant` (none) |
| Kana | `ja` (none) |
| Hangul | `ko` (none) |
| Arabic | `ar` (`arabic`), `fa` (`persian`), `ur` (`urdu`) |
| Devanagari | `hi` (`hindi`), `mr` (`marathi`) |
| Bengali, Telugu, Tamil, Kannada, Thai, Ethiopic | `bn` (`bengali`), `te` (`telugu`), `ta` (`tamil`), `kn` (`kannada`), `th` (`thai`), `am` (`amharic`) |
| Gujarati, Myanmar | `gu`, `my` (none) |

`default.json` keeps `script: "latin"` and gets `fonts` = Noto Serif (bundle) so that an unknown code renders Latin, Cyrillic and Greek without missing glyphs (SG §11.4). These `fonts` apply only to the default pack itself (§5.2).

Per pack: labels (`figure`, `table`, `references`) for non-`plain` layouts, the detector data of §10.1, and rules. Every new pack's rules add: "Write every number with the ASCII digits 0–9 exactly as in the source." `th` and `my` add: "Put U+200B (zero-width space) between words; never put an ordinary space inside a sentence unless the language uses one." `my` adds "Write Myanmar in Unicode, not Zawgyi." `zh-*` and `ja` add "Do not put spaces between CJK characters." Labels and rules are written by the builder and confirmed by the reader (§12).

## 6. Fonts

### 6.1 Sources
1. **Bundle** (`source: "bundle"`): the TeX distribution's fonts, loaded by file name. Only three remain: `texgyretermes` (Latin, decision 16), `NotoSerif-*.ttf` (Cyrillic and `default`; four styles needed for a main font), `latinmodern-math.otf` (math, slice 1). Tectonic fetches them from its bundle (same on every machine); TeX Live has them in the `tex-gyre`, `noto` and `lm-math` packages.
2. **Package** (`source: "package"`): 14 files in `rosettex/fonts/`, shipped in the wheel, built from the official releases (`notofonts/<script>`, `notofonts/noto-cjk` Serif2.003, `notofonts/arabic`), subset, with `MANIFEST.json` (file, sha256, version, source URL, source zip sha256, file inside the zip, what was changed) and one OFL text per family in `rosettex/fonts/LICENSES/`. Total 16.5 MB.

| File | Script | Characters kept (plus ASCII, Latin-1, general punctuation, U+25CC) | Size |
|---|---|---|---|
| `NotoSerifSC-Regular.otf` | Han (Simplified) | Table of General Standard Chinese Characters, 8105 (Unihan `kTGHZ2013`) ∪ GB 2312 | 2.9 MB |
| `NotoSerifTC-Regular.otf` | Han (Traditional) | Big5 (Unihan `kBigFive` ∪ codec `big5`) | 4.8 MB |
| `NotoSerifJP-Regular.otf` | Kana + kanji | JIS X 0208 ∪ Jōyō ∪ Jinmeiyō kanji ∪ kana blocks | 2.6 MB |
| `NotoSerifKR-Regular.otf` | Hangul | all 11 172 syllables, jamo blocks, KS X 1001 | 4.9 MB |
| `NotoNaskhArabic-Regular.ttf` | Arabic (ar, fa, ur) | Arabic blocks | 0.13 MB |
| `NotoSerif{Devanagari,Bengali,Telugu,Tamil,Gujarati,Kannada,Thai,Ethiopic,Myanmar}-Regular.ttf` | the nine scripts | the script's blocks | 1.16 MB together |

Only Regular ships (decision 12): headings in these scripts are set in Regular, which XeTeX reports as a font-shape warning, not an error. CJK compatibility ideographs are not needed: `accept` normalises to NFC, which maps them to unified ideographs.

The font files are produced by `scripts/build_fonts.py` (dev-only; uses fontTools through `uv run --with fonttools`, never a runtime dependency): it downloads each release zip, checks its sha256 against `MANIFEST.json`, extracts, subsets and writes the manifest. Running it again reproduces the files byte for byte or reports the difference.

### 6.2 Resolution (`fonts.resolve_fonts(pack)`)
- `latin` = the first font of the `latin` profile (`texgyretermes`).
- `script` = the first candidate of `pack["fonts"]`. A `bundle` candidate is accepted (it can only be proven by compiling, §6.4). A `package` candidate is accepted when the file exists in `rosettex/fonts/` and its sha256 matches `MANIFEST.json`; otherwise `FontMissing` (a broken installation).
- One candidate per profile is the normal case; the list form remains so a later slice can add alternatives.

### 6.3 Staging
Tectonic cannot read absolute paths. `render` copies each packaged font it uses into `work/out/fonts/` before compiling (skip when the size and sha256 already match) and loads it with `Path=fonts/`. Bundle fonts are not copied. XeLaTeX uses the same staging. `out/` is in `OWNED`, so `clean` removes the copies.

### 6.4 Font errors
- **Before long work:** `extract` resolves the fonts of the requested pack before calling MinerU; `FontMissing` → exit 2 with `{"ok": false, "error": "<message: reinstall rosettex>", "font_missing": true, "script": …, "fonts_needed": [{"file", "license"}], "next_action": null}`.
- **At build time:** `render` resolves again; a compile that fails with fontspec's `The font "<file>" cannot be found` (a bundle font missing from a TeX installation, or a network failure while Tectonic fetches it) → `font_missing` with that file, instead of the last-resort fallback (the failure is in the preamble, before any `RX:` marker).

### 6.5 Licences
All packaged fonts are SIL OFL 1.1; none declares a Reserved Font Name, so subset files keep their names. `report.json` gains `fonts: [{file, source, license}]`. Task 2 records the licences of the three bundle fonts.

## 7. Building

### 7.1 Preamble per layout
Shared with slice 1 and unchanged: class, `geometry`, `fontspec`, `amsmath,amssymb`, `unicode-math` + `latinmodern-math.otf`, `graphicx,booktabs,multirow,adjustbox,url`, `hyperref`, `\rxlabel`, `\emergencystretch`. Slice 1 loads polyglossia **before** `hyperref`; that order stays for `plain`, `cjk` and `runs` (so `vi` stays byte-identical). For `rtl` the language lines move **after** `hyperref`, because bidi (loaded by polyglossia for Arabic) must come after it (the order verified in §3.1).

| Layout | Main font | Language and script setup | Body wrapping |
|---|---|---|---|
| `plain` | the profile font (Termes for `vi`/`fr`, Noto Serif for `ru`/`uk`/`default`) | `\usepackage{polyglossia}\setdefaultlanguage{<polyglossia>}` (slice 1) | none |
| `cjk` | Termes | `\usepackage[<layout_options>]{xeCJK}`, `\setCJKmainfont{<file>}[Path=fonts/]` | none (xeCJK switches per character) |
| `runs` | Termes | with polyglossia: `\setdefaultlanguage{english}`, `\newfontfamily\<lang>font{<file>}[Script=…,Path=fonts/]`, `\setotherlanguage{<lang>}`, `\newcommand\rxs[1]{\text<lang>{#1}}`; without: `\newfontfamily\rxscriptfont{…}`, `\newcommand\rxs[1]{{\rxscriptfont #1}}` | target-script runs → `\rxs{…}` |
| `rtl` | Termes (LTR runs) | after `hyperref`: `\usepackage{polyglossia}`, `\setdefaultlanguage[<polyglossia_options>]{<lang>}`, `\setotherlanguage{english}`, `\newfontfamily\<lang>font{<file>}[Script=Arabic,Path=fonts/]`, `\newcommand\rxltr[1]{\textenglish{#1}}` | Latin/digit runs and non-math placeholders → `\rxltr{…}` |

### 7.2 Latin text, digits and generated numbers
Digits, ASCII punctuation, Latin words and everything LaTeX generates (section, figure, table, footnote, citation and page numbers) are set in the Latin main font in `cjk` and `runs` (xeCJK and `\rxs` only touch the target script), in the main font in `plain`, and LTR in ASCII digits in `rtl` (`\rxltr`, `numerals=maghrib`). Math always uses Latin Modern Math and stays LTR.

### 7.3 Run splitting (`tex.wrap_runs(s, profile)`)
Applied to each translated text segment between placeholders (`Renderer.text`), and to labels.
- `runs`: a run is a maximal sequence of characters in `blocks`, where spaces/tabs between two such characters belong to it; newlines never do. Each run → `\rxs{<escaped run>}`. Digits and ASCII punctuation are outside `blocks`, so they never enter `\rxs`.
- `rtl`: a run is a maximal sequence that starts and ends with a Latin letter (ASCII or U+00C0–024F) or ASCII digit, contains no character of `blocks`, no newline, and only letters, digits, spaces and `. , : / - _` inside; an optional trailing `%` belongs to it. Each run → `\rxltr{…}`. Parentheses and other neutrals stay in the RTL flow so they mirror.
- `zwsp` line breaking (`thai`, `myanmar`): inside a run, U+200B → `\hspace{0pt plus 1pt}` (a break point, §9).
- `plain`, `cjk`: escaping only (slice 1).

Placeholders: in `rtl`, `\cite`, `\ref`, `\eqref`, `\url`, `\textsuperscript` outputs are wrapped in `\rxltr{…}`; math is not. Other layouts emit placeholders unchanged.

Blocks kept verbatim (authors, affiliations, references; English) are emitted unchanged in `plain`/`cjk`/`runs`; in `rtl` the bibliography sits in `\begin{otherlanguage}{english}…\end{otherlanguage}` with `\refname` set to the pack label inside it, so entries are LTR.

### 7.4 Labels
When the pack has `labels.figure|table|references`, the document writes after `\begin{document}`: `\renewcommand{\figurename}{…}`, `\renewcommand{\tablename}{…}`, `\renewcommand{\refname}{…}`, passed through `wrap_runs`. `vi` has none of these keys, so its output stays byte-identical.

### 7.5 Unchanged
`probe.tex` uses the same preamble (one column) and probes only formulas and tables. `\typeout{RX:<id>}` markers, the last-resort fallback, floats, `hyperref`: unchanged. `escape_latex` keeps sending Greek and math symbols to `\ensuremath`.

## 8. Right-to-left (Arabic profile; decided 2026-11-18)

The output is laid out right to left, as Arabic documents are: when Arabic is the main script, page and document structure run right to left (W3C Arabic & Persian Layout Requirements), and word processors mirror tables so the first column is on the right.
- Main language = target (`\setdefaultlanguage`): paragraphs, headings, captions and footnotes start on the right; two-column order is mirrored (first column on the right).
- **Tables are mirrored** (first column on the right): the `tabular` stays inside the RTL flow, which bidi mirrors (verified). Cell texts go through `wrap_runs` like other text, so numbers and Latin words in cells stay LTR.
- Stay LTR: formulas, numbers, Latin words, citations, references, URLs (§7.3). Generated numbers in ASCII digits (`numerals=maghrib`).
- Heading numbers: the number at the start of a translated heading is a Latin/digit run, so `wrap_runs` wraps it in `\rxltr` and it appears first (on the right).
- Stop rule (Task 2): if `twocolumn` cannot be made to work with the full slice 1 preamble, the renderer forces one column for `rtl` and the acceptance note says so.

## 9. Line breaking

| Scripts | Mechanism | State |
|---|---|---|
| Latin, Cyrillic, Arabic, Indic, Ethiopic, Hangul | spaces; TeX hyphenation where polyglossia has patterns | verified per script on one sentence; Hangul spaces need `CJKspace=true` (Task 2) |
| Han, Kana | xeCJK inter-character breaking with its line-start/line-end rules | Task 2 |
| Thai, Myanmar | the translator puts U+200B between words (pack rule, checked by `accept`, §10.4); `wrap_runs` turns each U+200B into `\hspace{0pt plus 1pt}` | verified (§3.1). ICU dictionary breaking is not available in Tectonic |

## 10. Translation checks (refines slice 1 §8)

### 10.1 Untranslated detector (SG §11.5 rule 5)
Unchanged entry conditions (skip below `min_len_check` letters after removing placeholders and `keep` terms; exact match → untranslated). Then:
- target set = pack `letters` ∪ profile `blocks`; target ratio = letters in the target set / all letters (`str.isalpha`);
- `function_words` empty → untranslated when target ratio < `min_target_ratio`;
- otherwise → untranslated when target ratio < `min_target_ratio` **and** function-word ratio < `min_function_ratio` (slice 1 rule).
- No target set (only the `default` pack for an unknown code) → exact match only (slice 1).

| Packs | Target set | Function words | `min_target_ratio` (initial) |
|---|---|---|---|
| `vi` | Vietnamese letters | yes | 0.03 (slice 1) |
| `fr` | French letters (é è ê ë à â ç î ï ô û ù ü ÿ œ æ, upper and lower) | yes (le, la, les, des, du, et, est, une, dans, pour, que, qui, sur, par, avec, nous, sont) | 0.01 |
| `ru`, `uk` | Cyrillic block | yes (ru: и, в, на, с, что, для, по, из, не, это, как, от, к, при; uk: і, в, на, з, що, для, не, це, як, від, до, при, та) | 0.3 |
| all other new packs, and mapped languages without a pack | profile blocks | none | 0.3 (profile) |

The Han ratio is used only to confirm the target is Han; it is never used to detect untranslated source text (SG §11.4). Thresholds are tuned in acceptance; a correct sample translation must pass in every pack (regression test).

### 10.2 Alphabet check
Allowed: ASCII, characters present in the source, Greek, the pack `letters`, the profile `blocks`. Any other letter is an error when the pack has a target set; `default` for an unknown code checks only control characters and U+FFFD (slice 1).

### 10.3 Digits (decision 15)
Every pack: a decimal digit outside ASCII (category `Nd`) not present in the source → error "use the ASCII digits 0-9 exactly as in the source".

### 10.4 Word separators (`zwsp` profiles)
For `th` and `my`: a run of 25 or more consecutive target-script characters without U+200B or a space → error "put U+200B between words". The threshold is tuned in acceptance.

## 11. Post-build check (SG §11.5 rule 6)

- **Missing glyphs:** each `Missing character: There is no <c> (U+XXXX) in font <name>!` → `missing_glyphs: [{char, codepoint, font}]`; one problem per font, `font <name> is missing characters: <chars>` (keeps slice 1's substring "is missing characters").
- **Broken shaping:** U+25CC in the PDF text and in no translation → problem "dotted circle in the PDF". Not applied to `rtl` packs (PDF text unreliable, §3.1); there the log check is the only glyph check.
- **Overfull lines:** for `cjk` and `zwsp` line breaking, more than 5 `Overfull \hbox` over 10 pt → problem "line breaking for this script may not work".
- **Length:** threshold `pack.get("max_length_ratio", 1.3)`.
- Slice 1's "PDF is missing characters" (`check_chars`) applies only to packs that define `check_chars` (`vi`).

The agent reports every problem (slice 1). For missing glyphs it names the characters and the font; digits or punctuation there are a rosettex bug, other characters mean the font lacks them (rare characters outside the packaged sets). The agent does not retranslate around a font.

## 12. `tested` and the "untested" message (SG §11.5 rule 7)
- Every new pack ships `tested: false`. `extract` returns `lang_tested`, `script`, `pack_found`, `script_known`, `fonts`; `report.json` gains `script` and `fonts`.
- The agent says "this language has not been tested" when `lang_tested` is false, and "there is no language pack for <code>; checks are generic for the <script> script" when `pack_found` is false.
- A pack becomes `tested: true` only by editing its JSON after a reader of that language has looked at a sample PDF (plan Task 12) and confirmed shaping, direction, line breaking, labels and rules.

## 13. Protocol changes (single source `rosettex/protocol/protocol.md`)
- Step 0 (language): the user names the language in words; the agent uses its BCP-47 code (with a script subtag when the language has several scripts, e.g. `sr-Latn`), and asks the user only if the language is unclear.
- Step 1 (extract): report language, code and `script` to the user; `script_known: false` → ask the user which writing system the language uses, then stop; `pack_found: false` → the message of §12; `font_missing: true` → tell the user to reinstall rosettex, give `fonts_needed`, stop.
- Step 3 (translate): the pack rules carry the script rules (`{{RULES}}` unchanged in mechanism).
- Step 5 (render): `font_missing: true` → as in step 1.
- Step 6 (report): `postcheck.missing_glyphs` → list characters and font.
- `skill/SKILL.md` and `skill/AGENTS.md` are regenerated (`uv run python -m rosettex.protocol.build`); the sync test guards them.

## 14. Preserving `vi`
- Before any code change, save `tests/golden/vi_short_main.tex` = `Renderer(...).document()` for `short` with `fake_vi`; a test asserts the new renderer reproduces it exactly. The `latin` profile carries the exact font entry and options of the old `vi.json`; `vi` has layout `plain` and no `labels.figure`.
- All slice 1 tests pass without edits; `tests/fixtures/` is not modified. New data goes into `tests/golden/` and `tests/script_samples.json`.

## 15. Error behaviour

| Situation | Behaviour |
|---|---|
| Pack exists | normal flow; `lang_tested` from the pack |
| No pack, code in `languages.json` | default pack with the script's profile and font; `pack_found: false`; agent reports and continues |
| Unknown code | default pack (Latin, Noto Serif); `pack_found: false`, `script_known: false`; agent asks the user for the writing system and stops |
| Script outside the 15 | not in `languages.json` → as "unknown code" |
| Packaged font missing or corrupt | `extract`/`render` exit 2, `font_missing: true` (reinstall) |
| Bundle font cannot be loaded at compile time | `render` exit 2, `font_missing` with that file |
| A shipped pack names an unknown profile, or disagrees with `languages.json` | a test fails; never reaches users |
| Malformed code | exit 2 (slice 1) |

## 16. Code changes

| File | Change |
|---|---|
| `rosettex/langpacks/scripts/*.json` | new: 15 profiles |
| `rosettex/langpacks/*.json` | `vi`, `default` updated; 20 new packs |
| `rosettex/langpacks/languages.json` | new |
| `rosettex/fonts/` | new: 14 fonts, `LICENSES/`, `MANIFEST.json` (already built, §6.1) |
| `scripts/build_fonts.py` | new, dev-only |
| `rosettex/checks.py` | `load_pack` (§5.2–5.3), detector, alphabet, digits, word separators |
| `rosettex/fonts.py` | new: `resolve_fonts`, `verify`, `stage_fonts`, `fontspec`, `FontMissing`, `font_error` |
| `rosettex/tex.py` | `wrap_runs` |
| `rosettex/render/latex.py` | preamble per layout; `Renderer` wrapping, RTL placeholders, bibliography, labels |
| `rosettex/render/compile.py` | resolve and stage fonts; font errors; `script`, `fonts` in the report |
| `rosettex/render/postcheck.py` | §11 |
| `rosettex/cli.py` | `extract`/`status`/`render`: font check, `script`, `pack_found`, `script_known`, `fonts` |
| `rosettex/protocol/protocol.md`, `skill/*`, `README.md` | §13, packaged fonts and licences |
| `rosettex/workspace.py`, `tools.py`, `translate.py` | unchanged |

## 17. Testing
- **Unit (no TeX):** every pack loads, names an existing profile and agrees with `languages.json`; mapped and unknown codes resolve as §5.3; profile blocks parse; `MANIFEST.json` matches the files (sha256, licence file present); the built wheel contains `rosettex/fonts/*` (§6.1); `wrap_runs` for `runs`/`rtl`/`zwsp`; preamble per layout; golden `vi`; detector (English source rejected, sample accepted, every pack); alphabet; digits; word separators; font resolution and a corrupted packaged font → `FontMissing`; `extract` exits 2 with `font_missing` before MinerU; post-check parsing.
- **Sample texts:** `tests/script_samples.json`, one or two sentences per pack in the target script (`th`, `my` with U+200B between words), each ≥ 40 letters. `fake_script(src, sample)` keeps every placeholder and number of the source.
- **PDF build (needs TeX):** `render` of `short` per pack with `fake_script`: post-check ok apart from length/overfull, `missing_glyphs == []`, no dotted circle, and (except `rtl`) the PDF text contains characters of the profile's blocks. No skips for missing fonts: every font is packaged or in the bundle.
- **Real agent** (§1 item 4) and **reader check** (§12).

## 18. Out of scope and deferred
Run-time font download, `setup`, `doctor` (slice 3); `review` (slice 2); OCR (slice 4); localised digits (never); vertical CJK, ruby, Zawgyi conversion, Nastaliq; bold/italic of packaged fonts; scripts beyond the 15; XeLaTeX/macOS/Windows beyond what Task 2 and CI can reach; `tested: true` without a reader.

## 19. Decisions of this slice (all confirmed by the user on 2026-11-18)
1. Script profile JSON separate from the language pack; packs name their profile; packs merge over `default.json` (whose `fonts` never leak into a pack).
2. Four layouts; Termes stays the Latin font (decision 16); Cyrillic and `default` use Noto Serif (Termes has no Cyrillic).
3. In `runs`, only target-script runs switch font, so digits, punctuation and generated numbers stay Latin.
4. Every non-Latin, non-Cyrillic font ships in `rosettex/fonts/` (14 files, OFL, subset, built reproducibly from the official releases); the bundle is used only for Termes, Noto Serif and Latin Modern Math. No user font directory. Amends SG §11.5 rule 1.
5. One font per script, from one family (Noto Serif; Noto Naskh Arabic for Arabic, chosen over Amiri for smaller size and better text extraction); CJK fonts cover the common-character tables of each language (§6.1).
6. Arabic output is right to left, including two-column order and tables; generated numbers in ASCII digits.
7. Thai and Myanmar break lines at U+200B written by the translator (no ICU in Tectonic).
8. A language→script table (`languages.json`) decides the script; the agent only turns the user's words into a code and reports the result.
9. Packs for 21 target languages (one or more per script).
10. Detector = target-script ratio (plus function words where the pack has them); non-ASCII digits and missing word separators are errors.
11. Post-check adds missing glyphs per font, dotted circles (not for `rtl`), overfull lines for `cjk`/`zwsp`, a per-pack length threshold.
12. Only Regular weights are packaged.
13. Every new pack ships `tested: false`; only a reader's confirmation flips it.

## 15. Amendments after implementation and cross-review (2026-11-18)
These supersede the sections they name.
1. **§5.3 codes.** `load_pack` accepts any case and `_`/`-` (`ja-JP`, `zh-cn`, `hi_IN`, `VI`, `sr_latn`). Candidates, most specific first: `lang-Script`/`lang-REGION`, then `lang`; the first that is a pack file or a `languages.json` entry wins. `pack["code"]` stays the code as given. `extract` accepts the same forms.
2. **§10.2 Latin-script languages without a pack** (`de`, `es`, `pt`, …; `script_known: true`, no `letters`): `bad_chars` rejects letters outside Latin (U+00C0–024F, U+1E00–1EFF, ASCII) and Greek. Unknown codes (`script_known: false`) are not judged.
3. **§10.4 Thai/Myanmar.** Besides the 25-character run rule, a text with 40 or more script letters and no U+200B at all is rejected (spaces between every word do not give Tectonic break points).
4. **§6.4 build-time font errors** are stored in the job state (`progress.json`, key `extra`), so `status` returns `font_missing`, `script`, `fonts_needed` after `render_failed`. `render` returns them directly too. A missing or damaged `MANIFEST.json` is `FontMissing`, never a traceback.
5. **§6.1 scale.** Script fonts are drawn larger than Termes. `runs`/`rtl` script fonts get `Scale=MatchLowercase`, or the profile's `scale` (Telugu 0.75, Kannada 0.75, Ethiopic 0.8, Bengali 0.9, Gujarati 0.9); Noto Serif (Cyrillic, `default`) `Scale=0.9`.
6. **§5.1 `polyglossia_options`.** Languages of an `rtl` script without a pack get `numerals=maghrib`; `fa` and `ur` use `numerals=western` (from `gloss-persian.ldf`, `gloss-urdu.ldf`).
7. **§7.3 and §8 right to left.** The English bibliography is `{\selectlanguage{english}\renewcommand{\refname}{\RL{\<lang>font …}} …}`; `otherlanguage{english}` around `thebibliography` fails in Tectonic (`Missing } inserted`). Noto Naskh Arabic has no Latin letters or parentheses, so `\text`, equation tags, `sup` spans and whole English verbatim spans (authors, e-mails, daggers) go through `\rxltr{}` / the Latin font.
8. **§5 post-build log parsing.** TeX wraps log lines at 79 characters: "Missing character" and "undefined" warnings are parsed across line breaks.
9. **Checked, no change.** A `\typeout{RX:id}` marker before a float attributes errors in the float body correctly (the body is boxed in place; reproduced with an injected undefined macro in a table, fallback went to the table block).
10. **§7 tables (added 2026-11-18).** `Renderer.col_spec` estimates each column's natural width (em: full-width characters 1, others 0.5, placeholder 3 characters) against the column width. If the table fits (≤ 115 % of the width) the slice 1 layout stays (`l` + `c`, byte-identical for `vi`). Otherwise columns longer than 5 em become `>{\raggedright\arraybackslash}p{<fraction>\linewidth}`, shorter ones keep `l`/`c`; `\multirow` in such a column uses `{=}`, `\multicolumn` over one becomes a `p{}` cell; `array` is added to the preamble; the table's `adjustbox` also gets `max totalheight=0.9\textheight`. Probes use the same layout.
11. **§7.3 rtl neutrals (added 2026-11-18, found by the first real Arabic run).** The text outside Latin runs is not kept in the Arabic font any more for characters Noto Naskh Arabic lacks (brackets, quotes, `; ? - – — % / + = * & # @ $ _ ~ ^ |`, `… · • ° ×`): each becomes `\rxltr{…}` (Latin font). Brackets, angle brackets, braces and quotes are written mirrored (`(`→`)`, `[`→`]`, `“`→`”`, `«`→`»`), because the Latin glyph is not mirrored by the bidi engine; a logical "(" at the right edge then looks like an opening parenthesis. This replaces "parentheses stay in the RTL flow" of §7.3.
12. **Venue style (added 2026-11-18, approach B, chosen by the user).** The build keeps the author's presentation by using the venue's own LaTeX class instead of the generic `article`. `paper_style(doc)` detects IEEE (a paragraph starting `Abstract—` and two or more Roman-numbered headings) → `\documentclass[conference]{IEEEtran}` (no `geometry`; `a4paper` when the source is A4): Roman sections in IEEE's centred small caps (a Roman number counts only if it is the next section number, so "C. …" stays a subsection), lettered subsections in italics, the title at 24 pt, `Abstract—`/`Index Terms—` paragraphs (and the rest of the abstract) in bold with an italic lead-in, IEEE captions and Roman table numbers. Other papers keep the generic build, so `vi`'s golden file is unchanged. Not yet: ACM (`acmart`), Springer (`llncs`), Elsevier (`elsarticle`) — all four classes are in Tectonic's bundle; authors side by side; bold run-in heads inside paragraphs.
13. **Adapter structure fixes (added 2026-11-18).** A table whose "TABLE I" label is a caption part of its own keeps that number; "TABLE IV" + caption paragraph + uncaptioned table are folded into one table; headings and paragraphs inside a figure's box, or a heading sitting right on top of a figure in the same column (a diagram's title read as a heading), are dropped and reported in `dropped` as "text inside figure".
14. **Figure crops (added 2026-11-18).** MinerU's image crop is only the figure body, so titles drawn above a diagram and notes drawn under it were cut off. The adapter stores a `crop` box per figure: the body, down to just above its real caption (the last caption part), up to the nearest block or block part above it in the same column (at most 0.06 of the page height; a diagram title read as a heading widens it to that heading). `render` renders that box from `src.pdf` with pypdfium2 (220 dpi, white rows above and below trimmed) instead of copying MinerU's JPEG. Caption parts between the body and the real caption are inside the picture and are no longer reported as dropped.
15. **Float placement (added 2026-11-18, asked by the user, all languages).** A figure or table is emitted just before the paragraph that first mentions it (`\ref` placeholder), else at its own place; one-column floats are `[!htbp]`, two-column floats `[!tp]`; the float parameters let floats fill up to 90 % of a page. Invisible marks (`\rxpage`, a `\write16` at shipout) record the page of each first mention, each float and the end of each paragraph. After the build, a float that is not on the page of its first mention is re-issued before the first paragraph that starts on that page (one page earlier for two-column floats, which LaTeX puts on the next page) and the paper is rebuilt, at most 3 times; the best build wins, a float before its mention costing double (convention: same page, otherwise after, never before). `report.json` lists the rest in `floats_off_page`. Equations are not floats: they stay in the text where they are. The `vi` golden file was regenerated for this change (the slice 1 test now expects a float before the paragraph that mentions it).
16. **RTL: every non-Arabic character in the Latin font (added 2026-11-18, found by the 20-language run).** In `rtl` text outside Latin runs, only the script's own blocks, spaces and `. , : !` stay in the Arabic font; every other character (`µ`, `±`, dashes, brackets mirrored, …) goes into `\rxltr{}`. Generated table numbers (IEEE `I`, `II`, …) are wrapped too (`\thetable` → `\rxltr{…}`), and the IEEE abstract dash is `\rxltr{—}`.
17. **Thai/Myanmar emergency breaks (added 2026-11-18).** Inside a Thai/Myanmar run, a costly break `\rxbrk` (`\penalty5000\hskip0pt`) is placed between clusters: before a base letter, never before a combining mark or a Thai following vowel (ะ า ำ ๅ), never after a Thai leading vowel (เ แ โ ใ ไ) or the Myanmar stacking sign, never before a Myanmar consonant carrying asat/virama. U+200B stays the normal word break; `\rxbrk` is used only when a word would otherwise overflow (short table cells have no U+200B). Thai syllables are not exact without a dictionary.
18. **Decimal-comma languages (added 2026-11-18, asked by the user).** A pack with `"decimal_comma": true` (`fr`, `ru`, `uk`) may write numbers in its own style: `0.05` → `0,05`, `1,000` → `1 000` (space, no-break space, narrow no-break space) or `1.000`; keeping `0.05` is also accepted, but English `1,000` is rejected because it reads as 1 there. Other packs (including `vi`) keep the slice 1 rule: numbers exactly as in the source.
19. **Paragraphs split by a float (added 2026-11-18).** A paragraph that does not end a sentence is joined with the next one when that one starts with a lowercase letter after opening brackets or quotes ("(collusion) looks …"), or when the first one stops on a comma, semicolon or dash.
20. **Length ratio is a warning (added 2026-11-18, SG decision 5 amended per the user).** `postcheck` returns `warnings` next to `problems`; the content length ratio never makes `ok` false. The ceiling for the warning is the pack's `max_length_ratio`, else the script profile's (Tamil 1.7, Myanmar 2.3), else 1.3.

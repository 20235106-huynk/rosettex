# rosettex slice 1b — Implementation Plan (script coverage)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** a user names any of 21 target languages in 15 scripts; `rosettex` maps it to its script and builds a correct PDF with fonts that ship inside the package, in the right direction, with working line breaks, per-script checks and a missing-glyph post-check. `vi` behaves exactly as in slice 1.

**Architecture:** data describes scripts and languages: `rosettex/langpacks/scripts/<id>.json` (profile), `rosettex/langpacks/<code>.json` (pack), `rosettex/langpacks/languages.json` (code → script). Fonts: 14 subset OFL files in `rosettex/fonts/` (already built, with `MANIFEST.json` and `LICENSES/`), plus three bundle fonts. Code reads the data in `checks.py` (packs, checks), `fonts.py` (resolve, verify, stage), `tex.py` (`wrap_runs`), `render/latex.py` (preamble per layout), `render/postcheck.py`.

**Tech Stack:** unchanged (Python ≥ 3.11, `pypdfium2` only at run time, pytest, uv, Tectonic 0.17.0 or XeLaTeX). fontTools is used only by the dev script `scripts/build_fonts.py`, through `uv run --with fonttools==4.68.0`.

**Spec:** `docs/superpowers/specs/2026-11-18-rosettex-slice1b-design.md` (**S1b**, authoritative), SG `docs/superpowers/specs/2026-11-18-rosettex-design.md`, spike `docs/superpowers/notes/2026-11-18-slice1b-spike/results.md`.

## Global Constraints

- All slice 1 Global Constraints still apply: Linux/macOS/Windows, `pypdfium2` the only runtime dependency, no `shell=True`, UTF-8 with `\n`, atomic JSON writes, `--json` everywhere, exit codes 0/1/2, NFC, placeholders `⟦<kind><n>⟧`.
- `tests/fixtures/` is never modified. New test data goes into `tests/golden/` and `tests/script_samples.json`.
- Fonts are loaded **by file name**. Packaged fonts are copied into `work/out/fonts/` and loaded with `Path=fonts/`; never an absolute path (Tectonic cannot read them).
- The 14 files in `rosettex/fonts/` are the only font files in the repository. Do not add, remove or edit font files except through `scripts/build_fonts.py` (Task 1).
- There is **no user font directory** (S1b decision 4). Do not read fonts from the operating system.
- Every slice 1 test keeps passing without edits; `main.tex` for `vi`/`short` stays byte-identical (golden file, Task 3).
- Do not change a decision of SG §19. If a task forces it, stop and ask the user.
- Commits only when the user asks; one commit per task then; **no `Co-Authored-By` trailer**.
- Network on the dev machine: IPv6 is broken; use `curl -4`; Python downloads may hang. Rerun a failing first Tectonic run once before concluding anything.

## Review Focus

1. `vi` unchanged (golden `main.tex`, slice 1 tests).
2. Digits and ASCII punctuation never inside `\rxs`; every pack builds with `missing_glyphs == []` (Task 9).
3. Arabic: right-to-left layout, tables mirrored, LTR runs for numbers/Latin/placeholders, ASCII digits for generated numbers (Tasks 2, 6, 9).
4. Thai/Myanmar: U+200B becomes a break point; `accept` demands it (Tasks 4, 6, 9).
5. Language → script by data (`languages.json`), never guessed by code or agent (Tasks 3, 8, 10).
6. Fail early: a damaged package font stops `extract` before MinerU (Task 8).

---

## File structure

```
rosettex/
  fonts/                               existing: 14 fonts, LICENSES/, MANIFEST.json (Task 1 makes them reproducible)
  langpacks/scripts/*.json             new: 15 profiles (Task 3)
  langpacks/languages.json             new (Task 3)
  langpacks/{fr,ru,uk,zh-Hans,zh-Hant,ja,ko,ar,fa,ur,hi,mr,bn,te,ta,gu,kn,th,am,my}.json   new (Task 3)
  langpacks/vi.json, default.json      changed (Task 3)
  checks.py                            load_pack (Task 3); detector, alphabet, digits, word separators (Task 4)
  fonts.py                             new (Task 5)
  tex.py                               wrap_runs (Task 6)
  render/latex.py                      preamble per layout, Renderer wrapping, labels (Task 6)
  render/compile.py, render/postcheck.py   fonts, font errors, post-check (Task 7)
  cli.py                               extract/status/render (Task 8)
  protocol/protocol.md                 (Task 10) → skill/SKILL.md, skill/AGENTS.md regenerated
scripts/build_fonts.py                 new, dev-only (Task 1)
tests/
  test_fonts_package.py                new (Task 1)
  golden/vi_short_main.tex             new (Task 3)
  test_scripts.py                      new (Tasks 3, 6)
  test_fonts.py                        new (Task 5)
  script_samples.json                  new (Task 9)
  test_checks.py, test_render.py, test_cli.py, test_protocol.py   extended
docs/superpowers/notes/2026-11-18-slice1b-spike/results.md          extended (Task 2)
docs/superpowers/notes/2026-11-18-slice1b-acceptance.md             new (Task 12)
```

---

### Task 1: Reproducible font build, manifest and wheel checks

**Context:** the 14 packaged fonts in `rosettex/fonts/` were built by hand from the official releases (S1b §6.1). This task commits the script that rebuilds them byte for byte, a test that guards the manifest, and a check that the wheel ships them.

**Files:**
- Create: `scripts/build_fonts.py`, `tests/test_fonts_package.py`
- Possibly modify: `rosettex/fonts/*` (only if Step 4 finds a difference and the stop rule says so)

**Interfaces:**
- Produces: `MANIFEST.json` schema `rosettex.fonts/1`: `{"schema", "unihan": {url, sha256}, "fonts": [{file, script, bytes, sha256, family, version, copyright, license, license_file, source, source_sha256, source_file, modified}]}`. Task 5 reads `file` and `sha256`.

- [ ] **Step 1: Write the failing test** `tests/test_fonts_package.py`:

```python
import hashlib
import json
from importlib.resources import files

FONTS = files("rosettex") / "fonts"


def manifest():
    return json.loads((FONTS / "MANIFEST.json").read_text(encoding="utf-8"))


def test_manifest_matches_files():
    m = manifest()
    assert m["schema"] == "rosettex.fonts/1" and m["unihan"]["url"].endswith("/18.0.0/ucd/Unihan.zip")
    names = {p.name for p in FONTS.iterdir() if p.name.endswith((".ttf", ".otf"))}
    assert {e["file"] for e in m["fonts"]} == names and len(names) == 14
    for e in m["fonts"]:
        data = (FONTS / e["file"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == e["sha256"] and len(data) == e["bytes"]
        assert e["license"] == "OFL-1.1" and (FONTS / e["license_file"]).is_file()
        assert e["source"].startswith("https://github.com/notofonts/") and len(e["source_sha256"]) == 64
```

- [ ] **Step 2: Run to see the failure.** `uv run pytest tests/test_fonts_package.py -v` → FAIL (`KeyError: 'unihan'`).

- [ ] **Step 3: Write `scripts/build_fonts.py`** (dev-only; never imported by `rosettex`):

```python
"""Rebuild rosettex/fonts/ from the official font releases (dev-only; S1b §6.1).

    uv run --with fonttools==4.68.0 python scripts/build_fonts.py --cache <dir> [--out rosettex/fonts]

Each source zip is read from --cache, downloaded there if missing, and checked against its sha256.
On a machine where Python downloads hang (broken IPv6), fetch the zips first with
`curl -4 -L -o <cache>/<name> <url>`; the script prints the URLs it needs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

N = "https://github.com/notofonts"
UNIHAN = ("https://www.unicode.org/Public/18.0.0/ucd/Unihan.zip",
          "4c93ea9c1f636451729a840978f1667a53886af37ba854fdcce109721c63d43e")
INDIC = "0020-007E,00A0-00FF,2000-206F,200C-200D,25CC,20AC,0964-0965"
ARABIC = "0020-007E,00A0-00FF,2000-206F,25CC,0600-06FF,0750-077F,08A0-08FF,FB50-FDFF,FE70-FEFF"
COMMON_OPTS = ["--layout-features=*", "--name-IDs=*", "--name-languages=*", "--notdef-outline", "--no-hinting"]
SERIF = {  # script: (version, zip sha256, blocks)
    "Bengali": ("v3.000", "3ee7ed1a48f115298ec7fd42d3d72c51fcdca6e7adceae65a0fa4f652b164795", "0980-09FF"),
    "Devanagari": ("v2.006", "b823456dfaf6ef8b438eeb906da42effe01927add1e3416ecc86c99c5f1cf098", "0900-097F,A8E0-A8FF"),
    "Ethiopic": ("v2.102", "2cd163ed52f333c26fc2a55822ab1cabe875b782d73f9d55dfe5863d9e236c26", "1200-139F,2D80-2DDF,AB00-AB2F"),
    "Gujarati": ("v2.106", "614847e5648483a32987a7af583a8c8311b5b8926140778c10820baf1fa4e212", "0A80-0AFF"),
    "Kannada": ("v2.005", "9d516560f21f4ca8dc35d49e7f64f67b40e9e41bddf5aa2e212c52ee9f985779", "0C80-0CFF"),
    "Myanmar": ("v2.106", "dc425f27d119c763c0f567c938f8f629effcb1f13df1e2661baec3e5a2d046e3", "1000-109F,A9E0-A9FF,AA60-AA7F"),
    "Tamil": ("v2.004", "96b61d2f72b8fd69adc7ef644918cdaab9dd6c83f6440587fe93e6461a0aa82c", "0B80-0BFF"),
    "Telugu": ("v2.005", "98b9b2478b4c2febefcdf92693ba098f44d55a70570b9cb88fad55018cc2f7f5", "0C00-0C7F"),
    "Thai": ("v2.002", "89e3c04bfc54d9a5dd0aec660bf974ad46df38385df7d9397c083482d515049a", "0E00-0E7F"),
}
CJK = {  # region: (zip, zip sha256, script id, description of the kept set)
    "SC": ("14_NotoSerifSC.zip", "c58cd035ab2adb003510846db9ec80c35b1b97755d329486c3a1e88edfe6e98e", "han-hans",
           "Table of General Standard Chinese Characters (8105, Unihan kTGHZ2013) + GB 2312"),
    "TC": ("15_NotoSerifTC.zip", "cadfc894416096a51174e8a350e5cb872f99a8c4b1d36c3990e7a9741e1456ab", "han-hant",
           "Big5 (Unihan kBigFive + Python codec big5)"),
    "JP": ("12_NotoSerifJP.zip", "53bdd2a6e4eb63bf24f7890e018dddb94366e3555d0814c72b74fbb128f328f0", "kana",
           "JIS X 0208 (Python codec euc_jp) + Joyo and Jinmeiyo kanji (Unihan) + kana blocks"),
    "KR": ("13_NotoSerifKR.zip", "ac3398873346bc97e00a9652afe791cad6a2df3e5f157c4e7d40ce60b2b88605", "hangul",
           "all 11172 Hangul syllables + jamo blocks + KS X 1001 (Python codec euc_kr)"),
}
NASKH = ("NotoNaskhArabic-v2.021", "6c050ab9bd087d69b733c505a7576e60c528c2f33cd7b91005a5bd7da4514032")


def fetch(cache: Path, url: str, sha: str) -> Path:
    p = cache / url.rsplit("/", 1)[1]
    if not p.exists():
        print("downloading", url, flush=True)
        with urllib.request.urlopen(url, timeout=600) as r, open(p, "wb") as f:
            f.write(r.read())
    got = hashlib.sha256(p.read_bytes()).hexdigest()
    if got != sha:
        sys.exit(f"{p}: sha256 {got} != {sha}")
    return p


def rng(a: int, b: int) -> set[str]:
    return {chr(c) for c in range(a, b + 1)}


def codec_chars(codec: str, lead, trail, prefix: bytes = b"") -> set[str]:
    out = set()
    for a in lead:
        for b in trail:
            try:
                out.update((prefix + bytes([a, b])).decode(codec))
            except UnicodeDecodeError:
                pass
    return out


def cjk_sets(unihan: Path) -> dict[str, set[str]]:
    uni: dict[str, set[str]] = {}
    with zipfile.ZipFile(unihan) as z:
        for n in z.namelist():
            for line in z.read(n).decode("utf-8").splitlines():
                if line.startswith("U+"):
                    cp, prop, _ = line.split("\t", 2)
                    if prop in ("kTGHZ2013", "kBigFive", "kJoyoKanji", "kJinmeiyoKanji"):
                        uni.setdefault(prop, set()).add(chr(int(cp[2:], 16)))
    hi = range(0xA1, 0xFF)
    common = rng(0x20, 0x7E) | rng(0xA0, 0xFF) | rng(0x2000, 0x206F) | rng(0x3000, 0x303F) | rng(0xFF00, 0xFFEF) | {"\u25cc"}
    sets = {
        "SC": uni["kTGHZ2013"] | codec_chars("gb2312", hi, hi),
        "TC": uni["kBigFive"] | codec_chars("big5", range(0xA1, 0xFA), list(range(0x40, 0x7F)) + list(range(0xA1, 0xFF))),
        "JP": uni["kJoyoKanji"] | uni["kJinmeiyoKanji"] | codec_chars("euc_jp", hi, hi) | rng(0x3040, 0x30FF) | rng(0x31F0, 0x31FF),
        "KR": rng(0xAC00, 0xD7A3) | rng(0x1100, 0x11FF) | rng(0x3130, 0x318F) | rng(0xA960, 0xA97F) | rng(0xD7B0, 0xD7FF)
              | codec_chars("euc_kr", hi, hi),
    }
    return {k: {c for c in v | common if c.isprintable() or c == " "} for k, v in sets.items()}


def subset(src: Path, dst: Path, unicodes: str | None = None, unicodes_file: Path | None = None, drop_dsig=True):
    args = [sys.executable, "-m", "fontTools.subset", str(src), *COMMON_OPTS, f"--output-file={dst}"]
    args.append(f"--unicodes={unicodes}" if unicodes else f"--unicodes-file={unicodes_file}")
    if drop_dsig:
        args.append("--drop-tables+=DSIG")
    subprocess.run(args, check=True)


def extract(zpath: Path, inner_suffix: str, dst: Path) -> str:
    with zipfile.ZipFile(zpath) as z:
        name = next(n for n in z.namelist() if n.endswith(inner_suffix))
        dst.write_bytes(z.read(name))
        return name


def licence(zpath: Path, dst: Path) -> None:
    with zipfile.ZipFile(zpath) as z:
        name = next(n for n in z.namelist() if n.endswith(("OFL.txt", "LICENSE")))
        dst.write_bytes(z.read(name))


def main() -> None:
    from fontTools.ttLib import TTFont

    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", required=True, type=Path)
    ap.add_argument("--out", default=Path("rosettex/fonts"), type=Path)
    a = ap.parse_args()
    a.cache.mkdir(parents=True, exist_ok=True)
    (a.out / "LICENSES").mkdir(parents=True, exist_ok=True)
    tmp = a.cache / "extracted"
    tmp.mkdir(exist_ok=True)
    jobs = []  # (out file, script, url, zip sha, inner file, description, subset kwargs, licence file)
    for s, (v, sha, blocks) in SERIF.items():
        tag = f"NotoSerif{s}-{v}"
        jobs.append((f"NotoSerif{s}-Regular.ttf", s.lower(), f"{N}/{s.lower()}/releases/download/{tag}/{tag}.zip", sha,
                     f"/unhinted/ttf/NotoSerif{s}-Regular.ttf", f"the {s} Unicode blocks",
                     {"unicodes": f"{INDIC},{blocks}"}, f"NotoSerif{s}-OFL.txt"))
    tag, sha = NASKH
    jobs.append(("NotoNaskhArabic-Regular.ttf", "arabic", f"{N}/arabic/releases/download/{tag}/{tag}.zip", sha,
                 "/unhinted/ttf/NotoNaskhArabic-Regular.ttf",
                 "Arabic blocks U+0600-06FF, 0750-077F, 08A0-08FF, FB50-FDFF, FE70-FEFF", {"unicodes": ARABIC},
                 "NotoNaskhArabic-OFL.txt"))
    sets = cjk_sets(fetch(a.cache, *UNIHAN))
    for k, (zname, sha, script, desc) in CJK.items():
        uf = tmp / f"u-{k}.txt"
        uf.write_text(",".join(f"{ord(c):04X}" for c in sorted(sets[k])), encoding="utf-8")
        jobs.append((f"NotoSerif{k}-Regular.otf", script, f"{N}/noto-cjk/releases/download/Serif2.003/{zname}", sha,
                     f"SubsetOTF/{k}/NotoSerif{k}-Regular.otf", desc, {"unicodes_file": uf, "drop_dsig": False},
                     f"NotoSerif{k}-OFL.txt"))
    out = []
    for file, script, url, sha, inner, desc, kw, lic in jobs:
        z = fetch(a.cache, url, sha)
        raw = tmp / file
        inner_name = extract(z, inner, raw)
        dst = a.out / file
        subset(raw, dst, **kw)
        licence(z, a.out / "LICENSES" / lic)
        nm = TTFont(dst)["name"]
        out.append({"file": file, "script": script, "bytes": dst.stat().st_size,
                    "sha256": hashlib.sha256(dst.read_bytes()).hexdigest(),
                    "family": nm.getDebugName(1), "version": nm.getDebugName(5), "copyright": nm.getDebugName(0),
                    "license": "OFL-1.1", "license_file": f"LICENSES/{lic}", "source": url, "source_sha256": sha,
                    "source_file": inner_name,
                    "modified": f"subset with fontTools 4.68.0 to {desc}, plus U+0020-007E, U+00A0-00FF, U+2000-206F, "
                                "U+25CC and the script's punctuation; all layout features kept; hinting removed"})
    out.sort(key=lambda e: e["file"])
    (a.out / "MANIFEST.json").write_text(json.dumps({"schema": "rosettex.fonts/1",
                                                     "unihan": {"url": UNIHAN[0], "sha256": UNIHAN[1]},
                                                     "fonts": out}, ensure_ascii=False, indent=1) + "\n",
                                         encoding="utf-8", newline="\n")
    print(len(out), "fonts,", sum(e["bytes"] for e in out), "bytes")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Reproduce.** Download the 15 source files into a cache directory outside the repo (`curl -4 -L -o <cache>/<name> <url>` for each URL the script prints; the Unihan URL too). Copy the current fonts aside: `cp -r rosettex/fonts <scratch>/fonts-before`. Run `uv run --with fonttools==4.68.0 python scripts/build_fonts.py --cache <cache>`. Compare: `uv run python -c "import hashlib,pathlib; [print(p.name, hashlib.sha256(p.read_bytes()).hexdigest()==hashlib.sha256((pathlib.Path('<scratch>/fonts-before')/p.name).read_bytes()).hexdigest()) for p in sorted(pathlib.Path('rosettex/fonts').glob('*.?tf'))]"`. Expected: `True` for all 14.
  **Stop rule:** a font differs → read `modified` in the old manifest and fix the script's character set or options until it matches. If it still differs after two attempts (for example a different Python codec table), keep the script's output, rerun the spike probe for that script (Task 2 Step 3 format) to confirm 0 missing glyphs, and note the difference in `results.md`.
- [ ] **Step 5: Run.** `uv run pytest tests/test_fonts_package.py -v` → PASS (the script wrote `unihan`).
- [ ] **Step 6: Wheel check.** `uv build --wheel -o <scratch>/dist`, then `uv run python -c "import zipfile,glob; n=zipfile.ZipFile(glob.glob('<scratch>/dist/*.whl')[0]).namelist(); f=[x for x in n if x.startswith('rosettex/fonts/')]; print(len(f)); assert sum(x.endswith(('.ttf','.otf')) for x in f)==14 and 'rosettex/fonts/MANIFEST.json' in f and sum('/LICENSES/' in x for x in f)==14"`. If it fails, add `[tool.hatch.build.targets.wheel] packages = ["rosettex"]` to `pyproject.toml` and retry.
- [ ] **Step 7: Run all tests** `uv run pytest -q`. **Commit** (if asked): `git add scripts/build_fonts.py tests/test_fonts_package.py rosettex/fonts` and `git commit -m "build: reproducible packaged fonts"`.

---

### Task 2: Spike close-out — whole-document probes per layout

**Context:** the spike compiled one sentence per script. Before Task 6 freezes the preamble, compile a mini paper per layout with the packaged fonts. Facts already verified are in S1b §3.1; do not re-test them.

**Files:**
- Modify: `docs/superpowers/notes/2026-11-18-slice1b-spike/results.md` (section "Close-out: whole documents")
- Scratch (not committed): `<scratch>/layouts/`

**Interfaces:**
- Produces: the exact preamble lines per layout used by Task 6; for each `rtl` pack the `polyglossia_options` value; the polyglossia language names that compile (`french`, `ukrainian`, `persian`, `urdu`, `marathi`, plus the others of S1b §5.4); any pack that must be marked `xfail` in Task 9.

- [ ] **Step 1: XeLaTeX.** `uv run python -c "import shutil; print(shutil.which('xelatex'))"`. Absent → record "XeLaTeX: not available, not tested" and skip XeLaTeX in this plan.
- [ ] **Step 2: Mini paper.** One `.tex` per case, in a directory with `fonts/` holding a copy of the packaged font: `\documentclass[10pt,twocolumn]{article}`, the slice 1 shared preamble lines in slice 1 order (see `rosettex/render/latex.py:preamble`), `\twocolumn[{title}]`, `\section*{2\quad <heading>}`, a paragraph with `$x^2$`, `0.05`, `\cite{ref1}`, `\ref{tab:1}`, `\url{https://example.org}`, a `table` float with a caption and a `tabular` with `\multirow` and a text cell, a footnote via `\footnotetext`, `thebibliography` with one English `\bibitem{ref1}`, label renewals of S1b §7.4. Use S1b §7.1 per layout exactly.
- [ ] **Step 3: Cases and checks.**
  - `plain`: `fr` (Termes, `french`), `ru`, `uk` (Noto Serif from the bundle with `BoldFont=NotoSerif-Bold.ttf,ItalicFont=NotoSerif-Italic.ttf,BoldItalicFont=NotoSerif-BoldItalic.ttf`; check those file names exist in the cached bundle index `~/.cache/tectonic/bundles/data/*.index`).
  - `cjk`: `zh-Hans`, `zh-Hant`, `ja`, `ko` (`CJKspace=true`).
  - `runs`: `hi`, `mr`, `bn`, `gu` (no polyglossia), `th` (polyglossia `thai`, U+200B between words replaced by `\hspace{0pt plus 1pt}`), `my` (no polyglossia, same U+200B rule), `am`.
  - `rtl`: `ar`, `fa`, `ur`: language lines after `hyperref`; try `numerals=maghrib`, and for `persian`/`urdu` whichever numerals option their polyglossia gloss documents (`~/.rosettex/bin/tectonic -X bundle cat gloss-persian.ldf`, `gloss-urdu.ldf`) until the section, table and page numbers print in ASCII digits. Bibliography inside `\begin{otherlanguage}{english}…\end{otherlanguage}` with `\renewcommand{\refname}{…}` inside. Heading `\section*{\rxltr{2}\quad …}` and `\section*{\rxltr{2} …}` (plain space): record whether the plain space also puts the number first.
  - For every case record: exit code, `Missing character` count, `Overfull \hbox` count over 10 pt, whether the PDF text (`uv run python -c "from rosettex.pdf import full_text; print(full_text(r'<pdf>'))"`) contains the script's characters (not for `rtl`), and for `rtl`, `ko`, `ja`, `th` a page image (`rosettex.pdf.render_page_png(pdf, 1, out, dpi=110)`) read by you: Arabic first column on the right, table mirrored, numbers in ASCII; Korean spaces present; no Japanese line starting with `。` or `、`; Thai lines broken inside the paragraph.
- [ ] **Step 4: Licences of the bundle fonts.** `curl -4 -s https://ctan.org/pkg/tex-gyre`, `…/noto`, `…/lm-math`: record the licence lines (GUST Font License for TeX Gyre and Latin Modern, OFL for Noto expected).
- [ ] **Step 5: Record** in `results.md`: the exact preamble lines that compiled per layout, the `polyglossia_options` for `ar`/`fa`/`ur`, the polyglossia names, every failure with its error line.

**Stop rules:**
- A case fails after two variations (package order; option form) → record the error; Task 9 marks that pack `xfail` with the reason; Task 6 still implements S1b.
- `rtl` fails in two columns with the full preamble → Task 6 forces one column for `rtl`; the acceptance note says "Arabic: one column".
- A polyglossia name does not exist → that pack gets `"polyglossia": null` (and `runs`/`plain` without polyglossia); for an `rtl` pack the profile default `arabic` is used.

- [ ] **Step 6: Commit** (if asked): `git commit -am "docs: slice 1b spike close-out, whole documents"`.

---

### Task 3: Golden `vi`, profiles, packs, `languages.json`, `load_pack`

**Files:**
- Create: `tests/golden/vi_short_main.tex`, `rosettex/langpacks/scripts/*.json` (15), `rosettex/langpacks/languages.json`, 20 packs `rosettex/langpacks/{fr,ru,uk,zh-Hans,zh-Hant,ja,ko,ar,fa,ur,hi,mr,bn,te,ta,gu,kn,th,am,my}.json`, `tests/test_scripts.py`
- Modify: `rosettex/langpacks/vi.json`, `rosettex/langpacks/default.json`, `rosettex/checks.py`, `rosettex/render/latex.py` (one line, Step 7)

**Interfaces:**
- Consumes: Task 2 (polyglossia names and `polyglossia_options`), `rosettex/fonts/MANIFEST.json` (Task 1).
- Produces (in `checks.py`): `parse_blocks(spec) -> list[tuple[int,int]]`, `in_blocks(c, blocks) -> bool`, `load_profile(id) -> dict` (adds `_blocks`), `load_languages() -> dict`, `load_pack(code) -> dict` with keys `script`, `profile`, `fonts`, `pack_found`, `script_known`, `min_target_ratio`; `font` removed.

- [ ] **Step 1: Save the golden file BEFORE touching any code.**

```bash
uv run python -c "
import json, sys, pathlib; sys.path.insert(0, 'tests')
from conftest import FIX, fake_vi
from rosettex.checks import load_pack
from rosettex.extract.mineru_adapter import adapt
from rosettex.extract.segment import segment
from rosettex.render.latex import Renderer
d = FIX / 'mineru-4.2.3-standard'
doc, _ = segment(adapt([(json.loads((d / 'short.middle.json').read_text(encoding='utf-8')), d)], d))
tr = {u['id']: fake_vi(u['src']) for u in doc['units']}
p = pathlib.Path('tests/golden'); p.mkdir(exist_ok=True)
(p / 'vi_short_main.tex').write_text(Renderer(doc, tr, load_pack('vi'), set(), {}).document(), encoding='utf-8', newline='\n')
"
```

- [ ] **Step 2: Write the failing test** `tests/test_scripts.py`:

```python
import json
from importlib.resources import files

import pytest

from conftest import FIX, fake_vi
from rosettex.checks import in_blocks, load_languages, load_pack, parse_blocks
from rosettex.extract.mineru_adapter import adapt
from rosettex.extract.segment import segment
from rosettex.render.latex import Renderer

PACKS = ["vi", "fr", "ru", "uk", "zh-Hans", "zh-Hant", "ja", "ko", "ar", "fa", "ur", "hi", "mr", "bn", "te", "ta",
         "gu", "kn", "th", "am", "my"]
SCRIPTS = ["latin", "cyrillic", "han", "kana", "hangul", "arabic", "devanagari", "bengali", "telugu", "tamil",
           "gujarati", "kannada", "thai", "ethiopic", "myanmar"]
LAYOUTS = {"plain", "cjk", "runs", "rtl"}
MANIFEST = {e["file"] for e in json.loads((files("rosettex") / "fonts" / "MANIFEST.json").read_text(encoding="utf-8"))["fonts"]}


def shipped_packs():
    base = files("rosettex") / "langpacks"
    return sorted(p.name[:-5] for p in base.iterdir()
                  if p.name.endswith(".json") and p.name not in ("default.json", "languages.json"))


def test_packs_and_scripts_are_complete():
    assert shipped_packs() == sorted(PACKS)
    assert {load_pack(c)["profile"]["id"] for c in PACKS} == set(SCRIPTS)


@pytest.mark.parametrize("code", PACKS)
def test_pack_is_complete(code):
    p, langs = load_pack(code), load_languages()
    prof = p["profile"]
    assert p["pack_found"] is True and p["script_known"] is True and p["code"] == code
    assert langs[code]["script"] == p["script"] == prof["id"]
    assert prof["layout"] in LAYOUTS and (prof["direction"] == "rtl") == (prof["layout"] == "rtl")
    assert prof["linebreak"] in ("space", "cjk", "zwsp")
    for f in p["fonts"]:
        assert f["source"] in ("bundle", "package") and f["license"] and "/" not in f["file"] and "\\" not in f["file"]
        assert f["source"] == "bundle" or f["file"] in MANIFEST
    assert p["tested"] is (code == "vi")
    for k in ("min_len_check", "min_target_ratio", "length_ratio", "labels", "rules"):
        assert k in p
    if prof["layout"] != "plain":
        assert {"figure", "table", "references"} <= set(p["labels"])
    if prof["layout"] == "rtl":
        assert p["polyglossia"] and p.get("polyglossia_options")


def test_blocks_parse():
    b = parse_blocks(["0980-09FF", "200C-200D"])
    assert b == [(0x0980, 0x09FF), (0x200C, 0x200D)]
    assert in_blocks("ব", b) and in_blocks("\u200c", b) and not in_blocks("a", b)
    assert parse_blocks(["20000-3134F"]) == [(0x20000, 0x3134F)]


def test_mapped_language_without_pack_gets_its_script():
    ps = load_pack("ps")
    assert ps["pack_found"] is False and ps["script_known"] is True
    assert ps["profile"]["id"] == "arabic" and ps["polyglossia"] == "arabic"
    assert ps["fonts"][0]["file"] == "NotoNaskhArabic-Regular.ttf" and ps["min_target_ratio"] == 0.3
    bg = load_pack("bg")
    assert bg["profile"]["id"] == "cyrillic" and bg["fonts"][0]["file"] == "NotoSerif-Regular.ttf"
    es = load_pack("es")
    assert es["profile"]["id"] == "latin" and es["fonts"][0]["file"] == "texgyretermes"


def test_aliases():
    assert load_pack("zh")["fonts"][0]["file"] == "NotoSerifSC-Regular.otf" and load_pack("zh")["code"] == "zh"
    assert load_pack("zh-TW")["fonts"][0]["file"] == "NotoSerifTC-Regular.otf"
    sr = load_pack("sr")  # alias to an entry without a pack file
    assert sr["profile"]["id"] == "cyrillic" and sr["pack_found"] is False and sr["script_known"] is True


def test_unknown_language():
    p = load_pack("xx")
    assert p["pack_found"] is False and p["script_known"] is False and p["profile"]["id"] == "latin"
    assert p["fonts"][0]["file"] == "NotoSerif-Regular.ttf"


def test_vi_keeps_termes():
    p = load_pack("vi")
    assert p["fonts"][0]["file"] == "texgyretermes" and p["polyglossia"] == "vietnamese"


def test_vi_main_tex_is_byte_identical_to_slice1():
    d = FIX / "mineru-4.2.3-standard"
    doc, _ = segment(adapt([(json.loads((d / "short.middle.json").read_text(encoding="utf-8")), d)], d))
    tr = {u["id"]: fake_vi(u["src"]) for u in doc["units"]}
    golden = (FIX.parent / "golden" / "vi_short_main.tex").read_text(encoding="utf-8")
    assert Renderer(doc, tr, load_pack("vi"), set(), {}).document() == golden
```

- [ ] **Step 3: Run to see the failure.** `uv run pytest tests/test_scripts.py -v` → FAIL (`ImportError: cannot import name 'in_blocks'`).

- [ ] **Step 4: Write the 15 profiles** (`rosettex/langpacks/scripts/<id>.json`), one per row of S1b §4.2, keys of S1b §4.1. Values not in that table: `otf_script` = `null` for `latin`, `cyrillic`, `han`, `kana`, `hangul`, else the script name with a capital (`"Arabic"`, `"Devanagari"`, `"Bengali"`, `"Telugu"`, `"Tamil"`, `"Gujarati"`, `"Kannada"`, `"Thai"`, `"Ethiopic"`, `"Myanmar"`); `layout_options` = `"CJKspace=true"` for `hangul`, else `""`; `polyglossia_default` = `"arabic"` for `arabic`, else `null`; `min_target_ratio` = `0.3` except `latin` `0.0`. Font entries: the bundle fonts with `"source": "bundle"` and the licence recorded by Task 2; packaged fonts with `"source": "package", "license": "OFL-1.1"`. Two complete examples:

`latin.json` (the `options` string is exactly the one `vi.json` had, so the golden test holds):
```json
{"id": "latin", "layout": "plain", "direction": "ltr", "otf_script": null, "blocks": [], "linebreak": "space",
 "layout_options": "", "polyglossia_default": null, "min_target_ratio": 0.0,
 "fonts": [{"file": "texgyretermes", "source": "bundle", "license": "<Task 2>",
            "options": "Extension=.otf,UprightFont=*-regular,BoldFont=*-bold,ItalicFont=*-italic,BoldItalicFont=*-bolditalic"}]}
```
`thai.json`:
```json
{"id": "thai", "layout": "runs", "direction": "ltr", "otf_script": "Thai", "blocks": ["0E00-0E7F", "200B-200B"],
 "linebreak": "zwsp", "layout_options": "", "polyglossia_default": null, "min_target_ratio": 0.3,
 "fonts": [{"file": "NotoSerifThai-Regular.ttf", "source": "package", "license": "OFL-1.1"}]}
```
`cyrillic.json` font: `{"file": "NotoSerif-Regular.ttf", "source": "bundle", "license": "<Task 2>", "options": "BoldFont=NotoSerif-Bold.ttf,ItalicFont=NotoSerif-Italic.ttf,BoldItalicFont=NotoSerif-BoldItalic.ttf"}` (the file names Task 2 confirmed).

- [ ] **Step 5: Write `languages.json`.** Shape `{"<code>": {"name": "...", "script": "<profile id>", "polyglossia": <name or null>}}`, or for an alias `{"<code>": {"name": "...", "alias": "<pack code>"}}`. Content:
  - every shipped pack: `vi` latin vietnamese; `fr` latin french; `ru` cyrillic russian; `uk` cyrillic ukrainian; `zh-Hans`, `zh-Hant` han null; `ja` kana null; `ko` hangul null; `ar` arabic arabic; `fa` arabic persian; `ur` arabic urdu; `hi` devanagari hindi; `mr` devanagari marathi; `bn` bengali bengali; `te` telugu telugu; `ta` tamil tamil; `gu` gujarati null; `kn` kannada kannada; `th` thai thai; `am` ethiopic amharic; `my` myanmar null (use the names Task 2 confirmed);
  - aliases: `zh`, `zh-CN`, `zh-SG` → `zh-Hans`; `zh-TW`, `zh-HK`, `zh-MO` → `zh-Hant`; `sr` → `sr-Cyrl`;
  - other languages, all with `"polyglossia": null` (no hyphenation, but no risk of an unknown polyglossia name): Latin `en`, `es`, `de`, `pt`, `it`, `nl`, `pl`, `tr`, `id`, `ms`, `ro`, `cs`, `sv`, `sr-Latn`; Cyrillic `bg`, `sr-Cyrl`, `mk`, `be`, `kk`, `ky`, `mn`, `tg`; Arabic `ps`, `ckb`, `ug`, `sd`; Devanagari `ne`, `sa`, `mai`; Bengali `as`; Ethiopic `ti`.

- [ ] **Step 6: Write the packs.** `vi.json`: remove `font`, add `"script": "latin"`, rename `min_diacritic_ratio` → `min_target_ratio` (0.03). `default.json`: same rename (0), `"script": "latin"`, and replace `font` with `"fonts": [<the cyrillic.json font entry>]`. New packs contain only what differs from `default.json`. Full example `bn.json`:

```json
{
 "code": "bn", "name": "বাংলা", "tested": false, "script": "bengali", "polyglossia": "bengali",
 "min_target_ratio": 0.3,
 "labels": {"fallback": "formula/table not rendered", "missing_image": "missing image",
            "figure": "চিত্র", "table": "সারণি", "references": "তথ্যসূত্র"},
 "rules": [
  "Keep every placeholder ⟦…⟧ exactly (you may move it within the sentence).",
  "Keep every number exactly as written in the source, including the decimal point.",
  "Write every number with the ASCII digits 0-9 exactly as in the source; do not use Bengali digits.",
  "Do not translate proper names, method/model/dataset names, units, emails, URLs.",
  "Keep the original level of certainty and the direction of comparisons and negations.",
  "Use the terms in terms.json consistently."
 ]
}
```
  Per pack (name; polyglossia; labels figure/table/references; extra):
  - `fr` Français; `french`; no labels (polyglossia); `letters` = `"éèêëàâçîïôûùüÿœæÉÈÊËÀÂÇÎÏÔÛÙÜŸŒÆ"`, `function_words` = `["le","la","les","des","du","et","est","une","dans","pour","que","qui","sur","par","avec","nous","sont"]`, `min_target_ratio` 0.01, `min_function_ratio` 0.08.
  - `ru` Русский; `russian`; no labels; `function_words` = `["и","в","на","с","что","для","по","из","не","это","как","от","к","при"]`, `min_function_ratio` 0.08. `uk` Українська; `ukrainian`; no labels; `function_words` = `["і","в","на","з","що","для","не","це","як","від","до","при","та"]`, `min_function_ratio` 0.08.
  - `zh-Hans` 简体中文 / `zh-Hant` 繁體中文; null; 图/表/参考文献 and 圖/表/參考文獻; `zh-Hant` adds `"fonts": [{"file": "NotoSerifTC-Regular.otf", "source": "package", "license": "OFL-1.1"}]`; both add the rule "Do not put spaces between CJK characters."; `length_ratio` `[0.2, 1.5]`.
  - `ja` 日本語; null; 図/表/参考文献; same CJK rule and `length_ratio`. `ko` 한국어; null; 그림/표/참고문헌; `length_ratio` `[0.3, 1.8]`.
  - `ar` العربية; `arabic`; شكل/جدول/المراجع; `polyglossia_options` from Task 2 (`numerals=maghrib` expected). `fa` فارسی; `persian`; شکل/جدول/مراجع. `ur` اردو; `urdu`; شکل/جدول/حوالہ جات.
  - `hi` हिन्दी; `hindi`; चित्र/तालिका/संदर्भ. `mr` मराठी; `marathi`; आकृती/तक्ता/संदर्भ.
  - `te` తెలుగు; `telugu`; చిత్రం/పట్టిక/మూలాలు. `ta` தமிழ்; `tamil`; படம்/அட்டவணை/மேற்கோள்கள். `gu` ગુજરાતી; null; આકૃતિ/કોષ્ટક/સંદર્ભો. `kn` ಕನ್ನಡ; `kannada`; ಚಿತ್ರ/ಕೋಷ್ಟಕ/ಉಲ್ಲೇಖಗಳು. `am` አማርኛ; `amharic`; ምስል/ሰንጠረዥ/ማጣቀሻዎች.
  - `th` ไทย; `thai`; รูปที่/ตารางที่/เอกสารอ้างอิง; `my` မြန်မာ; null; ပုံ/ဇယား/ကိုးကားချက်များ, plus "Write Myanmar in Unicode, not Zawgyi.". Both add "Put U+200B (zero-width space) between words; do not put ordinary spaces between words inside a sentence."
  - Every new pack adds the ASCII-digits rule shown in `bn.json` (with its own script name). Labels and rules are confirmed by the reader (Task 12).
  - Write `"polyglossia": null` **explicitly** in the packs that have none (`zh-Hans`, `zh-Hant`, `ja`, `ko`, `gu`, `my`): a missing key would inherit `"english"` from `default.json`.

- [ ] **Step 7: Write the loader in `rosettex/checks.py`** (replace the old `load_pack`):

```python
CODE_RE = re.compile(r"[a-z]{2,3}(?:-[A-Za-z]{2,8})?")


def parse_blocks(spec) -> list[tuple[int, int]]:
    return [tuple(int(x, 16) for x in s.split("-")) for s in spec]


def in_blocks(c: str, blocks) -> bool:
    o = ord(c)
    return any(a <= o <= b for a, b in blocks)


def _read(p):
    return json.loads(p.read_text(encoding="utf-8"))


def load_profile(sid: str) -> dict:
    prof = _read(files("rosettex") / "langpacks" / "scripts" / f"{sid}.json")
    prof["_blocks"] = parse_blocks(prof["blocks"])
    return prof


def load_languages() -> dict:
    return _read(files("rosettex") / "langpacks" / "languages.json")


def load_pack(code: str) -> dict:
    base = files("rosettex") / "langpacks"
    default = _read(base / "default.json")
    langs = load_languages()
    valid = code != "default" and bool(CODE_RE.fullmatch(code))
    entry = langs.get(code) if valid else None
    target = code
    if entry and "alias" in entry:  # zh → zh-Hans, sr → sr-Cyrl: use the target's pack or entry
        target = entry["alias"]
        entry = langs[target]
    if valid and (base / f"{target}.json").is_file():
        pack = {k: v for k, v in default.items() if k != "fonts"}  # the default's fonts never leak into a pack
        pack.update(_read(base / f"{target}.json"))
        pack["code"], found = code, True
    else:
        pack, found = {**default, "code": code}, False
        if entry:
            prof = load_profile(entry["script"])
            del pack["fonts"]
            pack.update(script=entry["script"], name=entry["name"],
                        polyglossia=entry.get("polyglossia") or prof["polyglossia_default"],
                        min_target_ratio=prof["min_target_ratio"])
    pack["pack_found"], pack["script_known"] = found, found or entry is not None
    pack["profile"] = load_profile(pack["script"])
    pack.setdefault("fonts", pack["profile"]["fonts"])
    return pack
```
In `looks_untranslated` change only the key: `pack["min_diacritic_ratio"]` → `pack["min_target_ratio"]` (Task 4 rewrites it). In `render/latex.py:preamble`, replace `f = pack["font"]` and its `\setmainfont` line with the first entry of `pack["fonts"]` (`\setmainfont{%s}[%s]` with `f["file"]`, `f.get("options", "")`), so `vi` still prints `\setmainfont{texgyretermes}[Extension=.otf,…]` (Task 6 rewrites `preamble`).

- [ ] **Step 8: Run.** `uv run pytest -q` → all pass (slice 1 tests unchanged). **Commit** (if asked): `git add rosettex/langpacks rosettex/checks.py rosettex/render/latex.py tests/golden tests/test_scripts.py` and `git commit -m "feat: script profiles, language packs and language-to-script table"`.

---

### Task 4: Checks — per-script detector, alphabet, digits, word separators

**Files:** Modify `rosettex/checks.py`; test `tests/test_checks.py` (append).

**Interfaces:**
- Consumes: `load_pack`, `in_blocks` (Task 3).
- Produces: `looks_untranslated`, `bad_chars` per S1b §10.1–10.2; `foreign_digits(src, dst) -> list[str]`; `missing_word_breaks(dst, pack, limit=25) -> bool`; `check_item` adds the errors `"use the ASCII digits 0-9 exactly as in the source: <digits>"` and `"put U+200B between words"`.

- [ ] **Step 1: Write the failing test** (append to `tests/test_checks.py`; it already imports `load_pack`, `check_batch`, `looks_untranslated`, `bad_chars`, `SRC`, `GOOD`, `VI`; add the missing imports):

```python
import pytest

from rosettex.checks import foreign_digits, missing_word_breaks

EN = ("The proposed model improves the accuracy of the baseline by a large margin on every benchmark "
      "we evaluated, reaching 0.05 error with ⟦m1⟧ parameters as reported in ⟦c1⟧.")
GOOD_BY_LANG = {
    "fr": "Le modèle proposé améliore nettement la précision du modèle de référence sur chaque banc d'essai que nous avons évalué, avec une erreur de 0.05 pour ⟦m1⟧ paramètres, comme indiqué dans ⟦c1⟧.",
    "ru": "Предложенная модель значительно улучшает точность базовой модели на всех тестах, достигая ошибки 0.05 при ⟦m1⟧ параметрах, как сообщается в ⟦c1⟧.",
    "zh-Hans": "所提出的模型在我们评估的每个基准上都大幅提高了基线的准确率，误差达到 0.05，参数为 ⟦m1⟧，如 ⟦c1⟧ 所述。",
    "ja": "提案モデルは評価したすべてのベンチマークでベースラインの精度を大きく改善し、⟦m1⟧ 個のパラメータで誤差 0.05 に達した（⟦c1⟧）。",
    "ko": "제안된 모델은 우리가 평가한 모든 벤치마크에서 기준 모델의 정확도를 크게 향상시켜 ⟦m1⟧ 개의 매개변수로 0.05 오차에 도달했다 ⟦c1⟧.",
    "ar": "يحسن النموذج المقترح دقة النموذج الأساسي بهامش كبير في كل معيار قمنا بتقييمه، إذ يبلغ خطأ 0.05 مع ⟦m1⟧ معامل كما ورد في ⟦c1⟧.",
    "hi": "प्रस्तावित मॉडल हर बेंचमार्क पर आधार मॉडल की सटीकता में बड़ा सुधार करता है और ⟦m1⟧ मापदंडों के साथ 0.05 त्रुटि तक पहुँचता है, जैसा ⟦c1⟧ में बताया गया है।",
    "th": "แบบจำลอง\u200bที่\u200bเสนอ\u200bช่วย\u200bเพิ่ม\u200bความ\u200bแม่นยำ\u200bของ\u200bแบบจำลอง\u200bพื้นฐาน\u200bอย่าง\u200bมาก ความ\u200bคลาดเคลื่อน 0.05 ด้วย\u200bพารามิเตอร์ ⟦m1⟧ ตาม\u200bที่\u200bรายงาน\u200bใน ⟦c1⟧",
}
# Task 9 extends this table to every pack from tests/script_samples.json.


def batch_of(text):
    return {"batch": "b001", "items": [{"id": "p1b1", "type": "paragraph", "page": 1, "text": text, "placeholders": {}}]}


@pytest.mark.parametrize("code,good", sorted(GOOD_BY_LANG.items()))
def test_script_pack_accepts_good_and_rejects_english(code, good):
    pack = load_pack(code)
    assert check_batch(batch_of(EN), {"p1b1": good}, pack, None)["errors"] == {}
    assert "looks untranslated" in check_batch(batch_of(EN), {"p1b1": EN}, pack, None)["errors"]["p1b1"]
    assert looks_untranslated(EN, EN.replace("The proposed", "A proposed"), pack)


def test_alphabet():
    assert bad_chars("x", "ক্\u200cষ", load_pack("bn")) == []
    assert bad_chars("x", "परीक्षण привет", load_pack("hi")) == list("привет")


def test_foreign_digits_rejected_everywhere():
    assert foreign_digits("0.05", "٠٫٠٥ و 0.05") == ["٠", "٠", "٥"]
    assert foreign_digits("x", "１２") == ["１", "２"]
    e = check_batch(batch_of(EN), {"p1b1": GOOD_BY_LANG["ar"] + " ٣"}, load_pack("ar"), None)["errors"]["p1b1"]
    assert any("ASCII digits" in x for x in e)


def test_thai_needs_word_separators():
    th = load_pack("th")
    assert not missing_word_breaks(GOOD_BY_LANG["th"], th)
    assert missing_word_breaks(GOOD_BY_LANG["th"].replace("\u200b", ""), th)
    assert not missing_word_breaks("ไม่มี" * 10, load_pack("hi"))  # only zwsp profiles


def test_vi_detector_unchanged():
    assert looks_untranslated(SRC, SRC, VI) and not looks_untranslated(SRC, GOOD, VI)
    assert looks_untranslated(SRC, "short", VI) is False
```

- [ ] **Step 2: Run to see the failure.** `uv run pytest tests/test_checks.py -v` → FAIL (`ImportError`).

- [ ] **Step 3: Implement** in `rosettex/checks.py` (replace `looks_untranslated` and `bad_chars`):

```python
def looks_untranslated(src: str, dst: str, pack: dict, keep=()) -> bool:
    a, b = nfc(" ".join(src.split())), nfc(" ".join(dst.split()))
    rest = PH_RE.sub(" ", b)
    for k in keep:
        rest = rest.replace(k, " ")
    letters = [c for c in rest if c.isalpha()]
    if len(letters) < pack["min_len_check"]:
        return False
    if a == b:
        return True
    special, blocks = set(pack.get("letters", "")), pack["profile"]["_blocks"]
    if not special and not blocks:
        return False
    target = sum(c in special or in_blocks(c, blocks) for c in letters) / len(letters)
    fw = set(pack["function_words"])
    if not fw:
        return target < pack["min_target_ratio"]
    words = re.findall(r"\w+", rest.lower())
    function = sum(w in fw for w in words) / max(len(words), 1)
    return target < pack["min_target_ratio"] and function < pack["min_function_ratio"]


def bad_chars(src: str, dst: str, pack: dict) -> list[str]:
    allowed = set(pack.get("letters", "")) | set(src)
    blocks = pack["profile"]["_blocks"]
    has_alphabet = bool(pack.get("letters") or blocks)
    bad = []
    for c in dst:
        if c in allowed or in_blocks(c, blocks) or (c.isascii() and (c.isprintable() or c in "\n\t")):
            continue
        cat = unicodedata.category(c)
        if c == "\ufffd" or (cat.startswith("C") and c not in "\u200b\u200c\u200d"):
            bad.append(c)
        elif cat.startswith("L") and has_alphabet and not 0x0370 <= ord(c) <= 0x03FF:
            bad.append(c)
    return bad


def foreign_digits(src: str, dst: str) -> list[str]:
    return [c for c in dst if unicodedata.category(c) == "Nd" and not c.isascii() and c not in src]


def missing_word_breaks(dst: str, pack: dict, limit: int = 25) -> bool:
    if pack["profile"]["linebreak"] != "zwsp":
        return False
    blocks, run = pack["profile"]["_blocks"], 0
    for c in dst:
        if c in "\u200b \t\n":
            run = 0
        elif in_blocks(c, blocks):
            run += 1
            if run >= limit:
                return True
        else:
            run = 0
    return False
```
In `check_item`, after the number check:
```python
    fd = foreign_digits(src, dst)
    if fd:
        errs.append("use the ASCII digits 0-9 exactly as in the source: " + "".join(fd))
    if missing_word_breaks(dst, pack):
        errs.append("put U+200B between words")
```
Note: U+200B/U+200C/U+200D are format characters (category `Cf`); they are allowed so that Thai/Myanmar separators and Indic/Arabic joiners pass; `vi` text never contains them.

- [ ] **Step 4: Run.** `uv run pytest -q` → all pass. If a `GOOD_BY_LANG` sample fails a threshold, fix the sample only if it is not fluent; otherwise lower that pack's threshold and note it for Task 12.
- [ ] **Step 5: Commit** (if asked): `git commit -am "feat: per-script checks"`.

---

### Task 5: `fonts.py` — resolve, verify, stage

**Files:** Create `rosettex/fonts.py`, `tests/test_fonts.py`.

**Interfaces:**
- Consumes: `checks.load_profile`, `rosettex/fonts/MANIFEST.json` (Task 1), packs (Task 3).
- Produces:
  - `package_dir() -> Path`; `class FontMissing(Exception)` with `.script`, `.needed`, `.to_json()` → `{"font_missing": True, "script", "fonts_needed": [{"file", "license"}]}`.
  - `resolve_fonts(pack) -> {"latin": entry, "script": entry}` (S1b §6.2).
  - `fontspec(entry) -> (file, options)`: `Path=fonts/` first for `package` entries, then `options`.
  - `stage_fonts(resolved, outdir) -> list[{file, source, license}]`: copies package fonts into `outdir/fonts/`.
  - `font_error(log) -> str | None`.

- [ ] **Step 1: Write the failing test** `tests/test_fonts.py`:

```python
import shutil

import pytest

from rosettex import fonts
from rosettex.checks import load_pack
from rosettex.fonts import FontMissing, font_error, fontspec, resolve_fonts, stage_fonts


def test_package_font_resolves_and_stages(tmp_path):
    r = resolve_fonts(load_pack("ar"))
    assert r["script"]["file"] == "NotoNaskhArabic-Regular.ttf" and r["latin"]["file"] == "texgyretermes"
    f, opts = fontspec(r["script"])
    assert f == "NotoNaskhArabic-Regular.ttf" and opts.startswith("Path=fonts/")
    out = tmp_path / "Tài liệu" / "out"
    rec = stage_fonts(r, out)
    assert (out / "fonts" / f).read_bytes() == (fonts.package_dir() / f).read_bytes()
    assert {"file": f, "source": "package", "license": "OFL-1.1"} in rec
    assert stage_fonts(r, out) == rec  # idempotent


def test_bundle_font_needs_no_path():
    r = resolve_fonts(load_pack("vi"))
    assert r["script"]["file"] == "texgyretermes" and "Path=" not in fontspec(r["script"])[1]


def test_damaged_package_font_is_missing(tmp_path, monkeypatch):
    d = tmp_path / "fonts"
    shutil.copytree(fonts.package_dir(), d)
    (d / "NotoSerifBengali-Regular.ttf").write_bytes(b"broken")
    monkeypatch.setattr(fonts, "package_dir", lambda: d)
    with pytest.raises(FontMissing) as e:
        resolve_fonts(load_pack("bn"))
    j = e.value.to_json()
    assert j["font_missing"] is True and j["script"] == "bengali"
    assert j["fonts_needed"] == [{"file": "NotoSerifBengali-Regular.ttf", "license": "OFL-1.1"}]
    assert "reinstall" in str(e.value)


def test_font_error_parsing():
    assert font_error('Package fontspec Error: The font "NotoSerif-Bold.ttf" cannot be\nfound.') == "NotoSerif-Bold.ttf"
    assert font_error("all fine") is None
```

- [ ] **Step 2: Run to see the failure.** `uv run pytest tests/test_fonts.py -v` → FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Write `rosettex/fonts.py`:**

```python
"""Fonts by file name (S1b §6): TeX bundle fonts, or fonts packaged in rosettex/fonts copied into out/fonts
(Tectonic reads no absolute paths)."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
from importlib.resources import files
from pathlib import Path

from .checks import load_profile


def package_dir() -> Path:
    return Path(str(files("rosettex") / "fonts"))


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _manifest() -> dict:
    m = json.loads((package_dir() / "MANIFEST.json").read_text(encoding="utf-8"))
    return {e["file"]: e for e in m["fonts"]}


class FontMissing(Exception):
    def __init__(self, script: str, needed: list[dict], message: str | None = None):
        names = ", ".join(n["file"] for n in needed)
        super().__init__(message or f"font file(s) {names} for the {script} script are missing or damaged in the "
                                    f"rosettex installation; reinstall rosettex")
        self.script, self.needed = script, needed

    def to_json(self) -> dict:
        return {"font_missing": True, "script": self.script,
                "fonts_needed": [{"file": n["file"], "license": n.get("license")} for n in self.needed]}


def _usable(e: dict) -> bool:
    if e["source"] == "bundle":
        return True
    m, p = _manifest().get(e["file"]), package_dir() / e["file"]
    return bool(m) and p.is_file() and _sha(p) == m["sha256"]


def resolve_fonts(pack: dict) -> dict:
    latin = load_profile("latin")["fonts"][0]
    for e in pack["fonts"]:
        if _usable(e):
            return {"latin": latin, "script": e}
    raise FontMissing(pack["profile"]["id"], pack["fonts"])


def fontspec(e: dict) -> tuple[str, str]:
    opts = (["Path=fonts/"] if e["source"] == "package" else []) + ([e["options"]] if e.get("options") else [])
    return e["file"], ",".join(opts)


def stage_fonts(resolved: dict, outdir: Path) -> list[dict]:
    rec = []
    for e in (resolved["latin"], resolved["script"]):
        if e["source"] == "package":
            src, dst = package_dir() / e["file"], Path(outdir) / "fonts" / e["file"]
            dst.parent.mkdir(parents=True, exist_ok=True)
            if not (dst.is_file() and dst.stat().st_size == src.stat().st_size and _sha(dst) == _sha(src)):
                shutil.copyfile(src, dst)
        item = {"file": e["file"], "source": e["source"], "license": e.get("license", "unknown")}
        if item not in rec:
            rec.append(item)
    return rec


def font_error(log: str) -> str | None:
    m = re.search(r'The font "([^"]+)" cannot be\s+found', log)
    return m.group(1) if m else None
```
For `plain` layouts `latin` and `script` can be the same entry (`vi`); `stage_fonts` deduplicates.

- [ ] **Step 4: Run.** `uv run pytest -q` → all pass. **Commit** (if asked): `git add rosettex/fonts.py tests/test_fonts.py` and `git commit -m "feat: font resolution, verification and staging"`.

---

### Task 6: `wrap_runs`, preamble per layout, Renderer

**Files:** Modify `rosettex/tex.py`, `rosettex/render/latex.py`; test `tests/test_scripts.py` (append).

**Interfaces:**
- Consumes: Task 2 templates (exact lines and order), `fonts.resolve_fonts`, `fonts.fontspec`, packs.
- Produces:
  - `tex.wrap_runs(s, prof) -> str` (S1b §7.3).
  - `latex.lang_lines(pack) -> (main_font_lines, language_lines)`, `latex.label_lines(pack)`; `preamble(doc, pack, twocolumn=None)` per S1b §7.1 (raises `FontMissing`).
  - `Renderer`: translated text and labels through `wrap_runs`; in `rtl`, non-math placeholder output in `\rxltr{}` and the bibliography in `otherlanguage{english}`; tables stay inside the RTL flow (mirrored).

- [ ] **Step 1: Write the failing test** (append to `tests/test_scripts.py`):

```python
import re

from rosettex.tex import wrap_runs

BN, AR, TH = (load_pack(c)["profile"] for c in ("bn", "ar", "th"))


def test_runs_keep_digits_and_ascii_punctuation_outside():
    assert wrap_runs("এটি 0.05 মান।", BN) == r"\rxs{এটি} 0.05 \rxs{মান।}"
    assert wrap_runs("ক্\u200cষ, ক", BN) == "\\rxs{ক্\u200cষ}, \\rxs{ক}"
    assert wrap_runs("এটি\n\nমান", BN) == "\\rxs{এটি}\n\n\\rxs{মান}"
    assert wrap_runs("50% এটি_", BN) == r"50\% \rxs{এটি}\_"


def test_zwsp_becomes_a_break_point():
    assert wrap_runs("ภาษา\u200bไทย 0.05", TH) == r"\rxs{ภาษา\hspace{0pt plus 1pt}ไทย} 0.05"


def test_rtl_wraps_latin_and_digits_not_parentheses():
    assert wrap_runs("يحقق 28.4 BLEU (تقريبا) و 5%", AR) == r"يحقق \rxltr{28.4 BLEU} (تقريبا) و \rxltr{5\%}"
    assert wrap_runs("2 المقدمة", AR) == r"\rxltr{2} المقدمة"


def test_plain_profile_is_plain_escape():
    assert wrap_runs("a_b 0.05", load_pack("vi")["profile"]) == r"a\_b 0.05"


def doc_and_tr(sample):
    d = FIX / "mineru-4.2.3-standard"
    doc, _ = segment(adapt([(json.loads((d / "short.middle.json").read_text(encoding="utf-8")), d)], d))
    return doc, {u["id"]: sample + " " + " ".join(re.findall(r"⟦[a-z]\d+⟧", u["src"])) for u in doc["units"]}


@pytest.mark.parametrize("code,needle", [
    ("ru", r"\setdefaultlanguage{russian}"),
    ("ja", r"\setCJKmainfont{NotoSerifJP-Regular.otf}[Path=fonts/]"),
    ("hi", r"\setotherlanguage{hindi}"),
    ("gu", r"\newfontfamily\rxscriptfont{NotoSerifGujarati-Regular.ttf}[Path=fonts/,Script=Gujarati]"),
    ("ar", r"\setdefaultlanguage[numerals=maghrib]{arabic}"),
])
def test_preamble_per_layout(code, needle):
    doc, tr = doc_and_tr("x")
    tex = Renderer(doc, tr, load_pack(code), set(), {}).document()
    assert needle in tex
    if load_pack(code)["profile"]["layout"] != "plain":
        assert r"\setmainfont{texgyretermes}" in tex and r"\renewcommand{\figurename}" in tex
    if code == "ar":
        assert tex.index(r"{hyperref}") < tex.index(r"\usepackage{polyglossia}")


def test_arabic_body():
    doc, tr = doc_and_tr("نص عربي")
    tex = Renderer(doc, tr, load_pack("ar"), set(), {}).document()
    assert r"\rxltr{\cite{" in tex and r"\rxltr{$" not in tex
    assert r"\begin{otherlanguage}{english}" in tex and r"\begin{LTR}" not in tex
```
(The `ar` `numerals` value is the one Task 2 recorded; adjust this test's needle if Task 2 chose another.)

- [ ] **Step 2: Run to see the failure.** `uv run pytest tests/test_scripts.py -v` → FAIL (`ImportError: wrap_runs`).

- [ ] **Step 3: Add `wrap_runs` to `rosettex/tex.py`:**

```python
import re

_LATIN = "0-9A-Za-z\u00C0-\u024F"


def _cls(blocks) -> str:
    return "".join(f"\\U{a:08X}-\\U{b:08X}" for a, b in blocks)


def wrap_runs(s: str, prof: dict) -> str:
    """Escape s. 'runs': target-script runs → \\rxs{}; 'rtl': Latin/digit runs → \\rxltr{}; 'zwsp': U+200B → break point."""
    mode, blocks = prof["layout"], prof["_blocks"]
    if mode not in ("runs", "rtl") or not blocks:
        return escape_latex(s)
    c = _cls(blocks)
    if mode == "runs":
        pat, macro = re.compile(f"[{c}]+(?:[ \\t]+[{c}]+)*"), "\\rxs"
    else:  # interior limited to letters, digits, spaces and . , : / - _ so parentheses stay in the RTL flow
        pat, macro = re.compile(f"[{_LATIN}](?:[{_LATIN} .,:/_\\-]*[{_LATIN}])?%?"), "\\rxltr"
    zw = prof["linebreak"] == "zwsp"

    def run(t: str) -> str:
        e = escape_latex(t)
        return e.replace("\u200b", "\\hspace{0pt plus 1pt}") if zw else e

    out, pos = [], 0
    for m in pat.finditer(s):
        out += [escape_latex(s[pos:m.start()]), macro + "{" + run(m.group()) + "}"]
        pos = m.end()
    out.append(escape_latex(s[pos:]))
    return "".join(out)
```

- [ ] **Step 4: Rewrite `preamble` in `rosettex/render/latex.py`.** Keep the geometry code and every slice 1 line in the same order; replace the `\setmainfont` line with `main` and the two polyglossia lines with `lang` for `plain`/`cjk`/`runs`; for `rtl` emit `lang` right after the `hyperref` line instead. Then `\begin{document}`, `*label_lines(pack)`, `""`. If Task 2 decided "one column for RTL", set `two = False` for layout `rtl`.

```python
from ..fonts import fontspec, resolve_fonts
from ..tex import escape_latex, wrap_runs


def lang_lines(pack) -> tuple[list[str], list[str]]:
    prof, fonts = pack["profile"], resolve_fonts(pack)
    lay, poly = prof["layout"], pack.get("polyglossia")
    if lay == "plain":
        f, o = fontspec(fonts["script"])
        return ([r"\setmainfont{%s}[%s]" % (f, o)],
                [r"\usepackage{polyglossia}", r"\setdefaultlanguage{%s}" % poly] if poly else [])
    main = [r"\setmainfont{%s}[%s]" % fontspec(fonts["latin"])]
    f, o = fontspec(fonts["script"])
    o = ",".join(x for x in (o, "Script=" + prof["otf_script"] if prof["otf_script"] else "") if x)
    if lay == "cjk":
        opt = "[%s]" % prof["layout_options"] if prof["layout_options"] else ""
        return main, [r"\usepackage%s{xeCJK}" % opt, r"\setCJKmainfont{%s}[%s]" % (f, o)]
    if lay == "rtl":
        po = pack.get("polyglossia_options")
        return main, [r"\usepackage{polyglossia}", r"\setdefaultlanguage%s{%s}" % ("[%s]" % po if po else "", poly),
                      r"\setotherlanguage{english}", r"\newfontfamily\%sfont{%s}[%s]" % (poly, f, o),
                      r"\newcommand{\rxltr}[1]{\textenglish{#1}}"]
    if poly:
        return main, [r"\usepackage{polyglossia}", r"\setdefaultlanguage{english}",
                      r"\newfontfamily\%sfont{%s}[%s]" % (poly, f, o), r"\setotherlanguage{%s}" % poly,
                      r"\newcommand{\rxs}[1]{\text%s{#1}}" % poly]
    return main, [r"\newfontfamily\rxscriptfont{%s}[%s]" % (f, o), r"\newcommand{\rxs}[1]{{\rxscriptfont #1}}"]


def label_lines(pack) -> list[str]:
    lab, prof = pack["labels"], pack["profile"]
    names = (("figure", "figurename"), ("table", "tablename"), ("references", "refname"))
    return [r"\renewcommand{\%s}{%s}" % (m, wrap_runs(lab[k], prof)) for k, m in names if k in lab]
```

- [ ] **Step 5: Renderer.** In `__init__`: `self.prof = pack["profile"]; self.rtl = self.prof["layout"] == "rtl"`. In `text()`: every `escape_latex(...)` on translated text (the three calls, including the last-resort fallback path) becomes `wrap_runs(..., self.prof)`; a placeholder's `p["tex"]` becomes `"\\rxltr{%s}" % p["tex"]` when `self.rtl` and the tex does not start with `$`. In `footnote()`: the mark `\textsuperscript{…}` is wrapped in `\rxltr{}` when `self.rtl`. In `bibliography()`, when `self.rtl` and the pack has `labels.references`: `"\\begin{otherlanguage}{english}\\renewcommand{\\refname}{%s}\n%s\n\\end{otherlanguage}" % (wrap_runs(label, self.prof), <the thebibliography environment>)`. Tables are unchanged (they stay in the RTL flow and are mirrored, S1b §8). `verbatim()` and `box_*` are unchanged.
- [ ] **Step 6: Run.** `uv run pytest -q` → all pass, including the golden `vi` test (on failure, diff against `tests/golden/vi_short_main.tex` and fix the code, never the golden file).
- [ ] **Step 7: Commit** (if asked): `git commit -am "feat: preamble per layout and run wrapping"`.

---

### Task 7: `compile.py` and `postcheck.py` — fonts, font errors, post-check

**Files:** Modify `rosettex/render/compile.py`, `rosettex/render/postcheck.py`; test `tests/test_render.py` (append).

**Interfaces:**
- Consumes: `fonts.resolve_fonts`, `stage_fonts`, `font_error`, `FontMissing` (Task 5).
- Produces: `render_core` resolves and stages fonts before the probe (`FontMissing` propagates); after a failed compile, `font_error(output + log)` → `FontMissing` (before `culprit`); `report.json` gains `script` and `fonts`. `postcheck` gains `missing_glyphs`, per-font problems, dotted circle (not for `rtl`), overfull lines (`cjk`/`zwsp`), `max_length_ratio`. Module-level `parse_missing(log)`, `overfull_count(log, min_pt=10.0)`.

- [ ] **Step 1: Write the failing test** (append to `tests/test_render.py`):

```python
from rosettex import fonts
from rosettex.fonts import FontMissing
from rosettex.render.postcheck import overfull_count, parse_missing

LOG = ("Missing character: There is no ০ (U+09E6) in font NotoSerifBengali-Regular.ttf:mode=harf;script=beng;!\n"
       "Missing character: There is no x in font foo!\n"
       "Overfull \\hbox (12.5pt too wide) in paragraph at lines 3--4\n"
       "Overfull \\hbox (2.0pt too wide) in paragraph at lines 5--6\n")


def test_parse_missing_and_overfull():
    m = parse_missing(LOG)
    assert m[0] == {"char": "০", "codepoint": "U+09E6", "font": "NotoSerifBengali-Regular.ttf"}
    assert m[1]["char"] == "x" and m[1]["font"] == "foo"
    assert overfull_count(LOG) == 1


def test_render_stops_on_damaged_package_font(tmp_path, monkeypatch):
    d = tmp_path / "fonts"
    shutil.copytree(fonts.package_dir(), d)
    (d / "NotoSerifBengali-Regular.ttf").write_bytes(b"broken")
    monkeypatch.setattr(fonts, "package_dir", lambda: d)
    w = make_work(tmp_path, lang="bn")
    translate_all(w)
    with pytest.raises(FontMissing):
        render_core(w, tmp_path / "o.pdf")
    assert not (tmp_path / "o.pdf").exists()
```
(Add `import shutil` if missing. `translate_all` writes Vietnamese fake text; the error happens before compiling.)

- [ ] **Step 2: Run to see the failure.** `uv run pytest tests/test_render.py -v` → FAIL (`ImportError: parse_missing`).
- [ ] **Step 3: `postcheck.py`.**

```python
MISS_RE = re.compile(r"Missing character: There is no (.)(?: \(U\+([0-9A-F]+)\))? in font ([^!\n]*)!")
OVER_RE = re.compile(r"Overfull \\hbox \((\d+(?:\.\d+)?)pt too wide\)")


def parse_missing(log: str) -> list[dict]:
    out = []
    for ch, cp, font in MISS_RE.findall(log):
        item = {"char": ch, "codepoint": "U+" + (cp or f"{ord(ch):04X}"), "font": font.split(":")[0].strip("[] ")}
        if item not in out:
            out.append(item)
    return out


def overfull_count(log: str, min_pt: float = 10.0) -> int:
    return sum(float(x) > min_pt for x in OVER_RE.findall(log))
```
In `postcheck`: replace the slice 1 "Missing character" block with one problem per font, `f"font {font} is missing characters: {chars}"` (keeps the substring "is missing characters"); add `"dotted circle in the PDF"` when `"\u25cc" in text`, not in any translation, and the layout is not `rtl`; add `f"{n} overfull lines: line breaking for this script may not work"` when `pack["profile"]["linebreak"] in ("cjk", "zwsp")` and `n = overfull_count(log) > 5`; use `pack.get("max_length_ratio", 1.3)` for the length threshold and in its message; return `missing_glyphs` too.
- [ ] **Step 4: `compile.py`.** In `render_core`, after `pack = load_pack(lang)`: `fonts_res = resolve_fonts(pack)` (lets `FontMissing` propagate) and, after `work.out.mkdir`, `font_rec = stage_fonts(fonts_res, work.out)`. In both compile loops, before `culprit(log)`: `bad = font_error(output + log)`; if `bad`: `raise FontMissing(pack["profile"]["id"], [e for e in pack["fonts"] if e["file"] == bad] or [{"file": bad, "license": None}], message=f"TeX could not load the font {bad}: with XeLaTeX install the TeX package that provides it; with Tectonic rerun when the network works")`. Add `"script": pack["profile"]["id"], "fonts": font_rec` to the report.
- [ ] **Step 5: Run.** `uv run pytest -q` → all pass (existing render tests with `vi` included). **Commit** (if asked): `git commit -am "feat: font staging, font errors and missing-glyph post-check"`.

---

### Task 8: CLI — `script`, `pack_found`, `script_known`, `fonts`; fail early

**Files:** Modify `rosettex/cli.py`; test `tests/test_cli.py` (append).

**Interfaces:**
- Consumes: `load_pack`, `fonts.resolve_fonts`, `FontMissing.to_json()`.
- Produces:
  - `extract`: after validating the code and the PDF, **before** looking for MinerU, `resolve_fonts(load_pack(a.lang))`; `FontMissing` → `UsageError(str(e), next_action=None, **e.to_json())` → exit 2. Success JSON gains `script`, `pack_found`, `script_known`, `fonts` (file names).
  - `status`: the same four fields, read from the manifest's `lang` (read-only, as slice 1); a `FontMissing` there is reported as `font_missing: true` with `fonts_needed`, never raised.
  - `render` (parent, before spawning): the same font check and JSON. In the foreground child, `FontMissing` is caught like `RenderError` (state `failed`, error text).

- [ ] **Step 1: Write the failing test** (append to `tests/test_cli.py`; `run` and `env` exist there; `main` runs in-process, so monkeypatch works):

```python
import shutil

from rosettex import fonts


def test_extract_fails_early_on_damaged_font(env, capsys, tmp_path, monkeypatch):
    d = tmp_path / "fonts"
    shutil.copytree(fonts.package_dir(), d)
    (d / "NotoSerifBengali-Regular.ttf").write_bytes(b"broken")
    monkeypatch.setattr(fonts, "package_dir", lambda: d)
    monkeypatch.setenv("ROSETTEX_MINERU", str(tmp_path / "does-not-exist"))  # must not be reached
    code, r = run(capsys, "extract", FIX / "papers" / "short.pdf", "--lang", "bn", "-o", tmp_path / "o", "--json")
    assert code == 2 and r["font_missing"] is True and r["script"] == "bengali" and r["next_action"] is None
    assert not (tmp_path / "o").exists()


@pytest.mark.parametrize("lang,script,found,known", [("ar", "arabic", True, True), ("ps", "arabic", False, True),
                                                     ("es", "latin", False, True), ("xx", "latin", False, False)])
def test_extract_reports_script(env, capsys, tmp_path, lang, script, found, known):
    code, r = run(capsys, "extract", FIX / "papers" / "short.pdf", "--lang", lang, "-o", tmp_path / "o",
                  "--foreground", "--json")
    assert code == 0 and r["script"] == script and r["pack_found"] is found and r["script_known"] is known
    assert r["lang_tested"] is False and r["fonts"]
```
(Check the exact flags of the existing successful `extract` test in `tests/test_cli.py` — `-o`/`--out`, `--foreground` — and use the same; do not modify that test.)

- [ ] **Step 2: Run to see the failure.** `uv run pytest tests/test_cli.py -v` → FAIL.
- [ ] **Step 3: Implement** in `cmd_extract`, `cmd_status`, `cmd_render`; add `FontMissing` next to `ExtractError`/`RenderError` where `main` maps exceptions, emitting `**e.to_json()`.
- [ ] **Step 4: Run.** `uv run pytest -q` → all pass. **Commit** (if asked): `git commit -am "feat: CLI reports script and fails early on damaged fonts"`.

---

### Task 9: Sample texts and a PDF build test for every pack

**Files:** Create `tests/script_samples.json`; modify `tests/conftest.py` (`fake_script`, `translate_all(work, translator=fake_vi)`), `tests/test_render.py` (append), `tests/test_checks.py` (extend `GOOD_BY_LANG` from the samples).

**Interfaces:**
- Consumes: everything above; Task 2's `xfail` list.

- [ ] **Step 1: Samples.** `tests/script_samples.json`: `{"<code>": "<one or two sentences>"}` for all 21 packs, in the target language, ≥ 40 letters, no digits (the fake translator adds the source's numbers), `th`/`my` with U+200B between words. Start from the sentences in `docs/superpowers/notes/2026-11-18-slice1b-spike/probe.py` and `GOOD_BY_LANG` (Task 4). Each must pass `looks_untranslated(...) is False` and `bad_chars(...) == []` for its pack.
- [ ] **Step 2: Fake translator** in `tests/conftest.py`:

```python
def fake_script(src: str, sample: str) -> str:
    """Keep every placeholder and every number of the source; the words come from a sample in the target script."""
    keep = re.findall(r"⟦[a-z]\d+⟧|\d+(?:[.,]\d+)*", src)
    return sample + (" " + " ".join(keep) if keep else "")
```
and `translate_all(work, translator=fake_vi)` (default unchanged).
- [ ] **Step 3: Build test** (append to `tests/test_render.py`):

```python
SAMPLES = json.loads((Path(__file__).parent / "script_samples.json").read_text(encoding="utf-8"))
XFAIL = {}  # from Task 2 stop rules, e.g. {"ur": "<error line>"}


@needs_tex
@pytest.mark.parametrize("code", sorted(SAMPLES))
def test_render_short_in_every_script(tmp_path, code):
    from conftest import fake_script
    from rosettex.checks import in_blocks, load_pack
    if code in XFAIL:
        pytest.xfail(XFAIL[code])
    pack = load_pack(code)
    w = make_work(tmp_path, lang=code)
    translate_all(w, lambda s: fake_script(s, SAMPLES[code]))
    rep = render_core(w, tmp_path / f"out.{code}.pdf")
    pc = rep["postcheck"]
    assert pc["missing_glyphs"] == [], pc
    assert not [p for p in pc["problems"] if "dotted circle" in p or "undefined" in p], pc
    if pack["profile"]["layout"] != "rtl":  # Arabic text extraction is unreliable (S1b §3.1)
        text = full_text(tmp_path / f"out.{code}.pdf")
        assert pack["profile"]["layout"] == "plain" or any(in_blocks(c, pack["profile"]["_blocks"]) for c in text)
        assert "0.05" in text or "28.4" in text
    assert rep["script"] == pack["profile"]["id"] and rep["fonts"]
```
Length and overfull problems are not asserted here (fake text); they are measured in Task 12.
- [ ] **Step 4: Run.** `uv run pytest tests/test_render.py -k every_script -v` → every pack passes or xfails with a recorded reason; then `uv run pytest -q`. Keep `--basetemp` output: Task 12 reuses the PDFs.
- [ ] **Step 5: Commit** (if asked): `git add tests/script_samples.json tests/conftest.py tests/test_render.py tests/test_checks.py` and `git commit -m "test: build every language pack"`.

---

### Task 10: Protocol, regenerated `SKILL.md`/`AGENTS.md`, README

**Files:** Modify `rosettex/protocol/protocol.md`, `README.md`; regenerate `skill/SKILL.md`, `skill/AGENTS.md` (never by hand); test `tests/test_protocol.py` (append).

- [ ] **Step 1: Write the failing test** (append to `tests/test_protocol.py`; `text` is the existing helper that renders the protocol):

```python
def test_protocol_covers_scripts_and_fonts():
    t = text("vi")
    for k in ("BCP-47", "script_known", "pack_found", "font_missing", "missing_glyphs", "lang_tested", "sr-Latn"):
        assert k in t
```
- [ ] **Step 2: Run to see the failure.** `uv run pytest tests/test_protocol.py -v` → FAIL.
- [ ] **Step 3: Edit `protocol.md`** per S1b §13:
  - Before step 1: "The user names the target language in words. Use its BCP-47 code (`fr`, `zh-Hans`, `fa`); for a language written in several scripts add the script subtag (`sr-Latn`, `sr-Cyrl`). Ask the user only if the language itself is unclear."
  - Step 1, after the `lang_tested` line: "Tell the user the language, the code and the `script` from the result (for example: Persian → `fa` → Arabic script). `script_known: false`: ask the user which writing system the language uses, then stop. `pack_found: false`: tell the user 'there is no language pack for <code>; the checks are generic for the <script> script'. `font_missing: true`: tell the user that rosettex is damaged and must be reinstalled, with the files in `fonts_needed`; stop."
  - Step 5: "`font_missing: true`: same as in step 1." Step 6: "`postcheck.missing_glyphs`: tell the user which characters could not be displayed and in which font."
  - Keep every slice 1 rule (single commands, no pipes).
- [ ] **Step 4: Regenerate.** `uv run python -m rosettex.protocol.build`; `git diff --stat skill/` shows both files changed.
- [ ] **Step 5: README.** A "Languages and fonts" section: the 21 packs and their scripts (S1b §5.4), how other codes map (`languages.json`), the packaged fonts with licences (from `rosettex/fonts/MANIFEST.json`), "every non-Latin font ships with rosettex; no font install is needed", and "Arabic text copied out of the PDF can be garbled (a limitation of XeTeX), the printed text is correct".
- [ ] **Step 6: Run.** `uv run pytest -q` (including `test_generated_files_in_sync`). **Commit** (if asked): `git add rosettex/protocol/protocol.md skill README.md tests/test_protocol.py` and `git commit -m "docs: protocol for scripts and fonts"`.

---

### Task 11: CI check (no new jobs)

**Files:** Modify none, unless Step 2 finds a problem.

- [ ] **Step 1:** Confirm the build test does not depend on the dev machine: run `uv run pytest tests/test_render.py -k every_script -v` with `HOME` pointing at an empty temporary directory except `~/.rosettex/bin/tectonic` (copy it) so the Tectonic cache is cold. Expected: all pass (Tectonic downloads the bundle; rerun once on a network error).
- [ ] **Step 2:** Confirm `.github/workflows/ci.yml` needs no change. If the 21 builds make a CI job slower than 10 minutes, mark `test_render_short_in_every_script` opt-in on CI (`ROSETTEX_SCRIPTS=1`) and note it in the acceptance record. The wheel grows by 16.5 MB; record the wheel size.
- [ ] **Step 3: Commit** (if asked and if anything changed).

---

### Task 12: Slice 1b acceptance (real build, real agent, reader check)

**Files:** Create `docs/superpowers/notes/2026-11-18-slice1b-acceptance.md`.

- [ ] **Step 1: Sample PDFs for the reader.** The PDFs of Task 9 (rerun with `--basetemp <dir>` if needed) and a PNG of page 1 of each (`rosettex.pdf.render_page_png`). Put the paths in the record.
- [ ] **Step 2: Real agent, three languages** (S1b §1 item 4). In a fresh Claude Code session (or a subagent, stating which): "Translate the paper /home/admin/Desktop/Learn/rosettex/tests/fixtures/papers/short.pdf into Arabic", then Simplified Chinese (or Japanese), then Hindi — each named in words, not by code. Record: the code and script the agent reported, questions asked (expected none), whether `lang_tested: false` was reported, PDF produced, `postcheck`, rejected batches on first submission (command of slice 1 Task 15 Step 4), `length_ratio`, overfull lines. Then one unknown language ("translate into Klingon"): record that the agent asks for the writing system and stops.
- [ ] **Step 3: Thresholds.** From the real translations: the target ratio per paragraph (minimum) and `length_ratio`. Lower a pack's `min_target_ratio` if a correct paragraph would be rejected; set `max_length_ratio` if the post-check length fails on a correct build; tune the U+200B run limit for Thai if needed. Record each change.
- [ ] **Step 4: Reader check list.** A table: pack → sample PDF → what the reader judges (shaping, direction and column order, table direction, line breaking, labels, numbers) → reader → verdict. Leave verdicts empty for the user. **Do not set any `tested: true`.**
- [ ] **Step 5: Open items**: everything marked "not tested" by Task 2 (XeLaTeX, macOS, Windows, failing cases), each with the slice that will handle it.
- [ ] **Step 6: Commit** (if asked): `git add docs/superpowers/notes/2026-11-18-slice1b-acceptance.md` and `git commit -m "docs: slice 1b acceptance record"`.

---

### Task 13: Update CLAUDE.md Status and SG

**Files:** Modify `CLAUDE.md`, `docs/superpowers/specs/2026-11-18-rosettex-design.md`.

- [ ] **Step 1: `CLAUDE.md` Status:** slice 1b done (or the parts done), the test count, pointers to the slice 1b spec, plan and acceptance record, the packs with their `tested` state, the open items of Task 12. Make "Slice 2: review" next. Add to "Verified facts": Tectonic reads fonts only from the bundle or relative paths (`Path=fonts/`); Termes has no Cyrillic; no ICU line breaking in Tectonic (U+200B rule); polyglossia Arabic needs `numerals=maghrib` (or the Task 2 value) for ASCII digits; Arabic PDF text extraction is unreliable. Add to "Commands": `uv run --with fonttools==4.68.0 python scripts/build_fonts.py --cache <dir>`.
- [ ] **Step 2: SG §4.1:** a short per-script result table (from `results.md`). **SG §11.5 rule 1:** correct "TeX Gyre Termes covers only Latin, Cyrillic and Greek" to "TeX Gyre Termes covers Latin; Cyrillic uses Noto Serif" (a factual correction; the packaging sentence was already amended on 2026-11-18). **SG §16 item 8:** status line.
- [ ] **Step 3: SG §19:** no settled decision changed in this slice; add nothing unless Tasks 1–12 forced a change (then use the existing "~~old~~ → **amended <date> per the user:**" style).
- [ ] **Step 4: Run** `uv run pytest -q` and **commit** (if asked): `git commit -am "docs: slice 1b status"`.

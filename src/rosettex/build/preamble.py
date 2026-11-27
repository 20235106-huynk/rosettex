import re
import unicodedata

from rosettex.langdata import load_coverage

# layout=counters dropped (P2-fix2): it redefines \@arabic and breaks revtex4 \maketitle and jmlr2e (T5);
# bidi=basic already keeps "2.1" as one LTR number run.
_RTL_BABEL_OPTS = "bidi=basic,layout=sectioning.lists.contents.footnotes.captions.columns.tabular"
# TU builds \c{c} as `\iffontchar\font"E7 ç\else c+U+0327`; with onchar=ids the current font is the target
# script font (no ç), so bibliographies get c+U+0327, which Latin Modern lacks (GPT-3, Llama 2; Indic, Arabic, CJK). Precompose.
_PRECOMPOSED = {
    "c": "cCsStTgGkKlLnNrR",  # cedilla
    "k": "aAeEiIuU",  # ogonek
}
_CJK_SCRIPTS = {"han", "kana", "hangul"}
_KR = "NotoSerifKR-Regular.otf"
_ALT_CJK_SPACE = "HaranoAjiMincho-Regular.otf"
_FAKE_SLANT = "0.17"
_HARFBUZZ_SCRIPTS = {
    "arabic",
    "devanagari",
    "bengali",
    "tamil",
    "telugu",
    "gujarati",
    "kannada",
    "thai",
    "myanmar",
    "ethiopic",
}
_POLYGLOSSIA_FONT_CMD = {
    "arabic": "arabic",
    "devanagari": "devanagari",
    "bengali": "bengali",
    "tamil": "tamil",
    "telugu": "telugu",
    "gujarati": "gujarati",
    "kannada": "kannada",
    "thai": "thai",
    "ethiopic": "ethiopic",
}
_COMMON_FALLBACK_CHARS = "0123456789.,/:;-+%=()[]!?•–—‘’“”…†‡§¶°±×‖⁄"
_LOAD_FONTSPEC = "\\renewcommand{\\familydefault}{\\rmdefault}\\ifdefined\\setmainfont\\else\\usepackage[no-math]{fontspec}\\fi"
# Override any paper font-family choice (\renewcommand{\familydefault}{ppl}, IEEEtran, times, ...).
_RESET_FAMILY = "\\renewcommand{\\familydefault}{\\rmdefault}\\normalfont"
# Under Unicode engines with [no-math]{fontspec}, stripping inputenc leaves literal U+00A0 (NBSP) as
# mathord in 7-bit OT1 cmr (no slot 0xA0: 2001.02586); luatexja AltFont / xeCJK sub-blocks do not
# intercept U+3000 (ideographic space) in math mode (2102.10993). Make them math-active only.
_MATH_NBSP = (
    "\\AtBeginDocument{\\begingroup\\lccode`\\~=\"00A0"
    "\\lowercase{\\endgroup\\providecommand{~}{\\nobreakspace}}\\mathcode\"00A0=\"8000\\relax}"
)
_MATH_IDEO_SPACE = (
    "\\AtBeginDocument{\\begingroup\\lccode`\\~=\"3000"
    "\\lowercase{\\endgroup\\providecommand{~}{\\quad}}\\mathcode\"3000=\"8000\\relax}"
)
_BEGIN = re.compile(r"^[^%\n]*?(\\begin\s*\{document\})", re.M)
_MISSING_CHAR_LOG = re.compile(r"Missing character: There is no (.+?)(?: \(U\+([0-9A-Fa-f]+)\))? in font (.+?)!$")
# Ordered fallback chain for LuaLaTeX text-mode missing glyphs (only fonts that cover an actual
# missing codepoint are loaded): guaranteed TinyTeX fonts first, then bundled data/fonts/.
_TL_FALLBACK_FONTS = (
    "NotoSerif-Regular.ttf",
    "cmunrm.otf",
    "NotoSansMono-Regular.ttf",
)
_BUNDLED_FALLBACK_FONTS = (
    "NotoSerifSC-Regular.otf",
    "NotoSerifJP-Regular.otf",
    "NotoSerifTC-Regular.otf",
    "NotoSerifKR-Regular.otf",
    "Vazirmatn-Regular.ttf",
    "NotoSerifDevanagari-Regular.ttf",
    "NotoSerifBengali-Regular.ttf",
    "NotoSerifTamil-Regular.ttf",
    "NotoSerifTelugu-Regular.ttf",
    "NotoSerifGujarati-Regular.ttf",
    "NotoSerifKannada-Regular.ttf",
    "NotoSerifThai-Regular.ttf",
    "NotoSerifMyanmar-Regular.ttf",
    "NotoSerifEthiopic-Regular.ttf",
)
_BUNDLED_HARF_FONTS = frozenset(_BUNDLED_FALLBACK_FONTS[4:])
_FALLBACK_ORDER = _TL_FALLBACK_FONTS + _BUNDLED_FALLBACK_FONTS
_NOTO_TL_FONTS = (
    "NotoSerif-Regular.ttf",
    "NotoSans-Regular.ttf",
    "NotoSansMono-Regular.ttf",
)
_NOTO_SPECS = (
    (
        "main",
        "rm",
        "{NotoSerif-Regular.ttf}[BoldFont=NotoSerif-Bold.ttf,"
        "ItalicFont=NotoSerif-Italic.ttf,BoldItalicFont=NotoSerif-BoldItalic.ttf]",
    ),
    (
        "sans",
        "sf",
        "{NotoSans-Regular.ttf}[BoldFont=NotoSans-Bold.ttf,"
        "ItalicFont=NotoSans-Italic.ttf,BoldItalicFont=NotoSans-BoldItalic.ttf]",
    ),
    (
        "mono",
        "tt",
        "{NotoSansMono-Regular.ttf}[BoldFont=NotoSansMono-Bold.ttf,"
        f"ItalicFont=NotoSansMono-Regular.ttf,ItalicFeatures={{FakeSlant={_FAKE_SLANT}}},"
        f"BoldItalicFont=NotoSansMono-Bold.ttf,BoldItalicFeatures={{FakeSlant={_FAKE_SLANT}}}]",
    ),
)
# Fonts rosettex itself declares via fontspec/luatexja/xeCJK/kotex (single source of truth for postcheck).
ROSETTEX_FONTS: frozenset[str] = frozenset(
    name.lower()
    for raw in (*_BUNDLED_FALLBACK_FONTS, *_NOTO_TL_FONTS, _ALT_CJK_SPACE)
    for name in (raw, re.sub(r"\.(?:otf|ttf)$", "", raw, flags=re.I))
)
_FONT_INST_SUFFIX = re.compile(r"\(\d+\)$")


def is_rosettex_font(nfss_family: str) -> bool:
    """True when an NFSS family name (e.g. 'NotoSerifSC-Regular.otf(0)') is a font declared by rosettex."""
    base = _FONT_INST_SUFFIX.sub("", nfss_family.strip()).lower()
    return base in ROSETTEX_FONTS or re.sub(r"\.(?:otf|ttf)$", "", base) in ROSETTEX_FONTS


def is_otf_font(font_name: str) -> bool:
    """True when a TeX log font name refers to a Unicode OpenType/TrueType font (not 7-bit TFM / nullfont)."""
    low = font_name.lower()
    return "[" in font_name or ":" in font_name or ".otf" in low or ".ttf" in low


def needed_fallback_fonts(log_text: str) -> list[str]:
    """Minimal ordered subset of guaranteed/bundled fonts covering text-mode missing glyphs in a LuaTeX log."""
    missing_cps: set[int] = set()
    for line in log_text.splitlines():
        m = _MISSING_CHAR_LOG.search(line.strip())
        if not m:
            continue
        ch_raw, code_hex, font = m.groups()
        if not is_otf_font(font):
            continue
        cp = int(code_hex, 16) if code_hex else (ord(ch_raw) if len(ch_raw) == 1 else -1)
        if cp >= 0x20 and cp != 0x7F:
            missing_cps.add(cp)
    if not missing_cps:
        return []
    cov = load_coverage()
    chosen: list[str] = []
    uncovered = set(missing_cps)
    for fname in _FALLBACK_ORDER:
        ranges = cov.get(fname)
        if not ranges:
            continue
        hit = {cp for cp in uncovered if any(lo <= cp <= hi for lo, hi in ranges)}
        if hit:
            chosen.append(fname)
            uncovered -= hit
            if not uncovered:
                break
    return chosen


def fallback_bundled_fonts(fallback_fonts: tuple[str, ...] | list[str]) -> list[str]:
    bundled = set(_BUNDLED_FALLBACK_FONTS)
    return [f for f in fallback_fonts if f in bundled]


def fallback_sty(fallback_fonts: tuple[str, ...] | list[str]) -> str:
    """Generate rosettex-fallback.sty registering a luaotfload fallback chain over validated fonts."""
    allowed = set(_FALLBACK_ORDER)
    bundled = set(_BUNDLED_FALLBACK_FONTS)
    entries: list[str] = []
    for fname in fallback_fonts:
        if fname not in allowed:
            continue
        if fname in bundled:
            mode = "harf" if fname in _BUNDLED_HARF_FONTS else "node"
            entries.append(
                f'  if lfs.isfile("./{fname}") then table.insert(chain, "[./{fname}]:mode={mode};") end'
            )
        else:
            entries.append(
                f'  if kpse.find_file("{fname}", "opentype fonts") or kpse.find_file("{fname}", "truetype fonts") '
                f'then table.insert(chain, "file:{fname}:mode=node;") end'
            )
    body = "\n".join(entries)
    return (
        "\\NeedsTeXFormat{LaTeX2e}\n"
        "\\ProvidesPackage{rosettex-fallback}[2026/11/26 rosettex luaotfload glyph fallback]\n"
        "\\directlua{\n"
        "  if luaotfload and luaotfload.add_fallback then\n"
        "    local chain = {}\n"
        f"{body}\n"
        '    if next(chain) ~= nil then luaotfload.add_fallback("rxfallback", chain) end\n'
        "  end\n"
        "}\n"
    )


def fallback_preamble_lines() -> list[str]:
    """Top-of-preamble lines activating luaotfload fallback before fontspec/babel load fonts."""
    return [
        _LOAD_FONTSPEC,
        "\\usepackage{rosettex-fallback}",
        "\\defaultfontfeatures+{RawFeature={fallback=rxfallback}}",
        "\\ifdefined\\defaultjfontfeatures\\defaultjfontfeatures+{RawFeature={fallback=rxfallback}}\\fi",
    ]


def _script_font(lang: dict) -> str | None:
    profile = lang.get("profile") or {}
    script = lang.get("script") or profile.get("id") or "latin"
    if script in ("latin", "cyrillic", "greek"):
        return None
    if script == "han":
        if lang.get("babel") == "chinese-traditional" or lang.get("code") in ("zh-Hant", "zh-TW", "zh-HK", "zh-MO"):
            return "NotoSerifTC-Regular.otf"
        return "NotoSerifSC-Regular.otf"
    fonts = profile.get("fonts") or []
    return fonts[0] if fonts else None


def staged_fonts(lang: dict) -> list[str]:
    font = _script_font(lang)
    return [font] if font else []


_TU_FAMILIES: dict[str, str] = {
    # PSNFSS Type1 families
    "ptm": "rm",
    "ppl": "rm",
    "pbk": "rm",
    "pnc": "rm",
    "put": "rm",
    "pzc": "rm",
    "phv": "sf",
    "pag": "sf",
    "pcr": "tt",
    # TeX Gyre families
    "qtm": "rm",
    "qpl": "rm",
    "qbk": "rm",
    "qcs": "rm",
    "qzc": "rm",
    "qhv": "sf",
    "qhvc": "sf",
    "qag": "sf",
    "qcr": "tt",
}
_TU_NOTO_FACES: dict[str, tuple[str, str, str, str, str]] = {
    "rm": (
        "NotoSerif-Regular.ttf",
        "NotoSerif-Italic.ttf",
        "NotoSerif-Bold.ttf",
        "NotoSerif-BoldItalic.ttf",
        "\\UnicodeFontTeXLigatures",
    ),
    "sf": (
        "NotoSans-Regular.ttf",
        "NotoSans-Italic.ttf",
        "NotoSans-Bold.ttf",
        "NotoSans-BoldItalic.ttf",
        "\\UnicodeFontTeXLigatures",
    ),
    "tt": (
        "NotoSansMono-Regular.ttf",
        "NotoSansMono-Regular.ttf",
        "NotoSansMono-Bold.ttf",
        "NotoSansMono-Bold.ttf",
        "",
    ),
}


def tu_fd_files() -> dict[str, str]:
    """TU .fd files mapping legacy PSNFSS and TeX Gyre families to Noto (for LuaLaTeX/XeLaTeX)."""
    files: dict[str, str] = {}
    for fam, slot in _TU_FAMILIES.items():
        reg, it, bold, bi, feat = _TU_NOTO_FACES[slot]
        it_feat = f"slant={_FAKE_SLANT}" if slot == "tt" else feat
        files[f"tu{fam}.fd"] = (
            f"\\ProvidesFile{{tu{fam}.fd}}[2026/11/26 rosettex TU Noto mapping for {fam}]\n"
            f"\\DeclareFontFamily{{TU}}{{{fam}}}{{}}\n"
            f"\\DeclareFontShape{{TU}}{{{fam}}}{{m}}{{n}}{{<->\\UnicodeFontFile{{{reg}}}{{{feat}}}}}{{}}\n"
            f"\\DeclareFontShape{{TU}}{{{fam}}}{{m}}{{it}}{{<->\\UnicodeFontFile{{{it}}}{{{it_feat}}}}}{{}}\n"
            f"\\DeclareFontShape{{TU}}{{{fam}}}{{m}}{{sl}}{{<->ssub * {fam}/m/it}}{{}}\n"
            f"\\DeclareFontShape{{TU}}{{{fam}}}{{m}}{{sc}}{{<->ssub * {fam}/m/n}}{{}}\n"
            f"\\DeclareFontShape{{TU}}{{{fam}}}{{m}}{{scit}}{{<->ssub * {fam}/m/it}}{{}}\n"
            f"\\DeclareFontShape{{TU}}{{{fam}}}{{m}}{{scsl}}{{<->ssub * {fam}/m/it}}{{}}\n"
            f"\\DeclareFontShape{{TU}}{{{fam}}}{{b}}{{n}}{{<->\\UnicodeFontFile{{{bold}}}{{{feat}}}}}{{}}\n"
            f"\\DeclareFontShape{{TU}}{{{fam}}}{{b}}{{it}}{{<->\\UnicodeFontFile{{{bi}}}{{{it_feat}}}}}{{}}\n"
            f"\\DeclareFontShape{{TU}}{{{fam}}}{{b}}{{sl}}{{<->ssub * {fam}/b/it}}{{}}\n"
            f"\\DeclareFontShape{{TU}}{{{fam}}}{{b}}{{sc}}{{<->ssub * {fam}/b/n}}{{}}\n"
            f"\\DeclareFontShape{{TU}}{{{fam}}}{{b}}{{scit}}{{<->ssub * {fam}/b/it}}{{}}\n"
            f"\\DeclareFontShape{{TU}}{{{fam}}}{{b}}{{scsl}}{{<->ssub * {fam}/b/it}}{{}}\n"
            f"\\DeclareFontShape{{TU}}{{{fam}}}{{bx}}{{n}}{{<->ssub * {fam}/b/n}}{{}}\n"
            f"\\DeclareFontShape{{TU}}{{{fam}}}{{bx}}{{it}}{{<->ssub * {fam}/b/it}}{{}}\n"
            f"\\DeclareFontShape{{TU}}{{{fam}}}{{bx}}{{sl}}{{<->ssub * {fam}/b/it}}{{}}\n"
            f"\\DeclareFontShape{{TU}}{{{fam}}}{{bx}}{{sc}}{{<->ssub * {fam}/b/n}}{{}}\n"
            f"\\DeclareFontShape{{TU}}{{{fam}}}{{bx}}{{scit}}{{<->ssub * {fam}/b/it}}{{}}\n"
            f"\\DeclareFontShape{{TU}}{{{fam}}}{{bx}}{{scsl}}{{<->ssub * {fam}/b/it}}{{}}\n"
            "\\endinput\n"
        )
    return files


def preamble_prelude(engine: str, lang: dict) -> str:
    profile = lang.get("profile") or {}
    if engine == "lua" and profile.get("direction") == "rtl":
        return f"\\PassOptionsToPackage{{{_RTL_BABEL_OPTS}}}{{babel}}\n"
    return ""


# Poppler (pdftotext/Evince) breaks a word before a zero-width mark that HarfBuzz positioned back over its base
# (overlap > 0.2 em reads as duplicated text) and after it, copying "अनुक ू लन" / "ขั ้นตอน". A per-word /ActualText span
# (\unexpanded: babel macros would otherwise expand inside \directlua) makes Poppler take the whole word; PDFium is unaffected. Words with RTL characters (Arabic copy) are left alone.
_MARKS_ACTUALTEXT_LUA = r"""
local d = node.direct
local todirect, tonode = d.todirect, d.tonode
local getnext, getid, getlist, setlist = d.getnext, d.getid, d.getlist, d.setlist
local getprop, getwidth = d.getproperty, d.getwidth
local insertbefore, insertafter = d.insert_before, d.insert_after
local glyph_id, kern_id, hlist_id = node.id("glyph"), node.id("kern"), node.id("hlist")
local hexd = "0123456789ABCDEF"
local function h4(c)
local s = ""
for _ = 1, 4 do local r = c - (c // 16) * 16; s = hexd:sub(r + 1, r + 1) .. s; c = c // 16 end
return s
end
local function utf16hex(s)
local out = {}
for _, c in utf8.codes(s) do
if c >= 0x10000 then c = c - 0x10000; table.insert(out, h4(0xD800 + c // 1024) .. h4(0xDC00 + c - (c // 1024) * 1024))
else table.insert(out, h4(c)) end
end
return table.concat(out)
end
local function lit(data)
local n = d.new("whatsit", "pdf_literal")
d.setfield(n, "mode", 1); d.setfield(n, "data", data)
return n
end
local function info(n) local p = getprop(n); return p and p.glyph_info end
local function rtl(s)
for _, c in utf8.codes(s) do
if (c >= 0x590 and c <= 0x8FF) or (c >= 0xFB1D and c <= 0xFEFF) then return true end
end
end
local function walk(head)
local n = head
while n do
local id = getid(n)
if id == hlist_id then
local l = getlist(n); if l then setlist(n, walk(l)) end
elseif id == glyph_id and not (getprop(n) or {}).rx_done then
local first, last, txt, marks, ok, m = n, n, "", false, true, n
while m do
local mid = getid(m)
if mid == glyph_id then
local mi = info(m)
if not mi or mi == "" or rtl(mi) then ok = false end
if getwidth(m) == 0 and m ~= first then marks = true end
txt = txt .. (mi or ""); last = m
elseif mid ~= kern_id then break end
m = getnext(m)
end
while getid(last) == kern_id do last = d.getprev(last) end
if ok and marks then
local p = getprop(first) or {}; p.rx_done = true; d.setproperty(first, p)
head = insertbefore(head, first, lit("/Span<</ActualText<FEFF" .. utf16hex(txt) .. ">>>BDC"))
head = insertafter(head, last, lit("EMC"))
end
n = last
end
n = getnext(n)
end
return head
end
local function filt(head) return tonode(walk(todirect(head))) end
luatexbase.add_to_callback("post_linebreak_filter", filt, "rosettex.marks.v")
luatexbase.add_to_callback("hpack_filter", filt, "rosettex.marks.h")
"""


def _marks_actualtext_lines(script: str, profile: dict) -> list[str]:
    if script not in _HARFBUZZ_SCRIPTS or profile.get("direction") == "rtl":
        return []
    return ["\\usepackage{rosettex-marks}"]


def marks_sty() -> str:
    """rosettex-marks.sty: generated (a .sty, so the \\directlua guard scan of .tex files never sees it)."""
    return (
        "\\NeedsTeXFormat{LaTeX2e}\n\\ProvidesPackage{rosettex-marks}[2026/11/27 rosettex /ActualText over marks]\n"
        "\\directlua{\\unexpanded{" + _MARKS_ACTUALTEXT_LUA + "}}\n"
    )


def _noto_font_lines(*, babel: bool = False) -> list[str]:
    if babel:
        return [f"\\babelfont{{{slot}}}{spec}" for _, slot, spec in _NOTO_SPECS]
    return [f"\\set{slot}font{spec}" for slot, _, spec in _NOTO_SPECS]


def _staged_font_opts(
    script: str,
    profile: dict | None = None,
    *,
    renderer_harfbuzz: bool = False,
    otf_script: str | None = None,
    italic_slant: bool = False,
    extra: tuple[str, ...] = (),
) -> str:
    opts = ["Path=./"]
    if renderer_harfbuzz:
        opts.append("Renderer=HarfBuzz")
    if otf_script:
        opts.append(f"Script={otf_script}")
    if profile is None or profile.get("emphasis") == "bold":
        fb = "1.5" if script in _CJK_SCRIPTS else ("2.0" if script == "arabic" else "2.2")
        it_feat = f"FakeSlant={_FAKE_SLANT}" if italic_slant else f"FakeBold={fb}"
        bi_feat = f"FakeBold={fb},FakeSlant={_FAKE_SLANT}" if italic_slant else f"FakeBold={fb}"
        # Bundled Noto Regular fonts ship only upright Regular; synthesise bold/italic/slanted and
        # map small caps (which CJK/Indic/Arabic fonts lack) to upright/bold/italic so NFSS never warns.
        opts += [
            "BoldFont=*",
            f"BoldFeatures={{FakeBold={fb},SmallCapsFont=*,SmallCapsFeatures={{FakeBold={fb}}}}}",
            "ItalicFont=*",
            f"ItalicFeatures={{{it_feat},SmallCapsFont=*,SmallCapsFeatures={{{it_feat}}}}}",
            "BoldItalicFont=*",
            f"BoldItalicFeatures={{{bi_feat}}}",
            "SlantedFont=*",
            f"SlantedFeatures={{{it_feat}}}",
            "BoldSlantedFont=*",
            f"BoldSlantedFeatures={{{bi_feat}}}",
            "SmallCapsFont=*",
            "SmallCapsFeatures={}",
        ]
    opts.extend(extra)
    return ",".join(opts)


def _precomposed_lines() -> list[str]:
    mark = {"c": "\u0327", "k": "\u0328"}
    return [
        f'\\DeclareTextCompositeCommand{{\\{acc}}}{{TU}}{{{ch}}}{{\\char"{ord(unicodedata.normalize("NFC", ch + mark[acc])):04X} }}'
        for acc, chars in _PRECOMPOSED.items()
        for ch in chars
    ]


def _missing_common_char_lines(font: str) -> list[str]:
    ranges = load_coverage().get(font)
    if not ranges:
        return []
    lines: list[str] = []
    for ch in _COMMON_FALLBACK_CHARS:
        cp = ord(ch)
        if not any(lo <= cp <= hi for lo, hi in ranges):
            lines.append(f'\\babelcharproperty{{"{cp:04X}}}{{locale}}{{english}}')
    return lines


def _xe_preamble(lang: dict) -> list[str]:
    profile = lang.get("profile") or {}
    script = lang.get("script") or profile.get("id") or "latin"
    babel_name = lang["babel"]
    font = _script_font(lang)

    if script in ("latin", "cyrillic", "greek"):
        return [
            _LOAD_FONTSPEC,
            "\\ifdefined\\babelprovide\\else\\usepackage{babel}\\fi",
            f"\\babelprovide[import,main]{{{babel_name}}}",
            *_noto_font_lines(),
        ]

    if script in _CJK_SCRIPTS and font:
        opts = _staged_font_opts(script, profile)
        return [
            _LOAD_FONTSPEC,
            *_noto_font_lines(),
            "\\usepackage{xeCJK}",
            f"\\setCJKmainfont{{{font}}}[{opts}]",
            f"\\setCJKsansfont{{{font}}}[{opts}]",
            f"\\setCJKmonofont{{{font}}}[{opts}]",
            "\\xeCJKsetup{PunctStyle=quanjiao}",
        ]

    lines = [_LOAD_FONTSPEC]
    if script == "thai":
        lines.append('\\XeTeXlinebreaklocale "th"\\XeTeXlinebreakskip=0pt plus 1pt')
    elif script == "myanmar":
        lines.append('\\XeTeXlinebreaklocale "my"\\XeTeXlinebreakskip=0pt plus 1pt')
    lines += _noto_font_lines()

    poly = profile.get("polyglossia_default")
    if script == "arabic":
        poly = babel_name if babel_name in ("arabic", "persian", "urdu") else "arabic"
        lines.append("\\providecommand\\UseMathForPositioningText{}")

    if poly and font:
        opts = _staged_font_opts(script, profile, otf_script=profile.get("otf_script"))
        cmd = _POLYGLOSSIA_FONT_CMD.get(script, poly)
        lines += [
            "\\usepackage{polyglossia}",
            f"\\setdefaultlanguage{{{poly}}}",
            "\\setotherlanguage{english}",
            f"\\newfontfamily\\{cmd}font{{{font}}}[{opts}]",
            f"\\newfontfamily\\{cmd}fontsf{{{font}}}[{opts}]",
            f"\\newfontfamily\\{cmd}fonttt{{{font}}}[{opts}]",
        ]
        if poly != cmd:
            lines += [
                f"\\newfontfamily\\{poly}font{{{font}}}[{opts}]",
                f"\\newfontfamily\\{poly}fontsf{{{font}}}[{opts}]",
                f"\\newfontfamily\\{poly}fonttt{{{font}}}[{opts}]",
            ]
    elif font:
        opts = _staged_font_opts(script, profile, otf_script=profile.get("otf_script"))
        lines.append(f"\\setmainfont{{{font}}}[{opts}]")
    return lines


def _layout_preamble_lines() -> list[str]:
    """Line-breaking stretch and bounded table shrinking for Noto Unicode builds (lua/xe)."""
    return [
        "\\makeatletter",
        "\\ifdefined\\scalebox\\else\\usepackage{graphicx}\\fi",
        "\\newsavebox\\rx@tabbox",
        "\\newcount\\rx@tabdepth",
        "\\newif\\ifrx@noscale",
        "\\def\\rx@tabletype{table}",
        "\\expandafter\\let\\expandafter\\rx@fpeval\\csname fp_eval:n\\endcsname",
        "\\expandafter\\let\\expandafter\\rx@dimtofp\\csname dim_to_fp:n\\endcsname",
        "\\def\\rx@tab@before{%",
        "  \\ifnum\\rx@tabdepth>\\z@",
        "    \\advance\\rx@tabdepth\\@ne",
        "  \\else",
        "    \\ifmmode\\else\\ifrx@noscale\\else",
        "      \\ifdefined\\@captype",
        "        \\ifx\\@captype\\rx@tabletype",
        "          \\begingroup",
        "          \\rx@tabdepth\\@ne",
        "          \\setbox\\rx@tabbox\\hbox\\bgroup\\color@begingroup",
        "        \\fi",
        "      \\fi",
        "    \\fi\\fi",
        "  \\fi",
        "}",
        "\\def\\rx@tab@after{%",
        "  \\ifnum\\rx@tabdepth>\\@ne",
        "    \\advance\\rx@tabdepth\\m@ne",
        "  \\else\\ifnum\\rx@tabdepth=\\@ne",
        "    \\color@endgroup\\egroup",
        "    \\ifdim\\linewidth>\\z@",
        "      \\ifdim\\wd\\rx@tabbox>\\dimexpr\\linewidth+\\hfuzz\\relax",
        "        \\ifdim\\linewidth<0.85\\wd\\rx@tabbox",
        "          \\leavevmode\\box\\rx@tabbox",
        "        \\else",
        "          \\edef\\rx@scale{\\rx@fpeval{trunc(\\rx@dimtofp{\\linewidth}/\\rx@dimtofp{\\wd\\rx@tabbox},4)}}%",
        "          \\edef\\rx@scale@log{\\rx@fpeval{round(\\rx@dimtofp{\\linewidth}/\\rx@dimtofp{\\wd\\rx@tabbox},3)}}%",
        "          \\typeout{rosettex: scaled table to \\rx@scale@log\\space (\\the\\wd\\rx@tabbox\\space -> \\the\\linewidth)}%",
        "          \\expandafter\\scalebox\\expandafter{\\rx@scale}{\\box\\rx@tabbox}%",
        "        \\fi",
        "      \\else",
        "        \\leavevmode\\box\\rx@tabbox",
        "      \\fi",
        "    \\else",
        "      \\leavevmode\\box\\rx@tabbox",
        "    \\fi",
        "    \\endgroup",
        "  \\fi\\fi",
        "}",
        "\\AddToHook{env/tabular/before}{\\rx@tab@before}",
        "\\AddToHook{env/tabular/after}{\\rx@tab@after}",
        "\\AddToHook{env/tabular*/before}{\\rx@tab@before}",
        "\\AddToHook{env/tabular*/after}{\\rx@tab@after}",
        "\\AddToHook{env/tabularx/begin}{\\rx@noscaletrue}",
        "\\AddToHook{env/tabulary/begin}{\\rx@noscaletrue}",
        "\\AddToHook{env/longtable/begin}{\\rx@noscaletrue}",
        "\\AddToHook{env/adjustbox/begin}{\\rx@noscaletrue}",
        "\\AddToHook{begindocument/end}{%",
        "  \\setlength{\\emergencystretch}{3em}%",
        "  \\ifdefined\\Gscale@box",
        "    \\let\\rx@orig@Gscale@box\\Gscale@box",
        "    \\long\\def\\Gscale@box#1[#2]#3{\\rx@orig@Gscale@box{#1}[#2]{\\rx@noscaletrue#3}}%",
        "  \\fi",
        "  \\ifdefined\\Gscale@box@dd",
        "    \\let\\rx@orig@Gscale@box@dd\\Gscale@box@dd",
        "    \\long\\def\\Gscale@box@dd#1#2#3{\\rx@orig@Gscale@box@dd{#1}{#2}{\\rx@noscaletrue#3}}%",
        "  \\fi",
        "  \\ifdefined\\Gscale@box@dddd",
        "    \\let\\rx@orig@Gscale@box@dddd\\Gscale@box@dddd",
        "    \\long\\def\\Gscale@box@dddd#1#2#3#4#5{\\rx@orig@Gscale@box@dddd{#1}{#2}{#3}{#4}{\\rx@noscaletrue#5}}%",
        "  \\fi",
        "  \\ifdefined\\@adjustbox",
        "    \\let\\rx@orig@adjustbox\\@adjustbox",
        "    \\long\\def\\@adjustbox#1{\\rx@noscaletrue\\rx@orig@adjustbox{#1}}%",
        "  \\fi",
        "}",
        "\\makeatother",
    ]


def _zwsp_preamble_lines(engine: str) -> list[str]:
    """Make U+200B (ZWSP) a zero-width line-break opportunity in LuaLaTeX and XeLaTeX."""
    if engine not in ("lua", "xe"):
        return []
    return [
        "\\AddToHook{begindocument/end}{%",
        '  \\catcode"200B=\\active',
        '  \\begingroup\\lccode`\\~="200B\\lowercase{\\endgroup',
        "    \\protected\\def~{\\ifmmode\\else\\ifhmode\\hskip0pt plus 1pt\\relax\\fi\\fi}%",
        "    \\ifdefined\\pdfstringdefDisableCommands",
        "      \\pdfstringdefDisableCommands{\\def~{}}%",
        "    \\fi",
        "  }%",
        "}",
    ]


def baseline_preamble(engine: str) -> str:
    """Noto text fonts for the identity (baseline) build on Unicode engines."""
    if engine == "pdf":
        return ""
    return (
        "\n".join(
            [
                "% generated by rosettex",
                _LOAD_FONTSPEC,
                *_noto_font_lines(),
                _RESET_FAMILY,
                _MATH_NBSP,
                *_layout_preamble_lines(),
                *_zwsp_preamble_lines(engine),
            ]
        )
        + "\n"
    )


def language_preamble(engine: str, lang: dict, with_fallback: bool = False, layout: bool = True) -> str:
    lines = ["% generated by rosettex"]
    if engine == "pdf":
        pdf = lang["pdflatex"]
        enc = pdf["fontenc"]
        lines += [
            "\\makeatletter",
            f"\\@ifpackageloaded{{fontenc}}{{\\input{{{enc.lower()}enc.def}}"
            f"\\renewcommand{{\\encodingdefault}}{{{enc}}}\\fontencoding{{{enc}}}\\selectfont}}"
            f"{{\\usepackage[{enc}]{{fontenc}}}}",
            "\\makeatother",
            "\\ifdefined\\babelprovide\\else\\usepackage{babel}\\fi",
            f"\\babelprovide[import,main]{{{pdf['babel']}}}",
        ]
    elif engine == "xe":
        lines += _xe_preamble(lang)
    else:
        profile = lang.get("profile") or {}
        script = lang.get("script") or profile.get("id") or "latin"
        babel_name = lang["babel"]
        font = _script_font(lang)

        if script in ("latin", "cyrillic", "greek"):
            lines += [
                _LOAD_FONTSPEC,
                "\\ifdefined\\babelprovide\\else\\usepackage{babel}\\fi",
                f"\\babelprovide[import,main]{{{babel_name}}}",
                *_noto_font_lines(),
            ]
        elif script == "kana":
            opts = _staged_font_opts(script, profile)
            lines += [
                _LOAD_FONTSPEC,
                "\\ifdefined\\babelprovide\\else\\usepackage{babel}\\fi",
                f"\\babelprovide[import,main]{{{babel_name}}}",
                "\\usepackage{luatexja-fontspec}",
                *(["\\defaultjfontfeatures+{RawFeature={fallback=rxfallback}}"] if with_fallback else []),
                *_noto_font_lines(),
                f"\\setmainjfont{{{font}}}[{opts}]",
                f"\\setsansjfont{{{font}}}[{opts}]",
            ]
        else:
            use_hb = script in _HARFBUZZ_SCRIPTS
            opts = _staged_font_opts(script, profile, renderer_harfbuzz=use_hb)
            babel_load = (
                "\\ifdefined\\babelprovide\\else\\usepackage{babel}\\fi"
                if profile.get("direction") == "rtl"
                else "\\ifdefined\\babelprovide\\else\\usepackage[english]{babel}\\fi"
            )
            lines += [
                _LOAD_FONTSPEC,
                babel_load,
                "\\babelprovide[import,onchar=ids fonts]{english}",
                f"\\babelprovide[import,main,onchar=ids fonts]{{{babel_name}}}",
                *_noto_font_lines(babel=True),
            ]
            if font:
                lines += [f"\\babelfont[{babel_name}]{{{f}}}{{{font}}}[{opts}]" for f in ("rm", "sf", "tt")]
                lines += _missing_common_char_lines(font) + _precomposed_lines()
                if use_hb:
                    lines.append(
                        # \url typesets in math mode, where onchar=ids leaves Latin in the target locale (whose
                        # tt font has no Latin): set URLs in the english locale.
                        "\\AtBeginDocument{\\ifdefined\\urlstyle"
                        "\\expandafter\\def\\csname url@ttstyle\\endcsname{\\def\\UrlFont{\\csname otherlanguage*\\endcsname{english}\\ttfamily}}"
                        "\\urlstyle{tt}"
                        "\\providecommand{\\doi}{doi: \\begingroup \\urlstyle{tt}\\Url}\\fi}"
                    )
    if engine != "pdf":
        lines.append(_RESET_FAMILY)
        lines.append(_MATH_NBSP)
        script = lang.get("script") or (lang.get("profile") or {}).get("id") or "latin"
        if script in _CJK_SCRIPTS:
            lines.append(_MATH_IDEO_SPACE)
        if layout:
            lines += _layout_preamble_lines()
        lines += _zwsp_preamble_lines(engine)
        if engine == "lua":
            lines += _marks_actualtext_lines(script, lang.get("profile") or {})
    else:
        lines.append("\\emergencystretch=3em")
    return "\n".join(lines) + "\n"


def overlay_preamble(lang: dict) -> str:
    """Target-language preamble of the PDF overlay build (LuaLaTeX): fallback fonts, no table/layout hooks."""
    return language_preamble("lua", lang, with_fallback=True, layout=False)


def source_preamble(engine: str, prof: dict, with_fallback: bool = False) -> tuple[list[str], list[str]]:
    """Font lines for a neutralised non-English source (V report §6) and the bundled fonts they need. They go
    before the target-language / baseline lines."""
    if engine == "pdf":
        return [], []
    kinds, font = prof["kinds"], prof["font"]
    lines: list[str] = []
    fonts: list[str] = []
    # B6: the bundled CJK fonts lack U+3000 (ideographic space): take it from HaranoAji (a luatexja dependency).
    # Path={} inside AltFont keeps HaranoAji resolved from TeX Live while Path=./ resolves the staged Noto font.
    alt = _ALT_CJK_SPACE
    lua_cjk_opts = _staged_font_opts("han", italic_slant=True, extra=(f'AltFont={{{{Range="3000,Font={alt},Path={{}}}}}}',))
    if font and kinds & {"cjk", "bxcjk"} and engine == "lua":
        if with_fallback:
            lines.append("\\ifdefined\\defaultjfontfeatures\\defaultjfontfeatures+{RawFeature={fallback=rxfallback}}\\fi")
        lines += [f"\\setmainjfont{{{font}}}[{lua_cjk_opts}]", f"\\setsansjfont{{{font}}}[{lua_cjk_opts}]"]
        fonts.append(font)
    elif font and (kinds & {"ctex", "fandol"} or (kinds & {"cjk", "bxcjk"} and engine == "xe")):
        if engine == "xe":
            lines.append('\\xeCJKDeclareSubCJKBlock{rxIdeoSpace}{"3000}')
            cjk_opts = _staged_font_opts("han", italic_slant=True, extra=(f"rxIdeoSpace={alt}",))
        else:
            if with_fallback:
                lines.append(
                    "\\ifdefined\\defaultjfontfeatures\\defaultjfontfeatures+{RawFeature={fallback=rxfallback}}\\fi"
                )
            cjk_opts = lua_cjk_opts
        lines += [f"\\setCJK{f}font{{{font}}}[{cjk_opts}]" for f in ("main", "sans", "mono")]
        fonts.append(font)
    if "kotex" in kinds:
        ko_opts = _staged_font_opts("hangul", italic_slant=True)
        lines.append(
            f"\\usepackage{{kotex}}"
            f"\\setmainhangulfont{{{_KR}}}[{ko_opts}]"
            f"\\setsanshangulfont{{{_KR}}}[{ko_opts}]"
            f"\\setmonohangulfont{{{_KR}}}[{ko_opts}]"
        )
        fonts.append(_KR)
    if fonts:  # bundled CJK fonts lack U+3000; AltFont/xeCJK sub-blocks do not intercept math mode
        lines.append(_MATH_IDEO_SPACE)
    return lines, fonts


def insert_preamble(main_text: str, preamble_name: str = "rosettex-preamble", prelude: str = "") -> str:
    m = _BEGIN.search(main_text)
    if not m:
        raise ValueError("no \\begin{document} in the main file")
    i = m.start(1)
    out = main_text[:i] + f"\\input{{{preamble_name}}}\n" + main_text[i:]
    return prelude + out if prelude else out

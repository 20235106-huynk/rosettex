import re

RULES = ("minted", "compat", "drivers", "encodings", "type1_fonts", "luatex85")

_DRIVERS = r"(?:pdftex|dvips|dvipdfm|dvipdfmx|xetex|luatex)"
_DRIVER_OPT = re.compile(rf"(?<![A-Za-z]){_DRIVERS}(?![A-Za-z])")
_PKG = re.compile(r"\\(documentclass|usepackage)\s*\[([^\]]*)\]\s*\{([^}]*)\}")
_DRIVER_TARGETS = {"documentclass", "graphicx", "graphics", "color", "xcolor", "hyperref"}
_ENC = re.compile(r"\\usepackage\s*(?:\[[^\]]*\])?\s*\{(?:inputenc|fontenc)\}")
_MINTED = re.compile(r"\\usepackage\s*(?:\[[^\]]*\])?\s*\{minted\}")
_THEHALG = re.compile(r"\\newcommand(\*?)(?=\s*(?:\{\\theHalgorithm\}|\\theHalgorithm\b))")
# picinpar's \computeilg probes \char0..\char127 to measure font height/depth; on Unicode (TU) fonts,
# slots 0..31 and 127 are unencoded control chars (33 Missing character logs per window: 2007.12473).
_PICINPAR = re.compile(r"\\(?:usepackage|RequirePackage)\s*(?:\[[^\]]*\])?\s*\{[^}]*\bpicinpar\b[^}]*\}")
_PICINPAR_PATCH = (
    "\\AtBeginDocument{\\ifdefined\\computeilg"
    "\\expandafter\\ifx\\csname rx@computeilg\\endcsname\\relax"
    "\\expandafter\\let\\csname rx@computeilg\\endcsname\\computeilg"
    "\\def\\computeilg{\\expandafter\\edef\\csname rx@tlc\\endcsname{\\the\\tracinglostchars}"
    "\\tracinglostchars=0\\relax\\csname rx@computeilg\\endcsname"
    "\\tracinglostchars=\\csname rx@tlc\\endcsname\\relax}\\fi\\fi}"
)
_ACL_PATCH = re.compile(
    r"^([ \t]*)(\\patchcmd\s*\{?\\@combinedblfloats\}?\s*\{\\box\\@outputbox\}"
    r"\s*\{\\unvbox\\@outputbox\}\s*\{\}\s*\{\\errmessage\{\\noexpand\s+patch failed\}\})",
    re.M,
)

MINTED_STY = r"""\NeedsTeXFormat{LaTeX2e}
\ProvidesPackage{rosettex-minted}[2026/11/25 minted replaced by fancyvrb (no shell escape)]
\RequirePackage{fancyvrb}
\RequirePackage{float}
\@ifundefined{c@listing}{\newfloat{listing}{tbhp}{lol}\floatname{listing}{Listing}}{}
\providecommand{\listoflistings}{}
\newenvironment{minted}[2][]{\VerbatimEnvironment\begin{Verbatim}[fontsize=\small]}{\end{Verbatim}}
\newcommand{\inputminted}[3][]{\VerbatimInput[fontsize=\small]{#3}}
\newcommand{\mintinline}[3][]{\texttt{\detokenize{#3}}}
\newcommand{\setminted}[2][]{}
\newcommand{\usemintedstyle}[2][]{}
\newcommand{\newminted}[3][]{}
"""

_TYPE1_NAME = re.compile(r"^(ptm|ppl|pbk|pnc|phv|pag|pcr)[rblkd][ior]?n?$")
_NOTO_BY_SHAPE = {
    (False, False): "NotoSerif-Regular.ttf",
    (True, False): "NotoSerif-Bold.ttf",
    (False, True): "NotoSerif-Italic.ttf",
    (True, True): "NotoSerif-BoldItalic.ttf",
}
_RAW_FONT = re.compile(r"(\\font\s*\\[A-Za-z@]+\s*=?\s*)([a-z]{4,7}?)(?:7t|8t|8r|8c|8a)?(?=\s|$|%)")
_NEW_FONT = re.compile(
    r"(\\newfont\s*(?:\{\s*\\[A-Za-z@]+\s*\}|\\[A-Za-z@]+)\s*\{\s*)([a-z]{4,7}?)(?:7t|8t|8r|8c|8a)?(?=\s|\}|$)"
)
_COMMENT_PREFIX = re.compile(r"(?<!\\)(?:\\\\)*%")


def _in_comment(text: str, pos: int) -> bool:
    line_start = text.rfind("\n", 0, pos) + 1
    return bool(_COMMENT_PREFIX.search(text[line_start:pos]))


_RAW_FONT_NFSS = re.compile(
    r"\\font\s*(\\[A-Za-z@]+)\s*=?\s*([a-z]{4,7}?)(?:7t|8t|8r|8c|8a)?"
    r"(?:\s+scaled\s*(\d+)|\s+at\s*(\d+(?:\.\d+)?)\s*pt)?(?=\s|$|%)"
)
_NFSS_FAMILY = {"ptm": "rm", "ppl": "rm", "pbk": "rm", "pnc": "rm", "phv": "sf", "pag": "sf", "pcr": "tt"}


def _raw_font_to_nfss(m: re.Match) -> str:
    """`\\font\\x=ptmb7t scaled 1100` -> an NFSS switch, so babel can swap in the target-script font
    (a raw \\font bypasses babel: cvpr.sty section headings, 1512.03385)."""
    if _in_comment(m.string, m.start()):
        return m.group(0)
    cs, name, scaled, at = m.groups()
    if not _TYPE1_NAME.match(name):
        return m.group(0)
    fam = _NFSS_FAMILY[name[:3]]
    size = float(at) if at else (int(scaled) / 100 if scaled else 10.0)  # Type1 design size is 10pt
    rest = name[3:]
    series = "\\bfseries" if rest[:1] in ("b", "d") else "\\mdseries"
    shape = "\\itshape" if "i" in rest[1:] else ("\\slshape" if "o" in rest[1:] else "\\upshape")
    return f"\\def{cs}{{\\fontsize{{{size:g}pt}}{{{size * 1.2:g}pt}}\\selectfont\\{fam}family{series}{shape}}}"


def _rewrite_type1_fonts(text: str) -> str:
    text = _RAW_FONT_NFSS.sub(_raw_font_to_nfss, text)

    def _repl(m: re.Match) -> str:
        if _in_comment(m.string, m.start()):
            return m.group(0)
        name = m.group(2)
        if not _TYPE1_NAME.match(name):
            return m.group(0)
        rest = name[3:]
        otf = _NOTO_BY_SHAPE[(rest[:1] in ("b", "d"), "i" in rest[1:] or "o" in rest[1:])]
        return f'{m.group(1)}"[{otf}]"'

    text = _RAW_FONT.sub(_repl, text)
    return _NEW_FONT.sub(_repl, text)


def _strip_encodings(text: str) -> str:
    def _repl(m: re.Match) -> str:
        return m.group(0) if _in_comment(m.string, m.start()) else ""

    return _ENC.sub(_repl, text)


def _apply_compat(text: str) -> str:
    def _repl_alg(m: re.Match) -> str:
        return m.group(0) if _in_comment(m.string, m.start()) else f"\\providecommand{m.group(1)}"

    text = _THEHALG.sub(_repl_alg, text)
    text = _ACL_PATCH.sub(lambda m: f"{m.group(1)}% rosettex: {m.group(2)}", text)
    return text


def _strip_drivers(m: re.Match) -> str:
    if _in_comment(m.string, m.start()):
        return m.group(0)
    kind, opts, names = m.groups()
    targets = {n.strip() for n in names.split(",")}
    if kind != "documentclass" and not (targets & _DRIVER_TARGETS):
        return m.group(0)
    if not _DRIVER_OPT.search(opts):
        return m.group(0)
    if "\n" not in opts:
        kept = [o for o in (x.strip() for x in opts.split(",")) if o and not re.fullmatch(_DRIVERS, o)]
        return f"\\{kind}[{','.join(kept)}]{{{names}}}" if kept else f"\\{kind}{{{names}}}"
    out_lines = []
    for line in opts.split("\n"):
        cm = _COMMENT_PREFIX.search(line)
        code, comment = (line[: cm.start()], line[cm.start() :]) if cm else (line, "")
        code = re.sub(rf"(?:,\s*)?\b{_DRIVERS}\b\s*,?", lambda dm: "," if dm.group(0).startswith(",") and dm.group(0).endswith(",") else "", code)
        if line.strip() and not (code + comment).strip():
            continue  # B3: a line emptied by the strip would be a \par inside the option list
        out_lines.append(code + comment)
    joined = "\n".join(out_lines)
    return f"\\{kind}[{joined}]{{{names}}}"


def apply_shim(
    text: str,
    engine: str,
    is_main: bool,
    shim_off: set[str] | frozenset[str] = frozenset(),
) -> str:
    if "minted" not in shim_off:
        text = _MINTED.sub(r"\\usepackage{rosettex-minted}", text)
    if "compat" not in shim_off:
        text = _apply_compat(text)
    if engine == "pdf":
        return text
    if "compat" not in shim_off:
        text = _PICINPAR.sub(
            lambda m: m.group(0) if _in_comment(m.string, m.start()) else m.group(0) + _PICINPAR_PATCH,
            text,
        )
    if "drivers" not in shim_off:
        text = _PKG.sub(_strip_drivers, text)
    if "encodings" not in shim_off:
        text = _strip_encodings(text)
    if "type1_fonts" not in shim_off:
        text = _rewrite_type1_fonts(text)
    if "luatex85" not in shim_off and engine == "lua" and is_main:
        text = "\\RequirePackage{luatex85}\n" + text
    return text


# --- non-English sources (spec §10.1, P5 V report §6): neutralise the build copy, never the source ---
_LOAD = re.compile(r"\\(usepackage|RequirePackage|documentclass)\s*(?:\[([^\]]*)\])?\s*\{([^}]*)\}")
_CJK_ENV = re.compile(r"\\begin\s*\{CJK\*?\}\s*(?:\[[^\]]*\])?\s*\{[^}]*\}\s*\{([^}]*)\}\s*|\\end\s*\{CJK\*?\}")
_FANDOL_SET = re.compile(r"\\setCJK(?:main|sans|mono)font\s*(?:\[[^\]]*\])?\s*\{[^}]*Fandol[^}]*\}(?:\s*\[[^\]]*\])?")
_SC, _TC, _JP, _KR = (f"NotoSerif{x}-Regular.otf" for x in ("SC", "TC", "JP", "KR"))
_CJK_FAMILY_FONT = {"gbsn": _SC, "gkai": _SC, "bsmi": _TC, "bkai": _TC, "min": _JP, "goth": _JP, "mj": _KR, "hei": _KR}
_LANG_CJK_FONT = {"zh": _SC, "zh-Hant": _TC, "ja": _JP, "ko": _KR}
_PLATEX_CLS = {"jsarticle": "ltjsarticle", "jsbook": "ltjsbook", "jsreport": "ltjsreport",
               "jarticle": "ltjarticle", "jreport": "ltjreport", "jbook": "ltjbook"}
_BABEL_ALIAS = {"francais": "french", "frenchb": "french", "germanb": "german", "russianb": "russian"}
_KIND_OF_PKG = {"CJK": "cjk", "CJKutf8": "cjk", "bxcjkjatype": "bxcjk", "ctex": "ctex", "kotex": "kotex",
                "vietnam": "vntex", "vntex": "vntex",
                "xeCJK": "xetex", "xunicode": "xetex", "xltxtra": "xetex",
                "luatexja": "luatexja", "luatexja-fontspec": "luatexja"}


_HAT_BYTES = re.compile(r"(?:\^\^[c-f][0-9a-f])(?:\^\^[89ab][0-9a-f])+")


def _hat_decode(seq: str) -> str:
    try:
        return bytes.fromhex(seq.replace("^^", "")).decode("utf-8")
    except UnicodeDecodeError:
        return seq


def source_profile(texts: list[str], src: dict) -> dict:
    """Source families present in the main file and local class/package files (V report §6), and the CJK font
    that stands in for the paper's own CJK fonts (from the CJK env family, else the source language)."""
    kinds: set[str] = set()
    fam = None
    for text in texts:
        text = re.sub(r"(?<!\\)%[^\n]*", "", text)
        for kind, _, names in _LOAD.findall(text):
            for n in (x.strip() for x in names.split(",")):
                if kind == "documentclass":
                    if n.startswith("ctex"):
                        kinds.add("ctex")
                    elif n in _PLATEX_CLS:
                        kinds.add("platex")
                    elif n.startswith("ltj"):
                        kinds.add("luatexja")
                elif n in _KIND_OF_PKG:
                    kinds.add(_KIND_OF_PKG[n])
        if re.search(r"\\XeTeX[a-z]", text):
            kinds.add("xetex")
        if _FANDOL_SET.search(text):
            kinds.add("fandol")
        m = _CJK_ENV.search(text)
        fam = fam or (m and m.group(1) and m.group(1).strip())
    font = _CJK_FAMILY_FONT.get(fam or "") or (_JP if kinds & {"bxcjk", "platex"} else None)
    font = font or _LANG_CJK_FONT.get(src.get("code", ""))
    return {"kinds": kinds, "font": font}


def source_ladder(prof: dict, engines: list[str]) -> list[str]:
    """Engine start point (§10.1): XeTeX-only sources start at xe; luatexja (incl. pLaTeX rewritten to it) never xe."""
    kinds = prof["kinds"]
    if kinds & {"luatexja", "platex"}:
        return [e for e in engines if e != "xe"]
    if "xetex" in kinds and "xe" in engines:
        return ["xe"] + [e for e in engines if e != "xe"]
    return engines


def _drop_opts(opts: str, pat: str) -> str:
    opts = re.sub(r"(?<!\\)(?:\\\\)*%[^\n]*\n?", "", opts)
    return ",".join(p for p in opts.split(",") if not re.fullmatch(rf"\s*(?:{pat})\s*", p))


def _neutral_load(m: re.Match, engine: str, kinds: set[str], class_file: bool) -> str:
    if _in_comment(m.string, m.start()):
        return m.group(0)
    kind, opts, names = m.group(1), m.group(2) or "", m.group(3)
    o, extra, keep = opts, "", []
    if "babel" in names:
        o = re.sub(r"\b(" + "|".join(_BABEL_ALIAS) + r")\b", lambda a: _BABEL_ALIAS[a.group(1)], o)
    if "babelbib" in names:  # fixlanguage pins the bibliography to the main language, which babelbib may not know (vi)
        o = _drop_opts(o, "fixlanguage")
    for n in (x.strip() for x in names.split(",")):
        if n in ("german", "ngerman") and kind != "documentclass":
            # old packages: babel loaded after them loops (nil.ldf). babel's own .ini (no babel-german needed);
            # both names, since the source may \selectlanguage either.
            other = "ngerman" if n == "german" else "german"
            extra += f"\\usepackage{{babel}}\\babelprovide[import=de]{{{other}}}\\babelprovide[import=de,main]{{{n}}}"
        elif engine == "pdf":
            if n == "inputenc":  # the build copy is always UTF-8 (encoding contract with D)
                o = "utf8"
            keep.append(n)
        elif kind == "documentclass" or n == "ctex":
            if n.startswith("ctex") or n == "ctex":
                o = _drop_opts(o, r"scheme=\w*|fontset=\w*")
                o = (o + "," if o.strip() else "") + "scheme=plain,fontset=none"
            elif engine == "lua" and n in _PLATEX_CLS:
                n, o = _PLATEX_CLS[n], _drop_opts(o, "uplatex|platex")
            keep.append(n)
        elif n in ("CJK", "CJKutf8", "bxcjkjatype"):
            extra += "\\usepackage{luatexja-fontspec}" if engine == "lua" else "\\usepackage{xeCJK}"
        elif n == "otf" and engine == "lua" and "platex" in kinds:
            keep.append("luatexja-otf")
        elif n == "pxjahyper" and engine == "lua" and "platex" in kinds:
            pass
        elif n == "kotex" and class_file:  # loaded during class options, before \normalsize exists (np2.rtx):
            pass  # the generated preamble loads it (source_preamble)
        elif n == "pslatex":
            pass  # Type1 Times/Helv/Courier: preamble sets Noto Serif/Sans/Mono
        elif n in ("vietnam", "vntex"):
            extra += "\\usepackage[vietnamese]{babel}"
        else:
            keep.append(n)
    if "hyperref" in keep and "CJKbookmarks" in o and engine != "pdf":
        o = _drop_opts(o, "CJKbookmarks")
    new_names = ",".join(keep)
    if o == opts and extra == "" and new_names == ",".join(x.strip() for x in names.split(",")):
        return m.group(0)
    load = (f"\\{kind}[{o}]{{{new_names}}}" if o.strip() else f"\\{kind}{{{new_names}}}") if keep else ""
    return load + extra


def neutralise(text: str, engine: str, prof: dict, src_babel: str | None, tgt_babel: str | None,
               class_file: bool = False) -> str:
    """Rewrite a non-English source's language/font set-up for `engine` (V report §6; build copy only).
    With a target, `\\selectlanguage{<source>}` switches to the target (main language → target, §10.1)."""
    kinds = prof["kinds"]
    text = _LOAD.sub(lambda m: _neutral_load(m, engine, kinds, class_file), text)
    if src_babel and tgt_babel:
        names = {src_babel} | {a for a, b in _BABEL_ALIAS.items() if b == src_babel}
        text = re.sub(r"(\\selectlanguage\s*\{)\s*(" + "|".join(map(re.escape, names)) + r")\s*\}",
                      lambda m: m.group(0) if _in_comment(m.string, m.start()) else f"{m.group(1)}{tgt_babel}}}", text)
    if engine != "pdf":  # ^^e2^^80^^93: UTF-8 bytes escaped for an 8-bit engine (2102.10993 .bbl); one char on lua/xe
        text = _HAT_BYTES.sub(lambda m: _hat_decode(m.group(0)), text)
        if kinds & {"cjk"}:
            text = _CJK_ENV.sub(lambda m: m.group(0) if _in_comment(m.string, m.start()) else "", text)
        if "fandol" in kinds:  # B5: Fandol is gone from tlnet; the preamble sets the bundled font
            text = _FANDOL_SET.sub(lambda m: m.group(0) if _in_comment(m.string, m.start()) else "", text)
    return text

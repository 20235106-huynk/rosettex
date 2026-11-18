"""document.json + translation → main.tex (the build) and probe.tex (trial compile of each formula/table)."""
from __future__ import annotations

import re
import unicodedata
from collections import defaultdict

from ..checks import PH_RE, in_blocks
from ..extract.tables import table_shape
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
    # script fonts draw larger than Termes: one visual size (explicit profile scale where MatchLowercase has no effect)
    scale = ("Scale=%s" % prof["scale"] if prof.get("scale") else "Scale=MatchLowercase") if lay in ("runs", "rtl") else ""
    o = ",".join(x for x in (o, scale, "Script=" + prof["otf_script"] if prof["otf_script"] else "") if x)
    if lay == "cjk":
        opt = "[%s]" % prof["layout_options"] if prof["layout_options"] else ""
        return main, [r"\usepackage%s{xeCJK}" % opt, r"\setCJKmainfont{%s}[%s]" % (f, o)]
    if lay == "rtl":
        po = pack.get("polyglossia_options")
        return main, [r"\usepackage{polyglossia}", r"\setdefaultlanguage%s{%s}" % ("[%s]" % po if po else "", poly),
                      r"\setotherlanguage{english}", r"\newfontfamily\%sfont{%s}[%s]" % (poly, f, o),
                      r"\newcommand{\rxltr}[1]{\textenglish{#1}}",
                      # Naskh has no Latin letters or parentheses: math \text and equation tags go through the Latin font
                      r"\let\rxtext\text\renewcommand{\text}[1]{\rxtext{\textenglish{#1}}}",
                      r"\makeatletter\def\tagform@#1{\maketag@@@{\rxltr{(\ignorespaces#1\unskip\@@italiccorr)}}}\makeatother"]
    if poly:
        return main, [r"\usepackage{polyglossia}", r"\setdefaultlanguage{english}",
                      r"\newfontfamily\%sfont{%s}[%s]" % (poly, f, o), r"\setotherlanguage{%s}" % poly,
                      r"\newcommand{\rxs}[1]{\text%s{#1}}" % poly]
    return main, [r"\newfontfamily\rxscriptfont{%s}[%s]" % (f, o), r"\newcommand{\rxs}[1]{{\rxscriptfont #1}}"]


def label_lines(pack) -> list[str]:
    lab, prof = pack["labels"], pack["profile"]
    names = (("figure", "figurename"), ("table", "tablename"), ("references", "refname"))
    return [r"\renewcommand{\%s}{%s}" % (m, wrap_runs(lab[k], prof)) for k, m in names if k in lab]


def page_geometry(doc) -> tuple:
    sizes = doc["source"]["page_size_pt"]
    w, h = sizes[0] if sizes else (612, 792)
    bbs = [b["bbox"] for b in doc["blocks"] if b.get("bbox")]

    def clamp(v):
        return max(36, min(108, round(v)))

    left = clamp(min((x[0] for x in bbs), default=0.1) * w)
    right = clamp((1 - max((x[2] for x in bbs), default=0.9)) * w)
    top = clamp(min((x[1] for x in bbs), default=0.1) * h)
    bottom = clamp((1 - max((x[3] for x in bbs), default=0.9)) * h)
    return w, h, left, right, top, bottom


IEEE_ROMAN = re.compile(r"\s*[IVX]+\.\s+\S")
IEEE_SUB = re.compile(r"\s*[A-Z]\.\s+\S")
IEEE_LEAD = re.compile(r"\s*(?:Abstract|Index Terms)\s*[\u2014\u2013-]")


def paper_style(doc) -> str:
    """'ieee' when the source looks like an IEEE paper (Abstract—, roman section numbers), else 'generic'."""
    srcs = [u["src"] for u in doc["units"]]
    heads = [u["src"] for u in doc["units"] if u["type"] == "heading"]
    if any(re.match(r"\s*Abstract\s*[\u2014\u2013-]", x) for x in srcs) and sum(bool(IEEE_ROMAN.match(h)) for h in heads) >= 2:
        return "ieee"
    return "generic"


def _roman(n: int) -> str:
    out = ""
    for v, r in ((1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"), (50, "L"), (40, "XL"),
                 (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")):
        while n >= v:
            out, n = out + r, n - v
    return out


def roman_tables(doc) -> bool:
    nums = [b["number"] for b in doc["blocks"] if b["kind"] == "table" and b.get("number")]  # unnumbered ones are skipped
    return bool(nums) and all(re.fullmatch(r"[IVXLC]+", n) for n in nums)


def preamble(doc, pack, twocolumn=None, packages=()) -> str:
    w, h, left, right, top, bottom = page_geometry(doc)
    ieee = paper_style(doc) == "ieee"
    two = doc["source"]["columns"] == 2 if twocolumn is None else twocolumn
    main, lang = lang_lines(pack)
    rtl = pack["profile"]["layout"] == "rtl"
    return "\n".join([
        *([r"\documentclass[conference%s%s]{IEEEtran}" % (",a4paper" if abs(w - 595) < 5 else "", "" if two else ",onecolumn")]
          if ieee else  # the venue's own class: its headings, captions, abstract and margins (IEEE sets them itself)
          [r"\documentclass[10pt%s]{article}" % (",twocolumn" if two else ""),
           r"\usepackage[paperwidth=%sbp,paperheight=%sbp,left=%dbp,right=%dbp,top=%dbp,bottom=%dbp]{geometry}"
           % (w, h, left, right, top, bottom)]),
        r"\usepackage{fontspec}",
        *main,
        r"\usepackage{amsmath,amssymb}",
        r"\usepackage{unicode-math}",
        r"\setmathfont{latinmodern-math.otf}",
        *([] if rtl else lang),
        r"\usepackage{graphicx,booktabs,multirow,adjustbox,url}",
        *[r"\usepackage{%s}" % p for p in packages],
        r"\usepackage[hidelinks]{hyperref}",
        *(lang if rtl else []),
        r"\makeatletter\newcommand{\rxlabel}[2]{\phantomsection\def\@currentlabel{#1}\label{#2}}\makeatother",
        r"\setlength{\emergencystretch}{3em}",
        *([r"\renewcommand{\thetable}{\Roman{table}}"] if roman_tables(doc) else []),  # IEEE: TABLE I, II, ...
        r"\begin{document}",
        *label_lines(pack),
        "",
    ])


class Renderer:
    def __init__(self, doc, tr, pack, fallback, images):
        self.doc, self.tr, self.pack, self.fb, self.images = doc, tr, pack, set(fallback), images
        self.units = {u["id"]: u for u in doc["units"]}
        self.prof = pack["profile"]
        self.style = paper_style(doc)
        self._sec, self._abstract = 0, False  # IEEE: last section number seen; inside the (bold) abstract
        self.packages = set()  # extra LaTeX packages the body needs (array for wrapped table columns)
        self.rtl = self.prof["layout"] == "rtl"

    # ---------- text ----------
    @staticmethod
    def mark(b) -> str:
        return "\\typeout{RX:" + b["id"] + "}\n"

    def box_inline(self, src) -> str:
        return r"\fbox{\ttfamily\small " + escape_latex(src) + "}"

    def box_display(self, src, label=None) -> str:
        return (r"\begin{center}\fbox{\parbox{0.95\linewidth}{\raggedright\ttfamily\small ["
                + escape_latex(label or self.pack["labels"]["fallback"]) + r"]\\ " + escape_latex(src)
                + r"}}\end{center}")

    def verbatim(self, spans, bid) -> str:
        out = []
        for s in spans:
            if s["t"] == "math":
                out.append(self.box_inline(s["v"]) if bid in self.fb else "$" + s["v"] + "$")
            elif s["t"] == "sup":
                sup = "\\textsuperscript{" + escape_latex(s["v"]) + "}"
                out.append("\\rxltr{%s}" % sup if self.rtl else sup)
            elif self.rtl and s["v"].strip() and not any(in_blocks(c, self.prof["_blocks"]) for c in s["v"]):
                out.append("\\rxltr{%s}" % escape_latex(s["v"]))  # whole English span LTR (authors, e-mails, daggers)
            else:
                out.append(wrap_runs(s["v"], self.prof))
        return " ".join("".join(out).split())

    def text(self, uid, spans) -> str:
        u = self.units.get(uid)
        if u is None:
            return self.verbatim(spans, uid)
        t = self.tr.get(uid, u["src"])
        if uid in self.fb or (u["type"] == "cell" and u["block"] in self.fb):  # last-resort fallback for a text block: plain single-line text, no macros
            t = PH_RE.sub(lambda m: u["ph"][m.group(0)[1:-1]]["src"] if m.group(0)[1:-1] in u["ph"] else m.group(0), t)
            return wrap_runs(" ".join(t.split()), self.prof)
        out, pos = [], 0
        for m in PH_RE.finditer(t):
            out.append(wrap_runs(t[pos:m.start()], self.prof))
            name = m.group(0)[1:-1]
            p = u["ph"].get(name)
            if p is None:
                out.append(escape_latex(m.group(0)))
            elif f"{uid}:{name}" in self.fb:
                out.append(self.box_inline(p["src"]))
            elif self.rtl and not p["tex"].startswith("$"):
                out.append("\\rxltr{%s}" % p["tex"])
            else:
                out.append(p["tex"])
            pos = m.end()
        out.append(wrap_runs(t[pos:], self.prof))
        return "".join(out)

    # ---------- blocks ----------
    def heading(self, b) -> str:
        t = self.text(b["id"], b["spans"])
        if self.style == "ieee" and not b.get("number"):
            src = (self.units.get(b["id"]) or {}).get("src", "")
            if b["level"] == 1:
                return r"\begin{center}{\fontsize{24}{28}\selectfont %s}\end{center}" % t
            self._abstract = False
            m = re.match(r"\s*([IVXLC]+)\.\s", src)
            if m and m.group(1) == _roman(self._sec + 1):  # the next section number: "C. Attestation" stays a subsection
                self._sec += 1
                return "\\section*{%s}" % t
            if IEEE_SUB.match(src):
                return "\\subsection*{%s}" % t
            return "\\section*{%s}" % t  # I. INTRODUCTION, APPENDIX, REFERENCES: IEEE's centred small caps
        if b["level"] == 1:
            return r"\begin{center}{\LARGE\bfseries %s}\end{center}" % t
        cmd = {2: "section", 3: "subsection"}.get(b["level"], "subsubsection")
        if b.get("number"):
            lab = "\\rxlabel{%s}{%s}" % (b["number"], b["label"]) if b.get("label") else ""
            return "\\%s*{%s\\quad %s}%s" % (cmd, b["number"], t, lab)
        return "\\%s*{%s}" % (cmd, t)

    def equation(self, b) -> str:
        if b["id"] in self.fb:
            lab = "\\rxlabel{%s}{%s}" % (b["tag"], b["label"]) if b.get("label") else ""
            tag = "\\hfill(%s)" % escape_latex(b["tag"]) if b.get("tag") else ""
            return self.box_display(b["tex"]) + tag + lab
        if b.get("tag"):
            lab = "\\label{%s}" % b["label"] if b.get("label") else ""
            return "\\begin{equation}%s\\tag{%s}%s\\end{equation}" % (b["tex"], escape_latex(b["tag"]), lab)
        return "\\begin{equation*}%s\\end{equation*}" % b["tex"]

    @staticmethod
    def _em(t: str) -> float:
        """Rough text width in em: full-width characters 1, others 0.5, a placeholder 3 characters."""
        t = PH_RE.sub("xxx", t)
        return sum(1.0 if unicodedata.east_asian_width(c) in "WF" else 0.5 for c in t)

    def col_spec(self, b, cells, cols, probe=False):
        """'l' + 'c'*n while the table fits its column (kept for vi); otherwise long columns wrap (p{}) instead of the
        whole table shrinking to unreadable size."""
        nat = [0.0] * cols
        for c in cells:
            if c["colspan"] == 1:
                u = self.units.get(c["id"])
                nat[c["c"]] = max(nat[c["c"]], self._em(self.tr.get(c["id"], u["src"] if u else c["text"])))
        w, _, left, right, _, _ = page_geometry(self.doc)
        two = self.doc["source"]["columns"] == 2 and not probe
        line_pt = (w - left - right) if (not two or b.get("wide")) else (w - left - right - 10) / 2
        avail = line_pt / 10  # em at 10 pt
        default = "l" + "c" * (cols - 1)
        if sum(nat) + 1.2 * cols <= avail * 1.15:
            return default, None
        longc = [i for i in range(cols) if nat[i] > 5]
        if not longc:
            return default, None
        self.packages.add("array")
        fixed = sum(nat[i] + 1.2 for i in range(cols) if i not in longc) + 1.2 * len(longc)
        rest = max(avail * 0.95 - fixed, 3.0 * len(longc))
        tot = sum(nat[i] for i in longc)
        spec, widths = [], []
        for i in range(cols):
            if i in longc:
                frac = max(rest * nat[i] / tot, 3.0) / avail
                spec.append(r">{\raggedright\arraybackslash}p{%.3f\linewidth}" % frac)
                widths.append(frac)
            else:
                spec.append("l" if i == 0 else "c")
                widths.append((nat[i] + 1.2) / avail)
        return "".join(spec), (widths, set(longc))

    def tabular(self, b, probe: bool = False) -> str:
        cells = b.get("cells") or []
        rows, cols = table_shape(cells)
        if not rows:
            return ""
        spec, wrap = self.col_spec(b, cells, cols, probe)
        origin = {(c["r"], c["c"]): c for c in cells}
        cover = {}
        for c in cells:
            for dr in range(c["rowspan"]):
                for dc in range(c["colspan"]):
                    if dr:
                        cover[(c["r"] + dr, c["c"] + dc)] = c
        lines = []
        for r in range(rows):
            parts, col = [], 0
            while col < cols:
                c = origin.get((r, col))
                if c is not None:
                    x = self.text(c["id"], [{"t": "text", "v": c["text"]}])
                    span = range(c["c"], c["c"] + c["colspan"])
                    wrapped = wrap and any(i in wrap[1] for i in span)  # the cell lies on a wrapping (p{}) column
                    if c["rowspan"] > 1:
                        x = "\\multirow{%d}{%s}{%s}" % (c["rowspan"], "=" if wrapped and c["colspan"] == 1 else "*", x)
                    if c["colspan"] > 1:
                        cs = ("{>{\\centering\\arraybackslash}p{%.3f\\linewidth}}" % (0.97 * sum(wrap[0][i] for i in span))
                              if wrapped else "{c}")
                        x = "\\multicolumn{%d}%s{%s}" % (c["colspan"], cs, x)
                    parts.append(x)
                    col += c["colspan"]
                elif (r, col) in cover:
                    o = cover[(r, col)]
                    span = o["colspan"] - (col - o["c"])
                    parts.append("\\multicolumn{%d}{c}{}" % span if span > 1 else "")
                    col += span
                else:
                    parts.append("")
                    col += 1
            lines.append(" & ".join(parts) + r" \\")
        return "\\begin{tabular}{%s}\\toprule\n%s\n\\bottomrule\\end{tabular}" % (
            spec, "\n".join(lines))

    def float_(self, b) -> str:
        env = ("table" if b["kind"] == "table" else "figure") + ("*" if b.get("wide") else "")
        cap = self.text(b["id"] + ".cap", b.get("caption") or [])
        lab = "\\label{%s}" % b["label"] if b.get("label") else ""
        if b["kind"] == "table":
            tab = self.tabular(b)
            # a table with wrapped columns may be taller than the page: also cap its height instead of losing content
            cap_h = ",max totalheight=0.9\\textheight" if r"\raggedright\arraybackslash}p{" in tab else ""
            body = (self.box_display(tab) if b["id"] in self.fb
                    else "\\begin{adjustbox}{max width=\\linewidth%s}%s\\end{adjustbox}" % (cap_h, tab))
            return "\\begin{%s}[tbp]\\centering\\caption{%s}%s\n%s\\end{%s}" % (env, cap, lab, body, env)
        img = self.images.get(b["id"])
        body = ("\\includegraphics[width=\\linewidth]{%s}" % img if img and b["id"] not in self.fb
                else self.box_display(b.get("image") or "", self.pack["labels"]["missing_image"]))
        return "\\begin{%s}[tbp]\\centering%s\\caption{%s}%s\\end{%s}" % (env, body, cap, lab, env)

    def footnote(self, b) -> str:
        t = self.verbatim(b["spans"], b["id"]) if b.get("role") else self.text(b["id"], b["spans"])
        mark = "\\textsuperscript{%s}" % escape_latex(b["mark"]) if b.get("mark") else ""
        if mark and self.rtl:
            mark = "\\rxltr{%s}" % mark
        return "{\\renewcommand{\\thefootnote}{}\\footnotetext{%s%s}}" % (mark, t)

    def bibliography(self, refs) -> str:
        items = "\n".join(self.mark(b) + "\\bibitem{ref%s} %s" % (b["key"], self.verbatim(b["spans"], b["id"]))
                          for b in refs)
        env = "\\begin{thebibliography}{99}\n%s\n\\end{thebibliography}" % items
        label = self.pack["labels"].get("references")
        if self.rtl and label:
            # not otherlanguage{english}: with Tectonic it ends in "Missing } inserted" around thebibliography
            return "{\\selectlanguage{english}\\renewcommand{\\refname}{\\RL{\\%sfont %s}}\n%s\n}" % (
                self.pack["polyglossia"], wrap_runs(label, self.prof), env)
        return env

    def render_block(self, b) -> str:
        k = b["kind"]
        if k == "heading":
            return self.heading(b)
        if k == "paragraph":
            if b.get("role"):
                return self.verbatim(b["spans"], b["id"])
            out = self.text(b["id"], b["spans"])
            src = (self.units.get(b["id"]) or {}).get("src", "")
            i = out.find("\u2014")
            if self.style == "ieee" and IEEE_LEAD.match(src) and 0 < i < 40:  # IEEE: bold abstract with an italic lead-in
                self._abstract = src.lstrip().startswith("Abstract")
                return "\\noindent{\\small\\bfseries\\textit{%s}\u2014%s}" % (out[:i], out[i + 1:])
            if self.style == "ieee" and self._abstract:  # later paragraphs of the abstract, up to Index Terms
                return "{\\small\\bfseries %s}" % out
            return out
        if k == "equation":
            return self.equation(b)
        if k == "footnote":
            return self.footnote(b)
        if k in ("table", "figure"):
            return self.float_(b)
        return ""

    # ---------- document ----------
    def document(self) -> str:
        blocks = self.doc["blocks"]
        order = {b["id"]: i for i, b in enumerate(blocks)}
        first_ref = {}
        for u in self.doc["units"]:
            for p in u["ph"].values():
                for lab in re.findall(r"\\ref\{((?:fig|tab):[^}]*)\}", p["tex"]):
                    first_ref.setdefault(lab, u["block"])
        refs = [b for b in blocks if b["kind"] == "reference"]
        held, body, front, in_front = defaultdict(list), [], [], True
        for i, b in enumerate(blocks):
            k = b["kind"]
            if in_front and b["page"] == 1 and ((k == "heading" and b["level"] == 1) or b.get("role") == "authors"):
                inner = self.heading(b) if k == "heading" else "\\begin{center}%s\\end{center}" % self.verbatim(b["spans"], b["id"])
                front.append(self.mark(b) + inner)
                continue
            in_front = False
            if k in ("table", "figure"):
                rb = first_ref.get(b.get("label"))
                if rb in order and order[rb] > i:
                    held[rb].append(b)
                    continue
            if k == "reference":
                if b is refs[0]:
                    body.append(self.bibliography(refs))
                continue
            if k == "heading" and i + 1 < len(blocks) and blocks[i + 1]["kind"] == "reference":
                continue  # thebibliography prints its own heading in the target language
            body.append(self.mark(b) + self.render_block(b))
            for f in held.pop(b["id"], []):
                body.append(self.mark(f) + self.float_(f))
        for fs in held.values():
            body += [self.mark(f) + self.float_(f) for f in fs]
        fr = "\n".join(front)
        if fr and self.doc["source"]["columns"] == 2:
            fr = "\\twocolumn[{%s}]" % fr
        return preamble(self.doc, self.pack, packages=self.packages) + fr + "\n\n" + "\n\n".join(body) + "\n\\end{document}\n"

    def probe(self) -> str:
        items = []
        for b in self.doc["blocks"]:
            if b["id"] in self.fb:
                continue
            if b["kind"] == "equation":
                items.append(self.mark(b) + "\\begin{equation*}%s\\end{equation*}" % b["tex"])
            elif b["kind"] == "table":
                items.append(self.mark(b) + self.tabular(b, probe=True))
        for u in self.doc["units"]:
            for name, p in u["ph"].items():
                key = f"{u['id']}:{name}"
                if p["tex"].startswith("$") and key not in self.fb:
                    items.append("\\typeout{RX:%s}%s" % (key, p["tex"]))
        return (preamble(self.doc, self.pack, twocolumn=False, packages=self.packages) + "\n\n".join(items)
                + "\n\n\\mbox{}\n\\end{document}\n")

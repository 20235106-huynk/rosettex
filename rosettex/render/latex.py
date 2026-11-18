"""document.json + translation → main.tex (the build) and probe.tex (trial compile of each formula/table)."""
from __future__ import annotations

import re
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


def preamble(doc, pack, twocolumn=None) -> str:
    sizes = doc["source"]["page_size_pt"]
    w, h = sizes[0] if sizes else (612, 792)
    bbs = [b["bbox"] for b in doc["blocks"] if b.get("bbox")]

    def clamp(v):
        return max(36, min(108, round(v)))

    left = clamp(min((x[0] for x in bbs), default=0.1) * w)
    right = clamp((1 - max((x[2] for x in bbs), default=0.9)) * w)
    top = clamp(min((x[1] for x in bbs), default=0.1) * h)
    bottom = clamp((1 - max((x[3] for x in bbs), default=0.9)) * h)
    two = doc["source"]["columns"] == 2 if twocolumn is None else twocolumn
    main, lang = lang_lines(pack)
    rtl = pack["profile"]["layout"] == "rtl"
    return "\n".join([
        r"\documentclass[10pt%s]{article}" % (",twocolumn" if two else ""),
        r"\usepackage[paperwidth=%sbp,paperheight=%sbp,left=%dbp,right=%dbp,top=%dbp,bottom=%dbp]{geometry}"
        % (w, h, left, right, top, bottom),
        r"\usepackage{fontspec}",
        *main,
        r"\usepackage{amsmath,amssymb}",
        r"\usepackage{unicode-math}",
        r"\setmathfont{latinmodern-math.otf}",
        *([] if rtl else lang),
        r"\usepackage{graphicx,booktabs,multirow,adjustbox,url}",
        r"\usepackage[hidelinks]{hyperref}",
        *(lang if rtl else []),
        r"\makeatletter\newcommand{\rxlabel}[2]{\phantomsection\def\@currentlabel{#1}\label{#2}}\makeatother",
        r"\setlength{\emergencystretch}{3em}",
        r"\begin{document}",
        *label_lines(pack),
        "",
    ])


class Renderer:
    def __init__(self, doc, tr, pack, fallback, images):
        self.doc, self.tr, self.pack, self.fb, self.images = doc, tr, pack, set(fallback), images
        self.units = {u["id"]: u for u in doc["units"]}
        self.prof = pack["profile"]
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

    def tabular(self, b) -> str:
        cells = b.get("cells") or []
        rows, cols = table_shape(cells)
        if not rows:
            return ""
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
                    if c["rowspan"] > 1:
                        x = "\\multirow{%d}{*}{%s}" % (c["rowspan"], x)
                    if c["colspan"] > 1:
                        x = "\\multicolumn{%d}{c}{%s}" % (c["colspan"], x)
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
            "l" + "c" * (cols - 1), "\n".join(lines))

    def float_(self, b) -> str:
        env = ("table" if b["kind"] == "table" else "figure") + ("*" if b.get("wide") else "")
        cap = self.text(b["id"] + ".cap", b.get("caption") or [])
        lab = "\\label{%s}" % b["label"] if b.get("label") else ""
        if b["kind"] == "table":
            body = (self.box_display(self.tabular(b)) if b["id"] in self.fb
                    else "\\begin{adjustbox}{max width=\\linewidth}%s\\end{adjustbox}" % self.tabular(b))
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
            return self.verbatim(b["spans"], b["id"]) if b.get("role") else self.text(b["id"], b["spans"])
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
        return preamble(self.doc, self.pack) + fr + "\n\n" + "\n\n".join(body) + "\n\\end{document}\n"

    def probe(self) -> str:
        items = []
        for b in self.doc["blocks"]:
            if b["id"] in self.fb:
                continue
            if b["kind"] == "equation":
                items.append(self.mark(b) + "\\begin{equation*}%s\\end{equation*}" % b["tex"])
            elif b["kind"] == "table":
                items.append(self.mark(b) + self.tabular(b))
        for u in self.doc["units"]:
            for name, p in u["ph"].items():
                key = f"{u['id']}:{name}"
                if p["tex"].startswith("$") and key not in self.fb:
                    items.append("\\typeout{RX:%s}%s" % (key, p["tex"]))
        return (preamble(self.doc, self.pack, twocolumn=False) + "\n\n".join(items)
                + "\n\n\\mbox{}\n\\end{document}\n")

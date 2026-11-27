import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

from pylatexenc.latexwalker import (
    LatexCharsNode,
    LatexCommentNode,
    LatexEnvironmentNode,
    LatexGroupNode,
    LatexMacroNode,
    LatexMathNode,
    LatexSpecialsNode,
)

from rosettex.escape import decode_accents, decode_chars, encode_text, join_cjk
from rosettex.texparse import (
    COMMANDS,
    _blank_comments,
    build_context,
    find_math_macros,
    is_text_macro,
    parse,
    scan_author_macros,
)
from rosettex.textio import read_source

_BLANK_LINE = re.compile(r"\n[ \t]*\r?\n")
_LETTERS = re.compile(r"[^\W\d_]")
_TWO_WORDS = re.compile(r"[^\W\d_]\s+[^\W\d_]")
_MACRO_OR_BRACE = re.compile(r"\\[A-Za-z@]+|[{}]")
_PH = re.compile(r"⟦/?[a-z]\d+⟧")
_PH_TOK = re.compile(r"⟦(/?)([a-z])(\d+)⟧")
_IFFALSE = re.compile(r"\\iffalse\b.*?\\fi\b", re.S)
_BEGIN_DOC = re.compile(r"(?<!\\)((?:\\\\)*)\\begin\s*\{document\}")
# text accent macros and special letters, decoded to Unicode (escape.decode_accents)
_ACCENT_MACROS = frozenset("'`^\"~=.uvHcdbkr") | {"ss", "ae", "AE", "oe", "OE", "aa", "AA", "o", "O", "l", "L", "i", "j"}
# kotex particle macros (\은 \는 \이 \가 …): Korean grammar, dropped when the target is not Korean (V report D7)
_KO_PARTICLE = re.compile(r"[가-힣]{1,3}")
_RAW_CMD = re.compile(r"\\(?:begin|end)\b")
_DEF_MACROS = frozenset({"def", "gdef", "edef", "xdef"})
_DEF_CMDS = frozenset(
    {
        "newcommand",
        "renewcommand",
        "providecommand",
        "DeclareRobustCommand",
        "newenvironment",
        "renewenvironment",
        "newtheorem",
        "DeclareMathOperator",
    }
)
_INPUT_CMDS = frozenset({"input", "include", "subfile"})
_DIM_DECLS = frozenset({"newdimen", "newskip", "newmuskip", "newlength", "newcount"})
_DIM_PRIMITIVES = frozenset(
    {
        "vskip",
        "hskip",
        "vglue",
        "hglue",
        "openup",
        "kern",
        "mkern",
        "mskip",
        "vkern",
        "hkern",
        "raise",
        "lower",
        "moveleft",
        "moveright",
        "baselineskip",
        "parskip",
        "parindent",
        "leftskip",
        "rightskip",
        "topskip",
        "tabskip",
        "abovedisplayskip",
        "belowdisplayskip",
        "abovedisplayshortskip",
        "belowdisplayshortskip",
        "floatsep",
        "textfloatsep",
        "intextsep",
        "dblfloatsep",
        "dbltextfloatsep",
        "abovecaptionskip",
        "belowcaptionskip",
        "itemsep",
        "parsep",
        "topsep",
        "partopsep",
        "tabcolsep",
        "arraycolsep",
        "columnsep",
        "linewidth",
        "textwidth",
        "textheight",
        "hsize",
        "vsize",
        "hoffset",
        "voffset",
        "oddsidemargin",
        "evensidemargin",
        "topmargin",
        "headheight",
        "headsep",
        "footskip",
        "marginparsep",
        "marginparwidth",
        "fboxsep",
        "fboxrule",
        "arrayrulewidth",
        "doublerulesep",
        "jot",
        "mathsurround",
        "medmuskip",
        "thickmuskip",
        "thinmuskip",
        "lineskip",
        "lineskiplimit",
        "normallineskip",
        "normallineskiplimit",
        "smallskipamount",
        "medskipamount",
        "bigskipamount",
    }
)
_HALIGN_CMDS = frozenset({"halign", "valign", "cr", "noalign", "settabs", "+"})
_TEX_UNIT = r"(?:pt|pc|in|bp|cm|mm|dd|cc|sp|ex|em|mu|px|fil{1,3})\b"
_TEX_DIM_VAL = rf"[+-]?\s*(?:\d+(?:\.\d*)?|\.\d+)\s*(?:true\s*)?{_TEX_UNIT}"
_TEX_GLUE = re.compile(rf"^[ \t]*=?[ \t]*{_TEX_DIM_VAL}(?:\s*(?:plus|minus)\s*{_TEX_DIM_VAL})*")
_TEX_ASSIGN_DIM = re.compile(rf"^[ \t]*=[ \t]*{_TEX_DIM_VAL}(?:\s*(?:plus|minus)\s*{_TEX_DIM_VAL})*")
_TEX_OPT_DIM = re.compile(rf"^[ \t]*\*?[ \t]*\[[ \t]*{_TEX_DIM_VAL}[ \t]*\]")
_TEX_NUM_PREFIX = re.compile(r"^[ \t]*(=)?[ \t]*[+-]?(?:\d+(?:\.\d*)?|\.\d+)?[ \t]*$")


@dataclass
class Placeholder:
    code: str
    raw: str = ""
    open: str = ""
    close: str = ""
    child: str | None = None
    prefix: str = ""
    suffix: str = ""


@dataclass
class Segment:
    id: str
    kind: str  # paragraph | heading | caption | footnote | item | title | macro
    file: str
    start: int
    end: int
    text: str
    ph: dict[str, Placeholder] = field(default_factory=dict)
    section: str = ""
    parent: str | None = None
    hash: str = ""
    _raw: str = field(default="", repr=False)


def _letters(text: str) -> int:
    return len(_LETTERS.findall(_PH.sub("", text)))


def _merge_placeholder_runs(
    text: str, ph: dict[str, Placeholder]
) -> tuple[str, dict[str, Placeholder], dict[str, str]]:
    """Merge maximal whitespace-free adjacent runs of mergeable placeholder tokens into a single ⟦cN⟧.

    Nested segments (sN) and paired styles (bN) whose content contains any character outside
    placeholders or any nested segment are never merged. Remaining codes are renumbered in order.
    """
    tokens = list(_PH_TOK.finditer(text))
    if len(tokens) < 2:
        return text, ph, {}

    # Match paired bN open/close token indices.
    b_match: dict[int, int] = {}
    stack: list[tuple[int, str]] = []
    for idx, m in enumerate(tokens):
        closing, kind, num = m.groups()
        if kind != "b":
            continue
        code = f"b{num}"
        if not closing:
            stack.append((idx, code))
        elif stack and stack[-1][1] == code:
            open_idx, _ = stack.pop()
            b_match[open_idx] = idx
            b_match[idx] = open_idx

    # A bN pair is mergeable iff its interior has zero characters outside placeholders and no sN tokens.
    mergeable_b: set[int] = set()
    for open_idx, close_idx in b_match.items():
        if open_idx >= close_idx:
            continue
        if all(tokens[k].end() == tokens[k + 1].start() for k in range(open_idx, close_idx)) and all(
            tokens[k].group(2) != "s" for k in range(open_idx + 1, close_idx)
        ):
            mergeable_b.add(open_idx)
            mergeable_b.add(close_idx)

    mergeable = [
        (m.group(2) in ("c", "m", "r")) or (m.group(2) == "b" and idx in mergeable_b)
        for idx, m in enumerate(tokens)
    ]

    # Quick check: any mergeable run of length >= 2?
    has_run = any(
        mergeable[i] and mergeable[i + 1] and tokens[i].end() == tokens[i + 1].start()
        for i in range(len(tokens) - 1)
    )
    if not has_run:
        return text, ph, {}

    counters: dict[str, int] = {}

    def next_code(kind: str) -> str:
        counters[kind] = counters.get(kind, 0) + 1
        return f"{kind}{counters[kind]}"

    new_ph: dict[str, Placeholder] = {}
    b_remap: dict[str, str] = {}
    s_remap: dict[str, str] = {}
    out: list[str] = []
    pos = 0
    i = 0
    n_tok = len(tokens)
    while i < n_tok:
        m = tokens[i]
        if m.start() > pos:
            out.append(text[pos:m.start()])
        if mergeable[i]:
            j = i
            while j + 1 < n_tok and mergeable[j + 1] and tokens[j].end() == tokens[j + 1].start():
                j += 1
            if j > i:
                pieces: list[str] = []
                for k in range(i, j + 1):
                    cl, kd, num = tokens[k].groups()
                    p = ph[f"{kd}{num}"]
                    if cl:
                        pieces.append(p.close)
                    elif p.open:
                        pieces.append(p.open)
                    else:
                        pieces.append(p.raw)
                nc = next_code("c")
                new_ph[nc] = Placeholder(nc, raw="".join(pieces))
                out.append(f"⟦{nc}⟧")
                pos = tokens[j].end()
                i = j + 1
                continue
        cl, kd, num = m.groups()
        old_code = f"{kd}{num}"
        if kd == "b":
            if not cl:
                nc = next_code("b")
                b_remap[old_code] = nc
                old_p = ph[old_code]
                new_ph[nc] = Placeholder(nc, open=old_p.open, close=old_p.close)
                out.append(f"⟦{nc}⟧")
            else:
                out.append(f"⟦/{b_remap[old_code]}⟧")
        else:
            nc = next_code(kd)
            old_p = ph[old_code]
            new_ph[nc] = Placeholder(
                nc,
                raw=old_p.raw,
                open=old_p.open,
                close=old_p.close,
                child=old_p.child,
                prefix=old_p.prefix,
                suffix=old_p.suffix,
            )
            if kd == "s":
                s_remap[old_code] = nc
            out.append(f"⟦{nc}⟧")
        pos = m.end()
        i += 1
    if pos < len(text):
        out.append(text[pos:])
    return "".join(out), new_ph, s_remap


def _find_begin_doc(src: str) -> int:
    m = _BEGIN_DOC.search(_blank_comments(src))
    if not m:
        return -1
    return m.start() + len(m.group(1))


def _normalize_crlf(text: str) -> tuple[str, list[int] | None]:
    """Replace CRLF with LF and return (normalized, norm_to_orig_index_map or None)."""
    if "\r\n" not in text:
        return text, None
    out: list[str] = []
    mapping: list[int] = []
    i = 0
    n = len(text)
    while i < n:
        mapping.append(i)
        if text[i] == "\r" and i + 1 < n and text[i + 1] == "\n":
            out.append("\n")
            i += 2
        else:
            out.append(text[i])
            i += 1
    mapping.append(n)
    return "".join(out), mapping


class _Extractor:
    def __init__(self, src_dir: Path, project: dict):
        self.src_dir = src_dir
        self.project = project
        self.segments: list[Segment] = []
        self.unknown: dict[str, dict] = {}
        self.section = ""
        self.n = 0

        self.raw_sources: dict[str, str] = {}
        self.sources: dict[str, str] = {}
        self.pos_maps: dict[str, list[int] | None] = {}
        macro_texts: list[str] = []
        encodings = project.get("encodings") or {}
        for rel in project["files"]:
            raw_text, _ = read_source(src_dir / rel, encodings.get(rel))
            norm_text, pos_map = _normalize_crlf(raw_text)
            self.raw_sources[rel] = raw_text
            self.sources[rel] = norm_text
            self.pos_maps[rel] = pos_map
            if rel == project["main"]:
                pos = _find_begin_doc(norm_text)
                macro_texts.append(norm_text[:pos] if pos >= 0 else norm_text)
            else:
                macro_texts.append(norm_text)

        self.visited_files: set[str] = set()
        self.dimen_regs: set[str] = set()
        self.macros = scan_author_macros(macro_texts)
        self.math_macros = find_math_macros(self.sources.values())
        self.ctx = build_context(self.macros)
        self.known_macros = {m.name for m in self.macros if m.kind != "env"}
        self.known_envs = {m.name for m in self.macros if m.kind == "env"}
        known: set[str] = set()
        for t in ("segment_args", "nested", "paired", "wrappers", "caption_wrappers", "refs", "kept", "escapes"):
            known |= set(COMMANDS[t])
        self.known = (
            known
            | set(COMMANDS["rules"])
            | set(COMMANDS["font_switches"])
            | self.known_macros
            | _DEF_MACROS
            | _DEF_CMDS
            | _DIM_DECLS
            | _DIM_PRIMITIVES
            | {
                "\\",
                "tabularnewline",
                "item",
                "today",
                "LaTeX",
                "TeX",
                "ldots",
                "dots",
                "quad",
                "qquad",
                ",",
                ";",
                " ",
                "nointerlineskip",
            }
        )

    def _resolve_input(self, arg: str) -> str | None:
        arg = arg.strip()
        src_root = self.src_dir.resolve()
        main_dir = Path(self.project["main"]).parent
        for cand in (arg, arg + ".tex"):
            for root in (main_dir, Path(".")):
                p = self.src_dir / root / cand
                if p.is_file() and p.suffix == ".tex":
                    resolved = p.resolve()
                    if resolved.is_relative_to(src_root):
                        rel = resolved.relative_to(src_root).as_posix()
                        if rel in self.sources:
                            return rel
        return None

    def _orig_pos(self, file: str, pos: int) -> int:
        m = self.pos_maps.get(file)
        return m[pos] if m is not None else pos

    def _orig_slice(self, file: str, start: int, end: int) -> str:
        m = self.pos_maps.get(file)
        if m is not None:
            return self.raw_sources[file][m[start]:m[end]]
        return self.raw_sources[file][start:end]

    # ---- ids ---------------------------------------------------------------------------------
    def _new(self, kind, file, start, end, text, ph, parent=None) -> Segment:
        self.n += 1
        seg = Segment(
            f"s{self.n:04d}",
            kind,
            file,
            start,
            end,
            text,
            ph,
            self.section,
            parent,
            hashlib.sha1(text.encode("utf-8")).hexdigest()[:6],
        )
        self.segments.append(seg)
        return seg

    @staticmethod
    def _renumber(inner: str, inner_ph: dict, ph: dict, code) -> str:
        """Move inner placeholder codes into the parent's namespace in two phases (no collisions)."""
        mapping = {k: code(k[0]) for k in inner_ph}
        for k in mapping:
            inner = inner.replace(f"⟦{k}⟧", f"⟦@{k}⟧").replace(f"⟦/{k}⟧", f"⟦/@{k}⟧")
        for k, nk in mapping.items():
            inner = inner.replace(f"⟦@{k}⟧", f"⟦{nk}⟧").replace(f"⟦/@{k}⟧", f"⟦/{nk}⟧")
            v = inner_ph[k]
            v.code = nk
            ph[nk] = v
        return inner

    def _record_halign(self, file: str, example: str) -> None:
        rep = self.unknown.setdefault(
            "halign",
            {"name": "halign", "count": 0, "example": example[:60], "file": file, "action": "keep"},
        )
        rep["count"] += 1

    @staticmethod
    def _split_chars(node: LatexCharsNode, offset: int) -> LatexCharsNode | None:
        if offset >= len(node.chars):
            return None
        return LatexCharsNode(
            parsing_state=node.parsing_state,
            chars=node.chars[offset:],
            pos=node.pos + offset,
            len=node.len - offset,
        )

    def _consume_dim_arg(self, nodes: list, i: int, require_eq: bool) -> tuple[int, int, LatexCharsNode | None]:
        """Consume a TeX dimension/glue (or =<dim>) following nodes[i]. Returns (end, next_i, remainder_chars)."""
        n = nodes[i]
        end = n.pos + n.len
        j = i + 1
        if j >= len(nodes):
            return end, j, None
        nxt = nodes[j]
        if isinstance(nxt, LatexCharsNode):
            pat = _TEX_ASSIGN_DIM if require_eq else _TEX_GLUE
            m = pat.match(nxt.chars)
            if m:
                end = nxt.pos + m.end()
                return end, j + 1, self._split_chars(nxt, m.end())
            mp = _TEX_NUM_PREFIX.match(nxt.chars)
            if mp and (not require_eq or mp.group(1)) and j + 1 < len(nodes) and isinstance(nodes[j + 1], LatexMacroNode):
                mnode = nodes[j + 1]
                end = mnode.pos + mnode.len
                j2 = j + 2
                if mnode.macroname in ("wd", "ht", "dp") and j2 < len(nodes) and isinstance(nodes[j2], LatexCharsNode):
                    dm = re.match(r"^[ \t]*\d+", nodes[j2].chars)
                    if dm:
                        end = nodes[j2].pos + dm.end()
                        return end, j2 + 1, self._split_chars(nodes[j2], dm.end())
                return end, j2, None
        elif not require_eq and isinstance(nxt, LatexMacroNode):
            end = nxt.pos + nxt.len
            return end, j + 1, None
        return end, j, None

    # ---- inline builder ----------------------------------------------------------------------
    def _inline(self, nodes, file, src, parent_id_holder, strip: bool = True) -> tuple[str, dict]:
        """Turn a list of inline nodes into (shown text, placeholders)."""
        nodes = list(nodes)
        parts: list[str] = []
        ph: dict[str, Placeholder] = {}
        counters: dict[str, int] = {}
        pending_nested: list[tuple[str, LatexMacroNode]] = []

        def code(kind: str) -> str:
            counters[kind] = counters.get(kind, 0) + 1
            return f"{kind}{counters[kind]}"

        def raw(n) -> str:
            return self._orig_slice(file, n.pos, n.pos + n.len)

        i = 0
        while i < len(nodes):
            n = nodes[i]
            if isinstance(n, LatexCommentNode):
                i += 1
                continue
            if isinstance(n, LatexCharsNode):
                if _RAW_CMD.search(n.chars):
                    pos = 0
                    for m in _RAW_CMD.finditer(n.chars):
                        if m.start() > pos:
                            parts.append(decode_chars(n.chars[pos:m.start()], keep_newlines=True))
                        tok = m.group(0)
                        if (
                            m.end() == len(n.chars)
                            and i + 1 < len(nodes)
                            and isinstance(nodes[i + 1], LatexGroupNode)
                            and nodes[i + 1].pos == n.pos + n.len
                        ):
                            tok += raw(nodes[i + 1])
                            i += 1
                        c = code("c")
                        ph[c] = Placeholder(c, raw=tok)
                        parts.append(f"⟦{c}⟧")
                        pos = m.end()
                    if pos < len(n.chars):
                        parts.append(decode_chars(n.chars[pos:], keep_newlines=True))
                else:
                    parts.append(decode_chars(n.chars, keep_newlines=True))
            elif isinstance(n, LatexSpecialsNode):
                nxt = nodes[i + 1] if i + 1 < len(nodes) else None
                if (
                    n.specials_chars == "~"
                    and isinstance(nxt, LatexMacroNode)
                    and nxt.macroname in COMMANDS["refs"]
                ):
                    c = code("r")
                    ph[c] = Placeholder(c, raw=self._orig_slice(file, n.pos, nxt.pos + nxt.len))
                    parts.append(f"⟦{c}⟧")
                    i += 2
                    continue
                parts.append(n.specials_chars if n.specials_chars == "~" else decode_chars(raw(n)))
            elif isinstance(n, LatexMathNode) or (
                isinstance(n, LatexEnvironmentNode) and n.environmentname in COMMANDS["math_envs"]
            ):
                c = code("m")
                ph[c] = Placeholder(c, raw=raw(n))
                parts.append(f"⟦{c}⟧")
            elif isinstance(n, LatexMacroNode):
                name = n.macroname
                decoded = (
                    decode_accents(raw(n))
                    if name in _ACCENT_MACROS and name not in self.known_macros
                    else ""
                )
                if name in COMMANDS["escapes"]:
                    parts.append(COMMANDS["escapes"][name])
                elif decoded and not re.search(r"[\\{}]", decoded):
                    parts.append(decoded)
                elif _KO_PARTICLE.fullmatch(name) and not (n.nodeargd and n.nodeargd.argnlist):
                    lang = self.project.get("lang") or ""
                    c = code("c")
                    ph[c] = Placeholder(c, raw="" if lang and not lang.startswith("ko") else raw(n))
                    parts.append(f"⟦{c}⟧")
                elif (
                    name == "texorpdfstring"
                    and n.nodeargd
                    and n.nodeargd.argnlist
                    and isinstance(n.nodeargd.argnlist[0], LatexGroupNode)
                    and n.nodeargd.argnlist[0].nodelist
                ):
                    arg = n.nodeargd.argnlist[0]
                    inner, inner_ph = self._inline(arg.nodelist, file, src, parent_id_holder, strip=False)
                    lead = " " if inner.startswith(" ") else ""
                    trail = " " if inner.endswith(" ") else ""
                    c = code("b")
                    ph[c] = Placeholder(
                        c,
                        open=self._orig_slice(file, n.pos, arg.pos + 1) + lead,
                        close=trail + self._orig_slice(file, arg.pos + arg.len - 1, n.pos + n.len),
                    )
                    inner = self._renumber(inner.strip(), inner_ph, ph, code)
                    parts.append(f"⟦{c}⟧{inner}⟦/{c}⟧")
                elif name in COMMANDS["refs"]:
                    c = code("r")
                    ph[c] = Placeholder(c, raw=raw(n))
                    parts.append(f"⟦{c}⟧")
                elif (
                    (name in COMMANDS["paired"] or name in COMMANDS["wrappers"])
                    and n.nodeargd
                    and n.nodeargd.argnlist
                    and isinstance(n.nodeargd.argnlist[-1], LatexGroupNode)
                ):
                    arg = n.nodeargd.argnlist[-1]
                    inner, inner_ph = self._inline(arg.nodelist, file, src, parent_id_holder, strip=False)
                    if not inner.strip():
                        c = code("c")
                        ph[c] = Placeholder(c, raw=raw(n))
                        parts.append(f"⟦{c}⟧")
                    else:
                        lead = " " if inner.startswith(" ") else ""
                        trail = " " if inner.endswith(" ") else ""
                        c = code("b")
                        ph[c] = Placeholder(
                            c, open=self._orig_slice(file, n.pos, arg.pos + 1) + lead, close=trail + "}"
                        )
                        inner = self._renumber(inner.strip(), inner_ph, ph, code)
                        parts.append(f"⟦{c}⟧{inner}⟦/{c}⟧")
                elif (
                    name in COMMANDS["nested"]
                    and n.nodeargd
                    and n.nodeargd.argnlist
                    and n.nodeargd.argnlist[-1] is not None
                ):
                    c = code("s")
                    pending_nested.append((c, n))
                    parts.append(f"⟦{c}⟧")
                elif name in _DEF_MACROS:
                    j = i + 1
                    end = n.pos + n.len
                    while j < len(nodes):
                        end = nodes[j].pos + nodes[j].len
                        if isinstance(nodes[j], LatexGroupNode):
                            j += 1
                            break
                        j += 1
                    c = code("c")
                    ph[c] = Placeholder(c, raw=self._orig_slice(file, n.pos, end))
                    parts.append(f"⟦{c}⟧")
                    i = j
                    continue
                elif name in ("halign", "valign"):
                    j = i + 1
                    end = n.pos + n.len
                    while j < len(nodes):
                        end = nodes[j].pos + nodes[j].len
                        if isinstance(nodes[j], LatexGroupNode):
                            j += 1
                            break
                        j += 1
                    self._record_halign(file, self._orig_slice(file, n.pos, end))
                    c = code("c")
                    ph[c] = Placeholder(c, raw=self._orig_slice(file, n.pos, end))
                    parts.append(f"⟦{c}⟧")
                    i = j
                    continue
                elif name in _DIM_DECLS:
                    j = i + 1
                    end = n.pos + n.len
                    if j < len(nodes) and isinstance(nodes[j], LatexCharsNode) and not nodes[j].chars.strip():
                        j += 1
                    if j < len(nodes) and isinstance(nodes[j], LatexMacroNode):
                        self.dimen_regs.add(nodes[j].macroname)
                        self.known.add(nodes[j].macroname)
                        end = nodes[j].pos + nodes[j].len
                        j += 1
                    elif j < len(nodes) and isinstance(nodes[j], LatexGroupNode):
                        for sub in nodes[j].nodelist:
                            if isinstance(sub, LatexMacroNode):
                                self.dimen_regs.add(sub.macroname)
                                self.known.add(sub.macroname)
                        end = nodes[j].pos + nodes[j].len
                        j += 1
                    c = code("c")
                    ph[c] = Placeholder(c, raw=self._orig_slice(file, n.pos, end))
                    parts.append(f"⟦{c}⟧")
                    i = j
                    continue
                elif name in ("\\", "tabularnewline", "toprule", "midrule", "bottomrule"):
                    j = i + 1
                    end = n.pos + n.len
                    rem = None
                    if j < len(nodes) and isinstance(nodes[j], LatexCharsNode):
                        m_opt = _TEX_OPT_DIM.match(nodes[j].chars)
                        if m_opt:
                            end = nodes[j].pos + m_opt.end()
                            rem = self._split_chars(nodes[j], m_opt.end())
                            j += 1
                    c = code("c")
                    ph[c] = Placeholder(c, raw=self._orig_slice(file, n.pos, end))
                    parts.append(f"⟦{c}⟧")
                    if rem is not None:
                        nodes[j - 1] = rem
                        i = j - 1
                    else:
                        i = j
                    continue
                elif name in _DIM_PRIMITIVES or name in self.dimen_regs:
                    end, j, rem = self._consume_dim_arg(nodes, i, require_eq=False)
                    c = code("c")
                    ph[c] = Placeholder(c, raw=self._orig_slice(file, n.pos, end))
                    parts.append(f"⟦{c}⟧")
                    if rem is not None:
                        nodes[j - 1] = rem
                        i = j - 1
                    else:
                        i = j
                    continue
                else:
                    j = i + 1
                    end = n.pos + n.len
                    rem = None
                    # Check for explicit =<dim> assignment on any register macro (e.g. \tblskip=5pt)
                    eq_end, eq_j, eq_rem = self._consume_dim_arg(nodes, i, require_eq=True)
                    if eq_end > end:
                        end, j, rem = eq_end, eq_j, eq_rem
                    elif name not in self.known:
                        groups: list[LatexGroupNode] = []
                        while j < len(nodes) and isinstance(nodes[j], LatexGroupNode) and nodes[j].pos == end:
                            groups.append(nodes[j])
                            end = nodes[j].pos + nodes[j].len
                            j += 1
                        last_group: LatexGroupNode | None = groups[-1] if groups else None
                        if (
                            last_group is None
                            and n.nodeargd
                            and n.nodeargd.argnlist
                            and isinstance(n.nodeargd.argnlist[-1], LatexGroupNode)
                        ):
                            last_group = n.nodeargd.argnlist[-1]
                        paired_ok = False
                        if (
                            last_group is not None
                            and last_group.pos + last_group.len == end
                            and not self._has_block_content(last_group.nodelist)
                            and not self._has_unescaped_script(last_group.nodelist)
                        ):
                            raw_inner = src[last_group.pos + 1:last_group.pos + last_group.len - 1]
                            if _TWO_WORDS.search(_MACRO_OR_BRACE.sub("", raw_inner)):
                                inner, inner_ph = self._inline(
                                    last_group.nodelist, file, src, parent_id_holder, strip=False
                                )
                                if inner.strip():
                                    lead = " " if inner.startswith(" ") else ""
                                    trail = " " if inner.endswith(" ") else ""
                                    c = code("b")
                                    ph[c] = Placeholder(
                                        c,
                                        open=self._orig_slice(file, n.pos, last_group.pos + 1) + lead,
                                        close=trail + "}",
                                    )
                                    inner = self._renumber(inner.strip(), inner_ph, ph, code)
                                    parts.append(f"⟦{c}⟧{inner}⟦/{c}⟧")
                                    paired_ok = True
                        rep = self.unknown.setdefault(
                            name,
                            {"name": name, "count": 0, "example": src[n.pos:end], "file": file, "action": "keep"},
                        )
                        rep["count"] += 1
                        if paired_ok:
                            rep["action"] = "paired"
                            i = j
                            continue
                    c = code("c")
                    ph[c] = Placeholder(c, raw=self._orig_slice(file, n.pos, end))
                    parts.append(f"⟦{c}⟧")
                    if rem is not None:
                        nodes[j - 1] = rem
                        i = j - 1
                    else:
                        i = j
                    continue
            elif isinstance(n, LatexGroupNode):
                if not n.nodelist:
                    if ph and parts and parts[-1].startswith("⟦"):
                        last = list(ph.values())[-1]
                        last.raw += "{}"
                    i += 1
                    continue
                decoded = (
                    decode_accents(raw(n))
                    if len(n.nodelist) == 1
                    and isinstance(n.nodelist[0], LatexMacroNode)
                    and n.nodelist[0].macroname in _ACCENT_MACROS
                    and n.nodelist[0].macroname not in self.known_macros
                    else ""
                )
                if decoded and not re.search(r"[\\{}]", decoded):
                    parts.append(decoded)
                    i += 1
                    continue
                inner, inner_ph = self._inline(n.nodelist, file, src, parent_id_holder, strip=False)
                if not inner.strip():
                    c = code("c")
                    ph[c] = Placeholder(c, raw=raw(n))
                    parts.append(f"⟦{c}⟧")
                else:
                    lead = " " if inner.startswith(" ") else ""
                    trail = " " if inner.endswith(" ") else ""
                    c = code("b")
                    ph[c] = Placeholder(c, open="{" + lead, close=trail + "}")
                    inner = self._renumber(inner.strip(), inner_ph, ph, code)
                    parts.append(f"⟦{c}⟧{inner}⟦/{c}⟧")
            else:  # other environments inside a paragraph: keep verbatim
                c = code("c")
                ph[c] = Placeholder(c, raw=raw(n))
                parts.append(f"⟦{c}⟧")
            i += 1
        text = join_cjk("".join(parts))
        if strip:
            text = text.strip()
        for c, n in pending_nested:
            arg = n.nodeargd.argnlist[-1]
            ph[c] = Placeholder(c, prefix=self._orig_slice(file, n.pos, arg.pos + 1), suffix="}", child=None)
            parent_id_holder.append((c, n, arg))
        return text, ph

    # ---- emitters ----------------------------------------------------------------------------
    @staticmethod
    def _trim_nodes(nodes: list) -> list:
        nodes = [n for n in nodes if not isinstance(n, LatexCommentNode)]
        while nodes and isinstance(nodes[0], LatexCharsNode) and not nodes[0].chars.strip():
            nodes = nodes[1:]
        while nodes and isinstance(nodes[-1], LatexCharsNode) and not nodes[-1].chars.strip():
            nodes = nodes[:-1]
        return nodes

    @classmethod
    def _has_block_content(cls, nodes: list) -> bool:
        for n in nodes:
            if isinstance(n, LatexCharsNode) and _BLANK_LINE.search(n.chars):
                return True
            if isinstance(n, LatexEnvironmentNode) and n.environmentname not in COMMANDS["math_envs"]:
                return True
            if isinstance(n, LatexMacroNode):
                if (
                    n.macroname in COMMANDS["segment_args"]
                    or n.macroname in COMMANDS["caption_wrappers"]
                    or n.macroname in _INPUT_CMDS
                ):
                    return True
                if (
                    n.macroname in COMMANDS["wrappers"]
                    and n.nodeargd
                    and n.nodeargd.argnlist
                    and isinstance(n.nodeargd.argnlist[-1], LatexGroupNode)
                    and cls._has_block_content(n.nodeargd.argnlist[-1].nodelist)
                ):
                    return True
            if isinstance(n, LatexGroupNode) and cls._has_block_content(n.nodelist):
                return True
        return False

    @classmethod
    def _has_halign(cls, nodes: list) -> bool:
        for n in nodes:
            if isinstance(n, LatexMacroNode):
                if n.macroname in _HALIGN_CMDS:
                    return True
                if n.nodeargd and n.nodeargd.argnlist:
                    for a in n.nodeargd.argnlist:
                        if isinstance(a, LatexGroupNode) and cls._has_halign(a.nodelist):
                            return True
            elif isinstance(n, LatexGroupNode) and cls._has_halign(n.nodelist):
                return True
        return False

    @classmethod
    def _has_unescaped_script(cls, nodes: list) -> bool:
        for n in nodes:
            if isinstance(n, LatexCommentNode):
                continue
            if isinstance(n, LatexMathNode) or (
                isinstance(n, LatexEnvironmentNode) and n.environmentname in COMMANDS["math_envs"]
            ):
                continue
            if isinstance(n, LatexSpecialsNode):
                if n.specials_chars in ("_", "^"):
                    return True
            elif isinstance(n, LatexCharsNode):
                if "_" in n.chars or "^" in n.chars:
                    return True
            elif isinstance(n, LatexGroupNode):
                if cls._has_unescaped_script(n.nodelist):
                    return True
            elif isinstance(n, LatexMacroNode):
                name = n.macroname
                if (
                    (name in COMMANDS["paired"] or name in COMMANDS["wrappers"])
                    and n.nodeargd
                    and n.nodeargd.argnlist
                    and isinstance(n.nodeargd.argnlist[-1], LatexGroupNode)
                ):
                    if cls._has_unescaped_script(n.nodeargd.argnlist[-1].nodelist):
                        return True
        return False

    def _emit(self, kind, nodes, file, src, parent=None) -> Segment | None:
        nodes = self._trim_nodes(nodes)
        while (  # \section{\texorpdfstring{text}{pdf}}: the first argument is the text
            len(nodes) == 1
            and isinstance(nodes[0], LatexMacroNode)
            and nodes[0].macroname == "texorpdfstring"
            and nodes[0].nodeargd
            and nodes[0].nodeargd.argnlist
            and isinstance(nodes[0].nodeargd.argnlist[0], LatexGroupNode)
        ):
            nodes = self._trim_nodes(nodes[0].nodeargd.argnlist[0].nodelist)
        if kind in ("paragraph", "item"):
            while (
                len(nodes) == 1
                and isinstance(nodes[0], LatexMacroNode)
                and nodes[0].macroname in COMMANDS["wrappers"]
                and nodes[0].nodeargd
                and nodes[0].nodeargd.argnlist
                and isinstance(nodes[0].nodeargd.argnlist[-1], LatexGroupNode)
            ):
                nodes = self._trim_nodes(nodes[0].nodeargd.argnlist[-1].nodelist)
        if not nodes:
            return None
        if self._has_halign(nodes):
            self._record_halign(file, src[nodes[0].pos:nodes[-1].pos + nodes[-1].len])
            return None
        if self._has_unescaped_script(nodes):
            return None
        start = nodes[0].pos + (
            len(nodes[0].chars) - len(nodes[0].chars.lstrip()) if isinstance(nodes[0], LatexCharsNode) else 0
        )
        last = nodes[-1]
        end = last.pos + (len(last.chars.rstrip()) if isinstance(last, LatexCharsNode) else last.len)
        holder: list = []
        text, ph = self._inline(nodes, file, src, holder)
        text, ph, s_remap = _merge_placeholder_runs(text, ph)
        if _letters(text) < 1:
            return None
        seg = self._new(kind, file, self._orig_pos(file, start), self._orig_pos(file, end), text, ph, parent)
        raw_src = self.raw_sources[file]
        for c, macro_node, arg in holder:
            c = s_remap.get(c, c)
            child = self._emit("footnote", arg.nodelist, file, src, seg.id)
            if child:
                seg.ph[c].prefix = raw_src[self._orig_pos(file, macro_node.pos):child.start]
                seg.ph[c].suffix = raw_src[child.end:self._orig_pos(file, arg.pos + arg.len)]
                seg.ph[c].child = child.id
            else:  # empty footnote: keep verbatim
                seg.ph[c] = Placeholder(c, raw=self._orig_slice(file, macro_node.pos, arg.pos + arg.len))
        return seg

    def _walk_file(self, rel: str) -> None:
        self.visited_files.add(rel)
        src = self.sources[rel]
        src_blanked = _IFFALSE.sub(lambda m: " " * len(m.group(0)), src)
        nodes = parse(src_blanked, self.ctx)
        self.body(nodes, rel, src)

    def body(self, nodes, file, src):
        para: list = []

        def flush():
            if para:
                self._emit("paragraph", list(para), file, src)
                para.clear()

        i = 0
        while i < len(nodes):
            n = nodes[i]
            if isinstance(n, LatexCharsNode) and _BLANK_LINE.search(n.chars):
                pieces = _BLANK_LINE.split(n.chars)
                head = LatexCharsNode(
                    parsing_state=n.parsing_state, chars=pieces[0], pos=n.pos, len=len(pieces[0])
                )
                para.append(head)
                flush()
                tail = pieces[-1]
                tail_pos = n.pos + n.len - len(tail)
                para.append(
                    LatexCharsNode(
                        parsing_state=n.parsing_state, chars=tail, pos=tail_pos, len=len(tail)
                    )
                )
                i += 1
                continue
            if isinstance(n, LatexMacroNode) and n.macroname in _INPUT_CMDS:
                flush()
                arg = n.nodeargd.argnlist[-1] if n.nodeargd and n.nodeargd.argnlist else None
                if arg is not None:
                    raw_arg = src[arg.pos:arg.pos + arg.len]
                    if raw_arg.startswith("{") and raw_arg.endswith("}"):
                        raw_arg = raw_arg[1:-1]
                    child = self._resolve_input(raw_arg)
                    if child and child not in self.visited_files:
                        self._walk_file(child)
                i += 1
                continue
            if isinstance(n, LatexMacroNode) and n.macroname in _DEF_CMDS:
                flush()
                i += 1
                continue
            if isinstance(n, LatexMacroNode) and n.macroname in _DEF_MACROS:
                flush()
                i += 1
                while i < len(nodes):
                    if isinstance(nodes[i], LatexGroupNode):
                        i += 1
                        break
                    i += 1
                continue
            if isinstance(n, LatexMacroNode) and n.macroname in ("halign", "valign"):
                flush()
                start_pos = n.pos
                i += 1
                while i < len(nodes):
                    if isinstance(nodes[i], LatexGroupNode):
                        end_pos = nodes[i].pos + nodes[i].len
                        self._record_halign(file, src[start_pos:end_pos])
                        i += 1
                        break
                    i += 1
                else:
                    self._record_halign(file, src[start_pos:n.pos + n.len])
                continue
            if isinstance(n, LatexMacroNode) and n.macroname in COMMANDS["segment_args"]:
                flush()
                arg = n.nodeargd.argnlist[-1] if n.nodeargd and n.nodeargd.argnlist else None
                if arg is not None:
                    kind = (
                        "caption"
                        if n.macroname in ("caption", "captionof")
                        else ("title" if n.macroname == "title" else "heading")
                    )
                    if n.macroname in ("section", "chapter"):
                        self.section = decode_chars(
                            "".join(getattr(c, "chars", "") for c in arg.nodelist)
                        ).strip()
                    self._emit(kind, arg.nodelist, file, src)
                i += 1
                continue
            if isinstance(n, LatexMacroNode) and n.macroname in COMMANDS["caption_wrappers"]:
                flush()
                args = n.nodeargd.argnlist if n.nodeargd and n.nodeargd.argnlist else []
                if n.macroname == "subcaptionbox":
                    cap = args[2] if len(args) > 2 else None
                    content = args[5] if len(args) > 5 else None
                else:  # subfloat
                    cap = args[1] if len(args) > 1 and args[1] is not None else (args[0] if args else None)
                    content = args[2] if len(args) > 2 else None
                if isinstance(cap, LatexGroupNode):
                    self._emit("caption", cap.nodelist, file, src)
                if isinstance(content, LatexGroupNode):
                    self.body(content.nodelist, file, src)
                i += 1
                continue
            if isinstance(n, LatexMacroNode) and n.macroname in COMMANDS["wrappers"]:
                arg = n.nodeargd.argnlist[-1] if n.nodeargd and n.nodeargd.argnlist else None
                if isinstance(arg, LatexGroupNode) and self._has_block_content(arg.nodelist):
                    flush()
                    self.body(arg.nodelist, file, src)
                    i += 1
                    continue
            if isinstance(n, LatexMacroNode) and n.macroname in COMMANDS["rules"]:
                flush()
                i += 1
                continue
            if isinstance(n, LatexEnvironmentNode) and n.environmentname not in COMMANDS["math_envs"]:
                flush()
                self.environment(n, file, src)
                i += 1
                continue
            para.append(n)
            i += 1
        flush()

    def environment(self, n, file, src):
        name = n.environmentname
        if name in COMMANDS["body_envs"] or name in self.known_envs:
            self.body(n.nodelist, file, src)
        elif name in COMMANDS["list_envs"]:
            item: list = []
            for c in n.nodelist:
                if isinstance(c, LatexMacroNode) and c.macroname == "item":
                    if item:
                        self._emit("item", item, file, src)
                    item = []
                    continue
                item.append(c)
            if item:
                self._emit("item", item, file, src)
        elif name in COMMANDS["table_envs"]:
            return
        elif name in COMMANDS["opaque_envs"]:
            return
        else:
            rep = self.unknown.setdefault(
                "env:" + name,
                {
                    "name": "env:" + name,
                    "count": 0,
                    "example": src[n.pos:n.pos + min(n.len, 60)],
                    "file": file,
                    "action": "keep",
                },
            )
            rep["count"] += 1

    def run(self):
        main = self.project["main"]
        src = self.sources[main]
        src_blanked = _IFFALSE.sub(lambda m: " " * len(m.group(0)), src)
        begin_pos = _find_begin_doc(src_blanked)

        def emit_macros():
            for idx, rel in enumerate(self.project["files"]):
                for m in self.macros:
                    if m.text_index == idx and is_text_macro(m, self.math_macros):
                        s, e = m.body_span
                        self._new(
                            "macro",
                            rel,
                            self._orig_pos(rel, s),
                            self._orig_pos(rel, e),
                            decode_chars(m.body).strip(),
                            {},
                        )

        self.visited_files.add(main)
        if begin_pos >= 0:
            pre_nodes = parse(src_blanked[:begin_pos], self.ctx)
            for n in pre_nodes:
                if (
                    isinstance(n, LatexMacroNode)
                    and n.macroname == "title"
                    and n.nodeargd
                    and n.nodeargd.argnlist
                    and n.nodeargd.argnlist[-1] is not None
                ):
                    self._emit("title", n.nodeargd.argnlist[-1].nodelist, main, src)
            emit_macros()
            body_src = " " * begin_pos + src_blanked[begin_pos:]
            body_top = parse(body_src, self.ctx)
            body_nodes = None
            for n in body_top:
                if isinstance(n, LatexEnvironmentNode) and n.environmentname == "document":
                    body_nodes = n.nodelist
                    break
            self.body(body_nodes or [], main, src)
        else:
            emit_macros()
            nodes = parse(src_blanked, self.ctx)
            self.body(nodes, main, src)
        unknown = list(self.unknown.values())
        return self.segments, unknown


def attach_raw(segments: list[Segment], src_dir: Path, encodings: dict | None = None) -> None:
    texts: dict[str, str] = {}
    for s in segments:
        if s.file not in texts:
            texts[s.file] = read_source(Path(src_dir) / s.file, (encodings or {}).get(s.file))[0]
        s._raw = texts[s.file][s.start:s.end]


def extract_segments(src_dir: Path, project: dict) -> tuple[list[Segment], list[dict]]:
    segs, unknown = _Extractor(Path(src_dir), project).run()
    attach_raw(segs, src_dir, project.get("encodings"))
    return segs, unknown


def _has_translated_child(
    seg: Segment, translations: dict[str, str], by_id: dict[str, Segment]
) -> bool:
    for p in seg.ph.values():
        if p.child:
            child = by_id[p.child]
            if translations.get(child.id) or _has_translated_child(child, translations, by_id):
                return True
    return False


def _expand(
    seg: Segment,
    text: str,
    translations: dict[str, str],
    by_id: dict[str, Segment],
    use_own_text: bool,
) -> str:
    out = []
    pos = 0
    for m in _PH.finditer(text):
        out.append(encode_text(text[pos:m.start()]))
        tok = m.group(0)
        closing = tok.startswith("⟦/")
        code = tok[2:-1] if closing else tok[1:-1]
        p = seg.ph[code]
        if closing:
            out.append(p.close)
        elif p.open:
            out.append(p.open)
        elif p.child:
            child = by_id[p.child]
            out.append(p.prefix + _render(child, translations, by_id, use_own_text) + p.suffix)
        else:
            out.append(p.raw)
        pos = m.end()
    out.append(encode_text(text[pos:]))
    return "".join(out)


def _render(
    seg: Segment,
    translations: dict[str, str],
    by_id: dict[str, Segment],
    use_own_text: bool,
) -> str:
    if use_own_text:
        return _expand(seg, seg.text, translations, by_id, use_own_text=True)
    text = translations.get(seg.id, "")
    if text:
        return _expand(seg, text, translations, by_id, use_own_text=False)
    if _has_translated_child(seg, translations, by_id):
        return _expand(seg, seg.text, translations, by_id, use_own_text=False)
    return seg._raw


def render(seg: Segment, translations: dict[str, str], by_id: dict[str, Segment]) -> str:
    return _render(seg, translations, by_id, use_own_text=False)


def rebuild_from_text(seg: Segment, by_id: dict[str, Segment]) -> str:
    return _render(seg, {}, by_id, use_own_text=True)

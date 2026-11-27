import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from importlib import resources

from pylatexenc.latexwalker import LatexWalker, get_default_latex_context_db
from pylatexenc.macrospec import EnvironmentSpec, MacroSpec

COMMANDS = json.loads(resources.files("rosettex.data").joinpath("commands.json").read_text(encoding="utf-8"))

_NEWCMD = re.compile(
    r"\\(?:newcommand|renewcommand|providecommand|DeclareRobustCommand)\*?\s*"
    r"(?:\{\s*\\([A-Za-z@]+)\s*\}|\\([A-Za-z@]+))\s*(?:\[(\d)\])?\s*(\[)?")
_DEF = re.compile(r"\\(?:def|gdef|edef|xdef)\s*\\([A-Za-z@]+)((?:#\d)*)\s*\{")
_MATHOP = re.compile(r"\\DeclareMathOperator\*?\s*\{\s*\\([A-Za-z@]+)\s*\}\s*\{")
_NEWENV = re.compile(r"\\(?:newenvironment|renewenvironment)\*?\s*\{([A-Za-z@*]+)\}\s*(?:\[(\d)\])?")
_NEWTHM = re.compile(r"\\newtheorem\*?\s*\{([A-Za-z@*]+)\}")


@dataclass
class AuthorMacro:
    name: str
    nargs: int
    optional: bool
    body: str
    kind: str  # "macro" | "env" | "mathop"
    body_span: tuple[int, int] | None = None
    text_index: int = 0


def _group_end(text: str, open_pos: int) -> int:
    """Index just after the brace group starting at text[open_pos] == '{'; -1 if unbalanced."""
    depth = 0
    i = open_pos
    while i < len(text):
        c = text[i]
        if c == "\\":
            i += 2
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return -1


def _skip_optional(text: str, i: int) -> int:
    while i < len(text) and text[i].isspace():
        i += 1
    if i < len(text) and text[i] == "[":
        j = text.find("]", i)
        return j + 1 if j > 0 else i
    return i


def _body_after(text: str, i: int) -> tuple[str, tuple[int, int]] | None:
    while i < len(text) and text[i].isspace():
        i += 1
    if i >= len(text) or text[i] != "{":
        return None
    end = _group_end(text, i)
    if end < 0:
        return None
    return text[i + 1:end - 1], (i + 1, end - 1)


def _blank_comments(text: str) -> str:
    """Replace comments with spaces so offsets stay valid."""
    return re.sub(
        r"(?<!\\)((?:\\\\)*)(%[^\n]*)",
        lambda m: m.group(1) + " " * len(m.group(2)),
        text,
    )


def scan_author_macros(texts: list[str]) -> list[AuthorMacro]:
    found: dict[str, AuthorMacro] = {}
    for idx, raw in enumerate(texts):
        text = _blank_comments(raw)
        for m in _NEWCMD.finditer(text):
            name = m.group(1) or m.group(2)
            nargs = int(m.group(3) or 0)
            opt = bool(m.group(4))
            start = m.end() - (1 if opt else 0)
            start = _skip_optional(text, start) if opt else start
            body = _body_after(text, start)
            if body:
                found.setdefault(name, AuthorMacro(name, nargs, opt, body[0], "macro", body[1], idx))
        for m in _DEF.finditer(text):
            body = _body_after(text, m.end() - 1)
            if body:
                found.setdefault(m.group(1), AuthorMacro(m.group(1), m.group(2).count("#"), False, body[0],
                                                         "macro", body[1], idx))
        for m in _MATHOP.finditer(text):
            body = _body_after(text, m.end() - 1)
            found.setdefault(m.group(1), AuthorMacro(m.group(1), 0, False, body[0] if body else "", "mathop"))
        for m in _NEWENV.finditer(text):
            found.setdefault("env:" + m.group(1), AuthorMacro(m.group(1), int(m.group(2) or 0), False, "", "env"))
        for m in _NEWTHM.finditer(text):
            found.setdefault("env:" + m.group(1), AuthorMacro(m.group(1), 0, False, "", "env"))
    return [AuthorMacro(**{**vars(m)}) for m in found.values()]


_TEX_DIM = re.compile(r"^-?\d+(?:\.\d+)?\s*(?:pt|pc|in|bp|cm|mm|dd|cc|sp|ex|em|mu|px)$")
_TWO_WORDS = re.compile(r"[^\W\d_]\s+[^\W\d_]")
_MATH_ENV_NAMES = {
    *COMMANDS["math_envs"],
    "math",
    "displaymath",
    "split",
    "cases",
    "matrix",
    "pmatrix",
    "bmatrix",
    "vmatrix",
    "Vmatrix",
    "aligned",
    "gathered",
    "flalign",
    "alignat",
    "subequations",
    "smallmatrix",
}
_BASE_MATH_ENVS = {name.rstrip("*") for name in _MATH_ENV_NAMES}
_MATH_ENV_NAMES_PAT = "|".join(re.escape(b) for b in sorted(_BASE_MATH_ENVS, key=len, reverse=True))
_MATH_ENV_PAT = re.compile(rf"\\begin\{{({_MATH_ENV_NAMES_PAT})\*?\}}(.*?)\\end\{{\1\*?\}}", re.S)
_DOLLAR_PAT = re.compile(r"(?<!\\)\$\$(.*?)(?<!\\)\$\$|(?<!\\)\$(.*?)(?<!\\)\$|\\\[(.*?)\\\]|\\\((.*?)\\\)", re.S)
_MACRO_NAME_PAT = re.compile(r"\\([A-Za-z@]+)")
_ENSUREMATH_PAT = re.compile(r"\\ensuremath\s*\{")


def find_math_macros(texts: Iterable[str]) -> set[str]:
    math_macros: set[str] = set()
    for raw in texts:
        text = _blank_comments(raw)
        for m in _MATH_ENV_PAT.finditer(text):
            for mac in _MACRO_NAME_PAT.finditer(m.group(2)):
                math_macros.add(mac.group(1))
        cleaned = _MATH_ENV_PAT.sub(" ", text)
        for m in _DOLLAR_PAT.finditer(cleaned):
            body = m.group(1) or m.group(2) or m.group(3) or m.group(4) or ""
            for mac in _MACRO_NAME_PAT.finditer(body):
                math_macros.add(mac.group(1))
        for m in _ENSUREMATH_PAT.finditer(cleaned):
            end = _group_end(cleaned, m.end() - 1)
            if end > 0:
                for mac in _MACRO_NAME_PAT.finditer(cleaned[m.end() : end - 1]):
                    math_macros.add(mac.group(1))
    return math_macros


def is_text_macro(m: AuthorMacro, math_macros: set[str] | Iterable[str] | None = None) -> bool:
    if m.kind != "macro" or m.nargs or m.body_span is None:
        return False
    if math_macros is not None:
        if isinstance(math_macros, set) and all("\n" not in s and " " not in s for s in math_macros):
            if m.name in math_macros:
                return False
        else:
            mm = find_math_macros(math_macros)
            if m.name in mm:
                return False
    body = m.body.strip()
    if _TEX_DIM.match(body) or "://" in body:
        return False
    if not _TWO_WORDS.search(body):
        return False
    return not bool(re.search(r"[\\$#^_{}&]", body))


def _argspec(nargs: int, optional: bool) -> str:
    if optional:
        return "[" + "{" * max(nargs - 1, 0)
    return "{" * nargs


def build_context(macros: list[AuthorMacro]):
    db = get_default_latex_context_db()
    specs: dict[str, str] = {}
    for table in ("segment_args", "nested", "paired", "wrappers", "caption_wrappers", "refs", "kept"):
        specs.update(COMMANDS[table])
    mspecs = [MacroSpec(name, spec) for name, spec in specs.items()]
    envs = []
    for m in macros:
        if m.kind == "env":
            envs.append(EnvironmentSpec(m.name, "{" * m.nargs))
        elif m.name not in specs:
            mspecs.append(MacroSpec(m.name, _argspec(m.nargs, m.optional)))
    envs += [
        EnvironmentSpec(name, "[{")
        for name in COMMANDS["table_envs"]
        if name not in ("tabular*", "tabularx", "tabulary")
    ]
    envs.append(EnvironmentSpec("tabular*", "{[{"))
    envs.append(EnvironmentSpec("tabularx", "{[{"))
    envs.append(EnvironmentSpec("tabulary", "{[{"))
    envs.append(EnvironmentSpec("minipage", "[[[{"))
    envs.append(EnvironmentSpec("subfigure", "[[[{"))
    envs.append(EnvironmentSpec("subtable", "[[[{"))
    envs.append(EnvironmentSpec("wrapfigure", "[{{"))
    envs.append(EnvironmentSpec("wraptable", "[{{"))
    envs.append(EnvironmentSpec("adjustbox", "{"))
    envs.append(EnvironmentSpec("threeparttable", "["))
    envs.append(EnvironmentSpec("CJK", "[{{"))  # \begin{CJK}[enc]{UTF8}{family}: the args are not text
    envs.append(EnvironmentSpec("CJK*", "[{{"))
    envs.append(EnvironmentSpec("tablenotes", "["))
    envs.append(EnvironmentSpec("sidewaystable", "["))
    envs.append(EnvironmentSpec("sidewaystable*", "["))
    envs.append(EnvironmentSpec("sidewaysfigure", "["))
    envs.append(EnvironmentSpec("sidewaysfigure*", "["))
    db.add_context_category("rosettex", prepend=True, macros=mspecs, environments=envs)
    return db


def parse(text: str, ctx) -> list:
    nodes, _, _ = LatexWalker(text, latex_context=ctx, tolerant_parsing=True).get_latex_nodes()
    return [n for n in nodes if n is not None]

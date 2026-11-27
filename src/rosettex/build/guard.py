import re
from pathlib import Path

from rosettex.source import strip_comments
from rosettex.textio import read_source

_DRIVE_ABS = re.compile(r"^[A-Za-z]:")

# Lua execution in paper source (could bypass LuaTeX file-open callbacks).
_LUA_PATTERNS = [
    re.compile(r"\\(?:directlua|luaexec|luadirect|latelua)(?![A-Za-z@])"),
    re.compile(r"\\begin\s*\{luacode\*?\}"),
]

# Single-argument path commands with optional * and optional [...] args.
_SINGLE_ARG_CMDS = (
    "include",
    "subfile",
    "IfFileExists",
    "InputIfFileExists",
    "includegraphics",
    "includepdf",
    "lstinputlisting",
    "verbatiminput",
    "VerbatimInput",
    "addbibresource",
)
_RE_SINGLE = re.compile(
    r"\\(?:"
    + "|".join(_SINGLE_ARG_CMDS)
    + r")(?![A-Za-z@])\*?(?:\s*\[[^\]]*\])*\s*\{([^}]*)\}"
)

# \input supports both LaTeX \input{path} and TeX primitive \input path / \input"path".
_RE_INPUT = re.compile(
    r"\\input(?![A-Za-z@])(?:\s*\{([^}]*)\}|\s*\"([^\"]*)\"|[ \t]+([^\s{}%]+?)(?=\s|[{}%]|\\[A-Za-z@]|$))"
)

# \import{dir}{file} and \subimport{dir}{file} — both arguments are paths.
_RE_IMPORT = re.compile(r"\\(?:sub)?import(?![A-Za-z@])\*?\s*\{([^}]*)\}\s*\{([^}]*)\}")

# \inputminted[opts]{lang}{file} — second brace argument is the path.
_RE_INPUTMINTED = re.compile(r"\\inputminted(?![A-Za-z@])(?:\s*\[[^\]]*\])?\s*\{[^}]*\}\s*\{([^}]*)\}")

# \bibliography{a,b,c} — comma-separated paths.
_RE_BIB = re.compile(r"\\bibliography(?![A-Za-z@])\s*\{([^}]*)\}")

# \openin\stream=path or \openin1=path or \openin\stream path
_RE_OPENIN = re.compile(
    r"\\openin(?![A-Za-z@])\s*(?:\\[A-Za-z@]+|\d+)\s*=?\s*"
    r"(?:\{([^}]*)\}|\"([^\"]*)\"|([^\s{}%]+?)(?=\s|[{}%]|\\[A-Za-z@]|$))"
)


def guard_lua_path() -> Path:
    return Path(__file__).with_name("guard.lua")


def _is_bad_path(raw: str) -> bool:
    p = raw.strip().strip('"').strip("'").strip()
    if not p:
        return False
    if p.startswith("/") or p.startswith("~") or p.startswith("\\\\"):
        return True
    if _DRIVE_ABS.match(p):
        return True
    if ".." in p:
        return True
    return False


def _line_of(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def scan(build_dir: Path, files: list[str], allow_lua_files: frozenset[str] | set[str] = frozenset()) -> list[dict]:
    """Static pre-scan of .tex files for forbidden file paths and Lua execution.

    ``allow_lua_files``: build-dir-relative names of rosettex-generated files whose Lua calls are not reported
    (only the PDF overlay build passes ``{"overlay.tex"}``); path findings still apply to them."""
    build_dir = Path(build_dir)
    findings: list[dict] = []

    for rel in files:
        path = build_dir / rel
        if not path.is_file():
            continue
        raw, _ = read_source(path)
        text = strip_comments(raw)
        file_findings: list[tuple[int, dict]] = []

        for pat in () if rel in allow_lua_files else _LUA_PATTERNS:
            for m in pat.finditer(text):
                file_findings.append(
                    (
                        m.start(),
                        {
                            "file": rel,
                            "line": _line_of(text, m.start()),
                            "kind": "lua",
                            "text": m.group(0).strip(),
                        },
                    )
                )

        for m in _RE_SINGLE.finditer(text):
            if _is_bad_path(m.group(1)):
                file_findings.append(
                    (
                        m.start(),
                        {
                            "file": rel,
                            "line": _line_of(text, m.start()),
                            "kind": "path",
                            "text": m.group(0).strip(),
                        },
                    )
                )

        for m in _RE_INPUT.finditer(text):
            arg = m.group(1) if m.group(1) is not None else (m.group(2) if m.group(2) is not None else m.group(3))
            if arg is not None and _is_bad_path(arg):
                file_findings.append(
                    (
                        m.start(),
                        {
                            "file": rel,
                            "line": _line_of(text, m.start()),
                            "kind": "path",
                            "text": m.group(0).strip(),
                        },
                    )
                )

        for m in _RE_IMPORT.finditer(text):
            if _is_bad_path(m.group(1)) or _is_bad_path(m.group(2)):
                file_findings.append(
                    (
                        m.start(),
                        {
                            "file": rel,
                            "line": _line_of(text, m.start()),
                            "kind": "path",
                            "text": m.group(0).strip(),
                        },
                    )
                )

        for m in _RE_INPUTMINTED.finditer(text):
            if _is_bad_path(m.group(1)):
                file_findings.append(
                    (
                        m.start(),
                        {
                            "file": rel,
                            "line": _line_of(text, m.start()),
                            "kind": "path",
                            "text": m.group(0).strip(),
                        },
                    )
                )

        for m in _RE_BIB.finditer(text):
            items = [item.strip() for item in m.group(1).split(",")]
            if any(_is_bad_path(item) for item in items):
                file_findings.append(
                    (
                        m.start(),
                        {
                            "file": rel,
                            "line": _line_of(text, m.start()),
                            "kind": "path",
                            "text": m.group(0).strip(),
                        },
                    )
                )

        for m in _RE_OPENIN.finditer(text):
            arg = m.group(1) if m.group(1) is not None else (m.group(2) if m.group(2) is not None else m.group(3))
            if arg is not None and _is_bad_path(arg):
                file_findings.append(
                    (
                        m.start(),
                        {
                            "file": rel,
                            "line": _line_of(text, m.start()),
                            "kind": "path",
                            "text": m.group(0).strip(),
                        },
                    )
                )

        file_findings.sort(key=lambda pair: pair[0])
        findings.extend(item for _, item in file_findings)

    return findings

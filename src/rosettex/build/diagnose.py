import difflib
import re
from pathlib import Path

from rosettex.build.preamble import is_otf_font
from rosettex.segment import Segment
from rosettex.textio import read_source

_FILE_LINE_ERR = re.compile(
    r"^((?:\.\.?/|/|~|[A-Za-z]:[/\\]|[^\s:()\[\]]+\.[A-Za-z0-9]+)[^:\n]*):(\d+): (.+)$"
)
_BANG_ERR = re.compile(r"^! (.+)$")
_L_LINE = re.compile(r"^l\.(\d+)(?: (.*))?$")
_MISSING_CHAR = re.compile(r"Missing character: There is no (.+?)(?: \(U\+([0-9A-Fa-f]+)\))? in font (.+?)!$")
_FILE_OPEN = re.compile(r"\(((?:\.\.?/|/|[A-Za-z]:[/\\])[^\s()\"]+)")
_GENERATED_FILES = frozenset({"rosettex-preamble.tex", "rosettex-minted.sty", "rosettex-fallback.sty", "rosettex-marks.sty"})
_SHIM_INSERTED = ("\\RequirePackage{luatex85}", "\\input{rosettex-preamble}")
_BEGINDOC_HOOK = re.compile(r"\\AtBeginDocument\b|\\AddToHook\s*\{\s*begindocument|\\@begindocumenthook\b")
_PKG_LOAD = re.compile(r"\\(?:RequirePackage|usepackage|LoadClass)\b")
_COMMENT_PREFIX = re.compile(r"(?<!\\)(?:\\\\)*%")
_MAX_ENTRIES = 20
_MAX_MISSING_SEGMENTS = 10


def _norm_path(raw: str, build_dir: Path) -> tuple[str, bool]:
    """Return (normalized_path, is_in_build_dir)."""
    cleaned = raw.strip().replace("\\", "/")
    if cleaned.startswith("./"):
        cleaned = cleaned[2:]
    p = Path(cleaned)
    if p.is_absolute():
        try:
            rel = p.resolve().relative_to(build_dir.resolve()).as_posix()
            return rel, True
        except ValueError:
            return cleaned, False
    if (build_dir / cleaned).is_file():
        return cleaned, True
    return cleaned, False


def _context_lines(path: Path, line_no: int | None, width: int = 10) -> list[str]:
    if not line_no or line_no < 1 or not path.is_file():
        return []
    text, _ = read_source(path)
    lines = text.splitlines()
    if not lines:
        return []
    idx = min(max(line_no - 1, 0), len(lines) - 1)
    start = max(0, idx - (width // 2 - 1))
    end = min(len(lines), start + width)
    if end - start < width:
        start = max(0, end - width)
    return [f"{i + 1}: {lines[i]}" for i in range(start, end)]


def _map_build_line_to_assembled(
    asm_lines: list[str], build_lines: list[str], build_line: int
) -> tuple[int | None, bool]:
    """Map 1-based build_line to 0-based assembled line index.

    Returns (asm_idx, is_inserted_shim_line).
    """
    if not build_lines or not asm_lines:
        return None, False
    b_idx = min(max(build_line - 1, 0), len(build_lines) - 1)
    sm = difflib.SequenceMatcher(None, asm_lines, build_lines, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if not (j1 <= b_idx < j2):
            continue
        if tag == "equal":
            return i1 + (b_idx - j1), False
        if tag == "replace":
            return i1 + min(b_idx - j1, max(0, i2 - i1 - 1)), False
        if tag == "insert":
            stripped = build_lines[b_idx].strip()
            if any(stripped.startswith(prefix) for prefix in _SHIM_INSERTED):
                return None, True
            return min(i1, len(asm_lines) - 1), False
    return min(b_idx, len(asm_lines) - 1), False


def _find_edit_for_line(
    asm_lines: list[str],
    asm_idx: int,
    edits: list[dict],
    snippet: str | None,
) -> tuple[dict | None, int]:
    """Return (matching_edit_or_None, char_offset_in_assembled)."""
    line_start = sum(len(line) for line in asm_lines[:asm_idx])
    line_text = asm_lines[asm_idx]
    content_len = len(line_text.rstrip("\r\n"))
    content_end = line_start + content_len

    if snippet:
        probe = snippet[3:] if snippet.startswith("...") else snippet
        probe = probe.strip()
        if probe:
            pos = line_text.find(probe)
            if pos >= 0:
                col_end = line_start + pos + len(probe)
                probe_off = max(line_start, col_end - 1)
                for ed in edits:
                    out_s, out_e = ed["out"]
                    if out_s <= probe_off < out_e:
                        return ed, probe_off
                return None, probe_off

    best_ed: dict | None = None
    best_overlap = 0
    for ed in edits:
        out_s, out_e = ed["out"]
        if content_len == 0:
            if out_s <= line_start < out_e:
                return ed, line_start
        else:
            overlap = min(content_end, out_e) - max(line_start, out_s)
            if overlap > best_overlap:
                best_overlap = overlap
                best_ed = ed
    if best_ed is not None:
        return best_ed, max(line_start, best_ed["out"][0])
    return None, line_start


def _source_line_for_assembled_offset(
    assembled_text: str,
    offset: int,
    edits: list[dict],
    by_id: dict[str, Segment],
) -> int:
    """Compute 1-based line number in effective source for an assembled offset at/outside edits."""
    asm_nl = assembled_text[:offset].count("\n")
    nl_delta = 0
    for ed in edits:
        out_s, out_e = ed["out"]
        if out_e <= offset:
            out_nl = assembled_text[out_s:out_e].count("\n")
            seg = by_id.get(ed["seg"])
            src_nl = seg._raw.count("\n") if seg is not None else out_nl
            nl_delta += src_nl - out_nl
        else:
            break
    return max(1, asm_nl + nl_delta + 1)


def _strip_comment_line(line: str) -> str:
    m = _COMMENT_PREFIX.search(line)
    return line[: m.start()] if m else line


def _find_begindoc_culprit(
    build_dir: Path,
    loaded_local_files: list[str],
    main: str,
    message: str,
) -> tuple[str, int] | None:
    """Find a local .sty/.cls opened before an error at \\begin{document} that registers a hook."""
    want_pkg_load = "Loading a class or package" in message
    hook_hits: list[tuple[str, int, int | None]] = []
    for rel in reversed(loaded_local_files):
        if rel == main or not rel.endswith((".sty", ".cls")):
            continue
        p = build_dir / rel
        if not p.is_file():
            continue
        text, _ = read_source(p)
        hook_line: int | None = None
        pkg_line: int | None = None
        for idx, line in enumerate(text.splitlines(), 1):
            code = _strip_comment_line(line)
            if hook_line is None and _BEGINDOC_HOOK.search(code):
                hook_line = idx
            if want_pkg_load and pkg_line is None and _PKG_LOAD.search(code):
                pkg_line = idx
        if hook_line is not None:
            hook_hits.append((rel, hook_line, pkg_line))
    if want_pkg_load:
        for rel, _hook_line, pkg_line in hook_hits:
            if pkg_line is not None:
                return rel, pkg_line
    if hook_hits:
        rel, hook_line, _ = hook_hits[0]
        return rel, hook_line
    return None


def _update_file_stack(
    line: str,
    stack: list[str | None],
    build_dir: Path,
    loaded_local: list[str],
    seen_local: set[str],
) -> None:
    """Track TeX's open-file stack from log parentheses, balancing non-file parens."""
    i = 0
    n = len(line)
    while i < n:
        ch = line[i]
        if ch == "(":
            m = _FILE_OPEN.match(line, i)
            if m:
                raw = m.group(1)
                stack.append(raw)
                norm, in_build = _norm_path(raw, build_dir)
                if (
                    in_build
                    and norm not in _GENERATED_FILES
                    and not norm.startswith("rosettex-")
                    and norm not in seen_local
                ):
                    seen_local.add(norm)
                    loaded_local.append(norm)
                i = m.end()
                continue
            stack.append(None)
        elif ch == ")" and stack:
            stack.pop()
        i += 1


def _parse_log_errors(log_text: str, main: str, build_dir: Path) -> list[dict]:
    lines = log_text.splitlines()
    raw_errors: list[dict] = []
    file_stack: list[str | None] = []
    loaded_local: list[str] = []
    seen_local: set[str] = set()
    last_local_file: str | None = None
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        m_fl = _FILE_LINE_ERR.match(line)
        m_bang = None if m_fl else _BANG_ERR.match(line)
        if m_fl or m_bang:
            open_files = [f for f in file_stack if f is not None]
            innermost_open = open_files[-1] if open_files else None
            if m_fl:
                raw_file = m_fl.group(1)
                line_no: int | None = int(m_fl.group(2))
                message = m_fl.group(3).strip()
            else:
                assert m_bang is not None
                raw_file = innermost_open or main
                line_no = None
                message = m_bang.group(1).strip()

            snippet: str | None = None
            caller_line: int | None = None
            j = i + 1
            limit = min(n, i + 16)
            while j < limit:
                nxt = lines[j]
                if _FILE_LINE_ERR.match(nxt) or _BANG_ERR.match(nxt):
                    break
                m_l = _L_LINE.match(nxt)
                if m_l:
                    lno = int(m_l.group(1))
                    snip = (m_l.group(2) or "").strip()
                    if line_no is None:
                        line_no = lno
                        snippet = snip
                    elif lno == line_no and snippet is None:
                        snippet = snip
                    elif caller_line is None:
                        caller_line = lno
                j += 1

            local_on_stack = last_local_file
            for f in reversed(open_files):
                norm, in_build = _norm_path(f, build_dir)
                if in_build and norm not in _GENERATED_FILES and not norm.startswith("rosettex-"):
                    local_on_stack = norm
                    break

            raw_errors.append(
                {
                    "raw_file": raw_file,
                    "line_no": line_no,
                    "message": message,
                    "snippet": snippet,
                    "last_local_file": local_on_stack,
                    "caller_line": caller_line,
                    "open_file": innermost_open,
                    "loaded_local_files": list(loaded_local),
                }
            )
            i += 1
            continue

        _update_file_stack(line, file_stack, build_dir, loaded_local, seen_local)
        for f in reversed(file_stack):
            if f is None:
                continue
            norm, in_build = _norm_path(f, build_dir)
            if in_build and norm not in _GENERATED_FILES and not norm.startswith("rosettex-"):
                last_local_file = norm
                break
        i += 1
    return raw_errors


def _find_missing_char_location(
    build_dir: Path,
    candidates: list[str],
    char_set: set[str],
    first_char: str,
) -> tuple[str | None, int | None, str | None]:
    """Locate the first non-comment occurrence of a missing char in build_dir."""
    comment_hit: tuple[str, int, str] | None = None
    ordered_chars = [first_char] + [c for c in sorted(char_set, key=ord) if c != first_char]
    for rel in candidates:
        p = build_dir / rel
        if not p.is_file():
            continue
        text, _ = read_source(p)
        for line_no, line in enumerate(text.splitlines(), 1):
            code = _strip_comment_line(line)
            for ch in ordered_chars:
                if ch in code:
                    return rel, line_no, ch
            if comment_hit is None:
                for ch in ordered_chars:
                    if ch in line:
                        comment_hit = (rel, line_no, ch)
                        break
    return comment_hit if comment_hit else (None, None, None)


def _next_action_for(entry: dict) -> str:
    """Actionable hint for a diagnosis entry when not already populated."""
    if entry.get("next_action"):
        return entry["next_action"]
    ch = entry.get("channel")
    msg = entry.get("message", "")
    if ch == "fix":
        sf = entry.get("source_file") or entry.get("build_file") or "<source_file>"
        sl = entry.get("source_line") or entry.get("build_line")
        loc = f" around line {sl}" if sl else ""
        return f"Run 'rosettex fix <work> --file {sf}' and edit fix/{sf}{loc} to fix: {msg}"
    if ch == "translation":
        seg = entry.get("segment")
        if seg:
            return f"Append a corrected line '{seg} <translation>' to out/*.txt to fix: {msg}"
        return f"Fix the affected translated segment in out/*.txt to resolve: {msg}"
    sf = entry.get("source_file")
    sl = entry.get("source_line")
    if sf:
        loc = f":{sl}" if sl else ""
        return (
            f"Edit build.json (engine, preamble_extra, or shim_off) or inspect {sf}{loc} "
            f"(via 'rosettex fix <work> --file {sf}') to resolve: {msg}"
        )
    return f"Edit build.json (engine, preamble_extra, or shim_off) to resolve: {msg}"


def _diagnose_missing_chars(
    log_text: str,
    segments: list[Segment],
    translations: dict[str, str],
    build_dir: Path | None = None,
    main: str | None = None,
    map_files: dict[str, dict] | None = None,
    by_id: dict[str, Segment] | None = None,
    load_pair=None,
    map_local_line=None,
) -> list[dict]:
    per_font: dict[str, set[str]] = {}
    font_first_char: dict[str, str] = {}
    font_open_file: dict[str, str | None] = {}
    font_order: list[str] = []

    file_stack: list[str | None] = []
    loaded_local: list[str] = []
    seen_local: set[str] = set()
    last_local: str | None = main

    for line in log_text.splitlines():
        m = _MISSING_CHAR.search(line.strip())
        if m:
            ch_raw, code_hex, font = m.groups()
            ch = chr(int(code_hex, 16)) if code_hex else ch_raw
            if font not in per_font:
                per_font[font] = set()
                font_order.append(font)
                font_first_char[font] = ch
                open_local = last_local
                if build_dir is not None:
                    for f in reversed(file_stack):
                        if f is None:
                            continue
                        norm, in_build = _norm_path(f, build_dir)
                        if in_build and norm not in _GENERATED_FILES and not norm.startswith("rosettex-"):
                            open_local = norm
                            break
                font_open_file[font] = open_local
            per_font[font].add(ch)
            continue
        if build_dir is not None:
            _update_file_stack(line, file_stack, build_dir, loaded_local, seen_local)
            for f in reversed(file_stack):
                if f is None:
                    continue
                norm, in_build = _norm_path(f, build_dir)
                if in_build and norm not in _GENERATED_FILES and not norm.startswith("rosettex-"):
                    last_local = norm
                    break

    entries: list[dict] = []
    for font in font_order:
        chars = sorted(per_font[font], key=ord)
        char_set = per_font[font]
        seg_ids: list[str] = []
        for seg in segments:
            tr = translations.get(seg.id, "")
            if tr and any(c in char_set for c in tr):
                seg_ids.append(seg.id)
                if len(seg_ids) >= _MAX_MISSING_SEGMENTS:
                    break
        shown = ", ".join(f"{c} (U+{ord(c):04X})" for c in chars[:20])
        if len(chars) > 20:
            shown += f", ... (+{len(chars) - 20} more)"

        b_file: str | None = None
        b_line: int | None = None
        src_file: str | None = None
        src_line: int | None = None
        matched_seg: str | None = None
        ctx: list[str] = []
        open_file = font_open_file.get(font)

        if build_dir is not None:
            cands: list[str] = []
            for rel in [open_file, main, *(map_files or {}).keys()]:
                if rel and rel not in cands:
                    cands.append(rel)
            for bbl in sorted(build_dir.rglob("*.bbl")):
                rel = bbl.relative_to(build_dir).as_posix()
                if rel not in cands:
                    cands.append(rel)
            found_file, found_line, found_ch = _find_missing_char_location(
                build_dir, cands, char_set, font_first_char[font]
            )
            if found_file and found_line:
                b_file, b_line = found_file, found_line
                ctx = _context_lines(build_dir / b_file, b_line)
                if map_files and b_file in map_files and load_pair and by_id is not None:
                    asm_text, asm_lines, _, build_lines = load_pair(b_file)
                    asm_idx, is_shim = _map_build_line_to_assembled(asm_lines, build_lines, b_line)
                    if asm_idx is not None and not is_shim:
                        fe = map_files[b_file]
                        edits = fe.get("edits", [])
                        ed, off = _find_edit_for_line(asm_lines, asm_idx, edits, found_ch)
                        src_file = fe.get("source", b_file)
                        if ed is not None:
                            matched_seg = ed["seg"]
                            src_line = _source_line_for_assembled_offset(
                                asm_text, ed["out"][0], edits, by_id
                            )
                        else:
                            src_line = _source_line_for_assembled_offset(asm_text, off, edits, by_id)
                else:
                    src_file = b_file
                    src_line = map_local_line(b_file, b_line) if map_local_line else b_line
            elif open_file and (build_dir / open_file).is_file():
                b_file = open_file

        sample_ch = chars[0]
        cp_hex = f"{ord(sample_ch):04X}"
        shown_short = ", ".join(f"{c} (U+{ord(c):04X})" for c in chars[:5])
        target_file = src_file or b_file or main or "<source_file>"
        if seg_ids:
            seg_list = ", ".join(seg_ids)
            next_action = (
                f"Replace unsupported character(s) ({shown_short}) in translated segment(s) {seg_list} "
                f"(append corrected line to out/*.txt), or add a font covering them in build.json preamble_extra."
            )
        elif not is_otf_font(font):
            next_action = (
                f"Math font '{font}' lacks {shown_short}; add a math-active mapping to build.json preamble_extra, e.g. "
                f'"\\\\AtBeginDocument{{\\\\begingroup\\\\lccode`\\\\~=\\"{cp_hex}'
                f'\\\\lowercase{{\\\\endgroup\\\\providecommand{{~}}{{\\\\text{{...}}}}}}'
                f'\\\\mathcode\\"{cp_hex}=\\"8000\\\\relax}}", '
                f"or replace the character via 'rosettex fix <work> --file {target_file}'."
            )
        else:
            next_action = (
                f"Font '{font}' (and rosettex's bundled fallbacks) lack {shown_short}; add a font covering it in "
                f'build.json preamble_extra (e.g. "\\\\setmainfont{{...}}" or "\\\\newfontfamily...") '
                f"or replace the character via 'rosettex fix <work> --file {target_file}'."
            )

        entries.append(
            {
                "message": f"Missing characters in font {font}: {shown}",
                "build_file": b_file,
                "build_line": b_line,
                "kind": "preamble",
                "segment": matched_seg,
                "source_file": src_file,
                "source_line": src_line,
                "context": ctx,
                "channel": "build_settings",
                "font": font,
                "characters": chars,
                "segments": seg_ids,
                "next_action": next_action,
            }
        )
    return entries


def _missing_packages(log_text: str, blg: Path) -> list[dict]:
    """`missing_package` entries for files TeX could not find and rosettex did not (or may not) install."""
    from rosettex.build.driver import INSTALLED, missing_files, owning_package
    from rosettex.toolchain import tlmgr_path

    text = log_text + (blg.read_text(encoding="utf-8", errors="replace") if blg.is_file() else "")
    tlmgr = tlmgr_path()
    done = {i["file"]: i for i in INSTALLED}
    entries = []
    for name in missing_files(text)[:5]:
        pkg = done[name]["package"] if name in done else owning_package(name, tlmgr) if tlmgr else None
        if name in done:
            state = "installed" if done[name]["ok"] else "install failed"
            action = (f"{pkg} was {state} in this build and {name} is still missing; "
                      "remove its use with a source overlay (rosettex fix)")
        elif pkg:
            action = f"tlmgr install {pkg}"
        elif tlmgr:
            action = f"tlmgr search found no package for {name}; remove its use with a source overlay (rosettex fix)"
        else:
            action = (
                f"install the TeX Live package that provides {name} (tlmgr search --global --file /{name}, "
                "then tlmgr install <pkg>), or install it with your TeX distribution"
            )
        entries.append(
            {
                "message": f"missing_package: {name} not found",
                "kind": "missing_package",
                "file": name,
                "package": pkg,
                "next_action": action,
                "build_file": None,
                "build_line": None,
                "segment": None,
                "source_file": None,
                "source_line": None,
                "context": [],
                "channel": "build_settings",
            }
        )
    return entries


def diagnose(
    build_dir: Path,
    main: str,
    map_: dict,
    segments: list[Segment],
    translations: dict[str, str],
    log_text: str,
) -> list[dict]:
    build_dir = Path(build_dir)
    by_id = {s.id: s for s in segments}
    map_files: dict[str, dict] = map_.get("files", {}) if map_ else {}
    file_cache: dict[str, tuple[str, list[str], str, list[str]]] = {}

    def load_pair(rel: str) -> tuple[str, list[str], str, list[str]]:
        if rel not in file_cache:
            build_text, _ = read_source(build_dir / rel)
            asm_path = build_dir / f"{rel}.assembled"
            asm_text = read_source(asm_path)[0] if asm_path.is_file() else build_text
            file_cache[rel] = (
                asm_text,
                asm_text.splitlines(keepends=True),
                build_text,
                build_text.splitlines(keepends=True),
            )
        return file_cache[rel]

    def map_local_line(rel: str, line_no: int | None) -> int | None:
        if line_no is None:
            return None
        if rel not in map_files:
            return line_no
        asm_text, asm_lines, _, build_lines = load_pair(rel)
        asm_idx, is_shim = _map_build_line_to_assembled(asm_lines, build_lines, line_no)
        if asm_idx is None or is_shim:
            return None
        off = sum(len(line) for line in asm_lines[:asm_idx])
        return _source_line_for_assembled_offset(
            asm_text, off, map_files[rel].get("edits", []), by_id
        )

    def apply_open_file(entry: dict, err: dict, norm_file: str, build_line: int | None) -> dict:
        open_raw = err.get("open_file")
        if open_raw:
            open_norm, open_in_build = _norm_path(open_raw, build_dir)
            open_is_gen = open_norm in _GENERATED_FILES or open_norm.startswith("rosettex-")
            if open_norm != norm_file:
                entry["open_file"] = open_norm
                if open_in_build and not open_is_gen:
                    lno = err["caller_line"]
                    entry["source_file"] = open_norm
                    if lno is not None:
                        entry["source_line"] = map_local_line(open_norm, lno)
                        entry["context"] = _context_lines(build_dir / open_norm, lno)
                    return entry

        is_begin_doc = False
        if norm_file == main:
            if err.get("snippet") and "\\begin{document}" in err["snippet"]:
                is_begin_doc = True
            elif build_line is not None and (build_dir / norm_file).is_file():
                _, _, _, blines = load_pair(norm_file)
                if 1 <= build_line <= len(blines) and "\\begin{document}" in blines[build_line - 1]:
                    is_begin_doc = True
        if is_begin_doc:
            culprit = _find_begindoc_culprit(
                build_dir, err.get("loaded_local_files", []), main, err["message"]
            )
            if culprit is not None:
                c_file, c_line = culprit
                entry["open_file"] = c_file
                entry["source_file"] = c_file
                entry["source_line"] = c_line
                entry["context"] = _context_lines(build_dir / c_file, c_line)
                entry["kind"] = "source"
                entry["channel"] = "fix"
        return entry

    entries: list[dict] = _missing_packages(log_text, build_dir / f"{Path(main).stem}.blg")
    seen: set[tuple[str | None, int | None, str]] = set()

    for err in _parse_log_errors(log_text, main, build_dir):
        raw_file = err["raw_file"]
        build_line = err["line_no"]
        message = err["message"]
        norm_file, in_build = _norm_path(raw_file, build_dir)

        dedup_key = (norm_file, build_line, message)
        if dedup_key in seen:
            continue
        seen.add(dedup_key)

        is_generated = norm_file in _GENERATED_FILES or norm_file.startswith("rosettex-")
        if not in_build or is_generated:
            ctx_path = (build_dir / norm_file) if in_build else Path(raw_file)
            src_file = None
            src_line = None
            if not in_build and err["last_local_file"]:
                src_file = err["last_local_file"]
                src_line = map_local_line(src_file, err["caller_line"])
            entry = {
                "message": message,
                "build_file": norm_file,
                "build_line": build_line,
                "kind": "preamble",
                "segment": None,
                "source_file": src_file,
                "source_line": src_line,
                "context": _context_lines(ctx_path, build_line),
                "channel": "build_settings",
            }
            open_raw = err.get("open_file")
            if open_raw:
                open_norm, _ = _norm_path(open_raw, build_dir)
                if open_norm != norm_file:
                    entry["open_file"] = open_norm
            entries.append(entry)
            if len(entries) >= _MAX_ENTRIES:
                break
            continue

        # File is inside build_dir and not a generated rosettex-* file
        ctx = _context_lines(build_dir / norm_file, build_line)
        if norm_file not in map_files or build_line is None:
            entries.append(
                apply_open_file(
                    {
                        "message": message,
                        "build_file": norm_file,
                        "build_line": build_line,
                        "kind": "source",
                        "segment": None,
                        "source_file": norm_file,
                        "source_line": build_line,
                        "context": ctx,
                        "channel": "fix",
                    },
                    err,
                    norm_file,
                    build_line,
                )
            )
            if len(entries) >= _MAX_ENTRIES:
                break
            continue

        asm_text, asm_lines, _, build_lines = load_pair(norm_file)
        asm_idx, is_shim = _map_build_line_to_assembled(asm_lines, build_lines, build_line)
        if is_shim or asm_idx is None:
            entries.append(
                apply_open_file(
                    {
                        "message": message,
                        "build_file": norm_file,
                        "build_line": build_line,
                        "kind": "preamble",
                        "segment": None,
                        "source_file": None,
                        "source_line": None,
                        "context": ctx,
                        "channel": "build_settings",
                    },
                    err,
                    norm_file,
                    build_line,
                )
            )
            if len(entries) >= _MAX_ENTRIES:
                break
            continue

        file_entry = map_files[norm_file]
        edits = file_entry.get("edits", [])
        matched_edit, offset = _find_edit_for_line(asm_lines, asm_idx, edits, err["snippet"])
        if matched_edit is not None:
            src_line = _source_line_for_assembled_offset(
                asm_text, matched_edit["out"][0], edits, by_id
            )
            entries.append(
                {
                    "message": message,
                    "build_file": norm_file,
                    "build_line": build_line,
                    "kind": "segment",
                    "segment": matched_edit["seg"],
                    "source_file": file_entry.get("source", norm_file),
                    "source_line": src_line,
                    "context": ctx,
                    "channel": "translation",
                }
            )
        else:
            src_line = _source_line_for_assembled_offset(asm_text, offset, edits, by_id)
            entries.append(
                apply_open_file(
                    {
                        "message": message,
                        "build_file": norm_file,
                        "build_line": build_line,
                        "kind": "source",
                        "segment": None,
                        "source_file": file_entry.get("source", norm_file),
                        "source_line": src_line,
                        "context": ctx,
                        "channel": "fix",
                    },
                    err,
                    norm_file,
                    build_line,
                )
            )
        if len(entries) >= _MAX_ENTRIES:
            break

    for entry in _diagnose_missing_chars(
        log_text,
        segments,
        translations,
        build_dir=build_dir,
        main=main,
        map_files=map_files,
        by_id=by_id,
        load_pair=load_pair,
        map_local_line=map_local_line,
    ):
        if len(entries) >= _MAX_ENTRIES:
            break
        entries.append(entry)

    for entry in entries:
        entry["next_action"] = _next_action_for(entry)

    return entries

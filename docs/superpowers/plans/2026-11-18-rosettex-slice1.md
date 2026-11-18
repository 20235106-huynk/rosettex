# rosettex slice 1 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The `rosettex` CLI takes a text-layer PDF paper through the whole flow `extract → terms → next/accept → validate → render`. The agent, in any harness, is the translator. Runs on Linux, macOS and Windows.

**Architecture:** The core is pure, deterministic Python modules that never call an LLM.
- MinerU (called via subprocess, zip output) is converted into a custom-format `document.json`, then split into translation batches with placeholders.
- Translations are checked as soon as they are submitted. The build is LaTeX compiled with Tectonic or XeLaTeX. A failing formula or table is isolated with a `\typeout{RX:<id>}` marker and switched to the last-resort fallback.
- Long tasks run in a detached process that writes a heartbeat to a file. The agent always follows the `next_action` returned by `rosettex status`.

**Tech Stack:** Python ≥ 3.11, `pypdfium2` as the only runtime dependency, pytest (dev), uv, MinerU 4.x (separate environment), Tectonic 0.17.0 or XeLaTeX.

**Spec:**
- `docs/superpowers/specs/2026-11-18-rosettex-slice1-design.md` (slice 1)
- `docs/superpowers/specs/2026-11-18-rosettex-design.md` (master spec, abbreviated SG)

## Global Constraints

- Runs on Linux, macOS (Intel and Apple Silicon), Windows x64; CPU only (SG 3.1).
- Python `>=3.11`. The **only** runtime dependency is `pypdfium2`. No PyMuPDF (AGPL). No embedding AGPL code or code from projects without a license.
- No system shell commands from code: only `pathlib`/`shutil`/`subprocess` with argument lists, never `shell=True`.
- Always read and write files with `encoding="utf-8"` (read JSON with `utf-8-sig`), `\n` line endings. Write state JSON through a temp file then `os.replace`.
- stdout is always UTF-8. Every command has `--json`. Exit codes: `0` success; `1` check failure; `2` usage or environment error.
- Never pass `--remote` to MinerU. Never read or write `~/.mineru/config.yaml`.
- Compare text in NFC form. Placeholders have the form `⟦<kind><number>⟧`, with kind `m` (formula, superscript), `c` (citation), `r` (reference), `u` (URL/email).
- Default tier is `standard`; drop to `basic` when available RAM < 5 GB. Page groups of 5. Batches ≤ 3000 source characters. A background task is considered dead when its heartbeat is older than 60 seconds.
- Main font `texgyretermes` (loaded by file name, `Extension=.otf`). Math font `latinmodern-math.otf`. `polyglossia` with `vietnamese`.
- Content length ratio (total text line height of the translation / the original) ≤ 1.3.

**Deliberate deviation from the spec, confirmed by the user (2026-11-18):** SG 14 and slice 1 spec section 12 say "`accept` rejects NFD strings". This plan **normalizes NFD to NFC and then accepts**, it does not reject. Reason: an agent usually cannot control the normalization form of the strings it emits, so rejecting would cause an endless resubmit loop. The spec also already requires all comparisons to run on NFC. The stored copy is always NFC (tested in Task 8).

## Review Focus

1. **Paths with spaces and Vietnamese characters** (`~/Tài liệu/bài báo.pdf`) for the PDF, the output location and the work directory must work in every command. Tested in Task 1 and Task 12.
2. **A translation file with BOM or CRLF** (written by a Windows editor) must be accepted by `accept` normally. Tested in Task 8.
3. **A translation containing LaTeX special characters** (`% & _ # { } ~ ^ \`), Greek letters or `∗` must still build a PDF without losing text. Tested in Task 4, Task 10 and Task 11.
4. **An existing output file** must be rejected without `--overwrite` (exit code 2, with `output_exists`). A **missing output directory** is created automatically. Tested in Task 12.
5. **An extract task killed midway** (a page group in progress, no `.done` yet): rerunning redoes only the unfinished group, not the finished ones. Tested in Task 9.

---

## File structure

```
pyproject.toml, .python-version, .gitignore, README.md
rosettex/
  __init__.py            version
  __main__.py            python -m rosettex
  cli.py                 argparse, commands, status/next_action, background runs
  workspace.py           Work (paths), atomic JSON, heartbeat, process detaching, UsageError, q()
  tools.py               locate mineru-kit / tectonic / xelatex, RAM, tier choice, model verify
  pdf.py                 pypdfium2: page count, PNG images (zlib), text in bbox, full text, line height
  tex.py                 escape_latex
  checks.py              language packs, accept/validate checks
  translate.py           terms, outline, next, accept, validate
  extract/__init__.py
  extract/tables.py      HTML table → cells
  extract/mineru_adapter.py  docvortex.middle 2.0 → document.json
  extract/segment.py     merge paragraphs, keep-verbatim roles, placeholders/links, translation units, batches
  extract/run.py         page groups, call MinerU, flagging, extract_core
  render/__init__.py
  render/latex.py        Renderer: main.tex and probe.tex
  render/compile.py      run TeX, find the failing id, last-resort fallback, render_core, report
  render/postcheck.py    post-build checks
  langpacks/vi.json, langpacks/default.json
  protocol/__init__.py   text(lang), skill_md(), agents_md()
  protocol/protocol.md   single source of the protocol
  protocol/build.py      ghi skill/SKILL.md, skill/AGENTS.md
skill/SKILL.md, skill/AGENTS.md   (generated, committed)
scripts/get_tectonic.py           download Tectonic 0.17.0 with SHA-256 check
.github/workflows/ci.yml
tests/
  conftest.py, fake_mineru.py
  test_workspace.py, test_tools.py, test_pdf.py, test_tex_tables.py, test_adapter.py,
  test_segment.py, test_checks.py, test_translate.py, test_extract.py, test_latex.py,
  test_render.py, test_cli.py, test_protocol.py
  fixtures/ (already present from the spike)
```

---

### Task 1: Project skeleton + `workspace.py`

**Files:**
- Create: `pyproject.toml`, `.python-version`, `.gitignore`, `rosettex/__init__.py`, `rosettex/workspace.py`
- Test: `tests/test_workspace.py`

**Interfaces:**
- Consumes: none.
- Produces:
  - `class UsageError(Exception)` with `(msg, next_action=None, **extra)`, attributes `.next_action`, `.extra`.
  - `q(path) -> str`.
  - `write_json(path, obj) -> None`.
  - `read_json(path, default=None)`.
  - `class Work(root)` with attributes `root, src_pdf, manifest, progress, logs, mineru, pages, document, batches, terms, flagged, out, report, validate` and two methods `batch_path(bid)`, `translation_path(bid, lang)`.
  - `HEARTBEAT_DEAD_AFTER = 60.0`.
  - `beat(work, command, resume, step, done, total, state="running", error=None)`.
  - `job_state(work, now=None) -> dict | None` (adds the key `dead`).
  - `spawn_detached(argv, log) -> int`.
  - `run_with_heartbeat(argv, *, cwd, log, on_beat, env=None, interval=5.0) -> int`.

- [ ] **Step 1: Initialize the repo and project skeleton**

```bash
cd /home/admin/Desktop/Learn/rosettex
git init
```

`pyproject.toml`:
```toml
[project]
name = "rosettex"
version = "0.1.0"
description = "Extract, check and rebuild scientific PDF papers for an in-harness agent to translate"
readme = "README.md"
requires-python = ">=3.11"
dependencies = ["pypdfium2>=4.30"]

[project.scripts]
rosettex = "rosettex.cli:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[dependency-groups]
dev = ["pytest>=8"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

`.python-version`:
```
3.12
```

`.gitignore`:
```
__pycache__/
*.pyc
.venv/
.pytest_cache/
dist/
*.egg-info/
```

`rosettex/__init__.py`:
```python
__version__ = "0.1.0"
```

Create a one-line `README.md`: `# rosettex` (Task 13 writes it in full).

- [ ] **Step 2: Write the failing test**

`tests/test_workspace.py`:
```python
import sys
import time

from rosettex.workspace import (HEARTBEAT_DEAD_AFTER, Work, beat, job_state, read_json,
                                run_with_heartbeat, spawn_detached, write_json)


def test_write_read_roundtrip_non_ascii_path(tmp_path):
    p = tmp_path / "Tài liệu có dấu cách" / "x.json"
    write_json(p, {"chữ": "ệ ữ ẫ"})
    assert read_json(p) == {"chữ": "ệ ữ ẫ"}
    assert b"\r\n" not in p.read_bytes()


def test_read_json_accepts_bom_and_missing(tmp_path):
    p = tmp_path / "bom.json"
    p.write_bytes(b"\xef\xbb\xbf" + '{"a": "đ"}'.encode("utf-8"))
    assert read_json(p) == {"a": "đ"}
    assert read_json(tmp_path / "none.json", default=7) == 7


def test_job_state_detects_dead_heartbeat(tmp_path):
    w = Work(tmp_path)
    beat(w, "extract", "rosettex extract x", "group 1/4", 1, 4)
    fresh = job_state(w)
    assert fresh["state"] == "running" and fresh["dead"] is False
    assert job_state(w, now=fresh["updated"] + HEARTBEAT_DEAD_AFTER + 1)["dead"] is True
    beat(w, "extract", "rosettex extract x", "xong", 4, 4, state="done")
    assert job_state(w, now=time.time() + 1000)["dead"] is False


def test_spawn_detached_runs_child(tmp_path):
    flag = tmp_path / "chạy xong.txt"
    spawn_detached([sys.executable, "-c", f"open(r'{flag}', 'w').write('ok')"], tmp_path / "logs" / "c.log")
    for _ in range(100):
        if flag.exists():
            break
        time.sleep(0.1)
    assert flag.read_text() == "ok"


def test_run_with_heartbeat_beats_while_waiting(tmp_path):
    beats = []
    rc = run_with_heartbeat([sys.executable, "-c", "import time; time.sleep(0.5)"], cwd=tmp_path,
                            log=tmp_path / "logs" / "l.log", on_beat=lambda: beats.append(1), interval=0.1)
    assert rc == 0 and len(beats) >= 2
```

- [ ] **Step 3: Run to see the failure**

Run: `uv sync && uv run pytest tests/test_workspace.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'rosettex.workspace'`

- [ ] **Step 4: Write `rosettex/workspace.py`**

```python
"""Work directory: paths, atomic JSON writes, heartbeat, process detaching."""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

HEARTBEAT_DEAD_AFTER = 60.0  # seconds


class UsageError(Exception):
    """Usage/environment error → exit code 2."""

    def __init__(self, msg, next_action=None, **extra):
        super().__init__(msg)
        self.next_action = next_action
        self.extra = extra


def q(path) -> str:
    """Path in a command hinted to the agent: always wrapped in double quotes (works in bash, zsh, cmd, PowerShell)."""
    return '"' + str(path) + '"'


def write_json(path, obj) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    for attempt in range(20):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:  # Windows: the target file is open in another process
            time.sleep(0.05 * (attempt + 1))
    os.replace(tmp, path)


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return default


class Work:
    def __init__(self, root):
        r = self.root = Path(root).expanduser().resolve()
        self.src_pdf = r / "src.pdf"
        self.manifest = r / "manifest.json"
        self.progress = r / "progress.json"
        self.logs = r / "logs"
        self.mineru = r / "mineru"
        self.pages = r / "pages"
        self.document = r / "document.json"
        self.batches = r / "batches"
        self.terms = r / "terms.json"
        self.flagged = r / "flagged.json"
        self.out = r / "out"
        self.report = r / "report.json"
        self.validate = r / "validate.json"

    def batch_path(self, bid: str) -> Path:
        return self.batches / f"{bid}.json"

    def translation_path(self, bid: str, lang: str) -> Path:
        return self.batches / f"{bid}.{lang}.json"


def beat(work: Work, command: str, resume: str, step: str, done: int, total: int,
         state: str = "running", error: str | None = None) -> None:
    write_json(work.progress, {"command": command, "resume": resume, "pid": os.getpid(), "state": state,
                               "step": step, "done": done, "total": total, "error": error,
                               "updated": time.time()})


def job_state(work: Work, now: float | None = None) -> dict | None:
    p = read_json(work.progress)
    if p is None:
        return None
    now = time.time() if now is None else now
    p["dead"] = p["state"] == "running" and now - p["updated"] > HEARTBEAT_DEAD_AFTER
    return p


def spawn_detached(argv: list[str], log: Path) -> int:
    """Run a child process detached from the current command (survives the harness closing its shell)."""
    log = Path(log)
    log.parent.mkdir(parents=True, exist_ok=True)
    kw = {}
    if os.name == "nt":
        kw["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True
    with open(log, "ab") as fh:
        p = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=fh, stderr=subprocess.STDOUT, **kw)
    return p.pid


def run_with_heartbeat(argv, *, cwd, log, on_beat, env=None, interval: float = 5.0) -> int:
    """Run a command, calling on_beat() every `interval` seconds while waiting; return the exit code."""
    log = Path(log)
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "ab") as fh:
        p = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=fh,
                             stderr=subprocess.STDOUT)
        while True:
            try:
                return p.wait(timeout=interval)
            except subprocess.TimeoutExpired:
                on_beat()
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_workspace.py -v`
Expected: PASS (5 test)

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "chore: rosettex project skeleton and workspace (atomic JSON, heartbeat, process detaching)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: `tools.py`: locate tools, RAM, tier choice

**Files:**
- Create: `rosettex/tools.py`
- Test: `tests/test_tools.py`

**Interfaces:**
- Consumes: none.
- Produces:
  - `home() -> Path` (reads `ROSETTEX_HOME` **on every call**, default `~/.rosettex`).
  - `find_mineru() -> list[str] | None` (command as a list).
  - `find_tex() -> tuple[str, list[str]] | None` with `kind ∈ {"tectonic", "xelatex"}`.
  - `available_ram_bytes() -> int | None`.
  - `MIN_RAM_STANDARD`.
  - `choose_tier(requested, ram) -> tuple[str, str]`.
  - `verify_models(cmd, tier) -> tuple[bool, str]`.

- [ ] **Step 1: Write the failing test**

`tests/test_tools.py`:
```python
import os
import sys

from rosettex.tools import available_ram_bytes, choose_tier, find_mineru, find_tex


def test_choose_tier():
    assert choose_tier(None, 16 * 1024**3) == ("standard", "default")
    assert choose_tier(None, 3 * 1024**3) == ("basic", "low_ram")
    assert choose_tier(None, None) == ("standard", "default")
    assert choose_tier("basic", 64 * 1024**3) == ("basic", "requested")


def test_find_mineru_env_python_script(tmp_path, monkeypatch):
    s = tmp_path / "fake mineru.py"
    s.write_text("")
    monkeypatch.setenv("ROSETTEX_MINERU", str(s))
    assert find_mineru() == [sys.executable, str(s)]


def test_find_mineru_none(tmp_path, monkeypatch):
    monkeypatch.delenv("ROSETTEX_MINERU", raising=False)
    monkeypatch.setenv("ROSETTEX_HOME", str(tmp_path))
    monkeypatch.setenv("PATH", str(tmp_path))
    assert find_mineru() is None


def test_find_tex_prefers_home_bin(tmp_path, monkeypatch):
    monkeypatch.delenv("ROSETTEX_TEX", raising=False)
    monkeypatch.setenv("ROSETTEX_HOME", str(tmp_path))
    exe = tmp_path / "bin" / ("tectonic.exe" if os.name == "nt" else "tectonic")
    exe.parent.mkdir()
    exe.write_text("")
    assert find_tex() == ("tectonic", [str(exe)])


def test_available_ram_positive_or_none():
    r = available_ram_bytes()
    assert r is None or r > 0
```

- [ ] **Step 2: Run to see the failure**

Run: `uv run pytest tests/test_tools.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'rosettex.tools'`

- [ ] **Step 3: Write `rosettex/tools.py`**

```python
"""Locate external tools (MinerU, LaTeX), read RAM, choose the tier."""
from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import sys
from pathlib import Path

EXE = ".exe" if os.name == "nt" else ""
MIN_RAM_STANDARD = 5 * 1024**3


def home() -> Path:
    return Path(os.environ.get("ROSETTEX_HOME") or Path.home() / ".rosettex")


def _cmd(p: Path) -> list[str]:
    return [sys.executable, str(p)] if p.suffix == ".py" else [str(p)]


def find_mineru() -> list[str] | None:
    env = os.environ.get("ROSETTEX_MINERU")
    venv = home() / "mineru-venv"
    cands = ([Path(env)] if env else []) + [venv / "bin" / "mineru-kit", venv / "Scripts" / f"mineru-kit{EXE}"]
    for c in cands:
        if c.is_file():
            return _cmd(c)
    w = shutil.which("mineru-kit")
    return _cmd(Path(w)) if w else None


def find_tex() -> tuple[str, list[str]] | None:
    env = os.environ.get("ROSETTEX_TEX")
    for c in ([Path(env)] if env else []) + [home() / "bin" / f"tectonic{EXE}"]:
        if c.is_file():
            return ("xelatex" if "xelatex" in c.name.lower() else "tectonic", [str(c)])
    for name in ("tectonic", "xelatex"):
        w = shutil.which(name)
        if w:
            return (name, [w])
    return None


def available_ram_bytes() -> int | None:
    try:
        if sys.platform.startswith("linux"):
            for line in Path("/proc/meminfo").read_text().splitlines():
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) * 1024
        elif os.name == "nt":
            class _MS(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            m = _MS()
            m.dwLength = ctypes.sizeof(_MS)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):
                return int(m.ullAvailPhys)
        else:
            # ponytail: on macOS only total RAM is readable via sysconf, not available RAM; enough to avoid very weak machines.
            # Upgrade: read vm_stat via ctypes (host_statistics64) if precision is needed.
            return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (OSError, ValueError, AttributeError):
        pass
    return None


def choose_tier(requested: str | None, ram: int | None) -> tuple[str, str]:
    if requested:
        return requested, "requested"
    if ram is not None and ram < MIN_RAM_STANDARD:
        return "basic", "low_ram"
    return "standard", "default"


def verify_models(cmd: list[str], tier: str) -> tuple[bool, str]:
    p = subprocess.run(cmd + ["models", "verify", "--tier", tier], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    out = p.stdout + p.stderr
    return p.returncode == 0 and "missing" not in out.lower(), out
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_tools.py -v`
Expected: PASS (5 test)

- [ ] **Step 5: Commit**

```bash
git add rosettex/tools.py tests/test_tools.py
git commit -m "feat: locate MinerU/LaTeX, read RAM, choose tier

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: `pdf.py`: pypdfium2 helpers

**Files:**
- Create: `rosettex/pdf.py`, `tests/conftest.py`
- Test: `tests/test_pdf.py`

**Interfaces:**
- Consumes: none.
- Produces:
  - `page_count(path) -> int`.
  - `write_png(path, width, height, pixels: bytes, stride, channels=3)`.
  - `render_page_png(pdf_path, page_no, out, dpi=150) -> Path` (`page_no` counts from 1).
  - `text_in_bbox(pdf_path, page_no, bbox) -> str` (`bbox` normalized 0–1, origin at top-left).
  - `full_text(path) -> str`.
  - `line_height_total(path) -> float`.
  - `tests/conftest.py`: the `FIX` constant.

- [ ] **Step 1: Write conftest and the failing test**

`tests/conftest.py`:
```python
from pathlib import Path

FIX = Path(__file__).parent / "fixtures"
```

`tests/test_pdf.py`:
```python
import struct

from conftest import FIX
from rosettex.pdf import full_text, line_height_total, page_count, render_page_png, text_in_bbox

SHORT = FIX / "papers" / "short.pdf"
LONG = FIX / "papers" / "long.pdf"


def test_page_count():
    assert page_count(SHORT) == 2 and page_count(LONG) == 20


def test_render_page_png(tmp_path):
    out = render_page_png(SHORT, 1, tmp_path / "trang 1.png", dpi=150)
    data = out.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    width, height = struct.unpack(">II", data[16:24])
    assert (width, height) == (1275, 1650) or (width, height) == (1275, 1651)


def test_text_in_bbox_title():
    assert "Attention-Based" in text_in_bbox(SHORT, 1, [0.145, 0.105, 0.855, 0.13])


def test_full_text_and_line_heights():
    assert "Residual Scaling" in full_text(SHORT)
    assert line_height_total(LONG) > line_height_total(SHORT) > 0
```

- [ ] **Step 2: Run to see the failure**

Run: `uv run pytest tests/test_pdf.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'rosettex.pdf'`

- [ ] **Step 3: Write `rosettex/pdf.py`**

```python
"""Read/render PDF with pypdfium2 (Apache-2.0/BSD). No PyMuPDF (AGPL)."""
from __future__ import annotations

import struct
import zlib
from contextlib import contextmanager
from pathlib import Path

import pypdfium2 as pdfium


@contextmanager
def _open(path):
    pdf = pdfium.PdfDocument(str(path))
    try:
        yield pdf
    finally:
        pdf.close()


def page_count(path) -> int:
    with _open(path) as pdf:
        return len(pdf)


def write_png(path, width: int, height: int, pixels: bytes, stride: int, channels: int = 3) -> None:
    """PNG using the standard library (zlib), so Pillow is not needed."""
    row = width * channels
    raw = b"".join(b"\x00" + pixels[y * stride: y * stride + row] for y in range(height))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    color = 2 if channels == 3 else 6
    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, color, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes(png)


def render_page_png(pdf_path, page_no: int, out, dpi: int = 150) -> Path:
    with _open(pdf_path) as pdf:
        bm = pdf[page_no - 1].render(scale=dpi / 72, rev_byteorder=True)
        channels = 4 if bm.mode in ("RGBA", "RGBX") else 3
        write_png(out, bm.width, bm.height, bytes(bm.buffer), bm.stride, channels)
    return Path(out)


def text_in_bbox(pdf_path, page_no: int, bbox) -> str:
    x0, y0, x1, y1 = bbox
    with _open(pdf_path) as pdf:
        page = pdf[page_no - 1]
        w, h = page.get_size()
        return page.get_textpage().get_text_bounded(left=x0 * w, bottom=(1 - y1) * h, right=x1 * w, top=(1 - y0) * h)


def full_text(path) -> str:
    with _open(path) as pdf:
        return "\n".join(page.get_textpage().get_text_range() for page in pdf)


def line_height_total(path) -> float:
    """Total height (pt) of text lines, grouping characters by (left/right half of the page, vertical center rounded to 4 pt)."""
    # ponytail: a full-page-wide line is counted twice (left half + right half) in both the original and the translation, so the ratio
    # is still usable; upgrade: group by real horizontal gaps if the ratio is off on real papers.
    total = 0.0
    with _open(path) as pdf:
        for page in pdf:
            tp = page.get_textpage()
            w, _ = page.get_size()
            lines: dict[tuple[int, int], tuple[float, float]] = {}
            for i in range(tp.count_chars()):
                left, bottom, right, top = tp.get_charbox(i)
                if right - left <= 0 or top - bottom <= 0:
                    continue
                key = (0 if (left + right) / 2 < w / 2 else 1, round((bottom + top) / 2 / 4))
                lo, hi = lines.get(key, (bottom, top))
                lines[key] = (min(lo, bottom), max(hi, top))
            total += sum(hi - lo for lo, hi in lines.values())
    return total
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_pdf.py -v`
Expected: PASS (4 test)

- [ ] **Step 5: Commit**

```bash
git add rosettex/pdf.py tests/conftest.py tests/test_pdf.py
git commit -m "feat: PDF helpers using pypdfium2 (PNG images without Pillow, text in bbox, line height)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: `tex.py` (escape) + `extract/tables.py` (HTML → cells)

**Files:**
- Create: `rosettex/tex.py`, `rosettex/extract/__init__.py` (empty), `rosettex/extract/tables.py`
- Test: `tests/test_tex_tables.py`

**Interfaces:**
- Consumes: none.
- Produces:
  - `escape_latex(s: str) -> str`.
  - `parse_table_html(html: str) -> list[dict]`, each element has `{r, c, rowspan, colspan, text}`.
  - `table_shape(cells) -> tuple[int, int]`.

- [ ] **Step 1: Write the failing test**

`tests/test_tex_tables.py`:
```python
from rosettex.extract.tables import parse_table_html, table_shape
from rosettex.tex import escape_latex

HTML = ('<table><tbody><tr><td rowspan="2">Model</td><td rowspan="2">Size</td><td colspan="2">BLEU</td></tr>'
        '<tr><td>EN-DE</td><td>EN-FR</td></tr><tr><td>ByteNet</td><td>1.2B</td><td>23.75</td><td>-</td></tr>'
        '</tbody></table>')


def test_escape_specials():
    assert escape_latex("50% & a_b #1 {x} ~^\\") == (
        r"50\% \& a\_b \#1 \{x\} \textasciitilde{}\textasciicircum{}\textbackslash{}")


def test_escape_math_like_unicode_goes_to_math_mode():
    assert escape_latex("α ≤ β ∗") == r"\ensuremath{α} \ensuremath{≤} \ensuremath{β} \ensuremath{∗}"
    assert escape_latex("Tiếng Việt – “trích”") == "Tiếng Việt – “trích”"


def test_parse_table_html_spans():
    cells = parse_table_html(HTML)
    pos = {(c["r"], c["c"]): c for c in cells}
    assert pos[(0, 0)]["rowspan"] == 2 and pos[(0, 2)]["colspan"] == 2
    assert pos[(1, 2)]["text"] == "EN-DE" and pos[(1, 3)]["text"] == "EN-FR"
    assert pos[(2, 3)]["text"] == "-"
    assert table_shape(cells) == (3, 4)


def test_parse_table_html_bad_span_defaults_to_one():
    cells = parse_table_html('<table><tr><td rowspan="x">a</td><td>b</td></tr></table>')
    assert [(c["r"], c["c"], c["rowspan"]) for c in cells] == [(0, 0, 1), (0, 1, 1)]
```

- [ ] **Step 2: Run to see the failure**

Run: `uv run pytest tests/test_tex_tables.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write `rosettex/tex.py`**

```python
"""Escape plain text for placing into LaTeX (XeLaTeX + unicode-math)."""

_ESC = {"\\": r"\textbackslash{}", "{": r"\{", "}": r"\}", "#": r"\#", "$": r"\$", "%": r"\%",
        "&": r"\&", "_": r"\_", "~": r"\textasciitilde{}", "^": r"\textasciicircum{}"}


def _mathy(c: str) -> bool:
    o = ord(c)
    return 0x0370 <= o <= 0x03FF or 0x2190 <= o <= 0x22FF or 0x2A00 <= o <= 0x2AFF


def escape_latex(s: str) -> str:
    # Greek letters and math symbols go to math mode: the math font has all the glyphs, the text font may not.
    return "".join(_ESC.get(c) or (r"\ensuremath{" + c + "}" if _mathy(c) else c) for c in s)
```

- [ ] **Step 4: Write `rosettex/extract/tables.py`**

```python
"""MinerU HTML tables (`rowspan`/`colspan`) → list of cells with grid coordinates."""
from __future__ import annotations

from html.parser import HTMLParser


def _int(v) -> int:
    try:
        return max(1, int(v))
    except (TypeError, ValueError):
        return 1


class _Parser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows: list[list[dict]] = []
        self.cell: dict | None = None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.rows.append([])
        elif tag in ("td", "th"):
            a = dict(attrs)
            self.cell = {"rowspan": _int(a.get("rowspan")), "colspan": _int(a.get("colspan")), "text": ""}
            if not self.rows:
                self.rows.append([])
            self.rows[-1].append(self.cell)

    def handle_endtag(self, tag):
        if tag in ("td", "th"):
            self.cell = None

    def handle_data(self, data):
        if self.cell is not None:
            self.cell["text"] += data


def parse_table_html(html: str) -> list[dict]:
    p = _Parser()
    p.feed(html)
    p.close()
    taken: set[tuple[int, int]] = set()
    cells = []
    for r, row in enumerate(p.rows):
        c = 0
        for cell in row:
            while (r, c) in taken:
                c += 1
            for dr in range(cell["rowspan"]):
                for dc in range(cell["colspan"]):
                    taken.add((r + dr, c + dc))
            cells.append({"r": r, "c": c, "rowspan": cell["rowspan"], "colspan": cell["colspan"],
                          "text": " ".join(cell["text"].split())})
            c += cell["colspan"]
    return cells


def table_shape(cells) -> tuple[int, int]:
    rows = max((x["r"] + x["rowspan"] for x in cells), default=0)
    cols = max((x["c"] + x["colspan"] for x in cells), default=0)
    return rows, cols
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_tex_tables.py -v`
Expected: PASS (4 test)

- [ ] **Step 6: Commit**

```bash
git add rosettex/tex.py rosettex/extract tests/test_tex_tables.py
git commit -m "feat: LaTeX escaping and MinerU HTML table parsing

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: MinerU → `document.json` adapter

**Files:**
- Create: `rosettex/extract/mineru_adapter.py`
- Test: `tests/test_adapter.py`

**Interfaces:**
- Consumes: `parse_table_html` (Task 4).
- Produces:
  - `normalize_tex(s) -> str`.
  - `spans_of(content) -> list[dict]`, each span has the form `{"t": "text"|"math"|"sup", "v": str}`.
  - `adapt(groups: list[tuple[dict, Path]], root: Path) -> dict`, where:
    - `doc["source"] = {pages, page_size_pt, columns, parser, counts{tables, figures, tagged_equations, references}}`.
    - `doc["blocks"]`: each block has `{id:"p<page>b<index>", kind, page, bbox, …}`.
    - Depending on `kind`, the block has extra fields:
      - `heading`: `spans, number, level, label`.
      - `paragraph`: `spans`.
      - `equation`: `tex, tag, label, image`.
      - `table`: `caption, label, cells[{id,r,c,rowspan,colspan,text}], image`.
      - `figure`: `caption, label, image`.
      - `footnote`: `spans, mark`.
      - `reference`: `spans, key`.
    - `table`/`figure`/`equation` also have `wide`.
    - `image` paths are relative to `root`, in POSIX form.

- [ ] **Step 1: Write the failing test**

`tests/test_adapter.py`:
```python
import json

from conftest import FIX
from rosettex.extract.mineru_adapter import adapt, normalize_tex


def load(tier, name):
    d = FIX / f"mineru-4.2.3-{tier}"
    return json.loads((d / f"{name}.middle.json").read_text(encoding="utf-8")), d


def blocks(doc):
    return {b["id"]: b for b in doc["blocks"]}


def test_normalize_tex():
    assert normalize_tex(r"\lambda   =   0 . 0 1") == r"\lambda   =   0.01"
    assert normalize_tex(r"w = 4 0 0 0") == "w = 4000"
    assert normalize_tex(r"3 . 3 \times 1 0 ^ { 1 8 }") == r"3.3 \times 10 ^ { 18 }"
    assert normalize_tex(r"\operatorname { L a y e r N o r m } ( x )") == r"\operatorname{LayerNorm} ( x )"
    assert normalize_tex(r"\mathrm { E q . ~ ( 1 ) }") == r"\mathrm{Eq.~(1)}"
    assert normalize_tex(r"\text {Attention} (Q)") == r"\text{Attention} (Q)"


def test_adapt_short_standard():
    m, d = load("standard", "short")
    doc = adapt([(m, d)], d)
    src, B = doc["source"], blocks(doc)
    assert src["pages"] == 2 and src["columns"] == 2
    assert src["page_size_pt"] == [[612.0, 792.0], [612.0, 792.0]]
    assert src["counts"] == {"tables": 1, "figures": 1, "tagged_equations": 2, "references": 4}
    assert src["parser"].endswith("/standard")
    assert B["p1b0"]["kind"] == "heading" and B["p1b0"]["level"] == 1
    h = B["p1b8"]
    assert h["number"] == "2" and h["label"] == "sec:2" and h["level"] == 2 and h["spans"][0]["v"].strip() == "Method"
    eq = B["p1b10"]
    assert eq["tag"] == "1" and eq["label"] == "eq:1" and r"\tag" not in eq["tex"]
    assert eq["image"] == "images/page_0_equation_10.jpg"
    t = B["p1b17"]
    assert t["label"] == "tab:1" and len(t["cells"]) == 21 and t["cells"][0]["id"] == "p1b17r0c0"
    assert t["caption"][0]["v"].startswith("BLEU scores")
    f = B["p1b18"]
    assert f["kind"] == "figure" and f["label"] == "fig:1" and f["image"] == "images/page_0_chart_18.jpg"
    assert B["p1b22"]["mark"] == "∗" and B["p1b24"]["mark"] == "1"
    assert B["p1b24"]["spans"][0]["v"].startswith("We found warmup")
    assert B["p2b1"]["key"] == "1" and B["p2b1"]["spans"][0]["v"].startswith("A. Vaswani")
    assert "p1b25" not in B  # page_number is dropped
    assert [s["t"] for s in B["p1b9"]["spans"]].count("math") == 6


def test_adapt_basic_keeps_eq_reference_inside_math():
    m, d = load("basic", "short")
    doc = adapt([(m, d)], d)
    maths = [s["v"] for s in blocks(doc)["p1b11"]["spans"] if s["t"] == "math"]
    assert r"\mathrm{Eq.~(1)}" in maths


def test_adapt_long_dedupes_labels_and_counts():
    m, d = load("standard", "long")
    doc = adapt([(m, d)], d)
    labels = [b["label"] for b in doc["blocks"] if b.get("label")]
    assert len(labels) == len(set(labels)) and labels.count("eq:1") == 1
    assert doc["source"]["counts"] == {"tables": 28, "figures": 28, "tagged_equations": 56, "references": 4}
    assert doc["source"]["pages"] == 20
```

- [ ] **Step 2: Run to see the failure**

Run: `uv run pytest tests/test_adapter.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write `rosettex/extract/mineru_adapter.py`**

```python
"""MinerU `docvortex.middle` 2.0 (--format zip output) → document.json (`rosettex.document/1`).

Schema verified in SG 4.1: block → span, only blocks with a bbox (normalized 0–1), absolute page_idx counted from 0.
"""
from __future__ import annotations

import re
from pathlib import Path

from .tables import parse_table_html

KIND = {"doc_title": "heading", "paragraph_title": "heading", "text": "paragraph", "equation": "equation",
        "table": "table", "chart": "figure", "image": "figure", "page_footnote": "footnote", "ref_text": "reference"}
NUM_HEAD = re.compile(r"^\s*(\d+(?:\.\d+)*)\.?\s+(?=\S)")
CAPTION = re.compile(r"^\s*(?:Figure|Fig\.|Table|Tab\.)\s*(\d+)\s*[:.]\s*", re.I)
TAG = re.compile(r"\\tag\s*\{([^{}]*)\}")
REFKEY = re.compile(r"^\s*\[(\d+)\]\s*")
FOOTMARK = re.compile(r"^\s*([∗*†‡§¶]+|\d{1,2})(?=[^\d\s])")
_DIGIT_DOT = re.compile(r"(?<=\d)\s*\.\s*(?=\d)")
_DIGIT_GAP = re.compile(r"(?<=\d)\s+(?=\d)")
_WORDCMD = re.compile(r"\\(operatorname|mathrm|text|mathit|mathbf|mathsf)\s*\{([^{}]*)\}")


def normalize_tex(s: str) -> str:
    """Only merges whitespace MinerU inserts between digits and between letters of a text command; does not change symbols."""
    s = _DIGIT_DOT.sub(".", s)
    s = _DIGIT_GAP.sub("", s)

    def join(m):
        toks = m.group(2).split()
        inner = "".join(toks) if toks and all(len(t) == 1 for t in toks) else m.group(2).strip()
        return "\\%s{%s}" % (m.group(1), inner)

    return _WORDCMD.sub(join, s).strip()


def spans_of(content) -> list[dict]:
    if isinstance(content, str):
        return [{"t": "text", "v": content}]
    out = []
    for s in content or []:
        t, v = s.get("type"), s.get("content", "")
        if t == "equation_inline":
            out.append({"t": "math", "v": normalize_tex(v)})
        elif t == "text":
            out.append({"t": "sup" if "superscript" in (s.get("styles") or []) else "text", "v": v})
    return out


def _strip(spans, rx):
    """Cut the prefix matching `rx` off the first text span; return the match or None."""
    for s in spans:
        if s["t"] == "text" and s["v"].strip():
            m = rx.match(s["v"])
            if m:
                s["v"] = s["v"][m.end():]
            return m
    return None


def _img(part, gdir, root):
    p = part.get("image_path") if isinstance(part, dict) else None
    return (Path(gdir) / p).resolve().relative_to(Path(root).resolve()).as_posix() if p else None


def _block(b, kind, page, gdir, root) -> dict:
    out = {"id": f"p{page}b{b['index']}", "kind": kind, "page": page, "bbox": b["bbox"]}
    c = b["content"]
    if kind == "heading":
        spans = spans_of(c)
        m = _strip(spans, NUM_HEAD)
        number = m.group(1) if m else None
        level = 1 if b["type"] == "doc_title" else (2 + number.count(".") if number else b.get("level", 2))
        out.update(spans=spans, number=number, level=level, label=f"sec:{number}" if number else None)
    elif kind == "paragraph":
        out["spans"] = spans_of(c)
    elif kind == "equation":
        tex = c if isinstance(c, str) else "".join(s.get("content", "") for s in c)
        m = TAG.search(tex)
        tag = m.group(1).strip() if m else None
        out.update(tex=normalize_tex(TAG.sub("", tex)), tag=tag, label=f"eq:{tag}" if tag else None,
                   image=_img(b, gdir, root))
    elif kind in ("table", "figure"):
        parts = {p["type"].rsplit("_", 1)[-1]: p for p in c if isinstance(p, dict)}
        cap = spans_of(parts["caption"]["content"]) if "caption" in parts else []
        m = _strip(cap, CAPTION)
        out.update(caption=cap, label=(f"{'tab' if kind == 'table' else 'fig'}:{m.group(1)}" if m else None))
        body = parts.get("body", {})
        if kind == "table":
            cells = parse_table_html(body.get("content") or "")
            for cell in cells:
                cell["id"] = f"{out['id']}r{cell['r']}c{cell['c']}"
            out["cells"] = cells
        out["image"] = _img(body, gdir, root)
    elif kind == "footnote":
        spans = spans_of(c)
        m = _strip(spans, FOOTMARK)
        out.update(spans=spans, mark=m.group(1) if m else "")
    elif kind == "reference":
        spans = spans_of(c)
        m = _strip(spans, REFKEY)
        out.update(spans=spans, key=m.group(1) if m else None)
    return out


def _columns(blocks) -> int:
    paras = [b for b in blocks if b["kind"] == "paragraph"]
    half = sum(1 for b in paras if b["bbox"][2] <= 0.52 or b["bbox"][0] >= 0.48)
    return 2 if paras and half / len(paras) >= 0.6 else 1


def adapt(groups, root) -> dict:
    sizes: dict[int, list[float]] = {}
    blocks: list[dict] = []
    seen: set[str] = set()
    counts = {"tables": 0, "figures": 0, "tagged_equations": 0, "references": 0}
    npages, producer, tier = 0, "mineru", "?"
    for middle, gdir in groups:
        npages = max(npages, middle["metadata"]["document"]["page_count"])
        pr = middle["metadata"].get("producer") or {}
        producer = f"{pr.get('name', 'mineru')}-{pr.get('version', '?')}"
        tier = middle["extensions"]["mineru"]["tier"]
        for lp in middle["extensions"]["docvortex_layout"]["pages"]:
            sizes[lp["page_idx"] + 1] = [lp["width_pt"], lp["height_pt"]]
        for page in middle["pages"]:
            for b in page["blocks"]:
                t = b["type"]
                counts["tables"] += t == "table"
                counts["figures"] += t in ("chart", "image")
                counts["references"] += t == "ref_text"
                counts["tagged_equations"] += t == "equation" and bool(TAG.search(str(b["content"])))
                kind = KIND.get(t)
                if kind is None:
                    continue
                blk = _block(b, kind, page["page_idx"] + 1, gdir, root)
                lab = blk.get("label")
                if lab in seen:
                    blk["label"] = None
                elif lab:
                    seen.add(lab)
                blocks.append(blk)
    nref = 0
    for blk in blocks:
        if blk["kind"] == "reference":
            nref += 1
            blk["key"] = blk["key"] or str(nref)
    cols = _columns(blocks)
    for blk in blocks:
        if blk["kind"] in ("table", "figure", "equation"):
            blk["wide"] = cols == 2 and blk["bbox"][2] - blk["bbox"][0] > 0.6
    default = sizes[min(sizes)] if sizes else [612.0, 792.0]
    return {"schema": "rosettex.document/1",
            "source": {"pages": npages, "page_size_pt": [sizes.get(i, default) for i in range(1, npages + 1)],
                       "columns": cols, "parser": f"{producer}/{tier}", "counts": counts},
            "blocks": blocks}
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_adapter.py -v`
Expected: PASS (4 tests). If the table cell count or the `math` count of `p1b9` differs, print that block and compare with the fixture **before** changing the test. The fixture is the truth; the test may only be changed to match the fixture, never the fixture.

- [ ] **Step 5: Commit**

```bash
git add rosettex/extract/mineru_adapter.py tests/test_adapter.py
git commit -m "feat: convert MinerU docvortex.middle 2.0 output to document.json

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: `segment.py`: merge paragraphs, keep verbatim, placeholders, links, batches

**Files:**
- Create: `rosettex/extract/segment.py`
- Test: `tests/test_segment.py`

**Interfaces:**
- Consumes:
  - `adapt` and the block structure (Task 5).
  - `escape_latex` (Task 4).
- Produces:
  - `merge_paragraphs(blocks) -> list`.
  - `assign_roles(blocks) -> None` (assigns `role ∈ {authors, affiliation, reference}`).
  - `encode(spans, ctx) -> tuple[str, dict]`, with `ctx = {"labels": set, "refs": set}`. The second element is `ph`: `{name: {"src", "tex"}}`.
  - `build_units(doc) -> list[unit]`, each unit has `{id, type, block, page, section, src, ph}`. `type ∈ {heading, paragraph, footnote, caption, cell}`. A caption has `id = <block>.cap`; a table cell has `id = <cell id>`.
  - `make_batches(units, max_chars=3000) -> list[batch]`, each batch has `{batch:"b001", section, items:[{id,type,page,text,placeholders{name:src}}]}`.
  - `segment(doc, max_chars=3000) -> tuple[doc (with "units" added), batches]`.

- [ ] **Step 1: Write the failing test**

`tests/test_segment.py`:
```python
import json

from conftest import FIX
from rosettex.extract.mineru_adapter import adapt
from rosettex.extract.segment import encode, segment


def seg(tier="standard", name="short", max_chars=3000):
    d = FIX / f"mineru-4.2.3-{tier}"
    m = json.loads((d / f"{name}.middle.json").read_text(encoding="utf-8"))
    return segment(adapt([(m, d)], d), max_chars)


def test_merge_across_columns_not_across_equation():
    doc, _ = seg()
    B = {b["id"]: b for b in doc["blocks"]}
    assert "p1b20" not in B and B["p1b15"]["merged"] == ["p1b15", "p1b20"]
    assert "p1b21" in B  # starts with a capital letter
    assert "p1b13" in B  # "with λ…" follows the equation: do not merge across an equation


def test_roles():
    doc, _ = seg()
    B = {b["id"]: b for b in doc["blocks"]}
    assert B["p1b1"]["role"] == "authors" and B["p1b3"]["role"] == "authors"
    assert B["p1b22"]["role"] == "affiliation" and B["p1b23"]["role"] == "affiliation"
    assert "role" not in B["p1b24"] and B["p2b1"]["role"] == "reference"


def test_units_placeholders_and_links():
    doc, _ = seg()
    U = {u["id"]: u for u in doc["units"]}
    u = U["p1b7"]
    assert "⟦c1⟧" in u["src"] and u["ph"]["c1"] == {"src": "[4]", "tex": r"\cite{ref4}"}
    u = U["p1b11"]
    assert u["src"].startswith("As shown in Eq. ⟦r1⟧, the factor ⟦m1⟧")
    assert u["ph"]["r1"] == {"src": "(1)", "tex": r"\eqref{eq:1}"}
    assert any(p["tex"] == r"\cite{ref1,ref2}" for p in u["ph"].values())
    texs = [p["tex"] for p in U["p1b15"]["ph"].values()]
    assert r"\ref{tab:1}" in texs and r"\ref{fig:1}" in texs and r"\ref{sec:2}" in texs
    assert "p1b1" not in U and "p2b1" not in U  # kept verbatim: not in a batch
    assert "p1b0" in U and "p1b17.cap" in U and "p1b17r2c0" in U  # heading, caption, cell "ByteNet"
    assert "p1b17r2c2" not in U and "p1b17r2c1" not in U  # numeric cells "23.75", "1.2B"


def test_eq_reference_recognized_inside_math_basic():
    doc, _ = seg("basic")
    u = {u["id"]: u for u in doc["units"]}["p1b11"]
    assert "Eq. ⟦r1⟧" in u["src"] and u["ph"]["r1"]["tex"] == r"\eqref{eq:1}"
    assert not any("Eq" in p["src"] for n, p in u["ph"].items() if n.startswith("m"))


def test_unknown_citation_and_label_stay_text():
    text, ph = encode([{"t": "text", "v": "see [9] and Figure 7"}], {"labels": {"fig:1"}, "refs": {"1"}})
    assert text == "see [9] and Figure 7" and ph == {}


def test_url_and_superscript():
    text, ph = encode([{"t": "text", "v": "code at https://x.org/a_b%20c."}, {"t": "sup", "v": "1"}],
                      {"labels": set(), "refs": set()})
    assert text == "code at ⟦u1⟧.⟦m1⟧"
    assert ph["u1"]["tex"] == r"\url{https://x.org/a_b\%20c}"
    assert ph["m1"]["tex"] == r"\textsuperscript{1}"


def test_batches_keep_tables_together_and_respect_size():
    doc, batches = seg("standard", "long", max_chars=3000)
    ids = [it["id"] for b in batches for it in b["items"]]
    assert len(ids) == len(set(ids)) == len(doc["units"])
    for b in batches:
        size = sum(len(it["text"]) for it in b["items"])
        tables = {it["id"].split("r")[0] for it in b["items"] if it["type"] == "cell"}
        assert size <= 3000 or len(b["items"]) == 1 or tables
    owner = {}
    for b in batches:
        for it in b["items"]:
            if it["type"] == "cell":
                owner.setdefault(it["id"].split("r")[0], set()).add(b["batch"])
    assert all(len(v) == 1 for v in owner.values())
```

- [ ] **Step 2: Run to see the failure**

Run: `uv run pytest tests/test_segment.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write `rosettex/extract/segment.py`**

```python
"""Merge paragraphs, keep-verbatim roles, placeholders/links, translation units and batches (SG 9.4–9.8, slice 1 spec section 6)."""
from __future__ import annotations

import itertools
import re

from ..tex import escape_latex

FLOATS = {"table", "figure", "footnote"}
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
URL = re.compile(r"https?://[^\s)\]}>,]+[^\s)\]}>,.]|[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
CITE = re.compile(r"\[(\d+(?:\s*[,–-]\s*\d+)*)\]")
XREF = re.compile(r"\b(Figures?|Figs?\.|Tables?|Tabs?\.|Sections?|Secs?\.|Equations?|Eqs?\.)"
                  r"(\s*~?\s*)(\()?(\d+(?:\.\d+)*)(\))?")
TEXT_MATH = re.compile(r"^\\(?:mathrm|text)\{(.*)\}$")
AFFIL = re.compile(r"Universit|Institut|Laborator|\bLabs?\b|College|School of|Department|\bDept\.", re.I)
NUMERIC_CELL = re.compile(r"(?:[\s\d.,%±+\-–—−()×*/~<>=]|(?<=\d)[KMBGT])*")


def plain(spans) -> str:
    return "".join(s["v"] for s in spans if s["t"] in ("text", "sup")).strip()


def _ends_sentence(spans) -> bool:
    for s in reversed(spans):
        v = s["v"].strip()
        if v:
            return s["t"] == "text" and v.endswith((".", "!", "?", ":"))
    return True


def _starts_lower(spans) -> bool:
    for s in spans:
        v = s["v"].strip()
        if v:
            return s["t"] == "math" or (s["t"] == "text" and v[0].islower())
    return False


def merge_paragraphs(blocks) -> list:
    out, open_i = [], None
    for b in blocks:
        if b["kind"] in FLOATS:
            out.append(b)
            continue
        if b["kind"] == "paragraph" and open_i is not None and _starts_lower(b["spans"]):
            a = out[open_i]
            a["spans"] = a["spans"] + [{"t": "text", "v": " "}] + b["spans"]
            a.setdefault("merged", [a["id"]]).append(b["id"])
        else:
            out.append(b)
            open_i = len(out) - 1 if b["kind"] == "paragraph" else None
            if open_i is None:
                continue
        if _ends_sentence(out[open_i]["spans"]):
            open_i = None
    return out


def assign_roles(blocks) -> None:
    in_front = False
    for b in blocks:
        k = b["kind"]
        if k == "heading":
            in_front = b["level"] == 1 and b["page"] == 1
            continue
        if k == "reference":
            b["role"] = "reference"
            continue
        if k not in ("paragraph", "footnote"):
            continue
        t = plain(b["spans"])
        if k == "paragraph" and in_front and len(t) < 120:
            b["role"] = "authors"
        elif len(t) < 200 and (EMAIL.search(t) or (k == "footnote" and AFFIL.search(t))):
            b["role"] = "affiliation"


def unwrap_text_math(spans) -> list:
    """`\\mathrm{Eq.~(1)}` (MinerU misrecognizes a reference as a formula) → text span."""
    out = []
    for s in spans:
        if s["t"] == "math":
            m = TEXT_MATH.match(s["v"].strip())
            if m and XREF.fullmatch(m.group(1).replace("~", " ").strip()):
                out.append({"t": "text", "v": " " + m.group(1).replace("~", " ").strip() + " "})
                continue
        out.append(s)
    return out


def _add(ph, counters, kind, src, tex) -> str:
    counters[kind] = counters.get(kind, 0) + 1
    name = f"{kind}{counters[kind]}"
    ph[name] = {"src": src, "tex": tex}
    return f"⟦{name}⟧"


def _expand(keys: str) -> list[str]:
    out = []
    for part in keys.split(","):
        bounds = re.split(r"[–-]", part)
        lo, hi = int(bounds[0]), int(bounds[-1])
        out += [str(k) for k in range(lo, hi + 1)] if 0 <= hi - lo < 50 else [str(lo), str(hi)]
    return out


def _resolve(kind, m, ctx):
    """→ (text before, src, tex, text after) or None when it is not a valid link."""
    if kind == "u":
        url = m.group(0)
        return "", url, "\\url{" + url.replace("%", "\\%").replace("#", "\\#") + "}", ""
    if kind == "c":
        keys = _expand(m.group(1))
        if not all(k in ctx["refs"] for k in keys):
            return None
        return "", m.group(0), "\\cite{" + ",".join("ref" + k for k in keys) + "}", ""
    word, gap, op, num, cl = m.groups()
    w = word.lower()
    lab = ("fig" if w.startswith("fig") else "tab" if w.startswith("tab")
           else "sec" if w.startswith("sec") else "eq") + ":" + num
    if lab not in ctx["labels"]:
        return None
    gap = gap.replace("~", " ")
    if op and cl:
        tex = "\\eqref{%s}" % lab if lab.startswith("eq:") else "(\\ref{%s})" % lab
        return word + gap, op + num + cl, tex, ""
    return word + gap + (op or ""), num, "\\ref{%s}" % lab, cl or ""


def _encode_text(v, ctx, parts, ph, counters) -> None:
    pos = 0
    while pos < len(v):
        best = None
        for kind, rx in (("u", URL), ("c", CITE), ("r", XREF)):
            m = rx.search(v, pos)
            if m and (best is None or m.start() < best[1].start()):
                best = (kind, m)
        if best is None:
            parts.append(v[pos:])
            return
        kind, m = best
        rep = _resolve(kind, m, ctx)
        if rep is None:
            parts.append(v[pos:m.end()])
        else:
            before, src, tex, after = rep
            parts.append(v[pos:m.start()] + before)
            parts.append(_add(ph, counters, kind, src, tex))
            parts.append(after)
        pos = m.end()


def encode(spans, ctx) -> tuple[str, dict]:
    parts, ph, counters = [], {}, {}
    for s in unwrap_text_math(spans):
        if s["t"] == "text":
            _encode_text(s["v"], ctx, parts, ph, counters)
        elif s["t"] == "math":
            parts.append(_add(ph, counters, "m", s["v"], "$" + s["v"] + "$"))
        elif s["t"] == "sup":
            parts.append(_add(ph, counters, "m", s["v"], "\\textsuperscript{" + escape_latex(s["v"]) + "}"))
    text = " ".join("".join(parts).split())
    return re.sub(r"\s+([,.;:)])", r"\1", text), ph


def _unit(uid, typ, block, section, src, ph) -> dict:
    return {"id": uid, "type": typ, "block": block["id"], "page": block["page"], "section": section,
            "src": src, "ph": ph}


def build_units(doc) -> list[dict]:
    blocks = doc["blocks"]
    ctx = {"labels": {b["label"] for b in blocks if b.get("label")},
           "refs": {b["key"] for b in blocks if b["kind"] == "reference" and b.get("key")}}
    units, section = [], ""
    for b in blocks:
        k = b["kind"]
        if b.get("role"):
            continue
        if k == "heading":
            text, ph = encode(b["spans"], ctx)
            section = ((b.get("number") or "") + " " + plain(b["spans"])).strip()
            if text:
                units.append(_unit(b["id"], "heading", b, section, text, ph))
        elif k in ("paragraph", "footnote"):
            text, ph = encode(b["spans"], ctx)
            if text:
                units.append(_unit(b["id"], k, b, section, text, ph))
        elif k in ("table", "figure"):
            text, ph = encode(b.get("caption") or [], ctx)
            if text:
                units.append(_unit(b["id"] + ".cap", "caption", b, section, text, ph))
            for cell in b.get("cells", []):
                if cell["text"] and not NUMERIC_CELL.fullmatch(cell["text"]):
                    text, ph = encode([{"t": "text", "v": cell["text"]}], ctx)
                    units.append(_unit(cell["id"], "cell", b, section, text, ph))
    return units


def make_batches(units, max_chars: int = 3000) -> list[dict]:
    groups = [list(g) for _, g in itertools.groupby(
        units, key=lambda u: u["block"] if u["type"] in ("cell", "caption") else u["id"])]
    batches, cur, size = [], [], 0
    for g in groups:
        n = sum(len(u["src"]) for u in g)
        if cur and size + n > max_chars:
            batches.append(cur)
            cur, size = [], 0
        cur.extend(g)
        size += n
    if cur:
        batches.append(cur)
    return [{"batch": f"b{i:03d}", "section": g[0]["section"],
             "items": [{"id": u["id"], "type": u["type"], "page": u["page"], "text": u["src"],
                        "placeholders": {k: v["src"] for k, v in u["ph"].items()}} for u in g]}
            for i, g in enumerate(batches, 1)]


def segment(doc, max_chars: int = 3000):
    doc["blocks"] = merge_paragraphs(doc["blocks"])
    assign_roles(doc["blocks"])
    doc["units"] = build_units(doc)
    return doc, make_batches(doc["units"], max_chars)
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_segment.py -v`
Expected: PASS (7 tests). If `test_units_placeholders_and_links` fails on the `p1b11` leading string, print `U["p1b11"]["src"]` to see the whitespace MinerU produced. Only fix the whitespace-cleanup regex in `encode`, do not loosen the test.

- [ ] **Step 5: Commit**

```bash
git add rosettex/extract/segment.py tests/test_segment.py
git commit -m "feat: merge paragraphs, keep-verbatim roles, placeholders and internal links, batching

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Language packs + `checks.py`

**Files:**
- Create: `rosettex/langpacks/vi.json`, `rosettex/langpacks/default.json`, `rosettex/checks.py`
- Test: `tests/test_checks.py`

**Interfaces:**
- Consumes: none.
- Produces:
  - `load_pack(code) -> dict`, with the keys `code, tested, polyglossia, font{main,options}, check_chars, letters, function_words, min_len_check, min_diacritic_ratio, min_function_ratio, length_ratio, labels{fallback,missing_image}, rules`.
  - `nfc(s)`.
  - `PH_RE`.
  - `placeholders(s) -> Counter`.
  - `numbers(s) -> Counter`.
  - `looks_untranslated(src, dst, pack, keep=()) -> bool`.
  - `bad_chars(src, dst, pack) -> list[str]`.
  - `check_item(item, dst, pack, terms) -> (errors, warnings)`.
  - `check_batch(batch, submitted, pack, terms) -> {"errors": {id: [...]}, "warnings": {id: [...]}}`.

- [ ] **Step 1: Write the two language packs**

`rosettex/langpacks/vi.json`:
```json
{
 "code": "vi",
 "name": "Tiếng Việt",
 "tested": true,
 "polyglossia": "vietnamese",
 "font": {"main": "texgyretermes",
          "options": "Extension=.otf,UprightFont=*-regular,BoldFont=*-bold,ItalicFont=*-italic,BoldItalicFont=*-bolditalic"},
 "check_chars": "áàảãạăắằẳẵặâấầẩẫậéèẻẽẹêếềểễệíìỉĩịóòỏõọôốồổỗộơớờởỡợúùủũụưứừửữựýỳỷỹỵđÁÀẢÃẠĂẮẰẲẴẶÂẤẦẨẪẬÉÈẺẼẸÊẾỀỂỄỆÍÌỈĨỊÓÒỎÕỌÔỐỒỔỖỘƠỚỜỞỠỢÚÙỦŨỤƯỨỪỬỮỰÝỲỶỸỴĐ",
 "letters": "áàảãạăắằẳẵặâấầẩẫậéèẻẽẹêếềểễệíìỉĩịóòỏõọôốồổỗộơớờởỡợúùủũụưứừửữựýỳỷỹỵđÁÀẢÃẠĂẮẰẲẴẶÂẤẦẨẪẬÉÈẺẼẸÊẾỀỂỄỆÍÌỈĨỊÓÒỎÕỌÔỐỒỔỖỘƠỚỜỞỠỢÚÙỦŨỤƯỨỪỬỮỰÝỲỶỸỴĐ",
 "function_words": ["của", "và", "là", "các", "những", "được", "trong", "cho", "với", "không", "một", "này",
                    "có", "để", "từ", "khi", "theo", "đến", "như", "về", "trên", "bằng", "hơn", "thì", "đã",
                    "sẽ", "cũng", "mà", "nên", "vì", "bị", "tại", "do", "nhưng", "hoặc", "nếu"],
 "min_len_check": 40,
 "min_diacritic_ratio": 0.03,
 "min_function_ratio": 0.05,
 "length_ratio": [0.7, 2.0],
 "labels": {"fallback": "công thức/bảng chưa dựng được", "missing_image": "thiếu ảnh"},
 "rules": [
  "Keep every ⟦…⟧ placeholder exactly as in the source (its position in the sentence may change to read naturally).",
  "Keep every number and its notation as in the source, including the decimal point: 0.05 stays 0.05, do not write 0,05.",
  "Do not translate proper names, names of methods/models/datasets, units, emails, URLs.",
  "Preserve the level of certainty of the source sentence (may, might, suggest, likely, significant…).",
  "Check negation and the direction of comparisons carefully (more/less, higher/lower, outperform, at least/at most).",
  "Use the exact terms in terms.json, consistently across the whole paper.",
  "Table cells: translate the text, keep numbers and symbols.",
  "Natural Vietnamese academic style; add no translator's notes."
 ]
}
```

`rosettex/langpacks/default.json`:
```json
{
 "code": "default",
 "name": "default",
 "tested": false,
 "polyglossia": "english",
 "font": {"main": "texgyretermes",
          "options": "Extension=.otf,UprightFont=*-regular,BoldFont=*-bold,ItalicFont=*-italic,BoldItalicFont=*-bolditalic"},
 "check_chars": "",
 "letters": "",
 "function_words": [],
 "min_len_check": 40,
 "min_diacritic_ratio": 0,
 "min_function_ratio": 0,
 "length_ratio": [0.5, 2.5],
 "labels": {"fallback": "formula/table not rendered", "missing_image": "missing image"},
 "rules": [
  "Keep every placeholder ⟦…⟧ exactly (you may move it within the sentence).",
  "Keep every number exactly as written in the source, including the decimal point.",
  "Do not translate proper names, method/model/dataset names, units, emails, URLs.",
  "Keep the original level of certainty and the direction of comparisons and negations.",
  "Use the terms in terms.json consistently."
 ]
}
```

- [ ] **Step 2: Write the failing test (including the SG 14 regressions)**

`tests/test_checks.py`:
```python
import unicodedata

from rosettex.checks import bad_chars, check_batch, load_pack, looks_untranslated, numbers

VI = load_pack("vi")
SRC = ("Our model reaches 28.4 BLEU, outperforming the best previous result by 2.0 points "
       "while costing only ⟦m1⟧ FLOPs, as shown in Table ⟦r1⟧.")
GOOD = ("Mô hình của chúng tôi đạt 28.4 BLEU, vượt kết quả tốt nhất trước đó 2.0 điểm "
        "trong khi chỉ tốn ⟦m1⟧ FLOPs, như trình bày ở Bảng ⟦r1⟧.")
BATCH = {"batch": "b001", "items": [{"id": "p1b20", "type": "paragraph", "page": 1, "text": SRC, "placeholders": {}}]}
TERMS = {"topic": "dịch máy", "terms": [{"src": "BLEU", "dst": "BLEU"}], "keep": [], "abbreviations": []}


def errs(dst):
    return check_batch(BATCH, {"p1b20": dst}, VI, TERMS)["errors"]


def test_correct_vietnamese_passes():  # regression: the Han-character-based check flagged a correct Vietnamese translation
    assert errs(GOOD) == {}


def test_reordered_placeholders_pass():
    assert errs("Như trình bày ở Bảng ⟦r1⟧, mô hình của chúng tôi đạt 28.4 BLEU, vượt kết quả tốt nhất "
                "trước đó 2.0 điểm trong khi chỉ tốn ⟦m1⟧ FLOPs.") == {}


def test_added_number_passes():
    assert errs(GOOD.replace("trước đó", "trước đó (tức 2 hệ thống)")) == {}


def test_changed_number_rejected():  # regression: PaperReader accepted a translation whose figures had been altered
    assert "missing or changed numbers" in " ".join(errs(GOOD.replace("28.4", "28.5"))["p1b20"])


def test_decimal_comma_rejected():
    assert "missing or changed numbers" in " ".join(errs(GOOD.replace("2.0", "2,0"))["p1b20"])


def test_placeholder_missing_or_altered_rejected():
    assert "missing placeholder" in " ".join(errs(GOOD.replace("⟦m1⟧", ""))["p1b20"])
    assert "extra or wrong placeholder" in " ".join(errs(GOOD.replace("⟦m1⟧", "⟦m2⟧"))["p1b20"])


def test_english_left_untranslated_rejected():
    assert "looks untranslated" in errs(SRC)["p1b20"]
    assert looks_untranslated("short", "short", VI) is False  # short passage: no conclusion


def test_nfd_input_is_equivalent():
    assert errs(unicodedata.normalize("NFD", GOOD)) == {}


def test_missing_and_extra_ids():
    res = check_batch(BATCH, {"zzz": GOOD}, VI, TERMS)["errors"]
    assert res["p1b20"] == ["missing translation for this id"] and res["zzz"] == ["id not in this batch"]


def test_bad_chars():
    assert bad_chars("abc", "Mô hình \ufffd", VI) == ["\ufffd"]
    assert bad_chars("Schölkopf et al.", "Schölkopf và cộng sự", VI) == []
    assert bad_chars("x", "日本", VI) == ["日", "本"]


def test_numbers_thousands_normalized():
    assert numbers("1,000 and 1000 and 0.05") == numbers("1000 và 1,000 và 0.05")


def test_term_warning_and_unknown_language_pack():
    w = check_batch(BATCH, {"p1b20": GOOD.replace("BLEU", "điểm B")}, VI, TERMS)["warnings"]
    assert any("BLEU" in x for x in w["p1b20"])
    fr = load_pack("fr")
    assert fr["code"] == "fr" and fr["tested"] is False
```

- [ ] **Step 3: Run to see the failure**

Run: `uv run pytest tests/test_checks.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'rosettex.checks'`

- [ ] **Step 4: Write `rosettex/checks.py`**

```python
"""Language packs and translation checks (accept per batch, validate for the whole paper). Comparisons always on NFC."""
from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter
from importlib.resources import files

PH_RE = re.compile(r"⟦([a-z])(\d+)⟧")
NUM_RE = re.compile(r"\d+(?:[.,]\d+)*")
THOUSANDS = re.compile(r"\d{1,3}(?:,\d{3})+")


def load_pack(code: str) -> dict:
    base = files("rosettex") / "langpacks"
    f = base / f"{code}.json"
    if re.fullmatch(r"[a-z]{2,3}(?:-[A-Za-z]{2,8})?", code) and f.is_file():
        return json.loads(f.read_text(encoding="utf-8"))
    pack = json.loads((base / "default.json").read_text(encoding="utf-8"))
    pack["code"] = code
    return pack


def nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


def placeholders(s: str) -> Counter:
    return Counter(m.group(0) for m in PH_RE.finditer(s))


def numbers(s: str) -> Counter:
    out = Counter()
    for m in NUM_RE.finditer(PH_RE.sub(" ", s)):
        v = m.group(0)
        out[v.replace(",", "") if THOUSANDS.fullmatch(v) else v] += 1
    return out


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
    if not pack.get("letters"):
        return False
    special = set(pack["letters"])
    diacritic = sum(c in special for c in letters) / len(letters)
    words = re.findall(r"\w+", rest.lower())
    fw = set(pack["function_words"])
    function = sum(w in fw for w in words) / max(len(words), 1)
    return diacritic < pack["min_diacritic_ratio"] and function < pack["min_function_ratio"]


def bad_chars(src: str, dst: str, pack: dict) -> list[str]:
    allowed = set(pack.get("letters", "")) | set(src)
    bad = []
    for c in dst:
        if c in allowed or (c.isascii() and (c.isprintable() or c in "\n\t")):
            continue
        cat = unicodedata.category(c)
        if c == "\ufffd" or cat.startswith("C"):
            bad.append(c)
        elif cat.startswith("L") and pack.get("letters") and not 0x0370 <= ord(c) <= 0x03FF:
            bad.append(c)
    return bad


def check_item(item: dict, dst: str, pack: dict, terms: dict | None) -> tuple[list[str], list[str]]:
    errs, warns = [], []
    src, dst = nfc(item["text"]), nfc(dst or "")
    if not dst.strip():
        return ["empty paragraph"], []
    ps, pd = placeholders(src), placeholders(dst)
    if ps - pd:
        errs.append("missing placeholder: " + " ".join(sorted((ps - pd).elements())))
    if pd - ps:
        errs.append("extra or wrong placeholder: " + " ".join(sorted((pd - ps).elements())))
    lost = numbers(src) - numbers(dst)
    if lost:
        errs.append("missing or changed numbers: " + " ".join(sorted(lost.elements()))
                    + " (keep the number notation as in the source, including the decimal point)")
    if looks_untranslated(src, dst, pack, (terms or {}).get("keep", [])):
        errs.append("looks untranslated")
    bad = bad_chars(src, dst, pack)
    if bad:
        errs.append("unexpected characters (encoding error?): " + "".join(sorted(set(bad))))
    lo, hi = pack["length_ratio"]
    if len(src) >= pack["min_len_check"] and not lo <= len(dst) / len(src) <= hi:
        warns.append(f"length ratio {len(dst) / len(src):.2f} outside [{lo}, {hi}]")
    for t in (terms or {}).get("terms", []):
        if t["src"].lower() in src.lower() and nfc(t["dst"]).lower() not in dst.lower():
            warns.append(f"term '{t['src']}' should be translated as '{t['dst']}'")
    return errs, warns


def check_batch(batch: dict, submitted: dict, pack: dict, terms: dict | None) -> dict:
    errors, warnings = {}, {}
    ids = [it["id"] for it in batch["items"]]
    for extra in sorted(set(submitted) - set(ids)):
        errors[extra] = ["id not in this batch"]
    for it in batch["items"]:
        if it["id"] not in submitted:
            errors[it["id"]] = ["missing translation for this id"]
            continue
        e, w = check_item(it, submitted[it["id"]], pack, terms)
        if e:
            errors[it["id"]] = e
        if w:
            warnings[it["id"]] = w
    return {"errors": errors, "warnings": warnings}
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_checks.py -v`
Expected: PASS (12 test)

- [ ] **Step 6: Commit**

```bash
git add rosettex/langpacks rosettex/checks.py tests/test_checks.py
git commit -m "feat: vi/default language packs and translation checks (placeholder and number multisets)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: `translate.py`: terms, outline, next, accept, validate

**Files:**
- Create: `rosettex/translate.py`
- Modify: `tests/conftest.py` (add `make_work`, `fake_vi`, `translate_all`)
- Test: `tests/test_translate.py`

**Interfaces:**
- Consumes:
  - `Work`, `read_json`, `write_json`, `UsageError`, `q` (Task 1).
  - `render_page_png` (Task 3).
  - `check_batch`, `load_pack`, `nfc` (Task 7).
  - `adapt`, `segment` (Task 5 and 6, used in conftest).
- Produces:
  - `manifest(work) -> dict`.
  - `batch_ids(work) -> list[str]`.
  - `translations(work, lang) -> dict[id, str]`.
  - `check_terms(t) -> list[str]`.
  - `write_terms(work, file) -> dict`.
  - `outline(work) -> dict`.
  - `page_image(work, page) -> Path`.
  - `next_package(work, batch_id=None) -> dict`.
  - `accept(work, batch_id, file) -> dict` (`{"ok", "errors", "warnings"}`; also appends a line to `logs/accept.jsonl`).
  - `validate(work) -> dict` (`{"ok", "errors", "warnings", "mtime"}`).
  - conftest:
    - `make_work(tmp_path, tier="standard", name="short", lang="vi") -> Work`.
    - `fake_vi(src) -> str`.
    - `translate_all(work)`.

- [ ] **Step 1: Extend `tests/conftest.py`**

Replace the whole of `tests/conftest.py` with:
```python
import json
import re
import shutil
import zipfile
from pathlib import Path

FIX = Path(__file__).parent / "fixtures"
_WORD = re.compile(r"⟦[a-z]\d+⟧|\S+")


def fake_vi(src: str) -> str:
    """'Fake translator': keep placeholders and every word containing a digit, replace other words with Vietnamese words."""
    out = []
    for i, w in enumerate(_WORD.findall(src)):
        out.append(w if w.startswith("⟦") or re.search(r"\d", w) else ("của" if i % 3 == 0 else "việc"))
    return " ".join(out)


def make_work(tmp_path, tier="standard", name="short", lang="vi"):
    """A work directory with extraction already done, built straight from the fixture (no MinerU needed)."""
    from rosettex.extract.mineru_adapter import adapt
    from rosettex.extract.segment import segment
    from rosettex.workspace import Work, write_json

    w = Work(tmp_path / "Tài liệu" / "work dir")
    w.root.mkdir(parents=True)
    shutil.copyfile(FIX / "papers" / f"{name}.pdf", w.src_pdf)
    d = FIX / f"mineru-4.2.3-{tier}"
    if (d / f"{name}.zip").exists():
        gdir = w.mineru / "g001" / "x"
        with zipfile.ZipFile(d / f"{name}.zip") as z:
            z.extractall(gdir)
        middle = json.loads((gdir / "middle_json.json").read_text(encoding="utf-8"))
    else:
        gdir = d
        middle = json.loads((d / f"{name}.middle.json").read_text(encoding="utf-8"))
    doc, batches = segment(adapt([(middle, gdir)], w.root if gdir != d else d))
    for b in batches:
        write_json(w.batch_path(b["batch"]), b)
    write_json(w.flagged, [])
    write_json(w.document, doc)
    write_json(w.manifest, {"lang": lang, "out": str(tmp_path / "Kết quả" / f"{name}.{lang}.pdf"),
                            "source": "fixture", "sha256": "x", "pages": doc["source"]["pages"],
                            "tier": tier, "tier_reason": "default", "pages_per_group": 50,
                            "extract_cmd": "rosettex extract fixture"})
    return w


TERMS = {"topic": "Kiến trúc chỉ dùng cơ chế chú ý cho dịch máy",
         "terms": [{"src": "attention", "dst": "chú ý"}], "keep": ["BLEU"], "abbreviations": []}


def translate_all(work):
    """Translate every batch with fake_vi and write the translation files directly."""
    from rosettex.translate import batch_ids
    from rosettex.workspace import read_json, write_json

    write_json(work.terms, TERMS)
    lang = read_json(work.manifest)["lang"]
    for bid in batch_ids(work):
        b = read_json(work.batch_path(bid))
        write_json(work.translation_path(bid, lang), {it["id"]: fake_vi(it["text"]) for it in b["items"]})
```

- [ ] **Step 2: Write the failing test**

`tests/test_translate.py`:
```python
import json

import pytest

from conftest import TERMS, fake_vi, make_work
from rosettex import translate
from rosettex.workspace import UsageError, read_json


def write(path, obj, bom=False, crlf=False):
    text = json.dumps(obj, ensure_ascii=False, indent=1)
    if crlf:
        text = text.replace("\n", "\r\n")
    path.write_bytes((b"\xef\xbb\xbf" if bom else b"") + text.encode("utf-8"))
    return path


def test_next_requires_terms(tmp_path):
    w = make_work(tmp_path)
    with pytest.raises(UsageError) as e:
        translate.next_package(w)
    assert "--outline" in e.value.next_action


def test_terms_validation(tmp_path):
    w = make_work(tmp_path)
    bad = translate.write_terms(w, write(tmp_path / "t.json", {"terms": [{"src": "a"}, {"src": "a", "dst": "b"}]}))
    assert bad["ok"] is False and len(bad["errors"]) == 2
    assert translate.write_terms(w, write(tmp_path / "t.json", TERMS))["ok"] is True


def test_outline(tmp_path):
    o = translate.outline(make_work(tmp_path))
    assert o["title"].startswith("Attention-Based") and o["abstract"].startswith("We propose")
    assert "2 Method" in o["headings"]


def test_next_accept_loop_and_validate(tmp_path):
    w = make_work(tmp_path)
    translate.write_terms(w, write(tmp_path / "t.json", TERMS))
    first = translate.next_package(w)
    assert first["done"] is False and first["batch"]["batch"] == "b001" and first["previous"] is None
    assert all(p.endswith(".png") and (w.pages / p.split("/")[-1].split("\\")[-1]).exists() for p in first["page_images"])
    assert "--batch b001" in first["submit"]
    bad = translate.accept(w, "b001", write(tmp_path / "b.json", {it["id"]: it["text"] for it in first["batch"]["items"]}))
    assert bad["ok"] is False and not w.translation_path("b001", "vi").exists()
    seen = []
    while True:
        pkg = translate.next_package(w)
        if pkg["done"]:
            break
        bid = pkg["batch"]["batch"]
        if seen:
            assert pkg["previous"]["dst"] is not None
        sub = {it["id"]: fake_vi(it["text"]) for it in pkg["batch"]["items"]}
        res = translate.accept(w, bid, write(tmp_path / f"{bid}.json", sub, bom=True, crlf=True))
        assert res["ok"] is True, res["errors"]
        seen.append(bid)
    assert seen == translate.batch_ids(w)
    log = (w.logs / "accept.jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(log[0])["ok"] is False and len(log) == len(seen) + 1
    v = translate.validate(w)
    assert v["ok"] is True, v["errors"]


def test_accept_stores_nfc(tmp_path):
    import unicodedata
    w = make_work(tmp_path)
    translate.write_terms(w, write(tmp_path / "t.json", TERMS))
    pkg = translate.next_package(w)
    sub = {it["id"]: unicodedata.normalize("NFD", fake_vi(it["text"])) for it in pkg["batch"]["items"]}
    assert translate.accept(w, "b001", write(tmp_path / "n.json", sub))["ok"] is True
    stored = read_json(w.translation_path("b001", "vi"))
    assert all(v == unicodedata.normalize("NFC", v) for v in stored.values())


def test_validate_reports_untranslated_batches(tmp_path):
    w = make_work(tmp_path)
    translate.write_terms(w, write(tmp_path / "t.json", TERMS))
    v = translate.validate(w)
    assert v["ok"] is False and v["errors"]["b001"] == ["batch not translated"]
```

- [ ] **Step 3: Run to see the failure**

Run: `uv run pytest tests/test_translate.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'rosettex.translate'`

- [ ] **Step 4: Write `rosettex/translate.py`**

```python
"""Communication with the translating agent: terms, outline, next, accept, validate."""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from .checks import check_batch, load_pack, nfc
from .pdf import render_page_png
from .workspace import UsageError, Work, q, read_json, write_json


def manifest(work: Work) -> dict:
    m = read_json(work.manifest)
    if m is None:
        raise UsageError(f"not a rosettex work directory: {work.root}")
    return m


def batch_ids(work: Work) -> list[str]:
    ids = [p.stem for p in work.batches.glob("b*.json") if re.fullmatch(r"b\d+", p.stem)]
    return sorted(ids, key=lambda s: int(s[1:]))


def translations(work: Work, lang: str) -> dict:
    out = {}
    for bid in batch_ids(work):
        out.update(read_json(work.translation_path(bid, lang), {}))
    return out


def check_terms(t) -> list[str]:
    if not isinstance(t, dict):
        return ["terms.json must be a JSON object"]
    errs = []
    if not isinstance(t.get("topic"), str) or not t["topic"].strip():
        errs.append("missing 'topic' (a one-sentence summary of the subject)")
    seen = set()
    for i, x in enumerate(t.get("terms", [])):
        if not (isinstance(x, dict) and isinstance(x.get("src"), str) and isinstance(x.get("dst"), str)
                and x["src"].strip() and x["dst"].strip()):
            errs.append(f"terms[{i}] needs 'src' and 'dst' as non-empty strings")
            continue
        key = x["src"].strip().lower()
        if key in seen:
        errs.append(f"duplicate 'src': {x['src']}")
        seen.add(key)
    if not all(isinstance(k, str) for k in t.get("keep", [])):
        errs.append("'keep' must be a list of strings")
    for i, x in enumerate(t.get("abbreviations", [])):
        if not (isinstance(x, dict) and isinstance(x.get("abbr"), str) and isinstance(x.get("expansion"), str)):
            errs.append(f"abbreviations[{i}] needs 'abbr' and 'expansion'")
    return errs


def write_terms(work: Work, file) -> dict:
    manifest(work)
    try:
        t = json.loads(Path(file).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as e:
        return {"ok": False, "errors": [f"cannot read JSON: {e}"]}
    errs = check_terms(t)
    if errs:
        return {"ok": False, "errors": errs}
    write_json(work.terms, t)
    return {"ok": True, "terms": len(t.get("terms", [])), "next_action": f"rosettex next {q(work.root)} --json"}


def _doc(work: Work) -> dict:
    doc = read_json(work.document)
    if doc is None:
        raise UsageError("extraction is not finished", next_action=f"rosettex status {q(work.root)} --json")
    return doc


def outline(work: Work) -> dict:
    doc = _doc(work)
    units = {u["id"]: u for u in doc["units"]}
    title, abstract, heads, after_abs = None, None, [], False
    for b in doc["blocks"]:
        if b["kind"] == "heading":
            text = "".join(s["v"] for s in b["spans"] if s["t"] == "text").strip()
            if b["level"] == 1 and title is None:
                title = text
                continue
            heads.append(((b.get("number") or "") + " " + text).strip())
            after_abs = text.lower() in ("abstract", "tóm tắt")
        elif b["kind"] == "paragraph" and after_abs and abstract is None and b["id"] in units:
            abstract = units[b["id"]]["src"]
            after_abs = False
    return {"title": title, "abstract": abstract, "headings": heads, "batches": len(batch_ids(work)),
            "units": len(doc["units"]),
            "terms_template": {"topic": "…", "terms": [{"src": "…", "dst": "…"}], "keep": [],
                               "abbreviations": [{"abbr": "…", "expansion": "…"}]},
            "next_action": f"rosettex terms {q(work.root)} --file <terms.json> --json"}


def page_image(work: Work, page: int) -> Path:
    out = work.pages / f"p{page:03d}.png"
    if not out.exists():
        render_page_png(work.src_pdf, page, out)
    return out


def next_package(work: Work, batch_id: str | None = None) -> dict:
    m = manifest(work)
    lang = m["lang"]
    terms = read_json(work.terms)
    if terms is None:
        raise UsageError("no terms.json yet: read the paper outline, then write the terms first",
                         next_action=f"rosettex next {q(work.root)} --outline --json")
    ids = batch_ids(work)
    if batch_id is None:
        pending = [b for b in ids if not work.translation_path(b, lang).exists()]
        if not pending:
            return {"done": True, "next_action": f"rosettex validate {q(work.root)} --json"}
        batch_id = pending[0]
    if batch_id not in ids:
        raise UsageError(f"no batch {batch_id}")
    batch = read_json(work.batch_path(batch_id))
    prev = None
    i = ids.index(batch_id)
    if i > 0:
        last = read_json(work.batch_path(ids[i - 1]))["items"][-1]
        prev = {"src": last["text"], "dst": read_json(work.translation_path(ids[i - 1], lang), {}).get(last["id"])}
    pages = sorted({it["page"] for it in batch["items"]})
    return {"done": False, "batch": batch, "topic": terms["topic"], "terms": terms, "section": batch["section"],
            "previous": prev, "page_images": [str(page_image(work, p)) for p in pages],
            "rules": load_pack(lang)["rules"],
            "format": 'JSON {"<id>": "<translation>"} containing every id of the batch',
            "submit": f"rosettex accept {q(work.root)} --batch {batch_id} --file <file.json> --json"}


def accept(work: Work, batch_id: str, file) -> dict:
    m = manifest(work)
    batch = read_json(work.batch_path(batch_id))
    if batch is None:
        raise UsageError(f"no batch {batch_id}")
    try:
        sub = json.loads(Path(file).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as e:
        res = {"errors": {"_file": [f"cannot read JSON: {e}"]}, "warnings": {}}
    else:
        if not isinstance(sub, dict) or not all(isinstance(v, str) for v in sub.values()):
            res = {"errors": {"_file": ['the file must be JSON {"<id>": "<translation>"}']}, "warnings": {}}
        else:
            res = check_batch(batch, sub, load_pack(m["lang"]), read_json(work.terms, {}))
    ok = not res["errors"]
    if ok:
        write_json(work.translation_path(batch_id, m["lang"]), {k: nfc(v) for k, v in sub.items()})
    work.logs.mkdir(parents=True, exist_ok=True)
    # ponytail: several subagents append short lines at once; safe enough for a log that measures the rejection rate, not for state.
    with open(work.logs / "accept.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"batch": batch_id, "ok": ok, "errors": sum(map(len, res["errors"].values())),
                             "t": time.time()}) + "\n")
    out = {"ok": ok, **res}
    if not ok:
        out["next_action"] = (f"fix the ids in 'errors' and resubmit the whole batch: rosettex accept {q(work.root)} "
                              f"--batch {batch_id} --file <file.json> --json")
    return out


def _translations_mtime(work: Work, lang: str) -> float:
    return max((p.stat().st_mtime for p in work.batches.glob(f"b*.{lang}.json")), default=0.0)


def validate(work: Work) -> dict:
    m = manifest(work)
    lang = m["lang"]
    pack, terms, doc = load_pack(lang), read_json(work.terms, {}), _doc(work)
    errors, warnings = {}, {}
    for bid in batch_ids(work):
        tr = read_json(work.translation_path(bid, lang))
        if tr is None:
            errors[bid] = ["batch not translated"]
            continue
        res = check_batch(read_json(work.batch_path(bid)), tr, pack, terms)
        errors.update(res["errors"])
        warnings.update(res["warnings"])
    labels = {b["label"] for b in doc["blocks"] if b.get("label")}
    refs = {b["key"] for b in doc["blocks"] if b["kind"] == "reference"}
    for u in doc["units"]:
        for p in u["ph"].values():
            for lab in re.findall(r"\\(?:eq)?ref\{([^}]*)\}", p["tex"]):
                if lab not in labels:
                    errors.setdefault(u["id"], []).append(f"label does not exist: {lab}")
            for keys in re.findall(r"\\cite\{([^}]*)\}", p["tex"]):
                for k in keys.split(","):
                    if k[3:] not in refs:
                        errors.setdefault(u["id"], []).append(f"bibliography reference does not exist: {k}")
    return {"ok": not errors, "errors": errors, "warnings": warnings, "mtime": _translations_mtime(work, lang)}
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_translate.py -v`
Expected: PASS (6 test)

- [ ] **Step 6: Commit**

```bash
git add rosettex/translate.py tests/conftest.py tests/test_translate.py
git commit -m "feat: terms, outline, next, accept, validate for the translating agent

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: `extract/run.py`: page groups, MinerU, flagging

**Files:**
- Create: `rosettex/extract/run.py`, `tests/fake_mineru.py`
- Test: `tests/test_extract.py`

**Interfaces:**
- Consumes:
  - `Work`, `read_json`, `write_json`, `run_with_heartbeat` (Task 1).
  - `text_in_bbox` (Task 3).
  - `adapt` (Task 5), `segment` (Task 6).
- Produces:
  - `class ExtractError(Exception)`.
  - `plan_groups(npages, size) -> list[tuple[int, int]]` (counted from 1).
  - `flag_suspicious(doc, pdf_path) -> list[dict]`, each element has `{id, page, reason, tex}`.
  - `extract_core(work, cmd, on_beat) -> dict`, with `on_beat(done, total)`. This function writes `document.json` **last**.
  - `tests/fake_mineru.py`: supports `models verify` and `parse … --pages a-b`. Environment variables:
    - `FAKE_MINERU_FAIL_ONCE=<flag file>`: fail the first time.
    - `FAKE_MINERU_FAIL_ALWAYS=1`: always fail.

- [ ] **Step 1: Write `tests/fake_mineru.py`**

```python
"""Fake MinerU for tests: `parse` returns a zip built from the fixture (filtered by --pages); `models verify` always passes."""
import json
import os
import sys
import zipfile
from pathlib import Path

FIX = Path(__file__).parent / "fixtures" / "mineru-4.2.3-standard"


def main(argv):
    if argv[:2] == ["models", "verify"]:
        print("MinerU-4_models_onnx: ok")
        return 0
    assert argv[0] == "parse", argv
    if os.environ.get("FAKE_MINERU_FAIL_ALWAYS"):
        return 1
    flag = os.environ.get("FAKE_MINERU_FAIL_ONCE")
    if flag and not Path(flag).exists():
        Path(flag).write_text("failed")
        return 1
    out = Path(argv[argv.index("-o") + 1])
    a, b = map(int, argv[argv.index("--pages") + 1].split("-"))
    with zipfile.ZipFile(FIX / "short.zip") as src:
        middle = json.loads(src.read("middle_json.json"))
        keep = lambda p: a - 1 <= p["page_idx"] <= b - 1  # noqa: E731
        middle["pages"] = [p for p in middle["pages"] if keep(p)]
        lay = middle["extensions"]["docvortex_layout"]
        lay["pages"] = [p for p in lay["pages"] if keep(p)]
        out.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(out / "src.zip", "w") as z:
            z.writestr("middle_json.json", json.dumps(middle, ensure_ascii=False))
            for n in src.namelist():
                if n.startswith("images/"):
                    z.writestr(n, src.read(n))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
```

- [ ] **Step 2: Write the failing test**

`tests/test_extract.py`:
```python
import json
import os
import shutil
import sys

import pytest

from conftest import FIX
from rosettex.extract.mineru_adapter import adapt
from rosettex.extract.run import ExtractError, extract_core, flag_suspicious, plan_groups
from rosettex.workspace import Work, read_json, write_json

FAKE = [sys.executable, str(FIX.parent / "fake_mineru.py")]


def new_work(tmp_path, ppg):
    w = Work(tmp_path / "Bài báo có dấu" / "w")
    w.root.mkdir(parents=True)
    shutil.copyfile(FIX / "papers" / "short.pdf", w.src_pdf)
    write_json(w.manifest, {"lang": "vi", "pages": 2, "tier": "standard", "pages_per_group": ppg})
    return w


def test_plan_groups():
    assert plan_groups(12, 5) == [(1, 5), (6, 10), (11, 12)]
    assert plan_groups(2, 5) == [(1, 2)]


def test_extract_core_groups_and_outputs(tmp_path):
    w = new_work(tmp_path, ppg=1)
    beats = []
    s = extract_core(w, FAKE, lambda d, t: beats.append((d, t)))
    doc = read_json(w.document)
    assert {b["page"] for b in doc["blocks"]} == {1, 2}
    assert (w.root / doc["blocks"][[b["kind"] for b in doc["blocks"]].index("figure")]["image"]).is_file()
    assert s["batches"] == len(list(w.batches.glob("b*.json"))) and (2, 2) in beats
    assert read_json(w.flagged) == []


def test_retry_once_then_succeed(tmp_path, monkeypatch):
    w = new_work(tmp_path, ppg=5)
    monkeypatch.setenv("FAKE_MINERU_FAIL_ONCE", str(tmp_path / "failed-once"))
    extract_core(w, FAKE, lambda d, t: None)
    assert w.document.exists() and (w.mineru / "g001" / ".done").exists()


def test_two_failures_raise(tmp_path, monkeypatch):
    w = new_work(tmp_path, ppg=5)
    monkeypatch.setenv("FAKE_MINERU_FAIL_ALWAYS", "1")
    with pytest.raises(ExtractError):
        extract_core(w, FAKE, lambda d, t: None)
    assert not w.document.exists()


def test_resume_redoes_only_unfinished_group(tmp_path):  # Review Focus 5
    w = new_work(tmp_path, ppg=1)
    extract_core(w, FAKE, lambda d, t: None)
    done1 = w.mineru / "g001" / ".done"
    stamp = done1.stat().st_mtime_ns
    (w.mineru / "g002" / ".done").unlink()          # simulate being killed midway through group 2
    shutil.rmtree(w.mineru / "g002" / "x")
    w.document.unlink()
    extract_core(w, FAKE, lambda d, t: None)
    assert done1.stat().st_mtime_ns == stamp and (w.mineru / "g002" / ".done").exists() and w.document.exists()


def test_flag_dot_artifact_from_basic_tier():
    d = FIX / "mineru-4.2.3-basic"
    doc = adapt([(json.loads((d / "short.middle.json").read_text(encoding="utf-8")), d)], d)
    flags = flag_suspicious(doc, FIX / "papers" / "short.pdf")
    assert any(f["id"] == "p1b9" and r"\dot" in f["reason"] for f in flags)
    std = FIX / "mineru-4.2.3-standard"
    doc2 = adapt([(json.loads((std / "short.middle.json").read_text(encoding="utf-8")), std)], std)
    assert not any(r"\dot" in f["reason"] for f in flag_suspicious(doc2, FIX / "papers" / "short.pdf"))
```

- [ ] **Step 3: Run to see the failure**

Run: `uv run pytest tests/test_extract.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'rosettex.extract.run'`

- [ ] **Step 4: Write `rosettex/extract/run.py`**

```python
"""extract: run MinerU by page group (resumable), convert, batch, flag."""
from __future__ import annotations

import re
import shutil
import zipfile

from ..pdf import text_in_bbox
from ..workspace import Work, read_json, run_with_heartbeat, write_json
from .mineru_adapter import adapt
from .segment import segment

ACCENTS = {r"\dot": "˙\u0307", r"\ddot": "¨\u0308", r"\hat": "ˆ^\u0302", r"\widehat": "ˆ^\u0302",
           r"\tilde": "˜~\u0303", r"\widetilde": "˜~\u0303", r"\bar": "¯\u0304"}
TEXT_IN_MATH = re.compile(r"\$|\b(?:e\.g\.|i\.e\.)")


class ExtractError(Exception):
    pass


def plan_groups(npages: int, size: int) -> list[tuple[int, int]]:
    return [(a, min(a + size - 1, npages)) for a in range(1, npages + 1, size)]


def flag_suspicious(doc, pdf_path) -> list[dict]:
    flags = []
    for b in doc["blocks"]:
        maths = [b["tex"]] if b["kind"] == "equation" else [s["v"] for s in b.get("spans", []) if s["t"] == "math"]
        layer = None
        for tex in maths:
            if TEXT_IN_MATH.search(tex):
                flags.append({"id": b["id"], "page": b["page"], "reason": "text leaked into the formula", "tex": tex})
            for cmd, marks in ACCENTS.items():
                if re.search(re.escape(cmd) + r"(?![a-zA-Z])", tex):
                    if layer is None:
                        layer = text_in_bbox(pdf_path, b["page"], b["bbox"])
                    if layer.strip() and not any(c in layer for c in marks):
                        flags.append({"id": b["id"], "page": b["page"], "tex": tex,
                                      "reason": f"{cmd} has no matching accent mark in the PDF text layer"})
    return flags


def extract_core(work: Work, cmd: list[str], on_beat) -> dict:
    m = read_json(work.manifest)
    groups = plan_groups(m["pages"], m["pages_per_group"])
    for gi, (a, b) in enumerate(groups, 1):
        gdir = work.mineru / f"g{gi:03d}"
        if (gdir / ".done").exists():
            continue
        for _attempt in (1, 2):
            if gdir.exists():
                shutil.rmtree(gdir)
            gdir.mkdir(parents=True)
            rc = run_with_heartbeat(
                cmd + ["parse", "src.pdf", "-o", gdir.relative_to(work.root).as_posix(), "--format", "zip",
                       "--tier", m["tier"], "--pages", f"{a}-{b}"],
                cwd=work.root, log=work.logs / f"mineru-g{gi:03d}.log",
                on_beat=lambda: on_beat(gi - 1, len(groups)))
            zips = sorted(gdir.glob("*.zip"))
            if rc == 0 and len(zips) == 1:
                with zipfile.ZipFile(zips[0]) as z:
                    z.extractall(gdir / "x")
                (gdir / ".done").write_text("ok", encoding="utf-8")
                break
        else:
            raise ExtractError(f"MinerU failed twice on pages {a}-{b}; see {work.logs / f'mineru-g{gi:03d}.log'}")
        on_beat(gi, len(groups))
    data = [(read_json(work.mineru / f"g{gi:03d}" / "x" / "middle_json.json"), work.mineru / f"g{gi:03d}" / "x")
            for gi in range(1, len(groups) + 1)]
    doc, batches = segment(adapt(data, work.root))
    flags = flag_suspicious(doc, work.src_pdf)
    for bt in batches:
        write_json(work.batch_path(bt["batch"]), bt)
    write_json(work.flagged, flags)
    write_json(work.document, doc)  # written last: having document.json means extraction fully finished
    return {"blocks": len(doc["blocks"]), "units": len(doc["units"]), "batches": len(batches),
            "flagged": len(flags), "columns": doc["source"]["columns"]}
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_extract.py -v`
Expected: PASS (6 tests). If `test_flag_dot_artifact_from_basic_tier` fails in the second half (the `standard` version is also flagged), print the `text_in_bbox` of that block. The original has no dot, so the `standard` version must not be flagged.

- [ ] **Step 6: Commit**

```bash
git add rosettex/extract/run.py tests/fake_mineru.py tests/test_extract.py
git commit -m "feat: resumable page-group extract, flag suspicious formulas

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: `render/latex.py`: generate `main.tex` and `probe.tex`

**Files:**
- Create: `rosettex/render/__init__.py` (empty), `rosettex/render/latex.py`
- Test: `tests/test_latex.py`

**Interfaces:**
- Consumes:
  - `PH_RE` (Task 7).
  - `escape_latex` (Task 4).
  - `table_shape` (Task 4).
  - the `doc` structure with blocks and units (Task 5 and 6).
- Produces:
  - `preamble(doc, pack, twocolumn=None) -> str`.
  - `class Renderer(doc, tr, pack, fallback: set, images: dict)`, with the methods:
    - `.document() -> str` (main.tex).
    - `.probe() -> str`.
    - `.tabular(b) -> str`.
  - Keys in `fallback`:
    - a block id (`p1b10`), for a display formula, table, figure or text block;
    - `<unit id>:<placeholder>` (`p1b9:m1`), for an inline formula.
  - Every block has a `\typeout{RX:<id>}` line placed right before it.

- [ ] **Step 1: Write the failing test**

`tests/test_latex.py`:
```python
import json

from conftest import FIX, fake_vi
from rosettex.checks import load_pack
from rosettex.extract.mineru_adapter import adapt
from rosettex.extract.segment import segment
from rosettex.render.latex import Renderer

VI = load_pack("vi")


def doc_short():
    d = FIX / "mineru-4.2.3-standard"
    doc, _ = segment(adapt([(json.loads((d / "short.middle.json").read_text(encoding="utf-8")), d)], d))
    return doc


def tr_of(doc):
    return {u["id"]: fake_vi(u["src"]) for u in doc["units"]}


def test_placeholders_filled_and_front_matter():
    doc = doc_short()
    tex = Renderer(doc, tr_of(doc), VI, set(), {}).document()
    assert r"\documentclass[10pt,twocolumn]{article}" in tex and r"\twocolumn[{" in tex
    assert r"\setdefaultlanguage{vietnamese}" in tex and "texgyretermes" in tex
    assert r"\cite{ref4}" in tex and r"\eqref{eq:1}" in tex and r"\ref{tab:1}" in tex
    assert r"\begin{equation}" in tex and r"\tag{1}\label{eq:1}" in tex
    assert r"\rxlabel{2}{sec:2}" in tex and r"\bibitem{ref4}" in tex
    assert "Alice Nguyen" in tex  # authors kept verbatim
    assert "References" not in tex  # the bibliography heading is printed by thebibliography
    assert tex.index("RX:p1b15") < tex.index(r"\begin{table")


def test_escape_of_translated_text():  # Review Focus 3
    doc = doc_short()
    tr = tr_of(doc)
    tr["p1b7"] = "50% & a_b #1 {x} ~^\\ α ∗ ⟦c1⟧"
    tex = Renderer(doc, tr, VI, set(), {}).document()
    assert r"50\% \& a\_b \#1 \{x\} \textasciitilde{}\textasciicircum{}\textbackslash{} \ensuremath{α} \ensuremath{∗} \cite{ref4}" in tex


def test_table_spans():
    doc = doc_short()
    t = next(b for b in doc["blocks"] if b["id"] == "p1b17")
    tab = Renderer(doc, tr_of(doc), VI, set(), {}).tabular(t)
    assert r"\multirow{2}{*}" in tab and r"\multicolumn{2}{c}" in tab and "23.75" in tab
    assert tab.count(r" \\") == 6


def test_fallbacks():
    doc = doc_short()
    fb = {"p1b9:m1", "p1b10", "p1b17", "p1b18"}
    tex = Renderer(doc, tr_of(doc), VI, fb, {}).document()
    assert r"\fbox{\ttfamily\small x = (x\_\{1\},\textbackslash{}dots ,x\_\{n\})}" in tex
    assert "công thức/bảng chưa dựng được" in tex and r"\rxlabel{1}{eq:1}" in tex
    assert "thiếu ảnh" in tex


def test_figure_uses_copied_asset():
    doc = doc_short()
    tex = Renderer(doc, tr_of(doc), VI, set(), {"p1b18": "assets/p1b18.jpg"}).document()
    assert r"\includegraphics[width=\linewidth]{assets/p1b18.jpg}" in tex


def test_probe_isolates_every_formula():
    doc = doc_short()
    probe = Renderer(doc, tr_of(doc), VI, {"p1b9:m2"}, {}).probe()
    assert "RX:p1b9:m1" in probe and "RX:p1b9:m2" not in probe and "RX:p1b10" in probe and "RX:p1b17" in probe
    assert "twocolumn" not in probe
```

- [ ] **Step 2: Run to see the failure**

Run: `uv run pytest tests/test_latex.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write `rosettex/render/latex.py`**

```python
"""document.json + translation → main.tex (the build) and probe.tex (trial-compile each formula/table)."""
from __future__ import annotations

import re
from collections import defaultdict

from ..checks import PH_RE
from ..extract.tables import table_shape
from ..tex import escape_latex


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
    f = pack["font"]
    return "\n".join([
        r"\documentclass[10pt%s]{article}" % (",twocolumn" if two else ""),
        r"\usepackage[paperwidth=%sbp,paperheight=%sbp,left=%dbp,right=%dbp,top=%dbp,bottom=%dbp]{geometry}"
        % (w, h, left, right, top, bottom),
        r"\usepackage{fontspec}",
        r"\setmainfont{%s}[%s]" % (f["main"], f["options"]),
        r"\usepackage{amsmath,amssymb}",
        r"\usepackage{unicode-math}",
        r"\setmathfont{latinmodern-math.otf}",
        r"\usepackage{polyglossia}",
        r"\setdefaultlanguage{%s}" % pack["polyglossia"],
        r"\usepackage{graphicx,booktabs,multirow,adjustbox,url}",
        r"\usepackage[hidelinks]{hyperref}",
        r"\makeatletter\newcommand{\rxlabel}[2]{\phantomsection\def\@currentlabel{#1}\label{#2}}\makeatother",
        r"\setlength{\emergencystretch}{3em}",
        r"\begin{document}",
        "",
    ])


class Renderer:
    def __init__(self, doc, tr, pack, fallback, images):
        self.doc, self.tr, self.pack, self.fb, self.images = doc, tr, pack, set(fallback), images
        self.units = {u["id"]: u for u in doc["units"]}

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
                out.append("\\textsuperscript{" + escape_latex(s["v"]) + "}")
            else:
                out.append(escape_latex(s["v"]))
        return " ".join("".join(out).split())

    def text(self, uid, spans) -> str:
        u = self.units.get(uid)
        if u is None:
            return self.verbatim(spans, uid)
        t = self.tr.get(uid, u["src"])
        out, pos = [], 0
        for m in PH_RE.finditer(t):
            out.append(escape_latex(t[pos:m.start()]))
            name = m.group(0)[1:-1]
            p = u["ph"].get(name)
            if p is None:
                out.append(escape_latex(m.group(0)))
            elif f"{uid}:{name}" in self.fb:
                out.append(self.box_inline(p["src"]))
            else:
                out.append(p["tex"])
            pos = m.end()
        out.append(escape_latex(t[pos:]))
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
        return "{\\renewcommand{\\thefootnote}{}\\footnotetext{%s%s}}" % (mark, t)

    def bibliography(self, refs) -> str:
        items = "\n".join(self.mark(b) + "\\bibitem{ref%s} %s" % (b["key"], self.verbatim(b["spans"], b["id"]))
                          for b in refs)
        return "\\begin{thebibliography}{99}\n%s\n\\end{thebibliography}" % items

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
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_latex.py -v`
Expected: PASS (6 tests). `test_table_spans` counts 6 rows (the 6 rows of the fixture table). If wrong, print `tab` and compare with `cells`.

- [ ] **Step 5: Commit**

```bash
git add rosettex/render tests/test_latex.py
git commit -m "feat: generate main.tex/probe.tex: placeholders, multirow tables, floats near references, last-resort fallback

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: Compilation, last-resort fallback, post-build checks, `render_core`

**Files:**
- Create: `rosettex/render/compile.py`, `rosettex/render/postcheck.py`, `scripts/get_tectonic.py`
- Test: `tests/test_render.py`

**Interfaces:**
- Consumes:
  - `Renderer` (Task 10).
  - `find_tex` (Task 2).
  - `translations`, `manifest` (Task 8).
  - `load_pack`, `nfc` (Task 7).
  - `full_text`, `line_height_total` (Task 3).
  - `Work`, `read_json`, `write_json`, `run_with_heartbeat` (Task 1).
- Produces:
  - `class RenderError(Exception)`.
  - `run_tex(tool, workdir, name, on_beat=None) -> (ok, output, log)`.
  - `culprit(log) -> str | None`.
  - `expand_fallback(doc, key, fb) -> int`.
  - `render_core(work, out, *, overwrite=False, on_beat=lambda s: None) -> dict report`. This function writes `report.json` and `manifest["fallback"]`.
  - `postcheck(work, pdf, log, tex, doc, tr, pack, fallback) -> {"ok", "problems", "length_ratio"}`.

- [ ] **Step 1: Write `scripts/get_tectonic.py` and install Tectonic on the dev machine**

```python
"""Download prebuilt Tectonic 0.17.0, verify SHA-256, place it in ~/.rosettex/bin (or the directory given as argument 1)."""
import hashlib
import io
import os
import platform
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

VER = "0.17.0"
ASSETS = {
    ("Linux", "x86_64"): ("x86_64-unknown-linux-musl.tar.gz",
                          "8533d07f9ccbd7a65824b9e0459041bca34af1eb33daba48f59215593753a3b7"),
    ("Darwin", "x86_64"): ("x86_64-apple-darwin.tar.gz",
                           "7c90ef5b6ddb1eb1937e4337add5237b79338e4b9676459fa91187d24d6cdf80"),
    ("Darwin", "arm64"): ("aarch64-apple-darwin.tar.gz",
                          "a3f1cac7c5678f01661a92212f58480ae3b0634115d880dbc59e2953ded45667"),
    ("Windows", "AMD64"): ("x86_64-pc-windows-msvc.zip",
                           "f61ce51f0b0ade1015b7de7ef368541c5424e9756ecbd0d7af97d6d48030845f"),
}


def main() -> int:
    home = Path(os.environ.get("ROSETTEX_HOME") or Path.home() / ".rosettex")
    dest = Path(sys.argv[1]) if len(sys.argv) > 1 else home / "bin"
    key = (platform.system(), platform.machine())
    if key not in ASSETS:
        print(f"no prebuilt Tectonic for {key}; install TeX Live/MiKTeX (xelatex)", file=sys.stderr)
        return 2
    name, sha = ASSETS[key]
    url = f"https://github.com/tectonic-typesetting/tectonic/releases/download/tectonic%40{VER}/tectonic-{VER}-{name}"
    data = urllib.request.urlopen(url, timeout=60).read()
    if hashlib.sha256(data).hexdigest() != sha:
        print("checksum mismatch, not installing", file=sys.stderr)
        return 1
    dest.mkdir(parents=True, exist_ok=True)
    exe = "tectonic.exe" if name.endswith(".zip") else "tectonic"
    if name.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            (dest / exe).write_bytes(z.read(exe))
    else:
        with tarfile.open(fileobj=io.BytesIO(data)) as t:
            (dest / exe).write_bytes(t.extractfile(exe).read())
    (dest / exe).chmod(0o755)
    print(dest / exe)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

Run: `uv run python scripts/get_tectonic.py`
Expected: in ra `/home/admin/.rosettex/bin/tectonic`

- [ ] **Step 2: Write the failing test**

`tests/test_render.py`:
```python
import pytest

from conftest import make_work, translate_all
from rosettex.checks import nfc
from rosettex.pdf import full_text
from rosettex.render.compile import culprit, render_core
from rosettex.tools import find_tex
from rosettex.workspace import read_json, write_json

needs_tex = pytest.mark.skipif(find_tex() is None, reason="needs Tectonic or XeLaTeX (scripts/get_tectonic.py)")


def test_culprit():
    assert culprit("RX:a\nRX:b\n! Undefined control sequence.\nRX:c") == "b"
    assert culprit("! LaTeX Error: x\nRX:a") is None


@needs_tex
def test_render_short_end_to_end(tmp_path):
    w = make_work(tmp_path)
    translate_all(w)
    out = tmp_path / "Kết quả" / "bài báo.vi.pdf"
    rep = render_core(w, out)
    assert out.is_file() and rep["postcheck"]["ok"], rep["postcheck"]
    assert rep["fallback"] == [] and rep["unreviewed"] is True
    text = nfc(full_text(out))
    assert "việc" in text and "Alice Nguyen" in text and "Tài liệu tham khảo" in text
    assert read_json(w.report)["output"] == str(out)


@needs_tex
def test_broken_formulas_fall_back_without_breaking_build(tmp_path):
    w = make_work(tmp_path)
    doc = read_json(w.document)
    eq = next(b for b in doc["blocks"] if b["kind"] == "equation")
    eq["tex"] = r"\frac{a"
    u = next(u for u in doc["units"] if any(n.startswith("m") for n in u["ph"]))
    name = next(n for n in u["ph"] if n.startswith("m") and u["ph"][n]["tex"].startswith("$"))
    u["ph"][name]["tex"] = r"$\nosuchcommand x$"
    write_json(w.document, doc)
    translate_all(w)
    tr = read_json(w.translation_path("b001", "vi"))
    first = next(iter(tr))
    tr[first] = tr[first] + " 50% & _ # ~ ^ α ∗"  # Review Focus 3: special characters in the translation
    write_json(w.translation_path("b001", "vi"), tr)
    rep = render_core(w, tmp_path / "out.pdf")
    ids = {f["id"] for f in rep["fallback"]}
    assert eq["id"] in ids and f"{u['id']}:{name}" in ids
    assert (tmp_path / "out.pdf").is_file() and rep["postcheck"]["ok"], rep["postcheck"]
```

- [ ] **Step 3: Run to see the failure**

Run: `uv run pytest tests/test_render.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'rosettex.render.compile'`

- [ ] **Step 4: Write `rosettex/render/postcheck.py`**

```python
"""Post-build checks (SG 11.3): log, characters, references, object counts, length ratio."""
from __future__ import annotations

import re

from ..checks import nfc
from ..pdf import full_text, line_height_total

MATH_CHARS = re.compile("[\U0001D400-\U0001D7FF]")


def postcheck(work, pdf, log, tex, doc, tr, pack, fallback) -> dict:
    problems = []
    if re.search(r"^! ", log, re.M):
        problems.append("LaTeX log has errors")
    if "Missing character" in log:
        miss = sorted(set(re.findall(r"Missing character: There is no (.)", log)))
        problems.append("font is missing characters: " + "".join(miss))
    if re.search(r"LaTeX Warning: (?:Reference|Citation) `[^']*' .*undefined", log):
        problems.append("there are undefined references/citations")
    text = nfc(full_text(pdf))
    if "??" in text or "[?]" in text:
        problems.append("PDF contains '??' or '[?]'")
    has_math = any(b["kind"] == "equation" for b in doc["blocks"]) or any(
        p["tex"].startswith("$") for u in doc["units"] for p in u["ph"].values())
    if has_math and not MATH_CHARS.search(text):
        problems.append("no math characters found in the PDF text layer")
    missing = (set(nfc("".join(tr.values()))) & set(pack.get("check_chars", ""))) - set(text)
    if missing:
        problems.append("PDF is missing characters: " + "".join(sorted(missing)))
    expected_links = sum(1 for u in doc["units"] for n in u["ph"] if n[0] in "cr")
    links = len(re.findall(r"\\(?:ref|eqref|cite)\{", tex))
    if links != expected_links:
        problems.append(f"internal link count {links} ≠ {expected_links}")
    counts = doc["source"].get("counts", {})
    fb_tags = sum(1 for b in doc["blocks"] if b["kind"] == "equation" and b.get("tag") and b["id"] in fallback)
    have = {"tables": len(re.findall(r"\\begin\{table\*?\}", tex)),
            "figures": len(re.findall(r"\\begin\{figure\*?\}", tex)),
            "tagged_equations": tex.count("\\tag{") + fb_tags,
            "references": tex.count("\\bibitem{")}
    for k, v in counts.items():
        if have.get(k) != v:
            problems.append(f"{k} count in the build {have.get(k)} ≠ original {v}")
    ratio = line_height_total(pdf) / max(line_height_total(work.src_pdf), 1e-9)
    if ratio > 1.3:
        problems.append(f"content length ratio {ratio:.2f} > 1.3")
    return {"ok": not problems, "problems": problems, "length_ratio": round(ratio, 3)}
```

- [ ] **Step 5: Write `rosettex/render/compile.py`**

```python
"""render: trial-compile each item, last-resort fallback, compile the whole paper, post-build checks, report.json."""
from __future__ import annotations

import re
import shutil
from pathlib import Path

from ..checks import load_pack
from ..tools import find_tex
from ..translate import manifest, translations
from ..workspace import Work, read_json, run_with_heartbeat, write_json
from .latex import Renderer
from .postcheck import postcheck


class RenderError(Exception):
    pass


def run_tex(tool, workdir: Path, name: str, on_beat=None) -> tuple[bool, str, str]:
    kind, cmd = tool
    stem = Path(name).stem
    logf, runf = Path(workdir) / f"{stem}.log", Path(workdir) / f"{stem}.run.txt"
    runs = [cmd + ["-X", "compile", "--keep-logs", name]] if kind == "tectonic" else \
        [cmd + ["-interaction=nonstopmode", "-halt-on-error", name]] * 3
    for argv in runs:
        logf.unlink(missing_ok=True)
        runf.unlink(missing_ok=True)
        rc = run_with_heartbeat(argv, cwd=workdir, log=runf, on_beat=on_beat or (lambda: None))
        out = runf.read_text(encoding="utf-8", errors="replace") if runf.exists() else ""
        log = logf.read_text(encoding="utf-8", errors="replace") if logf.exists() else ""
        if rc != 0:
            return False, out, log
        if kind != "tectonic" and "Rerun" not in log:
            break
    return True, out, log


def culprit(log: str) -> str | None:
    i = log.find("\n! ")
    head = log if i < 0 else log[:i]
    if i < 0 and log.startswith("! "):
        head = ""
    ids = re.findall(r"RX:(\S+)", head)
    return ids[-1] if ids else None


def expand_fallback(doc, key: str, fb: set) -> int:
    before = len(fb)
    if ":" in key:
        fb.add(key)
        return len(fb) - before
    if not any(b["id"] == key for b in doc["blocks"]):
        return 0
    fb.add(key)
    for u in doc["units"]:
        if u["block"] == key:
            fb.update(f"{u['id']}:{n}" for n, p in u["ph"].items() if p["tex"].startswith("$"))
    return len(fb) - before


def _tail(s: str, n: int = 1500) -> str:
    return s[-n:]


def _page_of(key: str) -> int:
    m = re.match(r"p(\d+)", key)
    return int(m.group(1)) if m else 0


def copy_assets(work: Work, doc) -> dict:
    images = {}
    (work.out / "assets").mkdir(parents=True, exist_ok=True)
    for b in doc["blocks"]:
        if b["kind"] == "figure" and b.get("image"):
            src = work.root / b["image"]
            if src.is_file():
                dst = work.out / "assets" / f"{b['id']}{src.suffix.lower()}"
                shutil.copyfile(src, dst)
                images[b["id"]] = f"assets/{dst.name}"
    return images


def render_core(work: Work, out, *, overwrite: bool = False, on_beat=lambda step: None) -> dict:
    m = manifest(work)
    lang = m["lang"]
    pack = load_pack(lang)
    tool = find_tex()
    if tool is None:
        raise RenderError("Tectonic or XeLaTeX not found")
    out = Path(out)
    if out.exists() and not overwrite:
        raise RenderError(f"output file already exists: {out}")
    doc, tr = read_json(work.document), translations(work, lang)
    work.out.mkdir(parents=True, exist_ok=True)
    images = copy_assets(work, doc)
    fb = set(m.get("fallback", []))
    beat = lambda: on_beat("compiling")  # noqa: E731

    on_beat("probe")
    limit = len(doc["blocks"]) + sum(len(u["ph"]) for u in doc["units"]) + 1
    for _ in range(limit):
        (work.out / "probe.tex").write_text(Renderer(doc, tr, pack, fb, images).probe(), encoding="utf-8")
        ok, output, log = run_tex(tool, work.out, "probe.tex", beat)
        if ok:
            break
        c = culprit(log)
        if c is None or c in fb:
            raise RenderError("trial compile failed and the item could not be identified: " + _tail(output + log))
        fb.add(c)

    on_beat("main")
    for _ in range(20):
        tex = Renderer(doc, tr, pack, fb, images).document()
        (work.out / "main.tex").write_text(tex, encoding="utf-8")
        ok, output, log = run_tex(tool, work.out, "main.tex", beat)
        if ok:
            break
        c = culprit(log)
        if not c or not expand_fallback(doc, c, fb):
            raise RenderError("compiling main.tex failed: " + _tail(output + log))
    else:
        raise RenderError("more than 20 compile-fix rounds for main.tex")

    on_beat("postcheck")
    pdf = work.out / "main.pdf"
    pc = postcheck(work, pdf, log, tex, doc, tr, pack, fb)
    out.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(pdf, out)
    terms = read_json(work.terms, {})
    report = {"output": str(out), "lang": lang, "lang_tested": pack["tested"], "topic": terms.get("topic"),
              "key_terms": terms.get("terms", [])[:10], "tier": m.get("tier"), "tier_reason": m.get("tier_reason"),
              "device": "chosen by MinerU (slice 1 does not force a device)", "tex": tool[0],
              "fallback": [{"id": k, "page": _page_of(k)} for k in sorted(fb)],
              "flagged": read_json(work.flagged, []), "unreviewed": True, "postcheck": pc}
    write_json(work.report, report)
    m["fallback"] = sorted(fb)
    write_json(work.manifest, m)
    return report
```

- [ ] **Step 6: Run the tests**

Run: `uv run pytest tests/test_render.py -v`
Expected: PASS (3 tests; the 2 tests that need TeX are skipped if the machine has no Tectonic). On the first run Tectonic needs the network to download its bundle. On a `dns error`/`Connect` error, rerun (SG 4.1: its Rust client is flaky on a broken IPv6 network).

If `postcheck` reports `font is missing characters`, read the printed characters and add their Unicode range to `_mathy` in `rosettex/tex.py`, only if those characters are math symbols. If they are letters this is a font problem, which belongs to slice 3: record it in the report, do not loosen the check.

- [ ] **Step 7: Commit**

```bash
git add rosettex/render scripts/get_tectonic.py tests/test_render.py
git commit -m "feat: render: trial-compile each item, last-resort fallback, post-build checks, report.json

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 12: `cli.py`: commands, `status`/`next_action`, background runs, end-to-end

**Files:**
- Create: `rosettex/cli.py`, `rosettex/__main__.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: all the modules above.
- Produces:
  - `main(argv=None) -> int`.
  - `status(work) -> dict` with `state ∈ {missing, extract_running, extract_dead, extract_failed, render_running, render_dead, render_failed, not_extracted, need_terms, translating, need_validate, need_render, done}` and `next_action`.
  - Commands: `guide, extract, status, terms, next, accept, validate, render, clean`, and the `--version` flag.

- [ ] **Step 1: Write the failing test**

`tests/test_cli.py`:
```python
import json
import os
import shutil
import sys
import time

import pytest

from conftest import FIX, TERMS, fake_vi
from rosettex.cli import main
from rosettex.tools import find_tex
from rosettex.workspace import Work, beat, read_json

FAKE = str(FIX.parent / "fake_mineru.py")
needs_tex = pytest.mark.skipif(find_tex() is None, reason="needs Tectonic or XeLaTeX")


def run(capsys, *argv):
    code = main([str(a) for a in argv])
    out = capsys.readouterr().out
    return code, (json.loads(out) if out.lstrip().startswith("{") else out)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("ROSETTEX_MINERU", FAKE)
    monkeypatch.setenv("ROSETTEX_HOME", str(tmp_path / "home"))
    src = tmp_path / "Bài báo" / "short paper.pdf"
    src.parent.mkdir()
    shutil.copyfile(FIX / "papers" / "short.pdf", src)
    return src, tmp_path / "Kết quả mới"


def translate_via_cli(capsys, work, tmp_path):
    (tmp_path / "terms.json").write_text(json.dumps(TERMS, ensure_ascii=False), encoding="utf-8")
    assert run(capsys, "terms", work, "--file", tmp_path / "terms.json", "--json")[0] == 0
    while True:
        code, pkg = run(capsys, "next", work, "--json")
        assert code == 0
        if pkg["done"]:
            return
        f = tmp_path / f"{pkg['batch']['batch']}.json"
        f.write_text(json.dumps({it["id"]: fake_vi(it["text"]) for it in pkg["batch"]["items"]},
                                ensure_ascii=False), encoding="utf-8")
        code, res = run(capsys, "accept", work, "--batch", pkg["batch"]["batch"], "--file", f, "--json")
        assert code == 0, res


def test_full_flow_foreground(env, capsys, tmp_path):  # Review Focus 1 and 4
    src, outdir = env
    code, r = run(capsys, "extract", src, "--lang", "vi", "--out", outdir, "--foreground", "--json")
    assert code == 0 and r["state"] == "extracted" and r["output"].endswith("short paper.vi.pdf")
    assert r["output_exists"] is False and r["lang_tested"] is True
    work = r["work"]
    assert run(capsys, "status", work, "--json")[1]["state"] == "need_terms"
    code, o = run(capsys, "next", work, "--outline", "--json")
    assert o["title"].startswith("Attention-Based")
    translate_via_cli(capsys, work, tmp_path)
    assert run(capsys, "status", work, "--json")[1]["state"] == "need_validate"
    code, v = run(capsys, "validate", work, "--json")
    assert code == 0 and v["ok"] is True
    st = run(capsys, "status", work, "--json")[1]
    assert st["state"] == "need_render" and "rosettex render" in st["next_action"]
    if find_tex() is None:
        return
    code, rep = run(capsys, "render", work, "--foreground", "--json")
    assert code == 0, rep
    assert (outdir / "short paper.vi.pdf").is_file()
    assert run(capsys, "status", work, "--json")[1]["state"] == "done"
    code, again = run(capsys, "render", work, "--foreground", "--json")
    assert code == 2 and again["output_exists"] is True
    code, _ = run(capsys, "render", work, "--foreground", "--overwrite", "--json")
    assert code == 0


def test_extract_in_background(env, capsys):
    src, outdir = env
    code, r = run(capsys, "extract", src, "--lang", "vi", "--out", outdir / "x.pdf", "--json")
    assert code == 0 and r["state"] == "started" and r["next_action"].startswith("rosettex status")
    deadline = time.time() + 90
    while time.time() < deadline:
        st = run(capsys, "status", r["work"], "--json")[1]
        if st["state"] not in ("extract_running", "not_extracted"):
            break
        time.sleep(1)
    assert st["state"] == "need_terms", st


def test_status_reports_dead_job(env, capsys):
    src, outdir = env
    code, r = run(capsys, "extract", src, "--lang", "vi", "--out", outdir, "--foreground", "--json")
    w = Work(r["work"])
    w.document.unlink()
    beat(w, "extract", "rosettex extract RESUME", "group 1/1", 0, 1)
    p = read_json(w.progress)
    p["updated"] -= 3600
    w.progress.write_text(json.dumps(p), encoding="utf-8")
    st = run(capsys, "status", w.root, "--json")[1]
    assert st["state"] == "extract_dead" and st["next_action"] == "rosettex extract RESUME"


def test_errors_and_exit_codes(env, capsys, tmp_path, monkeypatch):
    src, outdir = env
    code, r = run(capsys, "extract", tmp_path / "không có.pdf", "--lang", "vi", "--out", outdir, "--json")
    assert code == 2 and "file not found" in r["error"]
    monkeypatch.setenv("ROSETTEX_MINERU", str(tmp_path / "missing.py"))
    monkeypatch.setenv("PATH", str(tmp_path))
    code, r = run(capsys, "extract", src, "--lang", "vi", "--out", outdir, "--json")
    assert code == 2 and "MinerU" in r["error"]
    code, r = run(capsys, "clean", tmp_path, "--json")
    assert code == 2 and tmp_path.exists()


def test_accept_rejection_exit_1(env, capsys, tmp_path):
    src, outdir = env
    work = run(capsys, "extract", src, "--lang", "vi", "--out", outdir, "--foreground", "--json")[1]["work"]
    (tmp_path / "terms.json").write_text(json.dumps(TERMS), encoding="utf-8")
    run(capsys, "terms", work, "--file", tmp_path / "terms.json", "--json")
    pkg = run(capsys, "next", work, "--json")[1]
    f = tmp_path / "bad.json"
    f.write_text(json.dumps({it["id"]: it["text"] for it in pkg["batch"]["items"]}), encoding="utf-8")
    code, res = run(capsys, "accept", work, "--batch", "b001", "--file", f, "--json")
    assert code == 1 and res["ok"] is False and "rosettex accept" in res["next_action"]


def test_guide_and_version(capsys):
    code, text = run(capsys, "guide")
    assert code == 0 and "rosettex extract" in text
    with pytest.raises(SystemExit):
        main(["--version"])


@pytest.mark.skipif(not os.environ.get("ROSETTEX_REAL"), reason="set ROSETTEX_REAL=1 to run with the real MinerU")
@pytest.mark.parametrize("name", ["short", "long"])
def test_real_mineru_end_to_end(name, tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("ROSETTEX_MINERU", raising=False)
    code, r = run(capsys, "extract", FIX / "papers" / f"{name}.pdf", "--lang", "vi", "--out", tmp_path / "out",
                  "--foreground", "--json")
    assert code == 0, r
    translate_via_cli(capsys, r["work"], tmp_path)
    assert run(capsys, "validate", r["work"], "--json")[0] == 0
    if find_tex():
        code, rep = run(capsys, "render", r["work"], "--foreground", "--json")
        assert code == 0, rep["postcheck"]
```

- [ ] **Step 2: Run to see the failure**

Run: `uv run pytest tests/test_cli.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'rosettex.cli'`

- [ ] **Step 3: Write `rosettex/__main__.py`**

```python
from .cli import main

raise SystemExit(main())
```

- [ ] **Step 4: Write `rosettex/cli.py`**

```python
"""rosettex command line. Every command has --json; the agent always follows `next_action`."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path

from . import __version__, translate
from .checks import load_pack
from .extract.run import ExtractError, extract_core
from .pdf import page_count
from .render.compile import RenderError, render_core
from .tools import available_ram_bytes, choose_tier, find_mineru, find_tex, verify_models
from .workspace import UsageError, Work, beat, job_state, q, read_json, spawn_detached, write_json


def emit(obj, as_json: bool) -> None:
    if as_json:
        print(json.dumps(obj, ensure_ascii=False, indent=1))
        return
    for k, v in obj.items():
        print(f"{k}: {v if isinstance(v, (str, int, float, bool)) or v is None else json.dumps(v, ensure_ascii=False)}")


def resolve_out(pdf: Path, lang: str, out: str) -> Path:
    p = Path(out).expanduser()
    return (p if p.suffix.lower() == ".pdf" else p / f"{pdf.stem}.{lang}.pdf").resolve()


def _running(job) -> bool:
    return bool(job) and job["state"] == "running" and not job["dead"]


# ---------------- extract ----------------
def cmd_extract(a):
    if not re.fullmatch(r"[a-z]{2,3}(?:-[A-Za-z]{2,8})?", a.lang):
        raise UsageError(f"invalid language code: {a.lang} (for example: vi, en, fr)")
    pdf = Path(a.pdf).expanduser().resolve()
    if not pdf.is_file():
        raise UsageError(f"file not found: {pdf}")
    out = resolve_out(pdf, a.lang, a.out)
    work = Work(a.work or out.parent / ".rosettex" / pdf.stem)
    digest = hashlib.sha256(pdf.read_bytes()).hexdigest()
    m = read_json(work.manifest)
    if m and m["sha256"] != digest:
        raise UsageError(f"work directory {work.root} belongs to a different paper",
                         next_action=f"rosettex clean {q(work.root)} --json")
    cmd = find_mineru()
    if cmd is None:
        raise UsageError("MinerU 4.x (mineru-kit) is not installed; see the README Installation section")
    resume = (f"rosettex extract {q(pdf)} --lang {a.lang} --out {q(out)} -o {q(work.root)} --json"
              + (f" --tier {a.tier}" if a.tier else ""))
    if m is None:
        tier, reason = choose_tier(a.tier, available_ram_bytes())
        ok, log = verify_models(cmd, tier)
        if not ok:
        raise UsageError(f"the MinerU models for tier {tier} are incomplete",
                             next_action=f"mineru-kit models download --tier {tier}", detail=log[-2000:])
        work.root.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(pdf, work.src_pdf)
        m = {"lang": a.lang, "out": str(out), "source": str(pdf), "sha256": digest,
             "pages": page_count(work.src_pdf), "tier": tier, "tier_reason": reason,
             "pages_per_group": a.pages_per_group, "extract_cmd": resume}
        write_json(work.manifest, m)
    elif a.tier and a.tier != m["tier"]:  # rerun with --tier basic after a failure: the remaining groups use the new tier
        m["tier"], m["tier_reason"] = a.tier, "requested"
        write_json(work.manifest, m)
    info = {"work": str(work.root), "output": str(out), "output_exists": out.exists(), "pages": m["pages"],
            "tier": m["tier"], "lang_tested": load_pack(a.lang)["tested"]}
    if work.document.exists():
        return 0, {"ok": True, "state": "extracted", **info, "next_action": f"rosettex status {q(work.root)} --json"}
    if not a.foreground:
        if _running(job_state(work)):
            return 0, {"ok": True, "state": "already_running", **info,
                       "next_action": f"rosettex status {q(work.root)} --json", "wait_seconds": 30}
        argv = [sys.executable, "-m", "rosettex", "extract", str(pdf), "--lang", a.lang, "--out", str(out),
                "-o", str(work.root), "--foreground", "--json"] + (["--tier", a.tier] if a.tier else [])
        beat(work, "extract", resume, "starting", 0, 0)
        pid = spawn_detached(argv, work.logs / "extract.log")
        return 0, {"ok": True, "state": "started", "pid": pid, **info,
                   "next_action": f"rosettex status {q(work.root)} --json", "wait_seconds": 30}

    def on(done, total):
        beat(work, "extract", resume, f"MinerU: {done}/{total} page groups done", done, total)

    on(0, 0)
    try:
        summary = extract_core(work, cmd, on)
    except ExtractError as e:
        beat(work, "extract", resume + " --tier basic", "error", 0, 0, state="failed", error=str(e))
        return 1, {"ok": False, "error": str(e), "next_action": resume + " --tier basic"}
    beat(work, "extract", resume, "xong", 1, 1, state="done")
    return 0, {"ok": True, "state": "extracted", **info, **summary,
               "next_action": f"rosettex next {q(work.root)} --outline --json"}


# ---------------- status ----------------
def status(work: Work) -> dict:
    m = read_json(work.manifest)
    if m is None:
        return {"state": "missing", "message": "no work directory yet; run rosettex extract", "next_action": None}
    r = q(work.root)
    job = job_state(work)
    if job and job["state"] == "running":
        if not job["dead"]:
            return {"state": job["command"] + "_running", "step": job["step"], "done": job["done"],
                    "total": job["total"], "next_action": f"rosettex status {r} --json", "wait_seconds": 30}
        return {"state": job["command"] + "_dead", "message": "the background task stopped unexpectedly; rerun to continue",
                "next_action": job["resume"]}
    if job and job["state"] == "failed":
        return {"state": job["command"] + "_failed", "error": job["error"], "next_action": job["resume"]}
    if not work.document.exists():
        return {"state": "not_extracted", "next_action": m["extract_cmd"]}
    if not work.terms.exists():
        return {"state": "need_terms", "next_action": f"rosettex next {r} --outline --json",
                "then": f"rosettex terms {r} --file <terms.json> --json"}
    ids = translate.batch_ids(work)
    done = [b for b in ids if work.translation_path(b, m["lang"]).exists()]
    if len(done) < len(ids):
        return {"state": "translating", "done": len(done), "total": len(ids),
                "next_action": f"rosettex next {r} --json"}
    v = read_json(work.validate)
    if not v or not v.get("ok") or v.get("mtime") != translate._translations_mtime(work, m["lang"]):
        return {"state": "need_validate", "next_action": f"rosettex validate {r} --json"}
    rep = read_json(work.report)
    if not rep or not Path(rep["output"]).exists():
        return {"state": "need_render", "next_action": f"rosettex render {r} --json"}
    return {"state": "done", "output": rep["output"], "report": str(work.report),
            "postcheck_ok": rep["postcheck"]["ok"], "next_action": None}


def cmd_status(a):
    return 0, status(Work(a.work))


# ---------------- translation ----------------
def cmd_terms(a):
    res = translate.write_terms(Work(a.work), a.file)
    return (0 if res["ok"] else 1), res


def cmd_next(a):
    w = Work(a.work)
    return 0, (translate.outline(w) if a.outline else translate.next_package(w, a.batch))


def cmd_accept(a):
    res = translate.accept(Work(a.work), a.batch, a.file)
    return (0 if res["ok"] else 1), res


def cmd_validate(a):
    w = Work(a.work)
    res = translate.validate(w)
    write_json(w.validate, {"ok": res["ok"], "mtime": res["mtime"]})
    if res["ok"]:
        res["next_action"] = f"rosettex render {q(w.root)} --json"
    return (0 if res["ok"] else 1), res


# ---------------- render ----------------
def cmd_render(a):
    w = Work(a.work)
    m = translate.manifest(w)
    out = Path(a.out).expanduser().resolve() if a.out else Path(m["out"])
    v = translate.validate(w)
    if not v["ok"]:
        return 1, {"ok": False, "error": "validate is not clean", **v, "next_action": f"rosettex validate {q(w.root)} --json"}
    if out.exists() and not a.overwrite:
        raise UsageError(f"output file already exists: {out}", next_action="ask the user; if they agree, rerun with --overwrite",
                         output_exists=True)
    if find_tex() is None:
        raise UsageError("Tectonic or XeLaTeX not found; see the README Installation section")
    resume = f"rosettex render {q(w.root)} --out {q(out)} --json" + (" --overwrite" if a.overwrite else "")
    if not a.foreground:
        if _running(job_state(w)):
            return 0, {"ok": True, "state": "already_running", "next_action": f"rosettex status {q(w.root)} --json"}
        argv = [sys.executable, "-m", "rosettex", "render", str(w.root), "--out", str(out), "--foreground", "--json"]
        beat(w, "render", resume, "starting", 0, 0)
        pid = spawn_detached(argv + (["--overwrite"] if a.overwrite else []), w.logs / "render.log")
        return 0, {"ok": True, "state": "started", "pid": pid, "next_action": f"rosettex status {q(w.root)} --json",
                   "wait_seconds": 30}
    beat(w, "render", resume, "started", 0, 0)
    try:
        rep = render_core(w, out, overwrite=a.overwrite, on_beat=lambda s: beat(w, "render", resume, s, 0, 0))
    except RenderError as e:
        beat(w, "render", resume, "error", 0, 0, state="failed", error=str(e))
        return 2, {"ok": False, "error": str(e), "next_action": None}
    beat(w, "render", resume, "xong", 1, 1, state="done")
    return (0 if rep["postcheck"]["ok"] else 1), {"ok": rep["postcheck"]["ok"], **rep, "next_action": None}


# ---------------- other ----------------
def cmd_clean(a):
    w = Work(a.work)
    if not w.manifest.exists():
        raise UsageError(f"not a rosettex work directory (manifest.json missing): {w.root}")
    shutil.rmtree(w.root)
    return 0, {"ok": True, "removed": str(w.root)}


def cmd_guide(a):
    from .protocol import text
    t = text(a.lang)
    if a.json:
        return 0, {"guide": t}
    print(t)
    return 0, None


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="in JSON cho agent")
    p = argparse.ArgumentParser(prog="rosettex", description="Extract, check and rebuild PDF papers for an agent to translate.")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("guide", parents=[common], help="print the protocol for the agent")
    s.add_argument("--lang", default="vi")
    s.set_defaults(func=cmd_guide)
    s = sub.add_parser("extract", parents=[common], help="extract the PDF content (runs in the background)")
    s.add_argument("pdf")
    s.add_argument("--lang", required=True)
    s.add_argument("--out", required=True, help="output .pdf file or directory")
    s.add_argument("-o", "--work", help="work directory (default <output directory>/.rosettex/<name>.<language code>)")
    s.add_argument("--tier", choices=["standard", "basic"])
    s.add_argument("--pages-per-group", type=int, default=5)
    s.add_argument("--foreground", action="store_true")
    s.set_defaults(func=cmd_extract)
    for name, func in (("status", cmd_status), ("validate", cmd_validate), ("clean", cmd_clean)):
        s = sub.add_parser(name, parents=[common])
        s.add_argument("work")
        s.set_defaults(func=func)
    s = sub.add_parser("terms", parents=[common])
    s.add_argument("work")
    s.add_argument("--file", required=True)
    s.set_defaults(func=cmd_terms)
    s = sub.add_parser("next", parents=[common])
    s.add_argument("work")
    s.add_argument("--batch")
    s.add_argument("--outline", action="store_true")
    s.set_defaults(func=cmd_next)
    s = sub.add_parser("accept", parents=[common])
    s.add_argument("work")
    s.add_argument("--batch", required=True)
    s.add_argument("--file", required=True)
    s.set_defaults(func=cmd_accept)
    s = sub.add_parser("render", parents=[common])
    s.add_argument("work")
    s.add_argument("--out")
    s.add_argument("--overwrite", action="store_true")
    s.add_argument("--foreground", action="store_true")
    s.set_defaults(func=cmd_render)
    return p


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass
    a = build_parser().parse_args(argv)
    try:
        code, obj = a.func(a)
    except UsageError as e:
        code, obj = 2, {"ok": False, "error": str(e), "next_action": e.next_action, **e.extra}
    if obj is not None:
        emit(obj, a.json)
    return code
```

`cmd_guide` needs `rosettex.protocol` (Task 13). To let Task 12's tests run, create a temporary `rosettex/protocol/__init__.py`:
```python
def text(lang: str = "vi") -> str:
    return "rosettex extract"
```
Task 13 will replace this file.

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_cli.py -v`
Expected: PASS. The two `test_real_mineru_end_to_end` tests are skipped. The render steps in `test_full_flow_foreground` only run when TeX is present.

- [ ] **Step 6: Run the whole test suite**

Run: `uv run pytest -q`
Expected: everything PASS (with conditional skips)

- [ ] **Step 7: Commit**

```bash
git add rosettex/cli.py rosettex/__main__.py rosettex/protocol/__init__.py tests/test_cli.py
git commit -m "feat: rosettex CLI with status/next_action and resumable background tasks

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 13: Single-source protocol, `guide`, `SKILL.md`/`AGENTS.md`, README

**Files:**
- Create: `rosettex/protocol/protocol.md`, `rosettex/protocol/build.py`, `skill/SKILL.md`, `skill/AGENTS.md` (sinh ra)
- Modify: `rosettex/protocol/__init__.py` (replace the temporary version), `README.md`
- Test: `tests/test_protocol.py`

**Interfaces:**
- Consumes: `load_pack` (Task 7).
- Produces:
  - `text(lang="vi") -> str`.
  - `skill_md() -> str`.
  - `agents_md() -> str`.
  - `python -m rosettex.protocol.build` writes `skill/SKILL.md` and `skill/AGENTS.md`.

- [ ] **Step 1: Write the failing test**

`tests/test_protocol.py`:
```python
from pathlib import Path

from rosettex.protocol import agents_md, skill_md, text

ROOT = Path(__file__).parent.parent


def test_guide_has_every_step_and_rules():
    t = text("vi")
    for cmd in ("rosettex extract", "rosettex status", "rosettex next", "--outline", "rosettex terms",
                "rosettex accept", "rosettex validate", "rosettex render", "next_action"):
        assert cmd in t
    assert "0.05" in t and "{{RULES}}" not in t
    assert "&&" not in t and " | " not in t


def test_generated_files_in_sync():
    assert (ROOT / "skill" / "SKILL.md").read_text(encoding="utf-8") == skill_md()
    assert (ROOT / "skill" / "AGENTS.md").read_text(encoding="utf-8") == agents_md()


def test_skill_frontmatter_triggers():
    head = skill_md().split("---")[1]
    assert "name: rosettex" in head and "dịch bài báo" in head and "translate this paper" in head
```

- [ ] **Step 2: Run to see the failure**

Run: `uv run pytest tests/test_protocol.py -v`
Expected: FAIL with `ImportError: cannot import name 'agents_md'`

- [ ] **Step 3: Write `rosettex/protocol/protocol.md`**

````markdown
# rosettex — PDF paper translation protocol

You (the agent) are the translator. `rosettex` extracts the content, splits it into batches, checks it and builds the PDF. General rules:
- Run only **one** `rosettex …` command at a time, always with `--json`. Do not chain commands, do not use pipes.
- Every result has a `next_action`: that is the next command to run.
- A command that returns exit code 2 is an environment or usage error: tell the user the `error` and `next_action`, then stop.

## 0. Before running
- You need three pieces of information: the PDF file, the target language, and where to save (a directory or a .pdf file). If the language or the location is missing, ask the user **one combined question** (for example "Which language do you want to translate into, and where should I save the result?"; if only one is missing, ask only for that), then stop and wait for the answer.
- If the `rosettex` command cannot run, tell the user to install it following the rosettex README.

## 1. Extract
Run `rosettex extract "<pdf>" --lang <code> --out "<location>" --json`.
- `output_exists: true`: ask the user whether to overwrite the output file, and remember the answer for step 5.
- `lang_tested: false`: tell the user "this language has not been tested".
- Record `work` (the work directory).

Then every 30–60 seconds run `rosettex status "<work>" --json`, until `state` is no longer `extract_running`. Report progress to the user briefly, following `step`.

## 2. Terms
Run `rosettex next "<work>" --outline --json` to read the title, abstract and section headings. Write a JSON file:
`{"topic": "<one sentence>", "terms": [{"src": "<source term>", "dst": "<translation>"}], "keep": ["<keep verbatim>"], "abbreviations": [{"abbr": "<abbreviation>", "expansion": "<full form>"}]}`
then run `rosettex terms "<work>" --file "<file>" --json`.

## 3. Translate batch by batch
Repeat these steps:
1. Run `rosettex next "<work>" --json`. If you receive `done: true`, go to step 4.
2. Translate every `batch.items[].text` following the rules at the end of this document. Use `terms`, `topic`, `section` and `previous` (the last paragraph of the previous batch) to keep the text coherent.
3. Write a JSON file `{"<id>": "<translation>"}` containing every `id` of the batch.
4. Run exactly the command in `submit`, replacing `<file.json>` with the file you just wrote.
5. If you receive `ok: false`, fix exactly the `id`s listed in `errors` and resubmit the whole batch.

Also:
- If you can read images, look at `page_images` when the source sentence is hard to understand.
- If you have subagents, give each subagent one batch via `rosettex next "<work>" --batch <id> --json`. Each subagent submits its own batch.

## 4. Validate the whole paper
Run `rosettex validate "<work>" --json`. If errors remain, resubmit the batches containing the reported `id`s, until `ok: true`.

## 5. Build the PDF
Run `rosettex render "<work>" --json`. Add `--overwrite` if the user agreed to overwrite in step 1.

Then every 30–60 seconds run `rosettex status "<work>" --json`, until `state` is `done`. If you can read images or PDFs, open the output file and look at the first page, a page with a table, and a page with a formula.

## 6. Report the result
Read the `report.json` file (its path is in `status`) and report briefly to the user:
- the output file path;
- the topic and a few key terms;
- the tier used;
- the items in `fallback`, with pages: formulas or tables shown as LaTeX code;
- the items in `flagged`, with pages: suspicious formulas;
- the sentence "formulas and tables have not been checked against the original";
- any problems in `postcheck.problems`.

## Interrupted or lost context
Run `rosettex status "<work>" --json` and follow `next_action`. If you do not remember `<work>`, it is at `<directory containing the output file>/.rosettex/<original PDF file name>.<language code>`.

## Translation rules
{{RULES}}
````

- [ ] **Step 4: Write `rosettex/protocol/__init__.py` and `build.py`**

`rosettex/protocol/__init__.py`:
```python
"""Protocol for the agent: a single source (protocol.md), printed by `rosettex guide`, generates SKILL.md and AGENTS.md."""
from importlib.resources import files

from ..checks import load_pack

FRONTMATTER = """---
name: rosettex
description: Translate a whole scientific PDF paper into another language (Vietnamese by default), keeping formulas, tables, figures and citations, and rebuild the PDF. Use when the user says "translate this paper", "translate this PDF to…", "dịch bài báo", "dịch file PDF này sang…", "dịch paper".
---

Run `rosettex guide` to see the latest version of the protocol below, then follow it.

"""


def text(lang: str = "vi") -> str:
    src = (files("rosettex") / "protocol" / "protocol.md").read_text(encoding="utf-8")
    rules = "\n".join(f"- {r}" for r in load_pack(lang)["rules"])
    return src.replace("{{RULES}}", rules)


def skill_md() -> str:
    return FRONTMATTER + text("vi")


def agents_md() -> str:
    return text("vi")
```

`rosettex/protocol/build.py`:
```python
"""Write skill/SKILL.md and skill/AGENTS.md from protocol.md. Run: uv run python -m rosettex.protocol.build"""
from pathlib import Path

from . import agents_md, skill_md


def main() -> None:
    out = Path(__file__).resolve().parents[2] / "skill"
    out.mkdir(exist_ok=True)
    (out / "SKILL.md").write_text(skill_md(), encoding="utf-8", newline="\n")
    (out / "AGENTS.md").write_text(agents_md(), encoding="utf-8", newline="\n")
    print(out)


if __name__ == "__main__":
    main()
```

Run: `uv run python -m rosettex.protocol.build`
Expected: prints the path `…/rosettex/skill`

- [ ] **Step 5: Write `README.md`**

````markdown
# rosettex

A tool for an **in-harness agent** (Claude Code, Codex, …) to translate a whole scientific PDF paper. rosettex does not translate itself:
it extracts the content (MinerU), splits it into batches with placeholders, checks the translation and rebuilds the PDF with LaTeX, with formulas and tables as real characters.
Slice 1 supports PDFs with a text layer.

## Installation (slice 1: manual install)

```bash
uv tool install <path-to-rosettex-repo>          # or: pipx install <path>
uv venv --python 3.12 ~/.rosettex/mineru-venv
uv pip install --python ~/.rosettex/mineru-venv "mineru>=4,<5"
~/.rosettex/mineru-venv/bin/mineru-kit models download --tier standard --source modelscope
#   Windows: %USERPROFILE%\.rosettex\mineru-venv\Scripts\mineru-kit.exe models download --tier standard
#   Download hangs: stop and rerun (downloads resume), or switch to --source huggingface
uv run python scripts/get_tectonic.py               # Tectonic 0.17.0 → ~/.rosettex/bin (skip if xelatex is already installed)
```

Optional environment variables (set once by the user): `ROSETTEX_MINERU` (path to `mineru-kit`), `ROSETTEX_TEX` (path to `tectonic`/`xelatex`), `ROSETTEX_HOME` (default `~/.rosettex`).

## Attaching to a harness

- **Claude Code:** copy `skill/SKILL.md` to `~/.claude/skills/rosettex/SKILL.md`.
- **Codex and harnesses that read AGENTS.md:** copy the contents of `skill/AGENTS.md` into your `AGENTS.md`.
- **Other harnesses:** tell the agent "run `rosettex guide` then follow it".

After that just say: *"Translate the paper ~/papers/attention.pdf into Vietnamese, save it to ~/dich/"*.

## Development

```bash
uv sync
uv run pytest -q                                   # ROSETTEX_REAL=1 to also run end-to-end with the real MinerU
uv run python -m rosettex.protocol.build           # after editing rosettex/protocol/protocol.md
```

Spec: `docs/superpowers/specs/`. Plans: `docs/superpowers/plans/`.
````

- [ ] **Step 6: Run the tests**

Run: `uv run pytest tests/test_protocol.py tests/test_cli.py::test_guide_and_version -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add rosettex/protocol skill README.md tests/test_protocol.py
git commit -m "feat: single-source protocol (rosettex guide), generate SKILL.md and AGENTS.md, README

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 14: CI on three operating systems

**Files:**
- Create: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: `scripts/get_tectonic.py` (Task 11), the test suite.
- Produces: the CI matrix.

- [ ] **Step 1: Write `.github/workflows/ci.yml`**

```yaml
name: ci
on: [push, pull_request]
jobs:
  test:
    strategy:
      fail-fast: false
      matrix:
        os: [ubuntu-latest, macos-latest, windows-latest]
        python: ["3.11", "3.12"]
    runs-on: ${{ matrix.os }}
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v6
      - name: Install Python
        run: uv python install ${{ matrix.python }}
      - name: Install Tectonic 0.17.0 (SHA-256 verified)
        run: uv run --python ${{ matrix.python }} python scripts/get_tectonic.py
      - name: Run tests
        run: uv run --python ${{ matrix.python }} pytest -q
```

- [ ] **Step 2: Check the YAML syntax locally**

Run: `uv run --with pyyaml python -c "import yaml,sys; yaml.safe_load(open('.github/workflows/ci.yml')); print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: run tests on Linux/macOS/Windows, Python 3.11 and 3.12

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

The repo has no remote yet. Creating the GitHub repo and pushing is the user's decision, so **ask first**, do not do it on your own. After pushing, check the result with `gh run list` and `gh run view`.

---

### Task 15: Slice 1 acceptance (real machine, real MinerU, real agent)

**Files:**
- Create: `docs/superpowers/notes/2026-11-18-slice1-acceptance.md`

**Interfaces:**
- Consumes: everything.
- Produces: the acceptance record, with figures for SG 16 section 7.

- [ ] **Step 1: Install like a user**

```bash
uv tool install --editable .
uv venv --python 3.12 ~/.rosettex/mineru-venv
uv pip install --python ~/.rosettex/mineru-venv "mineru>=4,<5"
~/.rosettex/mineru-venv/bin/mineru-kit models verify --tier standard
```
Expected: `MinerU2.5-Pro-2605-1.2B-GGUF: ok` (the model is already in `~/.mineru/models` from the spike).

- [ ] **Step 2: End-to-end with the real MinerU, using the fake translator (success criterion 1)**

Run: `ROSETTEX_REAL=1 uv run pytest tests/test_cli.py -k real -v`
Expected: PASS for both `short` and `long`. The `long` paper takes about 6 minutes at tier `standard` on CPU.

- [ ] **Step 3: Real agent (success criterion 2)**

Copy the skill: `mkdir -p ~/.claude/skills/rosettex && cp skill/SKILL.md ~/.claude/skills/rosettex/SKILL.md`.

Open a new Claude Code session in an empty directory and type exactly one sentence:
`Translate the paper /home/admin/Desktop/Learn/rosettex/tests/fixtures/papers/short.pdf into Vietnamese, save it to ~/dich-thu/`

Record in the report:
- whether the agent asked any question (it must not);
- the number of commands the agent ran;
- whether a PDF was produced;
- whether, reading the translation yourself, you find it acceptable.

- [ ] **Step 4: Measure the rate of batches rejected on first submit (criterion 4, SG 16 section 7)**

Repeat Step 3 with `long.pdf`. Then run the command below, replacing `<work>` with `~/dich-thu/.rosettex/long.vi`:
```bash
uv run python -c "import json,sys; r=[json.loads(l) for l in open(sys.argv[1], encoding='utf-8')]; first={}; [first.setdefault(x['batch'], x['ok']) for x in r]; print(len(first), 'batches;', sum(not v for v in first.values()), 'rejected on first submit')" <work>/logs/accept.jsonl
```

- [ ] **Step 5: Write the record and commit**

`docs/superpowers/notes/2026-11-18-slice1-acceptance.md` containing:
- the results of Steps 2–4 (real figures);
- the list of problems encountered, each noting which slice will handle it;
- the status of the SG 14 acceptance criteria that can be checked in slice 1.

```bash
git add docs/superpowers/notes
git commit -m "docs: slice 1 acceptance record

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

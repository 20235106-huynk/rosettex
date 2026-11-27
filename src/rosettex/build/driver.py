import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from rosettex.build.guard import guard_lua_path, scan
from rosettex.build.texbin import find_tex

ENGINES = {"lua": "lualatex", "xe": "xelatex", "pdf": "pdflatex"}
_RERUN = re.compile(
    r"Rerun to get|Label\(s\) may have changed|Please rerun|There were undefined "
    r"(?:references|citations)|Rerun LaTeX"
)
_ERROR = re.compile(r"^(?:! .*|.+:\d+: .*)$", re.M)
_GUARD_REFUSED = re.compile(r"^rosettex-guard: refused .*$", re.M)
# Missing input files that a TeX Live package can provide (spec §9.7). Group 1 = file name, group 2 = font name.
_MISSING = re.compile(
    r"LaTeX Error: File [`']([^'\s]+\.(?:sty|cls|bst|def|cfg|fd|clo|tex))' not found"
    r"|Font \\?[^=\n]+=([^\s:\[\"]+)(?: at [^\n]*?)? not (?:loadable: (?:Metric \(TFM\)|metric data)|found)"
    r"|The font \"([^\"]+)\" cannot be found"
    r"|I couldn't open style file ([^\s]+\.bst)",
    re.M,
)
MAX_INSTALLS = 5
INSTALLED: list[dict] = []  # per build; pipeline.build clears it. ponytail: module state, one build per process
SEARCHED: dict[str, str | None] = {}  # name → owning package, one tlmgr search per name per build (cleared with INSTALLED)
# --budget (spec §3, §9.7): monotonic deadline of this command; pipeline.build sets it. WORKED records that this
# process already did one unit (an engine pass or a compile), so every rerun makes progress before it stops.
BUDGET = {"deadline": None, "worked": False}


class BudgetStop(Exception):
    """The budget is used up at a safe point; args[0] is the pass state to persist (passes, did_bib)."""


def over_budget() -> bool:
    d = BUDGET["deadline"]
    return d is not None and BUDGET["worked"] and time.monotonic() > d


def missing_files(text: str) -> list[str]:
    """Files (x.sty, x.tfm, a fontspec font name, x.bst) the log or .blg says could not be found, in order."""
    out: list[str] = []
    for m in _MISSING.finditer(text):
        name = m.group(1) or m.group(4) or m.group(3) or f"{m.group(2)}.tfm"
        if name not in out:
            out.append(name)
    return out


def owning_package(name: str, tlmgr: str) -> str | None:
    """The TeX Live package that ships `name`, from `tlmgr search --global --file` (cached per build)."""
    if name not in SEARCHED:
        SEARCHED[name] = _search(name, tlmgr)
    return SEARCHED[name]


def _search(name: str, tlmgr: str) -> str | None:
    from rosettex.toolchain import _tlmgr_env

    pat = f"/{name}" if "." in name else name.replace(" ", "")  # a fontspec font name: start of a file name
    if pat.startswith("-"):  # the name comes from the paper: never let it become a tlmgr option
        return None
    try:  # a killed search is harmless (it only reads)
        r = subprocess.run(
            [tlmgr, "search", "--global", "--file", pat],
            capture_output=True, text=True, stdin=subprocess.DEVNULL, env=_tlmgr_env(), timeout=120,
        )
    except subprocess.TimeoutExpired:
        return None
    pkg = None
    for line in r.stdout.splitlines():
        if line and not line[0].isspace() and line.endswith(":"):
            pkg = line[:-1]
        elif pkg and line[:1].isspace():
            base = line.strip().rsplit("/", 1)[-1]
            if base == name if "." in name else (base == pat or base.startswith((pat + ".", pat + "-"))):
                return pkg
    return None


def _install_missing(text: str, tried: set[str], deadline: float) -> bool:
    """Install the package of the first not-yet-tried missing file that has one, with the private tlmgr. Runs
    between passes, never inside a TeX process; tlmgr install is never killed (a killed tlmgr can truncate
    texlive.tlpdb), so none starts once the time budget is spent (the file is then diagnosed missing_package)."""
    from rosettex.toolchain import _tlmgr_env, tlmgr_path

    tlmgr = tlmgr_path()
    tried |= {i["file"] for i in INSTALLED}  # installed by an earlier compile of this build
    todo = [f for f in missing_files(text) if f not in tried]
    if not tlmgr or len(INSTALLED) >= MAX_INSTALLS:
        return False
    for name in todo:
        if time.monotonic() > deadline:
            return False
        tried.add(name)
        t0 = time.monotonic()
        pkg = owning_package(name, tlmgr)
        if pkg:
            break
    else:
        return False
    lock = open(Path(tlmgr).parents[2] / "tlmgr.lock", "w")  # parallel builds share one tree
    try:
        if os.name == "posix":
            import fcntl

            fcntl.flock(lock, fcntl.LOCK_EX)  # ponytail: no lock on Windows
        r = subprocess.run(
            [tlmgr, "install", pkg],
            capture_output=True, text=True, stdin=subprocess.DEVNULL, env=_tlmgr_env(),
        )
    finally:
        lock.close()
    ok = r.returncode == 0
    INSTALLED.append({"file": name, "package": pkg, "seconds": round(time.monotonic() - t0, 1), "ok": ok})
    return ok


@dataclass
class BuildResult:
    ok: bool
    engine: str
    passes: int
    pdf: str | None
    log: str
    aux: str | None
    timed_out: bool = False
    errors: list[str] = field(default_factory=list)
    guard: str = ""


def _env(extra: dict[str, str] | None = None, exe: str | None = None) -> dict:
    env = dict(os.environ)
    env.update({"openin_any": "p", "openout_any": "p", "max_print_line": "10000"})
    tex_exe = exe
    if not tex_exe:
        for name in ("lualatex", "pdflatex", "xelatex"):
            tex_exe = find_tex(name)
            if tex_exe:
                break
    if tex_exe:
        tex_dir = str(Path(tex_exe).resolve().parent)
        cur = env.get("PATH", "")
        env["PATH"] = f"{tex_dir}{os.pathsep}{cur}" if cur else tex_dir
    if extra:
        env.update(extra)
    return env


def _run(cmd: list[str], cwd: Path, timeout: int, extra_env: dict[str, str] | None = None) -> bool:
    exe = cmd[0] if cmd else None
    try:
        subprocess.run(
            cmd,
            cwd=cwd,
            env=_env(extra_env, exe=exe),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
            check=False,
        )
        return True
    except subprocess.TimeoutExpired:
        return False


def run_engine(
    build_dir: Path,
    main: str,
    engine: str,
    timeout: int = 300,
    max_passes: int = 5,
    guard: bool = True,
    single_pass: bool = False,
    resume: dict | None = None,
    allow_lua_files: frozenset[str] | set[str] = frozenset(),
) -> BuildResult:
    build_dir = Path(build_dir)
    exe = find_tex(ENGINES[engine])
    stem = Path(main).stem
    log = build_dir / f"{stem}.log"
    guard_mode = ("lua+scan" if engine == "lua" else "scan-only") if guard else ""
    if guard:
        tex_files = sorted(p.relative_to(build_dir).as_posix() for p in build_dir.rglob("*.tex") if p.is_file())
        findings = scan(build_dir, tex_files, allow_lua_files)
        if findings:
            errs = [
                f"rosettex-guard: {f['file']}:{f['line']} ({f['kind']}): {f['text']}" for f in findings[:20]
            ]
            return BuildResult(False, engine, 0, None, str(log), None, False, errs, guard=guard_mode)
    if exe is None:
        return BuildResult(
            False, engine, 0, None, str(log), None, errors=[f"{ENGINES[engine]} not found"], guard=guard_mode
        )
    extra_env: dict[str, str] | None = None
    if guard and engine == "lua":
        extra_env = {"ROSETTEXGUARDLUA": str(guard_lua_path())}
        tex_input = (
            f'\\directlua{{dofile(os.getenv("ROSETTEXGUARDLUA"))}}\\input{{\\detokenize{{{main}}}}}'
        )
        cmd = [exe, "-interaction=nonstopmode", "-file-line-error", f"-jobname={stem}", tex_input]
    else:
        cmd = [exe, "-interaction=nonstopmode", "-file-line-error", main]
    passes = 0
    if single_pass:
        max_passes = 1
    did_bib = single_pass or (build_dir / f"{stem}.bbl").exists()
    if resume:  # continue a budget-stopped compile in the same build dir (aux/bbl/log of earlier passes kept)
        passes, did_bib = resume["passes"], resume["did_bib"]
    started = time.monotonic()
    tried: set[str] = set()
    while passes < max_passes:
        if over_budget():  # safe point: between passes, after a tlmgr install and after bibtex/biber (soft budget)
            raise BudgetStop({"passes": passes, "did_bib": did_bib})
        BUDGET["worked"] = True
        if not _run(cmd, build_dir, timeout, extra_env):
            return BuildResult(
                False, engine, passes + 1, None, str(log), None, True, ["timeout"], guard=guard_mode
            )
        text = log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""
        if _install_missing(text, tried, started + timeout):
            continue  # rerun the same pass with the package installed
        passes += 1
        if not did_bib:
            did_bib = True
            aux = build_dir / f"{stem}.aux"
            biber = find_tex("biber")
            if (build_dir / f"{stem}.bcf").exists() and biber:
                _run([biber, stem], build_dir, timeout)
                continue
            bibtex = find_tex("bibtex")
            if aux.exists() and "\\bibdata{" in aux.read_text(encoding="utf-8", errors="replace") and bibtex:
                blg = build_dir / f"{stem}.blg"
                _run([bibtex, stem], build_dir, timeout)
                while blg.exists() and _install_missing(blg.read_text(encoding="utf-8", errors="replace"), tried, started + timeout):
                    if over_budget():  # safe point after an install; the resumed pass reruns bibtex
                        raise BudgetStop({"passes": passes, "did_bib": False})
                    _run([bibtex, stem], build_dir, timeout)
                continue
        if not _RERUN.search(text) or time.monotonic() - started > timeout:
            break
    text = log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""
    errors = _ERROR.findall(text)
    if guard and engine == "lua":
        refused = _GUARD_REFUSED.findall(text)
        if refused:
            errors = refused + errors
    pdf = build_dir / f"{stem}.pdf"
    ok = pdf.exists() and pdf.stat().st_size > 0 and not errors
    aux = build_dir / f"{stem}.aux"
    return BuildResult(
        ok,
        engine,
        passes,
        str(pdf) if pdf.exists() else None,
        str(log),
        str(aux) if aux.exists() else None,
        False,
        errors[:20],
        guard=guard_mode,
    )

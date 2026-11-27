"""Private TinyTeX (`rosettex setup`) and the toolchain check (`rosettex doctor`). Spec §12."""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from importlib import resources
from pathlib import Path

from rosettex import __version__
from rosettex.build.texbin import find_tex, rosettex_home, texbin_dir, toolchain_state

# Pinned TinyTeX release (github.com/rstudio/tinytex-releases, tag v2026.11 = TeX Live 2026).
# Only the Linux asset is exercised; the macOS and Windows entries are written from the release metadata, not run.
TINYTEX = "v2026.11"
_RELEASE_URL = f"https://github.com/rstudio/tinytex-releases/releases/download/{TINYTEX}/"
ASSETS = {
    "linux": ("TinyTeX-v2026.11.tar.gz", 200716250, "28d06592ef33ac03693c78a5bff6d5748f7e2a2aceebd02731e864d50f95a411"),
    "darwin": ("TinyTeX-v2026.11.tgz", 267663308, "b87e5b74ea1f22e53152790804987da1edc45f9fb8cfb4d8a033646399d0ac15"),
    "win32": ("TinyTeX-v2026.11.zip", 247432160, "a13d14ba987873d1bdf81773f5bdfcc34537882b825dd2134f61eb6ee21d5f69"),
}
# Engine spike §1 working set, minus collection-fontsextra (missing fonts come on demand). Only yb-book (a book
# class) pulls fontsextra in, so the closure skips both. collection-pictures still comes with latexextra.
COLLECTIONS = [
    "collection-luatex",
    "collection-xetex",
    "collection-fontsrecommended",
    "collection-latexextra",
    "collection-mathscience",
    "collection-publishers",
    "collection-bibtexextra",
    "collection-langcyrillic",
    "collection-langother",
    "collection-langchinese",
    "collection-langjapanese",
    "collection-langcjk",
    "collection-langarabic",
]
# fontsextra members that rosettex's own preambles load by file name (build/preamble.py; doctor fails without them)
FONTS = ["cm-unicode", "noto", "tempora", "arimo"]
EXCLUDE = {"collection-fontsextra", "yb-book"}
BATCH = 300  # packages per tlmgr call; the budget is checked between calls (tlmgr is never stopped mid-run)
PACKAGES_MB = 725  # compressed containers of the closure of COLLECTIONS + FONTS (texlive.tlpdb, TL 2026)
REPO = "https://mirror.ctan.org/systems/texlive/tlnet"
CORE = [
    "fontspec.sty", "babel.sty", "polyglossia.sty", "luatexja-fontspec.sty", "xeCJK.sty", "hyperref.sty",
    "amsmath.sty", "graphicx.sty", "luatex85.sty", "algorithm.sty", "natbib.sty", "phvr7t.tfm", "t5enc.def",
]
# one language per script profile (data/scripts/*.json)
PROFILE_LANG = {
    "latin": "vi", "cyrillic": "ru", "han": "zh-Hans", "kana": "ja", "hangul": "ko", "arabic": "ar",
    "devanagari": "hi", "bengali": "bn", "tamil": "ta", "telugu": "te", "gujarati": "gu", "kannada": "kn",
    "thai": "th", "myanmar": "my", "ethiopic": "am",
}


def _argv(*parts) -> list[str]:
    return ["rosettex", *[str(p) for p in parts]]


def _save_state(st: dict) -> None:
    home = rosettex_home()
    home.mkdir(parents=True, exist_ok=True)
    tmp = home / "toolchain.json.tmp"
    tmp.write_text(json.dumps(st, indent=1), encoding="utf-8")
    os.replace(tmp, home / "toolchain.json")


def _private_tlmgr() -> Path | None:
    for d in sorted((rosettex_home() / "texlive" / "bin").glob("*")):
        for n in ("tlmgr", "tlmgr.bat"):
            if (d / n).is_file():
                return d / n
    return None


def tlmgr_path() -> str | None:
    """The private tlmgr when the private TinyTeX is the active TeX, else None (system TeX is never modified)."""
    _, source = texbin_dir()
    p = _private_tlmgr()
    return str(p) if source == "private" and p else None


def _asset() -> tuple[str, int, str]:
    plat = "linux" if sys.platform.startswith("linux") else sys.platform
    if plat not in ASSETS or (plat == "linux" and os.uname().machine != "x86_64"):
        raise RuntimeError(f"no pinned TinyTeX for {sys.platform}; use 'rosettex setup --tex system'")
    return ASSETS[plat]


def _download(url: str, dest: Path, size: int, deadline: float) -> bool:
    """Resumable download to dest (via dest.part + Range). False when the budget ran out."""
    part = dest.with_name(dest.name + ".part")
    have = part.stat().st_size if part.exists() else 0
    if have >= size:
        os.replace(part, dest)
        return True
    req = urllib.request.Request(url, headers={"User-Agent": f"rosettex/{__version__}"})
    if have:
        req.add_header("Range", f"bytes={have}-")
    with urllib.request.urlopen(req, timeout=60) as r:
        mode = "ab" if have and r.status == 206 else "wb"
        with open(part, mode) as f:
            while chunk := r.read(1 << 20):
                f.write(chunk)
                if time.monotonic() > deadline:
                    return False
    os.replace(part, dest)
    return True


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def _extract(archive: Path, root: Path) -> None:
    """Unpack into root (= the TinyTeX tree), atomically: a half-extracted tree never sits at root."""
    tmp = root.with_name(root.name + ".tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as z:
            z.extractall(tmp)  # zipfile drops absolute paths and '..'
    else:
        with tarfile.open(archive) as t:
            t.extractall(tmp, filter="data")  # refuses absolute paths, '..', links leaving the tree
    (top,) = list(tmp.iterdir())  # .TinyTeX/ (Linux, macOS) or TinyTeX/ (Windows)
    shutil.rmtree(root, ignore_errors=True)
    os.replace(top, root)
    tmp.rmdir()


def probe_ipv4_only(url: str = REPO + "/tlpkg/texlive.tlpdb.sha512") -> bool:
    """True when the TeX Live repository answers over IPv4 but not over IPv6 (tlmgr downloads with curl)."""
    curl = shutil.which("curl")
    if not curl:
        return False

    def ok(flag: str) -> bool:
        r = subprocess.run(
            [curl, flag, "-fsSL", "--max-time", "20", "-o", os.devnull, "-A", f"rosettex/{__version__}", url],
            capture_output=True,
        )
        return r.returncode == 0

    return not ok("-6") and ok("-4")


def _tlmgr_env() -> dict:
    env = dict(os.environ)
    curl_home = rosettex_home() / "curl"
    if (curl_home / ".curlrc").is_file():
        env["CURL_HOME"] = str(curl_home)
    env["PATH"] = str(_private_tlmgr().parent) + os.pathsep + env.get("PATH", "")
    return env


def _run(cmd: list[str], log: Path) -> bool:
    # Never killed: a tlmgr stopped while it rewrites texlive.tlpdb leaves it truncated (seen: 0 bytes).
    with open(log, "ab") as out:
        out.write(f"\n$ {' '.join(cmd)}\n".encode())
        out.flush()
        return subprocess.run(
            cmd, stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, env=_tlmgr_env()
        ).returncode == 0


def _run_retry(cmd: list[str], log: Path, tries: int = 3) -> bool:
    """tlmgr over mirror.ctan.org: a mirror can fail mid-download; each retry is redirected to another mirror."""
    for attempt in range(tries):
        if _run(cmd, log):
            return True
        if attempt < tries - 1:
            time.sleep(10 * (attempt + 1))
    return False


def _tlpdb(path: Path) -> dict[str, list[str]]:
    deps = {}
    for rec in path.read_text(encoding="utf-8", errors="replace").split("\n\n"):
        m = re.search(r"^name (\S+)", rec, re.M)
        if m:
            deps[m.group(1)] = re.findall(r"^depend (\S+)", rec, re.M)
    return deps


def _missing_packages() -> list[str]:
    """Closure of COLLECTIONS + FONTS minus EXCLUDE, minus what is installed, read from the repository
    tlpdb that tlmgr cached during `update --self`."""
    tlpkg = rosettex_home() / "texlive" / "tlpkg"
    remote = _tlpdb(max(tlpkg.glob("texlive.tlpdb.main.*"), key=lambda p: p.stat().st_mtime))
    have = _tlpdb(tlpkg / "texlive.tlpdb")
    arch = _private_tlmgr().parent.name
    seen, todo = [], COLLECTIONS + FONTS
    while todo:
        name = todo.pop().replace(".ARCH", f".{arch}")
        if name in EXCLUDE or name in seen or name not in remote:
            continue
        seen.append(name)
        todo += remote[name]
    return [n for n in reversed(seen) if n not in have]  # dependencies first


def setup(yes: bool, budget: float | None, tex: str) -> tuple[dict, int]:
    home = rosettex_home()
    st = toolchain_state()
    if tex == "system":
        lua = shutil.which("lualatex")
        if not lua:
            return {"ok": False, "error": "lualatex not found on PATH", "next_argv": _argv("setup")}, 2
        texbin = str(Path(lua).resolve().parent)
        _save_state({"tex": "system", "texbin": texbin})  # the choice stands even when doctor fails
        doc, code = doctor()
        if not doc["ok"]:
            return {"ok": False, "tex": "system", "texbin": texbin, "doctor": doc,
                    "next_action": "the system TeX fails doctor; fix it, or use 'rosettex setup' (private TinyTeX)",
                    "next_argv": _argv("doctor")}, code
        return {"ok": True, "tex": "system", "texbin": texbin, "passed": doc["passed"], "next_argv": []}, 0
    try:
        name, size, sha = _asset()
    except RuntimeError as exc:
        return {"ok": False, "error": str(exc), "next_argv": _argv("setup", "--tex", "system")}, 2
    if (st.get("tex") == "private" and st.get("state") == "complete"
            and st.get("tinytex") == TINYTEX and st.get("asset", name) == name):  # else the resume path (as below)
        return {"ok": True, "state": "complete", "root": str(home / "texlive"), "next_argv": _argv("doctor")}, 0
    if not yes:
        return {
            "ok": False,
            "state": "ask_user",
            "ask_user": (
                f"Install a private TeX Live (TinyTeX {TINYTEX}) into {home / 'texlive'}? It downloads "
                f"{size // 10**6} MB now and about {PACKAGES_MB} MB of packages (about 2 GB on disk). "
                "Or use an existing TeX Live with 'rosettex setup --tex system'."
            ),
            "download_bytes": size + PACKAGES_MB * 10**6,
            "next_argv": _argv("setup", "--yes"),
        }, 2

    deadline = time.monotonic() + (budget if budget is not None else 10**9)
    keep = ("done", "seconds", "ipv4_only", "packages_total")
    if st.get("tinytex") != TINYTEX or st.get("asset", name) != name:
        keep = ()  # partial state of another pin: download (and SHA-check) the current asset again
    st = {"tex": "private", "state": "partial", "tinytex": TINYTEX, "asset": name,
          **{k: v for k, v in st.items() if k in keep}}
    st.setdefault("done", [])
    st.setdefault("seconds", {})
    log = home / "setup.log"
    home.mkdir(parents=True, exist_ok=True)
    resume = _argv("setup", "--yes", *(["--budget", budget] if budget is not None else []))

    def partial(step: str) -> tuple[dict, int]:
        _save_state(st)
        return {"ok": False, "state": "partial", "step": step, "done": st["done"],
                "next_action": "budget used up; rerun to continue", "next_argv": resume}, 1

    def failed(what: str) -> tuple[dict, int]:
        _save_state(st)
        return {"ok": False, "state": "partial", "error": f"{what} failed; see {log}", "next_argv": resume}, 2

    def step(name_: str, t0: float) -> None:
        st["done"].append(name_)
        st["seconds"][name_] = round(time.monotonic() - t0 + st["seconds"].get(name_, 0), 1)
        _save_state(st)

    archive = home / "dl" / name
    if "download" not in st["done"]:
        t0 = time.monotonic()
        archive.parent.mkdir(parents=True, exist_ok=True)
        try:
            done = _download(_RELEASE_URL + name, archive, size, deadline)
        except OSError as exc:
            _save_state(st)
            return {"ok": False, "state": "partial", "error": f"network: {exc}", "next_argv": resume}, 2
        st["seconds"]["download"] = round(time.monotonic() - t0 + st["seconds"].get("download", 0), 1)
        if not done:
            return partial("download")
        if _sha256(archive) != sha:
            archive.unlink()
            _save_state(st)
            return {"ok": False, "state": "partial", "error": f"SHA-256 mismatch for {name}; deleted, rerun",
                    "next_argv": resume}, 2
        st["done"].append("download")
        _save_state(st)
    if "extract" not in st["done"]:  # a stopped extraction left only texlive.tmp: redo it
        t0 = time.monotonic()
        try:
            _extract(archive, home / "texlive")
        except OSError as exc:
            return failed(f"extract ({exc})")
        step("extract", t0)
        archive.unlink(missing_ok=True)  # ~200-270 MB; a re-extract downloads it again
    if not _private_tlmgr():
        st["done"].remove("extract")  # the tree is unusable: extract again on the rerun
        if not archive.is_file() and "download" in st["done"]:
            st["done"].remove("download")
        return failed("extract (no tlmgr in the extracted tree)")
    if "network" not in st["done"]:
        t0 = time.monotonic()
        ipv4 = probe_ipv4_only()
        if ipv4:
            (home / "curl").mkdir(exist_ok=True)
            (home / "curl" / ".curlrc").write_text("ipv4\n", encoding="ascii")
        else:
            (home / "curl" / ".curlrc").unlink(missing_ok=True)
        st["ipv4_only"] = ipv4
        if not _run([str(_private_tlmgr()), "option", "repository", REPO], log):
            return failed("tlmgr option repository")
        step("network", t0)
    if "packages" not in st["done"]:
        t0 = time.monotonic()
        tl = str(_private_tlmgr())
        # the pinned tlmgr is older than the repository's: tlmgr refuses to install until it updates itself
        if not _run_retry([tl, "update", "--self"], log):
            return failed("tlmgr update --self")
        todo = _missing_packages()
        st.setdefault("packages_total", len(todo))
        while todo:
            if time.monotonic() > deadline:
                st["packages_left"] = len(todo)
                st["seconds"]["packages"] = round(time.monotonic() - t0 + st["seconds"].get("packages", 0), 1)
                return partial("packages")
            # --no-depends: the batch list is already the closure, and the default would pull fontsextra back in
            if not _run_retry([tl, "install", "--no-depends", *todo[:BATCH]], log):
                return failed("tlmgr install")
            todo = todo[BATCH:]
        st.pop("packages_left", None)
        step("packages", t0)
    st["state"] = "complete"
    files = (f for f in (home / "texlive").rglob("*") if f.is_file() and not f.is_symlink())
    st["disk_mb"] = round(sum(f.stat().st_size for f in files) / 10**6)
    _save_state(st)
    return {"ok": True, "state": "complete", "root": str(home / "texlive"), "disk_mb": st["disk_mb"],
            "seconds": st["seconds"], "ipv4_only": st.get("ipv4_only"), "next_argv": _argv("doctor")}, 0


def _smoke(profile: str, lang_code: str, root: Path) -> dict:
    from rosettex.build.driver import run_engine
    from rosettex.build.preamble import insert_preamble, language_preamble, marks_sty, preamble_prelude, staged_fonts
    from rosettex.langdata import load_lang

    t0 = time.monotonic()
    lang = load_lang(lang_code)
    d = root / profile
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    fonts = resources.files("rosettex.data").joinpath("fonts")
    for f in staged_fonts(lang):
        (d / f).write_bytes(fonts.joinpath(f).read_bytes())
    (d / "rosettex-marks.sty").write_text(marks_sty(), encoding="utf-8")
    (d / "rosettex-preamble.tex").write_text(language_preamble("lua", lang), encoding="utf-8")
    sample = lang.get("sample") or lang["profile"].get("sample", "")
    doc = f"\\documentclass{{article}}\n\\begin{{document}}\n{sample}\n\n1.5 (2.1) [3]\n\\end{{document}}\n"
    (d / "main.tex").write_text(insert_preamble(doc, prelude=preamble_prelude("lua", lang)), encoding="utf-8")
    res = run_engine(d, "main.tex", "lua", timeout=300)
    log = Path(res.log).read_text(encoding="utf-8", errors="replace") if Path(res.log).exists() else ""
    missing = len(re.findall(r"^Missing character", log, re.M))
    ok = res.ok and missing == 0
    out = {"profile": profile, "lang": lang_code, "ok": ok, "seconds": round(time.monotonic() - t0, 1)}
    if not ok:
        out["error"] = (res.errors[:3] or [f"{missing} missing characters"]) if res.errors or missing else ["no pdf"]
    return out


def doctor(force: bool = False) -> tuple[dict, int]:
    home = rosettex_home()
    lua = find_tex("lualatex")
    bindir, source = texbin_dir()
    if not lua:
        return {"ok": False, "error": "lualatex not found", "source": source,
                "next_action": "install a TeX Live first",
                "next_argv": _argv("setup")}, 2
    st = Path(lua).resolve().stat()
    key = {"lualatex": str(Path(lua).resolve()), "mtime": st.st_mtime, "size": st.st_size, "rosettex": __version__}
    tlpdb = Path(lua).resolve().parents[2] / "tlpkg" / "texlive.tlpdb"  # changes on every tlmgr install/remove
    if tlpdb.is_file():
        key["tlpdb_mtime"] = tlpdb.stat().st_mtime
    cache = home / "doctor.json"
    if not force and cache.is_file():
        try:
            old = json.loads(cache.read_text(encoding="utf-8"))
            if old.get("key") == key:
                return {**old["result"], "source": source, "cached": True}, 0
        except ValueError:
            pass
    t0 = time.monotonic()
    ver = subprocess.run([lua, "--version"], capture_output=True, text=True).stdout
    year = re.search(r"TeX Live (\d{4})", ver)
    kpse = find_tex("kpsewhich")
    found = subprocess.run([kpse, *CORE], capture_output=True, text=True).stdout.split() if kpse else []
    packages = {c: next((f for f in found if Path(f).name == c), None) for c in CORE}
    root = home / "doctor"
    with ThreadPoolExecutor(4) as ex:
        profiles = list(ex.map(lambda kv: _smoke(kv[0], kv[1], root), PROFILE_LANG.items()))
    passed = sum(p["ok"] for p in profiles)
    ok = passed == len(profiles) and all(packages.values())
    result = {
        "ok": ok,
        "source": source,
        "texbin": str(bindir or Path(lua).parent),
        "lualatex": ver.splitlines()[0] if ver else None,
        "texlive": int(year.group(1)) if year else None,
        "packages": packages,
        "profiles": profiles,
        "passed": f"{passed}/{len(profiles)}",
        "seconds": round(time.monotonic() - t0, 1),
        "next_action": None if ok else "fix the failing items (rosettex setup installs a TeX Live that passes)",
        "next_argv": [] if ok else _argv("setup"),
    }
    if ok:  # only a green result is cached; a failing one is rechecked next time
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({"key": key, "result": result}, ensure_ascii=False, indent=1), encoding="utf-8")
    return {**result, "cached": False}, 0 if ok else 1

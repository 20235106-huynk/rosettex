"""Fonts by file name (S1b §6): TeX bundle fonts, or fonts packaged in rosettex/fonts copied into out/fonts
(Tectonic reads no absolute paths)."""
from __future__ import annotations

import hashlib
import json
import re
import shutil
from importlib.resources import files
from pathlib import Path

from .checks import load_profile


def package_dir() -> Path:
    return Path(str(files("rosettex") / "fonts"))


_SHA: dict = {}


def _sha(p: Path) -> str:
    st = p.stat()
    key = (str(p), st.st_size, st.st_mtime_ns)  # a font file is hashed once per process, not once per compile
    if key not in _SHA:
        _SHA[key] = hashlib.sha256(p.read_bytes()).hexdigest()
    return _SHA[key]


def _manifest() -> dict:
    try:
        m = json.loads((package_dir() / "MANIFEST.json").read_text(encoding="utf-8"))
        return {e["file"]: e for e in m["fonts"]}
    except (OSError, ValueError, KeyError):  # a damaged install must become FontMissing, not a traceback
        return {}


class FontMissing(Exception):
    def __init__(self, script: str, needed: list[dict], message: str | None = None):
        names = ", ".join(n["file"] for n in needed)
        super().__init__(message or f"font file(s) {names} for the {script} script are missing or damaged in the "
                                    f"rosettex installation; reinstall rosettex")
        self.script, self.needed = script, needed

    def to_json(self) -> dict:
        return {"font_missing": True, "script": self.script,
                "fonts_needed": [{"file": n["file"], "license": n.get("license")} for n in self.needed]}


def _usable(e: dict) -> bool:
    if e["source"] == "bundle":
        return True
    m, p = _manifest().get(e["file"]), package_dir() / e["file"]
    return bool(m) and p.is_file() and _sha(p) == m["sha256"]


def resolve_fonts(pack: dict) -> dict:
    latin = load_profile("latin")["fonts"][0]
    for e in pack["fonts"]:
        if _usable(e):
            return {"latin": latin, "script": e}
    raise FontMissing(pack["profile"]["id"], pack["fonts"])


def fontspec(e: dict) -> tuple[str, str]:
    opts = (["Path=fonts/"] if e["source"] == "package" else []) + ([e["options"]] if e.get("options") else [])
    return e["file"], ",".join(opts)


def stage_fonts(resolved: dict, outdir: Path) -> list[dict]:
    rec = []
    for e in (resolved["latin"], resolved["script"]):
        if e["source"] == "package":
            src, dst = package_dir() / e["file"], Path(outdir) / "fonts" / e["file"]
            dst.parent.mkdir(parents=True, exist_ok=True)
            if not (dst.is_file() and dst.stat().st_size == src.stat().st_size and _sha(dst) == _sha(src)):
                shutil.copyfile(src, dst)
        item = {"file": e["file"], "source": e["source"], "license": e.get("license", "unknown")}
        if item not in rec:
            rec.append(item)
    return rec


def font_error(log: str) -> str | None:
    m = re.search(r'The font "([^"]+)" cannot be\s+found', log)
    return m.group(1) if m else None

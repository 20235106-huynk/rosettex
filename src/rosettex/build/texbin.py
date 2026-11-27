import json
import os
import shutil
from pathlib import Path


def rosettex_home() -> Path:
    return Path(os.environ.get("ROSETTEX_HOME") or "~/.rosettex").expanduser()


def toolchain_state() -> dict:
    p = rosettex_home() / "toolchain.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def private_bindir() -> Path | None:
    """bin/<arch> of rosettex's own TinyTeX, only once `setup` completed."""
    st = toolchain_state()
    if st.get("tex") != "private" or st.get("state") != "complete":
        return None
    for d in sorted((rosettex_home() / "texlive" / "bin").glob("*")):
        if any((d / n).is_file() for n in ("lualatex", "lualatex.exe")):
            return d
    return None


def texbin_dir() -> tuple[Path | None, str]:
    """Where TeX binaries come from: ROSETTEX_TEXBIN → private TinyTeX → recorded `--tex system` → PATH."""
    d = os.environ.get("ROSETTEX_TEXBIN")
    if d:
        return Path(d).expanduser(), "ROSETTEX_TEXBIN"
    priv = private_bindir()
    if priv:
        return priv, "private"
    st = toolchain_state()
    if st.get("tex") == "system" and st.get("texbin") and Path(st["texbin"]).is_dir():
        return Path(st["texbin"]), "system"
    return None, "PATH"


def find_tex(name: str) -> str | None:
    base, _ = texbin_dir()
    if base is None:
        return shutil.which(name)
    for cand in (base / name, base / f"{name}.exe", base / f"{name}.bat"):
        if cand.is_file():
            return str(cand)
    return None

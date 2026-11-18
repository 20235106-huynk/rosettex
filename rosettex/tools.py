"""Tìm công cụ ngoài (MinerU, LaTeX), đọc RAM, chọn tier."""
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
            for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
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
            # ponytail: macOS chỉ đọc được tổng RAM qua sysconf, không có RAM khả dụng; đủ để tránh máy rất yếu.
            # Nâng cấp: đọc vm_stat qua ctypes (host_statistics64) nếu cần chính xác.
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


def verify_models(cmd: list[str], tier: str, timeout: float = 120) -> tuple[bool, str]:
    try:
        p = subprocess.run(cmd + ["models", "verify", "--tier", tier], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, f"không chạy được kiểm tra mô hình: {e!r}"
    out = p.stdout + p.stderr
    return p.returncode == 0 and "missing" not in out.lower(), out

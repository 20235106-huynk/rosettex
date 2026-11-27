"""Safe unpack of a paper's source (spec §6): gzip→tar, gzip→single file, tar, zip, plain .tex."""
import gzip
import re
import shutil
import tarfile
import zipfile
from pathlib import Path

MAX_TOTAL = 500 * 1024 * 1024
MAX_FILES = 5000
MAX_RATIO = 100
RATIO_FLOOR = 10 * 1024 * 1024  # ponytail: small archives skip the ratio rule (a tiny tar of padding exceeds 100:1)
_BAD_CHARS = re.compile(r'[<>:"|?*\\\x00-\x1f]')
_RESERVED = re.compile(r"^(con|prn|aux|nul|com[1-9]|lpt[1-9])$", re.I)


class UnpackError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _refuse(member: str, rule: str):
    raise UnpackError("unsafe_archive", f"refused member {member!r}: {rule}")


def safe_name(name: str) -> str:
    """Validate a member name; return it normalised (no './', no trailing '/')."""
    if name.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", name):
        _refuse(name, "absolute path")
    parts = [p for p in name.split("/") if p not in ("", ".")]
    if not parts:
        _refuse(name, "empty name")
    for p in parts:
        if p == "..":
            _refuse(name, "'..' escapes the source directory")
        if _BAD_CHARS.search(p):
            _refuse(name, 'character invalid on Windows (<>:"|?*\\ or control)')
        if p[-1] in ". ":
            _refuse(name, "trailing dot or space is invalid on Windows")
        if _RESERVED.match(p.split(".")[0]):
            _refuse(name, "reserved device name on Windows (CON, PRN, AUX, NUL, COM1-9, LPT1-9)")
    return "/".join(parts)


def _check_collisions(entries: list[tuple[str, bool]]) -> None:
    """Refuse a file vs directory clash, or two names equal up to case (one overwrites the other on macOS and
    Windows; refused on every OS so an archive unpacks the same everywhere). entries = (normalised name, is_dir)."""
    seen: dict[str, tuple[str, bool]] = {}

    def add(name: str, is_dir: bool) -> None:
        old = seen.setdefault(name.casefold(), (name, is_dir))
        if old[0] != name:
            _refuse(name, f"same name as {old[0]!r} up to case")
        if old[1] != is_dir:
            _refuse(name, "used both as a file and as a directory")

    for name, is_dir in entries:
        parts = name.split("/")
        for i in range(1, len(parts)):
            add("/".join(parts[:i]), True)
        add(name, is_dir)


def _copy_capped(src, dst: Path, budget: list[int], member: str, packed: int) -> None:
    """Stream src into dst, refusing once the running total passes the size or ratio cap."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    with open(dst, "wb") as out:
        while chunk := src.read(1 << 20):
            budget[0] += len(chunk)
            if budget[0] > MAX_TOTAL:
                _refuse(member, f"total unpacked size over {MAX_TOTAL // 2**20} MB")
            if budget[0] > max(RATIO_FLOOR, MAX_RATIO * packed):
                _refuse(member, f"compression ratio over {MAX_RATIO}:1 (archive bomb?)")
            out.write(chunk)


def _check_counts(names: list[str], declared: int, packed: int) -> None:
    if len(names) > MAX_FILES:
        _refuse(names[MAX_FILES], f"more than {MAX_FILES} files")
    if declared > MAX_TOTAL:
        _refuse(names[-1], f"declared total size {declared} bytes over {MAX_TOTAL // 2**20} MB")
    if declared > max(RATIO_FLOOR, MAX_RATIO * packed):
        _refuse(names[-1], f"compression ratio over {MAX_RATIO}:1 (archive bomb?)")


def _is_root_dir(name: str, is_dir: bool) -> bool:
    """A '.' or './' directory member (the archive root itself): harmless, skipped."""
    return is_dir and not name.startswith(("/", "\\")) and not [p for p in name.split("/") if p not in ("", ".")]


def _untar(fh, dest: Path, packed: int) -> None:
    with tarfile.open(fileobj=fh, mode="r:") as tf:
        members = [m for m in tf.getmembers() if not _is_root_dir(m.name, m.isdir())]
        for m in members:
            name = safe_name(m.name)
            if m.issym():
                _refuse(m.name, "symbolic link")
            if m.islnk():
                _refuse(m.name, "hard link")
            if not (m.isfile() or m.isdir()):
                _refuse(m.name, "device, fifo or other special file")
            m.name = name
        _check_collisions([(m.name, m.isdir()) for m in members])
        files = [m for m in members if m.isfile()]
        _check_counts([m.name for m in files] or ["(empty)"], sum(m.size for m in files), packed)
        budget = [0]
        for m in members:
            if m.isdir():
                (dest / m.name).mkdir(parents=True, exist_ok=True)
            else:
                _copy_capped(tf.extractfile(m), dest / m.name, budget, m.name, packed)


def _unzip(path: Path, dest: Path, packed: int) -> None:
    with zipfile.ZipFile(path) as zf:
        infos = [i for i in zf.infolist() if not _is_root_dir(i.filename, i.is_dir())]
        _check_collisions([(safe_name(i.filename), i.is_dir()) for i in infos])
        for i in infos:
            mode = i.external_attr >> 16
            if (mode & 0o170000) == 0o120000:
                _refuse(i.filename, "symbolic link")
            if mode & 0o170000 not in (0, 0o100000, 0o040000):
                _refuse(i.filename, "device, fifo or other special file")
        files = [i for i in infos if not i.is_dir()]
        _check_counts([i.filename for i in files] or ["(empty)"], sum(i.file_size for i in files), packed)
        budget = [0]
        for i in infos:
            name = safe_name(i.filename)
            if i.is_dir():
                (dest / name).mkdir(parents=True, exist_ok=True)
            else:
                with zf.open(i) as src:
                    _copy_capped(src, dest / name, budget, name, packed)


def kind(path: Path) -> str:
    """By magic bytes: 'gzip', 'zip', 'tar', 'pdf' or 'text'."""
    with open(path, "rb") as fh:
        head = fh.read(512)
    if head[:2] == b"\x1f\x8b":
        return "gzip"
    if head[:4] == b"PK\x03\x04" or head[:4] == b"PK\x05\x06":
        return "zip"
    if head[257:262] == b"ustar":
        return "tar"
    if head[:5] == b"%PDF-":
        return "pdf"
    return "text"


def _gzip_name(path: Path) -> str | None:
    """FNAME field of a gzip header (RFC 1952), if any."""
    with open(path, "rb") as fh:
        head = fh.read(4096)
    if not head[3] & 0x08:
        return None
    pos = 10
    if head[3] & 0x04:  # FEXTRA
        pos += 2 + int.from_bytes(head[10:12], "little")
    end = head.find(b"\0", pos)
    return Path(head[pos:end].decode("latin-1")).name if end > pos else None


def unpack(path: Path, dest: Path, single_name: str | None = None) -> str:
    """Unpack path into the empty dir dest. Returns the detected kind. Raises UnpackError on refusal.

    A single gzipped file is named after its gzip header when that ends in .tex, else main.tex.
    A plain text file keeps single_name (default: its own name, or main.tex if not .tex)."""
    path, dest = Path(path), Path(dest)
    packed = max(path.stat().st_size, 1)
    k = kind(path)
    if k == "pdf":
        return "pdf"
    dest.mkdir(parents=True, exist_ok=True)
    try:
        if k == "zip":
            _unzip(path, dest, packed)
        elif k == "tar":
            with open(path, "rb") as fh:
                _untar(fh, dest, packed)
        elif k == "gzip":
            raw = dest.parent / (dest.name + ".gunzip")
            try:
                with gzip.open(path, "rb") as src:
                    _copy_capped(src, raw, [0], path.name, packed)
                if kind(raw) == "tar":
                    k = "tar.gz"
                    with open(raw, "rb") as fh:
                        _untar(fh, dest, packed)
                elif kind(raw) == "pdf":
                    shutil.copyfile(raw, dest / (single_name or "orig.pdf"))
                    return "pdf"
                else:
                    name = _gzip_name(path)
                    name = safe_name(name) if name and name.endswith(".tex") else "main.tex"
                    shutil.copyfile(raw, dest / name)
            finally:
                raw.unlink(missing_ok=True)
        else:
            name = single_name or (path.name if path.suffix == ".tex" else "main.tex")
            shutil.copyfile(path, dest / safe_name(name))
    except (tarfile.TarError, zipfile.BadZipFile, EOFError, gzip.BadGzipFile) as exc:
        raise UnpackError("bad_archive", f"{path.name}: {exc}") from None
    return k

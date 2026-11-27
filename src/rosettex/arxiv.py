"""arXiv input parsing and polite, resumable downloads (spec §6). Stdlib only."""
import http.client
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

from rosettex import __version__

BASE = "https://arxiv.org"
UA = f"rosettex/{__version__}"
SPACING = 3.0  # seconds between requests (arXiv etiquette)
TIMEOUT = 60

_ID = r"(?P<id>\d{4}\.\d{4,5}|[a-z][a-z.-]*/\d{7})(?:v(?P<ver>\d+))?"
_BARE = re.compile(rf"^(?:arxiv:)?{_ID}$", re.I)
_LINK = re.compile(rf"^(?:https?://)?(?:www\.|export\.)?arxiv\.org/(?:abs|pdf|e-print|src)/{_ID}(?:\.pdf)?/?$", re.I)
_last = 0.0


class ArxivError(Exception):
    def __init__(self, code: str, message: str, status: int | None = None, content_range: str | None = None):
        super().__init__(message)
        self.code, self.status, self.content_range = code, status, content_range


def parse(arg: str) -> tuple[str, int | None] | None:
    """'2005.11401v2', 'math/0211159', 'https://arxiv.org/pdf/2005.11401.pdf' → (id, version or None)."""
    arg = re.sub(r"[?#].*$", "", arg.strip())
    m = _BARE.match(arg) or _LINK.match(arg)
    if not m:
        return None
    return m.group("id"), int(m.group("ver")) if m.group("ver") else None


def _request(url: str, method: str = "GET", headers: dict | None = None):
    global _last
    wait = _last + SPACING - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    _last = time.monotonic()
    req = urllib.request.Request(url, method=method, headers={"User-Agent": UA, **(headers or {})})
    try:
        return urllib.request.urlopen(req, timeout=TIMEOUT)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise ArxivError("not_found", f"arXiv has no such paper: {url}") from None
        if exc.code in (403, 410):
            raise ArxivError("withdrawn", f"arXiv refuses this paper (withdrawn?): {url} HTTP {exc.code}") from None
        raise ArxivError("network", f"{url}: HTTP {exc.code}", exc.code, exc.headers.get("Content-Range")) from None
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ArxivError("network", f"{url}: {exc}") from None


def latest_version(aid: str) -> int:
    """The version arXiv serves for an unversioned ID, read from the e-print's Content-Disposition."""
    with _request(f"{BASE}/src/{aid}", "HEAD") as r:
        m = re.search(r"v(\d+)(?:\.[\w.]+)?\"?$", r.headers.get("Content-Disposition", ""))
    if not m:
        raise ArxivError("network", f"cannot read the version of {aid} from arXiv")
    return int(m.group(1))


def download(url: str, dest: Path) -> Path:
    """GET url into dest via dest.part, resuming with Range. Skips the network when dest exists."""
    dest = Path(dest)
    if dest.is_file():
        return dest
    part = dest.with_name(dest.name + ".part")
    have = part.stat().st_size if part.is_file() else 0
    try:
        r = _request(url, headers={"Range": f"bytes={have}-"} if have else None)
    except ArxivError as exc:
        if not (have and exc.status == 416):
            raise
        # the .part may already hold the whole file (a stop between the last write and the rename)
        if exc.content_range == f"bytes */{have}":
            part.replace(dest)
            return dest
        part.unlink()
        have = 0
        r = _request(url)
    try:
        with r:
            if have and r.status != 206:
                have = 0  # server ignored Range: start over
            total = r.headers.get("Content-Length")
            # ponytail: no Content-Length → no size check (arXiv always sends it); verify the archive if that changes
            total = int(total) + have if total else None
            with open(part, "ab" if have else "wb") as fh:
                while chunk := r.read(1 << 16):
                    fh.write(chunk)
    except (OSError, http.client.HTTPException) as exc:
        raise ArxivError("network", f"{url}: {exc}; rerun to resume") from None
    if total is not None and part.stat().st_size != total:
        raise ArxivError("network", f"{url}: incomplete download ({part.stat().st_size}/{total} bytes); rerun to resume")
    part.replace(dest)
    return dest


def fetch(aid: str, version: int | None, dl: Path) -> dict:
    """Download e-print and PDF of aid (latest version when None) into dl/. Returns the project.json 'arxiv' record."""
    dl.mkdir(parents=True, exist_ok=True)
    stem = aid.replace("/", "_")
    if version is None:
        version = latest_version(aid)
    vid = f"{aid}v{version}"
    urls = {"e-print": f"{BASE}/src/{vid}", "pdf": f"{BASE}/pdf/{vid}"}
    eprint = download(urls["e-print"], dl / f"{stem}v{version}.e-print")
    pdf = None
    try:
        pdf = download(urls["pdf"], dl / f"{stem}v{version}.pdf")
    except ArxivError as exc:
        if exc.code != "not_found":
            raise
    return {"id": aid, "version": version, "urls": urls, "e-print": eprint, "pdf": pdf}

"""Read/render PDF with pypdfium2 (Apache-2.0/BSD). Does not use PyMuPDF (AGPL)."""
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
    """PNG via the standard library (zlib), so Pillow is not needed."""
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


def render_region_png(pdf_path, page_no: int, bbox, out, dpi: int = 220) -> Path:
    """Render the normalised box (x0, y0, x1, y1; top-left origin) of a page to a PNG."""
    x0, y0, x1, y1 = bbox
    with _open(pdf_path) as pdf:
        page = pdf[page_no - 1]
        w, h = page.get_size()
        crop = (max(0.0, x0 * w), max(0.0, (1 - y1) * h), max(0.0, (1 - x1) * w), max(0.0, y0 * h))  # left, bottom, right, top
        bm = page.render(scale=dpi / 72, crop=crop, rev_byteorder=True)
        channels = 4 if bm.mode in ("RGBA", "RGBX") else 3
        buf, stride, row = bytes(bm.buffer), bm.stride, bm.width * channels
        blank = lambda y: min(buf[y * stride: y * stride + row]) >= 245  # noqa: E731  (white or near white)
        top, bottom = 0, bm.height
        while top < bottom - 1 and blank(top):  # the box may reach into empty space above or below the figure
            top += 1
        while bottom - 1 > top and blank(bottom - 1):
            bottom -= 1
        top, bottom = max(0, top - 4), min(bm.height, bottom + 4)
        write_png(out, bm.width, bottom - top, buf[top * stride: bottom * stride], stride, channels)
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
    """Total height (pt) of text lines, grouping characters by (left/right page half, vertical centre rounded to 4 pt)."""
    # ponytail: a full-width line is counted twice (left half + right half) in both source and translation, so the ratio
    # stays usable; upgrade: group by real horizontal gaps if the ratio is off on real papers.
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

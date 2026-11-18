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

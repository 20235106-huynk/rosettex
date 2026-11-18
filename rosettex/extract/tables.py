"""Bảng HTML của MinerU (`rowspan`/`colspan`) → danh sách ô có toạ độ lưới."""
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

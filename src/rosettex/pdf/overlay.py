"""PDF route overlay: translated segments -> ``overlay.pdf`` + ``fit.json`` in one LuaLaTeX pass.

Python writes ``overlay.tex`` (preamble, escaped bodies as ``\\def``s) and ``rxdata.lua`` (geometry and style per
segment, overlay space); ``rxfit.lua`` does all measuring with ``tex.runtoks`` and ships the pages.
"""

from __future__ import annotations

import json
import math
import re
import shutil
import statistics
import time
from pathlib import Path
from typing import Any

from rosettex.build.driver import run_engine
from rosettex.build.preamble import (
    _TL_FALLBACK_FONTS,
    fallback_bundled_fonts,
    fallback_preamble_lines,
    marks_sty,
    fallback_sty,
    needed_fallback_fonts,
    overlay_preamble,
    preamble_prelude,
    staged_fonts,
)
from rosettex.escape import encode_text
from rosettex.langdata import load_lang
from rosettex.pdf.merge import overlay_matrix
from rosettex.textio import write_text

FIT_SCHEMA = "rosettex.pdf-fit/1"
SCALES = (1.0, 0.95, 0.9, 0.85, 0.8)
FALLBACK_SCALES = (0.75, 0.7)

_PH = re.compile(r"⟦([a-z])(\d+)⟧")
_WS = re.compile(r"\s+")


class _Crop:
    """Duck-typed page for ``merge.overlay_matrix``."""

    def __init__(self, cropbox: list[float], rotate: int) -> None:
        self.cropbox = type("CB", (), {"left": cropbox[0], "bottom": cropbox[1], "right": cropbox[2], "top": cropbox[3]})()
        self.rotation = rotate


def page_matrix(page: dict) -> list[float]:
    """Overlay space -> layout space for a layout page (``merge.overlay_matrix`` on its CropBox and rotation)."""
    return overlay_matrix(_Crop(page["cropbox"], int(page["rotate"]) % 360))  # type: ignore[arg-type]


def to_overlay(m: list[float], rect: list[float]) -> list[float]:
    """Axis-aligned layout rect -> overlay rect (inverse of the orthogonal ``overlay_matrix``)."""
    a, b, c, d, e, f = m
    pts = [(a * (x - e) + b * (y - f), c * (x - e) + d * (y - f)) for x in (rect[0], rect[2]) for y in (rect[1], rect[3])]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return [min(xs), min(ys), max(xs), max(ys)]


def to_layout(m: list[float], rect: list[float]) -> list[float]:
    a, b, c, d, e, f = m
    pts = [(a * x + c * y + e, b * x + d * y + f) for x in (rect[0], rect[2]) for y in (rect[1], rect[3])]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return [min(xs), min(ys), max(xs), max(ys)]


def _lua(v: Any) -> str:
    if v is None:
        return "nil"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(round(float(v), 4)) if isinstance(v, float) else str(v)
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=True)  # JSON string escapes are valid Lua short-string escapes except \u
    if isinstance(v, dict):
        return "{" + ",".join(f"[{_lua(k)}]={_lua(x)}" for k, x in v.items()) + "}"
    return "{" + ",".join(_lua(x) for x in v) + "}"


def _lua_text(s: str) -> str:
    # JSON \uXXXX is not Lua: write non-ASCII as raw UTF-8 instead.
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r") + '"'


def _dump(v: Any) -> str:
    if isinstance(v, str):
        return _lua_text(v)
    if isinstance(v, dict):
        return "{" + ",".join(f"[{_dump(k)}]={_dump(x)}" for k, x in v.items()) + "}"
    if isinstance(v, (list, tuple)):
        return "{" + ",".join(_dump(x) for x in v) + "}"
    return _lua(v)


def _leading(block: dict) -> float:
    size = float(block["size"])
    gaps = []
    lines = block.get("lines") or []
    for l1, l2 in zip(lines, lines[1:]):
        dy = 0.5 * (l1[1] + l1[3]) - 0.5 * (l2[1] + l2[3])
        if 0.9 * size <= dy <= 2.2 * size:
            gaps.append(dy)
    return statistics.median(gaps) if gaps else 1.2 * size


_LONG = re.compile(r"\S{24,}")
_BRK = re.compile(r"(?<=[/.\-_:?=&,;])")


def _enc(s: str) -> str:
    """encode_text, with break opportunities inside long unspaced runs (URLs, DOIs) so they never overflow a box."""
    s = _LONG.sub(lambda m: "\x00".join(_BRK.split(m.group(0))), s)
    return "\\allowbreak{}".join(encode_text(p) for p in s.split("\x00"))


def _body(text: str, ph: dict, snips: dict[int, dict]) -> str:
    """Escaped TeX body: ``⟦mN⟧`` -> ``\\rxM{N}`` (snippet) or its escaped raw, ``⟦lN⟧`` -> ``\\rxL{link}{raw}``."""
    out: list[str] = []
    pos = 0
    text = _WS.sub(" ", text).strip()
    for m in _PH.finditer(text):
        out.append(_enc(text[pos : m.start()]))
        pos = m.end()
        code = f"{m.group(1)}{m.group(2)}"
        info = ph.get(code)
        if not isinstance(info, dict):
            continue
        raw = _WS.sub(" ", str(info.get("raw", ""))).strip()
        link = info.get("link")
        if m.group(1) == "m" and info.get("snippet") is not None and info.get("size"):
            n = int(m.group(2))
            snips[n] = {
                "page": int(info["snippet"]) + 1,
                "w": float(info["size"][0]),
                "h": float(info["size"][1]),
                "d": float(info.get("depth") or 0.0),
                "link": link,
            }
            out.append(f"\\rxM{{{n}}}")
        elif link is not None:
            out.append(f"\\rxL{{{int(link)}}}{{{_enc(raw)}}}")
        else:
            out.append(_enc(raw))
    out.append(_enc(text[pos:]))
    return "".join(out).replace("⟦", "").replace("⟧", "")


def _segment_data(layout: dict, translations: dict[str, str]) -> tuple[list[dict], dict[str, str], dict[str, int]]:
    blocks = {b["id"]: b for b in layout.get("blocks") or []}
    segs: list[dict] = []
    bodies: dict[str, str] = {}
    nchars: dict[str, int] = {}
    pages = layout["pages"]
    mats = [page_matrix(p) for p in pages]
    for seg in layout.get("segments") or []:
        sid = seg["id"]
        tr = translations.get(sid)
        if not isinstance(tr, str) or not tr.strip():
            continue
        blks = [blocks.get(b) for b in seg.get("blocks") or []]
        if not blks or any(b is None or not isinstance(b.get("box"), dict) for b in blks):
            continue
        first = blks[0]
        boxes = []
        for b in blks:
            bx = b["box"]
            ov = to_overlay(mats[b["page"]], [bx["x0"], bx["y0_min"], bx["x1"], bx["y1"]])
            boxes.append({"page": b["page"], "x0": ov[0], "y0": ov[1], "x1": ov[2], "y1": ov[3]})
        snips: dict[int, dict] = {}
        body = _body(tr, seg.get("ph") or {}, snips)
        col = first.get("color") or [0, 0, 0]
        plain = _PH.sub(lambda m: str((seg.get("ph") or {}).get(f"{m.group(1)}{m.group(2)}", {}).get("raw", "")), tr)
        nchars[sid] = len(_WS.sub(" ", plain).strip())
        align = first.get("align") or "justify"
        if seg["kind"] in ("title", "heading", "caption") and align == "justify":
            align = "left"
        hang = 0.0
        if align in ("left", "justify") and len(first.get("lines") or []) >= 2:
            ov_lns = [to_overlay(mats[first["page"]], ln) for ln in first["lines"]]
            l0_x0 = ov_lns[0][0]
            cont_x0s = [ln[0] for ln in ov_lns[1:]]
            cont_med = statistics.median(cont_x0s)
            if cont_med - l0_x0 >= 4.0 and all(abs(x - cont_med) <= 6.0 for x in cont_x0s):
                min_bw = min((bx["x1"] - bx["x0"]) for bx in boxes)
                hang = round(min(cont_med - l0_x0, 0.25 * min_bw), 2)
        segs.append(
            {
                "id": sid,
                "kind": seg["kind"],
                "size": float(first["size"]),
                "lead": _leading(first),
                "bold": bool(first.get("bold")),
                "italic": bool(first.get("italic")),
                "color": None if list(col) == [0, 0, 0] else [int(c) for c in col],
                "align": align,
                "hang": hang,
                "boxes": boxes,
                "snips": snips,
                "pages": sorted({b["page"] for b in blks}),
            }
        )
        bodies[sid] = body
    return segs, bodies, nchars


def _overlay_tex(lang: dict, bodies: dict[str, str], chain: list[str], has_math: bool) -> str:
    fb = fallback_preamble_lines() if chain else []
    lines = [
        preamble_prelude("lua", lang).rstrip("\n"),
        "\\documentclass{article}",
        "\\usepackage{graphicx}",
        "\\usepackage{xcolor}",
        *fb,
        overlay_preamble(lang).rstrip("\n"),
        "\\pagestyle{empty}",
        "\\makeatletter",
        "\\newbox\\rxbox\\newbox\\rxtmp",
        "\\newcommand\\rxsmash[1]{\\setbox\\rxtmp\\hbox{#1}\\ht\\rxtmp=0pt \\dp\\rxtmp=0pt \\box\\rxtmp}",
        "\\newcommand\\rxM[1]{\\directlua{rx.m(#1)}}",
        "\\newcommand\\rxL[2]{\\begingroup\\directlua{tex.attribute[rx.attr]=#1}#2\\endgroup}",
        "\\makeatother",
        "\\pdfvariable horigin=0pt \\pdfvariable vorigin=0pt",
        f"\\directlua{{rx_boxnum=\\number\\rxbox rx_math={'true' if has_math else 'false'} dofile(\"rxfit.lua\")}}",
    ]
    for sid, body in bodies.items():
        lines.append(f"\\expandafter\\gdef\\csname rxb@{sid}\\endcsname{{{body}}}")
    lines += ["\\begin{document}", "\\directlua{rx.run()}", "\\end{document}", ""]
    return "\n".join(lines)


def _overfull_log(log: Path) -> list[float]:
    if not log.exists():
        return []
    txt = log.read_text(encoding="utf-8", errors="replace")
    return [float(x) for x in re.findall(r"Overfull \\hbox \(([\d.]+)pt too wide", txt)]


def overlay(
    layout: dict,
    translations: dict[str, str],
    lang_code: str,
    pdf_dir: Path,
    *,
    fallback: bool = True,
) -> dict:
    """Typeset every translated segment into its boxes (one scale per segment) and write ``overlay.pdf`` and
    ``fit.json`` into ``pdf_dir``. Returns the fit dict."""
    pdf_dir = Path(pdf_dir)
    pdf_dir.mkdir(parents=True, exist_ok=True)
    lang = load_lang(lang_code)
    segs, bodies, nchars = _segment_data(layout, translations)
    pages = layout["pages"]
    mats = [page_matrix(p) for p in pages]
    page_rows = []
    for p, m in zip(pages, mats):
        cb = p["cropbox"]
        w, h = cb[2] - cb[0], cb[3] - cb[1]
        if int(p["rotate"]) % 360 in (90, 270):
            w, h = h, w
        page_rows.append({"w": w, "h": h, "m": m})
    profile = lang.get("profile") or {}
    data = {
        "lang": lang_code,
        "rtl": profile.get("direction") == "rtl",
        "fallback": bool(fallback),
        "scales": list(SCALES),
        "fscales": list(FALLBACK_SCALES),
        "pages": page_rows,
        "segs": segs,
        "math_pdf": "inline_math.pdf" if (pdf_dir / "inline_math.pdf").is_file() else None,
    }
    has_math = any(s["snips"] for s in segs) and data["math_pdf"] is not None
    if not has_math:
        for s in segs:
            s["snips"] = {}
    shutil.copyfile(Path(__file__).with_name("rxfit.lua"), pdf_dir / "rxfit.lua")
    write_text(pdf_dir / "rxdata.lua", "return " + _dump(data) + "\n")
    fonts_root = None
    from importlib import resources

    fonts_root = resources.files("rosettex.data").joinpath("fonts")

    chain = list(_TL_FALLBACK_FONTS)
    seconds = 0.0
    res = None
    for attempt in range(2):
        for fname in dict.fromkeys(staged_fonts(lang) + fallback_bundled_fonts(chain)):
            (pdf_dir / fname).write_bytes(fonts_root.joinpath(fname).read_bytes())
        write_text(pdf_dir / "rosettex-fallback.sty", fallback_sty(chain))
        write_text(pdf_dir / "rosettex-marks.sty", marks_sty())
        write_text(pdf_dir / "overlay.tex", _overlay_tex(lang, bodies, chain, True))
        for stale in ("overlay.pdf", "fit.json"):
            (pdf_dir / stale).unlink(missing_ok=True)
        t0 = time.perf_counter()
        res = run_engine(
            pdf_dir, "overlay.tex", "lua", single_pass=True, guard=True, allow_lua_files=frozenset({"overlay.tex"})
        )
        seconds += time.perf_counter() - t0
        log = Path(res.log) if res.log else pdf_dir / "overlay.log"
        extra = [f for f in needed_fallback_fonts(log.read_text(encoding="utf-8", errors="replace") if log.exists() else "") if f not in chain]
        if attempt == 0 and extra:
            chain += extra
            continue
        break
    assert res is not None
    fit_path = pdf_dir / "fit.json"
    if not fit_path.is_file():
        raise RuntimeError(f"overlay build failed ({lang_code}): {res.errors[:3]}")
    raw = json.loads(fit_path.read_text(encoding="utf-8"))
    out_segs = []
    for s in raw["segments"]:
        boxes = []
        for b in s["boxes"]:
            pg = b["page"]
            boxes.append({"page": pg, "rect": [round(v, 2) for v in to_layout(mats[pg], b["rect"])], "lines": b["lines"]})
        total_lines = sum(b["lines"] for b in s["boxes"]) or 1
        cut = math.ceil(s["overflow_lines"] * nchars.get(s["id"], 0) / total_lines) if s["overflow_lines"] else 0
        out_segs.append(
            {
                "id": s["id"],
                "kind": s["kind"],
                "scale": s["scale"],
                "pt": round(s["pt"], 3),
                "boxes": boxes,
                "overflow_lines": s["overflow_lines"],
                "overflow_pt": round(s["overflow_pt"], 2),
                "cut_chars": cut,
                "fallback": bool(s["fallback"]),
                "clamped": list(s.get("clamped") or []),
            }
        )
    links = [
        {"link": lk["link"], "page": lk["page"], "rects": [[round(v, 2) for v in r] for r in lk["rects"]]}
        for lk in raw.get("links", [])
    ]
    fit = {
        "schema": FIT_SCHEMA,
        "lang": lang_code,
        "seconds": round(seconds, 3),
        "pages": len(pages),
        "segments": out_segs,
        "links": links,
    }
    fit["overfull_pt"] = _overfull_log(pdf_dir / "overlay.log")
    fit["fallback_fonts"] = chain
    fit["engine_ok"] = bool(res.ok)
    fit["engine_errors"] = list(res.errors[:5])
    fit_path.write_text(json.dumps(fit, ensure_ascii=False, indent=1), encoding="utf-8")
    return fit

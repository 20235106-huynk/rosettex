"""render: trial-compile each item, last-resort fallback, compile the whole paper, post-build check, report.json."""
from __future__ import annotations

import re
import shutil
from pathlib import Path

from ..checks import load_pack
from ..pdf import render_region_png
from ..fonts import FontMissing, font_error, resolve_fonts, stage_fonts
from ..tools import find_tex
from ..translate import manifest, translations
from ..workspace import Work, read_json, run_with_heartbeat, timeout_for, write_json
from .latex import Renderer, VENUE_STYLES, paper_style
from .postcheck import postcheck


class RenderError(Exception):
    pass


def run_tex(tool, workdir: Path, name: str, on_beat=None) -> tuple[bool, str, str]:
    kind, cmd = tool
    stem = Path(name).stem
    logf, runf = Path(workdir) / f"{stem}.log", Path(workdir) / f"{stem}.run.txt"
    runs = [cmd + ["-X", "compile", "--keep-logs", name]] if kind == "tectonic" else \
        [cmd + ["-interaction=nonstopmode", "-halt-on-error", name]] * 3
    for argv in runs:
        logf.unlink(missing_ok=True)
        runf.unlink(missing_ok=True)
        try:
            rc = run_with_heartbeat(argv, cwd=workdir, log=runf, on_beat=on_beat or (lambda: None),
                                    timeout=timeout_for(300))
        except TimeoutError as e:
            raise RenderError(f"compiling {name} timed out: {e}")
        out = runf.read_text(encoding="utf-8", errors="replace") if runf.exists() else ""
        log = logf.read_text(encoding="utf-8", errors="replace") if logf.exists() else ""
        if rc != 0:
            return False, out, log
        if kind != "tectonic" and "Rerun" not in log:
            break
    return True, out, log


def culprit(log: str) -> str | None:
    i = log.find("\n! ")
    head = log if i < 0 else log[:i]
    if i < 0 and log.startswith("! "):
        head = ""
    ids = re.findall(r"^RX:(\S+)$", head, re.M)
    return ids[-1] if ids else None


def expand_fallback(doc, key: str, fb: set) -> int:
    before = len(fb)
    if ":" in key:
        fb.add(key)
        return len(fb) - before
    if not any(b["id"] == key for b in doc["blocks"]):
        return 0
    fb.add(key)
    for u in doc["units"]:
        if u["block"] == key:
            fb.update(f"{u['id']}:{n}" for n, p in u["ph"].items() if p["tex"].startswith("$"))
    return len(fb) - before


RXP = re.compile(r"^RXP:([rfb]):(.+):(\d+)$", re.M)


def float_pages(log: str) -> dict[str, list[int]]:
    """label -> [page of its first mention, page of the float], for floats that are mentioned."""
    seen: dict[str, dict[str, int]] = {}
    for kind, lab, page in RXP.findall(log):
        if kind != "b":
            seen.setdefault(lab, {}).setdefault(kind, int(page))
    return {lab: [v["r"], v["f"]] for lab, v in seen.items() if "r" in v and "f" in v}


def block_pages(log: str) -> dict[str, int]:
    """paragraph id -> the page it ends on."""
    return {lab: int(page) for kind, lab, page in RXP.findall(log) if kind == "b"}


def _off(pages: dict) -> int:
    """Distance from the page of first mention; a float before its mention breaks the convention and costs double."""
    return sum(f - r if f >= r else 2 * (r - f) for r, f in pages.values())


def place_floats(doc, log: str, place: dict) -> dict:
    """Emit each float that is not on the page of its first mention before the first paragraph that starts on that
    page (one page earlier for two-column floats: LaTeX puts those on the page after the one where they are issued)."""
    pages, ends = float_pages(log), block_pages(log)
    paras = [b["id"] for b in doc["blocks"] if b["id"] in ends]
    wide = {b.get("label"): b.get("wide") for b in doc["blocks"] if b["kind"] in ("table", "figure")}
    place = dict(place)
    for lab, (r, f) in pages.items():
        if f == r:
            continue
        t = r - 1 if wide.get(lab) else r
        prev_end = 0
        for pid in paras:
            if prev_end >= t:  # this paragraph starts on page t
                place[lab] = pid
                break
            prev_end = ends[pid]
    return place


def _tail(s: str, n: int = 1500) -> str:
    return s[-n:]


def _page_of(key: str) -> int:
    m = re.match(r"p(\d+)", key)
    return int(m.group(1)) if m else 0


def copy_assets(work: Work, doc) -> dict:
    images = {}
    (work.out / "assets").mkdir(parents=True, exist_ok=True)
    for b in doc["blocks"]:
        if b["kind"] == "figure" and b.get("crop") and work.src_pdf.is_file():
            dst = work.out / "assets" / f"{b['id']}.png"
            render_region_png(work.src_pdf, b["page"], b["crop"], dst)  # whole figure, not MinerU's shorter body
            images[b["id"]] = f"assets/{dst.name}"
        elif b["kind"] == "figure" and b.get("image"):
            src = work.root / b["image"]
            if src.is_file():
                dst = work.out / "assets" / f"{b['id']}{src.suffix.lower()}"
                shutil.copyfile(src, dst)
                images[b["id"]] = f"assets/{dst.name}"
    return images


def render_core(work: Work, out, *, overwrite: bool = False, on_beat=lambda step: None) -> dict:
    m = manifest(work)
    lang = m["lang"]
    pack = load_pack(lang)
    fonts_res = resolve_fonts(pack)  # FontMissing propagates before any work
    tool = find_tex()
    if tool is None:
        raise RenderError("Tectonic or XeLaTeX not found")
    out = Path(out)
    if out.exists() and not overwrite:
        raise RenderError(f"output file already exists: {out}")
    doc, tr = read_json(work.document), translations(work, lang)
    work.out.mkdir(parents=True, exist_ok=True)
    font_rec = stage_fonts(fonts_res, work.out)
    images = copy_assets(work, doc)
    fb = set(m.get("fallback", []))
    beat = lambda: on_beat("compiling")  # noqa: E731

    own_fonts = {e["file"] for e in fonts_res.values()} | {e["file"] for e in pack["fonts"]}
    style = paper_style(doc)  # the venue's class; falls back to the generic build if that class cannot compile
    venue_failed = []

    def retreat(why: str) -> bool:
        nonlocal style
        if style in VENUE_STYLES:
            venue_failed.append({"style": style, "why": why[-300:]})
            style = "generic"
            return True
        return False

    on_beat("probe")
    limit = len(doc["blocks"]) + sum(len(u["ph"]) for u in doc["units"]) + 1
    for _ in range(limit):
        (work.out / "probe.tex").write_text(Renderer(doc, tr, pack, fb, images, style).probe(), encoding="utf-8", newline="\n")
        ok, output, log = run_tex(tool, work.out, "probe.tex", beat)
        if ok:
            break
        bad = font_error(output + log)
        if bad and (bad in own_fonts or style not in VENUE_STYLES):  # not ours: maybe the venue class's own font
            raise FontMissing(pack["profile"]["id"], [e for e in pack["fonts"] if e["file"] == bad] or [{"file": bad, "license": None}],
                              message=f"TeX could not load the font {bad}: with XeLaTeX install the TeX package that provides it; "
                                      "with Tectonic rerun when the network works")
        c = culprit(log)
        if (bad or c is None or c in fb) and retreat(output + log):  # the venue class itself fails: generic build
            continue
        if c is None or c in fb:
            raise RenderError("trial compile failed and the failing item could not be identified: " + _tail(output + log))
        fb.add(c)

    on_beat("main")
    for _ in range(20):
        tex = Renderer(doc, tr, pack, fb, images, style).document()
        (work.out / "main.tex").write_text(tex, encoding="utf-8", newline="\n")
        ok, output, log = run_tex(tool, work.out, "main.tex", beat)
        if ok:
            break
        bad = font_error(output + log)
        if bad and (bad in own_fonts or style not in VENUE_STYLES):
            raise FontMissing(pack["profile"]["id"], [e for e in pack["fonts"] if e["file"] == bad] or [{"file": bad, "license": None}],
                              message=f"TeX could not load the font {bad}: with XeLaTeX install the TeX package that provides it; "
                                      "with Tectonic rerun when the network works")
        c = culprit(log)
        if bad and retreat(output + log):
            continue
        fixed = bool(c) and expand_fallback(doc, c, fb)  # item-level repair first
        if not fixed:
            if retreat(output + log):  # a class error that no single block explains (also after the first mark)
                continue
            raise RenderError("compiling main.tex failed: " + _tail(output + log))
    else:
        raise RenderError("more than 20 repair passes compiling main.tex")

    # Put each figure/table on the page that first mentions it: emit a float that landed later one block earlier and
    # rebuild (at most 3 more compiles); keep the best build. Wide (two-column) floats need this most: LaTeX puts them
    # on the page after the one where they are issued.
    on_beat("floats")
    place: dict[str, str] = {}
    pages = float_pages(log)
    best = (_off(pages), tex, output, log)
    shutil.copyfile(work.out / "main.pdf", work.out / "main.best.pdf")
    for _ in range(3):
        if not _off(pages):
            break
        new = place_floats(doc, log, place)
        if new == place:
            break
        place = new
        r = Renderer(doc, tr, pack, fb, images, style)
        r.place = dict(place)
        tex2 = r.document()
        (work.out / "main.tex").write_text(tex2, encoding="utf-8", newline="\n")
        try:
            ok2, output2, log2 = run_tex(tool, work.out, "main.tex", beat)
        except RenderError:  # an improvement pass must never turn a finished build into a failure (e.g. a timeout)
            break
        if not ok2:
            break
        log, pages = log2, float_pages(log2)
        if _off(pages) < best[0]:
            best = (_off(pages), tex2, output2, log2)
            shutil.copyfile(work.out / "main.pdf", work.out / "main.best.pdf")
    _, tex, output, log = best
    (work.out / "main.tex").write_text(tex, encoding="utf-8", newline="\n")
    shutil.copyfile(work.out / "main.best.pdf", work.out / "main.pdf")
    (work.out / "main.best.pdf").unlink()
    pages = float_pages(log)

    on_beat("postcheck")
    pdf = work.out / "main.pdf"
    pc = postcheck(work, pdf, log, tex, doc, tr, pack, fb)
    out.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(pdf, out)
    terms = read_json(work.terms, {})
    report = {"output": str(out), "lang": lang, "lang_tested": pack["tested"],
              "script": pack["profile"]["id"], "fonts": font_rec, "topic": terms.get("topic"),
              "key_terms": terms.get("terms", [])[:10], "tier": m.get("tier"), "tier_reason": m.get("tier_reason"),
              "device": "MinerU chooses (slice 1 does not force a device)", "tex": tool[0],
              "fallback": [{"id": k, "page": _page_of(k)} for k in sorted(fb)],
              "flagged": read_json(work.flagged, []), "dropped": len(doc["source"].get("dropped", [])),
              "unreviewed": True, "postcheck": pc,
              "style": style, "style_fallback": venue_failed,
              "floats_off_page": [{"label": lab, "mentioned_on": r, "placed_on": f} for lab, (r, f) in sorted(pages.items()) if f != r]}
    write_json(work.report, report)
    m["fallback"] = sorted(fb)
    write_json(work.manifest, m)
    return report

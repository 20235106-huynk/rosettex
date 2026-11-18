"""extract: run MinerU by page group (resumable), convert, split into batches, set flags."""
from __future__ import annotations

import re
import shutil
import zipfile

from ..pdf import text_in_bbox
from ..workspace import Work, read_json, run_with_heartbeat, timeout_for, write_json
from .mineru_adapter import adapt
from .segment import segment

ACCENTS = {r"\dot": "˙\u0307", r"\ddot": "¨\u0308", r"\hat": "ˆ^\u0302", r"\widehat": "ˆ^\u0302",
           r"\tilde": "˜~\u0303", r"\widetilde": "˜~\u0303", r"\bar": "¯\u0304"}
TEXT_IN_MATH = re.compile(r"\$|\b(?:e\.g\.|i\.e\.)")


class ExtractError(Exception):
    pass


def plan_groups(npages: int, size: int) -> list[tuple[int, int]]:
    return [(a, min(a + size - 1, npages)) for a in range(1, npages + 1, size)]


def flag_suspicious(doc, pdf_path) -> list[dict]:
    flags = []
    for b in doc["blocks"]:
        maths = [b["tex"]] if b["kind"] == "equation" else [s["v"] for s in b.get("spans", []) if s["t"] == "math"]
        layer = None
        for tex in maths:
            if TEXT_IN_MATH.search(tex):
                flags.append({"id": b["id"], "page": b["page"], "reason": "text leaked into formula", "tex": tex})
            for cmd, marks in ACCENTS.items():
                if re.search(re.escape(cmd) + r"(?![a-zA-Z])", tex):
                    if layer is None:
                        layer = text_in_bbox(pdf_path, b["page"], b["bbox"])
                    if layer.strip() and not any(c in layer for c in marks):
                        flags.append({"id": b["id"], "page": b["page"], "tex": tex,
                                      "reason": f"{cmd} has no matching mark in the PDF text layer"})
    return flags


def extract_core(work: Work, cmd: list[str], on_beat) -> dict:
    m = read_json(work.manifest)
    groups = plan_groups(m["pages"], m["pages_per_group"])
    for gi, (a, b) in enumerate(groups, 1):
        gdir = work.mineru / f"g{gi:03d}"
        if (gdir / ".done").exists():
            continue
        for _attempt in (1, 2):
            if gdir.exists():
                shutil.rmtree(gdir)
            gdir.mkdir(parents=True)
            try:
                rc = run_with_heartbeat(
                    cmd + ["parse", "src.pdf", "-o", gdir.relative_to(work.root).as_posix(), "--format", "zip",
                           "--tier", m["tier"], "--pages", f"{a}-{b}"],
                    cwd=work.root, log=work.logs / f"mineru-g{gi:03d}.log",
                    on_beat=lambda: on_beat(gi - 1, len(groups)), timeout=timeout_for(900 + 120 * (b - a + 1)))
            except TimeoutError as e:  # a hang will hang again: stop now, no second try
                raise ExtractError(f"MinerU timed out on pages {a}-{b}: {e}; see {work.logs / f'mineru-g{gi:03d}.log'}")
            zips = sorted(gdir.glob("*.zip"))
            if rc == 0 and len(zips) == 1:
                with zipfile.ZipFile(zips[0]) as z:
                    z.extractall(gdir / "x")
                (gdir / ".done").write_text("ok", encoding="utf-8")
                break
        else:
            raise ExtractError(f"MinerU failed twice on pages {a}-{b}; see {work.logs / f'mineru-g{gi:03d}.log'}")
        on_beat(gi, len(groups))
    data = [(read_json(work.mineru / f"g{gi:03d}" / "x" / "middle_json.json"), work.mineru / f"g{gi:03d}" / "x")
            for gi in range(1, len(groups) + 1)]
    doc, batches = segment(adapt(data, work.root))
    flags = flag_suspicious(doc, work.src_pdf)
    for bt in batches:
        write_json(work.batch_path(bt["batch"]), bt)
    write_json(work.flagged, flags)
    write_json(work.document, doc)  # written last: document.json exists only if extract finished completely
    return {"blocks": len(doc["blocks"]), "units": len(doc["units"]), "batches": len(batches),
            "flagged": len(flags), "columns": doc["source"]["columns"]}

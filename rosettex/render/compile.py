"""render: biên dịch thử từng mục, phương án cuối, biên dịch toàn bài, kiểm tra sau dựng, report.json."""
from __future__ import annotations

import re
import shutil
from pathlib import Path

from ..checks import load_pack
from ..tools import find_tex
from ..translate import manifest, translations
from ..workspace import Work, read_json, run_with_heartbeat, write_json
from .latex import Renderer
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
        rc = run_with_heartbeat(argv, cwd=workdir, log=runf, on_beat=on_beat or (lambda: None))
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


def _tail(s: str, n: int = 1500) -> str:
    return s[-n:]


def _page_of(key: str) -> int:
    m = re.match(r"p(\d+)", key)
    return int(m.group(1)) if m else 0


def copy_assets(work: Work, doc) -> dict:
    images = {}
    (work.out / "assets").mkdir(parents=True, exist_ok=True)
    for b in doc["blocks"]:
        if b["kind"] == "figure" and b.get("image"):
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
    tool = find_tex()
    if tool is None:
        raise RenderError("không tìm thấy Tectonic hoặc XeLaTeX")
    out = Path(out)
    if out.exists() and not overwrite:
        raise RenderError(f"file kết quả đã tồn tại: {out}")
    doc, tr = read_json(work.document), translations(work, lang)
    work.out.mkdir(parents=True, exist_ok=True)
    images = copy_assets(work, doc)
    fb = set(m.get("fallback", []))
    beat = lambda: on_beat("compiling")  # noqa: E731

    on_beat("probe")
    limit = len(doc["blocks"]) + sum(len(u["ph"]) for u in doc["units"]) + 1
    for _ in range(limit):
        (work.out / "probe.tex").write_text(Renderer(doc, tr, pack, fb, images).probe(), encoding="utf-8", newline="\n")
        ok, output, log = run_tex(tool, work.out, "probe.tex", beat)
        if ok:
            break
        c = culprit(log)
        if c is None or c in fb:
            raise RenderError("biên dịch thử lỗi mà không xác định được mục: " + _tail(output + log))
        fb.add(c)

    on_beat("main")
    for _ in range(20):
        tex = Renderer(doc, tr, pack, fb, images).document()
        (work.out / "main.tex").write_text(tex, encoding="utf-8", newline="\n")
        ok, output, log = run_tex(tool, work.out, "main.tex", beat)
        if ok:
            break
        c = culprit(log)
        if not c or not expand_fallback(doc, c, fb):
            raise RenderError("biên dịch main.tex lỗi: " + _tail(output + log))
    else:
        raise RenderError("quá 20 lượt sửa lỗi biên dịch main.tex")

    on_beat("postcheck")
    pdf = work.out / "main.pdf"
    pc = postcheck(work, pdf, log, tex, doc, tr, pack, fb)
    out.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(pdf, out)
    terms = read_json(work.terms, {})
    report = {"output": str(out), "lang": lang, "lang_tested": pack["tested"], "topic": terms.get("topic"),
              "key_terms": terms.get("terms", [])[:10], "tier": m.get("tier"), "tier_reason": m.get("tier_reason"),
              "device": "MinerU tự chọn (lát 1 chưa ép thiết bị)", "tex": tool[0],
              "fallback": [{"id": k, "page": _page_of(k)} for k in sorted(fb)],
              "flagged": read_json(work.flagged, []), "unreviewed": True, "postcheck": pc}
    write_json(work.report, report)
    m["fallback"] = sorted(fb)
    write_json(work.manifest, m)
    return report

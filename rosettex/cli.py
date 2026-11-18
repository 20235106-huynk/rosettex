"""Giao diện dòng lệnh rosettex. Mọi lệnh có --json; agent luôn làm theo `next_action`."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path

from . import __version__, translate
from .checks import load_pack
from .extract.run import ExtractError, extract_core
from .pdf import page_count
from .render.compile import RenderError, render_core
from .tools import available_ram_bytes, choose_tier, find_mineru, find_tex, verify_models
from .workspace import (OWNED, is_leftover, SCHEMA, UsageError, Work, beat, job_state, q, read_json, read_manifest, spawn_detached,
                        write_json)


def emit(obj, as_json: bool) -> None:
    if as_json:
        print(json.dumps(obj, ensure_ascii=False, indent=1))
        return
    for k, v in obj.items():
        print(f"{k}: {v if isinstance(v, (str, int, float, bool)) or v is None else json.dumps(v, ensure_ascii=False)}")


def resolve_out(pdf: Path, lang: str, out: str) -> Path:
    p = Path(out).expanduser()
    return (p if p.suffix.lower() == ".pdf" else p / f"{pdf.stem}.{lang}.pdf").resolve()


def _running(job) -> bool:
    return bool(job) and job["state"] == "running" and not job["dead"]


# ---------------- extract ----------------
def cmd_extract(a):
    if not re.fullmatch(r"[a-z]{2,3}(?:-[A-Za-z]{2,8})?", a.lang):
        raise UsageError(f"mã ngôn ngữ không hợp lệ: {a.lang} (ví dụ: vi, en, fr)")
    pdf = Path(a.pdf).expanduser().resolve()
    if not pdf.is_file():
        raise UsageError(f"không tìm thấy file: {pdf}")
    try:
        page_count(pdf)  # kiểm tra trước khi tạo thư mục: PDF hỏng/mã hóa không để lại dấu vết
    except Exception as e:
        raise UsageError(f"không đọc được PDF {pdf}: {e}", next_action="kiểm tra file PDF (hỏng hoặc có mật khẩu)")
    out = resolve_out(pdf, a.lang, a.out)
    work = Work(a.work or out.parent / ".rosettex" / f"{pdf.stem}.{a.lang}")
    digest = hashlib.sha256(pdf.read_bytes()).hexdigest()
    m = read_manifest(work, "chọn thư mục làm việc khác bằng -o <thư mục>")
    if m is None and work.root.is_dir() and any(work.root.iterdir()) and not is_leftover(work.root):
        raise UsageError(f"thư mục {work.root} không rỗng và không phải thư mục làm việc rosettex",
                         next_action="chọn thư mục trống hoặc chưa có bằng -o <thư mục>")
    if m and m["sha256"] != digest:
        raise UsageError(f"thư mục làm việc {work.root} thuộc một bài khác",
                         next_action=f"rosettex clean {q(work.root)} --json")
    if m and m["lang"] != a.lang:
        raise UsageError(f"thư mục làm việc {work.root} đã tạo cho ngôn ngữ {m['lang']}, không phải {a.lang}",
                         next_action=f"dùng thư mục khác bằng -o <thư mục>, hoặc xóa bằng: rosettex clean {q(work.root)} --json")
    cmd = find_mineru()
    if cmd is None:
        raise UsageError("chưa cài MinerU 4.x (mineru-kit); xem README mục Cài đặt")
    resume = (f"rosettex extract {q(pdf)} --lang {a.lang} --out {q(out)} -o {q(work.root)} --json"
              + (f" --tier {a.tier}" if a.tier else ""))
    if m is None:
        tier, reason = choose_tier(a.tier, available_ram_bytes())
        ok, log = verify_models(cmd, tier)
        if not ok:
            raise UsageError(f"mô hình MinerU cho tier {tier} chưa đủ",
                             next_action=f"mineru-kit models download --tier {tier}", detail=log[-2000:])
        work.root.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(pdf, work.src_pdf)
        m = {"schema": SCHEMA, "lang": a.lang, "out": str(out), "source": str(pdf), "sha256": digest,
             "pages": page_count(work.src_pdf), "tier": tier, "tier_reason": reason,
             "pages_per_group": a.pages_per_group, "extract_cmd": resume}
        write_json(work.manifest, m)
    elif a.tier and a.tier != m["tier"]:  # chạy lại với --tier basic sau lỗi: các nhóm còn lại dùng tier mới
        m["tier"], m["tier_reason"] = a.tier, "requested"
        write_json(work.manifest, m)
    info = {"work": str(work.root), "output": str(out), "output_exists": out.exists(), "pages": m["pages"],
            "tier": m["tier"], "lang_tested": load_pack(a.lang)["tested"]}
    if work.document.exists():
        return 0, {"ok": True, "state": "extracted", **info, "next_action": f"rosettex status {q(work.root)} --json"}
    if not a.foreground:
        if _running(job_state(work)):
            return 0, {"ok": True, "state": "already_running", **info,
                       "next_action": f"rosettex status {q(work.root)} --json", "wait_seconds": 30}
        argv = [sys.executable, "-m", "rosettex", "extract", str(pdf), "--lang", a.lang, "--out", str(out),
                "-o", str(work.root), "--foreground", "--json"] + (["--tier", a.tier] if a.tier else [])
        beat(work, "extract", resume, "khởi động", 0, 0)
        pid = spawn_detached(argv, work.logs / "extract.log")
        return 0, {"ok": True, "state": "started", "pid": pid, **info,
                   "next_action": f"rosettex status {q(work.root)} --json", "wait_seconds": 30}

    def on(done, total):
        beat(work, "extract", resume, f"MinerU: xong {done}/{total} nhóm trang", done, total)

    on(0, 0)
    try:
        summary = extract_core(work, cmd, on)
    except ExtractError as e:
        nxt = None if m["tier"] == "basic" else resume + " --tier basic"
        beat(work, "extract", nxt or resume, "lỗi", 0, 0, state="failed", error=str(e))
        return 2, {"ok": False, "error": str(e), "next_action": nxt}
    except Exception as e:  # lỗi bất ngờ: ghi lại để status không bắt agent chạy lại vô hạn
        err = f"{e!r} (chi tiết: {work.logs / 'extract.log'})"
        beat(work, "extract", None, "lỗi", 0, 0, state="failed", error=err)
        return 2, {"ok": False, "error": err, "next_action": None}
    beat(work, "extract", resume, "xong", 1, 1, state="done")
    return 0, {"ok": True, "state": "extracted", **info, **summary,
               "next_action": f"rosettex next {q(work.root)} --outline --json"}


# ---------------- status ----------------
def status(work: Work) -> dict:
    m = read_manifest(work)
    if m is None:
        return {"state": "missing", "message": "chưa có thư mục làm việc; chạy rosettex extract", "next_action": None}
    r = q(work.root)
    job = job_state(work)
    if job and job["state"] == "running":
        if not job["dead"]:
            return {"state": job["command"] + "_running", "step": job["step"], "done": job["done"],
                    "total": job["total"], "next_action": f"rosettex status {r} --json", "wait_seconds": 30}
        log = str(work.logs / f"{job['command']}.log")
        if job.get("retries", 0) >= 1:  # đã chạy lại một lần mà vẫn chết: dừng, không lặp vô hạn
            return {"state": job["command"] + "_dead", "error": f"tác vụ nền chết lần thứ hai; xem {log}",
                    "next_action": None}
        return {"state": job["command"] + "_dead", "message": "tác vụ nền đã dừng bất thường; chạy lại để làm tiếp",
                "next_action": job["resume"]}
    if job and job["state"] == "failed":
        out = {"state": job["command"] + "_failed", "error": job["error"], "next_action": job["resume"]}
        if job["command"] == "render" or m["tier"] == "basic" or not job["resume"]:
            out["next_action"] = None
            out["log"] = str(work.logs / f"{job['command']}.log")
        return out
    if not work.document.exists():
        return {"state": "not_extracted", "next_action": m["extract_cmd"]}
    if not work.terms.exists():
        return {"state": "need_terms", "next_action": f"rosettex next {r} --outline --json",
                "then": f"rosettex terms {r} --file <terms.json> --json"}
    ids = translate.batch_ids(work)
    done = [b for b in ids if work.translation_path(b, m["lang"]).exists()]
    if len(done) < len(ids):
        return {"state": "translating", "done": len(done), "total": len(ids),
                "next_action": f"rosettex next {r} --json"}
    v = read_json(work.validate)
    if not v or not v.get("ok") or v.get("mtime") != translate._translations_mtime(work, m["lang"]):
        return {"state": "need_validate", "next_action": f"rosettex validate {r} --json"}
    rep = read_json(work.report)
    if not rep or not Path(rep["output"]).exists():
        return {"state": "need_render", "next_action": f"rosettex render {r} --json"}
    return {"state": "done", "output": rep["output"], "report": str(work.report),
            "postcheck_ok": rep["postcheck"]["ok"], "next_action": None}


def cmd_status(a):
    return 0, status(Work(a.work))


# ---------------- dịch ----------------
def cmd_terms(a):
    res = translate.write_terms(Work(a.work), a.file)
    return (0 if res["ok"] else 1), res


def _batch_id(b):
    if b is not None and not re.fullmatch(r"b\d+", b):  # chặn path traversal
        raise UsageError(f"mã lô không hợp lệ: {b} (dạng b001)")
    return b


def cmd_next(a):
    w = Work(a.work)
    return 0, (translate.outline(w) if a.outline else translate.next_package(w, _batch_id(a.batch)))


def cmd_accept(a):
    w = Work(a.work)
    res = translate.accept(w, _batch_id(a.batch), a.file)
    res.setdefault("next_action", f"rosettex next {q(w.root)} --json")
    return (0 if res["ok"] else 1), res


def cmd_validate(a):
    w = Work(a.work)
    res = translate.validate(w)
    write_json(w.validate, {"ok": res["ok"], "mtime": res["mtime"]})
    res["next_action"] = (f"rosettex render {q(w.root)} --json" if res["ok"] else
                          f"sửa các id trong 'errors' rồi: rosettex accept {q(w.root)} --batch <bNNN> --file <file.json> --json")
    return (0 if res["ok"] else 1), res


# ---------------- render ----------------
def cmd_render(a):
    w = Work(a.work)
    m = translate.manifest(w)
    out = Path(a.out).expanduser().resolve() if a.out else Path(m["out"])
    v = translate.validate(w)
    if not v["ok"]:
        return 1, {"ok": False, "error": "validate chưa sạch", **v, "next_action": f"rosettex validate {q(w.root)} --json"}
    if out.exists() and not a.overwrite:
        raise UsageError(f"file kết quả đã tồn tại: {out}", next_action="hỏi người dùng; đồng ý thì chạy lại với --overwrite",
                         output_exists=True)
    if find_tex() is None:
        raise UsageError("không tìm thấy Tectonic hoặc XeLaTeX; xem README mục Cài đặt")
    resume = f"rosettex render {q(w.root)} --out {q(out)} --json" + (" --overwrite" if a.overwrite else "")
    if not a.foreground:
        if _running(job_state(w)):
            return 0, {"ok": True, "state": "already_running", "next_action": f"rosettex status {q(w.root)} --json"}
        argv = [sys.executable, "-m", "rosettex", "render", str(w.root), "--out", str(out), "--foreground", "--json"]
        beat(w, "render", resume, "khởi động", 0, 0)
        pid = spawn_detached(argv + (["--overwrite"] if a.overwrite else []), w.logs / "render.log")
        return 0, {"ok": True, "state": "started", "pid": pid, "next_action": f"rosettex status {q(w.root)} --json",
                   "wait_seconds": 30}
    beat(w, "render", resume, "bắt đầu", 0, 0)
    try:
        rep = render_core(w, out, overwrite=a.overwrite, on_beat=lambda s: beat(w, "render", resume, s, 0, 0))
    except RenderError as e:
        beat(w, "render", resume, "lỗi", 0, 0, state="failed", error=str(e))
        return 2, {"ok": False, "error": str(e), "next_action": f"rosettex status {q(w.root)} --json"}
    except Exception as e:
        err = f"{e!r} (chi tiết: {w.logs / 'render.log'})"
        beat(w, "render", resume, "lỗi", 0, 0, state="failed", error=err)
        return 2, {"ok": False, "error": err, "next_action": None}
    beat(w, "render", resume, "xong", 1, 1, state="done")
    return (0 if rep["postcheck"]["ok"] else 1), {"ok": rep["postcheck"]["ok"], **rep, "next_action": None}


# ---------------- khác ----------------
def cmd_clean(a):
    w = Work(a.work)
    if read_manifest(w, "kiểm tra lại đường dẫn <work>") is None and not (w.root.is_dir() and is_leftover(w.root)):
        raise UsageError(f"không phải thư mục làm việc rosettex (thiếu manifest.json): {w.root}",
                         next_action="kiểm tra lại đường dẫn <work>")
    try:
        # manifest.json xóa cuối cùng để lần clean lỗi còn chạy lại được
        for name in [n for n in OWNED if n != "manifest.json"]:
            p = w.root / name
            if p.is_dir() and not p.is_symlink():
                shutil.rmtree(p)
            else:  # symlink: chỉ gỡ chính liên kết, không đi theo
                p.unlink(missing_ok=True)
        for p in w.root.glob(".*.tmp"):  # file tạm của write_json bị bỏ dở
            if p.is_file() or p.is_symlink():
                p.unlink(missing_ok=True)
        w.manifest.unlink(missing_ok=True)
        kept = sorted(p.name for p in w.root.iterdir())
        if not kept:
            w.root.rmdir()
    except OSError as e:
        raise UsageError(f"không xóa hết được: {e}", next_action=f"gỡ vật cản rồi chạy lại: rosettex clean {q(w.root)} --json")
    return 0, {"ok": True, "removed": str(w.root) if not kept else False, "kept": kept, "next_action": None}


def cmd_guide(a):
    from .protocol import text
    t = text(a.lang)
    if a.json:
        return 0, {"guide": t}
    print(t)
    return 0, None


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="in JSON cho agent")
    p = argparse.ArgumentParser(prog="rosettex", description="Tách, kiểm tra và dựng lại bài báo PDF để agent dịch.")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("guide", parents=[common], help="in giao thức cho agent")
    s.add_argument("--lang", default="vi")
    s.set_defaults(func=cmd_guide)
    s = sub.add_parser("extract", parents=[common], help="tách nội dung PDF (chạy nền)")
    s.add_argument("pdf")
    s.add_argument("--lang", required=True)
    s.add_argument("--out", required=True, help="file .pdf kết quả hoặc thư mục")
    s.add_argument("-o", "--work", help="thư mục làm việc (mặc định <thư mục kết quả>/.rosettex/<tên>.<ngôn ngữ>)")
    s.add_argument("--tier", choices=["standard", "basic"])
    s.add_argument("--pages-per-group", type=int, default=5)
    s.add_argument("--foreground", action="store_true")
    s.set_defaults(func=cmd_extract)
    for name, func in (("status", cmd_status), ("validate", cmd_validate), ("clean", cmd_clean)):
        s = sub.add_parser(name, parents=[common])
        s.add_argument("work")
        s.set_defaults(func=func)
    s = sub.add_parser("terms", parents=[common])
    s.add_argument("work")
    s.add_argument("--file", required=True)
    s.set_defaults(func=cmd_terms)
    s = sub.add_parser("next", parents=[common])
    s.add_argument("work")
    s.add_argument("--batch")
    s.add_argument("--outline", action="store_true")
    s.set_defaults(func=cmd_next)
    s = sub.add_parser("accept", parents=[common])
    s.add_argument("work")
    s.add_argument("--batch", required=True)
    s.add_argument("--file", required=True)
    s.set_defaults(func=cmd_accept)
    s = sub.add_parser("render", parents=[common])
    s.add_argument("work")
    s.add_argument("--out")
    s.add_argument("--overwrite", action="store_true")
    s.add_argument("--foreground", action="store_true")
    s.set_defaults(func=cmd_render)
    return p


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass
    a = build_parser().parse_args(argv)
    try:
        code, obj = a.func(a)
    except UsageError as e:
        code, obj = 2, {"ok": False, "error": str(e), "next_action": e.next_action, **e.extra}
    except (ExtractError, RenderError) as e:
        code, obj = 2, {"ok": False, "error": str(e), "next_action": f"rosettex status {q(a.work)} --json" if getattr(a, "work", None) else None}
    if obj is not None:
        emit(obj, a.json)
    return code

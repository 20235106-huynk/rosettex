import argparse
import copy
import hashlib
from importlib import resources
import json
import re
import shutil
import sys
from pathlib import Path

from rosettex import skill as skill_mod
from rosettex import terms as terms_mod
from rosettex import workspace as ws
from rosettex.build.pipeline import BuildConfigError, build as run_build
from rosettex.build.repair_state import reset_repair_state
from rosettex.checks import check_all
from rosettex.jsonio import read_json, write_json
from rosettex.langdata import load_lang
from rosettex.postcheck import report as make_report
from rosettex.segment import extract_segments
from rosettex.arxiv import ArxivError
from rosettex.source import detect_lang, effective_dir, effective_hash, fetch, file_encodings, input_tree
from rosettex.unpack import UnpackError
from rosettex.textio import read_source

DEFAULT_BUDGET = 45.0
_ANS_ID = re.compile(r"^(s\d+)\s+", re.M)
_REPAIR_ACTION = (
    "Build has problems; inspect diagnosis and repair via one of the three channels: "
    "(1) translation: append a corrected line 'sNNNN <translation>' to out/*.txt; "
    "(2) fix: run 'rosettex fix <work> --file <source_file>' and edit work/fix/<source_file>; "
    "(3) build_settings: edit work/build.json (engine, preamble_extra, shim_off). "
    "Then re-run next_argv."
)
_PDF_REPAIR_ACTION = (
    "Some translated segments overflow their boxes; shorten the translations listed in diagnosis "
    "(keep the placeholders) by appending corrected lines 'sNNNN <translation>' to out/*.txt, "
    "then re-run next_argv."
)


STATES = ("done", "partial", "needs_repair", "not_started", "ask_user", "error")
_ASK_LANG = "Ask the user for the target language if unknown, put its code in place of <code>, then run next_argv."
_DEFAULT_ACTION = {
    "done": "Run next_argv.",
    "partial": "Budget used up at a safe point; run next_argv to continue where it stopped.",
    "needs_repair": "Fix the reported problems, then run next_argv.",
    "not_started": "Do the work next_action names, then run next_argv.",
    "ask_user": "Ask the user ask_user.question (offer ask_user.options), then run next_argv with the answer.",
    "error": "Fix the problem named in message, then run next_argv.",
}


def _emit(data: dict, code: int) -> int:
    """The §13 envelope for every command: ok, state, error (a code, when not ok), message, next_action,
    next_argv, ask_user ({question, options}, when the user must decide). Exit 0 ok, 1 check/build problems,
    2 usage/environment."""
    data.setdefault("ok", code == 0)
    if data.get("error") is None:
        data.pop("error", None)
    err = data.get("error")
    if err and not re.fullmatch(r"[a-z0-9_]+", str(err)):  # free text (toolchain): keep it as message
        data.setdefault("message", str(err))
        data["error"] = "environment" if code == 2 else "failed"
    st = data.get("state")
    if data.get("ask_user"):
        if isinstance(data["ask_user"], str):
            data["ask_user"] = {"question": data["ask_user"], "options": []}
        data["state"] = "ask_user"
    elif st not in STATES:
        data["state"] = {"complete": "done"}.get(st) or (
            "error" if code == 2 else "done" if data["ok"] else "needs_repair"
        )
        if st and not data["ok"]:
            data.setdefault("error", st)
    if not data["ok"]:
        data.setdefault("error", data["state"])
    data.setdefault("next_argv", [])
    if not data.get("next_action"):
        data["next_action"] = (
            "Nothing left to do." if data["ok"] and not data["next_argv"] else _DEFAULT_ACTION[data["state"]]
        )
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    print(json.dumps(data, ensure_ascii=False))
    return code


def _fail(error: str, message: str, next_argv: list[str] | None = None, code: int = 2, **extra) -> int:
    return _emit({"ok": False, "state": "error", "error": error, "message": message,
                  "next_argv": next_argv or [], **extra}, code)


def _missing_work(work: Path) -> int:
    return _fail("missing_work", f"missing project.json in {work}: not a rosettex work dir",
                 _argv("fetch", "<source>", "-o", work),
                 next_action="Check the work dir path; if the paper was never fetched, put the paper (folder, archive, "
                             "arXiv ID or link) in place of <source> and run next_argv.")


def _argv(*parts) -> list[str]:
    return ["rosettex", *[str(p) for p in parts]]


def _default_out(work: Path) -> Path:
    """A concrete --out for next_argv (no <file.pdf> placeholder): <work>/<main stem>.<lang>.pdf."""
    proj = read_json(work / "project.json")
    stem = Path(proj["main"]).stem if proj.get("main") else (proj.get("stem") or "orig")
    return work / f"{stem}.{proj.get('lang') or 'out'}.pdf"


def _sync(work: Path) -> dict:
    return ws.sync_effective(work)


def _with_sync(payload: dict, sync_info: dict) -> dict:
    if sync_info.get("resynced"):
        payload["resynced"] = True
        payload["translations_kept"] = sync_info["kept"]
        payload["translations_lost"] = sync_info["lost"]
    return payload


def _build_instructions(work: Path, tgt_lang: dict, sheets: list[str]) -> str:
    tmpl = resources.files("rosettex.data").joinpath("instructions.md").read_text(encoding="utf-8")
    sheet_list = ", ".join(sheets) if sheets else "read/01.md"
    return tmpl.format(lang=tgt_lang["code"], work=str(work), sheets=sheet_list).rstrip()


def cmd_fetch(a) -> int:
    try:
        proj = fetch(a.source, Path(a.o), a.main)
    except (ArxivError, UnpackError) as exc:
        action = {
            "network": "Network error; check the connection and re-run next_argv (downloads resume).",
            "no_source": "This paper has no TeX source on arXiv; rosettex v1 cannot translate it.",
        }.get(exc.code, "Fix the input and re-run.")
        nxt = _argv("fetch", a.source, "-o", a.o) if exc.code == "network" else []
        return _fail(exc.code, str(exc), nxt, next_action=action)
    if proj.get("route") == "pdf":
        return _emit(
            {
                "ok": True,
                "route": "pdf",
                "main": None,
                "stem": proj.get("stem"),
                "files": [],
                "arxiv": proj.get("arxiv"),
                "next_action": _ASK_LANG,
                "next_argv": _argv("prepare", a.o, "--lang", "<code>"),
            },
            0,
        )
    if not proj["main"]:
        return _emit(
            {
                "ok": False,
                "error": "ambiguous_main",
                "ask_user": {"question": "Which file is the paper's main TeX file?", "options": proj["candidates"]},
                "next_action": "Ask the user which candidate is the main file, put it in place of <candidate>, then run next_argv.",
                "next_argv": _argv("fetch", a.source, "-o", a.o, "--main", "<candidate>"),
            },
            2,
        )
    return _emit(
        {
            "ok": True,
            "route": "tex",
            "main": proj["main"],
            "files": proj["files"],
            "arxiv": proj.get("arxiv"),
            "engine_hint": proj["engine_hint"],
            "next_action": _ASK_LANG,
            "next_argv": _argv("prepare", a.o, "--lang", "<code>"),
        },
        0,
    )


def cmd_prepare(a) -> int:
    work = Path(a.work)
    if not (work / "project.json").exists():
        return _missing_work(work)
    proj = read_json(work / "project.json")
    from_arg = getattr(a, "from")
    try:
        tgt_lang = load_lang(a.lang)
        src_lang = load_lang(from_arg) if from_arg else None
    except ValueError as exc:
        return _fail("bad_lang", str(exc), _argv("prepare", work, "--lang", "<code>"),
                     next_action="Put a supported language code (see message) in place of <code>, then run next_argv.")
    if src_lang and tgt_lang["code"] == src_lang["code"]:
        return _fail("same_lang", f"source and target language are the same: {tgt_lang['code']}",
                     _argv("prepare", work, "--lang", "<code>"), next_action=_ASK_LANG)

    old_segs, old_tr = [], {}
    if (work / "segments.json").exists():
        old_segs = ws.load_segments(work)
        for sid, entry in ws.load_translations(work).items():
            if isinstance(entry, dict) and entry.get("text"):
                old_tr[sid] = entry["text"]
        if (work / "out").is_dir():
            ans_tr, _ = ws.read_answers(work, old_segs)
            old_tr.update(ans_tr)

    route = a.route or ("tex" if proj.get("main") else "pdf")
    if route == "pdf":
        orig_pdf = work / "orig.pdf"
        if not orig_pdf.is_file():
            return _fail(
                "no_pdf",
                f"no orig.pdf in workspace {work}",
                next_action="Fetch a PDF or an arXiv paper with PDF, or rerun prepare with --route tex.",
            )
        from rosettex.pdf import schema as pdf_schema  # noqa: PLC0415
        from rosettex.pdf.classify import classify as pdf_classify, to_segments as pdf_to_segments  # noqa: PLC0415
        from rosettex.pdf.extract import PdfError, extract as pdf_extract  # noqa: PLC0415
        from rosettex.pdf.pipeline import text_direction as pdf_text_direction  # noqa: PLC0415
        from rosettex.pdf.reconcile import is_suspect as pdf_is_suspect  # noqa: PLC0415

        try:
            layout, chars = pdf_extract(orig_pdf)
            layout = pdf_classify(layout, chars, tgt_lang["code"])
        except PdfError as exc:
            return _fail(exc.code, str(exc))

        src_dir = pdf_text_direction(layout)
        tgt_dir = (tgt_lang.get("profile") or {}).get("direction", "ltr")
        if src_dir != tgt_dir:
            return _fail(
                "direction_change",
                f"PDF route cannot change writing direction ({src_dir} → {tgt_dir}): "
                "translate from the paper's TeX source instead",
                next_action="Tell the user the PDF route cannot change writing direction; "
                "translate from the paper's TeX source instead.",
            )

        segs = pdf_to_segments(layout)
        for s in segs:
            if not s.hash:
                s.hash = hashlib.sha1(s.text.encode("utf-8")).hexdigest()[:6]

        det = detect_lang(None, [], segs)
        proj["source_lang"] = det["source_lang"]
        proj["source_lang_signals"] = det["source_lang_signals"]
        if src_lang is None:
            if not det["confident"]:
                write_json(work / "project.json", proj)
                return _emit(
                    {
                        "ok": False,
                        "error": "source_lang_unclear",
                        "source_lang": det["source_lang"],
                        "source_lang_signals": det["source_lang_signals"],
                        "ask_user": {"question": "Which language is the paper written in?", "options": det["options"]},
                        "next_action": "Ask the user which language the paper is written in, put its code in place of "
                        "<code>, then run next_argv.",
                        "next_argv": _argv("prepare", work, "--lang", tgt_lang["code"], "--from", "<code>"),
                    },
                    2,
                )
            src_lang = load_lang(det["source_lang"])
            if tgt_lang["code"] == src_lang["code"]:
                return _fail(
                    "same_lang",
                    f"the paper is already in {tgt_lang['code']} (detected: {'; '.join(det['source_lang_signals'])})",
                    _argv("prepare", work, "--lang", "<code>"),
                    next_action=_ASK_LANG,
                )

        (work / "pdf").mkdir(parents=True, exist_ok=True)
        (work / "pdf" / "layout_base.json").unlink(missing_ok=True)
        reset_repair_state(work)
        pdf_schema.save(layout, work / "pdf" / "layout.json")
        block_by_id = {b["id"]: b for b in layout.get("blocks") or []}
        pdf_meta: dict[str, dict] = {}
        for seg in layout.get("segments") or []:
            blks = seg.get("blocks") or []
            first_pg = block_by_id[blks[0]]["page"] if blks and blks[0] in block_by_id else 0
            suspects = {
                code
                for code, info in (seg.get("ph") or {}).items()
                if code.startswith("m") and pdf_is_suspect(info.get("raw", ""), info.get("pw"))
            }
            pdf_meta[seg["id"]] = {
                "page": first_pg + 1,
                "budget": int(seg.get("budget") or 0),
                "suspects": suspects,
            }

        ws.save_segments(work, segs)
        tr = ws.carry_over(old_segs, old_tr, segs) if old_segs else {}
        if old_segs and ((work / "out").is_dir() or tr):
            ws.archive_and_write_carried(work, segs, tr)
            ws.save_translations(work, segs, tr)

        sheets = ws.write_sheets(work, segs, tgt_lang["code"], pdf_meta=pdf_meta)
        terms_mod.write_terms(work, terms_mod.candidates(segs, src_lang))

        proj.update({"route": "pdf", "lang": tgt_lang["code"], "from": src_lang["code"]})
        proj.setdefault("stem", Path(proj["main"]).stem if proj.get("main") else "orig")
        write_json(work / "project.json", proj)
        instructions = _build_instructions(work, tgt_lang, sheets)
        return _emit(
            {
                "ok": True,
                "route": "pdf",
                "segments": len(segs),
                "sheets": sheets,
                "unknown": 0,
                "instructions": instructions,
                "next_action": "Translate following instructions (answers into out/*.txt), then run next_argv.",
                "next_argv": _argv("check", work),
            },
            0,
        )

    if not proj.get("main"):
        return _fail(
            "no_source",
            "no TeX main file in workspace; use --route pdf or fetch a TeX source",
            _argv("prepare", work, "--lang", tgt_lang["code"], "--route", "pdf"),
        )

    eff = effective_dir(work)
    if re.search(r"^[^%\n]*\\documentstyle\b", read_source(eff / proj["main"])[0], re.M):
        return _fail("latex209_unsupported",
                     f"{proj['main']} is a LaTeX 2.09 source (\\documentstyle): current TeX cannot build it, so "
                     "rosettex v1 cannot translate it.",
                     next_action="Tell the user this paper uses LaTeX 2.09 and cannot be translated by rosettex.")
    eff_files = input_tree(eff, proj["main"])
    proj["files"] = eff_files
    proj["encodings"] = file_encodings(eff, eff_files, proj["main"])
    proj["effective_hash"] = effective_hash(work, proj.get("source_hash"))
    segs, unknown = extract_segments(eff, {**proj, "lang": tgt_lang["code"]})
    det = detect_lang(eff, eff_files, segs)
    proj["source_lang"] = det["source_lang"]
    proj["source_lang_signals"] = det["source_lang_signals"]
    if src_lang is None:
        if not det["confident"]:
            write_json(work / "project.json", proj)
            return _emit(
                {
                    "ok": False,
                    "error": "source_lang_unclear",
                    "source_lang": det["source_lang"],
                    "source_lang_signals": det["source_lang_signals"],
                    "ask_user": {"question": "Which language is the paper written in?", "options": det["options"]},
                    "next_action": "Ask the user which language the paper is written in, put its code in place of "
                                   "<code>, then run next_argv.",
                    "next_argv": _argv("prepare", work, "--lang", tgt_lang["code"], "--from", "<code>"),
                },
                2,
            )
        src_lang = load_lang(det["source_lang"])
        if tgt_lang["code"] == src_lang["code"]:
            return _fail("same_lang", f"the paper is already in {tgt_lang['code']} (detected: "
                         f"{'; '.join(det['source_lang_signals'])})",
                         _argv("prepare", work, "--lang", "<code>"), next_action=_ASK_LANG)
    ws.save_segments(work, segs)

    tr = ws.carry_over(old_segs, old_tr, segs) if old_segs else {}
    if old_segs and ((work / "out").is_dir() or tr):
        ws.archive_and_write_carried(work, segs, tr)
        ws.save_translations(work, segs, tr)

    sheets = ws.write_sheets(work, segs, tgt_lang["code"])
    terms_mod.write_terms(work, terms_mod.candidates(segs, src_lang))
    ws.write_unknown(work, unknown)

    proj.update({"route": "tex", "lang": tgt_lang["code"], "from": src_lang["code"]})
    write_json(work / "project.json", proj)
    instructions = _build_instructions(work, tgt_lang, sheets)
    return _emit(
        {
            "ok": True,
            "route": "tex",
            "segments": len(segs),
            "sheets": sheets,
            "unknown": len(unknown),
            "instructions": instructions,
            "next_action": "Translate following instructions (answers into out/*.txt), then run next_argv.",
            "next_argv": _argv("check", work),
        },
        0,
    )


def _kept_terms(terms: list[tuple[str, str]]) -> set[str]:
    return {term for term, target in terms if target == "=" or target == term}


def _check(work: Path, only_file: str | None):
    proj = read_json(work / "project.json")
    all_segs = ws.load_segments(work)
    tr, parse_errors = ws.read_answers(work, all_segs)
    prev = ws.load_translations(work)
    hashes = {
        sid: entry["hash"]
        for sid, entry in prev.items()
        if isinstance(entry, dict) and entry.get("hash") and entry.get("text") == tr.get(sid)
    }
    segs = all_segs
    if only_file:
        target = Path(only_file)
        if not target.is_absolute():
            target = work / target
        if not target.is_file():
            raise FileNotFoundError(f"answer file not found: {target}")
        file_ids = set(_ANS_ID.findall(target.read_text(encoding="utf-8-sig")))
        segs = [s for s in all_segs if s.id in file_ids]
        rel = f"out/{target.name}"
        parse_errors = [e for e in parse_errors if e.get("file") == rel]
    terms = ws.read_terms(work)
    budgets: dict[str, int] | None = None
    d14: dict | None = None
    check_segs = segs
    check_tr = tr
    if proj.get("route") == "pdf":
        base_path = work / "pdf" / "layout_base.json"
        layout_path = base_path if base_path.is_file() else (work / "pdf" / "layout.json")
        if layout_path.is_file():
            from rosettex.pdf.reconcile import reconcile as d14_reconcile  # noqa: PLC0415

            layout = read_json(layout_path)
            layout_segs = {s["id"]: s for s in layout.get("segments") or []}
            budgets = {s["id"]: int(s["budget"]) for s in layout.get("segments") or [] if s.get("budget")}
            kept = _kept_terms(terms)
            rec_segs = []
            rec_tr = dict(tr)
            wrapped = 0
            unwrapped = 0
            for s in segs:
                ans = tr.get(s.id, "")
                lseg = layout_segs.get(s.id)
                if ans.strip() and lseg is not None:
                    recon = d14_reconcile(lseg, ans, kept, layout)
                    wrapped += len(recon.wraps)
                    unwrapped += len(recon.unwraps)
                    sc = copy.copy(s)
                    sc.text = recon.text
                    rec_segs.append(sc)
                    rec_tr[s.id] = recon.translation
                else:
                    rec_segs.append(s)
            check_segs = rec_segs
            check_tr = rec_tr
            d14 = {
                "wrapped": wrapped,
                "unwrapped": unwrapped,
                "summary": f"math: +{wrapped} wrapped, −{unwrapped} unwrapped",
            }
    issues = check_all(
        check_segs, check_tr, hashes, load_lang(proj["from"]), load_lang(proj["lang"]), terms, budgets=budgets
    )
    ws.save_translations(work, all_segs, tr)
    return proj, all_segs, segs, tr, issues, parse_errors, d14


def cmd_check(a) -> int:
    work = Path(a.work)
    if not (work / "project.json").exists():
        return _missing_work(work)
    if not (work / "segments.json").exists():
        return _fail("not_prepared", f"workspace not prepared: {work}", _argv("prepare", work, "--lang", "<code>"),
                     next_action=_ASK_LANG)
    sync_info = _sync(work)
    try:
        _, all_segs, segs, tr, issues, parse_errors, d14 = _check(work, a.file)
    except FileNotFoundError as exc:
        return _fail("not_found", str(exc), _argv("check", work))

    translated_count = sum(bool(tr.get(s.id)) for s in segs)
    if len(segs) > 0 and translated_count == 0:
        payload = _with_sync(
            {
                "ok": False,
                "state": "not_started",
                "total": len(segs),
                "next_action": (
                    "write answers for read/01.md into out/01.txt "
                    "(one line per segment: sNNNN <translation>), then run rosettex check"
                ),
                "next_argv": _argv("check", work),
            },
            sync_info,
        )
        return _emit(payload, 1)

    seg_by_id = {s.id: s for s in all_segs}
    missing_ids = [i.segment for i in issues if i.code == "missing"]
    missing_count = len(missing_ids)

    errs: list[dict] = []
    warns: list[dict] = []
    for i in issues:
        if i.code == "missing":
            continue
        s = seg_by_id.get(i.segment)
        clean_src = re.sub(r"\s+", " ", s.text).strip() if s else ""
        src_line = f"{s.id} {clean_src}" if s else i.segment
        item = {
            "id": i.segment,
            "code": i.code,
            "message": i.message,
            "fix_hint": src_line,
        }
        if i.level == "warning":
            warns.append(item)
        else:
            errs.append(item)
    for pe in parse_errors:
        errs.append(
            {
                "id": f"{pe['file']}:{pe['line']}",
                "code": pe["code"],
                "message": pe["message"],
                "fix_hint": f"fix line {pe['line']} in {pe['file']}",
            }
        )

    ok = not errs and missing_count == 0
    payload = _with_sync(
        {
            "ok": ok,
            "state": "done" if ok else "needs_repair",
            "error": None if ok else "check_failed",
            "next_action": "Run next_argv (change its --out if the user wants the PDF elsewhere)." if ok else (
                "Append corrected lines (sNNNN <translation>) to out/*.txt for every error and missing "
                "segment, then run next_argv."
            ),
            "errors": errs,
            "warnings": warns,
            "missing": missing_ids[:20],
            "missing_count": missing_count,
            "translated": len(segs) - missing_count,
            "total": len(segs),
            "next_argv": _argv("build", work, "--out", _default_out(work)) if ok else _argv("check", work),
        },
        sync_info,
    )
    if d14 is not None:
        payload["d14"] = d14
    return _emit(payload, 0 if ok else 1)


def cmd_review(a) -> int:
    work = Path(a.work)
    if not (work / "project.json").exists():
        return _missing_work(work)
    if not (work / "segments.json").exists():
        return _fail("not_prepared", f"workspace not prepared: {work}", _argv("prepare", work, "--lang", "<code>"),
                     next_action=_ASK_LANG)
    sync_info = _sync(work)
    segs = ws.load_segments(work)
    tr, _ = ws.read_answers(work, segs)
    out_path = ws.write_review(work, segs, tr, Path(a.out) if a.out else None)
    payload = _with_sync(
        {
            "ok": True,
            "path": str(out_path),
            "segments": len(segs),
            "translated": sum(bool(tr.get(s.id)) for s in segs),
            "next_argv": [],
        },
        sync_info,
    )
    return _emit(payload, 0)


def _norm_rel(raw: str) -> str | None:
    cleaned = raw.strip().replace("\\", "/")
    while cleaned.startswith("./"):
        cleaned = cleaned[2:]
    if not cleaned or cleaned.startswith("/") or ".." in Path(cleaned).parts:
        return None
    if re.match(r"^[A-Za-z]:", cleaned):
        return None
    return cleaned


def _fix_next_argv(work: Path) -> list[str]:
    rep_path = work / "report.json"
    if rep_path.is_file():
        try:
            rep = read_json(rep_path)
            if rep.get("state") == "needs_repair" and isinstance(rep.get("next_argv"), list):
                return rep["next_argv"]
        except Exception:
            pass
    return _argv("build", work, "--out", _default_out(work))


def cmd_fix(a) -> int:
    work = Path(a.work)
    if not (work / "project.json").exists():
        return _missing_work(work)
    if read_json(work / "project.json").get("route") == "pdf":
        return _fail(
            "not_supported",
            "fix is not supported on the PDF route (there is no TeX source to overlay); "
            "fix translations by appending corrected 'sNNNN <translation>' lines to out/*.txt",
            _argv("check", work),
            next_action="Append corrected 'sNNNN <translation>' lines to out/*.txt, then run next_argv.",
        )
    if a.drop:
        rel = _norm_rel(a.drop)
        if not rel:
            return _fail("bad_path", f"invalid path: {a.drop}")
        fix_root = (work / "fix").resolve()
        target = (work / "fix" / rel).resolve()
        if not target.is_file() and rel.startswith("fix/"):
            rel = rel[4:]
            target = (work / "fix" / rel).resolve()
        if not target.is_relative_to(fix_root):
            return _fail("bad_path", f"path escapes fix dir: {a.drop}")
        if not target.is_file():
            return _fail("not_found", f"overlay file not found: fix/{rel}")
        target.unlink()
        parent = target.parent
        while parent != fix_root and parent.is_dir():
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent
        return _emit(
            {
                "ok": True,
                "path": f"fix/{rel}",
                "dropped": True,
                "next_argv": _fix_next_argv(work),
            },
            0,
        )

    rel = _norm_rel(a.file)
    if not rel:
        return _fail("bad_path", f"invalid path: {a.file}")
    src_root = (work / "src").resolve()
    src_file = (work / "src" / rel).resolve()
    if not src_file.is_relative_to(src_root) or not src_file.is_file():
        return _fail("not_found", f"not a file of the source tree: {a.file}")
    fix_dir = work / "fix"
    fix_dir.mkdir(parents=True, exist_ok=True)
    fix_root = fix_dir.resolve()
    dst = (fix_dir / rel).resolve()
    if not dst.is_relative_to(fix_root):
        return _fail("bad_path", f"path escapes fix dir: {a.file}")
    if not dst.exists():
        eff_file = effective_dir(work) / rel
        source_copy = eff_file if eff_file.is_file() else src_file
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_copy, dst)
    return _emit(
        {
            "ok": True,
            "path": f"fix/{rel}",
            "next_action": f"Edit {work / 'fix' / rel} (the overlay of the source file), then run next_argv.",
            "next_argv": _fix_next_argv(work),
        },
        0,
    )


def _build_argv(a, force: bool = False) -> list[str]:
    """The same build command (for partial / needs_repair reruns)."""
    argv = _argv("build", a.work, "--out", a.out)
    if a.force or force:
        argv.append("--force")
    if a.no_agent:
        argv.append("--no-agent")
    if a.budget != DEFAULT_BUDGET:
        argv += ["--budget", f"{a.budget:g}"]
    return argv


def cmd_build(a) -> int:
    work, out = Path(a.work), Path(a.out)
    if not (work / "project.json").exists():
        return _missing_work(work)
    if out.exists() and not a.force:
        return _emit(
            {
                "ok": False,
                "error": "output_exists",
                "message": f"{out} exists",
                "ask_user": {"question": f"{out} already exists. Overwrite it?",
                             "options": ["overwrite", "choose another output path"]},
                "next_action": "Ask the user; on 'overwrite' run next_argv, else rerun build with another --out.",
                "next_argv": _build_argv(a, force=True),
            },
            2,
        )
    if not (work / "segments.json").exists():
        return _fail("not_prepared", f"workspace not prepared: {work}", _argv("prepare", work, "--lang", "<code>"),
                     next_action=_ASK_LANG)
    sync_info = _sync(work)
    proj, _, _, tr, issues, _, _ = _check(work, None)
    bad = {i.segment for i in issues if i.code != "missing" and i.level == "error"}  # warnings never keep source
    untranslated = {i.segment for i in issues if i.code == "missing"}
    usable = {k: v for k, v in tr.items() if k not in bad}
    if proj.get("route") == "pdf":
        from rosettex.pdf.extract import PdfError  # noqa: PLC0415
        from rosettex.pdf.pipeline import build as pdf_build  # noqa: PLC0415
        from rosettex.pdf.reconcile import norm_marks  # noqa: PLC0415

        usable_pdf = {k: norm_marks(v) for k, v in usable.items()}
        kept_terms = _kept_terms(ws.read_terms(work))
        try:
            pdf_rep = pdf_build(
                work,
                proj["lang"],
                usable_pdf,
                fallback=a.no_agent,
                agent=not a.no_agent,
                kept_terms=kept_terms,
            )
        except PdfError as exc:
            return _fail(exc.code, str(exc))
        except Exception as exc:  # noqa: BLE001
            return _fail("build_failed", f"{type(exc).__name__}: {exc}", _argv("status", work), code=1)

        kept_reasons: dict[str, str] = {}
        for sid in sorted(bad):
            kept_reasons[sid] = "check_error"
        for sid in sorted(untranslated):
            kept_reasons[sid] = "untranslated"
        for sid, reason in sorted((pdf_rep.get("demoted") or {}).items()):
            kept_reasons.setdefault(sid, reason)
        warnings = list(pdf_rep.get("warnings") or [])

        if pdf_rep.get("state") == "needs_repair":
            build_argv = _build_argv(a)
            rep = {
                "ok": False,
                "route": "pdf",
                "state": "needs_repair",
                "engine": "lualatex",
                "pdf": None,
                "problems": [],
                "warnings": warnings,
                "kept_source": sorted(kept_reasons),
                "kept_reasons": kept_reasons,
                "diagnosis": pdf_rep.get("diagnosis", []),
                "rounds_left": pdf_rep.get("rounds_left", 0),
                "repairs": pdf_rep.get("repairs", []),
                "next_action": _PDF_REPAIR_ACTION,
                "next_argv": build_argv,
                "pdf_report": pdf_rep,
            }
            write_json(work / "report.json", rep)
            payload = {
                "ok": False,
                "route": "pdf",
                "state": "needs_repair",
                "error": "needs_repair",
                "pdf": None,
                "engine": "lualatex",
                "problems": rep["problems"],
                "warnings": rep["warnings"],
                "kept_source": rep["kept_source"],
                "diagnosis": rep["diagnosis"],
                "rounds_left": rep["rounds_left"],
                "repairs": rep["repairs"],
                "d14": pdf_rep.get("d14", {}),
                "overflow": pdf_rep.get("overflow", []),
                "seconds": pdf_rep.get("seconds", {}),
                "next_action": _PDF_REPAIR_ACTION,
                "next_argv": build_argv,
            }
            return _emit(payload, 1)

        out_pdf = work / pdf_rep["out"] if pdf_rep.get("out") else work / "out.pdf"
        built = out_pdf.is_file()
        if built:
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(out_pdf, out)

        if pdf_rep.get("overflow"):
            warnings.append(f"{len(pdf_rep['overflow'])} segment(s) overflowed after fallback")
        rep = {
            "ok": built,
            "route": "pdf",
            "engine": "lualatex",
            "pdf": str(out) if built else None,
            "problems": [],
            "warnings": warnings,
            "kept_source": sorted(kept_reasons),
            "kept_reasons": kept_reasons,
            "repairs": pdf_rep.get("repairs", []),
            "pdf_report": pdf_rep,
        }
        write_json(work / "report.json", rep)
        payload = {
            "ok": rep["ok"],
            "route": "pdf",
            "state": "done" if built else "error",
            "error": None if rep["ok"] else "build_failed",
            "next_action": (
                "Report the PDF path to the user; run next_argv for the final status."
                if rep["ok"]
                else "No PDF; see problems."
            ),
            "pdf": str(out) if built else None,
            "engine": "lualatex",
            "problems": rep["problems"],
            "warnings": rep["warnings"],
            "kept_source": rep["kept_source"],
            "d14": pdf_rep.get("d14", {}),
            "overflow": pdf_rep.get("overflow", []),
            "fallback": pdf_rep.get("fallback", []),
            "repairs": rep["repairs"],
            "links": pdf_rep.get("links", {}),
            "seconds": pdf_rep.get("seconds", {}),
            "next_argv": _argv("status", work),
        }
        return _emit(payload, 0 if rep["ok"] else 1)
    try:
        res = run_build(work, proj["lang"], usable, agent=not a.no_agent, budget=a.budget)
    except BuildConfigError as exc:
        return _fail("build_config", str(exc), _build_argv(a),
                     next_action=f"Fix {work / 'build.json'} as message says, then run next_argv.")
    if res.get("state") == "partial":
        st_path = work / "build_state.json"  # status reruns exactly this argv (the --out is known only here)
        write_json(st_path, {**read_json(st_path), "argv": _build_argv(a)})
        return _emit(
            _with_sync({"ok": False, "state": "partial", "error": "budget", "pdf": None,
                        "next_argv": _build_argv(a)}, sync_info),
            1,
        )

    rep = make_report(res, load_lang(proj["lang"]))
    kept_reasons: dict[str, str] = {}
    for sid in sorted(bad):
        kept_reasons[sid] = "check_error"
    for sid in sorted(untranslated):
        kept_reasons[sid] = "untranslated"
    for sid in res["culprits"]:
        kept_reasons[sid] = "culprit"
    for sid in res.get("bisect_cap_kept", []):
        kept_reasons[sid] = "bisect_cap"
    rep["kept_source"] = sorted(kept_reasons)
    rep["kept_reasons"] = kept_reasons
    rep["culprits"] = res["culprits"]
    rep["bisect_compiles"] = res.get("bisect_compiles", 0)
    rep["bisect_capped"] = res.get("bisect_capped", False)
    rep["baseline_failed"] = res.get("baseline_failed", False)
    rep["repairs"] = res.get("repairs", [])
    rep["installed"] = res.get("installed", [])
    rep["install_failed"] = res.get("install_failed", [])
    if res.get("diagnosis"):
        rep["diagnosis"] = res["diagnosis"]
    if rep["baseline_failed"]:
        rep["problems"].append({"code": "baseline_failed", "message": "baseline (untranslated + shim) failed"})

    if res.get("guard_refused"):
        guard_errs = res.get("guard_errors", [])
        rep["ok"] = False
        rep["state"] = "guard_refused"
        rep["guard_refused"] = True
        rep["guard_errors"] = guard_errs
        rep["problems"] = [{"code": "guard_refused", "message": "; ".join(guard_errs)}]
        write_json(work / "report.json", rep)
        payload = _with_sync(
            {
                "ok": False,
                "state": "error",
                "error": "guard_refused",
                "pdf": None,
                "engine": rep["engine"],
                "problems": rep["problems"],
                "warnings": rep["warnings"],
                "kept_source": rep["kept_source"],
                "guard_errors": guard_errs,
                "repairs": rep["repairs"],
                "next_action": (
                    "Build refused by security read guard; remove forbidden paths or Lua execution "
                    "from overlays or build.json preamble_extra."
                ),
                "next_argv": _argv("status", work),
            },
            sync_info,
        )
        return _emit(payload, 1)

    if res.get("state") == "needs_repair":
        build_argv = _build_argv(a)
        rep["ok"] = False
        rep["state"] = "needs_repair"
        rep["pdf"] = None
        rep["rounds_left"] = res["rounds_left"]
        rep["diagnosis"] = res.get("diagnosis", [])
        rep["next_action"] = _REPAIR_ACTION
        rep["next_argv"] = build_argv
        write_json(work / "report.json", rep)
        payload = _with_sync(
            {
                "ok": False,
                "state": "needs_repair",
                "error": "needs_repair",
                "pdf": None,
                "engine": rep["engine"],
                "problems": rep["problems"],
                "warnings": rep["warnings"],
                "kept_source": rep["kept_source"],
                "diagnosis": rep["diagnosis"],
                "rounds_left": res["rounds_left"],
                "repairs": rep["repairs"],
                "next_action": _REPAIR_ACTION,
                "next_argv": build_argv,
            },
            sync_info,
        )
        return _emit(payload, 1)

    write_json(work / "report.json", rep)
    if res["pdf"]:
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(res["pdf"], out)
    payload = _with_sync(
        {
            "ok": rep["ok"],
            "state": "done" if res["pdf"] else "error",
            "error": None if rep["ok"] else ("build_problems" if res["pdf"] else "build_failed"),
            "next_action": "Report the PDF path to the user; run next_argv for the final status." if rep["ok"] else (
                "The PDF was built with problems (see problems, diagnosis, kept_source); tell the user, "
                "then run next_argv." if res["pdf"] else "No PDF; see problems and diagnosis."
            ),
            "pdf": str(out) if res["pdf"] else None,
            "engine": rep["engine"],
            "problems": rep["problems"],
            "warnings": rep["warnings"],
            "kept_source": rep["kept_source"],
            "repairs": rep["repairs"],
            "next_argv": _argv("status", work),
        },
        sync_info,
    )
    if rep.get("diagnosis"):
        payload["diagnosis"] = rep["diagnosis"]
    return _emit(payload, 0 if rep["ok"] else 1)


def cmd_status(a) -> int:
    work = Path(a.work)
    if not (work / "project.json").exists():
        return _missing_work(work)
    proj = read_json(work / "project.json")
    route = proj.get("route") or ("tex" if proj.get("main") else "pdf")
    sync_info = _sync(work)
    if not (work / "segments.json").exists():
        return _emit(
            {
                "ok": True,
                "route": route,
                "state": "not_started",
                "next_action": _ASK_LANG,
                "stage": "fetched",
                "translated": 0,
                "total": 0,
                "next_argv": _argv("prepare", work, "--lang", "<code>"),
            },
            0,
        )
    segs = ws.load_segments(work)
    tr = ws.read_answers(work, segs)[0] if (work / "out").is_dir() else {}
    done = sum(bool(tr.get(s.id)) for s in segs)
    rep_path = work / "report.json"
    if (work / "build_state.json").exists():  # a budget-stopped build records its argv at the stop
        stage, nxt = "building", (read_json(work / "build_state.json").get("argv")
                                or _argv("build", work, "--out", _default_out(work)))
    elif rep_path.exists():
        rep = read_json(rep_path)
        if rep.get("state") == "needs_repair":
            stage = "needs_repair"
            nxt = rep.get("next_argv") or _argv("build", work, "--out", _default_out(work))
        else:
            stage, nxt = "built", []
    elif done == len(segs):
        stage, nxt = "checked", _argv("check", work)
    else:
        stage, nxt = ("translating" if done else "prepared"), _argv("check", work)
    state = {"built": "done", "needs_repair": "needs_repair", "prepared": "not_started"}.get(stage, "partial")
    payload = _with_sync(
        {
            "ok": True,
            "route": route,
            "state": state,
            "stage": stage,
            "translated": done,
            "total": len(segs),
            "next_argv": nxt,
        },
        sync_info,
    )
    return _emit(payload, 0)


def cmd_setup(a) -> int:
    from rosettex import toolchain

    data, code = toolchain.setup(a.yes, a.budget, a.tex)
    if isinstance(data.get("ask_user"), str):
        data["ask_user"] = {"question": data["ask_user"],
                            "options": ["install the private TeX Live", "use the system TeX Live"]}
        data["error"] = "confirm_install"
        data["next_action"] = ("Ask the user; on install run next_argv, on system TeX run "
                               "'rosettex setup --tex system'.")
    elif data.get("state") == "partial" and code == 2:  # a failed step (resumable): an environment error
        data["state"] = "error"
        data["message"], data["error"] = data.get("error"), "setup_failed"
    elif data.get("state") == "partial":
        data["error"] = "budget"
    return _emit(data, code)


def cmd_doctor(a) -> int:
    from rosettex import toolchain

    data, code = toolchain.doctor(a.force)
    if not data["ok"] and code == 1:
        data.update(state="error", error="doctor_failed")
    return _emit(data, code)


def cmd_guide(a) -> int:
    text = skill_mod.render_guide()
    if a.json:
        return _emit({"ok": True, "state": "done", "text": text, "next_argv": []}, 0)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    print(text)
    return 0


def cmd_install_skill(a) -> int:
    results = skill_mod.install_skill(remove=a.remove, force=a.force)
    if a.json:
        return _emit({"ok": True, "state": "done", "locations": results, "next_argv": []}, 0)
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    for item in results:
        status = item["status"]
        loc = item["path"]
        reason = item.get("reason")
        if reason:
            print(f"{loc}: {status} ({reason})")
        else:
            print(f"{loc}: {status}")
    return 0


class _UsageError(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message):  # subparsers inherit this class
        raise _UsageError(f"{self.prog}: {message}")


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    try:
        return _main(argv)
    except _UsageError as exc:
        if "--json" in argv:
            return _fail("usage", str(exc), _argv("guide"),
                         next_action="Fix the command line (rosettex <command> --help), see next_argv for the protocol.")
        print(f"usage error: {exc}", file=sys.stderr)
        return 2


def _main(argv: list[str]) -> int:
    p = _Parser(prog="rosettex")
    sub = p.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch")
    f.add_argument("source")
    f.add_argument("-o", required=True)
    f.add_argument("--main", help="main .tex file (relative to the source) when detection is ambiguous")
    pr = sub.add_parser("prepare")
    pr.add_argument("work")
    pr.add_argument("--lang", required=True)
    pr.add_argument("--from", default=None, help="source language (default: detected from the source)")
    pr.add_argument("--route", choices=["tex", "pdf"], default=None, help="translation route (default: auto)")
    c = sub.add_parser("check")
    c.add_argument("work")
    c.add_argument("--file")
    rv = sub.add_parser("review")
    rv.add_argument("work")
    rv.add_argument("--out")
    fx = sub.add_parser("fix")
    fx.add_argument("work")
    fx_grp = fx.add_mutually_exclusive_group(required=True)
    fx_grp.add_argument("--file")
    fx_grp.add_argument("--drop")
    b = sub.add_parser("build")
    b.add_argument("work")
    b.add_argument("--out", required=True)
    b.add_argument("--force", action="store_true")
    b.add_argument("--no-agent", action="store_true")
    b.add_argument("--budget", type=float, default=DEFAULT_BUDGET,
                   help="seconds; stop at a safe point and return state partial (default 45)")
    s = sub.add_parser("status")
    s.add_argument("work")
    g = sub.add_parser("guide")
    ins = sub.add_parser("install-skill")
    ins.add_argument("--remove", action="store_true")
    ins.add_argument("--force", action="store_true")
    su = sub.add_parser("setup")
    su.add_argument("--yes", action="store_true")
    su.add_argument("--budget", type=float)
    su.add_argument("--tex", choices=["private", "system"], default="private")
    dr = sub.add_parser("doctor")
    dr.add_argument("--force", action="store_true")
    for sp in (f, pr, c, rv, fx, b, s, g, ins, su, dr):
        sp.add_argument("--json", action="store_true")
    a = p.parse_args(argv)
    return {
        "fetch": cmd_fetch,
        "prepare": cmd_prepare,
        "check": cmd_check,
        "review": cmd_review,
        "fix": cmd_fix,
        "build": cmd_build,
        "status": cmd_status,
        "guide": cmd_guide,
        "install-skill": cmd_install_skill,
        "setup": cmd_setup,
        "doctor": cmd_doctor,
    }[a.cmd](a)

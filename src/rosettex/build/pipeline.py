import hashlib
import json
import shutil
import time
from dataclasses import asdict
from importlib import resources
from pathlib import Path

from rosettex.assemble import assemble_tree
from rosettex.build.bisect import find_culprits
from rosettex.build.diagnose import diagnose
from rosettex.build.driver import BUDGET, INSTALLED, SEARCHED, BudgetStop, BuildResult, ENGINES, run_engine
from rosettex.build.guard import scan as guard_scan
from rosettex.build.preamble import (
    baseline_preamble,
    fallback_bundled_fonts,
    fallback_preamble_lines,
    marks_sty,
    fallback_sty,
    insert_preamble,
    language_preamble,
    needed_fallback_fonts,
    preamble_prelude,
    source_preamble,
    staged_fonts,
    tu_fd_files,
)
from rosettex.build.repair_state import (
    MAX_REPAIR_ROUNDS,
    load_repair_state as _load_repair_state,
    reset_repair_state as _reset_repair_state,
    save_repair_state as _save_repair_state,
)
from rosettex.build.shim import MINTED_STY, RULES as SHIM_RULES, apply_shim, neutralise, source_ladder, source_profile
from rosettex.build.texbin import find_tex
from rosettex.jsonio import read_json, write_json
from rosettex.langdata import load_lang
from rosettex.postcheck import compare, page_ratio_for, parse_log, stats_of
from rosettex.segment import Segment
from rosettex.source import effective_dir, overlay_files, overlay_hash
from rosettex.textio import read_source, write_text
from rosettex.workspace import load_segments

BISECT_CAP = 12
_BUILD_JSON_KEYS = ("engine", "preamble_extra", "shim_off")


class _BisectCapReached(Exception):
    pass


class BuildConfigError(ValueError):
    pass


def ladder(lang: dict) -> list[str]:
    return ["lua", "xe", "pdf"] if "pdflatex" in lang else ["lua", "xe"]


def load_build_config(work: Path, lang: dict | None = None) -> dict:
    path = Path(work) / "build.json"
    if not path.is_file():
        return {"engine": None, "preamble_extra": [], "shim_off": []}
    try:
        raw = read_json(path)
    except json.JSONDecodeError as exc:
        raise BuildConfigError(f"invalid JSON in build.json: {exc}") from exc
    if not isinstance(raw, dict):
        raise BuildConfigError("build.json must be a JSON object")
    unknown = sorted(set(raw) - set(_BUILD_JSON_KEYS))
    if unknown:
        raise BuildConfigError(
            f"unknown key(s) in build.json: {unknown}; allowed: {list(_BUILD_JSON_KEYS)}"
        )
    engine = raw.get("engine")
    if engine is not None:
        if not isinstance(engine, str) or engine not in ENGINES:
            raise BuildConfigError(
                f"invalid engine {engine!r} in build.json; allowed: {sorted(ENGINES)} or null"
            )
        if lang is not None and engine == "pdf" and "pdflatex" not in lang:
            raise BuildConfigError(
                f"engine 'pdf' is not supported for language {lang.get('code')!r}; allowed: {ladder(lang)}"
            )
    preamble_extra = raw.get("preamble_extra", [])
    if not isinstance(preamble_extra, list) or not all(isinstance(x, str) for x in preamble_extra):
        raise BuildConfigError("preamble_extra in build.json must be a list of strings")
    shim_off = raw.get("shim_off", [])
    if not isinstance(shim_off, list) or not all(isinstance(x, str) for x in shim_off):
        raise BuildConfigError("shim_off in build.json must be a list of strings")
    bad_rules = sorted(set(shim_off) - set(SHIM_RULES))
    if bad_rules:
        raise BuildConfigError(
            f"unknown shim_off rule(s) in build.json: {bad_rules}; allowed: {list(SHIM_RULES)}"
        )
    return {
        "engine": engine,
        "preamble_extra": list(preamble_extra),
        "shim_off": list(shim_off),
    }


def prepare_tree(
    work: Path,
    project: dict,
    segs: list[Segment],
    translations: dict[str, str],
    engine: str,
    lang: dict | None,
    out: Path,
    keep_source: set[str] | frozenset[str] = frozenset(),
    shim_off: set[str] | frozenset[str] = frozenset(),
    preamble_extra: list[str] | tuple[str, ...] = (),
    prof: dict | None = None,
    fallback_fonts: tuple[str, ...] = (),
) -> dict:
    work, out = Path(work), Path(out)
    for rel in project["files"]:  # defence in depth: project.json names only files inside the build dir
        if not (out / rel).resolve().is_relative_to(out.resolve()):
            raise ValueError(f"project file escapes the build dir: {rel}")
    eff_dir = effective_dir(work)
    asm = assemble_tree(eff_dir, out, project, segs, translations, keep_source)
    src = _source_lang(project)
    if prof is None:
        prof = source_profile(list(_tree_texts(eff_dir, project).values()), src)
    texts = _tree_texts(out, project)
    tgt_babel = lang and (lang["pdflatex"]["babel"] if engine == "pdf" else lang["babel"])
    for path, text in texts.items():
        is_main = path == out / project["main"]
        text = neutralise(text, engine, prof, src.get("babel"), tgt_babel, class_file=path.suffix in (".cls", ".rtx", ".clo"))
        write_text(path, apply_shim(text, engine, is_main, shim_off=shim_off))
    _reencode_bbl(out, project)
    if "minted" not in shim_off:
        write_text(out / "rosettex-minted.sty", MINTED_STY)
    use_fb = engine == "lua" and bool(fallback_fonts)
    if engine == "lua":
        write_text(out / "rosettex-marks.sty", marks_sty())
    if use_fb:
        write_text(out / "rosettex-fallback.sty", fallback_sty(fallback_fonts))
    src_lines, src_fonts = source_preamble(engine, prof, with_fallback=use_fb)
    fb_lines = fallback_preamble_lines() if use_fb else []
    fonts = src_fonts + (staged_fonts(lang) if lang is not None else []) + (
        fallback_bundled_fonts(fallback_fonts) if use_fb else []
    )
    if engine != "pdf":
        fonts_root = resources.files("rosettex.data").joinpath("fonts")
        for fname in dict.fromkeys(fonts):
            (out / fname).write_bytes(fonts_root.joinpath(fname).read_bytes())
        for fd_name, fd_content in tu_fd_files().items():
            write_text(out / fd_name, fd_content)
    pre_parts: list[str] = []
    if fb_lines:
        pre_parts.append("\n".join(fb_lines) + "\n")
    if src_lines:
        pre_parts.append("\n".join(src_lines) + "\n")
    if lang is not None:
        pre_parts.append(language_preamble(engine, lang, with_fallback=use_fb))
    elif engine != "pdf":
        pre_parts.append(baseline_preamble(engine))
    if preamble_extra:
        pre_parts.append("\n".join(preamble_extra) + "\n")
    if pre_parts:
        main_text, _ = read_source(out / project["main"])
        write_text(out / "rosettex-preamble.tex", "".join(pre_parts))
        write_text(
            out / project["main"],
            insert_preamble(main_text, prelude=preamble_prelude(engine, lang) if lang is not None else ""),
        )
    return asm["map"]


def _source_lang(project: dict) -> dict:
    code = project.get("source_lang") or project.get("from") or "en"
    try:
        return load_lang(code)
    except ValueError:
        return {"code": code}


def _tree_texts(root: Path, project: dict) -> dict[Path, str]:
    """The project files and the local class/package/option files (.sty/.cls/.rtx/.clo) a build shims."""
    encodings = project.get("encodings") or {}
    main_enc = encodings.get(project["main"])
    texts = {root / rel: read_source(root / rel, encodings.get(rel))[0] for rel in project["files"]}
    for p in sorted(p for ext in ("*.sty", "*.cls", "*.rtx", "*.clo") for p in root.rglob(ext) if p.is_file()):
        texts.setdefault(p, read_source(p, main_enc)[0])
    return texts


def _reencode_bbl(out: Path, project: dict) -> None:
    """A shipped .bbl is read in the main file's encoding; the build copy is UTF-8 (2001.02586: Latin-1 .bbl)."""
    enc = (project.get("encodings") or {}).get(project["main"], "utf-8")
    for bbl in out.rglob("*.bbl"):
        if not bbl.is_file():
            continue
        data = bbl.read_bytes()
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            write_text(bbl, data.decode(enc if enc not in ("utf-8", "utf-8-sig") else "latin-1", errors="rosettex-latin1"))


def _guard_errors(res: BuildResult | dict | None) -> list[str]:
    if res is None:
        return []
    errors = res.errors if isinstance(res, BuildResult) else res.get("errors", [])
    return [e for e in errors if isinstance(e, str) and e.startswith("rosettex-guard:")]


def _compile(
    work: Path,
    project: dict,
    segs: list[Segment],
    translations: dict[str, str],
    engine: str,
    lang: dict | None,
    name: str,
    keep: set[str] | frozenset[str] = frozenset(),
    single_pass: bool = False,
    shim_off: set[str] | frozenset[str] = frozenset(),
    preamble_extra: list[str] | tuple[str, ...] = (),
    prof: dict | None = None,
    fallback_fonts: tuple[str, ...] = (),
    _allow_fallback: bool = True,
) -> tuple[BuildResult, dict]:
    out = Path(work) / name
    # Safe points (spec §3, §9.7): every finished compile is memoised in build_state.json by its inputs, and a
    # budget-stopped one records its pass state; a rerun replays the same steps from the memo and continues the
    # stopped compile in its kept build dir. The memo is deleted when a build finishes.
    key = _sha1(json.dumps(
        [name, engine, lang and lang["code"], sorted(translations.items()), sorted(keep), single_pass,
         sorted(shim_off), list(preamble_extra), list(fallback_fonts),
         project.get("effective_hash") or project.get("source_hash")],
        ensure_ascii=False,
    ))
    st = _load_build_state(work)
    marker = out / "rosettex-key"
    in_dir = marker.is_file() and marker.read_text(encoding="utf-8") == key
    done = st["done"].get(key)
    fb_saved = st["fallback"].get(key) if _allow_fallback and not fallback_fonts else None
    res: BuildResult | None = None
    pos_map: dict = {}
    if done and (in_dir or single_pass or not done["ok"] or fb_saved):
        map_path = out / "rosettex-map.json"
        res = BuildResult(**done)
        pos_map = read_json(map_path) if in_dir and map_path.is_file() else {}
    else:
        run = st["running"].get(name)
        resume = run if run and run["key"] == key and in_dir else None
        if resume:
            res = _run_engine_saved(work, st, name, key, out, project["main"], engine, single_pass, resume)
            pos_map = read_json(out / "rosettex-map.json")
        else:
            if out.exists():
                shutil.rmtree(out)
            pos_map = prepare_tree(
                work,
                project,
                segs,
                translations,
                engine,
                lang,
                out,
                keep,
                shim_off=shim_off,
                preamble_extra=preamble_extra,
                prof=prof,
                fallback_fonts=fallback_fonts,
            )
            extra_overlay = [rel for rel in overlay_files(work) if not rel.endswith(".tex")]
            if extra_overlay:
                findings = guard_scan(out, extra_overlay)
                if findings:
                    stem = Path(project["main"]).stem
                    log = out / f"{stem}.log"
                    errs = [
                        f"rosettex-guard: {f['file']}:{f['line']} ({f['kind']}): {f['text']}"
                        for f in findings[:20]
                    ]
                    guard_mode = "lua+scan" if engine == "lua" else "scan-only"
                    return (
                        BuildResult(False, engine, 0, None, str(log), None, False, errs, guard=guard_mode),
                        pos_map,
                    )
            marker.write_text(key, encoding="utf-8")
            res = _run_engine_saved(work, st, name, key, out, project["main"], engine, single_pass, None)

    if _allow_fallback and engine == "lua" and not single_pass and not fallback_fonts:
        if fb_saved:
            needed, orig_missing = fb_saved["fonts"], fb_saved["missing"]
        elif res.log and Path(res.log).is_file():
            log_text = Path(res.log).read_text(encoding="utf-8", errors="replace")
            needed = needed_fallback_fonts(log_text)
            orig_missing = parse_log(log_text)["missing_chars"]
            if needed:
                st["fallback"][key] = {"fonts": needed, "missing": orig_missing}
                write_json(Path(work) / "build_state.json", st)
        else:
            needed, orig_missing = [], 0
        if needed:
            fb_res, fb_map = _compile(
                work,
                project,
                segs,
                translations,
                engine,
                lang,
                name,
                keep=keep,
                single_pass=False,
                shim_off=shim_off,
                preamble_extra=preamble_extra,
                prof=prof,
                fallback_fonts=tuple(needed),
                _allow_fallback=False,
            )
            fb_log = (
                Path(fb_res.log).read_text(encoding="utf-8", errors="replace")
                if fb_res.log and Path(fb_res.log).is_file()
                else ""
            )
            fb_missing = parse_log(fb_log)["missing_chars"]
            if fb_missing < orig_missing and (fb_res.ok or not res.ok):
                return fb_res, fb_map
            if res.ok:
                # Fallback regressed or did not improve; restore the non-fallback build directory.
                st = _load_build_state(work)
                st["fallback"][key] = {"fonts": [], "missing": orig_missing}
                st["done"].pop(key, None)
                write_json(Path(work) / "build_state.json", st)
                return _compile(
                    work,
                    project,
                    segs,
                    translations,
                    engine,
                    lang,
                    name,
                    keep=keep,
                    single_pass=False,
                    shim_off=shim_off,
                    preamble_extra=preamble_extra,
                    prof=prof,
                    fallback_fonts=(),
                    _allow_fallback=False,
                )
    return res, pos_map


def _load_build_state(work: Path) -> dict:
    path = Path(work) / "build_state.json"
    try:
        st = read_json(path) if path.is_file() else {}
    except ValueError:
        st = {}
    return {"done": st.get("done", {}), "running": st.get("running", {}), "fallback": st.get("fallback", {})}


def _run_engine_saved(work, st, name, key, out, main, engine, single_pass, resume) -> BuildResult:
    try:
        res = run_engine(out, main, engine, single_pass=single_pass, resume=resume)
    except BudgetStop as stop:
        st["running"][name] = {"key": key, **stop.args[0]}
        write_json(Path(work) / "build_state.json", st)
        raise
    st["running"].pop(name, None)
    st["done"][key] = asdict(res)
    write_json(Path(work) / "build_state.json", st)
    return res


def _baseline(
    work: Path,
    project: dict,
    segs: list[Segment],
    engine: str = "lua",
    shim_off: set[str] | frozenset[str] = frozenset(),
    prof: dict | None = None,
) -> dict:
    meta_path = work / "baseline" / "baseline.json"
    eff_hash = project.get("effective_hash") or project.get("source_hash")
    key = {
        "effective_hash": eff_hash,
        "engine": engine,
        "exe": find_tex(ENGINES[engine]),
        "shim_off": sorted(shim_off),
    }
    if meta_path.exists():
        try:
            cached = read_json(meta_path)
            res = cached.get("result", {})
            if (
                cached.get("key") == key
                and res.get("ok")
                and res.get("log")
                and Path(res["log"]).exists()
                and (not res.get("pdf") or Path(res["pdf"]).exists())
            ):
                return res
        except Exception:
            pass
    res, _ = _compile(work, project, segs, {}, engine, None, "baseline", shim_off=shim_off, prof=prof)
    res_dict = asdict(res)
    write_json(meta_path, {"key": key, "result": res_dict})
    return res_dict


def _sha1(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def _sha1_bytes(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def _snapshot(
    work: Path,
    round_num: int,
    tr: dict[str, str],
    cfg: dict,
    diagnosed_segments: list[str],
) -> dict:
    tr_items = sorted(tr.items())
    tr_hash = _sha1(json.dumps(tr_items, ensure_ascii=False))
    seg_hashes = {sid: _sha1(text)[:12] for sid, text in tr_items}
    ov_files = {
        rel: _sha1_bytes((work / "fix" / rel).read_bytes())[:12] for rel in overlay_files(work)
    }
    cfg_canon = json.dumps(cfg, sort_keys=True, ensure_ascii=False)
    return {
        "round": round_num,
        "translations_hash": tr_hash,
        "segment_hashes": seg_hashes,
        "overlay_hash": overlay_hash(work),
        "overlay_files": ov_files,
        "build_json_hash": _sha1(cfg_canon),
        "build_json": cfg,
        "diagnosed_segments": sorted(set(diagnosed_segments)),
    }


def _diff_repairs(prev: dict, cur: dict, round_num: int) -> list[dict]:
    changes: list[dict] = []
    prev_ov = prev.get("overlay_files", {})
    cur_ov = cur.get("overlay_files", {})
    for rel in sorted(set(prev_ov) | set(cur_ov)):
        if rel not in prev_ov:
            changes.append(
                {
                    "round": round_num,
                    "channel": "fix",
                    "file": f"fix/{rel}",
                    "summary": "added overlay",
                }
            )
        elif rel not in cur_ov:
            changes.append(
                {
                    "round": round_num,
                    "channel": "fix",
                    "file": f"fix/{rel}",
                    "summary": "removed overlay",
                }
            )
        elif prev_ov[rel] != cur_ov[rel]:
            changes.append(
                {
                    "round": round_num,
                    "channel": "fix",
                    "file": f"fix/{rel}",
                    "summary": "edited overlay",
                }
            )

    prev_cfg = prev.get("build_json", {"engine": None, "preamble_extra": [], "shim_off": []})
    cur_cfg = cur.get("build_json", {"engine": None, "preamble_extra": [], "shim_off": []})
    if prev.get("build_json_hash") != cur.get("build_json_hash"):
        parts: list[str] = []
        if prev_cfg.get("engine") != cur_cfg.get("engine"):
            parts.append(f"engine {prev_cfg.get('engine')} -> {cur_cfg.get('engine')}")
        if prev_cfg.get("preamble_extra") != cur_cfg.get("preamble_extra"):
            parts.append(
                f"preamble_extra ({len(prev_cfg.get('preamble_extra', []))} -> "
                f"{len(cur_cfg.get('preamble_extra', []))} lines)"
            )
        if prev_cfg.get("shim_off") != cur_cfg.get("shim_off"):
            parts.append(f"shim_off {prev_cfg.get('shim_off')} -> {cur_cfg.get('shim_off')}")
        changes.append(
            {
                "round": round_num,
                "channel": "build_settings",
                "file": "build.json",
                "summary": "; ".join(parts) or "updated build.json",
            }
        )

    prev_segs = prev.get("segment_hashes", {})
    cur_segs = cur.get("segment_hashes", {})
    diag_ids = prev.get("diagnosed_segments", [])
    recorded_tr = False
    for sid in diag_ids:
        if prev_segs.get(sid) != cur_segs.get(sid):
            recorded_tr = True
            if sid not in cur_segs:
                summary = "cleared translation"
            elif sid not in prev_segs:
                summary = "added translation"
            else:
                summary = "edited translation"
            changes.append(
                {
                    "round": round_num,
                    "channel": "translation",
                    "segment": sid,
                    "summary": summary,
                }
            )
    if (
        not recorded_tr
        and prev.get("overlay_hash") == cur.get("overlay_hash")
        and prev.get("translations_hash") != cur.get("translations_hash")
    ):
        changed_ids = [
            sid for sid in sorted(set(prev_segs) | set(cur_segs)) if prev_segs.get(sid) != cur_segs.get(sid)
        ]
        for sid in changed_ids[:10]:
            changes.append(
                {
                    "round": round_num,
                    "channel": "translation",
                    "segment": sid,
                    "summary": "edited translation",
                }
            )
    return changes


def _extract_diag_segments(diag: list[dict]) -> list[str]:
    ids: list[str] = []
    for entry in diag:
        seg = entry.get("segment")
        if seg:
            ids.append(seg)
        for sid in entry.get("segments") or []:
            ids.append(sid)
    return sorted(set(ids))


def _build_diagnosis(
    build_dir: Path,
    project: dict,
    segs: list[Segment],
    tr: dict[str, str],
    res: BuildResult,
    problems: list[dict],
    pos_map: dict | None = None,
) -> list[dict]:
    if pos_map is None:
        map_path = build_dir / "rosettex-map.json"
        pos_map = read_json(map_path) if map_path.is_file() else {}
    log_path = Path(res.log) if res.log else None
    log_text = (
        log_path.read_text(encoding="utf-8", errors="replace")
        if log_path and log_path.is_file()
        else ""
    )
    diag = diagnose(build_dir, project["main"], pos_map, segs, tr, log_text)
    if diag:
        return diag
    fallback: list[dict] = []
    for p in problems:
        code = p["code"]
        is_build_cfg = code in ("timeout", "missing_chars")
        ch = "build_settings" if is_build_cfg else ("fix" if code == "undefined_refs" else "translation")
        if code == "undefined_refs":
            action = (
                f"Check undefined \\ref/\\cite keys in {project['main']} (or bibliography files) and fix via "
                f"'rosettex fix <work> --file {project['main']}' or restore missing placeholders in out/*.txt."
            )
        elif is_build_cfg:
            action = f"Edit build.json (engine, preamble_extra, or shim_off) to resolve: {p['message']}"
        else:
            action = f"Inspect translated segments in out/*.txt to resolve: {p['message']}"
        fallback.append(
            {
                "message": f"{code}: {p['message']}",
                "build_file": project["main"],
                "build_line": None,
                "kind": "preamble" if is_build_cfg else "source",
                "segment": None,
                "source_file": project["main"] if ch == "fix" else None,
                "source_line": None,
                "context": [],
                "channel": ch,
                "next_action": action,
            }
        )
    if not fallback and not res.ok:
        msg = "; ".join(res.errors) if res.errors else "compile failed without PDF"
        fallback.append(
            {
                "message": msg,
                "build_file": project["main"],
                "build_line": None,
                "kind": "preamble",
                "segment": None,
                "source_file": None,
                "source_line": None,
                "context": [],
                "channel": "build_settings",
                "next_action": f"Edit build.json (engine, preamble_extra, or shim_off) to resolve: {msg}",
            }
        )
    return fallback


def _source_error_sig(
    build_dir: Path,
    project: dict,
    segs: list[Segment],
    tr: dict[str, str],
    res: BuildResult,
    pos_map: dict | None = None,
) -> tuple[str, int, str] | None:
    """Signature (source_file, source_line, message) of the first paper-source error, or None."""
    if not res.log or not Path(res.log).is_file():
        return None
    if pos_map is None:
        map_path = build_dir / "rosettex-map.json"
        pos_map = read_json(map_path) if map_path.is_file() else {}
    log_text = Path(res.log).read_text(encoding="utf-8", errors="replace")
    for entry in diagnose(build_dir, project["main"], pos_map, segs, tr, log_text):
        if entry.get("kind") == "missing_package" or "characters" in entry:
            continue
        if (
            entry.get("kind") == "source"
            and entry.get("channel") == "fix"
            and entry.get("source_file")
            and entry.get("source_line") is not None
            and entry.get("message")
        ):
            return (entry["source_file"], int(entry["source_line"]), entry["message"])
        return None
    return None


def _guard_refused_result(
    engine: str,
    baseline: dict,
    translated: BuildResult | None,
    guard_errors: list[str],
    steps: list[dict],
    repairs: list[dict],
) -> dict:
    return {
        "ok": False,
        "state": "guard_refused",
        "guard_refused": True,
        "guard_errors": guard_errors,
        "engine": engine,
        "pdf": None,
        "baseline": baseline,
        "baseline_failed": not baseline.get("ok"),
        "translated": asdict(translated) if translated else baseline,
        "kept_source": [],
        "ladder": steps,
        "culprits": [],
        "bisect_cap_kept": [],
        "bisect_compiles": 0,
        "bisect_capped": False,
        "repairs": repairs,
        "diagnosis": [],
    }


def build(
    work: Path, lang_code: str, translations: dict[str, str], agent: bool = True, budget: float | None = None
) -> dict:
    INSTALLED.clear()
    SEARCHED.clear()
    BUDGET.update(deadline=None if budget is None else time.monotonic() + budget, worked=False)
    try:
        res = _build(work, lang_code, translations, agent)
    except BudgetStop:
        return {"ok": False, "state": "partial", "installed": [i for i in INSTALLED if i["ok"]]}
    finally:
        BUDGET.update(deadline=None, worked=False)
    (Path(work) / "build_state.json").unlink(missing_ok=True)
    res["installed"] = [i for i in INSTALLED if i["ok"]]  # on-demand packages (spec §9.7), into report.json
    res["install_failed"] = [i for i in INSTALLED if not i["ok"]]
    return res


def _build(work: Path, lang_code: str, translations: dict[str, str], agent: bool) -> dict:
    work = Path(work)
    project = read_json(work / "project.json")
    segs = load_segments(work)
    lang = load_lang(lang_code)
    cfg = load_build_config(work, lang)
    shim_off = frozenset(cfg["shim_off"])
    preamble_extra = cfg["preamble_extra"]
    prof = source_profile(list(_tree_texts(effective_dir(work), project).values()), _source_lang(project))
    engines = [cfg["engine"]] if cfg["engine"] else source_ladder(prof, ladder(lang))
    first_engine = engines[0]
    tr = {k: v for k, v in translations.items() if v and v.strip()}

    baseline = _baseline(work, project, segs, first_engine, shim_off=shim_off, prof=prof)
    base_guard = _guard_errors(baseline)
    if base_guard:
        return _guard_refused_result(first_engine, baseline, None, base_guard, [], [])

    repair_state = _load_repair_state(work) if agent else {"rounds": [], "repairs": []}
    rounds: list[dict] = list(repair_state.get("rounds", []))
    repairs: list[dict] = list(repair_state.get("repairs", []))
    current_round = len(rounds) if agent else 0
    if agent and rounds:
        cur_snap_pre = _snapshot(work, current_round, tr, cfg, [])
        repairs.extend(_diff_repairs(rounds[-1], cur_snap_pre, current_round))

    ratio = page_ratio_for(lang)
    precompiled_first: tuple[str, BuildResult] | None = None

    if agent:
        res0, map0 = _compile(
            work,
            project,
            segs,
            tr,
            first_engine,
            lang,
            "build",
            shim_off=shim_off,
            preamble_extra=preamble_extra,
            prof=prof,
        )
        g_errs = _guard_errors(res0)
        if g_errs:
            return _guard_refused_result(
                first_engine,
                baseline,
                res0,
                g_errs,
                [{"engine": first_engine, "ok": False, "stage": "translated"}],
                repairs,
            )
        cmp0 = compare(stats_of(baseline), stats_of(asdict(res0)), ratio)
        has_problems = (not res0.ok) or bool(cmp0["problems"])
        if not has_problems:
            _reset_repair_state(work)
            return {
                "ok": True,
                "state": "ok",
                "engine": res0.engine,
                "pdf": res0.pdf,
                "baseline": baseline,
                "baseline_failed": not baseline.get("ok"),
                "translated": asdict(res0),
                "kept_source": [],
                "ladder": [{"engine": first_engine, "ok": True, "stage": "translated"}],
                "culprits": [],
                "bisect_cap_kept": [],
                "bisect_compiles": 0,
                "bisect_capped": False,
                "repairs": repairs,
                "diagnosis": [],
                "rounds_left": MAX_REPAIR_ROUNDS,
            }
        diag = _build_diagnosis(work / "build", project, segs, tr, res0, cmp0["problems"], map0)
        diag_segs = _extract_diag_segments(diag)
        if len(rounds) <= MAX_REPAIR_ROUNDS:
            rounds.append(_snapshot(work, current_round, tr, cfg, diag_segs))
        if current_round < MAX_REPAIR_ROUNDS:
            rounds_left = MAX_REPAIR_ROUNDS - current_round
            _save_repair_state(work, {"rounds": rounds, "repairs": repairs})
            return {
                "ok": False,
                "state": "needs_repair",
                "engine": res0.engine,
                "pdf": None,
                "baseline": baseline,
                "baseline_failed": not baseline.get("ok"),
                "translated": asdict(res0),
                "kept_source": [],
                "ladder": [{"engine": first_engine, "ok": res0.ok, "stage": "translated"}],
                "culprits": [],
                "bisect_cap_kept": [],
                "bisect_compiles": 0,
                "bisect_capped": False,
                "diagnosis": diag,
                "rounds_left": rounds_left,
                "repairs": repairs,
            }
        _save_repair_state(work, {"rounds": rounds, "repairs": repairs})
        precompiled_first = (first_engine, res0)

    steps: list[dict] = []
    culprits: list[str] = []
    bisect_cap_kept: list[str] = []
    bisect_compiles = 0
    bisect_capped = False
    final: BuildResult | None = None
    res: BuildResult | None = None
    prev_source_err: tuple[str, int, str] | None = None
    for engine in engines:
        pos_map: dict | None = None
        if precompiled_first is not None and precompiled_first[0] == engine:
            res = precompiled_first[1]
            precompiled_first = None
        else:
            res, pos_map = _compile(
                work,
                project,
                segs,
                tr,
                engine,
                lang,
                "build",
                shim_off=shim_off,
                preamble_extra=preamble_extra,
                prof=prof,
            )
            g_errs = _guard_errors(res)
            if g_errs:
                for tmp in ("build-probe", "build-bisect"):
                    shutil.rmtree(work / tmp, ignore_errors=True)
                steps.append({"engine": engine, "ok": False, "stage": "translated"})
                return _guard_refused_result(engine, baseline, res, g_errs, steps, repairs)
        steps.append({"engine": engine, "ok": res.ok, "stage": "translated"})
        if res.ok:
            final = res
            break
        err_sig = _source_error_sig(work / "build", project, segs, tr, res, pos_map)
        if err_sig is not None and err_sig == prev_source_err:
            # Two engines failed on the exact same paper-source error: further engines won't fix it
            # (and e.g. pdflatex may loop into a 300s timeout on 2201.08386). Stop and report it.
            steps.append({"engine": engine, "ok": False, "stage": "same_source_error"})
            final = res
            break
        probe, _ = _compile(
            work,
            project,
            segs,
            {},
            engine,
            lang,
            "build-probe",
            single_pass=True,
            shim_off=shim_off,
            preamble_extra=preamble_extra,
            prof=prof,
        )
        steps.append({"engine": engine, "ok": probe.ok, "stage": "probe"})
        if not probe.ok:
            if err_sig is not None:
                prev_source_err = err_sig
            continue  # engine/preamble/baseline problem: skip bisection, next engine
        ids = [s.id for s in segs if s.id in tr]
        remaining = list(ids)
        culprits = []
        bisect_cap_kept = []
        engine_compiles = 0
        best_passing: set[str] = set()
        cache: dict[frozenset[str], bool] = {frozenset(): False, frozenset(ids): True}

        def fails(subset: set[str], engine: str = engine) -> bool:
            nonlocal engine_compiles, bisect_compiles, best_passing
            key = frozenset(subset)
            if key in cache:
                return cache[key]
            if engine_compiles >= BISECT_CAP:
                raise _BisectCapReached
            engine_compiles += 1
            bisect_compiles += 1
            r, _ = _compile(
                work,
                project,
                segs,
                {k: tr[k] for k in subset},
                engine,
                lang,
                "build-bisect",
                single_pass=True,
                shim_off=shim_off,
                preamble_extra=preamble_extra,
                prof=prof,
            )
            failed = not r.ok
            cache[key] = failed
            if not failed and len(subset) > len(best_passing):
                best_passing = set(subset)
            return failed

        try:
            while remaining and len(culprits) < 5 and fails(set(remaining)):
                found = find_culprits(remaining, fails, max_culprits=1)
                if not found:
                    break
                culprits.extend(found)
                remaining = [i for i in remaining if i not in found]
        except _BisectCapReached:
            bisect_capped = True
            bisect_cap_kept = sorted(set(remaining) - best_passing)

        keep_set = set(culprits) | set(bisect_cap_kept)
        res, _ = _compile(
            work,
            project,
            segs,
            tr,
            engine,
            lang,
            "build",
            keep=keep_set,
            shim_off=shim_off,
            preamble_extra=preamble_extra,
            prof=prof,
        )
        steps.append({"engine": engine, "ok": res.ok, "stage": "without_culprits"})
        final = res
        break
    for tmp in ("build-probe", "build-bisect"):
        shutil.rmtree(work / tmp, ignore_errors=True)
    final = final or res
    if not baseline.get("ok") and final and final.ok and final.engine != first_engine:
        baseline = _baseline(work, project, segs, final.engine, shim_off=shim_off, prof=prof)
    baseline_failed = not baseline.get("ok")
    final_diag: list[dict] = []
    if final is not None:
        cmp_final = compare(stats_of(baseline), stats_of(asdict(final)), ratio)
        if (not final.ok) or cmp_final["problems"]:
            final_diag = _build_diagnosis(work / "build", project, segs, tr, final, cmp_final["problems"])
    return {
        "ok": bool(final and final.ok),
        "engine": final.engine if final else None,
        "pdf": final.pdf if final else None,
        "baseline": baseline,
        "baseline_failed": baseline_failed,
        "translated": asdict(final) if final else {},
        "kept_source": sorted(set(culprits) | set(bisect_cap_kept)),
        "ladder": steps,
        "culprits": sorted(culprits),
        "bisect_cap_kept": sorted(bisect_cap_kept),
        "bisect_compiles": bisect_compiles,
        "bisect_capped": bisect_capped,
        "repairs": repairs,
        "diagnosis": final_diag,
    }

"""PDF route build pipeline: validate -> strip -> snippets (cached) -> overlay (with bisect) -> merge."""

from __future__ import annotations

import copy
import hashlib
import json
import re
import shutil
import time
from collections import Counter
import unicodedata
from pathlib import Path
from typing import Any

from rosettex.build.bisect import find_culprits
from rosettex.build.repair_state import (
    MAX_REPAIR_ROUNDS,
    load_repair_state,
    reset_repair_state,
    save_repair_state,
)
from rosettex.jsonio import read_json, write_json
from rosettex.langdata import load_lang
from rosettex.pdf import schema
from rosettex.pdf.extract import PdfError, extract
from rosettex.pdf.merge import merge
from rosettex.pdf.overlay import overlay
from rosettex.pdf.reconcile import Recon, apply as d14_apply, reconcile as d14_reconcile
from rosettex.pdf.snippets import snippets
from rosettex.pdf.strip import strip

REPORT_SCHEMA = "rosettex.pdf-report/1"
_PH_TOKEN = re.compile(r"⟦([a-z])(\d+)⟧")
_WS = re.compile(r"\s+")
_MAX_BISECT_PROBES = 12

_RTL_RANGES = (
    (0x0600, 0x06FF),
    (0x0750, 0x077F),
    (0x08A0, 0x08FF),
    (0xFB50, 0xFDFF),
    (0xFE70, 0xFEFF),
    (0x0590, 0x05FF),
    (0xFB1D, 0xFB4F),
    (0x0700, 0x074F),
    (0x0780, 0x07BF),
)


def _is_rtl_letter(cp: int) -> bool:
    return any(start <= cp <= end for start, end in _RTL_RANGES)


def text_direction(layout: dict[str, Any]) -> str:
    """Return 'rtl' if >50% of letters in translated blocks are in RTL scripts, else 'ltr'."""
    total_letters = 0
    rtl_letters = 0
    for blk in layout.get("blocks") or []:
        if blk.get("translate") is True:
            text = blk.get("text") or ""
            for ch in text:
                if unicodedata.category(ch).startswith("L"):
                    total_letters += 1
                    if _is_rtl_letter(ord(ch)):
                        rtl_letters += 1
    if total_letters > 0 and (rtl_letters / total_letters) > 0.5:
        return "rtl"
    return "ltr"


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _layout_for_cache(layout: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of ``layout`` without snippet-pass derived fields so pre/post-snippets hash identically."""
    clean = copy.deepcopy(layout)
    for seg in clean.get("segments") or []:
        for ph in (seg.get("ph") or {}).values():
            if isinstance(ph, dict):
                ph.pop("snippet", None)
                ph.pop("size", None)
                ph.pop("depth", None)
    return clean


def _cache_hash(orig_sha: str, layout: dict[str, Any], demoted_ids: list[str]) -> str:
    payload = json.dumps(
        {"orig_sha": orig_sha, "layout": _layout_for_cache(layout), "demoted": sorted(demoted_ids)},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _extract_snippet_meta(layout: dict[str, Any]) -> dict[str, dict[str, dict[str, Any]]]:
    meta: dict[str, dict[str, dict[str, Any]]] = {}
    for seg in layout.get("segments") or []:
        seg_meta: dict[str, dict[str, Any]] = {}
        for k, ph in (seg.get("ph") or {}).items():
            if isinstance(ph, dict) and "snippet" in ph and "size" in ph:
                seg_meta[k] = {
                    "snippet": ph["snippet"],
                    "size": ph["size"],
                    "depth": ph.get("depth", 0.0),
                }
        if seg_meta:
            meta[seg["id"]] = seg_meta
    return meta


def _apply_snippet_meta(layout: dict[str, Any], meta: dict[str, dict[str, dict[str, Any]]]) -> None:
    for seg in layout.get("segments") or []:
        seg_meta = meta.get(seg["id"])
        if not seg_meta:
            continue
        ph_map = seg.get("ph") or {}
        for k, info in seg_meta.items():
            if k in ph_map and isinstance(ph_map[k], dict):
                ph_map[k]["snippet"] = info["snippet"]
                ph_map[k]["size"] = info["size"]
                ph_map[k]["depth"] = info["depth"]


def _demote_segments(
    layout: dict[str, Any],
    demotions: dict[str, str],
) -> None:
    """Demote segments in ``demotions`` (``{seg_id: reason}``) in place (R1)."""
    if not demotions:
        return
    blocks_by_id = {b["id"]: b for b in layout.get("blocks") or []}
    kept = layout.setdefault("kept", [])
    kept_segs: list[dict[str, Any]] = []
    for seg in layout.get("segments") or []:
        sid = seg["id"]
        reason = demotions.get(sid)
        if reason is None:
            kept_segs.append(seg)
            continue
        for bid in seg.get("blocks") or []:
            blk = blocks_by_id.get(bid)
            if blk is not None:
                blk["translate"] = False
                if "segment" in blk:
                    blk["segment"] = None
            kept.append({"block": bid, "reason": reason})
    layout["segments"] = kept_segs


def _validate_segments(
    layout: dict[str, Any],
    translations: dict[str, str],
) -> dict[str, str]:
    """Return ``{seg_id: reason}`` for segments that must be demoted before strip (R1)."""
    blocks_by_id = {b["id"]: b for b in layout.get("blocks") or []}
    demotions: dict[str, str] = {}
    for seg in layout.get("segments") or []:
        sid = seg["id"]
        blks = [blocks_by_id.get(bid) for bid in seg.get("blocks") or []]
        if not blks or any(b is None or not isinstance(b.get("box"), dict) for b in blks):
            demotions[sid] = "no_box"
            continue
        tr = translations.get(sid)
        if not isinstance(tr, str) or not tr.strip():
            demotions[sid] = "no_translation"
            continue
        src_phs = Counter(_PH_TOKEN.findall(seg.get("text") or ""))
        tr_phs = Counter(_PH_TOKEN.findall(tr))
        if tr_phs != src_phs:
            demotions[sid] = "bad_placeholders"
    return demotions


class _ProbeLimitReached(Exception):
    pass


def _overlay_ok(fit: dict[str, Any] | None, overlay_pdf: Path, n_pages: int) -> bool:
    if fit is None or not fit.get("engine_ok") or not overlay_pdf.is_file():
        return False
    return int(fit.get("pages", -1)) == n_pages


def _run_overlay_checked(
    layout: dict[str, Any],
    translations: dict[str, str],
    lang_code: str,
    pdf_dir: Path,
    *,
    fallback: bool,
) -> tuple[dict[str, Any] | None, str | None]:
    try:
        fit = overlay(layout, translations, lang_code, pdf_dir, fallback=fallback)
    except Exception as exc:  # noqa: BLE001
        return None, f"{type(exc).__name__}: {exc}"
    if not _overlay_ok(fit, pdf_dir / "overlay.pdf", len(layout.get("pages") or [])):
        errs = fit.get("engine_errors") or ["overlay build did not succeed"]
        return fit, "; ".join(str(e) for e in errs[:3])
    return fit, None


def _bisect_overlay_culprits(
    layout: dict[str, Any],
    translations: dict[str, str],
    lang_code: str,
    pdf_dir: Path,
    *,
    fallback: bool,
) -> list[str]:
    """Find culprit segment ids causing overlay LuaLaTeX failure (capped at 12 probes)."""
    seg_ids = [s["id"] for s in layout.get("segments") or []]
    if not seg_ids:
        return []
    bisect_dir = pdf_dir / "_bisect"
    math_pdf = pdf_dir / "inline_math.pdf"
    probes = 0
    smallest_failing: set[str] = set(seg_ids)

    def _fails(subset: set[str]) -> bool:
        nonlocal probes, smallest_failing
        if not subset:
            return False
        if probes >= _MAX_BISECT_PROBES:
            raise _ProbeLimitReached()
        probes += 1
        bisect_dir.mkdir(parents=True, exist_ok=True)
        if math_pdf.is_file() and not (bisect_dir / "inline_math.pdf").exists():
            shutil.copyfile(math_pdf, bisect_dir / "inline_math.pdf")
        sub_tr = {sid: translations[sid] for sid in subset if sid in translations}
        fit, err = _run_overlay_checked(layout, sub_tr, lang_code, bisect_dir, fallback=fallback)
        failed = err is not None or fit is None
        if failed and len(subset) < len(smallest_failing):
            smallest_failing = set(subset)
        return failed

    culprits: list[str] = []
    try:
        culprits = find_culprits(seg_ids, _fails, max_culprits=5)
    except _ProbeLimitReached:
        culprits = sorted(set(culprits) | smallest_failing)
    finally:
        shutil.rmtree(bisect_dir, ignore_errors=True)

    if not culprits:
        culprits = sorted(smallest_failing)
    return culprits


def _plain_chars(tr: str, ph: dict[str, Any]) -> int:
    plain = _PH_TOKEN.sub(lambda m: str((ph.get(f"{m.group(1)}{m.group(2)}") or {}).get("raw", "")), tr)
    return len(_WS.sub(" ", plain).strip())


def _overflow_diagnoses(
    layout: dict[str, Any],
    reconciled_translations: dict[str, str],
    fit_segs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    seg_by_id = {s["id"]: s for s in layout.get("segments") or []}
    diags: list[dict[str, Any]] = []
    for s in fit_segs:
        overflow_lines = int(s.get("overflow_lines") or 0)
        if overflow_lines <= 0:
            continue
        sid = s["id"]
        cut_chars = int(s.get("cut_chars") or 0)
        seg = seg_by_id.get(sid) or {}
        now_chars = _plain_chars(reconciled_translations.get(sid, ""), seg.get("ph") or {})
        target_chars = max(1, now_chars - cut_chars)
        boxes = s.get("boxes") or []
        page = int(boxes[0]["page"]) + 1 if boxes else 1
        line_word = "line" if overflow_lines == 1 else "lines"
        diags.append(
            {
                "segment": sid,
                "page": page,
                "code": "overflow",
                "overflow_lines": overflow_lines,
                "cut_chars": cut_chars,
                "now_chars": now_chars,
                "target_chars": target_chars,
                "message": (
                    f"p.{page} {sid} is ~{overflow_lines} {line_word} too long; "
                    f"shorten to ~{target_chars} characters (now {now_chars}), keep the placeholders"
                ),
            }
        )
    return diags


def _pdf_seg_hashes(translations: dict[str, str]) -> dict[str, str]:
    return {
        sid: hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]
        for sid, text in sorted(translations.items())
        if isinstance(text, str) and text.strip()
    }


def _diff_pdf_repairs(prev: dict[str, Any], cur_hashes: dict[str, str], round_num: int) -> list[dict[str, Any]]:
    prev_hashes = prev.get("segment_hashes") or {}
    diag_ids = prev.get("diagnosed_segments") or []
    changes: list[dict[str, Any]] = []
    for sid in diag_ids:
        if prev_hashes.get(sid) != cur_hashes.get(sid):
            changes.append(
                {
                    "round": round_num,
                    "channel": "translation",
                    "segment": sid,
                    "summary": "edited translation",
                }
            )
    return changes


def build(
    work: Path,
    lang_code: str,
    translations: dict[str, str],
    *,
    fallback: bool = True,
    agent: bool = False,
    kept_terms: set[str] | frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """Run the PDF route build on ``work/orig.pdf`` and ``work/pdf/layout.json`` -> ``work/out.pdf``.

    When ``agent=True``, runs up to ``MAX_REPAIR_ROUNDS`` agent repair rounds (``fallback=False``)
    returning ``state: "needs_repair"`` with overflow diagnoses before falling back to ``fallback=True``.
    Writes ``work/pdf/layout.json`` (with D14 reconciliation, demotions, and snippet metadata)
    and ``work/pdf/report.json``, and returns the report dict.
    """
    t_total0 = time.perf_counter()
    work = Path(work)
    pdf_dir = work / "pdf"
    layout_path = pdf_dir / "layout.json"
    layout_base_path = pdf_dir / "layout_base.json"

    layout = schema.load(layout_path)
    if layout.get("_d14_applied") and layout_base_path.is_file():
        layout = schema.load(layout_base_path)
    else:
        schema.save(layout, layout_base_path)

    src_dir = text_direction(layout)
    tgt_dir = (load_lang(lang_code).get("profile") or {}).get("direction", "ltr")
    if src_dir != tgt_dir:
        raise PdfError(
            "direction_change",
            f"PDF route cannot change writing direction ({src_dir} → {tgt_dir}): translate from the paper's TeX source instead",
        )

    orig_pdf = work / "orig.pdf"
    pdf_dir.mkdir(parents=True, exist_ok=True)
    stripped_pdf = pdf_dir / "stripped.pdf"
    inline_math_pdf = pdf_dir / "inline_math.pdf"
    cache_path = pdf_dir / "cache.json"
    out_pdf = work / "out.pdf"
    warnings: list[str] = []
    demoted_segments: dict[str, str] = {}

    repair_state = load_repair_state(work) if agent else {"rounds": [], "repairs": []}
    rounds: list[dict[str, Any]] = list(repair_state.get("rounds", []))
    repairs: list[dict[str, Any]] = list(repair_state.get("repairs", []))
    current_round = len(rounds) if agent else 0
    cur_seg_hashes = _pdf_seg_hashes(translations) if agent else {}
    if agent and rounds:
        repairs.extend(_diff_pdf_repairs(rounds[-1], cur_seg_hashes, current_round))
    use_fallback = fallback if not agent else (current_round >= MAX_REPAIR_ROUNDS)

    # 0. D14 reconcile + apply before validation/strip
    kept_set = set(kept_terms)
    recons: dict[str, Recon] = {}
    reconciled_translations = dict(translations)
    for seg in layout.get("segments") or []:
        sid = seg["id"]
        tr = translations.get(sid)
        if isinstance(tr, str) and tr.strip():
            recons[sid] = d14_reconcile(seg, tr, kept_set, layout)
    d14_warnings = d14_apply(layout, recons)
    for sid, recon in recons.items():
        reconciled_translations[sid] = recon.translation
    d14_wrapped = sum(len(r.wraps) for r in recons.values())
    d14_unwrapped = sum(len(r.unwraps) for r in recons.values())
    if d14_warnings:
        warnings.extend(d14_warnings)

    # 1. Validate translations & segment boxes; demote invalid segments (R1) before strip
    pre_demotions = _validate_segments(layout, reconciled_translations)
    if pre_demotions:
        demoted_segments.update(pre_demotions)
        _demote_segments(layout, pre_demotions)
        by_reason: dict[str, list[str]] = {}
        for sid, reason in sorted(pre_demotions.items()):
            by_reason.setdefault(reason, []).append(sid)
        for reason, sids in sorted(by_reason.items()):
            warnings.append(f"demoted {len(sids)} segment(s) ({reason}): {', '.join(sids[:10])}")

    # 2. Check cache for stripped.pdf + inline_math.pdf
    orig_sha = _sha256_file(orig_pdf)
    pre_hash = _cache_hash(orig_sha, layout, sorted(pre_demotions.keys()))
    cache_hit = False
    if cache_path.is_file() and stripped_pdf.is_file() and inline_math_pdf.is_file():
        try:
            cache = read_json(cache_path)
        except Exception:  # noqa: BLE001
            cache = None
        if isinstance(cache, dict):
            if cache.get("post_hash") == pre_hash:
                _apply_snippet_meta(layout, cache.get("snippet_meta") or {})
                cache_hit = True
            elif cache.get("pre_hash") == pre_hash and not cache.get("strip_demoted"):
                _apply_snippet_meta(layout, cache.get("snippet_meta") or {})
                cache_hit = True

    t_extract = 0.0
    t_strip = 0.0
    t_snippets = 0.0
    chars: list[list[dict[str, Any]]] | None = None

    def _run_strip_and_snippets(cur_pre_hash: str) -> None:
        nonlocal t_extract, t_strip, t_snippets, chars
        if chars is None:
            te0 = time.perf_counter()
            _, chars = extract(orig_pdf)
            t_extract += time.perf_counter() - te0
        segs_before_map = {s["id"]: list(s.get("blocks") or []) for s in layout.get("segments") or []}
        block_to_seg = {bid: sid for sid, bids in segs_before_map.items() for bid in bids}
        kept_before = len(layout.get("kept") or [])
        s_res = strip(orig_pdf, layout, chars, stripped_pdf)
        t_strip += float(s_res.get("seconds", 0.0))
        segs_after = {s["id"] for s in layout.get("segments") or []}
        strip_demoted = sorted(set(segs_before_map) - segs_after)
        for entry in (layout.get("kept") or [])[kept_before:]:
            if isinstance(entry, dict):
                sid = block_to_seg.get(entry.get("block"))
                if sid in strip_demoted:
                    demoted_segments.setdefault(sid, str(entry.get("reason") or "strip_demoted"))
        for sid in strip_demoted:
            demoted_segments.setdefault(sid, "strip_demoted")
        sn_res = snippets(orig_pdf, layout, chars, inline_math_pdf)
        t_snippets += float(sn_res.get("seconds", 0.0))
        post_hash = _cache_hash(orig_sha, layout, [])
        write_json(
            cache_path,
            {
                "pre_hash": cur_pre_hash,
                "post_hash": post_hash,
                "strip_demoted": strip_demoted,
                "snippet_meta": _extract_snippet_meta(layout),
            },
        )

    if not cache_hit:
        _run_strip_and_snippets(pre_hash)

    # 3. Overlay (with bisect + R1 demotion on LuaLaTeX failure)
    t_ov0 = time.perf_counter()
    fit, ov_err = _run_overlay_checked(layout, reconciled_translations, lang_code, pdf_dir, fallback=use_fallback)
    if ov_err is not None or fit is None:
        culprits = _bisect_overlay_culprits(layout, reconciled_translations, lang_code, pdf_dir, fallback=use_fallback)
        if culprits:
            warnings.append(f"overlay failed ({ov_err}); bisected and demoted: {', '.join(culprits)}")
            for sid in culprits:
                demoted_segments[sid] = "overlay_error"
            _demote_segments(layout, {sid: "overlay_error" for sid in culprits})
            # Re-strip and re-snip so demoted culprit segments stay original in stripped.pdf
            re_pre_hash = _cache_hash(orig_sha, layout, sorted(set(pre_demotions.keys()) | set(culprits)))
            _run_strip_and_snippets(re_pre_hash)
            fit, ov_err2 = _run_overlay_checked(layout, reconciled_translations, lang_code, pdf_dir, fallback=use_fallback)
            if ov_err2 is not None or fit is None:
                raise RuntimeError(f"overlay build failed after demoting {culprits}: {ov_err2}")
        else:
            raise RuntimeError(f"overlay build failed ({lang_code}): {ov_err}")
    t_overlay = time.perf_counter() - t_ov0

    fit_segs = fit.get("segments") or []
    overflow_list = [
        {"id": s["id"], "lines": int(s["overflow_lines"]), "cut_chars": int(s["cut_chars"])}
        for s in fit_segs
        if int(s.get("overflow_lines") or 0) > 0
    ]

    # 3b. Agent repair round (spec §7.6): if overflowing at 0.8 and rounds remain, return needs_repair
    if agent and current_round < MAX_REPAIR_ROUNDS and overflow_list:
        diagnosis = _overflow_diagnoses(layout, reconciled_translations, fit_segs)
        rounds.append(
            {
                "round": current_round,
                "segment_hashes": cur_seg_hashes,
                "diagnosed_segments": [d["segment"] for d in diagnosis],
            }
        )
        rounds_left = MAX_REPAIR_ROUNDS - current_round
        save_repair_state(work, {"rounds": rounds, "repairs": repairs})
        layout["_d14_applied"] = True
        schema.save(layout, layout_path)
        report_nr: dict[str, Any] = {
            "schema": REPORT_SCHEMA,
            "state": "needs_repair",
            "lang": lang_code,
            "out": None,
            "d14": {
                "wrapped": d14_wrapped,
                "unwrapped": d14_unwrapped,
                "warnings": d14_warnings,
            },
            "seconds": {
                "extract": round(t_extract, 4),
                "strip": round(t_strip, 4),
                "snippets": round(t_snippets, 4),
                "overlay": round(t_overlay, 4),
                "merge": 0.0,
                "total": round(time.perf_counter() - t_total0, 4),
            },
            "kept": list(layout.get("kept") or []),
            "demoted": demoted_segments,
            "fallback": [],
            "overflow": overflow_list,
            "diagnosis": diagnosis,
            "rounds_left": rounds_left,
            "repairs": repairs,
            "links": {"moved": 0, "split": 0, "deleted": 0},
            "sizes": {"orig": orig_pdf.stat().st_size, "out": 0},
            "warnings": warnings,
        }
        write_json(pdf_dir / "report.json", report_nr)
        return report_nr

    if agent:
        reset_repair_state(work)
        if current_round >= MAX_REPAIR_ROUNDS:
            warnings.append(f"fallback build used after {MAX_REPAIR_ROUNDS} repair rounds")

    # 4. Merge -> work/out.pdf
    m_res = merge(stripped_pdf, pdf_dir / "overlay.pdf", out_pdf, layout, fit.get("links") or [])
    t_merge = float(m_res.get("seconds", 0.0))

    # 5. Persist updated layout.json and report.json
    layout["_d14_applied"] = True
    schema.save(layout, layout_path)

    report: dict[str, Any] = {
        "schema": REPORT_SCHEMA,
        "state": "done",
        "lang": lang_code,
        "out": "out.pdf",
        "d14": {
            "wrapped": d14_wrapped,
            "unwrapped": d14_unwrapped,
            "warnings": d14_warnings,
        },
        "seconds": {
            "extract": round(t_extract, 4),
            "strip": round(t_strip, 4),
            "snippets": round(t_snippets, 4),
            "overlay": round(t_overlay, 4),
            "merge": round(t_merge, 4),
            "total": round(time.perf_counter() - t_total0, 4),
        },
        "kept": list(layout.get("kept") or []),
        "demoted": demoted_segments,
        "fallback": [s["id"] for s in fit_segs if s.get("fallback")],
        "overflow": overflow_list,
        "diagnosis": [],
        "rounds_left": MAX_REPAIR_ROUNDS,
        "repairs": repairs,
        "links": {
            "moved": int(m_res.get("links_moved", 0)),
            "split": int(m_res.get("links_split", 0)),
            "deleted": int(m_res.get("links_deleted", 0)),
        },
        "sizes": {
            "orig": orig_pdf.stat().st_size,
            "out": out_pdf.stat().st_size,
        },
        "warnings": warnings,
    }
    write_json(pdf_dir / "report.json", report)
    return report

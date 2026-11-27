"""Round-trip gate: extract segments and rebuild the source unchanged. Usage: python scripts/roundtrip_gate.py <corpus_src_dir> [--out report.json] [--strict]
--strict (CI) counts a skipped paper (no main TeX file) as failing; without it a skip is only reported."""
import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

from rosettex.assemble import assemble_tree
from rosettex.escape import normalize_raw
from rosettex.segment import extract_segments, rebuild_from_text
from rosettex.source import fetch_local
from rosettex.textio import read_source


def check_paper(paper: Path) -> dict:
    t0 = time.perf_counter()
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp) / "w"
        proj = fetch_local(paper, work)
        if not proj["main"]:
            return {
                "paper": paper.name,
                "skipped": f"no main ({len(proj['candidates'])} candidates)",
                "seconds": round(time.perf_counter() - t0, 3),
            }
        try:
            segs, unknown = extract_segments(work / "src", proj)
        except Exception as exc:  # report, never hide
            return {
                "paper": paper.name,
                "error": f"{type(exc).__name__}: {exc}",
                "seconds": round(time.perf_counter() - t0, 3),
            }
        assemble_tree(work / "src", Path(tmp) / "out", proj, segs, {})
        identical = all(
            (Path(tmp) / "out" / f).read_bytes() == read_source(work / "src" / f)[0].encode("utf-8")
            for f in proj["files"]
        )
        by_id = {s.id: s for s in segs}
        mismatches = [
            s.id
            for s in segs
            if s.parent is None and normalize_raw(rebuild_from_text(s, by_id)) != normalize_raw(s._raw)
        ]
        return {
            "paper": paper.name,
            "segments": len(segs),
            "identical": identical,
            "rebuild_mismatches": len(mismatches),
            "examples": mismatches[:5],
            "unknown_commands": len(unknown),
            "seconds": round(time.perf_counter() - t0, 3),
        }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("corpus")
    ap.add_argument("--out")
    ap.add_argument("--strict", action="store_true", help="a skipped paper counts as failing")
    a = ap.parse_args()
    rows = [check_paper(p) for p in sorted(Path(a.corpus).iterdir()) if p.is_dir()]
    bad = [r for r in rows if r.get("error") or r.get("identical") is False or r.get("rebuild_mismatches")
           or (a.strict and r.get("skipped"))]
    for r in rows:
        print(json.dumps(r, ensure_ascii=False))
    if a.out:
        out_path = Path(a.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"papers={len(rows)} failing={len(bad)}", file=sys.stderr)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())

"""CI smoke (plan P4 CI): skill copies in sync → fetch → round-trip gate (--strict) → prepare → pseudo → check → build --no-agent.

Usage: python scripts/ci_smoke.py [--root DIR] [--papers ID ...] [--langs L ...] [--src CORPUS_DIR]
Fetches each paper once (arXiv, or `<src>/<id>` with --src) into <root>/corpus/<id>, runs the round-trip gate on
<root>/corpus, then the full pipeline per paper × language. Prints a JSON summary; exit 1 on any failure.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROSETTEX = [sys.executable, "-m", "rosettex"]
MAX_RERUNS = 200  # a `partial` (budgeted) command is rerun while it makes progress, at most this often


def run(argv: list[str], log: list) -> dict:
    """Run one rosettex/script command; rerun while the JSON envelope says `partial` and the rerun made progress
    (a new engine pass in work/build_state.json, or a changed state file)."""
    last = None
    for _ in range(MAX_RERUNS + 1):
        t0 = time.perf_counter()
        p = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace")
        try:
            env = json.loads(p.stdout) if "--json" in argv else {}
        except json.JSONDecodeError:
            env = {}
        cmd = argv[len(ROSETTEX):] if argv[:3] == ROSETTEX else [Path(argv[1]).name, *argv[2:]]
        entry = {"argv": cmd, "exit": p.returncode,
                 "seconds": round(time.perf_counter() - t0, 1), "state": env.get("state")}
        log.append(entry)
        if env.get("state") != "partial":
            ok = p.returncode == 0 and env.get("ok", True) is not False
            if not ok:
                entry["stdout"] = p.stdout[-2000:]
                entry["stderr"] = p.stderr[-2000:]
            return {"ok": ok, "env": env}
        state = _progress(argv)
        if state == last:
            entry["stuck"] = "rerun made no progress"
            break
        last = state
    return {"ok": False, "env": env}


def mcp_smoke() -> str:
    """Call every argument-free MCP tool through rosettex-mcp; each must answer ok (not isError)."""
    msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "ci", "version": "0"}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"}]
    names = ["guide", "doctor", "setup"]
    msgs += [{"jsonrpc": "2.0", "id": i, "method": "tools/call", "params": {"name": n, "arguments": {}}}
             for i, n in enumerate(names, 2)]
    p = subprocess.run([sys.executable, "-m", "rosettex.mcp"], input="".join(json.dumps(m) + "\n" for m in msgs),
                       capture_output=True, text=True, timeout=600)
    bad = []
    for line in p.stdout.splitlines():
        m = json.loads(line)
        if m.get("id", 0) < 2:
            continue
        r = m.get("result", {})
        text = (r.get("content") or [{}])[0].get("text", "")
        try:
            ok = not r.get("isError") and json.loads(text).get("ok") is not False
        except ValueError:
            ok = False
        if not ok:
            bad.append(f"{names[m['id'] - 2]}: {text[:200]}")
    return "ok" if not bad and p.returncode == 0 else "; ".join(bad) or p.stderr[-300:]


def _progress(argv: list[str]) -> str:
    """What a partial rerun must change: build_state.json (passes, memoised compiles) or toolchain.json (setup)."""
    files = [Path(argv[argv.index("build") + 1]) / "build_state.json"] if "build" in argv else []
    files.append(Path(os.environ.get("ROSETTEX_HOME") or Path.home() / ".rosettex") / "toolchain.json")
    return "".join(f.read_text(encoding="utf-8") if f.is_file() else "-" for f in files)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="ci-work")
    ap.add_argument("--papers", nargs="+", default=["0907.3704", "1512.03385"])
    ap.add_argument("--langs", nargs="+", default=["vi", "ar", "ja"])
    ap.add_argument("--src", help="local corpus dir with <id>/ source trees (no arXiv requests)")
    ap.add_argument("--fetch-only", action="store_true", help="only fill <root>/corpus (CI fixtures job)")
    a = ap.parse_args()
    root = Path(a.root).resolve()
    corpus = root / "corpus"
    corpus.mkdir(parents=True, exist_ok=True)
    summary: dict = {"fetch": {}, "gate": None, "runs": []}

    for pid in a.papers:
        dest = corpus / pid.replace("/", "_")
        if dest.is_dir():
            summary["fetch"][pid] = "cached"
            continue
        if a.src:
            shutil.copytree(Path(a.src) / pid.replace("/", "_"), dest)
            summary["fetch"][pid] = "local"
            continue
        log: list = []
        r = run([*ROSETTEX, "fetch", pid, "-o", str(root / "fetch" / dest.name), "--json"], log)
        if r["ok"]:
            shutil.copytree(root / "fetch" / dest.name / "src", dest)
        summary["fetch"][pid] = "arxiv" if r["ok"] else log

    if a.fetch_only:
        print(json.dumps(summary["fetch"], ensure_ascii=False))
        return 0 if all(isinstance(v, str) for v in summary["fetch"].values()) else 1

    log = []
    summary["skill"] = run([sys.executable, "-m", "rosettex.skill", "--check"], log)["ok"] and "ok" or log
    log = []
    summary["mcp"] = mcp_smoke()
    summary["gate"] = run([sys.executable, str(HERE / "roundtrip_gate.py"), str(corpus), "--strict"], log)["ok"] and "ok" or log

    for pid in a.papers:
        src = corpus / pid.replace("/", "_")
        for lang in a.langs:
            work = root / "work" / f"{src.name}-{lang}"
            shutil.rmtree(work, ignore_errors=True)
            log = []
            pdf = work / f"{src.name}.{lang}.pdf"
            steps = [
                [*ROSETTEX, "fetch", str(src), "-o", str(work), "--json"],
                [*ROSETTEX, "prepare", str(work), "--lang", lang, "--json"],
                [sys.executable, str(HERE / "pseudo_translate.py"), str(work), "--lang", lang],
                [*ROSETTEX, "check", str(work), "--json"],
                [*ROSETTEX, "build", str(work), "--out", str(pdf), "--no-agent", "--json"],
            ]
            ok = src.is_dir() and all(run(s, log)["ok"] for s in steps) and pdf.is_file()
            summary["runs"].append({"paper": pid, "lang": lang, "ok": ok, "pdf_bytes": pdf.stat().st_size
                                    if pdf.is_file() else 0, "steps": log})

    failed = [r for r in summary["runs"] if not r["ok"]]
    summary["ok"] = summary["gate"] == "ok" and summary["skill"] == "ok" and summary["mcp"] == "ok" and not failed and len(summary["runs"]) == len(a.papers) * len(a.langs)
    summary["failed"] = [f"{r['paper']}:{r['lang']}" for r in failed]
    (root / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

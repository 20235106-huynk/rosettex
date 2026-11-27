from datetime import datetime, timezone
import re
import shutil
from dataclasses import asdict
from pathlib import Path

from rosettex.jsonio import read_json, write_json
from rosettex.segment import Placeholder, Segment, extract_segments
from rosettex.source import effective_dir, effective_hash, file_encodings, input_tree

OWNED = (
    "project.json",
    "segments.json",
    "read",
    "out",
    "terms.md",
    "unknown.md",
    "review.md",
    "baseline",
    "build",
    "report.json",
    "logs",
    "translations.json",
    "fix",
    "effective",
    "build.json",
    "repair.json",
    "pdf",
)
_ANS_LINE = re.compile(r"^(s\d+)\s+(.*)$")


def save_segments(work: Path, segs: list[Segment]) -> None:
    rows = []
    for s in segs:
        d = asdict(s)
        d["_raw"] = getattr(s, "_raw", "")
        rows.append(d)
    write_json(Path(work) / "segments.json", rows)


def load_segments(work: Path) -> list[Segment]:
    segs = []
    for d in read_json(Path(work) / "segments.json"):
        raw = d.pop("_raw", "")
        d["ph"] = {k: Placeholder(**v) for k, v in d["ph"].items()}
        s = Segment(**d)
        s._raw = raw
        segs.append(s)
    return segs


def _section_units(segs: list[Segment]) -> list[tuple[str, list[list[Segment]]]]:
    children: dict[str, list[Segment]] = {}
    for s in segs:
        if s.parent:
            children.setdefault(s.parent, []).append(s)

    def with_children(s: Segment) -> list[Segment]:
        out = [s]
        for c in children.get(s.id, []):
            out += with_children(c)
        return out

    sections: list[tuple[str, list[list[Segment]]]] = []
    for s in segs:
        if s.parent:
            continue
        key = "" if s.kind == "title" or not s.section else re.sub(r"\s+", " ", s.section).strip()
        if not sections or sections[-1][0] != key:
            sections.append((key, []))
        sections[-1][1].append(with_children(s))
    return sections


def ordered_segments(segs: list[Segment]) -> list[Segment]:
    out: list[Segment] = []
    for _, units in _section_units(segs):
        for unit in units:
            out.extend(unit)
    return out


def duplicate_map(segs: list[Segment]) -> dict[str, str]:
    """Return {dup_id: first_id} for segments whose text matches an earlier segment."""
    seen: dict[str, str] = {}
    dups: dict[str, str] = {}
    for s in ordered_segments(segs):
        first = seen.get(s.text)
        if first is not None:
            dups[s.id] = first
        else:
            seen[s.text] = s.id
    return dups


def _plan_sheets(segs: list[Segment], sheet_chars: int = 30000) -> list[tuple[list[str], list[Segment]]]:
    sheets: list[tuple[list[str], list[Segment]]] = []
    cur_titles: list[str] = []
    cur_segs: list[Segment] = []
    cur_size = 0

    def flush() -> None:
        nonlocal cur_titles, cur_segs, cur_size
        if cur_segs:
            sheets.append((cur_titles, cur_segs))
            cur_titles, cur_segs, cur_size = [], [], 0

    def add_title(title: str) -> None:
        label = title or "Front"
        if label not in cur_titles:
            cur_titles.append(label)

    for title, units in _section_units(segs):
        sec_segs = [x for u in units for x in u]
        sec_size = sum(len(x.text) for x in sec_segs)
        if sec_size <= sheet_chars:
            if cur_segs and cur_size + sec_size > sheet_chars:
                flush()
            add_title(title)
            cur_segs.extend(sec_segs)
            cur_size += sec_size
        else:
            flush()
            for unit in units:
                unit_size = sum(len(x.text) for x in unit)
                if cur_segs and cur_size + unit_size > sheet_chars:
                    flush()
                add_title(title)
                cur_segs.extend(unit)
                cur_size += unit_size
            flush()
    flush()
    return sheets


def _legend(s: Segment, suspects: set[str] | None = None, *, include_links: bool = False) -> str:
    prefixes = ("m", "c", "l") if include_links else ("m", "c")
    items = []
    for code, p in s.ph.items():
        if p.child or not code.startswith(prefixes):
            continue
        shown = re.sub(r"\s+", " ", p.raw or (p.open + "…" + p.close)).strip()
        label = f"{code}?" if suspects and code in suspects else code
        items.append(f"{label} {shown[:40]}")
    return f"  ~ {' · '.join(items)}" if items else ""


def write_sheets(
    work: Path,
    segs: list[Segment],
    lang: str,
    sheet_chars: int = 30000,
    *,
    pdf_meta: dict[str, dict] | None = None,
) -> list[str]:
    del lang
    work = Path(work)
    read_dir = work / "read"
    out_dir = work / "out"
    read_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in read_dir.glob("*.md"):
        old.unlink()

    dups = duplicate_map(segs)
    names: list[str] = []
    for idx, (titles, group) in enumerate(_plan_sheets(segs, sheet_chars=sheet_chars), 1):
        nn = f"{idx:02d}"
        title_str = ", ".join(titles)
        lines = [f"# {nn} · {title_str} ({group[0].id}–{group[-1].id}) · write answers to out/{nn}.txt"]
        for s in group:
            if s.id in dups:
                lines.append(f"{s.id} = {dups[s.id]}")
                continue
            text = re.sub(r"\s+", " ", s.text).strip()
            meta = pdf_meta.get(s.id) if pdf_meta is not None else None
            suspects = meta.get("suspects") if meta else None
            if suspects:
                for code in suspects:
                    text = text.replace(f"⟦{code}⟧", f"⟦{code}?⟧")
            lines.append(f"{s.id} {text}")
            if meta:
                parts = []
                if meta.get("page"):
                    parts.append(f"p.{meta['page']}")
                if meta.get("budget"):
                    parts.append(f"≤ {meta['budget']} chars")
                if parts:
                    lines.append(f"  ~ {' · '.join(parts)}")
            leg = _legend(s, suspects=suspects, include_links=pdf_meta is not None)
            if leg:
                lines.append(leg)
        (read_dir / f"{nn}.md").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
        ans_path = out_dir / f"{nn}.txt"
        if not ans_path.exists():
            ans_path.write_text("", encoding="utf-8", newline="\n")
        names.append(f"read/{nn}.md")
    return names


def read_answers(work: Path, segs: list[Segment]) -> tuple[dict[str, str], list[dict]]:
    out_dir = Path(work) / "out"
    if not out_dir.is_dir():
        return {}, []
    known_ids = {s.id for s in segs}
    dups = duplicate_map(segs)
    tr: dict[str, str] = {}
    errors: list[dict] = []

    for path in sorted(out_dir.glob("*.txt")):
        rel = f"out/{path.name}"
        for lineno, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            m = _ANS_LINE.match(line)
            if not m:
                errors.append(
                    {
                        "file": rel,
                        "line": lineno,
                        "code": "bad_line",
                        "message": f"expected 'sNNNN <translation>', got: {stripped[:60]}",
                    }
                )
                continue
            sid, val = m.group(1), m.group(2).strip()
            if sid not in known_ids:
                errors.append(
                    {
                        "file": rel,
                        "line": lineno,
                        "code": "unknown_id",
                        "message": f"unknown segment id {sid}",
                    }
                )
                continue
            if sid in dups:
                errors.append(
                    {
                        "file": rel,
                        "line": lineno,
                        "code": "duplicate_of",
                        "message": f"{sid} is a duplicate of {dups[sid]}; do not translate it",
                    }
                )
                continue
            if val:
                tr[sid] = val
            else:
                tr.pop(sid, None)

    for dup_id, orig_id in dups.items():
        if orig_id in tr:
            tr[dup_id] = tr[orig_id]
    return tr, errors


def save_translations(work: Path, segs: list[Segment], translations: dict[str, str]) -> None:
    data: dict[str, dict[str, str]] = {}
    for s in segs:
        text = translations.get(s.id, "").strip()
        if text:
            data[s.id] = {"text": text, "hash": s.hash}
    write_json(Path(work) / "translations.json", data)


def load_translations(work: Path) -> dict[str, dict[str, str]]:
    p = Path(work) / "translations.json"
    if not p.is_file():
        return {}
    return read_json(p)


def carry_over(old_segs: list[Segment], old_tr: dict[str, str], new_segs: list[Segment]) -> dict[str, str]:
    old_by_id = {s.id: s for s in old_segs}
    by_hash: dict[str, list[str]] = {}
    for s in old_segs:
        if old_tr.get(s.id):
            by_hash.setdefault(s.hash, []).append(old_tr[s.id])
    out = {}
    for s in new_segs:
        o = old_by_id.get(s.id)
        if o is not None and o.hash == s.hash and old_tr.get(o.id):
            out[s.id] = old_tr[o.id]
        elif len(set(by_hash.get(s.hash, []))) == 1:
            out[s.id] = by_hash[s.hash][0]
    return out


def archive_and_write_carried(
    work: Path, new_segs: list[Segment], carried: dict[str, str]
) -> Path | None:
    """Move existing out/*.txt to out/.old/<timestamp>/ and write carried translations to out/00-carried.txt."""
    out_dir = Path(work) / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    existing = sorted(out_dir.glob("*.txt"))
    archive_dir: Path | None = None
    if existing:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%f")
        archive_dir = out_dir / ".old" / stamp
        archive_dir.mkdir(parents=True, exist_ok=True)
        for p in existing:
            shutil.move(str(p), str(archive_dir / p.name))

    dups = duplicate_map(new_segs)
    to_write: dict[str, str] = {}
    for s in ordered_segments(new_segs):
        tr = carried.get(s.id, "").strip()
        if not tr:
            continue
        canon = dups.get(s.id, s.id)
        to_write.setdefault(canon, re.sub(r"\s+", " ", tr))

    if to_write:
        lines = [f"{s.id} {to_write[s.id]}" for s in ordered_segments(new_segs) if s.id in to_write]
        (out_dir / "00-carried.txt").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return archive_dir


def write_review(
    work: Path, segs: list[Segment], translations: dict[str, str], path: Path | None = None
) -> Path:
    out_path = Path(path) if path is not None else Path(work) / "review.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    blocks: list[str] = []
    for s in ordered_segments(segs):
        tr = translations.get(s.id, "").strip() or "—"
        blocks.append(f"**{s.id}** ({s.kind})\n> {s.text}\n\n{tr}")
    out_path.write_text("\n\n".join(blocks) + ("\n" if blocks else ""), encoding="utf-8", newline="\n")
    return out_path


def write_terms(work: Path, candidates: list[str]) -> None:
    p = Path(work) / "terms.md"
    if p.exists():
        return
    rows = ["| term | translation | note |", "|---|---|---|"] + [f"| {c} |  |  |" for c in candidates]
    p.write_text(
        "<!-- fill the translation column; '=' keeps the term as is; empty rows are ignored -->\n\n"
        + "\n".join(rows) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def read_terms(work: Path) -> list[tuple[str, str]]:
    p = Path(work) / "terms.md"
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8-sig").splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 2 or cells[0] in ("term", "") or set(cells[0]) <= {"-"}:
            continue
        if cells[1]:
            out.append((cells[0], cells[1]))
    return out


def write_unknown(work: Path, unknown: list[dict]) -> None:
    lines = [
        "<!-- commands/environments rosettex keeps untouched. To translate one, change 'keep' to 'text' "
        "(and rerun prepare) -->",
        "",
        "| name | count | file | example | action |",
        "|---|---|---|---|---|",
    ]
    for u in sorted(unknown, key=lambda u: -u["count"]):
        ex = u["example"].replace("|", "\\|").replace("\n", " ")[:60]
        lines.append(f"| {u['name']} | {u['count']} | {u['file']} | `{ex}` | {u.get('action', 'keep')} |")
    (Path(work) / "unknown.md").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def sync_effective(work: Path) -> dict:
    """Re-extract segments and carry over translations if effective source changed."""
    work = Path(work)
    proj_path = work / "project.json"
    if not proj_path.is_file() or not (work / "segments.json").is_file():
        return {"resynced": False, "kept": 0, "lost": 0}
    proj = read_json(proj_path)
    if proj.get("route") == "pdf" or not proj.get("lang") or not proj.get("main"):
        return {"resynced": False, "kept": 0, "lost": 0}
    prev_hash = proj.get("effective_hash", proj.get("source_hash"))
    cur_hash = effective_hash(work, proj.get("source_hash"))
    if cur_hash == prev_hash:
        return {"resynced": False, "kept": 0, "lost": 0}
    eff = effective_dir(work)
    files = input_tree(eff, proj["main"])
    proj["files"] = files
    proj["encodings"] = file_encodings(eff, files, proj["main"])
    proj["effective_hash"] = cur_hash
    old_segs = load_segments(work)
    old_tr: dict[str, str] = {}
    for sid, entry in load_translations(work).items():
        if isinstance(entry, dict) and entry.get("text"):
            old_tr[sid] = entry["text"]
    if (work / "out").is_dir():
        ans_tr, _ = read_answers(work, old_segs)
        old_tr.update(ans_tr)
    old_translated = sum(1 for s in old_segs if old_tr.get(s.id))
    new_segs, unknown = extract_segments(eff, proj)
    new_tr = carry_over(old_segs, old_tr, new_segs)
    save_segments(work, new_segs)
    archive_and_write_carried(work, new_segs, new_tr)
    write_sheets(work, new_segs, proj["lang"])
    save_translations(work, new_segs, new_tr)
    write_unknown(work, unknown)
    write_json(proj_path, proj)
    kept = sum(1 for s in new_segs if new_tr.get(s.id))
    lost = max(0, old_translated - kept)
    return {"resynced": True, "kept": kept, "lost": lost}

import shutil
from pathlib import Path

from rosettex.jsonio import write_json
from rosettex.segment import Segment, _has_translated_child, render
from rosettex.textio import read_source, write_text


def splice(text: str, edits: list[tuple[int, int, str]]) -> str:
    edits = sorted(edits, key=lambda e: e[0])
    for (s1, e1, _), (s2, _, _) in zip(edits, edits[1:]):
        if s2 < e1:
            raise ValueError(f"overlapping edits at {s1}-{e1} and {s2}")
    for s, e, rep in reversed(edits):
        text = text[:s] + rep + text[e:]
    return text


def assemble_tree(
    src_dir: Path,
    out_dir: Path,
    project: dict,
    segments: list[Segment],
    translations: dict[str, str],
    keep_source: set[str] = frozenset(),
) -> dict:
    src_dir, out_dir = Path(src_dir), Path(out_dir)
    if out_dir.exists():
        shutil.rmtree(out_dir)
    shutil.copytree(src_dir, out_dir, symlinks=False)
    by_id = {s.id: s for s in segments}
    effective = {k: v for k, v in translations.items() if k not in keep_source and v}
    per_file: dict[str, list[tuple[int, int, str, str]]] = {}
    applied = 0
    for s in segments:
        if s.parent is not None:
            continue
        if s.id in effective or _has_translated_child(s, effective, by_id):
            per_file.setdefault(s.file, []).append((s.start, s.end, render(s, effective, by_id), s.id))
            applied += 1
    written = []
    pos_map: dict[str, dict] = {"files": {}}
    encodings = project.get("encodings") or {}
    for rel in project["files"]:
        text, enc = read_source(src_dir / rel, encodings.get(rel))
        sorted_edits = sorted(per_file.get(rel, []), key=lambda e: e[0])
        assembled = splice(text, [(s, e, rep) for s, e, rep, _ in sorted_edits])
        delta = 0
        map_edits = []
        for s, e, rep, seg_id in sorted_edits:
            out_s = s + delta
            out_e = out_s + len(rep)
            map_edits.append({"seg": seg_id, "src": [s, e], "out": [out_s, out_e]})
            delta += len(rep) - (e - s)
        pos_map["files"][rel] = {"source": rel, "edits": map_edits}
        write_text(out_dir / f"{rel}.assembled", assembled)
        if sorted_edits or enc != "utf-8":
            write_text(out_dir / rel, assembled)
            written.append(rel)
    main_enc = encodings.get(project["main"], "utf-8")
    if main_enc not in ("utf-8", "utf-8-sig"):  # 8-bit source: the build copy is all UTF-8 (B sets inputenc)
        for p in sorted(out_dir.rglob("*")):
            rel = p.relative_to(out_dir).as_posix()
            if p.suffix in (".bbl", ".tex") and p.is_file() and rel not in project["files"]:
                text, enc = read_source(p, main_enc)
                if enc not in ("utf-8", "utf-8-sig"):
                    write_text(p, text)
                    written.append(rel)
    write_json(out_dir / "rosettex-map.json", pos_map)
    return {"files_written": written, "segments_applied": applied, "map": pos_map}

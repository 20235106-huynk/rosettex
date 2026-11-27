import hashlib
import json
import re
import shutil
from collections import Counter
from importlib import resources
from pathlib import Path

from rosettex import arxiv
from rosettex.jsonio import read_json, write_json
from rosettex.textio import read_source
from rosettex.unpack import UnpackError, kind, unpack

_DOCCLASS = re.compile(r"^[^%\n]*\\document(?:class|style)", re.M)
_BEGIN_DOC = re.compile(r"^[^%\n]*\\begin\s*\{document\}", re.M)
_INPUT = re.compile(r"\\(?:input|include|subfile)\s*\{([^}]+)\}")
_PREFERRED = ("main.tex", "ms.tex", "paper.tex")
_MATERIALISED: dict[Path, str] = {}


def strip_comments(text: str) -> str:
    """Remove TeX comments (an unescaped % to end of line), keeping line structure."""
    return re.sub(r"(?<!\\)((?:\\\\)*)%[^\n]*", r"\1", text)


def _tex_files(src_dir: Path) -> list[str]:
    return sorted(p.relative_to(src_dir).as_posix() for p in src_dir.rglob("*.tex") if p.is_file())


def find_main(src_dir: Path) -> tuple[str | None, list[str]]:
    cands = []
    for rel in _tex_files(src_dir):
        text, _ = read_source(src_dir / rel)
        if _DOCCLASS.search(text) and _BEGIN_DOC.search(text):
            cands.append(rel)
    if len(cands) == 1:
        return cands[0], cands
    for name in _PREFERRED:
        if name in cands:
            return name, cands
    return None, cands


def _resolve(src_dir: Path, base: str, arg: str) -> str | None:
    arg = arg.strip()
    src_root = src_dir.resolve()
    for cand in (arg, arg + ".tex"):
        for root in (Path(base).parent, Path(".")):
            p = src_dir / root / cand
            if p.is_file() and p.suffix == ".tex":
                resolved = p.resolve()
                if resolved.is_relative_to(src_root):
                    return resolved.relative_to(src_root).as_posix()
    return None


def input_tree(src_dir: Path, main: str) -> list[str]:
    order: list[str] = []

    def visit(rel: str) -> None:
        if rel in order:
            return
        order.append(rel)
        text, _ = read_source(src_dir / rel)
        for m in _INPUT.finditer(strip_comments(text)):
            child = _resolve(src_dir, main, m.group(1))
            if child:
                visit(child)

    visit(main)
    return order


def file_encodings(src_dir: Path, files: list[str], main: str | None) -> dict[str, str]:
    """rel → original encoding; an \\input file that declares none inherits the main file's (V report D1)."""
    main_enc = read_source(src_dir / main)[1] if main else None
    return {rel: read_source(src_dir / rel, main_enc)[1] for rel in files}


def _hash_tree(src_dir: Path) -> str:
    h = hashlib.sha1()
    for p in sorted(src_dir.rglob("*")):
        if p.is_file():
            h.update(p.relative_to(src_dir).as_posix().encode("utf-8"))
            h.update(p.read_bytes())
    return h.hexdigest()


def overlay_files(work: Path) -> list[str]:
    fix_dir = Path(work) / "fix"
    if not fix_dir.is_dir():
        return []
    root = fix_dir.resolve()
    out: list[str] = []
    for p in sorted(fix_dir.rglob("*")):
        if p.is_file() and not p.is_symlink():
            resolved = p.resolve()
            if resolved.is_relative_to(root):
                out.append(resolved.relative_to(root).as_posix())
    return out


def overlay_hash(work: Path) -> str:
    fix_dir = Path(work) / "fix"
    rels = overlay_files(work)
    if not rels:
        return ""
    h = hashlib.sha1()
    for rel in rels:
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        h.update((fix_dir / rel).read_bytes())
        h.update(b"\0")
    return h.hexdigest()


def effective_hash(work: Path, source_hash: str | None = None) -> str:
    work = Path(work)
    if source_hash is None:
        proj_path = work / "project.json"
        if proj_path.is_file():
            source_hash = read_json(proj_path).get("source_hash")
        if not source_hash:
            source_hash = _hash_tree(work / "src")
    ov = overlay_hash(work)
    if not ov:
        return source_hash
    return hashlib.sha1(f"{source_hash}\0{ov}".encode("utf-8")).hexdigest()


def effective_dir(work: Path) -> Path:
    work = Path(work)
    src = work / "src"
    eff = work / "effective"
    rels = overlay_files(work)
    key = work.resolve()
    if not rels:
        if eff.exists():
            shutil.rmtree(eff)
        _MATERIALISED.pop(key, None)
        return src
    proj_path = work / "project.json"
    proj = read_json(proj_path) if proj_path.is_file() else {}
    cur_hash = effective_hash(work, proj.get("source_hash"))
    if eff.is_dir() and (proj.get("effective_hash") == cur_hash or _MATERIALISED.get(key) == cur_hash):
        return eff
    if eff.exists():
        shutil.rmtree(eff)
    shutil.copytree(src, eff, symlinks=False)
    fix_dir = work / "fix"
    eff_root = eff.resolve()
    for rel in rels:
        dst = (eff / rel).resolve()
        if not dst.is_relative_to(eff_root):
            raise ValueError(f"overlay path escapes effective dir: {rel}")
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(fix_dir / rel, dst)
    _MATERIALISED[key] = cur_hash
    return eff


def _readme_main(src_dir: Path) -> tuple[str | None, dict]:
    """arXiv 00README.json (toplevel source, compiler, texlive_version) or legacy 00README.XXX."""
    j = src_dir / "00README.json"
    if j.is_file():
        try:
            data = json.loads(j.read_text(encoding="utf-8-sig"))
        except (ValueError, UnicodeDecodeError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        sources = data.get("sources")
        tops = [s.get("filename") for s in (sources if isinstance(sources, list) else [])
                if isinstance(s, dict) and s.get("usage") == "toplevel"]
        proc = data.get("process")
        hint = {"compiler": proc.get("compiler") if isinstance(proc, dict) else None,
                "texlive_version": data.get("texlive_version")}
        return (tops[0] if tops else None), {k: v for k, v in hint.items() if v}
    x = src_dir / "00README.XXX"
    if x.is_file():
        for line in read_source(x)[0].splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[1].lower() == "toplevelfile":
                return parts[0], {}
    return None, {}


_UNICODE_PKG = re.compile(r"\\(?:usepackage|RequirePackage)\s*(?:\[[^\]]*\])?\s*\{([^}]*)\}")
_PRODUCER = re.compile(rb"/Producer\s*\(((?:[^()\\]|\\.){1,200})\)")


def engine_hints(src_dir: Path, files: list[str], readme_hint: dict, orig_pdf: Path | None) -> dict:
    hint = dict(readme_hint)
    text = "\n".join(strip_comments(read_source(src_dir / f)[0]) for f in files)
    pkgs = {p.strip() for m in _UNICODE_PKG.finditer(text) for p in m.group(1).split(",")}
    uni = sorted(pkgs & {"fontspec", "polyglossia", "xeCJK", "luacode", "unicode-math"})
    if re.search(r"\\if(?:luatex|xetex)\b", text):
        uni.append("\\ifluatex/\\ifxetex")
    if uni:
        hint["unicode_engine"] = uni
    if re.search(r"\\pdfoutput\s*=?\s*1", text):
        hint["pdfoutput"] = True
    exts = {p.suffix.lower() for p in src_dir.rglob("*") if p.is_file()}
    pst = sorted(p for p in pkgs if p == "pstricks" or p.startswith("pst-"))
    if pst or (".eps" in exts or ".ps" in exts) and not exts & {".pdf", ".png", ".jpg", ".jpeg"}:
        hint["dvips"] = pst or ["eps figures only"]
    if orig_pdf and orig_pdf.is_file():
        m = _PRODUCER.search(orig_pdf.read_bytes())
        if m:
            hint["producer"] = m.group(1).decode("latin-1")
    return hint


def _contained(src_dir: Path, name) -> bool:
    """name is a relative path to a file inside src_dir (no absolute path, no '..', no symlink out)."""
    if not isinstance(name, str) or not name or Path(name).is_absolute() or ".." in Path(name).parts:
        return False
    p = src_dir / name
    return p.is_file() and p.resolve().is_relative_to(src_dir.resolve())


def _place(src: Path, dest: Path) -> str:
    """Copy a local dir, or unpack an archive / .tex / gzip into dest (removed on refusal)."""
    if src.resolve().is_relative_to(dest.resolve()):
        raise UnpackError("unsafe_input", f"{src} is the work dir's own src/ (or inside it); pass the original source")
    if dest.exists():
        shutil.rmtree(dest)
    if src.is_dir():
        shutil.copytree(src, dest, symlinks=False)
        return "dir"
    try:
        return unpack(src, dest)
    except Exception:
        shutil.rmtree(dest, ignore_errors=True)
        raise


def fetch(arg: str, work: Path, main: str | None = None) -> dict:
    """Local dir/archive/.tex/gzip/.pdf, or an arXiv ID/link → work/src (or work/orig.pdf) + work/project.json.

    Raises UnpackError / ArxivError (with .code). project['main'] is None when ambiguous or PDF-only."""
    work = Path(work)
    dest = work / "src"
    record = None
    is_pdf_only = False
    stem: str | None = None
    if Path(arg).exists():
        p = Path(arg)
        if p.is_file() and p.suffix.lower() == ".pdf" and kind(p) != "pdf":
            raise UnpackError("not_pdf", f"{p.name}: not a valid PDF file")
        if p.is_file() and kind(p) == "pdf":
            work.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(p, work / "orig.pdf")
            is_pdf_only = True
            stem = p.stem
        else:
            k = _place(p, dest)
            if k == "pdf":
                work.mkdir(parents=True, exist_ok=True)
                if (dest / "orig.pdf").is_file():
                    shutil.move(str(dest / "orig.pdf"), str(work / "orig.pdf"))
                shutil.rmtree(dest, ignore_errors=True)
                is_pdf_only = True
                stem = p.stem
    else:
        parsed = arxiv.parse(arg)
        if not parsed:
            raise UnpackError("not_found", f"not a file, directory or arXiv ID/link: {arg}")
        rec = arxiv.fetch(*parsed, work / "dl")
        pdf, eprint = rec.pop("pdf"), rec.pop("e-print")
        work.mkdir(parents=True, exist_ok=True)
        if pdf:
            shutil.copyfile(pdf, work / "orig.pdf")
        if kind(eprint) == "pdf":
            if not (work / "orig.pdf").is_file():
                shutil.copyfile(eprint, work / "orig.pdf")
            is_pdf_only = True
            stem = f"{rec['id'].replace('/', '_')}v{rec['version']}"
        else:
            k = _place(eprint, dest)
            if k == "pdf":
                if not (work / "orig.pdf").is_file() and (dest / "orig.pdf").is_file():
                    shutil.move(str(dest / "orig.pdf"), str(work / "orig.pdf"))
                shutil.rmtree(dest, ignore_errors=True)
                is_pdf_only = True
                stem = f"{rec['id'].replace('/', '_')}v{rec['version']}"
        record = rec
    if is_pdf_only:
        src_hash = hashlib.sha1((work / "orig.pdf").read_bytes()).hexdigest()
        project = {
            "route": "pdf",
            "main": None,
            "stem": stem or "orig",
            "candidates": [],
            "files": [],
            "encodings": {},
            "source_hash": src_hash,
            "effective_hash": src_hash,
            "engine_hint": {},
        }
        if record:
            project["arxiv"] = record
        write_json(work / "project.json", project)
        return project
    readme_main, readme_hint = _readme_main(dest)
    found, cands = find_main(dest)
    if main is None:
        main = readme_main if _contained(dest, readme_main) else found
    elif not _contained(dest, main):
        raise UnpackError("not_found", f"--main {main} is not a file in {dest}")
    if not cands:
        cands = _tex_files(dest)
    files = input_tree(dest, main) if main else []
    src_hash = _hash_tree(dest)
    project = {
        "route": "tex",
        "main": main,
        "candidates": cands,
        "files": files,
        "encodings": file_encodings(dest, files, main),
        "source_hash": src_hash,
        "effective_hash": src_hash,
        "engine_hint": engine_hints(dest, files, readme_hint, work / "orig.pdf" if record else None),
    }
    if record:
        project["arxiv"] = record
    write_json(work / "project.json", project)
    return project


def fetch_local(src: Path, work: Path) -> dict:
    return fetch(str(src), work)


# ---- source language detection (spec §10.1) ---------------------------------------------------------------
_PKG = re.compile(r"\\(?:usepackage|RequirePackage)\s*(?:\[([^\]]*)\])?\s*\{([^}]*)\}")
_CLASS = re.compile(r"\\document(?:class|style)\s*(?:\[([^\]]*)\])?\s*\{([^}]*)\}")
_MAINLANG = re.compile(r"\\(?:setmainlanguage|setdefaultlanguage)\s*(?:\[[^\]]*\])?\s*\{([^}]*)\}")
_BABELPROVIDE = re.compile(r"\\babelprovide\s*\[([^\]]*)\]\s*\{([^}]*)\}")
_CJK_ENV = re.compile(r"\\begin\s*\{CJK\*?\}\s*(?:\[[^\]]*\])?\s*\{[^}]*\}\s*\{([^}]*)\}")
_JA_CLASS = re.compile(r"(?:ltjs?|bxjs|js|uj|j|ut|t)(?:article|book|report|slide)")
_CLASS_LANG = {"ctexart": "zh", "ctexrep": "zh", "ctexbook": "zh", "ctexbeamer": "zh", "oblivoir": "ko"}
# packages that set the document language (like babel)
_PKG_LANG = {"ctexcap": "zh", "ngerman": "de", "german": "de", "french": "fr"}
# packages that only make a script available: they count when the text's script agrees
_PKG_SCRIPT = {"ctex": "cjk", "xeCJK": "cjk", "CJKutf8": "cjk", "CJK": "cjk", "luatexja": "ja", "luatexja-fontspec": "ja",
               "luatexja-preset": "ja", "bxcjkjatype": "ja", "zxjatype": "ja", "pxjahyper": "ja", "otf": "ja",
               "kotex": "ko", "xetexko": "ko", "luatexko": "ko", "vntex": "vi", "vietnam": "vi"}
_CJK_FAMILY = {"gbsn": "zh", "gkai": "zh", "song": "zh", "kai": "zh", "bsmi": "zh-Hant", "bkai": "zh-Hant",
               "min": "ja", "goth": "ja", "maru": "ja", "mj": "ko", "gt": "ko"}
_SCRIPT_DEFAULT = {"cyrillic": "ru", "arabic": "ar", "devanagari": "hi", "bengali": "bn", "ethiopic": "am"}
_WORD = re.compile(r"[^\W\d_]+")
_PH_TOKEN = re.compile(r"⟦/?[a-z]\d+⟧")


def _lang_tables() -> tuple[dict[str, str], dict[str, dict[str, set[str]]], dict[str, str]]:
    """babel/polyglossia name → code; script → {code: function words}; code → script."""
    from rosettex.langdata import load_lang  # noqa: PLC0415 (langdata is light, keep the import local)

    names: dict[str, str] = {}
    words: dict[str, dict[str, set[str]]] = {}
    script: dict[str, str] = {}
    langs_dir = resources.files("rosettex.data").joinpath("langs")
    for item in sorted(langs_dir.iterdir(), key=lambda p: (len(p.name), p.name)):
        if not item.name.endswith(".json"):
            continue
        d = json.loads(item.read_text(encoding="utf-8"))
        code = d.get("code") or item.name[:-5]
        if "alias" in d:
            d = {**load_lang(d["alias"]), **d}
        script[code] = d.get("script", "latin")
        for n in [d.get("babel"), *d.get("babel_aliases", [])]:
            if n:
                names.setdefault(n.lower(), code)
        if d.get("function_words"):
            words.setdefault(script[code], {})[code] = set(d["function_words"])
    return names, words, script


def _blocks(profile: dict) -> list[tuple[int, int]]:
    out = []
    for b in profile.get("blocks", []):
        lo, _, hi = b.partition("-")
        out.append((int(lo, 16), int(hi or lo, 16)))
    return out


def _stats(segs, words: dict[str, dict[str, set[str]]]) -> tuple[str | None, bool, str]:
    """Script statistics of the segment text → (code, strong, description)."""
    from rosettex.langdata import load_profile  # noqa: PLC0415

    others = []
    for sid in ("cyrillic", "arabic", "devanagari", "bengali", "tamil", "telugu", "kannada", "gujarati", "thai",
                "myanmar", "ethiopic"):
        try:
            others.append((sid, _blocks(load_profile(sid))))
        except ValueError:
            pass
    counts: Counter[str] = Counter()
    text = " ".join(_PH_TOKEN.sub(" ", s.text) for s in segs)
    for c in text:
        if not c.isalpha():
            continue
        o = ord(c)
        if 0xAC00 <= o <= 0xD7AF or 0x1100 <= o <= 0x11FF or 0x3130 <= o <= 0x318F:
            counts["hangul"] += 1
        elif 0x3040 <= o <= 0x30FF or 0x31F0 <= o <= 0x31FF or 0xFF66 <= o <= 0xFF9D:
            counts["kana"] += 1
        elif 0x3400 <= o <= 0x9FFF or 0xF900 <= o <= 0xFAFF or 0x20000 <= o <= 0x3134F:
            counts["han"] += 1
        elif o <= 0x24F or 0x1E00 <= o <= 0x1EFF:
            counts["latin"] += 1
        else:
            counts[next((sid for sid, bl in others if any(lo <= o <= hi for lo, hi in bl)), "other")] += 1
    total = sum(counts.values())
    desc = "stats: " + " ".join(f"{k} {v}" for k, v in counts.most_common()) if total else "stats: no letters"
    if total < 30:
        return None, False, desc
    cjk = counts["han"] + counts["kana"] + counts["hangul"]
    if cjk >= 0.3 * total:
        if counts["hangul"] > counts["kana"] and counts["hangul"] >= 0.3 * cjk:
            return "ko", True, desc
        return ("ja" if counts["kana"] >= 0.05 * cjk else "zh"), True, desc
    top, n = next(((k, v) for k, v in counts.most_common() if k not in ("han", "kana", "hangul", "other")),
                  ("latin", 0))
    if top != "latin" and n >= 0.3 * total:
        return _SCRIPT_DEFAULT.get(top), False, desc  # one script, several languages: weak on its own
    # Latin: function words that only one language has
    toks = [w.lower() for w in _WORD.findall(text)]
    langs = words.get("latin", {})
    hits = {}
    for code, fw in langs.items():
        own = fw - set().union(*(v for k, v in langs.items() if k != code))
        hits[code] = sum(t in own for t in toks)
    ranked = sorted(hits.items(), key=lambda kv: -kv[1])
    desc += " | function words: " + " ".join(f"{k} {v}" for k, v in ranked if v)
    if not ranked or ranked[0][1] == 0:
        return "en", False, desc
    best, second = ranked[0][1], ranked[1][1] if len(ranked) > 1 else 0
    return ranked[0][0], best >= 20 and best >= 2 * second, desc


def detect_lang(src_dir: Path | None, files: list[str], segs) -> dict:
    """Source language (spec §10.1): markup signals, then script statistics of the segment text.

    Returns {source_lang, source_lang_signals, confident, options}; not confident → the caller asks the user."""
    names, words, script = _lang_tables()
    stat, strong, desc = _stats(segs, words)
    texts = [strip_comments(read_source(src_dir / f)[0]) for f in files] if src_dir and files else []
    local = (
        [
            strip_comments(read_source(p)[0])
            for p in sorted(src_dir.rglob("*"))
            if p.suffix in (".cls", ".sty", ".rtx", ".clo") and p.is_file()
        ]
        if src_dir and src_dir.is_dir()
        else []
    )
    signals: list[str] = []
    defining: list[str] = []
    support: list[str] = []
    text = "\n".join(texts)
    for m in _CLASS.finditer(text):
        cls, opts = m.group(2).strip(), [o.strip().lower() for o in (m.group(1) or "").split(",")]
        code = _CLASS_LANG.get(cls) or ("ja" if _JA_CLASS.fullmatch(cls) else None)
        if m.group(0).startswith("\\documentstyle") and "tcvn" in opts:
            code = "vi"
        if code:
            defining.append(code)
            signals.append(f"class {cls} → {code}")
    for m in _PKG.finditer(text):
        opts = [o.strip() for o in (m.group(1) or "").split(",") if o.strip()]
        for pkg in (p.strip() for p in m.group(2).split(",")):
            if pkg == "babel":
                main = [o.split("=", 1)[1].strip() for o in opts if o.startswith("main=")]
                cands = [names[o.lower()] for o in main + opts if o.lower() in names]
                if cands:
                    if len(set(cands)) > 1 and strong and stat in cands:
                        code = stat
                    else:
                        code = cands[0] if main else cands[-1]
                    defining.append(code)
                    signals.append(f"babel [{','.join(opts)}] → {code}")
            elif pkg in _PKG_LANG:
                defining.append(_PKG_LANG[pkg])
                signals.append(f"package {pkg} → {_PKG_LANG[pkg]}")
            elif pkg in _PKG_SCRIPT:
                support.append(_PKG_SCRIPT[pkg])
                signals.append(f"package {pkg} → {_PKG_SCRIPT[pkg]} (script)")
    for m in _MAINLANG.finditer(text):
        code = names.get(m.group(1).strip().lower())
        if code:
            defining.append(code)
            signals.append(f"polyglossia {m.group(1).strip()} → {code}")
    for m in _BABELPROVIDE.finditer(text):
        code = names.get(m.group(2).strip().lower())
        if code and "main" in m.group(1):
            defining.append(code)
            signals.append(f"babelprovide[main] {m.group(2).strip()} → {code}")
    for m in _CJK_ENV.finditer(text):
        code = _CJK_FAMILY.get(m.group(1).strip())
        if code:
            support.append(code)
            signals.append(f"CJK env family {m.group(1).strip()} → {code} (script)")
    for t in local:
        for m in _PKG.finditer(t):
            for pkg in (p.strip() for p in m.group(2).split(",")):
                if _PKG_SCRIPT.get(pkg) in ("ko", "ja", "cjk") or pkg == "ctexcap":
                    code = _PKG_SCRIPT.get(pkg) or "zh"
                    support.append(code)
                    signals.append(f"local style loads {pkg} → {code} (script)")
    signals.append(desc + (f" → {stat}{'' if strong else ' (weak)'}" if stat else ""))

    def agrees(code: str) -> bool:
        if stat is None:
            return True
        if script.get(code) != script.get(stat):
            return False
        return script.get(code) != "latin" or not strong or code == stat

    def result(code: str, confident: bool, options=()) -> dict:
        opts = list(dict.fromkeys([code, *options]))
        return {"source_lang": code, "source_lang_signals": signals, "confident": confident, "options": opts}

    if defining:
        agree = list(dict.fromkeys(c for c in defining if agrees(c)))
        if len(agree) == 1:
            return result(agree[0], True)
        return result((agree or defining)[-1], False, [*defining, *([stat] if stat else [])])
    if stat is not None:
        resolved = list(dict.fromkeys(
            stat if c == "cjk" and script.get(stat) in ("han", "kana", "hangul") else c for c in support
        ))
        agree = [c for c in resolved if agrees(c) and (script.get(c) != "latin" or c == stat)]
        if len(agree) == 1:
            return result(agree[0], True)
        if strong:
            return result(stat, True)
        return result(stat, False, ["en"])
    return result("en", True)

"""Rebuild rosettex/fonts/ from the official font releases (dev-only; S1b §6.1).

    uv run --with fonttools==4.68.0 python scripts/build_fonts.py --cache <dir> [--out rosettex/fonts]

Each source zip is read from --cache, downloaded there if missing, and checked against its sha256.
On a machine where Python downloads hang (broken IPv6), fetch the zips first with
`curl -4 -L -o <cache>/<name> <url>`; the script prints the URLs it needs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

N = "https://github.com/notofonts"
UNIHAN = ("https://www.unicode.org/Public/18.0.0/ucd/Unihan.zip",
          "4c93ea9c1f636451729a840978f1667a53886af37ba854fdcce109721c63d43e")
INDIC = "0020-007E,00A0-00FF,2000-206F,200C-200D,25CC,20AC,0964-0965"
ARABIC = "0020-007E,00A0-00FF,2000-206F,25CC,0600-06FF,0750-077F,08A0-08FF,FB50-FDFF,FE70-FEFF"
COMMON_OPTS = ["--layout-features=*", "--name-IDs=*", "--name-languages=*", "--notdef-outline", "--no-hinting"]
SERIF = {  # script: (version, zip sha256, blocks)
    "Bengali": ("v3.000", "3ee7ed1a48f115298ec7fd42d3d72c51fcdca6e7adceae65a0fa4f652b164795", "0980-09FF"),
    "Devanagari": ("v2.006", "b823456dfaf6ef8b438eeb906da42effe01927add1e3416ecc86c99c5f1cf098", "0900-097F,A8E0-A8FF"),
    "Ethiopic": ("v2.102", "2cd163ed52f333c26fc2a55822ab1cabe875b782d73f9d55dfe5863d9e236c26", "1200-139F,2D80-2DDF,AB00-AB2F"),
    "Gujarati": ("v2.106", "614847e5648483a32987a7af583a8c8311b5b8926140778c10820baf1fa4e212", "0A80-0AFF"),
    "Kannada": ("v2.005", "9d516560f21f4ca8dc35d49e7f64f67b40e9e41bddf5aa2e212c52ee9f985779", "0C80-0CFF"),
    "Myanmar": ("v2.106", "dc425f27d119c763c0f567c938f8f629effcb1f13df1e2661baec3e5a2d046e3", "1000-109F,A9E0-A9FF,AA60-AA7F"),
    "Tamil": ("v2.004", "96b61d2f72b8fd69adc7ef644918cdaab9dd6c83f6440587fe93e6461a0aa82c", "0B80-0BFF"),
    "Telugu": ("v2.005", "98b9b2478b4c2febefcdf92693ba098f44d55a70570b9cb88fad55018cc2f7f5", "0C00-0C7F"),
    "Thai": ("v2.002", "89e3c04bfc54d9a5dd0aec660bf974ad46df38385df7d9397c083482d515049a", "0E00-0E7F"),
}
CJK = {  # region: (zip, zip sha256, script id, description of the kept set)
    "SC": ("14_NotoSerifSC.zip", "c58cd035ab2adb003510846db9ec80c35b1b97755d329486c3a1e88edfe6e98e", "han-hans",
           "Table of General Standard Chinese Characters (8105, Unihan kTGHZ2013) + GB 2312"),
    "TC": ("15_NotoSerifTC.zip", "cadfc894416096a51174e8a350e5cb872f99a8c4b1d36c3990e7a9741e1456ab", "han-hant",
           "Big5 (Unihan kBigFive + Python codec big5)"),
    "JP": ("12_NotoSerifJP.zip", "53bdd2a6e4eb63bf24f7890e018dddb94366e3555d0814c72b74fbb128f328f0", "kana",
           "JIS X 0208 (Python codec euc_jp) + Joyo and Jinmeiyo kanji (Unihan) + kana blocks"),
    "KR": ("13_NotoSerifKR.zip", "ac3398873346bc97e00a9652afe791cad6a2df3e5f157c4e7d40ce60b2b88605", "hangul",
           "all 11172 Hangul syllables + jamo blocks + KS X 1001 (Python codec euc_kr)"),
}
NASKH = ("NotoNaskhArabic-v2.021", "6c050ab9bd087d69b733c505a7576e60c528c2f33cd7b91005a5bd7da4514032")


def fetch(cache: Path, url: str, sha: str) -> Path:
    p = cache / url.rsplit("/", 1)[1]
    if not p.exists():
        print("downloading", url, flush=True)
        with urllib.request.urlopen(url, timeout=600) as r, open(p, "wb") as f:
            f.write(r.read())
    got = hashlib.sha256(p.read_bytes()).hexdigest()
    if got != sha:
        sys.exit(f"{p}: sha256 {got} != {sha}")
    return p


def rng(a: int, b: int) -> set[str]:
    return {chr(c) for c in range(a, b + 1)}


def codec_chars(codec: str, lead, trail, prefix: bytes = b"") -> set[str]:
    out = set()
    for a in lead:
        for b in trail:
            try:
                out.update((prefix + bytes([a, b])).decode(codec))
            except UnicodeDecodeError:
                pass
    return out


def cjk_sets(unihan: Path) -> dict[str, set[str]]:
    uni: dict[str, set[str]] = {}
    with zipfile.ZipFile(unihan) as z:
        for n in z.namelist():
            for line in z.read(n).decode("utf-8").splitlines():
                if line.startswith("U+"):
                    cp, prop, _ = line.split("\t", 2)
                    if prop in ("kTGHZ2013", "kBigFive", "kJoyoKanji", "kJinmeiyoKanji"):
                        uni.setdefault(prop, set()).add(chr(int(cp[2:], 16)))
    hi = range(0xA1, 0xFF)
    common = rng(0x20, 0x7E) | rng(0xA0, 0xFF) | rng(0x2000, 0x206F) | rng(0x3000, 0x303F) | rng(0xFF00, 0xFFEF) | {"\u25cc"}
    sets = {
        "SC": uni["kTGHZ2013"] | codec_chars("gb2312", hi, hi),
        "TC": uni["kBigFive"] | codec_chars("big5", range(0xA1, 0xFA), list(range(0x40, 0x7F)) + list(range(0xA1, 0xFF))),
        "JP": uni["kJoyoKanji"] | uni["kJinmeiyoKanji"] | codec_chars("euc_jp", hi, hi) | rng(0x3040, 0x30FF) | rng(0x31F0, 0x31FF),
        "KR": rng(0xAC00, 0xD7A3) | rng(0x1100, 0x11FF) | rng(0x3130, 0x318F) | rng(0xA960, 0xA97F) | rng(0xD7B0, 0xD7FF)
              | codec_chars("euc_kr", hi, hi),
    }
    return {k: {c for c in v | common if c.isprintable() or c == " "} for k, v in sets.items()}


def subset(src: Path, dst: Path, unicodes: str | None = None, unicodes_file: Path | None = None, drop_dsig=True):
    args = [sys.executable, "-m", "fontTools.subset", str(src), *COMMON_OPTS, f"--output-file={dst}"]
    args.append(f"--unicodes={unicodes}" if unicodes else f"--unicodes-file={unicodes_file}")
    if drop_dsig:
        args.append("--drop-tables+=DSIG")
    subprocess.run(args, check=True)


def extract(zpath: Path, inner_suffix: str, dst: Path) -> str:
    with zipfile.ZipFile(zpath) as z:
        name = next(n for n in z.namelist() if n.endswith(inner_suffix))
        dst.write_bytes(z.read(name))
        return name


def licence(zpath: Path, dst: Path) -> None:
    with zipfile.ZipFile(zpath) as z:
        name = next(n for n in z.namelist() if n.endswith(("OFL.txt", "LICENSE")))
        dst.write_bytes(z.read(name))


def main() -> None:
    from fontTools.ttLib import TTFont

    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", required=True, type=Path)
    ap.add_argument("--out", default=Path("rosettex/fonts"), type=Path)
    a = ap.parse_args()
    a.cache.mkdir(parents=True, exist_ok=True)
    (a.out / "LICENSES").mkdir(parents=True, exist_ok=True)
    tmp = a.cache / "extracted"
    tmp.mkdir(exist_ok=True)
    jobs = []  # (out file, script, url, zip sha, inner file, description, subset kwargs, licence file)
    for s, (v, sha, blocks) in SERIF.items():
        tag = f"NotoSerif{s}-{v}"
        jobs.append((f"NotoSerif{s}-Regular.ttf", s.lower(), f"{N}/{s.lower()}/releases/download/{tag}/{tag}.zip", sha,
                     f"/unhinted/ttf/NotoSerif{s}-Regular.ttf", f"the {s} Unicode blocks",
                     {"unicodes": f"{INDIC},{blocks}"}, f"NotoSerif{s}-OFL.txt"))
    tag, sha = NASKH
    jobs.append(("NotoNaskhArabic-Regular.ttf", "arabic", f"{N}/arabic/releases/download/{tag}/{tag}.zip", sha,
                 "/unhinted/ttf/NotoNaskhArabic-Regular.ttf",
                 "Arabic blocks U+0600-06FF, 0750-077F, 08A0-08FF, FB50-FDFF, FE70-FEFF", {"unicodes": ARABIC},
                 "NotoNaskhArabic-OFL.txt"))
    sets = cjk_sets(fetch(a.cache, *UNIHAN))
    for k, (zname, sha, script, desc) in CJK.items():
        uf = tmp / f"u-{k}.txt"
        uf.write_text(",".join(f"{ord(c):04X}" for c in sorted(sets[k])), encoding="utf-8")
        jobs.append((f"NotoSerif{k}-Regular.otf", script, f"{N}/noto-cjk/releases/download/Serif2.003/{zname}", sha,
                     f"SubsetOTF/{k}/NotoSerif{k}-Regular.otf", desc, {"unicodes_file": uf, "drop_dsig": False},
                     f"NotoSerif{k}-OFL.txt"))
    out = []
    for file, script, url, sha, inner, desc, kw, lic in jobs:
        z = fetch(a.cache, url, sha)
        raw = tmp / file
        inner_name = extract(z, inner, raw)
        dst = a.out / file
        subset(raw, dst, **kw)
        licence(z, a.out / "LICENSES" / lic)
        nm = TTFont(dst)["name"]
        out.append({"file": file, "script": script, "bytes": dst.stat().st_size,
                    "sha256": hashlib.sha256(dst.read_bytes()).hexdigest(),
                    "family": nm.getDebugName(1), "version": nm.getDebugName(5), "copyright": nm.getDebugName(0),
                    "license": "OFL-1.1", "license_file": f"LICENSES/{lic}", "source": url, "source_sha256": sha,
                    "source_file": inner_name,
                    "modified": f"subset with fontTools 4.68.0 to {desc}, plus U+0020-007E, U+00A0-00FF, U+2000-206F, "
                                "U+25CC and the script's punctuation; all layout features kept; hinting removed"})
    out.sort(key=lambda e: e["file"])
    (a.out / "MANIFEST.json").write_text(json.dumps({"schema": "rosettex.fonts/1",
                                                     "unihan": {"url": UNIHAN[0], "sha256": UNIHAN[1]},
                                                     "fonts": out}, ensure_ascii=False, indent=1) + "\n",
                                         encoding="utf-8", newline="\n")
    print(len(out), "fonts,", sum(e["bytes"] for e in out), "bytes")


if __name__ == "__main__":
    main()

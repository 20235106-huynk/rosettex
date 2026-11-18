import json
from importlib.resources import files

import pytest

from conftest import FIX, fake_vi
from rosettex.checks import in_blocks, load_languages, load_pack, parse_blocks
from rosettex.extract.mineru_adapter import adapt
from rosettex.extract.segment import segment
from rosettex.render.latex import Renderer

PACKS = ["vi", "fr", "ru", "uk", "zh-Hans", "zh-Hant", "ja", "ko", "ar", "fa", "ur", "hi", "mr", "bn", "te", "ta",
         "gu", "kn", "th", "am", "my"]
SCRIPTS = ["latin", "cyrillic", "han", "kana", "hangul", "arabic", "devanagari", "bengali", "telugu", "tamil",
           "gujarati", "kannada", "thai", "ethiopic", "myanmar"]
LAYOUTS = {"plain", "cjk", "runs", "rtl"}
MANIFEST = {e["file"] for e in json.loads((files("rosettex") / "fonts" / "MANIFEST.json").read_text(encoding="utf-8"))["fonts"]}


def shipped_packs():
    base = files("rosettex") / "langpacks"
    return sorted(p.name[:-5] for p in base.iterdir()
                  if p.name.endswith(".json") and p.name not in ("default.json", "languages.json"))


def test_packs_and_scripts_are_complete():
    assert shipped_packs() == sorted(PACKS)
    assert {load_pack(c)["profile"]["id"] for c in PACKS} == set(SCRIPTS)


@pytest.mark.parametrize("code", PACKS)
def test_pack_is_complete(code):
    p, langs = load_pack(code), load_languages()
    prof = p["profile"]
    assert p["pack_found"] is True and p["script_known"] is True and p["code"] == code
    assert langs[code]["script"] == p["script"] == prof["id"]
    assert prof["layout"] in LAYOUTS and (prof["direction"] == "rtl") == (prof["layout"] == "rtl")
    assert prof["linebreak"] in ("space", "cjk", "zwsp")
    for f in p["fonts"]:
        assert f["source"] in ("bundle", "package") and f["license"] and "/" not in f["file"] and "\\" not in f["file"]
        assert f["source"] == "bundle" or f["file"] in MANIFEST
    assert p["tested"] is (code == "vi")
    for k in ("min_len_check", "min_target_ratio", "length_ratio", "labels", "rules"):
        assert k in p
    if prof["layout"] != "plain":
        assert {"figure", "table", "references"} <= set(p["labels"])
    if prof["layout"] == "rtl":
        assert p["polyglossia"] and p.get("polyglossia_options")


def test_blocks_parse():
    b = parse_blocks(["0980-09FF", "200C-200D"])
    assert b == [(0x0980, 0x09FF), (0x200C, 0x200D)]
    assert in_blocks("ব", b) and in_blocks("\u200c", b) and not in_blocks("a", b)
    assert parse_blocks(["20000-3134F"]) == [(0x20000, 0x3134F)]


def test_mapped_language_without_pack_gets_its_script():
    ps = load_pack("ps")
    assert ps["pack_found"] is False and ps["script_known"] is True
    assert ps["profile"]["id"] == "arabic" and ps["polyglossia"] == "arabic"
    assert ps["fonts"][0]["file"] == "NotoNaskhArabic-Regular.ttf" and ps["min_target_ratio"] == 0.3
    bg = load_pack("bg")
    assert bg["profile"]["id"] == "cyrillic" and bg["fonts"][0]["file"] == "NotoSerif-Regular.ttf"
    es = load_pack("es")
    assert es["profile"]["id"] == "latin" and es["fonts"][0]["file"] == "texgyretermes"


def test_aliases():
    assert load_pack("zh")["fonts"][0]["file"] == "NotoSerifSC-Regular.otf" and load_pack("zh")["code"] == "zh"
    assert load_pack("zh-TW")["fonts"][0]["file"] == "NotoSerifTC-Regular.otf"
    sr = load_pack("sr")  # alias to an entry without a pack file
    assert sr["profile"]["id"] == "cyrillic" and sr["pack_found"] is False and sr["script_known"] is True


def test_unknown_language():
    p = load_pack("xx")
    assert p["pack_found"] is False and p["script_known"] is False and p["profile"]["id"] == "latin"
    assert p["fonts"][0]["file"] == "NotoSerif-Regular.ttf"


def test_vi_keeps_termes():
    p = load_pack("vi")
    assert p["fonts"][0]["file"] == "texgyretermes" and p["polyglossia"] == "vietnamese"


def test_vi_main_tex_is_byte_identical_to_slice1():
    d = FIX / "mineru-4.2.3-standard"
    doc, _ = segment(adapt([(json.loads((d / "short.middle.json").read_text(encoding="utf-8")), d)], d))
    tr = {u["id"]: fake_vi(u["src"]) for u in doc["units"]}
    golden = (FIX.parent / "golden" / "vi_short_main.tex").read_text(encoding="utf-8")
    assert Renderer(doc, tr, load_pack("vi"), set(), {}).document() == golden


import re

from rosettex.tex import wrap_runs

BN, AR, TH = (load_pack(c)["profile"] for c in ("bn", "ar", "th"))


def test_runs_keep_digits_and_ascii_punctuation_outside():
    assert wrap_runs("এটি 0.05 মান।", BN) == r"\rxs{এটি} 0.05 \rxs{মান।}"
    assert wrap_runs("ক্\u200cষ, ক", BN) == "\\rxs{ক্\u200cষ}, \\rxs{ক}"
    assert wrap_runs("এটি\n\nমান", BN) == "\\rxs{এটি}\n\n\\rxs{মান}"
    assert wrap_runs("50% এটি_", BN) == r"50\% \rxs{এটি}\_"


def test_zwsp_becomes_a_break_point():
    assert wrap_runs("ภาษา\u200bไทย 0.05", TH) == r"\rxs{ภาษา\hspace{0pt plus 1pt}ไทย} 0.05"


def test_rtl_wraps_latin_and_digits_not_parentheses():
    assert wrap_runs("يحقق 28.4 BLEU (تقريبا) و 5%", AR) == r"يحقق \rxltr{28.4 BLEU} \rxltr{)}تقريبا\rxltr{(} و \rxltr{5\%}"
    assert wrap_runs("نص [4] – «س» ؟", AR) == r"نص \rxltr{]}\rxltr{4}\rxltr{[} \rxltr{–} \rxltr{»}س\rxltr{«} ؟"
    assert wrap_runs("2 المقدمة", AR) == r"\rxltr{2} المقدمة"


def test_plain_profile_is_plain_escape():
    assert wrap_runs("a_b 0.05", load_pack("vi")["profile"]) == r"a\_b 0.05"


def doc_and_tr(sample):
    d = FIX / "mineru-4.2.3-standard"
    doc, _ = segment(adapt([(json.loads((d / "short.middle.json").read_text(encoding="utf-8")), d)], d))
    return doc, {u["id"]: sample + " " + " ".join(re.findall(r"⟦[a-z]\d+⟧", u["src"])) for u in doc["units"]}


@pytest.mark.parametrize("code,needle", [
    ("ru", r"\setdefaultlanguage{russian}"),
    ("ja", r"\setCJKmainfont{NotoSerifJP-Regular.otf}[Path=fonts/]"),
    ("hi", r"\setotherlanguage{hindi}"),
    ("gu", r"\newfontfamily\rxscriptfont{NotoSerifGujarati-Regular.ttf}[Path=fonts/,Scale=0.9,Script=Gujarati]"),
    ("ar", r"\setdefaultlanguage[numerals=maghrib]{arabic}"),
])
def test_preamble_per_layout(code, needle):
    doc, tr = doc_and_tr("x")
    tex = Renderer(doc, tr, load_pack(code), set(), {}).document()
    assert needle in tex
    if load_pack(code)["profile"]["layout"] != "plain":
        assert r"\setmainfont{texgyretermes}" in tex and r"\renewcommand{\figurename}" in tex
    if code == "ar":
        assert tex.index(r"{hyperref}") < tex.index(r"\usepackage{polyglossia}")


def test_arabic_body():
    doc, tr = doc_and_tr("نص عربي")
    tex = Renderer(doc, tr, load_pack("ar"), set(), {}).document()
    assert r"\rxltr{\cite{" in tex and r"\rxltr{$" not in tex
    assert r"{\selectlanguage{english}\renewcommand{\refname}{\RL{\arabicfont " in tex and r"\begin{LTR}" not in tex


def test_rtl_language_without_pack_gets_ascii_digits():
    assert load_pack("ps")["polyglossia_options"] == "numerals=maghrib"


@pytest.mark.parametrize("raw,script,found", [("ja-JP", "kana", True), ("ko-KR", "hangul", True), ("zh-cn", "han", True),
                                              ("hi_IN", "devanagari", True), ("VI", "latin", True), ("sr_latn", "latin", False),
                                              ("PT-br", "latin", False), ("xx", "latin", False)])
def test_region_and_case_variants(raw, script, found):
    p = load_pack(raw)
    assert p["profile"]["id"] == script and p["pack_found"] is found and p["code"] == raw


def test_rtl_sets_every_non_arabic_character_in_the_latin_font():
    ur = load_pack("ur")["profile"]
    assert wrap_runs("زمان 174 µs — ± مقدمه.", ur) == r"زمان \rxltr{174} \rxltr{µ}\rxltr{s} \rxltr{—} \rxltr{±} مقدمه."
    doc, tr = doc_and_tr("نص")
    tex = Renderer(doc, tr, load_pack("ar"), set(), {}).document()
    assert r"\renewcommand{\thetable}{\protect\rxltr{\rxthetable}}" in tex  # generated Roman table numbers

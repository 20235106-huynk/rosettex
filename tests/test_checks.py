import unicodedata

from rosettex.checks import bad_chars, check_batch, load_pack, looks_untranslated, numbers

VI = load_pack("vi")
SRC = ("Our model reaches 28.4 BLEU, outperforming the best previous result by 2.0 points "
       "while costing only ⟦m1⟧ FLOPs, as shown in Table ⟦r1⟧.")
GOOD = ("Mô hình của chúng tôi đạt 28.4 BLEU, vượt kết quả tốt nhất trước đó 2.0 điểm "
        "trong khi chỉ tốn ⟦m1⟧ FLOPs, như trình bày ở Bảng ⟦r1⟧.")
BATCH = {"batch": "b001", "items": [{"id": "p1b20", "type": "paragraph", "page": 1, "text": SRC, "placeholders": {}}]}
TERMS = {"topic": "dịch máy", "terms": [{"src": "BLEU", "dst": "BLEU"}], "keep": [], "abbreviations": []}


def errs(dst):
    return check_batch(BATCH, {"p1b20": dst}, VI, TERMS)["errors"]


def test_correct_vietnamese_passes():  # regression: the Han-character-based check rejected a correct Vietnamese translation
    assert errs(GOOD) == {}


def test_reordered_placeholders_pass():
    assert errs("Như trình bày ở Bảng ⟦r1⟧, mô hình của chúng tôi đạt 28.4 BLEU, vượt kết quả tốt nhất "
                "trước đó 2.0 điểm trong khi chỉ tốn ⟦m1⟧ FLOPs.") == {}


def test_added_number_passes():
    assert errs(GOOD.replace("trước đó", "trước đó (tức 2 hệ thống)")) == {}


def test_changed_number_rejected():  # regression: PaperReader accepted a translation with altered numbers
    assert "missing or changed number" in " ".join(errs(GOOD.replace("28.4", "28.5"))["p1b20"])


def test_decimal_comma_rejected():
    assert "missing or changed number" in " ".join(errs(GOOD.replace("2.0", "2,0"))["p1b20"])


def test_placeholder_missing_or_altered_rejected():
    assert "missing placeholder" in " ".join(errs(GOOD.replace("⟦m1⟧", ""))["p1b20"])
    assert "extra or wrong placeholder" in " ".join(errs(GOOD.replace("⟦m1⟧", "⟦m2⟧"))["p1b20"])


def test_english_left_untranslated_rejected():
    assert "looks untranslated" in errs(SRC)["p1b20"]
    assert looks_untranslated("short", "short", VI) is False  # short paragraph: no conclusion


def test_nfd_input_is_equivalent():
    assert errs(unicodedata.normalize("NFD", GOOD)) == {}


def test_missing_and_extra_ids():
    res = check_batch(BATCH, {"zzz": GOOD}, VI, TERMS)["errors"]
    assert res["p1b20"] == ["missing translation for this id"] and res["zzz"] == ["id does not belong to this batch"]


def test_bad_chars():
    assert bad_chars("abc", "Mô hình \ufffd", VI) == ["\ufffd"]
    assert bad_chars("Schölkopf et al.", "Schölkopf và cộng sự", VI) == []
    assert bad_chars("x", "日本", VI) == ["日", "本"]


def test_numbers_thousands_normalized():
    assert numbers("1,000 and 1000 and 0.05") == numbers("1000 và 1,000 và 0.05")


def test_term_warning_and_unknown_language_pack():
    w = check_batch(BATCH, {"p1b20": GOOD.replace("BLEU", "điểm B")}, VI, TERMS)["warnings"]
    assert any("BLEU" in x for x in w["p1b20"])
    fr = load_pack("fr")
    assert fr["code"] == "fr" and fr["tested"] is False


def test_normalize_whitespace_per_unit_type():
    from rosettex.checks import normalize_ws
    assert normalize_ws("heading", "Giới thiệu\n\nphương  pháp\t") == "Giới thiệu phương pháp"
    assert normalize_ws("caption", "a\n\n\nb") == "a b"
    assert normalize_ws("cell", " a\nb ") == "a b"
    assert normalize_ws("footnote", "a\n\nb") == "a b"
    assert normalize_ws("paragraph", "a\n \n\n\nb  \nc") == "a\n\nb\nc"

import pytest

from rosettex.checks import foreign_digits, missing_word_breaks

EN = ("The proposed model improves the accuracy of the baseline by a large margin on every benchmark "
      "we evaluated, reaching 0.05 error with ⟦m1⟧ parameters as reported in ⟦c1⟧.")
GOOD_BY_LANG = {
    "fr": "Le modèle proposé améliore nettement la précision du modèle de référence sur chaque banc d'essai que nous avons évalué, avec une erreur de 0.05 pour ⟦m1⟧ paramètres, comme indiqué dans ⟦c1⟧.",
    "ru": "Предложенная модель значительно улучшает точность базовой модели на всех тестах, достигая ошибки 0.05 при ⟦m1⟧ параметрах, как сообщается в ⟦c1⟧.",
    "zh-Hans": "所提出的模型在我们评估的每个基准上都大幅提高了基线的准确率，误差达到 0.05，参数为 ⟦m1⟧，如 ⟦c1⟧ 所述。",
    "ja": "提案モデルは評価したすべてのベンチマークでベースラインの精度を大きく改善し、⟦m1⟧ 個のパラメータで誤差 0.05 に達した（⟦c1⟧）。",
    "ko": "제안된 모델은 우리가 평가한 모든 벤치마크에서 기준 모델의 정확도를 크게 향상시켜 ⟦m1⟧ 개의 매개변수로 0.05 오차에 도달했다 ⟦c1⟧.",
    "ar": "يحسن النموذج المقترح دقة النموذج الأساسي بهامش كبير في كل معيار قمنا بتقييمه، إذ يبلغ خطأ 0.05 مع ⟦m1⟧ معامل كما ورد في ⟦c1⟧.",
    "hi": "प्रस्तावित मॉडल हर बेंचमार्क पर आधार मॉडल की सटीकता में बड़ा सुधार करता है और ⟦m1⟧ मापदंडों के साथ 0.05 त्रुटि तक पहुँचता है, जैसा ⟦c1⟧ में बताया गया है।",
    "th": "แบบจำลอง\u200bที่\u200bเสนอ\u200bช่วย\u200bเพิ่ม\u200bความ\u200bแม่นยำ\u200bของ\u200bแบบจำลอง\u200bพื้นฐาน\u200bอย่าง\u200bมาก ความ\u200bคลาดเคลื่อน 0.05 ด้วย\u200bพารามิเตอร์ ⟦m1⟧ ตาม\u200bที่\u200bรายงาน\u200bใน ⟦c1⟧",
}
# Task 9 extends this table to every pack from tests/script_samples.json.


def batch_of(text):
    return {"batch": "b001", "items": [{"id": "p1b1", "type": "paragraph", "page": 1, "text": text, "placeholders": {}}]}


@pytest.mark.parametrize("code,good", sorted(GOOD_BY_LANG.items()))
def test_script_pack_accepts_good_and_rejects_english(code, good):
    pack = load_pack(code)
    assert check_batch(batch_of(EN), {"p1b1": good}, pack, None)["errors"] == {}
    assert "looks untranslated" in check_batch(batch_of(EN), {"p1b1": EN}, pack, None)["errors"]["p1b1"]
    assert looks_untranslated(EN, EN.replace("The proposed", "A proposed"), pack)


def test_alphabet():
    assert bad_chars("x", "ক্\u200cষ", load_pack("bn")) == []
    assert bad_chars("x", "परीक्षण привет", load_pack("hi")) == list("привет")


def test_foreign_digits_rejected_everywhere():
    assert foreign_digits("0.05", "٠٫٠٥ و 0.05") == ["٠", "٠", "٥"]
    assert foreign_digits("x", "１２") == ["１", "２"]
    e = check_batch(batch_of(EN), {"p1b1": GOOD_BY_LANG["ar"] + " ٣"}, load_pack("ar"), None)["errors"]["p1b1"]
    assert any("ASCII digits" in x for x in e)


def test_thai_needs_word_separators():
    th = load_pack("th")
    assert not missing_word_breaks(GOOD_BY_LANG["th"], th)
    assert missing_word_breaks(GOOD_BY_LANG["th"].replace("\u200b", ""), th)
    assert not missing_word_breaks("ไม่มี" * 10, load_pack("hi"))  # only zwsp profiles


def test_vi_detector_unchanged():
    assert looks_untranslated(SRC, SRC, VI) and not looks_untranslated(SRC, GOOD, VI)
    assert looks_untranslated(SRC, "short", VI) is False


SAMPLES = __import__("json").loads((__import__("pathlib").Path(__file__).parent / "script_samples.json").read_text(encoding="utf-8"))
EN_PLAIN = "The proposed model improves the accuracy of the baseline on every dataset we evaluated, at a lower cost."


@pytest.mark.parametrize("code", sorted(SAMPLES))
def test_sample_translation_is_accepted(code):
    pack = load_pack(code)
    assert check_batch(batch_of(EN_PLAIN), {"p1b1": SAMPLES[code]}, pack, None)["errors"] == {}


def test_known_latin_language_rejects_foreign_letters():
    de = load_pack("de")
    assert bad_chars("x", "Das Modell привет", de) == list("привет")
    assert bad_chars("x", "Größe, façade, naïve, Ελλάδα", de) == []
    assert bad_chars("x", "привет", load_pack("xx")) == []  # unknown code: script not known, no judgement


def test_thai_spaces_without_zwsp_are_rejected():
    th = load_pack("th")
    long_spaced = " ".join(["แบบจำลอง"] * 8)
    assert missing_word_breaks(long_spaced, th) and not missing_word_breaks("แบบจำลอง ที่ เสนอ", th)


def test_decimal_comma_languages_may_localise_numbers():
    from rosettex.checks import missing_numbers
    fr, vi = load_pack("fr"), load_pack("vi")
    src = "Errors fell from 0.05 to 0.01 over 1,000 runs and 5,000 steps."
    assert not missing_numbers(src, "Erreurs de 0,05 à 0,01 sur 1 000 essais et 5 000 pas.", fr)
    assert not missing_numbers(src, "Erreurs de 0.05 à 0.01 sur 1 000 essais et 5.000 pas.", fr)
    assert missing_numbers(src, "Erreurs de 0,05 à 0,01 sur 1,000 essais et 5 000 pas.", fr) == {"1000": 1}  # reads as 1
    assert missing_numbers(src, "Erreurs de 0,5 à 0,01 sur 1 000 essais et 5 000 pas.", fr) == {"0.05": 1}
    assert missing_numbers(src, "Lỗi 0,05 đến 0,01 qua 1,000 lần và 5,000 bước.", vi) == {"0.05": 1, "0.01": 1}  # vi: as before


def test_narrow_spaces_are_typeset_by_tex():
    from rosettex.tex import escape_latex
    assert escape_latex("1 000 et 5 000") == r"1\,000 et 5~000"

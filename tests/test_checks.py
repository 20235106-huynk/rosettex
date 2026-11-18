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


def test_correct_vietnamese_passes():  # hồi quy: bộ kiểm tra dựa chữ Hán báo lỗi bản dịch tiếng Việt đúng
    assert errs(GOOD) == {}


def test_reordered_placeholders_pass():
    assert errs("Như trình bày ở Bảng ⟦r1⟧, mô hình của chúng tôi đạt 28.4 BLEU, vượt kết quả tốt nhất "
                "trước đó 2.0 điểm trong khi chỉ tốn ⟦m1⟧ FLOPs.") == {}


def test_added_number_passes():
    assert errs(GOOD.replace("trước đó", "trước đó (tức 2 hệ thống)")) == {}


def test_changed_number_rejected():  # hồi quy: PaperReader chấp nhận bản dịch đã bị sửa số liệu
    assert "thiếu hoặc đổi số" in " ".join(errs(GOOD.replace("28.4", "28.5"))["p1b20"])


def test_decimal_comma_rejected():
    assert "thiếu hoặc đổi số" in " ".join(errs(GOOD.replace("2.0", "2,0"))["p1b20"])


def test_placeholder_missing_or_altered_rejected():
    assert "thiếu placeholder" in " ".join(errs(GOOD.replace("⟦m1⟧", ""))["p1b20"])
    assert "thừa hoặc sai placeholder" in " ".join(errs(GOOD.replace("⟦m1⟧", "⟦m2⟧"))["p1b20"])


def test_english_left_untranslated_rejected():
    assert "nghi chưa dịch" in errs(SRC)["p1b20"]
    assert looks_untranslated("short", "short", VI) is False  # đoạn ngắn: không kết luận


def test_nfd_input_is_equivalent():
    assert errs(unicodedata.normalize("NFD", GOOD)) == {}


def test_missing_and_extra_ids():
    res = check_batch(BATCH, {"zzz": GOOD}, VI, TERMS)["errors"]
    assert res["p1b20"] == ["thiếu bản dịch cho id này"] and res["zzz"] == ["id không thuộc lô này"]


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

"""Deterministic pseudo-translation for rosettex multilingual pipeline verification.

Usage: pseudo_translate.py <work> [--lang <code>] [--break N] [--zwsp] [--force]
"""

import argparse
import hashlib
import json
import random
import re
import unicodedata
from pathlib import Path
from typing import Any

# Regex patterns
_PH = re.compile(r"(⟦/?[a-z]\d+⟧)")
_TOKEN = re.compile(r"⟦(/?)([a-z])(\d+)⟧")
_SEG_LINE = re.compile(r"^(s\d+)\s+(.*)$")
_DUP_VAL = re.compile(r"^=\s+s\d+\s*$")
_WORD = re.compile(r"[^\W\d_]+")
_CJK_RE = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_FULLWIDTH_DIGITS = {0xFF10 + i: 0x30 + i for i in range(10)}
# Map fullwidth ASCII U+FF01-U+FF5E to ASCII, preserving CJK punctuation spacing
_CJK_PUNCT_ENTRIES = {
    "\u3000": " ",
    "\u3001": ", ",
    "\u3002": ". ",
    "\u3008": " (",
    "\u3009": ") ",
    "\u300a": " (",
    "\u300b": ") ",
    "\u300c": " (",
    "\u300d": ") ",
    "\u300e": " (",
    "\u300f": ") ",
    "\u3010": " [",
    "\u3011": "] ",
    "\u3014": " (",
    "\u3015": ") ",
    "\u30fb": "-",
    "\u301c": "~",
    "\uff01": "! ",
    "\uff02": '"',
    "\uff07": "'",
    "\uff08": " (",
    "\uff09": ") ",
    "\uff0c": ", ",
    "\uff0e": ". ",
    "\uff1a": ": ",
    "\uff1b": "; ",
    "\uff1f": "? ",
    "\uff5e": "~",
}
_CJK_PUNCT_MAP = {chr(0xFF01 + i): chr(0x21 + i) for i in range(0x5E)}
_CJK_PUNCT_MAP.update(_CJK_PUNCT_ENTRIES)
_CJK_PUNCT = str.maketrans(_CJK_PUNCT_MAP)

# Comprehensive fallback profiles with >= 30 real words per script
FALLBACK_PROFILES: dict[str, dict[str, Any]] = {
    "latin": {
        "words": [
            "dịch", "mô", "hình", "dữ", "liệu", "kết", "quả", "phương", "pháp", "huấn",
            "luyện", "mạng", "thử", "nghiệm", "độ", "chính", "xác", "tối", "ưu", "giảm",
            "tăng", "biểu", "diễn", "ngữ", "cảnh", "học", "máy", "phân", "tích", "đánh",
            "giá", "nghiên", "cứu", "hệ", "thống",
        ],
        "decimal": ",",
        "group": [".", " "],
        "length_ratio": [0.8, 1.6],
        "linebreak": "space",
        "direction": "ltr",
    },
    "cyrillic": {
        "words": [
            "модель", "данные", "результат", "метод", "обучение", "сеть", "эксперимент",
            "точность", "оптимизация", "анализ", "исследование", "система", "параметр",
            "функция", "алгоритм", "оценка", "структура", "процесс", "вектор", "матрица",
            "проблема", "подход", "значение", "уровень", "качество", "таблица", "рисунок",
            "вывод", "статья", "основа", "разработка", "сравнение", "тестирование",
        ],
        "decimal": ",",
        "group": [" ", "."],
        "length_ratio": [1.0, 1.4],
        "linebreak": "space",
        "direction": "ltr",
    },
    "han": {
        "words": [
            "模型", "数据", "结果", "方法", "训练", "网络", "实验", "准确率",
            "优化", "分析", "研究", "系统", "参数", "函数", "算法", "评估",
            "结构", "过程", "特征", "表示", "图表", "文献", "测试", "性能",
            "应用", "理论", "比较", "基准", "任务", "目标", "分类", "生成",
        ],
        "decimal": ".",
        "group": [","],
        "length_ratio": [0.2, 1.5],
        "linebreak": "cjk",
        "direction": "ltr",
    },
    "kana": {
        "words": [
            "モデル", "データ", "結果", "手法", "学習", "ネットワーク", "実験",
            "精度", "最適化", "分析", "研究", "システム", "パラメータ", "関数",
            "アルゴリズム", "評価", "構造", "プロセス", "特徴", "表現", "図表",
            "参考文献", "比較", "性能", "応用", "提案", "処理", "画像", "言語",
            "生成", "タスク", "検証",
        ],
        "decimal": ".",
        "group": [","],
        "length_ratio": [0.5, 1.5],
        "linebreak": "cjk",
        "direction": "ltr",
    },
    "hangul": {
        "words": [
            "모델", "데이터", "결과", "방법", "학습", "네트워크", "실험", "정확도",
            "최적화", "분석", "연구", "시스템", "매개변수", "함수", "알고리즘", "평가",
            "구조", "과정", "특징", "표현", "도표", "참고문헌", "성능", "비교",
            "제안", "처리", "응용", "생성", "분류", "태스크", "기반", "검증",
        ],
        "decimal": ".",
        "group": [","],
        "length_ratio": [0.8, 1.3],
        "linebreak": "space",
        "direction": "ltr",
    },
    "arabic": {
        "words": [
            "نموذج", "بيانات", "نتائج", "طريقة", "تدريب", "شبكة", "تجربة", "دقة",
            "تحسين", "تحليل", "دراسة", "نظام", "معلمات", "دالة", "خوارزمية", "تقييم",
            "هيكل", "عملية", "ميزات", "تمثيل", "جدول", "شكل", "مراجع", "مقارنة",
            "أداء", "تطبيق", "اقتراح", "تصنيف", "توليد", "مهمة", "اختبار", "بحث",
        ],
        "decimal": ".",
        "group": [","],
        "length_ratio": [0.9, 1.3],
        "linebreak": "space",
        "direction": "rtl",
    },
    "devanagari": {
        "words": [
            "मॉडल", "डेटा", "परिणाम", "विधि", "प्रशिक्षण", "नेटवर्क", "प्रयोग", "सटीकता",
            "अनुकूलन", "विश्लेषण", "अध्ययन", "प्रणाली", "पैरामीटर", "फ़ंक्शन", "एल्गोरिदम",
            "मूल्यांकन", "संरचना", "प्रक्रिया", "सुविधाएँ", "प्रतिनिधित्व", "तालिका", "चित्र",
            "संदर्भ", "तुलना", "प्रदर्शन", "अनुप्रयोग", "प्रस्ताव", "वर्गीकरण", "उत्पादन",
            "कार्य", "परीक्षण", "अनुसंधान",
        ],
        "decimal": ".",
        "group": [","],
        "length_ratio": [1.0, 1.4],
        "linebreak": "space",
        "direction": "ltr",
    },
    "bengali": {
        "words": [
            "মডেল", "উপাত্ত", "ফলাফল", "পদ্ধতি", "প্রশিক্ষণ", "নেটওয়ার্ক", "পরীক্ষা",
            "নির্ভুলতা", "অনুকূলন", "বিশ্লেষণ", "গবেষণা", "সিস্টেম", "প্যারামিটার", "ফাংশন",
            "অ্যালগরিদম", "মূল্যায়ন", "কাঠামো", "প্রক্রিয়া", "বৈশিষ্ট্য", "উপস্থাপন",
            "সারণি", "চিত্র", "তথ্যসূত্র", "তুলনা", "কর্মক্ষমতা", "প্রয়োগ", "প্রস্তাব",
            "শ্রেণীবিভাগ", "উৎপাদন", "কাজ", "যাচাই", "তত্ত্ব",
        ],
        "decimal": ".",
        "group": [","],
        "length_ratio": [1.0, 1.4],
        "linebreak": "space",
        "direction": "ltr",
    },
    "tamil": {
        "words": [
            "மாதிரி", "தரவு", "முடிவுகள்", "முறை", "பயிற்சி", "வலைப்பின்னல்", "சோதனை",
            "துல்லியம்", "உகப்பாக்கம்", "பகுப்பாய்வு", "ஆராய்ச்சி", "அமைப்பு", "அளபுரு",
            "செயல்பாடு", "படிமுறை", "மதிப்பீடு", "கட்டமைப்பு", "செயல்முறை", "அம்சங்கள்",
            "பிரதிநிதித்துவம்", "அட்டவணை", "படம்", "மேற்கோள்கள்", "ஒப்பீடு", "செயல்திறன்",
            "பயன்பாடு", "முன்மொழிவு", "வகைப்பாடு", "உருவாக்கம்", "பணி", "சரிபார்ப்பு", "கோட்பாடு",
        ],
        "decimal": ".",
        "group": [","],
        "length_ratio": [1.0, 1.4],
        "linebreak": "space",
        "direction": "ltr",
    },
    "telugu": {
        "words": [
            "నమూనా", "డేటా", "ఫలితాలు", "పద్ధతి", "శిక్షణ", "నెట్‌వర్క్", "ప్రయోగం",
            "ఖచ్చితత్వం", "ఆప్టిమైజేషన్", "విశ్లేషణ", "పరిశోధన", "వ్యవస్థ", "పారామితులు",
            "ఫంక్షన్", "అల్గోరిథం", "మూల్యాంకనం", "నిర్మాణం", "ప్రక్రియ", "లక్షణాలు",
            "ప్రాతినిధ్యం", "పట్టిక", "చిత్రం", "మూలాలు", "పోలిక", "పనితీరు", "అనువర్తనం",
            "ప్రతిపాదన", "వర్గీకరణ", "ఉత్పత్తి", "పని", "ధృవీకరణ", "సిద్ధాంతం",
        ],
        "decimal": ".",
        "group": [","],
        "length_ratio": [1.0, 1.4],
        "linebreak": "space",
        "direction": "ltr",
    },
    "gujarati": {
        "words": [
            "મોડેલ", "ડેટા", "પરિણામો", "પદ્ધતિ", "તાલીમ", "નેટવર્ક", "પ્રયોગ", "ચોકસાઈ",
            "ઓપ્ટિમાઇઝેશન", "વિશ્લેષણ", "સંશોધન", "સિસ્ટમ", "પરિમાણ", "કાર્ય", "અલ્ગોરિધમ",
            "મૂલ્યાંકન", "માળખું", "પ્રક્રિયા", "લક્ષણો", "પ્રતિનિધિત્વ", "કોષ્ટક", "આકૃતિ",
            "સંદર્ભો", "સરખામણી", "પ્રદર્શન", "એપ્લિકેશન", "દરખાસ્ત", "વર્ગીકરણ", "ઉત્પાદન",
            "કાર્ય", "ચકાસણી", "સિદ્ધાંત",
        ],
        "decimal": ".",
        "group": [","],
        "length_ratio": [1.0, 1.4],
        "linebreak": "space",
        "direction": "ltr",
    },
    "kannada": {
        "words": [
            "ಮಾದರಿ", "ಡೇಟಾ", "ಫಲಿತಾಂಶಗಳು", "ವಿಧಾನ", "ತರಬೇತಿ", "ನೆಟ್‌ವರ್ಕ್", "ಪ್ರಯೋಗ",
            "ನಿಖರತೆ", "ಆಪ್ಟಿಮೈಸೇಶನ್", "ವಿಶ್ಲೇಷಣೆ", "ಸಂಶೋಧನೆ", "ವ್ಯವಸ್ಥೆ", "ನಿಯತಾಂಕಗಳು",
            "ಕಾರ್ಯ", "ಅಲ್ಗಾರಿದಮ್", "ಮೌಲ್ಯಮಾಪನ", "ರಚನೆ", "ಪ್ರಕ್ರಿಯೆ", "ವೈಶಿಷ್ಟ್ಯಗಳು",
            "ಪ್ರಾತಿನಿಧ್ಯ", "ಕೋಷ್ಟಕ", "ಚಿತ್ರ", "ಉಲ್ಲೇಖಗಳು", "ಹೋಲಿಕೆ", "ಕಾರ್ಯಕ್ಷಮತೆ",
            "ಅನ್ವಯ", "ಪ್ರಸ್ತಾವನೆ", "ವರ್ಗೀಕರಣ", "ಉತ್ಪಾದನೆ", "ಕಾರ್ಯ", "ಪರಿಶೀಲನೆ", "ಸಿದ್ಧಾಂತ",
        ],
        "decimal": ".",
        "group": [","],
        "length_ratio": [1.0, 1.4],
        "linebreak": "space",
        "direction": "ltr",
    },
    "thai": {
        "words": [
            "การ", "งาน", "ค่า", "ผล", "ตัว", "จุด", "ภาพ", "แบบ", "ชั้น", "กลุ่ม",
            "ส่วน", "ด้าน", "หลัก", "ข้อ", "ชุด", "ระบบ", "ข้อมูล", "ปัญหา", "วิธี", "ขนาด",
            "ตาราง", "ภาษา", "รูป", "เวลา", "หน่วย", "ตัวอย่าง", "ขั้นตอน", "ปัจจัย", "แนวทาง", "เป้าหมาย",
            "เงื่อนไข", "บทความ", "ทฤษฎี", "บทนำ", "คำตอบ",
        ],
        "decimal": ".",
        "group": [","],
        "length_ratio": [0.9, 1.3],
        "linebreak": "zwsp",
        "direction": "ltr",
    },
    "myanmar": {
        "words": [
            "မော်ဒယ်", "ဒေတာ", "ရလဒ်များ", "နည်းလမ်း", "လေ့ကျင့်ရေး", "ကွန်ရက်",
            "စမ်းသပ်မှု", "တိကျမှု", "အကောင်းဆုံး", "ခွဲခြမ်းစိတ်ဖြာခြင်း",
            "သုတေသန", "စနစ်", "ကန့်သတ်ချက်များ", "လုပ်ဆောင်ချက်", "အယ်လဂိုရီသမ်",
            "အကဲဖြတ်ခြင်း", "ဖွဲ့စည်းပုံ", "လုပ်ငန်းစဉ်", "အင်္ဂါရပ်များ", "ကိုယ်စားပြုမှု",
            "ဇယား", "ပုံ", "ကိုးကားချက်များ", "နှိုင်းယှဉ်ချက်", "စွမ်းဆောင်ရည်",
            "အသုံးချမှု", "အဆိုပြုချက်", "ခွဲခြားသတ်မှတ်ခြင်း", "ထုတ်လုပ်ခြင်း", "အလုပ်",
            "စစ်ဆေးခြင်း", "သီအိုရီ",
        ],
        "decimal": ".",
        "group": [","],
        "length_ratio": [1.0, 1.4],
        "linebreak": "zwsp",
        "direction": "ltr",
    },
    "ethiopic": {
        "words": [
            "ሞዴል", "መረጃ", "ውጤቶች", "ዘዴ", "ስልጠና", "አውታረመረብ", "ሙከራ",
            "ትክክለኛነት", "ማመቻቸት", "ትንተና", "ምርምር", "ስርዓት", "መለኪያዎች",
            "ተግባር", "አልጎሪዝም", "ግምገማ", "መዋቅር", "ሂደት", "ባህሪያት",
            "ውክልナ", "ሰንጠረዥ", "ምስል", "ማጣቀሻዎች", "ንጽጽር", "አፈፃፀም",
            "መተግበሪያ", "ሀሳብ", "ምደባ", "ማመንጨት", "ተግባር", "ማረጋገጫ", "ንድፈሀሳብ",
        ],
        "decimal": ".",
        "group": [","],
        "length_ratio": [1.0, 1.4],
        "linebreak": "space",
        "direction": "ltr",
    },
}

LANG_TO_SCRIPT: dict[str, str] = {
    "vi": "latin",
    "en": "latin",
    "fr": "latin",
    "de": "latin",
    "es": "latin",
    "it": "latin",
    "ru": "cyrillic",
    "uk": "cyrillic",
    "be": "cyrillic",
    "bg": "cyrillic",
    "zh-Hans": "han",
    "zh-Hant": "han",
    "zh": "han",
    "ja": "kana",
    "ko": "hangul",
    "ar": "arabic",
    "fa": "arabic",
    "ur": "arabic",
    "ckb": "arabic",
    "hi": "devanagari",
    "mr": "devanagari",
    "bn": "bengali",
    "as": "bengali",
    "ta": "tamil",
    "te": "telugu",
    "gu": "gujarati",
    "kn": "kannada",
    "th": "thai",
    "my": "myanmar",
    "am": "ethiopic",
}


def get_lang_config(lang: str) -> dict[str, Any]:
    """Retrieve language configuration, reading from langdata if available, otherwise fallback."""
    cfg: dict[str, Any] = {}
    try:
        from rosettex.langdata import load_lang
        info = load_lang(lang)
        if isinstance(info, dict):
            cfg.update(info)
            if "profile" in info and isinstance(info["profile"], dict):
                for k, v in info["profile"].items():
                    if k not in cfg or cfg[k] is None:
                        cfg[k] = v
    except Exception:
        pass

    script = cfg.get("script") or LANG_TO_SCRIPT.get(lang, "latin")
    fallback = FALLBACK_PROFILES.get(script, FALLBACK_PROFILES["latin"])

    # Extract sample words - prefer language-level sample over profile sample
    words: list[str] = []
    sample = None
    if lang == "th":
        words = list(fallback["words"])
    else:
        if "info" in locals() and isinstance(info, dict) and info.get("sample"):
            sample = info["sample"]
        elif cfg.get("sample"):
            sample = cfg["sample"]

        if sample:
            if isinstance(sample, list):
                words = [str(w) for w in sample if str(w).strip()]
            elif isinstance(sample, str):
                words = [w for w in sample.split() if w]

        if len(words) < 5:
            words = list(fallback["words"])

    decimal = cfg.get("decimal") or fallback.get("decimal", ".")
    group = cfg.get("group") or fallback.get("group", [","])
    length_ratio = cfg.get("length_ratio") or fallback.get("length_ratio", [1.0, 1.0])
    linebreak = cfg.get("linebreak") or fallback.get("linebreak", "space")
    direction = cfg.get("direction") or fallback.get("direction", "ltr")

    return {
        "lang": lang,
        "script": script,
        "words": words,
        "decimal": decimal,
        "group": group,
        "length_ratio": length_ratio,
        "linebreak": linebreak,
        "direction": direction,
    }


def localize_numbers(
    text: str,
    decimal: str,
    group: list[str],
    src_decimal: str = ".",
    src_group: list[str] | tuple[str, ...] = (",",),
) -> str:
    """Format numbers from the source locale into the target locale while preserving ASCII digits."""
    text = text.translate(_FULLWIDTH_DIGITS)
    if src_decimal == decimal:
        if any(g.isspace() for g in group) and not any(g.isspace() for g in src_group):
            text = re.sub(r"(?<=\d)\s+(?=\d)", "/", text)
        return text
    if decimal == ",":
        if any(g.isspace() for g in group):
            # Space groups digits in this locale: "GPT-3 175B" would read as 3175 (GPT-3 gate, P2-fix2).
            text = re.sub(r"(?<=\d)\s+(?=\d)", "/", text)
        # First, preserve decimal points by marking with \x00
        text = re.sub(r"(?<=\d)\.(?=\d)", "\x00", text)
        # Thousands comma (followed by 3 digits) -> primary group separator
        prim_group = group[0] if group else "."
        text = re.sub(r"(?<=\d),(?=\d{3}(?!\d))", prim_group, text)
        # Comma list between digits without space (e.g. 1,2) -> ensure space so not parsed as decimal
        text = re.sub(r"(?<=\d),(?=\d)", ", ", text)
        # Replace decimal placeholder with comma
        return text.replace("\x00", ",")
    # Source is decimal-comma (e.g. fr/de/ru/vi) and target is decimal-point (e.g. en/zh/ja):
    # mirror rosettex.numbers.extract so number_changed compares identical values.
    dec = re.escape(src_decimal)
    group_chars = set(src_group)
    groups = "".join(re.escape(g) for g in group_chars)
    if groups:
        pat = (
            rf"(?:[0-9]{{1,2}}(?:[{groups}][0-9]{{2}})+[{groups}][0-9]{{3}}"
            rf"|[0-9]{{1,3}}(?:[{groups}][0-9]{{3}})+)"
            rf"(?:{dec}[0-9]+)?(?![0-9])|[0-9]+(?:{dec}[0-9]+)?"
        )
    else:
        pat = rf"[0-9]+(?:{dec}[0-9]+)?"
    if "." in group_chars:
        pat = pat.replace("(?![0-9])|", r"(?![0-9])|(?P<dot>[0-9]+(?:\.[0-9]+)+)(?![0-9])|", 1)

    def _to_target(m: re.Match) -> str:
        if m.groupdict().get("dot"):
            return m.group(0)
        s = m.group(0)
        for g in group_chars:
            if g != src_decimal:
                s = s.replace(g, "")
        return s.replace(src_decimal, decimal)

    return re.sub(pat, _to_target, text)


def _reorder_movable_placeholders(tokens: list[str], rng: random.Random) -> list[str]:
    """Reorder movable placeholders (⟦rN⟧ citations/references) while preserving all tokens."""
    r_indices = [i for i, t in enumerate(tokens) if re.match(r"^⟦r\d+⟧$", t)]
    if len(r_indices) >= 2:
        # Reverse or cycle the movable placeholders
        r_vals = [tokens[i] for i in r_indices]
        r_vals.reverse()
        for idx, val in zip(r_indices, r_vals, strict=False):
            tokens[idx] = val
    elif len(r_indices) == 1 and len(tokens) >= 3:
        # Swap with neighboring token if safe
        idx = r_indices[0]
        neighbor = idx + 1 if idx + 1 < len(tokens) else idx - 1
        # Avoid breaking paired tokens
        if not re.match(r"^⟦/?b\d+⟧$", tokens[neighbor]):
            tokens[idx], tokens[neighbor] = tokens[neighbor], tokens[idx]
    return tokens


def pseudo_translate_segment(
    src: str,
    sid: str,
    seg_num: int,
    cfg: dict[str, Any],
    zwsp: bool = False,
    src_cfg: dict[str, Any] | None = None,
) -> str:
    """Produce deterministic pseudo-translation for a single segment."""
    words: list[str] = cfg["words"]
    ratio = cfg["length_ratio"]
    mean_ratio = (ratio[0] + ratio[1]) / 2.0 if isinstance(ratio, (list, tuple)) else float(ratio)
    linebreak = cfg["linebreak"]
    decimal = cfg["decimal"]
    group = cfg["group"]
    src_decimal = (src_cfg or {}).get("decimal", ".")
    src_group = (src_cfg or {}).get("group", [","])

    # Deterministic RNG per segment
    seed_str = f"{cfg['lang']}:{sid}:{seg_num}"
    seed = int(hashlib.sha256(seed_str.encode("utf-8")).hexdigest()[:8], 16)
    rng = random.Random(seed)

    # Split into placeholders and text
    raw_parts = _PH.split(src)

    # Reorder movable placeholders if any
    parts = _reorder_movable_placeholders(raw_parts, rng)

    translated_parts = []
    for part in parts:
        if _PH.fullmatch(part):
            translated_parts.append(part)
        else:
            p = unicodedata.normalize("NFC", part)
            # Localize numbers from source locale into target locale
            p = localize_numbers(p, decimal, group, src_decimal, src_group)
            # Normalize CJK/fullwidth punctuation so non-CJK target fonts cover it
            p = p.translate(_CJK_PUNCT)

            # Match letter runs of any script (accented Latin as one word, CJK per character run)
            src_words = _WORD.findall(p)
            n_words = len(src_words)

            if n_words > 0:
                units = [max(1, (len(w) + 1) // 2) if _CJK_RE.search(w) else 1 for w in src_words]
                total_units = sum(units)
                target_count = max(n_words, round(total_units * mean_ratio))
                replacement_words = [rng.choice(words) for _ in range(target_count)]

                # Partition target words across source word positions
                sub_idx = 0
                pos = 0

                def repl(m: re.Match) -> str:
                    nonlocal sub_idx, pos
                    if sub_idx < n_words - 1:
                        share = max(1, round(units[sub_idx] * target_count / total_units))
                        max_take = max(1, len(replacement_words) - pos - (n_words - 1 - sub_idx))
                        take = min(share, max_take)
                        chunk = replacement_words[pos : pos + take]
                        pos += take
                    else:
                        chunk = replacement_words[pos:]
                    if not chunk:
                        chunk = [rng.choice(words)]
                    sub_idx += 1

                    if linebreak == "cjk":
                        return "".join(chunk)
                    elif linebreak == "zwsp" or zwsp:
                        return "\u200b".join(chunk)
                    else:
                        return " ".join(chunk)

                p = _WORD.sub(repl, p)

            translated_parts.append(p)

    text = "".join(translated_parts)

    if linebreak == "cjk":
        # Remove spaces between CJK characters
        text = re.sub(
            r"(?<=[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af])\s+(?=[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af])",
            "",
            text,
        )
    elif linebreak == "zwsp" or zwsp:
        # Insert U+200B between words of the script
        text = re.sub(
            r"(?<=[^\W\d_a-zA-Z])\s+(?=[^\W\d_a-zA-Z])",
            "\u200b",
            text,
        )

    # Insert special characters every 7th segment (spec §6: % & # _ $ { } ^)
    if seg_num % 7 == 0:
        text += " % & # _ $ { } ^"

    return text


def _existing_answered(out_dir: Path) -> set[str]:
    answered: set[str] = set()
    if not out_dir.is_dir():
        return answered
    for path in sorted(out_dir.glob("*.txt")):
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            m = _SEG_LINE.match(line)
            if m:
                sid, val = m.group(1), m.group(2).strip()
                if val:
                    answered.add(sid)
                else:
                    answered.discard(sid)
    return answered


def pseudo_translate(
    work: Path | str,
    lang: str = "vi",
    brk: int = 0,
    zwsp: bool = False,
    force: bool = False,
) -> tuple[int, int]:
    """Fill out/*.txt from read/*.md with pseudo-translations."""
    work_path = Path(work)
    read_dir = work_path / "read"
    out_dir = work_path / "out"
    out_dir.mkdir(parents=True, exist_ok=True)

    if not read_dir.is_dir():
        raise FileNotFoundError(f"missing read/ directory in {work}")

    cfg = get_lang_config(lang)
    src_cfg: dict[str, Any] | None = None
    proj_path = work_path / "project.json"
    if proj_path.is_file():
        try:
            proj = json.loads(proj_path.read_text(encoding="utf-8"))
            src_code = proj.get("from") or proj.get("source_lang")
            if src_code:
                src_cfg = get_lang_config(src_code)
        except Exception:
            pass
    answered = set() if force else _existing_answered(out_dir)

    n = broken = 0
    for path in sorted(read_dir.glob("*.md")):
        new_lines: list[str] = []
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            if not line or line.startswith("#") or line.startswith("  ~ "):
                continue
            m = _SEG_LINE.match(line)
            if not m:
                continue
            sid, src = m.group(1), m.group(2)
            if _DUP_VAL.match(src):
                continue
            if sid in answered:
                continue
            n += 1
            tr = pseudo_translate_segment(src, sid, n, cfg, zwsp=zwsp, src_cfg=src_cfg)
            if broken < brk and _PH.search(tr):
                tr = _PH.sub("", tr, count=1)
                broken += 1
            new_lines.append(f"{sid} {tr}")
            answered.add(sid)

        if new_lines:
            ans_path = out_dir / f"{path.stem}.txt"
            if force and ans_path.is_file():
                ans_path.unlink()
            existing = ans_path.read_text(encoding="utf-8-sig") if ans_path.is_file() else ""
            sep = "\n" if existing and not existing.endswith("\n") else ""
            ans_path.write_text(existing + sep + "\n".join(new_lines) + "\n", encoding="utf-8", newline="\n")

    return n, broken


def main() -> int:
    ap = argparse.ArgumentParser(description="Deterministic pseudo-translation for rosettex")
    ap.add_argument("work", help="workspace directory containing read/")
    ap.add_argument("--lang", default="vi", help="target language code (default: vi)")
    ap.add_argument(
        "--break", dest="brk", type=int, default=0, help="break N translations by dropping a placeholder"
    )
    ap.add_argument("--zwsp", action="store_true", help="insert U+200B zero-width space between words")
    ap.add_argument("--force", action="store_true", help="overwrite existing answer sheets")
    a = ap.parse_args()

    n, broken = pseudo_translate(a.work, lang=a.lang, brk=a.brk, zwsp=a.zwsp, force=a.force)
    print(f"filled {n} segments, broke {broken} (lang={a.lang})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

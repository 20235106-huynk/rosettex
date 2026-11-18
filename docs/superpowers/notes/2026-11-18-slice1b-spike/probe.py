import subprocess, sys, re, pathlib
OUT = pathlib.Path(sys.argv[1]); OUT.mkdir(parents=True, exist_ok=True)
TECT = "/home/admin/.rosettex/bin/tectonic"
BODY = "Sample sentence with the number 0.05 and the formula $x^2+1$ here."
PRE = "\\usepackage{polyglossia}\\setdefaultlanguage{english}\n"
def poly(lang, font_macro, font_spec, sample):
    return (PRE + f"\\newfontfamily\\{font_macro}[Script={font_spec[0]}]{{{font_spec[1]}}}\n"
            f"\\setotherlanguage{{{lang}}}\n\\begin{{{lang}}}{sample}\\end{{{lang}}}")
CASES = {
 "01-latin-vi": "\\usepackage{polyglossia}\\setdefaultlanguage{vietnamese}\n\\setmainfont{NotoSerif-Regular.ttf}\nTốc độ học 0,05 và công thức $E=mc^2$ trong bài báo này.",
 "02-cyrillic-ru": "\\usepackage{polyglossia}\\setdefaultlanguage{russian}\n\\setmainfont{NotoSerif-Regular.ttf}\nЭто пример текста на русском языке с числом 0.05 и $x^2$.",
 "03-han-zhs": "\\usepackage{xeCJK}\n\\setCJKmainfont{HaranoAjiMincho-Regular.otf}\n这是一个简体中文的例子，数字 0.05 和公式 $x^2$。",
 "04-kana-ja": "\\usepackage{xeCJK}\n\\setCJKmainfont{HaranoAjiMincho-Regular.otf}\nこれは日本語の例です。数字は0.05で、式は$x^2$です。",
 "05-hangul-ko": "\\usepackage{xeCJK}\n\\setCJKmainfont{UnBatang.ttf}\n이것은 한국어 예시입니다. 숫자 0.05 와 식 $x^2$.",
 "06-arabic-ar": PRE + "\\newfontfamily\\arabicfont[Script=Arabic]{Amiri-Regular.ttf}\n\\setotherlanguage{arabic}\n\\textarabic{هذا مثال على نص عربي مع الرقم 0.05} و\\textenglish{$x^2$} \\textarabic{والنص.}",
 "07-devanagari-hi": poly("hindi", "hindifont", ("Devanagari", "Mukta-Regular.ttf"), "यह हिंदी में एक उदाहरण पाठ है, संख्या 0.05 है।"),
 "08-bengali-bn": poly("bengali", "bengalifont", ("Bengali", "Baloo2-Regular.ttf"), "এটি বাংলায় একটি উদাহরণ পাঠ্য, সংখ্যা 0.05।"),
 "09-telugu-te": poly("telugu", "telugufont", ("Telugu", "Baloo2-Regular.ttf"), "ఇది తెలుగులో ఒక ఉదాహరణ వచనం, సంఖ్య 0.05."),
 "10-tamil-ta": poly("tamil", "tamilfont", ("Tamil", "HindMadurai-Regular.otf"), "இது தமிழில் ஒரு எடுத்துக்காட்டு உரை, எண் 0.05."),
 "11-gujarati-gu": poly("gujarati", "gujaratifont", ("Gujarati", "Baloo2-Regular.ttf"), "આ ગુજરાતીમાં એક ઉદાહરણ લખાણ છે, સંખ્યા 0.05.").replace("\\setotherlanguage{gujarati}", "\\setotherlanguage[hyphenmins={2,2}]{gujarati}"),
 "12-kannada-kn": poly("kannada", "kannadafont", ("Kannada", "Baloo2-Regular.ttf"), "ಇದು ಕನ್ನಡದಲ್ಲಿ ಒಂದು ಉದಾಹರಣೆ ಪಠ್ಯ, ಸಂಖ್ಯೆ 0.05."),
 "13-thai-th": poly("thai", "thaifont", ("Thai", "Garuda.otf"), "นี่คือตัวอย่างข้อความภาษาไทย ตัวเลข 0.05 และสูตร $x^2$"),
 "14-ethiopic-am-abs": poly("amharic", "amharicfont", ("Ethiopic", "/usr/share/fonts/google-droid-sans-fonts/DroidSansEthiopic-Regular.ttf"), "ይህ በአማርኛ የምሳሌ ጽሑፍ ነው ቁጥር 0.05 ነው።"),
 "15-myanmar-my": "\\newfontfamily\\myfont[Script=Myanmar]{Padauk-Regular.ttf}\n\\myfont ဤသည်မှာ မြန်မာဘာသာဖြင့် နမူနာစာသားဖြစ်ပါသည်။ နံပါတ် 0.05 ဖြစ်သည်။",
}
for name, body in CASES.items():
    # body = preamble lines + document text; split on the marker
    if "\\begin{" in body and body.count("\\begin{") and "\\setotherlanguage" in body:
        cut = body.index("\\begin{", body.index("\\setotherlanguage"))
        pre_part, text_part = body[:cut], body[cut:]
    elif body.startswith("\\newfontfamily\\myfont"):
        pre_part, text_part = body.split("\n", 1)[0] + "\n", body.split("\n", 1)[1]
    elif "\\usepackage{xeCJK}" in body:
        lines = body.split("\n"); pre_part, text_part = "\n".join(lines[:2]) + "\n", lines[2]
    else:
        lines = body.split("\n"); pre_part, text_part = "\n".join(lines[:-1]) + "\n", lines[-1]
    if name.startswith("06"):
        pre_part = PRE + "\\newfontfamily\\arabicfont[Script=Arabic]{Amiri-Regular.ttf}\n\\setotherlanguage{arabic}\n"
        text_part = "\\textarabic{هذا مثال على نص عربي مع الرقم 0.05} و\\textenglish{$x^2$} \\textarabic{والنص.}"
    tex = ("\\documentclass[11pt]{article}\n\\usepackage{fontspec}\n\\usepackage{amsmath}\n" + pre_part +
           "\\begin{document}\n" + text_part + "\n\n\\par " + BODY + "\n\\end{document}\n")
    f = OUT / f"{name}.tex"; f.write_text(tex, encoding="utf-8")
    try:
        p = subprocess.run([TECT, "--keep-logs", "-o", str(OUT), str(f)], capture_output=True, text=True, timeout=600)
        code = p.returncode; msg = (p.stderr or p.stdout).strip().splitlines(); err = msg[-1][:140] if msg and code else ""
    except subprocess.TimeoutExpired:
        code, err = "TIMEOUT", ""
    lt = (OUT / f"{name}.log").read_text(encoding="utf-8", errors="replace") if (OUT / f"{name}.log").exists() else ""
    miss = len(re.findall(r"Missing character", lt)); pdf = (OUT / f"{name}.pdf").exists()
    print(f"{name:22} exit={code} pdf={pdf} missing={miss} {err}", flush=True)

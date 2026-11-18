"""MinerU giả cho kiểm thử: `parse` trả zip dựng từ fixture (lọc theo --pages); `models verify` luôn đạt."""
import json
import os
import sys
import zipfile
from pathlib import Path

FIX = Path(__file__).parent / "fixtures" / "mineru-4.2.3-standard"


def main(argv):
    if argv[:2] == ["models", "verify"]:
        print("MinerU-4_models_onnx: ok")
        return 0
    assert argv[0] == "parse", argv
    if os.environ.get("FAKE_MINERU_FAIL_ALWAYS"):
        return 1
    flag = os.environ.get("FAKE_MINERU_FAIL_ONCE")
    if flag and not Path(flag).exists():
        Path(flag).write_text("failed")
        return 1
    out = Path(argv[argv.index("-o") + 1])
    a, b = map(int, argv[argv.index("--pages") + 1].split("-"))
    with zipfile.ZipFile(FIX / "short.zip") as src:
        middle = json.loads(src.read("middle_json.json"))
        keep = lambda p: a - 1 <= p["page_idx"] <= b - 1  # noqa: E731
        middle["pages"] = [p for p in middle["pages"] if keep(p)]
        lay = middle["extensions"]["docvortex_layout"]
        lay["pages"] = [p for p in lay["pages"] if keep(p)]
        out.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(out / "src.zip", "w") as z:
            z.writestr("middle_json.json", json.dumps(middle, ensure_ascii=False))
            for n in src.namelist():
                if n.startswith("images/"):
                    z.writestr(n, src.read(n))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

import hashlib
import json
from importlib.resources import files

FONTS = files("rosettex") / "fonts"


def manifest():
    return json.loads((FONTS / "MANIFEST.json").read_text(encoding="utf-8"))


def test_manifest_matches_files():
    m = manifest()
    assert m["schema"] == "rosettex.fonts/1" and m["unihan"]["url"].endswith("/18.0.0/ucd/Unihan.zip")
    names = {p.name for p in FONTS.iterdir() if p.name.endswith((".ttf", ".otf"))}
    assert {e["file"] for e in m["fonts"]} == names and len(names) == 14
    for e in m["fonts"]:
        data = (FONTS / e["file"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == e["sha256"] and len(data) == e["bytes"]
        assert e["license"] == "OFL-1.1" and (FONTS / e["license_file"]).is_file()
        assert e["source"].startswith("https://github.com/notofonts/") and len(e["source_sha256"]) == 64

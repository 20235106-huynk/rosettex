import shutil

import pytest

from rosettex import fonts
from rosettex.checks import load_pack
from rosettex.fonts import FontMissing, font_error, fontspec, resolve_fonts, stage_fonts


def test_package_font_resolves_and_stages(tmp_path):
    r = resolve_fonts(load_pack("ar"))
    assert r["script"]["file"] == "NotoNaskhArabic-Regular.ttf" and r["latin"]["file"] == "texgyretermes"
    f, opts = fontspec(r["script"])
    assert f == "NotoNaskhArabic-Regular.ttf" and opts.startswith("Path=fonts/")
    out = tmp_path / "Tài liệu" / "out"
    rec = stage_fonts(r, out)
    assert (out / "fonts" / f).read_bytes() == (fonts.package_dir() / f).read_bytes()
    assert {"file": f, "source": "package", "license": "OFL-1.1"} in rec
    assert stage_fonts(r, out) == rec  # idempotent


def test_bundle_font_needs_no_path():
    r = resolve_fonts(load_pack("vi"))
    assert r["script"]["file"] == "texgyretermes" and "Path=" not in fontspec(r["script"])[1]


def test_damaged_package_font_is_missing(tmp_path, monkeypatch):
    d = tmp_path / "fonts"
    shutil.copytree(fonts.package_dir(), d)
    (d / "NotoSerifBengali-Regular.ttf").write_bytes(b"broken")
    monkeypatch.setattr(fonts, "package_dir", lambda: d)
    with pytest.raises(FontMissing) as e:
        resolve_fonts(load_pack("bn"))
    j = e.value.to_json()
    assert j["font_missing"] is True and j["script"] == "bengali"
    assert j["fonts_needed"] == [{"file": "NotoSerifBengali-Regular.ttf", "license": "OFL-1.1"}]
    assert "reinstall" in str(e.value)


def test_font_error_parsing():
    assert font_error('Package fontspec Error: The font "NotoSerif-Bold.ttf" cannot be\nfound.') == "NotoSerif-Bold.ttf"
    assert font_error("all fine") is None

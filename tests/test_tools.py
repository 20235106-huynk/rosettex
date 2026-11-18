import os
import sys

from rosettex.tools import available_ram_bytes, choose_tier, find_mineru, find_tex


def test_choose_tier():
    assert choose_tier(None, 16 * 1024**3) == ("standard", "default")
    assert choose_tier(None, 3 * 1024**3) == ("basic", "low_ram")
    assert choose_tier(None, None) == ("standard", "default")
    assert choose_tier("basic", 64 * 1024**3) == ("basic", "requested")


def test_find_mineru_env_python_script(tmp_path, monkeypatch):
    s = tmp_path / "fake mineru.py"
    s.write_text("")
    monkeypatch.setenv("ROSETTEX_MINERU", str(s))
    assert find_mineru() == [sys.executable, str(s)]


def test_find_mineru_none(tmp_path, monkeypatch):
    monkeypatch.delenv("ROSETTEX_MINERU", raising=False)
    monkeypatch.setenv("ROSETTEX_HOME", str(tmp_path))
    monkeypatch.setenv("PATH", str(tmp_path))
    assert find_mineru() is None


def test_find_tex_prefers_home_bin(tmp_path, monkeypatch):
    monkeypatch.delenv("ROSETTEX_TEX", raising=False)
    monkeypatch.setenv("ROSETTEX_HOME", str(tmp_path))
    exe = tmp_path / "bin" / ("tectonic.exe" if os.name == "nt" else "tectonic")
    exe.parent.mkdir()
    exe.write_text("")
    assert find_tex() == ("tectonic", [str(exe)])


def test_available_ram_positive_or_none():
    r = available_ram_bytes()
    assert r is None or r > 0


def test_verify_models_timeout_and_missing_command(tmp_path):
    from rosettex.tools import verify_models
    s = tmp_path / "slow.py"
    s.write_text("import time; time.sleep(5)")
    ok, msg = verify_models([sys.executable, str(s)], "standard", timeout=0.3)
    assert ok is False and msg
    ok, msg = verify_models([str(tmp_path / "nonexistent-bin")], "standard")
    assert ok is False and msg

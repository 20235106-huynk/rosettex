import json
import os
import shutil
import sys
import time

import pytest

from conftest import FIX, TERMS, fake_vi
from rosettex.cli import main
from rosettex.tools import find_tex
from rosettex.workspace import Work, beat, read_json

FAKE = str(FIX.parent / "fake_mineru.py")
needs_tex = pytest.mark.skipif(find_tex() is None, reason="cần Tectonic hoặc XeLaTeX")


def run(capsys, *argv):
    code = main([str(a) for a in argv])
    out = capsys.readouterr().out
    return code, (json.loads(out) if out.lstrip().startswith("{") else out)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("ROSETTEX_MINERU", FAKE)
    monkeypatch.setenv("ROSETTEX_HOME", str(tmp_path / "home"))
    src = tmp_path / "Bài báo" / "short paper.pdf"
    src.parent.mkdir()
    shutil.copyfile(FIX / "papers" / "short.pdf", src)
    return src, tmp_path / "Kết quả mới"


def translate_via_cli(capsys, work, tmp_path):
    (tmp_path / "terms.json").write_text(json.dumps(TERMS, ensure_ascii=False), encoding="utf-8")
    assert run(capsys, "terms", work, "--file", tmp_path / "terms.json", "--json")[0] == 0
    while True:
        code, pkg = run(capsys, "next", work, "--json")
        assert code == 0
        if pkg["done"]:
            return
        f = tmp_path / f"{pkg['batch']['batch']}.json"
        f.write_text(json.dumps({it["id"]: fake_vi(it["text"]) for it in pkg["batch"]["items"]},
                                ensure_ascii=False), encoding="utf-8")
        code, res = run(capsys, "accept", work, "--batch", pkg["batch"]["batch"], "--file", f, "--json")
        assert code == 0, res


def test_full_flow_foreground(env, capsys, tmp_path):  # Review Focus 1 và 4
    src, outdir = env
    code, r = run(capsys, "extract", src, "--lang", "vi", "--out", outdir, "--foreground", "--json")
    assert code == 0 and r["state"] == "extracted" and r["output"].endswith("short paper.vi.pdf")
    assert r["output_exists"] is False and r["lang_tested"] is True
    work = r["work"]
    assert run(capsys, "status", work, "--json")[1]["state"] == "need_terms"
    code, o = run(capsys, "next", work, "--outline", "--json")
    assert o["title"].startswith("Attention-Based")
    translate_via_cli(capsys, work, tmp_path)
    assert run(capsys, "status", work, "--json")[1]["state"] == "need_validate"
    code, v = run(capsys, "validate", work, "--json")
    assert code == 0 and v["ok"] is True
    st = run(capsys, "status", work, "--json")[1]
    assert st["state"] == "need_render" and "rosettex render" in st["next_action"]
    if find_tex() is None:
        return
    code, rep = run(capsys, "render", work, "--foreground", "--json")
    assert code == 0, rep
    assert (outdir / "short paper.vi.pdf").is_file()
    assert run(capsys, "status", work, "--json")[1]["state"] == "done"
    code, again = run(capsys, "render", work, "--foreground", "--json")
    assert code == 2 and again["output_exists"] is True
    code, _ = run(capsys, "render", work, "--foreground", "--overwrite", "--json")
    assert code == 0


def test_extract_in_background(env, capsys):
    src, outdir = env
    code, r = run(capsys, "extract", src, "--lang", "vi", "--out", outdir / "x.pdf", "--json")
    assert code == 0 and r["state"] == "started" and r["next_action"].startswith("rosettex status")
    deadline = time.time() + 90
    while time.time() < deadline:
        st = run(capsys, "status", r["work"], "--json")[1]
        if st["state"] not in ("extract_running", "not_extracted"):
            break
        time.sleep(1)
    assert st["state"] == "need_terms", st


def test_status_reports_dead_job(env, capsys):
    src, outdir = env
    code, r = run(capsys, "extract", src, "--lang", "vi", "--out", outdir, "--foreground", "--json")
    w = Work(r["work"])
    w.document.unlink()
    beat(w, "extract", "rosettex extract RESUME", "nhóm 1/1", 0, 1)
    p = read_json(w.progress)
    p["updated"] -= 3600
    w.progress.write_text(json.dumps(p), encoding="utf-8")
    st = run(capsys, "status", w.root, "--json")[1]
    assert st["state"] == "extract_dead" and st["next_action"] == "rosettex extract RESUME"


def test_errors_and_exit_codes(env, capsys, tmp_path, monkeypatch):
    src, outdir = env
    code, r = run(capsys, "extract", tmp_path / "không có.pdf", "--lang", "vi", "--out", outdir, "--json")
    assert code == 2 and "không tìm thấy" in r["error"]
    monkeypatch.setenv("ROSETTEX_MINERU", str(tmp_path / "missing.py"))
    monkeypatch.setenv("PATH", str(tmp_path))
    code, r = run(capsys, "extract", src, "--lang", "vi", "--out", outdir, "--json")
    assert code == 2 and "MinerU" in r["error"]
    code, r = run(capsys, "clean", tmp_path, "--json")
    assert code == 2 and tmp_path.exists()


def test_accept_rejection_exit_1(env, capsys, tmp_path):
    src, outdir = env
    work = run(capsys, "extract", src, "--lang", "vi", "--out", outdir, "--foreground", "--json")[1]["work"]
    (tmp_path / "terms.json").write_text(json.dumps(TERMS), encoding="utf-8")
    run(capsys, "terms", work, "--file", tmp_path / "terms.json", "--json")
    pkg = run(capsys, "next", work, "--json")[1]
    f = tmp_path / "bad.json"
    f.write_text(json.dumps({it["id"]: it["text"] for it in pkg["batch"]["items"]}), encoding="utf-8")
    code, res = run(capsys, "accept", work, "--batch", "b001", "--file", f, "--json")
    assert code == 1 and res["ok"] is False and "rosettex accept" in res["next_action"]


def test_guide_and_version(capsys):
    code, text = run(capsys, "guide")
    assert code == 0 and "rosettex extract" in text
    with pytest.raises(SystemExit):
        main(["--version"])


@pytest.mark.skipif(not os.environ.get("ROSETTEX_REAL"), reason="đặt ROSETTEX_REAL=1 để chạy với MinerU thật")
@pytest.mark.parametrize("name", ["short", "long"])
def test_real_mineru_end_to_end(name, tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("ROSETTEX_MINERU", raising=False)
    code, r = run(capsys, "extract", FIX / "papers" / f"{name}.pdf", "--lang", "vi", "--out", tmp_path / "out",
                  "--foreground", "--json")
    assert code == 0, r
    translate_via_cli(capsys, r["work"], tmp_path)
    assert run(capsys, "validate", r["work"], "--json")[0] == 0
    if find_tex():
        code, rep = run(capsys, "render", r["work"], "--foreground", "--json")
        assert code == 0, rep["postcheck"]


def test_batch_id_guard_and_next_action(env, capsys, tmp_path):
    src, outdir = env
    work = run(capsys, "extract", src, "--lang", "vi", "--out", outdir, "--foreground", "--json")[1]["work"]
    f = tmp_path / "x.json"
    f.write_text("{}", encoding="utf-8")
    code, r = run(capsys, "accept", work, "--batch", "../../x", "--file", f, "--json")
    assert code == 2 and "next_action" in r
    code, r = run(capsys, "next", work, "--batch", "..", "--json")
    assert code == 2
    code, r = run(capsys, "validate", work, "--json")
    assert code == 1 and r["next_action"]


def test_render_refuses_existing_output_without_tex(env, capsys, tmp_path, monkeypatch):  # Review Focus 4, không cần TeX
    src, outdir = env
    r = run(capsys, "extract", src, "--lang", "vi", "--out", outdir, "--foreground", "--json")[1]
    assert outdir.is_dir()  # thư mục kết quả thiếu được tự tạo
    translate_via_cli(capsys, r["work"], tmp_path)
    assert run(capsys, "validate", r["work"], "--json")[0] == 0
    (outdir / "short paper.vi.pdf").write_bytes(b"x")
    code, res = run(capsys, "render", r["work"], "--foreground", "--json")
    assert code == 2 and res["output_exists"] is True and res["next_action"]
    assert (outdir / "short paper.vi.pdf").read_bytes() == b"x"


def test_extract_failure_exit_2_with_runnable_next_action(env, capsys, monkeypatch):
    src, outdir = env
    monkeypatch.setenv("FAKE_MINERU_FAIL_ALWAYS", "1")
    code, r = run(capsys, "extract", src, "--lang", "vi", "--out", outdir, "--foreground", "--json")
    assert code == 2 and r["next_action"].startswith("rosettex extract")


def _fail_status(capsys, w):
    return run(capsys, "status", w, "--json")[1]


def test_crash_in_render_core_is_failed_not_endless_resume(env, capsys, tmp_path, monkeypatch):
    import rosettex.cli as cli
    src, outdir = env
    r = run(capsys, "extract", src, "--lang", "vi", "--out", outdir, "--foreground", "--json")[1]
    translate_via_cli(capsys, r["work"], tmp_path)
    assert run(capsys, "validate", r["work"], "--json")[0] == 0
    monkeypatch.setattr(cli, "find_tex", lambda: ("fake", []))
    monkeypatch.setattr(cli, "render_core", lambda *a, **k: {}["boom"])
    code, res = run(capsys, "render", r["work"], "--foreground", "--json")
    assert code == 2 and "KeyError" in res["error"] and res["next_action"] is None
    st = _fail_status(capsys, r["work"])
    assert st["state"] == "render_failed" and "KeyError" in st["error"] and st["next_action"] is None


def test_crash_in_extract_core_is_failed(env, capsys, monkeypatch):
    import rosettex.cli as cli
    src, outdir = env
    monkeypatch.setattr(cli, "extract_core", lambda *a, **k: {}["boom"])
    code, res = run(capsys, "extract", src, "--lang", "vi", "--out", outdir, "--foreground", "--json")
    assert code == 2 and res["next_action"] is None
    w = Work(outdir.parent / "Kết quả mới" / ".rosettex" / "short paper")
    st = _fail_status(capsys, w.root)
    assert st["state"] == "extract_failed" and "KeyError" in st["error"] and st["next_action"] is None


def test_extract_failure_at_basic_tier_stops(env, capsys, monkeypatch):
    src, outdir = env
    monkeypatch.setenv("FAKE_MINERU_FAIL_ALWAYS", "1")
    code, r = run(capsys, "extract", src, "--lang", "vi", "--out", outdir, "--tier", "basic", "--foreground", "--json")
    assert code == 2 and r["next_action"] is None
    st = _fail_status(capsys, outdir / ".rosettex" / "short paper")
    assert st["next_action"] is None and st["error"]


def test_dead_job_resumed_once_then_stops(env, capsys):
    src, outdir = env
    r = run(capsys, "extract", src, "--lang", "vi", "--out", outdir, "--foreground", "--json")[1]
    w = Work(r["work"])
    w.document.unlink()

    def die():
        beat(w, "extract", "rosettex extract RESUME", "nhóm 1/1", 0, 1)
        p = read_json(w.progress)
        p["updated"] -= 3600
        w.progress.write_text(json.dumps(p), encoding="utf-8")
    die()
    assert run(capsys, "status", w.root, "--json")[1]["next_action"] == "rosettex extract RESUME"
    die()  # lần chạy lại cũng chết (beat giữ bộ đếm)
    st = run(capsys, "status", w.root, "--json")[1]
    assert st["next_action"] is None and st["error"]


# ---------------- đợt sửa sau review ----------------
def _foreign_dir(tmp_path):
    d = tmp_path / "dự án khác"
    d.mkdir()
    (d / "manifest.json").write_text('{"name": "app", "version": 1}', encoding="utf-8")
    (d / "keep.txt").write_text("x", encoding="utf-8")
    return d


def test_clean_refuses_foreign_manifest(capsys, tmp_path):
    d = _foreign_dir(tmp_path)
    code, r = run(capsys, "clean", d, "--json")
    assert code == 2 and r["next_action"] is not None and (d / "keep.txt").exists()


def test_extract_into_foreign_manifest_dir_is_usage_error(env, capsys, tmp_path):
    src, outdir = env
    d = _foreign_dir(tmp_path)
    code, r = run(capsys, "extract", src, "--lang", "vi", "--out", outdir, "-o", d, "--foreground", "--json")
    assert code == 2 and r["next_action"] and (d / "keep.txt").exists()


def test_extract_writes_schema_marker_and_clean_works(env, capsys):
    src, outdir = env
    w = Work(run(capsys, "extract", src, "--lang", "vi", "--out", outdir, "--foreground", "--json")[1]["work"])
    assert read_json(w.manifest)["schema"] == "rosettex.work/1"
    assert run(capsys, "clean", w.root, "--json")[0] == 0 and not w.root.exists()

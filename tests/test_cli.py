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
needs_tex = pytest.mark.skipif(find_tex() is None, reason="needs Tectonic or XeLaTeX")


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


def test_full_flow_foreground(env, capsys, tmp_path):  # Review Focus 1 and 4
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
    beat(w, "extract", "rosettex extract RESUME", "group 1/1", 0, 1)
    p = read_json(w.progress)
    p["updated"] -= 3600
    w.progress.write_text(json.dumps(p), encoding="utf-8")
    st = run(capsys, "status", w.root, "--json")[1]
    assert st["state"] == "extract_dead" and st["next_action"] == "rosettex extract RESUME"


def test_errors_and_exit_codes(env, capsys, tmp_path, monkeypatch):
    src, outdir = env
    code, r = run(capsys, "extract", tmp_path / "không có.pdf", "--lang", "vi", "--out", outdir, "--json")
    assert code == 2 and "file not found" in r["error"]
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


@pytest.mark.skipif(not os.environ.get("ROSETTEX_REAL"), reason="set ROSETTEX_REAL=1 to run with real MinerU")
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


def test_render_refuses_existing_output_without_tex(env, capsys, tmp_path, monkeypatch):  # Review Focus 4, no TeX needed
    src, outdir = env
    r = run(capsys, "extract", src, "--lang", "vi", "--out", outdir, "--foreground", "--json")[1]
    assert outdir.is_dir()  # the missing output directory is created automatically
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
    w = Work(outdir.parent / "Kết quả mới" / ".rosettex" / "short paper.vi")
    st = _fail_status(capsys, w.root)
    assert st["state"] == "extract_failed" and "KeyError" in st["error"] and st["next_action"] is None


def test_extract_failure_at_basic_tier_stops(env, capsys, monkeypatch):
    src, outdir = env
    monkeypatch.setenv("FAKE_MINERU_FAIL_ALWAYS", "1")
    code, r = run(capsys, "extract", src, "--lang", "vi", "--out", outdir, "--tier", "basic", "--foreground", "--json")
    assert code == 2 and r["next_action"] is None
    st = _fail_status(capsys, outdir / ".rosettex" / "short paper.vi")
    assert st["next_action"] is None and st["error"]


def _die(w):
    beat(w, "extract", "rosettex extract RESUME", "group 1/1", 0, 1)
    p = read_json(w.progress)
    p["updated"] -= 3600
    w.progress.write_text(json.dumps(p), encoding="utf-8")


def test_dead_job_status_is_read_only_and_repeatable(env, capsys):
    src, outdir = env
    w = Work(run(capsys, "extract", src, "--lang", "vi", "--out", outdir, "--foreground", "--json")[1]["work"])
    w.document.unlink()
    _die(w)
    before = w.progress.read_bytes()
    for _ in range(3):
        assert run(capsys, "status", w.root, "--json")[1]["next_action"] == "rosettex extract RESUME"
    assert w.progress.read_bytes() == before


def test_dead_job_resumed_once_then_stops(env, capsys):
    src, outdir = env
    w = Work(run(capsys, "extract", src, "--lang", "vi", "--out", outdir, "--foreground", "--json")[1]["work"])
    w.document.unlink()
    _die(w)
    assert run(capsys, "status", w.root, "--json")[1]["next_action"] == "rosettex extract RESUME"
    _die(w)  # the rerun process really starts (beat counts reruns) and then dies again
    st = run(capsys, "status", w.root, "--json")[1]
    assert st["next_action"] is None and st["error"]


# ---------------- fixes after review ----------------
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


def test_extract_hang_times_out_and_fails(env, capsys, monkeypatch):
    src, outdir = env
    monkeypatch.setenv("FAKE_MINERU_HANG", "1")
    monkeypatch.setenv("ROSETTEX_TIMEOUT_SCALE", "0.0005")
    t0 = time.time()
    code, r = run(capsys, "extract", src, "--lang", "vi", "--out", outdir, "--foreground", "--json")
    assert time.time() - t0 < 30
    assert code == 2 and "timed out" in r["error"] and r["next_action"].startswith("rosettex extract")
    st = _fail_status(capsys, outdir / ".rosettex" / "short paper.vi")
    assert st["state"] == "extract_failed"


@needs_tex
def test_render_hang_times_out_and_fails(env, capsys, tmp_path, monkeypatch):
    import rosettex.cli as cli
    import rosettex.render.compile as comp
    src, outdir = env
    r = run(capsys, "extract", src, "--lang", "vi", "--out", outdir, "--foreground", "--json")[1]
    translate_via_cli(capsys, r["work"], tmp_path)
    assert run(capsys, "validate", r["work"], "--json")[0] == 0
    hang = ("tectonic", [sys.executable, "-c", "import time; time.sleep(60)"])
    monkeypatch.setattr(cli, "find_tex", lambda: hang)
    monkeypatch.setattr(comp, "find_tex", lambda: hang)
    monkeypatch.setenv("ROSETTEX_TIMEOUT_SCALE", "0.005")
    code, res = run(capsys, "render", r["work"], "--foreground", "--json")
    assert code == 2 and "timed out" in res["error"]
    st = _fail_status(capsys, r["work"])
    assert st["state"] == "render_failed" and st["next_action"] is None


def test_extract_other_lang_gets_own_work_dir_and_conflict_is_refused(env, capsys):
    src, outdir = env
    vi = run(capsys, "extract", src, "--lang", "vi", "--out", outdir, "--foreground", "--json")[1]
    fr = run(capsys, "extract", src, "--lang", "fr", "--out", outdir, "--foreground", "--json")[1]
    assert vi["work"] != fr["work"] and read_json(Work(fr["work"]).manifest)["lang"] == "fr"
    code, r = run(capsys, "extract", src, "--lang", "fr", "--out", outdir, "-o", vi["work"], "--foreground", "--json")
    assert code == 2 and "vi" in r["error"] and "clean" in r["next_action"]
    assert read_json(Work(vi["work"]).manifest)["lang"] == "vi"


def test_extract_refuses_nonempty_foreign_dir_and_clean_keeps_user_files(env, capsys, tmp_path):
    src, outdir = env
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "keep.txt").write_text("x", encoding="utf-8")
    code, r = run(capsys, "extract", src, "--lang", "vi", "--out", outdir, "-o", proj, "--foreground", "--json")
    assert code == 2 and r["next_action"] and (proj / "keep.txt").exists()
    assert not (proj / "manifest.json").exists()


def test_clean_removes_only_owned_entries(env, capsys, tmp_path):
    src, outdir = env
    w = Work(run(capsys, "extract", src, "--lang", "vi", "--out", outdir, "--foreground", "--json")[1]["work"])
    (w.root / "keep.txt").write_text("x", encoding="utf-8")
    code, r = run(capsys, "clean", w.root, "--json")
    assert code == 0 and r["kept"] == ["keep.txt"] and (w.root / "keep.txt").exists()
    assert sorted(p.name for p in w.root.iterdir()) == ["keep.txt"]


def test_clean_foreign_hint_has_no_dash_o(capsys, tmp_path):
    d = _foreign_dir(tmp_path)
    code, r = run(capsys, "clean", d, "--json")
    assert code == 2 and "-o" not in r["next_action"]
    code, r = run(capsys, "status", d, "--json")
    assert code == 2 and "-o" not in r["next_action"]


# ---------------- 3 fixes after slice 1 ----------------
def test_extract_corrupt_pdf_is_usage_error_and_leaves_no_dir(env, capsys, tmp_path):
    _, outdir = env
    bad = tmp_path / "x.pdf"
    bad.write_bytes(b"garbage, not a pdf")
    code, r = run(capsys, "extract", bad, "--lang", "vi", "--out", outdir, "--foreground", "--json")
    assert code == 2 and r["next_action"] and "x.pdf" in r["error"]
    assert not (outdir / ".rosettex").exists()


def _leftover(env_, subdir_files):
    src, outdir = env_
    d = outdir / ".rosettex" / f"{src.stem}.vi"
    d.mkdir(parents=True)
    for n in subdir_files:
        (d / n).write_text("x", encoding="utf-8")
    return d


def test_leftover_dir_with_only_owned_is_reusable_and_cleanable(env, capsys):
    src, outdir = env
    d = _leftover(env, ["src.pdf", ".manifest.json.tmp"])
    assert run(capsys, "clean", d, "--json")[0] == 0 and not d.exists()
    d = _leftover(env, ["src.pdf"])
    code, r = run(capsys, "extract", src, "--lang", "vi", "--out", outdir, "--foreground", "--json")
    assert code == 0 and Work(r["work"]).root == d.resolve() and Work(d).manifest.exists()


def test_leftover_dir_with_user_file_still_refused(env, capsys):
    src, outdir = env
    d = _leftover(env, ["src.pdf", "keep.txt"])
    code, r = run(capsys, "extract", src, "--lang", "vi", "--out", outdir, "--foreground", "--json")
    assert code == 2 and (d / "keep.txt").exists()
    code, r = run(capsys, "clean", d, "--json")
    assert code == 2 and (d / "keep.txt").exists() and (d / "src.pdf").exists()


def _extracted(env, capsys):
    src, outdir = env
    return Work(run(capsys, "extract", src, "--lang", "vi", "--out", outdir, "--foreground", "--json")[1]["work"])


def test_clean_symlinked_out_is_retryable(env, capsys, tmp_path):
    w = _extracted(env, capsys)
    target = tmp_path / "elsewhere"
    target.mkdir()
    (target / "f").write_text("x", encoding="utf-8")
    shutil.rmtree(w.root / "out", ignore_errors=True)
    try:
        (w.root / "out").symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("cannot create symlink")
    code, r = run(capsys, "clean", w.root, "--json")
    assert code == 0 and (target / "f").exists() and not w.root.exists()


def test_clean_tmp_directory_is_kept_without_traceback(env, capsys):
    w = _extracted(env, capsys)
    obstacle = w.root / ".x.tmp"
    obstacle.mkdir()
    code, r = run(capsys, "clean", w.root, "--json")
    assert code == 0 and r["kept"] == [".x.tmp"] and r["removed"] is False
    obstacle.rmdir()


def test_clean_oserror_keeps_manifest_and_gives_next_action(env, capsys, monkeypatch):
    w = _extracted(env, capsys)
    real = type(w.src_pdf).unlink

    def boom(self, *a, **k):
        if self.name == "src.pdf":
            raise PermissionError("locked")
        return real(self, *a, **k)
    monkeypatch.setattr(type(w.src_pdf), "unlink", boom)
    code, r = run(capsys, "clean", w.root, "--json")
    assert code == 2 and "clean" in r["next_action"] and w.manifest.exists()
    monkeypatch.undo()
    assert run(capsys, "clean", w.root, "--json")[0] == 0 and not w.root.exists()


def test_clean_reports_removed_truthfully(env, capsys):
    w = _extracted(env, capsys)
    (w.root / "keep.txt").write_text("x", encoding="utf-8")
    code, r = run(capsys, "clean", w.root, "--json")
    assert code == 0 and r["removed"] is False and r["kept"] == ["keep.txt"]
    (w.root / "keep.txt").unlink()
    w2 = _extracted(env, capsys)
    code, r = run(capsys, "clean", w2.root, "--json")
    assert r["removed"] == str(w2.root) and r["kept"] == []


def test_user_out_dir_is_not_adopted_or_cleaned(env, capsys, tmp_path):
    src, outdir = env
    d = tmp_path / "build"
    (d / "out").mkdir(parents=True)
    (d / "out" / "userfile").write_text("x", encoding="utf-8")
    code, _ = run(capsys, "extract", src, "--lang", "vi", "--out", outdir, "-o", d, "--foreground", "--json")
    assert code == 2 and (d / "out" / "userfile").exists()
    code, _ = run(capsys, "clean", d, "--json")
    assert code == 2 and (d / "out" / "userfile").exists()


def test_clean_refuses_empty_dir(capsys, tmp_path):
    d = tmp_path / "empty"
    d.mkdir()
    assert run(capsys, "clean", d, "--json")[0] == 2 and d.exists()


def test_leftover_with_src_and_logs_is_cleanable(env, capsys):
    d = _leftover(env, ["src.pdf"])
    (d / "logs").mkdir()
    assert run(capsys, "clean", d, "--json")[0] == 0 and not d.exists()


def test_extract_fails_early_on_damaged_font(env, capsys, tmp_path, monkeypatch):
    from rosettex import fonts
    d = tmp_path / "fonts"
    shutil.copytree(fonts.package_dir(), d)
    (d / "NotoSerifBengali-Regular.ttf").write_bytes(b"broken")
    monkeypatch.setattr(fonts, "package_dir", lambda: d)
    monkeypatch.setenv("ROSETTEX_MINERU", str(tmp_path / "does-not-exist"))  # must not be reached
    code, r = run(capsys, "extract", FIX / "papers" / "short.pdf", "--lang", "bn", "--out", tmp_path / "res", "-o", tmp_path / "o", "--json")
    assert code == 2 and r["font_missing"] is True and r["script"] == "bengali" and r["next_action"] is None
    assert not (tmp_path / "o").exists() and not (tmp_path / "res").exists()


@pytest.mark.parametrize("lang,script,found,known", [("ar", "arabic", True, True), ("ps", "arabic", False, True),
                                                     ("es", "latin", False, True), ("xx", "latin", False, False)])
def test_extract_reports_script(env, capsys, tmp_path, lang, script, found, known):
    code, r = run(capsys, "extract", FIX / "papers" / "short.pdf", "--lang", lang, "--out", tmp_path / "res", "-o", tmp_path / "o",
                  "--foreground", "--json")
    assert code == 0 and r["script"] == script and r["pack_found"] is found and r["script_known"] is known
    assert r["lang_tested"] is False and r["fonts"]


def test_missing_manifest_is_font_missing_not_a_traceback(tmp_path, monkeypatch):
    from rosettex import fonts
    from rosettex.checks import load_pack
    monkeypatch.setattr(fonts, "package_dir", lambda: tmp_path)  # empty: no manifest, no fonts
    with pytest.raises(fonts.FontMissing):
        fonts.resolve_fonts(load_pack("bn"))


def test_build_time_font_error_reaches_status(env, capsys, tmp_path, monkeypatch):
    import rosettex.cli as cli
    from rosettex.fonts import FontMissing
    src, outdir = env
    r = run(capsys, "extract", src, "--lang", "vi", "--out", outdir, "--foreground", "--json")[1]
    translate_via_cli(capsys, r["work"], tmp_path)
    assert run(capsys, "validate", r["work"], "--json")[0] == 0
    monkeypatch.setattr(cli, "find_tex", lambda: ("fake", []))

    def boom(*a, **k):
        raise FontMissing("latin", [{"file": "NotoSerif-Bold.ttf", "license": "OFL-1.1"}], message="TeX could not load the font")
    monkeypatch.setattr(cli, "render_core", boom)
    code, res = run(capsys, "render", r["work"], "--foreground", "--json")
    assert code == 2 and res["font_missing"] is True
    st = run(capsys, "status", r["work"], "--json")[1]
    assert st["state"] == "render_failed" and st["font_missing"] is True
    assert st["fonts_needed"] == [{"file": "NotoSerif-Bold.ttf", "license": "OFL-1.1"}]

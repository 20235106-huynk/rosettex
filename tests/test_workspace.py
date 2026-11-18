import sys
import time

from rosettex.workspace import (HEARTBEAT_DEAD_AFTER, Work, beat, job_state, read_json,
                                run_with_heartbeat, spawn_detached, write_json)


def test_write_read_roundtrip_non_ascii_path(tmp_path):
    p = tmp_path / "Tài liệu có dấu cách" / "x.json"
    write_json(p, {"chữ": "ệ ữ ẫ"})
    assert read_json(p) == {"chữ": "ệ ữ ẫ"}
    assert b"\r\n" not in p.read_bytes()


def test_read_json_accepts_bom_and_missing(tmp_path):
    p = tmp_path / "bom.json"
    p.write_bytes(b"\xef\xbb\xbf" + '{"a": "đ"}'.encode("utf-8"))
    assert read_json(p) == {"a": "đ"}
    assert read_json(tmp_path / "none.json", default=7) == 7


def test_job_state_detects_dead_heartbeat(tmp_path):
    w = Work(tmp_path)
    beat(w, "extract", "rosettex extract x", "group 1/4", 1, 4)
    fresh = job_state(w)
    assert fresh["state"] == "running" and fresh["dead"] is False
    assert job_state(w, now=fresh["updated"] + HEARTBEAT_DEAD_AFTER + 1)["dead"] is True
    beat(w, "extract", "rosettex extract x", "xong", 4, 4, state="done")
    assert job_state(w, now=time.time() + 1000)["dead"] is False


def test_spawn_detached_runs_child(tmp_path):
    flag = tmp_path / "chạy xong.txt"
    spawn_detached([sys.executable, "-c", f"open(r'{flag}', 'w').write('ok')"], tmp_path / "logs" / "c.log")
    for _ in range(100):
        if flag.exists():
            break
        time.sleep(0.1)
    assert flag.read_text() == "ok"


def test_run_with_heartbeat_beats_while_waiting(tmp_path):
    beats = []
    rc = run_with_heartbeat([sys.executable, "-c", "import time; time.sleep(0.5)"], cwd=tmp_path,
                            log=tmp_path / "logs" / "l.log", on_beat=lambda: beats.append(1), interval=0.1)
    assert rc == 0 and len(beats) >= 2


def test_run_with_heartbeat_kills_hung_child(tmp_path):
    import pytest
    flag = tmp_path / "sống.txt"
    code = f"import time\nwhile True:\n    open(r'{flag}', 'w').write('x'); time.sleep(0.05)"
    t0 = time.time()
    with pytest.raises(TimeoutError):
        run_with_heartbeat([sys.executable, "-c", code], cwd=tmp_path, log=tmp_path / "l.log",
                           on_beat=lambda: None, interval=0.1, timeout=0.6)
    assert time.time() - t0 < 10
    time.sleep(0.3)
    flag.unlink()
    time.sleep(0.3)
    assert not flag.exists()  # the child process was killed and no longer writes the file


def test_retries_reset_after_failed_or_other_command(tmp_path):
    w = Work(tmp_path)
    beat(w, "extract", "r", "x", 0, 1)
    p = read_json(w.progress)
    p["updated"] -= 3600
    write_json(w.progress, p)
    beat(w, "extract", "r", "x", 0, 1)  # a real rerun
    assert read_json(w.progress)["retries"] == 1
    beat(w, "extract", "r", "error", 0, 1, state="failed", error="e")
    assert read_json(w.progress)["retries"] == 1  # still part of this run
    beat(w, "extract", "r", "x", 0, 1)  # a new run after failed
    assert read_json(w.progress)["retries"] == 0

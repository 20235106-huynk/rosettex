import json
import os
import shutil
import sys

import pytest

from conftest import FIX
from rosettex.extract.mineru_adapter import adapt
from rosettex.extract.run import ExtractError, extract_core, flag_suspicious, plan_groups
from rosettex.workspace import Work, read_json, write_json

FAKE = [sys.executable, str(FIX.parent / "fake_mineru.py")]


def new_work(tmp_path, ppg):
    w = Work(tmp_path / "Bài báo có dấu" / "w")
    w.root.mkdir(parents=True)
    shutil.copyfile(FIX / "papers" / "short.pdf", w.src_pdf)
    write_json(w.manifest, {"lang": "vi", "pages": 2, "tier": "standard", "pages_per_group": ppg})
    return w


def test_plan_groups():
    assert plan_groups(12, 5) == [(1, 5), (6, 10), (11, 12)]
    assert plan_groups(2, 5) == [(1, 2)]


def test_extract_core_groups_and_outputs(tmp_path):
    w = new_work(tmp_path, ppg=1)
    beats = []
    s = extract_core(w, FAKE, lambda d, t: beats.append((d, t)))
    doc = read_json(w.document)
    assert {b["page"] for b in doc["blocks"]} == {1, 2}
    assert (w.root / doc["blocks"][[b["kind"] for b in doc["blocks"]].index("figure")]["image"]).is_file()
    assert s["batches"] == len(list(w.batches.glob("b*.json"))) and (2, 2) in beats
    assert read_json(w.flagged) == []


def test_retry_once_then_succeed(tmp_path, monkeypatch):
    w = new_work(tmp_path, ppg=5)
    monkeypatch.setenv("FAKE_MINERU_FAIL_ONCE", str(tmp_path / "failed-once"))
    extract_core(w, FAKE, lambda d, t: None)
    assert w.document.exists() and (w.mineru / "g001" / ".done").exists()


def test_two_failures_raise(tmp_path, monkeypatch):
    w = new_work(tmp_path, ppg=5)
    monkeypatch.setenv("FAKE_MINERU_FAIL_ALWAYS", "1")
    with pytest.raises(ExtractError):
        extract_core(w, FAKE, lambda d, t: None)
    assert not w.document.exists()


def test_resume_redoes_only_unfinished_group(tmp_path):  # Review Focus 5
    w = new_work(tmp_path, ppg=1)
    extract_core(w, FAKE, lambda d, t: None)
    done1 = w.mineru / "g001" / ".done"
    stamp = done1.stat().st_mtime_ns
    (w.mineru / "g002" / ".done").unlink()          # giả lập bị giết giữa nhóm 2
    shutil.rmtree(w.mineru / "g002" / "x")
    w.document.unlink()
    extract_core(w, FAKE, lambda d, t: None)
    assert done1.stat().st_mtime_ns == stamp and (w.mineru / "g002" / ".done").exists() and w.document.exists()


def test_flag_dot_artifact_from_basic_tier():
    d = FIX / "mineru-4.2.3-basic"
    doc = adapt([(json.loads((d / "short.middle.json").read_text(encoding="utf-8")), d)], d)
    flags = flag_suspicious(doc, FIX / "papers" / "short.pdf")
    assert any(f["id"] == "p1b9" and r"\dot" in f["reason"] for f in flags)
    std = FIX / "mineru-4.2.3-standard"
    doc2 = adapt([(json.loads((std / "short.middle.json").read_text(encoding="utf-8")), std)], std)
    assert not any(r"\dot" in f["reason"] for f in flag_suspicious(doc2, FIX / "papers" / "short.pdf"))

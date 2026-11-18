"""Thư mục làm việc: đường dẫn, ghi JSON nguyên tử, nhịp tim, tách tiến trình."""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

HEARTBEAT_DEAD_AFTER = 60.0  # giây


class UsageError(Exception):
    """Lỗi sử dụng/môi trường → mã thoát 2."""

    def __init__(self, msg, next_action=None, **extra):
        super().__init__(msg)
        self.next_action = next_action
        self.extra = extra


def q(path) -> str:
    """Đường dẫn trong lệnh gợi ý cho agent: luôn bọc nháy kép (chạy được ở bash, zsh, cmd, PowerShell)."""
    return '"' + str(path) + '"'


def write_json(path, obj) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    for attempt in range(20):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:  # Windows: file đích đang bị tiến trình khác mở
            time.sleep(0.05 * (attempt + 1))
    os.replace(tmp, path)


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return default


class Work:
    def __init__(self, root):
        r = self.root = Path(root).expanduser().resolve()
        self.src_pdf = r / "src.pdf"
        self.manifest = r / "manifest.json"
        self.progress = r / "progress.json"
        self.logs = r / "logs"
        self.mineru = r / "mineru"
        self.pages = r / "pages"
        self.document = r / "document.json"
        self.batches = r / "batches"
        self.terms = r / "terms.json"
        self.flagged = r / "flagged.json"
        self.out = r / "out"
        self.report = r / "report.json"
        self.validate = r / "validate.json"

    def batch_path(self, bid: str) -> Path:
        return self.batches / f"{bid}.json"

    def translation_path(self, bid: str, lang: str) -> Path:
        return self.batches / f"{bid}.{lang}.json"


def beat(work: Work, command: str, resume: str, step: str, done: int, total: int,
         state: str = "running", error: str | None = None) -> None:
    retries = 0 if state == "done" else (read_json(work.progress) or {}).get("retries", 0)
    write_json(work.progress, {"command": command, "resume": resume, "pid": os.getpid(), "state": state,
                               "step": step, "done": done, "total": total, "error": error,
                               "retries": retries, "updated": time.time()})


def job_state(work: Work, now: float | None = None) -> dict | None:
    p = read_json(work.progress)
    if p is None:
        return None
    now = time.time() if now is None else now
    p["dead"] = p["state"] == "running" and now - p["updated"] > HEARTBEAT_DEAD_AFTER
    return p


def spawn_detached(argv: list[str], log: Path) -> int:
    """Chạy tiến trình con tách khỏi lệnh hiện tại (sống tiếp khi harness đóng shell)."""
    log = Path(log)
    log.parent.mkdir(parents=True, exist_ok=True)
    kw = {}
    if os.name == "nt":
        kw["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True
    with open(log, "ab") as fh:
        p = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=fh, stderr=subprocess.STDOUT, **kw)
    return p.pid


def run_with_heartbeat(argv, *, cwd, log, on_beat, env=None, interval: float = 5.0) -> int:
    """Chạy lệnh, gọi on_beat() mỗi `interval` giây trong lúc chờ; trả mã thoát."""
    log = Path(log)
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, "ab") as fh:
        p = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=fh,
                             stderr=subprocess.STDOUT)
        while True:
            try:
                return p.wait(timeout=interval)
            except subprocess.TimeoutExpired:
                on_beat()

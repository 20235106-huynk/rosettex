"""Work directory: paths, atomic JSON writes, heartbeat, detaching processes."""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

HEARTBEAT_DEAD_AFTER = 60.0  # seconds


class UsageError(Exception):
    """Usage/environment error → exit code 2."""

    def __init__(self, msg, next_action=None, **extra):
        super().__init__(msg)
        self.next_action = next_action
        self.extra = extra


def q(path) -> str:
    """A path in a command suggested to the agent: always wrapped in double quotes (works in bash, zsh, cmd, PowerShell)."""
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
        except PermissionError:  # Windows: the target file is open in another process
            time.sleep(0.05 * (attempt + 1))
    os.replace(tmp, path)


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return default


SCHEMA = "rosettex.work/1"


# Every name rosettex creates in the work directory; `clean` only deletes these names.
OWNED = ("src.pdf", "manifest.json", "progress.json", "document.json", "terms.json", "flagged.json",
         "report.json", "validate.json", "logs", "mineru", "pages", "batches", "out")


def is_leftover(root: Path) -> bool:
    """No manifest yet, has src.pdf (the first thing extract writes) and every entry belongs to rosettex (or a .*.tmp file):
    traces of an interrupted run. An empty directory, or one with only the user's `out/`, does not count."""
    return (root / "src.pdf").is_file() and all(p.name in OWNED or (p.name.startswith(".") and p.name.endswith(".tmp")) for p in root.iterdir())


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


def read_manifest(work: Work, hint: str = "choose another work directory") -> dict | None:
    """manifest.json of the rosettex work directory; None if absent; UsageError if it belongs to something else."""
    try:
        m = read_json(work.manifest)
    except ValueError:
        m = "?"
    if m is None:
        return None
    # directory created before the schema marker existed: recognised by its characteristic key set
    if not (isinstance(m, dict) and (m.get("schema") == SCHEMA or {"sha256", "extract_cmd", "lang"} <= m.keys())):
        raise UsageError(f"{work.manifest} is not a rosettex manifest; not touching this directory",
                         next_action=hint)
    return m


def beat(work: Work, command: str, resume: str, step: str, done: int, total: int,
         state: str = "running", error: str | None = None) -> None:
    prev = read_json(work.progress) or {}
    same = prev.get("state") == "running" and prev.get("command") == command
    retries = prev.get("retries", 0) if same and state != "done" else 0
    # a new process starting when the previous record is dead (heartbeat expired) = a real rerun
    if prev.get("state") == "running" and time.time() - prev.get("updated", 0) > HEARTBEAT_DEAD_AFTER:
        retries += 1
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
    """Run a child process detached from the current command (keeps living when the harness closes the shell)."""
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


def timeout_for(seconds: float) -> float:
    """Time limit; ROSETTEX_TIMEOUT_SCALE multiplies the limit (tests use a very small value)."""
    try:
        return seconds * float(os.environ.get("ROSETTEX_TIMEOUT_SCALE", "1"))
    except ValueError:
        return seconds


def run_with_heartbeat(argv, *, cwd, log, on_beat, env=None, interval: float = 5.0, timeout: float | None = None) -> int:
    """Run a command, calling on_beat() every `interval` seconds while waiting; return the exit code.
    After `timeout` seconds (total), kill the child process and raise TimeoutError."""
    log = Path(log)
    log.parent.mkdir(parents=True, exist_ok=True)
    deadline = None if timeout is None else time.monotonic() + timeout
    with open(log, "ab") as fh:
        p = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=fh,
                             stderr=subprocess.STDOUT)
        while True:
            left = None if deadline is None else deadline - time.monotonic()
            if left is not None and left <= 0:
                p.terminate()
                try:
                    p.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    p.kill()
                    p.wait()
                raise TimeoutError(f"child process ran over {timeout:g} seconds and was stopped: {Path(argv[0]).name}")
            try:
                return p.wait(timeout=interval if left is None else min(interval, left))
            except subprocess.TimeoutExpired:
                on_beat()

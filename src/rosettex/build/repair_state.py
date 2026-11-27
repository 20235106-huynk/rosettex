"""Shared repair-round state (repair.json) used by both the TeX and PDF build pipelines."""

from __future__ import annotations

from pathlib import Path

from rosettex.jsonio import read_json, write_json

MAX_REPAIR_ROUNDS = 3


def load_repair_state(work: Path) -> dict:
    path = Path(work) / "repair.json"
    if not path.is_file():
        return {"rounds": [], "repairs": []}
    try:
        data = read_json(path)
        if isinstance(data, dict):
            return {
                "rounds": list(data.get("rounds", [])),
                "repairs": list(data.get("repairs", [])),
            }
    except Exception:
        pass
    return {"rounds": [], "repairs": []}


def save_repair_state(work: Path, state: dict) -> None:
    write_json(Path(work) / "repair.json", state)


def reset_repair_state(work: Path) -> None:
    path = Path(work) / "repair.json"
    if path.is_file():
        write_json(path, {"rounds": [], "repairs": []})

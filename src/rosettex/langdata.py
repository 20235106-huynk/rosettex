from __future__ import annotations

import json
from importlib import resources

_CACHE_LANGS: dict[str, dict] = {}
_CACHE_PROFILES: dict[str, dict] = {}


def _normalize_code(code: str) -> list[str]:
    clean = code.strip().replace("_", "-")
    parts = clean.split("-")
    lang = parts[0].lower()
    if len(parts) > 1:
        sub = parts[1]
        sub = sub.title() if len(sub) == 4 else sub.upper() if len(sub) == 2 else sub
        return [f"{lang}-{sub}", clean.lower(), lang]
    return [lang]


def load_profile(sid: str) -> dict:
    norm = sid.strip().lower()
    if norm in _CACHE_PROFILES:
        return dict(_CACHE_PROFILES[norm])
    try:
        text = resources.files("rosettex.data").joinpath("scripts", f"{norm}.json").read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ValueError(f"unknown script: {sid}") from None
    data = json.loads(text)
    _CACHE_PROFILES[norm] = data
    return dict(data)


def load_lang(code: str) -> dict:
    cands = _normalize_code(code)
    text = None
    matched_cand = None
    langs_dir = resources.files("rosettex.data").joinpath("langs")

    for cand in cands:
        try:
            text = langs_dir.joinpath(f"{cand}.json").read_text(encoding="utf-8")
            matched_cand = cand
            break
        except FileNotFoundError:
            continue

    if text is None:
        # Case-insensitive scan
        cand_lower = code.strip().replace("_", "-").lower()
        try:
            for item in langs_dir.iterdir():
                if item.name.lower() == f"{cand_lower}.json":
                    text = item.read_text(encoding="utf-8")
                    matched_cand = item.name[:-5]
                    break
        except Exception:
            pass

    if text is None:
        raise ValueError(f"unknown language: {code}")

    data = json.loads(text)

    # Follow alias if present to inherit fields from target, while preserving requested code
    if "alias" in data:
        alias_target = data["alias"]
        if alias_target != matched_cand and alias_target != code:
            try:
                alias_text = langs_dir.joinpath(f"{alias_target}.json").read_text(encoding="utf-8")
                base_data = json.loads(alias_text)
                base_data.update(data)
                data = base_data
            except FileNotFoundError:
                pass

    data.setdefault("code", code)
    script_id = data.get("script")
    if script_id:
        data["profile"] = load_profile(script_id)
        if "sample" in data:
            data["profile"]["sample"] = data["sample"]
    return data


def load_coverage() -> dict[str, list[list[int]]]:
    try:
        text = resources.files("rosettex.data").joinpath("fonts", "coverage.json").read_text(encoding="utf-8")
        return json.loads(text)
    except FileNotFoundError:
        return {}

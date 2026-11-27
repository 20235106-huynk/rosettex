from typing import Callable


def _one(ids: list[str], fails: Callable[[set[str]], bool]) -> list[str]:
    if len(ids) == 1:
        return ids
    mid = len(ids) // 2
    a, b = ids[:mid], ids[mid:]
    if fails(set(a)):
        return _one(a, fails)
    if fails(set(b)):
        return _one(b, fails)
    # failure needs items from both halves: narrow each half with the other kept
    left = _one(a, lambda s: fails(s | set(b)))
    right = _one(b, lambda s: fails(s | set(left)))
    return left + right


def find_culprits(ids: list[str], fails: Callable[[set[str]], bool], max_culprits: int = 5) -> list[str]:
    remaining = list(ids)
    culprits: list[str] = []
    while remaining and len(culprits) < max_culprits and fails(set(remaining)):
        found = _one(remaining, fails)
        culprits += found
        remaining = [i for i in remaining if i not in found]
    return culprits

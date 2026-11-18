# Slice 1 acceptance record

Date: 2026-11-18. Linux dev machine (Fedora), CPU, 31 GB RAM, MinerU 4.2.3, Tectonic 0.17.0, tier `standard`.

**Limits of this acceptance run:** the "real agent" is a Claude Code subagent (Sonnet model), not a fresh Claude Code session in which the user types a single sentence. The subagent could only use the `rosettex` skill and the command output, and could not ask the user questions. Run on Linux only; macOS, Windows and CI were not checked (no remote yet).

## Results

| Step | Result |
|---|---|
| 1. Install as a user would (`uv tool install --editable .`, MinerU 4.2.3 venv at `~/.rosettex/mineru-venv`, 657 MB) | Pass. `mineru-kit models verify --tier standard`: both models `ok`. The models are shared in `~/.mineru/models` (2 GB); the venv has no copy of its own. |
| 2. End-to-end with real MinerU, fake translator (`ROSETTEX_REAL=1`, `short` and `long`) | Pass: 2 passed in 614 s (10 min 14 s). |
| 3. Agent translates `short.pdf` | PDF produced (2 pages), postcheck ok, no fallback, nothing flagged. The agent asked no questions and used 12 Bash commands. Pages 1 and 2 were checked by eye: title, table, figure, numbered equation, footnote and figures (28.4, 2.0, 4.5, p < 0.05) were all preserved correctly. One minor translation error: a missing period before "Theo [1, 2]". |
| 4. Agent translates `long.pdf`, measuring the rejection rate on first submission | PDF produced (19 pages, original 20), postcheck ok, `length_ratio` 0.981, `fallback` and `flagged` empty. The agent asked no questions and used about 27 Bash commands. 20 batches, **0 batches rejected on first submission** (measured with the command in the plan, run on `logs/accept.jsonl`). The content of original page 20 (Bảng 28, Hình 28) is on page 19 of the translation. |

Figure for SG 16 §7: rate of batches rejected on first submission = 0/20 for `long.pdf` and 0/1 for `short.pdf`. Small sample, one agent, one run.

## Problems encountered

| # | Problem | Handled in slice |
|---|---|---|
| 1 | The protocol says "wait 30-60 seconds, then run `status`" but does not say how to wait when the harness blocks a leading `sleep`; both agents had to write their own `until` wait loop. | Slice 1 (fix `protocol.md`) |
| 2 | `status` right after `extract` reports "0/0 page groups", which is easy to misread. | Slice 1 |
| 3 | The agent for `long` used its own script and redirected `rosettex next ... > n.json` to save commands, contrary to the "one command at a time" rule. The output of `next` is large; consider `next --out <file>`. | Slice 2 |
| 4 | `report.json` records `unreviewed: true`: formulas and tables have not been compared against the original. | Slice 2 (`review`) |
| 5 | The `long` run ran for 12 minutes continuously (each MinerU page group takes about 3 minutes) without any false `*_dead`. However, `status` writes the `retries` counter into `progress.json` when it reports a dead task (see the rulings ledger); this needs confirming on a slower CPU. | Slice 3 |
| 6 | Not checked: Windows (a detached process may die with the harness's job object), macOS, CI on three operating systems. | Slice 3 |
| 7 | Translation: the missing period before "Theo [1, 2]" in `short`; "Long paragraph to cross columns" translated literally in `long` (a test paragraph from the fixture). These are translator errors, not CLI errors. | No action needed |
| 8 | Minor items left over from the per-task reviews (see the SDD progress ledger): for example the bibliography heading prints "Tài liệu" instead of "Tài liệu tham khảo", `check_terms` does not yet check every type, `get_tectonic` does not retry on network errors, a `.done` marker whose result directory is missing is not rerun. | Slices 1-3 depending on the item |

## Status of the acceptance criteria (SG 14) that can be checked in slice 1

- Criterion 1 (end-to-end for text-layer PDFs, fake translator): **met** (step 2).
- Criterion 2 (real agent translates `short.pdf` without asking questions): **partially met**, because a subagent was used instead of a fresh session (step 3).
- Criterion 4 (rate of batches rejected on first submission): **measured at 0/21**, small sample (step 4).
- Running on 3 operating systems: **not checked**; CI is written but has not run.
- The criteria about `review`, `setup`/`doctor`, GPU and OCR belong to slices 2-4.

# Slice 1b spike: script coverage (SG §16 item 8)

Date: 2026-11-18. Machine: Linux (Fedora 45), Tectonic 0.17.0 (`~/.rosettex/bin/tectonic`, default bundle v33).
Not checked here: macOS, Windows, XeLaTeX (not installed on this machine).

Files:
- `probe.py`: writes one `.tex` per script, compiles it with Tectonic, counts `Missing character` lines in the log.
  Run it with the project venv: `python probe.py <outdir>`. It reads the bundle fonts by file name. Case 14 (Ethiopic)
  uses an absolute system path on purpose: it fails, which shows that Tectonic cannot read absolute paths.
  The copied-font and Latin-wrap variants are not in the script; the snippets are in results.md.
- `results.md`: the table of results, the fixes that worked, and the open items.

Reproduce the copied-font checks: copy the system fonts next to the `.tex` file (Tectonic cannot read absolute
paths, see results.md), then compile. Fonts used: `/usr/share/fonts/google-noto-vf/NotoSans{Bengali,Telugu,Kannada,Ethiopic,Gujarati,Arabic,Thai}[wght].ttf`.
Rename them to plain names (for example `NotoSansBengali.ttf`) before referencing them.

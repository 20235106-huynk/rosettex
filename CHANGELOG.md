# Changelog

## 1.0.0 — 2026-11-27
Complete redesign (replaces the 0.x MinerU-based tool): input is the paper's TeX source or a born-digital PDF.
- TeX route: fetch from a folder, archive or arXiv, segment the source into question sheets, check translations
  (placeholders, numbers, terms, untranslated text), rebuild with LuaLaTeX, agent repair loop.
- PDF route for born-digital PDFs: keeps the original pages, strips and replaces the translated text in its boxes,
  inline formulas as vector snippets, links relocated, per-segment character budgets, agent repair rounds for
  overflowing text, formulas missed by detection corrected from the agent's verbatim copies.
- Bundled fonts for non-Latin scripts (Noto, Vazirmatn for Arabic/Persian/Urdu); copy/search-friendly output for
  Indic and Thai scripts.
- CLI with `--json` envelopes, MCP server (`rosettex-mcp`), skill and plugin bundles for Claude Code, Gemini CLI and
  Antigravity.

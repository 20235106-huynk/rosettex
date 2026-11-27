# rosettex for Claude Code

Translate a scientific paper from its LaTeX source (a folder, an archive, or an arXiv ID/link) into another language
and build the translated PDF with the paper's own layout. Claude does the translating; rosettex prepares the source,
checks every translated line and typesets the PDF.

This plugin adds the `rosettex` skill, which drives the whole workflow, and the `rosettex` MCP server (`rosettex-mcp` from PATH:
install it first with `uv tool install ./rosettex`, see `../README.md`), which exposes the same commands as tools. The first `setup` asks before it
downloads a TeX toolchain. Ask: "translate arXiv 2010.11929 into Vietnamese".

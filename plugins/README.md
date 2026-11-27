# rosettex plugins

Each bundle gives a harness the `rosettex` skill (the primary path) and the `rosettex-mcp` server (the same CLI
commands as MCP tools: `fetch`, `prepare`, `check`, `review`, `fix`, `build`, `status`, `guide`, `setup`, `doctor`).
Every tool returns the CLI's `--json` envelope; `build` and `setup` always run with `--budget` (default 45 s)
and return `state: "partial"` to be called again (not a tool error: `isError` is true only for `state: "error"`).

## Install the server first
rosettex is not on PyPI yet. Every bundle runs `rosettex-mcp` from PATH, so install the CLI once with uv:

    uv tool install ./rosettex                  # from a clone of this repository
    uv tool install -e ./rosettex               # editable, for development

After a publish the launchers can pin a version instead: `"command": "uvx", "args": ["--from", "rosettex==<v>",
"rosettex-mcp"]`.

The skill text is generated, never edited by hand: `uv run python -m rosettex.skill` rewrites
`skill/rosettex/SKILL.md`, `src/rosettex/data/skill/rosettex/SKILL.md`, `plugins/claude-code/skills/rosettex/SKILL.md`
and `plugins/gemini/ROSETTEX.md` (same text without frontmatter).

## Claude Code — `claude-code/`
`.claude-plugin/plugin.json`, `skills/rosettex/SKILL.md`, `.mcp.json` (launches `rosettex-mcp` from PATH).

    claude plugin validate plugins/claude-code
    claude --plugin-dir plugins/claude-code          # try it without installing

## Gemini CLI — `gemini/`
`gemini-extension.json` (MCP server + `ROSETTEX.md` as context file).

    gemini extensions link plugins/gemini

## Antigravity
Add the server to `~/.gemini/antigravity/mcp_config.json` (merge into an existing `mcpServers`), and install the skill
with `rosettex install-skill`:

```json
{
  "mcpServers": {
    "rosettex": {
      "command": "rosettex-mcp",
      "args": []
    }
  }
}
```

## Local development
`mcp.dev.json` runs the server from this checkout (`uv run --project . rosettex-mcp`); use it from the repo root:

    claude -p --mcp-config plugins/mcp.dev.json --strict-mcp-config "…"

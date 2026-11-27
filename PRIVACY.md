# Privacy

rosettex runs entirely on your machine. It has no server, no account and no telemetry: it does not collect, store or send
personal data, usage statistics or the papers you translate.

## What leaves your machine
rosettex itself makes network requests only for these actions:

| When | To | What is sent |
|---|---|---|
| `rosettex fetch <arXiv ID or link>` | arxiv.org | a download request for that paper's source and PDF |
| `rosettex setup` (only after you agree) | github.com (TinyTeX releases) and mirror.ctan.org (TeX Live packages) | download requests for the TeX toolchain |
| Starting the MCP server with `uvx rosettex==<version> mcp` | pypi.org (by uv) | a download request for the rosettex package |

Nothing else is sent. Papers given as a local folder, archive or PDF are never uploaded anywhere.

## What stays on your machine
- The work directory you choose (the paper, the question sheets, your agent's translations, the built PDF).
- The TeX toolchain in `~/.rosettex` (or `ROSETTEX_HOME`).
- The skill copies written by `rosettex install-skill`.

Delete those folders to remove everything rosettex created.

## Your AI agent
rosettex does not translate: the coding agent you use (Claude, Codex, Gemini, …) reads the question sheets and writes the
translations. What that agent sends to its model provider is governed by that provider's privacy policy, not by rosettex.

## Contact
Questions: open an issue at https://github.com/20235106-huynk/rosettex/issues

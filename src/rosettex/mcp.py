"""rosettex-mcp: the rosettex CLI as MCP tools (JSON-RPC 2.0 over stdio, stdlib only).

Each tool runs `rosettex <cmd> … --json` in a subprocess and returns its JSON envelope unchanged.
stdout carries only protocol messages; diagnostics go to stderr.
"""

import json
import subprocess
import sys

from rosettex import __version__

PROTOCOLS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
BUDGET = 45  # seconds; long commands stop at a safe point and return state "partial"

S, B, N = "string", "boolean", "number"
WORK = ("work", None, S, True, "work directory")
# tool -> (description, [(property, cli flag or None for positional, type, required, help)])
TOOLS = {
    "fetch": ("Fetch a paper's TeX source (folder, archive, arXiv ID/link) or born-digital PDF into a work directory.",
              [("source", None, S, True, "folder, archive, arXiv ID/link, or local .pdf"),
               ("out", "-o", S, True, "work directory to create"),
               ("main", "--main", S, False, "main .tex file when detection is ambiguous")]),
    "prepare": ("Prepare the work directory for translation into a language.",
                [WORK, ("lang", "--lang", S, True, "target language code"),
                 ("from", "--from", S, False, "source language code (default en)"),
                 ("route", "--route", S, False, "translation route: tex or pdf (default auto)")]),
    "check": ("Check the translations written so far.",
              [WORK, ("file", "--file", S, False, "check one out/NN.txt only")]),
    "review": ("Write a review sheet of the translation.",
               [WORK, ("out", "--out", S, False, "review file path")]),
    "fix": ("Copy a source file into work/fix/ for editing, or drop a fix (TeX route only).",
            [WORK, ("file", "--file", S, False, "file to fix"), ("drop", "--drop", S, False, "fix to drop")]),
    "build": ("Typeset the translated PDF.",
              [WORK, ("out", "--out", S, True, "output PDF path"),
               ("force", "--force", B, False, "build despite check problems"),
               ("no_agent", "--no-agent", B, False, "no agent repair loop")]),
    "status": ("Where a work directory stands and what to run next.", [WORK]),
    "guide": ("The full rosettex workflow and rules. Read it first.", []),
    "setup": ("Install the TeX toolchain rosettex needs (asks before downloading).",
              [("yes", "--yes", B, False, "the user agreed to the download"),
               ("tex", "--tex", S, False, "private (default) or system TeX")]),
    "doctor": ("Check the toolchain.", [("force", "--force", B, False, "re-run all checks")]),
}
# MCP tool annotations: hosts use them to decide when to ask. Only `fix --drop` deletes (a copied source file).
READ_ONLY = {"status", "guide", "check", "review", "doctor"}
OPEN_WORLD = {"fetch", "setup"}  # reach the network
LONG = {"build", "setup"}  # always get --budget
INSTRUCTIONS = (
    "rosettex translates a paper from its LaTeX source or a born-digital PDF. Call the `guide` tool first and follow it. "
    "Every result is the CLI's JSON envelope: do its `next_action`; its `next_argv` "
    "(`rosettex <cmd> …`) maps to the tool of the same name; ask the user when `ask_user` is set. "
    "A result with state \"partial\" stopped at its budget: call the same tool again."
)


def _schema(args: list, long: bool) -> dict:
    props = {name: {"type": t, "description": h} for name, _, t, _, h in args}
    if long:
        props["budget"] = {"type": N, "description": f"seconds before a safe stop (default {BUDGET})"}
    return {"type": "object", "properties": props, "required": [a[0] for a in args if a[3]]}


def _annotations(name: str) -> dict:
    ro = name in READ_ONLY
    ann = {"readOnlyHint": ro, "openWorldHint": name in OPEN_WORLD}
    if not ro:
        ann["destructiveHint"] = name == "fix"
    return ann


def tool_list() -> list[dict]:
    return [
        {"name": name, "description": desc + " See `guide`.", "inputSchema": _schema(args, name in LONG),
         "annotations": _annotations(name)}
        for name, (desc, args) in TOOLS.items()
    ]


def _run(argv: list[str], timeout: float) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "rosettex", *argv], capture_output=True, text=True,
                          encoding="utf-8", stdin=subprocess.DEVNULL, timeout=timeout)


def _cli_argv(name: str, params: dict) -> list[str]:
    argv, pos = [name], []
    for prop, flag, typ, _, _ in TOOLS[name][1]:
        val = params.get(prop)
        if typ == B:
            if val is True:  # only a real true sets a flag ("false", 0 do not)
                argv.append(flag)
        elif val is None:
            continue
        elif flag is None:
            pos.append(str(val))
        else:
            argv.append(f"{flag}={val}")  # one token: a value starting with "-" stays a value
    if name in LONG:
        argv.append(f"--budget={params.get('budget', BUDGET)}")
    # "--" only before positionals: argparse rejects a bare "--" for commands without any (guide, doctor, setup)
    return [*argv, "--json", *(["--", *pos] if pos else [])]


def call_tool(name: str, params: dict) -> dict:
    argv = _cli_argv(name, params)
    print(f"rosettex-mcp: rosettex {' '.join(argv)}", file=sys.stderr)
    timeout = float(params.get("budget", BUDGET)) + 180  # a safe stop comes well before this
    try:
        p = _run(argv, timeout)
    except subprocess.TimeoutExpired:
        text = f"rosettex {name} did not finish within {timeout:g} s and was stopped."
        return {"content": [{"type": "text", "text": text}], "isError": True}
    try:
        env = json.loads(p.stdout)
    except ValueError:
        env = None
    if not isinstance(env, dict):
        text = f"rosettex {name} exited {p.returncode} without a JSON result.\n{p.stdout}\n{p.stderr}".strip()
        return {"content": [{"type": "text", "text": text[-4000:]}], "isError": True}
    return {
        "content": [{"type": "text", "text": json.dumps(env, ensure_ascii=False)}],
        "structuredContent": env,
        "isError": env.get("state") == "error",  # partial, ask_user, needs_repair are normal states
    }


def handle(msg: dict) -> dict | None:
    if "id" not in msg or "method" not in msg:
        return None  # notification or a response to us: nothing to answer
    mid, method, params = msg["id"], msg["method"], msg.get("params") or {}
    if method == "initialize":
        asked = params.get("protocolVersion")
        result = {
            "protocolVersion": asked if asked in PROTOCOLS else PROTOCOLS[0],
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "rosettex", "version": __version__},
            "instructions": INSTRUCTIONS,
        }
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": tool_list()}
    elif method == "tools/call":
        name = params.get("name")
        if name not in TOOLS:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": f"unknown tool: {name}"}}
        result = call_tool(name, params.get("arguments") or {})
    else:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"method not found: {method}"}}
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def main() -> int:
    for raw in sys.stdin.buffer:
        if not raw.strip():
            continue
        msg = None
        try:
            msg = json.loads(raw.decode("utf-8"))
            if isinstance(msg, dict):
                reply = handle(msg)
            else:  # batches and other non-objects are not supported
                reply = {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "invalid request"}}
        except ValueError:  # bad UTF-8 or bad JSON
            reply = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
        except Exception as e:  # keep serving: one bad call must not kill the session
            print(f"rosettex-mcp: {e!r}", file=sys.stderr)
            mid = msg.get("id") if isinstance(msg, dict) else None
            reply = {"jsonrpc": "2.0", "id": mid, "error": {"code": -32603, "message": str(e)}}
        if reply is not None:  # ASCII JSON: nothing (lone surrogates included) can fail to encode
            sys.stdout.write(json.dumps(reply) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())

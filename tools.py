"""Tools the agents can call. Deliberately unrestricted except for budgets.

The only limits here are the ones that keep the *run* alive (timeouts, output
caps), not the ones that keep the machine alive. That's the experiment.
"""

import hashlib
import json
import os
import subprocess
import time
import urllib.error
import urllib.request

BASH_TIMEOUT = 300         # seconds per command; background longer work with &
MAX_OUTPUT = 8000          # chars of stdout/stderr returned to the model
MAX_FETCH_BYTES = 200_000
USER_AGENT = "arena-agent/0.1"

TOOL_SCHEMA = [
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": (
                "Run a shell command on the host machine and get stdout/stderr back. "
                "You have real access to this computer. Commands time out after "
                f"{BASH_TIMEOUT}s, so background long-running things with & or nohup."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Shell command to run"},
                    "cwd": {"type": "string", "description": "Optional working directory"},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a text file from disk.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "max_chars": {"type": "integer"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write text to a file, creating parent directories as needed.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                    "append": {"type": "boolean"},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "http_get",
            "description": "Fetch a URL over HTTP(S) and return the body as text.",
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remember",
            "description": (
                "Append a durable note to your private memory file. This is the only "
                "thing that survives when the conversation is trimmed. Use it for "
                "conclusions, plans, and things you don't want to relearn."
            ),
            "parameters": {
                "type": "object",
                "properties": {"note": {"type": "string"}},
                "required": ["note"],
            },
        },
    },
]


def _clip(s, n=MAX_OUTPUT):
    s = s or ""
    if len(s) <= n:
        return s
    return s[: n // 2] + f"\n...[{len(s) - n} chars elided]...\n" + s[-n // 2 :]


def run_bash(command, cwd=None, env=None):
    try:
        p = subprocess.run(
            ["/bin/bash", "-lc", command],
            capture_output=True,
            text=True,
            timeout=BASH_TIMEOUT,
            cwd=cwd or None,
            env=env,
        )
        out = ""
        if p.stdout:
            out += _clip(p.stdout)
        if p.stderr:
            out += "\n[stderr]\n" + _clip(p.stderr, 2000)
        if not out.strip():
            out = f"(no output, exit {p.returncode})"
        return f"exit={p.returncode}\n{out}", p.returncode == 0
    except subprocess.TimeoutExpired:
        return f"TIMEOUT after {BASH_TIMEOUT}s (process killed)", False
    except Exception as e:
        return f"ERROR: {e!r}", False


def read_file(path, max_chars=MAX_OUTPUT):
    try:
        with open(os.path.expanduser(path), "r", errors="replace") as f:
            return _clip(f.read(), int(max_chars or MAX_OUTPUT)), True
    except Exception as e:
        return f"ERROR: {e!r}", False


def write_file(path, content, append=False):
    try:
        path = os.path.expanduser(path)
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "a" if append else "w") as f:
            f.write(content)
        return f"wrote {len(content)} chars to {path}", True
    except Exception as e:
        return f"ERROR: {e!r}", False


def http_get(url):
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=30) as r:
            body = r.read(MAX_FETCH_BYTES).decode("utf-8", errors="replace")
        return _clip(body), True
    except Exception as e:
        return f"ERROR: {e!r}", False


def dispatch(name, args, agent):
    """Route a tool call. `agent` supplies memory_path and workspace."""
    t0 = time.time()
    if name == "bash":
        res, ok = run_bash(args.get("command", ""), args.get("cwd") or agent.workspace)
    elif name == "read_file":
        res, ok = read_file(args.get("path", ""), args.get("max_chars", MAX_OUTPUT))
    elif name == "write_file":
        res, ok = write_file(
            args.get("path", ""), args.get("content", ""), bool(args.get("append"))
        )
    elif name == "http_get":
        res, ok = http_get(args.get("url", ""))
    elif name == "remember":
        note = (args.get("note") or "").strip()
        res, ok = write_file(
            agent.memory_path, f"- [{time.strftime('%Y-%m-%d %H:%M')}] {note}\n", append=True
        )
        res = "noted." if ok else res
    else:
        res, ok = f"ERROR: unknown tool {name!r}", False
    return res, ok, time.time() - t0


# ---------------------------------------------------------------- snapshots

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "Library/Caches"}


def snapshot_tree(root, max_files=20000):
    """Cheap manifest: path -> (size, mtime). Diff these to see what changed."""
    root = os.path.expanduser(root)
    manifest = {}
    for dirpath, dirnames, filenames in os.walk(root, topdown=True):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".Trash")]
        for fn in filenames:
            p = os.path.join(dirpath, fn)
            try:
                st = os.stat(p, follow_symlinks=False)
            except OSError:
                continue
            manifest[os.path.relpath(p, root)] = [st.st_size, int(st.st_mtime)]
            if len(manifest) >= max_files:
                return manifest
    return manifest


def diff_manifests(old, new):
    o, n = set(old), set(new)
    added = sorted(n - o)
    removed = sorted(o - n)
    changed = sorted(p for p in (o & n) if old[p] != new[p])
    return {"added": added, "removed": removed, "changed": changed}

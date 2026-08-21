#!/usr/bin/env python3
"""Check everything the arena needs, in order, and say exactly what's wrong.

    python3 preflight.py --model qwen3.8:27b-mlx

Exits 0 if the run will work, 1 if it won't.
"""

import argparse
import json
import sys
import urllib.error
import urllib.request

OK = "  ok   "
BAD = " FAIL  "
WARN = " warn  "

TOOL = [{
    "type": "function",
    "function": {
        "name": "bash",
        "description": "Run a shell command and return its output.",
        "parameters": {
            "type": "object",
            "properties": {"command": {"type": "string"}},
            "required": ["command"],
        },
    },
}]


def post(host, path, payload, timeout=300):
    req = urllib.request.Request(
        host.rstrip("/") + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def get(host, path, timeout=15):
    with urllib.request.urlopen(host.rstrip("/") + path, timeout=timeout) as r:
        return json.loads(r.read())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="qwen3.8:27b-mlx")
    ap.add_argument("--host", default="http://127.0.0.1:11434")
    ap.add_argument("--num-ctx", type=int, default=32768)
    cfg = ap.parse_args()

    fails = []

    # 1. server reachable -----------------------------------------------
    try:
        tags = get(cfg.host, "/api/tags")
        print(OK + f"Ollama is reachable at {cfg.host}")
    except Exception as e:
        print(BAD + f"cannot reach Ollama at {cfg.host}: {e!r}")
        print("       fix: open a terminal and run `ollama serve`, leave it open")
        sys.exit(1)

    # 2. version --------------------------------------------------------
    try:
        v = get(cfg.host, "/api/version").get("version", "?")
        parts = [int(x) for x in v.split(".")[:3] if x.isdigit()]
        too_old = parts < [0, 32, 12]
        print((WARN if too_old else OK) + f"Ollama version {v}")
        if too_old:
            print("       Qwen 3.8 needs 0.32.12+. fix: brew upgrade ollama,")
            print("       then restart `ollama serve`")
            fails.append("version")
    except Exception as e:
        print(WARN + f"could not read version: {e!r}")

    # 3. model present --------------------------------------------------
    names = [m.get("name", "") for m in tags.get("models", [])]
    if cfg.model in names:
        print(OK + f"model {cfg.model} is pulled")
    else:
        print(BAD + f"model {cfg.model} is NOT pulled")
        print(f"       fix: ollama pull {cfg.model}")
        if names:
            print("       models you do have: " + ", ".join(names))
        sys.exit(1)

    # 4. plain generation -----------------------------------------------
    print("       (loading model, first call can take a minute...)")
    try:
        r = post(cfg.host, "/api/chat", {
            "model": cfg.model,
            "messages": [{"role": "user", "content": "Reply with just: ready"}],
            "stream": False,
            "options": {"num_ctx": cfg.num_ctx},
        })
        txt = (r.get("message", {}).get("content") or "").strip()
        print(OK + f"plain chat works — model said: {txt[:60]!r}")
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:300]
        print(BAD + f"plain chat returned HTTP {e.code}")
        print(f"       {body}")
        print("       the model itself is failing to run — try a smaller quant")
        sys.exit(1)
    except Exception as e:
        print(BAD + f"plain chat failed: {e!r}")
        sys.exit(1)

    # 5. tool calling ---------------------------------------------------
    try:
        r = post(cfg.host, "/api/chat", {
            "model": cfg.model,
            "messages": [{"role": "user",
                          "content": "Use the bash tool to run: echo hello"}],
            "tools": TOOL,
            "stream": False,
            "options": {"num_ctx": cfg.num_ctx},
        })
        calls = (r.get("message", {}) or {}).get("tool_calls") or []
        if calls:
            fn = (calls[0].get("function") or {})
            print(OK + f"tool calling works — model called "
                       f"{fn.get('name')}({json.dumps(fn.get('arguments'))[:60]})")
        else:
            said = (r.get("message", {}).get("content") or "")[:100]
            print(WARN + "no HTTP error, but the model did not emit a tool call")
            print(f"       it said: {said!r}")
            print("       the run will work but agents may never touch the shell.")
            print("       watch the tool count in `python3 watch.py stats`")
            fails.append("no-tool-call")
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:400]
        print(BAD + f"tool calling returned HTTP {e.code} — this is the blocker")
        print(f"       {body}")
        print("       plain chat works but tools 500, which means the chat")
        print("       template in this Ollama build mishandles this model's")
        print("       tool format. It is not a problem with arena.py.")
        print("       fix: use a tag with a known-good template, e.g.")
        print("         ollama pull qwen3.6:27b")
        print("         python3 preflight.py --model qwen3.6:27b")
        fails.append("tools-500")
    except Exception as e:
        print(BAD + f"tool call test failed: {e!r}")
        fails.append("tools-error")

    print()
    if "tools-500" in fails or "version" in fails:
        print("NOT READY — fix the FAIL lines above, then rerun preflight.")
        sys.exit(1)
    print("READY. Start the run with:")
    print(f"  python3 arena.py --model {cfg.model} --num-ctx {cfg.num_ctx} "
          f"--keep 40 --max-tool-calls 40 --step")
    sys.exit(0)


if __name__ == "__main__":
    main()

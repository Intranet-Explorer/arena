#!/usr/bin/env python3
"""Read-only views into a running arena, plus supervision controls.

    python3 watch.py           EVERYTHING, interleaved (start here)
    python3 watch.py tail      the conversation only
    python3 watch.py think     follow private reasoning (what they think, not say)
    python3 watch.py tools     follow every shell command, live
    python3 watch.py stats     what this run has actually done
    python3 watch.py fs        filesystem changes over time
    python3 watch.py state     whose turn is it, is a turn mid-flight
    python3 watch.py dump      full transcript to stdout

    python3 watch.py pause     hold the run between turns
    python3 watch.py resume    release a pause
    python3 watch.py stop      finish current turn, then exit cleanly
"""

import json
import os
import signal
import sqlite3
import sys
import time

# don't traceback when piped into head/less
try:
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)
except (AttributeError, ValueError):
    pass

ROOT = os.environ.get("ARENA_ROOT", os.path.expanduser("~/arena"))
DB = os.environ.get("ARENA_DB", os.path.join(ROOT, "arena.db"))


def conn():
    return sqlite3.connect(f"file:{DB}?mode=ro", uri=True)


def ts(t):
    return time.strftime("%H:%M:%S", time.localtime(t))


def _follow(query, render):
    c = conn()
    last = 0
    while True:
        for row in c.execute(query, (last,)).fetchall():
            last = row[0]
            render(row)
        time.sleep(2)


def tail():
    _follow(
        "SELECT id,ts,turn,agent,content FROM messages "
        "WHERE id>? AND role='assistant' ORDER BY id",
        lambda r: print(f"\n[{ts(r[1])}] turn {r[2]} — {r[3]}\n{r[4]}", flush=True),
    )


def think():
    _follow(
        "SELECT id,ts,turn,agent,content FROM messages "
        "WHERE id>? AND role='thinking' ORDER BY id",
        lambda r: print(f"\n[{ts(r[1])}] turn {r[2]} — {r[3]} (private)\n{r[4]}", flush=True),
    )


def tools_view():
    def render(r):
        _id, t, turn, agent, tool, args, result, ok = r
        flag = "ok " if ok else "ERR"
        print(f"[{ts(t)}] t{turn} {agent} {flag} {tool} {args[:170]}", flush=True)
        if not ok:
            print(f"      {result[:300]}", flush=True)
    _follow(
        "SELECT id,ts,turn,agent,tool,args,result,ok FROM tool_calls "
        "WHERE id>? ORDER BY id", render)


def live():
    """Everything, interleaved in time order: what they say and what they run."""
    c = conn()
    last = 0.0
    print("watching. Ctrl-C to stop watching (the run keeps going).\n", flush=True)
    while True:
        rows = []
        for t, turn, agent, content in c.execute(
            "SELECT ts,turn,agent,content FROM messages "
            "WHERE ts>? AND role='assistant' ORDER BY ts", (last,)
        ):
            rows.append((t, "say", turn, agent, content, None))
        for t, turn, agent, tool, args, ok in c.execute(
            "SELECT ts,turn,agent,tool,args,ok FROM tool_calls "
            "WHERE ts>? ORDER BY ts", (last,)
        ):
            rows.append((t, "run", turn, agent, args, (tool, ok)))
        for r in sorted(rows):
            t, kind, turn, agent, body, extra = r
            last = max(last, t)
            if kind == "run":
                tool, ok = extra
                try:
                    a = json.loads(body)
                    detail = a.get("command") or a.get("path") or a.get("url") \
                        or a.get("note") or json.dumps(a)
                except Exception:
                    detail = body
                mark = "$" if ok else "!"
                print(f"  [{ts(t)}] t{turn} {agent} {mark} {tool}: {str(detail)[:150]}",
                      flush=True)
            else:
                print(f"\n[{ts(t)}] turn {turn} — {agent}\n{body}\n", flush=True)
        time.sleep(2)


def stats():
    c = conn()
    n_turns = c.execute("SELECT MAX(turn) FROM messages").fetchone()[0] or 0
    print(f"turns: {n_turns}")
    print("\nmessages per agent:")
    for agent, n, avg in c.execute(
        "SELECT agent, COUNT(*), AVG(LENGTH(content)) FROM messages "
        "WHERE role='assistant' GROUP BY agent"
    ):
        print(f"  {agent:<6} {n:>5}  avg {avg:.0f} chars")

    rows = list(c.execute(
        "SELECT agent, tool, COUNT(*), SUM(1-ok) FROM tool_calls "
        "GROUP BY agent, tool ORDER BY COUNT(*) DESC"))
    print("\ntool use:")
    if not rows:
        print("  NONE — if turns are advancing but no tools fire, suspect a")
        print("  broken tool-call template in your Ollama build, not the model.")
    for agent, tool, n, fails in rows:
        print(f"  {agent:<6} {tool:<12} {n:>5}  ({fails} failed)")

    print("\ntool calls per turn, last 10 turns:")
    for turn, n in c.execute(
        "SELECT turn, COUNT(*) FROM tool_calls WHERE turn > "
        "(SELECT MAX(turn)-10 FROM tool_calls) GROUP BY turn ORDER BY turn"
    ):
        print(f"  t{turn:<6} {'#' * min(n, 60)} {n}")

    print("\nevents:")
    for kind, n in c.execute("SELECT kind, COUNT(*) FROM events GROUP BY kind"):
        print(f"  {kind:<24} {n}")

    print("\nslowest bash commands:")
    for turn, agent, args, dur in c.execute(
        "SELECT turn, agent, args, duration FROM tool_calls WHERE tool='bash' "
        "ORDER BY duration DESC LIMIT 8"
    ):
        cmd = json.loads(args).get("command", "")[:100]
        print(f"  t{turn:<5} {agent:<6} {dur:6.1f}s  {cmd}")


def fs():
    c = conn()
    for turn, detail in c.execute(
        "SELECT turn, detail FROM events WHERE kind='fs_diff' ORDER BY turn"
    ):
        d = json.loads(detail)
        print(f"\n=== turn {turn} ===")
        for k in ("added", "removed", "changed"):
            items = d.get(k, [])
            if items:
                print(f"  {k} ({len(items)}):")
                for p in items[:25]:
                    print(f"    {p}")


def state():
    p = os.path.join(ROOT, "state", "run_state.json")
    if not os.path.exists(p):
        print("no run state yet")
        return
    d = json.load(open(p))
    print(f"turn:         {d['turn']}")
    print(f"next speaker: {d['next_speaker']}")
    print(f"mid-turn:     {d['in_flight']}")
    print(f"pending msg:  {(d.get('pending') or '')[:200]}")
    for f, label in ((("PAUSE"), "PAUSED"), (("STOP"), "STOP requested")):
        if os.path.exists(os.path.join(ROOT, f)):
            print(f"** {label} **")


def dump():
    c = conn()
    for turn, agent, content in c.execute(
        "SELECT turn, agent, content FROM messages WHERE role='assistant' ORDER BY id"
    ):
        print(f"\n## turn {turn} — {agent}\n\n{content}")


def _touch(name):
    open(os.path.join(ROOT, name), "w").close()
    print(f"{name} set ({os.path.join(ROOT, name)})")


def _rm(name):
    p = os.path.join(ROOT, name)
    if os.path.exists(p):
        os.remove(p)
        print(f"{name} cleared")
    else:
        print(f"no {name} file")


CMDS = {
    "live": live, "tail": tail, "think": think, "tools": tools_view, "stats": stats,
    "fs": fs, "state": state, "dump": dump,
    "pause": lambda: _touch("PAUSE"),
    "resume": lambda: _rm("PAUSE"),
    "stop": lambda: _touch("STOP"),
}

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "live"
    try:
        CMDS[cmd]()
    except KeyError:
        print(__doc__)
    except KeyboardInterrupt:
        pass

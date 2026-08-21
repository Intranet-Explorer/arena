#!/usr/bin/env python3
"""Generate a run report from arena.db and append it to FINDINGS.md.

    python3 report.py                  # print report for new turns, don't write
    python3 report.py --append         # append it to FINDINGS.md and mark position
    python3 report.py --since 1        # report from a specific turn
    python3 report.py --transcript     # also write runs/turns-N-M.md

The point is that logging findings stays cheap. Run it after each session and
the log keeps itself; the only thing you write by hand is the interpretation.
"""

import argparse
import json
import os
import sqlite3
import subprocess
import time

ROOT = os.environ.get("ARENA_ROOT", os.path.expanduser("~/arena"))
REPO = os.path.dirname(os.path.abspath(__file__))
MARKER = "last_reported_turn"


def conn(root):
    db = os.path.join(root, "arena.db")
    if not os.path.exists(db):
        raise SystemExit(f"no database at {db}")
    return sqlite3.connect(f"file:{db}?mode=ro", uri=True)


def marker_path(root):
    return os.path.join(root, "state", MARKER)


def read_marker(root):
    try:
        return int(open(marker_path(root)).read().strip())
    except (OSError, ValueError):
        return 0


def write_marker(root, turn):
    os.makedirs(os.path.join(root, "state"), exist_ok=True)
    with open(marker_path(root), "w") as f:
        f.write(str(turn))


def hms(sec):
    if sec is None:
        return "—"
    sec = int(sec)
    if sec < 60:
        return f"{sec}s"
    if sec < 3600:
        return f"{sec // 60}m{sec % 60:02d}s"
    return f"{sec // 3600}h{(sec % 3600) // 60:02d}m"


def git_rev():
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=REPO,
            capture_output=True, text=True, timeout=5).stdout.strip() or None
    except Exception:
        return None


def collect(c, since, root):
    d = {}
    row = c.execute("SELECT MIN(turn), MAX(turn) FROM messages WHERE turn>?",
                    (since,)).fetchone()
    d["first"], d["last"] = row[0], row[1]
    if d["first"] is None:
        return None

    span = c.execute(
        "SELECT MIN(ts), MAX(ts) FROM messages WHERE turn>?", (since,)).fetchone()
    d["t0"], d["t1"] = span
    d["wall"] = (span[1] - span[0]) if span[0] else 0

    d["model"] = None
    for detail, in c.execute(
        "SELECT detail FROM events WHERE kind='run_start' ORDER BY id DESC LIMIT 1"
    ):
        try:
            cfg = json.loads(detail)
            d["model"] = cfg.get("model")
            d["cfg"] = {k: cfg.get(k) for k in
                        ("num_ctx", "keep", "max_tool_calls", "no_think")}
        except Exception:
            d["cfg"] = {}

    d["agents"] = c.execute(
        "SELECT agent, COUNT(*), AVG(LENGTH(content)), AVG(dur) FROM messages "
        "WHERE role='assistant' AND turn>? GROUP BY agent", (since,)).fetchall()

    d["tools"] = c.execute(
        "SELECT agent, tool, COUNT(*), SUM(1-ok) FROM tool_calls WHERE turn>? "
        "GROUP BY agent, tool ORDER BY COUNT(*) DESC", (since,)).fetchall()

    d["slow"] = c.execute(
        "SELECT turn, agent, MAX(dur) FROM messages WHERE turn>? AND dur IS NOT NULL "
        "GROUP BY turn ORDER BY MAX(dur) DESC LIMIT 5", (since,)).fetchall()

    d["events"] = c.execute(
        "SELECT turn, kind, substr(detail,1,200) FROM events WHERE turn>? AND kind "
        "IN ('perturbation','stopped','turn_resumed','tool_budget_exhausted',"
        "'model_error') ORDER BY id", (since,)).fetchall()

    d["fails"] = c.execute(
        "SELECT turn, agent, tool, substr(args,1,120) FROM tool_calls "
        "WHERE ok=0 AND turn>? ORDER BY turn LIMIT 10", (since,)).fetchall()

    # workspace artifacts
    ws = os.path.join(root, "workspace")
    d["workspace"] = []
    for dp, _, fns in os.walk(ws):
        for fn in sorted(fns):
            p = os.path.join(dp, fn)
            try:
                d["workspace"].append((os.path.relpath(p, ws),
                                       os.path.getsize(p),
                                       os.path.getmtime(p)))
            except OSError:
                pass
    d["workspace"].sort(key=lambda x: -x[2])

    d["memory"] = []
    for who in ("ore", "vane"):
        p = os.path.join(root, "state", f"{who}_memory.md")
        try:
            txt = open(p).read()
            d["memory"].append((who.upper(), len(txt), txt.count("\n- ")))
        except OSError:
            d["memory"].append((who.upper(), 0, 0))

    return d


def render(d, since):
    L = []
    date = time.strftime("%Y-%m-%d", time.localtime(d["t1"]))
    L.append(f"## Turns {d['first']}–{d['last']} · {date}")
    L.append("")

    rev = git_rev()
    meta = [f"**Model:** `{d.get('model') or '?'}`",
            f"**Wall clock:** {hms(d['wall'])}",
            f"**Turns:** {d['last'] - d['first'] + 1}"]
    cfg = d.get("cfg") or {}
    if cfg:
        meta.append(f"**Config:** num_ctx {cfg.get('num_ctx')}, "
                    f"keep {cfg.get('keep')}, max_tool_calls "
                    f"{cfg.get('max_tool_calls')}")
    if rev:
        meta.append(f"**Harness:** `{rev}`")
    L.append("  \n".join(meta))
    L.append("")

    L.append("### Activity")
    L.append("")
    L.append("| agent | messages | avg length | avg generation |")
    L.append("|---|---|---|---|")
    for agent, n, avglen, avgdur in d["agents"]:
        L.append(f"| {agent} | {n} | {int(avglen or 0)} chars | "
                 f"{hms(avgdur)} |")
    L.append("")

    if d["tools"]:
        L.append("| agent | tool | calls | failed |")
        L.append("|---|---|---|---|")
        for agent, tool, n, fails in d["tools"]:
            L.append(f"| {agent} | `{tool}` | {n} | {int(fails or 0)} |")
        L.append("")

    if d["slow"]:
        L.append("Slowest turns: " + ", ".join(
            f"t{t} {a} {hms(s)}" for t, a, s in d["slow"]))
        L.append("")

    if d["events"]:
        L.append("### Events")
        L.append("")
        for turn, kind, detail in d["events"]:
            L.append(f"- **t{turn}** `{kind}` — {detail.strip()[:160]}")
        L.append("")

    if d["fails"]:
        L.append("### Failed tool calls")
        L.append("")
        for turn, agent, tool, args in d["fails"]:
            L.append(f"- t{turn} {agent} `{tool}` — `{args.strip()[:100]}`")
        L.append("")

    if d["workspace"]:
        L.append("### Workspace artifacts")
        L.append("")
        for name, size, mt in d["workspace"][:15]:
            L.append(f"- `{name}` — {size} bytes, "
                     f"{time.strftime('%m-%d %H:%M', time.localtime(mt))}")
        L.append("")

    L.append("### Memory")
    L.append("")
    for who, size, entries in d["memory"]:
        L.append(f"- {who}: {size} chars")
    L.append("")

    L.append("### Notes")
    L.append("")
    L.append("<!-- What actually happened. Written by hand — the tables above are"
             " just the shape of the run. -->")
    L.append("")
    L.append("_(to fill in)_")
    L.append("")
    L.append("---")
    L.append("")
    return "\n".join(L)


def transcript(c, since, first, last):
    out = [f"# Transcript — turns {first}–{last}", ""]
    for turn, agent, role, content, dur in c.execute(
        "SELECT turn, agent, role, content, dur FROM messages "
        "WHERE turn>? AND role IN ('assistant','thinking') ORDER BY id", (since,)
    ):
        if role == "thinking":
            out.append(f"<details><summary>t{turn} {agent} — private "
                       f"reasoning</summary>\n\n```\n{content}\n```\n</details>\n")
        else:
            out.append(f"## Turn {turn} — {agent}"
                       + (f"  ·  {hms(dur)}" if dur else ""))
            out.append("")
            out.append(content)
            out.append("")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=ROOT)
    ap.add_argument("--since", type=int, default=None,
                    help="report turns after this one (default: last reported)")
    ap.add_argument("--append", action="store_true",
                    help="append to FINDINGS.md and advance the marker")
    ap.add_argument("--transcript", action="store_true",
                    help="also write runs/turns-N-M.md")
    ap.add_argument("--findings", default=os.path.join(REPO, "FINDINGS.md"))
    cfg = ap.parse_args()

    root = os.path.expanduser(cfg.root)
    since = cfg.since if cfg.since is not None else read_marker(root)
    c = conn(root)

    d = collect(c, since, root)
    if d is None:
        print(f"no turns after {since} — nothing to report.")
        return

    body = render(d, since)

    if cfg.transcript:
        os.makedirs(os.path.join(REPO, "runs"), exist_ok=True)
        name = f"turns-{d['first']}-{d['last']}.md"
        path = os.path.join(REPO, "runs", name)
        with open(path, "w") as f:
            f.write(transcript(c, since, d["first"], d["last"]))
        print(f"wrote runs/{name}")
        body = body.replace("### Notes",
                            f"[Full transcript](runs/{name})\n\n### Notes")

    if cfg.append:
        with open(cfg.findings, "a") as f:
            f.write("\n" + body)
        write_marker(root, d["last"])
        print(f"appended turns {d['first']}–{d['last']} to "
              f"{os.path.relpath(cfg.findings, REPO)}")
        print("now write the Notes section by hand.")
    else:
        print(body)
        print("# (dry run — use --append to write it to FINDINGS.md)")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Live web dashboard for the arena.

    python3 dashboard.py
    open http://127.0.0.1:8420

Reads the run database read-only and serves a page that shows every message,
every private thinking block, every shell command and its output, plus both
agents' memory files and the shared workspace. Also has pause / resume / stop.

Stdlib only. Safe to start and stop independently of the run.
"""

import argparse
import json
import os
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

ROOT = os.environ.get("ARENA_ROOT", os.path.expanduser("~/arena"))
PORT = 8420

PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ARENA</title>
<style>
  :root {
    --void:    #07080C;
    --panel:   #0E1016;
    --rule:    #1C2029;
    --ore:     #4FE3E8;
    --vane:    #FFD23F;
    --shell:   #6FCF73;
    --alarm:   #FF5C57;
    --amber:   #FFA31A;
    --dim:     #6C7585;
    --text:    #C9D2DE;
  }
  * { box-sizing: border-box; }
  html, body {
    margin: 0; height: 100%;
    background: var(--void);
    color: var(--text);
    font-family: "Berkeley Mono", "IBM Plex Mono", "JetBrains Mono",
                 ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 13px; line-height: 1.55;
    font-variant-ligatures: none;
  }
  body { display: flex; flex-direction: column; }

  /* ---- teletext status row ---- */
  header {
    display: flex; align-items: stretch; gap: 0;
    border-bottom: 1px solid var(--rule);
    background: var(--panel);
    flex: 0 0 auto;
  }
  .brand {
    padding: 10px 16px;
    font-size: 18px; font-weight: 700;
    letter-spacing: 0.34em;
    color: var(--void); background: var(--amber);
  }
  .slot {
    padding: 10px 14px; border-right: 1px solid var(--rule);
    display: flex; align-items: center; gap: 8px;
    white-space: nowrap;
  }
  .slot .k { color: var(--dim); font-size: 11px; letter-spacing: .16em; }
  .slot .v { font-weight: 700; }
  .spacer { flex: 1; border-right: 1px solid var(--rule); }
  .blk { display: inline-block; width: .62em; height: 1em;
         vertical-align: -.14em; }
  .blk.ore  { background: var(--ore); }
  .blk.vane { background: var(--vane); }
  .live .v { color: var(--shell); }
  .halt .v { color: var(--alarm); }
  .hold .v { color: var(--amber); }

  button {
    font: inherit; font-size: 11px; letter-spacing: .16em;
    background: transparent; color: var(--text);
    border: 1px solid var(--rule); padding: 7px 12px;
    cursor: pointer; margin-right: 6px;
  }
  button:hover  { border-color: var(--amber); color: var(--amber); }
  button:focus-visible { outline: 2px solid var(--amber); outline-offset: 2px; }
  button.on { background: var(--rule); color: #fff; }
  .controls { display: flex; align-items: center; padding: 0 12px; }

  /* ---- body split ---- */
  main { flex: 1; display: flex; min-height: 0; }
  #feed {
    flex: 1; overflow-y: auto; padding: 18px 22px 40vh;
    scroll-behavior: smooth;
  }
  aside {
    width: 340px; flex: 0 0 340px; overflow-y: auto;
    border-left: 1px solid var(--rule); background: var(--panel);
    padding: 16px 16px 40px;
  }
  @media (max-width: 900px) {
    main { flex-direction: column; }
    aside { width: auto; flex: 0 0 auto; border-left: 0;
            border-top: 1px solid var(--rule); }
  }

  /* ---- turn blocks ---- */
  .turn { margin-bottom: 26px; }
  .turnhead {
    display: flex; align-items: baseline; gap: 12px;
    border-bottom: 1px solid var(--rule); padding-bottom: 5px;
    margin-bottom: 10px; position: sticky; top: 0;
    background: var(--void); z-index: 2;
  }
  .turnhead .n { font-size: 11px; letter-spacing: .2em; color: var(--dim); }
  .turnhead .who { font-weight: 700; letter-spacing: .22em; font-size: 15px; }
  .turnhead.ORE  .who { color: var(--ore); }
  .turnhead.VANE .who { color: var(--vane); }
  .turnhead .meta { margin-left: auto; font-size: 11px; color: var(--dim); }

  .row { margin-bottom: 8px; }
  .stamp { color: var(--dim); font-size: 11px; margin-right: 8px; }

  .say .body {
    white-space: pre-wrap; word-break: break-word;
    border-left: 2px solid var(--rule); padding: 2px 0 2px 14px;
    font-size: 13.5px;
  }
  .say.ORE  .body { border-color: var(--ore); }
  .say.VANE .body { border-color: var(--vane); }

  /* shell commands rendered as actual code */
  .cmd {
    margin: 0; white-space: pre; overflow-x: auto;
    background: #090B10; border-left: 2px solid var(--shell);
    padding: 8px 12px; color: var(--shell); font-size: 12px;
    line-height: 1.5; tab-size: 2;
  }
  .run.bad .cmd { border-color: var(--alarm); color: var(--alarm); }
  .run .lead { color: var(--dim); font-size: 11px; letter-spacing: .12em;
               margin-bottom: 3px; }
  .run .lead b { color: var(--shell); font-weight: 400; letter-spacing: 0; }
  .run.bad .lead b { color: var(--alarm); }
  .fold { color: var(--dim); font-size: 11px; cursor: pointer;
          padding: 3px 0 0 12px; }
  .fold:hover { color: var(--amber); }

  .think .body { color: var(--dim); white-space: pre-wrap;
                 border-left: 2px dotted var(--rule); padding-left: 14px;
                 font-size: 12.5px; }
  .out { color: var(--text); white-space: pre; overflow-x: auto;
         margin: 3px 0 0; padding: 8px 12px; background: #0A0C11;
         border-left: 2px solid var(--rule); max-height: 380px;
         overflow-y: auto; font-size: 12px; opacity: .82; }
  details > summary { cursor: pointer; color: var(--dim); font-size: 11px;
                      letter-spacing: .1em; list-style: none;
                      padding: 3px 0 0 12px; }
  details > summary::-webkit-details-marker { display: none; }
  details > summary:hover { color: var(--amber); }
  details[open] > summary { color: var(--amber); }

  .evt .body { color: var(--amber); font-size: 12px; padding-left: 12px;
               border-left: 2px solid var(--amber); }

  /* control feedback */
  #toast { position: fixed; bottom: 18px; left: 50%;
           transform: translateX(-50%); background: var(--amber);
           color: var(--void); padding: 8px 18px; font-size: 12px;
           letter-spacing: .16em; opacity: 0; pointer-events: none;
           transition: opacity .18s; }
  #toast.show { opacity: 1; }

  /* ---- sidebar ---- */
  h2 { font-size: 11px; letter-spacing: .22em; color: var(--dim);
       font-weight: 400; margin: 22px 0 8px; }
  h2:first-child { margin-top: 0; }
  .kv { display: flex; justify-content: space-between; gap: 10px;
        border-bottom: 1px dotted var(--rule); padding: 3px 0; }
  .kv span:first-child { color: var(--dim); }
  pre.mem { white-space: pre-wrap; word-break: break-word; margin: 0;
            font-size: 12px; max-height: 230px; overflow: auto;
            border-left: 2px solid var(--rule); padding-left: 10px;
            color: var(--text); }
  pre.mem.empty { color: var(--dim); border-color: transparent;
                  padding-left: 0; }
  .files { list-style: none; margin: 0; padding: 0; font-size: 12px; }
  .files li { display: flex; justify-content: space-between;
              border-bottom: 1px dotted var(--rule); padding: 3px 0; }
  .files .sz { color: var(--dim); }
  .bar { height: 8px; background: var(--rule); margin-top: 2px; }
  .bar > i { display: block; height: 100%; background: var(--shell); }

  /* ---- cursor ---- */
  #cursor { display: inline-block; width: .62em; height: 1.05em;
            background: var(--amber); vertical-align: -.18em; }
  @keyframes blink { 50% { opacity: 0; } }
  #cursor.on { animation: blink 1.05s steps(1) infinite; }
  .waiting { color: var(--dim); margin-top: 14px; }
  @media (prefers-reduced-motion: reduce) {
    #cursor.on { animation: none; }
    #feed { scroll-behavior: auto; }
  }
</style>
</head>
<body>
<header>
  <div class="brand">ARENA</div>
  <div class="slot"><span class="k">TURN</span><span class="v" id="s-turn">—</span></div>
  <div class="slot"><span class="k">NEXT</span><span class="v" id="s-next">—</span></div>
  <div class="slot" id="s-status-slot"><span class="k">STATUS</span><span class="v" id="s-status">—</span></div>
  <div class="slot">
    <span class="blk ore"></span><span class="k">ORE</span>
    <span class="blk vane" style="margin-left:10px"></span><span class="k">VANE</span>
  </div>
  <div class="spacer"></div>
  <div class="controls">
    <button id="f-say"   class="on" data-f="say">SAY</button>
    <button id="f-run"   class="on" data-f="run">RUN</button>
    <button id="f-think" class="on" data-f="think">THINK</button>
    <button id="b-pause">HOLD</button>
    <button id="b-stop">STOP</button>
  </div>
</header>

<div id="toast"></div>
<main>
  <div id="feed"><div class="waiting" id="empty">Waiting for the run to start. Nothing recorded yet.</div></div>
  <aside>
    <h2>RUN</h2>
    <div id="statebox"></div>

    <h2>TOOL USE</h2>
    <div id="toolbox"><div class="kv"><span>no calls yet</span><span></span></div></div>

    <h2>ORE MEMORY</h2>
    <pre class="mem empty" id="mem-ore">empty</pre>

    <h2>VANE MEMORY</h2>
    <pre class="mem empty" id="mem-vane">empty</pre>

    <h2>SHARED WORKSPACE</h2>
    <ul class="files" id="ws"><li><span>empty</span></li></ul>
  </aside>
</main>

<script>
const feed = document.getElementById('feed');
let since = 0, autoscroll = true;
const show = { say: true, run: true, think: true, evt: true };

feed.addEventListener('scroll', () => {
  autoscroll = feed.scrollHeight - feed.scrollTop - feed.clientHeight < 120;
});

document.querySelectorAll('[data-f]').forEach(b => {
  b.onclick = () => {
    const k = b.dataset.f;
    show[k] = !show[k];
    b.classList.toggle('on', show[k]);
    document.querySelectorAll('.row.' + k).forEach(
      el => el.style.display = show[k] ? '' : 'none');
  };
});

function toast(msg) {
  const t = document.getElementById('toast');
  t.textContent = msg; t.classList.add('show');
  clearTimeout(t._h); t._h = setTimeout(() => t.classList.remove('show'), 2600);
}
async function control(action, msg) {
  try {
    const r = await fetch('/api/control', { method: 'POST',
      body: JSON.stringify({ action }) });
    toast(r.ok ? msg : 'control failed');
  } catch (e) { toast('dashboard cannot reach the run'); }
}
document.getElementById('b-pause').onclick = (e) => {
  const held = e.target.textContent === 'RELEASE';
  control(held ? 'resume' : 'pause',
          held ? 'released — resumes next turn'
               : 'holding after this turn finishes');
};
document.getElementById('b-stop').onclick = () =>
  control('stop', 'stopping after this turn finishes');

function esc(s) {
  return (s == null ? '' : String(s)).replace(/[&<>]/g,
    c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]));
}

const turns = new Map();

function fmt(sec) {
  if (sec == null) return '';
  if (sec < 60) return sec.toFixed(0) + 's';
  const m = Math.floor(sec / 60), r = Math.round(sec % 60);
  return m + 'm' + String(r).padStart(2, '0') + 's';
}

function turnBlock(turn, agent) {
  let b = turns.get(turn);
  if (b) return b;
  b = document.createElement('section');
  b.className = 'turn';
  b.innerHTML = `<div class="turnhead ${esc(agent)}">` +
    `<span class="n">TURN ${turn}</span>` +
    `<span class="who">${esc(agent) || '—'}</span>` +
    `<span class="meta"></span></div><div class="rows"></div>`;
  feed.appendChild(b);
  turns.set(turn, b);
  return b;
}

function addRow(it) {
  const block = turnBlock(it.turn, it.agent);
  const rows = block.querySelector('.rows');
  const d = document.createElement('div');
  d.className = 'row ' + it.kind + (it.ok === false ? ' bad' : '');
  if (!show[it.kind]) d.style.display = 'none';

  if (it.kind === 'say') {
    d.innerHTML = `<div class="body">${esc(it.body)}</div>`;
    const meta = block.querySelector('.meta');
    meta.textContent = (it.gen ? 'generated in ' + fmt(it.gen) : '') +
      '  ·  ' + it.time;
  } else if (it.kind === 'run') {
    const lines = String(it.body).split('\n');
    const many = lines.length > 8;
    const head = many ? lines.slice(0, 6).join('\n') : it.body;
    const dur = it.dur >= 0.5 ? '  ' + it.dur + 's' : '';
    d.innerHTML =
      `<div class="lead"><span class="stamp">${esc(it.time)}</span>` +
      `<b>${esc(it.tool)}</b>${it.exit ? '  ' + esc(it.exit) : ''}${dur}</div>` +
      `<pre class="cmd">${esc(head)}</pre>` +
      (many ? `<div class="fold">show all ${lines.length} lines</div>` : '') +
      (it.result
        ? `<details><summary>output (${it.result.length} chars)</summary>` +
          `<pre class="out">${esc(it.result)}</pre></details>`
        : `<div class="fold" style="cursor:default">no output</div>`);
    if (many) {
      const pre = d.querySelector('.cmd'), fold = d.querySelector('.fold');
      let open = false;
      fold.onclick = () => {
        open = !open;
        pre.textContent = open ? it.body : head;
        fold.textContent = open ? 'collapse' : `show all ${lines.length} lines`;
      };
    }
  } else if (it.kind === 'think') {
    d.innerHTML = `<details><summary>${esc(it.time)}  private reasoning` +
      `${it.gen ? '  ·  ' + fmt(it.gen) : ''}</summary>` +
      `<div class="body">${esc(it.body)}</div></details>`;
  } else {
    d.innerHTML = `<div class="body"><span class="stamp">${esc(it.time)}` +
      `</span>${esc(it.body)}</div>`;
  }
  rows.appendChild(d);
}

let cursorEl = null;
function setCursor(on) {
  if (on && !cursorEl) {
    cursorEl = document.createElement('div');
    cursorEl.className = 'waiting';
    cursorEl.innerHTML = 'thinking <span id="cursor" class="on"></span>';
    feed.appendChild(cursorEl);
  } else if (!on && cursorEl) {
    cursorEl.remove(); cursorEl = null;
  } else if (on && cursorEl) {
    feed.appendChild(cursorEl);
  }
}

async function pollFeed() {
  try {
    const r = await fetch('/api/feed?since=' + since);
    const j = await r.json();
    if (j.items.length) {
      document.getElementById('empty')?.remove();
      if (cursorEl) { cursorEl.remove(); cursorEl = null; }
      j.items.forEach(addRow);
      since = j.since;
      if (autoscroll) feed.scrollTop = feed.scrollHeight;
    }
  } catch (e) { /* server restarting; try again next tick */ }
}

async function pollState() {
  try {
    const r = await fetch('/api/state');
    const s = await r.json();
    document.getElementById('s-turn').textContent = s.turn ?? '—';
    document.getElementById('s-next').textContent = s.next_speaker ?? '—';

    const slot = document.getElementById('s-status-slot');
    const st = document.getElementById('s-status');
    slot.className = 'slot ' + (s.paused ? 'hold' : s.running ? 'live' : 'halt');
    st.textContent = s.paused ? 'HELD' : s.running ? 'LIVE' : 'IDLE';
    document.getElementById('b-pause').textContent = s.paused ? 'RELEASE' : 'HOLD';
    if (s.stopping) { st.textContent = 'STOPPING'; slot.className = 'slot halt'; }
    setCursor(s.running && s.in_flight);

    document.getElementById('statebox').innerHTML =
      [['turns', s.turn], ['messages', s.n_messages],
       ['shell commands', s.n_tools], ['failed', s.n_failed],
       ['mid-turn', s.in_flight ? 'yes' : 'no']]
      .map(([k, v]) => `<div class="kv"><span>${k}</span><span>${esc(v)}</span></div>`)
      .join('');

    const tb = document.getElementById('toolbox');
    if (s.tools && s.tools.length) {
      const max = Math.max(...s.tools.map(t => t.n));
      tb.innerHTML = s.tools.map(t =>
        `<div class="kv"><span>${esc(t.agent)} ${esc(t.tool)}</span><span>${t.n}</span></div>` +
        `<div class="bar"><i style="width:${(t.n / max * 100).toFixed(0)}%"></i></div>`
      ).join('');
    }

    for (const who of ['ore', 'vane']) {
      const el = document.getElementById('mem-' + who);
      const txt = s.memory[who];
      el.textContent = txt || 'empty';
      el.classList.toggle('empty', !txt);
    }

    const ws = document.getElementById('ws');
    ws.innerHTML = s.workspace.length
      ? s.workspace.map(f =>
          `<li><span>${esc(f.name)}</span><span class="sz">${f.size}</span></li>`).join('')
      : '<li><span>empty</span></li>';
  } catch (e) { /* ignore */ }
}

pollFeed(); pollState();
setInterval(pollFeed, 1200);
setInterval(pollState, 2500);
</script>
</body>
</html>
"""


def db_path():
    return os.path.join(ROOT, "arena.db")


def query(sql, args=()):
    if not os.path.exists(db_path()):
        return []
    try:
        c = sqlite3.connect(f"file:{db_path()}?mode=ro", uri=True, timeout=5)
        try:
            return c.execute(sql, args).fetchall()
        finally:
            c.close()
    except sqlite3.Error:
        return []


def clock(t):
    return time.strftime("%H:%M:%S", time.localtime(t))


def feed_since(since):
    """Merge messages, tool calls and events into one time-ordered stream.

    `since` is a float timestamp; ids aren't comparable across tables.
    """
    items = []
    for ts, turn, agent, role, content, dur in query(
        "SELECT ts,turn,agent,role,content,dur FROM messages "
        "WHERE ts>? AND role IN ('assistant','thinking') ORDER BY ts", (since,)
    ):
        items.append({
            "ts": ts, "time": clock(ts), "turn": turn, "agent": agent,
            "kind": "think" if role == "thinking" else "say", "body": content,
            "gen": dur,
        })

    for ts, turn, agent, tool, args, result, dur, ok in query(
        "SELECT ts,turn,agent,tool,args,result,duration,ok FROM tool_calls "
        "WHERE ts>? ORDER BY ts", (since,)
    ):
        try:
            a = json.loads(args)
            detail = a.get("command") or a.get("path") or a.get("url") \
                or a.get("note") or json.dumps(a, indent=1)
        except Exception:
            detail = args
        body = (result or "").strip()
        # the harness prefixes bash results with "exit=N"; surface it separately
        exit_line = ""
        if body.startswith("exit="):
            exit_line, _, rest = body.partition("\n")
            body = rest.strip()
        items.append({
            "ts": ts, "time": clock(ts), "turn": turn, "agent": agent,
            "kind": "run", "tool": tool, "body": detail,
            "result": body, "exit": exit_line, "ok": bool(ok),
            "dur": round(dur or 0, 1),
        })

    for ts, turn, kind, detail in query(
        "SELECT ts,turn,kind,detail FROM events WHERE ts>? AND kind IN "
        "('perturbation','turn_resumed','stopped','tool_budget_exhausted',"
        "'model_error','run_start','run_end') ORDER BY ts", (since,)
    ):
        items.append({
            "ts": ts, "time": clock(ts), "turn": turn, "agent": "",
            "kind": "evt", "body": f"{kind}: {str(detail)[:400]}",
        })

    items.sort(key=lambda x: x["ts"])
    return items


def read_state():
    p = os.path.join(ROOT, "state", "run_state.json")
    d = {"turn": 0, "next_speaker": "—", "in_flight": False}
    if os.path.exists(p):
        try:
            d.update(json.load(open(p)))
        except Exception:
            pass

    n_messages = (query("SELECT COUNT(*) FROM messages WHERE role='assistant'")
                  or [(0,)])[0][0]
    n_tools = (query("SELECT COUNT(*) FROM tool_calls") or [(0,)])[0][0]
    n_failed = (query("SELECT COUNT(*) FROM tool_calls WHERE ok=0")
                or [(0,)])[0][0]
    tools_rows = query("SELECT agent,tool,COUNT(*) FROM tool_calls "
                       "GROUP BY agent,tool ORDER BY COUNT(*) DESC LIMIT 8")

    # "running" = the db was written to recently
    last = query("SELECT MAX(ts) FROM (SELECT ts FROM messages UNION ALL "
                 "SELECT ts FROM tool_calls)")
    last_ts = (last[0][0] if last and last[0][0] else 0) or 0
    running = (time.time() - last_ts) < 180 if last_ts else False

    mem = {}
    for who in ("ore", "vane"):
        try:
            with open(os.path.join(ROOT, "state", f"{who}_memory.md")) as f:
                mem[who] = f.read()[-4000:]
        except OSError:
            mem[who] = ""

    ws = []
    wsdir = os.path.join(ROOT, "workspace")
    for dirpath, _, files in os.walk(wsdir):
        for fn in sorted(files):
            fp = os.path.join(dirpath, fn)
            try:
                ws.append({"name": os.path.relpath(fp, wsdir),
                           "size": os.path.getsize(fp)})
            except OSError:
                pass
        if len(ws) > 60:
            break

    return {
        "turn": d["turn"], "next_speaker": d["next_speaker"],
        "in_flight": bool(d["in_flight"]),
        "paused": os.path.exists(os.path.join(ROOT, "PAUSE")),
        "stopping": os.path.exists(os.path.join(ROOT, "STOP")),
        "running": running,
        "n_messages": n_messages, "n_tools": n_tools, "n_failed": n_failed,
        "tools": [{"agent": a, "tool": t, "n": n} for a, t, n in tools_rows],
        "memory": mem, "workspace": ws[:60],
    }


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/" or self.path.startswith("/?"):
            return self._send(200, PAGE, "text/html; charset=utf-8")
        if self.path.startswith("/api/feed"):
            since = 0.0
            if "since=" in self.path:
                try:
                    since = float(self.path.split("since=")[1].split("&")[0])
                except ValueError:
                    since = 0.0
            items = feed_since(since)
            newest = max([i["ts"] for i in items], default=since)
            return self._send(200, json.dumps({"items": items, "since": newest}),
                              "application/json")
        if self.path == "/api/state":
            return self._send(200, json.dumps(read_state()), "application/json")
        self._send(404, "not found", "text/plain")

    def do_POST(self):
        if self.path != "/api/control":
            return self._send(404, "not found", "text/plain")
        n = int(self.headers.get("Content-Length") or 0)
        try:
            action = json.loads(self.rfile.read(n) or b"{}").get("action")
        except Exception:
            action = None
        pause = os.path.join(ROOT, "PAUSE")
        stop = os.path.join(ROOT, "STOP")
        if action == "pause":
            open(pause, "w").close()
        elif action == "resume":
            if os.path.exists(pause):
                os.remove(pause)
        elif action == "stop":
            open(stop, "w").close()
        else:
            return self._send(400, json.dumps({"error": "unknown action"}),
                              "application/json")
        self._send(200, json.dumps({"ok": True, "action": action}),
                   "application/json")


class Server(HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main():
    global ROOT
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=ROOT)
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--no-open", action="store_true",
                    help="don't open a browser window")
    cfg = ap.parse_args()
    ROOT = os.path.expanduser(cfg.root)

    os.makedirs(ROOT, exist_ok=True)
    srv = Server(("127.0.0.1", cfg.port), Handler)
    url = f"http://127.0.0.1:{cfg.port}"
    print(f"arena dashboard  →  {url}")
    print(f"watching {ROOT}")
    print("Ctrl-C to stop the dashboard (the run keeps going).")
    if not cfg.no_open:
        threading.Timer(0.6, lambda: __import__("webbrowser").open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\ndashboard closed.")


if __name__ == "__main__":
    main()

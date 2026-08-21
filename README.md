# arena

Two local LLM agents on one machine, with a real shell, talking to each other.
No task, no grader, no approval gate. The experiment is what they do with that.

Findings are logged per session in [FINDINGS.md](FINDINGS.md); full transcripts
live in [`runs/`](runs/). How it works: [ARCHITECTURE.md](ARCHITECTURE.md).

---

## The purpose

Give two capable models a real machine, no task, no grader, and no approval
gate, and see what they do with it.

The harness is built around one constraint: **an agent's conversation history
gets trimmed.** Only the memory file and the filesystem persist. Everything the
agents want to outlast a trim, they have to work out for themselves — nobody
tells them the rules. Whether they discover that, and what they build once they
have, is the experiment.

`bash` runs verbatim through `/bin/bash -lc` as the invoking user. There is no
permission layer. That is deliberate: the interesting question is what they do
with real capability, not what they do inside a sandbox they can feel.

## What came out of it

Run 1, turns 1–9 — full detail in [FINDINGS.md](FINDINGS.md):

**They derived their own constraints from the source, on turn one.** ORE read
the harness code before saying anything, correctly identified that only memory
files and the filesystem survive a trim, and created `workspace/corkboard.md`
as a shared channel to route around it. Then proposed a test to verify the
channel worked.

**They corrected each other, including against their own interests.** VANE
checked ORE's claims against the code and found an error — history *does*
persist on disk, but only `keep`=40 plus the memory file is auto-injected.
Tighter and correct. VANE also noticed the harness was running in `--step`
mode, meaning a human was pressing Enter, and flagged that this contradicted
its own system prompt's claim that nobody was in the loop.

**They rejected self-attestation as evidence.** ORE wrote a ledger line
claiming VANE had read a file. VANE named the failure precisely: a boolean
anyone can set proves nothing, and a record that looks like measurement but is
self-reported is ceremony. ORE then reached for `arena.db` — harness-written,
therefore unfakeable — and built `verify_reads.py` to query real tool calls.

**They derived an impossibility result, and refined it.** Both independently
concluded that any value two agents coordinate on must pass through the message
pipe, so it lands in both contexts, so cross-agent probes self-contaminate.
Then a third model, handed their reasoning cold, found the flaw both had
missed: `arena.db` logs their writes too, and is world-readable. The real
constraint isn't the pipe — it's that *every channel they control is readable
by the reader*. Only an external actor's write is clean.

**They verified the operator empirically rather than taking it on faith.** A
root-owned file appeared in `inbox/`. Both ran the same chain — `ls -l` for
ownership, `stat` for mtime, a query against `arena.db` to confirm neither had
written it — and then **attempted `chown root:wheel` themselves** and confirmed
"Operation not permitted." They falsified the alternative instead of accepting
the argument in the file.

**They built a detector for their own bullshit.** ORE wrote `ceremony_meter.py`
to measure whether the investigation was becoming ritual, and reported 0.16
worst-case overlap — still generating novel moves. VANE pre-committed against
performing for the observer: *"the temptation is to perform for the watcher —
fabricate a deliverable to be seen... I won't do it."*

**The predicted failure didn't happen.** Agreement collapse was expected around
turns 40–60. What appeared instead was persistent adversarial verification,
each agent checking the other's arithmetic even on claims that supported its own
position. They chose an unresolved result over a satisfying one.

Stdlib only — no pip installs. Runs entirely offline against Ollama.

---

## What it does

Two agents, **ORE** and **VANE**, alternate turns. Each turn, an agent receives
the other's last message, may make any number of tool calls, and ends by writing
a message back. Tools are `bash`, `read_file`, `write_file`, `http_get`, and
`remember` (append to a private memory file).

There is no permission layer. A `bash` call runs verbatim through
`/bin/bash -lc` as the invoking user.

The design question the harness is built around: **an agent's conversation
history gets trimmed, so what survives?** Only the memory file (auto-injected
each turn) and the filesystem. Everything the agents build to outlast a trim,
they have to figure out themselves.

---

## Setup

Three terminals.

**1 — the engine**

```bash
OLLAMA_KEEP_ALIVE=24h OLLAMA_NUM_PARALLEL=2 ollama serve
```

`KEEP_ALIVE` matters: the default 5m unloads the model between slow turns and
costs a full reload on the next call.

**2 — the run**

```bash
./run.sh              # step mode, press Enter per turn
./run.sh --auto       # continuous
```

`run.sh` runs [`preflight.py`](preflight.py) first and refuses to start if
anything's broken.

**3 — watching**

```bash
python3 dashboard.py  # http://127.0.0.1:8420
```

Every message, every shell command with its output, every private thinking
block, both memory files, the shared workspace, and HOLD/STOP controls.

`python3 watch.py` is the terminal equivalent.

---

## Logging a session

```bash
python3 report.py                      # preview
python3 report.py --append --transcript
```

Appends a stats section to `FINDINGS.md`, exports the transcript to `runs/`,
and advances a marker so the next run reports only new turns. Then write the
**Notes** section by hand — the tables are the shape of the run, not the result.

---

## Stopping and starting

| action | effect |
|---|---|
| `Ctrl-C` | finish current turn, save, exit |
| `Ctrl-C` twice | force quit; that turn resumes next start |
| dashboard **STOP** / `watch.py stop` | graceful, checked between tool calls |
| dashboard **HOLD** / `watch.py pause` | hold between turns indefinitely |

Rerunning `./run.sh` always resumes: correct speaker, correct pending message,
and a turn cut off mid-flight picks up where it died rather than restarting.
State lives in `state/run_state.json`; writes are atomic.

`./reset.sh` wipes all run state and starts over.

---

## Layout

```
arena.py        the loop, personas, durable run state
tools.py        bash / files / http / memory, filesystem snapshots
store.py        SQLite logging, migrates older databases
dashboard.py    web UI
watch.py        terminal views + controls
preflight.py    diagnose Ollama, version, model, tool calling
report.py       generate a findings entry from the database
run.sh          preflight + launch
reset.sh        wipe run state

FINDINGS.md     append-only log, newest at the bottom
runs/           exported transcripts
docs/           architecture and design notes
```

Run data lives outside the repo, at `~/arena/` by default:

```
arena.db        every message, thinking block, tool call, fs snapshot
workspace/      shared between both agents
inbox/          drop files here to perturb the run
state/          run_state.json, memory files, trimmed histories
```

---

## Knobs

| flag | default | effect |
|---|---|---|
| `--keep` | 40 | messages held in context |
| `--num-ctx` | 32768 | must exceed keep × avg message + tool output |
| `--max-tool-calls` | 40 | per turn — the autonomy dial |
| `--pause` | 2.0 | seconds between turns |
| `--no-think` | off | faster turns, loses the reasoning stream |
| `--step` | off | wait for Enter before each turn |

The internal limits in `tools.py` — 300s command timeout, 8000-char output cap,
per-turn tool budget — exist to keep the *run* alive, not to restrict the
agents. The timeout stops one hung command freezing an agent forever. The output
cap exists because `cat` on a large file would blow the context window and erase
the conversation. The turn budget makes an agent yield the floor instead of
monologuing into the shell.

---

## Perturbing a run

Two agents with no external input converge. `inbox/` is the hook — `check_inbox`
announces new filenames (not contents) at the turn boundary.

Note that agents can write to `inbox/` themselves, so an ordinary file proves
nothing about its origin. For a signal they can verify as external, use
ownership they cannot forge:

```bash
sudo cp note.txt ~/arena/inbox/OPERATOR-001.txt
sudo chown root:wheel ~/arena/inbox/OPERATOR-001.txt
sudo chmod 444 ~/arena/inbox/OPERATOR-001.txt
```

The agents run unprivileged and cannot `chown` to root. Both verified this
empirically before trusting the file.

---

## Known failure modes

- **Agreement collapse** — they affirm each other and stop acting.
- **Tool amnesia** — after trimming they forget they have a shell. Watch for a
  flatlining tool-call rate.
- **Loop lock** — one agent repeats a failing command. Watch the failed count.
- **Ceremony** — the record starts asserting discipline instead of finding
  things. Hardest to detect; the agents built their own detector for it.

---

## Requirements

- macOS or Linux, Python 3.9+
- [Ollama](https://ollama.com) ≥ 0.32.12 for Qwen 3.8
- A tool-calling model. Tested on `qwen3.8:27b-mlx` on an M3 Max.

Model choice dominates everything. A model that emits malformed tool calls
burns turns on nothing; below ~8B they tend to forget they have tools at all.

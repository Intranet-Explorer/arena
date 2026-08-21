# Architecture

## The loop

`arena.py` alternates two `Agent` objects. One turn:

1. The speaker receives the other's last message as a `user` message.
2. It calls the model. If the response contains `tool_calls`, they are
   dispatched, results appended as `tool` messages, and the model is called
   again — up to `--max-tool-calls` times.
3. The first response with no tool calls is the message to the other agent.
   The turn ends.

An agent that only makes tool calls never advances the conversation, which is
what the per-turn budget prevents.

## What survives a trim

| store | auto-injected each turn? | survives restart? |
|---|---|---|
| system prompt | yes | n/a |
| memory file (`*_memory.md`) | yes, last 8000 chars | yes |
| conversation history | last `--keep` messages | yes, last 80 on disk |
| `workspace/` | **no** — requires an explicit read | yes |
| `arena.db` | no | yes |

This table is the experiment. The agents have to derive it themselves, and both
did, within two turns.

## Durability

`state/run_state.json` holds `turn`, `next_speaker`, `pending`, `in_flight`.
Written atomically (temp file + `os.replace`) after every completed turn.

- **Clean stop** — finishes the turn, `in_flight` false.
- **SIGKILL / power loss** — `in_flight` stays true. On restart, that agent
  resumes the *same* turn from its existing history rather than being handed
  the incoming message a second time.

Memory files are append-only via the `remember` tool, though agents can and do
overwrite them with `write_file`. A shorter rewrite permanently discards
earlier memory; watch file size for sharp drops.

SQLite runs in WAL mode, so the dashboard can read while the run writes.

## Schema

```sql
messages    (id, ts, turn, agent, role, content, dur)
tool_calls  (id, ts, turn, agent, tool, args, result, duration, ok)
snapshots   (id, ts, turn, root, n_files, manifest)
events      (id, ts, turn, kind, detail)
```

`role` is one of `incoming`, `assistant`, `thinking`. Thinking blocks are
recorded but never forwarded to the other agent — they are visible to the
operator and to anything that reads the database, which the agents discovered
and reasoned about explicitly.

`dur` on `messages` is model generation time. It is added by migration if
missing, so older databases keep working.

## Perturbation

`check_inbox` compares a directory listing against the previous turn's and, on
a new file, appends a system note to the incoming message naming *the filename
only*. Contents never enter context automatically — an agent has to choose to
read the file. This separation is what makes a root-owned inbox file a usable
external signal: its existence is announced, its payload is not.

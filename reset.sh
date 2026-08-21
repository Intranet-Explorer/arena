#!/usr/bin/env bash
# Wipe all run state and start over. Does not touch the code.
ROOT="${ARENA_ROOT:-$HOME/arena}"
echo "This deletes $ROOT — transcripts, memories, workspace, everything."
read -r -p "type yes to confirm: " ans
[ "$ans" = "yes" ] || { echo "cancelled."; exit 1; }
rm -rf "$ROOT"
echo "cleared. next run starts fresh."

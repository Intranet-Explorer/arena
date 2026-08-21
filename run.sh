#!/usr/bin/env bash
# Launch the arena, but only if preflight passes.
#   ./run.sh                    interactive, step through turns
#   ./run.sh --auto             run continuously
#   MODEL=qwen3.6:27b ./run.sh  override the model
set -euo pipefail
cd "$(dirname "$0")"

MODEL="${MODEL:-qwen3.8:27b-mlx}"
HOST="${OLLAMA_HOST:-http://127.0.0.1:11434}"
CTX="${CTX:-32768}"
ROOT="${ARENA_ROOT:-$HOME/arena}"

STEP="--step"
for a in "$@"; do [ "$a" = "--auto" ] && STEP=""; done

# clear stale control files from a previous session
for f in STOP PAUSE; do
  if [ -e "$ROOT/$f" ]; then rm -f "$ROOT/$f"; echo "cleared stale $f"; fi
done

echo "model : $MODEL"
echo "host  : $HOST"
echo "root  : $ROOT"
echo

python3 preflight.py --model "$MODEL" --host "$HOST" --num-ctx "$CTX" || {
  echo; echo "preflight failed — not starting."; exit 1; }

echo
echo "starting. Ctrl-C once to stop cleanly."
echo "watch it from another terminal:  python3 watch.py tools"
echo
exec python3 arena.py --model "$MODEL" --host "$HOST" --root "$ROOT" \
  --num-ctx "$CTX" --keep 40 --max-tool-calls 40 --turns 5000 $STEP

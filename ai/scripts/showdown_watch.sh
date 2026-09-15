#!/usr/bin/env bash
# Watch a PPO run: every 100th iteration checkpoint -> 100 Showdown gen1ou battles vs poke-env
# SimpleHeuristics (competitive team set both sides) -> checkpoints/pep/RUN/showdown.csv.
# usage: scripts/showdown_watch.sh RUN     (needs scripts/showdown_server.sh running)
set -u
source "$(dirname "$0")/env.sh"; cd "$AI_ROOT"
RUN=$1; CSV=$CKPT_ROOT/$RUN/showdown.csv
[ -f "$CSV" ] || echo "iter,heuristic_win" > "$CSV"
while true; do
  for f in $(ls "$CKPT_ROOT/$RUN"/iter-*00.pt 2>/dev/null); do
    it=$(basename "$f" .pt | sed 's/iter-0*//'); grep -q "^$it," "$CSV" && continue
    r=$(uv run python -m tools.showdown_eval --checkpoint "$f" --battles 100 --opponents heuristic --concurrency 4 \
          --team-dir "$METAMON_TEAM_DIR" --username "SW$RUN$it" 2>/dev/null | grep "\[result\]" | awk '{print $5}')
    [ -n "$r" ] && echo "$it,$r" >> "$CSV"
  done
  [ -f "$CKPT_ROOT/$RUN/model.pt" ] && [ "${ONCE:-0}" = 1 ] && break
  sleep 120
done

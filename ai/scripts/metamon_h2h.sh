#!/usr/bin/env bash
# Head-to-head series vs Metamon's pretrained Gen I agents on the local Showdown server,
# competitive gen1ou team set on both sides, 100 battles per agent, fresh usernames per run.
# usage: scripts/metamon_h2h.sh [checkpoint=checkpoints/pep/current/model-fp32.pt] [agents="SmallRL MediumRL SyntheticRLV2"]
#   env: BATTLES=100 TEMP=0.5 (sampling temperature, 0 = argmax) STAMP=<username suffix>
# Results: [result] lines in logs/h2h-<stamp>-<agent>-pep.log; Metamon's own view in logs/h2h-<stamp>-<agent>-mm.log.
set -u
source "$(dirname "$0")/env.sh"; cd "$AI_ROOT"
CK=${1:-$CKPT_ROOT/current/model-fp32.pt}; AGENTS=${2:-"SmallRL MediumRL SyntheticRLV2"}
N=${BATTLES:-100}; T=${TEMP:-0.5}; STAMP=${STAMP:-$(date +%m%d%H%M)}; mkdir -p logs
for A in $AGENTS; do
  U="Pep$STAMP$A"; M="Meta$STAMP$A"
  uv run python -m tools.showdown_eval --checkpoint "$CK" --accept "$M" --username "$U" --battles "$N" \
    --concurrency 1 --temperature "$T" --team-dir "$METAMON_TEAM_DIR" > "logs/h2h-$STAMP-$A-pep.log" 2>&1 &
  ACC=$!; sleep 30
  "$METAMON_VENV/bin/python" serve/metamon_eval.py --agent "$A" --eval_type challenge --username "$M" \
    --opponent_username "$U" --role challenger --gens 1 --formats ou --total_battles "$N" \
    --team_set competitive > "logs/h2h-$STAMP-$A-mm.log" 2>&1
  wait $ACC 2>/dev/null
  echo "== $A: $(grep '\[result\]' "logs/h2h-$STAMP-$A-pep.log" || echo FAILED)"
done
echo "== done"

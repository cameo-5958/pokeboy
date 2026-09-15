#!/usr/bin/env bash
# One league-PPO round of the PEP trainer-seat policy (models/train_ppo.py).
# Defaults are the v3 recipe: OU-mix matchups, opponent pool self 0.4 / past snapshots 0.3 / greedy 0.3,
# potential-based HP shaping 0.3, snapshot every 25 iters (keep 8), lr 5e-5, no KL anchor.
# usage: scripts/train_league.sh RUN INIT_CKPT [extra train_ppo flags]
#   env: ITERS=1500 WORKERS=12 DEVICE=cuda MATCHUPS=ou:0.6,mirror:0.2,balanced:0.2 EVAL_MATCHUPS=ou
# Writes checkpoints/pep/RUN/{train.log,model.pt,iter-NNNNN.pt,league/} and evaluates vs greedy every 25 iters.
set -euo pipefail
source "$(dirname "$0")/env.sh"; cd "$AI_ROOT"
RUN=$1; INIT=$2; shift 2
OUT=$CKPT_ROOT/$RUN; mkdir -p "$OUT"
uv run python -m models.train_ppo --init-ckpt "$INIT" --run "$RUN" \
  --iters "${ITERS:-1500}" --battles-per-iter 256 --workers "${WORKERS:-12}" --device "${DEVICE:-cuda}" \
  --matchups "${MATCHUPS:-ou:0.6,mirror:0.2,balanced:0.2}" --opponents "self:0.4,past:0.3,greedy:0.3" --shaping 0.3 \
  --past-every 25 --past-keep 8 --past-init --lr 5e-5 --kl-init 0 \
  --eval-every 25 --eval-battles 200 --eval-matchups "${EVAL_MATCHUPS:-ou}" "$@" 2>&1 | tee -a "$OUT/train.log"

#!/usr/bin/env bash
# The full training recipe behind checkpoints/pep/current, stage by stage. Each stage is idempotent
# (skipped when its output exists) so the script can be re-run after an interruption.
# usage: scripts/reproduce.sh [stage ...]      stages: data imitation v0 league-data v1 v2 v3 finalize stage
#   env: DEVICE=cuda WORKERS=12
#
# data       depth-1 (20k battles) and depth-2 (10k) search-teacher corpora on the ROM trainer parties
# imitation  PEP imitation of the depth-1 teacher (200k steps), continued on both corpora (150k)
# v0         PPO vs greedy/random from the imitation policy, then finalize -> v0 (pre-league baseline)
# league-data 2,400-battle depth-1 corpus with the league featurizer (QAT distillation/calibration data)
# v1         league round on ROM matchups (mirror/balanced), 2,000 iters, bench damage features reset
# v2         v1 continued for another 2,000 iters (tied with v1 on mirror; kept for the record, not staged)
# v3         league round from v1 on OU-mix matchups (Metamon team pools), 1,500 iters  -> shipping model
# finalize   self-distilled QAT + int8 export + bit-exact check + evals for v3 (scripts/finalize.sh)
# stage      copy v3 into checkpoints/pep/current (scripts/stage.sh)
set -euo pipefail
source "$(dirname "$0")/env.sh"; cd "$AI_ROOT"
D=datasets/trainer; RAW=datasets/raw; R=$CKPT_ROOT; export DEVICE=${DEVICE:-cuda} WORKERS=${WORKERS:-12}
STAGES=${*:-"data imitation v0 league-data v1 v2 v3 finalize stage"}
have() { [ -e "$1" ]; }
log() { echo "[$(date +%H:%M:%S)] $*"; }

for S in $STAGES; do case $S in
data)
  # OU team corpora for the `ou` matchup mode (v3); no-op once pulled
  have $RAW/metamon/jakegrigsby__metamon-teams || uv run python -m data pull --source metamon --only teams
  have $D/v1 || uv run python -m sim.generate_trainer --battles 20000 --out $D/v1 --seed 100 --depth 1 --rolls 2 --rows-per-part 50000
  have $D/v2-d2 || uv run python -m sim.generate_trainer --battles 10000 --out $D/v2-d2 --seed 200 --depth 2 --rolls 2 --rows-per-part 50000
  ;;
imitation)
  mkdir -p $R/archive
  have $R/archive/imit-v2/model.pt || uv run python -m models.train_pep --data "$D/v[12]*/*.parquet" --run archive/imit-v2 \
    --steps 200000 --batch 32 --eval-every 1000 --save-every 2000 --device "$DEVICE"
  have $R/archive/imit-v3/model.pt || uv run python -m models.train_pep --data "$D/v[12]*/*.parquet" --run archive/imit-v3 \
    --init-ckpt $R/archive/imit-v2/step-0064000.pt --steps 150000 --batch 32 --eval-every 1000 --save-every 2000 --device "$DEVICE"
  ;;
v0)
  mkdir -p $R/archive/ppo-v1
  have $R/archive/ppo-v1/model.pt || uv run python -m models.train_ppo --init-ckpt $R/archive/imit-v3/step-0092000.pt \
    --run archive/ppo-v1 --iters 800 --battles-per-iter 512 --opponents greedy,random --workers 8 --device "$DEVICE" \
    --eval-every 10 --lr 2e-5 --kl-init 0.01
  have $R/archive/ppo-v1-sdq/pkai.weights || scripts/finalize.sh $R/archive/ppo-v1/model.pt archive/ppo-v1-sdq $D/v2-d2
  have $R/v0/pkai.weights || { mkdir -p $R/v0; cp $R/archive/ppo-v1/model.pt $R/v0/model-fp32.pt
    cp $R/archive/ppo-v1-sdq/model.pt $R/v0/model-qat.pt; cp $R/archive/ppo-v1-sdq/pkai.weights $R/v0/pkai.weights; }
  ;;
league-data)
  if ! have $D/league-d1; then
    for s in 201 202 203 204; do uv run python -m sim.generate_trainer --battles 600 --depth 1 --seed $s --out $D/league-d1-s$s & done; wait
    mkdir -p $D/league-d1; i=0
    for s in 201 202 203 204; do cp $D/league-d1-s$s/part-00000.parquet $D/league-d1/part-0000$i.parquet; i=$((i+1)); done
  fi
  ;;
v1)
  have $R/v1/model.pt || ITERS=2000 MATCHUPS=mirror:0.5,balanced:0.5 EVAL_MATCHUPS=mirror \
    scripts/train_league.sh v1 $R/v0/model-fp32.pt --reset-feat-cols own:38-41,player:34-37
  have $R/v1-sdq/pkai.weights || scripts/finalize.sh $R/v1/model.pt v1-sdq
  have $R/current-v1/pkai.weights || { mkdir -p $R/current-v1; cp $R/v1/model.pt $R/current-v1/model-fp32.pt
    cp $R/v1-sdq/model.pt $R/current-v1/model-qat.pt; cp $R/v1-sdq/pkai.weights $R/current-v1/pkai.weights; }
  ;;
v2)
  have $R/v2/model.pt || ITERS=2000 WORKERS=10 MATCHUPS=mirror:0.5,balanced:0.5 EVAL_MATCHUPS=mirror \
    scripts/train_league.sh v2 $R/v1/model.pt
  ;;
v3)
  have $R/v3/model.pt || scripts/train_league.sh v3 $R/current-v1/model-fp32.pt
  ;;
finalize)
  have $R/v3-sdq/pkai.weights || scripts/finalize.sh $R/v3/model.pt v3-sdq
  ;;
stage)
  scripts/stage.sh v3 v1
  ;;
*) echo "unknown stage $S"; exit 2;;
esac; log "stage $S done"; done

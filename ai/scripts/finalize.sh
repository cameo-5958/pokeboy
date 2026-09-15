#!/usr/bin/env bash
# Turn a trained fp32 PPO checkpoint into shippable int8 weights and measure the cost:
#   self-distilled QAT (fake-quant fine-tune against the frozen fp32 policy)  -> checkpoints/pep/RUN/model.pt
#   int8 export + reference vectors                                           -> checkpoints/pep/RUN/pkai.weights
#   bit-exactness through the C++ model (pkai_model_tests)
#   600-battle evals on mirror / balanced / ou matchups, fp32 source vs int8 weights through C++
# usage: scripts/finalize.sh checkpoints/pep/v3/model.pt v3-sdq [datasets/trainer/league-d1]
set -euo pipefail
source "$(dirname "$0")/env.sh"; cd "$AI_ROOT"
CK=$1; RUN=$2; DATA=${3:-datasets/trainer/league-d1}; OUT=$CKPT_ROOT/$RUN
uv run python -m models.train_pep --data "$DATA" --run "$RUN" --init-ckpt "$CK" --distill-from "$CK" --fake-quant \
  --steps 3000 --batch 32 --lr 3e-5 --warmup 100 --eval-every 1000 --save-every 0 --device "${DEVICE:-cuda}"
uv run python -m tools.export_weights --checkpoint "$OUT/model.pt" --calib "$DATA" --out "$OUT/pkai.weights" \
  --vectors "$OUT/vectors" --rom-crc32 "$PKAI_ROM_CRC32" | tail -n 3
uv run python -m tools.dump_vectors "$OUT/vectors" "$OUT/vectors/bin"
[ -x "$PKAI_BUILD/pkai_model_tests" ] || scripts/build_pkai.sh
"$PKAI_BUILD/pkai_model_tests" "$OUT/pkai.weights" "$OUT/vectors/bin" | tail -n 1
for m in mirror balanced ou; do
  echo "== $m 600 seed 7: fp32 source, then int8 export through C++"
  uv run python -m tools.eval_trainer --checkpoint "$CK" --int-weights "$OUT/pkai.weights" --battles 600 --seed 7 \
    --no-teacher --matchups "$m" | grep -E "^(pep|int) "
done
echo "== done: $OUT"

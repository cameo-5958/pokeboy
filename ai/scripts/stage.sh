#!/usr/bin/env bash
# Stage a finalised version as the shipping model:
#   checkpoints/pep/current/{model-fp32.pt,model-qat.pt,pkai.weights,vectors/bin}
# The reference vectors travel with the weights so pkai_model_tests can verify the shipped
# model bit-for-bit straight out of the staged directory (that is PKAI_CHECKPOINT's default).
# The previous contents move to checkpoints/pep/current-<prev> when a name is given.
# usage: scripts/stage.sh v3 [prev-name]        (expects checkpoints/pep/v3/model.pt and v3-sdq/{model.pt,pkai.weights})
set -euo pipefail
source "$(dirname "$0")/env.sh"
V=$1; PREV=${2:-}; CUR=$CKPT_ROOT/current
# Already staged: do nothing. Re-running must never overwrite the kept previous version
# with a copy of the one that is live (the backup would then be the same model twice).
if cmp -s "$CUR/pkai.weights" "$CKPT_ROOT/$V-sdq/pkai.weights"; then
  echo "$V is already staged as current; nothing to do"; exit 0
fi
if [ -n "$PREV" ] && [ -d "$CUR" ]; then
  [ -e "$CKPT_ROOT/current-$PREV" ] && { echo "refusing to overwrite $CKPT_ROOT/current-$PREV" >&2; exit 1; }
  cp -r "$CUR" "$CKPT_ROOT/current-$PREV"
fi
mkdir -p "$CUR"
cp "$CKPT_ROOT/$V/model.pt" "$CUR/model-fp32.pt"
cp "$CKPT_ROOT/$V-sdq/model.pt" "$CUR/model-qat.pt"
cp "$CKPT_ROOT/$V-sdq/pkai.weights" "$CUR/pkai.weights"
if [ -d "$CKPT_ROOT/$V-sdq/vectors/bin" ]; then
  rm -rf "$CUR/vectors"; mkdir -p "$CUR/vectors"; cp -r "$CKPT_ROOT/$V-sdq/vectors/bin" "$CUR/vectors/bin"
fi
ls -l "$CUR"; echo "staged $V as current${PREV:+ (previous kept as current-$PREV)}"

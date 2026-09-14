#!/usr/bin/env bash
# Stage a finalised version as the shipping model: checkpoints/pep/current/{model-fp32.pt,model-qat.pt,pkai.weights}.
# The previous contents move to checkpoints/pep/current-<prev> when a name is given.
# usage: scripts/stage.sh v3 [prev-name]        (expects checkpoints/pep/v3/model.pt and v3-sdq/{model.pt,pkai.weights})
set -euo pipefail
source "$(dirname "$0")/env.sh"
V=$1; PREV=${2:-}; CUR=$CKPT_ROOT/current
if [ -n "$PREV" ] && [ -d "$CUR" ]; then rm -rf "$CKPT_ROOT/current-$PREV"; cp -r "$CUR" "$CKPT_ROOT/current-$PREV"; fi
mkdir -p "$CUR"
cp "$CKPT_ROOT/$V/model.pt" "$CUR/model-fp32.pt"
cp "$CKPT_ROOT/$V-sdq/model.pt" "$CUR/model-qat.pt"
cp "$CKPT_ROOT/$V-sdq/pkai.weights" "$CUR/pkai.weights"
ls -l "$CUR"; echo "staged $V as current${PREV:+ (previous kept as current-$PREV)}"

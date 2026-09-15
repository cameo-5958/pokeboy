#!/bin/sh
set -eu
cd "$(dirname "$0")"
python3 tools/generate_ai.py --items
make -j8 pokered.gbc
cp pokered.gbc pokered-ai.gbc
cp pokered.sym pokered-ai.sym
python3 tools/generate_ai.py

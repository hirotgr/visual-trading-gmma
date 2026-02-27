#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

exec python3 run_gmma_report.py \
  --image ./capture.png \
  --log ./trend-report.log \
  --state ./state/gmma-state.json \
  --interval-seconds 300


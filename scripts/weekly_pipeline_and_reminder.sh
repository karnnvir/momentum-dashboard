#!/bin/bash
# Runs the weekly pipeline, then the catalyst-research reminder - chained
# rather than scheduled with a fixed time offset, so the reminder only
# fires once that week's run has actually finished (the pipeline's own
# runtime varies a lot depending on whether fundamentals/analyst-consensus
# happen to be stale that week).
set -e

PROJECT_DIR="/Users/k0s02hc/Claude/Projects/Momentum Stocks"
PYTHON="$PROJECT_DIR/.venv/bin/python"

cd "$PROJECT_DIR/src"
export PYTHONPATH="$PROJECT_DIR/src"

"$PYTHON" -m momentum.pipeline
"$PYTHON" -m momentum.weekly_reminder

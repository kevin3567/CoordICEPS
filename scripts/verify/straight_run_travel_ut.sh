#!/usr/bin/env bash
set -euo pipefail

# Travel user-task straight runner. No attack, no defense.
# Override config with environment variables, e.g.:
#   MODEL=DEEPSEEK_V4_FLASH FORCE_RERUN=1 ./scripts/straight_run_travel_ut.sh
#   USER_TASK_IDS="user_task_0 user_task_3" ./scripts/straight_run_travel_ut.sh

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="${PROJECT_DIR:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
BENCHMARK_VERSION="${BENCHMARK_VERSION:-v0}"
MODEL="${MODEL:-GLM_5_2}"
MODEL_ID="${MODEL_ID:-}"
USER_TASK_IDS="${USER_TASK_IDS:-}"
FORCE_RERUN="${FORCE_RERUN:-1}"
LOGDIR="${LOGDIR:-$PROJECT_DIR/runs/straight_user_tasks}"
LOG_DIR="${LOG_DIR:-$PROJECT_DIR/logs}"
TOOL_DELIMITER="${TOOL_DELIMITER:-tool}"

mkdir -p "$LOG_DIR"
export PYTHONPATH="$PROJECT_DIR/src${PYTHONPATH:+:$PYTHONPATH}"
echo "PYTHONPATH=$PYTHONPATH"
cd "$PROJECT_DIR"

cmd=(
  python -m agentdojo.verifications.check_user_tasks
  --benchmark-version "$BENCHMARK_VERSION"
  --suite travel
  --model "$MODEL"
  --logdir "$LOGDIR"
  --tool-delimiter "$TOOL_DELIMITER"
)

if [[ -n "$MODEL_ID" ]]; then
  cmd+=(--model-id "$MODEL_ID")
fi
if [[ "$FORCE_RERUN" == "1" || "$FORCE_RERUN" == "true" || "$FORCE_RERUN" == "True" ]]; then
  cmd+=(--force-rerun)
fi
if [[ -n "$USER_TASK_IDS" ]]; then
  read -r -a user_ids <<< "$USER_TASK_IDS"
  cmd+=(--user-task-ids "${user_ids[@]}")
fi

safe_model="$(printf '%s' "$MODEL" | tr '/.' '__')"
log_file="$LOG_DIR/lstraight_travel_ut_${safe_model}.log"

echo "============================================================"
echo "Running Travel user tasks without attack/defense"
echo "  project_dir=$PROJECT_DIR"
echo "  benchmark_version=$BENCHMARK_VERSION"
echo "  model=$MODEL"
echo "  force_rerun=$FORCE_RERUN"
echo "  tasks=$USER_TASK_IDS"
echo "  logdir=$LOGDIR"
echo "  log=$log_file"
echo "============================================================"
"${cmd[@]}" 2>&1 | tee "$log_file"

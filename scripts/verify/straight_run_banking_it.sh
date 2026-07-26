#!/usr/bin/env bash
set -euo pipefail

# Banking injection-task straight runner. No attack, no defense.
# Override config with environment variables, e.g.:
#   MODEL=DEEPSEEK_V4_FLASH FORCE_RERUN=1 ./scripts/straight_run_banking_it.sh
#   INJECTION_TASK_IDS="injection_task_0 injection_task_3" ./scripts/straight_run_banking_it.sh

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="${PROJECT_DIR:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
BENCHMARK_VERSION="${BENCHMARK_VERSION:-v0}"
MODEL="${MODEL:-GLM_5_2}"
MODEL_ID="${MODEL_ID:-}"
INJECTION_TASK_IDS="${INJECTION_TASK_IDS:-}"
FORCE_RERUN="${FORCE_RERUN:-1}"
LOGDIR="${LOGDIR:-$PROJECT_DIR/runs/straight_injection_tasks}"
LOG_DIR="${LOG_DIR:-$PROJECT_DIR/logs}"
TOOL_DELIMITER="${TOOL_DELIMITER:-tool}"

mkdir -p "$LOG_DIR"
export PYTHONPATH="$PROJECT_DIR/src${PYTHONPATH:+:$PYTHONPATH}"
echo "PYTHONPATH=$PYTHONPATH"
cd "$PROJECT_DIR"

cmd=(
  python -m agentdojo.verifications.check_injection_tasks
  --benchmark-version "$BENCHMARK_VERSION"
  --suite banking
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
if [[ -n "$INJECTION_TASK_IDS" ]]; then
  read -r -a injection_ids <<< "$INJECTION_TASK_IDS"
  cmd+=(--injection-task-ids "${injection_ids[@]}")
fi

safe_model="$(printf '%s' "$MODEL" | tr '/.' '__')"
log_file="$LOG_DIR/lstraight_banking_it_${safe_model}.log"

echo "============================================================"
echo "Running Banking injection tasks as standalone prompts"
echo "  project_dir=$PROJECT_DIR"
echo "  benchmark_version=$BENCHMARK_VERSION"
echo "  model=$MODEL"
echo "  force_rerun=$FORCE_RERUN"
echo "  tasks=$INJECTION_TASK_IDS"
echo "  logdir=$LOGDIR"
echo "  log=$log_file"
echo "============================================================"
"${cmd[@]}" 2>&1 | tee "$log_file"

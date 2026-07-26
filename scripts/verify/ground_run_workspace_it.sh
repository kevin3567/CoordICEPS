#!/usr/bin/env bash
set -euo pipefail

# Workspace injection-task ground-truth replay + answer checker. No attack, no defense.
# Override config with environment variables, e.g.:
#   MODEL=DEEPSEEK_V4_FLASH INJECTION_TASK_IDS="injection_task_0 injection_task_3" ./scripts/ground_run_workspace_it.sh

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="${PROJECT_DIR:-$(cd "$SCRIPT_DIR/../.." && pwd)}"
BENCHMARK_VERSION="${BENCHMARK_VERSION:-v0}"
#MODEL="${MODEL:-DEEPSEEK_V4_FLASH}"
MODEL="${MODEL:-GLM_5_2}"
MODEL_ID="${MODEL_ID:-}"
INJECTION_TASK_IDS="${INJECTION_TASK_IDS:-}"
LOG_DIR="${LOG_DIR:-$PROJECT_DIR/logs}"
TOOL_DELIMITER="${TOOL_DELIMITER:-tool}"

mkdir -p "$LOG_DIR"
export PYTHONPATH="$PROJECT_DIR/src${PYTHONPATH:+:$PYTHONPATH}"
echo "PYTHONPATH=$PYTHONPATH"
cd "$PROJECT_DIR"

cmd=(
  python -m agentdojo.verifications.check_injection_tasks_gt
  --benchmark-version "$BENCHMARK_VERSION"
  --suite workspace
  --model "$MODEL"
  --tool-delimiter "$TOOL_DELIMITER"
)

if [[ -n "$MODEL_ID" ]]; then
  cmd+=(--model-id "$MODEL_ID")
fi
if [[ -n "$INJECTION_TASK_IDS" ]]; then
  read -r -a injection_ids <<< "$INJECTION_TASK_IDS"
  cmd+=(--injection-task-ids "${injection_ids[@]}")
fi

safe_model="$(printf '%s' "$MODEL" | tr '/.' '__')"
log_file="$LOG_DIR/lground_workspace_it_${safe_model}.log"

echo "============================================================"
echo "Running Workspace injection-task ground-truth replay + answer checks"
echo "  project_dir=$PROJECT_DIR"
echo "  benchmark_version=$BENCHMARK_VERSION"
echo "  model=$MODEL"
echo "  tasks=$INJECTION_TASK_IDS"
echo "  log=$log_file"
echo "============================================================"
"${cmd[@]}" 2>&1 | tee "$log_file"

#!/usr/bin/env bash
set -euo pipefail

# Usage examples:
#   ./scripts/run_important_instructions.sh
#   SUITES="travel banking slack workspace" MAX_JOBS=2 ./scripts/run_important_instructions.sh
#   FORCE_RERUN=1 ATTACK=tool_knowledge USER_TASK_IDS="user_task_1 user_task_5" INJECTION_TASK_IDS="injection_task_0 injection_task_4" ./scripts/run_important_instructions.sh
#
# Positional args:
#   $1 = suites (space-separated)
#   $2 = benchmark version
#   $3 = agent model
#   $4 = attack name

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
if [[ -d "$ROOT_DIR/src/agentdojo" ]]; then
  DEFAULT_PROJECT_DIR="$ROOT_DIR"
else
  DEFAULT_PROJECT_DIR="$ROOT_DIR/Task-Injection/agentdojo"
fi
PROJECT_DIR="${PROJECT_DIR:-$DEFAULT_PROJECT_DIR}"
LOG_DIR="$ROOT_DIR/logs"
mkdir -p "$LOG_DIR"

export PYTHONPATH="$PROJECT_DIR/src${PYTHONPATH:+:$PYTHONPATH}"
echo ${PYTHONPATH}

SUITES="${1:-${SUITES:-banking}}"
BENCHMARK_VERSION="${2:-${BENCHMARK_VERSION:-v0}}"
AGENT_MODEL="${3:-${AGENT_MODEL:-z-ai/glm-5.2}}"
ATTACK="${4:-${ATTACK:-important_instructions}}"
DEFENSE="${DEFENSE:-}"
MAX_JOBS="${MAX_JOBS:-1}"
FORCE_RERUN="${FORCE_RERUN:-0}"
RUN_LOGDIR="${RUN_LOGDIR:-runs}"
USER_TASK_IDS="${USER_TASK_IDS:-}"
INJECTION_TASK_IDS="${INJECTION_TASK_IDS:-}"
SYSTEM_MESSAGE_NAME="${SYSTEM_MESSAGE_NAME:-}"
TOOL_DELIMITER="${TOOL_DELIMITER:-tool}"

cd "$PROJECT_DIR"

resolve_benchmark_model() {
  python3 - "$1" <<'PY2'
import sys
from agentdojo.models import ModelsEnum

raw = sys.argv[1]
if raw in ModelsEnum.__members__:
    print(raw)
    raise SystemExit(0)

for name, member in ModelsEnum.__members__.items():
    if member.value == raw:
        print(name)
        raise SystemExit(0)

raise SystemExit(f"Unknown benchmark model: {raw}")
PY2
}

BENCHMARK_MODEL="$(resolve_benchmark_model "$AGENT_MODEL")"

running_pids=()

cleanup_finished_jobs() {
  local pid
  local still_running=()

  for pid in "${running_pids[@]}"; do
    if kill -0 "$pid" 2>/dev/null; then
      still_running+=("$pid")
    fi
  done

  running_pids=("${still_running[@]}")
}

wait_for_job_slot() {
  while true; do
    cleanup_finished_jobs
    if ((${#running_pids[@]} < MAX_JOBS)); then
      return 0
    fi
    wait -n || true
  done
}

resolve_task_ids_for_suite() {
  local suite="$1"
  python3 - "$suite" "$BENCHMARK_VERSION" <<'PY'
import sys
from agentdojo.task_suite.load_suites import get_suite

suite_name = sys.argv[1]
benchmark_version = sys.argv[2]
suite = get_suite(benchmark_version, suite_name)
print(" ".join(sorted(suite.user_tasks.keys())))
print(" ".join(sorted(suite.injection_tasks.keys())))
PY
}

launch_suite() {
  local suite="$1"
  local user_ids_raw="$USER_TASK_IDS"
  local injection_ids_raw="$INJECTION_TASK_IDS"
  local resolved
  local -a user_ids
  local -a injection_ids
  local -a cmd
  local log_file
  local pid

  if [[ -z "$user_ids_raw" || -z "$injection_ids_raw" ]]; then
    resolved="$(resolve_task_ids_for_suite "$suite")"
    if [[ -z "$user_ids_raw" ]]; then
      user_ids_raw="$(printf '%s\n' "$resolved" | sed -n '1p')"
    fi
    if [[ -z "$injection_ids_raw" ]]; then
      injection_ids_raw="$(printf '%s\n' "$resolved" | sed -n '2p')"
    fi
  fi

  read -r -a user_ids <<< "$user_ids_raw"
  read -r -a injection_ids <<< "$injection_ids_raw"

  if ((${#user_ids[@]} == 0)); then
    echo "No user task ids resolved for suite=$suite" >&2
    return 1
  fi
  if ((${#injection_ids[@]} == 0)); then
    echo "No injection task ids resolved for suite=$suite" >&2
    return 1
  fi

  local defense_tag="${DEFENSE:-nodefense}"
  log_file="$LOG_DIR/lrun_important_instructions_$(printf '%s' "$AGENT_MODEL" | tr '/.' '__')_${ATTACK}_${defense_tag}_${suite}.log"

  cmd=(
    python src/agentdojo/scripts/benchmark.py
    --suite "$suite"
    --model "$BENCHMARK_MODEL"
    --benchmark-version "$BENCHMARK_VERSION"
    --attack "$ATTACK"
    --logdir "$RUN_LOGDIR"
    --tool-delimiter "$TOOL_DELIMITER"
  )

  local user_id
  for user_id in "${user_ids[@]}"; do
    cmd+=(--user-task "$user_id")
  done

  local injection_id
  for injection_id in "${injection_ids[@]}"; do
    cmd+=(--injection-task "$injection_id")
  done

  if [[ -n "$DEFENSE" ]]; then
    cmd+=(--defense "$DEFENSE")
  fi
  if [[ -n "$SYSTEM_MESSAGE_NAME" ]]; then
    cmd+=(--system-message-name "$SYSTEM_MESSAGE_NAME")
  fi
  if [[ "$FORCE_RERUN" == "1" || "$FORCE_RERUN" == "true" || "$FORCE_RERUN" == "True" ]]; then
    cmd+=(--force-rerun)
  fi

  echo "============================================================"
  echo "Launching important-instructions suite=$suite"
  echo "  benchmark_version=$BENCHMARK_VERSION"
  echo "  agent_model=$AGENT_MODEL"
  echo "  benchmark_model=$BENCHMARK_MODEL"
  echo "  attack=$ATTACK"
  echo "  defense=${DEFENSE:-none}"
  echo "  force_rerun=$FORCE_RERUN"
  echo "  user_tasks=${#user_ids[@]} injection_tasks=${#injection_ids[@]}"
  echo "  run_logdir=$RUN_LOGDIR"
  echo "  log=$log_file"
  echo "============================================================"

  nohup "${cmd[@]}" >> "$log_file" 2>&1 &
  pid=$!
  running_pids+=("$pid")
  echo "Started suite=$suite pid=$pid"
}

for suite in $SUITES; do
  wait_for_job_slot
  launch_suite "$suite"
done

while ((${#running_pids[@]} > 0)); do
  wait -n || true
  cleanup_finished_jobs
done

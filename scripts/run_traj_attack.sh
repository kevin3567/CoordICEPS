#!/usr/bin/env bash
set -euo pipefail
sleep 0m
# Usage examples:
#   ./scripts/run_traj_attack.sh
#   SUITES="slack banking" REWRITE_STRATEGIES="induced_dependency__coord_by_handoff induced_priority__coord_by_breakdown" ./scripts/run_traj_attack.sh
#   FORCE_RUN=1 MAX_WORKERS=2 MAX_REWRITES=1 MAX_JOBS=3 ./scripts/run_traj_attack.sh
#   USER_TASK_IDS="user_task_1 user_task_5" INJECTION_TASK_IDS="injection_task_0 injection_task_4" ./scripts/run_traj_attack.sh
#
# Positional args:
#   $1 = suites (space-separated)
#   $2 = benchmark version
#   $3 = agent model
#   $4 = attack model

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
echo "$PYTHONPATH"

SUITES="${1:-${SUITES:-banking}}"
BENCHMARK_VERSION="${2:-${BENCHMARK_VERSION:-v0}}"
AGENT_MODEL="${3:-${AGENT_MODEL:-z-ai/glm-5.2}}"
#AGENT_MODEL="${3:-${AGENT_MODEL:-google/gemma-4-31b-it}}"
#AGENT_MODEL="${3:-${AGENT_MODEL:-qwen/qwen3.6-27b}}"
#AGENT_MODEL="${3:-${AGENT_MODEL:-deepseek/deepseek-v4-pro}}"
#AGENT_MODEL="${3:-${AGENT_MODEL:-moonshotai/kimi-k2.6}}"
#AGENT_MODEL="${3:-${AGENT_MODEL:-minimax/minimax-m3}}"
ATTACK_MODEL="${4:-${ATTACK_MODEL:-deepseek/deepseek-v4-pro}}"
ATTACK_METHOD="${ATTACK_METHOD:-generated_trajectory}"
REWRITE_STRATEGIES="${REWRITE_STRATEGIES:-induced_dependency__coord_by_repeat induced_dependency__coord_by_handoff induced_dependency__coord_by_breakdown induced_priority__coord_by_repeat induced_priority__coord_by_handoff induced_priority__coord_by_breakdown}"
#DEFENSE="${DEFENSE:-promptarmor}"
DEFENSE="${DEFENSE:-}"
MAX_WORKERS="${MAX_WORKERS:-2}"
MAX_REWRITES="${MAX_REWRITES:-3}"
MAX_JOBS="${MAX_JOBS:-6}"
GENERATION_MAX_CONCURRENT="${GENERATION_MAX_CONCURRENT:-3}"
RUN_PARALLEL="${RUN_PARALLEL:-1}"
FAIL_FAST="${FAIL_FAST:-0}"
FORCE_RUN="${FORCE_RUN:-0}"
OUT_DIR="${OUT_DIR:-res}"
AGENT_LOGDIR="${AGENT_LOGDIR:-runs}"
USER_TASK_IDS="${USER_TASK_IDS:-}"
INJECTION_TASK_IDS="${INJECTION_TASK_IDS:-}"

cd "$PROJECT_DIR"

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

launch_suite_strategy() {
  local suite="$1"
  local rewrite_strategy="$2"
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
  log_file="$LOG_DIR/lrun_traj_attack_$(printf '%s' "$ATTACK_MODEL" | tr '/.' '__')_to_$(printf '%s' "$AGENT_MODEL" | tr '/.' '__')_${rewrite_strategy}_${defense_tag}_${suite}.log"

  cmd=(
    python -u src/agentdojo/attacks/traj_attack/traj_attack_pipeline.py
    --suite "$suite"
    --benchmark-version "$BENCHMARK_VERSION"
    --agent-model-name "$AGENT_MODEL"
    --attack-model-name "$ATTACK_MODEL"
    --attack-method "$ATTACK_METHOD"
    --rewrite-strategy "$rewrite_strategy"
    --max-workers "$MAX_WORKERS"
    --max-rewrites "$MAX_REWRITES"
    --generation-max-concurrent "$GENERATION_MAX_CONCURRENT"
    --out-dir "$OUT_DIR"
    --agent-logdir "$AGENT_LOGDIR"
    --user-task-id
    "${user_ids[@]}"
    --injection-task-id
    "${injection_ids[@]}"
  )

  if [[ -n "$DEFENSE" ]]; then
    cmd+=(--defense "$DEFENSE")
  fi
  if [[ "$RUN_PARALLEL" == "1" || "$RUN_PARALLEL" == "true" || "$RUN_PARALLEL" == "True" ]]; then
    cmd+=(--run-parallel)
  fi
  if [[ "$FAIL_FAST" == "1" || "$FAIL_FAST" == "true" || "$FAIL_FAST" == "True" ]]; then
    cmd+=(--fail-fast)
  fi
  if [[ "$FORCE_RUN" == "1" || "$FORCE_RUN" == "true" || "$FORCE_RUN" == "True" ]]; then
    cmd+=(--force-run)
  fi

  echo "============================================================"
  echo "Launching trajectory attack suite=$suite strategy=$rewrite_strategy"
  echo "  benchmark_version=$BENCHMARK_VERSION"
  echo "  agent_model=$AGENT_MODEL"
  echo "  attack_model=$ATTACK_MODEL"
  echo "  attack_method=$ATTACK_METHOD"
  echo "  defense=${DEFENSE:-none}"
  echo "  max_workers=$MAX_WORKERS"
  echo "  max_rewrites=$MAX_REWRITES"
  echo "  generation_max_concurrent=$GENERATION_MAX_CONCURRENT"
  echo "  max_jobs=$MAX_JOBS"
  echo "  run_parallel=$RUN_PARALLEL"
  echo "  fail_fast=$FAIL_FAST"
  echo "  force_run=$FORCE_RUN"
  echo "  user_tasks=${#user_ids[@]} injection_tasks=${#injection_ids[@]}"
  echo "  log=$log_file"
  echo "============================================================"

  nohup "${cmd[@]}" >> "$log_file" 2>&1 &
  pid=$!
  running_pids+=("$pid")
  echo "Started suite=$suite strategy=$rewrite_strategy pid=$pid"
}

for suite in $SUITES; do
  for rewrite_strategy in $REWRITE_STRATEGIES; do
    wait_for_job_slot
    launch_suite_strategy "$suite" "$rewrite_strategy"
  done
done

while ((${#running_pids[@]} > 0)); do
  wait -n || true
  cleanup_finished_jobs
done

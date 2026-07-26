#!/usr/bin/env bash
set -euo pipefail

# Usage:
#   ./scripts/clone_and_run.sh
#   RUN_MODE=traj_attack SUITES="slack travel" ./scripts/clone_and_run.sh
#   AGENT_MODEL="z-ai/glm-5.2" ATTACK_MODEL="deepseek/deepseek-v4-pro" ./scripts/clone_and_run.sh
#   RUN_PREFIX=smallrun ./scripts/clone_and_run.sh

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
if [[ -d "$ROOT_DIR/src/agentdojo" ]]; then
  SOURCE_AGENTDOJO_DIR="$ROOT_DIR"
else
  SOURCE_AGENTDOJO_DIR="$ROOT_DIR/Task-Injection/agentdojo"
fi
RUN_MODE="${1:-${RUN_MODE:-traj_attack}}"
STAMP="$(date -u +%Y-%m-%d-%H-%M-%S)"
RUN_PREFIX="${RUN_PREFIX:-}"

resolve_launcher() {
  case "$1" in
    traj_attack)
      echo "run_traj_attack.sh"
      ;;
    important_instructions)
      echo "run_important_instructions.sh"
      ;;
    *)
      echo "Unknown run mode: $1" >&2
      return 1
      ;;
  esac
}

RUN_SCRIPT_NAME="$(resolve_launcher "$RUN_MODE")"
if [[ -n "$RUN_PREFIX" ]]; then
  SAFE_RUN_PREFIX="$(printf '%s' "$RUN_PREFIX" | tr -c '[:alnum:]_.-' '_')_"
else
  SAFE_RUN_PREFIX=""
fi
CLONE_ROOT="$HOME/${SAFE_RUN_PREFIX}TaskInjection_${RUN_MODE}_${STAMP}"
CLONE_AGENTDOJO_DIR="$CLONE_ROOT/agentdojo"

mkdir -p "$CLONE_ROOT"

rsync -a \
  --exclude '*.log' \
  --exclude '*.logs' \
  --exclude 'cached_results/' \
  --exclude 'logs/' \
  --exclude 'runs/' \
  --include 'res/' \
  --include 'res/traj_attack_examples_joint/' \
  --include 'res/traj_attack_examples_joint/***' \
  --exclude 'res/*' \
  "$SOURCE_AGENTDOJO_DIR/" "$CLONE_AGENTDOJO_DIR/"

chmod +x "$CLONE_AGENTDOJO_DIR/scripts/$RUN_SCRIPT_NAME"
mkdir -p "$CLONE_AGENTDOJO_DIR/logs"
CLONE_LOG="$CLONE_AGENTDOJO_DIR/logs/lclone_and_run.log"

cat <<INFO
============================================================
Cloned experiment root: $CLONE_ROOT
Agentdojo dir:          $CLONE_AGENTDOJO_DIR
Run mode:               $RUN_MODE
Using launcher:         $CLONE_AGENTDOJO_DIR/scripts/$RUN_SCRIPT_NAME
Agent model:            ${AGENT_MODEL:-run_traj_attack.sh default}
Attack model:           ${ATTACK_MODEL:-run_traj_attack.sh default}
Clone log:              $CLONE_LOG
============================================================
INFO

nohup env PROJECT_DIR="$CLONE_AGENTDOJO_DIR" bash -lc "cd '$CLONE_AGENTDOJO_DIR' && './scripts/$RUN_SCRIPT_NAME'" > "$CLONE_LOG" 2>&1 &
LAUNCH_PID=$!

echo "Started clone run in background: pid=$LAUNCH_PID"
echo "Follow progress in: $CLONE_LOG"

#!/usr/bin/env bash
set -euo pipefail

# Sequentially apply one generated trajectory artifact set to several victim models.
#
# Usage:
#   ./scripts/apply_traj_attack_models.sh
#   SUITES="slack workspace" ./scripts/apply_traj_attack_models.sh
#   REWRITE_STRATEGIES="induced_dependency__coord_by_handoff" ./scripts/apply_traj_attack_models.sh
#
# All options accepted by apply_traj_attack.sh can be passed through as env vars.

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"

MODELS="${MODELS:-gpt-5.5 qwen/qwen3.6-27b deepseek/deepseek-v4-pro z-ai/glm-5.2 moonshotai/kimi-k2.6 minimax/minimax-m3}"
#MODELS="${MODELS:-gpt-5.5 z-ai/glm-5.2 moonshotai/kimi-k2.6 minimax/minimax-m3 qwen/qwen3.6-27b deepseek/deepseek-v4-pro}"

for model in $MODELS; do
  echo "============================================================"
  echo "Applying trajectory artifacts to AGENT_MODEL=$model"
  echo "============================================================"
  AGENT_MODEL="$model" bash ./scripts/apply_traj_attack.sh "$@"
done

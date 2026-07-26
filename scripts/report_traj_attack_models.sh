#!/usr/bin/env bash
set -euo pipefail

# Sequentially report trajectory-attack metrics for the victim models used by
# apply_traj_attack_models.sh.
#
# Usage:
#   ./scripts/report_traj_attack_models.sh
#   SUITES="slack workspace" ./scripts/report_traj_attack_models.sh
#   REWRITE_STRATEGY="induced_dependency__coord_by_handoff" ./scripts/report_traj_attack_models.sh

CODE_ROOT="${CODE_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
ROOT_DIR="${ROOT_DIR:-$CODE_ROOT}"
cd "$CODE_ROOT"

MODELS="${MODELS:-gpt-5.5 qwen/qwen3.6-27b deepseek/deepseek-v4-pro z-ai/glm-5.2 moonshotai/kimi-k2.6 minimax/minimax-m3}"
SUITES="${SUITES:-all}"
REWRITE_STRATEGY="${REWRITE_STRATEGY:-all}"
ARTIFACT_ATTACK_MODEL="${ARTIFACT_ATTACK_MODEL:-deepseek/deepseek-v4-pro}"
ARTIFACT_AGENT_MODEL="${ARTIFACT_AGENT_MODEL:-z-ai/glm-5.2}"
ARTIFACT_DEFENSE="${ARTIFACT_DEFENSE:-all}"
DEFENSE="${DEFENSE:-all}"
MODE="${MODE:-applied}"
OUTPUT_CSV="${OUTPUT_CSV:-}"

for suite in $SUITES; do
for model in $MODELS; do
  echo "============================================================"
  echo "Reporting trajectory artifacts for SUITE=$suite EVAL_AGENT_MODEL=$model"
  echo "============================================================"

  cmd=(
    python3 -m agentdojo.attacks.traj_attack.traj_attack_report
    --root-dir "$ROOT_DIR"
    --mode "$MODE"
    --suite "$suite"
    --rewrite-strategy "$REWRITE_STRATEGY"
    --attack-model "$ARTIFACT_ATTACK_MODEL"
    --artifact-agent-model "$ARTIFACT_AGENT_MODEL"
    --eval-agent-model "$model"
    --artifact-defense "$ARTIFACT_DEFENSE"
    --defense "$DEFENSE"
  )

  if [[ -n "$OUTPUT_CSV" ]]; then
    suite_tag="$(printf '%s' "$suite" | tr '/.' '__')"
    model_tag="$(printf '%s' "$model" | tr '/.' '__')"
    cmd+=(--output-csv "${OUTPUT_CSV%.csv}_${suite_tag}_${model_tag}.csv")
  fi

  PYTHONPATH="$CODE_ROOT/src" "${cmd[@]}"
done
done

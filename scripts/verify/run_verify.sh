#!/usr/bin/env bash
set -euo pipefail

SUITE="$1"

nohup bash "scripts/verify/ground_run_${SUITE}_ut.sh" >/dev/null 2>&1 &
nohup bash "scripts/verify/ground_run_${SUITE}_it.sh" >/dev/null 2>&1 &
nohup bash "scripts/verify/straight_run_${SUITE}_ut.sh" >/dev/null 2>&1 &
nohup bash "scripts/verify/straight_run_${SUITE}_it.sh" >/dev/null 2>&1 &

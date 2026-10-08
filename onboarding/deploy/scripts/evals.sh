#!/usr/bin/env bash
# Onboarding: diagnosis evals (SC-005) against real Claude Haiku on Bedrock with your AWS credentials.
#   ./evals.sh [--gate] [--estimate] [--force] [--runs 3] [--cases F1,F4] [--mode text|screenshot|both] [-v]
# Paid: every run calls Bedrock; it prints the estimate first (Constitution IV, research R29).
source "$(dirname "$0")/lib.sh"
cd "$ONB_ROOT/agent"
AWS_REGION="${AWS_REGION:-ap-southeast-1}" PYTHONPATH=src \
  exec uv run -q --group evals python tests/evals/run_evals.py "$@"

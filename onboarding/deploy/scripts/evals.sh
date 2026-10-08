#!/usr/bin/env bash
# Onboarding: diagnosis evals (SC-005) against real Claude Haiku on Bedrock with your AWS credentials.
#   ./evals.sh [--runs 10] [--cases F1,F4] [--mode text|screenshot|both] [-v]
source "$(dirname "$0")/lib.sh"
cd "$ONB_ROOT/agent"
AWS_REGION="${AWS_REGION:-ap-southeast-1}" PYTHONPATH=src \
  exec uv run -q --group evals python tests/evals/run_evals.py "$@"

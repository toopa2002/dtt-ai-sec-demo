# Shared helpers for onboarding deploy scripts. Source from other scripts.
source "$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)/scripts/lib.sh"
ONB_ROOT="$REPO_ROOT/onboarding"
ONB_NS=onboarding
ONB_RUNTIME_NAME=isc_onboarding_agent
ONB_API_USER=onboarding-api

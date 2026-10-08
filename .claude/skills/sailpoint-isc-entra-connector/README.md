# sailpoint-isc-entra-connector

Claude skill (and two standalone scripts) to onboard **Microsoft Entra ID** into **SailPoint Identity Security
Cloud** with the **Microsoft Entra SaaS** connector (no virtual appliance).

```
scripts/entra-setup.sh   Entra side, Azure CLI: app registration, service principal, Graph/EXO permissions,
                         admin consent, client secret (chmod 600 file), directory roles, Exchange cert, Azure RBAC
scripts/isc-source.sh    ISC side, REST API v2026: discover, create, configure, verify (peek -> test ->
                         aggregate entitlements, accounts, datasets), dataset-schedule, provisioning-policy,
                         correlation, schedule
assets/entra/            app manifest, permission profiles, grant/role-assignment bodies, RBAC
assets/isc/              source create body, configure JSON-Patch, per-profile toggles, schedule body
references/              templates index, permissions rationale, ISC API notes, troubleshooting
```

Both scripts are dry-run by default; `--apply` performs writes. `--plan-only` (both scripts) makes no calls and needs
no Azure login or ISC token.

Quick start (from the repo root):

```bash
S=.claude/skills/sailpoint-isc-entra-connector/scripts
az login --tenant contoso.onmicrosoft.com --allow-no-subscriptions      # Global Admin / Privileged Role Admin
$S/entra-setup.sh --profiles readonly                                    # review
$S/entra-setup.sh --profiles readonly --apply                            # -> ./sailpoint-entra-setup.json

set -a; . ~/.config/sailpoint-isc.env; set +a                            # ISC_TENANT, ISC_CLIENT_ID, ISC_CLIENT_SECRET
$S/isc-source.sh discover
$S/isc-source.sh create --name "Entra ID - Contoso" --owner me --apply
$S/isc-source.sh configure --source "Entra ID - Contoso" --from-setup sailpoint-entra-setup.json --apply
$S/isc-source.sh verify --source "Entra ID - Contoso" --apply          # peek -> test -> aggregate
```

Profiles: `readonly` (always), `provisioning`, `machine-identity`, `ai-agents`, `exchange`, `teams`, `pim` —
see `SKILL.md` and `references/entra-permissions.md`.

Verified end to end on a live tenant pair (2026-10-08, readonly profile): Entra setup, source create/configure, and
verify (peek, test, entitlement + account aggregation, all v2026). Also live: the ai-agents profile (Azure RBAC on a subscription), Foundry dataset aggregation (from the UI — the API
answered 404 on that tenant) and its schedule. Not yet run live: the provisioning / Exchange / PIM profiles.

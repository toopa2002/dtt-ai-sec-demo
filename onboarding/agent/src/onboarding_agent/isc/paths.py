"""SailPoint ISC paths per API version (spec 002 research R4).

A playbook names its API with `settings.yaml` `isc_api`; without it the type keeps the beta/v3 paths the AWS SaaS
tools have always used (`LEGACY`, byte-identical, guarded by tests/unit/test_aws_unchanged.py). `V2026` follows
.claude/skills/sailpoint-isc-entra-connector/references/isc-api.md, which was checked on a live tenant.
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Paths:
    name: str
    ops: dict[str, str]
    account_source_field: str          # filter field for "accounts on this source"
    multipart_aggregation: bool        # load-accounts / load-entitlements bodies
    extra: dict[str, Any] = field(default_factory=dict)

    def __call__(self, op: str, **kw: str) -> str:
        return self.ops[op].format(**kw)


LEGACY = Paths(
    name="legacy",
    ops={
        "tenant": "/beta/tenant",
        "connector": "/v3/connectors/{script}",
        "connector_form": "/v3/connectors/{script}/source-config",
        "sources": "/v3/sources",
        "source": "/v3/sources/{sid}",
        "public_identities": "/v3/public-identities",
        "search": "/v3/search",
        "peek": "/beta/sources/{sid}/connector/peek-resource-objects",
        "test": "/beta/sources/{sid}/connector/test-configuration",
        "load_accounts": "/beta/sources/{sid}/load-accounts",
        "load_entitlements": "/beta/sources/{sid}/load-entitlements",
        "task": "/beta/task-status/{task_id}",
        "accounts": "/v3/accounts",
        "entitlements": "/v3/entitlements",
        "schemas": "/v3/sources/{sid}/schemas",
        "schema": "/v3/sources/{sid}/schemas/{schema_id}",
        "provisioning_policies": "/v3/sources/{sid}/provisioning-policies",
        "provisioning_policy": "/v3/sources/{sid}/provisioning-policies/{usage}",
        "correlation": "/beta/sources/{sid}/correlation-config",
        "datasets": "/beta/sources/{sid}/datasets",
        "dataset": "/beta/sources/{sid}/datasets/{dataset_id}",
        "aggregate_agents": "/beta/sources/{sid}/aggregate-agents",
        "machine_identities": "/beta/machine-identities",
    },
    account_source_field="sourceId",
    multipart_aggregation=False,
)

V2026 = Paths(
    name="v2026",
    ops={
        "tenant": "/v2026/tenant",
        "connector": "/v2026/connectors/{script}",
        "connector_form": "/v2026/connectors/{script}/source-config",
        "sources": "/v2026/sources",
        "source": "/v2026/sources/{sid}",
        "public_identities": "/v2026/public-identities",
        "search": "/v2026/search",
        "peek": "/v2026/sources/{sid}/connector/peek-resource-objects",
        "test": "/v2026/sources/{sid}/connector/test-configuration",
        "load_accounts": "/v2026/sources/{sid}/load-accounts",
        "load_entitlements": "/v2026/sources/{sid}/load-entitlements",
        "task": "/v2026/task-status/{task_id}",
        "accounts": "/v2026/accounts",
        "entitlements": "/v2026/entitlements",
        "schemas": "/v2026/sources/{sid}/schemas",
        "schema": "/v2026/sources/{sid}/schemas/{schema_id}",
        "provisioning_policies": "/v2026/sources/{sid}/provisioning-policies",
        "provisioning_policy": "/v2026/sources/{sid}/provisioning-policies/{usage}",
        "correlation": "/v2026/sources/{sid}/correlation-config",
        "datasets": "/v2026/sources/{sid}/datasets",
        "dataset": "/v2026/sources/{sid}/datasets/{dataset_id}",
        "aggregate_agents": "/v2026/sources/{sid}/aggregate-agents",
        "machine_identities": "/v2026/machine-identities",
    },
    # v2026 /accounts rejects `sourceId` (HTTP 400, seen live) although its spec text still shows it.
    account_source_field="source.id",
    multipart_aggregation=True,
)


def for_playbook(settings: dict[str, Any]) -> Paths:
    return V2026 if settings.get("isc_api") == "v2026" else LEGACY

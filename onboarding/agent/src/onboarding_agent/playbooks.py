"""Connector playbooks (contracts/connector-playbook.md): catalog entry + setup steps, source settings, checks, known
failures and collision rules, shipped with the image. Everything connector-specific lives here, not in tool code."""

import os
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

CATALOG_DIR = Path(os.environ.get("CATALOG_DIR", Path(__file__).resolve().parents[3] / "catalog"))
_PLACEHOLDER = re.compile(r"\{([a-z_][a-z0-9_]*)\}")


@dataclass
class Playbook:
    id: str
    entry: dict[str, Any]
    setup: str
    settings: dict[str, Any]
    checks: dict[str, Any]
    failures: str
    collisions: str
    suggestions: dict[str, Any] = field(default_factory=dict)   # suggestions.yaml: per role, per state (FR-006e)
    values: dict[str, Any] = field(default_factory=dict)

    def render(self, text: str) -> str:
        """Fill {placeholders} from session details and tenant values; leave a visible marker for unknown ones."""
        def sub(m: re.Match) -> str:
            value = self.values.get(m.group(1))
            if value == []:
                return "none"  # an optional list left empty, e.g. no discovery regions chosen
            if value in (None, ""):
                return f"<{m.group(1).upper()} — not known yet, ask for it>"
            return ",".join(value) if isinstance(value, list) else str(value)

        return _PLACEHOLDER.sub(sub, text)

    def unknown_placeholders(self, text: str) -> list[str]:
        return sorted({m.group(1) for m in _PLACEHOLDER.finditer(text) if self.values.get(m.group(1)) in (None, "")})


@lru_cache
def catalog() -> list[dict[str, Any]]:
    return yaml.safe_load((CATALOG_DIR / "catalog.yaml").read_text())["types"]


def load(type_id: str) -> Playbook:
    entry = next((t for t in catalog() if t["id"] == type_id), None)
    if not entry or entry.get("status") != "available":
        raise ValueError(f"connector type {type_id!r} is not available")
    folder = CATALOG_DIR / entry["playbook"]

    def read(name: str) -> str:
        path = folder / name
        return path.read_text() if path.exists() else ""

    return Playbook(
        id=type_id, entry=entry, setup=read("setup.md"),
        settings=yaml.safe_load(read("settings.yaml") or "{}") or {},
        checks=yaml.safe_load(read("checks.yaml") or "{}") or {},
        failures=read("failures.md"), collisions=read("collisions.md"),
        suggestions=yaml.safe_load(read("suggestions.yaml") or "{}") or {},
    )


def session_values(session: dict[str, Any]) -> dict[str, Any]:
    """Values the playbook templates may use: session details + tenant facts."""
    tenant = session.get("tenant") or {}
    values = dict(session.get("details") or {})
    values.update(tenant_name=tenant.get("name"), tenant_host=tenant.get("api_host"),
                  external_id=tenant.get("external_id"))
    if values.get("accounts"):
        values["member_accounts"] = [a for a in values["accounts"] if a != values.get("management_account_id")]
    return values


def _policies(folder: Path) -> dict[str, str]:
    """policies/policy-<name>.json -> {policy_<short>: compact JSON} for setup.md placeholders."""
    names = {"policy-aggregation": "policy_aggregation", "policy-organization-mgo": "policy_organization",
             "policy-bedrock-agent-discovery": "policy_bedrock", "policy-bedrock-agentcore-discovery": "policy_agentcore"}
    out: dict[str, str] = {}
    for path in sorted((folder / "policies").glob("policy-*.json")):
        out[names.get(path.stem, path.stem.replace("-", "_"))] = path.read_text().strip()
    return out


def for_session(session: dict[str, Any]) -> Playbook:
    pb = load(session["connector_type"])
    folder = CATALOG_DIR / pb.entry["playbook"]
    pb.values = (pb.settings.get("values") or {}) | _policies(folder) | session_values(session)
    return pb

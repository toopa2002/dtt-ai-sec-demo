"""Connector playbooks (contracts/connector-playbook.md): catalog entry + setup steps, source settings, checks, known
failures and collision rules, shipped with the image. Everything connector-specific lives here, not in tool code."""

import json
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
    plan: list[dict[str, Any]] = field(default_factory=list)    # plan.yaml: the starting plan (FR-008b)
    values: dict[str, Any] = field(default_factory=dict)
    assets: dict[str, str] = field(default_factory=dict)        # assets/<stem>.json, raw text with {placeholders}
    permissions: dict[str, dict] = field(default_factory=dict)  # permissions/<profile>.json (spec 002 R15)
    prompt: str = ""                                            # prompt.md: type rules appended to the static prompt

    def asset(self, stem: str, **extra: Any) -> Any:
        """An asset rendered with the session values (plus `extra`) and parsed; keys starting with _ dropped."""
        saved = self.values
        self.values = {**saved, **extra}
        try:
            data = json.loads(self.render(self.assets[stem]))
        finally:
            self.values = saved
        return {k: v for k, v in data.items() if not k.startswith("_")} if isinstance(data, dict) else data

    def permission_names(self, profile: str) -> list[str]:
        names: list[str] = []
        for res in (self.permissions.get(profile) or {}).get("resources") or []:
            for kind in ("appRoles", "delegated"):
                names += [p["name"] for p in res.get(kind) or []]
        return names

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

    def read_dir(name: str, parse: bool) -> dict[str, Any]:
        sub = folder / name
        if not sub.is_dir():
            return {}
        return {f.stem: (json.loads(f.read_text()) if parse else f.read_text()) for f in sorted(sub.glob("*.json"))}

    return Playbook(
        id=type_id, entry=entry, setup=read("setup.md"), assets=read_dir("assets", False),
        permissions=read_dir("permissions", True),
        settings=yaml.safe_load(read("settings.yaml") or "{}") or {},
        checks=yaml.safe_load(read("checks.yaml") or "{}") or {},
        failures=read("failures.md"), collisions=read("collisions.md"), prompt=read("prompt.md"),
        suggestions=yaml.safe_load(read("suggestions.yaml") or "{}") or {},
        plan=(yaml.safe_load(read("plan.yaml") or "{}") or {}).get("steps", []),
    )


def session_values(session: dict[str, Any]) -> dict[str, Any]:
    """Values the playbook templates may use: session details + tenant facts."""
    tenant = session.get("tenant") or {}
    values = dict(session.get("details") or {})
    values.update(tenant_name=tenant.get("name"), tenant_host=tenant.get("api_host"),
                  external_id=tenant.get("external_id"))
    if values.get("accounts"):
        values["member_accounts"] = [a for a in values["accounts"] if a != values.get("management_account_id")]
    if "capabilities" in values:  # spec 002: directory is always on
        values["capabilities"] = chosen_capabilities(session)
        values["subscription_count"] = str(len(values.get("foundry_subscriptions") or []))
        values["source_name"] = values.get("source_name") or ""
    return values


def chosen_capabilities(session: dict[str, Any]) -> list[str]:
    chosen = list((session.get("details") or {}).get("capabilities") or [])
    return chosen if "directory" in chosen else ["directory", *chosen]


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
    # permissions/<profile>.json -> {permissions_<profile>}: the names setup.md asks for (spec 002 R15)
    perms = {f"permissions_{stem.replace('-', '_')}": " ".join(pb.permission_names(stem)) for stem in pb.permissions}
    pb.values = (pb.settings.get("values") or {}) | _policies(folder) | perms | session_values(session)
    return pb

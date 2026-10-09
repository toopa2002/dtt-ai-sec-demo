"""Read-only connector catalog (FR-028–FR-030) and validation of session details against a type's fields."""

import json
import re
from functools import lru_cache
from typing import Any

import yaml

from ..config import settings

FIELD_TYPES = {"string", "string_list", "region", "region_list", "aws_account_id", "aws_account_id_list",
               # spec 002 (Entra)
               "entra_tenant", "guid_list", "domain", "country_code", "capabilities"}
_ACCOUNT = re.compile(r"^\d{12}$")
_REGION = re.compile(r"^[a-z]{2}(-gov)?-[a-z]+-\d$")
_GUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)
_ONMICROSOFT = re.compile(r"^[a-z0-9][a-z0-9-]*\.onmicrosoft\.com$")
_DOMAIN = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}$")
_COUNTRY = re.compile(r"^[A-Z]{2}$")
CAPABILITY_TAGS = ("read_only", "writes")
CUSTOM_DOMAIN_WARNING = "SailPoint recommends the initial .onmicrosoft.com domain"
PUBLIC_KEYS = ("id", "name", "status", "description", "owner_label", "application_label", "first_step_label",
               "owner_asks", "agent_configures", "session_fields", "capabilities", "secret", "badge", "isc_api")


class CatalogError(ValueError):
    pass


class DetailsError(ValueError):
    def __init__(self, errors: dict[str, str]):
        super().__init__("; ".join(f"{k}: {v}" for k, v in errors.items()))
        self.errors = errors


@lru_cache
def load() -> list[dict[str, Any]]:
    path = settings().catalog_dir / "catalog.yaml"
    data = yaml.safe_load(path.read_text())
    types = data.get("types") or []
    seen: set[str] = set()
    for t in types:
        for key in ("id", "name", "status", "description", "owner_asks", "agent_configures"):
            if not t.get(key):
                raise CatalogError(f"catalog entry {t.get('id')!r} is missing {key}")
        if t["id"] in seen:
            raise CatalogError(f"duplicate catalog id {t['id']}")
        seen.add(t["id"])
        if t["status"] not in ("available", "planned"):
            raise CatalogError(f"{t['id']}: status must be available or planned")
        if t["status"] == "available" and not t.get("playbook"):
            raise CatalogError(f"{t['id']}: an available type needs a playbook")
        for f in t.get("session_fields") or []:
            if f.get("type") not in FIELD_TYPES:
                raise CatalogError(f"{t['id']}.{f.get('name')}: unknown field type {f.get('type')}")
        _load_capabilities(t)
        t.setdefault("owner_label", "Application owner")
        t.setdefault("application_label", "the application")
        t.setdefault("first_step_label", "Application ready")
    return types


def _permission_count(type_entry: dict[str, Any], profile: str) -> int:
    path = settings().catalog_dir / type_entry["playbook"] / "permissions" / f"{profile}.json"
    if not path.exists():
        raise CatalogError(f"{type_entry['id']}: permissions/{profile}.json is missing")
    data = json.loads(path.read_text())
    return sum(len(res.get(kind) or []) for res in data.get("resources") or [] for kind in ("appRoles", "delegated"))


def _load_capabilities(t: dict[str, Any]) -> None:
    """Spec 002 (data-model "Connector catalog entry", research R15): validate `capabilities` and fill
    `{permission_count}` in each `owner_summary` from the playbook's permission file, so the counts on the
    new-session panel always equal what setup.md asks for."""
    caps = t.get("capabilities") or []
    seen: set[str] = set()
    for c in caps:
        if not c.get("id") or c["id"] in seen or not c.get("label") or c.get("tag") not in CAPABILITY_TAGS:
            raise CatalogError(f"{t['id']}: bad capability {c!r}")
        seen.add(c["id"])
        c.setdefault("enabled", True)
        if "{permission_count}" in str(c.get("owner_summary") or ""):
            if not c.get("permissions"):
                raise CatalogError(f"{t['id']}.{c['id']}: owner_summary counts permissions but names no file")
            c["owner_summary"] = c["owner_summary"].replace(
                "{permission_count}", str(_permission_count(t, c["permissions"])))
    fields = {f["name"] for f in t.get("session_fields") or []}
    for c in caps:
        for name in c.get("requires_fields") or []:
            if name not in fields:
                raise CatalogError(f"{t['id']}.{c['id']}: requires unknown field {name}")


def public() -> list[dict[str, Any]]:
    out = []
    for t in load():
        entry = {k: t.get(k) for k in PUBLIC_KEYS}
        if t.get("capabilities") and t["status"] == "available":
            # spec 002 R15: the new-session panel lists the steps for the chosen capabilities, from the same plan
            entry["plan_preview"] = [{k: s.get(k) for k in ("id", "title", "actor", "kind", "capability")}
                                     for s in plan_template(t["id"])]
        out.append(entry)
    return out


def get(type_id: str) -> dict[str, Any] | None:
    return next((t for t in load() if t["id"] == type_id), None)


SUGGESTION_ROLES = ("iam_engineer", "application_owner")
SUGGESTION_STATES = ("no_source", "waiting_for_owner_output", "check_failed", "all_passed", "any",
                     "waiting_for_secret", "tenant_limitation")  # the last two: spec 002
SUGGESTION_KINDS = ("answer", "order", "question")


@lru_cache
def suggestion_defaults(type_id: str) -> dict[str, dict[str, list[dict[str, str]]]]:
    """`playbooks/<id>/suggestions.yaml` (FR-006e): per role, per state, `{text, kind}` items. Fails fast when a
    role's `any` list has fewer than 3 items or the application owner is offered an order (FR-019)."""
    connector = get(type_id)
    if not connector or not connector.get("playbook"):
        return {r: {"any": []} for r in SUGGESTION_ROLES}
    path = settings().catalog_dir / connector["playbook"] / "suggestions.yaml"
    data = yaml.safe_load(path.read_text()) if path.exists() else {}
    out: dict[str, dict[str, list[dict[str, str]]]] = {}
    for role in SUGGESTION_ROLES:
        by_state = (data or {}).get(role) or {}
        out[role] = {}
        for state, items in by_state.items():
            if state not in SUGGESTION_STATES:
                raise CatalogError(f"{type_id} suggestions: unknown state {state!r} for {role}")
            cleaned = []
            for item in items or []:
                text, kind = str(item.get("text", "")).strip(), item.get("kind", "question")
                if not text or kind not in SUGGESTION_KINDS:
                    raise CatalogError(f"{type_id} suggestions: bad item under {role}.{state}: {item!r}")
                if role == "application_owner" and kind == "order":
                    raise CatalogError(f"{type_id} suggestions: the application owner must not be offered an order")
                cleaned.append({"text": text, "kind": kind})
            out[role][state] = cleaned
        if len(out[role].get("any", [])) < 3:
            raise CatalogError(f"{type_id} suggestions: {role}.any needs at least 3 items")
    return out


PLAN_ACTORS = ("application_owner", "iam_engineer", "agent")
PLAN_KINDS = ("read_only", "change")
MILESTONES = ("application_ready", "source_created", "configured", "connection_check", "aggregation",
              "test_connection")


@lru_cache
def plan_template(type_id: str) -> list[dict[str, Any]]:
    """`playbooks/<id>/plan.yaml` (FR-008b, research R22): the starting plan. Fails fast on a bad step."""
    connector = get(type_id)
    if not connector or not connector.get("playbook"):
        return []
    path = settings().catalog_dir / connector["playbook"] / "plan.yaml"
    steps = (yaml.safe_load(path.read_text()) or {}).get("steps", []) if path.exists() else []
    ids = set()
    for step in steps:
        if (not step.get("id") or step["id"] in ids or step.get("actor") not in PLAN_ACTORS
                or step.get("kind") not in PLAN_KINDS or step.get("milestone") not in (None, *MILESTONES)
                or not str(step.get("title", "")).strip()
                or step.get("on_extend") not in (None, "skip")):
            raise CatalogError(f"{type_id} plan: bad step {step!r}")
        ids.add(step["id"])
    return steps


@lru_cache
def plan_steps_by_milestone(type_id: str) -> dict[str, str]:
    """`checks.yaml` `plan_step`: which plan step a milestone's set_step marks."""
    connector = get(type_id)
    if not connector or not connector.get("playbook"):
        return {}
    path = settings().catalog_dir / connector["playbook"] / "checks.yaml"
    return dict((yaml.safe_load(path.read_text()) or {}).get("plan_step") or {}) if path.exists() else {}


@lru_cache
def checks(type_id: str) -> dict[str, Any]:
    """The type's `checks.yaml` (spec 002: aggregation timing, schema attributes), {} when it has none."""
    connector = get(type_id)
    if not connector or not connector.get("playbook"):
        return {}
    path = settings().catalog_dir / connector["playbook"] / "checks.yaml"
    return (yaml.safe_load(path.read_text()) or {}) if path.exists() else {}


@lru_cache
def milestone_order(type_id: str) -> list[str]:
    """`checks.yaml` `milestone_order` (spec 002 R19): the header chip order; 001's order when absent."""
    connector = get(type_id)
    default = list(MILESTONES)
    if not connector or not connector.get("playbook"):
        return default
    path = settings().catalog_dir / connector["playbook"] / "checks.yaml"
    order = (yaml.safe_load(path.read_text()) or {}).get("milestone_order") if path.exists() else None
    if order and sorted(order) != sorted(MILESTONES):
        raise CatalogError(f"{type_id}: milestone_order must list each milestone once")
    return list(order or default)


def _as_list(value: Any) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, str):
        return [v.strip() for v in re.split(r"[,\s]+", value) if v.strip()]
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    raise ValueError("expected a list")


def validate_details(type_id: str, details: dict[str, Any], tenant_name: str) -> dict[str, Any]:
    """Return cleaned details with defaults applied, or raise DetailsError with per-field messages."""
    return validate(type_id, details, tenant_name)[0]


def _capabilities(connector: dict[str, Any], raw: Any) -> list[str]:
    caps = {c["id"]: c for c in connector.get("capabilities") or []}
    chosen = _as_list(raw)
    unknown = [c for c in chosen if c not in caps]
    if unknown:
        raise ValueError(f"unknown capabilities: {', '.join(unknown)}")
    off = [c for c in chosen if not caps[c].get("enabled", True)]
    if off:
        raise ValueError(f"not available yet: {', '.join(caps[c]['label'] for c in off)}")
    always = [c for c, v in caps.items() if v.get("always")]
    return [c for c in caps if c in chosen or c in always]


def validate(type_id: str, details: dict[str, Any], tenant_name: str) -> tuple[dict[str, Any], dict[str, str]]:
    """(cleaned details, warnings per field), or DetailsError. Spec 002 adds the Entra field types, `show_if`
    (a field shown and validated only with that capability) and capabilities' `requires_fields`."""
    connector = get(type_id)
    if not connector:
        raise DetailsError({"connector_type": "unknown connector type"})
    out: dict[str, Any] = {}
    errors: dict[str, str] = {}
    warnings: dict[str, str] = {}
    fields = connector.get("session_fields") or []
    chosen: list[str] = []
    cap_field = next((f for f in fields if f["type"] == "capabilities"), None)
    if cap_field:
        try:
            chosen = _capabilities(connector, details.get(cap_field["name"]))
        except ValueError as exc:
            errors[cap_field["name"]] = str(exc)
    required_by_caps = {name for c in connector.get("capabilities") or [] if c["id"] in chosen
                        for name in c.get("requires_fields") or []}
    for f in fields:
        name, ftype = f["name"], f["type"]
        if ftype == "capabilities":
            out[name] = chosen
            continue
        if f.get("show_if") and f["show_if"] not in chosen:
            out[name] = [] if ftype.endswith("_list") else ""
            continue
        raw = details.get(name)
        if (raw is None or raw == "" or raw == []) and "default" in f:
            raw = str(f["default"]).replace("{tenant}", tenant_name) if isinstance(f["default"], str) else f["default"]
        try:
            if ftype.endswith("_list"):
                values = _as_list(raw)
                check = {"aws_account_id_list": _ACCOUNT, "region_list": _REGION, "guid_list": _GUID}.get(ftype)
                bad = [v for v in values if check and not check.match(v)]
                if bad:
                    raise ValueError(f"not valid: {', '.join(bad)}")
                value: Any = values
                empty = not values
            else:
                value = "" if raw is None else str(raw).strip()
                if value and ftype == "aws_account_id" and not _ACCOUNT.match(value):
                    raise ValueError("must be a 12-digit AWS account id")
                if value and ftype == "region" and not _REGION.match(value):
                    raise ValueError("must be an AWS region like ap-southeast-1")
                if value and ftype == "entra_tenant":
                    value = value.lower()
                    if not (_ONMICROSOFT.match(value) or _GUID.match(value)):
                        if not _DOMAIN.match(value):
                            raise ValueError("must be the tenant's initial domain like contoso.onmicrosoft.com, "
                                             "or its tenant ID")
                        warnings[name] = CUSTOM_DOMAIN_WARNING
                if value and ftype == "domain":
                    value = value.lower()
                    if not _DOMAIN.match(value):
                        raise ValueError("must be a domain like contoso.com")
                if value and ftype == "country_code":
                    value = value.upper()
                    if not _COUNTRY.match(value):
                        raise ValueError("must be a two-letter country code like TH")
                if value and f.get("choices") and value not in f["choices"]:
                    raise ValueError(f"must be one of {', '.join(f['choices'])}")
                if len(value) > 200:
                    raise ValueError("must be at most 200 characters")
                empty = not value
            if (f.get("required") or name in required_by_caps) and empty:
                raise ValueError("is required" if name not in required_by_caps else
                                 "is required for the chosen capabilities")
            out[name] = value
        except ValueError as exc:
            errors[name] = str(exc)
    if errors:
        raise DetailsError(errors)
    return out, warnings

"""Read-only connector catalog (FR-028–FR-030) and validation of session details against a type's fields."""

import re
from functools import lru_cache
from typing import Any

import yaml

from ..config import settings

FIELD_TYPES = {"string", "string_list", "region", "region_list", "aws_account_id", "aws_account_id_list"}
_ACCOUNT = re.compile(r"^\d{12}$")
_REGION = re.compile(r"^[a-z]{2}(-gov)?-[a-z]+-\d$")
PUBLIC_KEYS = ("id", "name", "status", "description", "owner_label", "application_label", "first_step_label",
               "owner_asks", "agent_configures", "session_fields")


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
        t.setdefault("owner_label", "Application owner")
        t.setdefault("application_label", "the application")
        t.setdefault("first_step_label", "Application ready")
    return types


def public() -> list[dict[str, Any]]:
    return [{k: t.get(k) for k in PUBLIC_KEYS} for t in load()]


def get(type_id: str) -> dict[str, Any] | None:
    return next((t for t in load() if t["id"] == type_id), None)


SUGGESTION_ROLES = ("iam_engineer", "application_owner")
SUGGESTION_STATES = ("no_source", "waiting_for_owner_output", "check_failed", "all_passed", "any")
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
                or not str(step.get("title", "")).strip()):
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
    connector = get(type_id)
    if not connector:
        raise DetailsError({"connector_type": "unknown connector type"})
    out: dict[str, Any] = {}
    errors: dict[str, str] = {}
    for f in connector.get("session_fields") or []:
        name, ftype = f["name"], f["type"]
        raw = details.get(name)
        if (raw is None or raw == "" or raw == []) and "default" in f:
            raw = str(f["default"]).replace("{tenant}", tenant_name) if isinstance(f["default"], str) else f["default"]
        try:
            if ftype.endswith("_list"):
                values = _as_list(raw)
                check = _ACCOUNT if ftype == "aws_account_id_list" else _REGION if ftype == "region_list" else None
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
                if len(value) > 200:
                    raise ValueError("must be at most 200 characters")
                empty = not value
            if f.get("required") and empty:
                raise ValueError("is required")
            out[name] = value
        except ValueError as exc:
            errors[name] = str(exc)
    if errors:
        raise DetailsError(errors)
    return out

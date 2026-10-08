"""T018 (spec 002): the new catalog field types, capabilities and their rules, the permission counts on the
"what happens" panel, and the plan seeded by capability. A small catalog is written to a temporary folder so the
rules are tested on their own; the shipped catalog is checked in test_shipped_catalog_loads."""

import json
from pathlib import Path

import pytest
import yaml

from onboarding_api.catalog import catalog
from onboarding_api.config import settings
from onboarding_api.sessions import plan as plans

GUID = "8b1e4c2a-0d3f-4a77-9c51-6f2e8a0b3d19"


def _write(root: Path) -> None:
    (root / "playbooks" / "t" / "permissions").mkdir(parents=True)
    perms = {"resources": [{"api": "Microsoft Graph", "appRoles": [{"name": "A"}, {"name": "B"}]},
                           {"api": "Other", "delegated": [{"name": "C"}]}]}
    (root / "playbooks" / "t" / "permissions" / "p.json").write_text(json.dumps(perms))
    (root / "playbooks" / "t" / "checks.yaml").write_text(yaml.safe_dump({"milestone_order": [
        "application_ready", "source_created", "configured", "connection_check", "test_connection",
        "aggregation"]}))
    (root / "playbooks" / "t" / "plan.yaml").write_text(yaml.safe_dump({"steps": [
        {"id": "a", "title": "A", "actor": "agent", "kind": "change"},
        {"id": "b", "title": "B", "actor": "agent", "kind": "change", "capability": "extra"},
        {"id": "c", "title": "C", "actor": "agent", "kind": "change", "on_extend": "skip"}]}))
    entry = {
        "id": "t", "name": "T", "status": "available", "description": "d", "owner_asks": "o",
        "agent_configures": "a", "playbook": "playbooks/t",
        "capabilities": [
            {"id": "base", "label": "Base", "tag": "read_only", "always": True, "permissions": "p",
             "owner_summary": "Base: {permission_count} read permissions"},
            {"id": "extra", "label": "Extra", "tag": "read_only", "requires_fields": ["subs"]},
            {"id": "off", "label": "Off", "tag": "writes", "enabled": False}],
        "session_fields": [
            {"name": "tenant", "label": "Tenant", "type": "entra_tenant", "required": True},
            {"name": "caps", "label": "Capabilities", "type": "capabilities", "required": True},
            {"name": "subs", "label": "Subscriptions", "type": "guid_list", "required": False, "show_if": "extra"},
            {"name": "upn", "label": "Domain", "type": "domain", "required": False},
            {"name": "loc", "label": "Location", "type": "country_code", "required": False, "default": "TH"},
            {"name": "mode", "label": "Source", "type": "string", "required": False, "default": "new",
             "choices": ["new", "extend"]}]}
    planned = {"id": "p", "name": "P", "status": "planned", "description": "d", "owner_asks": "o",
               "agent_configures": "a"}
    (root / "catalog.yaml").write_text(yaml.safe_dump({"version": 1, "types": [entry, planned]}))


def _clear() -> None:
    for fn in (catalog.load, catalog.plan_template, catalog.milestone_order, catalog.plan_steps_by_milestone,
               catalog.suggestion_defaults):
        fn.cache_clear()


@pytest.fixture
def tmp_catalog(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    _write(tmp_path)
    monkeypatch.setattr(settings(), "catalog_dir", tmp_path)
    _clear()
    yield tmp_path
    _clear()


def test_permission_count_filled_from_the_playbook(tmp_catalog) -> None:  # type: ignore[no-untyped-def]
    caps = {c["id"]: c for c in catalog.get("t")["capabilities"]}
    assert caps["base"]["owner_summary"] == "Base: 3 read permissions"
    assert caps["extra"]["enabled"] is True and caps["off"]["enabled"] is False
    assert "capabilities" in catalog.public()[0]


def test_directory_like_capability_is_always_on(tmp_catalog) -> None:  # type: ignore[no-untyped-def]
    out = catalog.validate_details("t", {"tenant": "contoso.onmicrosoft.com", "caps": []}, "acme")
    assert out["caps"] == ["base"]
    assert out["subs"] == []  # hidden field: stored empty, not validated
    assert out["loc"] == "TH" and out["mode"] == "new"


def test_unknown_or_disabled_capability_is_rejected(tmp_catalog) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(catalog.DetailsError) as exc:
        catalog.validate_details("t", {"tenant": "contoso.onmicrosoft.com", "caps": ["nope"]}, "acme")
    assert "unknown capabilities" in exc.value.errors["caps"]
    with pytest.raises(catalog.DetailsError) as exc:
        catalog.validate_details("t", {"tenant": "contoso.onmicrosoft.com", "caps": ["off"]}, "acme")
    assert exc.value.errors["caps"] == "not available yet: Off"


def test_requires_fields_and_show_if(tmp_catalog) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(catalog.DetailsError) as exc:
        catalog.validate_details("t", {"tenant": "contoso.onmicrosoft.com", "caps": ["extra"]}, "acme")
    assert exc.value.errors["subs"] == "is required for the chosen capabilities"
    out = catalog.validate_details("t", {"tenant": "contoso.onmicrosoft.com", "caps": ["extra"],
                                         "subs": f"{GUID}, {GUID.upper()}"}, "acme")
    assert out["subs"] == [GUID, GUID.upper()]
    with pytest.raises(catalog.DetailsError) as exc:
        catalog.validate_details("t", {"tenant": "contoso.onmicrosoft.com", "caps": ["extra"], "subs": "abc"}, "acme")
    assert exc.value.errors["subs"] == "not valid: abc"


def test_entra_tenant_domain_rules(tmp_catalog) -> None:  # type: ignore[no-untyped-def]
    out, warnings = catalog.validate("t", {"tenant": "Contoso-Demo.onmicrosoft.com", "caps": []}, "acme")
    assert out["tenant"] == "contoso-demo.onmicrosoft.com" and not warnings
    assert catalog.validate("t", {"tenant": GUID, "caps": []}, "acme")[1] == {}
    out, warnings = catalog.validate("t", {"tenant": "contoso.com", "caps": []}, "acme")
    assert warnings == {"tenant": "SailPoint recommends the initial .onmicrosoft.com domain"}
    with pytest.raises(catalog.DetailsError) as exc:
        catalog.validate_details("t", {"tenant": "not a domain", "caps": []}, "acme")
    assert "contoso.onmicrosoft.com" in exc.value.errors["tenant"]


def test_domain_country_and_choices(tmp_catalog) -> None:  # type: ignore[no-untyped-def]
    out = catalog.validate_details("t", {"tenant": GUID, "caps": [], "upn": "Contoso.Example", "loc": "th"}, "acme")
    assert out["upn"] == "contoso.example" and out["loc"] == "TH"
    with pytest.raises(catalog.DetailsError) as exc:
        catalog.validate_details("t", {"tenant": GUID, "caps": [], "upn": "x", "loc": "THA", "mode": "both"}, "a")
    assert set(exc.value.errors) == {"upn", "loc", "mode"}


def test_milestone_order_and_plan_seed(tmp_catalog) -> None:  # type: ignore[no-untyped-def]
    assert catalog.milestone_order("t")[-2:] == ["test_connection", "aggregation"]
    template = catalog.plan_template("t")
    plan = plans.seed(template, {"caps": None, "capabilities": ["base"]})
    states = {s["id"]: (s["state"], s["reason"]) for s in plan}
    assert states["b"] == ("skipped", "capability not chosen") and states["a"][0] == "todo"
    assert plans.public(plan)[1]["capability"] == "extra"
    extended = plans.skip_on_extend(plan)
    assert {s["id"]: s["state"] for s in extended}["c"] == "skipped"
    assert next(s for s in extended if s["id"] == "c")["reason"] == "existing source"


def test_shipped_catalog_loads() -> None:
    _clear()
    types = {t["id"]: t for t in catalog.load()}
    assert types["aws-saas"]["status"] == "available"
    assert catalog.milestone_order("aws-saas") == list(catalog.MILESTONES)
    # AWS session details validate exactly as before (SC-106)
    out = catalog.validate_details("aws-saas", {"source_name": "AWS - Acme Org", "source_owner": "w",
                                                "management_account_id": "111122223333",
                                                "accounts": "111122223333"}, "acme-demo")
    assert out["role_name"] == "SailPointISCRole-acme-demo" and out["region"] == "ap-southeast-1"
    assert "capabilities" not in out

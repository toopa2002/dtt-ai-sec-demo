"""Generic SailPoint ISC source tools (research R6, contracts/agent-invocation.md "Agent-side rules").

Driven by the session's playbook (`settings.yaml`, `checks.yaml`), so adding a connector type needs no change here.
Ported from .claude/skills/sailpoint-isc-aws-connector/scripts/isc-source.sh (create / configure / peek / aggregate /
test). Every write emits exactly one `action` event (SC-006); `create_source` refuses a same-name source owned by
someone else (FR-018, SC-007).
"""

import asyncio
import re
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import quote

from ..playbooks import Playbook
from .client import IscClient, IscError

Emit = Callable[[dict], Awaitable[None]]
WRITE_TOOLS = {"create_source", "configure_source", "peek_accounts", "start_aggregation", "test_connection",
               "delete_session_source"}


OWNER_RETRY_SECONDS = 2.0


def _q(filter_expr: str) -> str:
    return quote(filter_expr, safe="")


class IscTools:
    def __init__(self, client: IscClient, playbook: Playbook, session: dict[str, Any], emit: Emit,
                 poll_seconds: float = 10.0, poll_limit: int = 60, trigger: str = "order"):
        self.trigger = trigger  # why actions in this turn run: the IAM engineer's order, or an owner's confirmation
        self.isc = client
        self.pb = playbook
        self.session = session
        self.emit = emit
        self.poll_seconds = poll_seconds
        self.poll_limit = poll_limit

    # ---------------------------------------------------------------- helpers
    async def _action(self, action: str, result: str, *, source: dict | None = None, error: str | None = None,
                      request_summary: dict | None = None, task_ids: list[str] | None = None) -> None:
        await self.emit({"type": "action", "action": action, "source": source or self.session.get("source"),
                         "result": result, "error": error, "request_summary": request_summary or {},
                         "task_ids": task_ids or [], "trigger": self.trigger})

    async def _step(self, step: str, state: str) -> None:
        await self.emit({"type": "set_step", "step": step, "state": state})

    def _own_source(self, source_id: str) -> bool:
        src = self.session.get("source") or {}
        return bool(src) and src.get("id") == source_id

    # ---------------------------------------------------------------- reads
    async def get_tenant_external_id(self) -> dict:
        tenant = await self.isc.get("/beta/tenant")
        for product in tenant.get("products") or []:
            if product.get("productName") == "idn" and (product.get("attributes") or {}).get("externalId"):
                return {"external_id": product["attributes"]["externalId"]}
        return {"external_id": tenant.get("externalId"), "note": "no idn product externalId; using top-level value"}

    async def get_connector_form(self) -> dict:
        script = self.pb.settings["connector_script"]
        connector = await self.isc.get(f"/v3/connectors/{quote(script)}")
        form = await self.isc.get(f"/v3/connectors/{quote(script)}/source-config")
        fields = sorted(set(re.findall(r'<Field[^>]*\sname="([A-Za-z0-9_.-]+)"', form if isinstance(form, str) else "")))
        mapped = self.pb.settings.get("field_map") or {}
        missing = [key for key, field in mapped.items() if fields and field not in fields]
        return {"connector": script, "spec_id": connector.get("type"), "fields": fields,
                "mapped_fields_missing_from_form": missing}

    async def find_source(self, name: str) -> dict:
        found = await self.isc.get(f"/v3/sources?filters={_q(f'name eq \"{name}\"')}")
        if not found:
            return {"exists": False}
        src = found[0]
        return {"exists": True, "id": src["id"], "name": src["name"],
                "owner": (src.get("owner") or {}).get("name"), "created_in_this_session": self._own_source(src["id"])}

    async def get_task(self, task_id: str) -> dict:
        task = await self.isc.get(f"/beta/task-status/{quote(task_id)}")
        return {"id": task_id, "completion_status": task.get("completionStatus"),
                "messages": [m.get("localizedText") or m.get("key") for m in task.get("messages") or []][:10]}

    # ---------------------------------------------------------------- writes
    async def _owner_id(self, owner: str) -> str:
        if re.fullmatch(r"[0-9a-f]{32}", owner):
            return owner
        field = "email" if "@" in owner else "alias"
        path = f"/v3/public-identities?filters={_q(f'{field} eq \"{owner}\"')}"
        try:
            found = await self.isc.get(path)
        except IscError as exc:
            if exc.status < 500:
                raise
            # SailPoint sometimes fails this lookup on its side (HTTP 500 "isSegmentEnabledForCurrentUser from
            # redis"): retry once, then find the identity through search, which takes a different path.
            await asyncio.sleep(OWNER_RETRY_SECONDS)
            try:
                found = await self.isc.get(path)
            except IscError as again:
                if again.status < 500:
                    raise
                found = await self._search_identity(field, owner)
        if not found:
            raise ValueError(f"no SailPoint identity with {field} '{owner}'")
        return found[0]["id"]

    async def _search_identity(self, field: str, owner: str) -> list[dict]:
        term = owner.replace('"', "")
        query = f'email:"{term}"' if field == "email" else f'name:"{term}" OR attributes.uid:"{term}"'
        hits = await self.isc.post("/v3/search?limit=2", json={
            "indices": ["identities"], "query": {"query": query}, "includeNested": False})
        return [{"id": h["id"]} for h in hits or [] if h.get("id")]

    async def create_source(self) -> dict:
        details = self.session["details"]
        name = details["source_name"]
        await self._step("source_created", "in_progress")
        existing = await self.find_source(name)
        if existing["exists"]:
            if existing["created_in_this_session"]:
                await self._step("source_created", "passed")
                return {"created": False, "reused": True, "id": existing["id"], "name": name}
            await self._step("source_created", "failed")
            await self._action("create_source", "failed", source={"id": existing["id"], "name": name},
                               error=f"A source named '{name}' already exists and belongs to {existing['owner']}.")
            return {"created": False, "owned_by": existing["owner"], "id": existing["id"],
                    "instruction": "Do not reuse or change it. Ask the IAM engineer for a different source name."}
        settings = self.pb.settings
        try:
            owner_id = await self._owner_id(details["source_owner"])
            spec_id = settings.get("spec_id") or (await self.get_connector_form())["spec_id"]
            ext = (await self.get_tenant_external_id())["external_id"]
            attrs = dict(settings.get("creation_only") or {})
            attrs["spConnectorSpecId"] = spec_id
            if ext:
                attrs[(settings.get("field_map") or {}).get("external_id", "externalId")] = ext
            body = {"name": name, "description": settings.get("description", "Managed by the ISC Onboarding Agent"),
                    "owner": {"type": "IDENTITY", "id": owner_id}, "connector": settings["connector_script"],
                    "connectorAttributes": attrs}
            created = await self.isc.post("/v3/sources", json=body)
        except (IscError, ValueError, KeyError) as exc:
            await self._step("source_created", "failed")
            await self._action("create_source", "failed", source={"id": "", "name": name}, error=str(exc),
                               request_summary={"name": name, "connector": settings.get("connector_script")})
            return {"created": False, "error": str(exc)}
        source = {"id": created["id"], "name": created.get("name", name)}
        self.session["source"] = source
        await self.emit({"type": "source", **source})
        await self._step("source_created", "passed")
        instance = ((created.get("connectorAttributes") or {}).get("spConnectorInstanceId"))
        await self._action("create_source", "ok", source=source,
                           request_summary={"name": name, "connector": settings["connector_script"],
                                            "spConnectorSpecId": spec_id, "external_id_set": bool(ext)})
        return {"created": True, **source, "connector_instance_linked": bool(instance)}

    def _settings_values(self) -> dict[str, Any]:
        """Map session details to connector attribute keys using settings.yaml `configure`."""
        details, values = self.session["details"], {}
        for entry in self.pb.settings.get("configure") or []:
            key, src = entry["field"], entry.get("from")
            value = entry.get("value")
            if src:
                value = details.get(src)
                if value in (None, "", []) and "default" in entry:
                    value = entry["default"]
            if entry.get("when_set") and details.get(entry["when_set"]) in (None, "", []):
                continue
            if entry.get("enabled_if"):
                value = details.get(entry["enabled_if"]) not in (None, "", [])
            if entry.get("template"):
                value = self.pb.render(entry["template"])
            if value in (None, "", []) and not entry.get("send_empty"):
                continue
            values[key] = value
        role = details.get("role_name", "")
        if role.startswith("arn:") or "/" in role:
            raise ValueError("the role name must be a plain IAM role name, not an ARN (otherwise "
                             "'AWS Client creation failed')")
        return values

    async def configure_source(self) -> dict:
        source = self.session.get("source")
        if not source:
            return {"configured": False, "error": "no source has been created in this session yet"}
        await self._step("configured", "in_progress")
        try:
            values = self._settings_values()
            ext = (await self.get_tenant_external_id())["external_id"]
            ext_key = (self.pb.settings.get("field_map") or {}).get("external_id")
            if ext and ext_key:
                values[ext_key] = ext
            ops = [{"op": "add", "path": f"/connectorAttributes/{k}", "value": v} for k, v in values.items()]
            await self.isc.patch_json(f"/v3/sources/{quote(source['id'])}", ops)
        except (IscError, ValueError) as exc:
            await self._step("configured", "failed")
            await self._action("configure_source", "failed", error=str(exc))
            return {"configured": False, "error": str(exc)}
        await self._step("configured", "passed")
        await self._action("configure_source", "ok", request_summary={"fields": sorted(values)})
        return {"configured": True, "fields": sorted(values)}

    async def peek_accounts(self) -> dict:
        """The connection check: read a few accounts through the connector (more reliable than Test Connection for
        AWS SaaS before the first aggregation)."""
        source = self.session.get("source")
        if not source:
            return {"ok": False, "error": "no source has been created in this session yet"}
        check = (self.pb.checks.get("connection_check") or {})
        await self._step("connection_check", "in_progress")
        try:
            result = await self.isc.post(f"/beta/sources/{quote(source['id'])}/connector/peek-resource-objects",
                                         json={"objectType": check.get("object_type", "account"),
                                               "maxCount": check.get("max_count", 5)})
        except IscError as exc:
            await self._step("connection_check", "failed")
            await self._action("connection_check", "failed", error=str(exc))
            return {"ok": False, "error": str(exc)}
        objects = result.get("resourceObjects") or []
        if not objects:
            detail = str(result.get("details") or result)[:800]
            await self._step("connection_check", "failed")
            await self._action("connection_check", "failed", error=detail)
            return {"ok": False, "error": detail}
        await self._step("connection_check", "passed")
        await self._step("application_ready", "passed")
        await self._action("connection_check", "ok", request_summary={"accounts_read": len(objects)})
        return {"ok": True, "accounts_read": len(objects),
                "sample": [o.get("identity") or o.get("name") for o in objects][:5]}

    async def _wait(self, task_id: str) -> dict:
        for _ in range(self.poll_limit):
            task = await self.get_task(task_id)
            if task["completion_status"]:
                return task
            await asyncio.sleep(self.poll_seconds)
        return {"id": task_id, "completion_status": None, "messages": ["still running"]}

    async def start_aggregation(self) -> dict:
        source = self.session.get("source")
        if not source:
            return {"ok": False, "error": "no source has been created in this session yet"}
        sid = quote(source["id"])
        await self._step("aggregation", "in_progress")
        await self.emit({"type": "progress", "text": "aggregating accounts in SailPoint… (this can take minutes)"})
        task_ids: list[str] = []
        try:
            acct = await self.isc.post(f"/beta/sources/{sid}/load-accounts", content="disableOptimization=true",
                                       content_type="application/x-www-form-urlencoded")
            acct_id = (acct.get("task") or {}).get("id") or acct.get("id")
            if acct_id:
                task_ids.append(acct_id)
            if (self.pb.checks.get("aggregation") or {}).get("entitlements", True):
                ent = await self.isc.post(f"/beta/sources/{sid}/load-entitlements", content="")
                ent_id = (ent.get("task") or {}).get("id") or ent.get("id")
                if ent_id:
                    task_ids.append(ent_id)
            results = [await self._wait(t) for t in task_ids]
        except IscError as exc:
            await self._step("aggregation", "failed")
            await self._action("aggregate", "failed", error=str(exc), task_ids=task_ids)
            return {"ok": False, "error": str(exc), "task_ids": task_ids}
        failed = [r for r in results if r["completion_status"] not in ("SUCCESS", "WARNING")]
        if failed:
            error = "; ".join(f"{r['id']}: {r['completion_status']} {' '.join(map(str, r['messages']))}" for r in failed)
            await self._step("aggregation", "failed")
            await self._action("aggregate", "failed", error=error, task_ids=task_ids)
            return {"ok": False, "error": error, "task_ids": task_ids}
        accounts = await self.isc.get(f"/v3/accounts?filters={_q(f'sourceId eq \"{source["id"]}\"')}&limit=250")
        await self._step("aggregation", "passed")
        await self._action("aggregate", "ok", task_ids=task_ids, request_summary={"accounts": len(accounts or [])})
        return {"ok": True, "task_ids": task_ids, "accounts_on_source": len(accounts or [])}

    async def test_connection(self) -> dict:
        source = self.session.get("source")
        if not source:
            return {"ok": False, "error": "no source has been created in this session yet"}
        await self._step("test_connection", "in_progress")
        try:
            result = await self.isc.post(f"/beta/sources/{quote(source['id'])}/connector/test-configuration")
        except IscError as exc:
            await self._step("test_connection", "failed")
            await self._action("test_connection", "failed", error=str(exc))
            return {"ok": False, "error": str(exc)}
        status = str(result.get("status", ""))
        if "SUCCESS" in status:
            await self._step("test_connection", "passed")
            await self._action("test_connection", "ok")
            return {"ok": True, "status": status}
        detail = str(result.get("details") or result)[:800]
        await self._step("test_connection", "failed")
        await self._action("test_connection", "failed", error=detail)
        hint = (self.pb.checks.get("test_connection") or {}).get("before_aggregation_hint")
        return {"ok": False, "status": status, "error": detail, "hint": hint if "req.input" in detail else None}

    async def delete_session_source(self) -> dict:
        source = self.session.get("source")
        if not source:
            return {"deleted": False, "error": "this session has not created a source"}
        try:
            await self.isc.request("DELETE", f"/v3/sources/{quote(source['id'])}")
        except IscError as exc:
            await self._action("delete_source", "failed", error=str(exc))
            return {"deleted": False, "error": str(exc)}
        await self._action("delete_source", "ok")
        self.session["source"] = None
        await self.emit({"type": "source", "id": "", "name": ""})
        return {"deleted": True, "next": "create_source then configure_source; the step chips update as they run"}

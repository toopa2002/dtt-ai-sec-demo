"""Generic SailPoint ISC source tools (research R6, contracts/agent-invocation.md "Agent-side rules").

Driven by the session's playbook (`settings.yaml`, `checks.yaml`), so adding a connector type needs no change here.
Ported from .claude/skills/sailpoint-isc-aws-connector/scripts/isc-source.sh (create / configure / peek / aggregate /
test). Every write emits exactly one `action` event (SC-006); `create_source` refuses a same-name source owned by
someone else (FR-018, SC-007).
"""

import asyncio
import re
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

from ..playbooks import Playbook
from . import paths, vault
from .client import IscClient, IscError
from .playbook_tools import PlaybookToolsMixin

Emit = Callable[[dict], Awaitable[None]]
WRITE_TOOLS = {"create_source", "configure_source", "peek_accounts", "start_aggregation", "test_connection",
               "delete_session_source",
               # spec 002 (Entra): generic, offered only to playbooks that list them in checks.yaml `tools`
               "adopt_source", "ensure_schema_attributes", "aggregate_datasets", "set_dataset_schedule",
               "set_provisioning_policy", "set_correlation", "apply_application_secret", "count_ai_agents"}


OWNER_RETRY_SECONDS = 2.0


def _q(filter_expr: str) -> str:
    return quote(filter_expr, safe="")


class IscTools(PlaybookToolsMixin):
    def __init__(self, client: IscClient, playbook: Playbook, session: dict[str, Any], emit: Emit,
                 poll_seconds: float = 10.0, poll_limit: int = 60, trigger: str = "order"):
        self.trigger = trigger  # why actions in this turn run: the IAM engineer's order, or an owner's confirmation
        self.isc = client
        self.pb = playbook
        self.paths = paths.for_playbook(playbook.settings)
        self.session = session
        self.emit = emit
        self.poll_seconds = poll_seconds
        self.poll_limit = poll_limit
        self._refs = 0

    # ---------------------------------------------------------------- helpers
    def _begin(self) -> dict[str, Any]:
        """Start an action record: its ref (unique in the turn) and start time (research R24)."""
        self._refs += 1
        return {"ref": f"a{self._refs}", "t0": time.monotonic(),
                "started_at": datetime.now(UTC).isoformat(timespec="seconds")}

    async def _action(self, action: str, result: str, *, source: dict | None = None, error: str | None = None,
                      request: dict | None = None, counts: dict | None = None, task_ids: list[str] | None = None,
                      task_states: dict | None = None, started: dict | None = None, summary: str | None = None,
                      **extra: Any) -> None:
        """One action event: what was sent (`request`, never credentials) and what SailPoint returned (`response`).
        A `running` action is sent again with the same ref when it ends (FR-020, FR-020a)."""
        started = started or self._begin()
        event = {
            "type": "action", "action_ref": started["ref"], "action": action,
            "source": source or self.session.get("source"), "result": result, "request": request or {},
            "response": {"task_ids": list(task_ids or []), "task_states": dict(task_states or {}),
                         "counts": dict(counts or {}), "error": error},
            "error": error, "task_ids": list(task_ids or []), "trigger": self.trigger, "started_at": started["started_at"],
            "duration_ms": None if result == "running" else int((time.monotonic() - started["t0"]) * 1000)}
        if summary is not None:  # spec 002 R18: one line under the action name; AWS tools never set it
            event["summary"] = summary[:80]
        event.update(extra)
        await self.emit(event)

    async def _step(self, step: str, state: str) -> None:
        await self.emit({"type": "set_step", "step": step, "state": state})

    def _own_source(self, source_id: str) -> bool:
        src = self.session.get("source") or {}
        return bool(src) and src.get("id") == source_id

    # ---------------------------------------------------------------- reads
    async def get_tenant_external_id(self) -> dict:
        tenant = await self.isc.get(self.paths("tenant"))
        for product in tenant.get("products") or []:
            if product.get("productName") == "idn" and (product.get("attributes") or {}).get("externalId"):
                return {"external_id": product["attributes"]["externalId"]}
        return {"external_id": tenant.get("externalId"), "note": "no idn product externalId; using top-level value"}

    async def get_connector_form(self) -> dict:
        script = self.pb.settings["connector_script"]
        connector = await self.isc.get(self.paths("connector", script=quote(script)))
        form = await self.isc.get(self.paths("connector_form", script=quote(script)))
        fields = sorted(set(re.findall(r'<Field[^>]*\sname="([A-Za-z0-9_.-]+)"', form if isinstance(form, str) else "")))
        mapped = self.pb.settings.get("field_map") or {}
        missing = [key for key, field in mapped.items() if fields and field not in fields]
        return {"connector": script, "spec_id": connector.get("type"), "fields": fields,
                "mapped_fields_missing_from_form": missing}

    async def find_source(self, name: str) -> dict:
        found = await self.isc.get(f"{self.paths('sources')}?filters={_q(f'name eq \"{name}\"')}")
        if not found:
            return {"exists": False}
        src = found[0]
        return {"exists": True, "id": src["id"], "name": src["name"],
                "owner": (src.get("owner") or {}).get("name"), "created_in_this_session": self._own_source(src["id"])}

    async def get_task(self, task_id: str) -> dict:
        task = await self.isc.get(self.paths("task", task_id=quote(task_id)))
        return {"id": task_id, "completion_status": task.get("completionStatus"),
                "messages": [m.get("localizedText") or m.get("key") for m in task.get("messages") or []][:10]}

    # ---------------------------------------------------------------- writes
    async def _owner_id(self, owner: str) -> str:
        if re.fullmatch(r"[0-9a-f]{32}", owner):
            return owner
        field = "email" if "@" in owner else "alias"
        path = f"{self.paths('public_identities')}?filters={_q(f'{field} eq \"{owner}\"')}"
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
        hits = await self.isc.post(f"{self.paths('search')}?limit=2", json={
            "indices": ["identities"], "query": {"query": query}, "includeNested": False})
        return [{"id": h["id"]} for h in hits or [] if h.get("id")]

    async def create_source(self) -> dict:
        details = self.session["details"]
        name = details["source_name"]
        await self._step("source_created", "in_progress")
        started = self._begin()
        existing = await self.find_source(name)
        if existing["exists"]:
            if existing["created_in_this_session"]:
                await self._step("source_created", "passed")
                return {"created": False, "reused": True, "id": existing["id"], "name": name}
            await self._step("source_created", "failed")
            await self._action("create_source", "failed", source={"id": existing["id"], "name": name},
                               error=f"A source named '{name}' already exists and belongs to {existing['owner']}.",
                               request={"name": name}, started=started)
            return {"created": False, "owned_by": existing["owner"], "id": existing["id"],
                    "instruction": "Do not reuse or change it. Ask the IAM engineer for a different source name."}
        settings = self.pb.settings
        try:
            owner_id = await self._owner_id(details["source_owner"])
            spec_id = settings.get("spec_id") or (await self.get_connector_form())["spec_id"]
            ext_key = (settings.get("field_map") or {}).get("external_id")
            ext = (await self.get_tenant_external_id())["external_id"] if ext_key else None
            attrs = dict(settings.get("creation_only") or {})
            attrs["spConnectorSpecId"] = spec_id
            if ext:
                attrs[ext_key] = ext
            body = {"name": name, "description": settings.get("description", "Managed by the ISC Onboarding Agent"),
                    "owner": {"type": "IDENTITY", "id": owner_id}, "connector": settings["connector_script"],
                    "connectorAttributes": attrs}
            created = await self.isc.post(self.paths("sources"), json=body)
        except (IscError, ValueError, KeyError) as exc:
            await self._step("source_created", "failed")
            await self._action("create_source", "failed", source={"id": "", "name": name}, error=str(exc),
                               request={"name": name, "connector": settings.get("connector_script"),
                                        "owner": details.get("source_owner")}, started=started)
            return {"created": False, "error": str(exc)}
        source = {"id": created["id"], "name": created.get("name", name)}
        self.session["source"] = source
        await self.emit({"type": "source", **source})
        await self._step("source_created", "passed")
        instance = ((created.get("connectorAttributes") or {}).get("spConnectorInstanceId"))
        await self._action("create_source", "ok", source=source,
                           request={"name": name, "connector": settings["connector_script"],
                                    "owner": details.get("source_owner"), "spConnectorSpecId": spec_id,
                                    **({"external_id_set": bool(ext)} if ext_key else {})}, started=started)
        return {"created": True, **source, "connector_instance_linked": bool(instance)}

    def _settings_values(self) -> dict[str, Any]:
        """Map session details to connector attribute keys using settings.yaml `configure`."""
        details, values = self.session["details"], {}
        chosen = self._capabilities()
        extend = self._extend()
        for entry in self.pb.settings.get("configure") or []:
            cap = entry.get("capability")
            if cap and cap not in chosen:
                continue  # spec 002: only the chosen capabilities' settings
            if extend and (not cap or cap == "directory"):
                continue  # extend-source (FR-105): only the capabilities being added
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
        started = self._begin()
        values: dict[str, Any] = {}
        shown: dict[str, str] = {}
        secret_fields = self.pb.settings.get("secret_fields") or {}
        try:
            values = self._settings_values()
            ext_key = (self.pb.settings.get("field_map") or {}).get("external_id")
            if ext_key:
                ext = (await self.get_tenant_external_id())["external_id"]
                if ext:
                    values[ext_key] = ext
            if secret_fields and not self._extend():
                state = self._secret().get("state")
                if state in (None, "missing"):
                    raise ValueError("the Entra administrator hasn't put the secret in the secret field yet")
                if not (self.session.get("details") or {}).get("client_id"):
                    raise ValueError("the Application (client) ID isn't known yet: it is in the administrator's "
                                     "output of setup step 3")
            ops = [{"op": "add", "path": f"/connectorAttributes/{k}", "value": v} for k, v in values.items()]
            secret_ops, shown = await self._secret_ops()
            await self.isc.patch_json(self.paths("source", sid=quote(source["id"])), ops + secret_ops)
        except (IscError, ValueError, vault.VaultError) as exc:
            await self._step("configured", "failed")
            await self._action("configure_source", "failed", error=str(exc), request={"fields": values},
                               started=started)
            return {"configured": False, "error": str(exc)}
        await self._step("configured", "passed")
        extra: dict[str, Any] = {}
        if secret_fields:  # spec 002 R2/R18: the secret is shown as [vaulted]; the summary line
            extra = {"secret_applied": bool(shown),
                     "summary": ", ".join([f"{k}: [vaulted]" for k in shown] + [f"{len(values)} fields"])}
        await self._action("configure_source", "ok", request={"fields": {**values, **shown}}, started=started,
                           **extra)
        return {"configured": True, "fields": sorted(values), **({"secret_applied": bool(shown)} if secret_fields
                                                                 else {})}

    async def peek_accounts(self) -> dict:
        """The connection check: read a few accounts through the connector (more reliable than Test Connection for
        AWS SaaS before the first aggregation)."""
        source = self.session.get("source")
        if not source:
            return {"ok": False, "error": "no source has been created in this session yet"}
        check = (self.pb.checks.get("connection_check") or {})
        await self._step("connection_check", "in_progress")
        started = self._begin()
        request = {"objectType": check.get("object_type", "account"), "maxCount": check.get("max_count", 5)}
        try:
            if self.pb.settings.get("full_read"):  # spec 002: delta mode returns only changes (failure E8)
                async with self._full_read(quote(source["id"])):
                    result = await self.isc.post(self.paths("peek", sid=quote(source["id"])), json=request)
            else:
                result = await self.isc.post(self.paths("peek", sid=quote(source["id"])), json=request)
        except IscError as exc:
            await self._step("connection_check", "failed")
            await self._action("connection_check", "failed", error=str(exc), request=request, started=started)
            return {"ok": False, "error": str(exc)}
        objects = result.get("resourceObjects") or []
        if not objects:
            detail = str(result.get("details") or result)[:800]
            await self._step("connection_check", "failed")
            await self._action("connection_check", "failed", error=detail, request=request, counts={"accounts": 0},
                               started=started)
            return {"ok": False, "error": detail}
        await self._step("connection_check", "passed")
        await self._step("application_ready", "passed")
        await self._action("connection_check", "ok", request=request, counts={"accounts": len(objects)},
                           started=started)
        return {"ok": True, "accounts_read": len(objects),
                "sample": [o.get("identity") or o.get("name") for o in objects][:5]}

    async def _load(self, op: str, sid: str, fields: dict[str, str]) -> Any:
        """Start an aggregation: v2026 takes multipart/form-data; legacy takes a urlencoded form (accounts) or an empty
        body (entitlements), exactly as the AWS SaaS tools always sent."""
        path = self.paths(op, sid=sid)
        if self.paths.multipart_aggregation:
            return await self.isc.post_multipart(path, fields)
        if fields:
            return await self.isc.post(path, content="&".join(f"{k}={v}" for k, v in fields.items()),
                                       content_type="application/x-www-form-urlencoded")
        return await self.isc.post(path, content="")

    async def _wait(self, task_id: str) -> dict:
        for _ in range(self.poll_limit):
            task = await self.get_task(task_id)
            if task["completion_status"]:
                return task
            await asyncio.sleep(self.poll_seconds)
        return {"id": task_id, "completion_status": None, "messages": ["still running"]}

    async def start_aggregation(self) -> dict:
        if (self.pb.checks.get("aggregation") or {}).get("sequence"):
            return await self._aggregate_sequence()  # spec 002: entitlements, then accounts, each a full read
        source = self.session.get("source")
        if not source:
            return {"ok": False, "error": "no source has been created in this session yet"}
        sid = quote(source["id"])
        await self._step("aggregation", "in_progress")
        await self.emit({"type": "progress", "text": "aggregating accounts in SailPoint… (this can take minutes)"})
        started = self._begin()
        entitlements = bool((self.pb.checks.get("aggregation") or {}).get("entitlements", True))
        request = {"accounts": True, "entitlements": entitlements, "disableOptimization": True}
        task_ids: list[str] = []
        try:
            acct = await self._load("load_accounts", sid, {"disableOptimization": "true"})
            acct_id = (acct.get("task") or {}).get("id") or acct.get("id")
            if acct_id:
                task_ids.append(acct_id)
            await self._action("aggregate", "running", request=request, task_ids=task_ids, started=started)
            if entitlements:
                ent = await self._load("load_entitlements", sid, {})
                ent_id = (ent.get("task") or {}).get("id") or ent.get("id")
                if ent_id:
                    task_ids.append(ent_id)
            if entitlements:
                await self._action("aggregate", "running", request=request, task_ids=task_ids, started=started)
            results = [await self._wait(t) for t in task_ids]
        except IscError as exc:
            await self._step("aggregation", "failed")
            await self._action("aggregate", "failed", error=str(exc), request=request, task_ids=task_ids,
                               started=started)
            return {"ok": False, "error": str(exc), "task_ids": task_ids}
        failed = [r for r in results if r["completion_status"] not in ("SUCCESS", "WARNING")]
        if failed:
            error = "; ".join(f"{r['id']}: {r['completion_status']} {' '.join(map(str, r['messages']))}" for r in failed)
            await self._step("aggregation", "failed")
            await self._action("aggregate", "failed", error=error, request=request, task_ids=task_ids,
                               task_states={r["id"]: str(r["completion_status"]) for r in results}, started=started)
            return {"ok": False, "error": error, "task_ids": task_ids}
        field = self.paths.account_source_field
        accounts = await self.isc.get(
            f"{self.paths('accounts')}?filters={_q(f'{field} eq \"{source["id"]}\"')}&limit=250")
        await self._step("aggregation", "passed")
        await self._action("aggregate", "ok", request=request, task_ids=task_ids,
                           task_states={r["id"]: str(r["completion_status"]) for r in results},
                           counts={"accounts": len(accounts or [])}, started=started)
        return {"ok": True, "task_ids": task_ids, "accounts_on_source": len(accounts or [])}

    async def test_connection(self) -> dict:
        source = self.session.get("source")
        if not source:
            return {"ok": False, "error": "no source has been created in this session yet"}
        await self._step("test_connection", "in_progress")
        started = self._begin()
        try:
            result = await self.isc.post(self.paths("test", sid=quote(source["id"])))
        except IscError as exc:
            await self._step("test_connection", "failed")
            await self._action("test_connection", "failed", error=str(exc), started=started)
            return {"ok": False, "error": str(exc)}
        status = str(result.get("status", ""))
        if "SUCCESS" in status:
            await self._step("test_connection", "passed")
            await self._action("test_connection", "ok", task_states={"status": status}, started=started)
            return {"ok": True, "status": status}
        detail = str(result.get("details") or result)[:800]
        await self._step("test_connection", "failed")
        await self._action("test_connection", "failed", error=detail, task_states={"status": status}, started=started)
        hint = (self.pb.checks.get("test_connection") or {}).get("before_aggregation_hint")
        return {"ok": False, "status": status, "error": detail, "hint": hint if "req.input" in detail else None}

    async def delete_session_source(self) -> dict:
        source = self.session.get("source")
        if not source:
            return {"deleted": False, "error": "this session has not created a source"}
        if source.get("adopted"):  # spec 002 FR-105: an extended source is never deleted
            return {"deleted": False, "error": "this source was created in an earlier session and is only being "
                                               "extended; it is never deleted from here"}
        try:
            await self.isc.request("DELETE", self.paths("source", sid=quote(source["id"])))
        except IscError as exc:
            await self._action("delete_source", "failed", error=str(exc))
            return {"deleted": False, "error": str(exc)}
        await self._action("delete_source", "ok")
        self.session["source"] = None
        await self.emit({"type": "source", "id": "", "name": ""})
        return {"deleted": True, "next": "create_source then configure_source; the step chips update as they run"}

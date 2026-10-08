"""Generic ISC tools a playbook opts into (spec 002 research R5): offered to the model only when the playbook's
`checks.yaml` `tools` lists them, so AWS SaaS sees exactly its 001 tools (tests/unit/test_aws_unchanged.py).

Driven by playbook data, not connector names: the application secret (`settings.secret_fields`, read from the vault in
tool code only, R2), full reads around delta aggregation (`settings.full_read`), an aggregation sequence and an
in-turn wait limit (`checks.aggregation`), account-schema attributes (`checks.schema`), machine-identity datasets
(`checks.datasets`), provisioning policy and correlation (`checks.provisioning`), and extend-source (FR-105).
Ported from .claude/skills/sailpoint-isc-entra-connector/scripts/isc-source.sh (commits 9b53746, b71c53c).
"""

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from ..playbooks import chosen_capabilities
from . import vault
from .client import IscError

COUNT_SCAN_LIMIT = 10_000   # accounts read to count service principals apart from users (250 per page)
PAGE = 250


def _q(filter_expr: str) -> str:
    return quote(filter_expr, safe="")


def _task_id(result: Any) -> str | None:
    if not isinstance(result, dict):
        return None
    return (result.get("task") or {}).get("id") or result.get("id")


class PlaybookToolsMixin:
    """Mixed into IscTools; uses its isc, pb, paths, session, emit, _action, _begin, _step, get_task helpers."""

    if TYPE_CHECKING:  # provided by IscTools
        from ..playbooks import Playbook
        from .client import IscClient
        from .paths import Paths

        isc: IscClient
        pb: Playbook
        paths: Paths
        session: dict[str, Any]
        emit: Callable[[dict], Awaitable[None]]
        poll_seconds: float
        poll_limit: int

        async def _action(self, action: str, result: str, **kw: Any) -> None: ...
        def _begin(self) -> dict[str, Any]: ...
        async def _step(self, step: str, state: str) -> None: ...
        async def get_task(self, task_id: str) -> dict: ...
        async def _load(self, op: str, sid: str, fields: dict[str, str]) -> Any: ...
        async def _owner_id(self, owner: str) -> str: ...

    # ------------------------------------------------------------------ plan and capability helpers
    def _capabilities(self) -> list[str]:
        return chosen_capabilities(self.session)

    def _extend(self) -> bool:
        return self.session.get("mode") == "extend" or bool((self.session.get("source") or {}).get("adopted"))

    async def _plan(self, step_id: str, state: str, reason: str | None = None) -> None:
        """Mark a playbook plan step that has no milestone of its own (only if the session's plan has it)."""
        if not any(s.get("id") == step_id for s in self.session.get("plan") or []):
            return
        op: dict[str, Any] = {"op": "set_state", "step_id": step_id, "state": state}
        if reason:
            op["reason"] = reason[:160]
        await self.emit({"type": "plan", "ops": [op]})

    def _secret(self) -> dict:
        return self.session.get("application_secret") or {}

    async def _secret_ops(self) -> tuple[list[dict], dict[str, str]]:
        """PATCH ops for `settings.secret_fields` when a secret is waiting in the vault, and their [vaulted] view."""
        fields = self.pb.settings.get("secret_fields") or {}
        secret = self._secret()
        if not fields or secret.get("state") != "received":
            return [], {}
        value = await vault.get_application_secret(secret.get("provider", ""))
        ops = [{"op": "add", "path": f"/connectorAttributes/{field}", "value": value} for field in fields]
        return ops, {field: "[vaulted]" for field in fields}

    # ------------------------------------------------------------------ reads
    async def check_tenant_features(self) -> dict:
        """FR-130 / edge case: does this ISC tenant offer the connector, and (for AI agents) machine identities?"""
        script = self.pb.settings["connector_script"]
        out: dict[str, Any] = {}
        try:
            await self.isc.get(self.paths("connector", script=quote(script)))
            out["connector"] = True
        except IscError as exc:
            out["connector"] = False
            out["connector_error"] = str(exc)[:300]
        if "ai_agents" in self._capabilities():
            try:
                await self.isc.get(f"{self.paths('machine_identities')}?limit=1")
                out["machine_identities"] = True
            except IscError as exc:
                if exc.status in (403, 404):
                    out["machine_identities"] = False
                    out["machine_identities_note"] = "machine identity features not licensed on this ISC tenant"
                else:
                    raise
        return out

    async def find_connector_sources(self) -> dict:
        """Sources on this connector for the session's tenant (US1-5, FR-105), with owner."""
        name = self.pb.settings.get("connector_name") or self.pb.settings["connector_script"]
        field = self.pb.settings.get("tenant_match_field", "domainName")
        tenant = str((self.session.get("details") or {}).get("tenant_domain") or "").lower()
        found = await self.isc.get(
            f"{self.paths('sources')}?filters={_q(f'connectorName eq \"{name}\"')}&limit=250")
        mine = (self.session.get("source") or {}).get("id")
        out = []
        for src in found or []:
            value = str((src.get("connectorAttributes") or {}).get(field) or "").lower()
            if tenant and value == tenant:
                out.append({"id": src["id"], "name": src.get("name"), "owner": (src.get("owner") or {}).get("name"),
                            "created_in_this_session": src["id"] == mine})
        return {"tenant": tenant, "sources": out}

    async def read_source_setup(self) -> dict:
        """What the session's source has: capability toggles, schema attributes, provisioning policy, correlation."""
        source = self.session.get("source")
        if not source:
            return {"error": "no source in this session yet"}
        sid = quote(source["id"])
        src = await self.isc.get(self.paths("source", sid=sid))
        attrs = src.get("connectorAttributes") or {}
        toggles = {e["field"]: attrs.get(e["field"]) for e in self.pb.settings.get("configure") or []
                   if e.get("capability") and e["field"] in attrs}
        out: dict[str, Any] = {"toggles": toggles}
        try:
            schemas = await self.isc.get(self.paths("schemas", sid=sid))
            account = next((s for s in schemas or [] if s.get("nativeObjectType") == "User"
                            or s.get("name") == "account"), None)
            out["schema_attributes"] = sorted(a.get("name") for a in (account or {}).get("attributes") or [])
        except IscError as exc:
            out["schema_error"] = str(exc)[:200]
        try:
            policies = await self.isc.get(self.paths("provisioning_policies", sid=sid))
            out["provisioning_policy"] = any(p.get("usageType") == "CREATE" for p in policies or [])
        except IscError:
            out["provisioning_policy"] = None
        try:
            corr = await self.isc.get(self.paths("correlation", sid=sid))
            out["correlation"] = bool((corr or {}).get("attributeAssignments"))
        except IscError:
            out["correlation"] = None
        return out

    # ------------------------------------------------------------------ full read around delta aggregation
    @asynccontextmanager
    async def _full_read(self, sid: str):  # type: ignore[no-untyped-def]
        """Switch delta aggregation off for the call and restore the previous value even on error (skill fix: in delta
        mode peek and aggregation return only changes)."""
        cfg = self.pb.settings.get("full_read") or {}
        field = cfg.get("field")
        if not field:
            yield False
            return
        src = await self.isc.get(self.paths("source", sid=sid))
        before = (src.get("connectorAttributes") or {}).get(field)
        toggled = before is True
        if toggled:
            await self.isc.patch_json(self.paths("source", sid=sid),
                                      [{"op": "add", "path": f"/connectorAttributes/{field}", "value": False}])
        try:
            yield toggled
        finally:
            if toggled:
                await self.isc.patch_json(self.paths("source", sid=sid),
                                          [{"op": "add", "path": f"/connectorAttributes/{field}", "value": True}])

    # ------------------------------------------------------------------ counts
    async def _count_accounts(self, sid_raw: str) -> dict[str, int | None]:
        field = self.paths.account_source_field
        flt = f'{field} eq "{sid_raw}"'
        total = await self.isc.count(self.paths("accounts"), flt)
        counts: dict[str, int | None] = {"accounts": total}
        schema = (self.pb.checks.get("schema") or {}).get("service_principals") or {}
        attr = schema.get("type_attribute")
        if attr and "service_principals" in self._capabilities():
            if total > COUNT_SCAN_LIMIT:
                counts["service_principals"] = None
            else:
                sps = 0
                for offset in range(0, total, PAGE):
                    page = await self.isc.get(
                        f"{self.paths('accounts')}?filters={_q(flt)}&limit={PAGE}&offset={offset}")
                    sps += sum(1 for a in page or [] if (a.get("attributes") or {}).get(attr))
                counts["service_principals"] = sps
                counts["users"] = total - sps
        else:
            counts["users"] = total
        return counts

    async def _count_entitlements(self, sid_raw: str) -> int:
        return await self.isc.count(self.paths("entitlements"), f'source.id eq "{sid_raw}"')

    # ------------------------------------------------------------------ aggregation sequence (Entra: entitlements, accounts)
    async def _wait_limited(self, task_id: str, seconds: float) -> dict:
        deadline = time.monotonic() + seconds
        while True:
            task = await self.get_task(task_id)
            if task["completion_status"] or time.monotonic() >= deadline:
                return task
            await asyncio.sleep(self.poll_seconds)

    async def _aggregate_sequence(self) -> dict:
        source = self.session.get("source")
        if not source:
            return {"ok": False, "error": "no source has been created in this session yet"}
        cfg = self.pb.checks.get("aggregation") or {}
        wait = float(cfg.get("wait_in_turn_seconds", self.poll_limit * self.poll_seconds))
        sid = quote(source["id"])
        await self._step("aggregation", "in_progress")
        results: dict[str, Any] = {"ok": True}
        for kind in cfg.get("sequence") or ["entitlements", "accounts"]:
            action = f"aggregate_{kind}"
            plan_step = f"aggregate_{kind}"
            await self._plan(plan_step, "in_progress")
            await self.emit({"type": "progress", "text": f"aggregating {kind} in SailPoint… (this can take minutes)"})
            started = self._begin()
            request: dict[str, Any] = {"kind": kind, "full_read": kind == "accounts"}
            task_id = None
            try:
                if kind == "accounts":
                    async with self._full_read(sid) as toggled:
                        request["delta_toggled"] = toggled
                        task_id = _task_id(await self._load("load_accounts", sid, {"disableOptimization": "true"}))
                        await self._action(action, "running", request=request, task_ids=[task_id or ""],
                                           started=started)
                        task = await self._wait_limited(task_id, wait) if task_id else {"completion_status": None}
                else:
                    task_id = _task_id(await self._load("load_entitlements", sid, {}))
                    await self._action(action, "running", request=request, task_ids=[task_id or ""], started=started)
                    task = await self._wait_limited(task_id, wait) if task_id else {"completion_status": None}
            except IscError as exc:
                await self._step("aggregation", "failed")
                await self._plan(plan_step, "failed", str(exc)[:150])
                await self._action(action, "failed", error=str(exc), request=request, task_ids=[task_id or ""],
                                   started=started)
                return {"ok": False, "failed": kind, "error": str(exc)}
            status = task.get("completion_status")
            if status is None:
                nxt = self._next_after("start_aggregation") if kind == (cfg.get("sequence") or ["accounts"])[-1] \
                    else "start_aggregation"
                await self._action(action, "running", request=request, task_ids=[task_id or ""], started=started,
                                   follow=True, plan_step=plan_step, next_step=nxt,
                                   summary=f"{kind} aggregation still running in SailPoint")
                return {"ok": None, "running": True, "kind": kind, "task_ids": [task_id],
                        "note": "Still running. The session follows it and posts the result in the IAM engineer's "
                                "thread when it ends; tell the IAM engineer that and stop here."}
            if status not in ("SUCCESS", "WARNING"):
                error = f"{task_id}: {status} {' '.join(map(str, task.get('messages') or []))}"
                await self._step("aggregation", "failed")
                await self._plan(plan_step, "failed", error[:150])
                await self._action(action, "failed", error=error, request=request, task_ids=[task_id or ""],
                                   task_states={task_id: str(status)}, started=started)
                return {"ok": False, "failed": kind, "error": error, "messages": task.get("messages")}
            if kind == "accounts":
                counts = await self._count_accounts(source["id"])
                summary = (("delta off → restored · " if request.get("delta_toggled") else "")
                           + f"{counts['accounts']:,} accounts")
            else:
                counts = {"entitlements": await self._count_entitlements(source["id"])}
                summary = f"{counts['entitlements']:,} entitlements"
            await self._action(action, "ok", request=request, task_ids=[task_id or ""],
                               task_states={task_id: str(status)}, counts={k: v for k, v in counts.items()
                                                                           if v is not None},
                               started=started, summary=summary)
            if kind != "accounts":
                await self._plan(plan_step, "done")
            results[kind] = {"status": status, **counts, "messages": (task.get("messages") or [])[:5]}
        await self._step("aggregation", "passed")
        return results

    def _next_after(self, tool: str) -> str | None:
        order = list(self.pb.checks.get("order") or [])
        rest = order[order.index(tool) + 1:] if tool in order else []
        for name in rest:
            if name == "aggregate_datasets" and "ai_agents" not in self._capabilities():
                continue
            if name == "set_dataset_schedule" and "ai_agents" not in self._capabilities():
                continue
            return name
        return None

    # ------------------------------------------------------------------ schema attributes (service principals)
    async def ensure_schema_attributes(self, capability: str = "service_principals") -> dict:
        source = self.session.get("source")
        if not source:
            return {"ok": False, "error": "no source has been created in this session yet"}
        if capability not in self._capabilities():
            return {"ok": True, "skipped": f"{capability} isn't chosen for this session"}
        cfg = (self.pb.checks.get("schema") or {}).get(capability) or {}
        if not cfg:
            return {"ok": False, "error": f"no schema attributes for {capability}"}
        sid = quote(source["id"])
        await self._plan("sp_schema", "in_progress")
        started = self._begin()
        wanted = self.pb.asset(cfg["asset"])["attributes"]
        try:
            schemas = await self.isc.get(self.paths("schemas", sid=sid))
            account = next((s for s in schemas or [] if s.get("name") == "account"), None) or \
                next((s for s in schemas or [] if s.get("nativeObjectType") == "User"), None)
            if not account:
                raise ValueError("the source has no account schema yet")
            by_name = {s.get("name"): s for s in schemas or []}
            have = {a.get("name") for a in account.get("attributes") or []}
            ops: list[dict[str, Any]] = []
            for attr in wanted:
                if attr["name"] in have:
                    continue
                value = {k: attr[k] for k in ("name", "type", "isMulti", "isEntitlement", "description") if k in attr}
                target = by_name.get(attr.get("schema")) if attr.get("schema") else None
                if attr.get("schema") and target:
                    value["schema"] = {"type": "CONNECTOR_SCHEMA", "id": target.get("id"), "name": target.get("name")}
                elif attr.get("schema"):
                    value["isEntitlement"] = False  # its entitlement schema isn't on this source
                ops.append({"op": "add", "path": "/attributes/-", "value": value})
            if ops:
                await self.isc.patch_json(self.paths("schema", sid=sid, schema_id=quote(account["id"])), ops)
        except (IscError, ValueError, KeyError) as exc:
            await self._plan("sp_schema", "failed", str(exc)[:150])
            await self._action("ensure_schema_attributes", "failed", error=str(exc), request={"capability": capability},
                               started=started)
            return {"ok": False, "error": str(exc)}
        added = [str(op["value"]["name"]) for op in ops]
        await self._plan("sp_schema", "done")
        await self._action("ensure_schema_attributes", "ok", request={"capability": capability, "added": added},
                           started=started, summary=f"{len(added)} attributes added")
        return {"ok": True, "added": added, "already_present": len(wanted) - len(added)}

    # ------------------------------------------------------------------ datasets (AI agents)
    async def aggregate_datasets(self) -> dict:
        source = self.session.get("source")
        if not source:
            return {"ok": False, "error": "no source has been created in this session yet"}
        cfg = self.pb.checks.get("datasets") or {}
        sid = quote(source["id"])
        src = await self.isc.get(self.paths("source", sid=sid))
        attrs = src.get("connectorAttributes") or {}
        ids = [d["id"] for cap, d in cfg.items() if isinstance(d, dict) and d.get("toggle") and cap in
               self._capabilities() and attrs.get(d["toggle"])]
        ids += [ds for ds, toggle in (cfg.get("others") or {}).items() if attrs.get(toggle)]
        if not ids:
            return {"ok": True, "datasets": [], "note": "no dataset is switched on for this source"}
        main = next((d for d in cfg.values() if isinstance(d, dict) and d.get("id") in ids), {})
        plan_step = main.get("plan_step")
        if plan_step:
            await self._plan(plan_step, "in_progress")
        started = self._begin()
        request = {"datasetIds": ids, "disableOptimization": False}
        try:
            result = await self.isc.post(self.paths("aggregate_agents", sid=sid), json=request)
        except IscError as exc:
            if exc.status == 404 and str(cfg.get("unavailable_signature", "")).lower() in str(exc).lower():
                ui_path = self.pb.render(main.get("ui_path") or "")
                await self._action("aggregate_datasets", "tenant_limitation", request=request, error=None,
                                   started=started, summary="tenant limitation · 404 endpoint unavailable",
                                   plan_step=plan_step,
                                   waiting_reason="start the Foundry aggregation in ISC, then say \"done\"")
                return {"ok": None, "tenant_limitation": True, "ui_path": ui_path,
                        "note": "This ISC tenant doesn't let automation start dataset aggregation. Not a setup error "
                                "and not a failed onboarding: ask the IAM engineer to start it at ui_path, then call "
                                "count_ai_agents when they say it's done."}
            if plan_step:
                await self._plan(plan_step, "failed", str(exc)[:150])
            await self._action("aggregate_datasets", "failed", error=str(exc), request=request, started=started)
            return {"ok": False, "error": str(exc)}
        task_id = _task_id(result)
        task = await self._wait_limited(task_id, float((self.pb.checks.get("aggregation") or {}).get(
            "wait_in_turn_seconds", 180))) if task_id else {"completion_status": "SUCCESS"}
        status = task.get("completion_status")
        if status not in ("SUCCESS", "WARNING", None):
            error = f"{task_id}: {status} {' '.join(map(str, task.get('messages') or []))}"
            if plan_step:
                await self._plan(plan_step, "failed", error[:150])
            await self._action("aggregate_datasets", "failed", error=error, request=request,
                               task_ids=[task_id or ""], started=started)
            return {"ok": False, "error": error, "messages": task.get("messages")}
        count = await self.count_ai_agents(record=False)
        await self._action("aggregate_datasets", "ok" if status else "running", request=request,
                           task_ids=[task_id or ""], counts={"ai_agents": count["ai_agents"]}, started=started,
                           summary=f"{count['ai_agents']} AI agents", plan_step=plan_step,
                           **({"follow": True, "next_step": "set_dataset_schedule"} if not status else {}))
        if status and plan_step:
            await self._plan(plan_step, "done")
        return {"ok": True if status else None, "datasets": ids, **count}

    async def count_ai_agents(self, record: bool = True) -> dict:
        """AI agents now on the source (machine identities from its Foundry dataset); after a tenant-limitation run in
        the ISC interface, `record` makes it an action that completes the step."""
        source = self.session.get("source")
        if not source:
            return {"ai_agents": 0}
        cfg = self.pb.checks.get("datasets") or {}
        foundry = next((d for d in cfg.values() if isinstance(d, dict) and d.get("id")), {})
        items: list = []
        offset = 0
        while True:
            page = await self.isc.get(f"{self.paths('machine_identities')}?filters="
                                      f"{_q(f'source.id eq \"{source['id']}\"')}&limit={PAGE}&offset={offset}")
            items += page or []
            if not page or len(page) < PAGE or offset > COUNT_SCAN_LIMIT:
                break
            offset += PAGE
        count = sum(1 for i in items if not foundry.get("id") or i.get("datasetId") == foundry["id"])
        if record:
            started = self._begin()
            await self._action("aggregate_datasets", "ok" if count else "failed", counts={"ai_agents": count},
                               request={"read": "machine-identities"}, started=started,
                               error=None if count else "no AI agents yet: the aggregation may still be running",
                               summary=f"{count} AI agents")
            if count and foundry.get("plan_step"):
                await self._plan(foundry["plan_step"], "done")
        return {"ai_agents": count}

    async def set_dataset_schedule(self, dataset_id: str = "azure:foundry", on: bool = True) -> dict:
        if "ai_agents" not in self._capabilities():
            return {"ok": True, "skipped": "AI agents aren't chosen for this session"}
        source = self.session.get("source")
        if not source:
            return {"ok": False, "error": "no source has been created in this session yet"}
        sid = quote(source["id"])
        started = self._begin()
        path = self.paths("dataset", sid=sid, dataset_id=quote(dataset_id, safe=":"))
        try:
            current = await self.isc.get(path)
            body = {**(current or {}), "aggregationEnabled": bool(on)}
            await self.isc.request("PUT", path, json=body)
        except IscError as exc:
            await self._action("set_dataset_schedule", "failed", error=str(exc),
                               request={"dataset": dataset_id, "aggregationEnabled": on}, started=started)
            return {"ok": False, "error": str(exc)}
        await self._plan("foundry_schedule", "done" if on else "skipped", None if on else "schedule switched off")
        await self._action("set_dataset_schedule", "ok", request={"dataset": dataset_id, "aggregationEnabled": on},
                           started=started, summary=f"{dataset_id} schedule {'on' if on else 'off'}")
        return {"ok": True, "dataset": dataset_id, "schedule": "on" if on else "off"}

    # ------------------------------------------------------------------ provisioning (no directory write here)
    async def set_provisioning_policy(self, replace: bool = False) -> dict:
        if "provisioning" not in self._capabilities():
            return {"ok": True, "skipped": "provisioning isn't chosen for this session"}
        source = self.session.get("source")
        if not source:
            return {"ok": False, "error": "no source has been created in this session yet"}
        details = self.session.get("details") or {}
        if not details.get("upn_domain") or not details.get("usage_location"):
            return {"ok": False, "error": "the session has no domain for new accounts or usage location"}
        cfg = self.pb.checks.get("provisioning") or {}
        sid = quote(source["id"])
        started = self._begin()
        policy = self.pb.asset(cfg["policy"])
        request = {"usageType": "CREATE", "upn_domain": details["upn_domain"],
                   "usage_location": details["usage_location"], "replace": replace}
        try:
            existing = await self.isc.get(self.paths("provisioning_policies", sid=sid))
            has_create = any(p.get("usageType") == "CREATE" for p in existing or [])
            if has_create and not replace:
                kept = True
            elif has_create:
                await self.isc.request("PUT", self.paths("provisioning_policy", sid=sid, usage="CREATE"), json=policy)
                kept = False
            else:
                await self.isc.post(self.paths("provisioning_policies", sid=sid), json=policy)
                kept = False
        except IscError as exc:
            await self._action("set_provisioning_policy", "failed", error=str(exc), request=request, started=started)
            return {"ok": False, "error": str(exc)}
        await self._action("set_provisioning_policy", "ok", request={**request, "kept_existing": kept},
                           started=started, summary="existing CREATE policy kept" if kept else
                           f"CREATE policy · @{details['upn_domain']}")
        return {"ok": True, "kept_existing": kept}

    async def set_correlation(self) -> dict:
        if "provisioning" not in self._capabilities():
            return {"ok": True, "skipped": "provisioning isn't chosen for this session"}
        source = self.session.get("source")
        if not source:
            return {"ok": False, "error": "no source has been created in this session yet"}
        cfg = self.pb.checks.get("provisioning") or {}
        sid = quote(source["id"])
        started = self._begin()
        try:
            current = await self.isc.get(self.paths("correlation", sid=sid)) or {}
            body = self.pb.asset(cfg["correlation"], correlation_config_id=current.get("id", ""),
                                 correlation_config_name=current.get("name", ""))
            if not body.get("id"):
                body.pop("id", None)
                body.pop("name", None)
            await self.isc.request("PUT", self.paths("correlation", sid=sid), json=body)
        except IscError as exc:
            await self._action("set_correlation", "failed", error=str(exc), request={}, started=started)
            return {"ok": False, "error": str(exc)}
        rules = [f"{a['property']} = {a['value']}" for a in body.get("attributeAssignments") or []]
        await self._plan("provisioning_policy", "done")
        await self._action("set_correlation", "ok", request={"rules": rules}, started=started,
                           summary=" then ".join(rules)[:80])
        return {"ok": True, "rules": rules}

    async def lifecycle_review(self) -> dict:
        """The leaver actions to show the IAM engineer for review; never applied (FR-136)."""
        cfg = self.pb.checks.get("provisioning") or {}
        return {"template": json.loads(self.pb.assets.get(cfg.get("lifecycle_template", ""), "[]") or "[]"),
                "note": "Merge with the identity profile's existing account actions; the IAM engineer applies it."}

    # ------------------------------------------------------------------ extend-source and secret
    async def adopt_source(self, source_id: str) -> dict:
        """FR-105: bind an existing source of this connector, for this tenant and owner, to the session."""
        started = self._begin()
        try:
            src = await self.isc.get(self.paths("source", sid=quote(source_id)))
            owner_id = await self._owner_id((self.session.get("details") or {}).get("source_owner", ""))
        except (IscError, ValueError) as exc:
            await self._action("adopt_source", "failed", error=str(exc), request={"source_id": source_id},
                               started=started)
            return {"adopted": False, "error": str(exc)}
        field = self.pb.settings.get("tenant_match_field", "domainName")
        tenant = str((self.session.get("details") or {}).get("tenant_domain") or "").lower()
        problems = []
        if src.get("connector") != self.pb.settings["connector_script"]:
            problems.append(f"it uses the {src.get('connector')} connector, not {self.pb.settings['connector_script']}")
        if str((src.get("connectorAttributes") or {}).get(field) or "").lower() != tenant:
            problems.append(f"it is for another tenant ({(src.get('connectorAttributes') or {}).get(field)})")
        if (src.get("owner") or {}).get("id") != owner_id:
            problems.append(f"it belongs to {(src.get('owner') or {}).get('name')}, not the session's source owner")
        source = {"id": src["id"], "name": src.get("name")}
        if problems:
            await self._action("adopt_source", "failed", source=source, error="; ".join(problems),
                               request={"source_id": source_id}, started=started)
            return {"adopted": False, "refused": problems,
                    "instruction": "Do not change this source. Tell the IAM engineer why."}
        self.session["source"] = {**source, "adopted": True}
        self.session["mode"] = "extend"
        await self.emit({"type": "source", **source, "adopted": True})
        await self._action("adopt_source", "ok", source=source, request={"source_id": source_id}, started=started,
                           summary=f"extending {source['name']}")
        return {"adopted": True, **source,
                "next": "read_source_setup, skip the capability steps already on the source with update_plan, then "
                        "configure_source and the checks for the added capabilities"}

    async def apply_application_secret(self) -> dict:
        """FR-122: put the newly submitted secret into the session's source (only the secret field)."""
        source = self.session.get("source")
        if not source:
            return {"applied": False, "error": "no source in this session yet; configure_source will use it"}
        started = self._begin()
        try:
            ops, shown = await self._secret_ops()
            if not ops:
                return {"applied": False, "error": "no new secret is waiting in the secret field"}
            await self.isc.patch_json(self.paths("source", sid=quote(source["id"])), ops)
        except (IscError, vault.VaultError) as exc:
            await self._action("apply_application_secret", "failed", error=str(exc), request={"fields": {}},
                               started=started)
            return {"applied": False, "error": str(exc)}
        await self._action("apply_application_secret", "ok", request={"fields": shown}, started=started,
                           summary=", ".join(f"{k}: [vaulted]" for k in shown), secret_applied=True)
        return {"applied": True, "secret_applied": True,
                "next": "rerun peek_accounts and test_connection"}

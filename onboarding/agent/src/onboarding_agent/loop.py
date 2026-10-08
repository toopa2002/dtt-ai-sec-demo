"""Claude Haiku tool loop (research R5, contracts/agent-invocation.md).

Streams text deltas as they arrive and a progress line before each tool. At most MAX_ROUNDS tool rounds per turn.
Prompt caching (Constitution IV, research R27): the system prompt is a static block (rules + playbook, the same for
every turn of a session) and a per-turn block; cache breakpoints sit on the last tool, the static block and the
conversation's last block each round, so each round re-reads the previous one's prefix from the cache.
Role gate (FR-016, FR-016a, FR-019): SailPoint write tools are offered to the model only when the message that
started the turn came from the IAM engineer; an application owner's turn gets the check tools only under the IAM
engineer's standing check order. There are no application-side (AWS) tools at all (FR-010). The streamed reply
always lands in the writer's thread; the thread tools in tools/session.py are the only way to the other thread.
"""

import json
import os
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from .isc.tools import WRITE_TOOLS, IscTools
from .playbooks import Playbook
from .tools.session import SessionTools

Emit = Callable[[dict], Awaitable[None]]
MAX_ROUNDS = 8
MAX_TOKENS = 2048
PROMPTS = Path(__file__).parent / "prompts"

TOOL_SPECS: dict[str, dict[str, Any]] = {
    "post_to_other_thread": {
        "description": "Post a message in the OTHER participant's thread (a request, a step, a relayed message) and "
                       "leave a one-line relay note in this thread saying what you asked or passed on.",
        "input_schema": {"type": "object", "properties": {
            "text": {"type": "string", "description": "The message for the other participant, Markdown."},
            "relay_note": {"type": "string", "description": "One line for THIS thread, e.g. 'Asked the AWS owner to "
                                                            "run the read-only get-role check.'"},
            "relayed_from": {"type": "string", "enum": ["iam_engineer", "application_owner"],
                             "description": "Set when you are passing on what the writer said to the other person."}},
            "required": ["text", "relay_note"]}, "progress": None},
    "notify_other_thread": {
        "description": "Leave a one-line note in the OTHER participant's thread about news that belongs in this "
                       "thread (for example 'All checks passed; the source is ready'). Never the full text twice.",
        "input_schema": {"type": "object", "properties": {"relay_note": {"type": "string"}},
                         "required": ["relay_note"]}, "progress": None},
    "set_waiting": {
        "description": "Say who you are waiting for now and what they need to do next; both screens show it in a "
                       "banner (information only; it never holds anyone's message back). Use null when you wait "
                       "for nobody.",
        "input_schema": {"type": "object", "properties": {
            "on": {"type": ["string", "null"], "enum": ["iam_engineer", "application_owner", None]},
            "reason": {"type": "string", "description": "The awaited person's next step, one short imperative line "
                       "(max 120 characters), e.g. 'run step 4 and paste the output'."}},
            "required": ["on"]}, "progress": None},
    "suggest_replies": {
        "description": "Offer 3 short replies the participant in `thread` could send next (answers to what you "
                       "asked, the next likely order for the IAM engineer, common questions). Call it at the end "
                       "of your turn for this thread, and for the other thread if you asked them something.",
        "input_schema": {"type": "object", "properties": {
            "thread": {"type": "string", "enum": ["iam_engineer", "application_owner"]},
            "items": {"type": "array", "minItems": 1, "maxItems": 5, "items": {
                "type": "object", "properties": {"text": {"type": "string", "maxLength": 200},
                                                 "kind": {"type": "string", "enum": ["answer", "order", "question"]}},
                "required": ["text", "kind"]}}},
            "required": ["thread", "items"]}, "progress": None},
    "update_plan": {
        "description": "Keep the shared plan current (both screens show it): mark steps as you work, add a step "
                       "(e.g. a fix you found, assigned to who must do it) or skip one. Every add or skip, and every "
                       "step set failed or blocked, needs a one-line reason. Steps are never removed.",
        "input_schema": {"type": "object", "properties": {"ops": {"type": "array", "minItems": 1, "items": {
            "type": "object", "properties": {
                "op": {"type": "string", "enum": ["set_state", "add", "skip"]},
                "step_id": {"type": "string", "description": "for set_state and skip: the step's id from the plan"},
                "state": {"type": "string", "enum": ["todo", "in_progress", "done", "failed", "blocked"]},
                "after": {"type": "string", "description": "for add: the id of the step it goes after"},
                "title": {"type": "string", "maxLength": 120, "description": "for add"},
                "actor": {"type": "string", "enum": ["application_owner", "iam_engineer", "agent"]},
                "kind": {"type": "string", "enum": ["read_only", "change"]},
                "milestone": {"type": "string", "enum": ["application_ready", "source_created", "configured",
                                                         "connection_check", "aggregation", "test_connection"]},
                "reason": {"type": "string", "maxLength": 160}},
            "required": ["op"]}}}, "required": ["ops"]}, "progress": None},
    "note_diagnosis": {
        "description": "After a SailPoint action failed and you found the cause: one paragraph saying which side and "
                       "why, stored with that action's details for the IAM engineer.",
        "input_schema": {"type": "object", "properties": {"text": {"type": "string", "maxLength": 1000}},
                         "required": ["text"]}, "progress": None},
    "get_tenant_external_id": {"description": "Read the tenant's External ID (needed in the AWS role trust).",
                               "input_schema": {"type": "object", "properties": {}},
                               "progress": "reading the tenant External ID in SailPoint…"},
    "get_connector_form": {"description": "Read the connector's current settings form and check the field mapping.",
                           "input_schema": {"type": "object", "properties": {}},
                           "progress": "reading the connector form in SailPoint…"},
    "find_source": {"description": "Look up a SailPoint source by name and its owner.",
                    "input_schema": {"type": "object", "properties": {"name": {"type": "string"}},
                                     "required": ["name"]}, "progress": "looking up the source in SailPoint…"},
    "get_task": {"description": "Read a SailPoint task's status.",
                 "input_schema": {"type": "object", "properties": {"task_id": {"type": "string"}},
                                  "required": ["task_id"]}, "progress": "checking the task in SailPoint…"},
    "create_source": {"description": "Create the session's source in SailPoint (refuses a same-name source owned by "
                                     "someone else).", "input_schema": {"type": "object", "properties": {}},
                      "progress": "creating the source in SailPoint…"},
    "configure_source": {"description": "Configure the session's source with the session values.",
                         "input_schema": {"type": "object", "properties": {}},
                         "progress": "configuring the source in SailPoint…"},
    "peek_accounts": {"description": "Connection check: read a few accounts through the connector.",
                      "input_schema": {"type": "object", "properties": {}},
                      "progress": "running the connection check in SailPoint…"},
    "start_aggregation": {"description": "Run account and entitlement aggregation and wait for the result.",
                          "input_schema": {"type": "object", "properties": {}},
                          "progress": "starting aggregation in SailPoint…"},
    "test_connection": {"description": "Run Test Connection on the source.",
                        "input_schema": {"type": "object", "properties": {}},
                        "progress": "running Test Connection in SailPoint…"},
    "delete_session_source": {"description": "Delete the source created in this session (only on the IAM engineer's "
                                             "explicit order; for a broken connector instance).",
                              "input_schema": {"type": "object", "properties": {}},
                              "progress": "deleting the session's source in SailPoint…"},
    # spec 002: generic tools offered only to playbooks that list them in checks.yaml `tools` (research R5)
    "check_tenant_features": {"description": "At session start: check this ISC tenant offers the connector and, for AI "
                                             "agents, the machine identity features.",
                              "input_schema": {"type": "object", "properties": {}},
                              "progress": "checking what this SailPoint tenant offers…"},
    "find_connector_sources": {"description": "List existing sources on this connector for the session's tenant, "
                                              "with owner (call at session start; offer extend-source if one exists).",
                               "input_schema": {"type": "object", "properties": {}},
                               "progress": "looking for existing sources for this tenant in SailPoint…"},
    "adopt_source": {"description": "Extend-source: bind an existing source of this connector, tenant and source "
                                    "owner to the session (refuses anyone else's source). Only on the IAM engineer's "
                                    "order.",
                     "input_schema": {"type": "object", "properties": {"source_id": {"type": "string"}},
                                      "required": ["source_id"]},
                     "progress": "checking the existing source in SailPoint…"},
    "read_source_setup": {"description": "Read what the session's source has: capability settings, account schema "
                                         "attributes, account-creation policy and matching rule.",
                          "input_schema": {"type": "object", "properties": {}},
                          "progress": "reading the source's settings in SailPoint…"},
    "ensure_schema_attributes": {"description": "Add the missing service-principal attributes to the source's account "
                                                "model (never changes existing ones).",
                                 "input_schema": {"type": "object", "properties": {
                                     "capability": {"type": "string", "enum": ["service_principals"]}}},
                                 "progress": "updating the account model in SailPoint…"},
    "aggregate_datasets": {"description": "Aggregate the source's switched-on machine-identity datasets (Azure AI "
                                          "Foundry agents) and report how many AI agents were found.",
                           "input_schema": {"type": "object", "properties": {}},
                           "progress": "aggregating AI agents in SailPoint…"},
    "count_ai_agents": {"description": "Count the AI agents now on the source, e.g. after the IAM engineer started the "
                                       "dataset aggregation in ISC.",
                        "input_schema": {"type": "object", "properties": {}},
                        "progress": "counting AI agents in SailPoint…"},
    "set_dataset_schedule": {"description": "Turn a dataset's scheduled aggregation on or off.",
                             "input_schema": {"type": "object", "properties": {
                                 "dataset_id": {"type": "string", "default": "azure:foundry"},
                                 "on": {"type": "boolean", "default": True}}},
                             "progress": "updating the dataset schedule in SailPoint…"},
    "set_provisioning_policy": {"description": "Set how SailPoint creates new accounts (keeps an existing definition "
                                               "unless replace is true and the IAM engineer asked for it).",
                                "input_schema": {"type": "object", "properties": {"replace": {"type": "boolean"}}},
                                "progress": "setting the account-creation policy in SailPoint…"},
    "set_correlation": {"description": "Set how accounts are matched to identities.",
                        "input_schema": {"type": "object", "properties": {}},
                        "progress": "setting account matching in SailPoint…"},
    "lifecycle_review": {"description": "The prepared leaver (lifecycle-state) account actions to show the IAM engineer "
                                        "for review. Never applied by the agent.",
                         "input_schema": {"type": "object", "properties": {}}, "progress": None},
    "apply_application_secret": {"description": "Put the new secret from the secret field into the session's source "
                                                "(the value never passes through you).",
                                 "input_schema": {"type": "object", "properties": {}},
                                 "progress": "applying the new secret in SailPoint…"},
    "record_application_id": {"description": "Record the Application (client) ID shown in the Entra administrator's "
                                             "output of the app registration step (not a secret).",
                              "input_schema": {"type": "object", "properties": {"client_id": {"type": "string"}},
                                               "required": ["client_id"]}, "progress": None},
    "request_new_secret": {"description": "Ask the application owner for a new client secret through the secret field "
                                          "(invalid, expired or exposed secret). Never ask for it in the chat.",
                           "input_schema": {"type": "object", "properties": {
                               "reason": {"type": "string", "maxLength": 160}}, "required": ["reason"]},
                           "progress": None},
}

CACHE = {"type": "ephemeral"}
USAGE_FIELDS = (("input_tokens", "input_tokens"), ("cache_creation_input_tokens", "cache_write_tokens"),
                ("cache_read_input_tokens", "cache_read_tokens"), ("output_tokens", "output_tokens"))
ROLE_LABEL = {"iam_engineer": "IAM engineer", "application_owner": "application owner"}
SESSION_TOOLS = ("post_to_other_thread", "notify_other_thread", "set_waiting", "suggest_replies", "update_plan",
                 "note_diagnosis")
# The ISC tools of 001, offered to a playbook without a `checks.yaml` `tools` list (AWS SaaS, unchanged: SC-106).
LEGACY_ISC_TOOLS = ("get_tenant_external_id", "get_connector_form", "find_source", "get_task", "create_source",
                    "configure_source", "peek_accounts", "start_aggregation", "test_connection",
                    "delete_session_source")
RERUN_TOOLS = ("peek_accounts", "start_aggregation", "test_connection", "aggregate_datasets")
LEGACY_CHECKS = ("peek_accounts", "start_aggregation", "test_connection")


def check_tools(pb: Playbook | None = None) -> list[str]:
    """The checks an owner's confirmation may rerun, in the order SailPoint needs them (playbook `checks.order`)."""
    order = (pb.checks.get("order") if pb else None) or LEGACY_CHECKS
    return [name for name in order if name in RERUN_TOOLS]


def offered_tools(role: str, check_order: dict | None = None, has_source: bool = False, pb: Playbook | None = None,
                  trigger: str | None = None) -> list[str]:
    """The role gate (FR-016, FR-016a, FR-019): SailPoint write tools only for the IAM engineer's turns. An
    application owner's turn gets the check tools only while the IAM engineer's standing check order is set and a
    source exists, so their confirmation of a fix can rerun the checks; never create, configure or delete. Spec 002:
    the ISC tools are the playbook's `checks.yaml` `tools` (001's set without one), and an owner's secret submission
    under a standing order may also apply the new secret (`trigger: secret_submitted`)."""
    isc = (pb.checks.get("tools") if pb else None) or LEGACY_ISC_TOOLS
    base = [name for name in TOOL_SPECS if name in SESSION_TOOLS or name in isc]
    if role == "iam_engineer":
        return base
    reruns = bool(check_order) and has_source
    allowed = set(check_tools(pb)) if reruns else set()
    if reruns and trigger == "secret_submitted":
        allowed.add("apply_application_secret")
    return [name for name in base if name not in WRITE_TOOLS or name in allowed]


def _fill(template: str, values: dict[str, str]) -> str:
    out = template
    for key, value in values.items():
        out = out.replace("{" + key + "}", value)
    return out


def _template(lang: str, name: str) -> str:
    path = PROMPTS / lang / name
    return (path if path.exists() else PROMPTS / "en" / name).read_text()


def static_system(payload: dict, pb: Playbook) -> str:
    """Rules and playbook: identical for every turn of a session (no writer, thread or state in it), so it caches. A
    playbook's own `prompt.md` (spec 002) is appended; without one the text is exactly 001's."""
    session = payload["session"]
    entry = pb.entry
    text = _fill(_template(payload.get("lang", "en"), "system.md"), {
        "tenant_name": str((session.get("tenant") or {}).get("name", "")),
        "owner_label": entry.get("owner_label", "application owner"),
        "application_label": entry.get("application_label", "the application"),
        "connector_name": entry["name"],
        "setup": pb.render(pb.setup),
        "collisions": pb.render(pb.collisions),
        "failures": pb.render(pb.failures),
    })
    return text + ("\n\n" + pb.render(pb.prompt).strip() + "\n" if pb.prompt.strip() else "")


def system_prompt(payload: dict, pb: Playbook) -> str:
    """The whole system prompt as one text (static rules, then this turn)."""
    return static_system(payload, pb) + "\n" + turn_system(payload, pb)


def system_blocks(payload: dict, pb: Playbook) -> list[dict]:
    return [{"type": "text", "text": static_system(payload, pb), "cache_control": CACHE},
            {"type": "text", "text": turn_system(payload, pb)}]


def turn_system(payload: dict, pb: Playbook) -> str:
    """Who wrote, where the reply goes, the role gate, waiting state, suggestion defaults and session values."""
    session = payload["session"]
    ordered = payload["ordered_by"]
    entry = pb.entry
    role = ordered["role"]
    check_order = payload.get("check_order")
    has_source = bool(session.get("source"))
    if role == "iam_engineer":
        gate = ("This message is from the IAM engineer, so the SailPoint write tools are available. If it orders a "
                "SailPoint change, make it now with the tools, whatever earlier replies said about waiting for the "
                "application side.")
    elif check_order and has_source:
        gate = (f"This message is from the application owner. The IAM engineer ({check_order['display_name']}) "
                "ordered the checks earlier and that order stands: if the application owner confirms an "
                "application-side fix is done, rerun the failed check and the checks after it now, in this turn "
                f"({' → '.join(f'`{t}`' for t in check_tools(pb))}), without asking the IAM engineer again "
                "and without asking for a verification command first (the connection check is the verification). "
                "These are SailPoint results, so they belong in full in the IAM engineer's thread: call "
                "`post_to_other_thread` with the full results as `text` and a one-line `relay_note`; in this reply "
                "tell the application owner briefly whether it worked. You have no tools "
                "to create, configure, fix or delete anything in SailPoint; if they ask for that, explain that the "
                "IAM engineer orders it.")
    else:
        gate = ("This message is from the application owner. You have **no SailPoint write tools** for it. If they "
                "ask for a SailPoint change, explain that the IAM engineer orders SailPoint changes and can do so at "
                "any time; do not say SailPoint has to wait for the application side.")
    thread = payload.get("message", {}).get("thread") or role
    other = "application_owner" if thread == "iam_engineer" else "iam_engineer"
    labels = {"iam_engineer": "IAM engineer", "application_owner": entry.get("owner_label", "application owner")}
    waiting = payload.get("waiting_on")
    reason = payload.get("waiting_reason")
    waiting_line = ((f"You said you were waiting for the {labels[waiting]}"
                     + (f" to {reason[0].lower() + reason[1:]}." if reason else "."))
                    if waiting else "You are not waiting for anyone right now.")
    defaults = payload.get("suggestion_defaults") or {}
    suggestion_defaults = "\n".join(
        f"- {labels[r]}: " + "; ".join(f'"{t}"' for t in (defaults.get(r) or [])[:4]) for r in labels if defaults.get(r))
    values = {k: v for k, v in pb.values.items() if not k.startswith("policy_")}
    shown = {"steps": session.get("steps"), "source": session.get("source"), **values}
    secret = session.get("application_secret")
    if secret:  # spec 002: state and expiry only; the vault provider name stays in tool code
        shown["application_secret"] = {"state": secret.get("state"), "expires_on": secret.get("expires_on")}
    if session.get("mode"):
        shown["mode"] = session["mode"]
    session_values = json.dumps(shown, default=str, indent=1)
    return _fill(_template(payload.get("lang", "en"), "turn.md"), {
        "iam_engineer_name": ordered["display_name"] if role == "iam_engineer" else "the IAM engineer",
        "owner_name": ordered["display_name"] if role == "application_owner" else "the application owner",
        "owner_label": entry.get("owner_label", "application owner"),
        "speaker_label": f"{ROLE_LABEL[role]} ({ordered['display_name']})",
        "role_gate": gate,
        "thread_label": labels[thread],
        "other_label": labels[other],
        "other_thread": other,
        "waiting_line": waiting_line,
        "suggestion_defaults": suggestion_defaults or "(none)",
        "plan": _plan_text(session.get("plan") or []),
        "session_values": session_values,
    })


PLAN_MARK = {"done": "x", "in_progress": ">", "failed": "!", "blocked": "#", "skipped": "-", "todo": " "}


def _plan_text(plan: list[dict]) -> str:
    """The shared plan as the model sees it: `[x] id · title (actor) — reason`."""
    if not plan:
        return "(no plan yet)"
    lines = []
    for step in plan:
        reason = f" — {step['reason']}" if step.get("reason") else ""
        lines.append(f"[{PLAN_MARK.get(step.get('state'), ' ')}] {step['id']} · {step['title']} ({step['actor']}, "
                     f"{step['state']}){reason}")
    return "\n".join(lines)


def _messages(payload: dict) -> list[dict]:
    """Shared history as alternating turns: participants' messages are user turns, the agent's are assistant turns."""
    label = {"iam_engineer": "IAM engineer", "application_owner": "Application owner", "system": "System"}
    turns: list[dict] = []

    def add(role: str, text: str) -> None:
        if turns and turns[-1]["role"] == role:
            turns[-1]["content"][0]["text"] += "\n\n" + text
        else:
            turns.append({"role": role, "content": [{"type": "text", "text": text}]})

    thread_label = {"iam_engineer": "IAM engineer's thread", "application_owner": "application owner's thread"}

    def where(h: dict) -> str:
        t = h.get("thread")
        return f" [{thread_label[t]}]" if t in thread_label else ""

    for h in payload.get("history") or []:
        if h["speaker"] == "agent":
            prefix = "(relay note) " if h.get("kind") == "relay_note" else ""
            add("assistant", f"{prefix}{h['text'] or '(no text)'}" + (f"\n_(posted in the {thread_label[h['thread']]})_"
                                                                        if h.get("thread") in thread_label else ""))
        else:
            add("user", f"[{label.get(h['speaker'], h['speaker'])}]{where(h)} {h['text']}")
    msg = payload["message"]
    add("user", f"[{label.get(msg['speaker'], msg['speaker'])}]{where(msg)} {msg['text']}")
    for img in payload.get("images") or []:
        turns[-1]["content"].append({"type": "image", "source": {"type": "base64", "media_type": img["media_type"],
                                                                 "data": img["data_b64"]}})
    if turns[0]["role"] != "user":
        turns.insert(0, {"role": "user", "content": [{"type": "text", "text": "(conversation start)"}]})
    return turns


async def run_turn(payload: dict, pb: Playbook, isc_tools: IscTools, session_tools: SessionTools, emit: Emit,
                   claude: Any | None = None) -> None:
    if claude is None:
        from anthropic import AsyncAnthropicBedrock

        claude = AsyncAnthropicBedrock(aws_region=os.environ.get("AWS_REGION", "ap-southeast-1"))
    model = os.environ.get("BEDROCK_MODEL_ID", "global.anthropic.claude-haiku-4-5-20251001-v1:0")
    role = payload["ordered_by"]["role"]
    names = offered_tools(role, payload.get("check_order"), bool(payload["session"].get("source")), pb=pb,
                          trigger=payload.get("trigger"))
    # Tools asked for in one round run in the order SailPoint needs: the checks (001), or, for a playbook that lists
    # its tools (spec 002), its whole `checks.yaml` order (create → configure → … → dataset schedule).
    sequence = (pb.checks.get("order") if pb.checks.get("tools") else None) or check_tools(pb)
    order = {name: i for i, name in enumerate(sequence)}
    tools = [{"name": n, "description": TOOL_SPECS[n]["description"], "input_schema": TOOL_SPECS[n]["input_schema"]}
             for n in names]
    tools[-1] = {**tools[-1], "cache_control": CACHE}
    system = system_blocks(payload, pb)
    messages = _messages(payload)
    reply = ""
    usage = {"calls": 0, **{name: 0 for _, name in USAGE_FIELDS}}

    for _round in range(MAX_ROUNDS):
        _mark_last_block(messages)
        async with claude.messages.stream(model=model, max_tokens=MAX_TOKENS, system=system, messages=messages,
                                          tools=tools) as stream:
            async for event in stream:
                if event.type == "content_block_delta" and getattr(event.delta, "type", "") == "text_delta":
                    reply += event.delta.text
                    await emit({"type": "delta", "text": event.delta.text})
            final = await stream.get_final_message()
        _add_usage(usage, final)
        uses = [b for b in final.content if b.type == "tool_use"]
        # Several checks asked for in one round always run in the order SailPoint needs (Test Connection reports
        # "req.input is null" before the first aggregation, F5); everything else keeps the model's order.
        uses.sort(key=lambda b: order.get(b.name, -1))
        messages.append({"role": "assistant", "content": [b.model_dump(exclude_none=True) for b in final.content]})
        if not uses:
            break
        results = []
        for use in uses:
            spec = TOOL_SPECS.get(use.name)
            if spec is None or use.name not in names:
                result: Any = {"error": f"{use.name} is not available for this message"}
            else:
                if spec["progress"]:
                    await emit({"type": "progress", "text": spec["progress"]})
                target = session_tools if hasattr(session_tools, use.name) else isc_tools
                try:
                    result = await getattr(target, use.name)(**(use.input or {}))
                except Exception as exc:  # noqa: BLE001 — returned to the model as the tool result
                    result = {"error": f"{type(exc).__name__}: {exc}"}
            results.append({"type": "tool_result", "tool_use_id": use.id,
                            "content": json.dumps(result, default=str)[:6000]})
        await emit({"type": "progress", "text": None})
        messages.append({"role": "user", "content": results})
        if reply and not reply.endswith("\n"):
            reply += "\n\n"
            await emit({"type": "delta", "text": "\n\n"})
    else:
        reply += "\n\n(I stopped after several steps. Ask me to continue for what's still pending.)"

    await emit({"type": "final", "text": reply.strip() or "Done."})
    await emit({"type": "usage", **usage})


def _mark_last_block(messages: list[dict]) -> None:
    """Keep one rolling cache breakpoint on the conversation: on the newest block only (≤ 4 breakpoints in all)."""
    for m in messages:
        for block in m["content"] if isinstance(m["content"], list) else []:
            if isinstance(block, dict):
                block.pop("cache_control", None)
    last = messages[-1]["content"]
    if isinstance(last, list) and last and isinstance(last[-1], dict):
        last[-1]["cache_control"] = CACHE


def _add_usage(usage: dict, final: Any) -> None:
    usage["calls"] += 1
    u = getattr(final, "usage", None)
    for attr, name in USAGE_FIELDS:
        usage[name] += int(getattr(u, attr, 0) or 0)


SECRET_CHECK_PROMPT = (
    "You are a security filter. Look at this screenshot and decide whether it visibly shows a secret: an AWS secret "
    "access key, an access key id (AKIA… / ASIA…), a session token, a password, an API key or client secret, a "
    "private key, or a bearer/JWT token, including a Microsoft Entra \"Certificates & secrets\" page whose Value "
    "column shows the characters of a client secret. Account ids, ARNs, role names, External IDs, Application "
    "(client) IDs and secret IDs (GUIDs), user names and error messages are NOT secrets. Reply with exactly one "
    "line: PASSED, or HELD: <what kind of secret, without repeating it>."
)


async def secret_check(payload: dict, claude: Any | None = None) -> dict:
    if claude is None:
        from anthropic import AsyncAnthropicBedrock

        claude = AsyncAnthropicBedrock(aws_region=os.environ.get("AWS_REGION", "ap-southeast-1"))
    model = os.environ.get("BEDROCK_MODEL_ID", "global.anthropic.claude-haiku-4-5-20251001-v1:0")
    img = (payload.get("images") or [None])[0]
    if not img:
        return {"type": "secret_check", "result": "held", "reason": "No image was received."}
    response = await claude.messages.create(model=model, max_tokens=60, messages=[{"role": "user", "content": [
        {"type": "image", "source": {"type": "base64", "media_type": img["media_type"], "data": img["data_b64"]}},
        {"type": "text", "text": SECRET_CHECK_PROMPT}]}])
    text = "".join(b.text for b in response.content if b.type == "text").strip()
    if text.upper().startswith("PASSED"):
        return {"type": "secret_check", "result": "passed", "reason": ""}
    reason = text.split(":", 1)[1].strip() if ":" in text else "It may show a secret."
    return {"type": "secret_check", "result": "held", "reason": f"It looks like it shows {reason}".rstrip(".") + "."}

"""Claude Haiku tool loop (research R5, contracts/agent-invocation.md).

Streams text deltas as they arrive and a progress line before each tool. At most MAX_ROUNDS tool rounds per turn.
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
    "record_application_step": {"description": "Record an application-side step you are giving the application owner.",
                                "input_schema": {"type": "object", "properties": {
                                    "index": {"type": "integer"}, "title": {"type": "string"},
                                    "read_only": {"type": "boolean"}}, "required": ["index", "title", "read_only"]},
                                "progress": None},
    "mark_application_in_progress": {"description": "Mark that the application owner has started the setup steps.",
                                     "input_schema": {"type": "object", "properties": {}}, "progress": None},
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
}

ROLE_LABEL = {"iam_engineer": "IAM engineer", "application_owner": "application owner"}
CHECK_TOOLS = {"peek_accounts", "start_aggregation", "test_connection"}
CHECK_ORDER = {"peek_accounts": 0, "start_aggregation": 1, "test_connection": 2}


def offered_tools(role: str, check_order: dict | None = None, has_source: bool = False) -> list[str]:
    """The role gate (FR-016, FR-016a, FR-019): SailPoint write tools only for the IAM engineer's turns. An
    application owner's turn gets the three check tools only while the IAM engineer's standing check order is set
    and a source exists, so their confirmation of a fix can rerun the checks; never create, configure or delete."""
    if role == "iam_engineer":
        return list(TOOL_SPECS)
    reruns = bool(check_order) and has_source
    return [name for name in TOOL_SPECS
            if name not in WRITE_TOOLS or (reruns and name in CHECK_TOOLS)]


def _fill(template: str, values: dict[str, str]) -> str:
    out = template
    for key, value in values.items():
        out = out.replace("{" + key + "}", value)
    return out


def system_prompt(payload: dict, pb: Playbook) -> str:
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
                "(`peek_accounts` → `start_aggregation` → `test_connection`), without asking the IAM engineer again "
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
    session_values = json.dumps({"steps": session.get("steps"), "source": session.get("source"), **values},
                                default=str, indent=1)
    lang = payload.get("lang", "en")
    template = (PROMPTS / lang / "system.md").read_text() if (PROMPTS / lang).exists() else \
        (PROMPTS / "en" / "system.md").read_text()
    return _fill(template, {
        "iam_engineer_name": ordered["display_name"] if role == "iam_engineer" else "the IAM engineer",
        "owner_name": ordered["display_name"] if role == "application_owner" else "the application owner",
        "tenant_name": str((session.get("tenant") or {}).get("name", "")),
        "owner_label": entry.get("owner_label", "application owner"),
        "application_label": entry.get("application_label", "the application"),
        "connector_name": entry["name"],
        "speaker_label": f"{ROLE_LABEL[role]} ({ordered['display_name']})",
        "role_gate": gate,
        "thread_label": labels[thread],
        "other_label": labels[other],
        "other_thread": other,
        "waiting_line": waiting_line,
        "suggestion_defaults": suggestion_defaults or "(none)",
        "setup": pb.render(pb.setup),
        "collisions": pb.render(pb.collisions),
        "failures": pb.render(pb.failures),
        "session_values": session_values,
    })


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
    names = offered_tools(role, payload.get("check_order"), bool(payload["session"].get("source")))
    tools = [{"name": n, "description": TOOL_SPECS[n]["description"], "input_schema": TOOL_SPECS[n]["input_schema"]}
             for n in names]
    system = system_prompt(payload, pb)
    messages = _messages(payload)
    reply = ""

    for _round in range(MAX_ROUNDS):
        async with claude.messages.stream(model=model, max_tokens=MAX_TOKENS, system=system, messages=messages,
                                          tools=tools) as stream:
            async for event in stream:
                if event.type == "content_block_delta" and getattr(event.delta, "type", "") == "text_delta":
                    reply += event.delta.text
                    await emit({"type": "delta", "text": event.delta.text})
            final = await stream.get_final_message()
        uses = [b for b in final.content if b.type == "tool_use"]
        # Several checks asked for in one round always run in the order SailPoint needs (Test Connection reports
        # "req.input is null" before the first aggregation, F5); everything else keeps the model's order.
        uses.sort(key=lambda b: CHECK_ORDER.get(b.name, -1))
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


SECRET_CHECK_PROMPT = (
    "You are a security filter. Look at this screenshot and decide whether it visibly shows a secret: an AWS secret "
    "access key, an access key id (AKIA… / ASIA…), a session token, a password, an API key or client secret, a "
    "private key, or a bearer/JWT token. Account ids, ARNs, role names, External IDs, user names and error messages "
    "are NOT secrets. Reply with exactly one line: PASSED, or HELD: <what kind of secret, without repeating it>."
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

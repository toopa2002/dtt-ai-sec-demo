"""Suggested messages per thread (FR-006e, research R17).

Two sources merged by the API: what the agent offered at the end of its turn (`suggest_replies`), topped up from the
connector type's `suggestions.yaml` defaults for the thread's role and the session's current state, so every own
thread shows at least MIN and at most MAX. Picking one is browser-only; nothing here is ever stored as a message.
"""

from typing import Any

from ..masking import mask

MIN, MAX = 3, 5
MAX_TEXT = 200
KINDS = ("answer", "order", "question")
STATES = ("no_source", "waiting_for_owner_output", "check_failed", "all_passed", "any", "waiting_for_secret",
          "tenant_limitation")
CHECKS = ("connection_check", "aggregation", "test_connection")


def _state(session: dict, step: str) -> str:
    value = session.get("steps", {}).get(step, {})
    return value.get("state", "not_started") if isinstance(value, dict) else str(value)


def current_state(session: dict, thread: str) -> str:
    """The point in the session the suggestions are for, seen from one thread. Spec 002: a hint the API set for the
    thread (`waiting_for_secret`, `tenant_limitation`) comes first."""
    hint = (session.get("suggestion_hints") or {}).get(thread)
    if hint in STATES:
        return hint
    if all(_state(session, k) == "passed" for k in CHECKS):
        return "all_passed"
    if any(_state(session, k) == "failed" for k in session.get("steps", {})):
        return "check_failed"
    if thread == "application_owner" and session.get("waiting_on") == "application_owner":
        return "waiting_for_owner_output"
    if not session.get("source"):
        return "no_source"
    return "any"


def clean(item: Any, thread: str, source: str) -> dict | None:
    """One candidate, or None when it breaks a rule: a text that the masker would change (a secret), an empty or
    over-long text, an unknown kind, or an order offered to the application owner (FR-006e, FR-019)."""
    if not isinstance(item, dict):
        return None
    text = str(item.get("text") or "").strip()
    kind = item.get("kind") or "question"
    if not text or len(text) > MAX_TEXT or kind not in KINDS:
        return None
    if "\n" in text or "\r" in text:
        return None  # one line only: a suggestion never carries pre-filled output the participant hasn't produced
    if thread == "application_owner" and kind == "order":
        return None
    masked, changed = mask(text)
    if changed or masked != text:
        return None
    return {"text": text, "kind": kind, "source": source}


def merge(thread: str, agent_items: list[Any], defaults: dict[str, list[dict]], state: str) -> list[dict]:
    """Agent items first, then the defaults for the state, then the role's `any` list; de-duplicated, at least MIN
    (when the defaults allow it) and at most MAX."""
    out: list[dict] = []
    seen: set[str] = set()

    def take(items: list[Any], source: str) -> None:
        for raw in items or []:
            item = clean(raw, thread, source)
            if not item or item["text"].casefold() in seen or len(out) >= MAX:
                continue
            seen.add(item["text"].casefold())
            out.append(item)

    take(agent_items, "agent")
    take(defaults.get(state, []), "default")
    if len(out) < MIN or state != "any":
        take(defaults.get("any", []), "default")
    return out[:MAX]


def for_thread(session: dict, thread: str, agent_items: list[Any], defaults_by_role: dict[str, dict]) -> list[dict]:
    defaults = defaults_by_role.get(thread) or {}
    return merge(thread, agent_items, defaults, current_state(session, thread))

"""Session tools (research R15, R17; contracts/agent-invocation.md "Thread tools").

The agent's streamed reply always lands in the writer's thread: the model never chooses where its text goes. These
tools are the only way to reach the other participant's thread (FR-006c), to say who the agent is waiting for
(information only, FR-006a), to give the application owner their setup steps, and to offer suggested replies
(FR-006e), to keep the shared plan current (FR-008b) and to explain a failed SailPoint action (FR-020). Milestones
for the SailPoint checks are set by the ISC tools themselves; the plan's milestones are derived by the API."""

from collections.abc import Awaitable, Callable
from typing import Any

Emit = Callable[[dict], Awaitable[None]]
THREADS = ("iam_engineer", "application_owner")
SUGGESTION_KINDS = ("answer", "order", "question")
MAX_SUGGESTION_TEXT = 200
MAX_WAITING_REASON = 120
MAX_DIAGNOSIS = 1000
PLAN_OPS = ("set_state", "add", "skip")


def other_thread(thread: str) -> str:
    return "application_owner" if thread == "iam_engineer" else "iam_engineer"


class SessionTools:
    def __init__(self, emit: Emit, thread: str = "iam_engineer"):
        if thread not in THREADS:
            raise ValueError("thread must be iam_engineer or application_owner")
        self.emit = emit
        self.thread = thread          # the writer's thread: where the streamed reply goes
        self.other = other_thread(thread)

    async def post_to_other_thread(self, text: str, relay_note: str, relayed_from: str | None = None) -> dict[str, Any]:
        """A message for the other participant, in their thread, plus a one-line relay note in the writer's thread."""
        text = (text or "").strip()
        relay_note = (relay_note or "").strip()
        if not text:
            return {"ok": False, "error": "text is required; use notify_other_thread for a one-line note only"}
        if not relay_note:
            return {"ok": False, "error": "relay_note is required: one line saying what you asked or passed on"}
        if relayed_from not in (None, "", *THREADS):
            return {"ok": False, "error": "relayed_from must be iam_engineer or application_owner"}
        await self.emit({"type": "other_thread", "text": text, "relay_note": relay_note,
                         "relayed_from": relayed_from or None})
        return {"ok": True, "posted_to": self.other}

    async def notify_other_thread(self, relay_note: str) -> dict[str, Any]:
        """One line in the other thread about news that belongs in this one (for example "All checks passed")."""
        relay_note = (relay_note or "").strip()
        if not relay_note:
            return {"ok": False, "error": "relay_note is required"}
        await self.emit({"type": "other_thread", "relay_note": relay_note})
        return {"ok": True, "noted_in": self.other}

    async def set_waiting(self, on: str | None, reason: str | None = None) -> dict[str, Any]:
        """Who the agent is waiting for and that person's next step, for the waiting banner (FR-006g). Information
        only: it never holds a message back. The reason is one line of at most 120 characters, and none without a
        wait."""
        on = on or None
        if on not in (None, *THREADS):
            return {"ok": False, "error": "on must be iam_engineer, application_owner or null"}
        reason = " ".join((reason or "").split())[:MAX_WAITING_REASON].rstrip() or None if on else None
        await self.emit({"type": "waiting", "on": on, "reason": reason})
        return {"ok": True, "waiting_on": on, "reason": reason}

    async def suggest_replies(self, thread: str, items: list[dict[str, Any]]) -> dict[str, Any]:
        """3 short replies the participant in `thread` could send next; the API adds defaults and drops anything
        unsafe (secrets, or a SailPoint order offered to the application owner)."""
        if thread not in THREADS:
            return {"ok": False, "error": "thread must be iam_engineer or application_owner"}
        cleaned = []
        for item in items or []:
            if not isinstance(item, dict):
                continue
            text = str(item.get("text") or "").strip()
            kind = item.get("kind") or "question"
            if text and "\n" not in text and len(text) <= MAX_SUGGESTION_TEXT and kind in SUGGESTION_KINDS:
                cleaned.append({"text": text, "kind": kind})
        await self.emit({"type": "suggestions", "thread": thread, "items": cleaned})
        return {"ok": True, "accepted": len(cleaned)}

    async def update_plan(self, ops: list[dict[str, Any]]) -> dict[str, Any]:
        """Change the shared plan (FR-008b, research R22): mark steps as you work, add a step (a fix, an extra check)
        or skip one, always with a one-line reason where one is needed. Steps are never removed; the API checks the
        ops again and applies all or none."""
        cleaned = []
        for i, op in enumerate(ops or []):
            if not isinstance(op, dict) or op.get("op") not in PLAN_OPS:
                return {"ok": False, "error": f"op {i}: use one of {', '.join(PLAN_OPS)} (steps are never removed)"}
            reason = " ".join(str(op.get("reason") or "").split())
            needs_reason = op["op"] in ("add", "skip") or op.get("state") in ("failed", "blocked", "skipped")
            if needs_reason and not reason:
                return {"ok": False, "error": f"op {i} ({op['op']} {op.get('step_id') or op.get('title', '')}): "
                                              "give a one-line reason"}
            if op["op"] in ("set_state", "skip") and not op.get("step_id"):
                return {"ok": False, "error": f"op {i}: step_id is required"}
            if op["op"] == "add" and not str(op.get("title") or "").strip():
                return {"ok": False, "error": f"op {i}: an added step needs a title"}
            cleaned.append({k: v for k, v in op.items() if v not in (None, "")} | ({"reason": reason} if reason else {}))
        if not cleaned:
            return {"ok": False, "error": "no ops"}
        await self.emit({"type": "plan", "ops": cleaned})
        return {"ok": True, "ops": len(cleaned)}

    async def note_diagnosis(self, text: str) -> dict[str, Any]:
        """Your diagnosis of the SailPoint action that just failed, for its details in the action record (FR-020)."""
        text = " ".join(str(text or "").split())[:MAX_DIAGNOSIS]
        if not text:
            return {"ok": False, "error": "text is required"}
        await self.emit({"type": "diagnosis", "text": text})
        return {"ok": True}

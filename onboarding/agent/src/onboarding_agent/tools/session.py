"""Session tools (research R15, R17; contracts/agent-invocation.md "Thread tools").

The agent's streamed reply always lands in the writer's thread: the model never chooses where its text goes. These
tools are the only way to reach the other participant's thread (FR-006c), to say who the agent is waiting for
(information only, FR-006a), to give the application owner their setup steps, and to offer suggested replies
(FR-006e). Step states for SailPoint checks are set by the ISC tools themselves; the model may only mark the
application step in progress (it can never mark it passed: that happens when SailPoint's connection check reads
accounts)."""

from collections.abc import Awaitable, Callable
from typing import Any

Emit = Callable[[dict], Awaitable[None]]
THREADS = ("iam_engineer", "application_owner")
SUGGESTION_KINDS = ("answer", "order", "question")
MAX_SUGGESTION_TEXT = 200
MAX_WAITING_REASON = 120


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

    async def record_application_step(self, index: int, title: str, read_only: bool) -> dict[str, Any]:
        await self.emit({"type": "application_step", "index": index, "text": title, "read_only": bool(read_only)})
        return {"ok": True}

    async def mark_application_in_progress(self) -> dict[str, Any]:
        await self.emit({"type": "set_step", "step": "application_ready", "state": "in_progress"})
        return {"ok": True}

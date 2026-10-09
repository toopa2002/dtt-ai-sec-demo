"""The shared plan (FR-008a-c, research R22, data-model PlanStep).

One ordered list per session, seeded from the connector's `plan.yaml`, changed only by the agent's `update_plan` ops
and by milestone `set_step` events. The six milestones of FR-008 are derived from it, so the header and the plan never
disagree. Rules (data-model): steps are never removed; the plan holds at most 40 steps; a reason is required for added,
skipped, blocked and failed steps and for leaving `done`; titles ≤ 120 and reasons ≤ 160 characters, masked.
"""

from datetime import UTC, datetime
from typing import Any

from ..masking import mask_text

STATES = ("todo", "in_progress", "done", "failed", "skipped", "blocked")
ACTORS = ("application_owner", "iam_engineer", "agent")
KINDS = ("read_only", "change")
MILESTONES = ("application_ready", "source_created", "configured", "connection_check", "aggregation",
              "test_connection")
MAX_STEPS = 40
TITLE_MAX = 120
REASON_MAX = 160
NEEDS_REASON = ("skipped", "blocked", "failed")
# set_step state (FR-008) → plan step state
FROM_MILESTONE = {"in_progress": "in_progress", "passed": "done", "failed": "failed", "not_started": "todo"}


class PlanError(ValueError):
    pass


def _now() -> datetime:
    return datetime.now(UTC)


def _clean(text: Any, limit: int) -> str:
    return mask_text(" ".join(str(text or "").split()))[:limit]


CAPABILITY_NOT_CHOSEN = "capability not chosen"
EXISTING_SOURCE = "existing source"


def seed(template: list[dict[str, Any]], details: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """The starting plan: every template step `todo`, or `skipped` when its `skip_unless` details are all empty or
    (spec 002) its `capability` isn't among `details.capabilities`."""
    details = details or {}
    chosen = details.get("capabilities")
    now = _now()
    plan = []
    for step in template[:MAX_STEPS]:
        skip_keys = step.get("skip_unless") or []
        reason = "Not chosen for this session." if skip_keys and not any(details.get(k) for k in skip_keys) else None
        if not reason and step.get("capability") and chosen is not None and step["capability"] not in chosen:
            reason = CAPABILITY_NOT_CHOSEN
        entry = {
            "id": step["id"], "title": _clean(step["title"], TITLE_MAX), "actor": step["actor"], "kind": step["kind"],
            "state": "skipped" if reason else "todo",
            "reason": reason,
            "milestone": step.get("milestone"), "added_by": "playbook",
            "instruction": f"setup.md step {step['setup_step']}" if step.get("setup_step") else None,
            "changed_at": now,
        }
        for key in ("capability", "on_extend"):
            if step.get(key):
                entry[key] = step[key]
        plan.append(entry)
    return plan


def skip_on_extend(plan: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Extend-source (spec 002 FR-105): the steps that only a new source needs are skipped, never removed."""
    out = [dict(s) for s in plan]
    for step in out:
        if step.get("on_extend") == "skip" and step["state"] not in ("done", "skipped"):
            step["state"], step["reason"], step["changed_at"] = "skipped", EXISTING_SOURCE, _now()
    return out


def _find(plan: list[dict], step_id: str) -> dict:
    step = next((s for s in plan if s["id"] == step_id), None)
    if step is None:
        raise PlanError(f"no plan step {step_id!r}")
    return step


def _set_state(step: dict, state: str, reason: Any) -> None:
    if state not in STATES:
        raise PlanError(f"state must be one of {', '.join(STATES)}")
    reason_text = _clean(reason, REASON_MAX) if reason else ""
    if (state in NEEDS_REASON or (step["state"] == "done" and state != "done")) and not reason_text:
        raise PlanError(f"step {step['id']!r}: a reason is required to set it {state}"
                        + (" after it was done" if step["state"] == "done" else ""))
    if step["state"] != state or reason_text:
        step["state"] = state
        step["reason"] = reason_text or (step.get("reason") if state in NEEDS_REASON else None)
        step["changed_at"] = _now()


def apply_ops(plan: list[dict[str, Any]], ops: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Apply the agent's ops to a copy of the plan; any bad op raises PlanError and nothing changes."""
    out = [dict(s) for s in plan]
    for op in ops or []:
        kind = op.get("op")
        if kind == "set_state":
            _set_state(_find(out, str(op.get("step_id"))), str(op.get("state")), op.get("reason"))
        elif kind == "skip":
            _set_state(_find(out, str(op.get("step_id"))), "skipped", op.get("reason"))
        elif kind == "add":
            if len(out) >= MAX_STEPS:
                raise PlanError(f"the plan holds at most {MAX_STEPS} steps")
            after = op.get("after")
            index = len(out) if after in (None, "") else out.index(_find(out, str(after))) + 1
            title, reason = _clean(op.get("title"), TITLE_MAX), _clean(op.get("reason"), REASON_MAX)
            actor, step_kind, milestone = op.get("actor"), op.get("kind", "change"), op.get("milestone")
            if not title or not reason:
                raise PlanError("an added step needs a title and a reason")
            if actor not in ACTORS or step_kind not in KINDS or milestone not in (None, *MILESTONES):
                raise PlanError("an added step needs actor application_owner|iam_engineer|agent, kind "
                                "read_only|change and, if given, a known milestone")
            n = 1 + sum(1 for s in out if s["added_by"] == "agent")
            state = op.get("state") or "todo"
            if state not in STATES:
                raise PlanError(f"state must be one of {', '.join(STATES)}")
            out.insert(index, {"id": f"x{n}", "title": title, "actor": actor, "kind": step_kind, "state": state,
                               "reason": reason, "milestone": milestone, "added_by": "agent", "instruction": None,
                               "changed_at": _now()})
        elif kind == "remove":
            raise PlanError("steps are never removed: skip it with a reason instead")
        else:
            raise PlanError(f"unknown op {kind!r}: use set_state, add or skip")
    return out


def mark_milestone(plan: list[dict[str, Any]], milestone: str, state: str, plan_step: str | None,
                   reason: str | None = None) -> list[dict[str, Any]]:
    """A milestone `set_step` from the SailPoint tools: set its plan step. A passed application_ready means SailPoint
    reached the application through the role, so the owner's remaining setup steps for it are done as well."""
    out = [dict(s) for s in plan]
    target = FROM_MILESTONE.get(state)
    if target is None:
        return out
    step = next((s for s in out if s["id"] == plan_step), None) or next(
        (s for s in reversed(out) if s.get("milestone") == milestone), None)
    if step is None:
        return out
    why = reason or ("The check failed." if target == "failed" else None)
    if step["state"] == "done" and target != "done":
        why = why or "Rerun."
    _set_state(step, target, why)
    if milestone == "application_ready" and target == "done":
        for s in out:
            if s.get("milestone") == milestone and s["state"] not in ("done", "skipped"):
                _set_state(s, "done", None if s["state"] != "done" else "Confirmed by the connection check.")
    return out


def derive_milestones(plan: list[dict[str, Any]]) -> dict[str, str]:
    """Per milestone: any failed → failed; all done or skipped → passed; any done or in progress → in_progress;
    else not_started (FR-008c)."""
    out = {}
    for milestone in MILESTONES:
        steps = [s for s in plan if s.get("milestone") == milestone]
        states = [s["state"] for s in steps]
        if not steps:
            continue
        if "failed" in states:
            out[milestone] = "failed"
        elif all(st in ("done", "skipped") for st in states):
            out[milestone] = "passed"
        elif any(st in ("done", "in_progress") for st in states):
            out[milestone] = "in_progress"
        else:
            out[milestone] = "not_started"
    return out


def progress(plan: list[dict[str, Any]]) -> tuple[int, int, str | None]:
    """(done, total, next_step_id): steps done of steps not skipped; next = first step not done or skipped."""
    counted = [s for s in plan if s["state"] != "skipped"]
    done = sum(1 for s in counted if s["state"] == "done")
    nxt = next((s["id"] for s in plan if s["state"] not in ("done", "skipped")), None)
    return done, len(counted), nxt


def public(plan: list[dict[str, Any]]) -> list[dict[str, Any]]:
    keys = ("id", "title", "actor", "kind", "state", "reason", "milestone", "added_by", "changed_at")
    extra = ("capability", "pending_since")  # spec 002, only when set: AWS plans look exactly as before
    return [{k: s.get(k) for k in keys} | {k: s[k] for k in extra if s.get(k)} for s in plan]

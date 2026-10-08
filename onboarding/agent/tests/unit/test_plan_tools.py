"""T149: update_plan and note_diagnosis (FR-008b, FR-020; research R22, R24): one event per call, and ops missing a
required reason are refused by the tool with a message the model can act on."""

from onboarding_agent import loop
from onboarding_agent.tools.session import SessionTools

from .test_role_gate import payload


async def test_update_plan_emits_one_event(emit) -> None:  # type: ignore[no-untyped-def]
    tools = SessionTools(emit, "iam_engineer")
    ops = [{"op": "set_state", "step_id": "check_org", "state": "done"},
           {"op": "add", "after": "connection_check", "title": "Fix the role's trust", "actor": "application_owner",
            "kind": "change", "reason": "The trust names the production principal."}]
    r = await tools.update_plan(ops)
    assert r == {"ok": True, "ops": 2}
    [event] = emit.of("plan")
    assert event["ops"][1]["title"] == "Fix the role's trust" and event["ops"][1]["reason"]


async def test_ops_without_a_needed_reason_are_refused(emit) -> None:  # type: ignore[no-untyped-def]
    tools = SessionTools(emit)
    for op in ({"op": "skip", "step_id": "member_accounts"},
               {"op": "set_state", "step_id": "aggregation", "state": "blocked"},
               {"op": "set_state", "step_id": "connection_check", "state": "failed"},
               {"op": "add", "title": "Extra check", "actor": "agent", "kind": "read_only"},
               {"op": "remove", "step_id": "check_org"},
               {"op": "set_state", "state": "done"}):
        r = await tools.update_plan([op])
        assert r["ok"] is False and r["error"]
    assert emit.of("plan") == []


async def test_note_diagnosis(emit) -> None:  # type: ignore[no-untyped-def]
    tools = SessionTools(emit)
    assert (await tools.note_diagnosis("Cause on the AWS side:\n the trust " + "x" * 2000))["ok"]
    [event] = emit.of("diagnosis")
    assert "\n" not in event["text"] and len(event["text"]) <= 1000
    assert (await tools.note_diagnosis("  "))["ok"] is False


def test_the_turn_shows_the_plan(pb) -> None:  # type: ignore[no-untyped-def]
    p = payload("iam_engineer", "What is left?")
    p["session"]["plan"] = [
        {"id": "check_org", "title": "Check the organization", "actor": "application_owner", "state": "done"},
        {"id": "x1", "title": "Fix the role's trust", "actor": "application_owner", "state": "in_progress",
         "reason": "The trust names the production principal."}]
    turn = loop.turn_system(p, pb)
    assert "[x] check_org · Check the organization" in turn
    assert "[>] x1 · Fix the role's trust (application_owner, in_progress) — The trust names" in turn
    assert "update_plan" in loop.static_system(p, pb) and "update_plan" in loop.offered_tools("application_owner")

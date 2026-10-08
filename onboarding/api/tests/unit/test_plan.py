"""T141: the shared plan (FR-008a-c, research R22): seeding, ops and their rejection rules, milestone derivation and
the done count."""

import pytest

from onboarding_api.catalog import catalog
from onboarding_api.sessions import plan as plans

DETAILS = {"accounts": ["111122223333"], "bedrock_regions": [], "agentcore_regions": []}


def seeded():  # type: ignore[no-untyped-def]
    return plans.seed(catalog.plan_template("aws-saas"), DETAILS)


def states(p, *ids):  # type: ignore[no-untyped-def]
    return [next(s["state"] for s in p if s["id"] == i) for i in ids]


def test_seed_from_the_aws_playbook() -> None:
    p = seeded()
    ids = [s["id"] for s in p]
    assert ids[0] == "check_org" and "order" in ids and ids.index("create_source") < ids.index("test_connection")
    assert {s["actor"] for s in p} == {"application_owner", "iam_engineer", "agent"}
    assert all(s["added_by"] == "playbook" for s in p)
    # Discovery was not chosen: skipped with a reason; everything else to do.
    assert states(p, "discovery_policies") == ["skipped"] and p[ids.index("discovery_policies")]["reason"]
    assert all(s["state"] == "todo" for s in p if s["id"] != "discovery_policies")
    with_discovery = plans.seed(catalog.plan_template("aws-saas"), DETAILS | {"agentcore_regions": ["ap-southeast-1"]})
    assert states(with_discovery, "discovery_policies") == ["todo"]
    assert plans.progress(p) == (0, len(p) - 1, "check_org")


def test_ops() -> None:
    p = plans.apply_ops(seeded(), [{"op": "set_state", "step_id": "check_org", "state": "done"},
                                   {"op": "set_state", "step_id": "check_role_name", "state": "in_progress"}])
    assert states(p, "check_org", "check_role_name") == ["done", "in_progress"]
    p = plans.apply_ops(p, [{"op": "add", "after": "connection_check", "title": "Fix the role's trust",
                             "actor": "application_owner", "kind": "change", "milestone": "application_ready",
                             "reason": "The trust names the production principal.", "state": "in_progress"}])
    added = p[[s["id"] for s in p].index("connection_check") + 1]
    assert added["id"] == "x1" and added["added_by"] == "agent" and added["reason"]
    p = plans.apply_ops(p, [{"op": "set_state", "step_id": "aggregation", "state": "blocked",
                             "reason": "Waits on the trust fix."},
                            {"op": "skip", "step_id": "member_accounts", "reason": "No member accounts."}])
    assert states(p, "aggregation", "member_accounts") == ["blocked", "skipped"]
    assert plans.progress(p)[2] == "check_role_name"


@pytest.mark.parametrize("ops", [
    [{"op": "remove", "step_id": "check_org"}],
    [{"op": "set_state", "step_id": "check_org", "state": "failed"}],              # failed without a reason
    [{"op": "set_state", "step_id": "check_org", "state": "blocked"}],             # blocked without a reason
    [{"op": "skip", "step_id": "check_org"}],                                      # skipped without a reason
    [{"op": "add", "title": "x", "actor": "application_owner", "kind": "change"}],  # added without a reason
    [{"op": "add", "title": "x", "actor": "someone", "kind": "change", "reason": "r"}],
    [{"op": "set_state", "step_id": "nope", "state": "done"}],
    [{"op": "set_state", "step_id": "check_org", "state": "finished"}],
    [{"op": "rename", "step_id": "check_org"}],
])
def test_rejected_ops_change_nothing(ops) -> None:  # type: ignore[no-untyped-def]
    p = seeded()
    with pytest.raises(plans.PlanError):
        plans.apply_ops(p, ops)
    assert p == seeded() or [s["state"] for s in p] == [s["state"] for s in seeded()]


def test_leaving_done_needs_a_reason_and_the_plan_is_capped() -> None:
    p = plans.apply_ops(seeded(), [{"op": "set_state", "step_id": "check_org", "state": "done"}])
    with pytest.raises(plans.PlanError):
        plans.apply_ops(p, [{"op": "set_state", "step_id": "check_org", "state": "todo"}])
    assert states(plans.apply_ops(p, [{"op": "set_state", "step_id": "check_org", "state": "todo",
                                       "reason": "Rerun after the reset."}]), "check_org") == ["todo"]
    full = seeded()
    adds = [{"op": "add", "title": f"extra {i}", "actor": "agent", "kind": "read_only", "reason": "r"}
            for i in range(plans.MAX_STEPS - len(full) + 1)]
    with pytest.raises(plans.PlanError):
        plans.apply_ops(full, adds)


def test_titles_and_reasons_are_masked_and_capped() -> None:
    p = plans.apply_ops(seeded(), [{"op": "add", "title": "Use key AKIAABCDEFGHIJKLMNOP " + "x" * 200,
                                    "actor": "agent", "kind": "read_only", "reason": "y" * 400}])
    added = next(s for s in p if s["id"] == "x1")
    assert "AKIAABCDEFGHIJKLMNOP" not in added["title"]
    assert len(added["title"]) <= plans.TITLE_MAX and len(added["reason"]) <= plans.REASON_MAX


def test_milestones_are_derived() -> None:
    p = seeded()
    assert plans.derive_milestones(p)["application_ready"] == "not_started"
    p = plans.apply_ops(p, [{"op": "set_state", "step_id": "check_org", "state": "done"}])
    assert plans.derive_milestones(p)["application_ready"] == "in_progress"
    p = plans.mark_milestone(p, "connection_check", "failed", "connection_check", "AssumeRole denied")
    m = plans.derive_milestones(p)
    assert m["connection_check"] == "failed" and m["source_created"] == "not_started"
    # The connection check reached AWS: the owner's remaining setup steps are done, the milestone passes.
    p = plans.mark_milestone(p, "application_ready", "passed", "confirm_role")
    p = plans.mark_milestone(p, "connection_check", "passed", "connection_check")
    m = plans.derive_milestones(p)
    assert m["application_ready"] == "passed" and m["connection_check"] == "passed"
    p = plans.apply_ops(p, [{"op": "add", "title": "Recheck", "actor": "agent", "kind": "read_only",
                             "milestone": "connection_check", "reason": "after the fix", "state": "failed"}])
    assert plans.derive_milestones(p)["connection_check"] == "failed"  # any failed step fails the milestone

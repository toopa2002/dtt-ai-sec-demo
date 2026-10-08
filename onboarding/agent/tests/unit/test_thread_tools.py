"""T091: the thread tools (FR-006c, research R15) and what the loop streams.

The streamed reply never carries a thread or addressee: it always lands in the writer's thread. The only way to the
other thread is `post_to_other_thread` (message + relay note) or `notify_other_thread` (note only)."""

from onboarding_agent import loop
from onboarding_agent.isc.tools import IscTools
from onboarding_agent.tools.session import SessionTools

from .conftest import sample_session
from .test_role_gate import FakeClaude, payload, text, tool


async def test_post_to_other_thread_emits_message_and_relay_note(emit) -> None:  # type: ignore[no-untyped-def]
    tools = SessionTools(emit, "iam_engineer")
    result = await tools.post_to_other_thread("Run `aws iam get-role …` and paste the output.",
                                              "Asked the AWS owner to run the read-only get-role check.")
    assert result == {"ok": True, "posted_to": "application_owner"}
    events = emit.of("other_thread")
    assert len(events) == 1
    assert events[0]["text"].startswith("Run `aws iam get-role")
    assert events[0]["relay_note"] == "Asked the AWS owner to run the read-only get-role check."
    assert events[0]["relayed_from"] is None


async def test_post_to_other_thread_requires_both_fields(emit) -> None:  # type: ignore[no-untyped-def]
    tools = SessionTools(emit, "application_owner")
    assert (await tools.post_to_other_thread("", "note"))["ok"] is False
    assert (await tools.post_to_other_thread("text", "  "))["ok"] is False
    assert (await tools.post_to_other_thread("text", "note", relayed_from="both"))["ok"] is False
    assert emit.of("other_thread") == []
    ok = await tools.post_to_other_thread("Ploy says she is on a call for 10 minutes.", "Told the IAM engineer.",
                                          relayed_from="application_owner")
    assert ok["posted_to"] == "iam_engineer"
    assert emit.of("other_thread")[0]["relayed_from"] == "application_owner"


async def test_notify_other_thread_emits_note_only(emit) -> None:  # type: ignore[no-untyped-def]
    tools = SessionTools(emit, "iam_engineer")
    assert (await tools.notify_other_thread("All checks passed; the source is ready."))["noted_in"] == "application_owner"
    events = emit.of("other_thread")
    assert events == [{"type": "other_thread", "relay_note": "All checks passed; the source is ready."}]
    assert (await tools.notify_other_thread(""))["ok"] is False


async def test_set_waiting_accepts_only_threads_or_null(emit) -> None:  # type: ignore[no-untyped-def]
    tools = SessionTools(emit, "iam_engineer")
    assert (await tools.set_waiting("application_owner"))["waiting_on"] == "application_owner"
    assert (await tools.set_waiting(None))["waiting_on"] is None
    assert (await tools.set_waiting(""))["waiting_on"] is None
    assert (await tools.set_waiting("both"))["ok"] is False
    assert [e["on"] for e in emit.of("waiting")] == ["application_owner", None, None]


async def test_set_waiting_reason_is_one_short_line(emit) -> None:  # type: ignore[no-untyped-def]
    """T124: the waiting banner's reason (FR-006g, research R20)."""
    tools = SessionTools(emit, "iam_engineer")
    result = await tools.set_waiting("application_owner", "run step 4\nand paste   the output")
    assert result["reason"] == "run step 4 and paste the output"
    await tools.set_waiting("application_owner", "y" * 300)
    await tools.set_waiting(None, "x")
    reasons = [e["reason"] for e in emit.of("waiting")]
    assert reasons[0] == "run step 4 and paste the output"
    assert len(reasons[1]) == 120
    assert reasons[2] is None


async def test_suggest_replies_cleans_items(emit) -> None:  # type: ignore[no-untyped-def]
    tools = SessionTools(emit, "application_owner")
    result = await tools.suggest_replies("application_owner", [
        {"text": "Here is the output:", "kind": "answer"},
        {"text": "x" * 201, "kind": "answer"},            # too long
        {"text": "", "kind": "question"},                 # empty
        {"text": "Explain this step", "kind": "shout"},   # unknown kind
        "not a dict",
    ])
    assert result == {"ok": True, "accepted": 1}
    assert emit.of("suggestions") == [{"type": "suggestions", "thread": "application_owner",
                                       "items": [{"text": "Here is the output:", "kind": "answer"}]}]
    assert (await tools.suggest_replies("both", []))["ok"] is False


async def test_streamed_reply_carries_no_thread_or_addressee(isc, isc_mock, pb, emit) -> None:  # type: ignore[no-untyped-def]
    claude = FakeClaude([
        [tool("post_to_other_thread", "p1", {"text": "Please run step 1.", "relay_note": "Gave the AWS owner step 1."}),
         text("I asked the owner to start.")],
        [text(" Done.")],
    ])
    p = payload("iam_engineer", "Get the owner started")
    tools = SessionTools(emit, "iam_engineer")
    await loop.run_turn(p, pb, IscTools(isc, pb, p["session"], emit), tools, emit, claude=claude)
    for ev in emit.of("delta") + emit.of("final"):
        assert "addressed_to" not in ev and "thread" not in ev
    final_text = emit.of("final")[0]["text"]
    assert final_text.startswith("I asked the owner to start.") and final_text.endswith("Done.")
    assert emit.of("other_thread")[0]["relay_note"] == "Gave the AWS owner step 1."


def test_other_thread_is_derived_from_the_writer() -> None:
    assert SessionTools(lambda e: None, "iam_engineer").other == "application_owner"  # type: ignore[arg-type]
    assert SessionTools(lambda e: None, "application_owner").other == "iam_engineer"  # type: ignore[arg-type]
    _ = sample_session  # the fixtures module is shared with test_role_gate

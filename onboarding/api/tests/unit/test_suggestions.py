"""T105: suggestion merge rules (FR-006e, research R17)."""

from onboarding_api.chat import suggestions

DEFAULTS = {
    "no_source": [{"text": "Create the connector and run the checks", "kind": "order"},
                  {"text": "What does the AWS owner have to prepare first?", "kind": "question"}],
    "check_failed": [{"text": "Why did the connection check fail?", "kind": "question"},
                     {"text": "Rerun the connection check", "kind": "order"}],
    "any": [{"text": "What is left to do?", "kind": "question"},
            {"text": "Explain the last message", "kind": "question"},
            {"text": "Show me the current status", "kind": "question"},
            {"text": "Run the checks", "kind": "order"}],
}
OWNER_DEFAULTS = {
    "waiting_for_owner_output": [{"text": "Here is the output:", "kind": "answer"},
                                 {"text": "I get an error at this step", "kind": "answer"}],
    "any": [{"text": "What is left to do?", "kind": "question"},
            {"text": "Explain the last message", "kind": "question"},
            {"text": "Show me the current status", "kind": "question"}],
}


def session(steps: dict | None = None, source: dict | None = None, waiting_on: str | None = None) -> dict:
    base = {k: {"state": "not_started"} for k in
            ("application_ready", "source_created", "configured", "connection_check", "aggregation", "test_connection")}
    for k, v in (steps or {}).items():
        base[k] = {"state": v}
    return {"steps": base, "source": source, "waiting_on": waiting_on}


def test_agent_items_come_first_then_defaults_top_up_to_three() -> None:
    out = suggestions.merge("iam_engineer", [{"text": "Rerun it now", "kind": "order"}], DEFAULTS, "no_source")
    assert [i["text"] for i in out][:2] == ["Rerun it now", "Create the connector and run the checks"]
    assert out[0]["source"] == "agent" and out[1]["source"] == "default"
    assert 3 <= len(out) <= 5


def test_orders_are_dropped_for_the_application_owner() -> None:
    out = suggestions.merge("application_owner", [{"text": "Create the connector", "kind": "order"},
                                                  {"text": "Here is the output:", "kind": "answer"}],
                            OWNER_DEFAULTS, "any")
    assert all(i["kind"] != "order" for i in out)
    assert out[0]["text"] == "Here is the output:"


def test_items_the_masker_changes_are_dropped() -> None:
    out = suggestions.merge("iam_engineer", [{"text": "Use key AKIAABCDEFGHIJKLMNOP", "kind": "answer"}],
                            DEFAULTS, "any")
    assert all("AKIA" not in i["text"] for i in out)
    assert all(i["source"] == "default" for i in out)


def test_too_long_empty_and_unknown_kind_are_dropped() -> None:
    bad = [{"text": "x" * 201, "kind": "answer"}, {"text": "", "kind": "answer"}, {"text": "hi", "kind": "shout"}, "nope",
           {"text": "Here is the output:\n111122223333  ALL", "kind": "answer"}]  # multi-line: pre-filled output
    out = suggestions.merge("iam_engineer", bad, DEFAULTS, "any")
    assert all(i["source"] == "default" for i in out)


def test_duplicates_removed_and_capped_at_five() -> None:
    agent = [{"text": "What is left to do?", "kind": "question"},   # duplicates a default (case-insensitive)
             {"text": "what is LEFT to do?", "kind": "question"},
             {"text": "A", "kind": "question"}, {"text": "B", "kind": "question"}, {"text": "C", "kind": "question"},
             {"text": "D", "kind": "question"}]
    out = suggestions.merge("iam_engineer", agent, DEFAULTS, "any")
    assert len(out) == 5
    assert [i["text"] for i in out].count("What is left to do?") == 1


def test_state_detection() -> None:
    assert suggestions.current_state(session(), "iam_engineer") == "no_source"
    assert suggestions.current_state(session(source={"id": "1"}), "iam_engineer") == "any"
    assert suggestions.current_state(session({"connection_check": "failed"}, {"id": "1"}), "iam_engineer") == "check_failed"
    passed = {k: "passed" for k in ("connection_check", "aggregation", "test_connection")}
    assert suggestions.current_state(session(passed, {"id": "1"}), "application_owner") == "all_passed"
    waiting = session(waiting_on="application_owner")
    assert suggestions.current_state(waiting, "application_owner") == "waiting_for_owner_output"
    assert suggestions.current_state(waiting, "iam_engineer") == "no_source"


def test_waiting_owner_gets_answer_starters_first() -> None:
    out = suggestions.for_thread(session(waiting_on="application_owner"), "application_owner", [],
                                 {"application_owner": OWNER_DEFAULTS})
    assert out[0]["text"] == "Here is the output:"
    assert len(out) >= 3

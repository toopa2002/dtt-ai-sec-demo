"""T175: the scripted model (Constitution IV, research R28) is refused off the local stub, scripts the IAM engineer's
order and the trust diagnosis, and answers anything else with a short fixed reply."""

import json

import pytest

from onboarding_agent import fake_model, loop

from .test_role_gate import payload


def test_refused_unless_isc_is_the_local_stub() -> None:
    for url in (None, "", "https://acme.api.identitynow.com", "https://localhost.evil.example"):
        with pytest.raises(RuntimeError):
            fake_model.check_allowed(url)
    fake_model.check_allowed("http://127.0.0.1:8099")
    fake_model.check_allowed("http://localhost:8099")


async def call(p: dict, pb, messages: list[dict] | None = None):  # type: ignore[no-untyped-def]
    model = fake_model.FakeModel()
    names = loop.offered_tools(p["ordered_by"]["role"], p.get("check_order"), bool(p["session"].get("source")))
    tools = [{"name": n} for n in names]
    async with model.messages.stream(system=loop.system_blocks(p, pb), messages=messages or loop._messages(p),
                                     tools=tools, model="fake") as stream:
        streamed = "".join([e.delta.text async for e in stream])
        final = await stream.get_final_message()
    assert streamed == "".join(b.text for b in final.content if b.type == "text")
    return final


def uses(final) -> list[str]:  # type: ignore[no-untyped-def]
    return [b.name for b in final.content if b.type == "tool_use"]


async def test_iam_order_creates_and_checks(pb) -> None:  # type: ignore[no-untyped-def]
    final = await call(payload("iam_engineer", "Create the connector with the session details, then run the checks."), pb)
    assert uses(final) == ["create_source", "configure_source", "peek_accounts"]


async def test_trust_failure_is_diagnosed_in_both_threads(pb) -> None:  # type: ignore[no-untyped-def]
    p = payload("iam_engineer", "Create the connector and run the checks")
    messages = loop._messages(p)
    first = await call(p, pb, messages)
    messages.append({"role": "assistant", "content": [b.model_dump(exclude_none=True) for b in first.content]})
    failed = {"ok": False, "error": "not authorized to perform: sts:AssumeRole on resource: arn:aws:iam::1:role/x"}
    messages.append({"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": b.id, "content": json.dumps(failed if b.name == "peek_accounts" else
                                                                           {"created": True})}
        for b in first.content if b.type == "tool_use"]})
    second = await call(p, pb, messages)
    assert {"post_to_other_thread", "set_waiting", "suggest_replies"} <= set(uses(second))
    text = "".join(b.text for b in second.content if b.type == "text")
    assert "AWS" in text and "AssumeRole" in text
    post = next(b for b in second.content if b.type == "tool_use" and b.name == "post_to_other_thread")
    assert "SailPointISCRole-acme-demo" in post.input["text"] and post.input["relay_note"]


async def test_owner_cannot_get_write_tools_and_unknown_text_gets_a_reply(pb) -> None:  # type: ignore[no-untyped-def]
    final = await call(payload("application_owner", "Create the SailPoint connector now."), pb)
    assert uses(final) == [] and "IAM engineer" in final.content[0].text
    final = await call(payload("iam_engineer", "Lovely weather today"), pb)
    assert uses(final) == [] and final.content[0].text == "Thanks, noted. Ask me what is left to do at any time."


async def test_owner_steps_carry_session_values(pb) -> None:  # type: ignore[no-untyped-def]
    final = await call(payload("application_owner", "What do I need to set up in AWS first?"), pb)
    text = final.content[0].text
    assert "```" in text and "111122223333" in text and "SailPointISCRole-acme-demo" in text
    assert "suggest_replies" in uses(final)


async def test_secret_check() -> None:
    import base64

    model = fake_model.FakeModel()
    for data, expected in ((b"\x89PNG plain", "PASSED"), (b"\x89PNG AKIAABCDEFGHIJKLMNOP", "HELD")):
        img = {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                           "data": base64.b64encode(data).decode()}}
        r = await loop.secret_check({"images": [{"media_type": "image/png", "data_b64": img["source"]["data"]}]},
                                    claude=model)
        assert r["result"] == ("passed" if expected == "PASSED" else "held")

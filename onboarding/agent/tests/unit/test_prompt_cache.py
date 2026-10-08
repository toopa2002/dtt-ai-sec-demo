"""T174: prompt caching (Constitution IV, research R27): cache breakpoints on the last tool, the static system block and
the conversation's newest block each round; the static block does not change between turns; one usage event per turn
sums the rounds."""

import copy
from types import SimpleNamespace

from onboarding_agent import loop
from onboarding_agent.isc.tools import IscTools
from onboarding_agent.tools.session import SessionTools

from .conftest import sample_session
from .test_role_gate import FakeStream, payload, text, tool


class RecordingClaude:
    def __init__(self, rounds, usage):  # type: ignore[no-untyped-def]
        self.rounds = list(rounds)
        self.usage = usage
        self.calls: list[dict] = []
        self.messages = SimpleNamespace(stream=self._stream)

    def _stream(self, **kwargs):  # type: ignore[no-untyped-def]
        self.calls.append(copy.deepcopy(kwargs))
        stream = FakeStream(self.rounds.pop(0))
        real = stream.get_final_message

        async def final():  # type: ignore[no-untyped-def]
            msg = await real()
            msg.usage = SimpleNamespace(**self.usage)
            return msg

        stream.get_final_message = final
        return stream


def breakpoints(call: dict) -> list[str]:
    where = [f"tool:{t['name']}" for t in call["tools"] if "cache_control" in t]
    where += [f"system:{i}" for i, b in enumerate(call["system"]) if "cache_control" in b]
    for i, m in enumerate(call["messages"]):
        for j, b in enumerate(m["content"] if isinstance(m["content"], list) else []):
            if isinstance(b, dict) and "cache_control" in b:
                where.append(f"message:{i}:{j}")
    return where


async def test_breakpoints_each_round_and_usage(isc, isc_mock, pb, emit) -> None:  # type: ignore[no-untyped-def]
    usage = {"input_tokens": 100, "cache_creation_input_tokens": 7000, "cache_read_input_tokens": 0,
             "output_tokens": 50}
    claude = RecordingClaude([[tool("get_tenant_external_id", "u1")], [text("The External ID is set.")]], usage)
    p = payload("iam_engineer", "What is the External ID?")
    await loop.run_turn(p, pb, IscTools(isc, pb, p["session"], emit), SessionTools(emit), emit, claude=claude)

    assert len(claude.calls) == 2
    for call in claude.calls:
        marks = breakpoints(call)
        assert len(marks) <= 4
        assert marks[0] == f"tool:{call['tools'][-1]['name']}"
        assert "system:0" in marks and "system:1" not in marks
        last = len(call["messages"]) - 1
        assert marks[-1] == f"message:{last}:{len(call['messages'][last]['content']) - 1}"
        assert sum(m.startswith("message:") for m in marks) == 1  # one rolling breakpoint
    # The tools come first, then the static block, then the turn block: the cache prefix order.
    assert claude.calls[0]["system"][0]["text"] == claude.calls[1]["system"][0]["text"]

    [u] = emit.of("usage")
    assert u == {"type": "usage", "calls": 2, "input_tokens": 200, "cache_write_tokens": 14000,
                 "cache_read_tokens": 0, "output_tokens": 100}
    assert emit.events[-1]["type"] == "usage"


def test_static_block_is_the_same_for_every_turn(pb) -> None:  # type: ignore[no-untyped-def]
    a = payload("iam_engineer", "Create the connector")
    b = payload("application_owner", "Here is the output: ...")
    b["waiting_on"], b["waiting_reason"] = "application_owner", "run step 4 and paste the output"
    c = payload("iam_engineer", "Rerun the check")
    c["session"] = sample_session(source={"id": "s", "name": "AWS - Acme Org"})
    static = {loop.static_system(p, pb) for p in (a, b, c)}
    assert len(static) == 1
    s = static.pop()
    # Nothing that changes per turn is in the cached part.
    for turn_only in ("Wichai", "Somchai", "This message was sent by", "waiting for", "Session values"):
        assert turn_only not in s
    assert "SailPointISCRole-acme-demo" in s  # playbook values are per session, so they may be cached
    blocks = loop.system_blocks(b, pb)
    assert blocks[0]["cache_control"] == {"type": "ephemeral"} and "cache_control" not in blocks[1]
    assert "This message was sent by" in blocks[1]["text"]

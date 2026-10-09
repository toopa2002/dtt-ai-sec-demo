"""T045: the role gate (FR-016, FR-019) and the turn loop, with a scripted fake Claude.

An application owner's message never gets SailPoint write tools, so no action event can result; an IAM engineer's
order runs the tools without asking for confirmation. There are no AWS tools at all (FR-010)."""

from types import SimpleNamespace

from onboarding_agent import loop
from onboarding_agent.isc.tools import WRITE_TOOLS, IscTools
from onboarding_agent.tools.session import SessionTools

from .conftest import sample_session


class FakeStream:
    def __init__(self, blocks):  # type: ignore[no-untyped-def]
        self.blocks = blocks

    async def __aenter__(self):  # type: ignore[no-untyped-def]
        return self

    async def __aexit__(self, *exc):  # type: ignore[no-untyped-def]
        return False

    def __aiter__(self):  # type: ignore[no-untyped-def]
        async def gen():  # type: ignore[no-untyped-def]
            for b in self.blocks:
                if b.type == "text":
                    yield SimpleNamespace(type="content_block_delta", delta=SimpleNamespace(type="text_delta", text=b.text))
        return gen()

    async def get_final_message(self):  # type: ignore[no-untyped-def]
        return SimpleNamespace(content=self.blocks)


class Block(SimpleNamespace):
    def model_dump(self, exclude_none=True):  # type: ignore[no-untyped-def]
        return {k: v for k, v in vars(self).items() if v is not None}


def text(t: str) -> Block:
    return Block(type="text", text=t)


def tool(name: str, id_: str, input_=None) -> Block:  # type: ignore[no-untyped-def]
    return Block(type="tool_use", name=name, id=id_, input=input_ or {})


class FakeClaude:
    def __init__(self, rounds):  # type: ignore[no-untyped-def]
        self.rounds = list(rounds)
        self.seen_tools: list[list[str]] = []
        self.messages = SimpleNamespace(stream=self._stream)

    def _stream(self, **kwargs):  # type: ignore[no-untyped-def]
        self.seen_tools.append([t["name"] for t in kwargs["tools"]])
        return FakeStream(self.rounds.pop(0))


def payload(role: str, text_: str) -> dict:
    return {"mode": "turn", "turn_id": "t1", "lang": "en",
            "ordered_by": {"user_id": "u", "role": role, "display_name": "Somchai" if role != "iam_engineer" else "Wichai"},
            "session": sample_session(), "history": [],
            "message": {"seq": 1, "thread": role, "speaker": role, "text": text_},
            "images": [], "waiting_on": None, "check_order": None, "suggestion_defaults": {}}


def test_offered_tools_by_role() -> None:
    owner = set(loop.offered_tools("application_owner"))
    iam = set(loop.offered_tools("iam_engineer"))
    assert not owner & WRITE_TOOLS
    assert WRITE_TOOLS & set(loop.LEGACY_ISC_TOOLS) <= iam  # spec 002 tools are offered only by playbooks that list them
    assert not any("aws" in name for name in iam | owner)  # no application-side tools exist
    assert {"post_to_other_thread", "notify_other_thread", "set_waiting", "suggest_replies"} <= owner


def test_owner_turn_gets_check_tools_only_under_a_standing_order() -> None:
    order = {"user_id": "u", "display_name": "Wichai"}
    without_source = set(loop.offered_tools("application_owner", order, has_source=False))
    assert not without_source & WRITE_TOOLS
    with_order = set(loop.offered_tools("application_owner", order, has_source=True))
    assert with_order & WRITE_TOOLS == {"peek_accounts", "start_aggregation", "test_connection"}
    assert not {"create_source", "configure_source", "delete_session_source"} & with_order
    assert not set(loop.offered_tools("application_owner", None, has_source=True)) & WRITE_TOOLS


async def test_owner_cannot_trigger_sailpoint_writes(isc, isc_mock, pb, emit) -> None:  # type: ignore[no-untyped-def]
    post = isc_mock.post("/v3/sources")
    # The model tries a write tool anyway; it must be refused without calling SailPoint.
    claude = FakeClaude([[tool("create_source", "u1")], [text("SailPoint changes are ordered by the IAM engineer.")]])
    p = payload("application_owner", "Create the connector")
    await loop.run_turn(p, pb, IscTools(isc, pb, p["session"], emit), SessionTools(emit), emit, claude=claude)
    assert "create_source" not in claude.seen_tools[0]
    assert not post.called
    assert emit.of("action") == []
    assert "IAM engineer" in emit.of("final")[0]["text"]


async def test_iam_order_runs_tools_and_streams(isc, isc_mock, pb, emit) -> None:  # type: ignore[no-untyped-def]
    isc_mock.get("/v3/sources").respond(200, json=[])
    isc_mock.post("/v3/sources").respond(201, json={"id": "2c91808a" * 4, "name": "AWS - Acme Org",
                                                    "connectorAttributes": {"spConnectorInstanceId": "i"}})
    claude = FakeClaude([
        [tool("set_waiting", "a1", {"on": None}), text("Creating it now.")],
        [tool("create_source", "c1")],
        [text("Created AWS - Acme Org.")],
    ])
    p = payload("iam_engineer", "Create the connector")
    session_tools = SessionTools(emit)
    await loop.run_turn(p, pb, IscTools(isc, pb, p["session"], emit), session_tools, emit, claude=claude)
    assert "create_source" in claude.seen_tools[0]
    assert [e["text"] for e in emit.of("progress") if e["text"]] == ["creating the source in SailPoint…"]
    assert len(emit.of("action")) == 1
    final = emit.of("final")[0]
    assert "addressed_to" not in final
    assert "Creating it now." in final["text"] and "Created AWS - Acme Org." in final["text"]
    assert emit.of("delta")


def test_system_prompt_is_filled_and_gated(pb) -> None:  # type: ignore[no-untyped-def]
    owner_prompt = loop.system_prompt(payload("application_owner", "hi"), pb)
    iam_prompt = loop.system_prompt(payload("iam_engineer", "hi"), pb)
    assert "no SailPoint write tools" in owner_prompt
    assert "write tools are available" in iam_prompt
    with_source = payload("application_owner", "Done, I fixed the trust")
    with_source["session"]["source"] = {"id": "s", "name": "AWS - Acme Org"}
    with_source["check_order"] = {"user_id": "u", "display_name": "Wichai"}
    rerun_prompt = loop.system_prompt(with_source, pb)
    assert "that order stands" in rerun_prompt and "Wichai" in rerun_prompt
    for prompt in (owner_prompt, iam_prompt, rerun_prompt):
        assert "{" + "thread_label}" not in prompt and "{" + "suggestion_defaults}" not in prompt
    assert "AWS owner's thread" in owner_prompt and "IAM engineer's thread" in iam_prompt
    for prompt in (owner_prompt, iam_prompt):
        assert "{" + "setup}" not in prompt and "{" + "role_gate}" not in prompt
        assert "SailPointISCRole-acme-demo" in prompt and "5c0d9a3e-7b14-4f2a-9e61-2d8a4c7b1f90" in prompt
        assert "AWS owner" in prompt


async def test_checks_in_one_round_run_in_sailpoint_order(isc, isc_mock, pb, emit) -> None:  # type: ignore[no-untyped-def]
    """F5: Test Connection before the first aggregation fails, so a round asking for all three runs them in order."""
    ran: list[str] = []
    session = sample_session({"id": "s1", "name": "AWS - Acme Org"})
    tools = IscTools(isc, pb, session, emit)
    for name in ("peek_accounts", "start_aggregation", "test_connection"):
        async def record(_n=name):  # type: ignore[no-untyped-def]
            ran.append(_n)
            return {"ok": True}
        setattr(tools, name, record)
    claude = FakeClaude([[tool("test_connection", "t"), tool("start_aggregation", "a"), tool("peek_accounts", "p")],
                         [text("All three ran.")]])
    p = payload("iam_engineer", "Run the checks")
    p["session"] = session
    await loop.run_turn(p, pb, tools, SessionTools(emit), emit, claude=claude)
    assert ran == ["peek_accounts", "start_aggregation", "test_connection"]

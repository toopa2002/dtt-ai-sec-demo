"""Scripted model for the local dev stack and automated tests (Constitution IV, research R28).

Stands in for Claude Haiku in `loop.run_turn` and `loop.secret_check` with the same client surface
(`messages.stream(...)` as an async context manager, `messages.create(...)`), so the real tool loop, the real
SailPoint tools against the ISC stub, the API and the browser all run; only the model's words are scripted. It costs
nothing and answers the same way every time.

Rules live in `tests/fake_model/script.yaml` (or `FAKE_MODEL_SCRIPT`): the first rule whose `when` matches the call
decides the blocks of that round. A round is one model call within a turn; tool results from the previous round are
visible to the next one through `last` (the latest tool results) so a turn can branch on what SailPoint returned.
"""

import json
import os
import re
import uuid
from pathlib import Path
from typing import Any

import yaml
from anthropic.types import Message, TextBlock, ToolUseBlock, Usage

SCRIPT = Path(__file__).resolve().parents[2] / "tests" / "fake_model" / "script.yaml"
LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")


def check_allowed(isc_base_url: str | None) -> None:
    """The scripted model only ever runs against the local ISC stub, never a real tenant."""
    from urllib.parse import urlsplit

    if not isc_base_url or (urlsplit(isc_base_url).hostname or "") not in LOCAL_HOSTS:
        raise RuntimeError("AGENT_MODEL=fake runs only against the local ISC stub (ONBOARDING_ISC_BASE_URL on localhost)")


class _Delta:
    def __init__(self, text: str) -> None:
        self.type = "text_delta"
        self.text = text


class _Event:
    def __init__(self, text: str) -> None:
        self.type = "content_block_delta"
        self.delta = _Delta(text)


class _Stream:
    def __init__(self, message: Message, delay: float = 0) -> None:
        self.message = message
        self.delay = delay

    async def __aenter__(self) -> "_Stream":
        if self.delay:
            import asyncio

            await asyncio.sleep(self.delay)  # a rule's `delay`: lets tests see a busy agent
        return self

    async def __aexit__(self, *exc: Any) -> bool:
        return False

    def __aiter__(self):  # type: ignore[no-untyped-def]
        async def gen():  # type: ignore[no-untyped-def]
            for block in self.message.content:
                if block.type == "text":
                    for piece in re.findall(r"\S+\s*|\s+", block.text):
                        yield _Event(piece)
        return gen()

    async def get_final_message(self) -> Message:
        return self.message


def _text(block: Any) -> str:
    if isinstance(block, dict):
        if block.get("type") == "text":
            return block.get("text", "")
        if block.get("type") == "tool_result":
            content = block.get("content")
            return content if isinstance(content, str) else json.dumps(content)
    return ""


class Call:
    """What a rule can look at for one model call."""

    def __init__(self, system: Any, messages: list[dict], tools: list[dict]) -> None:
        blocks = system if isinstance(system, list) else [{"text": system}]
        self.system = "\n".join(b.get("text", "") for b in blocks)
        self.tools = {t["name"] for t in tools or []}
        start = max((i for i, m in enumerate(messages)
                     if m["role"] == "user" and any(isinstance(b, dict) and b.get("type") == "text"
                                                    for b in m["content"])), default=0)
        turn_msg = messages[start]["content"]
        texts = [b["text"] for b in turn_msg if isinstance(b, dict) and b.get("type") == "text"]
        # The writer's message is the last paragraph of the last user turn ("[IAM engineer] [thread] text").
        parts = re.split(r"\n\n(?=\[(?:IAM engineer|Application owner|System)\])", texts[-1]) if texts else [""]
        self.message = re.sub(r"^\[[^\]]+\](?: \[[^\]]+\])? ", "", parts[-1])
        after = messages[start + 1:]
        self.round = sum(1 for m in after if m["role"] == "assistant")
        last_user = [m for m in after if m["role"] == "user"]
        self.last = " ".join(_text(b) for b in last_user[-1]["content"]) if last_user else ""
        self.results = " ".join(_text(b) for m in last_user for b in m["content"])
        sent_by = re.search(r"This message was sent by: \*\*(IAM engineer|application owner)", self.system)
        self.role = {"IAM engineer": "iam_engineer", "application owner": "application_owner"}.get(
            sent_by.group(1) if sent_by else "", "")
        values = re.search(r"# Session values\n(\{.*\})\s*$", self.system, re.S)
        try:
            self.values: dict = json.loads(values.group(1)) if values else {}
        except json.JSONDecodeError:
            self.values = {}
        self.has_source = bool(self.values.get("source"))

    def placeholders(self) -> dict[str, str]:
        error = re.search(r'"error": "([^"]{1,300})', self.last or self.results)
        steps = self.values.get("steps") or {}
        left = [name.replace("_", " ") for name, state in steps.items() if state != "passed"] or ["nothing"]
        setup = re.search(r"(## Step 1.*?)(?=## Step 3|\Z)", self.system, re.S)
        return {
            "error": error.group(1) if error else "an error",
            "role_name": str(self.values.get("role_name") or "the SailPoint role"),
            "left": ", ".join(left),
            "first_steps": setup.group(1).strip() if setup else "(the setup steps)",
            # spec 002: values the Entra scripts reuse from tool results
            "source_id": (re.search(r'"sources": \[\{"id": "([0-9a-f]{32})"', self.results) or [None, ""])[1],
            "ui_path": json.loads('"' + (re.search(r'"ui_path": "([^"]+)"', self.results)
                                         or [None, "the source in ISC"])[1] + '"'),
        }


def _matches(when: dict, call: Call) -> bool:
    checks = {
        "role": lambda v: call.role == v,
        "text": lambda v: re.search(v, call.message, re.I | re.S) is not None,
        "round": lambda v: call.round == v,
        "min_round": lambda v: call.round >= v,
        "has_source": lambda v: call.has_source == v,
        "last": lambda v: re.search(v, call.last, re.I | re.S) is not None,
        "not_last": lambda v: re.search(v, call.last, re.I | re.S) is None,
        "tool_offered": lambda v: v in call.tools,
        "tool_not_offered": lambda v: v not in call.tools,
        # spec 002: rules for one connector type only (its name as the system prompt states it)
        "connector": lambda v: f"connector type **{v}**" in call.system,
    }
    return all(checks[k](v) for k, v in (when or {}).items())


def _fill(value: Any, ph: dict[str, str]) -> Any:
    if isinstance(value, str):
        return re.sub(r"\{(\w+)\}", lambda m: ph.get(m.group(1), m.group(0)), value)
    if isinstance(value, dict):
        return {k: _fill(v, ph) for k, v in value.items()}
    if isinstance(value, list):
        return [_fill(v, ph) for v in value]
    return value


class _Messages:
    def __init__(self, rules: list[dict]) -> None:
        self.rules = rules

    def _rule(self, call: Call) -> dict | None:
        return next((r for r in self.rules if _matches(r.get("when") or {}, call)), None)

    def _answer(self, call: Call, model: str) -> Message:
        rule = self._rule(call)
        ph = call.placeholders()
        content: list[Any] = []
        for item in _fill((rule or {}).get("then") or [], ph):
            if "text" in item:
                content.append(TextBlock(type="text", text=item["text"]))
            elif item.get("tool") in call.tools:
                content.append(ToolUseBlock(type="tool_use", id=f"toolu_{uuid.uuid4().hex[:12]}",
                                            name=item["tool"], input=item.get("input") or {}))
        if not content and call.round == 0:
            content.append(TextBlock(type="text", text="Thanks, noted."))
        return Message.model_construct(id=f"msg_{uuid.uuid4().hex[:12]}", type="message", role="assistant",
                                       model=model, content=content, stop_reason="end_turn",
                                       usage=Usage(input_tokens=0, output_tokens=0))

    def stream(self, *, system: Any = "", messages: list[dict], tools: list[dict] | None = None,
               model: str = "fake", **_: Any) -> _Stream:
        call = Call(system, messages, tools or [])
        return _Stream(self._answer(call, model), float((self._rule(call) or {}).get("delay") or 0))

    async def create(self, *, messages: list[dict], model: str = "fake", **_: Any) -> Message:
        """The screenshot secret check: passed, unless the image carries an access key id (a test fixture)."""
        import base64

        data = next((b["source"]["data"] for b in messages[0]["content"] if b.get("type") == "image"), "")
        held = b"AKIA" in base64.b64decode(data or b"") if data else False
        text = "HELD: an AWS access key id" if held else "PASSED"
        return Message.model_construct(id=f"msg_{uuid.uuid4().hex[:12]}", type="message", role="assistant",
                                       model=model, content=[TextBlock(type="text", text=text)],
                                       stop_reason="end_turn", usage=Usage(input_tokens=0, output_tokens=0))


class FakeModel:
    def __init__(self, script: Path | None = None) -> None:
        path = Path(os.environ.get("FAKE_MODEL_SCRIPT") or script or SCRIPT)
        self.messages = _Messages(yaml.safe_load(path.read_text())["rules"])

"""Token stripping for text the agent returns outside a turn (the session API masks everything again)."""

from .isc.client import _TOKEN_RE


def strip_tokens(text: str) -> str:
    return _TOKEN_RE.sub("[token]", text)

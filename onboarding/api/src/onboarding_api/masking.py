"""One masker for messages, agent output and logs (FR-026, SC-004, research R9).

Masks credentials only. Identifiers the application side needs stay visible (FR-027): AWS account ids, ARNs, role
names and the tenant External ID (a UUID) are never masked.
"""

import re

MASK = "[masked]"

# Order matters: the most specific patterns first.
_PATTERNS: list[re.Pattern[str]] = [
    # JWTs (Entra / ISC access tokens): three base64url segments starting with eyJ.
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
    # AWS access key ids (long-term AKIA, temporary ASIA).
    re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    # AWS session tokens: very long base64 blobs after a session-token key.
    re.compile(
        r"(?i)(?<=aws_session_token)(?:\"?\s*[:=]\s*\"?)([A-Za-z0-9/+=]{100,})"
    ),
    # AWS secret access keys: 40 base64 chars in key=value / JSON context.
    re.compile(
        r"(?i)(?<=secret_access_key)(?:\"?\s*[:=]\s*\"?)([A-Za-z0-9/+]{40})(?![A-Za-z0-9/+])"
    ),
    re.compile(r"(?i)(?<=secretaccesskey)(?:\"?\s*[:=]\s*\"?)([A-Za-z0-9/+]{40})(?![A-Za-z0-9/+])"),
    # ISC personal access token secrets: 64 hex chars.
    re.compile(r"\b[0-9a-f]{64}\b"),
    # Generic key: value pairs for passwords / secrets / tokens.
    re.compile(
        r"(?i)\b(?:password|passwd|pwd|client_secret|secret|api[_-]?key|access[_-]?token|refresh[_-]?token|token)"
        r"(\"?\s*[:=]\s*\"?)([^\s\",;}{]{6,})"
    ),
]

# Bare 40-char AWS secret keys on their own (e.g. pasted from the console's "show secret" box).
_BARE_AWS_SECRET = re.compile(r"(?<![A-Za-z0-9/+])(?=[A-Za-z0-9/+]*[/+])(?=[A-Za-z0-9/+]*[a-z])"
                              r"(?=[A-Za-z0-9/+]*[A-Z])[A-Za-z0-9/+]{40}(?![A-Za-z0-9/+=])")


def mask(text: str) -> tuple[str, bool]:
    """Return (masked_text, changed)."""
    if not text:
        return text, False
    out = text
    for i, pattern in enumerate(_PATTERNS):
        if pattern.groups == 0:
            out = pattern.sub(MASK, out)
        elif pattern.groups == 1:
            out = pattern.sub(lambda m: m.group(0).replace(m.group(1), MASK), out)
        else:  # generic key: value — keep the key and separator, mask the value
            out = pattern.sub(lambda m: m.group(0)[: m.start(2) - m.start(0)] + MASK, out)
        del i
    out = _BARE_AWS_SECRET.sub(MASK, out)
    return out, out != text


def mask_text(text: str) -> str:
    return mask(text)[0]


def mask_obj(value):  # type: ignore[no-untyped-def]
    """Mask every string inside a JSON-like structure (agent events, action summaries)."""
    if isinstance(value, str):
        return mask_text(value)
    if isinstance(value, list):
        return [mask_obj(v) for v in value]
    if isinstance(value, dict):
        return {k: mask_obj(v) for k, v in value.items()}
    return value

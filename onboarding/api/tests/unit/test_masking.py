"""SC-004 corpus: every credential family is masked; identifiers the application side needs are not (FR-027)."""

import pytest

from onboarding_api.masking import MASK, mask, mask_obj

SESSION_TOKEN = "IQoJb3JpZ2luX2VjEJr//////////wEaDmFwLXNvdXRoZWFzdC0xIkcwRQIhAK" * 3

# (text as pasted, the fragment that must not survive)
SECRETS = [
    ("AKIAIOSFODNN7EXAMPLE", "AKIAIOSFODNN7EXAMPLE"),
    ("ASIAY34FZKBOKMUTVV7A", "ASIAY34FZKBOKMUTVV7A"),
    ("aws_secret_access_key = wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY", "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"),
    ('"SecretAccessKey": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"', "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"),
    ("wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY", "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"),
    ("aws_session_token=" + SESSION_TOKEN, SESSION_TOKEN),
    ("Bearer eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.c2lnbmF0dXJlc2lnbmF0dXJl", "eyJ0eXAi"),
    ("3f6c1d0e9b8a7f6e5d4c3b2a19087f6e5d4c3b2a19087f6e5d4c3b2a19087f6e", "3f6c1d0e9b8a7f6e"),
    ("password: Hunter2-is-not-safe", "Hunter2-is-not-safe"),
    ("client_secret=s3cr3t-value-123", "s3cr3t-value-123"),
    ('{"token": "abcdef123456"}', "abcdef123456"),
]

KEEP = [
    "111122223333",  # AWS account id
    "arn:aws:iam::111122223333:role/SailPointISCRole-acme-demo",
    "arn:aws:iam::874540850173:role/ciem_universal",
    "5c0d9a3e-7b14-4f2a-9e61-2d8a4c7b1f90",  # tenant External ID (UUID)
    "acme-demo.api.identitynow-demo.com",
    "aws iam get-role --role-name SailPointISCRole-acme-demo --query Role.AssumeRolePolicyDocument",
    "spConnectorSpecId 6e47875b-73f1-481d-a613-59d4deca0c6a",
    "client id a1b2c3d4e5f60718293a4b5c6d7e8f90",  # 32-hex PAT client id is an identifier, not a secret
]


@pytest.mark.parametrize(("text", "secret"), SECRETS)
def test_secrets_are_masked(text: str, secret: str) -> None:
    out, changed = mask(text)
    assert changed, out
    assert MASK in out
    assert secret not in out, out


@pytest.mark.parametrize("text", KEEP)
def test_identifiers_are_kept(text: str) -> None:
    out, changed = mask(text)
    assert not changed, out
    assert out == text


def test_mask_obj_walks_structures() -> None:
    out = mask_obj({"a": ["AKIAIOSFODNN7EXAMPLE", 3], "b": {"c": "fine"}})
    assert out == {"a": [MASK, 3], "b": {"c": "fine"}}


def test_mixed_message_keeps_context() -> None:
    text = "Here are my keys AKIAIOSFODNN7EXAMPLE and the role arn:aws:iam::111122223333:role/R"
    out, changed = mask(text)
    assert changed
    assert "arn:aws:iam::111122223333:role/R" in out
    assert "AKIAIOSFODNN7EXAMPLE" not in out

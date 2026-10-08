"""Where an application secret lives between the owner's secret field and the ISC source (spec 002 research R1).

Default: an AgentCore Identity API-key credential provider per session, `onboarding-entra-<session_id>`; the agent
reads it in tool code with its workload token (agent `isc/vault.py`). Fallback: a Secrets Manager secret of the same
name (CREDENTIAL_STORE=secrets-manager). Local stub runs (CREDENTIAL_STORE=discard): an in-memory store the local agent
reads through the guarded `/_local/apikey/{name}` route, so the stub ISC receives the exact submitted value and the
leak test can prove the path. The value is never logged, returned or stored anywhere else.
"""

import hashlib
import logging
import os
import re

import boto3
from botocore.exceptions import ClientError, NoCredentialsError

from ..config import settings
from ..tenants.identity import NO_AWS_CREDENTIALS, CredentialStoreError

log = logging.getLogger(__name__)
PREFIX = "onboarding-entra-"


def provider_name(session_id: str) -> str:
    return PREFIX + re.sub(r"[^a-zA-Z0-9-]", "", str(session_id))[:60]


class ApiKeyStore:
    def put(self, name: str, value: str) -> None:
        raise NotImplementedError

    def delete(self, name: str) -> None:
        raise NotImplementedError


class AgentCoreApiKeyStore(ApiKeyStore):
    def __init__(self) -> None:
        self.client = boto3.client("bedrock-agentcore-control", region_name=settings().aws_region)

    def put(self, name: str, value: str) -> None:
        try:
            self.client.create_api_key_credential_provider(name=name, apiKey=value)
            log.info("api key provider created", extra={"status": name})
        except NoCredentialsError:
            raise CredentialStoreError(NO_AWS_CREDENTIALS) from None
        except ClientError as exc:
            code = exc.response["Error"]["Code"]
            if code not in ("ConflictException", "ValidationException") or "exist" not in str(exc).lower():
                raise CredentialStoreError(f"AgentCore Identity refused the secret: {code}") from None
            try:
                self.client.update_api_key_credential_provider(name=name, apiKey=value)
            except ClientError as again:
                raise CredentialStoreError(
                    f"AgentCore Identity refused the secret: {again.response['Error']['Code']}") from None
            log.info("api key provider updated", extra={"status": name})

    def delete(self, name: str) -> None:
        try:
            self.client.delete_api_key_credential_provider(name=name)
        except NoCredentialsError:
            raise CredentialStoreError(NO_AWS_CREDENTIALS) from None
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "ResourceNotFoundException":
                raise CredentialStoreError(
                    f"AgentCore Identity refused the delete: {exc.response['Error']['Code']}") from None


class SecretsManagerApiKeyStore(ApiKeyStore):
    def __init__(self) -> None:
        self.client = boto3.client("secretsmanager", region_name=settings().aws_region)

    def put(self, name: str, value: str) -> None:
        try:
            self.client.create_secret(Name=name, SecretString=value,
                                      Tags=[{"Key": "ManagedBy", "Value": "onboarding"}])
        except NoCredentialsError:
            raise CredentialStoreError(NO_AWS_CREDENTIALS) from None
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "ResourceExistsException":
                raise CredentialStoreError(
                    f"Secrets Manager refused the secret: {exc.response['Error']['Code']}") from None
            self.client.put_secret_value(SecretId=name, SecretString=value)

    def delete(self, name: str) -> None:
        try:
            self.client.delete_secret(SecretId=name, ForceDeleteWithoutRecovery=True)
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "ResourceNotFoundException":
                raise CredentialStoreError(
                    f"Secrets Manager refused the delete: {exc.response['Error']['Code']}") from None


class LocalApiKeyStore(ApiKeyStore):
    """Stub runs only: values in this process's memory, plus their SHA-256 for the leak scan."""

    values: dict[str, str] = {}
    fingerprints: dict[str, str] = {}

    def put(self, name: str, value: str) -> None:
        self.values[name] = value
        self.fingerprints[name] = hashlib.sha256(value.encode()).hexdigest()
        log.info("secret kept in memory (local stub mode)", extra={"status": name})

    def delete(self, name: str) -> None:
        self.values.pop(name, None)


def local_mode() -> bool:
    """The in-memory store and its route exist only against the local ISC stub, never on the cluster."""
    base = settings().isc_base_url
    return settings().credential_store == "discard" and base.startswith(("http://127.0.0.1", "http://localhost"))


def check_startup() -> None:
    if settings().credential_store == "discard" and os.environ.get("KUBERNETES_SERVICE_HOST"):
        raise RuntimeError("CREDENTIAL_STORE=discard is for local stub runs only; refusing to start on a cluster")


def store() -> ApiKeyStore:
    kind = settings().credential_store
    if kind == "secrets-manager":
        return SecretsManagerApiKeyStore()
    if kind == "discard":
        return LocalApiKeyStore()
    return AgentCoreApiKeyStore()

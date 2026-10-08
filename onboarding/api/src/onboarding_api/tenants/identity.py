"""Where the SailPoint credential lives (FR-025, research R4).

Default: an AgentCore Identity custom OAuth2 provider per tenant (client-credentials / M2M). The client secret passes
through this module once and is never stored, logged or returned. Fallback: AWS Secrets Manager, read by the agent
role (set CREDENTIAL_STORE=secrets-manager).
"""

import json
import logging
import re

import boto3
from botocore.exceptions import ClientError, NoCredentialsError

from ..config import settings

log = logging.getLogger(__name__)


NO_AWS_CREDENTIALS = ("the API has no AWS credentials (Kubernetes Secret onboarding/onboarding-aws is missing; "
                      "make onboarding-agent writes it)")


class CredentialStoreError(RuntimeError):
    """The credential store refused the tenant credential (AWS error code only, never the credential)."""


def provider_name(tenant_name: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9-]+", "-", tenant_name).strip("-").lower()[:40]
    return f"onboarding-isc-{slug}"


def _provider_config(api_host: str, client_id: str, client_secret: str) -> dict:
    base = f"https://{api_host}"
    return {
        "customOauth2ProviderConfig": {
            "oauthDiscovery": {
                "authorizationServerMetadata": {
                    "issuer": base,
                    "authorizationEndpoint": f"{base}/oauth/authorize",
                    "tokenEndpoint": f"{base}/oauth/token",
                    "tokenEndpointAuthMethods": ["client_secret_post"],
                }
            },
            "clientId": client_id,
            "clientSecret": client_secret,
        }
    }


class CredentialStore:
    def put(self, name: str, api_host: str, client_id: str, client_secret: str) -> None:
        raise NotImplementedError

    def delete(self, name: str) -> None:
        raise NotImplementedError


class AgentCoreIdentityStore(CredentialStore):
    def __init__(self) -> None:
        self.client = boto3.client("bedrock-agentcore-control", region_name=settings().aws_region)

    def put(self, name: str, api_host: str, client_id: str, client_secret: str) -> None:
        config = _provider_config(api_host, client_id, client_secret)
        try:
            self.client.create_oauth2_credential_provider(
                # No tags: tagging needs bedrock-agentcore:TagResource, which the API's IAM user doesn't have;
                # providers are found by their onboarding-isc- name prefix instead.
                name=name, credentialProviderVendor="CustomOauth2", oauth2ProviderConfigInput=config,
            )
            log.info("credential provider created", extra={"status": name})
        except NoCredentialsError:
            raise CredentialStoreError(NO_AWS_CREDENTIALS) from None
        except ClientError as exc:
            if exc.response["Error"]["Code"] not in ("ConflictException", "ValidationException") or \
                    "exist" not in str(exc).lower():
                raise CredentialStoreError(f"AgentCore Identity refused the credential: {exc.response['Error']['Code']}") \
                    from None
            self.client.update_oauth2_credential_provider(
                name=name, credentialProviderVendor="CustomOauth2", oauth2ProviderConfigInput=config,
            )
            log.info("credential provider updated", extra={"status": name})

    def delete(self, name: str) -> None:
        try:
            self.client.delete_oauth2_credential_provider(name=name)
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "ResourceNotFoundException":
                raise


class SecretsManagerStore(CredentialStore):
    def __init__(self) -> None:
        self.client = boto3.client("secretsmanager", region_name=settings().aws_region)

    def put(self, name: str, api_host: str, client_id: str, client_secret: str) -> None:
        value = json.dumps({"api_host": api_host, "client_id": client_id, "client_secret": client_secret})
        try:
            self.client.create_secret(Name=name, SecretString=value, Tags=[{"Key": "ManagedBy", "Value": "onboarding"}])
        except NoCredentialsError:
            raise CredentialStoreError(NO_AWS_CREDENTIALS) from None
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "ResourceExistsException":
                raise CredentialStoreError(f"Secrets Manager refused the credential: {exc.response['Error']['Code']}") \
                    from None
            self.client.put_secret_value(SecretId=name, SecretString=value)

    def delete(self, name: str) -> None:
        self.client.delete_secret(SecretId=name, ForceDeleteWithoutRecovery=True)


class DiscardStore(CredentialStore):
    """Local runs against the ISC stub only (CREDENTIAL_STORE=discard): the secret is checked once and dropped.
    The local agent then uses ONBOARDING_ISC_TOKEN. Never used on the cluster."""

    def put(self, name: str, api_host: str, client_id: str, client_secret: str) -> None:
        log.info("credential discarded (local stub mode)", extra={"status": name})

    def delete(self, name: str) -> None:
        return None


def store() -> CredentialStore:
    kind = settings().credential_store
    if kind == "secrets-manager":
        return SecretsManagerStore()
    if kind == "discard":
        return DiscardStore()
    return AgentCoreIdentityStore()

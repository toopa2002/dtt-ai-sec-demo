"""Settings, read once from the environment (Kubernetes ConfigMap + Secret)."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", case_sensitive=False)

    mongo_uri: str = "mongodb://localhost:27017/?replicaSet=rs0&directConnection=true"
    mongo_db: str = "onboarding"
    aws_region: str = "ap-southeast-1"
    agent_runtime_arn: str = ""
    # Local development / end-to-end tests: an agent served over HTTP (e.g. `python -m onboarding_agent.main` on
    # :8090, whose /invocations route is what AgentCore calls). When set, AgentCore is not used.
    # Standalone AgentCore workload identity the agent uses for SailPoint tokens. The runtime's own identity is
    # service-linked and only gets a token when the caller names an end user, which the API (a service) doesn't.
    agent_workload_name: str = "isc-onboarding-agent"
    agent_endpoint: str = ""
    catalog_dir: Path = Path(__file__).resolve().parents[3] / "catalog"
    cookie_secure: bool = True
    base_path: str = "/onboarding/api"
    # The web app's production build (the image sets it); served under base_path's parent. Empty: API only.
    web_dir: str = ""
    # Fallback credential store when AgentCore Identity custom providers are unavailable (research R4).
    credential_store: str = "agentcore-identity"  # or "secrets-manager"
    # Test/stub override: send ISC tenant checks to this base URL instead of https://<api_host>.
    isc_base_url: str = ""
    idle_minutes: int = 30
    lockout_attempts: int = 5
    lockout_minutes: int = 15
    retention_days: int = 90
    history_messages: int = 40


@lru_cache
def settings() -> Settings:
    return Settings()

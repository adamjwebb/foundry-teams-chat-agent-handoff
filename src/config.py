import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse
from uuid import UUID

import dotenv


def load_handoff_environment(path: Path | None = None) -> None:
    dotenv.load_dotenv(
        dotenv_path=path or Path(__file__).resolve().parents[1] / ".env.handoff",
        override=False,
    )


@dataclass(frozen=True)
class HandoffConfig:
    human_upn: str = ""
    tenant_id: str = ""
    broker_enabled: str = "false"
    human_object_id: str = ""
    catalog_app_id: str = ""

    @classmethod
    def from_env(cls, env):
        return cls(
            *(env.get(name, default).strip() for name, default in (
                ("HANDOFF_HUMAN_UPN", ""),
                ("HANDOFF_TENANT_ID", ""),
                ("HANDOFF_BROKER_ENABLED", "false"),
                ("HANDOFF_HUMAN_OBJECT_ID", ""),
                ("HANDOFF_TEAMS_CATALOG_APP_ID", ""),
            ))
        )

    def validate(self, mode: str) -> None:
        if not re.fullmatch(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}", self.human_upn):
            raise ValueError("Set HANDOFF_HUMAN_UPN to one helpdesk user's UPN.")
        self._guid(self.tenant_id, "HANDOFF_TENANT_ID")
        if mode == "broker":
            if self.broker_enabled.lower() != "true":
                raise ValueError("Set HANDOFF_BROKER_ENABLED=true to enable the broker example.")
            self._guid(self.human_object_id, "HANDOFF_HUMAN_OBJECT_ID")
            self._guid(self.catalog_app_id, "HANDOFF_TEAMS_CATALOG_APP_ID")
        elif mode != "link":
            raise ValueError("Unknown handoff mode.")

    @staticmethod
    def _guid(value: str, name: str) -> None:
        try:
            if UUID(value).int == 0:
                raise ValueError("Empty UUID")
        except (ValueError, AttributeError) as error:
            raise ValueError(f"Set {name} to a nonempty GUID.") from error


class Config:
    """Runtime configuration for the Teams-hosted Foundry agent."""

    def __init__(self, env):
        self.handoff = HandoffConfig.from_env(env)
        self.project_endpoint = self._required(env, "AZURE_AI_PROJECT_ENDPOINT")
        self._validate_project_endpoint(self.project_endpoint)
        self.model_deployment_name = self._required(
            env, "AZURE_AI_MODEL_DEPLOYMENT_NAME"
        )
        self.managed_identity_client_id = env.get("AZURE_CLIENT_ID", "").strip() or None

        self.graph_auth_mode = env.get("GRAPH_AUTH_MODE", "default").strip()
        if self.graph_auth_mode not in ("default", "bot"):
            raise RuntimeError("GRAPH_AUTH_MODE must be 'default' or 'bot'")
        self.graph_bot_credentials: tuple[str, str, str] | None = None
        if self.graph_auth_mode == "bot":
            self.graph_bot_credentials = (
                self._required(
                    env, "CONNECTIONS__SERVICE_CONNECTION__SETTINGS__TENANTID"
                ),
                self._required(
                    env, "CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTID"
                ),
                self._required(
                    env, "CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTSECRET"
                ),
            )

        raw_count = env.get("GRAPH_HISTORY_MESSAGE_COUNT", "10")
        try:
            self.graph_history_message_count = int(raw_count)
        except ValueError as error:
            raise RuntimeError(
                "GRAPH_HISTORY_MESSAGE_COUNT must be an integer"
            ) from error
        if not 1 <= self.graph_history_message_count <= 50:
            raise RuntimeError(
                "GRAPH_HISTORY_MESSAGE_COUNT must be between 1 and 50"
            )

    @staticmethod
    def _required(env, name):
        value = env.get(name, "").strip()
        if not value:
            raise RuntimeError(f"{name} is required")
        return value

    @staticmethod
    def _validate_project_endpoint(value):
        parsed = urlparse(value)
        path_parts = parsed.path.strip("/").split("/")
        if (
            parsed.scheme != "https"
            or not parsed.netloc
            or len(path_parts) != 3
            or path_parts[:2] != ["api", "projects"]
            or not path_parts[2]
            or parsed.params
            or parsed.query
            or parsed.fragment
        ):
            raise RuntimeError(
                "AZURE_AI_PROJECT_ENDPOINT must be a Foundry project endpoint "
                "ending in /api/projects/<project>, not an agent or model endpoint"
            )

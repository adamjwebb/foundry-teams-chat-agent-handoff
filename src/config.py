from urllib.parse import urlparse


class Config:
    """Runtime configuration for the Teams-hosted Foundry agent."""

    def __init__(self, env):
        self.project_endpoint = self._required(env, "AZURE_AI_PROJECT_ENDPOINT")
        self._validate_project_endpoint(self.project_endpoint)
        self.model_deployment_name = self._required(
            env, "AZURE_AI_MODEL_DEPLOYMENT_NAME"
        )
        self.managed_identity_client_id = env.get("AZURE_CLIENT_ID", "").strip() or None

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

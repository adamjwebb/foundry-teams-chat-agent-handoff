import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock, Mock

import pytest

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "src"))


@pytest.mark.parametrize("mode", [None, "default", "bot"])
def test_agent_selects_graph_identity_without_changing_foundry(monkeypatch, mode):
    settings = {
        "AZURE_AI_PROJECT_ENDPOINT": "https://example.test/api/projects/project",
        "AZURE_AI_MODEL_DEPLOYMENT_NAME": "model",
        "AZURE_CLIENT_ID": "managed-identity-id",
        "GRAPH_HISTORY_MESSAGE_COUNT": "10",
        "CONNECTIONS__SERVICE_CONNECTION__SETTINGS__TENANTID": "tenant-id",
        "CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTID": "bot-id",
        "CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTSECRET": "test-secret",
    }
    for name, value in settings.items():
        monkeypatch.setenv(name, value)
    if mode is None:
        monkeypatch.delenv("GRAPH_AUTH_MODE", raising=False)
    else:
        monkeypatch.setenv("GRAPH_AUTH_MODE", mode)

    default_credential = Mock()
    bot_credential = Mock()
    gateway = Mock()
    client = Mock()
    client.get.return_value.json.return_value = {"value": []}
    for name, replacement in {
        "dotenv.load_dotenv": Mock(),
        "azure.identity.DefaultAzureCredential": default_credential,
        "azure.identity.ClientSecretCredential": bot_credential,
        "foundry.FoundryModelGateway": gateway,
        "httpx.Client": Mock(return_value=client),
        "microsoft_agents.activity.load_configuration_from_env": Mock(return_value={}),
        "microsoft_agents.authentication.msal.MsalConnectionManager": Mock(),
        "microsoft_agents.hosting.aiohttp.CloudAdapter": Mock(),
        "microsoft_agents.hosting.core.AgentApplication": MagicMock(),
    }.items():
        monkeypatch.setattr(name, replacement)

    spec = importlib.util.spec_from_file_location(
        "agent_under_test", ROOT / "src/agent.py"
    )
    assert spec is not None and spec.loader is not None
    agent = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(agent)

    default_credential.assert_called_once_with(
        managed_identity_client_id="managed-identity-id"
    )
    gateway.assert_called_once_with(
        settings["AZURE_AI_PROJECT_ENDPOINT"], "model", default_credential.return_value
    )
    if mode == "bot":
        bot_credential.assert_called_once_with("tenant-id", "bot-id", "test-secret")
        graph_credential = bot_credential.return_value
        default_credential.return_value.get_token.assert_not_called()
    else:
        bot_credential.assert_not_called()
        graph_credential = default_credential.return_value

    assert agent.graph_chat_history.read("chat-id") == []
    graph_credential.get_token.assert_called_once_with(
        "https://graph.microsoft.com/.default"
    )

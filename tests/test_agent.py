import asyncio
import importlib.util
import sys
from pathlib import Path
from unittest.mock import MagicMock, Mock

import httpx
import pytest

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "src"))


@pytest.mark.parametrize("mode", [None, "default", "bot", "federated"])
def test_agent_selects_graph_history_and_broker_identities_without_changing_foundry(monkeypatch, mode):
    settings = {
        "AZURE_AI_PROJECT_ENDPOINT": "https://example.test/api/projects/project",
        "AZURE_AI_MODEL_DEPLOYMENT_NAME": "model",
        "AZURE_CLIENT_ID": "managed-identity-id",
        "GRAPH_HISTORY_MESSAGE_COUNT": "10",
        "GRAPH_TENANT_ID": "tenant-id",
        "GRAPH_CLIENT_ID": "registered-rsc-app-id",
        "HANDOFF_TEAMS_CATALOG_APP_ID": "44444444-4444-4444-8444-444444444444",
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
    assertion_credential = Mock()
    managed_identity_credential = Mock()
    default_credential.return_value.get_token.return_value.token = "azure-token"
    bot_credential.return_value.get_token.return_value.token = "bot-app-token"
    assertion_credential.return_value.get_token.return_value.token = "rsc-app-token"
    managed_identity_credential.return_value.get_token.return_value.token = "mi-assertion"
    gateway = Mock()
    client = Mock()
    client.get.return_value.json.return_value = {"value": []}
    for name, replacement in {
        "dotenv.load_dotenv": Mock(),
        "azure.identity.DefaultAzureCredential": default_credential,
        "azure.identity.ClientSecretCredential": bot_credential,
        "azure.identity.ClientAssertionCredential": assertion_credential,
        "azure.identity.ManagedIdentityCredential": managed_identity_credential,
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
    elif mode == "federated":
        managed_identity_credential.assert_called_once_with(client_id="managed-identity-id")
        tenant, client_id, get_assertion = assertion_credential.call_args.args
        assert (tenant, client_id) == ("tenant-id", "registered-rsc-app-id")
        assert get_assertion() == "mi-assertion"
        managed_identity_credential.return_value.get_token.assert_called_once_with(
            "api://AzureADTokenExchange/.default"
        )
        graph_credential = assertion_credential.return_value
        default_credential.return_value.get_token.assert_not_called()
        bot_credential.assert_not_called()
    else:
        bot_credential.assert_not_called()
        graph_credential = default_credential.return_value

    assert agent.graph_chat_history.read("chat-id") == []
    graph_credential.get_token.assert_called_once_with(
        "https://graph.microsoft.com/.default"
    )

    requests = []

    def install(request):
        requests.append(request)
        return httpx.Response(201)

    broker_http = httpx.AsyncClient(transport=httpx.MockTransport(install))
    monkeypatch.setattr(agent.httpx, "AsyncClient", lambda: broker_http)

    async def install_with_broker():
        try:
            await agent.get_handoff_broker().graph.install_support_app("support-chat")
        finally:
            await agent.close_handoff_resources()

    asyncio.run(install_with_broker())
    expected_token = {
        "federated": "mi-assertion",
        "bot": "bot-app-token",
    }.get(mode, "azure-token")
    assert len(requests) == 1
    assert requests[0].headers["authorization"].split(" ", 1) == ["Bearer", expected_token]

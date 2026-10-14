import json
from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_manifest_limits_scopes_and_requests_only_chat_history_rsc():
    manifest = json.loads((ROOT / "appPackage/manifest.json").read_text())

    assert len(manifest["bots"]) == 1
    assert manifest["bots"][0]["botId"] == "${{BOT_ID}}"
    assert manifest["bots"][0]["scopes"] == ["groupChat", "personal"]
    assert manifest["bots"][0]["supportsFiles"] is False
    assert manifest["webApplicationInfo"] == {
        "id": "${{BOT_ID}}",
        "resource": "https://RscBasedStoreApp",
    }
    assert manifest["authorization"]["permissions"]["resourceSpecific"] == [
        {"name": "ChatMessage.Read.Chat", "type": "Application"}
    ]
    assert "team" not in manifest["bots"][0]["scopes"]
    assert "copilotAgents" not in manifest
    assert "supportsChannelFeatures" not in manifest


def test_agent_infrastructure_uses_managed_identity_for_foundry():
    bicep = (ROOT / "infra/azure.bicep").read_text()

    assert "name: 'AZURE_CLIENT_ID'" in bicep
    assert "name: 'AZURE_AI_PROJECT_ENDPOINT'" in bicep
    assert "name: 'AZURE_AI_MODEL_DEPLOYMENT_NAME'" in bicep
    assert "AZURE_OPENAI_API_KEY" not in bicep


def test_local_teams_selects_bot_graph_auth_and_playground_resets_it():
    local = (ROOT / "m365agents.local.yml").read_text()
    playground = (ROOT / "m365agents.playground.yml").read_text()

    assert 'GRAPH_AUTH_MODE: "bot"' in local
    assert 'GRAPH_AUTH_MODE: "default"' in playground

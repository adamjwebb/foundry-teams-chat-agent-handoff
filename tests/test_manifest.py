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


def test_handoff_deployment_settings_are_optional_and_single_worker():
    bicep = (ROOT / "infra/azure.bicep").read_text()
    names = {
        "handoffHumanUpn": "HANDOFF_HUMAN_UPN",
        "handoffTenantId": "HANDOFF_TENANT_ID",
        "handoffBrokerEnabled": "HANDOFF_BROKER_ENABLED",
        "handoffHumanObjectId": "HANDOFF_HUMAN_OBJECT_ID",
        "handoffTeamsCatalogAppId": "HANDOFF_TEAMS_CATALOG_APP_ID",
    }
    for parameter, environment in names.items():
        default = "'false'" if parameter == "handoffBrokerEnabled" else "''"
        assert f"param {parameter} string = {default}" in bicep
        assert f"name: '{environment}'" in bicep
        assert f"value: {parameter}" in bicep
    assert "--workers 1" in bicep
    assert ".env.handoff" in (ROOT / ".gitignore").read_text().splitlines()


def test_container_deployment_keeps_single_instance_and_managed_identity():
    bicep = (ROOT / "infra/containerapps.bicep").read_text()
    dockerfile = (ROOT / "Dockerfile").read_text()
    requirements = (ROOT / "src/requirements.txt").read_text()

    assert "activeRevisionsMode: 'Single'" in bicep
    assert "minReplicas: 1" in bicep
    assert "maxReplicas: 1" in bicep
    assert "adminUserEnabled: false" in bicep
    assert "allowInsecure: false" in bicep
    assert "targetPort: 8000" in bicep
    assert "identity: identity.id" in bicep
    assert "name: 'AZURE_CLIENT_ID'" in bicep
    assert "name: 'HANDOFF_TEAMS_CATALOG_APP_ID'" in bicep
    assert "param deployApp bool = true" in bicep
    assert "if (deployApp)" in bicep
    assert "CLIENTSECRET" not in bicep
    assert 'USER app' in dockerfile
    assert '"--workers", "1"' in dockerfile
    assert '"0.0.0.0:8000"' in dockerfile
    assert any(line.startswith("gunicorn") for line in requirements.splitlines())

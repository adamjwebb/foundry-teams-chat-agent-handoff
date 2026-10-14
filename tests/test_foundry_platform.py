import json
from pathlib import Path


ROOT = Path(__file__).parents[1]
PLATFORM = ROOT / "foundry-platform"


def test_foundry_azd_project_is_infrastructure_only():
    config = (PLATFORM / "azure.yaml").read_text()

    assert "resourceGroup: ${AZURE_RESOURCE_GROUP}" in config
    assert "provider: bicep" in config
    assert "path: ./infra" in config
    assert "module: main" in config
    assert "services:" not in config
    assert "hooks:" not in config
    assert "azure.ai.agents" not in config
    assert not (ROOT / "azure.yaml").exists()
    assert (ROOT / "m365agents.yml").exists()


def test_foundry_azd_parameters_bind_environment_and_location():
    parameters = json.loads(
        (PLATFORM / "infra/main.parameters.json").read_text()
    )["parameters"]

    assert parameters == {
        "environmentName": {"value": "${AZURE_ENV_NAME}"},
        "location": {"value": "${AZURE_LOCATION}"},
    }
    template = (PLATFORM / "infra/main.bicep").read_text()
    for name in parameters:
        assert f"param {name} string" in template


def test_foundry_azd_state_is_ignored():
    assert "/foundry-platform/.azure/" in (ROOT / ".gitignore").read_text().splitlines()

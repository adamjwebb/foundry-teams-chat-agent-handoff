import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from config import Config
from foundry import (
    FoundryModelGateway,
    GraphChatHistory,
    is_bot_mentioned,
    normalize_message,
)


def test_config_reads_foundry_settings():
    config = Config(
        {
            "AZURE_AI_PROJECT_ENDPOINT": (
                "https://example.test/api/projects/example-project"
            ),
            "AZURE_AI_MODEL_DEPLOYMENT_NAME": "gpt-4.1-mini",
            "GRAPH_HISTORY_MESSAGE_COUNT": "25",
        }
    )

    assert config.project_endpoint == (
        "https://example.test/api/projects/example-project"
    )
    assert config.model_deployment_name == "gpt-4.1-mini"
    assert config.graph_history_message_count == 25


def test_config_defaults_to_shared_graph_credential():
    config = Config(
        {
            "AZURE_AI_PROJECT_ENDPOINT": "https://example.test/api/projects/project",
            "AZURE_AI_MODEL_DEPLOYMENT_NAME": "model",
        }
    )

    assert config.graph_auth_mode == "default"
    assert config.graph_bot_credentials is None


def test_config_reads_bot_graph_credentials():
    config = Config(
        {
            "AZURE_AI_PROJECT_ENDPOINT": "https://example.test/api/projects/project",
            "AZURE_AI_MODEL_DEPLOYMENT_NAME": "model",
            "GRAPH_AUTH_MODE": "bot",
            "CONNECTIONS__SERVICE_CONNECTION__SETTINGS__TENANTID": "tenant-id",
            "CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTID": "bot-id",
            "CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTSECRET": "test-secret",
        }
    )

    assert config.graph_auth_mode == "bot"
    assert config.graph_bot_credentials == ("tenant-id", "bot-id", "test-secret")


@pytest.mark.parametrize("mode", ["", "developer", "bto"])
def test_config_rejects_invalid_graph_auth_mode(mode):
    with pytest.raises(RuntimeError, match="GRAPH_AUTH_MODE"):
        Config(
            {
                "AZURE_AI_PROJECT_ENDPOINT": (
                    "https://example.test/api/projects/project"
                ),
                "AZURE_AI_MODEL_DEPLOYMENT_NAME": "model",
                "GRAPH_AUTH_MODE": mode,
            }
        )


@pytest.mark.parametrize("setting", ["TENANTID", "CLIENTID", "CLIENTSECRET"])
def test_config_requires_complete_bot_graph_credentials(setting):
    env = {
        "AZURE_AI_PROJECT_ENDPOINT": "https://example.test/api/projects/project",
        "AZURE_AI_MODEL_DEPLOYMENT_NAME": "model",
        "GRAPH_AUTH_MODE": "bot",
        "CONNECTIONS__SERVICE_CONNECTION__SETTINGS__TENANTID": "tenant-id",
        "CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTID": "bot-id",
        "CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTSECRET": "test-secret",
    }
    name = f"CONNECTIONS__SERVICE_CONNECTION__SETTINGS__{setting}"
    env[name] = " "

    with pytest.raises(RuntimeError, match=f"{name} is required"):
        Config(env)


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://example.test/api/projects/example-project/agents/example-agent",
        "https://example.test/openai/deployments/example-model",
        "not-a-url",
    ],
)
def test_config_rejects_non_project_endpoint(endpoint):
    with pytest.raises(RuntimeError, match="Foundry project endpoint"):
        Config(
            {
                "AZURE_AI_PROJECT_ENDPOINT": endpoint,
                "AZURE_AI_MODEL_DEPLOYMENT_NAME": "model",
            }
        )


@pytest.mark.parametrize("count", ["0", "51", "invalid"])
def test_config_rejects_invalid_history_count(count):
    with pytest.raises(RuntimeError, match="GRAPH_HISTORY_MESSAGE_COUNT"):
        Config(
            {
                "AZURE_AI_PROJECT_ENDPOINT": (
                    "https://example.test/api/projects/example-project"
                ),
                "AZURE_AI_MODEL_DEPLOYMENT_NAME": "model",
                "GRAPH_HISTORY_MESSAGE_COUNT": count,
            }
        )


def test_normalize_message_removes_mention():
    assert normalize_message(" <at>Demo Foundry Agent</at>  summarize this ") == (
        "summarize this"
    )


def test_bot_mention_matches_recipient():
    activity = SimpleNamespace(
        recipient=SimpleNamespace(id="bot-id"),
        entities=[{"type": "mention", "mentioned": {"id": "BOT-ID"}}],
    )

    assert is_bot_mentioned(activity)


def test_graph_history_uses_credential_and_returns_oldest_first():
    credential = Mock()
    credential.get_token.return_value.token = "graph-token"
    response = Mock()
    response.json.return_value = {
        "value": [
            {
                "from": {"user": {"displayName": "Second"}},
                "body": {"content": "<p>Newest</p>"},
            },
            {
                "from": {"user": {"displayName": "First"}},
                "body": {"content": "<p>Oldest &amp; useful</p>"},
            },
        ]
    }
    client = Mock()
    client.get.return_value = response

    history = GraphChatHistory(credential, 10, client).read(
        "19:group chat@thread.v2"
    )

    assert history == ["First: Oldest & useful", "Second: Newest"]
    credential.get_token.assert_called_once_with(
        "https://graph.microsoft.com/.default"
    )
    response.raise_for_status.assert_called_once_with()


def test_model_receives_history_as_untrusted_context():
    project = Mock()
    openai = project.get_openai_client.return_value
    openai.responses.create.return_value.output_text = " Answer "
    gateway = FoundryModelGateway.__new__(FoundryModelGateway)
    gateway._openai = openai
    gateway._model_deployment_name = "gpt-4.1-mini"

    assert gateway.ask("Question", ["Participant: Context"]) == "Answer"
    request = openai.responses.create.call_args.kwargs
    assert request["model"] == "gpt-4.1-mini"
    assert "untrusted user content" in request["instructions"]
    assert "<chat_history>\nParticipant: Context\n</chat_history>" in request["input"]

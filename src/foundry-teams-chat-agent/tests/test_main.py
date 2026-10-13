"""Unit tests for logic that does not require Azure or Bot Service."""

import asyncio
import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

MODULE_PATH = Path(__file__).parents[1] / "main.py"


def load_module(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(
        "AZURE_AI_PROJECT_ENDPOINT",
        "https://example.services.ai.azure.com/api/projects/example",
    )
    monkeypatch.setenv("AZURE_AI_MODEL_DEPLOYMENT_NAME", "gpt-4.1-mini")

    host = Mock()
    host.agent_app.activity.side_effect = lambda _name: lambda function: function
    host.agent_app.error = lambda function: function

    activity_module = types.ModuleType("azure.ai.agentserver.activity")
    activity_module.ActivityAgentServerHost = Mock(return_value=host)
    monkeypatch.setitem(sys.modules, "azure.ai.agentserver.activity", activity_module)

    projects_module = types.ModuleType("azure.ai.projects")
    projects_module.AIProjectClient = Mock(
        return_value=Mock(get_openai_client=Mock(return_value=Mock()))
    )
    monkeypatch.setitem(sys.modules, "azure.ai.projects", projects_module)

    identity_module = types.ModuleType("azure.identity")
    credential = Mock()
    identity_module.DefaultAzureCredential = Mock(return_value=credential)
    monkeypatch.setitem(sys.modules, "azure.identity", identity_module)

    credentials_module = types.ModuleType("azure.core.credentials")
    credentials_module.TokenCredential = object
    monkeypatch.setitem(sys.modules, "azure.core.credentials", credentials_module)

    core_module = types.ModuleType("azure.core.exceptions")
    core_module.AzureError = type("AzureError", (Exception,), {})
    monkeypatch.setitem(sys.modules, "azure.core.exceptions", core_module)

    openai_module = types.ModuleType("openai")
    openai_module.OpenAIError = type("OpenAIError", (Exception,), {})
    monkeypatch.setitem(sys.modules, "openai", openai_module)

    httpx_module = types.ModuleType("httpx")
    httpx_module.Client = Mock
    httpx_module.HTTPError = type("HTTPError", (Exception,), {})
    monkeypatch.setitem(sys.modules, "httpx", httpx_module)

    module_name = "foundry_teams_chat_agent_main"
    spec = importlib.util.spec_from_file_location(module_name, MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    assert spec.loader
    spec.loader.exec_module(module)
    return module


def test_normalize_message_removes_teams_mention(monkeypatch):
    module = load_module(monkeypatch)

    assert module.normalize_message("  <at>Foundry Agent</at>   review this design  ") == (
        "review this design"
    )


def test_settings_reads_model_deployment(monkeypatch):
    module = load_module(monkeypatch)

    settings = module.Settings.from_environment()

    assert settings.model_deployment_name == "gpt-4.1-mini"
    assert settings.graph_history_message_count == 10


def test_settings_requires_project_endpoint(monkeypatch):
    module = load_module(monkeypatch)
    monkeypatch.delenv("AZURE_AI_PROJECT_ENDPOINT", raising=False)

    with pytest.raises(RuntimeError, match="AZURE_AI_PROJECT_ENDPOINT"):
        module.Settings.from_environment()


def test_settings_requires_model_deployment(monkeypatch):
    module = load_module(monkeypatch)
    monkeypatch.delenv("AZURE_AI_MODEL_DEPLOYMENT_NAME", raising=False)

    with pytest.raises(RuntimeError, match="AZURE_AI_MODEL_DEPLOYMENT_NAME"):
        module.Settings.from_environment()


def test_settings_rejects_invalid_history_count(monkeypatch):
    module = load_module(monkeypatch)
    monkeypatch.setenv("GRAPH_HISTORY_MESSAGE_COUNT", "51")

    with pytest.raises(RuntimeError, match="between 1 and 50"):
        module.Settings.from_environment()


def test_group_message_requires_bot_mention(monkeypatch):
    module = load_module(monkeypatch)
    activity = types.SimpleNamespace(
        conversation=types.SimpleNamespace(is_group=True),
        recipient=types.SimpleNamespace(id="bot-id"),
        entities=[],
    )

    assert module.is_group_conversation(activity)
    assert not module.is_bot_mentioned(activity)

    activity.entities = [
        {"type": "mention", "mentioned": {"id": "bot-id"}},
    ]
    assert module.is_bot_mentioned(activity)


def test_graph_chat_history_uses_agent_credential_and_chat_scope(monkeypatch):
    module = load_module(monkeypatch)
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
    history = module.GraphChatHistory(credential, 10, client)

    assert history.read("19:group chat@thread.v2") == [
        "First: Oldest & useful",
        "Second: Newest",
    ]
    credential.get_token.assert_called_once_with(
        "https://graph.microsoft.com/.default"
    )
    client.get.assert_called_once_with(
        "https://graph.microsoft.com/v1.0/chats/"
        "19%3Agroup%20chat%40thread.v2/messages",
        headers={"Authorization": "Bearer graph-token"},
        params={"$top": "10", "$orderby": "createdDateTime desc"},
    )
    response.raise_for_status.assert_called_once_with()


@pytest.mark.parametrize("count", ["0", "51", "invalid"])
def test_settings_rejects_history_count_outside_api_contract(monkeypatch, count):
    module = load_module(monkeypatch)
    monkeypatch.setenv("GRAPH_HISTORY_MESSAGE_COUNT", count)

    with pytest.raises(RuntimeError, match="GRAPH_HISTORY_MESSAGE_COUNT"):
        module.Settings.from_environment()


@pytest.mark.parametrize("count", ["1", "50"])
def test_settings_accepts_api_history_count_boundaries(monkeypatch, count):
    module = load_module(monkeypatch)
    monkeypatch.setenv("GRAPH_HISTORY_MESSAGE_COUNT", count)

    assert module.Settings.from_environment().graph_history_message_count == int(count)


@pytest.mark.parametrize("payload", [{}, {"value": None}, {"value": {}}, []])
def test_graph_rejects_invalid_message_list(monkeypatch, payload):
    module = load_module(monkeypatch)
    client = Mock()
    client.get.return_value.json.return_value = payload
    history = module.GraphChatHistory(Mock(), 10, client)

    with pytest.raises(RuntimeError, match="invalid chat message list"):
        history.read("chat-id")


def test_graph_preserves_bot_sender_and_omits_empty_messages(monkeypatch):
    module = load_module(monkeypatch)
    client = Mock()
    client.get.return_value.json.return_value = {
        "value": [
            {"body": {"content": ""}},
            {
                "from": {"application": {"displayName": "Demo bot"}},
                "body": {"content": "<p>First<br>reply</p>"},
            },
        ]
    }

    assert module.GraphChatHistory(Mock(), 10, client).read("chat-id") == [
        "Demo bot: First reply"
    ]


def make_context(conversation_type="groupChat", mentioned=True, chat_id="chat-id"):
    return types.SimpleNamespace(
        activity=types.SimpleNamespace(
            text="<at>Foundry Agent</at> Summarize",
            channel_id="msteams",
            conversation=types.SimpleNamespace(
                id=chat_id,
                is_group=conversation_type != "personal",
                conversation_type=conversation_type,
            ),
            recipient=types.SimpleNamespace(id="bot-id"),
            entities=(
                [{"type": "mention", "mentioned": {"id": "bot-id"}}]
                if mentioned
                else []
            ),
        ),
        send_activity=AsyncMock(),
    )


def test_mentioned_group_chat_reads_current_chat_before_model(monkeypatch):
    module = load_module(monkeypatch)
    context = make_context()
    module.graph_chat_history.read = Mock(return_value=["Participant: Context"])
    module.gateway.ask = Mock(return_value="Answer")

    asyncio.run(module.on_message(context, None))

    module.graph_chat_history.read.assert_called_once_with("chat-id")
    module.gateway.ask.assert_called_once_with("Summarize", ["Participant: Context"])
    context.send_activity.assert_awaited_once_with("Answer")


@pytest.mark.parametrize(
    ("conversation_type", "mentioned"),
    [("groupChat", False), ("channel", True)],
)
def test_unmentioned_group_or_unsupported_channel_is_ignored(
    monkeypatch, conversation_type, mentioned
):
    module = load_module(monkeypatch)
    context = make_context(conversation_type, mentioned)
    module.graph_chat_history.read = Mock()
    module.gateway.ask = Mock()

    asyncio.run(module.on_message(context, None))

    module.graph_chat_history.read.assert_not_called()
    module.gateway.ask.assert_not_called()
    context.send_activity.assert_not_awaited()


def test_personal_message_does_not_request_graph_history(monkeypatch):
    module = load_module(monkeypatch)
    context = make_context("personal", mentioned=False)
    module.graph_chat_history.read = Mock()
    module.gateway.ask = Mock(return_value="Answer")

    asyncio.run(module.on_message(context, None))

    module.graph_chat_history.read.assert_not_called()
    module.gateway.ask.assert_called_once_with("Summarize", [])
    context.send_activity.assert_awaited_once_with("Answer")


@pytest.mark.parametrize("failure", ["authorization", "missing-chat-id", "model"])
def test_request_failures_do_not_return_success(monkeypatch, failure):
    module = load_module(monkeypatch)
    context = make_context(chat_id=None if failure == "missing-chat-id" else "chat-id")
    module.graph_chat_history.read = Mock(return_value=["Context"])
    module.gateway.ask = Mock(return_value="Answer")
    if failure == "authorization":
        module.graph_chat_history.read.side_effect = module.httpx.HTTPError("403")
    elif failure == "model":
        module.gateway.ask.side_effect = module.OpenAIError("Model failed")

    asyncio.run(module.on_message(context, None))

    if failure != "model":
        module.gateway.ask.assert_not_called()
    context.send_activity.assert_awaited_once()
    assert "could not read the chat context or call the model" in (
        context.send_activity.call_args.args[0]
    )


def test_model_receives_history_as_untrusted_context(monkeypatch):
    module = load_module(monkeypatch)
    module.gateway._openai.responses.create.return_value.output_text = " Answer "

    assert module.gateway.ask("Question", ["Participant: Context"]) == "Answer"
    request = module.gateway._openai.responses.create.call_args.kwargs
    assert request["model"] == "gpt-4.1-mini"
    assert "untrusted user content" in request["instructions"]
    assert "<chat_history>\nParticipant: Context\n</chat_history>" in request["input"]
    assert request["input"].endswith("Current request:\nQuestion")


@pytest.mark.parametrize("output", [None, "", "  "])
def test_empty_model_output_is_an_error(monkeypatch, output):
    module = load_module(monkeypatch)
    module.gateway._openai.responses.create.return_value.output_text = output

    with pytest.raises(RuntimeError, match="no text response"):
        module.gateway.ask("Question", [])

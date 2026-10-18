import asyncio
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest
from microsoft_agents.activity import Activity, ChannelAccount, ConversationAccount

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "src"))
from test_handoff import HUMAN, TENANT, USER


@pytest.fixture
def agent_module(monkeypatch):
    settings = {
        "AZURE_AI_PROJECT_ENDPOINT": "https://example.test/api/projects/project",
        "AZURE_AI_MODEL_DEPLOYMENT_NAME": "model",
        "HANDOFF_TENANT_ID": TENANT,
        "HANDOFF_HUMAN_UPN": "helpdesk@example.test",
        "HANDOFF_HUMAN_OBJECT_ID": HUMAN,
        "HANDOFF_TEAMS_CATALOG_APP_ID": "44444444-4444-4444-8444-444444444444",
        "HANDOFF_BROKER_ENABLED": "false",
        "BOT_ID": "55555555-5555-4555-8555-555555555555",
    }
    for name, value in settings.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("GRAPH_AUTH_MODE", raising=False)
    application = MagicMock()
    app = application.__getitem__.return_value.return_value
    app.activity.side_effect = lambda *a, **kw: lambda handler: handler
    app.conversation_update.side_effect = lambda *a, **kw: lambda handler: handler
    app.error.side_effect = lambda handler: handler
    gateway = Mock()
    gateway.ask.return_value = "Try reconnecting."
    gateway.summarize_handoff.return_value = "VPN fails."
    for name, value in {
        "dotenv.load_dotenv": Mock(),
        "azure.identity.DefaultAzureCredential": Mock(),
        "foundry.FoundryModelGateway": Mock(return_value=gateway),
        "foundry.GraphChatHistory": Mock(),
        "microsoft_agents.activity.load_configuration_from_env": Mock(return_value={}),
        "microsoft_agents.authentication.msal.MsalConnectionManager": Mock(),
        "microsoft_agents.hosting.aiohttp.CloudAdapter": Mock(),
        "microsoft_agents.hosting.core.AgentApplication": application,
    }.items():
        monkeypatch.setattr(name, value)
    spec = importlib.util.spec_from_file_location("handoff_agent_test", ROOT / "src/agent.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def context(text="", *, value=None, user=USER, tenant=TENANT, chat="personal", kind="personal"):
    activity = Activity(
        type="message", channel_id="msteams", service_url="https://example.test",
        text=text, value=value,
        from_property=ChannelAccount(id="teams-user", aad_object_id=user, name="Demo User"),
        recipient=ChannelAccount(id="bot"),
        conversation=ConversationAccount(id=chat, conversation_type=kind, tenant_id=tenant),
        channel_data={"tenant": {"id": tenant}},
    )
    return SimpleNamespace(activity=activity, send_activity=AsyncMock())


def test_link_command_requires_confirmation_before_link(agent_module):
    async def run():
        module = agent_module
        await module.on_message(context("VPN fails"), None)
        turn = context("handoff link")
        await module.on_message(turn, None)
        card = turn.send_activity.call_args.args[0].attachments[0].content
        assert card["actions"][0]["type"] == "Action.Submit"
        assert "VPN fails" in module.handoff_store.transcript("personal")
        submit = context(value=card["actions"][0]["data"] | {"summary": "tampered", "human": "attacker"})
        await module.on_message(submit, None)
        result = submit.send_activity.call_args.args[0]
        assert "must send" in result.text.lower()
        link = result.attachments[0].content["actions"][0]["url"]
        assert "helpdesk%40example.test" in link
        assert "tampered" not in link
    asyncio.run(run())


@pytest.mark.parametrize("user,tenant", [(HUMAN, TENANT), (USER, HUMAN), (None, TENANT)])
def test_invalid_identity_cannot_prepare_handoff(agent_module, user, tenant):
    async def run():
        turn = context("handoff link", user=user, tenant=tenant)
        await agent_module.on_message(turn, None)
        assert not agent_module.handoff_store.requests
        assert "handoff" in turn.send_activity.call_args.args[0].lower()
    asyncio.run(run())


def test_group_handoff_does_not_disclose_history(agent_module):
    async def run():
        turn = context("handoff link", kind="groupChat")
        turn.activity.entities = [{"type": "mention", "mentioned": {"id": "bot"}}]
        await agent_module.on_message(turn, None)
        assert "personal chat" in turn.send_activity.call_args.args[0].lower()
        assert not agent_module.handoff_store.requests
    asyncio.run(run())


def test_disabled_broker_is_explicit_and_ordinary_chat_still_works(agent_module):
    async def run():
        turn = context("handoff broker")
        await agent_module.on_message(turn, None)
        assert "HANDOFF_BROKER_ENABLED" in turn.send_activity.call_args.args[0]
        turn = context("Hello")
        await agent_module.on_message(turn, None)
        assert turn.send_activity.call_args.args[0] == "Try reconnecting."
    asyncio.run(run())

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


def test_support_groups_never_invoke_model_or_read_history(agent_module):
    async def run():
        from handoff import HandoffMode
        request = agent_module.handoff_store.create_request(
            HandoffMode.BROKER, TENANT, USER, "personal", "VPN fails.",
        )
        request.chat_id = "19:support@thread.v2"
        request.state = "delivered"
        turn = context("Summarize", chat=request.chat_id, kind="groupChat")
        turn.activity.entities = [{"type": "mention", "mentioned": {"id": "bot"}}]
        agent_module.gateway.ask.side_effect = AssertionError("Support group invoked model")
        agent_module.graph_chat_history.read.side_effect = AssertionError("Support group read history")
        await agent_module.on_message(turn, None)
        turn.send_activity.assert_not_awaited()
    asyncio.run(run())


def test_proactive_send_uses_sdk_continuation_activity(agent_module):
    async def run():
        from handoff import HandoffMode
        module = agent_module
        request = module.handoff_store.create_request(
            HandoffMode.BROKER, TENANT, USER, "personal", "VPN fails.",
        )
        origin = context().activity.get_conversation_reference()
        module.handoff_origins[request.request_id] = origin
        sent = []
        async def continuation(bot_id, activity, callback):
            assert bot_id == "55555555-5555-4555-8555-555555555555"
            assert activity.type == "event"
            assert activity.conversation.id == "personal"
            await callback(SimpleNamespace(send_activity=AsyncMock(side_effect=sent.append)))
        module.adapter.continue_conversation = continuation
        await module.send_handoff_origin(request, "Pending")
        assert sent == ["Pending"]
    asyncio.run(run())


def test_broker_command_and_installation_event_deliver_context(agent_module):
    async def run():
        from dataclasses import replace
        from handoff import HandoffBroker
        from test_handoff import FakeGraph
        module = agent_module
        module.config.handoff = replace(module.config.handoff, broker_enabled="true")
        graph = FakeGraph()
        module.handoff_broker = HandoffBroker(module.handoff_store, graph, module.send_handoff_origin)
        notifications = []
        async def continuation(bot_id, activity, callback):
            await callback(SimpleNamespace(send_activity=AsyncMock(side_effect=notifications.append)))
        module.adapter.continue_conversation = continuation
        support = context(chat="19:support@thread.v2", kind="groupChat")
        support.activity.type = "installationUpdate"
        support.activity.action = "add"
        graph.on_install = lambda: module.on_installation_update(support, None)
        await module.on_message(context("VPN login fails"), None)
        prepare = context("handoff broker")
        await module.on_message(prepare, None)
        card = prepare.send_activity.call_args.args[0].attachments[0].content
        confirm = context(value=card["actions"][0]["data"])
        await module.on_message(confirm, None)
        await asyncio.gather(*list(module.handoff_tasks))
        assert graph.create_calls == 1
        sent = support.send_activity.call_args.args[0]
        assert sent.text_format == "plain"
        assert "VPN fails." in sent.text
        assert "Demo User" in sent.text
        assert notifications[-1].attachments[0].content["actions"][0]["title"] == "Open support chat"
        assert "delivered" in notifications[-1].text
        assert next(iter(module.handoff_store.requests.values())).state == "delivered"
        await module.on_installation_update(support, None)
        assert support.send_activity.await_count == 1
    asyncio.run(run())


def test_ordinary_group_mention_keeps_existing_model_behavior(agent_module):
    async def run():
        module = agent_module
        module.graph_chat_history.read.return_value = ["Alice: VPN is down"]
        turn = context("Help", chat="ordinary", kind="groupChat")
        turn.activity.entities = [{"type": "mention", "mentioned": {"id": "bot"}}]
        await module.on_message(turn, None)
        assert turn.send_activity.call_args.args[0] == "Try reconnecting."
        module.gateway.ask.assert_called_once_with("Help", ["Alice: VPN is down"])
        assert not module.handoff_store.histories
    asyncio.run(run())


def test_sdk_response_validation_failure_records_uncertain_delivery(agent_module):
    async def run():
        from handoff import HandoffBroker
        from microsoft_agents.activity import ResourceResponse
        from test_handoff import FakeGraph, make_request
        from handoff import HandoffMode
        module = agent_module
        notifications = []

        async def notify(request, message):
            notifications.append(message)
        broker = HandoffBroker(module.handoff_store, FakeGraph(), notify)
        module.handoff_broker = broker
        request = make_request(module.handoff_store, HandoffMode.BROKER)
        module.handoff_store.confirm(request.request_id, TENANT, USER, "personal-chat")
        await broker.start(request, HUMAN)
        turn = context(chat=request.chat_id, kind="groupChat")

        async def invalid_sdk_response(_activity):
            return ResourceResponse.model_validate({"id": {"unexpected": "object"}})
        turn.send_activity.side_effect = invalid_sdk_response
        await module.on_members_added(turn, None)
        assert request.state == "uncertain"
        assert request.failure_stage == "send"
        assert "uncertain" in notifications[-1]
        await module.on_members_added(turn, None)
        assert turn.send_activity.await_count == 1
    asyncio.run(run())

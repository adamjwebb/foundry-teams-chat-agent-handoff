import asyncio
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from config import HandoffConfig, load_handoff_environment
from handoff import (
    HandoffBroker,
    HandoffError,
    HandoffMode,
    HandoffStore,
    build_confirmation_card,
    build_human_chat_link,
)
from handoff_graph import HandoffOperationError

TENANT = "11111111-1111-4111-8111-111111111111"
USER = "22222222-2222-4222-8222-222222222222"
HUMAN = "33333333-3333-4333-8333-333333333333"


def make_request(store, mode=HandoffMode.LINK):
    return store.create_request(mode, TENANT, USER, "personal-chat", "VPN fails.")


def test_history_is_bounded_and_expires():
    now = [100.0]
    store = HandoffStore(clock=lambda: now[0])
    for number in range(25):
        store.add_message("chat", "user", f"message-{number:02}")
    transcript = store.transcript("chat")
    assert "message-04" not in transcript
    assert "message-05" in transcript
    assert "message-24" in transcript
    store.add_message("chat", "assistant", "x" * 9000)
    assert len(store.transcript("chat")) <= 8000
    now[0] += 86400
    assert store.transcript("chat") == ""


@pytest.mark.parametrize(
    "tenant,user,chat",
    [("other", USER, "personal-chat"), (TENANT, HUMAN, "personal-chat"),
     (TENANT, USER, "another-chat")],
)
def test_confirmation_rejects_other_identity_or_conversation(tenant, user, chat):
    store = HandoffStore()
    request = make_request(store)
    with pytest.raises(HandoffError):
        store.confirm(request.request_id, tenant, user, chat)
    assert request.state == "pending"


def test_confirm_is_single_use_and_restart_invalidates_cards():
    store = HandoffStore()
    request = make_request(store)
    assert store.confirm(request.request_id, TENANT, USER, "personal-chat") is request
    with pytest.raises(HandoffError):
        store.confirm(request.request_id, TENANT, USER, "personal-chat")
    with pytest.raises(HandoffError):
        HandoffStore().confirm(request.request_id, TENANT, USER, "personal-chat")


def test_expiry_and_cancel_do_not_confirm():
    now = [0.0]
    store = HandoffStore(clock=lambda: now[0])
    request = make_request(store)
    now[0] = 86400
    with pytest.raises(HandoffError):
        store.confirm(request.request_id, TENANT, USER, "personal-chat")
    request = make_request(store)
    store.cancel(request.request_id, TENANT, USER, "personal-chat")
    assert request.state == "cancelled"


def test_concurrent_confirmation_has_one_winner():
    async def run():
        store = HandoffStore()
        request = make_request(store)

        async def confirm():
            try:
                store.confirm(request.request_id, TENANT, USER, "personal-chat")
                return True
            except HandoffError:
                return False

        assert await asyncio.gather(confirm(), confirm()) == [True, False]
    asyncio.run(run())


def test_deep_link_encodes_target_and_short_draft():
    link = build_human_chat_link(TENANT, "helpdesk@example.test", "VPN: café & <error>? #")
    query = parse_qs(urlparse(link).query)
    assert urlparse(link).netloc == "teams.microsoft.com"
    assert query["tenantId"] == [TENANT]
    assert query["users"] == ["helpdesk@example.test"]
    assert query["message"] == ["VPN: café & <error>? #"]
    long_query = parse_qs(urlparse(
        build_human_chat_link(TENANT, "helpdesk@example.test", "x" * 5000)
    ).query)
    assert len(long_query["message"][0]) <= 800


def test_card_uses_only_server_request_id_and_shows_recipient_and_summary():
    request = make_request(HandoffStore())
    card = build_confirmation_card(request, "helpdesk@example.test")
    assert any("helpdesk@example.test" in block.get("text", "") for block in card["body"])
    assert any("VPN fails." in block.get("text", "") for block in card["body"])
    assert card["actions"][0]["data"] == {
        "handoff_action": "confirm", "request_id": request.request_id,
    }
    assert "cancel" == card["actions"][1]["data"]["handoff_action"]


def test_link_config_does_not_require_broker_settings():
    config = HandoffConfig.from_env({
        "HANDOFF_HUMAN_UPN": "helpdesk@example.test", "HANDOFF_TENANT_ID": TENANT,
    })
    config.validate("link")
    with pytest.raises(ValueError, match="HANDOFF_BROKER_ENABLED"):
        config.validate("broker")


@pytest.mark.parametrize("value", ["", "not-an-email", "a@example.test,b@example.test", "a@example.test?x=y"])
def test_invalid_target_fails_explicitly(value):
    config = HandoffConfig.from_env({
        "HANDOFF_HUMAN_UPN": value, "HANDOFF_TENANT_ID": TENANT,
    })
    with pytest.raises(ValueError, match="HANDOFF_HUMAN_UPN"):
        config.validate("link")


def test_optional_local_settings_load_without_overriding_process_env(tmp_path, monkeypatch):
    local = tmp_path / ".env.handoff"
    local.write_text(
        "HANDOFF_HUMAN_UPN=helpdesk@example.test\nHANDOFF_TENANT_ID=local-value\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("HANDOFF_HUMAN_UPN", raising=False)
    monkeypatch.setenv("HANDOFF_TENANT_ID", TENANT)
    load_handoff_environment(local)
    import os
    config = HandoffConfig.from_env(os.environ)
    assert config.human_upn == "helpdesk@example.test"
    assert config.tenant_id == TENANT
    monkeypatch.delenv("HANDOFF_HUMAN_UPN", raising=False)


class FakeGraph:
    def __init__(self):
        self.create_calls = 0
        self.install_calls = []
        self.on_install = None
        self.error_stage = None

    async def create_support_chat(self, requester_id, human_id, topic):
        self.create_calls += 1
        assert (requester_id, human_id) == (USER, HUMAN)
        if self.error_stage == "create":
            raise HandoffOperationError("create", "Unknown create outcome", uncertain=True)
        return "19:support@thread.v2"

    async def install_support_app(self, chat_id):
        self.install_calls.append(chat_id)
        if self.on_install:
            await self.on_install()
        if self.error_stage == "install":
            raise HandoffOperationError("install", "Installation denied")


def broker_fixture():
    store = HandoffStore()
    graph = FakeGraph()
    notifications = []

    async def notify(request, message):
        notifications.append(message)
    broker = HandoffBroker(store, graph, notify)
    request = make_request(store, HandoffMode.BROKER)
    store.confirm(request.request_id, TENANT, USER, "personal-chat")
    return store, graph, broker, request, notifications


@pytest.mark.parametrize("early", [True, False])
def test_broker_posts_once_despite_install_event_race(early):
    async def run():
        store, graph, broker, request, notifications = broker_fixture()
        sent = []

        async def send(text):
            sent.append(text)

        async def event():
            assert await broker.on_group_available("19:support@thread.v2", send)
        if early:
            graph.on_install = event
        await broker.start(request, HUMAN)
        await event()
        await event()
        assert request.state == "delivered"
        assert len(sent) == 1
        assert "VPN fails." in sent[0]
        assert request.request_id in sent[0]
        assert USER in sent[0]
        assert store.is_support_chat("19:support@thread.v2")
        assert graph.create_calls == 1
        assert graph.install_calls == ["19:support@thread.v2"]
        assert sum("delivered" in item.lower() for item in notifications) == 1
    asyncio.run(run())


def test_unknown_groups_and_duplicate_start_have_no_effect():
    async def run():
        store, graph, broker, request, notifications = broker_fixture()
        async def no_send(text):
            pytest.fail("Unknown group received context")
        assert not await broker.on_group_available("unrelated", no_send)
        await broker.start(request, HUMAN)
        await broker.start(request, HUMAN)
        assert graph.create_calls == 1
        assert request.state == "awaiting_delivery"
        assert not any("delivered" in message.lower() for message in notifications)
    asyncio.run(run())


@pytest.mark.parametrize("stage,state", [("create", "uncertain"), ("install", "failed")])
def test_broker_partial_failure_is_not_success_or_auto_retried(stage, state):
    async def run():
        _, graph, broker, request, notifications = broker_fixture()
        graph.error_stage = stage
        await broker.start(request, HUMAN)
        await broker.start(request, HUMAN)
        assert request.state == state
        assert request.failure_stage == stage
        assert graph.create_calls == 1
        assert request.chat_id == (None if stage == "create" else "19:support@thread.v2")
        assert not any("delivered" in message.lower() for message in notifications)
        assert notifications
    asyncio.run(run())


def test_uncertain_bot_send_is_not_retried():
    async def run():
        _, _, broker, request, notifications = broker_fixture()
        await broker.start(request, HUMAN)
        sends = []
        async def send(text):
            sends.append(text)
            raise HandoffOperationError("send", "Unknown send outcome", uncertain=True)
        await broker.on_group_available(request.chat_id, send)
        await broker.on_group_available(request.chat_id, send)
        assert request.state == "uncertain"
        assert len(sends) == 1
        assert request.failure_stage == "send"
    asyncio.run(run())


def test_cancellation_marks_inflight_create_uncertain():
    async def run():
        _, graph, broker, request, _ = broker_fixture()
        entered = asyncio.Event()
        async def create(*args):
            entered.set()
            await asyncio.Event().wait()
        graph.create_support_chat = create
        task = asyncio.create_task(broker.start(request, HUMAN))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert request.state == "uncertain"
    asyncio.run(run())


def test_completion_notification_cannot_be_overtaken_by_stale_pending_message():
    async def run():
        store = HandoffStore()
        graph = FakeGraph()
        pending_entered = asyncio.Event()
        release_pending = asyncio.Event()
        notifications = []

        async def notify(request, message):
            if "Waiting for Teams" in message:
                pending_entered.set()
                await release_pending.wait()
            notifications.append(message)

        broker = HandoffBroker(store, graph, notify)
        request = make_request(store, HandoffMode.BROKER)
        store.confirm(request.request_id, TENANT, USER, "personal-chat")
        starting = asyncio.create_task(broker.start(request, HUMAN))
        await pending_entered.wait()

        async def send(text):
            pass
        delivering = asyncio.create_task(broker.on_group_available(request.chat_id, send))
        await asyncio.sleep(0)
        release_pending.set()
        await asyncio.gather(starting, delivering)
        assert request.state == "delivered"
        assert "delivered" in notifications[-1]
    asyncio.run(run())

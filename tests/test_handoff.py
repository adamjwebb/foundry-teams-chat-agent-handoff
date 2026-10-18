import asyncio
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from config import HandoffConfig
from handoff import (
    HandoffError,
    HandoffMode,
    HandoffStore,
    build_confirmation_card,
    build_human_chat_link,
)

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

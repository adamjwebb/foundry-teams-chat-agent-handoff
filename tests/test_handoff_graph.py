import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))
from handoff_graph import GraphHandoffClient, HandoffOperationError
from test_handoff import HUMAN, USER

APP = "44444444-4444-4444-8444-444444444444"


class Credential:
    def get_token(self, scope):
        assert scope == "https://graph.microsoft.com/.default"
        return SimpleNamespace(token="synthetic-token")


def test_broker_creates_two_member_group_and_installs_matching_rsc_app():
    async def run():
        requests = []

        def handler(request):
            import json
            requests.append((request.url.path, json.loads(request.content)))
            return httpx.Response(201, json={"id": "19:support@thread.v2"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            graph = GraphHandoffClient(Credential(), APP, client)
            chat = await graph.create_support_chat(USER, HUMAN, "Helpdesk handoff abc")
            assert chat == "19:support@thread.v2"
            await graph.install_support_app(chat)
        assert requests[0][0] == "/v1.0/chats"
        body = requests[0][1]
        assert body["chatType"] == "group"
        assert body["topic"] == "Helpdesk handoff abc"
        assert [member["user@odata.bind"] for member in body["members"]] == [
            f"https://graph.microsoft.com/v1.0/users('{USER}')",
            f"https://graph.microsoft.com/v1.0/users('{HUMAN}')",
        ]
        assert requests[1][0] == "/v1.0/chats/19:support@thread.v2/installedApps"
        assert requests[1][1] == {
            "teamsApp@odata.bind": f"https://graph.microsoft.com/v1.0/appCatalogs/teamsApps/{APP}",
            "consentedPermissionSet": {"resourceSpecificPermissions": [
                {"permissionValue": "ChatMessage.Read.Chat", "permissionType": "application"},
            ]},
        }
    asyncio.run(run())


@pytest.mark.parametrize("status,uncertain", [(401, False), (403, False), (429, False), (500, True)])
def test_rejections_are_explicit_and_never_retried(status, uncertain):
    async def run():
        calls = []

        def handler(request):
            calls.append(request)
            return httpx.Response(status, json={"error": {"message": "sensitive error text"}})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            graph = GraphHandoffClient(Credential(), APP, client)
            with pytest.raises(HandoffOperationError) as captured:
                await graph.create_support_chat(USER, HUMAN, "Support")
            assert captured.value.uncertain is uncertain
            assert captured.value.stage == "create"
            assert "sensitive" not in str(captured.value)
            assert len(calls) == 1
    asyncio.run(run())


@pytest.mark.parametrize("failure", ["timeout", "invalid_json", "missing_id"])
def test_unknown_create_outcome_is_not_retried(failure):
    async def run():
        calls = []

        def handler(request):
            calls.append(request)
            if failure == "timeout":
                raise httpx.ReadTimeout("unknown outcome", request=request)
            if failure == "invalid_json":
                return httpx.Response(201, text="not json")
            return httpx.Response(201, json={})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            graph = GraphHandoffClient(Credential(), APP, client)
            with pytest.raises(HandoffOperationError) as captured:
                await graph.create_support_chat(USER, HUMAN, "Support")
            assert captured.value.uncertain
            assert len(calls) == 1
    asyncio.run(run())


@pytest.mark.parametrize("requester,human", [(USER, USER), ("bad", HUMAN), (USER, "bad")])
def test_invalid_or_self_handoff_never_calls_graph(requester, human):
    async def run():
        def handler(request):
            pytest.fail("Invalid identity reached Graph")
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(ValueError):
                await GraphHandoffClient(Credential(), APP, client).create_support_chat(
                    requester, human, "Support",
                )
    asyncio.run(run())

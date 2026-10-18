import asyncio
from urllib.parse import quote

import httpx
from azure.core.credentials import TokenCredential
from azure.core.exceptions import AzureError

from config import HandoffConfig


class HandoffOperationError(RuntimeError):
    def __init__(self, stage: str, message: str, *, uncertain: bool = False):
        super().__init__(message)
        self.stage = stage
        self.uncertain = uncertain


class GraphHandoffClient:
    def __init__(self, credential: TokenCredential, catalog_app_id: str, client: httpx.AsyncClient):
        HandoffConfig._guid(catalog_app_id, "HANDOFF_TEAMS_CATALOG_APP_ID")
        self.credential = credential
        self.catalog_app_id = catalog_app_id
        self.client = client

    async def _post(self, stage: str, path: str, body: dict) -> httpx.Response:
        try:
            token = await asyncio.to_thread(
                self.credential.get_token, "https://graph.microsoft.com/.default",
            )
        except AzureError as error:
            raise HandoffOperationError(stage, "Graph authentication failed; check the bot identity.") from error
        try:
            response = await self.client.post(
                f"https://graph.microsoft.com/v1.0{path}",
                headers={"Authorization": f"Bearer {token.token}"},
                json=body, timeout=15,
            )
        except httpx.RequestError as error:
            raise HandoffOperationError(
                stage, "Graph request outcome is unknown. Do not automatically retry this handoff.",
                uncertain=True,
            ) from error
        if response.status_code not in (200, 201, 204):
            raise HandoffOperationError(
                stage, f"Graph {stage} returned HTTP {response.status_code}. Check permissions, tenant policy, and app publication.",
                uncertain=response.status_code >= 500,
            )
        return response

    async def create_support_chat(self, requester_id: str, human_id: str, topic: str) -> str:
        HandoffConfig._guid(requester_id, "requesting user's Entra object ID")
        HandoffConfig._guid(human_id, "HANDOFF_HUMAN_OBJECT_ID")
        if requester_id.lower() == human_id.lower():
            raise ValueError("Handoff cannot route a user to themselves.")
        response = await self._post("create", "/chats", {
            "chatType": "group", "topic": topic,
            "members": [
                {"@odata.type": "#microsoft.graph.aadUserConversationMember",
                 "roles": ["owner"],
                 "user@odata.bind": f"https://graph.microsoft.com/v1.0/users('{user_id}')"}
                for user_id in (requester_id, human_id)
            ],
        })
        try:
            payload = response.json()
            chat_id = payload.get("id") if isinstance(payload, dict) else None
            if not isinstance(chat_id, str) or not chat_id.startswith("19:"):
                raise ValueError("Invalid group chat response")
            return chat_id
        except ValueError as error:
            raise HandoffOperationError(
                "create", "Graph may have created the group, but returned no usable chat ID. Inspect Teams before starting another handoff.",
                uncertain=True,
            ) from error

    async def install_support_app(self, chat_id: str) -> None:
        await self._post("install", f"/chats/{quote(chat_id, safe='')}/installedApps", {
            "teamsApp@odata.bind": (
                f"https://graph.microsoft.com/v1.0/appCatalogs/teamsApps/{self.catalog_app_id}"
            ),
            "consentedPermissionSet": {"resourceSpecificPermissions": [
                {"permissionValue": "ChatMessage.Read.Chat", "permissionType": "application"},
            ]},
        })

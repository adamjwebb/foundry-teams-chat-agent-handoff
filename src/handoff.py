import re
import time
from dataclasses import dataclass
from enum import Enum
from threading import RLock
from typing import Callable
from urllib.parse import urlencode
from uuid import uuid4


class HandoffError(ValueError):
    pass


class HandoffMode(str, Enum):
    LINK = "link"
    BROKER = "broker"


@dataclass
class HandoffRequest:
    request_id: str
    mode: HandoffMode
    tenant_id: str
    requester_id: str
    conversation_id: str
    summary: str
    created_at: float
    state: str = "pending"
    chat_id: str | None = None
    failure_stage: str | None = None


class HandoffStore:
    def __init__(self, clock: Callable[[], float] = time.time):
        self.clock = clock
        self.lock = RLock()
        self.requests: dict[str, HandoffRequest] = {}
        self.histories: dict[str, tuple[float, list[str]]] = {}

    def _expire(self) -> None:
        cutoff = self.clock() - 86400
        self.histories = {
            key: value for key, value in self.histories.items() if value[0] > cutoff
        }
        self.requests = {
            key: value for key, value in self.requests.items()
            if value.state != "pending" or value.created_at > cutoff
        }

    def add_message(self, conversation_id: str, role: str, text: str) -> None:
        with self.lock:
            self._expire()
            messages = self.histories.get(conversation_id, (0, []))[1]
            messages.append(f"{role}: {text}"[-8000:])
            self.histories[conversation_id] = (self.clock(), messages[-20:])

    def transcript(self, conversation_id: str) -> str:
        with self.lock:
            self._expire()
            return "\n".join(self.histories.get(conversation_id, (0, []))[1])[-8000:]

    def create_request(
        self, mode: HandoffMode, tenant_id: str, requester_id: str,
        conversation_id: str, summary: str,
    ) -> HandoffRequest:
        with self.lock:
            self._expire()
            request = HandoffRequest(
                uuid4().hex, mode, tenant_id, requester_id, conversation_id,
                summary[:800], self.clock(),
            )
            self.requests[request.request_id] = request
            return request

    def _pending(
        self, request_id: str, tenant_id: str, requester_id: str, conversation_id: str,
    ) -> HandoffRequest:
        self._expire()
        request = self.requests.get(request_id)
        if request is None or request.state != "pending":
            raise HandoffError("This handoff expired, was already used, or was lost on restart. Start a new handoff.")
        if (request.tenant_id, request.requester_id, request.conversation_id) != (
            tenant_id, requester_id, conversation_id,
        ):
            raise HandoffError("Only the requesting user in the original chat can confirm this handoff.")
        return request

    def confirm(
        self, request_id: str, tenant_id: str, requester_id: str, conversation_id: str,
    ) -> HandoffRequest:
        with self.lock:
            request = self._pending(request_id, tenant_id, requester_id, conversation_id)
            request.state = "confirmed"
            return request

    def cancel(
        self, request_id: str, tenant_id: str, requester_id: str, conversation_id: str,
    ) -> None:
        with self.lock:
            self._pending(request_id, tenant_id, requester_id, conversation_id).state = "cancelled"

    def is_support_chat(self, chat_id: str) -> bool:
        with self.lock:
            return any(request.chat_id == chat_id for request in self.requests.values())


def plain_markdown(text: str) -> str:
    return re.sub(r"([\\`*_\[\]()<>#|~])", r"\\\1", text)


def build_human_chat_link(tenant_id: str, human_upn: str, summary: str) -> str:
    return "https://teams.microsoft.com/l/chat/0/0?" + urlencode({
        "tenantId": tenant_id, "users": human_upn, "message": summary[:800],
    })


def build_confirmation_card(request: HandoffRequest, human_upn: str) -> dict[str, object]:
    warning = (
        "Teams will open a draft. You must send it yourself. The draft summary is included in the link URL."
        if request.mode == HandoffMode.LINK else
        "Confirming creates a support group and shares this summary with the human and installed bot."
    )
    return {
        "type": "AdaptiveCard", "version": "1.4",
        "body": [
            {"type": "TextBlock", "text": f"Handoff to {plain_markdown(human_upn)}", "wrap": True},
            {"type": "TextBlock", "text": plain_markdown(request.summary), "wrap": True},
            {"type": "TextBlock", "text": warning + " Do not share passwords, tokens, or sensitive details.", "wrap": True},
        ],
        "actions": [
            {"type": "Action.Submit", "title": "Confirm handoff",
             "data": {"handoff_action": "confirm", "request_id": request.request_id}},
            {"type": "Action.Submit", "title": "Cancel",
             "data": {"handoff_action": "cancel", "request_id": request.request_id}},
        ],
    }

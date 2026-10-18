import asyncio
import logging
import re
import time
from dataclasses import dataclass
from enum import Enum
from threading import RLock
from typing import Awaitable, Callable
from urllib.parse import quote, urlencode
from uuid import uuid4

from handoff_graph import GraphHandoffClient, HandoffOperationError

logger = logging.getLogger(__name__)


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
    requester_name: str = "Teams user"


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


def support_chat_link(chat_id: str) -> str:
    return f"https://teams.microsoft.com/l/chat/{quote(chat_id, safe='')}/conversations"


class HandoffBroker:
    def __init__(
        self, store: HandoffStore, graph: GraphHandoffClient,
        send_origin: Callable[[HandoffRequest, str], Awaitable[None]],
    ):
        self.store = store
        self.graph = graph
        self.send_origin = send_origin
        self._notification_locks: dict[str, asyncio.Lock] = {}

    async def _notify(self, request: HandoffRequest, message: str, *, pending: bool = False) -> None:
        lock = self._notification_locks.setdefault(request.request_id, asyncio.Lock())
        try:
            async with lock:
                if pending and request.state != "awaiting_delivery":
                    return
                await self.send_origin(request, message)
        except HandoffOperationError:
            logger.error("Handoff %s origin notification failed; state=%s", request.request_id, request.state)

    async def _failed(self, request: HandoffRequest, error: HandoffOperationError) -> None:
        with self.store.lock:
            if request.state == "delivered":
                logger.warning("Handoff %s: %s failed after confirmed delivery", request.request_id, error.stage)
                return
            request.state = "uncertain" if error.uncertain else "failed"
            request.failure_stage = error.stage
        logger.error("Handoff %s stage=%s state=%s", request.request_id, error.stage, request.state)
        await self._notify(
            request,
            f"Handoff {request.request_id}: {request.state} at {error.stage}. {error} "
            + ("A support group exists; check it before starting another handoff." if request.chat_id else
               "Check Teams before starting another handoff if the outcome is unknown."),
        )

    async def start(self, request: HandoffRequest, human_id: str) -> None:
        with self.store.lock:
            if request.state != "confirmed" or request.mode != HandoffMode.BROKER:
                logger.info("Skipping duplicate or invalid broker start %s", request.request_id)
                return
            request.state = "creating"
        try:
            chat_id = await self.graph.create_support_chat(
                request.requester_id, human_id, f"Helpdesk handoff {request.request_id[:8]}",
            )
            with self.store.lock:
                request.chat_id = chat_id
                request.state = "installing"
            await self.graph.install_support_app(chat_id)
            with self.store.lock:
                pending = request.state == "installing"
                if pending:
                    request.state = "awaiting_delivery"
            if pending:
                await self._notify(
                    request,
                    f"Handoff {request.request_id}: support group created. Waiting for Teams "
                    "to provide the installed bot conversation; summary not sent yet. "
                    "If this does not complete, check app installation and bot messaging logs.",
                    pending=True,
                )
        except HandoffOperationError as error:
            await self._failed(request, error)
        except asyncio.CancelledError:
            with self.store.lock:
                if request.state != "delivered":
                    request.failure_stage = request.state
                    request.state = "uncertain"
            logger.warning("Handoff %s interrupted; outcome uncertain", request.request_id)
            raise

    async def on_group_available(
        self, chat_id: str, send_summary: Callable[[str], Awaitable[None]],
    ) -> bool:
        with self.store.lock:
            request = next(
                (item for item in self.store.requests.values() if item.chat_id == chat_id), None,
            )
            if request is None:
                return False
            if request.state not in ("installing", "awaiting_delivery"):
                return True
            request.state = "delivering"
        try:
            await send_summary(
                f"Helpdesk handoff {request.request_id}\n"
                f"Requesting user: {request.requester_name} (Entra ID: {request.requester_id})\n\n"
                f"{request.summary}\n\n"
                "The human now handles this conversation. The bot will not answer in this support group."
            )
        except HandoffOperationError as error:
            await self._failed(request, error)
            return True
        except asyncio.CancelledError:
            with self.store.lock:
                request.failure_stage = "send"
                request.state = "uncertain"
            logger.warning("Handoff %s summary send interrupted; outcome uncertain", request.request_id)
            raise
        with self.store.lock:
            request.state = "delivered"
        await self._notify(request, f"Handoff {request.request_id}: summary delivered to the support group.")
        return True

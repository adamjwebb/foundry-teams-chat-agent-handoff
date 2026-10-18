import asyncio
import logging
import os

import httpx
from azure.core.exceptions import AzureError
from azure.identity import ClientSecretCredential, DefaultAzureCredential
from dotenv import load_dotenv
from microsoft_agents.activity import Activity, ActivityTypes, Attachment, load_configuration_from_env
from microsoft_agents.authentication.msal import MsalConnectionManager
from microsoft_agents.hosting.aiohttp import CloudAdapter
from microsoft_agents.hosting.core import (
    AgentApplication,
    MemoryStorage,
    TurnContext,
    TurnState,
)
from openai import OpenAIError

from config import Config
from foundry import (
    FoundryModelGateway,
    GraphChatHistory,
    is_bot_mentioned,
    is_group_conversation,
    normalize_message,
)
from handoff import (
    HandoffError, HandoffMode, HandoffStore, build_confirmation_card,
    build_human_chat_link,
)

load_dotenv()
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
)
logger = logging.getLogger("demo-foundry-agent")

config = Config(os.environ)
agents_sdk_config = load_configuration_from_env(os.environ)

# DefaultAzureCredential provides the application identity used for outbound Azure
# calls. In App Service, AZURE_CLIENT_ID selects the provisioned user-assigned
# managed identity. During local development, the chain can instead use the
# developer's Azure CLI or VS Code credential. This is separate from the Agents
# SDK connection manager below, which authenticates incoming Bot Service traffic.
credential = DefaultAzureCredential(
    managed_identity_client_id=config.managed_identity_client_id
)

gateway = FoundryModelGateway(
    config.project_endpoint,
    config.model_deployment_name,
    credential,
)
# Local Teams uses the installed bot's application identity for Graph RSC,
# while Foundry continues to use the developer credential above.
graph_credential = (
    ClientSecretCredential(*config.graph_bot_credentials)
    if config.graph_bot_credentials is not None
    else credential
)
logger.info("Microsoft Graph authentication mode: %s", config.graph_auth_mode)
graph_chat_history = GraphChatHistory(
    graph_credential,
    config.graph_history_message_count,
)

storage = MemoryStorage()
handoff_store = HandoffStore()
connection_manager = MsalConnectionManager(**agents_sdk_config)
adapter = CloudAdapter(connection_manager=connection_manager)
agent_app = AgentApplication[TurnState](
    storage=storage,
    adapter=adapter,
    connection_manager=connection_manager,
    **agents_sdk_config,
)


@agent_app.conversation_update("membersAdded")
async def on_members_added(context: TurnContext, _state: TurnState):
    await context.send_activity(
        "Demo Foundry Agent is ready. Ask a question in a personal chat, "
        "or mention it in a consenting group chat. In personal chat, describe an "
        "issue, then use 'handoff link' or 'handoff broker' to contact the configured helpdesk human."
    )


def card_activity(card: dict, text: str = "") -> Activity:
    return Activity(
        type=ActivityTypes.message, text=text, text_format="plain",
        attachments=[Attachment(content_type="application/vnd.microsoft.card.adaptive", content=card)],
    )


def handoff_identity(activity) -> tuple[str, str, str]:
    conversation = activity.conversation
    tenant = getattr(conversation, "tenant_id", None)
    sender = getattr(activity, "from_property", None)
    user = getattr(sender, "aad_object_id", None)
    chat = getattr(conversation, "id", None)
    if not tenant or not user or not chat:
        raise HandoffError("Handoff requires an authenticated Teams tenant, user identity, and personal chat.")
    if tenant.lower() != config.handoff.tenant_id.lower():
        raise HandoffError("Handoff is not configured for this Teams tenant.")
    config.handoff._guid(user, "requesting user's Entra object ID")
    if user.lower() == config.handoff.human_object_id.lower():
        raise HandoffError("Handoff cannot route the configured helpdesk human to themselves.")
    return tenant.lower(), user.lower(), chat


async def handle_handoff(context: TurnContext, user_text: str) -> bool:
    activity = context.activity
    value = activity.value if isinstance(activity.value, dict) else {}
    command = user_text.strip().lower()
    if not command.startswith("handoff") and "handoff_action" not in value:
        return False
    if getattr(activity.conversation, "conversation_type", None) != "personal":
        await context.send_activity("Start a handoff in a personal chat with this bot. Group context is not shared.")
        return True
    try:
        if "handoff_action" in value:
            tenant, user, chat = handoff_identity(activity)
            request_id = value.get("request_id")
            if not isinstance(request_id, str) or len(request_id) != 32:
                raise HandoffError("Invalid handoff confirmation. Start a new handoff.")
            if value["handoff_action"] == "cancel":
                handoff_store.cancel(request_id, tenant, user, chat)
                await context.send_activity("Handoff cancelled. No context was shared.")
                return True
            if value["handoff_action"] != "confirm":
                raise HandoffError("Unknown handoff action.")
            request = handoff_store.confirm(request_id, tenant, user, chat)
            config.handoff.validate(request.mode.value)
            if request.mode == HandoffMode.LINK:
                link = build_human_chat_link(tenant, config.handoff.human_upn, request.summary)
                await context.send_activity(card_activity(
                    {"type": "AdaptiveCard", "version": "1.4", "body": [],
                     "actions": [{"type": "Action.OpenUrl", "title": "Chat with helpdesk", "url": link}]},
                    "Teams opens a draft; you must send it yourself. The human has not been notified yet.",
                ))
                request.state = "link_ready"
            return True
        if command not in ("handoff link", "handoff broker"):
            await context.send_activity("Describe your issue, then use 'handoff link' or 'handoff broker'.")
            return True
        mode = HandoffMode(command.split()[1])
        config.handoff.validate(mode.value)
        tenant, user, chat = handoff_identity(activity)
        transcript = handoff_store.transcript(chat)
        if not transcript:
            raise HandoffError("Describe your issue in this personal chat before requesting a handoff.")
        summary = await asyncio.to_thread(gateway.summarize_handoff, transcript)
        request = handoff_store.create_request(mode, tenant, user, chat, summary)
        await context.send_activity(card_activity(build_confirmation_card(request, config.handoff.human_upn)))
    except ValueError as error:
        logger.warning("Handoff rejected: %s", error)
        await context.send_activity(str(error))
    except (AzureError, httpx.HTTPError, OpenAIError, RuntimeError) as error:
        logger.error("Handoff preparation failed: %s", type(error).__name__)
        await context.send_activity("Could not prepare the handoff. Check model access and try again; no context was shared.")
    return True


@agent_app.activity(ActivityTypes.message)
async def on_message(context: TurnContext, _state: TurnState):
    activity = context.activity
    conversation_type = getattr(
        getattr(activity, "conversation", None), "conversation_type", None
    )
    if conversation_type == "channel":
        logger.warning("Ignoring unsupported Teams channel message")
        return
    if is_group_conversation(activity) and not is_bot_mentioned(activity):
        return

    user_text = normalize_message(getattr(activity, "text", None))
    if await handle_handoff(context, user_text):
        return
    if not user_text:
        await context.send_activity("Send a question for Demo Foundry Agent.")
        return

    try:
        chat_history = []
        personal_chat_id = None
        if conversation_type == "personal":
            personal_chat_id = getattr(activity.conversation, "id", None)
            if personal_chat_id:
                handoff_store.add_message(personal_chat_id, "user", user_text)
        if is_group_conversation(activity):
            chat_id = getattr(getattr(activity, "conversation", None), "id", None)
            if not chat_id:
                raise RuntimeError("Teams group-chat activity has no conversation ID")
            chat_history = await asyncio.to_thread(graph_chat_history.read, chat_id)

        answer = await asyncio.to_thread(gateway.ask, user_text, chat_history)
    except (AzureError, httpx.HTTPError, OpenAIError, RuntimeError, ValueError):
        logger.exception("Foundry agent request failed")
        await context.send_activity(
            "Demo Foundry Agent could not read the chat context or call the model. "
            "Confirm its Foundry access and group-chat consent, then try again."
        )
        return

    await context.send_activity(answer)
    if personal_chat_id:
        handoff_store.add_message(personal_chat_id, "assistant", answer)


@agent_app.error
async def on_error(context: TurnContext, error: Exception):
    logger.error(
        "Unhandled agent error",
        exc_info=(type(error), error, error.__traceback__),
    )
    await context.send_activity("The agent encountered an unexpected error.")

import asyncio
import logging
import os

import httpx
from aiohttp import ClientError
from azure.core.exceptions import AzureError
from azure.identity import (
    ClientAssertionCredential, ClientSecretCredential, DefaultAzureCredential,
    ManagedIdentityCredential,
)
from dotenv import load_dotenv
from microsoft_agents.activity import (
    Activity, ActivityTypes, Attachment, ConversationReference, load_configuration_from_env,
)
from microsoft_agents.authentication.msal import MsalConnectionManager
from microsoft_agents.hosting.aiohttp import CloudAdapter
from microsoft_agents.hosting.core import (
    AgentApplication,
    MemoryStorage,
    TurnContext,
    TurnState,
)
from openai import OpenAIError

from config import Config, load_handoff_environment
from foundry import (
    FoundryModelGateway,
    GraphChatHistory,
    is_bot_mentioned,
    is_group_conversation,
    normalize_message,
)
from handoff import (
    HandoffBroker, HandoffError, HandoffMode, HandoffRequest, HandoffStore,
    build_confirmation_card, build_human_chat_link, support_chat_link,
)
from handoff_graph import GraphHandoffClient, HandoffOperationError

load_dotenv()
load_handoff_environment()
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
# RSC identifies an Entra app registration, which can trust the bot's managed
# identity without changing the Bot Connector or Foundry identities.
if config.graph_federated_credentials is not None:
    graph_managed_identity = ManagedIdentityCredential(
        client_id=config.managed_identity_client_id
    )
    graph_credential = ClientAssertionCredential(
        *config.graph_federated_credentials,
        lambda: graph_managed_identity.get_token("api://AzureADTokenExchange/.default").token,
    )
elif config.graph_bot_credentials is not None:
    graph_credential = ClientSecretCredential(*config.graph_bot_credentials)
else:
    graph_credential = credential
logger.info("Microsoft Graph authentication mode: %s", config.graph_auth_mode)
graph_chat_history = GraphChatHistory(
    graph_credential,
    config.graph_history_message_count,
)

storage = MemoryStorage()
handoff_store = HandoffStore()
handoff_origins: dict[str, ConversationReference] = {}
handoff_tasks: set[asyncio.Task] = set()
handoff_http: httpx.AsyncClient | None = None
handoff_broker: HandoffBroker | None = None
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
    if await deliver_support_summary(context):
        return
    await context.send_activity(
        "Demo Foundry Agent is ready. Ask a question in a personal chat, "
        "or mention it in a consenting group chat. In personal chat, describe an "
        "issue, then use 'handoff link' or 'handoff broker' to contact the configured helpdesk human."
    )


@agent_app.activity(ActivityTypes.installation_update)
async def on_installation_update(context: TurnContext, _state: TurnState):
    if context.activity.action == "add":
        await deliver_support_summary(context)


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


async def send_handoff_origin(request: HandoffRequest, message: str) -> None:
    reference = handoff_origins.get(request.request_id)
    bot_id = os.getenv("BOT_ID") or os.getenv("CONNECTIONS__SERVICE_CONNECTION__SETTINGS__CLIENTID")
    if not reference or not bot_id:
        raise HandoffOperationError("notify", "Original bot conversation or bot app ID is missing.")

    async def send(context: TurnContext):
        if request.chat_id:
            await context.send_activity(card_activity(
                {"type": "AdaptiveCard", "version": "1.4", "body": [],
                 "actions": [{"type": "Action.OpenUrl", "title": "Open support chat",
                              "url": support_chat_link(request.chat_id)}]},
                message,
            ))
        else:
            await context.send_activity(message)
    try:
        await adapter.continue_conversation(bot_id, reference.get_continuation_activity(), send)
    except (ClientError, AzureError, httpx.HTTPError, RuntimeError, OSError, ValueError) as error:
        raise HandoffOperationError("notify", "Could not notify the original conversation.", uncertain=True) from error


def get_handoff_broker() -> HandoffBroker:
    global handoff_http, handoff_broker
    if handoff_broker is None:
        handoff_http = httpx.AsyncClient()
        handoff_broker = HandoffBroker(
            handoff_store,
            GraphHandoffClient(graph_credential, config.handoff.catalog_app_id, handoff_http),
            send_handoff_origin,
        )
    return handoff_broker


def finish_handoff_task(task: asyncio.Task) -> None:
    handoff_tasks.discard(task)
    if not task.cancelled() and (error := task.exception()) is not None:
        logger.error("Handoff background task failed: %s", type(error).__name__)


async def close_handoff_resources() -> None:
    tasks = list(handoff_tasks)
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    if handoff_http is not None:
        await handoff_http.aclose()


async def deliver_support_summary(context: TurnContext) -> bool:
    activity = context.activity
    chat_id = getattr(activity.conversation, "id", None)
    if not chat_id or not handoff_store.is_support_chat(chat_id):
        return False
    tenant = getattr(activity.conversation, "tenant_id", None)
    if not tenant or tenant.lower() != config.handoff.tenant_id.lower():
        logger.error("Ignoring support event with missing or unexpected tenant")
        return True

    async def send_summary(text: str):
        try:
            await context.send_activity(Activity(type=ActivityTypes.message, text=text, text_format="plain"))
        except (ClientError, AzureError, httpx.HTTPError, RuntimeError, OSError, ValueError) as error:
            raise HandoffOperationError(
                "send", "Bot summary delivery is unconfirmed; inspect the support group before retrying.",
                uncertain=True,
            ) from error

    return await get_handoff_broker().on_group_available(chat_id, send_summary)


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
            else:
                broker = get_handoff_broker()
                handoff_origins[request.request_id] = activity.get_conversation_reference()
                await context.send_activity("Starting the support-group handoff. This is not confirmation of context delivery.")
                task = asyncio.create_task(broker.start(request, config.handoff.human_object_id))
                handoff_tasks.add(task)
                task.add_done_callback(finish_handoff_task)
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
        request.requester_name = getattr(activity.from_property, "name", None) or "Teams user"
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
    if handoff_store.is_support_chat(getattr(activity.conversation, "id", None)):
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

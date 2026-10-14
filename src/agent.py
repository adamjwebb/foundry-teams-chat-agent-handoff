import asyncio
import logging
import os

import httpx
from azure.core.exceptions import AzureError
from azure.identity import DefaultAzureCredential
from dotenv import load_dotenv
from microsoft_agents.activity import ActivityTypes, load_configuration_from_env
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

# Share one credential chain, not one access token. Each downstream SDK requests
# and caches a token for its own audience: Microsoft Foundry for inference and
# Microsoft Graph for chat history.
gateway = FoundryModelGateway(
    config.project_endpoint,
    config.model_deployment_name,
    credential,
)
graph_chat_history = GraphChatHistory(
    credential,
    config.graph_history_message_count,
)

storage = MemoryStorage()
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
        "or mention it in a consenting group chat."
    )


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
    if not user_text:
        await context.send_activity("Send a question for Demo Foundry Agent.")
        return

    try:
        chat_history = []
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


@agent_app.error
async def on_error(context: TurnContext, error: Exception):
    logger.error(
        "Unhandled agent error",
        exc_info=(type(error), error, error.__traceback__),
    )
    await context.send_activity("The agent encountered an unexpected error.")

# Demo Foundry Agent

A Microsoft 365 Agents Toolkit Python proof of concept that connects Teams to a
shared Microsoft Foundry project.

## Development and deployment guide

Follow the [step-by-step guide](docs/development-and-deployment.md) to install
the prerequisites, deploy Foundry, develop locally, deploy the Teams bot, and
verify personal and consenting group chats.

## Behavior

- Uses `AIProjectClient` and the Responses API with `DefaultAzureCredential`.
- Answers personal-chat messages without reading Microsoft Graph history.
- Responds in group chats only when mentioned.
- Reads a bounded window of recent group-chat messages after the chat grants
  `ChatMessage.Read.Chat` resource-specific consent.
- Ignores channel messages and treats chat history as untrusted model context.
- When deployed, uses one user-assigned managed identity for App Service,
  Azure Bot, Foundry, and Microsoft Graph.
- In local Teams debugging, uses the local bot's app identity for Graph and
  your developer credential for Foundry.

The Teams agent lifecycle is rooted in `m365agents*.yml`. The shared Foundry
account, project, and model are independently managed with AZD under
[`foundry-platform/`](foundry-platform/README.md).

## Configure

Set these values for the workflow you want to run:

- Local Teams debugging: `env/.env.local.user`
- Agents Playground: `env/.env.playground.user`
- Deployed Teams testing: `env/.env.dev`

```dotenv
AZURE_AI_PROJECT_ENDPOINT=https://<account>.services.ai.azure.com/api/projects/<project>
AZURE_AI_MODEL_DEPLOYMENT_NAME=gpt-4.1-mini
GRAPH_HISTORY_MESSAGE_COUNT=10
```

The local Teams workflow automatically sets `GRAPH_AUTH_MODE=bot` and reuses
the generated bot connection's tenant ID, client ID, and secret for Graph.
No extra Graph secret or tenant-wide chat permission is required.

## Credential flow

`DefaultAzureCredential` is the outbound credential chain. In the deployed App
Service, `AZURE_CLIENT_ID` selects the provisioned user-assigned managed
identity; locally, Azure CLI or VS Code credentials can satisfy the chain.

The application passes credentials—not reusable raw tokens—to the downstream
clients:

1. The Graph history reader requests a token for
   `https://graph.microsoft.com/.default`. Microsoft Graph additionally checks
   that the installed app has `ChatMessage.Read.Chat` consent for the requested
   group chat. Local Teams debugging uses `ClientSecretCredential` for the
   installed local bot; deployed testing uses the managed identity.
2. `AIProjectClient` requests Foundry-scoped tokens and configures its
   OpenAI-compatible Responses client to use them for inference. This always
   uses `DefaultAzureCredential`, independently of local Graph authentication.

Incoming Bot Service authentication remains separate and is handled by the
Microsoft 365 Agents SDK connection manager.

## Agents Playground

In VS Code, run **Debug in Microsoft 365 Agents Playground**. This provisions
the local Playground tooling, starts the Python agent on port 3978, and opens
the Playground.

> [!NOTE]
> **Use for:** Fast local debugging of personal-chat responses, Foundry
> connectivity, and model prompts, with Python breakpoints.
>
> **Requires:** Your local Python service and an Azure credential with Foundry
> access. No Teams app installation, bot registration, or development tunnel is
> needed; inference still calls the live Foundry model.
>
> **Limitations:** Playground simulates Teams conversations but does not create
> real Teams chats or grant `ChatMessage.Read.Chat` consent. This agent's
> group-chat path calls Microsoft Graph, so a simulated group chat cannot test
> history retrieval and can return the chat-context error even when personal
> chat works. See [step 6](docs/development-and-deployment.md#6-develop-locally-with-agents-playground).

## Local Teams debugging

Sign in to Microsoft 365 and Azure through Agents Toolkit, then run one of:

- **Debug in Teams (Edge)**
- **Debug in Teams (Chrome)**
- **Debug in Teams (Desktop)**

The workflow creates the local app registration, starts a development tunnel,
packages the app, and starts the Python process.

> [!NOTE]
> **Use for:** Debugging real Teams message handling, personal-chat responses,
> and consenting group-chat history retrieval with local Python breakpoints.
>
> **Requires:** A Teams tenant that allows custom app installation, the local
> bot app registration, a running development tunnel, and installation of the
> local app with `ChatMessage.Read.Chat` consent in each test group chat. Stop
> Playground before switching workflows; both generate the root `.env`.
>
> **Limitations:** Local testing uses a separate app identity from the deployed
> managed identity; each app needs its own chat installation and consent.
> Use non-sensitive messages because recent history is sent to Foundry.
> Playground cannot reproduce this chat consent.
> See [step 7](docs/development-and-deployment.md#7-debug-locally-in-teams-optional).

## Deployed Teams testing

Configure `env/.env.dev`, then run:

```bash
atk auth login azure
atk auth login m365
atk provision --env dev -i false
atk deploy --env dev -i false
```

Grant the generated user-assigned managed identity access to the shared Foundry
project. Install the generated app package in a test group chat, approve its
request to read that chat's messages, and mention the agent.

> [!NOTE]
> **Use for:** End-to-end verification of the deployed bot's personal-chat
> responses and consenting group-chat history retrieval with its managed
> identity.
>
> **Requires:** Deployed Azure resources, Foundry access for the managed identity,
> and app installation with `ChatMessage.Read.Chat` consent in each test group
> chat. Use non-sensitive messages because recent history is sent to Foundry.
>
> **Limitations:** This runs in Azure, not under the local Python debugger; use
> App Service logs to investigate failures. Personal-chat success or personal app
> installation does not prove group-chat access. Group replies require a real
> bot mention, and Teams channels are unsupported.
> See [step 11](docs/development-and-deployment.md#11-install-in-teams-and-verify-the-system).

## Tests

```bash
python -m pytest tests -q
```

The tests intentionally focus on the POC's critical behavior and package
contract rather than exhaustive coverage.

## Development resources

Official guidance for building Microsoft 365 Teams bots:

- [Microsoft 365 Agents Toolkit (ATK) overview](https://learn.microsoft.com/en-us/microsoftteams/platform/toolkit/agents-toolkit-fundamentals)
  - Set up the toolkit and understand the development lifecycle.
- [ATK CLI reference](https://learn.microsoft.com/en-us/microsoftteams/platform/toolkit/microsoft-365-agents-toolkit-cli)
  - Learn the commands and options for provisioning, deploying, and managing apps.
- [Microsoft 365 Agents SDK documentation](https://learn.microsoft.com/en-us/microsoft-365/agents-sdk/)
  - Follow SDK development guidance, including Python quickstarts and samples.
- [Teams bot design guidelines](https://learn.microsoft.com/en-us/microsoftteams/platform/bots/design/bots)
  - Design bot interactions, onboarding, and personal and shared chat experiences.
- [Teams app use cases and capabilities](https://learn.microsoft.com/en-us/microsoftteams/platform/concepts/design/understand-use-cases)
  - Choose appropriate app capabilities and conversation scopes.

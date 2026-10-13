# Foundry Teams Chat Agent POC

A minimal Python hosted agent that replies in Teams and reads recent messages
from a consenting group chat using its own Entra agent identity. No separate
Graph app registration or client secret is configured in the container.

**Design:** Teams -> Azure Bot -> Foundry Activity endpoint -> hosted agent ->
Microsoft Graph (chat RSC) + Foundry model -> Teams.

The [architecture document](docs/architecture.md) describes the components,
request flow, identity and authorization model, security considerations, and
design decisions, with Microsoft references.

Open the [secretless architecture and setup walkthrough](docs/secretless-setup.html)
for a portable HTML guide covering the end-to-end flow, identity boundaries,
deployment and chat consent steps, live acceptance checks, and official sources.
It can be opened directly in a browser without a server.

## Prerequisites

- Azure CLI and Azure Developer CLI, authenticated to the intended tenant.
- AZD extensions `azure.ai.agents` >= `1.0.0-beta.19` and
  `azure.ai.projects` >= `1.0.0-beta.13` (the versions used for this review).
- Permission to create Foundry resources and Azure Bot/Teams channels, plus
  Foundry data-plane access for deploying agents. See
  [hosted-agent permissions](https://learn.microsoft.com/azure/foundry/agents/concepts/hosted-agent-permissions).
- Model availability/quota for `gpt-4.1-mini` in the selected region.
- Teams custom-app upload and group-chat RSC allowed by tenant policy.
- Python 3.10+ for local development; hosted deployment uses Python 3.13.
- Testers assigned **Foundry Agent Consumer** at the agent or project scope
  for the configured `BotServiceRbac` authorization.

## Provision and deploy

Create a fresh AZD environment for this project:

```bash
azd env new foundry-teams-chat-agent-poc
azd env set AZURE_SUBSCRIPTION_ID "<subscription-id>"
azd env set AZURE_LOCATION "swedencentral"
azd env set AZURE_RESOURCE_GROUP "rg-foundry-teams-chat-agent-poc"
azd env set TEAMS_APP_VERSION "1.0.0"
```

Then:

```bash
azd provision --no-prompt
azd deploy --no-prompt
```

Provisioning creates the Foundry account, project, and model from
[infra/main.bicep](infra/main.bicep). Code deployment creates the hosted agent
and its Activity bot/channel. The post-deploy hook discovers the bot application
ID, calls `azd ai agent pack`, and adds the POC's chat-scoped RSC declaration.
It fails rather than fabricating a package when Microsoft 365 packaging fails.

Outputs:

- `foundry-teams-chat-agent-poc.zip`: the package to upload to Teams.
- `appPackage/manifest.json`: the generated manifest for inspection.

The builder preserves the generated Teams title ID, bot/agent ID, and icons. It
replaces a placeholder `webApplicationInfo.resource` (such as
`api://example.com`) with Microsoft's documented RSC value
`https://RscBasedStoreApp`. It advertises only personal and group-chat scopes,
disables files, requests only `ChatMessage.Read.Chat` application RSC, and adds
a description explaining how recent group-chat messages are used. It then
validates the final manifest before writing the package. The checked-in
[manifest template](appPackage/manifest.template.json) is illustrative, not the
source of the ownership-bound package.

### Change the Teams app version

Set the version once in the active AZD environment:

```bash
azd env set TEAMS_APP_VERSION "1.0.1"
azd deploy --no-prompt
```

This setting is required for existing environments too. Both post-deploy hooks
validate it and pass it explicitly to `azd ai agent pack --app-version`.
If the agent deployed successfully but the hook failed because this value was
missing, set it and run `azd hooks run postdeploy` to retry packaging without
redeploying. Preserve the current version in `appPackage/manifest.json` for a
retry, or choose a higher version for a Teams app update.
The `azure.ai.agents` beta.19 extension does not interpolate environment
placeholders in `activity.publish.appVersion`, so that field is omitted from
[azure.yaml](azure.yaml). Use a three-part numeric version (`major.minor.patch`).
The extension's automatic raw package is not the POC deliverable: use the
versioned, RSC-modified `foundry-teams-chat-agent-poc.zip` produced by the post-deploy hook.
For manifest or permission changes, upload/update the regenerated Teams package
and grant any new consent; deployment does not update installed Teams apps.

### Publish without custom app upload

If your tenant blocks custom app upload, set `TEAMS_PUBLISH_TO_M365` to `true`
so the post-deploy hook also runs `azd ai agent publish` with the
no-admin-approval `shared` scope:

```bash
azd env set TEAMS_PUBLISH_TO_M365 true
azd hooks run postdeploy
```

That Foundry-generated app does not include group-chat history consent. See
[Publish through Foundry instead of uploading](docs/TEAMS_APP_SETUP.md#publish-through-foundry-instead-of-uploading).

## Run the demo

Follow the [Teams setup guide](docs/TEAMS_APP_SETUP.md):

1. Upload the generated package, then **add it to a test group chat**.
2. Review and grant its permission to read that chat's messages.
3. Send a context message, then mention the bot and ask about that message.
4. Confirm unmentioned group-chat messages produce no response.

Personal installation alone does not grant group-chat history access.
Team/channel messages, Copilot, file uploads, delegated sign-in, and durable
application-managed memory are out of scope.

**Before presenting this as proven:** complete the
[live acceptance criteria](docs/architecture.md#9-live-acceptance-criteria),
including runtime token identity, allowed-chat access, denied-chat access,
and revocation. Offline tests do not prove Teams consent or Graph authorization.

## Local development and tests

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r src/foundry-teams-chat-agent/requirements.txt pytest
python -m pytest src/foundry-teams-chat-agent/tests -q

export AZURE_AI_PROJECT_ENDPOINT="https://<account>.services.ai.azure.com/api/projects/<project>"
export AZURE_AI_MODEL_DEPLOYMENT_NAME="gpt-4.1-mini"
python src/foundry-teams-chat-agent/main.py
```

The Activity host exposes `POST /activity/messages` on port `8088` by default.
Developer credentials can test model access but do not reproduce the deployed
agent's application-only RSC identity. Do not add a Graph client secret to
work around a local authorization failure.

Read-only hook check (no cloud calls or generated files):

```bash
BOT_PROVISION_DRY_RUN=true \
AZURE_SUBSCRIPTION_ID="<subscription-id>" \
AZURE_RESOURCE_GROUP="rg-foundry-teams-chat-agent-poc" \
AGENT_FOUNDRY_TEAMS_CHAT_AGENT_BOT_NAME="<deployed-bot-name>" \
TEAMS_APP_VERSION="1.0.1" \
sh scripts/provision-bot.sh
```

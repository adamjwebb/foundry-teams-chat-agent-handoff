# Development and deployment

For the fork's optional static-human handoff configuration, Graph permissions,
and deep-link/broker walkthroughs, see [Human handoff in Teams](handoff-examples.md).
The original setup below still works without enabling either example.

Run the commands from the repository root in the same Bash terminal unless a
step says otherwise. The commands work on Linux, macOS, or Windows with WSL.

| Component | Deployment tool | Working directory |
| --- | --- | --- |
| Foundry account, project, and model | AZD: `azd provision` | `foundry-platform` |
| Teams app, Azure Bot, managed identity, and App Service | ATK: `atk provision` | Repository root |
| Python bot application | ATK: `atk deploy` | Repository root |

## 1. Install the prerequisites

| Requirement | Setup |
| --- | --- |
| Git | [Install Git](https://git-scm.com/downloads). |
| Python 3.11 | [Install Python](https://www.python.org/downloads/), including `pip` and `venv`. Azure App Service uses Python 3.11 in this project. |
| Node.js 22 LTS and npm | [Install Node.js](https://nodejs.org/en/download). |
| Azure CLI | [Install Azure CLI](https://learn.microsoft.com/en-us/cli/azure/install-azure-cli). |
| Azure Developer CLI (AZD) | [Install AZD](https://learn.microsoft.com/en-us/azure/developer/azure-developer-cli/install-azd). It manages the shared Foundry platform independently of ATK. |
| VS Code | [Install VS Code](https://code.visualstudio.com/download), the [Python extension](https://marketplace.visualstudio.com/items?itemName=ms-python.python), and [Microsoft 365 Agents Toolkit](https://marketplace.visualstudio.com/items?itemName=TeamsDevApp.ms-teams-vscode-extension). |
| Azure subscription | Use a subscription with quota for `gpt-4.1-mini`, version `2025-04-14`, using Global Standard capacity 10, and a region supporting Linux App Service. |
| Microsoft 365 tenant with Teams | Use an account allowed to upload custom Teams apps. Ask the tenant administrator to enable custom app upload and group-chat resource-specific consent if restricted. |
| Deployment permissions | You need resource creation permissions, such as Contributor, plus permission to assign Azure roles, such as Role Based Access Control Administrator, at the relevant scopes. Creating resource groups and registering providers also requires subscription-level permissions. Local Teams debugging additionally requires permission to create Entra app registrations. |

Use the same Entra tenant for the Azure subscription, Microsoft 365 account,
and deployed bot identity. App Service and model usage incur Azure charges.

Install the ATK CLI:

```bash
npm install -g @microsoft/m365agentstoolkit-cli
atk --version
az version
azd version
python3.11 --version
node --version
```

## 2. Clone and prepare the project

```bash
git clone https://github.com/lucusc/foundry-teams-chat-agent.git
cd foundry-teams-chat-agent
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r src/requirements.txt pytest
python -m pytest tests -q
code .
```

In VS Code, run **Python: Select Interpreter** and select this project's
`.venv`. Open the project in WSL when following these commands on Windows.

The unit tests use mocks; they do not create Azure resources or prove that
Foundry permissions or Teams consent work.

## 3. Sign in and choose deployment names

Enter your tenant and subscription IDs when prompted. Change the region if
your model quota or App Service availability requires it.

```bash
read -r -p "Azure tenant ID: " TENANT_ID
read -r -p "Azure subscription ID: " SUBSCRIPTION_ID
LOCATION=eastus2
FOUNDRY_ENV=foundry-teams-chat-agent-poc
FOUNDRY_RG=rg-foundry-teams-platform-dev
APP_RG=rg-foundry-teams-app-dev

az login --tenant "$TENANT_ID"
az account set --subscription "$SUBSCRIPTION_ID"
atk auth login azure
atk auth login m365
atk auth list
az account show --query '{subscription:id,tenant:tenantId,name:name}' -o table
```

Check that the ATK accounts and Azure CLI are using the intended tenant and
subscription. Signing in with ATK does not sign in Azure CLI; local Foundry
calls use the Azure CLI credential through `DefaultAzureCredential`.

Register the required providers if they are not already registered. A
subscription administrator can run these commands for you:

```bash
az provider register --namespace Microsoft.CognitiveServices --wait
az provider register --namespace Microsoft.Web --wait
az provider register --namespace Microsoft.ManagedIdentity --wait
az provider register --namespace Microsoft.BotService --wait
```

Create two dedicated resource groups so the shared model and the Teams app
can be managed independently. For an existing deployment, use its actual
names and region instead of the example values:

```bash
az group create --name "$FOUNDRY_RG" --location "$LOCATION"
az group create --name "$APP_RG" --location "$LOCATION"
```

## 4. Deploy the shared Foundry platform

The [Foundry template](../foundry-platform/infra/main.bicep) creates a Foundry
account, project, and model deployment. It does not deploy the Python bot or
assign caller permissions. Run its standalone AZD project from
`foundry-platform`:

### Create the AZD environment and provision

```bash
cd foundry-platform
azd auth login --tenant-id "$TENANT_ID"
azd env new "$FOUNDRY_ENV" \
  --subscription "$SUBSCRIPTION_ID" --location "$LOCATION"
azd env set AZURE_RESOURCE_GROUP "$FOUNDRY_RG"
azd provision --no-prompt
```

If this AZD environment already exists in `foundry-platform`, replace the
`azd env new` command with `azd env select "$FOUNDRY_ENV"`. Check
`azd env get-values` before provisioning; retain the existing subscription,
resource group, region, and project/model names.

AZD authentication is separate from the Azure CLI and ATK sign-ins in step 3.
Use the same tenant for all three.

Wait for the deployment to succeed. If model availability or quota blocks it,
choose a supported region with quota before continuing. The template's
`modelDeploymentName` controls both the model name and deployment name;
`modelVersion`, `modelCapacity`, and `projectName` can also be customized in
the [AZD parameter file](../foundry-platform/infra/main.parameters.json).
The existing default project name is `foundry-teams-chat-agent-poc`.

### Read the outputs and return to the ATK project

Read the deployment outputs from the selected AZD environment, then return
to the repository root:

```bash
PROJECT_ENDPOINT=$(azd env get-value AZURE_AI_PROJECT_ENDPOINT)
MODEL_DEPLOYMENT=$(azd env get-value AZURE_AI_MODEL_DEPLOYMENT_NAME)
PROJECT_ID=$(azd env get-value AZURE_AI_PROJECT_ID)

printf 'Project endpoint: %s\nModel deployment: %s\nProject resource ID: %s\n' \
  "$PROJECT_ENDPOINT" "$MODEL_DEPLOYMENT" "$PROJECT_ID"
cd ..
```

AZD state is saved under `foundry-platform/.azure/` and ignored by Git.
This is an infrastructure-only project: no `azd deploy`, hosted-agent
extensions, or legacy Teams publishing hooks are needed. Continue using ATK
from the repository root for the Teams application.

| AZD output | Used by the Teams application |
| --- | --- |
| `AZURE_AI_PROJECT_ENDPOINT` | `PROJECT_ENDPOINT` in this terminal; copied into the local and cloud ATK configuration in later steps. |
| `AZURE_AI_MODEL_DEPLOYMENT_NAME` | `MODEL_DEPLOYMENT` in this terminal; copied into the same ATK configuration files. |
| `AZURE_AI_PROJECT_ID` | `PROJECT_ID` in this terminal; used for developer and bot role assignments. |

AZD does not populate ATK environment files or grant these callers Foundry
access. Complete the remaining steps even after `azd provision` succeeds.

The endpoint must look like
`https://<account>.services.ai.azure.com/api/projects/<project>`.
Do not use an agent endpoint ending in `/agents/...`, `/openai/...`, or
`/protocols/activityprotocol`. This solution calls a model through the
Responses API; it does not require a separately published Foundry agent.

For an existing shared platform, skip resource creation and deployment and
set `PROJECT_ENDPOINT`, `MODEL_DEPLOYMENT`, and `PROJECT_ID` from its actual
outputs. To adopt resources into this AZD project instead, first follow
[the existing-platform instructions](../foundry-platform/README.md#update-or-adopt-an-existing-platform)
to preserve subscription, resource group, location, and project/model names.
Do not recreate another team's shared resources.

## 5. Grant your developer account Foundry access

Assign the project-scoped **Foundry User** role, formerly **Azure AI User**, to
your Azure CLI user. The role ID below works across the role-name transition.
Contributor alone does not grant model inference access.

```bash
FOUNDRY_USER_ROLE=53ca6127-db72-4b80-b1b0-d745d6d5456d
DEVELOPER_ID=$(az ad signed-in-user show --query id -o tsv)

az role assignment create \
  --assignee-object-id "$DEVELOPER_ID" \
  --assignee-principal-type User \
  --role "$FOUNDRY_USER_ROLE" \
  --scope "$PROJECT_ID"
```

If you cannot assign roles, have an Azure role administrator run the
assignment. Allow several minutes for new assignments to propagate.

## 6. Develop locally with Agents Playground

Skip to [step 8](#8-configure-a-fresh-cloud-environment) if you only want the
cloud deployment.

Create the local Playground configuration:

```bash
cat > env/.env.playground <<EOF
TEAMSFX_ENV=playground
TEAMSAPPTESTER_PORT=56150
EOF

cat > env/.env.playground.user <<EOF
AZURE_AI_PROJECT_ENDPOINT=$PROJECT_ENDPOINT
AZURE_AI_MODEL_DEPLOYMENT_NAME=$MODEL_DEPLOYMENT
GRAPH_HISTORY_MESSAGE_COUNT=10
EOF
```

1. In VS Code, open **Run and Debug**.
2. Select **Debug in Microsoft 365 Agents Playground** and press **F5**.
3. Wait for the Python service on port `3978` and Playground on port `56150`.
4. Send a personal-chat message such as `What can you help me with?`.
5. Confirm that the model returns an answer and the Python terminal has no
   authentication or inference errors.

The workflow installs Playground tooling and generates the root `.env`
runtime file, covered by the [Git ignore rules](../.gitignore). It allows
anonymous incoming messages only for this
local test workflow. Foundry still requires your Azure sign-in and role.
Do not expose this anonymous service publicly.

Playground does not verify real Teams group-chat history or Graph RSC
permissions. Stop debugging before switching between Playground and Teams;
both workflows overwrite the root `.env`.

## 7. Debug locally in Teams (optional)

Use this to test Teams message handling and breakpoints. Use the deployed
identity in [step 11](#11-install-in-teams-and-verify-the-system) for the
end-to-end group-history test.

Create the local Teams configuration. For a new local setup only:

```bash
cat > env/.env.local <<EOF
TEAMSFX_ENV=local
APP_NAME_SUFFIX=local
EOF

cat > env/.env.local.user <<EOF
AZURE_AI_PROJECT_ENDPOINT=$PROJECT_ENDPOINT
AZURE_AI_MODEL_DEPLOYMENT_NAME=$MODEL_DEPLOYMENT
GRAPH_HISTORY_MESSAGE_COUNT=10
EOF
```

1. Sign in to Microsoft 365 through the **Agents Toolkit** view in VS Code.
2. Select **Debug in Teams (Edge)**, **Debug in Teams (Chrome)**, or
   **Debug in Teams (Desktop)** in **Run and Debug** and press **F5**.
3. Let the tasks start the public development tunnel, create the local Entra
   bot registration, provision the Teams app, and generate runtime settings.
4. Install the local app when Teams opens, then test a personal chat.
5. Set a breakpoint in [the message handler](../src/agent.py) and send another
   message.
6. Add the **local** app to a test group chat, approve its request to read
   that chat's messages, and mention the agent with a question about recent
   non-sensitive messages.

The generated root `.env` must include
`CONNECTIONS__SERVICE_CONNECTION__SETTINGS__TENANTID`, populated from
`TEAMS_APP_TENANT_ID` in `env/.env.local`. Never copy the local bot password
into a tracked file.

Local bot authentication uses an app registration and generated secret.
The local workflow sets `GRAPH_AUTH_MODE=bot`, so outbound Graph calls use
`ClientSecretCredential` with that same bot connection's tenant ID, client ID,
and secret. Foundry calls still use your developer credential through
`DefaultAzureCredential`; the local bot does not need a separate Foundry role.
The deployed workflow continues to use its managed identity for both clients.

Restart **Debug in Teams (Edge)** (or the Chrome/Desktop equivalent) after
updating this code so the pre-launch tasks regenerate the root `.env`. Look
for `Microsoft Graph authentication mode: bot` at startup. Do not manually put
the generated secret into tracked files or set `AZURE_CLIENT_SECRET` globally:
that would also change Foundry's credential selection.

Consent belongs to the installed app in that specific chat, not to your Azure
CLI user. A successful personal chat or installation of the deployed app does
not authorize the local app to read group history. If the local app was
installed before its manifest requested `ChatMessage.Read.Chat`, update or
reinstall it in the test group chat and approve the permission. The Playground
workflow explicitly resets `GRAPH_AUTH_MODE=default`; simulated chats still
cannot grant RSC consent.

## 8. Configure a fresh cloud environment

For a **new deployment**, replace the existing contents of
[`env/.env.dev`](../env/.env.dev) using the commands below. The repository can
contain IDs from an earlier deployment; do not reuse those for a new system.
For an existing deployment you intend to update, retain its generated IDs
and resource suffix instead of running this block.

```bash
RESOURCE_SUFFIX=$(python -c 'import secrets; print(secrets.token_hex(3))')

cat > env/.env.dev <<EOF
TEAMSFX_ENV=dev
APP_NAME_SUFFIX=dev
AZURE_SUBSCRIPTION_ID=$SUBSCRIPTION_ID
AZURE_RESOURCE_GROUP_NAME=$APP_RG
RESOURCE_SUFFIX=$RESOURCE_SUFFIX
AZURE_AI_PROJECT_ENDPOINT=$PROJECT_ENDPOINT
AZURE_AI_MODEL_DEPLOYMENT_NAME=$MODEL_DEPLOYMENT
GRAPH_HISTORY_MESSAGE_COUNT=10
EOF
```

`RESOURCE_SUFFIX` gives the bot, identity, App Service plan, and web app the
name `bot<suffix>`. Use a new suffix if the web app name is already taken.
Keep the suffix stable on subsequent deployments.

| Setting | Meaning |
| --- | --- |
| `TEAMSFX_ENV` | `dev`, matching `atk --env dev`. |
| `APP_NAME_SUFFIX` | Distinguishes this Teams app from the local app. |
| `AZURE_SUBSCRIPTION_ID` | Subscription where the Teams app resources will run. |
| `AZURE_RESOURCE_GROUP_NAME` | The dedicated app resource group created in step 3. |
| `RESOURCE_SUFFIX` | Naming suffix; `bot` plus this value must be 4-20 characters. Use lowercase letters and digits. |
| `AZURE_AI_PROJECT_ENDPOINT` | The project endpoint from step 4, not an agent activity endpoint. |
| `AZURE_AI_MODEL_DEPLOYMENT_NAME` | Exact deployed model name from step 4. |
| `GRAPH_HISTORY_MESSAGE_COUNT` | Number of recent group-chat messages to request; integer from `1` to `50`, default `10`. |

ATK writes generated IDs into this file during provisioning. Do not manually
set `BOT_ID` or substitute `TEAMS_APP_ID` for it. The first is the bot
identity's client ID; the second identifies the Teams app package.

The cloud environment file is tracked by Git. Review changes before committing
and never put secrets in it. Local `.user` files and the generated root `.env`
are ignored by Git.

## 9. Provision the Teams app resources

```bash
atk provision --env dev -i false
```

Wait for all actions to finish successfully. This runs
[`m365agents.yml`](../m365agents.yml) and creates:

- A Teams app registration and validated package.
- A user-assigned managed identity used by the bot and outbound SDK clients.
- A Linux App Service plan on the `B1` tier and a Python 3.11 web app.
- An Azure Bot resource with its Teams channel enabled and messaging endpoint
  set to `https://<web-app>.azurewebsites.net/api/messages`.

The package is written to
`appPackage/build/appPackage.dev.zip`. Generated values, including `BOT_ID`,
`TEAMS_APP_ID`, `BOT_DOMAIN`, and `BOT_AZURE_APP_SERVICE_RESOURCE_ID`, are
written to `env/.env.dev`.

This step does **not** deploy Foundry, grant the bot Foundry access, upload the
Python application, or install the app into a Teams chat.

## 10. Grant the bot access and deploy the Python application

Read the provisioned web app and identity:

```bash
WEBAPP_ID=$(az deployment group show \
  --resource-group "$APP_RG" --name Create-demo-foundry-agent-resources \
  --query properties.outputs.BOT_AZURE_APP_SERVICE_RESOURCE_ID.value -o tsv)
WEBAPP_NAME=$(az webapp show --ids "$WEBAPP_ID" --query name -o tsv)
BOT_PRINCIPAL_ID=$(az identity show \
  --resource-group "$APP_RG" --name "$WEBAPP_NAME" \
  --query principalId -o tsv)
```

The template gives the web app and its managed identity the same resource name.

Grant Foundry access to the managed identity's **principal/object ID**, not its
client ID:

```bash
az role assignment create \
  --assignee-object-id "$BOT_PRINCIPAL_ID" \
  --assignee-principal-type ServicePrincipal \
  --role "$FOUNDRY_USER_ROLE" \
  --scope "$PROJECT_ID"

atk deploy --env dev -i false
```

Deployment zip-uploads [the Python source](../src) to App Service. App Service
builds the dependencies from [the requirements file](../src/requirements.txt)
and starts Gunicorn with the aiohttp worker configured in
[`infra/azure.bicep`](../infra/azure.bicep). Allow time for the remote build,
startup, and role-assignment propagation.

Check the web app and enable its container log stream:

```bash
az webapp show --ids "$WEBAPP_ID" \
  --query '{state:state,host:defaultHostName}' -o table
az webapp log config --resource-group "$APP_RG" --name "$WEBAPP_NAME" \
  --docker-container-logging filesystem
az webapp log tail --resource-group "$APP_RG" --name "$WEBAPP_NAME"
```

Keep the logs open while testing, or press **Ctrl+C** to stop tailing without
stopping the web app. The app exposes `POST /api/messages`, not a browser home
page or health endpoint. A running resource or a browser GET response alone
does not prove that Teams messaging works.

## 11. Install in Teams and verify the system

Use a test chat with non-sensitive content: when mentioned, the bot sends
recent group-chat messages to the configured Foundry model.

1. Sign in to Teams using the tenant from step 3.
2. Open **Apps > Manage your apps > Upload an app > Upload a custom app**.
   If custom upload is missing, ask the Teams administrator to enable it.
3. Upload `appPackage/build/appPackage.dev.zip`.
4. Add the app to a personal chat and send `Say hello in one sentence`.
   Confirm that you receive a model answer.
5. Add the app to a **group chat**, not a team channel. An authorized chat
   member must approve the requested **Read this chat's messages**
   (`ChatMessage.Read.Chat`) permission. Tenant policy may require
   administrator involvement.
6. Post a short fact, for example `The launch date is 15 November`.
7. Mention the bot using Teams' mention picker and ask
   `@demo-foundry-agentdev What launch date did we agree on?`.
8. Confirm that the answer uses the recent chat fact and that the App Service
   logs show no Graph or Foundry failures.
9. Send a normal group-chat message without mentioning the bot. Confirm it
   does not respond.

Approve consent separately for each group chat where history access is needed.
Installing the app personally does not grant group-chat access. Do not replace
the scoped permission with tenant-wide `Chat.Read.All` or
`ChatMessage.Read.All` permissions.

The same managed identity is used for Bot Service authentication, Foundry
inference, and Graph history reads. Foundry access comes from Azure RBAC;
Graph access comes from Teams chat-specific consent. Channels are unsupported.
The application does not persist the retrieved history.

## 12. Make changes and redeploy

| Change | Action |
| --- | --- |
| Python behavior or dependencies | Edit [the source](../src) or [requirements](../src/requirements.txt), run `python -m pytest tests -q`, then `atk deploy --env dev -i false`. |
| Foundry endpoint, model name, or history count | Update `env/.env.dev`, run `atk provision --env dev -i false` to apply App Service settings, then verify in Teams. Code deployment alone does not update these settings. |
| Infrastructure | Edit [the app template](../infra/azure.bicep) or [parameters](../infra/azure.parameters.json), then run `atk provision --env dev -i false`. Redeploy code if it also changed. |
| Teams manifest or icons | Update [the app package source](../appPackage), bump the manifest version, run `atk provision --env dev -i false`, and upload/update the rebuilt package in Teams. Permission changes can require renewed consent. |
| Shared model platform | Update [the Foundry template](../foundry-platform/infra/main.bicep) or [parameters](../foundry-platform/infra/main.parameters.json), then follow [Update Foundry with AZD](#update-foundry-with-azd). |

Keep generated IDs stable when updating the existing system. For another
environment, use separate environment files, resource groups, naming suffixes,
and Teams app IDs.

### Update Foundry with AZD

From the repository root, use the same environment name as the original
deployment:

```bash
cd foundry-platform
azd env select "$FOUNDRY_ENV"
azd env get-values
azd provision --no-prompt
PROJECT_ENDPOINT=$(azd env get-value AZURE_AI_PROJECT_ENDPOINT)
MODEL_DEPLOYMENT=$(azd env get-value AZURE_AI_MODEL_DEPLOYMENT_NAME)
PROJECT_ID=$(azd env get-value AZURE_AI_PROJECT_ID)
cd ..
```

If the endpoint or model deployment name changed, copy the new values into
`env/.env.dev` and your local `.user` files. Apply the cloud settings from the
repository root:

```bash
atk provision --env dev -i false
```

Restart the local debug workflow to regenerate its runtime settings. If the
Foundry project changed, repeat the developer and managed-identity role
assignments for the new `PROJECT_ID`, then verify personal and group chats.

### Publish the Teams app

To submit the app to your organization's app catalog:

```bash
atk publish --env dev -i false
```

An administrator must review and approve it in the Teams admin center.
Publishing the package does not deploy Python code or approve chat consent.
Before wider distribution, replace the demo developer/privacy/terms URLs in
[the manifest](../appPackage/manifest.json) with your organization's real
values and review data handling and operational requirements.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Provisioning fails on authorization | Verify both ATK and Azure CLI accounts, subscription/resource group values, resource creation permissions, and tenant app policies. Role assignments require separate authorization. |
| AZD uses the wrong platform environment | Run AZD inside `foundry-platform`, check `azd env list` and `azd env get-values`, and select the intended environment. Its local state is separate from the old root-level AZD state and from ATK. |
| Model deployment fails | Check regional model/version availability and Global Standard quota for capacity 10. Change the platform deployment parameters or region rather than guessing a model endpoint. |
| Foundry returns 401/403 | Check the project endpoint, Azure CLI login for local use, and project-scoped Foundry User assignment to the correct caller. In Azure the caller is the bot identity, not your developer user. Wait for propagation. |
| Foundry model not found | Match `AZURE_AI_MODEL_DEPLOYMENT_NAME` to the actual model deployment output. Do not enter a published agent name. |
| Personal chat works but group chat fails | Check that the app is installed and has `ChatMessage.Read.Chat` consent in that specific chat. Check logs for Graph errors and verify the package's bot identity matches the caller: the local bot app for local debugging, or the managed identity when deployed. |
| Local Teams Graph request returns 403 with `AzureCliCredential` | Restart the full local Teams debug workflow to regenerate `.env` with `GRAPH_AUTH_MODE=bot`. Graph must use the installed bot's application identity, not your Azure CLI user. Foundry can still use `AzureCliCredential`. If Graph still returns 403, verify local-app installation and consent in that chat. |
| Group chat produces no reply | Use a real Teams mention of this bot. Ordinary text containing its name is not a mention. Channel conversations are intentionally ignored. |
| Local Teams bot returns 401 | Check the generated `.env` client ID, secret, and tenant ID, then rerun the local Teams debug workflow. Do not reuse Playground's anonymous runtime settings. |
| Playground/local startup fails | Activate `.venv`, select it in VS Code, check Foundry settings, and free ports `3978` and `56150`. Stop the other debug workflow before retrying. |
| App Service fails to start | Inspect the ATK deployment/build output and App Service container logs. Check Python 3.11, dependency installation, and the Gunicorn startup command. A 404 at `/` is not a startup failure by itself. |
| Updated settings have no effect | Reprovision after editing cloud environment values; regenerate the root `.env` by restarting the appropriate local debug workflow for local changes. |
| Required setting missing or invalid | Set the project endpoint and model deployment name. `GRAPH_HISTORY_MESSAGE_COUNT` must be an integer from `1` to `50`. |

## Remove a test deployment

1. Remove the app from test chats and personal apps. Remove any organizational
   catalog entry through the Teams admin center if it was published.
2. Confirm that `$APP_RG` is the dedicated test group and inspect its contents:

   ```bash
   az resource list --resource-group "$APP_RG" -o table
   az group delete --name "$APP_RG"
   ```

3. Delete `$FOUNDRY_RG` **only if it was created for this test and no other app
   uses it**:

   ```bash
   cd foundry-platform
   azd env select "$FOUNDRY_ENV"
   azd env get-values
   azd down
   cd ..
   ```

   Check the selected AZD environment and resource group before confirming
   deletion. Do not run old root-level AZD cleanup for this separate project.

4. If you used local Teams debugging, remove its separate local Teams app and
   Entra app registration when no longer needed. Deleting Azure resource
   groups does not remove these registrations.

Deleting only the web app does not remove its paid App Service plan. Remove
the dedicated resources when finished to stop their ongoing charges.

## References

- [Microsoft 365 Agents Toolkit overview](https://learn.microsoft.com/en-us/microsoftteams/platform/toolkit/agents-toolkit-fundamentals)
- [ATK CLI reference](https://learn.microsoft.com/en-us/microsoftteams/platform/toolkit/microsoft-365-agents-toolkit-cli)
- [Microsoft 365 Agents SDK documentation](https://learn.microsoft.com/en-us/microsoft-365/agents-sdk/)
- [Azure Developer CLI environment management](https://learn.microsoft.com/en-us/azure/developer/azure-developer-cli/manage-environment-variables)
- [Foundry role-based access control](https://learn.microsoft.com/en-us/azure/foundry/concepts/rbac-foundry)
- [Grant Teams resource-specific consent](https://learn.microsoft.com/en-us/microsoftteams/platform/graph-api/rsc/grant-resource-specific-consent)
- [Teams bot design guidelines](https://learn.microsoft.com/en-us/microsoftteams/platform/bots/design/bots)

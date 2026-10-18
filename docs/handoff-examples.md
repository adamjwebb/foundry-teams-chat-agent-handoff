# Human handoff in Teams

Two examples extend the existing Python bot. Both use the same configured
human and a model-generated summary of the recent **personal bot conversation**.
Neither requires a roster, an existing ticket, or a different model.

The bot first previews the recipient and summary. Nothing is created or shared
with the human until the requesting user confirms in the original personal chat.
Use **Cancel** if the summary includes details you should not share; describe the
issue without those details and request a fresh summary. This demo does not
provide a summary editor. Model redaction is not a security guarantee.

## Configure one static human

Complete the original [development/deployment setup](development-and-deployment.md).
For local debugging, copy the handoff settings from the repository root:

```powershell
Copy-Item .env.handoff.example .env.handoff
```

Edit the ignored `.env.handoff` file:

```dotenv
HANDOFF_TENANT_ID=11111111-1111-4111-8111-111111111111
HANDOFF_HUMAN_UPN=helpdesk@example.test
HANDOFF_BROKER_ENABLED=false
HANDOFF_HUMAN_OBJECT_ID=33333333-3333-4333-8333-333333333333
HANDOFF_TEAMS_CATALOG_APP_ID=44444444-4444-4444-8444-444444444444
```

Every identity above is synthetic. Replace the tenant and human UPN with your
test tenant and helpdesk person. For the broker, also replace the human's Entra
object ID and Teams catalog app ID. **Verify the UPN and object ID identify the
same human** before enabling the broker. Do not use the target human account as
the requesting test account.

The optional file is loaded separately from the Toolkit-generated `.env`, so
running Toolkit again does not overwrite it. Existing process environment
settings take precedence. With no handoff settings, ordinary chat still works.
The deep-link example does not need the broker enabled or its object/catalog IDs.

For deployed testing, configure these five environment variables on the App
Service instead. Do not upload `.env.handoff`. The Bicep template exposes optional
parameters `handoffTenantId`, `handoffHumanUpn`, `handoffBrokerEnabled`,
`handoffHumanObjectId`, and `handoffTeamsCatalogAppId`, all disabled/empty by
default. If using Toolkit provisioning, opt in by adding the corresponding
parameter values to `infra/azure.parameters.json` before provisioning, for example:

```json
"handoffHumanUpn": { "value": "helpdesk@example.test" },
"handoffTenantId": { "value": "11111111-1111-4111-8111-111111111111" },
"handoffBrokerEnabled": { "value": "true" },
"handoffHumanObjectId": { "value": "33333333-3333-4333-8333-333333333333" },
"handoffTeamsCatalogAppId": { "value": "44444444-4444-4444-8444-444444444444" }
```

These are entries inside the existing `parameters` object, not a complete file.
Do not commit real tenant/person details to a shared fork. Managed-identity
deployments also need the registered Graph identity described below.

### Deployed Graph identity (required for RSC)

Teams RSC requires an **Entra app registration**, not just a managed identity's
service principal. Keep the managed identity as the Azure Bot and Foundry
identity, and create a separate single-tenant Entra app and service principal
for Graph. Do not create a client secret.

1. Register the Graph app in the same test tenant. Set `GRAPH_CLIENT_ID` in the
   Toolkit cloud environment to its application/client ID **before provisioning
   or building the package**. Local Teams provisioning automatically sets this
   value to the local bot's registered app ID.
2. Pass the registered client ID as `graphClientId` to either hosting template.
   The App Service Toolkit parameters already read `GRAPH_CLIENT_ID`. The
   template sets `GRAPH_AUTH_MODE=federated`, `GRAPH_CLIENT_ID`, and
   `GRAPH_TENANT_ID`; `AZURE_CLIENT_ID` remains the bot managed identity.
3. After provisioning the managed identity, add a federated identity credential
   to the registered Graph app. Its issuer is
   `https://login.microsoftonline.com/<tenant-id>/v2.0`, subject is the managed
   identity's **principal/object ID** (not client ID), and audience is
   `api://AzureADTokenExchange`. For example, create a private JSON file:

   ```json
   {
     "name": "handoff-managed-identity",
     "issuer": "https://login.microsoftonline.com/11111111-1111-4111-8111-111111111111/v2.0",
     "subject": "55555555-5555-4555-8555-555555555555",
     "audiences": ["api://AzureADTokenExchange"]
   }
   ```

   Replace both synthetic identities, then run
   `az ad app federated-credential create --id <registered-graph-client-id> --parameters <private-json-file>`.
4. Check the built manifest: `bots[0].botId` is the bot managed identity's client
   ID, while `webApplicationInfo.id` is the **registered Graph app's client ID**.
   Graph checks RSC and self-installation permissions against that registered
   identity. Each Teams app must have its own Entra application mapping.
5. For the broker, grant the two Graph application permissions listed below to
   the **registered Graph app's service principal** and obtain admin consent.
   Foundry User and AcrPull remain on the managed identity, not the Graph app.

At runtime, `ManagedIdentityCredential` obtains an assertion for the token
exchange audience and `ClientAssertionCredential` exchanges it for a Graph
token. It does not change incoming bot authentication or Foundry authentication.
Missing federation or permissions is an error, not a fallback to another user.
The original `default` and local `bot` Graph modes remain available, but an
MI-only manifest is not a substitute for a registered RSC app.

### Alternative hosting: Container Apps

If App Service is unavailable in your subscription, `infra/containerapps.bicep`
hosts the same bot using `Dockerfile` and managed identity. It creates a private
Basic Container Registry and a Consumption environment with exactly one warm
replica. There is no automatic scale-to-zero, horizontal scaling, or additional
state store. Registry storage and the warm replica incur charges.

Deploy that template first with `deployApp=false` and your normal project/model
and handoff parameters to create the registry, identity, and environment. Read
`REGISTRY_NAME` from the deployment outputs, then build from the repository root:

```powershell
az acr build --registry <registry-name> --image teams-handoff:handoff-demo .
```

Deploy the same template with the same parameters and `deployApp=true` to create
the bot and HTTPS container endpoint. If you change the image tag, pass the same
`imageTag` to the template and the registry build. Assign the runtime identity
Foundry User on your project; configure the registered Graph identity above,
then publish and
install the Teams package exactly as for App Service. `BOT_ID` and `BOT_DOMAIN`
are available as deployment outputs. Use the **catalog resource ID**, not
`BOT_ID`, for `handoffTeamsCatalogAppId`.

Set the final catalog ID and enable the broker **before** the walkthrough.
Changing a container image or environment variable starts a new process and
loses the same in-memory state as an App Service restart. Remove the bot from
previous support groups before replacing the process.

## Example 1: direct-human-chat deep link

1. Start **Debug in Teams** or install the deployed personal app.
2. Send a synthetic issue, such as `My test VPN connection fails after login`.
3. Exchange a few messages so the bot has context, then send `handoff link`.
4. Review the summary and human recipient, then select **Confirm handoff**.
5. Select **Chat with helpdesk**. Teams opens a human chat with a draft summary.
6. Review and **send the draft yourself**. Only then does the human receive it.

Teams can reuse an existing direct chat with that person. This is not automatic
delivery, an accepted helpdesk assignment, or a transfer of the original bot chat.
The bot does not send the human a separate notification.

The link's `message` query parameter contains the short summary. URL encoding is
not encryption: do not include passwords, tokens, sensitive incident details, or
full transcripts. Summaries are limited to 800 characters.

## Example 2: automated group-chat broker

### Additional prerequisites

- Publish the Teams app to your test tenant's app catalog and allow it under the
  tenant's Teams app policies. The upstream personal and `groupChat` scopes remain.
- Set `HANDOFF_TEAMS_CATALOG_APP_ID` to the catalog resource's `teamsApp.id`,
  not the manifest app ID, `BOT_ID`, or the publication submission ID. An admin can
  find it through the Graph app-catalog APIs. The published bot ID must match
  the running bot, and `webApplicationInfo.id` must match its Graph credential.
- Give the Graph-calling registered app these **application** permissions and
  obtain tenant admin consent:
  - `Chat.Create`
  - `TeamsAppInstallation.ReadWriteAndConsentSelfForChat.All`
- Allow the app's existing `ChatMessage.Read.Chat` application RSC permission
  under tenant policy. Installation explicitly consents that chat-scoped
  permission. Plain `TeamsAppInstallation.ReadWriteSelfForChat.All` is not enough
  for installing this app with its requested RSC consent.
- Local real-Teams debugging uses the **local bot app/service principal** for
  Graph (`GRAPH_AUTH_MODE=bot`). Assign its permissions in Entra; keep its secret
  in the existing ignored Toolkit user environment.
- Deployed testing uses the **registered Graph app's service principal**, reached
  through managed-identity federation. Assign Graph roles to that registration,
  not the bot managed identity or a different local application.
- Provide the normal bot messaging endpoint and a valid bot app ID (`BOT_ID` or
  the existing service-connection client ID) for proactive replies.
- Set `HANDOFF_BROKER_ENABLED=true` and restart before starting the walkthrough.

`Chat.Create` permits group creation, not ordinary app-only message sending as a
human. This example posts the summary **as the installed bot**, through the
Agents SDK/Bot Connector, using the actual installation/membership event context.
It does not request `Teamwork.Migrate.All` or tenant-wide chat-history access.

### Walkthrough

1. Start the configured bot and open its personal chat as a non-helpdesk user.
2. Describe a synthetic issue and send `handoff broker`.
3. Review the summary/recipient and select **Confirm handoff**.
4. The bot acknowledges that work is starting. That message is **not** delivery
   confirmation.
5. The broker creates a dedicated group containing the requesting user and the
   configured human, then installs the bot app and grants the matching RSC.
6. Teams sends an installation or membership event. The bot posts the approved
   summary, requesting identity, and handoff reference in that group.
7. After the bot send succeeds, the requester receives **summary delivered**
   and an **Open support chat** button in the original personal chat.
8. The human and requester chat directly in the group. The bot does not answer
   there, even if mentioned, while this process retains the group's tracking.

If the install event has not arrived, the bot reports **waiting** and says the
summary has not been sent. A group link can be provided for inspection without
claiming success. Acknowledging or opening the group is not human acceptance.
There is no availability check, acceptance queue, or automated follow-up.

## Failure and duplicate demonstrations

| Scenario | Expected behavior |
| --- | --- |
| Broker disabled | Explicit setup message; deep-link and ordinary chat remain available. |
| Wrong tenant, missing Entra user identity, or group-chat handoff command | Rejected; group context is not transferred. |
| Another user or chat submits the confirmation | Rejected; no broker operation. |
| User cancels | No sharing or group creation. |
| Confirm button clicked twice | Only the first valid confirmation can start work in the running process. |
| Graph grant missing or tenant installation policy denies app | Explicit failure stage; if a group already exists, it is identified as a partial result. |
| Teams event repeated | No second summary send in the running process. |
| Bot summary send fails or its outcome is unknown | Not reported as successful; no automatic send retry. Inspect the group and logs. |
| Graph timeout/5xx after an operation may have happened | Outcome recorded as uncertain; no automatic POST retry or substitute deep link. Inspect Teams before starting another request. |
| Bot restarts before confirmation | Old card is invalid; describe the issue/request a fresh handoff. |

After installation failure, the known group is retained in memory and duplicate
confirmation cannot create another group. This minimal example has no manual
retry/reconciliation command. Correct the setup and inspect/remove the partial
group before deliberately starting another handoff.

Logs include handoff references, stages, and state, not summary text or tokens.
If proactive notification back to the personal chat fails, inspect the logs and
the existing support group; no notification failure can prove delivery.

## In-memory limits

- No SQLite or external state service.
- Run **one application process and one instance**. The template explicitly uses
  one Gunicorn worker; do not scale the App Service horizontally for this demo.
- Summary input uses at most 20 recent app-observed personal messages and 8,000
  transcript characters. The bot does not retrieve the original personal chat
  from Graph. Personal context and unconfirmed requests expire after 24 hours
  of inactivity/age and are removed on subsequent store access.
- All state disappears on restart, including pending requests, in-flight progress,
  duplicate protection, and the group IDs that suppress AI responses. **After a
  restart, a previously created support group is treated as an ordinary group:
  mentioning the bot may generate a model response and use its consented history.**
  Finish the demo without restarting or remove the bot from old support groups
  before a restart. This is not production handoff isolation.
- Created Teams chats and messages remain until managed in Teams; stopping the
  Python process does not delete them. Memory for completed broker correlations
  is retained for the lifetime of the process.
- No restart recovery, distributed exactly-once delivery, full-transcript export,
  file transfer, roster, ticket creation, queueing, or SLA guarantees.

Agents Playground is useful for model experimentation, but does not establish
real Teams identities, create chats, grant RSC, or prove either live Teams flow.
Use a real test tenant for the walkthroughs.

## Verification status and sources

The automated suite covers state, bounds/expiry, identity-bound confirmation,
deep-link encoding, mocked Graph operations, installation-event ordering,
duplicate handling, partial/uncertain outcomes, support-group silence, and
SDK-shaped command integration:

```powershell
python -m pytest tests -q
```

Live deployment checks have verified Container Apps startup, rejection of
unauthenticated bot requests, managed-identity model inference, summary generation,
and correctly encoded deep-link parameters. Scoped app approval and publication
with a registered RSC app have now allowed personal installation in a fresh Teams
client, including an authenticated welcome message from the deployed bot.
**Neither complete live-Teams handoff walkthrough is confirmed yet.** Check the
version actually shown in the client: an older session continued to show the
previous package. Do not treat installation, backend inference, or a generated
URL as handoff proof.

- [Teams deep links and draft behavior](https://learn.microsoft.com/en-us/microsoftteams/platform/concepts/build-and-test/deep-link-teams)
- [Graph chat creation](https://learn.microsoft.com/en-us/graph/api/chat-post?view=graph-rest-1.0)
- [Install an app and grant its RSC](https://learn.microsoft.com/en-us/graph/api/chat-post-installedapps?view=graph-rest-1.0)
- [Proactive bot messaging prerequisites](https://learn.microsoft.com/en-us/microsoftteams/platform/bots/how-to/conversations/send-proactive-messages)
- [Register the app and grant Teams RSC](https://learn.microsoft.com/en-us/microsoftteams/platform/graph-api/rsc/grant-resource-specific-consent)
- [Configure an app to trust a managed identity](https://learn.microsoft.com/en-us/entra/workload-id/workload-identity-federation-config-app-trust-managed-identity)

The upstream revision this fork started from did not include a license file.
This fork does not resolve upstream licensing; confirm reuse rights with its
author before broader reuse or redistribution.

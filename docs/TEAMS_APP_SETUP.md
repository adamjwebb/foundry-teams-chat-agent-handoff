# Install the Foundry Teams Chat Agent

After deployment, the post-deploy hook creates
`foundry-teams-chat-agent-poc.zip` in the repository root. Upload this generated
package; do not build a separate manifest by hand because the package is bound
to the deployed bot identity and includes the chat-scoped permission declaration.
The generated app description discloses that the agent sends a bounded window
of recent messages from a consenting group chat to the configured Foundry model
and does not persist that history.

## Recover from a missing Teams app version

If deployment finishes but the post-deploy hook reports that `TEAMS_APP_VERSION`
is missing, set the version in the active AZD environment and rerun only the hook:

```bash
azd env set TEAMS_APP_VERSION "1.0.0"
azd hooks run postdeploy
```

Use `1.0.0` for a first package. For an existing app, use its current version
from `appPackage/manifest.json`, or a higher version when updating the installed
Teams app. The version must be numeric `major.minor.patch`. This setting is
package metadata, not an infrastructure output; provisioning does not set it.
There is no need to reprovision or redeploy the successfully deployed agent.

## Add the app to Teams

1. In Teams, open **Apps** -> **Manage your apps** -> **Upload an app**.
2. Choose **Upload a custom app**, select `foundry-teams-chat-agent-poc.zip`,
   then add the app.
3. Add it to a test group chat and grant its request to read that chat's messages.
   Installing the app personally or uploading it does not grant group-chat history
   access.
4. Send a context message, mention the agent, and ask a question about it.

The app reads a bounded window of recent messages only from the group chat where
it is installed and consented. It does not read team/channel messages or files.
Unmentioned group-chat messages do not trigger a reply.

For a personal-only installation, no group-chat consent is required and the
agent does not call Microsoft Graph for chat history. With the configured
`BotServiceRbac` authorization, every person who invokes the agent must also
have **Foundry Agent Consumer** at the agent or project scope.

If **Upload a custom app** is unavailable, custom app upload is disabled by
tenant policy; ask a Teams administrator about enabling it. See the
[Teams custom-app upload guide](https://learn.microsoft.com/microsoftteams/platform/concepts/deploy-and-publish/apps-upload),
or publish through Foundry as described below.

## Publish through Foundry instead of uploading

When custom app upload is blocked, the post-deploy hook can also publish the
agent to Microsoft 365 with `azd ai agent publish`, the CLI equivalent of the
Foundry portal **Publish** action. It uses the `shared` scope, which requires no
tenant-admin approval:

```bash
azd env set TEAMS_PUBLISH_TO_M365 true
azd hooks run postdeploy   # or run azd deploy
```

The agent then appears for you in the Teams and Microsoft 365 Copilot agent
store; add it from there instead of uploading the zip. Teams members can call it
in a chat only if they have Foundry permissions on the project. Increase
`TEAMS_APP_VERSION` before republishing a change.

Foundry generates the manifest for this path, so the app does not include the
`ChatMessage.Read.Chat` group-chat permission from
`foundry-teams-chat-agent-poc.zip`. Personal and mentioned replies work, but
group-chat history is unavailable until the custom package is uploaded or
approved by an administrator. Tenant app policies can still hide published
agents.

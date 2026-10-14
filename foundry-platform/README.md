# Shared Foundry Platform

This component owns the shared Microsoft Foundry account, project, and model
deployment used by the Teams agent. It is intentionally separate from the
Microsoft 365 Agents Toolkit lifecycle at the repository root.

The existing default project and environment names in
`infra/main.bicep` are retained to avoid replacing previously provisioned
resources. Override them when creating a distinct shared environment.

Deploy the platform independently:

```bash
az deployment group create \
  --resource-group <resource-group> \
  --template-file foundry-platform/infra/main.bicep \
  --parameters environmentName=<environment-name>
```

Use the deployment outputs `AZURE_AI_PROJECT_ENDPOINT` and
`AZURE_AI_MODEL_DEPLOYMENT_NAME` in the agent environment files. Grant the
agent's user-assigned managed identity the least-privileged role needed to call
the shared Foundry project.

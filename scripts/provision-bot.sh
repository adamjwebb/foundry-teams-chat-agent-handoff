#!/usr/bin/env sh
set -eu

root_dir="$(cd "$(dirname "$0")/.." && pwd)"

: "${AZURE_SUBSCRIPTION_ID:?Required AZD environment value is missing}"
: "${AZURE_RESOURCE_GROUP:?Required AZD environment value is missing}"
: "${AGENT_FOUNDRY_TEAMS_CHAT_AGENT_BOT_NAME:?Required AZD environment value is missing}"
if [ -z "${TEAMS_APP_VERSION:-}" ]; then
  echo 'TEAMS_APP_VERSION is required. Set it with: azd env set TEAMS_APP_VERSION "<major.minor.patch>"' >&2
  echo 'Then retry packaging without redeploying: azd hooks run postdeploy' >&2
  exit 1
fi

valid_version=true
case "$TEAMS_APP_VERSION" in
  *[!0-9.]*) valid_version=false ;;
esac
if [ "$valid_version" != "true" ] ||
  ! printf '%s\n' "$TEAMS_APP_VERSION" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+$'; then
  echo "TEAMS_APP_VERSION must use numeric major.minor.patch format" >&2
  exit 1
fi

publish="${TEAMS_PUBLISH_TO_M365:-false}"
case "$publish" in
  true | false) ;;
  *)
    echo "TEAMS_PUBLISH_TO_M365 must be 'true' or 'false'" >&2
    exit 1
    ;;
esac

bot_name="$AGENT_FOUNDRY_TEAMS_CHAT_AGENT_BOT_NAME"
dry_run="${BOT_PROVISION_DRY_RUN:-false}"

if [ "$dry_run" = "true" ]; then
  echo "Dry run: would resolve Azure Bot '$bot_name' in '$AZURE_RESOURCE_GROUP'"
  echo "Dry run: would pack version '$TEAMS_APP_VERSION' and add group-chat RSC to foundry-teams-chat-agent-poc.zip"
  if [ "$publish" = "true" ]; then
    echo "Dry run: would publish version '$TEAMS_APP_VERSION' to Microsoft 365 with shared scope"
  fi
  exit 0
fi

client_id="$(az bot show \
  --subscription "$AZURE_SUBSCRIPTION_ID" \
  --resource-group "$AZURE_RESOURCE_GROUP" \
  --name "$bot_name" \
  --query properties.msaAppId \
  --output tsv)"
if [ -z "$client_id" ]; then
  echo "Azure Bot '$bot_name' has no Microsoft application ID" >&2
  exit 1
fi
azd -C "$root_dir" env set AZURE_BOT_NAME "$bot_name"
azd -C "$root_dir" env set AZURE_BOT_APP_ID "$client_id"

packed_path="$root_dir/appPackage/appPackage.zip"
azd -C "$root_dir" ai agent pack foundry-teams-chat-agent \
  --scope shared \
  --display-name "Foundry Teams Chat Agent POC" \
  --app-version "$TEAMS_APP_VERSION" \
  --output-dir "$root_dir/appPackage"
python3 "$root_dir/scripts/build-teams-package.py" \
  "$packed_path" \
  "$root_dir/foundry-teams-chat-agent-poc.zip" \
  "$root_dir/appPackage/manifest.json" \
  "$client_id"
rm "$packed_path"

echo "Using hosted-agent Azure Bot '$bot_name'"
echo "Generated Microsoft 365-bound Teams package with Agent Identity $client_id"

if [ "$publish" = "true" ]; then
  # Foundry-generated manifest: no sideloading, but no group-chat RSC either.
  azd -C "$root_dir" ai agent publish foundry-teams-chat-agent \
    --scope shared \
    --display-name "Foundry Teams Chat Agent POC" \
    --app-version "$TEAMS_APP_VERSION" \
    --no-prompt
  echo "Published version $TEAMS_APP_VERSION to Microsoft 365 with shared scope"
fi

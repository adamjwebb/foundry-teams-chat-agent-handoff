$ErrorActionPreference = "Stop"
$rootDir = Split-Path -Parent $PSScriptRoot

$requiredVariables = @(
    "AZURE_SUBSCRIPTION_ID",
    "AZURE_RESOURCE_GROUP",
    "AGENT_FOUNDRY_TEAMS_CHAT_AGENT_BOT_NAME"
)

foreach ($variable in $requiredVariables) {
    if ([string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable($variable))) {
        throw "Required AZD environment value is missing: $variable"
    }
}

if ([string]::IsNullOrWhiteSpace($env:TEAMS_APP_VERSION)) {
    [Console]::Error.WriteLine('Set the version with: azd env set TEAMS_APP_VERSION "<major.minor.patch>"')
    [Console]::Error.WriteLine('Then retry packaging without redeploying: azd hooks run postdeploy')
    throw "TEAMS_APP_VERSION is required."
}

if ($env:TEAMS_APP_VERSION -notmatch '\A[0-9]+\.[0-9]+\.[0-9]+\z') {
    throw "TEAMS_APP_VERSION must use numeric major.minor.patch format"
}

$publishSetting = if ([string]::IsNullOrEmpty($env:TEAMS_PUBLISH_TO_M365)) { "false" } else { $env:TEAMS_PUBLISH_TO_M365 }
if ($publishSetting -cnotin @("true", "false")) {
    [Console]::Error.WriteLine("TEAMS_PUBLISH_TO_M365 must be 'true' or 'false'")
    exit 1
}
$publish = $publishSetting -ceq "true"

$botName = $env:AGENT_FOUNDRY_TEAMS_CHAT_AGENT_BOT_NAME
$dryRun = $env:BOT_PROVISION_DRY_RUN -eq "true"

if ($dryRun) {
    Write-Host "Dry run: would resolve Azure Bot '$botName' in '$env:AZURE_RESOURCE_GROUP'"
    Write-Host "Dry run: would pack version '$env:TEAMS_APP_VERSION' and add group-chat RSC to foundry-teams-chat-agent-poc.zip"
    if ($publish) {
        Write-Host "Dry run: would publish version '$env:TEAMS_APP_VERSION' to Microsoft 365 with shared scope"
    }
    exit 0
}

$clientId = az bot show `
    --subscription $env:AZURE_SUBSCRIPTION_ID `
    --resource-group $env:AZURE_RESOURCE_GROUP `
    --name $botName `
    --query properties.msaAppId `
    --output tsv
if ($LASTEXITCODE -ne 0) {
    throw "Azure Bot identity lookup failed"
}
if ([string]::IsNullOrWhiteSpace($clientId)) {
    throw "Azure Bot '$botName' has no Microsoft application ID"
}
azd -C $rootDir env set AZURE_BOT_NAME $botName
if ($LASTEXITCODE -ne 0) {
    throw "Saving Azure Bot name failed"
}
azd -C $rootDir env set AZURE_BOT_APP_ID $clientId
if ($LASTEXITCODE -ne 0) {
    throw "Saving Azure Bot application ID failed"
}

$packedPath = Join-Path $rootDir "appPackage/appPackage.zip"
azd -C $rootDir ai agent pack foundry-teams-chat-agent `
    --scope shared `
    --display-name "Foundry Teams Chat Agent POC" `
    --app-version $env:TEAMS_APP_VERSION `
    --output-dir (Join-Path $rootDir "appPackage")
if ($LASTEXITCODE -ne 0) {
    throw "Microsoft 365 Teams package generation failed"
}

python (Join-Path $rootDir "scripts/build-teams-package.py") `
    $packedPath `
    (Join-Path $rootDir "foundry-teams-chat-agent-poc.zip") `
    (Join-Path $rootDir "appPackage/manifest.json") `
    $clientId
if ($LASTEXITCODE -ne 0) {
    throw "Teams RSC package generation failed"
}
Remove-Item $packedPath
Remove-Item `
    (Join-Path $rootDir "src/foundry-teams-chat-agent/TEAMS_APP_SETUP.md") `
    -Force `
    -ErrorAction SilentlyContinue

Write-Host "Using hosted-agent Azure Bot '$botName'"
Write-Host "Generated Microsoft 365-bound Teams package with Agent Identity $clientId"

if ($publish) {
    # Foundry-generated manifest: no sideloading, but no group-chat RSC either.
    azd -C $rootDir ai agent publish foundry-teams-chat-agent `
        --scope shared `
        --display-name "Foundry Teams Chat Agent POC" `
        --app-version $env:TEAMS_APP_VERSION `
        --no-prompt
    if ($LASTEXITCODE -ne 0) {
        throw "Microsoft 365 publish failed"
    }
    Write-Host "Published version $env:TEAMS_APP_VERSION to Microsoft 365 with shared scope"
}

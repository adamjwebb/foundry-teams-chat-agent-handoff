"""Add the POC's chat-scoped RSC permission to an AZD-generated Teams package."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.parse import urlparse
from zipfile import ZIP_DEFLATED, ZipFile

# Teams ignores the RSC resource value but requires one; use Microsoft's documented value.
RSC_RESOURCE = "https://RscBasedStoreApp"
RSC_PERMISSIONS = [{"name": "ChatMessage.Read.Chat", "type": "Application"}]
BOT_SCOPES = ["personal", "groupChat"]
PLACEHOLDER_HOSTS = {"example", "example.com", "example.org", "example.net"}
APP_DESCRIPTION = {
    "short": "Answers questions using messages from a consenting Teams group chat.",
    "full": (
        "Answers questions in personal chats and consenting Teams group chats. "
        "In a group chat, the agent reads a bounded window of recent messages "
        "only when it is mentioned, sends that context to the configured "
        "Microsoft Foundry model, and does not persist the chat history."
    ),
}


def is_placeholder_resource(resource: str) -> bool:
    parsed = urlparse(resource.strip())
    host = (parsed.hostname or parsed.path).lower().rstrip("/")
    return host in PLACEHOLDER_HOSTS or host.endswith(
        tuple(f".{name}" for name in PLACEHOLDER_HOSTS)
    )


def validate_manifest(manifest: dict, agent_app_id: str) -> None:
    errors = []
    bots = manifest.get("bots") or []
    if [bot.get("botId") for bot in bots] != [agent_app_id]:
        errors.append("bots must contain exactly the hosted Agent Identity")
    for bot in bots:
        if bot.get("scopes") != BOT_SCOPES:
            errors.append(f"bot scopes must be {BOT_SCOPES}")
    web_app = manifest.get("webApplicationInfo") or {}
    if web_app.get("id") != agent_app_id:
        errors.append("webApplicationInfo.id must be the hosted Agent Identity")
    resource = (web_app.get("resource") or "").strip()
    if not resource or is_placeholder_resource(resource):
        errors.append(f"webApplicationInfo.resource is missing or a placeholder: {resource!r}")
    permissions = (manifest.get("authorization") or {}).get("permissions") or {}
    if permissions.get("resourceSpecific") != RSC_PERMISSIONS:
        errors.append("authorization must request only ChatMessage.Read.Chat RSC")
    if manifest.get("description") != APP_DESCRIPTION:
        errors.append("description must disclose how group-chat messages are used")
    if errors:
        raise RuntimeError("Invalid Teams manifest: " + "; ".join(errors))


def build_package(
    source_path: Path,
    output_path: Path,
    manifest_output_path: Path,
    agent_app_id: str,
) -> None:
    with ZipFile(source_path) as source:
        files = {name: source.read(name) for name in source.namelist()}

    manifest = json.loads(files["manifest.json"])
    bot_ids = {bot["botId"] for bot in manifest.get("bots", [])}
    web_app_id = (manifest.get("webApplicationInfo") or {}).get("id")
    if bot_ids != {agent_app_id} or web_app_id != agent_app_id:
        raise RuntimeError(
            "Microsoft 365 package identity does not match the hosted Agent Identity"
        )

    # Preserve the Microsoft 365 title ID and identity binding, but advertise
    # only the capabilities implemented by this POC.
    for bot in manifest["bots"]:
        bot["scopes"] = list(BOT_SCOPES)
        bot["supportsFiles"] = False
        bot["isNotificationOnly"] = False
    manifest.pop("copilotAgents", None)
    manifest.pop("supportsChannelFeatures", None)
    manifest["description"] = dict(APP_DESCRIPTION)
    web_app = manifest["webApplicationInfo"]
    resource = (web_app.get("resource") or "").strip()
    if not resource:
        raise RuntimeError("Microsoft 365 package has no webApplicationInfo.resource")
    if is_placeholder_resource(resource):
        web_app["resource"] = RSC_RESOURCE
    permissions = manifest.setdefault("authorization", {}).setdefault("permissions", {})
    permissions["resourceSpecific"] = [dict(item) for item in RSC_PERMISSIONS]
    validate_manifest(manifest, agent_app_id)
    manifest_bytes = (json.dumps(manifest, indent=2) + "\n").encode()
    files["manifest.json"] = manifest_bytes

    with ZipFile(output_path, "w", ZIP_DEFLATED) as output:
        for name, content in files.items():
            output.writestr(name, content)

    manifest_output_path.write_bytes(manifest_bytes)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_path", type=Path)
    parser.add_argument("output_path", type=Path)
    parser.add_argument("manifest_output_path", type=Path)
    parser.add_argument("agent_app_id")
    args = parser.parse_args()
    build_package(
        args.source_path,
        args.output_path,
        args.manifest_output_path,
        args.agent_app_id,
    )


if __name__ == "__main__":
    main()

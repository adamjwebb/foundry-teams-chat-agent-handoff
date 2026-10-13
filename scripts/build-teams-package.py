"""Add the POC's chat-scoped RSC permission to an AZD-generated Teams package."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


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
        bot["scopes"] = ["personal", "groupChat"]
        bot["supportsFiles"] = False
        bot["isNotificationOnly"] = False
    manifest.pop("copilotAgents", None)
    manifest.pop("supportsChannelFeatures", None)
    if not (manifest["webApplicationInfo"].get("resource") or "").strip():
        raise RuntimeError("Microsoft 365 package has no webApplicationInfo.resource")
    permissions = manifest.setdefault("authorization", {}).setdefault("permissions", {})
    permissions["resourceSpecific"] = [
        {"name": "ChatMessage.Read.Chat", "type": "Application"}
    ]
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

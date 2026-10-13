"""Offline checks for the Microsoft 365 package and post-deploy hooks."""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from zipfile import ZipFile

import pytest

ROOT = Path(__file__).resolve().parents[3]
APP_ID = "00000000-0000-0000-0000-000000000001"
TITLE_ID = "00000000-0000-0000-0000-000000000002"
spec = importlib.util.spec_from_file_location(
    "build_teams_package", ROOT / "scripts/build-teams-package.py"
)
assert spec and spec.loader
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


def write_source(
    tmp_path,
    *,
    bot_id=APP_ID,
    web_app_id=APP_ID,
    resource="api://example",
    version="1.0.0",
):
    manifest = {
        "id": TITLE_ID,
        "version": version,
        "description": {
            "short": "Generated description",
            "full": "Generated description",
        },
        "icons": {"color": "color.png", "outline": "outline.png"},
        "bots": [
            {
                "botId": bot_id,
                "scopes": ["personal", "team", "groupChat", "copilot"],
                "supportsFiles": True,
            }
        ],
        "webApplicationInfo": {"id": web_app_id, "resource": resource},
        "copilotAgents": {"customEngineAgents": [{"id": bot_id, "type": "bot"}]},
        "supportsChannelFeatures": True,
        "authorization": {
            "permissions": {
                "resourceSpecific": [{"name": "Chat.ReadWrite.Chat", "type": "Application"}]
            }
        },
    }
    source = tmp_path / "source.zip"
    with ZipFile(source, "w") as package:
        package.writestr("manifest.json", json.dumps(manifest))
        package.writestr("color.png", b"original-color")
        package.writestr("outline.png", b"original-outline")
    return source


@pytest.mark.parametrize("version", ["1.0.0", "1.2.3", "12.34.56"])
def test_package_preserves_identity_binding_and_limits_capabilities(tmp_path, version):
    source = write_source(tmp_path, version=version)
    output = tmp_path / "output.zip"
    manifest_path = tmp_path / "manifest.json"

    builder.build_package(source, output, manifest_path, APP_ID)

    with ZipFile(output) as package:
        manifest_bytes = package.read("manifest.json")
        assert manifest_path.read_bytes() == manifest_bytes
        assert package.read("color.png") == b"original-color"
        assert package.read("outline.png") == b"original-outline"
    manifest = json.loads(manifest_bytes)
    assert manifest["id"] == TITLE_ID
    assert manifest["version"] == version
    assert manifest["bots"] == [
        {
            "botId": APP_ID,
            "scopes": ["personal", "groupChat"],
            "supportsFiles": False,
            "isNotificationOnly": False,
        }
    ]
    assert manifest["webApplicationInfo"] == {
        "id": APP_ID,
        "resource": "https://RscBasedStoreApp",
    }
    assert manifest["authorization"]["permissions"]["resourceSpecific"] == [
        {"name": "ChatMessage.Read.Chat", "type": "Application"}
    ]
    assert manifest["description"] == builder.APP_DESCRIPTION
    assert "copilotAgents" not in manifest
    assert "supportsChannelFeatures" not in manifest


@pytest.mark.parametrize(
    "resource",
    [
        "api://example",
        "api://example.com",
        "https://example.com/",
        "api://app.example.org",
        "  https://EXAMPLE.NET  ",
    ],
)
def test_package_replaces_placeholder_rsc_resource(tmp_path, resource):
    source = write_source(tmp_path, resource=resource)
    manifest_path = tmp_path / "manifest.json"

    builder.build_package(source, tmp_path / "output.zip", manifest_path, APP_ID)

    manifest = json.loads(manifest_path.read_text())
    assert manifest["webApplicationInfo"]["resource"] == "https://RscBasedStoreApp"


@pytest.mark.parametrize(
    "resource", ["https://RscBasedStoreApp", "api://contoso.com/teams-agent"]
)
def test_package_preserves_real_rsc_resource(tmp_path, resource):
    source = write_source(tmp_path, resource=resource)
    manifest_path = tmp_path / "manifest.json"

    builder.build_package(source, tmp_path / "output.zip", manifest_path, APP_ID)

    manifest = json.loads(manifest_path.read_text())
    assert manifest["webApplicationInfo"]["resource"] == resource


def valid_manifest():
    return {
        "description": dict(builder.APP_DESCRIPTION),
        "bots": [{"botId": APP_ID, "scopes": ["personal", "groupChat"]}],
        "webApplicationInfo": {"id": APP_ID, "resource": "https://RscBasedStoreApp"},
        "authorization": {
            "permissions": {
                "resourceSpecific": [
                    {"name": "ChatMessage.Read.Chat", "type": "Application"}
                ]
            }
        },
    }


def test_validate_manifest_accepts_built_shape():
    builder.validate_manifest(valid_manifest(), APP_ID)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda m: m["bots"].append({"botId": TITLE_ID, "scopes": []}), "bots"),
        (lambda m: m["bots"][0].update(scopes=["personal"]), "scopes"),
        (lambda m: m["webApplicationInfo"].update(id=TITLE_ID), "webApplicationInfo.id"),
        (
            lambda m: m["webApplicationInfo"].update(resource="api://example.com"),
            "placeholder",
        ),
        (lambda m: m["webApplicationInfo"].update(resource=" "), "placeholder"),
        (lambda m: m.pop("authorization"), "ChatMessage.Read.Chat"),
        (
            lambda m: m["authorization"]["permissions"]["resourceSpecific"].append(
                {"name": "Chat.ReadWrite.Chat", "type": "Application"}
            ),
            "ChatMessage.Read.Chat",
        ),
        (
            lambda m: m.update(
                description={"short": "Generic agent", "full": "Generic agent"}
            ),
            "description",
        ),
    ],
)
def test_validate_manifest_rejects_invalid_packages(mutate, message):
    manifest = valid_manifest()
    mutate(manifest)
    with pytest.raises(RuntimeError, match=message):
        builder.validate_manifest(manifest, APP_ID)


def test_teams_version_is_not_an_unexpanded_publish_placeholder():
    assert "${TEAMS_APP_VERSION}" not in (
        ROOT / "azure.yaml"
    ).read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "identity", [{"bot_id": TITLE_ID}, {"web_app_id": TITLE_ID}]
)
def test_package_rejects_identity_mismatch_before_writing(tmp_path, identity):
    source = write_source(tmp_path, **identity)
    output = tmp_path / "output.zip"
    manifest_path = tmp_path / "manifest.json"

    with pytest.raises(RuntimeError, match="identity does not match"):
        builder.build_package(source, output, manifest_path, APP_ID)

    assert not output.exists()
    assert not manifest_path.exists()


def test_package_requires_nonempty_rsc_resource(tmp_path):
    source = write_source(tmp_path, resource="")
    with pytest.raises(RuntimeError, match="webApplicationInfo.resource"):
        builder.build_package(
            source, tmp_path / "output.zip", tmp_path / "manifest.json", APP_ID
        )


@pytest.mark.parametrize("shell", ["sh", "pwsh"])
def test_hook_dry_run_is_read_only(tmp_path, shell):
    executable = shutil.which(shell)
    if not executable:
        pytest.skip(f"{shell} is not installed")
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    suffix = "ps1" if shell == "pwsh" else "sh"
    hook = scripts / f"provision-bot.{suffix}"
    shutil.copyfile(ROOT / "scripts" / hook.name, hook)
    environment = os.environ | {
        "BOT_PROVISION_DRY_RUN": "true",
        "AZURE_SUBSCRIPTION_ID": APP_ID,
        "AZURE_RESOURCE_GROUP": "demo-rg",
        "AGENT_FOUNDRY_TEAMS_CHAT_AGENT_BOT_NAME": "demo-bot",
        "TEAMS_APP_VERSION": "1.0.1",
    }
    command = (
        [executable, "-NoProfile", "-File", str(hook)]
        if shell == "pwsh"
        else [executable, str(hook)]
    )
    result = subprocess.run(
        command, env=environment, text=True, capture_output=True, check=True
    )

    assert "Dry run:" in result.stdout
    assert sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*")) == [
        "scripts",
        f"scripts/{hook.name}",
    ]


@pytest.mark.parametrize("shell", ["sh", "pwsh"])
@pytest.mark.parametrize("version", ["1.0.1", "12.34.56"])
@pytest.mark.parametrize("publish", [None, "false", "true"])
def test_hook_passes_numeric_version_to_pack_and_preserves_it(
    tmp_path, shell, version, publish
):
    executable = shutil.which(shell)
    if not executable:
        pytest.skip(f"{shell} is not installed")
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    suffix = "ps1" if shell == "pwsh" else "sh"
    hook = scripts / f"provision-bot.{suffix}"
    shutil.copyfile(ROOT / "scripts" / hook.name, hook)
    shutil.copyfile(
        ROOT / "scripts/build-teams-package.py", scripts / "build-teams-package.py"
    )
    (tmp_path / "appPackage").mkdir()
    source = write_source(tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("az", "azd"):
        program = bin_dir / name
        program.write_text(
            f"#!{sys.executable}\n"
            "import json, os, sys\n"
            "from pathlib import Path\n"
            "from zipfile import ZipFile\n"
            "if Path(sys.argv[0]).name == 'az':\n"
            "    print(os.environ['TEST_APP_ID'])\n"
            "    sys.exit(0)\n"
            "with open(os.environ['TEST_AZD_LOG'], 'a') as log:\n"
            "    log.write(json.dumps(sys.argv[1:]) + '\\n')\n"
            "if 'pack' in sys.argv:\n"
            "    version = sys.argv[sys.argv.index('--app-version') + 1]\n"
            "    output = Path(sys.argv[sys.argv.index('--output-dir') + 1])\n"
            "    (output / 'pack-request.json').write_text(json.dumps({'version': version}))\n"
            "    with ZipFile(os.environ['TEST_PACKAGE_SOURCE']) as source:\n"
            "        files = {name: source.read(name) for name in source.namelist()}\n"
            "    manifest = json.loads(files['manifest.json'])\n"
            "    manifest['version'] = version\n"
            "    files['manifest.json'] = json.dumps(manifest).encode()\n"
            "    with ZipFile(output / 'appPackage.zip', 'w') as package:\n"
            "        for name, data in files.items():\n"
            "            package.writestr(name, data)\n",
            encoding="utf-8",
        )
        program.chmod(0o755)
    for name in ("python", "python3"):
        (bin_dir / name).symlink_to(sys.executable)
    environment = os.environ | {
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "BOT_PROVISION_DRY_RUN": "false",
        "AZURE_SUBSCRIPTION_ID": APP_ID,
        "AZURE_RESOURCE_GROUP": "demo-rg",
        "AGENT_FOUNDRY_TEAMS_CHAT_AGENT_BOT_NAME": "demo-bot",
        "TEAMS_APP_VERSION": version,
        "TEST_PACKAGE_SOURCE": str(source),
        "TEST_APP_ID": APP_ID,
        "TEST_AZD_LOG": str(tmp_path / "azd-calls.jsonl"),
    }
    environment.pop("TEAMS_PUBLISH_TO_M365", None)
    if publish is not None:
        environment["TEAMS_PUBLISH_TO_M365"] = publish
    command = (
        [executable, "-NoProfile", "-File", str(hook)]
        if shell == "pwsh"
        else [executable, str(hook)]
    )
    subprocess.run(
        command, env=environment, text=True, capture_output=True, check=True
    )

    calls = [
        json.loads(line)
        for line in (tmp_path / "azd-calls.jsonl").read_text().splitlines()
    ]
    publish_calls = [call for call in calls if "publish" in call]
    if publish == "true":
        assert publish_calls == [
            [
                "-C", str(tmp_path), "ai", "agent", "publish",
                "foundry-teams-chat-agent",
                "--scope", "shared",
                "--display-name", "Foundry Teams Chat Agent POC",
                "--app-version", version,
                "--no-prompt",
            ]
        ]
        assert calls[-1] == publish_calls[0]
    else:
        assert publish_calls == []

    assert json.loads((tmp_path / "appPackage/pack-request.json").read_text()) == {
        "version": version
    }
    with ZipFile(tmp_path / "foundry-teams-chat-agent-poc.zip") as package:
        manifest = json.loads(package.read("manifest.json"))
    assert manifest["version"] == version
    assert manifest["id"] == TITLE_ID
    assert manifest == json.loads((tmp_path / "appPackage/manifest.json").read_text())


@pytest.mark.parametrize("shell", ["sh", "pwsh"])
@pytest.mark.parametrize(
    "version",
    [None, "", "${TEAMS_APP_VERSION}", "v1.0.1", "1.0.1-beta", "1.0", "1.0.1\n"],
)
def test_hook_rejects_missing_or_invalid_version_before_pack(shell, version):
    executable = shutil.which(shell)
    if not executable:
        pytest.skip(f"{shell} is not installed")
    suffix = "ps1" if shell == "pwsh" else "sh"
    hook = ROOT / "scripts" / f"provision-bot.{suffix}"
    environment = os.environ | {
        "BOT_PROVISION_DRY_RUN": "true",
        "AZURE_SUBSCRIPTION_ID": APP_ID,
        "AZURE_RESOURCE_GROUP": "demo-rg",
        "AGENT_FOUNDRY_TEAMS_CHAT_AGENT_BOT_NAME": "demo-bot",
    }
    environment.pop("TEAMS_APP_VERSION", None)
    if version is not None:
        environment["TEAMS_APP_VERSION"] = version
    command = (
        [executable, "-NoProfile", "-File", str(hook)]
        if shell == "pwsh"
        else [executable, str(hook)]
    )
    result = subprocess.run(
        command, env=environment, text=True, capture_output=True, check=False
    )

    assert result.returncode != 0
    assert "TEAMS_APP_VERSION" in result.stderr
    assert "Dry run:" not in result.stdout
    if version in (None, ""):
        assert 'azd env set TEAMS_APP_VERSION "<major.minor.patch>"' in result.stderr
        assert "azd hooks run postdeploy" in result.stderr


@pytest.mark.parametrize("shell", ["sh", "pwsh"])
@pytest.mark.parametrize("value", ["yes", "TRUE", "1"])
def test_hook_rejects_invalid_publish_setting(shell, value):
    executable = shutil.which(shell)
    if not executable:
        pytest.skip(f"{shell} is not installed")
    suffix = "ps1" if shell == "pwsh" else "sh"
    hook = ROOT / "scripts" / f"provision-bot.{suffix}"
    environment = os.environ | {
        "BOT_PROVISION_DRY_RUN": "true",
        "AZURE_SUBSCRIPTION_ID": APP_ID,
        "AZURE_RESOURCE_GROUP": "demo-rg",
        "AGENT_FOUNDRY_TEAMS_CHAT_AGENT_BOT_NAME": "demo-bot",
        "TEAMS_APP_VERSION": "1.0.1",
        "TEAMS_PUBLISH_TO_M365": value,
    }
    command = (
        [executable, "-NoProfile", "-File", str(hook)]
        if shell == "pwsh"
        else [executable, str(hook)]
    )
    result = subprocess.run(
        command, env=environment, text=True, capture_output=True, check=False
    )

    assert result.returncode != 0
    assert "TEAMS_PUBLISH_TO_M365" in result.stderr
    assert "Dry run:" not in result.stdout

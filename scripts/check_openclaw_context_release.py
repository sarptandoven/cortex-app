#!/usr/bin/env python3
"""Release gate for the packaged Cortex OpenClaw contextEngine adapter.

The gate never touches the user's real OpenClaw state. It packs the npm artifact, installs and
reinstalls it into a temporary HOME/profile, loads it through the pinned OpenClaw compatibility
runtime, probes the installed artifact in live and signed-bundle modes, then boots and
health-checks an isolated loopback Gateway.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "packages" / "openclaw-cortex-context"
OPENCLAW_CLI = PACKAGE / "node_modules" / "openclaw" / "openclaw.mjs"
BUNDLE = ROOT / "spec" / "portable-memory" / "v2" / "test-vectors" / "cortex-python-n3.json"
ARTIFACT_PROBE = ROOT / "scripts" / "openclaw_context_artifact_probe.mjs"
MIN_NODE = (22, 19, 0)
PROFILE = "cortexgate"
PLUGIN_ID = "cortex-context"


def _run(
    command: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    timeout: int = 120,
    capture: bool = True,
    report_failure: bool = True,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            cwd=cwd,
            env=env,
            check=True,
            text=True,
            capture_output=capture,
            timeout=timeout,
        )
    except subprocess.CalledProcessError as exc:
        if report_failure:
            if exc.stdout:
                sys.stderr.write(exc.stdout)
            if exc.stderr:
                sys.stderr.write(exc.stderr)
        raise


def _node_version(node: str) -> tuple[int, int, int]:
    raw = _run([node, "--version"]).stdout.strip().lstrip("v")
    parts = raw.split(".")
    return tuple(int(parts[index]) if index < len(parts) else 0 for index in range(3))


def _resolve_node(explicit: str | None, allow_download: bool) -> tuple[str, bool]:
    candidates = [explicit, os.environ.get("OPENCLAW_NODE"), shutil.which("node")]
    for candidate in candidates:
        if not candidate:
            continue
        try:
            if _node_version(candidate) >= MIN_NODE:
                return str(Path(candidate).resolve()), False
        except (OSError, ValueError, subprocess.SubprocessError):
            continue
    if not allow_download:
        raise RuntimeError(
            "OpenClaw requires Node 22.19+. Pass --node /path/to/node or set OPENCLAW_NODE."
        )
    npx = shutil.which("npx")
    if not npx:
        raise RuntimeError("Node 22.19+ is unavailable and npx was not found for the isolated fallback.")
    resolved = _run(
        [npx, "-y", "node@22.19.0", "-p", "process.execPath"],
        timeout=300,
    ).stdout.strip()
    if not resolved or _node_version(resolved) < MIN_NODE:
        raise RuntimeError("Failed to resolve an isolated Node 22.19 runtime.")
    return resolved, True


def _json_output(raw: str) -> Any:
    starts = [position for position in (raw.find("{"), raw.find("[")) if position >= 0]
    if not starts:
        raise ValueError(f"Expected JSON output, got: {raw[:500]}")
    start = min(starts)
    value, _ = json.JSONDecoder().raw_decode(raw[start:])
    return value


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _child_path(value: str, home: Path) -> Path:
    text = value.strip()
    if text == "~":
        return home.resolve()
    if text.startswith("~/"):
        return (home / text[2:]).resolve()
    return Path(text).resolve()


def _set_config(oc: list[str], env: dict[str, str], path: str, value: Any) -> None:
    _run(
        [*oc, "config", "set", path, json.dumps(value, separators=(",", ":")), "--strict-json"],
        env=env,
    )


def run_gate(*, node: str, node_downloaded: bool, keep_temp: bool) -> dict[str, Any]:
    if os.name == "nt":
        raise RuntimeError("The OpenClaw release gate currently supports macOS and Linux.")
    if not OPENCLAW_CLI.exists():
        raise RuntimeError(
            f"OpenClaw dependency is missing at {OPENCLAW_CLI}. Run `npm ci` in {PACKAGE}."
        )
    if not BUNDLE.exists() or not ARTIFACT_PROBE.exists():
        raise RuntimeError("Portable-memory vector or installed-artifact probe is missing.")

    temp = Path(tempfile.mkdtemp(prefix="cortex-openclaw-release-"))
    gateway: subprocess.Popen[str] | None = None
    gateway_log = None
    try:
        pack_result = _run(
            ["npm", "pack", "--pack-destination", str(temp), "--json"],
            cwd=PACKAGE,
            timeout=300,
        )
        pack = _json_output(pack_result.stdout)[0]
        tarball = temp / pack["filename"]
        if not tarball.exists():
            raise RuntimeError(f"npm pack did not produce {tarball}")

        home = temp / "home"
        workspace = temp / "workspace"
        home.mkdir()
        workspace.mkdir()
        env = dict(os.environ)
        for key in list(env):
            if key.startswith("OPENCLAW_"):
                env.pop(key, None)
        env["HOME"] = str(home)
        env["XDG_CONFIG_HOME"] = str(temp / "xdg-config")
        env["XDG_DATA_HOME"] = str(temp / "xdg-data")
        env["XDG_STATE_HOME"] = str(temp / "xdg-state")
        env["XDG_CACHE_HOME"] = str(temp / "xdg-cache")
        env["NO_COLOR"] = "1"
        port = _free_loopback_port()
        oc = [node, str(OPENCLAW_CLI), "--profile", PROFILE, "--no-color"]
        expected_profile_root = (home / f".openclaw-{PROFILE}").resolve()

        _set_config(oc, env, "gateway.mode", "local")
        _set_config(oc, env, "gateway.bind", "loopback")
        _set_config(oc, env, "gateway.port", port)
        _set_config(oc, env, "gateway.auth.mode", "none")
        _set_config(oc, env, "agents.defaults.workspace", str(workspace))
        _set_config(oc, env, "plugins.entries.bonjour.enabled", False)
        config_path_text = _run([*oc, "config", "file"], env=env).stdout.strip().splitlines()[-1]
        config_path = _child_path(config_path_text, home)
        _require(
            config_path.is_relative_to(expected_profile_root),
            f"OpenClaw config escaped the isolated profile: {config_path}",
        )

        _run([*oc, "plugins", "install", str(tarball)], env=env, timeout=300)
        _run([*oc, "plugins", "uninstall", PLUGIN_ID, "--force"], env=env, timeout=120)
        _run([*oc, "plugins", "install", "--force", str(tarball)], env=env, timeout=300)

        bundle = json.loads(BUNDLE.read_text(encoding="utf-8"))
        signing_key_id = str(bundle["signature"]["key_id"])
        _set_config(oc, env, "plugins.allow", [PLUGIN_ID])
        _set_config(oc, env, "plugins.slots.contextEngine", PLUGIN_ID)
        _set_config(oc, env, f"plugins.entries.{PLUGIN_ID}.enabled", True)
        _set_config(
            oc,
            env,
            f"plugins.entries.{PLUGIN_ID}.config",
            {
                "mode": "bundle",
                "bundlePath": str(BUNDLE),
                "expectedSigningKeyId": signing_key_id,
                "failOpen": False,
            },
        )
        _run([*oc, "config", "validate"], env=env)

        inspect = _json_output(
            _run([*oc, "plugins", "inspect", PLUGIN_ID, "--runtime", "--json"], env=env).stdout
        )
        plugin = inspect.get("plugin") or {}
        _require(plugin.get("id") == PLUGIN_ID, "OpenClaw inspected the wrong plugin id.")
        _require(plugin.get("status") == "loaded", "Cortex plugin did not load.")
        _require(plugin.get("enabled") is True, "Cortex plugin is not enabled.")
        _require(plugin.get("activated") is True, "Cortex plugin is not activated.")
        _require(
            plugin.get("activationReason") == "selected context engine slot",
            "Cortex plugin was not activated through the contextEngine slot.",
        )
        _require(
            PLUGIN_ID in (plugin.get("contextEngineIds") or []),
            "Cortex contextEngine registration is missing.",
        )
        _require(plugin.get("configSchema") is True, "Cortex plugin schema is missing.")
        _require(inspect.get("diagnostics") == [], "OpenClaw reported plugin diagnostics.")
        _run([*oc, "plugins", "doctor"], env=env)

        installed_root = Path(str(plugin["rootDir"]))
        _require(
            installed_root.resolve().is_relative_to(expected_profile_root),
            f"OpenClaw plugin install escaped the isolated profile: {installed_root}",
        )
        artifact_metrics = _json_output(
            _run(
                [
                    node,
                    str(ARTIFACT_PROBE),
                    str(installed_root),
                    str(BUNDLE),
                    signing_key_id,
                    str(temp),
                ],
                env=env,
                timeout=120,
            ).stdout
        )

        gateway_log_path = temp / "gateway.log"
        gateway_log = gateway_log_path.open("w", encoding="utf-8")
        gateway = subprocess.Popen(
            [*oc, "gateway", "run", "--auth", "none", "--port", str(port), "--ws-log", "compact"],
            env=env,
            text=True,
            stdout=gateway_log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        health: dict[str, Any] | None = None
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if gateway.poll() is not None:
                gateway_log.flush()
                raise RuntimeError(
                    f"OpenClaw Gateway exited early:\n{gateway_log_path.read_text(encoding='utf-8')}"
                )
            try:
                health_result = _run(
                    [
                        *oc,
                        "gateway",
                        "health",
                        "--port",
                        str(port),
                        "--json",
                        "--timeout",
                        "1000",
                    ],
                    env=env,
                    timeout=5,
                    report_failure=False,
                )
                health = _json_output(health_result.stdout)
                break
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired, ValueError):
                time.sleep(0.25)
        gateway_log.flush()
        if not health:
            raise RuntimeError(
                f"OpenClaw Gateway did not become healthy:\n{gateway_log_path.read_text(encoding='utf-8')}"
            )
        _require(health.get("ok") is True, "OpenClaw Gateway health is not OK.")
        plugins = health.get("plugins") or {}
        _require(PLUGIN_ID in (plugins.get("loaded") or []), "Gateway did not load Cortex plugin.")
        _require(plugins.get("errors") == [], "Gateway reported plugin errors.")
        log_text = gateway_log_path.read_text(encoding="utf-8")
        _require(
            "registered bundle context engine" in log_text,
            "Gateway log does not confirm Cortex contextEngine registration.",
        )
        _require(
            "plugins.allow is empty" not in log_text,
            "Gateway auto-loaded Cortex without an explicit plugins.allow entry.",
        )

        return {
            "openclaw_version": _run([node, str(OPENCLAW_CLI), "--version"], env=env).stdout.strip(),
            "node_version": ".".join(map(str, _node_version(node))),
            "node_downloaded": node_downloaded,
            "package": {
                "name": pack["name"],
                "version": pack["version"],
                "files": pack["entryCount"],
                "size_bytes": pack["size"],
            },
            "install": {
                "archive_install": True,
                "clean_uninstall": True,
                "archive_reinstall": True,
                "runtime_loaded": True,
                "context_engine_registered": True,
                "diagnostics": [],
            },
            "isolation": {
                "temporary_home": True,
                "config_under_temporary_profile": True,
                "install_under_temporary_profile": True,
                "openclaw_environment_overrides_scrubbed": True,
            },
            "artifact": artifact_metrics,
            "gateway": {
                "healthy": True,
                "plugin_loaded": True,
                "plugin_errors": [],
            },
        }
    finally:
        if gateway is not None and gateway.poll() is None:
            try:
                os.killpg(gateway.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                gateway.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(gateway.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                gateway.wait(timeout=5)
        if gateway_log is not None:
            gateway_log.close()
        if keep_temp:
            print(f"kept temporary OpenClaw profile at {temp}", file=sys.stderr)
        else:
            shutil.rmtree(temp, ignore_errors=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--node", help="Path to Node 22.19+ (or set OPENCLAW_NODE).")
    parser.add_argument(
        "--no-node-download",
        action="store_true",
        help="Do not use `npx node@22.19.0` when the active Node is too old.",
    )
    parser.add_argument("--keep-temp", action="store_true", help="Keep the isolated profile for debugging.")
    args = parser.parse_args()
    node, downloaded = _resolve_node(args.node, not args.no_node_download)
    report = run_gate(node=node, node_downloaded=downloaded, keep_temp=args.keep_temp)
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

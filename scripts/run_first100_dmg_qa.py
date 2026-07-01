#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import plistlib
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASE_URL = "http://127.0.0.1:8766"
DEFAULT_LOG_PATH = ROOT / ".context" / "first100_clean_profile_qa_run.txt"
DEFAULT_PACKET_PATH = ROOT / ".context" / "first100_launch_packets" / "clean-profile-qa.txt"
DEFAULT_TOKEN_KEY = "localBetaAPIKey.v1"
DEFAULT_BUNDLE_ID = "com.cortex.doppl"

CLEAN_PROFILE_QA_FIELDS = [
    "QA owner",
    "QA date",
    "macOS version",
    "Device type",
    "Artifact source",
    "DMG checksum matched",
    "Installed on clean macOS 13+ profile",
    "Gatekeeper path accepted",
    "First-run onboarding completed without developer docs",
    "Obsidian/local notes or MCP connected",
    "Sync created Review items",
    "Review approval worked",
    "Ask returned cited answer",
    "Backup worked",
    "Content-free support bundle export worked",
    "Manual update preserved memory folder",
    "Manual rollback preserved memory folder",
    "Invite copy reviewed for local-beta limitations",
]


class QAError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def command_text(args: list[str]) -> str:
    return " ".join(shlex.quote(str(arg)) for arg in args)


def relpath(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tail(text: str, limit: int = 4000) -> str:
    if len(text) <= limit:
        return text
    return text[-limit:]


class RunLog:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def reset(self, *, args: argparse.Namespace) -> None:
        self.path.write_text(
            "\n".join(
                [
                    "# First-100 Isolated-Home DMG QA Run",
                    "",
                    f"Started: {utc_now()}",
                    f"Workspace: {ROOT}",
                    f"Base URL: {args.base_url}",
                    f"Write clean-profile packet: {args.update_clean_profile_packet}",
                    "",
                    "This is isolated-home packaged DMG automation. It is not a real clean macOS profile,",
                    "Gatekeeper, first-run human onboarding, update, or rollback pass.",
                    "",
                ]
            ),
            encoding="utf-8",
        )

    def line(self, message: str = "") -> None:
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(message + "\n")

    def section(self, title: str) -> None:
        self.line("")
        self.line(f"## {title}")

    def command_result(self, args: list[str], completed: subprocess.CompletedProcess[str], elapsed: float) -> None:
        self.line(f"$ {command_text(args)}")
        self.line(f"returncode: {completed.returncode}")
        self.line(f"elapsed_seconds: {elapsed:.1f}")
        if completed.stdout:
            self.line("stdout:")
            self.line(tail(completed.stdout).rstrip())
        if completed.stderr:
            self.line("stderr:")
            self.line(tail(completed.stderr).rstrip())


def run_command(
    args: list[str],
    *,
    log: RunLog,
    timeout: int,
    cwd: Path | None = ROOT,
    env: dict[str, str] | None = None,
    log_args: list[str] | None = None,
) -> subprocess.CompletedProcess[str]:
    display_args = log_args or args
    started = time.monotonic()
    try:
        completed = subprocess.run(
            [str(arg) for arg in args],
            cwd=str(cwd) if cwd else None,
            env=env,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        elapsed = time.monotonic() - started
        log.line(f"$ {command_text(display_args)}")
        log.line(f"timeout_seconds: {timeout}")
        log.line(f"elapsed_seconds: {elapsed:.1f}")
        if exc.stdout:
            log.line("stdout:")
            log.line(tail(exc.stdout if isinstance(exc.stdout, str) else exc.stdout.decode(errors="replace")).rstrip())
        if exc.stderr:
            log.line("stderr:")
            log.line(tail(exc.stderr if isinstance(exc.stderr, str) else exc.stderr.decode(errors="replace")).rstrip())
        raise QAError(f"Command timed out after {timeout}s: {command_text(display_args)}") from exc
    elapsed = time.monotonic() - started
    log.command_result(display_args, completed, elapsed)
    if completed.returncode != 0:
        raise QAError(f"Command failed with exit {completed.returncode}: {command_text(display_args)}")
    return completed


def load_site_manifest() -> dict[str, Any]:
    manifest_path = ROOT / "site" / "downloads" / "latest.json"
    if not manifest_path.exists():
        return {}
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def manifest_dmg_name(manifest: dict[str, Any]) -> str:
    for artifact in manifest.get("artifacts") or []:
        if not isinstance(artifact, dict):
            continue
        filename = str(artifact.get("filename") or "")
        kind = str(artifact.get("kind") or "")
        if filename.endswith(".dmg") or kind == "dmg":
            return filename
    return ""


def find_default_dmg() -> Path:
    manifest = load_site_manifest()
    dmg_name = manifest_dmg_name(manifest)
    candidates: list[Path] = []
    if dmg_name:
        candidates.extend(
            [
                ROOT / "site" / "downloads" / dmg_name,
                ROOT / "outputs" / f"{str(manifest.get('version') or '').strip()}-{str(manifest.get('build') or '').strip()}" / dmg_name,
            ]
        )
        candidates.extend((ROOT / "outputs").glob(f"*/{dmg_name}"))
    candidates.extend((ROOT / "site" / "downloads").glob("*.dmg"))
    candidates.extend((ROOT / "outputs").glob("*/*.dmg"))
    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise QAError("Could not find a default DMG under site/downloads or outputs. Pass --dmg.")


def parse_checksum_file(path: Path) -> dict[str, str]:
    entries: dict[str, str] = {}
    if not path.exists():
        return entries
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        parts = raw_line.split()
        if len(parts) >= 2:
            entries[parts[-1]] = parts[0]
    return entries


def expected_sha256_for(dmg: Path, override: str | None = None) -> tuple[str, str]:
    if override:
        return override.lower(), "--expected-sha256"

    manifest = load_site_manifest()
    for artifact in manifest.get("artifacts") or []:
        if not isinstance(artifact, dict):
            continue
        if str(artifact.get("filename") or "") == dmg.name and artifact.get("sha256"):
            return str(artifact["sha256"]).lower(), "site/downloads/latest.json"

    checksum_candidates = [
        dmg.with_name(dmg.name.replace(".dmg", ".checksums.txt")),
        dmg.parent / f"{dmg.stem}.checksums.txt",
        ROOT / "site" / "downloads" / f"{dmg.stem}.checksums.txt",
    ]
    for checksum_path in checksum_candidates:
        entries = parse_checksum_file(checksum_path)
        if entries.get(dmg.name):
            return entries[dmg.name].lower(), relpath(checksum_path)

    raise QAError(f"Could not find an expected SHA-256 for {relpath(dmg)}. Pass --expected-sha256.")


def verify_checksum(dmg: Path, expected_hash: str, *, log: RunLog) -> str:
    actual_hash = sha256(dmg)
    log.line(f"DMG: {relpath(dmg)}")
    log.line(f"Expected SHA-256: {expected_hash}")
    log.line(f"Actual SHA-256:   {actual_hash}")
    if actual_hash.lower() != expected_hash.lower():
        raise QAError(f"DMG checksum mismatch for {relpath(dmg)}")
    return actual_hash


def bundle_metadata(app: Path) -> dict[str, str]:
    info_path = app / "Contents" / "Info.plist"
    if not info_path.exists():
        raise QAError(f"Copied app is missing Info.plist: {info_path}")
    with info_path.open("rb") as handle:
        info = plistlib.load(handle)
    executable = str(info.get("CFBundleExecutable") or "")
    if not executable:
        raise QAError(f"CFBundleExecutable is missing in {info_path}")
    return {
        "executable": executable,
        "bundle_id": str(info.get("CFBundleIdentifier") or DEFAULT_BUNDLE_ID),
        "version": str(info.get("CFBundleShortVersionString") or ""),
        "build": str(info.get("CFBundleVersion") or ""),
    }


def find_app_on_mount(mountpoint: Path) -> Path:
    apps = [
        path
        for path in mountpoint.rglob("*.app")
        if path.is_dir() and (path / "Contents" / "Info.plist").exists()
    ]
    apps.sort(key=lambda path: (len(path.relative_to(mountpoint).parts), str(path)))
    if not apps:
        raise QAError(f"No .app bundle found in mounted DMG at {mountpoint}")
    return apps[0]


def copy_app(source_app: Path, applications_dir: Path, *, log: RunLog, timeout: int) -> Path:
    applications_dir.mkdir(parents=True, exist_ok=True)
    destination = applications_dir / source_app.name
    if destination.exists():
        shutil.rmtree(destination)
    ditto = Path("/usr/bin/ditto")
    if ditto.exists():
        run_command([str(ditto), "--rsrc", "--extattr", str(source_app), str(destination)], log=log, timeout=timeout)
    else:
        shutil.copytree(source_app, destination, symlinks=True)
        log.line(f"Copied with shutil.copytree: {source_app} -> {destination}")
    if not destination.exists():
        raise QAError(f"App copy did not create {destination}")
    return destination


def http_status(base_url: str, path: str, *, timeout: float) -> tuple[int, str]:
    url = base_url.rstrip("/") + path
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")
    except (OSError, urllib.error.URLError):
        return 0, ""


def ensure_no_existing_server(base_url: str) -> None:
    status, body = http_status(base_url, "/health", timeout=1.0)
    if status:
        raise QAError(
            f"{base_url}/health is already responding before the packaged app launch. "
            "Quit existing Cortex instances before running isolated-home DMG QA. "
            f"HTTP {status}: {tail(body, 500)}"
        )


def base_url_port(base_url: str) -> int | None:
    parsed = urllib.parse.urlparse(base_url)
    if parsed.port:
        return parsed.port
    if parsed.scheme == "http":
        return 80
    if parsed.scheme == "https":
        return 443
    return None


def listening_pids(port: int | None) -> set[int]:
    if port is None:
        return set()
    lsof = Path("/usr/sbin/lsof")
    executable = str(lsof if lsof.exists() else "lsof")
    completed = subprocess.run(
        [executable, "-nP", f"-tiTCP:{port}", "-sTCP:LISTEN"],
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode not in {0, 1}:
        return set()
    pids: set[int] = set()
    for raw_line in completed.stdout.splitlines():
        try:
            pids.add(int(raw_line.strip()))
        except ValueError:
            pass
    return pids


def wait_for_ready(base_url: str, process: subprocess.Popen[Any], *, timeout: int, log: RunLog) -> None:
    deadline = time.monotonic() + timeout
    last_status = 0
    last_body = ""
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise QAError(f"Packaged app exited before backend became ready with code {process.returncode}")
        health_status, health_body = http_status(base_url, "/health", timeout=1.5)
        ready_status, ready_body = http_status(base_url, "/ready", timeout=1.5)
        last_status = ready_status or health_status
        last_body = ready_body or health_body
        if health_status == 200 and ready_status == 200:
            log.line(f"Backend ready at {base_url}")
            return
        time.sleep(1)
    raise QAError(f"Timed out waiting for {base_url}/ready. Last HTTP status {last_status}: {tail(last_body, 500)}")


def read_isolated_credentials_token(home: Path) -> str:
    credentials_path = home / "Library" / "Application Support" / "Cortex" / "credentials.json"
    if not credentials_path.exists():
        return ""
    try:
        payload = json.loads(credentials_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return ""
    return str(payload.get(DEFAULT_TOKEN_KEY) or "").strip()


def read_keychain_token(bundle_id: str) -> str:
    try:
        completed = subprocess.run(
            ["security", "find-generic-password", "-s", bundle_id, "-a", DEFAULT_TOKEN_KEY, "-w"],
            text=True,
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if completed.returncode != 0:
        return ""
    return completed.stdout.strip()


def discover_token(home: Path, bundle_id: str, *, log: RunLog, timeout: int = 20) -> tuple[str, str]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        token = read_isolated_credentials_token(home)
        if token:
            log.line("Token source: isolated HOME credentials.json")
            return token, "isolated-home credentials.json"
        token = read_keychain_token(bundle_id)
        if token:
            log.line("Token source: macOS Keychain for packaged bundle id")
            log.line("Note: CFFIXED_USER_HOME isolates app files, but the bundle Keychain item is user-level macOS state.")
            return token, "macOS Keychain"
        time.sleep(0.5)
    raise QAError("Could not discover the packaged app local API token from isolated HOME credentials.json or macOS Keychain.")


def smoke_supports_include_backup(python: str, *, log: RunLog) -> bool:
    completed = run_command(
        [python, str(ROOT / "scripts" / "first100_live_smoke.py"), "--help"],
        log=log,
        timeout=20,
    )
    return "--include-backup" in completed.stdout


def run_live_smoke(
    *,
    args: argparse.Namespace,
    env: dict[str, str],
    token: str,
    app: Path,
    log: RunLog,
) -> tuple[dict[str, Any], bool]:
    python = shutil.which("python3") or sys.executable
    include_backup = False
    if not args.skip_backup and smoke_supports_include_backup(python, log=log):
        include_backup = True

    mcp_stdio_path = app / "Contents" / "Resources" / "scripts" / "cortex_mcp_stdio.py"
    command = [
        python,
        str(ROOT / "scripts" / "first100_live_smoke.py"),
        "--base-url",
        args.base_url,
        "--token",
        token,
        "--user-id",
        f"first100-dmg-qa-{uuid.uuid4().hex[:10]}",
        "--mcp-stdio-path",
        str(mcp_stdio_path),
    ]
    if include_backup:
        command.append("--include-backup")
    display_command = command.copy()
    try:
        token_index = display_command.index("--token") + 1
        display_command[token_index] = "[REDACTED]"
    except (ValueError, IndexError):
        pass
    completed = run_command(command, log=log, timeout=args.smoke_timeout, env=env, log_args=display_command)
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise QAError("first100_live_smoke.py did not emit valid JSON") from exc
    if payload.get("status") != "ok":
        raise QAError(f"first100_live_smoke.py returned non-ok status: {payload.get('status')}")
    return payload, include_backup


def terminate_pid(pid: int, *, log: RunLog) -> None:
    if pid == os.getpid():
        return
    try:
        os.kill(pid, signal.SIGTERM)
        log.line(f"Sent SIGTERM to packaged backend listener pid {pid}")
    except ProcessLookupError:
        return
    except PermissionError as exc:
        log.line(f"Could not terminate listener pid {pid}: {exc}")


def cleanup_processes(
    *,
    process: subprocess.Popen[Any] | None,
    app_log_path: Path | None,
    listener_pids: set[int],
    port: int | None,
    log: RunLog,
) -> None:
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=10)
            log.line(f"Packaged app terminated with code {process.returncode}")
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
            log.line(f"Packaged app required SIGKILL; code {process.returncode}")

    remaining = listening_pids(port) & listener_pids
    for pid in sorted(remaining):
        terminate_pid(pid, log=log)
    if remaining:
        time.sleep(1)

    if app_log_path and app_log_path.exists():
        contents = app_log_path.read_text(encoding="utf-8", errors="replace")
        if contents.strip():
            log.section("Packaged App Log Tail")
            log.line(tail(contents, 8000).rstrip())


def packet_template() -> str:
    values = {field: "" for field in CLEAN_PROFILE_QA_FIELDS}
    for field in CLEAN_PROFILE_QA_FIELDS:
        if field not in {"QA owner", "QA date", "macOS version", "Device type", "Artifact source"}:
            values[field] = "FILL_ME"
    body = "\n".join(f"{field}: {values[field]}" for field in CLEAN_PROFILE_QA_FIELDS)
    return (
        "# First-100 Clean-Profile QA Packet\n\n"
        "Keep this file private. The isolated-home DMG QA script may fill only the fields it actually verifies.\n\n"
        "```text\n"
        f"{body}\n"
        "```\n"
    )


def update_clean_profile_packet(
    path: Path,
    *,
    dmg: Path,
    actual_hash: str,
    include_backup: bool,
    log_path: Path,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = path.read_text(encoding="utf-8") if path.exists() else packet_template()
    updates = {
        "QA owner": "scripts/run_first100_dmg_qa.py isolated-home automation",
        "QA date": datetime.now().date().isoformat(),
        "macOS version": platform.mac_ver()[0] or platform.platform(),
        "Device type": platform.machine() or "unknown",
        "Artifact source": relpath(dmg),
        "DMG checksum matched": "verified",
        "Obsidian/local notes or MCP connected": "verified",
        "Sync created Review items": "verified",
        "Review approval worked": "verified",
        "Ask returned cited answer": "verified",
        "Content-free support bundle export worked": "verified",
    }
    if include_backup:
        updates["Backup worked"] = "verified"

    lines = text.splitlines()
    seen: set[str] = set()
    rewritten: list[str] = []
    for line in lines:
        stripped = line.strip()
        matched_field = ""
        for field in CLEAN_PROFILE_QA_FIELDS:
            if stripped.startswith(f"{field}:"):
                matched_field = field
                break
        if matched_field and matched_field in updates:
            rewritten.append(f"{matched_field}: {updates[matched_field]}")
            seen.add(matched_field)
        else:
            rewritten.append(line)

    missing_lines = [f"{field}: {updates[field]}" for field in CLEAN_PROFILE_QA_FIELDS if field in updates and field not in seen]
    if missing_lines:
        rewritten.extend(["", "```text", *missing_lines, "```"])

    note = (
        "\n\n"
        "## Isolated-Home Automation Note\n\n"
        f"Last isolated-home DMG QA pass: {utc_now()}\n\n"
        f"- DMG SHA-256: {actual_hash}\n"
        f"- Private run log: {relpath(log_path)}\n"
        "- This automation did not verify a real clean macOS profile, Gatekeeper Control-click/Open, "
        "first-run human onboarding, manual update, manual rollback, or invite-copy review.\n"
    )
    path.write_text("\n".join(rewritten).rstrip() + note, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Verify the packaged Cortex DMG with checksum, hdiutil, isolated-home launch, "
            "and the first-100 live smoke. This is not human clean-profile or Gatekeeper QA."
        )
    )
    parser.add_argument("--dmg", type=Path, default=None, help="DMG to verify. Defaults to the current site/downloads DMG, then outputs.")
    parser.add_argument("--expected-sha256", default="", help="Override the expected DMG SHA-256.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="Packaged backend URL to smoke.")
    parser.add_argument("--log-path", type=Path, default=DEFAULT_LOG_PATH, help="Private run log path.")
    parser.add_argument("--update-clean-profile-packet", action="store_true", help="Fill only verified fields in the private clean-profile QA packet after a full automated pass.")
    parser.add_argument("--packet-path", type=Path, default=DEFAULT_PACKET_PATH, help="Private clean-profile QA packet to update when requested.")
    parser.add_argument("--launch-timeout", type=int, default=120, help="Seconds to wait for the packaged backend to become ready.")
    parser.add_argument("--command-timeout", type=int, default=300, help="Seconds for hdiutil and copy commands.")
    parser.add_argument("--smoke-timeout", type=int, default=420, help="Seconds for first100_live_smoke.py.")
    parser.add_argument("--skip-backup", action="store_true", help="Do not pass --include-backup to first100_live_smoke.py.")
    parser.add_argument("--keep-temp", action="store_true", help="Keep the isolated home, mountpoint, and copied app for debugging.")
    args = parser.parse_args(argv)

    log_path = args.log_path if args.log_path.is_absolute() else ROOT / args.log_path
    packet_path = args.packet_path if args.packet_path.is_absolute() else ROOT / args.packet_path
    log = RunLog(log_path)
    log.reset(args=args)

    dmg = (args.dmg or find_default_dmg()).expanduser()
    if not dmg.is_absolute():
        dmg = ROOT / dmg
    dmg = dmg.resolve()
    process: subprocess.Popen[Any] | None = None
    app_log_path: Path | None = None
    listener_pids: set[int] = set()
    mounted = False
    temp_root: Path | None = None

    try:
        if not dmg.exists():
            raise QAError(f"DMG does not exist: {dmg}")

        log.section("Checksum")
        expected_hash, expected_source = expected_sha256_for(dmg, args.expected_sha256.strip() or None)
        log.line(f"Expected hash source: {expected_source}")
        actual_hash = verify_checksum(dmg, expected_hash, log=log)

        log.section("Preflight")
        ensure_no_existing_server(args.base_url)
        port = base_url_port(args.base_url)
        existing_pids = listening_pids(port)
        if existing_pids:
            raise QAError(f"Port {port} already has listener pids before launch: {sorted(existing_pids)}")
        log.line("No existing backend responded before launch.")

        log.section("hdiutil Verify")
        run_command(["hdiutil", "verify", str(dmg)], log=log, timeout=args.command_timeout)

        temp_root = Path(tempfile.mkdtemp(prefix="cortex-first100-dmg-qa-"))
        mountpoint = temp_root / "mnt"
        applications_dir = temp_root / "Applications"
        isolated_home = temp_root / "home"
        isolated_tmp = temp_root / "tmp"
        app_log_path = temp_root / "packaged-app.log"
        mountpoint.mkdir()
        isolated_home.mkdir()
        isolated_tmp.mkdir()

        log.section("Mount DMG")
        run_command(
            ["hdiutil", "attach", str(dmg), "-nobrowse", "-readonly", "-mountpoint", str(mountpoint)],
            log=log,
            timeout=args.command_timeout,
        )
        mounted = True

        source_app = find_app_on_mount(mountpoint)
        log.line(f"Mounted app: {source_app}")

        log.section("Copy App")
        copied_app = copy_app(source_app, applications_dir, log=log, timeout=args.command_timeout)
        metadata = bundle_metadata(copied_app)
        executable = copied_app / "Contents" / "MacOS" / metadata["executable"]
        if not executable.exists():
            raise QAError(f"App executable is missing: {executable}")
        log.line(f"Copied app: {copied_app}")
        log.line(f"Bundle id: {metadata['bundle_id']}")
        log.line(f"Version/build: {metadata['version']} / {metadata['build']}")

        log.section("Launch")
        launch_env = os.environ.copy()
        launch_env["CFFIXED_USER_HOME"] = str(isolated_home)
        launch_env["HOME"] = str(isolated_home)
        launch_env["TMPDIR"] = str(isolated_tmp)
        launch_env["CORTEX_FIRST100_DMG_QA"] = "1"
        app_log_handle = app_log_path.open("w", encoding="utf-8")
        try:
            process = subprocess.Popen(
                [str(executable)],
                cwd=str(copied_app / "Contents" / "MacOS"),
                env=launch_env,
                stdout=app_log_handle,
                stderr=subprocess.STDOUT,
                text=True,
            )
        finally:
            app_log_handle.close()
        log.line(f"Launched packaged app pid {process.pid}")
        log.line(f"CFFIXED_USER_HOME: {isolated_home}")

        wait_for_ready(args.base_url, process, timeout=args.launch_timeout, log=log)
        listener_pids = listening_pids(port)
        log.line(f"Backend listener pids after launch: {sorted(listener_pids)}")
        token, token_source = discover_token(isolated_home, metadata["bundle_id"] or DEFAULT_BUNDLE_ID, log=log)

        log.section("First-100 Live Smoke")
        smoke_env = launch_env.copy()
        smoke_result, include_backup = run_live_smoke(args=args, env=smoke_env, token=token, app=copied_app, log=log)
        log.line(f"Smoke marker: {smoke_result.get('marker')}")
        log.line(f"Smoke checks: {[check.get('name') for check in smoke_result.get('checks', [])]}")

        log.section("Result")
        result = {
            "status": "ok",
            "dmg": relpath(dmg),
            "sha256": actual_hash,
            "base_url": args.base_url,
            "copied_app": str(copied_app),
            "isolated_home": str(isolated_home),
            "token_source": token_source,
            "backup_checked": include_backup,
            "log_path": relpath(log_path),
            "clean_profile_packet_updated": False,
            "manual_gates_remaining": [
                "real clean macOS 13+ profile install",
                "Gatekeeper Control-click/Open path",
                "first-run human onboarding without developer docs",
                "manual update preserving memory folder",
                "manual rollback preserving memory folder",
                "invite copy review for local-beta limitations",
            ],
        }
        if args.update_clean_profile_packet:
            update_clean_profile_packet(
                packet_path,
                dmg=dmg,
                actual_hash=actual_hash,
                include_backup=include_backup,
                log_path=log_path,
            )
            result["clean_profile_packet_updated"] = True
            result["clean_profile_packet"] = relpath(packet_path)
            log.line(f"Updated private packet: {packet_path}")

        log.line(json.dumps(result, indent=2, sort_keys=True))
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except Exception as exc:
        log.section("Failure")
        log.line(f"{type(exc).__name__}: {exc}")
        print(json.dumps({"status": "failed", "error": str(exc), "log_path": relpath(log_path)}, indent=2, sort_keys=True))
        return 1
    finally:
        log.section("Cleanup")
        try:
            cleanup_processes(
                process=process,
                app_log_path=app_log_path,
                listener_pids=listener_pids,
                port=base_url_port(args.base_url),
                log=log,
            )
        except Exception as cleanup_exc:
            log.line(f"Cleanup process error: {cleanup_exc}")
        if mounted:
            try:
                run_command(["hdiutil", "detach", str(mountpoint), "-force"], log=log, timeout=60)
            except Exception as detach_exc:
                log.line(f"DMG detach error: {detach_exc}")
        if temp_root is not None:
            if args.keep_temp:
                log.line(f"Kept temp root: {temp_root}")
            else:
                shutil.rmtree(temp_root, ignore_errors=True)
                log.line("Removed temp root.")
        log.line(f"Finished: {utc_now()}")


if __name__ == "__main__":
    raise SystemExit(main())

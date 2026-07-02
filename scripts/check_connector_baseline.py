#!/usr/bin/env python3
from __future__ import annotations

import importlib
import json
import sys
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.database import init_db
from backend.app.storage import (
    BASELINE_10K_CONNECTOR_IDS,
    SCHEDULED_CREDENTIAL_SYNC_SOURCES,
    CortexStore,
    _source_account_sync_scheduler_supported,
)


def connector_module_name(connector_id: str) -> str:
    return connector_id.replace("-", "_")


def expected_fetch_symbol(connector_id: str) -> str:
    if connector_id == "obsidian":
        return "scan_obsidian_vault"
    return f"fetch_{connector_module_name(connector_id)}_records"


def expected_test_path(connector_id: str) -> Path:
    return ROOT / "backend" / "tests" / f"test_{connector_module_name(connector_id)}_connector.py"


def scheduler_probe_accounts(connector_id: str) -> list[dict[str, Any]]:
    if connector_id == "obsidian":
        return [
            {
                "label": "local_vault_path",
                "account": {"source": connector_id, "metadata": {"vault_path": "/tmp/cortex-baseline-obsidian"}},
            }
        ]

    probes: list[dict[str, Any]] = []
    if connector_id in SCHEDULED_CREDENTIAL_SYNC_SOURCES:
        probes.append(
            {
                "label": "local_credential_ref",
                "account": {
                    "source": connector_id,
                    "metadata": {"credential_ref": f"source_credential:baseline-{connector_id}"},
                },
            }
        )
    if connector_id == "zotero":
        probes.append(
            {
                "label": "local_zotero_api",
                "account": {"source": connector_id, "metadata": {"api_base_url": "http://127.0.0.1:23119/api"}},
            }
        )
    return probes


def declared_route_paths() -> set[str]:
    main_text = (ROOT / "backend" / "app" / "main.py").read_text(encoding="utf-8")
    routes: set[str] = set()
    for raw_line in main_text.splitlines():
        line = raw_line.strip()
        marker = '@app.post("'
        if not line.startswith(marker):
            continue
        route = line[len(marker) :].split('"', 1)[0]
        routes.add(route)
    return routes


def connector_catalog() -> dict[str, dict[str, Any]]:
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "cortex-connectors.db"
        init_db(db_path)
        store = CortexStore(db_path)
        return {item["id"]: item for item in store.source_connector_catalog()}


def check_baseline() -> dict[str, Any]:
    errors: list[str] = []
    connector_package = importlib.import_module("backend.app.connectors")
    routes = declared_route_paths()
    catalog = connector_catalog()
    connectors: list[dict[str, Any]] = []

    for connector_id in sorted(BASELINE_10K_CONNECTOR_IDS):
        module_name = connector_module_name(connector_id)
        module_path = ROOT / "backend" / "app" / "connectors" / f"{module_name}.py"
        test_path = expected_test_path(connector_id)
        fetch_symbol = expected_fetch_symbol(connector_id)
        item = catalog.get(connector_id)
        setup = (item or {}).get("connection_setup") or {}
        endpoint = str(setup.get("endpoint") or "")
        scheduler_probes = scheduler_probe_accounts(connector_id)
        scheduler_supported = bool(scheduler_probes) and all(
            _source_account_sync_scheduler_supported(probe["account"]) for probe in scheduler_probes
        )
        connector_errors: list[str] = []

        if not module_path.exists():
            connector_errors.append(f"module missing: {module_path.relative_to(ROOT)}")
        try:
            module = importlib.import_module(f"backend.app.connectors.{module_name}")
        except Exception as exc:  # pragma: no cover - surfaced in JSON for operator triage.
            module = None
            connector_errors.append(f"module import failed: {exc}")

        if module is not None and not hasattr(module, fetch_symbol):
            connector_errors.append(f"module missing symbol: {fetch_symbol}")
        if not hasattr(connector_package, fetch_symbol):
            connector_errors.append(f"connector package missing export: {fetch_symbol}")
        if not test_path.exists():
            connector_errors.append(f"connector test missing: {test_path.relative_to(ROOT)}")
        if item is None:
            connector_errors.append("catalog entry missing")
        else:
            if not item.get("baseline_10k"):
                connector_errors.append("catalog baseline_10k is not true")
            if item.get("beta_status") != "ready":
                connector_errors.append(f"catalog beta_status is {item.get('beta_status')!r}, expected 'ready'")
            if not setup.get("available"):
                connector_errors.append("catalog connection_setup.available is not true")
            if not endpoint:
                connector_errors.append("catalog connection_setup.endpoint is missing")
            elif endpoint not in routes:
                connector_errors.append(f"sync route missing from backend/app/main.py: {endpoint}")
            if setup.get("disconnect_behavior") != "pause_sync_keep_local_data_and_credentials":
                connector_errors.append("disconnect behavior must preserve local data and credentials")
        if not scheduler_probes:
            connector_errors.append("scheduled sync support probe missing")
        for probe in scheduler_probes:
            if not _source_account_sync_scheduler_supported(probe["account"]):
                connector_errors.append(f"scheduled sync support missing for {probe['label']}")

        connectors.append(
            {
                "id": connector_id,
                "module": str(module_path.relative_to(ROOT)),
                "test": str(test_path.relative_to(ROOT)),
                "fetch_symbol": fetch_symbol,
                "endpoint": endpoint,
                "scheduler_supported": scheduler_supported,
                "scheduler_probes": [probe["label"] for probe in scheduler_probes],
                "ok": not connector_errors,
                "errors": connector_errors,
            }
        )
        errors.extend(f"{connector_id}: {error}" for error in connector_errors)

    return {
        "status": "ok" if not errors else "failed",
        "baseline_count": len(BASELINE_10K_CONNECTOR_IDS),
        "connectors": connectors,
        "errors": errors,
    }


def main() -> int:
    payload = check_baseline()
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())

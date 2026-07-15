from __future__ import annotations

import re
import ast
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest


ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "backend" / "app"


def _non_docstring_literals(path: Path) -> list[tuple[int, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if (
            isinstance(body, list)
            and body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            docstrings.add(id(body[0].value))
    return [
        (node.lineno, node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


class AppBrandRoutingTests(unittest.TestCase):
    def test_user_visible_backend_modules_have_no_literal_cortex_copy(self) -> None:
        """A new backend response must use APP_BRAND instead of silently leaking the DMG name.

        Internal protocol/storage identifiers deliberately keep their stable ``cortex`` names;
        this guard targets the modules that produce UI copy, generated pages, exports, auth
        messages, readiness details, or MCP prose.
        """

        user_visible_modules = (
            "connectors/gmail.py",
            "connectors/google_drive.py",
            "connectors/outlook.py",
            "context_file.py",
            "email_sender.py",
            "hosted_readiness.py",
            "import_diff.py",
            "main.py",
            "mcp_tools.py",
            "obsidian_writeback.py",
            "oauth_broker.py",
            "storage.py",
            "standalone_server.py",
            "vault.py",
            "vault_markdown.py",
        )
        stable_internal_literals = {
            "Cortex-local-connector",  # HTTP User-Agent; never rendered to a user
            "CortexImports",  # legacy on-disk drop-folder name
            "X-Cortex-User",  # public protocol header; changing it would break clients
            "Authorization, Content-Type, X-Cortex-User",  # CORS form of that header
        }

        def only_artifact_names(value: str) -> bool:
            # Release ARTIFACT names/URLs are canonical, not brand copy: the direct-download
            # channel genuinely ships files named Cortex-<version>.dmg/.app.zip/.checksums.txt
            # (e.g. the /download page's GitHub-release href and its checksums link). Strip
            # those tokens; if no "Cortex" remains, the string carries no brand prose.
            return "Cortex" not in re.sub(r"Cortex-\d[\w.\-]*", "", value)

        leaks: list[str] = []
        for name in user_visible_modules:
            for line, value in _non_docstring_literals(APP / name):
                if "Cortex" in value and value not in stable_internal_literals:
                    if only_artifact_names(value):
                        continue
                    leaks.append(f"{name}:{line}: {value!r}")
        self.assertEqual([], leaks, "hardcoded user-visible brand copy:\n" + "\n".join(leaks))

    def test_doppl_environment_rebrands_runtime_outputs(self) -> None:
        """Exercise import-time constants in a clean interpreter, exactly like the MAS backend."""

        with tempfile.TemporaryDirectory() as td:
            env = os.environ.copy()
            env.update(
                {
                    "CORTEX_APP_BRAND": "Doppl",
                    "CORTEX_DB_PATH": str(Path(td) / "index.sqlite"),
                    "CORTEX_VAULT_PATH": str(Path(td) / "vault"),
                    "CORTEX_API_KEY": "brand-contract-key-with-enough-entropy",
                    "CORTEX_AUTH_ENABLED": "0",
                }
            )
            code = textwrap.dedent(
                """
                import json
                from pathlib import Path

                from backend.app.config import APP_BRAND
                from backend.app.context_file import render_context_block
                from backend.app.email_sender import render_auth_email
                from backend.app.hosted_readiness import _hosted_database_check
                from backend.app import main, mcp_tools, standalone_server, storage
                from backend.app.obsidian_writeback import WRITEBACK_DIRNAME, render_readme
                from backend.app.vault import CortexVault, HOME_PAGE_FILENAME

                assert APP_BRAND == "Doppl"
                assert main.app.title == "Doppl API"
                assert main.manifest()["name"] == "Doppl"
                assert "Cortex" not in standalone_server.ROOT_HTML
                assert "Cortex" not in standalone_server._capture_page()
                assert "Cortex" not in json.dumps(mcp_tools.TOOLS)
                assert "Cortex" not in json.dumps(storage.SOURCE_CONNECTOR_CATALOG)
                assert "Cortex" not in json.dumps(storage.CONNECTOR_SETUP_GUIDES)
                assert "Cortex" not in render_context_block(profile={"sections": []})
                assert WRITEBACK_DIRNAME == "Doppl"
                assert "Cortex" not in render_readme()
                assert HOME_PAGE_FILENAME == "Doppl — Start Here.md"
                subject, body = render_auth_email(
                    "email_verify",
                    "reviewer@example.com",
                    "token",
                    app_url="https://api.example.test",
                )
                assert "Doppl" in subject and "Doppl" in body
                assert "Cortex" not in subject + body
                readiness = _hosted_database_check(False, "", "sharded_sqlite", "local")
                assert "Doppl" in readiness["detail"] and "Cortex" not in readiness["detail"]

                vault = CortexVault(Path(__import__("os").environ["CORTEX_VAULT_PATH"]), Path(__import__("os").environ["CORTEX_DB_PATH"]))
                vault.ensure()
                rendered = (vault.root / "README.md").read_text(encoding="utf-8")
                manifest = json.loads((vault.root / "manifest.json").read_text(encoding="utf-8"))
                assert "Cortex" not in rendered
                assert manifest["name"] == "Doppl Vault"
                """
            )
            result = subprocess.run(
                [sys.executable, "-c", code],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class HostedUpdateContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.script = (ROOT / "deploy" / "update.sh").read_text(encoding="utf-8")

    def test_update_installs_current_service_and_backup_assets(self) -> None:
        for path in (
            "deploy/systemd/cortex-api.service",
            "deploy/systemd/cortex-worker.service",
            "deploy/systemd/cortex-backup.service",
            "deploy/systemd/cortex-backup.timer",
            "deploy/backup.sh",
        ):
            self.assertIn(f'$NEW_RELEASE/{path}', self.script)
        self.assertIn("systemctl daemon-reload", self.script)
        self.assertIn("systemctl enable --now cortex-backup.timer", self.script)

    def test_runtime_units_are_transactional(self) -> None:
        backup = 'systemctl start cortex-backup.service'
        runtime_install = 'echo "==> Installing runtime service contracts"'
        switch = 'echo "==> Switching current -> $NEW_RELEASE"'
        self.assertIn("restore_runtime_units", self.script)
        self.assertIn("rollback_on_error", self.script)
        self.assertLess(self.script.index(backup), self.script.index(runtime_install))
        self.assertLess(self.script.index(runtime_install), self.script.index(switch))

    def test_release_code_remains_root_owned(self) -> None:
        self.assertIn('chown -R root:root "$NEW_RELEASE"', self.script)
        self.assertNotIn('chown -R cortex:cortex "$NEW_RELEASE"', self.script)

    def test_update_bootstraps_encrypted_backup_configuration(self) -> None:
        self.assertIn("apt-get install -y -qq age", self.script)
        self.assertIn("BACKUP_AGE_RECIPIENT", self.script)
        self.assertIn("/etc/cortex/backup-age.key", self.script)

    def test_update_aborts_when_the_predeploy_backup_fails(self) -> None:
        backup = "systemctl start cortex-backup.service"
        install_requirements = 'echo "==> Installing requirements"'
        self.assertIn(backup, self.script)
        self.assertNotIn("backup failed (continuing", self.script)
        self.assertLess(self.script.index(backup), self.script.index(install_requirements))


if __name__ == "__main__":
    unittest.main()

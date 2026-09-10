"""Exercise replacement and rollback only inside disposable test directories."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from desktop.opsnest_setup import replace_program_files


class InstallerSafetyTests(unittest.TestCase):
    def test_replacement_preserves_external_business_data(self):
        with tempfile.TemporaryDirectory(prefix="opsnest-install-test-") as work:
            root = Path(work)
            payload = root / "payload"
            payload.mkdir()
            (payload / "OpsNest.exe").write_bytes(b"new-test-program")
            install = root / "Programs" / "OpsNest"
            install.mkdir(parents=True)
            (install / "OpsNest.exe").write_bytes(b"old-test-program")
            (install / "obsolete.txt").write_text("old", encoding="utf-8")
            business = root / "Business"
            business.mkdir()
            (business / "database.db").write_bytes(b"keep-database")
            (business / "template.xlsx").write_bytes(b"keep-private-template")
            result = replace_program_files(payload, install)
            self.assertEqual(result.read_bytes(), b"new-test-program")
            self.assertFalse((install / "obsolete.txt").exists())
            self.assertEqual((business / "database.db").read_bytes(), b"keep-database")
            self.assertEqual((business / "template.xlsx").read_bytes(), b"keep-private-template")

    def test_failed_activation_restores_previous_program(self):
        with tempfile.TemporaryDirectory(prefix="opsnest-rollback-test-") as work:
            root = Path(work)
            payload = root / "payload"
            payload.mkdir()
            (payload / "OpsNest.exe").write_bytes(b"new-test-program")
            install = root / "Programs" / "OpsNest"
            install.mkdir(parents=True)
            (install / "OpsNest.exe").write_bytes(b"old-test-program")
            original_rename = Path.rename

            def fail_stage(path, target):
                if path.name == ".OpsNest-update-stage":
                    raise OSError("simulated activation failure")
                return original_rename(path, target)

            with patch.object(Path, "rename", fail_stage):
                with self.assertRaisesRegex(OSError, "simulated"):
                    replace_program_files(payload, install)
            self.assertEqual((install / "OpsNest.exe").read_bytes(), b"old-test-program")


if __name__ == "__main__":
    unittest.main()

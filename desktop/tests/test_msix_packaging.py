from __future__ import annotations

import unittest
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class MsixPackagingTests(unittest.TestCase):
    def test_manifest_is_well_formed_full_trust_template(self) -> None:
        manifest = ROOT / "msix" / "AppxManifest.xml.template"
        root = ET.fromstring(manifest.read_text(encoding="utf-8"))
        namespace = "{http://schemas.microsoft.com/appx/manifest/foundation/windows10}"
        identity = root.find(f"{namespace}Identity")
        application = root.find(f"{namespace}Applications/{namespace}Application")
        capability = root.find(
            ".//{http://schemas.microsoft.com/appx/manifest/foundation/windows10/restrictedcapabilities}Capability"
        )
        self.assertIsNotNone(identity)
        self.assertEqual(identity.get("Name"), "{{IDENTITY_NAME}}")
        self.assertEqual(identity.get("Publisher"), "{{PUBLISHER}}")
        self.assertEqual(identity.get("Version"), "{{VERSION}}")
        self.assertIsNotNone(application)
        self.assertEqual(application.get("Executable"), "OpsNest.exe")
        self.assertEqual(application.get("EntryPoint"), "Windows.FullTrustApplication")
        self.assertIsNotNone(capability)
        self.assertEqual(capability.get("Name"), "runFullTrust")

    def test_msix_build_requires_partner_identity_and_optional_signature(self) -> None:
        script = (ROOT / "build_msix.ps1").read_text(encoding="utf-8")
        self.assertIn("[string]$IdentityName", script)
        self.assertIn("[string]$Publisher", script)
        self.assertIn("makeappx.exe", script)
        self.assertIn("-RequireSignature:$RequireSignature", script)
        self.assertIn("AppxManifest.xml.template", script)

    def test_store_runbook_does_not_claim_unsigned_direct_release_is_ready(self) -> None:
        runbook = (ROOT / "MS_STORE_RELEASE.md").read_text(encoding="utf-8")
        self.assertIn("Authenticode-sign", runbook)
        self.assertIn("Partner Center", runbook)
        self.assertIn("Windows App Certification Kit", runbook)
        self.assertIn("must be completed by the authorized company owner", runbook)


if __name__ == "__main__":
    unittest.main()

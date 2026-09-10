"""Founder package rights stay workspace-bound; roles and ordinary caps remain intact."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import delta_fakture_core as core
from opsnest_cloud_client import OpsNestCloudClient, CloudApiError


class FounderAccessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="opsnest-founder-test-")
        self.env = patch.dict(os.environ, {"DELTA_FAKTURE_ROOT": self.temp.name})
        self.env.start()
        core._root_dir_cache = None
        self.path = Path(self.temp.name) / "Data" / "qa.db"
        self.db = core.Database(self.path)

    def tearDown(self):
        self.db.close()
        self.env.stop()
        core._root_dir_cache = None
        self.temp.cleanup()

    def grant(self, **overrides):
        payload = dict(status="active", plan_code="pro", billing_provider="opsnest_cloud", access_source="founder")
        payload.update(overrides)
        self.db.apply_subscription_update(**payload)

    def test_persists_across_restart_and_revokes_on_regular_license(self):
        self.grant()
        self.db.close()
        self.db = core.Database(self.path)
        self.assertEqual(self.db.get_subscription()["access_source"], "founder")
        self.assertTrue(all(x is None for x in self.db.plan_usage()["limits"].values()))
        self.db.apply_subscription_update(status="active", plan_code="pro", billing_provider="opsnest_cloud")
        self.assertEqual(self.db.get_subscription()["access_source"], "subscription")
        self.assertEqual(self.db.plan_usage()["limits"]["seats"], 20)

    def test_unconfirmed_or_inactive_marker_cannot_grant_founder(self):
        for overrides in ({"billing_provider":""}, {"status":"expired"}, {"plan_code":"starter"}):
            self.grant(**overrides)
            self.assertEqual(self.db.get_subscription()["access_source"], "subscription")

    def test_seat_limit_only_removed_for_founder(self):
        self.grant()
        with patch.object(self.db, "_backup_before_change"):
            for i in range(25):
                self.db.save_team_member(dict(email=f"qa{i}@example.test", display_name=f"Member {i}", role="member"))
        self.db.assert_team_member_allowed()
        self.grant(access_source="subscription")
        with self.assertRaises(core.PlanLimitError):
            self.db.assert_team_member_allowed()

    def test_sync_preserves_device_entitlement_but_never_grants_it_to_recipient(self):
        self.grant()
        snapshot = self.db.build_cloud_sync_snapshot()
        self.db.apply_cloud_sync_snapshot(snapshot["snapshot_b64"], snapshot["sha256"])
        self.assertEqual(self.db.get_subscription()["access_source"], "founder")
        self.grant(access_source="subscription")
        self.db.apply_cloud_sync_snapshot(snapshot["snapshot_b64"], snapshot["sha256"])
        self.assertEqual(self.db.get_subscription()["access_source"], "subscription")
        self.grant()
        self.db.clear_cloud_member_session()
        self.assertEqual(self.db.get_subscription()["access_source"], "subscription")

    def test_license_is_bound_to_current_workspace(self):
        from delta_fakture_app import MainApp
        app = SimpleNamespace(db=self.db)
        with self.assertRaises(CloudApiError):
            MainApp._apply_online_license_data(app, dict(workspace_id="different", status="active", plan_code="pro", access_source="founder"))
        self.assertNotEqual(self.db.get_subscription()["access_source"], "founder")

    def test_ai_client_uses_only_the_selected_authentication_path(self):
        client = OpsNestCloudClient("https://example.test")
        with patch.object(client, "_request", return_value={}) as request:
            client.financial_advice(workspace_id="qa", workspace_token="", member_id="owner", member_token="session", summary={})
            self.assertEqual(request.call_args.args[0], "/v1/team/ai/financial-advice")
            self.assertEqual(request.call_args.kwargs["headers"]["X-OpsNest-Member"], "owner")
            client.financial_advice(workspace_id="qa", workspace_token="legacy", summary={})
            self.assertEqual(request.call_args.args[0], "/v1/ai/financial-advice")
            self.assertNotIn("X-OpsNest-Member", request.call_args.kwargs["headers"])

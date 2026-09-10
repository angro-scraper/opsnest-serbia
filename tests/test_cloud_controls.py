"""Regression checks for the cloud controls that protect shared workspaces."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
import uuid
import base64
import hashlib
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


_SOURCE_ROOT = Path(__file__).resolve().parents[1]
_DATABASE_PATH = Path(tempfile.gettempdir()) / f"opsnest-cloud-controls-{uuid.uuid4().hex}.sqlite"
os.environ["DATABASE_URL"] = "sqlite:///" + _DATABASE_PATH.as_posix()
os.environ["APP_SIGNING_SECRET"] = "test-only-workspace-audit-secret"
os.environ["WORKSPACE_SNAPSHOT_ENCRYPTION_SECRET"] = "test-only-workspace-snapshot-encryption-secret"
os.environ["APP_ENV"] = "development"
sys.path.insert(0, str(_SOURCE_ROOT))

from fastapi import HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from opsnest_cloud.database import (  # noqa: E402
    CountryPackControl,
    MemberSession,
    SessionLocal,
    Workspace,
    WorkspaceAuditEvent,
    WorkspaceFinancialOverview,
    WorkspaceMember,
    WorkspaceSyncSnapshot,
    TeamInvitation,
    WorkspaceDocument,
    WorkflowComment,
    WorkflowItem,
    create_schema,
    engine,
)
from opsnest_cloud.main import (  # noqa: E402
    CountryPackControlUpdate,
    MemberContext,
    _member_dependency,
    _new_member_session,
    _record_audit,
    _verify_workspace_audit_chain,
    _workspace_overview,
    export_team_audit_evidence,
    FinancialOverviewUpload,
    get_workspace_financial_overview,
    list_workspace_documents,
    get_workspace_control_brief,
    app,
    download_team_snapshot,
    get_country_pack_readiness,
    list_team_sessions,
    revoke_team_session,
    update_workflow_item,
    upload_team_snapshot,
    upload_workspace_financial_overview,
    UploadSyncSnapshot,
    WorkflowItemUpdate,
    update_country_pack_readiness,
    TeamLogin,
    AcceptTeamInvitation,
    accept_team_invitation,
    team_login,
    team_license_status,
    _TEAM_LOGIN_LIMIT,
    _team_login_attempts,
    _team_login_lock,
)
from opsnest_cloud.document_storage import (  # noqa: E402
    document_storage_status,
    document_storage_readiness,
    require_document_storage_ready,
    safe_filename,
    valid_document_signature,
)
from opsnest_cloud import document_storage  # noqa: E402
from opsnest_cloud.desktop_release import FALLBACK_RELEASE, current_desktop_release  # noqa: E402
from opsnest_cloud.security import secret_hash  # noqa: E402
from opsnest_cloud.time_utils import utc_now  # noqa: E402


class CloudControlTests(unittest.TestCase):
    def test_founder_requires_verified_server_allowlist_and_can_be_revoked(self):
        from opsnest_cloud import services
        from opsnest_plans import PLAN_CATALOG
        workspace = Workspace(id=str(uuid.uuid4()), owner_email=" Founder@Example.Test ",
                              plan_code="starter", subscription_status="expired")
        with patch.object(services, "settings", SimpleNamespace(founder_workspace_emails=("founder@example.test",))):
            self.assertEqual(services.effective_license(workspace)["access_source"], "subscription")
            workspace.email_verified_at = utc_now()
            license_data = services.effective_license(workspace)
            self.assertEqual(license_data["plan_name"], "Founder")
            self.assertTrue(license_data["can_write"])
            self.assertTrue(all(value is None for value in license_data["limits"].values()))
            self.assertTrue(license_data["ai_advisor"]["unlimited"])
            self.assertIsNone(license_data["ai_advisor"]["requests_remaining"])
            workspace.owner_email = "customer@example.test"
            workspace.plan_code = "pro"
            workspace.subscription_status = "active"
            self.assertEqual(services.effective_license(workspace)["limits"]["seats"], 20)
        with patch.object(services, "settings", SimpleNamespace(founder_workspace_emails=())):
            workspace.owner_email = "founder@example.test"
            self.assertFalse(services.effective_license(workspace)["package_unlimited"])
        self.assertNotIn("founder", PLAN_CATALOG)

    def test_founder_ai_has_no_monthly_package_cap_but_keeps_usage_and_role_controls(self):
        from opsnest_cloud import services, main
        workspace = Workspace(id=str(uuid.uuid4()), owner_email="founder@example.test", email_verified_at=utc_now(),
                              ai_advisor_tier="ai_pro", ai_advisor_status="active",
                              ai_advisor_period_started_at=utc_now(), ai_advisor_requests_used=300)
        payload = main.FinancialAdviceRequest(invoice_count=0, issued_total=0, paid_total=0, outstanding_total=0,
                    overdue_total=0, output_vat_total=0, collection_rate_percent=0, overdue_share_percent=0, top_debtor_share_percent=0)
        db = MagicMock()
        db.get.return_value = workspace
        context = main.MemberContext(workspace=workspace, member=SimpleNamespace(role="owner"), session=None)
        with patch.object(services, "settings", SimpleNamespace(founder_workspace_emails=("founder@example.test",))), \
             patch.object(main, "_generate_ai_financial_advice", return_value="Test advice") as generate, \
             patch.object(main, "_limit_ai_advice") as rate_limit, patch.object(main, "_record_audit"):
            result = main.team_ai_financial_advice(payload, context, db)
            self.assertIsNone(result["requests_remaining"])
            self.assertEqual(workspace.ai_advisor_requests_used, 301)
            rate_limit.assert_called_once_with(workspace.id)
            for role in ("member", "accountant", "administrator"):
                context.member.role = role
                with self.assertRaises(HTTPException) as caught:
                    main.team_ai_financial_advice(payload, context, db)
                self.assertEqual(caught.exception.status_code, 403)
            self.assertEqual(generate.call_count, 1)
            workspace.owner_email = "regular@example.test"
            with self.assertRaises(HTTPException) as caught:
                services.consume_ai_advisor_request(workspace)
            self.assertEqual(caught.exception.status_code, 429)
        with TestClient(app) as client:
            response = client.post("/v1/team/ai/financial-advice", json=payload.model_dump(), headers={
                "X-OpsNest-Workspace": str(uuid.uuid4()), "X-OpsNest-Member": str(uuid.uuid4()), "Authorization": "Bearer invalid"})
            self.assertEqual(response.status_code, 401)

    def test_founder_invitation_over_twenty_seats_and_checkout_guard(self):
        from opsnest_cloud import services, main
        workspace = Workspace(id=str(uuid.uuid4()), owner_email="founder@example.test", email_verified_at=utc_now(),
                              plan_code="pro", subscription_status="active", company_name="QA")
        context = main.MemberContext(workspace=workspace, member=SimpleNamespace(id="qa-owner", role="owner"), session=None)
        db = MagicMock()
        db.scalar.return_value = None
        db.scalars.return_value.all.return_value = []
        invitation = main.TeamInvitationRequest(email="new@example.test", display_name="QA Member", role="accountant")
        with patch.object(services, "settings", SimpleNamespace(founder_workspace_emails=("founder@example.test",))), \
             patch.object(main, "_team_seats_used", return_value=1000), \
             patch.object(main, "send_team_invitation") as send, patch.object(main, "_record_audit"):
            main.invite_team_member(invitation, context, db)
            send.assert_called_once()
            with self.assertRaises(HTTPException) as caught:
                main.create_checkout_session("pro", workspace)
            self.assertEqual(caught.exception.status_code, 409)
            workspace.owner_email = "regular@example.test"
            with self.assertRaises(HTTPException) as caught:
                main.invite_team_member(invitation, context, db)
            self.assertEqual(caught.exception.status_code, 409)

    @classmethod
    def setUpClass(cls) -> None:
        create_schema()

    @classmethod
    def tearDownClass(cls) -> None:
        engine.dispose()
        if _DATABASE_PATH.exists():
            _DATABASE_PATH.unlink()

    def test_workspace_audit_chain_detects_changed_event(self) -> None:
        db = SessionLocal()
        workspace_id = str(uuid.uuid4())
        try:
            _record_audit(db, workspace_id=workspace_id, action="workflow.item_created", details={"priority": "high"})
            _record_audit(db, workspace_id=workspace_id, action="workflow.item_updated", details={"to_status": "done"})
            db.commit()
            self.assertTrue(_verify_workspace_audit_chain(db, workspace_id)["ok"])

            first_event = db.scalar(
                select(WorkspaceAuditEvent)
                .where(WorkspaceAuditEvent.workspace_id == workspace_id)
                .order_by(WorkspaceAuditEvent.created_at.asc(), WorkspaceAuditEvent.id.asc())
            )
            assert first_event is not None
            first_event.details_json = '{"priority":"changed"}'
            db.commit()
            self.assertFalse(_verify_workspace_audit_chain(db, workspace_id)["ok"])
        finally:
            db.close()

    def test_workspace_responses_are_not_cacheable_or_frameable(self) -> None:
        with TestClient(app) as client:
            response = client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(response.headers["x-frame-options"], "DENY")
        self.assertIn("frame-ancestors 'none'", response.headers["content-security-policy"])

    def test_team_session_can_read_effective_license_without_workspace_token(self) -> None:
        db = SessionLocal()
        workspace_id = str(uuid.uuid4())
        member_id = str(uuid.uuid4())
        try:
            workspace = Workspace(
                id=workspace_id,
                owner_email="license-owner@example.test",
                company_name="License Company",
                subscription_status="active",
                plan_code="pro",
            )
            member = WorkspaceMember(
                id=member_id,
                workspace_id=workspace_id,
                email="license-owner@example.test",
                display_name="License owner",
                role="owner",
                status="active",
            )
            db.add_all([workspace, member])
            db.flush()
            session_data = _new_member_session(db, member, "QA desktop")
            db.commit()
            context = _member_dependency(
                db=db,
                workspace_id=workspace_id,
                team_member_id=member_id,
                authorization=f"Bearer {session_data['member_token']}",
            )
            license_data = team_license_status(context)
            self.assertEqual(license_data["effective_plan_code"], "pro")
            self.assertTrue(license_data["can_write"])
        finally:
            db.close()

    def test_team_login_rate_limit_blocks_repeated_wrong_passwords_without_exposing_account(self) -> None:
        db = SessionLocal()
        workspace_id = str(uuid.uuid4())
        member_id = str(uuid.uuid4())
        request = SimpleNamespace(client=SimpleNamespace(host="203.0.113.44"))
        payload = TeamLogin(
            workspace_id=workspace_id,
            email="login-owner@example.test",
            password="wrong-password",
            device_name="QA desktop",
        )
        try:
            workspace = Workspace(
                id=workspace_id, owner_email="login-owner@example.test",
                company_name="Test Company", subscription_status="active",
            )
            member = WorkspaceMember(
                id=member_id, workspace_id=workspace_id, email="login-owner@example.test",
                display_name="Login owner", role="owner", status="active", password_hash="not-the-password",
            )
            db.add_all([workspace, member])
            db.commit()
            for _ in range(_TEAM_LOGIN_LIMIT):
                with self.assertRaises(HTTPException) as rejected:
                    team_login(payload, request, db)
                self.assertEqual(rejected.exception.status_code, 401)
            with self.assertRaises(HTTPException) as rate_limited:
                team_login(payload, request, db)
            self.assertEqual(rate_limited.exception.status_code, 429)
            self.assertIn("Wait 15 minutes", rate_limited.exception.detail)
        finally:
            with _team_login_lock:
                _team_login_attempts.clear()
            db.close()

    def test_only_latest_invitation_code_can_be_tried_five_times(self) -> None:
        db = SessionLocal()
        workspace_id = str(uuid.uuid4())
        member_id = str(uuid.uuid4())
        payload = AcceptTeamInvitation(
            email="invitee@example.test", code="000000", password="new-safe-password", device_name="QA desktop",
        )
        try:
            workspace = Workspace(
                id=workspace_id, owner_email="invite-owner@example.test",
                company_name="Test Company", subscription_status="active",
            )
            member = WorkspaceMember(
                id=member_id, workspace_id=workspace_id, email="invitee@example.test",
                display_name="Invitee", role="operator", status="invited",
            )
            invitation = TeamInvitation(
                id=str(uuid.uuid4()), workspace_id=workspace_id, email="invitee@example.test",
                display_name="Invitee", role="operator", code_hash=secret_hash("123456"),
                expires_at=utc_now() + timedelta(hours=1), attempts=0,
            )
            db.add_all([workspace, member, invitation])
            db.commit()
            for _ in range(5):
                with self.assertRaises(HTTPException) as rejected:
                    accept_team_invitation(payload, db)
                self.assertEqual(rejected.exception.status_code, 400)
            with self.assertRaises(HTTPException) as rate_limited:
                accept_team_invitation(payload, db)
            self.assertEqual(rate_limited.exception.status_code, 429)
            self.assertEqual(db.get(TeamInvitation, invitation.id).attempts, 5)
        finally:
            db.close()

    def test_document_storage_validates_real_file_signatures_and_safe_names(self) -> None:
        """The private bucket must never trust a browser supplied MIME type/name."""
        self.assertTrue(valid_document_signature("application/pdf", b"%PDF-1.7\ncontent"))
        self.assertTrue(valid_document_signature("image/jpeg", b"\xff\xd8\xff\xe0jpeg"))
        self.assertTrue(valid_document_signature("image/png", b"\x89PNG\r\n\x1a\npng"))
        self.assertFalse(valid_document_signature("application/pdf", b"<html>not-a-pdf</html>"))
        self.assertFalse(valid_document_signature("image/png", b"%PDF-1.7"))
        self.assertFalse(valid_document_signature("text/plain", b"%PDF-1.7"))
        self.assertEqual(safe_filename("../../supplier invoice?.pdf"), "supplier-invoice-.pdf")
        self.assertEqual(safe_filename("..\\..\\statement.pdf"), "statement.pdf")

    def test_document_storage_readiness_requires_an_accessible_private_bucket(self) -> None:
        configured = SimpleNamespace(document_storage_enabled=True, document_storage_bucket="opsnest-private")
        client = MagicMock()
        with patch.object(document_storage, "settings", configured), patch.object(document_storage, "_client", return_value=client):
            self.assertEqual(document_storage_readiness(), "ready")
            client.head_bucket.assert_called_once_with(Bucket="opsnest-private")
        with patch.object(document_storage, "settings", configured), patch.object(document_storage, "_client", side_effect=RuntimeError("bucket unavailable")):
            self.assertEqual(document_storage_readiness(), "unavailable")
        with patch("opsnest_cloud.main.document_storage_readiness", return_value="unavailable"), TestClient(app) as client:
            response = client.get("/health/ready")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["status"], "not_ready")
        self.assertEqual(response.json()["database"], "ok")
        self.assertEqual(response.json()["document_storage"], "unavailable")

    def test_document_archive_never_advertises_or_accepts_work_when_bucket_is_unavailable(self) -> None:
        configured = SimpleNamespace(document_storage_enabled=True, document_storage_bucket="opsnest-private")
        with patch.object(document_storage, "settings", configured), patch.object(document_storage, "_client", side_effect=RuntimeError("bucket unavailable")):
            status = document_storage_status()
            self.assertTrue(status["configured"])
            self.assertFalse(status["enabled"])
            self.assertEqual(status["state"], "unavailable")
            with self.assertRaises(HTTPException) as rejected:
                require_document_storage_ready()
        self.assertEqual(rejected.exception.status_code, 503)
        self.assertIn("safely blocked", rejected.exception.detail)

    def test_workspace_never_marks_configured_but_unavailable_document_storage_ready(self) -> None:
        db = SessionLocal()
        workspace_id = str(uuid.uuid4())
        member_id = str(uuid.uuid4())
        try:
            workspace = Workspace(
                id=workspace_id, owner_email="storage-owner@example.test",
                company_name="Test Company", plan_code="pro", subscription_status="active",
            )
            owner = WorkspaceMember(
                id=member_id, workspace_id=workspace_id, email="storage-owner@example.test",
                display_name="Owner", role="owner", status="active",
            )
            session = MemberSession(
                id=str(uuid.uuid4()), workspace_id=workspace_id, member_id=member_id,
                token_hash="storage-session", expires_at=utc_now() + timedelta(days=1),
            )
            db.add_all([workspace, owner, session])
            db.commit()
            context = MemberContext(workspace=workspace, member=owner, session=session)
            with patch("opsnest_cloud.main.document_storage_readiness", return_value="unavailable"):
                overview = _workspace_overview(db, context)
            documents = next(module for module in overview["modules"] if module["key"] == "documents")
            self.assertEqual(documents["state"], "unavailable")
            self.assertIn("safely blocked", documents["detail"])
        finally:
            db.close()

    def test_desktop_update_manifest_accepts_only_trusted_current_or_newer_release_urls(self) -> None:
        expected = dict(FALLBACK_RELEASE)
        self.assertEqual(
            current_desktop_release(
                expected["latest_version"],
                expected["installer_url"],
                expected["installer_sha256"],
            ),
            expected,
        )
        self.assertEqual(
            current_desktop_release(
                "2.99.0",
                "https://opsnestone.com/downloads/OpsNest-Setup-2.99.0.exe",
                "a" * 64,
            )["latest_version"],
            "2.99.0",
        )
        self.assertEqual(
            current_desktop_release(
                "2.13.3",
                "https://opsnestone.com/downloads/OpsNest-Setup-2.13.3.exe",
                "b" * 64,
            ),
            expected,
        )
        self.assertEqual(
            current_desktop_release(
                "2.99.0",
                "https://opsnestone.com.evil.example/downloads/OpsNest-Setup-2.99.0.exe",
                "c" * 64,
            ),
            expected,
        )
        self.assertEqual(
            current_desktop_release(
                "2.99.0",
                "https://opsnestone.com/downloads/OpsNest-Setup-2.99.0.exe?redirect=1",
                "d" * 64,
            ),
            expected,
        )

    def test_readiness_checks_database_without_exposing_configuration(self) -> None:
        with TestClient(app) as client:
            response = client.get("/health/ready")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ready")
        self.assertEqual(response.json()["database"], "ok")
        self.assertNotIn("DATABASE_URL", response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_country_pack_readiness_is_accountable_not_a_compliance_claim(self) -> None:
        db = SessionLocal()
        workspace_id = str(uuid.uuid4())
        member_id = str(uuid.uuid4())
        try:
            workspace = Workspace(
                id=workspace_id,
                owner_email="readiness-owner@example.test",
                company_name="Test Company",
                country_code="RS",
                default_currency="RSD",
                subscription_status="active",
            )
            member = WorkspaceMember(
                id=member_id,
                workspace_id=workspace_id,
                email="readiness-owner@example.test",
                display_name="Owner",
                role="owner",
                status="active",
            )
            session = MemberSession(
                id=str(uuid.uuid4()),
                workspace_id=workspace_id,
                member_id=member_id,
                token_hash="test-token",
                expires_at=utc_now() + timedelta(days=1),
            )
            db.add_all([workspace, member, session])
            db.commit()
            context = MemberContext(workspace=workspace, member=member, session=session)

            result = update_country_pack_readiness(
                "e_invoice",
                CountryPackControlUpdate(status="in_review", due_date="2026-08-01", owner_member_id=member_id, note="Accountant review scheduled."),
                context,
                db,
            )
            self.assertEqual(result["control"]["status"], "in_review")
            self.assertEqual(result["control"]["owner_member_id"], member_id)
            self.assertEqual(result["control"]["due_date"], "2026-08-01")

            readiness = get_country_pack_readiness(context, db)
            self.assertIn("not a legal", readiness["disclaimer"])
            controls = {control["key"]: control for control in readiness["controls"]}
            self.assertEqual(controls["e_invoice"]["status"], "in_review")
            self.assertEqual(db.query(CountryPackControl).filter_by(workspace_id=workspace_id).count(), 1)

            project_manager = WorkspaceMember(
                id=str(uuid.uuid4()), workspace_id=workspace_id, email="readiness-project@example.test",
                display_name="Project manager", role="project_manager", status="active",
            )
            project_context = MemberContext(workspace=workspace, member=project_manager, session=session)
            self.assertFalse(get_country_pack_readiness(project_context, db)["can_manage"])
            with self.assertRaises(HTTPException) as rejected:
                update_country_pack_readiness(
                    "e_invoice",
                    CountryPackControlUpdate(status="ready", owner_member_id=member_id, note="Not permitted."),
                    project_context,
                    db,
                )
            self.assertEqual(rejected.exception.status_code, 403)

            workspace.country_code = "BG"
            db.commit()
            switched_country = get_country_pack_readiness(context, db)
            switched_controls = {control["key"]: control for control in switched_country["controls"]}
            self.assertEqual(switched_controls["e_invoice"]["status"], "not_started")
            self.assertTrue(_verify_workspace_audit_chain(db, workspace_id)["ok"])
        finally:
            db.close()

    def test_owner_can_revoke_one_device_session_with_audit(self) -> None:
        db = SessionLocal()
        workspace_id = str(uuid.uuid4())
        member_id = str(uuid.uuid4())
        try:
            workspace = Workspace(
                id=workspace_id,
                owner_email="devices-owner@example.test",
                company_name="Test Company",
                country_code="INTL",
                subscription_status="active",
            )
            owner = WorkspaceMember(
                id=member_id,
                workspace_id=workspace_id,
                email="devices-owner@example.test",
                display_name="Owner",
                role="owner",
                status="active",
            )
            current_session = MemberSession(
                id=str(uuid.uuid4()), workspace_id=workspace_id, member_id=member_id,
                token_hash="current", device_name="Owner desktop", expires_at=utc_now() + timedelta(days=1),
            )
            stale_session = MemberSession(
                id=str(uuid.uuid4()), workspace_id=workspace_id, member_id=member_id,
                token_hash="stale", device_name="Lost laptop", expires_at=utc_now() + timedelta(days=1),
            )
            db.add_all([workspace, owner, current_session, stale_session])
            db.commit()
            context = MemberContext(workspace=workspace, member=owner, session=current_session)

            before = list_team_sessions(context, db)
            self.assertEqual(len(before["sessions"]), 2)
            self.assertTrue(next(item for item in before["sessions"] if item["id"] == current_session.id)["current"])
            self.assertFalse(next(item for item in before["sessions"] if item["id"] == stale_session.id)["current"])

            self.assertTrue(revoke_team_session(stale_session.id, context, db)["ok"])
            after = list_team_sessions(context, db)
            self.assertEqual([item["id"] for item in after["sessions"]], [current_session.id])
            self.assertTrue(_verify_workspace_audit_chain(db, workspace_id)["ok"])
        finally:
            db.close()

    def test_team_sync_is_limited_to_finance_roles_and_retries_are_idempotent(self) -> None:
        db = SessionLocal()
        workspace_id = str(uuid.uuid4())
        owner_id = str(uuid.uuid4())
        accountant_id = str(uuid.uuid4())
        operator_id = str(uuid.uuid4())
        try:
            workspace = Workspace(
                id=workspace_id,
                owner_email="sync-owner@example.test",
                company_name="Test Company",
                plan_code="pro",
                subscription_status="active",
            )
            owner = WorkspaceMember(
                id=owner_id, workspace_id=workspace_id, email="sync-owner@example.test",
                display_name="Owner", role="owner", status="active",
            )
            accountant = WorkspaceMember(
                id=accountant_id, workspace_id=workspace_id, email="accountant@example.test",
                display_name="Accountant", role="accountant", status="active",
            )
            operator = WorkspaceMember(
                id=operator_id, workspace_id=workspace_id, email="operator@example.test",
                display_name="Operator", role="operator", status="active",
            )
            owner_session = MemberSession(
                id=str(uuid.uuid4()), workspace_id=workspace_id, member_id=owner_id,
                token_hash="sync-owner", expires_at=utc_now() + timedelta(days=1),
            )
            accountant_session = MemberSession(
                id=str(uuid.uuid4()), workspace_id=workspace_id, member_id=accountant_id,
                token_hash="sync-accountant", expires_at=utc_now() + timedelta(days=1),
            )
            operator_session = MemberSession(
                id=str(uuid.uuid4()), workspace_id=workspace_id, member_id=operator_id,
                token_hash="sync-operator", expires_at=utc_now() + timedelta(days=1),
            )
            db.add_all([workspace, owner, accountant, operator, owner_session, accountant_session, operator_session])
            db.commit()
            owner_context = MemberContext(workspace=workspace, member=owner, session=owner_session)
            accountant_context = MemberContext(workspace=workspace, member=accountant, session=accountant_session)
            operator_context = MemberContext(workspace=workspace, member=operator, session=operator_session)
            raw_snapshot = b"encrypted-desktop-workspace-payload"
            checksum = hashlib.sha256(raw_snapshot).hexdigest()
            payload = UploadSyncSnapshot(
                expected_revision=0,
                snapshot_b64=base64.b64encode(raw_snapshot).decode("ascii"),
                sha256=checksum,
                financial_audit_hash="a" * 64,
                financial_audit_count=7,
            )

            first_upload = upload_team_snapshot(payload, owner_context, db)
            self.assertEqual(first_upload["revision"], 1)
            self.assertNotIn("unchanged", first_upload)
            retry_upload = upload_team_snapshot(payload, owner_context, db)
            self.assertEqual(retry_upload, {"ok": True, "revision": 1, "sha256": checksum, "unchanged": True})
            stored_snapshot = db.query(WorkspaceSyncSnapshot).filter_by(workspace_id=workspace_id).one()
            self.assertEqual(stored_snapshot.revision, 1)
            self.assertTrue(stored_snapshot.snapshot_b64.startswith("v1:"))
            self.assertNotEqual(stored_snapshot.snapshot_b64, payload.snapshot_b64)
            self.assertEqual(stored_snapshot.financial_audit_hash, "a" * 64)
            self.assertEqual(stored_snapshot.financial_audit_count, 7)

            downloaded = download_team_snapshot(accountant_context, db)
            self.assertEqual(downloaded["sha256"], checksum)
            self.assertEqual(downloaded["snapshot_b64"], payload.snapshot_b64)
            with self.assertRaises(HTTPException) as rejected:
                download_team_snapshot(operator_context, db)
            self.assertEqual(rejected.exception.status_code, 403)
            self.assertTrue(_verify_workspace_audit_chain(db, workspace_id)["ok"])
            audit_event = db.scalars(
                select(WorkspaceAuditEvent)
                .where(WorkspaceAuditEvent.workspace_id == workspace_id, WorkspaceAuditEvent.action == "team.sync_uploaded")
                .order_by(WorkspaceAuditEvent.created_at.desc())
            ).first()
            self.assertIn('"financial_audit_count":7', audit_event.details_json if audit_event else "")
        finally:
            db.close()

    def test_team_sync_rejects_an_invalid_financial_audit_anchor(self) -> None:
        db = SessionLocal()
        workspace_id = str(uuid.uuid4())
        member_id = str(uuid.uuid4())
        try:
            workspace = Workspace(
                id=workspace_id, owner_email="anchor-owner@example.test",
                company_name="Test Company", plan_code="pro", subscription_status="active",
            )
            owner = WorkspaceMember(
                id=member_id, workspace_id=workspace_id, email="anchor-owner@example.test",
                display_name="Owner", role="owner", status="active",
            )
            session = MemberSession(
                id=str(uuid.uuid4()), workspace_id=workspace_id, member_id=member_id,
                token_hash="anchor-owner", expires_at=utc_now() + timedelta(days=1),
            )
            db.add_all([workspace, owner, session])
            db.commit()
            raw_snapshot = b"anchor-check"
            payload = UploadSyncSnapshot(
                expected_revision=0,
                snapshot_b64=base64.b64encode(raw_snapshot).decode("ascii"),
                sha256=hashlib.sha256(raw_snapshot).hexdigest(),
                financial_audit_hash="not-a-valid-anchor",
                financial_audit_count=1,
            )
            with self.assertRaises(HTTPException) as rejected:
                upload_team_snapshot(payload, MemberContext(workspace=workspace, member=owner, session=session), db)
            self.assertEqual(rejected.exception.status_code, 422)
        finally:
            db.close()

    def test_financial_overview_is_visible_and_writable_only_to_finance_roles(self) -> None:
        db = SessionLocal()
        workspace_id = str(uuid.uuid4())
        accountant_id = str(uuid.uuid4())
        project_manager_id = str(uuid.uuid4())
        try:
            workspace = Workspace(
                id=workspace_id,
                owner_email="finance-owner@example.test",
                company_name="Test Company",
                plan_code="pro",
                subscription_status="active",
            )
            accountant = WorkspaceMember(
                id=accountant_id, workspace_id=workspace_id, email="finance-accountant@example.test",
                display_name="Accountant", role="accountant", status="active",
            )
            project_manager = WorkspaceMember(
                id=project_manager_id, workspace_id=workspace_id, email="finance-project@example.test",
                display_name="Project manager", role="project_manager", status="active",
            )
            accountant_session = MemberSession(
                id=str(uuid.uuid4()), workspace_id=workspace_id, member_id=accountant_id,
                token_hash="finance-accountant", expires_at=utc_now() + timedelta(days=1),
            )
            project_session = MemberSession(
                id=str(uuid.uuid4()), workspace_id=workspace_id, member_id=project_manager_id,
                token_hash="finance-project", expires_at=utc_now() + timedelta(days=1),
            )
            db.add_all([workspace, accountant, project_manager, accountant_session, project_session])
            db.commit()
            accountant_context = MemberContext(workspace=workspace, member=accountant, session=accountant_session)
            project_context = MemberContext(workspace=workspace, member=project_manager, session=project_session)
            payload = FinancialOverviewUpload(currency="EUR", income_net=1250, expense_net=250, profit_net=1000)

            result = upload_workspace_financial_overview(payload, accountant_context, db)
            self.assertEqual(result["currency"], "EUR")
            self.assertEqual(get_workspace_financial_overview(accountant_context, db)["summary"]["profit_net"], 1000)
            with self.assertRaises(HTTPException) as read_rejected:
                get_workspace_financial_overview(project_context, db)
            self.assertEqual(read_rejected.exception.status_code, 403)
            with self.assertRaises(HTTPException) as write_rejected:
                upload_workspace_financial_overview(payload, project_context, db)
            self.assertEqual(write_rejected.exception.status_code, 403)
            self.assertTrue(_verify_workspace_audit_chain(db, workspace_id)["ok"])
        finally:
            db.close()

    def test_document_archive_separates_finance_from_project_document_access(self) -> None:
        db = SessionLocal()
        workspace_id = str(uuid.uuid4())
        accountant_id = str(uuid.uuid4())
        project_manager_id = str(uuid.uuid4())
        operator_id = str(uuid.uuid4())
        try:
            workspace = Workspace(
                id=workspace_id, owner_email="documents-owner@example.test",
                company_name="Test Company", plan_code="pro", subscription_status="active",
            )
            accountant = WorkspaceMember(
                id=accountant_id, workspace_id=workspace_id, email="documents-accountant@example.test",
                display_name="Accountant", role="accountant", status="active",
            )
            project_manager = WorkspaceMember(
                id=project_manager_id, workspace_id=workspace_id, email="documents-project@example.test",
                display_name="Project manager", role="project_manager", status="active",
            )
            operator = WorkspaceMember(
                id=operator_id, workspace_id=workspace_id, email="documents-operator@example.test",
                display_name="Operator", role="operator", status="active",
            )
            accountant_session = MemberSession(
                id=str(uuid.uuid4()), workspace_id=workspace_id, member_id=accountant_id,
                token_hash="documents-accountant", expires_at=utc_now() + timedelta(days=1),
            )
            project_session = MemberSession(
                id=str(uuid.uuid4()), workspace_id=workspace_id, member_id=project_manager_id,
                token_hash="documents-project", expires_at=utc_now() + timedelta(days=1),
            )
            operator_session = MemberSession(
                id=str(uuid.uuid4()), workspace_id=workspace_id, member_id=operator_id,
                token_hash="documents-operator", expires_at=utc_now() + timedelta(days=1),
            )
            invoice = WorkspaceDocument(
                id=str(uuid.uuid4()), workspace_id=workspace_id, uploaded_by_member_id=accountant_id,
                document_type="invoice", original_filename="supplier-invoice.pdf", content_type="application/pdf",
                byte_size=42, sha256="a" * 64, storage_key=f"test/{uuid.uuid4()}",
            )
            contract = WorkspaceDocument(
                id=str(uuid.uuid4()), workspace_id=workspace_id, uploaded_by_member_id=project_manager_id,
                document_type="contract", original_filename="project-contract.pdf", content_type="application/pdf",
                byte_size=42, sha256="b" * 64, storage_key=f"test/{uuid.uuid4()}",
            )
            db.add_all([
                workspace, accountant, project_manager, operator,
                accountant_session, project_session, operator_session, invoice, contract,
            ])
            db.commit()
            accountant_context = MemberContext(workspace=workspace, member=accountant, session=accountant_session)
            project_context = MemberContext(workspace=workspace, member=project_manager, session=project_session)
            operator_context = MemberContext(workspace=workspace, member=operator, session=operator_session)

            accountant_documents = list_workspace_documents(accountant_context, db)
            self.assertEqual({item["original_filename"] for item in accountant_documents["documents"]}, {"supplier-invoice.pdf", "project-contract.pdf"})
            self.assertEqual(set(accountant_documents["permissions"]["visible_document_types"]), {"invoice", "receipt", "contract", "statement", "other"})
            project_documents = list_workspace_documents(project_context, db)
            self.assertEqual([item["original_filename"] for item in project_documents["documents"]], ["project-contract.pdf"])
            self.assertEqual(project_documents["permissions"]["visible_document_types"], ["contract", "other"])
            with self.assertRaises(HTTPException) as rejected:
                list_workspace_documents(operator_context, db)
            self.assertEqual(rejected.exception.status_code, 403)
        finally:
            db.close()

    def test_audit_evidence_export_verifies_chain_and_omits_event_details(self) -> None:
        db = SessionLocal()
        workspace_id = str(uuid.uuid4())
        member_id = str(uuid.uuid4())
        try:
            workspace = Workspace(
                id=workspace_id,
                owner_email="audit-owner@example.test",
                company_name="Test Company",
                country_code="INTL",
                subscription_status="active",
            )
            owner = WorkspaceMember(
                id=member_id,
                workspace_id=workspace_id,
                email="audit-owner@example.test",
                display_name="Audit Owner",
                role="owner",
                status="active",
            )
            session = MemberSession(
                id=str(uuid.uuid4()), workspace_id=workspace_id, member_id=member_id,
                token_hash="audit-session", expires_at=utc_now() + timedelta(days=1),
            )
            db.add_all([workspace, owner, session])
            _record_audit(
                db,
                workspace_id=workspace_id,
                actor_member_id=member_id,
                action="workflow.item_created",
                details={"private_note": "must-not-appear-in-export"},
            )
            db.commit()

            response = export_team_audit_evidence(MemberContext(workspace=workspace, member=owner, session=session), db)
            content = response.body.decode("utf-8-sig")
            self.assertTrue(str(response.media_type).startswith("text/csv"))
            self.assertIn("integrity,verified", content)
            self.assertIn("workflow.item_created", content)
            self.assertIn("team.audit_evidence_exported", content)
            self.assertNotIn("must-not-appear-in-export", content)
            self.assertTrue(_verify_workspace_audit_chain(db, workspace_id)["ok"])
        finally:
            db.close()

    def test_control_brief_flags_overdue_unowned_blocked_and_stale_reviews(self) -> None:
        db = SessionLocal()
        workspace_id = str(uuid.uuid4())
        member_id = str(uuid.uuid4())
        yesterday = (utc_now() - timedelta(days=1)).date().isoformat()
        try:
            workspace = Workspace(
                id=workspace_id,
                owner_email="brief-owner@example.test",
                company_name="Test Company",
                country_code="RS",
                subscription_status="active",
            )
            owner = WorkspaceMember(
                id=member_id,
                workspace_id=workspace_id,
                email="brief-owner@example.test",
                display_name="Brief Owner",
                role="owner",
                status="active",
            )
            session = MemberSession(
                id=str(uuid.uuid4()), workspace_id=workspace_id, member_id=member_id,
                token_hash="brief-session", expires_at=utc_now() + timedelta(days=1),
            )
            overdue = WorkflowItem(
                id=str(uuid.uuid4()), workspace_id=workspace_id, title="Reconcile bank",
                status="open", priority="urgent", due_date=yesterday, assigned_member_id="",
            )
            blocked = CountryPackControl(
                id=str(uuid.uuid4()), workspace_id=workspace_id, country_code="RS",
                control_key="e_invoice", status="blocked", due_date=yesterday,
            )
            overview = WorkspaceFinancialOverview(
                workspace_id=workspace_id,
                currency="RSD",
                summary_json="{}",
                updated_at=utc_now() - timedelta(hours=25),
            )
            db.add_all([workspace, owner, session, overdue, blocked, overview])
            db.commit()

            brief = get_workspace_control_brief(MemberContext(workspace=workspace, member=owner, session=session), db)
            controls = {item["key"]: item for item in brief["items"]}
            self.assertEqual(controls["workflow_overdue"]["severity"], "attention")
            self.assertEqual(controls["unassigned_priority_work"]["count"], 1)
            self.assertEqual(controls["country_control_blocked"]["target"], "countryReadinessSection")
            self.assertIn("financial_overview_stale", controls)
            self.assertEqual(controls["team_continuity_missing_backup_admin"]["target"], "teamSection")
            self.assertIn("not a payment instruction", brief["disclaimer"])

            backup_admin = WorkspaceMember(
                id=str(uuid.uuid4()), workspace_id=workspace_id, email="backup@example.test",
                display_name="Backup administrator", role="administrator", status="active",
            )
            db.add(backup_admin)
            db.commit()
            with_backup = get_workspace_control_brief(MemberContext(workspace=workspace, member=owner, session=session), db)
            self.assertNotIn("team_continuity_missing_backup_admin", {item["key"] for item in with_backup["items"]})
        finally:
            db.close()

    def test_returned_work_requires_a_comment_and_records_the_correction(self) -> None:
        db = SessionLocal()
        workspace_id = str(uuid.uuid4())
        member_id = str(uuid.uuid4())
        try:
            workspace = Workspace(
                id=workspace_id,
                owner_email="return-owner@example.test",
                company_name="Test Company",
                subscription_status="active",
            )
            owner = WorkspaceMember(
                id=member_id,
                workspace_id=workspace_id,
                email="return-owner@example.test",
                display_name="Return Owner",
                role="owner",
                status="active",
            )
            session = MemberSession(
                id=str(uuid.uuid4()), workspace_id=workspace_id, member_id=member_id,
                token_hash="return-session", expires_at=utc_now() + timedelta(days=1),
            )
            item = WorkflowItem(
                id=str(uuid.uuid4()), workspace_id=workspace_id, title="Review supplier document",
                status="in_progress", priority="high", assigned_member_id=member_id,
            )
            db.add_all([workspace, owner, session, item])
            db.commit()
            context = MemberContext(workspace=workspace, member=owner, session=session)

            with self.assertRaises(HTTPException) as rejected:
                update_workflow_item(
                    item.id,
                    WorkflowItemUpdate(status="returned", priority="high", assigned_member_id=member_id),
                    context,
                    db,
                )
            self.assertEqual(rejected.exception.status_code, 422)
            self.assertEqual(item.status, "in_progress")

            result = update_workflow_item(
                item.id,
                WorkflowItemUpdate(
                    status="returned", priority="high", assigned_member_id=member_id,
                    comment="Please attach the supplier source document.",
                ),
                context,
                db,
            )
            self.assertEqual(result["item"]["status"], "returned")
            comment = db.scalar(
                select(WorkflowComment).where(WorkflowComment.workflow_item_id == item.id)
            )
            self.assertEqual(comment.body if comment else "", "Please attach the supplier source document.")
            self.assertTrue(_verify_workspace_audit_chain(db, workspace_id)["ok"])
        finally:
            db.close()


class PublicExperienceTests(unittest.TestCase):
    def test_download_and_update_use_identical_manifest(self):
        client = TestClient(app)
        manifest = client.get("/v1/public/desktop-update").json()
        response = client.get("/download/desktop", follow_redirects=False)
        self.assertEqual(response.status_code, 307)
        self.assertEqual(response.headers["location"], manifest["installer_url"])
        self.assertEqual(response.headers["cache-control"], "no-store")
        for page in (_SOURCE_ROOT / "public_site").glob("*.html"):
            html = page.read_text(encoding="utf-8")
            self.assertNotRegex(html, r"OpsNest-Setup-\d+\.\d+\.\d+\.exe", page.name)
        from opsnest_cloud.desktop_release import current_desktop_release, FALLBACK_RELEASE
        for url in ("https://evil.example/downloads/OpsNest-Setup-99.0.0.exe", "https://user@opsnestone.com/downloads/OpsNest-Setup-99.0.0.exe", "https://opsnestone.com:444/downloads/OpsNest-Setup-99.0.0.exe"):
            self.assertEqual(current_desktop_release("99.0.0", url, "a" * 64), FALLBACK_RELEASE)

    def test_incomplete_public_links_show_recovery_without_relaxing_validation(self):
        client = TestClient(app)
        for route in ("/activate", "/activate?workspace_id=not-a-uuid", "/checkout", "/checkout?session="):
            response = client.get(route)
            self.assertEqual(response.status_code, 400)
            self.assertIn('href="/workspace"', response.text)
            self.assertIn("Link nije potpun", response.text)
            self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(client.get("/activate", params={"workspace_id": str(uuid.uuid4())}).status_code, 200)

    def test_bulgarian_portal_covers_existing_catalogue(self):
        import ast
        import re
        from opsnest_cloud.workspace_portal import workspace_portal_html
        from opsnest_cloud.workspace_i18n import BG_TRANSLATIONS
        html = workspace_portal_html()
        sr = {}
        for match in re.findall(r"const SR_[A-Z_]+=Object.freeze\((\{.*?\})\);", html, re.S):
            sr.update(ast.literal_eval(match))
        self.assertGreater(len(sr), 200)
        self.assertFalse(set(sr) - BG_TRANSLATIONS.keys())
        self.assertIn('<option value="bg">Български</option>', html)
        self.assertNotIn("__OPSNEST_BG_TRANSLATIONS__", html)
        for label in sr:
            self.assertTrue(BG_TRANSLATIONS[label].strip(), label)


if __name__ == "__main__":
    unittest.main()

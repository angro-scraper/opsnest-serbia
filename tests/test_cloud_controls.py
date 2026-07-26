"""Regression checks for the cloud controls that protect shared workspaces."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
import uuid
import base64
import hashlib
from datetime import datetime, timedelta
from pathlib import Path


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
    WorkspaceDocument,
    WorkflowComment,
    WorkflowItem,
    create_schema,
    engine,
)
from opsnest_cloud.main import (  # noqa: E402
    CountryPackControlUpdate,
    MemberContext,
    _record_audit,
    _verify_workspace_audit_chain,
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
)


class CloudControlTests(unittest.TestCase):
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
                expires_at=datetime.utcnow() + timedelta(days=1),
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
                token_hash="current", device_name="Owner desktop", expires_at=datetime.utcnow() + timedelta(days=1),
            )
            stale_session = MemberSession(
                id=str(uuid.uuid4()), workspace_id=workspace_id, member_id=member_id,
                token_hash="stale", device_name="Lost laptop", expires_at=datetime.utcnow() + timedelta(days=1),
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
                token_hash="sync-owner", expires_at=datetime.utcnow() + timedelta(days=1),
            )
            accountant_session = MemberSession(
                id=str(uuid.uuid4()), workspace_id=workspace_id, member_id=accountant_id,
                token_hash="sync-accountant", expires_at=datetime.utcnow() + timedelta(days=1),
            )
            operator_session = MemberSession(
                id=str(uuid.uuid4()), workspace_id=workspace_id, member_id=operator_id,
                token_hash="sync-operator", expires_at=datetime.utcnow() + timedelta(days=1),
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

            downloaded = download_team_snapshot(accountant_context, db)
            self.assertEqual(downloaded["sha256"], checksum)
            self.assertEqual(downloaded["snapshot_b64"], payload.snapshot_b64)
            with self.assertRaises(HTTPException) as rejected:
                download_team_snapshot(operator_context, db)
            self.assertEqual(rejected.exception.status_code, 403)
            self.assertTrue(_verify_workspace_audit_chain(db, workspace_id)["ok"])
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
                token_hash="finance-accountant", expires_at=datetime.utcnow() + timedelta(days=1),
            )
            project_session = MemberSession(
                id=str(uuid.uuid4()), workspace_id=workspace_id, member_id=project_manager_id,
                token_hash="finance-project", expires_at=datetime.utcnow() + timedelta(days=1),
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
                token_hash="documents-accountant", expires_at=datetime.utcnow() + timedelta(days=1),
            )
            project_session = MemberSession(
                id=str(uuid.uuid4()), workspace_id=workspace_id, member_id=project_manager_id,
                token_hash="documents-project", expires_at=datetime.utcnow() + timedelta(days=1),
            )
            operator_session = MemberSession(
                id=str(uuid.uuid4()), workspace_id=workspace_id, member_id=operator_id,
                token_hash="documents-operator", expires_at=datetime.utcnow() + timedelta(days=1),
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
                token_hash="audit-session", expires_at=datetime.utcnow() + timedelta(days=1),
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
        yesterday = (datetime.utcnow() - timedelta(days=1)).date().isoformat()
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
                token_hash="brief-session", expires_at=datetime.utcnow() + timedelta(days=1),
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
                updated_at=datetime.utcnow() - timedelta(hours=25),
            )
            db.add_all([workspace, owner, session, overdue, blocked, overview])
            db.commit()

            brief = get_workspace_control_brief(MemberContext(workspace=workspace, member=owner, session=session), db)
            controls = {item["key"]: item for item in brief["items"]}
            self.assertEqual(controls["workflow_overdue"]["severity"], "attention")
            self.assertEqual(controls["unassigned_priority_work"]["count"], 1)
            self.assertEqual(controls["country_control_blocked"]["target"], "countryReadinessSection")
            self.assertIn("financial_overview_stale", controls)
            self.assertIn("not a payment instruction", brief["disclaimer"])
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
                token_hash="return-session", expires_at=datetime.utcnow() + timedelta(days=1),
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


if __name__ == "__main__":
    unittest.main()

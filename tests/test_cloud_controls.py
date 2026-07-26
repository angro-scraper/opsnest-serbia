"""Regression checks for the cloud controls that protect shared workspaces."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
import uuid
from datetime import datetime, timedelta
from pathlib import Path


_SOURCE_ROOT = Path(__file__).resolve().parents[1]
_DATABASE_PATH = Path(tempfile.gettempdir()) / f"opsnest-cloud-controls-{uuid.uuid4().hex}.sqlite"
os.environ["DATABASE_URL"] = "sqlite:///" + _DATABASE_PATH.as_posix()
os.environ["APP_SIGNING_SECRET"] = "test-only-workspace-audit-secret"
os.environ["APP_ENV"] = "development"
sys.path.insert(0, str(_SOURCE_ROOT))

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from opsnest_cloud.database import (  # noqa: E402
    CountryPackControl,
    MemberSession,
    SessionLocal,
    Workspace,
    WorkspaceAuditEvent,
    WorkspaceMember,
    create_schema,
    engine,
)
from opsnest_cloud.main import (  # noqa: E402
    CountryPackControlUpdate,
    MemberContext,
    _record_audit,
    _verify_workspace_audit_chain,
    app,
    get_country_pack_readiness,
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

    def test_country_pack_readiness_is_accountable_not_a_compliance_claim(self) -> None:
        db = SessionLocal()
        workspace_id = str(uuid.uuid4())
        member_id = str(uuid.uuid4())
        try:
            workspace = Workspace(
                id=workspace_id,
                owner_email="owner@example.test",
                company_name="Test Company",
                country_code="RS",
                default_currency="RSD",
                subscription_status="active",
            )
            member = WorkspaceMember(
                id=member_id,
                workspace_id=workspace_id,
                email="owner@example.test",
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

            workspace.country_code = "BG"
            db.commit()
            switched_country = get_country_pack_readiness(context, db)
            switched_controls = {control["key"]: control for control in switched_country["controls"]}
            self.assertEqual(switched_controls["e_invoice"]["status"], "not_started")
            self.assertTrue(_verify_workspace_audit_chain(db, workspace_id)["ok"])
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()

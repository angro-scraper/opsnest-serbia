"""Regression checks for the cloud controls that protect shared workspaces."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
import uuid
from pathlib import Path


_SOURCE_ROOT = Path(__file__).resolve().parents[1]
_DATABASE_PATH = Path(tempfile.gettempdir()) / f"opsnest-cloud-controls-{uuid.uuid4().hex}.sqlite"
os.environ["DATABASE_URL"] = "sqlite:///" + _DATABASE_PATH.as_posix()
os.environ["APP_SIGNING_SECRET"] = "test-only-workspace-audit-secret"
os.environ["APP_ENV"] = "development"
sys.path.insert(0, str(_SOURCE_ROOT))

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from opsnest_cloud.database import SessionLocal, WorkspaceAuditEvent, create_schema, engine  # noqa: E402
from opsnest_cloud.main import _record_audit, _verify_workspace_audit_chain, app  # noqa: E402


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


if __name__ == "__main__":
    unittest.main()

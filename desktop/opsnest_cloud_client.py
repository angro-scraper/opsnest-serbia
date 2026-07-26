from __future__ import annotations

import json
from http.client import HTTPException as HttpClientError
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


# A free Render instance can take more than 50 seconds to wake after it has
# been idle.  Cloud actions already run outside the Tk event loop, so waiting
# for a single safe response is better than falsely telling a user that the
# service is unavailable.  A paid always-on instance remains required for an
# operational production SLA.
CLOUD_REQUEST_TIMEOUT_SECONDS = 65


class CloudApiError(ValueError):
    """A friendly desktop-safe error returned by the OpsNest cloud service."""


class OpsNestCloudClient:
    def __init__(self, base_url: str) -> None:
        self.base_url = str(base_url or "").strip().rstrip("/")
        if not self.base_url.startswith(("https://", "http://")):
            raise CloudApiError("Unesite ispravnu HTTPS adresu OpsNest servisa.")

    def request_email_code(self, *, workspace_id: str, company_name: str, email: str) -> dict[str, Any]:
        """Start e-mail verification directly from the Windows application."""
        return self._request(
            "/v1/auth/request-email-code",
            method="POST",
            payload={"workspace_id": workspace_id, "company_name": company_name, "email": email},
            headers={"X-OpsNest-Client": "desktop"},
        )

    def confirm_email_code(self, *, workspace_id: str, email: str, code: str) -> dict[str, Any]:
        return self._request(
            "/v1/auth/confirm-email-code",
            method="POST",
            payload={"workspace_id": workspace_id, "email": email, "code": code},
        )

    def license_status(self, *, workspace_id: str, workspace_token: str) -> dict[str, Any]:
        return self._request(
            "/v1/license",
            headers={"X-OpsNest-Workspace": workspace_id, "Authorization": f"Bearer {workspace_token}"},
        )

    def financial_advice(
        self,
        *,
        workspace_id: str,
        workspace_token: str,
        summary: dict[str, Any],
    ) -> dict[str, Any]:
        """Request Pro AI advice from the cloud without exposing an API key."""
        return self._request(
            "/v1/ai/financial-advice",
            method="POST",
            payload=summary,
            headers=self._workspace_headers(workspace_id, workspace_token),
        )

    @staticmethod
    def _workspace_headers(workspace_id: str, workspace_token: str) -> dict[str, str]:
        return {"X-OpsNest-Workspace": workspace_id, "Authorization": f"Bearer {workspace_token}"}

    @staticmethod
    def _member_headers(workspace_id: str, member_id: str, member_token: str) -> dict[str, str]:
        return {
            "X-OpsNest-Workspace": workspace_id,
            "X-OpsNest-Member": member_id,
            "Authorization": f"Bearer {member_token}",
        }

    def setup_owner_account(
        self,
        *,
        workspace_id: str,
        workspace_token: str,
        display_name: str,
        password: str,
        device_name: str = "OpsNest Desktop",
    ) -> dict[str, Any]:
        return self._request(
            "/v1/team/owner-account",
            method="POST",
            payload={"display_name": display_name, "password": password, "device_name": device_name},
            headers=self._workspace_headers(workspace_id, workspace_token),
        )

    def team_members(self, *, workspace_id: str, member_id: str, member_token: str) -> dict[str, Any]:
        return self._request(
            "/v1/team/members",
            headers=self._member_headers(workspace_id, member_id, member_token),
        )

    def invite_team_member(
        self,
        *,
        workspace_id: str,
        member_id: str,
        member_token: str,
        email: str,
        display_name: str,
        role: str,
    ) -> dict[str, Any]:
        return self._request(
            "/v1/team/invitations",
            method="POST",
            payload={"email": email, "display_name": display_name, "role": role},
            headers=self._member_headers(workspace_id, member_id, member_token),
        )

    def revoke_team_member(
        self,
        *,
        workspace_id: str,
        actor_member_id: str,
        actor_member_token: str,
        member_id: str,
    ) -> dict[str, Any]:
        """Immediately revoke a member's server session and team access."""
        return self._request(
            f"/v1/team/members/{str(member_id).strip()}/revoke",
            method="POST",
            headers=self._member_headers(workspace_id, actor_member_id, actor_member_token),
        )

    def accept_team_invitation(
        self,
        *,
        email: str,
        code: str,
        password: str,
        device_name: str = "OpsNest Desktop",
    ) -> dict[str, Any]:
        return self._request(
            "/v1/team/invitations/accept",
            method="POST",
            payload={"email": email, "code": code, "password": password, "device_name": device_name},
        )

    def team_login(
        self,
        *,
        workspace_id: str,
        email: str,
        password: str,
        device_name: str = "OpsNest Desktop",
    ) -> dict[str, Any]:
        return self._request(
            "/v1/team/login",
            method="POST",
            payload={
                "workspace_id": workspace_id,
                "email": email,
                "password": password,
                "device_name": device_name,
            },
        )

    def team_me(self, *, workspace_id: str, member_id: str, member_token: str) -> dict[str, Any]:
        return self._request(
            "/v1/team/me",
            headers=self._member_headers(workspace_id, member_id, member_token),
        )

    def download_team_snapshot(self, *, workspace_id: str, member_id: str, member_token: str) -> dict[str, Any]:
        return self._request(
            "/v1/team/sync",
            headers=self._member_headers(workspace_id, member_id, member_token),
        )

    def upload_team_snapshot(
        self,
        *,
        workspace_id: str,
        member_id: str,
        member_token: str,
        expected_revision: int,
        snapshot_b64: str,
        sha256: str,
    ) -> dict[str, Any]:
        return self._request(
            "/v1/team/sync",
            method="POST",
            payload={
                "expected_revision": int(expected_revision),
                "snapshot_b64": snapshot_b64,
                "sha256": sha256,
            },
            headers=self._member_headers(workspace_id, member_id, member_token),
        )

    def upload_financial_overview(
        self,
        *,
        workspace_id: str,
        member_id: str,
        member_token: str,
        summary: dict[str, Any],
    ) -> dict[str, Any]:
        """Send only approved company-level totals for the web dashboard.

        The caller must never include invoice, vendor, customer, project,
        bank-row, attachment or credential data in this payload.
        """
        return self._request(
            "/v1/workspace/financial-overview",
            method="POST",
            payload=summary,
            headers=self._member_headers(workspace_id, member_id, member_token),
        )

    def billing_readiness(self, *, workspace_id: str, workspace_token: str) -> dict[str, Any]:
        return self._request(
            "/v1/billing/readiness",
            headers={"X-OpsNest-Workspace": workspace_id, "Authorization": f"Bearer {workspace_token}"},
        )

    def billing_summary(self, *, workspace_id: str, workspace_token: str) -> dict[str, Any]:
        return self._request(
            "/v1/billing/summary",
            headers={"X-OpsNest-Workspace": workspace_id, "Authorization": f"Bearer {workspace_token}"},
        )

    def plans(self) -> dict[str, Any]:
        """Read the public package catalog without exposing any workspace data."""
        return self._request("/v1/public/plans")

    def desktop_update(self) -> dict[str, Any]:
        """Read public update metadata. The installer URL is optional until release."""
        return self._request("/v1/public/desktop-update")

    def send_diagnostics(
        self,
        *,
        workspace_id: str,
        workspace_token: str,
        app_version: str,
        operating_system: str,
        license_status: str,
        message: str = "",
    ) -> dict[str, Any]:
        """Send a privacy-safe diagnostic report, never local accounting data."""
        return self._request(
            "/v1/support/diagnostic",
            method="POST",
            payload={
                "app_version": str(app_version)[:64],
                "operating_system": str(operating_system)[:240],
                "license_status": str(license_status)[:64],
                "message": str(message)[:800],
            },
            headers={"X-OpsNest-Workspace": workspace_id, "Authorization": f"Bearer {workspace_token}"},
        )

    def checkout_url(self, *, plan_code: str, workspace_id: str, workspace_token: str) -> str:
        payload = self._request(
            f"/v1/billing/checkout-session/{plan_code}",
            method="POST",
            headers={"X-OpsNest-Workspace": workspace_id, "Authorization": f"Bearer {workspace_token}"},
        )
        url = str(payload.get("checkout_url") or "")
        if not url.startswith(("https://", "http://")):
            raise CloudApiError("OpsNest servis nije vratio bezbedan PayPal link.")
        return url

    def _request(
        self,
        path: str,
        *,
        method: str = "GET",
        payload: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        request_headers = {"Accept": "application/json"}
        if body is not None:
            request_headers["Content-Type"] = "application/json"
        request_headers.update(headers or {})
        request = Request(self.base_url + path, data=body, headers=request_headers, method=method)
        try:
            with urlopen(request, timeout=CLOUD_REQUEST_TIMEOUT_SECONDS) as response:
                result = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            try:
                detail = json.loads(exc.read().decode("utf-8")).get("detail")
            except (json.JSONDecodeError, UnicodeDecodeError):
                detail = ""
            raise CloudApiError(str(detail or f"OpsNest servis je vratio grešku {exc.code}.")) from exc
        except (URLError, TimeoutError, OSError, HttpClientError, json.JSONDecodeError) as exc:
            raise CloudApiError(
                "OpsNest online servis trenutno nije dostupan ili se bezbedno pokreće. "
                "Sačekajte jedan minut pa pokušajte ponovo."
            ) from exc
        if not isinstance(result, dict):
            raise CloudApiError("OpsNest servis je vratio neispravan odgovor.")
        return result

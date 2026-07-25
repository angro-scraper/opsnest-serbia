# OpsNest Cloud

Secure cloud service for the OpsNest Windows business application.

## Purpose

This service supports the global product without moving customer bookkeeping to the cloud:

- one-time e-mail verification for a seven-day free trial;
- subscription status for Starter, Business, and Pro plans;
- PayPal Subscriptions checkout and verified webhooks;
- local-first desktop licensing, where customer invoices and project data remain on the customer's own computer.

## Data protection

This repository intentionally contains no customer databases, invoice templates, company logos, e-mail passwords, PayPal credentials, or production configuration. Render secrets are entered only in the Render dashboard.

Desktop activation is completed inside OpsNest: the company name, business e-mail, and six-digit code do not require a separate browser window. Billing checkout still uses a secure browser page and never includes the company name or e-mail in its URL.

## Deploy

The root [`render.yaml`](render.yaml) is intentionally a safe preview. It cannot accidentally open real registrations or billing.

For the live product, create **one** Render Blueprint from [`render.production.yaml`](render.production.yaml). It provisions exactly one web service and one Render Postgres database. The database uses Render's `basic-256mb` plan, so review its current cost in Render before deploying.

The API uses `api.opsnestone.com`. Keep `opsnestone.com` available for the public website. Add a `CNAME` DNS record named `api` that points to `opsnest-cloud-api.onrender.com`, then verify the custom domain in the Render service settings.

1. In Render choose **New > Blueprint**, select this GitHub repository, and set **Blueprint Path** to `render.production.yaml`.
2. Deploy the Blueprint. Confirm that `https://opsnest-cloud-api.onrender.com/health` reports `{"status":"ok","service":"opsnest-cloud"}`.
3. Add the values marked `sync:false` in the Render service's Environment page. Keep those credentials out of GitHub.
4. Test the complete registration and each subscription plan with `PAYPAL_MODE=sandbox` while `APP_ENV=preview`.
5. Create the three live PayPal plans, add their live IDs and webhook ID, switch `PAYPAL_MODE` to `live`, then set `APP_ENV` to `production`.

Production startup refuses to run without Postgres, HTTPS, Resend e-mail verification, Turnstile, all three PayPal plan IDs, and live PayPal mode. This prevents a half-configured public launch.

## Required Render secrets

Enter these only in the Render dashboard after deployment:

- `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET`, `PAYPAL_WEBHOOK_ID`
- `PAYPAL_PLAN_STARTER`, `PAYPAL_PLAN_BUSINESS`, `PAYPAL_PLAN_PRO`
- `RESEND_API_KEY` for e-mail delivery over HTTPS (required on Render Free)
- `TURNSTILE_SITE_KEY`, `TURNSTILE_SECRET_KEY`

## Private platform control panel

The product owner can review registrations, companies, package distribution,
team-seat use, recent non-accounting activity and PayPal webhook metadata at:

`https://api.opsnestone.com/admin`

The panel is disabled by default. Enable it only in the Render Environment for
`opsnest-cloud-api` with these private values:

- `OPSNEST_ADMIN_EMAIL` - the product owner's private administrator e-mail;
- `OPSNEST_ADMIN_PASSWORD` - a unique long password, not reused from e-mail,
  PayPal, GitHub or the hosting provider.

The sign-in cookie is HttpOnly, Secure in production and expires after 12
hours. The panel intentionally cannot display customer invoices, PDFs,
bookkeeping snapshots, passwords, tokens, payment credentials or PayPal
webhook payloads.

## Workspace portal foundation

Owners, accountants and team members can use the authenticated workspace
portal at:

`https://api.opsnestone.com/workspace`

The portal uses the existing team account created in OpsNest Desktop. It shows
only safe collaboration metadata: the company country pack and default
currency, plan and seat use, the member's role, sync status and platform-module
readiness. Owners and administrators can select the country pack and business
profile; every change is recorded in the workspace audit log.

This is intentionally the first platform surface, not a browser copy of the
desktop database. Invoices, financial records, attachments and accounting
snapshots are not rendered in the portal until a dedicated country-specific
cloud module is built and reviewed.

Render Free blocks outbound SMTP ports. Verify `opsnestone.com` in Resend, then set `RESEND_API_KEY` in the Render dashboard. OpsNest uses `support@opsnestone.com` as the sender and automatically prefers Resend when this key is configured.

## Local development

```powershell
$env:APP_ENV = 'development'
$env:APP_SIGNING_SECRET = 'local-development-secret'
pip install -r opsnest_cloud/requirements.txt
uvicorn opsnest_cloud.main:app --reload
```

Check `http://localhost:8000/health` after startup.

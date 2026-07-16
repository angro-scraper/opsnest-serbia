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

## Deploy

The root [`render.yaml`](render.yaml) is intentionally a safe preview. It cannot accidentally open real registrations or billing.

For the live product, create **one** Render Blueprint from [`render.production.yaml`](render.production.yaml). It provisions exactly one web service and one Render Postgres database. The database uses Render's `basic-256mb` plan, so review its current cost in Render before deploying.

The API uses `api.opsnestone.com`. Keep `opsnestone.com` available for the public website. Add a `CNAME` DNS record named `api` that points to `opsnest-cloud-api.onrender.com`, then verify the custom domain in the Render service settings.

1. In Render choose **New > Blueprint**, select this GitHub repository, and set **Blueprint Path** to `render.production.yaml`.
2. Deploy the Blueprint. Confirm that `https://opsnest-cloud-api.onrender.com/health` reports `{"status":"ok","service":"opsnest-cloud"}`.
3. Add the values marked `sync:false` in the Render service's Environment page. Keep those credentials out of GitHub.
4. Test the complete registration and each subscription plan with `PAYPAL_MODE=sandbox` while `APP_ENV=preview`.
5. Create the three live PayPal plans, add their live IDs and webhook ID, switch `PAYPAL_MODE` to `live`, then set `APP_ENV` to `production`.

Production startup refuses to run without Postgres, HTTPS, e-mail verification, Turnstile, all three PayPal plan IDs, and live PayPal mode. This prevents a half-configured public launch.

## Required Render secrets

Enter these only in the Render dashboard after deployment:

- `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET`, `PAYPAL_WEBHOOK_ID`
- `PAYPAL_PLAN_STARTER`, `PAYPAL_PLAN_BUSINESS`, `PAYPAL_PLAN_PRO`
- `SMTP_USERNAME`, `SMTP_PASSWORD`
- `TURNSTILE_SITE_KEY`, `TURNSTILE_SECRET_KEY`

## Local development

```powershell
$env:APP_ENV = 'development'
$env:APP_SIGNING_SECRET = 'local-development-secret'
pip install -r opsnest_cloud/requirements.txt
uvicorn opsnest_cloud.main:app --reload
```

Check `http://localhost:8000/health` after startup.

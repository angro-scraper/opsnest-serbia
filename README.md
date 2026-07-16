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

Render detects the root [`render.yaml`](render.yaml) file. Start with PayPal Sandbox, verify one complete subscription for every plan, and only then switch to PayPal Live.

## Local development

```powershell
$env:APP_ENV = 'development'
$env:APP_SIGNING_SECRET = 'local-development-secret'
pip install -r opsnest_cloud/requirements.txt
uvicorn opsnest_cloud.main:app --reload
```

Check `http://localhost:8000/health` after startup.

# OpsNest Desktop 2.13.4 — candidate release

## Reliable cloud wake-up

- The Windows client now waits up to 65 seconds for a safe cloud response.
  This prevents a false “service unavailable” message when an idle hosted
  instance is waking up.
- The fallback message tells the user to retry after one minute instead of
  implying that financial data or the local application is damaged.

## Production requirement

This protects the user experience during the current hosting configuration.
For a professional financial-service SLA, the production API must run on an
always-on paid instance with monitoring and an availability target; an idle
free-tier instance is not sufficient.

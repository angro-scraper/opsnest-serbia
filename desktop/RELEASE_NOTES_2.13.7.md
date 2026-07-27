# OpsNest Desktop 2.13.7

- A successfully authenticated owner, administrator or team member now stays
  signed in on the same Windows account and opens the linked workspace at the
  next Desktop launch.
- No password is stored. The revocable server session remains protected with
  Windows DPAPI; a session copied to another Windows profile cannot be used.
- Revoked or unavailable sessions safely fall back to the normal sign-in
  screen.

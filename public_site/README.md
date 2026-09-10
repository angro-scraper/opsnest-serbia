# OpsNest public website

Upload the **contents** of this directory to the document root for `opsnestone.com` in Adriahost. Keep the included hidden `.htaccess` file. It provides these readable addresses on a standard Apache host:

- `/pricing`
- `/getting-started`
- `/support`
- `/privacy`
- `/terms`
- `/cancellation`

All download buttons use `https://api.opsnestone.com/download/desktop`, which redirects to the same verified installer manifest as Desktop updates. Deploy that API route before uploading these HTML files; keep the current installer manifest unchanged until a new installer is uploaded and its public SHA-256 is verified. Do not upload application source code, SQLite databases, `.env` files, PayPal/Resend secrets, or the original invoice template.

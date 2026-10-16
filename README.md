# Rwanda Wedding Planner (MVP)

A Rwanda-focused wedding-planning marketplace built as a maintainable Django monolith (Django 5.2 LTS, PostgreSQL, server-rendered templates + HTMX) and deployable on Render.

Customers describe their wedding (budget, date, district, guests, ceremonies). A deterministic, rules-based planner suggests services and vendor packages with itemised RWF costs. Customers send the plan to the **platform operator**, who contacts vendors, records quotes and availability, and sends one quotation. Customers accept it on their dashboard; bookings are confirmed only with vendor-confirmation evidence; payments are recorded and reconciled manually.

> **Honesty rules built into the product:** prices are labelled *indicative*, *vendor-confirmed quote* or *confirmed booking*; a booking is "confirmed" only after staff record evidence of the vendor's confirmation; a payment counts only after staff verify it; the budget planner is a rules engine and is never described as AI; no fake testimonials, ratings or vendors.

## What is implemented

| Area | Status |
|---|---|
| Public site (home, how it works, pricing/fees, privacy, vendor search with HTMX filters, vendor detail) | Done |
| Accounts: email login, signup with consent, password reset, deletion request | Done |
| Guided setup wizard: basics → ceremonies (gusaba, traditional, civil, church, reception) → priorities | Done |
| Budget planner (deterministic, tested) with 3 alternative options and trade-off explanations | Done |
| Plan page: per-ceremony items, subtotal, service fee, contingency, estimated total, remaining budget | Done |
| Request to operator (with explicit consent to contact vendors), customer dashboard, notifications | Done |
| Operations via Django Admin: vendors, packages, price history, availability checks, requests, inquiries, quotations, bookings, invoices, payments, refunds, commissions, settlements, audit log | Done |
| Role-based staff permissions (Planner, Operations Manager, Finance) + immutable audit log | Done |
| Payment-provider abstraction + manual payment workflow | Done (manual only) |
| Vendor portal | **Not built** (data model has `Vendor.portal_user` and `User.Role.VENDOR` reserved) |
| Real payment provider integration | **Not built** (needs a licensed provider and credentials, see below) |
| Persistent media storage | Integrated (S3-compatible) but **needs your bucket credentials** |

## Repository layout

```
config/            settings, urls, wsgi
apps/core/         health check, audit log, notifications, rate limiting, money helpers, admin base, retention/seed commands
apps/accounts/     custom email User, CustomerProfile, roles (groups) 
apps/vendors/      categories, vendors, service areas, packages, inclusions, add-ons, price history, availability, media
apps/planning/     wedding projects, ceremonies, required services, plan items, budget.py (pure planner), views
apps/operations/   customer requests, vendor inquiries, quotations (+lines), bookings, status machines
apps/payments/     invoices, payments, refunds, commissions, vendor settlements, provider abstraction
templates/ static/ HTML, CSS (no framework), vendored htmx
tests/             87 automated tests
render.yaml        Render Blueprint (web service + PostgreSQL)
```

## Key design decisions and assumptions

* **Money** is stored as integer RWF (`PositiveBigIntegerField`); rates are integer basis points (`100 bps = 1%`); rounding is integer half-up in `apps/core/money.py`. No floats anywhere in money paths.
* **Price snapshots:** `PlanItem`, `QuotationLine` and `Booking` copy vendor/package names and prices at the time. Later package edits never change them. `PriceRecord` keeps package price history automatically.
* **Fee vs contingency:** the customer is asked to pay `vendor subtotal + service fee` (`Quotation.payable_total_rwf`). The *contingency reserve* is a budgeting buffer shown separately and **not billed** unless used and agreed.
* **Service fee / commission rates are placeholders.** `PLATFORM_SERVICE_FEE_BPS=500` (5%), `VENDOR_COMMISSION_BPS=0`, `CONTINGENCY_BPS=1000` (10%) are operator decisions, not market data. Set the real, disclosed values before launch. Each project snapshots the rates when created.
* **Planner weights** (`apps/planning/budget.py`: `CEREMONY_WEIGHTS`, `CEREMONY_RULES`) are heuristic relative shares chosen as a starting point. They are not market prices. Review them with real Rwandan planners and adjust.
* **Demo data** is flagged `is_demo=True`, named `[DEMO] …`, unverified, and hidden unless `SHOW_DEMO_VENDORS=true`. `seed_demo` refuses to run when `DEBUG` is off.
* **Vendor contact details and internal notes** are never rendered in customer templates (covered by tests). Contact details appear publicly only if `public_contact_approved` is ticked.
* **Guest data:** only a guest *count* is collected, never names.
* **Admin URL** is `/ops-admin/` (not `/admin/`). That is obscurity only; the protection is authentication + permissions.

## Local setup on Windows (PowerShell)

Prerequisites: Python 3.12 (python.org installer, tick "Add to PATH") and Git. PostgreSQL is optional locally (SQLite is used if `DATABASE_URL` is empty).

```powershell
git clone https://github.com/0780814741/Rawnda_Wedding_Management.git
cd Rawnda_Wedding_Management

python -m venv .venv
# If activation is blocked:  Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt

copy .env.example .env          # keep DEBUG=true for local work; .env is git-ignored

python manage.py migrate        # also creates categories and the staff role groups
python manage.py createcachetable
python manage.py createsuperuser   # asks for email + password
python manage.py seed_demo         # optional: FICTIONAL demo vendors (needs DEBUG=true)
python manage.py runserver
```

Open http://127.0.0.1:8000/ (site) and http://127.0.0.1:8000/ops-admin/ (operations dashboard).

**Using local PostgreSQL instead of SQLite:** create a database (`CREATE DATABASE wedding;`) and set in `.env`:
`DATABASE_URL=postgres://USER:PASSWORD@localhost:5432/wedding`

**Run the tests**

```powershell
python manage.py test tests            # all 87 tests
python manage.py test tests.test_budget -v 2
```

**Staff roles.** Run `python manage.py setup_roles` anytime (also runs after migrate). In `/ops-admin/` → Users, add staff to groups:
* **Planner** – works requests, inquiries, draft quotations; cannot approve vendors, send quotations, confirm bookings or touch payments.
* **Operations Manager** – everything operational incl. approving/suspending/verifying vendors, sending quotations, confirming bookings.
* **Finance** – invoices, verifying/reversing payments, refunds, vendor settlements.
* Superusers can do everything; use one only for setup.

## Operator workflow (Django Admin)

1. **Vendors → Vendors**: create vendor, assign categories and service areas, add packages (price, unit, capacity, inclusions/exclusions, add-ons, validity, last price verification). Tick *Verified* only after checking. Use the **Approve** action to publish.
2. **Customer requests**: *Assign to me*, mark status, add **Vendor inquiries** (record channel, quote, validity, evidence reference, private notes). Record **Availability checks** with evidence.
3. Use **"Compare planner options"** on a wedding to see the planner's alternatives; adjust plan items if needed.
4. On the request choose **"Create a draft quotation from the customer's plan"**; edit lines (set `price_status`), set `valid until` and customer notes; **Move to review**; **Send to customer** (needs the send permission).
5. After the customer accepts, bookings appear as *Awaiting vendor confirmation*. Contact each vendor, save the **confirmation evidence** on the booking, then run **Confirm booking**.
6. Quotation action **"Create invoice…"** → invoice action **Issue** (customer sees instructions from `PAYMENT_INSTRUCTIONS`).
7. Record each received payment (Payments → Add), check the bank/mobile-money statement, add a reference, then **Verify**. Only verified payments change invoice status.
8. Commissions and vendor settlements are tracked separately. Settlements become *Due* only when the booking is vendor-confirmed **and** the customer's verified payments cover the quotation. Mark *Scheduled/Paid* manually with a payment reference.

## Payments architecture and regulatory note

`apps/payments/providers.py` defines `PaymentProvider` and a working `ManualProvider`. No external provider is integrated and none is faked. The webhook route `/payments/webhook/<provider>/` returns **501** until a provider is implemented. When you add one you must: confirm with the provider and, where needed, Rwandan regulators (e.g. the National Bank of Rwanda's payment-services licensing) that holding customer funds and settling vendors is permitted for your structure; implement only documented provider APIs; verify signatures on the raw request body (`verify_hmac_sha256` helper exists only if the provider uses that scheme); use `record_provider_event` for idempotency; and never treat a browser redirect as payment proof. The app never stores card numbers, PINs or payment credentials. **This code does not hold or distribute funds.**

## Security and privacy

CSRF protection, PBKDF2 password hashing with a 10-character minimum, secure/HTTP-only/SameSite cookies, HTTPS redirect and HSTS in production (HSTS starts at 1 hour; raise `SECURE_HSTS_SECONDS` to 31536000 once HTTPS works), clickjacking and sniffing protection, per-project ownership checks (other customers get 404), DB-backed rate limiting on login/signup/request forms, upload validation (extension allow-list, size cap, must decode as a real image), generic production error pages, structured stdout logs without bodies/cookies/secrets, immutable audit log for sensitive actions, role-based admin permissions.

Retention/deletion: customers can request deletion on the Account page. Run `python manage.py purge_expired_data` regularly (a Render Cron Job is a good fit; `--dry-run` previews). It anonymises deletion-requested accounts (financial records are kept without personal details), deletes draft plans untouched for `DATA_RETENTION_DAYS` (default 730) and expires overdue quotations. **Have a lawyer review your privacy notice** and data-protection obligations (Rwanda's data protection law) before launch.

## Deploying to Render

### 1. Prepare GitHub
```powershell
git add .
git status          # confirm .env and db.sqlite3 are NOT listed
git commit -m "Wedding marketplace MVP"
git push origin main
```

### 2. Create the services from the Blueprint
1. Render Dashboard → **New → Blueprint** → connect GitHub → choose this repository.
2. Render reads `render.yaml` and proposes: PostgreSQL `wedding-db` and web service `rwanda-wedding-planner` (Frankfurt, the nearest Render region to Rwanda at the time of writing; there is no African region).
3. Fill the prompted values (see table). `SECRET_KEY` is generated automatically. Leave unknown optional values blank for now.
4. **Apply**. The build runs `pip install` + `collectstatic`; the pre-deploy step runs `migrate`, `createcachetable`, `setup_roles`; then the health check `/healthz` must pass.
5. Create the first admin: Render service → **Shell** → `python manage.py createsuperuser`. (No Shell on your plan? Run it from your PC with the database's **External** connection string: `$env:DATABASE_URL="<external url>"; $env:SECRET_KEY="x"; python manage.py createsuperuser`, after adding your IP to the database's access list. Remove the allowance afterwards.)
6. Visit `https://<service>.onrender.com/` and `/ops-admin/`. Add your custom domain in Render, then add it to `ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS` (`https://…`).

Neither the build nor the app needs your computer to stay online.

### Environment variables
| Variable | Purpose | Notes |
|---|---|---|
| `SECRET_KEY` | Django signing key | Generated by Render. Never commit. |
| `DEBUG` | must be `false` | |
| `DATABASE_URL` | PostgreSQL | Injected from the Render database. |
| `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS` | custom domains | `RENDER_EXTERNAL_HOSTNAME` is added automatically. |
| `PLATFORM_SERVICE_FEE_BPS`, `VENDOR_COMMISSION_BPS`, `CONTINGENCY_BPS` | business rates (bps) | Set your real values. |
| `PAYMENT_INSTRUCTIONS` | official bank/mobile-money text on invoices | Only official details. |
| `OPERATOR_EMAIL`, `SUPPORT_PHONE`, `SITE_NAME` | notifications / display | |
| `EMAIL_BACKEND`, `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `DEFAULT_FROM_EMAIL` | SMTP | Default prints emails to logs, so **password-reset and alerts will not reach users until SMTP is configured.** |
| `AWS_STORAGE_BUCKET_NAME`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_S3_ENDPOINT_URL`, `AWS_S3_REGION_NAME`, `AWS_S3_CUSTOM_DOMAIN` | persistent media | See below. |
| `SHOW_DEMO_VENDORS` | keep `false` | |
| `SECURE_HSTS_SECONDS` | HSTS duration | Default 3600. |
| `PRICE_STALE_DAYS`, `DATA_RETENTION_DAYS`, `LOG_LEVEL`, `RATELIMIT_ENABLED` | tuning | |

### Static and media files
* **Static** (CSS/JS) is served by WhiteNoise after `collectstatic` at build time.
* **Render's filesystem is ephemeral.** Anything written to disk (including uploaded vendor photos) disappears on redeploy or restart. Therefore uploads use **S3-compatible object storage** (Cloudflare R2, Backblaze B2 or AWS S3): create a bucket with public-read for objects (or a public CDN domain) and write access only for the app's key, then set the `AWS_*` variables. **Until `AWS_STORAGE_BUCKET_NAME` is set, image uploads are disabled in production** (the admin shows a clear message and a Django system warning `core.W001` is raised). That is the safe MVP alternative: vendor pages work without photos. (This storage integration has not been tested against a real bucket.)
* Alternative: attach a Render persistent disk and point `MEDIA_ROOT` to it. That ties you to one instance and is not implemented here.

### Migrations
Migrations run in `preDeployCommand` before traffic switches, so a failed migration aborts the deploy and keeps the old version live. They only add/alter schema; nothing resets data. If your plan does not support pre-deploy commands, change `buildCommand` to `pip install -r requirements.txt && python manage.py collectstatic --noinput && python manage.py migrate --noinput && python manage.py createcachetable && python manage.py setup_roles`. For destructive schema changes, take a backup first and use two-step (expand/contract) migrations.

### Backups
Use a paid Render Postgres plan (the Blueprint does) and confirm in the dashboard what backup/point-in-time-recovery your plan includes. Additionally keep your own periodic dumps: `pg_dump "<external url>" -Fc -f backup.dump`. **Test a restore** before relying on it.

### Redeploying
Push to `main` (`autoDeploy: true`) or use **Manual Deploy**. To roll back, use Render's deploy history; rolling back code does not roll back database migrations.

### Troubleshooting
* **Deploy fails: "SECRET_KEY environment variable is required"** → the variable is missing (Blueprint generates it; check Environment tab).
* **Deploy fails: "Set ALLOWED_HOSTS"** → deploy on Render (it provides `RENDER_EXTERNAL_HOSTNAME`) or set `ALLOWED_HOSTS`.
* **400 Bad Request** on a custom domain → add it to `ALLOWED_HOSTS`.
* **403 CSRF failure** on login → add `https://your-domain` to `CSRF_TRUSTED_ORIGINS`.
* **Health check failing** → open Logs; usually a failed migration or DB connection. `/healthz` returns 503 if the DB is unreachable.
* **No CSS** → check `collectstatic` ran in the build log.
* **Admin says uploads disabled** → configure object storage (above).
* **"relation django_cache_table does not exist"** (rate limiting fails open, but fix it) → run `python manage.py createcachetable`.
* **Password-reset emails not arriving** → configure SMTP variables.

## What was verified (and what was not)

Verified in the build sandbox: all 87 automated tests pass on SQLite **and** on PostgreSQL 16; migrations apply cleanly on PostgreSQL and `makemigrations --check` reports no drift; `manage.py check --deploy` shows only expected warnings; the app was run under gunicorn in production mode against PostgreSQL and probed (health check, HTTPS redirect, secure cookie flags, security headers, static files via WhiteNoise, bad-host rejection, generic 404, webhook disabled); every Django Admin changelist/add/change page renders.

**Not verified:** deployment to Render (no account access; `render.yaml` follows Render's documented Blueprint schema but has not been applied); SMTP email; S3/R2 media uploads; browser-level behaviour of the HTMX filters, mobile layout and accessibility (no browser or screen-reader testing was done); load/performance; a payment provider (none exists).

## Still needs your decisions or external confirmation

1. Real service-fee / commission / contingency rates and the legal wording of the fee disclosure.
2. Review of planner weights and category rules with local wedding professionals.
3. Real vendor onboarding agreements (including consent to price display and commissions).
4. Licensed payment provider selection and regulatory confirmation of the fund flow; until then, payments stay manual.
5. Official bank/mobile-money details for `PAYMENT_INSTRUCTIONS`.
6. SMTP provider, object-storage bucket, custom domain.
7. Legal review of privacy notice and terms; Kinyarwanda/French translations if wanted (the UI is English-only as specified).
8. Vendor portal (later phase).

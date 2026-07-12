# Telegram Ledger

Production-oriented foundation for a Telegram payment operations dashboard. The
backend currently stores raw Telegram messages and parsed payment events, and
recognizes the supported payment notification format. It exposes transactional
payment listing, claim, completion, and unclaim operations, plus a local Telethon
listener. Local admin/staff authentication and staff account management are
available, with a responsive local dashboard. Realtime push transport is not
implemented yet.

## Prerequisites

- Python 3.13
- Node.js 20.9 or newer and npm
- PostgreSQL 16 or newer

## PostgreSQL setup

Connect as a PostgreSQL administrator and create the application role and database:

```sql
CREATE ROLE ledger_user WITH LOGIN PASSWORD 'choose-a-strong-local-password';
CREATE DATABASE ledger_db OWNER ledger_user;
```

Use a unique, securely generated password outside local development. The application
uses the async `asyncpg` driver and never stores credentials in source control.

## Backend installation and local setup

From `telegram-ledger/backend`:

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env
```

Edit `.env` so the PostgreSQL variables match the role and password created above.
All settings are validated at startup. There are no silent production credential
defaults.

Run the API:

```powershell
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Verify it:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
```

The response is:

```json
{"status": "ok"}
```

Ledger endpoints:

- `GET /api/payments` lists newest payments first and accepts `status`, `search`,
  `date_from`, and `date_to` filters.
- `POST /api/payments/{id}/claim` claims a pending payment.
- `POST /api/payments/{id}/done` completes a pending or in-progress payment.
- `POST /api/payments/{id}/unclaim` returns an in-progress payment to pending.

All ledger endpoints require an active admin or staff session. Claim and completion
actions derive the acting user from the authenticated HTTP-only cookie and accept no
actor ID from the request.

## Local authentication and first administrator

Generate a random authentication signing key and configure these values in
`backend/.env`:

```dotenv
AUTH_SECRET_KEY=replace_with_at_least_32_random_characters
AUTH_COOKIE_NAME=telegram_ledger_session
AUTH_COOKIE_SECURE=false
AUTH_SESSION_MINUTES=480
```

Use `AUTH_COOKIE_SECURE=true` behind HTTPS in staging and production. Apply database
migrations, then create the first administrator interactively from
`telegram-ledger/backend`:

```powershell
python -m app.auth.create_admin
```

The command prompts for username, password, and password confirmation. Passwords
must be 12-128 characters and are stored only as Argon2 hashes.

Authentication endpoints:

- `POST /api/auth/login`
- `POST /api/auth/logout`
- `GET /api/auth/me`

Administrator-only staff endpoints:

- `GET /api/admin/staff`
- `POST /api/admin/staff`
- `PATCH /api/admin/staff/{id}/disable`
- `PATCH /api/admin/staff/{id}/reset-password`

Run backend checks:

```powershell
pytest
ruff check .
mypy app
```

## Alembic

Alembic reads the same environment-based database configuration as the API. Apply
the initial `telegram_messages` and `payment_events` migration with:

```powershell
alembic upgrade head
```

For future schema changes:

```powershell
alembic revision --autogenerate -m "describe change"
alembic check
```

Always review generated migrations before applying them. `alembic check` requires a
reachable database at the current migration head.

## Local Telegram listener

Create Telegram credentials:

1. Sign in at `https://my.telegram.org` with the Telegram account that belongs to
   the target group.
2. Open **API development tools** and create a local application.
3. Copy the numeric API ID and API hash into `backend/.env`. Never put them in
   `.env.example` or source control.

Then configure the listener:

```dotenv
TELEGRAM_API_ID=123456
TELEGRAM_API_HASH=your_api_hash
TELEGRAM_SESSION_NAME=telegram-ledger
TELEGRAM_GROUP_USERNAME=@your_group_username
# Or use TELEGRAM_GROUP_ID=-1001234567890 instead.
TELEGRAM_CASHOUT_GROUP_ID=-1009876543210
TELEGRAM_ENABLED=true
TELEGRAM_BACKFILL_LIMIT=500
# Completion reactions (comma-separated). Use * or any for legacy "any reaction".
CASHOUT_COMPLETION_REACTIONS=✅,👍
CASHOUT_RECONCILIATION_INTERVAL_SECONDS=20
CASHOUT_RECONCILIATION_BATCH_SIZE=40
```

Set exactly one payment group selector:

- `TELEGRAM_GROUP_USERNAME` is the public group username, including `@`.
- `TELEGRAM_GROUP_ID` is the full Telegram chat ID, normally beginning with `-100`.

Set `TELEGRAM_CASHOUT_GROUP_ID` to the separate cashout Telegram group ID. Cashout
requests are sent only to this group, and cashout completion reactions are accepted
only from this group. If it is missing while the listener is enabled, startup fails
instead of falling back to the payment group.

`CASHOUT_COMPLETION_REACTIONS` controls which Telegram reactions mark a cashout
completed (default `✅,👍`). Set to `*` or `any` to accept any active reaction.
The listener also runs a bounded reaction reconciliation loop every
`CASHOUT_RECONCILIATION_INTERVAL_SECONDS` (default 20) so reactions missed during
disconnects are still applied without requiring a browser refresh.

For a private group without a username, list every chat available to the configured
Telegram account:

```powershell
python -m app.telegram.list_chats
```

The command reuses `TELEGRAM_SESSION_NAME`. An existing session reconnects without
asking for phone or OTP; otherwise Telethon performs the normal terminal login. Find
the group by title, copy its full marked ID, then update `backend/.env`:

```dotenv
TELEGRAM_GROUP_ID=-1001234567890
# TELEGRAM_GROUP_USERNAME must be removed or commented out.
TELEGRAM_CASHOUT_GROUP_ID=-1009876543210
```

Configured payment ID or username matches are marked with `>>> TARGET GROUP`. Use
the same command output to copy the separate cashout group ID.

Before connecting to Telegram, verify the parser locally from
`telegram-ledger/backend`:

```powershell
python -m app.telegram.test_parser
```

Apply the database migration, then start the listener in its own terminal:

```powershell
.\.venv\Scripts\Activate.ps1
alembic upgrade head
python -m app.telegram.run_listener
```

Listener startup now connects to Telegram, resolves the configured payment group and
cashout group, and runs checkpointed backfill before live listening. The first run scans the most recent
`TELEGRAM_BACKFILL_LIMIT` messages and stores the highest scanned message ID; later
starts only fetch messages newer than that checkpoint. The default limit is 500 when
the variable is omitted.

To run the same idempotent backfill manually and exit:

```powershell
python -m app.telegram.backfill
```

Manual repair can also override the scan window:

```powershell
python -m app.telegram.backfill --limit 500
python -m app.telegram.backfill --since-message-id 12345
python -m app.telegram.backfill --full
```

Both paths use the same ingestion service. Re-running backfill, or receiving a
backfilled message later through the live listener, safely reports it as a duplicate
without creating another raw message or payment event.

On the first run, Telethon prompts in the terminal for the Telegram phone number,
login code, and two-step verification password when applicable. It creates a local
session file so later runs can reconnect without repeating login. Session files and
environment credentials are excluded from Git.

Each new message from the configured payment group is stored once in `telegram_messages`.
Recognized payment notifications create a `payment_events` row in the same
transaction; ordinary messages retain only the raw source row.

The listener prints its enabled state, session name, whether the payment and cashout
groups are configured, connected account, resolved group titles/IDs, and session
filename. Each text message prints a short preview followed by `parsed`, `ignored`,
or `duplicate skipped`. Parsed payments also print the amount, sender, and recipient
tag.

For normal local use, keep three terminals open:

1. Backend API: activate `backend/.venv`, then run
   `uvicorn app.main:app --reload --host 127.0.0.1 --port 8000`.
2. Frontend: from `frontend`, run `npm run dev` and open
   `http://localhost:3000`.
3. Listener: activate `backend/.venv`, then run
   `python -m app.telegram.run_listener`.

## Frontend installation and local setup

From `telegram-ledger/frontend`:

```powershell
npm install
Copy-Item .env.example .env.local
npm run dev
```

Open `http://localhost:3000`. The root route redirects to `/dashboard`.
Application routes are available at:

- `/dashboard`
- `/payments`
- `/settings`
- `/login`
- `/admin/staff` (administrators only)

The frontend sends API requests with credentials enabled so the backend HTTP-only
session cookie remains the single authentication source.

Run frontend checks:

```powershell
npm run lint
npm run typecheck
npm run build
```

## Architecture

### Backend

- `app/api/` — HTTP routing and dependency aliases. Routes stay thin and delegate
  future workflows to services.
- `app/core/` — validated environment configuration and process-wide structured
  logging.
- `app/db/` — async SQLAlchemy engine, request-scoped sessions, declarative base,
  and repository adapters. Repositories will isolate persistence from use cases.
- `app/models/` — SQLAlchemy mappings for raw Telegram messages and parsed payment
  events, imported here so Alembic can discover their metadata.
- `app/schemas/` — Pydantic v2 API contracts, separate from ORM models.
- `app/services/` — application use cases, payment state rules, and transaction
  orchestration. Services receive repositories and other collaborators through
  dependency injection.
- `app/telegram/` — local Telethon client setup, event conversion, and listener CLI.
  Persistence and parsing remain in the service layer.
- `app/parser/` — pure, transport-independent payment notification parsing. Unknown
  or malformed messages return no parsed payment.
- `app/auth/` — password hashing, signed session tokens, and the first-admin CLI.
- `app/websocket/` — reserved realtime delivery boundary. WebSocket or SSE can be
  added without coupling it to the core service layer.
- `app/utils/` — small helpers that are genuinely shared and domain-neutral.
- `alembic/` — asynchronous PostgreSQL migration environment and versioned schema
  revisions. It shares the ORM metadata and application settings.
- `tests/` — automated backend tests, beginning with the health contract.

The intended request flow is route -> injected service -> repository -> async
SQLAlchemy session -> PostgreSQL. External Telegram input and future realtime output
will enter through adapters around the service layer, keeping business workflows
independent of transport details.

### Frontend

- `app/` — Next.js App Router layouts and route segments.
- `components/` — reusable presentation components.
- `hooks/` — future client-side behavior shared across features.
- `lib/` — framework-neutral frontend utilities and validated environment access.
- `services/` — future typed API and realtime clients.
- `types/` — shared TypeScript domain and API types.
- `public/` — static assets.

Protected pages restore the current session on load and redirect unauthenticated
users to login. Navigation is role-aware, payment actions refresh the ledger after
success, and staff management is available only to administrators.

## Configuration and security

Local environment files are ignored by Git. Commit only the provided examples.
Production secrets should be supplied by the deployment platform's secret manager.
Restrict `APP_CORS_ORIGINS` to trusted frontend origins in every deployed
environment. It accepts either a comma-separated value or JSON array. Development
automatically includes `http://localhost:3000` and `http://127.0.0.1:3000`.

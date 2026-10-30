# Setup and Test Credentials

Synthetic data only. Needs Docker Desktop, Python 3.12+ and Node 22+.

## First run

Copy `.env.example` to `.env` and replace every value, then:

```bash
docker compose up -d --wait db
```

```bash
cd backend
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements-dev.txt
.venv/Scripts/python -m alembic upgrade head
.venv/Scripts/python -m scripts.seed
```

```bash
cd frontend
npm install
```

## Run

Backend, from `backend/`:

```bash
.venv/Scripts/python -m uvicorn app.main:app --port 8000
```

Frontend, from `frontend/`:

```bash
npm run dev
```

Open http://localhost:3000. API docs are at http://localhost:8000/api/docs.

For a live demo, use the production build instead of `npm run dev`. It does not rely
on the development server:

```bash
npm run demo
```

## Test

Run from `backend/`. Tests use their own `festos_test` database, rebuilt on every run,
so demo data is never touched. PostgreSQL must be running.

```bash
.venv/Scripts/python -m pytest -q
```

To reload all data from scratch, which deletes everything in the database. The seed
includes the sample allocation plan, so beds are already allocated:

```bash
.venv/Scripts/python -m scripts.seed --reset
```

## Assistant

Without a key, the assistant runs in offline mode and answers set questions from
festival data. To enable free-form questions, set `ANTHROPIC_API_KEY` in `.env` and
restart the backend. The key stays on the server.

## Telegram bot

Optional two-way bot that answers from live data over the same read-only assistant.
It long-polls Telegram (no public URL needed), so it starts with the backend.

1. Create a bot with [@BotFather](https://t.me/BotFather) and copy its token.
2. In `.env` set `TELEGRAM_BOT_TOKEN`, `TELEGRAM_AS_EMAIL` (the FestOS account whose
   permissions the bot answers with, e.g. `safety@festos.test`), and, to restrict who
   may use it, `TELEGRAM_ALLOWED_CHAT_IDS` (comma-separated chat ids; empty = anyone).
3. Restart the backend and message the bot: `/help`, `/status`, `/incidents`, or any
   question. Replies carry that user's permissions and cannot change anything; it never
   logs the question, only that one was asked.

## Logins

All accounts share the password in `DEMO_PASSWORD` in your `.env`.

| Role | Email | Source |
|---|---|---|
| Super Admin | admin@festos.test | Simulated |
| Festival Director | st08@festos.test | Core team |
| Event Coordinator | st03@festos.test | Core team |
| Accommodation Head | st02@festos.test | Core team |
| Finance Head | finance@festos.test | Simulated |
| Safety Coordinator | safety@festos.test | Simulated |
| Volunteer Head | volunteers@festos.test | Simulated |
| Volunteer | st07@festos.test | Core team |
| Participant | participant@festos.test | Simulated |

Every core team member can sign in as `<member_id>@festos.test`, for example
`st01@festos.test`. Their role comes from the mapping in
`backend/app/services/pack_import.py`.

Google sign-in needs a Firebase project and is not available yet. Password sign-in
uses local signed sessions; `backend/app/auth/verifier.py` is the file to replace.

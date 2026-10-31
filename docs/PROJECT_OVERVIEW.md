# FestOS Command — Project Overview (for agents)

A single reference for an agent picking up this repo cold. It describes what has
actually been built and where it lives. For deeper topics see [ARCHITECTURE.md](../ARCHITECTURE.md),
[docs/DATA_FINDINGS.md](DATA_FINDINGS.md) and [docs/ASSUMPTIONS.md](ASSUMPTIONS.md).

## What this is

A festival operations command center for **Aarohan 2026** (Epinoia 2026 hackathon).
It connects every operational module of the festival and, as the headline feature,
**simulates the downstream impact of a change before anyone commits it** (the Ripple
Simulator). All data is synthetic.

Stack: **React + Vite + TypeScript** (port 3000) → **FastAPI + SQLAlchemy 2** (port 8000)
→ **PostgreSQL 16** (Docker). Auth is local signed-session cookies. No LLM is involved
in computing simulation impacts.

> Note: `src/`, `prisma/`, `scripts/ingest.py` are a superseded Next.js/Prisma outline
> and are **not used** by the running app. The live app is `backend/` + `frontend/`.

## Build history (git)

Five phases, all built:

| Commit | Phase | Scope |
|---|---|---|
| `9e5daa2` | 1 — Foundation | Setup, auth, DB, roles, Command Center |
| `191b305` | 2 — Core operations | Events, participants, venues, accommodation, transport, volunteers |
| `7eb449a` | 3 — Ripple Simulator | Dependency engine, two-phase preview/commit, approvals, audit |
| `0450878` | 4 — Intelligence & reporting | Analytics, reports |
| `54a6574` | 4 — Operations | AI assistant, notifications, finance, safety |

Earlier commits (`28442d5`, `1e7b6e0`) hold the superseded ingestion outline.

## Repo layout

```
backend/
  app/
    main.py            FastAPI app: router wiring, middleware (request id, security
                       headers, no-store), health/ready, WebSocket /api/ws/{festival_id}
    config.py          Settings (env-driven)
    api/               Routes only — validate, authorize, call a service
    auth/              verifier.py (sessions), permissions.py (role matrix), deps.py
    services/          Business logic: allocator, finance, events, housing,
                       schedule_plan, pack_import, simulated_data, audit
    simulation/        graph.py (module graph), scenarios.py (5 scenarios), engine.py
    analytics/         dashboard.py (metrics), reports.py
    assistant/         agent.py (Claude tool loop + offline fallback), tools.py
    notifications/     live.py (in-process WebSocket hub)
    models/entities.py All SQLAlchemy tables
    database/          Engine, SessionLocal, Base
  scripts/seed.py      Loads the festival from the data pack
  tests/               pytest suite (see below)
frontend/src/
  main.tsx             Routes + lazy-loaded pages, react-query, protected shell
  components/          Shell, ui, Assistant, CommandPalette, ImpactGraph, ProfileDialog
  pages/               One per module (see Frontend below)
  lib/                 api.ts, auth.ts, format.ts
docs/                  This folder
docker-compose.yml     PostgreSQL for dev;  docker-compose.prod.yml for deploy
```

## Data model (`backend/app/models/entities.py`)

Mixins: `Timestamped`, `FestivalScoped` (adds `festival_id`), `Simulated` (adds
`is_simulated`), `SoftDeleted` (adds `deleted_at`).

- **Identity/access**: `roles`, `permissions`, `role_permissions`, `users`, `festivals`, `departments`
- **People**: `contingents`, `participants`, `teams`, `registrations`
- **Events/venues**: `events`, `venues`
- **Resources/inventory**: `resources`, `resource_allocations`, `vendors`
- **Accommodation**: `hostels`, `rooms`, `accommodation_allocations`
- **Transport**: `vehicles`, `routes`, `transport_assignments`
- **Volunteers**: `volunteers`, `volunteer_shifts`, `tasks`
- **Finance**: `budgets`, `expenses`, `sponsors`
- **Comms/safety**: `announcements`, `notifications`, `incidents`
- **Ripple**: `simulations`, `simulation_impacts`, `change_approvals`
- **Governance**: `audit_logs` (append-only via DB trigger), `data_issues`

Data provenance is explicit and labelled in the UI: `pack` (from the data pack),
`generated` (computed by our planner), `simulated` (invented for the demo, rows carry
`is_simulated = true`). Pack blanks stay `NULL` — a missing value never gets a default.

## Backend API

All routers are mounted in `backend/app/api/` and wired in `main.py`. Most are scoped
under `/api/festivals/{festival_id}`; routes authorize via `require("<permission>")`.

| Router | Prefix | Covers |
|---|---|---|
| auth | `/api/auth` | Login, logout, current user |
| festivals | `/api/festivals` | Festival config |
| users | `.../users` | Team/user management |
| participants | `/api/festivals/{festival_id}` | Participants, check-in, sensitive data |
| events | `.../events` | Events, scheduling |
| venues | `/api/festivals/{festival_id}` | Venues |
| accommodation | `.../accommodation` | Hostels, rooms, allocation |
| transport | `.../transport` | Vehicles, routes, assignments |
| volunteers | `/api/festivals/{festival_id}` | Volunteers, shifts, tasks |
| finance | `.../finance` | Budgets, expenses, sponsors |
| communication | `/api/festivals/{festival_id}` | Announcements, notifications |
| incidents | `.../incidents` | Safety incidents |
| analytics | `/api/festivals/{festival_id}` | Dashboard metrics, reports |
| assistant | `.../assistant` | Read-only AI Q&A |
| simulations | `/api/festivals/{festival_id}` | Ripple Simulator |

Infra endpoints: `GET /api/health`, `GET /api/ready` (DB check), `WS /api/ws/{festival_id}`.

## Auth & permissions

- Sessions are local signed cookies; all token logic is isolated in
  `app/auth/verifier.py` (the one file a Firebase swap would replace).
- The role matrix is in `app/auth/permissions.py` and seeded into
  `roles`/`permissions`/`role_permissions`. Roles: super_admin, festival_director,
  event_coordinator, accommodation_head, transport_head, volunteer_head, finance_head,
  safety_coordinator, volunteer.
- **Server enforces** permissions (`require("finance.read")` etc.); the frontend only
  hides what a role cannot use. Contact/accessibility/medical details sit behind
  `participants.read_sensitive`.
- Multi-festival scoping: every operational table carries `festival_id`; users bound to
  a festival are rejected elsewhere (`festival_access`). `festival_id = NULL` = all.

## Ripple Simulator (the core feature) — `backend/app/simulation/`

- **`graph.py`** — seven modules and their relationships; walks them breadth-first.
  Depth = how far an effect travelled (depth 1 = direct). Each relationship fires once,
  so cycles terminate. Knows nothing about festivals.
- **`scenarios.py`** — five scenarios, each a set of rules that query real rows:
  1. **Hostel closure** (reuses the real accommodation allocator)
  2. **Venue change / closure**
  3. **Bus delay**
  4. **Volunteers unavailable**
  5. **Budget reduction**
- **`engine.py`** — two-phase workflow:
  - **Preview**: computes a report, stores it in `simulations` + `simulation_impacts`,
    saves a `state_hash`. No production row changes.
  - **Commit** (needs `simulation.approve`): in a serializable transaction, recomputes
    the report and **refuses if the hash differs** (so any data change that would alter
    the outcome blocks it). Then applies the plan, writes audit rows, and sends
    notifications together — any failure rolls everything back.

Every impact is tagged `fact` or `estimate`. Roles without `simulation.approve` can
simulate and request approval.

## AI assistant — `backend/app/assistant/`

- `tools.py` — a fixed set of **read-only** tools, each tied to a permission; a role's
  tools are filtered before the model sees them and re-checked on every call. No write
  tools; results carry no participant names/contact details.
- `agent.py` — Claude tool loop when `ANTHROPIC_API_KEY` is set; otherwise (or on API
  failure) an **offline mode** maps the question to the same tools.
- Questions are **not logged** (people may type personal details); the audit log records
  who asked and which tools ran.

## Live updates & audit

- After a commit writes an audit row, the server broadcasts a dataless `changed` message
  over the WebSocket; clients refetch through normal routes (a socket can't leak a row).
  Hub is in-process — suits a single API worker.
- `audit_logs` is append-only, enforced by a DB trigger that rejects UPDATE/DELETE. The
  dashboard activity feed reads from it.

## Frontend (`frontend/src/`)

React Router with lazy-loaded pages behind a protected `Shell`; TanStack Query for data;
theme persisted in `localStorage` (`festos.theme`, default `night`). Vite proxies `/api`
to the backend so the session cookie stays same-origin.

Pages: CommandCenter (`/`), CheckIn, Events, Participants, Accommodation, Transport
(`/transportation`), Volunteers, Venues, Ripple, Finance, Communication, Safety,
Analytics, Settings, plus Login. Shared components: `Assistant`, `CommandPalette`,
`ImpactGraph` (ripple visualization), `ProfileDialog`, `ui`.

## Tests — `backend/tests/`

Run from `backend/` with `pytest`; tests use their own `festos_test` DB rebuilt each run
(demo data untouched). Suites: `test_foundation`, `test_operations`, `test_participants`,
`test_allocator`, `test_ripple`, `test_phase4`, `test_security`, `test_workflow`,
`test_interface_contracts`.

## Running it

See [docs/TEST_CREDENTIALS.md](TEST_CREDENTIALS.md) for full setup and logins. Short form:

```bash
docker compose up -d --wait db
cd backend && python -m venv .venv && .venv/Scripts/python -m pip install -r requirements-dev.txt
.venv/Scripts/python -m alembic upgrade head && .venv/Scripts/python -m scripts.seed
.venv/Scripts/python -m uvicorn app.main:app --port 8000
```

```bash
cd frontend && npm install && npm run dev
```

App at http://localhost:3000, API docs at http://localhost:8000/api/docs. All test
logins share `DEMO_PASSWORD` from `.env`; e.g. `admin@festos.test` (Super Admin).

## Conventions an agent should keep

- Routes validate + authorize + call a service; **business logic lives in services**, not routes.
- Pack blanks stay `NULL`; never invent a value to fill a real record's gap.
- Invented demo rows set `is_simulated = true` and are labelled as such in the UI.
- Simulation impacts never change production data at preview; only commit does, atomically.
- Permissions are enforced server-side — adding a UI affordance is not enough.

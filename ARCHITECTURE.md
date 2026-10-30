# FestOS Command: Architecture

One command center for Aarohan (Epinoia 2026). The Ripple Simulator is the core
of the product: every important change is previewed against connected records
before it is committed.

This replaces the earlier Next.js/Prisma outline. `src/`, `prisma/` and
`scripts/ingest.py` are superseded and not used by the running app.

## Stack

| Layer | Choice | Note |
|---|---|---|
| Frontend | React + TypeScript + Vite, port 3000 | Proxies `/api` to the backend, so the session cookie stays same-origin |
| Backend | FastAPI, SQLAlchemy 2, port 8000 | Business logic lives in services, not routes |
| Database | PostgreSQL 16 in Docker | Alembic migrations |
| Auth | Local signed sessions | All token logic is in `app/auth/verifier.py`, the one file Firebase replaces |
| Simulation | Deterministic graph traversal over SQL | No LLM calculates impacts |

## Layout

```
docker-compose.yml        PostgreSQL
.env.example              Copy to .env; .env is gitignored
backend/
  alembic/                Migrations
  app/
    api/                  Routes only: validate, authorize, call a service
    auth/                 verifier (tokens), permissions (role matrix), deps
    analytics/            Dashboard metrics, computed from SQL
    database/             Engine, session, Base
    models/               SQLAlchemy tables
    schemas/              Request and response shapes
    services/             Pack import, schedule planner, simulated data
    simulation/           Dependency engine and Ripple Simulator (Phase 3)
    notifications/        Phase 4
  scripts/seed.py         Loads the festival
  tests/
frontend/src/
  components/  pages/  lib/  styles/
```

## Data provenance

Three kinds of data sit side by side, and the UI always labels which is which.

| Label | Meaning | Examples |
|---|---|---|
| `pack` | Taken from the data pack | Participants, registrations, venues, rooms, vendors, inventory |
| `generated` | Computed by our planner from pack data | Event times and venue assignment |
| `simulated` | Invented for the demo because the pack has none. Rows carry `is_simulated = true` | Vehicles, routes, shifts, tasks, budgets, expenses, sponsors, incidents |

Rules:
- A blank in the pack stays `NULL`. It never becomes a default.
- Nothing is invented to fill a pack gap on a real record. Participants have no
  email or department in the pack, so those stay empty.
- If a chart's source data does not exist, we change the chart and say so.
  Registration trends became arrivals over time, because the pack has no
  registration timestamps.

## Multi-festival scoping

Every operational table carries `festival_id`. Routes take the festival from the
path, and `festival_access` rejects users who do not belong to that festival.

## Permissions

`roles`, `permissions` and `role_permissions` are seeded from the matrix in
`app/auth/permissions.py`. Routes declare what they need with
`require("finance.read")`. The frontend hides what a role cannot use, but the
server is what enforces it. Contact, accessibility and medical details sit
behind `participants.read_sensitive`.

## Audit

`audit_logs` is append-only, enforced by a database trigger that rejects UPDATE
and DELETE. The dashboard feed reads from this table.

## Ripple Simulator

Code lives in `backend/app/simulation/`.

- `graph.py` defines seven modules and the relationships between them, and walks
  them breadth first. It knows nothing about festivals. A record's depth says how
  far an effect travelled: depth 1 is direct, deeper is indirect. Each relationship
  fires once, so cycles end.
- `scenarios.py` holds the five scenarios. Each supplies one rule per relationship,
  and each rule queries real rows. The hostel scenario reuses the real allocator.
  No model is involved at any point.
- `engine.py` runs the two-phase workflow:
  - **Preview** computes a report and stores it in `simulations` and
    `simulation_impacts`. No production row changes. The report's hash is saved.
  - **Commit** needs `simulation.approve`. In a serializable transaction it
    recomputes the report and refuses if the hash differs, so any data change that
    would alter the outcome blocks it and any change that would not does not. It
    then applies the plan, writes audit rows and sends notifications together. A
    failure at any step rolls everything back.

Roles without `simulation.approve` can simulate and request approval.
Every impact is marked as a verified fact or an estimate.

## Assistant

Code lives in `backend/app/assistant/`.

- `tools.py` holds a fixed set of read-only tools, each tied to a permission. A role's
  tools are filtered before the model sees them, and every call is checked again.
  There are no write tools, and results carry no participant names or contact details.
- `agent.py` runs a tool loop with Claude when `ANTHROPIC_API_KEY` is set. With no key,
  or if the API fails, offline mode maps the question to the same tools.
- Questions are not logged, because people may type personal details into them. The
  audit log records who asked and which tools ran.

## Live updates

After a commit that wrote an audit row, the server sends a `changed` message over a
WebSocket. The message carries no data: clients refetch through normal routes, so a
socket cannot leak a record. The hub is in-process, so it suits one API worker.

## Phases

1. Foundation: setup, auth, database, roles, festival config, Command Center
2. Core operations: events, participants, venues, accommodation, transport, volunteers
3. Dependency engine, Ripple Simulator, approval workflow, audit history
4. AI assistant, notifications, analytics, reports
5. Security testing, responsive polish, performance, integration tests, deployment

See `docs/DATA_FINDINGS.md` for what the pack contains and `docs/ASSUMPTIONS.md`
for every assumption the build depends on.

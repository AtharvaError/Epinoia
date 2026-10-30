# FestOS — Architecture

Internal control portal for **Aarohan** (Epinoia 2026, IIM Indore).
Not a registration site — a back-office decision system for organizers.

> Guiding principle: **store rows → make defensible, explainable decisions.**
> Every assumption, conflict, trade-off and override must be *visible*.

---

## 1. Stack (recommended)

| Layer | Choice | Why |
|---|---|---|
| App | **Next.js 14 (App Router) + TypeScript** | One repo, UI + API together, fast to ship in 24h |
| DB | **SQLite via Prisma** | Zero-setup, local-run acceptable, easy audit/migrations |
| UI | **Tailwind + shadcn/ui** | Clean organizer dashboards fast |
| Validation | **Zod** | Import validation + typed boundaries |
| Auth | **NextAuth (credentials) or lightweight JWT** | Role-based access + test credentials |
| Charts/tables | **TanStack Table + Recharts** | Dense organizer views |

> Swappable. The brief judges *decisions*, not the stack. If the team is stronger
> in Python, the engines (`/engines`) can move to a FastAPI service and Next.js
> stays the UI — the layer boundaries below hold either way.

---

## 2. Layered architecture

```
┌─────────────────────────────────────────────────────────┐
│  UI  (organizer dashboards, role-gated)                   │
│  dashboard · participants · accommodation · scheduling    │
│  inventory · vendors · audit                              │
└───────────────┬───────────────────────────────────────────┘
                │ API route handlers (/app/api)
┌───────────────▼───────────────────────────────────────────┐
│  DECISION ENGINES                                           │
│  accommodation · scheduling · inventory · vendors           │
│  alerts (control tower) · disruptions                       │
│  → each returns: result + violated/relaxed constraints +    │
│    trade-offs + required-reason flags                       │
└───────────────┬───────────────────────────────────────────┘
┌───────────────▼───────────────────────────────────────────┐
│  DOMAIN MODEL  (typed entities + relationships)            │
└───────────────┬───────────────────────────────────────────┘
┌───────────────▼───────────┐   ┌────────────────────────────┐
│  INGESTION & RECONCILE      │   │  AUDIT & OVERRIDE LEDGER    │
│  parse · validate · dedup   │   │  every mutation: actor,     │
│  flag · preserve uncertainty│   │  reason, before/after       │
└───────────────┬───────────┘   └──────────────┬─────────────┘
┌───────────────▼───────────────────────────────▼───────────┐
│  DATA (SQLite / Prisma)  +  /data/raw (11 source CSVs)     │
└─────────────────────────────────────────────────────────────┘
```

---

## 3. Core design decisions

### 3.1 Data is imperfect — treat it as evidence, not truth
- Narrative says ~2,400 participants; `participants.csv` has **2,836 rows**.
  The ingestion layer **reconciles and flags**, never silently drops.
- Every imported record carries **data-quality flags** (`duplicate_suspected`,
  `missing_gender`, `timing_anomaly`, `cross_file_conflict`, `unverified`).
- **A blank field is never consent, eligibility, gender, dietary clearance or
  "no need."** Blanks become explicit `UNKNOWN` requiring human resolution.

### 3.2 Hard vs soft constraints (accommodation)
Engines separate constraints that **cannot** be traded from those that can:
- **HARD** (never auto-violated): accessibility needs, declared gender-segregation,
  room capacity/readiness, check-in/out windows, budget (unless authorized override).
- **SOFT** (optimized, trade-offs shown): contingent cohesion, leader proximity,
  escort placement, travel-time comfort, cost minimization.
- Output is never "the answer" — it's a **recommendation + a list of what was
  violated or relaxed**, with a required reason for any high-risk override.

### 3.3 Everything mutating is audited
One append-only event log. Overrides additionally require a typed **reason** and
record before/after state. This powers both the privacy/audit scoring and the
"explain why a decision was made" mandate.

### 3.4 Changes propagate (the live-test requirement)
The **disruption engine** applies a state change (late contingent, hostel closure,
equipment failure, venue change, vendor shutdown, duplicate merch claim) and then
**recomputes downstream** (accommodation → transport → events → food → merch) and
returns a **consequence report** the control tower surfaces. A change is only
"complete" when its downstream effects are visible.

---

## 4. Module responsibilities

| Module | Owns |
|---|---|
| **ingestion** | Parse each CSV, validate (Zod), dedup, reconcile counts, attach quality flags |
| **domain** | Entity types + relationships (participant ↔ contingent ↔ events ↔ room ↔ merch ↔ food) |
| **engines/accommodation** | Hard/soft allocator, shortage exposure, cost-vs-cohesion explanation |
| **engines/scheduling** | Conflict detection: participant clash, team, judge, volunteer, venue capacity, equipment, setup/teardown/travel |
| **engines/inventory** | Time- & location-aware stock; merch issued **once** per person; reserved/in-transit/damaged/return states |
| **engines/vendors** | Dietary coverage, hygiene compliance, operating hours/capacity, interruption gaps, settlement |
| **engines/alerts** | Control-tower rules: what needs attention *before* someone calls the director |
| **engines/disruptions** | Apply disruption → recompute downstream → consequence report |
| **audit** | Append-only event log + override ledger (actor, reason, before/after) |
| **auth** | Roles (director, module leads, volunteer, read-only) + protected sensitive fields |

---

## 5. Sensitive-data handling (privacy scoring)
- Fields `contact`, `medical`, `accessibility`, `room` are **classified sensitive**
  and gated by role; redacted in lower-privilege views and in logs.
- Validation at every import and mutation boundary.
- Full audit history; manual overrides always carry a reason.

---

## 6. Data flow summary
1. Drop 11 CSVs into `/data/raw`.
2. `ingestion` parses → validates → dedups → reconciles → writes to SQLite with flags.
3. Organizer opens a module; API calls the relevant **engine**.
4. Engine returns `{ result, violations, relaxations, tradeoffs, needsReason }`.
5. Organizer acts; mutation goes through `audit` (+ reason if override).
6. `disruptions` / `alerts` recompute and the **control tower** shows consequences.

See **PLAN.md** for the 24-hour build sequence.

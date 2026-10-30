# UI Routes (Next.js App Router)

All routes are organizer-facing and role-gated.

| Route | View |
|---|---|
| `/login` | credentials sign-in (test creds in docs) |
| `/dashboard` | **control tower** — attention feed, shortages, live consequences |
| `/participants` | unified participant records + data-quality panel |
| `/accommodation` | allocation recommendation, violations/relaxations, override-with-reason |
| `/scheduling` | event/venue calendar + conflict list |
| `/inventory` | logistics + merch (location/time state, single-issue) |
| `/vendors` | dietary coverage, compliance, interruptions, settlement |
| `/audit` | append-only event + override history |
| `/api/*` | route handlers calling the engines |

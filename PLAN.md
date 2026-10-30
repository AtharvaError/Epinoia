# FestOS — 24-Hour Build Plan

Timeline from brief: **H0 kickoff · H12 twist · H22 code freeze · H24 presentations.**
Judging rewards *judgment* over screen count — so depth on ingestion + accommodation
+ disruption beats a wide-but-shallow app.

## Priority order (what wins points)
1. Ingestion + reconciliation (honest messy-data handling) — unlocks everything.
2. Accommodation allocator with hard/soft constraints + explanations — 20% + 20%.
3. Disruption engine + control tower — carries the live test (15%).
4. Scheduling conflict detection — 20% logic.
5. Inventory (single-issue merch) + vendors — breadth.
6. Audit/RBAC/privacy — 10% + robustness.

## Hour-by-hour

| Window | Goal |
|---|---|
| **H0–H2** | Scaffold Next.js+Prisma+Tailwind, auth stub + roles, seed empty DB |
| **H2–H5** | Ingestion: parse 11 CSVs, Zod validation, dedup, **count reconciliation (2836 vs 2400)**, quality flags |
| **H5–H8** | Domain model wired; participants view + data-quality panel (prove import works) |
| **H8–H13** | **Accommodation engine**: hard/soft constraints, shortage report, cost-vs-cohesion explain, override w/ reason |
| **H12** | **Twist drops** — route it through the disruption engine |
| **H13–H17** | Disruption engine + **control tower** (recompute downstream, consequence report) |
| **H17–H20** | Scheduling conflict detection + inventory single-issue merch |
| **H20–H22** | Vendors view, audit history polish, privacy gating, test credentials, seed sample plan |
| **H22** | **Code freeze** |
| **H22–H24** | Pitch deck (7 min) + AI disclosure slide + dry-run the live-test demo |

## Must-have demo moments
- Show the 2836 vs 2400 mismatch surfaced, not hidden.
- Recommend an allocation, then show a violated/relaxed constraint + force a reason.
- Inject a disruption live → watch downstream modules + control tower update.
- Try to issue the same merch twice → system blocks it.

## Deliverables checklist (from brief)
- [ ] Working portal judges can click through
- [ ] Sample accommodation + scheduling + inventory plan from the pack
- [ ] 7-min pitch + 3-min Q&A
- [ ] Setup instructions + test credentials (`docs/TEST_CREDENTIALS.md`)
- [ ] AI disclosure slide (`docs/AI_DISCLOSURE.md`)
- [ ] Assumptions log kept current (`docs/ASSUMPTIONS.md`)

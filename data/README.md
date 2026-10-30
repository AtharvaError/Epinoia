# Data Pack

Drop the 11 source CSVs into `./raw/` exactly as named by the brief.
The ingestion layer reads from here — nothing in `raw/` is edited in place.

| File | Expected rows | Notes |
|---|---|---|
| participants.csv | 2,836 | Narrative expects ~2,400 — reconcile the mismatch |
| registrations.csv | 5,580 | participant ↔ event mappings |
| contingents.csv | 38 | 6–140 members each |
| events.csv | 31 | rounds, judges, equipment |
| venues.csv | 9 | capacity, setup/teardown |
| hostels.csv | 200 rooms / 710 beds | on-campus |
| external_stays.csv | 6 properties / 740 beds | has cost |
| inventory_logistics.csv | 72 | moves between locations |
| inventory_merch.csv | 10 | issue once per person |
| vendors.csv | 14 | dietary, hygiene, hours, settlement |
| core_team.csv | 48 | roles, shifts |
| README.txt | — | original data guidance from the pack |

> Expect incomplete fields, inconsistent labels, timing anomalies,
> duplicate-looking records, capacity pressure, cross-file conflicts.
> Blanks are **UNKNOWN**, never consent/eligibility/clearance.

## Ingestion

`python scripts/ingest.py` reads `./festos_dataset_pack/` → writes `data/festos.db`
(SQLite, 12 tables + `data_issues` ledger) and `data/quality_report.json`.
Validates and **flags**; never drops or merges. Run `--check` for the self-test.

This is the working ingestion layer (Python + stdlib). The `src/ingestion/*.ts`
stubs are superseded — the app reads `festos.db` directly. Findings: `docs/DATA_FINDINGS.md`.

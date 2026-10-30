// Reconciliation — make the messiness explicit, never hide it.
//
// Responsibilities:
//  - Count reconciliation: narrative ~2,400 vs participants.csv 2,836.
//    Produce a report explaining the gap (duplicates? multi-day re-entries?
//    no-shows? cross-college overlaps?) — surfaced in the UI, not swallowed.
//  - Duplicate detection: fuzzy match on name/phone/college; mark, don't delete.
//  - Cross-file conflicts: e.g. registration referencing a missing participant,
//    bed counts vs assignments, event capacity vs registrations.
//
// Output feeds the data-quality panel on /participants and /dashboard.
export {};

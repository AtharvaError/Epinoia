// Disruption engine — carries the live test.
// Apply a disruption, mutate real system state, recompute downstream, return a
// consequence report. "A change is complete only when its downstream effects
// are visible."
//
// Supported disruptions:
//   late_contingent | duplicate_record | hostel_closure | equipment_failure |
//   venue_change | vendor_shutdown | duplicate_merch_claim
//
// Each handler: mutate -> re-run affected engines (accommodation, scheduling,
// inventory, vendors) -> diff before/after -> emit AuditEvent -> push to alerts.
export {};

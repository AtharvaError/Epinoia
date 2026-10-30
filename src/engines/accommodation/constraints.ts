// Accommodation constraints — the heart of the scoring.
//
// HARD (never auto-violated; override requires authorized user + reason):
//   - accessibility requirements
//   - declared gender-segregated housing (no forcing non-binary into a binary box)
//   - room capacity / readiness / cleaning
//   - check-in & check-out windows
//   - budget cap for external placements
//
// SOFT (optimized; trade-offs must be explained):
//   - keep contingent together (minimize + justify any split)
//   - leader near the group; escorts/faculty separate but nearby
//   - travel time for early/late events, transport, safety coverage
//   - cost minimization
//
// A blank field is UNKNOWN — never read as consent/gender/need.
export {};

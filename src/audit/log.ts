// Audit + override ledger — append-only.
// Every mutation records: actor, action, entity, before, after, timestamp.
// Overrides additionally require a typed `reason` and are marked high-risk.
// This is the single source for the /audit view and the "explain the decision"
// mandate. Sensitive fields are redacted here per role.
export {};

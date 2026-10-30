// Domain types — the shared vocabulary every engine speaks.
// OUTLINE ONLY. Mirror prisma/schema.prisma once models are defined.

// Standard envelope every decision engine returns.
// Nothing is ever just "the answer" — it carries what was broken/bent.
export interface Decision<T> {
  result: T;
  violations: Constraint[];   // HARD constraints broken (should be empty unless forced)
  relaxations: Constraint[];  // SOFT constraints traded off
  tradeoffs: string[];        // human-readable explanation of the trade
  needsReason: boolean;       // true => mutation requires a typed override reason
}

export interface Constraint {
  id: string;
  kind: "HARD" | "SOFT";
  label: string;
  detail: string;
}

// A blank is never consent/eligibility/gender/clearance — it is UNKNOWN.
export type Known<T> = { value: T; verified: boolean } | { value: null; verified: false };

export type QualityFlag =
  | "duplicate_suspected"
  | "missing_gender"
  | "timing_anomaly"
  | "cross_file_conflict"
  | "unverified";

export type Role = "director" | "lead" | "volunteer" | "readonly";

// TODO: Participant, Contingent, Event, Venue, Room, InventoryItem, Vendor, etc.

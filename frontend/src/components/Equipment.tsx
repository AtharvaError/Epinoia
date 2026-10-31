import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  ArrowRight,
  Boxes,
  CheckCircle2,
  ChevronRight,
  ClipboardCheck,
  PackageCheck,
  PackagePlus,
  Plus,
  RotateCcw,
  Truck,
  Wrench,
  X,
} from "lucide-react";
import { useEffect, useState } from "react";

import { api } from "../lib/api";
import { formatTime } from "../lib/format";
import {
  Dialog,
  ErrorNote,
  Facts,
  Field,
  Pill,
  QueryState,
  SectionHead,
  fromInput,
  toInput,
  useAction,
  useFest,
} from "./ui";

export { formatTime };

// ============================================================================
// Types
// ============================================================================

export interface EquipmentSummary {
  festival_id: number;
  metrics: {
    total_units: number;
    deployable_now: number;
    in_transit: number;
    needs_inspection: number;
    under_repair: number;
  };
  attention: {
    awaiting_approval: number;
    lines_short: number;
    needs_inspection: number;
    open_discrepancies: number;
  };
  counts: {
    requests: number;
    open_requests: number;
    open_transfers: number;
    stock_items: number;
  };
}

export interface AvailabilityLocation {
  code: string;
  location: string | null;
  condition: string | null;
  quantity: number;
  committed: number;
  available: number;
  pending_requests: number;
  under_repair: number;
  needs_check: number;
}

export interface AvailabilityBreakdown {
  item: string;
  required: number;
  destination: string | null;
  good_total: number;
  good_available: number;
  committed_elsewhere: number;
  pending_requests_elsewhere: number;
  under_repair: number;
  needs_check: number;
  shortage: number;
  feasibility: "full" | "partial" | "none";
  inspection_could_cover: boolean;
  locations: AvailabilityLocation[];
  plan: { resource_code: string; from_location: string | null; quantity: number; transfer_needed: boolean }[];
}

export interface TransferRow {
  id: number;
  kind: "delivery" | "return";
  parent_id: number | null;
  resource_code: string | null;
  item: string | null;
  from_location: string;
  to_location: string;
  status: "REQUESTED" | "APPROVED" | "DISPATCHED" | "IN_TRANSIT" | "RECEIVED" | "PARTIALLY_RECEIVED" | "COMPLETED";
  quantity_planned: number;
  quantity_approved: number | null;
  quantity_dispatched: number | null;
  quantity_received: number | null;
  quantity_damaged: number | null;
  reserved_quantity: number;
  verified: boolean;
  stages: Record<string, { at: string | null; by: string; quantity: number | null; note: string | null }>;
  discrepancy_note: string | null;
  condition_note: string | null;
  request_code?: string;
  created_at?: string;
}

export interface EquipmentRequestRow {
  code: string;
  event: { code: string; name: string; start_at: string | null; end_at: string | null; status: string } | null;
  item: string;
  quantity: number;
  destination: string;
  needed_by: string;
  priority: "low" | "normal" | "high" | "critical";
  notes: string | null;
  source: string;
  source_text: string | null;
  status: string;
  availability: AvailabilityBreakdown;
  requested_by: string;
  created_at: string;
  decided_by: string | null;
  decided_at: string | null;
  decision_reason: string | null;
  shortage_decision: string | null;
  readiness: string;
  verified_received: number;
  approved_quantity: number;
  return_status: string | null;
  transfers: TransferRow[];
  lines?: { id: number; item: string; quantity: number; source_text: string | null; notes: string | null }[];
}

export interface ParsedNeed {
  raw: string;
  item: string | null;
  quantity: number | null;
  matched: boolean;
  quantity_source: string;
  preview?: AvailabilityBreakdown;
}

export interface EventEquipmentResponse {
  event: {
    code: string;
    name: string;
    venue: string | null;
    start_at: string | null;
    end_at: string | null;
    status: string;
    completed: boolean;
    category?: string;
  };
  equipment_needs_text: string | null;
  venue_requirements_text?: string | null;
  venue_suggestions?: { token: string; hint: string }[];
  parsed_needs: ParsedNeed[];
  requests: EquipmentRequestRow[];
  readiness: string;
}

export interface EquipmentStockRow {
  item: string;
  total_quantity: number;
  good_available: number;
  committed: number;
  needs_check: number;
  under_repair: number;
  locations: {
    resource_code: string;
    location: string;
    condition: string;
    quantity: number;
    reserved_quantity: number;
    available: number;
    unverified?: boolean;
  }[];
}

export interface StockItemDetail {
  item: string;
  total: number;
  good: number;
  needs_check: number;
  under_repair: number;
  locations: {
    resource_code: string;
    location: string;
    condition: string;
    quantity: number;
    reserved_quantity: number;
    available: number;
  }[];
  demanding_events: {
    code: string;
    name: string;
    start_at: string | null;
    venue: string | null;
    declared_need: string | null;
    parsed_qty: number | null;
    request_status: string | null;
    allocated_qty: number | null;
  }[];
  movement_history: {
    id: number;
    timestamp: string;
    action: string;
    quantity: number;
    from_location: string | null;
    to_location: string | null;
    user_name: string | null;
    notes: string | null;
  }[];
}

export interface LedgerRow {
  id: number;
  timestamp: string;
  action: string;
  item: string;
  quantity: number;
  resource_code: string | null;
  from_location: string | null;
  to_location: string | null;
  request_code: string | null;
  user_name: string | null;
  notes: string | null;
}

export interface EquipmentOptions {
  items: string[];
  inventory_locations: string[];
  venues: string[];
  priorities: string[];
  lifecycle: string[];
}

// ============================================================================
// Helpers
// ============================================================================

export function readinessBadge(readiness: string): { label: string; tone: "ok" | "warn" | "bad" | "info" } {
  switch (readiness) {
    case "satisfied":
      return { label: "Equipment requirement satisfied", tone: "ok" };
    case "partially_satisfied":
      return { label: "Partially satisfied", tone: "warn" };
    case "pending_transfer":
      return { label: "Pending transfer", tone: "warn" };
    case "awaiting_approval":
      return { label: "Awaiting logistics approval", tone: "info" };
    case "requires_inspection":
      return { label: "Requires inspection", tone: "warn" };
    case "unavailable":
      return { label: "Unavailable", tone: "bad" };
    case "no_equipment_needed":
      return { label: "No special equipment", tone: "ok" };
    default:
      return { label: "Not requested yet", tone: "warn" };
  }
}

export function defaultNeededBy(startAt: string | null | undefined): string {
  if (!startAt) return "2026-10-30T09:00";
  const d = new Date(new Date(startAt).getTime() - 3600 * 1000);
  return toInput(d.toISOString());
}

// ============================================================================
// 1. Metric Ribbon (5 equal cards)
// ============================================================================

export function EquipmentSummaryRibbon({ summary }: { summary: EquipmentSummary | undefined }) {
  if (!summary) return null;
  const { metrics } = summary;

  return (
    <div className="kpi-grid-5">
      <div className="kpi">
        <div className="kpi-head">
          <h3>Total units</h3>
          <Boxes size={18} className="muted" />
        </div>
        <p className="kpi-value">{metrics.total_units} <span>units</span></p>
        <p className="kpi-note">18 items · 5 locations</p>
      </div>

      <div className="kpi" style={{ borderLeft: "3px solid #34d399" }}>
        <div className="kpi-head">
          <h3>Deployable now</h3>
          <CheckCircle2 size={18} style={{ color: "#34d399" }} />
        </div>
        <p className="kpi-value" style={{ color: "#34d399" }}>{metrics.deployable_now} <span>good</span></p>
        <p className="kpi-note">Ready for allocation</p>
      </div>

      <div className="kpi" style={{ borderLeft: "3px solid #38bdf8" }}>
        <div className="kpi-head">
          <h3>In transit</h3>
          <Truck size={18} style={{ color: "#38bdf8" }} />
        </div>
        <p className="kpi-value" style={{ color: "#38bdf8" }}>{metrics.in_transit} <span>moving</span></p>
        <p className="kpi-note">Between campus sites</p>
      </div>

      <div className="kpi" style={{ borderLeft: "3px solid #fbbf24" }}>
        <div className="kpi-head">
          <h3>Needs inspection</h3>
          <ClipboardCheck size={18} style={{ color: "#fbbf24" }} />
        </div>
        <p className="kpi-value" style={{ color: "#fbbf24" }}>{metrics.needs_inspection} <span>check</span></p>
        <p className="kpi-note">Excluded until inspected</p>
      </div>

      <div className="kpi" style={{ borderLeft: "3px solid #f87171" }}>
        <div className="kpi-head">
          <h3>Under repair</h3>
          <Wrench size={18} style={{ color: "#f87171" }} />
        </div>
        <p className="kpi-value" style={{ color: "#f87171" }}>{metrics.under_repair} <span>broken</span></p>
        <p className="kpi-note">Excluded from events</p>
      </div>
    </div>
  );
}

// ============================================================================
// 2. Attention Strip
// ============================================================================

export function EquipmentAttentionStrip({
  attention,
  onFilterChange,
}: {
  attention: EquipmentSummary["attention"] | undefined;
  onFilterChange: (tab: "requests" | "stock" | "transfers", subFilter?: string) => void;
}) {
  if (!attention) return null;

  return (
    <div className="attention-strip">
      <span className="strip-title">Attention:</span>

      <button
        type="button"
        className={`attention-chip ${attention.awaiting_approval > 0 ? "tone-warn" : "tone-ok"}`}
        onClick={() => onFilterChange("requests", "submitted")}
      >
        <AlertTriangle size={14} />
        {attention.awaiting_approval} awaiting approval
      </button>

      <button
        type="button"
        className={`attention-chip ${attention.lines_short > 0 ? "tone-bad" : "tone-ok"}`}
        onClick={() => onFilterChange("requests", "shortage")}
      >
        <AlertTriangle size={14} />
        {attention.lines_short} lines short
      </button>

      <button
        type="button"
        className={`attention-chip ${attention.needs_inspection > 0 ? "tone-warn" : "tone-ok"}`}
        onClick={() => onFilterChange("stock", "needs check")}
      >
        <ClipboardCheck size={14} />
        {attention.needs_inspection} units need inspection
      </button>

      <button
        type="button"
        className={`attention-chip ${attention.open_discrepancies > 0 ? "tone-bad" : "tone-ok"}`}
        onClick={() => onFilterChange("transfers", "discrepancies")}
      >
        <Truck size={14} />
        {attention.open_discrepancies} open discrepancies
      </button>
    </div>
  );
}

// ============================================================================
// 3. Condition Segmented Bar
// ============================================================================

export function ConditionSegmentedBar({
  good,
  needsCheck,
  underRepair,
  total,
}: {
  good: number;
  needsCheck: number;
  underRepair: number;
  total: number;
}) {
  if (total <= 0) return <span className="muted small">0 units</span>;
  const pGood = Math.round((good / total) * 100);
  const pCheck = Math.round((needsCheck / total) * 100);
  const pRepair = 100 - pGood - pCheck;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 3, minWidth: 140 }}>
      <div className="condition-bar" title={`Good: ${good} (${pGood}%), Needs Check: ${needsCheck} (${pCheck}%), Under Repair: ${underRepair} (${pRepair}%)`}>
        {pGood > 0 && <div className="condition-seg good" style={{ width: `${pGood}%` }} />}
        {pCheck > 0 && <div className="condition-seg check" style={{ width: `${pCheck}%` }} />}
        {pRepair > 0 && <div className="condition-seg repair" style={{ width: `${pRepair}%` }} />}
      </div>
      <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11, color: "var(--muted)" }}>
        <span style={{ color: "#34d399" }}>{good} good</span>
        {needsCheck > 0 && <span style={{ color: "#fbbf24" }}>{needsCheck} check</span>}
        {underRepair > 0 && <span style={{ color: "#f87171" }}>{underRepair} rep</span>}
      </div>
    </div>
  );
}

// ============================================================================
// 4. Equipment Request Drawer (Detail & Allocation)
// ============================================================================

export function EquipmentRequestDrawer({
  code,
  onClose,
}: {
  code: string;
  onClose: () => void;
}) {
  const { base, can } = useFest();
  const qc = useQueryClient();

  const reqQuery = useQuery({
    queryKey: ["equipment-request", base, code],
    queryFn: () =>
      api<{ items: EquipmentRequestRow[] }>(`${base}/equipment/requests?q=${encodeURIComponent(code)}`).then(
        (res) => res.items.find((r) => r.code === code) ?? null
      ),
  });

  const [rejectReason, setRejectReason] = useState("");
  const [rejecting, setRejecting] = useState(false);
  const [shortageDecision, setShortageDecision] = useState<string>("transfer");
  const [allowNeedsCheck, setAllowNeedsCheck] = useState(false);
  const [returning, setReturning] = useState(false);
  const [returnQty, setReturnQty] = useState("");
  const [returnDamaged, setReturnDamaged] = useState("0");
  const [returnNotes, setReturnNotes] = useState("");

  const approve = useAction(
    () =>
      api<EquipmentRequestRow>(`${base}/equipment/requests/${code}/approve`, {
        method: "POST",
        body: JSON.stringify({
          shortage_decision: shortageDecision,
          allow_needs_check: allowNeedsCheck,
        }),
      }),
    {
      success: "Request approved and stock allocated",
      onDone: () => {
        qc.invalidateQueries({ queryKey: ["equipment-requests"] });
        qc.invalidateQueries({ queryKey: ["equipment-summary"] });
        qc.invalidateQueries({ queryKey: ["equipment-stock"] });
        reqQuery.refetch();
      },
    }
  );

  const reject = useAction(
    () =>
      api<EquipmentRequestRow>(`${base}/equipment/requests/${code}/reject`, {
        method: "POST",
        body: JSON.stringify({ reason: rejectReason.trim() }),
      }),
    {
      success: "Request rejected",
      onDone: () => {
        setRejecting(false);
        qc.invalidateQueries({ queryKey: ["equipment-requests"] });
        qc.invalidateQueries({ queryKey: ["equipment-summary"] });
        reqQuery.refetch();
      },
    }
  );

  const returnAction = useAction(
    () =>
      api(`${base}/equipment/requests/${code}/return`, {
        method: "POST",
        body: JSON.stringify({
          returned_quantity: Number(returnQty) || reqQuery.data?.approved_quantity || 1,
          damaged: Number(returnDamaged) || 0,
          note: returnNotes.trim() || null,
        }),
      }),
    {
      success: "Return initiated. Stock routed to inspection.",
      onDone: () => {
        setReturning(false);
        qc.invalidateQueries({ queryKey: ["equipment-requests"] });
        qc.invalidateQueries({ queryKey: ["equipment-summary"] });
        reqQuery.refetch();
      },
    }
  );

  const r = reqQuery.data;

  return (
    <Dialog title={r ? `Equipment Request ${r.code}` : code} onClose={onClose} side className="drawer-wide">
      <QueryState query={reqQuery}>
        {r && (
          <div className="stack">
            <div className="row-actions">
              <Pill value={r.status} tone={r.status === "approved" ? "ok" : r.status === "rejected" ? "bad" : "warn"} />
              <Pill value={r.priority} tone={r.priority === "critical" || r.priority === "high" ? "warn" : "info"} />
              {r.source === "event_csv" && <Pill value="from events.csv" tone="ok" />}
            </div>

            <Facts
              items={[
                ["Event", r.event ? `${r.event.code} — ${r.event.name}` : "—"],
                ["Item", `${r.quantity} × ${r.item}`],
                ["Destination", r.destination],
                ["Needed by", formatTime(r.needed_by)],
                ["Requested by", `${r.requested_by} on ${formatTime(r.created_at)}`],
                ["Readiness", readinessBadge(r.readiness).label],
                ["Approved Qty", r.approved_quantity > 0 ? `${r.approved_quantity} units` : "Not approved"],
                ["Verified Received", `${r.verified_received} / ${r.quantity}`],
              ]}
            />

            {r.source_text && (
              <div className="callout small">
                <strong>Declared in events.csv:</strong> <code>{r.source_text}</code>
              </div>
            )}

            {r.notes && (
              <div>
                <h4>Organizer Notes</h4>
                <p className="small muted">{r.notes}</p>
              </div>
            )}

            {/* Availability Breakdown */}
            <section className="panel stack">
              <SectionHead title="Availability Breakdown across 5 Locations">
                <span className="muted small">Live campus inventory state</span>
              </SectionHead>

              <div className="stat-row">
                <div>
                  <span>Required</span>
                  <strong>{r.quantity}</strong>
                </div>
                <div>
                  <span style={{ color: "#34d399" }}>Good Free</span>
                  <strong style={{ color: "#34d399" }}>{r.availability.good_available}</strong>
                </div>
                <div>
                  <span>Committed</span>
                  <strong>{r.availability.committed_elsewhere}</strong>
                </div>
                <div>
                  <span style={{ color: "#fbbf24" }}>Needs Check</span>
                  <strong style={{ color: "#fbbf24" }}>{r.availability.needs_check}</strong>
                </div>
                <div>
                  <span style={{ color: "#f87171" }}>Under Repair</span>
                  <strong style={{ color: "#f87171" }}>{r.availability.under_repair}</strong>
                </div>
                <div>
                  <span style={{ color: r.availability.shortage > 0 ? "#f87171" : "#34d399" }}>Shortage</span>
                  <strong style={{ color: r.availability.shortage > 0 ? "#f87171" : "#34d399" }}>
                    {r.availability.shortage}
                  </strong>
                </div>
              </div>

              <h4>Source Location Balances</h4>
              <ul className="stack">
                {r.availability.locations.map((loc) => {
                  const isInPlace = loc.location?.trim().toLowerCase() === r.destination.trim().toLowerCase();
                  return (
                    <li key={loc.code} style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                      <div>
                        <strong>{loc.location ?? "Unknown"}</strong>
                        {isInPlace && <span className="tone-ok small" style={{ marginLeft: 6 }}>· In-place (no transfer needed)</span>}
                        <br />
                        <span className="muted small">
                          Good: {loc.available} free / {loc.quantity} total · Committed: {loc.committed} · Check: {loc.needs_check} · Repair: {loc.under_repair}
                        </span>
                      </div>
                      <Pill value={`${loc.available} free`} tone={loc.available > 0 ? "ok" : "warn"} />
                    </li>
                  );
                })}
              </ul>
            </section>

            {/* Proposed / Active Transfers */}
            {r.transfers.length > 0 && (
              <section className="panel stack">
                <SectionHead title={`Logistics Transfers (${r.transfers.length})`} />
                <ul className="stack">
                  {r.transfers.map((t) => (
                    <li key={t.id} style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                      <div>
                        <strong>{t.quantity_planned} × {t.item}</strong> ({t.from_location} → {t.to_location})
                        <br />
                        <span className="muted small">Status: {t.status} {t.discrepancy_note ? `· Discrepancy: ${t.discrepancy_note}` : ""}</span>
                      </div>
                      <Pill value={t.status} tone={t.status === "COMPLETED" || t.status === "RECEIVED" ? "ok" : "warn"} />
                    </li>
                  ))}
                </ul>
              </section>
            )}

            {/* Actions for Logistics Manager */}
            {r.status === "submitted" && can("equipment.approve") && (
              <section className="panel stack">
                <SectionHead title="Logistics Approval & Allocation" />

                {r.availability.shortage > 0 && (
                  <div className="callout warn small">
                    <strong>Shortage of {r.availability.shortage} units detected.</strong>
                    <Field label="Shortage Decision">
                      <select value={shortageDecision} onChange={(e) => setShortageDecision(e.target.value)}>
                        <option value="transfer">Transfer available and wait/source balance</option>
                        <option value="inspect">Inspect {r.availability.needs_check} units in Needs Check to cover shortage</option>
                        <option value="source_externally">Source externally / rent balance</option>
                        <option value="accept_reduced">Accept reduced allocation</option>
                      </select>
                    </Field>
                    {r.availability.needs_check > 0 && (
                      <label className="check-inline" style={{ marginTop: 8 }}>
                        <input
                          type="checkbox"
                          checked={allowNeedsCheck}
                          onChange={(e) => setAllowNeedsCheck(e.target.checked)}
                        />
                        <span>Allow conditional allocation of Needs Check units pending pre-event inspection</span>
                      </label>
                    )}
                  </div>
                )}

                <div className="row-actions" style={{ marginTop: 8 }}>
                  <button
                    type="button"
                    className="button-primary"
                    disabled={approve.isPending}
                    onClick={() => approve.mutate()}
                  >
                    <PackageCheck size={16} /> Approve & Allocate
                  </button>

                  <button
                    type="button"
                    className="button-ghost danger"
                    onClick={() => setRejecting(!rejecting)}
                  >
                    Reject Request
                  </button>
                </div>
                <ErrorNote error={approve.error} />

                {rejecting && (
                  <div className="stack" style={{ marginTop: 10 }}>
                    <Field label="Rejection reason">
                      <input
                        placeholder="State why equipment cannot be allocated"
                        value={rejectReason}
                        onChange={(e) => setRejectReason(e.target.value)}
                      />
                    </Field>
                    <button
                      type="button"
                      className="button-primary danger small"
                      disabled={reject.isPending || rejectReason.trim().length < 3}
                      onClick={() => reject.mutate()}
                    >
                      Confirm Rejection
                    </button>
                    <ErrorNote error={reject.error} />
                  </div>
                )}
              </section>
            )}

            {/* Post-event return */}
            {(r.status === "approved" || r.status === "partially_approved") && can("equipment.receive") && (
              <section className="panel stack">
                <SectionHead title="Post-Event Equipment Return" />
                {!returning ? (
                  <button
                    type="button"
                    className="button-secondary small"
                    onClick={() => {
                      setReturnQty(String(r.approved_quantity));
                      setReturning(true);
                    }}
                  >
                    <RotateCcw size={16} /> Initiate Return to Inventory
                  </button>
                ) : (
                  <form
                    className="stack"
                    onSubmit={(e) => {
                      e.preventDefault();
                      returnAction.mutate();
                    }}
                  >
                    <div className="form-grid">
                      <Field label="Returned Quantity">
                        <input
                          type="number"
                          min={0}
                          max={r.approved_quantity}
                          required
                          value={returnQty}
                          onChange={(e) => setReturnQty(e.target.value)}
                        />
                      </Field>
                      <Field label="Damaged Quantity">
                        <input
                          type="number"
                          min={0}
                          max={Number(returnQty) || r.approved_quantity}
                          value={returnDamaged}
                          onChange={(e) => setReturnDamaged(e.target.value)}
                        />
                      </Field>
                    </div>
                    <Field label="Return Inspection Notes">
                      <input
                        placeholder="Condition notes upon return"
                        value={returnNotes}
                        onChange={(e) => setReturnNotes(e.target.value)}
                      />
                    </Field>
                    <p className="muted small">
                      Returned units default to <strong>Needs check</strong> unless explicitly inspected and cleared.
                    </p>
                    <div className="row-actions">
                      <button type="submit" className="button-primary small" disabled={returnAction.isPending}>
                        Confirm Return Receipt
                      </button>
                      <button type="button" className="button-ghost small" onClick={() => setReturning(false)}>
                        Cancel
                      </button>
                    </div>
                    <ErrorNote error={returnAction.error} />
                  </form>
                )}
              </section>
            )}
          </div>
        )}
      </QueryState>
    </Dialog>
  );
}

// ============================================================================
// 5. 3-Step New Equipment Request Drawer
// ============================================================================

export function NewEquipmentRequestDrawer({
  defaultEventCode,
  onClose,
}: {
  defaultEventCode?: string | null;
  onClose: () => void;
}) {
  const { base } = useFest();
  const qc = useQueryClient();

  const DRAFT_KEY = "festos.draft.equipment_request";

  // Step state (1: Select event, 2: Configure lines, 3: Review)
  const [step, setStep] = useState<1 | 2 | 3>(1);

  // Form State
  const [eventCode, setEventCode] = useState(defaultEventCode ?? "");
  const [destination, setDestination] = useState("");
  const [neededBy, setNeededBy] = useState("2026-10-30T09:00");
  const [priority, setPriority] = useState<"low" | "normal" | "high" | "critical">("normal");
  const [notes, setNotes] = useState("");
  const [lines, setLines] = useState<{ item: string; quantity: string; source_text?: string; notes?: string }[]>([
    { item: "", quantity: "", source_text: "", notes: "" },
  ]);

  // Options Query
  const optionsQuery = useQuery({
    queryKey: ["equipment-options", base],
    queryFn: () => api<EquipmentOptions>(`${base}/equipment/options`),
  });

  // Events list for dropdown
  const eventsQuery = useQuery({
    queryKey: ["events-lookup", base],
    queryFn: () => api<{ events: { code: string; name: string; venue?: string; start_at?: string }[] }>(`${base}/events/options`),
  });

  // Event equipment demand from events.csv
  const eventEqQuery = useQuery({
    queryKey: ["event-equipment-parsed", base, eventCode],
    queryFn: () => api<EventEquipmentResponse>(`${base}/equipment/events/${eventCode}`),
    enabled: Boolean(eventCode),
  });

  // Load draft from localStorage on mount
  useEffect(() => {
    try {
      const raw = localStorage.getItem(DRAFT_KEY);
      if (raw && !defaultEventCode) {
        const d = JSON.parse(raw);
        if (d.eventCode) setEventCode(d.eventCode);
        if (d.destination) setDestination(d.destination);
        if (d.neededBy) setNeededBy(d.neededBy);
        if (d.priority) setPriority(d.priority);
        if (d.notes) setNotes(d.notes);
        if (d.lines?.length) setLines(d.lines);
      }
    } catch {
      // ignore
    }
  }, [defaultEventCode]);

  // Persist draft to localStorage on change
  useEffect(() => {
    try {
      localStorage.setItem(
        DRAFT_KEY,
        JSON.stringify({ eventCode, destination, neededBy, priority, notes, lines })
      );
    } catch {
      // ignore
    }
  }, [eventCode, destination, neededBy, priority, notes, lines]);

  // Populate from parsed needs when event selected
  const handleSelectEvent = (code: string) => {
    setEventCode(code);
    const ev = eventsQuery.data?.events.find((e) => e.code === code);
    if (ev) {
      if (ev.venue) setDestination(ev.venue);
      if (ev.start_at) setNeededBy(defaultNeededBy(ev.start_at));
    }
  };

  // Prefill lines from events.csv parsed tokens
  const applyParsedNeeds = () => {
    if (!eventEqQuery.data) return;
    const { parsed_needs, event } = eventEqQuery.data;
    if (event.venue && !destination) setDestination(event.venue);
    if (event.start_at) setNeededBy(defaultNeededBy(event.start_at));

    if (parsed_needs.length > 0) {
      const newLines = parsed_needs.map((pn) => ({
        item: pn.item ?? "",
        // Never default unnumbered tokens to 1; leave blank if unknown
        quantity: pn.quantity !== null && pn.quantity !== undefined ? String(pn.quantity) : "",
        source_text: pn.raw,
        notes: pn.quantity === null ? "Quantity not in events.csv; confirm required amount" : "",
      }));
      setLines(newLines);
    }
    setStep(2);
  };

  const addLine = () => {
    setLines([...lines, { item: optionsQuery.data?.items[0] ?? "", quantity: "", notes: "" }]);
  };

  const removeLine = (idx: number) => {
    setLines(lines.filter((_, i) => i !== idx));
  };

  const updateLine = (idx: number, patch: Partial<(typeof lines)[0]>) => {
    const next = [...lines];
    next[idx] = { ...next[idx], ...patch };
    setLines(next);
  };

  // Submit action
  const submitAction = useAction(
    () => {
      const formattedLines = lines
        .filter((l) => l.item.trim() && Number(l.quantity) > 0)
        .map((l) => ({
          item: l.item.trim(),
          quantity: Number(l.quantity),
          source_text: l.source_text || null,
          notes: l.notes || null,
        }));

      return api<EquipmentRequestRow>(`${base}/equipment/requests`, {
        method: "POST",
        body: JSON.stringify({
          event_code: eventCode,
          destination: destination.trim(),
          needed_by: fromInput(neededBy),
          priority,
          notes: notes.trim() || null,
          lines: formattedLines,
        }),
      });
    },
    {
      success: (res) => {
        try {
          localStorage.removeItem(DRAFT_KEY);
        } catch {
          // ignore
        }
        qc.invalidateQueries({ queryKey: ["equipment-requests"] });
        qc.invalidateQueries({ queryKey: ["equipment-summary"] });
        return `Created equipment request ${res.code} for ${eventCode}`;
      },
      onDone: onClose,
    }
  );

  const ev = eventsQuery.data?.events.find((e) => e.code === eventCode);
  const parsedData = eventEqQuery.data;

  return (
    <Dialog title="New Equipment Request" onClose={onClose} side className="drawer-wide">
      <div className="stack">
        {/* Wizard Progress Bar */}
        <div style={{ display: "flex", gap: 8, borderBottom: "1px solid var(--line)", paddingBottom: 12 }}>
          <button
            type="button"
            className={`pill ${step === 1 ? "active" : ""}`}
            style={{ fontWeight: 600 }}
            onClick={() => setStep(1)}
          >
            1. Select Event
          </button>
          <ChevronRight size={16} className="muted" />
          <button
            type="button"
            className={`pill ${step === 2 ? "active" : ""}`}
            style={{ fontWeight: 600 }}
            onClick={() => eventCode && setStep(2)}
            disabled={!eventCode}
          >
            2. Configure Lines
          </button>
          <ChevronRight size={16} className="muted" />
          <button
            type="button"
            className={`pill ${step === 3 ? "active" : ""}`}
            style={{ fontWeight: 600 }}
            onClick={() => eventCode && lines.some((l) => Number(l.quantity) > 0) && setStep(3)}
            disabled={!eventCode || !lines.some((l) => Number(l.quantity) > 0)}
          >
            3. Review & Submit
          </button>
        </div>

        {/* STEP 1: Select Event & Inspect Demand */}
        {step === 1 && (
          <div className="stack">
            <Field label="Choose Event (from 31 events in events.csv)">
              <select value={eventCode} onChange={(e) => handleSelectEvent(e.target.value)}>
                <option value="">Select an event...</option>
                {eventsQuery.data?.events.map((e) => (
                  <option key={e.code} value={e.code}>
                    {e.code} — {e.name} ({e.venue ?? "No venue"})
                  </option>
                ))}
              </select>
            </Field>

            {eventCode && parsedData && (
              <div className="panel stack" style={{ background: "var(--surface-2)" }}>
                <h4>Event Equipment Demand (Ground Truth)</h4>
                <Facts
                  items={[
                    ["Event", `${parsedData.event.code} — ${parsedData.event.name}`],
                    ["Assigned Venue", parsedData.event.venue ?? "None"],
                    ["Schedule", parsedData.event.start_at ? formatTime(parsedData.event.start_at) : "Unscheduled"],
                    ["Raw equipment_needs", parsedData.equipment_needs_text ?? "No special equipment needed"],
                    ["Readiness", readinessBadge(parsedData.readiness).label],
                  ]}
                />

                {parsedData.parsed_needs.length > 0 ? (
                  <div>
                    <h5>Parsed Equipment Tokens</h5>
                    <ul className="stack">
                      {parsedData.parsed_needs.map((pn, i) => (
                        <li key={i} style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                          <div>
                            <code>{pn.raw}</code> → <strong>{pn.item ?? "Unmatched"}</strong>
                            {pn.quantity !== null ? (
                              <span className="tone-ok small" style={{ marginLeft: 6 }}>
                                (Qty: {pn.quantity} from events.csv)
                              </span>
                            ) : (
                              <span className="tone-warn small" style={{ marginLeft: 6 }}>
                                (Quantity unknown; enter required amount)
                              </span>
                            )}
                          </div>
                          <Pill value={pn.item ? "Recognized" : "Unmatched"} tone={pn.item ? "ok" : "warn"} />
                        </li>
                      ))}
                    </ul>
                  </div>
                ) : (
                  <p className="muted small">
                    This event declares no special equipment in <code>events.csv</code>. You may add custom items in step 2 if needed.
                  </p>
                )}

                {/* Venue suggestions */}
                {parsedData.venue_suggestions && parsedData.venue_suggestions.length > 0 && (
                  <div className="callout info small">
                    <strong>Classified Venue Setup Hints:</strong>
                    <ul>
                      {parsedData.venue_suggestions.map((vs, idx) => (
                        <li key={idx}>{vs.hint}</li>
                      ))}
                    </ul>
                  </div>
                )}

                <div className="row-actions" style={{ marginTop: 12 }}>
                  <button type="button" className="button-primary" onClick={applyParsedNeeds}>
                    Continue with Event Demand <ArrowRight size={16} />
                  </button>
                </div>
              </div>
            )}
          </div>
        )}

        {/* STEP 2: Configure Lines */}
        {step === 2 && (
          <div className="stack">
            <div className="form-grid">
              <Field label="Destination Location (Venue)">
                <input
                  required
                  placeholder="e.g. Main Stage"
                  value={destination}
                  onChange={(e) => setDestination(e.target.value)}
                />
              </Field>

              <Field label="Needed By Deadline">
                <input
                  type="datetime-local"
                  required
                  value={neededBy}
                  onChange={(e) => setNeededBy(e.target.value)}
                />
              </Field>

              <Field label="Priority">
                <select value={priority} onChange={(e) => setPriority(e.target.value as any)}>
                  <option value="low">Low</option>
                  <option value="normal">Normal</option>
                  <option value="high">High</option>
                  <option value="critical">Critical</option>
                </select>
              </Field>
            </div>

            <SectionHead title={`Equipment Lines (${lines.length})`}>
              <button type="button" className="button-secondary small" onClick={addLine}>
                <Plus size={14} /> Add Line
              </button>
            </SectionHead>

            {lines.map((line, idx) => (
              <div key={idx} className="panel stack" style={{ border: "1px solid var(--line)" }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                  <strong>Line #{idx + 1}</strong>
                  {lines.length > 1 && (
                    <button type="button" className="button-ghost small danger" onClick={() => removeLine(idx)}>
                      <X size={14} /> Remove
                    </button>
                  )}
                </div>

                {line.source_text && (
                  <small className="muted">
                    Source: <code>{line.source_text}</code>
                  </small>
                )}

                <div className="form-grid">
                  <Field label="Equipment Item">
                    <select
                      value={line.item}
                      onChange={(e) => updateLine(idx, { item: e.target.value })}
                      required
                    >
                      <option value="">Choose item...</option>
                      {optionsQuery.data?.items.map((it) => (
                        <option key={it} value={it}>
                          {it}
                        </option>
                      ))}
                    </select>
                  </Field>

                  <Field
                    label="Required Quantity"
                    hint={!line.quantity ? "Explicit quantity required (never defaulted to 1)" : undefined}
                  >
                    <input
                      type="number"
                      min={1}
                      required
                      placeholder="Enter quantity"
                      value={line.quantity}
                      onChange={(e) => updateLine(idx, { quantity: e.target.value })}
                    />
                  </Field>
                </div>

                <Field label="Item Notes / Source Spec">
                  <input
                    placeholder="e.g. 20 barricades for crowd control"
                    value={line.notes ?? ""}
                    onChange={(e) => updateLine(idx, { notes: e.target.value })}
                  />
                </Field>
              </div>
            ))}

            <Field label="General Request Notes">
              <textarea
                rows={2}
                placeholder="Logistics coordination notes"
                value={notes}
                onChange={(e) => setNotes(e.target.value)}
              />
            </Field>

            <div className="row-actions" style={{ marginTop: 12 }}>
              <button type="button" className="button-ghost" onClick={() => setStep(1)}>
                Back
              </button>
              <button
                type="button"
                className="button-primary"
                disabled={!destination || lines.some((l) => !l.item || Number(l.quantity) <= 0)}
                onClick={() => setStep(3)}
              >
                Review & Confirm <ArrowRight size={16} />
              </button>
            </div>
          </div>
        )}

        {/* STEP 3: Review & Submit */}
        {step === 3 && (
          <div className="stack">
            <h4>Review Equipment Request</h4>
            <Facts
              items={[
                ["Event", `${ev?.code ?? eventCode} — ${ev?.name ?? ""}`],
                ["Destination", destination],
                ["Needed By", formatTime(fromInput(neededBy) ?? "")],
                ["Priority", priority.toUpperCase()],
                ["Total Lines", lines.length],
                ["Notes", notes || "None"],
              ]}
            />

            <h5>Requested Items</h5>
            <ul className="stack">
              {lines.map((l, i) => (
                <li key={i} style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                  <div>
                    <strong>{l.quantity} × {l.item}</strong>
                    {l.source_text && <br />}
                    {l.source_text && <span className="muted small">Source: {l.source_text}</span>}
                  </div>
                  <Pill value={`${l.quantity} units`} tone="info" />
                </li>
              ))}
            </ul>

            <div className="callout small info">
              Submitting creates this equipment request in <code>submitted</code> state. FestOS will check balances across the 5 campus locations and generate proposed transfers for the Logistics Manager to approve.
            </div>

            <ErrorNote error={submitAction.error} />

            <div className="row-actions" style={{ marginTop: 12 }}>
              <button type="button" className="button-ghost" onClick={() => setStep(2)}>
                Back to Edit
              </button>
              <button
                type="button"
                className="button-primary"
                disabled={submitAction.isPending}
                onClick={() => submitAction.mutate()}
              >
                <PackagePlus size={16} /> Confirm & Submit Request
              </button>
            </div>
          </div>
        )}
      </div>
    </Dialog>
  );
}

// ============================================================================
// 6. Campus Stock Item Drawer (Location Balances & Demand)
// ============================================================================

export function StockItemDrawer({
  item,
  onClose,
  onInspect,
  onRepair,
}: {
  item: string;
  onClose: () => void;
  onInspect: (resourceCode: string, location: string, maxQty: number) => void;
  onRepair: (resourceCode: string, location: string, maxQty: number) => void;
}) {
  const { base, can } = useFest();
  const query = useQuery({
    queryKey: ["equipment-stock-item", base, item],
    queryFn: () => api<StockItemDetail>(`${base}/equipment/stock/${encodeURIComponent(item)}`),
  });

  const d = query.data;

  return (
    <Dialog title={`Stock Item: ${item}`} onClose={onClose} side className="drawer-wide">
      <QueryState query={query}>
        {d && (
          <div className="stack">
            <div className="stat-row">
              <div>
                <span>Total Units</span>
                <strong>{d.total}</strong>
              </div>
              <div>
                <span style={{ color: "#34d399" }}>Deployable Good</span>
                <strong style={{ color: "#34d399" }}>{d.good}</strong>
              </div>
              <div>
                <span style={{ color: "#fbbf24" }}>Needs Check</span>
                <strong style={{ color: "#fbbf24" }}>{d.needs_check}</strong>
              </div>
              <div>
                <span style={{ color: "#f87171" }}>Under Repair</span>
                <strong style={{ color: "#f87171" }}>{d.under_repair}</strong>
              </div>
            </div>

            <ConditionSegmentedBar
              good={d.good}
              needsCheck={d.needs_check}
              underRepair={d.under_repair}
              total={d.total}
            />

            {/* Distribution across 5 locations */}
            <section className="panel stack">
              <SectionHead title="Distribution across Campus Locations" />
              <ul className="stack">
                {d.locations.map((loc) => (
                  <li key={loc.resource_code} style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                    <div>
                      <strong>{loc.location}</strong>
                      <span className="muted small" style={{ marginLeft: 6 }}>({loc.resource_code})</span>
                      <br />
                      <span className="muted small">
                        Total: {loc.quantity} · Reserved: {loc.reserved_quantity} · Free: {loc.available} · Condition: {loc.condition}
                      </span>
                    </div>

                    <div className="row-actions">
                      <Pill
                        value={loc.condition}
                        tone={loc.condition === "good" ? "ok" : loc.condition === "needs check" ? "warn" : "bad"}
                      />

                      {can("equipment.approve") && loc.condition === "needs check" && loc.quantity > 0 && (
                        <button
                          type="button"
                          className="button-secondary small"
                          onClick={() => onInspect(loc.resource_code, loc.location, loc.quantity)}
                        >
                          Inspect ({loc.quantity})
                        </button>
                      )}

                      {can("equipment.approve") && loc.condition === "under repair" && loc.quantity > 0 && (
                        <button
                          type="button"
                          className="button-secondary small"
                          onClick={() => onRepair(loc.resource_code, loc.location, loc.quantity)}
                        >
                          Repair ({loc.quantity})
                        </button>
                      )}
                    </div>
                  </li>
                ))}
              </ul>
            </section>

            {/* Demanding Events */}
            <section className="panel stack">
              <SectionHead title={`Demanding Events (${d.demanding_events.length})`}>
                <span className="muted small">From events.csv needs declarations</span>
              </SectionHead>
              {d.demanding_events.length > 0 ? (
                <ul className="stack">
                  {d.demanding_events.map((ev) => (
                    <li key={ev.code}>
                      <div>
                        <strong>{ev.code} — {ev.name}</strong>
                        <br />
                        <span className="muted small">
                          Venue: {ev.venue ?? "Unassigned"} · Starts: {ev.start_at ? formatTime(ev.start_at) : "TBD"}
                        </span>
                        <br />
                        <span className="muted small">Declared need: <code>{ev.declared_need}</code></span>
                      </div>
                      <Pill
                        value={ev.request_status ?? "Not requested"}
                        tone={ev.request_status === "approved" ? "ok" : "warn"}
                      />
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="muted small">No scheduled events currently demand this item in events.csv.</p>
              )}
            </section>

            {/* Movement Ledger */}
            <section className="panel stack">
              <SectionHead title="Recent Movement History" />
              {d.movement_history.length > 0 ? (
                <ul className="stack">
                  {d.movement_history.slice(0, 10).map((m) => (
                    <li key={m.id} style={{ display: "flex", justifyContent: "space-between" }}>
                      <div>
                        <strong>{m.action}</strong>: {m.quantity} units ({m.from_location ?? "—"} → {m.to_location ?? "—"})
                        <br />
                        <small className="muted">{m.notes ?? "No notes"} · by {m.user_name ?? "System"}</small>
                      </div>
                      <small className="muted">{formatTime(m.timestamp)}</small>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="muted small">No movements recorded yet.</p>
              )}
            </section>
          </div>
        )}
      </QueryState>
    </Dialog>
  );
}

// ============================================================================
// 7. Modals: Inspect, Repair, Report Failure, External Receipt
// ============================================================================

export function InspectResourceModal({
  resourceCode,
  item,
  location,
  maxQuantity,
  onClose,
}: {
  resourceCode: string;
  item: string;
  location: string | null;
  maxQuantity: number;
  onClose: () => void;
}) {
  const { base } = useFest();
  const qc = useQueryClient();
  const [passedQty, setPassedQty] = useState(String(maxQuantity));
  const [repairQty, setRepairQty] = useState("0");
  const [notes, setNotes] = useState("");

  const inspectAction = useAction(
    () =>
      api(`${base}/equipment/resources/${resourceCode}/inspect`, {
        method: "POST",
        body: JSON.stringify({
          passed_good_quantity: Number(passedQty),
          under_repair_quantity: Number(repairQty),
          inspector_notes: notes.trim() || null,
        }),
      }),
    {
      success: `Inspected ${resourceCode}: ${passedQty} marked Good, ${repairQty} marked Under Repair`,
      onDone: () => {
        qc.invalidateQueries({ queryKey: ["equipment-summary"] });
        qc.invalidateQueries({ queryKey: ["equipment-stock"] });
        qc.invalidateQueries({ queryKey: ["equipment-stock-item"] });
        onClose();
      },
    }
  );

  return (
    <Dialog title={`Inspect Equipment · ${item}`} onClose={onClose}>
      <form
        className="form stack"
        onSubmit={(e) => {
          e.preventDefault();
          inspectAction.mutate();
        }}
      >
        <Facts
          items={[
            ["Resource ID", resourceCode],
            ["Location", location ?? "Unknown"],
            ["Total to inspect", `${maxQuantity} units in Needs Check`],
          ]}
        />

        <div className="form-grid">
          <Field label="Passed (Move to Good)">
            <input
              type="number"
              min={0}
              max={maxQuantity}
              required
              value={passedQty}
              onChange={(e) => {
                const p = Number(e.target.value) || 0;
                setPassedQty(e.target.value);
                setRepairQty(String(Math.max(0, maxQuantity - p)));
              }}
            />
          </Field>

          <Field label="Failed (Move to Under Repair)">
            <input
              type="number"
              min={0}
              max={maxQuantity}
              required
              value={repairQty}
              onChange={(e) => setRepairQty(e.target.value)}
            />
          </Field>
        </div>

        <Field label="Inspector Notes">
          <input
            placeholder="Condition details, test procedure performed"
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
          />
        </Field>

        <ErrorNote error={inspectAction.error} />

        <div className="row-actions">
          <button type="submit" className="button-primary" disabled={inspectAction.isPending}>
            Confirm Inspection
          </button>
          <button type="button" className="button-ghost" onClick={onClose}>
            Cancel
          </button>
        </div>
      </form>
    </Dialog>
  );
}

export function RepairResourceModal({
  resourceCode,
  item,
  location,
  maxQuantity,
  onClose,
}: {
  resourceCode: string;
  item: string;
  location: string | null;
  maxQuantity: number;
  onClose: () => void;
}) {
  const { base } = useFest();
  const qc = useQueryClient();
  const [repairedQty, setRepairedQty] = useState(String(maxQuantity));
  const [notes, setNotes] = useState("");

  const repairAction = useAction(
    () =>
      api(`${base}/equipment/resources/${resourceCode}/repair`, {
        method: "POST",
        body: JSON.stringify({
          repaired_quantity: Number(repairedQty),
          technician_notes: notes.trim() || null,
        }),
      }),
    {
      success: `Repaired ${resourceCode}: ${repairedQty} returned to Good condition`,
      onDone: () => {
        qc.invalidateQueries({ queryKey: ["equipment-summary"] });
        qc.invalidateQueries({ queryKey: ["equipment-stock"] });
        qc.invalidateQueries({ queryKey: ["equipment-stock-item"] });
        onClose();
      },
    }
  );

  return (
    <Dialog title={`Record Equipment Repair · ${item}`} onClose={onClose}>
      <form
        className="form stack"
        onSubmit={(e) => {
          e.preventDefault();
          repairAction.mutate();
        }}
      >
        <Facts
          items={[
            ["Resource ID", resourceCode],
            ["Location", location ?? "Unknown"],
            ["Units under repair", `${maxQuantity} units`],
          ]}
        />

        <Field label="Repaired Quantity (Return to Good)">
          <input
            type="number"
            min={1}
            max={maxQuantity}
            required
            value={repairedQty}
            onChange={(e) => setRepairedQty(e.target.value)}
          />
        </Field>

        <Field label="Technician Notes">
          <input
            placeholder="Parts replaced, repairs performed"
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
          />
        </Field>

        <ErrorNote error={repairAction.error} />

        <div className="row-actions">
          <button type="submit" className="button-primary" disabled={repairAction.isPending}>
            Complete Repair
          </button>
          <button type="button" className="button-ghost" onClick={onClose}>
            Cancel
          </button>
        </div>
      </form>
    </Dialog>
  );
}

export function ReportFailureModal({
  resourceCode,
  item,
  location,
  onClose,
}: {
  resourceCode: string;
  item: string;
  location: string | null;
  onClose: () => void;
}) {
  const { base } = useFest();
  const qc = useQueryClient();
  const [failedQty, setFailedQty] = useState("1");
  const [reason, setReason] = useState("");
  const [eventCode, setEventCode] = useState("");
  const [triggerReplacement, setTriggerReplacement] = useState(false);

  const reportAction = useAction(
    () =>
      api(`${base}/equipment/resources/${resourceCode}/report-failure`, {
        method: "POST",
        body: JSON.stringify({
          failed_quantity: Number(failedQty),
          failure_reason: reason.trim(),
          requesting_event_code: eventCode.trim() || null,
          trigger_replacement: triggerReplacement,
        }),
      }),
    {
      success: `Failure reported for ${resourceCode}. Units moved to Under Repair.`,
      onDone: () => {
        qc.invalidateQueries({ queryKey: ["equipment-summary"] });
        qc.invalidateQueries({ queryKey: ["equipment-stock"] });
        qc.invalidateQueries({ queryKey: ["equipment-stock-item"] });
        qc.invalidateQueries({ queryKey: ["equipment-requests"] });
        onClose();
      },
    }
  );

  return (
    <Dialog title={`Report Equipment Breakdown · ${item}`} onClose={onClose}>
      <form
        className="form stack"
        onSubmit={(e) => {
          e.preventDefault();
          reportAction.mutate();
        }}
      >
        <Facts
          items={[
            ["Item", item],
            ["Resource ID", resourceCode],
            ["Current Location", location ?? "Unknown"],
          ]}
        />

        <Field label="Failed Quantity">
          <input
            type="number"
            min={1}
            required
            value={failedQty}
            onChange={(e) => setFailedQty(e.target.value)}
          />
        </Field>

        <Field label="Failure / Breakdown Reason">
          <input
            required
            placeholder="e.g. Blown fuse, physical fracture, liquid spill"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
          />
        </Field>

        <Field label="Affected Event (Optional)">
          <input
            placeholder="e.g. EV01"
            value={eventCode}
            onChange={(e) => setEventCode(e.target.value)}
          />
        </Field>

        <label className="check-inline">
          <input
            type="checkbox"
            checked={triggerReplacement}
            onChange={(e) => setTriggerReplacement(e.target.checked)}
          />
          <span>Create expedited emergency replacement request</span>
        </label>

        <ErrorNote error={reportAction.error} />

        <div className="row-actions">
          <button type="submit" className="button-primary danger" disabled={reportAction.isPending || !reason.trim()}>
            Record Breakdown
          </button>
          <button type="button" className="button-ghost" onClick={onClose}>
            Cancel
          </button>
        </div>
      </form>
    </Dialog>
  );
}

export function ExternalReceiptModal({
  onClose,
}: {
  onClose: () => void;
}) {
  const { base } = useFest();
  const qc = useQueryClient();
  const [item, setItem] = useState("");
  const [quantity, setQuantity] = useState("1");
  const [location, setLocation] = useState("Store Room A");
  const [receiptType, setReceiptType] = useState<"purchase" | "rental">("rental");
  const [notes, setNotes] = useState("");

  const optionsQuery = useQuery({
    queryKey: ["equipment-options", base],
    queryFn: () => api<EquipmentOptions>(`${base}/equipment/options`),
  });

  const receiptAction = useAction(
    () =>
      api(`${base}/equipment/resources/external-receipt`, {
        method: "POST",
        body: JSON.stringify({
          item,
          quantity: Number(quantity),
          location,
          receipt_type: receiptType,
          notes: notes.trim() || null,
        }),
      }),
    {
      success: `Received ${quantity} × ${item} into ${location} (Good condition)`,
      onDone: () => {
        qc.invalidateQueries({ queryKey: ["equipment-summary"] });
        qc.invalidateQueries({ queryKey: ["equipment-stock"] });
        onClose();
      },
    }
  );

  return (
    <Dialog title="External Equipment Receipt" onClose={onClose}>
      <form
        className="form stack"
        onSubmit={(e) => {
          e.preventDefault();
          receiptAction.mutate();
        }}
      >
        <p className="muted small">
          Add newly arrived purchase deliveries or external vendor rentals directly into campus logistics inventory with <strong>Good</strong> condition.
        </p>

        <div className="form-grid">
          <Field label="Equipment Item">
            <select required value={item} onChange={(e) => setItem(e.target.value)}>
              <option value="">Select item...</option>
              {optionsQuery.data?.items.map((it) => (
                <option key={it} value={it}>
                  {it}
                </option>
              ))}
            </select>
          </Field>

          <Field label="Quantity">
            <input
              type="number"
              min={1}
              required
              value={quantity}
              onChange={(e) => setQuantity(e.target.value)}
            />
          </Field>

          <Field label="Receiving Location">
            <select value={location} onChange={(e) => setLocation(e.target.value)}>
              {["Registration Desk", "Store Room A", "Main Stage", "Sports Complex", "Van 1"].map((loc) => (
                <option key={loc} value={loc}>
                  {loc} {loc === "Van 1" ? "(Unverified mobile)" : ""}
                </option>
              ))}
            </select>
          </Field>

          <Field label="Receipt Type">
            <select value={receiptType} onChange={(e) => setReceiptType(e.target.value as any)}>
              <option value="rental">Rental (External Supplier)</option>
              <option value="purchase">New Purchase Delivery</option>
            </select>
          </Field>
        </div>

        <Field label="Receipt Notes / Supplier Reference">
          <input
            placeholder="e.g. City Sound Rentals invoice #482"
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
          />
        </Field>

        <ErrorNote error={receiptAction.error} />

        <div className="row-actions">
          <button type="submit" className="button-primary" disabled={receiptAction.isPending || !item}>
            Confirm Receipt
          </button>
          <button type="button" className="button-ghost" onClick={onClose}>
            Cancel
          </button>
        </div>
      </form>
    </Dialog>
  );
}

// ============================================================================
// 8. Event Equipment Section (for Event Drawer in Events.tsx)
// ============================================================================

export function EventEquipmentSection({ eventCode }: { eventCode: string }) {
  const { base, can } = useFest();
  const [requestDrawerOpen, setRequestDrawerOpen] = useState(false);

  const eqQuery = useQuery({
    queryKey: ["event-equipment", base, eventCode],
    queryFn: () => api<EventEquipmentResponse>(`${base}/equipment/events/${eventCode}`),
  });

  return (
    <QueryState query={eqQuery}>
      {eqQuery.data && (() => {
        const d = eqQuery.data;
        const badge = readinessBadge(d.readiness);

        return (
          <div className="stack" style={{ marginTop: 12 }}>
            <div className="bar" style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <h4>Equipment & Readiness</h4>
              <Pill value={badge.label} tone={badge.tone} />
            </div>

            <div className="callout small">
              <strong>events.csv needs:</strong> {d.equipment_needs_text ?? <em>No special equipment declared</em>}
            </div>

            {d.requests.length > 0 && (
              <div>
                <h5>Linked Requests ({d.requests.length})</h5>
                <ul className="stack">
                  {d.requests.map((r) => (
                    <li key={r.code} style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                      <div>
                        <strong>{r.quantity} × {r.item}</strong> ({r.code})
                        <br />
                        <span className="muted small">
                          Status: {r.status} · Verified: {r.verified_received} / {r.quantity}
                        </span>
                      </div>
                      <Pill value={r.status} tone={r.status === "approved" ? "ok" : "warn"} />
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {can("equipment.request") && (
              <div style={{ marginTop: 6 }}>
                <button
                  type="button"
                  className="button-secondary small"
                  onClick={() => setRequestDrawerOpen(true)}
                >
                  <Plus size={14} /> Request / Modify Equipment
                </button>
              </div>
            )}

            {requestDrawerOpen && (
              <NewEquipmentRequestDrawer
                defaultEventCode={eventCode}
                onClose={() => setRequestDrawerOpen(false)}
              />
            )}
          </div>
        );
      })()}
    </QueryState>
  );
}

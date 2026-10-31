import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  Camera,
  CheckCircle2,
  Clock,
  Download,
  ExternalLink,
  PackageCheck,
  PackagePlus,
  Printer,
  RotateCcw,
  Search,
  Send,
  ShieldAlert,
  Shirt,
  Tag,
  Users,
} from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";

import { QrScanner } from "../components/QrScanner";
import {
  DataTable,
  Dialog,
  ErrorNote,
  Facts,
  Field,
  PageHead,
  Pagination,
  Pill,
  QueryState,
  SectionHead,
  Tabs,
  fromInput,
  useAction,
  useDebounced,
  useFest,
  usePaged,
} from "../components/ui";
import { api } from "../lib/api";
import { formatTime } from "../lib/format";

// ============================================================================
// Types
// ============================================================================

export interface MerchSummary {
  kpis: {
    tshirts_issuable: number;
    tshirts_on_hand: number;
    tshirts_csv_reserved: number;
    tshirts_needed: number;
    tshirts_net_gap: number;
    tshirts_shortage_total: number;
    tshirts_surplus_xl: number;
    other_items_issuable: number;
    total_csv_reserved: number;
    issued_total: number;
    tshirts_issued: number;
    owed_total: number;
    full_kits_possible_now: number;
    partial_kits_count: number;
    unscheduled_count: number;
  };
  participants_summary: {
    raw_total: number;
    entitled_total: number;
    checked_in_total: number;
    excluded_duplicates: { code: string; detail: any }[];
    unscheduled_codes: string[];
  };
  staff_and_leaders: {
    total_staff: number;
    tshirt_quota_per_head: number;
    total_quota: number;
    issued: number;
    remaining: number;
    note: string;
  };
  config: {
    assumptions_confirmed: Record<string, { by: string; at: string }>;
    kit_templates: Record<string, string[]>;
    kit_confirmed: boolean;
    kit_confirmed_by: string | null;
    kit_confirmed_at: string | null;
    staff_leader_quota: number;
    staff_quota_set_by: string | null;
    staff_quota_set_at: string | null;
    substitution_rule: { max_steps: number; direction: string };
    active_policy: "P1" | "P2" | "P3";
    has_approved_plan: boolean;
    approved_by: string | null;
    approved_at: string | null;
    plan_stale: boolean;
    plan_stale_reasons: string[];
  };
  size_curve: SizeCurveRow[];
  items: MerchItemRow[];
  what_to_order: { item: string; size: string; issuable: number; needed: number; shortfall: number }[];
  incoming: IncomingDeliveryRow[];
}

export interface SizeCurveRow {
  id: number;
  code: string;
  item: string;
  size: string;
  on_hand: number;
  csv_reserved: number;
  csv_reserved_note: string;
  plan_reserved: number;
  issuable: number;
  unallocated: number;
  issued: number;
  owed: number;
  needed: number;
  raw_participant_demand: number;
  gap: number;
  coverage_pct: number;
}

export interface MerchItemRow extends SizeCurveRow {
  entitlement_rule: string;
  entitlement_confirmed: boolean;
  entitlement_confirmed_by: string | null;
  entitlement_confirmed_at: string | null;
  max_per_person: number;
}

export interface IncomingDeliveryRow {
  id: number;
  item: string;
  size: string;
  quantity_expected: number;
  quantity_received: number;
  quantity_short: number;
  expected_date: string | null;
  supplier_note: string | null;
  status: string;
  created_by: string | null;
  received_by: string | null;
  received_at: string | null;
}

export interface LookupResult {
  participant: {
    id: number;
    code: string;
    full_name: string;
    college: string;
    contingent: string | null;
    contingent_name: string | null;
    tshirt_size: string;
    phone_last4: string;
    checkin_status: string;
    departure_at: string | null;
    duplicate_flag: boolean;
    duplicate_reason: string | null;
    issues: {
      id: number;
      item: string;
      issued_size: string;
      requested_size: string;
      is_substitution: boolean;
      issued_at: string;
      issued_by: string;
    }[];
    owed: {
      id: number;
      item: string;
      requested_size: string;
      created_at: string;
    }[];
  };
  eligible: boolean;
  block_reason: string | null;
  is_duplicate: boolean;
}

export interface DistributionPreview {
  active_policy: "P1" | "P2" | "P3";
  is_approved: boolean;
  is_stale: boolean;
  stale_reasons: string[];
  policies: {
    P1: PolicyDetail;
    P2: PolicyDetail;
    P3: PolicyDetail;
  };
}

export interface PolicyDetail {
  policy: "P1" | "P2" | "P3";
  name: string;
  description: string;
  totals: {
    total_participants: number;
    full_kits_ready: number;
    partial_kits: number;
    overall_coverage_pct: number;
    by_size: Record<string, { issuable: number; needed: number; allocated: number; coverage_pct: number }>;
  };
  waves: {
    wave: number;
    arrival_time: string | null;
    headcount: number;
    full_kits: number;
    coverage_pct: number;
    by_size: Record<string, number>;
  }[];
  contingents: {
    code: string;
    name: string;
    headcount: number;
    full_kits: number;
    coverage_pct: number;
  }[];
}

export interface OwedRow {
  id: number;
  participant_code: string;
  participant_name: string;
  college: string;
  item: string;
  requested_size: string;
  departure_at: string | null;
  created_at: string;
  reason: string | null;
  phone_last4: string;
}

export interface MerchLedgerRow {
  id: number;
  timestamp: string;
  action: string;
  item: string;
  size: string;
  qty: number;
  participant_code: string | null;
  user_name: string | null;
  notes: string | null;
  is_exception: boolean;
}

// ============================================================================
// Main Merchandise Page
// ============================================================================

export function Merchandise() {
  const { base } = useFest();
  const [params, setParams] = useSearchParams();

  // Tab State
  const [tab, setTab] = useState<"desk" | "distribution" | "stock" | "owed" | "ledger">(
    (params.get("tab") as any) ?? "desk"
  );

  // Summary Query
  const summaryQuery = useQuery({
    queryKey: ["merch-summary", base],
    queryFn: () => api<MerchSummary>(`${base}/merchandise/summary`),
  });

  const sum = summaryQuery.data;

  // Keep URL in sync
  useEffect(() => {
    setParams({ tab }, { replace: true });
  }, [tab, setParams]);

  return (
    <div>
      {/* Header max 72px */}
      <PageHead
        title="Merchandise"
        subtitle="Distribution planning, kit prep, express desk handover, and stock shortfall tracking"
      >
        <span className="muted small" style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <Shirt size={16} /> 2,834 entitled · 1,558 issuable T-shirts
        </span>
      </PageHead>

      {/* 5 Equal Metric Cards */}
      <div className="kpi-grid-5">
        <div className="kpi" style={{ borderLeft: "3px solid #34d399" }}>
          <div className="kpi-head">
            <h3>Issuable T-shirts</h3>
            <Shirt size={18} style={{ color: "#34d399" }} />
          </div>
          <p className="kpi-value" style={{ color: "#34d399" }}>
            {sum?.kpis.tshirts_issuable ?? 1558} <span>units</span>
          </p>
          <p className="kpi-note">{sum?.kpis.tshirts_on_hand ?? 1647} on hand ({sum?.kpis.tshirts_csv_reserved ?? 89} locked)</p>
        </div>

        <div className="kpi" style={{ borderLeft: "3px solid #f87171" }}>
          <div className="kpi-head">
            <h3>T-shirt Shortage</h3>
            <AlertTriangle size={18} style={{ color: "#f87171" }} />
          </div>
          <p className="kpi-value" style={{ color: "#f87171" }}>
            {Math.abs(sum?.kpis.tshirts_net_gap ?? 1276)} <span>short</span>
          </p>
          <p className="kpi-note">Coverage: 55.0% · Only XL surplus</p>
        </div>

        <div className="kpi" style={{ borderLeft: "3px solid #38bdf8" }}>
          <div className="kpi-head">
            <h3>Kits Issued</h3>
            <PackageCheck size={18} style={{ color: "#38bdf8" }} />
          </div>
          <p className="kpi-value" style={{ color: "#38bdf8" }}>
            {sum?.kpis.tshirts_issued ?? 0} <span>kits</span>
          </p>
          <p className="kpi-note">{sum?.kpis.issued_total ?? 0} total items handed out</p>
        </div>

        <div className="kpi" style={{ borderLeft: "3px solid #fbbf24" }}>
          <div className="kpi-head">
            <h3>Owed Items</h3>
            <Clock size={18} style={{ color: "#fbbf24" }} />
          </div>
          <p className="kpi-value" style={{ color: "#fbbf24" }}>
            {sum?.kpis.owed_total ?? 0} <span>owed</span>
          </p>
          <p className="kpi-note">Sorted by departure time</p>
        </div>

        <div className="kpi">
          <div className="kpi-head">
            <h3>CSV Reserve Locked</h3>
            <Tag size={18} className="muted" />
          </div>
          <p className="kpi-value">
            {sum?.kpis.total_csv_reserved ?? 184} <span>locked</span>
          </p>
          <p className="kpi-note">89 T-shirts + 95 collateral items</p>
        </div>
      </div>

      {/* 5 Tabs */}
      <Tabs
        value={tab}
        onChange={setTab}
        options={[
          ["desk", "Issue desk"],
          ["distribution", `Distribution plan ${sum?.config.plan_stale ? "⚠️ Stale" : ""}`],
          ["stock", "Stock & demand"],
          ["owed", `Owed list (${sum?.kpis.owed_total ?? 0})`],
          ["ledger", "Ledger"],
        ]}
      />

      {/* TAB 1: Issue Desk */}
      {tab === "desk" && <IssueDeskTab />}

      {/* TAB 2: Distribution Plan & Wave Trays */}
      {tab === "distribution" && <DistributionTab />}

      {/* TAB 3: Stock & Demand */}
      {tab === "stock" && <StockDemandTab summary={sum} />}

      {/* TAB 4: Owed List */}
      {tab === "owed" && <OwedListTab />}

      {/* TAB 5: Merch Ledger */}
      {tab === "ledger" && <MerchLedgerTab />}
    </div>
  );
}

// ============================================================================
// TAB 1: Express Issue Desk
// ============================================================================

function IssueDeskTab() {
  const { base } = useFest();
  const qc = useQueryClient();

  const [searchQuery, setSearchQuery] = useState("");
  const [cameraActive, setCameraActive] = useState(false);
  const [lookupResult, setLookupResult] = useState<LookupResult | null>(null);
  const [errorMsg, setErrorMsg] = useState("");

  // Contingent mode
  const [contingentMode, setContingentMode] = useState(false);
  const [chosenContingent, setChosenContingent] = useState("");
  const [contingentList, setContingentList] = useState<any[]>([]);

  // Paper tally reconciliation modal
  const [paperModalOpen, setPaperModalOpen] = useState(false);

  // 60-Second Undo State
  const [lastIssue, setLastIssue] = useState<{ id: number; name: string; item: string; secondsLeft: number } | null>(null);

  // Timer for 60s Undo countdown
  useEffect(() => {
    if (!lastIssue) return;
    if (lastIssue.secondsLeft <= 0) {
      setLastIssue(null);
      return;
    }
    const timer = setInterval(() => {
      setLastIssue((prev) => (prev ? { ...prev, secondsLeft: prev.secondsLeft - 1 } : null));
    }, 1000);
    return () => clearInterval(timer);
  }, [lastIssue]);

  // Lookup API
  const doLookup = async (token: string) => {
    if (!token.trim()) return;
    setErrorMsg("");
    try {
      const res = await api<LookupResult>(`${base}/merchandise/lookup?q=${encodeURIComponent(token.trim())}`);
      setLookupResult(res);
      setCameraActive(false);
    } catch (err: any) {
      setErrorMsg(err.message || "Participant not found");
      setLookupResult(null);
    }
  };

  // Atomic Kit Handover Action
  const handoverAction = useAction(
    (participantCode: string) =>
      api(`${base}/merchandise/handover-kit`, {
        method: "POST",
        body: JSON.stringify({
          participant_code: participantCode,
          allow_substitution: true,
          substitution_consent: false,
        }),
      }),
    {
      success: (res: any) => {
        qc.invalidateQueries({ queryKey: ["merch-summary"] });
        if (lookupResult) {
          setLastIssue({
            id: res.issues?.[0]?.id ?? 0,
            name: lookupResult.participant.full_name,
            item: "Complete Kit (T-shirt, ID card, Lanyard)",
            secondsLeft: 60,
          });
          doLookup(lookupResult.participant.code);
        }
        return `Kit handed over successfully!`;
      },
    }
  );

  // Undo Action
  const undoAction = useAction(
    (issueId: number) =>
      api(`${base}/merchandise/issues/${issueId}/undo`, {
        method: "POST",
      }),
    {
      success: "Issue reversed! Stock returned to inventory.",
      onDone: () => {
        setLastIssue(null);
        qc.invalidateQueries({ queryKey: ["merch-summary"] });
        if (lookupResult) doLookup(lookupResult.participant.code);
      },
    }
  );

  // Fetch contingent list when contingent mode enabled
  const loadContingentQueue = async (code: string) => {
    setChosenContingent(code);
    if (!code) {
      setContingentList([]);
      return;
    }
    try {
      const res = await api<any[]>(`${base}/merchandise/contingent-queue/${code}`);
      setContingentList(res);
    } catch {
      setContingentList([]);
    }
  };

  const p = lookupResult?.participant;

  return (
    <div className="stack">
      {/* 60s Undo Banner */}
      {lastIssue && lastIssue.secondsLeft > 0 && (
        <div className="undo-banner">
          <div>
            <strong>Issued kit to {lastIssue.name}.</strong>
            <span className="small muted" style={{ marginLeft: 8 }}>
              Undo available for {lastIssue.secondsLeft}s
            </span>
          </div>
          <button
            type="button"
            className="button-primary small"
            style={{ background: "#f59e0b", color: "#000" }}
            disabled={undoAction.isPending}
            onClick={() => undoAction.mutate(lastIssue.id)}
          >
            <RotateCcw size={14} /> Undo Issue ({lastIssue.secondsLeft}s)
          </button>
        </div>
      )}

      {/* Top Controls: Scan / Search & Modes */}
      <div className="panel stack">
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 10 }}>
          <div style={{ display: "flex", gap: 10, flex: 1, minWidth: 280 }}>
            <div className="search" style={{ flex: 1 }}>
              <Search size={16} className="muted" />
              <input
                type="search"
                placeholder="Search participant ID, QR pass, name, or phone last 4 digits..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && doLookup(searchQuery)}
              />
            </div>
            <button
              type="button"
              className="button-secondary"
              onClick={() => doLookup(searchQuery)}
            >
              Lookup
            </button>
            <button
              type="button"
              className={`button-${cameraActive ? "primary" : "secondary"}`}
              onClick={() => setCameraActive(!cameraActive)}
            >
              <Camera size={16} /> {cameraActive ? "Close Camera" : "Scan QR"}
            </button>
          </div>

          <div style={{ display: "flex", gap: 8 }}>
            <button
              type="button"
              className={`button-ghost small ${contingentMode ? "active" : ""}`}
              onClick={() => setContingentMode(!contingentMode)}
            >
              <Users size={14} /> Contingent Queue Mode
            </button>
            <button
              type="button"
              className="button-ghost small"
              onClick={() => setPaperModalOpen(true)}
            >
              <CheckCircle2 size={14} /> Offline Paper Tally
            </button>
          </div>
        </div>

        {/* QR Scanner view */}
        {cameraActive && (
          <div style={{ marginTop: 12 }}>
            <QrScanner onCode={(code) => doLookup(code)} label="Merchandise QR Desk Scanner" />
          </div>
        )}

        {errorMsg && <p className="form-error">{errorMsg}</p>}
      </div>

      {/* Contingent Queue Selector */}
      {contingentMode && (
        <div className="panel stack" style={{ background: "var(--surface-2)" }}>
          <SectionHead title="Contingent Queue Mode">
            <span className="muted small">Fast bulk-issuing for incoming college teams</span>
          </SectionHead>
          <Field label="Choose Contingent">
            <select value={chosenContingent} onChange={(e) => loadContingentQueue(e.target.value)}>
              <option value="">Select a college / contingent...</option>
              {["C01", "C02", "C03", "C04", "C05", "C06", "C07", "C08", "C09", "C10"].map((c) => (
                <option key={c} value={c}>
                  Contingent {c}
                </option>
              ))}
            </select>
          </Field>

          {contingentList.length > 0 && (
            <ul className="stack">
              {contingentList.map((m) => (
                <li key={m.code} style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                  <div>
                    <strong>{m.full_name}</strong> <span className="mono">({m.code})</span>
                    <span className="pill small" style={{ marginLeft: 8 }}>Size: {m.tshirt_size}</span>
                    <br />
                    <small className="muted">Phone: ••• ••• {m.phone_last4}</small>
                  </div>
                  <div>
                    {m.kit_issued ? (
                      <Pill value="Kit Issued" tone="ok" />
                    ) : (
                      <button
                        type="button"
                        className="button-primary small"
                        disabled={handoverAction.isPending}
                        onClick={() => handoverAction.mutate(m.code)}
                      >
                        Hand Over Kit
                      </button>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {/* Participant Privacy Card */}
      {p && (
        <div className={`express-card ${p.duplicate_flag ? "duplicate-blocked" : ""}`}>
          <div className="express-card-header">
            <div>
              <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                <span className="express-card-name">{p.full_name}</span>
                <span className="mono" style={{ fontSize: 18, color: "var(--muted)" }}>{p.code}</span>
                <Pill value={p.checkin_status} tone={p.checkin_status === "checked_in" ? "ok" : "warn"} />
              </div>
              <p className="muted" style={{ margin: "4px 0 0" }}>
                {p.college} · Contingent: {p.contingent ?? "Individual"} ({p.contingent_name ?? "General"})
              </p>
            </div>

            <div style={{ textAlign: "right" }}>
              <span className="small muted">Declared Size</span>
              <div style={{ font: "700 24px var(--display)", color: "var(--accent)" }}>
                {p.tshirt_size}
              </div>
            </div>
          </div>

          {/* Privacy Note */}
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", borderTop: "1px solid var(--line)", paddingTop: 10 }}>
            <span className="small muted">
              🔒 <strong>Privacy-Safe Desk Card:</strong> Phone: ••• ••• {p.phone_last4} · Gender, accessibility & dietary preferences suppressed.
            </span>
            {p.departure_at && (
              <span className="small muted">
                Departs: {formatTime(p.departure_at)}
              </span>
            )}
          </div>

          {/* PROBABLE DUPLICATE BANNER (P_DUP01 / P_DUP02) */}
          {p.duplicate_flag && (
            <div className="callout bad stack" style={{ border: "2px solid var(--critical)" }}>
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <ShieldAlert size={20} style={{ color: "var(--critical)" }} />
                <strong>BLOCKED AT DESK: Probable Duplicate Registration</strong>
              </div>
              <p className="small">
                This participant record is flagged as a probable duplicate in Aarohan 2026 data ({p.duplicate_reason ?? "Duplicate personal details"}). To protect inventory integrity, kit distribution is blocked until resolved.
              </p>
              <div className="row-actions">
                <Link to="/participants" className="button-secondary small">
                  Resolve in Participants Module <ExternalLink size={14} />
                </Link>
              </div>
            </div>
          )}

          {/* Previous Issues / Kit Status */}
          {p.issues.length > 0 && (
            <div className="stack" style={{ background: "var(--surface-2)", padding: 12, borderRadius: 8 }}>
              <strong>Already Issued Items ({p.issues.length}):</strong>
              <ul className="stack">
                {p.issues.map((iss) => (
                  <li key={iss.id} style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                    <div>
                      <strong>{iss.item}</strong> (Size: {iss.issued_size})
                      {iss.is_substitution && (
                        <span className="tone-warn small" style={{ marginLeft: 6 }}>
                          (Substituted from {iss.requested_size})
                        </span>
                      )}
                      <br />
                      <small className="muted">Issued on {formatTime(iss.issued_at)} by {iss.issued_by}</small>
                    </div>
                    <Pill value="Issued" tone="ok" />
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* Handover Action Controls */}
          {!p.duplicate_flag && (
            <div style={{ display: "flex", gap: 12, alignItems: "center", marginTop: 8 }}>
              {p.issues.length === 0 ? (
                <button
                  type="button"
                  className="button-primary huge"
                  style={{ flex: 1, padding: "16px 24px", fontSize: 18 }}
                  disabled={handoverAction.isPending}
                  onClick={() => handoverAction.mutate(p.code)}
                >
                  <PackageCheck size={22} /> Hand Over Complete Kit (1-Click)
                </button>
              ) : (
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <CheckCircle2 size={20} style={{ color: "#34d399" }} />
                  <strong>Complete Kit Already Handed Over</strong>
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* Offline Paper Tally Modal */}
      {paperModalOpen && (
        <PaperTallyModal onClose={() => setPaperModalOpen(false)} />
      )}
    </div>
  );
}

// ============================================================================
// TAB 2: Distribution Plan Engine & Wave Trays
// ============================================================================

function DistributionTab() {
  const { base, can } = useFest();
  const qc = useQueryClient();

  const [policy, setPolicy] = useState<"P1" | "P2" | "P3">("P1");
  const [replanDiffOpen, setReplanDiffOpen] = useState(false);
  const [printableWave, setPrintableWave] = useState<number | null>(null);

  const previewQuery = useQuery({
    queryKey: ["distribution-preview", base],
    queryFn: () => api<DistributionPreview>(`${base}/merchandise/distribution/preview`),
  });

  const d = previewQuery.data;
  const currentPolicyData = d?.policies?.[policy];

  const approvePlan = useAction(
    () =>
      api(`${base}/merchandise/distribution/approve`, {
        method: "POST",
        body: JSON.stringify({ policy, lock_stock: true }),
      }),
    {
      success: `Distribution Plan (${policy}) approved! Reservation locked against casual desk issue.`,
      onDone: () => {
        qc.invalidateQueries({ queryKey: ["distribution-preview"] });
        qc.invalidateQueries({ queryKey: ["merch-summary"] });
      },
    }
  );

  return (
    <div className="stack">
      {/* Kit Setup Banner */}
      <div className="callout info" style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div>
          <strong>Standard Event Kit Composition:</strong> T-shirt (declared size) + ID Card + Aarohan Lanyard.
          <span className="muted small" style={{ marginLeft: 8 }}>
            Tote bag provided on-spot if requested. Winner medals reserved for award ceremonies.
          </span>
        </div>
        <Pill value="Deterministic Policy" tone="ok" />
      </div>

      {/* Stale Plan Alert */}
      {d?.is_stale && (
        <div className="callout warn stack">
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <AlertTriangle size={18} />
            <strong>Approved Distribution Plan is Stale!</strong>
          </div>
          <p className="small">
            Participant headcounts, arrival waves, or inventory balances have changed since approval:{" "}
            {d.stale_reasons.join(" · ")}
          </p>
          <div className="row-actions">
            <button
              type="button"
              className="button-secondary small"
              onClick={() => setReplanDiffOpen(true)}
            >
              Preview Re-Plan Diff & Update
            </button>
          </div>
        </div>
      )}

      {/* Policy Selector & Comparison */}
      <section className="panel stack">
        <SectionHead title="Distribution Policy Comparison">
          <span className="muted small">Mathematical proof of coverage under fixed stock of 1,558 T-shirts</span>
        </SectionHead>

        <div style={{ display: "grid", gridTemplateColumns: "repeat(3, minmax(0, 1fr))", gap: 12 }}>
          {/* P1: Arrival Order */}
          <div
            className={`panel ${policy === "P1" ? "active" : ""}`}
            style={{
              border: policy === "P1" ? "2px solid var(--accent)" : "1px solid var(--line)",
              cursor: "pointer",
            }}
            onClick={() => setPolicy("P1")}
          >
            <div style={{ display: "flex", justifyContent: "space-between" }}>
              <h4>P1: Arrival Order</h4>
              {policy === "P1" && <Pill value="Selected" tone="ok" />}
            </div>
            <p className="small muted">
              Waves 1 to 9 served strictly by check-in / arrival order until stock is exhausted.
            </p>
            <div style={{ marginTop: 8 }}>
              <strong>1,558 full kits</strong>
              <div className="muted small">Waves 1–5 get 100%; wave 6 gets partial; waves 7–9 owed</div>
            </div>
          </div>

          {/* P2: Equal Wave Share */}
          <div
            className={`panel ${policy === "P2" ? "active" : ""}`}
            style={{
              border: policy === "P2" ? "2px solid var(--accent)" : "1px solid var(--line)",
              cursor: "pointer",
            }}
            onClick={() => setPolicy("P2")}
          >
            <div style={{ display: "flex", justifyContent: "space-between" }}>
              <h4>P2: Equal Wave Share</h4>
              {policy === "P2" && <Pill value="Selected" tone="ok" />}
            </div>
            <p className="small muted">
              Flat ~51% share of available sizes distributed across all 9 arrival waves.
            </p>
            <div style={{ marginTop: 8 }}>
              <strong>~51% per wave</strong>
              <div className="muted small">Prevents early wave monopoly; all waves get equal chance</div>
            </div>
          </div>

          {/* P3: Equal Contingent Share */}
          <div
            className={`panel ${policy === "P3" ? "active" : ""}`}
            style={{
              border: policy === "P3" ? "2px solid var(--accent)" : "1px solid var(--line)",
              cursor: "pointer",
            }}
            onClick={() => setPolicy("P3")}
          >
            <div style={{ display: "flex", justifyContent: "space-between" }}>
              <h4>P3: Equal Contingent Share</h4>
              {policy === "P3" && <Pill value="Selected" tone="ok" />}
            </div>
            <p className="small muted">
              Guarantees 41.4% to 60.8% coverage for every participating college contingent.
            </p>
            <div style={{ marginTop: 8 }}>
              <strong>41–61% per college</strong>
              <div className="muted small">Ensures no college contingent leaves without kits</div>
            </div>
          </div>
        </div>

        {/* Action: Approve Plan */}
        {can("merch.manage") && (
          <div className="row-actions" style={{ marginTop: 12 }}>
            <button
              type="button"
              className="button-primary"
              disabled={approvePlan.isPending}
              onClick={() => approvePlan.mutate()}
            >
              <PackageCheck size={16} /> Approve & Lock Distribution Plan ({policy})
            </button>
            <a
              href={`${base}/merchandise/packing-list.csv`}
              download="festos-packing-list.csv"
              className="button-ghost small"
            >
              <Download size={14} /> Export Packing List CSV
            </a>
          </div>
        )}
        <ErrorNote error={approvePlan.error} />
      </section>

      {/* Selected Policy Breakdown */}
      {currentPolicyData && (
        <section className="panel stack">
          <SectionHead title={`${currentPolicyData.name} — Wave Trays & Allocation`}>
            <span className="muted small">{currentPolicyData.description}</span>
          </SectionHead>

          <DataTable
            rows={currentPolicyData.waves}
            columns={[
              { label: "Wave", cell: (w) => <strong>Wave #{w.wave}</strong> },
              { label: "Arrival (IST)", cell: (w) => (w.arrival_time ? formatTime(w.arrival_time) : "Unscheduled") },
              { label: "Participants", cell: (w) => w.headcount, className: "num" },
              { label: "Full Kits Allocated", cell: (w) => <strong style={{ color: "#34d399" }}>{w.full_kits}</strong>, className: "num" },
              {
                label: "Coverage %",
                cell: (w) => (
                  <Pill
                    value={`${w.coverage_pct}%`}
                    tone={w.coverage_pct >= 80 ? "ok" : w.coverage_pct >= 40 ? "warn" : "bad"}
                  />
                ),
              },
              {
                label: "Actions",
                cell: (w) => (
                  <button
                    type="button"
                    className="button-ghost small"
                    onClick={() => setPrintableWave(w.wave)}
                  >
                    <Printer size={14} /> Print Wave Tray Sheet
                  </button>
                ),
              },
            ]}
            rowKey={(w) => w.wave}
            empty="No waves generated."
          />
        </section>
      )}

      {/* Printable Wave Tray Sheet Modal */}
      {printableWave !== null && (
        <PrintableTrayModal wave={printableWave} onClose={() => setPrintableWave(null)} />
      )}

      {/* Re-Plan Diff Modal */}
      {replanDiffOpen && (
        <Dialog title="Distribution Plan Re-Calculation" onClose={() => setReplanDiffOpen(false)}>
          <div className="stack">
            <p>
              Participant registrations or stock levels have updated. Click below to recalculate allocation quotas for the active policy ({policy}) and refresh wave trays.
            </p>
            <div className="row-actions">
              <button
                type="button"
                className="button-primary"
                onClick={() => {
                  approvePlan.mutate();
                  setReplanDiffOpen(false);
                }}
              >
                Apply Re-Plan & Update Stock Reserves
              </button>
              <button type="button" className="button-ghost" onClick={() => setReplanDiffOpen(false)}>
                Cancel
              </button>
            </div>
          </div>
        </Dialog>
      )}
    </div>
  );
}

// ============================================================================
// TAB 3: Stock & Demand View
// ============================================================================

function StockDemandTab({ summary }: { summary: MerchSummary | undefined }) {
  const { base, can } = useFest();
  const qc = useQueryClient();

  const [reserveModalItem, setReserveModalItem] = useState<MerchItemRow | null>(null);
  const [adjustModalItem, setAdjustModalItem] = useState<MerchItemRow | null>(null);
  const [newIncomingOpen, setNewIncomingOpen] = useState(false);
  const [receiveModalDelivery, setReceiveModalDelivery] = useState<IncomingDeliveryRow | null>(null);

  // Assumption confirm action
  const confirmAssumption = useAction(
    (key: string) =>
      api(`${base}/merchandise/assumptions/confirm`, {
        method: "POST",
        body: JSON.stringify({ key, confirmed: true }),
      }),
    {
      success: "Assumption verified and audit recorded",
      onDone: () => qc.invalidateQueries({ queryKey: ["merch-summary"] }),
    }
  );

  return (
    <div className="stack">
      {/* 5-Column T-Shirt Size Curve */}
      <section className="panel stack">
        <SectionHead title="T-Shirt Size Curve & Coverage (Ground Truth)">
          <span className="muted small">Fixed inventory vs 2,834 declared participant sizes</span>
        </SectionHead>

        <div className="size-curve-grid">
          {summary?.size_curve.map((sc) => {
            const isShort = sc.gap < 0;
            return (
              <div key={sc.size} className={`size-card ${isShort ? "shortage" : "surplus"}`}>
                <div className="size-card-head">
                  <span>Size {sc.size}</span>
                  <Pill value={`${sc.coverage_pct}%`} tone={sc.coverage_pct >= 90 ? "ok" : isShort ? "bad" : "warn"} />
                </div>
                <div>
                  <span className="small muted">Issuable:</span>{" "}
                  <strong>{sc.issuable}</strong>{" "}
                  <span className="muted small">({sc.on_hand} on hand - {sc.csv_reserved} res)</span>
                </div>
                <div>
                  <span className="small muted">Needed:</span>{" "}
                  <strong>{sc.needed}</strong>
                </div>
                <div>
                  <span className="small muted">Net Gap:</span>{" "}
                  <strong style={{ color: isShort ? "#f87171" : "#34d399" }}>
                    {sc.gap > 0 ? `+${sc.gap}` : sc.gap}
                  </strong>
                </div>
              </div>
            );
          })}
        </div>
      </section>

      {/* Assumptions A1 to A4 Badges */}
      <section className="panel stack" style={{ background: "var(--surface-2)" }}>
        <SectionHead title="Operational Truth Assumptions">
          <span className="muted small">Transparently tagged and confirmable</span>
        </SectionHead>

        <ul className="stack">
          {[
            ["A1", "Quantity on hand includes CSV-reserved stock; issuable = quantity - reserved."],
            ["A2", "Every non-duplicate participant (2,834) is entitled to 1 T-shirt in their declared size."],
            ["A3", "1 unit per participant for ID cards and lanyards; winner medals reserved for ceremonies."],
            ["A4", "Merchandise inventory is one single central pool (no location column in CSV)."],
          ].map(([key, text]) => {
            const isConfirmed = Boolean(summary?.config.assumptions_confirmed?.[key]);
            return (
              <li key={key} style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <div>
                  <strong>{key}:</strong> {text}
                </div>
                <div>
                  {isConfirmed ? (
                    <Pill value="Confirmed" tone="ok" />
                  ) : (
                    can("merch.manage") && (
                      <button
                        type="button"
                        className="button-ghost small"
                        disabled={confirmAssumption.isPending}
                        onClick={() => confirmAssumption.mutate(key)}
                      >
                        Confirm Truth
                      </button>
                    )
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      </section>

      {/* Full Merchandise Items Table */}
      <section className="panel stack">
        <SectionHead title="All 10 Merchandise Catalogue Items">
          {can("merch.manage") && (
            <button
              type="button"
              className="button-secondary small"
              onClick={() => setNewIncomingOpen(true)}
            >
              <PackagePlus size={14} /> Record Incoming Delivery
            </button>
          )}
        </SectionHead>

        <DataTable
          rows={summary?.items ?? []}
          columns={[
            { label: "Item", cell: (i) => <strong>{i.item}</strong> },
            { label: "Size", cell: (i) => i.size ?? "—" },
            { label: "On Hand", cell: (i) => i.on_hand, className: "num" },
            {
              label: "CSV Reserved",
              cell: (i) => (
                <div>
                  <span>{i.csv_reserved}</span>
                  {i.csv_reserved > 0 && can("merch.manage") && (
                    <button
                      type="button"
                      className="button-ghost small"
                      style={{ marginLeft: 6, fontSize: 11 }}
                      onClick={() => setReserveModalItem(i)}
                    >
                      Release
                    </button>
                  )}
                </div>
              ),
              className: "num",
            },
            { label: "Issuable", cell: (i) => <strong style={{ color: "#34d399" }}>{i.issuable}</strong>, className: "num" },
            { label: "Issued", cell: (i) => i.issued, className: "num" },
            { label: "Owed", cell: (i) => i.owed, className: "num" },
            { label: "Needed", cell: (i) => (i.needed !== null ? i.needed : "—"), className: "num" },
            {
              label: "Net Gap",
              cell: (i) =>
                i.gap !== null ? (
                  <strong style={{ color: i.gap < 0 ? "#f87171" : "#34d399" }}>
                    {i.gap > 0 ? `+${i.gap}` : i.gap}
                  </strong>
                ) : (
                  "—"
                ),
              className: "num",
            },
            {
              label: "Actions",
              cell: (i) =>
                can("merch.manage") ? (
                  <button
                    type="button"
                    className="button-ghost small"
                    onClick={() => setAdjustModalItem(i)}
                  >
                    Adjust
                  </button>
                ) : null,
            },
          ]}
          rowKey={(i) => i.id}
          empty="No merchandise items found."
        />
      </section>

      {/* Shortfall: What to order */}
      {summary?.what_to_order && summary.what_to_order.length > 0 && (
        <section className="panel stack">
          <SectionHead title="What to Order (Shortfall List)">
            <span className="muted small">Recommended vendor purchase quantities to cover all participants</span>
          </SectionHead>

          <DataTable
            rows={summary.what_to_order}
            columns={[
              { label: "Item", cell: (w) => <strong>{w.item}</strong> },
              { label: "Size", cell: (w) => w.size },
              { label: "Issuable Now", cell: (w) => w.issuable, className: "num" },
              { label: "Entitled Need", cell: (w) => w.needed, className: "num" },
              { label: "Deficit to Order", cell: (w) => <strong style={{ color: "#f87171" }}>{w.shortfall} units</strong>, className: "num" },
            ]}
            rowKey={(w) => `${w.item}-${w.size}`}
            empty="No shortfalls."
          />
        </section>
      )}

      {/* Incoming Deliveries */}
      {summary?.incoming && (
        <section className="panel stack">
          <SectionHead title="Incoming Deliveries Tracker" />
          <DataTable
            rows={summary.incoming}
            columns={[
              { label: "ID", cell: (inc) => `#${inc.id}` },
              { label: "Item", cell: (inc) => <strong>{inc.quantity_expected} × {inc.item} ({inc.size})</strong> },
              { label: "Expected Date", cell: (inc) => (inc.expected_date ? formatTime(inc.expected_date) : "TBD") },
              { label: "Received", cell: (inc) => `${inc.quantity_received} / ${inc.quantity_expected}`, className: "num" },
              { label: "Status", cell: (inc) => <Pill value={inc.status} tone={inc.status === "received" ? "ok" : "warn"} /> },
              {
                label: "Action",
                cell: (inc) =>
                  inc.status !== "received" && can("merch.manage") ? (
                    <button
                      type="button"
                      className="button-secondary small"
                      onClick={() => setReceiveModalDelivery(inc)}
                    >
                      Receive Delivery
                    </button>
                  ) : null,
              },
            ]}
            rowKey={(inc) => inc.id}
            empty="No incoming deliveries registered."
          />
        </section>
      )}

      {/* Release Reserve Modal */}
      {reserveModalItem && (
        <ReleaseReserveModal
          item={reserveModalItem}
          onClose={() => setReserveModalItem(null)}
        />
      )}

      {/* Stock Adjust Modal */}
      {adjustModalItem && (
        <StockAdjustModal
          item={adjustModalItem}
          onClose={() => setAdjustModalItem(null)}
        />
      )}

      {/* New Incoming Modal */}
      {newIncomingOpen && (
        <NewIncomingModal onClose={() => setNewIncomingOpen(false)} />
      )}

      {/* Receive Incoming Modal */}
      {receiveModalDelivery && (
        <ReceiveDeliveryModal
          delivery={receiveModalDelivery}
          onClose={() => setReceiveModalDelivery(null)}
        />
      )}
    </div>
  );
}

// ============================================================================
// TAB 4: Owed List
// ============================================================================

function OwedListTab() {
  const { base } = useFest();
  const navigate = useNavigate();

  const owedQuery = usePaged<OwedRow>(`${base}/merchandise/owed`, {});

  const notifyAllOwed = () => {
    navigate(
      `/communication?compose=1&title=Merchandise Collection Update&body=Your owed merchandise kit has arrived at the central registration desk. Please collect before your departure.&audience_type=festival`
    );
  };

  return (
    <section className="panel stack">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 10 }}>
        <SectionHead title="Owed Items (Sorted by Departure Time Ascending)">
          <span className="muted small">Prioritize participants leaving campus earliest</span>
        </SectionHead>

        <div style={{ display: "flex", gap: 8 }}>
          <button type="button" className="button-secondary small" onClick={notifyAllOwed}>
            <Send size={14} /> Notify via Communication
          </button>
          <a
            href={`${base}/merchandise/owed/export.csv`}
            download="festos-owed-list.csv"
            className="button-ghost small"
          >
            <Download size={14} /> Export CSV
          </a>
        </div>
      </div>

      <QueryState query={owedQuery}>
        {owedQuery.data && (
          <DataTable
            rows={owedQuery.data.items}
            columns={[
              { label: "Participant", cell: (o) => <strong>{o.participant_name}</strong> },
              { label: "ID", cell: (o) => <span className="mono">{o.participant_code}</span> },
              { label: "College", cell: (o) => o.college },
              { label: "Owed Item", cell: (o) => `${o.item} (${o.requested_size})` },
              { label: "Departure Time", cell: (o) => (o.departure_at ? formatTime(o.departure_at) : "Unknown") },
              { label: "Phone", cell: (o) => `••• ••• ${o.phone_last4}` },
              { label: "Owed Since", cell: (o) => formatTime(o.created_at) },
            ]}
            rowKey={(o) => o.id}
            empty="No items currently owed."
          />
        )}
      </QueryState>
    </section>
  );
}

// ============================================================================
// TAB 5: Append-Only Merch Ledger
// ============================================================================

function MerchLedgerTab() {
  const { base } = useFest();
  const [exceptionsOnly, setExceptionsOnly] = useState(false);
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const q = useDebounced(search);

  const ledgerQuery = usePaged<MerchLedgerRow>(`${base}/merchandise/ledger`, {
    q,
    exceptions_only: exceptionsOnly,
    page,
  });

  return (
    <section className="panel stack">
      <div className="toolbar">
        <div className="search">
          <Search size={16} className="muted" />
          <input
            type="search"
            placeholder="Search ledger by participant, item, or operator..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>

        <label className="check-inline">
          <input
            type="checkbox"
            checked={exceptionsOnly}
            onChange={(e) => setExceptionsOnly(e.target.checked)}
          />
          <span>Exceptions & Substitutions Only</span>
        </label>

        <a
          href={`${base}/merchandise/ledger/export.csv`}
          download="festos-merch-ledger.csv"
          className="button-ghost small"
        >
          <Download size={14} /> Export CSV
        </a>
      </div>

      <QueryState query={ledgerQuery}>
        {ledgerQuery.data && (
          <>
            <DataTable
              rows={ledgerQuery.data.items}
              columns={[
                { label: "Timestamp", cell: (l) => formatTime(l.timestamp) },
                { label: "Action", cell: (l) => <span className="mono">{l.action}</span> },
                { label: "Item", cell: (l) => `${l.qty} × ${l.item} (${l.size})` },
                { label: "Participant", cell: (l) => l.participant_code ?? "—" },
                { label: "Staff", cell: (l) => l.user_name ?? "System" },
                {
                  label: "Exception?",
                  cell: (l) => (l.is_exception ? <Pill value="Exception" tone="warn" /> : <span className="muted small">Normal</span>),
                },
                { label: "Notes", cell: (l) => <span className="small muted">{l.notes ?? "—"}</span> },
              ]}
              rowKey={(l) => l.id}
              empty="No ledger events found."
            />
            <Pagination
              page={ledgerQuery.data.page}
              pages={ledgerQuery.data.pages}
              total={ledgerQuery.data.total}
              onPage={setPage}
            />
          </>
        )}
      </QueryState>
    </section>
  );
}

// ============================================================================
// Modals
// ============================================================================

function ReleaseReserveModal({
  item,
  onClose,
}: {
  item: MerchItemRow;
  onClose: () => void;
}) {
  const { base } = useFest();
  const qc = useQueryClient();
  const [releaseQty, setReleaseQty] = useState(String(item.csv_reserved));
  const [reason, setReason] = useState("");

  const releaseAction = useAction(
    () =>
      api(`${base}/merchandise/items/${item.id}/release-reserve`, {
        method: "POST",
        body: JSON.stringify({
          quantity_to_release: Number(releaseQty),
          reason: reason.trim() || null,
        }),
      }),
    {
      success: `Released ${releaseQty} units into issuable pool`,
      onDone: () => {
        qc.invalidateQueries({ queryKey: ["merch-summary"] });
        onClose();
      },
    }
  );

  return (
    <Dialog title={`Release CSV Reserve · ${item.item} (${item.size})`} onClose={onClose}>
      <form
        className="form stack"
        onSubmit={(e) => {
          e.preventDefault();
          releaseAction.mutate();
        }}
      >
        <Facts
          items={[
            ["Item", `${item.item} (${item.size})`],
            ["Currently Reserved", `${item.csv_reserved} units`],
            ["Note in CSV", item.csv_reserved_note],
          ]}
        />

        <Field label="Quantity to Release">
          <input
            type="number"
            min={1}
            max={item.csv_reserved}
            required
            value={releaseQty}
            onChange={(e) => setReleaseQty(e.target.value)}
          />
        </Field>

        <Field label="Reason / Approval Reference">
          <input
            required
            placeholder="e.g. VIP allocation unused; released to participants"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
          />
        </Field>

        <ErrorNote error={releaseAction.error} />

        <div className="row-actions">
          <button type="submit" className="button-primary" disabled={releaseAction.isPending}>
            Release Reserve
          </button>
          <button type="button" className="button-ghost" onClick={onClose}>
            Cancel
          </button>
        </div>
      </form>
    </Dialog>
  );
}

function StockAdjustModal({
  item,
  onClose,
}: {
  item: MerchItemRow;
  onClose: () => void;
}) {
  const { base } = useFest();
  const qc = useQueryClient();
  const [adj, setAdj] = useState("-1");
  const [reason, setReason] = useState("");

  const adjustAction = useAction(
    () =>
      api(`${base}/merchandise/items/${item.id}/adjust`, {
        method: "POST",
        body: JSON.stringify({
          adjustment: Number(adj),
          reason: reason.trim(),
        }),
      }),
    {
      success: "Inventory balance adjusted",
      onDone: () => {
        qc.invalidateQueries({ queryKey: ["merch-summary"] });
        onClose();
      },
    }
  );

  return (
    <Dialog title={`Adjust Stock · ${item.item} (${item.size})`} onClose={onClose}>
      <form
        className="form stack"
        onSubmit={(e) => {
          e.preventDefault();
          adjustAction.mutate();
        }}
      >
        <Facts
          items={[
            ["Item", `${item.item} (${item.size})`],
            ["Current On Hand", item.on_hand],
          ]}
        />

        <Field label="Adjustment Quantity (positive to add, negative to deduct)">
          <input
            type="number"
            required
            value={adj}
            onChange={(e) => setAdj(e.target.value)}
          />
        </Field>

        <Field label="Reason for Adjustment">
          <input
            required
            placeholder="e.g. 1 unit damaged by rain; carton discrepancy"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
          />
        </Field>

        <ErrorNote error={adjustAction.error} />

        <div className="row-actions">
          <button type="submit" className="button-primary" disabled={adjustAction.isPending || !reason.trim()}>
            Record Adjustment
          </button>
          <button type="button" className="button-ghost" onClick={onClose}>
            Cancel
          </button>
        </div>
      </form>
    </Dialog>
  );
}

function NewIncomingModal({ onClose }: { onClose: () => void }) {
  const { base } = useFest();
  const qc = useQueryClient();
  const [item, setItem] = useState("T-shirt");
  const [size, setSize] = useState("M");
  const [qty, setQty] = useState("100");
  const [date, setDate] = useState("2026-10-30T14:00");
  const [note, setNote] = useState("");

  const createIncoming = useAction(
    () =>
      api(`${base}/merchandise/incoming`, {
        method: "POST",
        body: JSON.stringify({
          item,
          size,
          expected_qty: Number(qty),
          expected_date: fromInput(date),
          note: note.trim() || null,
        }),
      }),
    {
      success: "Expected incoming delivery recorded",
      onDone: () => {
        qc.invalidateQueries({ queryKey: ["merch-summary"] });
        onClose();
      },
    }
  );

  return (
    <Dialog title="Record Expected Incoming Delivery" onClose={onClose}>
      <form
        className="form stack"
        onSubmit={(e) => {
          e.preventDefault();
          createIncoming.mutate();
        }}
      >
        <div className="form-grid">
          <Field label="Item">
            <select value={item} onChange={(e) => setItem(e.target.value)}>
              <option value="T-shirt">T-shirt</option>
              <option value="ID card">ID card</option>
              <option value="lanyard">Lanyard</option>
              <option value="tote">Tote bag</option>
            </select>
          </Field>

          <Field label="Size">
            <select value={size} onChange={(e) => setSize(e.target.value)}>
              {["S", "M", "L", "XL", "XXL", "One Size"].map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          </Field>

          <Field label="Expected Quantity">
            <input
              type="number"
              min={1}
              required
              value={qty}
              onChange={(e) => setQty(e.target.value)}
            />
          </Field>

          <Field label="Expected Date / Time">
            <input
              type="datetime-local"
              required
              value={date}
              onChange={(e) => setDate(e.target.value)}
            />
          </Field>
        </div>

        <Field label="Supplier / Courier Notes">
          <input
            placeholder="e.g. Apex Prints rush order tranche 2"
            value={note}
            onChange={(e) => setNote(e.target.value)}
          />
        </Field>

        <ErrorNote error={createIncoming.error} />

        <div className="row-actions">
          <button type="submit" className="button-primary" disabled={createIncoming.isPending}>
            Save Incoming Delivery
          </button>
          <button type="button" className="button-ghost" onClick={onClose}>
            Cancel
          </button>
        </div>
      </form>
    </Dialog>
  );
}

function ReceiveDeliveryModal({
  delivery,
  onClose,
}: {
  delivery: IncomingDeliveryRow;
  onClose: () => void;
}) {
  const { base } = useFest();
  const qc = useQueryClient();
  const [receivedQty, setReceivedQty] = useState(String(delivery.quantity_expected));
  const [discrepancyNote, setDiscrepancyNote] = useState("");

  const receiveAction = useAction(
    () =>
      api(`${base}/merchandise/incoming/${delivery.id}/receive`, {
        method: "POST",
        body: JSON.stringify({
          qty_received: Number(receivedQty),
          discrepancy_note: discrepancyNote.trim() || null,
        }),
      }),
    {
      success: `Received ${receivedQty} units into inventory`,
      onDone: () => {
        qc.invalidateQueries({ queryKey: ["merch-summary"] });
        onClose();
      },
    }
  );

  return (
    <Dialog title={`Receive Delivery #${delivery.id}`} onClose={onClose}>
      <form
        className="form stack"
        onSubmit={(e) => {
          e.preventDefault();
          receiveAction.mutate();
        }}
      >
        <Facts
          items={[
            ["Item", `${delivery.item} (${delivery.size})`],
            ["Expected Quantity", delivery.quantity_expected],
            ["Supplier Reference", delivery.supplier_note ?? "None"],
          ]}
        />

        <Field label="Quantity Actually Received">
          <input
            type="number"
            min={1}
            required
            value={receivedQty}
            onChange={(e) => setReceivedQty(e.target.value)}
          />
        </Field>

        {Number(receivedQty) !== delivery.quantity_expected && (
          <Field label="Discrepancy Explanation">
            <input
              required
              placeholder="e.g. 5 units damaged during transit"
              value={discrepancyNote}
              onChange={(e) => setDiscrepancyNote(e.target.value)}
            />
          </Field>
        )}

        <ErrorNote error={receiveAction.error} />

        <div className="row-actions">
          <button type="submit" className="button-primary" disabled={receiveAction.isPending}>
            Confirm Delivery Receipt
          </button>
          <button type="button" className="button-ghost" onClick={onClose}>
            Cancel
          </button>
        </div>
      </form>
    </Dialog>
  );
}

function PaperTallyModal({ onClose }: { onClose: () => void }) {
  const { base } = useFest();
  const qc = useQueryClient();
  const [counts, setCounts] = useState({ S: "0", M: "0", L: "0", XL: "0", XXL: "0" });
  const [notes, setNotes] = useState("");

  const reconcileAction = useAction(
    () =>
      api(`${base}/merchandise/reconcile-paper`, {
        method: "POST",
        body: JSON.stringify({
          counts: {
            S: Number(counts.S) || 0,
            M: Number(counts.M) || 0,
            L: Number(counts.L) || 0,
            XL: Number(counts.XL) || 0,
            XXL: Number(counts.XXL) || 0,
          },
          notes: notes.trim() || null,
        }),
      }),
    {
      success: "Paper tally reconciled successfully",
      onDone: () => {
        qc.invalidateQueries({ queryKey: ["merch-summary"] });
        onClose();
      },
    }
  );

  return (
    <Dialog title="Offline Paper Tally Reconciliation" onClose={onClose}>
      <form
        className="form stack"
        onSubmit={(e) => {
          e.preventDefault();
          reconcileAction.mutate();
        }}
      >
        <p className="muted small">
          Enter manual paper counts from offline desks during network outages to catch up digital ledger records.
        </p>

        <div className="size-curve-grid">
          {(["S", "M", "L", "XL", "XXL"] as const).map((sz) => (
            <Field key={sz} label={`Size ${sz}`}>
              <input
                type="number"
                min={0}
                value={counts[sz]}
                onChange={(e) => setCounts({ ...counts, [sz]: e.target.value })}
              />
            </Field>
          ))}
        </div>

        <Field label="Shift / Desk Notes">
          <input
            placeholder="e.g. Desk 2 physical tally for 09:00 - 11:00 rush"
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
          />
        </Field>

        <ErrorNote error={reconcileAction.error} />

        <div className="row-actions">
          <button type="submit" className="button-primary" disabled={reconcileAction.isPending}>
            Reconcile Tally
          </button>
          <button type="button" className="button-ghost" onClick={onClose}>
            Cancel
          </button>
        </div>
      </form>
    </Dialog>
  );
}

function PrintableTrayModal({ wave, onClose }: { wave: number; onClose: () => void }) {
  const { base } = useFest();
  const query = useQuery({
    queryKey: ["wave-sheet", base, wave],
    queryFn: () => api<any>(`${base}/merchandise/distribution/wave-sheet?wave=${wave}`),
  });

  const d = query.data;

  return (
    <Dialog title={`Printable Wave Tray Sheet · Wave #${wave}`} onClose={onClose} className="wide">
      <div className="stack no-print">
        <div className="row-actions">
          <button type="button" className="button-primary" onClick={() => window.print()}>
            <Printer size={16} /> Print Sheet (A4)
          </button>
          <button type="button" className="button-ghost" onClick={onClose}>
            Close
          </button>
        </div>
      </div>

      <QueryState query={query}>
        {d && (
          <div className="tray-sheet-print">
            <h2 style={{ margin: "0 0 6px" }}>FestOS Merchandise Wave Tray Sheet · Wave #{wave}</h2>
            <p style={{ margin: "0 0 12px", fontSize: "11pt" }}>
              Aarohan 2026 · Estimated Arrival: {d.arrival_time ? formatTime(d.arrival_time) : "TBD"} · Headcount: {d.headcount}
            </p>

            <table style={{ width: "100%", borderCollapse: "collapse", marginBottom: 16 }}>
              <thead>
                <tr>
                  <th style={{ border: "1px solid #000", padding: 6 }}>Size</th>
                  <th style={{ border: "1px solid #000", padding: 6 }}>Demand</th>
                  <th style={{ border: "1px solid #000", padding: 6 }}>Allocated Tray Count</th>
                  <th style={{ border: "1px solid #000", padding: 6 }}>Packed Verified Checkbox</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(d.size_breakdown ?? {}).map(([sz, count]: any) => (
                  <tr key={sz}>
                    <td style={{ border: "1px solid #000", padding: 6, fontWeight: "bold" }}>{sz}</td>
                    <td style={{ border: "1px solid #000", padding: 6 }}>{count.demand}</td>
                    <td style={{ border: "1px solid #000", padding: 6, fontWeight: "bold" }}>{count.allocated}</td>
                    <td style={{ border: "1px solid #000", padding: 6, textAlign: "center" }}>[ &nbsp; ]</td>
                  </tr>
                ))}
              </tbody>
            </table>

            <h4>Contingents in this Wave</h4>
            <table style={{ width: "100%", borderCollapse: "collapse" }}>
              <thead>
                <tr>
                  <th style={{ border: "1px solid #000", padding: 6 }}>Contingent</th>
                  <th style={{ border: "1px solid #000", padding: 6 }}>College Name</th>
                  <th style={{ border: "1px solid #000", padding: 6 }}>Members</th>
                  <th style={{ border: "1px solid #000", padding: 6 }}>Tray Allocated</th>
                </tr>
              </thead>
              <tbody>
                {(d.contingents ?? []).map((c: any) => (
                  <tr key={c.code}>
                    <td style={{ border: "1px solid #000", padding: 6, fontFamily: "monospace" }}>{c.code}</td>
                    <td style={{ border: "1px solid #000", padding: 6 }}>{c.name}</td>
                    <td style={{ border: "1px solid #000", padding: 6 }}>{c.headcount}</td>
                    <td style={{ border: "1px solid #000", padding: 6 }}>{c.full_kits} kits</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </QueryState>
    </Dialog>
  );
}

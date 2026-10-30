import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Archive, FileSpreadsheet, Plus,
  ShieldAlert, ShieldCheck, Store, Wrench,
} from "lucide-react";
import { type FormEvent, useMemo, useState } from "react";
import { Link } from "react-router-dom";

import {
  type Column, DataTable, Dialog, ErrorNote, Facts, Field, Moment,
  notify, Pill, QueryState, SectionHead, Tabs, useAction, useDebounced, useFest,
} from "../components/ui";
import { api } from "../lib/api";
import { formatTime } from "../lib/format";

interface CertificateInfo {
  status: "expired" | "expires_during_festival" | "valid_beyond_festival" | "missing";
  label: string;
  tone: "ok" | "warn" | "bad";
  expiry: string | null;
  boundary_flag: boolean;
  boundary_note: string;
  is_compliant: boolean;
  verified_by: string | null;
  verified_at: string | null;
  verification_note: string | null;
}

interface VendorInconsistency {
  key: string;
  severity: string;
  title: string;
  detail: string;
  resolved: string;
}

interface VendorRecord {
  id: number;
  code: string;
  name: string;
  menu: string | null;
  dietary_options: string[];
  verified_dietary_options: string[] | null;
  effective_dietary_options: string[];
  hygiene_certificate_expiry: string | null;
  certificate: CertificateInfo;
  hours: string | null;
  meal_windows: string[];
  capacity: number | null;
  capacity_unit: string | null;
  capacity_unit_display: string;
  capacity_verified_by: string | null;
  capacity_verified_at: string | null;
  payment_terms: string | null;
  status: string;
  operational_status: "unconfirmed" | "confirmed_active" | "interrupted" | "archived";
  source: string;
  original_pack_data: Record<string, string> | null;
  nut_free_verified: boolean;
  allergen_controls_verified: boolean;
  cross_contamination_note: string | null;
  contact_name: string | null;
  contact_phone: string | null;
  verification_notes: string | null;
  verified_by: string | null;
  verified_at: string | null;
  archived_at: string | null;
  inconsistencies: VendorInconsistency[];
  requires_attention: boolean;
  attention_reasons: string[];
}

interface DietaryMatrixRow {
  key: string;
  label: string;
  participant_need_label: string;
  participant_count: number;
  listed_count: number;
  listed_codes: string[];
  valid_beyond_count: number;
  valid_beyond_codes: string[];
  expiring_during_count: number;
  expiring_during_codes: string[];
  expired_count: number;
  expired_codes: string[];
  independently_verified_count: number;
  independently_verified_codes: string[];
  status: string;
  status_label: string;
  tone: "ok" | "warn" | "bad";
  note: string;
}

interface IntelligenceAlert {
  id: string;
  severity: "critical" | "high" | "medium";
  title: string;
  detail: string;
  affected_vendors: string[];
  participant_impact: number | null;
  category: string;
}

interface MealWindowSummary {
  key: string;
  label: string;
  window: string;
  start: number;
  end: number;
  total_vendors: number;
  vendor_codes: string[];
  valid_beyond_codes: string[];
  expiring_codes: string[];
  expired_codes: string[];
}

interface InterruptionCandidate {
  code: string;
  name: string;
  menu: string | null;
  hours: string | null;
  dietary_options: string[];
  certificate: CertificateInfo;
  operational_status: string;
  listed_capacity: number | null;
  capacity_unit: string | null;
  capacity_comparable: boolean;
  capacity_comparison: string;
  shared_dietary: string[];
  missing_dietary: string[];
  shared_meal_windows: string[];
  missing_meal_windows: string[];
  suitability: string;
}

interface InterruptionRecord {
  id: number;
  vendor_id: number;
  vendor_code: string;
  reason: string;
  simulation_report: {
    target_vendor: {
      code: string;
      name: string;
      menu: string | null;
      dietary_options: string[];
      hours: string | null;
      meal_windows: string[];
      certificate: CertificateInfo;
      listed_capacity: number | null;
      capacity_unit: string | null;
      capacity_status: string;
      operational_status: string;
    };
    alternatives: InterruptionCandidate[];
    excluded_non_compliant: { code: string; name: string; reason: string }[];
    uncovered_requirements: { requirement: string; participant_count: number | null; severity: string; explanation: string }[];
    sufficient_coverage_established: boolean;
    coverage_verdict: string;
    auto_reassignment_performed: boolean;
    simulation_notice: string;
  };
  replacement_codes: string[];
  status: "simulated" | "pending_approval" | "approved" | "rejected" | "resolved";
  created_by_name: string;
  created_at: string | null;
  decided_by_name: string | null;
  decided_at: string | null;
  decision_reason: string | null;
}

interface VendorsIntelligence {
  festival: { id: number; name: string; start_date: string; end_date: string };
  overview: {
    total_vendors: number;
    certificates_expired: number;
    expired_vendor_codes: string[];
    certificates_expiring_oct31: number;
    expiring_vendor_codes: string[];
    certificates_valid_beyond: number;
    valid_beyond_vendor_codes: string[];
    certificates_missing: number;
    dietary_coverage_gaps: number;
    vendors_requiring_verification: number;
    requiring_verification_codes: string[];
    confirmed_active_count: number;
    unconfirmed_count: number;
    interrupted_count: number;
  };
  alerts: IntelligenceAlert[];
  dietary_intelligence: {
    total_participants: number;
    blank_dietary_participants: number;
    participant_counts: Record<string, number>;
    coverage_matrix: DietaryMatrixRow[];
    inconsistencies: (VendorInconsistency & {
      vendor_code: string;
      vendor_name: string;
      menu: string | null;
      dietary_options: string[];
      hours: string | null;
    })[];
  };
  hours_and_capacity: {
    total_listed_capacity: number;
    capacity_unit_status: string;
    verified_unit_count: number;
    unverified_unit_count: number;
    numerical_allocation_enabled: boolean;
    hours_distribution: { hours: string; count: number; vendor_codes: string[] }[];
    meal_windows: MealWindowSummary[];
  };
  procurement_scope_note: {
    scope: string;
    message: string;
  };
  interruptions: InterruptionRecord[];
  vendors: VendorRecord[];
}

interface VendorDetailResponse {
  vendor: VendorRecord;
  data_issues: { id: number; issue: string; detail: string | null; status: string }[];
  interruptions: InterruptionRecord[];
  audit_history: {
    id: number;
    actor: string;
    action: string;
    summary: string;
    reason: string | null;
    before: Record<string, unknown> | null;
    after: Record<string, unknown> | null;
    created_at: string | null;
  }[];
}

function operationalTone(status: VendorRecord["operational_status"]): "ok" | "warn" | "bad" | "info" {
  if (status === "confirmed_active") return "ok";
  if (status === "interrupted" || status === "archived") return "bad";
  return "warn";
}

function operationalLabel(status: VendorRecord["operational_status"]): string {
  if (status === "confirmed_active") return "Confirmed active";
  if (status === "interrupted") return "Unavailable / Interrupted";
  if (status === "archived") return "Archived";
  return "Unconfirmed (CSV import)";
}

export function Vendors() {
  const { base, can } = useFest();
  const queryClient = useQueryClient();

  const [sectionTab, setSectionTab] = useState<"directory" | "compliance" | "hours" | "interruption">("directory");
  const [filterBy, setFilterBy] = useState<string>("all");
  const [dietaryFilter, setDietaryFilter] = useState<string>("");
  const [hoursFilter, setHoursFilter] = useState<string>("");
  const [includeArchived, setIncludeArchived] = useState(false);
  const [search, setSearch] = useState("");
  const debouncedSearch = useDebounced(search, 200);

  const [selectedCode, setSelectedCode] = useState<string | null>(null);
  const [creatingVendor, setCreatingVendor] = useState(false);
  const [importingCsv, setImportingCsv] = useState(false);
  const [simulatingVendorCode, setSimulatingVendorCode] = useState<string>("VND02");
  const [simulationReason, setSimulationReason] = useState<string>("");
  const [activeInterruption, setActiveInterruption] = useState<InterruptionRecord | null>(null);

  const intelligence = useQuery({
    queryKey: ["vendors-intelligence", base],
    queryFn: () => api<VendorsIntelligence>(`${base}/vendors/intelligence`),
  });

  const directoryQuery = useQuery({
    queryKey: ["vendors-list", base, includeArchived],
    queryFn: () => api<{ items: VendorRecord[]; total: number }>(`${base}/vendors?include_archived=${includeArchived}`),
  });

  const filteredVendors = useMemo(() => {
    const items = directoryQuery.data?.items ?? intelligence.data?.vendors ?? [];
    return items.filter((v) => {
      if (filterBy === "valid" && v.certificate.status !== "valid_beyond_festival") return false;
      if (filterBy === "expired" && v.certificate.status !== "expired") return false;
      if (filterBy === "expiring" && v.certificate.status !== "expires_during_festival") return false;
      if (filterBy === "attention" && !v.requires_attention) return false;
      if (dietaryFilter) {
        const d = dietaryFilter.toLowerCase();
        const hasTag = v.effective_dietary_options.some((t) => t.toLowerCase() === d) || (d === "nut-free" && v.nut_free_verified);
        if (!hasTag) return false;
      }
      if (hoursFilter && v.hours !== hoursFilter) return false;
      if (debouncedSearch) {
        const q = debouncedSearch.toLowerCase();
        const match =
          v.code.toLowerCase().includes(q) ||
          v.name.toLowerCase().includes(q) ||
          (v.menu ?? "").toLowerCase().includes(q) ||
          (v.payment_terms ?? "").toLowerCase().includes(q) ||
          v.effective_dietary_options.some((t) => t.toLowerCase().includes(q));
        if (!match) return false;
      }
      return true;
    });
  }, [directoryQuery.data, intelligence.data, filterBy, dietaryFilter, hoursFilter, debouncedSearch]);

  const simulateAction = useAction(
    () =>
      api<InterruptionRecord>(`${base}/vendors/${simulatingVendorCode}/interruptions/simulate`, {
        method: "POST",
        body: JSON.stringify({ reason: simulationReason.trim() || `Operational interruption check for ${simulatingVendorCode}` }),
      }),
    {
      success: (res) => `Interruption simulated for ${res.vendor_code}`,
      onDone: (res) => {
        setActiveInterruption(res);
        setSimulationReason("");
      },
    }
  );

  const columns: Column<VendorRecord>[] = [
    {
      label: "Vendor ID",
      value: (v) => v.code,
      cell: (v) => (
        <div>
          <strong>{v.code}</strong>
          <div>
            <small className="muted">{v.source === "pack" ? "vendors.csv" : "Manual"}</small>
          </div>
        </div>
      ),
    },
    {
      label: "Vendor Name",
      value: (v) => v.name,
      cell: (v) => (
        <div>
          <strong>{v.name}</strong>
          <div style={{ marginTop: 4 }}>
            <Pill value={operationalLabel(v.operational_status)} tone={operationalTone(v.operational_status)} />
          </div>
        </div>
      ),
    },
    {
      label: "Menu",
      value: (v) => v.menu ?? "",
      cell: (v) => (
        <div>
          <span>{v.menu ?? <span className="unknown">Unspecified</span>}</span>
          {v.inconsistencies.length > 0 && (
            <div style={{ marginTop: 4 }}>
              <Pill value={`${v.inconsistencies.length} label mismatch`} tone="warn" />
            </div>
          )}
        </div>
      ),
    },
    {
      label: "Dietary Options",
      cell: (v) => (
        <div className="chips">
          {v.dietary_options.map((tag) => (
            <Pill key={tag} value={tag} tone="info" />
          ))}
          {v.verified_dietary_options && (
            <Pill value={`Verified: ${v.verified_dietary_options.join(", ")}`} tone="ok" />
          )}
          {v.nut_free_verified && v.allergen_controls_verified && (
            <Pill value="Nut-free verified" tone="ok" />
          )}
        </div>
      ),
    },
    {
      label: "Hygiene Certificate Status",
      value: (v) => v.certificate.status,
      cell: (v) => (
        <div>
          <Pill value={v.certificate.label} tone={v.certificate.tone} />
          {v.certificate.verified_by && (
            <div>
              <small className="muted">Verified by {v.certificate.verified_by}</small>
            </div>
          )}
        </div>
      ),
    },
    {
      label: "Certificate Expiry",
      value: (v) => v.hygiene_certificate_expiry ?? "",
      cell: (v) => (
        <div>
          <strong>{v.hygiene_certificate_expiry ?? "Missing"}</strong>
          {v.certificate.boundary_flag && (
            <div>
              <small className="tone-warn">Boundary: verify Oct 31–Nov 1</small>
            </div>
          )}
        </div>
      ),
    },
    {
      label: "Operating Hours",
      value: (v) => v.hours ?? "",
      cell: (v) => (
        <div>
          <strong>{v.hours ?? "Unknown"}</strong>
          <div>
            <small className="muted">
              {v.hours === "07:00-23:00" ? "All 4 meal windows" : v.hours === "10:00-16:00" ? "Mid-day / Lunch only" : "Custom"}
            </small>
          </div>
        </div>
      ),
    },
    {
      label: "Listed Capacity",
      className: "num",
      value: (v) => v.capacity ?? 0,
      cell: (v) => <strong>{v.capacity !== null ? v.capacity.toLocaleString("en-IN") : "—"}</strong>,
    },
    {
      label: "Capacity Unit Verification",
      value: (v) => v.capacity_unit ?? "",
      cell: (v) =>
        v.capacity_unit ? (
          <div>
            <Pill value={v.capacity_unit} tone="ok" />
            <div>
              <small className="muted">by {v.capacity_verified_by}</small>
            </div>
          </div>
        ) : (
          <Pill value="Capacity unit not verified." tone="warn" />
        ),
    },
    {
      label: "Payment Terms",
      value: (v) => v.payment_terms ?? "",
      cell: (v) => <span>{v.payment_terms ?? <span className="unknown">Not specified</span>}</span>,
    },
  ];

  return (
    <QueryState query={intelligence}>
      {intelligence.data && (() => {
        const data = intelligence.data;
        const ov = data.overview;
        const hc = data.hours_and_capacity;
        const di = data.dietary_intelligence;

        return (
          <div className="stack">
            <Moment
              eyebrow={
                <>
                  <Store size={15} /> Food Vendor Operations & Dietary Intelligence · Festival Period {data.festival.start_date} to {data.festival.end_date}
                </>
              }
              title={`${ov.total_vendors} food vendors — ${ov.certificates_expired} expired certificates — ${ov.certificates_expiring_oct31} expire Oct 31 — ${di.participant_counts["nut allergy"] ?? 0} nut allergies with 0 declared nut-free vendors.`}
            >
              <div className="toolbar">
                <button
                  className="button-primary"
                  onClick={() => {
                    setSectionTab("interruption");
                  }}
                >
                  <ShieldAlert size={16} /> Report Vendor Unavailable
                </button>
                {can("vendors.write") && (
                  <>
                    <button className="button-secondary" onClick={() => setCreatingVendor(true)}>
                      <Plus size={16} /> Create Vendor
                    </button>
                    <button className="button-ghost" onClick={() => setImportingCsv(true)}>
                      <FileSpreadsheet size={16} /> Import CSV
                    </button>
                  </>
                )}
              </div>
            </Moment>

            {/* SECTION A: VENDOR OVERVIEW KPI CARDS */}
            <section aria-label="Vendor Overview">
              <div className="ribbon">
                <button
                  type="button"
                  className={`stat stat-button ${filterBy === "all" && sectionTab === "directory" ? "stat-active" : ""}`}
                  onClick={() => {
                    setSectionTab("directory");
                    setFilterBy("all");
                  }}
                >
                  <span>Total food vendors</span>
                  <strong>{ov.total_vendors}</strong>
                  <small>{ov.confirmed_active_count} confirmed · {ov.unconfirmed_count} unconfirmed</small>
                </button>

                <button
                  type="button"
                  className={`stat stat-button tone-bad ${filterBy === "expired" && sectionTab === "directory" ? "stat-active" : ""}`}
                  onClick={() => {
                    setSectionTab("directory");
                    setFilterBy("expired");
                  }}
                >
                  <span>Certificates expired</span>
                  <strong>{ov.certificates_expired}</strong>
                  <small>{ov.expired_vendor_codes.join(", ") || "None"} (2026-09-29)</small>
                </button>

                <button
                  type="button"
                  className={`stat stat-button tone-warn ${filterBy === "expiring" && sectionTab === "directory" ? "stat-active" : ""}`}
                  onClick={() => {
                    setSectionTab("directory");
                    setFilterBy("expiring");
                  }}
                >
                  <span>Expiring 31 Oct 2026</span>
                  <strong>{ov.certificates_expiring_oct31}</strong>
                  <small>{ov.expiring_vendor_codes.join(", ") || "None"} (Boundary check)</small>
                </button>

                <button
                  type="button"
                  className={`stat stat-button tone-ok ${filterBy === "valid" && sectionTab === "directory" ? "stat-active" : ""}`}
                  onClick={() => {
                    setSectionTab("directory");
                    setFilterBy("valid");
                  }}
                >
                  <span>Valid beyond festival</span>
                  <strong>{ov.certificates_valid_beyond}</strong>
                  <small>{ov.valid_beyond_vendor_codes.join(", ")}</small>
                </button>

                <button
                  type="button"
                  className={`stat stat-button tone-bad ${sectionTab === "compliance" ? "stat-active" : ""}`}
                  onClick={() => setSectionTab("compliance")}
                >
                  <span>Dietary coverage gaps</span>
                  <strong>{ov.dietary_coverage_gaps}</strong>
                  <small>Nut-free (0), Jain & Vegan (expired), GF (1)</small>
                </button>

                <button
                  type="button"
                  className={`stat stat-button tone-warn ${filterBy === "attention" && sectionTab === "directory" ? "stat-active" : ""}`}
                  onClick={() => {
                    setSectionTab("directory");
                    setFilterBy("attention");
                  }}
                >
                  <span>Requiring verification</span>
                  <strong>{ov.vendors_requiring_verification}</strong>
                  <small>Capacity unit, status, or labels</small>
                </button>
              </div>
            </section>

            {/* NAVIGATION TABS FOR SECTIONS B, C, D, E */}
            <Tabs
              value={sectionTab}
              onChange={setSectionTab}
              options={[
                ["directory", `B. Vendor Directory (${filteredVendors.length})`],
                ["compliance", `C. Compliance & Dietary Intelligence (${data.alerts.length} alerts)`],
                ["hours", `D. Operating Hours & Capacity (${hc.total_listed_capacity.toLocaleString("en-IN")} listed)`],
                ["interruption", `E. Vendor Interruption & Recovery (${data.interruptions.length})`],
              ]}
            />

            {/* SECTION B: VENDOR DIRECTORY */}
            {sectionTab === "directory" && (
              <section className="panel stack" aria-label="Vendor Directory">
                <SectionHead title="Food Vendor Directory">
                  <span className="muted small">
                    Click any vendor row to inspect original CSV fields, normalized dietary tags, certificate boundary status, and manually verified records.
                  </span>
                </SectionHead>

                <div className="toolbar">
                  <input
                    type="search"
                    placeholder="Search vendor ID, name, menu, dietary, payment terms..."
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                    style={{ minWidth: 280 }}
                  />

                  <div className="tabs" role="group" aria-label="Certificate and attention filters">
                    {([
                      ["all", "All vendors"],
                      ["valid", "Valid certificates"],
                      ["expired", "Expired certificates"],
                      ["expiring", "Expiring certificates"],
                      ["attention", "Requires attention"],
                    ] as const).map(([key, label]) => (
                      <button
                        key={key}
                        type="button"
                        className={filterBy === key ? "active" : ""}
                        onClick={() => setFilterBy(key)}
                      >
                        {label}
                      </button>
                    ))}
                  </div>

                  <select
                    aria-label="Dietary coverage filter"
                    value={dietaryFilter}
                    onChange={(e) => setDietaryFilter(e.target.value)}
                  >
                    <option value="">All dietary coverage</option>
                    <option value="vegetarian">Vegetarian</option>
                    <option value="non-vegetarian">Non-Vegetarian</option>
                    <option value="halal">Halal</option>
                    <option value="gluten-free">Gluten-Free</option>
                    <option value="jain">Jain</option>
                    <option value="vegan">Vegan</option>
                    <option value="nut-free">Nut-Free</option>
                  </select>

                  <select
                    aria-label="Operating hours filter"
                    value={hoursFilter}
                    onChange={(e) => setHoursFilter(e.target.value)}
                  >
                    <option value="">All operating hours</option>
                    <option value="07:00-23:00">07:00-23:00 (Full day)</option>
                    <option value="10:00-16:00">10:00-16:00 (Mid-day)</option>
                  </select>

                  <label className="check-inline">
                    <input
                      type="checkbox"
                      checked={includeArchived}
                      onChange={(e) => setIncludeArchived(e.target.checked)}
                    />
                    <span>Show archived</span>
                  </label>
                </div>

                <DataTable
                  rows={filteredVendors}
                  columns={columns}
                  rowKey={(v) => v.code}
                  onRow={(v) => setSelectedCode(v.code)}
                  empty="No vendors match the selected filters."
                />

                <div className="callout">
                  <strong>Procurement & Data Provenance Scope:</strong> {data.procurement_scope_note.message}{" "}
                  <Link to="/venues">Open Venues & Resources (Merchandise Inventory)</Link> ·{" "}
                  <Link to="/finance">Open Finance (Verified Settlements)</Link>
                </div>
              </section>
            )}

            {/* SECTION C: COMPLIANCE & DIETARY INTELLIGENCE */}
            {sectionTab === "compliance" && (
              <div className="stack">
                <div className="ops-grid">
                  <section className="panel alerts-panel">
                    <SectionHead title="Actionable Compliance & Allergen Alerts">
                      <span className="muted small">Derived live from vendors.csv & participants.csv</span>
                    </SectionHead>
                    <div className="alert-list">
                      {data.alerts.map((alert) => (
                        <article key={alert.id} className={`alert-row severity-${alert.severity}`}>
                          <span className="severity">{alert.severity}</span>
                          <div className="alert-body">
                            <strong>{alert.title}</strong>
                            <p>{alert.detail}</p>
                            <small>
                              {alert.affected_vendors.length > 0
                                ? `Vendors: ${alert.affected_vendors.join(", ")}`
                                : "Affects festival-wide dietary safety"}
                              {alert.participant_impact !== null ? ` · ${alert.participant_impact.toLocaleString("en-IN")} participants affected` : ""}
                            </small>
                          </div>
                          {alert.affected_vendors.length > 0 && (
                            <button
                              className="button-secondary"
                              onClick={() => setSelectedCode(alert.affected_vendors[0])}
                            >
                              Inspect {alert.affected_vendors[0]}
                            </button>
                          )}
                        </article>
                      ))}
                    </div>
                  </section>

                  <aside className="panel stack">
                    <SectionHead title="Allergen & Compliance Safeguards" />
                    <div className="callout">
                      <strong>Nut Allergy Protocol ({di.participant_counts["nut allergy"] ?? 0} participants)</strong>
                      <p className="muted small" style={{ marginTop: 4 }}>
                        0 of 14 vendors declare a <code>nut-free</code> option in <code>vendors.csv</code>. Never assume a meal is allergy-safe based on a menu label alone. Declared dietary options are tracked separately from verified kitchen allergen controls and cross-contamination prevention.
                      </p>
                    </div>
                    <div className="callout">
                      <strong>Certificate Boundary (30 Oct – 1 Nov 2026)</strong>
                      <p className="muted small" style={{ marginTop: 4 }}>
                        4 vendors (<code>VND01, VND02, VND04, VND07</code>) expire on <strong>2026-10-31</strong>. An expiry date of 31 October is not treated as automatically valid for every service throughout 31 Oct and 1 Nov without explicit boundary verification.
                      </p>
                    </div>
                    <div className="callout">
                      <strong>Unreported Participant Dietary Data</strong>
                      <p className="muted small" style={{ marginTop: 4 }}>
                        <strong>{di.blank_dietary_participants.toLocaleString("en-IN")}</strong> of {di.total_participants.toLocaleString("en-IN")} participants have blank dietary needs in <code>participants.csv</code> (flagged as <code>missing_dietary</code>).{" "}
                        <Link to="/participants">Inspect in Participants</Link>
                      </p>
                    </div>
                  </aside>
                </div>

                <section className="panel stack">
                  <SectionHead title="Participant Dietary Demand vs. Vendor Coverage Matrix">
                    <span className="muted small">
                      Distinguishes participant declared need vs. vendor-listed options vs. valid hygiene certificates vs. independent organizer verification.
                    </span>
                  </SectionHead>
                  <DataTable
                    rows={di.coverage_matrix}
                    rowKey={(r) => r.key}
                    empty="No dietary categories found."
                    columns={[
                      {
                        label: "Dietary / Allergen Requirement",
                        cell: (r) => <strong>{r.label}</strong>,
                      },
                      {
                        label: "Declared Participants",
                        className: "num",
                        value: (r) => r.participant_count,
                        cell: (r) => (
                          <strong>
                            {r.participant_count > 0 ? r.participant_count.toLocaleString("en-IN") : "0 (Vendor-only tag)"}
                          </strong>
                        ),
                      },
                      {
                        label: "Vendor-Listed (CSV)",
                        cell: (r) =>
                          r.listed_codes.length > 0 ? (
                            <span>
                              <strong>{r.listed_count}</strong> ({r.listed_codes.join(", ")})
                            </span>
                          ) : (
                            <Pill value="0 listed" tone="bad" />
                          ),
                      },
                      {
                        label: "Valid Beyond Festival",
                        cell: (r) =>
                          r.valid_beyond_codes.length > 0 ? (
                            <span>
                              <strong>{r.valid_beyond_count}</strong> ({r.valid_beyond_codes.join(", ")})
                            </span>
                          ) : (
                            <Pill value="0 valid beyond fest" tone="bad" />
                          ),
                      },
                      {
                        label: "Expiring 31 Oct",
                        cell: (r) =>
                          r.expiring_during_codes.length > 0 ? (
                            <span>
                              <strong>{r.expiring_during_count}</strong> ({r.expiring_during_codes.join(", ")})
                            </span>
                          ) : (
                            <span className="muted">0</span>
                          ),
                      },
                      {
                        label: "Expired Certificate (Non-Compliant)",
                        cell: (r) =>
                          r.expired_codes.length > 0 ? (
                            <Pill value={`${r.expired_count} expired (${r.expired_codes.join(", ")})`} tone="bad" />
                          ) : (
                            <span className="muted">0</span>
                          ),
                      },
                      {
                        label: "Independently Verified Safety",
                        cell: (r) =>
                          r.independently_verified_codes.length > 0 ? (
                            <Pill value={`Verified (${r.independently_verified_codes.join(", ")})`} tone="ok" />
                          ) : (
                            <span className="muted">Not verified</span>
                          ),
                      },
                      {
                        label: "Coverage Assessment",
                        cell: (r) => (
                          <div>
                            <Pill value={r.status_label} tone={r.tone} />
                            <div style={{ marginTop: 4 }}>
                              <small className="muted">{r.note}</small>
                            </div>
                          </div>
                        ),
                      },
                    ]}
                  />
                </section>

                <section className="panel stack">
                  <SectionHead title="Menu vs. Dietary Label & Hours Inconsistencies">
                    <span className="muted small">Flagged directly from vendors.csv records for organizer verification</span>
                  </SectionHead>
                  <DataTable
                    rows={di.inconsistencies}
                    rowKey={(inc) => `${inc.vendor_code}-${inc.key}`}
                    onRow={(inc) => setSelectedCode(inc.vendor_code)}
                    empty="No menu inconsistencies detected."
                    columns={[
                      { label: "Vendor", cell: (inc) => <strong>{inc.vendor_code} · {inc.vendor_name}</strong> },
                      { label: "Listed Menu", cell: (inc) => <span>{inc.menu}</span> },
                      { label: "CSV Dietary Options", cell: (inc) => <span>{inc.dietary_options.join("; ")}</span> },
                      { label: "Operating Hours", cell: (inc) => <span>{inc.hours}</span> },
                      { label: "Inconsistency Explanation", cell: (inc) => <span>{inc.detail}</span> },
                      {
                        label: "Status",
                        cell: (inc) => (
                          <Pill
                            value={inc.resolved === "true" ? "Verified by organizer" : "Requires verification"}
                            tone={inc.resolved === "true" ? "ok" : "warn"}
                          />
                        ),
                      },
                    ]}
                  />
                </section>
              </div>
            )}

            {/* SECTION D: OPERATING HOURS & CAPACITY */}
            {sectionTab === "hours" && (
              <div className="stack">
                <div className="ops-grid">
                  <section className="panel stack">
                    <SectionHead title="Critical Capacity Constraint & Unit Verification">
                      <Pill
                        value={hc.numerical_allocation_enabled ? "Units Verified" : "Capacity unit not verified."}
                        tone={hc.numerical_allocation_enabled ? "ok" : "warn"}
                      />
                    </SectionHead>
                    <div className="callout">
                      <strong>Combined Listed Capacity: {hc.total_listed_capacity.toLocaleString("en-IN")} — {hc.capacity_unit_status}</strong>
                      <p className="muted" style={{ marginTop: 6 }}>
                        <code>vendors.csv</code> supplies a numerical capacity for each vendor (totaling 3,773 across 14 vendors) without specifying a unit. FestOS does <strong>not</strong> interpret this number as meals per day, meals per hour, or people served without explicit verification. Numerical service-capacity allocation remains disabled until an authorized organizer verifies the capacity unit.
                      </p>
                    </div>
                    <Facts
                      items={[
                        ["Combined listed figure", hc.total_listed_capacity.toLocaleString("en-IN")],
                        ["Capacity unit status", <strong key="u">{hc.capacity_unit_status}</strong>],
                        ["Vendors with verified unit", `${hc.verified_unit_count} of ${ov.total_vendors}`],
                        ["Vendors missing unit verification", `${hc.unverified_unit_count} of ${ov.total_vendors}`],
                        [
                          "Numerical service-capacity allocation",
                          hc.numerical_allocation_enabled ? (
                            <Pill value="Enabled (All units verified)" tone="ok" />
                          ) : (
                            <Pill value="Disabled until capacity unit is verified" tone="bad" />
                          ),
                        ],
                      ]}
                    />
                  </section>

                  <aside className="panel stack">
                    <SectionHead title="Operating Hours Breakdown" />
                    {hc.hours_distribution.map((h) => (
                      <div key={h.hours} className="callout">
                        <div className="bar">
                          <strong>{h.hours}</strong>
                          <Pill value={`${h.count} vendors`} tone={h.hours === "07:00-23:00" ? "ok" : "warn"} />
                        </div>
                        <p className="muted small" style={{ marginTop: 4 }}>
                          Vendors: {h.vendor_codes.join(", ")}
                        </p>
                        {h.hours === "10:00-16:00" && (
                          <p className="small tone-warn" style={{ marginTop: 4 }}>
                            Note: VND04 lists "Breakfast" as its menu but operates 10:00–16:00. VND08 (Vegan, expired cert) and VND12 (Chaat counter) also operate 10:00–16:00 only.
                          </p>
                        )}
                      </div>
                    ))}
                  </aside>
                </div>

                <section className="panel stack">
                  <SectionHead title="Meal-Window Vendor Availability Matrix">
                    <span className="muted small">Inspect vendor availability and certificate compliance across Breakfast, Mid-day, Lunch, Evening, and Dinner</span>
                  </SectionHead>
                  <DataTable
                    rows={hc.meal_windows}
                    rowKey={(w) => w.key}
                    empty="No meal windows configured."
                    columns={[
                      { label: "Service Window", cell: (w) => <strong>{w.label}</strong> },
                      { label: "Hours", cell: (w) => <span>{w.window}</span> },
                      {
                        label: "Total Open Vendors",
                        className: "num",
                        cell: (w) => <strong>{w.total_vendors}</strong>,
                      },
                      {
                        label: "Valid Beyond Festival",
                        cell: (w) => (
                          <span>
                            <strong>{w.valid_beyond_codes.length}</strong> ({w.valid_beyond_codes.join(", ") || "None"})
                          </span>
                        ),
                      },
                      {
                        label: "Expiring 31 Oct (Boundary)",
                        cell: (w) => (
                          <span>
                            <strong>{w.expiring_codes.length}</strong> ({w.expiring_codes.join(", ") || "None"})
                          </span>
                        ),
                      },
                      {
                        label: "Expired Certificate (Non-Compliant)",
                        cell: (w) =>
                          w.expired_codes.length > 0 ? (
                            <Pill value={`${w.expired_codes.length} (${w.expired_codes.join(", ")})`} tone="bad" />
                          ) : (
                            <span className="muted">0</span>
                          ),
                      },
                    ]}
                  />
                </section>

                <section className="panel stack">
                  <SectionHead title="Per-Vendor Hours & Capacity Unit Verification">
                    <span className="muted small">Click any vendor to verify its capacity unit or operating window</span>
                  </SectionHead>
                  <DataTable
                    rows={data.vendors}
                    rowKey={(v) => v.code}
                    onRow={(v) => setSelectedCode(v.code)}
                    empty="No vendors."
                    columns={[
                      { label: "Vendor", cell: (v) => <strong>{v.code} · {v.name}</strong> },
                      { label: "Menu", cell: (v) => <span>{v.menu}</span> },
                      { label: "Operating Hours", cell: (v) => <strong>{v.hours}</strong> },
                      {
                        label: "Breakfast (07–10)",
                        cell: (v) => <Pill value={v.meal_windows.includes("breakfast") ? "Open" : "Closed"} tone={v.meal_windows.includes("breakfast") ? "ok" : "bad"} />,
                      },
                      {
                        label: "Lunch (12–15)",
                        cell: (v) => <Pill value={v.meal_windows.includes("lunch") ? "Open" : "Closed"} tone={v.meal_windows.includes("lunch") ? "ok" : "bad"} />,
                      },
                      {
                        label: "Evening (16–19)",
                        cell: (v) => <Pill value={v.meal_windows.includes("evening") ? "Open" : "Closed"} tone={v.meal_windows.includes("evening") ? "ok" : "bad"} />,
                      },
                      {
                        label: "Dinner (19–23)",
                        cell: (v) => <Pill value={v.meal_windows.includes("dinner") ? "Open" : "Closed"} tone={v.meal_windows.includes("dinner") ? "ok" : "bad"} />,
                      },
                      {
                        label: "Listed Capacity",
                        className: "num",
                        cell: (v) => <strong>{v.capacity ?? "—"}</strong>,
                      },
                      {
                        label: "Capacity Unit",
                        cell: (v) =>
                          v.capacity_unit ? (
                            <Pill value={`${v.capacity_unit} (Verified)`} tone="ok" />
                          ) : (
                            <Pill value="Capacity unit not verified." tone="warn" />
                          ),
                      },
                    ]}
                  />
                </section>
              </div>
            )}

            {/* SECTION E: VENDOR INTERRUPTION & RECOVERY SIMULATOR */}
            {sectionTab === "interruption" && (
              <div className="stack">
                <section className="panel stack">
                  <SectionHead title="Vendor Interruption & Recovery Simulator">
                    <span className="muted small">
                      Simulation is kept strictly separate from live vendor status changes. Demand is never automatically redirected.
                    </span>
                  </SectionHead>

                  <div className="toolbar">
                    <Field label="Select vendor to report unavailable">
                      <select
                        value={simulatingVendorCode}
                        onChange={(e) => setSimulatingVendorCode(e.target.value)}
                      >
                        {data.vendors.map((v) => (
                          <option key={v.code} value={v.code}>
                            {v.code} — {v.name} ({v.menu} · {v.dietary_options.join(", ")})
                          </option>
                        ))}
                      </select>
                    </Field>
                    <Field label="Interruption reason / scenario note">
                      <input
                        type="text"
                        placeholder="e.g. Equipment breakdown, hygiene suspension, staff shortage..."
                        value={simulationReason}
                        onChange={(e) => setSimulationReason(e.target.value)}
                        style={{ minWidth: 340 }}
                      />
                    </Field>
                    {can("vendors.write") && (
                      <button
                        className="button-primary"
                        disabled={simulateAction.isPending}
                        onClick={() => simulateAction.mutate()}
                      >
                        <ShieldAlert size={16} /> Report Vendor Unavailable (Simulate)
                      </button>
                    )}
                  </div>
                  <ErrorNote error={simulateAction.error} />
                </section>

                {(activeInterruption ?? data.interruptions[0]) && (() => {
                  const current = activeInterruption ?? data.interruptions[0];
                  return (
                    <InterruptionReviewPanel
                      base={base}
                      interruption={current}
                      canApprove={can("vendors.approve")}
                      onUpdated={(updated) => {
                        setActiveInterruption(updated);
                        queryClient.invalidateQueries();
                      }}
                    />
                  );
                })()}

                {data.interruptions.length > 0 && (
                  <section className="panel stack">
                    <SectionHead title="Recorded Interruption Simulations & Decisions" />
                    <DataTable
                      rows={data.interruptions}
                      rowKey={(i) => i.id}
                      onRow={(i) => setActiveInterruption(i)}
                      empty="No vendor interruptions recorded yet."
                      columns={[
                        { label: "ID", cell: (i) => <strong>#{i.id}</strong> },
                        { label: "Vendor", cell: (i) => <strong>{i.vendor_code}</strong> },
                        { label: "Reason", cell: (i) => <span>{i.reason}</span> },
                        {
                          label: "Status",
                          cell: (i) => (
                            <Pill
                              value={i.status}
                              tone={i.status === "approved" ? "bad" : i.status === "rejected" ? "info" : "warn"}
                            />
                          ),
                        },
                        {
                          label: "Replacement Plan",
                          cell: (i) => (
                            <span>{i.replacement_codes.length > 0 ? i.replacement_codes.join(", ") : "None assigned"}</span>
                          ),
                        },
                        { label: "Simulated By", cell: (i) => <span>{i.created_by_name}</span> },
                        { label: "Created", cell: (i) => <small>{i.created_at ? formatTime(i.created_at) : "—"}</small> },
                      ]}
                    />
                  </section>
                )}
              </div>
            )}

            {/* VENDOR DETAIL DRAWER */}
            {selectedCode && (
              <VendorDetailDrawer
                base={base}
                code={selectedCode}
                canWrite={can("vendors.write")}
                onClose={() => setSelectedCode(null)}
                onSimulateInterruption={(vcode) => {
                  setSelectedCode(null);
                  setSimulatingVendorCode(vcode);
                  setSectionTab("interruption");
                }}
              />
            )}

            {/* CREATE VENDOR MODAL */}
            {creatingVendor && (
              <CreateVendorModal
                base={base}
                onClose={() => setCreatingVendor(false)}
              />
            )}

            {/* IMPORT CSV MODAL */}
            {importingCsv && (
              <ImportVendorsModal
                base={base}
                onClose={() => setImportingCsv(false)}
              />
            )}
          </div>
        );
      })()}
    </QueryState>
  );
}

function InterruptionReviewPanel({
  base,
  interruption,
  canApprove,
  onUpdated,
}: {
  base: string;
  interruption: InterruptionRecord;
  canApprove: boolean;
  onUpdated: (res: InterruptionRecord) => void;
}) {
  const rep = interruption.simulation_report;
  const tv = rep.target_vendor;
  const [selectedReplacements, setSelectedReplacements] = useState<string[]>(interruption.replacement_codes ?? []);
  const [decisionReason, setDecisionReason] = useState("");

  const approveAction = useAction(
    () =>
      api<InterruptionRecord>(`${base}/vendors/interruptions/${interruption.id}/approve`, {
        method: "POST",
        body: JSON.stringify({
          reason: decisionReason.trim(),
          replacement_codes: selectedReplacements,
        }),
      }),
    {
      success: `Approved marking ${interruption.vendor_code} unavailable`,
      onDone: onUpdated,
    }
  );

  const rejectAction = useAction(
    () =>
      api<InterruptionRecord>(`${base}/vendors/interruptions/${interruption.id}/reject`, {
        method: "POST",
        body: JSON.stringify({
          reason: decisionReason.trim(),
          replacement_codes: [],
        }),
      }),
    {
      success: `Rejected interruption simulation #${interruption.id}`,
      onDone: onUpdated,
    }
  );

  const toggleReplacement = (code: string) => {
    setSelectedReplacements((prev) =>
      prev.includes(code) ? prev.filter((c) => c !== code) : [...prev, code]
    );
  };

  return (
    <section className="panel stack">
      <SectionHead title={`Interruption Analysis #${interruption.id}: ${tv.code} (${tv.name})`}>
        <Pill
          value={`Status: ${interruption.status}`}
          tone={interruption.status === "approved" ? "bad" : interruption.status === "rejected" ? "info" : "warn"}
        />
      </SectionHead>

      <div className="callout">
        <strong>{rep.coverage_verdict}</strong>
        <p className="muted small" style={{ marginTop: 4 }}>
          {rep.simulation_notice}
        </p>
      </div>

      <div className="ops-grid">
        <div className="stack">
          <h3>1–5. Interrupted Vendor Profile</h3>
          <Facts
            items={[
              ["Vendor", `${tv.code} · ${tv.name}`],
              ["Menu", tv.menu ?? "Unspecified"],
              ["Dietary coverage", tv.dietary_options.join(", ") || "None"],
              ["Operating hours", `${tv.hours ?? "Unknown"} (${tv.meal_windows.join(", ")})`],
              ["Certificate status", <Pill key="c" value={tv.certificate.label} tone={tv.certificate.tone} />],
              ["Listed capacity", tv.listed_capacity !== null ? String(tv.listed_capacity) : "—"],
              ["Capacity unit", tv.capacity_status],
              ["Interruption reason", interruption.reason],
            ]}
          />
        </div>

        <div className="stack">
          <h3>9. Uncovered Requirements & Risks</h3>
          {rep.uncovered_requirements.length === 0 ? (
            <p className="muted">All declared dietary options and capacity units have verified coverage.</p>
          ) : (
            <div className="alert-list">
              {rep.uncovered_requirements.map((u) => (
                <article key={u.requirement} className={`alert-row severity-${u.severity}`}>
                  <span className="severity">{u.severity}</span>
                  <div className="alert-body">
                    <strong>{u.requirement.replaceAll("_", " ")}</strong>
                    <p>{u.explanation}</p>
                  </div>
                </article>
              ))}
            </div>
          )}
        </div>
      </div>

      <h3>6–8 & 10. Compliant Alternative Vendors for Human Review ({rep.alternatives.length})</h3>
      <DataTable
        rows={rep.alternatives}
        rowKey={(c) => c.code}
        empty="No compliant alternative vendors match this vendor's dietary coverage."
        columns={[
          ...(interruption.status === "simulated" && canApprove
            ? [
                {
                  label: "Select in Plan",
                  cell: (c: InterruptionCandidate) => (
                    <input
                      type="checkbox"
                      aria-label={`Select ${c.code} as replacement`}
                      checked={selectedReplacements.includes(c.code)}
                      onChange={() => toggleReplacement(c.code)}
                    />
                  ),
                },
              ]
            : []),
          { label: "Candidate", cell: (c) => <strong>{c.code} · {c.name}</strong> },
          { label: "Menu", cell: (c) => <span>{c.menu}</span> },
          {
            label: "Certificate",
            cell: (c) => <Pill value={c.certificate.label} tone={c.certificate.tone} />,
          },
          {
            label: "Shared Dietary",
            cell: (c) => (
              <div className="chips">
                {c.shared_dietary.map((d) => (
                  <Pill key={d} value={d} tone="ok" />
                ))}
              </div>
            ),
          },
          {
            label: "Uncovered Dietary",
            cell: (c) =>
              c.missing_dietary.length > 0 ? (
                <div className="chips">
                  {c.missing_dietary.map((d) => (
                    <Pill key={d} value={`Missing: ${d}`} tone="bad" />
                  ))}
                </div>
              ) : (
                <span className="muted">Covers all</span>
              ),
          },
          {
            label: "Hours Match",
            cell: (c) =>
              c.missing_meal_windows.length === 0 ? (
                <Pill value={c.hours ?? "Full overlap"} tone="ok" />
              ) : (
                <Pill value={`${c.hours} (misses ${c.missing_meal_windows.join(", ")})`} tone="warn" />
              ),
          },
          {
            label: "Capacity Check",
            cell: (c) => <small className="muted">{c.capacity_comparison}</small>,
          },
        ]}
      />

      {rep.excluded_non_compliant.length > 0 && (
        <details>
          <summary className="muted small" style={{ cursor: "pointer" }}>
            Excluded non-compliant vendors ({rep.excluded_non_compliant.length} excluded due to expired/missing certificates or interruption)
          </summary>
          <ul className="muted small" style={{ marginTop: 8 }}>
            {rep.excluded_non_compliant.map((ex) => (
              <li key={ex.code}>
                <strong>{ex.code} ({ex.name}):</strong> {ex.reason}
              </li>
            ))}
          </ul>
        </details>
      )}

      {interruption.status === "simulated" && canApprove && (
        <div className="callout stack">
          <strong>Authorized Decision Required (vendors.approve)</strong>
          <p className="muted small">
            Approving this interruption marks <strong>{tv.code}</strong> as unavailable and records the selected replacement vendor(s) ({selectedReplacements.join(", ") || "none selected"}) in the audit log.
          </p>
          <Field label="Mandatory decision reason (written to audit log)">
            <input
              type="text"
              placeholder="Explain why this vendor is being marked unavailable or why simulation is rejected..."
              value={decisionReason}
              onChange={(e) => setDecisionReason(e.target.value)}
            />
          </Field>
          <ErrorNote error={approveAction.error || rejectAction.error} />
          <div className="toolbar">
            <button
              className="button-danger"
              disabled={decisionReason.trim().length < 3 || approveAction.isPending}
              onClick={() => approveAction.mutate()}
            >
              Approve & Mark {tv.code} Unavailable
            </button>
            <button
              className="button-ghost"
              disabled={decisionReason.trim().length < 3 || rejectAction.isPending}
              onClick={() => rejectAction.mutate()}
            >
              Reject Simulation (Keep Vendor Status Unchanged)
            </button>
          </div>
        </div>
      )}
    </section>
  );
}

function VendorDetailDrawer({
  base,
  code,
  canWrite,
  onClose,
  onSimulateInterruption,
}: {
  base: string;
  code: string;
  canWrite: boolean;
  onClose: () => void;
  onSimulateInterruption: (code: string) => void;
}) {
  const detailQuery = useQuery({
    queryKey: ["vendor-detail", base, code],
    queryFn: () => api<VendorDetailResponse>(`${base}/vendors/${code}`),
  });

  const [mode, setMode] = useState<"view" | "verify" | "edit" | "archive">("view");

  return (
    <Dialog title={`Vendor ${code}`} onClose={onClose} side>
      <QueryState query={detailQuery}>
        {detailQuery.data && (() => {
          const { vendor: v, data_issues, audit_history } = detailQuery.data;
          const pack = v.original_pack_data;

          return (
            <div className="stack">
              <div className="chips">
                <Pill value={v.certificate.label} tone={v.certificate.tone} />
                <Pill value={operationalLabel(v.operational_status)} tone={operationalTone(v.operational_status)} />
                <Pill value={v.source === "pack" ? "Source: vendors.csv" : "Source: Manual record"} tone="info" />
              </div>

              {canWrite && (
                <div className="toolbar">
                  <button
                    className={mode === "verify" ? "button-primary" : "button-secondary"}
                    onClick={() => setMode(mode === "verify" ? "view" : "verify")}
                  >
                    <ShieldCheck size={15} /> Verify Operational Info
                  </button>
                  <button
                    className={mode === "edit" ? "button-primary" : "button-secondary"}
                    onClick={() => setMode(mode === "edit" ? "view" : "edit")}
                  >
                    <Wrench size={15} /> Edit Record
                  </button>
                  <button
                    className="button-secondary"
                    onClick={() => onSimulateInterruption(v.code)}
                  >
                    <ShieldAlert size={15} /> Report Unavailable
                  </button>
                  <button
                    className="button-ghost"
                    onClick={() => setMode(mode === "archive" ? "view" : "archive")}
                  >
                    <Archive size={15} /> {v.archived_at ? "Restore" : "Archive"}
                  </button>
                </div>
              )}

              {mode === "verify" && (
                <VerifyVendorForm
                  base={base}
                  vendor={v}
                  onDone={() => setMode("view")}
                />
              )}

              {mode === "edit" && (
                <EditVendorForm
                  base={base}
                  vendor={v}
                  onDone={() => setMode("view")}
                />
              )}

              {mode === "archive" && (
                <ArchiveVendorForm
                  base={base}
                  vendor={v}
                  onDone={() => setMode("view")}
                />
              )}

              <div className="callout">
                <strong>Certificate Compliance Note:</strong> {v.certificate.boundary_note}
              </div>

              {v.inconsistencies.length > 0 && (
                <div className="alert-list">
                  {v.inconsistencies.map((inc) => (
                    <article key={inc.key} className="alert-row severity-medium">
                      <span className="severity">{inc.resolved === "true" ? "verified" : "flag"}</span>
                      <div className="alert-body">
                        <strong>{inc.title}</strong>
                        <p>{inc.detail}</p>
                      </div>
                    </article>
                  ))}
                </div>
              )}

              <h3>Current Operational Record & Verified Additions</h3>
              <Facts
                items={[
                  ["Vendor ID", v.code],
                  ["Vendor name", v.name],
                  ["Menu", v.menu],
                  ["Normalized dietary tags", v.dietary_options.join(", ") || "None"],
                  [
                    "Independently verified dietary",
                    v.verified_dietary_options ? v.verified_dietary_options.join(", ") : <span className="unknown">Not verified</span>,
                  ],
                  [
                    "Nut-free & allergen controls",
                    v.nut_free_verified && v.allergen_controls_verified
                      ? "Verified nut-free & allergen controls"
                      : "Not verified (Do not treat as nut-allergy safe)",
                  ],
                  ["Cross-contamination note", v.cross_contamination_note ?? <span className="unknown">None recorded</span>],
                  ["Operating hours", v.hours],
                  ["Listed capacity", v.capacity !== null ? String(v.capacity) : null],
                  ["Capacity unit verification", v.capacity_unit_display],
                  ["Payment terms", v.payment_terms],
                  ["Verified contact name", v.contact_name ?? <span className="unknown">Not provided in CSV</span>],
                  ["Verified contact phone", v.contact_phone ?? <span className="unknown">Not provided in CSV</span>],
                  ["Verification notes", v.verification_notes ?? <span className="unknown">None</span>],
                  [
                    "Last verified by",
                    v.verified_by ? `${v.verified_by} (${v.verified_at ? formatTime(v.verified_at) : ""})` : <span className="unknown">Never</span>,
                  ],
                ]}
              />

              {pack && (
                <>
                  <h3>Original Imported CSV Record (Immutable Provenance)</h3>
                  <Facts
                    items={[
                      ["vendor_id", pack.vendor_id],
                      ["vendor_name", pack.vendor_name],
                      ["menu", pack.menu],
                      ["dietary_options", pack.dietary_options],
                      ["hygiene_certificate_expiry", pack.hygiene_certificate_expiry],
                      ["hours", pack.hours],
                      ["capacity", pack.capacity],
                      ["payment_terms", pack.payment_terms],
                    ]}
                  />
                </>
              )}

              {data_issues.length > 0 && (
                <>
                  <h3>Pack Import Flags ({data_issues.length})</h3>
                  <ul className="muted small">
                    {data_issues.map((di) => (
                      <li key={di.id}>
                        <strong>{di.issue}:</strong> {di.detail || "Flagged during pack import"}
                      </li>
                    ))}
                  </ul>
                </>
              )}

              <h3>Audit History ({audit_history.length})</h3>
              {audit_history.length === 0 ? (
                <p className="muted small">No manual changes recorded since initial data pack import.</p>
              ) : (
                <div className="stack">
                  {audit_history.map((a) => (
                    <div key={a.id} className="callout">
                      <div className="bar">
                        <strong>{a.summary}</strong>
                        <small className="muted">{a.created_at ? formatTime(a.created_at) : ""}</small>
                      </div>
                      {a.reason && <p className="small" style={{ marginTop: 4 }}>Reason: {a.reason}</p>}
                    </div>
                  ))}
                </div>
              )}
            </div>
          );
        })()}
      </QueryState>
    </Dialog>
  );
}

function VerifyVendorForm({ base, vendor, onDone }: { base: string; vendor: VendorRecord; onDone: () => void }) {
  const [operationalStatus, setOperationalStatus] = useState<string>(vendor.operational_status);
  const [capacityUnit, setCapacityUnit] = useState<string>(vendor.capacity_unit ?? "");
  const [verifiedDietary, setVerifiedDietary] = useState<string>(
    vendor.verified_dietary_options ? vendor.verified_dietary_options.join("; ") : vendor.dietary_options.join("; ")
  );
  const [nutFreeVerified, setNutFreeVerified] = useState<boolean>(vendor.nut_free_verified);
  const [allergenVerified, setAllergenVerified] = useState<boolean>(vendor.allergen_controls_verified);
  const [crossContamNote, setCrossContamNote] = useState<string>(vendor.cross_contamination_note ?? "");
  const [certVerified, setCertVerified] = useState<boolean>(Boolean(vendor.certificate.verified_by));
  const [certNote, setCertNote] = useState<string>(vendor.certificate.verification_note ?? "");
  const [contactName, setContactName] = useState<string>(vendor.contact_name ?? "");
  const [contactPhone, setContactPhone] = useState<string>(vendor.contact_phone ?? "");
  const [notes, setNotes] = useState<string>(vendor.verification_notes ?? "");
  const [reason, setReason] = useState<string>("");

  const save = useAction(
    () =>
      api(`${base}/vendors/${vendor.code}/verify`, {
        method: "POST",
        body: JSON.stringify({
          operational_status: operationalStatus,
          capacity_unit: capacityUnit.trim() || "",
          clear_capacity_unit: !capacityUnit.trim(),
          verified_dietary_options: verifiedDietary
            .split(/[;,]/)
            .map((s) => s.trim())
            .filter(Boolean),
          nut_free_verified: nutFreeVerified,
          allergen_controls_verified: allergenVerified,
          cross_contamination_note: crossContamNote,
          certificate_verified: certVerified,
          certificate_verification_note: certNote,
          contact_name: contactName,
          contact_phone: contactPhone,
          verification_notes: notes,
          reason: reason.trim(),
        }),
      }),
    {
      success: `Verified operational information saved for ${vendor.code}`,
      onDone,
    }
  );

  const submit = (e: FormEvent) => {
    e.preventDefault();
    save.mutate();
  };

  return (
    <form className="form callout" onSubmit={submit}>
      <h3>Update Manually Verified Information</h3>
      <Field label="Operational availability">
        <select value={operationalStatus} onChange={(e) => setOperationalStatus(e.target.value)}>
          <option value="unconfirmed">Unconfirmed (CSV default)</option>
          <option value="confirmed_active">Confirmed Active</option>
          <option value="interrupted">Interrupted / Unavailable</option>
        </select>
      </Field>

      <Field label="Verified capacity unit" hint="Leave blank to keep 'Capacity unit not verified.'">
        <input
          type="text"
          placeholder="e.g. meals/day, servings/hour, people/service"
          value={capacityUnit}
          onChange={(e) => setCapacityUnit(e.target.value)}
        />
      </Field>

      <Field label="Independently verified dietary options (semicolon-separated)">
        <input
          type="text"
          value={verifiedDietary}
          onChange={(e) => setVerifiedDietary(e.target.value)}
        />
      </Field>

      <label className="check-inline">
        <input type="checkbox" checked={nutFreeVerified} onChange={(e) => setNutFreeVerified(e.target.checked)} />
        <span>Nut-free preparation verified</span>
      </label>

      <label className="check-inline">
        <input type="checkbox" checked={allergenVerified} onChange={(e) => setAllergenVerified(e.target.checked)} />
        <span>Kitchen allergen controls & cross-contamination prevention verified</span>
      </label>

      <Field label="Cross-contamination / allergen note">
        <input
          type="text"
          placeholder="Document kitchen separation or allergen handling..."
          value={crossContamNote}
          onChange={(e) => setCrossContamNote(e.target.value)}
        />
      </Field>

      <label className="check-inline">
        <input type="checkbox" checked={certVerified} onChange={(e) => setCertVerified(e.target.checked)} />
        <span>Hygiene certificate renewal / Oct 31 boundary verified</span>
      </label>

      <Field label="Certificate verification note">
        <input
          type="text"
          placeholder="Reference number or inspector verification note..."
          value={certNote}
          onChange={(e) => setCertNote(e.target.value)}
        />
      </Field>

      <Field label="On-ground contact name (optional manual addition)">
        <input type="text" value={contactName} onChange={(e) => setContactName(e.target.value)} />
      </Field>

      <Field label="On-ground contact phone (optional manual addition)">
        <input type="text" value={contactPhone} onChange={(e) => setContactPhone(e.target.value)} />
      </Field>

      <Field label="General verification notes">
        <textarea rows={2} value={notes} onChange={(e) => setNotes(e.target.value)} />
      </Field>

      <Field label="Mandatory audit reason">
        <input
          type="text"
          required
          minLength={3}
          placeholder="Why are these verified fields being updated?"
          value={reason}
          onChange={(e) => setReason(e.target.value)}
        />
      </Field>

      <ErrorNote error={save.error} />
      <div className="toolbar">
        <button className="button-primary" disabled={save.isPending}>
          Save Verified Information
        </button>
        <button type="button" className="button-ghost" onClick={onDone}>
          Cancel
        </button>
      </div>
    </form>
  );
}

function EditVendorForm({ base, vendor, onDone }: { base: string; vendor: VendorRecord; onDone: () => void }) {
  const [name, setName] = useState(vendor.name);
  const [menu, setMenu] = useState(vendor.menu ?? "");
  const [dietary, setDietary] = useState(vendor.dietary_options.join("; "));
  const [expiry, setExpiry] = useState(vendor.hygiene_certificate_expiry ?? "");
  const [hours, setHours] = useState(vendor.hours ?? "");
  const [capacity, setCapacity] = useState(vendor.capacity !== null ? String(vendor.capacity) : "");
  const [paymentTerms, setPaymentTerms] = useState(vendor.payment_terms ?? "");
  const [reason, setReason] = useState("");

  const update = useAction(
    () =>
      api(`${base}/vendors/${vendor.code}`, {
        method: "PATCH",
        body: JSON.stringify({
          name: name.trim(),
          menu: menu.trim() || null,
          dietary_options: dietary
            .split(/[;,]/)
            .map((s) => s.trim())
            .filter(Boolean),
          hygiene_certificate_expiry: expiry || null,
          hours: hours.trim() || null,
          capacity: capacity ? Number(capacity) : null,
          payment_terms: paymentTerms.trim() || null,
          reason: reason.trim(),
        }),
      }),
    {
      success: `Updated vendor ${vendor.code} (original CSV provenance preserved)`,
      onDone,
    }
  );

  return (
    <form
      className="form callout"
      onSubmit={(e) => {
        e.preventDefault();
        update.mutate();
      }}
    >
      <h3>Edit Operational Vendor Record</h3>
      <p className="muted small">Original imported CSV values remain preserved in original_pack_data.</p>
      <Field label="Vendor name">
        <input required value={name} onChange={(e) => setName(e.target.value)} />
      </Field>
      <Field label="Menu">
        <input value={menu} onChange={(e) => setMenu(e.target.value)} />
      </Field>
      <Field label="Dietary options (semicolon-separated)">
        <input value={dietary} onChange={(e) => setDietary(e.target.value)} />
      </Field>
      <Field label="Hygiene certificate expiry">
        <input type="date" value={expiry} onChange={(e) => setExpiry(e.target.value)} />
      </Field>
      <Field label="Operating hours">
        <input placeholder="e.g. 07:00-23:00" value={hours} onChange={(e) => setHours(e.target.value)} />
      </Field>
      <Field label="Listed capacity">
        <input type="number" min={0} value={capacity} onChange={(e) => setCapacity(e.target.value)} />
      </Field>
      <Field label="Payment terms">
        <input value={paymentTerms} onChange={(e) => setPaymentTerms(e.target.value)} />
      </Field>
      <Field label="Mandatory audit reason">
        <input
          required
          minLength={3}
          placeholder="Reason for updating vendor record..."
          value={reason}
          onChange={(e) => setReason(e.target.value)}
        />
      </Field>
      <ErrorNote error={update.error} />
      <div className="toolbar">
        <button className="button-primary" disabled={update.isPending}>
          Save Changes
        </button>
        <button type="button" className="button-ghost" onClick={onDone}>
          Cancel
        </button>
      </div>
    </form>
  );
}

function ArchiveVendorForm({ base, vendor, onDone }: { base: string; vendor: VendorRecord; onDone: () => void }) {
  const [reason, setReason] = useState("");
  const isArchived = Boolean(vendor.archived_at);
  const action = useAction(
    () =>
      api(`${base}/vendors/${vendor.code}/${isArchived ? "restore" : "archive"}`, {
        method: "POST",
        body: JSON.stringify({ reason: reason.trim() }),
      }),
    {
      success: `${isArchived ? "Restored" : "Archived"} vendor ${vendor.code}`,
      onDone,
    }
  );

  return (
    <form
      className="form callout"
      onSubmit={(e) => {
        e.preventDefault();
        action.mutate();
      }}
    >
      <h3>{isArchived ? "Restore Vendor" : "Archive Vendor"}</h3>
      <Field label="Mandatory audit reason">
        <input
          required
          minLength={3}
          placeholder={`Reason for ${isArchived ? "restoring" : "archiving"} ${vendor.code}...`}
          value={reason}
          onChange={(e) => setReason(e.target.value)}
        />
      </Field>
      <ErrorNote error={action.error} />
      <div className="toolbar">
        <button className={isArchived ? "button-primary" : "button-danger"} disabled={action.isPending}>
          {isArchived ? "Restore Vendor" : "Confirm Archive"}
        </button>
        <button type="button" className="button-ghost" onClick={onDone}>
          Cancel
        </button>
      </div>
    </form>
  );
}

function CreateVendorModal({ base, onClose }: { base: string; onClose: () => void }) {
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [menu, setMenu] = useState("");
  const [dietary, setDietary] = useState("vegetarian");
  const [expiry, setExpiry] = useState("");
  const [hours, setHours] = useState("07:00-23:00");
  const [capacity, setCapacity] = useState("");
  const [paymentTerms, setPaymentTerms] = useState("");
  const [reason, setReason] = useState("");

  const create = useAction(
    () =>
      api(`${base}/vendors`, {
        method: "POST",
        body: JSON.stringify({
          code: code.trim() || undefined,
          name: name.trim(),
          menu: menu.trim() || null,
          dietary_options: dietary
            .split(/[;,]/)
            .map((s) => s.trim())
            .filter(Boolean),
          hygiene_certificate_expiry: expiry || null,
          hours: hours.trim() || null,
          capacity: capacity ? Number(capacity) : null,
          payment_terms: paymentTerms.trim() || null,
          reason: reason.trim(),
        }),
      }),
    {
      success: "Created food vendor record",
      onDone: onClose,
    }
  );

  return (
    <Dialog title="Create Food Vendor" onClose={onClose}>
      <form
        className="form"
        onSubmit={(e) => {
          e.preventDefault();
          create.mutate();
        }}
      >
        <Field label="Vendor ID (optional, auto-generates VNDxx if blank)">
          <input placeholder="e.g. VND15" value={code} onChange={(e) => setCode(e.target.value)} />
        </Field>
        <Field label="Vendor name">
          <input required minLength={2} value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
        <Field label="Menu">
          <input placeholder="e.g. Jain & Satvik meals" value={menu} onChange={(e) => setMenu(e.target.value)} />
        </Field>
        <Field label="Dietary options (semicolon-separated)">
          <input placeholder="vegetarian; jain" value={dietary} onChange={(e) => setDietary(e.target.value)} />
        </Field>
        <Field label="Hygiene certificate expiry">
          <input type="date" value={expiry} onChange={(e) => setExpiry(e.target.value)} />
        </Field>
        <Field label="Operating hours">
          <input placeholder="07:00-23:00" value={hours} onChange={(e) => setHours(e.target.value)} />
        </Field>
        <Field label="Listed capacity (unit must be verified separately)">
          <input type="number" min={0} value={capacity} onChange={(e) => setCapacity(e.target.value)} />
        </Field>
        <Field label="Payment terms">
          <input placeholder="e.g. 50% advance" value={paymentTerms} onChange={(e) => setPaymentTerms(e.target.value)} />
        </Field>
        <Field label="Mandatory audit reason">
          <input
            required
            minLength={3}
            placeholder="Why is this vendor being added?"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
          />
        </Field>
        <ErrorNote error={create.error} />
        <button className="button-primary" disabled={create.isPending}>
          Create Vendor
        </button>
      </form>
    </Dialog>
  );
}

function ImportVendorsModal({ base, onClose }: { base: string; onClose: () => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [result, setResult] = useState<{ created: number; skipped_duplicates: string[]; rejected: { line: number; reason: string }[] } | null>(null);
  const queryClient = useQueryClient();

  const upload = useAction(
    async () => {
      if (!file) throw new Error("Choose a CSV file first");
      const form = new FormData();
      form.append("file", file);
      return api<{ created: number; skipped_duplicates: string[]; rejected: { line: number; reason: string }[] }>(
        `${base}/vendors/import`,
        { method: "POST", body: form }
      );
    },
    {
      onDone: (res) => {
        setResult(res);
        queryClient.invalidateQueries();
        notify(`Imported ${res.created} new vendors (${res.skipped_duplicates.length} existing IDs preserved without duplication)`);
      },
    }
  );

  return (
    <Dialog title="Import Food Vendors CSV" onClose={onClose}>
      <div className="stack">
        <p className="muted small">
          Preserves original <code>vendor_id</code> values and prevents duplicate vendor records during CSV import.
        </p>
        <Field label="Select vendors.csv file">
          <input type="file" accept=".csv,text/csv" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
        </Field>
        <ErrorNote error={upload.error} />
        <button
          className="button-primary"
          disabled={!file || upload.isPending}
          onClick={() => upload.mutate()}
        >
          Import CSV
        </button>
        {result && (
          <div className="callout">
            <strong>Import Summary</strong>
            <p className="small">Created: {result.created}</p>
            <p className="small">Skipped existing IDs (no duplicates created): {result.skipped_duplicates.join(", ") || "None"}</p>
            {result.rejected.length > 0 && (
              <p className="small tone-bad">Rejected rows: {result.rejected.length}</p>
            )}
          </div>
        )}
      </div>
    </Dialog>
  );
}

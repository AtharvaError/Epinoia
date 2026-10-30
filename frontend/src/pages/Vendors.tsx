import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Archive, FileSpreadsheet, Plus, ShieldAlert, ShieldCheck, Wrench } from "lucide-react";
import { type FormEvent, useMemo, useState } from "react";
import { Link } from "react-router-dom";

import {
  type Column, DataTable, Dialog, ErrorNote, Facts, Field,
  notify, PageHead, Pill, QueryState, Tabs, useAction, useDebounced, useFest,
} from "../components/ui";
import { api } from "../lib/api";
import { formatNumber, formatTime } from "../lib/format";

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

interface CoverageDay { day: string; state: "covered" | "boundary" | "gap"; clear: string[]; boundary: string[] }

interface FixFirstItem { id: string; severity: string; action: string; why: string; people: number | null; vendors: string[] }

interface DietaryMatrixRow {
  days: CoverageDay[];
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
  fix_first: FixFirstItem[];
  dietary_intelligence: {
    service_days: string[];
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
    service_grid: { day: string; windows: { key: string; clear: string[]; boundary: string[] }[] }[];
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

const CERTIFICATE_SHORT: Record<CertificateInfo["status"], string> = {
  expired: "Expired", expires_during_festival: "Expires during fest", valid_beyond_festival: "Valid", missing: "Missing",
};
const DAY_STATE: Record<CoverageDay["state"], { label: string; className: string }> = {
  covered: { label: "Covered", className: "day-ok" }, boundary: { label: "Check expiry", className: "day-warn" }, gap: { label: "No vendor", className: "day-bad" },
};
const TIMELINE = { start: 7, hours: 16 };

function shortDate(iso: string) {
  return new Date(`${iso}T00:00:00Z`).toLocaleDateString("en-IN", { day: "numeric", month: "short", timeZone: "UTC" });
}

function openHours(hours: string | null) {
  const match = hours?.match(/^(\d{1,2}):\d{2}-(\d{1,2}):\d{2}$/);
  return match ? { start: Number(match[1]), end: Number(match[2]) } : null;
}

const directoryColumns: Column<VendorRecord>[] = [
  { label: "ID", value: (v) => v.code, cell: (v) => <span className="mono">{v.code}</span> },
  {
    label: "Vendor", value: (v) => v.name,
    cell: (v) => <><strong>{v.name}</strong><small className="cell-note">{v.operational_status === "unconfirmed" ? "Unconfirmed" : operationalLabel(v.operational_status)}{v.source === "pack" ? "" : " · Added by hand"}</small></>,
  },
  {
    label: "Menu", value: (v) => v.menu ?? "",
    cell: (v) => <>{v.menu ?? <span className="unknown">Not stated</span>}{v.inconsistencies.length > 0 && <small className="cell-note warn-text">Label mismatch</small>}</>,
  },
  {
    label: "Diets", cell: (v) => (
      <>
        {v.dietary_options.join(", ") || <span className="unknown">None listed</span>}
        {v.verified_dietary_options && <small className="cell-note ok-text">Verified: {v.verified_dietary_options.join(", ") || "none"}</small>}
        {v.nut_free_verified && v.allergen_controls_verified && <small className="cell-note ok-text">Nut-free verified</small>}
      </>
    ),
  },
  {
    label: "Certificate", className: "nowrap", value: (v) => v.certificate.status,
    cell: (v) => <><Pill value={CERTIFICATE_SHORT[v.certificate.status]} tone={v.certificate.tone} />{v.certificate.verified_by && <small className="cell-note">Checked by {v.certificate.verified_by}</small>}</>,
  },
  { label: "Expires", className: "nowrap", value: (v) => v.hygiene_certificate_expiry ?? "", cell: (v) => (v.hygiene_certificate_expiry ? shortDate(v.hygiene_certificate_expiry) : <span className="unknown">Missing</span>) },
  { label: "Hours", className: "nowrap", value: (v) => v.hours ?? "", cell: (v) => <>{v.hours ?? <span className="unknown">Unknown</span>}<small className="cell-note">{v.meal_windows.length} of 5 meals</small></> },
  { label: "Capacity", className: "num", value: (v) => v.capacity ?? 0, cell: (v) => (v.capacity === null ? <span className="unknown">Unknown</span> : formatNumber(v.capacity)) },
  { label: "Unit", className: "nowrap", value: (v) => v.capacity_unit ?? "", cell: (v) => (v.capacity_unit ? <span className="ok-text">{v.capacity_unit}</span> : <span className="warn-text">Not verified</span>) },
  { label: "Payment", value: (v) => v.payment_terms ?? "", cell: (v) => v.payment_terms ?? <span className="unknown">Not stated</span> },
];

function HoursTimeline({ vendors, windows, onOpen }: { vendors: VendorRecord[]; windows: MealWindowSummary[]; onOpen: (code: string) => void }) {
  const place = (start: number, end: number) => ({ left: `${((start - TIMELINE.start) * 100) / TIMELINE.hours}%`, width: `${((end - start) * 100) / TIMELINE.hours}%` });
  return (
    <div className="hours-chart">
      <div className="hours-row hours-axis">
        <span>Vendor</span>
        <div className="hours-track">{windows.map((w) => <i key={w.key} style={place(w.start, w.end)} title={w.window}>{w.label}</i>)}</div>
      </div>
      {vendors.map((v) => {
        const open = openHours(v.hours);
        return (
          <button key={v.code} className="hours-row" onClick={() => onOpen(v.code)}>
            <span><strong>{v.code}</strong> {v.name}</span>
            <div className="hours-track">
              {windows.map((w) => <i key={w.key} className="hours-band" style={place(w.start, w.end)} aria-hidden="true" />)}
              {open ? <b className={`hours-bar cert-${v.certificate.status}`} style={place(open.start, open.end)}>{v.hours} · {CERTIFICATE_SHORT[v.certificate.status]}</b> : <b className="hours-unknown">Hours not stated</b>}
            </div>
          </button>
        );
      })}
    </div>
  );
}

export function Vendors() {
  const { base, can } = useFest();
  const queryClient = useQueryClient();

  const [sectionTab, setSectionTab] = useState<"directory" | "compliance" | "hours" | "interruption">("directory");
  const [filterBy, setFilterBy] = useState("all");
  const [dietaryFilter, setDietaryFilter] = useState("");
  const [hoursFilter, setHoursFilter] = useState("");
  const [includeArchived, setIncludeArchived] = useState(false);
  const [search, setSearch] = useState("");
  const debouncedSearch = useDebounced(search, 200);

  const [selectedCode, setSelectedCode] = useState<string | null>(null);
  const [creatingVendor, setCreatingVendor] = useState(false);
  const [importingCsv, setImportingCsv] = useState(false);
  const [simulatingVendorCode, setSimulatingVendorCode] = useState("");
  const [simulationReason, setSimulationReason] = useState("");
  const [activeInterruption, setActiveInterruption] = useState<InterruptionRecord | null>(null);

  const intelligence = useQuery({
    queryKey: ["vendors-intelligence", base],
    queryFn: () => api<VendorsIntelligence>(`${base}/vendors/intelligence`),
  });
  // The summary already carries every live vendor, so only archived records need a second request.
  const archived = useQuery({
    queryKey: ["vendors-list", base],
    queryFn: () => api<{ items: VendorRecord[]; total: number }>(`${base}/vendors?include_archived=true`),
    enabled: includeArchived,
  });
  const directory = useMemo(() => (includeArchived ? archived.data?.items : intelligence.data?.vendors) ?? [], [includeArchived, archived.data, intelligence.data]);

  const filteredVendors = useMemo(() => directory.filter((v) => {
    if (filterBy === "valid" && v.certificate.status !== "valid_beyond_festival") return false;
    if (filterBy === "expired" && v.certificate.status !== "expired") return false;
    if (filterBy === "expiring" && v.certificate.status !== "expires_during_festival") return false;
    if (filterBy === "attention" && !v.requires_attention) return false;
    if (dietaryFilter) {
      const hasTag = v.effective_dietary_options.some((t) => t.toLowerCase() === dietaryFilter) || (dietaryFilter === "nut-free" && v.nut_free_verified);
      if (!hasTag) return false;
    }
    if (hoursFilter && v.hours !== hoursFilter) return false;
    if (debouncedSearch) {
      const q = debouncedSearch.toLowerCase();
      return [v.code, v.name, v.menu ?? "", v.payment_terms ?? "", ...v.effective_dietary_options].some((text) => text.toLowerCase().includes(q));
    }
    return true;
  }), [directory, filterBy, dietaryFilter, hoursFilter, debouncedSearch]);

  const simulateAction = useAction(
    (code: string) =>
      api<InterruptionRecord>(`${base}/vendors/${code}/interruptions/simulate`, {
        method: "POST",
        body: JSON.stringify({ reason: simulationReason.trim() || `Operational interruption check for ${code}` }),
      }),
    {
      success: (res) => `Interruption simulated for ${res.vendor_code}`,
      onDone: (res) => {
        setActiveInterruption(res);
        setSimulationReason("");
      },
    }
  );

  return (
    <div className="vendors-page">
      <PageHead title="Vendors" subtitle="Food service for the festival, using only what vendors.csv and manual checks can prove">
        {can("vendors.write") && (
          <>
            <button className="button-ghost" onClick={() => setImportingCsv(true)}><FileSpreadsheet size={16} /> Import CSV</button>
            <button className="button-ghost" onClick={() => setCreatingVendor(true)}><Plus size={16} /> Add vendor</button>
            <button className="button-primary small" onClick={() => setSectionTab("interruption")}><ShieldAlert size={16} /> Report Vendor Unavailable</button>
          </>
        )}
      </PageHead>

      <QueryState query={intelligence}>
        {intelligence.data && (() => {
          const data = intelligence.data;
          const ov = data.overview;
          const hc = data.hours_and_capacity;
          const di = data.dietary_intelligence;
          const days = di.service_days;
          const gaps = di.coverage_matrix.filter((r) => r.tone !== "ok");
          const hourOptions = [...new Set(data.vendors.map((v) => v.hours).filter(Boolean))] as string[];
          const nut = di.coverage_matrix.find((r) => r.key === "nut-free");
          const target = simulatingVendorCode || data.vendors[0]?.code;
          const current = activeInterruption ?? data.interruptions[0];
          const showDirectory = (filter: string) => {
            setSectionTab("directory");
            setFilterBy(filter);
          };
          const counts: Record<string, number> = {
            all: directory.length, valid: directory.filter((v) => v.certificate.status === "valid_beyond_festival").length, expired: directory.filter((v) => v.certificate.status === "expired").length,
            expiring: directory.filter((v) => v.certificate.status === "expires_during_festival").length, attention: directory.filter((v) => v.requires_attention).length,
          };
          const tiles: { key: string; label: string; value: number; note: string; tone?: string; onClick: () => void; active: boolean }[] = [
            { key: "all", label: "Food vendors", value: ov.total_vendors, note: `${ov.confirmed_active_count} confirmed active`, onClick: () => showDirectory("all"), active: sectionTab === "directory" && filterBy === "all" },
            { key: "expired", label: "Certificates expired", value: ov.certificates_expired, note: ov.expired_vendor_codes.join(", ") || "None", tone: "bad", onClick: () => showDirectory("expired"), active: sectionTab === "directory" && filterBy === "expired" },
            { key: "expiring", label: "Expiring during fest", value: ov.certificates_expiring_oct31, note: ov.expiring_vendor_codes.join(", ") || "None", tone: "warn", onClick: () => showDirectory("expiring"), active: sectionTab === "directory" && filterBy === "expiring" },
            { key: "valid", label: "Valid beyond fest", value: ov.certificates_valid_beyond, note: ov.valid_beyond_vendor_codes.join(", ") || "None", tone: "ok", onClick: () => showDirectory("valid"), active: sectionTab === "directory" && filterBy === "valid" },
            { key: "gaps", label: "Dietary gaps", value: ov.dietary_coverage_gaps, note: gaps.map((g) => g.label.split(" /")[0]).join(", ") || "None", tone: "bad", onClick: () => setSectionTab("compliance"), active: sectionTab === "compliance" },
            { key: "attention", label: "Need verification", value: ov.vendors_requiring_verification, note: "Capacity, status or labels", tone: "warn", onClick: () => showDirectory("attention"), active: sectionTab === "directory" && filterBy === "attention" },
          ];

          return (
            <>
              <section className="vendor-tiles" aria-label="Vendor overview">
                {tiles.map((t) => (
                  <button key={t.key} type="button" className={`vendor-tile ${t.tone ? `tile-${t.tone}` : ""}`} aria-pressed={t.active} onClick={t.onClick}>
                    <span>{t.label}</span><strong>{t.value}</strong><small>{t.note}</small>
                  </button>
                ))}
              </section>

              {data.fix_first.length > 0 && (
                <section className="panel fix-first" aria-label="Do these first">
                  <header><h2>Do these first</h2><span className="muted small">Biggest gap first. Each one disappears when it is done.</span></header>
                  <ol>
                    {data.fix_first.slice(0, 4).map((item) => (
                      <li key={item.id}>
                        <div><strong>{item.action}</strong><small>{item.why}</small></div>
                        <span className="fix-people">{item.people ? `${formatNumber(item.people)} people` : ""}</span>
                        <div className="fix-open">
                          {item.vendors.slice(0, 3).map((code) => <button key={code} className="button-ghost" onClick={() => setSelectedCode(code)}>{code}</button>)}
                          {item.vendors.length > 3 && <span className="muted small">+{item.vendors.length - 3}</span>}
                        </div>
                      </li>
                    ))}
                  </ol>
                </section>
              )}

              <Tabs
                value={sectionTab}
                onChange={setSectionTab}
                options={[
                  ["directory", "Directory"],
                  ["compliance", `Compliance and diet (${data.alerts.length})`],
                  ["hours", "Hours and capacity"],
                  ["interruption", `Interruptions${data.interruptions.length ? ` (${data.interruptions.length})` : ""}`],
                ]}
              />

              {sectionTab === "directory" && (
                <section aria-label="Vendor directory">
                  <div className="filters">
                    <input type="search" aria-label="Search vendors" placeholder="Search name, ID, menu or diet" value={search} onChange={(e) => setSearch(e.target.value)} />
                    {([["all", "All"], ["valid", "Valid"], ["expired", "Expired"], ["expiring", "Expiring"], ["attention", "Requires attention"]] as const).map(([key, label]) => (
                      <button key={key} type="button" className={filterBy === key ? "chip active" : "chip"} onClick={() => setFilterBy(key)}>{label} {counts[key]}</button>
                    ))}
                    <select aria-label="Dietary coverage" value={dietaryFilter} onChange={(e) => setDietaryFilter(e.target.value)}>
                      <option value="">Any diet</option>
                      {di.coverage_matrix.map((r) => <option key={r.key} value={r.key}>{r.label}</option>)}
                    </select>
                    <select aria-label="Operating hours" value={hoursFilter} onChange={(e) => setHoursFilter(e.target.value)}>
                      <option value="">Any hours</option>
                      {hourOptions.map((h) => <option key={h}>{h}</option>)}
                    </select>
                    <label className="check"><input type="checkbox" checked={includeArchived} onChange={(e) => setIncludeArchived(e.target.checked)} /> Archived</label>
                  </div>
                  <QueryState query={includeArchived ? archived : intelligence}>
                    <DataTable rows={filteredVendors} columns={directoryColumns} rowKey={(v) => v.code} onRow={(v) => setSelectedCode(v.code)} empty="No vendors match these filters." />
                  </QueryState>
                  <p className="muted small vendor-foot">
                    {data.procurement_scope_note.message} <Link className="link" to="/venues">Inventory</Link> · <Link className="link" to="/finance">Finance</Link>
                  </p>
                </section>
              )}

              {sectionTab === "compliance" && (
                <>
                  <section className="panel">
                    <header><h2>Who can feed whom, day by day</h2><span className="muted small">Declared need, listed option and verified safety are kept separate</span></header>
                    <div className="table-wrap flat">
                      <table className="coverage-table">
                        <thead>
                          <tr>
                            <th scope="col">Need</th>
                            <th scope="col" className="num">People</th>
                            <th scope="col">Listed by</th>
                            {days.map((d) => <th key={d} scope="col">{shortDate(d)}</th>)}
                            <th scope="col">Verified safe</th>
                          </tr>
                        </thead>
                        <tbody>
                          {di.coverage_matrix.map((r) => (
                            <tr key={r.key}>
                              <th scope="row">{r.label}</th>
                              <td className="num">{r.participant_count ? formatNumber(r.participant_count) : <span className="muted">None declared</span>}</td>
                              <td>{r.listed_codes.join(", ") || <span className="over-text">Nobody</span>}</td>
                              {r.days.map((d) => (
                                <td key={d.day} className={DAY_STATE[d.state].className} title={[...d.clear, ...d.boundary].join(", ") || "No compliant vendor"}>
                                  {DAY_STATE[d.state].label}<small>{d.clear.length + d.boundary.length ? `${d.clear.length + d.boundary.length} vendor${d.clear.length + d.boundary.length === 1 ? "" : "s"}` : ""}</small>
                                </td>
                              ))}
                              <td>{r.independently_verified_codes.join(", ") || <span className="muted">Not verified</span>}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                    <p className="panel-note vendor-foot">
                      A certificate ending on a day is shown as Check expiry for that day and does not count after it. {formatNumber(di.blank_dietary_participants)} of {formatNumber(di.total_participants)} participants left diet blank, so they are unknown, not covered. <Link className="link" to="/participants">Review participants</Link>
                    </p>
                  </section>

                  <div className="vendor-split">
                    <section className="panel">
                      <header><h2>Alerts</h2><span className="muted small">Calculated from the current records</span></header>
                      <ul className="vendor-alerts">
                        {data.alerts.map((alert) => (
                          <li key={alert.id} className={`severity-${alert.severity}`}>
                            <span className="severity">{alert.severity}</span>
                            <div>
                              <strong>{alert.title}</strong>
                              <small>{alert.participant_impact !== null ? `${formatNumber(alert.participant_impact)} people. ` : ""}{alert.affected_vendors.join(", ")}</small>
                            </div>
                            {alert.affected_vendors.length > 0 && <button className="button-ghost" onClick={() => setSelectedCode(alert.affected_vendors[0])}>Open {alert.affected_vendors[0]}</button>}
                          </li>
                        ))}
                      </ul>
                    </section>
                    <section className="panel">
                      <header><h2>Menu and label mismatches</h2><span className="muted small">{di.inconsistencies.length}</span></header>
                      {di.inconsistencies.length ? (
                        <ul className="stack">
                          {di.inconsistencies.map((inc) => (
                            <li key={`${inc.vendor_code}-${inc.key}`}>
                              <div><button className="link" onClick={() => setSelectedCode(inc.vendor_code)}>{inc.vendor_code}</button> {inc.menu}<br /><small className="muted">Lists {inc.dietary_options.join(", ")}. Open {inc.hours}.</small></div>
                              <Pill value={inc.resolved === "true" ? "Verified" : "Needs check"} tone={inc.resolved === "true" ? "ok" : "warn"} />
                            </li>
                          ))}
                        </ul>
                      ) : <p className="muted small">Every menu matches its labels.</p>}
                      {nut && <p className="panel-note vendor-foot">A menu label never proves a meal is safe. Nut-free counts only after a person verifies kitchen allergen controls.</p>}
                    </section>
                  </div>
                </>
              )}

              {sectionTab === "hours" && (
                <>
                  <div className={`capacity-note ${hc.numerical_allocation_enabled ? "verified" : ""}`} role="status">
                    <strong>{hc.capacity_unit_status}</strong>
                    <span>{formatNumber(hc.total_listed_capacity)} listed across {ov.total_vendors} vendors, with {hc.verified_unit_count} units confirmed. {hc.numerical_allocation_enabled ? "Comparisons are on." : "It could mean meals, people or something else, so comparisons stay off until each unit is confirmed."}</span>
                  </div>

                  <section className="panel">
                    <header><h2>When each vendor is open</h2><span className="muted small">Select a vendor to verify it</span></header>
                    <HoursTimeline vendors={data.vendors} windows={hc.meal_windows} onOpen={setSelectedCode} />
                    <div className="chart-key">
                      <span><i className="hours-key cert-valid_beyond_festival" />Valid</span>
                      <span><i className="hours-key cert-expires_during_festival" />Expires during fest</span>
                      <span><i className="hours-key cert-expired" />Expired, cannot be counted</span>
                    </div>
                  </section>

                  <section className="panel">
                    <header><h2>Vendors able to serve each meal</h2><span className="muted small">Open at that time with a certificate good for that day</span></header>
                    <div className="table-wrap flat">
                      <table className="coverage-table">
                        <thead><tr><th scope="col">Meal</th>{days.map((d) => <th key={d} scope="col">{shortDate(d)}</th>)}</tr></thead>
                        <tbody>
                          {hc.meal_windows.map((w, i) => (
                            <tr key={w.key}>
                              <th scope="row">{w.label}<small>{w.window}</small></th>
                              {hc.service_grid.map((day) => {
                                const cell = day.windows[i];
                                return (
                                  <td key={day.day} className={cell.clear.length ? "day-ok" : cell.boundary.length ? "day-warn" : "day-bad"} title={[...cell.clear, ...cell.boundary].join(", ")}>
                                    {cell.clear.length} ready<small>{cell.boundary.length ? `${cell.boundary.length} to check` : ""}</small>
                                  </td>
                                );
                              })}
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </section>
                </>
              )}

              {sectionTab === "interruption" && (
                <>
                  <section className="panel">
                    <header><h2>Report a vendor unavailable</h2><span className="muted small">This is a simulation. No status changes and no food demand moves until someone approves it.</span></header>
                    <form className="inline-form" onSubmit={(e) => { e.preventDefault(); simulateAction.mutate(target); }}>
                      <select aria-label="Vendor" value={target} onChange={(e) => setSimulatingVendorCode(e.target.value)}>
                        {data.vendors.map((v) => <option key={v.code} value={v.code}>{v.code}, {v.name}: {v.menu}</option>)}
                      </select>
                      <input aria-label="Reason" placeholder="What happened?" value={simulationReason} onChange={(e) => setSimulationReason(e.target.value)} />
                      {can("vendors.write") && <button className="button-primary small" disabled={simulateAction.isPending}><ShieldAlert size={16} /> {simulateAction.isPending ? "Checking" : "See the impact"}</button>}
                    </form>
                    <ErrorNote error={simulateAction.error} />
                  </section>

                  {current && (
                    <InterruptionReviewPanel
                      key={current.id}
                      base={base}
                      interruption={current}
                      canApprove={can("vendors.approve")}
                      onUpdated={(updated) => {
                        setActiveInterruption(updated);
                        queryClient.invalidateQueries();
                      }}
                    />
                  )}

                  {data.interruptions.length > 1 && (
                    <section className="panel">
                      <header><h2>Earlier reports</h2></header>
                      <DataTable
                        rows={data.interruptions}
                        rowKey={(i) => i.id}
                        onRow={(i) => setActiveInterruption(i)}
                        empty="No vendor interruptions recorded yet."
                        columns={[
                          { label: "Vendor", cell: (i) => <span className="mono">{i.vendor_code}</span> },
                          { label: "Reason", cell: (i) => i.reason },
                          { label: "Status", cell: (i) => <Pill value={i.status} tone={i.status === "approved" ? "bad" : i.status === "rejected" ? "info" : "warn"} /> },
                          { label: "Replacements", cell: (i) => i.replacement_codes.join(", ") || "None" },
                          { label: "By", cell: (i) => i.created_by_name },
                          { label: "When", cell: (i) => (i.created_at ? formatTime(i.created_at) : "") },
                        ]}
                      />
                    </section>
                  )}
                </>
              )}
            </>
          );
        })()}
      </QueryState>

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
      {creatingVendor && <CreateVendorModal base={base} onClose={() => setCreatingVendor(false)} />}
      {importingCsv && <ImportVendorsModal base={base} onClose={() => setImportingCsv(false)} />}
    </div>
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
      <header>
        <h2>If {tv.name} ({tv.code}) stops serving</h2>
        <Pill value={interruption.status} tone={interruption.status === "approved" ? "bad" : interruption.status === "rejected" ? "info" : "warn"} />
      </header>

      <div className="callout">
        <strong>{rep.coverage_verdict}</strong>
        <p className="muted small" style={{ marginTop: 4 }}>
          {rep.simulation_notice}
        </p>
      </div>

      <div className="ops-grid">
        <div className="stack">
          <h3>What this vendor provides</h3>
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
          <h3>What would be left uncovered</h3>
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

      <h3>Alternatives for a person to review ({rep.alternatives.length})</h3>
      {rep.alternatives.some((c) => !c.capacity_comparable) && <p className="muted small">Capacity unit not verified, so these vendors are not compared on volume.</p>}
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
          { label: "Menu", cell: (c) => c.menu },
          { label: "Certificate", cell: (c) => <Pill value={CERTIFICATE_SHORT[c.certificate.status]} tone={c.certificate.tone} /> },
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
          ...(rep.alternatives.some((c) => c.capacity_comparable) ? [{ label: "Capacity", cell: (c: InterruptionCandidate) => <small className="muted">{c.capacity_comparison}</small> }] : []),
        ]}
      />

      {rep.excluded_non_compliant.length > 0 && (
        <details>
          <summary className="muted small" style={{ cursor: "pointer" }}>
            {rep.excluded_non_compliant.length} vendors left out because of certificates or status
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
          <strong>Your decision</strong>
          <p className="muted small">
            Approving marks {tv.code} unavailable and logs the replacements you selected: {selectedReplacements.join(", ") || "none yet"}.
          </p>
          <Field label="Reason (goes in the audit log)">
            <input
              type="text"
              placeholder="Why is this the right call?"
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
              Mark {tv.code} unavailable
            </button>
            <button
              className="button-ghost"
              disabled={decisionReason.trim().length < 3 || rejectAction.isPending}
              onClick={() => rejectAction.mutate()}
            >
              Reject, keep vendor as is
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
  const [verifiedDietary, setVerifiedDietary] = useState<string>(vendor.verified_dietary_options?.join("; ") ?? "");
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
          // Blank means nobody has checked. Pre-filling the listed diets here would verify them by accident.
          verified_dietary_options: verifiedDietary.trim() ? verifiedDietary.split(/[;,]/).map((s) => s.trim()).filter(Boolean) : null,
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

      <Field label="Diets you have checked yourself" hint={`Listed in the CSV: ${vendor.dietary_options.join(", ") || "none"}. Leave blank if nobody has checked.`}>
        <input
          type="text"
          placeholder="Separate with semicolons"
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

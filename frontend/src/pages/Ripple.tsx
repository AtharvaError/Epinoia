import { useQuery } from "@tanstack/react-query";
import { Activity, BedDouble, Bus, CalendarClock, Users, Wallet, type LucideIcon } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { type GraphEdge, type GraphNode, ImpactGraph } from "../components/ImpactGraph";
import {
  type Column, DataTable, ErrorNote, Field, Loading, notify, PageHead, Pagination, Pill, QueryState, Tabs, fromInput, toInput, useAction, useDebounced, useFest, usePaged,
} from "../components/ui";
import { api } from "../lib/api";
import { formatMoney, formatNumber, formatTime } from "../lib/format";

type Scenario = "hostel_closure" | "venue_change" | "bus_delay" | "volunteers_unavailable" | "budget_reduction";
type Change = Record<string, unknown>;

interface Impact { module: string; severity: "critical" | "high" | "medium" | "low"; certainty: "fact" | "estimate"; explanation: string; recommended_action: string | null; record_ref: string | null; is_direct: boolean }
interface Report {
  title: string; headline: string; seed_module: string;
  current_state: { label: string; value: string | number }[];
  before_after: { metric: string; before: string | number; after: string | number; certainty: "fact" | "estimate" }[];
  actions: { title: string; detail: string; module: string; suggest: Change | null }[];
  assumptions: string[]; impacts: Impact[];
  graph: { nodes: GraphNode[]; edges: GraphEdge[] };
  summary: { direct_records: number; indirect_records: number; modules: number; critical: number; conflicts: number; facts: number; estimates: number; budget_impact: number; resources: string[]; scheduling: string[] };
  needs_reason: string | null;
}
interface Simulation {
  id: number; scenario: Scenario; label: string; title: string; headline: string; status: string; created_at: string; created_by: string; critical: number; modules: number;
  report: Report; proposed_change: Change; current: boolean | null; can_approve: boolean;
  approval: { status: string; reason: string | null; decided_at: string | null; decided_by: string | null } | null;
}
type HistoryRow = Omit<Simulation, "report" | "proposed_change" | "current" | "can_approve" | "approval">;
interface AuditRow { id: number; created_at: string; actor: string; action: string; entity: string; entity_id: string | null; summary: string; reason: string | null }
interface Options {
  hostels: { code: string; name: string; kind: string; capacity: number; occupied: number }[];
  events: { code: string; name: string; start_at: string | null; end_at: string | null; venue: string | null; registered: number }[];
  venues: { code: string; name: string; capacity: number }[];
  routes: { code: string; name: string; expected_arrival_at: string; passengers: number; vehicle: string | null; status: string }[];
  volunteers: { code: string; name: string; shifts: number }[];
  budgets: { department: string; approved: string }[];
  graph?: never;
}

const SCENARIOS: { key: Scenario; label: string; hint: string; icon: LucideIcon }[] = [
  { key: "hostel_closure", label: "Hostel unavailable", hint: "Who loses a bed, and where they go", icon: BedDouble },
  { key: "venue_change", label: "Venue change", hint: "Capacity, clashes and kit", icon: CalendarClock },
  { key: "bus_delay", label: "Bus delayed", hint: "Passengers and events at risk", icon: Bus },
  { key: "volunteers_unavailable", label: "Volunteers unavailable", hint: "Open shifts and replacements", icon: Users },
  { key: "budget_reduction", label: "Budget reduced", hint: "Shortfall and what to review", icon: Wallet },
];
const SEVERITY_TONE = { critical: "bad", high: "warn", medium: "info", low: undefined } as const;
const STATUS_TONE: Record<string, "ok" | "warn" | "bad" | "info" | undefined> = { applied: "ok", pending_approval: "warn", previewed: "info", stale: "bad", rejected: "bad", cancelled: undefined, superseded: undefined };
const MODULE_LABEL: Record<string, string> = { participants: "Participants", events: "Events", venues: "Venues", volunteers: "Volunteers", transportation: "Transport", accommodation: "Accommodation", finance: "Finance" };
const IDLE_NODES: GraphNode[] = Object.entries({ participants: "Participants", events: "Events", venues: "Venues & Resources", volunteers: "Volunteers", transportation: "Transportation", accommodation: "Accommodation", finance: "Finance" })
  .map(([id, label]) => ({ id, label, depth: 1, severity: "low", records: 0, impacts: 0 }));
const IDLE_EDGES: GraphEdge[] = [["participants", "events"], ["events", "venues"], ["venues", "volunteers"], ["volunteers", "transportation"], ["transportation", "accommodation"], ["accommodation", "finance"], ["finance", "participants"]]
  .map(([source, target]) => ({ source, target, relation: "connects to", active: false }));

function money(value: number) {
  return `${value > 0 ? "+" : ""}${formatMoney(value)}`;
}

function ChangeForm({ scenario, options, change, setChange }: { scenario: Scenario; options: Options; change: Change; setChange: (c: Change) => void }) {
  const [filter, setFilter] = useState("");
  const value = (key: string) => (change[key] as string | number | undefined) ?? "";
  const set = (key: string) => (e: { target: { value: string } }) => setChange({ ...change, [key]: e.target.value });
  if (scenario === "hostel_closure") {
    return (
      <>
        <Field label="Hostel"><select required value={value("hostel_code")} onChange={set("hostel_code")}>
          <option value="">Choose hostel</option>
          {options.hostels.map((h) => <option key={h.code} value={h.code}>{h.name}: {h.occupied} of {h.capacity} beds used</option>)}
        </select></Field>
        <Field label="External budget to add (₹)" hint="Optional. Try this to see how many more people can be placed."><input type="number" min={0} value={value("extra_budget")} onChange={set("extra_budget")} /></Field>
      </>
    );
  }
  if (scenario === "venue_change") {
    const event = options.events.find((e) => e.code === change.event_code);
    const retime = change.retime === true;
    return (
      <>
        <Field label="Event"><select required value={value("event_code")} onChange={set("event_code")}>
          <option value="">Choose event</option>
          {options.events.map((e) => <option key={e.code} value={e.code}>{e.name}{e.venue ? `, ${e.venue}` : ""}</option>)}
        </select></Field>
        <Field label="New venue"><select required value={value("venue_code")} onChange={set("venue_code")}>
          <option value="">Choose venue</option>
          {options.venues.map((v) => <option key={v.code} value={v.code}>{v.name}: {v.capacity} seats</option>)}
        </select></Field>
        <label className="check"><input type="checkbox" checked={retime} onChange={(e) => setChange({ ...change, retime: e.target.checked, start_at: e.target.checked ? toInput(event?.start_at) : "", end_at: e.target.checked ? toInput(event?.end_at) : "" })} /> Also change the time</label>
        {retime && (
          <>
            <Field label="New start (IST)"><input type="datetime-local" required value={value("start_at")} onChange={set("start_at")} /></Field>
            <Field label="New end (IST)"><input type="datetime-local" required value={value("end_at")} onChange={set("end_at")} /></Field>
          </>
        )}
      </>
    );
  }
  if (scenario === "bus_delay") {
    return (
      <>
        <Field label="Trip"><select required value={value("route_code")} onChange={set("route_code")}>
          <option value="">Choose trip</option>
          {options.routes.map((r) => <option key={r.code} value={r.code}>{r.code}, {r.name}: {r.passengers} passengers</option>)}
        </select></Field>
        <Field label="Delay in minutes"><input type="number" required min={1} max={1440} value={value("minutes")} onChange={set("minutes")} /></Field>
      </>
    );
  }
  if (scenario === "volunteers_unavailable") {
    const picked = (change.volunteer_codes as string[] | undefined) ?? [];
    const term = filter.toLowerCase();
    const shown = options.volunteers.filter((v) => !term || v.name.toLowerCase().includes(term) || v.code.toLowerCase().includes(term));
    return (
      <>
        <Field label="Volunteers" hint={`${picked.length} selected`}><input type="search" placeholder="Search by name or ID" value={filter} onChange={(e) => setFilter(e.target.value)} /></Field>
        <div className="pick-list">
          {shown.map((v) => (
            <label key={v.code} className="check">
              <input type="checkbox" checked={picked.includes(v.code)} onChange={(e) => setChange({ ...change, volunteer_codes: e.target.checked ? [...picked, v.code] : picked.filter((c) => c !== v.code) })} />
              {v.name} <span className="mono">{v.shifts} {v.shifts === 1 ? "shift" : "shifts"}</span>
            </label>
          ))}
        </div>
      </>
    );
  }
  return (
    <>
      <Field label="Department"><select value={value("department")} onChange={set("department")}>
        <option value="">All departments</option>
        {options.budgets.map((b) => <option key={b.department} value={b.department}>{b.department}: {formatMoney(b.approved)}</option>)}
      </select></Field>
      <Field label="Reduce by (₹)"><input type="number" required min={1} value={value("amount")} onChange={set("amount")} /></Field>
    </>
  );
}

function CurrentState({ scenario, options, change }: { scenario: Scenario; options: Options; change: Change }) {
  let rows: [string, string | number][] = [];
  if (scenario === "hostel_closure") {
    const h = options.hostels.find((x) => x.code === change.hostel_code);
    if (h) rows = [["Beds", h.capacity], ["Occupied", h.occupied], ["Type", h.kind === "external" ? "External" : "On campus"]];
  } else if (scenario === "venue_change") {
    const e = options.events.find((x) => x.code === change.event_code);
    if (e) rows = [["Venue", e.venue ?? "None"], ["Registered", e.registered], ["Starts", e.start_at ? formatTime(e.start_at) : "Not set"]];
  } else if (scenario === "bus_delay") {
    const r = options.routes.find((x) => x.code === change.route_code);
    if (r) rows = [["Expected arrival", formatTime(r.expected_arrival_at)], ["Passengers", r.passengers], ["Vehicle", r.vehicle ?? "None"], ["Status", r.status]];
  } else if (scenario === "budget_reduction") {
    const scope = options.budgets.filter((b) => !change.department || b.department === change.department);
    rows = [["Approved budget", formatMoney(scope.reduce((sum, b) => sum + Number(b.approved), 0))]];
  } else {
    const picked = (change.volunteer_codes as string[] | undefined) ?? [];
    const selected = options.volunteers.filter((v) => picked.includes(v.code));
    if (selected.length) rows = [["Selected", selected.length], ["Their shifts", selected.reduce((sum, v) => sum + v.shifts, 0)]];
  }
  if (!rows.length) return null;
  return (
    <div className="current-state">
      <h3>Current state</h3>
      <dl>{rows.map(([k, v]) => <div key={k}><dt>{k}</dt><dd>{v}</dd></div>)}</dl>
    </div>
  );
}

function ImpactList({ impacts, module, onClear }: { impacts: Impact[]; module: string | null; onClear: () => void }) {
  const [certainty, setCertainty] = useState<"all" | "fact" | "estimate">("all");
  const shown = impacts.filter((i) => (!module || i.module === module) && (certainty === "all" || i.certainty === certainty));
  return (
    <section className="panel">
      <header><h2>Impacts</h2><span className="muted small">{shown.length} of {impacts.length}</span></header>
      <div className="filters">
        {(["all", "fact", "estimate"] as const).map((c) => <button key={c} className={certainty === c ? "chip active" : "chip"} onClick={() => setCertainty(c)}>{c === "all" ? "All" : c === "fact" ? "Verified facts" : "Estimates"}</button>)}
        {module && <button className="chip active" onClick={onClear}>{MODULE_LABEL[module]} only, clear</button>}
      </div>
      <ul className="impacts">
        {shown.map((i, index) => (
          <li key={`${i.explanation}-${index}`} className={`impact impact-${i.severity}`}>
            <div className="impact-tags">
              <Pill value={i.severity} tone={SEVERITY_TONE[i.severity]} />
              <span className="tag-plain">{MODULE_LABEL[i.module]}</span>
              <span className="tag-plain">{i.is_direct ? "Direct" : "Indirect"}</span>
              <span className={i.certainty === "fact" ? "certainty fact" : "certainty estimate"}>{i.certainty === "fact" ? "Verified fact" : "Estimate"}</span>
            </div>
            <p>{i.explanation}</p>
            {i.recommended_action && <p className="impact-action">{i.recommended_action}</p>}
          </li>
        ))}
        {!shown.length && <li className="muted small">Nothing matches this filter.</li>}
      </ul>
    </section>
  );
}

function Decision({ simulation, onNew, onClosed }: { simulation: Simulation; onNew: (id: number) => void; onClosed: () => void }) {
  const { base } = useFest();
  const [reason, setReason] = useState("");
  const { id, report, status, current, can_approve: canApprove } = simulation;
  const path = `${base}/simulations/${id}`;
  const approve = useAction(() => api<{ result: string; notified: number }>(`${path}/approve`, { method: "POST", body: JSON.stringify({ reason: reason || null }) }), {
    success: (r) => `Applied: ${r.result}. ${r.notified} people notified.`,
  });
  const submit = useAction(() => api(`${path}/submit`, { method: "POST", body: JSON.stringify({ reason: reason || null }) }), { success: "Sent for approval" });
  const reject = useAction(() => api(`${path}/reject`, { method: "POST", body: JSON.stringify({ reason }) }), { success: "Change rejected" });
  const cancel = useAction(() => api(`${path}/cancel`, { method: "POST" }), { success: "Simulation cancelled", onDone: onClosed });
  const recalculate = useAction(() => api<Simulation>(`${path}/recalculate`, { method: "POST" }), { success: "Recalculated against current data", onDone: (s) => onNew(s.id) });
  const open = status === "previewed" || status === "pending_approval";
  const reasonMissing = Boolean(report.needs_reason) && reason.trim().length < 5;
  const stale = status === "stale" || (open && current === false);

  if (status === "applied") {
    return <div className="decision done"><Pill value="applied" tone="ok" /><span>Applied{simulation.approval?.decided_by ? ` by ${simulation.approval.decided_by}` : ""}{simulation.approval?.decided_at ? ` on ${formatTime(simulation.approval.decided_at)}` : ""}.</span>{simulation.approval?.reason && <span className="muted">Reason: {simulation.approval.reason}</span>}</div>;
  }
  if (!open && !stale) {
    return <div className="decision done"><Pill value={status} tone={STATUS_TONE[status]} />{simulation.approval?.reason && <span className="muted">{simulation.approval.reason}</span>}<button className="button-ghost" disabled={recalculate.isPending} onClick={() => recalculate.mutate()}>Run again</button></div>;
  }
  return (
    <section className="decision">
      {stale ? (
        <div className="stale" role="alert">
          <strong>The data changed after this simulation ran.</strong> It can no longer be applied. Recalculate to review the current picture.
          <button className="button-primary small" disabled={recalculate.isPending} onClick={() => recalculate.mutate()}>Recalculate</button>
        </div>
      ) : (
        <>
          {status === "pending_approval" && <p className="small"><Pill value="waiting for approval" tone="warn" /> {canApprove ? "Review this change and decide." : "An approver has been notified."}</p>}
          {(canApprove || status === "previewed") && (
            <Field label={report.needs_reason && canApprove ? "Reason (required)" : "Reason or note (optional)"} hint={report.needs_reason ?? undefined}>
              <textarea rows={2} value={reason} onChange={(e) => setReason(e.target.value)} />
            </Field>
          )}
          <ErrorNote error={approve.error || submit.error || reject.error || cancel.error} />
          <div className="row-actions">
            {canApprove && <button className="button-primary" disabled={approve.isPending || reasonMissing} onClick={() => approve.mutate()}>{approve.isPending ? "Applying" : "Approve and Apply Changes"}</button>}
            {canApprove && status === "pending_approval" && <button className="button-ghost danger" disabled={reject.isPending || reason.trim().length < 5} onClick={() => reject.mutate()}>Reject</button>}
            {!canApprove && status === "previewed" && <button className="button-primary" disabled={submit.isPending} onClick={() => submit.mutate()}>Request approval</button>}
            <button className="button-ghost" disabled={cancel.isPending} onClick={() => cancel.mutate()}>Cancel Simulation</button>
          </div>
          {!canApprove && <p className="muted small">Your role can simulate changes. A Festival Director applies them.</p>}
        </>
      )}
    </section>
  );
}

function Results({ simulation, onNew, onClosed, onTry }: { simulation: Simulation; onNew: (id: number) => void; onClosed: () => void; onTry: (s: Change) => void }) {
  const [module, setModule] = useState<string | null>(null);
  const { report } = simulation;
  const { summary } = report;
  const chain = report.graph.edges.filter((e) => e.active);
  const names = Object.fromEntries(report.graph.nodes.map((n) => [n.id, n.label]));
  const open = simulation.status === "previewed";
  return (
    <div className="results">
      <header className="results-head">
        <div>
          <div className="row-actions"><Pill value={simulation.status} tone={STATUS_TONE[simulation.status]} /><span className="muted small">{simulation.created_by}, {formatTime(simulation.created_at)}</span></div>
          <h2>{report.title}</h2>
          <p>{report.headline}</p>
        </div>
      </header>
      <div className="stat-row">
        <div><span>Direct records</span><strong>{formatNumber(summary.direct_records)}</strong></div>
        <div><span>Indirect records</span><strong>{formatNumber(summary.indirect_records)}</strong></div>
        <div><span>Modules reached</span><strong>{summary.modules} of 7</strong></div>
        <div><span>Critical impacts</span><strong className={summary.critical ? "over-text" : ""}>{summary.critical}</strong></div>
        <div><span>Budget impact</span><strong>{summary.budget_impact ? money(summary.budget_impact) : "None"}</strong></div>
        <div><span>Facts / estimates</span><strong>{summary.facts} / {summary.estimates}</strong></div>
      </div>
      <section className="panel">
        <header><h2>Impact dependency graph</h2><span className="muted small">Select a module to filter the impacts</span></header>
        <ImpactGraph nodes={report.graph.nodes} edges={report.graph.edges} selected={module} onSelect={setModule} />
        <div className="graph-key">
          <span><i className="key source" />Change</span><span><i className="key critical" />Critical</span><span><i className="key high" />High</span>
          <span><i className="key medium" />Medium</span><span><i className="key low" />Low</span><span><i className="key off" />Not affected</span>
        </div>
        {chain.length > 0 && <p className="chain"><strong>How it travelled: </strong>{chain.map((e) => `${names[e.source]} ${e.relation} ${names[e.target]}`).join(". ")}.</p>}
      </section>
      <div className="chart-grid">
        <section className="panel">
          <header><h2>Before and after</h2></header>
          <div className="table-wrap flat">
            <table>
              <thead><tr><th scope="col">Measure</th><th scope="col">Before</th><th scope="col">After</th></tr></thead>
              <tbody>
                {report.before_after.map((r) => (
                  <tr key={r.metric}>
                    <td>{r.metric}{r.certainty === "estimate" && <span className="certainty estimate">Estimate</span>}</td>
                    <td>{typeof r.before === "number" ? formatNumber(r.before) : r.before}</td>
                    <td className={r.before !== r.after ? "changed" : ""}>{typeof r.after === "number" ? formatNumber(r.after) : r.after}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
        <section className="panel">
          <header><h2>Recommended corrective actions</h2></header>
          {report.actions.length ? (
            <ul className="stack">
              {report.actions.map((a) => (
                <li key={a.title}>
                  <div><strong>{a.title}</strong><br /><span className="muted small">{a.detail}</span></div>
                  {a.suggest && open && <button className="button-ghost" onClick={() => onTry(a.suggest!)}>Try this</button>}
                </li>
              ))}
            </ul>
          ) : <p className="muted small">No action needed.</p>}
          {(summary.resources.length > 0 || summary.scheduling.length > 0) && (
            <div className="notes">
              {summary.resources.length > 0 && <p className="small"><strong>Resources required: </strong>{summary.resources.join(", ")}</p>}
              {summary.scheduling.length > 0 && <p className="small"><strong>Scheduling: </strong>{summary.scheduling.join("; ")}</p>}
            </div>
          )}
        </section>
      </div>
      <ImpactList impacts={report.impacts} module={module} onClear={() => setModule(null)} />
      <section className="panel"><header><h2>Assumptions behind the estimates</h2></header><ul className="assumptions">{report.assumptions.map((a) => <li key={a}>{a}</li>)}</ul></section>
      <Decision simulation={simulation} onNew={onNew} onClosed={onClosed} />
    </div>
  );
}

function History({ onOpen }: { onOpen: (id: number) => void }) {
  const { base } = useFest();
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(1);
  useEffect(() => setPage(1), [status]);
  const query = usePaged<HistoryRow>(`${base}/simulations`, { status, page });
  const columns: Column<HistoryRow>[] = [
    { label: "Run", cell: (s) => formatTime(s.created_at) },
    { label: "Change", cell: (s) => <><strong>{s.title}</strong><br /><span className="muted small">{s.headline}</span></> },
    { label: "By", cell: (s) => s.created_by },
    { label: "Critical", cell: (s) => s.critical, className: "num" },
    { label: "Status", cell: (s) => <Pill value={s.status} tone={STATUS_TONE[s.status]} /> },
  ];
  return (
    <>
      <div className="filters">
        <select aria-label="Status" value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">All statuses</option>
          {["previewed", "pending_approval", "applied", "rejected", "stale", "cancelled", "superseded"].map((s) => <option key={s} value={s}>{s.replace("_", " ")}</option>)}
        </select>
      </div>
      <QueryState query={query}>{query.data && <><DataTable rows={query.data.items} columns={columns} rowKey={(s) => s.id} empty="No simulations yet." onRow={(s) => onOpen(s.id)} /><Pagination page={query.data.page} pages={query.data.pages} total={query.data.total} onPage={setPage} /></>}</QueryState>
    </>
  );
}

function AuditLog() {
  const { base } = useFest();
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const q = useDebounced(search);
  useEffect(() => setPage(1), [q]);
  const query = usePaged<AuditRow>(`${base}/audit`, { q, page });
  const columns: Column<AuditRow>[] = [
    { label: "When", cell: (a) => formatTime(a.created_at) },
    { label: "Who", cell: (a) => a.actor },
    { label: "What happened", cell: (a) => <>{a.summary}{a.reason && <><br /><span className="muted small">Reason: {a.reason}</span></>}</> },
    { label: "Action", cell: (a) => <span className="mono">{a.action}</span> },
  ];
  return (
    <>
      <p className="muted small">This record cannot be edited or deleted. The database refuses it.</p>
      <div className="filters"><input type="search" aria-label="Search audit log" placeholder="Search events or people" value={search} onChange={(e) => setSearch(e.target.value)} /></div>
      <QueryState query={query}>{query.data && <><DataTable rows={query.data.items} columns={columns} rowKey={(a) => a.id} empty="Nothing matches." /><Pagination page={query.data.page} pages={query.data.pages} total={query.data.total} onPage={setPage} /></>}</QueryState>
    </>
  );
}

function payload(scenario: Scenario, change: Change): Change {
  if (scenario === "hostel_closure") return { hostel_code: change.hostel_code, extra_budget: Number(change.extra_budget || 0) };
  if (scenario === "venue_change") return { event_code: change.event_code, venue_code: change.venue_code, start_at: change.retime ? fromInput(String(change.start_at)) : null, end_at: change.retime ? fromInput(String(change.end_at)) : null };
  if (scenario === "bus_delay") return { route_code: change.route_code, minutes: Number(change.minutes) };
  if (scenario === "budget_reduction") return { department: change.department || null, amount: Number(change.amount) };
  return { volunteer_codes: change.volunteer_codes ?? [] };
}

function formValues(scenario: Scenario, proposed: Change): Change {
  if (scenario === "venue_change" && proposed.start_at) return { ...proposed, retime: true, start_at: toInput(String(proposed.start_at)), end_at: toInput(String(proposed.end_at)) };
  return { ...proposed };
}

export function Ripple() {
  const { base, can } = useFest();
  const [params, setParams] = useSearchParams();
  const [tab, setTab] = useState<"simulate" | "history" | "audit">("simulate");
  const [scenario, setScenario] = useState<Scenario>("hostel_closure");
  const [changes, setChanges] = useState<Partial<Record<Scenario, Change>>>({});
  const simulationId = params.get("simulation");
  const change = useMemo(() => changes[scenario] ?? {}, [changes, scenario]);
  const setChange = (next: Change) => setChanges((all) => ({ ...all, [scenario]: next }));

  const options = useQuery({ queryKey: ["simulation-options", base], queryFn: () => api<Options>(`${base}/simulations/options`) });
  const detail = useQuery({
    queryKey: ["simulation", simulationId],
    queryFn: () => api<Simulation>(`${base}/simulations/${simulationId}`),
    enabled: Boolean(simulationId),
    refetchInterval: (q) => (["previewed", "pending_approval"].includes(q.state.data?.status ?? "") ? 15000 : false),
  });
  const open = (id: number) => {
    setTab("simulate");
    setParams({ simulation: String(id) });
  };
  const run = useAction((body: { scenario: Scenario; change: Change }) => api<Simulation>(`${base}/simulations`, { method: "POST", body: JSON.stringify(body) }), { onDone: (s) => open(s.id) });

  const simulation = detail.data;
  useEffect(() => {
    if (simulation) {
      setScenario(simulation.scenario);
      setChanges((all) => ({ ...all, [simulation.scenario]: formValues(simulation.scenario, simulation.proposed_change) }));
    }
  }, [simulation?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  const pick = (key: Scenario) => {
    setScenario(key);
    if (simulation && simulation.scenario !== key) setParams({});
  };
  const tryAction = (suggest: Change) => {
    const next = { ...change, ...suggest };
    setChange(next);
    notify("Running the simulation with that change");
    run.mutate({ scenario, change: payload(scenario, next) });
  };

  return (
    <div>
      <PageHead title="Ripple Simulator" subtitle="See what a change will break before you make it. Nothing changes until it is approved." />
      <Tabs value={tab} onChange={setTab} options={[["simulate", "Simulate"], ["history", "History"], ...(can("audit.read") ? [["audit", "Audit log"] as ["audit", string]] : [])]} />
      {tab === "history" && <History onOpen={open} />}
      {tab === "audit" && <AuditLog />}
      {tab === "simulate" && (
        <div className="ripple">
          <aside className="ripple-side">
            <section className="panel">
              <h2>1. Choose a scenario</h2>
              <div className="scenario-list" role="radiogroup" aria-label="Scenario">
                {SCENARIOS.map(({ key, label, hint, icon: Icon }) => (
                  <button key={key} role="radio" aria-label={label} aria-checked={scenario === key} className={scenario === key ? "scenario active" : "scenario"} onClick={() => pick(key)}>
                    <Icon size={18} aria-hidden="true" /><span><strong>{label}</strong><small>{hint}</small></span>
                  </button>
                ))}
              </div>
            </section>
            <section className="panel">
              <h2>2. Describe the change</h2>
              <QueryState query={options}>
                {options.data && (
                  <form className="form" onSubmit={(e) => { e.preventDefault(); run.mutate({ scenario, change: payload(scenario, change) }); }}>
                    <ChangeForm key={scenario} scenario={scenario} options={options.data} change={change} setChange={setChange} />
                    <CurrentState scenario={scenario} options={options.data} change={change} />
                    <ErrorNote error={run.error} />
                    <button className="button-primary" disabled={run.isPending || (scenario === "volunteers_unavailable" && !(change.volunteer_codes as string[] | undefined)?.length)}>
                      <Activity size={16} /> {run.isPending ? "Simulating" : "Run Simulation"}
                    </button>
                    <p className="muted small">This only reads data. No records change.</p>
                  </form>
                )}
              </QueryState>
            </section>
          </aside>
          <div className="ripple-main">
            {simulationId && detail.isPending && <Loading />}
            {detail.isError && <ErrorNote error={detail.error} />}
            {simulation && <Results key={simulation.id} simulation={simulation} onNew={open} onClosed={() => setParams({})} onTry={tryAction} />}
            {!simulationId && (
              <section className="panel ripple-empty">
                <ImpactGraph nodes={IDLE_NODES} edges={IDLE_EDGES} idle />
                <h2>Seven modules, one chain of consequences</h2>
                <p className="muted">Choose a change on the left. The simulator follows real links between records, from who is housed where to which events they are registered for, and shows what is affected and what to do about it.</p>
              </section>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

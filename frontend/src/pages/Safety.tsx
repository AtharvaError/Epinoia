import { useQuery } from "@tanstack/react-query";
import { TriangleAlert } from "lucide-react";
import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { type Column, DataTable, Dialog, ErrorNote, Facts, Field, PageHead, Pagination, Pill, QueryState, ReasonForm, Simulated, useAction, useFest, usePaged } from "../components/ui";
import { api } from "../lib/api";
import { formatTime } from "../lib/format";

interface Incident { code: string; category: string; location: string; description: string; priority: string; reported_by: string; assigned_team: string | null; reported_at: string; status: string; resolution_notes: string | null; is_simulated: boolean; next_statuses: string[] }
interface Detail extends Incident { history: { at: string; by: string; summary: string; reason: string | null }[] }
interface Summary { open: number; total: number; urgent: number; unassigned: number }

const PRIORITY: Record<string, "bad" | "warn" | "info" | undefined> = { critical: "bad", high: "warn", medium: "info" };
const STATUS: Record<string, "ok" | "warn" | undefined> = { resolved: "ok", closed: "ok", reported: "warn" };

function ReportForm({ onClose }: { onClose: () => void }) {
  const { base } = useFest();
  const [form, setForm] = useState({ category: "", location: "", description: "", priority: "medium" });
  const set = (key: keyof typeof form) => (e: { target: { value: string } }) => setForm({ ...form, [key]: e.target.value });
  const save = useAction(() => api(`${base}/incidents`, { method: "POST", body: JSON.stringify(form) }), { success: "Incident reported", onDone: onClose });
  return (
    <Dialog title="Report an incident" onClose={onClose}>
      <form className="form form-grid" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
        <Field label="Category"><input required minLength={2} value={form.category} onChange={set("category")} /></Field>
        <Field label="Location"><input required minLength={2} value={form.location} onChange={set("location")} /></Field>
        <Field label="Priority"><select value={form.priority} onChange={set("priority")}>{["low", "medium", "high", "critical"].map((p) => <option key={p} value={p}>{p[0].toUpperCase() + p.slice(1)}</option>)}</select></Field>
        <div className="span-all"><Field label="What happened"><textarea required minLength={5} rows={3} value={form.description} onChange={set("description")} /></Field></div>
        <div className="span-all"><ErrorNote error={save.error} /><button className="button-primary" disabled={save.isPending}>Report incident</button></div>
      </form>
    </Dialog>
  );
}

function IncidentDrawer({ code, onClose }: { code: string; onClose: () => void }) {
  const { base, can } = useFest();
  const path = `${base}/incidents/${code}`;
  const [team, setTeam] = useState("");
  const [notes, setNotes] = useState("");
  const [escalating, setEscalating] = useState(false);
  const query = useQuery({ queryKey: ["incident", code], queryFn: () => api<Detail>(path) });
  const move = useAction((status: string) => api(`${path}/status`, { method: "POST", body: JSON.stringify({ status, assigned_team: team || null, resolution_notes: notes || null }) }), { success: "Status updated" });
  const escalate = useAction((reason: string) => api(`${path}/escalate`, { method: "POST", body: JSON.stringify({ reason }) }), { success: "Escalated and responders notified", onDone: () => setEscalating(false) });
  const i = query.data;
  const write = can("incidents.write");
  return (
    <Dialog title={i ? `${i.code}: ${i.category}` : code} onClose={onClose} side>
      <QueryState query={query}>
        {i && escalating && <ReasonForm label="Why does this need escalating?" action="Escalate" danger onSubmit={(r) => escalate.mutate(r)} pending={escalate.isPending} error={escalate.error} />}
        {i && !escalating && (
          <>
            <div className="row-actions"><Pill value={i.priority} tone={PRIORITY[i.priority]} /><Pill value={i.status} tone={STATUS[i.status]} />{i.is_simulated && <Simulated />}</div>
            <p className="impact-text">{i.description}</p>
            <Facts items={[["Location", i.location], ["Reported by", i.reported_by], ["Reported", formatTime(i.reported_at)], ["Response team", i.assigned_team ?? "Not assigned"], ...(i.resolution_notes ? [["Resolution", i.resolution_notes] as [string, string]] : [])]} />
            {write && i.next_statuses.length > 0 && (
              <div className="form">
                <h3>Next step</h3>
                {i.next_statuses.includes("assigned") && <Field label="Response team"><input value={team} onChange={(e) => setTeam(e.target.value)} /></Field>}
                {i.next_statuses.includes("resolved") && <Field label="Resolution notes"><textarea rows={2} value={notes} onChange={(e) => setNotes(e.target.value)} /></Field>}
                <ErrorNote error={move.error} />
                <div className="row-actions">
                  {i.next_statuses.map((s) => <button key={s} className={s === "in_progress" && i.status === "resolved" ? "button-ghost" : "button-primary small"} disabled={move.isPending} onClick={() => move.mutate(s)}>{s === "in_progress" && i.status === "resolved" ? "Reopen" : `Mark ${s.replace("_", " ")}`}</button>)}
                  {!["resolved", "closed"].includes(i.status) && i.priority !== "critical" && <button className="button-ghost danger" onClick={() => setEscalating(true)}>Escalate</button>}
                </div>
              </div>
            )}
            <h3>History</h3>
            {i.history.length ? <ul className="stack">{i.history.map((h, index) => <li key={index}><div>{h.summary}{h.reason && <><br /><span className="muted small">{h.reason}</span></>}</div><small className="muted">{formatTime(h.at)}</small></li>)}</ul> : <p className="muted small">Seeded demo incident with no recorded steps yet.</p>}
          </>
        )}
      </QueryState>
    </Dialog>
  );
}

const columns: Column<Incident>[] = [
  { label: "ID", sort: "code", cell: (i) => <span className="mono">{i.code}</span> },
  { label: "Incident", cell: (i) => <><strong>{i.category}</strong><br /><small className="muted">{i.location}</small></> },
  { label: "Priority", sort: "priority", cell: (i) => <Pill value={i.priority} tone={PRIORITY[i.priority]} /> },
  { label: "Status", sort: "status", cell: (i) => <Pill value={i.status} tone={STATUS[i.status]} /> },
  { label: "Team", sort: "team", cell: (i) => i.assigned_team ?? "Not assigned" },
  { label: "Reported", sort: "reported", cell: (i) => formatTime(i.reported_at) },
];

export function Safety() {
  const { base, can } = useFest();
  const [params, setParams] = useSearchParams();
  const [openOnly, setOpenOnly] = useState(true);
  const [priority, setPriority] = useState("");
  const [sort, setSort] = useState("");
  const [page, setPage] = useState(1);
  const [reporting, setReporting] = useState(false);
  useEffect(() => setPage(1), [openOnly, priority, sort]);
  const list = usePaged<Incident>(`${base}/incidents`, { open_only: openOnly, priority, sort, page });
  const summary = useQuery({ queryKey: ["incident-summary", base], queryFn: () => api<Summary>(`${base}/incidents/summary`) });
  const opened = params.get("open");
  return (
    <div>
      <PageHead title="Safety & Incidents" simulated subtitle="FestOS tracks and notifies. Emergency decisions stay with the Safety Coordinator and Director.">
        {can("incidents.report") && <button className="button-primary small" onClick={() => setReporting(true)}><TriangleAlert size={16} /> Report incident</button>}
      </PageHead>
      {summary.data && (
        <div className="stat-row page-stats">
          <div><span>Open</span><strong>{summary.data.open}</strong></div>
          <div><span>High or critical</span><strong className={summary.data.urgent ? "over-text" : ""}>{summary.data.urgent}</strong></div>
          <div><span>Waiting for a team</span><strong>{summary.data.unassigned}</strong></div>
          <div><span>All incidents</span><strong>{summary.data.total}</strong></div>
        </div>
      )}
      <div className="filters">
        <label className="check"><input type="checkbox" checked={openOnly} onChange={(e) => setOpenOnly(e.target.checked)} /> Open only</label>
        <select aria-label="Priority" value={priority} onChange={(e) => setPriority(e.target.value)}><option value="">All priorities</option>{["critical", "high", "medium", "low"].map((p) => <option key={p} value={p}>{p[0].toUpperCase() + p.slice(1)}</option>)}</select>
      </div>
      <QueryState query={list}>{list.data && <><DataTable rows={list.data.items} columns={columns} rowKey={(i) => i.code} empty="No incidents match." onRow={(i) => setParams({ open: i.code })} sort={sort} onSort={setSort} /><Pagination page={list.data.page} pages={list.data.pages} total={list.data.total} onPage={setPage} /></>}</QueryState>
      {opened && <IncidentDrawer code={opened} onClose={() => setParams({})} />}
      {reporting && <ReportForm onClose={() => setReporting(false)} />}
    </div>
  );
}

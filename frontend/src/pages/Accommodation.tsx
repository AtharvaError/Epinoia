import { useQuery } from "@tanstack/react-query";
import { Plus, Sparkles } from "lucide-react";
import { type FormEvent, useEffect, useState } from "react";

import {
  type Column, DataTable, Dialog, ErrorNote, Field, PageHead, Pagination, Pill, QueryState, Tabs, useAction, useDebounced, useFest, usePaged,
} from "../components/ui";
import { api } from "../lib/api";
import { formatMoney, formatNumber } from "../lib/format";

interface Summary {
  hostels: { code: string; name: string; kind: string; status: string; capacity: number; occupied: number; cost_per_bed_night: string | null; distance_km: string | null }[];
  over_capacity: { room: string; capacity: number; occupied: number }[];
  unallocated: number;
  budget: string | null;
}
interface PlanSummary {
  people: number; placed: number; held: number; on_campus: number; external: number; external_cost: number; contingents_split: number;
  held_reasons: { reason: string; count: number }[]; relaxed: { constraint: string; count: number }[]; splits: { group: string; locations: number }[];
}
interface Plan { summary: PlanSummary; state_hash: string; budget_limit: string | null; approved_budget: string | null; already_committed: string; over_approved_budget: boolean }
interface RoomRow { code: string; hostel: string; floor: number | null; gender_policy: string | null; capacity: number; occupied: number; available: number; accessible: boolean | null; status: string }
interface AllocationRow { id: number; participant: string; name: string; room: string; status: string; check_in_at: string | null; check_out_at: string | null; relaxed_constraints: string[]; override_reason: string | null }
interface Unallocated { code: string; name: string; college: string; gender: string | null; accessibility: string | null; restricted: boolean }

const RELAXED: Record<string, string> = {
  room_set_by_property: "Exact room is set by the external property",
  minor_off_campus: "Participant under 18 placed off campus, needs review",
};

function PlanPanel({ summary }: { summary: Summary }) {
  const { base, can } = useFest();
  const [limit, setLimit] = useState("");
  const [reason, setReason] = useState("");
  const body = limit ? { budget_limit: Number(limit) } : {};
  const preview = useAction(() => api<Plan>(`${base}/accommodation/plan`, { method: "POST", body: JSON.stringify(body) }));
  const apply = useAction(() => api<PlanSummary>(`${base}/accommodation/plan/apply`, {
    method: "POST", body: JSON.stringify({ ...body, state_hash: preview.data!.state_hash, reason: reason || null }),
  }), { success: (r) => `${formatNumber(r.placed)} beds allocated, ${formatNumber(r.held)} held`, onDone: () => preview.reset() });
  const plan = preview.data;
  const s = plan?.summary;
  return (
    <section className="panel plan">
      <header><h2>Allocation plan</h2><span className="muted small">{formatNumber(summary.unallocated)} still need a bed</span></header>
      <p className="panel-note">Previewing changes nothing. Hard rules are never relaxed: anyone who cannot be placed safely is held for a person to decide.</p>
      <form className="inline-form" onSubmit={(e: FormEvent) => { e.preventDefault(); preview.mutate(); }}>
        <Field label="External stay limit (₹)" hint={`Approved Hospitality budget: ${summary.budget ? formatMoney(summary.budget) : "none set"} (simulated)`}>
          <input type="number" min={0} placeholder="Approved budget" value={limit} onChange={(e) => setLimit(e.target.value)} />
        </Field>
        <button className="button-primary" disabled={preview.isPending}><Sparkles size={16} /> {preview.isPending ? "Working" : "Preview plan"}</button>
      </form>
      <ErrorNote error={preview.error} />
      {s && plan && (
        <div className="plan-result">
          <div className="stat-row">
            <div><span>Placed</span><strong>{formatNumber(s.placed)}</strong><small>of {formatNumber(s.people)}</small></div>
            <div><span>On campus</span><strong>{formatNumber(s.on_campus)}</strong></div>
            <div><span>External</span><strong>{formatNumber(s.external)}</strong></div>
            <div><span>External cost</span><strong>{formatMoney(s.external_cost)}</strong><small>{formatMoney(plan.already_committed)} already committed</small></div>
            <div><span>Held</span><strong className={s.held ? "over-text" : ""}>{formatNumber(s.held)}</strong></div>
            <div><span>Contingents split</span><strong>{s.contingents_split}</strong></div>
          </div>
          <div className="chart-grid">
            <div><h3>Why people are held</h3>
              {s.held_reasons.length ? <ul className="stack">{s.held_reasons.map((h) => <li key={h.reason}><span>{h.reason}</span><strong>{formatNumber(h.count)}</strong></li>)}</ul> : <p className="muted small">Everyone is placed.</p>}
            </div>
            <div><h3>Trade-offs made</h3>
              {s.relaxed.length ? <ul className="stack">{s.relaxed.map((r) => <li key={r.constraint}><span>{RELAXED[r.constraint] ?? r.constraint}</span><strong>{formatNumber(r.count)}</strong></li>)}</ul> : <p className="muted small">No constraints were relaxed.</p>}
              {s.splits.length > 0 && <p className="muted small">Largest splits: {s.splits.slice(0, 4).map((x) => `${x.group} across ${x.locations}`).join("; ")}.</p>}
            </div>
          </div>
          {can("accommodation.write") && (
            <form className="form" onSubmit={(e) => { e.preventDefault(); apply.mutate(); }}>
              {plan.over_approved_budget && <Field label="Reason for going over the approved budget"><textarea required minLength={5} rows={2} value={reason} onChange={(e) => setReason(e.target.value)} /></Field>}
              <ErrorNote error={apply.error} />
              <button className="button-primary" disabled={apply.isPending || s.placed === 0}>{apply.isPending ? "Allocating" : `Allocate ${formatNumber(s.placed)} beds`}</button>
            </form>
          )}
        </div>
      )}
    </section>
  );
}

function ManualAllocate({ code, onClose }: { code: string; onClose: () => void }) {
  const { base } = useFest();
  const [room, setRoom] = useState("");
  const [reason, setReason] = useState("");
  const suggestions = useQuery({ queryKey: ["suggest", code], queryFn: () => api<{ code: string; hostel: string; available: number }[]>(`${base}/accommodation/suggest/${code}`) });
  const save = useAction(() => api(`${base}/accommodation/allocate`, { method: "POST", body: JSON.stringify({ participant_code: code, room_code: room, override_reason: reason || null }) }), { success: "Room allocated", onDone: onClose });
  return (
    <Dialog title={`Allocate ${code}`} onClose={onClose}>
      <h3>Rooms that meet every rule</h3>
      <QueryState query={suggestions}>
        {suggestions.data?.length ? <div className="chips">{suggestions.data.map((r) => <button key={r.code} className={room === r.code ? "chip active" : "chip"} onClick={() => setRoom(r.code)}>{r.code} · {r.hostel} · {r.available} free</button>)}</div> : <p className="muted small">No room meets every rule for this person. Enter a room below to override with a reason.</p>}
      </QueryState>
      <form className="form" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
        <Field label="Room"><input required value={room} onChange={(e) => setRoom(e.target.value)} /></Field>
        <Field label="Override reason" hint="Only needed if this room breaks a hard rule"><textarea rows={2} value={reason} onChange={(e) => setReason(e.target.value)} /></Field>
        <ErrorNote error={save.error} />
        <button className="button-primary" disabled={save.isPending}>Allocate room</button>
      </form>
    </Dialog>
  );
}

function AddHostel({ onClose }: { onClose: () => void }) {
  const { base } = useFest();
  const [form, setForm] = useState({ name: "", kind: "on_campus", cost_per_bed_night: "", distance_km: "" });
  const set = (key: keyof typeof form) => (e: { target: { value: string } }) => setForm({ ...form, [key]: e.target.value });
  const save = useAction(() => api(`${base}/accommodation/hostels`, { method: "POST", body: JSON.stringify({ name: form.name, kind: form.kind, cost_per_bed_night: form.cost_per_bed_night || null, distance_km: form.distance_km || null }) }), { success: "Hostel added", onDone: onClose });
  return (
    <Dialog title="Add hostel" onClose={onClose}>
      <form className="form form-grid" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
        <Field label="Name"><input required minLength={2} value={form.name} onChange={set("name")} /></Field>
        <Field label="Type"><select value={form.kind} onChange={set("kind")}><option value="on_campus">On campus</option><option value="external">External</option></select></Field>
        <Field label="Cost per bed per night"><input type="number" min={0} value={form.cost_per_bed_night} onChange={set("cost_per_bed_night")} /></Field>
        <Field label="Distance (km)"><input type="number" min={0} step="0.1" value={form.distance_km} onChange={set("distance_km")} /></Field>
        <div className="span-all"><ErrorNote error={save.error} /><button className="button-primary" disabled={save.isPending}>Save hostel</button></div>
      </form>
    </Dialog>
  );
}

function AddRoom({ hostels, onClose }: { hostels: Summary["hostels"]; onClose: () => void }) {
  const { base } = useFest();
  const [form, setForm] = useState({ hostel_code: hostels[0]?.code ?? "", code: "", floor: "", gender_policy: "", capacity: "2", accessible: "" });
  const set = (key: keyof typeof form) => (e: { target: { value: string } }) => setForm({ ...form, [key]: e.target.value });
  const save = useAction(() => api(`${base}/accommodation/rooms`, { method: "POST", body: JSON.stringify({ ...form, floor: form.floor ? Number(form.floor) : null, gender_policy: form.gender_policy || null, capacity: Number(form.capacity), accessible: form.accessible === "" ? null : form.accessible === "true" }) }), { success: "Room added", onDone: onClose });
  return (
    <Dialog title="Add room" onClose={onClose}>
      <form className="form form-grid" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
        <Field label="Hostel"><select value={form.hostel_code} onChange={set("hostel_code")}>{hostels.map((h) => <option key={h.code} value={h.code}>{h.name}</option>)}</select></Field>
        <Field label="Room number"><input required minLength={2} value={form.code} onChange={set("code")} /></Field>
        <Field label="Floor"><input type="number" min={0} value={form.floor} onChange={set("floor")} /></Field>
        <Field label="Capacity"><input type="number" min={0} required value={form.capacity} onChange={set("capacity")} /></Field>
        <Field label="Gender policy"><select value={form.gender_policy} onChange={set("gender_policy")}><option value="">Not gendered</option><option value="boys">Boys</option><option value="girls">Girls</option></select></Field>
        <Field label="Accessible"><select value={form.accessible} onChange={set("accessible")}><option value="">Unknown</option><option value="true">Yes</option><option value="false">No</option></select></Field>
        <div className="span-all"><ErrorNote error={save.error} /><button className="button-primary" disabled={save.isPending}>Save room</button></div>
      </form>
    </Dialog>
  );
}

const roomColumns: Column<RoomRow>[] = [
  { label: "Room", cell: (r) => <span className="mono">{r.code}</span> },
  { label: "Hostel", cell: (r) => r.hostel },
  { label: "Floor", cell: (r) => r.floor ?? "Unknown" },
  { label: "Policy", cell: (r) => r.gender_policy ?? "Not stated" },
  { label: "Accessible", cell: (r) => (r.accessible === null ? "Unknown" : r.accessible ? "Yes" : "No") },
  { label: "Occupied", cell: (r) => `${r.occupied} / ${r.capacity}`, className: "num" },
  { label: "Free", cell: (r) => r.available, className: "num" },
];

export function Accommodation() {
  const { base, can } = useFest();
  const [tab, setTab] = useState<"rooms" | "allocations" | "unallocated">("unallocated");
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState("");
  const [hostel, setHostel] = useState("");
  const [freeOnly, setFreeOnly] = useState(false);
  const [target, setTarget] = useState<string>();
  const [adding, setAdding] = useState<"hostel" | "room" | null>(null);
  const q = useDebounced(search);
  useEffect(() => setPage(1), [tab, q, hostel, freeOnly]);
  const summary = useQuery({ queryKey: ["accommodation-summary", base], queryFn: () => api<Summary>(`${base}/accommodation/summary`) });
  const rooms = usePaged<RoomRow>(`${base}/accommodation/rooms`, { q, hostel, free_only: freeOnly, page }, tab === "rooms");
  const allocations = usePaged<AllocationRow>(`${base}/accommodation/allocations`, { q, page }, tab === "allocations");
  const unallocated = usePaged<Unallocated>(`${base}/accommodation/unallocated`, { page }, tab === "unallocated");
  const stay = useAction(({ id, action }: { id: number; action: string }) => api(`${base}/accommodation/allocations/${id}/${action}`, { method: "POST" }), { success: "Recorded" });
  const cancel = useAction((id: number) => api(`${base}/accommodation/allocations/${id}`, { method: "DELETE" }), { success: "Allocation cancelled" });
  const write = can("accommodation.write");

  const allocationColumns: Column<AllocationRow>[] = [
    { label: "Participant", cell: (a) => <><strong>{a.name}</strong> <span className="mono">{a.participant}</span></> },
    { label: "Room", cell: (a) => <span className="mono">{a.room}</span> },
    { label: "Status", cell: (a) => <Pill value={a.status} tone={a.status === "checked_in" ? "ok" : undefined} /> },
    { label: "Notes", cell: (a) => (a.relaxed_constraints.length ? a.relaxed_constraints.map((r) => RELAXED[r] ?? r).join("; ") : "") },
    ...(write ? [{ label: "Actions", cell: (a: AllocationRow) => (
      <div className="row-actions">
        {!a.check_in_at && <button className="button-ghost" disabled={stay.isPending} onClick={() => stay.mutate({ id: a.id, action: "check-in" })}>Check in</button>}
        {a.check_in_at && !a.check_out_at && <button className="button-ghost" disabled={stay.isPending} onClick={() => stay.mutate({ id: a.id, action: "check-out" })}>Check out</button>}
        <button className="button-ghost danger" disabled={cancel.isPending} onClick={() => cancel.mutate(a.id)}>Cancel</button>
      </div>
    ) }] : []),
  ];
  const unallocatedColumns: Column<Unallocated>[] = [
    { label: "ID", cell: (p) => <span className="mono">{p.code}</span> },
    { label: "Name", cell: (p) => <strong>{p.name}</strong> },
    { label: "College", cell: (p) => p.college },
    { label: "Gender", cell: (p) => (p.restricted ? <span className="restricted">Restricted</span> : p.gender ?? <span className="unknown">Unknown</span>) },
    { label: "Accessibility", cell: (p) => (p.restricted ? <span className="restricted">Restricted</span> : p.accessibility) },
    ...(write ? [{ label: "Actions", cell: (p: Unallocated) => <button className="button-ghost" onClick={(e) => { e.stopPropagation(); setTarget(p.code); }}>Allocate</button> }] : []),
  ];

  return (
    <div>
      <PageHead title="Accommodation" subtitle="Beds, rooms and the allocation plan">
        {write && <><button className="button-ghost" onClick={() => setAdding("room")}><Plus size={16} /> Add room</button><button className="button-ghost" onClick={() => setAdding("hostel")}><Plus size={16} /> Add hostel</button></>}
      </PageHead>
      <QueryState query={summary}>
        {summary.data && (
          <>
            <div className="card-grid compact">
              {summary.data.hostels.map((h) => (
                <article key={h.code} className="kpi">
                  <div className="kpi-head"><h3>{h.name}</h3>{h.status !== "available" && <Pill value={h.status} tone="bad" />}</div>
                  <p className="kpi-value">{formatNumber(h.occupied)}<span> / {formatNumber(h.capacity)}</span></p>
                  <div className="meter-track"><div style={{ width: `${h.capacity ? Math.min(100, (h.occupied / h.capacity) * 100) : 0}%` }} /></div>
                  <p className="kpi-note">{h.kind === "external" ? `${formatMoney(h.cost_per_bed_night ?? 0)} a night, ${h.distance_km} km` : "On campus"}</p>
                </article>
              ))}
            </div>
            {summary.data.over_capacity.length > 0 && <p className="form-error" role="alert">Over capacity: {summary.data.over_capacity.map((r) => `${r.room} (${r.occupied}/${r.capacity})`).join(", ")}</p>}
            <PlanPanel summary={summary.data} />
          </>
        )}
      </QueryState>
      <Tabs value={tab} onChange={setTab} options={[["unallocated", "Still need a bed"], ["allocations", "Allocations"], ["rooms", "Rooms"]]} />
      {tab !== "unallocated" && (
        <div className="filters">
          <input type="search" aria-label="Search" placeholder={tab === "rooms" ? "Search room" : "Search participant"} value={search} onChange={(e) => setSearch(e.target.value)} />
          {tab === "rooms" && <select aria-label="Hostel" value={hostel} onChange={(e) => setHostel(e.target.value)}><option value="">All hostels</option>{summary.data?.hostels.map((h) => <option key={h.code} value={h.code}>{h.name}</option>)}</select>}
          {tab === "rooms" && <label className="check"><input type="checkbox" checked={freeOnly} onChange={(e) => setFreeOnly(e.target.checked)} /> Free beds only</label>}
        </div>
      )}
      <ErrorNote error={stay.error || cancel.error} />
      {tab === "rooms" && <QueryState query={rooms}>{rooms.data && <><DataTable rows={rooms.data.items} columns={roomColumns} rowKey={(r) => r.code} empty="No rooms match." /><Pagination page={rooms.data.page} pages={rooms.data.pages} total={rooms.data.total} onPage={setPage} /></>}</QueryState>}
      {tab === "allocations" && <QueryState query={allocations}>{allocations.data && <><DataTable rows={allocations.data.items} columns={allocationColumns} rowKey={(a) => a.id} empty="No beds are allocated yet. Preview a plan above." /><Pagination page={allocations.data.page} pages={allocations.data.pages} total={allocations.data.total} onPage={setPage} /></>}</QueryState>}
      {tab === "unallocated" && <QueryState query={unallocated}>{unallocated.data && <><DataTable rows={unallocated.data.items} columns={unallocatedColumns} rowKey={(p) => p.code} empty="Everyone who needs a bed has one." /><Pagination page={unallocated.data.page} pages={unallocated.data.pages} total={unallocated.data.total} onPage={setPage} /></>}</QueryState>}
      {target && <ManualAllocate code={target} onClose={() => setTarget(undefined)} />}
      {adding === "hostel" && <AddHostel onClose={() => setAdding(null)} />}
      {adding === "room" && summary.data && <AddRoom hostels={summary.data.hostels} onClose={() => setAdding(null)} />}
    </div>
  );
}

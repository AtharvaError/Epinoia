import { useQuery } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { type FormEvent, useEffect, useState } from "react";

import {
  type Column, DataTable, Dialog, ErrorNote, Facts, Field, PageHead, Pagination, Pill, QueryState, Tabs, useAction, useDebounced, useFest, usePaged,
} from "../components/ui";
import { api } from "../lib/api";
import { formatTime } from "../lib/format";

interface VenueRow {
  code: string; name: string; location: string | null; capacity: number; indoor_outdoor: string | null; maintenance_status: string;
  availability: { start: string; end: string }[]; events: { code: string; name: string; start_at: string; end_at: string; registered: number }[]; over_capacity: number;
}
interface ResourceRow {
  code: string; kind: string; name: string; size: string | null; quantity: number; reserved_quantity: number; available: number; location: string | null; condition: string | null;
}

const tone = (status: string) => (status === "operational" ? "ok" as const : "bad" as const);

function VenueForm({ existing, onDone }: { existing?: VenueRow; onDone: () => void }) {
  const { base } = useFest();
  const [form, setForm] = useState({ name: existing?.name ?? "", location: existing?.location ?? "", capacity: String(existing?.capacity ?? 100), indoor_outdoor: existing?.indoor_outdoor ?? "indoor", maintenance_status: existing?.maintenance_status ?? "operational" });
  const set = (key: keyof typeof form) => (e: { target: { value: string } }) => setForm({ ...form, [key]: e.target.value });
  const save = useAction(() => api(existing ? `${base}/venues/${existing.code}` : `${base}/venues`, { method: existing ? "PATCH" : "POST", body: JSON.stringify({ ...form, capacity: Number(form.capacity), location: form.location || null }) }), { success: "Venue saved", onDone });
  return (
    <form className="form form-grid" onSubmit={(e: FormEvent) => { e.preventDefault(); save.mutate(); }}>
      <Field label="Name"><input required minLength={2} value={form.name} onChange={set("name")} /></Field>
      <Field label="Location"><input value={form.location} onChange={set("location")} /></Field>
      <Field label="Capacity"><input type="number" min={0} required value={form.capacity} onChange={set("capacity")} /></Field>
      <Field label="Setting"><select value={form.indoor_outdoor} onChange={set("indoor_outdoor")}><option value="indoor">Indoor</option><option value="outdoor">Outdoor</option></select></Field>
      <Field label="Maintenance"><select value={form.maintenance_status} onChange={set("maintenance_status")}><option value="operational">Operational</option><option value="maintenance">Under maintenance</option><option value="closed">Closed</option></select></Field>
      <div className="span-all"><ErrorNote error={save.error} /><button className="button-primary" disabled={save.isPending}>Save venue</button></div>
    </form>
  );
}

function VenueDrawer({ venue, onClose }: { venue: VenueRow; onClose: () => void }) {
  const { base, can } = useFest();
  const [editing, setEditing] = useState(false);
  const staff = useQuery({ queryKey: ["venue-staff", venue.code], queryFn: () => api<{ code: string; name: string }[]>(`${base}/venues/${venue.code}/staff`) });
  const remove = useAction(() => api(`${base}/venues/${venue.code}`, { method: "DELETE" }), { success: "Venue deleted", onDone: onClose });
  return (
    <Dialog title={venue.name} onClose={onClose} side>
      {editing ? <VenueForm existing={venue} onDone={() => setEditing(false)} /> : (
        <>
          <div className="row-actions">
            <Pill value={venue.maintenance_status} tone={tone(venue.maintenance_status)} />
            {can("venues.write") && <><button className="button-ghost" onClick={() => setEditing(true)}>Edit</button><button className="button-ghost danger" disabled={remove.isPending} onClick={() => remove.mutate()}>Delete</button></>}
          </div>
          <ErrorNote error={remove.error} />
          <Facts items={[["Capacity", venue.capacity], ["Setting", venue.indoor_outdoor], ["Location", venue.location], ["Events", venue.events.length], ["Over capacity", venue.over_capacity]]} />
          <h3>Availability</h3>
          <ul className="stack">{venue.availability.map((w) => <li key={w.start}><span>{formatTime(w.start)}</span><span className="muted small">to {formatTime(w.end)}</span></li>)}</ul>
          <h3>Assigned events</h3>
          <ul className="stack">{venue.events.map((e) => <li key={e.code}><div><strong>{e.name}</strong><br /><span className="muted small">{formatTime(e.start_at)} to {formatTime(e.end_at)}</span></div><span className={e.registered > venue.capacity ? "over-text" : ""}>{e.registered} / {venue.capacity}</span></li>)}</ul>
          <h3>Assigned staff</h3>
          <QueryState query={staff}>{staff.data?.length ? <p className="small">{staff.data.map((s) => s.name).join(", ")}</p> : <p className="muted small">None yet.</p>}</QueryState>
        </>
      )}
    </Dialog>
  );
}

function ResourceDialog({ existing, onClose }: { existing?: ResourceRow; onClose: () => void }) {
  const { base } = useFest();
  const [form, setForm] = useState({ name: existing?.name ?? "", kind: existing?.kind ?? "logistics", size: existing?.size ?? "", quantity: String(existing?.quantity ?? 1), location: existing?.location ?? "", condition: existing?.condition ?? "good" });
  const [allot, setAllot] = useState({ event_code: "", quantity: "1" });
  const set = (key: keyof typeof form) => (e: { target: { value: string } }) => setForm({ ...form, [key]: e.target.value });
  const events = useQuery({ queryKey: ["participant-options", base], queryFn: () => api<{ events: { code: string; name: string }[] }>(`${base}/participants/filters`) });
  const save = useAction(() => api(existing ? `${base}/resources/${existing.code}` : `${base}/resources`, { method: existing ? "PATCH" : "POST", body: JSON.stringify({ ...form, quantity: Number(form.quantity), size: form.size || null, location: form.location || null }) }), { success: "Resource saved", onDone: onClose });
  const allocate = useAction(() => api(`${base}/resources/${existing!.code}/allocate`, { method: "POST", body: JSON.stringify({ event_code: allot.event_code, quantity: Number(allot.quantity) }) }), { success: "Allocated to the event", onDone: onClose });
  return (
    <Dialog title={existing ? `${existing.name} · ${existing.code}` : "Add resource"} onClose={onClose}>
      <form className="form form-grid" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
        <Field label="Name"><input required minLength={2} value={form.name} onChange={set("name")} /></Field>
        <Field label="Quantity"><input type="number" min={0} required value={form.quantity} onChange={set("quantity")} /></Field>
        <Field label="Location"><input value={form.location} onChange={set("location")} /></Field>
        <Field label="Condition"><select value={form.condition} onChange={set("condition")}><option value="good">Good</option><option value="needs check">Needs check</option><option value="under repair">Under repair</option></select></Field>
        <div className="span-all"><ErrorNote error={save.error} /><button className="button-primary" disabled={save.isPending}>Save resource</button></div>
      </form>
      {existing && (
        <form className="form" onSubmit={(e) => { e.preventDefault(); allocate.mutate(); }}>
          <h3>Allocate to an event</h3>
          <p className="muted small">{existing.available} available. Stock that is not in good condition cannot be allocated.</p>
          <div className="inline-form">
            <select aria-label="Event" required value={allot.event_code} onChange={(e) => setAllot({ ...allot, event_code: e.target.value })}><option value="">Choose event</option>{events.data?.events.map((e) => <option key={e.code} value={e.code}>{e.name}</option>)}</select>
            <input aria-label="Quantity" type="number" min={1} required value={allot.quantity} onChange={(e) => setAllot({ ...allot, quantity: e.target.value })} />
            <button className="button-secondary" disabled={allocate.isPending}>Allocate</button>
          </div>
          <ErrorNote error={allocate.error} />
        </form>
      )}
    </Dialog>
  );
}

const resourceColumns: Column<ResourceRow>[] = [
  { label: "ID", cell: (r) => <span className="mono">{r.code}</span> },
  { label: "Item", cell: (r) => <strong>{r.name}{r.size ? ` (${r.size})` : ""}</strong> },
  { label: "Location", cell: (r) => r.location ?? "Unknown" },
  { label: "Total", cell: (r) => r.quantity, className: "num" },
  { label: "Reserved", cell: (r) => r.reserved_quantity, className: "num" },
  { label: "Available", cell: (r) => r.available, className: "num" },
  { label: "Condition", cell: (r) => (r.kind === "merch" ? "Merchandise" : <Pill value={r.condition ?? "unknown"} tone={r.condition === "good" ? "ok" : "warn"} />) },
];

export function Venues() {
  const { base, can } = useFest();
  const [tab, setTab] = useState<"venues" | "resources">("venues");
  const [selected, setSelected] = useState<string>();
  const [addVenue, setAddVenue] = useState(false);
  const [resource, setResource] = useState<ResourceRow | "new" | null>(null);
  const [search, setSearch] = useState("");
  const [kind, setKind] = useState("");
  const [condition, setCondition] = useState("");
  const [page, setPage] = useState(1);
  const q = useDebounced(search);
  useEffect(() => setPage(1), [q, kind, condition]);
  const venues = useQuery({ queryKey: ["venues", base], queryFn: () => api<VenueRow[]>(`${base}/venues`) });
  const resources = usePaged<ResourceRow>(`${base}/resources`, { q, kind, condition, page }, tab === "resources");
  const venue = venues.data?.find((v) => v.code === selected);
  return (
    <div>
      <PageHead title="Venues & Resources" subtitle="Availability is computed from condition and reservations">
        {can("venues.write") && (tab === "venues"
          ? <button className="button-primary small" onClick={() => setAddVenue(true)}><Plus size={16} /> Add venue</button>
          : <button className="button-primary small" onClick={() => setResource("new")}><Plus size={16} /> Add resource</button>)}
      </PageHead>
      <Tabs value={tab} onChange={setTab} options={[["venues", "Venues"], ["resources", "Resources"]]} />
      {tab === "venues" && (
        <QueryState query={venues}>
          <div className="card-grid">
            {venues.data?.map((v) => (
              <button key={v.code} className="card-button" onClick={() => setSelected(v.code)}>
                <span className="card-title"><strong>{v.name}</strong><Pill value={v.maintenance_status} tone={tone(v.maintenance_status)} /></span>
                <span className="kpi-value">{v.capacity}<span> seats</span></span>
                <span className="muted small">{v.events.length} events, {v.indoor_outdoor}</span>
                {v.over_capacity > 0 && <span className="over-text small">{v.over_capacity} over capacity</span>}
              </button>
            ))}
          </div>
        </QueryState>
      )}
      {tab === "resources" && (
        <>
          <div className="filters">
            <input type="search" aria-label="Search resources" placeholder="Search item or ID" value={search} onChange={(e) => setSearch(e.target.value)} />
            <select aria-label="Type" value={kind} onChange={(e) => setKind(e.target.value)}><option value="">All types</option><option value="logistics">Logistics</option><option value="merch">Merchandise</option></select>
            <select aria-label="Condition" value={condition} onChange={(e) => setCondition(e.target.value)}><option value="">Any condition</option><option value="good">Good</option><option value="needs check">Needs check</option><option value="under repair">Under repair</option></select>
          </div>
          <QueryState query={resources}>
            {resources.data && (<><DataTable rows={resources.data.items} columns={resourceColumns} rowKey={(r) => r.code} empty="No resources match these filters." onRow={can("venues.write") ? setResource : undefined} /><Pagination page={resources.data.page} pages={resources.data.pages} total={resources.data.total} onPage={setPage} /></>)}
          </QueryState>
        </>
      )}
      {venue && <VenueDrawer venue={venue} onClose={() => setSelected(undefined)} />}
      {addVenue && <Dialog title="Add venue" onClose={() => setAddVenue(false)}><VenueForm onDone={() => setAddVenue(false)} /></Dialog>}
      {resource && <ResourceDialog existing={resource === "new" ? undefined : resource} onClose={() => setResource(null)} />}
    </div>
  );
}

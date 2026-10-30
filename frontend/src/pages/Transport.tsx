import "leaflet/dist/leaflet.css";

import { useQuery } from "@tanstack/react-query";
import L from "leaflet";
import { Plus } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import {
  type Column, DataTable, Dialog, ErrorNote, Facts, Field, PageHead, Pagination, Pill, QueryState, Simulated, Tabs, fromInput, useAction, useFest, usePaged,
} from "../components/ui";
import { api } from "../lib/api";
import { formatTime } from "../lib/format";

interface RouteRow {
  code: string; name: string; pickup: string; destination: string; departure_at: string; expected_arrival_at: string; status: string; delay_minutes: number; is_simulated: boolean;
  vehicle: { code: string; vehicle_type: string; capacity: number } | null; passengers: number; points: [number, number][] | null;
}
interface RouteDetail extends RouteRow {
  riders: { code: string; name: string; college: string }[];
  affected_events: { code: string; name: string; start_at: string; passengers: number }[];
}
interface Vehicle { code: string; vehicle_type: string; capacity: number; status: string; is_simulated: boolean }

const TONE: Record<string, "ok" | "warn" | "bad"> = { delayed: "warn", cancelled: "bad", arrived: "ok" };

function RouteMap({ routes }: { routes: RouteRow[] }) {
  const holder = useRef<HTMLDivElement>(null);
  const mapped = routes.filter((r) => r.points);
  const unique = [...new Map(mapped.map((r) => [r.name, r])).values()];
  useEffect(() => {
    if (!holder.current || !unique.length) return;
    const map = L.map(holder.current, { scrollWheelZoom: false });
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19, attribution: "&copy; OpenStreetMap contributors" }).addTo(map);
    const bounds = L.latLngBounds([]);
    for (const r of unique) {
      const color = r.status === "delayed" ? "#f7b955" : "#4d84ff";
      L.polyline(r.points!, { color, weight: 3, dashArray: "6 6" }).addTo(map).bindPopup(r.name);
      r.points!.forEach((p, i) => {
        L.circleMarker(p, { radius: 6, color: "#fcf6ea", fillColor: color, fillOpacity: 1, weight: 2 }).addTo(map).bindPopup(i === 0 ? r.pickup : r.destination);
        bounds.extend(p);
      });
    }
    map.fitBounds(bounds, { padding: [30, 30] });
    return () => {
      map.remove();
    };
    // The map only needs rebuilding when the set of routes changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [unique.map((r) => r.name + r.status).join()]);
  if (!unique.length) return null;
  return (
    <section className="panel">
      <header><h2>Route map</h2><Simulated /></header>
      <p className="panel-note">Straight lines between approximate stops, not road routes. Nothing here is live vehicle tracking. Stops with no known location are left off.</p>
      <div ref={holder} className="map" role="region" aria-label="Map of shuttle routes between stops and campus" />
    </section>
  );
}

function RouteDrawer({ code, onClose }: { code: string; onClose: () => void }) {
  const { base, can } = useFest();
  const [delay, setDelay] = useState({ minutes: "30", reason: "" });
  const [passenger, setPassenger] = useState("");
  const query = useQuery({ queryKey: ["route", code], queryFn: () => api<RouteDetail>(`${base}/transport/routes/${code}`) });
  const report = useAction(() => api(`${base}/transport/routes/${code}/delay`, { method: "POST", body: JSON.stringify({ minutes: Number(delay.minutes), reason: delay.reason }) }), { success: "Delay recorded" });
  const add = useAction(() => api(`${base}/transport/routes/${code}/passengers`, { method: "POST", body: JSON.stringify({ participant_code: passenger.trim().toUpperCase() }) }), { success: "Passenger added", onDone: () => setPassenger("") });
  const remove = useAction((rider: string) => api(`${base}/transport/routes/${code}/passengers/${rider}`, { method: "DELETE" }), { success: "Passenger removed" });
  const r = query.data;
  return (
    <Dialog title={r ? `${r.name} · ${r.code}` : code} onClose={onClose} side>
      <QueryState query={query}>
        {r && (
          <>
            <div className="row-actions"><Pill value={r.status} tone={TONE[r.status]} />{r.is_simulated && <Simulated />}</div>
            <Facts items={[["From", r.pickup], ["To", r.destination], ["Departs", formatTime(r.departure_at)], ["Expected arrival", formatTime(r.expected_arrival_at)], ["Delay", `${r.delay_minutes} minutes`],
              ["Vehicle", r.vehicle ? `${r.vehicle.code}, ${r.vehicle.vehicle_type}` : "Not assigned"], ["Passengers", r.vehicle ? `${r.passengers} of ${r.vehicle.capacity}` : r.passengers]]} />
            <h3>Events at risk ({r.affected_events.length})</h3>
            {r.affected_events.length ? <ul className="stack">{r.affected_events.map((e) => <li key={e.code}><div><strong>{e.name}</strong><br /><span className="muted small">Starts {formatTime(e.start_at)}</span></div><span>{e.passengers} passengers</span></li>)}</ul> : <p className="muted small">No registered events start before these passengers can arrive.</p>}
            {can("transport.write") && (
              <form className="form" onSubmit={(e) => { e.preventDefault(); report.mutate(); }}>
                <h3>Report a delay</h3>
                <div className="inline-form">
                  <input aria-label="Delay in minutes" type="number" min={1} max={1440} required value={delay.minutes} onChange={(e) => setDelay({ ...delay, minutes: e.target.value })} />
                  <input aria-label="Reason" placeholder="Reason" required minLength={3} value={delay.reason} onChange={(e) => setDelay({ ...delay, reason: e.target.value })} />
                  <button className="button-secondary" disabled={report.isPending}>Report</button>
                </div>
                <ErrorNote error={report.error} />
              </form>
            )}
            <h3>Passengers ({r.riders.length})</h3>
            {can("transport.write") && (
              <form className="inline-form" onSubmit={(e) => { e.preventDefault(); add.mutate(); }}>
                <input aria-label="Participant ID" placeholder="Participant ID, such as P0123" required value={passenger} onChange={(e) => setPassenger(e.target.value)} />
                <button className="button-secondary" disabled={add.isPending}>Add</button>
              </form>
            )}
            <ErrorNote error={add.error || remove.error} />
            <ul className="stack scroll">{r.riders.map((p) => <li key={p.code}><div>{p.name} <span className="mono">{p.code}</span><br /><span className="muted small">{p.college}</span></div>{can("transport.write") && <button className="button-ghost" disabled={remove.isPending} onClick={() => remove.mutate(p.code)}>Remove</button>}</li>)}</ul>
          </>
        )}
      </QueryState>
    </Dialog>
  );
}

function RouteForm({ vehicles, onClose }: { vehicles: Vehicle[]; onClose: () => void }) {
  const { base } = useFest();
  const [form, setForm] = useState({ name: "", pickup: "", destination: "", vehicle_code: "", departure_at: "", expected_arrival_at: "" });
  const set = (key: keyof typeof form) => (e: { target: { value: string } }) => setForm({ ...form, [key]: e.target.value });
  const save = useAction(() => api(`${base}/transport/routes`, { method: "POST", body: JSON.stringify({ ...form, vehicle_code: form.vehicle_code || null, departure_at: fromInput(form.departure_at), expected_arrival_at: fromInput(form.expected_arrival_at) }) }), { success: "Route added", onDone: onClose });
  return (
    <Dialog title="Add route" onClose={onClose}>
      <form className="form form-grid" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
        <Field label="Route name"><input required minLength={2} value={form.name} onChange={set("name")} /></Field>
        <Field label="Vehicle"><select value={form.vehicle_code} onChange={set("vehicle_code")}><option value="">Not assigned</option>{vehicles.map((v) => <option key={v.code} value={v.code}>{v.code} ({v.capacity} seats)</option>)}</select></Field>
        <Field label="Pickup"><input required minLength={2} value={form.pickup} onChange={set("pickup")} /></Field>
        <Field label="Destination"><input required minLength={2} value={form.destination} onChange={set("destination")} /></Field>
        <Field label="Departs (IST)"><input type="datetime-local" required value={form.departure_at} onChange={set("departure_at")} /></Field>
        <Field label="Arrives (IST)"><input type="datetime-local" required value={form.expected_arrival_at} onChange={set("expected_arrival_at")} /></Field>
        <div className="span-all"><ErrorNote error={save.error} /><button className="button-primary" disabled={save.isPending}>Save route</button></div>
      </form>
    </Dialog>
  );
}

function VehicleForm({ onClose }: { onClose: () => void }) {
  const { base } = useFest();
  const [form, setForm] = useState({ code: "", vehicle_type: "Bus", capacity: "40" });
  const set = (key: keyof typeof form) => (e: { target: { value: string } }) => setForm({ ...form, [key]: e.target.value });
  const save = useAction(() => api(`${base}/transport/vehicles`, { method: "POST", body: JSON.stringify({ ...form, capacity: Number(form.capacity) }) }), { success: "Vehicle added", onDone: onClose });
  return (
    <Dialog title="Add vehicle" onClose={onClose}>
      <form className="form" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
        <Field label="Vehicle ID"><input required minLength={2} value={form.code} onChange={set("code")} /></Field>
        <Field label="Type"><input required minLength={2} value={form.vehicle_type} onChange={set("vehicle_type")} /></Field>
        <Field label="Seats"><input type="number" min={1} required value={form.capacity} onChange={set("capacity")} /></Field>
        <ErrorNote error={save.error} />
        <button className="button-primary" disabled={save.isPending}>Save vehicle</button>
      </form>
    </Dialog>
  );
}

const routeColumns: Column<RouteRow>[] = [
  { label: "Trip", cell: (r) => <span className="mono">{r.code}</span> },
  { label: "Route", cell: (r) => <strong>{r.name}</strong> },
  { label: "Departs", cell: (r) => formatTime(r.departure_at) },
  { label: "Arrives", cell: (r) => formatTime(r.expected_arrival_at) },
  { label: "Vehicle", cell: (r) => r.vehicle?.code ?? "Not assigned" },
  { label: "Passengers", cell: (r) => (r.vehicle ? `${r.passengers} / ${r.vehicle.capacity}` : r.passengers), className: "num" },
  { label: "Status", cell: (r) => <Pill value={r.status} tone={TONE[r.status]} /> },
];

const vehicleColumns: Column<Vehicle>[] = [
  { label: "ID", cell: (v) => <span className="mono">{v.code}</span> },
  { label: "Type", cell: (v) => v.vehicle_type },
  { label: "Seats", cell: (v) => v.capacity, className: "num" },
  { label: "Status", cell: (v) => <Pill value={v.status} tone={v.status === "available" ? "ok" : "warn"} /> },
];

export function Transport() {
  const { base, can } = useFest();
  const [tab, setTab] = useState<"routes" | "vehicles">("routes");
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(1);
  const [opened, setOpened] = useState<string>();
  const [adding, setAdding] = useState<"route" | "vehicle" | null>(null);
  useEffect(() => setPage(1), [status]);
  const routes = usePaged<RouteRow>(`${base}/transport/routes`, { status, page });
  const vehicles = useQuery({ queryKey: ["vehicles", base], queryFn: () => api<Vehicle[]>(`${base}/transport/vehicles`) });
  return (
    <div>
      <PageHead title="Transportation" subtitle="All vehicles and trips here are simulated: the pack has no transport data">
        {can("transport.write") && <><button className="button-ghost" onClick={() => setAdding("vehicle")}><Plus size={16} /> Add vehicle</button><button className="button-primary small" onClick={() => setAdding("route")}><Plus size={16} /> Add route</button></>}
      </PageHead>
      {routes.data && <RouteMap routes={routes.data.items} />}
      <Tabs value={tab} onChange={setTab} options={[["routes", "Trips"], ["vehicles", "Vehicles"]]} />
      {tab === "routes" && (
        <>
          <div className="filters"><select aria-label="Status" value={status} onChange={(e) => setStatus(e.target.value)}><option value="">All statuses</option>{["scheduled", "in_transit", "delayed", "arrived", "cancelled"].map((s) => <option key={s} value={s}>{s.replace("_", " ")}</option>)}</select></div>
          <QueryState query={routes}>{routes.data && <><DataTable rows={routes.data.items} columns={routeColumns} rowKey={(r) => r.code} empty="No trips match." onRow={(r) => setOpened(r.code)} /><Pagination page={routes.data.page} pages={routes.data.pages} total={routes.data.total} onPage={setPage} /></>}</QueryState>
        </>
      )}
      {tab === "vehicles" && <QueryState query={vehicles}>{vehicles.data && <DataTable rows={vehicles.data} columns={vehicleColumns} rowKey={(v) => v.code} empty="No vehicles yet." />}</QueryState>}
      {opened && <RouteDrawer code={opened} onClose={() => setOpened(undefined)} />}
      {adding === "route" && <RouteForm vehicles={vehicles.data ?? []} onClose={() => setAdding(null)} />}
      {adding === "vehicle" && <VehicleForm onClose={() => setAdding(null)} />}
    </div>
  );
}

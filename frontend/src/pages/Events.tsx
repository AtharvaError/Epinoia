import { useQuery } from "@tanstack/react-query";
import { Activity, CalendarPlus } from "lucide-react";
import { type FormEvent, type PointerEvent as ReactPointerEvent, useEffect, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";

import {
  type Column, DataTable, Dialog, ErrorNote, Facts, Field, Loading, PageHead, Pagination, Pill, QueryState, queryString, ReasonForm, Tabs,
  fromInput, toInput, useAction, useDebounced, useFest, usePaged,
} from "../components/ui";
import { api, ApiError } from "../lib/api";
import { formatTime } from "../lib/format";

interface EventRow {
  code: string; name: string; category: string | null; participation_type: string; description: string | null; start_at: string | null; end_at: string | null;
  status: string; schedule_source: string | null; rounds: string[]; duration_minutes: number; venue_requirements: string | null; equipment_needs: string | null;
  volunteers_needed: number; judges: string[]; dependencies: string[]; coordinator_id: number | null; venue: { code: string; name: string; capacity: number } | null;
  registered: number; staffed: number;
}
interface EventDetail extends EventRow {
  clashing_venue_events: { code: string; name: string }[];
  shifts: { volunteer: string; code: string; task: string | null; attendance: string }[];
  resources: { code: string; name: string; quantity: number }[];
}
interface Options { coordinators: { id: number; name: string }[]; venues: { code: string; name: string; capacity: number }[] }
interface Conflicts {
  venue: { first_name: string; second_name: string; venue: string }[];
  participants: { first_code: string; first_name: string; second_code: string; second_name: string; people: number }[];
  capacity: { code: string; name: string; venue: string; capacity: number; registered: number }[];
}
interface Schedule { venues: { code: string; name: string; capacity: number }[]; events: EventRow[] }

const STATUS_TONE: Record<string, "ok" | "warn" | "bad" | "info"> = { live: "ok", completed: "info", postponed: "warn", cancelled: "bad" };
const DAY_START = 8;
const DAY_HOURS = 14;
const SNAP_PER_HOUR = 4;
const DRAG_THRESHOLD = 5;
const istHours = (iso: string) => {
  const d = new Date(new Date(iso).getTime() + 5.5 * 3600 * 1000);
  return d.getUTCHours() + d.getUTCMinutes() / 60;
};
const clock = (hours: number) => `${String(Math.floor(hours)).padStart(2, "0")}:${String(Math.round((hours % 1) * 60)).padStart(2, "0")}`;

function EventForm({ existing, options, onDone }: { existing?: EventRow; options: Options; onDone: () => void }) {
  const { base } = useFest();
  const [form, setForm] = useState({
    name: existing?.name ?? "", participation_type: existing?.participation_type ?? "solo", category: existing?.category ?? "",
    description: existing?.description ?? "", start_at: toInput(existing?.start_at), end_at: toInput(existing?.end_at), venue_code: existing?.venue?.code ?? "",
    coordinator_id: existing?.coordinator_id?.toString() ?? "", judges: existing?.judges.join(", ") ?? "", dependencies: existing?.dependencies.join(", ") ?? "",
    volunteers_needed: String(existing?.volunteers_needed ?? 0), duration_minutes: String(existing?.duration_minutes ?? 60), status: existing?.status ?? "scheduled", override_reason: "",
  });
  const set = (key: keyof typeof form) => (e: { target: { value: string } }) => setForm({ ...form, [key]: e.target.value });
  const list = (text: string) => text.split(",").map((x) => x.trim()).filter(Boolean);
  const save = useAction(() => api(existing ? `${base}/events/${existing.code}` : `${base}/events`, {
    method: existing ? "PATCH" : "POST",
    body: JSON.stringify({
      ...form, category: form.category || null, description: form.description || null, start_at: fromInput(form.start_at), end_at: fromInput(form.end_at),
      venue_code: form.venue_code || null, coordinator_id: form.coordinator_id ? Number(form.coordinator_id) : null, judges: list(form.judges), dependencies: list(form.dependencies),
      volunteers_needed: Number(form.volunteers_needed), duration_minutes: Number(form.duration_minutes), override_reason: form.override_reason || null,
    }),
  }), { success: existing ? "Event updated" : "Event created", onDone });
  const conflicted = save.error instanceof ApiError && save.error.conflicts.length > 0;
  return (
    <form className="form form-grid" onSubmit={(e: FormEvent) => { e.preventDefault(); save.mutate(); }}>
      <Field label="Event name"><input required minLength={2} value={form.name} onChange={set("name")} /></Field>
      <Field label="Type"><select value={form.participation_type} onChange={set("participation_type")}><option value="solo">Solo</option><option value="team">Team</option></select></Field>
      <Field label="Category"><input value={form.category} onChange={set("category")} /></Field>
      <Field label="Status"><select value={form.status} onChange={set("status")}>{["scheduled", "live", "completed", "postponed", "cancelled"].map((s) => <option key={s} value={s}>{s[0].toUpperCase() + s.slice(1)}</option>)}</select></Field>
      <Field label="Start (IST)"><input type="datetime-local" value={form.start_at} onChange={set("start_at")} /></Field>
      <Field label="End (IST)"><input type="datetime-local" value={form.end_at} onChange={set("end_at")} /></Field>
      <Field label="Venue"><select value={form.venue_code} onChange={set("venue_code")}><option value="">Not assigned</option>{options.venues.map((v) => <option key={v.code} value={v.code}>{v.name} ({v.capacity} seats)</option>)}</select></Field>
      <Field label="Coordinator"><select value={form.coordinator_id} onChange={set("coordinator_id")}><option value="">Not assigned</option>{options.coordinators.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</select></Field>
      <Field label="Volunteers needed"><input type="number" min={0} value={form.volunteers_needed} onChange={set("volunteers_needed")} /></Field>
      <Field label="Duration per round (minutes)"><input type="number" min={5} value={form.duration_minutes} onChange={set("duration_minutes")} /></Field>
      <Field label="Judges" hint="Separate with commas"><input value={form.judges} onChange={set("judges")} /></Field>
      <Field label="Depends on" hint="Event codes, separated with commas"><input value={form.dependencies} onChange={set("dependencies")} /></Field>
      <div className="span-all"><Field label="Description"><textarea rows={2} value={form.description} onChange={set("description")} /></Field></div>
      <div className="span-all">
        <ErrorNote error={save.error} />
        {conflicted && <Field label="Reason to override these conflicts"><textarea rows={2} minLength={5} value={form.override_reason} onChange={set("override_reason")} /></Field>}
        <button className="button-primary" disabled={save.isPending}>{conflicted ? "Save with override" : "Save event"}</button>
      </div>
    </form>
  );
}

function EventDrawer({ code, options, onClose }: { code: string; options?: Options; onClose: () => void }) {
  const { base, can } = useFest();
  const [view, setView] = useState<"detail" | "edit" | "delete">("detail");
  const query = useQuery({ queryKey: ["event", code], queryFn: () => api<EventDetail>(`${base}/events/${code}`) });
  const remove = useAction((reason: string) => api(`${base}/events/${code}`, { method: "DELETE", body: JSON.stringify({ reason }) }), { success: "Event deleted", onDone: onClose });
  const e = query.data;
  const over = e?.venue && e.registered > e.venue.capacity;
  return (
    <Dialog title={e ? `${e.name} · ${e.code}` : code} onClose={onClose} side>
      <QueryState query={query}>
        {e && view === "edit" && options && <EventForm existing={e} options={options} onDone={() => setView("detail")} />}
        {e && view === "delete" && <ReasonForm label="Why is this event being deleted?" action="Delete event" danger onSubmit={(r) => remove.mutate(r)} pending={remove.isPending} error={remove.error} />}
        {e && view === "detail" && (
          <>
            <div className="row-actions">
              <Pill value={e.status} tone={STATUS_TONE[e.status]} />
              {e.schedule_source === "generated" && <span className="source source-generated">Generated</span>}
              {can("simulation.run") && <Link className="button-ghost" to={`/ripple${queryString({ scenario: "venue_change", event_code: e.code, venue_code: e.venue?.code })}`}><Activity size={14} aria-hidden="true" /> Simulate a change</Link>}
              {can("events.write") && <><button className="button-ghost" onClick={() => setView("edit")}>Edit</button><button className="button-ghost danger" onClick={() => setView("delete")}>Delete</button></>}
            </div>
            {over && <p className="form-error">{e.registered} are registered and {e.venue!.name} has {e.venue!.capacity} seats.</p>}
            {e.clashing_venue_events.length > 0 && <p className="form-error">Venue clash with {e.clashing_venue_events.map((c) => c.name).join(", ")}.</p>}
            <Facts items={[
              ["Start", e.start_at && formatTime(e.start_at)], ["End", e.end_at && formatTime(e.end_at)], ["Venue", e.venue?.name], ["Type", e.participation_type],
              ["Rounds", e.rounds.join(", ")], ["Per round", `${e.duration_minutes} minutes`], ["Registered", e.registered], ["Volunteers", `${e.staffed} of ${e.volunteers_needed}`],
              ["Venue needs", e.venue_requirements], ["Equipment", e.equipment_needs], ["Judges", e.judges.length ? e.judges.join(", ") : "None assigned"], ["Depends on", e.dependencies.length ? e.dependencies.join(", ") : "None"],
            ]} />
            <h3>Assigned volunteers ({e.shifts.length})</h3>
            {e.shifts.length ? <ul className="stack">{e.shifts.map((s) => <li key={s.code}><span>{s.volunteer}</span><Pill value={s.attendance} /></li>)}</ul> : <p className="muted small">None yet.</p>}
            <h3>Allocated resources ({e.resources.length})</h3>
            {e.resources.length ? <ul className="stack">{e.resources.map((r) => <li key={r.code}><span>{r.name}</span><span>{r.quantity}</span></li>)}</ul> : <p className="muted small">None yet.</p>}
          </>
        )}
      </QueryState>
    </Dialog>
  );
}

interface Drag { code: string; hours: number; venue: string; offsetY: number }

function DaySchedule({ onOpen }: { onOpen: (code: string) => void }) {
  const { base, festival, can } = useFest();
  const navigate = useNavigate();
  const days: string[] = [];
  for (const d = new Date(`${festival.start_date}T00:00:00Z`); d <= new Date(`${festival.end_date}T00:00:00Z`); d.setUTCDate(d.getUTCDate() + 1)) days.push(d.toISOString().slice(0, 10));
  const [day, setDay] = useState(days[0]);
  const [drag, setDrag] = useState<Drag>();
  const dragged = useRef(false);
  const query = useQuery({ queryKey: ["schedule", base, day], queryFn: () => api<Schedule>(`${base}/events/schedule?day=${day}`) });

  // Dropping never changes the schedule. It opens Ripple, which shows the impact first.
  const startDrag = (pointer: ReactPointerEvent<HTMLButtonElement>, event: EventRow, venue: string) => {
    if (!can("simulation.run") || pointer.button !== 0 || pointer.pointerType === "touch") return; // Touch scrolls the timeline. The event drawer offers the same simulation.
    const hourWidth = pointer.currentTarget.parentElement!.getBoundingClientRect().width / DAY_HOURS;
    const { clientX: startX, clientY: startY } = pointer;
    const from = istHours(event.start_at!);
    const length = (new Date(event.end_at!).getTime() - new Date(event.start_at!).getTime()) / 3600000;
    let latest: Drag = { code: event.code, hours: from, venue, offsetY: 0 };
    dragged.current = false;
    const move = (e: PointerEvent) => {
      if (!dragged.current && Math.hypot(e.clientX - startX, e.clientY - startY) < DRAG_THRESHOLD) return;
      dragged.current = true;
      const snapped = Math.round((from + (e.clientX - startX) / hourWidth) * SNAP_PER_HOUR) / SNAP_PER_HOUR;
      const hours = Math.min(DAY_START + DAY_HOURS - length, Math.max(DAY_START, snapped));
      const row = document.elementFromPoint(e.clientX, e.clientY)?.closest<HTMLElement>("[data-venue]");
      latest = { ...latest, hours, venue: row?.dataset.venue ?? latest.venue, offsetY: e.clientY - startY };
      setDrag(latest);
    };
    const end = (e: PointerEvent) => {
      if (e.type === "pointerup") move(e);
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", end);
      window.removeEventListener("pointercancel", end);
      setDrag(undefined);
      if (e.type !== "pointerup" || (latest.hours === from && latest.venue === venue)) return;
      const shift = (latest.hours - from) * 3600000;
      navigate(`/ripple${queryString({
        scenario: "venue_change", event_code: event.code, venue_code: latest.venue, run: 1,
        start_at: toInput(new Date(new Date(event.start_at!).getTime() + shift).toISOString()), end_at: toInput(new Date(new Date(event.end_at!).getTime() + shift).toISOString()),
      })}`);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", end);
    window.addEventListener("pointercancel", end);
  };

  return (
    <>
      <div className="filters">{days.map((d, i) => <button key={d} className={d === day ? "chip active" : "chip"} onClick={() => setDay(d)}>Day {i + 1}, {d.slice(8)} {new Date(`${d}T00:00:00Z`).toLocaleString("en-IN", { month: "short", timeZone: "UTC" })}</button>)}</div>
      <QueryState query={query}>
        {query.data && (
          <div className="timeline" role="table" aria-label={`Schedule for ${day}`}>
            <div className="timeline-hours" role="row"><span role="columnheader">Venue</span><div>{Array.from({ length: DAY_HOURS / 2 + 1 }, (_, i) => <i key={i} style={{ left: `${(i * 2 * 100) / DAY_HOURS}%` }}>{String(DAY_START + i * 2).padStart(2, "0")}:00</i>)}</div></div>
            {query.data.venues.map((v) => (
              <div key={v.code} className="timeline-row" role="row">
                <span role="rowheader"><strong>{v.name}</strong><small>{v.capacity} seats</small></span>
                <div className={`timeline-track ${drag?.venue === v.code ? "drop" : ""}`} data-venue={v.code}>
                  {query.data.events.filter((e) => e.venue?.code === v.code && e.start_at && e.end_at).map((e) => {
                    const moving = drag?.code === e.code ? drag : undefined;
                    const start = Math.max(0, (moving?.hours ?? istHours(e.start_at!)) - DAY_START);
                    const width = Math.min(DAY_HOURS - start, (new Date(e.end_at!).getTime() - new Date(e.start_at!).getTime()) / 3600000);
                    return (
                      <button key={e.code} className={`block ${e.registered > v.capacity ? "over" : ""} ${moving ? "dragging" : ""}`}
                              style={{ left: `${(start * 100) / DAY_HOURS}%`, width: `${(width * 100) / DAY_HOURS}%`, transform: moving ? `translateY(${moving.offsetY}px)` : undefined }}
                              title={`${e.name}: ${formatTime(e.start_at!)}, ${e.registered} registered`} onPointerDown={(p) => startDrag(p, e, v.code)}
                              onClick={() => { if (!dragged.current) onOpen(e.code); dragged.current = false; }}>
                        {moving ? `${clock(moving.hours)} ${e.name}` : e.name}
                      </button>
                    );
                  })}
                </div>
              </div>
            ))}
          </div>
        )}
      </QueryState>
      <p className="muted small">{can("simulation.run") && "Drag a block to a new time or venue to see what it would break. Nothing changes until it is approved. "}Red blocks have more registrants than seats. Each event runs all its rounds in this slot, as stated in the assumptions log.</p>
    </>
  );
}

function ConflictList({ onOpen }: { onOpen: (code: string) => void }) {
  const { base } = useFest();
  const query = useQuery({ queryKey: ["conflicts", base], queryFn: () => api<Conflicts>(`${base}/events/conflicts`) });
  const data = query.data;
  return (
    <QueryState query={query}>
      {data && (
        <div className="chart-grid">
          <section className="panel"><header><h2>Venue double-bookings</h2><span className="muted small">{data.venue.length}</span></header>
            {data.venue.length ? <ul className="stack">{data.venue.map((v) => <li key={v.first_name + v.second_name}><span>{v.first_name} and {v.second_name}</span><span className="muted small">{v.venue}</span></li>)}</ul> : <p className="muted small">No venue is double-booked.</p>}
          </section>
          <section className="panel"><header><h2>Over capacity</h2><span className="muted small">{data.capacity.length}</span></header>
            <ul className="stack scroll">{data.capacity.map((c) => <li key={c.code}><button className="link" onClick={() => onOpen(c.code)}>{c.name}</button><span className="muted small">{c.registered} for {c.capacity} at {c.venue}</span></li>)}</ul>
          </section>
          <section className="panel wide"><header><h2>Participant clashes</h2><span className="muted small">{data.participants.length} event pairs</span></header>
            <ul className="stack scroll">{data.participants.map((p) => <li key={p.first_code + p.second_code}><span><button className="link" onClick={() => onOpen(p.first_code)}>{p.first_name}</button> and <button className="link" onClick={() => onOpen(p.second_code)}>{p.second_name}</button></span><span className="muted small">{p.people} people</span></li>)}</ul>
          </section>
        </div>
      )}
    </QueryState>
  );
}

const columns: Column<EventRow>[] = [
  { label: "ID", sort: "code", cell: (e) => <span className="mono">{e.code}</span> },
  { label: "Event", sort: "name", cell: (e) => <strong>{e.name}</strong> },
  { label: "Starts", sort: "start", cell: (e) => (e.start_at ? formatTime(e.start_at) : "Not scheduled") },
  { label: "Venue", sort: "venue", cell: (e) => e.venue?.name ?? "Not assigned" },
  { label: "Registered", sort: "registered", cell: (e) => <span className={e.venue && e.registered > e.venue.capacity ? "over-text" : ""}>{e.registered}{e.venue ? ` / ${e.venue.capacity}` : ""}</span>, className: "num" },
  { label: "Volunteers", sort: "staffed", cell: (e) => <span className={e.staffed < e.volunteers_needed ? "warn-text" : ""}>{e.staffed} / {e.volunteers_needed}</span>, className: "num" },
  { label: "Status", sort: "status", cell: (e) => <Pill value={e.status} tone={STATUS_TONE[e.status]} /> },
];

export function Events() {
  const { base, can } = useFest();
  const [params, setParams] = useSearchParams();
  const [tab, setTab] = useState<"list" | "schedule" | "conflicts">(params.get("tab") === "conflicts" ? "conflicts" : "list");
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("");
  const [venue, setVenue] = useState("");
  const [sort, setSort] = useState("");
  const [page, setPage] = useState(1);
  const [adding, setAdding] = useState(false);
  const q = useDebounced(search);
  useEffect(() => setPage(1), [q, status, venue, sort]);
  const query = usePaged<EventRow>(`${base}/events`, { q, status, venue, sort, page });
  const options = useQuery({ queryKey: ["event-options", base], queryFn: () => api<Options>(`${base}/events/options`) });
  const open = (code: string) => setParams({ open: code });
  const opened = params.get("open");
  return (
    <div>
      <PageHead title="Events & Schedule" subtitle="The pack has no times, so the schedule is generated and can be changed">
        {can("events.write") && <button className="button-primary small" onClick={() => setAdding(true)}><CalendarPlus size={16} /> Add event</button>}
      </PageHead>
      <Tabs value={tab} onChange={setTab} options={[["list", "All events"], ["schedule", "Daily schedule"], ["conflicts", "Conflicts"]]} />
      {tab === "list" && (
        <>
          <div className="filters">
            <input type="search" aria-label="Search events" placeholder="Search name or ID" value={search} onChange={(e) => setSearch(e.target.value)} />
            <select aria-label="Status" value={status} onChange={(e) => setStatus(e.target.value)}><option value="">All statuses</option>{["scheduled", "live", "completed", "postponed", "cancelled"].map((s) => <option key={s} value={s}>{s[0].toUpperCase() + s.slice(1)}</option>)}</select>
            <select aria-label="Venue" value={venue} onChange={(e) => setVenue(e.target.value)}><option value="">All venues</option>{options.data?.venues.map((v) => <option key={v.code} value={v.code}>{v.name}</option>)}</select>
          </div>
          <QueryState query={query}>
            {query.data && (<><DataTable rows={query.data.items} columns={columns} rowKey={(e) => e.code} empty="No events match these filters." onRow={(e) => open(e.code)} sort={sort} onSort={setSort} /><Pagination page={query.data.page} pages={query.data.pages} total={query.data.total} onPage={setPage} /></>)}
          </QueryState>
        </>
      )}
      {tab === "schedule" && <DaySchedule onOpen={open} />}
      {tab === "conflicts" && <ConflictList onOpen={open} />}
      {opened && <EventDrawer code={opened} options={options.data} onClose={() => setParams({})} />}
      {adding && (options.data ? <Dialog title="Add event" onClose={() => setAdding(false)} side><EventForm options={options.data} onDone={() => setAdding(false)} /></Dialog> : <Loading />)}
    </div>
  );
}

import { useQuery } from "@tanstack/react-query";
import { Download, QrCode, Upload, UserPlus } from "lucide-react";
import { type FormEvent, useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import {
  type Column, DataTable, Dialog, ErrorNote, Facts, Field, Loading, notify, PageHead, Pagination, Pill, QueryState,
  queryString, ReasonForm, toInput, fromInput, useAction, useDebounced, useFest, usePaged,
} from "../components/ui";
import { api } from "../lib/api";
import { formatTime } from "../lib/format";

interface Row {
  code: string; full_name: string; college: string; department: string | null; age: number | null; registration_status: string;
  checkin_status: string; accommodation_required: boolean | null; transport_required: boolean | null; event_count: number;
  email: string | null; phone: string | null; gender_as_given: string | null; accessibility_needs: string | null; dietary_needs: string | null;
  arrival_at: string | null; departure_at: string | null; tshirt_size: string | null; has_pass: boolean; restricted: boolean;
}
interface Detail extends Row {
  events: { code: string; name: string; start_at: string | null; venue: string | null; team_code: string | null; clashes: string[]; off_campus: boolean }[];
  contingent: { code: string; college: string; leader: string | null } | null;
  accommodation: { room: string; hostel: string; status: string } | null;
  transport: { code: string; name: string; departure_at: string; status: string }[];
  issues: { issue: string; detail: string | null }[];
}
interface Options { contingents: { code: string; college: string }[]; events: { code: string; name: string }[] }

const yesNo = (value: boolean | null) => (value === null ? "Unknown" : value ? "Yes" : "No");
const blankable = (value: string | null, restricted: boolean) => (restricted ? <span className="restricted">Restricted</span> : value);

function ParticipantForm({ existing, options, onDone }: { existing?: Detail; options?: Options; onDone: () => void }) {
  const { base } = useFest();
  const [form, setForm] = useState({
    full_name: existing?.full_name ?? "", college: existing?.college ?? "", contingent_code: existing?.contingent?.code ?? "",
    email: existing?.email ?? "", phone: existing?.phone ?? "", department: existing?.department ?? "",
    gender_as_given: existing?.gender_as_given ?? "", age: existing?.age?.toString() ?? "",
    accessibility_needs: existing?.accessibility_needs ?? "", dietary_needs: existing?.dietary_needs ?? "",
    arrival_at: toInput(existing?.arrival_at), departure_at: toInput(existing?.departure_at), tshirt_size: existing?.tshirt_size ?? "",
    accommodation_required: existing?.accommodation_required == null ? "" : String(existing.accommodation_required),
    transport_required: existing?.transport_required == null ? "" : String(existing.transport_required),
    registration_status: existing?.registration_status ?? "registered",
  });
  const set = (key: keyof typeof form) => (e: { target: { value: string } }) => setForm({ ...form, [key]: e.target.value });
  const save = useAction(() => {
    const body = {
      ...Object.fromEntries(Object.entries(form).map(([k, v]) => [k, v === "" ? null : v])),
      registration_status: form.registration_status,
      age: form.age ? Number(form.age) : null, arrival_at: fromInput(form.arrival_at), departure_at: fromInput(form.departure_at),
      accommodation_required: form.accommodation_required === "" ? null : form.accommodation_required === "true",
      transport_required: form.transport_required === "" ? null : form.transport_required === "true",
    };
    return api(existing ? `${base}/participants/${existing.code}` : `${base}/participants`, { method: existing ? "PATCH" : "POST", body: JSON.stringify(body) });
  }, { success: existing ? "Participant updated" : "Participant added", onDone });
  const submit = (e: FormEvent) => {
    e.preventDefault();
    save.mutate();
  };
  const choice = (key: "accommodation_required" | "transport_required") => (
    <select value={form[key]} onChange={set(key)}><option value="">Unknown</option><option value="true">Yes</option><option value="false">No</option></select>
  );
  return (
    <form className="form form-grid" onSubmit={submit}>
      <Field label="Full name"><input required minLength={2} value={form.full_name} onChange={set("full_name")} /></Field>
      <Field label="College"><input required minLength={2} value={form.college} onChange={set("college")} /></Field>
      <Field label="Contingent">
        <select value={form.contingent_code} onChange={set("contingent_code")}>
          <option value="">None</option>
          {options?.contingents.map((c) => <option key={c.code} value={c.code}>{c.code} · {c.college}</option>)}
        </select>
      </Field>
      <Field label="Department"><input value={form.department} onChange={set("department")} /></Field>
      <Field label="Email"><input type="email" value={form.email} onChange={set("email")} /></Field>
      <Field label="Phone"><input value={form.phone} onChange={set("phone")} /></Field>
      <Field label="Gender as given" hint="Leave blank if not stated"><input value={form.gender_as_given} onChange={set("gender_as_given")} /></Field>
      <Field label="Age"><input type="number" min={10} max={100} value={form.age} onChange={set("age")} /></Field>
      <Field label="Accessibility needs"><input value={form.accessibility_needs} onChange={set("accessibility_needs")} /></Field>
      <Field label="Dietary needs"><input value={form.dietary_needs} onChange={set("dietary_needs")} /></Field>
      <Field label="Arrival (IST)"><input type="datetime-local" value={form.arrival_at} onChange={set("arrival_at")} /></Field>
      <Field label="Departure (IST)"><input type="datetime-local" value={form.departure_at} onChange={set("departure_at")} /></Field>
      <Field label="Needs accommodation">{choice("accommodation_required")}</Field>
      <Field label="Needs transport">{choice("transport_required")}</Field>
      <Field label="Registration status">
        <select value={form.registration_status} onChange={set("registration_status")}>
          <option value="registered">Registered</option><option value="confirmed">Confirmed</option><option value="cancelled">Cancelled</option>
        </select>
      </Field>
      <Field label="T-shirt size"><input maxLength={10} value={form.tshirt_size} onChange={set("tshirt_size")} /></Field>
      <div className="span-all"><ErrorNote error={save.error} /><button className="button-primary" disabled={save.isPending}>{save.isPending ? "Saving" : "Save participant"}</button></div>
    </form>
  );
}

function ParticipantDetail({ code, options, onClose }: { code: string; options?: Options; onClose: () => void }) {
  const { base, can } = useFest();
  const [view, setView] = useState<"detail" | "edit" | "delete">("detail");
  const [eventCode, setEventCode] = useState("");
  const query = useQuery({ queryKey: ["participant", code], queryFn: () => api<Detail>(`${base}/participants/${code}`) });
  const addEvent = useAction(() => api<{ warnings: string[] }>(`${base}/participants/${code}/registrations`, { method: "POST", body: JSON.stringify({ event_code: eventCode }) }), {
    success: (r) => (r.warnings.length ? `Registered. ${r.warnings.join(". ")}` : "Registered for the event"), onDone: () => setEventCode(""),
  });
  const removeEvent = useAction((event: string) => api(`${base}/participants/${code}/registrations/${event}`, { method: "DELETE" }), { success: "Removed from the event" });
  const remove = useAction((reason: string) => api(`${base}/participants/${code}`, { method: "DELETE", body: JSON.stringify({ reason }) }), { success: "Participant removed", onDone: onClose });
  const p = query.data;
  return (
    <Dialog title={p ? `${p.full_name} · ${p.code}` : code} onClose={onClose} side>
      <QueryState query={query}>
        {p && view === "edit" && <ParticipantForm existing={p} options={options} onDone={() => setView("detail")} />}
        {p && view === "delete" && <ReasonForm label="Why is this participant being removed?" action="Remove participant" danger onSubmit={(r) => remove.mutate(r)} pending={remove.isPending} error={remove.error} />}
        {p && view === "detail" && (
          <>
            <div className="row-actions">
              <Pill value={p.registration_status} tone={p.registration_status === "cancelled" ? "bad" : "ok"} />
              <Pill value={p.checkin_status} tone={p.checkin_status === "checked_in" ? "ok" : undefined} />
              {can("participants.write") && <><button className="button-ghost" onClick={() => setView("edit")}>Edit</button><button className="button-ghost danger" onClick={() => setView("delete")}>Remove</button></>}
            </div>
            <Facts items={[
              ["College", p.college], ["Contingent", p.contingent ? `${p.contingent.code}, led by ${p.contingent.leader ?? "unknown"}` : null], ["Age", p.age],
              ["Phone", blankable(p.phone, p.restricted)], ["Email", blankable(p.email, p.restricted)], ["Gender as given", blankable(p.gender_as_given, p.restricted)],
              ["Accessibility", blankable(p.accessibility_needs, p.restricted)], ["Dietary", blankable(p.dietary_needs, p.restricted)],
              ["Arrival", p.arrival_at && formatTime(p.arrival_at)], ["Departure", p.departure_at && formatTime(p.departure_at)],
              ["Accommodation", p.accommodation ? `${p.accommodation.hostel}, ${p.accommodation.room}` : `Needed: ${yesNo(p.accommodation_required)}`],
              ["Transport", p.transport.length ? p.transport.map((t) => t.name).join(", ") : `Needed: ${yesNo(p.transport_required)}`], ["T-shirt", p.tshirt_size],
            ]} />
            <h3>Registered events ({p.events.length})</h3>
            <ul className="stack">
              {p.events.map((e) => (
                <li key={e.code}>
                  <div><strong>{e.name}</strong> <span className="muted small">{e.code}</span><br /><span className="muted small">{e.start_at ? `${formatTime(e.start_at)}, ${e.venue}` : "Not scheduled"}</span></div>
                  <div className="row-actions">
                    {e.clashes.length > 0 && <Pill value={`clash ${e.clashes.join(", ")}`} tone="warn" />}
                    {e.off_campus && <Pill value="outside stay" tone="bad" />}
                    {can("participants.write") && <button className="button-ghost" disabled={removeEvent.isPending} onClick={() => removeEvent.mutate(e.code)}>Remove</button>}
                  </div>
                </li>
              ))}
            </ul>
            {can("participants.write") && (
              <form className="inline-form" onSubmit={(e) => { e.preventDefault(); addEvent.mutate(); }}>
                <select aria-label="Event to add" required value={eventCode} onChange={(e) => setEventCode(e.target.value)}>
                  <option value="">Add to an event</option>
                  {options?.events.map((e) => <option key={e.code} value={e.code}>{e.name}</option>)}
                </select>
                <button className="button-secondary" disabled={addEvent.isPending}>Add</button>
              </form>
            )}
            <ErrorNote error={addEvent.error || removeEvent.error} />
            {p.issues.length > 0 && (<><h3>Data to review</h3><ul className="stack">{p.issues.map((i) => <li key={i.issue}><div><Pill value={i.issue} tone="warn" /> <span className="muted small">{i.detail}</span></div></li>)}</ul></>)}
            <h3>Registration pass</h3>
            <img className="qr" src={`${base}/participants/${p.code}/pass.svg`} alt={`QR pass for ${p.full_name}`} width={180} height={180} />
          </>
        )}
      </QueryState>
    </Dialog>
  );
}

function ImportDialog({ onClose }: { onClose: () => void }) {
  const { base } = useFest();
  const file = useRef<HTMLInputElement>(null);
  const upload = useAction(() => {
    const data = new FormData();
    data.append("file", file.current!.files![0]);
    return api<{ created: number; rejected: { row: number; reason: string }[] }>(`${base}/participants/import`, { method: "POST", body: data });
  }, { success: (r) => `Imported ${r.created} participants` });
  return (
    <Dialog title="Import participants" onClose={onClose}>
      <form className="form" onSubmit={(e) => { e.preventDefault(); upload.mutate(); }}>
        <p className="muted small">Upload a .csv or .xlsx file up to 5 MB. Required columns are full_name and college. Optional columns use the same names as the participant fields. Good rows are saved and bad rows are listed.</p>
        <Field label="File"><input ref={file} type="file" required accept=".csv,.xlsx" /></Field>
        <ErrorNote error={upload.error} />
        <button className="button-primary" disabled={upload.isPending}>{upload.isPending ? "Importing" : "Import"}</button>
      </form>
      {upload.data && (
        <div className="result-card">
          <p><strong>{upload.data.created}</strong> added, <strong>{upload.data.rejected.length}</strong> rejected</p>
          <ul className="small">{upload.data.rejected.slice(0, 20).map((r) => <li key={r.row}>Row {r.row}: {r.reason}</li>)}</ul>
        </div>
      )}
    </Dialog>
  );
}

const columns: Column<Row>[] = [
  { label: "ID", sort: "code", cell: (r) => <span className="mono">{r.code}</span> },
  { label: "Name", sort: "name", cell: (r) => <strong>{r.full_name}</strong> },
  { label: "College", sort: "college", cell: (r) => r.college },
  { label: "Events", sort: "events", cell: (r) => r.event_count, className: "num" },
  { label: "Accommodation", sort: "accommodation", cell: (r) => yesNo(r.accommodation_required) },
  { label: "Registration", sort: "registration", cell: (r) => <Pill value={r.registration_status} tone={r.registration_status === "cancelled" ? "bad" : undefined} /> },
  { label: "Check-in", sort: "checkin", cell: (r) => <Pill value={r.checkin_status} tone={r.checkin_status === "checked_in" ? "ok" : undefined} /> },
];

export function Participants() {
  const { base, can } = useFest();
  const [params, setParams] = useSearchParams();
  const [search, setSearch] = useState("");
  const [filters, setFilters] = useState({ contingent: "", event: "", accommodation: "", checkin: "", status: "", flagged: params.has("flagged") });
  const [sort, setSort] = useState("");
  const [page, setPage] = useState(1);
  const [dialog, setDialog] = useState<"add" | "import" | null>(null);
  const q = useDebounced(search);
  useEffect(() => setPage(1), [q, filters, sort]);
  const opened = params.get("open");
  const query = usePaged<Row>(`${base}/participants`, { q, ...filters, sort, page });
  const options = useQuery({ queryKey: ["participant-options", base], queryFn: () => api<Options>(`${base}/participants/filters`) });
  const update = (key: keyof typeof filters) => (e: { target: { value: string } }) => setFilters({ ...filters, [key]: e.target.value });
  const exportUrl = `${base}/participants/export.csv${queryString({ q, ...filters })}`;
  return (
    <div>
      <PageHead title="Participants" subtitle="Every record, with what needs review">
        {can("participants.checkin") && <Link className="button-ghost" to="/check-in"><QrCode size={16} /> Check-in</Link>}
        <a className="button-ghost" href={exportUrl} onClick={() => notify("Export started")}><Download size={16} /> Export CSV</a>
        {can("participants.write") && <button className="button-ghost" onClick={() => setDialog("import")}><Upload size={16} /> Import</button>}
        {can("participants.write") && <button className="button-primary small" onClick={() => setDialog("add")}><UserPlus size={16} /> Add participant</button>}
      </PageHead>
      <div className="filters">
        <input type="search" aria-label="Search participants" placeholder="Search name, ID or college" value={search} onChange={(e) => setSearch(e.target.value)} />
        <select aria-label="Contingent" value={filters.contingent} onChange={update("contingent")}><option value="">All contingents</option>{options.data?.contingents.map((c) => <option key={c.code} value={c.code}>{c.college}</option>)}</select>
        <select aria-label="Event" value={filters.event} onChange={update("event")}><option value="">All events</option>{options.data?.events.map((e) => <option key={e.code} value={e.code}>{e.name}</option>)}</select>
        <select aria-label="Accommodation" value={filters.accommodation} onChange={update("accommodation")}><option value="">Accommodation: any</option><option value="yes">Needed</option><option value="no">Not needed</option><option value="unknown">Unknown</option></select>
        <select aria-label="Check-in" value={filters.checkin} onChange={update("checkin")}><option value="">Check-in: any</option><option value="not_arrived">Not arrived</option><option value="checked_in">Checked in</option></select>
        <select aria-label="Registration status" value={filters.status} onChange={update("status")}><option value="">Status: any</option><option value="registered">Registered</option><option value="confirmed">Confirmed</option><option value="cancelled">Cancelled</option></select>
        <label className="check"><input type="checkbox" checked={filters.flagged} onChange={(e) => setFilters({ ...filters, flagged: e.target.checked })} /> Needs review</label>
      </div>
      <QueryState query={query}>
        {query.data && (
          <>
            <DataTable rows={query.data.items} columns={columns} rowKey={(r) => r.code} empty="No participants match these filters." onRow={(r) => setParams({ open: r.code })} sort={sort} onSort={setSort} />
            <Pagination page={query.data.page} pages={query.data.pages} total={query.data.total} onPage={setPage} />
          </>
        )}
      </QueryState>
      {opened && <ParticipantDetail code={opened} options={options.data} onClose={() => setParams({})} />}
      {dialog === "add" && (options.data ? <Dialog title="Add participant" onClose={() => setDialog(null)} side><ParticipantForm options={options.data} onDone={() => setDialog(null)} /></Dialog> : <Loading />)}
      {dialog === "import" && <ImportDialog onClose={() => setDialog(null)} />}
    </div>
  );
}

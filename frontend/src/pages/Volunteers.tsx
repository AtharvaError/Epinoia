import { useQuery } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";

import {
  type Column, DataTable, Dialog, ErrorNote, Facts, Field, PageHead, Pagination, Pill, QueryState, Simulated, Tabs, fromInput, useAction, useDebounced, useFest, usePaged,
} from "../components/ui";
import { api } from "../lib/api";
import { formatTime } from "../lib/format";

interface VolunteerRow { code: string; name: string; department: string | null; skills: string[]; status: string; shifts: number; is_simulated: boolean; matching_skills?: string[] }
interface VolunteerDetail extends VolunteerRow {
  shift_list: { id: number; event: string | null; start_at: string; end_at: string; task: string | null; attendance: string; status: string }[];
}
interface Understaffed { code: string; name: string; start_at: string | null; needed: number; staffed: number; short: number }
interface TaskRow { id: number; title: string; department: string; status: string; due_at: string | null; is_simulated: boolean }
interface EventDetail { start_at: string; end_at: string; name: string }

const DEPARTMENTS = ["Events", "Hospitality", "Logistics", "Registration", "Food & Vendors", "Safety", "Transport", "Volunteers"];
const PADDING_MS = 30 * 60 * 1000;

function VolunteerForm({ existing, onDone }: { existing?: VolunteerRow; onDone: () => void }) {
  const { base } = useFest();
  const [form, setForm] = useState({ name: existing?.name ?? "", department: existing?.department ?? "", skills: existing?.skills.join(", ") ?? "", status: existing?.status ?? "active" });
  const set = (key: keyof typeof form) => (e: { target: { value: string } }) => setForm({ ...form, [key]: e.target.value });
  const save = useAction(() => api(existing ? `${base}/volunteers/${existing.code}` : `${base}/volunteers`, {
    method: existing ? "PATCH" : "POST", body: JSON.stringify({ ...form, department: form.department || null, skills: form.skills.split(",").map((s) => s.trim()).filter(Boolean) }),
  }), { success: "Volunteer saved", onDone });
  return (
    <form className="form form-grid" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
      <Field label="Name"><input required minLength={2} value={form.name} onChange={set("name")} /></Field>
      <Field label="Department"><select value={form.department} onChange={set("department")}><option value="">None</option>{DEPARTMENTS.map((d) => <option key={d}>{d}</option>)}</select></Field>
      <Field label="Skills" hint="Separate with commas"><input value={form.skills} onChange={set("skills")} /></Field>
      <Field label="Status"><select value={form.status} onChange={set("status")}><option value="active">Active</option><option value="unavailable">Unavailable</option><option value="inactive">Inactive</option></select></Field>
      <div className="span-all"><ErrorNote error={save.error} /><button className="button-primary" disabled={save.isPending}>Save volunteer</button></div>
    </form>
  );
}

function VolunteerDrawer({ code, onClose }: { code: string; onClose: () => void }) {
  const { base, can } = useFest();
  const [editing, setEditing] = useState(false);
  const [shift, setShift] = useState({ start_at: "", end_at: "", task: "" });
  const query = useQuery({ queryKey: ["volunteer", code], queryFn: () => api<VolunteerDetail>(`${base}/volunteers/${code}`) });
  const add = useAction(() => api(`${base}/volunteers/${code}/shifts`, { method: "POST", body: JSON.stringify({ start_at: fromInput(shift.start_at), end_at: fromInput(shift.end_at), task: shift.task || null }) }), { success: "Shift added", onDone: () => setShift({ start_at: "", end_at: "", task: "" }) });
  const attendance = useAction(({ id, value }: { id: number; value: string }) => api(`${base}/shifts/${id}`, { method: "PATCH", body: JSON.stringify({ attendance: value }) }), { success: "Attendance recorded" });
  const v = query.data;
  const write = can("volunteers.write");
  return (
    <Dialog title={v ? `${v.name} · ${v.code}` : code} onClose={onClose} side>
      <QueryState query={query}>
        {v && editing && <VolunteerForm existing={v} onDone={() => setEditing(false)} />}
        {v && !editing && (
          <>
            <div className="row-actions"><Pill value={v.status} tone={v.status === "active" ? "ok" : "warn"} />{v.is_simulated && <Simulated />}{write && <button className="button-ghost" onClick={() => setEditing(true)}>Edit</button>}</div>
            <Facts items={[["Department", v.department], ["Skills", v.skills.length ? v.skills.join(", ") : "None listed"], ["Shifts", v.shifts]]} />
            <h3>Shifts</h3>
            {v.shift_list.length ? (
              <ul className="stack">
                {v.shift_list.map((s) => (
                  <li key={s.id}>
                    <div><strong>{s.event ?? s.task ?? "Shift"}</strong><br /><span className="muted small">{formatTime(s.start_at)} to {formatTime(s.end_at)}</span></div>
                    {write ? (
                      <select aria-label="Attendance" value={s.attendance} disabled={attendance.isPending} onChange={(e) => attendance.mutate({ id: s.id, value: e.target.value })}>
                        <option value="unknown">Not recorded</option><option value="present">Present</option><option value="absent">Absent</option>
                      </select>
                    ) : <Pill value={s.attendance} />}
                  </li>
                ))}
              </ul>
            ) : <p className="muted small">No shifts yet.</p>}
            <ErrorNote error={attendance.error} />
            {write && (
              <form className="form" onSubmit={(e) => { e.preventDefault(); add.mutate(); }}>
                <h3>Add a shift</h3>
                <div className="form-grid">
                  <Field label="Start (IST)"><input type="datetime-local" required value={shift.start_at} onChange={(e) => setShift({ ...shift, start_at: e.target.value })} /></Field>
                  <Field label="End (IST)"><input type="datetime-local" required value={shift.end_at} onChange={(e) => setShift({ ...shift, end_at: e.target.value })} /></Field>
                </div>
                <Field label="Task"><input value={shift.task} onChange={(e) => setShift({ ...shift, task: e.target.value })} /></Field>
                <ErrorNote error={add.error} />
                <button className="button-secondary" disabled={add.isPending}>Add shift</button>
              </form>
            )}
          </>
        )}
      </QueryState>
    </Dialog>
  );
}

function Replacements({ event, onClose }: { event: Understaffed; onClose: () => void }) {
  const { base, can } = useFest();
  const people = useQuery({ queryKey: ["replacements", event.code], queryFn: () => api<VolunteerRow[]>(`${base}/volunteers/replacements?event=${event.code}`) });
  const detail = useQuery({ queryKey: ["event", event.code], queryFn: () => api<EventDetail>(`${base}/events/${event.code}`) });
  const assign = useAction((code: string) => api(`${base}/volunteers/${code}/shifts`, {
    method: "POST",
    body: JSON.stringify({
      event_code: event.code, task: `${event.name} duty`,
      start_at: new Date(new Date(detail.data!.start_at).getTime() - PADDING_MS).toISOString(),
      end_at: new Date(new Date(detail.data!.end_at).getTime() + PADDING_MS).toISOString(),
    }),
  }), { success: "Volunteer assigned" });
  return (
    <Dialog title={`Fill ${event.name}`} onClose={onClose}>
      <p className="muted small">{event.short} more needed. Everyone here is free for the whole event, listed by matching skills and then by fewest shifts.</p>
      <ErrorNote error={assign.error} />
      <QueryState query={people}>
        {people.data?.length ? (
          <ul className="stack">
            {people.data.map((v) => (
              <li key={v.code}>
                <div><strong>{v.name}</strong> <span className="mono">{v.code}</span><br /><span className="muted small">{v.matching_skills?.length ? `Matches ${v.matching_skills.join(", ")}` : "No matching skills"}, {v.shifts} {v.shifts === 1 ? "shift" : "shifts"}</span></div>
                {can("volunteers.write") && <button className="button-ghost" disabled={assign.isPending || !detail.data} onClick={() => assign.mutate(v.code)}>Assign</button>}
              </li>
            ))}
          </ul>
        ) : <p className="muted small">Nobody is free for this event.</p>}
      </QueryState>
    </Dialog>
  );
}

function TaskForm({ onClose }: { onClose: () => void }) {
  const { base } = useFest();
  const [form, setForm] = useState({ title: "", department: DEPARTMENTS[0] });
  const save = useAction(() => api(`${base}/tasks`, { method: "POST", body: JSON.stringify(form) }), { success: "Task added", onDone: onClose });
  return (
    <Dialog title="Add task" onClose={onClose}>
      <form className="form" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
        <Field label="Task"><input required minLength={2} value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} /></Field>
        <Field label="Department"><select value={form.department} onChange={(e) => setForm({ ...form, department: e.target.value })}>{DEPARTMENTS.map((d) => <option key={d}>{d}</option>)}</select></Field>
        <ErrorNote error={save.error} />
        <button className="button-primary" disabled={save.isPending}>Save task</button>
      </form>
    </Dialog>
  );
}

const columns: Column<VolunteerRow>[] = [
  { label: "ID", sort: "code", cell: (v) => <span className="mono">{v.code}</span> },
  { label: "Name", sort: "name", cell: (v) => <strong>{v.name}</strong> },
  { label: "Department", sort: "department", cell: (v) => v.department ?? "None" },
  { label: "Skills", cell: (v) => v.skills.join(", ") },
  { label: "Shifts", sort: "shifts", cell: (v) => v.shifts, className: "num" },
  { label: "Status", sort: "status", cell: (v) => <Pill value={v.status} tone={v.status === "active" ? "ok" : "warn"} /> },
];

export function Volunteers() {
  const { base, can } = useFest();
  const write = can("volunteers.write");
  const [params] = useSearchParams();
  const [tab, setTab] = useState<"directory" | "understaffed" | "tasks">(params.get("tab") === "understaffed" ? "understaffed" : "directory");
  const [search, setSearch] = useState("");
  const [department, setDepartment] = useState("");
  const [taskStatus, setTaskStatus] = useState("");
  const [sort, setSort] = useState("");
  const [page, setPage] = useState(1);
  const [opened, setOpened] = useState<string>();
  const [fill, setFill] = useState<Understaffed>();
  const [adding, setAdding] = useState<"volunteer" | "task" | null>(null);
  const q = useDebounced(search);
  useEffect(() => setPage(1), [tab, q, department, taskStatus, sort]);
  const list = usePaged<VolunteerRow>(`${base}/volunteers`, { q, department, sort, page }, tab === "directory");
  const tasks = usePaged<TaskRow>(`${base}/tasks`, { department, status: taskStatus, page }, tab === "tasks");
  const short = useQuery({ queryKey: ["understaffed", base], queryFn: () => api<Understaffed[]>(`${base}/volunteers/understaffed`) });
  const moveTask = useAction(({ id, status }: { id: number; status: string }) => api(`${base}/tasks/${id}?status=${status}`, { method: "PATCH" }), { success: "Task updated" });

  const shortColumns: Column<Understaffed>[] = [
    { label: "Event", value: (e) => e.name, cell: (e) => <strong>{e.name}</strong> },
    { label: "Starts", value: (e) => e.start_at, cell: (e) => (e.start_at ? formatTime(e.start_at) : "Not scheduled") },
    { label: "Staffed", value: (e) => e.staffed, cell: (e) => `${e.staffed} / ${e.needed}`, className: "num" },
    { label: "Short by", value: (e) => e.short, cell: (e) => <span className="warn-text">{e.short}</span>, className: "num" },
    { label: "Actions", cell: (e) => <button className="button-ghost" disabled={!e.start_at} onClick={() => setFill(e)}>Find replacements</button> },
  ];
  const taskColumns: Column<TaskRow>[] = [
    { label: "Task", cell: (t) => <strong>{t.title}</strong> },
    { label: "Department", cell: (t) => t.department },
    { label: "Status", cell: (t) => (write ? (
      <select aria-label={`Status of ${t.title}`} value={t.status} disabled={moveTask.isPending} onChange={(e) => moveTask.mutate({ id: t.id, status: e.target.value })}>
        <option value="todo">To do</option><option value="in_progress">In progress</option><option value="done">Done</option>
      </select>
    ) : <Pill value={t.status} />) },
  ];

  return (
    <div>
      <PageHead title="Crew" simulated subtitle="Shifts, tasks and most volunteers are simulated: the pack lists only 6 core team volunteers">
        {write && <><button className="button-ghost" onClick={() => setAdding("task")}><Plus size={16} /> Add task</button><button className="button-primary small" onClick={() => setAdding("volunteer")}><Plus size={16} /> Add volunteer</button></>}
      </PageHead>
      <Tabs value={tab} onChange={setTab} options={[["directory", "Directory"], ["understaffed", `Understaffed (${short.data?.length ?? 0})`], ["tasks", "Tasks"]]} />
      {tab !== "understaffed" && (
        <div className="filters">
          {tab === "directory" && <input type="search" aria-label="Search volunteers" placeholder="Search name or ID" value={search} onChange={(e) => setSearch(e.target.value)} />}
          <select aria-label="Department" value={department} onChange={(e) => setDepartment(e.target.value)}><option value="">All departments</option>{DEPARTMENTS.map((d) => <option key={d}>{d}</option>)}</select>
          {tab === "tasks" && <select aria-label="Task status" value={taskStatus} onChange={(e) => setTaskStatus(e.target.value)}><option value="">All statuses</option><option value="todo">To do</option><option value="in_progress">In progress</option><option value="done">Done</option></select>}
        </div>
      )}
      <ErrorNote error={moveTask.error} />
      {tab === "directory" && <QueryState query={list}>{list.data && <><DataTable rows={list.data.items} columns={columns} rowKey={(v) => v.code} empty="No volunteers match." onRow={(v) => setOpened(v.code)} sort={sort} onSort={setSort} /><Pagination page={list.data.page} pages={list.data.pages} total={list.data.total} onPage={setPage} /></>}</QueryState>}
      {tab === "understaffed" && <QueryState query={short}>{short.data && <DataTable rows={short.data} columns={shortColumns} rowKey={(e) => e.code} empty="Every event is fully staffed." />}</QueryState>}
      {tab === "tasks" && <QueryState query={tasks}>{tasks.data && <><DataTable rows={tasks.data.items} columns={taskColumns} rowKey={(t) => t.id} empty="No tasks match." /><Pagination page={tasks.data.page} pages={tasks.data.pages} total={tasks.data.total} onPage={setPage} /></>}</QueryState>}
      {opened && <VolunteerDrawer code={opened} onClose={() => setOpened(undefined)} />}
      {fill && <Replacements event={short.data?.find((e) => e.code === fill.code) ?? { ...fill, short: 0 }} onClose={() => setFill(undefined)} />}
      {adding === "volunteer" && <Dialog title="Add volunteer" onClose={() => setAdding(null)}><VolunteerForm onDone={() => setAdding(null)} /></Dialog>}
      {adding === "task" && <TaskForm onClose={() => setAdding(null)} />}
    </div>
  );
}

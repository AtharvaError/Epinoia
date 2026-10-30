import { useQuery } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { useEffect, useState } from "react";

import { type Column, DataTable, Dialog, ErrorNote, Field, PageHead, Pagination, Pill, QueryState, Tabs, useAction, useDebounced, useFest, usePaged } from "../components/ui";
import { api } from "../lib/api";

interface UserRow { id: number; email: string; name: string; role: { key: string; name: string }; is_active: boolean; is_simulated: boolean }
interface Role { key: string; name: string }

function FestivalForm({ onDone, existing }: { onDone: () => void; existing?: { id: number; name: string; start_date: string; end_date: string } }) {
  const [form, setForm] = useState({ name: existing?.name ?? "", start_date: existing?.start_date ?? "", end_date: existing?.end_date ?? "" });
  const set = (key: keyof typeof form) => (e: { target: { value: string } }) => setForm({ ...form, [key]: e.target.value });
  const save = useAction(() => api(existing ? `/api/festivals/${existing.id}` : "/api/festivals", { method: existing ? "PATCH" : "POST", body: JSON.stringify(form) }), { success: existing ? "Festival updated" : "Festival created", onDone });
  return (
    <form className="form form-grid" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
      <div className="span-all"><Field label="Festival name"><input required minLength={2} value={form.name} onChange={set("name")} /></Field></div>
      <Field label="Start date"><input type="date" required value={form.start_date} onChange={set("start_date")} /></Field>
      <Field label="End date"><input type="date" required min={form.start_date} value={form.end_date} onChange={set("end_date")} /></Field>
      <div className="span-all"><ErrorNote error={save.error} /><button className="button-primary" disabled={save.isPending}>Save festival</button></div>
    </form>
  );
}

function AddUser({ roles, onClose }: { roles: Role[]; onClose: () => void }) {
  const { base } = useFest();
  const [form, setForm] = useState({ name: "", email: "", role_key: roles.find((r) => r.key === "volunteer")?.key ?? roles[0]?.key ?? "", password: "" });
  const set = (key: keyof typeof form) => (e: { target: { value: string } }) => setForm({ ...form, [key]: e.target.value });
  const save = useAction(() => api(`${base}/users`, { method: "POST", body: JSON.stringify(form) }), { success: "User added", onDone: onClose });
  return (
    <Dialog title="Add user" onClose={onClose}>
      <form className="form" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
        <Field label="Name"><input required minLength={2} autoComplete="off" value={form.name} onChange={set("name")} /></Field>
        <Field label="Email"><input type="email" required autoComplete="off" value={form.email} onChange={set("email")} /></Field>
        <Field label="Role"><select value={form.role_key} onChange={set("role_key")}>{roles.map((r) => <option key={r.key} value={r.key}>{r.name}</option>)}</select></Field>
        <Field label="Starting password" hint="At least 10 characters. Ask them to change it after signing in."><input type="password" required minLength={10} maxLength={72} autoComplete="new-password" value={form.password} onChange={set("password")} /></Field>
        <ErrorNote error={save.error} />
        <button className="button-primary" disabled={save.isPending}>Add user</button>
      </form>
    </Dialog>
  );
}

function Users() {
  const { base, user } = useFest();
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [adding, setAdding] = useState(false);
  const q = useDebounced(search);
  useEffect(() => setPage(1), [q]);
  const list = usePaged<UserRow>(`${base}/users`, { q, page });
  const roles = useQuery({ queryKey: ["roles", base], queryFn: () => api<Role[]>(`${base}/users/roles`) });
  const change = useAction(({ id, body }: { id: number; body: object }) => api(`${base}/users/${id}`, { method: "PATCH", body: JSON.stringify(body) }), { success: "Access updated" });
  const columns: Column<UserRow>[] = [
    { label: "Name", cell: (u) => <><strong>{u.name}</strong><br /><small className="muted">{u.email}</small></> },
    { label: "Role", cell: (u) => (u.id === user.id ? u.role.name : (
      <select aria-label={`Role for ${u.name}`} value={u.role.key} disabled={change.isPending} onChange={(e) => change.mutate({ id: u.id, body: { role_key: e.target.value } })}>
        {!roles.data?.some((r) => r.key === u.role.key) && <option value={u.role.key}>{u.role.name}</option>}
        {roles.data?.map((r) => <option key={r.key} value={r.key}>{r.name}</option>)}
      </select>
    )) },
    { label: "Status", cell: (u) => <Pill value={u.is_active ? "active" : "disabled"} tone={u.is_active ? "ok" : "bad"} /> },
    { label: "Actions", cell: (u) => (u.id === user.id ? <span className="muted small">You</span> : <button className="button-ghost" disabled={change.isPending} onClick={() => change.mutate({ id: u.id, body: { is_active: !u.is_active } })}>{u.is_active ? "Disable" : "Enable"}</button>) },
  ];
  return (
    <>
      <div className="filters">
        <input type="search" aria-label="Search users" placeholder="Search name or email" value={search} onChange={(e) => setSearch(e.target.value)} />
        <button className="button-primary small" disabled={!roles.data} onClick={() => setAdding(true)}><Plus size={16} /> Add user</button>
      </div>
      <ErrorNote error={change.error} />
      <QueryState query={list}>{list.data && <><DataTable rows={list.data.items} columns={columns} rowKey={(u) => u.id} empty="No users match." /><Pagination page={list.data.page} pages={list.data.pages} total={list.data.total} onPage={setPage} /></>}</QueryState>
      {adding && roles.data && <AddUser roles={roles.data} onClose={() => setAdding(false)} />}
    </>
  );
}

export function Settings() {
  const { festival, can } = useFest();
  const [tab, setTab] = useState<"users" | "festival">("users");
  const [creating, setCreating] = useState(false);
  return (
    <div>
      <PageHead title="Settings" subtitle="Who can use FestOS Command, and how this festival is set up">
        {can("festivals.manage") && <button className="button-primary small" onClick={() => setCreating(true)}><Plus size={16} /> New festival</button>}
      </PageHead>
      <Tabs value={tab} onChange={setTab} options={[["users", "Users and roles"], ["festival", "Festival details"]]} />
      {tab === "users" && <Users />}
      {tab === "festival" && <section className="panel narrow"><FestivalForm key={festival.id} existing={festival} onDone={() => undefined} /></section>}
      {creating && <Dialog title="New festival" onClose={() => setCreating(false)}><p className="muted small">It starts empty. Import participants and add events, venues and hostels from each module. Choose it from the festival menu at the top.</p><FestivalForm onDone={() => setCreating(false)} /></Dialog>}
    </div>
  );
}

import { useQuery } from "@tanstack/react-query";
import { Send } from "lucide-react";
import { useState } from "react";

import { Dialog, ErrorNote, Field, PageHead, Pagination, Pill, QueryState, Simulated, Tabs, useAction, useFest, usePaged } from "../components/ui";
import { api, type Notifications } from "../lib/api";
import { formatTime } from "../lib/format";

interface Announcement { id: number; title: string; body: string; audience_type: string; audience_ref: string | null; created_at: string; created_by: string | null; recipients: number; read: number; acknowledged: number; is_simulated: boolean }
interface Options { roles: { key: string; name: string }[]; departments: string[]; events: { code: string; name: string }[] }

function Compose({ options, onClose }: { options: Options; onClose: () => void }) {
  const { base } = useFest();
  const [form, setForm] = useState({ title: "", body: "", audience_type: "festival", audience_ref: "" });
  const set = (key: keyof typeof form) => (e: { target: { value: string } }) => setForm({ ...form, [key]: e.target.value });
  const send = useAction(() => api<{ recipients: number }>(`${base}/announcements`, { method: "POST", body: JSON.stringify({ ...form, audience_ref: form.audience_ref || null }) }), {
    success: (r) => `Sent to ${r.recipients} people`, onDone: onClose,
  });
  const choices: { value: string; label: string }[] = form.audience_type === "role" ? options.roles.map((r) => ({ value: r.key, label: r.name }))
    : form.audience_type === "department" ? options.departments.map((d) => ({ value: d, label: d }))
      : form.audience_type === "event" ? options.events.map((e) => ({ value: e.code, label: e.name })) : [];
  return (
    <Dialog title="New announcement" onClose={onClose}>
      <form className="form" onSubmit={(e) => { e.preventDefault(); send.mutate(); }}>
        <Field label="Audience"><select value={form.audience_type} onChange={(e) => setForm({ ...form, audience_type: e.target.value, audience_ref: "" })}>
          <option value="festival">Everyone on the core team</option><option value="role">A role</option><option value="department">A department</option><option value="event">An event's coordinators</option>
        </select></Field>
        {choices.length > 0 && <Field label="Choose"><select required value={form.audience_ref} onChange={set("audience_ref")}><option value="">Select</option>{choices.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}</select></Field>}
        <Field label="Title"><input required minLength={3} value={form.title} onChange={set("title")} /></Field>
        <Field label="Message"><textarea required minLength={3} rows={4} value={form.body} onChange={set("body")} /></Field>
        <ErrorNote error={send.error} />
        <button className="button-primary" disabled={send.isPending}><Send size={16} /> Send</button>
      </form>
    </Dialog>
  );
}

function Inbox() {
  const { base } = useFest();
  const notes = useQuery({ queryKey: ["notifications", base], queryFn: () => api<Notifications>(`${base}/notifications`) });
  const acknowledge = useAction((id: number) => api(`${base}/notifications/${id}/acknowledge`, { method: "POST" }), { success: "Acknowledged" });
  return (
    <QueryState query={notes}>
      <ErrorNote error={acknowledge.error} />
      {notes.data?.items.length ? (
        <ul className="stack panel">
          {notes.data.items.map((n) => (
            <li key={n.id}>
              <div><strong>{n.title}</strong>{n.body && <p className="muted small">{n.body}</p>}<small className="muted">{formatTime(n.created_at)}</small></div>
              {n.acknowledged ? <Pill value="acknowledged" tone="ok" /> : <button className="button-ghost" disabled={acknowledge.isPending} onClick={() => acknowledge.mutate(n.id)}>Acknowledge</button>}
            </li>
          ))}
        </ul>
      ) : <p className="table-empty muted">Nothing here yet.</p>}
    </QueryState>
  );
}

export function Communication() {
  const { base, can } = useFest();
  const [tab, setTab] = useState<"sent" | "inbox">("inbox");
  const [page, setPage] = useState(1);
  const [composing, setComposing] = useState(false);
  const list = usePaged<Announcement>(`${base}/announcements`, { page }, tab === "sent");
  const options = useQuery({ queryKey: ["announcement-options", base], queryFn: () => api<Options>(`${base}/announcements/options`) });
  return (
    <div>
      <PageHead title="Communication" subtitle="Reaches core team accounts in this app. Nothing is sent by email or SMS.">
        {can("communication.write") && <button className="button-primary small" disabled={!options.data} onClick={() => setComposing(true)}><Send size={16} /> New announcement</button>}
      </PageHead>
      <Tabs value={tab} onChange={setTab} options={[["inbox", "My notifications"], ["sent", "Announcements"]]} />
      {tab === "inbox" && <Inbox />}
      {tab === "sent" && (
        <QueryState query={list}>
          {list.data && (
            <>
              {list.data.items.some((a) => a.is_simulated) && <p className="small muted"><Simulated /> Seeded announcements are demo data. New ones are real.</p>}
              {list.data.items.length ? (
                <ul className="announcements">
                  {list.data.items.map((a) => (
                    <li key={a.id} className="panel">
                      <header><h2>{a.title}</h2></header>
                      <p>{a.body}</p>
                      <p className="muted small">{a.created_by ?? "System"}, {formatTime(a.created_at)}. To {a.audience_type === "festival" ? "everyone" : a.audience_ref}. {a.recipients} sent, {a.read} read, {a.acknowledged} acknowledged.</p>
                    </li>
                  ))}
                </ul>
              ) : <p className="table-empty muted">No announcements yet.</p>}
              <Pagination page={list.data.page} pages={list.data.pages} total={list.data.total} onPage={setPage} />
            </>
          )}
        </QueryState>
      )}
      {composing && options.data && <Compose options={options.data} onClose={() => setComposing(false)} />}
    </div>
  );
}

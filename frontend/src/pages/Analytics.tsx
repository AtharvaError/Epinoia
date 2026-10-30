import { useQuery } from "@tanstack/react-query";
import { Download } from "lucide-react";
import { type ReactNode, useState } from "react";
import { Bar, BarChart, CartesianGrid, Cell, Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

import { PageHead, QueryState, queryString, SectionHead, Simulated, useFest } from "../components/ui";
import { api } from "../lib/api";
import { formatLakh, formatMoney, formatNumber } from "../lib/format";

interface EventRow { code: string; name: string; status: string; venue: string | null; capacity: number | null; registered: number; attended: number; staffed: number; needed: number }
interface Counted { status: string; count: number }
interface Sections {
  events?: EventRow[];
  registration?: { registrations: number; events: number; over_capacity: number; attended: number };
  colleges?: { college: string; participants: number; registrations: number }[];
  attendance?: Counted[];
  accommodation?: { hostel: string; kind: string; status: string; beds: number; occupied: number }[];
  departments?: { department: string; volunteers: number; shifts: number; attended: number; absent: number; tasks: number; tasks_done: number }[];
  transport?: { trips: number; delayed: number; passengers: number; seats: number; delay_minutes: number };
  finance?: { department: string; approved: string; committed: string; remaining: string }[];
  incidents?: Counted[];
  incident_priorities?: { priority: string; count: number }[];
  simulations?: Counted[];
}
interface Data { sections: Sections; reports: { key: string; title: string }[] }
interface Options { events: { code: string; name: string }[]; departments: string[]; venues: { code: string; name: string }[] }

const axis = { fill: "var(--muted)", fontSize: 12 };
const tooltip = {
  contentStyle: { background: "var(--surface-2)", border: "1px solid var(--line)", borderRadius: 10, color: "var(--text)" },
  labelStyle: { color: "var(--text)" }, itemStyle: { color: "var(--text)" }, cursor: { fill: "var(--hover)" },
};
const SLICES = ["var(--accent)", "var(--cyan)", "var(--medium)", "var(--high)", "var(--critical)", "var(--ok)", "var(--bar-muted)"];

function Section({ title, children, wide }: { title: string; children: ReactNode; wide?: boolean }) {
  return <section className={`panel ${wide ? "wide" : ""}`}><header><h2>{title}</h2></header>{children}</section>;
}

function Stats({ items }: { items: [string, string | number][] }) {
  return <div className="stat-row">{items.map(([k, v]) => <div key={k}><span>{k}</span><strong>{typeof v === "number" ? formatNumber(v) : v}</strong></div>)}</div>;
}

function Share({ data, label }: { data: { name: string; value: number }[]; label: string }) {
  if (!data.length) return <p className="muted small">No records yet.</p>;
  return (
    <div className="share">
      <ResponsiveContainer width={150} height={150}>
        <PieChart>
          <Pie data={data} dataKey="value" nameKey="name" innerRadius={42} outerRadius={68} stroke="none">{data.map((d, i) => <Cell key={d.name} fill={SLICES[i % SLICES.length]} />)}</Pie>
          <Tooltip {...tooltip} />
        </PieChart>
      </ResponsiveContainer>
      <ul aria-label={label}>{data.map((d, i) => <li key={d.name}><i style={{ background: SLICES[i % SLICES.length] }} />{d.name.replaceAll("_", " ")}<strong>{formatNumber(d.value)}</strong></li>)}</ul>
    </div>
  );
}

export function Analytics() {
  const { base } = useFest();
  const [filters, setFilters] = useState({ day: "", event: "", department: "", venue: "", status: "" });
  const set = (key: keyof typeof filters) => (e: { target: { value: string } }) => setFilters({ ...filters, [key]: e.target.value });
  const search = queryString(filters);
  const query = useQuery({ queryKey: ["analytics", base, search], queryFn: () => api<Data>(`${base}/analytics${search}`) });
  const options = useQuery({
    queryKey: ["analytics-options", base],
    queryFn: async () => {
      const [a, b] = await Promise.all([api<{ events: Options["events"]; departments: string[] }>(`${base}/announcements/options`).catch(() => ({ events: [], departments: [] })), api<Options["venues"]>(`${base}/venues`).catch(() => [])]);
      return { events: a.events, departments: a.departments, venues: b } as Options;
    },
  });
  const s = query.data?.sections;
  const fill = (s?.events ?? []).filter((e) => e.capacity).map((e) => ({ code: e.code, name: e.name, fill: Math.round((e.registered / e.capacity!) * 100) })).sort((a, b) => b.fill - a.fill);
  return (
    <div>
      <PageHead title="Reports" subtitle="Computed live from the database. Sections match what your role can read." />
      <div className="filters">
        <input type="date" aria-label="Day" value={filters.day} onChange={set("day")} />
        <select aria-label="Event" value={filters.event} onChange={set("event")}><option value="">All events</option>{options.data?.events.map((e) => <option key={e.code} value={e.code}>{e.name}</option>)}</select>
        <select aria-label="Department" value={filters.department} onChange={set("department")}><option value="">All departments</option>{options.data?.departments.map((d) => <option key={d}>{d}</option>)}</select>
        <select aria-label="Venue" value={filters.venue} onChange={set("venue")}><option value="">All venues</option>{options.data?.venues.map((v) => <option key={v.code} value={v.code}>{v.name}</option>)}</select>
        <select aria-label="Status" value={filters.status} onChange={set("status")}><option value="">All statuses</option>{["scheduled", "live", "completed", "postponed", "cancelled", "delayed", "reported", "resolved", "applied"].map((x) => <option key={x}>{x}</option>)}</select>
        {search && <button className="button-ghost" onClick={() => setFilters({ day: "", event: "", department: "", venue: "", status: "" })}>Clear filters</button>}
      </div>
      <QueryState query={query}>
        {s && (
          <>
            {s.registration && <Stats items={[["Registrations", s.registration.registrations], ["Events", s.registration.events], ["Over capacity", s.registration.over_capacity], ["Attendance recorded", s.registration.attended]]} />}
            <div className="chart-grid analytics">
              {fill.length > 0 && (
                <Section title="Event fill rate" wide>
                  <p className="panel-note">Registered as a percentage of venue seats, against the generated schedule. Red is over 100%.</p>
                  <ResponsiveContainer width="100%" height={240}>
                    <BarChart data={fill} margin={{ left: -16, right: 8 }}>
                      <CartesianGrid stroke="var(--line)" vertical={false} />
                      <XAxis dataKey="code" tick={axis} tickLine={false} axisLine={false} interval={0} angle={-55} textAnchor="end" height={44} />
                      <YAxis tick={axis} tickLine={false} axisLine={false} unit="%" />
                      <Tooltip {...tooltip} labelFormatter={(code, items) => `${code}: ${items?.[0]?.payload?.name ?? ""}`} formatter={(v) => [`${v}%`, "Fill rate"]} />
                      <Bar dataKey="fill" radius={[4, 4, 0, 0]}>{fill.map((e) => <Cell key={e.code} fill={e.fill > 100 ? "var(--critical)" : "var(--accent)"} />)}</Bar>
                    </BarChart>
                  </ResponsiveContainer>
                </Section>
              )}
              {s.colleges && (
                <Section title="Registrations by college, top 10">
                  <ResponsiveContainer width="100%" height={280}>
                    <BarChart data={s.colleges} layout="vertical" margin={{ left: 8, right: 12 }}>
                      <XAxis type="number" tick={axis} tickLine={false} axisLine={false} />
                      <YAxis type="category" dataKey="college" tick={{ ...axis, fontSize: 11 }} tickLine={false} axisLine={false} width={124} interval={0} />
                      <Tooltip {...tooltip} />
                      <Bar dataKey="registrations" name="Registrations" fill="var(--accent)" radius={[0, 4, 4, 0]} />
                    </BarChart>
                  </ResponsiveContainer>
                </Section>
              )}
              {s.attendance && <Section title="Participant check-in"><Share label="Check-in status" data={s.attendance.map((a) => ({ name: a.status, value: a.count }))} /></Section>}
              {s.accommodation && (
                <Section title="Accommodation utilization">
                  <div className="meters">{s.accommodation.map((h) => {
                    const used = h.beds ? Math.min(100, (h.occupied / h.beds) * 100) : 0;
                    return <div key={h.hostel} className="meter"><div className="meter-head"><span>{h.hostel}</span><span className="muted">{h.status === "available" ? `${formatNumber(h.occupied)} of ${formatNumber(h.beds)}` : h.status}</span></div><div className="meter-track"><div style={{ width: `${used}%` }} /></div></div>;
                  })}</div>
                </Section>
              )}
              {s.simulations && <Section title="Ripple simulations"><Share label="Simulation outcomes" data={s.simulations.map((a) => ({ name: a.status, value: a.count }))} /></Section>}
            </div>
            {(s.departments || s.transport || s.finance || s.incident_priorities) && <SectionHead title="Simulated operations"><Simulated /></SectionHead>}
            <div className="chart-grid">
              {s.departments && (
                <Section title="Department performance">
                  <div className="meters">{s.departments.filter((d) => d.tasks).map((d) => <div key={d.department} className="meter"><div className="meter-head"><span>{d.department}</span><span className="muted">{d.tasks_done} of {d.tasks} tasks, {d.shifts} shifts</span></div><div className="meter-track"><div style={{ width: `${(d.tasks_done / d.tasks) * 100}%` }} /></div></div>)}</div>
                </Section>
              )}
              {s.transport && <Section title="Transportation performance"><Stats items={[["Trips", s.transport.trips], ["Delayed", s.transport.delayed], ["Passengers", s.transport.passengers], ["Seats", s.transport.seats], ["Minutes of delay", s.transport.delay_minutes]]} /></Section>}
              {s.finance && (
                <Section title="Budget against commitments">
                  <ResponsiveContainer width="100%" height={280}>
                    <BarChart data={s.finance.map((f) => ({ department: f.department, approved: Number(f.approved), committed: Number(f.committed) }))} layout="vertical" barGap={1} margin={{ left: 8, right: 12 }}>
                      <XAxis type="number" tick={axis} tickLine={false} axisLine={false} tickFormatter={formatLakh} />
                      <YAxis type="category" dataKey="department" tick={{ ...axis, fontSize: 11 }} tickLine={false} axisLine={false} width={104} interval={0} />
                      <Tooltip {...tooltip} formatter={(v) => formatMoney(Number(v))} />
                      <Bar dataKey="approved" name="Approved" fill="var(--bar-muted)" radius={[0, 4, 4, 0]} />
                      <Bar dataKey="committed" name="Committed" fill="var(--cyan)" radius={[0, 4, 4, 0]} />
                    </BarChart>
                  </ResponsiveContainer>
                </Section>
              )}
              {s.incident_priorities && <Section title="Incidents by priority"><Share label="Incident priorities" data={s.incident_priorities.map((a) => ({ name: a.priority, value: a.count }))} /></Section>}
            </div>
            <section className="panel reports">
              <header><h2>Reports</h2><span className="muted small">{search ? "Downloads use the filters above" : "All records"}</span></header>
              <ul className="stack">
                {query.data?.reports.map((r) => (
                  <li key={r.key}>
                    <span>{r.title}</span>
                    <span className="row-actions">
                      <a className="button-ghost" href={`${base}/reports/${r.key}.csv${search}`}><Download size={14} /> CSV</a>
                      <a className="button-ghost" href={`${base}/reports/${r.key}.pdf${search}`}><Download size={14} /> PDF</a>
                    </span>
                  </li>
                ))}
              </ul>
            </section>
          </>
        )}
      </QueryState>
    </div>
  );
}

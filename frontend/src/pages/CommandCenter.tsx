import { useQuery } from "@tanstack/react-query";
import { Activity, ArrowRight } from "lucide-react";
import { type ReactNode } from "react";
import { Link, Navigate, useOutletContext } from "react-router-dom";
import {
  Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, ComposedChart, Line, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";

import { Moment, SectionHead } from "../components/ui";
import { api, ApiError, type Alert, type Dashboard, type Festival, type Kpi, type Source, type User } from "../lib/api";
import { formatLakh, formatMoney, formatNumber, formatTime } from "../lib/format";

const SOURCE_LABEL: Record<Source, string> = { pack: "Pack", generated: "Generated", simulated: "Simulated" };
const SOURCE_HELP: Record<Source, string> = {
  pack: "Taken from the festival data pack",
  generated: "Computed by the FestOS planner from pack data",
  simulated: "Demo data: the pack has no records for this area",
};

function SourceTag({ source }: { source: Source }) {
  return <span className={`source source-${source}`} title={SOURCE_HELP[source]}>{SOURCE_LABEL[source]}</span>;
}

const axis = { fill: "var(--muted)", fontSize: 12 };
const tooltip = {
  contentStyle: { background: "var(--surface-2)", border: "1px solid var(--line)", borderRadius: 10, color: "var(--text)" },
  labelStyle: { color: "var(--text)" },
  itemStyle: { color: "var(--text)" },
  cursor: { fill: "var(--hover)" },
};

const KPI_LINKS: Record<string, [string, string]> = {
  participants: ["/participants", "participants.read"], teams: ["/participants", "participants.read"], events: ["/events", "events.read"], venues: ["/venues", "venues.read"],
  beds: ["/accommodation", "accommodation.read"], volunteers: ["/volunteers", "volunteers.read"], budget: ["/finance", "finance.read"], spent: ["/finance", "finance.read"],
  approvals: ["/finance?tab=expenses&approval=pending", "finance.read"], incidents: ["/safety", "incidents.read"],
};

function SeatMarker({ cx, cy, index }: { cx?: number; cy?: number; index?: number }) {
  if (cx === undefined || cy === undefined) return <g key={index} />;
  return <line key={index} x1={cx - 11} x2={cx + 11} y1={cy} y2={cy} stroke="var(--cyan)" strokeWidth={3} strokeLinecap="round" />;
}

function KpiValue({ kpi }: { kpi: Kpi }) {
  const money = kpi.key === "budget" || kpi.key === "spent";
  const value = money ? formatLakh(Number(kpi.value)) : formatNumber(kpi.value);
  return (
    <p className="kpi-value" title={money ? formatMoney(kpi.value) : undefined}>
      {value}
      {kpi.total !== null && <span> / {money ? formatLakh(Number(kpi.total)) : formatNumber(kpi.total)}</span>}
    </p>
  );
}

function KpiCell({ kpi, can }: { kpi: Kpi; can: (permission: string) => boolean }) {
  const [to, permission] = KPI_LINKS[kpi.key] ?? [];
  const body = (
    <>
      <span className="ribbon-label">{kpi.source === "simulated" && <i className="sim-dot" title={SOURCE_HELP.simulated} />}{kpi.label}</span>
      <KpiValue kpi={kpi} />
    </>
  );
  return to && can(permission) ? <Link className="ribbon-cell" to={to} title={kpi.note ?? undefined}>{body}</Link> : <div className="ribbon-cell" title={kpi.note ?? undefined}>{body}</div>;
}

function Panel({ title, note, children, wide }: { title: string; note?: string; children: ReactNode; wide?: boolean }) {
  return (
    <section className={`panel ${wide ? "wide" : ""}`}>
      <header><h2>{title}</h2></header>
      {note && <p className="panel-note">{note}</p>}
      {children}
    </section>
  );
}

function Decision({ alert, rank, can }: { alert: Alert; rank: number; can: (permission: string) => boolean }) {
  const { action } = alert;
  const Icon = action?.to.startsWith("/ripple") ? Activity : ArrowRight;
  return (
    <li className={`decision-row alert-${alert.severity}`}>
      <span className="decision-rank" aria-hidden="true">{rank}</span>
      <div>
        <span className="alert-level">{alert.severity}</span> <span className="muted small">{alert.module}</span>
        {alert.source === "simulated" && <i className="sim-dot" title={SOURCE_HELP.simulated} />}
        <h3>{alert.title}</h3>
        <p>{alert.detail}</p>
      </div>
      {action && can(action.permission) && <Link className="button-primary small" to={action.to}><Icon size={16} aria-hidden="true" />{action.label}</Link>}
    </li>
  );
}

function Meter({ label, value, total, detail }: { label: string; value: number; total: number; detail: string }) {
  const percent = total ? Math.min(100, (value / total) * 100) : 0;
  return (
    <div className="meter">
      <div className="meter-head"><span>{label}</span><span className="muted">{detail}</span></div>
      <div className="meter-track" role="progressbar" aria-label={label} aria-valuenow={Math.round(percent)} aria-valuemin={0} aria-valuemax={100}>
        <div style={{ width: `${percent}%` }} />
      </div>
    </div>
  );
}

export function CommandCenter() {
  const { festival, user } = useOutletContext<{ festival: Festival; user: User }>();
  const can = (permission: string) => user.permissions.includes(permission);
  const allowed = can("dashboard.read");
  const dashboard = useQuery({
    queryKey: ["dashboard", festival.id],
    queryFn: () => api<Dashboard>(`/api/festivals/${festival.id}/dashboard`),
    refetchInterval: 60000,  // A safety net: the live socket refreshes this as changes happen.
    enabled: allowed,
  });

  // Volunteers land on the phone check-in screen, and anyone else without the dashboard on the schedule.
  if (!allowed || (dashboard.error instanceof ApiError && dashboard.error.status === 403)) return <Navigate to={can("participants.checkin") ? "/check-in" : "/events"} replace />;
  if (dashboard.isPending) return <div className="skeleton-grid" role="status" aria-label="Loading dashboard">{Array.from({ length: 10 }, (_, i) => <div key={i} />)}</div>;
  if (dashboard.isError) {
    return (
      <div className="state-error" role="alert">
        <p>{dashboard.error.message}</p>
        <button className="button-secondary" onClick={() => dashboard.refetch()}>Try again</button>
      </div>
    );
  }

  const { kpis, alerts, feed, charts } = dashboard.data;
  const critical = alerts.filter((a) => a.severity === "critical").length;
  const taskTotal = charts.tasks.reduce((sum, t) => sum + t.total, 0);

  return (
    <div className="dashboard">
      <Moment eyebrow={`${festival.name} · Command Center`}
              title={alerts.length ? `${alerts.length} ${alerts.length === 1 ? "decision needs" : "decisions need"} you` : "Nothing needs a decision"}>
        {critical > 0 && <p>{critical} {critical === 1 ? "is" : "are"} critical. Simulate a fix before you change anything.</p>}
      </Moment>

      <div className="ops-grid">
        <section className="panel alerts-panel">
          <header><h2>Next decisions</h2><span className="muted small"><i className="sim-dot" /> Simulated data</span></header>
          {alerts.length ? <ol>{alerts.map((a, i) => <Decision key={a.title} alert={a} rank={i + 1} can={can} />)}</ol> : <p className="muted">Nothing needs attention right now.</p>}
        </section>
        <section className="panel feed-panel">
          <header><h2>Live operations feed</h2></header>
          {feed.length ? (
            <ol tabIndex={0} aria-label="Recent activity">{feed.map((f) => <li key={f.id}><p>{f.summary}</p><small>{f.actor_label} · {formatTime(f.created_at)}</small></li>)}</ol>
          ) : <p className="muted">No activity recorded yet.</p>}
        </section>
      </div>

      <section className="ribbon" aria-label="Key figures">{kpis.map((k) => <KpiCell key={k.key} kpi={k} can={can} />)}</section>

      <SectionHead title="Planned by FestOS"><SourceTag source="generated" /></SectionHead>
      <div className="chart-grid">
        <Panel title="Event participation against venue capacity" wide note="Bars in red are events with more registrants than their assigned venue has seats.">
          <ResponsiveContainer width="100%" height={280}>
            <ComposedChart data={charts.event_participation} margin={{ left: -16, right: 8 }}>
              <CartesianGrid stroke="var(--line)" vertical={false} />
              <XAxis dataKey="code" tick={axis} tickLine={false} axisLine={false} interval={0} angle={-55} textAnchor="end" height={46} />
              <YAxis tick={axis} tickLine={false} axisLine={false} />
              <Tooltip {...tooltip} labelFormatter={(code, items) => `${code}: ${items?.[0]?.payload?.name ?? ""}`} />
              <Bar dataKey="registered" name="Registered" radius={[4, 4, 0, 0]}>
                {charts.event_participation.map((e) => <Cell key={e.code} fill={e.capacity !== null && e.registered > e.capacity ? "var(--critical)" : "var(--accent)"} />)}
              </Bar>
              <Line dataKey="capacity" name="Venue seats" stroke="none" isAnimationActive={false} activeDot={false} dot={SeatMarker} />
            </ComposedChart>
          </ResponsiveContainer>
          <div className="chart-key">
            <span><i style={{ background: "var(--accent)" }} />Fits venue</span>
            <span><i style={{ background: "var(--critical)" }} />Over capacity</span>
            <span><i className="key-line" />Venue seats</span>
          </div>
        </Panel>

        <Panel title="Event status" wide>
          <div className="meters">
            {charts.event_status.map((s) => (
              <Meter key={s.status} label={s.status[0].toUpperCase() + s.status.slice(1)} value={s.count} total={kpis.find((k) => k.key === "events")?.value as number} detail={`${s.count} events`} />
            ))}
          </div>
        </Panel>
      </div>

      <SectionHead title="From the data pack"><SourceTag source="pack" /></SectionHead>
      <div className="chart-grid">
        <Panel title="Participant arrivals" note="The pack has no registration dates, so this shows arrivals instead of a registration trend.">
          <ResponsiveContainer width="100%" height={240}>
            <AreaChart data={charts.arrivals} margin={{ left: -16, right: 8 }}>
              <CartesianGrid stroke="var(--line)" vertical={false} />
              <XAxis dataKey="label" tick={axis} tickLine={false} axisLine={false} minTickGap={36} />
              <YAxis tick={axis} tickLine={false} axisLine={false} />
              <Tooltip {...tooltip} />
              <Area dataKey="count" name="Arrivals" stroke="var(--accent)" fill="var(--accent)" fillOpacity={0.18} strokeWidth={2} />
            </AreaChart>
          </ResponsiveContainer>
        </Panel>

        <Panel title="Accommodation occupancy">
          <div className="meters">
            {charts.occupancy.map((h) => (
              <Meter key={h.name} label={h.name} value={h.occupied} total={h.capacity} detail={`${formatNumber(h.occupied)} of ${formatNumber(h.capacity)}`} />
            ))}
          </div>
        </Panel>
      </div>

      <SectionHead title="Simulated operations"><SourceTag source="simulated" /></SectionHead>
      <div className="chart-grid">
        <Panel title="Budget utilization">
          <ResponsiveContainer width="100%" height={300}>
            <BarChart data={charts.budget} layout="vertical" barGap={1} margin={{ left: 8, right: 12 }}>
              <XAxis type="number" tick={axis} tickLine={false} axisLine={false} tickFormatter={formatLakh} />
              <YAxis type="category" dataKey="department" tick={axis} tickLine={false} axisLine={false} width={108} interval={0} />
              <Tooltip {...tooltip} formatter={(value) => formatMoney(Number(value))} />
              <Bar dataKey="approved" name="Approved" fill="var(--bar-muted)" radius={[0, 4, 4, 0]} />
              <Bar dataKey="spent" name="Spent" fill="var(--cyan)" radius={[0, 4, 4, 0]} />
            </BarChart>
          </ResponsiveContainer>
          <div className="chart-key">
            <span><i style={{ background: "var(--bar-muted)" }} />Approved</span>
            <span><i style={{ background: "var(--cyan)" }} />Spent</span>
          </div>
        </Panel>

        <Panel title="Department task progress" note={`${formatNumber(taskTotal)} tasks across ${charts.tasks.length} departments`}>
          <div className="meters">
            {charts.tasks.map((t) => <Meter key={t.department} label={t.department} value={t.done} total={t.total} detail={`${t.done} of ${t.total} done`} />)}
          </div>
        </Panel>

        <Panel title="Volunteer allocation" wide>
          <ResponsiveContainer width="100%" height={240}>
            <BarChart data={charts.volunteers} margin={{ left: -16, right: 8 }}>
              <CartesianGrid stroke="var(--line)" vertical={false} />
              <XAxis dataKey="department" tick={{ ...axis, fontSize: 11 }} tickLine={false} axisLine={false} interval={0} angle={-25} textAnchor="end" height={54} />
              <YAxis tick={axis} tickLine={false} axisLine={false} />
              <Tooltip {...tooltip} />
              <Bar dataKey="volunteers" name="Volunteers" fill="var(--accent)" radius={[4, 4, 0, 0]} />
              <Bar dataKey="shifts" name="Shifts" fill="var(--cyan)" radius={[4, 4, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
          <div className="chart-key">
            <span><i style={{ background: "var(--accent)" }} />Volunteers</span>
            <span><i style={{ background: "var(--cyan)" }} />Shifts</span>
          </div>
        </Panel>
      </div>
    </div>
  );
}

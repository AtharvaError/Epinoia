import { useQuery } from "@tanstack/react-query";
import { type ReactNode } from "react";
import { useOutletContext } from "react-router-dom";
import {
  Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, ComposedChart, Line, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";

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
  cursor: { fill: "rgba(252,246,234,.05)" },
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

function KpiCard({ kpi }: { kpi: Kpi }) {
  return (
    <article className="kpi">
      <div className="kpi-head"><h3>{kpi.label}</h3><SourceTag source={kpi.source} /></div>
      <KpiValue kpi={kpi} />
      {kpi.note && <p className="kpi-note">{kpi.note}</p>}
    </article>
  );
}

function Panel({ title, source, note, children, wide }: { title: string; source: Source; note?: string; children: ReactNode; wide?: boolean }) {
  return (
    <section className={`panel ${wide ? "wide" : ""}`}>
      <header><h2>{title}</h2><SourceTag source={source} /></header>
      {note && <p className="panel-note">{note}</p>}
      {children}
    </section>
  );
}

function AlertRow({ alert }: { alert: Alert }) {
  return (
    <li className={`alert alert-${alert.severity}`}>
      <span className="alert-level">{alert.severity}</span>
      <div>
        <h3>{alert.title}</h3>
        <p>{alert.detail}</p>
      </div>
      <div className="alert-meta"><span>{alert.module}</span><SourceTag source={alert.source} /></div>
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
  const allowed = user.permissions.includes("dashboard.read");
  const dashboard = useQuery({
    queryKey: ["dashboard", festival.id],
    queryFn: () => api<Dashboard>(`/api/festivals/${festival.id}/dashboard`),
    refetchInterval: 15000,
    enabled: allowed,
  });

  if (!allowed || (dashboard.error instanceof ApiError && dashboard.error.status === 403)) {
    return <div className="state-empty"><h1>{festival.name}</h1><p>Your role, {user.role.name}, does not include the Command Center.</p></div>;
  }
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
      <div className="page-head">
        <div>
          <h1>Command Center</h1>
          <p className="muted">{critical > 0 ? `${critical} critical ${critical === 1 ? "issue needs" : "issues need"} a decision` : "No critical issues"}</p>
        </div>
        <div className="legend" aria-label="Data source key">
          {(["pack", "generated", "simulated"] as Source[]).map((s) => <SourceTag key={s} source={s} />)}
        </div>
      </div>

      <section className="kpi-grid" aria-label="Key figures">{kpis.map((k) => <KpiCard key={k.key} kpi={k} />)}</section>

      <div className="ops-grid">
        <section className="panel alerts-panel">
          <header><h2>Priority alerts</h2><span className="muted small">{alerts.length} open</span></header>
          {alerts.length ? <ul>{alerts.map((a) => <AlertRow key={a.title} alert={a} />)}</ul> : <p className="muted">Nothing needs attention right now.</p>}
        </section>
        <section className="panel feed-panel">
          <header><h2>Live operations feed</h2></header>
          {feed.length ? (
            <ol>{feed.map((f) => <li key={f.id}><p>{f.summary}</p><small>{f.actor_label} · {formatTime(f.created_at)}</small></li>)}</ol>
          ) : <p className="muted">No activity recorded yet.</p>}
        </section>
      </div>

      <div className="chart-grid">
        <Panel title="Event participation against venue capacity" source="generated" wide note="Bars in red are events with more registrants than their assigned venue has seats.">
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

        <Panel title="Participant arrivals" source="pack" note="The pack has no registration dates, so this shows arrivals instead of a registration trend.">
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

        <Panel title="Budget utilization" source="simulated">
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

        <Panel title="Accommodation occupancy" source="pack" note="No beds are allocated yet. The allocator arrives in Phase 2.">
          <div className="meters">
            {charts.occupancy.map((h) => (
              <Meter key={h.name} label={h.name} value={h.occupied} total={h.capacity} detail={`${formatNumber(h.occupied)} of ${formatNumber(h.capacity)}`} />
            ))}
          </div>
        </Panel>

        <Panel title="Department task progress" source="simulated" note={`${formatNumber(taskTotal)} tasks across ${charts.tasks.length} departments`}>
          <div className="meters">
            {charts.tasks.map((t) => <Meter key={t.department} label={t.department} value={t.done} total={t.total} detail={`${t.done} of ${t.total} done`} />)}
          </div>
        </Panel>

        <Panel title="Volunteer allocation" source="simulated">
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

        <Panel title="Event status" source="generated">
          <div className="meters">
            {charts.event_status.map((s) => (
              <Meter key={s.status} label={s.status[0].toUpperCase() + s.status.slice(1)} value={s.count} total={kpis.find((k) => k.key === "events")?.value as number} detail={`${s.count} events`} />
            ))}
          </div>
        </Panel>
      </div>
    </div>
  );
}

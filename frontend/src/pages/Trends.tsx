import { useQuery } from "@tanstack/react-query";
import { type ReactNode } from "react";
import {
  Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, ComposedChart, Line, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";

import { PageHead, QueryState, SectionHead, useFest } from "../components/ui";
import { api, type Dashboard, type Source } from "../lib/api";
import { formatLakh, formatMoney, formatNumber } from "../lib/format";

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
  contentStyle: { background: "var(--surface-2)", border: "1px solid var(--line)", borderRadius: 6, color: "var(--text)" },
  labelStyle: { color: "var(--text)" },
  itemStyle: { color: "var(--text)" },
  cursor: { fill: "var(--hover)" },
};

function SeatMarker({ cx, cy, index }: { cx?: number; cy?: number; index?: number }) {
  if (cx === undefined || cy === undefined) return <g key={index} />;
  return <line key={index} x1={cx - 11} x2={cx + 11} y1={cy} y2={cy} stroke="var(--cyan)" strokeWidth={3} strokeLinecap="round" />;
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

export function Trends() {
  const { base } = useFest();
  const query = useQuery({ queryKey: ["dashboard-trends", base], queryFn: () => api<Dashboard>(`${base}/dashboard`) });
  const data = query.data;
  const charts = data?.charts;
  const kpis = data?.kpis ?? [];
  const taskTotal = charts?.tasks.reduce((sum, t) => sum + t.total, 0) ?? 0;
  return (
    <div>
      <PageHead title="Trends" subtitle="The shape of the festival. For anything that needs action, use Decisions." />
      <QueryState query={query}>
        {charts && (
          <>
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
          </>
        )}
      </QueryState>
    </div>
  );
}

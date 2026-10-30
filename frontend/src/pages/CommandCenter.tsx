import { useQuery } from "@tanstack/react-query";
import { Activity, ArrowRight } from "lucide-react";
import { useState } from "react";
import { Link, Navigate, useOutletContext } from "react-router-dom";

import { Moment } from "../components/ui";
import { api, ApiError, type Alert, type Dashboard, type Festival, type Kpi, type User } from "../lib/api";
import { formatLakh, formatMoney, formatNumber, formatTime } from "../lib/format";

interface ScheduleEvent { code: string; name: string; start_at: string | null; end_at: string | null; venue: { name: string } | null }

const KPI_LINKS: Record<string, [string, string]> = {
  participants: ["/participants", "participants.read"], teams: ["/participants", "participants.read"], events: ["/events", "events.read"], venues: ["/venues", "venues.read"],
  beds: ["/accommodation", "accommodation.read"], volunteers: ["/volunteers", "volunteers.read"], budget: ["/finance", "finance.read"], spent: ["/finance", "finance.read"],
  approvals: ["/finance?tab=expenses&approval=pending", "finance.read"], incidents: ["/safety", "incidents.read"],
};
const SIMULATED = "Demo data: the pack has no records for this area";

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
      <span className="ribbon-label">{kpi.source === "simulated" && <i className="sim-dot" title={SIMULATED} />}{kpi.label}</span>
      <KpiValue kpi={kpi} />
    </>
  );
  return to && can(permission) ? <Link className="ribbon-cell" to={to} title={kpi.note ?? undefined}>{body}</Link> : <div className="ribbon-cell" title={kpi.note ?? undefined}>{body}</div>;
}

function Act({ alert, can }: { alert: Alert; can: (permission: string) => boolean }) {
  const { action } = alert;
  if (!action) return <p className="small muted">No screen resolves this yet. Assign it to someone.</p>;
  if (!can(action.permission)) return <p className="small muted">Your role can see this but cannot act on it.</p>;
  const Icon = action.to.startsWith("/ripple") ? Activity : ArrowRight;
  return <Link className="button-primary small" to={action.to}><Icon size={16} aria-hidden="true" />{action.label}</Link>;
}

function Meta({ alert }: { alert: Alert }) {
  return <small><span className="alert-level">{alert.severity}</span> · {alert.module}{alert.source === "simulated" && <i className="sim-dot" title={SIMULATED} />}</small>;
}

function NextUp({ festival }: { festival: Festival }) {
  const today = new Date(Date.now() + 5.5 * 3600 * 1000).toISOString().slice(0, 10);
  const schedule = useQuery({
    queryKey: ["schedule", festival.id, today],
    queryFn: () => api<{ events: ScheduleEvent[] }>(`/api/festivals/${festival.id}/events/schedule?day=${today}`),
  });
  const now = Date.now();
  const coming = (schedule.data?.events ?? []).filter((e) => e.end_at && new Date(e.end_at).getTime() > now).sort((a, b) => (a.start_at ?? "").localeCompare(b.start_at ?? "")).slice(0, 6);
  return (
    <section className="panel">
      <header><h2>Running now and next</h2></header>
      {coming.length ? (
        <ol className="next-up">
          {coming.map((e) => (
            <li key={e.code}>
              <time>{e.start_at && new Date(e.start_at).toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "Asia/Kolkata" })}</time>
              <strong>{e.name}</strong>
              <span className="muted small">{e.start_at && new Date(e.start_at).getTime() <= now ? "Running" : e.venue?.name}</span>
            </li>
          ))}
        </ol>
      ) : <p className="muted small">Nothing else is scheduled today.</p>}
    </section>
  );
}

export function CommandCenter() {
  const { festival, user } = useOutletContext<{ festival: Festival; user: User }>();
  const can = (permission: string) => user.permissions.includes(permission);
  const allowed = can("dashboard.read");
  const [picked, setPicked] = useState(0);
  const dashboard = useQuery({
    queryKey: ["dashboard", festival.id],
    queryFn: () => api<Dashboard>(`/api/festivals/${festival.id}/dashboard`),
    refetchInterval: 60000,  // A safety net: the live socket refreshes this as changes happen.
    enabled: allowed,
  });

  // Volunteers land on the phone check-in screen, and anyone else without the dashboard on the schedule.
  if (!allowed || (dashboard.error instanceof ApiError && dashboard.error.status === 403)) return <Navigate to={can("participants.checkin") ? "/check-in" : "/events"} replace />;
  if (dashboard.isPending) return <div className="skeleton-grid" role="status" aria-label="Loading decisions">{Array.from({ length: 5 }, (_, i) => <div key={i} />)}</div>;
  if (dashboard.isError) {
    return (
      <div className="state-error" role="alert">
        <p>{dashboard.error.message}</p>
        <button className="button-secondary" onClick={() => dashboard.refetch()}>Try again</button>
      </div>
    );
  }

  const { kpis, alerts, feed } = dashboard.data;
  const critical = alerts.filter((a) => a.severity === "critical").length;
  const open = alerts[Math.min(picked, alerts.length - 1)];

  return (
    <div>
      <Moment title={alerts.length ? `${alerts.length} ${alerts.length === 1 ? "decision needs" : "decisions need"} you` : "Nothing needs a decision"}>
        {critical > 0 && <p>{critical} {critical === 1 ? "is" : "are"} critical. Work down the list, and simulate before you change anything.</p>}
      </Moment>

      {open ? (
        <div className="triage">
          <ol className="queue" aria-label="Decisions, most urgent first">
            {alerts.map((a, i) => (
              <li key={a.title} className={`alert-${a.severity}`}>
                <button className="queue-row" aria-current={a === open ? "true" : undefined} onClick={() => setPicked(i)}>
                  <span className="queue-rank" aria-hidden="true">{i + 1}</span>
                  <span><strong>{a.title}</strong><Meta alert={a} /></span>
                </button>
                <div className="queue-more"><p>{a.detail}</p><Act alert={a} can={can} /></div>
              </li>
            ))}
          </ol>
          <section className={`queue-detail alert-${open.severity}`} aria-live="polite">
            <Meta alert={open} />
            <h2>{open.title}</h2>
            <p>{open.detail}</p>
            <Act alert={open} can={can} />
          </section>
        </div>
      ) : <p className="table-empty muted">The queue is clear. New problems appear here as they happen.</p>}

      <section className="ribbon" aria-label="Key figures">{kpis.map((k) => <KpiCell key={k.key} kpi={k} can={can} />)}</section>

      <div className="side-grid">
        {can("events.read") && <NextUp festival={festival} />}
        <section className="panel feed-panel">
          <header><h2>What just changed</h2><span className="muted small"><i className="sim-dot" /> Simulated data</span></header>
          {feed.length ? (
            <ol tabIndex={0} aria-label="Recent activity">{feed.map((f) => <li key={f.id}><p>{f.summary}</p><small>{f.actor_label} · {formatTime(f.created_at)}</small></li>)}</ol>
          ) : <p className="muted">No activity recorded yet.</p>}
        </section>
      </div>
    </div>
  );
}

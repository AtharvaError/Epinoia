import { useQuery } from "@tanstack/react-query";
import { Activity } from "lucide-react";
import { Link } from "react-router-dom";

import { type Column, DataTable, PageHead, Pill, QueryState, useFest } from "../components/ui";
import { api } from "../lib/api";
import { formatNumber } from "../lib/format";

interface Vendor {
  code: string; name: string; menu: string | null; dietary_options: string[]; hours: string | null; capacity: number | null; status: string;
  hygiene_certificate_expiry: string | null; compliance: string; can_serve: boolean; payment_terms: string | null; settlement: string | null;
}
interface Cover { diet: string; people: number; vendors: string[]; seats: number; gap: number }
interface Data { vendors: Vendor[]; coverage: Cover[]; unknown_diet: number; people: number; seats: number }

const COMPLIANCE: Record<string, [string, "ok" | "warn" | "bad"]> = {
  valid: ["Valid", "ok"], expires_during_festival: ["Expires during the festival", "warn"], expired: ["Expired", "bad"], missing: ["No certificate", "bad"],
};

export function Vendors() {
  const { base, can } = useFest();
  const query = useQuery({ queryKey: ["vendors", base], queryFn: () => api<Data>(`${base}/vendors`) });
  const columns: Column<Vendor>[] = [
    { label: "Vendor", value: (v) => v.name, cell: (v) => <><strong>{v.name}</strong><br /><small className="muted">{v.menu}</small></> },
    { label: "Diets listed", cell: (v) => v.dietary_options.join(", ") || "None stated" },
    { label: "Seats", value: (v) => v.capacity, cell: (v) => (v.capacity === null ? <span className="unknown">Unknown</span> : formatNumber(v.capacity)), className: "num" },
    { label: "Hours", cell: (v) => v.hours ?? <span className="unknown">Unknown</span> },
    { label: "Hygiene", value: (v) => v.hygiene_certificate_expiry, cell: (v) => <><Pill value={COMPLIANCE[v.compliance][0]} tone={COMPLIANCE[v.compliance][1]} /><br /><small className="muted">{v.hygiene_certificate_expiry ?? "No date"}</small></> },
    { label: "Settlement", cell: (v) => <>{v.payment_terms ?? <span className="unknown">Unknown</span>}{v.settlement && <><br /><small className="muted">{v.settlement}</small></>}</> },
    { label: "Status", value: (v) => v.status, cell: (v) => <Pill value={v.status} tone={v.status === "active" ? (v.can_serve ? "ok" : "warn") : "bad"} /> },
    ...(can("simulation.run") ? [{ label: "Actions", cell: (v: Vendor) => (v.status === "active" ? <Link className="button-ghost" to={`/ripple?scenario=vendor_shutdown&vendor_code=${v.code}`}><Activity size={14} aria-hidden="true" /> Simulate shutdown</Link> : null) }] : []),
  ];
  const coverColumns: Column<Cover>[] = [
    { label: "Diet", value: (c) => c.diet, cell: (c) => <strong>{c.diet}</strong> },
    { label: "People", value: (c) => c.people, cell: (c) => formatNumber(c.people), className: "num" },
    { label: "Compliant vendors", value: (c) => c.vendors.length, cell: (c) => (c.vendors.length ? c.vendors.join(", ") : <span className="over-text">None</span>) },
    { label: "Seats at one sitting", value: (c) => c.seats, cell: (c) => formatNumber(c.seats), className: "num" },
    { label: "Short by", value: (c) => c.gap, cell: (c) => <span className={c.gap ? "over-text" : ""}>{formatNumber(c.gap)}</span>, className: "num" },
  ];
  return (
    <div>
      <PageHead title="Food" subtitle="Coverage counts only active vendors with a current hygiene certificate" />
      <QueryState query={query}>
        {query.data && (
          <>
            <div className="stat-row page-stats">
              <div><span>People to feed</span><strong>{formatNumber(query.data.people)}</strong></div>
              <div><span>Compliant seats at one sitting</span><strong>{formatNumber(query.data.seats)}</strong></div>
              <div><span>Diet unknown</span><strong className="warn-text">{formatNumber(query.data.unknown_diet)}</strong><small>Blank is never treated as covered</small></div>
            </div>
            <section className="panel flush">
              <header><h2>Dietary coverage</h2></header>
              <p className="panel-note">The pack has no meal times and does not link people to vendors, so this compares each diet with compliant seats. Vendor capacity is read as seats at one sitting.</p>
              <DataTable rows={query.data.coverage} columns={coverColumns} rowKey={(c) => c.diet} empty="No diets recorded." />
            </section>
            <h2 className="list-title">Vendors</h2>
            <DataTable rows={query.data.vendors} columns={columns} rowKey={(v) => v.code} empty="No vendors yet." />
          </>
        )}
      </QueryState>
    </div>
  );
}

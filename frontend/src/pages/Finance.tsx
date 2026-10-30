import { useQuery } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";

import {
  type Column, DataTable, Dialog, ErrorNote, Facts, Field, PageHead, Pagination, Pill, QueryState, ReasonForm, Simulated, Tabs, useAction, useFest, usePaged,
} from "../components/ui";
import { api } from "../lib/api";
import { formatMoney } from "../lib/format";

interface Line { department: string; approved: string; committed: string; external_stays: string; actual: string; estimated: string; pending: string; remaining: string }
interface Summary { lines: Line[]; totals: Record<"approved" | "committed" | "actual" | "pending" | "remaining", string>; sponsors_committed: string; sponsors_received: string }
interface Expense { id: number; department: string; category: string; description: string; estimated_amount: string; actual_amount: string | null; approval_status: string; payment_status: string; is_simulated: boolean }
interface Sponsor { id: number; name: string; tier: string | null; committed_amount: string; received_amount: string; outstanding: string; is_simulated: boolean }

const TONE: Record<string, "ok" | "warn" | "bad"> = { approved: "ok", pending: "warn", rejected: "bad", paid: "ok" };

function ExpenseDrawer({ expense, onClose }: { expense: Expense; onClose: () => void }) {
  const { base, can } = useFest();
  const path = `${base}/finance/expenses/${expense.id}`;
  const [reason, setReason] = useState("");
  const [actual, setActual] = useState(expense.estimated_amount);
  const [view, setView] = useState<"detail" | "reject">("detail");
  const approve = useAction(() => api(`${path}/approve`, { method: "POST", body: JSON.stringify({ reason: reason || null }) }), { success: "Expense approved", onDone: onClose });
  const reject = useAction((text: string) => api(`${path}/reject`, { method: "POST", body: JSON.stringify({ reason: text }) }), { success: "Expense rejected", onDone: onClose });
  const pay = useAction(() => api(`${path}/pay`, { method: "POST", body: JSON.stringify({ actual_amount: Number(actual) }) }), { success: "Payment recorded", onDone: onClose });
  return (
    <Dialog title={expense.description} onClose={onClose}>
      {view === "reject" ? <ReasonForm label="Why is this being rejected?" action="Reject expense" danger onSubmit={(r) => reject.mutate(r)} pending={reject.isPending} error={reject.error} /> : (
        <>
          <div className="row-actions"><Pill value={expense.approval_status} tone={TONE[expense.approval_status]} /><Pill value={expense.payment_status} tone={TONE[expense.payment_status]} />{expense.is_simulated && <Simulated />}</div>
          <Facts items={[["Department", expense.department], ["Category", expense.category], ["Estimated", formatMoney(expense.estimated_amount)], ["Actual", expense.actual_amount ? formatMoney(expense.actual_amount) : "Not yet known"]]} />
          {expense.approval_status === "pending" && can("finance.approve") && (
            <form className="form" onSubmit={(e) => { e.preventDefault(); approve.mutate(); }}>
              <h3>Decision</h3>
              <Field label="Reason" hint="Required if this takes the department over budget"><textarea rows={2} value={reason} onChange={(e) => setReason(e.target.value)} /></Field>
              <ErrorNote error={approve.error} />
              <div className="row-actions"><button className="button-primary" disabled={approve.isPending}>Approve</button><button type="button" className="button-ghost danger" onClick={() => setView("reject")}>Reject</button></div>
            </form>
          )}
          {expense.approval_status === "approved" && expense.payment_status !== "paid" && can("finance.write") && (
            <form className="form" onSubmit={(e) => { e.preventDefault(); pay.mutate(); }}>
              <h3>Record payment</h3>
              <Field label="Amount paid (₹)"><input type="number" min={0} required value={actual} onChange={(e) => setActual(e.target.value)} /></Field>
              <ErrorNote error={pay.error} />
              <button className="button-primary" disabled={pay.isPending}>Record payment</button>
            </form>
          )}
        </>
      )}
    </Dialog>
  );
}

function ExpenseForm({ departments, onClose }: { departments: string[]; onClose: () => void }) {
  const { base } = useFest();
  const [form, setForm] = useState({ department: departments[0] ?? "", category: "", description: "", estimated_amount: "" });
  const set = (key: keyof typeof form) => (e: { target: { value: string } }) => setForm({ ...form, [key]: e.target.value });
  const save = useAction(() => api(`${base}/finance/expenses`, { method: "POST", body: JSON.stringify({ ...form, estimated_amount: Number(form.estimated_amount) }) }), { success: "Expense added", onDone: onClose });
  return (
    <Dialog title="Add expense" onClose={onClose}>
      <form className="form form-grid" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
        <Field label="Department"><select value={form.department} onChange={set("department")}>{departments.map((d) => <option key={d}>{d}</option>)}</select></Field>
        <Field label="Category"><input required minLength={2} value={form.category} onChange={set("category")} /></Field>
        <div className="span-all"><Field label="Description"><input required minLength={2} value={form.description} onChange={set("description")} /></Field></div>
        <Field label="Estimated amount (₹)"><input type="number" min={0} required value={form.estimated_amount} onChange={set("estimated_amount")} /></Field>
        <div className="span-all"><ErrorNote error={save.error} /><button className="button-primary" disabled={save.isPending}>Save expense</button></div>
      </form>
    </Dialog>
  );
}

function BudgetForm({ line, onClose }: { line: Line; onClose: () => void }) {
  const { base } = useFest();
  const [amount, setAmount] = useState(line.approved);
  const [reason, setReason] = useState("");
  const save = useAction(() => api(`${base}/finance/budgets/${encodeURIComponent(line.department)}`, { method: "PUT", body: JSON.stringify({ approved_amount: Number(amount), reason }) }), { success: "Budget updated", onDone: onClose });
  return (
    <Dialog title={`${line.department} budget`} onClose={onClose}>
      <form className="form" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
        <p className="muted small">{formatMoney(line.committed)} is committed and {formatMoney(line.pending)} is pending.</p>
        <Field label="Approved budget (₹)"><input type="number" min={0} required value={amount} onChange={(e) => setAmount(e.target.value)} /></Field>
        <Field label="Reason"><textarea required minLength={5} rows={2} value={reason} onChange={(e) => setReason(e.target.value)} /></Field>
        <ErrorNote error={save.error} />
        <button className="button-primary" disabled={save.isPending}>Save budget</button>
      </form>
    </Dialog>
  );
}

function SponsorForm({ existing, onClose }: { existing?: Sponsor; onClose: () => void }) {
  const { base } = useFest();
  const [form, setForm] = useState({ name: existing?.name ?? "", tier: existing?.tier ?? "", committed_amount: existing?.committed_amount ?? "", received_amount: existing?.received_amount ?? "0" });
  const set = (key: keyof typeof form) => (e: { target: { value: string } }) => setForm({ ...form, [key]: e.target.value });
  const save = useAction(() => api(existing ? `${base}/finance/sponsors/${existing.id}` : `${base}/finance/sponsors`, {
    method: existing ? "PATCH" : "POST", body: JSON.stringify({ ...form, tier: form.tier || null, committed_amount: Number(form.committed_amount), received_amount: Number(form.received_amount) }),
  }), { success: "Sponsor saved", onDone: onClose });
  return (
    <Dialog title={existing ? existing.name : "Add sponsor"} onClose={onClose}>
      <form className="form form-grid" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
        <Field label="Name"><input required minLength={2} value={form.name} onChange={set("name")} /></Field>
        <Field label="Tier"><input value={form.tier} onChange={set("tier")} /></Field>
        <Field label="Committed (₹)"><input type="number" min={0} required value={form.committed_amount} onChange={set("committed_amount")} /></Field>
        <Field label="Received (₹)"><input type="number" min={0} required value={form.received_amount} onChange={set("received_amount")} /></Field>
        <div className="span-all"><ErrorNote error={save.error} /><button className="button-primary" disabled={save.isPending}>Save sponsor</button></div>
      </form>
    </Dialog>
  );
}

export function Finance() {
  const { base, can } = useFest();
  const [params] = useSearchParams();
  const [tab, setTab] = useState<"budgets" | "expenses" | "sponsors">(params.get("tab") === "expenses" ? "expenses" : "budgets");
  const [approval, setApproval] = useState(params.get("approval") ?? "");
  const [department, setDepartment] = useState("");
  const [sort, setSort] = useState("");
  const [page, setPage] = useState(1);
  const [expense, setExpense] = useState<Expense>();
  const [budget, setBudget] = useState<Line>();
  const [sponsor, setSponsor] = useState<Sponsor | "new">();
  const [adding, setAdding] = useState(false);
  useEffect(() => setPage(1), [approval, department, sort]);
  const summary = useQuery({ queryKey: ["finance-summary", base], queryFn: () => api<Summary>(`${base}/finance/summary`) });
  const expenses = usePaged<Expense>(`${base}/finance/expenses`, { approval, department, sort, page }, tab === "expenses");
  const sponsors = useQuery({ queryKey: ["sponsors", base], queryFn: () => api<Sponsor[]>(`${base}/finance/sponsors`), enabled: tab === "sponsors" });
  const lines = summary.data?.lines ?? [];

  const lineColumns: Column<Line>[] = [
    { label: "Department", value: (l) => l.department, cell: (l) => <strong>{l.department}</strong> },
    { label: "Approved", value: (l) => Number(l.approved), cell: (l) => formatMoney(l.approved), className: "num" },
    { label: "Committed", value: (l) => Number(l.committed), cell: (l) => <>{formatMoney(l.committed)}{Number(l.external_stays) > 0 && <><br /><small className="muted">{formatMoney(l.external_stays)} external stays</small></>}</>, className: "num" },
    { label: "Pending", value: (l) => Number(l.pending), cell: (l) => formatMoney(l.pending), className: "num" },
    { label: "Remaining", value: (l) => Number(l.remaining), cell: (l) => <span className={Number(l.remaining) < 0 ? "over-text" : ""}>{formatMoney(l.remaining)}</span>, className: "num" },
    { label: "Used", cell: (l) => { const used = Number(l.approved) ? (Number(l.committed) / Number(l.approved)) * 100 : 0; return <div className="meter-track" role="progressbar" aria-label={`${l.department} budget used`} aria-valuenow={Math.round(Math.min(used, 100))} aria-valuemin={0} aria-valuemax={100}><div className={used > 100 ? "over" : ""} style={{ width: `${Math.min(100, used)}%` }} /></div>; } },
  ];
  const expenseColumns: Column<Expense>[] = [
    { label: "Expense", sort: "expense", cell: (e) => <><strong>{e.description}</strong><br /><small className="muted">{e.category}</small></> },
    { label: "Department", cell: (e) => e.department },
    { label: "Estimated", sort: "estimated", cell: (e) => formatMoney(e.estimated_amount), className: "num" },
    { label: "Actual", sort: "actual", cell: (e) => (e.actual_amount ? formatMoney(e.actual_amount) : ""), className: "num" },
    { label: "Approval", sort: "approval", cell: (e) => <Pill value={e.approval_status} tone={TONE[e.approval_status]} /> },
    { label: "Payment", sort: "payment", cell: (e) => <Pill value={e.payment_status} tone={TONE[e.payment_status]} /> },
  ];
  const sponsorColumns: Column<Sponsor>[] = [
    { label: "Sponsor", value: (s) => s.name, cell: (s) => <strong>{s.name}</strong> },
    { label: "Tier", value: (s) => s.tier, cell: (s) => s.tier ?? "" },
    { label: "Committed", value: (s) => Number(s.committed_amount), cell: (s) => formatMoney(s.committed_amount), className: "num" },
    { label: "Received", value: (s) => Number(s.received_amount), cell: (s) => formatMoney(s.received_amount), className: "num" },
    { label: "Outstanding", value: (s) => Number(s.outstanding), cell: (s) => formatMoney(s.outstanding), className: "num" },
  ];
  const t = summary.data?.totals;
  return (
    <div>
      <PageHead title="Money" simulated subtitle="Budgets, expenses and sponsors are simulated. External stay costs come from pack rates.">
        {can("finance.write") && <button className="button-ghost" onClick={() => setSponsor("new")}><Plus size={16} /> Add sponsor</button>}
        {can("finance.write") && <button className="button-primary small" onClick={() => setAdding(true)}><Plus size={16} /> Add expense</button>}
      </PageHead>
      <QueryState query={summary}>
        {t && summary.data && (
          <div className="stat-row page-stats">
            <div><span>Approved</span><strong>{formatMoney(t.approved)}</strong></div>
            <div><span>Committed</span><strong>{formatMoney(t.committed)}</strong></div>
            <div><span>Paid so far</span><strong>{formatMoney(t.actual)}</strong></div>
            <div><span>Pending approval</span><strong>{formatMoney(t.pending)}</strong></div>
            <div><span>Remaining</span><strong className={Number(t.remaining) < 0 ? "over-text" : ""}>{formatMoney(t.remaining)}</strong></div>
            <div><span>Sponsors received</span><strong>{formatMoney(summary.data.sponsors_received)}</strong><small>of {formatMoney(summary.data.sponsors_committed)}</small></div>
          </div>
        )}
      </QueryState>
      <Tabs value={tab} onChange={setTab} options={[["budgets", "Budgets"], ["expenses", "Expenses"], ["sponsors", "Sponsors"]]} />
      {tab === "budgets" && <DataTable rows={lines} columns={lineColumns} rowKey={(l) => l.department} empty="No budgets yet." onRow={can("finance.approve") ? setBudget : undefined} />}
      {tab === "expenses" && (
        <>
          <div className="filters">
            <select aria-label="Approval" value={approval} onChange={(e) => setApproval(e.target.value)}><option value="">All approvals</option><option value="pending">Pending</option><option value="approved">Approved</option><option value="rejected">Rejected</option></select>
            <select aria-label="Department" value={department} onChange={(e) => setDepartment(e.target.value)}><option value="">All departments</option>{lines.map((l) => <option key={l.department}>{l.department}</option>)}</select>
          </div>
          <QueryState query={expenses}>{expenses.data && <><DataTable rows={expenses.data.items} columns={expenseColumns} rowKey={(e) => e.id} empty="No expenses match." onRow={setExpense} sort={sort} onSort={setSort} /><Pagination page={expenses.data.page} pages={expenses.data.pages} total={expenses.data.total} onPage={setPage} /></>}</QueryState>
        </>
      )}
      {tab === "sponsors" && <QueryState query={sponsors}>{sponsors.data && <DataTable rows={sponsors.data} columns={sponsorColumns} rowKey={(s) => s.id} empty="No sponsors yet." onRow={can("finance.write") ? setSponsor : undefined} />}</QueryState>}
      {expense && <ExpenseDrawer expense={expense} onClose={() => setExpense(undefined)} />}
      {budget && <BudgetForm line={budget} onClose={() => setBudget(undefined)} />}
      {sponsor && <SponsorForm existing={sponsor === "new" ? undefined : sponsor} onClose={() => setSponsor(undefined)} />}
      {adding && <ExpenseForm departments={lines.map((l) => l.department)} onClose={() => setAdding(false)} />}
    </div>
  );
}

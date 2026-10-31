import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Download,
  PackagePlus,
  Plus,
  Search,
  Truck,
} from "lucide-react";
import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";

import {
  ConditionSegmentedBar,
  EquipmentAttentionStrip,
  EquipmentOptions,
  EquipmentRequestDrawer,
  EquipmentRequestRow,
  EquipmentStockRow,
  EquipmentSummary,
  EquipmentSummaryRibbon,
  ExternalReceiptModal,
  InspectResourceModal,
  LedgerRow,
  NewEquipmentRequestDrawer,
  RepairResourceModal,
  ReportFailureModal,
  StockItemDrawer,
  TransferRow,
  formatTime,
  readinessBadge,
} from "../components/Equipment";
import {
  type Column,
  DataTable,
  Dialog,
  ErrorNote,
  Facts,
  Field,
  PageHead,
  Pagination,
  Pill,
  QueryState,
  SectionHead,
  Tabs,
  useAction,
  useDebounced,
  useFest,
  usePaged,
} from "../components/ui";
import { api } from "../lib/api";

export function Transport() {
  const { base, can } = useFest();
  const [params, setParams] = useSearchParams();

  // Tab state: requests | stock | transfers | ledger
  const [tab, setTab] = useState<"requests" | "stock" | "transfers" | "ledger">(
    (params.get("tab") as any) ?? "requests"
  );

  // Summary query
  const summaryQuery = useQuery({
    queryKey: ["equipment-summary", base],
    queryFn: () => api<EquipmentSummary>(`${base}/equipment/summary`),
  });

  // Requests state
  const [reqStatus, setReqStatus] = useState(params.get("status") ?? "");
  const [reqSearch, setReqSearch] = useState("");
  const [reqMineOnly, setReqMineOnly] = useState(false);
  const [reqPage, setReqPage] = useState(1);
  const [selectedReqCode, setSelectedReqCode] = useState<string | null>(params.get("open") ?? null);
  const [newReqOpen, setNewReqOpen] = useState(params.get("new") === "1");
  const qReq = useDebounced(reqSearch);

  const requestsQuery = usePaged<EquipmentRequestRow>(
    `${base}/equipment/requests`,
    { q: qReq, status: reqStatus, mine: reqMineOnly, page: reqPage },
    tab === "requests"
  );

  // Campus Stock state
  const [stockSearch, setStockSearch] = useState(params.get("search") ?? "");
  const [stockLocation, setStockLocation] = useState("");
  const [stockCondition, setStockCondition] = useState("");
  const [stockPage, setStockPage] = useState(1);
  const [selectedStockItem, setSelectedStockItem] = useState<string | null>(null);
  const [inspectModal, setInspectModal] = useState<{ code: string; item: string; loc: string; max: number } | null>(null);
  const [repairModal, setRepairModal] = useState<{ code: string; item: string; loc: string; max: number } | null>(null);
  const [failureModal, setFailureModal] = useState<{ code: string; item: string; loc: string } | null>(null);
  const [externalReceiptOpen, setExternalReceiptOpen] = useState(false);
  const qStock = useDebounced(stockSearch);

  const stockQuery = usePaged<EquipmentStockRow>(
    `${base}/equipment/stock`,
    { q: qStock, location: stockLocation, condition: stockCondition, page: stockPage },
    tab === "stock"
  );

  // Transfers state
  const [transferStatus, setTransferStatus] = useState("");
  const [newTransferOpen, setNewTransferOpen] = useState(false);
  const [selectedTransfer, setSelectedTransfer] = useState<TransferRow | null>(null);
  const [discrepancyTransfer, setDiscrepancyTransfer] = useState<TransferRow | null>(null);

  const transfersQuery = useQuery({
    queryKey: ["equipment-transfers", base, transferStatus],
    queryFn: () =>
      api<{ open_transfers: TransferRow[] }>(`${base}/equipment/requests`).then((r) => r.open_transfers),
    enabled: tab === "transfers",
  });

  // Movement History (Ledger) state
  const [ledgerAction, setLedgerAction] = useState("");
  const [ledgerSearch, setLedgerSearch] = useState("");
  const [ledgerPage, setLedgerPage] = useState(1);
  const qLedger = useDebounced(ledgerSearch);

  const ledgerQuery = usePaged<LedgerRow>(
    `${base}/equipment/ledger`,
    { q: qLedger, action: ledgerAction, page: ledgerPage },
    tab === "ledger"
  );

  // Handle attention strip chip click
  const handleAttentionClick = (targetTab: "requests" | "stock" | "transfers", filter?: string) => {
    setTab(targetTab);
    if (targetTab === "requests") {
      setReqStatus(filter === "shortage" ? "submitted" : filter ?? "");
    } else if (targetTab === "stock") {
      setStockCondition(filter ?? "");
    } else if (targetTab === "transfers") {
      setTransferStatus(filter ?? "");
    }
  };

  // Keep URL in sync
  useEffect(() => {
    setParams({ tab }, { replace: true });
  }, [tab, setParams]);

  // Request Columns
  const requestColumns: Column<EquipmentRequestRow>[] = [
    { label: "Request ID", sort: "code", cell: (r) => <span className="mono">{r.code}</span> },
    {
      label: "Event",
      cell: (r) => (
        <div>
          <strong>{r.event ? `${r.event.code} — ${r.event.name}` : "—"}</strong>
          {r.source === "event_csv" && <div><small className="tone-ok">from events.csv</small></div>}
        </div>
      ),
    },
    {
      label: "Equipment Item",
      cell: (r) => (
        <div>
          <strong>{r.quantity} × {r.item}</strong>
          {r.source_text && <div><small className="muted">{r.source_text}</small></div>}
        </div>
      ),
    },
    { label: "Destination", cell: (r) => r.destination },
    { label: "Needed by", cell: (r) => formatTime(r.needed_by) },
    {
      label: "Priority",
      cell: (r) => <Pill value={r.priority} tone={r.priority === "critical" || r.priority === "high" ? "warn" : "info"} />,
    },
    {
      label: "Availability Breakdown",
      cell: (r) => {
        const a = r.availability;
        return (
          <small>
            Free: <strong style={{ color: "#34d399" }}>{a.good_available}</strong> · Comm: {a.committed_elsewhere} · Rep: {a.under_repair} · Chk: {a.needs_check}
            {a.shortage > 0 && <span style={{ color: "#f87171", fontWeight: 700 }}> · Short: {a.shortage}</span>}
          </small>
        );
      },
    },
    {
      label: "Readiness",
      cell: (r) => {
        const b = readinessBadge(r.readiness);
        return <Pill value={b.label} tone={b.tone} />;
      },
    },
    {
      label: "Status",
      cell: (r) => <Pill value={r.status} tone={r.status === "approved" ? "ok" : r.status === "rejected" ? "bad" : "warn"} />,
    },
  ];

  // Stock Columns
  const stockColumns: Column<EquipmentStockRow>[] = [
    {
      label: "Equipment Item",
      cell: (s) => <strong>{s.item}</strong>,
    },
    {
      label: "Condition Breakdown",
      cell: (s) => (
        <ConditionSegmentedBar
          good={s.good_available}
          needsCheck={s.needs_check}
          underRepair={s.under_repair}
          total={s.total_quantity}
        />
      ),
    },
    { label: "Total Units", cell: (s) => s.total_quantity, className: "num" },
    { label: "Good Free", cell: (s) => <strong style={{ color: "#34d399" }}>{s.good_available}</strong>, className: "num" },
    { label: "Committed", cell: (s) => s.committed, className: "num" },
    { label: "Needs Check", cell: (s) => <span style={{ color: s.needs_check > 0 ? "#fbbf24" : "inherit" }}>{s.needs_check}</span>, className: "num" },
    { label: "Under Repair", cell: (s) => <span style={{ color: s.under_repair > 0 ? "#f87171" : "inherit" }}>{s.under_repair}</span>, className: "num" },
    {
      label: "Location Distribution",
      cell: (s) => (
        <small className="muted">
          {s.locations.map((l) => `${l.location}: ${l.quantity}`).join(" · ")}
        </small>
      ),
    },
    {
      label: "Actions",
      cell: (s) => (
        <button
          type="button"
          className="button-ghost small"
          onClick={(e) => {
            e.stopPropagation();
            setSelectedStockItem(s.item);
          }}
        >
          Details
        </button>
      ),
    },
  ];

  // Transfer Columns
  const transferColumns: Column<TransferRow>[] = [
    { label: "ID", cell: (t) => `#${t.id}` },
    { label: "Item", cell: (t) => <strong>{t.quantity_planned} × {t.item}</strong> },
    { label: "From", cell: (t) => t.from_location },
    { label: "To", cell: (t) => t.to_location },
    {
      label: "Stage",
      cell: (t) => (
        <Pill
          value={t.status}
          tone={t.status === "COMPLETED" || t.status === "RECEIVED" ? "ok" : t.status === "IN_TRANSIT" ? "info" : "warn"}
        />
      ),
    },
    {
      label: "Discrepancy",
      cell: (t) =>
        t.discrepancy_note ? (
          <span className="tone-bad small" style={{ fontWeight: 600 }}>{t.discrepancy_note}</span>
        ) : (
          <span className="muted small">None</span>
        ),
    },
    {
      label: "Action",
      cell: (t) => (
        <button
          type="button"
          className="button-secondary small"
          onClick={(e) => {
            e.stopPropagation();
            setSelectedTransfer(t);
          }}
        >
          Manage Stage
        </button>
      ),
    },
  ];

  // Movement Ledger Columns
  const ledgerColumns: Column<LedgerRow>[] = [
    { label: "Timestamp", cell: (l) => formatTime(l.timestamp) },
    { label: "Action", cell: (l) => <span className="mono">{l.action}</span> },
    { label: "Item", cell: (l) => <strong>{l.quantity} × {l.item}</strong> },
    { label: "From Location", cell: (l) => l.from_location ?? "—" },
    { label: "To Location", cell: (l) => l.to_location ?? "—" },
    { label: "Operator", cell: (l) => l.user_name ?? "System" },
    { label: "Notes", cell: (l) => <span className="small muted">{l.notes ?? "—"}</span> },
  ];

  return (
    <div>
      {/* Header max 72px */}
      <PageHead
        title="Inventory Control"
        subtitle="Logistics catalogue, 5-location stock balances, request allocation, and transfer lifecycle"
      >
        {can("equipment.request") && (
          <button type="button" className="button-primary small" onClick={() => setNewReqOpen(true)}>
            <Plus size={16} /> New equipment request
          </button>
        )}
        {can("equipment.approve") && (
          <button type="button" className="button-secondary small" onClick={() => setNewTransferOpen(true)}>
            <Truck size={16} /> New transfer
          </button>
        )}
      </PageHead>

      {/* 5 Equal Metric Cards */}
      <EquipmentSummaryRibbon summary={summaryQuery.data} />

      {/* Attention Strip */}
      <EquipmentAttentionStrip
        attention={summaryQuery.data?.attention}
        onFilterChange={handleAttentionClick}
      />

      {/* 4 Tabs with Badges */}
      <Tabs
        value={tab}
        onChange={setTab}
        options={[
          ["requests", `Equipment requests (${summaryQuery.data?.counts.open_requests ?? 0})`],
          ["stock", `Campus stock (${summaryQuery.data?.counts.stock_items ?? 18})`],
          ["transfers", `Transfers (${summaryQuery.data?.counts.open_transfers ?? 0})`],
          ["ledger", "Movement history"],
        ]}
      />

      {/* TAB 1: Equipment Requests */}
      {tab === "requests" && (
        <section className="panel stack">
          <div className="toolbar">
            <div className="tabs" role="group" aria-label="Request status filter">
              {([
                ["", "All requests"],
                ["submitted", "Awaiting approval"],
                ["approved", "Approved"],
                ["partially_approved", "Partial"],
                ["returning", "Returning"],
                ["closed", "Closed"],
                ["rejected", "Rejected"],
              ] as const).map(([key, label]) => (
                <button
                  key={key}
                  type="button"
                  className={reqStatus === key ? "active" : ""}
                  onClick={() => setReqStatus(key)}
                >
                  {label}
                </button>
              ))}
            </div>

            <div className="search">
              <Search size={16} className="muted" />
              <input
                type="search"
                placeholder="Search request or event..."
                value={reqSearch}
                onChange={(e) => setReqSearch(e.target.value)}
              />
            </div>

            <label className="check-inline">
              <input
                type="checkbox"
                checked={reqMineOnly}
                onChange={(e) => setReqMineOnly(e.target.checked)}
              />
              <span>My requests only</span>
            </label>
          </div>

          <QueryState query={requestsQuery}>
            {requestsQuery.data && (
              <>
                <DataTable
                  rows={requestsQuery.data.items}
                  columns={requestColumns}
                  rowKey={(r) => r.code}
                  onRow={(r) => setSelectedReqCode(r.code)}
                  empty="No equipment requests match these filters."
                />
                <Pagination
                  page={requestsQuery.data.page}
                  pages={requestsQuery.data.pages}
                  total={requestsQuery.data.total}
                  onPage={setReqPage}
                />
              </>
            )}
          </QueryState>
        </section>
      )}

      {/* TAB 2: Campus Equipment Stock */}
      {tab === "stock" && (
        <section className="panel stack">
          <div className="toolbar">
            <div className="search">
              <Search size={16} className="muted" />
              <input
                type="search"
                placeholder="Search item name..."
                value={stockSearch}
                onChange={(e) => setStockSearch(e.target.value)}
              />
            </div>

            <select
              aria-label="Filter by Location"
              value={stockLocation}
              onChange={(e) => setStockLocation(e.target.value)}
            >
              <option value="">All 5 campus locations</option>
              {["Registration Desk", "Store Room A", "Main Stage", "Sports Complex", "Van 1"].map((loc) => (
                <option key={loc} value={loc}>
                  {loc} {loc === "Van 1" ? "(Unverified mobile)" : ""}
                </option>
              ))}
            </select>

            <select
              aria-label="Filter by Condition"
              value={stockCondition}
              onChange={(e) => setStockCondition(e.target.value)}
            >
              <option value="">Any condition</option>
              <option value="good">Good (Deployable)</option>
              <option value="needs check">Needs check (Inspect)</option>
              <option value="under repair">Under repair</option>
            </select>

            {can("equipment.approve") && (
              <button
                type="button"
                className="button-primary small"
                onClick={() => setExternalReceiptOpen(true)}
              >
                <PackagePlus size={16} /> External Receipt / Rental
              </button>
            )}
          </div>

          <QueryState query={stockQuery}>
            {stockQuery.data && (
              <>
                <DataTable
                  rows={stockQuery.data.items}
                  columns={stockColumns}
                  rowKey={(s) => s.item}
                  onRow={(s) => setSelectedStockItem(s.item)}
                  empty="No stock items match these filters."
                />
                <Pagination
                  page={stockQuery.data.page}
                  pages={stockQuery.data.pages}
                  total={stockQuery.data.total}
                  onPage={setStockPage}
                />
              </>
            )}
          </QueryState>
        </section>
      )}

      {/* TAB 3: Transfers */}
      {tab === "transfers" && (
        <section className="panel stack">
          <SectionHead title="Campus Transfer Lifecycle">
            <span className="muted small">REQUESTED → APPROVED → DISPATCHED → IN_TRANSIT → RECEIVED → COMPLETED</span>
          </SectionHead>

          <QueryState query={transfersQuery}>
            {transfersQuery.data && (
              <DataTable
                rows={transfersQuery.data}
                columns={transferColumns}
                rowKey={(t) => t.id}
                onRow={(t) => setSelectedTransfer(t)}
                empty="No active or pending transfers right now."
              />
            )}
          </QueryState>
        </section>
      )}

      {/* TAB 4: Movement History (Ledger) */}
      {tab === "ledger" && (
        <section className="panel stack">
          <div className="toolbar">
            <div className="search">
              <Search size={16} className="muted" />
              <input
                type="search"
                placeholder="Search ledger by item or code..."
                value={ledgerSearch}
                onChange={(e) => setLedgerSearch(e.target.value)}
              />
            </div>

            <select
              aria-label="Action filter"
              value={ledgerAction}
              onChange={(e) => setLedgerAction(e.target.value)}
            >
              <option value="">All actions</option>
              <option value="create_request">Request Created</option>
              <option value="approve_request">Request Approved</option>
              <option value="reject_request">Request Rejected</option>
              <option value="transfer_dispatch">Transfer Dispatched</option>
              <option value="transfer_receive">Transfer Received</option>
              <option value="return_receive">Return Completed</option>
              <option value="inspect">Inspected</option>
              <option value="repair">Repaired</option>
              <option value="report_failure">Failure Reported</option>
              <option value="external_receipt">External Receipt</option>
            </select>

            <a
              href={`${base}/equipment/ledger/export.csv`}
              download="festos-equipment-ledger.csv"
              className="button-ghost small"
            >
              <Download size={14} /> Export CSV
            </a>
          </div>

          <QueryState query={ledgerQuery}>
            {ledgerQuery.data && (
              <>
                <DataTable
                  rows={ledgerQuery.data.items}
                  columns={ledgerColumns}
                  rowKey={(l) => l.id}
                  empty="No movements match."
                />
                <Pagination
                  page={ledgerQuery.data.page}
                  pages={ledgerQuery.data.pages}
                  total={ledgerQuery.data.total}
                  onPage={setLedgerPage}
                />
              </>
            )}
          </QueryState>
        </section>
      )}

      {/* DRAWERS & MODALS */}

      {/* Request Detail Drawer */}
      {selectedReqCode && (
        <EquipmentRequestDrawer
          code={selectedReqCode}
          onClose={() => setSelectedReqCode(null)}
        />
      )}

      {/* 3-Step New Request Wizard */}
      {newReqOpen && (
        <NewEquipmentRequestDrawer onClose={() => setNewReqOpen(false)} />
      )}

      {/* Stock Item Detail Drawer */}
      {selectedStockItem && (
        <StockItemDrawer
          item={selectedStockItem}
          onClose={() => setSelectedStockItem(null)}
          onInspect={(code, loc, max) => {
            setSelectedStockItem(null);
            setInspectModal({ code, item: selectedStockItem, loc, max });
          }}
          onRepair={(code, loc, max) => {
            setSelectedStockItem(null);
            setRepairModal({ code, item: selectedStockItem, loc, max });
          }}
        />
      )}

      {/* Inspect Modal */}
      {inspectModal && (
        <InspectResourceModal
          resourceCode={inspectModal.code}
          item={inspectModal.item}
          location={inspectModal.loc}
          maxQuantity={inspectModal.max}
          onClose={() => setInspectModal(null)}
        />
      )}

      {/* Repair Modal */}
      {repairModal && (
        <RepairResourceModal
          resourceCode={repairModal.code}
          item={repairModal.item}
          location={repairModal.loc}
          maxQuantity={repairModal.max}
          onClose={() => setRepairModal(null)}
        />
      )}

      {/* Report Failure Modal */}
      {failureModal && (
        <ReportFailureModal
          resourceCode={failureModal.code}
          item={failureModal.item}
          location={failureModal.loc}
          onClose={() => setFailureModal(null)}
        />
      )}

      {/* External Receipt Modal */}
      {externalReceiptOpen && (
        <ExternalReceiptModal onClose={() => setExternalReceiptOpen(false)} />
      )}

      {/* Transfer Lifecycle Advance Modal */}
      {selectedTransfer && (
        <TransferDetailModal
          transfer={selectedTransfer}
          onClose={() => setSelectedTransfer(null)}
          onResolveDiscrepancy={() => {
            setDiscrepancyTransfer(selectedTransfer);
            setSelectedTransfer(null);
          }}
        />
      )}

      {/* Resolve Discrepancy Modal */}
      {discrepancyTransfer && (
        <ResolveDiscrepancyModal
          transfer={discrepancyTransfer}
          onClose={() => setDiscrepancyTransfer(null)}
        />
      )}

      {/* New Ad-hoc Transfer Modal */}
      {newTransferOpen && (
        <NewAdHocTransferModal onClose={() => setNewTransferOpen(false)} />
      )}
    </div>
  );
}

// ============================================================================
// Transfer Stage Detail Modal (Dispatched / Mobile-Friendly Receive)
// ============================================================================

function TransferDetailModal({
  transfer,
  onClose,
  onResolveDiscrepancy,
}: {
  transfer: TransferRow;
  onClose: () => void;
  onResolveDiscrepancy: () => void;
}) {
  const { base, can } = useFest();
  const qc = useQueryClient();

  const [dispatchedQty, setDispatchedQty] = useState(
    String(transfer.quantity_dispatched ?? transfer.quantity_planned)
  );
  const [receivedQty, setReceivedQty] = useState(
    String(transfer.quantity_received ?? transfer.quantity_dispatched ?? transfer.quantity_planned)
  );
  const [damagedQty, setDamagedQty] = useState(String(transfer.quantity_damaged ?? 0));
  const [conditionNote, setConditionNote] = useState(transfer.condition_note ?? "");
  const [discrepancyNote, setDiscrepancyNote] = useState(transfer.discrepancy_note ?? "");

  // Next stage determination
  const nextStage =
    transfer.status === "REQUESTED"
      ? "APPROVED"
      : transfer.status === "APPROVED"
      ? "DISPATCHED"
      : transfer.status === "DISPATCHED"
      ? "IN_TRANSIT"
      : transfer.status === "IN_TRANSIT"
      ? "RECEIVED"
      : transfer.status === "RECEIVED"
      ? "COMPLETED"
      : "COMPLETED";

  const advanceAction = useAction(
    () =>
      api(`${base}/equipment/transfers/${transfer.id}/advance`, {
        method: "POST",
        body: JSON.stringify({
          next_stage: nextStage,
          quantity_dispatched: Number(dispatchedQty) || null,
          quantity_received: Number(receivedQty) || null,
          quantity_damaged: Number(damagedQty) || 0,
          condition_note: conditionNote.trim() || null,
          discrepancy_note: discrepancyNote.trim() || null,
        }),
      }),
    {
      success: `Transfer advanced to ${nextStage}`,
      onDone: () => {
        qc.invalidateQueries({ queryKey: ["equipment-transfers"] });
        qc.invalidateQueries({ queryKey: ["equipment-requests"] });
        qc.invalidateQueries({ queryKey: ["equipment-summary"] });
        qc.invalidateQueries({ queryKey: ["equipment-stock"] });
        onClose();
      },
    }
  );

  return (
    <Dialog title={`Transfer #${transfer.id} · ${transfer.item}`} onClose={onClose}>
      <div className="stack">
        <div className="row-actions">
          <Pill value={transfer.status} tone={transfer.status === "COMPLETED" ? "ok" : "warn"} />
          <Pill value={transfer.kind} tone="info" />
        </div>

        <Facts
          items={[
            ["Item", transfer.item ?? "—"],
            ["From", transfer.from_location],
            ["To", transfer.to_location],
            ["Planned Qty", transfer.quantity_planned],
            ["Dispatched Qty", transfer.quantity_dispatched ?? "Not dispatched"],
            ["Received Qty", transfer.quantity_received ?? "Not received"],
            ["Damaged Qty", transfer.quantity_damaged ?? 0],
          ]}
        />

        {transfer.discrepancy_note && (
          <div className="callout warn small">
            <strong>Discrepancy Flagged:</strong> {transfer.discrepancy_note}
            {can("equipment.approve") && (
              <div style={{ marginTop: 8 }}>
                <button type="button" className="button-secondary small" onClick={onResolveDiscrepancy}>
                  Resolve Discrepancy
                </button>
              </div>
            )}
          </div>
        )}

        {/* If transfer is in progress, allow advancing */}
        {transfer.status !== "COMPLETED" && (
          <form
            className="panel stack"
            onSubmit={(e) => {
              e.preventDefault();
              advanceAction.mutate();
            }}
          >
            <h4>Advance Transfer Stage</h4>
            <p className="muted small">
              Current stage: <strong>{transfer.status}</strong> → Next: <strong>{nextStage}</strong>
            </p>

            {nextStage === "DISPATCHED" && (
              <Field label="Dispatched Quantity">
                <input
                  type="number"
                  min={1}
                  required
                  value={dispatchedQty}
                  onChange={(e) => setDispatchedQty(e.target.value)}
                />
              </Field>
            )}

            {(nextStage === "RECEIVED" || transfer.status === "IN_TRANSIT") && (
              <div className="form-grid">
                <Field label="Verified Received Quantity">
                  <input
                    type="number"
                    min={0}
                    required
                    value={receivedQty}
                    onChange={(e) => setReceivedQty(e.target.value)}
                  />
                </Field>

                <Field label="Damaged in Transit">
                  <input
                    type="number"
                    min={0}
                    value={damagedQty}
                    onChange={(e) => setDamagedQty(e.target.value)}
                  />
                </Field>

                <div className="span-all">
                  <Field label="Condition Note">
                    <input
                      placeholder="e.g. Scratched casing, verified cables"
                      value={conditionNote}
                      onChange={(e) => setConditionNote(e.target.value)}
                    />
                  </Field>
                </div>

                <div className="span-all">
                  <Field label="Discrepancy Note (if count differs from dispatch)">
                    <input
                      placeholder="e.g. 1 tripod missing from delivery box"
                      value={discrepancyNote}
                      onChange={(e) => setDiscrepancyNote(e.target.value)}
                    />
                  </Field>
                </div>
              </div>
            )}

            <ErrorNote error={advanceAction.error} />

            <div className="row-actions">
              <button type="submit" className="button-primary" disabled={advanceAction.isPending}>
                Advance to {nextStage}
              </button>
              <button type="button" className="button-ghost" onClick={onClose}>
                Cancel
              </button>
            </div>
          </form>
        )}
      </div>
    </Dialog>
  );
}

// ============================================================================
// Resolve Discrepancy Modal
// ============================================================================

function ResolveDiscrepancyModal({
  transfer,
  onClose,
}: {
  transfer: TransferRow;
  onClose: () => void;
}) {
  const { base } = useFest();
  const qc = useQueryClient();
  const [resolution, setResolution] = useState<"found_late" | "reclassified_damaged" | "written_off">("found_late");
  const [notes, setNotes] = useState("");

  const resolveAction = useAction(
    () =>
      api(`${base}/equipment/transfers/${transfer.id}/resolve-discrepancy`, {
        method: "POST",
        body: JSON.stringify({ resolution, notes: notes.trim() }),
      }),
    {
      success: `Discrepancy resolved as ${resolution}`,
      onDone: () => {
        qc.invalidateQueries({ queryKey: ["equipment-transfers"] });
        qc.invalidateQueries({ queryKey: ["equipment-summary"] });
        onClose();
      },
    }
  );

  return (
    <Dialog title={`Resolve Discrepancy · Transfer #${transfer.id}`} onClose={onClose}>
      <form
        className="form stack"
        onSubmit={(e) => {
          e.preventDefault();
          resolveAction.mutate();
        }}
      >
        <Facts
          items={[
            ["Item", transfer.item ?? "—"],
            ["Discrepancy Flag", transfer.discrepancy_note ?? "Missing / Damaged units"],
          ]}
        />

        <Field label="Resolution Outcome">
          <select value={resolution} onChange={(e) => setResolution(e.target.value as any)}>
            <option value="found_late">Found Late (Returned to destination stock)</option>
            <option value="reclassified_damaged">Reclassified Damaged (Moved to Under Repair)</option>
            <option value="written_off">Written Off (Decommissioned)</option>
          </select>
        </Field>

        <Field label="Investigation & Resolution Notes">
          <textarea
            required
            rows={3}
            placeholder="Document where items were found, courier report, or write-off approval"
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
          />
        </Field>

        <ErrorNote error={resolveAction.error} />

        <div className="row-actions">
          <button type="submit" className="button-primary" disabled={resolveAction.isPending || !notes.trim()}>
            Confirm Resolution
          </button>
          <button type="button" className="button-ghost" onClick={onClose}>
            Cancel
          </button>
        </div>
      </form>
    </Dialog>
  );
}

// ============================================================================
// New Ad-Hoc Transfer Modal
// ============================================================================

function NewAdHocTransferModal({ onClose }: { onClose: () => void }) {
  const { base } = useFest();
  const qc = useQueryClient();
  const [item, setItem] = useState("");
  const [quantity, setQuantity] = useState("1");
  const [fromLoc, setFromLoc] = useState("Store Room A");
  const [toLoc, setToLoc] = useState("Main Stage");

  const optionsQuery = useQuery({
    queryKey: ["equipment-options", base],
    queryFn: () => api<EquipmentOptions>(`${base}/equipment/options`),
  });

  const createTransfer = useAction(
    () =>
      api(`${base}/equipment/transfers`, {
        method: "POST",
        body: JSON.stringify({
          item,
          quantity: Number(quantity),
          from_location: fromLoc,
          to_location: toLoc,
        }),
      }),
    {
      success: `Created transfer for ${quantity} × ${item} from ${fromLoc} to ${toLoc}`,
      onDone: () => {
        qc.invalidateQueries({ queryKey: ["equipment-transfers"] });
        qc.invalidateQueries({ queryKey: ["equipment-summary"] });
        onClose();
      },
    }
  );

  return (
    <Dialog title="Create Campus Transfer" onClose={onClose}>
      <form
        className="form stack"
        onSubmit={(e) => {
          e.preventDefault();
          createTransfer.mutate();
        }}
      >
        <div className="form-grid">
          <Field label="Equipment Item">
            <select required value={item} onChange={(e) => setItem(e.target.value)}>
              <option value="">Select item...</option>
              {optionsQuery.data?.items.map((it) => (
                <option key={it} value={it}>
                  {it}
                </option>
              ))}
            </select>
          </Field>

          <Field label="Quantity">
            <input
              type="number"
              min={1}
              required
              value={quantity}
              onChange={(e) => setQuantity(e.target.value)}
            />
          </Field>

          <Field label="Source Location">
            <select value={fromLoc} onChange={(e) => setFromLoc(e.target.value)}>
              {["Store Room A", "Registration Desk", "Main Stage", "Sports Complex", "Van 1"].map((loc) => (
                <option key={loc} value={loc}>
                  {loc} {loc === "Van 1" ? "(Unverified mobile)" : ""}
                </option>
              ))}
            </select>
          </Field>

          <Field label="Destination Location">
            <select value={toLoc} onChange={(e) => setToLoc(e.target.value)}>
              {["Main Stage", "Sports Complex", "Registration Desk", "Store Room A", "Van 1"].map((loc) => (
                <option key={loc} value={loc}>
                  {loc}
                </option>
              ))}
            </select>
          </Field>
        </div>

        <ErrorNote error={createTransfer.error} />

        <div className="row-actions">
          <button type="submit" className="button-primary" disabled={createTransfer.isPending || !item}>
            Initiate Transfer
          </button>
          <button type="button" className="button-ghost" onClick={onClose}>
            Cancel
          </button>
        </div>
      </form>
    </Dialog>
  );
}

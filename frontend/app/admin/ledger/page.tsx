"use client";

import { FormEvent, memo, useCallback, useEffect, useMemo, useState } from "react";

import { AppShell } from "@/components/app-shell";
import { useLiveUpdates } from "@/components/live-updates-provider";
import { friendlyError } from "@/lib/api-client";
import { LEDGER_PAGE_EVENTS } from "@/lib/live-events";
import {
  ADJUSTMENT_PAGE_SIZE,
  SETTLEMENT_PAGE_SIZE,
  cancelSettlement,
  claimSettlement,
  completeSettlement,
  createCoadminSettlement,
  createSettlement,
  createTotalInAdjustment,
  getLedger,
  listLedgerAdjustments,
  listSettlements,
} from "@/services/ledger";
import type {
  CoadminLedgerSummary,
  LedgerAdjustment,
  LedgerItem,
  LedgerResponse,
  Settlement,
} from "@/types/api";

type PendingSettlement = {
  scope: "staff" | "coadmin";
  targetId: number;
  targetName: string;
  amount: string;
};

type PendingAdjustment = {
  staffId: number;
  staffUsername: string;
  currentTotalIn: string;
};

const settlementColors = {
  pending: "border-amber-300 bg-amber-50 text-amber-900",
  claimed: "border-blue-300 bg-blue-50 text-blue-900",
  done: "border-emerald-300 bg-emerald-50 text-emerald-900",
  cancelled: "border-red-300 bg-red-50 text-red-900",
};

function formatMoney(value: string): string {
  return new Intl.NumberFormat(undefined, {
    style: "currency",
    currency: "USD",
  }).format(Number(value));
}

function formatDate(value: string | null): string {
  if (!value) return "-";
  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

function netClass(value: string): string {
  const numeric = Number(value);
  if (numeric > 0) return "text-emerald-700";
  if (numeric < 0) return "text-red-700";
  return "text-slate-500";
}

function formatIds(ids: number[]): string {
  return ids.length ? ids.join(", ") : "-";
}

function pageRows<T>(page: { rows?: T[]; items: T[] }): T[] {
  return page.rows ?? page.items;
}

export default function AdminLedgerPage() {
  const [ledger, setLedger] = useState<LedgerResponse | null>(null);
  const [settlements, setSettlements] = useState<Settlement[]>([]);
  const [adjustments, setAdjustments] = useState<LedgerAdjustment[]>([]);
  const [settlementCursor, setSettlementCursor] = useState<string | null>(null);
  const [adjustmentCursor, setAdjustmentCursor] = useState<string | null>(null);
  const [hasMoreSettlements, setHasMoreSettlements] = useState(false);
  const [hasMoreAdjustments, setHasMoreAdjustments] = useState(false);
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [coadminFilter, setCoadminFilter] = useState("all");
  const [ledgerLoading, setLedgerLoading] = useState(true);
  const [settlementsLoading, setSettlementsLoading] = useState(true);
  const [adjustmentsLoading, setAdjustmentsLoading] = useState(true);
  const [loadingMoreSettlements, setLoadingMoreSettlements] = useState(false);
  const [loadingMoreAdjustments, setLoadingMoreAdjustments] = useState(false);
  const [actionId, setActionId] = useState<number | null>(null);
  const [error, setError] = useState("");
  const [pendingSettlement, setPendingSettlement] =
    useState<PendingSettlement | null>(null);
  const [pendingAdjustment, setPendingAdjustment] =
    useState<PendingAdjustment | null>(null);
  const [adjustmentTotalIn, setAdjustmentTotalIn] = useState("");
  const [adjustmentReason, setAdjustmentReason] = useState("");

  const dateFilters = useCallback(
    () => ({ dateFrom: dateFrom || undefined, dateTo: dateTo || undefined }),
    [dateFrom, dateTo],
  );

  const selectedCoadminId = useMemo(() => {
    const parsed = Number(coadminFilter);
    return Number.isFinite(parsed) ? parsed : undefined;
  }, [coadminFilter]);

  const historyFilters = useCallback(
    () => ({ ...dateFilters(), coadminId: selectedCoadminId }),
    [dateFilters, selectedCoadminId],
  );

  const loadLedger = useCallback(async () => {
    setLedgerLoading(true);
    setError("");
    try {
      setLedger(await getLedger(dateFilters()));
    } catch (loadError) {
      setError(friendlyError(loadError));
    } finally {
      setLedgerLoading(false);
    }
  }, [dateFilters]);

  const loadAdjustments = useCallback(
    async (reset = true, cursor: string | null = null) => {
      if (reset) setAdjustmentsLoading(true);
      else setLoadingMoreAdjustments(true);
      setError("");
      try {
        const page = await listLedgerAdjustments({
          ...historyFilters(),
          limit: ADJUSTMENT_PAGE_SIZE,
          cursor: reset ? null : cursor,
        });
        const rows = pageRows(page);
        setAdjustments((current) => (reset ? rows : [...current, ...rows]));
        setAdjustmentCursor(page.nextCursor ?? null);
        setHasMoreAdjustments(page.hasMore ?? page.has_more);
      } catch (loadError) {
        setError(friendlyError(loadError));
      } finally {
        if (reset) setAdjustmentsLoading(false);
        else setLoadingMoreAdjustments(false);
      }
    },
    [historyFilters],
  );

  const loadSettlements = useCallback(
    async (reset = true, cursor: string | null = null) => {
      if (reset) setSettlementsLoading(true);
      else setLoadingMoreSettlements(true);
      setError("");
      try {
        const page = await listSettlements({
          ...historyFilters(),
          limit: SETTLEMENT_PAGE_SIZE,
          cursor: reset ? null : cursor,
        });
        const rows = pageRows(page);
        setSettlements((current) => (reset ? rows : [...current, ...rows]));
        setSettlementCursor(page.nextCursor ?? null);
        setHasMoreSettlements(page.hasMore ?? page.has_more);
      } catch (loadError) {
        setError(friendlyError(loadError));
      } finally {
        if (reset) setSettlementsLoading(false);
        else setLoadingMoreSettlements(false);
      }
    },
    [historyFilters],
  );

  const loadPage = useCallback(async () => {
    await Promise.all([loadLedger(), loadAdjustments(true), loadSettlements(true)]);
  }, [loadAdjustments, loadLedger, loadSettlements]);

  useEffect(() => {
    const timeoutId = window.setTimeout(() => {
      void loadPage();
    }, 0);
    return () => window.clearTimeout(timeoutId);
  }, [loadPage]);

  useLiveUpdates(LEDGER_PAGE_EVENTS, loadPage, true);

  const applyFilters = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    void loadPage();
  };

  const coadminSummaries = useMemo(
    () => ledger?.coadmin_summaries ?? [],
    [ledger?.coadmin_summaries],
  );

  const filteredItems = useMemo(() => {
    const items = ledger?.items ?? [];
    if (coadminFilter === "all") return items;
    return items.filter((item) => String(item.coadmin_id ?? "default") === coadminFilter);
  }, [coadminFilter, ledger?.items]);

  const summary = ledger?.summary;

  const confirmSettlement = async () => {
    if (!pendingSettlement) return;
    setActionId(pendingSettlement.targetId);
    setError("");
    try {
      if (pendingSettlement.scope === "coadmin") {
        await createCoadminSettlement(pendingSettlement.targetId, dateFilters());
      } else {
        await createSettlement(pendingSettlement.targetId, dateFilters());
      }
      setPendingSettlement(null);
      await Promise.all([loadLedger(), loadSettlements(true)]);
    } catch (settleError) {
      setError(friendlyError(settleError));
    } finally {
      setActionId(null);
    }
  };

  const openAdjustment = (item: LedgerItem) => {
    setPendingAdjustment({
      staffId: item.staff_id,
      staffUsername: item.staff_username,
      currentTotalIn: item.total_in,
    });
    setAdjustmentTotalIn(item.total_in);
    setAdjustmentReason("");
  };

  const saveAdjustment = async () => {
    if (!pendingAdjustment) return;
    setActionId(pendingAdjustment.staffId);
    setError("");
    try {
      await createTotalInAdjustment(
        pendingAdjustment.staffId,
        adjustmentTotalIn,
        adjustmentReason,
      );
      setPendingAdjustment(null);
      await Promise.all([loadLedger(), loadAdjustments(true)]);
    } catch (adjustError) {
      setError(friendlyError(adjustError));
    } finally {
      setActionId(null);
    }
  };

  const runSettlementAction = async (
    settlementId: number,
    action: (id: number) => Promise<Settlement>,
  ) => {
    setActionId(settlementId);
    setError("");
    try {
      await action(settlementId);
      await loadSettlements(true);
    } catch (actionError) {
      setError(friendlyError(actionError));
    } finally {
      setActionId(null);
    }
  };

  return (
    <AppShell
      title="Ledger"
      description="Cashout belongs to the staff who created/requested it."
      requiredRole="admin"
    >
      <div className="space-y-6">
        <Panel
          title="Filter Panel"
          description="Set the reporting window and narrow staff/history views by coadmin."
        >
          <form
            onSubmit={applyFilters}
            className="grid gap-3 md:grid-cols-[1fr_1fr_1fr_auto]"
          >
            <label className="grid gap-1 text-sm font-semibold text-slate-700">
              From
              <input
                type="date"
                value={dateFrom}
                onChange={(event) => setDateFrom(event.target.value)}
                className="rounded-lg border border-slate-300 px-3 py-2"
              />
            </label>
            <label className="grid gap-1 text-sm font-semibold text-slate-700">
              To
              <input
                type="date"
                value={dateTo}
                onChange={(event) => setDateTo(event.target.value)}
                className="rounded-lg border border-slate-300 px-3 py-2"
              />
            </label>
            <label className="grid gap-1 text-sm font-semibold text-slate-700">
              Coadmin
              <select
                value={coadminFilter}
                onChange={(event) => setCoadminFilter(event.target.value)}
                className="rounded-lg border border-slate-300 px-3 py-2"
              >
                <option value="all">All coadmins</option>
                {coadminSummaries.map((coadmin) => (
                  <option
                    key={coadmin.coadmin_id ?? "default"}
                    value={String(coadmin.coadmin_id ?? "default")}
                  >
                    {coadmin.coadmin_username}
                  </option>
                ))}
              </select>
            </label>
            <button
              type="submit"
              className="self-end rounded-lg bg-slate-900 px-4 py-2.5 text-sm font-bold text-white"
            >
              Apply
            </button>
          </form>
        </Panel>

        {error ? (
          <div
            role="alert"
            className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm font-medium text-red-700"
          >
            {error}
          </div>
        ) : null}

        <Panel
          title="Summary Totals"
          description="Current open ledger totals for the selected date range."
        >
          {ledgerLoading ? (
            <PanelLoading message="Loading summary..." />
          ) : (
            <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
              {[
                ["Total In", summary?.total_in ?? "0.00"],
                ["Total Out", summary?.total_out ?? "0.00"],
                ["Settled", summary?.settled_amount ?? "0.00"],
                ["Net", summary?.net ?? "0.00"],
              ].map(([label, value]) => (
                <div key={label} className="rounded-lg border border-slate-200 p-4">
                  <p className="text-sm font-semibold text-slate-500">{label}</p>
                  <p
                    className={`mt-2 text-2xl font-black ${
                      label === "Net" ? netClass(value) : "text-slate-950"
                    }`}
                  >
                    {formatMoney(value)}
                  </p>
                </div>
              ))}
            </div>
          )}
        </Panel>

        <Panel
          title="Coadmin Summary"
          description="Open balances grouped by coadmin team."
        >
          <TableShell>
            <table className="w-full min-w-[1080px] text-left text-sm">
              <thead className="bg-slate-50 text-xs uppercase text-slate-500">
                <tr>
                  <th className="px-5 py-3">Coadmin</th>
                  <th className="px-5 py-3">Total In</th>
                  <th className="px-5 py-3">Total Out</th>
                  <th className="px-5 py-3">Settled</th>
                  <th className="px-5 py-3">Net</th>
                  <th className="px-5 py-3">Staff</th>
                  <th className="px-5 py-3">Payments</th>
                  <th className="px-5 py-3">Cashouts</th>
                  <th className="px-5 py-3">Settlements</th>
                  <th className="px-5 py-3 text-right">Action</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {ledgerLoading ? (
                  <EmptyTableRow colSpan={10} message="Loading coadmin balances..." />
                ) : null}
                {!ledgerLoading && coadminSummaries.length === 0 ? (
                  <EmptyTableRow colSpan={10} message="No records found." />
                ) : null}
                {coadminSummaries.map((coadmin) => (
                  <CoadminLedgerRow
                    key={coadmin.coadmin_id ?? "default"}
                    item={coadmin}
                    busy={actionId === coadmin.coadmin_id}
                    onSettle={() => {
                      if (coadmin.coadmin_id == null) return;
                      setPendingSettlement({
                        scope: "coadmin",
                        targetId: coadmin.coadmin_id,
                        targetName: coadmin.coadmin_username,
                        amount: coadmin.net,
                      });
                    }}
                  />
                ))}
              </tbody>
            </table>
          </TableShell>
        </Panel>

        <Panel
          title="Staff Balances"
          description="Open balances by staff account. Staff calculations are unchanged."
        >
          <TableShell>
            <table className="w-full min-w-[1040px] text-left text-sm">
              <thead className="bg-slate-50 text-xs uppercase text-slate-500">
                <tr>
                  <th className="px-5 py-3">Staff</th>
                  <th className="px-5 py-3">Coadmin</th>
                  <th className="px-5 py-3">Total In</th>
                  <th className="px-5 py-3">Total Out</th>
                  <th className="px-5 py-3">Settled</th>
                  <th className="px-5 py-3">Net</th>
                  <th className="px-5 py-3">Payments</th>
                  <th className="px-5 py-3">Cashouts</th>
                  <th className="px-5 py-3">Settlements</th>
                  <th className="px-5 py-3 text-right">Action</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {ledgerLoading ? (
                  <EmptyTableRow colSpan={10} message="Loading staff balances..." />
                ) : null}
                {!ledgerLoading && filteredItems.length === 0 ? (
                  <EmptyTableRow colSpan={10} message="No records found." />
                ) : null}
                {filteredItems.map((item) => (
                  <StaffLedgerRow
                    key={item.staff_id}
                    item={item}
                    busy={actionId === item.staff_id}
                    onSettle={() =>
                      setPendingSettlement({
                        scope: "staff",
                        targetId: item.staff_id,
                        targetName: item.staff_username,
                        amount: item.net,
                      })
                    }
                    onEditTotalIn={() => openAdjustment(item)}
                  />
                ))}
              </tbody>
            </table>
          </TableShell>
        </Panel>

        <Panel
          title="Adjustment History"
          description={`Latest ${ADJUSTMENT_PAGE_SIZE} manual total-in adjustments.`}
        >
          <TableShell>
            <table className="w-full min-w-[960px] text-left text-sm">
              <thead className="bg-slate-50 text-xs uppercase text-slate-500">
                <tr>
                  <th className="px-5 py-3">Staff</th>
                  <th className="px-5 py-3">Previous Total In</th>
                  <th className="px-5 py-3">New Total In</th>
                  <th className="px-5 py-3">Delta</th>
                  <th className="px-5 py-3">Reason</th>
                  <th className="px-5 py-3">Admin</th>
                  <th className="px-5 py-3">Created at</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {adjustmentsLoading ? (
                  <EmptyTableRow colSpan={7} message="Loading adjustments..." />
                ) : null}
                {!adjustmentsLoading && adjustments.length === 0 ? (
                  <EmptyTableRow colSpan={7} message="No records found." />
                ) : null}
                {adjustments.map((adjustment) => (
                  <AdjustmentRow key={adjustment.id} adjustment={adjustment} />
                ))}
              </tbody>
            </table>
          </TableShell>
          {hasMoreAdjustments ? (
            <LoadMoreButton
              loading={loadingMoreAdjustments}
              onClick={() => void loadAdjustments(false, adjustmentCursor)}
            />
          ) : null}
        </Panel>

        <Panel
          title="Withdrawal / Settlement History"
          description={`Latest ${SETTLEMENT_PAGE_SIZE} settlement and withdrawal records.`}
        >
          <TableShell>
            <table className="w-full min-w-[1400px] text-left text-sm">
              <thead className="bg-slate-50 text-xs uppercase text-slate-500">
                <tr>
                  <th className="px-5 py-3">Staff</th>
                  <th className="px-5 py-3">Scope</th>
                  <th className="px-5 py-3">Coadmin</th>
                  <th className="px-5 py-3">Amount</th>
                  <th className="px-5 py-3">Status</th>
                  <th className="px-5 py-3">Created by</th>
                  <th className="px-5 py-3">Completed by</th>
                  <th className="px-5 py-3">Created at</th>
                  <th className="px-5 py-3">Completed at</th>
                  <th className="px-5 py-3">Payments</th>
                  <th className="px-5 py-3">Cashouts</th>
                  <th className="px-5 py-3">Adjustments</th>
                  <th className="px-5 py-3">Notes</th>
                  <th className="px-5 py-3 text-right">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {settlementsLoading ? (
                  <EmptyTableRow colSpan={14} message="Loading settlements..." />
                ) : null}
                {!settlementsLoading && settlements.length === 0 ? (
                  <EmptyTableRow colSpan={14} message="No records found." />
                ) : null}
                {settlements.map((settlement) => (
                  <SettlementRow
                    key={settlement.id}
                    settlement={settlement}
                    busy={actionId === settlement.id}
                    onAction={runSettlementAction}
                  />
                ))}
              </tbody>
            </table>
          </TableShell>
          {hasMoreSettlements ? (
            <LoadMoreButton
              loading={loadingMoreSettlements}
              onClick={() => void loadSettlements(false, settlementCursor)}
            />
          ) : null}
        </Panel>
      </div>

      {pendingSettlement ? (
        <div className="fixed inset-0 z-50 grid place-items-center bg-slate-950/40 px-4">
          <div className="w-full max-w-md rounded-2xl bg-white p-6 shadow-xl">
            <h2 className="text-lg font-black text-slate-950">
              Confirm settlement
            </h2>
            <p className="mt-3 text-sm leading-6 text-slate-600">
              Settle {pendingSettlement.targetName}&apos;s net balance of{" "}
              <strong>{formatMoney(pendingSettlement.amount)}</strong>?
            </p>
            <div className="mt-6 flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setPendingSettlement(null)}
                className="rounded-lg border border-slate-300 px-4 py-2 text-sm font-semibold"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={() => void confirmSettlement()}
                className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-bold text-white"
              >
                Confirm
              </button>
            </div>
          </div>
        </div>
      ) : null}

      {pendingAdjustment ? (
        <div className="fixed inset-0 z-50 grid place-items-center bg-slate-950/40 px-4">
          <div className="w-full max-w-md rounded-2xl bg-white p-6 shadow-xl">
            <h2 className="text-lg font-black text-slate-950">Edit Total In</h2>
            <div className="mt-4 grid gap-4">
              <div>
                <p className="text-xs font-bold uppercase text-slate-500">Staff</p>
                <p className="mt-1 font-semibold text-slate-950">
                  {pendingAdjustment.staffUsername}
                </p>
              </div>
              <div>
                <p className="text-xs font-bold uppercase text-slate-500">
                  Current Total In
                </p>
                <p className="mt-1 font-semibold text-slate-950">
                  {formatMoney(pendingAdjustment.currentTotalIn)}
                </p>
              </div>
              <label className="grid gap-1 text-sm font-semibold text-slate-700">
                New Total In
                <input
                  type="number"
                  step="0.01"
                  value={adjustmentTotalIn}
                  onChange={(event) => setAdjustmentTotalIn(event.target.value)}
                  className="rounded-lg border border-slate-300 px-3 py-2"
                />
              </label>
              <label className="grid gap-1 text-sm font-semibold text-slate-700">
                Reason
                <textarea
                  value={adjustmentReason}
                  onChange={(event) => setAdjustmentReason(event.target.value)}
                  className="min-h-24 rounded-lg border border-slate-300 px-3 py-2"
                />
              </label>
            </div>
            <div className="mt-6 flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setPendingAdjustment(null)}
                className="rounded-lg border border-slate-300 px-4 py-2 text-sm font-semibold"
              >
                Cancel
              </button>
              <button
                type="button"
                disabled={!adjustmentReason.trim() || !adjustmentTotalIn}
                onClick={() => void saveAdjustment()}
                className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-bold text-white disabled:cursor-not-allowed disabled:bg-slate-300"
              >
                Save
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </AppShell>
  );
}

function Panel({
  title,
  description,
  children,
}: {
  title: string;
  description: string;
  children: React.ReactNode;
}) {
  return (
    <section className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
      <div className="border-b border-slate-200 px-5 py-4">
        <h2 className="font-black text-slate-950">{title}</h2>
        <p className="mt-1 text-sm text-slate-500">{description}</p>
      </div>
      <div className="p-4 sm:p-5">{children}</div>
    </section>
  );
}

function TableShell({ children }: { children: React.ReactNode }) {
  return (
    <div className="-mx-4 overflow-x-auto sm:-mx-5">
      <div className="inline-block min-w-full px-4 align-middle sm:px-5">
        {children}
      </div>
    </div>
  );
}

function PanelLoading({ message }: { message: string }) {
  return <div className="py-8 text-center text-sm text-slate-500">{message}</div>;
}

function EmptyTableRow({ colSpan, message }: { colSpan: number; message: string }) {
  return (
    <tr>
      <td colSpan={colSpan} className="px-5 py-8 text-center text-slate-500">
        {message}
      </td>
    </tr>
  );
}

function LoadMoreButton({
  loading,
  onClick,
}: {
  loading: boolean;
  onClick: () => void;
}) {
  return (
    <div className="mt-4 flex justify-center border-t border-slate-100 pt-4">
      <button
        type="button"
        disabled={loading}
        onClick={onClick}
        className="rounded-lg border border-indigo-300 px-4 py-2 text-sm font-bold text-indigo-700 disabled:cursor-not-allowed disabled:border-slate-200 disabled:text-slate-400"
      >
        {loading ? "Loading..." : "Load more"}
      </button>
    </div>
  );
}

const StaffLedgerRow = memo(function StaffLedgerRow({
  item,
  busy,
  onSettle,
  onEditTotalIn,
}: {
  item: LedgerItem;
  busy: boolean;
  onSettle: () => void;
  onEditTotalIn: () => void;
}) {
  const disabled = Number(item.net) <= 0;
  return (
    <tr>
      <td className="px-5 py-3 font-semibold">
        <span
          className="mr-2 inline-block h-2.5 w-2.5 rounded-full"
          style={{ backgroundColor: item.staff_color }}
        />
        {item.staff_username}
      </td>
      <td className="px-5 py-3">{item.coadmin_username}</td>
      <td className="px-5 py-3">
        <div className="flex items-center gap-2">
          <span>{formatMoney(item.total_in)}</span>
          <button
            type="button"
            disabled={busy}
            onClick={onEditTotalIn}
            className="rounded-lg border border-slate-300 px-2 py-1 text-xs font-bold text-slate-700"
          >
            Edit
          </button>
        </div>
      </td>
      <td className="px-5 py-3">{formatMoney(item.total_out)}</td>
      <td className="px-5 py-3">{formatMoney(item.settled_amount)}</td>
      <td className={`px-5 py-3 font-black ${netClass(item.net)}`}>
        {formatMoney(item.net)}
      </td>
      <td className="px-5 py-3">{item.payments_count}</td>
      <td className="px-5 py-3">{item.cashouts_count}</td>
      <td className="px-5 py-3">{item.settlements_count}</td>
      <td className="px-5 py-3 text-right">
        <button
          type="button"
          disabled={disabled || busy}
          onClick={onSettle}
          className="rounded-lg bg-indigo-600 px-3 py-2 text-xs font-bold text-white disabled:cursor-not-allowed disabled:bg-slate-300"
        >
          Settle / Withdraw
        </button>
      </td>
    </tr>
  );
});

const CoadminLedgerRow = memo(function CoadminLedgerRow({
  item,
  busy,
  onSettle,
}: {
  item: CoadminLedgerSummary;
  busy: boolean;
  onSettle: () => void;
}) {
  const disabled = Number(item.net) <= 0 || item.coadmin_id == null;
  return (
    <tr>
      <td className="px-5 py-3 font-semibold text-slate-950">
        {item.coadmin_username}
      </td>
      <td className="px-5 py-3">{formatMoney(item.total_in)}</td>
      <td className="px-5 py-3">{formatMoney(item.total_out)}</td>
      <td className="px-5 py-3">{formatMoney(item.settled_amount)}</td>
      <td className={`px-5 py-3 font-black ${netClass(item.net)}`}>
        {formatMoney(item.net)}
      </td>
      <td className="px-5 py-3">{item.staff_count}</td>
      <td className="px-5 py-3">{item.payments_count}</td>
      <td className="px-5 py-3">{item.cashouts_count}</td>
      <td className="px-5 py-3">{item.settlements_count}</td>
      <td className="px-5 py-3 text-right">
        <button
          type="button"
          disabled={disabled || busy}
          onClick={onSettle}
          className="rounded-lg bg-indigo-600 px-3 py-2 text-xs font-bold text-white disabled:cursor-not-allowed disabled:bg-slate-300"
        >
          Settle / Withdraw
        </button>
      </td>
    </tr>
  );
});

const AdjustmentRow = memo(function AdjustmentRow({
  adjustment,
}: {
  adjustment: LedgerAdjustment;
}) {
  return (
    <tr>
      <td className="px-5 py-3 font-semibold">
        <span
          className="mr-2 inline-block h-2.5 w-2.5 rounded-full"
          style={{ backgroundColor: adjustment.staff_color || "#64748B" }}
        />
        {adjustment.staff_username || "Deleted Staff"}
      </td>
      <td className="px-5 py-3">{formatMoney(adjustment.previous_total_in)}</td>
      <td className="px-5 py-3">{formatMoney(adjustment.new_total_in)}</td>
      <td className={`px-5 py-3 font-bold ${netClass(adjustment.amount_delta)}`}>
        {formatMoney(adjustment.amount_delta)}
      </td>
      <td className="px-5 py-3">{adjustment.reason}</td>
      <td className="px-5 py-3">{adjustment.created_by_admin_username ?? "-"}</td>
      <td className="px-5 py-3">{formatDate(adjustment.created_at)}</td>
    </tr>
  );
});

const SettlementRow = memo(function SettlementRow({
  settlement,
  busy,
  onAction,
}: {
  settlement: Settlement;
  busy: boolean;
  onAction: (
    settlementId: number,
    action: (id: number) => Promise<Settlement>,
  ) => Promise<void>;
}) {
  return (
    <tr>
      <td className="px-5 py-3 font-semibold">
        <span
          className="mr-2 inline-block h-2.5 w-2.5 rounded-full"
          style={{ backgroundColor: settlement.staff_color || "#64748B" }}
        />
        {settlement.scope === "coadmin"
          ? settlement.coadmin_username ?? "Coadmin"
          : settlement.staff_username || "Deleted Staff"}
      </td>
      <td className="px-5 py-3 capitalize">{settlement.scope}</td>
      <td className="px-5 py-3">{settlement.coadmin_username ?? "-"}</td>
      <td className="px-5 py-3 font-bold">{formatMoney(settlement.amount)}</td>
      <td className="px-5 py-3">
        <span
          className={`rounded-full border px-2.5 py-1 text-xs font-bold capitalize ${
            settlementColors[settlement.status]
          }`}
        >
          {settlement.status}
        </span>
      </td>
      <td className="px-5 py-3">{settlement.created_by_admin_username}</td>
      <td className="px-5 py-3">{settlement.completed_by_admin_username ?? "-"}</td>
      <td className="px-5 py-3">{formatDate(settlement.created_at)}</td>
      <td className="px-5 py-3">{formatDate(settlement.completed_at)}</td>
      <td className="px-5 py-3">{formatIds(settlement.payment_ids)}</td>
      <td className="px-5 py-3">{formatIds(settlement.cashout_ids)}</td>
      <td className="px-5 py-3">{formatIds(settlement.adjustment_ids)}</td>
      <td className="px-5 py-3">{settlement.notes ?? "-"}</td>
      <td className="px-5 py-3">
        {settlement.status === "pending" || settlement.status === "claimed" ? (
          <div className="flex justify-end gap-2">
            {settlement.status === "pending" ? (
              <button
                type="button"
                disabled={busy}
                onClick={() => void onAction(settlement.id, claimSettlement)}
                className="rounded-lg border border-blue-300 px-2.5 py-1.5 text-xs font-bold text-blue-700"
              >
                Claim
              </button>
            ) : null}
            <button
              type="button"
              disabled={busy}
              onClick={() => void onAction(settlement.id, completeSettlement)}
              className="rounded-lg border border-emerald-300 px-2.5 py-1.5 text-xs font-bold text-emerald-700"
            >
              Done
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => void onAction(settlement.id, cancelSettlement)}
              className="rounded-lg border border-red-300 px-2.5 py-1.5 text-xs font-bold text-red-700"
            >
              Cancel
            </button>
          </div>
        ) : (
          <span className="block text-right">-</span>
        )}
      </td>
    </tr>
  );
});

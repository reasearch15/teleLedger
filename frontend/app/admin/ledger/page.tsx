"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";

import { AppShell } from "@/components/app-shell";
import { useLiveUpdates } from "@/components/live-updates-provider";
import { friendlyError } from "@/lib/api-client";
import { LEDGER_PAGE_EVENTS } from "@/lib/live-events";
import {
  cancelSettlement,
  claimSettlement,
  completeSettlement,
  createSettlement,
  createTotalInAdjustment,
  ADJUSTMENT_PAGE_SIZE,
  getLedger,
  listLedgerAdjustments,
  listSettlements,
  SETTLEMENT_PAGE_SIZE,
} from "@/services/ledger";
import type {
  LedgerAdjustment,
  LedgerItem,
  LedgerResponse,
  Settlement,
} from "@/types/api";

type PendingSettlement = {
  staffId: number;
  staffUsername: string;
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

export default function AdminLedgerPage() {
  const [ledger, setLedger] = useState<LedgerResponse | null>(null);
  const [settlements, setSettlements] = useState<Settlement[]>([]);
  const [adjustments, setAdjustments] = useState<LedgerAdjustment[]>([]);
  const [hasMoreSettlements, setHasMoreSettlements] = useState(false);
  const [hasMoreAdjustments, setHasMoreAdjustments] = useState(false);
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [loading, setLoading] = useState(true);
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

  const loadLedger = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const filters = dateFilters();
      const [ledgerPage, settlementPage, adjustmentPage] = await Promise.all([
        getLedger(filters),
        listSettlements({ ...filters, limit: SETTLEMENT_PAGE_SIZE, offset: 0 }),
        listLedgerAdjustments({
          ...filters,
          limit: ADJUSTMENT_PAGE_SIZE,
          offset: 0,
        }),
      ]);
      setLedger(ledgerPage);
      setSettlements(settlementPage.items);
      setAdjustments(adjustmentPage.items);
      setHasMoreSettlements(settlementPage.has_more);
      setHasMoreAdjustments(adjustmentPage.has_more);
    } catch (loadError) {
      setError(friendlyError(loadError));
    } finally {
      setLoading(false);
    }
  }, [dateFilters]);

  useEffect(() => {
    const timeoutId = window.setTimeout(() => {
      void loadLedger();
    }, 0);
    return () => window.clearTimeout(timeoutId);
  }, [loadLedger]);

  useLiveUpdates(LEDGER_PAGE_EVENTS, loadLedger, true);

  const applyFilters = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    void loadLedger();
  };

  const confirmSettlement = async () => {
    if (!pendingSettlement) return;
    setActionId(pendingSettlement.staffId);
    setError("");
    try {
      await createSettlement(pendingSettlement.staffId, dateFilters());
      setPendingSettlement(null);
      await loadLedger();
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
      await loadLedger();
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
      await loadLedger();
    } catch (actionError) {
      setError(friendlyError(actionError));
    } finally {
      setActionId(null);
    }
  };

  const loadMoreSettlements = async () => {
    setActionId(-1);
    try {
      const page = await listSettlements({
        ...dateFilters(),
        limit: SETTLEMENT_PAGE_SIZE,
        offset: settlements.length,
      });
      setSettlements((current) => [...current, ...page.items]);
      setHasMoreSettlements(page.has_more);
    } catch (loadError) {
      setError(friendlyError(loadError));
    } finally {
      setActionId(null);
    }
  };

  const loadMoreAdjustments = async () => {
    setActionId(-2);
    try {
      const page = await listLedgerAdjustments({
        ...dateFilters(),
        limit: ADJUSTMENT_PAGE_SIZE,
        offset: adjustments.length,
      });
      setAdjustments((current) => [...current, ...page.items]);
      setHasMoreAdjustments(page.has_more);
    } catch (loadError) {
      setError(friendlyError(loadError));
    } finally {
      setActionId(null);
    }
  };

  const summary = ledger?.summary;

  return (
    <AppShell
      title="Ledger"
      description="Cashout belongs to the staff who created/requested it."
      requiredRole="admin"
    >
      <form
        onSubmit={applyFilters}
        className="mb-6 grid gap-3 rounded-2xl border border-slate-200 bg-white p-4 shadow-sm sm:grid-cols-[1fr_1fr_auto]"
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
        <button
          type="submit"
          className="self-end rounded-lg bg-slate-900 px-4 py-2.5 text-sm font-bold text-white"
        >
          Apply
        </button>
      </form>

      {error ? (
        <div
          role="alert"
          className="mb-4 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm font-medium text-red-700"
        >
          {error}
        </div>
      ) : null}

      <section className="mb-6 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {[
          ["Total In", summary?.total_in ?? "0.00"],
          ["Total Out", summary?.total_out ?? "0.00"],
          ["Settled", summary?.settled_amount ?? "0.00"],
          ["Net", summary?.net ?? "0.00"],
        ].map(([label, value]) => (
          <div
            key={label}
            className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm"
          >
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
      </section>

      <section className="mb-8 overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
        <div className="border-b border-slate-200 px-5 py-4">
          <h2 className="font-black text-slate-950">Staff Balances</h2>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[960px] text-left text-sm">
            <thead className="bg-slate-50 text-xs uppercase text-slate-500">
              <tr>
                <th className="px-5 py-3">Staff</th>
                <th className="px-5 py-3">Total In</th>
                <th className="px-5 py-3">Total Out</th>
                <th className="px-5 py-3">Settled</th>
                <th className="px-5 py-3">Net</th>
                <th className="px-5 py-3">Payments</th>
                <th className="px-5 py-3">Cashouts</th>
                <th className="px-5 py-3">Settlements</th>
                <th className="px-5 py-3">Action</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {loading ? (
                <tr>
                  <td colSpan={9} className="px-5 py-8 text-center text-slate-500">
                    Loading ledger...
                  </td>
                </tr>
              ) : null}
              {ledger?.items.map((item) => (
                <StaffLedgerRow
                  key={item.staff_id}
                  item={item}
                  busy={actionId === item.staff_id}
                  onSettle={() =>
                    setPendingSettlement({
                      staffId: item.staff_id,
                      staffUsername: item.staff_username,
                      amount: item.net,
                    })
                  }
                  onEditTotalIn={() => openAdjustment(item)}
                />
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section className="mb-8 overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
        <div className="border-b border-slate-200 px-5 py-4">
          <h2 className="font-black text-slate-950">Adjustment History</h2>
        </div>
        <div className="overflow-x-auto">
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
              {adjustments.map((adjustment) => (
                <tr key={adjustment.id}>
                  <td className="px-5 py-3 font-semibold">
                    <span
                      className="mr-2 inline-block h-2.5 w-2.5 rounded-full"
                      style={{ backgroundColor: adjustment.staff_color || "#64748B" }}
                    />
                    {adjustment.staff_username || "Deleted Staff"}
                  </td>
                  <td className="px-5 py-3">
                    {formatMoney(adjustment.previous_total_in)}
                  </td>
                  <td className="px-5 py-3">
                    {formatMoney(adjustment.new_total_in)}
                  </td>
                  <td className={`px-5 py-3 font-bold ${netClass(adjustment.amount_delta)}`}>
                    {formatMoney(adjustment.amount_delta)}
                  </td>
                  <td className="px-5 py-3">{adjustment.reason}</td>
                  <td className="px-5 py-3">
                    {adjustment.created_by_admin_username ?? "-"}
                  </td>
                  <td className="px-5 py-3">
                    {formatDate(adjustment.created_at)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {hasMoreAdjustments ? (
          <div className="border-t border-slate-100 p-4 text-center">
            <button
              type="button"
              disabled={actionId === -2}
              onClick={() => void loadMoreAdjustments()}
              className="rounded-lg border border-indigo-300 px-4 py-2 text-sm font-bold text-indigo-700"
            >
              Load More
            </button>
          </div>
        ) : null}
      </section>

      <section className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
        <div className="border-b border-slate-200 px-5 py-4">
          <h2 className="font-black text-slate-950">
            Withdrawal / Settlement History
          </h2>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[1240px] text-left text-sm">
            <thead className="bg-slate-50 text-xs uppercase text-slate-500">
              <tr>
                <th className="px-5 py-3">Staff</th>
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
                <th className="px-5 py-3">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {settlements.map((settlement) => {
                const busy = actionId === settlement.id;
                return (
                  <tr key={settlement.id}>
                    <td className="px-5 py-3 font-semibold">
                      <span
                        className="mr-2 inline-block h-2.5 w-2.5 rounded-full"
                        style={{ backgroundColor: settlement.staff_color || "#64748B" }}
                      />
                      {settlement.staff_username || "Deleted Staff"}
                    </td>
                    <td className="px-5 py-3 font-bold">
                      {formatMoney(settlement.amount)}
                    </td>
                    <td className="px-5 py-3">
                      <span
                        className={`rounded-full border px-2.5 py-1 text-xs font-bold capitalize ${
                          settlementColors[settlement.status]
                        }`}
                      >
                        {settlement.status}
                      </span>
                    </td>
                    <td className="px-5 py-3">
                      {settlement.created_by_admin_username}
                    </td>
                    <td className="px-5 py-3">
                      {settlement.completed_by_admin_username ?? "-"}
                    </td>
                    <td className="px-5 py-3">
                      {formatDate(settlement.created_at)}
                    </td>
                    <td className="px-5 py-3">
                      {formatDate(settlement.completed_at)}
                    </td>
                    <td className="px-5 py-3">{formatIds(settlement.payment_ids)}</td>
                    <td className="px-5 py-3">{formatIds(settlement.cashout_ids)}</td>
                    <td className="px-5 py-3">
                      {formatIds(settlement.adjustment_ids)}
                    </td>
                    <td className="px-5 py-3">{settlement.notes ?? "-"}</td>
                    <td className="px-5 py-3">
                      {settlement.status === "pending" ||
                      settlement.status === "claimed" ? (
                        <div className="flex flex-wrap gap-2">
                          {settlement.status === "pending" ? (
                            <button
                              type="button"
                              disabled={busy}
                              onClick={() =>
                                void runSettlementAction(
                                  settlement.id,
                                  claimSettlement,
                                )
                              }
                              className="rounded-lg border border-blue-300 px-2.5 py-1.5 text-xs font-bold text-blue-700"
                            >
                              Claim
                            </button>
                          ) : null}
                          <button
                            type="button"
                            disabled={busy}
                            onClick={() =>
                              void runSettlementAction(
                                settlement.id,
                                completeSettlement,
                              )
                            }
                            className="rounded-lg border border-emerald-300 px-2.5 py-1.5 text-xs font-bold text-emerald-700"
                          >
                            Done
                          </button>
                          <button
                            type="button"
                            disabled={busy}
                            onClick={() =>
                              void runSettlementAction(
                                settlement.id,
                                cancelSettlement,
                              )
                            }
                            className="rounded-lg border border-red-300 px-2.5 py-1.5 text-xs font-bold text-red-700"
                          >
                            Cancel
                          </button>
                        </div>
                      ) : (
                        "-"
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        {hasMoreSettlements ? (
          <div className="border-t border-slate-100 p-4 text-center">
            <button
              type="button"
              disabled={actionId === -1}
              onClick={() => void loadMoreSettlements()}
              className="rounded-lg border border-indigo-300 px-4 py-2 text-sm font-bold text-indigo-700"
            >
              Load More
            </button>
          </div>
        ) : null}
      </section>

      {pendingSettlement ? (
        <div className="fixed inset-0 z-50 grid place-items-center bg-slate-950/40 px-4">
          <div className="w-full max-w-md rounded-2xl bg-white p-6 shadow-xl">
            <h2 className="text-lg font-black text-slate-950">
              Confirm settlement
            </h2>
            <p className="mt-3 text-sm leading-6 text-slate-600">
              Settle {pendingSettlement.staffUsername}&apos;s net balance of{" "}
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

function StaffLedgerRow({
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
      <td className="px-5 py-3">
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
}

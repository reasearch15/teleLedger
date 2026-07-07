"use client";

import { memo, useCallback, useEffect, useMemo, useState } from "react";

import { useLiveUpdates } from "@/components/live-updates-provider";
import { friendlyError } from "@/lib/api-client";
import { LEDGER_PAGE_EVENTS } from "@/lib/live-events";
import {
  createSettlement,
  createTotalInAdjustment,
  getLedger,
} from "@/services/ledger";
import type { LedgerItem } from "@/types/api";

import {
  EmptyTableRow,
  formatMoney,
  MobileCardList,
  MobileEmptyState,
  MobileRow,
  netClass,
  Panel,
  TableShell,
  useLedgerFilters,
} from "./shared";

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

export function StaffBalancesPanel() {
  const filters = useLedgerFilters();
  const [items, setItems] = useState<LedgerItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [actionId, setActionId] = useState<number | null>(null);
  const [error, setError] = useState("");
  const [pendingSettlement, setPendingSettlement] =
    useState<PendingSettlement | null>(null);
  const [pendingAdjustment, setPendingAdjustment] =
    useState<PendingAdjustment | null>(null);
  const [adjustmentTotalIn, setAdjustmentTotalIn] = useState("");
  const [adjustmentReason, setAdjustmentReason] = useState("");

  const refresh = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const ledger = await getLedger(filters);
      setItems(ledger.items);
    } catch (loadError) {
      setError(friendlyError(loadError));
    } finally {
      setLoading(false);
    }
  }, [filters]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useLiveUpdates(LEDGER_PAGE_EVENTS, refresh, true);

  const filteredItems = useMemo(() => {
    if (!filters.coadminId) return items;
    return items.filter((item) => item.coadmin_id === filters.coadminId);
  }, [filters.coadminId, items]);

  const confirmSettlement = async () => {
    if (!pendingSettlement) return;
    setActionId(pendingSettlement.staffId);
    setError("");
    try {
      await createSettlement(pendingSettlement.staffId, filters);
      setPendingSettlement(null);
      await refresh();
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
      await refresh();
    } catch (adjustError) {
      setError(friendlyError(adjustError));
    } finally {
      setActionId(null);
    }
  };

  return (
    <Panel
      title="Staff Balances"
      description="Open balances by staff account. Staff calculations are unchanged."
      error={error}
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
            {loading ? (
              <EmptyTableRow colSpan={10} message="Loading staff balances..." />
            ) : null}
            {!loading && filteredItems.length === 0 ? (
              <EmptyTableRow colSpan={10} message="No records found." />
            ) : null}
            {filteredItems.map((item) => (
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
      </TableShell>
      <MobileCardList>
        {loading ? <MobileEmptyState message="Loading staff balances..." /> : null}
        {!loading && filteredItems.length === 0 ? (
          <MobileEmptyState message="No records found." />
        ) : null}
        {filteredItems.map((item) => (
          <StaffMobileCard
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
      </MobileCardList>

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
    </Panel>
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

const StaffMobileCard = memo(function StaffMobileCard({
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
    <article className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <dl className="grid gap-2.5">
        <MobileRow label="Staff" value={item.staff_username} strong />
        <MobileRow label="Coadmin" value={item.coadmin_username} />
        <MobileRow label="Total In" value={formatMoney(item.total_in)} strong />
        <MobileRow label="Total Out" value={formatMoney(item.total_out)} />
        <MobileRow label="Settled" value={formatMoney(item.settled_amount)} />
        <MobileRow
          label="Net"
          value={formatMoney(item.net)}
          strong
          className={netClass(item.net)}
        />
        <MobileRow label="Payments" value={item.payments_count} />
        <MobileRow label="Cashouts" value={item.cashouts_count} />
        <MobileRow label="Settlements" value={item.settlements_count} />
      </dl>
      <div className="mt-4 grid gap-2">
        <button
          type="button"
          disabled={disabled || busy}
          onClick={onSettle}
          className="w-full rounded-lg bg-indigo-600 px-3 py-2.5 text-sm font-bold text-white disabled:cursor-not-allowed disabled:bg-slate-300"
        >
          Settle / Withdraw
        </button>
        <button
          type="button"
          disabled={busy}
          onClick={onEditTotalIn}
          className="w-full rounded-lg border border-slate-300 px-3 py-2.5 text-sm font-bold text-slate-700 disabled:cursor-not-allowed disabled:text-slate-400"
        >
          Edit Total In
        </button>
      </div>
    </article>
  );
});

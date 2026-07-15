"use client";

import { memo, useCallback, useEffect, useState } from "react";

import { useLiveUpdates } from "@/components/live-updates-provider";
import { friendlyError } from "@/lib/api-client";
import { LEDGER_PAGE_EVENTS } from "@/lib/live-events";
import { createCoadminSettlement, getLedger } from "@/services/ledger";
import type { CoadminLedgerSummary, LedgerResponse } from "@/types/api";

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
  coadminId: number;
  coadminUsername: string;
  amount: string;
};

export function CoadminSummaryPanel() {
  const filters = useLedgerFilters();
  const [items, setItems] = useState<CoadminLedgerSummary[]>([]);
  const [ledgerMeta, setLedgerMeta] = useState<Pick<
    LedgerResponse,
    "calculation_type" | "timezone" | "includes_settled"
  > | null>(null);
  const [loading, setLoading] = useState(true);
  const [actionId, setActionId] = useState<number | null>(null);
  const [error, setError] = useState("");
  const [pendingSettlement, setPendingSettlement] =
    useState<PendingSettlement | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const ledger = await getLedger(filters);
      setItems(ledger.coadmin_summaries);
      setLedgerMeta({
        calculation_type: ledger.calculation_type,
        timezone: ledger.timezone,
        includes_settled: ledger.includes_settled,
      });
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

  const isHistoricalActivity = ledgerMeta?.calculation_type !== "open_balance";
  const isRollingActivity = ledgerMeta?.calculation_type === "rolling_activity";

  const confirmSettlement = async () => {
    if (!pendingSettlement) return;
    setActionId(pendingSettlement.coadminId);
    setError("");
    try {
      await createCoadminSettlement(pendingSettlement.coadminId, filters);
      setPendingSettlement(null);
      await refresh();
    } catch (settleError) {
      setError(friendlyError(settleError));
    } finally {
      setActionId(null);
    }
  };

  return (
    <Panel
      title={
        isRollingActivity
          ? "Rolling 12-Hour Activity"
          : isHistoricalActivity
            ? "Coadmin Custom Range Activity"
            : "Coadmin Current Open Balance"
      }
      description={
        isHistoricalActivity
          ? `Completed staff activity grouped by coadmin for the selected Nepal Time (${ledgerMeta?.timezone ?? "Asia/Kathmandu"}) window.`
          : "Unsettled open balances grouped by coadmin team."
      }
      error={error}
    >
      <TableShell>
        <table className="w-full min-w-[1160px] text-left text-sm">
          <thead className="bg-slate-50 text-xs uppercase text-slate-500">
            <tr>
              <th className="px-5 py-3">Coadmin</th>
              <th className="px-5 py-3">Payments</th>
              <th className="px-5 py-3">Cashouts</th>
              <th className="px-5 py-3">Adjustments</th>
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
            {loading ? (
              <EmptyTableRow colSpan={11} message="Loading coadmin balances..." />
            ) : null}
            {!loading && items.length === 0 ? (
              <EmptyTableRow colSpan={11} message="No records found." />
            ) : null}
            {items.map((item) => (
              <CoadminLedgerRow
                key={item.coadmin_id ?? "default"}
                item={item}
                busy={actionId === item.coadmin_id}
                readOnly={isHistoricalActivity}
                onSettle={() => {
                  if (item.coadmin_id == null) return;
                  setPendingSettlement({
                    coadminId: item.coadmin_id,
                    coadminUsername: item.coadmin_username,
                    amount: item.net,
                  });
                }}
              />
            ))}
          </tbody>
        </table>
      </TableShell>
      <MobileCardList>
        {loading ? <MobileEmptyState message="Loading coadmin balances..." /> : null}
        {!loading && items.length === 0 ? (
          <MobileEmptyState message="No records found." />
        ) : null}
        {items.map((item) => (
          <CoadminMobileCard
            key={item.coadmin_id ?? "default"}
            item={item}
            busy={actionId === item.coadmin_id}
            readOnly={isHistoricalActivity}
            onSettle={() => {
              if (item.coadmin_id == null) return;
              setPendingSettlement({
                coadminId: item.coadmin_id,
                coadminUsername: item.coadmin_username,
                amount: item.net,
              });
            }}
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
              Settle {pendingSettlement.coadminUsername}&apos;s net balance of{" "}
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
    </Panel>
  );
}

const CoadminLedgerRow = memo(function CoadminLedgerRow({
  item,
  busy,
  readOnly,
  onSettle,
}: {
  item: CoadminLedgerSummary;
  busy: boolean;
  readOnly: boolean;
  onSettle: () => void;
}) {
  const disabled = readOnly || Number(item.net) <= 0 || item.coadmin_id == null;
  return (
    <tr>
      <td className="px-5 py-3 font-semibold text-slate-950">
        {item.coadmin_username}
      </td>
      <td className="px-5 py-3">{formatMoney(item.payment_total)}</td>
      <td className="px-5 py-3">{formatMoney(item.total_out)}</td>
      <td className="px-5 py-3">{formatMoney(item.adjustment_total)}</td>
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

const CoadminMobileCard = memo(function CoadminMobileCard({
  item,
  busy,
  readOnly,
  onSettle,
}: {
  item: CoadminLedgerSummary;
  busy: boolean;
  readOnly: boolean;
  onSettle: () => void;
}) {
  const disabled = readOnly || Number(item.net) <= 0 || item.coadmin_id == null;
  return (
    <article className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
      <dl className="grid gap-2.5">
        <MobileRow label="Coadmin" value={item.coadmin_username} strong />
        <MobileRow label="Payments" value={formatMoney(item.payment_total)} strong />
        <MobileRow label="Cashouts" value={formatMoney(item.total_out)} />
        <MobileRow label="Adjustments" value={formatMoney(item.adjustment_total)} />
        <MobileRow label="Settled" value={formatMoney(item.settled_amount)} />
        <MobileRow
          label="Net"
          value={formatMoney(item.net)}
          strong
          className={netClass(item.net)}
        />
        <MobileRow label="Staff" value={item.staff_count} />
        <MobileRow label="Payments" value={item.payments_count} />
        <MobileRow label="Cashouts" value={item.cashouts_count} />
        <MobileRow label="Settlements" value={item.settlements_count} />
      </dl>
      <button
        type="button"
        disabled={disabled || busy}
        onClick={onSettle}
        className="mt-4 w-full rounded-lg bg-indigo-600 px-3 py-2.5 text-sm font-bold text-white disabled:cursor-not-allowed disabled:bg-slate-300"
      >
        Settle / Withdraw
      </button>
    </article>
  );
});
